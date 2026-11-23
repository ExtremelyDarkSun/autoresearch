# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# PURPOSE: 钉住 `tools/bench.py` 的汇总口径 —— 两个分母、量具效度、没评的怎么算。
# ORIGIN: none（新文件）。
"""`tools/bench.py` 的汇总口径测试。

为什么单测一个「求和」：**汇总这一步最容易把人误导**。

- 3 道题里 1 道没补丁可评，`pass_rate = passed/scored` 会变成 `2/2 = 1.0`，
  读起来是「全过」，而实际只评了 2 道；
- 反过来 `passed/usable` 会把「没评」和「没做对」混成一个桶。

所以 `summarize()` 必须**两个都输出**，并且把「量具是否成立」（负控红 + 正控绿）单独标出来 ——
量具不成立时，通过率再好看也没有意义。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bench  # noqa: E402


def _task(usable=True, neg="failed", gold="passed", patch="passed"):
    return {"commit": "abc1234", "usable": usable, "neg_status": neg,
            "gold_status": gold, "patch_status": patch}


class TestSummarize(unittest.TestCase):
    def test_empty_task_set_is_not_a_valid_instrument(self):
        s = bench.summarize([])
        self.assertEqual(s["usable"], 0)
        self.assertFalse(s["instrument_ok"], "一道题都没有时不许说量具成立")
        self.assertIsNone(s["pass_rate_scored"])
        self.assertIsNone(s["pass_rate_all"])

    def test_two_denominators_are_both_reported(self):
        tasks = [_task(patch="passed"), _task(patch="failed"), _task(patch="no_patch")]
        s = bench.summarize(tasks)
        self.assertEqual(s["usable"], 3)
        self.assertEqual(s["patch_scored"], 2)
        self.assertEqual(s["patch_passed"], 1)
        self.assertEqual(s["unscored"], 1)
        self.assertEqual(s["pass_rate_scored"], 0.5, "真评过的题里 1/2")
        self.assertEqual(s["pass_rate_all"], 0.3333, "全部可用题里 1/3")

    def test_unscored_only_lowers_the_all_denominator(self):
        tasks = [_task(patch="passed"), _task(patch="no_patch")]
        s = bench.summarize(tasks)
        self.assertEqual(s["pass_rate_scored"], 1.0)
        self.assertEqual(s["pass_rate_all"], 0.5)

    def test_infra_error_counts_as_unscored_not_as_failed(self):
        tasks = [_task(patch="passed"), _task(patch="infra_error")]
        s = bench.summarize(tasks)
        self.assertEqual(s["patch_scored"], 1, "infra_error 不进「真评过」的分母")
        self.assertEqual(s["unscored"], 1)
        self.assertEqual(s["pass_rate_scored"], 1.0)
        self.assertEqual(s["pass_rate_all"], 0.5)

    def test_instrument_broken_when_negative_control_passes(self):
        """负控没红 = 这道题根本测不出差别，不能说量具成立。"""
        tasks = [_task(neg="passed"), _task()]
        s = bench.summarize(tasks)
        self.assertEqual(s["neg_control_failed"], 1, "两条里只有一条红")
        self.assertEqual(s["usable"], 2)
        self.assertFalse(s["instrument_ok"], "只要有一条负控没红，量具就不成立")

    def test_instrument_broken_when_gold_control_fails(self):
        tasks = [_task(gold="failed"), _task()]
        s = bench.summarize(tasks)
        self.assertEqual(s["gold_control_passed"], 1)
        self.assertFalse(s["instrument_ok"])

    def test_unusable_task_is_excluded_from_both_denominators(self):
        tasks = [_task(), _task(usable=False, neg="failed", gold="passed", patch="passed")]
        s = bench.summarize(tasks)
        self.assertEqual(s["tasks_total"], 2)
        self.assertEqual(s["usable"], 1, "不能反解的题不进任何分母")
        self.assertEqual(s["pass_rate_all"], 1.0)


class TestTopDirsHint(unittest.TestCase):
    """「前缀不对」这种报错，光说「不对」没用 —— 得给出「那该填什么」。"""

    def _repo(self, layout):
        import shutil
        import subprocess
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for rel in layout:
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x = 1\n", encoding="utf-8")
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t",
                      "commit", "-q", "-m", "1"]):
            subprocess.run(argv, cwd=tmp, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return tmp

    def test_lists_most_changed_top_dirs(self):
        repo = self._repo(["pkg/a.py", "pkg/b.py", "pkg/c.py", "tests/test_a.py", "docs/x.md"])
        got = bench._top_dirs(repo)
        self.assertEqual(got[0], "pkg", "改动最多的目录应排第一")
        self.assertIn("tests", got)
        self.assertNotIn("docs", got, "非 .py 文件不该进来")

    def test_no_candidates_exits_2_with_a_hint(self):
        import subprocess
        repo = self._repo(["pkg/a.py", "pkg/b.py", "tests/test_a.py"])
        out = repo / "out"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(ROOT / "tools" / "bench.py"),
             "--repo", str(repo), "--python", sys.executable,
             "--limit", "1", "--out", str(out), "--src-prefixes", "nope/",
             "--timeout", "30"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("没有候选", proc.stderr)
        self.assertIn("--src-prefixes", proc.stderr)
        # 报错里要给出这个仓库的实际布局（否则读的人只能猜）
        self.assertIn("pkg/", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
