# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 可复现性矩阵（tools/repro_matrix.py）的回归测试。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。核心断言是「同一台机器连跑两次必须完全一致」——那是跨机比对的前提。
"""可复现性矩阵的测试。

最关键的一条是 `test_matrix_is_stable_on_same_machine`：如果同一台机器连跑两次都不一致，
那两台机器之间的差异就分不清是「环境不同」还是「矩阵本身在抖」，整张表就失去意义。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "repro_matrix.py"
PY = sys.executable

CASE_NAMES = ("profile", "doctor", "make_profile", "smoke", "gate_probe", "demo")


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, "-X", "utf8", "-B", str(TOOL), *args],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1200,
        stdin=subprocess.DEVNULL,
    )


class TestReproMatrix(unittest.TestCase):
    def test_matrix_is_stable_on_same_machine(self):
        proc = run_tool("--self-check")
        blob = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, blob)
        self.assertIn("0 条不稳定", blob, blob)
        self.assertNotIn("!", blob.replace("!!!", ""), blob)

    def test_json_has_all_cases(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "m.json"
            proc = run_tool("--json", str(out))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            names = [c["name"] for c in data["cases"]]
            self.assertEqual(names, list(CASE_NAMES))
            for case in data["cases"]:
                with self.subTest(case=case["name"]):
                    self.assertEqual(len(case["norm_sha"]), 16)
                    self.assertEqual(case["exit"], 0, f"{case['name']} 应当 exit 0")
                    self.assertIn(case["expect"], ("same", "may-differ"))
                    self.assertTrue(case["why"], "每条都要写清为什么这样期望")

    def test_json_omits_normalized_by_default(self):
        """默认不带规范化全文：跨机传的就是这份小 JSON。"""
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "m.json"
            run_tool("--json", str(out))
            data = json.loads(out.read_text(encoding="utf-8"))
            for case in data["cases"]:
                with self.subTest(case=case["name"]):
                    self.assertNotIn("normalized", case)

    def test_keep_normalized_flag(self):
        with tempfile.TemporaryDirectory() as t:
            out = pathlib.Path(t) / "m.json"
            run_tool("--json", str(out), "--keep-normalized")
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(all("normalized" in c for c in data["cases"]),
                            "--keep-normalized 时要保留全文，便于定位差异")


if __name__ == "__main__":
    unittest.main(verbosity=2)
