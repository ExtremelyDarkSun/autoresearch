# GENERATED-BY: research execution main chain migration, 2026-11-09
# LAST-EDIT: 2026-11-09，schema_version=2 复盘交接；历史复盘保留 unknown，不改写旧记录
# PURPOSE: 一次研究执行的主链 —— 事前四锚点与冻结、来源哈希过期、统一起跑闸（审计 / 意图 / 监控 / 实际 argv）、
#          append-only 生命周期、真实结束原因与退出码、复盘验收与前轮阻断。本地 runner 只用显式 argv 数组、无 shell。
# ORIGIN: 抽取自 AutoDL实验/tool/ai_work.py（prepare / check_start / start / finish / review 的通用逻辑）
#         与 AutoDL实验/tool/exp_launch.py（先检查后起跑、传递真实退出码）；⛔ 不含专题号、机器、读数、渠道、预算值。
# CUMULATIVE-DELTA: 2026-11-09 增加复盘v2下一步信息与真实判据来源；旧复盘兼容但缺项明确 unknown。
#   首版竞争无关：比赛语义留给 profile 与调用方的 argv/来源清单。
#   2026-11-09：`_ReadOnlyStore` 与 `_read_conn` 不再自己拼 `?mode=ro`，委托 `runtime.store.ReadonlyStore`
#   （WAL 库按 sidecar 实况选 mode=ro / immutable=1，只读不创建 -shm/-wal；-wal 有 -shm 缺判不可判）。

"""研究执行主链（J1–J6 · K1 起跑检查 · I7）。

这一层解决的问题：**「我准备好了、我跑了、我复盘了」都可以口头说，但只有可核的绑定值才算。**
所以每一步都落一条 append-only 记录，并绑一个能重算的哈希（来源文件、计划版本、argv、专题目标）。

一条主链（一个「轮次」= 一个 `plan_id`）
======================================

    prepare  事前材料：plan 校验 → J1 四锚点 + 实现五项/files + 显式 argv → 必要时 freeze_plan
             → 来源清单 / 专题主文档 / 专题目标 / 该会话用户原话 / 旧复盘来源 逐个哈希 → append 记录
    check    只读起跑闸：计划与已冻结版本一致 → 准备记录未过期 → 实际 argv 命中事前声明 →
             前轮无未复盘（J5）→ 未重跑同一轮（J6）→ 审计 audit_state →
             监控（**唯一意图权威**：`Monitoring.check_start`，可注入；未接线即拒）。⛔ 不写盘、⛔ 不起子进程。
    launch   同一套闸；全过才在 **sqlite 事务里原子占用**唯一活跃 run，并在事务内**只读复核**
             准备来源哈希 / 审计状态 / 监控状态（防闸后来源变化、新原话、审计撤销），
             再 subprocess.run(显式 argv, 无 shell)，结束后立即写真实原因与 exitcode。
             闸任一不过 ⇒ 一个子进程也不启动。
    finish   单独登记结束（跑在带外时用）：原因枚举 + 真实 exitcode；文档失败只追加事件，
             ⛔ 不覆盖已记录的 exitcode、⛔ 不阻断安全停。
    review   复盘验收：必须已有结束记录；事前计划未被改写；分支与 next_action 对账；
             证据来源逐个哈希；必须指向专题主文档且内容**实际变更**（判决落账）。
    status   只读投影：准备是否过期 · run 的结束原因/exitcode · 待复盘队列 · 下一轮阻断项。

为什么这几条判据
================

* **J1** 事前四锚点：假设文本、判据及出处（确认性必须给阈值**和**理由；探索性必须给停止条件，
  不强制数字阈值）、实际 argv、实现五项 + `files`；任何一项整段是占位词（`待填` / `TODO` / `<…>`）即拒。
* **J2** 过期按 **sha256** 判，不看 mtime：只 touch 不改内容 ⇒ 不过期；内容一变 ⇒ 过期，重新 prepare。
* **J3** `check` 与 `launch` 都拿**实际 argv** 与事前声明逐项比对（列表相等），不给「省略 argv 就跳过比对」的口子。
* **J4** 复盘必须建立在**本次真实启动记录 + 已结束记录**上；没有 `launched` 就没有「这一轮」。
* **J5** 同专题前轮已结束但未复盘 ⇒ 不许开新轮；被否定过的同一假设重试 ⇒ 必须在计划里写新条件。
* **J6** 同一 `plan_id` 已有启动记录 ⇒ 不能再 prepare / 再启动（不许事后补造事前材料），新轮次用新 `plan_id`。
* **K1（起跑检查部分）** 缺当前审计不启动；`owner_session` / 意图声明 / argv 都**必须显式给**，
  ⛔ 不沿用「省略就默认放行」的旧行为。
* **I7** 结束、失败、停跑、替代都记真实原因与退出码；文档失败单独记一条事件，不改退出码、不阻止停机。

边界（⛔ 不要读大）
==================

1. **不联网、不读凭据、不调用远端执行器。** 本模块只碰工作区里的 sqlite 与文件；`runner` 默认就是
   `subprocess.run(argv, shell=False)`，起什么由调用方的 argv 决定。
2. **不生成人读页、不发审批卡、不核真实宿主独立性。** 页面正文（scribe）与审批通道在别的层。
   审计独立性只有注入 verifier 才可能是 `verified`；没有 verifier 时 `require_verified_independence=True`
   会让 `audit_state()` 直接报问题 ⇒ 不启动（这是有意的 fail-closed）。
3. **不判科学。** 闸绿只说明该走的登记走了，不说明假设成立、也不构成实验授权。
4. **监控是唯一意图权威，生产不设旁路。** 不给 `monitor_check` 且 `require_monitor_check=True` ⇒ 拒绝启动。
   `workflow_cli` 固定接 `Monitoring(workspace, host=DSHHost()).check_start`；⛔ 不提供
   `--allow-unwired-monitor` / `--monitor-module` 这类可跳闸的开关（有就等于生产旁路）。
   监控结果必须带完整协议：`{ok|allow, problems, registered, state, intent_id}`；缺字段即拒，
   ⛔ 不接受 `{"ok": True}` 这种裸放行。`--intent-id` 必须等于监控当前 active/blocked 意图 id，
   `--intent-none` 必须对应 none/closed 且没有当前意图 id。
   旧的 `Governance.check_start` 只保留给兼容 API，**不接生产双重拦截**（否则只按 monitoring
   登记的真实用户永远无法起跑）。只有 Python 测试可以注入夹具回调，并**必须**显式
   `require_monitor_check=False` 标记夹具；夹具照样要给完整协议字段。
5. **argv 不按路径校验。** argv 是「声明过的命令」，控制手段是它与冻结计划里的声明**逐项相等**；
   参与哈希的文件引用（sources / input_files / implementation.files / review evidence）才做路径逃逸检查。
6. **verifier 是对象不是函数。** 传 `DSHHost()` 这类带 `.verify(kind, payload)` 的对象给 Governance；
   没有真实宿主证据时审计只会是 `declared` ⇒ 起跑被拒（fail-closed）。
7. **`event_sink` 只是通知。** 复盘落库后发 `result_analysis` / `evidence` 给注入的 hook
   （例如意图监控）；它失败只追加 `event_sink_error` 事件，⛔ 不回滚复盘、⛔ 不改退出码。
8. **执行准入靠注入，默认 fail-closed。** `authorization_check(plan, owner_session, exp_id)` 是生产预算准入的
   接缝（`admission.Admission(workspace, store=).check`：核 `Accounts` 的 active / expiry / reserved /
   exp / scope / unit ≥ `plan.budget.max_cost_units`，`large` / `submission` 再调 decisions）。
   不给它就**拒绝启动**；⛔ 统一入口不能只看 `plan.execution_argv` 就开远端大题而不看决策。
   计划里声明的授权在顶层 `authorization = {scope, series_id, reservation_id, decision_id}`（本层只记录与绑定）。
9. **预算只做到「单进程墙钟上界」。** `start()` 用 `plan.budget.max_seconds` 作为子进程硬超时
   （`subprocess.run(timeout=...)`，超时即杀并在结束后记 `reason=timeout`）。⚠️ 这**不是**完整预算：
   本层**不**做 `reserve` / `settle`，也**不**取调度器租约；`finish()` **不会**把预留按 0 结算 ——
   真实成本未知时预留一直留着，等有证据再结（`Admission` / `Accounts` 负责）。安全停不受结算阻碍。
   ⇒ 不能宣称「预算完整」。见下面「已知缺口」。

数据落点
========

工作区（`workspace`）内：

    .autoresearch/runtime.sqlite3   与 runtime.Store / Governance / Topics 共用同一个库
    topics/<topic>/TOPIC.md         专题主文档（参与 J2 哈希，路径可用 topic_doc_pattern 改）

本模块自建的 append-only 表（都有 `BEFORE UPDATE/DELETE` 触发器）：

    wf_preparations  准备记录（快照 + 来源指纹 + 声明 argv + 实现声明）
    wf_runs          启动记录（原子占用；DB 触发器保证同专题/同计划只有一个未结束 run）
    wf_finishes      结束记录（原因 + exitcode + 文档状态；一个 run 一条）
    wf_reviews       复盘记录（verdict / branch / next_action / 证据）
    wf_events        生命周期事件流（prepared / launched / finished / documentation_failed / reviewed）

注入点（测试与宿主接线）
========================

    clock          () -> float                     时间戳来源（默认 time.time）
    runner         (argv, cwd, env) -> int | 对象  子进程执行（默认 subprocess.run，无 shell）
    monitor_check  (owner_session, exp_id, action) -> {ok|allow, problems,
                   registered, state, intent_id}
                   ⚠️ 生产由 CLI 固定接 `Monitoring(workspace, host=DSHHost()).check_start`；
                      测试注入夹具时必须 `require_monitor_check=False`，且返回完整字段。
    verifier       传给 Governance 的**对象**（`DSHHost()`），Governance 调 `.verify(kind, payload)`
    event_sink     (kind, payload) | (payload) -> 任意；复盘落库后发 result_analysis / evidence
    gov / store    直接注入已构造的实例

退出码约定（CLI 与 Python 两侧一致）
====================================

    0    通过 / 已登记
    9    规则拒绝（闸不过、前置不全）—— 一定没有启动子进程
    2    输入或数据错误（文件缺失、JSON 坏、run 不存在、工作区未初始化）
    124  子进程超过 `plan.budget.max_seconds` 被终止（已按 timeout 记录）
    launch 通过时 **返回被启动进程的真实退出码**（原值保留，负值表示被信号终止）

已知缺口（⛔ 不要读成已实现）
============================

| 缺口 | 现状 | 下一步接缝 |
|---|---|---|
| **共享预算 reservation / 租约** | `start()` / `finish()` **不**调用 `runtime.accounts.Accounts.reserve/settle`，也不取调度器租约 ⇒ 只保证「子进程墙钟不超 `max_seconds`」，**不是**完整预算；`finish()` **不会**按 0 结算（真实成本未知时预留留着） | `authorization_check`（生产 = `admission.Admission(workspace, store=).check`）先核 `Accounts`；结算由带证据的结账动作完成，不在本层 |
| **大规模 / 远端执行的审批** | 本层只认注入的 `authorization_check`；不判断题量、不判断是不是远端 | `Admission` 读 `plan.authorization.scope`（small/large/submission），`large` / `submission` 调 decisions 的 approved 检查 |
| **计划指纹没有绑 `authorization`** | `governance.KEY_FIELDS` 未含 `authorization` ⇒ 改 scope/series/reservation/decision 不会让审计失效 | 已报给父 agent 把 `authorization` 加进 `KEY_FIELDS`；本层的缓解是**准备快照绑整份冻结计划**（含 `plan_sha256`）⇒ 改了就过期并拒绝起跑，且 `review` 会拒 |
| **整批成本模型** | `budget.max_cost_units` 只作为声明进准入请求，不换算、不累计 | 由 `Accounts` / `Admission` 负责 |
| **cgroup / 资源隔离** | 没有；子进程与调用方同机同权限 | 由部署的 execution engine 负责 |
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # 让 `python kernel/autoresearch/workflow_cli.py` 也能跑
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch.governance import (  # noqa: E402  (先补 sys.path 再导入)
    Governance,
    GovernanceError,
    validate_plan,
)
from kernel.autoresearch.review_continuation import project as project_continuation, validate as validate_continuation
from kernel.autoresearch.execution_target import ExecutionTarget  # noqa: E402
from kernel.autoresearch.runtime.store import ReadonlyStore, Store, canonical_json  # noqa: E402
from kernel.autoresearch.runtime.topics import Topics, TopicsError  # noqa: E402

__all__ = (
    "Workflow",
    "WorkflowError",
    "default_runner",
    "local_execution_policy",
    "FINISH_REASONS",
    "IMPL_FIELDS",
    "REVIEW_BRANCHES",
    "REVIEW_VERDICTS",
)

#: 结束原因。**只认这些**，避免「完事了」这类没法对账的措辞。
FINISH_REASONS = (
    "completed",
    "failed",
    "interrupted",
    "stopped_by_user",
    "launch_error",
    "timeout",
    "superseded",
)

#: 实现声明五项（J1）。`files` 单独查，因为要逐个核存在与哈希。
IMPL_FIELDS = ("expected_behavior", "load_path", "trigger", "observation", "non_activation")

REVIEW_VERDICTS = ("supported", "rejected", "inconclusive")
REVIEW_BRANCHES = ("support", "contradict", "inconclusive")

#: 写「没有偏离」时可用的词。写了别的就得说明偏离原因。
NO_DEVIATION = ("没有", "无", "none", "无偏离", "没有偏离")

#: 整段就是占位 ⇒ 判「没填」。只做整段匹配，免得把正常句子里的词判成占位。
_PLACEHOLDER = re.compile(r"^\s*(?:待填|待补|缺失|todo|tbd|fixme|xxx|n/a|\.\.\.|…)\s*[:：]?\s*$", re.I)
_ANGLE_PLACEHOLDER = re.compile(r"^<[^<>]+>$")
_LINE_LOCATOR = re.compile(r"^L([1-9]\d*)(?:-L?([1-9]\d*))?$")
_ABS_WINDOWS = re.compile(r"^[A-Za-z]:[\\/]")

#: `non_activation` 里不许出现的空话（源契约 §5 点名的那句）。
_PASSIVE_NON_ACTIVATION = ("没有证据说明没生效", "无反向证据", "没有反向证据", "无法判断是否生效")

_DDL = (
    "CREATE TABLE IF NOT EXISTS wf_preparations(\n"
    " prepared_id TEXT PRIMARY KEY,\n"
    " plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL,\n"
    " topic TEXT NOT NULL, owner_session TEXT NOT NULL,\n"
    " key_fingerprint TEXT NOT NULL, plan_sha256 TEXT NOT NULL,\n"
    " plan_source TEXT NOT NULL, argv_json TEXT NOT NULL,\n"
    " implementation_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,\n"
    " source_fingerprint TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS wf_preparations_plan ON wf_preparations(plan_id, created_at)",
    "CREATE INDEX IF NOT EXISTS wf_preparations_topic ON wf_preparations(topic, created_at)",
    "CREATE TABLE IF NOT EXISTS wf_runs(\n"
    " run_id TEXT PRIMARY KEY,\n"
    " prepared_id TEXT NOT NULL REFERENCES wf_preparations(prepared_id),\n"
    " plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL,\n"
    " topic TEXT NOT NULL, owner_session TEXT NOT NULL,\n"
    " hypothesis_id TEXT, hypothesis_text TEXT,\n"
    " argv_json TEXT NOT NULL, cwd TEXT NOT NULL, gates_json TEXT NOT NULL,\n"
    " source_fingerprint TEXT NOT NULL, plan_sha256 TEXT NOT NULL,\n"
    " started_at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS wf_runs_topic ON wf_runs(topic, started_at)",
    "CREATE TABLE IF NOT EXISTS wf_finishes(\n"
    " run_id TEXT PRIMARY KEY REFERENCES wf_runs(run_id),\n"
    " reason TEXT NOT NULL, exitcode INTEGER, signal TEXT,\n"
    " documentation_status TEXT NOT NULL, documentation_detail TEXT NOT NULL,\n"
    " finished_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS wf_reviews(\n"
    " run_id TEXT PRIMARY KEY REFERENCES wf_runs(run_id),\n"
    " plan_id TEXT NOT NULL, topic TEXT NOT NULL,\n"
    " hypothesis_id TEXT, hypothesis_text TEXT,\n"
    " verdict TEXT NOT NULL, branch TEXT NOT NULL, next_action TEXT NOT NULL,\n"
    " record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL,\n"
    " evidence_json TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS wf_reviews_topic ON wf_reviews(topic, created_at)",
    "CREATE TABLE IF NOT EXISTS wf_events(\n"
    " id INTEGER PRIMARY KEY, kind TEXT NOT NULL, topic TEXT NOT NULL,\n"
    " plan_id TEXT, prepared_id TEXT, run_id TEXT,\n"
    " payload_json TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
)

_APPEND_ONLY_TABLES = ("wf_preparations", "wf_runs", "wf_finishes", "wf_reviews", "wf_events")

#: 监控状态枚举（`Monitoring.check_start` 的协议字段；gate 只认这些）。
MONITOR_STATES = ("none", "active", "blocked", "closed")


class _ReadOnlyStore:
    """只读 sqlite 事务（不建表、不 `BEGIN IMMEDIATE`）。

    两个用途：
    1. `readonly=True` 的 `check/status` —— 不因为「读一下」就建库建表；
    2. `start()` 的占用事务里复核审计状态 —— 占用事务已经拿着写锁，复核必须是**只读**的
       另一个连接，⛔ 不能在事务里再开写事务（会等锁）。

    **连接方式委托 `runtime.store.ReadonlyStore`**（本轮只读入口的统一实现）：
    它读 SQLite 头里的日志模式与 `-wal` / `-shm` 实况再选路 —— 缺 sidecar 的 WAL 库用
    `immutable=1`（`mode=ro` 会创建 `-shm` / `-wal`），有 `-wal` 无 `-shm` 直接判不可判。
    ⛔ 本类不再自己拼 `?mode=ro` URI，也不在这里复制数据库。
    """

    def __init__(self, db_path):
        self._store = ReadonlyStore(db_path)
        self.db_path = Path(self._store.db_path)

    def connect(self):
        return self._store.connect()

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()


def _readonly_governance(db_path, *, verifier=None, policy=None):
    """不跑 `Governance.__init__`（它会建表）的只读实例：只调只读投影方法。"""
    gov = object.__new__(Governance)
    gov.store = _ReadOnlyStore(db_path)
    gov.verifier = verifier
    gov.experiment_id = None
    gov.policy = dict(policy or Governance.DEFAULT_POLICY)
    return gov


#: 一个专题 / 一个计划同时只能有一个未结束的 run。**在数据库层保证**，
#: 不只靠调用方自觉：两个进程同时 launch 时，第二个的 INSERT 会被触发器打断。
_CLAIM_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS wf_runs_one_active_topic BEFORE INSERT ON wf_runs\n"
    "WHEN EXISTS(SELECT 1 FROM wf_runs r WHERE r.topic = NEW.topic\n"
    "            AND NOT EXISTS(SELECT 1 FROM wf_finishes f WHERE f.run_id = r.run_id))\n"
    "BEGIN SELECT RAISE(ABORT, 'workflow: topic already has an unfinished run'); END",
    "CREATE TRIGGER IF NOT EXISTS wf_runs_one_run_per_plan BEFORE INSERT ON wf_runs\n"
    "WHEN EXISTS(SELECT 1 FROM wf_runs r WHERE r.plan_id = NEW.plan_id)\n"
    "BEGIN SELECT RAISE(ABORT, 'workflow: plan already has a run (open a new round)'); END",
)


class WorkflowError(ValueError):
    """研究执行主链拒绝执行。`errors` 带逐条原因，`code` 是 CLI 退出码（9 规则拒绝 / 2 输入错误）。"""

    def __init__(self, message, errors=None, code=9):
        super().__init__(message)
        self.errors = list(errors or [])
        self.code = 9 if code == 9 else 2


# ---------------------------------------------------------------------------
# 纯函数：填没填、是不是数、哈希、locator
# ---------------------------------------------------------------------------


def _filled(value):
    """「填了真内容没有」。整段是占位 / 空 / 纯尖括号占位 ⇒ False。"""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if _ANGLE_PLACEHOLDER.match(stripped) or _PLACEHOLDER.match(stripped):
        return False
    return True


def _is_number(value):
    """有限数值，且布尔不算数（`True` 不能当 1 用）。"""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    try:
        return abs(float(value)) != float("inf") and float(value) == float(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path):
    return _sha_bytes(Path(path).read_bytes())


def _sha_text(text):
    return _sha_bytes(text.encode("utf-8"))


def _locator_ok(text, locator):
    """locator 要么是行号范围 `L1` / `L1-L9`，要么是原文里真实存在的片段。"""
    match = _LINE_LOCATOR.match(str(locator).strip())
    if match:
        lines = text.splitlines()
        first = int(match.group(1))
        last = int(match.group(2) or first)
        return 1 <= first <= last <= len(lines)
    return str(locator) in text


def _criteria_reason(criteria):
    """确认性判据的「理由」：接受 rationale / reason / basis / why 四种写法。"""
    for key in ("rationale", "reason", "basis", "why"):
        if _filled(criteria.get(key)):
            return str(criteria.get(key))
    return None


def _is_no_deviation(text):
    return isinstance(text, str) and text.strip().lower() in {x.lower() for x in NO_DEVIATION}


def _iso(timestamp):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(timestamp)))
    except (TypeError, ValueError, OSError):
        return None


def default_runner(argv, cwd, env, timeout=None):
    """默认执行器：显式 argv 数组、`shell=False`、固定 cwd、继承 stdout/stderr。

    `timeout` 来自 `plan.budget.max_seconds`（调用方传）：超时由 `subprocess.run` 杀掉子进程
    并抛 `TimeoutExpired`，由 `start()` 记成 `reason=timeout`。
    """
    result = subprocess.run([str(item) for item in argv], cwd=str(cwd), env=dict(env),
                            shell=False, stdin=subprocess.DEVNULL, timeout=timeout)
    return result.returncode


def local_execution_policy(*, authorized_by, max_seconds, max_cost_units,
                           engine="local-synthetic", note=""):
    """部署侧**显式**授权「本地合成执行」的准入器。**不是**跳过闸的开关。

    ⚠️ 生产预算准入用 `admission.Admission(workspace, store=).check`（核 `Accounts` 的
    active / expiry / reserved / exp / scope / unit，`large` / `submission` 再调 decisions）。
    这个函数只是**测试与本地合成引擎**的显式策略，别拿它顶替 Admission。

    它只放行与这份 policy 明确匹配的请求（协议同 `authorization_check(plan, owner_session, exp_id)`）：
      * `authorized_by` 必填 —— 谁在部署配置里授权的，会随闸记录一起落盘；
      * 计划预算必须在这份额度内（`max_seconds` / `max_cost_units`）；
      * ⛔ 它**不**判断远端 / 大规模：那种执行必须接 `Admission`。这份 policy 的适用范围
        就是「本地合成 engine」，写进返回值的 `engine` 字段。
    """
    if not isinstance(authorized_by, str) or not authorized_by.strip():
        raise ValueError("local_execution_policy 需要 authorized_by：谁在部署配置里授权了本地执行")
    if not _is_number(max_seconds) or max_seconds <= 0:
        raise ValueError("local_execution_policy 需要正的 max_seconds 额度")
    if not _is_number(max_cost_units) or max_cost_units < 0:
        raise ValueError("local_execution_policy 需要非负的 max_cost_units 额度")

    def check(plan, owner_session, exp_id):
        budget = plan.get("budget") if isinstance(plan, dict) else None
        budget = budget if isinstance(budget, dict) else {}
        problems = []
        seconds = budget.get("max_seconds")
        if not _is_number(seconds):
            problems.append("计划缺 budget.max_seconds：本地执行策略无法判定时长上界")
        elif float(seconds) > float(max_seconds):
            problems.append(f"计划 budget.max_seconds={seconds} 超过本地执行额度 {max_seconds}")
        units = budget.get("max_cost_units")
        if not _is_number(units):
            problems.append("计划缺 budget.max_cost_units")
        elif float(units) > float(max_cost_units):
            problems.append(f"计划 budget.max_cost_units={units} 超过本地执行额度 {max_cost_units}")
        return {"ok": not problems, "problems": problems, "decision": "local-synthetic",
                "scope": "local", "engine": engine, "authorized_by": authorized_by,
                "owner_session": owner_session, "exp_id": exp_id, "note": note}

    return check


# ---------------------------------------------------------------------------
# 主链
# ---------------------------------------------------------------------------


class Workflow:
    """一次研究执行的主链对象。一个工作区一个实例（多进程安全靠 sqlite 事务 + 触发器）。"""

    def __init__(self, workspace, *, db=None, gov=None, store=None, verifier=None,
                 require_verified_independence=True, monitor_check=None,
                 require_monitor_check=True, authorization_check=None,
                 require_authorization_check=True, require_authorization=None,
                 event_sink=None, clock=None, runner=None,
                 environ=None, readonly=False, topic_doc_pattern="topics/{topic}/TOPIC.md",
                 execution_target=None, lifecycle=None):
        if not isinstance(workspace, (str, Path)):
            raise WorkflowError("workspace 必须是路径", code=2)
        self.root = Path(workspace).resolve()
        if not self.root.is_dir():
            raise WorkflowError(f"工作区不存在：{self.root}", code=2)
        self.db = Path(db).resolve() if db is not None else (self.root / ".autoresearch" / "runtime.sqlite3")
        self.readonly = bool(readonly)
        self.clock = clock or time.time
        self.runner = runner or default_runner
        self.environ = dict(os.environ if environ is None else environ)
        self.monitor_check = monitor_check
        self.require_monitor_check = bool(require_monitor_check)
        # ⚠️ `monitor_check=None` + `require_monitor_check=False` 只是**测试夹具**的写法，
        # `wf_runs.gates_json.monitor.fixture` 会记 True。生产入口（workflow_cli）固定接
        # `Monitoring(workspace, host=DSHHost()).check_start`，⛔ 没有跳过闸的开关。
        self.authorization_check = authorization_check
        if require_authorization is not None:  # 旧参数名兼容；新代码用 require_authorization_check
            require_authorization_check = require_authorization
        self.require_authorization_check = bool(require_authorization_check)
        self.event_sink = event_sink
        self.require_verified_independence = bool(require_verified_independence)
        self.topic_doc_pattern = str(topic_doc_pattern)
        if not self.topic_doc_pattern.count("{topic}"):
            raise WorkflowError("topic_doc_pattern 必须含 {topic} 占位", code=2)
        #: 执行落点：默认库 API 允许未 provision 的 legacy 根目录夹具；生产 CLI 固定
        #: `ExecutionTarget(workspace, require_provision=True)`（没有真实独立环境就不放行）。
        self.execution_target = execution_target or ExecutionTarget(self.root, require_provision=False)
        #: 生命周期投影（AI_WORK / 前后快照 / reporting 事件）。它是**通知/投影**，失败只记诊断，
        #: ⛔ 不阻断 prepare/start/finish/review，也不改退出码。
        self.lifecycle = lifecycle

        self.store = store
        self.gov = gov
        self.topics = None
        if not self.readonly:
            self.db.parent.mkdir(parents=True, exist_ok=True)
            if self.store is None:
                self.store = Store(self.db)
            if self.gov is None:
                self.gov = Governance(
                    self.store,
                    verifier=verifier,
                    policy={"require_verified_independence": self.require_verified_independence},
                )
            self.topics = Topics(self.store)
            with self.store.transaction() as conn:
                for statement in _DDL:
                    conn.execute(statement)
                for statement in _CLAIM_TRIGGERS:
                    conn.execute(statement)
                for table in _APPEND_ONLY_TABLES:
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END")
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END")
        elif self.db.is_file():
            self.topics = Topics(str(self.db), readonly=True)
            # 只读 `check` 也要跑同一套闸（计划 / 准备 / 审计 / 监控）：给它只读的 store 与 gov，
            # ⛔ 不建表、不写一个字节（`Governance.__init__` 会建表，所以这里绕过它）。
            if self.store is None:
                self.store = _ReadOnlyStore(self.db)
            if self.gov is None:
                self.gov = _readonly_governance(self.db, verifier=verifier)
        self._gov_ro = None

    # -- 基础设施 -----------------------------------------------------------

    def _now(self):
        value = self.clock()
        if not _is_number(value):
            raise WorkflowError("clock() 必须返回有限数值时间戳", code=2)
        return float(value)

    def _require_write(self):
        if self.readonly:
            raise WorkflowError("执行主链处于只读模式：prepare / check / launch / finish / review 需要可写工作区", code=2)

    @contextmanager
    def _read_conn(self):
        """只读连接：每句自动提交，不长期持读锁（别的进程还要写）。

        **连接方式委托 `runtime.store.ReadonlyStore`**：WAL 库按 sidecar 实况选
        `mode=ro` / `immutable=1`，读操作不创建 `-shm` / `-wal`；缺 `-wal` 有 `-shm`
        的歧义态抛 `StoreError`（调用方按「不可判」处理）。
        """
        if not self.db.is_file():
            raise WorkflowError("工作区数据库不存在：先 prepare 或运行 init", code=2)
        conn = ReadonlyStore(self.db).connect()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _table_exists(conn, name):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    @staticmethod
    def _one(conn, sql, args=()):
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row is not None else None

    def _all(self, conn, table, sql, args=()):
        """读多行；表不存在时返回空（只读诊断要能在半初始化的库上跑）。"""
        if not self._table_exists(conn, table):
            return []
        return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def _one_in(self, conn, table, sql, args=()):
        """读一行；表不存在时返回 None。"""
        if not self._table_exists(conn, table):
            return None
        return self._one(conn, sql, args)

    def _event(self, conn, kind, *, topic, plan_id=None, prepared_id=None, run_id=None,
               payload=None, actor="workflow"):
        conn.execute(
            "INSERT INTO wf_events(kind,topic,plan_id,prepared_id,run_id,payload_json,actor,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (kind, topic, plan_id, prepared_id, run_id,
             canonical_json(dict(payload or {})), actor, self._now()))

    # -- 路径与文件引用（防逃逸） -------------------------------------------

    def _resolve(self, value, label):
        """工作区内相对路径 → 绝对路径。绝对路径 / `..` / symlink 逃逸一律拒。"""
        if isinstance(value, Path):
            value = str(value)
        if not isinstance(value, str) or not value.strip():
            raise WorkflowError(f"{label} 的路径必填", code=2)
        raw = value.strip()
        if raw.startswith("\\\\") or _ABS_WINDOWS.match(raw) or Path(raw).is_absolute():
            raise WorkflowError(f"{label} 必须是工作区内的相对路径（不接受绝对路径）：{raw}", code=2)
        parts = Path(raw.replace("\\", "/")).parts
        if ".." in parts:
            raise WorkflowError(f"{label} 的路径不许含 '..'（可能越出工作区）：{raw}", code=2)
        target = (self.root / raw).resolve()
        if target == self.root or not target.is_relative_to(self.root):
            raise WorkflowError(f"{label} 的路径越出工作区：{raw}", code=2)
        return target

    def _rel(self, path):
        return Path(path).resolve().relative_to(self.root).as_posix()

    def _file_ref(self, ref, label):
        """把 `{path, locator}` / `路径字符串` 核成一个带哈希的来源引用。"""
        if isinstance(ref, str):
            path_text, locator = ref, None
        elif isinstance(ref, dict):
            path_text, locator = ref.get("path"), ref.get("locator")
            if not _filled(path_text):
                raise WorkflowError(f"{label} 的 path 必填", code=2)
            if not _filled(locator):
                raise WorkflowError(f"{label} 的 locator 必填（行号 L1-L9 或原文片段）", code=2)
        else:
            raise WorkflowError(f"{label} 必须是路径或 {{path, locator}}，实际 {type(ref).__name__}", code=2)
        path = self._resolve(path_text, label)
        if not path.is_file():
            raise WorkflowError(f"{label} 指向的文件不存在：{path_text}", code=2)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowError(f"{label} 必须是 UTF-8 文本：{path_text}（{exc}）", code=2) from None
        if locator is None:
            locator = f"L1-L{max(len(text.splitlines()), 1)}"
        elif not _locator_ok(text, locator):
            raise WorkflowError(f"{label} 的 locator 定位不到：{path_text} {locator}", code=2)
        return {"path": self._rel(path), "locator": str(locator), "sha256": _sha_file(path)}

    # -- 计划 ---------------------------------------------------------------

    def _load_plan(self, plan):
        """读计划：工作区内相对路径 或 内存 dict。返回 (对象, 来源描述, 文件字节哈希 or None)。"""
        if isinstance(plan, dict):
            return json.loads(json.dumps(plan)), "<memory>", None
        path = self._resolve(plan, "plan")
        if not path.is_file():
            raise WorkflowError(f"计划文件不存在：{plan}", code=2)
        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"计划不是合法 JSON：{exc}", code=2) from None
        if not isinstance(payload, dict):
            raise WorkflowError("计划必须是 JSON 对象", code=2)
        return payload, self._rel(path), _sha_bytes(raw)

    def _plan_checks(self, plan):
        """J1 四锚点 + 实现五项/files + 显式 argv。返回 (problems, argv 变体, 归一实现, 假设)。"""
        problems = []
        hypothesis = {"id": None, "text": None}
        raw_hypothesis = plan.get("hypothesis")
        if isinstance(raw_hypothesis, dict):
            hypothesis["id"] = raw_hypothesis.get("id")
            hypothesis["text"] = raw_hypothesis.get("text")
            if not _filled(hypothesis["id"]):
                problems.append("hypothesis.id 必填（这一轮改动的归属键）")
            if not _filled(hypothesis["text"]):
                problems.append("hypothesis.text 必须是一句可被否掉的话，不能是占位")
        elif isinstance(raw_hypothesis, str):
            hypothesis["text"] = raw_hypothesis
            if not _filled(raw_hypothesis):
                problems.append("hypothesis 必填且不能是占位词")
        else:
            problems.append("hypothesis 必须是字符串或 {id, text} 对象")

        criteria = plan.get("criteria")
        if not isinstance(criteria, dict):
            problems.append("criteria 必须是对象（观察什么、怎么判、判据从哪来）")
        else:
            mode = criteria.get("mode", "confirmatory")
            if mode not in ("confirmatory", "exploratory"):
                problems.append("criteria.mode 必须是 confirmatory 或 exploratory")
            if not _filled(criteria.get("metric")):
                problems.append("criteria.metric 必填：这一轮要读的那个量")
            if not _filled(criteria.get("source")):
                problems.append("criteria.source 必填：判据 / 门槛的出处（哪次读数、哪条规则、哪份契约）")
            if mode == "confirmatory":
                if criteria.get("direction") not in ("increase", "decrease"):
                    problems.append("确认性判据必须写 criteria.direction（increase / decrease）")
                if not _is_number(criteria.get("threshold")):
                    problems.append("确认性判据必须有有限数值 criteria.threshold（布尔 / NaN / 缺失都拒）")
                if not _criteria_reason(criteria):
                    problems.append("确认性判据必须有理由：criteria.rationale（阈值为什么是它、在什么条件下成立）")
            else:
                for key in ("observation", "interpretation_boundary", "conditions", "stop_condition"):
                    if not _filled(criteria.get(key)):
                        problems.append(f"探索性判据必须写 criteria.{key}")
                if criteria.get("threshold") is not None and not _is_number(criteria.get("threshold")):
                    problems.append("criteria.threshold 写了就必须是有限数值（探索性可以不写）")

        implementation = None
        raw_impl = plan.get("implementation")
        if not isinstance(raw_impl, dict):
            hint = ""
            if isinstance(plan.get("research"), dict) and isinstance(plan["research"].get("implementation"), dict):
                hint = "（本仓库契约要求放在计划**顶层** implementation，与 governance.plan_key_fields 一致）"
            problems.append("缺顶层 implementation：默认必审的「实现声明」五项 + files 每轮都要" + hint)
        else:
            for key in IMPL_FIELDS:
                if not _filled(raw_impl.get(key)):
                    problems.append(f"implementation.{key} 必填且不能是占位词")
            for phrase in _PASSIVE_NON_ACTIVATION:
                if phrase in str(raw_impl.get("non_activation") or ""):
                    problems.append(
                        "implementation.non_activation 不许写「没有反向证据」这类空话：要写没生效时能看到什么")
                    break
            files = raw_impl.get("files")
            if not isinstance(files, list) or not files:
                problems.append("implementation.files 必须是非空清单（实际改动、加载入口、运行配置）")
            else:
                normalized = []
                for index, ref in enumerate(files):
                    try:
                        normalized.append(self._file_ref(ref, f"implementation.files[{index}]"))
                    except WorkflowError as exc:
                        problems.append(str(exc))
                if not problems or len(normalized) == len(files):
                    implementation = {key: raw_impl.get(key) for key in IMPL_FIELDS}
                    implementation["files"] = normalized

        argv_variants = []
        raw_argv = plan.get("execution_argv")
        if isinstance(raw_argv, str) or not isinstance(raw_argv, list) or not raw_argv:
            problems.append("execution_argv 必须是显式 argv 数组（或多组数组），⛔ 不接受字符串命令行")
        else:
            groups = raw_argv if isinstance(raw_argv[0], list) else [raw_argv]
            for index, group in enumerate(groups):
                if not isinstance(group, list) or not group \
                        or any(not isinstance(item, str) or not item.strip() for item in group):
                    problems.append(f"execution_argv[{index}] 必须是全部为非空字符串的数组")
                    continue
                argv_variants.append(list(group))
        return problems, argv_variants, implementation, hypothesis

    # -- 专题 ---------------------------------------------------------------

    def _topic_row(self, conn, topic, *, prefer_topics=True):
        if prefer_topics and self.topics is not None:
            try:
                row = self.topics.get_topic(topic)
            except (sqlite3.Error, TopicsError):
                row = None
            if row is not None:
                return row
        if not self._table_exists(conn, "topics"):
            return None
        return self._one(conn, "SELECT id,title,objective,completion_criteria,status,version FROM topics WHERE id=?",
                         (topic,))

    def _topic_doc(self, topic):
        pattern = self.topic_doc_pattern.format(topic=topic)
        path = self._resolve(pattern, "专题主文档")
        if not path.is_file():
            raise WorkflowError(f"缺专题主文档：{pattern}（J2 要求主文档参与来源哈希，不能空着）")
        return {"path": self._rel(path), "sha256": _sha_file(path)}

    def _require_topic(self, conn, topic):
        row = self._topic_row(conn, topic)
        if row is None:
            raise WorkflowError(f"专题未登记：{topic}（先用 topics create 写目标与完成条件）")
        if row.get("status") != "active":
            raise WorkflowError(f"专题 {topic} 的状态是 {row.get('status')}：只有 active 才能开新轮")
        return row

    # -- 来源清单与快照 -----------------------------------------------------

    def _source_refs(self, sources, source_file):
        refs = list(sources or ())
        if source_file is not None:
            path = self._resolve(source_file, "source-file")
            if not path.is_file():
                raise WorkflowError(f"来源清单文件不存在：{source_file}", code=2)
            try:
                payload = json.loads(path.read_bytes().decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkflowError(f"来源清单不是合法 JSON：{exc}", code=2) from None
            if not isinstance(payload, list):
                raise WorkflowError("来源清单文件必须是 JSON 数组（元素是路径或 {path, locator}）", code=2)
            refs.extend(payload)
        return refs

    def _source_manifest(self, plan, implementation, extra_refs, topic_doc, topic=None):
        """把本轮依据的文件摊成 {相对路径: sha256}。J2 的过期判据就是它 + 专题目标/原话。

        ⚠️ `topics/<topic>/provision.json`（真实独立代码/环境落点）也进清单：
        改落点或重建环境会让旧 prepare 过期，⛔ 不能拿旧事前的哈希去新环境起跑。
        """
        manifest = {topic_doc["path"]: topic_doc["sha256"]}
        if topic:
            provision = self.root / "topics" / str(topic) / "provision.json"
            if provision.is_file():
                manifest[self._rel(provision)] = _sha_file(provision)
        for ref in (plan.get("input_files") or []):
            try:
                info = self._file_ref(ref, "input_files")
            except WorkflowError as exc:
                raise WorkflowError(f"计划的 input_files 有不可用引用：{exc}", [str(exc)], code=2) from None
            manifest[info["path"]] = info["sha256"]
        for ref in ((implementation or {}).get("files") or []):
            manifest[ref["path"]] = ref["sha256"]
        for index, ref in enumerate(extra_refs):
            try:
                info = self._file_ref(ref, f"sources[{index}]")
            except WorkflowError as exc:
                raise WorkflowError(f"来源清单有不可用引用：{exc}", [str(exc)], code=2) from None
            manifest[info["path"]] = info["sha256"]
        return manifest

    def _user_updates(self, conn, owner_session):
        if not self._table_exists(conn, "gov_user_updates"):
            return {}
        return {str(row["seq"]): _sha_text(row["quote"])
                for row in conn.execute(
                    "SELECT seq,quote FROM gov_user_updates WHERE source_session=? ORDER BY seq",
                    (owner_session,))}

    def _review_sources(self, conn, topic):
        manifest = {}
        for row in self._all(conn, "wf_reviews", "SELECT evidence_json FROM wf_reviews WHERE topic=?",
                             (topic,)):
            for ref in json.loads(row["evidence_json"]):
                manifest[ref["path"]] = ref["sha256"]
        return manifest

    def _file_hash_problems(self, rel, expected, label):
        if not rel or not expected:
            return []
        try:
            path = self._resolve(rel, label)
        except WorkflowError:
            return [f"{label}路径不再合法：{rel}"]
        if not path.is_file():
            return [f"{label}已不存在：{rel}"]
        if _sha_file(path) != expected:
            return [f"{label}内容变了：{rel}（J2 按哈希判过期，不看 mtime）"]
        return []

    def _expiry_problems(self, prep, conn, *, prefer_topics=True):
        """准备记录是否过期。**只按哈希判**：touch / 改 mtime 不失效；内容一变就失效。"""
        try:
            snapshot = json.loads(prep["snapshot_json"])
        except (TypeError, json.JSONDecodeError):
            return ["准备记录损坏（snapshot 读不出）⇒ 重新 prepare"]
        problems = []
        plan_snapshot = snapshot.get("plan") or {}
        latest = None
        if self._table_exists(conn, "gov_plans"):
            latest = self._one(
                conn, "SELECT version,key_fingerprint,plan_json FROM gov_plans WHERE plan_id=?"
                      " ORDER BY version DESC LIMIT 1", (prep["plan_id"],))
        if latest is None:
            problems.append("计划的已冻结版本已不在治理库 ⇒ 事前材料过期，重新 prepare")
        else:
            current = _sha_text(canonical_json(json.loads(latest["plan_json"])))
            if int(latest["version"]) != int(plan_snapshot.get("version", -1)) \
                    or current != plan_snapshot.get("plan_sha256"):
                problems.append("计划在 prepare 之后被改动或重新冻结（J2）⇒ 事前材料过期，重新 prepare")
        for rel, expected in sorted((snapshot.get("sources") or {}).items()):
            problems.extend(self._file_hash_problems(rel, expected, "来源"))
        for rel, expected in sorted((snapshot.get("review_sources") or {}).items()):
            problems.extend(self._file_hash_problems(rel, expected, "旧复盘来源"))
        topic_snapshot = snapshot.get("topic") or {}
        row = self._topic_row(conn, prep["topic"], prefer_topics=prefer_topics)
        if row is None:
            problems.append(f"专题 {prep['topic']} 已不在专题库 ⇒ 事前材料过期")
        elif (row.get("objective") or "") != (topic_snapshot.get("objective") or ""):
            problems.append("专题目标在 prepare 之后变了（J2）⇒ 事前材料过期")
        doc = topic_snapshot.get("doc") or {}
        problems.extend(self._file_hash_problems(doc.get("path"), doc.get("sha256"), "专题主文档"))
        if self._user_updates(conn, prep["owner_session"]) != (snapshot.get("user_updates") or {}):
            problems.append("该会话的用户原话在 prepare 之后变了（新增或改写，J2）⇒ 事前材料过期")
        return problems

    # -- 前轮与重试（J5 / J6） ------------------------------------------------

    def _predecessor_problems(self, conn, topic, plan, hypothesis):
        problems = []
        for run in self._all(conn, "wf_runs", "SELECT * FROM wf_runs WHERE topic=? ORDER BY started_at",
                             (topic,)):
            finish = self._one(conn, "SELECT * FROM wf_finishes WHERE run_id=?", (run["run_id"],))
            review = self._one(conn, "SELECT * FROM wf_reviews WHERE run_id=?", (run["run_id"],))
            if run["plan_id"] == plan.get("id"):
                problems.append(
                    f"本轮 plan {run['plan_id']} 已有实际启动记录 {run['run_id']}："
                    "不能重跑同一轮，也不能补造 / 改写事前材料（J6）⇒ 新设计请开新轮次（新 plan id）")
                continue
            if finish is None:
                problems.append(
                    f"同专题有未结束运行 {run['run_id']}（plan {run['plan_id']}）：不能并发开新轮")
            elif review is None:
                problems.append(
                    f"同专题前轮 {run['run_id']}（plan {run['plan_id']}）已结束但未复盘（{finish['reason']}）："
                    "先 review 写回判决与下一步，或写明停止结果（J5）")
        for review in self._all(conn, "wf_reviews",
                                "SELECT * FROM wf_reviews WHERE topic=? AND verdict='rejected' ORDER BY created_at",
                                (topic,)):
            same = (hypothesis.get("id") and review.get("hypothesis_id") == hypothesis.get("id")) \
                or (hypothesis.get("text") and review.get("hypothesis_text") == hypothesis.get("text"))
            if not same:
                continue
            label = hypothesis.get("id") or (hypothesis.get("text") or "")[:24]
            retry = plan.get("retry")
            if not isinstance(retry, dict):
                problems.append(
                    f"假设 {label} 在 {review['run_id']} 已被否定：重试必须在计划里写 "
                    "retry = {'of': <run/plan/假设>, 'new_condition': 这次不同的条件, 'sources': [...]}（J5）")
                continue
            if not _filled(retry.get("new_condition")):
                problems.append("retry.new_condition 必填：写清这次与前次不同的条件，不能是「再跑一次」")
            refs = retry.get("sources")
            if not isinstance(refs, list) or not refs:
                problems.append("retry.sources 必须是非空来源清单：新条件的依据")
            else:
                for index, ref in enumerate(refs):
                    try:
                        self._file_ref(ref, f"retry.sources[{index}]")
                    except WorkflowError as exc:
                        problems.append(str(exc))
        return problems

    # -- 冻结 ---------------------------------------------------------------

    def _ensure_frozen(self, plan, actor):
        """计划内容与最新冻结版本逐字节（canonical）一致就复用，否则冻结新版本。

        冻结放在闸外做：`check` 是只读的，⛔ 不在这里写盘。
        """
        plan_id = plan["id"]
        current = canonical_json(plan)
        latest = self.gov.latest_plan(plan_id)
        if latest is not None and canonical_json(latest["plan"]) == current:
            return {"version": latest["version"], "key_fingerprint": latest["key_fingerprint"],
                    "plan_sha256": _sha_text(current), "refroze": False, "reaudit_required": False}
        try:
            frozen = self.gov.freeze_plan(plan, actor=actor)
        except GovernanceError as exc:
            raise WorkflowError("冻结计划失败：" + str(exc), getattr(exc, "errors", []), code=2) from None
        latest = self.gov.latest_plan(plan_id)
        return {"version": latest["version"], "key_fingerprint": latest["key_fingerprint"],
                "plan_sha256": _sha_text(canonical_json(latest["plan"])),
                "refroze": True, "reaudit_required": frozen["reaudit_required"]}

    # -- 起跑闸 -------------------------------------------------------------

    def _monitor_gate(self, owner_session, plan_id, action, *, intent_id=None, intent_none=False):
        """监控检查：唯一意图权威，可注入。⛔ 没接线就放行等于假绿，所以默认 fail-closed。

        生产接线：`Monitoring(workspace, host=DSHHost()).check_start`（由 `workflow_cli` 固定接，
        ⛔ 不提供任何跳过开关）。**完整适配器协议**（缺字段即拒，⛔ 不接受 `{"ok": True}` 这种裸放行）：

            {ok|allow: bool, problems: [...], registered: bool,
             state: "none"|"active"|"blocked"|"closed", intent_id: str|null}

        闸还会核声明与真实状态一致：`--intent-id` 必须等于监控当前 active/blocked 意图 id；
        `--intent-none` 必须是 none/closed 且没有当前意图 id。
        `require_monitor_check=False` 只是**夹具标记**，不会豁免上面的协议字段；
        `monitor_check=None` + 显式 False 才是「明确未接线」的测试夹具。
        """
        fixture = not self.require_monitor_check
        if self.monitor_check is None:
            if self.require_monitor_check:
                return {"ok": False, "unwired": True, "fixture": fixture,
                        "problems": ["监控检查未接线（monitor_check=None）：未接线不放行；"
                                     "生产入口必须接 Monitoring.check_start。测试若注入夹具，"
                                     "必须显式 require_monitor_check=False"]}
            return {"ok": True, "unwired": True, "fixture": fixture, "problems": [],
                    "registered": None, "state": None, "intent_id": None,
                    "note": "调用方显式声明未接线的测试夹具"}
        function = self.monitor_check
        try:
            takes_action = "action" in inspect.signature(function).parameters
        except (TypeError, ValueError):
            takes_action = False
        try:
            result = function(owner_session, plan_id, action=action) if takes_action \
                else function(owner_session, plan_id)
        except Exception as exc:  # noqa: BLE001 - 监控坏掉不等于核过
            return {"ok": False, "fixture": fixture, "unwired": False,
                    "problems": [f"监控检查失败（{type(exc).__name__}: {exc}）：不可判即不放行"]}
        if not isinstance(result, dict):
            return {"ok": False, "fixture": fixture, "unwired": False,
                    "problems": [f"监控检查返回值不可判（需要对象）：{_jsonable(result)}"]}
        problems = [str(item) for item in (result.get("problems") or [])]
        verdict = result.get("ok")
        if verdict is None:
            verdict = result.get("allow")
        registered = result.get("registered")
        state = result.get("state")
        current_intent = result.get("intent_id")
        protocol = []
        if registered is not True:
            protocol.append("监控未登记（registered≠true）：先按 Monitoring.register 登记负责人会话")
        if state not in MONITOR_STATES:
            protocol.append(f"监控状态字段不可判（state={state!r}，应为 {'/'.join(MONITOR_STATES)}）")
        if current_intent is not None and not isinstance(current_intent, str):
            protocol.append(f"监控 intent_id 字段不可判（{current_intent!r}）")
        if _filled(owner_session) and intent_none:
            if state not in ("none", "closed"):
                protocol.append(
                    f"声明 --intent-none，但监控真实状态是 {state!r}：不能按『没有专项意图』起跑")
            if _filled(current_intent):
                protocol.append(
                    f"声明 --intent-none，但监控仍报告当前意图 id={current_intent!r}："
                    "先按意图流程处理，或换 --intent-id")
        elif _filled(owner_session):
            if not _filled(intent_id):
                protocol.append("必须显式声明 --intent-id 或 --intent-none（省略不默认放行）")
            elif state not in ("active", "blocked"):
                protocol.append(
                    f"声明 --intent-id={intent_id!r}，但监控真实状态是 {state!r}："
                    "只有 active/blocked 才能带意图 id 起跑")
            elif current_intent != intent_id:
                protocol.append(
                    f"声明的意图 id 与监控真实当前 id 不一致：声明 {intent_id!r}，"
                    f"监控 {current_intent!r}（不能填一个与监控状态无关的 id）")
        if protocol:
            problems = protocol + problems
            verdict = False
        if verdict is True:
            return {"ok": True, "problems": problems, "fixture": fixture, "unwired": False,
                    "registered": registered, "state": state, "intent_id": current_intent,
                    "declared_intent": intent_id if not intent_none else None,
                    "result": _jsonable(result)}
        if verdict is False:
            return {"ok": False, "fixture": fixture, "unwired": False,
                    "registered": registered, "state": state, "intent_id": current_intent,
                    "declared_intent": intent_id if not intent_none else None,
                    "problems": problems or ["监控检查未给出问题说明，但判不放行"]}
        return {"ok": False, "fixture": fixture, "unwired": False,
                "problems": [f"监控检查返回值不可判（需要 {{'ok'|'allow': bool}}）：{_jsonable(result)}"]}

    def _gate(self, *, topic, plan, owner_session, argv, intent_id, intent_none, action):
        """只读起跑闸。返回 {ok, problems, checks, preparation, plan_obj}。⛔ 不写盘、不起进程。"""
        problems = []
        checks = {}

        # ---- 0. 输入完备性：省略不默认放行 ----
        if not _filled(owner_session):
            problems.append("owner_session 必填：省略或空白不放行（旧入口「省略会话就默认放行」的行为在这里没有）")
        if intent_none and intent_id:
            problems.append("intent 声明冲突：--intent-id 与 --intent-none 只能给一个")
        elif not intent_none and not intent_id:
            problems.append("必须显式声明监控入口：--intent-id <id> 或 --intent-none（省略不默认放行）")
        actual = []
        if argv is None or (isinstance(argv, (list, tuple)) and not list(argv)):
            problems.append("必须给出实际 argv（显式数组，经 `--` 传入）：省略不默认放行，也不跳过比对")
        else:
            if isinstance(argv, str) or not isinstance(argv, (list, tuple)):
                problems.append("实际 argv 必须是字符串数组，⛔ 不接受字符串命令行")
            elif any(not isinstance(item, str) or not item for item in argv):
                problems.append("实际 argv 的每一项都必须是非空字符串")
            else:
                actual = [str(item) for item in argv]

        # ---- 1. 计划与 J1 ----
        plan_obj, plan_source, plan_file_sha = self._load_plan(plan)
        plan_id = plan_obj.get("id") if isinstance(plan_obj.get("id"), str) else plan_obj.get("id")
        if not _filled(plan_id):
            problems.append("计划缺 id（一轮 = 一个 plan_id）")
            plan_id = None
        errors = validate_plan(plan_obj)
        plan_problems, argv_variants, implementation, hypothesis = self._plan_checks(plan_obj)
        problems.extend("计划校验：" + item for item in errors)
        problems.extend(plan_problems)
        checks["plan_file"] = {"source": plan_source, "sha256": plan_file_sha}

        if plan_id:
            # ---- 2. 专题与主文档 ----
            try:
                topic_row = self._require_topic(self._gate_conn(), topic)
            except WorkflowError as exc:
                problems.extend(exc.errors or [str(exc)])
                topic_row = None
            try:
                topic_doc = self._topic_doc(topic)
                checks["topic_doc"] = topic_doc
            except WorkflowError as exc:
                problems.extend(exc.errors or [str(exc)])
            if topic_row is not None:
                checks["topic"] = {"id": topic_row["id"], "status": topic_row["status"],
                                   "objective": topic_row.get("objective") or ""}

            # ---- 3. 计划与已冻结版本一致 ----
            latest = self.gov.latest_plan(plan_id)
            if latest is None:
                problems.append("计划还没有冻结过：先 prepare（会冻结并记录哈希）")
            else:
                checks["plan"] = {"version": latest["version"], "key_fingerprint": latest["key_fingerprint"]}
                if canonical_json(latest["plan"]) != canonical_json(plan_obj):
                    problems.append("计划与已冻结版本不一致（内容或关键字段改了）：先 prepare 重新冻结")

            # ---- 3b. 执行落点（真实独立代码 / 环境；不接受假远端）----
            target = None
            if actual:
                try:
                    target = self.execution_target.resolve(topic, plan_obj, actual)
                    checks["execution_target"] = {"ok": True, **_jsonable(target)}
                except (ValueError, OSError) as exc:
                    checks["execution_target"] = {"ok": False, "problems": [str(exc)]}
                    problems.append("执行目标：" + str(exc))

            # ---- 4. 准备记录：存在 / 会话一致 / argv 命中 / 未过期 ----
            preparation = self._latest_preparation(self._gate_conn(), plan_id, topic)
            if preparation is None:
                problems.append("这一轮还没有 prepare 记录：先 prepare 冻结来源哈希、实现声明与 argv")
            else:
                checks["preparation"] = {"prepared_id": preparation["prepared_id"],
                                         "owner_session": preparation["owner_session"],
                                         "source_fingerprint": preparation["source_fingerprint"]}
                if _filled(owner_session) and preparation["owner_session"] != owner_session:
                    problems.append(
                        f"owner_session 与 prepare 记录的负责人不一致：{owner_session!r} != "
                        f"{preparation['owner_session']!r}（不可省会话默认放行）")
                declared = json.loads(preparation["argv_json"])
                if actual and declared and actual not in declared:
                    problems.append(
                        "实际 argv 与事前声明的 argv 不一致（逐项严格比对）："
                        f"实际 {actual} ；声明 {declared}")
                problems.extend(self._lifecycle_before_problem(preparation, self._gate_conn()))
                problems.extend(self._expiry_problems(preparation, self._gate_conn()))

            # ---- 5. 前轮与重试（J5 / J6）----
            problems.extend(self._predecessor_problems(self._gate_conn(), topic, plan_obj, hypothesis))

            # ---- 6. 审计（K1 起跑检查：缺审计不启动）----
            try:
                state = self.gov.audit_state(plan_id)
            except (GovernanceError, sqlite3.Error) as exc:
                state = {"ok": False, "problems": [f"审计状态不可判：{exc}"], "decision": None}
            checks["audit"] = {
                "ok": bool(state.get("ok")), "decision": state.get("decision"),
                "audit_decision": state.get("audit_decision"),
                "independence": state.get("independence"),
                "audits_used": state.get("audits_used"), "limit": state.get("limit"),
                "key_fingerprint": state.get("key_fingerprint"),
            }
            if not state.get("ok"):
                problems.extend("审计：" + item for item in (state.get("problems") or ["不可判"]))

            # ---- 7. 意图：唯一权威是监控（Monitoring.check_start）----
            # ⛔ 不再叠加旧的 Governance.check_start：那会让真实用户只按 monitoring 登记时永不能起跑
            # （两套账孤岛）。旧 Governance 意图 API 保留给兼容，但不接生产双重拦截。
            intent_argument = None if intent_none else (intent_id or None)
            checks["intent"] = {"authority": "monitoring",
                                "declared_intent_id": intent_argument,
                                "declared_none": bool(intent_none)}

            # ---- 8. 监控检查（可注入；未接线默认拒；完整协议字段）----
            monitor = self._monitor_gate(owner_session, plan_id, action,
                                         intent_id=intent_argument, intent_none=intent_none)
            checks["monitor"] = monitor
            if not monitor["ok"]:
                problems.extend("监控：" + item for item in monitor["problems"])

            # ---- 9. 执行准入（预算 / 规模 / 决策；可注入；未接线默认拒）----
            budget = plan_obj.get("budget") if isinstance(plan_obj.get("budget"), dict) else {}
            timeout_seconds = budget.get("max_seconds") if _is_number(budget.get("max_seconds")) else None
            case_sets = plan_obj.get("case_sets") if isinstance(plan_obj.get("case_sets"), dict) else {}
            case_count = sum(len(case_sets.get(kind) or []) for kind in ("target", "regression", "control")
                             if isinstance(case_sets.get(kind), list))
            declaration = plan_obj.get("authorization")
            declaration = declaration if isinstance(declaration, dict) else None
            reservation_key = "wf:" + _sha_text(canonical_json(
                [preparation["prepared_id"] if preparation else None, actual]))[:32]
            record = {
                "action": action, "workspace": str(self.root), "topic": topic,
                "plan_id": plan_id,
                "plan_version": latest["version"] if latest is not None else None,
                "prepared_id": preparation["prepared_id"] if preparation else None,
                "owner_session": owner_session, "argv": actual,
                "budget": dict(budget), "case_count": case_count,
                # 预算服务要幂等 reserve 就用它当 request_key（同一轮 + 同一 argv 恒定）。
                "reservation_key": reservation_key,
                # 计划里声明的执行授权（Admission 核它；本层只记录与绑定）。
                "authorization": _jsonable(declaration),
            }
            checks["execution"] = {
                "cwd": str(self.root), "shell": False,
                "timeout_seconds": timeout_seconds,
                "env_added": sorted(["AUTORESEARCH_WORKSPACE", "AUTORESEARCH_TOPIC",
                                     "AUTORESEARCH_PLAN_ID", "AUTORESEARCH_RUN_ID",
                                     "AUTORESEARCH_PREPARED_ID"]),
                "budget": dict(budget), "case_count": case_count,
                "authorization_declaration": _jsonable(declaration),
                "note": "只保证单进程墙钟不超 plan.budget.max_seconds；"
                        "共享预算 reservation / 租约由 Admission 与 Accounts 负责，本层不结算"
                        "（见模块文档「已知缺口」）",
            }
            admission = self._authorization_gate(plan_obj, owner_session, plan_id, record)
            checks["authorization"] = admission
            checks["authorization"]["request"] = _jsonable(record)
            if not admission["ok"]:
                problems.extend("执行准入：" + item for item in admission["problems"])

            return {"ok": not problems, "problems": problems, "checks": checks,
                    "preparation": preparation if plan_id and "preparation" in checks else None,
                    "plan_obj": plan_obj, "plan_id": plan_id, "plan_source": plan_source,
                    "argv_variants": argv_variants, "implementation": implementation,
                    "hypothesis": hypothesis, "actual_argv": actual,
                    "timeout_seconds": timeout_seconds, "target": target}

        return {"ok": not problems, "problems": problems, "checks": checks, "preparation": None,
                "plan_obj": plan_obj, "plan_id": plan_id, "plan_source": plan_source,
                "argv_variants": argv_variants, "implementation": implementation,
                "hypothesis": hypothesis, "actual_argv": actual,
                "timeout_seconds": None, "target": None}

    def _authorization_gate(self, plan, owner_session, plan_id, record):
        """执行准入：生产预算 / 规模 / 决策的接缝。默认 fail-closed。

        协议（与 `Admission.check` 一致）：`authorization_check(plan, owner_session, exp_id)`，
        返回 `{ok|allow, problems, decision, ...}`。`decisions` 的检查在 `Admission` 里：
        小样本按 `Accounts` 的 active / expiry / reserved / exp / scope / unit 放行，
        `large` / `submission` 另要 approved 决策。⛔ 没接线就放行等于「统一入口能从 plan 的
        argv 随便开远端大题而不看决策」，所以默认拒绝。
        """
        fixture = not self.require_authorization_check
        if self.authorization_check is None:
            if self.require_authorization_check:
                return {"ok": False, "unwired": True, "fixture": fixture,
                        "problems": ["执行准入未接线（authorization_check=None）：未接线不放行；"
                                     "生产入口必须接 admission.Admission(workspace, store=).check"
                                     "（它核 Accounts 的 active/expiry/reserved/exp/scope/unit，"
                                     "large/submission 再调 decisions）；测试注入夹具时必须显式 "
                                     "require_authorization_check=False"]}
            return {"ok": True, "unwired": True, "fixture": fixture, "problems": [],
                    "note": "调用方显式声明未接线的测试夹具"}
        function = self.authorization_check
        try:
            parameters = list(inspect.signature(function).parameters.values())
        except (TypeError, ValueError):
            parameters = []
        positional = [item for item in parameters
                      if item.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                       inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        has_varargs = any(item.kind is inspect.Parameter.VAR_POSITIONAL for item in parameters)
        try:
            if has_varargs or len(positional) >= 3:
                result = function(plan, owner_session, plan_id)
            elif len(positional) == 1 and positional[0].name == "request":
                result = function(request=record)
            elif len(positional) == 1:
                result = function(record)
            else:
                result = function(plan=plan, owner_session=owner_session, exp_id=plan_id)
        except Exception as exc:  # noqa: BLE001 - 准入坏掉不等于核过
            return {"ok": False, "fixture": fixture, "unwired": False,
                    "problems": [f"执行准入检查失败（{type(exc).__name__}: {exc}）：不可判即不放行"]}
        if isinstance(result, dict):
            problems = [str(item) for item in (result.get("problems") or [])]
            verdict = result.get("ok")
            if verdict is None:
                verdict = result.get("allow")
            if verdict is True:
                return {"ok": True, "problems": problems, "fixture": fixture, "unwired": False,
                        "decision": result.get("decision"), "scope": result.get("scope"),
                        "result": _jsonable(result)}
            if verdict is False:
                return {"ok": False, "fixture": fixture, "unwired": False,
                        "decision": result.get("decision"), "scope": result.get("scope"),
                        "problems": problems or ["执行准入未给出问题说明，但判不放行"]}
        return {"ok": False, "fixture": fixture, "unwired": False,
                "problems": [f"执行准入返回值不可判（需要 {{'ok'|'allow': bool}}）：{_jsonable(result)}"]}

    def _gate_conn(self):
        """闸内复用同一个只读连接的简单持有（调用方负责关闭：见 `_with_conn`）。"""
        if getattr(self, "_gate_conn_holder", None) is None:
            raise WorkflowError("内部错误：只读连接未建立", code=2)
        return self._gate_conn_holder

    @contextmanager
    def _with_conn(self):
        with self._read_conn() as conn:
            self._gate_conn_holder = conn
            try:
                yield conn
            finally:
                self._gate_conn_holder = None

    def _readonly_gov(self):
        """占用事务里复核审计用的**只读** Governance（独立只读连接）。

        ⚠️ 占用事务持有 `BEGIN IMMEDIATE` 写锁；若这里用可写 Store 的
        `BEGIN IMMEDIATE` 会自己等自己（busy timeout）⇒ 必须走只读连接。
        """
        if self._gov_ro is not None:
            return self._gov_ro
        if not self.db.is_file():
            self._gov_ro = self.gov
            return self._gov_ro
        policy = getattr(self.gov, "policy", None) if self.gov is not None else None
        verifier = getattr(self.gov, "verifier", None) if self.gov is not None else None
        self._gov_ro = _readonly_governance(self.db, verifier=verifier, policy=policy)
        return self._gov_ro

    # -- 生命周期投影（通知，非闸） -----------------------------------------

    def _lifecycle(self):
        if self.lifecycle is not None:
            return self.lifecycle
        if self.readonly:
            return None
        try:
            from kernel.autoresearch.lifecycle import Lifecycle
            self.lifecycle = Lifecycle(self.root, db=self.db)
        except Exception:  # noqa: BLE001 - 投影不可用不算闸失败
            self.lifecycle = None
        return self.lifecycle

    def _lifecycle_call(self, kind, action, context=None, *args, **kwargs):
        """调 Lifecycle 的 before/after/sync；**永不抛**。失败只追加诊断事件。"""
        lifecycle = self._lifecycle()
        if lifecycle is None:
            return {"ok": False, "wired": False, "error": "Lifecycle 未接线"}
        context = dict(context or {})
        try:
            outcome = getattr(lifecycle, action)(*args, **kwargs)
            return {"ok": not isinstance(outcome, dict) or outcome.get('ok') is not False,
                    "wired": True, "kind": kind, "result": _jsonable(outcome)}
        except Exception as exc:  # noqa: BLE001 - 投影失败不改变任何决定与退出码
            detail = f"{type(exc).__name__}: {exc}"
            try:
                with self.store.transaction() as conn:
                    self._event(conn, "lifecycle_error", topic=context.get("topic") or "",
                                plan_id=context.get("plan_id"),
                                prepared_id=context.get("prepared_id"),
                                run_id=context.get("run_id"),
                                payload={"kind": kind, "action": action, "error": detail,
                                         "note": "生命周期投影失败不改变决定、退出码与安全停"},
                                actor="lifecycle")
            except Exception:  # noqa: BLE001 - 连诊断都写不进去时仍不抛
                pass
            return {"ok": False, "wired": True, "kind": kind, "error": detail}

    def _lifecycle_before_problem(self, preparation, conn=None):
        """启动前必须有一份与这份 preparation 绑定、且与当前主文档字节一致的 before 快照。"""
        topic = preparation.get("topic")
        plan_id = preparation.get("plan_id")
        if not _filled(topic) or not _filled(plan_id):
            return ["准备记录缺 topic/plan_id：无法核 before 快照"]
        base = self.root / "topics" / str(topic) / "snapshots" / str(plan_id) / str(preparation['prepared_id'])
        receipt_path, snapshot_path = base / "before.json", base / "before.md"
        if not receipt_path.is_file() or not snapshot_path.is_file():
            return [f"缺 before 主文档快照：topics/{topic}/snapshots/{plan_id}/before.*"
                    "（prepare 的 Lifecycle.before 没成功 ⇒ 不允许启动；历史轮次不补造）"]
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
            snapshot_sha = _sha_file(snapshot_path)
        except (OSError, ValueError) as exc:
            return [f"before 快照/收据不可读：{exc}"]
        if not isinstance(receipt, dict):
            return ["before 收据不是 JSON 对象"]
        problems = []
        if receipt.get("prepared_id") != preparation.get("prepared_id"):
            problems.append("before 收据的 prepared_id 与准备记录不一致")
        if receipt.get("plan_sha256") != preparation.get("plan_sha256"):
            problems.append("before 收据的 plan_sha256 与准备记录不一致")
        if receipt.get("source_fingerprint") != preparation.get("source_fingerprint"):
            problems.append("before 收据的 source_fingerprint 与准备记录不一致")
        if receipt.get("sha256") != snapshot_sha:
            problems.append("before 快照字节与收据里的 sha256 不一致")
        doc = self._topic_doc(topic)
        if receipt.get("sha256") != doc["sha256"]:
            problems.append("before 快照与当前专题主文档字节不一致（主文档在 prepare 后变过）")
        if conn is not None and self._table_exists(conn, "wf_events"):
            incomplete = self._one(
                conn, "SELECT 1 AS x FROM wf_events WHERE kind='lifecycle_incomplete'"
                      " AND prepared_id=? LIMIT 1", (preparation.get("prepared_id"),))
            if incomplete is not None:
                problems.append("prepare 的 Lifecycle 投影被标记为 incomplete（before/sync 失败）")
        return problems

    def _latest_preparation(self, conn, plan_id, topic):
        return self._one(conn, "SELECT * FROM wf_preparations WHERE plan_id=? AND topic=?"
                               " ORDER BY created_at DESC, rowid DESC LIMIT 1", (plan_id, topic))

    # -- 对外操作 -----------------------------------------------------------

    def prepare(self, *, topic, plan, owner_session, sources=(), source_file=None, actor="main"):
        """准备事前材料：J1 四锚点 + 实现五项/files + argv，冻结哈希，append 一条准备记录。"""
        self._require_write()
        for name, value in (("topic", topic), ("owner_session", owner_session), ("actor", actor)):
            if not _filled(value):
                raise WorkflowError(f"{name} 必填（不可省会话默认放行）", code=2)
        plan_obj, plan_source, plan_file_sha = self._load_plan(plan)
        errors = validate_plan(plan_obj)
        plan_problems, argv_variants, implementation, hypothesis = self._plan_checks(plan_obj)
        problems = ["计划校验：" + item for item in errors] + plan_problems
        if not _filled(plan_obj.get("id")):
            problems.append("计划缺 id（一轮 = 一个 plan_id）")
        if problems:
            raise WorkflowError("事前材料不齐，不能准备：" + "；".join(problems), problems, code=2)
        plan_id = plan_obj["id"]

        with self._with_conn() as conn:
            topic_row = self._require_topic(conn, topic)
            topic_doc = self._topic_doc(topic)
            blockers = self._predecessor_problems(conn, topic, plan_obj, hypothesis)
        if blockers:
            raise WorkflowError("不能准备新一回：" + "；".join(blockers), blockers)

        frozen = self._ensure_frozen(plan_obj, actor)
        extra_refs = self._source_refs(sources, source_file)
        manifest = self._source_manifest(plan_obj, implementation, extra_refs, topic_doc, topic)
        normalized_impl = dict(implementation or {})

        with self.store.transaction() as conn:
            # 冻结与写准备记录之间可能有人又冻结了新版本 ⇒ 这里再核一次，别把旧版本写进快照。
            latest = self._one_in(conn, "gov_plans",
                                  "SELECT version,key_fingerprint,plan_json FROM gov_plans"
                                  " WHERE plan_id=? ORDER BY version DESC LIMIT 1", (plan_id,))
            plan_sha256 = _sha_text(canonical_json(json.loads(latest["plan_json"]))) if latest else None
            if latest is None or int(latest["version"]) != int(frozen["version"]) \
                    or plan_sha256 != frozen["plan_sha256"]:
                raise WorkflowError("计划在准备期间被重新冻结：重试 prepare（没有写任何准备记录）",
                                    ["计划版本在 prepare 期间变化"], code=9)
            count = conn.execute("SELECT COUNT(*) AS n FROM wf_preparations").fetchone()["n"]
            snapshot = {
                "plan": {"plan_id": plan_id, "version": latest["version"],
                         "key_fingerprint": latest["key_fingerprint"],
                         "plan_sha256": plan_sha256},
                "topic": {"id": topic, "objective": topic_row.get("objective") or "",
                          "status": topic_row.get("status"), "doc": topic_doc},
                "user_updates": self._user_updates(conn, owner_session),
                "review_sources": self._review_sources(conn, topic),
                "sources": manifest,
                "implementation": normalized_impl,
                "argv": [list(group) for group in argv_variants],
            }
            source_fingerprint = _sha_text(canonical_json(snapshot))
            prepared_id = "PREP-" + _sha_text(canonical_json(
                [plan_id, topic, owner_session, frozen["plan_sha256"], source_fingerprint, count,
                 self._now()]))[:16]
            conn.execute(
                "INSERT INTO wf_preparations(prepared_id,plan_id,plan_version,topic,owner_session,"
                "key_fingerprint,plan_sha256,plan_source,argv_json,implementation_json,snapshot_json,"
                "source_fingerprint,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (prepared_id, plan_id, frozen["version"], topic, owner_session,
                 frozen["key_fingerprint"], frozen["plan_sha256"], plan_source,
                 canonical_json([list(group) for group in argv_variants]),
                 canonical_json(normalized_impl), canonical_json(snapshot),
                 source_fingerprint, actor, self._now()))
            self._event(conn, "prepared", topic=topic, plan_id=plan_id, prepared_id=prepared_id,
                        payload={"plan_version": frozen["version"], "refroze": frozen["refroze"],
                                 "reaudit_required": frozen["reaudit_required"],
                                 "source_fingerprint": source_fingerprint,
                                 "sources": sorted(manifest), "argv": segments(argv_variants)},
                        actor=actor)
        out = {"ok": True, "prepared_id": prepared_id, "plan_id": plan_id,
               "plan_version": frozen["version"], "key_fingerprint": frozen["key_fingerprint"],
               "plan_sha256": frozen["plan_sha256"], "plan_source": plan_source,
               "plan_file_sha256": plan_file_sha, "source_fingerprint": source_fingerprint,
               "refroze": frozen["refroze"], "reaudit_required": frozen["reaudit_required"],
               "argv": [list(group) for group in argv_variants],
               "implementation": normalized_impl, "snapshot": snapshot,
               "owner_session": owner_session, "topic": topic}
        # 生命周期：先 before（启动前主文档快照），再 sync（AI_WORK / 收据 / reporting 投影）。
        # ⚠️ before 失败 = 这一轮准备不完整：落一条 lifecycle_incomplete，`start()` 的闸会因为
        #    缺 before 收据而拒绝，⛔ 不允许事后补造历史快照。
        prep_ref = {"prepared_id": prepared_id, "topic": topic, "plan_id": plan_id,
                    "plan_sha256": frozen["plan_sha256"],
                    "source_fingerprint": source_fingerprint}
        before = self._lifecycle_call("before", "before", prep_ref, prep_ref)
        out["lifecycle_before"] = before
        out["lifecycle_complete"] = bool(before.get("ok"))
        if not before.get("ok"):
            with self.store.transaction() as conn:
                self._event(conn, "lifecycle_incomplete", topic=topic, plan_id=plan_id,
                            prepared_id=prepared_id,
                            payload={"stage": "before", "error": before.get("error"),
                                     "note": "prepare 未完成 before 快照：start 会被拒"},
                            actor=actor)
        out["lifecycle_sync"] = self._lifecycle_call(
            "sync", "sync", dict(prep_ref, stage="prepare"), topic, plan_id=plan_id)
        return out

    def check(self, *, topic, plan, owner_session, argv, intent_id=None, intent_none=False):
        """只读起跑检查（start-check）。返回 {ok, problems, checks}；⛔ 不写盘、不起进程。"""
        with self._with_conn():
            result = self._gate(topic=topic, plan=plan, owner_session=owner_session, argv=argv,
                                intent_id=intent_id, intent_none=intent_none, action="launch")
        result.pop("plan_obj", None)
        result.pop("implementation", None)
        result.pop("argv_variants", None)
        result["action"] = "check"
        return result

    # -- 写事务内的只读启动复核（本地 start 与受管远端 claim/send 共用） --------

    def _readonly_launch_recheck(self, conn, preparation, plan_obj, *, plan_id, owner_session,
                                 intent_id=None, intent_none=False, action="launch", record=None):
        """在**已有写事务**里做启动前的只读复核；本地起进程与受管远端发请求共用同一段判据。

        为什么必须共用：`check()` 与真正的启动/发送之间有时序缝 —— 计划被重新冻结、来源文件改了、
        用户又说了新原话、审计被新阻断、监控出现新待审、预算预留被释放，这些都可能在缝隙里发生。
        只在 `check()` 里核一次等于「核完再改就放行」。

        调用约束（⛔ 不要放宽）：
        * 调用方必须已经持有 `BEGIN IMMEDIATE`（`self.store.transaction()`）的连接 `conn`；
        * 本方法**只读**：读 `conn`、只读 Governance（独立只读连接）、读文件、只读监控与准入；
          ⛔ 不在这里再开写事务（会自己等锁），⛔ 不在这里注册监控事件。

        返回 `{"ok": bool, "checks": {...}, "failures": [{"kind", "message", "problems"}]}`；
        `message` 是给用户看的完整句子（本地与远端的处置不同：本地零子进程、远端零远端请求），
        调用方只决定怎么抛。
        """
        checks = {}
        failures = []

        def fail(kind, message, problems):
            failures.append({"kind": kind, "message": message,
                             "problems": [str(item) for item in (problems or [message])]})

        # 1) 计划版本与冻结字节（后面的复核都建立在这一轮冻结的计划上）
        latest = self._one_in(conn, "gov_plans",
                              "SELECT version,plan_json FROM gov_plans WHERE plan_id=?"
                              " ORDER BY version DESC LIMIT 1", (plan_id,))
        plan_sha256 = _sha_text(canonical_json(json.loads(latest["plan_json"]))) if latest else None
        version_ok = (latest is not None
                      and int(latest["version"]) == int(preparation["plan_version"])
                      and plan_sha256 == preparation["plan_sha256"])
        checks["plan_version_recheck"] = {
            "ok": version_ok,
            "version": (None if latest is None else int(latest["version"])),
            "expected_version": int(preparation["plan_version"])}
        if not version_ok:
            fail("plan_version",
                 "拒绝启动：计划在 prepare 之后被改动或重新冻结 ⇒ 先重新 prepare"
                 "（没有启动任何子进程 / 没有发出任何远端请求）",
                 ["计划版本与准备记录不一致"])
            return {"ok": False, "checks": checks, "failures": failures}

        # 2) 生命周期 before 快照：必须与这份 preparation 绑定，且字节仍对得上
        before = self._lifecycle_before_problem(preparation, conn)
        checks["lifecycle_before_recheck"] = {"ok": not before, "problems": before}
        if before:
            fail("lifecycle_before",
                 "拒绝启动：before 主文档快照缺失 / 与准备记录不一致（prepare 的生命周期未完成）"
                 "⇒ 不允许事后补造历史快照（没有启动任何子进程 / 没有发出任何远端请求）："
                 + "；".join(before), before)
            return {"ok": False, "checks": checks, "failures": failures}

        # 3) 事前来源哈希（来源文件 / 专题目标与主文档 / 该会话用户原话 / 旧复盘来源）
        sources = self._expiry_problems(preparation, conn, prefer_topics=False)
        checks["preparation_recheck"] = {"ok": not sources, "problems": sources}
        if sources:
            fail("preparation_sources",
                 "拒绝启动：准备来源在闸之后变了（来源文件 / 专题目标或主文档 / 用户原话变了）"
                 "⇒ 先重新 prepare（没有启动任何子进程 / 没有发出任何远端请求）："
                 + "；".join(sources), sources)
            return {"ok": False, "checks": checks, "failures": failures}

        # 4) 审计状态（只读 Governance：占用事务持有写锁，不能在这里再开写事务）
        try:
            audit = self._readonly_gov().audit_state(plan_id)
        except (GovernanceError, sqlite3.Error) as exc:
            audit = {"ok": False, "problems": [f"审计状态复核不可判：{exc}"],
                     "decision": None, "key_fingerprint": None}
        checks["audit_recheck"] = {"ok": bool(audit.get("ok")),
                                   "decision": audit.get("decision"),
                                   "key_fingerprint": audit.get("key_fingerprint"),
                                   "problems": list(audit.get("problems") or [])}
        if not audit.get("ok"):
            problems = list(audit.get("problems") or ["审计状态不可判"])
            fail("audit",
                 "拒绝启动：审计状态在闸之后变成了不通过（新阻断 / 政策撤销 / 意见变动）"
                 "（没有启动任何子进程 / 没有发出任何远端请求）：" + "；".join(problems), problems)
            return {"ok": False, "checks": checks, "failures": failures}

        # 5) 监控（唯一意图权威；只读调用，安全动作之外都拦）
        monitor = self._monitor_gate(
            owner_session, plan_id, action,
            intent_id=None if intent_none else (intent_id or None),
            intent_none=bool(intent_none))
        checks["monitor_recheck"] = monitor
        if not monitor["ok"]:
            fail("monitor",
                 "拒绝启动：监控状态在闸之后变了（新用户原话 / 待审触发 / 意见未处置 / 登记变化）"
                 "（没有启动任何子进程 / 没有发出任何远端请求）："
                 + "；".join(monitor["problems"]), monitor["problems"])
            return {"ok": False, "checks": checks, "failures": failures}

        # 6) 执行准入（预算预留 / 作用域 / 决策）
        #    此刻持有 RESERVED 写锁，别的进程无法提交 release / settle；核验器必须是**只读**的
        #    （`Admission.check` 只读），否则它会在这里等锁 —— 异常也按拒绝处理。
        try:
            authorization = self._authorization_gate(plan_obj, owner_session, plan_id,
                                                     dict(record or {}))
        except sqlite3.Error as exc:
            authorization = {"ok": False, "unwired": False, "fixture": False,
                             "problems": [f"占用事务里复核执行准入失败"
                                          f"（{type(exc).__name__}: {exc}）：不放行"]}
        checks["authorization_recheck"] = authorization
        if not authorization["ok"]:
            fail("authorization",
                 "拒绝启动：执行准入在占用事务里复核失败（预算预留已释放 / 过期 / 不属于本轮 / "
                 "决策已撤回）（没有启动任何子进程 / 没有发出任何远端请求）："
                 + "；".join(authorization["problems"]), authorization["problems"])

        return {"ok": not failures, "checks": checks, "failures": failures}

    def start(self, *, topic, plan, owner_session, argv, intent_id=None, intent_none=False,
              actor="main", finish=True):
        """通过闸后**原子占用**唯一活跃 run，再用显式 argv 真的启动本地子进程，保留真实退出码。"""
        self._require_write()
        if not _filled(actor):
            raise WorkflowError("actor 必填", code=2)
        with self._with_conn():
            gate = self._gate(topic=topic, plan=plan, owner_session=owner_session, argv=argv,
                              intent_id=intent_id, intent_none=intent_none, action="launch")
        if not gate["ok"]:
            raise WorkflowError("拒绝启动（没有启动任何子进程）：" + "；".join(gate["problems"]),
                                gate["problems"], code=9)
        preparation = gate["preparation"]
        plan_obj = gate["plan_obj"]
        plan_id = gate["plan_id"]
        argv_actual = gate["actual_argv"]
        hypothesis = gate["hypothesis"]
        gates = gate["checks"]
        target = gate.get("target") or {}
        execution_cwd = target.get("cwd") or str(self.root)
        env_additions = target.get("env_additions") or {}

        with self.store.transaction() as conn:
            recheck = self._readonly_launch_recheck(
                conn, preparation, plan_obj, plan_id=plan_id, owner_session=owner_session,
                intent_id=intent_id, intent_none=intent_none, action="launch",
                record=(gates.get("authorization") or {}).get("request") or {})
            gates.update(recheck["checks"])
            failures = recheck["failures"]
            if failures:
                first = failures[0]
                raise WorkflowError(first["message"], first["problems"], code=9)
            count = conn.execute("SELECT COUNT(*) AS n FROM wf_runs").fetchone()["n"]
            run_id = "RUN-" + _sha_text(canonical_json(
                [preparation["prepared_id"], argv_actual, count, self._now()]))[:16]
            try:
                conn.execute(
                    "INSERT INTO wf_runs(run_id,prepared_id,plan_id,plan_version,topic,owner_session,"
                    "hypothesis_id,hypothesis_text,argv_json,cwd,gates_json,source_fingerprint,"
                    "plan_sha256,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, preparation["prepared_id"], plan_id, preparation["plan_version"], topic,
                     owner_session, hypothesis.get("id"), hypothesis.get("text"),
                     canonical_json(argv_actual), execution_cwd, canonical_json(_jsonable(gates)),
                     preparation["source_fingerprint"], preparation["plan_sha256"], self._now()))
            except sqlite3.IntegrityError as exc:
                raise WorkflowError(
                    "拒绝启动：同专题已有未结束运行，或本轮 plan 已经启动过（并发 / 重复启动）——" + str(exc),
                    [str(exc)]) from None
            self._event(conn, "launched", topic=topic, plan_id=plan_id,
                        prepared_id=preparation["prepared_id"], run_id=run_id,
                        payload={"argv": argv_actual, "cwd": execution_cwd,
                                 "source_fingerprint": preparation["source_fingerprint"],
                                 "execution_target": _jsonable(target),
                                 "execution_scope": target.get("execution_scope", "local"),
                                 "remote_stop_verified": bool(target.get("remote_stop_verified")),
                                 "monitor_gate": "unwired" if gates.get("monitor", {}).get("unwired") else "wired"},
                        actor=actor)

        start_sync = self._lifecycle_call(
            "sync", "sync",
            {"topic": topic, "plan_id": plan_id, "prepared_id": preparation["prepared_id"],
             "run_id": run_id, "stage": "start"},
            topic, plan_id=plan_id)
        env = dict(self.environ)
        env.update(env_additions)
        env.update({"AUTORESEARCH_WORKSPACE": str(self.root), "AUTORESEARCH_TOPIC": topic,
                    "AUTORESEARCH_PLAN_ID": plan_id, "AUTORESEARCH_RUN_ID": run_id,
                    "AUTORESEARCH_PREPARED_ID": preparation["prepared_id"],
                    "AUTORESEARCH_EXECUTION_SCOPE": str(target.get("execution_scope", "local"))})
        timeout_seconds = gate.get("timeout_seconds")
        execution = {"scope": target.get("execution_scope", "local"),
                     "remote_stop_verified": bool(target.get("remote_stop_verified")),
                     "cwd": execution_cwd,
                     "note": "本地超时只证明本地子进程被终止；⛔ 不声称远端/S​​SH 任务已停止"}
        try:
            exitcode = self._invoke(argv_actual, env, timeout_seconds, cwd=execution_cwd)
        except subprocess.TimeoutExpired:
            # plan.budget.max_seconds 是这一层的硬上界：超时即终止，并记真实原因。
            recorded = self._record_finish(
                run_id, exitcode=None, reason="timeout", signal="timeout",
                documentation={"status": "skipped",
                               "detail": f"超过 plan.budget.max_seconds={timeout_seconds}，子进程已终止"},
                actor=actor)
            return {"ok": True, "run_id": run_id, "prepared_id": preparation["prepared_id"],
                    "plan_id": plan_id, "topic": topic, "argv": argv_actual,
                    "exitcode": None, "reason": recorded["reason"], "finished": True,
                    "timed_out": True, "timeout_seconds": timeout_seconds,
                    "report_pending": True, 'execution': execution}
        except KeyboardInterrupt:
            self._record_finish(run_id, exitcode=130, reason="interrupted", signal="SIGINT",
                                documentation={"status": "skipped", "detail": "安全停：用户中断"},
                                actor=actor)
            raise
        except WorkflowError:
            self._record_finish(run_id, exitcode=None, reason="launch_error", signal=None,
                                documentation={"status": "failed", "detail": "子进程启动失败"},
                                actor=actor)
            raise
        except BaseException as exc:  # noqa: BLE001 - 启动失败也要留下真实原因
            self._record_finish(run_id, exitcode=None, reason="launch_error", signal=None,
                                documentation={"status": "failed",
                                               "detail": f"{type(exc).__name__}: {exc}"}, actor=actor)
            raise WorkflowError(f"子进程启动失败：{type(exc).__name__}: {exc}", code=2) from None

        result = {"ok": True, "run_id": run_id, "prepared_id": preparation["prepared_id"],
                  "plan_id": plan_id, "topic": topic, "argv": argv_actual, "exitcode": exitcode,
                  'execution': execution, 'lifecycle_start': start_sync}
        if finish:
            recorded = self._record_finish(run_id, exitcode=exitcode,
                                           reason=_reason_from_exit(exitcode), signal=_signal(exitcode),
                                           documentation={"status": "skipped",
                                                           "detail": "运行已结束；人读页 / 报告由后续复盘与 scribe 交付"},
                                           actor=actor)
            result.update({"reason": recorded["reason"], "finished": True,
                           "report_pending": True})
        else:
            result.update({"reason": None, "finished": False,
                           "note": "显式 finish=False：run 仍未结束，必须调用 finish 登记真实原因与退出码"})
        return result

    def finish(self, *, run_id, exitcode, reason, documentation=None, actor="main"):
        """登记结束（带外执行 / 先启动后回收时用）。

        三条硬边界：
        1. 文档失败只追加事件，⛔ 不改退出码、⛔ 不阻安全停；
        2. **不结算预算**：真实成本未知时预留（reservation）继续留着，等有证据再结；
        3. ⛔ 不改写已记录的结束原因与退出码（append-only）。
        """
        self._require_write()
        return self._record_finish(run_id, exitcode=exitcode, reason=reason, signal=_signal(exitcode),
                                   documentation=documentation, actor=actor)

    def _invoke(self, argv, env, timeout=None, *, cwd=None):
        """调注入的 runner。`timeout` 只传给**声明接受它**的 runner（默认 runner 接受并执行）。"""
        runner = self.runner
        try:
            parameters = inspect.signature(runner).parameters
            accepts_timeout = "timeout" in parameters or any(
                item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())
        except (TypeError, ValueError):
            accepts_timeout = False
        if accepts_timeout:
            result = runner(list(argv), Path(cwd) if cwd is not None else self.root, dict(env), timeout=timeout)
        else:
            result = runner(list(argv), Path(cwd) if cwd is not None else self.root, dict(env))
        if isinstance(result, bool):
            raise WorkflowError("runner 返回值不可判（布尔不是退出码）", code=2)
        if isinstance(result, int):
            return _normalize_exitcode(result)
        returncode = getattr(result, "returncode", None)
        if not isinstance(returncode, int) or isinstance(returncode, bool):
            raise WorkflowError("runner 返回值不可判（需要整数或带 returncode 的对象）", code=2)
        return _normalize_exitcode(returncode)

    def _record_finish(self, run_id, *, exitcode, reason, signal, documentation, actor):
        if not _filled(run_id):
            raise WorkflowError("run_id 必填", code=2)
        if reason not in FINISH_REASONS:
            raise WorkflowError(f"结束原因必须是 {'/'.join(FINISH_REASONS)}，实际 {reason!r}", code=2)
        self._validate_finish(exitcode, reason)
        doc = _normalize_documentation(documentation)
        with self.store.transaction() as conn:
            run = self._one(conn, "SELECT * FROM wf_runs WHERE run_id=?", (run_id,))
            if run is None:
                raise WorkflowError(f"没有这个运行记录：{run_id}", code=2)
            if self._one(conn, "SELECT 1 AS x FROM wf_finishes WHERE run_id=?", (run_id,)):
                raise WorkflowError(f"运行 {run_id} 已记录结束，append-only：不改写结束原因与退出码")
            try:
                conn.execute(
                    "INSERT INTO wf_finishes(run_id,reason,exitcode,signal,documentation_status,"
                    "documentation_detail,finished_at) VALUES(?,?,?,?,?,?,?)",
                    (run_id, reason, exitcode, signal, doc["status"], doc["detail"], self._now()))
            except sqlite3.IntegrityError as exc:
                raise WorkflowError(f"结束记录写不进去：{exc}", [str(exc)]) from None
            self._event(conn, "finished", topic=run["topic"], plan_id=run["plan_id"],
                        prepared_id=run["prepared_id"], run_id=run_id,
                        payload={"reason": reason, "exitcode": exitcode, "signal": signal,
                                 "documentation": doc, "report_pending": True}, actor=actor)
            if doc["status"] == "failed":
                self._event(conn, "documentation_failed", topic=run["topic"], plan_id=run["plan_id"],
                            prepared_id=run["prepared_id"], run_id=run_id,
                            payload={"detail": doc["detail"],
                                     "note": "文档失败不覆盖原退出码，也不阻止安全停"},
                            actor=actor)
        projection = self._lifecycle_call('sync', 'sync',
            {'topic': run['topic'], 'plan_id': run['plan_id'], 'run_id': run_id, 'stage': 'finish'},
            run['topic'], plan_id=run['plan_id'])
        return {"ok": True, "run_id": run_id, "exitcode": exitcode, "reason": reason,
                "signal": signal, "documentation": doc, "report_pending": True,
                'lifecycle_sync': projection}

    @staticmethod
    def _validate_finish(exitcode, reason):
        if exitcode is not None and (isinstance(exitcode, bool) or not isinstance(exitcode, int)):
            raise WorkflowError("exitcode 必须是整数（被信号终止用负值）或 launch_error 时的 None", code=2)
        if reason == "completed" and exitcode != 0:
            raise WorkflowError(f"reason=completed 的退出码必须是 0，实际 {exitcode}", code=2)
        if reason in ("failed",) and (exitcode == 0 or exitcode is None):
            raise WorkflowError(f"reason={reason} 必须带非零退出码（信号用负值），实际 {exitcode}", code=2)
        if reason == "timeout" and exitcode == 0:
            raise WorkflowError("reason=timeout 的退出码不能是 0（超时被终止应记 None 或负值）", code=2)
        if reason == "launch_error" and exitcode == 0:
            raise WorkflowError("reason=launch_error 的退出码不能是 0", code=2)

    def review(self, *, run_id, record=None, actor="main", require_version=None, **fields):
        """复盘验收：必须已有结束记录；事前计划未被改写；证据哈希；判决落账到专题主文档。"""
        self._require_write()
        if not _filled(run_id):
            raise WorkflowError("run_id 必填", code=2)
        if isinstance(record, (str, Path)):
            path = self._resolve(record, "review")
            if not path.is_file():
                raise WorkflowError(f"复盘记录文件不存在：{record}", code=2)
            raw = path.read_bytes()
            try:
                payload = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkflowError(f"复盘记录不是合法 JSON：{exc}", code=2) from None
            record_sha = _sha_bytes(raw)
            record_source = self._rel(path)
        elif isinstance(record, dict):
            payload = json.loads(json.dumps(record))
            record_sha = _sha_text(canonical_json(payload))
            record_source = "<memory>"
        elif record is None and fields:
            payload = json.loads(json.dumps(fields))
            record_sha = _sha_text(canonical_json(payload))
            record_source = "<kwargs>"
        else:
            raise WorkflowError("review 需要 record（JSON 路径或 dict）", code=2)
        if not isinstance(payload, dict):
            raise WorkflowError("复盘记录必须是 JSON 对象", code=2)
        if require_version is not None and (type(require_version) is not int or require_version != 2):
            raise WorkflowError('require_version 只支持 2 或 None', code=2)
        if require_version == 2 and (type(payload.get('schema_version')) is not int or payload.get('schema_version') != 2):
            raise WorkflowError('新复盘必须使用 schema_version=2，填写下一步判据、成本、负责人及审批需要；历史导入请显式选择兼容模式', code=2)

        problems = []
        for key in ("result_summary", "verdict", "branch", "next_action", "deviations"):
            if not _filled(payload.get(key)):
                problems.append(f"review.{key} 必填且不能是占位")
        if payload.get("verdict") not in REVIEW_VERDICTS:
            problems.append(f"review.verdict 必须是 {'/'.join(REVIEW_VERDICTS)}")
        if payload.get("branch") not in REVIEW_BRANCHES:
            problems.append(f"review.branch 必须是 {'/'.join(REVIEW_BRANCHES)}")
        evidence_refs = []
        evidence = payload.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            problems.append("review.evidence 必须是非空来源清单（结果读数 / 逐题产物）")
        else:
            for index, ref in enumerate(evidence):
                try:
                    evidence_refs.append(self._file_ref(ref, f"review.evidence[{index}]"))
                except WorkflowError as exc:
                    problems.append(str(exc))
        continuation_refs = []

        def continuation_source(ref, label):
            checked = self._file_ref(ref, label)
            continuation_refs.append(dict(checked, purpose='next_action_criterion'))
            return checked

        problems.extend(validate_continuation(payload, file_ref=continuation_source))
        if problems:
            raise WorkflowError("复盘记录不合规：" + "；".join(problems), problems, code=2)
        evidence_refs.extend(continuation_refs)

        with self._with_conn() as conn:
            run = self._one(conn, "SELECT * FROM wf_runs WHERE run_id=?", (run_id,))
            if run is None:
                raise WorkflowError(f"没有这个运行记录：{run_id}", code=2)
            finish = self._one(conn, "SELECT * FROM wf_finishes WHERE run_id=?", (run_id,))
            if finish is None:
                raise WorkflowError("本轮还没有结束记录：运行结束不等于报告交付，先 finish 再 review（J4）")
            if self._one(conn, "SELECT 1 AS x FROM wf_reviews WHERE run_id=?", (run_id,)):
                raise WorkflowError(f"运行 {run_id} 已复盘，append-only：不改写判决")
            topic_doc = self._topic_doc(run["topic"])
            snapshot = json.loads(self._latest_preparation(conn, run["plan_id"], run["topic"])["snapshot_json"])

        latest = self.gov.latest_plan(run["plan_id"])
        if latest is None or int(latest["version"]) != int(run["plan_version"]) \
                or _sha_text(canonical_json(latest["plan"])) != run["plan_sha256"]:
            raise WorkflowError(
                "事前计划在启动之后被改写或重新冻结：复盘只能记录偏差，⛔ 不许回写判据（J6）⇒ "
                "恢复事前版本，或开新轮次")

        frozen_plan = latest["plan"]
        branches = frozen_plan.get("branches") if isinstance(frozen_plan.get("branches"), dict) else {}
        branch = branches.get(payload["branch"])
        expected = branch.get("action") if isinstance(branch, dict) else None
        if expected is None:
            if _is_no_deviation(payload["deviations"]):
                raise WorkflowError(
                    f"这一轮的 branches 没有定义 {payload['branch']} 档：必须在 deviations 里写清偏离原因")
        elif payload["next_action"] != expected and _is_no_deviation(payload["deviations"]):
            raise WorkflowError(
                "下一步偏离了事前分支行动，但 deviations 写的是「没有偏离」：写清为什么改方向")

        update = payload.get("hypothesis_update")
        if not isinstance(update, (dict, str)):
            raise WorkflowError(
                "必须给 hypothesis_update 指向本专题主文档里判决落账的位置（J4）：怎么更新、更新在哪一行")
        try:
            update_ref = self._file_ref(update, "hypothesis_update")
        except WorkflowError as exc:
            raise WorkflowError(f"hypothesis_update 不可用：{exc}", [str(exc)], code=2) from None
        if update_ref["path"] != topic_doc["path"]:
            raise WorkflowError(
                f"hypothesis_update 必须指向本专题主文档 {topic_doc['path']}，实际 {update_ref['path']}")
        if update_ref["sha256"] == snapshot["topic"]["doc"]["sha256"]:
            raise WorkflowError(
                "专题主文档与 prepare 时一模一样：判决没有实际落账（J4）⇒ 先更新主文档再 review")

        with self.store.transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO wf_reviews(run_id,plan_id,topic,hypothesis_id,hypothesis_text,"
                    "verdict,branch,next_action,record_json,record_sha256,evidence_json,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, run["plan_id"], run["topic"], run.get("hypothesis_id"),
                     run.get("hypothesis_text"), payload["verdict"], payload["branch"],
                     payload["next_action"], canonical_json(payload), record_sha,
                     canonical_json(evidence_refs), self._now()))
            except sqlite3.IntegrityError as exc:
                raise WorkflowError(f"复盘记录写不进去（可能已被复盘）：{exc}", [str(exc)]) from None
            self._event(conn, "reviewed", topic=run["topic"], plan_id=run["plan_id"],
                        prepared_id=run["prepared_id"], run_id=run_id,
                        payload={"verdict": payload["verdict"], "branch": payload["branch"],
                                 "next_action": payload["next_action"], "record_sha256": record_sha,
                                 "hypothesis_update": update_ref},
                        actor=actor)
        result = {"ok": True, "run_id": run_id, "plan_id": run["plan_id"],
                  "verdict": payload["verdict"], "branch": payload["branch"],
                  "next_action": payload["next_action"], "evidence": evidence_refs,
                  "record_source": record_source, "record_sha256": record_sha,
                  "hypothesis_update": update_ref,
                  "continuation": project_continuation(payload)}
        # 复盘已落库后才发事件：**通知失败不回滚复盘、也不改退出码**。
        context = {'topic': run['topic'], 'plan_id': run['plan_id'], 'run_id': run_id}
        result['lifecycle_after'] = self._lifecycle_call('after', 'after', context, run_id)
        result['lifecycle_sync'] = self._lifecycle_call('sync', 'sync', context,
                                                      run['topic'], plan_id=run['plan_id'])
        result["event_sink"] = self._notify_review(run, payload, evidence_refs, record_sha, actor)
        return result

    def _notify_review(self, run, payload, evidence_refs, record_sha, actor):
        """把「结果分析 / 证据」通知给注入的 event_sink（例如意图监控）。

        `finish` 只标 `report_pending`，等 hook 反查；真正的「分析已发生」由这里发。
        ⛔ 没有 event_sink 不算失败（它不是闸）；有但失败也只追加一条事件。
        """
        if self.event_sink is None:
            return {"ok": True, "wired": False, "skipped": "未注入 event_sink"}
        base = {"topic": run["topic"], "plan_id": run["plan_id"], "run_id": run["run_id"],
                "prepared_id": run["prepared_id"], "owner_session": run["owner_session"],
                "verdict": payload["verdict"], "branch": payload["branch"],
                "next_action": payload["next_action"], "record_sha256": record_sha,
                "evidence": evidence_refs, "actor": actor}
        sent = {}
        try:
            arity = 2
            try:
                arity = len(inspect.signature(self.event_sink).parameters)
            except (TypeError, ValueError):
                arity = 2
            for kind in ("result_analysis", "evidence"):
                body = dict(base, kind=kind)
                if arity <= 1:
                    outcome = self.event_sink(body)
                else:
                    outcome = self.event_sink(kind, body)
                sent[kind] = _jsonable(outcome)
        except Exception as exc:  # noqa: BLE001 - 通知失败不能undo已落的复盘
            with self.store.transaction() as conn:
                self._event(conn, "event_sink_error", topic=run["topic"], plan_id=run["plan_id"],
                            prepared_id=run["prepared_id"], run_id=run["run_id"],
                            payload={"error": f"{type(exc).__name__}: {exc}",
                                     "note": "复盘已落库；事件通知失败不改变复盘与退出码"},
                            actor=actor)
            return {"ok": False, "wired": True, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "wired": True, "sent": sent}

    def status(self, *, topic=None, plan_id=None):
        """只读投影：准备是否过期 · 运行结束原因/退出码 · 待复盘队列 · 下一轮阻断项。"""
        if not self.db.is_file():
            return {"ok": False, "problems": ["工作区数据库不存在：先 prepare 或运行 init"],
                    "workspace": str(self.root), "db": str(self.db)}
        rows_out = {"ok": True, "problems": [], "workspace": str(self.root), "db": str(self.db),
                    "topic": None, "preparations": [], "runs": [], "pending_reports": [],
                    "documentation_failures": [], "next_round": {"allowed": True, "problems": []}}
        with self._read_conn() as conn:
            if topic is not None:
                row = self._topic_row(conn, topic)
                rows_out["topic"] = row
                if row is None:
                    rows_out["problems"].append(f"专题未登记或不存在：{topic}")
            where, params = [], []
            if topic is not None:
                where.append("topic=?")
                params.append(topic)
            if plan_id is not None:
                where.append("plan_id=?")
                params.append(plan_id)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            preparations = self._all(
                conn, "wf_preparations",
                "SELECT * FROM wf_preparations" + clause + " ORDER BY created_at", tuple(params))
            for prep in preparations:
                expired = self._expiry_problems(prep, conn)
                rows_out["preparations"].append({
                    "prepared_id": prep["prepared_id"], "plan_id": prep["plan_id"],
                    "plan_version": prep["plan_version"], "topic": prep["topic"],
                    "owner_session": prep["owner_session"],
                    "created_at": _iso(prep["created_at"]),
                    "source_fingerprint": prep["source_fingerprint"],
                    "argv": json.loads(prep["argv_json"]),
                    "expired": bool(expired), "expiry_problems": expired})
            runs = self._all(
                conn, "wf_runs",
                "SELECT * FROM wf_runs" + clause + " ORDER BY started_at", tuple(params))
            for run in runs:
                finish = self._one_in(conn, "wf_finishes", "SELECT * FROM wf_finishes WHERE run_id=?",
                                      (run["run_id"],))
                review = self._one_in(conn, "wf_reviews", "SELECT * FROM wf_reviews WHERE run_id=?",
                                      (run["run_id"],))
                item = {"run_id": run["run_id"], "plan_id": run["plan_id"], "topic": run["topic"],
                        "prepared_id": run["prepared_id"], "owner_session": run["owner_session"],
                        "hypothesis_id": run.get("hypothesis_id"),
                        "argv": json.loads(run["argv_json"]),
                        "started_at": _iso(run["started_at"]),
                        "finished": finish is not None,
                        "reason": (finish or {}).get("reason"),
                        "exitcode": (finish or {}).get("exitcode"),
                        "signal": (finish or {}).get("signal"),
                        "documentation_status": (finish or {}).get("documentation_status"),
                        "finished_at": _iso((finish or {}).get("finished_at")),
                        "reviewed": review is not None,
                        "review": (None if review is None else {
                            "verdict": review["verdict"], "branch": review["branch"],
                            "next_action": review["next_action"],
                            "continuation": project_continuation(json.loads(review["record_json"]))})}
                rows_out["runs"].append(item)
                if finish is not None and review is None:
                    rows_out["pending_reports"].append(run["run_id"])
                if finish is not None and (finish.get("documentation_status") == "failed"):
                    rows_out["documentation_failures"].append({
                        "run_id": run["run_id"], "detail": finish.get("documentation_detail")})
            blockers = []
            if topic is not None:
                plan_obj = {}
                if plan_id:
                    latest = self._one_in(conn, "gov_plans", "SELECT plan_json FROM gov_plans WHERE plan_id=?"
                                                             " ORDER BY version DESC LIMIT 1", (plan_id,))
                    if latest is not None:
                        plan_obj = json.loads(latest["plan_json"])
                hypothesis = plan_obj.get("hypothesis") if isinstance(plan_obj, dict) else None
                if isinstance(hypothesis, dict):
                    hypothesis_info = {"id": hypothesis.get("id"), "text": hypothesis.get("text")}
                elif isinstance(hypothesis, str):
                    hypothesis_info = {"id": None, "text": hypothesis}
                else:
                    hypothesis_info = {"id": None, "text": None}
                blockers = self._predecessor_problems(conn, topic, plan_obj, hypothesis_info)
                # 过期只对「你打算起跑的那一轮」有意义：
                # * 给了 plan_id ⇒ 判这个 plan 的最新准备；
                # * 没给 ⇒ 只有当这个专题还一轮都没跑过时才判（首轮材料过期不能起跑）；
                #   已经跑过的轮次在复盘后过期是正常的，不能因此拦下一轮。
                newest = {}
                for item in rows_out["preparations"]:
                    newest[item["plan_id"]] = item  # 已按 created_at 升序，后者覆盖前者
                if plan_id is not None:
                    target = newest.get(plan_id)
                    if target is None:
                        blockers.append(f"plan {plan_id} 还没有 prepare 记录")
                    elif target["expired"]:
                        blockers.append(f"plan {plan_id} 的最新准备记录已过期 ⇒ 重新 prepare")
                elif not rows_out["preparations"]:
                    blockers.append("这一轮还没有 prepare 记录")
                elif not rows_out["runs"] and any(item["expired"] for item in newest.values()):
                    blockers.append("这一轮的准备记录已过期 ⇒ 重新 prepare")
            rows_out["next_round"] = {"allowed": not blockers, "problems": blockers}
        return rows_out


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _normalize_exitcode(exitcode):
    """Windows 的退出码是 32 位无符号：被信号/负数退出会回成 4294967287 这类大数。

    只在**明显是负数补码**时折回去（>= 2**31），正常退出码不受影响。
    """
    if isinstance(exitcode, int) and not isinstance(exitcode, bool) and exitcode >= 2 ** 31:
        return exitcode - 2 ** 32
    return exitcode


def _reason_from_exit(exitcode):
    if exitcode is None:
        return "launch_error"
    if exitcode == 0:
        return "completed"
    if exitcode < 0:
        return "interrupted"
    return "failed"


def _signal(exitcode):
    if isinstance(exitcode, int) and not isinstance(exitcode, bool) and exitcode < 0:
        return f"signal:{-exitcode}"
    return None


def _normalize_documentation(documentation):
    if documentation is None:
        return {"status": "skipped", "detail": ""}
    if not isinstance(documentation, dict):
        raise WorkflowError("documentation 必须是 {status, detail} 对象", code=2)
    status = documentation.get("status", "skipped")
    if status not in ("ok", "failed", "skipped"):
        raise WorkflowError("documentation.status 必须是 ok / failed / skipped", code=2)
    detail = documentation.get("detail") or ""
    if not isinstance(detail, str):
        raise WorkflowError("documentation.detail 必须是文本", code=2)
    if status == "failed" and not detail.strip():
        raise WorkflowError("documentation.status=failed 必须写 detail（哪里失败）", code=2)
    return {"status": status, "detail": detail}


def segments(variants):
    """只留 argv 的选项形状，便于事件里肉眼对账（完整 argv 在 argv_json）。"""
    out = []
    for group in variants or ():
        out.append([item for item in group if str(item).startswith("-")])
    return out


def _jsonable(value):
    """把注入对象的返回值压成可 JSON 化的普通结构（事件与审计要能落盘）。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)
