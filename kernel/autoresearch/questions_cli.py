# GENERATED-BY: 普通向用户提问台账 CLI，2026-11-09
# LAST-EDIT: 2026-11-09 首版：ask / open / show / answer / declare / enqueue / link / expire / cancel
# PURPOSE: 命令行入口 —— 把待答问题落盘、查待答、绑已认证回复、记手工文字（只 declared）、外发与关闭。
# ORIGIN: kernel/autoresearch/questions.py；⛔ 不含真实通道配置、个人身份或具体机器。
# CUMULATIVE-DELTA: 首版。只读子命令（open / show）零写；写子命令一律走台账事务。
#   本轮补：复用 `decisions_cli` 的 `--channel` / `--channel-config` 给 `answer` 提供可信
#   `authenticate`（FeishuChannel 验签 + 允许列表 + 本地台账）；无配置明确拒（不降级）；
#   `ask` 加 `--blocking`。⛔ 不另起凭据格式、不回显凭据原值。

"""普通向用户提问台账的命令行（E7）。

用法（参数以 `--help` 为准）：

    python -m kernel.autoresearch.questions_cli --workspace W ask \\
        --exp exp-01 --body "基线用哪一版？" --owner <负责人> [--asked-by agent] \\
        [--recipient <通道收件人>] [--deadline <epoch 秒或带时区的 ISO 8601>] \\
        [--blocking] [--topic T]

    ... open  [--exp exp-01]
    ... show  Q-000001
    ... answer Q-000001 --source feishu --event-id <入向事件> --channel-config <JSON>
    ... declare Q-000001 --body "用户口头说……" --actor <记录者> [--source PATH ...]
    ... enqueue Q-000001 [--actor questions]     # 走现 Delivery；没通道就留在队列
    ... link    Q-000001 --exp exp-01 --text "为什么算这个实验" [--source PATH ...]
    ... expire  Q-000001
    ... cancel  Q-000001 --text "为什么不问了"

`answer` 怎么拿到可信身份
========================

身份核验**必须由部署注入**。本 CLI 复用 `decisions_cli` 的同一套通道配置
（`--channel module:factory` / `--channel-config <内联 JSON | JSON 文件 | @文件>`，
构造内核 `channels/feishu.py` 的 `FeishuChannel`），拿它的 `authenticate` 去核入向事件：

* 有配置 ⇒ 真实验签 + 允许列表 + 本地台账比对，通过才允许把问题记成 `answered`；
* 没配置 ⇒ 明确拒绝（`NoTrustedChannel`），⛔ 不降级成「就当是用户」；手工文字只能 `declare`；
* 只读子命令（`open` / `show`）与本地登记**不构造通道、不联网、不写库**。

⛔ 本 CLI 不发审批卡、不建审批页、不批准执行；`enqueue` 只把待答内容排进交付台账，
真正外发是部署的发送端那一环（本命令不假装已送达）。

退出码
======

    0  已登记 / 只读查询成功
    9  规则拒绝（未知问题、身份不可信、缺可信通道配置、错实验号、重放矛盾、状态不对）
    2  输入或数据错误（工作区未初始化、台账缺失或旧 schema、JSON 坏）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch.questions import (  # noqa: E402
    QuestionConflict,
    QuestionError,
    Questions,
)


def _authenticator(args):
    """按部署配置取可信身份核验器；⛔ 不给配置就没有核验器（不猜、不放宽）。

    复用 `decisions_cli` 的同一个配置加载器（`--channel` / `--channel-config`）：
    ⛔ 不另起一套凭据格式，也不回显配置里的凭据原值。
    """
    if not getattr(args, 'channel', None) and not getattr(args, 'channel_config', None):
        return None, None
    from kernel.autoresearch.decisions_cli import select_channel

    channel = select_channel(args)
    if channel is None:
        return None, None
    verify = getattr(channel, 'authenticate', None)
    if not callable(verify):
        raise QuestionError('配置出来的通道没有 authenticate 能力'
                            ' ⇒ 无法核入向身份，拒（⛔ 不降级成「就当是用户」）')
    return verify, channel


def _dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _sources(workspace, paths):
    root = Path(workspace).resolve()
    refs = []
    for raw in paths or []:
        path = (root / raw).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"证据文件不存在或越出工作区：{raw}")
        try:
            count = len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            raise ValueError(f"证据文件必须是 UTF-8 文本：{raw}") from None
        refs.append({"path": raw, "locator": f"L1-L{max(count, 1)}"})
    return refs


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m kernel.autoresearch.questions_cli",
        description="普通向用户提问台账：落盘 / 查待答 / 绑已认证回复 / 记手工文字（只 declared）/ "
                    "外发 / 关闭。⛔ 不建审批页、不强制任何通道、不自动批准执行。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=argparse.SUPPRESS, help="工作区根目录")
    common.add_argument("--db", default=argparse.SUPPRESS, help="状态库；默认 <workspace>/.autoresearch/runtime.sqlite3")
    common.add_argument("--actor", default=argparse.SUPPRESS, help="执行者标识（本地登记字段）")
    common.add_argument("--channel", default=argparse.SUPPRESS,
                        help="部署方通道工厂 module:factory（answer 用）")
    common.add_argument("--channel-config", default=argparse.SUPPRESS, dest="channel_config",
                        help="内核 FeishuChannel 配置：内联 JSON / JSON 文件 / @文件（answer 用）")
    parser.add_argument("--workspace", default=None, help="工作区根目录")
    parser.add_argument("--db", default=None)
    parser.add_argument("--actor", default="agent")
    # 可信通道来源：与 `decisions_cli` 同一套（互斥）。只读子命令**不构造**通道。
    parser.add_argument("--channel", default=None,
                        help="部署方通道工厂 module:factory 或 path/to/file.py:factory（answer 用）")
    parser.add_argument("--channel-config", default=None, dest="channel_config",
                        help="内核 FeishuChannel 配置：内联 JSON / JSON 文件 / @文件（answer 用）")

    sub = parser.add_subparsers(dest="action", required=True)

    ask = sub.add_parser("ask", parents=[common], help="登记一条待答问题（必须关联实验号）")
    ask.add_argument("--exp", required=True, help="关联的实验号（如 exp-01）")
    ask.add_argument("--body", required=True, help="待答内容（要问人的那句话）")
    ask.add_argument("--owner", required=True, help="负责人：谁负责拿到答复")
    ask.add_argument("--asked-by", default=None, help="提问方（默认取 --actor）")
    ask.add_argument("--question-id", default=None, help="显式问题号（默认 Q-000001 顺序号）")
    ask.add_argument("--topic", default=None)
    ask.add_argument("--recipient", default=None, help="通道收件人（外发才需要）")
    ask.add_argument("--deadline", default=None,
                     help="deadline：epoch 秒数或带时区的 ISO 8601；不给就没有 deadline")
    ask.add_argument("--blocking", action="store_true",
                     help="只有明确依赖这条答复才继续的动作才加；默认 False（问问题不等于停等）")
    ask.add_argument("--note", default=None)

    opened = sub.add_parser("open", parents=[common], help="只读：待答 / 超期 / 未关联（分开列）")
    opened.add_argument("--exp", default=None)

    show = sub.add_parser("show", parents=[common], help="只读：一条问题的完整视图（含事件）")
    show.add_argument("question_id")

    answer = sub.add_parser("answer", parents=[common],
                            help="绑定一条已认证入向事件（需要 --channel-config / --channel 的可信身份）")
    answer.add_argument("question_id")
    answer.add_argument("--source", required=True, help="入向事件来源（通道名，例如 feishu）")
    answer.add_argument("--event-id", required=True, dest="event_id")
    answer.add_argument("--note", default=None)

    declare = sub.add_parser("declare", parents=[common],
                             help="记手工文字：只算 declared，⛔ 不改状态、不冒充已回答")
    declare.add_argument("question_id")
    declare.add_argument("--body", required=True)
    declare.add_argument("--source", action="append", default=[], help="证据文件（可重复）")
    declare.add_argument("--note", default=None)

    enqueue = sub.add_parser("enqueue", parents=[common],
                             help="把待答内容排进现交付台账（kind=notice）；没通道就留在队列")
    enqueue.add_argument("question_id")

    link = sub.add_parser("link", parents=[common],
                          help="把旧/未关联的问题显式关联到实验（追加事件，不改旧行）")
    link.add_argument("question_id")
    link.add_argument("--exp", required=True)
    link.add_argument("--text", required=True, help="为什么算这个实验（理由，必填）")
    link.add_argument("--source", action="append", default=[])

    expire = sub.add_parser("expire", parents=[common], help="open -> expired（只认已过 deadline 的）")
    expire.add_argument("question_id")

    cancel = sub.add_parser("cancel", parents=[common], help="open -> cancelled（必须给理由）")
    cancel.add_argument("question_id")
    cancel.add_argument("--text", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.workspace:
        _dump({"ok": False, "error": "UsageError", "reason": "必须给 --workspace（子命令前后都认）"})
        return 2
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        _dump({"ok": False, "error": "NotInitialized",
               "reason": f"工作区不存在：{workspace}"})
        return 2
    readonly = args.action in ("open", "show")
    authenticate = None
    if args.action == "answer":
        # 只有需要核身份的写入口才构造通道；只读查询与本地登记一律不联网。
        try:
            authenticate, _channel = _authenticator(args)
        except Exception as exc:  # noqa: BLE001 - 配置问题要说清，且不回显凭据
            from kernel.autoresearch.decisions_cli import channel_error_text
            _dump({"ok": False, "error": type(exc).__name__,
                   "reason": channel_error_text(exc)})
            return 9
        if authenticate is None:
            _dump({"ok": False, "error": "NoTrustedChannel",
                   "reason": "没有可信通道配置 ⇒ 入向身份不可核，不能把文字当成用户已回答；"
                             "要给 --channel-config <JSON>（内核 FeishuChannel 配置）或 "
                             "--channel module:factory。⛔ 手工文字只能 `declare`（只算 declared）"})
            return 9
    try:
        ledger = Questions(workspace, db=args.db, readonly=readonly,
                           authenticate=authenticate)
    except (QuestionError, OSError) as exc:
        # 构造失败是环境/输入问题（未初始化、旧 schema、库不可读），不是台账规则拒绝。
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    try:
        if args.action == "open":
            _dump({"ok": True, **ledger.open_questions(args.exp)})
            return 0
        if args.action == "show":
            _dump({"ok": True, "question": ledger.show(args.question_id)})
            return 0
        if args.action == "ask":
            item = ledger.ask(args.exp, args.body, owner=args.owner,
                              asked_by=args.asked_by or args.actor,
                              question_id=args.question_id, topic=args.topic,
                              recipient=args.recipient, deadline=args.deadline,
                              blocking=bool(args.blocking), note=args.note)
            _dump({"ok": True, "question": item,
                   "reply_hint": item.get("reply_hint")})
            return 0
        if args.action == "answer":
            item = ledger.answer(args.question_id, source=args.source,
                                 event_id=args.event_id, note=args.note)
            _dump({"ok": True, "question": item})
            return 0
        if args.action == "declare":
            item = ledger.declare(args.question_id, body=args.body, actor=args.actor,
                                  sources=_sources(workspace, args.source), note=args.note)
            _dump({"ok": True, "question": item,
                   "note": "只记成 declared：⛔ 不算用户已回答，状态仍是 open"})
            return 0
        if args.action == "enqueue":
            _dump({"ok": True, "delivery": ledger.enqueue(args.question_id, actor=args.actor)})
            return 0
        if args.action == "link":
            item = ledger.link(args.question_id, exp_id=args.exp, actor=args.actor,
                               reason=args.text,
                               sources=_sources(workspace, args.source))
            _dump({"ok": True, "question": item})
            return 0
        if args.action == "expire":
            _dump({"ok": True, "question": ledger.expire(args.question_id, actor=args.actor)})
            return 0
        if args.action == "cancel":
            _dump({"ok": True, "question": ledger.cancel(args.question_id, reason=args.text,
                                                         actor=args.actor)})
            return 0
        raise ValueError(f"不支持的命令：{args.action}")
    except QuestionConflict as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 9
    except QuestionError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 9
    except (OSError, json.JSONDecodeError) as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    except ValueError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
