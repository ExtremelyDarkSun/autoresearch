# GENERATED-BY: 完整功能迁移（持续推进 A1–A7 / 远端进度 H1–H9 / 入向超期 D2）
# LAST-EDIT: 2026-11-09（首版；probe / tick / watch / rearm / outcome / status 六命令）
# PURPOSE: progress 的命令行入口 —— 采集、单轮判定、受管阻塞监视、显式重挂、三出口与只读状态。
# ORIGIN: 上游推进器的 CLI 形态（同一命令多模式、JSON 输出、退出码区分「无需打扰」「叫醒」「失败」）；
#         宿主约定见 kernel/autoresearch/progress.py 的 WATCH_CONTRACT。
# CUMULATIVE-DELTA: 首版。与上游的差异：① watch 是**受管后台作业本体**，一次唤醒即退出，不设固定寿命、
#         不自续、不起替代服务；② 退出码可能被后台作业压成 1，所以同时把 verdict / intended_exit /
#         actual_exit 写进 <state.root>/runs/<job_id>.json 与事件账（A7）；③ probe 不用管道采集，
#         原文落私有工作区文件并回传路径与 sha256；④ rearm 必须显式执行，未重挂不许再挂第二个作业。

"""progress 命令行（父 agent 从这里接）。

退出码（⛔ 后台作业可能把它们压成 1：真值一律读 JSON 与 runs/<job_id>.json）：

    0  成功（probe 无事件 / tick 无需唤醒 / watch 已唤醒 / rearm·outcome / status 健康）
    3  probe 有变化 / tick 已唤醒 / status 需要动作（重挂、采集失败、超期）
    4  采集失败（fail-closed；watch/tick 也表示「因采集失败而唤醒」）
    5  watch 到达调用方给的轮数上限（**没有唤醒**，已写 needs_rearm）
    6  拿不到租约（未启动）或运行中丢租约
    7  还有未重挂的唤醒（拒绝再挂第二个作业）
    9  不可判（goal 未知 / 覆盖不确切 / status 不绿）
    2  spec 或参数被拒
    1  运行异常

⛔ 本 CLI **不会**自己起替代服务、不会自续、不会伪造「宿主已被唤醒」。
`watch` 只在被人（或人的受管作业）显式启动时运行；进程结束由宿主通知负责人才会醒。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许直接当脚本跑（研究侧常用写法）
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kernel.autoresearch import progress  # noqa: E402


def _load_json_file(path, label):
    if not path:
        return None
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise progress.ProgressError(f"{label} 文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise progress.ProgressError(f"{label} 不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(document, dict):
        raise progress.ProgressError(f"{label} 顶层必须是对象：{path}")
    return document


def _load_adapter(path):
    """注入适配器：模块里可以有 `goal_state()` / `overdue()` 两个无参函数。"""
    if not path:
        return None, None
    target = Path(path)
    if not target.is_file():
        raise progress.ProgressError(f"adapter 不存在：{path}")
    spec = importlib.util.spec_from_file_location(f"progress_adapter_{abs(hash(str(target))) & 0xFFFF}",
                                                  target)
    if spec is None or spec.loader is None:
        raise progress.ProgressError(f"adapter 载入失败：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    goal = getattr(module, "goal_state", None)
    overdue = getattr(module, "overdue", None)
    if goal is not None and not callable(goal):
        raise progress.ProgressError("adapter.goal_state 必须是可调用对象")
    if overdue is not None and not callable(overdue):
        raise progress.ProgressError("adapter.overdue 必须是可调用对象")
    return goal, overdue


def _build_progress(args):
    spec = progress.load_spec(args.spec) if args.spec else None
    goal, overdue = _load_adapter(args.adapter)
    static_goal = _load_json_file(args.goal_json, "goal")
    static_overdue = _load_json_file(args.overdue_json, "overdue")
    if goal is None and static_goal is not None:
        goal = static_goal
    if overdue is None and static_overdue is not None:
        overdue = static_overdue
    return progress.Progress(
        args.root, spec, spec_path=args.spec if args.spec else None,
        db_path=args.db, owner=args.owner, goal=goal, overdue=overdue,
        ttl_sec=args.ttl,
    )


def _emit(result, code, out_path=None):
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text + "\n", encoding="utf-8", newline="\n")
        target.replace(temporary)
    print(text, flush=True)
    return code


def _add_common(parser, *, suppress):
    """公共参数：既可写在子命令前，也可写在子命令后（宿主脚本两种写法都有）。"""
    def default(value):
        return argparse.SUPPRESS if suppress else value

    parser.add_argument("--root", default=default("."),
                        help="工作区根（状态根与事件库默认落在它下面）")
    # ⚠️ 顶层不设 required：宿主脚本常把公共参数写在子命令后面；缺 spec 在入口处判（退出码 2）
    parser.add_argument("--spec", default=default(None),
                        help="声明式 spec（JSON）；主机与路径都写在这里")
    parser.add_argument("--db", default=default(None), help="覆盖 spec.state.db（事件库路径）")
    parser.add_argument("--owner", default=default(None), help="负责人标识（默认取 spec.owner）")
    parser.add_argument("--goal-json", default=default(None),
                        help="goal 状态快照 JSON（{state, coverage, why}）；也可用 --adapter 注入")
    parser.add_argument("--overdue-json", default=default(None),
                        help="入向超期快照 JSON（{max_age_sec, items:[{id,age_sec}]}）；"
                             "不给则该维度标 unverified")
    parser.add_argument("--adapter", default=default(None),
                        help="注入模块：可定义 goal_state() 与 overdue() 两个无参函数")
    parser.add_argument("--ttl", type=float, default=default(90.0),
                        help="租约 TTL（秒，默认 90）")
    parser.add_argument("--out", default=default(None), help="把本次 JSON 结果原子写一份到这个路径")


def _sub_common():
    parser = argparse.ArgumentParser(add_help=False)
    _add_common(parser, suppress=True)
    return parser


def build_parser():
    parser = argparse.ArgumentParser(
        prog="progress",
        description="通用进度探针与受管推进器：采集外部变化、该叫醒时叫一次、然后退出并等显式重挂。")
    _add_common(parser, suppress=False)

    sub = parser.add_subparsers(dest="command", required=True,
                                metavar="{probe,tick,watch,rearm,outcome,status}")
    common = _sub_common()

    sub.add_parser("probe", parents=[common],
                   help="只跑一轮采集 + 相邻比较（不判定、不取租约）")

    tick = sub.add_parser("tick", parents=[common],
                          help="单轮：采集 + 判定 + 落账；需要唤醒就写 wake 记录")
    tick.add_argument("--hold-lease", action="store_true",
                      help="本轮结束后不释放租约（调试用；正常 tick 会释放）")

    watch = sub.add_parser(
        "watch", parents=[common],
        help="阻塞监视（受管后台作业本体）：一次唤醒即退出并写 needs_rearm；不设固定寿命")
    watch.add_argument("--poll", type=float, default=None, help="轮询间隔（秒，默认取 spec.poll_sec）")
    watch.add_argument("--max-rounds", type=int, default=0,
                       help="调用方显式给的轮数上限（0 = 不限；到了写 exhausted + needs_rearm，"
                            "⛔ 不假装成功）")

    rearm = sub.add_parser("rearm", parents=[common],
                           help="owner 显式重挂：清 needs_rearm、代次 +1、给出下一条 watch 命令")
    rearm.add_argument("--note", default=None, help="本轮/本次处理的一句话说明")
    rearm.add_argument("--job", default=None, help="可选：新作业号（受管宿主给的 id）")

    outcome = sub.add_parser(
        "outcome", parents=[common],
        help="一次唤醒的出口：advance / nothing(+next_condition) / problem")
    outcome.add_argument("--kind", required=True, choices=list(progress.OUTCOME_KINDS),
                         help="出口种类：advance（推进）/ nothing（无事+下次条件）/ problem（登记问题）")
    outcome.add_argument("--action", default=None, help="advance 必填：做了什么")
    outcome.add_argument("--next-condition", default=None,
                         help="nothing 必填：下次判定条件（不是「无事」，是条件）")
    outcome.add_argument("--problem", default=None, help="problem 必填：登记的新问题")
    outcome.add_argument("--source", action="append", default=[], metavar="REF",
                         help="来源（路径或 event:<seq>，可重复）")
    outcome.add_argument("--note", default=None, help="补充说明")

    sub.add_parser("status", parents=[common],
                   help="只读状态：租约、最近观测、静默窗口、goal/overdue、needs_rearm、阻塞项")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        instance = _build_progress(args)
        if args.command == "probe":
            result = instance.probe()
            if not result["ok"]:
                return _emit(result, progress.EXIT_CODES["collect_failed"], args.out)
            code = progress.EXIT_CODES["health_action"] if result["changes"] else progress.EXIT_CODES["ok"]
            return _emit(result, code, args.out)

        if args.command == "tick":
            result = instance.tick(hold_lease=args.hold_lease)
            verdict = result.get("verdict")
            if verdict == "pending_rearm":
                return _emit(result, progress.EXIT_CODES["pending_rearm"], args.out)
            if verdict == "lease_busy":
                return _emit(result, progress.EXIT_CODES["lease"], args.out)
            if verdict == "wake":
                return _emit(result, result.get("intended_exit",
                                                progress.EXIT_CODES["ok"]), args.out)
            if verdict == "undecidable":
                return _emit(result, progress.EXIT_CODES["undecidable"], args.out)
            return _emit(result, progress.EXIT_CODES["ok"], args.out)

        if args.command == "watch":
            print("[progress] watch 启动：阻塞进程 · 自己叫不醒宿主 · 一次唤醒即退出并写 needs_rearm；"
                  "退出后由宿主作业通知负责人，再显式 rearm 挂回（⛔ 本进程不自续、不起替代服务）",
                  file=sys.stderr, flush=True)
            result = instance.watch(poll=args.poll, max_rounds=args.max_rounds)
            code = int(result.get("intended_exit", progress.EXIT_CODES["error"]))
            if verdict_needs_exit_record(result):
                instance.record_watch_exit(result, code)
                result["actual_exit"] = code
            return _emit(result, code, args.out)

        if args.command == "rearm":
            result = instance.rearm(note=args.note, job_id=args.job)
            return _emit(result, progress.EXIT_CODES["ok"], args.out)

        if args.command == "outcome":
            result = instance.outcome(
                args.kind, action=args.action, next_condition=args.next_condition,
                problem=args.problem, note=args.note, sources=args.source or None)
            return _emit(result, progress.EXIT_CODES["ok"], args.out)

        if args.command == "status":
            result = instance.status()
            blockers = set(result.get("blockers") or [])
            if blockers & {"goal_state_unknown", "goal_coverage_partial", "overdue_unknown"}:
                return _emit(result, progress.EXIT_CODES["undecidable"], args.out)
            return _emit(result, progress.EXIT_CODES["ok"] if result["ok"]
                         else progress.EXIT_CODES["health_action"], args.out)

        raise progress.ProgressError(f"不支持的命令：{args.command}")
    except progress.SpecError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["spec"]
    except progress.ProgressError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["spec"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["error"]
    except Exception as exc:  # noqa: BLE001 - 入口一律留结构化错误，不把栈打到用户屏幕上
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["error"]


def verdict_needs_exit_record(result) -> bool:
    """只有真的跑过 watch（有 job_id）才补 actual_exit；拒绝启动那几种没有作业记录。"""
    return bool(result.get("job_id"))


if __name__ == "__main__":
    raise SystemExit(main())
