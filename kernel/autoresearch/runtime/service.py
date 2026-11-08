# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/service.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import json
import time
try:
    from kernel.autoresearch.runtime.store import ALLOWED_TRANSITIONS, VersionConflict, InvalidTransition, canonical_json
    from kernel.autoresearch.runtime.accounts import Accounts, ApprovalError, BudgetError
    from kernel.autoresearch.runtime.scheduler import Scheduler
    from kernel.autoresearch.runtime.execution import Execution
except ImportError:
    from kernel.autoresearch.runtime.store import ALLOWED_TRANSITIONS, VersionConflict, InvalidTransition, canonical_json
    from kernel.autoresearch.runtime.accounts import Accounts, ApprovalError, BudgetError
    from kernel.autoresearch.runtime.scheduler import Scheduler
    from kernel.autoresearch.runtime.execution import Execution

class RuntimeErrorBase(ValueError):
    pass

class NoAdapter:

    def _deny(self, *args, **kwargs):
        raise PermissionError('external actions require an explicitly configured adapter')
    submit = query = collect = cancel = _deny

class Runtime:

    def __init__(self, store, adapter=None, evidence=None, clock=time.time, ownership_verifier=None, audit_verifier=None, idle_verifier=None, execution_class=Execution):
        self.store, self.clock = (store, clock)
        self.ownership_verifier = ownership_verifier
        self.audit_verifier = audit_verifier
        self.adapter = adapter
        self.accounts = Accounts(store, evidence)
        self.scheduler = Scheduler(store, clock=clock, idle_verifier=idle_verifier)
        with store.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS reservation_bindings(\n                reservation_id TEXT PRIMARY KEY REFERENCES budget_reservations(id),\n                request_key TEXT NOT NULL UNIQUE, exp_id TEXT NOT NULL REFERENCES experiments(id),\n                lease_token TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)')
            for op in ('UPDATE', 'DELETE'):
                conn.execute(f"CREATE TRIGGER IF NOT EXISTS reservation_bindings_no_{op.lower()}\n                    BEFORE {op} ON reservation_bindings BEGIN\n                    SELECT RAISE(ABORT,'reservation binding is immutable'); END")
        self.execution = execution_class(store, adapter or NoAdapter(), validate_reservation=self._reservation, validate_lease=self._lease, validate_queue=self._queue_guard)

    def _external(self):
        if self.adapter is None:
            raise PermissionError('external actions require an explicitly configured adapter')

    def _transition(self, conn, exp_id, state, actor='runtime', expected_version=None):
        row = conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
        if row is None:
            raise RuntimeErrorBase('experiment does not exist')
        if expected_version is not None and row['version'] != expected_version:
            raise VersionConflict('experiment version changed')
        if row['state'] == state:
            return dict(row)
        if state not in ALLOWED_TRANSITIONS[row['state']]:
            raise InvalidTransition(f"transition {row['state']} -> {state} is forbidden")
        conn.execute('UPDATE experiments SET state=?,version=version+1,updated_at=? WHERE id=?', (state, self.clock(), exp_id))
        self.store.emit(conn, exp_id, 'runtime_transition', actor, {'from': row['state'], 'to': state, 'version': row['version'] + 1})
        return dict(conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone())

    def request_approval(self, exp_id, expected_version, actor='runtime'):
        with self.store.transaction() as conn:
            return self._transition(conn, exp_id, 'awaiting_approval', actor, expected_version)

    def prepare(self, exp_id, expected_version, actor='runtime'):
        with self.store.transaction() as conn:
            valid = conn.execute("SELECT 1 FROM budget_reservations r\n                JOIN budget_accounts a ON a.series_id=r.series_id\n                WHERE r.exp_id=? AND r.status='reserved' AND r.scope='run'\n                AND a.scope='run' AND a.active=1 AND a.expires_at>? AND r.units>0", (exp_id, self.clock())).fetchone()
            if not valid:
                raise ApprovalError('prepare requires a live run budget reservation')
            return self._transition(conn, exp_id, 'ready', actor, expected_version)

    def _reservation(self, conn, spec, action):
        if action != 'submit':
            raise PermissionError('unsupported reservation action')
        row = conn.execute('SELECT r.*,a.active,a.expires_at,a.scope AS account_scope\n            FROM budget_reservations r JOIN budget_accounts a ON a.series_id=r.series_id\n            WHERE r.id=?', (spec.reservation_id,)).fetchone()
        if row is None or row['exp_id'] != spec.exp_id or row['scope'] != 'run' or (row['account_scope'] != 'run') or (row['status'] != 'reserved') or (not row['active']) or (row['expires_at'] <= self.clock()) or (row['units'] <= 0):
            raise ApprovalError('execution requires its own live run reservation')
        bound = conn.execute('SELECT * FROM reservation_bindings WHERE reservation_id=? OR request_key=?', (spec.reservation_id, spec.request_key)).fetchall()
        if bound:
            if len(bound) != 1 or any((bound[0][k] != v for k, v in (('reservation_id', spec.reservation_id), ('request_key', spec.request_key), ('exp_id', spec.exp_id), ('lease_token', spec.lease_id)))):
                raise BudgetError('reservation is already bound to another execution request')
        else:
            conn.execute('INSERT INTO reservation_bindings VALUES(?,?,?,?,?)', (spec.reservation_id, spec.request_key, spec.exp_id, spec.lease_id, self.clock()))
            self.store.emit(conn, spec.exp_id, 'reservation_bound', 'runtime', {'reservation_id': spec.reservation_id, 'request_key': spec.request_key, 'lease_token': spec.lease_id})
        return True

    def _lease(self, conn, spec, action):
        self._external()
        if self.ownership_verifier is not None:
            if self.ownership_verifier(conn, spec.exp_id) is not True:
                raise PermissionError('runtime backend ownership is not verified')
        else:
            try:
                if __package__:
                    from kernel.autoresearch.runtime.backend import require_runtime_ownership
                else:
                    from kernel.autoresearch.runtime.backend import require_runtime_ownership
                require_runtime_ownership(self.store, spec.exp_id)
            except (ImportError, ValueError, RuntimeError) as exc:
                raise PermissionError('runtime backend ownership is not verified') from exc
        now = self.clock()
        row = conn.execute('SELECT t.*,o.owner,o.generation AS live_generation,\n            o.lease_until AS owner_until FROM tasks t JOIN ownership o ON o.exp_id=t.exp_id\n            WHERE t.token=?', (spec.lease_id,)).fetchone()
        if row is None or row['exp_id'] != spec.exp_id or row['status'] != 'leased' or (not row['paid']) or (row['lease_until'] <= now) or (row['owner_until'] <= now) or (row['owner_generation'] != row['live_generation']):
            raise PermissionError('execution task token or owner generation is stale')
        if json.loads(row['payload']).get('request_key') != spec.request_key:
            raise PermissionError('task payload must identify this execution request_key')
        if action == 'cancel':
            return True
        if action != 'submit':
            raise PermissionError('unsupported lease action')
        keys = json.loads(row['resource_keys'])
        if not keys:
            raise PermissionError('paid execution must hold explicit resource leases')
        for key in keys:
            self.execution.policy.require('submit', {'experiment': spec.exp_id, 'resource': key})
            if conn.execute('SELECT 1 FROM scheduler_resource_quarantine WHERE resource_key=?', (key,)).fetchone():
                raise PermissionError('execution resource is quarantined')
            resource = conn.execute('SELECT * FROM resources WHERE resource_key=?', (key,)).fetchone()
            if resource is None or resource['task_id'] != row['id'] or resource['exp_id'] != spec.exp_id or (resource['generation'] != row['live_generation']) or (resource['token'] != spec.lease_id) or (resource['lease_until'] <= now):
                raise PermissionError('execution resource lease is absent or stale')
        exp = conn.execute('SELECT state FROM experiments WHERE id=?', (spec.exp_id,)).fetchone()
        if exp is None or exp['state'] not in ('ready', 'running'):
            raise PermissionError('experiment is not ready for a paid execution')
        if exp['state'] == 'ready':
            self._transition(conn, spec.exp_id, 'running')
        return True

    def _queue_guard(self, conn, spec):
        row = conn.execute('SELECT state FROM experiments WHERE id=?', (spec.exp_id,)).fetchone()
        if row is None or row['state'] not in ('ready', 'running'):
            raise PermissionError('experiment does not accept new paid attempts')
        return True

    def queue(self, spec):
        with self.store.transaction() as conn:
            return self.execution.queue(spec, conn=conn)

    def submit(self, attempt_id):
        self._external()
        return self.execution.submit(attempt_id)

    def reconcile(self, attempt_id):
        self._external()
        result = self.execution.reconcile(attempt_id)
        with self.store.transaction() as conn:
            unresolved = conn.execute("SELECT 1 FROM exec_attempts WHERE exp_id=?\n                AND state IN ('queued','submitting','running','unknown')", (result['exp_id'],)).fetchone()
            state = conn.execute('SELECT state FROM experiments WHERE id=?', (result['exp_id'],)).fetchone()[0]
            if not unresolved and state == 'running':
                self._transition(conn, result['exp_id'], 'collecting')
        return result

    def collect(self, attempt_id):
        self._external()
        row = self.execution.get(attempt_id)
        if row['state'] not in ('collecting', 'partial', 'completed'):
            raise RuntimeErrorBase('original remote job is not confirmed complete')
        result = self.execution.collect(attempt_id)
        with self.store.transaction() as conn:
            pending = conn.execute("SELECT 1 FROM exec_attempts WHERE exp_id=?\n                AND state NOT IN ('completed','failed','cancelled','partial')", (row['exp_id'],)).fetchone()
            state = conn.execute('SELECT state FROM experiments WHERE id=?', (row['exp_id'],)).fetchone()[0]
            if not pending and state == 'running':
                self._transition(conn, row['exp_id'], 'collecting')
        return result

    def cancel(self, attempt_id):
        self._external()
        return self.execution.cancel(attempt_id)

    def finalize(self, exp_id, expected_version, actor='runtime', audit_evidence=None):
        with self.store.transaction() as conn:
            exp = conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
            if exp is None or exp['version'] != expected_version:
                raise VersionConflict('experiment version changed or absent')
            if exp['state'] in ('completed', 'failed'):
                return dict(exp)
            history = [dict(row) for row in conn.execute('SELECT a.*,j.remote_id,j.remote_state\n                FROM exec_attempts a JOIN exec_jobs j ON j.attempt_id=a.id WHERE a.exp_id=?', (exp_id,))]
            if not history or any((row['state'] not in ('completed', 'failed', 'cancelled', 'partial') or row['remote_state'] not in ('completed', 'failed', 'cancelled') for row in history)):
                raise RuntimeErrorBase('all original jobs must be definitively terminal before finalization')
            superseded = {row['previous_attempt'] for row in history if row['previous_attempt']}
            attempts = [row for row in history if row['id'] not in superseded]
            if not attempts or any((row['state'] not in ('completed', 'failed', 'cancelled') for row in attempts)):
                raise RuntimeErrorBase('retry chain leaves must be definitively terminal')
            if conn.execute("SELECT 1 FROM budget_reservations WHERE exp_id=? AND status='reserved'", (exp_id,)).fetchone():
                raise BudgetError('settle or release all experiment reservations first')
            if conn.execute("SELECT 1 FROM tasks WHERE exp_id=? AND paid=1 AND status IN ('pending','leased')", (exp_id,)).fetchone():
                raise RuntimeErrorBase('ACK or cancel all paid tasks before finalization')
            if conn.execute('SELECT 1 FROM resources WHERE exp_id=?', (exp_id,)).fetchone():
                raise RuntimeErrorBase('independently confirm all execution resources idle before finalization')
            if any((row['state'] != 'completed' for row in attempts)):
                return self._transition(conn, exp_id, 'failed', actor)
            self.execution.policy.require('publish', {'experiment': exp_id})
            for attempt in attempts:
                self.execution.policy.require('publish', self.execution._context(attempt))
                token = json.loads(attempt['spec_json'])['lease_id']
                task = conn.execute('SELECT resource_keys FROM tasks WHERE token=?', (token,)).fetchone()
                if task:
                    for key in json.loads(task['resource_keys']):
                        self.execution.policy.require('publish', {'experiment': exp_id, 'resource': key})
            if self.audit_verifier is None or self.audit_verifier(conn, exp_id, attempts, audit_evidence) is not True:
                raise PermissionError('completed execution requires independently verified audit evidence')
            self.store.emit(conn, exp_id, 'runtime_audit_verified', actor, {'evidence': audit_evidence, 'attempt_ids': [a['id'] for a in attempts], 'history_attempt_ids': [a['id'] for a in history]})
            if exp['state'] == 'running':
                self._transition(conn, exp_id, 'collecting', actor)
            current = conn.execute('SELECT state FROM experiments WHERE id=?', (exp_id,)).fetchone()[0]
            if current == 'collecting':
                self._transition(conn, exp_id, 'auditing', actor)
            return self._transition(conn, exp_id, 'completed', actor)
