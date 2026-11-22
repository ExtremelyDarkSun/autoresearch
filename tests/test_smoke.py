# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: tools/smoke.py 的测试——端到端自检本身也要有人验，否则「自检全绿」可能只是它不检查。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。断言七步全 PASS、exit 0、耗时在合理范围，且不改动仓库 tracked 文件。
"""端到端自检脚本的测试。

这里只验「自检这个动作本身」：它必须真的跑完七步、真的全 PASS、真的 exit 0。
**它不能替代反证**——证明 `smoke.py` 会在链条断掉时变红，靠的是主机上做过的一次
先红后绿（撤掉覆盖判定 → 第 4 步 FAIL → 恢复 → 7 PASS），那件事记录在审计文档里，
写不成测试（测试里没法可靠地把被测代码改坏再改回来）。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "smoke.py"
PY = sys.executable


class TestSmoke(unittest.TestCase):
    def test_smoke_passes(self):
        started = time.time()
        proc = subprocess.run(
            [PY, "-X", "utf8", "-B", str(TOOL)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
            stdin=subprocess.DEVNULL,
        )
        elapsed = time.time() - started
        blob = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, blob)
        self.assertIn("7 PASS / 0 FAIL", blob, blob)
        self.assertNotIn("[FAIL]", blob, blob)
        # 七步各自都要出现 PASS，避免「总数对但某步被跳过」
        for step in ("1 生成 profile", "2 全通过", "3 有题未通过", "4 覆盖不全",
                     "5 两次读数可比", "6 超时参数边界", "7 仓库未被改动"):
            with self.subTest(step=step):
                self.assertIn(f"[PASS] {step}", blob, blob)
        self.assertLess(elapsed, 300, f"自检太慢（{elapsed:.0f}s），可能哪一步在等超时")

    def test_smoke_leaves_repo_clean(self):
        """自检不能留下脏改动：读数来源必须可信，跑完仓库该是干净的。"""
        before = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                                 "--untracked-files=no"],
                                capture_output=True, text=True, encoding="utf-8")
        subprocess.run([PY, "-X", "utf8", "-B", str(TOOL)], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, stdin=subprocess.DEVNULL)
        after = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                                "--untracked-files=no"],
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(before.stdout.strip(), after.stdout.strip(),
                         "自检改动了仓库 tracked 文件")


if __name__ == "__main__":
    unittest.main(verbosity=2)
