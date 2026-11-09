# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/accounts.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
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

class Accounts:

    def __init__(self, store, evidence=None):
        self.store = store
        self.evidence = evidence or ApprovalEvidence()
        with store.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS budget_accounts(\n                series_id TEXT PRIMARY KEY, scope TEXT NOT NULL, expires_at REAL NOT NULL,\n                total_units INTEGER NOT NULL CHECK(total_units>=0),\n                reserved_units INTEGER NOT NULL DEFAULT 0 CHECK(reserved_units>=0),\n                spent_units INTEGER NOT NULL DEFAULT 0 CHECK(spent_units>=0),\n                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),\n                CHECK(reserved_units<=total_units-spent_units))')
            conn.execute('CREATE TABLE IF NOT EXISTS approval_events(\n                source TEXT NOT NULL,event_id TEXT NOT NULL,payload_json TEXT NOT NULL,\n                principal TEXT NOT NULL,result_json TEXT NOT NULL,created_at REAL NOT NULL,\n                PRIMARY KEY(source,event_id))')
            conn.execute("CREATE TABLE IF NOT EXISTS budget_reservations(\n                id TEXT PRIMARY KEY,series_id TEXT NOT NULL REFERENCES budget_accounts(series_id),\n                exp_id TEXT NOT NULL REFERENCES experiments(id),scope TEXT NOT NULL,\n                units INTEGER NOT NULL CHECK(units>=0), actual_units INTEGER,\n                status TEXT NOT NULL CHECK(status IN ('reserved','settled','released')),\n                created_at REAL NOT NULL,updated_at REAL NOT NULL)")
            conn.execute('CREATE TABLE IF NOT EXISTS account_requests(\n                request_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL,result_json TEXT NOT NULL)')
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

    def authorize(self, source, event, request_key):
        event = json.loads(canonical_json(event))
        principal = self.evidence.verify(source, event)
        for field in ('event_id', 'series_id', 'scope', 'exp_id'):
            require_text(event.get(field), field)
        action = event.get('action', 'grant')
        if action not in ('grant', 'renew', 'revoke'):
            raise ApprovalError('unknown approval action')
        expiry = timestamp(event.get('expires_at'))
        total = units_value(event.get('total_units'), 'total_units')
        payload = ['authorize', source, event]
        with self.store.transaction() as conn:
            cached = self._cached(conn, request_key, payload)
            if cached is not None:
                return cached
            old_event = conn.execute('SELECT * FROM approval_events WHERE source=? AND event_id=?', (source, event['event_id'])).fetchone()
            if old_event:
                if old_event['payload_json'] != canonical_json(event):
                    raise IdempotencyConflict('event id has different payload')
                return self._remember(conn, request_key, payload, json.loads(old_event['result_json']))
            if conn.execute('SELECT 1 FROM experiments WHERE id=?', (event['exp_id'],)).fetchone() is None:
                raise ApprovalError('approval experiment does not exist')
            old = conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (event['series_id'],)).fetchone()
            if action != 'revoke' and expiry <= time.time():
                raise ApprovalError('approval already expired')
            if old is None:
                if action != 'grant':
                    raise ApprovalError('series account does not exist')
                conn.execute('INSERT INTO budget_accounts(series_id,scope,expires_at,total_units)\n                    VALUES(?,?,?,?)', (event['series_id'], event['scope'], expiry, total))
            else:
                if action == 'grant':
                    raise ApprovalError('existing series requires explicit renew or revoke')
                if old['scope'] != event['scope']:
                    raise ApprovalError('series scope cannot change')
                if action == 'renew':
                    if total < old['spent_units'] + old['reserved_units']:
                        raise BudgetError('renewed cap is below committed usage')
                    conn.execute('UPDATE budget_accounts SET total_units=?,expires_at=?,active=1\n                        WHERE series_id=?', (total, expiry, event['series_id']))
                else:
                    conn.execute('UPDATE budget_accounts SET active=0 WHERE series_id=?', (event['series_id'],))
            result = dict(conn.execute('SELECT * FROM budget_accounts WHERE series_id=?', (event['series_id'],)).fetchone())
            conn.execute('INSERT INTO approval_events VALUES(?,?,?,?,?,?)', (source, event['event_id'], canonical_json(event), principal, canonical_json(result), time.time()))
            self.store.emit(conn, event['exp_id'], 'approval_' + action, principal, {'source': source, 'event_id': event['event_id'], 'account': result})
            return self._remember(conn, request_key, payload, result)

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
