# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 拦住「文档里的测试计数悄悄过期」——副机 L69 实测发现文档写 1218，实际 1360。
# ORIGIN: none（新文件）。文档与代码不符是这个项目明确要抓的缺口类别之一，
#         而「测试数量」是最容易过期、最没人复查的一个数字：它只增不减，写一次就错。
# CUMULATIVE-DELTA: 首版。用 unittest 的发现计数（只 import、不执行）与文档里的数字对账。
"""文档里的测试计数必须等于实际能发现的测试数。

为什么要专门写个测试：这个数字只增不减，**写进文档那天就开始过期**，而没人会为了核对它
去跑一遍全量自测。副机 L69 顺手量了一次，发现文档写 1218、实际 1360 —— 151 个测试的差距，
也就是说这句话已经错了一段时间没人发现。

这里用 `unittest.TestLoader().discover()` 只做发现、不执行用例，所以很快（1–2 秒）；
它数的是「能被发现的用例数」，与 `selftest` 打印的 `Ran N tests` 是同一个量。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCS = {
    "QUICKSTART": ROOT / "docs" / "QUICKSTART.md",
    "REPRODUCIBILITY": ROOT / "docs" / "REPRODUCIBILITY.md",
}


def discovered_counts() -> tuple[int, int]:
    """返回 (kernel/autoresearch/runtime 的用例数, tests 的用例数)。"""
    loader = unittest.TestLoader()
    kernel = loader.discover(str(ROOT / "kernel" / "autoresearch" / "runtime"))
    tests = unittest.TestLoader().discover(str(ROOT / "tests"))
    return kernel.countTestCases(), tests.countTestCases()


def ran_numbers(text: str) -> list[int]:
    """抽出文档里所有 `Ran N tests` 的 N。"""
    return [int(m) for m in re.findall(r"Ran (\d+) tests", text)]


class TestDocumentedCounts(unittest.TestCase):
    def test_ran_numbers_in_docs_match_reality(self):
        kernel_n, tests_n = discovered_counts()
        expected = {kernel_n, tests_n}
        for name, path in DOCS.items():
            with self.subTest(doc=name):
                text = path.read_text(encoding="utf-8")
                found = set(ran_numbers(text))
                self.assertTrue(found, f"{name} 里没找到任何 `Ran N tests`，文档结构变了就更新这个测试")
                self.assertEqual(
                    found, expected,
                    f"{name} 里写的测试数 {sorted(found)} 与实际 {sorted(expected)} 不符；"
                    f"改了测试就同步文档（kernel={kernel_n} / tests={tests_n}）",
                )

    def test_documented_total_equals_sum(self):
        kernel_n, tests_n = discovered_counts()
        total = kernel_n + tests_n
        text = DOCS["QUICKSTART"].read_text(encoding="utf-8")
        m = re.search(r"合计 \*\*(\d+) 个 Python 测试", text)
        self.assertIsNotNone(m, "QUICKSTART 里的「合计 **N 个 Python 测试」表述不见了")
        self.assertEqual(int(m.group(1)), total,
                         f"合计写 {m.group(1)}，实际 {total}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
