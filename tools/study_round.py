"""跑一轮完整的研究实验：造题 → 交给 solver 做 → 收补丁 → 判分 → 出报告。

## 它把哪些零件串起来

前面几十轮里这些零件是分开验的：`scan` 造题、`make_task_bundle` 造「看不到答案」的副本、
`bench` 判分与汇总、`research.py eval` 的契约。这条脚本只做一件事：**按顺序调它们**，
并保证每一步的产物都在同一个目录下、可复查。

## 为什么要有它

分开跑的时候，每轮我都要手工串五条命令，而且**每条命令的参数都记在脑子里**。
一旦某一步用了上次留下的目录，读数就会混进旧产物 —— 这在这几周里发生过不止一次。
把顺序写进脚本，至少「这次跑的是这次的东西」是确定的。

## 用法

    python tools/study_round.py --repo D:\\rich --python C:\\py\\python.exe \\
        --src-prefixes rich/ --limit 3 --out rounds/r1 \\
        --solver-cmd "dsh --profile headless --cwd {task_dir} --prompt {prompt_file}"

不给 `--solver-cmd` 就只做「造题 + 造副本 + 判分」，补丁目录留空（读数会是 nothing_scored）。
`solver-cmd` 里的 `{task_dir}` 与 `{prompt_file}` 会被替换。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import make_task_bundle  # noqa: E402
import unfix  # noqa: E402

PROMPT = """仓库里有一个测试用例失败：{test_id}

请只修改源码让它通过，**不要修改测试文件**，也不要修改 pytest 配置。

你可以自由读代码。完成后不要提交，改动留在工作区即可。
"""


def sh(args, cwd=None, timeout=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=timeout)


def build_round(repo: pathlib.Path, py: pathlib.Path, limit: int, out: pathlib.Path,
                src_prefixes, test_prefixes, solver_cmd=None,
                solver_timeout=900, timeout=180.0, patch_dir=None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    scan_dir = out / "scan"
    print(f"[1/5] 造题（limit={limit}）", flush=True)
    rc = unfix.scan(repo, py, limit, scan_dir, src_prefixes, test_prefixes)
    if rc != 0:
        return {"ok": False, "stage": "scan", "rc": rc}

    cand_file = scan_dir / "candidates.jsonl"
    rows = ([json.loads(x) for x in cand_file.read_text(encoding="utf-8").splitlines()
             if x.strip()] if cand_file.is_file() else [])
    usable = [r for r in rows if r.get("usable")]
    print(f"      可用题 {len(usable)} / 候选 {len(rows)}", flush=True)
    if not usable:
        return {"ok": False, "stage": "scan", "rc": 2, "usable": 0}

    print("[2/5] 造「看不到答案」的副本", flush=True)
    tasks_dir = out / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    bundles = []
    for i, row in enumerate(usable, 1):
        # 目录名**不带 sha**：带 sha 就等于把提交号告诉解题方（副机 L110 指出过）。
        dest = tasks_dir / f"task{i:02d}"
        try:
            meta = make_task_bundle.build(repo, row["commit"], dest,
                                          tuple(src_prefixes), tuple(test_prefixes))
        except SystemExit as exc:
            print(f"      task{i:02d} 造副本失败：{exc}", file=sys.stderr, flush=True)
            continue
        bundles.append({"row": row, "dir": dest, "meta": meta})
        print(f"      task{i:02d} ← {row['commit'][:8]}  提交数={meta['commits']}", flush=True)

    if not bundles:
        return {"ok": False, "stage": "bundle", "rc": 2}

    patches = out / "patches"
    patches.mkdir(exist_ok=True)
    if patch_dir:
        # 「已有补丁」模式：直接拿一份现成补丁复评（不跑 solver）。
        # 这样同一轮可以只换补丁重跑判分，不用重新造题。
        src_dir = pathlib.Path(patch_dir)
        n = 0
        for src_patch in src_dir.glob("*.patch"):
            (patches / src_patch.name).write_bytes(src_patch.read_bytes())
            n += 1
        print(f"[3/5] 已导入 {n} 份现成补丁（{src_dir}）", flush=True)
    if solver_cmd and not patch_dir:
        print("[3/5] 让 solver 做每一道题", flush=True)
        for b in bundles:
            row = b["row"]
            prompt = b["dir"].parent / f"{b['dir'].name}.prompt.txt"
            prompt.write_text(PROMPT.format(test_id=(row["fail_to_pass"] or ["?"])[0]),
                              encoding="utf-8")
            cmd = solver_cmd.replace("{task_dir}", str(b["dir"])).replace(
                "{prompt_file}", str(prompt))
            try:
                argv = shlex.split(cmd, posix=False)
                # Windows 下必须传 argv 数组：传字符串给 subprocess 会走另一套解析，
                # 路径带空格时直接 FileNotFoundError（实测）。
                p = sh(argv, cwd=str(b["dir"]), timeout=solver_timeout)
                print(f"      {b['dir'].name} solver exit={p.returncode}", flush=True)
            except subprocess.TimeoutExpired:
                print(f"      {b['dir'].name} solver 超时 {solver_timeout}s", flush=True)
            d = sh(["git", "-C", str(b["dir"]), "diff"], )
            (patches / f"{row['commit']}.patch").write_text(d.stdout, encoding="utf-8",
                                                           newline="\n")
            print(f"      补丁 {len(d.stdout)} B", flush=True)
    else:
        print("[3/5] 未给 --solver-cmd：本轮不产生补丁", flush=True)

    # 题单：内核/bench 都吃这个形状
    tasks_file = out / "tasks.jsonl"
    with tasks_file.open("w", encoding="utf-8", newline="\n") as fh:
        for b in bundles:
            row = b["row"]
            fh.write(json.dumps({"task_id": row["commit"], "repo": str(repo),
                                 "commit": row["commit"], "test_files": row["test_files"],
                                 "fail_to_pass": row["fail_to_pass"],
                                 "subject": row.get("subject")}, ensure_ascii=False) + "\n")

    print("[4/5] 判分（bench）", flush=True)
    import bench  # 同目录
    rc = bench.run_bench(repo, py, len(bundles), out / "bench", src_prefixes,
                         test_prefixes, patch_dir=patches, timeout=timeout,
                         budget_seconds=1800.0, tasks_file=str(tasks_file))
    print("[5/5] 汇总", flush=True)
    summary = json.loads((out / "bench" / "summary.json").read_text(encoding="utf-8"))
    report = {
        "ok": True, "repo": str(repo), "tasks": len(bundles),
        "solver": bool(solver_cmd),
        "usable": summary["summary"].get("usable"),
        "patch_scored": summary["summary"].get("patch_scored"),
        "patch_passed": summary["summary"].get("patch_passed"),
        "pass_rate_scored": summary["summary"].get("pass_rate_scored"),
        "pass_rate_all": summary["summary"].get("pass_rate_all"),
        "instrument_ok": summary["summary"].get("instrument_ok"),
        "bench_rc": rc,
        # 让「这份 round.json 是不是基于最新那份 summary」可判定：
        # 单独重跑 bench 会更新 summary.json，但不会更新 round.json，
        # 两个文件的指纹不一致就说明 round.json 是旧的。
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "bench_summary_sha256": hashlib.sha256(
            (out / "bench" / "summary.json").read_bytes()).hexdigest()[:16],
    }
    (out / "round.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", default=None)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--src-prefixes", default=None)
    ap.add_argument("--test-prefixes", default=None)
    ap.add_argument("--patch-dir", default=None,
                    help="已有补丁目录（<commit>.patch）：导入后直接判分，不跑 solver")
    ap.add_argument("--solver-cmd", default=None,
                    help="解题命令；可用 {task_dir} 与 {prompt_file} 占位")
    ap.add_argument("--solver-timeout", type=float, default=900.0)
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args(argv)

    repo = pathlib.Path(args.repo).resolve()
    py = pathlib.Path(args.python) if args.python else repo / ".venv/Scripts/python.exe"
    src = (tuple(x for x in args.src_prefixes.split(",") if x)
           if args.src_prefixes else unfix.DEFAULT_SRC_PREFIXES)
    tests = (tuple(x for x in args.test_prefixes.split(",") if x)
             if args.test_prefixes else unfix.DEFAULT_TEST_PREFIXES)
    rep = build_round(repo, py, args.limit, pathlib.Path(args.out), src, tests,
                      solver_cmd=args.solver_cmd, solver_timeout=args.solver_timeout,
                      patch_dir=args.patch_dir,
                      timeout=args.timeout)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
