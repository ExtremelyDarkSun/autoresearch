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


class TestNormalization(unittest.TestCase):
    """规范化规则本身也要测——它是整张表的地基。

    副机 L66 抓到的坑：原规则在空格处停下，于是解释器装在 `C:\\Program Files\\...`
    的机器上得到 `"<PATH> Files<UNC>"`，而装在 `C:\\Python313\\...` 的机器得到 `"<PATH>"`。
    **一个用来消除环境噪声的工具，自己制造了噪声**，而且只在「安装路径含空格」的机器上发作。
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from tools.repro_matrix import normalize  # noqa: PLC0415
        self.normalize = normalize

    def test_quoted_path_with_spaces_equals_without_spaces(self):
        with_spaces = r'command = ["C:\\Program Files\\Python312\\python.exe", "-X"]'
        without = r'command = ["C:\\Python313\\python.exe", "-X"]'
        a = self.normalize(with_spaces)
        b = self.normalize(without)
        self.assertEqual(a, b, f"带空格与不带空格应当规范化成同一文本：{a!r} vs {b!r}")
        self.assertEqual(a, 'command = ["<PATH>", "-X"]')
        self.assertNotIn("<UNC>", a, "`\\\\` 是 JSON 转义，不该被当成 UNC 前缀")

    def test_tree_state_examples_collapse(self):
        a = self.normalize("第 7 步 实际：无新增改动")
        b = self.normalize("第 7 步 实际：与开始时一致（本来就脏 2 项）")
        c = self.normalize("第 7 步 实际：与开始时一致（本来就脏 11 项）")
        self.assertEqual(a, b)
        self.assertEqual(b, c, "脏几项是前置条件，不该影响跨机比对")

    def test_bare_path_with_spaces_is_one_path(self):
        """裸路径含空格也要整条吃掉 —— 判据来自副机 L67。

        我原来那版在裸路径上遇空格即停，理由是「没法可靠判断结尾」。副机没接受这个偷懒，
        给出判据：**只有当空格后面那一块仍以反斜杠结尾时**，才把空格并进来。
        """
        cases = [
            ("C:\\Python313\\python.exe", "<PATH>"),
            ("C:\\Program Files\\Python312\\python.exe", "<PATH>"),
            ("\\\\server\\share\\tool.exe", "<UNC>"),
        ]
        for src, want in cases:
            with self.subTest(src=src):
                self.assertEqual(self.normalize(src), want)

    def test_bare_path_with_multiple_spaced_segments(self):
        """多段各自含空格的路径 —— 副机 L70 用它把我上一版钉红。

        `C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe` 里有两处空格，
        上一版在 `(x86)` 处断掉；`C:\\Program Files\\Common Files\\python.exe` 被截成
        `<PATH> Files\\python.exe`。两个都是真实安装路径，不是构造出来的。
        现在改成逐字符扫描：遇到空白就往同一行后面找反斜杠，找得到才继续。
        """
        cases = [
            ("C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe", "<PATH>"),
            ("C:\\Program Files\\Common Files\\python.exe", "<PATH>"),
            ("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", "<PATH>"),
        ]
        for src, want in cases:
            with self.subTest(src=src):
                self.assertEqual(self.normalize(src), want)

    def test_bare_path_does_not_swallow_following_prose(self):
        cases = [
            ("C:\\Python313\\python.exe 后面还有散文", "<PATH> 后面还有散文"),
            ("C:\\Program Files\\Python312\\python.exe 跑起来了", "<PATH> 跑起来了"),
        ]
        for src, want in cases:
            with self.subTest(src=src):
                self.assertEqual(self.normalize(src), want,
                                 "路径后面的人类语言不能被当成路径的一部分")

    def test_durations_and_timestamps_masked(self):
        text = "Ran 1351 tests in 118.962s\n2026-11-23T05:14:37.293Z"
        out = self.normalize(text)
        self.assertIn("<DUR>", out)
        self.assertIn("<TS>", out)
        self.assertNotIn("118.962", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
