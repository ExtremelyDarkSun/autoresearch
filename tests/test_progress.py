# GENERATED-BY: 完整功能迁移（持续推进 A1–A7 / 远端进度 H1–H9 / 入向超期 D2）
# LAST-EDIT: 2026-11-09（首版；真实子进程 / 真实文件与目录变化 / 三次失败 / 退避 / 超期优先 /
#             goal 覆盖防重复 / 租约并发 / needs_rearm / watch 子进程结构化退出）
# PURPOSE: progress 机制的离线回归：用合成临时夹具证明采集、fail-closed、判定顺序、租约围栏、
#          重挂协议与受管监视的退出语义；不连接真实机器、不发消息、不触发外部评测。
# ORIGIN: kernel/autoresearch/progress.py 的契约；行为预期 A1–A7 / D2 / H1–H9。
# CUMULATIVE-DELTA: 首版。全部测试只用标准库与临时目录；远端用例用「本地假 ssh / 假传输程序」，
#          因此验证的是 argv 模板展开、shell=False 与取回路径，而不是真实网络。

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kernel.autoresearch import progress

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "kernel" / "autoresearch" / "progress_cli.py"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


class Clock:
    """可注入时钟：退避与超期用例要能「推进时间」而不真的睡。"""

    def __init__(self, start=1_000_000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def counter_argv(path):
    """真实子进程：读文件行数并按 JSON 输出。"""
    code = "import json,sys;print(json.dumps({'n': len(open(sys.argv[1]).read().splitlines())}))"
    return [sys.executable, "-c", code, str(path)]


def make_spec(collectors, events=(), **overrides):
    spec = {
        "version": 1,
        "name": "unit",
        "owner": "owner-unit",
        "quiet": {"window_sec": 600, "max_window_sec": 1200},
        "round": {"kind": "direct_instruction"},
        "overdue": {"max_age_sec": 304},
        "state": {"root": ".state/progress", "db": ".state/progress.sqlite3"},
        "collectors": list(collectors),
        "events": list(events),
    }
    spec.update(overrides)
    return spec


def write_spec(root, spec, name="spec.json"):
    path = Path(root) / name
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class CliResult:
    """CLI 调用的结果。⚠️ 用文件句柄收集输出，不用管道（与采集协议一致）。"""

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
                cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout,
            )
        return CliResult(completed.returncode,
                         out_path.read_text(encoding="utf-8", errors="replace"),
                         err_path.read_text(encoding="utf-8", errors="replace"))


def events_of(instance, kind):
    return [event for event in instance.events() if event["kind"] == kind]


# ---------------------------------------------------------------------------
# spec 闭集
# ---------------------------------------------------------------------------


class SpecTests(unittest.TestCase):
    def test_unknown_keys_and_missing_round_are_rejected(self):
        base = make_spec([{"key": "n", "kind": "cmd", "argv": ["echo", "1"], "pick": "n"}])
        progress.validate_spec(base)  # 基线可过
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(dict(base, extra_key=True))
        broken = dict(base)
        broken.pop("round")
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(broken)
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(dict(base, round={"kind": "old_stage"}))

    def test_command_must_be_argv_not_shell_string(self):
        base = make_spec([{"key": "n", "kind": "cmd", "argv": "echo 1"}])
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(base)
        base = make_spec([{"key": "n", "kind": "cmd", "argv": ["echo", "1"], "unknown": 1}])
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(base)

    def test_ssh_transport_requires_explicit_template_and_address(self):
        collector = {"key": "n", "kind": "cmd", "argv": ["echo", "1"],
                     "transport": {"type": "ssh", "host": "", "user": "u", "port": 22,
                                   "ssh_argv": ["ssh", "{user}@{host}"]}}
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(make_spec([collector]))
        collector = {"key": "n", "kind": "cmd", "argv": ["echo", "1"],
                     "transport": {"type": "ssh", "host": "h", "user": "u", "port": 22,
                                   "ssh_argv": ["ssh", "{host}", "{secret}"]}}
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(make_spec([collector]))

    def test_remote_path_collector_needs_argv_or_fetch(self):
        collector = {"key": "d", "kind": "dir", "path": "/data/out", "aspect": "file_count",
                     "transport": {"type": "ssh", "host": "h", "user": "u", "port": 22,
                                   "ssh_argv": ["ssh", "{user}@{host}"]}}
        with self.assertRaises(progress.SpecError):
            progress.validate_spec(make_spec([collector]))

    def test_local_path_collector_rejects_ignored_remote_settings(self):
        for extra in ({"remote_argv": ["echo", "1"]}, {"pick": "n"}, {"format": "regex"}):
            with self.subTest(extra=extra):
                collector = {"key": "f", "kind": "file", "path": "x", "aspect": "exists"}
                collector.update(extra)
                with self.assertRaises(progress.SpecError):
                    progress.validate_spec(make_spec([collector]))


# ---------------------------------------------------------------------------
# 采集：真实子进程 + 真实文件/目录 + 原始捕获
# ---------------------------------------------------------------------------


class CollectTests(unittest.TestCase):
    def test_command_probe_keeps_raw_output_and_reports_path_and_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "n.txt"
            data.write_text("1\n", encoding="utf-8")
            spec = make_spec([{"key": "n", "kind": "cmd", "argv": counter_argv(data), "pick": "n"}],
                             [{"key": "n", "op": "increased_by", "value": 1}])
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            first = instance.probe()
            self.assertTrue(first["ok"], first["failures"])
            self.assertTrue(first["baseline"])
            self.assertEqual(first["values"]["n"], 1)
            capture = first["captures"]["n"]
            stdout_path = root / capture["stdout"]["path"]
            self.assertTrue(stdout_path.is_file())
            # 子进程在 Windows 上会写 CRLF；原始字节一律不截断、不改写
            self.assertEqual(stdout_path.read_bytes().replace(b"\r\n", b"\n"), b'{"n": 1}\n')
            self.assertEqual(capture["stdout"]["sha256"], progress._digest(stdout_path.read_bytes()))
            self.assertEqual(capture["stdout"]["text"].strip(), '{"n": 1}')
            self.assertIn('"n": 1', capture["stdout"]["text"])
            self.assertTrue((root / capture["stderr"]["path"]).is_file())
            self.assertEqual(capture["exit_code"], 0)
            self.assertFalse(capture["timed_out"])

            # 原始输出同时作为事件证据存档（路径 + 字节数 + sha256 + 全文）
            observation = events_of(instance, "observation")[-1]
            archived = observation["payload"]["captures"]["n"]
            self.assertIn('"n": 1', archived["stdout"]["text"])
            self.assertEqual(archived["stdout"]["bytes"], stdout_path.stat().st_size)
            self.assertTrue(archived["stdout"]["path"].startswith(".state/progress/captures/"))

            data.write_text("1\n2\n", encoding="utf-8")
            second = instance.probe()
            self.assertTrue(second["ok"])
            self.assertEqual(len(second["changes"]), 1)
            change = second["changes"][0]
            self.assertEqual((change["key"], change["op"]), ("n", "increased_by"))
            self.assertEqual((change["before"], change["after"]), (1, 2))
            self.assertNotEqual(change["before_sha256"], change["after_sha256"])
            self.assertEqual(change["what_changed"][0]["after"], 2)
            # 变化载荷里带原始捕获（原文仍是全文）
            change_event = events_of(instance, "change")[-1]
            self.assertIn('"n": 2', change_event["payload"]["raw_captures"]["n"]["stdout"]["text"])

    def test_file_and_dir_changes_produce_hashed_events(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "out").mkdir()
            (root / "lines.txt").write_text("a\nb\n", encoding="utf-8")
            spec = make_spec([
                {"key": "lines", "kind": "file", "path": str(root / "lines.txt"), "aspect": "line_count"},
                {"key": "out", "kind": "dir", "path": str(root / "out"), "aspect": "file_count"},
            ], [{"key": "lines", "op": "increased_by", "value": 1},
                {"key": "out", "op": "increased_by", "value": 1}])
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            first = instance.probe()
            self.assertTrue(first["ok"], first["failures"])
            self.assertEqual(first["values"], {"lines": 2, "out": 0})
            (root / "lines.txt").write_text("a\nb\nc\n", encoding="utf-8")
            (root / "out" / "r1.jsonl").write_text("{}\n", encoding="utf-8")
            second = instance.probe()
            self.assertEqual(len(second["changes"]), 2)
            by_key = {item["key"]: item for item in second["changes"]}
            self.assertEqual(by_key["lines"]["after"], 3)
            self.assertEqual(by_key["out"]["after"], 1)
            for item in second["changes"]:
                self.assertTrue(item["before_sha256"].startswith("sha256:"))
                self.assertTrue(item["after_sha256"].startswith("sha256:"))
                self.assertNotEqual(item["before_sha256"], item["after_sha256"])

    def test_timeout_and_nonzero_exit_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            slow = make_spec([{"key": "slow", "kind": "cmd",
                               "argv": [sys.executable, "-c", "import time;time.sleep(5)"],
                               "timeout_sec": 0.8}])
            instance = progress.Progress(root, slow, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            started = time.time()
            result = instance.probe()
            elapsed = time.time() - started
            self.assertFalse(result["ok"])
            self.assertLess(elapsed, 4.0)
            self.assertEqual(result["failures"][0]["kind"], "timeout")
            self.assertTrue(result["failures"][0]["capture"]["timed_out"])
            self.assertTrue((root / result["failures"][0]["capture"]["stdout"]["path"]).is_file())

            root2 = root / "second"
            root2.mkdir()
            failing = make_spec([{"key": "boom", "kind": "cmd",
                                  "argv": [sys.executable, "-c",
                                           "import sys;print('boom');sys.exit(3)"],
                                  "timeout_sec": 30}])
            instance2 = progress.Progress(root2, failing, owner="owner-unit",
                                          goal={"state": "complete", "coverage": "none"})
            result2 = instance2.probe()
            self.assertFalse(result2["ok"])
            self.assertEqual(result2["failures"][0]["kind"], "nonzero_exit")
            self.assertEqual(result2["failures"][0]["capture"]["exit_code"], 3)
            self.assertIn("boom", result2["failures"][0]["capture"]["stdout"]["text"])
            # fail-closed 的关键断言：失败轮不许退化成「没有变化」
            self.assertEqual(result2["changes"], [])

    def test_missing_file_is_fail_closed_but_exists_aspect_is_a_value(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            missing = make_spec([{"key": "gone", "kind": "file",
                                  "path": str(root / "nope.txt"), "aspect": "size"}])
            instance = progress.Progress(root, missing, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            result = instance.probe()
            self.assertFalse(result["ok"])
            self.assertEqual(result["failures"][0]["kind"], "missing")

            root2 = root / "exists"
            root2.mkdir()
            exists = make_spec([{"key": "flag", "kind": "file",
                                 "path": str(root2 / "flag.txt"), "aspect": "exists"}],
                               [{"key": "flag", "op": "appeared"}])
            instance2 = progress.Progress(root2, exists, owner="owner-unit",
                                          goal={"state": "complete", "coverage": "none"})
            self.assertTrue(instance2.probe()["ok"])
            (root2 / "flag.txt").write_text("x", encoding="utf-8")
            result2 = instance2.probe()
            self.assertTrue(result2["ok"])
            self.assertEqual(result2["changes"][0]["op"], "appeared")

    def test_three_failures_escalate_to_progress_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec = make_spec([{"key": "boom", "kind": "cmd",
                               "argv": [sys.executable, "-c", "import sys;sys.exit(9)"],
                               "timeout_sec": 30}])
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            for _ in range(3):
                self.assertFalse(instance.probe()["ok"])
            failures = events_of(instance, "collect_failure")
            unavailable = events_of(instance, "progress_unavailable")
            self.assertEqual(len(failures), 3)
            self.assertEqual(len(unavailable), 1)
            self.assertEqual(unavailable[0]["payload"]["failure_streak"], 3)
            self.assertEqual(unavailable[0]["payload"]["first_failure"], failures[0]["locator"])
            self.assertTrue(unavailable[0]["payload"]["recovery"])

            decision = instance.tick()
            self.assertEqual(decision["verdict"], "wake")
            self.assertEqual(decision["causes"][0]["kind"], "progress_unavailable")
            self.assertEqual(decision["intended_exit"], progress.EXIT_CODES["collect_failed"])
            status = instance.status()
            self.assertFalse(status["ok"])
            self.assertIn("collect_failed", status["blockers"])
            # 成功一轮会清零（新目录，避免旧失败链干扰）
            root2 = root / "ok"
            root2.mkdir()
            good = make_spec([{"key": "n", "kind": "cmd", "argv": [sys.executable, "-c",
                                                                   "print('1')"], "format": "regex",
                               "pattern": r"(1)"}])
            instance2 = progress.Progress(root2, good, owner="owner-unit",
                                          goal={"state": "complete", "coverage": "none"})
            instance2.probe()
            self.assertEqual(instance2.status()["progress"]["failure_streak"], 0)

    def test_ssh_argv_template_uses_only_spec_values_and_no_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_ssh = ("import json,sys;"
                        "print(json.dumps({'user_host': sys.argv[1], 'port': sys.argv[2],"
                        " 'remote': sys.argv[3]}))")
            spec = make_spec([{
                "key": "remote", "kind": "cmd", "pick": "remote",
                "argv": ["echo", "hi"],
                "transport": {"type": "ssh", "host": "example.invalid", "user": "runner", "port": 2222,
                              "ssh_argv": [sys.executable, "-c", fake_ssh,
                                           "{user}@{host}", "{port}"],
                              "fetch_argv": [sys.executable, "-c", "pass",
                                             "{source}", "{destination}"]},
            }])
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            with mock.patch.object(progress.subprocess, "run", wraps=subprocess.run) as spy:
                result = instance.probe()
            self.assertTrue(result["ok"], result["failures"])
            self.assertEqual(result["values"]["remote"], "echo hi")
            capture = result["captures"]["remote"]
            self.assertEqual(capture["transport"], "ssh")
            self.assertEqual(capture["argv"][-3:], ["runner@example.invalid", "2222", "echo hi"])
            # shell=False 且 argv 是列表：命令从不经过本地 shell
            called = spy.call_args
            self.assertIsInstance(called.args[0], list)
            self.assertIs(called.kwargs.get("shell"), False)

    def test_remote_path_collector_can_use_declared_remote_argv(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_ssh = "import json;print(json.dumps({'count': 3}))"
            spec = make_spec([{
                "key": "out", "kind": "dir", "path": "/data/out", "aspect": "file_count",
                "pick": "count",
                "remote_argv": ["sh", "-c", "count files under the declared path"],
                "transport": {"type": "ssh", "host": "example.invalid", "user": "runner",
                              "port": 2222,
                              "ssh_argv": [sys.executable, "-c", fake_ssh]},
            }])
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            result = instance.probe()
            self.assertTrue(result["ok"], result["failures"])
            self.assertEqual(result["values"]["out"], 3)
            self.assertIn("count files under the declared path",
                          result["captures"]["out"]["argv"][-1])

    def test_file_transfer_adapter_fetches_remote_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "remote" / "results.jsonl"
            source.parent.mkdir()
            source.write_text("{}\n{}\n{}\n", encoding="utf-8")
            copy = "import shutil,sys;shutil.copyfile(sys.argv[1], sys.argv[2])"
            spec = make_spec([{
                "key": "lines", "kind": "file", "path": str(source), "aspect": "line_count",
                "transport": {"type": "ssh", "host": "example.invalid", "user": "runner", "port": 2222,
                              "ssh_argv": ["ssh", "{user}@{host}"],
                              "fetch_argv": [sys.executable, "-c", copy, "{source}", "{destination}"]},
            }])
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            result = instance.probe()
            self.assertTrue(result["ok"], result["failures"])
            self.assertEqual(result["values"]["lines"], 3)
            fetched = list(instance.capture_root.rglob("results.jsonl"))
            self.assertEqual(len(fetched), 1)
            self.assertEqual(fetched[0].read_text(encoding="utf-8"), "{}\n{}\n{}\n")
            # 取回走的是 spec 里的 fetch 模板（{source}/{destination} 已展开），不是内核硬编码
            fetch_record = result["captures"]["lines"]
            self.assertTrue(any("fetched" in part for part in fetch_record["argv"]), fetch_record["argv"])
            self.assertEqual(fetch_record["exit_code"], 0)


# ---------------------------------------------------------------------------
# 判定顺序：goal / overdue / 变化 / 静默
# ---------------------------------------------------------------------------


class DecisionTests(unittest.TestCase):
    def _changed_instance(self, root, **spec_overrides):
        data = root / "n.txt"
        data.write_text("1\n", encoding="utf-8")
        spec = make_spec([{"key": "n", "kind": "cmd", "argv": counter_argv(data), "pick": "n"}],
                         [{"key": "n", "op": "increased_by", "value": 1}], **spec_overrides)
        return spec, data

    def test_goal_unknown_is_never_green(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, data = self._changed_instance(root)
            instance = progress.Progress(root, spec, owner="owner-unit")  # 没有 goal 来源
            instance.probe()
            status = instance.status()
            self.assertFalse(status["ok"])
            self.assertFalse(status["green"])
            self.assertIn("goal_state_unknown", status["blockers"])
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "undecidable")
            self.assertEqual(decision["intended_exit"], progress.EXIT_CODES["undecidable"])
            self.assertEqual(decision["causes"][0]["kind"], "goal_state_unknown")
            self.assertFalse(decision["supplement"]["allowed"])

    def test_goal_full_coverage_suppresses_supplement_even_on_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, data = self._changed_instance(root)
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "active", "coverage": "full"})
            instance.probe()
            data.write_text("1\n2\n", encoding="utf-8")
            instance.probe()  # 变化确实发生了
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "sleep")
            self.assertEqual(decision["causes"][0]["kind"], "goal_covers")
            self.assertEqual(events_of(instance, "wake"), [])
            self.assertFalse(instance.status()["needs_rearm"]["value"])
            self.assertFalse(decision["supplement"]["allowed"])

    def test_goal_partial_coverage_is_undecidable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, _ = self._changed_instance(root)
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "active", "coverage": "partial"})
            instance.probe()
            status = instance.status()
            self.assertFalse(status["ok"])
            self.assertIn("goal_coverage_partial", status["blockers"])
            self.assertEqual(instance.tick()["verdict"], "undecidable")

    def test_goal_none_coverage_allows_supplement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, data = self._changed_instance(root)
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "active", "coverage": "none"})
            instance.probe()
            data.write_text("1\n2\n", encoding="utf-8")
            instance.probe()
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "wake")
            self.assertTrue(decision["supplement"]["allowed"])
            self.assertEqual(decision["causes"][0]["kind"], "progress_change")

    def test_overdue_user_message_outranks_quiet_window_and_goal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clock = Clock()
            spec, data = self._changed_instance(root)
            instance = progress.Progress(root, spec, owner="owner-unit", clock=clock,
                                         goal={"state": "active", "coverage": "full"},
                                         overdue={"max_age_sec": 304,
                                                  "items": [{"id": "m1", "age_sec": 900,
                                                             "summary": "用户问了一个问题"}]})
            instance.probe()
            clock.advance(5)  # 静默窗口远未到点
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "wake")
            self.assertEqual(decision["causes"][0]["kind"], "overdue_user_message")
            self.assertIn("先回复用户", decision["reason"])
            status = instance.status()
            self.assertFalse(status["ok"])
            self.assertIn("overdue_user_message", status["blockers"])

    def test_overdue_provider_failure_is_undecidable(self):
        def boom():
            raise RuntimeError("inbox 读不到")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, _ = self._changed_instance(root)
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "active", "coverage": "none"},
                                         overdue=boom)
            instance.probe()
            decision = instance.tick()
            self.assertEqual(decision["verdict"], "undecidable")
            self.assertEqual(decision["causes"][0]["kind"], "overdue_unknown")
            self.assertIn("overdue_unknown", instance.status()["blockers"])

    def test_direct_instruction_round_comes_from_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, _ = self._changed_instance(root, round={"kind": "direct_instruction"})
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            instance.probe()
            status = instance.status()
            self.assertEqual(status["round"]["kind"], "direct_instruction")
            self.assertFalse(status["round"]["planning_required"])
            decision = instance.tick()
            self.assertFalse(decision["planning_required"])
            self.assertEqual(decision["round"]["kind"], "direct_instruction")
            self.assertFalse(decision["round"]["planning_required"])

            root2 = root / "planned"
            root2.mkdir()
            spec2, _ = self._changed_instance(root2, round={"kind": "planned"})
            instance2 = progress.Progress(root2, spec2, owner="owner-unit",
                                          goal={"state": "complete", "coverage": "none"})
            instance2.probe()
            self.assertTrue(instance2.status()["round"]["planning_required"])

    def test_quiet_window_wakes_and_idle_windows_double_with_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clock = Clock()
            spec, _ = self._changed_instance(root, quiet={"window_sec": 10, "max_window_sec": 24})
            instance = progress.Progress(root, spec, owner="owner-unit", clock=clock,
                                         goal={"state": "complete", "coverage": "none"})
            instance.probe()
            self.assertEqual(instance.tick()["verdict"], "sleep")  # 刚有动静
            clock.advance(10)
            first = instance.tick()
            self.assertEqual(first["verdict"], "wake")
            self.assertEqual(first["causes"][0]["kind"], "quiet_window")
            instance.outcome("nothing", next_condition="等外部产物多一行")
            instance.rearm(note="第一轮无事")
            self.assertEqual(instance.status()["quiet"]["effective_sec"], 10.0)

            clock.advance(10)
            self.assertEqual(instance.tick()["verdict"], "wake")
            instance.outcome("nothing", next_condition="等外部产物多一行")
            instance.rearm(note="第二轮无事")
            self.assertEqual(instance.status()["quiet"]["effective_sec"], 20.0)  # 两次无事 ⇒ 翻倍

            clock.advance(20)
            self.assertEqual(instance.tick()["verdict"], "wake")
            instance.outcome("nothing", next_condition="等外部产物多一行")
            instance.rearm(note="第三轮无事")
            clock.advance(20)
            self.assertEqual(instance.tick()["verdict"], "wake")
            instance.outcome("nothing", next_condition="等外部产物多一行")
            instance.rearm(note="第四轮无事")
            self.assertEqual(instance.status()["quiet"]["effective_sec"], 24.0)  # 封顶

            clock.advance(24)
            self.assertEqual(instance.tick()["verdict"], "wake")
            instance.outcome("advance", action="推进了一轮")
            instance.rearm(note="推进后清零")
            self.assertEqual(instance.status()["quiet"]["effective_sec"], 10.0)


# ---------------------------------------------------------------------------
# 唤醒协议：needs_rearm / 三出口 / 重挂
# ---------------------------------------------------------------------------


class WakeProtocolTests(unittest.TestCase):
    def _woken(self, root, clock=None):
        data = root / "n.txt"
        data.write_text("1\n", encoding="utf-8")
        spec = make_spec([{"key": "n", "kind": "cmd", "argv": counter_argv(data), "pick": "n"}],
                         [{"key": "n", "op": "increased_by", "value": 1}])
        instance = progress.Progress(root, spec, owner="owner-unit", clock=clock or time.time,
                                     goal={"state": "complete", "coverage": "none"})
        instance.probe()
        data.write_text("1\n2\n", encoding="utf-8")
        decision = instance.tick()
        assert decision["verdict"] == "wake", decision
        return instance

    def test_wake_writes_needs_rearm_and_structured_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            instance = self._woken(root)
            wake = events_of(instance, "wake")[-1]
            self.assertTrue(wake["payload"]["wake_id"].startswith("W-"))
            self.assertFalse(wake["payload"]["host_woken"])
            self.assertTrue(wake["payload"]["requires_host_notification"])
            self.assertEqual(wake["payload"]["intended_exit"], progress.EXIT_CODES["ok"])
            self.assertTrue(wake["payload"]["needs_rearm"])
            needs = instance.status()["needs_rearm"]
            self.assertTrue(needs["value"])
            self.assertEqual(needs["locator"], wake["locator"])
            # 未重挂前不许再挂第二个作业
            self.assertEqual(instance.tick()["verdict"], "pending_rearm")
            self.assertEqual(instance.watch()["verdict"], "pending_rearm")
            self.assertEqual(instance.watch()["intended_exit"], progress.EXIT_CODES["pending_rearm"])

    def test_three_outcome_exits_are_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            instance = self._woken(root)
            with self.assertRaises(progress.ProgressError):
                instance.outcome("advance")  # 缺 action
            with self.assertRaises(progress.ProgressError):
                instance.outcome("nothing")  # 缺 next_condition
            with self.assertRaises(progress.ProgressError):
                instance.outcome("problem")  # 缺 problem
            with self.assertRaises(progress.ProgressError):
                instance.outcome("blocked")  # 不在三出口里
            problem = instance.outcome("problem", problem="远端采集链路挂了",
                                       sources=["event:1"])
            self.assertTrue(problem["issue_id"].startswith("ISSUE-"))
            # 问题号必须真的落进事件账，而不是只在返回值里
            persisted = events_of(instance, "outcome")[-1]["payload"]
            self.assertEqual(persisted["issue_id"], problem["issue_id"])
            self.assertTrue(instance.status()["needs_rearm"]["value"])

    def test_rearm_requires_outcome_first_and_outcome_is_single_use(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            instance = self._woken(root)
            with self.assertRaises(progress.ProgressError):  # 没记出口就重挂 ⇒ 拒（H7）
                instance.rearm(note="跳出口")
            first = instance.outcome("nothing", next_condition="等下一份产物")
            self.assertTrue(first["ok"])
            with self.assertRaises(progress.ProgressError):  # 一次唤醒只能有一个出口
                instance.outcome("advance", action="又想改口")
            self.assertEqual(instance.rearm(note="记完出口再重挂")["generation"], 1)
            with self.assertRaises(progress.ProgressError):  # 已重挂 ⇒ 没有待处置的唤醒
                instance.outcome("advance", action="重复出口")

    def test_rearm_is_explicit_and_single_use(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            instance = self._woken(root)
            instance.outcome("nothing", next_condition="等下一份产物")
            first = instance.rearm(note="处理完一轮")
            self.assertEqual(first["generation"], 1)
            self.assertFalse(first["needs_rearm"]["value"])
            self.assertIn("watch", first["next_command"])
            with self.assertRaises(progress.ProgressError):
                instance.rearm(note="重复重挂")  # 没有待重挂的作业
            self.assertFalse(instance.status()["needs_rearm"]["value"])


# ---------------------------------------------------------------------------
# 租约与围栏
# ---------------------------------------------------------------------------


class LeaseTests(unittest.TestCase):
    def test_second_job_same_owner_is_refused_and_fence_protects_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "n.txt"
            data.write_text("1\n", encoding="utf-8")
            spec = make_spec([{"key": "n", "kind": "cmd", "argv": counter_argv(data), "pick": "n"}])
            first = progress.Progress(root, spec, owner="owner-unit", holder="holder-A",
                                      goal={"state": "complete", "coverage": "none"})
            second = progress.Progress(root, spec, owner="owner-unit", holder="holder-B",
                                       goal={"state": "complete", "coverage": "none"})
            lease = first.acquire()
            self.assertEqual(lease["fence"], 1)
            with self.assertRaises(progress.LeaseBusy):
                second.acquire()
            # 被拒者只追加 lease_busy：租约、持有人都不动
            state = first.lease()
            self.assertEqual(state["holder"], "holder-A")
            self.assertEqual(state["fence"], 1)
            self.assertTrue(state["live"])
            busy = events_of(second, "lease_busy")
            self.assertEqual(len(busy), 1)
            self.assertEqual(busy[0]["payload"]["held_by"], "holder-A")

            refused = second.tick()
            self.assertEqual(refused["verdict"], "lease_busy")
            self.assertEqual(refused["intended_exit"], progress.EXIT_CODES["lease"])

            # 模拟租约过期（不杀进程、不判 stale）：接管方围栏 +1，旧围栏的写入被拒
            conn = sqlite3.connect(first.db_path)
            try:
                conn.execute("UPDATE progress_lease SET expires_ts=0 WHERE name='progress'")
                conn.commit()
            finally:
                conn.close()
            takeover = second.acquire()
            self.assertEqual(takeover["fence"], 2)
            self.assertTrue(events_of(second, "lease_stolen"))
            with self.assertRaises(progress.FenceLost):
                first.append("note", {"what": "旧 fence 还想写"})
            with self.assertRaises(progress.FenceLost):
                first.renew()
            # 在岗者的写入照旧成立
            second.append("note", {"what": "在岗者写一条"})
            self.assertEqual(second.lease()["fence"], 2)
            second.release()
            third = progress.Progress(root, spec, owner="owner-unit", holder="holder-C",
                                      goal={"state": "complete", "coverage": "none"})
            self.assertEqual(third.acquire()["fence"], 3)
            third.release()

    def test_same_holder_reacquire_keeps_fence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec = make_spec([{"key": "flag", "kind": "file",
                               "path": str(root / "flag.txt"), "aspect": "exists"}])
            instance = progress.Progress(root, spec, owner="owner-unit", holder="holder-A",
                                         goal={"state": "complete", "coverage": "none"})
            self.assertEqual(instance.acquire()["fence"], 1)
            self.assertEqual(instance.acquire()["fence"], 1)
            instance.release()


# ---------------------------------------------------------------------------
# 事件账：只追加 + 可定位 + 篡改可判
# ---------------------------------------------------------------------------


class LedgerTests(unittest.TestCase):
    def test_events_are_append_only_locatable_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "n.txt"
            data.write_text("1\n", encoding="utf-8")
            spec = make_spec([{"key": "n", "kind": "cmd", "argv": counter_argv(data), "pick": "n"}],
                             [{"key": "n", "op": "increased_by", "value": 1}])
            instance = progress.Progress(root, spec, owner="owner-unit",
                                         goal={"state": "complete", "coverage": "none"})
            instance.probe()
            data.write_text("1\n2\n", encoding="utf-8")
            instance.probe()
            events = instance.events()
            self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
            self.assertEqual(events[0]["locator"], "event:1")
            self.assertEqual(events[0]["prev_sha"], progress.GENESIS)
            for previous, current in zip(events, events[1:]):
                self.assertEqual(current["prev_sha"], previous["sha256"])
            # 只追加：UPDATE / DELETE 被触发器拒
            conn = sqlite3.connect(instance.db_path)
            try:
                with self.assertRaises(sqlite3.Error):
                    conn.execute("UPDATE progress_events SET kind='note' WHERE seq=1")
                with self.assertRaises(sqlite3.Error):
                    conn.execute("DELETE FROM progress_events WHERE seq=1")
                # 手工拆触发器改写 ⇒ 读路径按指纹拒（可判的损坏，不是通过）
                conn.execute("DROP TRIGGER progress_events_no_update")
                conn.execute("UPDATE progress_events SET payload_json='{\"tampered\":true}' WHERE seq=1")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(progress.ProgressError):
                instance.events()


# ---------------------------------------------------------------------------
# CLI 与受管监视
# ---------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def _spec(self, root, *, window=600):
        return make_spec([{"key": "flag", "kind": "file",
                           "path": str(root / "flag.txt"), "aspect": "exists"}],
                         [{"key": "flag", "op": "appeared"}],
                         quiet={"window_sec": window, "max_window_sec": 1200})

    @staticmethod
    def _goal(root):
        path = Path(root) / "goal.json"
        path.write_text(json.dumps({"state": "complete", "coverage": "none",
                                    "why": "本轮由外部推进器补足"}), encoding="utf-8")
        return path

    def test_probe_cli_exit_codes_and_watch_subprocess_exit_on_event(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = write_spec(root, self._spec(root))
            goal = self._goal(root)
            first = run_cli(["--root", str(root), "--spec", str(path), "probe"])
            self.assertEqual(first.returncode, 0, first.stderr)
            payload = json.loads(first.stdout)
            self.assertTrue(payload["baseline"])

            (root / "flag.txt").write_text("x", encoding="utf-8")
            second = run_cli(["--root", str(root), "--spec", str(path), "probe"])
            self.assertEqual(second.returncode, 3, second.stderr)
            self.assertEqual(json.loads(second.stdout)["changes"][0]["op"], "appeared")

            watched = run_cli(["--root", str(root), "--spec", str(path), "watch",
                               "--owner", "owner-unit", "--goal-json", str(goal),
                               "--poll", "0.05", "--max-rounds", "3"])
            self.assertEqual(watched.returncode, 0, watched.stderr)
            result = json.loads(watched.stdout)
            self.assertEqual(result["verdict"], "wake")
            self.assertEqual(result["intended_exit"], 0)
            self.assertEqual(result["actual_exit"], 0)
            self.assertFalse(result["host_woken"])
            self.assertTrue(result["requires_host_notification"])
            self.assertFalse(result["self_restart"])
            self.assertTrue(result["needs_rearm"]["value"])
            self.assertIn("显式", result["next"])

            record_path = root / ".state" / "progress" / "runs" / f"{result['job_id']}.json"
            self.assertTrue(record_path.is_file(), record_path)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["verdict"], "wake")
            self.assertEqual(record["intended_exit"], 0)
            self.assertEqual(record["actual_exit"], 0)
            self.assertFalse(record["host_woken"])
            self.assertIn("exit_locator", record)
            # 真值落账：wake 与 watch_exit 都在事件账里（退出码不可信也不能掩盖判词）
            instance = progress.Progress(root, self._spec(root), owner="owner-unit")
            kinds = [event["kind"] for event in instance.events()]
            self.assertIn("wake", kinds)
            self.assertIn("watch_exit", kinds)
            wake = events_of(instance, "wake")[-1]
            self.assertEqual(wake["payload"]["intended_exit"], 0)
            exit_event = events_of(instance, "watch_exit")[-1]
            self.assertEqual(exit_event["payload"]["actual_exit"], 0)

    def test_watch_blocks_without_event_and_wakes_on_later_change(self):
        """真正的阻塞语义：没有事件时不退出；事件出现后按结构化判词退出。"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = write_spec(root, self._spec(root))
            goal = self._goal(root)
            baseline = run_cli(["--root", str(root), "--spec", str(path), "probe"])
            self.assertEqual(baseline.returncode, 0, baseline.stderr)

            process = None
            out_path = root / "watch-stdout.bin"
            err_path = root / "watch-stderr.bin"
            with out_path.open("wb") as out, err_path.open("wb") as err:
                process = subprocess.Popen(
                    [sys.executable, "-B", "-X", "utf8", str(CLI), "--root", str(root),
                     "--spec", str(path), "watch", "--owner", "owner-unit",
                     "--goal-json", str(goal), "--poll", "0.05"],
                    cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=err)
                try:
                    time.sleep(0.8)
                    self.assertIsNone(process.poll(),
                                      "watch 不该在没有事件时退出（也不是固定寿命作业）")
                    (root / "flag.txt").write_text("x", encoding="utf-8")
                    process.wait(timeout=60)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            stdout = out_path.read_text(encoding="utf-8", errors="replace")
            stderr = err_path.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(process.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["verdict"], "wake")
            self.assertEqual(result["actual_exit"], 0)
            self.assertFalse(result["host_woken"])
            self.assertIn("叫不醒宿主", result["host_notice"])
            self.assertGreaterEqual(result["rounds"], 2)

    def test_watch_refuses_second_job_until_rearm_then_cap_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = write_spec(root, self._spec(root))
            goal = self._goal(root)
            run_cli(["--root", str(root), "--spec", str(path), "probe"])
            (root / "flag.txt").write_text("x", encoding="utf-8")
            first = run_cli(["--root", str(root), "--spec", str(path), "watch",
                             "--owner", "owner-unit", "--goal-json", str(goal),
                             "--poll", "0.05"])
            self.assertEqual(first.returncode, 0, first.stderr)

            refused = run_cli(["--root", str(root), "--spec", str(path), "watch",
                               "--owner", "owner-unit", "--goal-json", str(goal),
                               "--poll", "0.05", "--max-rounds", "1"])
            self.assertEqual(refused.returncode, 7, refused.stderr)
            self.assertEqual(json.loads(refused.stdout)["verdict"], "pending_rearm")

            outcome = run_cli(["--root", str(root), "--spec", str(path), "outcome",
                               "--kind", "nothing", "--next-condition", "等下一个产物文件"])
            self.assertEqual(outcome.returncode, 0, outcome.stderr)
            rearm = run_cli(["--root", str(root), "--spec", str(path), "rearm",
                             "--owner", "owner-unit", "--note", "记了无事，重挂"])
            self.assertEqual(rearm.returncode, 0, rearm.stderr)
            self.assertEqual(json.loads(rearm.stdout)["generation"], 1)

            capped = run_cli(["--root", str(root), "--spec", str(path), "watch",
                              "--owner", "owner-unit", "--goal-json", str(goal),
                              "--poll", "0.05", "--max-rounds", "1"])
            self.assertEqual(capped.returncode, 5, capped.stderr)
            capped_result = json.loads(capped.stdout)
            self.assertEqual(capped_result["verdict"], "exhausted")
            self.assertIn("没有唤醒", capped_result["reason"])
            self.assertTrue(capped_result["needs_rearm"]["value"])
            # 到达上限也必须留结构化判词与预期退出码
            record = json.loads((root / ".state" / "progress" / "runs" /
                                 f"{capped_result['job_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(record["verdict"], "exhausted")
            self.assertEqual(record["intended_exit"], 5)
            self.assertEqual(record["actual_exit"], 5)

    def test_status_cli_reports_blockers_instead_of_fake_green(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = write_spec(root, self._spec(root))
            run_cli(["--root", str(root), "--spec", str(path), "probe"])
            status = run_cli(["--root", str(root), "--spec", str(path), "status"])
            self.assertEqual(status.returncode, 9, status.stderr)  # 未接 goal ⇒ 不可判
            payload = json.loads(status.stdout)
            self.assertFalse(payload["ok"])
            self.assertIn("goal_state_unknown", payload["blockers"])
            self.assertIn("watch_contract", payload)
            self.assertFalse(payload["watch_contract"]["host_woken"])


if __name__ == "__main__":
    unittest.main()
