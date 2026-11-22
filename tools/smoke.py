# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: 一条命令端到端自检「这个仓库的评测链在这里能不能跑通」——单元测试证明不了这件事，
#         因为它们走的全是桩，不是真的 CLI + 真的子进程 + 真的文件系统。
# ORIGIN: none（新文件）。诱因：`examples/tasklist.example.jsonl` 里**没有 command 字段**，
#         而 QUICKSTART 的示例用 `--per-task-command` —— 照抄示例的人会得到「一题都跑不起来」，
#         而且报错说的是题单缺字段，不是「这个示例本来就不能跑」。
# CUMULATIVE-DELTA: 首版。七步检查，每步都打印期望值与实际值，全过才 exit 0。
"""端到端自检：从零到一份可信读数，全链走一遍。

## 为什么需要它

仓库里有 1342 个单元测试，但它们全部吃桩：桩执行器、桩 profile、临时目录。
**一个测试全绿的仓库，仍然可能「新人照着文档做不到」。** 这条链上有五个真实接缝：

1. `make_profile.py` 生成的 profile 是否真的 `can_run`
2. `research.py eval` 是否真的把 argv 交给子进程（不是只打印）
3. 覆盖不全时，读数是否真的变成「不可用」而不是 1.0
4. 两次读数能不能比，比出来是不是对的
5. 参数边界是否在人能理解的地方被拦住（而不是 Traceback）

单元测试逐点验过，但没有一条命令把五点串起来。这个脚本就是那条命令。

## 用法

```powershell
python -X utf8 -B tools\\smoke.py          # 全链自检，全过 exit 0
python -X utf8 -B tools\\smoke.py --keep   # 保留临时目录，便于事后翻产物
```

它只用合成数据（`smoke-*` 题号、`python -c` 命令），不读任何真实题库，不联网，不花钱。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from kernel.autoresearch.adapters import agent_harness as ah  # noqa: E402
from kernel.autoresearch.profiles import load_profile  # noqa: E402

PY = sys.executable
RESEARCH = REPO / "research.py"
MAKE_PROFILE = REPO / "tools" / "make_profile.py"


class Result:
    def __init__(self, step: str, ok: bool, expect: str, actual: str, detail: str = ""):
        self.step = step
        self.ok = ok
        self.expect = expect
        self.actual = actual
        self.detail = detail


def run(argv, timeout=300):
    return subprocess.run(
        argv, cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def eval_report(profile: Path, tasks: Path, output: Path, *extra):
    """跑一次 `research.py eval`，返回 (exit, report 或 None, 原文)。"""
    proc = run([PY, "-X", "utf8", "-B", str(RESEARCH), "eval",
                "--profile", str(profile), "--tasks", str(tasks),
                "--output", str(output), *extra])
    try:
        return proc.returncode, json.loads(proc.stdout), proc.stdout + proc.stderr
    except json.JSONDecodeError:
        return proc.returncode, None, proc.stdout + proc.stderr


def write_tasks(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def step_profile(tmp: Path) -> tuple[Result, Path | None]:
    profile_dir = tmp / "profile"
    proc = run([PY, "-X", "utf8", "-B", str(MAKE_PROFILE), "--out", str(profile_dir)])
    if proc.returncode != 0:
        return Result("1 生成 profile", False, "exit 0", f"exit {proc.returncode}",
                      proc.stdout + proc.stderr), None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Result("1 生成 profile", False, "stdout 是 JSON", proc.stdout[:200]), None
    ok = payload.get("ok") is True and bool(payload.get("argv_preview"))
    return Result("1 生成 profile", ok,
                  "ok=true 且 argv 非空",
                  f"ok={payload.get('ok')} argv={len(payload.get('argv_preview') or [])} 段"), profile_dir


def step_all_passed(tmp: Path, profile: Path) -> Result:
    tasks = write_tasks(tmp / "t-pass.jsonl", [
        {"task_id": "smoke-p1", "command": [PY, "-c", "pass"]},
        {"task_id": "smoke-p2", "command": [PY, "-c", "pass"]},
    ])
    code, report, raw = eval_report(profile, tasks, tmp / "run-pass")
    if report is None:
        return Result("2 全通过", False, "JSON 报告", raw[:200])
    summary = (report.get("results") or {}).get("summary") or {}
    ok = (code == 0 and report.get("ok") is True
          and report.get("verdict") == "all_passed"
          and summary.get("pass_rate") == 1.0)
    return Result("2 全通过", ok,
                  "exit 0 / verdict=all_passed / pass_rate=1.0",
                  f"exit {code} / verdict={report.get('verdict')} / "
                  f"pass_rate={summary.get('pass_rate')}")


def step_not_all_passed(tmp: Path, profile: Path) -> tuple[Result, Path | None]:
    tasks = write_tasks(tmp / "t-mixed.jsonl", [
        {"task_id": "smoke-m1", "command": [PY, "-c", "pass"]},
        {"task_id": "smoke-m2", "command": [PY, "-c", "import sys;sys.exit(3)"]},
    ])
    out = tmp / "run-mixed"
    code, report, raw = eval_report(profile, tasks, out)
    if report is None:
        return Result("3 有题未通过", False, "JSON 报告", raw[:200]), None
    summary = (report.get("results") or {}).get("summary") or {}
    ok = (report.get("verdict") == "not_all_passed"
          and report.get("alignment_error") is None
          and summary.get("pass_rate") == 0.5
          and summary.get("aligned") is True)
    res = Result("3 有题未通过", ok,
                 "verdict=not_all_passed / 无 alignment_error / pass_rate=0.5 / aligned=true",
                 f"verdict={report.get('verdict')} / "
                 f"alignment_error={report.get('alignment_error')} / "
                 f"pass_rate={summary.get('pass_rate')} / aligned={summary.get('aligned')}")
    return res, out


STUB = """\
import sys, json, pathlib
tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])
out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])
out.mkdir(parents=True, exist_ok=True)
rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
keep = rows[:-1]          # 故意丢最后一题 —— 这就是「静默丢题」的形状
with (out / 'task_results.jsonl').open('w', encoding='utf-8', newline='\\n') as fh:
    for r in keep:
        fh.write(json.dumps({'task_id': r['task_id'], 'status': 'passed'}, ensure_ascii=False) + '\\n')
print('emitted', len(keep), 'of', len(rows))
"""


def step_misaligned(tmp: Path) -> Result:
    stub = tmp / "stub_drop.py"
    stub.write_text(STUB, encoding="utf-8")
    profile_dir = tmp / "profile-drop"
    proc = run([PY, "-X", "utf8", "-B", str(MAKE_PROFILE), "--out", str(profile_dir)])
    if proc.returncode != 0:
        return Result("4 覆盖不全", False, "生成 drop profile", proc.stdout + proc.stderr)
    # 把生成 profile 里的执行器换成会丢题的桩
    toml_path = profile_dir / "profile.toml"
    text = toml_path.read_text(encoding="utf-8")
    import re

    replacement = 'command = [' + ", ".join(json.dumps(x) for x in (
        PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}")) + ']'
    # 必须用 lambda：`re.sub` 会把替换串里的 `\\` 当转义处理，Windows 路径会被打散成
    # 单反斜杠，结果是一份非法 TOML —— 报错却是「Unescaped '\' in a string」，
    # 与真正的原因（替换方式）看着毫无关系。
    text = re.sub(r"^command = \[.*?\]$", lambda _m: replacement, text,
                  count=1, flags=re.M | re.S)
    toml_path.write_text(text, encoding="utf-8")

    tasks = write_tasks(tmp / "t-drop.jsonl", [
        {"task_id": "smoke-d1", "command": [PY, "-c", "pass"]},
        {"task_id": "smoke-d2", "command": [PY, "-c", "pass"]},
        {"task_id": "smoke-d3", "command": [PY, "-c", "pass"]},
    ])
    code, report, raw = eval_report(profile_dir, tasks, tmp / "run-drop")
    if report is None:
        return Result("4 覆盖不全", False, "JSON 报告", raw[:200])
    summary = (report.get("results") or {}).get("summary") or {}
    ok = (report.get("verdict") == "misaligned"
          and report.get("ok") is False
          and summary.get("pass_rate") is None
          and summary.get("pass_rate_raw") == 1.0
          and summary.get("aligned") is False
          and "smoke-d3" in json.dumps(report.get("alignment") or {}))
    return Result("4 覆盖不全", ok,
                  "verdict=misaligned / ok=false / pass_rate=null / pass_rate_raw=1.0 / 缺 d3",
                  f"verdict={report.get('verdict')} / ok={report.get('ok')} / "
                  f"pass_rate={summary.get('pass_rate')} / raw={summary.get('pass_rate_raw')} / "
                  f"aligned={summary.get('aligned')}")


def step_compare(tmp: Path, profile: Path, mixed_out: Path | None) -> Result:
    if mixed_out is None:
        return Result("5 两次读数可比", False, "上一步产物", "上一步没产出")
    before = tmp / "run-base"
    tasks = write_tasks(tmp / "t-base.jsonl", [
        {"task_id": "smoke-m1", "command": [PY, "-c", "pass"]},
        {"task_id": "smoke-m2", "command": [PY, "-c", "import sys;sys.exit(3)"]},
    ])
    code, report, raw = eval_report(profile, tasks, before)
    if report is None:
        return Result("5 两次读数可比", False, "JSON 报告", raw[:200])
    loaded = load_profile(profile)
    result = ah.compare_results(
        ah.read_results(before / "task_results.jsonl", loaded),
        ah.read_results(mixed_out / "task_results.jsonl", loaded),
        profile=loaded,
    )
    ok = result.get("comparable") is True and result.get("verdict") == "flat"
    return Result("5 两次读数可比", ok,
                  "comparable=true / verdict=flat（两次结果完全相同）",
                  f"comparable={result.get('comparable')} / verdict={result.get('verdict')} / "
                  f"reasons={result.get('reasons')}")


def step_timeout_guard(tmp: Path, profile: Path) -> Result:
    tasks = write_tasks(tmp / "t-guard.jsonl", [
        {"task_id": "smoke-g1", "command": [PY, "-c", "pass"]},
    ])
    bad = []
    for value in ("inf", "nan", "0", "-5"):
        proc = run([PY, "-X", "utf8", "-B", str(RESEARCH), "eval",
                    "--profile", str(profile), "--tasks", str(tasks),
                    "--output", str(tmp / f"run-guard-{value}"), "--timeout", value])
        blob = proc.stdout + proc.stderr
        if proc.returncode != 2 or "Traceback" in blob or "OverflowError" in blob:
            bad.append(f"{value}:exit{proc.returncode}")
    ok = not bad
    return Result("6 超时参数边界", ok,
                  "--timeout inf/nan/0/-5 全部 exit 2 且无 Traceback",
                  "全部拦住" if ok else "漏：" + ", ".join(bad))


def snapshot_tracked() -> str:
    proc = run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"])
    return proc.stdout.strip() if proc.returncode == 0 else "<git 不可用>"


def step_no_repo_mutation(before: str) -> Result:
    """自检跑完之后，仓库的 tracked 文件不该**多出**改动。

    注意是「多出」而不是「为空」：开始跑之前仓库本来就可能是脏的（比如你正在改文档）。
    拿「是否为空」当判据会把别人的未提交改动误判成自检的副作用，而真正该抓的是
    **自检自己写进了仓库**——那会让下一次 `git status` 变脏，也让读数来源变得不可信
    （到底跑的是哪份配置）。
    """
    after = snapshot_tracked()
    ok = after == before
    if ok:
        actual = "无新增改动" if not before else f"与开始时一致（本来就脏 {len(before.splitlines())} 项）"
    else:
        new_lines = [ln for ln in after.splitlines() if ln not in before.splitlines()]
        actual = f"新增 {len(new_lines)} 项：" + "; ".join(new_lines[:3])
    return Result("7 仓库未被改动", ok, "git status --porcelain -uno 与开始时一致", actual)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="smoke.py", description="端到端自检评测链")
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="autoresearch-smoke-"))
    before = snapshot_tracked()
    results: list[Result] = []
    try:
        r1, profile = step_profile(tmp)
        results.append(r1)
        if profile is None:
            report(results, tmp, args.keep)
            return 1
        results.append(step_all_passed(tmp, profile))
        r3, mixed_out = step_not_all_passed(tmp, profile)
        results.append(r3)
        results.append(step_misaligned(tmp))
        results.append(step_compare(tmp, profile, mixed_out))
        results.append(step_timeout_guard(tmp, profile))
        results.append(step_no_repo_mutation(before))
    finally:
        pass

    failed = report(results, tmp, args.keep)
    return 0 if failed == 0 else 1


def report(results: list[Result], tmp: Path, keep: bool) -> int:
    width = max(len(r.step) for r in results)
    print("=" * 78)
    print("端到端自检：从零到一份可信读数")
    print("=" * 78)
    for r in results:
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.step.ljust(width)}  实际：{r.actual}")
        if not r.ok:
            print(f"{' ' * 7}期望：{r.expect}")
            if r.detail:
                print(f"{' ' * 7}细节：{r.detail[:600]}")
    failed = sum(1 for r in results if not r.ok)
    print("-" * 78)
    print(f"合计 {len(results)} 步：{len(results) - failed} PASS / {failed} FAIL")
    if keep:
        print(f"产物保留在：{tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
