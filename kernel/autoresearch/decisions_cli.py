# GENERATED-BY: portable extraction / neutral decision layer, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00（加 --execution-plan 与 authorize-check）
# PURPOSE: 决策审批链的命令行入口 —— prepare / send / status / inbox / overdue / reply / hook / authorize-check
# ORIGIN: none（新文件）。与 kernel/autoresearch/decisions.py 配套；通道由部署方以 --channel 注入
# CUMULATIVE-DELTA: 首版。零外部配置可离线建页与诊断；发送与审批一律要求真实通道与真实事件。
#   本轮补：prepare 可带完整执行规范（--execution-plan），新增 authorize-check 供人工/主链复核。
"""决策审批链 CLI。

    python -m kernel.autoresearch.decisions_cli --db <runtime.sqlite3> prepare --exp exp-01 \\
        --decision-id E1.1 --plan plan.json --recipient chat-01 \\
        --scope large --owner-session sess-01 --reason "大题单要人批" --undecided A,B \\
        --execution-plan workflow_plan.json

    python -m kernel.autoresearch.decisions_cli --db <...> send --decision E1.1 --channel deploy.py:channel
    python -m kernel.autoresearch.decisions_cli --db <...> status --decision E1.1
    python -m kernel.autoresearch.decisions_cli --db <...> inbox --source chat --event ev.json --channel deploy.py:channel
    python -m kernel.autoresearch.decisions_cli --db <...> overdue --exp exp-01
    python -m kernel.autoresearch.decisions_cli --db <...> reply --exp exp-01 --event ev-1 --body "…（exp-01）…"
    python -m kernel.autoresearch.decisions_cli --db <...> hook --exp exp-01
    python -m kernel.autoresearch.decisions_cli --db <...> authorize-check --plan workflow_plan.json

**这里没有 `--approve` / `--fake` / `--force`**：批准只能来自一条落盘的真实入向事件，
送达只能来自真实通道返回的回执。未配 `--channel` 时 `send` / 审批一律 fail-closed（exit 2），
但 `prepare`（生成自包含审批页）、`status`、`overdue`、`hook` 的只读部分零配置可用。

退出码：`0` 正常 · `2` 参数 / 配置 / 通道 / 身份失败（fail-closed）· `3` 需人工处置（隔离、
链路不完整、未获授权）· `9` 有超期未回的入向文字（RED，阻持续推进）。
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
try:
    from kernel.autoresearch.runtime.store import Store
    from kernel.autoresearch.decisions import (DecisionService, DecisionError, ReadinessError,
                                               DecisionConflict, OrderingViolated,
                                               ChannelUnavailable, ReceiptRejected,
                                               IdentityRefused, QuarantineBlocked,
                                               OverdueInbound, SCOPES,
                                               TEXT_REPLY_GRACE_SECONDS)
except ImportError:  # pragma: no cover
    from kernel.autoresearch.runtime.store import Store
    from kernel.autoresearch.decisions import (DecisionService, DecisionError, ReadinessError,
                                               DecisionConflict, OrderingViolated,
                                               ChannelUnavailable, ReceiptRejected,
                                               IdentityRefused, QuarantineBlocked,
                                               OverdueInbound, SCOPES,
                                               TEXT_REPLY_GRACE_SECONDS)

EXIT_OK, EXIT_CONFIG, EXIT_BLOCKED, EXIT_OVERDUE = 0, 2, 3, 9


def load_channel(spec):
    """按 `module:factory` 或 `path/to/file.py:factory` 载入部署方通道。

    ⚠️ 通道**不在核心里**：它由部署方实现并注入。工厂函数必须返回一个带
    `health()` / `send_file()` / `send_card()` / `verify_receipt()` 的对象；
    平台触达条件（例如群聊要 @ 机器人）由适配层自己声明，核心只记录。
    """
    if not spec:
        return None
    if ':' not in spec:
        raise ChannelUnavailable('--channel 要写成 module:factory 或 path/to/file.py:factory')
    target, factory_name = spec.rsplit(':', 1)
    try:
        if target.endswith('.py') or '/' in target or '\\' in target:
            path = Path(target).resolve()
            if not path.is_file():
                raise ChannelUnavailable(f'通道模块不存在：{path}')
            module_spec = importlib.util.spec_from_file_location('_deploy_channel', path)
            if module_spec is None or module_spec.loader is None:
                raise ChannelUnavailable(f'无法载入通道模块：{path}')
            module = importlib.util.module_from_spec(module_spec)
            sys.modules['_deploy_channel'] = module
            module_spec.loader.exec_module(module)
        else:
            module = importlib.import_module(target)
    except ChannelUnavailable:
        raise
    except Exception as exc:
        raise ChannelUnavailable(f'载入通道失败：{type(exc).__name__}: {exc}') from exc
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        raise ChannelUnavailable(f'通道模块 {target} 里没有可调用的工厂 {factory_name}()')
    channel = factory()
    if channel is None:
        raise ChannelUnavailable('通道工厂返回了 None')
    return channel


def _service(args):
    store = Store(Path(args.db))
    channel = load_channel(getattr(args, 'channel', None))
    return DecisionService(store, channel=channel)


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _load_plan(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise DecisionError('方案文件必须是 JSON 对象')
    if 'plan_set' in data or 'decision' in data:
        return data.get('plan_set') or {}, data.get('decision') or {}
    return data, {}


def _load_execution(path):
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(data, dict) and isinstance(data.get('plan'), dict):
        data = data['plan']
    if not isinstance(data, dict):
        raise DecisionError('执行计划必须是 JSON 对象')
    return data


def _cmd_prepare(args):
    service = _service(args)
    plan_set, embedded = _load_plan(args.plan)
    decision = dict(embedded)
    decision.update({'decision_id': args.decision_id})
    if args.recipient:
        decision['recipient'] = args.recipient
    if args.scope:
        decision['scope'] = args.scope
    if args.owner_session:
        decision['owner_session'] = args.owner_session
    if args.short_code:
        decision['short_code'] = args.short_code
    if args.undecided is not None:
        decision['undecided'] = [item.strip() for item in args.undecided.split(',') if item.strip()]
    if args.recommended:
        decision['recommended'] = args.recommended
    decision['needs_user'] = bool(args.needs_user)
    if args.reason:
        decision['reason'] = args.reason
    view = service.prepare(exp_id=args.exp, decision=decision, plan_set=plan_set,
                           page_dir=args.out, evidence=args.evidence or [],
                           aggregate=args.aggregate or '',
                           execution_plan=_load_execution(args.execution_plan))
    _print(view)
    return EXIT_OK


def _cmd_authorize_check(args):
    """人工 / 主链可跑的准入复核：只读决策台账，打印判词，不产生批准。"""
    service = _service(args)
    plan = _load_execution(args.plan)
    owner_session = args.owner_session or str(plan.get('owner_session') or '')
    exp_id = args.exp or str(plan.get('exp_id') or '')
    verdict = service.check_authorization(plan=plan, owner_session=owner_session,
                                          exp_id=exp_id, scope=args.scope)
    _print(verdict)
    return EXIT_OK if verdict.get('ok') else EXIT_BLOCKED


def _cmd_send(args):
    service = _service(args)
    result = service.send(args.decision, worker=args.worker)
    _print(result)
    if result['ok']:
        return EXIT_OK
    return EXIT_BLOCKED if result.get('status') == 'quarantined' else EXIT_CONFIG


def _cmd_status(args):
    service = _service(args)
    status = service.status(args.decision, exp_id=args.exp)
    _print(status)
    rows = status.get('decisions') if 'decisions' in status else [status]
    for row in rows:
        if row.get('exit_code'):
            return row['exit_code']
    return EXIT_OK


def _cmd_inbox(args):
    service = _service(args)
    event = json.loads(Path(args.event).read_text(encoding='utf-8'))
    row = service.ingest(args.source, event)
    _print({'source': row['source'], 'event_id': row['event_id'],
            'status': row['status'], 'consumed': bool(row['consumed']),
            'category': row['category'], 'principal': row['principal'],
            'error': row['error'],
            'result': json.loads(row['result_json']) if row['result_json'] else None})
    if row['status'] in ('rejected', 'received', 'command_pending'):
        # 没配身份校验器 / 没配真实通道时这里就是 fail-closed：事件落盘，但不产生批准
        return EXIT_CONFIG
    return EXIT_OK


def _cmd_overdue(args):
    service = _service(args)
    status = service.reply_status(args.exp, grace=args.grace)
    for item in status['overdue']:
        item['action'] = (f"reply --exp {item.get('exp_id')} --event {item.get('event_id')}"
                          ' --body "<取自产物的答案，写明实验号>"')
    _print(status)
    return EXIT_OVERDUE if status['overdue'] else EXIT_OK


def _cmd_reply(args):
    service = _service(args)
    claim = service.claim_reply(args.worker, exp_id=args.exp)
    if claim is None:
        _print({'ok': False, 'reason': '没有待回的入向文字（或已被别的实例领走）'})
        return EXIT_BLOCKED
    if claim['event_id'] != args.event:
        _print({'ok': False, 'reason': f"最老的待回事件是 {claim['event_id']}，不是 {args.event}；"
                                       '先回更老的那条（逐条回，跳着回会留下超期）'})
        return EXIT_BLOCKED
    task = service.answer(claim, args.body)
    payload = {'ok': True, 'delivery_id': task['id'], 'event_id': claim['event_id'],
               'exp_id': claim['exp_id'], 'status': 'queued'}
    if service.channel is not None:
        delivered = service.deliver_reply(task['id'], worker=args.worker)
        payload['delivery'] = delivered
    _print(payload)
    return EXIT_OK if payload.get('delivery', {}).get('ok', True) else EXIT_CONFIG


def _cmd_hook(args):
    service = _service(args)
    verdict = service.progress_verdict(args.exp)
    verdict['decisions'] = service.status(exp_id=args.exp).get('decisions', [])
    verdict['ok'] = verdict['ok'] and all(row['ok'] for row in verdict['decisions'])
    if not verdict['ok'] and verdict['exit_code'] == 0:
        verdict['exit_code'] = EXIT_BLOCKED
        verdict['next'] = verdict['next'] if verdict['next'] != 'continue' else 'fix_decisions'
    _print(verdict)
    return verdict['exit_code']


def build_parser():
    parser = argparse.ArgumentParser(prog='decisions_cli', description='决策审批链（中性核心）')
    parser.add_argument('--db', required=True, help='runtime.sqlite3 路径')
    parser.add_argument('--channel', default=None,
                        help='部署方通道：module:factory 或 path/to/file.py:factory（不配则 fail-closed）')
    sub = parser.add_subparsers(dest='command', required=True)

    prepare = sub.add_parser('prepare', help='建决策并生成自包含审批页（零配置可用）')
    prepare.add_argument('--exp', required=True)
    prepare.add_argument('--decision-id', required=True)
    prepare.add_argument('--plan', required=True, help='方案集 JSON（也可含 "decision" 段）')
    prepare.add_argument('--recipient', default='')
    prepare.add_argument('--scope', choices=SCOPES, default=None)
    prepare.add_argument('--owner-session', default='')
    prepare.add_argument('--short-code', default='')
    prepare.add_argument('--undecided', default=None, help='逗号分隔，例如 A,B')
    prepare.add_argument('--recommended', default=None)
    prepare.add_argument('--needs-user', dest='needs_user', action='store_true', default=True)
    prepare.add_argument('--no-needs-user', dest='needs_user', action='store_false')
    prepare.add_argument('--reason', default='')
    prepare.add_argument('--out', default=None, help='审批页目录（默认 <db 所在目录>/web）')
    prepare.add_argument('--evidence', nargs='*', default=None)
    prepare.add_argument('--aggregate', default='')
    prepare.add_argument('--execution-plan', default=None,
                         help='完整执行规范 JSON（argv/实现/预算/题集）；给了才可用于 Workflow 起跑')
    prepare.set_defaults(func=_cmd_prepare)

    send = sub.add_parser('send', help='先页后卡发送（必须有真实通道）')
    send.add_argument('--decision', required=True)
    send.add_argument('--worker', default='decisions-cli')
    send.set_defaults(func=_cmd_send)

    status = sub.add_parser('status', help='按台账重算双回执与状态（零配置可用）')
    status.add_argument('--decision', default=None)
    status.add_argument('--exp', default=None)
    status.set_defaults(func=_cmd_status)

    inbox = sub.add_parser('inbox', help='落一条真实入向事件并尝试应用')
    inbox.add_argument('--source', required=True)
    inbox.add_argument('--event', required=True, help='事件 JSON 文件')
    inbox.set_defaults(func=_cmd_inbox)

    overdue = sub.add_parser('overdue', help='文字消息回复窗口诊断（零配置可用）')
    overdue.add_argument('--exp', default=None)
    overdue.add_argument('--grace', type=float, default=TEXT_REPLY_GRACE_SECONDS)
    overdue.set_defaults(func=_cmd_overdue)

    reply = sub.add_parser('reply', help='回一条文字（必须引用原始事件并写明实验号）')
    reply.add_argument('--exp', required=True)
    reply.add_argument('--event', required=True)
    reply.add_argument('--body', required=True)
    reply.add_argument('--worker', default='decisions-cli')
    reply.set_defaults(func=_cmd_reply)

    hook = sub.add_parser('hook', help='给推进器 / 主链的单一判词（零配置可用）')
    hook.add_argument('--exp', default=None)
    hook.set_defaults(func=_cmd_hook)

    check = sub.add_parser('authorize-check',
                           help='准入复核：拿当前实际 plan 去核真实批准（只读，零配置可用）')
    check.add_argument('--plan', required=True, help='当前实际 plan（完整执行规范）JSON')
    check.add_argument('--owner-session', default=None)
    check.add_argument('--exp', default=None)
    check.add_argument('--scope', choices=SCOPES, default=None)
    check.set_defaults(func=_cmd_authorize_check)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ReadinessError, DecisionConflict, DecisionError, OrderingViolated,
            ChannelUnavailable, ReceiptRejected, IdentityRefused, QuarantineBlocked,
            OverdueInbound) as exc:
        _print({'ok': False, 'error': type(exc).__name__, 'problems': getattr(exc, 'problems', []),
                'message': str(exc)})
        return EXIT_CONFIG
    except (OSError, ValueError, KeyError) as exc:
        _print({'ok': False, 'error': type(exc).__name__, 'message': str(exc)})
        return EXIT_CONFIG


if __name__ == '__main__':
    raise SystemExit(main())
