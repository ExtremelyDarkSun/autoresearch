# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: 把 profile 的 [evaluation].command 真正接到 subprocess —— 补上全仓缺失的那一截接线。
# ORIGIN: none（新文件）。此前 build_command() 只有两个调用点
#         （kernel/autoresearch/cli.py 与 profiles/agent-harness/checks/cli.py），
#         二者都只 dump/print argv（executed: false），全仓没有任何路径把它交给 subprocess。
# CUMULATIVE-DELTA: 首版。默认要求 can_run=true 才执行；--force 可越过配置检查但会明确标注。
"""评测运行器：真正执行一次评测，并读回结果。

## 为什么需要这个模块

拆开看这条链上有三段：

1. `evaluation_state()` —— 判断「配置是否合规」（`can_run`）
2. `build_command()` —— 把配置模板实例化成 argv
3. **`subprocess.run(argv)` —— 真的执行**

本仓库原本只有 1 和 2。第 3 段缺失意味着：即使 `can_run=true`、argv 生成正确，
**也没有任何代码会去跑它**。副机在 L41 里把这一点验证到了行号级别：

> `git grep -n "build_command("` 只命中 `cli.py:117` 与 `checks/cli.py:118`，
> 二者都只 dump/print argv；全仓没有任何路径在 `can_run=true` 后把该 argv 交给
> `subprocess.run` 并读回 `task_results.jsonl`。

本模块就是那第三段。

## 安全边界

- **必须 `can_run=true`**。配置没配齐就拒绝执行，返回 blocking 原因。
  （曾经的 `--force` 已删除：`build_command()` 自己也会检查 configured / command / limits，
  而这些正是 `can_run` 的绝大部分条件，所以 `force` 绕不过任何有意义的东西——
  留着它只会让人以为「强制能跑」，实际仍然抛错。）
- 不拼 shell：argv 来自 `build_command()`，已是 list。
- 超时可配，超时视为该次评测失败（`timed_out: true`）。
- 执行完必定尝试读回结果；读不到就如实报 `results_read_error`，不编造通过率。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .adapters.agent_harness import ResultsError, read_results, read_tasklist
from .profiles import build_command, evaluation_state, load_profile

#: 默认单次评测超时（秒）。真评测通常比这慢，调用方应显式调大。
DEFAULT_TIMEOUT_SECONDS = 3600.0


class EvaluationRunError(RuntimeError):
    """评测无法发起（配置不合规、执行器缺失等）。"""


def run_evaluation(
    profile_path,
    *,
    tasks: str,
    output: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> dict:
    """按 profile 的 `[evaluation].command` 执行一次评测，并读回结果。

    参数
    ----
    profile_path : profile.toml 的路径（文件或目录）
    tasks : 题单路径；会替换 `command` 模板里的 `{tasks}`
    output : 结果目录；会替换 `{output}`，结果文件按 profile 的 `result_file` 落在这里
    timeout : 整次评测的超时秒数
    cwd : 子进程工作目录；默认沿用当前目录

    返回
    ----
    一个可直接 dump 成 JSON 的 dict，至少含 `ok` / `executed` / `argv`。

    抛出
    ----
    `EvaluationRunError`：配置不合规；或 `build_command` 拒绝生成 argv。
    """
    profile = load_profile(profile_path)
    state = evaluation_state(profile)

    if not state.get("can_run"):
        raise EvaluationRunError(
            "配置不合规，拒绝执行："
            + "；".join(state.get("blocking") or ["未知原因"])
        )

    try:
        argv = build_command(profile, tasks=tasks, output=output)
    except Exception as exc:  # ProfileError 及任何生成失败
        raise EvaluationRunError(f"无法生成评测 argv：{type(exc).__name__}: {exc}") from exc

    report: dict = {
        "ok": False,
        "executed": False,
        "profile": profile.get("id"),
        "argv": argv,
        "tasks": str(tasks),
        "output": str(output),
        "timeout_seconds": timeout,
        "exit": None,
        "timed_out": False,
        "stdout_tail": None,
        "stderr_tail": None,
        "results": None,
        "results_read_error": None,
        "alignment": None,
        "alignment_error": None,
    }

    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        report["executed"] = True
        report["timed_out"] = True
        report["stderr_tail"] = f"整次评测超过 {timeout:g} 秒被中断"
        return report
    except OSError as exc:
        report["stderr_tail"] = f"无法启动评测进程：{type(exc).__name__}: {exc}"
        return report

    report["executed"] = True
    report["exit"] = proc.returncode
    report["stdout_tail"] = (proc.stdout or "").strip()[-2000:] or None
    report["stderr_tail"] = (proc.stderr or "").strip()[-2000:] or None

    # 执行完必定尝试读回结果。读不到就如实报错，不编造通过率。
    #
    # 静默丢题检查（2026-11-22 补，副机 L54 命名 CONFIRMED-SILENT-DROP）：
    # 此前 `ok` 只等于 `exit == 0`，于是「题单 2 行、执行器只写回 1 行」会被判
    # ok=true / pass_rate=1.0，missing 那一题被静默吞掉，run_meta 还自洽地谎称
    # tasks_total=1。这里强制把「结果里的 task_id 集合」与「输入题单」对齐：
    # 少一题或多一题都判 ok=false，并把差异原样写进报告。
    try:
        results = read_results(Path(output), profile)
        report["results"] = results

        tasklist = read_tasklist(tasks)
        expected = [str(t["task_id"]) for t in (tasklist.get("tasks") or [])]
        actual = [str(t["task_id"]) for t in (results.get("tasks") or [])]
        expected_set, actual_set = set(expected), set(actual)
        missing = sorted(expected_set - actual_set)
        unexpected = sorted(actual_set - expected_set)

        report["alignment"] = {
            "expected_count": len(expected),
            "actual_count": len(actual),
            "missing_task_ids": missing,
            "unexpected_task_ids": unexpected,
        }

        if missing or unexpected:
            report["ok"] = False
            report["alignment_error"] = (
                f"结果与题单不一致：缺 {len(missing)} 题"
                + (f"（{', '.join(missing[:5])}）" if missing else "")
                + f"、多 {len(unexpected)} 题"
                + (f"（{', '.join(unexpected[:5])}）" if unexpected else "")
            )
        else:
            report["ok"] = proc.returncode == 0
    except ResultsError as exc:
        report["results_read_error"] = str(exc)
        report["ok"] = False

    return report
