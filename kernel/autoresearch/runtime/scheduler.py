# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/scheduler.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import json
import math
import time
import uuid
from contextlib import closing

class SchedulerError(RuntimeError):
    pass

class OwnershipConflict(SchedulerError):
    pass

class FencingError(SchedulerError):
    pass

class IdempotencyConflict(SchedulerError):
    pass

class PausedError(SchedulerError):
    pass

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value

def _seconds(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('lease_seconds must be finite and positive')
    return value

def _resources(values):
    if isinstance(values, str):
        raise ValueError('resources must be a sequence, not a string')
    return sorted(set((_text(v, 'resource') for v in values)))

class Scheduler:

    def __init__(self, store, *, clock=time.time, idle_verifier=None):
        if isinstance(store, (str, bytes)) or hasattr(store, '__fspath__'):
            if __package__:
                from kernel.autoresearch.runtime.store import Store
            else:
                from kernel.autoresearch.runtime.store import Store
            store = Store(store)
        self.store = store
        self.clock = clock
        self.idle_verifier = idle_verifier
        statements = ['CREATE TABLE IF NOT EXISTS ownership (\n                exp_id TEXT PRIMARY KEY REFERENCES experiments(id),\n                owner TEXT NOT NULL, generation INTEGER NOT NULL,\n                lease_until REAL NOT NULL)', 'CREATE TABLE IF NOT EXISTS scheduler_active (\n                exp_id TEXT PRIMARY KEY REFERENCES experiments(id),\n                active INTEGER NOT NULL CHECK(active IN (0,1)))', 'CREATE TABLE IF NOT EXISTS scheduler_control (\n                id INTEGER PRIMARY KEY CHECK(id=1),\n                pause_new_experiments INTEGER NOT NULL DEFAULT 0,\n                pause_paid INTEGER NOT NULL DEFAULT 0)', 'INSERT OR IGNORE INTO scheduler_control(id) VALUES(1)', "CREATE TABLE IF NOT EXISTS tasks (\n                id TEXT PRIMARY KEY,\n                exp_id TEXT NOT NULL REFERENCES experiments(id),\n                idempotency_key TEXT NOT NULL,\n                payload TEXT NOT NULL, kind TEXT NOT NULL,\n                resource_keys TEXT NOT NULL,\n                paid INTEGER NOT NULL, starts_experiment INTEGER NOT NULL,\n                target_agent_id TEXT,\n                status TEXT NOT NULL CHECK(status IN ('pending','leased','acked','cancelled')),\n                owner_generation INTEGER NOT NULL,\n                worker_id TEXT, token TEXT, lease_until REAL,\n                attempts INTEGER NOT NULL DEFAULT 0,\n                available_at REAL NOT NULL, created_at REAL NOT NULL,\n                updated_at REAL NOT NULL, result TEXT, last_error TEXT,\n                UNIQUE(exp_id,idempotency_key))", 'CREATE INDEX IF NOT EXISTS tasks_ready\n                ON tasks(exp_id,status,available_at,created_at)', 'CREATE TABLE IF NOT EXISTS resources (\n                resource_key TEXT PRIMARY KEY,\n                task_id TEXT NOT NULL REFERENCES tasks(id),\n                exp_id TEXT NOT NULL REFERENCES experiments(id),\n                generation INTEGER NOT NULL, token TEXT NOT NULL,\n                lease_until REAL NOT NULL)', 'CREATE TABLE IF NOT EXISTS scheduler_resource_quarantine (\n                resource_key TEXT PRIMARY KEY REFERENCES resources(resource_key),\n                reason TEXT NOT NULL, quarantined_at REAL NOT NULL)']
        with self.store.transaction() as conn:
            for statement in statements:
                conn.execute(statement)

    def _now(self):
        value = float(self.clock())
        if not math.isfinite(value):
            raise ValueError('clock must return a finite timestamp')
        return value

    def _emit(self, conn, exp_id, kind, actor, payload):
        self.store.emit(conn, exp_id, 'scheduler.' + kind, actor, payload)

    def _owner(self, conn, exp_id, owner, generation, now):
        row = conn.execute('SELECT * FROM ownership WHERE exp_id=?', (exp_id,)).fetchone()
        if not row or row['owner'] != owner or row['generation'] != generation or (row['lease_until'] <= now):
            raise FencingError('owner/generation lease is stale')
        return row

    @staticmethod
    def _task(row):
        if row is None:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        result['resources'] = json.loads(result.pop('resource_keys'))
        result['result'] = json.loads(result['result']) if result['result'] is not None else None
        result['generation'] = result['owner_generation']
        result['paid'] = bool(result['paid'])
        result['starts_experiment'] = bool(result['starts_experiment'])
        return result

    def _release_resources(self, conn, row, now, reason):
        leases = conn.execute('SELECT * FROM resources WHERE task_id=?', (row['id'],)).fetchall()
        for lease in leases:
            if row['paid']:
                inserted = conn.execute('INSERT OR IGNORE INTO scheduler_resource_quarantine\n                    VALUES(?,?,?)', (lease['resource_key'], reason, now)).rowcount
                if inserted:
                    self._emit(conn, row['exp_id'], 'resource_quarantined', 'scheduler', {'resource_key': lease['resource_key'], 'task_id': row['id'], 'token': lease['token'], 'reason': reason})
            else:
                conn.execute('DELETE FROM resources WHERE resource_key=? AND resource_key NOT IN\n                    (SELECT resource_key FROM scheduler_resource_quarantine)', (lease['resource_key'],))

    def confirm_resource_idle(self, resource_key, evidence, *, actor='main'):
        _text(resource_key, 'resource_key')
        _text(actor, 'actor')
        if not isinstance(evidence, dict):
            raise ValueError('evidence must be a JSON object')
        encoded = _json(evidence)
        if self.idle_verifier is None:
            raise SchedulerError('resource idle verification is unavailable; release denied')
        with self.store.transaction() as conn:
            now = self._now()
            self._reap(conn, now)
            row = conn.execute('SELECT r.*,q.reason,q.quarantined_at FROM resources r\n                JOIN scheduler_resource_quarantine q USING(resource_key)\n                WHERE resource_key=?', (resource_key,)).fetchone()
            if row is None:
                raise SchedulerError('resource is not quarantined')
            snapshot = dict(row)
            for key in ('resource_key', 'task_id', 'token'):
                if evidence.get(key) != snapshot[key]:
                    raise FencingError('idle evidence does not identify the held resource attempt')
            if self.idle_verifier(resource_key, json.loads(encoded), dict(snapshot)) is not True:
                raise SchedulerError('resource idle evidence was rejected')
            conn.execute('DELETE FROM scheduler_resource_quarantine WHERE resource_key=?', (resource_key,))
            conn.execute('DELETE FROM resources WHERE resource_key=?', (resource_key,))
            self._emit(conn, row['exp_id'], 'resource_idle_confirmed', actor, {'resource_key': resource_key, 'task_id': row['task_id'], 'token': row['token'], 'evidence': json.loads(encoded)})
            return {'resource_key': resource_key, 'released': True}

    def _reap(self, conn, now, exp_id=None):
        sql = "SELECT * FROM tasks WHERE status='leased' AND lease_until<=?"
        args = [now]
        if exp_id is not None:
            sql += ' AND exp_id=?'
            args.append(exp_id)
        rows = conn.execute(sql, args).fetchall()
        for row in rows:
            self._release_resources(conn, row, now, 'lease_expired')
            conn.execute("UPDATE tasks SET status='pending', worker_id=NULL,\n                token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now, row['id']))
            self._emit(conn, row['exp_id'], 'lease_expired', 'scheduler', {'task_id': row['id'], 'attempt': row['attempts']})
        return len(rows)

    def activate(self, exp_id, goal='', *, actor='main'):
        _text(exp_id, 'exp_id')
        if not isinstance(goal, str):
            raise ValueError('goal must be text')
        with self.store.transaction() as conn:
            now = self._now()
            active = conn.execute('SELECT active FROM scheduler_active WHERE exp_id=?', (exp_id,)).fetchone()
            paused = conn.execute('SELECT pause_new_experiments FROM scheduler_control WHERE id=1').fetchone()[0]
            if paused and (not active or not active['active']):
                raise PausedError('new experiments are paused')
            created = conn.execute("INSERT OR IGNORE INTO experiments\n                (id,goal,state,version,created_at,updated_at)\n                VALUES(?,?,'planned',0,?,?)", (exp_id, goal, now, now)).rowcount
            if created:
                self._emit(conn, exp_id, 'experiment_registered', actor, {'goal': goal})
            conn.execute('INSERT INTO scheduler_active VALUES(?,1)\n                ON CONFLICT(exp_id) DO UPDATE SET active=1', (exp_id,))
            self._emit(conn, exp_id, 'active', actor, {'active': True})
            return {'exp_id': exp_id, 'active': True}

    def set_active(self, exp_id, active, *, actor='main'):
        if type(active) is not bool:
            raise ValueError('active must be bool')
        with self.store.transaction() as conn:
            row = conn.execute('SELECT active FROM scheduler_active WHERE exp_id=?', (exp_id,)).fetchone()
            control = conn.execute('SELECT * FROM scheduler_control WHERE id=1').fetchone()
            if active and (not row or not row['active']) and control['pause_new_experiments']:
                raise PausedError('new experiments are paused')
            conn.execute('INSERT INTO scheduler_active VALUES(?,?)\n                ON CONFLICT(exp_id) DO UPDATE SET active=excluded.active', (exp_id, int(active)))
            self._emit(conn, exp_id, 'active', actor, {'active': active})
        return {'exp_id': exp_id, 'active': active}

    def takeover(self, exp_id, owner, *, lease_seconds=300, force=False):
        _text(owner, 'owner')
        duration = _seconds(lease_seconds)
        with self.store.transaction() as conn:
            now = self._now()
            row = conn.execute('SELECT * FROM ownership WHERE exp_id=?', (exp_id,)).fetchone()
            if row and row['lease_until'] > now and (not force):
                raise OwnershipConflict('experiment already has a live orchestrator')
            generation = row['generation'] + 1 if row else 1
            conn.execute('INSERT INTO ownership VALUES(?,?,?,?) ON CONFLICT(exp_id)\n                DO UPDATE SET owner=excluded.owner,generation=excluded.generation,\n                lease_until=excluded.lease_until', (exp_id, owner, generation, now + duration))
            held_tasks = conn.execute('SELECT DISTINCT t.* FROM tasks t JOIN resources r\n                ON t.id=r.task_id WHERE r.exp_id=?', (exp_id,)).fetchall()
            for held in held_tasks:
                self._release_resources(conn, held, now, 'owner_takeover')
            conn.execute("UPDATE tasks SET status='pending',worker_id=NULL,token=NULL,\n                lease_until=NULL,updated_at=? WHERE exp_id=? AND status='leased'", (now, exp_id))
            conn.execute("UPDATE tasks SET owner_generation=? WHERE exp_id=?\n                AND status='pending'", (generation, exp_id))
            result = {'exp_id': exp_id, 'owner': owner, 'generation': generation, 'lease_until': now + duration}
            self._emit(conn, exp_id, 'takeover', owner, result)
            return result

    def renew_owner(self, exp_id, owner, generation, *, lease_seconds=300):
        duration = _seconds(lease_seconds)
        with self.store.transaction() as conn:
            now = self._now()
            own = self._owner(conn, exp_id, owner, generation, now)
            until = max(now + duration, own['lease_until'])
            conn.execute('UPDATE ownership SET lease_until=? WHERE exp_id=?', (until, exp_id))
            self._emit(conn, exp_id, 'owner_renewed', owner, {'generation': generation, 'lease_until': until})
            return {'exp_id': exp_id, 'owner': owner, 'generation': generation, 'lease_until': until}

    def enqueue(self, exp_id, key, payload, owner, generation, *, kind='message', resources=(), paid=False, starts_experiment=False, target_agent_id=None):
        _text(key, 'key')
        _text(kind, 'kind')
        if not isinstance(payload, dict):
            raise ValueError('payload must be a JSON object')
        if type(paid) is not bool or type(starts_experiment) is not bool:
            raise ValueError('paid and starts_experiment must be bool')
        if kind in ('plan', 'planning') and paid:
            raise ValueError('planning tasks cannot be paid execution tasks')
        if target_agent_id is not None:
            _text(target_agent_id, 'target_agent_id')
        encoded = _json(payload)
        encoded_resources = _json(_resources(resources))
        spec = (encoded, kind, encoded_resources, int(paid), int(starts_experiment), target_agent_id)
        with self.store.transaction() as conn:
            now = self._now()
            self._owner(conn, exp_id, owner, generation, now)
            state = conn.execute('SELECT state FROM experiments WHERE id=?', (exp_id,)).fetchone()[0]
            if paid and state in ('completed', 'failed'):
                raise SchedulerError('terminal experiment cannot enqueue paid execution')
            old = conn.execute('SELECT * FROM tasks WHERE exp_id=? AND idempotency_key=?', (exp_id, key)).fetchone()
            if old:
                existing = tuple((old[k] for k in ('payload', 'kind', 'resource_keys', 'paid', 'starts_experiment', 'target_agent_id')))
                if existing != spec:
                    raise IdempotencyConflict('same key has a different payload or scheduling specification')
                return self._task(old)
            task_id = uuid.uuid4().hex
            conn.execute("INSERT INTO tasks(id,exp_id,idempotency_key,payload,kind,\n                resource_keys,paid,starts_experiment,target_agent_id,status,owner_generation,\n                available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)", (task_id, exp_id, key, *spec, generation, now, now, now))
            self._emit(conn, exp_id, 'enqueued', owner, {'task_id': task_id, 'key': key, 'generation': generation})
            return self._task(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def claim(self, exp_id, owner, generation, worker_id, *, lease_seconds=60):
        _text(worker_id, 'worker_id')
        duration = _seconds(lease_seconds)
        with self.store.transaction() as conn:
            now = self._now()
            own = self._owner(conn, exp_id, owner, generation, now)
            self._reap(conn, now)
            active = conn.execute('SELECT active FROM scheduler_active WHERE exp_id=?', (exp_id,)).fetchone()
            if not active or not active['active']:
                return None
            control = conn.execute('SELECT * FROM scheduler_control WHERE id=1').fetchone()
            rows = conn.execute("SELECT * FROM tasks WHERE exp_id=? AND status='pending'\n                AND available_at<=? AND owner_generation=? ORDER BY created_at,id", (exp_id, now, generation)).fetchall()
            state = conn.execute('SELECT state FROM experiments WHERE id=?', (exp_id,)).fetchone()[0]
            for row in rows:
                if row['paid'] and (control['pause_paid'] or state in ('completed', 'failed')):
                    continue
                if row['starts_experiment'] and control['pause_new_experiments']:
                    continue
                keys = json.loads(row['resource_keys'])
                if any((conn.execute('SELECT 1 FROM resources WHERE resource_key=?', (k,)).fetchone() for k in keys)):
                    continue
                token = uuid.uuid4().hex
                until = min(now + duration, own['lease_until'])
                conn.execute("UPDATE tasks SET status='leased',worker_id=?,token=?,lease_until=?,\n                    attempts=attempts+1,updated_at=? WHERE id=?", (worker_id, token, until, now, row['id']))
                for key in keys:
                    conn.execute('INSERT INTO resources VALUES(?,?,?,?,?,?)', (key, row['id'], exp_id, generation, token, until))
                self._emit(conn, exp_id, 'claimed', owner, {'task_id': row['id'], 'worker_id': worker_id, 'token': token, 'generation': generation, 'lease_until': until})
                task = self._task(conn.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())
                task['owner'] = owner
                envelope = {'exp_id': exp_id, 'owner': owner, 'generation': generation, 'task_id': task['id'], 'worker_id': worker_id, 'token': token, 'lease_until': until, 'kind': task['kind'], 'payload': task['payload'], 'resources': task['resources']}
                task['dispatch'] = {'tool': 'send_message', 'agent_id': task['target_agent_id'], 'message': _json(envelope), 'task_id': task['id'], 'payload': task['payload'], 'token': token, 'generation': generation, 'requires_target': task['target_agent_id'] is None}
                return task
            return None

    def _attempt(self, conn, task_id, owner, generation, worker_id, token, now):
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise FencingError('unknown task')
        self._owner(conn, row['exp_id'], owner, generation, now)
        if row['owner_generation'] != generation or row['worker_id'] != worker_id or (not token) or (row['token'] != token):
            raise FencingError('task attempt is stale')
        return row

    def ack(self, task_id, owner, generation, worker_id, token, *, result=None):
        encoded = _json(result)
        with self.store.transaction() as conn:
            now = self._now()
            row = self._attempt(conn, task_id, owner, generation, worker_id, token, now)
            if row['status'] == 'acked':
                if row['result'] != encoded:
                    raise IdempotencyConflict('duplicate ACK has a different result')
                return self._task(row)
            if row['status'] != 'leased' or row['lease_until'] <= now:
                raise FencingError('task is not leased or lease expired')
            conn.execute("UPDATE tasks SET status='acked',result=?,updated_at=? WHERE id=?", (encoded, now, task_id))
            self._release_resources(conn, row, now, 'task_attempt_ended')
            self._emit(conn, row['exp_id'], 'acked', owner, {'task_id': task_id, 'result': result})
            return self._task(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def retry(self, task_id, owner, generation, worker_id, token, *, delay_seconds=0, error=''):
        delay = float(delay_seconds)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('delay_seconds must be finite and non-negative')
        with self.store.transaction() as conn:
            now = self._now()
            row = self._attempt(conn, task_id, owner, generation, worker_id, token, now)
            if row['status'] != 'leased' or row['lease_until'] <= now:
                raise FencingError('task is not leased or lease expired')
            conn.execute("UPDATE tasks SET status='pending',worker_id=NULL,token=NULL,\n                lease_until=NULL,available_at=?,last_error=?,updated_at=? WHERE id=?", (now + delay, str(error), now, task_id))
            self._release_resources(conn, row, now, 'task_attempt_ended')
            self._emit(conn, row['exp_id'], 'retry', owner, {'task_id': task_id, 'error': str(error), 'available_at': now + delay})
            return self._task(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def renew_task(self, task_id, owner, generation, worker_id, token, *, lease_seconds=60):
        duration = _seconds(lease_seconds)
        with self.store.transaction() as conn:
            now = self._now()
            row = self._attempt(conn, task_id, owner, generation, worker_id, token, now)
            if row['status'] != 'leased' or row['lease_until'] <= now:
                raise FencingError('task is not leased or lease expired')
            own = self._owner(conn, row['exp_id'], owner, generation, now)
            until = min(max(now + duration, row['lease_until']), own['lease_until'])
            conn.execute('UPDATE tasks SET lease_until=?,updated_at=? WHERE id=?', (until, now, task_id))
            conn.execute('UPDATE resources SET lease_until=? WHERE task_id=? AND token=?', (until, task_id, token))
            self._emit(conn, row['exp_id'], 'task_renewed', owner, {'task_id': task_id, 'lease_until': until})
            return self._task(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def cancel(self, task_id, owner, generation, *, reason):
        _text(reason, 'reason')
        with self.store.transaction() as conn:
            now = self._now()
            row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise FencingError('unknown task')
            self._owner(conn, row['exp_id'], owner, generation, now)
            if row['status'] in ('acked', 'cancelled'):
                return self._task(row)
            if row['owner_generation'] != generation:
                raise FencingError('task generation is stale')
            conn.execute("UPDATE tasks SET status='cancelled',token=NULL,lease_until=NULL,\n                last_error=?,updated_at=? WHERE id=?", (reason, now, task_id))
            self._release_resources(conn, row, now, 'task_attempt_ended')
            self._emit(conn, row['exp_id'], 'cancelled', owner, {'task_id': task_id, 'reason': reason})
            return self._task(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def set_control(self, *, pause_new_experiments=None, pause_paid=None, actor='main', reason):
        _text(reason, 'reason')
        for value in (pause_new_experiments, pause_paid):
            if value is not None and type(value) is not bool:
                raise ValueError('pause flags must be bool or None')
        with self.store.transaction() as conn:
            old = dict(conn.execute('SELECT * FROM scheduler_control WHERE id=1').fetchone())
            new = {'pause_new_experiments': old['pause_new_experiments'] if pause_new_experiments is None else int(pause_new_experiments), 'pause_paid': old['pause_paid'] if pause_paid is None else int(pause_paid)}
            conn.execute('UPDATE scheduler_control SET pause_new_experiments=?,pause_paid=? WHERE id=1', tuple(new.values()))
            self._emit(conn, None, 'control', actor, {**new, 'reason': reason})
            return {k: bool(v) for k, v in new.items()}

    def get_task(self, task_id):
        with closing(self.store.connect()) as conn:
            return self._task(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def active_experiments(self):
        with closing(self.store.connect()) as conn:
            return [r['exp_id'] for r in conn.execute('SELECT exp_id FROM scheduler_active WHERE active=1 ORDER BY exp_id')]

    def overview(self, *, active_only=False):
        with closing(self.store.connect()) as conn:
            conn.execute('BEGIN')
            now = self._now()
            control = dict(conn.execute('SELECT * FROM scheduler_control WHERE id=1').fetchone())
            sql = 'SELECT e.*,COALESCE(a.active,0) AS active,o.owner,o.generation,o.lease_until\n                FROM experiments e LEFT JOIN scheduler_active a ON e.id=a.exp_id\n                LEFT JOIN ownership o ON e.id=o.exp_id'
            if active_only:
                sql += ' WHERE a.active=1'
            experiments = []
            for row in conn.execute(sql + ' ORDER BY e.id'):
                item = dict(row)
                item['active'] = bool(item['active'])
                item['owner_valid'] = item['lease_until'] is not None and item['lease_until'] > now
                item['tasks'] = {r['status']: r['n'] for r in conn.execute('SELECT status,COUNT(*) n FROM tasks WHERE exp_id=? GROUP BY status', (row['id'],))}
                item['expired_tasks'] = conn.execute("SELECT COUNT(*) FROM tasks WHERE exp_id=? AND status='leased' AND lease_until<=?", (row['id'], now)).fetchone()[0]
                experiments.append(item)
            leases = [dict(r) for r in conn.execute('SELECT r.*,q.reason AS quarantine_reason,\n                q.quarantined_at FROM resources r LEFT JOIN scheduler_resource_quarantine q\n                USING(resource_key) ORDER BY resource_key')]
            for resource in leases:
                expired = resource['lease_until'] <= now
                resource['state'] = 'unknown' if resource['quarantine_reason'] is not None or expired else 'leased'
                resource['occupied'] = True
                resource['valid'] = not expired and resource['quarantine_reason'] is None
            return {'as_of': now, 'controls': {k: bool(v) for k, v in control.items() if k != 'id'}, 'active_experiments': [e['id'] for e in experiments if e['active']], 'experiments': experiments, 'resources': leases}
