# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09 check_budget供统一视图复用原预算判据；check继续另核真实专项批准
# PURPOSE: Connect shared runtime budget reservations and decision approval to real workflow launch.
# ORIGIN: existing runtime Accounts and full behavior expectations
# CUMULATIVE-DELTA: 2026-11-09 抽出check_budget供统一检查复用，原check继续组合真实决策；支持同一now复现，非有限时钟拒绝。此前只读连接零副作用：Admission.check 不再自己拼 `?mode=ro`，委托 `runtime.store.ReadonlyStore`（WAL 库按 sidecar 实况选 mode=ro / immutable=1，读不创建 -shm/-wal；WAL 歧义态 StoreError 按「共享预算不可判」fail-closed）。此前：2026-11-09 可信时序收紧：HostBudgetSource 增加只读 trusted_ordering，返回宿主日志核验过的 session/seq/host_time，供 Accounts 在同一事务里判定新旧；跨会话不拿 seq 当全局序，时序不可判 fail-closed。此前：2026-11-09 预算准入收紧：HostBudgetSource 逐键核真实用户命令，并核 verify 返回的 source/proof/session/seq/rpc/created_at；Admission.check 保持只读、缺表/坏计划 fail-closed。此前：Read-only admission; no caller self-approval or budget fabrication.
from __future__ import annotations
import hashlib
import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from .host_evidence import EvidenceError, session_id

BUDGET_SCOPES = ('small', 'large', 'submission')
BUDGET_ACTIONS = ('grant', 'renew', 'revoke')
BUDGET_EVENT_FIELDS = frozenset({'action', 'event_id', 'series_id', 'scope', 'exp_id',
                                 'expires_at', 'total_units', 'proof'})
BUDGET_COMMAND_FIELDS = BUDGET_EVENT_FIELDS - {'proof'}
_MAX_UNITS = 2 ** 63 - 1


def _command_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' 必须是非空文字')
    return value


def _command_units(value, name):
    if type(value) is not int or not 0 <= value <= _MAX_UNITS:
        raise ValueError(name + ' 必须是非负 int64 整数')
    return value


def _command_expiry(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('expires_at 必须是有限时间戳')
    return value


class Admission:
    def __init__(self, workspace, *, store=None, decisions=None, clock=time.time):
        self.workspace = Path(workspace).resolve()
        self.db = Path(store.db_path) if store is not None else self.workspace / '.autoresearch/runtime.sqlite3'
        self.decisions = decisions
        self.clock = clock

    def check_budget(self, plan, owner_session, exp_id, *, now=None):
        """只读核对冻结计划所指账号、预留、范围和金额；不核决策、不签发额度。"""
        problems = []
        result = {'ok': False, 'problems': problems, 'budget': None, 'decision': None}
        if not isinstance(plan, dict) or not isinstance(owner_session, str) or not owner_session.strip():
            problems.append('缺计划或负责人来源')
            return result
        if not isinstance(exp_id, str) or not exp_id.strip():
            problems.append('缺实验标识')
            return result
        auth = plan.get('authorization')
        if not isinstance(auth, dict) or auth.get('scope') not in BUDGET_SCOPES:
            problems.append('authorization必须显式声明small、large或submission作用域')
            return result
        for key in ('series_id', 'reservation_id'):
            if not isinstance(auth.get(key), str) or not auth[key].strip():
                problems.append('authorization缺' + key)
        budget = plan.get('budget')
        units = None
        if not isinstance(budget, dict):
            problems.append('计划必须显式声明预算对象')
        else:
            units = budget.get('max_cost_units')
            if type(units) is not int or units < 0:
                problems.append('计划缺少非负整数max_cost_units')
        if problems:
            return result
        if not self.db.is_file():
            problems.append('共享预算数据库不存在')
            return result
        try:
            from .runtime.store import ReadonlyStore
            # 只读连接委托 ReadonlyStore：WAL 库按文件头与 sidecar 实况选 mode=ro / immutable=1，
            # 读不创建 -shm / -wal（裸 mode=ro 会）；WAL 歧义态抛 StoreError（下面按不可判处理）。
            with closing(ReadonlyStore(self.db).connect()) as conn:
                reservation = conn.execute('SELECT * FROM budget_reservations WHERE id=?', (auth['reservation_id'],)).fetchone()
                account = conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (auth['series_id'],)).fetchone()
                result['budget'] = {'reservation': dict(reservation) if reservation else None,
                                    'account': dict(account) if account else None}
                if not reservation or not account:
                    problems.append('预算账号或预留不存在')
                else:
                    moment = self.clock() if now is None else now
                    if type(moment) not in (int, float) or not math.isfinite(moment) or moment <= 0:
                        raise ValueError('预算时钟不可判，必须是有限正时间')
                    expires = account['expires_at']
                    if type(expires) not in (int, float) or not math.isfinite(expires) or expires <= 0:
                        raise ValueError('预算到期时间不可判')
                    if not account['active'] or expires <= moment:
                        problems.append('预算已撤回或过期')
                    if account['scope'] != auth['scope'] or reservation['scope'] != auth['scope']:
                        problems.append('执行范围超出已授权预算作用域')
                    if reservation['series_id'] != auth['series_id'] or reservation['exp_id'] != exp_id:
                        problems.append('预算预留属于另一实验或预算系列')
                    if reservation['status'] != 'reserved':
                        problems.append('预算预留已释放或结算')
                    if reservation['units'] < units:
                        problems.append('预算预留未覆盖事前声明的最大成本')
        except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            problems.append('共享预算不可判：' + str(exc))
        result['ok'] = not problems
        return result

    def check(self, plan, owner_session, exp_id):
        """只读执行准入：复用预算判据，再核真实专项批准。"""
        result = self.check_budget(plan, owner_session, exp_id)
        problems = result['problems']
        auth = plan.get('authorization') if isinstance(plan, dict) else None
        if not isinstance(auth, dict):
            return result
        if auth.get('scope') in ('large', 'submission'):
            decision_id = auth.get('decision_id')
            if not isinstance(decision_id, str) or not decision_id.strip():
                problems.append('扩大测试或线上提交缺少同实验的已批准决策')
            elif self.decisions is None:
                problems.append('审批核验器未接线，不能启动扩大测试或提交')
            else:
                try:
                    # 调用方不能改 decisions 接口；默认 record=False（只读），Workflow 事务里可调。
                    check = self.decisions.check_authorization(plan=plan, owner_session=owner_session, exp_id=exp_id)
                    result['decision'] = check
                    if not isinstance(check, dict) or check.get('ok') is not True:
                        problems.append('审批链未明确批准当前实验和版本')
                except Exception as exc:
                    problems.append('审批证据不可判：' + str(exc))
        result['ok'] = not problems
        return result


class HostBudgetSource:
    """Accounts source adapter. User messages must match the exact budget command.

    Instantiate from trusted deployment code; no command can select its own log root
    or supply its own `expected` object. The returned principal is the proof session,
    and every provenance field of the host verdict is checked against that proof.

    `trusted_ordering` is the read-only timeline interface: it returns only the
    session, seq and host time that the host verifier just accepted for this exact
    event. `Accounts` uses it for fencing; caller-supplied time/seq fields are never
    trusted (the event field set is exact, so they cannot even be added).
    """
    requires_trusted_ordering = True

    def __init__(self, host, allowed_principals, *, clock=time.time):
        self.host = host
        if isinstance(allowed_principals, str):
            raise ValueError('allowed_principals 必须是会话标识集合，不能是单个字符串')
        principals = set()
        for value in allowed_principals:
            try:
                principals.add(session_id(value))
            except EvidenceError as exc:
                raise ValueError('allowed_principals 含非法会话标识：' + str(exc)) from None
        if not principals:
            raise ValueError('预算授权必须配置至少一个可信会话')
        if not callable(clock):
            raise ValueError('clock 必须可调用')
        self.allowed_principals = frozenset(principals)
        self.clock = clock
        self._last = None

    def __call__(self, event):
        return self._verified(event)[0]

    def trusted_ordering(self, event):
        """只读时序元数据：只由宿主核验结果产生，绝不采信调用方新增字段。"""
        return self._verified(event)[1]

    def _verified(self, event):
        if not isinstance(event, dict):
            raise ValueError('预算事件必须是对象')
        if set(event) != BUDGET_EVENT_FIELDS:
            raise ValueError('预算命令字段不完整或有未知项')
        action = event['action']
        if action not in BUDGET_ACTIONS:
            raise ValueError('预算命令action必须是grant、renew或revoke')
        for key in ('event_id', 'series_id', 'exp_id'):
            _command_text(event[key], key)
        if event['scope'] not in BUDGET_SCOPES:
            raise ValueError('预算范围必须显式声明')
        _command_expiry(event['expires_at'])
        _command_units(event['total_units'], 'total_units')
        proof = event['proof']
        if not isinstance(proof, dict) or set(proof) != {'session_id', 'seq'}:
            raise ValueError('proof只能是{session_id,seq}两个键，不接受证据文件路径')
        if type(proof['seq']) is not int or proof['seq'] < 0:
            raise ValueError('proof.seq 必须是非负整数')
        try:
            expected_session = session_id(proof['session_id'])
        except EvidenceError as exc:
            raise ValueError('proof.session_id 不合法：' + str(exc)) from None
        # 同一进程内 verify 紧接着 trusted_ordering 会复用这次核验；
        # 指纹不同就重新核验宿主日志，缓存只省重复读取，不是信任来源。
        fingerprint = json.dumps(event, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if self._last is not None and self._last[0] == fingerprint:
            return self._last[1], dict(self._last[2])
        # expected 只能由本适配器从事件本身构造；调用者没有自选入口。
        expected = {key: value for key, value in event.items() if key != 'proof'}
        verified = self.host.verify('user_command', {'proof': {'session_id': proof['session_id'],
                                                               'seq': proof['seq']},
                                                     'expected': expected})
        if not isinstance(verified, dict) or verified.get('verified') is not True:
            raise ValueError('预算授权没有真实用户证据')
        if verified.get('kind') != 'user_command':
            raise ValueError('预算授权证据类型不是真实用户命令')
        source = verified.get('source')
        if not isinstance(source, str) or not source.strip() or source != getattr(self.host, 'evidence_source', None):
            raise ValueError('预算授权来源与宿主适配器不符')
        session = verified.get('session_id')
        if not isinstance(session, str) or session not in self.allowed_principals:
            raise ValueError('预算授权来源不属于部署信任范围')
        if session != expected_session:
            raise ValueError('核验返回的会话与proof不一致')
        if type(verified.get('seq')) is not int or verified.get('seq') != proof['seq']:
            raise ValueError('核验返回的序号与proof不一致')
        rpc_id = verified.get('rpc_id')
        if not isinstance(rpc_id, str) or not rpc_id.strip():
            raise ValueError('真实用户消息缺少客户端请求标识')
        command = verified.get('command')
        if command != expected:
            raise ValueError('核验返回的命令与事件不逐键相等')
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False,
                                           allow_nan=False).encode('utf-8')).hexdigest()
        if verified.get('command_sha256') != digest:
            raise ValueError('核验返回的命令摘要与事件不相等')
        created_at = verified.get('created_at')
        if type(created_at) not in (int, float) or not math.isfinite(created_at):
            raise ValueError('用户消息缺少可判定的时间')
        if created_at > self.clock():
            # 只拒绝“未来”时间；不设最大年龄等无据时限。
            raise ValueError('用户消息时间在将来，不能作为已发生的授权证据')
        ordering = {'session': expected_session, 'seq': proof['seq'],
                    'host_time': float(created_at), 'rpc_id': rpc_id, 'source': source}
        self._last = (fingerprint, expected_session, ordering)
        return expected_session, ordering
