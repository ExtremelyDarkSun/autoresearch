# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/execution.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import posixpath
import re
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from typing import Protocol
try:
    from kernel.autoresearch.runtime.policy import ActionPolicy
except ImportError:
    from kernel.autoresearch.runtime.policy import ActionPolicy
STATES = frozenset({'queued', 'submitting', 'running', 'unknown', 'collecting', 'partial', 'completed', 'failed', 'cancelled'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled'})

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def sha_valid(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None

@dataclass(frozen=True)
class SharedInput:
    path: str
    fingerprint: str
    read_only: bool
    immutable: bool

@dataclass(frozen=True)
class RunSpec:
    exp_id: str
    request_key: str
    code_fingerprint: str
    task_fingerprint: str
    environment_fingerprint: str
    unit_root: str
    code_root: str
    environment_root: str
    input_root: str
    output_root: str
    task_ids: tuple[str, ...]
    reservation_id: str
    lease_id: str
    isolated_environment: bool
    shared_mutable_hardlinks: bool
    required_artifacts: tuple[str, ...] = ('results.json',)
    shared_inputs: tuple[SharedInput, ...] = ()
    spec_version: int = 1
    model_id: str | None = None
    execution_config: dict | None = None
    execution_config_fingerprint: str | None = None

    def validate(self, *, require_execution=False):
        if type(self.spec_version) is not int or self.spec_version != 1:
            raise ValueError('不支持的RunSpec.spec_version')
        configured = any((value is not None for value in (self.model_id, self.execution_config, self.execution_config_fingerprint)))
        if require_execution or configured:
            if not isinstance(self.execution_config, dict) or not self.execution_config:
                raise ValueError('启动必须显式指定非空execution_config映射')

            def validate_json(value):
                if isinstance(value, dict):
                    if any((not isinstance(key, str) for key in value)):
                        raise ValueError('execution_config的JSON对象键必须是字符串')
                    for child in value.values():
                        validate_json(child)
                elif isinstance(value, list):
                    for child in value:
                        validate_json(child)
                elif value is not None and type(value) not in (str, int, float, bool):
                    raise ValueError('execution_config必须只含JSON类型')
            validate_json(self.execution_config)
            actual = digest(canonical(self.execution_config).encode('utf-8'))
            if not sha_valid(self.execution_config_fingerprint) or actual != self.execution_config_fingerprint:
                raise ValueError('execution_config_fingerprint与canonical JSON不符')
        for key in ('exp_id', 'request_key', 'reservation_id', 'lease_id'):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ValueError(f'RunSpec.{key} 必须非空')
        for key in ('code_fingerprint', 'task_fingerprint', 'environment_fingerprint'):
            if not sha_valid(getattr(self, key)):
                raise ValueError(f'RunSpec.{key} 必须是小写SHA256')
        roots = [self.unit_root, self.code_root, self.environment_root, self.input_root, self.output_root]
        if any((not p.startswith('/') or posixpath.normpath(p) != p or p == '/' for p in roots)):
            raise ValueError('运行单元路径必须为规范化POSIX绝对路径')
        children = roots[1:]
        if any((not p.startswith(self.unit_root + '/') for p in children)):
            raise ValueError('代码、环境、输入、输出必须在独立运行单元内')
        if any((a == b or a.startswith(b + '/') or b.startswith(a + '/') for i, a in enumerate(children) for b in children[i + 1:])):
            raise ValueError('代码、环境、输入、输出路径不得重叠')
        if self.isolated_environment is not True or self.shared_mutable_hardlinks is not False:
            raise ValueError('拒绝共享可变硬链接或共享环境')
        if not self.task_ids or any((not isinstance(t, str) or not re.fullmatch('[A-Za-z0-9_.-]+', t) or t in {'.', '..'} for t in self.task_ids)):
            raise ValueError('题单必须非空且题号必须是安全文件名')
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError('题单不得重复')
        for path in self.required_artifacts:
            if path.startswith('/') or posixpath.normpath(path) != path or path.startswith('../') or (path in {'', '.', '..'}):
                raise ValueError('产物清单必须使用安全相对路径')
        if not self.required_artifacts or len(set(self.required_artifacts)) != len(self.required_artifacts):
            raise ValueError('产物清单必须非空且不得重复')
        for item in self.shared_inputs:
            if not isinstance(item, SharedInput) or item.read_only is not True or item.immutable is not True or (not sha_valid(item.fingerprint)):
                raise ValueError('共享输入必须声明只读、不变和内容指纹')
            if not item.path.startswith('/') or posixpath.normpath(item.path) != item.path:
                raise ValueError('共享只读输入必须声明规范化绝对路径')
            if any((item.path == root or item.path.startswith(root + '/') or root.startswith(item.path.rstrip('/') + '/') for root in (self.code_root, self.environment_root, self.output_root))):
                raise ValueError('共享只读输入不得覆盖可变代码、环境或输出')
        return self

    def artifact_checklist(self):
        return tuple(self.required_artifacts)

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value['task_ids'] = tuple(value['task_ids'])
        value['required_artifacts'] = tuple(value['required_artifacts'])
        value['shared_inputs'] = tuple((SharedInput(**v) for v in value.get('shared_inputs', [])))
        return cls(**value)

@dataclass(frozen=True)
class JobStatus:
    remote_id: str | None
    state: str
    evidence: dict | None = None

@dataclass(frozen=True)
class ResultBundle:
    artifacts: dict[str, bytes]
    manifest: dict[str, str]

class Adapter(Protocol):

    def submit(self, spec: RunSpec, attempt_id: str, output_dir: str) -> JobStatus:
        ...

    def query(self, request_key: str, remote_id: str | None) -> JobStatus:
        ...

    def collect(self, request_key: str, remote_id: str, output_dir: str) -> ResultBundle:
        ...

    def cancel(self, request_key: str, remote_id: str | None) -> JobStatus:
        ...

class DefinitiveFailure(RuntimeError):
    pass

class Execution:
    spec_type = RunSpec

    def __init__(self, store, adapter: Adapter, *, validate_reservation=None, validate_lease=None, validate_absence=None, validate_queue=None, validate_send=None, policy=None, actor='runner'):
        self.store, self.adapter, self.actor = (store, adapter, actor)
        self.validate_reservation = validate_reservation
        self.validate_lease = validate_lease
        self.validate_absence = validate_absence
        self.validate_queue = validate_queue
        #: 紧邻真正发送前的**最后一次**事务内只读复核（`(conn, spec) -> True`）。
        #: ⛔ 必须在同一个写事务里、在 `adapter.submit` 之前调用：这是「claim/排队之后 → 真正发送」
        #: 之间唯一还能挡住「批准被撤回 / 来源被改 / 监控新增待审 / 审计被新阻断」的位置。
        self.validate_send = validate_send
        self.policy = policy or ActionPolicy(store)
        with store.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS exec_attempts (\n                id TEXT PRIMARY KEY, exp_id TEXT NOT NULL REFERENCES experiments(id),\n                request_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL, spec_json TEXT NOT NULL,\n                state TEXT NOT NULL, previous_attempt TEXT REFERENCES exec_attempts(id),\n                output_dir TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            conn.execute('CREATE TABLE IF NOT EXISTS exec_jobs (\n                attempt_id TEXT PRIMARY KEY REFERENCES exec_attempts(id), remote_id TEXT,\n                state TEXT NOT NULL, remote_state TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            conn.execute('CREATE TABLE IF NOT EXISTS exec_observations (\n                id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES exec_attempts(id),\n                operation TEXT NOT NULL, phase TEXT NOT NULL, payload_json TEXT NOT NULL,\n                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            conn.execute('CREATE TABLE IF NOT EXISTS exec_artifacts (\n                observation_id INTEGER NOT NULL REFERENCES exec_observations(id),\n                path TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,\n                PRIMARY KEY(observation_id,path))')
            for table in ('exec_observations', 'exec_artifacts'):
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'append-only evidence'); END")

    def _observe(self, conn, attempt_id, operation, phase, payload):
        cursor = conn.execute('INSERT INTO exec_observations (attempt_id,operation,phase,payload_json) VALUES (?,?,?,?)', (attempt_id, operation, phase, canonical(payload)))
        exp = conn.execute('SELECT exp_id FROM exec_attempts WHERE id=?', (attempt_id,)).fetchone()[0]
        self.store.emit(conn, exp, 'execution_' + phase, self.actor, {'attempt_id': attempt_id, 'operation': operation, **payload})
        return cursor.lastrowid

    def _state(self, conn, attempt_id, state, remote_id=None, remote_state=None):
        if state not in STATES:
            raise ValueError('未知生命周期状态')
        current = conn.execute('SELECT state FROM exec_attempts WHERE id=?', (attempt_id,)).fetchone()[0]
        if current in TERMINAL and current != state:
            return
        conn.execute('UPDATE exec_attempts SET state=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (state, attempt_id))
        conn.execute('UPDATE exec_jobs SET state=?,remote_id=COALESCE(?,remote_id),remote_state=COALESCE(?,remote_state),updated_at=CURRENT_TIMESTAMP WHERE attempt_id=?', (state, remote_id, remote_state, attempt_id))

    def get(self, attempt_id):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT a.*,j.remote_id,j.remote_state FROM exec_attempts a JOIN exec_jobs j ON j.attempt_id=a.id WHERE a.id=?', (attempt_id,)).fetchone()
            if not row:
                raise KeyError(attempt_id)
            return dict(row)
        finally:
            conn.close()

    def queue(self, spec: RunSpec, previous_attempt=None, *, conn=None):
        spec.validate()
        payload = canonical(asdict(spec))
        payload_hash = digest(payload.encode('utf-8'))
        if conn is not None and (not conn.in_transaction):
            raise ValueError('queue外部conn必须已经开启事务')
        context = self.store.transaction() if conn is None else nullcontext(conn)
        with context as conn:
            if self.validate_queue is not None and self.validate_queue(conn, spec) is not True:
                raise PermissionError('queue必须通过事务内实验状态验证')
            old = conn.execute('SELECT id,payload_hash FROM exec_attempts WHERE request_key=?', (spec.request_key,)).fetchone()
            if old:
                if old['payload_hash'] != payload_hash:
                    raise ValueError('相同request_key对应不同payload，拒绝请求')
                return old['id']
            if previous_attempt:
                old_attempt = conn.execute('SELECT a.exp_id,a.state,j.remote_state FROM exec_attempts a JOIN exec_jobs j ON j.attempt_id=a.id WHERE a.id=?', (previous_attempt,)).fetchone()
                if not old_attempt or old_attempt['exp_id'] != spec.exp_id:
                    raise ValueError('重试必须引用本实验的原attempt')
                if old_attempt['state'] not in {'failed', 'cancelled', 'partial'} or (old_attempt['state'] == 'partial' and old_attempt['remote_state'] != 'completed'):
                    raise ValueError('原job未明确结束，禁止新attempt')
            for active in conn.execute("SELECT spec_json FROM exec_attempts WHERE state NOT IN ('completed','failed','cancelled','partial')"):
                active_root = json.loads(active['spec_json'])['unit_root']
                if active_root == spec.unit_root or active_root.startswith(spec.unit_root + '/') or spec.unit_root.startswith(active_root + '/'):
                    raise ValueError('活跃attempt必须使用不重叠的运行单元')
            attempt_id = uuid.uuid4().hex
            output = spec.output_root + '/' + attempt_id
            conn.execute("INSERT INTO exec_attempts (id,exp_id,request_key,payload_hash,spec_json,state,previous_attempt,output_dir) VALUES (?,?,?,?,?,'queued',?,?)", (attempt_id, spec.exp_id, spec.request_key, payload_hash, payload, previous_attempt, output))
            conn.execute("INSERT INTO exec_jobs(attempt_id,state) VALUES (?,'queued')", (attempt_id,))
            self._observe(conn, attempt_id, 'queue', 'recorded', {'payload_hash': payload_hash})
            return attempt_id

    def _context(self, row):
        context = {'experiment': row['exp_id'], 'attempt': row['id']}
        if row['remote_id']:
            context['job'] = row['remote_id']
        spec = self.spec_type.from_dict(json.loads(row['spec_json']))
        context['resource'] = spec.lease_id
        return context

    def submit(self, attempt_id):
        row = self.get(attempt_id)
        spec = self.spec_type.from_dict(json.loads(row['spec_json']))
        with self.store.transaction() as conn:
            current = conn.execute('SELECT state FROM exec_attempts WHERE id=?', (attempt_id,)).fetchone()[0]
            if current != 'queued':
                return self.get(attempt_id)
            self.policy.require('submit', self._context(row))
            spec.validate(require_execution=True)
            for callback in (self.validate_reservation, self.validate_lease):
                if callback is None or callback(conn, spec, 'submit') is not True:
                    raise PermissionError('启动必须通过事务内reservation和lease验证')
            # 紧邻发送前的最后一道：同一个写事务里再复核一次上游（来源 / 审计 / 监控 / 批准）。
            # ⛔ 放在 `_state('submitting')` 之前：没过就等于这次 send 从未发生。
            if self.validate_send is not None and self.validate_send(conn, spec) is not True:
                raise PermissionError('发送前复核未通过：这次 send 没有发生')
            self._state(conn, attempt_id, 'submitting')
            self._observe(conn, attempt_id, 'submit', 'intent', {'request_key': spec.request_key})
        try:
            status = self.adapter.submit(spec, attempt_id, row['output_dir'])
            self._apply_status(attempt_id, 'submit', status)
        except DefinitiveFailure as exc:
            with self.store.transaction() as conn:
                self._state(conn, attempt_id, 'failed', remote_state='failed')
                self._observe(conn, attempt_id, 'submit', 'failed', {'error': str(exc)})
        except Exception as exc:
            self._unknown(attempt_id, 'submit', exc)
        return self.get(attempt_id)

    def _unknown(self, attempt_id, operation, exc):
        with self.store.transaction() as conn:
            current = conn.execute('SELECT state FROM exec_attempts WHERE id=?', (attempt_id,)).fetchone()[0]
            if current not in TERMINAL:
                self._state(conn, attempt_id, 'unknown')
            self._observe(conn, attempt_id, operation, 'unknown', {'error': str(exc), 'error_type': type(exc).__name__})

    def _apply_status(self, attempt_id, operation, status):
        if isinstance(status, JobStatus) and status.state == 'not_found':
            if operation != 'query' or status.remote_id is not None:
                raise ValueError('不存在证明只能来自query且不得指向已知job')
            with self.store.transaction() as conn:
                row = dict(conn.execute('SELECT a.*,j.remote_id,j.remote_state FROM exec_attempts a JOIN exec_jobs j ON j.attempt_id=a.id WHERE a.id=?', (attempt_id,)).fetchone())
                if row['remote_id'] or row['state'] not in {'unknown', 'submitting'}:
                    raise ValueError('已有job或当前非启动不确定状态，拒绝不存在恢复')
                if self.validate_absence is None or self.validate_absence(conn, row, status) is not True:
                    raise PermissionError('恢复原请求必须通过可信无job及无在途启动证明')
                self._state(conn, attempt_id, 'queued', remote_state='not_found')
                self._observe(conn, attempt_id, 'query', 'absence_confirmed', asdict(status))
            return
        if not isinstance(status, JobStatus) or status.state not in {'queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'}:
            raise ValueError('adapter返回非法job状态')
        if status.state != 'unknown' and (not status.remote_id):
            raise ValueError('adapter已知job状态必须提供remote_id')
        row = self.get(attempt_id)
        if row['remote_id'] and status.remote_id != row['remote_id']:
            raise ValueError('query不许切换原job')
        state = 'collecting' if status.state == 'completed' else 'running' if status.state == 'queued' else status.state
        with self.store.transaction() as conn:
            current = conn.execute('SELECT state FROM exec_attempts WHERE id=?', (attempt_id,)).fetchone()[0]
            if current not in TERMINAL:
                self._state(conn, attempt_id, state, status.remote_id, status.state)
            self._observe(conn, attempt_id, operation, 'response', asdict(status))

    def reconcile(self, attempt_id):
        row = self.get(attempt_id)
        if row['state'] in TERMINAL or row['state'] == 'queued':
            return row
        self.policy.require('query', self._context(row))
        with self.store.transaction() as conn:
            self._observe(conn, attempt_id, 'query', 'intent', {'request_key': row['request_key'], 'remote_id': row['remote_id']})
        try:
            self._apply_status(attempt_id, 'query', self.adapter.query(row['request_key'], row['remote_id']))
        except Exception as exc:
            self._unknown(attempt_id, 'query', exc)
        return self.get(attempt_id)

    @staticmethod
    def validate_results(spec, bundle):
        errors = []
        if not isinstance(bundle, ResultBundle):
            raise ValueError('adapter must return ResultBundle')
        for path in spec.artifact_checklist():
            content = bundle.artifacts.get(path)
            if content is None:
                errors.append('missing:' + path)
            elif not isinstance(content, bytes):
                errors.append('invalid_content:' + path)
            elif bundle.manifest.get(path) != digest(content):
                errors.append('hash_mismatch:' + path)
        for path, content in bundle.artifacts.items():
            if not isinstance(content, bytes) or not sha_valid(bundle.manifest.get(path)) or bundle.manifest.get(path) != digest(content):
                errors.append('invalid_manifest:' + path)
            if path.startswith('/') or ':' in path or chr(92) in path or (posixpath.normpath(path) != path) or path.startswith('../') or (path in {'', '.', '..'}):
                errors.append('unsafe_path:' + str(path))
        return tuple(sorted(set(errors)))

    def collect(self, attempt_id):
        row = self.get(attempt_id)
        if row['state'] == 'completed':
            return row
        if row['state'] not in {'collecting', 'partial'} or row['remote_state'] != 'completed':
            raise ValueError('取回前必须query确认原job完成')
        self.policy.require('collect', self._context(row))
        spec = self.spec_type.from_dict(json.loads(row['spec_json']))
        with self.store.transaction() as conn:
            self._state(conn, attempt_id, 'collecting')
            self._observe(conn, attempt_id, 'collect', 'intent', {'remote_id': row['remote_id']})
        try:
            bundle = self.adapter.collect(row['request_key'], row['remote_id'], row['output_dir'])
            errors = self.validate_results(spec, bundle)
            with self.store.transaction() as conn:
                observation = self._observe(conn, attempt_id, 'collect', 'response', {'errors': errors, 'manifest': bundle.manifest})
                for path, content in bundle.artifacts.items():
                    if isinstance(content, bytes):
                        conn.execute('INSERT INTO exec_artifacts(observation_id,path,sha256,content) VALUES (?,?,?,?)', (observation, path, digest(content), content))
                self._state(conn, attempt_id, 'partial' if errors else 'completed')
        except Exception as exc:
            self._unknown(attempt_id, 'collect', exc)
        return self.get(attempt_id)

    def cancel(self, attempt_id):
        row = self.get(attempt_id)
        if row['state'] in TERMINAL:
            return row
        self.policy.require('cancel', self._context(row))
        local_cancel = False
        with self.store.transaction() as conn:
            current = conn.execute('SELECT state FROM exec_attempts WHERE id=?', (attempt_id,)).fetchone()[0]
            if current == 'queued':
                self._state(conn, attempt_id, 'cancelled', remote_state='cancelled')
                self._observe(conn, attempt_id, 'cancel', 'local', {})
                local_cancel = True
            elif current not in TERMINAL:
                spec = self.spec_type.from_dict(json.loads(row['spec_json']))
                if self.validate_lease is None or self.validate_lease(conn, spec, 'cancel') is not True:
                    raise PermissionError('远端取消必须核lease所有权或可信恢复凭证')
                self._observe(conn, attempt_id, 'cancel', 'intent', {'remote_id': row['remote_id']})
            else:
                local_cancel = True
        if local_cancel:
            return self.get(attempt_id)
        try:
            self._apply_status(attempt_id, 'cancel', self.adapter.cancel(row['request_key'], row['remote_id']))
        except Exception as exc:
            self._unknown(attempt_id, 'cancel', exc)
        return self.get(attempt_id)

    def retry(self, attempt_id, spec: RunSpec):
        row = self.get(attempt_id)
        self.policy.require('retry', self._context(row))
        if row['request_key'] == spec.request_key:
            raise ValueError('重试必须使用新request_key和新attempt')
        return self.queue(spec, previous_attempt=attempt_id)

class FakeAdapter:

    def __init__(self):
        self.jobs = {}
        self.submit_calls = 0
        self.query_calls = 0
        self.lose_submit_response = False
        self.timeout_query = False
        self.bundle = ResultBundle({}, {})

    def submit(self, spec, attempt_id, output_dir):
        self.submit_calls += 1
        payload = digest(canonical(asdict(spec)).encode('utf-8'))
        if spec.request_key in self.jobs:
            if self.jobs[spec.request_key]['payload'] != payload:
                raise ValueError('假远端拒绝冲突payload')
        else:
            self.jobs[spec.request_key] = {'payload': payload, 'remote_id': 'fake-' + attempt_id, 'state': 'running', 'output_dir': output_dir}
        if self.lose_submit_response:
            self.lose_submit_response = False
            raise TimeoutError('假远端已创建job，但启动响应丢失')
        return self.query(spec.request_key, None)

    def query(self, request_key, remote_id):
        self.query_calls += 1
        if self.timeout_query:
            raise TimeoutError('假远端query超时')
        job = self.jobs.get(request_key)
        if not job:
            return JobStatus(remote_id, 'unknown')
        if remote_id and remote_id != job['remote_id']:
            raise ValueError('假远端原job不匹配')
        return JobStatus(job['remote_id'], job['state'])

    def collect(self, request_key, remote_id, output_dir):
        job = self.jobs[request_key]
        if job['remote_id'] != remote_id or job['output_dir'] != output_dir:
            raise ValueError('假远端拒绝串job或输出目录')
        return self.bundle

    def cancel(self, request_key, remote_id):
        if request_key not in self.jobs:
            return JobStatus(remote_id, 'unknown')
        self.jobs[request_key]['state'] = 'cancelled'
        return self.query(request_key, remote_id)
