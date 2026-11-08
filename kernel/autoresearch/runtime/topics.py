# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/topics.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
try:
    from kernel.autoresearch.runtime.store import Store, canonical_json
except ImportError:
    from kernel.autoresearch.runtime.store import Store, canonical_json
TOPIC_STATUSES = frozenset(('active', 'paused', 'completed', 'archived'))
SESSION_STATUSES = frozenset(('open', 'handed_off', 'crashed', 'superseded', 'closed'))
INFLIGHT_STATUSES = frozenset(('open', 'released', 'lost'))
CHECKPOINT_KINDS = frozenset(('start', 'node', 'handoff', 'recovery', 'close', 'final'))
CHECKPOINT_FIELDS = ('summary', 'completed', 'pending', 'inflight', 'next_action', 'evidence', 'extra')
EDITED_TOPIC_FIELDS = ('title', 'objective', 'completion_criteria', 'priority')
_TOPIC_KEYS = ('id', 'title', 'objective', 'completion_criteria', 'priority', 'status', 'version', 'worktree', 'branch', 'generation', 'leader_session_id', 'leader_generation', 'leader_since', 'created_at', 'updated_at', 'updated_by')
_SESSION_KEYS = ('topic_id', 'session_id', 'generation', 'predecessor_session_id', 'predecessor_generation', 'status', 'opened_at', 'ended_at', 'ended_reason', 'note', 'actor')
_TS = "(strftime('%Y-%m-%dT%H:%M:%f','now') || 'Z')"
_SCHEMA = (f"CREATE TABLE IF NOT EXISTS topics(\n        id TEXT PRIMARY KEY,\n        title TEXT NOT NULL,\n        objective TEXT NOT NULL DEFAULT '',\n        completion_criteria TEXT NOT NULL DEFAULT '',\n        priority INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 0 AND 3),\n        status TEXT NOT NULL CHECK(status IN ('active','paused','completed','archived')),\n        version INTEGER NOT NULL DEFAULT 0,\n        worktree TEXT, branch TEXT,\n        generation INTEGER NOT NULL DEFAULT 0,\n        leader_session_id TEXT, leader_generation INTEGER,\n        leader_since TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,\n        updated_by TEXT NOT NULL)", f"CREATE TABLE IF NOT EXISTS topic_sessions(\n        topic_id TEXT NOT NULL REFERENCES topics(id),\n        session_id TEXT NOT NULL,\n        generation INTEGER NOT NULL,\n        predecessor_session_id TEXT, predecessor_generation INTEGER,\n        status TEXT NOT NULL CHECK(status IN ('open','handed_off','crashed','superseded','closed')),\n        opened_at TEXT NOT NULL, ended_at TEXT, ended_reason TEXT,\n        note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL,\n        PRIMARY KEY(topic_id, generation))", "CREATE UNIQUE INDEX IF NOT EXISTS topic_sessions_one_live\n        ON topic_sessions(topic_id) WHERE status='open'", 'CREATE UNIQUE INDEX IF NOT EXISTS topic_sessions_session_generation\n        ON topic_sessions(topic_id, session_id, generation)', f'CREATE TABLE IF NOT EXISTS topic_session_events(\n        id INTEGER PRIMARY KEY, topic_id TEXT NOT NULL, session_id TEXT NOT NULL,\n        generation INTEGER, kind TEXT NOT NULL, actor TEXT NOT NULL,\n        detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT {_TS})', f"CREATE TABLE IF NOT EXISTS topic_checkpoints(\n        id INTEGER PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id),\n        session_id TEXT NOT NULL, generation INTEGER NOT NULL,\n        kind TEXT NOT NULL CHECK(kind IN ('start','node','handoff','recovery','close','final')),\n        reason TEXT, summary TEXT NOT NULL,\n        completed_json TEXT NOT NULL DEFAULT '[]',\n        pending_json TEXT NOT NULL DEFAULT '[]',\n        inflight_json TEXT NOT NULL DEFAULT '[]',\n        next_action TEXT NOT NULL DEFAULT '',\n        evidence_json TEXT NOT NULL DEFAULT '[]',\n        extra_json TEXT NOT NULL DEFAULT '{{}}',\n        payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT {_TS})", 'CREATE INDEX IF NOT EXISTS topic_checkpoints_by_topic\n        ON topic_checkpoints(topic_id, id)', f"CREATE TABLE IF NOT EXISTS topic_experiments(\n        exp_id TEXT PRIMARY KEY REFERENCES experiments(id),\n        topic_id TEXT NOT NULL REFERENCES topics(id),\n        bound_session_id TEXT NOT NULL, bound_generation INTEGER NOT NULL,\n        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),\n        bound_at TEXT NOT NULL DEFAULT {_TS}, released_at TEXT, note TEXT NOT NULL DEFAULT '')", 'CREATE UNIQUE INDEX IF NOT EXISTS topic_experiments_one_active\n        ON topic_experiments(exp_id) WHERE active=1', 'CREATE INDEX IF NOT EXISTS topic_experiments_by_topic\n        ON topic_experiments(topic_id, active)', f"CREATE TABLE IF NOT EXISTS topic_worktrees(\n        path TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id),\n        bound_session_id TEXT, bound_generation INTEGER,\n        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),\n        note TEXT NOT NULL DEFAULT '', bound_at TEXT NOT NULL DEFAULT {_TS},\n        released_at TEXT)", 'CREATE UNIQUE INDEX IF NOT EXISTS topic_worktrees_one_active_path\n        ON topic_worktrees(path) WHERE active=1', 'CREATE INDEX IF NOT EXISTS topic_worktrees_by_topic\n        ON topic_worktrees(topic_id, active)', f"CREATE TABLE IF NOT EXISTS topic_branches(\n        branch TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id),\n        bound_session_id TEXT, bound_generation INTEGER,\n        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),\n        note TEXT NOT NULL DEFAULT '', bound_at TEXT NOT NULL DEFAULT {_TS},\n        released_at TEXT)", 'CREATE UNIQUE INDEX IF NOT EXISTS topic_branches_one_active\n        ON topic_branches(branch) WHERE active=1', 'CREATE INDEX IF NOT EXISTS topic_branches_by_topic\n        ON topic_branches(topic_id, active)', "CREATE TRIGGER IF NOT EXISTS topic_checkpoints_no_update\n        BEFORE UPDATE ON topic_checkpoints BEGIN\n        SELECT RAISE(ABORT, 'topic checkpoints are append-only'); END", "CREATE TRIGGER IF NOT EXISTS topic_checkpoints_no_delete\n        BEFORE DELETE ON topic_checkpoints BEGIN\n        SELECT RAISE(ABORT, 'topic checkpoints are append-only'); END", "CREATE TRIGGER IF NOT EXISTS topic_sessions_no_delete\n        BEFORE DELETE ON topic_sessions BEGIN\n        SELECT RAISE(ABORT, 'topic sessions are history'); END", "CREATE TRIGGER IF NOT EXISTS topic_session_events_no_update\n        BEFORE UPDATE ON topic_session_events BEGIN\n        SELECT RAISE(ABORT, 'topic session events are append-only'); END", "CREATE TRIGGER IF NOT EXISTS topic_session_events_no_delete\n        BEFORE DELETE ON topic_session_events BEGIN\n        SELECT RAISE(ABORT, 'topic session events are append-only'); END")
_READONLY_TABLES = ('topics', 'topic_sessions', 'topic_checkpoints', 'topic_experiments', 'topic_worktrees', 'topic_branches')

class TopicsError(RuntimeError):
    pass

class TopicNotFound(TopicsError):
    pass

class TopicConflict(TopicsError):
    pass

class VersionConflict(TopicsError):
    pass

class StaleGeneration(TopicsError):
    pass

class SelfFence(TopicsError):
    pass

class NotLeader(TopicsError):
    pass

class SessionConflict(TopicsError):
    pass

class CheckpointError(TopicsError):
    pass

class ExperimentConflict(TopicsError):
    pass

class PathConflict(TopicsError):
    pass

class BranchConflict(TopicsError):
    pass

class BindingRequired(TopicsError):
    pass

class IdempotencyConflict(TopicsError):
    pass

def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value

def _json_text(value, name):
    if not isinstance(value, (list, tuple, dict)):
        raise ValueError(f'{name} must be a JSON array or object')
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be JSON-serializable: {exc}') from exc

def _list_of_text(values, name):
    if values is None:
        raise ValueError(f'{name} must be an explicit list')
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f'{name} must be a list')
    return [_text(v, f'{name}[]') for v in values]

def _path_key(value):
    path = Path(value)
    if not path.is_absolute():
        return str(path).replace('\\', '/').casefold().rstrip('/')
    flat = str(path.resolve(strict=False)).replace('\\', '/').casefold()
    if flat.endswith('/'):
        return flat
    return flat.rstrip('/')

def _is_root_path(value):
    key = _path_key(value)
    if key == '/':
        return True
    body = key[:-1] if key.endswith('/') else key
    return len(body) == 2 and body[1] == ':'

def normalize_worktree_path(value):
    _text(value, 'absolute_path')
    path = Path(value)
    if not path.is_absolute():
        raise PathConflict('worktree path must be absolute')
    resolved = path.resolve(strict=False)
    if _is_root_path(str(resolved)):
        raise PathConflict(f'refusing a filesystem root as a worktree: {resolved}')
    return (str(resolved), _path_key(str(resolved)))

def paths_overlap(left, right):
    a, b = (_path_key(left), _path_key(right))
    if a == b:
        return True
    return _is_ancestor(a, b) or _is_ancestor(b, a)

def _is_ancestor(parent, child):
    if not parent:
        return False
    if parent.endswith('/'):
        return child.startswith(parent)
    return child.startswith(parent + '/')

def _validate_inflight(values, name='inflight'):
    if values is None:
        raise ValueError(f'{name} must be an explicit list')
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f'{name} must be a list')
    items = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError(f'{name} items must be objects')
        task = _text(item.get('task'), f'{name}[].task')
        status = item.get('status', 'open')
        if status not in INFLIGHT_STATUSES:
            raise ValueError(f'{name}[].status must be one of {sorted(INFLIGHT_STATUSES)}')
        owner = item.get('owner_session_id')
        if owner is not None:
            owner = _text(owner, f'{name}[].owner_session_id')
        evidence = item.get('evidence')
        if evidence is not None:
            _json_text(evidence, f'{name}[].evidence')
        record = {'task': task, 'status': status, 'owner_session_id': owner, 'evidence': evidence}
        for key in sorted(item):
            if key not in record:
                record[key] = item[key]
        items.append(record)
    return items

def _inflight_summary(items):
    statuses = {}
    for item in items:
        statuses[item['status']] = statuses.get(item['status'], 0) + 1
    return {'total': len(items), 'open': statuses.get('open', 0), 'released': statuses.get('released', 0), 'lost': statuses.get('lost', 0)}

class Topics:

    def __init__(self, store, *, clock=time.time, readonly=False):
        if isinstance(store, (str, bytes)) or hasattr(store, '__fspath__'):
            raw = str(store)
            if not readonly and raw not in (':memory:',):
                path = Path(raw)
                if path.parent and (not path.parent.exists()):
                    raise TopicsError('database parent directory must exist')
            store = Store(raw) if not readonly else _ReadOnlyHandle(raw)
        self.store = store
        self.clock = clock
        self.readonly = bool(readonly)
        if not self.readonly:
            with self.store.transaction() as conn:
                for statement in _SCHEMA:
                    conn.execute(statement)

    def _now_iso(self):
        with closing(self.store.connect()) as conn:
            return conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f','now') || 'Z'").fetchone()[0]

    def _begin(self):
        return self.store.transaction()

    def _audit(self, conn, topic_id, kind, actor, detail, exp_id=None):
        self.store.emit(conn, exp_id, 'topics.' + kind, actor, detail)

    def _event(self, conn, topic_id, session_id, generation, kind, actor, detail):
        conn.execute('INSERT INTO topic_session_events\n            (topic_id,session_id,generation,kind,actor,detail) VALUES(?,?,?,?,?,?)', (topic_id, session_id, generation, kind, actor, _json_text(detail, 'detail')))

    def _idempotent(self, conn, topic_id, key, payload, actor, produce):
        if key is not None:
            key = _text(key, 'idempotency_key')
            encoded = canonical_json(payload)
            row = conn.execute('SELECT * FROM store_requests WHERE request_key=?', ('topics:' + key,)).fetchone()
            if row:
                if row['payload_json'] != encoded:
                    raise IdempotencyConflict('idempotency key has a different payload')
                return (json.loads(row['result_json']), True)
        else:
            key, encoded = (None, None)
        result = produce()
        if key is not None:
            conn.execute('INSERT INTO store_requests VALUES(?,?,?)', ('topics:' + key, encoded, canonical_json(result)))
        self._audit(conn, topic_id, 'request_' + str(payload.get('op')), actor, {'op': payload.get('op'), 'key': key, 'idempotency_key': key})
        return (result, False)

    @staticmethod
    def _topic(row):
        if row is None:
            return None
        return {key: row[key] for key in _TOPIC_KEYS}

    @staticmethod
    def _session(row):
        if row is None:
            return None
        return {key: row[key] for key in _SESSION_KEYS}

    @staticmethod
    def _checkpoint(row):
        if row is None:
            return None
        inflight = json.loads(row['inflight_json'])
        return {'id': row['id'], 'topic_id': row['topic_id'], 'session_id': row['session_id'], 'generation': row['generation'], 'kind': row['kind'], 'reason': row['reason'], 'summary': row['summary'], 'completed': json.loads(row['completed_json']), 'pending': json.loads(row['pending_json']), 'inflight': inflight, 'next_action': row['next_action'], 'evidence': json.loads(row['evidence_json']), 'extra': json.loads(row['extra_json']), 'payload_sha256': row['payload_sha256'], 'created_at': row['created_at'], 'inflight_open': sum((1 for item in inflight if item.get('status') == 'open')), 'inflight_total': len(inflight)}

    @staticmethod
    def _experiment(row):
        if row is None:
            return None
        return {'exp_id': row['exp_id'], 'topic_id': row['topic_id'], 'bound_session_id': row['bound_session_id'], 'bound_generation': row['bound_generation'], 'active': bool(row['active']), 'bound_at': row['bound_at'], 'released_at': row['released_at'], 'note': row['note']}

    @staticmethod
    def _worktree(row, key='path'):
        if row is None:
            return None
        return {key: row[key], 'topic_id': row['topic_id'], 'bound_session_id': row['bound_session_id'], 'bound_generation': row['bound_generation'], 'active': bool(row['active']), 'bound_at': row['bound_at'], 'released_at': row['released_at'], 'note': row['note']}

    @staticmethod
    def _limit(limit, name='limit'):
        if limit is None:
            return None
        if type(limit) is not int or limit < 0:
            raise ValueError(f'{name} must be a non-negative integer or None')
        return limit

    @staticmethod
    def _offset(offset):
        if type(offset) is not int or offset < 0:
            raise ValueError('offset must be a non-negative integer')
        return offset

    def get_topic(self, topic_id):
        _text(topic_id, 'topic_id')
        conn = self._read_connect()
        try:
            return self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone())
        finally:
            conn.close()

    def list_topics(self, *, status=None, include_archived=False, limit=None, offset=0):
        if status is not None and status not in TOPIC_STATUSES:
            raise ValueError(f'status must be one of {sorted(TOPIC_STATUSES)}')
        limit, offset = (self._limit(limit), self._offset(offset))
        sql = 'SELECT * FROM topics'
        args, where = ([], [])
        if status is not None:
            where.append('status=?')
            args.append(status)
        elif not include_archived:
            where.append("status<>'archived'")
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY priority, created_at, id'
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args.extend([limit, offset])
        conn = self._read_connect()
        try:
            return [self._topic(row) for row in conn.execute(sql, args)]
        finally:
            conn.close()

    def _read_connect(self):
        conn = self.store.connect()
        conn.execute('PRAGMA query_only=ON')
        return conn

    @contextmanager
    def _read_snapshot(self):
        conn = self._read_connect()
        try:
            conn.execute('BEGIN')
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            conn.close()

    def _read_with(self, empty, produce):
        try:
            with self._read_snapshot() as conn:
                if not self._has_topics_table(conn):
                    return empty()
                return produce(conn)
        except sqlite3.OperationalError as exc:
            if 'no such table' in str(exc):
                return empty()
            raise

    @staticmethod
    def _has_topics_table(conn):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topics'").fetchone() is not None

    def _board_rows(self, topic_limit, topic_offset, checkpoint_limit, include_archived):
        return self._read_with(lambda: None, lambda conn: self._overview(conn, topic_limit, topic_offset, checkpoint_limit, include_archived))

    def _rows(self, sql, args=(), table=None):
        conn = self._read_connect()
        try:
            return list(conn.execute(sql, args))
        except sqlite3.OperationalError as exc:
            if table and 'no such table' in str(exc):
                return []
            raise
        finally:
            conn.close()

    def _require_topic(self, conn, topic_id):
        row = conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone()
        if row is None:
            raise TopicNotFound(f'topic {topic_id} does not exist')
        return row

    def _leader_row(self, conn, topic_id):
        return conn.execute("SELECT * FROM topic_sessions WHERE topic_id=? AND status='open'", (topic_id,)).fetchone()

    def leader(self, topic_id):
        _text(topic_id, 'topic_id')
        rows = self._rows("SELECT * FROM topic_sessions WHERE topic_id=? AND status='open'", (topic_id,), table='topic_sessions')
        return self._session(rows[0]) if rows else None

    def list_sessions(self, topic_id, *, limit=None, offset=0):
        _text(topic_id, 'topic_id')
        limit, offset = (self._limit(limit), self._offset(offset))
        sql = 'SELECT * FROM topic_sessions WHERE topic_id=? ORDER BY generation'
        args = [topic_id]
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args.extend([limit, offset])
        return [self._session(row) for row in self._rows(sql, args, table='topic_sessions')]

    def check_binding(self, topic_id):
        topic = self.get_topic(topic_id)
        if topic is None:
            raise TopicNotFound(f'topic {topic_id} does not exist')
        return {'topic_id': topic_id, 'worktree': topic['worktree'], 'branch': topic['branch'], 'ok': bool(topic['worktree']) and bool(topic['branch'])}

    def assert_ready(self, topic_id):
        _text(topic_id, 'topic_id')
        topic = self.get_topic(topic_id)
        if topic is None:
            raise TopicNotFound(f'topic {topic_id} does not exist')
        missing = []
        if not topic['worktree']:
            missing.append('worktree')
        if not topic['branch']:
            missing.append('branch')
        if missing:
            raise BindingRequired('topic lacks binding before work can start: ' + ','.join(missing))
        return {'topic_id': topic_id, 'worktree': topic['worktree'], 'branch': topic['branch'], 'ok': True}

    def create_topic(self, topic_id, *, title, objective='', completion_criteria='', priority=2, actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(title, 'title')
        if not isinstance(objective, str) or not isinstance(completion_criteria, str):
            raise ValueError('objective and completion_criteria must be text')
        if type(priority) is not int or not 0 <= priority <= 3:
            raise ValueError('priority must be an integer in 0..3')
        _text(actor, 'actor')
        payload = {'op': 'create_topic', 'topic_id': topic_id, 'title': title, 'objective': objective, 'completion_criteria': completion_criteria, 'priority': priority, 'actor': actor}
        with self._begin() as conn:

            def produce():
                if conn.execute('SELECT 1 FROM topics WHERE id=?', (topic_id,)).fetchone():
                    raise TopicConflict(f'topic {topic_id} already exists')
                conn.execute("INSERT INTO topics\n                    (id,title,objective,completion_criteria,priority,status,version,\n                     generation,created_at,updated_at,updated_by)\n                    VALUES(?,?,?,?,?,'active',0,0,%s,%s,?)" % (_TS, _TS), (topic_id, title, objective, completion_criteria, priority, actor))
                self._audit(conn, topic_id, 'created', actor, {'topic_id': topic_id, 'title': title})
                return self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone())
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def set_topic_fields(self, topic_id, expected_version, *, title=None, objective=None, completion_criteria=None, priority=None, actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(actor, 'actor')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a non-negative integer')
        changes = {}
        if title is not None:
            changes['title'] = _text(title, 'title')
        if objective is not None:
            if not isinstance(objective, str):
                raise ValueError('objective must be text')
            changes['objective'] = objective
        if completion_criteria is not None:
            if not isinstance(completion_criteria, str):
                raise ValueError('completion_criteria must be text')
            changes['completion_criteria'] = completion_criteria
        if priority is not None:
            if type(priority) is not int or not 0 <= priority <= 3:
                raise ValueError('priority must be an integer in 0..3')
            changes['priority'] = priority
        if not changes:
            raise ValueError('no editable field was provided')
        payload = {'op': 'set_topic_fields', 'topic_id': topic_id, 'expected_version': expected_version, 'changes': changes, 'actor': actor}
        with self._begin() as conn:

            def produce():
                row = self._require_topic(conn, topic_id)
                if row['version'] != expected_version:
                    raise VersionConflict(f"topic version is {row['version']}, expected {expected_version}")
                assignments = ','.join((f'{name}=?' for name in sorted(changes)))
                conn.execute(f'UPDATE topics SET {assignments},version=version+1,updated_at=' + _TS + ',updated_by=? WHERE id=? AND version=?', (*[changes[name] for name in sorted(changes)], actor, topic_id, expected_version))
                self._audit(conn, topic_id, 'fields_set', actor, {'changes': changes, 'expected_version': expected_version})
                return self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone())
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def set_topic_status(self, topic_id, status, expected_version, *, session_id, generation, actor='main', reason, idempotency_key=None):
        if status not in TOPIC_STATUSES:
            raise ValueError(f'status must be one of {sorted(TOPIC_STATUSES)}')
        _text(reason, 'reason')
        _text(actor, 'actor')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a non-negative integer')
        if status in ('completed', 'archived'):
            raise TopicConflict(f'use finalize_topic() to move a released topic to {status}; runtime status changes stay with the live owner')
        payload = {'op': 'set_topic_status', 'topic_id': topic_id, 'status': status, 'expected_version': expected_version, 'session_id': session_id, 'generation': generation, 'actor': actor, 'reason': reason}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                row = self._require_topic(conn, topic_id)
                if row['version'] != expected_version:
                    raise VersionConflict(f"topic version is {row['version']}, expected {expected_version}")
                conn.execute('UPDATE topics SET status=?,version=version+1,updated_at=' + _TS + ',updated_by=? WHERE id=? AND version=?', (status, actor, topic_id, expected_version))
                self._audit(conn, topic_id, 'status_set', actor, {'status': status, 'reason': reason, 'session_id': session_id, 'generation': generation})
                return self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone())
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def finalize_topic(self, topic_id, status, expected_version, *, reason, actor='main', idempotency_key=None):
        if status != 'archived':
            raise TopicConflict('finalize_topic only archives; use complete_topic() to complete a topic' if status == 'completed' else f"finalize_topic status must be 'archived', not {status!r}")
        _text(reason, 'reason')
        _text(actor, 'actor')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a non-negative integer')
        payload = {'op': 'finalize_topic', 'topic_id': topic_id, 'status': status, 'expected_version': expected_version, 'actor': actor, 'reason': reason}
        with self._begin() as conn:

            def produce():
                row = self._require_topic(conn, topic_id)
                if row['version'] != expected_version:
                    raise VersionConflict(f"topic version is {row['version']}, expected {expected_version}")
                if row['status'] == 'archived':
                    raise TopicConflict('topic is already archived')
                if self._leader_row(conn, topic_id) is not None:
                    raise SessionConflict('release the live session before archiving the topic')
                open_items, lost_items = self._open_inflight(conn, topic_id)
                if open_items or lost_items:
                    raise CheckpointError(f'latest checkpoint still carries open={open_items} lost={lost_items} inflight tasks; record their resolution in a checkpoint before archiving the topic')
                conn.execute('UPDATE topics SET status=?,version=version+1,updated_at=' + _TS + ',updated_by=? WHERE id=? AND version=?', (status, actor, topic_id, expected_version))
                self._audit(conn, topic_id, 'finalized', actor, {'status': status, 'reason': reason})
                return self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone())
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def complete_topic(self, topic_id, session_id, generation, *, checkpoint, actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(session_id, 'session_id')
        _text(actor, 'actor')
        if checkpoint is None:
            raise CheckpointError('complete_topic requires a structured checkpoint')
        spec = dict(checkpoint)
        allowed = {'summary', 'completed', 'pending', 'inflight', 'next_action', 'evidence', 'extra'}
        unknown = sorted(set(spec) - allowed)
        if unknown:
            raise ValueError('checkpoint has unknown fields: ' + ','.join(unknown))
        if 'summary' not in spec:
            raise CheckpointError('complete_topic checkpoint is missing summary')
        payload = {'op': 'complete_topic', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'checkpoint': spec, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                carried = self._progress_from(conn, topic_id)
                fields = {}
                for name, default in (('completed', []), ('pending', []), ('next_action', ''), ('evidence', []), ('extra', {})):
                    if name in spec:
                        fields[name] = spec[name]
                    elif carried['id'] is not None:
                        fields[name] = carried[name]
                    else:
                        fields[name] = default
                fields['inflight'] = spec['inflight'] if 'inflight' in spec else carried['inflight']
                pending_items = _list_of_text(fields['pending'], 'pending')
                inflight_items = _validate_inflight(fields['inflight'])
                evidence_items = _list_of_text(fields['evidence'], 'evidence')
                _list_of_text(fields['completed'], 'completed')
                if not isinstance(fields['next_action'], str):
                    raise ValueError('next_action must be text')
                open_items = [i['task'] for i in inflight_items if i['status'] == 'open']
                lost_items = [i['task'] for i in inflight_items if i['status'] == 'lost']
                if pending_items:
                    raise CheckpointError(f'pending is not empty: {pending_items}; a topic only closes when nothing is left pending')
                if open_items or lost_items:
                    raise CheckpointError(f'inflight still carries open={open_items} lost={lost_items}; release or explicitly resolve them before completing')
                if fields['next_action']:
                    raise CheckpointError('next_action must be empty on completion: ' + fields['next_action'])
                if not evidence_items:
                    raise CheckpointError('completion needs at least one evidence reference')
                row = self._write_checkpoint(conn, topic_id, session_id, generation, summary=spec['summary'], completed=fields['completed'], pending=[], inflight=inflight_items, next_action='', evidence=evidence_items, kind='close', reason='completed', extra={**fields['extra'], 'carried_from_checkpoint_id': carried['id'], 'completion_declaration': True})
                closed = self._end_live(conn, topic_id, 'closed', 'completed')
                conn.execute("UPDATE topics SET status='completed',leader_session_id=NULL,\n                    leader_generation=NULL,leader_since=NULL,version=version+1,\n                    updated_at=" + _TS + ',updated_by=? WHERE id=?', (actor, topic_id))
                self._audit(conn, topic_id, 'completed', actor, {'session_id': session_id, 'generation': generation, 'checkpoint_id': row['id']})
                return {'topic': self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone()), 'closed_session': self._session(closed), 'checkpoint': self._checkpoint(row), 'carried_from_checkpoint_id': carried['id']}
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def reopen_topic(self, topic_id, expected_version, *, reason, actor='main', idempotency_key=None):
        _text(reason, 'reason')
        _text(actor, 'actor')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a non-negative integer')
        payload = {'op': 'reopen_topic', 'topic_id': topic_id, 'expected_version': expected_version, 'actor': actor, 'reason': reason}
        with self._begin() as conn:

            def produce():
                row = self._require_topic(conn, topic_id)
                if row['version'] != expected_version:
                    raise VersionConflict(f"topic version is {row['version']}, expected {expected_version}")
                if row['status'] != 'completed':
                    raise TopicConflict(f"reopen_topic only reopens completed topics; status is {row['status']!r}")
                open_items, lost_items = self._open_inflight(conn, topic_id)
                if open_items or lost_items:
                    raise TopicConflict(f'reopen refuses to hide unfinished work: {open_items} open / {lost_items} lost inflight item(s)')
                conn.execute("UPDATE topics SET status='active',version=version+1,updated_at=" + _TS + ',updated_by=? WHERE id=? AND version=?', (actor, topic_id, expected_version))
                self._audit(conn, topic_id, 'reopened', actor, {'status': 'active', 'reason': reason})
                return self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone())
            return self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)

    def _open_inflight(self, conn, topic_id):
        row = conn.execute('SELECT * FROM topic_checkpoints WHERE topic_id=? ORDER BY id DESC LIMIT 1', (topic_id,)).fetchone()
        if row is None:
            return (0, 0)
        items = json.loads(row['inflight_json'])
        open_items = sum((1 for item in items if item.get('status') == 'open'))
        lost_items = sum((1 for i in items if i.get('status') == 'lost'))
        return (open_items, lost_items)

    def _next_generation(self, conn, topic_id):
        row = conn.execute('SELECT MAX(generation) AS g FROM topic_sessions WHERE topic_id=?', (topic_id,)).fetchone()
        return (row['g'] or 0) + 1

    def _live_session(self, conn, topic_id):
        return conn.execute("SELECT * FROM topic_sessions WHERE topic_id=? AND status='open'", (topic_id,)).fetchone()

    def _check_generation(self, conn, topic_id, expected_generation):
        if expected_generation is None:
            return
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError('expected_generation must be a non-negative integer or None')
        row = self._require_topic(conn, topic_id)
        if row['generation'] != expected_generation:
            raise StaleGeneration(f"topic generation is {row['generation']}, expected {expected_generation}")

    def _end_live(self, conn, topic_id, status, reason):
        live = self._live_session(conn, topic_id)
        if live is None:
            return None
        generation = live['generation']
        conn.execute('UPDATE topic_sessions SET status=?,ended_at=%s,ended_reason=?\n            WHERE topic_id=? AND generation=?' % _TS, (status, reason, topic_id, generation))
        self._event(conn, topic_id, live['session_id'], generation, 'session_ended', live['actor'], {'status': status, 'reason': reason})
        return conn.execute('SELECT * FROM topic_sessions WHERE topic_id=? AND generation=?', (topic_id, generation)).fetchone()

    def _open(self, conn, topic_id, session_id, note, actor, reason, takeover_reason=None):
        topic = self._require_topic(conn, topic_id)
        if topic['status'] in ('completed', 'archived'):
            raise TopicConflict(f"topic status is {topic['status']} and cannot start work")
        if topic['status'] == 'paused':
            raise TopicConflict('topic is paused: no new session may start; hand the live session off instead')
        missing = [name for name in ('worktree', 'branch') if not topic[name]]
        if missing:
            raise BindingRequired('topic lacks binding before work can start: ' + ','.join(missing))
        live = self._live_session(conn, topic_id)
        if live is not None and live['session_id'] == session_id:
            raise SelfFence(f"session {session_id} already holds generation {live['generation']}")
        if live is not None:
            if takeover_reason is None:
                raise SessionConflict(f"topic already has a live owner session {live['session_id']}; only recover() may take over, and it needs an explicit reason and a verified inflight list")
            self._end_live(conn, topic_id, 'crashed', 'recovery: ' + takeover_reason)
        predecessor = self._latest_session(conn, topic_id)
        generation = self._next_generation(conn, topic_id)
        conn.execute("INSERT INTO topic_sessions\n            (topic_id,session_id,generation,predecessor_session_id,predecessor_generation,\n             status,opened_at,note,actor) VALUES(?,?,?,?,?,'open',%s,?,?)" % _TS, (topic_id, session_id, generation, predecessor['session_id'] if predecessor else None, predecessor['generation'] if predecessor else None, note, actor))
        conn.execute('UPDATE topics SET generation=?,leader_session_id=?,leader_generation=?,\n            leader_since=%s,version=version+1,updated_at=%s,updated_by=? WHERE id=?' % (_TS, _TS), (generation, session_id, generation, actor, topic_id))
        self._event(conn, topic_id, session_id, generation, 'session_opened', actor, {'reason': reason, 'note': note, 'predecessor_session_id': predecessor['session_id'] if predecessor else None, 'predecessor_generation': predecessor['generation'] if predecessor else None})
        self._audit(conn, topic_id, 'session_opened', actor, {'session_id': session_id, 'generation': generation, 'reason': reason})
        return conn.execute('SELECT * FROM topic_sessions WHERE topic_id=? AND generation=?', (topic_id, generation)).fetchone()

    def _latest_session(self, conn, topic_id):
        return conn.execute('SELECT * FROM topic_sessions WHERE topic_id=?\n            ORDER BY generation DESC LIMIT 1', (topic_id,)).fetchone()

    def open_session(self, topic_id, session_id, *, expected_generation=None, note='', actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(session_id, 'session_id')
        _text(actor, 'actor')
        if not isinstance(note, str):
            raise ValueError('note must be text')
        if expected_generation is None:
            raise ValueError('open_session requires expected_generation (0 for the first term)')
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError('expected_generation must be a non-negative integer')
        payload = {'op': 'open_session', 'topic_id': topic_id, 'session_id': session_id, 'expected_generation': expected_generation, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._check_generation(conn, topic_id, expected_generation)
                row = self._open(conn, topic_id, session_id, note, actor, note)
                return self._session(row)
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def _write_checkpoint(self, conn, topic_id, session_id, generation, *, summary, completed, pending, inflight, next_action, evidence, kind, reason, extra):
        _text(summary, 'summary')
        completed = _list_of_text(completed, 'completed')
        pending = _list_of_text(pending, 'pending')
        inflight = _validate_inflight(inflight)
        if not isinstance(next_action, str):
            raise ValueError('next_action must be text')
        evidence = _list_of_text(evidence, 'evidence')
        if extra is None:
            extra = {}
        if not isinstance(extra, dict):
            raise ValueError('extra must be a JSON object')
        if kind not in CHECKPOINT_KINDS:
            raise ValueError(f'kind must be one of {sorted(CHECKPOINT_KINDS)}')
        if reason is not None:
            reason = _text(reason, 'reason')
        payload = {'summary': summary, 'completed': completed, 'pending': pending, 'inflight': inflight, 'next_action': next_action, 'evidence': evidence, 'extra': extra}
        digest = hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
        cursor = conn.execute('INSERT INTO topic_checkpoints\n            (topic_id,session_id,generation,kind,reason,summary,completed_json,pending_json,\n             inflight_json,next_action,evidence_json,extra_json,payload_sha256)\n            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (topic_id, session_id, generation, kind, reason, summary, _json_text(completed, 'completed'), _json_text(pending, 'pending'), _json_text(inflight, 'inflight'), next_action, _json_text(evidence, 'evidence'), _json_text(extra, 'extra'), digest))
        self._event(conn, topic_id, session_id, generation, 'checkpoint', session_id, {'id': cursor.lastrowid, 'kind': kind, 'sha256': digest})
        self._audit(conn, topic_id, 'checkpoint_written', session_id, {'checkpoint_id': cursor.lastrowid, 'kind': kind, 'sha256': digest})
        return conn.execute('SELECT * FROM topic_checkpoints WHERE id=?', (cursor.lastrowid,)).fetchone()

    def _progress_from(self, conn, topic_id):
        row = conn.execute('SELECT * FROM topic_checkpoints WHERE topic_id=? ORDER BY id DESC LIMIT 1', (topic_id,)).fetchone()
        if row is None:
            return {'id': None, 'session_id': None, 'generation': None, 'kind': None, 'summary': None, 'completed': [], 'pending': [], 'inflight': [], 'next_action': '', 'evidence': [], 'extra': {}}
        return {'id': row['id'], 'session_id': row['session_id'], 'generation': row['generation'], 'kind': row['kind'], 'summary': row['summary'], 'completed': json.loads(row['completed_json']), 'pending': json.loads(row['pending_json']), 'inflight': json.loads(row['inflight_json']), 'next_action': row['next_action'], 'evidence': json.loads(row['evidence_json']), 'extra': json.loads(row['extra_json'])}

    def _require_leader(self, conn, topic_id, session_id, generation):
        self._require_topic(conn, topic_id)
        live = self._live_session(conn, topic_id)
        if live is None:
            raise NotLeader('topic has no live owner session')
        if live['session_id'] != session_id:
            raise NotLeader(f"live owner is {live['session_id']}, not {session_id}")
        if type(generation) is not int or generation < 0:
            raise ValueError('generation must be a non-negative integer')
        if live['generation'] != generation:
            raise StaleGeneration(f"live generation is {live['generation']}, caller passed {generation}")
        return live

    def write_checkpoint(self, topic_id, session_id, generation, *, summary, completed=(), pending=(), inflight=(), next_action='', evidence=(), kind='node', extra=None, actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(session_id, 'session_id')
        _text(actor, 'actor')
        payload = {'op': 'write_checkpoint', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'summary': summary, 'completed': list(completed), 'pending': list(pending), 'inflight': list(inflight), 'next_action': next_action, 'evidence': list(evidence), 'kind': kind, 'extra': extra or {}, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                row = self._write_checkpoint(conn, topic_id, session_id, generation, summary=summary, completed=completed, pending=pending, inflight=inflight, next_action=next_action, evidence=evidence, kind=kind, reason=None, extra=extra)
                return self._checkpoint(row)
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def handoff(self, topic_id, session_id, generation, *, next_session_id=None, checkpoint=None, note='', actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(session_id, 'session_id')
        _text(actor, 'actor')
        if next_session_id is not None:
            _text(next_session_id, 'next_session_id')
            if next_session_id == session_id:
                raise SessionConflict('handoff target must be a different session')
        if not isinstance(note, str):
            raise ValueError('note must be text')
        if checkpoint is None:
            raise CheckpointError('handoff requires a structured checkpoint')
        spec = dict(checkpoint)
        allowed = {'summary', 'completed', 'pending', 'inflight', 'next_action', 'evidence', 'kind', 'extra'}
        unknown = sorted(set(spec) - allowed)
        if unknown:
            raise ValueError('checkpoint has unknown fields: ' + ','.join(unknown))
        for required in ('summary', 'inflight'):
            if required not in spec:
                raise CheckpointError(f'handoff checkpoint is missing {required}')
        payload = {'op': 'handoff', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'next_session_id': next_session_id, 'checkpoint': spec, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                kind = spec.get('kind', 'handoff')
                if kind not in ('handoff', 'final'):
                    raise CheckpointError('handoff checkpoint kind must be handoff or final')
                carried = self._progress_from(conn, topic_id)
                fields = {}
                for name, default in (('completed', []), ('pending', []), ('next_action', ''), ('evidence', []), ('extra', {})):
                    if name in spec:
                        fields[name] = spec[name]
                    elif carried['id'] is not None:
                        fields[name] = carried[name]
                    else:
                        fields[name] = default
                if 'inflight' in spec:
                    fields['inflight'] = spec['inflight']
                else:
                    fields['inflight'] = carried['inflight']
                row = self._write_checkpoint(conn, topic_id, session_id, generation, summary=spec['summary'], completed=fields['completed'], pending=fields['pending'], inflight=fields['inflight'], next_action=fields['next_action'], evidence=fields['evidence'], kind=kind, reason=None, extra={**fields['extra'], 'carried_from_checkpoint_id': carried['id'], 'handoff_to_session_id': next_session_id})
                closed = self._end_live(conn, topic_id, 'handed_off', 'handoff')
                if next_session_id is None:
                    conn.execute('UPDATE topics SET leader_session_id=NULL,\n                        leader_generation=NULL,leader_since=NULL,version=version+1,\n                        updated_at=' + _TS + ',updated_by=? WHERE id=?', (actor, topic_id))
                    return {'closed': self._session(closed), 'opened': None, 'checkpoint_id': row['id'], 'generation': closed['generation'], 'released': True, 'carried_from_checkpoint_id': carried['id']}
                opened = self._open(conn, topic_id, next_session_id, note, actor, 'handoff from ' + session_id)
                return {'closed': self._session(closed), 'opened': self._session(opened), 'checkpoint_id': row['id'], 'generation': opened['generation'], 'released': False, 'carried_from_checkpoint_id': carried['id']}
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def recover(self, topic_id, session_id, *, expected_generation, reason, inflight, note='', actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(session_id, 'session_id')
        _text(reason, 'reason')
        _text(actor, 'actor')
        if not isinstance(note, str):
            raise ValueError('note must be text')
        if expected_generation is None:
            raise ValueError('recover requires expected_generation')
        validated = _validate_inflight(inflight)
        payload = {'op': 'recover', 'topic_id': topic_id, 'session_id': session_id, 'expected_generation': expected_generation, 'reason': reason, 'inflight': validated, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._check_generation(conn, topic_id, expected_generation)
                carried = self._progress_from(conn, topic_id)
                opened = self._open(conn, topic_id, session_id, note, actor, 'recovery: ' + reason, takeover_reason=reason)
                if carried['id'] is None:
                    summary = f'crash takeover: {reason}（本专题此前没有 checkpoint，无进展可接续）'
                else:
                    summary = f"接续上一条进展（checkpoint {carried['id']} · {carried['kind']} · {carried['session_id']} gen {carried['generation']}）：{carried['summary']}；本次 crash takeover 原因：{reason}"
                checkpoint = self._write_checkpoint(conn, topic_id, session_id, opened['generation'], summary=summary, completed=carried['completed'], pending=carried['pending'], inflight=validated, next_action=carried['next_action'] or note, evidence=carried['evidence'], kind='recovery', reason=reason, extra={**carried['extra'], 'reason': reason, 'inflight': validated, 'previous_checkpoint_id': carried['id'], 'takeover_from_generation': expected_generation, 'takeover_from_session_id': carried['session_id']})
                result = self._session(opened)
                result['checkpoint_id'] = checkpoint['id']
                result['reason'] = reason
                result['inflight'] = _inflight_summary(validated)
                result['carried_from_checkpoint_id'] = carried['id']
                return result
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def close_session(self, topic_id, session_id, generation, *, status='closed', reason, actor='main', idempotency_key=None):
        _text(reason, 'reason')
        if status not in ('closed', 'handed_off', 'crashed'):
            raise ValueError("status must be one of ['closed', 'handed_off', 'crashed']")
        payload = {'op': 'close_session', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'status': status, 'reason': reason, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                closed = self._end_live(conn, topic_id, status, reason)
                conn.execute('UPDATE topics SET leader_session_id=NULL,leader_generation=NULL,\n                    leader_since=NULL,version=version+1,updated_at=%s,updated_by=?\n                    WHERE id=?' % _TS, (actor, topic_id))
                return self._session(closed)
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def link_experiment(self, topic_id, session_id, generation, exp_id, *, note='', actor='main', idempotency_key=None):
        _text(exp_id, 'exp_id')
        payload = {'op': 'link_experiment', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'exp_id': exp_id, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                if not conn.execute('SELECT 1 FROM experiments WHERE id=?', (exp_id,)).fetchone():
                    raise ExperimentConflict(f'experiment {exp_id} is not registered in the store')
                row = conn.execute('SELECT * FROM topic_experiments WHERE exp_id=?', (exp_id,)).fetchone()
                if row is not None and row['topic_id'] != topic_id:
                    raise ExperimentConflict(f"experiment {exp_id} already belongs to topic {row['topic_id']}; a first binding never moves between topics")
                if row is None:
                    conn.execute('INSERT INTO topic_experiments\n                        (exp_id,topic_id,bound_session_id,bound_generation,note)\n                        VALUES(?,?,?,?,?)', (exp_id, topic_id, session_id, generation, note))
                elif not row['active']:
                    conn.execute('UPDATE topic_experiments SET active=1,\n                        bound_session_id=?,bound_generation=?,released_at=NULL,note=?,\n                        bound_at=' + _TS + ' WHERE exp_id=?', (session_id, generation, note, exp_id))
                self._audit(conn, topic_id, 'experiment_linked', actor, {'exp_id': exp_id, 'session_id': session_id, 'generation': generation})
                linked = self._experiment(conn.execute('SELECT * FROM topic_experiments WHERE exp_id=?', (exp_id,)).fetchone())
                if linked is None:
                    raise ExperimentConflict(f'experiment {exp_id} link did not persist')
                return linked
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def register_experiment(self, topic_id, session_id, generation, exp_id, *, goal='', note='', actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(session_id, 'session_id')
        _text(exp_id, 'exp_id')
        _text(actor, 'actor')
        if not isinstance(goal, str) or not isinstance(note, str):
            raise ValueError('goal and note must be text')
        payload = {'op': 'register_experiment', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'exp_id': exp_id, 'goal': goal, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                row = conn.execute('SELECT * FROM topic_experiments WHERE exp_id=?', (exp_id,)).fetchone()
                if row is not None and row['topic_id'] != topic_id:
                    raise ExperimentConflict(f"experiment {exp_id} already belongs to topic {row['topic_id']}; a first binding never moves between topics")
                if not conn.execute('SELECT 1 FROM experiments WHERE id=?', (exp_id,)).fetchone():
                    conn.execute("INSERT INTO experiments\n                        (id,goal,state,version,created_at,updated_at)\n                        VALUES(?,?,'planned',0,?,?)", (exp_id, goal, self.clock(), self.clock()))
                    self.store.emit(conn, exp_id, 'experiment_created', actor, {'goal': goal, 'via': 'topics.register_experiment'})
                if row is None:
                    conn.execute('INSERT INTO topic_experiments\n                        (exp_id,topic_id,bound_session_id,bound_generation,note)\n                        VALUES(?,?,?,?,?)', (exp_id, topic_id, session_id, generation, note))
                elif not row['active']:
                    conn.execute('UPDATE topic_experiments SET active=1,\n                        bound_session_id=?,bound_generation=?,released_at=NULL,note=?,\n                        bound_at=' + _TS + ' WHERE exp_id=?', (session_id, generation, note, exp_id))
                self._audit(conn, topic_id, 'experiment_registered', actor, {'exp_id': exp_id, 'session_id': session_id, 'generation': generation})
                linked = self._experiment(conn.execute('SELECT * FROM topic_experiments WHERE exp_id=?', (exp_id,)).fetchone())
                registered = dict(conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone())
                return {'registered': registered, 'linked': linked, 'exp_id': exp_id, 'topic_id': topic_id}
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def unlink_experiment(self, topic_id, session_id, generation, exp_id, *, reason, actor='main', idempotency_key=None):
        _text(reason, 'reason')
        payload = {'op': 'unlink_experiment', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'exp_id': exp_id, 'reason': reason, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                row = conn.execute('SELECT * FROM topic_experiments WHERE exp_id=?', (exp_id,)).fetchone()
                if row is None:
                    raise ExperimentConflict(f'experiment {exp_id} is not linked to any topic')
                if row['topic_id'] != topic_id:
                    raise ExperimentConflict(f"experiment {exp_id} belongs to topic {row['topic_id']}")
                if not row['active']:
                    return self._experiment(row)
                conn.execute('UPDATE topic_experiments SET active=0,released_at=' + _TS + ',note=? WHERE exp_id=?', (reason, exp_id))
                self._audit(conn, topic_id, 'experiment_unlinked', actor, {'exp_id': exp_id, 'reason': reason})
                return self._experiment(conn.execute('SELECT * FROM topic_experiments WHERE exp_id=?', (exp_id,)).fetchone())
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def list_experiments(self, topic_id=None, *, active_only=False, limit=None, offset=0):
        limit, offset = (self._limit(limit), self._offset(offset))
        sql, args = ('SELECT * FROM topic_experiments', [])
        where = []
        if topic_id is not None:
            _text(topic_id, 'topic_id')
            where.append('topic_id=?')
            args.append(topic_id)
        if active_only:
            where.append('active=1')
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY topic_id,exp_id'
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args.extend([limit, offset])
        return [self._experiment(row) for row in self._rows(sql, args, table='topic_experiments')]

    def _active_paths(self, conn, exclude_topic=None):
        sql = 'SELECT path,topic_id FROM topic_worktrees WHERE active=1'
        args = []
        if exclude_topic is not None:
            sql += ' AND topic_id<>?'
            args.append(exclude_topic)
        return [(row['path'], row['topic_id']) for row in conn.execute(sql, args)]

    def _apply_worktree(self, conn, topic_id, session_id, generation, display, note, actor):
        for other, owner in self._active_paths(conn, exclude_topic=topic_id):
            if paths_overlap(display, other):
                raise PathConflict(f'path {display} overlaps topic {owner} path {other}')
        row = conn.execute('SELECT * FROM topic_worktrees WHERE path=?', (display,)).fetchone()
        if row is not None and row['active'] and (row['topic_id'] == topic_id):
            return (row, False)
        conn.execute('UPDATE topic_worktrees SET active=0,released_at=' + _TS + ' WHERE topic_id=? AND active=1', (topic_id,))
        if row is None:
            conn.execute('INSERT INTO topic_worktrees\n                (path,topic_id,bound_session_id,bound_generation,note)\n                VALUES(?,?,?,?,?)', (display, topic_id, session_id, generation, note))
        else:
            conn.execute('UPDATE topic_worktrees SET topic_id=?,active=1,\n                bound_session_id=?,bound_generation=?,released_at=NULL,note=?,\n                bound_at=' + _TS + ' WHERE path=?', (topic_id, session_id, generation, note, display))
        self._audit(conn, topic_id, 'worktree_bound', actor, {'path': display, 'session_id': session_id, 'generation': generation})
        return (conn.execute('SELECT * FROM topic_worktrees WHERE path=?', (display,)).fetchone(), True)

    def _apply_branch(self, conn, topic_id, session_id, generation, branch, note, actor):
        row = conn.execute('SELECT * FROM topic_branches WHERE branch=?', (branch,)).fetchone()
        if row is not None and row['active'] and (row['topic_id'] != topic_id):
            raise BranchConflict(f"branch {branch} is not reusable: it belongs to topic {row['topic_id']}")
        if row is not None and row['active'] and (row['topic_id'] == topic_id):
            return (row, False)
        conn.execute('UPDATE topic_branches SET active=0,released_at=' + _TS + ' WHERE topic_id=? AND active=1', (topic_id,))
        if row is None:
            conn.execute('INSERT INTO topic_branches\n                (branch,topic_id,bound_session_id,bound_generation,note)\n                VALUES(?,?,?,?,?)', (branch, topic_id, session_id, generation, note))
        else:
            conn.execute('UPDATE topic_branches SET topic_id=?,active=1,\n                bound_session_id=?,bound_generation=?,released_at=NULL,note=?,\n                bound_at=' + _TS + ' WHERE branch=?', (topic_id, session_id, generation, note, branch))
        self._audit(conn, topic_id, 'branch_bound', actor, {'branch': branch, 'session_id': session_id, 'generation': generation})
        return (conn.execute('SELECT * FROM topic_branches WHERE branch=?', (branch,)).fetchone(), True)

    def configure_workspace(self, topic_id, expected_version, *, absolute_path, branch, worktree_note='', branch_note='', actor='main', idempotency_key=None):
        _text(topic_id, 'topic_id')
        _text(actor, 'actor')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a non-negative integer')
        display, _ = normalize_worktree_path(absolute_path)
        _text(branch, 'branch')
        if not isinstance(branch_note, str) or not isinstance(worktree_note, str):
            raise ValueError('notes must be text')
        payload = {'op': 'configure_workspace', 'topic_id': topic_id, 'expected_version': expected_version, 'path': display, 'branch': branch, 'actor': actor}
        with self._begin() as conn:

            def produce():
                row = self._require_topic(conn, topic_id)
                if row['version'] != expected_version:
                    raise VersionConflict(f"topic version is {row['version']}, expected {expected_version}")
                if self._live_session(conn, topic_id) is not None:
                    raise SessionConflict('configure_workspace only runs while no session holds the topic; use bind_worktree/bind_branch as the live owner')
                self._apply_worktree(conn, topic_id, '(bootstrap)', None, display, worktree_note, actor)
                self._apply_branch(conn, topic_id, '(bootstrap)', None, branch, branch_note, actor)
                conn.execute('UPDATE topics SET worktree=?,branch=?,version=version+1,\n                    updated_at=' + _TS + ',updated_by=? WHERE id=? AND version=?', (display, branch, actor, topic_id, expected_version))
                topic = self._topic(conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone())
                return {'topic': topic, 'worktree': self._worktree(conn.execute('SELECT * FROM topic_worktrees WHERE path=?', (display,)).fetchone()), 'branch': self._worktree(conn.execute('SELECT * FROM topic_branches WHERE branch=?', (branch,)).fetchone(), key='branch')}
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def bind_worktree(self, topic_id, session_id, generation, absolute_path, *, note='', actor='main', idempotency_key=None):
        display, _ = normalize_worktree_path(absolute_path)
        payload = {'op': 'bind_worktree', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'path': display, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                row, changed = self._apply_worktree(conn, topic_id, session_id, generation, display, note, actor)
                if changed:
                    conn.execute('UPDATE topics SET worktree=?,version=version+1,\n                        updated_at=' + _TS + ',updated_by=? WHERE id=?', (display, actor, topic_id))
                return self._worktree(row)
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def bind_branch(self, topic_id, session_id, generation, branch, *, note='', actor='main', idempotency_key=None):
        _text(branch, 'branch')
        payload = {'op': 'bind_branch', 'topic_id': topic_id, 'session_id': session_id, 'generation': generation, 'branch': branch, 'actor': actor}
        with self._begin() as conn:

            def produce():
                self._require_leader(conn, topic_id, session_id, generation)
                row, changed = self._apply_branch(conn, topic_id, session_id, generation, branch, note, actor)
                if changed:
                    conn.execute('UPDATE topics SET branch=?,version=version+1,\n                        updated_at=' + _TS + ',updated_by=? WHERE id=?', (branch, actor, topic_id))
                return self._worktree(row, key='branch')
            result, _ = self._idempotent(conn, topic_id, idempotency_key, payload, actor, produce)
            return result

    def list_worktrees(self, topic_id=None, *, active_only=False, limit=None, offset=0):
        return self._list_bindings('topic_worktrees', 'path', topic_id, active_only=active_only, limit=limit, offset=offset)

    def list_branches(self, topic_id=None, *, active_only=False, limit=None, offset=0):
        return self._list_bindings('topic_branches', 'branch', topic_id, active_only=active_only, limit=limit, offset=offset)

    def _list_bindings(self, table, key, topic_id, *, active_only, limit, offset):
        limit, offset = (self._limit(limit), self._offset(offset))
        sql, args, where = (f'SELECT * FROM {table}', [], [])
        if topic_id is not None:
            _text(topic_id, 'topic_id')
            where.append('topic_id=?')
            args.append(topic_id)
        if active_only:
            where.append('active=1')
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += f' ORDER BY topic_id,{key}'
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args.extend([limit, offset])
        return [self._worktree(row, key=key) for row in self._rows(sql, args, table=table)]

    def sync_experiments(self, exp_ids, *, actor='main'):
        _text(actor, 'actor')
        ids = _list_of_text(exp_ids, 'exp_ids')
        imported, skipped = ([], [])
        conn = self.store.connect()
        try:
            conn.execute('PRAGMA query_only=ON')
            known = {row[0] for row in conn.execute('SELECT id FROM experiments')}
        except sqlite3.OperationalError:
            known = set()
        finally:
            conn.close()
        for exp_id in ids:
            (imported if exp_id in known else skipped).append(exp_id)
        return {'registered': imported, 'missing': skipped}

    def list_checkpoints(self, topic_id, *, limit=None, offset=0, before_id=None):
        _text(topic_id, 'topic_id')
        limit, offset = (self._limit(limit), self._offset(offset))
        sql = 'SELECT * FROM topic_checkpoints WHERE topic_id=?'
        args = [topic_id]
        if before_id is not None:
            if type(before_id) is not int or before_id < 0:
                raise ValueError('before_id must be a non-negative integer or None')
            sql += ' AND id<?'
            args.append(before_id)
        sql += ' ORDER BY id DESC'
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args.extend([limit, offset])
        return [self._checkpoint(row) for row in self._rows(sql, args, table='topic_checkpoints')]

    def latest_checkpoint(self, topic_id):
        _text(topic_id, 'topic_id')
        rows = self._rows('SELECT * FROM topic_checkpoints WHERE topic_id=?\n            ORDER BY id DESC LIMIT 1', (topic_id,), table='topic_checkpoints')
        return self._checkpoint(rows[0]) if rows else None

    def checkpoint_count(self, topic_id):
        _text(topic_id, 'topic_id')
        rows = self._rows('SELECT COUNT(*) AS n FROM topic_checkpoints WHERE topic_id=?', (topic_id,), table='topic_checkpoints')
        return rows[0]['n'] if rows else 0

    def overview(self, *, topic_limit=None, topic_offset=0, checkpoint_limit=1, include_archived=False):
        topic_limit = self._limit(topic_limit, 'topic_limit')
        topic_offset = self._offset(topic_offset)
        checkpoint_limit = self._limit(checkpoint_limit, 'checkpoint_limit')
        return self._read_with(self._empty_board, lambda conn: self._overview(conn, topic_limit, topic_offset, checkpoint_limit, include_archived))

    def _empty_board(self):
        return {'db_present': False, 'as_of': None, 'topics': [], 'unbound_worktree_topics': [], 'orphan_experiments': [], 'counts': {'topics': 0, 'open_sessions': 0, 'checkpoints': 0, 'active_experiments': 0, 'active_worktrees': 0, 'active_branches': 0}}

    def _overview(self, conn, topic_limit, topic_offset, checkpoint_limit, include_archived):
        as_of = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f','now') || 'Z'").fetchone()[0]
        sql = 'SELECT * FROM topics'
        args = []
        if not include_archived:
            sql += " WHERE status<>'archived'"
        sql += ' ORDER BY priority, created_at, id'
        if topic_limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args.extend([topic_limit, topic_offset])
        topics = []
        for row in conn.execute(sql, args):
            item = self._topic(row)
            item['session_count'] = conn.execute('SELECT COUNT(*) FROM topic_sessions WHERE topic_id=?', (row['id'],)).fetchone()[0]
            open_row = conn.execute("SELECT * FROM topic_sessions WHERE topic_id=? AND status='open'", (row['id'],)).fetchone()
            item['open_session'] = self._session(open_row)
            item['checkpoint_count'] = conn.execute('SELECT COUNT(*) FROM topic_checkpoints WHERE topic_id=?', (row['id'],)).fetchone()[0]
            item['experiment_count'] = conn.execute('SELECT COUNT(*) FROM topic_experiments WHERE topic_id=? AND active=1', (row['id'],)).fetchone()[0]
            latest = conn.execute('SELECT id,kind,summary,created_at,session_id,generation,\n                inflight_json FROM topic_checkpoints WHERE topic_id=?\n                ORDER BY id DESC LIMIT ?', (row['id'], checkpoint_limit or 0)).fetchall()
            item['recent_checkpoints'] = [{'id': r['id'], 'kind': r['kind'], 'at': r['created_at'], 'session_id': r['session_id'], 'generation': r['generation'], 'summary': r['summary'][:200] + '…' if len(r['summary']) > 200 else r['summary'], 'inflight_open': sum((1 for i in json.loads(r['inflight_json']) if i.get('status') == 'open'))} for r in latest]
            item['latest_checkpoint_at'] = latest[0]['created_at'] if latest else None
            topics.append(item)
        unbound = [r['id'] for r in conn.execute('SELECT id FROM topics WHERE worktree IS NULL OR branch IS NULL ORDER BY id')]
        orphans = [r['exp_id'] for r in conn.execute('SELECT exp_id FROM topic_experiments WHERE topic_id NOT IN (SELECT id FROM topics)')]
        missing_experiments = [r['exp_id'] for r in conn.execute('SELECT exp_id FROM topic_experiments WHERE active=1 AND exp_id NOT IN (SELECT id FROM experiments)')]
        counts = {'topics': conn.execute('SELECT COUNT(*) FROM topics').fetchone()[0], 'open_sessions': conn.execute("SELECT COUNT(*) FROM topic_sessions WHERE status='open'").fetchone()[0], 'checkpoints': conn.execute('SELECT COUNT(*) FROM topic_checkpoints').fetchone()[0], 'active_experiments': conn.execute('SELECT COUNT(*) FROM topic_experiments WHERE active=1').fetchone()[0], 'active_worktrees': conn.execute('SELECT COUNT(*) FROM topic_worktrees WHERE active=1').fetchone()[0], 'active_branches': conn.execute('SELECT COUNT(*) FROM topic_branches WHERE active=1').fetchone()[0]}
        return {'db_present': True, 'as_of': as_of, 'topics': topics, 'unbound_worktree_topics': unbound, 'orphan_experiments': orphans, 'missing_experiments': missing_experiments, 'counts': counts}

    def show(self, topic_id, *, checkpoint_limit=5, history_before=None, session_limit=10, experiment_limit=50, worktree_limit=20, branch_limit=20, include_archived_sessions=True):
        _text(topic_id, 'topic_id')
        checkpoint_limit = self._limit(checkpoint_limit, 'checkpoint_limit')
        session_limit = self._limit(session_limit, 'session_limit')
        experiment_limit = self._limit(experiment_limit, 'experiment_limit')
        worktree_limit = self._limit(worktree_limit, 'worktree_limit')
        branch_limit = self._limit(branch_limit, 'branch_limit')
        return self._read_with(self._empty_detail, lambda conn: self._show(conn, topic_id, checkpoint_limit, history_before, session_limit, experiment_limit, worktree_limit, branch_limit, include_archived_sessions))

    def _show(self, conn, topic_id, checkpoint_limit, history_before, session_limit, experiment_limit, worktree_limit, branch_limit, include_archived_sessions):
        row = conn.execute('SELECT * FROM topics WHERE id=?', (topic_id,)).fetchone()
        if row is None:
            detail = self._empty_detail()
            detail['db_present'] = True
            return detail
        topic = self._topic(row)
        sql = 'SELECT * FROM topic_sessions WHERE topic_id=?'
        args = [topic_id]
        if not include_archived_sessions:
            sql += " AND status<>'superseded'"
        sql += ' ORDER BY generation DESC'
        if session_limit is not None:
            sql += ' LIMIT ?'
            args.append(session_limit)
        sessions = [self._session(r) for r in conn.execute(sql, args)]
        sessions_total = conn.execute('SELECT COUNT(*) FROM topic_sessions WHERE topic_id=?', (topic_id,)).fetchone()[0]
        live = conn.execute("SELECT * FROM topic_sessions WHERE topic_id=?\n            AND status='open' ORDER BY generation DESC LIMIT 1", (topic_id,)).fetchone()
        checkpoints, total, has_more = self._checkpoint_page(conn, topic_id, checkpoint_limit, history_before)
        experiments = [self._experiment(r) for r in self._limited(conn, 'SELECT * FROM topic_experiments WHERE topic_id=? ORDER BY active DESC,exp_id', (topic_id,), experiment_limit)]
        worktrees = [self._worktree(r) for r in self._limited(conn, 'SELECT * FROM topic_worktrees WHERE topic_id=? ORDER BY active DESC,bound_at', (topic_id,), worktree_limit)]
        branches = [self._worktree(r, key='branch') for r in self._limited(conn, 'SELECT * FROM topic_branches WHERE topic_id=? ORDER BY active DESC,bound_at', (topic_id,), branch_limit)]
        return {'db_present': True, 'topic': topic, 'leader': self._session(live), 'sessions': sessions, 'sessions_total': sessions_total, 'checkpoints': checkpoints, 'checkpoints_total': total, 'has_more_checkpoints': has_more, 'experiments': experiments, 'worktrees': worktrees, 'branches': branches}

    @staticmethod
    def _limited(conn, sql, args, limit):
        if limit == 0:
            return []
        if limit is not None:
            sql += ' LIMIT ?'
            args = (*args, limit)
        return list(conn.execute(sql, args))

    @staticmethod
    def _empty_detail():
        return {'db_present': False, 'topic': None, 'leader': None, 'sessions': [], 'sessions_total': 0, 'checkpoints': [], 'checkpoints_total': 0, 'has_more_checkpoints': False, 'experiments': [], 'worktrees': [], 'branches': []}

    def _checkpoint_page(self, conn, topic_id, limit, before_id):
        total = conn.execute('SELECT COUNT(*) FROM topic_checkpoints WHERE topic_id=?', (topic_id,)).fetchone()[0]
        sql = 'SELECT * FROM topic_checkpoints WHERE topic_id=?'
        args = [topic_id]
        if before_id is not None:
            if type(before_id) is not int or before_id < 0:
                raise ValueError('history_before must be a non-negative integer or None')
            sql += ' AND id<?'
            args.append(before_id)
        sql += ' ORDER BY id DESC'
        if limit is not None:
            sql += ' LIMIT ?'
            args.append(limit)
        rows = list(conn.execute(sql, args))
        has_more = False
        if rows:
            has_more = conn.execute('SELECT 1 FROM topic_checkpoints WHERE topic_id=? AND id<? LIMIT 1', (topic_id, rows[-1]['id'])).fetchone() is not None
        return ([self._checkpoint(r) for r in rows], total, has_more)

    def readonly_board(self):
        return self.overview()

    def readonly_topic(self, topic_id, history_limit=20, history_before=None):
        return self.show(topic_id, checkpoint_limit=history_limit, history_before=history_before)

class _ReadOnlyHandle:

    def __init__(self, db_path):
        self.db_path = str(db_path)

    def connect(self):
        path = Path(self.db_path)
        if not path.exists():
            raise sqlite3.OperationalError('no such table: topics (database is absent)')
        uri = path.resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        return conn

def readonly_board(db_path):
    return _readonly_topics(db_path).overview()

def readonly_topic(db_path, topic_id, history_limit=20, history_before=None):
    return _readonly_topics(db_path).show(topic_id, checkpoint_limit=history_limit, history_before=history_before)

def _readonly_topics(db_path):
    if isinstance(db_path, Topics):
        return db_path
    return Topics(db_path, readonly=True)
