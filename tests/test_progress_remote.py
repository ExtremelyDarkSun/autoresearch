# GENERATED-BY: progress remote preflight bridge, 2026-11-10
# LAST-EDIT: 2026-11-10 首版：`progress_remote.remote_preflight` 的只读专项（复用 remote 真实夹具）
# PURPOSE: 证明三件事 —— ① 全过时给出可被**真 CLI 解析**的 `remote start` argv；② 缺部署 / disabled /
#          未 provision / 租约被占或在 quarantine / 计划资源不符 / 无预算 一律不进 runnable；
#          ③ 只读：缺库不建库、不清 WAL、工作区目录集合与文件 identity 不变、零远端调用。
# ORIGIN: 复用 tests/test_remote_workflow.py 的真实夹具（真 Store / 真预算 / 真所有权 / 真 plan / 假传输）。
# CUMULATIVE-DELTA: 首版。⛔ 不 monkeypatch progress.py；本文件只碰 progress_remote 与夹具。
"""`progress_remote` 的离线专项测试。

证据等级：**离线合成**。用真 Store / 真 Admission / 真所有权 marker / 真计划与 prepare 记录 +
进程内假传输；⛔ 不连真实 SSH、不起远端 worker、不发远端请求（预检本来也不发）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import test_remote_workflow as fixtures  # noqa: E402  （复用真实夹具；⛔ 不改它）
from kernel.autoresearch import progress_remote as pr  # noqa: E402
from kernel.autoresearch import remote_cli  # noqa: E402
from kernel.autoresearch.admission import Admission  # noqa: E402

TOPIC = fixtures.TOPIC
SESSION = fixtures.SESSION
RESOURCE = fixtures.RESOURCE


def _snapshot(root):
    """工作区快照：文件集合 + 每个文件的 (size, mtime_ns, sha256)；另记 WAL sidecar。"""
    files = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        files[path.relative_to(root).as_posix()] = {
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    sidecars = sorted(p.name for p in Path(root).rglob("*")
                      if p.is_file() and (p.name.endswith("-wal") or p.name.endswith("-shm")))
    return {"files": files, "sidecars": sidecars}


class ProgressRemoteCase(fixtures.RemoteCase):
    """在 remote 夹具上加：默认的测试接线 + 预检调用 + 只读快照。"""

    def wired(self, **overrides):
        """测试注入的三件对象（生产路径没有这个开关，必须显式 allow_fixture_gates）。"""
        kwargs = {
            "monitor_check": self.monitor_ok,
            "authorization_check": Admission(self.ws, store=self.store).check,
            "verifier": self.verifier,
            "allow_fixture_gates": True,
        }
        kwargs.update(overrides)
        return kwargs

    def preflight(self, plan_rel=None, **overrides):
        kwargs = self.wired()
        kwargs.update(overrides)
        return pr.remote_preflight(self.ws, topic=TOPIC, plan=plan_rel or "plans/P1.json",
                                   owner_session=SESSION, argv=self.argv,
                                   resources=[RESOURCE], intent_none=True, **kwargs)

    def starts(self):
        return sum(1 for call in self.transport.calls if "start" in shlex_split(call[-1]))


def shlex_split(command):
    import shlex
    return shlex.split(command)


# ---------------------------------------------------------------------------
# 正例：全过 → 可执行、可被真 CLI 解析的 argv
# ---------------------------------------------------------------------------


class RunnableTests(ProgressRemoteCase):
    def test_all_green_returns_parseable_launch_command(self):
        plan_rel = self.ready()
        self.provision()
        before = _snapshot(self.ws)
        result = self.preflight(plan_rel)
        self.assertTrue(result["ok"], result["checks"])
        self.assertEqual(result["status"], "runnable")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["writes"], [])
        self.assertFalse(result["network"])
        self.assertEqual(result["flow_type"], "remote")
        self.assertIsNone(result["check"].get("action"))  # 原始 check 原样保留
        self.assertIsNotNone(result["check"])
        # 逐项 checks 都是 pass
        states = {name: item["state"] for name, item in result["checks"].items()}
        self.assertEqual(set(states.values()), {"pass"}, states)
        for name in ("deployment", "worker", "ownership", "lease", "plan_resources",
                     "preparation", "plan", "budget", "sources", "audit", "monitor",
                     "authorization", "execution_target", "argv", "fixtures"):
            self.assertIn(name, result["checks"], name)
        command = result["launch_command"]
        self.assertIsInstance(command, list)
        self.assertEqual(command[:5], [sys.executable, "-X", "utf8", "-B", pr.REMOTE_CLI_PATH])
        # 用**真** CLI 解析器解析：`--` 之后的 argv 原样等于实际 argv
        head, tail = remote_cli._split_argv(command[5:])
        parsed = remote_cli._parser().parse_args(head)
        self.assertEqual(parsed.action, "start")
        self.assertEqual(Path(parsed.workspace), self.ws)
        self.assertEqual(parsed.plan, plan_rel)
        self.assertEqual(parsed.owner_session, SESSION)
        self.assertTrue(parsed.intent_none)
        self.assertEqual(parsed.resource, [RESOURCE])
        self.assertEqual(tail, self.argv)
        # 只读：没有发远端请求，工作区文件 identity 不变
        self.assertEqual(self.starts(), 0)
        self.assertEqual(_snapshot(self.ws), before)
        # plan / preparation 元数据保留给 Progress 映射
        self.assertIsNotNone(result["preparation"])
        self.assertIsNotNone(result["frozen_plan"])
        self.assertEqual(result["preparation"]["plan_id"], "P1")
        self.assertEqual(result["frozen_plan"]["plan_id"], "P1")
        self.assertEqual(result["plan_meta"]["authorization"]["scope"], "small")
        self.assertEqual(result["plan_meta"]["plan_resources"], [RESOURCE])
        self.assertEqual(result["budget"]["status"], "reserved")
        self.assertEqual(result["lease"]["status"], "free")

    def test_launch_command_helper_matches_preflight(self):
        plan_rel = self.ready()
        self.provision()
        result = self.preflight(plan_rel)
        helper = pr.launch_command(self.ws, topic=TOPIC, plan=plan_rel, owner_session=SESSION,
                                   argv=self.argv, resources=[RESOURCE], intent_none=True)
        self.assertEqual(helper, result["launch_command"])

    def test_intents_dict_matches_explicit_argument(self):
        plan_rel = self.ready()
        self.provision()
        explicit = self.preflight(plan_rel)
        via_dict = pr.remote_preflight(
            self.ws, topic=TOPIC, plan=plan_rel, owner_session=SESSION, argv=self.argv,
            resources=[RESOURCE], intents={"none": True}, **self.wired())
        self.assertEqual(via_dict["launch_command"], explicit["launch_command"])

    def test_plan_can_be_a_dict(self):
        plan_rel = self.ready()
        self.provision()
        payload = json.loads((self.ws / plan_rel).read_text(encoding="utf-8"))
        from_dict = pr.remote_preflight(
            self.ws, topic=TOPIC, plan=payload, owner_session=SESSION, argv=self.argv,
            resources=[RESOURCE], intent_none=True, **self.wired())
        self.assertTrue(from_dict["ok"], from_dict["checks"])


# ---------------------------------------------------------------------------
# 负例：部署 / worker / 租约 / 计划资源 / 预算
# ---------------------------------------------------------------------------


class BlockedTests(ProgressRemoteCase):
    def _assert_not_runnable(self, result, status, *, name=None, state=None):
        self.assertFalse(result["ok"], result["checks"])
        self.assertEqual(result["status"], status, result["checks"])
        self.assertIsNone(result["launch_command"])
        if name is not None:
            self.assertIn(name, result["blocked"] + result["unknown"] + result["missing"])
            if state is not None:
                self.assertEqual(result["checks"][name]["state"], state,
                                 result["checks"][name])

    def test_missing_deployment_is_blocked(self):
        plan_rel = self.ready()
        self.provision()
        self.deployment_path.unlink()
        before = _snapshot(self.ws)
        result = self.preflight(plan_rel)
        self._assert_not_runnable(result, "blocked", name="deployment", state="blocked")
        self.assertIn("deployment_unavailable", [item["code"] for item in result["blockers"]])
        self.assertEqual(self.starts(), 0)
        self.assertEqual(_snapshot(self.ws), before)

    def test_disabled_deployment_is_blocked(self):
        plan_rel = self.ready()
        self.provision()
        self.write_deployment(dict(self.deployment_config, enabled=False))
        result = self.preflight(plan_rel)
        self._assert_not_runnable(result, "blocked", name="deployment", state="blocked")
        self.assertIsNone(result["deployment"])

    def test_worker_not_provisioned_is_blocked(self):
        plan_rel = self.ready()
        result = self.preflight(plan_rel)   # ⛔ 故意不 provision
        self._assert_not_runnable(result, "blocked", name="worker", state="blocked")
        self.assertIn("worker_not_ready", [item["code"] for item in result["blockers"]])

    def test_quarantined_lease_is_unknown(self):
        plan_rel = self.ready()
        self.provision()
        # 建表用真组件（夹具里的 Runtime 是惰性构造的，这张表要 Scheduler 在才算）
        from kernel.autoresearch.runtime.scheduler import Scheduler
        Scheduler(self.store)
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute("INSERT OR REPLACE INTO scheduler_resource_quarantine"
                         "(resource_key,reason,quarantined_at) VALUES(?,?,?)",
                         (RESOURCE, "上一个付费任务结束未经核空闲", 1.0))
            conn.commit()
        finally:
            conn.close()
        result = self.preflight(plan_rel)
        self._assert_not_runnable(result, "unknown", name="lease", state="unknown")
        self.assertEqual(result["lease"]["status"], "quarantined")
        self.assertIn("lease_quarantined", [item["code"] for item in result["blockers"]])

    def test_plan_resources_mismatch_is_blocked(self):
        plan_rel = self.ready(resources=["machine:other/gpu1"])
        self.provision()
        result = self.preflight(plan_rel)   # 实际给 RESOURCE，计划声明别的
        self._assert_not_runnable(result, "blocked", name="plan_resources", state="blocked")
        self.assertEqual(result["plan_meta"]["plan_resources"], ["machine:other/gpu1"])

    def test_no_budget_is_blocked(self):
        plan_rel = self.ready()
        self.provision()
        self.accounts.release(self.reservation["id"], "release-before-preflight")
        result = self.preflight(plan_rel)
        self._assert_not_runnable(result, "blocked")
        self.assertIn("budget", result["blocked"])

    def test_missing_prepare_is_blocked(self):
        plan_rel = self.write_plan(self.plan())
        self.provision()
        result = self.preflight(plan_rel)
        self._assert_not_runnable(result, "blocked", name="preparation", state="missing")

    def test_fixture_injection_without_explicit_allow_is_blocked(self):
        plan_rel = self.ready()
        self.provision()
        result = pr.remote_preflight(
            self.ws, topic=TOPIC, plan=plan_rel, owner_session=SESSION, argv=self.argv,
            resources=[RESOURCE], intent_none=True,
            monitor_check=self.monitor_ok,
            authorization_check=Admission(self.ws, store=self.store).check,
            verifier=self.verifier)          # ⚠️ 没有 allow_fixture_gates=True
        self._assert_not_runnable(result, "blocked", name="fixtures", state="blocked")
        self.assertIn("fixture_gates_not_allowed",
                      [item["code"] for item in result["blockers"]])

    def test_services_host_is_not_adopted(self):
        plan_rel = self.ready()
        self.provision()
        result = pr.remote_preflight(
            self.ws, topic=TOPIC, plan=plan_rel, owner_session=SESSION, argv=self.argv,
            resources=[RESOURCE], intent_none=True,
            services={"monitor_check": self.monitor_ok,
                      "authorization_check": Admission(self.ws, store=self.store).check,
                      "verifier": self.verifier,
                      "host": object()},
            allow_fixture_gates=True)
        self.assertTrue(result["ok"], result["checks"])
        self.assertTrue(any("host" in note for note in result["notes"]), result["notes"])

    def test_intent_conflict_is_blocked(self):
        plan_rel = self.ready()
        self.provision()
        result = pr.remote_preflight(
            self.ws, topic=TOPIC, plan=plan_rel, owner_session=SESSION, argv=self.argv,
            resources=[RESOURCE], intent_id="I1", intent_none=True, **self.wired())
        self._assert_not_runnable(result, "blocked")
        self.assertIn("invalid_arguments", [item["code"] for item in result["blockers"]])


# ---------------------------------------------------------------------------
# 只读：缺库不建库、无 WAL sidecar、目录集合与文件 identity 不变
# ---------------------------------------------------------------------------


class ReadOnlyTests(ProgressRemoteCase):
    def test_missing_db_is_refused_without_creating_anything(self):
        workspace = self.root / "fresh-ws"
        (workspace / ".autoresearch" / "remote").mkdir(parents=True)
        (workspace / ".autoresearch" / "remote" / "deployment.json").write_text(
            json.dumps(self.deployment_config), encoding="utf-8")
        before = _snapshot(workspace)
        result = pr.remote_preflight(
            workspace, topic=TOPIC, plan="plans/P1.json", owner_session=SESSION,
            argv=self.argv, resources=[RESOURCE], intent_none=True, **self.wired())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("workspace_db_missing",
                      [item["code"] for item in result["blockers"]])
        self.assertFalse((workspace / ".autoresearch" / "runtime.sqlite3").exists())
        self.assertEqual(_snapshot(workspace), before)

    def test_readonly_preflight_does_not_touch_workspace_or_wal(self):
        plan_rel = self.ready()
        self.provision()
        before = _snapshot(self.ws)
        result = self.preflight(plan_rel)
        self.assertTrue(result["ok"], result["checks"])
        after = _snapshot(self.ws)
        self.assertEqual(after["sidecars"], [], after["sidecars"])
        self.assertEqual(after, before)
        self.assertEqual(result["writes"], [])

    def test_import_has_no_file_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-X", "utf8", "-B", "-c",
                       "import sys;sys.path.insert(0, %r);import kernel.autoresearch.progress_remote as m;"
                       "print(bool(m.remote_preflight), bool(m.launch_command))" % str(ROOT)]
            outcome = subprocess.run(command, cwd=directory, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, timeout=120)
            self.assertEqual(outcome.returncode, 0, outcome.stderr.decode("utf-8", "replace"))
            self.assertEqual(outcome.stdout.decode("utf-8").strip(), "True True")
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
