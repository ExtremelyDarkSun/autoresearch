# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/store.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path

class StoreError(ValueError):
    pass

class VersionConflict(StoreError):
    pass

class InvalidTransition(StoreError):
    pass

class IdempotencyConflict(StoreError):
    pass
STATES = frozenset(('planned', 'awaiting_approval', 'ready', 'running', 'collecting', 'auditing', 'completed', 'failed', 'paused'))
ALLOWED_TRANSITIONS = {'planned': frozenset(('awaiting_approval', 'paused', 'failed')), 'awaiting_approval': frozenset(('ready', 'planned', 'paused', 'failed')), 'ready': frozenset(('running', 'awaiting_approval', 'paused', 'failed')), 'running': frozenset(('collecting', 'paused', 'failed')), 'collecting': frozenset(('auditing', 'paused', 'failed')), 'auditing': frozenset(('completed', 'paused', 'failed')), 'paused': frozenset(('awaiting_approval', 'failed')), 'failed': frozenset(), 'completed': frozenset()}

def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value

class Store:

    def __init__(self, db_path):
        self.db_path = str(Path(db_path).resolve())
        if str(db_path) == ':memory:':
            raise ValueError('Store requires a file-backed database')
        self.busy_timeout_ms = 10000
        with self.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS experiments(\n                id TEXT PRIMARY KEY, goal TEXT, state TEXT,\n                version INTEGER DEFAULT 0, created_at REAL, updated_at REAL)')
            conn.execute('CREATE TABLE IF NOT EXISTS events(\n                id INTEGER PRIMARY KEY, exp_id TEXT REFERENCES experiments(id),\n                kind TEXT NOT NULL, actor TEXT NOT NULL,\n                payload_json TEXT NOT NULL, created_at REAL NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS store_requests(\n                request_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL,\n                result_json TEXT NOT NULL)')
            conn.execute("CREATE TRIGGER IF NOT EXISTS events_no_update\n                BEFORE UPDATE ON events BEGIN\n                SELECT RAISE(ABORT, 'events are append-only'); END")
            conn.execute("CREATE TRIGGER IF NOT EXISTS events_no_delete\n                BEFORE DELETE ON events BEGIN\n                SELECT RAISE(ABORT, 'events are append-only'); END")

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute(f'PRAGMA busy_timeout={self.busy_timeout_ms}')
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            raise
        finally:
            conn.close()

    def emit(self, conn, exp_id, kind, actor, payload):
        if not conn.in_transaction:
            raise StoreError('emit requires an active transaction')
        require_text(kind, 'kind')
        require_text(actor, 'actor')
        cursor = conn.execute('INSERT INTO events\n            (exp_id,kind,actor,payload_json,created_at) VALUES(?,?,?,?,?)', (exp_id, kind, actor, canonical_json(payload), time.time()))
        return cursor.lastrowid

    def ensure_experiment(self, exp_id, goal=''):
        require_text(exp_id, 'exp_id')
        if not isinstance(goal, str):
            raise ValueError('goal must be text')
        with self.transaction() as conn:
            now = time.time()
            cursor = conn.execute("INSERT OR IGNORE INTO experiments\n                (id,goal,state,version,created_at,updated_at)\n                VALUES(?,?,'planned',0,?,?)", (exp_id, goal, now, now))
            if cursor.rowcount:
                self.emit(conn, exp_id, 'experiment_created', 'store', {'goal': goal})
            return dict(conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone())

    def get_experiment(self, exp_id):
        conn = self.connect()
        try:
            row = conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def transition(self, exp_id, expected_version, new_state, actor, idempotency_key):
        require_text(exp_id, 'exp_id')
        require_text(actor, 'actor')
        require_text(idempotency_key, 'idempotency_key')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a non-negative integer')
        if new_state not in STATES:
            raise InvalidTransition('unknown experiment state')
        payload = canonical_json([exp_id, expected_version, new_state, actor])
        with self.transaction() as conn:
            old = conn.execute('SELECT * FROM store_requests WHERE request_key=?', (idempotency_key,)).fetchone()
            if old:
                if old['payload_json'] != payload:
                    raise IdempotencyConflict('request key has different payload')
                return json.loads(old['result_json'])
            row = conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
            if row is None:
                raise StoreError('experiment does not exist')
            if row['version'] != expected_version:
                raise VersionConflict('experiment version changed')
            if new_state not in ALLOWED_TRANSITIONS.get(row['state'], ()):
                raise InvalidTransition(f"transition {row['state']} -> {new_state} is forbidden")
            conn.execute('UPDATE experiments SET state=?,version=version+1,updated_at=?\n                WHERE id=? AND version=?', (new_state, time.time(), exp_id, expected_version))
            result = dict(conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone())
            self.emit(conn, exp_id, 'experiment_transition', actor, {'from': row['state'], 'to': new_state, 'version': result['version'], 'idempotency_key': idempotency_key})
            conn.execute('INSERT INTO store_requests VALUES(?,?,?)', (idempotency_key, payload, canonical_json(result)))
            return result
