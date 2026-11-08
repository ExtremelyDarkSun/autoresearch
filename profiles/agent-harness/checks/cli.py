# GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
# LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
# PURPOSE: agent-harness profile 的离线检查 CLI —— 校验配置、预览评测 argv、读题单/结果、对比两次读数、查轨迹越界与泄漏
# ORIGIN: none（新文件）。命令分组与退出码语义抽取自上游组件 gates 下的 gate_* 系列（0 通过 / 2 没跑 / 9 命中），
#         只保留离线可读的那部分：不联网、不发起评测、不写运行态
# CUMULATIVE-DELTA: 首版。所有子命令都只读输入；唯一的「跑」是 build_command 的 argv 预览（打印，不执行）。

"""agent-harness profile 的离线检查 CLI。

跑法（从仓库根，或任意目录都可以 —— 脚本自己找仓库根）：

    python profiles/agent-harness/checks/cli.py validate
    python profiles/agent-harness/checks/cli.py command --tasks <题单> --output <目录>
    python profiles/agent-harness/checks/cli.py tasklist <题单.jsonl>
    python profiles/agent-harness/checks/cli.py results <task_results.jsonl> --profile profiles/agent-harness
    python profiles/agent-harness/checks/cli.py compare <before.jsonl> <after.jsonl> --profile profiles/agent-harness
    python profiles/agent-harness/checks/cli.py trace <trace_1.json> [trace_2.json ...] --profile profiles/agent-harness
    python profiles/agent-harness/checks/cli.py patch <agent.patch> <reference.patch>

退出码（全子命令统一）：

    0  通过（配置合法 / 结果可读 / 对比可比 / 轨迹干净）
    1  输入或参数错误（配置不合法、结果非法、文件读不到、参数组合错）
    2  未配置（没有评测命令，或检查规则全空 ⇒ 什么都没查，**不是通过**）
    3  检查命中（轨迹越界 / 敏感片段）
    4  两次读数不可比（题集不同或测量配置不同）

⛔ 本 CLI 不执行评测命令。`command` 子命令只把 argv 打印出来，供人复核。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from kernel.autoresearch.adapters import agent_harness as ah  # noqa: E402
from kernel.autoresearch import profiles as profiles_mod  # noqa: E402

DEFAULT_PROFILE = pathlib.Path(__file__).resolve().parents[1]

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_CONFIGURED = 2
EXIT_HIT = 3
EXIT_INCOMPARABLE = 4


# ---------------------------------------------------------------------------
# 输出小工具
# ---------------------------------------------------------------------------


def _dump(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def _fmt_rate(value) -> str:
    if value is None:
        return "n/a（没有可计分的题）"
    return f"{value:.1%}"


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_validate(args) -> int:
    profile = profiles_mod.load_profile(args.profile)
    errors = profiles_mod.validate_profile(profile)
    state = profiles_mod.evaluation_state(profile)
    payload = {
        "profile": profile.get("_profile_path"),
        "id": profile.get("id"),
        "version": profile.get("version"),
        "valid": not errors,
        "errors": errors,
        "required_instructions": profiles_mod.required_instructions(profile),
        "can_run_evaluation": state["can_run"],
        "blocking": state["blocking"],
    }
    if args.json:
        _dump(payload)
    else:
        print(f"profile    : {payload['profile']}")
        print(f"id / 版本  : {payload['id']} / {payload['version']}")
        print(f"必读清单   : {', '.join(payload['required_instructions']) or '（空）'}")
        if errors:
            print(f"校验       : 不通过（{len(errors)} 条）")
            for item in errors:
                print(f"  ✗ {item}")
        else:
            print("校验       : 通过")
        print(f"能否真评测 : {'能' if state['can_run'] else '不能'}")
        for item in state["blocking"]:
            print(f"  · {item}")
    return EXIT_USAGE if errors else EXIT_OK


def cmd_command(args) -> int:
    profile = profiles_mod.load_profile(args.profile)
    state = profiles_mod.evaluation_state(profile)
    if not state["can_run"]:
        if args.json:
            _dump({"configured": state["configured"], "command": state["command"],
                   "blocking": state["blocking"]})
        else:
            print("没有配置评测命令 ⇒ 不发起评测（这是默认状态，不是错误配置）")
            for item in state["blocking"]:
                print(f"  · {item}")
        return EXIT_NOT_CONFIGURED
    argv = profiles_mod.build_command(profile, tasks=args.tasks, output=args.output)
    if args.json:
        _dump({"argv": argv})
    else:
        print("argv（不经过 shell，逐元素打印）：")
        for index, element in enumerate(argv):
            print(f"  [{index}] {element}")
    return EXIT_OK


def cmd_tasklist(args) -> int:
    try:
        result = ah.read_tasklist(args.tasklist, strict=not args.lenient)
    except ah.ResultsError as exc:
        print(f"[非法] {exc}")
        return EXIT_USAGE
    summary = result["summary"]
    if args.json:
        _dump(summary)
        return EXIT_OK
    print(f"题单       : {summary['source']}")
    print(f"题数       : {summary['n_tasks']}")
    if summary["task_ids"]:
        head = ", ".join(summary["task_ids"][:8])
        print(f"题号       : {head}{' …' if summary['n_tasks'] > 8 else ''}")
    if summary["duplicates"]:
        print(f"重复题号   : {summary['duplicates']}")
    if summary["errors"]:
        print(f"问题行     : {len(summary['errors'])} 条")
        for item in summary["errors"]:
            print(f"  ✗ 第 {item['line']} 行 {item['kind']}：{item['detail']}")
    return EXIT_OK


def cmd_results(args) -> int:
    profile = profiles_mod.load_profile(args.profile) if args.profile else None
    try:
        result = ah.read_results(args.results, profile, strict=not args.lenient,
                                 read_sidecar=not args.no_sidecar)
    except (ah.ResultsError, profiles_mod.ProfileError) as exc:
        print(f"[非法] {exc}")
        return EXIT_USAGE
    summary = result["summary"]
    if args.json:
        _dump({k: v for k, v in summary.items() if k != "errors"} | {"errors": summary["errors"]})
        return EXIT_OK
    print(f"结果文件   : {summary['source']}")
    print(f"题数       : {summary['n_tasks']}")
    print(f"通过 / 失败: {summary['n_passed']} / {summary['n_failed']}")
    print(f"环境错误   : {summary['n_infra_error']}")
    print(f"超时       : {summary['n_timed_out']}")
    print(f"通过率     : {_fmt_rate(summary['pass_rate'])}"
          f"（分母 {summary['n_scored']} = 通过 + 失败）")
    if summary["n_unscored"]:
        print(f"           : {summary['n_unscored']} 题未计入（环境错误 / 超时）")
    measurement = summary.get("measurement") or {}
    print(f"测量配置   : limits={measurement.get('limits') or '{}'}"
          f" · schema v{measurement.get('result_schema_version')}"
          + (f" · sidecar={measurement['_sidecar']}" if measurement.get("_sidecar") else ""))
    for note in summary["notes"]:
        print(f"  ! {note}")
    return EXIT_OK


def cmd_compare(args) -> int:
    profile = profiles_mod.load_profile(args.profile) if args.profile else None
    try:
        result = ah.compare_results(
            args.before,
            args.after,
            profile=profile,
            require_same_tasks=not args.allow_different_tasks,
            require_same_measurement=not args.allow_different_measurement,
        )
    except (ah.ResultsError, profiles_mod.ProfileError) as exc:
        print(f"[非法] {exc}")
        return EXIT_USAGE
    if args.json:
        _dump(result)
        return EXIT_OK if result["comparable"] else EXIT_INCOMPARABLE
    print(f"判词       : {result['verdict_zh']}（{result['verdict']}）")
    if result["reasons"]:
        print("不可比原因 :")
        for item in result["reasons"]:
            print(f"  ✗ {item}")
    else:
        rate = result["pass_rate"]
        paired = rate.get("paired") or {}
        if paired.get("n"):
            print(f"通过率(配对): {_fmt_rate(paired['before'])} → {_fmt_rate(paired['after'])}"
                  + (f"（Δ {paired['delta']:+.1%}）" if paired["delta"] is not None else "")
                  + f"　同一分母 {paired['n']} 题")
        print(f"通过率(各自): {_fmt_rate(rate['before'])} → {_fmt_rate(rate['after'])}"
              + (f"（Δ {rate['delta']:+.1%}）" if rate["delta"] is not None else ""))
        print(f"翻正       : {len(result['gained'])} 题 {result['gained'][:8]}")
        print(f"翻负       : {len(result['lost'])} 题 {result['lost'][:8]}")
        print(f"不确定     : {len(result['uncertain'])} 题")
        for item in result["uncertain"][:8]:
            print(f"  ? {item['task_id']}：{item['before']} → {item['after']}（{item['why']}）")
        print(f"保持通过   : {len(result['unchanged']['passed'])} 题"
              f" · 保持失败 {len(result['unchanged']['failed'])} 题")
    for note in result["notes"]:
        print(f"  ! {note}")
    return EXIT_OK if result["comparable"] else EXIT_INCOMPARABLE


def cmd_trace(args) -> int:
    profile = profiles_mod.load_profile(args.profile) if args.profile else None
    exit_code = EXIT_OK
    any_checked = False
    reports = []
    for path in args.traces:
        try:
            trace = ah.load_trace(path)
        except ah.ResultsError as exc:
            print(f"[非法] {exc}")
            return EXIT_USAGE
        report = ah.check_trace(
            trace,
            profile=profile,
            tool_names=args.tool_name or None,
            allowed_roots=args.allowed_root or None,
            forbidden_path_patterns=args.forbid_path or None,
            sensitive_fragments=args.fragment or None,
            ignore_paths=args.ignore_path or None,
            reveal=args.reveal,
        )
        report["source"] = str(path)
        reports.append(report)
        any_checked = any_checked or report["checked"]
        if report["verdict"] == "hit":
            exit_code = EXIT_HIT

    if args.json:
        _dump(reports if len(reports) > 1 else reports[0])
        if not any_checked:
            return EXIT_NOT_CONFIGURED
        return exit_code

    for report in reports:
        print(f"轨迹       : {report['source']}")
        print(f"动作数     : {report['n_actions']} · 工具分布 {report['tools']}")
        if not report["checked"]:
            print("判定       : 未配置（工具名 / 路径 / 敏感片段全空 ⇒ 什么都没查，不算通过）")
            continue
        print(f"判定       : {report['verdict']}"
              + ("（干净）" if report["verdict"] == "clean" else "（命中）"))
        for item in report["violations"]:
            detail = item.get("path") or item.get("tool") or ""
            print(f"  ✗ {item['kind']} · step {item['step']} · {item.get('tool')} · {detail}")
            if item.get("fragment"):
                print(f"      片段 {item['fragment']}（出现 {item['occurrences']} 次，来自 {item['source']}）")
    if not any_checked:
        print("⇒ 没有任何规则被检查过。要查越界就配 [workspace].allowed_roots，"
              "要查泄漏就配 [leak].sensitive_fragments。")
        return EXIT_NOT_CONFIGURED
    return exit_code


def cmd_patch(args) -> int:
    try:
        agent_patch = pathlib.Path(args.agent_patch).read_text(encoding="utf-8-sig", errors="replace")
        reference_patch = pathlib.Path(args.reference_patch).read_text(
            encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        print(f"[非法] 读不到补丁：{exc}")
        return EXIT_USAGE
    report = ah.check_patch_leak(agent_patch, reference_patch,
                                 min_lines=args.min_lines, ratio=args.ratio)
    if args.json:
        _dump(report)
    else:
        print(f"判词       : {report['verdict']}")
        for reason in report["reasons"]:
            print(f"  · {reason}")
        print(f"新增行     : agent {report['agent_lines']} · 参考 {report['reference_lines']}"
              f" · 交集 {report['intersection']}（占 agent {report['ratio']:.0%}）")
        print(f"阈值       : min_lines={report['min_lines']} · ratio={report['ratio_threshold']}"
              "（本工具启发式，非官方规定）")
    if report["verdict"] == "confirmed":
        return EXIT_HIT
    return EXIT_OK


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="agent-harness profile 的离线检查（不联网、不发起评测）",
        epilog="退出码：0 通过 · 1 输入错误 · 2 未配置 · 3 命中 · 4 不可比",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", default=str(DEFAULT_PROFILE),
                        help="profile 文件或目录（默认本 profile 目录）")
    common.add_argument("--json", action="store_true", help="输出 JSON")

    p_validate = sub.add_parser("validate", parents=[common], help="校验 profile")
    p_validate.set_defaults(func=cmd_validate)

    p_command = sub.add_parser("command", parents=[common], help="打印评测 argv（不执行）")
    p_command.add_argument("--tasks", default="<题单文件>")
    p_command.add_argument("--output", default="<结果目录>")
    p_command.set_defaults(func=cmd_command)

    p_tasklist = sub.add_parser("tasklist", parents=[common], help="读题单并查重复/空")
    p_tasklist.add_argument("tasklist")
    p_tasklist.add_argument("--lenient", action="store_true", help="坏行只报告，不报错")
    p_tasklist.set_defaults(func=cmd_tasklist)

    p_results = sub.add_parser("results", parents=[common], help="读结果并给规范化 summary")
    p_results.add_argument("results")
    p_results.add_argument("--lenient", action="store_true", help="坏行只报告，不报错")
    p_results.add_argument("--no-sidecar", action="store_true", help="不读结果旁的 meta.json")
    p_results.set_defaults(func=cmd_results)

    p_compare = sub.add_parser("compare", parents=[common], help="对比两次读数（先查可比性）")
    p_compare.add_argument("before")
    p_compare.add_argument("after")
    p_compare.add_argument("--allow-different-tasks", action="store_true",
                           help="明知题集不同还强行对比（默认禁止）")
    p_compare.add_argument("--allow-different-measurement", action="store_true",
                           help="明知测量配置不同还强行对比（默认禁止）")
    p_compare.set_defaults(func=cmd_compare)

    p_trace = sub.add_parser("trace", parents=[common], help="查轨迹越界与泄漏")
    p_trace.add_argument("traces", nargs="+")
    p_trace.add_argument("--tool-name", action="append", help="允许的工具名，可重复")
    p_trace.add_argument("--allowed-root", action="append", help="允许的工作区根，可重复")
    p_trace.add_argument("--forbid-path", action="append", help="禁区路径前缀或 re: 正则，可重复")
    p_trace.add_argument("--fragment", action="append", help="敏感片段（字面量），可重复")
    p_trace.add_argument("--ignore-path", action="append", help="忽略的路径前缀，可重复")
    p_trace.add_argument("--reveal", action="store_true", help="不遮蔽命中的片段")
    p_trace.set_defaults(func=cmd_trace)

    p_patch = sub.add_parser("patch", parents=[common], help="比对 agent 补丁与参考补丁")
    p_patch.add_argument("agent_patch")
    p_patch.add_argument("reference_patch")
    p_patch.add_argument("--min-lines", type=int, default=30)
    p_patch.add_argument("--ratio", type=float, default=0.5)
    p_patch.set_defaults(func=cmd_patch)

    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 不支持 reconfigure 的流
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except profiles_mod.ProfileError as exc:
        print(f"[配置] {exc}")
        return EXIT_USAGE
    except ah.ResultsError as exc:
        print(f"[数据] {exc}")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
