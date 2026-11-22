# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: kernel/autoresearch/evaluation_runner.py 的单元测试（合成 profile + 临时目录）。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。重点验证「配置不合规必须拒绝」与「--force 会留痕」，
#                   以及执行后确实读回了结果（不是只看退出码）。
"""评测运行器（runner 接线）的测试。

被验证的那一截接线，此前在全仓缺失：`build_command()` 只被 print/dump，
没有任何代码把它的输出交给 `subprocess.run`。副机 L41 把这一点验到了行号级别。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import evaluation_runner as er  # noqa: E402

PY = sys.executable
RUN_EVAL = ROOT / "tools" / "run_eval.py"
PROFILE_DIR = ROOT / "profiles" / "agent-harness"


def build_profile(
    dest: pathlib.Path,
    *,
    command: list[str] | None = None,
    configured: bool = True,
    limits: dict | None = None,
) -> pathlib.Path:
    """在 dest 造一份最小 profile；把 RULES.md 等既有材料复制过去以通过校验。"""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("RULES.md", "README.md", "EXTRACTION.md"):
        src = PROFILE_DIR / name
        if src.is_file():
            (dest / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    for sub in ("schemas", "examples", "checks"):
        src = PROFILE_DIR / sub
        if src.is_dir():
            for item in src.rglob("*"):
                if item.is_file():
                    target = dest / sub / item.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(item.read_bytes())

    default_cmd = [
        PY, "-X", "utf8", "-B", str(RUN_EVAL),
        "--tasks", "{tasks}", "--out", "{output}",
        "--per-task-command", "--allow-failures",
    ]
    lines = [
        'id = "agent-harness"',
        "version = 1",
        "",
        "[evaluation]",
        f"configured = {'true' if configured else 'false'}",
        'result_file = "task_results.jsonl"',
        'trace_dir = "traces"',
        "result_schema_version = 1",
        "command = [" + ", ".join(json.dumps(x) for x in (command if command is not None else default_cmd)) + "]",
        "",
        "[limits]",
    ]
    for key, value in (limits if limits is not None else {
        "per_task_minutes": 10, "max_tool_calls": 20,
        "max_turns": 30, "command_timeout_seconds": 60,
    }).items():
        lines.append(f"{key} = {json.dumps(value)}")
    lines += ["", "[instructions]", 'required = ["RULES.md"]', ""]
    path = dest / "profile.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_tasks(path: pathlib.Path, rows) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


class TestRefusesWhenNotConfigured(unittest.TestCase):
    def test_unconfigured_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p", configured=False)
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "x-1", "command": [PY, "-c", "pass"]}])
            with self.assertRaises(er.EvaluationRunError) as ctx:
                er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))
            self.assertIn("配置不合规", str(ctx.exception))

    def test_empty_command_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p", command=[])
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "x-1", "command": [PY, "-c", "pass"]}])
            with self.assertRaises(er.EvaluationRunError):
                er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))

    def test_missing_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p", limits={"per_task_minutes": 10})
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "x-1", "command": [PY, "-c", "pass"]}])
            with self.assertRaises(er.EvaluationRunError) as ctx:
                er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))
            self.assertIn("limits", str(ctx.exception))


class TestExecutesAndReadsBack(unittest.TestCase):
    def test_successful_run_returns_results(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p")
            tasks = write_tasks(tmp / "t.jsonl", [
                {"task_id": "y-1", "command": [PY, "-c", "pass"]},
                {"task_id": "y-2", "command": [PY, "-c", "import sys;sys.exit(1)"]},
            ])
            report = er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=120)
            self.assertTrue(report["executed"])
            self.assertEqual(report["exit"], 0)
            self.assertIsNone(report["results_read_error"])
            self.assertIsNotNone(report["results"])
            self.assertEqual(len(report["results"]["tasks"]), 2)
            statuses = {t2["task_id"]: t2["status"] for t2 in report["results"]["tasks"]}
            self.assertEqual(statuses, {"y-1": "passed", "y-2": "failed"})
            # 通过率口径：passed/(passed+failed)
            self.assertEqual(report["results"]["summary"]["pass_rate"], 0.5)

    def test_missing_results_file_is_reported_not_faked(self):
        """执行器不写结果文件时，必须如实报 results_read_error，不能编造读数。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            # 一个带占位符、但什么都不写的执行器；占位符齐全才不会在 can_run 阶段被拦
            script = (
                "import sys;"
                "print('假装跑完了 tasks=%s out=%s' % (sys.argv[1], sys.argv[2]));"
                "sys.exit(0)"
            )
            profile = build_profile(tmp / "p", command=[PY, "-c", script, "{tasks}", "{output}"])
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "z-1", "command": [PY, "-c", "pass"]}])
            report = er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=60)
            self.assertTrue(report["executed"])
            self.assertEqual(report["exit"], 0)
            self.assertIsNone(report["results"], "没有结果文件就不该有读数")
            self.assertIsNotNone(report["results_read_error"])
            self.assertIn("找不到结果文件", report["results_read_error"])


class TestSilentDropDetection(unittest.TestCase):
    """L54 CONFIRMED-SILENT-DROP：执行器少写/多写题时，不许判 ok=true。

    背景（副机 L54 原文）：题单 2 行、桩只回 1 行，run_meta 自洽地谎称 tasks_total=1，
    而 research.py eval 仍 exit=0 / ok=true / pass_rate=1.0 —— 被吞掉的那题看不出来。
    根因是 ok 只等于「执行器 exit==0」。
    """

    def _run_with_stub(self, tmp: pathlib.Path, task_ids, emitted_ids):
        """用桩执行器只写回 emitted_ids，模拟静默丢题 / 多写题。"""
        stub = tmp / "stub.py"
        stub.write_text(
            "import sys, json, pathlib\n"
            "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "emit = " + repr(list(emitted_ids)) + "\n"
            "with (out / 'task_results.jsonl').open('w', encoding='utf-8', newline='\\n') as fh:\n"
            "    for tid in emit:\n"
            "        fh.write(json.dumps({'task_id': tid, 'status': 'passed'}, ensure_ascii=False) + '\\n')\n"
            "print('pretend done:', len(emit))\n",
            encoding="utf-8",
        )
        profile = build_profile(tmp / "p", command=[
            PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}",
        ])
        tasks = write_tasks(tmp / "t.jsonl", [
            {"task_id": tid, "statement": "x"} for tid in task_ids
        ])
        return er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=60)

    def test_missing_task_is_not_ok(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["a-1", "a-2"], ["a-1"])
            # 执行器自己 exit 0，但这不算 ok —— 少了一题
            self.assertEqual(report["exit"], 0)
            self.assertFalse(report["ok"], "少写一题必须判 ok=false")
            self.assertEqual(report["alignment"]["expected_count"], 2)
            self.assertEqual(report["alignment"]["actual_count"], 1)
            self.assertEqual(report["alignment"]["missing_task_ids"], ["a-2"])
            self.assertIn("缺 1 题", report["alignment_error"])
            self.assertIn("a-2", report["alignment_error"])

    def test_extra_task_is_not_ok(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["b-1"], ["b-1", "b-ghost"])
            self.assertFalse(report["ok"], "多写一题也必须判 ok=false")
            self.assertEqual(report["alignment"]["unexpected_task_ids"], ["b-ghost"])
            self.assertIn("多 1 题", report["alignment_error"])

    def test_aligned_run_is_ok(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["c-1", "c-2"], ["c-1", "c-2"])
            self.assertTrue(report["ok"], "题目对齐且 exit 0 才判 ok=true")
            self.assertIsNone(report["alignment_error"])
            self.assertEqual(report["alignment"]["missing_task_ids"], [])
            self.assertEqual(report["alignment"]["unexpected_task_ids"], [])

    # ---- 第二层假绿（副机 L57）：ok=false 之后 summary.pass_rate 仍是 1.0 ----
    #
    # 只把顶层 ok 改成 false 是不够的：任何拿 report["results"]["summary"] 当读数的
    # 下游（图表、对比、CLI 打印）照样看到 100%。对齐结论必须同时注入 summary。

    def test_misaligned_run_nulls_summary_pass_rate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["d-1", "d-2"], ["d-1"])
            self.assertFalse(report["ok"])
            summary = report["results"]["summary"]
            self.assertIsNone(summary["pass_rate"], "覆盖不全时 summary.pass_rate 必须不可用")
            self.assertEqual(summary["pass_rate_raw"], 1.0, "原值要留档")
            self.assertFalse(summary["aligned"])
            self.assertEqual(summary["alignment"]["missing_task_ids"], ["d-2"])
            self.assertIn("未对齐", summary["pass_rate_basis"])

    def test_aligned_run_keeps_summary_pass_rate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["g-1", "g-2"], ["g-1", "g-2"])
            summary = report["results"]["summary"]
            self.assertEqual(summary["pass_rate"], 1.0)
            self.assertEqual(summary["pass_rate_raw"], 1.0)
            self.assertTrue(summary["aligned"])
            self.assertNotIn("未对齐", summary["pass_rate_basis"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
