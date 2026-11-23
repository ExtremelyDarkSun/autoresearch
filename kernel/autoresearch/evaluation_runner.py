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

import math
import subprocess
from pathlib import Path

from .adapters.agent_harness import ResultsError, read_results, read_tasklist
from .profiles import build_command, evaluation_state, load_profile

#: 默认单次评测超时（秒）。profile 里写了 total_budget_minutes 时以它为准。
DEFAULT_TIMEOUT_SECONDS = 3600.0


class EvaluationRunError(RuntimeError):
    """评测无法发起（配置不合规、执行器缺失等）。"""


def _results_file_exists(output, profile) -> bool:
    """结果文件到底存不存在？用来区分「没写」与「写了但读不了」。"""
    evaluation = profile.get("evaluation") if isinstance(profile, dict) else None
    name = "task_results.jsonl"
    if isinstance(evaluation, dict) and isinstance(evaluation.get("result_file"), str):
        name = evaluation["result_file"]
    return (Path(output) / name).is_file()


def resolve_budget(profile: dict, timeout) -> dict:
    """把 `[limits].total_budget_minutes` 与调用方给的 `timeout` 合成为一个生效值。

    为什么需要这一步：`total_budget_minutes` 的语义是「整批总预算」，但此前**没有任何
    代码读它** —— 写进 TOML 等于没写。这类「配置里声明了、实际没人执行」的键是最容易
    被当成已生效的一类假绿（`per_task_minutes` 之前正是同一个毛病）。

    规则：

    * 没给 `timeout`、profile 也没声明 ⇒ `DEFAULT_TIMEOUT_SECONDS`
    * 没给 `timeout`、profile 声明了 ⇒ 用声明值
    * 给了 `timeout`、没声明 ⇒ 用给的
    * 给了 `timeout`、也声明了，且给的**超过**声明 ⇒ **夹到声明值**并留痕
      （`clamped=True`）。宁可少跑一会儿，也不静默超支：预算是红线，不是建议。

    返回 `{"declared_total_minutes", "requested_seconds", "effective_timeout_seconds",
    "clamped", "why"}`，原样进报告，事后能复盘「这次为什么只跑了 60 秒」。
    """
    limits = profile.get("limits") if isinstance(profile.get("limits"), dict) else {}
    declared_minutes = limits.get("total_budget_minutes")
    declared_seconds = None
    if isinstance(declared_minutes, (int, float)) and not isinstance(declared_minutes, bool):
        # nan / inf / 0 / 负数都不算「声明了预算」。放到 subprocess 里会变成
        # `OverflowError: timestamp out of range` 这种与真实原因无关的报错。
        if not math.isfinite(float(declared_minutes)) or declared_minutes <= 0:
            raise EvaluationRunError(
                f"[limits].total_budget_minutes 必须是有限正数，实际是 {declared_minutes!r}"
            )
        declared_seconds = float(declared_minutes) * 60.0

    requested = None if timeout is None else float(timeout)
    if requested is not None and (not math.isfinite(requested) or requested <= 0):
        # 与 tools/run_eval.py 的 --timeout 同一套判据：nan / inf / 0 / 负数一律不收。
        # 否则 inf 会在 subprocess 里变成 OverflowError，0 会立刻超时且难以归因。
        raise EvaluationRunError(
            f"timeout 必须是有限正数（秒），实际是 {timeout!r}"
        )

    if requested is None and declared_seconds is None:
        effective = DEFAULT_TIMEOUT_SECONDS
        why = "未指定 timeout，profile 也未声明 total_budget_minutes ⇒ 用默认值"
    elif requested is None:
        effective = declared_seconds
        why = "未指定 timeout ⇒ 采用 profile 声明的 total_budget_minutes"
    elif declared_seconds is None:
        effective = requested
        why = "profile 未声明 total_budget_minutes ⇒ 采用调用方给的 timeout"
    elif requested > declared_seconds:
        effective = declared_seconds
        why = (
            f"调用方给的 {requested:g} 秒超过 profile 声明的总预算 {declared_seconds:g} 秒"
            " ⇒ 夹到声明值（预算优先于调用方参数）"
        )
    else:
        effective = requested
        why = "调用方给的 timeout 在 profile 声明的总预算之内"

    return {
        "declared_total_minutes": declared_minutes if declared_seconds is not None else None,
        "requested_seconds": requested,
        "effective_timeout_seconds": effective,
        "clamped": bool(
            requested is not None
            and declared_seconds is not None
            and requested > declared_seconds
        ),
        "why": why,
    }


def run_evaluation(
    profile_path,
    *,
    tasks: str,
    output: str,
    timeout: float | None = None,
    cwd: str | None = None,
) -> dict:
    """按 profile 的 `[evaluation].command` 执行一次评测，并读回结果。

    参数
    ----
    profile_path : profile.toml 的路径（文件或目录）
    tasks : 题单路径；会替换 `command` 模板里的 `{tasks}`
    output : 结果目录；会替换 `{output}`，结果文件按 profile 的 `result_file` 落在这里
    timeout : 整次评测的超时秒数。`None` 表示「按 profile 的 total_budget_minutes，
              没声明就用默认值」；显式给了但超过声明预算会被夹到声明值，并在报告的
              `budget` 字段里留痕
    cwd : 子进程工作目录；默认沿用当前目录

    返回
    ----
    一个可直接 dump 成 JSON 的 dict，至少含 `ok` / `executed` / `argv` / `verdict`。

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

    budget = resolve_budget(profile, timeout)
    timeout = budget["effective_timeout_seconds"]

    report: dict = {
        "ok": False,
        "executed": False,
        "profile": profile.get("id"),
        "argv": argv,
        "tasks": str(tasks),
        "output": str(output),
        "timeout_seconds": timeout,
        "budget": budget,
        "exit": None,
        "timed_out": False,
        "stdout_tail": None,
        "stderr_tail": None,
        "results": None,
        "results_read_error": None,
        "alignment": None,
        "alignment_error": None,
        # `ok` 是个 bool，装不下「为什么不是」。verdict 给出一句能直接看懂的归类：
        #   all_passed      评测跑完，题全通过
        #   not_all_passed  评测跑完、覆盖也齐，但有题没通过（**不是故障**，是读数）
        #   misaligned      结果覆盖与题单对不上（缺题/多题），读数不可用
        #   timed_out       整次评测超时
        #   launch_failed   评测进程起不来
        #   no_results      跑完了但读不到结果文件
        "verdict": "not_run",
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
        report["verdict"] = "timed_out"
        return report
    except OSError as exc:
        report["stderr_tail"] = f"无法启动评测进程：{type(exc).__name__}: {exc}"
        report["verdict"] = "launch_failed"
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
    except ResultsError as exc:
        report["results_read_error"] = str(exc)
        report["ok"] = False
        # 「没写结果」和「写的结果读不了」是两件事，操作者的下一步动作完全不同：
        #   没写   → 去看执行器的输出路径 / result_file 配置对不对
        #   读不了 → 去看执行器写出来的格式（状态值、task_id 类型、非法 JSON 行）
        # 早先两种情况都报 no_results，等于把诊断信息抹平了。
        report["verdict"] = "results_invalid" if _results_file_exists(output, profile) else "no_results"
        return report

    report["results"] = results
    summary = results.get("summary")
    if not isinstance(summary, dict):
        summary = None

    # 第二层假绿（副机 L57）：`ok=false` 之后，`report["results"]["summary"]["pass_rate"]`
    # 仍然是 1.0 —— 任何拿 summary 当读数的下游照样看到「全通过」。所以对齐结论必须
    # 同时注入 summary：对不上就把 pass_rate 置 None（原值留 pass_rate_raw）。
    try:
        tasklist = read_tasklist(tasks)
    except ResultsError as exc:
        # 结果读到了、题单读不到 ⇒ 无法确认覆盖是否完整，pass_rate 自然不可判定。
        if summary is not None:
            summary["aligned"] = False
            # 只在没有原值时补：`read_results()` 若已因 sidecar 对齐置过 raw，那是更早、
            # 更接近数据的一手记录，不能被这里用「已经变成 None 的 pass_rate」覆盖掉。
            summary.setdefault("pass_rate_raw", summary.get("pass_rate"))
            summary["pass_rate"] = None
            summary["pass_rate_basis"] = f"题单读取失败，无法确认对齐：{exc}"
        report["alignment_error"] = f"读不到输入题单，无法确认结果覆盖完整：{exc}"
        report["ok"] = False
        report["verdict"] = "misaligned"
        return report

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

    if summary is not None:
        summary["aligned"] = not missing and not unexpected
        # ⚠️ 必须 setdefault，不能直接赋值。
        #
        # `read_results()` 在有 sidecar（真实跑过 `tools/run_eval.py` 的形状）时，已经
        # 因为题单对不上把 `pass_rate` 置成 None、并把**原值 1.0** 存进 `pass_rate_raw`。
        # 这里若写 `summary["pass_rate_raw"] = summary.get("pass_rate")`，拿到的是 None，
        # 会把那一手记录覆盖掉 —— 结果就是 raw 也变成 null，谁都不知道原来是 1.0。
        # 副机 L61 第 2 步实测踩到的就是这个（stub 不写 sidecar 时反而正常，所以只有
        # 「真实执行器 + 丢题」这一条路径会暴露）。
        summary.setdefault("pass_rate_raw", summary.get("pass_rate"))
        summary["alignment"] = dict(report["alignment"])

    if missing or unexpected:
        report["ok"] = False
        report["verdict"] = "misaligned"
        report["alignment_error"] = (
            f"结果与题单不一致：缺 {len(missing)} 题"
            + (f"（{', '.join(missing[:5])}）" if missing else "")
            + f"、多 {len(unexpected)} 题"
            + (f"（{', '.join(unexpected[:5])}）" if unexpected else "")
        )
        if summary is not None:
            summary["pass_rate"] = None
            summary["pass_rate_basis"] = (
                f"题单未对齐，pass_rate 不可用（缺 {len(missing)}、多 {len(unexpected)}）；"
                "原值见 pass_rate_raw"
            )
    else:
        report["ok"] = proc.returncode == 0
        # `verdict` 描述的是**结果**，不是进程的退出码。
        #
        # 早先这里写成「exit==0 ⇒ all_passed」，于是「3 题全 failed、执行器仍 exit 0」
        # 会被报成 all_passed —— 而 pass_rate 明明是 0.0。名字里带 passed，却是靠
        # 退出码推出来的，这就是最典型的一类假绿：判据看的是进程脸色，不是数据。
        # 反过来，「全部通过但执行器按约定 exit 1」也会被误报成 launch_failed。
        #
        # 现在只认数据：n_passed 与 n_tasks 都在，就按它们判；拿不到读数才退回退出码。
        n_passed = (summary or {}).get("n_passed")
        n_tasks = (summary or {}).get("n_tasks")
        if isinstance(n_passed, int) and isinstance(n_tasks, int):
            report["verdict"] = "all_passed" if n_passed >= n_tasks else "not_all_passed"
        else:
            report["verdict"] = "all_passed" if proc.returncode == 0 else "not_all_passed"

    return report
