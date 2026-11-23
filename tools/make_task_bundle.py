"""从一次提交造一个「模型看不到答案」的题目副本。

## 为什么必须这样

上一轮副机交了一份「dsh 做题闭环」的补丁，逐字等于我自己某次提交的改动 ——
连中文注释和实验编号都一样。原因很简单：**题就出在那个仓库里，答案写在 git 历史里**，
模型 `git log -S` 一翻就找到了。

所以出题不能给「真仓库 + 某个起点」，必须给**剥离了未来的副本**：
`commit^` 的整棵树 + 该 commit 自己的测试文件，重新 `git init` 成一个**单提交**仓库。

副作用是好的一面：副本里没有 `commit` 之后任何东西，也没有 `commit` 自己的源码改动。
模型只能从「失败的测试」反推意图 —— 这才是要测的能力。

## 用法

    python tools/make_task_bundle.py --repo D:\\rich --commit 648ceda2 --dest D:\\task-648ceda2

产出：
    <dest>/            单提交仓库（commit^ 的树 + commit 的测试）
    <dest>.meta.json   这道题的元信息（原始 commit、src/test 文件、校验结果）

## 校验（不通过就不出题）

1. `git log --all --oneline` **只能有 1 行** —— 有第 2 行就说明带进来了未来；
2. 该仓库里 `commit` 之后的所有提交都不能出现（单提交自然满足，但要显式验一遍）；
3. `git status` 干净。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys


def sh(*args, cwd=None) -> tuple[int, str, str]:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout, p.stderr


def build(repo: pathlib.Path, commit: str, dest: pathlib.Path,
          src_prefixes=("",), test_prefixes=("tests/",)) -> dict:
    repo = pathlib.Path(repo).resolve()
    dest = pathlib.Path(dest).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} 不是空目录，先换个位置或清空它")

    code, out, err = sh("git", "-C", str(repo), "rev-parse", f"{commit}^")
    if code != 0:
        raise SystemExit(f"读不到 {commit}^：{err[:160]}")
    parent = out.strip()

    code, out, err = sh("git", "-C", str(repo), "show", "--name-only", "--format=", commit)
    if code != 0:
        raise SystemExit(f"读不到 {commit} 的文件清单：{err[:160]}")
    files = [x for x in out.splitlines() if x.strip()]
    src = [f for f in files if f.startswith(tuple(src_prefixes))] if src_prefixes != ("",) else files
    tests = [f for f in files if f.startswith(tuple(test_prefixes))]

    dest.parent.mkdir(parents=True, exist_ok=True)
    # 上一次中断可能留下已注册的 worktree（目录被删了但 git 还记得），
    # 直接 add 会报 "is a missing but already registered worktree"。先 prune，失败再强删重试。
    sh("git", "-C", str(repo), "worktree", "prune")
    code, _o, err = sh("git", "-C", str(repo), "worktree", "add", "--detach", str(dest), parent)
    if code != 0 and "already registered" in (err or ""):
        sh("git", "-C", str(repo), "worktree", "remove", "--force", str(dest))
        sh("git", "-C", str(repo), "worktree", "prune")
        code, _o, err = sh("git", "-C", str(repo), "worktree", "add", "--detach", str(dest), parent)
    if code != 0:
        raise SystemExit(f"建 worktree 失败：{err[:160]}")

    # 把该 commit 自己的测试放进来：这才是「要修的东西」
    if tests:
        sh("git", "-C", str(dest), "checkout", commit, "--", *tests)
    # 断开与原仓库的联系，重建成单提交
    gitfile = dest / ".git"
    if gitfile.is_file():
        gitfile.unlink()
    for keep in (".git",):
        p = dest / keep
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    sh("git", "init", "-q", str(dest))
    sh("git", "-C", str(dest), "config", "core.fileMode", "false")
    sh("git", "-C", str(dest), "add", "-A")
    sh("git", "-C", str(dest), "-c", "user.name=task", "-c", "user.email=t@x",
       "commit", "-q", "-m", "task start")
    # worktree 元数据在父仓库那边还留着一条，清掉
    sh("git", "-C", str(repo), "worktree", "prune")

    return verify(dest, {"origin_repo": str(repo), "origin_commit": commit,
                         "parent": parent, "src_files": src, "test_files": tests})


def verify(dest: pathlib.Path, meta: dict) -> dict:
    """出题前必须过的三道关。过不了就抛异常 —— 不许把带答案的题交出去。"""
    code, out, _ = sh("git", "-C", str(dest), "log", "--all", "--oneline")
    lines = [x for x in out.splitlines() if x.strip()]
    if len(lines) != 1:
        raise SystemExit(f"题目副本里应该只有 1 个提交，实际 {len(lines)} 个 —— "
                         f"历史里可能带着答案，拒绝出题\n" + "\n".join(lines[:5]))

    code, out, _ = sh("git", "-C", str(dest), "status", "--porcelain")
    if out.strip():
        raise SystemExit(f"题目副本工作树不干净：\n{out[:300]}")

    meta = dict(meta)
    meta["commits"] = len(lines)
    meta["files"] = sum(1 for p in dest.rglob("*") if p.is_file())
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--src-prefixes", default="", help="逗号分隔；留空表示不限")
    ap.add_argument("--test-prefixes", default="tests/", help="逗号分隔")
    args = ap.parse_args(argv)

    src = tuple(x for x in args.src_prefixes.split(",") if x) or ("",)
    tests = tuple(x for x in args.test_prefixes.split(",") if x)
    meta = build(pathlib.Path(args.repo), args.commit, pathlib.Path(args.dest), src, tests)
    meta_path = pathlib.Path(str(args.dest) + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"题目副本已生成：{args.dest}")
    print(f"  提交数 = {meta['commits']}（必须为 1）")
    print(f"  测试文件 = {meta['test_files']}")
    print(f"  元信息 = {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
