# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/delegation.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import time
import uuid
try:
    from kernel.autoresearch.runtime.store import canonical_json, require_text, IdempotencyConflict
except ImportError:
    from kernel.autoresearch.runtime.store import canonical_json, require_text, IdempotencyConflict

class DelegationError(ValueError):
    pass

class HostVerificationError(DelegationError):
    pass

class FencingError(DelegationError):
    pass

@dataclass(frozen=True)
class HostSession:
    session_id: str
    parent_session: str | None
    depth: int

@dataclass(frozen=True)
class VerifiedHostEvent:
    action: str
    principal: str
    event_id: str
    payload_hash: str
    sessions: tuple[HostSession, ...]

def payload_hash(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()
_OUTCOMES = frozenset(('finished', 'failed', 'stopped', 'declined', 'exhausted', 'abnormal'))

def _outcome(value):
    require_text(value, 'outcome')
    if value not in _OUTCOMES:
        raise DelegationError('outcome must be finished, failed, stopped, declined, exhausted or abnormal')
    return value

def _hash(value, name):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise DelegationError(f'{name} must be a lowercase SHA256')
    return value

def _generation(value):
    if type(value) is not int or value < 1:
        raise DelegationError('generation must be a positive integer')
    return value

class Delegation:

    def __init__(self, store, *, host_verifier=None):
        if host_verifier is not None and (not callable(getattr(host_verifier, 'verify', None))):
            raise HostVerificationError('host_verifier must provide verify; boolean callbacks are forbidden')
        self.store = store
        self.host_verifier = host_verifier
        statements = ('CREATE TABLE IF NOT EXISTS delegation_owners(\n                exp_id TEXT PRIMARY KEY REFERENCES experiments(id), root_session TEXT NOT NULL,\n                orchestrator_session TEXT NOT NULL, parent_session TEXT NOT NULL,\n                depth INTEGER NOT NULL CHECK(depth=1), rolepack_hash TEXT NOT NULL,\n                generation INTEGER NOT NULL CHECK(generation>0))', "CREATE TABLE IF NOT EXISTS delegation_tasks(\n                task_id TEXT PRIMARY KEY, exp_id TEXT NOT NULL REFERENCES experiments(id),\n                generation INTEGER NOT NULL, orchestrator_session TEXT NOT NULL,\n                role TEXT NOT NULL CHECK(role IN ('planner','runner','scribe')),\n                input_hash TEXT NOT NULL, expected_artifact_hash TEXT NOT NULL,\n                status TEXT NOT NULL CHECK(status IN ('requested','dispatched','settled','accepted','rejected')),\n                child_session TEXT UNIQUE, parent_session TEXT, depth INTEGER,\n                result_hash TEXT, outcome TEXT, review_reason TEXT)", 'CREATE TABLE IF NOT EXISTS delegation_requests(\n                request_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT NOT NULL)', 'CREATE TABLE IF NOT EXISTS delegation_evidence(\n                event_id TEXT PRIMARY KEY, exp_id TEXT NOT NULL REFERENCES experiments(id),\n                action TEXT NOT NULL, principal TEXT NOT NULL, payload_json TEXT NOT NULL,\n                host_json TEXT NOT NULL, created_at REAL NOT NULL)')
        with self.store.transaction() as conn:
            for sql in statements:
                conn.execute(sql)
            for table in ('delegation_requests', 'delegation_evidence'):
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}\n                        BEFORE {operation} ON {table} BEGIN\n                        SELECT RAISE(ABORT, 'delegation evidence is append-only'); END")

    def _verify(self, action, principal, evidence, expected, owner, child=None):
        require_text(principal, 'principal')
        if self.host_verifier is None:
            raise HostVerificationError('trusted host verifier unavailable; operation denied')
        event = self.host_verifier.verify(action, principal, evidence, json.loads(canonical_json(expected)))
        if type(event) is not VerifiedHostEvent or event.action != action or event.principal != principal or (event.payload_hash != payload_hash(expected)):
            raise HostVerificationError('host event does not authenticate principal and complete payload')
        require_text(event.event_id, 'host event_id')
        if not isinstance(event.sessions, tuple) or any((type(s) is not HostSession for s in event.sessions)):
            raise HostVerificationError('host session metadata missing')
        sessions = {s.session_id: s for s in event.sessions}
        if len(sessions) != len(event.sessions):
            raise HostVerificationError('duplicate host session metadata')
        root = sessions.get(owner['root_session'])
        orch = sessions.get(owner['orchestrator_session'])
        if root is None or root.parent_session is not None or type(root.depth) is not int or (root.depth != 0) or (orch is None) or (orch.parent_session != root.session_id) or (type(orch.depth) is not int) or (orch.depth != 1):
            raise HostVerificationError('root metadata or exact depth1 parent chain rejected')
        if child is not None:
            sub = sessions.get(child)
            if sub is None or sub.parent_session != orch.session_id or type(sub.depth) is not int or (sub.depth != 2):
                raise HostVerificationError('child must have exact orchestrator parent and depth2')
        return event

    def _owner(self, conn, exp_id, generation, principal, *, root=False):
        row = conn.execute('SELECT * FROM delegation_owners WHERE exp_id=?', (exp_id,)).fetchone()
        if row is None or row['generation'] != generation:
            raise FencingError('unregistered or stale generation')
        if principal != row['root_session' if root else 'orchestrator_session']:
            raise FencingError('principal does not own this operation')
        return dict(row)

    def _task(self, conn, task_id):
        row = conn.execute('SELECT * FROM delegation_tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            raise DelegationError('unknown task')
        return dict(row)

    def _old(self, conn, key, payload):
        require_text(key, 'request_key')
        row = conn.execute('SELECT * FROM delegation_requests WHERE request_key=?', (key,)).fetchone()
        if row:
            if row['payload_json'] != canonical_json(payload):
                raise IdempotencyConflict('request key has different delegation payload')
            return json.loads(row['result_json'])
        return None

    def _record(self, conn, key, payload, result, event):
        if conn.execute('SELECT 1 FROM delegation_evidence WHERE event_id=?', (event.event_id,)).fetchone():
            raise HostVerificationError('host event already consumed')
        conn.execute('INSERT INTO delegation_evidence VALUES(?,?,?,?,?,?,?)', (event.event_id, payload['exp_id'], payload['action'], event.principal, canonical_json(payload), canonical_json(asdict(event)), time.time()))
        self.store.emit(conn, payload['exp_id'], 'delegation.' + payload['action'], event.principal, {'request_key': key, 'host_event_id': event.event_id, 'binding': payload, 'result': result})
        conn.execute('INSERT INTO delegation_requests VALUES(?,?,?)', (key, canonical_json(payload), canonical_json(result)))
        return result

    def register(self, exp_id, root_session, orchestrator_session, rolepack_hash, generation, *, principal, evidence, request_key):
        for name, value in (('exp_id', exp_id), ('root_session', root_session), ('orchestrator_session', orchestrator_session)):
            require_text(value, name)
        _hash(rolepack_hash, 'rolepack_hash')
        _generation(generation)
        if principal != root_session or root_session == orchestrator_session:
            raise FencingError('only authenticated root can register its distinct direct child')
        owner = dict(exp_id=exp_id, root_session=root_session, orchestrator_session=orchestrator_session, parent_session=root_session, depth=1, rolepack_hash=rolepack_hash, generation=generation)
        payload = dict(action='register', **owner, principal=principal)
        with self.store.transaction() as conn:
            old_owner = conn.execute('SELECT * FROM delegation_owners WHERE exp_id=?', (exp_id,)).fetchone()
            if old_owner and (old_owner['root_session'] != root_session or generation < old_owner['generation']):
                raise FencingError('root changed or generation stale')
            event = self._verify('register', principal, evidence, payload, owner)
            old = self._old(conn, request_key, payload)
            if old is not None:
                return old
            if old_owner and generation <= old_owner['generation']:
                raise FencingError('new registration must increase generation')
            conn.execute('INSERT INTO delegation_owners VALUES(?,?,?,?,?,?,?) ON CONFLICT(exp_id)\n                DO UPDATE SET orchestrator_session=excluded.orchestrator_session,\n                parent_session=excluded.parent_session,depth=excluded.depth,\n                rolepack_hash=excluded.rolepack_hash,generation=excluded.generation', tuple(owner.values()))
            return self._record(conn, request_key, payload, owner, event)

    def request_task(self, exp_id, generation, role, input_hash, expected_artifact_hash, *, principal, evidence, request_key, after_task_id=None):
        _generation(generation)
        if role not in ('planner', 'runner', 'scribe'):
            raise DelegationError('role must be planner, runner or scribe')
        _hash(input_hash, 'input_hash')
        _hash(expected_artifact_hash, 'expected_artifact_hash')
        with self.store.transaction() as conn:
            owner = self._owner(conn, exp_id, generation, principal)
            if after_task_id is not None:
                predecessor = self._task(conn, after_task_id)
                if predecessor['exp_id'] != exp_id or predecessor['generation'] != generation or predecessor['orchestrator_session'] != principal or (predecessor['status'] != 'accepted'):
                    raise DelegationError('dependent progression requires current orchestrator acceptance')
            payload = dict(action='request', exp_id=exp_id, generation=generation, role=role, input_hash=input_hash, expected_artifact_hash=expected_artifact_hash, orchestrator_session=principal, principal=principal, after_task_id=after_task_id)
            event = self._verify('request', principal, evidence, payload, owner)
            old = self._old(conn, request_key, payload)
            if old is not None:
                return old
            task_id = uuid.uuid4().hex
            conn.execute("INSERT INTO delegation_tasks(task_id,exp_id,generation,orchestrator_session,\n                role,input_hash,expected_artifact_hash,status) VALUES(?,?,?,?,?,?,?,'requested')", (task_id, exp_id, generation, principal, role, input_hash, expected_artifact_hash))
            return self._record(conn, request_key, payload, self._task(conn, task_id), event)

    def dispatch(self, task_id, child_session, *, principal, evidence, request_key):
        require_text(child_session, 'child_session')
        return self._change('dispatch', task_id, principal, evidence, request_key, dict(child_session=child_session))

    def settle(self, task_id, result_hash, outcome, *, principal, evidence, request_key):
        _hash(result_hash, 'result_hash')
        _outcome(outcome)
        return self._change('settle', task_id, principal, evidence, request_key, dict(result_hash=result_hash, outcome=outcome))

    def review(self, task_id, accept, reason, *, principal, evidence, request_key):
        if type(accept) is not bool:
            raise DelegationError('accept must be bool')
        require_text(reason, 'reason')
        return self._change('review', task_id, principal, evidence, request_key, dict(accept=accept, reason=reason))

    def _change(self, action, task_id, principal, evidence, key, changes):
        with self.store.transaction() as conn:
            task = self._task(conn, task_id)
            owner = self._owner(conn, task['exp_id'], task['generation'], task['orchestrator_session'])
            allowed = (task['orchestrator_session'], task['child_session']) if action == 'settle' else (task['orchestrator_session'],)
            if principal not in allowed or principal is None:
                raise FencingError('only bound direct parent/child may perform this operation')
            child = changes.get('child_session', task['child_session'])
            payload = dict(action=action, exp_id=task['exp_id'], task_id=task_id, generation=task['generation'], orchestrator_session=task['orchestrator_session'], role=task['role'], input_hash=task['input_hash'], expected_artifact_hash=task['expected_artifact_hash'], child_session=child, parent_session=task['orchestrator_session'], depth=2, principal=principal, **{k: v for k, v in changes.items() if k != 'child_session'})
            if action == 'review':
                payload.update(result_hash=task['result_hash'], outcome=task['outcome'])
            event = self._verify(action, principal, evidence, payload, owner, child)
            old = self._old(conn, key, payload)
            if old is not None:
                return old
            required = {'dispatch': 'requested', 'settle': 'dispatched', 'review': 'settled'}[action]
            if task['status'] != required:
                raise DelegationError(f'{action} requires {required}')
            if action == 'review' and changes['accept'] and (task['outcome'] != 'finished'):
                raise DelegationError('only finished tasks may be accepted')
            if action == 'dispatch':
                if conn.execute('SELECT 1 FROM delegation_tasks WHERE child_session=?', (child,)).fetchone():
                    raise DelegationError('child session already bound to another task')
                conn.execute("UPDATE delegation_tasks SET status='dispatched',child_session=?,\n                    parent_session=?,depth=2 WHERE task_id=?", (child, task['orchestrator_session'], task_id))
            elif action == 'settle':
                conn.execute("UPDATE delegation_tasks SET status='settled',result_hash=?,outcome=? WHERE task_id=?", (changes['result_hash'], changes['outcome'], task_id))
            else:
                conn.execute('UPDATE delegation_tasks SET status=?,review_reason=? WHERE task_id=?', ('accepted' if changes['accept'] else 'rejected', changes['reason'], task_id))
            return self._record(conn, key, payload, self._task(conn, task_id), event)

    def audit(self, exp_id, generation, reason, *, principal, evidence, request_key):
        require_text(reason, 'reason')
        with self.store.transaction() as conn:
            owner = self._owner(conn, exp_id, generation, principal, root=True)
            payload = dict(action='audit', exp_id=exp_id, generation=generation, principal=principal, reason=reason)
            event = self._verify('audit', principal, evidence, payload, owner)
            old = self._old(conn, request_key, payload)
            if old is not None:
                return old
            return self._record(conn, request_key, payload, {'audited': True}, event)

    def status(self, exp_id):
        with closing(self.store.connect()) as conn:
            conn.execute('BEGIN')
            owner = conn.execute('SELECT * FROM delegation_owners WHERE exp_id=?', (exp_id,)).fetchone()
            if owner is None:
                return {'exp_id': exp_id, 'registered': False, 'waiting': False, 'accepted': False, 'owner': None, 'tasks': []}
            tasks = [dict(r) for r in conn.execute('SELECT * FROM delegation_tasks\n                WHERE exp_id=? AND generation=? ORDER BY task_id', (exp_id, owner['generation']))]
            return {'exp_id': exp_id, 'registered': True, 'owner': dict(owner), 'tasks': tasks, 'waiting': any((t['status'] in ('requested', 'dispatched', 'settled') for t in tasks)), 'accepted': bool(tasks) and all((t['status'] == 'accepted' for t in tasks))}
