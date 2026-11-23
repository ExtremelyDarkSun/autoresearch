# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 对抗矩阵探测（tools/gate_probe.py）的回归测试——它自己也得有人验。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。断言 9 个场景全部符合预期、exit 0。
"""对抗矩阵的回归测试。

`gate_probe.py` 就是那个抓到「3 题全 failed 却报 all_passed」的探针。它必须一直能跑、
一直全绿；一旦某条闸门被改松，这里会先红。

注意：它证明的是「当前这 9 个场景按当前期望判定」。要让它真的能发现新缺口，
靠的是**加场景**——每发现一次「我们没想到的合法形状」，就应该往 SCENARIOS 里补一条。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "gate_probe.py"
PY = sys.executable


class TestGateProbe(unittest.TestCase):
    def test_all_scenarios_match_expectation(self):
        proc = subprocess.run(
            [PY, "-X", "utf8", "-B", str(TOOL)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
            stdin=subprocess.DEVNULL,
        )
        blob = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, blob)
        self.assertNotIn("MISMATCH", blob, blob)
        self.assertIn("0 不符", blob, blob)

    def test_known_scenarios_present(self):
        """场景清单不能被悄悄删短——删掉一条就等于少测一种形状。"""
        text = TOOL.read_text(encoding="utf-8")
        for key in ("静默丢最后一题", "0 字节结果文件", "同一题号写两行",
                    "task_id 写成整数", "skipped", "首尾空格", "非 JSON",
                    "全部 failed 但 exit 0"):
            with self.subTest(key=key):
                self.assertIn(key, text, f"场景「{key}」不见了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
