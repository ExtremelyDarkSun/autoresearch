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

import json
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


class TestCliSelftest(unittest.TestCase):
    """`--selftest` 是不带子命令的入口 —— 它曾经根本敲不通。

    事故：`add_subparsers(required=True)` 之后再加 `--selftest`，于是
    `unfix.py --selftest` 永远报「缺少 cmd」。而我在 RUNBOOK 里把它写成标准用法，
    **照着自己的文档敲才发现敲不通** —— 文档比代码严，被打回来的是代码。
    """

    def test_selftest_flag_is_accepted_without_subcommand(self):
        import unittest as ut
        calls = {"discover": 0}
        real_discover = ut.TestLoader.discover

        def fake_discover(self, *a, **k):
            calls["discover"] += 1
            return ut.TestSuite()

        ut.TestLoader.discover = fake_discover
        try:
            # 把内部那次 TextTestRunner 的输出吞掉：它会打一行 `Ran 0 tests`，
            # 而那行会被 `research.py selftest` 的计数闸当成一个计数来源。
            import contextlib
            import io
            with contextlib.redirect_stderr(io.StringIO()):
                code = unfix.main(["--selftest"])
        finally:
            ut.TestLoader.discover = real_discover
        self.assertEqual(code, 0, "返回非 0 说明 argparse 又把 --selftest 拦掉了")
        self.assertEqual(calls["discover"], 1, "没有走到跑自测那条分支")

    def test_no_subcommand_prints_help_instead_of_crashing(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = unfix.main([])
        self.assertEqual(code, 2)
        self.assertIn("usage", buf.getvalue())


class TestRegressionGate(unittest.TestCase):
    """回归闸：题目测试过了，不代表没把库弄坏。

    这是本工具的一个**真实假绿入口**（实测）：一个补丁把 `rich/file_proxy.py` 的题修好、
    同时悄悄改坏 `set_cell_size`，不带回归检查时判 `passed`；带上之后判 `failed` 并指出
    `tests/test_cells.py::test_set_cell_size`。
    """

    def test_known_failures_match_across_separators(self):
        """pytest 在 Windows 上打 `tests\\a.py::t`，人和文档写 `tests/a.py::t`。

        不归一化的话每条已知失败都算新增，闸永远红 —— 一个永远红的闸等于没有闸。
        """
        self.assertEqual(unfix.new_failures(["tests\\a.py::t"], ["tests/a.py::t"]), [])
        self.assertEqual(unfix.new_failures(["tests/a.py::t"], ["tests\\a.py::t"]), [])

    def test_truly_new_failure_is_reported(self):
        got = unfix.new_failures(["tests\\a.py::t", "tests\\b.py::u"], ["tests/a.py::t"])
        self.assertEqual(got, ["tests\\b.py::u"])

    def test_empty_known_means_everything_is_new(self):
        self.assertEqual(unfix.new_failures(["tests/a.py::t"], []), ["tests/a.py::t"])
        self.assertEqual(unfix.new_failures([], ["tests/a.py::t"]), [])


class TestScanSmoke(unittest.TestCase):
    """`scan()` 也要有一条冒烟用例 —— 它曾经在 HEAD 上直接崩。

    事故（副机 L80 发现，`dca9116` 之后）：我把 `TEST_PREFIXES` 重命名成
    `DEFAULT_TEST_PREFIXES` 时漏了 `scan()` 里那一行，于是 `unfix.py scan` 抛
    `NameError: name 'TEST_PREFIXES' is not defined`。

    **18 例 selftest 全绿也测不出来，因为没有一条用例调用 `scan()`。**
    这正是「测试只覆盖到你测过的那个入口」。
    """

    def _tiny_repo(self) -> pathlib.Path:
        import subprocess
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_mod.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8")
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "1"]):
            subprocess.run(argv, cwd=tmp, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        (tmp / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        (tmp / "tests" / "test_mod.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n", encoding="utf-8")
        for argv in (["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "2"]):
            subprocess.run(argv, cwd=tmp, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return tmp

    def test_scan_smoke_does_not_crash(self):
        repo = self._tiny_repo()
        out = repo / "out"
        py = pathlib.Path(sys.executable)
        code = unfix.scan(repo, py, 1, out, ("mod.py",), ("tests/",))
        self.assertEqual(code, 0)
        rows = [json.loads(l) for l in (out / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1, "应当验了 1 个候选")
        self.assertTrue(rows[0]["usable"], rows[0]["why"])



class TestRegressionVerdict(unittest.TestCase):
    """回归检查没跑成，等于没查 —— 这时不许说「题目通过了」。

    四种退出码都吃过亏（4 路径不存在 / 124 挂住 / 5 一条没收集到 / 2 收集错误）：
    它们让 FAILED 行为空，new_list 就为空，闸悄悄放过。
    dca9116 之前的实测：status='passed'，而 steps.regression.exit=4。
    """

    def test_clean_run_without_new_failures_keeps_task_verdict(self):
        self.assertEqual(unfix.regression_verdict(0, []), (None, None))
        self.assertEqual(unfix.regression_verdict(1, []), (None, None))

    def test_new_failures_mean_regression(self):
        status, why = unfix.regression_verdict(1, ["tests/b.py::u"])
        self.assertEqual(status, "failed")
        self.assertIn("新增 1 条回归失败", why)
        self.assertIn("tests/b.py::u", why)

    def test_path_not_found_is_infra_error(self):
        status, why = unfix.regression_verdict(4, [])
        self.assertEqual(status, "infra_error")
        self.assertIn("没跑成", why)

    def test_hang_is_infra_error(self):
        status, why = unfix.regression_verdict(unfix.TIMEOUT_EXIT, [])
        self.assertEqual(status, "infra_error")
        self.assertIn("挂住", why)

    def test_zero_collected_is_infra_error(self):
        self.assertEqual(unfix.regression_verdict(5, [])[0], "infra_error")
        self.assertEqual(unfix.regression_verdict(2, [])[0], "infra_error")
        self.assertEqual(unfix.regression_verdict(3, [])[0], "infra_error")


class TestCollectionBroken(unittest.TestCase):
    """exit 1 也可能是「收集阶段就错」—— 一个桶装两件事。"""

    def test_error_line_means_broken(self):
        out = "ERROR tests/test_x.py - ImportError\n\n1 failed, 2 passed in 0.10s\n"
        self.assertTrue(unfix.collection_broken_from(out))

    def test_during_collection_phrase_means_broken(self):
        self.assertTrue(unfix.collection_broken_from(
            "!!!!!!!!!!! Interrupted: 6 errors during collection !!!!!!!!!!!"))

    def test_plain_failures_are_not_broken(self):
        out = "FAILED tests/a.py::test_x - assert False\n1 failed in 0.10s\n"
        self.assertFalse(unfix.collection_broken_from(out))

    def test_empty_output_is_not_broken(self):
        self.assertFalse(unfix.collection_broken_from(""))
        self.assertFalse(unfix.collection_broken_from(None))

    def test_error_word_inside_a_test_name_does_not_count(self):
        """ERROR 必须是行首（^ERROR <path>），不能因为用例名里带 ERROR 就误判。"""
        out = "FAILED tests/a.py::test_ERROR_handling - assert False\n"
        self.assertFalse(unfix.collection_broken_from(out))


class TestPatchTouchesTests(unittest.TestCase):
    """被评测的 agent 改测试 = 改判据 —— 这是评测器最危险的假绿。

    实测：源码仍是修复前，但一份只改断言的补丁让 `pytest` 变成 `4 passed`。
    工具现在直接拒绝这种补丁（`infra_error`），而不是给它一个 `passed`。
    """

    def test_patch_editing_tests_is_detected(self):
        patch = (
            "diff --git a/tests/test_x.py b/tests/test_x.py\n"
            "--- a/tests/test_x.py\n"
            "+++ b/tests/test_x.py\n"
            "@@ -1 +1 @@\n"
            "-    assert f() == 2\n"
            "+    assert True\n"
        )
        self.assertEqual(unfix.patch_touches_tests(patch, ("tests/",)), ["tests/test_x.py"])

    def test_patch_editing_only_source_is_clean(self):
        patch = (
            "diff --git a/rich/cells.py b/rich/cells.py\n"
            "--- a/rich/cells.py\n"
            "+++ b/rich/cells.py\n"
            "@@ -1 +1 @@\n-x\n+y\n"
        )
        self.assertEqual(unfix.patch_touches_tests(patch, ("tests/",)), [])

    def test_dev_null_headers_are_ignored(self):
        patch = "--- /dev/null\n+++ b/rich/new.py\n--- a/old.py\n+++ /dev/null\n"
        self.assertEqual(unfix.patch_touches_tests(patch, ("tests/",)), [])

    def test_custom_prefixes_are_honoured(self):
        patch = "--- a/test_x.py\n+++ b/test_x.py\n"
        self.assertEqual(unfix.patch_touches_tests(patch, ("tests/",)), [])
        self.assertEqual(unfix.patch_touches_tests(patch, ("test_",)), ["test_x.py"])

    def test_empty_patch_is_clean(self):
        self.assertEqual(unfix.patch_touches_tests("", ("tests/",)), [])
        self.assertEqual(unfix.patch_touches_tests(None, ("tests/",)), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)