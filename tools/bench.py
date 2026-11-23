# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# PURPOSE: 把「题集 → 负控 → 正控 → 评补丁」串成一条命令，让**任何一台机器**都能跑出一份可读的读数。
# ORIGIN: none（新文件）。
"""一轮完整的小型评测：从仓库历史造题，验证量具，再评补丁。

为什么需要它：`unfix.py` 的三个子命令各管一段，跑一轮要人肉串五条命令，而且**没人负责汇总**。
汇总这一步最容易出错 —— 分母怎么取、没测成的怎么算、两个口径报哪个，全在那一步。

这个工具只做一件事：把它们串起来，并且**把两个分母都写出来**（`pass_rate_scored` / `pass_rate_all`）。

用法：

    python -X utf8 -B tools/bench.py --repo <仓库> --limit 3 --out <输出目录>
    python -X utf8 -B tools/bench.py --repo <仓库> --limit 3 --out <目录> --patch-dir <放 *.patch 的目录>

`--patch-dir` 里按 `<commit>.patch` 命名；匹配到的会被评一次，没匹到的题会记 `no_patch`。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import unfix  # noqa: E402


def summarize(tasks: list[dict]) -> dict:
    """把逐题结果折成一份口径明确的汇总。

    两个分母都写出来，是因为它们回答的是不同问题：

    * `pass_rate_scored` = 通过 / **真被评过**的题（`patch_scored`）
      —— 回答「我的补丁在能评的题上表现如何」；
    * `pass_rate_all` = 通过 / **全部可用题**（`usable`）
      —— 回答「整套题集上，通过率是多少」，`no_patch` 与 `infra_error` 都算没通过。

    只报前者会把分母缩小、数字变好看；只报后者会把「没评」和「没做对」混在一起。
    **一个桶装两件事的地方，就是把两个都写出来。**
    """
    total = len(tasks)
    usable = [t for t in tasks if t.get("usable") and not t.get("skipped")]
    neg_ok = [t for t in usable if t.get("neg_status") == "failed"]
    gold_ok = [t for t in usable if t.get("gold_status") == "passed"]
    scored = [t for t in usable if t.get("patch_status") in ("passed", "failed")]
    passed = [t for t in scored if t.get("patch_status") == "passed"]
    # `no_patch` 与 `infra_error` 都算「没评成」，但原因完全不同：
    # 前者是没给补丁（可能只是文件名没对上），后者是给了但被判据拒绝（例如改了测试）。
    # 合成一个数会让「一道都没匹配上」看起来像「一道都没做出来」。
    no_patch = [t for t in usable if t.get("patch_status") == "no_patch"]
    patch_errors = [t for t in usable if t.get("patch_status") == "infra_error"]
    unscored = no_patch + patch_errors

    def rate(a: int, b: int):
        return None if b == 0 else round(a / b, 4)

    return {
        "tasks_total": total,
        "usable": len(usable),
        # 量具效度：负控必须红、正控必须绿，两条都成立这道题才算数
        "neg_control_failed": len(neg_ok),
        "gold_control_passed": len(gold_ok),
        "instrument_ok": len(neg_ok) == len(usable) == len(gold_ok) and len(usable) > 0,
        # 补丁评分
        "patch_scored": len(scored),
        "patch_passed": len(passed),
        "unscored": len(unscored),
        "no_patch": len(no_patch),
        "patch_infra_error": len(patch_errors),
        "pass_rate_scored": rate(len(passed), len(scored)),
        "pass_rate_all": rate(len(passed), len(usable)),
        "note": ("两个口径都给了：scored 是「真评过」的分母，all 是「全部可用题」的分母；"
                 "只报其中一个等于替读者做决定"),
    }


def _top_dirs(repo: pathlib.Path, sample: int = 300, limit: int = 5) -> list[str]:
    """这个仓库最近改动最多的顶层目录（用来提示 --src-prefixes 该怎么填）。

    「前缀不对」这种报错，光说「不对」没用 —— 得给出「那该填什么」。
    """
    code, out, _ = unfix.git(repo, "log", f"-n{sample}", "--name-only", "--format=")
    if code != 0:
        return []
    counts: dict[str, int] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.endswith(".py"):
            continue
        head = line.split("/")[0] if "/" in line else "."
        counts[head] = counts.get(head, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ranked[:limit]]


def run_bench(repo: pathlib.Path, py: pathlib.Path, limit: int, out_dir: pathlib.Path,
              src_prefixes, test_prefixes, patch_dir: pathlib.Path | None = None,
              timeout: float = 180.0, budget_seconds: float = 600.0) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = out_dir / "scan"
    print(f"[1/3] 造题与验证（limit={limit}）", flush=True)
    rc = unfix.scan(repo, py, limit, scan_dir, src_prefixes, test_prefixes)
    if rc != 0:
        print(f"  scan 失败（exit {rc}），停止", file=sys.stderr)
        return rc

    cand_file = scan_dir / "candidates.jsonl"
    if not cand_file.is_file():
        # 0 个候选时 `scan` 不写这个文件，早期版本在这里直接 `FileNotFoundError` ——
        # 那个报错把「本仓库没有同时改源码与测试的提交」说成了「文件不存在」，
        # 读的人会去查磁盘，而真正的原因通常是前缀没匹配。
        # 实测（副机 L85）：默认前缀 `fastapi/ httpx/ requests/ rich/ src/` 对一个
        # 源码在 `kernel/`、`project/` 的仓库当然匹配不到。
        print(f"没有候选：这个仓库里没有任何提交同时改了「{list(src_prefixes)}」下的源码"
              f"和「{list(test_prefixes)}」下的测试。", file=sys.stderr)
        print("多半是前缀与仓库布局不匹配 —— 用 --src-prefixes / --test-prefixes 指定。",
              file=sys.stderr)
        # 别让人自己去猜布局：直接把这个仓库最近改动过的目录名列出来。
        hint = _top_dirs(repo)
        if hint:
            print(f"  这个仓库最近 300 个提交里，改动最多的顶层目录是：{'、'.join(hint)}",
                  file=sys.stderr)
            print(f"  例如：--src-prefixes \"{hint[0]}/\"", file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in cand_file.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    usable = [r for r in rows if r.get("usable")]
    print(f"  候选 {len(rows)}，可用 {len(usable)}", flush=True)

    if not usable:
        summary = summarize([])
        summary.update({"repo": str(repo), "patched": bool(patch_dir)})
        (out_dir / "summary.json").write_text(
            json.dumps({"summary": summary, "tasks": []}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        (out_dir / "summary.md").write_text(_markdown(summary, []), encoding="utf-8")
        print(f"  候选 {len(rows)}，但**一道都没通过红→绿两关** —— 这不是通过率 0，是量具没成立。",
              file=sys.stderr)
        return 2

    tasks = []
    t_start = time.monotonic()
    stopped = False
    for i, row in enumerate(usable, 1):
        commit = row["commit"]
        tests = row["test_files"]
        # 预算闸：到点就停，并且**写出一份部分汇总**。
        # 硬跑到底的代价不是慢，是**一旦被杀就什么都没有**（实测被 14 分钟上限掐掉，零产出）。
        if budget_seconds is not None and (time.monotonic() - t_start) > budget_seconds:
            stopped = True
            for rest in usable[i - 1:]:
                tasks.append({"commit": rest["commit"], "subject": rest.get("subject"),
                              "tests": rest.get("test_files", []), "usable": True,
                              "skipped": "budget", "patch_status": None})
            print(f"  预算用尽（>{budget_seconds:g}s）：剩 {len(usable) - i + 1} 道没跑，照常写部分汇总",
                  file=sys.stderr)
            break
        task = {"commit": commit, "subject": row.get("subject"), "tests": tests,
                "usable": True, "fail_to_pass": row.get("fail_to_pass", [])}
        print(f"[2/3] ({i}/{len(usable)}) {commit} 负控 / 正控", flush=True)
        neg = unfix._capture_run(repo, py, commit, tests, out_dir / f"neg-{commit}",
                                apply_gold=False, patch=None, timeout=timeout,
                                src_prefixes=src_prefixes, test_prefixes=test_prefixes,
                                    # 判绿按题目要的用例，不是整文件：
                                    # 同文件有既有失败时，正确补丁会被误判 failed。
                                    fail_to_pass=task.get("fail_to_pass") or None)
        gold = unfix._capture_run(repo, py, commit, tests, out_dir / f"gold-{commit}",
                                 apply_gold=True, patch=None, timeout=timeout,
                                 src_prefixes=src_prefixes, test_prefixes=test_prefixes,
                                    # 判绿按题目要的用例，不是整文件：
                                    # 同文件有既有失败时，正确补丁会被误判 failed。
                                    fail_to_pass=task.get("fail_to_pass") or None)
        task["neg_status"] = neg["status"]
        task["gold_status"] = gold["status"]

        patch = None
        if patch_dir:
            cand = patch_dir / f"{commit}.patch"
            if cand.exists():
                patch = cand
        if patch is None:
            task["patch_status"] = "no_patch"
        else:
            print(f"        评补丁 {patch.name}", flush=True)
            got = unfix._capture_run(repo, py, commit, tests, out_dir / f"patch-{commit}",
                                    apply_gold=False, patch=str(patch), timeout=timeout,
                                    src_prefixes=src_prefixes, test_prefixes=test_prefixes,
                                    # 判绿按题目要的用例，不是整文件：
                                    # 同文件有既有失败时，正确补丁会被误判 failed。
                                    fail_to_pass=task.get("fail_to_pass") or None)
            task["patch_status"] = got["status"]
            task["patch_reason"] = got.get("reason")
        tasks.append(task)

    summary = summarize(tasks)
    summary["budget_seconds"] = budget_seconds
    summary["skipped_budget"] = sum(1 for x in tasks if x.get("skipped") == "budget")
    summary["stopped_early"] = bool(stopped)

    if patch_dir and summary["patch_scored"] == 0 and summary["no_patch"] == summary["usable"]:
        # 这不是「一道都没做出来」，是**一个补丁文件都没匹配上**。
        # 最常见的错因：文件名要求 `<commit>.patch`，而人给的是 `<完整 40 位 sha>.patch`。
        sample = "、".join(f"{x['commit']}.patch" for x in usable[:3])
        print(f"注意：--patch-dir {patch_dir} 里没有任何文件匹配这些题的文件名。", file=sys.stderr)
        print(f"  约定是 `<commit>.patch`，例如：{sample}", file=sys.stderr)
        print("  所以 pass_rate_all=0.0 是「没评」不是「没做出来」——看 unscored/no_patch 两个字段。",
              file=sys.stderr)

    summary["repo"] = str(repo)
    summary["patched"] = bool(patch_dir)
    (out_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "tasks": tasks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (out_dir / "summary.md").write_text(_markdown(summary, tasks), encoding="utf-8")

    print("[3/3] 汇总", flush=True)
    print(f"  可用题 {summary['usable']}；负控红 {summary['neg_control_failed']}；"
          f"正控绿 {summary['gold_control_passed']}；量具成立={summary['instrument_ok']}", flush=True)
    print(f"  评过 {summary['patch_scored']}，通过 {summary['patch_passed']}；"
          f"pass_rate_scored={summary['pass_rate_scored']} / "
          f"pass_rate_all={summary['pass_rate_all']}", flush=True)
    print(f"  产出：{out_dir / 'summary.json'}、{out_dir / 'summary.md'}", flush=True)
    return 0


def _markdown(summary: dict, tasks: list[dict]) -> str:
    lines = ["# 一轮小型评测：读数与口径", ""]
    if summary.get("stopped_early"):
        lines += [f"> **这一轮因时间预算停止**：跑了 {summary['usable']} 道，"
                  f"另有 {summary['skipped_budget']} 道没跑（`skipped=budget`）。",
                  "> 部分读数也是读数 —— 比什么都没有强。", ""]
    lines += [
        f"- 仓库：`{summary['repo']}`",
        f"- 可用题：**{summary['usable']}**（候选里过了红→绿两关的）",
        f"- 负控（不打补丁应红）：**{summary['neg_control_failed']}/{summary['usable']}**",
        f"- 正控（打 gold 应绿）：**{summary['gold_control_passed']}/{summary['usable']}**",
        f"- 量具成立：**{summary['instrument_ok']}**（两条对照都成立且至少有一道题）",
        "",
        "## 补丁评分",
        "",
        "| 口径 | 值 | 分母是什么 |",
        "|---|---|---|",
        f"| pass_rate_scored | {summary['pass_rate_scored']} | 真评过的题（{summary['patch_scored']}）|",
        f"| pass_rate_all | {summary['pass_rate_all']} | 全部可用题（{summary['usable']}）|",
        "",
        f"没评成：**{summary['unscored']}** 道（`no_patch` 或 `infra_error`）。",
        "",
        "> 两个口径都写出来，是因为它们回答不同问题。只报好看的那个等于替读者做决定。",
        "",
        "## 逐题",
        "",
        "| 提交 | 主题 | 负控 | 正控 | 补丁 |",
        "|---|---|---|---|---|",
    ]
    for t in tasks:
        subject = (t.get("subject") or "").replace("|", "\\|")[:40]
        lines.append(f"| `{t['commit']}` | {subject} | {t.get('neg_status')} | "
                     f"{t.get('gold_status')} | {t.get('patch_status')} |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bench.py")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", default=None, help="默认 <repo>/.venv/Scripts/python.exe")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--src-prefixes", default=None)
    ap.add_argument("--test-prefixes", default=None)
    ap.add_argument("--patch-dir", default=None)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--budget-seconds", type=float, default=600.0,
                    help="整轮的时间预算；到点就停手并写出一份部分汇总")
    args = ap.parse_args(argv)

    repo = pathlib.Path(args.repo).resolve()
    py = pathlib.Path(args.python) if args.python else (repo / ".venv/Scripts/python.exe")
    if not py.exists():
        print(f"找不到解释器：{py}（用 --python 指定）", file=sys.stderr)
        return 2

    src_prefixes = (tuple(args.src_prefixes.split(",")) if args.src_prefixes
                    else unfix.DEFAULT_SRC_PREFIXES)
    test_prefixes = (tuple(args.test_prefixes.split(",")) if args.test_prefixes
                     else unfix.DEFAULT_TEST_PREFIXES)
    return run_bench(repo, py, args.limit, pathlib.Path(args.out), src_prefixes,
                     test_prefixes,
                     pathlib.Path(args.patch_dir) if args.patch_dir else None,
                     args.timeout, args.budget_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
