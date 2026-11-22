# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: tools/run_eval.py 的单元测试（全部合成数据 / 临时目录 / 只用标准库）。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。刻意不硬编码任何 verdict —— 每个断言都由被测函数当场产出，
#                   依据是 L41 那次教训：一个把结论写死的验收脚本，跑一万遍也只重复自己的偏见。
"""通用评测执行器的测试。

三条约束（与仓库其它测试一致）：

1. 只用标准库（unittest / json / pathlib / subprocess / tempfile / sys）。
2. 全部合成数据：题号是 `syn-xxx`，命令是 `python -c`，不碰任何真实题库或模型。
3. 现场只读：唯一读到的既有文件是 `tools/run_eval.py` 本身与
   `profiles/agent-harness/schemas/results.schema.json`；写入一律在 tempfile 里。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_eval.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch.adapters import agent_harness as ah  # noqa: E402

PY = sys.executable


def write_jsonl(path: pathlib.Path, rows) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def run_tool(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, "-X", "utf8", "-B", str(TOOL), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


class TestCliContract(unittest.TestCase):
    """命令行契约：缺参数、缺题单、缺命令来源都必须明确失败。"""

    def test_missing_required_args_exits_2(self):
        proc = run_tool()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_missing_command_source_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = write_jsonl(pathlib.Path(tmp) / "t.jsonl", [{"task_id": "syn-001"}])
            proc = run_tool("--tasks", str(tasks), "--out", str(pathlib.Path(tmp) / "o"))
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn("task-cmd", (proc.stdout + proc.stderr).lower())

    def test_bad_tasklist_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = write_jsonl(pathlib.Path(tmp) / "t.jsonl", [
                {"task_id": "syn-001"},
                {"task_id": "syn-001"},          # 重复
                {"repo": "x/y"},                  # 缺 task_id
            ])
            proc = run_tool("--tasks", str(tasks), "--out", str(pathlib.Path(tmp) / "o"),
                            "--per-task-command")
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
            self.assertIn("重复", proc.stderr)

    def test_task_cmd_must_be_json_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = write_jsonl(pathlib.Path(tmp) / "t.jsonl", [{"task_id": "syn-001"}])
            proc = run_tool("--tasks", str(tasks), "--out", str(pathlib.Path(tmp) / "o"),
                            "--task-cmd", "python eval.py")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("JSON", proc.stdout + proc.stderr)


class TestStatusMapping(unittest.TestCase):
    """四态映射必须按实测退出码/超时判定，不允许任何预设。"""

    def _run_cases(self, rows, timeout="2"):
        tmp_ctx = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_ctx.cleanup)
        tmp = pathlib.Path(tmp_ctx.name)
        tasks = write_jsonl(tmp / "t.jsonl", rows)
        out = tmp / "out"
        # 用 --allow-failures 让退出码策略与本类无关：这里只测状态映射。
        # 退出码策略另有 TestExitCodePolicy 专门覆盖。
        proc = run_tool("--tasks", str(tasks), "--out", str(out),
                        "--per-task-command", "--timeout", timeout, "--allow-failures")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return out, read_jsonl(out / "task_results.jsonl")

    def test_exit_zero_is_passed(self):
        _, rows = self._run_cases([{"task_id": "syn-ok", "command": [PY, "-c", "print(1)"]}])
        self.assertEqual([r["status"] for r in rows], ["passed"])

    def test_nonzero_is_failed(self):
        _, rows = self._run_cases([{"task_id": "syn-bad", "command": [PY, "-c", "import sys;sys.exit(9)"]}])
        self.assertEqual([r["status"] for r in rows], ["failed"])
        self.assertIn("9", rows[0]["reason"])

    def test_sleep_past_timeout_is_timed_out(self):
        _, rows = self._run_cases([{"task_id": "syn-slow", "command": [PY, "-c", "import time;time.sleep(6)"]}],
                                  timeout="1")
        self.assertEqual([r["status"] for r in rows], ["timed_out"])

    def test_missing_executable_is_infra_error(self):
        _, rows = self._run_cases([{"task_id": "syn-noexe",
                                    "command": ["definitely-not-a-real-program-e7c1"]}])
        self.assertEqual([r["status"] for r in rows], ["infra_error"])
        self.assertIn("reason", rows[0])

    def test_mixed_batch_counts_are_derived_not_hardcoded(self):
        rows_in = [
            {"task_id": "syn-1", "command": [PY, "-c", "pass"]},
            {"task_id": "syn-2", "command": [PY, "-c", "import sys;sys.exit(1)"]},
            {"task_id": "syn-3", "command": [PY, "-c", "import time;time.sleep(6)"]},
            {"task_id": "syn-4", "command": ["definitely-not-a-real-program-e7c1"]},
        ]
        out, rows = self._run_cases(rows_in, timeout="1")
        statuses = {r["task_id"]: r["status"] for r in rows}
        self.assertEqual(statuses, {
            "syn-1": "passed",
            "syn-2": "failed",
            "syn-3": "timed_out",
            "syn-4": "infra_error",
        })
        meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
        # 计数必须来自实测，而不是写死
        for status, count in meta["status_counts"].items():
            self.assertEqual(count, sum(1 for r in rows if r["status"] == status), status)


class TestOutputCompatibility(unittest.TestCase):
    """产物必须能被仓库自己的 read_results() 读，且 pass_rate 口径正确。"""

    def test_results_readable_by_repo_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            tasks = write_jsonl(tmp / "t.jsonl", [
                {"task_id": "syn-a", "command": [PY, "-c", "pass"]},
                {"task_id": "syn-b", "command": [PY, "-c", "import sys;sys.exit(1)"]},
                {"task_id": "syn-c", "command": [PY, "-c", "import time;time.sleep(6)"]},
            ])
            out = tmp / "out"
            proc = run_tool("--tasks", str(tasks), "--out", str(out),
                            "--per-task-command", "--timeout", "1", "--allow-failures")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            parsed = ah.read_results(str(out / "task_results.jsonl"))
            self.assertEqual(len(parsed["tasks"]), 3)
            self.assertEqual(parsed["summary"]["errors"], [])
            # passed / (passed + failed)；timed_out 不进分母
            self.assertEqual(parsed["summary"]["pass_rate"], 0.5)

    def test_dry_run_executes_nothing_and_writes_no_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            marker = tmp / "ran.marker"
            tasks = write_jsonl(tmp / "t.jsonl", [
                {"task_id": "syn-d", "command": [PY, "-c",
                                                 f"open(r'{marker}','w').write('x')"]},
            ])
            out = tmp / "out"
            proc = run_tool("--tasks", str(tasks), "--out", str(out),
                            "--per-task-command", "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertFalse(marker.exists(), "dry-run 不得真的执行命令")
            self.assertEqual(read_jsonl(out / "task_results.jsonl"), [])
            plan = json.loads((out / "run_plan.json").read_text(encoding="utf-8"))
            self.assertFalse(plan["executed"])
            self.assertEqual(len(plan["plan"]), 1)


class TestPlaceholders(unittest.TestCase):
    """占位符替换必须逐元素进行，不重新分词、不拼 shell。"""

    def test_placeholders_substituted_without_resplitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            tasks = write_jsonl(tmp / "t.jsonl", [
                {"task_id": "syn p h", "repo": "a b/c d", "base_commit": "deadbeef"},
            ])
            out = tmp / "out"
            # 让被测命令把收到的 argv 原样写文件，从而验证没有重新分词
            echo = tmp / "argv.json"
            template = json.dumps([PY, "-c",
                                   "import sys,json,pathlib;"
                                   f"pathlib.Path(r'{echo}').write_text(json.dumps(sys.argv[1:],ensure_ascii=False),encoding='utf-8')",
                                   "{task_id}", "{repo}", "{base_commit}", "{workspace}", "{out}"])
            proc = run_tool("--tasks", str(tasks), "--out", str(out),
                            "--task-cmd", template, "--timeout", "60")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            got = json.loads(echo.read_text(encoding="utf-8"))
            self.assertEqual(got[0], "syn p h", "带空格的 task_id 必须仍是一个 argv 元素")
            self.assertEqual(got[1], "a b/c d", "带空格的 repo 必须仍是一个 argv 元素")
            self.assertEqual(got[2], "deadbeef")
            self.assertIn("syn p h", got[3])   # workspace 路径里含 task_id
            self.assertTrue(got[4])


class TestExitCodePolicy(unittest.TestCase):
    """退出码不许骗人（L41 实测的两个假绿）。

    背景：只要题跑完就 exit 0，会让「上层只看进程退出码」把「全题失败」当成评测成功。
    现在的策略是：有非 passed → exit 1；要「跑完就算成功」显式加 --allow-failures。
    """

    def _setup(self, tmp: pathlib.Path, rows):
        tasks = write_jsonl(tmp / "t.jsonl", rows)
        return tasks

    def test_any_non_passed_exits_1_by_default(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            tasks = self._setup(tmp, [
                {"task_id": "p-1", "command": [PY, "-c", "pass"]},
                {"task_id": "p-2", "command": [PY, "-c", "import sys;sys.exit(1)"]},
            ])
            proc = run_tool("--tasks", str(tasks), "--out", str(tmp / "o"), "--per-task-command")
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("非 passed", proc.stderr)

    def test_allow_failures_restores_0(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            tasks = self._setup(tmp, [
                {"task_id": "p-1", "command": [PY, "-c", "import sys;sys.exit(2)"]},
            ])
            proc = run_tool("--tasks", str(tasks), "--out", str(tmp / "o"),
                            "--per-task-command", "--allow-failures")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_all_passed_exits_0(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            tasks = self._setup(tmp, [{"task_id": "p-1", "command": [PY, "-c", "pass"]}])
            proc = run_tool("--tasks", str(tasks), "--out", str(tmp / "o"), "--per-task-command")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_timeout_must_be_positive_finite(self):
        """--timeout nan/inf/0/-1/1e999 必须在参数解析阶段就被挡掉，且不留产物。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            tasks = self._setup(tmp, [{"task_id": "p-1", "command": [PY, "-c", "pass"]}])
            for value in ("nan", "inf", "0", "-1", "1e999"):
                with self.subTest(timeout=value):
                    out = tmp / f"o-{value.replace('-', 'neg').replace('.', '_')}"
                    proc = run_tool("--tasks", str(tasks), "--out", str(out),
                                    "--per-task-command", "--timeout", value)
                    self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                    self.assertNotIn("Traceback", proc.stderr)
                    self.assertFalse(out.exists(), f"--timeout {value} 不该创建输出目录")

    def test_dry_run_writes_no_results_file(self):
        """L41 实测：dry-run 曾生成 0 字节 task_results.jsonl，只看文件存在会误读。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            tasks = self._setup(tmp, [{"task_id": "p-1", "command": [PY, "-c", "pass"]}])
            out = tmp / "o"
            proc = run_tool("--tasks", str(tasks), "--out", str(out),
                            "--per-task-command", "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertFalse((out / "task_results.jsonl").exists())
            self.assertTrue((out / "run_plan.json").is_file())

    def test_task_id_cannot_escape_output_dir(self):
        """题号会拼进目录名，必须拒绝路径穿越与 Windows 保留名。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            for bad in ("..\\..\\evil", "C:\\abs", "CON", "aux.", "bad<>:?*"):
                with self.subTest(task_id=bad):
                    tasks = write_jsonl(tmp / "bad.jsonl",
                                        [{"task_id": bad, "command": [PY, "-c", "pass"]}])
                    proc = run_tool("--tasks", str(tasks), "--out", str(tmp / "o"),
                                    "--per-task-command")
                    self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
                    self.assertIn("不能当目录名", proc.stderr)


class TestSidecarAlignment(unittest.TestCase):
    """直读路径上的假绿（副机 L58）。

    `read_results()` 只拿到结果文件，看不到题单。所以「题单 2 题、结果写回 1 题」在
    直读路径上会被算成 pass_rate=1.0，被吞掉的题彻底消失。约定由跑评测的一方把题单
    快照写进 sidecar 的 `tasklist_task_ids`，读取端据此判「覆盖不全」。

    另外必须守住「不误杀」：无 sidecar / 字段缺失 / 类型不合法时，summary 不能多出
    任何一个键 —— 否则所有历史结果文件的行为都会被悄悄改掉。
    """

    _BASE_KEYS = {
        "n_tasks", "n_passed", "n_failed", "n_infra_error", "n_timed_out", "n_scored",
        "n_unscored", "pass_rate", "pass_rate_basis", "task_ids", "errors", "notes",
        "source", "measurement",
    }

    def _case(self, tmp: pathlib.Path, name: str, results_rows, sidecar=None):
        out = tmp / name
        out.mkdir(parents=True, exist_ok=True)
        write_jsonl(out / "task_results.jsonl", results_rows)
        if sidecar is not None:
            (out / "run_meta.json").write_text(
                json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
        return ah.read_results(out)["summary"]

    def test_missing_task_nulls_pass_rate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            summary = self._case(
                tmp, "drop",
                [{"task_id": "syn-a", "status": "passed"}],
                {"tasklist_task_ids": ["syn-a", "syn-b"]},
            )
            self.assertIsNone(summary["pass_rate"], "覆盖不全时 pass_rate 必须不可用")
            self.assertEqual(summary["pass_rate_raw"], 1.0, "原值必须留档，不能凭空消失")
            self.assertFalse(summary["aligned"])
            self.assertEqual(summary["alignment"]["missing_task_ids"], ["syn-b"])
            self.assertIn("未对齐", summary["pass_rate_basis"])

    def test_extra_task_nulls_pass_rate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            summary = self._case(
                tmp, "extra",
                [{"task_id": "syn-a", "status": "passed"},
                 {"task_id": "syn-ghost", "status": "passed"}],
                {"tasklist_task_ids": ["syn-a"]},
            )
            self.assertIsNone(summary["pass_rate"])
            self.assertEqual(summary["alignment"]["unexpected_task_ids"], ["syn-ghost"])

    def test_aligned_keeps_pass_rate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            summary = self._case(
                tmp, "ok",
                [{"task_id": "syn-a", "status": "passed"},
                 {"task_id": "syn-b", "status": "failed"}],
                {"tasklist_task_ids": ["syn-b", "syn-a"]},
            )
            self.assertEqual(summary["pass_rate"], 0.5)
            self.assertEqual(summary["pass_rate_raw"], 0.5)
            self.assertTrue(summary["aligned"])
            self.assertEqual(summary["alignment"]["missing_task_ids"], [])

    def test_no_sidecar_adds_no_keys(self):
        """没有 sidecar 时必须与改前**完全一致**：多一个键就是改了所有历史读数。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            summary = self._case(tmp, "plain", [{"task_id": "syn-a", "status": "passed"}])
            self.assertEqual(set(summary) - self._BASE_KEYS, set())
            self.assertEqual(summary["pass_rate"], 1.0)

    def test_invalid_sidecar_field_adds_no_keys(self):
        for bad in ("not-a-list", 123, {}, [], ["", "  "]):
            with self.subTest(value=repr(bad)):
                with tempfile.TemporaryDirectory() as t:
                    tmp = pathlib.Path(t)
                    summary = self._case(
                        tmp, "bad",
                        [{"task_id": "syn-a", "status": "passed"}],
                        {"tasklist_task_ids": bad},
                    )
                    self.assertEqual(set(summary) - self._BASE_KEYS, set())
                    self.assertEqual(summary["pass_rate"], 1.0)

    def test_read_sidecar_false_ignores_snapshot(self):
        """显式 read_sidecar=False 时，连对齐判定也不该发生。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            out = tmp / "off"
            out.mkdir(parents=True, exist_ok=True)
            write_jsonl(out / "task_results.jsonl", [{"task_id": "syn-a", "status": "passed"}])
            (out / "run_meta.json").write_text(
                json.dumps({"tasklist_task_ids": ["syn-a", "syn-b"]}), encoding="utf-8")
            summary = ah.read_results(out, read_sidecar=False)["summary"]
            self.assertEqual(set(summary) - self._BASE_KEYS, set())
            self.assertEqual(summary["pass_rate"], 1.0)

    def test_run_eval_writes_tasklist_snapshot(self):
        """执行器自己必须把题号快照落进 run_meta.json，否则读取端没有依据。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            tasks = write_jsonl(tmp / "t.jsonl", [
                {"task_id": "syn-1", "command": [PY, "-c", "pass"]},
                {"task_id": "syn-2", "command": [PY, "-c", "pass"]},
            ])
            out = tmp / "o"
            proc = run_tool("--tasks", str(tasks), "--out", str(out),
                            "--per-task-command", "--allow-failures")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(meta["tasklist_task_ids"], ["syn-1", "syn-2"])


class TestSelfTest(unittest.TestCase):
    def test_self_test_passes(self):
        proc = run_tool("--self-test")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("SELFTEST=PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
