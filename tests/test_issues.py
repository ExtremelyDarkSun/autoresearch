# GENERATED-BY: issues ledger migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：§9 台账的查重 / 负责人 / 修复 / 复验 / 归档 / 只读 / 并发 / 篡改检测
# PURPOSE: 证明问题台账四件事 —— ① 同一命令只算一个 issue（含真并发）；② 负责人是显式字段；
#          ③ 复验绑可核 argv + 退出码 + 证据；④ 归档不重复、不覆盖、失败可诊断、历史不丢。
# ORIGIN: 合成临时工作区夹具；⛔ 不含真实事故编号、机器、读数或个人通道。
# CUMULATIVE-DELTA: 首版。并发用两个真实操作系统进程；其余用真实文件与真实 sqlite。

"""问题台账（§9）的集成测试。

所有材料都是**临时目录里的合成夹具**：一个工作区、一个证据文件、几条假命令。
判据（不是「跑通就行」）：

1. 同一 `command + scope` 在并发下也只有一个 issue id（一个 `opened` + 一个 `recurred`）；
2. 命令里带临时路径时**不**自动模糊合并 —— 只有显式 `dedupe_key` 才归并；
3. `assigned` 必须给显式 `owner` 字段，`text` 里写名字不算；
4. `verified` 必须给可核 argv + 真实退出码 + 证据；`passed` 与退出码不一致要拒；
5. `fixed` 之后同一命令复发 ⇒ 重开（`recurred`），并且旧证据一条不丢；
6. 归档只认一次结案（重复归档拒）、同名不同内容换名不覆盖、目录写不进去要留诊断事件；
7. `status` / `index` / `events` 只读零写；写历史被触发器挡住；链被篡改要判「台账损坏」。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch.issues import IssueRuleError, Issues  # noqa: E402
from kernel.autoresearch.issues_cli import main as issues_main  # noqa: E402


class IssueCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "ws"
        (self.ws / ".autoresearch").mkdir(parents=True)
        (self.ws / "results").mkdir()
        self.log = self.ws / "results" / "run.log"
        self.log.write_text("启动评测\nERROR: boom at step 3\nexit code 1\n", encoding="utf-8")
        self.fix_log = self.ws / "results" / "fix.log"
        self.fix_log.write_text("patched parse_args\n", encoding="utf-8")
        self.issues = Issues(self.ws)

    def tearDown(self):
        self._tmp.cleanup()

    # -- 夹具 ---------------------------------------------------------------

    def evidence(self, name="run.log", locator="L2"):
        return [{"path": f"results/{name}", "locator": locator}]

    def record(self, *, command=None, scope="tests", summary="跑测试崩了", owner="session-a",
               exitcode=1, sources=None, dedupe_key=None, actor="agent"):
        return self.issues.record(
            summary=summary, owner=owner,
            command=list(command if command is not None else ["python", "-m", "pytest", "tests"]),
            exitcode=exitcode, sources=sources if sources is not None else self.evidence(),
            scope=scope, actor=actor, dedupe_key=dedupe_key)

    def fixed_and_verified(self, issue_id, *, exitcode=0):
        self.issues.update(issue_id, kind="fixed", actor="agent", text="补了空指针判断",
                           sources=self.evidence(name="fix.log", locator="L1"))
        return self.issues.update(issue_id, kind="verified", actor="agent",
                                  text="复跑同一条命令", sources=self.evidence(),
                                  command=["python", "-m", "pytest", "tests"], exitcode=exitcode)

    def closed_issue(self):
        opened = self.record()
        self.fixed_and_verified(opened["issue_id"])
        return opened["issue_id"]

    def db_bytes(self):
        return (self.ws / ".autoresearch" / "issues.sqlite3").read_bytes()

    def kinds(self, issue_id):
        return [row["kind"] for row in self.issues.events() if row["issue_id"] == issue_id]


class DedupeTests(IssueCase):
    def test_same_command_and_scope_is_one_issue(self):
        first = self.record()
        second = self.record()
        self.assertEqual(first["issue_id"], second["issue_id"])
        self.assertEqual(first["kind"], "opened")
        self.assertEqual(second["kind"], "recurred")
        self.assertEqual(self.kinds(first["issue_id"]), ["opened", "recurred"])
        self.assertEqual(len(self.issues.status()), 1)
        self.assertEqual(self.issues.status(first["issue_id"])["state"], "open")

    def test_same_command_different_scope_is_a_different_issue(self):
        first = self.record(scope="tests")
        second = self.record(scope="packaging")
        self.assertNotEqual(first["issue_id"], second["issue_id"])
        self.assertEqual(len(self.issues.status()), 2)

    def test_temp_paths_are_not_fuzzy_merged_but_explicit_key_is(self):
        a = self.record(command=["python", "run.py", "temp/a1/task.json"], scope="single-task")
        b = self.record(command=["python", "run.py", "temp/b2/task.json"], scope="single-task")
        self.assertNotEqual(a["issue_id"], b["issue_id"], "不同命令不允许自动合并")
        c = self.record(command=["python", "run.py", "temp/c3/task.json"], scope="single-task",
                        dedupe_key="tasklist-runner-crash")
        d = self.record(command=["python", "run.py", "temp/d4/task.json"], scope="single-task",
                        dedupe_key="tasklist-runner-crash")
        self.assertEqual(c["issue_id"], d["issue_id"], "显式 dedupe_key 才归并")
        self.assertEqual(c["payload"]["dedupe_source"], "explicit")
        self.assertEqual(self.issues.status(c["issue_id"])["dedupe_source"], "explicit")
        self.assertEqual(len(self.issues.status()), 3)

    def test_issue_ids_are_sequential(self):
        ids = [self.record(command=["cmd", str(index)], scope="s")["issue_id"] for index in range(3)]
        self.assertEqual(ids, ["ISSUE-000001", "ISSUE-000002", "ISSUE-000003"])

    def test_concurrent_same_command_yields_one_issue(self):
        script = (
            "import sys\n"
            "from kernel.autoresearch.issues import Issues\n"
            "ws, log = sys.argv[1], sys.argv[2]\n"
            "issues = Issues(ws)\n"
            "event = issues.record(summary='并发登记', owner='session-a',\n"
            "                      command=['python', '-m', 'pytest', 'tests'], exitcode=1,\n"
            "                      sources=[{'path': log, 'locator': 'L2'}],\n"
            "                      scope='tests', actor='agent')\n"
            "print(event['issue_id'], event['kind'])\n"
        )
        command = [sys.executable, "-X", "utf8", "-B", "-c", script,
                   str(self.ws), "results/run.log"]
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        first = subprocess.Popen(command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        outputs = [first.communicate(timeout=120), second.communicate(timeout=120)]
        self.assertEqual([first.returncode, second.returncode], [0, 0], outputs)
        printed = [line.split() for line in (outputs[0][0] + outputs[1][0]).splitlines() if line.strip()]
        self.assertEqual(len(printed), 2, outputs)
        issue_ids = {item[0] for item in printed}
        kinds = {item[1] for item in printed}
        self.assertEqual(len(issue_ids), 1, printed)
        self.assertEqual(kinds, {"opened", "recurred"}, printed)
        self.assertEqual(len(self.issues.events()), 2)
        self.assertEqual(len(self.issues.status()), 1)


class OwnershipTests(IssueCase):
    def test_assignment_changes_owner_field(self):
        issue_id = self.record()["issue_id"]
        self.assertEqual(self.issues.status(issue_id)["owner"], "session-a")
        self.issues.update(issue_id, kind="assigned", actor="agent", text="换给 session-b 负责",
                           sources=self.evidence(), owner="session-b")
        state = self.issues.status(issue_id)
        self.assertEqual(state["owner"], "session-b")
        self.assertEqual(state["state"], "open")
        self.assertEqual(state["last_kind"], "assigned")

    def test_assignment_requires_explicit_owner(self):
        issue_id = self.record()["issue_id"]
        with self.assertRaises(ValueError) as caught:
            self.issues.update(issue_id, kind="assigned", actor="agent",
                               text="负责人改成 session-b", sources=self.evidence())
        self.assertIn("owner", str(caught.exception))
        self.assertEqual(self.issues.status(issue_id)["owner"], "session-a")


class VerificationTests(IssueCase):
    def test_verification_needs_command_exitcode_and_evidence(self):
        issue_id = self.record()["issue_id"]
        with self.assertRaises(IssueRuleError):
            self.issues.update(issue_id, kind="verified", actor="agent", text="应该好了",
                               sources=self.evidence(), command=["python", "-m", "pytest"], exitcode=0)
        self.issues.update(issue_id, kind="fixed", actor="agent", text="改了入口校验",
                           sources=self.evidence(name="fix.log", locator="L1"))
        with self.assertRaises(ValueError) as caught:
            self.issues.update(issue_id, kind="verified", actor="agent", text="应该好了",
                               sources=self.evidence(), passed=True, command=None, exitcode=None)
        self.assertIn("argv", str(caught.exception))
        with self.assertRaises(ValueError):
            self.issues.update(issue_id, kind="verified", actor="agent", text="应该好了",
                               sources=self.evidence(), command="python -m pytest", exitcode=0)

    def test_passed_must_match_exitcode(self):
        issue_id = self.record()["issue_id"]
        self.issues.update(issue_id, kind="fixed", actor="agent", text="改了入口校验",
                           sources=self.evidence(name="fix.log", locator="L1"))
        with self.assertRaises(IssueRuleError):
            self.issues.update(issue_id, kind="verified", actor="agent", text="应该好了",
                               sources=self.evidence(), command=["python", "-m", "pytest"],
                               exitcode=1, passed=True)
        event = self.issues.update(issue_id, kind="verified", actor="agent", text="还是失败",
                                   sources=self.evidence(), command=["python", "-m", "pytest"],
                                   exitcode=1)
        self.assertEqual(event["payload"]["state"], "open")
        self.assertFalse(event["payload"]["passed"])
        state = self.issues.status(issue_id)
        self.assertEqual(state["state"], "open")
        self.assertFalse(state["resume_allowed"])
        self.assertEqual(state["verification"]["exitcode"], 1)
        self.assertEqual(state["verification"]["command"], ["python", "-m", "pytest"])

    def test_verified_records_command_and_exitcode_not_just_a_boolean(self):
        issue_id = self.closed_issue()
        state = self.issues.status(issue_id)
        self.assertEqual(state["state"], "closed")
        self.assertTrue(state["resume_allowed"])
        self.assertEqual(state["verification"]["command"], ["python", "-m", "pytest", "tests"])
        self.assertEqual(state["verification"]["exitcode"], 0)
        self.assertIn("完成记录", state["verification"]["note"])


class ReopenTests(IssueCase):
    def test_fixed_then_recurred_reopens_and_keeps_history(self):
        issue_id = self.closed_issue()
        before = self.kinds(issue_id)
        again = self.record()
        self.assertEqual(again["issue_id"], issue_id)
        self.assertEqual(again["kind"], "recurred")
        state = self.issues.status(issue_id)
        self.assertEqual(state["state"], "open")
        self.assertFalse(state["resume_allowed"])
        self.assertEqual(self.kinds(issue_id), before + ["recurred"])
        self.assertIn("verified", self.kinds(issue_id))  # 早先的复验证据还在
        self.fixed_and_verified(issue_id)
        self.assertEqual(self.issues.status(issue_id)["state"], "closed")

    def test_explicit_reopen_requires_closed_or_fixed(self):
        issue_id = self.record()["issue_id"]
        with self.assertRaises(IssueRuleError):
            self.issues.update(issue_id, kind="reopened", actor="agent", text="又出现了",
                               sources=self.evidence())
        self.fixed_and_verified(issue_id)
        event = self.issues.update(issue_id, kind="reopened", actor="agent",
                                   text="上线后又见到同一个错", sources=self.evidence())
        self.assertEqual(event["payload"]["state"], "open")


class ArchiveTests(IssueCase):
    def archive_files(self):
        return sorted((self.ws / ".autoresearch" / "issues" / "archive").rglob("*.json"))

    def test_archive_copies_full_history_and_index(self):
        issue_id = self.closed_issue()
        history = [row for row in self.issues.events() if row["issue_id"] == issue_id]
        event = self.issues.archive(issue_id, actor="agent")
        path = self.ws / event["payload"]["archive_path"]
        self.assertTrue(path.is_file())
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([row["digest"] for row in on_disk], [row["digest"] for row in history])
        self.assertEqual(event["payload"]["archive_sha256"],
                         __import__("hashlib").sha256(path.read_bytes()).hexdigest())
        self.assertEqual(event["payload"]["bytes"], path.stat().st_size)
        self.assertEqual(event["payload"]["event_count"], len(history))
        state = self.issues.status(issue_id)
        self.assertEqual(state["state"], "closed")
        self.assertTrue(state["resume_allowed"])
        self.assertEqual(len(self.issues.events()), len(history) + 1)  # 热件一条不删
        index = self.issues.archive_index()
        self.assertEqual([item["archive_path"] for item in index], [event["payload"]["archive_path"]])

    def test_duplicate_archive_is_refused(self):
        issue_id = self.closed_issue()
        self.issues.archive(issue_id, actor="agent")
        with self.assertRaises(IssueRuleError) as caught:
            self.issues.archive(issue_id, actor="agent")
        self.assertIn("已经归过冷件", str(caught.exception))
        self.assertEqual(self.kinds(issue_id).count("archived"), 1)
        self.assertEqual(len(self.archive_files()), 1)
        self.assertEqual(len(self.issues.archive_index()), 1)

    def test_unclosed_issue_cannot_be_archived(self):
        issue_id = self.record()["issue_id"]
        with self.assertRaises(IssueRuleError):
            self.issues.archive(issue_id, actor="agent")
        self.assertEqual(self.archive_files(), [])

    def test_archive_never_overwrites_existing_file(self):
        issue_id = self.closed_issue()
        order = len([row for row in self.issues.events() if row["issue_id"] == issue_id])
        directory = self.ws / ".autoresearch" / "issues" / "archive"
        directory.mkdir(parents=True)
        day = directory / __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime())
        day.mkdir(parents=True)
        occupied = day / f"{issue_id}-{order}.json"
        occupied.write_bytes(b'{"old":"do not overwrite"}')
        event = self.issues.archive(issue_id, actor="agent")
        self.assertNotEqual(event["payload"]["archive_path"], occupied.relative_to(self.ws).as_posix())
        self.assertEqual(occupied.read_bytes(), b'{"old":"do not overwrite"}')
        self.assertTrue((self.ws / event["payload"]["archive_path"]).is_file())
        self.assertFalse(event["payload"]["reused"])

    def test_archive_write_failure_leaves_diagnosable_event(self):
        issue_id = self.closed_issue()
        # 把 .autoresearch/issues 变成普通文件：归档目录建不出来（真实 OSError，不是假失败）。
        issues_dir = self.ws / ".autoresearch" / "issues"
        if issues_dir.exists():
            raise AssertionError("夹具前提：归档目录还不该存在")
        issues_dir.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            self.issues.archive(issue_id, actor="agent")
        self.assertIn("archive_failed", str(caught.exception))
        events = [row for row in self.issues.events() if row["issue_id"] == issue_id]
        self.assertEqual(events[-1]["kind"], "archive_failed")
        self.assertEqual(events[-1]["payload"]["reason"], "archive_write_failed")
        self.assertTrue(events[-1]["payload"]["error"])
        self.assertEqual(events[-1]["payload"]["intended_path"].split("/")[0], ".autoresearch")
        state = self.issues.status(issue_id)
        self.assertEqual(state["state"], "closed")
        self.assertTrue(state["resume_allowed"])
        self.assertEqual(self.issues.archive_index(), [])

    def test_reopened_and_closed_again_allows_a_second_archive(self):
        issue_id = self.closed_issue()
        first = self.issues.archive(issue_id, actor="agent")
        self.record()  # 复发
        self.fixed_and_verified(issue_id)
        second = self.issues.archive(issue_id, actor="agent")
        self.assertEqual(self.kinds(issue_id).count("archived"), 2)
        self.assertNotEqual(first["payload"]["archive_path"], second["payload"]["archive_path"])
        self.assertEqual(len(self.issues.archive_index()), 2)


class LedgerIntegrityTests(IssueCase):
    def test_readonly_operations_never_write(self):
        issue_id = self.record()["issue_id"]
        before = self.db_bytes()
        readonly = Issues(self.ws, readonly=True)
        readonly.events()
        readonly.status()
        readonly.status(issue_id)
        readonly.archive_index()
        self.assertEqual(before, self.db_bytes())
        with self.assertRaises(IssueRuleError):
            readonly.record(summary="x", owner="o", command=["cmd"], exitcode=1,
                            sources=self.evidence(), scope="s")
        self.assertEqual(before, self.db_bytes())

    def test_readonly_on_missing_ledger_is_empty(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        readonly = Issues(empty, readonly=True)
        self.assertEqual(readonly.events(), [])
        self.assertEqual(readonly.status(), [])

    def test_history_is_append_only_and_tamper_is_detected(self):
        issue_id = self.record()["issue_id"]
        import contextlib
        with contextlib.closing(sqlite3.connect(str(self.issues.db))) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE issue_events SET kind='closed'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM issue_events")
            conn.execute("INSERT INTO issue_events VALUES(2,?, 'opened', '{}', '', 'x')", (issue_id,))
            conn.commit()  # 伪造行必须真的落盘，链校验才有对象
        with self.assertRaises(ValueError) as caught:
            self.issues.events()
        self.assertIn("台账损坏", str(caught.exception))

    def test_unknown_issue_is_refused(self):
        with self.assertRaises(ValueError):
            self.issues.status("ISSUE-999999")
        with self.assertRaises(ValueError):
            self.issues.update("ISSUE-999999", kind="fixed", actor="agent", text="x",
                               sources=self.evidence())
        with self.assertRaises(ValueError):
            self.issues.archive("ISSUE-999999", actor="agent")


class CliTests(IssueCase):
    def cli(self, argv):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = issues_main(argv)
        return code, out.getvalue()

    def test_cli_record_status_index(self):
        code, output = self.cli(["--workspace", str(self.ws), "record",
                                 "--summary", "跑测试崩了", "--owner", "session-a",
                                 "--scope", "tests", "--exitcode", "1",
                                 "--source", "results/run.log",
                                 "--", "python", "-m", "pytest", "tests"])
        self.assertEqual(code, 0, output)
        payload = json.loads(output)
        issue_id = payload["event"]["issue_id"]
        self.assertEqual(payload["event"]["payload"]["command"],
                         ["python", "-m", "pytest", "tests"])
        code, output = self.cli(["status", "--workspace", str(self.ws)])
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["issues"][0]["issue_id"], issue_id)
        code, output = self.cli(["index", "--workspace", str(self.ws)])
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["archive_index"], [])

    def test_cli_rejections_and_input_errors(self):
        code, output = self.cli(["--workspace", str(self.ws), "record",
                                 "--summary", "x", "--owner", "o", "--scope", "s", "--exitcode", "1"])
        self.assertEqual(code, 2, output)  # 少了真实 argv
        self.assertIn("argv", output)
        code, _ = self.cli(["--workspace", str(self.ws), "status", "--issue", "ISSUE-999999"])
        self.assertEqual(code, 2)
        code, output = self.cli(["--workspace", str(Path(self._tmp.name) / "nope"), "status"])
        self.assertEqual(code, 2, output)

    def test_cli_full_lifecycle_with_rule_rejections(self):
        code, output = self.cli(["record", "--workspace", str(self.ws), "--summary", "崩了",
                                 "--owner", "session-a", "--scope", "tests", "--exitcode", "1",
                                 "--source", "results/run.log", "--", "python", "-m", "pytest"])
        self.assertEqual(code, 0, output)
        issue_id = json.loads(output)["event"]["issue_id"]
        code, output = self.cli(["verify", "--workspace", str(self.ws), "--issue", issue_id,
                                 "--text", "应该好了", "--source", "results/run.log",
                                 "--exitcode", "0", "--", "python", "-m", "pytest"])
        self.assertEqual(code, 9, output)  # 还没记修复
        for args in (["fix", "--workspace", str(self.ws), "--issue", issue_id,
                      "--text", "改了入口校验", "--source", "results/run.log"],
                     ["verify", "--workspace", str(self.ws), "--issue", issue_id,
                      "--text", "复跑", "--source", "results/run.log", "--exitcode", "0",
                      "--", "python", "-m", "pytest"],
                     ["archive", "--workspace", str(self.ws), "--issue", issue_id]):
            code, output = self.cli(args)
            self.assertEqual(code, 0, output)
        code, output = self.cli(["archive", "--workspace", str(self.ws), "--issue", issue_id])
        self.assertEqual(code, 9, output)  # 重复归档
        code, output = self.cli(["index", "--workspace", str(self.ws)])
        self.assertEqual(code, 0, output)
        self.assertEqual(len(json.loads(output)["archive_index"]), 1)


if __name__ == "__main__":
    unittest.main()
