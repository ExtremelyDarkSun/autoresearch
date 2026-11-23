# GENERATED-BY: agent implementation, 2026-11-24 +08:00
# LAST-EDIT: 2026-11-24 +08:00
# PURPOSE: 钉住 `tools/unfix.py` 的判据 —— 这套判据在做实验时**连续错三次**，必须有用例守着。
# ORIGIN: none（新文件）。
# CUMULATIVE-DELTA: 首版。
"""`tools/unfix.py` 的判据测试。

为什么单独给一个「分类函数」写测试：在真实仓库上做实验时，这套判据连续错三次，
每次都是把两件不同的事装进同一个桶：

1. pytest 的 `2`（收集错误）与 `1`（测试失败）混在「非零」里 —— 一次埋掉 22 个候选；
2. `failed` 与「进程被杀」混在 `reason` 字段里；
3. 「超时」混着「两边都跑不完」（不能用）与「红挂住绿通过」（**好题**）—— 扔掉 9 道。

三次都发生在判据里，所以它们必须以用例的形式钉住，而不是靠再读一遍代码。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import unfix  # noqa: E402


class TestClassify(unittest.TestCase):
    """`classify(red_exit, green_exit, red_ids, collection_broken) -> (usable, why)`。"""

    def test_standard_red_then_green_is_usable(self):
        usable, why = unfix.classify(1, 0, ["tests/t.py::test_a"])
        self.assertTrue(usable)
        self.assertIn("红→绿成立", why)

    def test_red_timeout_green_pass_is_usable(self):
        """红挂住、绿通过 —— 这个 bug 的表现就是死循环，是好题，不许扔。

        这是第 3 次「一个桶装两件事」的事故：rich 有 9 个提交是这类，
        原先被记成「超时 ⇒ 不可用」。
        """
        usable, why = unfix.classify(unfix.TIMEOUT_EXIT, 0, [])
        self.assertTrue(usable)
        self.assertIn("挂死", why)

    def test_both_timeout_is_not_usable(self):
        usable, why = unfix.classify(unfix.TIMEOUT_EXIT, unfix.TIMEOUT_EXIT, [])
        self.assertFalse(usable)
        self.assertIn("两边都跑不完", why)

    def test_green_timeout_is_not_usable(self):
        usable, why = unfix.classify(1, unfix.TIMEOUT_EXIT, ["tests/t.py::test_a"])
        self.assertFalse(usable)

    def test_red_pass_means_test_does_not_cover_change(self):
        usable, why = unfix.classify(0, 0, [])
        self.assertFalse(usable)
        self.assertIn("没覆盖", why)

    def test_collection_error_is_not_a_failure(self):
        """`2` 是收集中断：测试根本没跑起来，不是「测出失败」。"""
        usable, why = unfix.classify(2, 0, [], collection_broken=True)
        self.assertFalse(usable)
        self.assertIn("收集错误", why)

    def test_other_nonzero_is_not_a_failure(self):
        usable, why = unfix.classify(3, 0, [])
        self.assertFalse(usable)
        self.assertIn("不是「有测试失败」", why)

    def test_red_one_without_ids_is_not_usable(self):
        usable, why = unfix.classify(1, 0, [])
        self.assertFalse(usable)
        self.assertIn("解析不到用例 id", why)

    def test_green_failure_is_env_problem(self):
        usable, why = unfix.classify(1, 1, ["tests/t.py::test_a"])
        self.assertFalse(usable)
        self.assertIn("环境问题", why)


class TestTimeoutExit(unittest.TestCase):
    def test_sh_returns_124_instead_of_raising(self):
        """超时必须变成返回值，不能抛出去 —— 否则一道挂住的题会让整批扫描崩掉。"""
        code, out, err = unfix.sh([sys.executable, "-c", "import time;time.sleep(5)"], timeout=1)
        self.assertEqual(code, unfix.TIMEOUT_EXIT)
        self.assertIn("超过", err)

    def test_timeout_exit_is_not_pytest_code(self):
        self.assertEqual(unfix.TIMEOUT_EXIT, 124)


class TestLayoutPrefixes(unittest.TestCase):
    """源码/测试目录前缀是**假设**，不是事实。

    实测踩到：测试放在根目录的仓库，工具报「该提交没有测试改动」——
    而真正的原因是前缀没匹配。**原因写错了，等于把人引到错的方向。**
    """

    def _repo(self) -> pathlib.Path:
        import subprocess
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp / "test_mod.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
            subprocess.run(argv, cwd=tmp, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return tmp

    def test_default_prefixes_miss_flat_layout(self):
        repo = self._repo()
        src, tests = unfix.changed_files(repo, "HEAD", unfix.DEFAULT_SRC_PREFIXES)
        self.assertEqual(src, [])
        self.assertEqual(tests, [], "根目录的 test_mod.py 不属于默认的 tests/ 前缀")

    def test_explicit_prefixes_pick_it_up(self):
        repo = self._repo()
        src, tests = unfix.changed_files(repo, "HEAD", ("mod.py",), ("test_",))
        self.assertEqual(src, ["mod.py"])
        self.assertEqual(tests, ["test_mod.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
