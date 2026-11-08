# GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
# LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
# PURPOSE: 只读采集「每台机器现在的运行状态、所在服务器还有几张空闲显卡、看起来开得起来吗」，
#          产出可 JSON 序列化的报告，并渲染 markdown / 一行摘要。供 worker、事件判定与页面复用。
# ORIGIN: tool/autodl_availability.py（机制抽取：状态采集/判读/有界耗时/机名缓存；
#         真实 token 值、机器名单与本机固定路径都不带入）
# CUMULATIVE-DELTA: 首版。四处结构性改动：
#   ① 凭据只从**环境变量名**或**显式文件路径**取，默认 env 名 `AUTODL_TOKEN`，⛔ 不内置任何值或本机路径；
#   ② 传输层可注入（`transport`），没有它就没人能离线测；报告只记凭据来源，⛔ 不记凭据值；
#   ③ 机器名单、接口地址、超时、硬截止、机名缓存路径全部是显式参数；
#   ④ 判词与判据通用化：已关机 ≠ 不可用 · 字段不够标未知 · 不靠试开机。
"""只读采集机器可用性。

边界（⛔ 不许越过）
------------------
本文件**只发一次实例列表查询**（语义上是「读」），**绝不调用开机 / 关机 / 重启**，
因此不会开机、不会开 GPU、不会花钱。唯一能真正证明「开得起来」的办法是调一次开机 —— 那件事这里**不做**，
所以「开机可用性」一栏永远只是「看起来」的判词，`certain` 恒为 False（正在跑的除外）。

⚠️ **读取机器状态 ≠ 开机成功**：这里判的是字段，不是真开机。任何下游都不得把本报告当成「已经开机」。

耗时有界
--------
`deadline_s`（默认 20 秒）是整次查询的硬上限：单次请求超时取 `min(timeout, deadline_s)`，
外层再套 daemon 线程 + `Event.wait(deadline)` 兜底 ⇒ 就算 DNS 卡住，调用方最多等这么久也会拿到结果。
⛔ 不做「每台再来一次 detail 查询」：那只会把 1 次请求变成 N+1 次，拿不到新信息。

判据（写死在这里，⛔ 下游不许自己发挥）
------------------------------------
| 情况 | 运行状态 | 空闲显卡 | 开机可用性 | 说明 |
|---|---|---|---|---|
| `status=running` | 运行中 | 照抄字段 | 已在用 | 已开着，不需要开机 |
| `status=starting` | 启动中 | 照抄字段 | 已在用 | 同上 |
| `status=stopping` | 关机中 | 照抄字段 | 说不准 | 过渡态，等它停稳 |
| `status=shutdown` 且 `gpu_idle_num>0` | 已关机 | n / N | 可能可开 | 服务器还有闲卡；存储条件看不到 |
| `status=shutdown` 且 `gpu_idle_num=0` | 已关机 | 0 / N | 可能开不了 | 现在缺卡；**不是判死**，没试开机 |
| `start_mode=non_gpu` | 照抄 | 照抄 | 说不准 | 无卡档不占显卡，闲卡数说明不了 |
| 空闲卡字段缺失 / 不是数字 | 照抄 | 未知 | 说不准 | 接口字段不够，不猜 |
| 列表查不到这台机 | 未知 | 未知 | 说不准 | 不在本账号实例列表里 |
| 整次查询失败（超时 / 被拒 / 无凭据） | 未知 | 未知 | 说不准 | 全部标未知，并给原因 |

⭐ **关机不是「不可用」**：`shutdown` 只说明它现在没开，不说明它开不起来。
⭐ **字段不够就标未知**：缺 `gpu_idle_num` / `gpu_all_num` 时，GPU 余量与开机可用性都写「未知」+ 原因。
⭐ **不靠开机试探**：任何「试一次能不能开」都不在本文件里，也不许以后加进来。

凭据
----
只从**环境变量名**（默认 `AUTODL_TOKEN`，可用 `token_env=` 改）或**显式文件路径**（`token_file=`）读。
读到的值只放进请求头，**不打印、不落盘、不进报告**；报告里只有「来源类型」与「有没有拿到」。
实例记录只按白名单取字段，密码 / 令牌 / SSH 命令类字段一律不取。
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# ⚠️ Windows 控制台默认编码打不出部分符号 ⇒ 解释器层面改成 UTF-8（用 reconfigure，不重复包 wrapper）。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

TZ = timezone(timedelta(hours=8))

#: 实例列表接口（公开地址；部署方可用 `api_url=` 覆盖）。
DEFAULT_API_URL = "https://www.autodl.com/api/v1/instance"
#: 默认凭据环境变量**名**（不是值）。
DEFAULT_TOKEN_ENV = "AUTODL_TOKEN"
DEFAULT_TIMEOUT_S = 15.0
MAX_BODY_BYTES = 4 * 1024 * 1024

STATUS_ZH = {
    "running": "运行中",
    "starting": "启动中",
    "stopping": "关机中",
    "shutdown": "已关机",
}

#: 「可用」的两种判词。`已在用` 也算可用 —— 它现在就能跑东西。
AVAILABLE_VERDICTS = ("可能可开", "已在用")

#: ⛔ 白名单：只取这些字段。凭据类字段（root_password / jupyter_token / ssh_command / phone）不在其中，
#:    所以就算接口多给了它们，也进不了报告。
SAFE_KEYS = (
    "machine_alias",
    "region_name",
    "status",
    "status_at",
    "start_mode",
    "snapshot_gpu_alias_name",
    "gpu_all_num",
    "gpu_idle_num",
    "payg_price",
    "root_fs_used_rate",
    "disk_health_status",
    "uuid",
    "req_gpu_amount",
    "charge_type",
)

CRITERIA = {
    "read_only": "只发一次实例列表查询；不调用开机 / 关机 / 重启",
    "shutdown_is_not_unavailable": "已关机只说明现在没开，不说明开不起来",
    "unknown_when_fields_missing": "缺 gpu_idle_num / gpu_all_num 时，GPU 余量与开机可用性都标未知",
    "never_probe_by_booting": "不用「试一次开机」测可用性，所以开机可用性恒为推断",
    "gpu_headroom_semantics": "gpu_idle_num / gpu_all_num 按「所在服务器」理解（总数 ≠ 本机租的那几张）",
    "reading_is_not_booting": "读取状态不等于开机成功；报告不构成任何开机事实",
}

#: 传输层签名：(url, body_bytes, headers, timeout) -> (http_status, body_bytes)。
#: 自检与测试靠注入它跑离线；生产默认用 urllib。
Transport = Callable[[str, bytes, dict, float], "tuple[int, bytes]"]


# ── 小工具 ────────────────────────────────────────────────
def _int(value: Any) -> "int | None":
    """把接口里的数取成 int；取不到就返回 None（⛔ 不当 0 用）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


_TOKENISH = re.compile(r"[A-Za-z0-9_\-\.]{32,}")


def redact(text: str) -> str:
    """失败信息里可能出现长串（token / 口令 / 会话号）—— 一律抹掉再进报告。"""
    if not text:
        return ""
    text = re.sub(r"(?i)(authorization|token|password|passwd|secret|口令|密码)\s*[:=：]\s*\S+",
                  r"\1: <已抹掉>", str(text))
    return _TOKENISH.sub("<已抹掉>", text)


def build_headers(token: str) -> dict:
    """照实例列表接口的习惯构造请求头。⛔ 头里除了 token 没有任何身份信息。"""
    return {
        "accept": "*/*",
        "authorization": token,
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://www.autodl.com",
        "referer": "https://www.autodl.com/console/instance/list",
        "user-agent": "Mozilla/5.0 autoresearch-availability/1.0",
    }


def resolve_credential(*, env_var: "str | None" = None,
                       token_file: "str | Path | None" = None) -> "tuple[str | None, str | None, str | None]":
    """读凭据。返回 `(token, ref, why)`。

    **优先级**：显式 `token_file` > 显式 `env_var` > 默认环境变量名（`DEFAULT_TOKEN_ENV`）。

    `ref` 只描述来源，形如 `env:AUTODL_TOKEN` / `file:<路径>` / `None`；`why` 是「没拿到」的原因。
    ⛔ 返回的 token 值不进任何日志、报告或异常文本。
    """
    if token_file:
        path = Path(token_file)
        try:
            if not path.is_file():
                return None, f"file:{path}", f"凭据文件不存在：{path}"
            token = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            return None, f"file:{path}", f"凭据文件读不动：{redact(str(exc))[:120]}"
        if not token:
            return None, f"file:{path}", f"凭据文件是空的：{path}"
        return token, f"file:{path}", None

    name = env_var or DEFAULT_TOKEN_ENV
    token = os.environ.get(name, "").strip()
    if not token:
        return None, f"env:{name}", f"环境变量 {name} 没有值（也可以用 token_file 指到一份凭据文件）"
    return token, f"env:{name}", None


def urllib_transport(url: str, body: bytes, headers: dict, timeout: float) -> "tuple[int, bytes]":
    """默认传输：一次 POST，超时必有。⛔ 不打印请求头。"""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read(MAX_BODY_BYTES)


# ── 查询（只读）───────────────────────────────────────────
def fetch_instances(token: str, *,
                    api_url: str = DEFAULT_API_URL,
                    timeout: float = DEFAULT_TIMEOUT_S,
                    transport: "Transport | None" = None) -> dict:
    """只发一次实例列表查询。返回 `{"ok", "why", "instances"}`。

    ⛔ 超时必有；失败只给原因，不给请求头、不给凭据。
    """
    body = json.dumps({"page_index": 1, "page_size": 100}).encode("utf-8")
    headers = build_headers(token)
    send = transport or urllib_transport
    try:
        status, raw = send(api_url, body, headers, float(timeout))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "why": f"接口返回 HTTP {exc.code}（拿不到机器列表）", "instances": []}
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            return {"ok": False, "why": f"查询超时（超过 {timeout:g} 秒没有回应）", "instances": []}
        return {"ok": False, "why": f"连不上接口：{redact(str(reason))[:120]}", "instances": []}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "why": f"查询超时（超过 {timeout:g} 秒没有回应）", "instances": []}
    except OSError as exc:
        return {"ok": False, "why": f"查询中断：{redact(str(exc))[:120]}", "instances": []}

    if int(status) >= 400:
        return {"ok": False, "why": f"接口返回 HTTP {status}（拿不到机器列表）", "instances": []}
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {"ok": False, "why": "接口返回的不是可解析的 JSON", "instances": []}
    if not isinstance(doc, dict):
        return {"ok": False, "why": "接口返回的结构不认识（顶层不是对象）", "instances": []}

    code = str(doc.get("code") or "")
    if code and code != "Success":
        message = redact(str(doc.get("msg") or ""))[:160]
        if code.lower().startswith("authorizefailed") or "登录" in message:
            return {"ok": False,
                    "why": f"凭据被拒（{code}）：{message or '登录超时'} ⇒ 需要重新获取登录凭据（本文件不会打印凭据）",
                    "instances": []}
        return {"ok": False, "why": f"接口拒绝（{code}）：{message}", "instances": []}

    data = doc.get("data") or {}
    listing = data.get("list") if isinstance(data, dict) else None
    if not isinstance(listing, list):
        return {"ok": False, "why": "接口成功但没给出机器列表（data.list 缺失）", "instances": []}
    return {"ok": True, "why": None, "instances": [item for item in listing if isinstance(item, dict)]}


# ── 判读 ──────────────────────────────────────────────────
def _gpu_headroom(inst: dict) -> dict:
    """GPU 余量：只认接口给的闲卡字段；给不出就「未知」+ 原因。"""
    idle = _int(inst.get("gpu_idle_num"))
    total = _int(inst.get("gpu_all_num"))
    if idle is None or total is None:
        return {"known": False, "idle": None, "all": None, "text": "未知",
                "why": "接口没有给出可用的空闲显卡字段（gpu_idle_num / gpu_all_num）"}
    if total <= 0:
        return {"known": False, "idle": idle, "all": total, "text": "未知",
                "why": f"接口给的总显卡数是 {total}，不像有效值"}
    if idle < 0:
        return {"known": False, "idle": idle, "all": total, "text": "未知",
                "why": f"接口给的空闲显卡数是 {idle}，不像有效值"}
    return {"known": True, "idle": idle, "all": total,
            "text": f"{idle} / {total} 张空闲", "why": None}


def _boot_verdict(status: "str | None", start_mode: "str | None", head: dict,
                  in_list: bool) -> "tuple[str, str, bool]":
    """开机可用性。返回 `(判词, 原因, 有没有把握)`。

    ⭐ 三条硬规矩：① 已关机 ≠ 不可用；② 字段不够就「说不准」；③ 没试过开机，所以恒无把握。
    """
    if not in_list:
        return ("说不准", "这台机不在本次查询拿到的机器列表里（可能已释放或改了名）", False)
    if status in ("running", "starting"):
        return ("已在用", "已经开着，不需要开机", True)
    if status == "stopping":
        return ("说不准", "正在关机，机器处于过渡状态，等它停稳再看", False)
    if status is None:
        return ("说不准", "接口没有给出运行状态，判断不了", False)
    if status not in STATUS_ZH:
        return ("说不准", f"运行状态是接口里的新值「{status}」，本文件不认识", False)
    if not head["known"]:
        return ("说不准", head["why"] or "接口没给空闲显卡字段，判断不了能不能开起来", False)
    if start_mode == "non_gpu":
        return ("说不准",
                "这台机记录的启动档是「无卡」（不占显卡），所以空闲显卡数说明不了它能不能开起来", False)
    if head["idle"] > 0:
        return ("可能可开",
                f"它所在的服务器还有 {head['idle']} 张空闲显卡；服务器存储够不够，只读接口看不到，"
                f"所以这只是「看起来能开」（没有试开机）", False)
    return ("可能开不了",
            "它所在的服务器当前没有空闲显卡；这只说明现在缺卡，**不算判死**（没有试开机）", False)


def classify(inst: dict, in_list: bool = True) -> dict:
    """把一条实例记录判成一行结论。只取白名单字段。"""
    safe = {key: inst.get(key) for key in SAFE_KEYS if key in inst}
    alias = str(safe.get("machine_alias") or "").strip() or "（无名）"
    status = str(safe.get("status") or "").strip() or None
    start_mode = str(safe.get("start_mode") or "").strip() or None
    head = _gpu_headroom(safe)
    verdict, why, certain = _boot_verdict(status, start_mode, head, in_list)

    notes = []
    if start_mode == "non_gpu":
        notes.append("启动档记录为「无卡」，开机不占显卡，但那样也跑不了要显卡的任务")
    disk_pct = None
    if isinstance(safe.get("root_fs_used_rate"), (int, float)):
        disk_pct = round(float(safe["root_fs_used_rate"]) * 100, 1)
    price = _int(safe.get("payg_price"))

    return {
        "machine": alias,
        "region": safe.get("region_name"),
        "uuid": safe.get("uuid"),
        "status_raw": status,
        "status": STATUS_ZH.get(status, "未知") if status else "未知",
        "gpu_name": safe.get("snapshot_gpu_alias_name"),
        "gpu_headroom": head["text"],
        "gpu_headroom_known": head["known"],
        "gpu_idle_num": head["idle"],
        "gpu_all_num": head["all"],
        "start_mode": start_mode,
        "boot_availability": verdict,
        "boot_reason": why,
        "certain": certain,
        "in_list": in_list,
        "price_yuan_per_hour": (round(price / 100.0, 2) if price is not None else None),
        "disk_used_percent": disk_pct,
        "status_at": safe.get("status_at"),
        "notes": notes,
    }


def _missing_row(alias: str, query_why: "str | None" = None) -> dict:
    """列表里没有这台机时补的一行。⛔ 查询本身失败时，原因不能写成「这台机不在列表里」。"""
    row = classify({"machine_alias": alias}, in_list=False)
    if query_why:
        short = query_why if len(str(query_why)) <= 30 else str(query_why)[:30] + "…"
        row["boot_reason"] = f"这次没拿到机器列表（{short}），所以这台机现在判断不了"
        row["boot_availability"] = "说不准"
    return row


# ── 有界耗时 + 机名缓存 ───────────────────────────────────
def _call_with_deadline(fn: Callable[[], Any], deadline_s: float) -> "tuple[bool, Any]":
    """在**硬截止**内跑 `fn`。返回 `(跑完了吗, 结果)`。

    ⚠️ `urlopen(timeout=…)` 只约束 socket 读写，DNS 卡住时不受它管；
    所以再套一层 daemon 线程 + `Event.wait(deadline)`：到点就放弃，
    调用方（每分钟一次的 tick）**一定**能在 `deadline_s` 之后拿到结果。
    被放弃的线程是 daemon，不会拖住进程退出。
    """
    holder: dict = {}
    done = threading.Event()

    def _run() -> None:
        try:
            holder["value"] = fn()
        except BaseException as exc:      # noqa: BLE001 —— 任何异常都要变成「说不清」，不许抛给调用方
            holder["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_run, name="autodl-availability", daemon=True)
    thread.start()
    finished = done.wait(max(0.05, float(deadline_s)))
    return finished, holder.get("value")


def read_name_cache(path: "str | Path | None") -> "list[str]":
    """读上次的机名清单。坏文件 / 读不到一律当没有（⛔ 不许因此报错）。"""
    if not path:
        return []
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = doc.get("machines") if isinstance(doc, dict) else None
    return [str(x) for x in names if str(x).strip()] if isinstance(names, list) else []


def write_name_cache(path: "str | Path | None", names, at: str) -> bool:
    """只在机名清单变了时写。⛔ 写不动不报错（目录可能不在、可能没权限）。"""
    if not path or not names:
        return False
    if read_name_cache(path) == list(names):
        return False
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"at": at, "machines": list(names)}, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError:
        return False


_LAST_GOOD: "list[str]" = []
_CACHE: dict = {"at": None, "report": None}


# ── 对外主接口 ────────────────────────────────────────────
def collect(*,
            token_env: "str | None" = None,
            token_file: "str | Path | None" = None,
            api_url: str = DEFAULT_API_URL,
            timeout: float = DEFAULT_TIMEOUT_S,
            machines: "list[str] | None" = None,
            transport: "Transport | None" = None,
            now: "datetime | None" = None,
            cache_seconds: float = 0.0,
            deadline_s: float = 20.0,
            remember: bool = True,
            cache_file: "str | Path | None" = None) -> dict:
    """查一次，返回可 JSON 序列化的报告字典。

    参数
    ----
    token_env / token_file : 凭据来源（环境变量**名** 或显式文件路径）。都不给就用默认环境变量名。
    api_url    : 实例列表接口。
    timeout    : 单次请求的超时秒数。
    machines   : 只保留这些机名，按给定顺序；列表里没有的会补一行「不在列表里」。
    transport  : 注入假接口用（签名 `(url, body, headers, timeout) -> (status, body_bytes)`）；
                 None = 走真接口。**离线测试就是靠它**。
    now        : 检查时间（默认本机当前时间，+08:00）。
    cache_seconds : >0 时把上一次结果缓存这么久（高频调用时不至于每次都联网）。
    deadline_s : 整次查询的硬上限（秒）。
    remember   : 成功时把机名清单记下来（进程内 + `cache_file`），供整次失败时逐台列出。
    cache_file : 机名清单缓存路径；`None` = 不落盘。
    """
    now = now or datetime.now(TZ)
    if cache_seconds and _CACHE["at"] and (now - _CACHE["at"]).total_seconds() < cache_seconds:
        return _CACHE["report"]

    started = time.monotonic()
    budget = float(deadline_s or 0) or 20.0
    effective_timeout = max(0.5, min(float(timeout or 0) or budget, budget))

    if transport is not None:
        token, credential_ref, credential_why = ("<injected>", "injected:transport", None)
    else:
        token, credential_ref, credential_why = resolve_credential(env_var=token_env, token_file=token_file)

    if credential_why:
        fetched = {"ok": False, "why": credential_why, "instances": []}
    else:
        finished, value = _call_with_deadline(
            lambda: fetch_instances(token, api_url=api_url, timeout=effective_timeout, transport=transport),
            budget)
        if not finished:
            fetched = {"ok": False,
                       "why": f"整次查询超过 {budget:g} 秒上限，已放弃（下一次 tick 再试）",
                       "instances": []}
        elif not isinstance(value, dict):
            fetched = {"ok": False, "why": "探测没有返回结果（内部错误）", "instances": []}
        else:
            fetched = value

    instances = fetched.get("instances") or []
    ok = bool(fetched.get("ok"))
    query_why = None if ok else (fetched.get("why") or "查询失败")
    by_alias: dict = {}
    for inst in instances:
        row = classify(inst, in_list=True)
        by_alias.setdefault(row["machine"], row)

    if machines:
        wanted = [str(item).strip() for item in machines if str(item).strip()]
        rows = [by_alias.pop(name, None) or _missing_row(name, query_why) for name in wanted]
        rows += [by_alias[name] for name in sorted(by_alias)]        # 没点名的也带上，放在后面
    else:
        rows = [by_alias[name] for name in sorted(by_alias)]

    # ⭐ 整次查询失败时，仍然把**上次查到的机名**逐台列出来（状态未知），
    #    ⛔ 不返回空表 —— 空表会让读页面的人以为「没有机器」。
    fallback_names: "list[str]" = []
    if not ok:
        fallback_names = list(_LAST_GOOD) or read_name_cache(cache_file)
        if not rows and fallback_names:
            rows = [_missing_row(name, query_why) for name in fallback_names]

    if ok and rows:
        names = [row["machine"] for row in rows if row.get("in_list")]
        if names:
            _LAST_GOOD[:] = names
            if remember:
                write_name_cache(cache_file, names, now.isoformat(timespec="seconds"))

    report = {
        "ok": ok,
        "read_only": True,
        "checked_at": now.isoformat(timespec="seconds"),
        "why_not": fetched.get("why"),
        "source": {
            "api": api_url,
            "credential_ref": credential_ref,
            "credential_present": bool(token),
            "timeout_s": effective_timeout,
            "deadline_s": budget,
            "elapsed_ms": int(round((time.monotonic() - started) * 1000)),
            "machine_names_from": ("list" if ok else
                                   ("memory" if _LAST_GOOD else
                                    ("cache" if fallback_names else "none"))),
        },
        "machines": rows,
        "summary": summarize(rows, ok),
        "criteria": CRITERIA,
    }
    if cache_seconds:
        _CACHE.update(at=now, report=report)
    return report


def summarize(rows: "list[dict]", ok: bool) -> dict:
    counts = {"total": len(rows), "running": 0, "already_up": 0,
              "likely_ok": 0, "likely_blocked": 0, "unknown": 0}
    for row in rows:
        if row["status_raw"] in ("running", "starting"):
            counts["running"] += 1
        if row["boot_availability"] == "已在用":
            counts["already_up"] += 1
        elif row["boot_availability"] == "可能可开":
            counts["likely_ok"] += 1
        elif row["boot_availability"] == "可能开不了":
            counts["likely_blocked"] += 1
        else:
            counts["unknown"] += 1
    counts["query_ok"] = bool(ok)
    return counts


# ── 渲染 ──────────────────────────────────────────────────
def _hhmm(iso: "str | None") -> str:
    """把 ISO 时间压成 HH:MM（同一时区的直接切；切不动就原样）。"""
    if not iso:
        return "—"
    match = re.search(r"T(\d{2}:\d{2})", str(iso))
    return match.group(1) if match else str(iso)


def _mmddhhmm(iso: "str | None") -> str:
    """把 ISO 时间压成「月-日 时:分」—— 跨天时只看 HH:MM 会读错。"""
    if not iso:
        return "—"
    match = re.search(r"(?:\d{4}-)?(\d{2})-(\d{2})T(\d{2}:\d{2})", str(iso))
    return f"{match.group(1)}-{match.group(2)} {match.group(3)}" if match else str(iso)


def _fmt_at(iso: "str | None") -> str:
    """标题用的可读时间；拿不到就原样。"""
    if not iso:
        return "时间不明"
    try:
        return datetime.fromisoformat(str(iso)).strftime("%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return str(iso)


def render_markdown(report: dict, *, title: str = "机器可用性", note: bool = True,
                    table: bool = True, heading: str = "###") -> str:
    """把报告渲染成 markdown。

    `heading` 决定标题形态：默认 `###`；传 `""` 就只出**加粗**标题，连 H3 都不占
    （给「把这段插进别人页面的次要位置」用，⛔ 不要抢占页面的一级/二级标题）。
    `note=False` 去掉判据脚注，`table=False` 只出第一段。
    """
    at = report.get("checked_at") or ""
    rows = report.get("machines") or []
    count = len(rows)
    stats = report.get("summary") or {}
    at_text = _fmt_at(at)

    if not report.get("ok"):
        head = (f"**这次没查成**（{_hhmm(at)}）。原因：{report.get('why_not') or '未知'}。"
                f"下面每台机器的状态都标成「说不准」，等下一次查。")
    elif count == 0:
        head = f"**没查到机器**（{_hhmm(at)}）。本账号的机器列表是空的。"
    elif stats.get("running", 0) == 0:
        head = (f"**{count} 台机器里现在 0 台在运行**（{_hhmm(at)} 查的）。"
                f"其中 {stats.get('likely_ok', 0)} 台所在服务器还有空闲显卡、看起来开得起来，"
                f"{stats.get('likely_blocked', 0)} 台所在服务器当前没有空闲显卡，"
                f"{stats.get('unknown', 0)} 台判断不了。关机不等于不能用，这一栏说的是「现在开起来难不难」。")
    else:
        head = (f"**{count} 台机器里现在有 {stats.get('running', 0)} 台在运行**（{_hhmm(at)} 查的）；"
                f"其余 {count - stats.get('running', 0)} 台没在运行，其中 {stats.get('likely_ok', 0)} 台看起来开得起来、"
                f"{stats.get('likely_blocked', 0)} 台所在服务器当前没有空闲显卡、"
                f"{stats.get('unknown', 0)} 台判断不了。")

    head_line = f"{heading} {title}（{at_text}）" if heading else f"**{title}（{at_text}）**"
    out = [head_line, "", head, ""]
    if not table:
        return "\n".join(out).rstrip() + "\n"

    out.append("| 机器 | 运行状态 | 空闲显卡 | 开机可用性 | 为什么这么判 / 无法判断的原因 | 状态时间 | 检查时间 |")
    out.append("|---|---|---|---|---|---|---|")
    for row in rows:
        why = (row.get("boot_reason") or "—").replace("|", "\\|").replace("**", "")
        out.append("| {m} | {s} | {g} | {b} | {w} | {sa} | {c} |".format(
            m=row.get("machine") or "—",
            s=row.get("status") or "未知",
            g=row.get("gpu_headroom") or "未知",
            b=row.get("boot_availability") or "说不准",
            w=why,
            sa=_mmddhhmm(row.get("status_at")),
            c=_hhmm(at),
        ))
    out.append("")
    if note:
        out.extend([
            "> **这次查的是什么**：只发了一次机器列表查询，没有开机、没有关机，也没有启动显卡任务。"
            "所以每台机器的「开机可用性」只是看字段推出来的，不是试出来的。",
            "> **三条判据**：① 已关机不等于不能用，只说明现在没开；"
            "② 接口没给空闲显卡数时，余量与开机可用性都标「未知」；"
            "③ 空闲显卡数是按整台服务器算的（不是本机租的那几张），所以 0 张只说明现在缺卡。",
            "> **读取不等于开机**：这份报告不构成任何「已经开机」的事实。",
        ])
    return "\n".join(out).rstrip() + "\n"


def render_line(report: dict) -> str:
    """一行版（塞进状态页或看门狗的一行）。⛔ 失败只降级成「说不清」，不抛异常。"""
    try:
        stats = report.get("summary") or {}
        at = _hhmm(report.get("checked_at"))
        if not report.get("ok"):
            return f"机器可用性 说不清（{report.get('why_not') or '查询失败'}）"
        return (f"机器可用性 {stats.get('total', 0)} 台：在跑 {stats.get('already_up', 0)} · "
                f"看起来能开 {stats.get('likely_ok', 0)} · 现在缺卡 {stats.get('likely_blocked', 0)} · "
                f"说不准 {stats.get('unknown', 0)}（查于 {at}）")
    except Exception:  # noqa: BLE001 —— 渲染永远不许把调用方打挂
        return "机器可用性 说不清"


def to_json(report: dict, **kwargs) -> str:
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("indent", 2)
    return json.dumps(report, **kwargs)


# ── 离线自检（不联网、不读真凭据）────────────────────────
def _fake_ok(_url: str, _body: bytes, _headers: dict, _timeout: float) -> "tuple[int, bytes]":
    document = {"code": "Success", "data": {"list": [
        {"machine_alias": "m-01", "region_name": "region-a", "status": "running",
         "status_at": "2026-01-01T00:00:00+08:00", "start_mode": "gpu",
         "snapshot_gpu_alias_name": "GPU-X", "gpu_all_num": 8, "gpu_idle_num": 3,
         "payg_price": 3280, "root_fs_used_rate": 0.839, "uuid": "u-1",
         "root_password": "LEAK-CANARY-PASSWORD", "jupyter_token": "LEAK-CANARY-JUPYTER",
         "ssh_command": "ssh -p 42340 root@example.invalid", "phone": "000****0000"},
        {"machine_alias": "m-02", "region_name": "region-a", "status": "shutdown",
         "status_at": "2026-01-01T00:00:00+08:00", "start_mode": "gpu",
         "snapshot_gpu_alias_name": "GPU-X", "gpu_all_num": 8, "gpu_idle_num": 0,
         "payg_price": 3280, "root_fs_used_rate": 0.5939, "uuid": "u-2"},
        {"machine_alias": "m-03", "region_name": "region-a", "status": "shutdown",
         "start_mode": "gpu", "gpu_all_num": 8, "gpu_idle_num": 1},
        {"machine_alias": "m-04", "region_name": "region-b", "status": "shutdown",
         "start_mode": "non_gpu", "gpu_all_num": 9, "gpu_idle_num": 9},
        {"machine_alias": "m-05", "region_name": "region-b", "status": "shutdown",
         "start_mode": "non_gpu"},
        {"machine_alias": "m-06", "region_name": "region-a", "status": "stopping",
         "start_mode": "gpu", "gpu_all_num": 8, "gpu_idle_num": 2},
    ]}}
    return 200, json.dumps(document).encode("utf-8")


def _fake_auth_fail(_url: str, _body: bytes, _headers: dict, _timeout: float) -> "tuple[int, bytes]":
    return 200, json.dumps({"code": "AuthorizeFailed", "msg": "登录超时"}).encode("utf-8")


def _fake_timeout(_url: str, _body: bytes, _headers: dict, _timeout: float) -> "tuple[int, bytes]":
    raise TimeoutError("timed out")


def _fake_empty(_url: str, _body: bytes, _headers: dict, _timeout: float) -> "tuple[int, bytes]":
    return 200, json.dumps({"code": "Success", "data": {"list": []}}).encode("utf-8")


def selftest() -> int:
    """离线自检：注入假传输，⛔ 不联网、⛔ 不读真凭据、⛔ 不开机。"""
    fails: "list[str]" = []

    def chk(condition: bool, message: str) -> bool:
        if not condition:
            fails.append(message)
        return condition

    now = datetime(2026, 1, 1, 0, 30, tzinfo=TZ)
    report = collect(transport=_fake_ok, now=now, remember=False, cache_file=None)
    rows = {row["machine"]: row for row in report["machines"]}

    chk(report["ok"] is True, "①a 查询成功应判 ok")
    chk(len(report["machines"]) == 6, f"①b 应出 6 行，实际 {len(report['machines'])}")
    chk(rows["m-01"]["status"] == "运行中", "①c 运行中的机器状态")
    chk(rows["m-01"]["boot_availability"] == "已在用", "①d 运行中的机器应判「已在用」")
    chk(rows["m-01"]["certain"] is True, "①e 只有「已在用」算有把握")
    chk(rows["m-06"]["status"] == "关机中", "①f 过渡态状态")
    chk(rows["m-06"]["boot_availability"] == "说不准", "①g 过渡态应判「说不准」")

    chk(rows["m-02"]["status"] == "已关机", "②a 已关机状态")
    chk(rows["m-02"]["boot_availability"] == "可能开不了", "②b 缺卡应判「可能开不了」")
    chk(rows["m-03"]["boot_availability"] == "可能可开", "②c 有余卡应判「可能可开」")
    for row in report["machines"]:
        chk(row["boot_availability"] != "不可用", f"②d ⛔ 不许把关机写成不可用（{row['machine']}）")
    for row in report["machines"]:
        if row["boot_availability"] in ("可能可开", "可能开不了"):
            chk("试开机" in (row["boot_reason"] or ""), f"②e 推断类判词必须写明没试开机（{row['machine']}）")

    chk(rows["m-05"]["gpu_headroom_known"] is False, "③a 缺字段的机器 GPU 余量应未知")
    chk(rows["m-05"]["gpu_headroom"] == "未知", "③b 缺字段时余量文案应为「未知」")
    chk(rows["m-05"]["boot_availability"] == "说不准", "③c 缺字段时开机可用性应为「说不准」")
    chk(rows["m-04"]["gpu_headroom_known"] is True, "③d 有字段时余量应已知")
    chk(rows["m-04"]["boot_availability"] == "说不准", "③e 无卡档不拿闲卡数判可开")

    dumped = to_json(report)
    for canary in ("LEAK-CANARY-PASSWORD", "LEAK-CANARY-JUPYTER", "root_password",
                   "jupyter_token", "ssh_command", "root@", "<injected>"):
        chk(canary not in dumped, f"④ ⛔ 报告里出现了凭据类内容：{canary}")

    bad = collect(transport=_fake_auth_fail, machines=["m-01", "m-02"], now=now,
                  remember=False, cache_file=None)
    chk(bad["ok"] is False, "⑤a 凭据被拒应判 ok=False")
    chk(len(bad["machines"]) == 2, "⑤b 点名的机器仍要出行（标说不准）")
    chk(all(row["boot_availability"] == "说不准" for row in bad["machines"]), "⑤c 查询失败时全部说不准")
    chk("凭据" in (bad["why_not"] or ""), "⑤d 原因要点明凭据")
    chk("说不清" in render_line(bad), "⑤e 一行版失败应降级成说不清")

    timed_out = collect(transport=_fake_timeout, now=now, remember=False, cache_file=None)
    chk(timed_out["ok"] is False and "超时" in (timed_out["why_not"] or ""), "⑤f 超时应如实写超时")

    empty = collect(transport=_fake_empty, now=now, remember=False, cache_file=None)
    chk(empty["ok"] is True and empty["machines"] == [], "⑤g 空列表应出 0 行而不是报错")

    missing = collect(token_env="AUTORESEARCH_NO_SUCH_ENV_VAR", now=now, remember=False, cache_file=None)
    chk(missing["ok"] is False and "环境变量" in (missing["why_not"] or ""),
        "⑤h 凭据缺失应报凭据来源，实际 %s" % missing["why_not"])

    markdown = render_markdown(report)
    for column in ("| 机器 |", "运行状态", "空闲显卡", "开机可用性", "检查时间"):
        chk(column in markdown, f"⑥a markdown 缺列：{column}")
    chk("2026-01-01" in markdown and "00:30" in markdown, "⑥b markdown 要带检查时间")
    chk("没有开机" in markdown, "⑥c markdown 要声明没动机器")
    chk(render_markdown(report, table=False).count("|") == 0, "⑥d table=False 时不出表")
    bad_h2 = [line for line in render_markdown(report).splitlines()
              if line.startswith("## ") and not line.startswith("### ")]
    chk(not bad_h2, f"⑥e ⛔ 不许出 H2：{bad_h2[:1]}")
    bold = render_markdown(report, heading="")
    chk("#" not in bold.split("\n")[0], "⑥f heading=\"\" 时标题应只有加粗")
    chk(render_line(report).startswith("机器可用性"), "⑥g 一行版要以「机器可用性」开头")

    chk(report["read_only"] is True, "⑦a 报告要自述 read_only")
    for key in ("read_only", "shutdown_is_not_unavailable", "unknown_when_fields_missing",
                "never_probe_by_booting", "reading_is_not_booting"):
        chk(key in report["criteria"], f"⑦b 判据缺项：{key}")

    chk(rows["m-01"]["price_yuan_per_hour"] == 32.8, "⑧a 单价换算错")
    chk(rows["m-01"]["disk_used_percent"] == 83.9, "⑧b 系统盘占用换算错")

    named = collect(transport=_fake_ok, machines=["m-01", "m-99"], now=now,
                    remember=False, cache_file=None)
    rows2 = {row["machine"]: row for row in named["machines"]}
    chk("不在本次查询拿到的机器列表里" in (rows2["m-99"]["boot_reason"] or ""),
        "⑨a 列表里真没有的机器，原因要写清「不在列表里」")
    chk(rows2["m-01"]["boot_availability"] == "已在用", "⑨b 点名不应打乱在列机器的判读")
    lost = collect(transport=_fake_timeout, machines=["m-99"], now=now, remember=False, cache_file=None)
    chk("没拿到机器列表" in (lost["machines"][0]["boot_reason"] or ""),
        "⑨c 查询失败时原因要写成「没拿到机器列表」，⛔ 不许说成「这台机不在列表里」")

    def _fake_slow(_url: str, _body: bytes, _headers: dict, _timeout: float) -> "tuple[int, bytes]":
        time.sleep(1.5)
        return 200, json.dumps({"code": "Success", "data": {"list": []}}).encode("utf-8")

    started = time.monotonic()
    slow = collect(transport=_fake_slow, now=now, deadline_s=0.2, remember=False, cache_file=None)
    spent = time.monotonic() - started
    chk(slow["ok"] is False, "⑩a 超过硬上限应判失败")
    chk("上限" in (slow["why_not"] or ""), "⑩b 原因要写「超过上限」")
    chk(spent < 0.6, f"⑩c 硬截止没生效：实际等了 {spent:.2f}s（上限 0.2s）")

    _LAST_GOOD.clear()
    collect(transport=_fake_ok, now=now, remember=False, cache_file=None)
    lost2 = collect(transport=_fake_timeout, now=now, remember=False, cache_file=None)
    chk(len(lost2["machines"]) == 6, f"⑪a 整次失败仍应列出 6 台，实际 {len(lost2['machines'])}")
    chk(all(row["boot_availability"] == "说不准" for row in lost2["machines"]), "⑪b 全部应标说不准")
    chk("没查成" in render_markdown(lost2), "⑪c 页面第一段要直说没查成")

    if fails:
        import sys
        print(f"SELFTEST=FAIL 共 {len(fails)} 条：", file=sys.stderr)
        for item in fails:
            print(f"  - {item}", file=sys.stderr)
        return 3
    print("SELFTEST=PASS（离线假传输，未联网、未读真凭据、未开机）")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
