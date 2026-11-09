# GENERATED-BY: 完整功能迁移（持续推进 A1–A7 / 远端进度 H1–H9 / 入向超期 D2 / 迭代节奏 §0.6）
# LAST-EDIT: 2026-11-09（D2 出口闸 + preflight / next-round / schedule 三命令）
# PURPOSE: progress 的命令行入口 —— 采集、单轮判定、受管阻塞监视、显式重挂、三出口、只读状态，
#          以及下一轮只读 preflight 与跨专题四阶段排序。
# ORIGIN: 上游推进器的 CLI 形态（同一命令多模式、JSON 输出、退出码区分「无需打扰」「叫醒」「失败」）；
#         宿主约定见 kernel/autoresearch/progress.py 的 WATCH_CONTRACT。
# CUMULATIVE-DELTA: 首版。与上游的差异：① watch 是**受管后台作业本体**，一次唤醒即退出，不设固定寿命、
#         不自续、不起替代服务；② 退出码可能被后台作业压成 1，所以同时把 verdict / intended_exit /
#         actual_exit 写进 <state.root>/runs/<job_id>.json 与事件账（A7）；③ probe 不用管道采集，
#         原文落私有工作区文件并回传路径与 sha256；④ rearm 必须显式执行，未重挂不许再挂第二个作业。
#   2026-11-09：⑤ `outcome --kind advance` 在落账前核只读 overdue（D2），不可判 / 超期都拒（退出码 9），
#         安全收尾用显式 `--safe-action`；⑥ 新增只读 `preflight` / `next-round` / `schedule`，全过才给
#         `next_command`，⛔ 不生成计划、不写盘、不实发任务。

"""progress 命令行（父 agent 从这里接）。

退出码（⛔ 后台作业可能把它们压成 1：真值一律读 JSON 与 runs/<job_id>.json）：

    0  成功（probe 无事件 / tick 无需唤醒 / watch 已唤醒 / rearm·outcome / preflight 全过 /
       next-round ready / schedule 有可推荐候选 / status 健康）
    3  probe 有变化 / tick 已唤醒 / status 需要动作（重挂、采集失败、超期）
    4  采集失败（fail-closed；watch/tick 也表示「因采集失败而唤醒」）
    5  watch 到达调用方给的轮数上限（**没有唤醒**，已写 needs_rearm）
    6  拿不到租约（未启动）或运行中丢租约
    7  还有未重挂的唤醒（拒绝再挂第二个作业）
    9  不可判 / 规则拒绝（goal 未知、覆盖不确切、status 不绿；`outcome advance` 遇超期或
       超期来源不可判；preflight / next-round / schedule 未全过 ⇒ 不给命令）
    2  spec 或参数被拒
    1  运行异常

只读子命令（`preflight` / `next-round` / `schedule` / `status`）不建库、不建目录、不写事件账：
它们只用 `ReadonlyStore`、`Workflow(readonly=True)`、`Monitoring.check_start`（零写）、
`Admission.check`（只读）与只读 overdue 源。⛔ 它们不会替你起进程、开机器或发任务。

`preflight` / `next-round` 需要显式给 `--next-plan`（工作区内相对路径）、`--topic`、
`--owner-session`、意图声明（`--intent-id` 或 `--intent-none`）与 `--` 之后的完整 argv；
`schedule` 需要显式候选（`--entries` 文件或可重复 `--entry-json`）。

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


def _split_argv(tokens):
    """`--` 之后原样作为被启动进程的 argv（⛔ 不经过 argparse 改写）。"""
    if "--" in tokens:
        index = tokens.index("--")
        return tokens[:index], tokens[index + 1:]
    return tokens, None


def _intents_from(args):
    """显式意图声明；`--intent-none` 与 `--intent-id` 由 argparse 互斥组保证只给一个。"""
    intent_id = getattr(args, "intent_id", None)
    if getattr(args, "intent_none", False):
        return {"none": True}
    if intent_id:
        return {"intent_id": intent_id}
    return None


def _resources_from(args):
    raw = getattr(args, "resources_json", None)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise progress.SpecError(f"--resources-json 不是合法 JSON：{exc}") from exc


def _schedule_entries(args):
    entries = []
    if getattr(args, "entries", None):
        target = Path(args.entries)
        try:
            document = json.loads(target.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise progress.ProgressError(f"--entries 文件不存在：{target}") from exc
        except json.JSONDecodeError as exc:
            raise progress.ProgressError(f"--entries 不是合法 JSON：{exc}") from exc
        if not isinstance(document, list):
            raise progress.ProgressError("--entries 文件必须是候选数组")
        entries.extend(document)
    for text in getattr(args, "entry_json", []) or []:
        try:
            entries.append(json.loads(text))
        except json.JSONDecodeError as exc:
            raise progress.ProgressError(f"--entry-json 不是合法 JSON：{exc}") from exc
    if not entries:
        raise progress.ProgressError("必须给 --entries <文件> 或至少一个 --entry-json")
    return entries


def _build_progress(args, *, d2=False):
    spec = progress.load_spec(args.spec) if args.spec else None
    if d2:
        # D2 生产入口固定接 workspace 库的可信只读来源；⛔ --overdue-json / --adapter
        # 不能当旁路（连 adapter 模块都不执行，避免自定义代码参与 D2 判定）。
        workspace = Path(getattr(args, "workspace", None) or args.root).resolve()
        runtime_db = workspace / ".autoresearch" / "runtime.sqlite3"
        return progress.Progress(
            args.root, spec, spec_path=args.spec if args.spec else None,
            db_path=args.db, owner=args.owner,
            goal=_load_json_file(args.goal_json, "goal"),
            overdue=_trusted_overdue_provider(runtime_db), ttl_sec=args.ttl)
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


def _trusted_overdue_provider(runtime_db):
    """D2 固定来源：`progress_host.read_overdue(workspace runtime db)`（只读、已认证入向/回执）。"""
    try:
        from kernel.autoresearch.progress_host import read_overdue
    except Exception:  # noqa: BLE001 - 拿不到可信来源 ⇒ 返回 None（unknown，⛔ 不假绿）
        return None

    def provider():
        return read_overdue(runtime_db)

    return provider


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
                             "⚠️ 只作 probe/tick/watch/status 诊断：outcome advance / preflight / "
                             "next-round / schedule 固定接 workspace 库的只读 read_overdue，"
                             "⛔ 静态快照不能当 D2 放行依据")
    parser.add_argument("--adapter", default=default(None),
                        help="注入模块：可定义 goal_state() 与 overdue() 两个无参函数；"
                             "⚠️ 它的 overdue() 只作诊断，D2 生产入口不用它")
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
                                metavar="{probe,tick,watch,rearm,outcome,status,"
                                        "preflight,next-round,schedule}")
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
        help="一次唤醒的出口：advance / nothing(+next_condition) / problem（advance 先核 D2 超期）")
    outcome.add_argument("--kind", required=True, choices=list(progress.OUTCOME_KINDS),
                         help="出口种类：advance（推进）/ nothing（无事+下次条件）/ problem（登记问题）")
    outcome.add_argument("--action", default=None, help="advance 必填：做了什么")
    outcome.add_argument("--next-condition", default=None,
                         help="nothing 必填：下次判定条件（不是「无事」，是条件）")
    outcome.add_argument("--problem", default=None, help="problem 必填：登记的新问题")
    outcome.add_argument("--source", action="append", default=[], metavar="REF",
                         help="来源（路径或 event:<seq>，可重复）")
    outcome.add_argument("--note", default=None, help="补充说明")
    outcome.add_argument("--safe-action", default=None, choices=list(progress.SAFE_ACTIONS),
                         help="advance 的安全收尾声明（L10：collect/stop/package/rollback/halt/"
                              "checkpoint）；它跳过超期闸，但⛔ 不能同时声明下一轮")
    outcome.add_argument("--next-plan", default=None,
                         help="advance 可选：显式下一轮计划（工作区内相对路径或 JSON 文件）；"
                              "五件必须同时给（next-plan/topic/owner-session/intent/argv）")
    outcome.add_argument("--topic", default=None, help="下一轮专题（与 --next-plan 同时给）")
    outcome.add_argument("--owner-session", default=None, help="下一轮负责人会话（同时给）")
    intent = outcome.add_mutually_exclusive_group()
    intent.add_argument("--intent-id", default=None, help="下一轮专项意图编号（同时给）")
    intent.add_argument("--intent-none", action="store_true",
                        help="下一轮显式声明没有专项意图（同时给）")

    for name, help_text in (
        ("preflight", "只读预检：全过才给可执行的下一轮 launch 命令；⛔ 不写盘、不起进程"),
        ("next-round", "跨轮下一轮：preflight + 出口/下一动作/用户停止检查；一次只推进一轮"),
    ):
        node = sub.add_parser(name, parents=[common], help=help_text,
                              epilog="`--` 之后是完整的下一轮 argv（必须与事前声明逐项相等）。")
        node.add_argument("--next-plan", required=True,
                          help="下一轮计划：工作区内相对路径（CLI 需要路径才能给命令）")
        node.add_argument("--topic", required=True, help="下一轮专题")
        node.add_argument("--owner-session", required=True, help="下一轮负责人会话")
        intent = node.add_mutually_exclusive_group(required=True)
        intent.add_argument("--intent-id", default=None, help="专项意图编号（必须等于监控真实当前 id）")
        intent.add_argument("--intent-none", action="store_true",
                            help="显式声明当前没有专项意图（监控必须是 none/closed）")
        node.add_argument("--stage", default=None, choices=list(progress.STAGES),
                          help="显式研究阶段（缺省时读计划 research.stage / stage / execution.stage）")
        node.add_argument("--resources-json", default=None,
                          help="显式资源声明（JSON：字符串数组或 {keys,gpu_units,machine,exclusive}）")
        node.add_argument("--workspace", default=None,
                          help="研究工作区（默认取 --root；两者通常是同一个）")
        if name == "next-round":
            node.add_argument("--user-stop", action="store_true",
                              help="调用方确认用户已停止：不推进、不重挂、不给命令")

    schedule = sub.add_parser(
        "schedule", parents=[common],
        help="只读跨专题四阶段排序：runnable/blocked/unknown + 逐项命令；⛔ 不实发任务、不开机")
    schedule.add_argument("--entries", default=None, help="候选数组 JSON 文件")
    schedule.add_argument("--entry-json", action="append", default=[], metavar="JSON",
                          help="单个候选 JSON（可重复）；候选键：topic/next_plan/owner/intents/"
                               "argv/stage/resources/note")
    schedule.add_argument("--stage-order", default=None,
                          help="四阶段顺序（逗号分隔；默认 mechanism,composition,large,paired）")
    schedule.add_argument("--user-stop", action="store_true",
                          help="调用方确认用户已停止：不排序、不给命令")
    schedule.add_argument("--workspace", default=None,
                          help="研究工作区（默认取 --root）")

    sub.add_parser("status", parents=[common],
                   help="只读状态：租约、最近观测、静默窗口、goal/overdue、needs_rearm、阻塞项")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    tokens = list(sys.argv[1:] if argv is None else argv)
    head, tail = _split_argv(tokens)
    args = parser.parse_args(head)
    try:
        d2 = args.command in ("preflight", "next-round", "schedule") or (
            args.command == "outcome" and getattr(args, "kind", None) == "advance")
        instance = _build_progress(args, d2=d2)
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
                problem=args.problem, note=args.note, sources=args.source or None,
                safe_action=args.safe_action,
                next_plan=args.next_plan, topic=args.topic, owner=args.owner_session,
                intents=_intents_from(args), argv=tail)
            return _emit(result, progress.EXIT_CODES["ok"], args.out)

        if args.command in ("preflight", "next-round"):
            keyword = dict(
                next_plan=args.next_plan, topic=args.topic, owner=args.owner_session,
                intents=_intents_from(args), argv=tail, workspace=args.workspace,
                stage=args.stage, resources=_resources_from(args))
            if args.command == "preflight":
                result = instance.preflight(**keyword)
            else:
                result = instance.next_round(user_stop=args.user_stop, **keyword)
            code = (progress.EXIT_CODES["ok"] if result.get("ok")
                    else progress.EXIT_CODES["undecidable"])
            return _emit(result, code, args.out)

        if args.command == "schedule":
            order = None
            if args.stage_order:
                order = [part.strip() for part in args.stage_order.split(",") if part.strip()]
            result = instance.schedule(
                entries=_schedule_entries(args), workspace=args.workspace,
                user_stop=args.user_stop, stage_order=order)
            code = (progress.EXIT_CODES["ok"] if result.get("ok")
                    else progress.EXIT_CODES["undecidable"])
            return _emit(result, code, args.out)

        if args.command == "status":
            result = instance.status()
            blockers = set(result.get("blockers") or [])
            if blockers & {"goal_state_unknown", "goal_coverage_partial", "overdue_unknown"}:
                return _emit(result, progress.EXIT_CODES["undecidable"], args.out)
            return _emit(result, progress.EXIT_CODES["ok"] if result["ok"]
                         else progress.EXIT_CODES["health_action"], args.out)

        raise progress.ProgressError(f"不支持的命令：{args.command}")
    except progress.OutcomeBlocked as exc:
        # D2 规则拒绝：一定没有落 advance 记录。用「不可判 / 规则拒绝」退出码（9）。
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["undecidable"]
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
