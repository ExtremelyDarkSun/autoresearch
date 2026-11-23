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

import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bench  # noqa: E402
import study_round  # noqa: E402


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


class TestNoPatchVsInfraError(unittest.TestCase):
    """「没给补丁」和「给了但被拒」都算没评成，但原因完全不同。

    合成一个数会让「一道补丁都没匹配上」（多半是文件名不对）看起来像
    「一道都没做出来」—— 而这两件事对读的人意味着相反的下一步。
    """

    def test_counts_are_split(self):
        tasks = [_task(patch="no_patch"), _task(patch="infra_error"), _task(patch="passed")]
        s = bench.summarize(tasks)
        self.assertEqual(s["no_patch"], 1)
        self.assertEqual(s["patch_infra_error"], 1)
        self.assertEqual(s["unscored"], 2)
        self.assertEqual(s["patch_scored"], 1)

    def test_warns_when_no_patch_file_matches(self):
        import shutil
        import subprocess
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def commit(files, msg):
            for rel, body in files.items():
                p = tmp / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, stdin=subprocess.DEVNULL)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                            "commit", "-q", "-m", msg], cwd=tmp, capture_output=True,
                           stdin=subprocess.DEVNULL)

        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True, stdin=subprocess.DEVNULL)
        commit({"pkg/m.py": "def f():\n    return 1\n",
                "tests/test_m.py": "from pkg.m import f\n\n\ndef test_f():\n    assert f() == 1\n"}, "1")
        commit({"pkg/m.py": "def f():\n    return 2\n",
                "tests/test_m.py": "from pkg.m import f\n\n\ndef test_f():\n    assert f() == 2\n"}, "2")

        empty_patches = tmp / "patches"
        empty_patches.mkdir()
        (empty_patches / "not-the-commit-name.patch").write_text("", encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(ROOT / "tools" / "bench.py"),
             "--repo", str(tmp), "--python", sys.executable, "--limit", "1",
             "--out", str(tmp / "out"), "--src-prefixes", "pkg/",
             "--patch-dir", str(empty_patches), "--timeout", "60"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("没有任何文件匹配", proc.stderr)
        self.assertIn("<commit>.patch", proc.stderr)




class TestBudgetGate(unittest.TestCase):
    """时间预算：到点停手，并且**写出一份部分汇总**。

    为什么要有它：实测一次服务端任务被 14 分钟上限掐掉，一份报告都没留下 ——
    「没做」和「做了被杀」在读数里完全一样。硬跑到底的代价不是慢，是**什么都没有**。
    """

    def test_skipped_tasks_do_not_count_as_usable(self):
        tasks = [dict(_task(), skipped="budget"), _task()]
        s = bench.summarize(tasks)
        self.assertEqual(s["usable"], 1, "因预算跳过的题不进分母")
        self.assertEqual(s["patch_scored"], 1)

    def test_zero_budget_stops_immediately_but_still_writes_a_summary(self):
        import shutil
        import subprocess
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix='bench-budget-'))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def git(*args):
            subprocess.run(['git', '-C', str(tmp), *args], capture_output=True,
                           stdin=subprocess.DEVNULL)

        (tmp / 'pkg').mkdir()
        (tmp / 'tests').mkdir()
        git('init', '-q')
        (tmp / 'pkg' / 'm.py').write_text('def f():' + chr(10) + '    return 1' + chr(10), encoding='utf-8')
        (tmp / 'tests' / 'test_m.py').write_text(
            'from pkg.m import f' + chr(10) * 3 + 'def test_f():' + chr(10) + '    assert f() == 1' + chr(10), encoding='utf-8')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '1')
        (tmp / 'pkg' / 'm.py').write_text('def f():' + chr(10) + '    return 2' + chr(10), encoding='utf-8')
        (tmp / 'tests' / 'test_m.py').write_text(
            'from pkg.m import f' + chr(10) * 3 + 'def test_f():' + chr(10) + '    assert f() == 2' + chr(10), encoding='utf-8')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', '2')

        out = tmp / 'out'
        proc = subprocess.run(
            [sys.executable, '-X', 'utf8', '-B', str(ROOT / 'tools' / 'bench.py'),
             '--repo', str(tmp), '--python', sys.executable, '--limit', '1',
             '--out', str(out), '--src-prefixes', 'pkg/', '--timeout', '60',
             '--budget-seconds', '0'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=300, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((out / 'summary.json').is_file(), '预算用尽也必须写汇总')
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))['summary']
        self.assertTrue(summary["stopped_early"])
        self.assertEqual(summary["skipped_budget"], 1)
        self.assertEqual(summary["usable"], 0)
        self.assertIn("因时间预算停止", (out / "summary.md").read_text(encoding="utf-8"))

class TestStudyRound(unittest.TestCase):
    """一轮完整实验：造题 → 造副本 → 收补丁 → 判分 → 报告。

    三种输入给三种读数（都实测过）：
      没补丁        → pass_rate_scored=None（没评，不是 0 分）
      已知 gold 补丁 → 1.0
      假 solver 补丁 → 0.0
    """

    def _fixture(self, tmp):
        repo = tmp / "repo"
        (repo / "tests").mkdir(parents=True)

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)

        def commit(msg):
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)

        git("init", "-q")
        (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (repo / "tests" / "test_f.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n", encoding="utf-8")
        commit("c1")
        (repo / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        (repo / "tests" / "test_f.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n\n# c2\n", encoding="utf-8")
        commit("c2")
        return repo, git("rev-parse", "--short", "HEAD").stdout.strip()

    def test_round_with_known_patch_scores_100(self):
        import shutil
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="round-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo, sha = self._fixture(tmp)

        patches = tmp / "given"
        patches.mkdir()
        (patches / f"{sha}.patch").write_text(
            "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n"
            "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n", encoding="utf-8")

        import study_round
        rep = study_round.build_round(repo, pathlib.Path(sys.executable), 5, tmp / "round",
                                      ("mod.py",), ("tests/",), patch_dir=patches,
                                      timeout=120.0)
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["patch_scored"], 1, rep)
        self.assertEqual(rep["patch_passed"], 1, rep)
        self.assertEqual(rep["pass_rate_scored"], 1.0, rep)

    def test_round_without_patch_is_not_zero(self):
        import shutil
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="round0-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo, _sha = self._fixture(tmp)
        import study_round
        rep = study_round.build_round(repo, pathlib.Path(sys.executable), 5, tmp / "round",
                                      ("mod.py",), ("tests/",), timeout=120.0)
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["patch_scored"], 0, rep)
        self.assertIsNone(rep["pass_rate_scored"], "没评必须是 None，不是 0.0")
class TestTaskBudgetAndHang(unittest.TestCase):
    """一道挂死的题不该拖垮整轮，也不该把读数搅浑。"""

    def test_neg_hang_skips_remaining_phases(self):
        import shutil
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="hang-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = tmp / "repo"
        (repo / "tests").mkdir(parents=True)

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)

        def commit(msg):
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)

        git("init", "-q")
        # 红相位会挂死：目标用例里 sleep 很久
        (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (repo / "tests" / "test_m.py").write_text(
            "import time\nfrom mod import f\n\n\ndef test_target():\n    time.sleep(30)\n    assert f() == 2\n",
            encoding="utf-8")
        commit("c1")
        (repo / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        (repo / "tests" / "test_m.py").write_text(
            "import time\nfrom mod import f\n\n\ndef test_target():\n    time.sleep(30)\n    assert f() == 2\n\n# c2\n",
            encoding="utf-8")
        commit("c2")

        sha = git("rev-parse", "--short", "HEAD").stdout.strip()
        tasks = tmp / "tasks.jsonl"
        tasks.write_text(
            json.dumps({"task_id": "x", "repo": str(repo), "commit": sha,
                        "test_files": ["tests/test_m.py"],
                        "fail_to_pass": ["tests/test_m.py::test_target"]}) + "\n",
            encoding="utf-8")

        rc = bench.run_bench(repo, pathlib.Path(sys.executable), 1, tmp / "out",
                             ("mod.py",), ("tests/",), timeout=2.0,
                             budget_seconds=120.0, tasks_file=str(tasks),
                             task_budget=20.0)
        self.assertEqual(rc, 0)
        summary = json.loads((tmp / "out" / "summary.json").read_text(encoding="utf-8"))
        task = summary["tasks"][0]
        self.assertEqual(task.get("skipped"), "neg_hang", task)
        self.assertIsNone(task.get("patch_status"), task)
class TestSingleTaskErrorDoesNotKillRound(unittest.TestCase):
    """一道题异常不能炸整轮 —— 跑了半小时什么都没留下是最贵的失败。"""

    def test_unknown_commit_is_recorded_and_round_continues(self):
        import shutil
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="badc-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = tmp / "repo"
        (repo / "tests").mkdir(parents=True)

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)

        git("init", "-q")
        (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "tests" / "test_m.py").write_text("def test_a():\n    assert True\n",
                                                  encoding="utf-8")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c1")
        sha = git("rev-parse", "--short", "HEAD").stdout.strip()

        tasks = tmp / "tasks.jsonl"
        tasks.write_text("\n".join([
            json.dumps({"task_id": "bad", "repo": str(repo), "commit": "deadbeef",
                        "test_files": ["tests/test_m.py"],
                        "fail_to_pass": ["tests/test_m.py::test_a"]}),
            json.dumps({"task_id": "ok", "repo": str(repo), "commit": sha,
                        "test_files": ["tests/test_m.py"],
                        "fail_to_pass": ["tests/test_m.py::test_a"]}),
        ]) + "\n", encoding="utf-8")

        rc = bench.run_bench(repo, pathlib.Path(sys.executable), 2, tmp / "out",
                             ("mod.py",), ("tests/",), timeout=30.0,
                             budget_seconds=120.0, tasks_file=str(tasks),
                             task_budget=60.0)
        self.assertEqual(rc, 0, "整轮不该以崩溃收场")
        summary = json.loads((tmp / "out" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(len(summary["tasks"]), 2, "两道题都要留下记录")
        bad = [t for t in summary["tasks"] if t["commit"] == "deadbeef"][0]
        self.assertEqual(bad.get("skipped"), "error", bad)
        self.assertIn("error", bad, "要把异常原文留在记录里")
if __name__ == "__main__":
    unittest.main(verbosity=2)
