# GENERATED-BY: managed remote execution wiring, 2026-11-09
# LAST-EDIT: 2026-11-13 19:10 R395 预检 / 起跑记录（与本地 `workflow.launch_record()` 同源，远端不另建一套）
# PURPOSE: 受管远端执行的可复用接线：部署配置只读校验 → 统一工作流闸（准备 / 审计 / 监控 / 执行准入）
#          → Runtime 预算预留与调度器资源租约 → SSHAdapter 提交 → 只认 worker 明确终态且核过 request token
#          才登记结束；query 不可判时保留预算与资源，⛔ 不假 finish、⛔ 不用本地超时冒充远端停机。
# ORIGIN: 复用 kernel/autoresearch/workflow.py（主链闸）、runtime/{service,scheduler,accounts,execution}.py
#         （预算 / 租约 / 幂等执行状态机）与 backends/ssh.py + ssh_worker.py（真远端协议）；本文件不新建预算台账。
# CUMULATIVE-DELTA: 首版。三条硬边界：① 缺部署 / 所有权 / 预算 / 资源租约 / 当前审计监控一律不提交；
#          ② 实际 argv 与冻结计划逐项绑定（再进 RunSpec.execution_config.argv，worker 无 shell 执行）；
#          ③ 终态只能来自远端 worker 记录并用 cancel 探针核 request token，query unknown 时保留租约与预留。
#          2026-11-13 R395：只读 `check()` 全过时返回 `preflight_record`（`kind=preflight`、`launched=false`），
#          claim 事务里 run 落库后返回 `launch_record`（`kind=launch`、带 run_id）；两者都复用
#          `workflow.launch_record()`，⛔ 远端不另写一种记录形状，也⛔ 不在预检里冒充已启动。
#          2026-11-09 只读零副作用：`_readonly_conn` 不再拼裸 `?mode=ro`，委托 `ReadonlyStore`
#          （WAL 读不创建 -shm/-wal）；缺库仍是原来的 RemoteWorkflowError(code=2)。
#          2026-11-09 专题主文档模式默认交给统一 Workflow 按 `.autoresearch/workspace-layout.json`
#          解析（`topic_doc_pattern=None`）；显式自定义 pattern 仍原样传给 Workflow。
"""受管远端执行接线（Workflow 闸 → Runtime RunSpec / 资源租约 → SSHAdapter）。

这一层解决什么问题
==================

`workflow.py` 只会起**本地子进程**，`backends/ssh.py` 只会做**远端请求**，两者之间原本没有接线：
远端执行既不能靠「本地超时」冒充「远端已停止」，也不能绕开统一闸自己去开机器。本文件补的就是这一段：

    prepare / check（统一 Workflow；判据不复制：起跑前复核与本地 start 共用同一段）
      → 起跑闸：准备记录未过期 · 当前审计通过 · 当前监控放行 · 执行准入（预算 / 决策）通过
      → 本层再核：部署已显式启用且 worker 已 provision · 真实 Runtime 所有权 · run 预算预留 · 调度器资源租约
      → claim 写事务内再复核一次（来源 / before 快照 / 审计 / 监控 / 准入）→ 写 run + 起跑投影
      → Runtime.RunSpec（execution_config.argv = 计划声明的实际 argv，worker 无 shell 执行）
      → 紧邻 send 前再复核一次（同一写事务）→ SSHAdapter.submit
      → query / cancel 只认 worker 状态文件里的明确终态，并用 cancel 的 --token 探针核 request token
      → 明确终态才 `Workflow.finish` 登记结束；query 不可判就保留预算与资源，⛔ 不写结束记录

    记录（R395）：`check` 全过 ⇒ preflight 记录（kind=preflight · launched=false · 无 run_id）；
                  claim 里 run 真的落库之后 ⇒ launch 记录（kind=launch · launched=true · 带 run_id）。

判据（不是「跑通就行」）
========================

1. **fail-closed**：部署配置缺失 / `enabled` 不是 true / worker 未 provision / 所有权不可核 /
   run 预算预留不存在 / 资源租约拿不到 / 当前审计或监控或执行准入不通过 ⇒ 一个远端请求都不发。
2. **argv 强绑定**：远端 `execution_config.argv` 只来自闸核过的实际 argv；它必须与冻结计划里的
   `execution_argv` 某一组逐项相等（由 `Workflow.check` 判），并与 prepare 记录的声明相等。
   本层不拼 shell、不加 `sh -c`、不改参数顺序；worker 用 `subprocess.Popen(argv, shell=False)` 执行。
3. **终态判定**：`JobStatus.state` 只是适配层映射。本层要求 ① worker 的 `state.json` 明确记了
   `completed` / `failed` / `cancelled`；② 再用 `adapter.cancel(request_key)`（worker 先核 `--token`）
   回报同一终态 —— 这才算「远端明确停止且 request token 对得上」。两者不一致或不可判 ⇒ `unknown`。
4. **unknown 不假 finish**：`unknown` 时 `wf_runs` 保持未结束、预算预留保持 `reserved`、资源租约保持
   占用，⛔ 不写 `wf_finishes`、⛔ 不释放资源、⛔ 不宣称完成或失败。
5. **安全 cancel 不被报告失败挡**：cancel 先发远端停止（Runtime 的 `ActionPolicy` 不拦 `cancel`，
   暂停与资源类发现也不拦），再把结果登记；登记失败只追加诊断，⛔ 不改「已经发出停止」这一事实。

数据落点（全在共享工作区库里，**没有第二套台账**）
================================================

    .autoresearch/runtime.sqlite3
        wf_preparations / wf_runs / wf_finishes / wf_events   统一主链（本层只 append 一条 run + 结束记录）
        budget_accounts / budget_reservations                  真实预算（run 作用域预留按计划上限幂等预留）
        experiments / ownership / tasks / resources            调度器所有权、任务与资源租约
        exec_attempts / exec_jobs / exec_observations          幂等执行状态机（queue / submit / query / cancel）
        backend_migrations + 实验目录 marker                   Runtime 所有权（缺它 `Runtime._lease` 直接拒）

    .autoresearch/remote/deployment.json                       受管远端部署配置（**默认不存在 = 默认关闭**）
    <state_root>/provision.json                                 SSH worker 的 provision 收据（核部署身份与版本）
    <state_root>/receipts/<hash>.json                           SSHAdapter 自己的请求收据（request_key → token）

已知缺口（⛔ 不要读成已实现）
============================

| 缺口 | 现状 | 需要的边界 |
|---|---|---|
| ~~Admission 与 Runtime 的预算作用域不一致~~ **已修（2026-11-09）** | `runtime.service.Runtime` 新增显式 `budget_scopes`（默认 `('run',)` 完全兼容旧行为）；本文件按 `plan.authorization.scope` 传 `budget_scopes=(scope,)` ⇒ **同一笔** `budget_reservations` 预留被 `Admission` 与 Runtime 各核一次，不再造第二笔 `run` 预留 | 已落地；旧调用方不传参数即保持原语义 |
| **`plan.execution` 不在审计指纹里** | `governance.KEY_FIELDS` 没有 `execution`，改远端运行单元 / 指纹不会让审计失效。本层缓解：远端块整段包含在冻结计划里，而 `wf_preparations.plan_sha256` / `wf_runs.plan_sha256` 绑整份计划 ⇒ prepare 之后改 `execution` 必然对不上（起跑被拒） | 把 `execution` 并入 `KEY_FIELDS` |
| **远端 argv 不在 `JobStatus.evidence` 里** | protocol v2 已把 `request_hash` / `attempt_id` / `token` / `stop_verified` / `stop_evidence` 放进 evidence（安全修复后 query/cancel/collect 都会核绑定）；但远端**实际 argv** 仍只写在 worker 的 `state.json` 里，evidence 不带。本层用「`RunSpec.execution_config.argv` == 计划声明 argv + request_key 绑定」证明请求内容，⛔ 不能说「读回了远端 argv」 | `ssh.py` 把 argv 摘要放进 evidence |
| **远端代码 / 题单 / 环境指纹靠计划声明** | `RunSpec` 要求三个 SHA256，只能由计划里的 `execution.remote.*_fingerprint` 给出；本层不重新计算（远端字节不在这里），**由 worker 在 `start` 时对真实字节重算并拒绝不一致**（DefinitiveFailure）。上传代码 / 题单到运行单元的动作不在本文件 | 独立的远端 provision / 上传步骤 |
| **成本不知道就不结算** | 本层**不**调用 `Accounts.settle`：远端没有可信计量，预留在明确终态后仍保持 `reserved`，等成本证据（`budget settle` 的人工核账边界） | 受信计量适配器 |
| **资源「空闲」只证明受管作业停了** | 默认 `idle_verifier` 只核「持有该租约的那次执行已被 worker 报终态」或「该租约从未提交过远端请求」，⛔ 不证明整台机器空闲 | 真实机器空闲探针 |
| **提交前失败的资源需要人工核空闲** | 若远端明确拒启动（DefinitiveFailure），本地无法证明「一个字节都没跑」，因此**不**自动释放资源（保持 quarantine），交 operator 用 `remote release` 之前的独立证据处理 | 远端 side-effect 证明 |

退出码约定（库与 `remote_cli` 一致）
==================================

    0    通过 / 已登记（含「远端已明确终态且已登记结束」）
    2    输入或配置错误（部署配置坏、计划文件缺失、JSON 坏、句柄找不到）
    3    不可判（远端 query unknown / token 核不过）：预算与资源都保留，没有任何终态结论
    9    规则拒绝（闸或前置不过）—— 一定没有发出远端请求

边界（⛔ 不要读大）
==================

1. **不读凭据**：`deployment.json` 只拿 `key_file` 的**路径**交给系统 ssh，本文件不打开它、不打印它。
2. **不联网除非显式动作**：`check` / `status` 只读本地；`provision` / `submit` / `query` / `cancel` /
   `collect` / `release` 是显式动作（`release` 与 `query` 会发远端探针）。
3. **判据只有一份**：起跑前的只读复核与本地 `start` 共用 `workflow._readonly_launch_recheck`（本轮
   从 `workflow.start` 里抽出来的共享方法，本地启动语义未改）。⛔ 本文件不复制那段判据，也**不**
   复制 lifecycle 语义：起跑投影直接调同一个 `workflow._lifecycle_call('sync', …)`。
   `lifecycle.py` / `decisions_cli.py` / `cli.py` / `docs` 未改。
4. **不判科学**：闸绿 + 远端跑完只说明登记与执行走了，不说明假设成立。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # 让 `python kernel/autoresearch/remote_cli.py` 也能跑
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch.backends.ssh import (  # noqa: E402  (先补 sys.path 再导入)
    WORKER_VERSION,
    SSHAdapter,
    SSHDeployment,
)
from kernel.autoresearch.runtime.accounts import ApprovalError, BudgetError  # noqa: E402
from kernel.autoresearch.runtime.execution import (  # noqa: E402
    DefinitiveFailure,
    RunSpec,
    SharedInput,
    digest,
)
from kernel.autoresearch.runtime.policy import ActionBlocked  # noqa: E402
from kernel.autoresearch.runtime.scheduler import (  # noqa: E402
    FencingError,
    OwnershipConflict,
    PausedError,
    SchedulerError,
)
from kernel.autoresearch.runtime.store import (  # noqa: E402
    ReadonlyStore,
    Store,
    VersionConflict,
    canonical_json,
)
from kernel.autoresearch.runtime.service import BUDGET_SCOPES, Runtime  # noqa: E402
from kernel.autoresearch.workflow import (  # noqa: E402
    RECORD_LAUNCH,
    RECORD_PREFLIGHT,
    Workflow,
    WorkflowError,
    launch_record,
)

__all__ = (
    "DEPLOYMENT_REL",
    "REMOTE_SCOPES",
    "RUN_SCOPE",
    "TERMINAL_REMOTE_STATES",
    "RemoteDeployment",
    "RemoteExecutionTarget",
    "RemoteWorkflow",
    "RemoteWorkflowError",
    "build_run_spec",
    "doctor",
    "fingerprint_probe",
    "load_deployment",
    "read_deployment",
    "remote_block",
    "remote_block_problems",
    "request_key_for",
)

#: 受管远端部署配置的固定相对路径（工作区内）。⛔ 不接受命令行任意路径 / 环境变量启用。
DEPLOYMENT_REL = ".autoresearch/remote/deployment.json"

#: 计划里允许的远端执行作用域。`ssh` 只是历史写法，语义与 `remote` 相同。
REMOTE_SCOPES = ("remote", "ssh")

#: `Runtime._reservation` 只认这个预算作用域。
RUN_SCOPE = "run"

#: worker 的明确终态（`state.json` 里 `state` 的值）。
TERMINAL_REMOTE_STATES = ("completed", "failed", "cancelled")

#: 可接受的「远端确实停了」的证据：**只认** worker 的 `stop_verified is True` 加带绑定的
#: `stop_evidence`。⛔ 只有 `state='completed'/'failed'/'cancelled'` 标签、或只有 `stopped_at`
#: 这类时间戳都不算停止证据：硬超时可能 `_wait_gone` 失败、主进程退出不代表子进程组已停、
#: 时间戳更不证明进程消失。缺证据 ⇒ `unknown`，保留预算与资源，⛔ 不登记终态。
STOP_PROOF_FLAG = "stop_verified"
STOP_PROOF_DETAIL = "stop_evidence"

#: worker 只把首项 python 换成独立环境解释器；摘要要允许这一个受控替换。
_PYTHON_ARGV_NAMES = ("python", "python3", "python.exe", "python3.exe")

#: 部署配置里交给 `SSHDeployment` 的键。
SSH_KEYS = ("host", "user", "port", "key_file", "known_hosts", "remote_dir", "python",
            "state_root", "connect_timeout", "command_timeout", "start_timeout", "cancel_grace")

#: 部署配置允许的键（严格白名单：拼错的键不会被静默忽略）。
DEPLOYMENT_KEYS = frozenset(
    {"enabled", "deployment_id", "owner", "lease_seconds", "note", *SSH_KEYS})

#: 远端运行块允许的键。
REMOTE_BLOCK_KEYS = frozenset(
    {"unit_root", "code_root", "environment_root", "input_root", "output_root", "task_ids",
     "required_artifacts", "code_fingerprint", "task_fingerprint", "environment_fingerprint",
     "environment", "isolated_environment", "shared_mutable_hardlinks", "shared_inputs",
     "resources"})

_REQUIRED_BLOCK_KEYS = ("unit_root", "code_root", "environment_root", "input_root", "output_root",
                        "task_ids", "required_artifacts", "code_fingerprint", "task_fingerprint",
                        "environment_fingerprint", "environment", "isolated_environment",
                        "shared_mutable_hardlinks")

_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SSH_CLIENTS = ("ssh", "ssh.exe", "plink", "plink.exe", "scp", "scp.exe", "sftp")
_MAX_SECONDS = 7 * 24 * 3600.0


class RemoteWorkflowError(WorkflowError):
    """受管远端接线拒绝执行。

    退出码语义：`9` 规则拒绝 / `2` 输入或配置错误 / `3` 不可判（保留预算与资源）。
    ⚠️ 基类只保留 9 与 2，这里额外保留 3，否则「不可判」会被报成输入错误。
    """

    def __init__(self, message, errors=None, code=9):
        super().__init__(message, errors=errors, code=code)
        self.code = 9 if code == 9 else (3 if code == 3 else 2)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jsonable(value):
    """把闸的检查结果摊成可入库 JSON（与 workflow 的 `_jsonable` 同义；不导入私有名）。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _filled(value):
    return isinstance(value, str) and bool(value.strip())


def _is_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return float(value) == float(value) and abs(float(value)) != float("inf")


def _posix_abs(value):
    """规范化 POSIX 绝对路径（远端路径判据；⛔ 不用本地 `Path.is_absolute`）。"""
    if not isinstance(value, str) or not value.startswith("/") or value == "/":
        return False
    if "\\" in value or "//" in value or value.endswith("/"):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/")[1:])


def _under(child, parent):
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def _overlap(left, right):
    return _under(left, right) or _under(right, left)


def _normalize_exitcode(exitcode):
    """Windows 会把负退出码回成 32 位无符号：明显是负数补码时折回去（与 workflow 同义）。"""
    if isinstance(exitcode, int) and not isinstance(exitcode, bool) and exitcode >= 2 ** 31:
        return exitcode - 2 ** 32
    return exitcode


@contextmanager
def _readonly_conn(db_path):
    """只读连接（不建表、不 `BEGIN IMMEDIATE`）：`check` / `status` / 空闲核验共用。

    连接方式委托 `runtime.store.ReadonlyStore`（WAL 库按 sidecar 实况选
    `mode=ro` / `immutable=1`，读不创建 `-shm` / `-wal`）；缺库仍抛原来的
    `RemoteWorkflowError(code=2)`，WAL 歧义态由 `StoreError` 冒给上层按不可判处理。
    """
    path = Path(db_path)
    if not path.is_file():
        raise RemoteWorkflowError(f"工作区数据库不存在：{path}（先 prepare 或运行 init）", code=2)
    conn = ReadonlyStore(path).connect()
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _row(conn, table, sql, args=()):
    if not _table_exists(conn, table):
        return None
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row is not None else None


def _rows(conn, table, sql, args=()):
    if not _table_exists(conn, table):
        return []
    return [dict(row) for row in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------------------
# 部署配置（默认关闭；不读凭据）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteDeployment:
    """一台受管远端机器的部署参数 + 归属身份。

    ⚠️ `ssh.key_file` 只是**路径**：本文件把它原样交给系统 ssh，从不打开它，也不打印内容。
    """

    path: str
    ssh: SSHDeployment
    deployment_id: str
    owner: str
    lease_seconds: float | None
    note: str

    def identity(self):
        """部署身份（进 request_key 的绑定值；⛔ 不含任何凭据内容）。"""
        return {"deployment_id": self.deployment_id, "ssh": self.ssh.identity()}

    def as_public(self):
        identity = self.ssh.identity()
        return {"path": self.path, "deployment_id": self.deployment_id, "owner": self.owner,
                "enabled": self.ssh.enabled is True, "host": identity["host"],
                "user": identity["user"], "port": identity["port"],
                "remote_dir": identity["remote_dir"], "python": identity["python"],
                "known_hosts": identity["known_hosts"],
                "key_file": ("<configured:内容不会被读取>" if identity["key_file"] else None),
                "lease_seconds": self.lease_seconds, "note": self.note}


def read_deployment(workspace):
    """读固定路径的部署配置。返回 `(RemoteDeployment|None, problems)`。**只读，不联网。**"""
    root = Path(workspace).resolve()
    path = root / DEPLOYMENT_REL
    problems: list[str] = []
    if not path.is_file():
        return None, [f"未配置受管远端部署：缺少工作区内 {DEPLOYMENT_REL}"
                      "（配置明确写了 enabled=true 才可能提交；默认关闭）"]
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, [f"部署配置不可读：{exc}"]
    try:
        config = json.loads(raw)
    except ValueError as exc:
        return None, [f"部署配置不是合法 JSON：{exc}"]
    if not isinstance(config, dict):
        return None, ["部署配置必须是 JSON 对象"]
    unknown = sorted(set(config) - DEPLOYMENT_KEYS)
    if unknown:
        problems.append(f"部署配置有未知键：{unknown}")
    if config.get("enabled") is not True:
        problems.append("部署配置 enabled 不是 true：SSH 后端默认关闭，本文件不会连接任何机器")
    deployment_id = config.get("deployment_id")
    if not isinstance(deployment_id, str) or not _DEPLOYMENT_ID.match(deployment_id):
        problems.append("deployment_id 必填且只允许字母/数字/_.:-（≤64）")
    owner = config.get("owner")
    if not isinstance(owner, str) or not _OWNER.match(owner):
        problems.append("owner 必填（调度器所有权身份，字母/数字/_.:@-）")
    lease_seconds = config.get("lease_seconds")
    if lease_seconds is not None and (not _is_number(lease_seconds)
                                      or not 0 < float(lease_seconds) <= _MAX_SECONDS):
        problems.append("lease_seconds 写了就必须是 (0, 7天] 的有限数")
    if "run_series_id" in config:
        problems.append("run_series_id 已废弃：执行预算只用计划 authorization 里的同一笔预留"
                        "（不再需要第二个 run 作用域账户）")
    note = config.get("note") or ""
    if not isinstance(note, str):
        problems.append("note 必须是字符串")
    ssh_fields = {key: config[key] for key in SSH_KEYS if key in config}
    try:
        ssh = SSHDeployment(enabled=config.get("enabled") is True, **ssh_fields)
        ssh.require_enabled()
    except (PermissionError, TypeError) as exc:
        problems.append(f"部署参数不可用：{exc}")
        ssh = None
    if problems or ssh is None:
        return None, problems
    return RemoteDeployment(path=DEPLOYMENT_REL, ssh=ssh, deployment_id=deployment_id,
                            owner=owner, lease_seconds=(None if lease_seconds is None
                                                        else float(lease_seconds)),
                            note=note), []


def load_deployment(workspace):
    """严格读部署配置：任何问题都抛 `RemoteWorkflowError(code=9)`（缺部署 ⇒ 不提交）。"""
    deployment, problems = read_deployment(workspace)
    if deployment is None:
        raise RemoteWorkflowError("受管远端部署未就绪，拒绝提交：" + "；".join(problems),
                                  problems, code=9)
    return deployment


def request_key_for(deployment, *, plan_id, prepared_id, plan_sha256):
    """远端请求键：绑定部署身份 + 计划 + 准备记录，**不含 argv**。

    ⛔ 故意不含 argv：同一个 prepare 记录第二次提交时 argv 若变了，SSHAdapter 的请求收据会按
    `request_hash`（含 spec/argv/timeout/environment）判「同一 request_key 登记过不同内容」并拒发，
    这样「换参数偷偷重发」在适配层就被挡住；本层再把计划声明与 prepare 记录钉死一次。
    """
    payload = {"format": 1, "deployment": deployment.identity(), "plan_id": plan_id,
               "prepared_id": prepared_id, "plan_sha256": plan_sha256}
    return "rw1-" + _sha_text(canonical_json(payload))[:40]


# ---------------------------------------------------------------------------
# 计划的远端运行块
# ---------------------------------------------------------------------------


def remote_execution(plan):
    """取 `plan.execution`（必须是对象且 scope 是受管远端）。"""
    execution = plan.get("execution") if isinstance(plan, dict) else None
    if not isinstance(execution, dict):
        raise RemoteWorkflowError(
            "计划缺顶层 execution 对象：受管远端入口只接受显式声明远端执行落点的计划", code=9)
    scope = execution.get("scope")
    if scope not in REMOTE_SCOPES:
        raise RemoteWorkflowError(
            f"计划 execution.scope={scope!r} 不是受管远端（{'/'.join(REMOTE_SCOPES)}）："
            "本地执行走统一 workflow launch，不要走远端入口", code=9)
    return execution


def remote_block(plan):
    """取并形状检查 `plan.execution.remote`。返回规范化副本（不改原计划）。"""
    execution = remote_execution(plan)
    block = execution.get("remote")
    if not isinstance(block, dict):
        raise RemoteWorkflowError("计划 execution.remote 必须是对象（远端运行单元与指纹声明）", code=9)
    problems = []
    unknown = sorted(set(block) - REMOTE_BLOCK_KEYS)
    if unknown:
        problems.append(f"execution.remote 有未知键：{unknown}")
    for key in _REQUIRED_BLOCK_KEYS:
        if key not in block:
            problems.append(f"execution.remote 缺 {key}")
    if problems:
        raise RemoteWorkflowError("远端运行块不完整：" + "；".join(problems), problems, code=9)
    return {
        "deployment_id": execution.get("deployment_id"),
        "unit_root": block.get("unit_root"), "code_root": block.get("code_root"),
        "environment_root": block.get("environment_root"),
        "input_root": block.get("input_root"), "output_root": block.get("output_root"),
        "task_ids": block.get("task_ids"), "required_artifacts": block.get("required_artifacts"),
        "code_fingerprint": block.get("code_fingerprint"),
        "task_fingerprint": block.get("task_fingerprint"),
        "environment_fingerprint": block.get("environment_fingerprint"),
        "environment": block.get("environment"),
        "isolated_environment": block.get("isolated_environment"),
        "shared_mutable_hardlinks": block.get("shared_mutable_hardlinks"),
        "shared_inputs": block.get("shared_inputs") or [],
        "resources": block.get("resources") or [],
    }


def _timeout_seconds(plan, block=None):
    """远端硬超时：取 `plan.budget.max_seconds`（远端 worker 用它终止进程组）。

    ⛔ 不额外给「远端专用超时」：那会成为绕过预算上界的口子。
    """
    budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
    seconds = budget.get("max_seconds")
    if not _is_number(seconds) or not 0 < float(seconds) <= _MAX_SECONDS:
        raise RemoteWorkflowError(
            "受管远端提交要求 plan.budget.max_seconds 是 (0, 7天] 的有限数（worker 的硬超时上界）",
            code=9)
    return float(seconds)


def remote_block_problems(deployment, plan, argv, resources=None, *, exp_id=None):
    """远端运行块的逐条问题（不抛异常，给 `check` / `doctor` / 执行落点用）。"""
    problems = []
    try:
        block = remote_block(plan)
    except RemoteWorkflowError as exc:
        return list(exc.errors or [str(exc)])
    if not _filled(block["deployment_id"]):
        problems.append("execution.deployment_id 必填：远端运行必须绑定一个部署标识")
    elif deployment is not None and block["deployment_id"] != deployment.deployment_id:
        problems.append(f"计划的 deployment_id={block['deployment_id']!r} 与当前部署 "
                        f"{deployment.deployment_id!r} 不一致：不能把计划提交到另一台机器")
    roots = [block["unit_root"], block["code_root"], block["environment_root"],
             block["input_root"], block["output_root"]]
    labels = ("unit_root", "code_root", "environment_root", "input_root", "output_root")
    for label, value in zip(labels, roots):
        if not _posix_abs(value):
            problems.append(f"execution.remote.{label} 必须是规范化 POSIX 绝对路径（实际 {value!r}）")
    if all(_posix_abs(value) for value in roots):
        for label, value in zip(labels[1:], roots[1:]):
            if not _under(value, block["unit_root"]):
                problems.append(f"execution.remote.{label} 必须在 unit_root 内")
        for index, left in enumerate(roots[1:]):
            for right in roots[index + 2:]:
                if _overlap(left, right):
                    problems.append("代码 / 环境 / 输入 / 输出路径不得重叠")
                    break
    if not isinstance(block["task_ids"], list) or not block["task_ids"]:
        problems.append("execution.remote.task_ids 必须是非空题号数组")
    else:
        bad = [item for item in block["task_ids"]
               if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", item)
               or item in (".", "..")]
        if bad:
            problems.append(f"execution.remote.task_ids 含不安全题号：{bad}")
        elif len(set(block["task_ids"])) != len(block["task_ids"]):
            problems.append("execution.remote.task_ids 不得重复")
    artifacts = block["required_artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        problems.append("execution.remote.required_artifacts 必须是非空相对路径数组")
    else:
        for item in artifacts:
            if not isinstance(item, str) or item.startswith("/") or ":" in item \
                    or "\\" in item or item.startswith("../") or item in ("", ".", ".."):
                problems.append(f"产物清单必须使用安全相对路径：{item!r}")
        if len(set(artifacts)) != len(artifacts):
            problems.append("产物清单不得重复")
    for key in ("code_fingerprint", "task_fingerprint", "environment_fingerprint"):
        if not isinstance(block[key], str) or not _SHA256.match(block[key]):
            problems.append(f"execution.remote.{key} 必须是 64 位小写 SHA256")
    if block["isolated_environment"] is not True:
        problems.append("execution.remote.isolated_environment 必须是 true")
    if block["shared_mutable_hardlinks"] is not False:
        problems.append("execution.remote.shared_mutable_hardlinks 必须是 false")
    environment = block["environment"]
    if not isinstance(environment, dict) or environment.get("kind") not in ("venv", "existing"):
        problems.append("execution.remote.environment 必须是 {'kind': 'venv'|'existing', …}")
    elif environment.get("kind") == "existing":
        if not _posix_abs(environment.get("python")):
            problems.append("existing 环境的 python 必须是规范化 POSIX 绝对路径")
        if any(_overlap(environment.get("python", ""), root) for root in
               (block["code_root"], block["output_root"], block["input_root"])):
            problems.append("existing 解释器不得落在代码 / 输入 / 输出目录里")
    if argv:
        first = Path(str(argv[0])).name.lower()
        if first in _SSH_CLIENTS:
            problems.append(f"argv[0]={argv[0]!r} 是 ssh/scp 客户端：受管远端执行不接受 shell/ssh 包装，"
                            "停止必须来自 worker 的进程记录而不是本地 ssh 进程")
    try:
        _timeout_seconds(plan, block)
    except RemoteWorkflowError as exc:
        problems.extend(exc.errors or [str(exc)])
    if resources is not None:
        if not resources:
            problems.append("必须显式给至少一个资源租约键（--resource）：没有资源租约不得提交")
        else:
            for key in resources:
                if not isinstance(key, str) or not _RESOURCE_KEY.match(key):
                    problems.append(f"资源键不安全：{key!r}（字母/数字/_.:@/-，≤128）")
            if len(set(map(str, resources))) != len(list(resources)):
                problems.append("资源键不得重复")
        declared = block.get("resources") or []
        if declared:
            if not isinstance(declared, list):
                problems.append("execution.remote.resources 必须是数组")
            elif list(declared) != [str(item) for item in resources]:
                problems.append(f"计划声明 resources={declared} 与实际 --resource "
                                f"{[str(item) for item in resources]} 不一致")
    if exp_id is not None and not _filled(exp_id):
        problems.append("实验标识（= plan id）必填")
    if deployment is not None:
        try:
            build_run_spec(deployment, plan, argv or ["python3"], exp_id or "pending-exp",
                           reservation_id="pending-reservation", lease_id="pending-lease",
                           prepared_id="pending-prepared", plan_sha256=None)
        except (RemoteWorkflowError, ValueError) as exc:
            problems.extend(getattr(exc, "errors", None) or [str(exc)])
    return list(dict.fromkeys(problems))


def build_run_spec(deployment, plan, argv, exp_id, *, reservation_id, lease_id,
                   prepared_id=None, plan_sha256=None):
    """构造真实的 `RunSpec`：argv / 超时 / 环境来自冻结计划，租约与预留号来自 Runtime。

    ⛔ 不做任何替换或补全：`execution_config.argv` 必须就是闸核过的实际 argv。
    """
    block = remote_block(plan)
    seconds = _timeout_seconds(plan, block)
    environment = block["environment"]
    config = {"argv": [str(item) for item in argv], "timeout_s": seconds,
              "environment": dict(environment)}
    config_fingerprint = digest(canonical_json(config).encode("utf-8"))
    shared_inputs = []
    for index, item in enumerate(block["shared_inputs"] or []):
        if not isinstance(item, dict):
            raise RemoteWorkflowError(f"execution.remote.shared_inputs[{index}] 必须是对象", code=9)
        try:
            shared_inputs.append(SharedInput(path=item["path"], fingerprint=item["fingerprint"],
                                             read_only=item["read_only"],
                                             immutable=item["immutable"]))
        except KeyError as exc:
            raise RemoteWorkflowError(
                f"execution.remote.shared_inputs[{index}] 缺 {exc}", code=9) from None
    request_key = request_key_for(deployment, plan_id=exp_id, prepared_id=prepared_id,
                                  plan_sha256=plan_sha256)
    try:
        return RunSpec(
            exp_id=exp_id, request_key=request_key,
            code_fingerprint=block["code_fingerprint"],
            task_fingerprint=block["task_fingerprint"],
            environment_fingerprint=block["environment_fingerprint"],
            unit_root=block["unit_root"], code_root=block["code_root"],
            environment_root=block["environment_root"], input_root=block["input_root"],
            output_root=block["output_root"], task_ids=tuple(block["task_ids"]),
            reservation_id=reservation_id, lease_id=lease_id,
            isolated_environment=block["isolated_environment"],
            shared_mutable_hardlinks=block["shared_mutable_hardlinks"],
            required_artifacts=tuple(block["required_artifacts"]),
            shared_inputs=tuple(shared_inputs), execution_config=config,
            execution_config_fingerprint=config_fingerprint,
        ).validate()
    except ValueError as exc:
        raise RemoteWorkflowError(f"RunSpec 不合规：{exc}", [str(exc)], code=9) from None


class RemoteExecutionTarget:
    """`Workflow(execution_target=…)` 的远端实现：让统一闸接受 `scope=remote` 的计划。

    ⚠️ 与 `execution_target.ExecutionTarget` 同形（`resolve(topic, plan, argv) -> dict`），
    只描述「这一轮的执行落点」，**不启动任何东西**。`remote_stop_verified` 在这里恒为 False：
    提交那一刻远端还没停；可核的停机只能在终态时用 worker 记录 + cancel token 探针得出。
    """

    def __init__(self, deployment):
        self.deployment = deployment

    def resolve(self, topic, plan, argv):
        if not isinstance(topic, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", topic) \
                or topic in (".", ".."):
            raise ValueError("专题标识不合法")
        if not isinstance(plan, dict) or not isinstance(argv, list) or not argv \
                or any(not isinstance(a, str) or not a for a in argv):
            raise ValueError("执行目标需要计划与显式argv")
        if self.deployment is None:
            raise ValueError("未配置受管远端部署：不能把远端计划绑定到本地执行")
        problems = remote_block_problems(self.deployment, plan, argv)
        if problems:
            raise ValueError("；".join(problems))
        block = remote_block(plan)
        return {"cwd": block["unit_root"], "env_additions": {}, "provision": None,
                "python": None, "executable": argv[0],
                "execution_scope": "remote", "remote_stop_verified": False,
                "remote_stop_verification": "worker-state+birth+cancel-token",
                "isolation_verified": True, "dependencies_verified": False,
                "deployment_id": self.deployment.deployment_id,
                "remote": {key: block[key] for key in
                           ("unit_root", "code_root", "environment_root", "input_root",
                            "output_root")},
                "note": "远端落点由 worker 在 start 时对真实字节核代码/题单/环境指纹",
                "source": "managed-runtime-ssh"}


# ---------------------------------------------------------------------------
# 主接线
# ---------------------------------------------------------------------------


class RemoteWorkflow:
    """把「已通过统一闸的计划」绑到真实 Runtime 资源租约与 SSHAdapter 的一次受管远端执行。"""

    def __init__(self, workspace, *, db=None, deployment=None, readonly=False, store=None,
                 verifier=None, monitor_check=None, require_monitor_check=True,
                 authorization_check=None, require_authorization_check=True,
                 ownership_verifier=None, idle_verifier=None, audit_verifier=None,
                 evidence=None, clock=None, transport=None, adapter=None, event_sink=None,
                 allow_fixture_gates=False,
                 topic_doc_pattern=None):
        if not isinstance(workspace, (str, Path)):
            raise RemoteWorkflowError("workspace 必须是路径", code=2)
        self.root = Path(workspace).resolve()
        if not self.root.is_dir():
            raise RemoteWorkflowError(f"工作区不存在：{self.root}", code=2)
        self.readonly = bool(readonly)
        self.db = Path(db).resolve() if db is not None else \
            (self.root / ".autoresearch" / "runtime.sqlite3")
        self.clock = clock or time.time
        self.deployment = deployment
        self.store = None
        #: `adapter` 注入只给离线测试（模拟安全修复后会回报 `remote_stop_verified` 的适配层）；
        #: 生产固定 `SSHAdapter(deployment.ssh)`，⛔ 不提供命令行开关。
        if adapter is not None:
            self.adapter = adapter
        elif deployment is None:
            self.adapter = None
        else:
            self.adapter = SSHAdapter(deployment.ssh, transport=transport, clock=self.clock)
        self._runtime = None
        self._runtime_scope = None
        self._runtime_kwargs = {
            "evidence": evidence, "ownership_verifier": ownership_verifier,
            "audit_verifier": audit_verifier,
            "idle_verifier": idle_verifier or self._verify_resource_idle}
        self.allow_fixture_gates = bool(allow_fixture_gates)
        #: claim 与 send 两处复核共用的上下文（prepare 记录 / 冻结计划 / 意图声明 / 准入请求）；
        #: 只在 `submit` 期间存在，⛔ 不参与任何持久化。
        self._launch_context = None
        if not self.readonly:
            self.store = store if store is not None else Store(self.db)
        self.workflow = Workflow(
            self.root, db=self.db, store=self.store, verifier=verifier,
            monitor_check=monitor_check, require_monitor_check=require_monitor_check,
            authorization_check=authorization_check,
            require_authorization_check=require_authorization_check,
            execution_target=RemoteExecutionTarget(deployment), event_sink=event_sink,
            clock=self.clock, readonly=self.readonly, topic_doc_pattern=topic_doc_pattern)

    # -- Runtime（按真实授权作用域构建；同一笔预算预留） ---------------------

    @property
    def runtime(self):
        """当前 Runtime（未按任何作用域构建时是 None）。"""
        return self._runtime

    def _runtime_for(self, scope):
        """按计划的 `authorization.scope` 构建 Runtime：执行层只接受这一个作用域。

        ⚠️ 这是**同一笔**预算预留（`plan.authorization.reservation_id`）：
        `workflow` 的 `Admission` 核一次授权，`Runtime(..., budget_scopes=(scope,))` 在同一张
        `budget_reservations` 上再核一次；⛔ 不再造第二笔 `run` 预留。
        """
        if scope not in BUDGET_SCOPES:
            raise RemoteWorkflowError(
                f"计划 authorization.scope={scope!r} 不在 {'/'.join(BUDGET_SCOPES)} 里："
                "执行层不接受这个预算作用域", code=9)
        if self._runtime is None or self._runtime_scope != scope:
            if self.store is None or self.adapter is None:
                raise RemoteWorkflowError("只读实例或没有部署：不能构建执行 Runtime", code=9)
            self._runtime = Runtime(self.store, self.adapter, clock=self.clock,
                                    budget_scopes=(scope,),
                                    validate_send=self._send_recheck,
                                    **self._runtime_kwargs)
            self._runtime_scope = scope
        return self._runtime

    # -- 写事务内的复核（claim 与 send 两处；共用 workflow 的同一段判据） -------

    def _launch_recheck(self, conn, *, action="launch"):
        """在**已有写事务**里跑统一 Workflow 的只读启动复核（`_readonly_launch_recheck`）。

        ⛔ 不复制判据：来源 / before 快照 / 审计 / 监控 / 执行准入的判据只有 `workflow.py` 一份
        （本地 `start` 与受管远端共用），否则两处会分叉、远端那条会「核了别的」。
        """
        context = self._launch_context
        if context is None:
            raise RemoteWorkflowError("内部错误：远端启动复核缺少上下文（prepare 记录 / 冻结计划）",
                                      code=2)
        result = self.workflow._readonly_launch_recheck(
            conn, context["preparation"], context["frozen"],
            plan_id=context["plan_id"], owner_session=context["owner_session"],
            intent_id=context.get("intent_id"), intent_none=bool(context.get("intent_none")),
            action=action, record=context.get("record") or {})
        self._launch_recheck_result = result
        return result

    def _send_recheck(self, conn, spec):
        """执行层紧邻 `adapter.submit` 之前的最后一道复核（同一个写事务，不嵌套写连接）。

        ⚠️ 这一层挡的是「claim 成功 → 真正发送」之间的变化：批准被撤回、来源被改、
        监控出现新待审、审计被新阻断。没有它，`check` 与 `claim` 里的复核都会被时序缝绕过。
        """
        context = self._launch_context
        if context is None:  # 非远端提交路径（不应该发生）⇒ fail-closed
            return False
        if spec.request_key != context["request_key"] or spec.lease_id != context["lease_token"]:
            return False
        result = self._launch_recheck(conn, action="launch")
        if not result["ok"]:
            first = result["failures"][0]
            raise PermissionError("发送前复核未通过：" + first["message"]
                                  + "；" + "；".join(first["problems"]))
        return True


    # -- 前置（fail-closed）--------------------------------------------------

    def _now(self):
        return float(self.clock())

    def _require_deployment(self):
        if self.deployment is None:
            raise RemoteWorkflowError(
                f"没有受管远端部署：先在工作区写 {DEPLOYMENT_REL}（enabled=true）并通过 remote doctor",
                code=9)
        return self.deployment

    def _require_adapter(self):
        self._require_deployment()
        if self.adapter is None:
            raise RemoteWorkflowError("SSH 适配器未构建（只读模式或构造失败）：不能提交", code=9)
        return self.adapter

    def _require_write(self):
        if self.readonly:
            raise RemoteWorkflowError("只读实例不能提交 / 取消 / 释放：构造时去掉 readonly=True",
                                      code=2)

    def _provision_receipt(self):
        """读 SSH worker 的 provision 收据（本地文件，不联网、不读凭据）。"""
        adapter = self._require_adapter()
        record = {"path": None, "ok": False, "problems": [], "worker_version": None,
                  "worker_sha256": None, "provisioned_at": None}
        path = adapter.state_root / "provision.json"
        record["path"] = str(path)
        if not path.is_file():
            record["problems"].append(
                f"没有 provision 收据：{path}（先显式运行 remote provision 推送 ssh_worker.py）")
            return record
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            record["problems"].append(f"provision 收据不可读：{exc}")
            return record
        if not isinstance(receipt, dict):
            record["problems"].append("provision 收据不是 JSON 对象")
            return record
        record.update({"worker_version": receipt.get("worker_version"),
                       "worker_sha256": receipt.get("worker_sha256"),
                       "provisioned_at": receipt.get("at")})
        try:
            local_sha = digest(adapter.worker_local_path.read_bytes())
        except OSError as exc:
            record["problems"].append(f"本地 ssh_worker.py 不可读：{exc}")
            return record
        if receipt.get("worker_sha256") != local_sha:
            record["problems"].append("远端 worker 字节与本地 ssh_worker.py 不一致：重新 provision")
        if receipt.get("worker_version") != WORKER_VERSION:
            record["problems"].append(
                f"远端 worker 版本 {receipt.get('worker_version')!r} 与本地 {WORKER_VERSION} 不一致")
        if receipt.get("deployment") != self.deployment.ssh.identity():
            record["problems"].append("provision 收据的部署身份与当前配置不一致：重新 provision")
        record["ok"] = not record["problems"]
        return record

    def provisioned(self):
        """部署侧只读视图：worker 是否已 provision（不联网）。"""
        try:
            return self._provision_receipt()
        except RemoteWorkflowError as exc:
            return {"ok": False, "problems": [str(exc)], "path": None}

    def _require_provisioned(self):
        record = self._provision_receipt()
        if not record["ok"]:
            raise RemoteWorkflowError(
                "远端 worker 未就绪，拒绝提交：" + "；".join(record["problems"]),
                record["problems"], code=9)
        # 收据与配置、字节、版本都已核过 ⇒ 下面这次调用只会走「本地收据命中」分支，不联网。
        self.adapter.provision()
        return record

    # -- 闸的附加判据 --------------------------------------------------------

    def _fixture_gates(self):
        return (not self.workflow.require_monitor_check) \
            or (not self.workflow.require_authorization_check)

    def _wiring_problems(self, gate):
        """统一闸自身的接线问题：审计 / 监控 / 执行准入必须是真实的当前状态。

        ⚠️ 夹具闸（`require_monitor_check=False` / `require_authorization_check=False`）默认**拒绝**；
        只有显式 `allow_fixture_gates=True`（仅供离线测试构造，CLI 从不设置）才放行，并会在
        run 的 `gates_json.remote.fixture_gates` 里留下 True 标记。
        """
        checks = gate.get("checks") or {}
        problems = []
        audit = checks.get("audit") or {}
        if audit.get("ok") is not True:
            problems.append(f"当前审计不通过或不可判（decision={audit.get('decision')!r}）")
        monitor = checks.get("monitor") or {}
        if monitor.get("unwired") is True:
            problems.append("监控检查未接线（monitor_check=None）：受管远端提交不放行")
        if monitor.get("fixture") is True and not self.allow_fixture_gates:
            problems.append("监控检查是夹具（require_monitor_check=False）：生产远端提交必须接真实监控；"
                            "只有显式 allow_fixture_gates=True 的离线测试能放行")
        elif monitor.get("ok") is not True:
            problems.append("当前监控不放行："
                            + "；".join(monitor.get("problems") or ["不可判"]))
        authorization = checks.get("authorization") or {}
        if authorization.get("unwired") is True:
            problems.append("执行准入未接线（authorization_check=None）：受管远端提交不放行")
        if authorization.get("fixture") is True and not self.allow_fixture_gates:
            problems.append("执行准入是夹具（require_authorization_check=False）："
                            "生产远端提交必须接 admission.Admission；"
                            "只有显式 allow_fixture_gates=True 的离线测试能放行")
        elif authorization.get("ok") is not True:
            problems.append("执行准入不放行："
                            + "；".join(authorization.get("problems") or ["不可判"]))
        target = checks.get("execution_target") or {}
        if target.get("ok") is not True:
            problems.append("执行落点未通过：" + "；".join(target.get("problems") or ["不可判"]))
        elif target.get("execution_scope") != "remote":
            problems.append(f"执行落点不是受管远端（scope={target.get('execution_scope')!r}）")
        if not _filled(gate.get("plan_id")):
            problems.append("闸结果缺 plan_id")
        return problems

    def _frozen_plan(self, plan_id, version, plan_sha256):
        """从 `gov_plans` 读回**冻结版本**的计划（⛔ 不信调用方再传一遍内容）。"""
        with _readonly_conn(self.db) as conn:
            row = _row(conn, "gov_plans",
                       "SELECT plan_json FROM gov_plans WHERE plan_id=? AND version=?",
                       (plan_id, version))
        if row is None:
            raise RemoteWorkflowError(f"冻结计划不存在：{plan_id} v{version}：先 prepare", code=2)
        plan = json.loads(row["plan_json"])
        if _sha_text(canonical_json(plan)) != plan_sha256:
            raise RemoteWorkflowError(
                "冻结计划字节与准备记录不一致：拒绝绑定远端执行（先重新 prepare）", code=9)
        return plan

    def _bind_plan(self, gate):
        """闸 → (preparation, 冻结计划, 实际 argv)。任何不一致都拒。"""
        preparation = gate.get("preparation")
        if not isinstance(preparation, dict):
            raise RemoteWorkflowError("这一轮没有 prepare 记录：先 prepare（会冻结来源与 argv）", code=9)
        actual = gate.get("actual_argv")
        if not isinstance(actual, list) or not actual:
            raise RemoteWorkflowError("缺实际 argv：受管远端提交必须显式给 `--` 后的完整 argv", code=9)
        declared = json.loads(preparation["argv_json"])
        if actual not in declared:
            raise RemoteWorkflowError(
                "实际 argv 与准备记录的声明不一致（逐项严格比对）："
                f"实际 {actual}；声明 {declared}", code=9)
        plan = self._frozen_plan(preparation["plan_id"], preparation["plan_version"],
                                 preparation["plan_sha256"])
        if gate.get("plan_id") != preparation["plan_id"]:
            raise RemoteWorkflowError("闸结果的 plan_id 与准备记录不一致", code=9)
        return preparation, plan, actual

    def _budget_problems(self, plan, exp_id=None):
        """同一笔 `authorization` 预算的前置检查（只读；⛔ 不 reserve、⛔ 不造第二笔预留）。"""
        problems = []
        authorization = plan.get("authorization")
        if not isinstance(authorization, dict):
            return ["计划缺 authorization：统一闸的预算准入与执行层都无从核对"]
        for key in ("scope", "series_id", "reservation_id"):
            if not _filled(authorization.get(key)):
                problems.append(f"计划 authorization 缺 {key}")
        scope = authorization.get("scope")
        if _filled(scope) and scope not in BUDGET_SCOPES:
            problems.append(f"计划 authorization.scope={scope!r} 不在 {'/'.join(BUDGET_SCOPES)} 里")
        budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
        units = budget.get("max_cost_units")
        if type(units) is not int or units <= 0:
            problems.append("受管远端提交要求 plan.budget.max_cost_units 是正整数"
                            "（执行预留必须 > 0 且覆盖这个上限）")
        problems.extend(self._budget_view_problems(authorization, exp_id, units))
        return problems

    def _ownership_problems(self, exp_id):
        """只读核真实 Runtime 所有权（backend marker + companion）；缺它 `Runtime._lease` 会拒。"""
        try:
            from kernel.autoresearch.runtime.backend import require_runtime_ownership
            require_runtime_ownership(SimpleNamespace(db_path=str(self.db)), exp_id)
        except Exception as exc:  # noqa: BLE001 - 不可核就是不放行
            return [f"实验 {exp_id} 没有可核的 Runtime 所有权（{type(exc).__name__}: {exc}）："
                    "Runtime._lease 会拒，先在部署侧完成受管所有权登记"]
        return []

    def _budget_view_problems(self, authorization, exp_id, units):
        """只读核那笔真实预留：id / exp / scope / status / 额度 / 账号有效期。"""
        reservation_id = authorization.get("reservation_id")
        series_id = authorization.get("series_id")
        scope = authorization.get("scope")
        if not _filled(reservation_id) or not _filled(series_id) or scope not in BUDGET_SCOPES:
            return []  # 缺字段的问题已由调用方报过
        if not self.db.is_file():
            return ["工作区状态库不存在：预算预留无从核对"]
        problems = []
        try:
            with _readonly_conn(self.db) as conn:
                reservation = _row(conn, "budget_reservations",
                                   "SELECT * FROM budget_reservations WHERE id=?", (reservation_id,))
                account = _row(conn, "budget_accounts",
                               "SELECT * FROM budget_accounts WHERE series_id=?", (series_id,))
        except (sqlite3.Error, RemoteWorkflowError) as exc:
            return [f"预算预留只读核对失败：{exc}"]
        if reservation is None:
            return [f"预算预留不存在：{reservation_id}"]
        if account is None:
            return [f"预算账号不存在：{series_id}"]
        if reservation["series_id"] != series_id:
            problems.append(f"预留 {reservation_id} 属于另一个预算系列")
        if exp_id is not None and reservation["exp_id"] != exp_id:
            problems.append(f"预留属于 {reservation['exp_id']!r}，不是本轮 {exp_id!r}")
        if reservation["scope"] != scope:
            problems.append(f"预留作用域 {reservation['scope']!r} 与计划 {scope!r} 不一致")
        if reservation["status"] != "reserved":
            problems.append(f"预留状态是 {reservation['status']!r}（需 reserved）")
        if account["scope"] != scope:
            problems.append(f"预算账号作用域 {account['scope']!r} 与计划 {scope!r} 不一致")
        if not account["active"]:
            problems.append("预算账号已撤回")
        if float(account["expires_at"] or 0) <= self._now():
            problems.append("预算账号已过期")
        if type(units) is int and int(reservation["units"]) < units:
            problems.append(f"预留 {reservation['units']} 单位未覆盖计划上限 {units}")
        return problems

    # -- 只读检查（prepare 视图）-------------------------------------------

    def check(self, *, topic, plan, owner_session, argv, resources=(), intent_id=None,
              intent_none=False):
        """只读：跑统一闸 + 远端绑定检查，返回会提交什么、还缺什么。⛔ 不写盘、不联网。"""
        problems = []
        deployment = self.deployment
        if deployment is None:
            problems.extend(read_deployment(self.root)[1])
        try:
            gate = self.workflow.check(topic=topic, plan=plan, owner_session=owner_session,
                                       argv=argv, intent_id=intent_id, intent_none=intent_none)
        except WorkflowError as exc:
            return {"ok": False, "problems": exc.errors or [str(exc)], "checks": {},
                    "code": exc.code, "submitted": False, "gate_ok": False}
        problems.extend(gate.get("problems") or [])
        problems.extend(self._wiring_problems(gate))
        out = {"ok": False, "problems": [], "code": 9, "submitted": False,
               "gate_ok": bool(gate.get("ok")),
               "checks": _jsonable(gate.get("checks") or {}),
               "fixture_gates": self._fixture_gates(), "plan_id": gate.get("plan_id"),
               "argv": _jsonable(gate.get("actual_argv")), "run_spec": None,
               "request_key": None,
               "deployment": (None if deployment is None else deployment.as_public()),
               "budget": None}
        preparation = gate.get("preparation")
        if not isinstance(preparation, dict):
            problems.append("这一轮还没有 prepare 记录：先 prepare（冻结来源、实现声明与 argv）")
        else:
            frozen = None
            try:
                frozen = self._frozen_plan(preparation["plan_id"], preparation["plan_version"],
                                           preparation["plan_sha256"])
            except RemoteWorkflowError as exc:
                problems.extend(exc.errors or [str(exc)])
            if frozen is not None:
                problems.extend(remote_block_problems(deployment, frozen, out["argv"],
                                                      list(resources),
                                                      exp_id=preparation["plan_id"]))
                problems.extend(self._budget_problems(frozen, preparation["plan_id"]))
                problems.extend(self._ownership_problems(preparation["plan_id"]))
                authorization = frozen.get("authorization") if isinstance(
                    frozen.get("authorization"), dict) else {}
                if deployment is not None:
                    request_key = request_key_for(
                        deployment, plan_id=preparation["plan_id"],
                        prepared_id=preparation["prepared_id"],
                        plan_sha256=preparation["plan_sha256"])
                    try:
                        spec = build_run_spec(deployment, frozen, out["argv"],
                                              preparation["plan_id"],
                                              reservation_id=authorization.get(
                                                  "reservation_id") or "preview-reservation",
                                              lease_id="preview-lease",
                                              prepared_id=preparation["prepared_id"],
                                              plan_sha256=preparation["plan_sha256"])
                        out["run_spec"] = {
                            "request_key": spec.request_key, "exp_id": spec.exp_id,
                            "reservation_id": spec.reservation_id,
                            "authorization_scope": authorization.get("scope"),
                            "lease_id": "preview-lease（提交时换成调度器 token）",
                            "execution_config": _jsonable(spec.execution_config),
                            "unit_root": spec.unit_root, "output_root": spec.output_root,
                            "task_ids": list(spec.task_ids),
                            "required_artifacts": list(spec.required_artifacts),
                            "note": "预算用计划 authorization 的同一笔预留；租约号提交时才产生"}
                    except (RemoteWorkflowError, ValueError) as exc:
                        problems.extend(getattr(exc, "errors", None) or [str(exc)])
                    out["request_key"] = request_key
                    out["budget"] = self._budget_view(frozen, preparation["plan_id"])
                    out["worker"] = self.provisioned()
        out["problems"] = list(dict.fromkeys(problems))
        out["ok"] = not out["problems"]
        out["code"] = 0 if out["ok"] else 9
        out["preflight_record"] = None
        if out["ok"] and out["plan_id"] and out["argv"]:
            # R395：预检记录与本地同源（同一 `launch_record` 构造函数），只在**全部检查通过**时产生。
            # 远端额外把部署 / 预览 run spec 的结论一起绑进 gates；`launched` 恒为 False。
            preflight_gates = dict(out["checks"] or {})
            preflight_gates["remote"] = {
                "ok": True,
                "deployment": (deployment.as_public() if deployment is not None else None),
                "request_key": out.get("request_key"), "run_spec": out.get("run_spec"),
                "budget": out.get("budget"), "worker": out.get("worker"),
                "execution_scope": "remote", "stage": "preview",
                "note": "只读预检：没有发出任何远端请求，也没有占用资源租约",
            }
            try:
                out["preflight_record"] = launch_record(
                    kind=RECORD_PREFLIGHT, plan_id=out["plan_id"],
                    plan_version=(preparation or {}).get("plan_version"),
                    plan_sha256=(preparation or {}).get("plan_sha256"),
                    plan_file_sha256=((out["checks"].get("plan_file") or {}).get("sha256")),
                    argv=out["argv"], gates=preflight_gates, recorded_at=self.workflow._now(),
                    extra={"execution_scope": "remote",
                           "deployment_id": (deployment.deployment_id if deployment else None)})
            except ValueError as exc:
                # 记录构造失败不是「通过」：把它变成一条拒绝，⛔ 不静默吞掉。
                out["ok"] = False
                out["code"] = 9
                out["problems"].append(f"预检记录不可构造：{exc}")
                out["preflight_record"] = None
        return out

    def _budget_view(self, plan, exp_id):
        """执行预算的只读视图（同一笔 authorization 预留；⛔ 不 reserve）。"""
        authorization = plan.get("authorization") if isinstance(plan.get("authorization"),
                                                               dict) else {}
        budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
        view = {"required_units": budget.get("max_cost_units"),
                "reservation_id": authorization.get("reservation_id"),
                "series_id": authorization.get("series_id"),
                "authorization_scope": authorization.get("scope"),
                "exp_id": exp_id, "exists": False, "status": None,
                "note": "Admission 与 Runtime 核同一笔预留（Runtime 显式 budget_scopes=(scope,)）"}
        if not self.db.is_file():
            return view
        try:
            with _readonly_conn(self.db) as conn:
                row = _row(conn, "budget_reservations",
                           "SELECT * FROM budget_reservations WHERE id=?",
                           (authorization.get("reservation_id"),))
                view["exists"] = row is not None
                if row is not None:
                    view.update({"status": row["status"], "units": row["units"],
                                 "exp_id": row["exp_id"], "scope": row["scope"]})
        except (sqlite3.Error, RemoteWorkflowError) as exc:
            view["note"] = f"只读查询失败：{exc}"
        return view

    # -- 提交 ---------------------------------------------------------------

    def submit(self, *, topic, plan, owner_session, argv, resources, intent_id=None,
               intent_none=False, actor="main"):
        """受管远端提交：所有前置通过才发一个远端请求；unknown 时保留预算与资源。"""
        self._require_write()
        if not _filled(actor):
            raise RemoteWorkflowError("actor 必填", code=2)
        deployment = self._require_deployment()
        self._require_adapter()
        if not isinstance(resources, (list, tuple)) or not resources:
            raise RemoteWorkflowError(
                "必须显式给至少一个资源租约键（--resource）：没有资源租约不得提交", code=9)
        resources = [str(item) for item in resources]
        gate = self.workflow.check(topic=topic, plan=plan, owner_session=owner_session,
                                   argv=argv, intent_id=intent_id, intent_none=intent_none)
        problems = list(gate.get("problems") or []) + self._wiring_problems(gate)
        if problems:
            raise RemoteWorkflowError("拒绝提交（没有发出任何远端请求）："
                                      + "；".join(dict.fromkeys(problems)),
                                      list(dict.fromkeys(problems)), code=9)
        preparation, frozen, actual = self._bind_plan(gate)
        exp_id = preparation["plan_id"]
        problems.extend(remote_block_problems(deployment, frozen, actual, resources, exp_id=exp_id))
        problems.extend(self._budget_problems(frozen, exp_id))
        problems.extend(self._ownership_problems(exp_id))
        if problems:
            raise RemoteWorkflowError("拒绝提交（没有发出任何远端请求）："
                                      + "；".join(dict.fromkeys(problems)),
                                      list(dict.fromkeys(problems)), code=9)
        self._require_provisioned()
        authorization = frozen["authorization"]
        scope = authorization["scope"]
        reservation_id = authorization["reservation_id"]
        request_key = request_key_for(deployment, plan_id=exp_id,
                                      prepared_id=preparation["prepared_id"],
                                      plan_sha256=preparation["plan_sha256"])
        timeout = _timeout_seconds(frozen)
        lease_seconds = deployment.lease_seconds or min(timeout + 300.0, _MAX_SECONDS)
        if lease_seconds < timeout:
            raise RemoteWorkflowError(
                f"部署 lease_seconds={lease_seconds} 短于计划硬超时 {timeout}："
                "租约会在作业结束前过期 ⇒ 资源会被误判空闲", code=9)
        # 这一轮已经占用过 run ⇒ J6：不许再起第二次（远端请求也不许）。
        with _readonly_conn(self.db) as conn:
            existing = _row(conn, "wf_runs", "SELECT run_id FROM wf_runs WHERE plan_id=?", (exp_id,))
        if existing is not None:
            raise RemoteWorkflowError(
                f"计划 {exp_id} 已经启动过（run {existing['run_id']}）：同一轮不许再提交远端请求；"
                "新轮次用新 plan id", code=9)
        # ---- 写路径：同一笔授权预留 → 实验状态 → 所有权 / 任务 / 资源租约 ----
        runtime = self._runtime_for(scope)
        reservation = {"reservation_id": reservation_id,
                       "series_id": authorization["series_id"], "scope": scope,
                       "units": frozen["budget"]["max_cost_units"],
                       "note": "Admission 与 Runtime 核同一笔 authorization 预留"}
        # claim 与 send 两处共用的复核上下文（⛔ 内容只来自准备记录与冻结计划）。
        self._launch_context = {
            "preparation": preparation, "frozen": frozen, "plan_id": exp_id,
            "owner_session": owner_session, "intent_id": intent_id,
            "intent_none": bool(intent_none), "request_key": request_key,
            "lease_token": None,
            "record": (gate.get("checks") or {}).get("authorization", {}).get("request") or {}}
        try:
            self._ensure_experiment_ready(runtime, exp_id, frozen.get("goal") or "")
            owner = deployment.owner
            task, claim = self._acquire_lease(runtime, exp_id, owner, resources, lease_seconds,
                                              request_key)
            self._launch_context["lease_token"] = claim["token"]
            try:
                spec = build_run_spec(deployment, frozen, actual, exp_id,
                                      reservation_id=reservation_id,
                                      lease_id=claim["token"],
                                      prepared_id=preparation["prepared_id"],
                                      plan_sha256=preparation["plan_sha256"])
            except RemoteWorkflowError:
                self._abandon_lease(runtime, task, owner, claim, reason="运行规范不合规，未提交")
                raise
            try:
                attempt_id = runtime.queue(spec)
            except (ValueError, PermissionError, SchedulerError) as exc:
                self._abandon_lease(runtime, task, owner, claim, reason="排队被拒：" + str(exc))
                raise RemoteWorkflowError(f"拒绝排队（没有发出任何远端请求）：{exc}", [str(exc)],
                                          code=9) from None
            try:
                run_id, lifecycle_sync, launch = self._claim_run(
                    gate=gate, preparation=preparation, frozen=frozen,
                    topic=topic, exp_id=exp_id, owner_session=owner_session,
                    argv=actual, deployment=deployment,
                    request_key=request_key, reservation=reservation,
                    claim=claim, attempt_id=attempt_id, resources=resources,
                    actor=actor)
            except RemoteWorkflowError:
                # claim 复核未过 ⇒ 这次没有写出任何 run 记录、也没有发远端请求：还回租约。
                self._abandon_lease(runtime, task, owner, claim,
                                    reason="claim 事务内复核未通过", attempt_id=attempt_id)
                raise
        except BaseException:
            self._launch_context = None
            raise
        result = {"ok": False, "run_id": run_id, "attempt_id": attempt_id,
                  "plan_id": exp_id, "topic": topic, "argv": actual,
                  "request_key": request_key, "remote_id": None, "state": None,
                  "submitted": False, "finished": False,
                  "authorization_scope": scope, "lifecycle_sync": lifecycle_sync,
                  "fixture_gates": self._fixture_gates(), "reservation": reservation,
                  "launch_record": launch,
                  "lease": {"task_id": task["id"], "token": claim["token"],
                            "resources": list(claim["resources"]),
                            "lease_until": claim["lease_until"]},
                  "deployment": deployment.as_public()}
        try:
            row = runtime.submit(attempt_id)
        except (PermissionError, ActionBlocked) as exc:
            # 事务内校验器先拒（含「紧邻发送前的复核」）⇒ 适配器还没被调用，远端没有任何请求。
            self._record_launch_error(run_id, topic, f"提交事务里的校验器拒了：{exc}", actor)
            self._abandon_lease(runtime, task, owner, claim, reason="提交事务被拒",
                                attempt_id=attempt_id)
            result.update({"problems": [str(exc)], "state": "failed",
                           "reason": "launch_error"})
            return result
        except DefinitiveFailure as exc:
            # 远端明确拒绝启动（代码/题单/环境指纹不符等）：没有跑起来的作业。
            self._record_launch_error(run_id, topic,
                                      f"远端明确拒绝启动（{type(exc).__name__}: {exc}）", actor)
            self._abandon_lease(runtime, task, owner, claim, reason="远端明确拒绝启动",
                                attempt_id=attempt_id)
            result.update({"problems": [str(exc)], "state": "failed",
                           "reason": "launch_error"})
            return result
        except (BudgetError, ValueError, SchedulerError) as exc:
            self._record_launch_error(run_id, topic, f"提交被拒（{type(exc).__name__}: {exc}）",
                                      actor)
            self._abandon_lease(runtime, task, owner, claim, reason="提交被拒",
                                attempt_id=attempt_id)
            result.update({"problems": [str(exc)], "state": "failed",
                           "reason": "launch_error"})
            return result
        except BaseException as exc:  # 传输中断 / 中断信号：远端状态不可判 ⇒ 保留一切
            self._record_unknown(run_id, topic, "submit", exc, actor)
            self._emit(run_id, topic, "remote_submit_unknown",
                       {"attempt_id": attempt_id, "error": f"{type(exc).__name__}: {exc}",
                        "note": "保留预算预留与资源租约；用 remote query 复核后再定终态"}, actor)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            result.update({"problems": [f"{type(exc).__name__}: {exc}"], "state": "unknown",
                           "note": "提交响应不可判：远端可能已经在跑；预算与资源都保留"})
            return result
        finally:
            self._launch_context = None
        row = dict(row)
        row["request_key"] = request_key
        result.update(self._interpret_attempt(row))
        if row.get("remote_state") in TERMINAL_REMOTE_STATES:
            result.update(self._finish_if_terminal(run_id, row, actor, argv=actual,
                                                   block=remote_block(frozen)))
        if result.get("finished"):
            result["ok"] = True
        elif result.get("state") == "unknown":
            result["note"] = ("远端状态不可判：预算预留与资源租约保留，⛔ 不写结束记录；"
                              "用 remote query 复核")
        else:
            result["ok"] = True
            result.setdefault("note", "远端已受理；用 remote query / status 跟进终态")
        return result

    # -- 内部写路径 ----------------------------------------------------------

    def _ensure_experiment_ready(self, runtime, exp_id, goal):
        """`planned → awaiting_approval → ready`：`Runtime.prepare` 要求活的预算预留。"""
        scheduler = runtime.scheduler
        try:
            scheduler.activate(exp_id, goal=goal)
        except PausedError as exc:
            raise RemoteWorkflowError(
                f"调度器暂停了新实验（{exc}）：受管远端提交不放行", [str(exc)], code=9) from None
        for _ in range(3):
            row = self.store.get_experiment(exp_id)
            if row is None:
                raise RemoteWorkflowError(f"实验登记失败：{exp_id}", code=9)
            state = row.get("state")
            try:
                if state == "planned":
                    runtime.request_approval(exp_id, row["version"])
                elif state == "awaiting_approval":
                    runtime.prepare(exp_id, row["version"])
                elif state == "ready":
                    return row
                else:
                    raise RemoteWorkflowError(
                        f"实验 {exp_id} 的状态是 {state!r}：这一轮已经提交/结束，不许再开远端请求",
                        code=9)
            except (ApprovalError, BudgetError) as exc:
                raise RemoteWorkflowError(
                    f"实验准备被预算层拒绝（{type(exc).__name__}: {exc}）："
                    "没有同一笔授权的活预留不得提交", [str(exc)], code=9) from None
            except (VersionConflict, SchedulerError):
                continue
        raise RemoteWorkflowError(f"实验 {exp_id} 状态机在重试后仍不可判：不放行", code=9)

    def _acquire_lease(self, runtime, exp_id, owner, resources, lease_seconds, request_key):
        """所有权 → 付费任务 → 资源租约（token 就是 RunSpec.lease_id）。"""
        scheduler = runtime.scheduler
        try:
            taken = scheduler.takeover(exp_id, owner, lease_seconds=lease_seconds)
        except (OwnershipConflict, SchedulerError, ValueError) as exc:
            raise RemoteWorkflowError(
                f"所有权不可取得（{type(exc).__name__}: {exc}）：受管远端提交不放行",
                [str(exc)], code=9) from None
        generation = taken["generation"]
        with _readonly_conn(self.db) as conn:
            used = _rows(conn, "tasks", "SELECT idempotency_key FROM tasks WHERE exp_id=?",
                         (exp_id,))
        key = f"rw:{request_key}:{len(used) + 1}"
        try:
            task = scheduler.enqueue(exp_id, key,
                                     {"request_key": request_key, "plan_id": exp_id,
                                      "deployment_id": self.deployment.deployment_id},
                                     owner, generation, paid=True, resources=resources,
                                     starts_experiment=True)
        except (SchedulerError, ValueError) as exc:
            raise RemoteWorkflowError(f"调度器拒绝排任务（{type(exc).__name__}: {exc}）",
                                      [str(exc)], code=9) from None
        try:
            claim = scheduler.claim(exp_id, owner, generation,
                                    f"remote:{self.deployment.deployment_id}",
                                    lease_seconds=lease_seconds)
        except (SchedulerError, ValueError) as exc:
            self._cancel_task(runtime, task, owner, generation, "claim raised")
            raise RemoteWorkflowError(f"资源租约不可核（{type(exc).__name__}: {exc}）",
                                      [str(exc)], code=9) from None
        if claim is None:
            self._cancel_task(runtime, task, owner, generation, "no resource lease available")
            raise RemoteWorkflowError(
                f"拿不到资源租约：资源 {resources} 被占用 / 新实验被暂停 / 任务不可 claim ⇒ 不放行",
                [f"resources={resources}"], code=9)
        return task, claim

    def _cancel_task(self, runtime, task, owner, generation, reason):
        try:
            runtime.scheduler.cancel(task["id"], owner, generation, reason=str(reason))
            return True
        except (SchedulerError, ValueError, FencingError):
            return False

    def _abandon_lease(self, runtime, task, owner, claim, *, reason, attempt_id=None):
        """提交前的失败清理：取消任务；**只有在能证明没发过远端请求时**才核空闲释放资源。

        ⛔ 证明不了就保持资源占用（quarantine），把结论交给 operator —— 不假装机器空闲。
        """
        out = {"released": False, "reason": reason, "resources": []}
        if not self._cancel_task(runtime, task, owner, claim["generation"], reason):
            out["cancel_error"] = "取消任务失败（租约保持，等它按过期规则处理）"
            return out
        if not self._no_remote_request(attempt_id):
            out["note"] = ("无法证明未发出远端请求：资源保持占用（quarantine），"
                           "需独立证据核空闲后再放行")
            return out
        evidence = {"kind": "lease_released_before_submit", "task_id": claim["id"],
                    "token": claim["token"], "attempt_id": attempt_id, "reason": reason}
        for resource_key in claim["resources"]:
            try:
                runtime.scheduler.confirm_resource_idle(
                    resource_key, dict(evidence, resource_key=resource_key),
                    actor="remote-workflow")
                out["resources"].append(resource_key)
            except (SchedulerError, ValueError) as exc:
                out.setdefault("idle_errors", []).append(f"{resource_key}: {exc}")
        out["released"] = bool(out["resources"])
        return out

    def _no_remote_request(self, attempt_id):
        """本地可证的「没发过远端请求」：该执行没有任何 submit 观察。"""
        if not attempt_id:
            return True
        try:
            with _readonly_conn(self.db) as conn:
                rows = _rows(conn, "exec_observations",
                             "SELECT operation FROM exec_observations WHERE attempt_id=?",
                             (attempt_id,))
        except (sqlite3.Error, RemoteWorkflowError):
            return False
        return not any(item["operation"] == "submit" for item in rows)

    def _claim_run(self, *, gate, preparation, frozen, topic, exp_id, owner_session, argv,
                   deployment, request_key, reservation, claim, attempt_id, resources, actor):
        """在写事务里做与本地同形的只读复核，然后 append 一条 `wf_runs` 并做一次起跑投影。

        ⚠️ 为什么由本层写 run：`workflow.start()` 是「本地起子进程」的入口，远端不发子进程，
        所以 run 记录与起跑投影这两步在这里做；列的语义、DB 触发器（同专题 / 同计划只能有一个
        未结束 run + append-only）、以及投影调用（`workflow._lifecycle_call('sync', …)`）与
        `workflow.start()` **完全同形**，判据不复制（共用 `_readonly_launch_recheck`）。
        """
        gates = dict(gate.get("checks") or {})
        gates["remote"] = {
            "deployment": deployment.identity(), "request_key": request_key,
            "reservation_id": reservation["reservation_id"],
            "series_id": reservation["series_id"], "units": reservation["units"],
            "authorization_scope": reservation["scope"],
            "budget_check": "Admission 与 Runtime(accepted budget_scopes=(scope,)) 核同一笔预留",
            "lease_id": claim["token"], "task_id": claim["id"],
            "resources": resources, "attempt_id": attempt_id,
            "execution_scope": "remote", "remote_stop_verified": False,
            "stop_verification": "worker-state+birth+cancel-token",
            "fixture_gates": self._fixture_gates(),
            "note": "远端终态只在 worker 明确报终态且 cancel token 探针通过后登记",
        }
        hypothesis = gate.get("hypothesis") if isinstance(gate.get("hypothesis"), dict) else {}
        with self.store.transaction() as conn:
            # 闸 → 取资源租约 → 排队之后，可能有人改来源 / 撤回预算 / 加监控触发 / 新阻断审计。
            # 这里跑**同一段**只读复核（与本地 start 共用 `_readonly_launch_recheck`）：计划版本、
            # before 快照、来源哈希、审计、监控、执行准入，任一不过就一个远端请求都不发。
            recheck = self._launch_recheck(conn, action="launch")
            gates.update(recheck["checks"])
            if not recheck["ok"]:
                first = recheck["failures"][0]
                raise RemoteWorkflowError(
                    "拒绝提交（没有发出任何远端请求）：" + first["message"],
                    list(first["problems"]), code=9)
            if conn.execute("SELECT 1 FROM wf_runs WHERE plan_id=?", (exp_id,)).fetchone():
                raise RemoteWorkflowError("拒绝提交：这个计划已经有 run（J6）", code=9)
            count = conn.execute("SELECT COUNT(*) AS n FROM wf_runs").fetchone()["n"]
            run_id = "RUN-" + _sha_text(canonical_json(
                [preparation["prepared_id"], argv, count, self._now()]))[:16]
            conn.execute(
                "INSERT INTO wf_runs(run_id,prepared_id,plan_id,plan_version,topic,owner_session,"
                "hypothesis_id,hypothesis_text,argv_json,cwd,gates_json,source_fingerprint,"
                "plan_sha256,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, preparation["prepared_id"], exp_id, preparation["plan_version"], topic,
                 owner_session, hypothesis.get("id"), hypothesis.get("text"),
                 canonical_json(argv), remote_block(frozen)["unit_root"],
                 canonical_json(_jsonable(gates)), preparation["source_fingerprint"],
                 preparation["plan_sha256"], self._now()))
            conn.execute(
                "INSERT INTO wf_events(kind,topic,plan_id,prepared_id,run_id,payload_json,actor,"
                "created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("launched", topic, exp_id, preparation["prepared_id"], run_id,
                 canonical_json(_jsonable({
                     "argv": argv, "cwd": remote_block(frozen)["unit_root"],
                     "execution_scope": "remote", "remote_stop_verified": False,
                     "request_key": request_key, "attempt_id": attempt_id,
                     "reservation_id": reservation["reservation_id"],
                     "authorization_scope": reservation["scope"],
                     "lease_id": claim["token"], "task_id": claim["id"],
                     "resources": resources, "deployment_id": deployment.deployment_id,
                     "fixture_gates": self._fixture_gates(),
                     "note": "受管远端：终态只能来自 worker 记录 + cancel token 探针"})),
                 actor, self._now()))
            # R395：run 记录在同一事务里提交 ⇒ 这才是「真的起跑了」。记录与本地 `start()` 同源，
            # 绑定计划 sha256 / 实际 argv / UTC 时间 / 本次 gates（含 remote 段）。
            record = launch_record(
                kind=RECORD_LAUNCH, plan_id=exp_id,
                plan_version=preparation["plan_version"],
                plan_sha256=preparation["plan_sha256"],
                plan_file_sha256=(gate.get("checks", {}).get("plan_file") or {}).get("sha256"),
                argv=argv, gates=gates, recorded_at=self._now(), run_id=run_id,
                extra={"prepared_id": preparation["prepared_id"],
                       "execution_scope": "remote", "remote_stop_verified": False,
                       "cwd": remote_block(frozen)["unit_root"],
                       "deployment_id": deployment.deployment_id,
                       "request_key": request_key, "attempt_id": attempt_id})
        # 起跑投影：与 `workflow.start()` 同形 —— run 落库提交后立刻 sync 一次，
        # 让「running 事件 / AI_WORK / 起跑收据」在远端还跑着的时候就反映出来，而不是等终态。
        # ⚠️ 只是投影：`_lifecycle_call` 永不抛；失败只写 `lifecycle_error` 诊断，
        # ⛔ 不改退出码、⛔ 不伪终态、⛔ 不阻安全 cancel。
        lifecycle_sync = self.workflow._lifecycle_call(
            "sync", "sync",
            {"topic": topic, "plan_id": exp_id, "prepared_id": preparation["prepared_id"],
             "run_id": run_id, "stage": "start"},
            topic, plan_id=exp_id)
        return run_id, lifecycle_sync, record

    def _record_launch_error(self, run_id, topic, detail, actor):
        try:
            self.workflow.finish(run_id=run_id, exitcode=None, reason="launch_error",
                                 documentation={"status": "skipped",
                                                "detail": "远端未启动成功：" + detail},
                                 actor=actor)
        except WorkflowError as exc:
            self._emit(run_id, topic, "remote_finish_failed",
                       {"error": str(exc), "note": "登记失败不改事实：远端未启动成功"}, actor)

    def _record_unknown(self, run_id, topic, operation, exc, actor):
        self._emit(run_id, topic, "remote_unknown",
                   {"operation": operation, "error": f"{type(exc).__name__}: {exc}",
                    "note": "不写结束记录；预算预留与资源租约保持"}, actor)

    def _emit(self, run_id, topic, kind, payload, actor):
        try:
            with self.store.transaction() as conn:
                row = conn.execute("SELECT plan_id,prepared_id FROM wf_runs WHERE run_id=?",
                                   (run_id,)).fetchone()
                conn.execute(
                    "INSERT INTO wf_events(kind,topic,plan_id,prepared_id,run_id,payload_json,"
                    "actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (kind, topic, row["plan_id"] if row else None,
                     row["prepared_id"] if row else None, run_id,
                     canonical_json(_jsonable(payload)), actor, self._now()))
        except (sqlite3.Error, RemoteWorkflowError):
            pass  # 诊断写不进去也不能改变已经发生的事实

    # -- 终态 ---------------------------------------------------------------

    def _interpret_attempt(self, row):
        """把执行记录翻成对外状态（⛔ 不推断终态）。"""
        remote_state = row.get("remote_state")
        out = {"exec_attempt_state": row.get("state"), "remote_state": remote_state,
               "submitted": bool(row.get("remote_id")) or row.get("state") not in ("queued",)}
        if row.get("remote_id"):
            out["remote_id"] = row["remote_id"]
        if remote_state in TERMINAL_REMOTE_STATES:
            out["state"] = remote_state
        elif remote_state == "running" or row.get("state") in ("running", "submitting"):
            out["state"] = "running"
        elif row.get("state") == "unknown" or remote_state == "unknown":
            out["state"] = "unknown"
        else:
            out["state"] = remote_state or row.get("state")
        return out

    def _terminal_fields(self, remote_state, evidence):
        """worker 终态 → `(reason, exitcode)`；不可判就抛错，⛔ 不猜。"""
        evidence = evidence if isinstance(evidence, dict) else {}
        exitcode = evidence.get("exitcode")
        signal = evidence.get("signal")
        exit_ok = isinstance(exitcode, int) and not isinstance(exitcode, bool)
        if remote_state == "completed":
            if exit_ok and exitcode != 0:
                raise RemoteWorkflowError(
                    f"worker 报 completed 但 exitcode={exitcode}：终态与退出码矛盾，不登记", code=3)
            return "completed", 0
        if remote_state == "cancelled":
            return "stopped_by_user", None
        if evidence.get("timed_out") is True:
            return "timeout", (exitcode if exit_ok and exitcode != 0 else None)
        if isinstance(signal, int) and not isinstance(signal, bool) and signal > 0:
            return "failed", -int(signal)
        if exit_ok:
            if exitcode == 0:
                raise RemoteWorkflowError(
                    "worker 报 failed 但 exitcode=0：终态与退出码矛盾，不登记", code=3)
            return "failed", _normalize_exitcode(exitcode)
        # 远端 launch_error（例如 worker 起进程失败）：没有退出码，按 launch_error 记。
        return "launch_error", None

    @staticmethod
    def _stop_proof(evidence, row):
        """停止证明：**只认** `stop_verified is True` + 带绑定的 `stop_evidence`。

        ⛔ `stopped_at` 之类时间戳、只有终态标签都不算：时间戳不证明进程（组）消失。
        返回 `(proof, None)` 或 `(None, 原因)`。
        """
        evidence = evidence if isinstance(evidence, dict) else {}
        if evidence.get(STOP_PROOF_FLAG) is not True:
            return None, f"{STOP_PROOF_FLAG} 不是 true（只有终态标签/时间戳不算停止）"
        detail = evidence.get(STOP_PROOF_DETAIL)
        if not isinstance(detail, dict) or not detail:
            return None, f"缺 {STOP_PROOF_DETAIL}：没有可核的停止细节"
        method = detail.get("method")
        if not _filled(method):
            return None, f"{STOP_PROOF_DETAIL} 缺 method：不能说明是怎么确认停止的"
        # 有同一进程标识就必须与运行记录一致（防止把别的进程/别的 attempt 的停止当成这次）。
        for key in ("pid", "birth"):
            if key in detail and key in evidence and detail.get(key) != evidence.get(key):
                return None, f"{STOP_PROOF_DETAIL}.{key} 与运行记录不一致"
        return {"method": method, "detail": _jsonable(detail)}, None

    @staticmethod
    def _argv_digests(argv, block):
        """可接受的「实际启动 argv」摘要集合（worker 只替换首项 python 解释器）。"""
        argv = [str(item) for item in argv]
        candidates = [argv]
        if argv and Path(argv[0]).name.lower() in _PYTHON_ARGV_NAMES:
            interpreters = []
            environment = block.get("environment") if isinstance(block.get("environment"), dict) \
                else {}
            if environment.get("kind") == "existing" and _filled(environment.get("python")):
                interpreters.append(environment["python"])
            root = block.get("environment_root")
            if _posix_abs(root):
                interpreters += [root + "/bin/python3", root + "/bin/python",
                                 root + "/Scripts/python.exe", root + "/python.exe"]
            for interpreter in interpreters:
                candidates.append([interpreter] + argv[1:])
        return {_sha_text(canonical_json(candidate)) for candidate in candidates}

    def _argv_proof(self, evidence, argv, block):
        """核 `argv_sha256` 与冻结 argv 一致；缺摘要 / 不符一律拒（⛔ 不猜）。"""
        digest = evidence.get("argv_sha256") if isinstance(evidence, dict) else None
        if not isinstance(digest, str) or not _SHA256.match(digest):
            return None, "远端回执缺 argv_sha256：不能证明跑的是计划声明的 argv"
        expected = self._argv_digests(argv, block)
        if digest not in expected:
            return None, (f"远端 argv 摘要 {digest[:12]}… 与冻结 argv 不符"
                          f"（可接受摘要 {sorted(item[:12] + '…' for item in expected)}）")
        return {"argv_sha256": digest, "accepted": len(expected)}, None

    def _verified_terminal(self, row, *, request_key, argv, block):
        """终态核验四条一起才算：

        ① worker 记录明确终态；② cancel `--token` 探针回报同一终态；
        ③ 探针回执带**本次 attempt** 的绑定（attempt_id 与本执行记录一致、request_hash 合法）；
        ④ 停止证明（`stop_verified is True` + 绑定 stop_evidence）与 argv 摘要都核过。

        ⛔ 只有 `state='failed'` 或被换成 `stopped_at` 的时间戳都不算停了；缺一条就按 `unknown`
        处理：不写结束记录、不释放资源。
        """
        remote_state = row.get("remote_state")
        if remote_state not in TERMINAL_REMOTE_STATES:
            return False, {"reason": f"worker 状态不是明确终态（{remote_state!r}）",
                           "state": remote_state}
        if self.adapter is None:
            return False, {"reason": "没有适配器：不能核 request token"}
        probe = self.adapter.cancel(request_key, row.get("remote_id"))
        evidence = probe.evidence if isinstance(probe.evidence, dict) else {}
        if probe.state not in TERMINAL_REMOTE_STATES:
            return False, {"reason": "cancel token 探针没有回报明确终态（token 或状态不可判）",
                           "state": probe.state, "evidence": _jsonable(evidence)}
        if probe.state != remote_state:
            return False, {"reason": f"探针终态 {probe.state!r} 与 worker 记录 "
                                     f"{remote_state!r} 不一致", "state": probe.state}
        if probe.remote_id and row.get("remote_id") and probe.remote_id != row["remote_id"]:
            return False, {"reason": "探针回报的 remote_id 与记录不一致", "state": probe.state}
        attempt_id = evidence.get("attempt_id")
        if not _filled(attempt_id) or attempt_id != row.get("id"):
            return False, {"reason": "探针回报的 attempt_id 与本执行记录不一致："
                                     "不能把别的 attempt 的停止当成这次",
                           "state": probe.state, "probe_attempt": attempt_id}
        if not isinstance(evidence.get("request_hash"), str) \
                or not _SHA256.match(evidence["request_hash"]):
            return False, {"reason": "探针回执缺合法 request_hash：不能证明是这次请求的记录",
                           "state": probe.state}
        stop, why = self._stop_proof(evidence, row)
        if stop is None:
            return False, {"reason": "没有停止证明：" + str(why),
                           "state": probe.state, "evidence": _jsonable(evidence)}
        argv_proof, why = self._argv_proof(evidence, argv, block)
        if argv_proof is None:
            return False, {"reason": why, "state": probe.state,
                           "evidence": _jsonable(evidence)}
        return True, {"state": remote_state,
                      "remote_id": probe.remote_id or row.get("remote_id"),
                      "attempt_id": attempt_id,
                      "request_hash": evidence.get("request_hash"),
                      "stop_proof": stop,
                      "argv_sha256": argv_proof["argv_sha256"],
                      "evidence": _jsonable(evidence),
                      "note": "worker 明确终态 + cancel --token 探针 + attempt/argv/停止证明"}

    def _run_expectation(self, run):
        """从 run 记录取冻结 argv 与远端运行块（⛔ 不信调用方另传一份内容）。"""
        plan = self._frozen_plan(run["plan_id"], run["plan_version"], run["plan_sha256"])
        return json.loads(run["argv_json"]), remote_block(plan)

    def _finish_if_terminal(self, run_id, row, actor, *, argv, block):
        """只有核过 token / attempt / argv / 停止证明的明确终态才登记结束。"""
        request_key = row.get("request_key")
        if not _filled(request_key):
            return {"finished": False, "terminal_verified": False,
                    "note": "执行记录缺 request_key：不能核 token"}
        ok, proof = self._verified_terminal(row, request_key=request_key, argv=argv, block=block)
        if not ok:
            return {"finished": False, "terminal_verified": False, "terminal_check": proof,
                    "note": "终态未核过：不写结束记录（预算与资源保留）"}
        try:
            reason, exitcode = self._terminal_fields(row.get("remote_state"),
                                                     proof.get("evidence"))
        except RemoteWorkflowError as exc:
            return {"finished": False, "terminal_verified": False, "terminal_check": proof,
                    "problems": [str(exc)], "note": "终态与退出码矛盾：不写结束记录"}
        documentation = {"status": "skipped",
                         "detail": "远端终态由 worker 记录 + cancel token 探针核过；"
                                   "人读页与复盘仍走 workflow review / scribe"}
        try:
            recorded = self.workflow.finish(run_id=run_id, exitcode=exitcode, reason=reason,
                                            documentation=documentation, actor=actor)
        except WorkflowError as exc:
            return {"finished": False, "terminal_verified": True, "terminal_check": proof,
                    "problems": [f"结束记录写不进去：{exc}"],
                    "note": "远端确实停了，但本地登记失败（append-only 已有记录？）"}
        return {"finished": True, "terminal_verified": True, "terminal_check": proof,
                "finish": {"reason": recorded.get("reason"),
                           "exitcode": recorded.get("exitcode"),
                           "signal": recorded.get("signal")},
                "reason": recorded.get("reason"), "exitcode": recorded.get("exitcode"),
                "signal": recorded.get("signal")}

    # -- 句柄 ---------------------------------------------------------------

    def _handle(self, *, run_id=None, topic=None, plan_id=None):
        """从统一的 `wf_runs` 重建远端句柄（没有第二张表：request_key 可重算）。"""
        with _readonly_conn(self.db) as conn:
            if run_id:
                run = _row(conn, "wf_runs", "SELECT * FROM wf_runs WHERE run_id=?", (run_id,))
            elif plan_id:
                run = _row(conn, "wf_runs", "SELECT * FROM wf_runs WHERE plan_id=?"
                                           " ORDER BY started_at DESC, rowid DESC LIMIT 1",
                           (plan_id,))
            elif topic:
                run = _row(conn, "wf_runs", "SELECT * FROM wf_runs WHERE topic=?"
                                           " ORDER BY started_at DESC, rowid DESC LIMIT 1",
                           (topic,))
            else:
                raise RemoteWorkflowError("需要 run_id / plan_id / topic 之一来定位远端运行",
                                          code=2)
            if run is None:
                raise RemoteWorkflowError(
                    f"没有匹配的 workflow run（run_id={run_id!r} plan={plan_id!r} "
                    f"topic={topic!r}）", code=2)
            gates = json.loads(run.get("gates_json") or "{}")
            remote = gates.get("remote") if isinstance(gates, dict) else None
            remote = remote if isinstance(remote, dict) else {}
            request_key = remote.get("request_key") if _filled(remote.get("request_key")) else None
            if request_key is None and self.deployment is not None:
                request_key = request_key_for(self.deployment, plan_id=run["plan_id"],
                                              prepared_id=run["prepared_id"],
                                              plan_sha256=run["plan_sha256"])
            if remote.get("deployment") is not None and self.deployment is not None \
                    and remote["deployment"] != self.deployment.identity():
                raise RemoteWorkflowError(
                    "这次 run 的部署身份与当前部署配置不一致：不能拿现在的配置去核另一个部署的"
                    "远端请求", code=9)
            finish = _row(conn, "wf_finishes", "SELECT * FROM wf_finishes WHERE run_id=?",
                          (run["run_id"],))
            attempt = _row(conn, "exec_attempts",
                           "SELECT a.*,j.remote_id,j.remote_state FROM exec_attempts a"
                           " LEFT JOIN exec_jobs j ON j.attempt_id=a.id"
                           " WHERE a.request_key=?", (request_key,))
        return {"run": run, "finish": finish, "remote": remote, "attempt": attempt,
                "request_key": request_key}

    def _runtime_from_handle(self, handle):
        """按这次 run 记录的授权作用域构建 Runtime（同一笔预算预留）。"""
        remote = handle.get("remote") or {}
        scope = remote.get("authorization_scope")
        if not _filled(scope):
            run = handle["run"]
            plan = self._frozen_plan(run["plan_id"], run["plan_version"], run["plan_sha256"])
            authorization = plan.get("authorization") \
                if isinstance(plan.get("authorization"), dict) else {}
            scope = authorization.get("scope")
        return self._runtime_for(scope)

    # -- 只读投影 -----------------------------------------------------------

    def status(self, *, run_id=None, topic=None, plan_id=None):
        """只读：本地投影（run / 远端绑定 / 执行状态 / 是否已核终态）。⛔ 不联网。"""
        projection = self.workflow.status(topic=topic, plan_id=plan_id)
        out = {"ok": bool(projection.get("ok")),
               "problems": list(projection.get("problems") or []),
               "workspace": str(self.root), "db": str(self.db), "query": False,
               "remote": [],
               "deployment": (None if self.deployment is None
                              else self.deployment.as_public()),
               "budget_note": "预算预留保持 reserved：本层不结算（缺可信计量时不假结算）",
               "resources_note": "unknown 时资源租约必须保留；明确终态后才可用 remote release"}
        with _readonly_conn(self.db) as conn:
            where, params = [], []
            if run_id:
                where.append("run_id=?"); params.append(run_id)
            if topic:
                where.append("topic=?"); params.append(topic)
            if plan_id:
                where.append("plan_id=?"); params.append(plan_id)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            runs = _rows(conn, "wf_runs", "SELECT * FROM wf_runs" + clause
                         + " ORDER BY started_at DESC", tuple(params))
            for run in runs:
                gates = json.loads(run.get("gates_json") or "{}")
                remote = gates.get("remote") if isinstance(gates, dict) else {}
                if not isinstance(remote, dict):
                    remote = {}
                finish = _row(conn, "wf_finishes", "SELECT * FROM wf_finishes WHERE run_id=?",
                              (run["run_id"],))
                attempt = _row(conn, "exec_attempts",
                               "SELECT a.*,j.remote_id,j.remote_state FROM exec_attempts a"
                               " LEFT JOIN exec_jobs j ON j.attempt_id=a.id"
                               " WHERE a.request_key=?", (remote.get("request_key"),))
                task = _row(conn, "tasks", "SELECT * FROM tasks WHERE token=?",
                            (remote.get("lease_id"),))
                leases = _rows(conn, "resources", "SELECT * FROM resources WHERE task_id=?",
                               ((task or {}).get("id"),))
                held_keys = (json.loads(task["resource_keys"]) if task else [])
                quarantine = []
                for key in held_keys:
                    quarantine.extend(_rows(conn, "scheduler_resource_quarantine",
                                            "SELECT * FROM scheduler_resource_quarantine"
                                            " WHERE resource_key=?", (key,)))
                reservation = _row(conn, "budget_reservations",
                                   "SELECT * FROM budget_reservations WHERE id=?",
                                   (remote.get("reservation_id"),))
                out["remote"].append({
                    "run_id": run["run_id"], "plan_id": run["plan_id"], "topic": run["topic"],
                    "prepared_id": run["prepared_id"], "started_at": run["started_at"],
                    "finished": finish is not None,
                    "finish": (None if finish is None else {
                        "reason": finish["reason"], "exitcode": finish["exitcode"],
                        "signal": finish["signal"]}),
                    "remote_binding": {key: remote.get(key) for key in
                                       ("request_key", "deployment_id", "reservation_id",
                                        "authorization_scope", "lease_id", "task_id",
                                        "attempt_id", "resources",
                                        "stop_verification", "fixture_gates")},
                    "deployment_matches": (self.deployment is not None
                                           and remote.get("deployment")
                                           == self.deployment.identity()),
                    "attempt": (None if attempt is None else {
                        "attempt_id": attempt["id"], "state": attempt["state"],
                        "remote_state": attempt.get("remote_state"),
                        "remote_id": attempt.get("remote_id"),
                        "output_dir": attempt.get("output_dir")}),
                    "terminal_verified": bool(attempt is not None
                                              and attempt.get("remote_state")
                                              in TERMINAL_REMOTE_STATES
                                              and finish is not None),
                    "task": (None if task is None else {
                        "task_id": task["id"], "status": task["status"],
                        "attempts": task["attempts"]}),
                    "resources_held": [item["resource_key"] for item in leases],
                    "resources_quarantined": [item["resource_key"] for item in quarantine],
                    "reservation": (None if reservation is None else {
                        "reservation_id": reservation["id"],
                        "status": reservation["status"], "units": reservation["units"],
                        "actual_units": reservation["actual_units"]}),
                    "note": ("预算与资源都保留，等 query 复核" if finish is None
                             else "已登记结束；复盘走 workflow review")})
        return out

    # -- 显式动作（联网）----------------------------------------------------

    def provision(self, *, force=False, actor="main"):
        """显式推送远端 worker（唯一的部署动作；⛔ 不做隐式部署）。"""
        self._require_write()
        self._require_deployment()
        del actor
        adapter = self._require_adapter()
        document = adapter.provision(force=bool(force))
        return {"ok": True, "provisioned": True, "document": _jsonable(document),
                "note": "已推送 ssh_worker.py 并核回版本；真实远端连通性仍以一次 query 为准"}

    def query(self, *, run_id=None, topic=None, plan_id=None, actor="main"):
        """显式远端复核：query → Runtime.reconcile；明确终态再用 token 探针定论。"""
        self._require_write()
        self._require_adapter()
        handle = self._handle(run_id=run_id, topic=topic, plan_id=plan_id)
        run, attempt = handle["run"], handle["attempt"]
        out = {"ok": False, "run_id": run["run_id"], "plan_id": run["plan_id"],
               "request_key": handle["request_key"], "query": True, "state": None,
               "finished": handle["finish"] is not None,
               "finish": (None if handle["finish"] is None else {
                   "reason": handle["finish"]["reason"],
                   "exitcode": handle["finish"]["exitcode"]}),
               "budget": "reserved", "resources": "held"}
        if attempt is None:
            out.update({"problems": ["没有执行记录：这次 run 从未提交（或用了别的部署标识）"],
                        "code": 2})
            return out
        if handle["finish"] is not None:
            out.update({"ok": True, "code": 0, "state": attempt.get("remote_state"),
                        "note": "已经登记结束；再 query 只回报既有记录"})
            return out
        try:
            runtime = self._runtime_from_handle(handle)
            row = dict(runtime.reconcile(attempt["id"]))
        except (PermissionError, ActionBlocked) as exc:
            out.update({"problems": [f"query 被策略拒绝：{exc}"], "code": 9})
            return out
        except (RuntimeError, ValueError, SchedulerError) as exc:
            out.update({"problems": [f"query 失败（{type(exc).__name__}: {exc}）：状态不可判"],
                        "state": "unknown", "code": 3})
            return out
        row["request_key"] = handle["request_key"]
        out.update(self._interpret_attempt(row))
        if row.get("remote_state") in TERMINAL_REMOTE_STATES:
            argv, block = self._run_expectation(run)
            out.update(self._finish_if_terminal(run["run_id"], row, actor, argv=argv,
                                                block=block))
            if out.get("finished"):
                out.update({"ok": True, "code": 0})
            else:
                out.update({"ok": False, "code": 3,
                            "problems": out.get("problems")
                            or [(out.get("terminal_check") or {}).get("reason")
                                or "终态未核过，保留预算与资源"]})
            return out
        if out.get("state") == "unknown":
            out.update({"ok": False, "code": 3,
                        "problems": ["远端不可判：预算预留与资源租约保留，⛔ 不写结束记录"]})
            return out
        out.update({"ok": True, "code": 0, "note": "远端仍在运行（或刚启动）：预算与资源保持占用"})
        return out

    def cancel(self, *, run_id=None, topic=None, plan_id=None, reason="stopped_by_user",
               actor="main"):
        """显式安全取消：先发远端停止（token 核验），再登记；⛔ 报告失败不挡停止。"""
        self._require_write()
        self._require_adapter()
        handle = self._handle(run_id=run_id, topic=topic, plan_id=plan_id)
        run, attempt = handle["run"], handle["attempt"]
        out = {"ok": False, "run_id": run["run_id"], "plan_id": run["plan_id"],
               "request_key": handle["request_key"], "cancel_sent": False, "state": None,
               "finished": handle["finish"] is not None, "budget": "reserved",
               "resources": "held"}
        if attempt is None:
            out.update({"problems": ["没有执行记录：这次 run 从未提交"], "code": 2})
            return out
        if handle["finish"] is not None:
            out.update({"ok": True, "code": 0, "state": attempt.get("remote_state"),
                        "note": "已经登记结束，无需再取消"})
            return out
        try:
            runtime = self._runtime_from_handle(handle)
            row = dict(runtime.cancel(attempt["id"]))
        except (PermissionError, ActionBlocked) as exc:
            out.update({"problems": [f"取消被拒（所有权 / 租约不可核）：{exc}"], "code": 9})
            return out
        except (RuntimeError, ValueError, SchedulerError) as exc:
            out.update({"problems": [f"取消请求失败（{type(exc).__name__}: {exc}）：状态不可判"],
                        "state": "unknown", "code": 3,
                        "note": "取消是否到达远端不可判：预算与资源保留，用 query 复核"})
            return out
        out["cancel_sent"] = True
        row["request_key"] = handle["request_key"]
        out.update(self._interpret_attempt(row))
        if row.get("remote_id") is None and self._no_remote_request(attempt["id"]):
            # 本地取消（队列里就被取消，或从未提交）：按 launch_error 记，没有远端作业。
            self._record_launch_error(run["run_id"], run["topic"],
                                      "远端从未受理这一次执行（本地取消）", actor)
            out.update({"ok": True, "code": 0, "state": "cancelled", "finished": True,
                        "reason": "launch_error",
                        "note": "这次 run 没有发出远端请求，已按 launch_error 登记结束"})
            self._abandon_lease_after_local_cancel(runtime, run, attempt["id"], out)
            return out
        if row.get("remote_state") in TERMINAL_REMOTE_STATES:
            argv, block = self._run_expectation(run)
            bookkeeping = self._finish_if_terminal(run["run_id"], row, actor, argv=argv,
                                                   block=block)
            out.update(bookkeeping)
            if out.get("finished"):
                out.update({"ok": True, "code": 0})
            else:
                out.update({"ok": True, "code": 3,
                            "note": "远端已停（token 已核），但结束登记未完成：安全停本身成立，"
                                    "登记问题按诊断处理", "bookkeeping": bookkeeping})
            return out
        out.update({"ok": False, "code": 3, "state": out.get("state") or "unknown",
                    "problems": ["远端还没有明确终态：取消已发出但未确认，预算与资源保留"]})
        return out

    def _abandon_lease_after_local_cancel(self, runtime, run, attempt_id, out):
        """本地取消后的资源清理（尽力而为，失败只记诊断，⛔ 不改已成立的安全停）。"""
        try:
            remote = json.loads(run.get("gates_json") or "{}").get("remote", {}) or {}
            with _readonly_conn(self.db) as conn:
                task = _row(conn, "tasks", "SELECT * FROM tasks WHERE token=?",
                            (remote.get("lease_id"),))
                ownership = _row(conn, "ownership", "SELECT * FROM ownership WHERE exp_id=?",
                                 (run["plan_id"],))
            if task is None or ownership is None:
                return
            owner, generation = ownership["owner"], int(ownership["generation"])
            if task["status"] in ("leased", "pending"):
                runtime.scheduler.cancel(task["id"], owner, generation,
                                         reason="local-cancel-before-submit")
            for resource_key in json.loads(task["resource_keys"]):
                runtime.scheduler.confirm_resource_idle(
                    resource_key,
                    {"kind": "lease_released_before_submit", "task_id": task["id"],
                     "token": task["token"], "attempt_id": attempt_id,
                     "resource_key": resource_key}, actor="remote-workflow")
                out.setdefault("released_resources", []).append(resource_key)
        except Exception as exc:  # noqa: BLE001 - 清理失败不覆盖「已经安全停」
            out["cleanup"] = f"{type(exc).__name__}: {exc}"

    def collect(self, *, run_id=None, topic=None, plan_id=None, actor="main"):
        """显式取回产物（scp）：只有 worker 报 completed 且已核终态时才允许。"""
        self._require_write()
        self._require_adapter()
        handle = self._handle(run_id=run_id, topic=topic, plan_id=plan_id)
        attempt = handle["attempt"]
        if attempt is None:
            raise RemoteWorkflowError("没有执行记录：不能取回", code=2)
        if attempt.get("remote_state") != "completed":
            raise RemoteWorkflowError(
                f"原 job 的远端状态是 {attempt.get('remote_state')!r}：只有 completed 才能取回"
                "（缺件只能是 partial，不是完成）", code=9)
        argv, block = self._run_expectation(handle["run"])
        ok, proof = self._verified_terminal(attempt, request_key=handle["request_key"],
                                            argv=argv, block=block)
        if not ok:
            raise RemoteWorkflowError("终态未核过（token / 状态不一致）：拒绝取回",
                                      [proof.get("reason", "")], code=9)
        row = dict(self._runtime_from_handle(handle).collect(attempt["id"]))
        return {"ok": row.get("state") in ("completed", "partial"),
                "attempt": {"attempt_id": row["id"], "state": row["state"],
                            "remote_state": row.get("remote_state")},
                "terminal_check": proof,
                "note": "产物按 RunSpec.required_artifacts 清单取回并逐个核 SHA256"}

    def release(self, *, run_id=None, topic=None, plan_id=None, actor="main"):
        """明确终态后核空闲并释放资源租约；**预算预留仍不结算**（缺可信计量）。"""
        self._require_write()
        self._require_adapter()
        handle = self._handle(run_id=run_id, topic=topic, plan_id=plan_id)
        run, attempt, remote = handle["run"], handle["attempt"], handle["remote"]
        if attempt is None:
            raise RemoteWorkflowError("没有执行记录：不能释放资源", code=2)
        if attempt.get("remote_state") not in TERMINAL_REMOTE_STATES:
            raise RemoteWorkflowError(
                f"远端状态是 {attempt.get('remote_state')!r}：未到明确终态不释放资源"
                "（query unknown 时保留预算与资源）", code=3)
        argv, block = self._run_expectation(run)
        ok, proof = self._verified_terminal(attempt, request_key=handle["request_key"],
                                            argv=argv, block=block)
        if not ok:
            raise RemoteWorkflowError("终态未核过（token / 状态 / argv 不一致）：不释放资源",
                                      [proof.get("reason", "")], code=3)
        with _readonly_conn(self.db) as conn:
            task = _row(conn, "tasks", "SELECT * FROM tasks WHERE token=?",
                        (remote.get("lease_id"),))
            ownership = _row(conn, "ownership", "SELECT * FROM ownership WHERE exp_id=?",
                             (run["plan_id"],))
            reservation = _row(conn, "budget_reservations", "SELECT * FROM budget_reservations"
                                                           " WHERE id=?",
                               (remote.get("reservation_id"),))
        if task is None:
            return {"ok": True, "released": [], "reservation": reservation,
                    "note": "没有任务记录：租约已不在（可能已释放）"}
        # 释放对象必须是**这一次**执行的租约：attempt 记录的 spec 与任务 token 双向对得上。
        spec = json.loads(attempt.get("spec_json") or "{}")
        if spec.get("lease_id") != task["token"] \
                or spec.get("request_key") != handle["request_key"] \
                or task["token"] != remote.get("lease_id"):
            raise RemoteWorkflowError(
                "释放对象与这次执行记录不一致（attempt / lease / request_key 对不上）："
                "⛔ 不能拿别的 attempt 的停止探针来释放这个租约", code=9)
        if ownership is None:
            raise RemoteWorkflowError("缺所有权记录：不能确认这次的租约归属", code=9)
        runtime = self._runtime_from_handle(handle)
        owner, generation = ownership["owner"], int(ownership["generation"])
        try:
            runtime.scheduler.renew_owner(
                run["plan_id"], owner, generation,
                lease_seconds=max(60.0, float(self.deployment.lease_seconds or 300.0)))
        except (SchedulerError, ValueError) as exc:
            raise RemoteWorkflowError(
                f"所有权租约不可续（{type(exc).__name__}: {exc}）：不能安全释放资源",
                [str(exc)], code=9) from None
        if task["status"] == "leased":
            try:
                runtime.scheduler.ack(
                    task["id"], owner, generation, task["worker_id"], task["token"],
                    result={"remote_state": attempt.get("remote_state"),
                            "terminal_verified": True, "remote_id": attempt.get("remote_id")})
            except (SchedulerError, ValueError) as exc:
                raise RemoteWorkflowError(f"ACK 释放资源失败（{type(exc).__name__}: {exc}）",
                                          [str(exc)], code=9) from None
        released = []
        evidence_base = {"kind": "worker_stop", "task_id": task["id"], "token": task["token"],
                         "request_key": handle["request_key"],
                         "state": attempt.get("remote_state"),
                         "remote_id": attempt.get("remote_id")}
        for resource_key in json.loads(task["resource_keys"]):
            try:
                runtime.scheduler.confirm_resource_idle(
                    resource_key, dict(evidence_base, resource_key=resource_key), actor=actor)
                released.append(resource_key)
            except (SchedulerError, ValueError) as exc:
                raise RemoteWorkflowError(f"核空闲失败（{resource_key}）：{exc}", [str(exc)],
                                          code=9) from None
        return {"ok": True, "released": released, "terminal_check": proof,
                "reservation": reservation,
                "budget_note": "预算预留未结算：本层没有可信计量，不把自述数字写成 settled"
                               "（用 budget settle 的人工核账边界）"}

    # -- 资源空闲核验器（注入给 Runtime.scheduler）--------------------------

    def _verify_resource_idle(self, resource_key, evidence, snapshot):
        """默认空闲核验：只证明**受管作业已经明确停了**，⛔ 不证明整台机器空闲。

        两种证据形态：
          * `worker_stop`：远端 worker 报过明确终态，且本地执行记录同一终态；
          * `lease_released_before_submit`：本地能证明这次租约从未发出过远端请求。
        两种都要求 evidence 的 resource_key / task_id / token 与 scheduler 快照逐项相等。
        """
        if not isinstance(evidence, dict) or not isinstance(snapshot, dict):
            return False
        for key in ("resource_key", "task_id", "token"):
            if evidence.get(key) != snapshot.get(key):
                return False
        kind = evidence.get("kind")
        request_key = evidence.get("request_key")
        try:
            with _readonly_conn(self.db) as conn:
                if kind == "worker_stop":
                    if evidence.get("state") not in TERMINAL_REMOTE_STATES:
                        return False
                    row = _row(conn, "exec_attempts",
                               "SELECT a.state,j.remote_state FROM exec_attempts a"
                               " LEFT JOIN exec_jobs j ON j.attempt_id=a.id"
                               " WHERE a.request_key=?", (request_key,))
                    return bool(row and row.get("remote_state") == evidence.get("state"))
                if kind == "lease_released_before_submit":
                    attempt_id = evidence.get("attempt_id")
                    if not attempt_id:
                        return True  # 连执行记录都没建：不可能发出远端请求
                    rows = _rows(conn, "exec_observations",
                                 "SELECT operation FROM exec_observations WHERE attempt_id=?",
                                 (attempt_id,))
                    return not any(item["operation"] == "submit" for item in rows)
        except (sqlite3.Error, RemoteWorkflowError):
            return False
        return False


# ---------------------------------------------------------------------------
# 只读诊断
# ---------------------------------------------------------------------------


def fingerprint_probe(block, *, worker_path="<remote_dir>/ssh_worker.py"):
    """给出在**远端运行单元**上现算三个指纹的命令（本文件不执行，也不填假值）。

    ⛔ 不接受「手写一个 64 位串」当已验证：`code_fingerprint` / `task_fingerprint` /
    `environment_fingerprint` 必须由**当前** `ssh_worker.py` 在远端对真实字节算出；
    worker 在 `start` 时会重算，不符就 `DefinitiveFailure`（远端拒绝启动）。

    命令直接可复制执行：worker 已经由 `remote provision` 部署到
    `<remote_dir>/ssh_worker.py`，所以用 `importlib.util.spec_from_file_location` 按**文件路径**
    导入（⛔ 不是 `sys.path.insert(0, '<worker文件>')` —— 那插入的是文件而不是目录，导不进来）。
    """
    fields = {"unit_root": block.get("unit_root", "<unit_root>"),
              "code_root": block.get("code_root", "<code_root>"),
              "environment_root": block.get("environment_root", "<environment_root>"),
              "input_root": block.get("input_root", "<input_root>")}

    def command(expression):
        return ("python3 -I -c \"import importlib.util as u;"
                f"s=u.spec_from_file_location('ssh_worker',{worker_path!r});"
                "m=u.module_from_spec(s);s.loader.exec_module(m);"
                f"print({expression})\"")

    environment = block.get("environment") if isinstance(block.get("environment"), dict) else {}
    python = environment.get("python") or (fields["environment_root"] + "/bin/python3")
    pyvenv_cfg = fields["environment_root"] + "/pyvenv.cfg"
    return {
        "code_fingerprint": command(f"m.code_fingerprint({fields['code_root']!r})"),
        "task_fingerprint": command(f"m.task_fingerprint({fields['input_root']!r})"),
        "environment_fingerprint": command(
            f"m.environment_fingerprint({python!r}, [], {pyvenv_cfg!r})"),
        "note": "在远端机器上跑这三条，把输出原样写进计划；worker 在 start 时会重算并拒绝不符。"
                "环境指纹包含解释器字节、版本、真实包清单与 requested packages。",
        "unit_root": fields["unit_root"],
    }


def doctor(workspace, *, plan_id=None):
    """只读诊断：部署 / worker / 所有权 / 预算 / 调度器。⛔ 不联网、不写盘、不读凭据。"""
    root = Path(workspace).resolve()
    out = {"ok": False, "workspace": str(root), "checks": {}, "problems": [],
           "can_submit": False, "network": "未连接（doctor 只读本地）",
           "evidence": "本命令不证明真实 SSH / POSIX 已验"}
    deployment, problems = read_deployment(root)
    out["checks"]["deployment"] = {
        "configured": deployment is not None, "path": DEPLOYMENT_REL,
        "public": None if deployment is None else deployment.as_public(),
        "problems": problems}
    out["problems"].extend(problems)
    if deployment is None:
        out["checks"]["next"] = [
            f"写 {DEPLOYMENT_REL}（enabled=true, host, user, known_hosts, remote_dir, "
            "deployment_id, owner），然后 remote provision"]
        return out
    db = root / ".autoresearch" / "runtime.sqlite3"
    out["checks"]["workspace_db"] = {"path": str(db), "exists": db.is_file()}
    if not db.is_file():
        out["problems"].append("工作区状态库不存在：先 init / prepare")
        return out
    adapter = SSHAdapter(deployment.ssh)  # 只用于 state_root / worker_local_path，不发请求
    receipt_path = adapter.state_root / "provision.json"
    receipt = {"path": str(receipt_path), "exists": receipt_path.is_file(), "matches": False,
               "problems": []}
    if receipt["exists"]:
        try:
            document = json.loads(receipt_path.read_text(encoding="utf-8"))
            local_sha = digest(adapter.worker_local_path.read_bytes())
            receipt["matches"] = (isinstance(document, dict)
                                  and document.get("worker_sha256") == local_sha
                                  and document.get("worker_version") == WORKER_VERSION
                                  and document.get("deployment") == deployment.ssh.identity())
            if not receipt["matches"]:
                receipt["problems"].append(
                    "provision 收据与当前 worker 字节 / 版本 / 部署身份不一致")
        except (OSError, ValueError) as exc:
            receipt["problems"].append(f"provision 收据不可读：{exc}")
    else:
        receipt["problems"].append("没有 provision 收据：需要显式 remote provision")
    out["checks"]["worker"] = receipt
    if not receipt["matches"]:
        out["problems"].extend(receipt["problems"])
    ownership = {"plan_id": plan_id, "verified": False, "reason": None}
    if plan_id:
        try:
            from kernel.autoresearch.runtime.backend import require_runtime_ownership
            require_runtime_ownership(SimpleNamespace(db_path=str(db)), plan_id)
            ownership["verified"] = True
        except Exception as exc:  # noqa: BLE001 - 诊断里任何失败都只是「不可核」
            ownership["reason"] = f"{type(exc).__name__}: {exc}"
    else:
        ownership["reason"] = "没有给 --plan-id：只报告「需要真实 Runtime 所有权」"
    out["checks"]["ownership"] = ownership
    budget = {"accounts": 0, "active_accounts": 0, "reservations": 0,
              "by_scope": {}, "covers_plan_max_cost_units": None}
    scheduler = {"ownership_rows": 0, "paid_tasks": 0, "resources": 0,
                 "quarantined": 0, "paused": None}
    try:
        with _readonly_conn(db) as conn:
            if _table_exists(conn, "budget_accounts"):
                budget["accounts"] = conn.execute("SELECT COUNT(*) FROM budget_accounts").fetchone()[0]
                budget["active_accounts"] = conn.execute(
                    "SELECT COUNT(*) FROM budget_accounts WHERE active=1").fetchone()[0]
                for row in conn.execute("SELECT scope,COUNT(*) AS n FROM budget_reservations"
                                        " WHERE status='reserved' GROUP BY scope"):
                    budget["by_scope"][row["scope"]] = row["n"]
                budget["reservations"] = conn.execute(
                    "SELECT COUNT(*) FROM budget_reservations WHERE status='reserved'").fetchone()[0]
            if _table_exists(conn, "ownership"):
                scheduler["ownership_rows"] = conn.execute(
                    "SELECT COUNT(*) FROM ownership").fetchone()[0]
            if _table_exists(conn, "tasks"):
                scheduler["paid_tasks"] = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE paid=1").fetchone()[0]
            if _table_exists(conn, "resources"):
                scheduler["resources"] = conn.execute(
                    "SELECT COUNT(*) FROM resources").fetchone()[0]
            if _table_exists(conn, "scheduler_resource_quarantine"):
                scheduler["quarantined"] = conn.execute(
                    "SELECT COUNT(*) FROM scheduler_resource_quarantine").fetchone()[0]
            if _table_exists(conn, "scheduler_control"):
                row = conn.execute("SELECT * FROM scheduler_control WHERE id=1").fetchone()
                if row is not None:
                    scheduler["paused"] = {
                        "pause_new_experiments": bool(row["pause_new_experiments"]),
                        "pause_paid": bool(row["pause_paid"])}
    except (sqlite3.Error, RemoteWorkflowError) as exc:
        out["problems"].append(f"状态库只读查询失败：{exc}")
    out["checks"]["budget"] = budget
    out["checks"]["scheduler"] = scheduler
    if plan_id and not ownership["verified"]:
        out["problems"].append(f"实验 {plan_id} 没有可核的 Runtime 所有权：Runtime._lease 会拒")
    if budget["accounts"] == 0:
        out["problems"].append("没有任何预算账号：没有预算不得提交（先 budget grant + reserve）")
    if budget["reservations"] == 0:
        out["problems"].append("没有 reserved 状态的预算预留：执行层不接受空预留"
                               "（同一笔 authorization 预留由 Admission 与 Runtime 共用）")
    out["checks"]["required_plan_fields"] = {
        "execution.scope": "remote（或历史写法 ssh）",
        "execution.deployment_id": deployment.deployment_id,
        "execution.remote.unit_root/code_root/environment_root/input_root/output_root":
            "规范化 POSIX 绝对路径；后四个在 unit_root 内且互不重叠",
        "execution.remote.task_ids/required_artifacts": "非空、安全、不重复",
        "execution.remote.*_fingerprint":
            "三个 64 位 SHA256；用当前 ssh_worker 的 code_fingerprint / task_fingerprint / "
            "environment_fingerprint 在远端现算（见 fingerprint_probe），worker 在 start 时对真实字节重算",
        "authorization": "scope（small/large/submission）/series_id/reservation_id —— "
                         "Admission 与 Runtime(budget_scopes=(scope,)) 核同一笔预留",
        "budget.max_cost_units/max_seconds": "正整数 / (0, 7天]",
        "execution_argv": "与实际 `--` argv 逐项相等"}
    out["checks"]["fingerprint_probe"] = fingerprint_probe(
        {"unit_root": "/srv/units/<plan>", "code_root": "/srv/units/<plan>/code",
         "environment_root": "/srv/units/<plan>/env",
         "input_root": "/srv/units/<plan>/input"})
    out["ok"] = not out["problems"]
    out["can_submit"] = out["ok"]
    out["next"] = (["用 remote check 预览一次（只读）", "remote start 提交（需要真实资源租约键）"]
                   if out["ok"] else ["按 problems 逐条补齐；补齐前不会发出任何远端请求"])
    return out
