# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/policy.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import json
import re
import uuid
from dataclasses import asdict, dataclass
from enum import Enum

class Action(str, Enum):
    SUBMIT = 'submit'
    RETRY = 'retry'
    QUERY = 'query'
    COLLECT = 'collect'
    CANCEL = 'cancel'
    PUBLISH = 'publish'
    DELIVER = 'deliver'
    REQUEST_APPROVAL = 'request_approval'

class Category(str, Enum):
    AUTHORIZATION = 'authorization'
    ISOLATION = 'isolation'
    INTEGRITY = 'integrity'
    RESULTS = 'results'
    DELIVERY = 'delivery'
    COMMUNICATION = 'communication'
    RESOURCE = 'resource'

class Severity(str, Enum):
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'

@dataclass(frozen=True)
class Scope:
    kind: str
    object_id: str

    def __post_init__(self):
        if self.kind not in {'experiment', 'attempt', 'job', 'channel', 'resource', 'global'}:
            raise ValueError('scope.kind 不在允许集合中')
        if not self.object_id or (self.kind == 'global' and self.object_id != '*'):
            raise ValueError('scope.object_id 必须非空，全局作用域必须为 *')

@dataclass(frozen=True)
class Finding:
    scope: Scope
    evidence_hash: str
    category: Category
    severity: Severity
    recoverable: bool
    detail: str
    finding_id: str = ''

    def __post_init__(self):
        if not isinstance(self.scope, Scope):
            raise ValueError('scope 必须是 Scope 对象')
        if not re.fullmatch('[0-9a-f]{64}', self.evidence_hash):
            raise ValueError('evidence_hash 必须为小写 SHA256')
        object.__setattr__(self, 'category', Category(self.category))
        object.__setattr__(self, 'severity', Severity(self.severity))
        if type(self.recoverable) is not bool or not self.detail.strip():
            raise ValueError('必须声明可恢复性与发现说明')
        if not self.finding_id:
            object.__setattr__(self, 'finding_id', uuid.uuid4().hex)

@dataclass(frozen=True)
class PolicyDecision:
    action: Action
    allowed: bool
    blocked_by: tuple[str, ...]
    next_actions: tuple[str, ...]
    accepted_findings: tuple[str, ...] = ()
    resolved_findings: tuple[str, ...] = ()

class ActionBlocked(RuntimeError):
    pass

class ActionPolicy:
    BLOCKS = {Category.AUTHORIZATION: {Action.SUBMIT, Action.RETRY}, Category.ISOLATION: {Action.SUBMIT, Action.RETRY, Action.PUBLISH}, Category.INTEGRITY: {Action.SUBMIT, Action.RETRY, Action.PUBLISH}, Category.RESULTS: {Action.PUBLISH}, Category.DELIVERY: {Action.DELIVER, Action.PUBLISH}, Category.COMMUNICATION: {Action.DELIVER, Action.REQUEST_APPROVAL}, Category.RESOURCE: {Action.SUBMIT, Action.RETRY}}
    REMEDY = {Category.AUTHORIZATION: 'request_approval', Category.ISOLATION: 'repair_isolation_then_reverify', Category.INTEGRITY: 'restore_frozen_evidence_then_reverify', Category.RESULTS: 'collect_missing_evidence', Category.DELIVERY: 'reconcile_delivery', Category.COMMUNICATION: 'repair_channel', Category.RESOURCE: 'reschedule_or_renew_reservation'}

    def __init__(self, store, *, validate_decision=None, validate_reverification=None):
        self.store = store
        self.validate_decision = validate_decision
        self.validate_reverification = validate_reverification
        with store.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS policy_findings (\n                id TEXT PRIMARY KEY, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL,\n                evidence_hash TEXT NOT NULL, category TEXT NOT NULL,\n                severity TEXT NOT NULL, recoverable INTEGER NOT NULL, detail TEXT NOT NULL,\n                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            conn.execute('CREATE TABLE IF NOT EXISTS policy_acceptances (\n                id INTEGER PRIMARY KEY, finding_id TEXT NOT NULL REFERENCES policy_findings(id),\n                evidence_hash TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,\n                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                UNIQUE(finding_id,evidence_hash))')
            conn.execute('CREATE TABLE IF NOT EXISTS policy_resolutions (\n                id INTEGER PRIMARY KEY, finding_id TEXT NOT NULL REFERENCES policy_findings(id),\n                old_evidence_hash TEXT NOT NULL, new_evidence_hash TEXT NOT NULL,\n                actor TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT NOT NULL,\n                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                UNIQUE(finding_id,old_evidence_hash,new_evidence_hash))')
            for table in ('policy_findings', 'policy_acceptances', 'policy_resolutions'):
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'append-only evidence'); END")

    def record(self, finding: Finding, actor='gate') -> str:
        values = (finding.finding_id, finding.scope.kind, finding.scope.object_id, finding.evidence_hash, finding.category.value, finding.severity.value, int(finding.recoverable), finding.detail)
        with self.store.transaction() as conn:
            old = conn.execute('SELECT * FROM policy_findings WHERE id=?', (finding.finding_id,)).fetchone()
            if old:
                if tuple((old[k] for k in ('id', 'scope_kind', 'scope_id', 'evidence_hash', 'category', 'severity', 'recoverable', 'detail'))) != values:
                    raise ValueError('相同 finding_id 不许改变发现内容')
                return finding.finding_id
            conn.execute('INSERT INTO policy_findings (id,scope_kind,scope_id,evidence_hash,category,severity,recoverable,detail) VALUES (?,?,?,?,?,?,?,?)', values)
            exp_id = finding.scope.object_id if finding.scope.kind == 'experiment' else None
            self.store.emit(conn, exp_id, 'finding_recorded', actor, asdict(finding))
        return finding.finding_id

    def accept(self, finding_id, evidence_hash, actor, reason, decision_evidence=None):
        if not actor or not reason.strip():
            raise ValueError('接受发现必须记录执行者和原因')
        with self.store.transaction() as conn:
            row = conn.execute('SELECT * FROM policy_findings WHERE id=?', (finding_id,)).fetchone()
            if not row or row['evidence_hash'] != evidence_hash:
                raise ValueError('接受必须绑定已存在发现的证据版本')
            if self.validate_decision is None or self.validate_decision(conn, dict(row), decision_evidence) is not True:
                raise PermissionError('接受发现必须通过可信决策证据验证')
            if row['category'] in {Category.AUTHORIZATION.value, Category.ISOLATION.value}:
                raise ValueError('授权或隔离缺失必须修复后复验，不许通过接受绕过')
            conn.execute('INSERT OR IGNORE INTO policy_acceptances (finding_id,evidence_hash,actor,reason) VALUES (?,?,?,?)', (finding_id, evidence_hash, actor, reason))
            self.store.emit(conn, row['scope_id'] if row['scope_kind'] == 'experiment' else None, 'finding_accepted', actor, {'finding_id': finding_id, 'evidence_hash': evidence_hash, 'reason': reason, 'decision_evidence': decision_evidence})

    def resolve(self, finding_id, old_evidence_hash, new_evidence_hash, actor, reason, evidence=None):
        if not actor or not reason.strip() or (not re.fullmatch('[0-9a-f]{64}', new_evidence_hash)):
            raise ValueError('修复必须记录执行者、原因和新证据SHA256')
        if old_evidence_hash == new_evidence_hash:
            raise ValueError('修复复验必须使用新证据版本')
        with self.store.transaction() as conn:
            row = conn.execute('SELECT * FROM policy_findings WHERE id=?', (finding_id,)).fetchone()
            if not row or row['evidence_hash'] != old_evidence_hash:
                raise ValueError('修复必须绑定旧发现的证据版本')
            if self.validate_reverification is None or self.validate_reverification(conn, dict(row), new_evidence_hash, evidence) is not True:
                raise PermissionError('修复必须通过可信复验验证')
            payload = {'finding_id': finding_id, 'old_evidence_hash': old_evidence_hash, 'new_evidence_hash': new_evidence_hash, 'reason': reason, 'evidence': evidence}
            conn.execute('INSERT OR IGNORE INTO policy_resolutions (finding_id,old_evidence_hash,new_evidence_hash,actor,reason,evidence_json) VALUES (?,?,?,?,?,?)', (finding_id, old_evidence_hash, new_evidence_hash, actor, reason, json.dumps(evidence, ensure_ascii=False, sort_keys=True)))
            self.store.emit(conn, row['scope_id'] if row['scope_kind'] == 'experiment' else None, 'finding_resolved', actor, payload)

    def evaluate(self, action, scope) -> PolicyDecision:
        action = Action(action)
        context = {scope.kind: scope.object_id} if isinstance(scope, Scope) else dict(scope)
        blocked, remedies, accepted, resolved = ([], set(), [], [])
        conn = self.store.connect()
        try:
            rows = conn.execute('SELECT f.*, a.id AS accepted, EXISTS(SELECT 1 FROM policy_resolutions r WHERE r.finding_id=f.id AND r.old_evidence_hash=f.evidence_hash) AS resolved FROM policy_findings f LEFT JOIN policy_acceptances a ON a.finding_id=f.id AND a.evidence_hash=f.evidence_hash ORDER BY f.created_at,f.id').fetchall()
        finally:
            conn.close()
        for row in rows:
            if row['scope_kind'] != 'global' and context.get(row['scope_kind']) != row['scope_id']:
                continue
            category = Category(row['category'])
            if category == Category.DELIVERY and action == Action.PUBLISH and (row['scope_kind'] not in {'experiment', 'attempt', 'job'}):
                continue
            if action not in self.BLOCKS[category]:
                continue
            if row['resolved']:
                resolved.append(row['id'])
            elif row['accepted'] is not None:
                accepted.append(row['id'])
            elif row['severity'] in {Severity.ERROR.value, Severity.CRITICAL.value}:
                blocked.append(row['id'])
                remedies.add(self.REMEDY[category] if row['recoverable'] else 'request_scoped_decision')
        return PolicyDecision(action, not blocked, tuple(blocked), tuple(sorted(remedies)), tuple(accepted), tuple(resolved))

    def require(self, action, scope):
        result = self.evaluate(action, scope)
        if not result.allowed:
            raise ActionBlocked(f'动作 {result.action.value} 受发现阻止：{result.blocked_by}')
        return result
