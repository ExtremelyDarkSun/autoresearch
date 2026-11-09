# GENERATED-BY: research execution main chain migration, 2026-11-09
# LAST-EDIT: 2026-11-09，review默认要求版本2；显式legacy-record保留历史未知信息
# PURPOSE: `workflow.py` 的命令行入口。**生产接线固定**：监控接 `Monitoring(workspace, host=DSHHost())`，
#          审计核验接 `DSHHost()`；⛔ 不提供跳过闸的开关（没有 --allow-unwired-monitor / --monitor-module）。
# ORIGIN: 抽取自 AutoDL实验/tool/exp_launch.py 的「先检查后起跑 + 保留真实退出码」入口形态；无比赛/渠道值。
# CUMULATIVE-DELTA: 首版。所有文件参数都是工作区内相对路径；argv 一律经 `--` 传入。
#   本轮补：`check` 是明确只读入口 —— 只读 Store / 只读决策服务 / `Workflow(readonly=True)`，
#   不建库、不建目录、不迁移 schema；缺库 / 缺表 / 旧 schema 返回 `undecidable`（退出码 2）。

"""研究执行主链的命令行入口。

用法（参数的准是 `--help`）：

    python -m kernel.autoresearch.workflow_cli prepare --workspace W --topic T --plan P \\
        --owner-session S [--source P ...] [--source-file F] [--actor A]

    python -m kernel.autoresearch.workflow_cli check  --workspace W --topic T --plan P \\
        --owner-session S (--intent-id I | --intent-none) -- ARGV...

    python -m kernel.autoresearch.workflow_cli launch （同 check）

    python -m kernel.autoresearch.workflow_cli finish --workspace W --run-id R --exitcode N \\
        --reason {completed,failed,interrupted,stopped_by_user,launch_error,timeout} \\
        [--documentation-status ok|failed|skipped --documentation-detail TEXT]

    python -m kernel.autoresearch.workflow_cli review --workspace W --run-id R --file review.json

    python -m kernel.autoresearch.workflow_cli status --workspace W [--topic T] [--plan-id P]

退出码：

    0   通过 / 已登记
    9   规则拒绝（闸或前置不过）—— **一定没有启动任何子进程**
    2   输入或数据错误（文件缺失、JSON 坏、run 不存在、工作区未初始化）
    124 子进程超过 `plan.budget.max_seconds` 被终止（已按 timeout 记录）
    130 被 Ctrl-C 中断（已按 interrupted 记录结束原因）
    launch 通过时：**被启动进程的真实退出码**（原值，负值表示被信号终止）

三条生产纪律写在代码里、不是可选参数：
1. 监控检查由 `Monitoring(workspace, host=DSHHost()).check_start` 提供；ImportError 或构建失败
   ⇒ 返回「未接线不放行」的检查器 ⇒ 拒绝起跑（fail-closed）。
2. 审计核验器是 `DSHHost()` 对象；没有真实宿主证据时审计只会是 `declared`
   ⇒ `audit_state()` 报问题 ⇒ 拒绝起跑。
3. 执行准入由 `admission.Admission(workspace, store=共享 Store).check(plan, owner_session, exp_id)` 提供：
   它核 `Accounts` 的预算预留（active / expiry / reserved / 归属 exp / scope / unit ≥
   `plan.budget.max_cost_units`），`plan.authorization.scope` 为 `large` / `submission` 时
   再调 decisions 的 approved 检查。模块缺失或构建失败 ⇒ fail-closed。
   ⛔ 没有 `--allow-unwired-*` 这类跳过闸的参数。
4. `check` 是**明确只读入口**：只读 `ReadonlyStore` + `Workflow(readonly=True)` + 只读准入
   （`DecisionService.readonly`）。它不建库、不建目录、不迁移 schema；缺库 / 缺表 / 旧 schema
   ⇒ `{"ok": false, "undecidable": true, ...}`（退出码 2），⛔ 不为了跑通检查去建库或补表。
   `Monitoring` 的**构造**本身不 DDL（`Monitor.__init__` 只记路径与注入 host；事件账在写命令里
   才 `CREATE TABLE`），`check_start` 是零写读；生产接线照旧固定接它。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch.workflow import FINISH_REASONS, Workflow, WorkflowError  # noqa: E402


def _dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _shared_store(workspace, db, *, readonly=False):
    """check / launch 用的共享状态库：与 `Admission` 用的是**同一个库**。

    `readonly=True`（`check`）⇒ `runtime.store.ReadonlyStore`：⛔ 不建库、不建目录、
    不迁移 schema。缺库就是缺库，只读检查返回「不可判」，⛔ 不会因为「读一下」把库建出来。

    `readonly=False`（`launch` 等写命令）⇒ 原来的 `Store`（建目录 + 建表/迁移）。
    """
    from kernel.autoresearch.runtime.store import ReadonlyStore, Store
    path = (Path(db).resolve() if db
            else (Path(workspace).resolve() / ".autoresearch" / "runtime.sqlite3"))
    if readonly:
        return ReadonlyStore(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return Store(path)


def _run_readonly_check(flow, args, tail):
    """只读起跑检查：缺库 / 缺表 / 旧 schema / WAL 歧义态 ⇒ 不可判（不建库、不迁移、不起进程）。

    `Workflow(readonly=True).check()` 本身不写盘（`_read_conn` / `_ReadOnlyStore` 都委托
    `runtime.store.ReadonlyStore`）；这里只把「读不到」的异常（`WorkflowError`：缺库；
    `sqlite3.Error`：缺表 / 旧 schema；`StoreError`：WAL 边界 / 不是 SQLite 文件）统一成
    同一形状的 `{"ok": False, "undecidable": True, ...}`，⛔ 不让它变成 traceback，
    ⛔ 也不为了「让检查过」去建库、补表或创建 `-shm` / `-wal`。
    """
    from kernel.autoresearch.runtime.store import StoreError
    try:
        return flow.check(topic=args.topic, plan=args.plan,
                          owner_session=args.owner_session, argv=tail,
                          intent_id=args.intent_id,
                          intent_none=bool(args.intent_none))
    except (WorkflowError, StoreError, sqlite3.Error, KeyError, IndexError) as exc:
        # `KeyError` / `IndexError` 是旧 schema 少列时的读出形状（例如准备记录缺 argv_json）：
        # 只读检查不该因此 traceback，也不该去补列 ⇒ 同样判不可判。
        problems = list(getattr(exc, "errors", None) or []) or [str(exc)]
        return {"ok": False, "undecidable": True, "action": "check", "checks": {},
                "problems": [f"只读检查不可判（缺库 / 缺表 / 旧 schema / WAL 边界，"
                             f"本次不建库不迁移）：{item}" for item in problems]}


def host_event_sink(workspace, db_path):
    """复盘落库后的监控通知：`Monitoring.event(...)`（L6 的 `result_analysis` 触发）。

    只是通知：拿不到 `Monitoring` 就返回 None（复盘照常完成）；转发失败由 `Workflow`
    记一条 `event_sink_error`，⛔ 不回滚复盘、⛔ 不改退出码。

    `Monitoring` 只有 `result_analysis` 这一个 kind（证据走它的 `sources`），所以
    `evidence` 那一次不再回传第二个事件，只说明「证据已随 result_analysis 的 sources 送达」。
    """
    try:
        from .host_evidence import DSHHost
        from .monitoring import Monitoring
    except Exception:  # noqa: BLE001 - 通知不到不算失败
        return None
    try:
        monitoring = Monitoring(str(workspace), db_path=str(db_path), host=DSHHost())
    except Exception:  # noqa: BLE001
        return None

    def sink(kind, payload):
        if kind != "result_analysis":
            return {"forwarded": False,
                    "note": "证据来源已随 result_analysis 的 sources 送达，不重复发第二种事件"}
        evidence = [{"path": item.get("path"), "locator": item.get("locator")}
                    for item in (payload.get("evidence") or []) if isinstance(item, dict)]
        return monitoring.event(payload.get("owner_session"), "result_analysis",
                                exp=payload.get("plan_id") or "",
                                stage="evidence",
                                note=payload.get("next_action") or "",
                                sources=evidence)

    return sink


def _host_verifier():
    """审计核验器：`DSHHost()` 对象（Governance 调 `.verify`）。拿不到就 None ⇒ 只会 declared。"""
    try:
        from .host_evidence import DSHHost
    except Exception:  # noqa: BLE001 - 拿不到核验器不是错误，但绝不会变成 verified
        return None
    try:
        return DSHHost()
    except Exception:  # noqa: BLE001
        return None


def host_monitor(workspace):
    """固定的监控入口：`Monitoring(workspace, host=DSHHost()).check_start`。

    ⛔ 没有「跳过闸」的开关。模块缺失、构造失败、没有 `check_start` 都返回一个
    **判不放行**的检查器，把原因原样带进闸的问题列表。

    **构造零写（已核）**：`Monitor.__init__` 只解析路径与注入 host，不建目录、不建表；
    事件账的 `CREATE TABLE` 只在写命令（`_append`）里发生，`check_start` 走只读连接。
    所以 `check` 用同一条构造不会把 `.autoresearch/` 建出来。
    """
    try:
        from .host_evidence import DSHHost
        from .monitoring import Monitoring
    except Exception as exc:  # noqa: BLE001 - ImportError 也要 fail-closed
        reason = f"监控模块不可用（{type(exc).__name__}: {exc}）：未接线不放行"

        def blocked(owner_session, exp_id, action="launch"):
            return {"ok": False, "problems": [reason]}

        return blocked, reason
    try:
        host = DSHHost()
        try:
            monitoring = Monitoring(str(workspace), host=host)
        except TypeError:
            monitoring = Monitoring(workspace=Path(workspace), host=host)
        check = getattr(monitoring, "check_start", None)
        if not callable(check):
            raise TypeError("Monitoring 实例没有可调用的 check_start")
        return check, None
    except Exception as exc:  # noqa: BLE001
        reason = f"监控入口构建失败（{type(exc).__name__}: {exc}）：未接线不放行"

        def blocked(owner_session, exp_id, action="launch"):
            return {"ok": False, "problems": [reason]}

        return blocked, reason


def host_authorization(workspace, store, *, readonly=None):
    """固定的**执行准入**入口：`admission.Admission(workspace, store=...).check`。

    协议：`check(plan, owner_session, exp_id) -> {ok|allow, problems, decision, scope}`。
    Admission 核 `Accounts` 的 active / expiry / reserved / exp / scope / unit
    ≥ `plan.budget.max_cost_units`；`plan.authorization.scope` 是 large / submission 时
    再调 decisions 的 approved 检查。

    `readonly=True`（`check`）⇒ 准入里的决策核验用**只读服务**
    （`DecisionService.readonly(store.db_path)`：不建表、不建 `Accounts`、写入口全拒）；
    `Admission.check` 本身只读，`ReadonlyStore` 由调用方传入。

    ⛔ 同样没有跳过开关。模块缺失 / 构造失败 / 没有 check ⇒ 返回「未接线不放行」的准入器：
    统一入口因此**不能**只凭 `plan.execution_argv` 就开远端大题而不看决策。
    """
    from kernel.autoresearch.runtime.store import is_readonly_store
    if readonly is None:
        readonly = is_readonly_store(store)
    try:
        from .admission import Admission
    except Exception as exc:  # noqa: BLE001 - ImportError 也要 fail-closed
        reason = f"执行准入模块不可用（{type(exc).__name__}: {exc}）：未接线不放行"

        def blocked(plan, owner_session, exp_id):
            return {"ok": False, "problems": [reason]}

        return blocked, reason
    try:
        try:
            from .decisions import DecisionService
            if readonly:
                decisions = DecisionService.readonly(getattr(store, "db_path", store))
            else:
                decisions = DecisionService(store)
            admission = Admission(Path(workspace), store=store, decisions=decisions)
        except TypeError:
            admission = Admission(workspace=Path(workspace), store=store)
        check = getattr(admission, "check", None)
        if not callable(check):
            raise TypeError("Admission 实例没有可调用的 check")
        return check, None
    except Exception as exc:  # noqa: BLE001
        reason = f"执行准入入口构建失败（{type(exc).__name__}: {exc}）：未接线不放行"

        def blocked(plan, owner_session, exp_id):
            return {"ok": False, "problems": [reason]}

        return blocked, reason


def _split_argv(tokens):
    """把 `--` 之后的部分原样当作被启动进程的 argv（⛔ 不经过 argparse 改写）。"""
    if "--" in tokens:
        index = tokens.index("--")
        return tokens[:index], tokens[index + 1:]
    return tokens, None


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m kernel.autoresearch.workflow_cli",
        description="比赛无关的研究执行主链（J1–J6 · K1 起跑检查 · I7）。"
                    "所有文件参数都是工作区内相对路径；被启动进程的 argv 一律经 `--` 传入。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", required=True, help="独立研究工作区根目录")
    common.add_argument("--db", default=None, help="状态库；默认 <workspace>/.autoresearch/runtime.sqlite3")
    common.add_argument("--actor", default="main", help="操作者标识（本地登记字段）")
    sub = parser.add_subparsers(dest="action", required=True)

    prepare = sub.add_parser("prepare", parents=[common],
                             help="准备事前材料：四锚点 + 实现五项/files + 显式 argv，冻结来源哈希")
    prepare.add_argument("--topic", required=True)
    prepare.add_argument("--plan", required=True, help="工作区内相对路径，或 JSON 对象文件")
    prepare.add_argument("--owner-session", required=True)
    prepare.add_argument("--source", action="append", default=[], help="来源文件（工作区内相对路径，可重复）")
    prepare.add_argument("--source-file", default=None, help="来源清单 JSON 数组（元素是路径或 {path, locator}）")

    for name, text in (("check", "只读起跑检查：不写盘、不起子进程"),
                       ("launch", "通过闸后真的启动本地子进程，并保留真实退出码")):
        node = sub.add_parser(name, parents=[common], help=text,
                              epilog="`--` 之后是被启动进程的完整 argv，"
                                     "必须与计划里的 execution_argv 逐项相等。")
        node.add_argument("--topic", required=True)
        node.add_argument("--plan", required=True)
        node.add_argument("--owner-session", required=True)
        intent = node.add_mutually_exclusive_group(required=True)
        intent.add_argument("--intent-id", default=None,
                            help="专项意图编号；必须等于 Monitoring.check_start 报告的真实当前 id")
        intent.add_argument("--intent-none", action="store_true",
                            help="显式声明当前没有专项意图（Monitoring 状态必须是 none/closed；省略不默认放行）")

    finish = sub.add_parser("finish", parents=[common],
                            help="登记结束：真实原因 + 退出码（文档失败不覆盖退出码）")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--exitcode", type=int, required=True, help="真实退出码（信号终止用负值）")
    finish.add_argument("--reason", required=True, choices=list(FINISH_REASONS))
    finish.add_argument("--documentation-status", default="skipped", choices=["ok", "failed", "skipped"])
    finish.add_argument("--documentation-detail", default="")

    review = sub.add_parser("review", parents=[common],
                            help="复盘验收：结束记录、计划未改写、证据哈希、判决落账")
    review.add_argument("--run-id", required=True)
    review.add_argument("--file", required=True, help="复盘记录 JSON（工作区内相对路径）；新记录要求 schema_version=2")
    review.add_argument("--legacy-record", action="store_true", help="显式导入旧复盘；缺少的下一步费用与审批信息仍为 unknown")

    status = sub.add_parser("status", parents=[common],
                            help="只读投影：准备是否过期、结束原因/退出码、待复盘、下一轮阻断")
    status.add_argument("--topic", default=None)
    status.add_argument("--plan-id", default=None)
    return parser


def main(argv=None):
    tokens = list(sys.argv[1:] if argv is None else argv)
    head, tail = _split_argv(tokens)
    args = _parser().parse_args(head)
    workspace = Path(args.workspace)
    common = {"workspace": workspace, "db": args.db}
    try:
        if args.action == "prepare":
            flow = Workflow(**common, verifier=_host_verifier())
            result = flow.prepare(topic=args.topic, plan=args.plan,
                                  owner_session=args.owner_session,
                                  sources=args.source, source_file=args.source_file,
                                  actor=args.actor)
            _dump(result)
            return 0

        if args.action in ("check", "launch"):
            # `check` 是明确只读入口：只读 Store + `Workflow(readonly=True)` + 只读准入构造。
            # ⛔ 不因为「检查一下」就建库 / 建目录 / 迁移 schema；缺库缺表返回不可判。
            readonly = args.action == "check"
            store = _shared_store(workspace, args.db, readonly=readonly)
            monitor, monitor_why = host_monitor(workspace)
            admission, admission_why = host_authorization(workspace, store, readonly=readonly)
            from .execution_target import ExecutionTarget
            flow = Workflow(workspace=workspace, db=args.db, store=store,
                            verifier=_host_verifier(), monitor_check=monitor,
                            authorization_check=admission,
                            execution_target=ExecutionTarget(workspace, require_provision=True),
                            readonly=readonly)
            for reason in (monitor_why, admission_why):
                if reason:
                    print(f"⚠️ {reason}", file=sys.stderr)
            if args.action == "check":
                result = _run_readonly_check(flow, args, tail)
                _dump(result)
                if result.get("ok"):
                    return 0
                # 读不到状态库是输入 / 数据问题（2），规则拒绝才是 9（一定没起子进程）。
                return 2 if result.get("undecidable") else 9
            result = flow.start(topic=args.topic, plan=args.plan,
                                owner_session=args.owner_session, argv=tail,
                                intent_id=args.intent_id,
                                intent_none=bool(args.intent_none), actor=args.actor)
            _dump(result)
            if result.get("timed_out"):
                return 124  # plan.budget.max_seconds 到点被终止
            # 真实退出码原值返回（这是「保留退出码」的落点）。
            return int(result.get("exitcode") or 0)

        if args.action == "finish":
            flow = Workflow(**common, verifier=_host_verifier())
            result = flow.finish(run_id=args.run_id, exitcode=args.exitcode, reason=args.reason,
                                 documentation={"status": args.documentation_status,
                                                "detail": args.documentation_detail},
                                 actor=args.actor)
            _dump(result)
            # 记录写成功就返回 0：文档失败不改退出码，也不阻断安全停。
            return 0

        if args.action == "review":
            db_path = (Path(args.db).resolve() if args.db
                       else (workspace.resolve() / ".autoresearch" / "runtime.sqlite3"))
            flow = Workflow(**common, verifier=_host_verifier(),
                            event_sink=host_event_sink(workspace, db_path))
            result = flow.review(run_id=args.run_id, record=args.file, actor=args.actor,
                                 require_version=None if args.legacy_record else 2)
            _dump(result)
            return 0 if result.get("ok") else 9

        if args.action == "status":
            flow = Workflow(**common, readonly=True)
            result = flow.status(topic=args.topic, plan_id=args.plan_id)
            _dump(result)
            return 0 if result.get("ok") else 2

        raise WorkflowError(f"不支持的命令：{args.action}", code=2)
    except KeyboardInterrupt:
        _dump({"ok": False, "problems": ["被用户中断：结束原因已按 interrupted 记录"]})
        return 130
    except WorkflowError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc),
               "problems": exc.errors or [str(exc)]})
        return exc.code
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
