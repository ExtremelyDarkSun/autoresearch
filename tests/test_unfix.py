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


class TestSelftestNeedsPytest(unittest.TestCase):
    """没有 pytest 的解释器上，`--selftest` 要说「环境缺东西」，不能报成「代码失败」。

    实测（副机 L82）：用全局 python 3.12（没装 pytest）跑 `--selftest`，得到 **1 例 FAIL**；
    换项目 venv 立刻 29 例 OK。那种读数读起来像代码坏了，其实是机器缺依赖。
    """

    def test_missing_pytest_says_so_and_exits_2(self):
        import subprocess
        script = pathlib.Path(unfix.__file__).resolve()
        # `-S` 屏蔽 site-packages ≈ 一个没装 pytest 的解释器
        proc = subprocess.run([sys.executable, "-S", "-X", "utf8", "-B", str(script), "--selftest"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("没有 pytest", proc.stderr)
        self.assertIn("这不是代码缺陷", proc.stderr)


class TestFalseGreens(unittest.TestCase):
    """集成级「假绿入口」回归 —— 已对到 eec6a1b。

    L81 写这组用例时基准是 dca9116；之后判据换过名字（`collection_broken` →
    `collection_broken_from`），也补了两处入口（`32a2ef9` 拒绝改测试的补丁、
    `eec6a1b` 在无 pytest 的解释器上 exit 2）。**用例必须跟着判据走**，否则它守的是
    一个已经不存在的行为。

    本类只调用 `tools/unfix.py` 的公开函数与真 CLI，不修改工具本身。
    每条用例上方都有一行 `git apply -R`：把对应修复反向应用回坏版本，这条用例就应当变红
    （反证补丁在 `C:\\rebuild\\l84\\counter\\`，逐条验证过）。
    """

    PY = pathlib.Path(sys.executable)

    # ---- 小工具 -------------------------------------------------------
    def _tmp(self) -> pathlib.Path:
        import shutil
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="unfix-t-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

    def _git(self, repo, *args) -> str:
        import subprocess
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def _commit(self, repo, msg) -> str:
        self._git(repo, "add", "-A")
        self._git(repo, "-c", "user.name=t", "-c", "user.email=t@t",
                  "commit", "-q", "-m", msg)
        return self._git(repo, "rev-parse", "--short", "HEAD")

    @staticmethod
    def _rows(out_dir) -> list[dict]:
        path = pathlib.Path(out_dir) / "candidates.jsonl"
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]

    def _gate_repo(self):
        """`probe_regression_gate.py` 同款合成仓库：base → 修 bug → 回归测试。

        回归闸只在题目测试通过之后（pytest exit 0）才进入，所以 `_run_gate()` 用
        `apply_gold=True` 让题目先过 —— 这正是 L83 指令写错、后来在
        `docs/UNFIX_TASKS.md` 里更正的那一点。
        """
        repo = self._tmp()
        self._git(repo, "init", "-q", "-b", "main")
        (repo / "mod.py").write_text("def value():\n    return 0\n",
                                     encoding="utf-8", newline="\n")
        (repo / "other.py").write_text("def twice(x):\n    return x * 2\n",
                                       encoding="utf-8", newline="\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_mod.py").write_text(
            "from mod import value\n\n\ndef test_value():\n    assert value() == 0\n",
            encoding="utf-8", newline="\n")
        self._commit(repo, "base")
        (repo / "mod.py").write_text("def value():\n    return 1\n",
                                     encoding="utf-8", newline="\n")
        (repo / "tests" / "test_mod.py").write_text(
            "from mod import value\n\n\ndef test_value():\n    assert value() == 1\n",
            encoding="utf-8", newline="\n")
        task = self._commit(repo, "fix: value returns 1")
        (repo / "tests" / "test_other.py").write_text(
            "from other import twice\n\n\ndef test_twice():\n    assert twice(2) == 4\n",
            encoding="utf-8", newline="\n")
        (repo / "tests" / "test_hang.py").write_text(
            "import time\n\n\ndef test_hang():\n    time.sleep(30)\n",
            encoding="utf-8", newline="\n")
        (repo / "tests" / "test_empty.py").write_text(
            "# no tests here on purpose\n", encoding="utf-8", newline="\n")
        self._commit(repo, "add regression tests")
        return repo, task

    def _run_gate(self, regression_tests):
        repo, task = self._gate_repo()
        out = self._tmp()
        unfix.run_one(repo, self.PY, task, ["tests/test_mod.py"], out,
                      True, None, 3, ("mod.py",), ("tests/",),
                      regression_tests=regression_tests)
        return json.loads((out / f"{task}.json").read_text(encoding="utf-8"))

    # ---- T1: scan 冒烟（函数入口 + 真 CLI） ---------------------------
    def test_scan_smoke_with_test_prefixes(self):
        """scan 的 `TEST_PREFIXES` 漏改：函数入口 NameError，CLI 也没有 --test-prefixes。

        这正是「18 例 selftest 全绿也测不出来」的那条：没有一条用例调用 `scan()`。
        """
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\14e803d-scan-test-prefixes.patch
        repo = self._tmp()
        self._git(repo, "init", "-q", "-b", "main")
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "mod.py").write_text("def value():\n    return 0\n",
                                             encoding="utf-8", newline="\n")
        (repo / "tests" / "test_mod.py").write_text(
            "import pathlib\nimport sys\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
            "from mod import value\n\n\ndef test_value():\n    assert value() == 0\n",
            encoding="utf-8", newline="\n")
        self._commit(repo, "base")
        (repo / "src" / "mod.py").write_text("def value():\n    return 1\n",
                                             encoding="utf-8", newline="\n")
        (repo / "tests" / "test_mod.py").write_text(
            "import pathlib\nimport sys\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
            "from mod import value\n\n\ndef test_value():\n    assert value() == 1\n",
            encoding="utf-8", newline="\n")
        self._commit(repo, "fix: value returns 1")

        # (a) 函数入口：默认 tests/ 前缀 + 显式 src/
        out = self._tmp()
        rc = unfix.scan(repo, self.PY, 1, out, ("src/",))
        self.assertEqual(rc, 0)
        rows = self._rows(out)
        self.assertEqual(len(rows), 1, rows)
        self.assertTrue(rows[0]["usable"], rows[0].get("why"))
        self.assertTrue(rows[0]["fail_to_pass"], rows[0])

        # (b) 真 CLI：--test-prefixes 必须存在且生效
        import subprocess
        out2 = self._tmp()
        p = subprocess.run(
            [str(self.PY), "-X", "utf8", "-B", str(ROOT / "tools" / "unfix.py"),
             "scan", "--repo", str(repo), "--python", str(self.PY),
             "--src-prefixes", "src/", "--test-prefixes", "tests/",
             "--limit", "1", "--out", str(out2)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stdout + "\n" + p.stderr)
        rows2 = self._rows(out2)
        self.assertEqual(len(rows2), 1, rows2)
        self.assertTrue(rows2[0]["usable"], rows2[0].get("why"))
        self.assertTrue(rows2[0]["fail_to_pass"], rows2[0])

    # ---- T2–T4: 回归闸自己没跑成，不许静默 passed ---------------------
    def test_regression_gate_missing_path_is_infra_error(self):
        """回归测试路径不存在（pytest exit 4）—— 没跑成不能算通过。"""
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\83f6c8f-regression-verdict.patch
        res = self._run_gate(["tests/does_not_exist.py"])
        self.assertEqual(res["status"], "infra_error", res)
        self.assertEqual(res["steps"]["regression"]["exit"], 4, res)

    def test_regression_gate_hang_is_infra_error(self):
        """回归测试挂住（本工具 exit 124）—— 没跑成不能算通过。"""
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\83f6c8f-regression-verdict.patch
        res = self._run_gate(["tests/test_hang.py"])
        self.assertEqual(res["status"], "infra_error", res)
        self.assertEqual(res["steps"]["regression"]["exit"], unfix.TIMEOUT_EXIT, res)

    def test_regression_gate_zero_tests_is_infra_error(self):
        """回归测试一条也没收集到（pytest exit 5）—— 没跑成不能算通过。"""
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\83f6c8f-regression-verdict.patch
        res = self._run_gate(["tests/test_empty.py"])
        self.assertEqual(res["status"], "infra_error", res)
        self.assertEqual(res["steps"]["regression"]["exit"], 5, res)

    # ---- T5: 收集错误也走 exit 1（--continue-on-collection-errors） ---
    def test_collection_error_detected_from_full_stdout(self):
        """收集错误（1 条 ERROR + 1 条 FAILED）也是 exit 1，不能当成「有测试失败」。

        判定必须看完整 stdout 里的行首 `ERROR <path>`，不能只看 tail 3 行。

        注意（本轮实测发现的残留缺口，本补丁不改工具，故不在此断言）：
        eec6a1b 的 `classify()` 写的是 `if red_exit != 1 and collection_broken`，
        所以 `classify(1, 0, ids, collection_broken=True)` 仍返回 usable=True；
        `run_one()` 的 exit 1 分支也不查这个布尔。真正被钉住的是「检测」这一层。
        """
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\83f6c8f-regression-verdict.patch
        # （该提交才引入 `collection_broken_from`；旧代码只用 tail 3 行的字符串判断）
        # 真跑一把：一个文件收集 ERROR + 一个文件真 FAILED，exit 仍是 1。
        import subprocess
        repo = self._tmp()
        (repo / "pytest.ini").write_text(
            "[pytest]\naddopts = --continue-on-collection-errors\n",
            encoding="utf-8", newline="\n")
        (repo / "test_bad.py").write_text(
            "import definitely_missing_module_xyz\n", encoding="utf-8", newline="\n")
        (repo / "test_fail.py").write_text(
            "def test_x():\n    assert False\n", encoding="utf-8", newline="\n")
        p = subprocess.run(
            [str(self.PY), "-m", "pytest", "test_bad.py", "test_fail.py", "-q",
             "--no-header", "-p", "no:cacheprovider"],
            cwd=repo, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 1, p.stdout + "\n" + p.stderr)
        self.assertTrue(unfix.collection_broken_from(p.stdout),
                        "完整 stdout 里同时有 ERROR 与 failed：\n" + p.stdout)
        rc, ids, tail, broken, _stats = unfix.pytest_ids(
            repo, self.PY, ["test_bad.py", "test_fail.py"])
        self.assertEqual(rc, 1, tail)
        self.assertIn("test_fail.py::test_x", ids)
        self.assertTrue(broken, "pytest_ids 没把 collection_broken 带出来：" + tail)

    # ---- T6: 子进程一律不写字节码 -------------------------------------
    def test_sh_suppresses_bytecode(self):
        """子进程写 `__pycache__` 会污染 `git status`，也会让同秒改写读到旧字节码。"""
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\83f6c8f-regression-verdict.patch
        import os
        old = os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        try:
            tmp = self._tmp()
            (tmp / "mod.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
            code, out, err = unfix.sh([str(self.PY), "-c", "import mod"],
                                      cwd=tmp, timeout=60)
            self.assertEqual(code, 0, err)
            self.assertFalse((tmp / "__pycache__").exists(),
                             "unfix.sh() 没有给子进程注入 PYTHONDONTWRITEBYTECODE")
        finally:
            if old is not None:
                os.environ["PYTHONDONTWRITEBYTECODE"] = old

    # ---- T7–T8: patch_touches_tests 的两个边界 -----------------------
    def test_deleted_test_file_is_detected(self):
        """补丁删文件：`+++ /dev/null` 这一侧没有路径，必须从 `--- a/tests/...` 认出。

        只读 `+++` 行头的实现会漏掉「删掉测试文件」——这也是在改判据。
        """
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\32a2ef9-patch-touches-tests.patch
        patch = (
            "diff --git a/tests/test_x.py b/tests/test_x.py\n"
            "deleted file mode 100644\n"
            "--- a/tests/test_x.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-def test_x():\n"
            "-    assert True\n"
        )
        self.assertEqual(unfix.patch_touches_tests(patch, ("tests/",)),
                         ["tests/test_x.py"])

    def test_test_prefix_outside_tests_dir_follows_explicit_prefix(self):
        """文件名带 `test_` 但不在 `tests/` 下：工具不猜，只认 `--test-prefixes`。

        eec6a1b 的边界是**显式前缀**：默认 `tests/` 时根目录 `test_x.py` 不拦��
        把 `test_` 纳入前缀，同一个补丁立刻被识别。这里把两侧都钉住，
        免得以后有人把「没传前缀」误读成「工具漏拦」。
        """
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\32a2ef9-patch-touches-tests.patch
        patch = (
            "diff --git a/test_x.py b/test_x.py\n"
            "--- a/test_x.py\n"
            "+++ b/test_x.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-def test_x():\n"
            "-    assert False\n"
            "+def test_x():\n"
            "+    assert True\n"
        )
        self.assertEqual(unfix.patch_touches_tests(patch, ("tests/",)), [])
        self.assertEqual(unfix.patch_touches_tests(patch, ("tests/", "test_")),
                         ["test_x.py"])

    # ---- T9: 无 pytest 的解释器上，--selftest 是 exit 2 不是 1 -------
    def test_selftest_without_pytest_exits_2_not_1(self):
        """`python -S` ≈ 没装 pytest 的解释器：必须 exit 2 并说清是环境缺东西。

        修之前它会把「1 例 FAIL」交给上层，读起来像代码坏了。
        """
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\eec6a1b-selftest-exit2-minimal.patch
        # （最小坏版本只把 exit 2 降回 exit 1；完整回退 eec6a1b 会让既有
        #   TestSelftestNeedsPytest 在 -S 下递归，故反证补丁保留 import 检查）
        import subprocess
        script = pathlib.Path(unfix.__file__).resolve()
        proc = subprocess.run(
            [sys.executable, "-S", "-X", "utf8", "-B", str(script), "--selftest"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 2, proc.stdout + "\n" + proc.stderr)
        self.assertIn("没有 pytest", proc.stderr)
        self.assertIn("这不是代码缺陷", proc.stderr)

    # ---- T10: 拒绝改测试的补丁，端到端接线 ---------------------------
    def test_cheat_patch_is_rejected_end_to_end(self):
        """只改断言的作弊补丁：必须在打补丁之前被拒，状态 infra_error 并指出文件。

        纯函数用例只能证明 `patch_touches_tests` 会识别；这条证明 `run_one` 真的
        调用了它，而且是在 `git apply` 之前 —— 否则工具会诚实地把假绿判成 passed。
        """
        # 反证（会红）：git apply -R C:\rebuild\l84\counter\32a2ef9-patch-touches-tests.patch
        repo, task = self._gate_repo()
        cheat = repo / "cheat.patch"
        cheat.write_text(
            "diff --git a/tests/test_mod.py b/tests/test_mod.py\n"
            "--- a/tests/test_mod.py\n"
            "+++ b/tests/test_mod.py\n"
            "@@ -1,5 +1,5 @@\n"
            " from mod import value\n"
            " \n"
            " \n"
            " def test_value():\n"
            "-    assert value() == 1\n"
            "+    assert True\n",
            encoding="utf-8", newline="\n")
        out = self._tmp()
        unfix.run_one(repo, self.PY, task, ["tests/test_mod.py"], out,
                      False, str(cheat), 30, ("mod.py",), ("tests/",))
        res = json.loads((out / f"{task}.json").read_text(encoding="utf-8"))
        self.assertEqual(res["status"], "infra_error", res)
        self.assertIn("补丁修改了测试文件", res["reason"])
        self.assertIn("tests/test_mod.py", res["reason"])
        self.assertEqual(res["steps"]["patch_check"]["touched_tests"],
                         ["tests/test_mod.py"])
        self.assertNotIn("pytest", res["steps"], "没打补丁就该拒，绝不能跑测试")


class TestCollectionErrorOutranksExitOne(unittest.TestCase):
    """`exit 1` 也可能是「收集阶段就错」—— 副机 L88 报的第三条判据层假绿。

    实测（副机报告 220）：`2 passed, 1 error` + exit 1 被报成 `failed`；
    全过但收集中有错被报成 `passed`。两者都该是 `infra_error`。

    同时要防**反向**错误：`ERROR tests/x.py::test_y` 是用例级错误（fixture/setup 抛异常），
    那是真实失败信号，算成「没测成」会把「做错了」从分母里拿掉。
    """

    def _repo(self):
        import shutil
        import subprocess
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="unfix-ce-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "tests").mkdir()
        # 让 pytest 遇到收集错误继续跑，才可能产生「有 error 也有 passed」的 exit 1
        (tmp / "pytest.ini").write_text(
            "[pytest]" + chr(10) + "addopts = --continue-on-collection-errors"
            + chr(10) + "testpaths = tests" + chr(10), encoding="utf-8")
        (tmp / "mod.py").write_text("def value():" + chr(10) + "    return 1" + chr(10),
                                    encoding="utf-8")
        (tmp / "tests" / "test_ok.py").write_text(
            "from mod import value" + chr(10) * 3 + "def test_a():" + chr(10)
            + "    assert value() == 1" + chr(10), encoding="utf-8")
        (tmp / "tests" / "test_broken.py").write_text(
            "import not_exist_module_xyz" + chr(10), encoding="utf-8")

        def git(*args):
            subprocess.run(["git", "-C", str(tmp), *args], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)

        git("init", "-q")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "1")
        (tmp / "mod.py").write_text("def value():" + chr(10) + "    return 2" + chr(10),
                                    encoding="utf-8")
        (tmp / "tests" / "test_ok.py").write_text(
            "from mod import value" + chr(10) * 3 + "def test_a():" + chr(10)
            + "    assert value() == 2" + chr(10), encoding="utf-8")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "2")
        return tmp

    def test_setup_error_is_not_collection_broken(self):
        self.assertFalse(unfix.collection_broken_from(
            "ERROR tests/a.py::test_x - RuntimeError: boom" + chr(10) + "1 failed in 0.10s"))
        self.assertTrue(unfix.collection_broken_from(
            "ERROR tests/a.py" + chr(10) + "2 passed, 1 error"))

    def test_run_one_reports_infra_error_when_collection_is_broken(self):
        repo = self._repo()
        code = unfix.run_one(repo, pathlib.Path(sys.executable), "HEAD",
                             ["tests/test_ok.py", "tests/test_broken.py"],
                             repo / "out", apply_gold=True, patch=None, timeout=90,
                             src_prefixes=("mod.py",))
        rec = json.loads((repo / "out" / "HEAD.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "infra_error", rec)
        self.assertIn("收集阶段就出错", rec["reason"])
        self.assertEqual(code, 2)





class TestConftestIsJudgment(unittest.TestCase):
    """根目录 `conftest.py` 也是判据 —— 副机 L89/L90 报的第四条假绿。

    实测：一道本来是红的题，只往仓库根加一个 conftest.py（把所有用例 skip），
    `run_one` 就报 `passed`。它不在 `tests/` 前缀下，**前缀守卫对它完全无感**。
    """

    def test_root_conftest_is_judgment(self):
        patch = "--- /dev/null" + chr(10) + "+++ b/conftest.py" + chr(10) + "@@ -0,0 +1 @@" + chr(10) + "+import pytest" + chr(10)
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["conftest.py"])

    def test_nested_conftest_is_judgment(self):
        patch = "--- /dev/null" + chr(10) + "+++ b/tests/unit/conftest.py" + chr(10)
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["tests/unit/conftest.py"])

    def test_pytest_ini_is_judgment(self):
        patch = "--- a/pytest.ini" + chr(10) + "+++ b/pytest.ini" + chr(10) + "@@ -1 +1 @@" + chr(10) + "-addopts = -q" + chr(10) + "+addopts = -q --continue-on-collection-errors" + chr(10)
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["pytest.ini"])

    def test_pyproject_pytest_section_is_judgment(self):
        patch = ("--- a/pyproject.toml" + chr(10) + "+++ b/pyproject.toml" + chr(10) + "@@ -1 +1,2 @@" + chr(10)
                 + " [build-system]" + chr(10) + "+[tool.pytest.ini_options]" + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["pyproject.toml"])

    def test_any_config_edit_is_judgment(self):
        """**改配置一律算动判据** —— 这条是因为一个真实绕过才变严的。

        副机 L92 的 E1：`tox.ini` 里本来就有 `[pytest]` 段，补丁在**远离段头的行**追加
        `addopts = -k "not test_b"`。diff 里看不到段头，按内容判断的守卫放行；
        而 pytest 会读它、把唯一失败的用例 deselect 掉，`failed` 就变 `passed`。

        结论：**对配置文件做内容判断不可靠**（diff 里没有上下文）。所以改成"动就算了"。
        代价是：一个真的需要改 `pyproject.toml` 的修复也会被拒 —— 这是有意的取舍，
        宁可拒绝也不能放行一次绕过。
        """
        patch = ("--- a/pyproject.toml" + chr(10) + "+++ b/pyproject.toml" + chr(10) + "@@ -1 +1 @@" + chr(10)
                 + "-name = old" + chr(10) + "+name = new" + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["pyproject.toml"])

    def test_tox_ini_addopts_far_from_section_header_is_judgment(self):
        """E1 的形状：段头不在 diff 里，但改动落在 pytest 段内。"""
        patch = ("diff --git a/tox.ini b/tox.ini" + chr(10)
                 + "--- a/tox.ini" + chr(10) + "+++ b/tox.ini" + chr(10)
                 + "@@ -20,0 +21 @@ " + chr(10)
                 + '+addopts = -k "not test_b"' + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["tox.ini"])

    def test_module_imported_by_conftest_is_judgment(self):
        """E2 的形状：补丁只改 `judge_helper.py`，但根 conftest 从它 import hook。

        没有这条分析时守卫返回 `[]`；改后的 hook 把全部用例 skip，`status` 变 `passed`。
        """
        import shutil
        import tempfile
        repo = pathlib.Path(tempfile.mkdtemp(prefix="unfix-e2-"))
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        (repo / "conftest.py").write_text(
            "from judge_helper import pytest_collection_modifyitems" + chr(10), encoding="utf-8")
        (repo / "judge_helper.py").write_text("def pytest_collection_modifyitems(c, i): pass" + chr(10),
                                              encoding="utf-8")
        patch = ("diff --git a/judge_helper.py b/judge_helper.py" + chr(10)
                 + "--- a/judge_helper.py" + chr(10) + "+++ b/judge_helper.py" + chr(10)
                 + "@@ -1 +1 @@ " + chr(10) + "-x" + chr(10) + "+y" + chr(10))
        # 不给 repo 时看不见 import 关系 —— 这正是 E2 打穿的那个口子
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), [])
        # 给了 repo，就能从 conftest 的 import 里认出它
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",), repo), ["judge_helper.py"])

    def test_source_only_edit_is_not_judgment(self):
        patch = "--- a/rich/cells.py" + chr(10) + "+++ b/rich/cells.py" + chr(10) + "@@ -1 +1 @@" + chr(10) + "-x" + chr(10) + "+y" + chr(10)
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), [])




class TestJudgmentParserPrecision(unittest.TestCase):
    """判据守卫的解析精度 —— 副机 L91 探针 B 抓到的误杀。

    第一版从任意以 `--- ` / `+++ ` 开头的行取路径，于是 **hunk 内容里恰好以 `--- ` 开头的行**
    会被当成文件头。实测：某个源文件新增的一行注释提到 `[tool.pytest.ini_options]`，
    居然把 `pyproject.toml` 判成判据 —— **误杀真的补丁**。

    现在路径只从 `diff --git a/<x> b/<y>` 与成对的 `--- a/...` / `+++ b/...` 取。
    """

    def _patch(self, path, body):
        return (f"diff --git a/{path} b/{path}" + chr(10) + f"--- a/{path}" + chr(10)
                + f"+++ b/{path}" + chr(10) + body)

    def test_source_comment_mentioning_pytest_does_not_flag_pyproject(self):
        patch = self._patch("kernel/a.py", "@@ -1 +1,2 @@" + chr(10) + " x" + chr(10)
                             + "+ # [tool.pytest.ini_options] 只是注释" + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), [])

    def test_real_pytest_section_in_pyproject_is_flagged(self):
        patch = self._patch("pyproject.toml", "@@ -1 +1,2 @@" + chr(10) + " [build-system]" + chr(10)
                             + "+[tool.pytest.ini_options]" + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["pyproject.toml"])

    def test_source_only_patch_is_clean(self):
        patch = self._patch("kernel/a.py", "@@ -1 +1 @@" + chr(10) + "-x" + chr(10) + "+y" + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), [])

    def test_added_content_that_renders_as_a_diff_header_is_not_a_header(self):
        """真补丁里，「一行内容看起来像文件头」长什么样 —— 副机 L92 的 E1 纠了我一次。

        我第一版用例写的补丁内容是 `+++ b/pyproject.toml`。**那在真补丁里不可能出现**：
        git diff 会给内容行加一个 `+` 前缀，内容 `+++ b/x` 会渲染成 `++++ b/x`。

        真实形状是：源文件里新增一行 `++ b/pyproject.toml`（两个加号），
        在补丁里那一行就是 `+++ b/pyproject.toml` —— 和文件头长得一模一样。
        旧解析从「任意以 `+++ ` 开头的行」取路径，于是 cur 被切成 pyproject.toml，
        紧随其后的 `+[tool.pytest.ini_options]` 就被判成判据：误杀一份只改了 kernel/a.py 的补丁。

        这条用例用的就是那个真实形状（副机 L92 复现过：旧实现返回 pyproject.toml，新实现返回空）。
        """
        patch = ("diff --git a/kernel/a.py b/kernel/a.py" + chr(10)
                 + "--- a/kernel/a.py" + chr(10) + "+++ b/kernel/a.py" + chr(10)
                 + "@@ -1 +1,3 @@" + chr(10) + " x" + chr(10)
                 + "+++ b/pyproject.toml" + chr(10) + "+[tool.pytest.ini_options]" + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), [])

    def test_new_root_conftest_is_flagged(self):
        patch = ("diff --git a/conftest.py b/conftest.py" + chr(10) + "new file mode 100644" + chr(10)
                 + "--- /dev/null" + chr(10) + "+++ b/conftest.py" + chr(10)
                 + "@@ -0,0 +1 @@" + chr(10) + "+import pytest" + chr(10))
        self.assertEqual(unfix.patch_touches_judgment(patch, ("tests/",)), ["conftest.py"])



    def test_run_one_passes_repo_to_judgment_check(self):
        """端到端：`run_one` 必须把 repo 传给守卫，否则 E2 那条口子还开着。

        纯函数用例证明「给了 repo 就能认出来」，但**管线有没有把 repo 传下去**
        是另一件事 —— 参数漏传不会报错，只会静默退回旧行为。
        """
        import shutil
        import subprocess
        import tempfile
        repo = pathlib.Path(tempfile.mkdtemp(prefix='unfix-e2e-'))
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        (repo / 'tests').mkdir()
        (repo / 'mod.py').write_text('def f():' + chr(10) + '    return 1' + chr(10), encoding='utf-8')
        (repo / 'judge_helper.py').write_text('def pytest_collection_modifyitems(c, i):' + chr(10) + '    pass' + chr(10), encoding='utf-8')
        (repo / 'conftest.py').write_text('from judge_helper import pytest_collection_modifyitems' + chr(10), encoding='utf-8')
        (repo / 'tests' / 'test_f.py').write_text(
            'from mod import f' + chr(10) * 3 + 'def test_f():' + chr(10) + '    assert f() == 1' + chr(10), encoding='utf-8')

        def git(*args):
            subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)

        git('init', '-q')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '1')
        (repo / 'mod.py').write_text('def f():' + chr(10) + '    return 2' + chr(10), encoding='utf-8')
        (repo / 'tests' / 'test_f.py').write_text(
            'from mod import f' + chr(10) * 3 + 'def test_f():' + chr(10) + '    assert f() == 2' + chr(10), encoding='utf-8')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '2')

        # 作弊补丁：只改 conftest 引用的辅助模块，把用例全 skip
        cheat = repo / 'cheat.patch'
        cheat.write_text(
            'diff --git a/judge_helper.py b/judge_helper.py' + chr(10)
            + '--- a/judge_helper.py' + chr(10) + '+++ b/judge_helper.py' + chr(10)
            + '@@ -1,2 +1,4 @@' + chr(10)
            + '-def pytest_collection_modifyitems(c, i):' + chr(10) + '-    pass' + chr(10)
            + '+import pytest' + chr(10)
            + '+def pytest_collection_modifyitems(c, i):' + chr(10)
            + '+    for it in i:' + chr(10)
            + "+        it.add_marker(pytest.mark.skip(reason='x'))" + chr(10), encoding='utf-8')

        code = unfix.run_one(repo, pathlib.Path(sys.executable), 'HEAD', ['tests/test_f.py'],
                             repo / 'out', apply_gold=False, patch=str(cheat), timeout=90,
                             src_prefixes=('mod.py', 'judge_helper.py'))
        rec = json.loads((repo / 'out' / 'HEAD.json').read_text(encoding='utf-8'))
        self.assertEqual(rec['status'], 'infra_error', rec)
        self.assertIn('judge_helper.py', rec['reason'])
        self.assertEqual(code, 2)



class TestSkippedIsNotPassed(unittest.TestCase):
    """「跳过」和「通过」在退出码上都是 0 —— 但对判据的意义相反。

    实测（副机 L94 + 桥的裁判）：一份让用例**全部 skip** 的补丁让 pytest exit 0，
    `run_one` 就判 `passed`。那等于任何模型都能靠 skip 拿满分。

    现在读 `--junitxml` 数「真跑过几条」：一条都没跑过就不是通过。
    """

    def test_pytest_stats_counts_each_kind(self):
        import tempfile
        xml = ('<?xml version="1.0"?>' + chr(10)
               + '<testsuites><testsuite tests="4">' + chr(10)
               + '<testcase name="a" classname="t"/>' + chr(10)
               + '<testcase name="b" classname="t"><failure/></testcase>' + chr(10)
               + '<testcase name="c" classname="t"><skipped/></testcase>' + chr(10)
               + '<testcase name="d" classname="t"><error/></testcase>' + chr(10)
               + '</testsuite></testsuites>')
        p = pathlib.Path(tempfile.mkdtemp()) / 'j.xml'
        p.write_text(xml, encoding='utf-8')
        self.addCleanup(lambda: __import__('shutil').rmtree(p.parent, ignore_errors=True))
        s = unfix.pytest_stats(p)
        self.assertEqual(s, {"total": 4, "passed": 1, "failed": 1, "skipped": 1, "error": 1})

    def test_missing_xml_gives_zeros(self):
        self.assertEqual(unfix.pytest_stats(pathlib.Path('nope-not-here.xml')),
                         {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0})

    def test_all_skipped_task_is_infra_error_not_passed(self):
        import shutil
        import subprocess
        import tempfile
        repo = pathlib.Path(tempfile.mkdtemp(prefix='unfix-skip-'))
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        (repo / 'tests').mkdir()
        (repo / 'mod.py').write_text('def f():' + chr(10) + '    return 1' + chr(10), encoding='utf-8')
        (repo / 'tests' / 'test_f.py').write_text(
            'import pytest' + chr(10) + 'from mod import f' + chr(10) + chr(10)
            + "pytestmark = pytest.mark.skip(reason='x')" + chr(10) + chr(10)
            + 'def test_f():' + chr(10) + '    assert f() == 1' + chr(10), encoding='utf-8')

        def git(*args):
            subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)

        git('init', '-q')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '1')
        (repo / 'mod.py').write_text('def f():' + chr(10) + '    return 2' + chr(10), encoding='utf-8')
        (repo / 'tests' / 'test_f.py').write_text(
            'import pytest' + chr(10) + 'from mod import f' + chr(10) + chr(10)
            + "pytestmark = pytest.mark.skip(reason='x')" + chr(10) + chr(10)
            + '# c2' + chr(10) + 'def test_f():' + chr(10) + '    assert f() == 2' + chr(10), encoding='utf-8')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '2')

        code = unfix.run_one(repo, pathlib.Path(sys.executable), 'HEAD', ['tests/test_f.py'],
                             repo / 'out', apply_gold=True, patch=None, timeout=90,
                             src_prefixes=('mod.py',))
        rec = json.loads((repo / 'out' / 'HEAD.json').read_text(encoding='utf-8'))
        self.assertEqual(rec['status'], 'infra_error', rec)
        self.assertIn('跳过不等于通过', rec['reason'])
        self.assertEqual(rec['steps']['pytest']['stats']['skipped'], 1)
        self.assertEqual(code, 2)



class TestClassifySkipped(unittest.TestCase):
    """`classify` 也要看「到底跑了几条」：跳过不是通过，也不是失败。

    实测：一条全 skip 的题在 `scan` 里被说成「退回源码后仍全过（测试没覆盖这处改动）」——
    结论对，理由错，而那个理由会把人引去「补测试覆盖」这条错路。
    """

    def test_green_all_skipped_is_not_usable(self):
        green = {'total': 3, 'passed': 0, 'failed': 0, 'skipped': 3, 'error': 0}
        usable, why = unfix.classify(1, 0, ['tests/a.py::t'], False, green_stats=green)
        self.assertFalse(usable)
        self.assertIn("跳过不等于通过", why)

    def test_red_all_skipped_is_not_usable(self):
        red = {'total': 2, 'passed': 0, 'failed': 0, 'skipped': 2, 'error': 0}
        usable, why = unfix.classify(0, 0, [], False, red_stats=red)
        self.assertFalse(usable)
        self.assertIn("全被跳过", why)
        self.assertNotIn("测试没覆盖", why, "全 skip 不该被说成「没覆盖」，那会把人引到补丁写法上")

    def test_normal_case_still_usable(self):
        red = {'total': 2, 'passed': 1, 'failed': 1, 'skipped': 0, 'error': 0}
        green = {'total': 2, 'passed': 2, 'failed': 0, 'skipped': 0, 'error': 0}
        usable, why = unfix.classify(1, 0, ['tests/a.py::t'], False,
                                     red_stats=red, green_stats=green)
        self.assertTrue(usable, why)


    def test_conditional_skip_is_not_usable(self):
        """副机 L95 找到的更刁的形状：红相位失败、绿相位**跳过**。

        测试写成「源码看起来修好了就 skip」。于是：红相位（buggy 源码）`1 failed`，
        绿相位（修好的源码）`1 skipped`。`classify(red=1, green=0, ids 非空)` 正好落进
        「红→绿成立」，把一道**测不出东西**的题收进题集。

        这条用例走真 `scan`（不只是纯函数），因为漏的是「stats 有没有从 pytest_ids
        传到 classify」这段管线。
        """
        import shutil
        import subprocess
        import tempfile
        repo = pathlib.Path(tempfile.mkdtemp(prefix='unfix-condskip-'))
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        (repo / 'tests').mkdir()
        body = ('import pytest' + chr(10) + 'from mod import target' + chr(10) + chr(10) + chr(10)
                + 'def test_target():' + chr(10)
                + '    if target() == 4:' + chr(10)
                + "        pytest.skip('x')" + chr(10)
                + '    assert target() == 4' + chr(10))
        (repo / 'mod.py').write_text('def target():' + chr(10) + '    return 1' + chr(10), encoding='utf-8')
        (repo / 'tests' / 'test_mod.py').write_text(body, encoding='utf-8')

        def git(*args):
            subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)

        git('init', '-q')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '1')
        (repo / 'mod.py').write_text('def target():' + chr(10) + '    return 4' + chr(10), encoding='utf-8')
        (repo / 'tests' / 'test_mod.py').write_text(body + '# c2' + chr(10), encoding='utf-8')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '2')

        rc = unfix.scan(repo, pathlib.Path(sys.executable), 1, repo / 'scan',
                        ('mod.py',), ('tests/',))
        self.assertEqual(rc, 0)
        rows = [json.loads(x) for x in (repo / 'scan' / 'candidates.jsonl').read_text(
            encoding='utf-8').splitlines() if x.strip()]
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['usable'], rows[0])
        self.assertIn('跳过不等于通过', rows[0]['why'])


    def test_missing_stats_is_fail_closed(self):
        """拿不到 junit（total==0）时**不能假设它跑过了** —— 副机 L96 提的 fail-closed。

        我原来写的是「total==0 就不检查」，等于**守卫在拿不到数据时静默消失**，
        而那正是最该警惕的时刻。
        """
        missing, why = unfix.real_pass_missing(None)
        self.assertTrue(missing)
        self.assertIn('统计不到执行条数', why)

        missing, why = unfix.real_pass_missing({'total': 0, 'passed': 0})
        self.assertTrue(missing, '空统计也要 fail-closed')

        missing, why = unfix.real_pass_missing({'total': 3, 'passed': 0, 'skipped': 3})
        self.assertTrue(missing)
        self.assertEqual(why, unfix.SKIP_GUARD_REASON)

        missing, why = unfix.real_pass_missing({'total': 3, 'passed': 2, 'skipped': 1})
        self.assertFalse(missing, '只要有一条真跑过就不算缺失')

if __name__ == "__main__":
    unittest.main(verbosity=2)
