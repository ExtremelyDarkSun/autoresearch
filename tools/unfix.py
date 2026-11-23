# GENERATED-BY: agent implementation, 2026-11-24 +08:00
# LAST-EDIT: 2026-11-24 +08:00
# PURPOSE: 从仓库自己的 git 历史里反解出「带真值的可判定任务」，并跑正负控。
# ORIGIN: none（新文件）。来自一次实验：比赛判分清单在平台侧拿不到，但快照带完整历史，
#         「同一次提交既改源码又改测试」的提交可以反解成 fail-to-pass。
#         实测 fastapi 16 道、rich 27 道可用（详见 docs/UNFIX_TASKS.md）。
# CUMULATIVE-DELTA: 首版。
"""从 git 历史反解可判定任务（unfix tasks）。

## 它解决的问题

有仓库、有测试，但**没有判分清单**（哪几条测试该从红变绿）。这时可以做的是：
把某次修 bug 提交的**源码改动退回去、测试留在新版本**，那条新增的测试就会红。
这个红就是自动得到的 fail-to-pass。

```
第 1 步（红）：源码退到 <commit>^，测试保持 HEAD  → 期望失败，失败用例 = fail-to-pass
第 2 步（绿）：源码恢复 HEAD                      → 期望全过
```

两步都成立，这道题才成立。**任何一步不成立，这道题不许进题集** —— 否则就是拿一把
没校准的尺子量东西。

## 三个子命令

```powershell
python -X utf8 -B tools/unfix.py scan   --repo <dir> [--limit 40]
python -X utf8 -B tools/unfix.py run    --repo <dir> --commit <sha> --tests tests/x.py --out <dir> [--apply-gold]
python -X utf8 -B tools/unfix.py selftest
```

## 判据（`classify`）

判定逻辑被抽成一个纯函数，因为**它曾经连续错三次**：

| 红 | 绿 | 结论 | 为什么 |
|---|---|---|---|
| 1（有用例 id） | 0 | 可用 | 标准情形 |
| 1（无 id） | 0 | 不可用 | 红了但说不出哪条红 |
| **124（挂住）** | 0 | **可用** | 这个 bug 的表现就是死循环；判成不可用会扔掉最像真 bug 的题 |
| 124 | 124 | 不可用 | 两边都跑不完，测不出差别 |
| 2（收集中断） | 0 | 不可用 | 测试根本没跑起来，不是「测出失败」 |
| 0 | 0 | 不可用 | 退回源码仍全过，说明测试没覆盖这处改动 |
| 任意 | ≠0 | 不可用 | 环境本身有问题 |

`124` 是本工具自己的超时码（不是 pytest 的）；pytest 的 `2` 是「被中断/收集错误」。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

#: 源码目录前缀。不同仓库布局不同（`fastapi/` 平铺、`src/` 型、`rich/` 型）。
DEFAULT_SRC_PREFIXES = ("fastapi/", "httpx/", "requests/", "rich/", "src/")
TEST_PREFIXES = ("tests/",)
TIMEOUT_EXIT = 124


def sh(argv, cwd=None, timeout=1800):
    """跑子进程；超时返回 124 而不是抛异常。

    **一个没有超时出口的执行器，会把「一道题挂了」变成「整批没跑」。**
    实测：一道 `tests/test_cells.py` 挂住的题，把整批 40 个候选的扫描打断在第 6 个。
    """
    try:
        proc = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TIMEOUT_EXIT, "", f"超过 {timeout:g} 秒被中断"
    return proc.returncode, proc.stdout, proc.stderr


def git(repo, *args, timeout=600):
    code, out, err = sh(["git", "-C", str(repo), *args], timeout=timeout)
    return code, out.strip(), err.strip()


def classify(red_exit: int, green_exit: int, red_ids: list[str],
             collection_broken: bool = False) -> tuple[bool, str]:
    """红/绿两阶段读数 → 「这道题成立吗」。返回 (usable, 理由)。

    纯函数，无副作用 —— 因为这套判据我连续错三次（见模块文档的表），
    必须能用单元测试逐条钉住，而不是靠在真实仓库上跑一遍看结果。
    """
    if red_exit == TIMEOUT_EXIT and green_exit == 0:
        return True, "红是挂住、绿是通过 ⇒ 可用（这个 bug 的表现就是挂死）"
    if red_exit == TIMEOUT_EXIT or green_exit == TIMEOUT_EXIT:
        return False, f"超时（red={red_exit} green={green_exit}）：两边都跑不完，测不出差别"
    if red_exit == 0:
        return False, "退回源码后仍全过（测试没覆盖这处改动）"
    if green_exit != 0:
        return False, f"恢复后仍不过（环境问题，pytest exit {green_exit}）"
    if red_exit != 1 and collection_broken:
        return False, f"红是收集错误（pytest exit {red_exit}）：测试根本没跑起来，不是「测出失败」"
    if red_exit != 1:
        return False, f"红不是「有测试失败」（pytest exit {red_exit}）"
    if not red_ids:
        return False, "红是 exit 1 但解析不到用例 id（输出里没有 FAILED 行）"
    return True, "红→绿成立"


def restore(repo: pathlib.Path) -> None:
    """非破坏性恢复：先取消暂存、再还原工作树。

    不用 `checkout HEAD -- <files>`：该提交若新增了文件，checkout 会按 pathspec 失败，
    **而失败是部分生效的** —— 暂存区留一半改动，下一个候选的「工作树是否干净」守卫就会误判。
    """
    sh(["git", "-C", str(repo), "reset", "-q", "HEAD", "--", "."])
    sh(["git", "-C", str(repo), "checkout", "--", "."])


def pytest_ids(repo: pathlib.Path, py: pathlib.Path, test_paths, timeout=180):
    if not test_paths:
        return 5, [], "没有测试文件"
    code, out, err = sh([str(py), "-m", "pytest", *test_paths, "-q", "--no-header",
                         "-p", "no:cacheprovider"], cwd=repo, timeout=timeout)
    tail = (out or "").strip().splitlines()[-3:]
    ids = sorted(set(re.findall(r"^FAILED (\S+)", out or "", flags=re.M)))
    return code, ids, " ⏎ ".join(tail)


def changed_files(repo: pathlib.Path, commit: str, src_prefixes) -> tuple[list[str], list[str]]:
    code, out, err = git(repo, "show", "--name-only", "--format=", commit)
    if code != 0:
        raise SystemExit(f"读不到提交 {commit}：{err[:160]}")
    files = [ln for ln in out.splitlines() if ln.strip()]
    src = [f for f in files if f.startswith(tuple(src_prefixes))]
    tests = [f for f in files if f.startswith(TEST_PREFIXES)]
    return src, tests


def scan(repo: pathlib.Path, py: pathlib.Path, limit: int, out_dir: pathlib.Path,
         src_prefixes) -> int:
    code, out, err = git(repo, "log", "--name-only", "--format=@@%h|%s", timeout=900)
    if code != 0:
        raise SystemExit("git log 失败：" + err[:160])

    cands, cur = [], None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            if cur:
                cands.append(cur)
            head, _, subj = line[2:].partition("|")
            cur = {"commit": head, "subject": subj, "files": []}
        elif cur is not None:
            cur["files"].append(line)
    if cur:
        cands.append(cur)

    keep = []
    for c in cands:
        src = [f for f in c["files"] if f.startswith(tuple(src_prefixes))]
        tst = [f for f in c["files"] if f.startswith(TEST_PREFIXES)]
        if src and tst:
            keep.append({"commit": c["commit"], "subject": c["subject"],
                         "src_files": src, "test_files": tst})
    print(f"候选（源码+测试同改）：{len(keep)}；本轮验证前 {min(limit, len(keep))}", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, cand in enumerate(keep[:limit], 1):
        rec = dict(cand)
        rec.update({"red": {}, "green": {}, "fail_to_pass": [], "usable": False,
                    "fail_mode": "assert", "why": None})
        code, dirty, _ = git(repo, "status", "--porcelain", "--untracked-files=no")
        if dirty:
            rec["why"] = "工作树不干净（跟踪文件有改动）"
        else:
            try:
                code, o, e = git(repo, "checkout", f"{cand['commit']}^", "--", *cand["src_files"])
                if code != 0:
                    rec["why"] = f"退回源码失败：{e[:120]}"
                else:
                    rc, rids, rtail = pytest_ids(repo, py, cand["test_files"])
                    rec["red"] = {"exit": rc, "failed_ids": rids, "tail": rtail}
                    git(repo, "checkout", "HEAD", "--", *cand["src_files"])
                    gc, gids, gtail = pytest_ids(repo, py, cand["test_files"])
                    rec["green"] = {"exit": gc, "failed_ids": gids, "tail": gtail}
                    broken = "during collection" in (rtail or "")
                    usable, why = classify(rc, gc, rids, broken)
                    rec["usable"] = usable
                    rec["why"] = why
                    if usable and rc == TIMEOUT_EXIT:
                        rec["fail_mode"] = "hang"
                    if usable:
                        rec["fail_to_pass"] = rids
            finally:
                restore(repo)
        rows.append(rec)
        print(f"[{i}/{min(limit, len(keep))}] {rec['commit']} usable={rec['usable']} "
              f"ftp={len(rec['fail_to_pass'])} {rec['why']} | {rec['subject'][:40]}", flush=True)
        with (out_dir / "candidates.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    tasks = [{"task_id": f"{repo.name}__{r['commit']}", "commit": r["commit"],
              "subject": r["subject"], "src_files": r["src_files"],
              "test_files": r["test_files"], "fail_to_pass": r["fail_to_pass"],
              "fail_mode": r["fail_mode"]} for r in rows if r["usable"]]
    with (out_dir / "tasks_unfix.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"完成：候选 {len(keep)}，验证 {len(rows)}，可用 {len(tasks)} → {out_dir}", flush=True)
    return 0


def run_one(repo: pathlib.Path, py: pathlib.Path, commit: str, tests: list[str],
            out_dir: pathlib.Path, apply_gold: bool, patch: str | None,
            timeout: float, src_prefixes) -> int:
    started = time.monotonic()
    res = {"task_id": commit, "reason": None, "notes": None, "status": "infra_error",
           "duration_seconds": None, "steps": {}}

    def finish(status, reason=None, notes=None):
        res["status"], res["reason"], res["notes"] = status, reason, notes
        res["duration_seconds"] = round(time.monotonic() - started, 2)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{commit}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False))
        return {"passed": 0, "failed": 1, "infra_error": 2}[status]

    if not py.is_file():
        return finish("infra_error", f"找不到解释器：{py}")
    code, dirty, _ = git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        return finish("infra_error", "工作树不干净（跟踪文件有改动），先清理")

    src, tests_changed = changed_files(repo, commit, src_prefixes)
    if not src:
        return finish("infra_error", "该提交没有源码改动，不能当反解题")
    if not tests_changed:
        return finish("infra_error", "该提交没有测试改动，反解不出 fail-to-pass")

    # 跑之前先对齐「你传的测试文件」与「这个提交改的测试文件」。
    #
    # 为什么必须查：传错测试文件时，**退回源码后那些测试照样全过**，于是正控负控都绿，
    # 一道根本不成立的题看起来完全正常 —— 这是题集层面的假绿。
    # 实测踩到：`648ceda2` 改的是 `tests/test_file_proxy.py`，我传了 `tests/test_cells.py`，
    # 负控直接 passed。
    wanted = {pathlib.PurePosixPath(p).as_posix() for p in tests_changed}
    given = {pathlib.PurePosixPath(p).as_posix() for p in tests}
    if not (wanted & given):
        return finish(
            "infra_error",
            f"传给 --tests 的文件与这个提交改的测试文件不相交；"
            f"这个提交改的是：{'、'.join(sorted(wanted))}")

    try:
        code, _, err = git(repo, "checkout", f"{commit}^", "--", *src)
        res["steps"]["revert"] = {"exit": code, "files": src}
        if code != 0:
            return finish("infra_error", f"退回源码失败：{err[:160]}")

        if patch:
            code, _, err = sh(["git", "-C", str(repo), "apply", patch])
            res["steps"]["apply"] = {"source": "指定补丁", "exit": code}
            if code != 0:
                return finish("infra_error", f"打补丁失败：{err[:160]}")
        elif apply_gold:
            # gold = **HEAD 的源码**，不是 `<commit>` 的源码：测试取自 HEAD，
            # 而该提交之后同一文件可能又被改过（实测 16 道里 2 道因此不过）。
            code, _, err = git(repo, "checkout", "HEAD", "--", *src)
            res["steps"]["apply"] = {"source": "gold（HEAD 的源码）", "exit": code}
            if code != 0:
                return finish("infra_error", f"应用 gold 失败：{err[:160]}")
        else:
            res["steps"]["apply"] = {"source": None, "note": "负控：不打补丁"}

        t0 = time.monotonic()
        code, out, _ = sh([str(py), "-m", "pytest", *tests, "-q", "--no-header",
                           "-p", "no:cacheprovider"], cwd=repo, timeout=timeout)
        res["steps"]["pytest"] = {"exit": code, "seconds": round(time.monotonic() - t0, 1),
                                  "tail": " ⏎ ".join((out or "").strip().splitlines()[-3:])}
        tail = res["steps"]["pytest"]["tail"]
    finally:
        restore(repo)

    if code == 0:
        return finish("passed", notes="全部测试通过")
    if code == 1:
        return finish("failed", f"有测试失败：{tail}")
    if code == TIMEOUT_EXIT:
        return finish("failed", f"测试在 {timeout:g} 秒内没跑完（挂住）—— 这个 bug 还在：{tail}")
    return finish("infra_error", f"不是「没通过」，是没测成（pytest exit {code}）：{tail}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="unfix.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="扫全历史，产出可用题集")
    s.add_argument("--repo", required=True)
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--out", default=None)
    s.add_argument("--python", default=None)
    s.add_argument("--src-prefixes", default=None, help="逗号分隔，默认内置几种布局")

    r = sub.add_parser("run", help="跑一道题（正控 / 负控 / 评补丁）")
    r.add_argument("--repo", required=True)
    r.add_argument("--commit", required=True)
    r.add_argument("--tests", nargs="+", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--python", default=None)
    r.add_argument("--apply-gold", action="store_true")
    r.add_argument("--patch", default=None)
    r.add_argument("--timeout", type=float, default=120.0)
    r.add_argument("--src-prefixes", default=None)

    ap.add_argument("--selftest", action="store_true", help="跑判据的单元测试")

    args = ap.parse_args(argv)
    prefixes = (tuple(args.src_prefixes.split(",")) if getattr(args, "src_prefixes", None)
                else DEFAULT_SRC_PREFIXES)

    if getattr(args, "selftest", False):
        import unittest
        suite = unittest.TestLoader().loadTestsFromName("tests.test_unfix")
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1

    if args.cmd == "scan":
        repo = pathlib.Path(args.repo).resolve()
        py = pathlib.Path(args.python) if args.python else (repo / ".venv/Scripts/python.exe")
        out = pathlib.Path(args.out) if args.out else (repo.parent / f"unfix-sweep-{repo.name}")
        return scan(repo, py, args.limit, out, prefixes)

    repo = pathlib.Path(args.repo).resolve()
    py = pathlib.Path(args.python) if args.python else (repo / ".venv/Scripts/python.exe")
    return run_one(repo, py, args.commit, args.tests, pathlib.Path(args.out),
                   args.apply_gold, args.patch, args.timeout, prefixes)


if __name__ == "__main__":
    raise SystemExit(main())
