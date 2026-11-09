# GENERATED-BY: 完整功能迁移（持续推进 A1–A7 / 远端进度 H1–H9 / 入向超期 D2 的宿主侧联动）
# LAST-EDIT: 2026-11-09（首版；doctor / goal / overdue / plan / register / verify / jobs / status / watch / tick）
# PURPOSE: progress_host 的固定入口 —— 只读展示 DSH 真实 goal、Decisions 超期、受管 job 计划与核验，并把两者接进 Progress。
# ORIGIN: 上游推进器 CLI 形态；宿主约定见 kernel/autoresearch/progress_host.py 的模块说明与 kernel/autoresearch/progress.py 的 WATCH_CONTRACT。
# CUMULATIVE-DELTA: 首版。刻意不提供 `--jsonl` / `--decoder` / `--goal-json` / `--overdue-json`：
#          证据只能来自固定 deployment + 生产 DSHHost（或部署自己的 host_module），CLI 不接受 caller 挑证据。

"""progress_host 命令行。

用法（固定 deployment，不是每次临时 JSON）：

    python -X utf8 -B kernel/autoresearch/progress_host_cli.py --deployment <部署.json> doctor
    ... goal
    ... overdue
    ... plan [--poll 20] [--max-rounds 0]
    ... register --job-id pwsh-7 [--command "<watch 命令>"] [--argv-json "[...]"]
    ... verify --job-id pwsh-7 --command "<watch 命令>"
    ... jobs / status
    ... watch [--poll 20] [--max-rounds 0]     # 真正由宿主后台作业启动的那条
    ... tick

退出码：

    0  正常 / 已知且可判（goal 覆盖状态确切、超期已核、job 已核验）
    2  deployment 或参数被拒
    3  需要动作（有超期未回复入向）
    9  不可判（goal 覆盖未知 / 超期不可核 / job 核不到 / 体检不通过）
    watch / tick 另用 progress 的退出码（0 已唤醒 / 4 采集失败唤醒 / 5 轮数上限 / 6 租约 /
    7 未重挂 / 9 不可判）；⚠️ 后台作业可能把非零压成 1，真值看 JSON 与 runs/<job_id>.json。

⛔ 本 CLI 不启动 server、不自续、不替宿主起 job、不发消息、不写宿主目录。
`watch` 由宿主的 `functions.pwsh`（或部署配置的 shell tool）后台作业启动；本工具只产出确切命令、
pending 标记与 job_id 登记/核验接口。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许直接当脚本跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kernel.autoresearch import progress, progress_host  # noqa: E402


def _emit(result, code):
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
    return code


def _adapter(args):
    return progress_host.build_adapter(args.deployment)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="progress-host",
        description="宿主侧联动：DSH 真实 goal、Decisions 超期、受管 job 登记；只读、不改宿主。")
    parser.add_argument("--deployment", required=True,
                        help="固定 deployment 配置（JSON）；证据路径只能来自它，不听 CLI 临时参数")
    sub = parser.add_subparsers(dest="command", required=True,
                                metavar="{doctor,goal,overdue,plan,register,verify,jobs,status,watch,tick}")

    sub.add_parser("doctor", help="体检：dsh_home / 会话日志 / goal 重放 / 超期只读库 / job 台账")
    sub.add_parser("goal", help="只读打印 DSH 真实 goal 的重放结果与 coverage 判定")
    sub.add_parser("overdue", help="只读打印文字入向的回复窗口（不带正文摘要）")

    plan = sub.add_parser("plan", help="生成受管 watch 的确切命令、宿主后台调用片段与登记命令")
    plan.add_argument("--poll", type=float, default=None, help="轮询间隔（秒）")
    plan.add_argument("--max-rounds", type=int, default=0, help="调用方显式轮数上限（0 = 不限）")

    register = sub.add_parser(
        "register", help="登记受管 job：先核验 exact 命令 + jobId，再落 append-only 台账")
    register.add_argument("--job-id", required=True, help="宿主返回的 job id（形状 <kind>-N）")
    register.add_argument("--command", dest="job_command", default=None,
                          help="启动该 job 的确切命令；省略时用 watch 写的 pending 标记")
    register.add_argument("--argv-json", default=None, help="可选：该命令的 argv 列表（JSON）")

    verify = sub.add_parser("verify", help="只核验、不登记")
    verify.add_argument("--job-id", required=True, help="宿主返回的 job id")
    verify.add_argument("--command", dest="job_command", required=True,
                        help="启动该 job 的确切命令")

    sub.add_parser("jobs", help="只读打印 job 登记台账（含 verified / why）")
    sub.add_parser("status", help="只读汇总：goal + overdue + 最近 job + pending 标记")

    watch = sub.add_parser(
        "watch",
        help="受管后台作业本体：用可信 goal/overdue 适配器构造 Progress；一次唤醒即退出并写 needs_rearm")
    watch.add_argument("--poll", type=float, default=None, help="轮询间隔（秒）")
    watch.add_argument("--max-rounds", type=int, default=0,
                       help="调用方显式轮数上限（0 = 不限；到了写 exhausted + needs_rearm）")

    sub.add_parser("tick", help="单轮：用可信 goal/overdue 适配器跑一次 Progress.tick")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = _adapter(args).doctor()
            return _emit(result, progress.EXIT_CODES["ok"] if result["ok"]
                         else progress.EXIT_CODES["undecidable"])

        if args.command == "goal":
            adapter = _adapter(args)
            result = {"replay": adapter.goal_replay(), "state": adapter.goal_state(),
                      "projection_diagnostic": adapter.projection_diagnostic(),
                      "readonly": True}
            known = result["state"].get("known") and result["replay"].get("available")
            return _emit(result, progress.EXIT_CODES["ok"] if known
                         else progress.EXIT_CODES["undecidable"])

        if args.command == "overdue":
            result = _adapter(args).overdue()
            if not result.get("checked"):
                code = progress.EXIT_CODES["undecidable"]
            elif result.get("overdue"):
                code = progress.EXIT_CODES["health_action"]
            else:
                code = progress.EXIT_CODES["ok"]
            return _emit(result, code)

        if args.command == "plan":
            return _emit(_adapter(args).plan(poll=args.poll,
                                             max_rounds=args.max_rounds or None),
                         progress.EXIT_CODES["ok"])

        if args.command == "register":
            adapter = _adapter(args)
            command = args.job_command
            argv = json.loads(args.argv_json) if args.argv_json else None
            if argv is not None and (not isinstance(argv, list)
                                     or not all(isinstance(item, str) for item in argv)):
                raise progress_host.HostProgressError("--argv-json 必须是字符串列表")
            if command is None:
                marker = adapter.pending_job()
                if not marker:
                    raise progress_host.HostProgressError(
                        "没有 --command，也没有 watch 写的 pending 标记；不能凭猜登记")
                command = marker.get("command")
                if argv is None and isinstance(marker.get("argv"), list):
                    argv = marker["argv"]
                expected = marker.get("command_sha256")
                if expected and progress_host._sha_text(command) != expected:
                    raise progress_host.HostProgressError(
                        "pending 标记的命令指纹不符；拒绝用被改过的命令登记")
            result = adapter.register_job(args.job_id, command, argv=argv)
            return _emit(result, progress.EXIT_CODES["ok"] if result["verified"]
                         else progress.EXIT_CODES["undecidable"])

        if args.command == "verify":
            result = _adapter(args).verify_job(args.job_id, args.job_command)
            return _emit(result, progress.EXIT_CODES["ok"] if result["verified"]
                         else progress.EXIT_CODES["undecidable"])

        if args.command == "jobs":
            adapter = _adapter(args)
            return _emit({"jobs": adapter.jobs(), "pending_job": adapter.pending_job()},
                         progress.EXIT_CODES["ok"])

        if args.command == "status":
            result = _adapter(args).status()
            if result["ok"]:
                code = progress.EXIT_CODES["ok"]
            elif "overdue_user_message" in result["blockers"]:
                code = progress.EXIT_CODES["health_action"]
            else:
                code = progress.EXIT_CODES["undecidable"]
            return _emit(result, code)

        if args.command == "watch":
            adapter = _adapter(args)
            deployment = adapter.deployment
            spec_path = deployment.get("progress_spec")
            if not spec_path:
                raise progress_host.DeploymentError("deployment.progress_spec 未配置：watch 不知道采什么")
            spec = progress.load_spec(spec_path)
            planned = adapter.plan(poll=args.poll, max_rounds=args.max_rounds or None)
            adapter.write_pending_job(planned["watch_command"], planned["watch_argv"])
            instance = progress.Progress(
                deployment["workspace"], spec, spec_path=spec_path,
                owner=deployment["owner"], goal=adapter.goal_state,
                overdue=adapter.overdue_provider)
            print("[progress-host] watch 启动：宿主受管后台作业本体；自己叫不醒宿主、"
                  "一次唤醒即退出并写 needs_rearm；退出后由宿主通知负责人并显式 rearm。"
                  f"待登记命令 sha256={planned['command_sha256']}",
                  file=sys.stderr, flush=True)
            result = instance.watch(poll=args.poll, max_rounds=args.max_rounds)
            code = int(result.get("intended_exit", progress.EXIT_CODES["error"]))
            if result.get("job_id"):
                adapter.finish_pending_job(result)
                instance.record_watch_exit(result, code)
                result["actual_exit"] = code
            result["pending_job"] = adapter.pending_job()
            return _emit(result, code)

        if args.command == "tick":
            adapter = _adapter(args)
            deployment = adapter.deployment
            spec_path = deployment.get("progress_spec")
            if not spec_path:
                raise progress_host.DeploymentError("deployment.progress_spec 未配置：tick 不知道采什么")
            spec = progress.load_spec(spec_path)
            instance = progress.Progress(
                deployment["workspace"], spec, spec_path=spec_path,
                owner=deployment["owner"], goal=adapter.goal_state,
                overdue=adapter.overdue_provider)
            result = instance.tick()
            code = int(result.get("intended_exit", progress.EXIT_CODES["ok"]))
            return _emit(result, code)

        raise progress_host.HostProgressError(f"不支持的命令：{args.command}")
    except progress_host.DeploymentError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["spec"]
    except progress_host.HostProgressError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["spec"]
    except progress.ProgressError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["spec"]
    except Exception as exc:  # noqa: BLE001 - 入口一律留结构化错误
        print(json.dumps({"ok": False, "error": type(exc).__name__, "reason": str(exc)},
                         ensure_ascii=False, indent=2))
        return progress.EXIT_CODES["error"]


if __name__ == "__main__":
    raise SystemExit(main())
