# GENERATED-BY: ssh worker safety repair, 2026-11-10
# LAST-EDIT: 2026-11-10 首版：取消意向/跨进程锁、进程组停止证明、超时/主进程退出不冒终态、
#            request-hash/token/attempt 绑定、安全 cancel 不被本地报告阻挡。
# PURPOSE: 用可控假 process / 假进程组 / 假传输证明 SSH worker 与 adapter 的停止与绑定语义；
#          ⛔ 不依赖 POSIX（真实进程组枚举只在 POSIX 远端成立，本机不能现场验）。
# ORIGIN: none（新文件）。被测对象：kernel/autoresearch/backends/ssh_worker.py 与 ssh.py。
# CUMULATIVE-DELTA: 首版。所有 worker 测试都在本机（含 Windows）可跑：真实子进程只在
#          「跨进程锁」一条里用到，其余用 mock 替换 process_birth / group_gone / _term_group /
#          _kill_group / _popen / _write_json，因此「组仍活」「主进程先退」「报告写失败」都能确定性复现。
"""SSH 停止安全与请求绑定测试（平台无关）。

覆盖的缺陷：
1. `cmd_cancel` 在没有 pid 时**不许**直接记 cancelled；只落取消意向，并阻止之后的晚启动。
2. `cmd_supervise` 的硬超时分支 TERM→KILL 后若组仍在，**不许**记 failed，只能 unknown。
3. 主进程 exit 0 / 非 0 都不证明同组子进程已停；证明不了 ⇒ unknown，保留 pid/birth。
4. `query` / `collect` / `cancel` 的回报必须和 request_hash / token / attempt_id 绑定。
5. 安全 cancel 不被本地 receipt 写失败（报告层）阻挡。

⚠️ 真实 POSIX 进程组（`killpg` + `/proc` 枚举）在本 Windows 主机**没有现场验**：
`group_gone` 在非 POSIX 返回 None（=不能证明），本文件用假组证明上层逻辑；真实远端仍需在 POSIX 机器上验。
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch.backends import ssh as sshmod  # noqa: E402
from kernel.autoresearch.backends import ssh_worker as worker  # noqa: E402
from kernel.autoresearch.runtime.execution import canonical, digest  # noqa: E402

TOKEN = "a" * 48
ATTEMPT = "attempt-1"


def patched(testcase, **values):
    """临时替换 worker 模块里的名字（假 process / 假组 / 假 spawn）。"""
    for name, value in values.items():
        patcher = mock.patch.object(worker, name, value)
        patcher.start()
        testcase.addCleanup(patcher.stop)


class FakeProcess:
    """可控假子进程：按队列返回 wait 结果；`TimeoutExpired` 实例会原样抛出。"""

    def __init__(self, pid: int, waits):
        self.pid = pid
        self._waits = list(waits)
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if not self._waits:
            raise subprocess.TimeoutExpired("fake-process", timeout)
        value = self._waits.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class WorkerCase(unittest.TestCase):
    """在临时目录里造出 worker 真实布局：unit/code|env|input|output + request.json + state.json。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)

    def build(self, *, state="starting", pid=None, birth=None, token=TOKEN,
              attempt=ATTEMPT):
        unit = self.root / "unit"
        for name in ("code", "env", "input", "output"):
            (unit / name).mkdir(parents=True, exist_ok=True)
        spec = {
            "unit_root": str(unit),
            "code_root": str(unit / "code"),
            "environment_root": str(unit / "env"),
            "input_root": str(unit / "input"),
            "output_root": str(unit / "output"),
            "task_ids": ["t1"],
            "required_artifacts": ["results.json"],
            "code_fingerprint": "a" * 64,
            "task_fingerprint": worker.sha_bytes(
                worker.canonical({"t1": "b" * 64}).encode("utf-8")),
            "environment_fingerprint": "c" * 64,
            "isolated_environment": True,
            "shared_mutable_hardlinks": False,
        }
        request = {
            "spec": spec,
            "argv": ["python3", str(unit / "code" / "job.py")],
            "timeout_s": 30,
            "environment": {"kind": "existing", "python": sys.executable, "packages": []},
            "python": None,
            "attempt_id": attempt,
            "output_dir": str(unit / "output" / "a1"),
            "token": token,
        }
        request["request_hash"] = worker.request_hash(request)
        environment = {"python": sys.executable, "prefix": str(unit / "env"),
                       "fingerprint": "c" * 64, "packages": []}
        document = {
            "request_hash": request["request_hash"],
            "attempt_id": attempt,
            "state": state,
            "token": token,
            "pid": pid,
            "birth": birth,
            "started_at": None if state == "starting" else 1.0,
            "finished_at": None,
            "timeout_s": 30.0,
            "argv": worker.launch_argv(request, environment),
            "spec": spec,
            "environment": environment,
            "output_dir": request["output_dir"],
            "exitcode": None,
            "signal": None,
            "timed_out": False,
            "reason": None,
            "protocol": worker.WORKER_VERSION,
            "cancel_requested": False,
            "stop_verified": False,
            "stop_evidence": None,
        }
        state_dir = unit / ".ssh_worker" / request["request_hash"][:32]
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
        (state_dir / "state.json").write_text(json.dumps(document), encoding="utf-8")
        return types.SimpleNamespace(unit=unit, state_dir=state_dir, request=request,
                                     state=document)

    @staticmethod
    def read_state(fixture):
        return json.loads((fixture.state_dir / "state.json").read_text(encoding="utf-8"))

    @staticmethod
    def write_state(fixture, document):
        (fixture.state_dir / "state.json").write_text(json.dumps(document), encoding="utf-8")

    @staticmethod
    def cancel(fixture, token=TOKEN):
        return worker.cmd_cancel(types.SimpleNamespace(state_dir=str(fixture.state_dir),
                                                       token=token))


class CancelIntentTests(WorkerCase):
    """缺陷 1：无 pid 的取消不能冒充已停，且必须挡住之后的一次晚启动。"""

    def test_cancel_without_pid_records_intent_but_never_claims_stopped(self):
        fixture = self.build(state="starting")
        result = self.cancel(fixture)
        document = self.read_state(fixture)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["state"], "unknown")
        self.assertNotIn(result["state"]["state"], ("cancelled", "failed", "completed"))
        self.assertTrue((fixture.state_dir / "cancel.json").is_file())
        self.assertTrue(document["cancel_requested"])
        self.assertFalse(document["stop_verified"])
        self.assertEqual(document["state"], "unknown")
        marker = json.loads((fixture.state_dir / "cancel.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["request_hash"], fixture.request["request_hash"])
        self.assertEqual(marker["token"], TOKEN)

    def test_late_supervise_after_cancel_intent_never_spawns(self):
        fixture = self.build(state="starting")
        self.cancel(fixture)
        spawned = []

        def fake_popen(argv, **kwargs):
            spawned.append(argv)
            raise AssertionError("取消意向已落盘，supervisor 不许再 spawn")

        patched(self, _popen=fake_popen)
        result = worker.cmd_supervise(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        document = self.read_state(fixture)
        self.assertEqual(spawned, [])
        self.assertTrue(result["cancelled"])
        self.assertEqual(document["state"], "cancelled")
        self.assertTrue(document["stop_verified"])
        self.assertEqual(document["stop_evidence"]["method"], "never-launched")
        self.assertEqual(document["reason"], "cancel-before-launch")

    def test_cancel_marker_alone_blocks_late_supervise(self):
        fixture = self.build(state="starting")
        (fixture.state_dir / "cancel.json").write_text(json.dumps({
            "kind": "cancel-requested",
            "request_hash": fixture.request["request_hash"],
            "token": TOKEN, "attempt_id": ATTEMPT,
        }), encoding="utf-8")
        spawned = []
        patched(self, _popen=lambda *a, **k: spawned.append(a))
        result = worker.cmd_supervise(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        document = self.read_state(fixture)
        self.assertEqual(spawned, [])
        self.assertTrue(result["cancelled"])
        self.assertEqual(document["state"], "cancelled")
        self.assertTrue(document["stop_verified"])
        self.assertEqual(document["stop_evidence"]["method"], "never-launched")

    def test_start_after_cancel_before_state_never_spawns(self):
        """取消落在 request-write 之后、start 之前：没有 state.json 也不许晚启动。"""
        fixture = self.build(state="starting")
        (fixture.state_dir / "state.json").unlink()
        first = self.cancel(fixture)
        self.assertFalse(first["ok"])
        self.assertFalse((fixture.state_dir / "state.json").exists())
        spawned = []
        manifest = {"t1": "b" * 64}
        patched(self,
                _popen=lambda argv, **kwargs: spawned.append(argv),
                verify_shared_inputs=lambda spec: None,
                code_fingerprint=lambda root: "a" * 64,
                task_manifest=lambda root: dict(manifest),
                resolve_environment=lambda request: fixture.state["environment"])
        result = worker.cmd_start(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        document = self.read_state(fixture)
        self.assertEqual(spawned, [])
        self.assertTrue(result.get("cancelled"))
        self.assertEqual(document["state"], "cancelled")
        self.assertTrue(document["stop_verified"])
        self.assertEqual(document["stop_evidence"]["method"], "never-launched")

    def test_wrong_token_cannot_rewrite_run_record(self):
        fixture = self.build(state="running", pid=4242, birth=111)
        signals = []
        patched(self, _term_group=lambda pid: signals.append("TERM"),
                _kill_group=lambda pid: signals.append("KILL"))
        result = self.cancel(fixture, token="b" * 48)
        document = self.read_state(fixture)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["state"], "unknown")
        self.assertEqual(signals, [])
        self.assertEqual(document["state"], "running")
        self.assertFalse(document["cancel_requested"])
        self.assertFalse((fixture.state_dir / "cancel.json").exists())

    def test_cancel_probe_returns_verified_terminal_without_signals(self):
        fixture = self.build(state="completed", pid=4242, birth=111)
        document = self.read_state(fixture)
        document.update(exitcode=0, finished_at=2.0, stop_verified=True,
                        stop_evidence={"method": "wait-group-empty"})
        self.write_state(fixture, document)
        signals = []
        patched(self, _term_group=lambda pid: signals.append("TERM"),
                _kill_group=lambda pid: signals.append("KILL"))
        result = self.cancel(fixture)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["state"], "completed")
        self.assertTrue(result["state"]["stop_verified"])
        self.assertEqual(signals, [])

    def test_cancel_probe_on_terminal_without_stop_evidence_is_unknown(self):
        fixture = self.build(state="failed", pid=4242, birth=111)
        document = self.read_state(fixture)
        document.update(exitcode=1, finished_at=2.0, stop_verified=False,
                        timed_out=True)
        self.write_state(fixture, document)
        result = self.cancel(fixture)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["state"], "unknown")


class CancelGroupProofTests(WorkerCase):
    """缺陷 2/3：只有整组证明已停才许写 cancelled，否则 unknown 且保留 pid/birth。"""

    def _fake_group(self, *, dies_on, signals):
        group = {"alive": True}

        def term(pid):
            signals.append("TERM")

        def kill(pid):
            signals.append("KILL")
            if dies_on == "KILL":
                group["alive"] = False

        def gone(pid):
            return not group["alive"]

        return term, kill, gone

    def test_cancel_unknown_when_group_survives_term_and_kill(self):
        fixture = self.build(state="running", pid=4242, birth=111)
        signals = []
        term, kill, gone = self._fake_group(dies_on=None, signals=signals)
        patched(self, _term_group=term, _kill_group=kill, group_gone=gone,
                process_birth=lambda pid: 111, GRACE_SECONDS=0.01, GROUP_POLL_SECONDS=0.001)
        result = self.cancel(fixture)
        document = self.read_state(fixture)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["state"], "unknown")
        self.assertNotEqual(document["state"], "cancelled")
        self.assertFalse(document["stop_verified"])
        self.assertEqual(document["pid"], 4242)
        self.assertEqual(document["birth"], 111)
        self.assertEqual(signals, ["TERM", "KILL"])

    def test_cancel_cancelled_only_after_group_is_proven_gone(self):
        fixture = self.build(state="running", pid=4242, birth=111)
        signals = []
        term, kill, gone = self._fake_group(dies_on="KILL", signals=signals)
        patched(self, _term_group=term, _kill_group=kill, group_gone=gone,
                process_birth=lambda pid: 111, GRACE_SECONDS=0.01, GROUP_POLL_SECONDS=0.001)
        result = self.cancel(fixture)
        document = self.read_state(fixture)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["state"], "cancelled")
        self.assertEqual(document["state"], "cancelled")
        self.assertTrue(document["stop_verified"])
        self.assertEqual(signals, ["TERM", "KILL"])

    def test_report_write_failure_does_not_block_verified_stop(self):
        fixture = self.build(state="running", pid=4242, birth=111)
        signals = []
        term, kill, gone = self._fake_group(dies_on="KILL", signals=signals)
        real_write = worker._write_json

        def failing_write(path, value):
            if pathlib.Path(path).name == "state.json" and value.get("state") == "cancelled":
                raise OSError("模拟审计/报告层写入失败")
            return real_write(path, value)

        patched(self, _term_group=term, _kill_group=kill, group_gone=gone,
                process_birth=lambda pid: 111, GRACE_SECONDS=0.01,
                GROUP_POLL_SECONDS=0.001, _write_json=failing_write)
        result = self.cancel(fixture)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["state"], "cancelled")
        self.assertFalse(result["recorded"])
        self.assertIn("warning", result)
        self.assertEqual(signals, ["TERM", "KILL"])

    def test_pid_reuse_never_signals_and_requires_empty_group(self):
        fixture = self.build(state="running", pid=os.getpid(), birth=1)
        signals = []
        patched(self, _term_group=lambda pid: signals.append("TERM"),
                _kill_group=lambda pid: signals.append("KILL"),
                group_members=lambda pid: set())
        result = self.cancel(fixture)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["state"], "cancelled")
        self.assertEqual(result["state"]["reason"], "pid-reused-original-gone")
        self.assertEqual(signals, [])
        self.assertTrue(worker.process_alive(os.getpid()))

    def test_pid_reuse_with_live_group_is_unknown_not_cancelled(self):
        fixture = self.build(state="running", pid=os.getpid(), birth=1)
        signals = []
        patched(self, _term_group=lambda pid: signals.append("TERM"),
                _kill_group=lambda pid: signals.append("KILL"),
                group_members=lambda pid: {999999})
        result = self.cancel(fixture)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["state"], "unknown")
        self.assertEqual(signals, [])

    def test_missing_birth_time_refuses_to_signal(self):
        fixture = self.build(state="running", pid=4242, birth=None)
        signals = []
        patched(self, _term_group=lambda pid: signals.append("TERM"),
                _kill_group=lambda pid: signals.append("KILL"),
                process_birth=lambda pid: 111)
        result = self.cancel(fixture)
        document = self.read_state(fixture)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["state"], "unknown")
        self.assertEqual(signals, [])
        self.assertEqual(document["pid"], 4242)
        self.assertFalse(document["stop_verified"])


class SupervisorStopProofTests(WorkerCase):
    """缺陷 2/3：超时与主进程退出都必须证明整组已停，否则 unknown。"""

    def _run(self, fixture, process, *, group_gone, signals):
        spawned = []

        def popen(argv, **kwargs):
            spawned.append(argv)
            return process

        patched(self, _popen=popen, group_gone=group_gone,
                process_birth=lambda pid: 111,
                _term_group=lambda pid: signals.append("TERM"),
                _kill_group=lambda pid: signals.append("KILL"),
                GRACE_SECONDS=0.01, GROUP_POLL_SECONDS=0.001)
        result = worker.cmd_supervise(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        self.assertTrue(spawned)
        return result

    def test_timeout_with_group_still_alive_is_unknown_not_failed(self):
        fixture = self.build(state="starting")
        signals = []
        process = FakeProcess(4242, [subprocess.TimeoutExpired("fake", 30),
                                     subprocess.TimeoutExpired("fake", 10)])
        result = self._run(fixture, process, group_gone=lambda pid: False, signals=signals)
        document = self.read_state(fixture)
        self.assertFalse(result["ok"])
        self.assertFalse(result["stop_verified"])
        self.assertEqual(document["state"], "unknown")
        self.assertTrue(document["timed_out"])
        self.assertFalse(document["stop_verified"])
        self.assertEqual(document["pid"], 4242)
        self.assertEqual(signals, ["TERM", "KILL"])

    def test_timeout_with_group_proven_gone_is_failed(self):
        fixture = self.build(state="starting")
        signals = []
        alive = {"value": True}

        def kill(pid):
            signals.append("KILL")
            alive["value"] = False

        process = FakeProcess(4242, [subprocess.TimeoutExpired("fake", 30), -15])
        patched(self, _popen=lambda argv, **k: process,
                group_gone=lambda pid: not alive["value"],
                process_birth=lambda pid: 111,
                _term_group=lambda pid: signals.append("TERM"),
                _kill_group=kill,
                GRACE_SECONDS=0.01, GROUP_POLL_SECONDS=0.001)
        result = worker.cmd_supervise(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        document = self.read_state(fixture)
        self.assertTrue(result["ok"])
        self.assertTrue(result["stop_verified"])
        self.assertEqual(document["state"], "failed")
        self.assertTrue(document["timed_out"])
        self.assertTrue(document["stop_verified"])
        self.assertIsNone(document["exitcode"])

    def test_main_exit_zero_with_live_group_is_unknown_not_completed(self):
        fixture = self.build(state="starting")
        process = FakeProcess(4242, [0])
        result = self._run(fixture, process, group_gone=lambda pid: False, signals=[])
        document = self.read_state(fixture)
        self.assertFalse(result["ok"])
        self.assertEqual(document["state"], "unknown")
        self.assertFalse(document["stop_verified"])
        self.assertEqual(document["reason"], "main-exit-group-not-verified")
        self.assertEqual(document["pid"], 4242)

    def test_main_exit_zero_with_group_proven_gone_is_completed(self):
        fixture = self.build(state="starting")
        process = FakeProcess(4242, [0])
        result = self._run(fixture, process, group_gone=lambda pid: True, signals=[])
        document = self.read_state(fixture)
        self.assertTrue(result["ok"])
        self.assertEqual(document["state"], "completed")
        self.assertTrue(document["stop_verified"])
        self.assertEqual(document["stop_evidence"]["method"], "wait-group-empty")

    def test_main_exit_zero_with_unverifiable_group_is_unknown(self):
        fixture = self.build(state="starting")
        process = FakeProcess(4242, [0])
        self._run(fixture, process, group_gone=lambda pid: None, signals=[])
        document = self.read_state(fixture)
        self.assertEqual(document["state"], "unknown")
        self.assertFalse(document["stop_verified"])


class QueryCollectBindingTests(WorkerCase):
    """缺陷 4：worker 自己也要把 state.json 和 request.json 对齐后才回报。"""

    def test_query_returns_completed_only_with_binding_and_stop_evidence(self):
        fixture = self.build(state="completed", pid=4242, birth=111)
        document = self.read_state(fixture)
        document.update(exitcode=0, finished_at=2.0, stop_verified=True,
                        stop_evidence={"method": "wait-group-empty"})
        self.write_state(fixture, document)
        result = worker.cmd_query(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        self.assertEqual(result["state"]["state"], "completed")
        self.assertTrue(result["state"]["stop_verified"])
        expected = worker.sha_bytes(worker.canonical(document["argv"]).encode("utf-8"))
        self.assertEqual(result["state"]["argv_sha256"], expected)
        self.assertNotIn("argv", result["state"])  # ⛔ 不回显完整 argv（参数里可能有 secret）

    def test_query_rejects_state_without_stop_evidence(self):
        fixture = self.build(state="completed", pid=4242, birth=111)
        document = self.read_state(fixture)
        document.update(exitcode=0, finished_at=2.0, stop_verified=False)
        self.write_state(fixture, document)
        result = worker.cmd_query(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        self.assertEqual(result["state"]["state"], "unknown")

    def test_query_rejects_token_mismatch(self):
        fixture = self.build(state="running", pid=4242, birth=111)
        document = self.read_state(fixture)
        document["token"] = "b" * 48
        self.write_state(fixture, document)
        result = worker.cmd_query(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        self.assertEqual(result["state"]["state"], "unknown")

    def test_query_rejects_attempt_mismatch(self):
        fixture = self.build(state="running", pid=4242, birth=111)
        document = self.read_state(fixture)
        document["attempt_id"] = "attempt-2"
        self.write_state(fixture, document)
        result = worker.cmd_query(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        self.assertEqual(result["state"]["state"], "unknown")

    def test_query_rejects_spec_mismatch(self):
        fixture = self.build(state="running", pid=4242, birth=111)
        document = self.read_state(fixture)
        document["spec"]["task_ids"] = ["t2"]
        self.write_state(fixture, document)
        result = worker.cmd_query(types.SimpleNamespace(state_dir=str(fixture.state_dir)))
        self.assertEqual(result["state"]["state"], "unknown")

    def test_collect_refuses_when_record_binding_broken(self):
        fixture = self.build(state="completed", pid=4242, birth=111)
        document = self.read_state(fixture)
        document.update(exitcode=0, finished_at=2.0, stop_verified=True,
                        stop_evidence={"method": "wait-group-empty"})
        (fixture.unit / "output" / "a1").mkdir(parents=True, exist_ok=True)
        (fixture.unit / "output" / "a1" / "results.json").write_text("{}", encoding="utf-8")
        document["spec"]["required_artifacts"] = ["../escape.txt"]
        self.write_state(fixture, document)
        with self.assertRaises(worker.WorkerError):
            worker.cmd_collect(types.SimpleNamespace(state_dir=str(fixture.state_dir)))

    def test_collect_refuses_without_stop_evidence(self):
        fixture = self.build(state="completed", pid=4242, birth=111)
        document = self.read_state(fixture)
        document.update(exitcode=0, finished_at=2.0, stop_verified=False)
        self.write_state(fixture, document)
        with self.assertRaises(worker.WorkerError):
            worker.cmd_collect(types.SimpleNamespace(state_dir=str(fixture.state_dir)))


class CrossProcessLockTests(unittest.TestCase):
    """跨进程互斥是真子进程，但只用标准库文件锁，Windows / POSIX 都可跑。"""

    def test_state_lock_excludes_another_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = pathlib.Path(temporary) / "state"
            state_dir.mkdir()
            ready = pathlib.Path(temporary) / "ready.txt"
            script = (
                "import sys, time\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                "from kernel.autoresearch.backends import ssh_worker as w\n"
                "with w.state_lock(sys.argv[1]):\n"
                "    open(sys.argv[2], 'w').write('locked')\n"
                "    time.sleep(1.5)\n"
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            child = subprocess.Popen([sys.executable, "-c", script, str(state_dir), str(ready)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=environment, shell=False)
            try:
                deadline = time.time() + 30
                while not ready.is_file() and time.time() < deadline:
                    if child.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(ready.is_file(), "子进程没有拿到锁（锁实现有问题）")
                with self.assertRaises(worker.WorkerError):
                    with worker.state_lock(state_dir, timeout=0.25):
                        self.fail("另一个进程持锁时不该拿到锁")
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=10)
            with worker.state_lock(state_dir, timeout=5):
                pass  # 子进程退出后必须能拿到


class StubTransport:
    """只回放预设 JSON；不发网络、不起进程。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.files = {}

    def run(self, argv, *, stdin_bytes=None, timeout=None):
        self.calls.append(list(argv))
        if not self.replies:
            raise RuntimeError("没有预设回复")
        reply = self.replies.pop(0)
        return {"code": 0, "stdout": json.dumps(reply).encode("utf-8"), "stderr": b""}

    def copy_to(self, local_path, remote_path, *, timeout=None):
        raise AssertionError("本测试不应推送文件")

    def copy_from(self, remote_path, local_path, *, timeout=None):
        target = pathlib.Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[remote_path])


class AdapterBindingTests(unittest.TestCase):
    """缺陷 4/5：adapter 只认和本地 receipt 绑定的回报；安全 cancel 不被报告层挡住。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "known_hosts").write_text("", encoding="utf-8")
        self.deployment = sshmod.SSHDeployment(
            enabled=True, host="fake-box", user="runner",
            known_hosts=str(self.root / "known_hosts"),
            remote_dir="/remote/tools", state_root=str(self.root / "state"))
        self.request_key = "rk-safety"
        self.local_hash = "d" * 64
        self.remote_hash = "e" * 64
        self.remote_id = "ssh-" + self.local_hash[:24]
        self.state_dir = "/remote/unit/.ssh_worker/" + self.local_hash[:32]
        self.output_dir = "/remote/unit/output/a1"
        self.argv_sha256 = "9" * 64

    def make_adapter(self, replies, *, receipt_extra=None):
        transport = StubTransport(replies)
        adapter = sshmod.SSHAdapter(self.deployment, transport=transport)
        adapter._provisioned = True
        receipt = {
            "request_key": self.request_key,
            "request_hash": self.local_hash,
            "remote_request_hash": self.remote_hash,
            "remote_id": self.remote_id,
            "state_dir": self.state_dir,
            "token": TOKEN,
            "attempt_id": ATTEMPT,
            "output_dir": self.output_dir,
            "created_at": 0,
        }
        receipt.update(receipt_extra or {})
        for key in [key for key, value in receipt.items() if value is None]:
            receipt.pop(key)
        adapter._write_receipt(adapter._receipt_path(self.request_key), receipt, exclusive=False)
        return adapter, transport, receipt

    def good_state(self, **overrides):
        document = {"state": "completed", "token": TOKEN, "attempt_id": ATTEMPT,
                    "request_hash": self.remote_hash, "stop_verified": True,
                    "argv_sha256": self.argv_sha256,
                    "pid": 4242, "birth": 111, "exitcode": 0, "signal": None,
                    "timed_out": False, "reason": None, "started_at": 1.0,
                    "finished_at": 2.0}
        document.update(overrides)
        return document

    def test_query_accepts_bound_completed_with_stop_evidence(self):
        adapter, transport, _ = self.make_adapter([{"ok": True, "state": self.good_state()}])
        status = adapter.query(self.request_key, self.remote_id)
        self.assertEqual(status.state, "completed")
        self.assertTrue(status.evidence["stop_verified"])
        self.assertEqual(status.evidence["argv_sha256"], self.argv_sha256)
        self.assertNotIn("argv", status.evidence)
        self.assertEqual(len(transport.calls), 1)

    def test_query_rejects_token_mismatch(self):
        adapter, _, _ = self.make_adapter(
            [{"ok": True, "state": self.good_state(token="b" * 48)}])
        status = adapter.query(self.request_key, self.remote_id)
        self.assertEqual(status.state, "unknown")
        self.assertIn("token", status.evidence["reason"])

    def test_query_rejects_request_hash_mismatch(self):
        adapter, _, _ = self.make_adapter(
            [{"ok": True, "state": self.good_state(request_hash="f" * 64)}])
        status = adapter.query(self.request_key, self.remote_id)
        self.assertEqual(status.state, "unknown")

    def test_query_rejects_attempt_mismatch(self):
        adapter, _, _ = self.make_adapter(
            [{"ok": True, "state": self.good_state(attempt_id="attempt-2")}])
        status = adapter.query(self.request_key, self.remote_id)
        self.assertEqual(status.state, "unknown")

    def test_query_rejects_terminal_without_stop_evidence(self):
        adapter, _, _ = self.make_adapter(
            [{"ok": True, "state": self.good_state(stop_verified=False)}])
        status = adapter.query(self.request_key, self.remote_id)
        self.assertEqual(status.state, "unknown")
        self.assertNotEqual(status.state, "completed")

    def test_query_without_remote_hash_is_unknown(self):
        adapter, _, _ = self.make_adapter(
            [{"ok": True, "state": self.good_state()}],
            receipt_extra={"remote_request_hash": None})
        status = adapter.query(self.request_key, self.remote_id)
        self.assertEqual(status.state, "unknown")
        self.assertIn("request_hash", status.evidence["reason"])

    def test_collect_refuses_wrong_binding(self):
        adapter, _, _ = self.make_adapter([{
            "ok": True, "state": "completed", "request_hash": "f" * 64,
            "token": TOKEN, "attempt_id": ATTEMPT, "stop_verified": True,
            "output_dir": self.output_dir, "artifacts": [], "missing": [],
        }])
        with self.assertRaises(RuntimeError):
            adapter.collect(self.request_key, self.remote_id, self.output_dir)

    def test_collect_refuses_without_stop_evidence(self):
        adapter, _, _ = self.make_adapter([{
            "ok": True, "state": "completed", "request_hash": self.remote_hash,
            "token": TOKEN, "attempt_id": ATTEMPT, "stop_verified": False,
            "output_dir": self.output_dir, "artifacts": [], "missing": [],
        }])
        with self.assertRaises(RuntimeError):
            adapter.collect(self.request_key, self.remote_id, self.output_dir)

    def test_collect_accepts_bound_artifacts(self):
        data = b'{"ok": true}'
        adapter, transport, _ = self.make_adapter([{
            "ok": True, "state": "completed", "request_hash": self.remote_hash,
            "token": TOKEN, "attempt_id": ATTEMPT, "stop_verified": True,
            "output_dir": self.output_dir,
            "artifacts": [{"path": "results.json", "sha256": digest(data),
                           "size": len(data)}],
            "missing": [],
        }])
        transport.files[self.output_dir + "/results.json"] = data
        bundle = adapter.collect(self.request_key, self.remote_id, self.output_dir)
        self.assertEqual(bundle.artifacts["results.json"], data)

    def test_cancel_without_token_never_calls_transport(self):
        adapter, transport, _ = self.make_adapter([], receipt_extra={"token": None})
        status = adapter.cancel(self.request_key, self.remote_id)
        self.assertEqual(status.state, "unknown")
        self.assertEqual(transport.calls, [])

    def test_cancel_still_stops_when_local_receipt_cannot_be_written(self):
        adapter, transport, _ = self.make_adapter(
            [{"ok": True, "state": self.good_state(state="cancelled")}],
            receipt_extra={"remote_request_hash": None})

        def failing_write(path, document, *, exclusive):
            raise OSError("模拟本地 receipt 写失败（报告层）")

        adapter._write_receipt = failing_write
        status = adapter.cancel(self.request_key, self.remote_id)
        self.assertEqual(status.state, "cancelled")
        self.assertTrue(status.evidence["stop_verified"])
        command = transport.calls[0][-1]
        self.assertIn("--token", command)
        self.assertIn(TOKEN, command)

    def test_cancel_rejects_token_mismatch_reply(self):
        adapter, _, _ = self.make_adapter(
            [{"ok": True, "state": self.good_state(state="cancelled", token="b" * 48)}])
        status = adapter.cancel(self.request_key, self.remote_id)
        self.assertEqual(status.state, "unknown")

    def test_worker_and_adapter_protocol_versions_match(self):
        self.assertEqual(sshmod.WORKER_VERSION, worker.WORKER_VERSION)
        self.assertGreaterEqual(worker.WORKER_VERSION, 2)

    def test_request_write_receipt_must_bind_token_attempt_and_protocol(self):
        good = {"request_hash": self.remote_hash, "token": TOKEN,
                "attempt_id": ATTEMPT, "protocol": sshmod.WORKER_VERSION}
        check = sshmod.SSHAdapter._request_write_reason
        self.assertIsNone(check(good, TOKEN, ATTEMPT))
        self.assertIn("token", check({**good, "token": "b" * 48}, TOKEN, ATTEMPT))
        self.assertIn("attempt_id",
                      check({**good, "attempt_id": "attempt-2"}, TOKEN, ATTEMPT))
        self.assertIn("protocol",
                      check({**good, "protocol": 1}, TOKEN, ATTEMPT))
        self.assertIn("request_hash",
                      check({**good, "request_hash": "not-a-hash"}, TOKEN, ATTEMPT))


class EnvironmentFingerprintPreservedTests(unittest.TestCase):
    """父会话刚加的 importlib.metadata 真实包指纹 / 独立环境核验不许回滚。"""

    def test_probe_uses_real_distribution_metadata_and_binds_packages(self):
        probe = worker._probe_python(sys.executable)
        self.assertIn("packages", probe)
        self.assertIsInstance(probe["packages"], list)
        first = worker.environment_fingerprint(sys.executable, [], None)
        again = worker.environment_fingerprint(sys.executable, [], None)
        self.assertEqual(first, again)
        declared = worker.environment_fingerprint(sys.executable, ["synthetic-probe"], None)
        self.assertNotEqual(first, declared)
        probe_source = worker._probe_python.__code__.co_consts
        self.assertTrue(any("importlib" in str(item) or "metadata" in str(item)
                            for item in probe_source))


if __name__ == "__main__":
    unittest.main()
