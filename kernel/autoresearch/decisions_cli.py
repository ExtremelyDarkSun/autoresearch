# GENERATED-BY: portable extraction / neutral decision layer, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-10 +08:00（加 --channel-config：按 JSON 配置直接构造内核 FeishuChannel）
# PURPOSE: 决策审批链的命令行入口 —— prepare / send / status / inbox / overdue / reply / hook / authorize-check
# ORIGIN: none（新文件）。与 kernel/autoresearch/decisions.py 配套；通道由部署方以 --channel 或 --channel-config 注入
# CUMULATIVE-DELTA: 首版。零外部配置可离线建页与诊断；发送与审批一律要求真实通道与真实事件。
#   2026-11-09 补：prepare 可带完整执行规范（--execution-plan），新增 authorize-check 供人工/主链复核。
#   2026-11-10 补：新增 --channel-config（内联 JSON / JSON 文件 / @文件）直接构造内核 FeishuChannel；
#   --channel 工厂保留，两者互斥。配置里只许 env: 凭据引用，报错不回显原值。
"""决策审批链 CLI。

    python -m kernel.autoresearch.decisions_cli --db <runtime.sqlite3> prepare --exp exp-01 \\
        --decision-id E1.1 --plan plan.json --recipient chat-01 \\
        --scope large --owner-session sess-01 --reason "大题单要人批" --undecided A,B \\
        --execution-plan workflow_plan.json

    python -m kernel.autoresearch.decisions_cli --db <...> send --decision E1.1 --channel deploy.py:channel
    python -m kernel.autoresearch.decisions_cli --db <...> send --decision E1.1 \\
        --channel-config '{"app_id_ref": "env:FEISHU_APP_ID", …}'
    python -m kernel.autoresearch.decisions_cli --db <...> status --decision E1.1
    python -m kernel.autoresearch.decisions_cli --db <...> inbox --source chat --event ev.json --channel deploy.py:channel
    python -m kernel.autoresearch.decisions_cli --db <...> overdue --exp exp-01
    python -m kernel.autoresearch.decisions_cli --db <...> reply --exp exp-01 --event ev-1 --body "…（exp-01）…"
    python -m kernel.autoresearch.decisions_cli --db <...> hook --exp exp-01
    python -m kernel.autoresearch.decisions_cli --db <...> authorize-check --plan workflow_plan.json

**通道只有两个来源，互斥**：

- `--channel module:factory` / `--channel path/to/file.py:factory`：部署方自己的工厂。
- `--channel-config <JSON>`：直接用内核 `channels/feishu.py` 的 `FeishuConfig` + `FeishuChannel`。
  值可以是内联 JSON 文本、一个 JSON 文件路径，或 `@文件`。配置里**只能有 `env:NAME` 凭据引用**；
  出现凭据值直接拒绝，且报错**不回显那个值**（见 `channel_error_text`）。

`--channel-config` 不放松任何判据：没开 `send_enabled`、缺允许列表、缺台账或凭据取不到值时，
`send` / 回复仍被核心拦下并按 fail-closed 返回 exit 2。它也不发任何网络请求 —— 构造通道只建本地台账。

收件侧（webhook 常驻、拉取已认证入向）不在本文件：用 `kernel/autoresearch/channels_cli.py` 的
`doctor` / `listen` / `poll`。

**这里没有 `--approve` / `--fake` / `--force`**：批准只能来自一条落盘的真实入向事件，
送达只能来自真实通道返回的回执。未配通道时 `send` / 审批一律 fail-closed（exit 2），
但 `prepare`（生成自包含审批页）与 `status` / `overdue` / `authorize-check` 的**读取部分**
零配置可用。

**查询与写入入口分开**：

- `status` / `overdue` / `authorize-check` 直接使用 `DecisionService.readonly(db)`，不装载通道、不建库、不迁移；缺库或缺 schema 返回不可判及非零退出码。
- 活动 WAL 数据库复用已有共享内存；SQLite 可能更新其读锁字节。缺共享内存时不创建辅助文件，也不忽略 WAL 中已提交的数据。
- `prepare` / `send` / `inbox` / `reply` / `hook` 使用写服务，允许初始化数据库。
- `hook` 调 `progress_verdict()` → `tick()`，会把过期决策转为 expired 并追加事件。

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
    from kernel.autoresearch.channels import feishu
    from kernel.autoresearch.decisions import (DecisionService, DecisionError, ReadinessError,
                                               DecisionConflict, OrderingViolated,
                                               ChannelUnavailable, ReceiptRejected,
                                               IdentityRefused, QuarantineBlocked,
                                               OverdueInbound, SCOPES,
                                               TEXT_REPLY_GRACE_SECONDS)
except ImportError:  # pragma: no cover
    from kernel.autoresearch.runtime.store import Store
    from kernel.autoresearch.channels import feishu
    from kernel.autoresearch.decisions import (DecisionService, DecisionError, ReadinessError,
                                               DecisionConflict, OrderingViolated,
                                               ChannelUnavailable, ReceiptRejected,
                                               IdentityRefused, QuarantineBlocked,
                                               OverdueInbound, SCOPES,
                                               TEXT_REPLY_GRACE_SECONDS)

EXIT_OK, EXIT_CONFIG, EXIT_BLOCKED, EXIT_OVERDUE = 0, 2, 3, 9

#: `--channel-config` 用来显式指明「后面是文件」的前缀：`--channel-config @channel.json`。
CONFIG_FILE_PREFIX = '@'


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


def channel_error_text(exc) -> str:
    """通道 / 配置错误 → 可打印文本。⛔ **不回显**配置里写的疑似凭据原值。

    `channels/feishu.py` 的 `FeishuConfigError` 有几条消息会把调用方写进去的值带进正文
    （例如 `app_id_ref` 写成字面量时）。那正是最不该进日志的东西，所以这里做三类处理：
    键名类消息（只含键名）原样保留、引用形态类消息只留字段名、其余「，现在是 …」截掉后半段。
    """
    text = str(exc)
    if '不许出现凭据值键' in text:
        return text.split('⇒', 1)[0].strip()
    if '配置文件不许含凭据值' in text:
        field = text.split(' 必须写成', 1)[0].strip()
        if not field or ' ' in field:
            field = '凭据字段'
        return f'{field} 必须是 "env:NAME" 形式的凭据引用；配置里的原值已隐去'
    if '，现在是' in text:
        return text.split('，现在是', 1)[0].strip() + '（配置原值已隐去）'
    return text


def _channel_config_text(spec):
    """`--channel-config` 的值 → `(JSON 文本, 来源说明)`。

    三种写法：内联 JSON 对象文本 / 已存在的 JSON 文件路径 / `@JSON 文件路径`。
    ⛔ 只读文件，不建通道、不发请求、不读任何凭据值。
    """
    text = str(spec).strip()
    if not text:
        raise ChannelUnavailable('--channel-config 给的是空值')
    if text.startswith(CONFIG_FILE_PREFIX):
        path = Path(text[len(CONFIG_FILE_PREFIX):]).expanduser()
        if not path.is_file():
            raise ChannelUnavailable(f'--channel-config 指向的文件不存在：{path}')
        return path.read_text(encoding='utf-8'), str(path)
    if text.startswith(('{', '[')):
        return text, '内联 JSON'
    candidate = Path(text).expanduser()
    if candidate.is_file():
        return candidate.read_text(encoding='utf-8'), str(candidate)
    raise ChannelUnavailable(
        '--channel-config 既不是 JSON 对象文本，也不是存在的文件路径 ⇒ '
        '写成 \'{"app_id_ref": "env:FEISHU_APP_ID", …}\'、channel.json 或 @channel.json')


def load_channel_config_mapping(spec):
    """`--channel-config` 的值 → 配置字典（键见 `channels/feishu.py` 的 `FeishuConfig`）。

    ⛔ 这里不校验「有没有凭据值」—— 那由 `FeishuConfig.__post_init__` 负责，因为那边知道
    哪些键属于凭据。本函数只保证「读得到、是 JSON 对象」。
    """
    raw, origin = _channel_config_text(spec)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ChannelUnavailable(f'--channel-config 不是合法 JSON（{origin}）：{exc}') from exc
    if not isinstance(data, dict):
        raise ChannelUnavailable('--channel-config 必须是 JSON 对象（键见 channels/feishu.py 的'
                                 ' FeishuConfig：app_id_ref / app_secret_ref / encrypt_key_ref …）')
    return data


def load_feishu_config(spec):
    """`--channel-config` 的值 → `feishu.FeishuConfig`。凭据只允许 `env:NAME` 引用。"""
    mapping = load_channel_config_mapping(spec)
    try:
        return feishu.from_mapping(mapping)
    except feishu.FeishuConfigError as exc:
        raise ChannelUnavailable(f'--channel-config 不成立：{channel_error_text(exc)}') from exc


def load_channel_config(spec, **kwargs):
    """按部署配置 JSON 直接构造内核 `FeishuChannel`（`channels/feishu.py`）。

    `kwargs` 只给离线注入用（`transport` / `credential_source` / `clock` / `ledger`）；
    ⛔ 构造通道不发任何网络请求：它只开本地台账。真实发送仍要过 `health()` 与 `send_enabled`。
    """
    if spec is None or str(spec).strip() == '':
        return None
    config = load_feishu_config(spec)
    try:
        return feishu.FeishuChannel(config, **kwargs)
    except feishu.FeishuError as exc:
        raise ChannelUnavailable(f'--channel-config 无法构造 FeishuChannel：'
                                 f'{channel_error_text(exc)}') from exc


def select_channel(args):
    """按 `--channel` / `--channel-config` 选通道来源。两者互斥，缺省返回 None（fail-closed）。"""
    spec = getattr(args, 'channel', None)
    config_spec = getattr(args, 'channel_config', None)
    if spec and config_spec:
        raise ChannelUnavailable('--channel 与 --channel-config 互斥：一次只能给一个通道来源')
    if config_spec:
        return load_channel_config(config_spec)
    return load_channel(spec)


def _service(args):
    """需要**真实通道**的命令（send / reply / inbox / prepare / hook）：装 Store + 通道 + 服务。"""
    store = Store(Path(args.db))
    channel = select_channel(args)
    return DecisionService(store, channel=channel)


def _readonly_service(db_path):
    """只读命令直接使用同版本内核的只读工厂，不装载消息通道。

    缺库或缺 schema 由工厂查询返回不可判；不回退到会建表的普通构造。
    """
    return DecisionService.readonly(str(db_path))


def _reader(args):
    return _readonly_service(args.db)


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
    """人工 / 主链可跑的准入复核：只读决策台账，打印判词，不产生批准。

    直接使用只读工厂，不装载通道、不建库或迁移 schema；缺少可读证据则不可判。
    """
    service = _reader(args)
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
    service = _reader(args)
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
    service = _reader(args)
    status = service.reply_status(args.exp, grace=args.grace)
    for item in status['overdue']:
        item['action'] = (f"reply --exp {item.get('exp_id')} --event {item.get('event_id')}"
                          ' --body "<取自产物的答案，写明实验号>"')
    _print(status)
    if status.get('undecidable'):
        # 只读构造下「读不到」不是「没有超期」：⛔ 不许把它报成 exit 0。
        return int(status.get('exit_code') or EXIT_CONFIG)
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
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--channel', default=None,
                        help='部署方通道：module:factory 或 path/to/file.py:factory（不配则 fail-closed）')
    source.add_argument('--channel-config', dest='channel_config', default=None,
                        help='通道配置 JSON（内联 / 文件 / @文件）：直接构造内核 FeishuChannel；'
                             '凭据只写 env:NAME 引用。与 --channel 互斥')
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

    hook = sub.add_parser('hook', help='给推进器 / 主链的单一判词（会 tick 收口过期决策 ⇒ 有写入）')
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
