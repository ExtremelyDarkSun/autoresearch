# GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
# LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
# PURPOSE: 单次 worker —— 采一次机器状态 → 判事件 → 落队列/台账/状态 → 刷新绑定名单与可用性快照。
#          给 Windows 定时任务或人工跑；窗口内只跑一次（不驻留、不自己排下一轮）。
# ORIGIN: tool/topic_watchdog.py 的 availability-only 分支（抽取「采集+判事件+发绑定+写快照」
#         四件事，去页面维护/租约/锁等实例逻辑；所有部署参数显式输入）
# CUMULATIVE-DELTA: 首版。⛔ 不内置任何项目根、机器名单、凭据值或计划任务名；
#   凭据只从环境变量名或显式文件取；`--dry-run` 只采集不写盘；⛔ 不调用任何云机操作（那在 cloud.py，需显式授权）。
"""单次机器可用性 worker。

它做什么（一次调用）
--------------------
1. `availability.collect()` 只读查一次机器列表（有硬截止，不会把调用方拖住）；
2. `events.record()` 判「从开不了变成能开」，命中就落 `events/pending/`；
3. `events.expire_stale()` 把太旧的待投递事件挪进 `events/expired/`（没有插件时必须有人做）；
4. 给了 `--targets-db` 就把「负责人会话」写进 `events/targets.json`；
5. 把可读快照写到 `.autoresearch/availability.md`。

它不做什么
----------
⛔ 不开机、不关机、不重启、不跑 GPU 任务（那些在 `cloud.py`，且必须显式传 `allow_control=True`）。
⛔ 不投递会话消息（那是 DSH 侧插件的事）。
⛔ 读取机器状态**不等于**开机成功：报告只说明「看起来能开」。

退出码
------
0 = 查到了 · 2 = 说不清（没给 workspace / 凭据缺 / 查询失败）· 3 = 自检失败。
"""

from __future__ import annotations

import argparse
import json
import os
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
                        help="负责人绑定所在 sqlite（只读）。不给就不动 targets.json。")
    parser.add_argument("--targets-table", default="topics",
                        help="绑定表名（默认 topics）。")
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
        "availability_md": None,
    }
    if args.dry_run:
        summary["emitted"] = [row.get("machine") for row in report.get("machines") or []]
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

    if args.targets_db:
        sessions = events.bound_sessions(args.targets_db, table=args.targets_table)
        events.write_targets(workspace, sessions, source=f"sqlite:{args.targets_table}", now=now)
    summary["targets"] = events.read_targets(workspace) if events.targets_path(workspace).is_file() else None

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
    return EXIT_OK if summary.get("ok") else EXIT_UNKNOWN


if __name__ == "__main__":
    sys.exit(main())
