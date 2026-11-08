# GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
# LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
# PURPOSE: 把「某台机器从开不了变成能开」判成一条事件，落成待投递队列 + append-only 台账 + 状态快照，
#          并把「该投给谁」写成绑定名单；供 DSH 侧插件把事件送进会话。
# ORIGIN: tool/autodl_events.py（机制抽取：变化判定/冷却/首轮基线/队列/绑定发布；
#         真实机器名单、topics 库内容与本机绝对路径都不带入）
# CUMULATIVE-DELTA: 首版。三处结构性改动：
#   ① 运行态根固定为 `<workspace>/.autoresearch/`，队列在 `events/{pending,delivered,expired}`、
#      绑定在 `events/targets.json`、回执在 `events/delivered.jsonl` —— 全部由 `workspace` 参数决定，⛔ 无内置路径；
#   ② 默认 watch 为空 = 「这次报告里出现的全部机器」，⛔ 不再写死任何机器名；
#   ③ 绑定来源（sqlite 路径 + 表名/列名）是显式参数；`topics` 表只读，读不到就空名单。
"""机器可用性事件：判定、落盘、发布绑定。

判据（写死在这里，⛔ 下游不许自己发挥）
------------------------------------
| 触发 | 条件 | 意思 |
|---|---|---|
| `verdict` | 上次**不是**「可能可开 / 已在用」，这次**是** | 开机可用性翻成可用了 |
| `idle` | 上次空闲卡数是 0，这次 ≥ 1 | 它所在服务器腾出卡了（无卡档机器靠这条才看得见） |

⭐ 只发「变成可用」，⛔ 不发「变回不可用」：要的是「现在能上了」这一个方向。
⭐ 首次运行只建基线、一条不发 —— 否则第一次装上就会把整张表当事件喷出来。
⭐ 整次查询失败（`ok=false`）时**不动状态**：那时所有机器都会标「说不准」，
   若拿它当现状，下一分钟就会伪造出一串「恢复」事件（假事件比没事件更坏）。
⭐ 冷却期（默认 20 分钟）按机器算：状态在平台上来回翻的时候不刷屏。
⭐ 不在本次列表里的机器**不判变化**：那种记录说的是「不知道」，不是「开不了」。

东西落在哪（全部相对 `<workspace>`）
-----------------------------------
- `.autoresearch/events/pending/<id>.json` —— 待投递队列，一条事件一个文件（插件投递后移走）
- `.autoresearch/events/delivered/` · `.autoresearch/events/expired/` —— 插件挪进来的落点
- `.autoresearch/events/ledger.jsonl` —— append-only 台账，**永不消费**（复核「发过什么」读这一份）
- `.autoresearch/events/state.json` —— 上次快照 + 每台机器的上次发事件时间（只存判词与数字，不存凭据）
- `.autoresearch/events/targets.json` —— 该投给哪些会话（`{"sessions":[…],"authoritative":true,"source":"…"}`）
- `.autoresearch/events/delivered.jsonl` —— **投递回执**（插件写；本文件的 `read_receipts` 读）
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# ⚠️ Windows 控制台默认编码打不出部分符号 ⇒ 解释器层面改成 UTF-8（用 reconfigure，不重复包 wrapper）。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

TZ = timezone(timedelta(hours=8))

EVENT_KIND = "machine_available"

#: 「可用」的两种判词。`已在用` 也算可用 —— 它现在就能跑东西。
AVAILABLE = ("可能可开", "已在用")
#: 其余一律算「不是可用」：`可能开不了`（缺卡）与 `说不准`（判不出来）。
NOT_AVAILABLE = ("可能开不了", "说不准")

DEFAULT_COOLDOWN_MINUTES = 20.0
DEFAULT_MAX_AGE_MINUTES = 45.0

RUNTIME_DIR_NAME = ".autoresearch"
EVENTS_DIR_NAME = "events"


# ── 路径（全部由 workspace 决定，⛔ 无内置绝对路径）────────
def workspace_root(workspace: "str | Path") -> Path:
    if workspace is None or str(workspace).strip() == "":
        raise ValueError("workspace 必填：运行态根是 <workspace>/.autoresearch/")
    return Path(workspace).expanduser().resolve()


def runtime_root(workspace: "str | Path") -> Path:
    return workspace_root(workspace) / RUNTIME_DIR_NAME


def events_root(workspace: "str | Path") -> Path:
    return runtime_root(workspace) / EVENTS_DIR_NAME


def pending_dir(workspace: "str | Path") -> Path:
    return events_root(workspace) / "pending"


def delivered_dir(workspace: "str | Path") -> Path:
    return events_root(workspace) / "delivered"


def expired_dir(workspace: "str | Path") -> Path:
    return events_root(workspace) / "expired"


def ledger_path(workspace: "str | Path") -> Path:
    return events_root(workspace) / "ledger.jsonl"


def state_path(workspace: "str | Path") -> Path:
    return events_root(workspace) / "state.json"


def targets_path(workspace: "str | Path") -> Path:
    return events_root(workspace) / "targets.json"


def receipt_path(workspace: "str | Path") -> Path:
    """投递回执（DSH 侧插件的 `logFile` 应指向这里）。"""
    return events_root(workspace) / "delivered.jsonl"


def availability_markdown_path(workspace: "str | Path") -> Path:
    """机器可用性快照（人可读；不是运行态结论，只作页面材料）。"""
    return runtime_root(workspace) / "availability.md"


# ── 小工具 ────────────────────────────────────────────────
def _now(now: "datetime | None" = None) -> datetime:
    return now or datetime.now(TZ)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _atomic_write(path: "str | Path", text: str) -> None:
    """先写 `.tmp` 再 `os.replace` —— 读的人不会读到半个文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _slug(name: Any) -> str:
    """机名做文件名用：只留安全字符（中文保留，路径是 UTF-8）。"""
    keep = "".join(ch for ch in str(name) if ch.isalnum() or ch in "-_")
    return keep or "machine"


def _fmt_num(value: Any) -> str:
    return "未知" if value is None else str(value)


# ── 快照与变化判定 ────────────────────────────────────────
def snapshot(report: dict) -> dict:
    """把一份 `availability.collect()` 报告压成「状态快照」。⛔ 不存凭据、不存长解释。"""
    machines: dict = {}
    for row in (report or {}).get("machines") or []:
        name = str(row.get("machine") or "").strip()
        if not name:
            continue
        machines[name] = {
            "verdict": row.get("boot_availability"),
            "idle": row.get("gpu_idle_num"),
            "all": row.get("gpu_all_num"),
            "status": row.get("status"),
            "status_raw": row.get("status_raw"),
            "in_list": bool(row.get("in_list")),
        }
    return {"at": (report or {}).get("checked_at"),
            "ok": bool((report or {}).get("ok")),
            "machines": machines}


def load_state(workspace: "str | Path") -> dict:
    """读上次状态。坏文件 / 读不到一律当「没有上次」（⛔ 不抛）。"""
    try:
        doc = json.loads(state_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _watch_set(watch: "Iterable[str] | str | None") -> "list[str]":
    if watch is None:
        return []
    if isinstance(watch, str):
        watch = watch.replace("，", ",").split(",")
    return [str(item).strip() for item in watch if str(item).strip()]


def _names(watch: "Iterable[str] | str | None", current: dict) -> "list[str]":
    """要看的机器名单：空 / `all` = 这次报告里出现的全部机器；否则就是名单本身。"""
    if isinstance(watch, str) and watch.strip().lower() == "all":
        return sorted((current or {}).keys())
    names = _watch_set(watch)
    return names or sorted((current or {}).keys())


def build_text(name: str, triggers: "list[str]", prev: dict, cur: dict,
               checked_at: "str | None", source_note: str) -> str:
    """事件正文（**唯一一份实现**：插件与 `--print` 都直接用这里生成的文本）。"""
    lines = []
    if "verdict" in triggers:
        head = f"{name} 的开机可用性变成了「{cur.get('verdict')}」，现在看起来能开了"
    else:
        head = f"{name} 所在的服务器腾出显卡了（开机可用性判词没变）"
    lines.append(head + f"（{checked_at or '时间不明'} 查的）")
    lines.append("")
    lines.append(f"- 之前：{prev.get('verdict')} · 空闲显卡 {_fmt_num(prev.get('idle'))}")
    lines.append(f"- 现在：{cur.get('verdict')} · 空闲显卡 {_fmt_num(cur.get('idle'))} · "
                 f"运行状态 {cur.get('status') or '未知'}")
    if "idle" in triggers and "verdict" not in triggers:
        lines.append("- 变的是**显卡余量**：它所在服务器从 0 张空闲变成有闲卡了；"
                     "这台机本身的开机可用性判词没变（例如它还记着「无卡」启动档），"
                     "所以「能不能真开起来」仍然没验证过。")
    lines.append("")
    lines.append("这条是**只读查询**推出来的通知：没有试开机，也没动机器。"
                 "要不要开机、要不要起实验，按项目现有规矩办。")
    if source_note:
        lines.append(source_note)
    return "\n".join(lines)


def diff(prev_machines: dict, cur_machines: dict, watch: "Iterable[str] | str | None" = None,
         idle_trigger: bool = True) -> "list[dict]":
    """纯函数：拿两份 `snapshot()['machines']` 算事件。返回事件列表（未落盘）。"""
    out: "list[dict]" = []
    for name in _names(watch, cur_machines):
        prev = (prev_machines or {}).get(name)
        cur = (cur_machines or {}).get(name)
        if not prev or not cur:
            continue
        if not cur.get("in_list") or not prev.get("in_list"):
            # 不在本次实例列表里 ⇒ 这条记录说的是「不知道」，⛔ 不拿它判变化。
            continue
        triggers = []
        if prev.get("verdict") not in AVAILABLE and cur.get("verdict") in AVAILABLE:
            triggers.append("verdict")
        if idle_trigger:
            before, after = prev.get("idle"), cur.get("idle")
            if isinstance(before, int) and isinstance(after, int) and before == 0 and after >= 1:
                triggers.append("idle")
        if not triggers:
            continue
        out.append({
            "kind": EVENT_KIND,
            "machine": name,
            "triggers": triggers,
            "from": {"verdict": prev.get("verdict"), "idle": prev.get("idle"),
                     "status": prev.get("status")},
            "to": {"verdict": cur.get("verdict"), "idle": cur.get("idle"),
                   "status": cur.get("status"), "gpu": cur.get("all")},
            "checked_at": None,   # 由 record() 填成这次报告的 checked_at
            "source": "实例列表接口（只读；没试开机）",
        })
    return out


def record(workspace: "str | Path", report: dict, *,
           watch: "Iterable[str] | str | None" = None,
           idle_trigger: bool = True,
           cooldown_minutes: float = DEFAULT_COOLDOWN_MINUTES,
           now: "datetime | None" = None,
           source_note: str = "") -> dict:
    """一次扫描：判事件 → 落 pending + ledger → 更新状态。返回摘要字典（⛔ 不抛给调用方）。

    失败语义：
    - `report` 为空或 `report['ok']` 为假 ⇒ **什么都不写**（状态保留上次成功的快照）；
    - 没有上次快照 ⇒ 只建基线，`baseline=True`；
    - 其余异常（目录写不动等）⇒ 由调用方 try/except 吞掉，本函数尽量不抛。
    """
    moment = _now(now)
    if not report:
        return {"ok": False, "why": "没有报告", "emitted": []}
    if not report.get("ok"):
        return {"ok": False, "why": report.get("why_not") or "整次查询没成功", "emitted": []}

    current = snapshot(report)
    state = load_state(workspace)
    emitted: "list[dict]" = []

    if state.get("machines"):
        candidates = diff(state.get("machines"), current["machines"], watch, idle_trigger)
        last_emit = state.get("last_emit") or {}
        for event in candidates:
            name = event["machine"]
            previous_at = last_emit.get(name)
            if previous_at:
                try:
                    age_min = (moment - datetime.fromisoformat(previous_at)).total_seconds() / 60
                except (TypeError, ValueError):
                    age_min = 1e9
                if age_min < float(cooldown_minutes):
                    continue
            event["checked_at"] = current.get("at") or _iso(moment)
            event["at"] = _iso(moment)
            event["text"] = build_text(name, event["triggers"], event["from"], event["to"],
                                       event["checked_at"], source_note)
            event["title"] = f"{name} 现在看起来能开了"
            event["id"] = f"{moment.strftime('%Y%m%dT%H%M%S')}-{_slug(name)}-{'+'.join(event['triggers'])}"
            emitted.append(event)
            last_emit[name] = event["at"]
        baseline = False
    else:
        baseline = True
        last_emit = state.get("last_emit") or {}

    _write_events(workspace, emitted)
    state_out = dict(current)
    state_out["last_emit"] = last_emit
    state_out["saved_at"] = _iso(moment)
    try:
        _atomic_write(state_path(workspace), json.dumps(state_out, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        pass
    return {"ok": True, "baseline": baseline, "checked_at": current.get("at"),
            "emitted": emitted, "machines": len(current["machines"])}


def _write_events(workspace: "str | Path", events: "list[dict]") -> None:
    for event in events:
        try:
            _atomic_write(pending_dir(workspace) / f"{event['id']}.json",
                          json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        except OSError:
            pass
        try:
            ledger = ledger_path(workspace)
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass


def read_pending(workspace: "str | Path") -> "list[dict]":
    """读待投递队列（按文件名排序 = 按时间排序）。坏文件跳过。"""
    out: "list[dict]" = []
    directory = pending_dir(workspace)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(event, dict):
            event["_file"] = str(path)
            out.append(event)
    return out


def expire_stale(workspace: "str | Path", *, max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
                 now: "datetime | None" = None) -> "list[str]":
    """把太旧的待投递事件挪进 `expired/`。返回被挪走的文件名。

    ⚠️ 装了 DSH 插件时这件事由插件做；没有插件（只跑 Python）时由 worker 调本函数。
    ⛔ 时间解析不出来的**当旧的**（宁可丢一条，也不要拿旧状态骗人）。
    """
    moment = _now(now)
    moved: "list[str]" = []
    directory = pending_dir(workspace)
    if not directory.is_dir():
        return moved
    expired_dir(workspace).mkdir(parents=True, exist_ok=True)
    for path in sorted(directory.glob("*.json")):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(str(event.get("at")))
        except (OSError, ValueError, TypeError):
            at = None
        if at is not None and (moment - at).total_seconds() <= max_age_minutes * 60:
            continue
        try:
            os.replace(path, expired_dir(workspace) / path.name)
            moved.append(path.name)
        except OSError:
            continue
    return moved


# ── 绑定名单（该投给谁）──────────────────────────────────
def bound_sessions(db: "str | Path | None",
                   table: str = "topics",
                   session_column: str = "leader_session_id",
                   since_column: str = "leader_since",
                   status_column: str = "status",
                   active_value: str = "active") -> "list[str]":
    """读一份 sqlite 里「负责人的会话 id」，按绑定时间新→旧、去重。

    设计口径：事件应该发到**绑定这个专题的那个会话**上 ⇒ 这张表就是唯一的绑定来源。
    ⛔ 只读（`mode=ro`）；读不到 / 表坏了 / 没有绑定 ⇒ 空列表（⛔ 不抛，也不猜）。
    表名与列名都是显式参数：内核不假设调用方用哪套 schema。
    """
    if db is None:
        return []
    path = Path(db)
    if not path.is_file():
        return []
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        try:
            query = (f"SELECT {session_column}, {since_column} FROM {table} "
                     f"WHERE {status_column}=? AND {session_column} IS NOT NULL "
                     f"AND TRIM({session_column}) <> ''")
            rows = connection.execute(query, (active_value,)).fetchall()
        except sqlite3.Error:
            return []
    finally:
        connection.close()
    seen: set = set()
    out: "list[str]" = []
    for session_id, since in sorted(rows, key=lambda row: str(row[1] or ""), reverse=True):
        text = str(session_id).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def write_targets(workspace: "str | Path", sessions: "Iterable[str]",
                  *, source: str = "bound-sessions", now: "datetime | None" = None) -> dict:
    """把「该投给谁」写成一个小文件，供 DSH 侧插件读（插件不直接读 sqlite）。

    `authoritative: True` 表示这份名单是**判出来的**（来自负责人绑定），不是用户手写的。
    """
    document = {
        "sessions": [str(item).strip() for item in (sessions or []) if str(item).strip()],
        "authoritative": True,
        "source": source,
        "at": _iso(_now(now)),
    }
    try:
        _atomic_write(targets_path(workspace), json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        return document
    except OSError:
        return {}


def read_targets(workspace: "str | Path") -> dict:
    """读绑定名单（坏文件按空名单处理；⛔ 不抛）。"""
    try:
        doc = json.loads(targets_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sessions": [], "authoritative": False, "source": None}
    if not isinstance(doc, dict):
        return {"sessions": [], "authoritative": False, "source": None}
    sessions = doc.get("sessions") if isinstance(doc.get("sessions"), list) else []
    return {
        "sessions": [str(item).strip() for item in sessions if str(item).strip()],
        "authoritative": doc.get("authoritative") is True,
        "source": doc.get("source") if isinstance(doc.get("source"), str) else None,
    }


# ── 投递回执 ─────────────────────────────────────────────
def append_receipt(workspace: "str | Path", record: dict) -> bool:
    """追加一行投递回执到 `events/delivered.jsonl`。⛔ 写不动不许抛。"""
    try:
        target = receipt_path(workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_receipts(workspace: "str | Path", *, kind: "str | None" = None) -> "list[dict]":
    """读回执（坏行跳过）。`kind` 过滤 `apply` / `delivered` / `waiting`。"""
    out: "list[dict]" = []
    try:
        lines = receipt_path(workspace).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if kind is not None and entry.get("kind") != kind:
            continue
        out.append(entry)
    return out


# ── 离线自检（不联网、不读真凭据）────────────────────────
def _report(rows: "list[tuple[str, dict]]", ok: bool = True,
            checked_at: str = "2026-01-01T00:30:00+08:00") -> dict:
    return {"ok": ok, "checked_at": checked_at, "why_not": None if ok else "接口拒绝",
            "machines": [dict({"machine": name, "in_list": True}, **row) for name, row in rows]}


def _row(verdict: str, idle: Any, status: str = "已关机", in_list: bool = True) -> dict:
    return {"boot_availability": verdict, "gpu_idle_num": idle, "gpu_all_num": 8,
            "status": status, "status_raw": "shutdown", "in_list": in_list}


def selftest() -> int:
    """离线自检：临时目录里跑完整事件生命周期，⛔ 不联网、不开机。"""
    import tempfile

    fails: "list[str]" = []

    def chk(condition: bool, message: str) -> bool:
        if not condition:
            fails.append(message)
        print(("  ok  " if condition else "  FAIL ") + message)
        return condition

    base = [("m-01", _row("可能开不了", 0)), ("m-02", _row("已在用", 0, "运行中")),
            ("m-03", _row("说不准", 0)), ("m-04", _row("说不准", 0))]

    with tempfile.TemporaryDirectory() as tmp:
        print("① 首次只建基线，⛔ 不喷事件")
        first = record(tmp, _report(base))
        chk(first["baseline"] is True and not first["emitted"], "首次应 baseline 且 0 事件")
        chk(state_path(tmp).is_file(), "应写出 state.json")
        chk(read_pending(tmp) == [], "首次不应写 pending 文件")

        print("② 可能开不了 → 可能可开：一条 verdict 事件")
        second = record(tmp, _report([("m-01", _row("可能可开", 0))] + base[1:]))
        chk(len(second["emitted"]) == 1 and second["emitted"][0]["machine"] == "m-01",
            f"应 1 条 m-01 事件，实际 {len(second['emitted'])}")
        event = second["emitted"][0] if second["emitted"] else {}
        chk(event.get("triggers") == ["verdict"], f"触发应是 verdict，实际 {event.get('triggers')}")
        chk("m-01" in (event.get("text") or "") and "可能可开" in (event.get("text") or ""),
            "正文应点名机器与新判词")
        files = sorted(pending_dir(tmp).glob("*.json"))
        chk(len(files) == 1, f"pending 应 1 个文件，实际 {len(files)}")
        chk(len(ledger_path(tmp).read_text(encoding="utf-8").splitlines()) == 1, "ledger 应 1 行")

        print("③ 已是可用 ⇒ 再扫不发（幂等）")
        record(tmp, _report([("m-01", _row("可能可开", 1))] + base[1:]))
        again = record(tmp, _report([("m-01", _row("可能可开", 3))] + base[1:]))
        chk(not again["emitted"], f"应 0 条，实际 {len(again['emitted'])}")

        print("④ 只有显卡余量 0→≥1（判词没变）：一条 idle 事件")
        idle = record(tmp, _report(base[:2] + [("m-03", _row("说不准", 4)), base[3]]))
        chk(len(idle["emitted"]) == 1 and idle["emitted"][0]["triggers"] == ["idle"],
            f"应 1 条 idle，实际 {[(e['machine'], e['triggers']) for e in idle['emitted']]}")
        chk("能不能真开起来" in (idle["emitted"][0]["text"] if idle["emitted"] else ""),
            "idle 事件正文要说清「没验证能不能开」")

        print("⑤ 冷却期：20 分钟内再翻一次不发")
        start = datetime(2026, 1, 1, 1, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp2:
            record(tmp2, _report(base), now=start)
            first_flip = record(tmp2, _report([("m-01", _row("可能可开", 1))] + base[1:]),
                                now=start + timedelta(minutes=1))
            chk(len(first_flip["emitted"]) == 1, "第一次翻转应发")
            record(tmp2, _report(base), now=start + timedelta(minutes=2))
            cooled = record(tmp2, _report([("m-01", _row("可能可开", 1))] + base[1:]),
                            now=start + timedelta(minutes=5))
            chk(not cooled["emitted"], f"冷却期内应 0 条，实际 {len(cooled['emitted'])}")
            record(tmp2, _report(base), now=start + timedelta(minutes=10))
            warmed = record(tmp2, _report([("m-01", _row("可能可开", 1))] + base[1:]),
                            now=start + timedelta(minutes=40))
            chk(len(warmed["emitted"]) == 1, f"过冷却期应发 1 条，实际 {len(warmed['emitted'])}")

        print("⑥ 整次查询失败：⛔ 不发事件，⛔ 不动状态")
        with tempfile.TemporaryDirectory() as tmp3:
            record(tmp3, _report(base))
            before = state_path(tmp3).read_text(encoding="utf-8")
            failed = record(tmp3, {"ok": False, "checked_at": "2026-01-01T00:40:00+08:00",
                                   "why_not": "超时",
                                   "machines": [dict({"machine": name, "in_list": True},
                                                     **_row("说不准", None)) for name, _ in base]})
            chk(failed["ok"] is False and not failed["emitted"], "失败时应 0 事件")
            chk(state_path(tmp3).read_text(encoding="utf-8") == before, "失败时 state.json 应逐字节不变")

        print("⑦ 名单外的机器翻成可用：不发；watch=all 才发")
        with tempfile.TemporaryDirectory() as tmp4:
            record(tmp4, _report(base))
            outside = record(tmp4, _report(base), watch=["m-01", "m-02"],
                             now=datetime(2026, 1, 1, 2, 0, tzinfo=TZ))
            record(tmp4, _report(base + [("m-99", _row("可能开不了", 0))]),
                   now=datetime(2026, 1, 1, 2, 1, tzinfo=TZ))
            not_watched = record(tmp4, _report(base + [("m-99", _row("可能可开", 5))]),
                                 watch=["m-01"], now=datetime(2026, 1, 1, 2, 2, tzinfo=TZ))
            chk(not not_watched["emitted"], "不在名单里的机器不应发")
            record(tmp4, _report(base + [("m-99", _row("可能开不了", 0))]),
                   now=datetime(2026, 1, 1, 3, 0, tzinfo=TZ))
            watched = record(tmp4, _report(base + [("m-99", _row("可能可开", 5))]),
                             watch="all", now=datetime(2026, 1, 1, 3, 30, tzinfo=TZ))
            chk([e["machine"] for e in watched["emitted"]] == ["m-99"],
                f"watch=all 应发 m-99，实际 {[e['machine'] for e in watched['emitted']]}")

        print("⑧ 不在实例列表里的机器：⛔ 不判变化")
        with tempfile.TemporaryDirectory() as tmp5:
            record(tmp5, _report(base))
            gone = record(tmp5, _report([("m-01", _row("可能可开", 3, in_list=False))] + base[1:]))
            chk(not gone["emitted"], f"不在列表里不应发，实际 {len(gone['emitted'])}")

        print("⑨ 坏 state.json / 坏事件文件：⛔ 不抛")
        with tempfile.TemporaryDirectory() as tmp6:
            state_path(tmp6).parent.mkdir(parents=True, exist_ok=True)
            state_path(tmp6).write_text("{not json", encoding="utf-8")
            broken = record(tmp6, _report(base))
            chk(broken["baseline"] is True, "坏 state 应退化成「没有上次」")
            pending_dir(tmp6).mkdir(parents=True, exist_ok=True)
            (pending_dir(tmp6) / "bad.json").write_text("{", encoding="utf-8")
            chk(read_pending(tmp6) == [], "坏事件文件应被跳过")

        print("⑩ 事件文件能被 read_pending 读回，且带 _file")
        with tempfile.TemporaryDirectory() as tmp7:
            record(tmp7, _report(base))
            record(tmp7, _report([("m-01", _row("可能可开", 1))] + base[1:]))
            got = read_pending(tmp7)
            chk(len(got) == 1 and got[0].get("machine") == "m-01" and got[0].get("_file"),
                f"read_pending 应读回 1 条带 _file，实际 {got}")

        print("⑪ 绑定名单：只认 active 且有负责人，按绑定时间新→旧")
        with tempfile.TemporaryDirectory() as tmp8:
            db = Path(tmp8) / "topics.sqlite"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, status TEXT, "
                                   "leader_session_id TEXT, leader_since TEXT)")
                connection.executemany("INSERT INTO topics VALUES (?,?,?,?)", [
                    ("t-old", "active", "session-b", "2026-01-01T01:00:00+08:00"),
                    ("t-new", "active", "session-a", "2026-01-01T02:00:00+08:00"),
                    ("t-dup", "active", "session-a", "2026-01-01T01:30:00+08:00"),
                    ("t-none", "active", None, None),
                    ("t-arch", "archived", "session-z", "2026-01-01T03:00:00+08:00"),
                ])
                connection.commit()
            finally:
                connection.close()
            chk(bound_sessions(db) == ["session-a", "session-b"],
                f"应得 [session-a, session-b]，实际 {bound_sessions(db)}")
            chk(bound_sessions(Path(tmp8) / "missing.sqlite") == [], "库不存在返回空且不抛")
            broken_db = Path(tmp8) / "broken.sqlite"
            broken_db.write_text("not a db", encoding="utf-8")
            chk(bound_sessions(broken_db) == [], "库是坏的也返回空且不抛")
            doc = write_targets(tmp8, bound_sessions(db), source="test")
            on_disk = json.loads(targets_path(tmp8).read_text(encoding="utf-8"))
            chk(doc and on_disk["sessions"] == ["session-a", "session-b"], "targets.json 写出了名单")
            chk(on_disk["authoritative"] is True and on_disk["source"] == "test",
                "targets.json 标明是「判出来的」与来源")
            write_targets(tmp8, [])
            chk(json.loads(targets_path(tmp8).read_text(encoding="utf-8"))["sessions"] == [],
                "没有绑定时写出空名单（插件据此 hold）")

        print("⑫ 回执与过期")
        with tempfile.TemporaryDirectory() as tmp9:
            chk(append_receipt(tmp9, {"kind": "apply", "pid": 1}) is True, "回执能写")
            chk(append_receipt(tmp9, {"kind": "delivered", "machines": ["m-01"]}) is True, "回执能追加")
            receipts = read_receipts(tmp9)
            chk(len(receipts) == 2 and read_receipts(tmp9, kind="delivered")[0]["machines"] == ["m-01"],
                "回执能按 kind 读回")
            record(tmp9, _report(base))
            record(tmp9, _report([("m-01", _row("可能可开", 1))] + base[1:]),
                   now=datetime(2026, 1, 1, 1, 30, tzinfo=TZ))
            future = datetime(2026, 1, 1, 3, 0, tzinfo=TZ)
            moved = expire_stale(tmp9, max_age_minutes=45, now=future)
            chk(len(moved) == 1 and not list(pending_dir(tmp9).glob("*.json")),
                f"过旧事件应挪进 expired，实际 {moved}")

    print()
    if fails:
        print(f"FAIL: {len(fails)} 条不通过")
        for item in fails:
            print("  - " + item)
        return 3
    print("PASS: autodl events 离线自检全通过（未联网、未开机）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
