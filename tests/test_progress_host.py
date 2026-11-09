# GENERATED-BY: 完整功能迁移（持续推进 A1–A7 / 远端进度 H1–H9 / 入向超期 D2 的宿主侧联动）
# LAST-EDIT: 2026-11-09（首版；goal 重放 / 覆盖注入 / 超期只读 / job 精确核验 / 只读不变量 / CLI）
# PURPOSE: progress_host 的离线回归：全部合成夹具（临时 dsh_home、临时 runtime sqlite、临时部署模块），
#          不连真实机器、不发消息、不写宿主目录；证明「读真实形状、判词不假绿、证据可被篡改拒」。
# ORIGIN: kernel/autoresearch/progress_host.py 的契约；宿主源码 @deepseek-ai/dsh-goal 的 fold 规则与 dsh-tool-pwsh 的后台输出。
# CUMULATIVE-DELTA: 首版。fixture decoder 通过部署的 host_module（可信构造器）注入，CLI 不暴露 decoder；
#          超期库由测试自建最小表（不是生产 schema 迁移），并断言读取后宿主文件字节不变。

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from kernel.autoresearch import progress, progress_host
from kernel.autoresearch.host_evidence import DSHHost

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "kernel" / "autoresearch" / "progress_host_cli.py"
SESSION_ID = "session-fixture"
T0 = 1_700_000_000_000


# ---------------------------------------------------------------------------
# 夹具：DSH 会话日志（形状与真实事件一致，内容全部合成）
# ---------------------------------------------------------------------------


def session_rows(events, *, session_id=SESSION_ID, created_at=T0):
    header = {"type": "session", "id": session_id, "createdAt": created_at,
              "origin": "top-level", "delegationDepth": 0, "parentSession": None,
              "isSeeded": False}
    rows = [header]
    for seq, event in enumerate(events, 1):
        row = {"seq": seq, "time": created_at + seq * 1000}
        row.update(event)
        rows.append(row)
    return rows


def goal_change(operation="create", *, phase="active", revision=1, rounds=0, max_rounds=5,
                goal_id="g-1", blocked_reason=None, created_at=T0, updated_at=None):
    goal = {"id": goal_id, "revision": revision, "objective": "合成目标",
            "phase": phase, "maxGoalRounds": max_rounds}
    if phase == "blocked":
        goal["blockedReason"] = blocked_reason or {"code": "external", "message": "外部依赖未就绪"}
    return {"type": "goal/change", "data": {
        "kind": "goal/change", "version": 1, "operation": operation, "goal": goal,
        "roundsStarted": rounds, "createdAt": created_at,
        "updatedAt": updated_at if updated_at is not None else created_at + 1000,
    }}


def goal_clear(revision, *, cleared_at=None):
    return {"type": "goal/change", "data": {
        "kind": "goal/change", "version": 1, "operation": "clear",
        "cleared": {"id": "g-1", "revision": revision},
        "clearedAt": cleared_at if cleared_at is not None else T0 + 4000,
    }}


def tool_call(call_id, command, *, name="pwsh", background=True):
    return {"type": "tool/call", "data": {
        "callId": call_id, "name": name, "step": 1, "turn": 1,
        "arguments": json.dumps({"command": command, "run_in_background": background}),
    }}


def tool_result(call_id, text):
    return {"type": "tool/result", "data": {"step": 1, "turn": 1, "message": {
        "role": "tool", "toolCallId": call_id, "isError": False,
        "content": [{"type": "text", "text": text}],
    }}}


def make_dsh_home(root: Path, rows) -> Path:
    home = Path(root) / "dsh-home"
    directory = home / "sessions" / "project" / SESSION_ID
    directory.mkdir(parents=True)
    (directory / "session.v4.jsonl.zstd").write_bytes(b"synthetic-stub")
    (Path(root) / "events.json").write_text(json.dumps(rows, ensure_ascii=False),
                                            encoding="utf-8")
    return home


HOST_MODULE = '''
import json
from pathlib import Path
from kernel.autoresearch.host_evidence import DSHHost


def build_host(dsh_home, workspace):
    rows = json.loads(Path(__file__).with_name("events.json").read_text(encoding="utf-8"))
    return DSHHost(root=dsh_home, workspace=workspace, decoder=lambda path: rows)
'''

TRUSTED_FULL = '''
def goal_coverage(view):
    return {"coverage": "full", "why": "部署观察到 goal 循环在跑（合成注入）",
            "source": "synthetic-host", "as_of": 1700000000}
'''


def make_deployment(root: Path, *, rows, trusted=TRUSTED_FULL, host_module=HOST_MODULE,
                    spec_path=None, decisions_db=None, name="fixture") -> Path:
    root = Path(root)
    home = make_dsh_home(root, rows)
    if spec_path is None:
        spec_path = make_progress_spec(root)
    if decisions_db is None:
        decisions_db = make_decisions_db(root, [])
    if host_module is not None:
        (root / "host_module.py").write_text(host_module, encoding="utf-8")
    if trusted is not None:
        (root / "trusted.py").write_text(trusted, encoding="utf-8")
    deployment = {
        "version": 1,
        "name": name,
        "dsh_home": str(home),
        "session_id": SESSION_ID,
        "workspace": str(root / "workspace"),
        "progress_spec": str(spec_path),
        "decisions_db": str(decisions_db),
        "state_root": str(root / "host-state"),
        "owner": "owner-host",
        "shell_tool_name": "pwsh",
        "overdue_grace_sec": 304,
    }
    if host_module is not None:
        deployment["host_module"] = str(root / "host_module.py")
    if trusted is not None:
        deployment["trusted_module"] = str(root / "trusted.py")
    path = root / "deployment.json"
    path.write_text(json.dumps(deployment, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def make_progress_spec(root: Path, *, window=600) -> Path:
    workspace = Path(root) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    spec = {
        "version": 1, "name": "host-fixture", "owner": "owner-host",
        "quiet": {"window_sec": window, "max_window_sec": 1200},
        "round": {"kind": "direct_instruction"},
        "state": {"root": ".state/progress", "db": ".state/progress.sqlite3"},
        "collectors": [{"key": "flag", "kind": "file",
                        "path": str(workspace / "flag.txt"), "aspect": "exists"}],
        "events": [{"key": "flag", "op": "appeared"}],
    }
    path = workspace / "progress_spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def make_decisions_db(root: Path, rows, *, now=None) -> Path:
    """测试自建**最小** runtime 表（只为只读等价查询；生产不会建表/迁移）。"""
    path = Path(root) / "runtime.sqlite3"
    now = float(now if now is not None else time.time())
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE inbox_events(source TEXT NOT NULL, event_id TEXT NOT NULL,"
                     " exp_id TEXT, payload_json TEXT NOT NULL, event_time REAL,"
                     " received_at REAL NOT NULL, status TEXT NOT NULL,"
                     " PRIMARY KEY(source, event_id))")
        conn.execute("CREATE TABLE delivery(id INTEGER PRIMARY KEY, exp_id TEXT, kind TEXT,"
                     " status TEXT, payload_json TEXT NOT NULL)")
        conn.execute("CREATE TABLE delivery_receipts(delivery_id INTEGER PRIMARY KEY, exp_id TEXT,"
                     " receipt_json TEXT NOT NULL, acked_at REAL NOT NULL)")
        for index, row in enumerate(rows, 1):
            conn.execute("INSERT INTO inbox_events(source,event_id,exp_id,payload_json,"
                         "event_time,received_at,status) VALUES(?,?,?,?,?,?,?)",
                         (row["source"], row["event_id"], row.get("exp_id", "exp-1"),
                          json.dumps(row["payload"], ensure_ascii=False),
                          row.get("event_time"), now, row.get("status", "received")))
            if row.get("reply"):
                conn.execute("INSERT INTO delivery(id,exp_id,kind,status,payload_json)"
                             " VALUES(?,?,?,?,?)",
                             (index, row.get("exp_id", "exp-1"), "reply", "delivered",
                              json.dumps({"event_reply_to": {"source": row["source"],
                                                             "event_id": row["event_id"]}})))
                conn.execute("INSERT INTO delivery_receipts(delivery_id,exp_id,receipt_json,"
                             "acked_at) VALUES(?,?,?,?)",
                             (index, row.get("exp_id", "exp-1"), json.dumps({"ok": True}), now))
        conn.commit()
    finally:
        conn.close()
    return path


def adapter(deployment_path, **kwargs):
    return progress_host.build_adapter(str(deployment_path), **kwargs)


class CliResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_cli(args, *, timeout=120):
    with tempfile.TemporaryDirectory() as folder:
        out_path = Path(folder) / "stdout.bin"
        err_path = Path(folder) / "stderr.bin"
        with out_path.open("wb") as out, err_path.open("wb") as err:
            completed = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(CLI), *args],
                cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout)
        return CliResult(completed.returncode,
                         out_path.read_text(encoding="utf-8", errors="replace"),
                         err_path.read_text(encoding="utf-8", errors="replace"))


def snapshot(root: Path) -> dict:
    state = {}
    for path in Path(root).rglob("*"):
        if path.is_file():
            stat = path.stat()
            state[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return state


# ---------------------------------------------------------------------------
# deployment 与 goal 重放
# ---------------------------------------------------------------------------


class DeploymentTests(unittest.TestCase):
    def test_unknown_keys_and_missing_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = make_deployment(root, rows=session_rows([goal_change()]))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["caller_json"] = "不允许"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(progress_host.DeploymentError):
                progress_host.load_deployment(path)
            document.pop("caller_json")
            document.pop("dsh_home")
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(progress_host.DeploymentError):
                progress_host.load_deployment(path)

    def test_relative_paths_resolve_against_deployment_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = make_deployment(root, rows=session_rows([goal_change()]))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["state_root"] = "relative-state"
            path.write_text(json.dumps(document), encoding="utf-8")
            deployment = progress_host.load_deployment(path)
            self.assertEqual(Path(deployment["state_root"]),
                             (root / "relative-state").resolve())


class GoalReplayTests(unittest.TestCase):
    def test_active_goal_is_read_from_host_events_and_stays_unknown_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         trusted=None)  # 不给可信覆盖 ⇒ 必须 unknown
            host = adapter(deployment)
            replay = host.goal_replay()
            self.assertTrue(replay["available"], replay)
            self.assertTrue(replay["present"])
            self.assertEqual(replay["phase"], "active")
            self.assertEqual(replay["rounds_started"], 0)
            self.assertEqual(replay["goal"]["objective_sha256"],
                             progress_host._sha_text("合成目标"))
            self.assertNotIn("合成目标", json.dumps(replay, ensure_ascii=False))  # 不复制正文
            state = host.goal_state()
            self.assertEqual(state["state"], "active")
            self.assertEqual(state["coverage"], "unknown")
            self.assertFalse(state["known"])
            self.assertIn("activation", state["why"])

    def test_trusted_coverage_can_assert_full_with_source_and_as_of(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]))
            state = adapter(deployment).goal_state()
            self.assertTrue(state["known"])
            self.assertEqual(state["coverage"], "full")
            self.assertIn("trusted:", state["source"])
            self.assertEqual(state["evidence"]["trusted_as_of"], 1700000000)

    def test_invalid_trusted_module_cannot_fake_full(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(
                root, rows=session_rows([goal_change(phase="active")]),
                trusted='def goal_coverage(view):\n    return {"coverage": "full"}\n')
            state = adapter(deployment).goal_state()
            self.assertFalse(state["known"])
            self.assertEqual(state["coverage"], "unknown")

    def test_phase_mapping(self):
        cases = {
            "paused": ("blocked", "none", True),
            "blocked": ("blocked", "none", True),
            "complete": ("complete", "none", True),
        }
        for phase, (state_name, coverage, known) in cases.items():
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                rows = session_rows([goal_change(phase="active")])
                if phase != "active":
                    rows = session_rows([goal_change(phase="active"),
                                         goal_change("pause", phase="paused", revision=2)])
                    if phase == "blocked":
                        rows = session_rows([goal_change(phase="active"),
                                             goal_change("block", phase="blocked", revision=2)])
                    if phase == "complete":
                        rows = session_rows([goal_change(phase="active"),
                                             goal_change("complete", phase="complete", revision=2)])
                deployment = make_deployment(root, rows=rows, trusted=None)
                host = adapter(deployment)
                state = host.goal_state()
                self.assertEqual((state["state"], state["coverage"], state["known"]),
                                 (state_name, coverage, known), state)
                replay_text = json.dumps(host.goal_replay(), ensure_ascii=False)
                self.assertNotIn("外部依赖未就绪", replay_text)  # 阻断原因只留指纹，不复制正文

    def test_no_goal_history_is_a_known_empty_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([]), trusted=None)
            state = adapter(deployment).goal_state()
            self.assertTrue(state["known"])
            self.assertEqual(state["coverage"], "none")
            self.assertIn("没有 goal", state["why"])

    def test_invalid_goal_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            broken = {"type": "goal/change", "data": {
                "kind": "goal/change", "version": 1, "operation": "create",
                "goal": {"id": "g-1", "revision": 1, "objective": "x",
                         "phase": "running", "maxGoalRounds": 5},
                "roundsStarted": 0, "createdAt": T0, "updatedAt": T0 + 1}}
            deployment = make_deployment(root, rows=session_rows([broken]))
            host = adapter(deployment)
            replay = host.goal_replay()
            self.assertFalse(replay["available"])
            self.assertIn("phase", replay["why"])
            state = host.goal_state()
            self.assertEqual((state["state"], state["known"]), ("unknown", False))

    def test_clear_tombstone_removes_the_goal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([goal_change(phase="active"),
                                 goal_change("complete", phase="complete", revision=2),
                                 goal_clear(3, cleared_at=T0 + 5000)])
            deployment = make_deployment(root, rows=rows, trusted=None)
            replay = adapter(deployment).goal_replay()
            self.assertTrue(replay["available"], replay)
            self.assertFalse(replay["present"])
            self.assertEqual(replay["rounds_started"], 0)

    def test_projection_cache_is_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         trusted=None)
            # 造一份「phase=complete」的派生缓存：只能当诊断，不能改判定
            sid = SESSION_ID
            cache_dir = Path(json.loads(deployment.read_text(encoding="utf-8"))["dsh_home"]) \
                / "storages" / "session_projcache" / "sessions"
            cache_dir.mkdir(parents=True)
            (cache_dir / f"{sid}.json").write_text(json.dumps({
                "version": 7,
                "record": {"identity": {"createdAt": T0, "formatVersion": 4,
                                        "cwd": "fixture", "isSeeded": False,
                                        "inheritedEventCount": 0},
                           "rows": {"goal": {"ver": 6, "seq": 9, "val": {
                               "current": {"goal": {"id": "g-1", "revision": 1,
                                                    "objective": "缓存里的目标",
                                                    "phase": "complete", "maxGoalRounds": 5},
                                           "roundsStarted": 0, "createdAt": T0,
                                           "updatedAt": T0 + 1},
                               "seenGoalIds": ["g-1"], "failure": None}}}},
            }), encoding="utf-8")
            host = adapter(deployment)
            self.assertEqual(host.goal_state()["coverage"], "unknown")  # 判定仍看事件重放
            diagnostic = host.projection_diagnostic()
            self.assertTrue(diagnostic["available"])
            self.assertFalse(diagnostic["authoritative"])
            self.assertFalse(diagnostic["matches_replay"])


# ---------------------------------------------------------------------------
# 接进 Progress：coverage / 超期优先 / 不假绿
# ---------------------------------------------------------------------------


class ProgressIntegrationTests(unittest.TestCase):
    def _instance(self, deployment, *, window=600):
        document = progress_host.load_deployment(deployment)
        spec = progress.load_spec(document["progress_spec"])
        host = progress_host.build_adapter(document)
        return progress.Progress(document["workspace"], spec,
                                 spec_path=document["progress_spec"], owner="owner-host",
                                 goal=host.goal_state, overdue=host.overdue_provider), document, host

    def test_active_goal_without_trusted_coverage_is_undecidable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         trusted=None)
            instance, _, _ = self._instance(deployment)
            instance.probe()
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "undecidable")
            self.assertEqual(decision["intended_exit"], progress.EXIT_CODES["undecidable"])
            self.assertEqual(decision["causes"][0]["kind"], "goal_state_unknown")
            self.assertFalse(decision["supplement"]["allowed"])

    def test_trusted_full_coverage_suppresses_duplicate_advance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]))
            instance, document, _ = self._instance(deployment)
            flag = Path(document["workspace"]) / "flag.txt"
            instance.probe()
            flag.write_text("x", encoding="utf-8")
            instance.probe()
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "sleep")
            self.assertEqual(decision["causes"][0]["kind"], "goal_covers")
            self.assertTrue(decision["goal"]["known"])

    def test_overdue_outranks_goal_suppression(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = time.time()
            db = make_decisions_db(root, [{
                "source": "chat", "event_id": "evt-1",
                "payload": {"body": "私人正文不该被复制", "recipient": "human"},
                "event_time": now - 900,
            }], now=now)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         decisions_db=db)
            instance, _, _ = self._instance(deployment)
            instance.probe()
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "wake")
            self.assertEqual(decision["causes"][0]["kind"], "overdue_user_message")
            payload = json.dumps(decision, ensure_ascii=False)
            self.assertNotIn("私人正文不该被复制", payload)

    def test_overdue_db_missing_is_undecidable_not_green(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         decisions_db=Path(root) / "missing.sqlite3")
            instance, _, _ = self._instance(deployment)
            instance.probe()
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "undecidable")
            self.assertEqual(decision["causes"][0]["kind"], "overdue_unknown")


# ---------------------------------------------------------------------------
# 超期只读查询
# ---------------------------------------------------------------------------


class OverdueTests(unittest.TestCase):
    def test_reads_text_events_and_reply_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = time.time()
            db = make_decisions_db(root, [
                {"source": "chat", "event_id": "old", "payload": {"body": "secret", "recipient": "h"},
                 "event_time": now - 1000},
                {"source": "chat", "event_id": "replied", "payload": {"body": "x", "recipient": "h"},
                 "event_time": now - 5000, "reply": True},
                {"source": "chat", "event_id": "card", "payload": {"card_action": {"option": "a"},
                                                                   "recipient": "h"},
                 "event_time": now - 5000},
                {"source": "chat", "event_id": "fresh", "payload": {"body": "y", "recipient": "h"},
                 "event_time": now - 10},
            ], now=now)
            result = progress_host.read_overdue(db, now=now)
            self.assertTrue(result["checked"], result)
            self.assertEqual(result["text_events"], 3)  # card click 不计
            self.assertEqual(result["replied"], 1)
            self.assertEqual(result["pending"], 1)
            self.assertEqual([item["id"] for item in result["overdue"]], ["old"])
            self.assertEqual(result["phase"], "unanswered_inbound")
            self.assertTrue(result["blocked"])
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("secret", serialized)  # 默认不带正文

    def test_missing_db_and_missing_tables_are_undecidable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = progress_host.read_overdue(Path(root) / "nope.sqlite3")
            self.assertFalse(result["checked"])
            self.assertIn("不存在", result["why"])

            blank = Path(root) / "blank.sqlite3"
            conn = sqlite3.connect(blank)
            conn.execute("CREATE TABLE unrelated(x INTEGER)")
            conn.commit()
            conn.close()
            result = progress_host.read_overdue(blank)
            self.assertFalse(result["checked"])
            self.assertIn("缺表", result["why"])

    def test_unanswerable_or_untimed_events_are_undecidable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = time.time()
            db = make_decisions_db(root, [
                {"source": "chat", "event_id": "no-to", "payload": {"body": "x"},
                 "event_time": now - 1000},
            ], now=now)
            result = progress_host.read_overdue(db, now=now)
            self.assertFalse(result["checked"])
            self.assertIn("recipient", result["why"])

            second = root / "second"
            second.mkdir()
            db2 = make_decisions_db(second, [
                {"source": "chat", "event_id": "no-time",
                 "payload": {"body": "x", "recipient": "h"}, "event_time": None},
            ], now=now)
            result2 = progress_host.read_overdue(db2, now=now)
            self.assertFalse(result2["checked"])
            self.assertIn("时间", result2["why"])

    def test_read_is_read_only_for_the_runtime_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = time.time()
            db = make_decisions_db(root, [
                {"source": "chat", "event_id": "e1", "payload": {"body": "x", "recipient": "h"},
                 "event_time": now - 10},
            ], now=now)
            before = (db.stat().st_size, db.stat().st_mtime_ns, snapshot(db.parent))
            progress_host.read_overdue(db, now=now)
            progress_host.read_overdue(db, now=now)
            after = (db.stat().st_size, db.stat().st_mtime_ns, snapshot(db.parent))
            self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# 受管 job：精确命令 + jobId 核验与 append-only 台账
# ---------------------------------------------------------------------------


class JobVerificationTests(unittest.TestCase):
    COMMAND = "python -X utf8 -B kernel/autoresearch/progress_host_cli.py --deployment d watch"

    def _deployment(self, root, rows):
        return make_deployment(root, rows=rows)

    def test_exact_command_and_job_id_are_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([
                tool_call("call-1", self.COMMAND),
                tool_result("call-1", "started background job pwsh-7"),
            ])
            host = adapter(self._deployment(root, rows))
            result = host.verify_job("pwsh-7", self.COMMAND)
            self.assertTrue(result["verified"], result)
            self.assertEqual(result["call_seq"], 1)
            self.assertEqual(result["result_seq"], 2)
            self.assertEqual(result["command_sha256"], progress_host._sha_text(self.COMMAND))
            self.assertNotIn(self.COMMAND, json.dumps(result, ensure_ascii=False))

    def test_wrong_job_id_wrong_command_and_ambiguity_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([
                tool_call("call-1", self.COMMAND),
                tool_result("call-1", "started background job pwsh-7"),
            ])
            host = adapter(self._deployment(root, rows))
            self.assertFalse(host.verify_job("pwsh-8", self.COMMAND)["verified"])
            self.assertFalse(host.verify_job("pwsh-7", self.COMMAND + " --poll 1")["verified"])

            rows2 = session_rows([
                tool_call("call-1", self.COMMAND),
                tool_result("call-1", "started background job pwsh-7"),
                tool_call("call-2", self.COMMAND),
                tool_result("call-2", "started background job pwsh-9"),
            ])
            host2 = adapter(self._deployment(root / "ambiguous", rows2))
            ambiguous = host2.verify_job("pwsh-7", self.COMMAND)
            self.assertFalse(ambiguous["verified"])
            self.assertIn("无法唯一绑定", ambiguous["why"])

    def test_crlf_only_is_normalized_but_nothing_else(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([
                tool_call("call-1", self.COMMAND.replace("\n", "\r\n")),
                tool_result("call-1", "started background job pwsh-7"),
            ])
            host = adapter(self._deployment(root, rows))
            self.assertTrue(host.verify_job("pwsh-7", self.COMMAND)["verified"])
            self.assertFalse(host.verify_job("pwsh-7", self.COMMAND + "  ")["verified"])

    def test_argv_must_bind_to_the_exact_command(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([
                tool_call("call-1", self.COMMAND),
                tool_result("call-1", "started background job pwsh-7"),
            ])
            host = adapter(self._deployment(root, rows))
            argv = ["python", "-X", "utf8", "-B",
                    "kernel/autoresearch/progress_host_cli.py", "--deployment", "d", "watch"]
            self.assertEqual(progress_host.render_command(argv), self.COMMAND)
            self.assertTrue(host.verify_job("pwsh-7", self.COMMAND, argv=argv)["verified"])
            mismatch = host.verify_job("pwsh-7", self.COMMAND, argv=["python", "other.py"])
            self.assertFalse(mismatch["verified"])
            self.assertIn("argv", mismatch["why"])

    def test_register_records_verified_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([
                tool_call("call-1", self.COMMAND),
                tool_result("call-1", "started background job pwsh-7"),
            ])
            host = adapter(self._deployment(root, rows))
            first = host.register_job("pwsh-7", self.COMMAND)
            self.assertTrue(first["verified"])
            self.assertFalse(first["idempotent"])
            second = host.register_job("pwsh-7", self.COMMAND)
            self.assertTrue(second["idempotent"])
            self.assertEqual(len(host.jobs()), 1)
            self.assertTrue(host.jobs()[0]["prev_sha"].startswith("sha256:"))
            self.assertTrue(host.jobs()[0]["sha256"].startswith("sha256:"))

    def test_register_without_evidence_records_unverified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([goal_change()])
            host = adapter(self._deployment(root, rows))
            result = host.register_job("pwsh-7", self.COMMAND)
            self.assertFalse(result["verified"])
            self.assertFalse(host.jobs()[0]["verified"])
            self.assertIn("tool/call", host.jobs()[0]["why"])

    def test_job_ledger_detects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([
                tool_call("call-1", self.COMMAND),
                tool_result("call-1", "started background job pwsh-7"),
            ])
            host = adapter(self._deployment(root, rows))
            host.register_job("pwsh-7", self.COMMAND)
            path = host.jobs_path()
            path.write_text(path.read_text(encoding="utf-8").replace("pwsh-7", "pwsh-8"),
                            encoding="utf-8")
            with self.assertRaises(progress_host.HostProgressError):
                host.jobs()


# ---------------------------------------------------------------------------
# 计划命令、状态、只读不变量
# ---------------------------------------------------------------------------


class PlanAndStatusTests(unittest.TestCase):
    def test_plan_generates_host_watch_and_registration_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]))
            plan = adapter(deployment).plan(poll=20)
            self.assertIn("progress_host_cli.py", plan["watch_command"])
            self.assertIn("watch", plan["watch_argv"])
            self.assertEqual(plan["host_call"]["tool"], "pwsh")
            self.assertTrue(plan["host_call"]["arguments"]["run_in_background"])
            self.assertEqual(plan["host_call"]["arguments"]["command"], plan["watch_command"])
            self.assertIn("register", plan["register_command"])
            self.assertIn("<jobId>", plan["register_command"])
            self.assertTrue(any("不启动" in note or "不启动 server" in note
                                for note in plan["notes"]))
            self.assertEqual(plan["command_sha256"],
                             progress_host._sha_text(plan["watch_command"]))

    def test_status_and_doctor_are_honest_about_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         trusted=None)
            host = adapter(deployment)
            status = host.status()
            self.assertFalse(status["ok"])
            self.assertIn("goal_coverage_unknown", status["blockers"])
            self.assertTrue(status["readonly"])
            doctor = host.doctor()
            self.assertTrue(doctor["ok"], doctor)  # 体检只看「读得到」，不假装覆盖已确知

    def test_host_reads_do_not_touch_host_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([
                goal_change(phase="active"),
                tool_call("call-1", "some command"),
                tool_result("call-1", "started background job pwsh-3"),
            ]))
            document = progress_host.load_deployment(deployment)
            before = snapshot(Path(document["dsh_home"]))
            host = progress_host.build_adapter(document)
            host.goal_replay()
            host.goal_state()
            host.overdue()
            host.projection_diagnostic()
            host.verify_job("pwsh-3", "some command")
            host.doctor()
            after = snapshot(Path(document["dsh_home"]))
            self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# CLI（固定 deployment；不暴露 decoder / caller JSON）
# ---------------------------------------------------------------------------


class HostCliTests(unittest.TestCase):
    def test_goal_overdue_plan_status_exit_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = time.time()
            db = make_decisions_db(root, [{
                "source": "chat", "event_id": "evt-1",
                "payload": {"body": "x", "recipient": "h"}, "event_time": now - 900,
            }], now=now)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         decisions_db=db)
            goal = run_cli(["--deployment", str(deployment), "goal"])
            self.assertEqual(goal.returncode, 0, goal.stderr)
            payload = json.loads(goal.stdout)
            self.assertEqual(payload["state"]["coverage"], "full")
            self.assertNotIn("合成目标", goal.stdout)

            overdue = run_cli(["--deployment", str(deployment), "overdue"])
            self.assertEqual(overdue.returncode, 3, overdue.stderr)
            overdue_payload = json.loads(overdue.stdout)
            self.assertEqual(overdue_payload["count"], 1)
            self.assertNotIn("x\"", overdue.stdout)  # 正文没被带出

            plan = run_cli(["--deployment", str(deployment), "plan"])
            self.assertEqual(plan.returncode, 0, plan.stderr)
            self.assertIn("progress_host_cli.py", json.loads(plan.stdout)["watch_command"])

            status = run_cli(["--deployment", str(deployment), "status"])
            self.assertEqual(status.returncode, 3, status.stderr)  # 有超期 ⇒ 需要动作

            doctor = run_cli(["--deployment", str(deployment), "doctor"])
            self.assertEqual(doctor.returncode, 0, doctor.stderr)

    def test_unknown_coverage_cli_exit_is_9(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]),
                                         trusted=None)
            goal = run_cli(["--deployment", str(deployment), "goal"])
            self.assertEqual(goal.returncode, 9, goal.stderr)
            self.assertFalse(json.loads(goal.stdout)["state"]["known"])

    def test_register_and_verify_cli_with_pending_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command = "python -X utf8 -B kernel/autoresearch/progress_host_cli.py --deployment d watch"
            rows = session_rows([
                tool_call("call-1", command),
                tool_result("call-1", "started background job pwsh-7"),
            ])
            deployment = make_deployment(root, rows=rows)
            verify = run_cli(["--deployment", str(deployment), "verify",
                              "--job-id", "pwsh-7", "--command", command])
            self.assertEqual(verify.returncode, 0, verify.stderr)
            self.assertTrue(json.loads(verify.stdout)["verified"])

            register = run_cli(["--deployment", str(deployment), "register",
                                "--job-id", "pwsh-7", "--command", command])
            self.assertEqual(register.returncode, 0, register.stderr)
            self.assertTrue(json.loads(register.stdout)["verified"])

            bad = run_cli(["--deployment", str(deployment), "register",
                           "--job-id", "pwsh-8", "--command", command])
            self.assertEqual(bad.returncode, 9, bad.stderr)
            jobs = run_cli(["--deployment", str(deployment), "jobs"])
            self.assertEqual(jobs.returncode, 0, jobs.stderr)
            self.assertEqual(len(json.loads(jobs.stdout)["jobs"]), 2)

    def test_cli_rejects_caller_supplied_evidence_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deployment = make_deployment(root, rows=session_rows([goal_change(phase="active")]))
            for extra in (["--jsonl", "x.jsonl"], ["--goal-json", "g.json"],
                          ["--overdue-json", "o.json"], ["--decoder", "d.py"]):
                with self.subTest(extra=extra):
                    result = run_cli(["--deployment", str(deployment), "goal", *extra])
                    self.assertEqual(result.returncode, 2, result.stderr)

    def test_watch_uses_host_goal_and_exits_with_structured_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([goal_change(phase="active"),
                                 goal_change("complete", phase="complete", revision=2)])
            deployment = make_deployment(root, rows=rows, trusted=None)
            document = progress_host.load_deployment(deployment)
            spec = json.loads(Path(document["progress_spec"]).read_text(encoding="utf-8"))
            spec["quiet"] = {"window_sec": 0, "max_window_sec": 0}
            Path(document["progress_spec"]).write_text(json.dumps(spec), encoding="utf-8")

            result = run_cli(["--deployment", str(deployment), "watch",
                              "--poll", "0.05", "--max-rounds", "1"])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["verdict"], "wake")
            self.assertEqual(payload["causes"][0]["kind"], "quiet_window")
            self.assertEqual(payload["goal"]["state"], "complete")
            self.assertTrue(payload["goal"]["known"])  # 来自宿主事件，不是 caller JSON
            self.assertFalse(payload["host_woken"])
            self.assertTrue(payload["needs_rearm"]["value"])
            self.assertIn("pending_job", payload)
            self.assertTrue(Path(document["state_root"], "pending_job.json").is_file())


# ---------------------------------------------------------------------------
# 真实 decoder 路径（生产形状）：fixture 自己写合法 zstd 多帧会话日志，
# 由 DSHHost 现有的 Node decoder 解；没有 Node 24 就跳过（不伪造通过）。
# ---------------------------------------------------------------------------


def _node_zstd_available():
    import shutil
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "out.txt"
        err = Path(folder) / "err.txt"
        with out.open("wb") as stream, err.open("wb") as errors:
            completed = subprocess.run(
                [node, "-e", "const z=require('node:zlib');console.log(typeof z.zstdCompressSync)"],
                stdin=subprocess.DEVNULL, stdout=stream, stderr=errors, timeout=60)
        text = out.read_text(encoding="utf-8", errors="replace")
        return node if completed.returncode == 0 and "function" in text else None


NODE = _node_zstd_available()


@unittest.skipUnless(NODE, "本机没有能压缩 zstd 的 Node 24；不伪造真实 decoder 通过")
class RealDecoderTests(unittest.TestCase):
    SCRIPT = ("const fs=require('node:fs'),z=require('node:zlib');"
              "const lines=fs.readFileSync(process.argv[2],'utf8').split(/\\r?\\n/).filter(Boolean);"
              "fs.writeFileSync(process.argv[3],"
              "Buffer.concat(lines.map(l=>z.zstdCompressSync(Buffer.from(l+'\\n','utf8')))));")

    def test_production_dshhost_decodes_real_frames_and_replays_goal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = session_rows([goal_change(phase="active")])
            plain = root / "plain.jsonl"
            plain.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                             encoding="utf-8")
            script = root / "compress.cjs"
            script.write_text(self.SCRIPT, encoding="utf-8")
            session_dir = root / "dsh-home" / "sessions" / "project" / SESSION_ID
            session_dir.mkdir(parents=True)
            target = session_dir / "session.v4.jsonl.zstd"
            out = root / "node-out.txt"
            err = root / "node-err.txt"
            with out.open("wb") as stream, err.open("wb") as errors:
                completed = subprocess.run(
                    [NODE, str(script), str(plain), str(target)],
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=errors, timeout=60)
            self.assertEqual(completed.returncode, 0, err.read_text(encoding="utf-8", errors="replace"))

            spec = make_progress_spec(root)
            decisions = make_decisions_db(root, [])
            deployment = {
                "version": 1, "name": "real-decoder", "dsh_home": str(root / "dsh-home"),
                "session_id": SESSION_ID, "workspace": str(root / "workspace"),
                "progress_spec": str(spec), "decisions_db": str(decisions),
                "state_root": str(root / "host-state"), "owner": "owner-host",
                "shell_tool_name": "pwsh", "overdue_grace_sec": 304, "node": NODE,
            }
            path = root / "deployment.json"
            path.write_text(json.dumps(deployment, ensure_ascii=False), encoding="utf-8")

            result = run_cli(["--deployment", str(path), "doctor"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            doctor = json.loads(result.stdout)
            self.assertTrue(doctor["ok"], doctor)
            # 没有任何 host_module / decoder 参数：走的是生产 DSHHost + 既有 Node decoder
            session_check = next(item for item in doctor["checks"] if item["name"] == "session_log")
            self.assertEqual(session_check["detail"]["source"], "dsh-persistent-log")


if __name__ == "__main__":
    unittest.main()
