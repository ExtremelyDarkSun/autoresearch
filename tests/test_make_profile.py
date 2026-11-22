# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: tools/make_profile.py 的测试——生成出来的 profile 必须真的 can_run，不是看起来配好了。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。所有断言都从生成物当场读出来，不硬编码 id 之外的期望值。
"""profile 生成器的测试。

重点不是「文件写出来了」，而是**写出来的东西真的能跑**：
`load_profile` 能读、`validate_profile` 无错、`evaluation_state().can_run` 为 true、
`build_command` 能把占位符换成真路径。少任一条，生成器就该报错退出。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "make_profile.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch.profiles import (  # noqa: E402
    REQUIRED_LIMIT_KEYS,
    build_command,
    evaluation_state,
    load_profile,
    validate_profile,
)

PY = sys.executable


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


class TestGeneratedProfileIsRunnable(unittest.TestCase):
    def test_can_run_is_true(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            profile = load_profile(out / "profile.toml")
            self.assertEqual(validate_profile(profile), [])
            state = evaluation_state(profile)
            self.assertTrue(state["can_run"], state.get("blocking"))
            self.assertTrue(state.get("executor_ok"))

    def test_materials_copied(self):
        """schema / examples / RULES.md 必须一起搬过去，否则 profile 自校验会挂。"""
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            for rel in ("RULES.md",
                        "schemas/tasklist.schema.json",
                        "schemas/results.schema.json",
                        "examples/tasklist.example.jsonl",
                        "checks/cli.py"):
                with self.subTest(rel=rel):
                    self.assertTrue((out / rel).is_file(), f"缺少 {rel}")

    def test_command_points_at_run_eval_with_placeholders(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            profile = load_profile(out / "profile.toml")
            argv = build_command(profile, tasks="T.jsonl", output="O")
            self.assertIn(str(ROOT / "tools" / "run_eval.py"), argv)
            self.assertIn("--per-task-command", argv)
            self.assertIn("T.jsonl", argv, "占位符 {tasks} 必须被替换")
            self.assertIn("O", argv, "占位符 {output} 必须被替换")
            self.assertNotIn("{tasks}", argv)

    def test_per_task_minutes_becomes_executor_timeout(self):
        """预算必须落到执行器参数上，否则它只是一行注释（假装生效的假绿）。"""
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out), "--limits", "per_task_minutes=30")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            profile = load_profile(out / "profile.toml")
            self.assertEqual(profile["limits"]["per_task_minutes"], 30)
            argv = build_command(profile, tasks="T.jsonl", output="O")
            self.assertIn("--timeout", argv)
            self.assertEqual(argv[argv.index("--timeout") + 1], str(30 * 60))

    def test_all_required_limits_present(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            limits = load_profile(out / "profile.toml")["limits"]
            for key in REQUIRED_LIMIT_KEYS:
                with self.subTest(key=key):
                    self.assertIn(key, limits)


class TestRefusals(unittest.TestCase):
    def test_existing_profile_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            self.assertEqual(run_tool("--out", str(out)).returncode, 0)
            before = (out / "profile.toml").read_bytes()
            second = run_tool("--out", str(out))
            self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
            self.assertEqual((out / "profile.toml").read_bytes(), before, "不该覆盖")

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            self.assertEqual(run_tool("--out", str(out)).returncode, 0)
            third = run_tool("--out", str(out), "--force", "--limits", "max_turns=7")
            self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
            self.assertEqual(load_profile(out / "profile.toml")["limits"]["max_turns"], 7)

    def test_bad_limit_key_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out), "--limits", "no_such_key=1")
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((out / "profile.toml").exists())

    def test_non_positive_limit_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out), "--limits", "max_turns=0")
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((out / "profile.toml").exists())

    def test_missing_python_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out), "--python", str(pathlib.Path(t) / "nope.exe"))
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertFalse((out / "profile.toml").exists())


class TestDryRun(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "prof"
            proc = run_tool("--out", str(out), "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertFalse(out.exists(), "--dry-run 不该建任何东西")
            self.assertIn("[evaluation]", proc.stdout)
            self.assertIn("--per-task-command", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
