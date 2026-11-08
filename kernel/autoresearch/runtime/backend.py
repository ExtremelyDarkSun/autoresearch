# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/backend.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager
MARKER = '.hil-runtime-owner.json'
COMPANION = MARKER + '.README.md'
CLI = 'python research.py runtime'

class BackendError(ValueError):
    pass

def _exp(value):
    if not isinstance(value, str) or not value.strip() or value in ('.', '..') or ('/' in value) or ('\\' in value):
        raise BackendError('exp_id must be a single non-empty directory name')
    return value

@contextmanager
def readonly(db_path):
    path = Path(db_path).resolve()
    if not path.is_file():
        raise BackendError('runtime database is missing')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()

def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

def _row(conn, exp_id):
    if 'backend_migrations' not in _tables(conn):
        return None
    row = conn.execute('SELECT * FROM backend_migrations WHERE exp_id=?', (exp_id,)).fetchone()
    return dict(row) if row else None

def _marker(path):
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8'))
    if not isinstance(value, dict) or value.get('format') != 1:
        raise BackendError('runtime ownership marker is invalid')
    return (value, hashlib.sha256(raw).hexdigest())

def _validate(conn, row, directory, db_path):
    if not row or row['backend'] != 'runtime':
        raise BackendError('runtime ownership is absent or pending')
    directory = Path(directory).resolve()
    marker, sha = _marker(directory / MARKER)
    expected = {'format': 1, 'exp_id': row['exp_id'], 'database': str(Path(db_path).resolve()), 'legacy_dir': str(directory), 'migration_id': row['migration_id']}
    if marker != expected or row['legacy_dir'] != str(directory) or row['marker_sha256'] != sha:
        raise BackendError('database and runtime ownership marker disagree')
    companion = directory / COMPANION
    if (directory / MARKER).stat().st_mode & 146 or companion.stat().st_mode & 146:
        raise BackendError('runtime ownership artifacts must remain read-only')
    if hashlib.sha256(companion.read_bytes()).hexdigest() != row['companion_sha256']:
        raise BackendError('runtime ownership provenance is missing or changed')
    if not conn.execute('SELECT 1 FROM experiments WHERE id=?', (row['exp_id'],)).fetchone():
        raise BackendError('runtime experiment is missing')
    return marker

class BackendRegistry:

    def __init__(self, store):
        self.store = store
        with store.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS backend_migrations(\n                exp_id TEXT PRIMARY KEY REFERENCES experiments(id),\n                backend TEXT NOT NULL CHECK(backend IN ('pending','runtime')),\n                legacy_dir TEXT NOT NULL UNIQUE, migration_id TEXT NOT NULL UNIQUE,\n                marker_sha256 TEXT, companion_sha256 TEXT,\n                evidence_json TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)")

    def adopt(self, exp_id, legacy_dir, *, verifier=None, actor='migration', goal=''):
        _exp(exp_id)
        directory = Path(legacy_dir).resolve()
        if directory.name != exp_id or not directory.is_dir():
            raise BackendError('legacy_dir must be the existing experiment directory')
        if not callable(verifier):
            raise BackendError('migration requires a trusted quiescence verifier')
        proof = verifier(exp_id, directory)
        if not isinstance(proof, dict) or proof.get('quiescent') is not True or type(proof.get('active_legacy_jobs')) is not int or (proof['active_legacy_jobs'] != 0) or (type(proof.get('live_legacy_authorizations')) is not int) or (proof['live_legacy_authorizations'] != 0) or (not proof.get('evidence')):
            raise BackendError('legacy jobs or authorizations are active or unverified')
        evidence = json.dumps(proof, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if not isinstance(actor, str) or not actor.strip():
            raise BackendError('actor must be non-empty')
        marker_path = directory / MARKER
        companion_path = directory / COMPANION
        if os.path.lexists(marker_path) or os.path.lexists(companion_path):
            raise BackendError('existing ownership artifacts require operator review')
        migration_id = str(uuid.uuid4())
        marker = {'format': 1, 'exp_id': exp_id, 'database': self.store.db_path, 'legacy_dir': str(directory), 'migration_id': migration_id}
        raw = (json.dumps(marker, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
        now = time.strftime('%Y-%m-%d %H:%M %z')
        provenance = f'<!--\nGENERATED-BY: runtime/backend.py ({now})\nLAST-EDIT: runtime/backend.py ({now})\nPURPOSE: 显式迁移生成的runtime所有权marker出处。\nORIGIN: none\nCUMULATIVE-DELTA: 迁移ID {migration_id}，执行者 {actor}。\n-->\n# Runtime ownership\n\nProducer: {CLI} / BackendRegistry.adopt\nMigration: {migration_id}\n\nEvidence: {evidence}\n\nMarker and database must agree. Never remove the marker to resume legacy writes.\n'.encode('utf-8')
        for path, data in ((marker_path, raw), (companion_path, provenance)):
            with path.open('xb') as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            path.chmod(292)
        self.store.ensure_experiment(exp_id, goal)
        with self.store.transaction() as conn:
            if _row(conn, exp_id):
                raise BackendError('experiment already has a backend migration record')
            state = conn.execute('SELECT state FROM experiments WHERE id=?', (exp_id,)).fetchone()[0]
            if state != 'planned':
                raise BackendError('adoption requires a planned runtime experiment')
            conn.execute('INSERT INTO backend_migrations VALUES(?,?,?,?,?,?,?,?,?)', (exp_id, 'pending', str(directory), migration_id, None, None, evidence, actor, time.time()))
            self.store.emit(conn, exp_id, 'backend_migration_pending', actor, {'migration_id': migration_id})
        with self.store.transaction() as conn:
            conn.execute("UPDATE backend_migrations SET backend='runtime',marker_sha256=?,companion_sha256=? WHERE exp_id=? AND migration_id=? AND backend='pending'", (hashlib.sha256(raw).hexdigest(), hashlib.sha256(provenance).hexdigest(), exp_id, migration_id))
            _validate(conn, _row(conn, exp_id), directory, self.store.db_path)
            self.store.emit(conn, exp_id, 'backend_runtime_owned', actor, {'migration_id': migration_id})
        return marker
    switch = adopt

    def register_new(self, exp_id, directory, *, actor='migration', goal=''):
        _exp(exp_id)
        directory = Path(directory).resolve()
        if directory.name != exp_id:
            raise BackendError('directory name must match exp_id')
        directory.mkdir(exist_ok=False)
        proof = {'quiescent': True, 'active_legacy_jobs': 0, 'live_legacy_authorizations': 0, 'evidence': {'kind': 'exclusive_new_directory', 'path': str(directory)}}
        return self.adopt(exp_id, directory, verifier=lambda *_: proof, actor=actor, goal=goal)

def require_runtime_ownership(store, exp_id):
    _exp(exp_id)
    with readonly(store.db_path) as conn:
        row = _row(conn, exp_id)
        if not row:
            raise BackendError('runtime backend has not been explicitly adopted')
        return _validate(conn, row, row['legacy_dir'], store.db_path)

def legacy_guard(exp_id, state_root, runtime_db=None):
    base = {'exp': exp_id, 'exit': 9, 'exit_code': 9, 'phase': 'runtime_owned', 'action': 'runtime_owned', 'next': 'runtime_cli', 'writes': False, 'reason': '实验由runtime拥有，旧stage不得执行。', 'runtime_cli': CLI}
    try:
        _exp(exp_id)
        directory = (Path(state_root).resolve() / exp_id).resolve()
        path = directory / MARKER
        has_marker = os.path.lexists(path)
        if has_marker:
            marker, _ = _marker(path)
            db = marker.get('database')
            if not isinstance(db, str) or not Path(db).is_absolute():
                raise BackendError('marker database must be an absolute path')
            if runtime_db is not None and Path(runtime_db).resolve() != Path(db).resolve():
                raise BackendError('explicit runtime database does not match marker')
            with readonly(db) as conn:
                _validate(conn, _row(conn, exp_id), directory, db)
            base['runtime_db'] = db
            return base
        if runtime_db is not None:
            with readonly(runtime_db) as conn:
                row = _row(conn, exp_id)
                exists = conn.execute('SELECT 1 FROM experiments WHERE id=?', (exp_id,)).fetchone()
                if row or exists:
                    raise BackendError('runtime registration has no matching ownership marker')
        return None
    except (ValueError, OSError, sqlite3.Error) as exc:
        base.update(phase='runtime_ownership_error', action='runtime_ownership_error', reason=str(exc))
        return base

def runtime_overview(db_path, exp_id=None):
    with readonly(db_path) as conn:
        tables = _tables(conn)
        result = {'backend': 'runtime', 'runtime_db': str(Path(db_path).resolve()), 'writes': False, 'runtime_cli': CLI, 'dimensions': {}}
        mapping = {'experiments': 'experiments', 'backend_ownership': 'backend_migrations', 'orchestrators': 'ownership', 'tasks': 'tasks', 'resources': 'resources', 'resource_quarantine': 'scheduler_resource_quarantine', 'budgets': 'budget_accounts', 'reservations': 'budget_reservations', 'executions': 'exec_attempts', 'delivery': 'delivery', 'delivery_receipts': 'delivery_receipts', 'control': 'scheduler_control'}
        for dimension, table in mapping.items():
            result['dimensions'][dimension] = [dict(r) for r in conn.execute(f'SELECT * FROM {table}')] if table in tables else []
        if exp_id is not None:
            result['exp'] = exp_id
            row = _row(conn, exp_id)
            if not row:
                raise BackendError('experiment has no backend ownership record')
            _validate(conn, row, row['legacy_dir'], db_path)
        return result
