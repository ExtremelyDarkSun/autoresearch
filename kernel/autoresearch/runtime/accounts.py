# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 16:05 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/accounts.py
# CUMULATIVE-DELTA: 2026-11-09 生产证明来源按宿主核验的会话序号与宿主时间做可信时序 fencing：旧事件不得覆盖新状态，跨会话不拿 seq 当全局序，时序不可判一律拒绝；无时序接口的传统来源仍按到达顺序（回归兼容）；旧事件重放只返回历史结果并标注是否已被后续事件取代。authorize 增加 create_experiment（默认 False）：仅真实用户命令入口用 True，在同一 BEGIN IMMEDIATE 事务里、fence 预检之后复用同一连接登记实验，后续任何拒绝整体回滚。此前：Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from dataclasses import dataclass
import hashlib
import hmac
import math
import time
import json
try:
    from kernel.autoresearch.runtime.store import canonical_json, require_text, IdempotencyConflict
except ImportError:
    from kernel.autoresearch.runtime.store import canonical_json, require_text, IdempotencyConflict

class ApprovalError(ValueError):
    pass

class BudgetError(ValueError):
    pass

def units_value(value, name='units'):
    if type(value) is not int or not 0 <= value <= 2 ** 63 - 1:
        raise ValueError(f'{name} must be a non-negative int64 integer')
    return value

def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ApprovalError('expires_at must be a finite timestamp')
    return value

def session_key(value):
    """比较会话标识时补齐 `session-` 前缀：宿主适配器返回规范值，历史记录可能没有前缀。"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return value if value.startswith('session-') else 'session-' + value

@dataclass(frozen=True)
class SourcePolicy:
    verifier: object
    principals: frozenset

class HMACSource:

    def __init__(self, key):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('HMAC key must contain at least 32 bytes')
        self._key = key

    def __call__(self, event):
        signature = event.get('signature')
        unsigned = {k: v for k, v in event.items() if k != 'signature'}
        expected = hmac.new(self._key, canonical_json(unsigned).encode('utf-8'), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            raise ApprovalError('source signature is invalid')
        return require_text(event.get('principal'), 'principal')

class ApprovalEvidence:

    def __init__(self, sources=None):
        self._sources = dict(sources or {})
        for name, policy in self._sources.items():
            require_text(name, 'source')
            if not isinstance(policy, SourcePolicy) or not callable(policy.verifier) or (not policy.principals):
                raise ValueError('each source needs a verifier and allowed principals')

    def verify(self, source, event):
        policy = self._sources.get(source)
        if policy is None:
            raise ApprovalError('approval source is not configured')
        if not isinstance(event, dict):
            raise ApprovalError('approval event must be an object')
        snapshot = json.loads(canonical_json(event))
        try:
            principal = policy.verifier(snapshot)
        except Exception as exc:
            raise ApprovalError('approval source verification failed') from exc
        if canonical_json(snapshot) != canonical_json(event):
            raise ApprovalError('verifier must not mutate the event')
        if not isinstance(principal, str) or principal not in policy.principals:
            raise ApprovalError('approval principal is not allowed')
        return principal

    def ordering(self, source, event, principal):
        """只读取来源给出的可信时序；没有时序接口的传统来源返回 None（按到达顺序）。

        生产证明来源用 `requires_trusted_ordering` 声明“必须有时序”，并由自己的
        只读接口从宿主证据产生 session/seq/host_time。事件里调用方写的任何时间或
        序号都不采信：时序只能来自来源，缺一项就拒绝。
        """
        policy = self._sources.get(source)
        if policy is None:
            raise ApprovalError('approval source is not configured')
        reader = getattr(policy.verifier, 'trusted_ordering', None)
        if reader is None:
            if getattr(policy.verifier, 'requires_trusted_ordering', False):
                raise ApprovalError('审批来源要求可信时序，但没有只读时序接口，不能放行')
            return None
        if not callable(reader):
            raise ApprovalError('审批来源的时序接口不可调用')
        try:
            value = reader(json.loads(canonical_json(event)))
        except Exception as exc:
            raise ApprovalError('审批来源时序不可判') from exc
        if not isinstance(value, dict):
            raise ApprovalError('审批来源时序不可判')
        session, seq, host_time = value.get('session'), value.get('seq'), value.get('host_time')
        if not isinstance(session, str) or not session or session != principal:
            raise ApprovalError('时序元数据与已核验会话不一致，不能放行')
        if type(seq) is not int or seq < 0:
            raise ApprovalError('时序元数据缺少可信序号，不能放行')
        if type(host_time) not in (int, float) or not math.isfinite(host_time):
            raise ApprovalError('时序元数据缺少可信宿主时间，不能放行')
        return {'session': session, 'seq': seq, 'host_time': float(host_time)}

class Accounts:

    def __init__(self, store, evidence=None):
        self.store = store
        self.evidence = evidence or ApprovalEvidence()
        with store.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS budget_accounts(\n                series_id TEXT PRIMARY KEY, scope TEXT NOT NULL, expires_at REAL NOT NULL,\n                total_units INTEGER NOT NULL CHECK(total_units>=0),\n                reserved_units INTEGER NOT NULL DEFAULT 0 CHECK(reserved_units>=0),\n                spent_units INTEGER NOT NULL DEFAULT 0 CHECK(spent_units>=0),\n                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),\n                CHECK(reserved_units<=total_units-spent_units))')
            conn.execute('CREATE TABLE IF NOT EXISTS approval_events(\n                source TEXT NOT NULL,event_id TEXT NOT NULL,payload_json TEXT NOT NULL,\n                principal TEXT NOT NULL,result_json TEXT NOT NULL,created_at REAL NOT NULL,\n                PRIMARY KEY(source,event_id))')
            conn.execute("CREATE TABLE IF NOT EXISTS budget_reservations(\n                id TEXT PRIMARY KEY,series_id TEXT NOT NULL REFERENCES budget_accounts(series_id),\n                exp_id TEXT NOT NULL REFERENCES experiments(id),scope TEXT NOT NULL,\n                units INTEGER NOT NULL CHECK(units>=0), actual_units INTEGER,\n                status TEXT NOT NULL CHECK(status IN ('reserved','settled','released')),\n                created_at REAL NOT NULL,updated_at REAL NOT NULL)")
            conn.execute('CREATE TABLE IF NOT EXISTS account_requests(\n                request_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL,result_json TEXT NOT NULL)')
            # 可信时序高水位只用于判定“旧事件不得覆盖新状态”，不是审计账本；
            # 审计仍由追加式 approval_events 承担，本表允许前移，旧行不动。
            conn.execute('CREATE TABLE IF NOT EXISTS approval_fences(\n                series_id TEXT PRIMARY KEY,session TEXT NOT NULL,seq INTEGER NOT NULL CHECK(seq>=0),\n                host_time REAL NOT NULL,max_host_time REAL NOT NULL,event_id TEXT NOT NULL,\n                action TEXT NOT NULL,updated_at REAL NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS approval_fence_marks(\n                series_id TEXT NOT NULL,session TEXT NOT NULL,\n                last_seq INTEGER NOT NULL CHECK(last_seq>=0),last_time REAL NOT NULL,\n                PRIMARY KEY(series_id,session))')
            for op in ('UPDATE', 'DELETE'):
                conn.execute(f"CREATE TRIGGER IF NOT EXISTS approval_events_no_{op.lower()}\n                    BEFORE {op} ON approval_events BEGIN\n                    SELECT RAISE(ABORT, 'approval events are append-only'); END")

    def _cached(self, conn, key, payload):
        require_text(key, 'request_key')
        old = conn.execute('SELECT * FROM account_requests WHERE request_key=?', (key,)).fetchone()
        if old:
            if old['payload_json'] != canonical_json(payload):
                raise IdempotencyConflict('request key has different payload')
            return json.loads(old['result_json'])
        return None

    def _remember(self, conn, key, payload, result):
        conn.execute('INSERT INTO account_requests VALUES(?,?,?)', (key, canonical_json(payload), canonical_json(result)))
        return result

    def get_account(self, series_id):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (series_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_reservation(self, reservation_id):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT * FROM budget_reservations WHERE id=?', (reservation_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def authorize(self, source, event, request_key, *, create_experiment=False):
        """核验来源并落账一次授权。

        `create_experiment=True` 只给“命令由真实用户消息逐键核验”的入口用：实验不存在时
        在同一 `BEGIN IMMEDIATE` 事务里、先完成 trusted_ordering/fence 预检再登记实验；
        任何后续拒绝整体回滚，不留实验行或事件。默认 False 仍要求实验已存在。
        """
        event = json.loads(canonical_json(event))
        principal = self.evidence.verify(source, event)
        ordering = self.evidence.ordering(source, event, principal)
        for field in ('event_id', 'series_id', 'scope', 'exp_id'):
            require_text(event.get(field), field)
        action = event.get('action', 'grant')
        if action not in ('grant', 'renew', 'revoke'):
            raise ApprovalError('unknown approval action')
        expiry = timestamp(event.get('expires_at'))
        total = units_value(event.get('total_units'), 'total_units')
        registered = False
        payload = ['authorize', source, event]
        with self.store.transaction() as conn:
            require_text(request_key, 'request_key')
            stored = conn.execute('SELECT * FROM account_requests WHERE request_key=?', (request_key,)).fetchone()
            if stored is not None:
                allowed = (canonical_json(payload), canonical_json(payload + [True]))
                if stored['payload_json'] not in allowed:
                    raise IdempotencyConflict('request key has different payload')
                # 请求键回放同样只代表历史结果，现状已变时必须显式标注。
                return self._historical_view(conn, event, json.loads(stored['result_json']))
            old_event = conn.execute('SELECT * FROM approval_events WHERE source=? AND event_id=?', (source, event['event_id'])).fetchone()
            if old_event:
                if old_event['payload_json'] != canonical_json(event):
                    raise IdempotencyConflict('event id has different payload')
                # 已落账的旧事件只回放历史结果，绝不重新应用。
                return self._remember(conn, request_key, payload, self._historical_view(conn, event, json.loads(old_event['result_json'])))
            existing = conn.execute('SELECT 1 FROM experiments WHERE id=?', (event['exp_id'],)).fetchone()
            if existing is None and not create_experiment:
                raise ApprovalError('approval experiment does not exist')
            # 可信时序必须在同一事务里先核：旧 renew 不得在较新 revoke 之后复活预算。
            fence_state = self._require_ordering(conn, event, ordering) if ordering is not None else None
            old = conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (event['series_id'],)).fetchone()
            if action != 'revoke' and expiry <= time.time():
                raise ApprovalError('approval already expired')
            if old is None:
                if action != 'grant':
                    raise ApprovalError('series account does not exist')
            else:
                if action == 'grant':
                    raise ApprovalError('existing series requires explicit renew or revoke')
                if old['scope'] != event['scope']:
                    raise ApprovalError('series scope cannot change')
                if action == 'renew' and total < old['spent_units'] + old['reserved_units']:
                    raise BudgetError('renewed cap is below committed usage')
            if existing is None:
                # 与授权、fence 同一事务：后续任何拒绝都会回滚这条实验登记。
                self.store.ensure_experiment(event['exp_id'], _connection=conn)
                registered = True
            if old is None:
                conn.execute('INSERT INTO budget_accounts(series_id,scope,expires_at,total_units)\n                    VALUES(?,?,?,?)', (event['series_id'], event['scope'], expiry, total))
            elif action == 'renew':
                conn.execute('UPDATE budget_accounts SET total_units=?,expires_at=?,active=1\n                    WHERE series_id=?', (total, expiry, event['series_id']))
            else:
                conn.execute('UPDATE budget_accounts SET active=0 WHERE series_id=?', (event['series_id'],))
            if ordering is not None:
                self._record_ordering(conn, event, ordering, fence_state)
            result = dict(conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (event['series_id'],)).fetchone())
            conn.execute('INSERT INTO approval_events VALUES(?,?,?,?,?,?)', (source, event['event_id'], canonical_json(event), principal, canonical_json(result), time.time()))
            self.store.emit(conn, event['exp_id'], 'approval_' + action, principal, {'source': source, 'event_id': event['event_id'], 'account': result})
            if registered:
                # 记下“本请求顺带建了实验”，同一请求键重放不会被误判成 payload 冲突。
                payload = ['authorize', source, event, True]
            return self._remember(conn, request_key, payload, result)

    def _historical_view(self, conn, event, result):
        """历史快照只回放历史：现状已被后续事件改写时显式标注，不能当成当前状态。"""
        row = conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (event['series_id'],)).fetchone()
        if isinstance(result, dict) and row is not None:
            current = dict(row)
            if any(result.get(key) != current.get(key)
                   for key in ('active', 'total_units', 'expires_at', 'scope')):
                return dict(result, replayed=True, superseded=True, current=current)
        return result

    def _require_ordering(self, conn, event, ordering):
        """用来源核验过的 session/seq/host_time 判定新旧；不可判就拒绝，不做到达顺序假设。

        同一会话内 seq 才有序；跨会话 seq 没有全局含义，只能用可信宿主时间比较。
        expires_at 等调用方自报字段不参与排序。
        """
        series_id = event['series_id']
        session, seq, host_time = ordering['session'], ordering['seq'], ordering['host_time']
        row = conn.execute('SELECT * FROM approval_fences WHERE series_id=?', (series_id,)).fetchone()
        fence = dict(row) if row is not None else None
        marks = {item['session']: dict(item) for item in conn.execute(
            'SELECT session,last_seq FROM approval_fence_marks WHERE series_id=?', (series_id,))}
        if fence is None and not marks:
            # 升级前的历史授权事件没有 fence 行：只能从已存 proof 恢复，恢复不了就判未知。
            fence, marks = self._recover_ordering(conn, series_id)
        if fence is not None and fence.get('unknown'):
            raise ApprovalError('该系列的历史授权事件缺少可信时序，先后不可判；拒绝放行，需重新 grant 新系列')
        floor = marks[session]['last_seq'] if session in marks else (
            fence['seq'] if fence is not None and fence['session'] == session else None)
        if floor is not None and seq <= floor:
            raise ApprovalError('审批事件的序号不晚于同会话已生效事件，旧事件不能覆盖新状态')
        if fence is not None and fence['session'] != session:
            baseline = fence['max_host_time']
            if baseline is None or not host_time > baseline:
                raise ApprovalError('跨会话审批事件没有更晚的可信宿主时间，先后不可判；拒绝旧事件覆盖')
        previous = fence['max_host_time'] if fence is not None else None
        return {'max_host_time': host_time if previous is None else max(previous, host_time)}

    def _recover_ordering(self, conn, series_id):
        """从已落账事件的 proof 恢复升级前基线；多会话或没有 proof 都算未知。"""
        sessions, prior = {}, False
        for row in conn.execute('SELECT payload_json FROM approval_events'):
            try:
                payload = json.loads(row['payload_json'])
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get('series_id') != series_id:
                continue
            prior = True
            proof = payload.get('proof')
            if not isinstance(proof, dict):
                continue
            session, seq = session_key(proof.get('session_id')), proof.get('seq')
            if session is None or type(seq) is not int or seq < 0:
                continue
            if seq > sessions.get(session, -1):
                sessions[session] = seq
        if not prior:
            return None, {}
        if len(sessions) != 1:
            return {'session': None, 'seq': None, 'max_host_time': None, 'unknown': True}, {}
        session, seq = next(iter(sessions.items()))
        return ({'session': session, 'seq': seq, 'max_host_time': None},
                {session: {'session': session, 'last_seq': seq}})

    def _record_ordering(self, conn, event, ordering, fence_state):
        """把本次已生效事件写成新的时序高水位；只前移，不回退。"""
        series_id = event['series_id']
        session, seq, host_time = ordering['session'], ordering['seq'], ordering['host_time']
        mark = conn.execute('SELECT last_seq,last_time FROM approval_fence_marks\n            WHERE series_id=? AND session=?', (series_id, session)).fetchone()
        if mark is None:
            conn.execute('INSERT INTO approval_fence_marks(series_id,session,last_seq,last_time)\n                VALUES(?,?,?,?)', (series_id, session, seq, host_time))
        else:
            conn.execute('UPDATE approval_fence_marks SET last_seq=?,last_time=?\n                WHERE series_id=? AND session=?',
                         (seq, max(mark['last_time'], host_time), series_id, session))
        conn.execute('INSERT INTO approval_fences\n            (series_id,session,seq,host_time,max_host_time,event_id,action,updated_at)\n            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(series_id) DO UPDATE SET\n            session=excluded.session,seq=excluded.seq,host_time=excluded.host_time,\n            max_host_time=excluded.max_host_time,event_id=excluded.event_id,\n            action=excluded.action,updated_at=excluded.updated_at',
                     (series_id, session, seq, host_time, fence_state['max_host_time'],
                      event['event_id'], event.get('action', 'grant'), time.time()))

    def reserve(self, series_id, exp_id, scope, units, request_key, actor='runtime'):
        for value, name in ((series_id, 'series_id'), (exp_id, 'exp_id'), (scope, 'scope'), (actor, 'actor')):
            require_text(value, name)
        units_value(units)
        payload = ['reserve', series_id, exp_id, scope, units, actor]
        with self.store.transaction() as conn:
            cached = self._cached(conn, request_key, payload)
            if cached is not None:
                return cached
            account = conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (series_id,)).fetchone()
            if account is None or not account['active'] or account['expires_at'] <= time.time():
                raise ApprovalError('series account is absent, revoked or expired')
            if account['scope'] != scope:
                raise ApprovalError('requested scope is not authorized')
            if units > account['total_units'] - account['spent_units'] - account['reserved_units']:
                raise BudgetError('insufficient shared budget')
            now = time.time()
            conn.execute("INSERT INTO budget_reservations\n                (id,series_id,exp_id,scope,units,status,created_at,updated_at)\n                VALUES(?,?,?,?,?,'reserved',?,?)", (request_key, series_id, exp_id, scope, units, now, now))
            conn.execute('UPDATE budget_accounts SET reserved_units=reserved_units+? WHERE series_id=?', (units, series_id))
            result = dict(conn.execute('SELECT * FROM budget_reservations WHERE id=?', (request_key,)).fetchone())
            self.store.emit(conn, exp_id, 'budget_reserved', actor, result)
            return self._remember(conn, request_key, payload, result)

    def settle(self, reservation_id, actual_units, request_key, actor='runtime'):
        units_value(actual_units, 'actual_units')
        return self._finish(reservation_id, actual_units, request_key, actor, 'settled')

    def release(self, reservation_id, request_key, actor='runtime'):
        return self._finish(reservation_id, 0, request_key, actor, 'released')

    def _finish(self, reservation_id, actual_units, request_key, actor, status):
        require_text(reservation_id, 'reservation_id')
        require_text(actor, 'actor')
        payload = [status, reservation_id, actual_units, actor]
        with self.store.transaction() as conn:
            cached = self._cached(conn, request_key, payload)
            if cached is not None:
                return cached
            reservation = conn.execute('SELECT * FROM budget_reservations WHERE id=?', (reservation_id,)).fetchone()
            if reservation is None or reservation['status'] != 'reserved':
                raise BudgetError('reservation is absent or already finalized')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wf_runs'").fetchone():
                active = conn.execute('SELECT r.run_id FROM wf_runs r WHERE r.plan_id=? AND NOT EXISTS (SELECT 1 FROM wf_finishes f WHERE f.run_id=r.run_id)', (reservation['exp_id'],)).fetchone()
                if active:
                    raise BudgetError('workflow execution must be definitively terminal before budget finalization')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reservation_bindings'").fetchone():
                binding = conn.execute('SELECT request_key FROM reservation_bindings WHERE reservation_id=?', (reservation_id,)).fetchone()
                if binding:
                    attempt = conn.execute('SELECT a.state,j.remote_state FROM exec_attempts a\n                        JOIN exec_jobs j ON j.attempt_id=a.id WHERE a.request_key=?', (binding['request_key'],)).fetchone()
                    if attempt is None or attempt['state'] not in ('completed', 'failed', 'cancelled', 'partial') or attempt['remote_state'] not in ('completed', 'failed', 'cancelled'):
                        raise BudgetError('bound execution must be definitively terminal before budget finalization')
            if actual_units > reservation['units']:
                raise BudgetError('actual units exceed reservation; reserve the upper bound first')
            conn.execute('UPDATE budget_accounts SET reserved_units=reserved_units-?,\n                spent_units=spent_units+? WHERE series_id=?', (reservation['units'], actual_units, reservation['series_id']))
            conn.execute('UPDATE budget_reservations SET status=?,actual_units=?,updated_at=? WHERE id=?', (status, actual_units, time.time(), reservation_id))
            result = dict(conn.execute('SELECT * FROM budget_reservations WHERE id=?', (reservation_id,)).fetchone())
            self.store.emit(conn, reservation['exp_id'], 'budget_' + status, actor, result)
            return self._remember(conn, request_key, payload, result)
