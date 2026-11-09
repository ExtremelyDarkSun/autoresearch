# GENERATED-BY: progress remote preflight bridge, 2026-11-10
# LAST-EDIT: 2026-11-09 首版：把已完成的 `RemoteWorkflow.check` 接成下一轮预检只读入口
# PURPOSE: 给进度/调度侧提供一个**只读、不联网、不建 Runtime、不 provision、不发送**的远端预检：
#          部署 → worker 收据 → 真正所有权 → 资源租约现状 → 计划资源一致 → 同一笔预算预留
#          → 统一闸（准备 / 来源 / 审计 / 监控 / 执行准入 / argv），逐项给 pass/missing/blocked/unknown，
#          全过才给可执行的 `remote_cli.py start ... -- argv`。
# ORIGIN: 复用 kernel/autoresearch/remote_workflow.py（RemoteWorkflow.check / read_deployment / 只读查询）、
#         remote_cli._space(readonly=True)（生产固定接 DSHHost + Monitoring + Admission）、
#         runtime.store.ReadonlyStore（零副作用只读句柄）。⛔ 不复制闸的判据。
# CUMULATIVE-DELTA: 首版。只新增本文件与 tests/test_progress_remote.py；不改 progress.py / CLI / docs /
#         remote 核心（remote_workflow.py · remote_cli.py）。返回结构按 Progress 约定（status/checks/
#         missing/blocked/unknown/launch_command/check），另带 preparation/plan_meta/budget/lease 等投影。
"""下一轮预检的受管远端只读入口（Progress 用）。

这一层解决什么
==============

进度/调度侧要回答「下一轮能不能推荐远端」：它原本只认本地 CPU，远端一律 `unknown`。
`RemoteWorkflow.check` 已经能只读跑完「部署 / 预算 / 所有权 / 统一闸」，但**不返回**
`preparation` / 冻结计划元数据，也不回答「资源现在能不能拿去排」（租约现状）与「起跑 argv 长什么样」。
本文件只做组合与投影，判据一条不复制：

    remote_preflight(...)
      → read_deployment（缺 / disabled / 参数不安全 ⇒ 逐条 blocked）
      → 只读读 preparation / gov_plans（plan_id / 版本 / 指纹 / 计划元数据）
      → remote_cli._space(readonly=True)（生产固定 DSHHost + Monitoring.check_start + Admission；
        测试注入只在 Python 层，且必须显式 allow_fixture_gates=True）
      → RemoteWorkflow(..., readonly=True).check(...)    ← 闸唯一判据
      → 只读补三项结构检查：worker 收据 / 真正所有权 / 资源租约现状
      → 逐项映射成 checks / missing / blocked / unknown / launch_command

硬边界（⛔ 不要读大）
====================

1. **只读**：不构造 Runtime（写实例）、不 provision、不 submit、不 claim 租约、不联网。
   只用 `ReadonlyStore` / `mode=ro` 连接；缺库直接拒，**不建库、不建目录、不补 WAL sidecar**
   （`writes` 恒为 `[]`，测试按目录集合与文件 identity 复核）。
2. **判据只有一份**：闸仍只在 `workflow.py`；本文件只摊平结果 + 补 worker / 所有权 / 租约现状。
3. **默认生产不跳闸**：`monitor_check` / `authorization_check` / `verifier`（或 `services` 里同名键）
   任一注入即视为测试接线，必须同时显式 `allow_fixture_gates=True`；否则按夹具闸拒（fail-closed）。
   `services["host"]` **不采用**：⛔ 不允许调用方换掉可信日志根（默认仍是 `DSHHost()`）。
4. **不给「ready」以外的假信号**：`launch_command` 只是 argv 助手；跑它仍然要过生产闸，
   本文件**不宣称**真实 SSH / worker 已验（证据等级见 `evidence`）。

返回（Progress 约定 + 本层投影）
================================

    ok                    bool（= status == 'runnable'）
    status                'runnable' | 'blocked' | 'unknown'
    checks                {name: {state: 'pass'|'missing'|'blocked'|'unknown', reason, evidence?}}
    missing / blocked / unknown   [检查名]
    launch_command        list[str] | None（仅 status='runnable'）
    check                 RemoteWorkflow.check 的原始返回（Progress 可再窄映射）
    notes                 [str]（证据等级 / 接线来源 / 未评估项）
    read_only / writes / network / flow_type
    以下为本层补充投影：
    blockers[{code, level, detail, source}] / problems[...]
    topic / plan_id / argv / resources / request_key
    preparation{prepared_id, plan_id, plan_version, plan_sha256, source_fingerprint, declared_argv}
    frozen_plan{plan_id, version, key_fingerprint, plan_sha256, sha256}
    plan_meta{hypothesis, budget, case_count, authorization, execution_argv, deployment_id,
              unit_root, task_ids, required_artifacts, plan_resources, execution_scope}
    budget{...} / lease{status, details} / deployment{...} / worker{ok, problems, path}
    wiring{source: production-readonly|injected, monitor_check, authorization_check,
           verifier, fixture_gates}

状态约定（给 Progress 的窄映射用）
==================================

* `blocked`：确定的硬前置缺失或明确拒绝（部署缺/disabled、worker 未 provision、无真正所有权、
  无预算预留、资源被占用、计划资源不符、闸不过、注入了夹具闸却没允许）。
* `unknown`：**不可判**（状态库不可读、监控/审计不可判、资源在 quarantine、只读决策服务不可用等）。
  这类不写「失败」，只表示「现在不推荐」。
* `status` 优先级：`blocked` > `unknown` > `runnable`。
* 早退时没跑到的检查标 `unknown`，`reason` 写明「未评估：更早的前置未过（没有当作通过）」。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # 让 `python kernel/autoresearch/progress_remote.py` 也能跑
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch import remote_cli  # noqa: E402
from kernel.autoresearch import remote_workflow as rw  # noqa: E402
from kernel.autoresearch.remote_workflow import (  # noqa: E402
    RemoteWorkflow,
    RemoteWorkflowError,
    _readonly_conn,
    read_deployment,
)
from kernel.autoresearch.workflow import WorkflowError  # noqa: E402

__all__ = ("REMOTE_CLI_PATH", "launch_command", "remote_preflight")

#: 起跑 argv 里用的入口（绝对路径，Progress 不需要猜 cwd 或相对路径）。
REMOTE_CLI_PATH = str(Path(remote_cli.__file__).resolve())

#: 证据等级（⛔ 不冒充真实运行）。
EVIDENCE = "offline-synthetic：只读本地状态；真实 SSH / POSIX worker 运行未验"

#: `services` 里允许解出的固定对象；`host` 之类一律不采用（不许换可信日志根）。
SERVICE_KEYS = ("monitor_check", "authorization_check", "verifier")

_CHECK_ORDER = ("deployment", "worker", "ownership", "lease", "plan_resources", "preparation",
                "plan", "budget", "sources", "audit", "monitor", "authorization",
                "execution_target", "argv", "fixtures")


# ---------------------------------------------------------------------------
# 起跑命令（只是 argv 助手，⛔ 不宣称 ready）
# ---------------------------------------------------------------------------


def launch_command(workspace, *, topic, plan, owner_session, argv, resources=(),
                   intent_id=None, intent_none=False, db=None):
    """拼出 `remote_cli.py start` 的 argv：绝对解释器 + 绝对脚本 + `--` 后的完整 argv。

    ⛔ 这不是「可以起跑」的判据：先用 `remote_preflight` 拿到 `ok=True`/`status='runnable'`。
    `remote start` 自己会再过一遍生产闸；任何一项不过仍然零远端请求。
    `resources` 就是显式资源键（与计划 `execution.remote.resources` 逐项相等，否则预检 blocked）。
    """
    if intent_none and intent_id:
        raise ValueError("intent 声明冲突：intent_id 与 intent_none 只能给一个")
    if not intent_none and not intent_id:
        raise ValueError("必须显式声明监控入口：intent_id 或 intent_none（省略不默认放行）")
    if not isinstance(argv, (list, tuple)) or not argv \
            or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv 必须是非空字符串数组")
    command = [sys.executable, "-X", "utf8", "-B", REMOTE_CLI_PATH, "start",
               "--workspace", str(Path(workspace).resolve()),
               "--topic", str(topic),
               "--plan", str(plan),
               "--owner-session", str(owner_session)]
    if db:
        command += ["--db", str(Path(db).resolve())]
    if intent_none:
        command.append("--intent-none")
    else:
        command += ["--intent-id", str(intent_id)]
    for key in resources or ():
        command += ["--resource", str(key)]
    command += ["--", *[str(item) for item in argv]]
    return command


def _display(command):
    """给日志看的可读串（⛔ 不要拿它去 exec：带空格的参数由调用方自己转义）。"""
    return " ".join(item if " " not in item else json.dumps(item) for item in command)


# ---------------------------------------------------------------------------
# 小工具：检查台账
# ---------------------------------------------------------------------------


class _Ledger:
    """`checks` / `missing` / `blocked` / `unknown` 的单一写入口（避免四张表互相漂）。"""

    def __init__(self):
        self.checks = {}
        self.order = list(_CHECK_ORDER)

    def set(self, name, state, reason, evidence=None):
        if name not in self.order:
            self.order.append(name)
        item = {"state": state, "reason": str(reason)}
        if evidence is not None:
            item["evidence"] = evidence
        self.checks[name] = item
        return self

    def pass_(self, name, reason, evidence=None):
        return self.set(name, "pass", reason, evidence)

    def missing(self, name, reason, evidence=None):
        return self.set(name, "missing", reason, evidence)

    def blocked(self, name, reason, evidence=None):
        return self.set(name, "blocked", reason, evidence)

    def unknown(self, name, reason, evidence=None):
        return self.set(name, "unknown", reason, evidence)

    def fill_unevaluated(self, names, why="未评估：更早的前置未过（没有当作通过）"):
        for name in names:
            if name not in self.checks:
                self.set(name, "unknown", why)

    def lists(self):
        out = {"missing": [], "blocked": [], "unknown": []}
        for name in self.order:
            item = self.checks.get(name)
            if item is not None and item["state"] in out:
                out[item["state"]].append(name)
        return out

    def ordered(self):
        return {name: self.checks[name] for name in self.order if name in self.checks}


def _resolve_intents(intents, intent_id, intent_none):
    """`intents={'intent_id': …}` / `{'none': True}` 与显式参数的唯一化。"""
    if intents is not None:
        if not isinstance(intents, dict):
            raise ValueError("intents 必须是 {'intent_id': …} 或 {'none': True}")
        unknown = sorted(set(intents) - {"intent_id", "none"})
        if unknown:
            raise ValueError(f"intents 有未知键：{unknown}")
        given_id = intents.get("intent_id")
        given_none = intents.get("none") is True
        if intent_id is not None or intent_none:
            raise ValueError("intents 与 intent_id/intent_none 只能给一种")
        intent_id, intent_none = given_id, given_none
    if intent_none and intent_id:
        raise ValueError("intent 声明冲突：intent_id 与 none 只能给一个")
    return intent_id, bool(intent_none)


def _services(services, monitor_check, authorization_check, verifier):
    """从 `services` 解出固定的三个对象；⛔ `host` 之类一律不采用（不许换可信日志根）。"""
    notes = []
    if services is not None:
        if not isinstance(services, dict):
            raise ValueError("services 必须是对象（只认 monitor_check / authorization_check / verifier）")
        unknown = sorted(set(services) - set(SERVICE_KEYS))
        if unknown:
            notes.append(f"services 里的 {unknown} 未被采用：只允许 {'/'.join(SERVICE_KEYS)}"
                         "（⛔ 不允许换可信日志根 host）")
        for key in SERVICE_KEYS:
            if services.get(key) is not None:
                provided = {"monitor_check": monitor_check,
                            "authorization_check": authorization_check,
                            "verifier": verifier}[key]
                if provided is not None:
                    raise ValueError(f"{key} 同时由 services 与关键字参数给出")
                if key == "monitor_check":
                    monitor_check = services[key]
                elif key == "authorization_check":
                    authorization_check = services[key]
                else:
                    verifier = services[key]
    return monitor_check, authorization_check, verifier, notes


# ---------------------------------------------------------------------------
# 只读结构检查（补三项：worker 收据 / 所有权 / 租约现状）
# ---------------------------------------------------------------------------


def _worker_state(flow):
    """worker provision 收据：缺 / 身份不符 / 版本不符 ⇒ 不可提交。"""
    try:
        record = flow.provisioned()
    except (RemoteWorkflowError, WorkflowError) as exc:
        return "unknown", f"worker 收据不可读：{exc}", None
    if record.get("ok"):
        return "pass", "provision 收据与当前 worker 字节 / 版本 / 部署身份一致", {
            "path": record.get("path"), "worker_version": record.get("worker_version"),
            "worker_sha256": record.get("worker_sha256")}
    return "blocked", "worker 未 provision 或不符：" + "；".join(
        record.get("problems") or ["不可判"]), {"path": record.get("path")}


def _ownership_state(db, plan_id):
    """真实 Runtime 所有权（backend_migrations + 目录 marker）。"""
    if not plan_id:
        return "missing", "缺 plan_id：不知道要核哪个实验的所有权", None
    try:
        from kernel.autoresearch.runtime.backend import require_runtime_ownership
        require_runtime_ownership(SimpleNamespace(db_path=str(db)), plan_id)
        return "pass", f"实验 {plan_id} 的 Runtime 所有权可核（marker 与库一致）", None
    except Exception as exc:  # noqa: BLE001 - 不可核就是不放行
        level = "unknown" if isinstance(exc, OSError) else "blocked"
        return level, (f"实验 {plan_id} 没有可核的 Runtime 所有权"
                       f"（{type(exc).__name__}: {exc}）"), None


def _lease_view(db, resources):
    """资源租约现状（只读）：free / held / quarantined / unconfigured / unknown。

    ⚠️ 只读预检**不 claim** 租约：取租约发生在 `remote start` 的写路径里。
    """
    view = {"status": "unknown", "resources": [str(item) for item in resources or ()],
            "details": {}, "note": "只读预检不 claim 租约；取租约发生在 remote start 的写路径"}
    if not resources:
        view.update(status="unconfigured", note="没有给资源键：没有资源租约不得提交")
        return view
    try:
        with _readonly_conn(db) as conn:
            held, quarantined = [], []
            for key in view["resources"]:
                lease = rw._row(conn, "resources", "SELECT * FROM resources WHERE resource_key=?",
                                (key,))
                if lease is not None:
                    held.append({"resource_key": key, "task_id": lease.get("task_id"),
                                 "lease_until": lease.get("lease_until")})
                block = rw._row(conn, "scheduler_resource_quarantine",
                                "SELECT * FROM scheduler_resource_quarantine"
                                " WHERE resource_key=?", (key,))
                if block is not None:
                    quarantined.append({"resource_key": key, "reason": block.get("reason")})
            view["details"] = {"held": held, "quarantined": quarantined}
            if quarantined:
                view["status"] = "quarantined"
            elif held:
                view["status"] = "held"
            else:
                view["status"] = "free"
    except (sqlite3.Error, RemoteWorkflowError, OSError) as exc:
        view.update(status="unknown", note=f"租约现状不可判：{exc}")
    return view


# ---------------------------------------------------------------------------
# preparation / 冻结计划 / 计划元数据（只读；⛔ 不重复跑闸）
# ---------------------------------------------------------------------------


def _load_preparation(db, topic, plan_id=None):
    meta = {"preparation": None, "frozen_plan": None, "plan": None, "problems": []}
    try:
        with _readonly_conn(db) as conn:
            if plan_id:
                preparation = rw._row(
                    conn, "wf_preparations",
                    "SELECT * FROM wf_preparations WHERE plan_id=? AND topic=?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT 1", (plan_id, topic))
            else:
                preparation = rw._row(
                    conn, "wf_preparations",
                    "SELECT * FROM wf_preparations WHERE topic=?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT 1", (topic,))
            if preparation is not None:
                frozen = rw._row(
                    conn, "gov_plans",
                    "SELECT version,key_fingerprint,plan_json FROM gov_plans WHERE plan_id=?"
                    " ORDER BY version DESC LIMIT 1", (preparation["plan_id"],))
            else:
                frozen = None
    except (sqlite3.Error, RemoteWorkflowError) as exc:
        meta["problems"].append(f"准备记录 / 冻结计划不可读（不可判）：{exc}")
        return meta
    if preparation is None:
        meta["problems"].append("这一轮还没有 prepare 记录：先 prepare（会冻结来源、实现声明与 argv）")
    else:
        try:
            declared = json.loads(preparation["argv_json"])
        except ValueError:
            declared = None
        meta["preparation"] = {
            "prepared_id": preparation["prepared_id"],
            "plan_id": preparation["plan_id"],
            "plan_version": preparation["plan_version"],
            "plan_sha256": preparation["plan_sha256"],
            "source_fingerprint": preparation["source_fingerprint"],
            "owner_session": preparation["owner_session"],
            "created_at": preparation["created_at"],
            "declared_argv": declared,
        }
    if frozen is None:
        if preparation is not None:
            meta["problems"].append("计划还没有冻结过：先 prepare（会冻结并记录哈希）")
    else:
        plan = json.loads(frozen["plan_json"])
        canonical = rw.canonical_json(plan)
        meta["plan"] = plan
        meta["frozen_plan"] = {
            "plan_id": preparation["plan_id"], "version": frozen["version"],
            "key_fingerprint": frozen["key_fingerprint"],
            "plan_sha256": rw._sha_text(canonical),
            "sha256": rw._sha_text(canonical),
        }
    if meta["preparation"] and meta["frozen_plan"]:
        if int(meta["preparation"]["plan_version"]) != int(meta["frozen_plan"]["version"]) \
                or meta["preparation"]["plan_sha256"] != meta["frozen_plan"]["plan_sha256"]:
            meta["problems"].append(
                "准备记录与最新冻结计划不一致（计划在 prepare 之后被改写 / 重新冻结）"
                "⇒ 先重新 prepare")
    return meta


def _plan_meta(plan):
    """Progress 逐项映射要的字段（只读投影；plan 来自 gov_plans 的冻结版本）。"""
    if not isinstance(plan, dict):
        return None
    budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
    case_sets = plan.get("case_sets") if isinstance(plan.get("case_sets"), dict) else {}
    case_count = sum(len(case_sets.get(kind) or []) for kind in ("target", "regression", "control")
                     if isinstance(case_sets.get(kind), list))
    hypothesis = plan.get("hypothesis")
    if isinstance(hypothesis, dict):
        hypothesis = {"id": hypothesis.get("id"), "text": hypothesis.get("text")}
    authorization = plan.get("authorization") if isinstance(plan.get("authorization"), dict) else {}
    execution = plan.get("execution") if isinstance(plan.get("execution"), dict) else {}
    remote = execution.get("remote") if isinstance(execution.get("remote"), dict) else {}
    return {
        "goal": plan.get("goal"),
        "hypothesis": hypothesis,
        "budget": {"max_seconds": budget.get("max_seconds"),
                   "max_cost_units": budget.get("max_cost_units")},
        "case_count": case_count,
        "authorization": {"scope": authorization.get("scope"),
                          "series_id": authorization.get("series_id"),
                          "reservation_id": authorization.get("reservation_id"),
                          "decision_id": authorization.get("decision_id")},
        "execution_argv": plan.get("execution_argv"),
        "deployment_id": execution.get("deployment_id"),
        "unit_root": remote.get("unit_root"),
        "task_ids": remote.get("task_ids"),
        "required_artifacts": remote.get("required_artifacts"),
        "plan_resources": list(remote.get("resources") or []),
        "execution_scope": execution.get("scope"),
    }


# ---------------------------------------------------------------------------
# 闸结果的窄映射（只读摊平；判据不复制）
# ---------------------------------------------------------------------------


def _gate_checks(ledger, result, injected, allow_fixture_gates, meta_plan_resources, resources):
    """把 `RemoteWorkflow.check` 的 checks/problems 摊成命名检查。"""
    checks = result.get("checks") or {}
    problems = list(result.get("problems") or [])

    def has(word):
        return any(word in item for item in problems)

    if injected:
        if allow_fixture_gates:
            ledger.pass_("fixtures", "测试注入的三件对象已显式 allow_fixture_gates=True"
                                     "（生产路径没有这个开关）")
        else:
            ledger.blocked("fixtures", "注入了测试闸但没有 allow_fixture_gates=True："
                                       "生产默认不跳闸")
    else:
        ledger.pass_("fixtures", "生产只读接线：DSHHost + Monitoring.check_start + Admission，"
                                 "没有跳过闸的开关")

    target = checks.get("execution_target") or {}
    if target:
        if target.get("ok") is True:
            ledger.pass_("execution_target",
                         f"执行落点是受管远端（scope={target.get('execution_scope')!r}）",
                         {"deployment_id": target.get("deployment_id")})
        else:
            ledger.blocked("execution_target", "；".join(target.get("problems") or ["不可判"]))
    else:
        ledger.unknown("execution_target", "闸没有执行落点结果（前置未过）")

    audit = checks.get("audit") or {}
    if audit:
        if audit.get("ok") is True:
            ledger.pass_("audit", "当前审计通过",
                         {"decision": audit.get("decision"),
                          "independence": audit.get("independence")})
        else:
            level = "unknown" if has("不可判") else "blocked"
            ledger.set("audit", level, "当前审计不通过或不可判"
                       f"（decision={audit.get('decision')!r}）")
    else:
        ledger.unknown("audit", "闸没有审计结果（前置未过）")

    monitor = checks.get("monitor") or {}
    if monitor:
        if monitor.get("unwired"):
            ledger.blocked("monitor", "监控检查未接线（monitor_check=None）：受管远端不放行")
        elif monitor.get("fixture") and not allow_fixture_gates:
            ledger.blocked("monitor", "监控检查是夹具（require_monitor_check=False）："
                                      "生产不放行（只有显式 allow_fixture_gates=True 的测试能过）")
        elif monitor.get("ok") is True:
            ledger.pass_("monitor", "当前监控放行（Monitoring.check_start）",
                         {"registered": monitor.get("registered"),
                          "state": monitor.get("state"),
                          "intent_id": monitor.get("intent_id"),
                          "fixture": bool(monitor.get("fixture"))})
        else:
            level = "unknown" if has("不可判") else "blocked"
            ledger.set("monitor", level, "当前监控不放行："
                       + "；".join(monitor.get("problems") or ["不可判"]))
    else:
        ledger.unknown("monitor", "闸没有监控结果（前置未过）")

    authorization = checks.get("authorization") or {}
    if authorization:
        if authorization.get("unwired"):
            ledger.blocked("authorization", "执行准入未接线（authorization_check=None）："
                                            "受管远端不放行")
        elif authorization.get("fixture") and not allow_fixture_gates:
            ledger.blocked("authorization", "执行准入是夹具（require_authorization_check=False）："
                                            "生产不放行")
        elif authorization.get("ok") is True:
            ledger.pass_("authorization", "执行准入通过（Admission 核同一笔 authorization 预留）",
                         {"decision": authorization.get("decision"),
                          "scope": authorization.get("scope"),
                          "fixture": bool(authorization.get("fixture"))})
        else:
            level = "unknown" if has("不可判") else "blocked"
            ledger.set("authorization", level, "执行准入不放行："
                       + "；".join(authorization.get("problems") or ["不可判"]))
    else:
        ledger.unknown("authorization", "闸没有执行准入结果（前置未过）")

    if checks.get("preparation"):
        ledger.pass_("preparation", "本轮 prepare 记录存在且与负责人一致",
                     {"prepared_id": checks["preparation"].get("prepared_id")})
    else:
        ledger.missing("preparation", "这一轮还没有 prepare 记录")

    plan_state = checks.get("plan") or {}
    if plan_state:
        ledger.pass_("plan", "计划与已冻结版本一致（逐字节 canonical 比较）",
                     {"version": plan_state.get("version"),
                      "key_fingerprint": plan_state.get("key_fingerprint")})
    else:
        ledger.missing("plan", "计划还没有冻结过 / 与冻结版本不一致")

    if has("准备记录已过期") or has("来源") or has("用户原话"):
        ledger.blocked("sources", "准备来源在闸里被判过期 / 变化："
                       + "；".join(item for item in problems if "来源" in item or "过期" in item))
    elif checks.get("preparation"):
        ledger.pass_("sources", "准备来源在闸里逐文件 sha256 复核过（结果见 check.problems）")
    else:
        ledger.unknown("sources", "没有 prepare 记录，来源无从核")

    if has("实际 argv") or has("argv 不一致"):
        ledger.blocked("argv", "实际 argv 与事前声明不一致（逐项严格比对）")
    elif result.get("argv"):
        ledger.pass_("argv", "实际 argv 与计划声明逐项相等", {"argv": result.get("argv")})
    else:
        ledger.unknown("argv", "闸没有给出实际 argv")

    budget = result.get("budget") or {}
    if budget and budget.get("exists") and budget.get("status") == "reserved":
        ledger.pass_("budget", "同一笔 authorization 预算预留仍 reserved",
                     {"reservation_id": budget.get("reservation_id"),
                      "scope": budget.get("scope"), "units": budget.get("units")})
    elif budget and budget.get("exists"):
        ledger.blocked("budget", f"预算预留状态是 {budget.get('status')!r}（需 reserved）")
    elif budget:
        ledger.missing("budget", "预算预留不存在：" + str(budget.get("reservation_id")))
    else:
        ledger.unknown("budget", "闸没有给出预算视图")

    declared = list(meta_plan_resources or [])
    given = [str(item) for item in resources or ()]
    if declared and given != declared:
        ledger.blocked("plan_resources", f"计划声明 resources={declared} 与实际 {given} 不一致")
    elif not given:
        ledger.missing("plan_resources", "没有给资源键：没有资源租约不得提交")
    else:
        ledger.pass_("plan_resources", "资源键与计划声明一致（或计划未声明）", {"resources": given})


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def remote_preflight(workspace, *, topic, plan, owner_session, argv, resources=(),
                     intent_id=None, intent_none=False, db=None, intents=None, now=None,
                     services=None, monitor_check=None, authorization_check=None,
                     verifier=None, clock=None, allow_fixture_gates=False):
    """下一轮远端预检（**只读**）。返回 Progress 约定结构 + 本层投影。

    参数
    ----
    workspace    绝对工作区路径
    topic        专题 id
    plan         计划：工作区内相对路径 或 内存 dict
    owner_session 负责人会话
    argv         **完整实际 argv**（与计划 `execution_argv` 逐项相等）
    resources    显式正式资源键（与计划 `execution.remote.resources` 一致）
    intents      `{'intent_id': …}` 或 `{'none': True}`（也可用 intent_id/intent_none）
    now          可选；给了就当 clock（只影响本层时间字段，不参与判据）
    services     可选：`{'monitor_check','authorization_check','verifier'}`（测试注入）
    allow_fixture_gates  测试注入时必须显式 True；默认 False（生产不跳闸）
    """
    workspace = Path(workspace).resolve()
    db_path = Path(db).resolve() if db else (workspace / ".autoresearch" / "runtime.sqlite3")
    ledger = _Ledger()
    notes = []
    check = None
    out = {
        "ok": False, "status": "unknown", "checks": {}, "missing": [], "blocked": [],
        "unknown": [], "launch_command": None, "check": None, "notes": notes,
        "read_only": True, "writes": [], "network": False, "flow_type": "remote",
        "blockers": [], "problems": [],
        "topic": topic, "plan_id": None,
        "argv": [str(item) for item in argv] if isinstance(argv, (list, tuple)) else None,
        "resources": [str(item) for item in resources or ()],
        "request_key": None, "preparation": None, "frozen_plan": None, "plan_meta": None,
        "run_spec": None, "deployment": None, "worker": None, "budget": None,
        "lease": {"status": "unknown", "details": {}, "resources": [], "note": "还没有检查"},
        "wiring": {"source": "production-readonly", "fixture_gates": False},
        "evidence": EVIDENCE,
    }

    def blocker(code, level, detail, source):
        out["blockers"].append({"code": code, "level": level, "detail": detail,
                                "source": source})
        out["problems"].append(detail)

    def finish():
        lists = ledger.lists()
        out["checks"] = ledger.ordered()
        out.update(lists)
        if lists["blocked"]:
            out["status"] = "blocked"
        elif lists["unknown"]:
            out["status"] = "unknown"
        else:
            out["status"] = "runnable"
        out["ok"] = out["status"] == "runnable"
        out["launch"]["ready"] = out["ok"]
        out["launch"]["requires"] = (["remote start 会再过一遍生产闸（本预检不是起跑许可）"]
                                     if out["ok"] else ["修掉 blocked / unknown 后再预检一次"])
        if out["ok"]:
            try:
                command = launch_command(
                    workspace, topic=topic, plan=plan, owner_session=owner_session,
                    argv=argv, resources=out["resources"], intent_id=intent_id,
                    intent_none=bool(intent_none), db=db)
            except ValueError as exc:  # 例如 intent 冲突 / argv 不是数组
                out["ok"] = False
                out["status"] = "blocked"
                out["launch"]["ready"] = False
                out["launch"]["requires"] = ["起跑 argv 拼不出来：" + str(exc)]
                blocker("invalid_arguments", "blocked", f"起跑 argv 拼不出来：{exc}",
                        "progress_remote")
                ledger.blocked("argv", f"起跑 argv 拼不出来：{exc}")
                out["checks"] = ledger.ordered()
                out.update(ledger.lists())
                out["code"] = 9
                return out
            out["launch"]["command"] = command
            out["launch"]["display"] = _display(command)
            out["launch_command"] = command
        out["code"] = 0 if out["ok"] else (9 if out["status"] == "blocked" else 3)
        return out

    out["launch"] = {"ready": False, "command": None, "display": None, "requires": []}

    # 0) 参数唯一化（intents / services / now）
    try:
        intent_id, intent_none = _resolve_intents(intents, intent_id, intent_none)
        monitor_check, authorization_check, verifier, service_notes = _services(
            services, monitor_check, authorization_check, verifier)
        notes.extend(service_notes)
    except ValueError as exc:
        blocker("invalid_arguments", "blocked", str(exc), "progress_remote")
        ledger.blocked("fixtures", str(exc))
        out["status"] = "blocked"
        return finish()
    if now is not None:
        if clock is not None:
            notes.append("now 与 clock 同时给了：以 clock 为准")
        else:
            clock = (lambda value=float(now): value)
    if ledger.checks == {}:
        pass  # 保持可读性；下面逐项写入

    injected = any(value is not None for value in
                   (monitor_check, authorization_check, verifier))
    out["wiring"] = {"source": "injected" if injected else "production-readonly",
                     "fixture_gates": bool(allow_fixture_gates) if injected else False}

    # 1) 部署配置（缺 / disabled / 参数不安全）
    deployment, deployment_problems = read_deployment(workspace)
    out["deployment"] = None if deployment is None else deployment.as_public()
    if deployment is None:
        ledger.blocked("deployment", "；".join(deployment_problems or ["部署不可用"]))
        for item in deployment_problems:
            blocker("deployment_unavailable", "blocked", item, "deployment.json")
        ledger.fill_unevaluated(_CHECK_ORDER[1:])
        out["status"] = "blocked"
        return finish()
    ledger.pass_("deployment", "受管远端部署已显式启用（enabled=true）且参数安全",
                 {"deployment": out["deployment"]})

    # 2) 状态库（缺库直接拒；⛔ 不建库）
    if not db_path.is_file():
        ledger.blocked("preparation", f"工作区数据库不存在：{db_path}（只读预检不建库）")
        for item in ("worker", "ownership", "lease", "plan_resources", "plan", "budget",
                     "sources", "audit", "monitor", "authorization", "execution_target",
                     "argv", "fixtures"):
            ledger.missing(item, "工作区状态库不存在：先 init / prepare（只读预检不建库）")
        blocker("workspace_db_missing", "blocked",
                f"工作区数据库不存在：{db_path}（只读预检不建库；先 init / prepare）",
                "runtime.sqlite3")
        out["status"] = "blocked"
        return finish()

    # 3) preparation / 冻结计划（只读；⛔ 不重复跑闸）
    meta = _load_preparation(db_path, topic)
    out["preparation"] = meta["preparation"]
    out["frozen_plan"] = meta["frozen_plan"]
    out["plan_meta"] = _plan_meta(meta["plan"])
    if meta["preparation"]:
        out["plan_id"] = meta["preparation"]["plan_id"]
    elif meta["plan"] is not None:
        out["plan_id"] = meta["plan"].get("id")
    for item in meta["problems"]:
        notes.append(item)

    # 4) 只读接线（生产 = 真实宿主监控 + Admission；注入 = 测试，必须显式 allow）
    if injected and not allow_fixture_gates:
        ledger.blocked("fixtures", "注入了测试闸但没有 allow_fixture_gates=True："
                                   "生产默认不跳闸")
        ledger.fill_unevaluated([name for name in _CHECK_ORDER if name not in ledger.checks])
        blocker("fixture_gates_not_allowed", "blocked",
                "注入了 monitor_check / authorization_check / verifier 但没有 "
                "allow_fixture_gates=True：生产默认不跳闸", "progress_remote")
        out["status"] = "blocked"
        return finish()
    try:
        space = ({"source": "injected", "store": None, "monitor_check": monitor_check,
                  "authorization_check": authorization_check, "verifier": verifier,
                  "fixture_gates": True}
                 if injected else
                 dict(remote_cli._space(workspace, db, readonly=True),
                      source="production-readonly", fixture_gates=False))
    except (RemoteWorkflowError, WorkflowError) as exc:
        code = getattr(exc, "code", 2)
        level = "unknown" if code == 3 else "blocked"
        blocker("wiring_unavailable", level, f"只读接线不可用：{exc}", "remote_cli._space")
        ledger.unknown("fixtures", f"只读接线不可用：{exc}")
        ledger.fill_unevaluated([name for name in _CHECK_ORDER if name not in ledger.checks])
        out["status"] = "unknown" if level == "unknown" else "blocked"
        return finish()
    out["wiring"].update({"source": space["source"],
                          "monitor_check": space.get("monitor_check") is not None,
                          "authorization_check": space.get("authorization_check") is not None,
                          "verifier": space.get("verifier") is not None,
                          "fixture_gates": space.get("fixture_gates")})
    notes.append("接线来源：" + space["source"] +
                 ("（测试注入，生产路径没有此开关）" if space["source"] == "injected"
                  else "（生产固定接 DSHHost + Monitoring + Admission）"))

    # 5) 唯一闸：RemoteWorkflow.check（只读、不联网、不提交）
    flow = RemoteWorkflow(
        workspace, db=db, deployment=deployment, readonly=True,
        monitor_check=space.get("monitor_check"),
        require_monitor_check=not injected,
        authorization_check=space.get("authorization_check"),
        require_authorization_check=not injected,
        verifier=space.get("verifier"), clock=clock,
        allow_fixture_gates=bool(allow_fixture_gates))
    try:
        check = flow.check(topic=topic, plan=plan, owner_session=owner_session, argv=argv,
                           resources=list(resources or ()), intent_id=intent_id,
                           intent_none=bool(intent_none))
    except (RemoteWorkflowError, WorkflowError) as exc:
        code = getattr(exc, "code", 2)
        level = "unknown" if code == 3 else "blocked"
        blocker("preflight_failed", level, f"只读预检失败：{exc}", "remote_workflow.check")
        ledger.set("plan", level, f"只读预检没有跑完：{exc}")
        ledger.fill_unevaluated([name for name in _CHECK_ORDER if name not in ledger.checks])
        out["status"] = "unknown" if level == "unknown" else "blocked"
        return finish()
    out["check"] = check
    out.update({"plan_id": check.get("plan_id") or out["plan_id"],
                "request_key": check.get("request_key"),
                "run_spec": check.get("run_spec"),
                "budget": check.get("budget"),
                "worker": check.get("worker")})
    if check.get("problems"):
        notes.append("闸的问题：" + "；".join(check["problems"]))
    for item in check.get("problems") or []:
        blocker("gate_problem", "unknown" if "不可判" in item else "blocked", item,
                "remote_workflow.check")

    declared_resources = (out["plan_meta"] or {}).get("plan_resources") or []
    _gate_checks(ledger, check, injected, bool(allow_fixture_gates), declared_resources,
                 resources)

    # 6) 补三项只读结构检查：worker 收据 / 真正所有权 / 租约现状
    worker_state, worker_reason, worker_evidence = _worker_state(flow)
    out["worker"] = {"ok": worker_state == "pass", "reason": worker_reason,
                     "evidence": worker_evidence}
    ledger.set("worker", worker_state, worker_reason, worker_evidence)
    if worker_state != "pass":
        blocker("worker_not_ready", "blocked" if worker_state == "blocked" else "unknown",
                worker_reason, "provision-receipt")

    ownership_state, ownership_reason, ownership_evidence = _ownership_state(
        db_path, out["plan_id"])
    ledger.set("ownership", ownership_state, ownership_reason, ownership_evidence)
    if ownership_state != "pass":
        blocker("ownership_not_ready",
                "blocked" if ownership_state in ("blocked", "missing") else "unknown",
                ownership_reason, "runtime.backend")

    lease = _lease_view(db_path, resources)
    out["lease"] = lease
    if lease["status"] == "free":
        ledger.pass_("lease", "资源现在空闲（只读检查；真正 claim 在 remote start）",
                     lease.get("details"))
    elif lease["status"] == "held":
        ledger.blocked("lease", "资源租约现在被占用（另一个任务持有）", lease.get("details"))
        blocker("lease_held", "blocked", "资源租约现在被占用："
                + json.dumps(lease.get("details"), ensure_ascii=False), "scheduler.resources")
    elif lease["status"] == "quarantined":
        ledger.unknown("lease", "资源在 quarantine（上一个付费任务结束未经核空闲）",
                       lease.get("details"))
        blocker("lease_quarantined", "unknown", "资源在 quarantine："
                + json.dumps(lease.get("details"), ensure_ascii=False), "scheduler.resources")
    elif lease["status"] == "unconfigured":
        ledger.missing("lease", "没有给资源键：没有资源租约不得提交")
        blocker("lease_unconfigured", "blocked",
                "没有给资源键：没有资源租约不得提交", "progress_remote")
    else:
        ledger.unknown("lease", lease.get("note") or "租约现状不可判")
        blocker("lease_unknown", "unknown", lease.get("note") or "租约现状不可判",
                "scheduler.resources")

    return finish()
