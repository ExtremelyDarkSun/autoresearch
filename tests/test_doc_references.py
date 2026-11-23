# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 拦住「文档里引用了一个不存在的提交」——它比数字过期更隐蔽：读者会以为是自己的问题。
# ORIGIN: none（新文件）。诱因：`docs/FALSE-GREEN-TAXONOMY.md` 每条事故都挂了提交号，
#         而提交号一旦写错（打错一位、在别的仓库、被 rebase 掉）就变成一条无法核实的断言。
# CUMULATIVE-DELTA: 首版。扫 docs/ 与 README，凡出现 7 位十六进制且看着像提交号的，都要求 git 里真有。
"""文档里引用的提交号必须真实存在。

为什么值得单独一个测试：文档写「（`9756c23`）」时，读者默认它可核实。写错一位以后，
**它不再是错的证据，而是一条无法核实的断言** —— 而后一种更糟，因为它看起来仍然很确定。

用 `git cat-file -e <hash>^{commit}` 逐条核；不在 git 仓库里（比如发布包解压）时整组跳过。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 扫描范围：文档正文 + 顶层说明。只认反引号包起来的 7 位十六进制，
#: 避免把 12 位以上的完整哈希或别的标识符误当提交号。
DOC_GLOBS = ("docs/*.md", "README.md")
HEX7 = re.compile(r"`([0-9a-f]{7})`")


def git_available() -> bool:
    if shutil.which("git") is None:
        return False
    return (ROOT / ".git").exists()


def known_commits() -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "log", "--all", "--format=%h"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return set(out.stdout.split())


def referenced(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return sorted(set(HEX7.findall(text)))


@unittest.skipUnless(git_available(), "不在 git 仓库里（或没有 git），无法核对提交号")
class TestDocReferences(unittest.TestCase):
    def test_every_referenced_commit_exists(self):
        known = known_commits()
        self.assertTrue(known, "git log 没返回任何提交")

        missing: list[str] = []
        files = []
        for pattern in DOC_GLOBS:
            files.extend(sorted(ROOT.glob(pattern)))
        self.assertTrue(files, "没找到要扫描的文档")

        for path in files:
            for sha in referenced(path):
                if sha not in known:
                    missing.append(f"{path.relative_to(ROOT)}: `{sha}`")

        self.assertEqual(
            missing, [],
            "这些提交号在仓库里不存在（打错了？在别的仓库？被 rebase 掉了？）：\n  "
            + "\n  ".join(missing),
        )

    def test_taxonomy_cites_real_incidents(self):
        """假绿分类学里每条事故都挂了提交号 —— 至少要有九条带了。"""
        path = ROOT / "docs" / "FALSE-GREEN-TAXONOMY.md"
        self.assertTrue(path.is_file(), "分类学不见了")
        text = path.read_text(encoding="utf-8")
        heads = re.findall(r"^## ", text, flags=re.M)
        self.assertGreaterEqual(len(heads), 14, "十四条的正文不许被删短")
        self.assertGreaterEqual(len(referenced(path)), 9, "引用的提交号明显变少了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
