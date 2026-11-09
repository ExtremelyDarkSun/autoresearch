# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09 16:40 +08:00
# PURPOSE: Create real shared budget grants from host-verified user commands and reserve before execution.
# ORIGIN: runtime Accounts plus host evidence adapter
# CUMULATIVE-DELTA: 2026-11-09 grant 不再在 authorize 之前 ensure_experiment：实验登记改由 Accounts.authorize(create_experiment=True) 在同一 BEGIN IMMEDIATE 事务里、可信时序/fence 预检之后完成，后续拒绝整体回滚，不会留下先写后拒的实验行或事件。此前：2026-11-09 grant 先核真实用户命令再登记实验；事件文件/proof 重复键、错误字段、缺失库均 exit 2；status 只读不建表；补 release（未终态执行挡）与 settle（无受信计量适配器时只输出 pending 人工核账，不写状态）。此前：Never create budgets from a plan declaration; unknown cost remains reserved.
import argparse
import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from .admission import (BUDGET_ACTIONS, BUDGET_COMMAND_FIELDS, BUDGET_SCOPES,
                        HostBudgetSource)
from .host_evidence import DSHHost, EvidenceError, session_id
from .runtime.accounts import Accounts, ApprovalEvidence, SourcePolicy
from .runtime.store import Store

DB_RELATIVE = Path('.autoresearch/runtime.sqlite3')
_KEY_FIELDS = ('event_id', 'series_id', 'exp_id')
_BASE_TABLES = ('experiments', 'budget_accounts', 'budget_reservations', 'account_requests')
_REQUIRED_COLUMNS = {
    'experiments': {'id'},
    'budget_accounts': {'series_id', 'scope', 'expires_at', 'total_units',
                        'reserved_units', 'spent_units', 'active'},
    'budget_reservations': {'id', 'series_id', 'exp_id', 'scope', 'units', 'actual_units',
                            'status', 'created_at', 'updated_at'},
    'account_requests': {'request_key', 'payload_json', 'result_json'},
}
_LINE_LOCATOR = re.compile(r'^L([1-9]\d*)(?:-L?([1-9]\d*))?$')
_MAX_UNITS = 2 ** 63 - 1
_SETTLE_PENDING = 3


class BudgetCliError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BudgetCliError('JSON含重复键，不能作为可信命令：' + str(key))
        result[key] = value
    return result


def _reject_constant(value):
    raise BudgetCliError('JSON不允许非有限数：' + str(value))


def _parse_json(text, label):
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except BudgetCliError:
        raise
    except (ValueError, TypeError) as exc:
        raise BudgetCliError(label + '不是合法 JSON：' + str(exc)) from None


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise BudgetCliError(name + ' 必须是非空文字')
    return value


def _units(value, name):
    if type(value) is not int or not 0 <= value <= _MAX_UNITS:
        raise BudgetCliError(name + ' 必须是非负 int64 整数')
    return value


def _workspace(value):
    root = Path(value).resolve()
    if not root.is_dir():
        raise BudgetCliError('工作区不存在或不是目录：' + str(value))
    return root


def _database(root):
    try:
        path = (root / DB_RELATIVE).resolve()
    except OSError as exc:
        raise BudgetCliError('工作区预算数据库路径不可判：' + str(exc)) from None
    if not path.is_relative_to(root):
        raise BudgetCliError('工作区预算数据库越出工作区根目录')
    if not path.exists():
        raise BudgetCliError('工作区预算数据库不存在：' + str(DB_RELATIVE))
    if not path.is_file():
        raise BudgetCliError('工作区预算数据库不是普通文件')
    return path


def _readonly(db):
    return sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)


def _require_tables(db, names):
    try:
        with closing(_readonly(db)) as conn:
            conn.execute('PRAGMA query_only=ON')
            present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = [name for name in names if name not in present]
            if missing:
                raise BudgetCliError('预算数据库表结构不完整，缺少 ' + ', '.join(missing) + '；本命令不自动建表')
            lacking = []
            for name in names:
                columns = {row[1] for row in conn.execute('PRAGMA table_info(' + name + ')')}
                absent = sorted(_REQUIRED_COLUMNS.get(name, set()) - columns)
                if absent:
                    lacking.append(name + ' 缺列 ' + ','.join(absent))
            if lacking:
                raise BudgetCliError('预算数据库表结构不完整：' + '；'.join(lacking) + '；本命令不自动建表')
    except sqlite3.Error as exc:
        raise BudgetCliError('预算数据库不可读：' + str(exc)) from None


def _fetch(db, sql, params):
    try:
        with closing(_readonly(db)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
    except sqlite3.Error as exc:
        raise BudgetCliError('预算数据库不可读：' + str(exc)) from None


def _workflow_active(db, exp_id):
    """只读判断同实验是否有未写 wf_finishes 的 run（与 Accounts._finish 同一判据）。"""
    try:
        with closing(_readonly(db)) as conn:
            conn.execute('PRAGMA query_only=ON')
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wf_runs'").fetchone():
                return False
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wf_finishes'").fetchone():
                raise BudgetCliError('预算数据库含 wf_runs 但缺少 wf_finishes，不能判定执行是否终态')
            return conn.execute(
                'SELECT r.run_id FROM wf_runs r WHERE r.plan_id=? AND NOT EXISTS'
                ' (SELECT 1 FROM wf_finishes f WHERE f.run_id=r.run_id)',
                (exp_id,)).fetchone() is not None
    except sqlite3.Error as exc:
        raise BudgetCliError('预算数据库不可读：' + str(exc)) from None


def _account(db, series_id):
    return _fetch(db, 'SELECT * FROM budget_accounts WHERE series_id=?', (series_id,))


def _reservation(db, reservation_id):
    return _fetch(db, 'SELECT * FROM budget_reservations WHERE id=?', (reservation_id,))


def _experiment(db, exp_id):
    return _fetch(db, 'SELECT id FROM experiments WHERE id=?', (exp_id,))


def _load_event(root, event_file):
    raw = _text(event_file, 'event-file')
    candidate = Path(raw.replace('\\', '/'))
    if candidate.is_absolute() or '..' in candidate.parts:
        raise BudgetCliError('预算事件文件须是工作区内相对路径，不能绝对路径或含 ..')
    path = (root / candidate).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise BudgetCliError('预算事件文件不在工作区内或不存在：' + raw)
    try:
        text = path.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError as exc:
        raise BudgetCliError('预算事件文件必须是 UTF-8 文本：' + str(exc)) from None
    event = _parse_json(text, '预算事件文件')
    if not isinstance(event, dict):
        raise BudgetCliError('预算事件文件必须是 JSON 对象')
    if 'proof' in event:
        raise BudgetCliError('事件文件只含待核的用户命令；proof 单独用 --proof-json 提供')
    if set(event) != BUDGET_COMMAND_FIELDS:
        extra = sorted(set(event) - BUDGET_COMMAND_FIELDS)
        missing = sorted(BUDGET_COMMAND_FIELDS - set(event))
        raise BudgetCliError('预算命令字段不完整或有未知项；多余=' + repr(extra) + ' 缺少=' + repr(missing))
    return event


def _load_proof(text):
    proof = _parse_json(text, 'proof')
    if not isinstance(proof, dict) or set(proof) != {'session_id', 'seq'}:
        raise BudgetCliError('proof 必须恰为 {session_id,seq} 两个键，不接受证据文件路径')
    if type(proof['seq']) is not int or proof['seq'] < 0:
        raise BudgetCliError('proof.seq 必须是非负整数')
    try:
        principal = session_id(proof['session_id'])
    except EvidenceError as exc:
        raise BudgetCliError('proof.session_id 不合法：' + str(exc)) from None
    return {'session_id': proof['session_id'], 'seq': proof['seq']}, principal


def _validate_command(event):
    action = event.get('action')
    if not isinstance(action, str) or action not in BUDGET_ACTIONS:
        raise BudgetCliError('action 必须是 grant、renew 或 revoke')
    for name in _KEY_FIELDS:
        _text(event.get(name), name)
    if event.get('scope') not in BUDGET_SCOPES:
        raise BudgetCliError('scope 必须是 small、large 或 submission')
    expires_at = event.get('expires_at')
    if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
        raise BudgetCliError('expires_at 必须是有限时间戳')
    _units(event.get('total_units'), 'total_units')
    if action != 'revoke' and expires_at <= time.time():
        raise BudgetCliError('授权已过期：expires_at 必须晚于当前时间')


def _grant(args, root, db):
    event = _load_event(root, args.event_file)
    _validate_command(event)
    proof, principal = _load_proof(args.proof_json)
    request_key = _text(args.request_key, 'request-key')
    payload = dict(event)
    payload['proof'] = proof
    # 先只读核真实用户消息；授权没通过前不建表、不登记实验。
    host = DSHHost(workspace=root)
    source = HostBudgetSource(host, {principal})
    verified_principal = source(payload)
    if event['action'] != 'grant':
        # 事件重放由 Accounts 的 event_id / request_key 幂等处理；这里只挡“无 grant 先 revoke”。
        _require_tables(db, ('budget_accounts',))
        if _account(db, event['series_id']) is None:
            raise BudgetCliError('预算系列不存在：' + event['series_id'] + '；revoke/renew 必须先有 grant')
    store = Store(db)
    accounts = Accounts(store, ApprovalEvidence(
        {'dsh-user': SourcePolicy(source, frozenset({verified_principal}))}))
    # 实验登记交给 authorize 在同一 BEGIN IMMEDIATE 事务里做：可信时序/fence 预检通过后
    # 才登记，后续任何拒绝整体回滚；这里不再另开事务，避免自锁和先写后拒的残留。
    return accounts.authorize('dsh-user', payload, request_key, create_experiment=True)


def _reserve(args, root, db):
    series_id = _text(args.series_id, 'series-id')
    exp_id = _text(args.exp_id, 'exp-id')
    request_key = _text(args.request_key, 'request-key')
    _units(args.units, 'units')
    if args.scope not in BUDGET_SCOPES:
        raise BudgetCliError('scope 必须是 small、large 或 submission')
    _require_tables(db, _BASE_TABLES)
    if _account(db, series_id) is None:
        raise BudgetCliError('预算系列账号不存在：' + series_id + '；先 grant')
    if _experiment(db, exp_id) is None:
        raise BudgetCliError('实验不存在：' + exp_id + '；先登记实验或 grant')
    return Accounts(Store(db)).reserve(series_id, exp_id, args.scope, args.units,
                                       request_key, actor='budget-cli')


def _status(args, root, db):
    series_id = _text(args.series_id, 'series-id')
    required = ['budget_accounts']
    if args.reservation_id is not None:
        _text(args.reservation_id, 'reservation-id')
        required.append('budget_reservations')
    _require_tables(db, required)
    account = _account(db, series_id)
    reservation = None
    if args.reservation_id is not None:
        reservation = _reservation(db, args.reservation_id)
    # 只读诊断：不建表、不补行、不改状态。
    return {'ok': True, 'series_id': series_id,
            'account': account, 'reservation': reservation}


def _release(args, root, db):
    reservation_id = _text(args.reservation_id, 'reservation-id')
    request_key = _text(args.request_key, 'request-key')
    _require_tables(db, _BASE_TABLES)
    reservation = _reservation(db, reservation_id)
    if reservation is None:
        raise BudgetCliError('预算预留不存在：' + reservation_id)
    if args.series_id is not None:
        series_id = _text(args.series_id, 'series-id')
        if reservation['series_id'] != series_id:
            raise BudgetCliError('预算预留不属于该预算系列：' + reservation_id)
    # 未终态执行（未 finish 的 wf_runs / 未终态的绑定尝试）由 Accounts._finish 挡；
    # 已终态的预留允许释放，重复 request_key 按 Accounts 幂等回放。
    return Accounts(Store(db)).release(reservation_id, request_key, actor='budget-cli')


def _locator_ok(text, locator):
    match = _LINE_LOCATOR.match(locator.strip())
    if match:
        lines = text.splitlines()
        first = int(match.group(1))
        last = int(match.group(2) or first)
        return 1 <= first <= last <= len(lines)
    return locator in text


def _evidence(root, evidence_file, locator):
    raw = _text(evidence_file, 'evidence-file')
    locator = _text(locator, 'locator')
    candidate = Path(raw.replace('\\', '/'))
    if candidate.is_absolute() or '..' in candidate.parts:
        raise BudgetCliError('成本证据必须是工作区内相对路径，不能绝对路径或含 ..')
    path = (root / candidate).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise BudgetCliError('成本证据文件不在工作区内或不存在：' + raw)
    try:
        data = path.read_bytes()
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise BudgetCliError('成本证据文件必须是 UTF-8 文本：' + str(exc)) from None
    except OSError as exc:
        raise BudgetCliError('成本证据文件不可读：' + str(exc)) from None
    if not _locator_ok(text, locator):
        raise BudgetCliError('成本证据 locator 定位不到：' + raw + ' ' + locator)
    return {'path': path.relative_to(root).as_posix(), 'locator': locator,
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def _settle(args, root, db):
    reservation_id = _text(args.reservation_id, 'reservation-id')
    series_id = _text(args.series_id, 'series-id')
    actual_units = _units(args.actual_units, 'actual-units')
    request_key = None
    if args.request_key is not None:
        request_key = _text(args.request_key, 'request-key')
    _require_tables(db, _BASE_TABLES)
    reservation = _reservation(db, reservation_id)
    if reservation is None:
        raise BudgetCliError('预算预留不存在：' + reservation_id)
    if reservation['status'] != 'reserved':
        raise BudgetCliError('预算预留已释放或结算，不能再次申报成本：' + reservation_id)
    if reservation['series_id'] != series_id:
        raise BudgetCliError('预算预留不属于该预算系列：' + reservation_id)
    if actual_units > reservation['units']:
        raise BudgetCliError('申报成本超过预留上限：' + str(actual_units) + ' > ' + str(reservation['units']))
    if _workflow_active(db, reservation['exp_id']):
        raise BudgetCliError('工作流执行未终态（缺少 wf_finishes）：不能申报结算；先 finish')
    evidence = _evidence(root, args.evidence_file, args.locator)
    # Accounts.settle 只接受调用方给的 actual_units；仓库内没有可信计量适配器，
    # 所以 CLI 不把自述数字写成 settled。这里只核声明与证据，交人工核账边界。
    return {'ok': True, 'settled': False, 'status': 'pending_manual_reconciliation',
            'cost_trust': 'declared-unverified',
            'reason': '真实成本不能由调用方自述结算；本命令只校验声明与证据，不改预算状态',
            'requires': '受信计量适配器或负责人确认的成本证据（显式调用 Accounts.settle）',
            'python_api': 'kernel.autoresearch.runtime.accounts.Accounts.settle',
            'declared': {'reservation_id': reservation_id,
                         'series_id': series_id,
                         'exp_id': reservation['exp_id'],
                         'actual_units': actual_units,
                         'request_key': request_key,
                         'evidence': evidence}}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m kernel.autoresearch.budget_cli',
        description='共享预算：授权核真实用户命令，计划填写成本本身不授额度。')
    sub = parser.add_subparsers(dest='command', required=True)

    grant = sub.add_parser('grant', help='用真实顶层用户消息逐键核 grant/renew/revoke')
    grant.add_argument('--workspace', required=True)
    grant.add_argument('--event-file', required=True,
                       help='完整 grant/renew/revoke JSON命令（工作区内相对路径），须与用户消息逐键相等')
    grant.add_argument('--proof-json', required=True,
                       help='真实顶层用户消息的{session_id,seq}；禁止日志路径')
    grant.add_argument('--request-key', required=True)

    reserve = sub.add_parser('reserve', help='在已授权系列下为实验预留额度')
    reserve.add_argument('--workspace', required=True)
    reserve.add_argument('--series-id', required=True)
    reserve.add_argument('--exp-id', required=True)
    reserve.add_argument('--scope', choices=BUDGET_SCOPES, required=True)
    reserve.add_argument('--units', type=int, required=True)
    reserve.add_argument('--request-key', required=True)

    status = sub.add_parser('status', help='只读查看系列账号（可带预留号）')
    status.add_argument('--workspace', required=True)
    status.add_argument('--series-id', required=True)
    status.add_argument('--reservation-id')

    release = sub.add_parser('release', help='释放预留；未终态执行会挡住')
    release.add_argument('--workspace', required=True)
    release.add_argument('--reservation-id', required=True)
    release.add_argument('--series-id')
    release.add_argument('--request-key', required=True)

    settle = sub.add_parser('settle', help='申报真实成本：无受信计量适配器时只输出 pending 人工核账')
    settle.add_argument('--workspace', required=True)
    settle.add_argument('--reservation-id', required=True)
    settle.add_argument('--series-id', required=True)
    settle.add_argument('--actual-units', type=int, required=True)
    settle.add_argument('--evidence-file', required=True,
                       help='工作区内成本证据文件（相对路径）')
    settle.add_argument('--locator', required=True,
                       help='证据定位：行号 L1 / L1-L9，或原文片段')
    settle.add_argument('--request-key')

    args = parser.parse_args(argv)
    try:
        root = _workspace(args.workspace)
        db = _database(root)
        if args.command == 'grant':
            result, code = _grant(args, root, db), 0
        elif args.command == 'reserve':
            result, code = _reserve(args, root, db), 0
        elif args.command == 'release':
            result, code = _release(args, root, db), 0
        elif args.command == 'settle':
            result, code = _settle(args, root, db), _SETTLE_PENDING
        else:
            result, code = _status(args, root, db), 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (ValueError, OSError, TypeError, KeyError, IndexError, sqlite3.Error) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
