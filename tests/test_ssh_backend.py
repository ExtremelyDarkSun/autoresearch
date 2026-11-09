# GENERATED-BY: ssh backend migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：离线端到端（假传输 + 真本地 worker 进程）+ 安全/幂等/未知/取消/POSIX 边界
# PURPOSE: 验证 SSH Adapter 的默认关闭、显式 provision、指纹核验、单命令 quote、幂等 receipt、
#          硬超时、token+进程出生时间取消、产物按清单取回、未知不冒终态。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。⛔ 不连真实网络/机器：FakeTransport 把远端路径映射到本地临时目录，
#          但 worker 是真进程（真 subprocess / 真进程组 / 真超时）。真 POSIX 进程组边界用 skipUnless 标明。
"""SSH 后端离线测试。

**这些测试不验证真实 SSH 网络**：`FakeTransport` 把 `/remote/...` 映射到本地临时目录后，
用 `sys.executable` 真跑 `ssh_worker.py`。它验证的是：命令构造（逐 token quote）、
幂等/未知语义、指纹、真实子进程与硬超时、取消的 token+出生时间核验、产物取回与 partial。
真实 ssh/scp 需要部署凭据与机器，本文件不碰；POSIX 进程组行为只在 POSIX 上跑，Windows 明确 skip。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch.backends import ssh as sshmod  # noqa: E402
from kernel.autoresearch.backends import ssh_worker as worker  # noqa: E402
from kernel.autoresearch.runtime.execution import (  # noqa: E402
    DefinitiveFailure, Execution, RunSpec, canonical, digest)

REMOTE = "/remote"

JOB = r"""
import json, os, sys, time
out = sys.argv[1]
payload = sys.argv[2:]
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, 'results.json'), 'w', encoding='utf-8') as f:
    json.dump({'payload': payload, 'cwd': os.getcwd(), 'python': sys.executable,
               'prefix': sys.prefix, 'venv': os.environ.get('VIRTUAL_ENV'),
               'nousersite': os.environ.get('PYTHONNOUSERSITE')}, f)
if payload and payload[0] == 'sleep':
    time.sleep(float(payload[1]))
"""

GROUP_JOB = r"""
import json, os, subprocess, sys, time
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
with open(os.path.join(out, 'grandchild.txt'), 'w') as f:
    f.write(str(child.pid))
time.sleep(120)
"""


class FakeTransport:
    """把远端 `/remote/...` 映射到本地目录；命令仍是**一条**字符串，用 shlex.split 还原。"""

    def __init__(self, base: pathlib.Path, deployment):
        self.base = pathlib.Path(base)
        self.deployment = deployment
        self.calls: list[list[str]] = []
        self.fail_tokens: set[str] = set()

    def local(self, remote) -> str:
        text = str(remote)
        if text.startswith(REMOTE):
            return str(self.base / text.lstrip("/").replace("/", os.sep))
        return text

    def _rewrite(self, value):
        if isinstance(value, str):
            return self.local(value) if value.startswith(REMOTE) else value
        if isinstance(value, list):
            return [self._rewrite(item) for item in value]
        if isinstance(value, dict):
            mapped = {key: self._rewrite(item) for key, item in value.items()}
            if 'execution_config_fingerprint' in mapped and isinstance(mapped.get('execution_config'), dict):
                mapped['execution_config_fingerprint'] = worker.sha_bytes(worker.canonical(mapped['execution_config']).encode('utf-8'))
            if 'request_hash' in mapped and 'spec' in mapped:
                mapped['request_hash'] = worker.request_hash(mapped)
            return mapped
        return value

    def run(self, argv, *, stdin_bytes=None, timeout=None) -> dict:
        self.calls.append(list(argv))
        command = argv[-1]
        tokens = shlex.split(command)
        if any(token in self.fail_tokens for token in tokens):
            raise ConnectionError("模拟连接中断：" + " ".join(tokens[:3]))
        mapped = []
        for index, token in enumerate(tokens):
            if index == 0 and token in ("python3", "python", self.deployment.python):
                mapped.append(sys.executable)
            elif token.startswith(REMOTE):
                mapped.append(self.local(token))
            else:
                mapped.append(token)
        if mapped[0] == "mv":
            os.replace(mapped[1], mapped[2])
            return {"code": 0, "stdout": b"", "stderr": b""}
        data = stdin_bytes
        if data:
            data = json.dumps(self._rewrite(json.loads(data))).encode("utf-8")
        result = subprocess.run(mapped, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout or 120)
        return {"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    def copy_to(self, local_path, remote_path, *, timeout=None) -> None:
        target = pathlib.Path(self.local(remote_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)

    def copy_from(self, remote_path, local_path, *, timeout=None) -> None:
        source = pathlib.Path(self.local(remote_path))
        pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, local_path)


class SSHCase(unittest.TestCase):
    venv_python = None
    environment_fingerprint = None
    _venv_root = None

    @classmethod
    def setUpClass(cls):
        cls._venv_root = pathlib.Path(tempfile.mkdtemp(prefix="autoresearch-ssh-venv-"))
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(cls._venv_root)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        candidates = [cls._venv_root / "bin" / "python3", cls._venv_root / "bin" / "python",
                      cls._venv_root / "Scripts" / "python.exe"]
        cls.venv_python = str(next(item for item in candidates if item.is_file()))
        cls.environment_fingerprint = worker.environment_fingerprint(
            cls.venv_python, [], str(cls._venv_root / "pyvenv.cfg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._venv_root, ignore_errors=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "remote").mkdir(parents=True)
        (self.root / "known_hosts").write_text("", encoding="utf-8")
        self.deployment = sshmod.SSHDeployment(
            enabled=True, host="fake-box", user="runner",
            known_hosts=str(self.root / "known_hosts"),
            remote_dir=REMOTE + "/tools", state_root=str(self.root / "state"))
        self.transport = FakeTransport(self.root / "root", self.deployment)
        (self.root / "root" / "remote").mkdir(parents=True, exist_ok=True)
        self.adapter = sshmod.SSHAdapter(self.deployment, transport=self.transport)
        self.addCleanup(self._kill_leftovers)

    def _kill_leftovers(self):
        receipts = self.root / "state" / "receipts"
        if not receipts.is_dir():
            return
        for path in receipts.glob("*.json"):
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            try:
                self.adapter.cancel(receipt["request_key"], receipt.get("remote_id"))
            except Exception:  # noqa: BLE001
                pass
            state_path = pathlib.Path(self.transport.local(receipt["state_dir"])) / "state.json"
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    pid = state.get("pid")
                    if pid and worker.process_alive(pid):
                        worker._kill_group(pid)
                except Exception:  # noqa: BLE001
                    pass

    # -- 夹具 ---------------------------------------------------------------

    def make_unit(self, name: str = "unit", job: str = JOB):
        remote_unit = f"{REMOTE}/{name}"
        local_unit = pathlib.Path(self.transport.local(remote_unit))
        for sub in ("code", "env", "input/tasks", "output"):
            (local_unit / sub).mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(local_unit / 'env')],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.venv_python = str(local_unit / 'env' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
        self.environment_fingerprint = worker.environment_fingerprint(
            self.venv_python, [], str(local_unit / 'env/pyvenv.cfg'))
        (local_unit / "code" / "job.py").write_text(job, encoding="utf-8")
        task = local_unit / "input" / "tasks" / "t1.txt"
        task.write_text("task one\n", encoding="utf-8")
        manifest = {"tasks": {"t1": {"path": "tasks/t1.txt",
                                     "sha256": worker.sha_file(task)}}}
        (local_unit / "input" / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        return {
            "remote": remote_unit,
            "local": local_unit,
            "code_fingerprint": worker.code_fingerprint(local_unit / "code"),
            "task_fingerprint": worker.task_fingerprint(local_unit / "input"),
        }

    def make_spec(self, unit, *, request_key="rk-1", argv=None, timeout=60,
                  required=("results.json",), environment=None):
        config = {"argv": argv or [self.venv_python, "job.py", "out"],
                  "timeout_s": timeout,
                  "environment": environment or {"kind": "existing",
                                                 "python": self.venv_python,
                                                 "packages": []}}
        spec = RunSpec(
            exp_id="E1", request_key=request_key,
            code_fingerprint=unit["code_fingerprint"],
            task_fingerprint=unit["task_fingerprint"],
            environment_fingerprint=self.environment_fingerprint,
            unit_root=unit["remote"], code_root=unit["remote"] + "/code",
            environment_root=unit["remote"] + "/env", input_root=unit["remote"] + "/input",
            output_root=unit["remote"] + "/output", task_ids=("t1",),
            reservation_id="RES-1", lease_id="LEASE-1", isolated_environment=True,
            shared_mutable_hardlinks=False, required_artifacts=tuple(required),
            execution_config=config,
            execution_config_fingerprint=digest(canonical(config).encode("utf-8")),
        )
        return spec

    def output_dir(self, unit, name="a1"):
        return unit["remote"] + "/output/" + name

    def output_local(self, unit, name="a1"):
        return pathlib.Path(self.transport.local(self.output_dir(unit, name)))

    def receipt(self, request_key="rk-1") -> dict:
        path = self.adapter._receipt_path(request_key)
        return json.loads(path.read_text(encoding="utf-8"))

    def wait_for(self, request_key, remote_id, states, timeout=30):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.adapter.query(request_key, remote_id)
            if last.state in states:
                return last
            time.sleep(0.1)
        self.fail(f"等不到状态 {states}；最后 {last}")

    def start_job(self, unit, *, request_key="rk-1", timeout=60, required=("results.json",),
                  argv=None, environment=None):
        self.adapter.provision()
        spec = self.make_spec(unit, request_key=request_key, timeout=timeout,
                              required=required, argv=argv, environment=environment)
        status = self.adapter.submit(spec, "attempt-1", self.output_dir(unit))
        return spec, status


class DeploymentSafetyTests(SSHCase):
    def test_disabled_by_default_never_calls_transport(self):
        adapter = sshmod.SSHAdapter(sshmod.SSHDeployment(), transport=self.transport)
        spec = self.make_spec(self.make_unit())
        with self.assertRaises(PermissionError):
            adapter.submit(spec, "a", self.output_dir(self.make_unit()))
        with self.assertRaises(PermissionError):
            adapter.provision()
        self.assertEqual(self.transport.calls, [])

    def test_enabled_requires_known_hosts_and_safe_remote_dir(self):
        for deployment in (
            sshmod.SSHDeployment(enabled=True, host="h", user="u"),
            sshmod.SSHDeployment(enabled=True, host="h", user="u", known_hosts="/x",
                                 remote_dir="/tmp/../etc"),
            sshmod.SSHDeployment(enabled=True, host="h; rm", user="u", known_hosts="/x"),
        ):
            with self.assertRaises(PermissionError):
                sshmod.SSHAdapter(deployment, transport=self.transport).submit(
                    self.make_spec(self.make_unit("u-" + str(id(deployment)))), "a", "/x")

    def test_submit_requires_explicit_provision(self):
        unit = self.make_unit()
        spec = self.make_spec(unit)
        with self.assertRaises(PermissionError):
            self.adapter.submit(spec, "attempt-1", self.output_dir(unit))

    def test_remote_command_quotes_every_token(self):
        tokens = ["/usr/bin/python3", "/remote/tools/ssh_worker.py", "--state-dir",
                  "/remote/unit with space/.ssh_worker/ab", "--token", "a;b$(touch x)"]
        command = sshmod.remote_command(tokens)
        self.assertEqual(shlex.split(command), tokens)
        self.assertNotIn(";b$(touch x)", command.replace("a;b$(touch x)", ""))
        argv = self.deployment.ssh_argv(command)
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[-1], command)


class ProvisionTests(SSHCase):
    def test_provision_pushes_worker_and_records_receipt(self):
        document = self.adapter.provision()
        self.assertEqual(document["worker_version"], sshmod.WORKER_VERSION)
        self.assertTrue((self.root / "state" / "provision.json").is_file())
        copied = [call for call in self.transport.calls]
        self.assertTrue(copied)
        # 第二次不再推送（除非 force）
        before = len(self.transport.calls)
        again = self.adapter.provision()
        self.assertEqual(again["worker_sha256"], document["worker_sha256"])
        self.assertLessEqual(len(self.transport.calls) - before, 1)  # 至多一次版本核对


@unittest.skipUnless(os.name == 'posix', '真实POSIX工作进程须在POSIX测试机验证；Windows只核适配层命令与禁用边界')
class SubmitTests(SSHCase):
    def test_submit_runs_worker_verifies_fingerprints_and_collects(self):
        unit = self.make_unit()
        payload = ["a;b", "$(touch pwned)", "`touch pwned2`"]
        spec, status = self.start_job(
            unit, argv=[self.venv_python, "job.py", str(self.output_local(unit)), *payload])
        self.assertEqual(status.remote_id, "ssh-" + self.receipt()["request_hash"][:24])
        final = self.wait_for("rk-1", status.remote_id, {"completed", "failed"})
        self.assertEqual(final.state, "completed", final.evidence)
        bundle = self.adapter.collect("rk-1", status.remote_id, self.output_dir(unit))
        document = json.loads(bundle.artifacts["results.json"].decode("utf-8"))
        self.assertEqual(document["payload"], payload)  # 参数原样到达，没被 shell 解释
        self.assertTrue(document["venv"])  # 独立环境真的被注入
        self.assertFalse((unit["local"] / "code" / "pwned").exists())
        self.assertFalse((unit["local"] / "code" / "pwned2").exists())
        self.assertEqual(bundle.manifest["results.json"],
                         digest(bundle.artifacts["results.json"]))
        # 收据里保留了可核的 remote_id / token
        receipt = self.receipt()
        self.assertEqual(receipt["remote_id"], status.remote_id)
        self.assertTrue(receipt["token"])

    def test_code_fingerprint_mismatch_is_definitive_and_starts_nothing(self):
        unit = self.make_unit()
        self.adapter.provision()
        spec = self.make_spec(unit)
        (unit["local"] / "code" / "job.py").write_text(JOB + "\n# changed\n", encoding="utf-8")
        with self.assertRaises(DefinitiveFailure):
            self.adapter.submit(spec, "attempt-1", self.output_dir(unit))
        self.assertFalse((self.output_local(unit) / "results.json").exists())

    def test_environment_fingerprint_mismatch_is_definitive(self):
        unit = self.make_unit()
        self.adapter.provision()
        spec = self.make_spec(unit)
        broken = RunSpec(**{**spec.__dict__, "environment_fingerprint": "0" * 64})
        with self.assertRaises(DefinitiveFailure):
            self.adapter.submit(broken, "attempt-1", self.output_dir(unit))

    def test_submit_is_idempotent_and_never_resubmits(self):
        unit = self.make_unit()
        self.adapter.provision()
        spec = self.make_spec(unit, argv=[self.venv_python, "job.py",
                                          str(self.output_local(unit))])
        first = self.adapter.submit(spec, "attempt-1", self.output_dir(unit))
        self.wait_for("rk-1", first.remote_id, {"completed", "failed"})
        starts = sum(1 for call in self.transport.calls if "start" in shlex.split(call[-1]))
        second = self.adapter.submit(spec, "attempt-1", self.output_dir(unit))
        self.assertEqual(second.remote_id, first.remote_id)
        self.assertEqual(sum(1 for call in self.transport.calls
                             if "start" in shlex.split(call[-1])), starts)
        other = self.make_spec(unit, argv=["/bin/true"])
        with self.assertRaises(DefinitiveFailure):
            self.adapter.submit(other, "attempt-1", self.output_dir(unit))

    def test_transport_failure_is_unknown_and_not_retried(self):
        unit = self.make_unit()
        self.adapter.provision()
        spec = self.make_spec(unit)
        self.transport.fail_tokens = {"start"}
        status = self.adapter.submit(spec, "attempt-1", self.output_dir(unit))
        self.assertEqual(status.state, "unknown")
        self.transport.fail_tokens = set()
        again = self.adapter.submit(spec, "attempt-1", self.output_dir(unit))
        self.assertEqual(again.state, "unknown")  # 本地 receipt 已存在，只 query
        self.assertEqual(sum(1 for call in self.transport.calls
                             if "start" in shlex.split(call[-1])), 1)


@unittest.skipUnless(os.name == 'posix', 'POSIX远端硬超时与进程组取消尚未在本Windows主机验证')
class TimeoutAndCancelTests(SSHCase):
    def test_hard_timeout_runs_remotely_and_preserves_reason(self):
        unit = self.make_unit()
        spec, status = self.start_job(
            unit, timeout=1, argv=[self.venv_python, "job.py",
                                   str(self.output_local(unit)), "sleep", "60"])
        final = self.wait_for("rk-1", status.remote_id, {"failed", "completed", "cancelled"}, 30)
        self.assertEqual(final.state, "failed")
        self.assertTrue(final.evidence.get("timed_out"))
        self.assertIsNone(final.evidence.get("exitcode"))
        self.assertIsNotNone(final.evidence.get("finished_at"))

    def test_cancel_verifies_token_and_rechecks_process(self):
        unit = self.make_unit()
        spec, status = self.start_job(
            unit, timeout=300, argv=[self.venv_python, "job.py",
                                     str(self.output_local(unit)), "sleep", "120"])
        state_path = pathlib.Path(self.transport.local(self.receipt()["state_dir"])) / "state.json"
        for _ in range(100):
            if state_path.is_file() and json.loads(state_path.read_text())["state"] == "running":
                break
            time.sleep(0.1)
        pid = json.loads(state_path.read_text())["pid"]
        # 错 token：只能 unknown，绝不能杀进程
        receipt = self.receipt()
        receipt["token"] = "b" * 48
        self.adapter._write_receipt(self.adapter._receipt_path("rk-1"), receipt, exclusive=False)
        bad = self.adapter.cancel("rk-1", status.remote_id)
        self.assertEqual(bad.state, "unknown")
        self.assertTrue(worker.process_alive(pid))
        # 正 token：TERM→KILL 后重新核进程不在，才 cancelled
        receipt["token"] = json.loads(state_path.read_text())["token"]
        self.adapter._write_receipt(self.adapter._receipt_path("rk-1"), receipt, exclusive=False)
        done = self.adapter.cancel("rk-1", status.remote_id)
        self.assertEqual(done.state, "cancelled")
        self.assertFalse(worker.process_alive(pid))

    def test_pid_reuse_check_never_signals_unrelated_process(self):
        state_dir = self.root / "reuse"
        state_dir.mkdir()
        token = "c" * 48
        (state_dir / "state.json").write_text(json.dumps({
            "state": "running", "token": token, "pid": os.getpid(), "birth": 1,
            "output_dir": str(self.root / "out")}), encoding="utf-8")
        result = worker.cmd_cancel(argparse.Namespace(state_dir=str(state_dir), token=token))
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["state"], "cancelled")
        self.assertEqual(result["state"]["reason"], "pid-reused-original-gone")
        self.assertTrue(worker.process_alive(os.getpid()))  # 测试进程没被误杀

    @unittest.skipUnless(os.name == "posix", "真实进程组 TERM→KILL 只在 POSIX 上验证")
    def test_cancel_kills_the_whole_process_group_on_posix(self):
        unit = self.make_unit(job=GROUP_JOB)
        spec, status = self.start_job(
            unit, timeout=300, argv=[self.venv_python, "job.py",
                                     str(self.output_local(unit))])
        state_path = pathlib.Path(self.transport.local(self.receipt()["state_dir"])) / "state.json"
        grandchild = None
        for _ in range(100):
            target = self.output_local(unit) / "grandchild.txt"
            if target.is_file():
                grandchild = int(target.read_text())
                break
            time.sleep(0.1)
        self.assertIsNotNone(grandchild)
        self.assertTrue(worker.process_alive(grandchild))
        done = self.adapter.cancel("rk-1", status.remote_id)
        self.assertEqual(done.state, "cancelled")
        self.assertFalse(worker.process_alive(grandchild))


@unittest.skipUnless(os.name == 'posix', '真实POSIX工作进程尚未在本Windows主机验证')
class CollectTests(SSHCase):
    def test_missing_artifact_is_true_partial_not_fake_complete(self):
        unit = self.make_unit()
        spec, status = self.start_job(
            unit, required=("results.json", "logs.txt"),
            argv=[self.venv_python, "job.py", str(self.output_local(unit))])
        final = self.wait_for("rk-1", status.remote_id, {"completed", "failed"})
        self.assertEqual(final.state, "completed")
        bundle = self.adapter.collect("rk-1", status.remote_id, self.output_dir(unit))
        self.assertIn("results.json", bundle.artifacts)
        self.assertNotIn("logs.txt", bundle.artifacts)
        errors = Execution.validate_results(spec, bundle)
        self.assertIn("missing:logs.txt", errors)

    def test_collect_rejects_artifact_path_escape(self):
        unit = self.make_unit()
        spec, status = self.start_job(
            unit, required=("results.json",),
            argv=[self.venv_python, "job.py", str(self.output_local(unit))])
        self.wait_for("rk-1", status.remote_id, {"completed", "failed"})
        receipt = self.receipt()
        state_path = pathlib.Path(self.transport.local(receipt["state_dir"])) / "state.json"
        state = json.loads(state_path.read_text())
        created = pathlib.Path(self.transport.local(state["output_dir"]))
        (created.parent / "escape.txt").write_text("x", encoding="utf-8")
        (created / "evil").mkdir(exist_ok=True)
        # 直接改 state 里的 required_artifacts 模拟被篡改的清单：worker 必须拒绝对路径
        state["spec"]["required_artifacts"] = ["../escape.txt"]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.adapter.collect("rk-1", status.remote_id, self.output_dir(unit))


if __name__ == "__main__":
    unittest.main()
