# GENERATED-BY: 完整功能迁移（持续推进 A1–A7 / 远端进度 H1–H9 / 入向超期 D2 的宿主侧联动）
# LAST-EDIT: 2026-11-09（回复按source+event_id匹配，无来源旧回执不作证明，时钟与事件时间须有限正值）
# PURPOSE: 把 hosting 事实接到通用进度机制上 —— goal 来自 DSH 真实会话事件重放，超期来自 Decisions 同 runtime 库的只读等价查询，
#          受管 watch 的 job_id 用 DSH 持久 tool_result 的 toolCallId/name/arguments 精确核验；不内置任何比赛/机器/结论。
# ORIGIN: 宿主源码 @deepseek-ai/dsh-goal（goal/change 事件与 fold 规则）、dsh-tool-goal（get_goal 输出形状）、
#          dsh-tool-pwsh（background jobId 与渲染文本）、dsh-session（事件/工具结果形状）；
#          仓库内 host_evidence.DSHHost（只读会话读取，不修改）、decisions.reply_status（超期语义，只读等价 SQL）。
# CUMULATIVE-DELTA: 2026-11-09 补source+event_id回复绑定、缺来源不认与有限正时间边界；
#          首版：① 只用 DSHHost.session()（生产默认 DSHHost(root=部署 dsh_home)，
#          复用已真实验证的 Node 多帧 decoder；CLI 不暴露 decoder / 不接受 caller 选 jsonl）；② goal 以会话事件 goal/change
#          重放为权威，projcache 只做派生诊断且不参与判定；③ activation 是进程内状态、持久未知 ⇒ coverage 保留 unknown，
#          只有部署可信模块 goal_coverage() 能给出 full/none 并带 why/source/as_of；④ job 核验要求登记命令**精确相等**
#          （CRLF 归一后）+ 命令/argv fingerprint + 唯一配对 + jobId 精确 token，不只子串；⑤ 超期摘要默认不含正文。
"""宿主侧联动：DSH goal / Decisions 超期 / 受管 job 登记（只读，不改宿主）。

运行时序
========

    deployment（固定配置，不是 caller 每次临时 JSON）
        └─ HostAdapter
             ├─ goal_replay()   ← DSHHost.session(session_id).raw_events 里的 goal/change 重放（权威）
             ├─ goal_state()    ← 上面的重放 + 可选 trusted_module.goal_coverage()（唯一能说 full/none 的地方）
             ├─ overdue()       ← runtime sqlite 的只读等价 SQL（文字事件 + 回复回执）
             ├─ verify_job()    ← DSH 持久 tool/call + tool/result 的精确命令 + jobId 核验
             └─ register_job()  ← append-only 台账（不伪造 verified）
        └─ progress_host_cli watch → progress.Progress(goal=adapter.goal_state, overdue=adapter.overdue)

边界（先写清楚，免得被读成授权）
================================

1. **只读宿主**：本模块不写 `$DSH_HOME`、不改 `host_evidence`、不建 Decisions schema、不发消息、不启动任何 server。
2. **证据不由 caller 挑**：生产一律 `DSHHost(root=部署的 dsh_home)` 自己去读宿主日志；CLI 没有
   `--jsonl` / `--decoder` / `--goal-json` 之类入口。`host_module` 只允许部署方在固定配置里提供
   **自己的可信 DSHHost 构造器**（测试用 fixture decoder 就是从这里注入的），不是 CLI 参数。
3. **goal 权威来源是会话事件重放**：`goal/change` 逐条解码并校验版本 / 字段集 / 相位迁移（与
   `@deepseek-ai/dsh-goal` 的 `fold.ts` 同一套规则）。`session_projcache` 是派生缓存，只做诊断对照，
   **不参与判定**；不拿 mtime 冒充进度（H3）。
4. **activation 持久未知**：宿主源码明示 `activation` 是进程内的、从不落盘。所以
   `phase=active` 时只读持久态**不能**断言 fully covered ⇒ coverage 保留 `unknown`，由宿主可信模块
   `goal_coverage()` 显式覆盖（必须给 why/source/as_of）；给不出就保持 unknown ⇒ progress 侧 `undecidable`，
   ⛔ 不编造 fully covered、也不 0 假绿。
5. **超期只读等价 SQL**：口径与 `decisions.DecisionService.reply_status()` 相同（文字事件非卡片点击；
   已回复 = `delivery(kind='reply',status='delivered')` + 回执里 `event_reply_to` 命中；超期 = 没有
   收件人 / 没有事件时间 / 年龄 > grace）。库缺失、缺表、行不可解析 ⇒ `checked=False` ⇒ progress 侧
   `overdue_unknown` ⇒ `undecidable`。⚠️ 两处**刻意更严**（同样不假绿）：① 缺 recipient / 缺 event_time
   时判**不可判**而不是「按超期算」；② 任一 `inbox_events` 行读不动就整轮不可判。默认**不带正文摘要**，
   只给 event_id / age / source（不复制私人内容）。
6. **job_id 必须可核**：`register` 把「watch 的精确命令 + 命令指纹 + argv 指纹」记进 append-only 台账，
   再用 DSH 持久 `tool/call`（name + arguments.command）与配对 `tool/result`（toolCallId + 文本里的
   `started background job <id>` / `moved to background job <id>`）核验：命令必须**精确相等**（只做 CRLF
   归一），jobId 必须是精确 token，配对必须唯一。核不到就记 `verified: false` + why，⛔ 不当成核过。
7. **受管运行不在这里起**：`watch` 由宿主的 `functions.pwsh` 后台作业启动；本工具只产出确切命令、
   后台调用片段与登记命令，并写一份 pending 标记给 `register` 用。它不会自建 server、不会自续。

deployment 配置（固定文件；键是闭集，不认识的键直接拒）
======================================================

```json
{
  "version": 1,
  "name": "deployment-name",
  "dsh_home": "C:/Users/<user>/.dsh",
  "session_id": "session-…",
  "workspace": "C:/path/to/research-workspace",
  "progress_spec": "progress/spec.json",
  "decisions_db": "C:/path/to/research-workspace/.autoresearch/runtime.sqlite3",
  "state_root": "C:/path/to/research-workspace/.autoresearch/host",
  "owner": "main",
  "shell_tool_name": "pwsh",
  "overdue_grace_sec": 304,
  "trusted_module": "deploy/trusted_coverage.py",
  "host_module": "deploy/trusted_host.py",
  "node": "C:/path/to/node24/node.exe",
  "note": "可选说明"
}
```

相对路径按 **deployment 文件所在目录** 解析。`node` 是给 **既有 decoder** 用的 Node 24 路径（不是证据通道）；
不写就由 DSHHost 自己找。`trusted_module` 可定义
`goal_coverage(view) -> {coverage: 'full'|'none', why, source, as_of}`；`host_module` 可定义
`build_host(dsh_home, workspace) -> DSHHost`（部署自己的可信构造器；测试用它注入 fixture decoder）。
两者都不是 CLI 参数，也不接受 caller 传证据。

对外接口
========

    load_deployment(path) -> dict
    build_adapter(deployment_or_path, *, host=None, clock=time.time) -> HostAdapter
    HostAdapter.goal_replay() / .goal_state() / .overdue()
    HostAdapter.projection_diagnostic()
    HostAdapter.verify_job(job_id, command, argv=None) -> dict
    HostAdapter.register_job(job_id, command, argv=None, owner=None) -> dict
    HostAdapter.jobs() / .job(job_id)
    HostAdapter.plan(poll=None, max_rounds=None) -> dict
    HostAdapter.status() / .doctor()
    HostAdapter.pending_job() / .write_pending_job(...) / .finish_pending_job(...)
    read_overdue(decisions_db, *, now=None, grace=..., exp_id=None) -> dict
    replay_goal(raw_events) -> dict
    REDACTED / TEXT_REPLY_GRACE_SECONDS / DEPLOYMENT_VERSION

对应行为预期：A1/A2（有效推进来源与恢复动作）· A3/A6（受管作业一次唤醒 + 显式重挂）· A4（静默窗口用真实
动静，不用 mtime）· A5/D2（用户超期优先、拒绝假绿）· A7（退出码不可信，判词与预期码落盘）·
H1（goal 主导）· H2（外部推进器只补 goal 不覆盖的时段）· H3（新进展看真实事件/产物）·
H4（唤醒载荷说明变了什么）· H5（声明式采集）· H6（采集失败 fail-closed）· H9（直令轮由配置识别）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from kernel.autoresearch.host_evidence import DSHHost, EvidenceError, session_id as host_session_id, visible_text

DEPLOYMENT_VERSION = 1
DEPLOYMENT_KEYS = frozenset({
    "version", "name", "dsh_home", "session_id", "workspace", "progress_spec", "decisions_db",
    "state_root", "owner", "shell_tool_name", "overdue_grace_sec", "trusted_module",
    "host_module", "node", "note",
})
GOAL_PHASES = ("active", "paused", "blocked", "complete")
GOAL_OPERATIONS = ("create", "edit", "pause", "resume", "complete", "block")
SHELL_TOOL_NAMES = frozenset({"pwsh", "bash", "functions.pwsh", "functions.bash",
                              "shell", "functions.shell"})
#: 与 `kernel/autoresearch/decisions.py` 的 `CARD_CLICK_INTENTS` 同义（只读镜像，不 import 那份会写库的模块）。
CARD_CLICK_INTENTS = frozenset(("approve", "approval", "reject", "deny"))
#: 与 `decisions.TEXT_REPLY_GRACE_SECONDS` 同义（300s 宽限 + 4s 取整余量）。
TEXT_REPLY_GRACE_SECONDS = 304.0
JOB_ID_RE = re.compile(r"\A[a-z][a-z0-9_]*-[0-9]+\Z")
_BACKGROUND_PHRASE_RE = re.compile(
    r"(?:(?:started\s+background|moved\s+to\s+background)\s+job\s+)([A-Za-z][A-Za-z0-9_]*-[0-9]+)")
SAFE_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class HostProgressError(ValueError):
    """宿主侧可判的拒绝（配置、证据形状、台账损坏）。"""


class DeploymentError(HostProgressError):
    """deployment 配置被拒。"""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_json(value) -> str:
    return _sha_text(_canonical(value))


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise HostProgressError(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise HostProgressError(f"不是合法 JSON：{path}（{exc}）") from exc


def _load_module(path: Path, label: str):
    if not path.is_file():
        raise HostProgressError(f"{label} 不存在：{path}")
    spec = importlib.util.spec_from_file_location(
        f"progress_host_{label}_{abs(hash(str(path))) & 0xFFFF}", path)
    if spec is None or spec.loader is None:
        raise HostProgressError(f"{label} 载入失败：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm_command(command: str) -> str:
    """命令比对口径：只把 CRLF 归一成 LF；其余一个字节都不放宽（不接受近似或子串）。"""
    return command.replace("\r\n", "\n")


def _job_id(value) -> str:
    if not isinstance(value, str) or not JOB_ID_RE.match(value.strip()):
        raise HostProgressError(f"job_id 不合法（宿主注册表形状是 <kind>-N）：{value!r}")
    return value.strip()


def render_command(argv) -> str:
    """把 argv 渲染成宿主 shell 的命令串（与 plan 用同一口径；用于 argv 与命令互相绑定）。"""
    if os.name == "nt":
        return subprocess.list2cmdline([str(item) for item in argv])
    return shlex.join([str(item) for item in argv])


def _message(row) -> dict:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    message = data.get("message", data)
    return message if isinstance(message, dict) else {}


# ---------------------------------------------------------------------------
# deployment：固定配置（不是 caller 每次临时 JSON）
# ---------------------------------------------------------------------------


def load_deployment(path) -> dict:
    """读并校验固定 deployment 配置；相对路径按配置文件所在目录解析。"""
    target = Path(path).resolve()
    raw = _load_json(target)
    if not isinstance(raw, dict):
        raise DeploymentError("deployment 顶层必须是对象")
    extra = set(raw) - DEPLOYMENT_KEYS
    if extra:
        raise DeploymentError(f"deployment 有不认识的键：{sorted(extra)}（拒绝写成看起来生效的配置）")
    if int(raw.get("version", 0)) != DEPLOYMENT_VERSION:
        raise DeploymentError(f"deployment.version 必须是 {DEPLOYMENT_VERSION}")
    base = target.parent
    out = {"path": str(target)}
    name = raw.get("name")
    if not _text(name):
        raise DeploymentError("deployment.name 必须是非空字符串")
    out["name"] = name.strip()

    def path_of(key, required=False):
        value = raw.get(key)
        if value is None or value == "":
            if required:
                raise DeploymentError(f"deployment.{key} 必填")
            return None
        candidate = Path(os.path.expanduser(str(value)))
        return str(candidate if candidate.is_absolute() else (base / candidate))

    out["dsh_home"] = path_of("dsh_home", required=True)
    out["workspace"] = path_of("workspace", required=True)
    out["decisions_db"] = path_of("decisions_db") or str(Path(out["workspace"]) / ".autoresearch" / "runtime.sqlite3")
    out["state_root"] = path_of("state_root") or str(Path(out["workspace"]) / ".autoresearch" / "host")
    out["progress_spec"] = path_of("progress_spec")
    out["trusted_module"] = path_of("trusted_module")
    out["host_module"] = path_of("host_module")
    out["node"] = path_of("node")

    sid = raw.get("session_id")
    try:
        out["session_id"] = host_session_id(sid)
    except EvidenceError as exc:
        raise DeploymentError(f"deployment.session_id 不合法：{exc}") from exc

    owner = raw.get("owner", "main")
    if not isinstance(owner, str) or not SAFE_NAME_RE.match(owner.strip()):
        raise DeploymentError(f"deployment.owner 不合法：{owner!r}")
    out["owner"] = owner.strip()

    tool = raw.get("shell_tool_name", "pwsh")
    if not _text(tool):
        raise DeploymentError("deployment.shell_tool_name 必须是非空字符串")
    out["shell_tool_name"] = tool.strip()

    grace = raw.get("overdue_grace_sec", TEXT_REPLY_GRACE_SECONDS)
    if not _number(grace) or grace <= 0:
        raise DeploymentError("deployment.overdue_grace_sec 必须是正数")
    out["overdue_grace_sec"] = float(grace)
    out["note"] = raw.get("note")
    return out


def build_host(deployment: dict) -> DSHHost:
    """生产：DSHHost(root=dsh_home)（用它已经真实验证的 Node 多帧 decoder）。

    `host_module` 只允许**部署配置**提供自己的可信构造器（测试注入 fixture decoder 走这里）；
    CLI 不暴露任何证据/decoder 参数。
    """
    workspace = deployment.get("workspace")
    module_path = deployment.get("host_module")
    if module_path:
        module = _load_module(Path(module_path), "host_module")
        factory = getattr(module, "build_host", None)
        if not callable(factory):
            raise DeploymentError("host_module 必须定义 build_host(dsh_home, workspace)")
        host = factory(deployment["dsh_home"], workspace)
        if host is None or not callable(getattr(host, "session", None)):
            raise DeploymentError("host_module.build_host 必须返回 DSHHost 形状（有 session 方法）")
        return host
    return DSHHost(root=deployment["dsh_home"], workspace=workspace,
                   node=deployment.get("node"))


def load_trusted(path):
    """载入部署可信模块。返回 `{module, path, source}`；文件不存在返回 None。"""
    if not path:
        return None
    module = _load_module(Path(path), "trusted_module")
    return {"module": module, "path": str(path), "source": Path(path).name}


# ---------------------------------------------------------------------------
# goal：DSH 会话事件重放（权威），projcache 仅诊断
# ---------------------------------------------------------------------------


def _goal_snapshot(value) -> dict:
    if not isinstance(value, dict):
        raise HostProgressError("goal/change.goal 必须是对象")
    phase = value.get("phase")
    if phase not in GOAL_PHASES:
        raise HostProgressError(f"goal/change.goal.phase 非法：{phase!r}")
    expected = ({"blockedReason", "id", "maxGoalRounds", "objective", "phase", "revision"}
                if phase == "blocked" else
                {"id", "maxGoalRounds", "objective", "phase", "revision"})
    if set(value) != expected:
        raise HostProgressError(f"goal/change.goal 字段集不符（phase={phase}）：{sorted(value)}")
    if not _text(value.get("id")):
        raise HostProgressError("goal/change.goal.id 必须是非空字符串")
    if type(value.get("revision")) is not int or value["revision"] < 1:
        raise HostProgressError("goal/change.goal.revision 必须是正整数")
    if not _text(value.get("objective")):
        raise HostProgressError("goal/change.goal.objective 必须是非空字符串")
    if type(value.get("maxGoalRounds")) is not int or value["maxGoalRounds"] < 1:
        raise HostProgressError("goal/change.goal.maxGoalRounds 必须是正整数")
    reason = None
    if phase == "blocked":
        raw_reason = value.get("blockedReason")
        if not isinstance(raw_reason, dict) or set(raw_reason) != {"code", "message"}:
            raise HostProgressError("goal/change.goal.blockedReason 必须恰好有 code 与 message")
        if not _text(raw_reason.get("code")) or not _text(raw_reason.get("message")):
            raise HostProgressError("goal/change.goal.blockedReason 缺 code/message")
        reason = {"code": raw_reason["code"], "message": raw_reason["message"]}
    return {"id": value["id"], "revision": value["revision"], "objective": value["objective"],
            "phase": phase, "maxGoalRounds": value["maxGoalRounds"], "blockedReason": reason}


def _decode_goal_change(value) -> dict:
    if not isinstance(value, dict):
        raise HostProgressError("goal 事件载荷必须是对象")
    if value.get("kind") != "goal/change":
        raise HostProgressError("goal 事件载荷 kind 必须是 'goal/change'")
    if value.get("version") != 1:
        raise HostProgressError(f"不支持的 goal/change 版本：{value.get('version')!r}")
    operation = value.get("operation")
    if operation == "clear":
        if set(value) != {"cleared", "clearedAt", "kind", "operation", "version"}:
            raise HostProgressError("goal clear 墓碑字段集不符")
        cleared = value.get("cleared")
        if not isinstance(cleared, dict) or set(cleared) != {"id", "revision"}:
            raise HostProgressError("goal clear 墓碑必须是 {id, revision}")
        if not _text(cleared.get("id")) or type(cleared.get("revision")) is not int \
                or cleared["revision"] < 1:
            raise HostProgressError("goal clear 墓碑 id/revision 不合法")
        if type(value.get("clearedAt")) not in (int, float) or value["clearedAt"] < 0:
            raise HostProgressError("goal clear.clearedAt 必须是非负数")
        return {"operation": "clear", "cleared": {"id": cleared["id"],
                                                  "revision": cleared["revision"]},
                "clearedAt": value["clearedAt"]}
    if operation not in GOAL_OPERATIONS:
        raise HostProgressError(f"goal/change.operation 非法：{operation!r}")
    if set(value) != {"createdAt", "goal", "kind", "operation", "roundsStarted", "updatedAt",
                      "version"}:
        raise HostProgressError("goal 快照变化字段集不符")
    for key in ("createdAt", "updatedAt"):
        if type(value.get(key)) not in (int, float) or value[key] < 0:
            raise HostProgressError(f"goal/change.{key} 必须是非负数")
    if value["updatedAt"] < value["createdAt"]:
        raise HostProgressError("goal/change.updatedAt 不能早于 createdAt")
    if type(value.get("roundsStarted")) is not int or value["roundsStarted"] < 0:
        raise HostProgressError("goal/change.roundsStarted 必须是非负整数")
    return {"operation": operation, "goal": _goal_snapshot(value.get("goal")),
            "roundsStarted": value["roundsStarted"], "createdAt": value["createdAt"],
            "updatedAt": value["updatedAt"]}


def _empty_replay() -> dict:
    return {"goal": None, "roundsStarted": 0, "createdAt": None, "updatedAt": None,
            "lastRef": None, "seenGoalIds": [], "last_seq": None, "changes": 0}


def _apply_goal_change(state: dict, change: dict) -> None:
    current = state["goal"]
    if change["operation"] == "clear":
        if current is None:
            raise HostProgressError("goal clear 需要已有当前 goal")
        cleared = change["cleared"]
        if cleared["id"] != current["id"] or cleared["revision"] != current["revision"] + 1:
            raise HostProgressError("goal clear 必须推进当前 goal 的 revision +1")
        if change["clearedAt"] < state["updatedAt"]:
            raise HostProgressError("goal clear 时间早于当前 goal 的 updatedAt")
        state.update({"goal": None, "roundsStarted": 0, "createdAt": None, "updatedAt": None})
        state["lastRef"] = cleared
        return
    goal = change["goal"]
    if change["operation"] == "create":
        if goal["revision"] != 1 or goal["phase"] != "active" or change["roundsStarted"] != 0:
            raise HostProgressError("goal create 必须是 revision=1、active、零轮的初始 goal")
        if current is not None and current["phase"] != "complete":
            raise HostProgressError("已有未 complete 的 goal 时不能 create")
        if goal["id"] in state["seenGoalIds"]:
            raise HostProgressError("goal id 重复使用")
        state["seenGoalIds"].append(goal["id"])
    else:
        if current is None:
            raise HostProgressError(f"goal {change['operation']} 需要已有当前 goal")
        if goal["id"] != current["id"] or goal["revision"] != current["revision"] + 1:
            raise HostProgressError(f"goal {change['operation']} 必须推进当前 goal 的 revision +1")
        if change["createdAt"] != state["createdAt"]:
            raise HostProgressError(f"goal {change['operation']} 不能改 createdAt")
        if change["updatedAt"] < state["updatedAt"]:
            raise HostProgressError(f"goal {change['operation']} 的 updatedAt 倒退")
        if change["roundsStarted"] != state["roundsStarted"]:
            raise HostProgressError(f"goal {change['operation']} 不能改 roundsStarted")
        if goal["objective"] != current["objective"] or goal["maxGoalRounds"] != current["maxGoalRounds"]:
            if change["operation"] != "edit":
                raise HostProgressError(f"goal {change['operation']} 不能改 objective/maxGoalRounds")
        if change["operation"] == "edit":
            if goal["phase"] != current["phase"] or goal["blockedReason"] != current["blockedReason"]:
                raise HostProgressError("goal edit 不能改 phase 或 blockedReason")
        elif change["operation"] == "pause":
            if current["phase"] != "active" or goal["phase"] != "paused":
                raise HostProgressError("goal pause 只能 active→paused")
        elif change["operation"] == "resume":
            if current["phase"] not in ("active", "paused", "blocked") or goal["phase"] != "active":
                raise HostProgressError("goal resume 只能 active/paused/blocked→active")
            if state["roundsStarted"] >= goal["maxGoalRounds"]:
                raise HostProgressError("goal resume 时轮次预算已耗尽")
        elif change["operation"] == "complete":
            if current["phase"] == "complete" or goal["phase"] != "complete":
                raise HostProgressError("goal complete 只能非 complete→complete")
        elif change["operation"] == "block":
            if current["phase"] != "active" or goal["phase"] != "blocked":
                raise HostProgressError("goal block 只能 active→blocked")
    state.update({"goal": goal, "roundsStarted": change["roundsStarted"],
                  "createdAt": change["createdAt"], "updatedAt": change["updatedAt"]})
    state["lastRef"] = {"id": goal["id"], "revision": goal["revision"]}


def replay_goal(raw_events) -> dict:
    """从 DSH 原始会话事件重放当前 goal（权威；与 dsh-goal 的 fold 规则一致）。

    返回 `{available, why, present, phase, goal{...}, rounds_started, created_at, updated_at,
    last_seq, changes}`。任何形状/迁移问题 ⇒ `available=False`（fail closed，不当成「没有 goal」）。
    """
    if not isinstance(raw_events, list):
        return {"available": False, "why": "会话事件不是列表", "present": False}
    state = _empty_replay()
    try:
        for row in raw_events:
            if not isinstance(row, dict):
                raise HostProgressError("会话事件里出现非对象项")
            if row.get("type") == "goal/change":
                change = _decode_goal_change(row.get("data"))
                _apply_goal_change(state, change)
                state["changes"] += 1
                state["last_seq"] = row.get("seq")
            elif row.get("type") == "user/message":
                data = row.get("data") if isinstance(row.get("data"), dict) else {}
                source = data.get("source") if isinstance(data.get("source"), dict) else {}
                if source.get("kind") != "goal":
                    continue
                current = state["goal"]
                if current is None or current["phase"] != "active":
                    raise HostProgressError("goal 轮次消息出现时没有 active goal")
                if source.get("goalId") != current["id"] \
                        or source.get("revision") != current["revision"]:
                    raise HostProgressError("goal 轮次消息与当前 goal 身份不符")
                round_number = source.get("round")
                if type(round_number) is not int or round_number != state["roundsStarted"] + 1 \
                        or round_number > current["maxGoalRounds"]:
                    raise HostProgressError("goal 轮次消息不是下一个被允许的轮次")
                state["roundsStarted"] = round_number
    except HostProgressError as exc:
        return {"available": False, "why": f"goal 重放失败：{exc}", "present": False}
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都按不可判处理
        return {"available": False, "why": f"goal 重放异常：{type(exc).__name__}: {exc}",
                "present": False}
    goal = state["goal"]
    objective = goal["objective"] if goal else None
    blocked = None
    if goal and goal["blockedReason"]:
        blocked = {
            "code": goal["blockedReason"]["code"],
            # ⛔ 不复制 blockedReason.message 原文：只留指纹与长度，供对照与定位
            "message_sha256": _sha_text(goal["blockedReason"]["message"]),
            "message_chars": len(goal["blockedReason"]["message"]),
        }
    return {
        "available": True,
        "why": None,
        "present": goal is not None,
        "phase": goal["phase"] if goal else None,
        "goal": None if goal is None else {
            "id": goal["id"],
            "revision": goal["revision"],
            "phase": goal["phase"],
            "max_goal_rounds": goal["maxGoalRounds"],
            "blocked_reason": blocked,
            "objective_sha256": _sha_text(objective),
            "objective_chars": len(objective),
        },
        "rounds_started": state["roundsStarted"],
        "created_at": state["createdAt"],
        "updated_at": state["updatedAt"],
        "last_seq": state["last_seq"],
        "changes": state["changes"],
    }


def _redacted_view(replay: dict) -> dict:
    """给可信模块的 goal 视图：**不含 objective 原文**，只给可判字段与指纹。"""
    goal = replay.get("goal") or {}
    return {
        "present": bool(replay.get("present")),
        "phase": replay.get("phase"),
        "rounds_started": replay.get("rounds_started"),
        "revision": goal.get("revision"),
        "max_goal_rounds": goal.get("max_goal_rounds"),
        "blocked_reason_code": ((goal.get("blocked_reason") or {}) or {}).get("code"),
        "objective_sha256": goal.get("objective_sha256"),
        "last_seq": replay.get("last_seq"),
    }


# ---------------------------------------------------------------------------
# overdue：Decisions 同 runtime 库的只读等价查询
# ---------------------------------------------------------------------------


def _is_card_click(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("card_action"), dict):
        return True
    if str(payload.get("kind") or "") == "card_action":
        return True
    return bool(payload.get("decision_id")) and \
        str(payload.get("intent") or "").strip().lower() in CARD_CLICK_INTENTS


def _readonly_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def read_overdue(decisions_db, *, now=None, grace=TEXT_REPLY_GRACE_SECONDS, exp_id=None) -> dict:
    """只读：文字事件回复窗口。**不建表、不迁移、不写**；读不动一律 `checked=False`（fail closed）。

    返回 `{checked, why, max_age_sec, items, overdue, count, text_events, replied, pending,
    phase, blocked}`。`items` 只含 event_id / age_sec / source / exp_id / status / why，
    ⛔ 默认不带正文摘要（不复制私人内容）。
    """
    clock = float(now if now is not None else time.time())
    grace = float(grace)
    empty = {"checked": False, "why": None, "error": None, "max_age_sec": grace, "items": [],
             "overdue": [], "count": 0, "text_events": 0, "replied": 0, "pending": 0,
             "phase": "unknown", "blocked": False}

    def fail(why):
        # progress.Progress 只认 `error` 判「不可判」；缺库/缺表/读不动一律走这一支，⛔ 不 0 假绿
        return dict(empty, why=why, error=why)

    if not math.isfinite(clock) or clock <= 0 or not math.isfinite(grace) or grace <= 0:
        return fail('回复窗口 now/grace 必须是有限正数')
    path = Path(str(decisions_db))
    if not path.is_file():
        return fail(f"runtime 库不存在：{path}（未建 schema，不猜）")
    try:
        conn = _readonly_connect(path)
    except sqlite3.Error as exc:
        return fail(f"runtime 库只读打开失败：{exc}")
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = {"inbox_events", "delivery", "delivery_receipts"} - tables
        if missing:
            return fail(f"runtime 库缺表 {sorted(missing)}（只读，不建 schema）")
        events = [dict(row) for row in conn.execute(
            "SELECT source,event_id,exp_id,payload_json,event_time,received_at,status"
            " FROM inbox_events WHERE (? IS NULL OR exp_id=?)"
            " ORDER BY event_time IS NULL, event_time, event_id, source", (exp_id, exp_id))]
        replies = {}
        for row in conn.execute(
                "SELECT d.id,d.exp_id,d.payload_json FROM delivery d"
                " JOIN delivery_receipts r ON r.delivery_id=d.id"
                " WHERE d.kind='reply' AND d.status='delivered'"):
            payload = json.loads(row["payload_json"]) if _text(row["payload_json"]) else {}
            binding = payload.get("event_reply_to") if isinstance(payload, dict) else None
            binding = binding if isinstance(binding, dict) else {}
            event_key = str(binding.get("event_id") or "")
            if not event_key:
                continue
            source_key = binding.get("source")
            if _text(source_key):
                replies.setdefault((source_key, event_key), []).append(dict(row))
    except (sqlite3.Error, ValueError, UnicodeError) as exc:
        return fail(f"runtime 库读取失败（不可判）：{type(exc).__name__}: {exc}")
    finally:
        conn.close()

    out = dict(empty, checked=True)
    for row in events:
        try:
            payload = json.loads(row["payload_json"])
        except (ValueError, TypeError) as exc:
            return fail(f"inbox_events 行不可解析（不可判）：{exc}")
        if not isinstance(payload, dict):
            return fail("inbox_events 行载荷不是对象（不可判）")
        if _is_card_click(payload):
            continue
        out["text_events"] += 1
        event_key = str(row["event_id"])
        if replies.get((row['source'], event_key)):
            out["replied"] += 1
            continue
        item = {"id": event_key, "source": row["source"], "why": None}
        recipient = payload.get("recipient")
        if not _text(recipient):
            return fail(f"事件 {event_key} 没有 recipient ⇒ 无法判定能否回复（不可判）")
        event_time = row["event_time"]
        if not _number(event_time) or not math.isfinite(event_time) or event_time <= 0:
            return fail(f"事件 {event_key} 没有可判定的事件时间（不可判）")
        age = max(0.0, (clock - float(event_time)) / 1000.0 if float(event_time) > 1e11
                  else clock - float(event_time))
        item["age_sec"] = round(age, 1)
        if age > grace:
            item["why"] = f"已经 {int(age)}s > grace {int(grace)}s"
            out["overdue"].append(item)
        else:
            out["pending"] += 1
        out["items"].append(item)
    out["count"] = len(out["overdue"])
    out["blocked"] = bool(out["overdue"])
    out["phase"] = "unanswered_inbound" if out["blocked"] else "ok"
    return out


# ---------------------------------------------------------------------------
# HostAdapter
# ---------------------------------------------------------------------------


class HostAdapter:
    """一份固定 deployment + 一个只读 DSHHost + 可选可信覆盖模块。"""

    def __init__(self, deployment, *, host=None, clock=time.time, trusted=None):
        self.deployment = load_deployment(deployment) if not isinstance(deployment, dict) else deployment
        if "path" not in self.deployment:
            raise DeploymentError("deployment 必须是 load_deployment() 的结果（缺 path）")
        self.deployment_path = Path(self.deployment["path"])
        self.clock = clock
        self.host = host if host is not None else build_host(self.deployment)
        self.trusted = trusted if trusted is not None else load_trusted(
            self.deployment.get("trusted_module"))
        self._session_cache = None
        self._session_error = None

    # -- 宿主会话 -----------------------------------------------------------

    def session(self, *, refresh=False) -> dict:
        if self._session_cache is not None and not refresh:
            return self._session_cache
        try:
            record = self.host.session(self.deployment["session_id"])
        except EvidenceError as exc:
            self._session_error = str(exc)
            raise HostProgressError(f"宿主会话读不到（不可判）：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            self._session_error = f"{type(exc).__name__}: {exc}"
            raise HostProgressError(f"宿主会话读取异常（不可判）：{self._session_error}") from exc
        self._session_cache = record
        self._session_error = None
        return record

    def goal_replay(self) -> dict:
        try:
            record = self.session()
        except HostProgressError as exc:
            return {"available": False, "why": str(exc), "present": False}
        replay = replay_goal(record.get("raw_events") or [])
        replay["session_id"] = record.get("session_id")
        replay["source"] = record.get("source")
        return replay

    # -- goal_state：progress.Progress 可直接用的形状 -----------------------

    def goal_state(self) -> dict:
        replay = self.goal_replay()
        base = {
            "source": "dsh-host",
            "evidence": {
                "session_id": replay.get("session_id"),
                "session_source": replay.get("source"),
                "last_seq": replay.get("last_seq"),
                "changes": replay.get("changes"),
                "objective_sha256": ((replay.get("goal") or {}) or {}).get("objective_sha256"),
                "rounds_started": replay.get("rounds_started"),
                "phase": replay.get("phase"),
            },
        }
        if not replay.get("available"):
            return dict(base, state="unknown", coverage="unknown", known=False,
                        why=replay.get("why") or "goal 状态不可判")
        if not replay.get("present"):
            return dict(base, state="complete", coverage="none", known=True,
                        why="会话当前没有 goal（没有 goal/change 记录）⇒ goal 不覆盖自动续轮，外部推进器可补足")
        phase = replay["phase"]
        if phase == "active":
            trusted = self._trusted_coverage(replay)
            if trusted is None:
                return dict(base, state="active", coverage="unknown", known=False,
                            why="goal phase=active；activation 是宿主进程内状态（源码明示从不落盘）⇒ "
                                "只读持久态不能核 coverage；需要部署可信模块给出 full/none")
            return dict(base, state="active", coverage=trusted["coverage"], known=True,
                        why=trusted["why"], source=trusted["source"],
                        evidence=dict(base["evidence"], trusted_as_of=trusted["as_of"]))
        if phase == "paused":
            return dict(base, state="blocked", coverage="none", known=True,
                        why="goal phase=paused（durable 上不会自动续轮；是否会被恢复只读态不可判）")
        if phase == "blocked":
            reason = ((replay.get("goal") or {}).get("blocked_reason") or {})
            return dict(base, state="blocked", coverage="none", known=True,
                        why=f"goal phase=blocked（code={reason.get('code')}）⇒ 当前不覆盖自动续轮")
        return dict(base, state="complete", coverage="none", known=True,
                    why="goal phase=complete（目标已完成）⇒ goal 不再覆盖推进")

    def _trusted_coverage(self, replay: dict):
        if not self.trusted:
            return None
        function = getattr(self.trusted["module"], "goal_coverage", None)
        if not callable(function):
            return None
        try:
            answer = function(_redacted_view(replay))
        except Exception:  # noqa: BLE001 - 可信模块抛错按「没有可信覆盖」处理
            return None
        if not isinstance(answer, dict):
            return None
        coverage = answer.get("coverage")
        if coverage not in ("full", "none"):
            return None
        if not _text(answer.get("why")) or not _text(answer.get("source")):
            return None
        if "as_of" not in answer:
            return None
        if not (_text(answer.get("as_of")) or _number(answer.get("as_of"))):
            return None
        return {"coverage": coverage, "why": answer["why"].strip(),
                "source": f"trusted:{self.trusted['source']}:{answer['source'].strip()}",
                "as_of": answer["as_of"]}

    # -- overdue ------------------------------------------------------------

    def overdue(self) -> dict:
        result = read_overdue(self.deployment["decisions_db"], now=self.clock(),
                              grace=self.deployment["overdue_grace_sec"])
        result["evidence"] = {"db": self.deployment["decisions_db"],
                              "readonly": True, "grace_sec": self.deployment["overdue_grace_sec"]}
        return result

    def overdue_provider(self) -> dict:
        """给 `progress.Progress(overdue=…)` 用的形状：不可核时**抛错**。

        `progress._overdue_state` 对 provider 异常按「不可判」处理（fail closed）；它不读
        `checked/error` 字段，所以这里必须用异常表达「核不了」，⛔ 不能返回空列表冒充 0 超期。
        """
        result = self.overdue()
        if not result.get("checked"):
            raise HostProgressError(
                f"入向超期不可核：{result.get('why') or '未知原因'}（fail closed，不 0 假绿）")
        return result

    # -- projcache：派生诊断（不参与判定） ----------------------------------

    def projection_diagnostic(self) -> dict:
        sid = self.deployment["session_id"]
        base = Path(self.deployment["dsh_home"]) / "storages" / "session_projcache" / "sessions"
        candidates = [base / f"{sid}.json", base / f"{sid.removeprefix('session-')}.json"]
        path = next((item for item in candidates if item.is_file()), None)
        out = {"available": False, "why": None, "path": None, "matches_replay": None,
               "authoritative": False}
        if path is None:
            return dict(out, why="没有该会话的 projcache 记录（派生缓存缺失不影响判定）")
        try:
            document = _load_json(path)
            record = document.get("record") or {}
            rows = record.get("rows") or {}
            row = rows.get("goal")
            if not isinstance(row, dict):
                return dict(out, path=str(path), why="projcache 没有 goal 行")
            value = row.get("val") if isinstance(row.get("val"), dict) else {}
            current = value.get("current")
            replay = self.goal_replay()
            if current is None:
                phase, revision, rounds, failure = None, None, None, value.get("failure")
                matches = replay.get("available") and not replay.get("present")
            else:
                goal = current.get("goal") or {}
                phase = goal.get("phase")
                revision = goal.get("revision")
                rounds = current.get("roundsStarted")
                failure = value.get("failure")
                target = replay.get("goal") or {}
                matches = bool(replay.get("available") and replay.get("present")
                               and phase == replay.get("phase")
                               and revision == target.get("revision")
                               and rounds == replay.get("rounds_started"))
            return {"available": True, "path": str(path), "why": None,
                    "matches_replay": matches, "authoritative": False,
                    "phase": phase, "revision": revision, "rounds_started": rounds,
                    "row_seq": row.get("seq"), "row_ver": row.get("ver"),
                    "projection_failure": failure,
                    "format_version": document.get("version"),
                    "note": "projcache 是派生缓存：只用于诊断，不参与判定"}
        except (HostProgressError, OSError, AttributeError, TypeError) as exc:
            return dict(out, path=str(path), why=f"projcache 读不动（不影响判定）：{exc}")

    # -- job 台账与核验 -----------------------------------------------------

    def jobs_path(self) -> Path:
        root = Path(self.deployment["state_root"])
        root.mkdir(parents=True, exist_ok=True)
        return root / "jobs.jsonl"

    def _append_job(self, row: dict) -> dict:
        path = self.jobs_path()
        rows = self.jobs()
        sequence = len(rows) + 1
        previous = rows[-1]["sha256"] if rows else "sha256:" + "0" * 64
        record = dict(row, seq=sequence, prev_sha=previous)
        record["sha256"] = "sha256:" + _sha_text(_canonical(
            {k: v for k, v in record.items() if k != "sha256"}))
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def jobs(self) -> list:
        path = self.jobs_path()
        if not path.is_file():
            return []
        rows = []
        previous = "sha256:" + "0" * 64
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise HostProgressError(f"job 台账第 {index} 行不是合法 JSON：{exc}") from exc
            if row.get("seq") != index or row.get("prev_sha") != previous:
                raise HostProgressError(f"job 台账第 {index} 行链不符：疑似被改写或插入")
            expected = "sha256:" + _sha_text(_canonical(
                {k: v for k, v in row.items() if k != "sha256"}))
            if row.get("sha256") != expected:
                raise HostProgressError(f"job 台账第 {index} 行指纹不符：疑似被改写")
            previous = row["sha256"]
            rows.append(row)
        return rows

    def job(self, job_id) -> dict | None:
        job_id = _job_id(job_id)
        for row in reversed(self.jobs()):
            if row.get("job_id") == job_id:
                return row
        return None

    def verify_job(self, job_id, command, argv=None) -> dict:
        """用 DSH 持久 tool/call + tool/result 精确核验一次受管后台启动。

        要求：命令**精确相等**（只做 CRLF 归一）、配对唯一、`started/moved to background job <id>`
        里的 jobId 与登记一致、若给 argv 则 argv 指纹必须与命令对应登记一致。
        """
        job_id = _job_id(job_id)
        if not _text(command):
            return {"verified": False, "job_id": job_id, "why": "登记命令为空"}
        if argv is not None:
            if not isinstance(argv, list) or not argv \
                    or not all(isinstance(item, str) and item for item in argv):
                return {"verified": False, "job_id": job_id, "why": "argv 必须是非空字符串列表"}
            if render_command(argv) != command:
                return {"verified": False, "job_id": job_id,
                        "why": "argv 与命令不互相对应（argv 指纹绑不到这条命令）"}
        target = _norm_command(command)
        try:
            record = self.session()
        except HostProgressError as exc:
            return {"verified": False, "job_id": job_id, "why": str(exc)}
        raw_events = record.get("raw_events") or []
        calls = []
        for row in raw_events:
            if row.get("type") != "tool/call":
                continue
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            name = str(data.get("name") or "")
            if name not in SHELL_TOOL_NAMES and name.removeprefix("functions.") not in SHELL_TOOL_NAMES:
                continue
            arguments = data.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    continue
            if not isinstance(arguments, dict):
                continue
            call_command = arguments.get("command")
            if not _text(call_command):
                continue
            if _norm_command(call_command) != target:
                continue
            calls.append({"seq": row.get("seq"), "callId": data.get("callId"),
                          "name": name, "arguments": arguments,
                          "background": arguments.get("run_in_background") is True})
        if not calls:
            return {"verified": False, "job_id": job_id,
                    "why": "宿主日志里没有命令精确相等的 tool/call（不接受近似或子串匹配）"}
        if len(calls) > 1:
            return {"verified": False, "job_id": job_id,
                    "why": f"命令精确相等的 tool/call 有 {len(calls)} 个，无法唯一绑定 job_id"}
        call = calls[0]
        if not call["callId"]:
            return {"verified": False, "job_id": job_id, "why": "tool/call 缺 callId"}
        results = []
        for row in raw_events:
            message = _message(row)
            if row.get("type") != "tool/result" or message.get("toolCallId") != call["callId"]:
                continue
            if type(row.get("seq")) is not int or type(call["seq"]) is not int \
                    or row["seq"] <= call["seq"]:
                continue
            results.append(row)
        if len(results) != 1:
            return {"verified": False, "job_id": job_id,
                    "why": f"与 callId 配对且序号在后的 tool/result 有 {len(results)} 个（要求恰好 1 个）"}
        result = results[0]
        message = _message(result)
        if message.get("isError") is True or message.get("is_error") is True:
            return {"verified": False, "job_id": job_id, "why": "tool/result 是错误结果"}
        text = visible_text(message.get("content"))
        matches = _BACKGROUND_PHRASE_RE.findall(text)
        if not matches:
            return {"verified": False, "job_id": job_id,
                    "why": "tool/result 文本里没有「started/moved to background job <id>」证据"}
        if matches != [job_id]:
            return {"verified": False, "job_id": job_id,
                    "why": f"tool/result 里的 jobId 是 {matches}，与登记 {job_id} 不一致"}
        if not call["background"] and "moved to background job" not in text:
            return {"verified": False, "job_id": job_id,
                    "why": "tool/call 没有 run_in_background，且结果不是「超时转后台」"}
        argv_sha = _sha_json(argv) if isinstance(argv, list) and argv else None
        return {
            "verified": True,
            "job_id": job_id,
            "session_id": record.get("session_id"),
            "source": record.get("source"),
            "tool": call["name"],
            "call_seq": call["seq"],
            "result_seq": result.get("seq"),
            "command_sha256": _sha_text(command),
            "normalized_command_sha256": _sha_text(target),
            "argv_sha256": argv_sha,
            "result_text_sha256": _sha_text(text),
        }

    def register_job(self, job_id, command, *, argv=None, owner=None) -> dict:
        """登记受管 job：先核验再落 append-only 台账；核不到也如实记 `verified: false`。"""
        job_id = _job_id(job_id)
        if not _text(command):
            raise HostProgressError("register 需要非空的 watch 命令（或用 watch 写的 pending 标记）")
        argv_sha = _sha_json(argv) if isinstance(argv, list) and argv else None
        command_sha = _sha_text(command)
        for row in self.jobs():
            if row.get("job_id") == job_id and row.get("command_sha256") == command_sha:
                return {"registered": True, "idempotent": True, "job_id": job_id,
                        "verified": bool(row.get("verified")), "record": row,
                        "why": "同一 job_id + 同一命令已登记过，按幂等处理"}
        verification = self.verify_job(job_id, command, argv=argv)
        record = self._append_job({
            "at": _iso(self.clock()),
            "job_id": job_id,
            "command": command,
            "command_sha256": command_sha,
            "normalized_command_sha256": _sha_text(_norm_command(command)),
            "argv": argv,
            "argv_sha256": argv_sha,
            "owner": owner or self.deployment["owner"],
            "session_id": self.deployment["session_id"],
            "deployment": self.deployment["name"],
            "verified": bool(verification.get("verified")),
            "verification": {k: v for k, v in verification.items()
                             if k in ("why", "call_seq", "result_seq", "tool", "source",
                                      "result_text_sha256")},
            "why": verification.get("why"),
        })
        return {"registered": True, "idempotent": False, "job_id": job_id,
                "verified": bool(verification.get("verified")),
                "record_locator": f"job:{record['seq']}",
                "record": record, "verification": verification}

    # -- pending 标记（watch 自己写的命令，供 register 用） -----------------

    def pending_path(self) -> Path:
        root = Path(self.deployment["state_root"])
        root.mkdir(parents=True, exist_ok=True)
        return root / "pending_job.json"

    def write_pending_job(self, command, argv, *, pid=None) -> dict:
        marker = {
            "command": command,
            "command_sha256": _sha_text(command),
            "normalized_command_sha256": _sha_text(_norm_command(command)),
            "argv": argv,
            "argv_sha256": _sha_json(argv) if isinstance(argv, list) and argv else None,
            "started_at": _iso(self.clock()),
            "pid": pid if pid is not None else os.getpid(),
            "session_id": self.deployment["session_id"],
            "owner": self.deployment["owner"],
            "deployment": self.deployment["name"],
        }
        path = self.pending_path()
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
        os.replace(temporary, path)
        return marker

    def finish_pending_job(self, result) -> dict:
        path = self.pending_path()
        marker = self.pending_job() or {}
        marker.update({
            "finished_at": _iso(self.clock()),
            "verdict": (result or {}).get("verdict"),
            "reason": (result or {}).get("reason"),
            "intended_exit": (result or {}).get("intended_exit"),
            "needs_rearm": (result or {}).get("needs_rearm"),
        })
        path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
        return marker

    def pending_job(self) -> dict | None:
        path = self.pending_path()
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    # -- 计划：确切命令 + 后台调用 + 登记命令 -------------------------------

    def plan(self, *, poll=None, max_rounds=None) -> dict:
        cli = Path(__file__).with_name("progress_host_cli.py")
        argv = [sys.executable, "-X", "utf8", "-B", str(cli),
                "--deployment", str(self.deployment_path), "watch"]
        if poll is not None:
            argv += ["--poll", str(poll)]
        if max_rounds is not None:
            argv += ["--max-rounds", str(max_rounds)]
        command = (subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv))
        register_argv = [sys.executable, "-X", "utf8", "-B", str(cli),
                         "--deployment", str(self.deployment_path), "register",
                         "--job-id", "<jobId>"]
        register_command = (subprocess.list2cmdline(register_argv) if os.name == "nt"
                            else shlex.join(register_argv))
        return {
            "watch_argv": argv,
            "watch_command": command,
            "command_sha256": _sha_text(command),
            "argv_sha256": _sha_json(argv),
            "host_tool": self.deployment["shell_tool_name"],
            "host_call": {
                "tool": self.deployment["shell_tool_name"],
                "arguments": {"command": command, "run_in_background": True},
            },
            "register_argv": register_argv,
            "register_command": register_command,
            "pending_marker": str(self.pending_path()),
            "notes": [
                "watch 是宿主受管后台作业本体：由 functions.pwsh（或部署配置的 shell tool）启动，"
                "本工具不启动 server、不自续、不替宿主起 job",
                "宿主返回 job_id 后执行 register_command（<jobId> 换成真实值）；"
                "register 会用 DSH 持久 tool_result 核验 exact command + jobId",
                "watch 自己叫不醒宿主：进程结束由宿主投递通知，负责人再按三出口记录并 rearm",
            ],
        }

    # -- 只读状态 -----------------------------------------------------------

    def status(self) -> dict:
        replay = self.goal_replay()
        goal_state = self.goal_state()
        overdue = self.overdue()
        jobs = self.jobs()
        latest = jobs[-1] if jobs else None
        blockers = []
        if not replay.get("available"):
            blockers.append("goal_unavailable")
        elif not goal_state.get("known"):
            blockers.append("goal_coverage_unknown")
        if not overdue.get("checked"):
            blockers.append("overdue_unknown")
        elif overdue.get("overdue"):
            blockers.append("overdue_user_message")
        if latest is not None and not latest.get("verified"):
            blockers.append("job_unverified")
        return {
            "ok": not blockers,
            "blockers": blockers,
            "deployment": {"name": self.deployment["name"], "path": str(self.deployment_path),
                           "session_id": self.deployment["session_id"],
                           "dsh_home": self.deployment["dsh_home"],
                           "decisions_db": self.deployment["decisions_db"],
                           "state_root": self.deployment["state_root"],
                           "shell_tool": self.deployment["shell_tool_name"]},
            "goal": {"replay": replay, "state": goal_state,
                     "projection_diagnostic": self.projection_diagnostic()},
            "overdue": {k: v for k, v in overdue.items() if k != "items"},
            "jobs": {"count": len(jobs), "latest": latest},
            "pending_job": self.pending_job(),
            "readonly": True,
        }

    def doctor(self) -> dict:
        checks = []

        def check(name, function):
            try:
                detail = function()
                checks.append({"name": name, "ok": True, "detail": detail})
            except Exception as exc:  # noqa: BLE001 - 体检把问题收集成明细，不中断
                checks.append({"name": name, "ok": False,
                               "detail": f"{type(exc).__name__}: {exc}"})

        def dsh_home():
            path = Path(self.deployment["dsh_home"])
            if not path.is_dir():
                raise HostProgressError(f"dsh_home 不存在：{path}")
            return str(path)

        def workspace():
            path = Path(self.deployment["workspace"])
            if not path.is_dir():
                raise HostProgressError(f"workspace 不存在：{path}")
            return str(path)

        def spec():
            spec = self.deployment.get("progress_spec")
            if not spec:
                raise HostProgressError("deployment.progress_spec 未配置（watch 需要）")
            if not Path(spec).is_file():
                raise HostProgressError(f"progress_spec 不存在：{spec}")
            return spec

        def session_log():
            record = self.session(refresh=True)
            return {"session_id": record.get("session_id"), "events": len(record.get("events") or []),
                    "raw_events": len(record.get("raw_events") or []), "source": record.get("source")}

        def goal():
            replay = self.goal_replay()
            if not replay.get("available"):
                raise HostProgressError(replay.get("why") or "goal 重放不可判")
            return {"phase": replay.get("phase"), "present": replay.get("present"),
                    "rounds_started": replay.get("rounds_started")}

        def overdue():
            result = self.overdue()
            if not result.get("checked"):
                raise HostProgressError(result.get("why") or "overdue 不可核")
            return {"text_events": result.get("text_events"), "replied": result.get("replied"),
                    "pending": result.get("pending"), "overdue": result.get("count")}

        def jobs():
            rows = self.jobs()
            return {"count": len(rows),
                    "unverified": sum(1 for row in rows if not row.get("verified"))}

        check("dsh_home", dsh_home)
        check("workspace", workspace)
        check("progress_spec", spec)
        check("session_log", session_log)
        check("goal_replay", goal)
        check("overdue_readonly", overdue)
        check("job_ledger", jobs)
        return {"ok": all(item["ok"] for item in checks), "checks": checks,
                "readonly": True, "deployment": self.deployment["name"]}


def build_adapter(deployment, *, host=None, clock=time.time, trusted=None) -> HostAdapter:
    """固定 deployment factory：CLI 与部署模块都用它，不接受 caller 传证据路径。"""
    return HostAdapter(deployment, host=host, clock=clock, trusted=trusted)


__all__ = [
    "HostAdapter", "HostProgressError", "DeploymentError", "load_deployment", "build_host",
    "load_trusted", "build_adapter", "replay_goal", "read_overdue", "render_command",
    "DEPLOYMENT_VERSION", "TEXT_REPLY_GRACE_SECONDS", "CARD_CLICK_INTENTS", "JOB_ID_RE",
    "SHELL_TOOL_NAMES", "GOAL_PHASES",
]
