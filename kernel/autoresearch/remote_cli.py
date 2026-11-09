# GENERATED-BY: managed remote execution wiring, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：doctor / check / provision / start / status / query / cancel / collect / release
# PURPOSE: `remote_workflow.py` 的命令行入口。生产接线固定：审计核验 `DSHHost()`、监控 `Monitoring.check_start`、
#          执行准入 `admission.Admission(...).check`；⛔ 没有 --allow-unwired-* 这类跳过闸的开关。
# ORIGIN: 形态照 `workflow_cli.py`（先检查后起跑 + 保留真实退出码），语义来自 remote_workflow 的受管远端链。
# CUMULATIVE-DELTA: 2026-11-09 默认关闭监控时不再要求意图声明参数； 首版。默认关闭：没有 `.autoresearch/remote/deployment.json`（enabled=true）时，
#          除 `doctor` / `status` 外的子命令都诚实拒绝并且**一个连接都不发**；`doctor` 与 `status` 只读本地。
"""受管远端执行的命令行入口。

    python -m kernel.autoresearch.remote_cli doctor  --workspace W [--plan-id P]
    python -m kernel.autoresearch.remote_cli check   --workspace W --topic T --plan P \\
        --owner-session S [--intent-id I | --intent-none] --resource R [...] -- ARGV...
    python -m kernel.autoresearch.remote_cli provision --workspace W [--force]
    python -m kernel.autoresearch.remote_cli start   （同 check，可加 --run-series-id / --run-reservation-id）
    python -m kernel.autoresearch.remote_cli status  --workspace W [--run-id R] [--topic T] [--plan-id P]
    python -m kernel.autoresearch.remote_cli query   --workspace W (--run-id R | --plan-id P | --topic T)
    python -m kernel.autoresearch.remote_cli cancel  --workspace W (--run-id R | ...) [--reason TEXT]
    python -m kernel.autoresearch.remote_cli collect --workspace W (--run-id R | ...)
    python -m kernel.autoresearch.remote_cli release --workspace W (--run-id R | ...)

退出码：

    0    通过 / 已登记
    2    输入或配置错误（部署配置坏、计划缺失、JSON 坏、找不到 run）
    3    不可判（远端 query unknown / token 核不过）：预算与资源都保留，⛔ 没有终态结论
    9    规则拒绝（闸或前置不过）—— **一定没有发出任何远端请求**
    130  被用户中断（受管远端：run 已占用，用 status / query 复核）

生产纪律写在代码里、不是可选参数：
1. 审计核验器是 `DSHHost()` 对象；没有真实宿主证据时审计只是 `declared` ⇒ 闸会拒。
2. 监控固定接 `Monitoring(workspace, host=DSHHost()).check_start`；ImportError / 构建失败 ⇒ 判不放行。
3. 执行准入固定接 `admission.Admission(workspace, store=共享 Store).check`；失败 ⇒ 判不放行。
4. 部署默认关闭：`SSHDeployment(enabled=False)` 或配置缺失时，`remote_workflow` 在任何网络动作前就拒。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch.remote_workflow import (  # noqa: E402
    DEPLOYMENT_REL,
    RemoteWorkflow,
    RemoteWorkflowError,
    doctor,
    read_deployment,
)
from kernel.autoresearch.workflow import WorkflowError  # noqa: E402


def _dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _host_verifier():
    """审计核验器：`DSHHost()` 对象（Governance 调 `.verify`）。拿不到就 None ⇒ 只会 declared。"""
    try:
        from kernel.autoresearch.host_evidence import DSHHost
    except Exception:  # noqa: BLE001 - 拿不到核验器不是错误，但绝不会变成 verified
        return None
    try:
        return DSHHost()
    except Exception:  # noqa: BLE001
        return None


def _space(workspace, db, *, readonly=False):
    """生产闸的三个固定接线（监控 / 执行准入 / 审计核验）。⛔ 没有跳过开关。

    `readonly=True`（`remote check`）**不建库**：缺库直接拒，不把「没有工作区」变成绿。
    只读工厂（`ReadonlyStore` / `DecisionService(readonly=…)`）由另一条线并行加；本函数先探测，
    拿不到就用只读视图对象，绝不因此放宽任何判据。
    """
    from kernel.autoresearch.workflow_cli import host_monitor

    path = Path(db).resolve() if db else \
        (Path(workspace).resolve() / ".autoresearch" / "runtime.sqlite3")
    if readonly:
        if not path.is_file():
            raise RemoteWorkflowError(
                f"工作区数据库不存在：{path}（check 不会自动建库；先 init / prepare）", code=2)
        store = None
        try:
            from kernel.autoresearch.runtime.store import ReadonlyStore
            store = ReadonlyStore(path)
        except (ImportError, AttributeError):
            store = SimpleNamespace(db_path=str(path))
        try:
            from kernel.autoresearch.admission import Admission
            decisions = None
            try:
                from kernel.autoresearch.decisions import DecisionService
                decisions = DecisionService(store)
            except Exception as exc:  # noqa: BLE001 - 只读决策服务不可用 ⇒ large/submission 不可核
                print(f"⚠️ 只读决策服务不可用（{type(exc).__name__}: {exc}）："
                      "large/submission 的审批链在 check 里不可核", file=sys.stderr)
            admission = Admission(Path(workspace), store=store, decisions=decisions).check
        except Exception as exc:  # noqa: BLE001 - 构建失败 ⇒ fail-closed
            reason = f"执行准入入口构建失败（{type(exc).__name__}: {exc}）：未接线不放行"

            def admission(plan, owner_session, exp_id):
                return {"ok": False, "problems": [reason]}
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        from kernel.autoresearch.runtime.store import Store
        store = Store(path)
        from kernel.autoresearch.workflow_cli import host_authorization
        admission, admission_why = host_authorization(workspace, store)
        if admission_why:
            print(f"⚠️ {admission_why}", file=sys.stderr)
    monitor, monitor_why = host_monitor(workspace)
    if monitor_why:
        print(f"⚠️ {monitor_why}", file=sys.stderr)
    return {"store": store, "monitor_check": monitor, "authorization_check": admission,
            "verifier": _host_verifier()}


def _flow(args, *, readonly, wiring=False):
    deployment, problems = read_deployment(args.workspace)
    if readonly and not wiring:
        return RemoteWorkflow(args.workspace, db=args.db, deployment=deployment, readonly=True)
    if deployment is None:
        raise RemoteWorkflowError(
            "受管远端部署未就绪，拒绝执行：" + "；".join(problems),
            problems, code=9)
    space = _space(args.workspace, args.db, readonly=readonly)
    return RemoteWorkflow(args.workspace, db=args.db, deployment=deployment,
                          readonly=readonly, **space)


def _split_argv(tokens):
    """`--` 之后原样当作 argv（⛔ 不经过 argparse 改写）。"""
    if "--" in tokens:
        index = tokens.index("--")
        return tokens[:index], tokens[index + 1:]
    return tokens, None


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m kernel.autoresearch.remote_cli",
        description="受管远端执行：统一工作流闸 → Runtime 预算 / 资源租约 → SSHAdapter。"
                    f"部署配置固定在 {DEPLOYMENT_REL}，默认关闭。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="`--` 之后是被远端 worker 执行的完整 argv，必须与计划里的 execution_argv 逐项相等。")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", required=True, help="独立研究工作区根目录")
    common.add_argument("--db", default=None,
                        help="状态库；默认 <workspace>/.autoresearch/runtime.sqlite3")
    common.add_argument("--actor", default="main", help="操作者标识（本地登记字段）")
    sub = parser.add_subparsers(dest="action", required=True)

    node = sub.add_parser("doctor", parents=[common],
                          help="只读诊断：部署 / worker / 所有权 / 预算 / 调度器（不联网）")
    node.add_argument("--plan-id", default=None, help="可选：核这个实验的 Runtime 所有权")

    for name, text in (("check", "只读检查：统一闸 + 远端绑定，打印会提交什么、还缺什么（不写盘、不联网）"),
                       ("start", "通过全部闸后提交一次受管远端执行")):
        node = sub.add_parser(name, parents=[common], help=text)
        node.add_argument("--topic", required=True)
        node.add_argument("--plan", required=True)
        node.add_argument("--owner-session", required=True)
        intent = node.add_mutually_exclusive_group()
        intent.add_argument("--intent-id", default=None,
                            help="专项意图编号（必须等于 Monitoring.check_start 的真实当前 id）")
        intent.add_argument("--intent-none", action="store_true",
                            help="显式声明当前没有专项意图（关闭监控时可省略；启用后仍须声明）")
        node.add_argument("--resource", action="append", default=[],
                          help="资源租约键（可重复，例如 machine:gpu-a/gpu0）")

    node = sub.add_parser("provision", parents=[common],
                          help="显式推送 ssh_worker.py（唯一的部署动作；默认不做）")
    node.add_argument("--force", action="store_true")

    handle = argparse.ArgumentParser(add_help=False)
    handle.add_argument("--run-id", default=None)
    handle.add_argument("--topic", default=None)
    handle.add_argument("--plan-id", default=None)

    sub.add_parser("status", parents=[common, handle],
                   help="只读投影：run / 远端绑定 / 执行状态 / 是否已核终态（不联网）")
    sub.add_parser("query", parents=[common, handle],
                   help="显式远端复核：query → 明确终态才登记结束（unknown 保留预算与资源）")
    node = sub.add_parser("cancel", parents=[common, handle],
                          help="显式安全取消：先发远端停止并核 token，再登记")
    node.add_argument("--reason", default="stopped_by_user")
    sub.add_parser("collect", parents=[common, handle],
                   help="显式取回产物（只有 worker 报 completed 且已核终态）")
    sub.add_parser("release", parents=[common, handle],
                   help="明确终态后核空闲并释放资源租约（预算仍不自动结算）")
    return parser


def main(argv=None):
    tokens = list(sys.argv[1:] if argv is None else argv)
    head, tail = _split_argv(tokens)
    args = _parser().parse_args(head)
    try:
        if args.action == "doctor":
            result = doctor(args.workspace, plan_id=args.plan_id)
            _dump(result)
            return 0 if result.get("ok") else 2

        if args.action == "status":
            flow = _flow(args, readonly=True)
            result = flow.status(run_id=args.run_id, topic=args.topic, plan_id=args.plan_id)
            _dump(result)
            return 0 if result.get("ok") else 2

        if args.action == "provision":
            flow = _flow(args, readonly=False)
            result = flow.provision(force=bool(args.force), actor=args.actor)
            _dump(result)
            return 0

        if args.action == "check":
            flow = _flow(args, readonly=True, wiring=True)
            result = flow.check(topic=args.topic, plan=args.plan, owner_session=args.owner_session,
                                argv=tail, resources=args.resource,
                                intent_id=args.intent_id, intent_none=bool(args.intent_none))
            _dump(result)
            return 0 if result.get("ok") else int(result.get("code") or 9)

        if args.action == "start":
            flow = _flow(args, readonly=False, wiring=True)
            result = flow.submit(topic=args.topic, plan=args.plan,
                                 owner_session=args.owner_session, argv=tail,
                                 resources=args.resource, intent_id=args.intent_id,
                                 intent_none=bool(args.intent_none), actor=args.actor)
            _dump(result)
            if result.get("finished"):
                return 0
            return 0 if result.get("ok") else 3

        if args.action in ("query", "cancel", "collect", "release"):
            flow = _flow(args, readonly=False, wiring=True)
            kwargs = {"run_id": args.run_id, "topic": args.topic, "plan_id": args.plan_id,
                      "actor": args.actor}
            if args.action == "query":
                result = flow.query(**kwargs)
                _dump(result)
                return int(result.get("code") or 0)
            if args.action == "cancel":
                result = flow.cancel(reason=args.reason, **kwargs)
                _dump(result)
                return int(result.get("code") or 0)
            if args.action == "collect":
                result = flow.collect(**kwargs)
                _dump(result)
                return 0 if result.get("ok") else 3
            result = flow.release(**kwargs)
            _dump(result)
            return 0 if result.get("ok") else 3

        raise RemoteWorkflowError(f"不支持的命令：{args.action}", code=2)
    except KeyboardInterrupt:
        _dump({"ok": False, "code": 130,
               "problems": ["被用户中断：run 已占用，用 remote status / query 复核；"
                            "远端是否在跑不可判时预算与资源保持"]})
        return 130
    except RemoteWorkflowError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc),
               "problems": exc.errors or [str(exc)], "code": exc.code})
        return exc.code
    except WorkflowError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc),
               "problems": exc.errors or [str(exc)], "code": exc.code})
        return exc.code
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc), "code": 2})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
