# GENERATED-BY: portable reporting migration, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00（ack 接真实宿主验证器）
# PURPOSE: reporting 的命令行入口 —— 供料、只读检查、验收回执、运行/整轮事件、整轮交付检查、只读诊断与流程状态投影。
# ORIGIN: 上游供料与交付工具的 CLI 形态（同一工具多模式、JSON 输出、退出码区分「检查未过」与「参数错误」）。
# CUMULATIVE-DELTA: 2026-11-09 ack 接真实宿主：显式 --proof-json + --owner-session 时用
#         `DSHHost(workspace=工作区)` 核验（证据根只用宿主默认，⛔ CLI 不提供自选根）；
#         不给 proof 时仍是 declared 自述。六个主命令参数全部带 help，另加 input / fail / status / process-status。

"""reporting 命令行。

退出码约定：

* `0` 成功（或检查通过）；
* `2` 参数/状态被拒（消息里写清缺哪一步与下一步命令）；
* `9` 检查未通过（页面待更新、整轮未交付、诊断待核）。

⛔ 本 CLI 不启动宿主子会话、不发消息、不改研究页以外的任何东西。

`ack` 的两条路：

* **不给** `--proof-json` ⇒ 不注入验证器，回执记 `declared`（调用方自述，不证明真实派工或送达）；
* **给了** `--proof-json '{"session_id","seq"}'`，且必须配 `--owner-session` ⇒ 构造
  `DSHHost(workspace=<工作区>)` 真核：会话可见回传、父子谱系、派工消息、写入工具整页内容，
  证据齐全才 `verified`。⛔ 证据根只用宿主默认（`~/.dsh`）或部署方自建实例，**CLI 不提供自选根**。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from kernel.autoresearch import reporting


def _source_from_args(args):
    if args.source:
        return {'path': args.source, 'locator': args.locator or ''}
    if args.session_id or args.message_id:
        return {'session_id': args.session_id or '', 'message_id': args.message_id or ''}
    raise reporting.ReportingError('必须给 --source + --locator（工作区内相对路径），'
                                   '或 --session-id + --message-id（宿主消息）')


def _proof_from_args(text):
    if not text:
        return None
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise reporting.ReportingError(f'--proof-json 不是合法 JSON：{exc}')
    if not isinstance(document, dict):
        raise reporting.ReportingError('--proof-json 必须是对象 {session_id, seq}')
    return document


def build_parser():
    parser = argparse.ArgumentParser(
        prog='reporting',
        description='研究现状页供料与验收；只读检查不写文件，验收回执默认只记调用方自述（declared）。')
    parser.add_argument('--workspace', default='.',
                        help='研究工作区根目录（默认当前目录）；人读页在 '
                             '<专题目录>/STATUS.md（与专题主文档同目录；专题目录由 '
                             '.autoresearch/workspace-layout.json 决定，无标记时是 topics/<专题>），'
                             '机器供料/回执在 <工作区>/.autoresearch/state/reporting/<专题>/')
    subparsers = parser.add_subparsers(dest='command', required=True,
                                       metavar='{prepare,check,ack,event,round-check,diagnose,input,fail,status,process-status}')

    preparer = subparsers.add_parser(
        'prepare', help='生成完整供料包（来源清单 + 全文 + 旧页全文 + 缺项补读），并登记来源快照')
    preparer.add_argument('--topic', required=True, help='专题名（只允许字母、数字与 . _ -）')
    preparer.add_argument('--source', action='append', default=[], metavar='REL_PATH',
                          help='追加显式来源（工作区内相对路径，可重复）；越界路径会被拒。'
                               '默认已扫专题目录的 TOPIC/OBJECTIVE/REQUIREMENTS/EVIDENCE*、'
                               'plans/** 与 sources.json 清单，通常不必手工加')
    preparer.add_argument('--inline-limit', type=int, default=reporting.INLINE_LIMIT_BYTES,
                          help='单个来源内联上限（字节，默认 %(default)s）；超过它的来源显式标'
                               '「未内联」并给补读路径，旧页面永远全文附带')

    checker = subparsers.add_parser('check', help='只读检查页面、来源版本与回执一致性（不写任何文件）')
    checker.add_argument('--topic', required=True, help='专题名')
    checker.add_argument('--required', action='store_true',
                         help='要求必须已有有效回执；不接受「历史页未接入」豁免（整轮交付用）')

    ack = subparsers.add_parser('ack', help='验收 scribe 写的研究正文并写回执（角色必须是 scribe）')
    ack.add_argument('--topic', required=True, help='专题名')
    ack.add_argument('--writer-role', required=True, choices=[reporting.WRITER_ROLE],
                     help='写页角色；只能是 %(choices)s（负责人自写不验收）')
    ack.add_argument('--writer-session', required=True,
                     help='实际写页会话 ID；空值拒收（回执要记清是谁写的）')
    ack.add_argument('--proof-json', default=None, metavar='JSON',
                     help='宿主可见回传的 proof：{"session_id": "...", "seq": 12}；'
                          '给了它就走真实宿主核验（DSHHost，工作区为本命令的 --workspace），'
                          '必须同时给 --owner-session；不给它只记 declared 自述')
    ack.add_argument('--owner-session', default=None,
                     help='派工负责人会话 ID；配 --proof-json 时必填（宿主用它核父子谱系与派工消息）')
    ack.add_argument('--report', default=None, help='写页报告路径或内联 JSON（可选，仅作数据记录）')

    event = subparsers.add_parser('event', help='追加运行事件（run 范围 = 单臂；round 范围 = 整轮）')
    event.add_argument('--topic', required=True, help='专题名')
    event.add_argument('--exp', required=True, help='实验号（只允许字母、数字与 . _ -）')
    event.add_argument('--kind', required=True, choices=list(reporting.EVENT_KINDS),
                       help='事件种类：%(choices)s；终态（completed/failed/stopped/superseded）必须带 --reason')
    event.add_argument('--event-id', required=True,
                       help='事件 ID；同一 ID 重复提交内容一致按幂等处理，内容不同则拒')
    event.add_argument('--scope', default='run', choices=list(reporting.SCOPES),
                       help='事件范围：run = 单臂/单次运行，round = 整轮（整轮交付只认 round）')
    event.add_argument('--source', help='来源文件（工作区内相对路径）')
    event.add_argument('--locator', help='来源定位：行号 / 节标题 / 消息序号 / 运行 ID')
    event.add_argument('--session-id', help='或给宿主消息的会话 ID（与 --message-id 配对）')
    event.add_argument('--message-id', help='或给宿主消息 ID（与 --session-id 配对）')
    event.add_argument('--reason', default=None,
                       help='真实原因：为什么结束/失败/停跑/被替代（终态必填）')
    event.add_argument('--exit-code', type=int, default=None,
                       help='该次运行的原始退出码（可选，仅记录；文档失败不得覆盖它）')

    round_check = subparsers.add_parser(
        'round-check', help='整轮交付检查：round 终态 + 负责人判断 + 当前页回执')
    round_check.add_argument('--topic', required=True, help='专题名')
    round_check.add_argument('--exp', required=True, help='要检查的实验号（整轮）')

    diagnose = subparsers.add_parser(
        'diagnose', help='只读诊断：checkpoint 时效、判定、页面交付状态（不写文件/锁/日志/DB）')
    diagnose.add_argument('--topic', required=True, help='专题名')
    diagnose.add_argument('--checkpoint-max-age-hours', type=float,
                          default=reporting.CHECKPOINT_MAX_AGE_HOURS,
                          help='checkpoint 时效阈值（小时，默认 %(default)s）；'
                               '超过或时间不可判 ⇒ 状态待核')
    diagnose.add_argument('--db', default=None,
                          help='可选：只读打开的运行时数据库（相对工作区或绝对路径）；'
                               '不给就写 UNKNOWN，不推断租约')

    owner_input = subparsers.add_parser(
        'input', help='登记负责人输入：用户要求（user_requirement）或整轮判断（leader_conclusion）')
    owner_input.add_argument('--topic', required=True, help='专题名')
    owner_input.add_argument('--kind', required=True, choices=list(reporting.INPUT_KINDS),
                             help='输入种类：%(choices)s；leader_conclusion 必须配 --exp')
    owner_input.add_argument('--text', required=True, help='输入正文（用户原话或负责人结论）')
    owner_input.add_argument('--source', help='来源文件（工作区内相对路径）')
    owner_input.add_argument('--locator', help='来源定位：行号 / 节标题 / 消息序号')
    owner_input.add_argument('--session-id', help='或给宿主消息的会话 ID（与 --message-id 配对）')
    owner_input.add_argument('--message-id', help='或给宿主消息 ID（与 --session-id 配对）')
    owner_input.add_argument('--exp', default=None, help='对哪一轮的结论（leader_conclusion 必填）')
    owner_input.add_argument('--input-id', default=None,
                             help='输入 ID（可选，默认按内容算；同一 ID 内容不同则拒）')

    failure = subparsers.add_parser('fail', help='记录一次写页失败，任务回到待重试（保留原因与待办）')
    failure.add_argument('--topic', required=True, help='专题名')
    failure.add_argument('--reason', required=True, help='失败原因（必填）')

    status = subparsers.add_parser('status', help='只读打印流程状态投影（不写 process_status/）')
    status.add_argument('--topic', required=True, help='专题名')
    status.add_argument('--checkpoint-max-age-hours', type=float,
                        default=reporting.CHECKPOINT_MAX_AGE_HOURS,
                        help='checkpoint 时效阈值（小时，默认 %(default)s）')
    status.add_argument('--db', default=None, help='可选：只读打开的运行时数据库')

    process_status = subparsers.add_parser(
        'process-status', help='把流程状态写进 process_status/（JSON + 摘要），⛔ 不碰研究页')
    process_status.add_argument('--topic', required=True, help='专题名')
    process_status.add_argument('--checkpoint-max-age-hours', type=float,
                                default=reporting.CHECKPOINT_MAX_AGE_HOURS,
                                help='checkpoint 时效阈值（小时，默认 %(default)s）')
    process_status.add_argument('--db', default=None, help='可选：只读打开的运行时数据库')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = Path(args.workspace)
    try:
        if args.command == 'prepare':
            result = reporting.prepare(workspace, args.topic,
                                       extra_sources=args.source or None,
                                       inline_limit=args.inline_limit)
            return _emit(result, 0)
        if args.command == 'check':
            result = reporting.check(workspace, args.topic, required=args.required)
            return _emit(result, 0 if result.get('ok') else 9)
        if args.command == 'ack':
            report = args.report
            if report:
                # 可能是路径，也可能是一段内联 JSON；太长的字符串当路径会抛 OSError，别让它盖掉参数错误。
                try:
                    is_file = Path(report).is_file()
                except OSError:
                    is_file = False
                if is_file:
                    report = Path(report).read_text(encoding='utf-8')
            proof = _proof_from_args(args.proof_json)
            host = None
            if proof is not None:
                if not args.owner_session:
                    raise reporting.ReportingError(
                        '走真实宿主核验必须同时给 --owner-session（派工负责人会话 ID）；'
                        '宿主要用它核父子谱系与派工消息')
                host = _host(workspace)
            result = reporting.acknowledge(
                workspace, args.topic, args.writer_session, writer_role=args.writer_role,
                host_verifier=host, proof=proof, owner_session=args.owner_session,
                report=report)
            return _emit(result, 0)
        if args.command == 'event':
            result = reporting.record_event(
                workspace, args.topic, args.exp, args.kind, args.event_id,
                _source_from_args(args), scope=args.scope, reason=args.reason,
                exit_code=args.exit_code)
            return _emit(result, 0)
        if args.command == 'round-check':
            result = reporting.check_round(workspace, args.topic, args.exp)
            return _emit(result, 0 if result.get('ok') else 9)
        if args.command == 'diagnose':
            result = reporting.diagnose(
                workspace, args.topic,
                checkpoint_max_age_hours=args.checkpoint_max_age_hours, db=args.db)
            return _emit(result, 0 if result.get('ok') else 9)
        if args.command == 'input':
            result = reporting.record_input(
                workspace, args.topic, args.kind, args.text, _source_from_args(args),
                exp=args.exp, input_id=args.input_id)
            return _emit(result, 0)
        if args.command == 'fail':
            result = reporting.failed(workspace, args.topic, args.reason)
            return _emit(result, 0)
        if args.command == 'status':
            result = reporting.process_status_projection(
                workspace, args.topic,
                checkpoint_max_age_hours=args.checkpoint_max_age_hours, db=args.db)
            return _emit(result, 0)
        if args.command == 'process-status':
            result = reporting.write_process_status(
                workspace, args.topic,
                checkpoint_max_age_hours=args.checkpoint_max_age_hours, db=args.db)
            return _emit(result, 0)
        raise reporting.ReportingError(f'不支持的命令：{args.command}')
    except (reporting.ReportingError, OSError, ValueError, sqlite3.Error, KeyError, TypeError,
            ImportError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'reason': str(exc),
                          'problems': getattr(exc, 'problems', [])},
                         ensure_ascii=False, indent=2))
        return 2


def _host(workspace):
    """构造真实宿主核验器（`DSHHost`）。

    ⛔ 只给可信工作区；证据根用宿主默认（`~/.dsh`）或部署方自己构造的实例，
    CLI **不提供**「自选证据根」的参数 —— 让被验方挑证据等于没验。
    """
    from kernel.autoresearch.host_evidence import DSHHost
    return DSHHost(workspace=Path(workspace).resolve())


def _emit(result, code):
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
