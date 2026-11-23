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


if __name__ == "__main__":
    unittest.main(verbosity=2)
