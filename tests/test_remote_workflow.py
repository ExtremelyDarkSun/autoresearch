# GENERATED-BY: managed remote execution wiring, 2026-11-09
# LAST-EDIT: 2026-11-13 补 R394/R395：门槛被对照读数满足时零远端请求；submit 返回 launch 记录、check 返回 preflight 记录
# PURPOSE: 证明三条承诺：① 缺部署 / 所有权 / 预算 / 资源租约 / 当前审计监控时不提交（零远端请求）；
#          ② 实际 argv 与计划逐项绑定，预算用**同一笔** authorization 预留（不造第二笔 run 预留）；
#          ③ 终态只在 worker 明确报终态 + cancel token 探针 + 停止证据齐备后登记，
#          unknown / 缺停止证据时保留预算与资源。
# ORIGIN: 合成临时工作区夹具；⛔ 不连任何真实机器、不读凭据、不发网络请求。
# CUMULATIVE-DELTA: 首版。`FakeWorkerTransport` 用**当前** ssh_worker 的函数算请求哈希与指纹，
#          但它是**进程内假传输**：真实 SSH / POSIX 进程组语义不在这里验证。
#          2026-11-13：确认性计划夹具补 `criteria.gate` / `.control`；新增「门槛不可分辨 ⇒
#          transport 一次都没被调用」与「预检记录 launched=false / 起跑记录带 run_id」两组回归。
"""受管远端接线的离线测试。

**这些测试不验证真实 SSH 网络**：`FakeWorkerTransport` 在进程内模拟远端 worker 的
`request-write / start / query / cancel / collect / version` 协议，**不启动任何远端或本地进程**。
它验证的是接线判据：

1. 部署默认关闭 / 配置缺失 / `enabled` 不是 true ⇒ 一个 `ssh` 命令都不发；
2. 统一闸（准备 / 审计 / 监控 / 执行准入）任一不过 ⇒ 不提交；
3. `RunSpec.execution_config.argv` 逐项等于计划声明的 argv；预算只用计划 `authorization`
   里的同一笔预留（`Runtime(budget_scopes=(scope,))`）；
4. `unknown`（start 响应丢失 / query 超时）⇒ 不写 `wf_finishes`、预算保持 `reserved`、资源租约保持；
5. 终态必须同时满足「worker 记录明确终态」+「cancel `--token` 探针回报同一终态」+「停止证据」，
   ⛔ 只有 `state='failed'` 不算停了（硬超时可能没杀干净、主进程退出不代表子组停）；
6. 安全 cancel 先发停止，登记失败不改变「已经发出停止」。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import governance as g  # noqa: E402
from kernel.autoresearch import remote_workflow as rw  # noqa: E402
from kernel.autoresearch.admission import Admission  # noqa: E402
from kernel.autoresearch.backends import ssh_worker as worker  # noqa: E402
from kernel.autoresearch.backends.ssh import WORKER_VERSION, SSHAdapter  # noqa: E402
from kernel.autoresearch.runtime.accounts import (  # noqa: E402
    Accounts,
    ApprovalEvidence,
    HMACSource,
    SourcePolicy,
)
from kernel.autoresearch.runtime.backend import BackendRegistry  # noqa: E402
from kernel.autoresearch.runtime.execution import JobStatus  # noqa: E402
from kernel.autoresearch.runtime.service import Runtime  # noqa: E402
from kernel.autoresearch.runtime.store import Store, canonical_json  # noqa: E402
from kernel.autoresearch.runtime.topics import Topics  # noqa: E402

TOPIC = "T1"
SESSION = "session-owner"
REVIEWER_SESSION = "session-reviewer"
REMOTE = "/remote"
KEY = b"test-only-key-not-for-production!!"
RESOURCE = "machine:gpu-a/gpu0"
UNIT = REMOTE + "/units/P1"
TERMINAL = ("completed", "failed", "cancelled")


# ---------------------------------------------------------------------------
# 假传输：进程内 worker 协议（不启动进程、不联网）
# ---------------------------------------------------------------------------


class FakeWorkerTransport:
    """把远端 `/remote/...` 映射到本地临时目录，并在进程内模拟 worker 的 JSON 协议。

    ⚠️ 它**不是** worker：不核指纹、不建进程组、不杀进程。真实 SSH/POSIX 语义仍待验。
    """

    def __init__(self, base, deployment):
        self.base = Path(base)
        self.deployment = deployment
        self.calls = []
        self.jobs = {}
        self.outcome = "completed"
        self.exitcode = 0
        self.timed_out = False
        self.fail_query = False
        self.fail_start_response = False

    # -- 路径映射 -----------------------------------------------------------

    def local(self, remote):
        text = str(remote)
        if text.startswith(REMOTE):
            return str(self.base / text.lstrip("/").replace("/", os.sep))
        return text

    def _reply(self, document):
        return {"code": 0, "stdout": (canonical_json(document) + "\n").encode("utf-8"),
                "stderr": b""}

    def _job(self, state_dir):
        return self.jobs.setdefault(state_dir, {"state": None, "pid": None, "birth": None,
                                                "token": None, "request": None,
                                                "attempt_id": None, "stop_verified": False,
                                                "stop_evidence": None, "exitcode": None,
                                                "signal": None, "timed_out": False,
                                                "stdout": b""})

    def only_job(self):
        if len(self.jobs) != 1:
            raise AssertionError("应当只有一次远端请求，实际 %d" % len(self.jobs))
        return next(iter(self.jobs.values()))

    @staticmethod
    def _launch_argv(request):
        """与 worker 一致：只把首项 python 换成独立环境解释器。"""
        argv = list(request["argv"])
        environment = request.get("environment") or {}
        if argv and os.path.basename(argv[0]).lower() in ("python", "python3", "python.exe",
                                                          "python3.exe"):
            if environment.get("kind") == "existing" and environment.get("python"):
                argv[0] = environment["python"]
            else:
                argv[0] = request["spec"]["environment_root"] + "/bin/python3"
        return argv

    @staticmethod
    def summary(job):
        keys = ("request_hash", "attempt_id", "state", "pid", "birth", "token", "exitcode",
                "signal", "timed_out", "reason", "started_at", "finished_at", "output_dir",
                "argv", "protocol", "cancel_requested", "stop_verified", "stop_evidence")
        summary = {key: job.get(key) for key in keys}
        argv = job.get("argv")
        summary["argv_sha256"] = (worker.sha_bytes(worker.canonical(argv).encode("utf-8"))
                                  if isinstance(argv, list) and argv else None)
        return summary

    # -- 传输协议（protocol v2：绑定 hash/token/attempt + stop_verified）------

    def run(self, argv, *, stdin_bytes=None, timeout=None):
        self.calls.append(list(argv))
        tokens = shlex.split(argv[-1])
        if tokens and tokens[0] == "mv":
            os.replace(self.local(tokens[1]), self.local(tokens[2]))
            return {"code": 0, "stdout": b"", "stderr": b""}
        subcommand = tokens[2]
        if subcommand == "version":
            return self._reply({"ok": True, "version": WORKER_VERSION,
                                "protocol": WORKER_VERSION, "features": ["request-binding"]})
        state_dir = tokens[tokens.index("--state-dir") + 1]
        job = self._job(state_dir)
        if subcommand == "request-write":
            request = json.loads(stdin_bytes.decode("utf-8"))
            job["request"] = request
            job["request_hash"] = worker.request_hash(request)
            job["output_dir"] = request["output_dir"]
            job["argv"] = list(request["argv"])
            job["token"] = request["token"]
            job["attempt_id"] = request.get("attempt_id")
            return self._reply({"ok": True, "state_dir": state_dir,
                                "request_hash": job["request_hash"],
                                "token": job["token"], "attempt_id": job["attempt_id"],
                                "protocol": WORKER_VERSION, "reused": False})
        if subcommand == "start":
            job.update({"state": "running", "pid": 987654, "birth": 1,
                        "started_at": 1.0, "reason": None, "stop_verified": False,
                        "argv": self._launch_argv(job["request"])})
            if self.fail_start_response:
                raise ConnectionError("模拟 start 响应丢失（远端可能已经跑了）")
            return self._reply({"ok": True, "state": self.summary(job)})
        if subcommand == "query":
            if self.fail_query:
                raise ConnectionError("模拟 query 超时")
            if job.get("state") is None:
                return self._reply({"ok": True, "state": {"state": "unknown",
                                                          "reason": "还没有 state.json"}})
            return self._reply({"ok": True, "state": self.summary(job)})
        if subcommand == "cancel":
            if job.get("state") is None:
                return self._reply({"ok": False, "state": {"state": "unknown",
                                                           "reason": "没有运行记录"}})
            token = tokens[tokens.index("--token") + 1]
            if token != job.get("token"):
                return self._reply({"ok": False, "state": {"state": "unknown",
                                                           "reason": "取消 token 与运行记录不符"}})
            if job["state"] not in TERMINAL:  # 真实 worker：运行中才真的终止
                job.update({"state": "cancelled", "reason": "term", "finished_at": 2.0,
                            "stop_verified": True, "cancel_requested": True,
                            "stop_evidence": {"method": "term", "pid": job.get("pid"),
                                              "birth": job.get("birth")}})
            return self._reply({"ok": True, "state": self.summary(job)})
        if subcommand == "collect":
            if job.get("state") != "completed":
                return self._reply({"ok": False, "state": job.get("state"),
                                   "reason": "原 job 不是 completed"})
            data = (job.get("stdout") or b"") or b'{"ok":true}\n'
            target = Path(self.local(job["output_dir"])) / "results.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return self._reply({"ok": True, "state": "completed",
                                "request_hash": job.get("request_hash"),
                                "token": job.get("token"),
                                "attempt_id": job.get("attempt_id"),
                                "stop_verified": True,
                                "argv_sha256": self.summary(job)["argv_sha256"],
                                "output_dir": job["output_dir"],
                                "artifacts": [{"path": "results.json",
                                               "sha256": hashlib.sha256(data).hexdigest()}],
                                "missing": []})
        raise RuntimeError("假传输不认识子命令：%s" % subcommand)

    def copy_to(self, local_path, remote_path, *, timeout=None):
        target = Path(self.local(remote_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)

    def copy_from(self, remote_path, local_path, *, timeout=None):
        source = Path(self.local(remote_path))
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, local_path)

    # -- 测试操作 -----------------------------------------------------------

    def ssh_commands(self):
        return [call[-1] for call in self.calls if call and call[0] == "ssh"]

    def finalize(self, state=None):
        """模拟远端作业自己结束（worker 记终态 + 停止证据）。"""
        job = self.only_job()
        state = state or self.outcome
        job["state"] = state
        job["finished_at"] = 3.0
        job["stop_verified"] = True
        job["stop_evidence"] = {"method": "wait-group-empty", "pid": job.get("pid"),
                                "birth": job.get("birth")}
        if state == "completed":
            job.update({"exitcode": 0, "signal": None, "timed_out": False})
        elif state == "failed":
            job.update({"exitcode": self.exitcode or 3, "signal": None,
                        "timed_out": bool(self.timed_out)})
        else:
            job.update({"exitcode": None, "signal": None})
        return job


class NoStopEvidenceAdapter(SSHAdapter):
    """测试补丁：回报**终态但没有停止证据**（模拟坏掉的适配层 / 回归）。

    生产适配层（protocol v2）只有在 worker 给出 `stop_verified=true` 时才报终态；
    本类故意把那个字段拿掉，用来证明 remote 层自己也会拒：⛔ 只看到 `state='failed'` 不登记。
    """

    @staticmethod
    def _strip(status):
        if status.state in TERMINAL and isinstance(status.evidence, dict):
            evidence = {key: value for key, value in status.evidence.items()
                        if key not in ("stop_verified", "stop_evidence", "stopped_at")}
            return JobStatus(status.remote_id, status.state, evidence)
        return status

    def query(self, request_key, remote_id):
        return self._strip(super().query(request_key, remote_id))

    def cancel(self, request_key, remote_id):
        return self._strip(super().cancel(request_key, remote_id))


class TamperAdapter(SSHAdapter):
    """在 query/cancel 的终态回执上做手脚：argv 摘要、停止细节、attempt 归属。

    用来证明 remote 层**自己**也核这些字段（不是只靠适配层）。
    """

    def __init__(self, deployment, *, tamper, **kwargs):
        self._tamper = tamper
        super().__init__(deployment, **kwargs)

    def _apply(self, status):
        if status.state in TERMINAL and isinstance(status.evidence, dict):
            return JobStatus(status.remote_id, status.state, self._tamper(dict(status.evidence)))
        return status

    def query(self, request_key, remote_id):
        return self._apply(super().query(request_key, remote_id))

    def cancel(self, request_key, remote_id):
        return self._apply(super().cancel(request_key, remote_id))


# ---------------------------------------------------------------------------
# 审计核验器夹具（不是生产实现）
# ---------------------------------------------------------------------------


class VerifiedHost:
    """审计核验器夹具：只回 `{verified: True, session_id: …}`；生产是 `DSHHost()`。"""

    def verify(self, kind, payload):
        if kind != "audit_independence" or not isinstance(payload, dict):
            return None
        wrapped = payload.get("proof")
        proof = wrapped if isinstance(wrapped, dict) else payload
        session = proof.get("session_id")
        if not session:
            return None
        return {"verified": True, "kind": kind, "session_id": session,
                "prepared_id": payload.get("prepared_id") or proof.get("prepared_id"),
                "owner_session": payload.get("owner_session")}


def audit_report(prepared_id):
    return {
        "prepared_id": prepared_id,
        "coverage": {category: {"verdict": "ok", "note": category + " 已核",
                                "sources": [{"path": "evidence/readings.md", "locator": "L1"}]}
                     for category in g.AUDIT_CATEGORIES},
        "issues": [], "resolves": [],
        "decision": "proceed",
        "decision_reason": "目标连接、设计与历史约束都可核",
        "focus": {"question_goal": "与专题目标相连",
                  "design_interpretation": "设计与解释边界可核",
                  "history_constraints": "已检索历史与限制"},
    }


class RemoteCase(unittest.TestCase):
    """一个临时工作区：真预算、真所有权、真执行状态机 + 一个进程内假传输。"""

    venv_python = None
    environment_fingerprint = None
    _venv_root = None

    @classmethod
    def setUpClass(cls):
        cls._venv_root = Path(tempfile.mkdtemp(prefix="autoresearch-remote-venv-"))
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(cls._venv_root)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        candidates = [cls._venv_root / "bin" / "python3", cls._venv_root / "bin" / "python",
                      cls._venv_root / "Scripts" / "python.exe"]
        cls.venv_python = str(next(item for item in candidates if item.is_file()))
        # 指纹用**当前** worker 的函数现算（⛔ 不写死假值）。
        cls.environment_fingerprint = worker.environment_fingerprint(
            cls.venv_python, [], str(cls._venv_root / "pyvenv.cfg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._venv_root, ignore_errors=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.db = self.ws / ".autoresearch" / "runtime.sqlite3"
        self.db.parent.mkdir()
        self.store = Store(self.db)
        self.topics = Topics(self.store)
        self.topics.create_topic(TOPIC, title="提高通过题数", objective="把通过题数从 3 提到 4")
        self.topic_dir = self.ws / "topics" / TOPIC
        self.topic_dir.mkdir(parents=True)
        self.doc = self.topic_dir / "TOPIC.md"
        self.doc.write_text("# T1\n\n## 2 假设与假设迭代\n\n- H1：收紧入口参数能提高通过题数\n",
                            encoding="utf-8")
        (self.ws / "evidence").mkdir()
        (self.ws / "evidence" / "readings.md").write_text(
            "上一轮读数：通过题数 3（逐题核对）\n", encoding="utf-8")
        (self.ws / "submission").mkdir()
        (self.ws / "submission" / "entry.py").write_text(
            "def parse_args(argv):\n    return argv\n", encoding="utf-8")
        (self.ws / "plans").mkdir()
        # 远端运行单元：本地映射 + 用当前 worker 函数算真实指纹。
        self.unit = self.root / "root" / REMOTE.lstrip("/") / "units" / "P1"
        for sub in ("code", "env", "input"):
            (self.unit / sub).mkdir(parents=True, exist_ok=True)
        (self.unit / "code" / "job.py").write_text("print('job')\n", encoding="utf-8")
        task = self.unit / "input" / "tasks" / "t1.txt"
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text("task one\n", encoding="utf-8")
        (self.unit / "input" / "manifest.json").write_text(json.dumps(
            {"tasks": {"t1": {"path": "tasks/t1.txt", "sha256": worker.sha_file(task)}}}),
            encoding="utf-8")
        self.code_fingerprint = worker.code_fingerprint(self.unit / "code")
        self.task_fingerprint = worker.task_fingerprint(self.unit / "input")
        # 部署配置（enabled=true 才可能有任何远端动作）。
        (self.ws / ".autoresearch" / "remote").mkdir()
        self.deployment_path = self.ws / rw.DEPLOYMENT_REL
        self.deployment_config = {
            "enabled": True, "deployment_id": "dep-a", "owner": "owner-a",
            "host": "fake-box", "user": "runner", "known_hosts": str(self.root / "known_hosts"),
            "remote_dir": REMOTE + "/tools", "state_root": str(self.root / "state"),
            "lease_seconds": 3600, "note": "offline fixture"}
        self.write_deployment(self.deployment_config)
        (self.root / "known_hosts").write_text("", encoding="utf-8")
        self.deployment = rw.load_deployment(self.ws)
        self.transport = FakeWorkerTransport(self.root / "root", self.deployment.ssh)
        # 真所有权：backend marker + companion（Runtime._lease 会真核）。
        (self.ws / "experiments").mkdir()
        BackendRegistry(self.store).register_new("P1", self.ws / "experiments" / "P1", actor="test")
        # 真预算：授权 small 作用域，并预留同一笔 authorization 预留。
        self.now = time.time()
        self.evidence = ApprovalEvidence({"receiver": SourcePolicy(HMACSource(KEY),
                                                                   frozenset({"human"}))})
        self.accounts = Accounts(self.store, self.evidence)
        event = {"event_id": "grant-1", "principal": "human", "exp_id": "P1",
                 "series_id": "S-1", "scope": "small",
                 "expires_at": self.now + 3600, "total_units": 100, "action": "grant"}
        event["signature"] = hmac.new(KEY, canonical_json(event).encode(), hashlib.sha256).hexdigest()
        self.accounts.authorize("receiver", event, "authorize-1")
        self.reservation = self.accounts.reserve("S-1", "P1", "small", 100, "reserve-1")
        self.argv = ["python3", "job.py", "--tasks", "t1"]
        self.monitor_calls = []
        self.verifier = VerifiedHost()
        self.flow = self.make_flow()

    # -- 夹具 ---------------------------------------------------------------

    def write_deployment(self, config):
        self.deployment_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    def monitor_ok(self, owner_session, exp_id, action="launch"):
        self.monitor_calls.append((owner_session, exp_id, action))
        return {"ok": True, "problems": [], "registered": True,
                "state": "none", "intent_id": None}

    def make_flow(self, **kwargs):
        kwargs.setdefault("deployment", self.deployment)
        kwargs.setdefault("store", self.store)
        kwargs.setdefault("verifier", self.verifier)
        kwargs.setdefault("clock", lambda: self.now)
        if "adapter" not in kwargs:
            # 生产同构：真的 SSHAdapter + 离线假传输（protocol v2：绑定 hash/token/attempt）。
            kwargs["adapter"] = SSHAdapter(self.deployment.ssh, transport=self.transport,
                                           clock=lambda: self.now)
        kwargs.setdefault("monitor_check", self.monitor_ok)
        kwargs.setdefault("require_monitor_check", False)
        kwargs.setdefault("authorization_check", Admission(self.ws, store=self.store).check)
        kwargs.setdefault("allow_fixture_gates", True)
        return rw.RemoteWorkflow(self.ws, **kwargs)

    def make_no_stop_flow(self):
        """坏掉的适配层（报终态但无停止证据）：remote 层也必须拒。"""
        return self.make_flow(adapter=NoStopEvidenceAdapter(self.deployment.ssh,
                                                            transport=self.transport,
                                                            clock=lambda: self.now))

    def make_tampered_flow(self, tamper):
        """在终态回执上做手脚的适配层（argv 摘要 / 停止细节 / attempt 归属）。"""
        return self.make_flow(adapter=TamperAdapter(self.deployment.ssh, tamper=tamper,
                                                    transport=self.transport,
                                                    clock=lambda: self.now))

    def plan(self, plan_id="P1", *, argv=None, resources=None, execution=None, **overrides):
        payload = {
            "id": plan_id,
            "goal": "验证收紧入口参数是否提高通过题数",
            "hypothesis": {"id": "H1", "text": "收紧入口参数会让通过题数至少多 1 题"},
            "change_set": [{"id": "c1", "description": "把入口参数校验改成只接受整数",
                            "hypothesis_id": "H1"}],
            "criteria": {"mode": "confirmatory", "metric": "通过题数", "direction": "increase",
                         "threshold": 1, "source": "evidence/readings.md 上一轮逐题读数",
                         "rationale": "上一轮只差 1 题，门槛取 1",
                         # R394：显式 confirmatory 同时给机器可读门槛与对照读数。
                         "gate": {"k": 3, "n": 6, "direction": "ge"},
                         "control": {"k": 1, "n": 6, "source": "上一轮同 6 题基线实测 1/6"}},
            "baseline": {"label": "上一轮冻结包", "fingerprint": "sha256:deadbeef"},
            "case_sets": {"target": ["t1"], "regression": ["r1"], "control": ["c1"]},
            "previous_passes": ["r1"],
            "branches": {kind: {"action": action} for kind, action in
                         {"support": "保留改动", "contradict": "回退改动",
                          "inconclusive": "补一轮读数"}.items()},
            "budget": {"max_cost_units": 100, "max_seconds": 60},
            "input_files": ["evidence/readings.md"],
            "implementation": {"expected_behavior": "入口只接受整数时非法参数被拒绝",
                               "load_path": "submission/entry.py 的 parse_args",
                               "trigger": "评测器传入字符串参数时",
                               "observation": "非法参数返回非零退出码并打印原因",
                               "non_activation": "非法参数仍被接受并继续运行",
                               "files": [{"path": "submission/entry.py", "locator": "L1-L2"}]},
            "authorization": {"scope": "small", "series_id": "S-1",
                              "reservation_id": self.reservation["id"]},
            "execution_argv": list(argv if argv is not None else self.argv),
            "execution": execution if execution is not None else {
                "scope": "remote", "deployment_id": "dep-a",
                "remote": {
                    "unit_root": UNIT, "code_root": UNIT + "/code",
                    "environment_root": UNIT + "/env", "input_root": UNIT + "/input",
                    "output_root": UNIT + "/output",
                    "task_ids": ["t1"], "required_artifacts": ["results.json"],
                    "code_fingerprint": self.code_fingerprint,
                    "task_fingerprint": self.task_fingerprint,
                    "environment_fingerprint": self.environment_fingerprint,
                    "environment": {"kind": "venv", "packages": []},
                    "isolated_environment": True, "shared_mutable_hardlinks": False,
                    "resources": list(resources or [RESOURCE]),
                }},
        }
        payload.update(overrides)
        return payload

    def write_plan(self, payload, name="P1"):
        path = self.ws / "plans" / (name + ".json")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return "plans/" + name + ".json"

    def ready(self, *, flow=None, **kwargs):
        flow = flow or self.flow
        payload = self.plan(**kwargs)
        rel = self.write_plan(payload)
        flow.workflow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION)
        prepared = flow.workflow.gov.prepare_audit("P1", actor="main")
        flow.workflow.gov.record_audit(prepared["prepared_id"],
                                       audit_report(prepared["prepared_id"]),
                                       actor=REVIEWER_SESSION,
                                       evidence={"session_id": REVIEWER_SESSION, "seq": 7})
        return rel

    def provision(self, flow=None):
        return (flow or self.flow).provision()

    def submit(self, plan_rel, *, flow=None, argv=None, resources=(RESOURCE,)):
        flow = flow or self.flow
        return flow.submit(topic=TOPIC, plan=plan_rel, owner_session=SESSION,
                           argv=list(self.argv if argv is None else argv),
                           resources=list(resources), intent_none=True)

    # -- 只读核对 -----------------------------------------------------------

    def scalar(self, sql, args=()):
        conn = sqlite3.connect(str(self.db))
        try:
            row = conn.execute(sql, args).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def finishes(self):
        return self.scalar("SELECT COUNT(*) FROM wf_finishes") or 0

    def lease_rows(self):
        return self.scalar("SELECT COUNT(*) FROM resources") or 0

    def reserved_units(self):
        return self.scalar("SELECT reserved_units FROM budget_accounts WHERE series_id='S-1'")

    def attempt(self):
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM exec_attempts").fetchone()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 默认关闭与闸
# ---------------------------------------------------------------------------


class DeploymentGateTests(RemoteCase):
    def test_missing_deployment_refuses_before_any_request(self):
        self.deployment_path.unlink()
        flow = self.make_flow(deployment=None)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit("plans/P1.json", flow=flow)
        self.assertEqual(caught.exception.code, 9)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)

    def test_disabled_deployment_is_refused(self):
        self.write_deployment(dict(self.deployment_config, enabled=False))
        deployment, problems = rw.read_deployment(self.ws)
        self.assertIsNone(deployment)
        self.assertTrue(any("enabled" in item for item in problems), problems)
        doctor = rw.doctor(self.ws)
        self.assertFalse(doctor["can_submit"])
        self.assertEqual(self.transport.calls, [])

    def test_worker_must_be_provisioned_explicitly(self):
        plan_rel = self.ready()
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertIn("provision", str(caught.exception))
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)

    def test_fixture_gates_are_refused_without_explicit_allow(self):
        plan_rel = self.ready()
        flow = self.make_flow(allow_fixture_gates=False)
        self.provision(flow)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel, flow=flow)
        self.assertIn("夹具", str(caught.exception))
        self.assertEqual([call for call in self.transport.calls
                          if "start" in shlex.split(call[-1])], [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)

    def test_missing_budget_reservation_blocks_submit(self):
        plan_rel = self.ready()
        # 把预留释放掉：Admission 与 Runtime 都必须拒（同一笔预留）。
        self.accounts.release(self.reservation["id"], "release-1")
        self.provision()
        before = len(self.transport.calls)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertEqual(caught.exception.code, 9)
        self.assertIn("执行准入", str(caught.exception))
        self.assertEqual(len(self.transport.calls), before)  # 只有 provision 的调用
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)

    def test_budget_in_wrong_scope_is_refused(self):
        plan_rel = self.ready()
        self.provision()
        plan = self.plan()
        plan["authorization"]["scope"] = "large"   # 预留仍是 small
        self.write_plan(plan)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertEqual(caught.exception.code, 9)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)

    def test_argv_must_match_declared_plan(self):
        plan_rel = self.ready()
        self.provision()
        wrong = list(self.argv)
        wrong[3] = "t9"
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel, argv=wrong)
        self.assertEqual(caught.exception.code, 9)
        self.assertTrue(any("argv" in item for item in caught.exception.errors),
                        caught.exception.errors)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)
        self.assertEqual([call for call in self.transport.calls
                          if "start" in shlex.split(call[-1])], [])

    def test_ssh_wrapped_argv_is_refused(self):
        argv = ["ssh", "fake-box", "python3", "job.py"]
        plan = self.plan(argv=argv)
        self.write_plan(plan)
        flow = self.make_flow()
        flow.workflow.prepare(topic=TOPIC, plan="plans/P1.json", owner_session=SESSION)
        prepared = flow.workflow.gov.prepare_audit("P1", actor="main")
        flow.workflow.gov.record_audit(prepared["prepared_id"],
                                       audit_report(prepared["prepared_id"]),
                                       actor=REVIEWER_SESSION,
                                       evidence={"session_id": REVIEWER_SESSION, "seq": 7})
        self.provision(flow)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            flow.submit(topic=TOPIC, plan="plans/P1.json", owner_session=SESSION,
                        argv=argv, resources=[RESOURCE], intent_none=True)
        self.assertIn("ssh", str(caught.exception))

    def test_deployment_id_mismatch_is_refused(self):
        plan = self.plan()
        plan["execution"]["deployment_id"] = "dep-b"
        self.write_plan(plan)
        self.flow.workflow.prepare(topic=TOPIC, plan="plans/P1.json", owner_session=SESSION)
        prepared = self.flow.workflow.gov.prepare_audit("P1", actor="main")
        self.flow.workflow.gov.record_audit(prepared["prepared_id"],
                                            audit_report(prepared["prepared_id"]),
                                            actor=REVIEWER_SESSION,
                                            evidence={"session_id": REVIEWER_SESSION, "seq": 7})
        self.provision()
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit("plans/P1.json")
        self.assertIn("deployment_id", str(caught.exception))


class OwnershipTests(RemoteCase):
    def test_unowned_experiment_cannot_submit(self):
        plan_rel = self.ready()
        self.provision()
        with self.store.transaction() as conn:
            conn.execute("UPDATE backend_migrations SET backend='pending' WHERE exp_id='P1'")
        with self.assertRaises(rw.RemoteWorkflowError):
            self.submit(plan_rel)
        self.assertEqual([call for call in self.transport.calls
                          if "start" in shlex.split(call[-1])], [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)


# ---------------------------------------------------------------------------
# 提交、终态与 unknown
# ---------------------------------------------------------------------------


class SubmitTests(RemoteCase):
    def test_indistinguishable_criterion_refuses_before_any_remote_request(self):
        """R394 的远端入口：门槛被对照读数满足时，check 不绿、submit 零远端请求、零 run 记录。"""
        plan = self.plan()
        plan["criteria"]["gate"] = {"k": 3, "n": 6, "direction": "ge"}
        plan["criteria"]["control"] = {"k": 3, "n": 6, "source": "BASE 同 6 题"}
        plan_rel = self.write_plan(plan)
        checked = self.flow.check(topic=TOPIC, plan=plan_rel, owner_session=SESSION,
                                  argv=self.argv, resources=[RESOURCE], intent_none=True)
        self.assertFalse(checked["ok"])
        self.assertIsNone(checked.get("preflight_record"))
        self.assertIn("门槛被对照读数满足", "；".join(checked["problems"]))
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertEqual(caught.exception.code, 9)
        # 真实适配器一次都没被调用：没有远端请求，也没有 run / finish 记录。
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)
        self.assertEqual(self.finishes(), 0)

    def test_successful_submit_returns_launch_record_bound_to_the_run(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.assertTrue(result["ok"], result)
        record = result["launch_record"]
        self.assertEqual(record["kind"], "launch")
        self.assertTrue(record["launched"])
        self.assertEqual(record["run_id"], result["run_id"])
        self.assertEqual(record["argv"], self.argv)
        self.assertEqual(record["plan"]["sha256"],
                         self.scalar("SELECT plan_sha256 FROM wf_runs WHERE run_id=?",
                                     (result["run_id"],)))
        self.assertEqual(record["gates"]["remote"]["execution_scope"], "remote")

    def test_successful_submit_binds_argv_budget_and_lease(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["submitted"])
        self.assertEqual(result["state"], "running")
        # 同一个 budget_reservations 台账：没有第二笔 run 预留。
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM budget_reservations"), 1)
        spec = json.loads(self.attempt()["spec_json"])
        self.assertEqual(spec["execution_config"]["argv"], self.argv)
        self.assertEqual(spec["reservation_id"], self.reservation["id"])
        self.assertEqual(spec["exp_id"], "P1")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM reservation_bindings"), 1)
        # 资源租约：付费任务 + 显式资源 + token 就是 lease_id。
        self.assertEqual(self.scalar("SELECT paid FROM tasks"), 1)
        self.assertEqual(json.loads(self.scalar("SELECT resource_keys FROM tasks")), [RESOURCE])
        task_token = self.scalar("SELECT token FROM tasks")
        self.assertEqual(task_token, spec["lease_id"])
        self.assertEqual(self.scalar("SELECT token FROM resources"), task_token)
        # 远端请求确实走了 ssh 命令（单条 command 字符串），payload 走 stdin JSON。
        commands = self.transport.ssh_commands()
        self.assertTrue(commands)
        self.assertNotIn("job.py", commands[0])
        self.assertEqual(self.transport.only_job()["request"]["argv"], self.argv)
        # run 记录：cwd 是远端运行单元（不是本地路径），终态未核 ⇒ 还没有结束记录。
        self.assertEqual(self.scalar("SELECT cwd FROM wf_runs"), UNIT)
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.reserved_units(), 100)
        self.assertEqual(self.lease_rows(), 1)

        # 同一笔预算预留：Admission 与 Runtime 核的都是 plan.authorization 那笔（id/exp/scope 全一致）。
        self.assertEqual(self.scalar("SELECT exp_id FROM budget_reservations"), "P1")
        self.assertEqual(self.scalar("SELECT scope FROM budget_reservations"), "small")
        self.assertEqual(json.loads(self.attempt()["spec_json"])["exp_id"], "P1")
        connection = sqlite3.connect(str(self.db))
        try:
            binding = connection.execute(
                "SELECT reservation_id,request_key,exp_id,lease_token FROM reservation_bindings"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(binding[0], self.reservation["id"])
        self.assertEqual(binding[2], "P1")
        self.assertEqual(binding[3], self.scalar("SELECT token FROM tasks"))
        # 远端 job 身份也钉住了：request_key 在 attempt 与执行绑定里是同一个。
        self.assertEqual(self.scalar("SELECT request_key FROM exec_attempts"), binding[1])

    def test_large_scope_without_approved_decision_is_refused(self):
        """large / submission 必须另有 approved 决策：Admission 拒 ⇒ 提交前就停。"""
        plan_rel = self.ready()
        self.provision()
        plan = self.plan()
        plan["authorization"]["scope"] = "large"
        self.write_plan(plan)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertEqual(caught.exception.code, 9)
        self.assertIn("执行准入", str(caught.exception))
        self.assertEqual([call for call in self.transport.calls
                          if "start" in shlex.split(call[-1])], [])

    def test_start_response_lost_is_unknown_and_keeps_everything(self):
        plan_rel = self.ready()
        self.provision()
        self.transport.fail_start_response = True
        result = self.submit(plan_rel)
        self.assertEqual(result["state"], "unknown", result)
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.reserved_units(), 100)
        self.assertEqual(self.lease_rows(), 1)
        # 远端其实已经在跑：query 之后按 worker 记录走。
        self.transport.fail_start_response = False
        self.transport.finalize("completed")
        queried = self.flow.query(run_id=result["run_id"])
        self.assertTrue(queried["finished"], queried)
        self.assertEqual(queried["finish"]["reason"], "completed")
        self.assertEqual(queried["finish"]["exitcode"], 0)
        self.assertEqual(self.finishes(), 1)
        # 预算仍不自动结算（没有可信计量）。
        self.assertEqual(self.reserved_units(), 100)
        self.assertEqual(self.scalar("SELECT status FROM budget_reservations"), "reserved")

    def test_query_timeout_is_unknown_and_never_finishes(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.transport.fail_query = True
        queried = self.flow.query(run_id=result["run_id"])
        self.assertEqual(queried["code"], 3, queried)
        self.assertFalse(queried["finished"])
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.reserved_units(), 100)
        self.assertEqual(self.lease_rows(), 1)

    def test_terminal_requires_token_probe(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.transport.finalize("completed")
        # 远端 state.json 里的 token 与请求不符（模拟 request token 核不过）
        self.transport.only_job()["token"] = "f" * 48
        queried = self.flow.query(run_id=result["run_id"])
        self.assertFalse(queried["finished"], queried)
        self.assertEqual(queried["code"], 3)
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.reserved_units(), 100)
        self.assertEqual(self.lease_rows(), 1)
        # token 对得上之后才允许登记。
        job = self.transport.only_job()
        job["token"] = job["request"]["token"]
        again = self.flow.query(run_id=result["run_id"])
        self.assertTrue(again["finished"], again)

    def test_argv_digest_mismatch_is_unknown(self):
        """远端回执的 argv 摘要与冻结 argv 不符 ⇒ 不登记终态、不释放资源。"""
        plan_rel = self.ready()
        self.provision()
        flow = self.make_tampered_flow(
            lambda evidence: dict(evidence, argv_sha256="0" * 64))
        result = self.submit(plan_rel, flow=flow)
        self.transport.finalize("completed")
        queried = flow.query(run_id=result["run_id"])
        self.assertFalse(queried["finished"], queried)
        self.assertEqual(queried["code"], 3)
        self.assertIn("argv", "；".join(queried["problems"]))
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.lease_rows(), 1)
        with self.assertRaises(rw.RemoteWorkflowError):
            flow.release(run_id=result["run_id"])

    def test_argv_digest_missing_is_unknown(self):
        """回执没有 argv 摘要 ⇒ 不能证明跑的是计划声明的 argv ⇒ unknown。"""
        plan_rel = self.ready()
        self.provision()
        flow = self.make_tampered_flow(
            lambda evidence: {key: value for key, value in evidence.items()
                              if key != "argv_sha256"})
        result = self.submit(plan_rel, flow=flow)
        self.transport.finalize("completed")
        queried = flow.query(run_id=result["run_id"])
        self.assertFalse(queried["finished"], queried)
        self.assertIn("argv_sha256", "；".join(queried["problems"]))
        self.assertEqual(self.finishes(), 0)

    def test_timestamp_only_is_not_a_stop_proof(self):
        """把 stop_evidence 换成 stopped_at 时间戳 ⇒ 伪停，必须拒。"""
        plan_rel = self.ready()
        self.provision()

        def tamper(evidence):
            evidence.pop("stop_evidence", None)
            evidence["stop_verified"] = True
            evidence["stopped_at"] = 123.0
            return evidence

        flow = self.make_tampered_flow(tamper)
        result = self.submit(plan_rel, flow=flow)
        self.transport.finalize("completed")
        queried = flow.query(run_id=result["run_id"])
        self.assertFalse(queried["finished"], queried)
        self.assertEqual(queried["code"], 3)
        self.assertIn("stop_evidence", "；".join(queried["problems"]))
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.lease_rows(), 1)

    def test_terminal_without_stop_evidence_is_unknown(self):
        """只有终态标签、没有停止证据 ⇒ 绝不登记终态，也绝不释放资源。"""
        plan_rel = self.ready()
        self.provision()
        flow = self.make_no_stop_flow()
        result = self.submit(plan_rel, flow=flow)
        self.transport.finalize("completed")
        queried = flow.query(run_id=result["run_id"])
        self.assertFalse(queried["finished"], queried)
        self.assertEqual(queried["code"], 3)
        self.assertIn("没有停止证明", "；".join(queried["problems"]))
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.reserved_units(), 100)
        self.assertEqual(self.lease_rows(), 1)
        with self.assertRaises(rw.RemoteWorkflowError):
            flow.release(run_id=result["run_id"])

    def test_stop_evidence_is_required_by_the_adapter_too(self):
        """worker 报终态但 stop_verified 不是 true ⇒ 适配层已降级 unknown（本层看不到终态）。"""
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        job = self.transport.only_job()
        job.update({"state": "failed", "exitcode": 3, "finished_at": 3.0,
                    "stop_verified": False})
        queried = self.flow.query(run_id=result["run_id"])
        self.assertFalse(queried["finished"], queried)
        self.assertEqual(queried["code"], 3)
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.lease_rows(), 1)

    def test_second_submit_of_same_plan_is_refused(self):
        plan_rel = self.ready()
        self.provision()
        first = self.submit(plan_rel)
        before = len(self.transport.calls)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertIn("拒绝提交", str(caught.exception))
        self.assertTrue(any(word in str(caught.exception)
                            for word in ("已经启动过", "已有实际启动记录", "重跑同一轮")),
                        str(caught.exception))
        self.assertEqual(len(self.transport.calls), before)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 1)
        self.assertEqual(first["run_id"], self.scalar("SELECT run_id FROM wf_runs"))

    def test_failed_remote_job_records_real_exitcode(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.transport.exitcode = 7
        self.transport.finalize("failed")
        queried = self.flow.query(run_id=result["run_id"])
        self.assertTrue(queried["finished"], queried)
        self.assertEqual(queried["finish"]["reason"], "failed")
        self.assertEqual(queried["finish"]["exitcode"], 7)

    def test_hard_timeout_is_recorded_as_timeout_without_exitcode(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        job = self.transport.finalize("failed")   # 带 stop_verified=true 的硬超时
        job.update({"exitcode": None, "signal": "SIGTERM", "timed_out": True})
        queried = self.flow.query(run_id=result["run_id"])
        self.assertTrue(queried["finished"], queried)
        self.assertEqual(queried["finish"]["reason"], "timeout")
        self.assertIsNone(queried["finish"]["exitcode"])


class CancelTests(RemoteCase):
    def test_cancel_is_token_verified_and_recorded(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        cancelled = self.flow.cancel(run_id=result["run_id"])
        self.assertTrue(cancelled["cancel_sent"], cancelled)
        self.assertTrue(cancelled["finished"], cancelled)
        self.assertEqual(self.scalar("SELECT reason FROM wf_finishes"), "stopped_by_user")
        self.assertEqual(self.scalar("SELECT state FROM exec_attempts"), "cancelled")
        self.assertEqual(self.reserved_units(), 100)  # 预算仍不自动结算
        released = self.flow.release(run_id=result["run_id"])
        self.assertEqual(released["released"], [RESOURCE])
        self.assertEqual(self.lease_rows(), 0)

    def test_cancel_with_wrong_token_stays_unknown(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.transport.only_job()["token"] = "e" * 48
        cancelled = self.flow.cancel(run_id=result["run_id"])
        self.assertTrue(cancelled["cancel_sent"])
        self.assertFalse(cancelled["finished"])
        self.assertEqual(cancelled["code"], 3)
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.reserved_units(), 100)
        self.assertEqual(self.lease_rows(), 1)

    def test_cancel_without_stop_evidence_is_not_finished(self):
        """安全 cancel 发出去了，但没有停止证据 ⇒ 不登记终态（资源保留）。"""
        plan_rel = self.ready()
        self.provision()
        flow = self.make_no_stop_flow()
        result = self.submit(plan_rel, flow=flow)
        cancelled = flow.cancel(run_id=result["run_id"])
        self.assertTrue(cancelled["cancel_sent"])
        self.assertFalse(cancelled["finished"])
        self.assertEqual(cancelled["code"], 3)
        self.assertEqual(self.finishes(), 0)
        self.assertEqual(self.lease_rows(), 1)

    def test_cancel_is_not_blocked_by_bookkeeping_failure(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)

        def broken_finish(**kwargs):
            raise rw.WorkflowError("模拟结束登记失败", code=2)

        self.flow.workflow.finish = broken_finish
        cancelled = self.flow.cancel(run_id=result["run_id"])
        self.assertTrue(cancelled["cancel_sent"])
        self.assertTrue(cancelled["ok"])
        self.assertEqual(cancelled["code"], 3)
        self.assertIn("登记", cancelled["note"])
        # 远端确实收到了停止（token 核过），本地登记失败不改变安全停。
        self.assertEqual(self.transport.only_job()["state"], "cancelled")
        self.assertTrue(self.transport.ssh_commands())


class QueryUnknownResourceTests(RemoteCase):
    def test_unknown_keeps_reservation_and_lease_then_release_after_terminal(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.transport.fail_query = True
        queried = self.flow.query(run_id=result["run_id"])
        self.assertEqual(queried["code"], 3)
        self.assertEqual(self.lease_rows(), 1)
        self.assertEqual(self.reserved_units(), 100)
        self.transport.fail_query = False
        self.transport.finalize("completed")
        queried = self.flow.query(run_id=result["run_id"])
        self.assertTrue(queried["finished"], queried)
        released = self.flow.release(run_id=result["run_id"])
        self.assertEqual(released["released"], [RESOURCE])
        self.assertEqual(self.lease_rows(), 0)
        self.assertEqual(self.scalar("SELECT status FROM budget_reservations"), "reserved")
        # 预算结算仍受既有护栏约束：没有可信计量就不写 settled。
        self.assertIn("未结算", released["budget_note"])


class LaunchRecheckTests(RemoteCase):
    """闸之后的时序缝：claim 与 send 两处都要用**同一段**只读复核挡住。

    ⛔ 反例覆盖父会话点名的四种变化：改来源 / 撤回预算 / 审计被新阻断 / 监控新增待审。
    判据一律是 **adapter.submit 调用次数 = 0**（��个 `start` 子命令都不发）。
    """

    def _patch_after(self, name, hook):
        original = getattr(self.flow, name)

        def wrapped(*args, **kwargs):
            outcome = original(*args, **kwargs)
            hook()
            return outcome

        setattr(self.flow, name, wrapped)

    def _starts(self):
        return sum(1 for call in self.transport.calls if "start" in shlex.split(call[-1]))

    def test_source_change_during_lease_acquisition_never_sends(self):
        plan_rel = self.ready()
        self.provision()

        def hook():
            (self.ws / "evidence" / "readings.md").write_text("闸后改动的读数\n", encoding="utf-8")

        self._patch_after("_acquire_lease", hook)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertIn("准备来源", str(caught.exception))
        self.assertEqual(self._starts(), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)
        self.assertEqual(self.lease_rows(), 0)   # 没发请求 ⇒ 租约已还
        self.assertEqual(self.reserved_units(), 100)

    def test_budget_revoked_during_lease_acquisition_never_sends(self):
        plan_rel = self.ready()
        self.provision()

        def hook():
            self.accounts.release(self.reservation["id"], "revoke-after-claim")

        self._patch_after("_acquire_lease", hook)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.submit(plan_rel)
        self.assertIn("执行准入", str(caught.exception))
        self.assertEqual(self._starts(), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)

    def test_audit_blocked_after_claim_never_sends(self):
        plan_rel = self.ready()
        self.provision()

        class BlockedAudit:
            def audit_state(self, plan_id):
                return {"ok": False, "decision": "blocked",
                        "problems": ["claim 之后出现新阻断"]}

        def hook():
            self.flow.workflow._gov_ro = BlockedAudit()

        self._patch_after("_claim_run", hook)
        result = self.submit(plan_rel)
        self.assertEqual(self._starts(), 0)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["reason"], "launch_error")
        self.assertIn("审计", "；".join(result["problems"]))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 1)
        self.assertEqual(self.scalar("SELECT reason FROM wf_finishes"), "launch_error")

    def test_monitor_trigger_after_claim_never_sends(self):
        plan_rel = self.ready()
        self.provision()

        def blocked_monitor(owner_session, exp_id, action="launch"):
            return {"ok": False, "problems": ["claim 之后出现新的用户原话"],
                    "registered": True, "state": "none", "intent_id": None}

        def hook():
            self.flow.workflow.monitor_check = blocked_monitor

        self._patch_after("_claim_run", hook)
        result = self.submit(plan_rel)
        self.assertEqual(self._starts(), 0)
        self.assertEqual(result["state"], "failed")
        self.assertIn("监控", "；".join(result["problems"]))
        self.assertEqual(self.scalar("SELECT reason FROM wf_finishes"), "launch_error")


class ReleaseOwnershipTests(RemoteCase):
    def test_release_refuses_other_attempt(self):
        """停止探针回报的是别的 attempt ⇒ 不能拿它释放这个租约。"""
        plan_rel = self.ready()
        self.provision()
        flow = self.make_tampered_flow(
            lambda evidence: dict(evidence, attempt_id="deadbeef" * 4))
        result = self.submit(plan_rel, flow=flow)
        self.transport.finalize("completed")
        queried = flow.query(run_id=result["run_id"])
        self.assertFalse(queried["finished"], queried)
        self.assertIn("attempt", "；".join(queried["problems"]))
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            flow.release(run_id=result["run_id"])
        self.assertEqual(caught.exception.code, 3, str(caught.exception))
        self.assertEqual(self.lease_rows(), 1)
        self.assertEqual(self.finishes(), 0)


class LifecycleProjectionTests(RemoteCase):
    """起跑投影：远端 run 落库后立刻 sync，不等终态。

    `workflow.start()` 在 run 落库后调 `_lifecycle_call('sync', …)`；受管远端必须同形，
    否则 `running` 事件 / AI_WORK / 起跑收据要等到终态才出现。
    """

    def events(self):
        from kernel.autoresearch import reporting
        path = reporting.events_path(self.ws, TOPIC)
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def test_submit_projects_running_event_before_terminal(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        self.assertTrue(result["lifecycle_sync"]["ok"], result["lifecycle_sync"])
        self.assertTrue(result["lifecycle_sync"]["wired"])
        events = self.events()
        running = [event for event in events if event.get("kind") == "running"]
        self.assertTrue(running, events)
        self.assertEqual(running[0]["event_id"], "wf-start-" + result["run_id"])
        self.assertIn(result["run_id"], json.dumps(running[0], ensure_ascii=False))
        # run 还没结束（也没有伪造终态）。
        self.assertEqual(self.finishes(), 0)
        # 起跑收据 / AI_WORK 也已经在工作区里投影出来（不是只在库里）。
        self.assertTrue(list((self.ws / ".autoresearch" / "state").rglob("*")))

    def test_unknown_submit_keeps_start_projection(self):
        plan_rel = self.ready()
        self.provision()
        self.transport.fail_start_response = True
        result = self.submit(plan_rel)
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(result["lifecycle_sync"]["ok"], result["lifecycle_sync"])
        running = [event for event in self.events() if event.get("kind") == "running"]
        self.assertTrue(running)
        self.assertEqual(running[0]["event_id"], "wf-start-" + result["run_id"])
        self.assertEqual(self.finishes(), 0)

    def test_lifecycle_failure_does_not_fake_terminal_state(self):
        plan_rel = self.ready()
        self.provision()
        self.flow.workflow._lifecycle_call = lambda *args, **kwargs: {
            "ok": False, "wired": True, "error": "模拟投影失败"}
        result = self.submit(plan_rel)
        self.assertFalse(result["lifecycle_sync"]["ok"])
        self.assertEqual(result["state"], "running")     # 远端已受理，不受投影失败影响
        self.assertEqual(self.finishes(), 0)             # ⛔ 不伪终态
        self.assertTrue(result["submitted"])
        # 安全 cancel 仍然可用（不被报告失败挡）。
        cancelled = self.flow.cancel(run_id=result["run_id"])
        self.assertTrue(cancelled["cancel_sent"])
        self.assertTrue(cancelled["finished"])


class CollectTests(RemoteCase):
    def test_collect_requires_completed_and_verified_terminal(self):
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        with self.assertRaises(rw.RemoteWorkflowError) as caught:
            self.flow.collect(run_id=result["run_id"])
        self.assertEqual(caught.exception.code, 9)  # 还在 running
        self.transport.finalize("completed")
        self.flow.query(run_id=result["run_id"])
        collection = self.flow.collect(run_id=result["run_id"])
        self.assertTrue(collection["ok"], collection)
        self.assertEqual(collection["attempt"]["state"], "completed")


# ---------------------------------------------------------------------------
# 只读检查与 doctor / CLI
# ---------------------------------------------------------------------------


class ReadOnlyTests(RemoteCase):
    def test_check_is_read_only_and_lists_what_is_missing(self):
        plan_rel = self.ready()
        before = sorted(p.relative_to(self.ws).as_posix()
                        for p in self.ws.rglob("*") if p.is_file())
        call_counter = len(self.transport.calls)
        flow = rw.RemoteWorkflow(self.ws, deployment=self.deployment, readonly=True)
        preview = flow.check(topic=TOPIC, plan=plan_rel, owner_session=SESSION,
                             argv=self.argv, resources=[RESOURCE], intent_none=True)
        self.assertFalse(preview["ok"])
        self.assertIn("监控", "；".join(preview["problems"]))  # 只读实例没有注入监控
        self.assertEqual(len(self.transport.calls), call_counter)  # 零远端调用
        after = sorted(p.relative_to(self.ws).as_posix()
                       for p in self.ws.rglob("*") if p.is_file())
        self.assertEqual(before, after)

    def test_check_previews_run_spec_when_gates_are_wired(self):
        plan_rel = self.ready()
        self.provision()
        flow = self.make_flow(readonly=True)
        preview = flow.check(topic=TOPIC, plan=plan_rel, owner_session=SESSION,
                             argv=self.argv, resources=[RESOURCE], intent_none=True)
        self.assertTrue(preview["ok"], preview["problems"])
        self.assertEqual(preview["run_spec"]["execution_config"]["argv"], self.argv)
        self.assertEqual(preview["run_spec"]["reservation_id"], self.reservation["id"])
        self.assertEqual(preview["budget"]["status"], "reserved")
        # R395：预检记录与本地同源，且**不冒充已启动**
        record = preview["preflight_record"]
        self.assertEqual(record["kind"], "preflight")
        self.assertFalse(record["launched"])
        self.assertNotIn("run_id", record)
        self.assertEqual(record["argv"], self.argv)
        self.assertEqual(record["gates"]["remote"]["stage"], "preview")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM wf_runs"), 0)
        self.assertEqual(self.finishes(), 0)

    def test_doctor_reports_honestly_without_network(self):
        self.provision()
        report = rw.doctor(self.ws, plan_id="P1")
        self.assertTrue(report["checks"]["deployment"]["configured"])
        self.assertTrue(report["checks"]["worker"]["matches"])
        self.assertTrue(report["checks"]["ownership"]["verified"])
        self.assertIsNone(report["checks"]["deployment"]["public"].get("key_file"))
        self.assertIn("未连接", report["network"])
        probe = report["checks"]["fingerprint_probe"]
        for key in ("code_fingerprint", "task_fingerprint", "environment_fingerprint"):
            self.assertIn("ssh_worker", probe[key])
            self.assertIn("spec_from_file_location", probe[key])
        self.assertEqual(self.transport.calls[0][0], "ssh")  # 只有 provision 那一次

    def test_fingerprint_probe_commands_are_executable(self):
        """`fingerprint_probe` 给的命令能按「worker 文件路径」导入并算出与 worker 相同的值。

        ⚠️ 这证明的是**命令可复制执行**（本机用当前 worker 文件与本地目录跑一遍），
        不是「远端机器上已跑过」。
        """
        worker_file = Path(rw.__file__).with_name("backends") / "ssh_worker.py"
        block = {"unit_root": str(self.unit), "code_root": str(self.unit / "code"),
                 "environment_root": str(self._venv_root),
                 "input_root": str(self.unit / "input"),
                 "environment": {"kind": "existing", "python": self.venv_python}}
        probe = rw.fingerprint_probe(block, worker_path=str(worker_file))
        expected = {
            "code_fingerprint": worker.code_fingerprint(self.unit / "code"),
            "task_fingerprint": worker.task_fingerprint(self.unit / "input"),
            "environment_fingerprint": worker.environment_fingerprint(
                self.venv_python, [], str(self._venv_root / "pyvenv.cfg")),
        }
        for key, value in expected.items():
            command = probe[key]
            self.assertTrue(command.startswith("python3 -I -c \""), command)
            program = command.split(" -c ", 1)[1].strip()
            self.assertTrue(program.startswith('"') and program.endswith('"'), program)
            program = program[1:-1]
            outcome = subprocess.run([sys.executable, "-I", "-c", program],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     timeout=120)
            self.assertEqual(outcome.returncode, 0, outcome.stderr.decode("utf-8", "replace"))
            self.assertEqual(outcome.stdout.decode("utf-8").strip(), value, key)

    def test_offline_unit_layout_is_what_the_fake_transport_serves(self):
        """离线 driver 往 unit 里放代码/题单的方式：直接写本地映射目录（= 真机的 scp 落点）。

        真实上传（scp 代码与题单到 `unit_root/code|input`）不在本文件里；这里把离线替身的
        布局固定下来，避免「测试里能看到、真机上却没人传」的误解。
        """
        self.assertTrue((self.unit / "code" / "job.py").is_file())
        self.assertTrue((self.unit / "input" / "tasks" / "t1.txt").is_file())
        self.assertTrue((self.unit / "input" / "manifest.json").is_file())
        # 计划里声明的三个指纹就是对这些真实字节算出来的（不是写死的串）。
        self.assertEqual(worker.code_fingerprint(self.unit / "code"), self.code_fingerprint)
        self.assertEqual(worker.task_fingerprint(self.unit / "input"), self.task_fingerprint)
        self.assertEqual(self.environment_fingerprint,
                         worker.environment_fingerprint(
                             self.venv_python, [], str(self._venv_root / "pyvenv.cfg")))
        # 假传输把 /remote/... 映射到本地这套目录（真机上由 scp 落到同样的相对位置）。
        self.assertEqual(Path(self.transport.local(UNIT + "/code")),
                         (self.root / "root" / REMOTE.lstrip("/") / "units" / "P1" / "code").resolve())


class RemoteCliTests(RemoteCase):
    def test_help_and_doctor_are_read_only(self):
        from kernel.autoresearch import remote_cli
        with self.assertRaises(SystemExit) as caught:
            remote_cli.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        before = sorted(p.relative_to(self.ws).as_posix()
                        for p in self.ws.rglob("*") if p.is_file())
        code = remote_cli.main(["doctor", "--workspace", str(self.ws), "--plan-id", "P1"])
        after = sorted(p.relative_to(self.ws).as_posix()
                       for p in self.ws.rglob("*") if p.is_file())
        self.assertEqual(code, 2)  # 还没 provision ⇒ 诚实拒绝
        self.assertEqual(before, after)

    def test_cli_status_is_read_only(self):
        from kernel.autoresearch import remote_cli
        plan_rel = self.ready()
        self.provision()
        result = self.submit(plan_rel)
        before = sorted(p.relative_to(self.ws).as_posix()
                        for p in self.ws.rglob("*") if p.is_file())
        code = remote_cli.main(["status", "--workspace", str(self.ws),
                                "--run-id", result["run_id"]])
        after = sorted(p.relative_to(self.ws).as_posix()
                       for p in self.ws.rglob("*") if p.is_file())
        self.assertEqual(code, 0)
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# service.py 的预算作用域映射（默认兼容 + 显式作用域）
# ---------------------------------------------------------------------------


class RuntimeBudgetScopesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "db.sqlite3")
        self.evidence = ApprovalEvidence({"receiver": SourcePolicy(HMACSource(KEY),
                                                                   frozenset({"human"}))})
        self.accounts = Accounts(self.store, self.evidence)
        from kernel.autoresearch.runtime.scheduler import Scheduler
        Scheduler(self.store).activate("E1")
        event = {"event_id": "g1", "principal": "human", "exp_id": "E1",
                 "series_id": "small-series", "scope": "small",
                 "expires_at": time.time() + 3600, "total_units": 50, "action": "grant"}
        event["signature"] = hmac.new(KEY, canonical_json(event).encode(), hashlib.sha256).hexdigest()
        self.accounts.authorize("receiver", event, "auth-1")
        self.accounts.reserve("small-series", "E1", "small", 50, "reserve-small")

    def test_default_runtime_still_requires_run_scope(self):
        runtime = Runtime(self.store)
        runtime.request_approval("E1", self.store.get_experiment("E1")["version"])
        with self.assertRaises(Exception) as caught:
            runtime.prepare("E1", self.store.get_experiment("E1")["version"])
        self.assertIn("run", str(caught.exception).lower())

    def test_explicit_scope_accepts_the_same_authorization_reservation(self):
        runtime = Runtime(self.store, budget_scopes=("small",))
        self.assertEqual(runtime.budget_scopes, ("small",))
        runtime.request_approval("E1", self.store.get_experiment("E1")["version"])
        prepared = runtime.prepare("E1", self.store.get_experiment("E1")["version"])
        self.assertEqual(prepared["state"], "ready")

    def test_unknown_scope_is_rejected_at_construction(self):
        with self.assertRaises(Exception):
            Runtime(self.store, budget_scopes=("gpu",))
        with self.assertRaises(Exception):
            Runtime(self.store, budget_scopes=())


if __name__ == "__main__":
    unittest.main()
