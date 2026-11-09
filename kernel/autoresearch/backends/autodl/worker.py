# GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
# LAST-EDIT: 2026-11-09 补齐 availability tick 的专题 process_status 投影与 targets-db 默认接线
# PURPOSE: 单次 worker —— 采一次机器状态 → 判事件 → 落队列/台账/状态 → 刷新绑定名单与可用性快照
#          → 按布局刷新每个已登记专题的 process_status 投影（⛔ 不写 STATUS.md 研究正文）。
#          给 Windows 定时任务或人工跑；窗口内只跑一次（不驻留、不自己排下一轮）。
# ORIGIN: tool/topic_watchdog.py 的 availability-only 分支（抽取「采集+判事件+发绑定+写快照」
#         四件事，去页面维护/租约/锁等实例逻辑；所有部署参数显式输入）
# CUMULATIVE-DELTA: 首版。⛔ 不内置任何项目根、机器名单、凭据值或计划任务名；
#   凭据只从环境变量名或显式文件取；`--dry-run` 只采集不写盘；⛔ 不调用任何云机操作（那在 cloud.py，需显式授权）。
#   2026-11-09 新增两件事：① `--targets-db` 默认 `<workspace>/.autoresearch/runtime.sqlite3`，
#   从 `topics.leader_session_id` 判绑定并写 `events/targets.json`；库/表缺 ⇒ 显式 unknown 且**不覆盖**
#   已有名单（无绑定就写权威空名单，插件 fallback=hold 不投）。② 每个已登记专题调用
#   `reporting.write_process_status`（只读证据投影）刷新 `.autoresearch/state/process_status/`；
#   专题目录由 workspace-layout 解析（中文布局同样生效），⛔ 不改 STATUS.md 任何字节、不伪造运行状态。
"""单次机器可用性 worker。

它做什么（一次调用）
--------------------
1. `availability.collect()` 只读查一次机器列表（有硬截止，不会把调用方拖住）；
2. `events.record()` 判「从开不了变成能开」，命中就落 `events/pending/`；
3. `events.expire_stale()` 把太旧的待投递事件挪进 `events/expired/`（没有插件时必须有人做）；
4. 从绑定库（默认 `<workspace>/.autoresearch/runtime.sqlite3`）读负责人会话，写 `events/targets.json`；
   库或 `topics` 表读不出 ⇒ 显式 `unknown`，已有名单保持不动（不猜、不清空）；
5. 对每个已登记专题调用 `reporting.write_process_status`，刷新
   `<workspace>/.autoresearch/state/process_status/<topic>.json` 与 `.md`；
6. 把可读快照写到 `.autoresearch/availability.md`。

它不做什么
----------
⛔ 不开机、不关机、不重启、不跑 GPU 任务（那些在 `cloud.py`，且必须显式传 `allow_control=True`）。
⛔ 不投递会话消息（那是 DSH 侧插件的事）；没有绑定会话时事件只留在队列里（插件 `fallback=hold`）。
⛔ 不写研究页 `STATUS.md` 任何字节：流程状态只进 `process_status/`。
⛔ 读取机器状态**不等于**开机成功：报告只说明「看起来能开」。

退出码
------
0 = 查到了 · 2 = 说不清（没给 workspace / 凭据缺 / 查询失败）· 3 = 自检失败。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    # 允许直接 `python worker.py`：把包根加进 sys.path（本文件在 <repo>/kernel/autoresearch/backends/autodl/）。
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from kernel.autoresearch.backends.autodl import availability, events  # noqa: E402

# ⚠️ Windows 控制台默认编码（cp936 / cp1252）打不出箭头与部分符号 ⇒ 直接在解释器层面改成 UTF-8。
#    用 reconfigure 而不是再包一层 TextIOWrapper：重复包装在解释器退出时会关掉真正的 stdout。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

EXIT_OK = 0
EXIT_UNKNOWN = 2
EXIT_SELFTEST = 3

#: 绑定/登记库的默认相对落点（工作区内）：与 `init` / `provision_topic` 用同一个库。
DEFAULT_TARGETS_DB = ".autoresearch/runtime.sqlite3"
#: 表名白名单（SQL 里拼表名，必须先验形状）。
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _targets_db_path(args: argparse.Namespace, workspace_path: Path) -> Path:
    """绑定库：显式 `--targets-db` 优先，否则用工作区的 runtime.sqlite3。"""
    raw = str(getattr(args, "targets_db", "") or "").strip()
    if raw:
        return Path(raw)
    return workspace_path / DEFAULT_TARGETS_DB


def _inside_workspace(path: Path, workspace_path: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(workspace_path.resolve())
    except (OSError, ValueError):
        return False


def _read_topic_registry(db_path: Path, table: str = "topics") -> dict:
    """只读登记表（⛔ 不建库、不建表、不猜）。

    返回 `{status: "ok"|"unknown", why, topics: [{id,status,leader}], targets_ok, targets_why}`。
    「库不存在」「缺表」「缺列」都显式 `unknown` —— ⛔ 不和「登记了 0 个专题」混为一谈。
    """
    path = Path(db_path)
    if not path.is_file():
        return {"status": "unknown", "why": f"登记库不存在：{path}", "topics": []}
    if not _TABLE_NAME.match(str(table or "")):
        return {"status": "unknown", "why": f"表名不安全：{table!r}", "topics": []}
    try:
        from kernel.autoresearch.runtime.store import ReadonlyStore
        connection = ReadonlyStore(path).connect()
    except Exception as exc:  # noqa: BLE001 —— 只读打不开按不可判处理，⛔ 不建库
        return {"status": "unknown",
                "why": f"登记库只读打不开：{type(exc).__name__}: {exc}", "topics": []}
    try:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns:
            return {"status": "unknown", "why": f"schema 缺 {table} 表：{path}", "topics": []}
        missing = [name for name in ("id", "status") if name not in columns]
        if missing:
            return {"status": "unknown", "why": f"{table} 表缺列 {missing}", "topics": []}
        has_leader = "leader_session_id" in columns
        select = "id, status, leader_session_id" if has_leader else "id, status"
        topics = [{"id": row[0], "status": row[1],
                   "leader": (row[2] if has_leader else None)}
                  for row in connection.execute(f"SELECT {select} FROM {table} ORDER BY id")]
        result = {"status": "ok", "why": None, "topics": topics, "targets_ok": has_leader}
        if not has_leader:
            result["targets_why"] = f"{table} 表缺 leader_session_id：绑定名单不可判"
        return result
    except Exception as exc:  # noqa: BLE001 —— sqlite3.Error / StoreError 都按不可判处理
        return {"status": "unknown",
                "why": f"{table} 表不可读：{type(exc).__name__}: {exc}", "topics": []}
    finally:
        connection.close()


def _process_status_summary(workspace_path: Path, registry: dict, targets_db: Path) -> dict:
    """对每个已登记专题做一次只读证据投影（`reporting.write_process_status`）。

    ⛔ 不写研究页 `STATUS.md`；缺专题目录 / 缺库 / 坏 schema 一律显式 unknown，⛔ 不假绿。
    """
    if registry.get("status") != "ok":
        return {"status": "unknown", "why": registry.get("why") or "登记表不可读",
                "topics": {}, "count": 0, "written_count": 0}
    try:
        from kernel.autoresearch import reporting
    except Exception as exc:  # noqa: BLE001 —— 投影层不可用也只降级成 unknown
        return {"status": "unknown", "why": f"reporting 不可用：{type(exc).__name__}: {exc}",
                "topics": {}, "count": 0, "written_count": 0}
    db_arg = str(targets_db) if _inside_workspace(targets_db, workspace_path) else None
    entries: dict = {}
    for row in registry.get("topics") or []:
        topic = str(row.get("id") or "").strip()
        try:
            projection = reporting.write_process_status(workspace_path, topic, db=db_arg)
            entries[topic] = {
                "ok": True,
                "verdict": projection.get("verdict"),
                "delivery": (projection.get("documentation") or {}).get("state"),
                "written": projection.get("written"),
            }
        except Exception as exc:  # noqa: BLE001 —— 单个专题失败不拖垮整次 tick
            entries[topic] = {"ok": False, "unknown": True,
                              "why": f"{type(exc).__name__}: {exc}"}
    written = [name for name, item in entries.items() if item.get("ok")]
    if not entries:
        status = "empty"
    elif len(written) == len(entries):
        status = "written"
    elif written:
        status = "partial"
    else:
        status = "unknown"
    return {"status": status, "why": None if status in ("written", "empty") else "部分专题不可投影",
            "topics": entries, "count": len(entries), "written_count": len(written)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autodl-worker",
        description="单次采集机器可用性、判事件、落队列与绑定名单。只读；不开机、不关机、不投递会话消息。")
    parser.add_argument("--workspace", default=os.environ.get("AUTORESEARCH_WORKSPACE", ""),
                        help="工作区根（运行态落在 <workspace>/.autoresearch/）。也可用环境变量 AUTORESEARCH_WORKSPACE。")
    parser.add_argument("--token-env", default=None,
                        help=f"凭据所在的**环境变量名**（默认 {availability.DEFAULT_TOKEN_ENV}）。⛔ 不要在这里写凭据值。")
    parser.add_argument("--token-file", default=None,
                        help="凭据文件路径（显式文件引用；给了它就优先于 --token-env）。⛔ 值不会被打印。")
    parser.add_argument("--api-url", default=availability.DEFAULT_API_URL,
                        help="实例列表接口（默认公开地址）。")
    parser.add_argument("--machines", default="",
                        help="只看这几台（逗号或空格分隔）；列表里没有的会补一行「不在列表里」。空 = 全看。")
    parser.add_argument("--watch", default="",
                        help="事件只看这些机器（逗号分隔）；空 = 本次报告里出现的全部机器；all 同空。")
    parser.add_argument("--no-idle-trigger", action="store_true",
                        help="只按开机可用性判词触发，不看显卡余量。")
    parser.add_argument("--cooldown-minutes", type=float, default=events.DEFAULT_COOLDOWN_MINUTES,
                        help=f"同一台机器两次发事件的最小间隔（分钟，默认 {events.DEFAULT_COOLDOWN_MINUTES:g}）。")
    parser.add_argument("--timeout", type=float, default=availability.DEFAULT_TIMEOUT_S,
                        help=f"单次请求超时秒数（默认 {availability.DEFAULT_TIMEOUT_S:g}）。")
    parser.add_argument("--deadline", type=float, default=20.0,
                        help="整次查询的硬上限秒数（默认 20）。")
    parser.add_argument("--max-age-minutes", type=float, default=events.DEFAULT_MAX_AGE_MINUTES,
                        help=f"待投递事件超过这个分钟数就挪进 expired/（默认 {events.DEFAULT_MAX_AGE_MINUTES:g}）。")
    parser.add_argument("--targets-db", default=None,
                        help="负责人绑定所在 sqlite（只读）。默认 <workspace>/.autoresearch/runtime.sqlite3；"
                             "给了不存在的路径或坏 schema 会显式 unknown 且不动已有 targets.json。")
    parser.add_argument("--targets-table", default="topics",
                        help=f"绑定表名（默认 topics，须含 id/status/leader_session_id）。")
    parser.add_argument("--once", action="store_true",
                        help="只跑一次（默认行为就是一次；保留此开关给计划任务命令行用）。")
    parser.add_argument("--dry-run", action="store_true",
                        help="只采集、不写任何文件（不落事件、不动状态、不写绑定与快照）。")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是人话摘要。")
    parser.add_argument("--selftest", action="store_true", help="离线自检（不联网、不读真凭据、不开机）。")
    return parser


def _split_names(raw: str) -> "list[str]":
    return [item for item in raw.replace("，", ",").replace(",", " ").split() if item]


def run_once(args: argparse.Namespace, *, transport=None,
             now: "datetime | None" = None) -> dict:
    """跑一次。返回摘要字典（⛔ 不抛：失败也返回 `ok=False` 与原因）。"""
    workspace = str(args.workspace or "").strip()
    if not workspace:
        return {"ok": False, "why": "没有给 --workspace（也不知道运行态该落在哪）"}

    report = availability.collect(
        token_env=args.token_env,
        token_file=args.token_file,
        api_url=args.api_url,
        timeout=args.timeout,
        machines=_split_names(args.machines) or None,
        transport=transport,
        now=now,
        deadline_s=args.deadline,
        remember=False,
        cache_file=None,
    )

    summary: dict = {
        "ok": bool(report.get("ok")),
        "checked_at": report.get("checked_at"),
        "machines": len(report.get("machines") or []),
        "why": report.get("why_not"),
        "dry_run": bool(args.dry_run),
        "workspace": str(Path(workspace).resolve()),
        "emitted": [],
        "baseline": None,
        "expired": [],
        "targets": None,
        "targets_status": "unknown",
        "targets_why": None,
        "events_hold": None,
        "topics": None,
        "process_status": {"status": "unknown", "why": "未执行", "topics": {},
                           "count": 0, "written_count": 0},
        "availability_md": None,
    }
    if args.dry_run:
        summary["emitted"] = [row.get("machine") for row in report.get("machines") or []]
        summary["process_status"] = {"status": "skipped-dry-run", "why": "dry-run 不写任何文件",
                                     "topics": {}, "count": 0, "written_count": 0}
        return summary
    if not report.get("ok"):
        # 查询失败：不写事件、不动状态（否则下一轮会伪造出一串「恢复」事件）。
        return summary

    result = events.record(
        workspace,
        report,
        watch=(args.watch or None),
        idle_trigger=not args.no_idle_trigger,
        cooldown_minutes=args.cooldown_minutes,
        now=now,
    )
    summary["emitted"] = [event.get("machine") for event in result.get("emitted") or []]
    summary["baseline"] = result.get("baseline")

    summary["expired"] = events.expire_stale(workspace, max_age_minutes=args.max_age_minutes, now=now)

    # ---- 绑定名单：默认读工作区 runtime.sqlite3；读不出显式 unknown，⛔ 不猜、不清空已有名单 ----
    workspace_path = Path(workspace).resolve()
    targets_db = _targets_db_path(args, workspace_path)
    registry = _read_topic_registry(targets_db, args.targets_table)
    summary["topics"] = {"db": str(targets_db), "status": registry.get("status"),
                         "why": registry.get("why"), "count": len(registry.get("topics") or [])}
    if registry.get("status") == "ok" and registry.get("targets_ok"):
        sessions = events.bound_sessions(targets_db, table=args.targets_table)
        document = events.write_targets(workspace, sessions,
                                        source=f"sqlite:{args.targets_table}", now=now)
        summary["targets"] = document or None
        summary["targets_status"] = "published" if sessions else "empty"
        # 没有绑定会话时事件只进队列；DSH 插件按 fallback=hold 不投递（⛔ 不发给没人绑的会话）。
        summary["events_hold"] = not sessions
    else:
        existing = (events.read_targets(workspace)
                    if events.targets_path(workspace).is_file() else None)
        summary["targets"] = existing
        summary["targets_status"] = "unknown"
        summary["targets_why"] = registry.get("targets_why") or registry.get("why")
        summary["events_hold"] = None

    # ---- 专题 process_status：每个已登记专题一次只读证据投影（⛔ 不写 STATUS.md）----
    summary["process_status"] = _process_status_summary(workspace_path, registry, targets_db)

    snapshot_path = events.availability_markdown_path(workspace)
    try:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(availability.render_markdown(report, heading="###"), encoding="utf-8")
        summary["availability_md"] = str(snapshot_path)
    except OSError as exc:
        summary["availability_md_error"] = str(exc)

    return summary


def main(argv: "list[str] | None" = None, *, transport=None,
         now: "datetime | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.selftest:
        code = availability.selftest()
        if code != 0:
            return code
        return events.selftest()

    summary = run_once(args, transport=transport, now=now)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        if not summary.get("ok"):
            print(f"说不清：{summary.get('why') or '查询失败'}")
        else:
            emitted = summary.get("emitted") or []
            print(f"查了 {summary.get('machines')} 台（{summary.get('checked_at')}）"
                  f"{'（dry-run，未写盘）' if summary.get('dry_run') else ''}")
            if summary.get("baseline"):
                print("首次运行：只建基线，不发事件。")
            elif emitted:
                print(f"新事件 {len(emitted)} 条：{'、'.join(str(x) for x in emitted)}")
            else:
                print("没有新的「变成可用」事件。")
            process = summary.get("process_status") or {}
            if process.get("status") == "written":
                print(f"流程状态：{process.get('count')} 个已登记专题已刷新"
                      "（只写 process_status/，⛔ 不写 STATUS.md）")
            elif process.get("status") == "empty":
                print("流程状态：登记表里没有专题（没有可刷新的 process_status）。")
            elif process.get("status") in ("unknown", "partial"):
                print(f"流程状态：{process.get('status')}（{process.get('why')}）")
            if summary.get("targets_status") == "unknown":
                print(f"绑定名单：说不清（{summary.get('targets_why')}）；已有 targets.json 保持不动")
            elif summary.get("events_hold"):
                print("绑定名单：当前没有绑定会话；事件只留在队列，不投递（fallback=hold）")
    return EXIT_OK if summary.get("ok") else EXIT_UNKNOWN


if __name__ == "__main__":
    sys.exit(main())
