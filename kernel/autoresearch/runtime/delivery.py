# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 17:10（initialize=False / readonly：跳过 DDL，只读写入口拒绝）
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/delivery.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
#   本轮补：`Delivery(store, initialize=False)` 跳过建表；`readonly`（默认按 store 自报）跳过 DDL 并拒绝
#   enqueue / claim / send / ack / reconcile；⛔ 只读构造不建库、不把缺表读成空。
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
try:
    from kernel.autoresearch.runtime.store import canonical_json, require_text
    from kernel.autoresearch.runtime.store import is_readonly_store
except ImportError:
    from kernel.autoresearch.runtime.store import canonical_json, require_text
    from kernel.autoresearch.runtime.store import is_readonly_store
KINDS = frozenset(('receipt', 'notice', 'summary', 'reply', 'approval_page', 'report', 'card', 'file'))

class DeliveryError(ValueError):
    pass

class PayloadConflict(DeliveryError):
    pass

class LeaseLost(DeliveryError):
    pass

class ArtifactChanged(DeliveryError):
    pass

class NotSent(Exception):
    pass

@dataclass(frozen=True)
class Receipt:
    message_id: str
    request_key: str
    exp_id: str
    recipient: str
    payload_hash: str

class OfflineAdapter(Protocol):

    def send(self, payload: dict, artifact_bytes: bytes | None) -> Receipt:
        ...

    def query(self, request_key: str) -> Receipt | None:
        ...

def _duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeliveryError('lease_seconds必须为正有限数')
    if not math.isfinite(value) or value <= 0:
        raise DeliveryError('lease_seconds必须为正有限数')
    return value

class Delivery:

    def __init__(self, store, *, clock=time.time, ack_hook=None, receipt_verifier=None,
                 event_verifier=None, initialize=None, readonly=None):
        """交付台账。`initialize` / `readonly` 决定构造时要不要建表（默认照旧建）。

        * `readonly=None`（默认）⇒ 按 `store` 自己声明的 `readonly` 判；`ReadonlyStore` 即只读；
        * `initialize=False` ⇒ **跳过 DDL**（表已由别的进程建好，或这是只读诊断）；
        * `readonly=True` ⇒ 跳过 DDL，并且所有写方法直接拒绝（fail-closed）。

        ⛔ 只读构造不建库、不建表、不迁移 schema；读到缺表时报错，不把未知表读成空。
        """
        self.store = store
        self.clock = clock
        self.ack_hook = ack_hook
        self.receipt_verifier = receipt_verifier
        self.event_verifier = event_verifier
        self.readonly = is_readonly_store(store) if readonly is None else bool(readonly)
        if initialize is None:
            initialize = not self.readonly
        if initialize and self.readonly:
            raise DeliveryError('只读 Delivery 不能初始化建表（initialize 与 readonly 冲突）')
        self.initialize = bool(initialize)
        if self.initialize:
            with store.transaction() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS delivery(\n                id INTEGER PRIMARY KEY, exp_id TEXT NOT NULL REFERENCES experiments(id),\n                kind TEXT NOT NULL, request_key TEXT NOT NULL, wire_key TEXT NOT NULL UNIQUE,\n                payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,\n                status TEXT NOT NULL, owner TEXT, fence INTEGER NOT NULL DEFAULT 0,\n                lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,\n                message_id TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,\n                UNIQUE(exp_id,request_key))')
                conn.execute('CREATE TABLE IF NOT EXISTS delivery_receipts(\n                delivery_id INTEGER PRIMARY KEY REFERENCES delivery(id),\n                exp_id TEXT NOT NULL REFERENCES experiments(id),\n                receipt_json TEXT NOT NULL, acked_at REAL NOT NULL)')
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS delivery_receipts_no_{operation.lower()}\n                    BEFORE {operation} ON delivery_receipts BEGIN\n                    SELECT RAISE(ABORT,'delivery receipts are append-only'); END")

    def _require_write(self, action):
        """只读 Delivery 的写动作一律拒绝：⛔ 不降级、不静默跳过、不伪造回执。"""
        if self.readonly:
            raise DeliveryError(f'只读 Delivery 拒绝 {action}：只读入口只能查询（get / status）；'
                                '要写就用可写构造')

    def enqueue(self, exp_id, kind, body=None, *, artifact=None, sha256=None, recipient, event_reply_to=None, request_key, conn=None):
        self._require_write('enqueue（排一条交付）')
        require_text(exp_id, 'exp_id')
        if not re.fullmatch('exp-[A-Za-z0-9][A-Za-z0-9_-]*', exp_id):
            raise DeliveryError('实验号格式错误')
        require_text(recipient, 'recipient')
        require_text(request_key, 'request_key')
        if kind not in KINDS:
            raise DeliveryError('未知交付kind')
        if body is not None and (not isinstance(body, str) or not body.strip()):
            raise DeliveryError('body必须为非空文本')
        if body:
            if any((token != exp_id for token in re.findall('(?<![A-Za-z0-9_-])exp-[A-Za-z0-9][A-Za-z0-9_-]*', body))):
                raise DeliveryError('消息包含其他实验号')
        artifact_path = None
        if artifact is not None:
            artifact_path = str(Path(artifact).resolve())
            if not isinstance(sha256, str) or not re.fullmatch('[0-9a-fA-F]{64}', sha256):
                raise DeliveryError('artifact必须指定sha256')
            sha256 = sha256.lower()
            for component in Path(artifact_path).parts:
                if component.startswith('exp-'):
                    if '.' not in component:
                        valid = component == exp_id
                    else:
                        valid = component in (exp_id + '.html', exp_id + '-paper.html', exp_id + '-conclusion.html', exp_id + '-main.html')
                    if not valid:
                        raise DeliveryError('artifact实验段或文件后缀不符')
            data = Path(artifact_path).read_bytes()
            if not data or hashlib.sha256(data).hexdigest() != sha256:
                raise ArtifactChanged('artifact为空或hash不等于指定版本')
        elif sha256 is not None:
            raise DeliveryError('没有artifact不能指定hash')
        if body is None and artifact_path is None:
            raise DeliveryError('禁止空消息')
        if kind == 'reply' and event_reply_to is None:
            raise DeliveryError('回复必须绑定入向事件')
        if event_reply_to is not None:
            if not isinstance(event_reply_to, dict):
                raise DeliveryError('事件绑定必须包含实验号和收件人')
            require_text(event_reply_to.get('event_id'), 'event_id')
            require_text(event_reply_to.get('source'), 'source')
            if event_reply_to.get('exp_id') != exp_id or event_reply_to.get('recipient') != recipient:
                raise DeliveryError('事件绑定跨实验或跨收件人')
        payload = {'exp_id': exp_id, 'kind': kind, 'body': body, 'artifact': artifact_path, 'sha256': sha256, 'recipient': recipient, 'event_reply_to': event_reply_to}
        text = canonical_json(payload)
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        wire_key = 'hil:' + hashlib.sha256(canonical_json([exp_id, request_key]).encode('utf-8')).hexdigest()

        def insert(connection):
            if not connection.in_transaction:
                raise DeliveryError('enqueue要求活动事务')
            if not connection.execute('SELECT 1 FROM experiments WHERE id=?', (exp_id,)).fetchone():
                raise DeliveryError('实验不存在')
            if event_reply_to is not None:
                if self.event_verifier is None or self.event_verifier(connection, payload) is not True:
                    raise DeliveryError('回复绑定未经可信事件核验')
            old = connection.execute('SELECT * FROM delivery WHERE exp_id=? AND request_key=?', (exp_id, request_key)).fetchone()
            if old:
                if old['payload_json'] != text:
                    raise PayloadConflict('request_key对应不同载荷')
                return dict(old)
            now = self.clock()
            cursor = connection.execute("INSERT INTO delivery\n                (exp_id,kind,request_key,wire_key,payload_json,payload_hash,status,created_at,updated_at)\n                VALUES(?,?,?,?,?,?,'queued',?,?)", (exp_id, kind, request_key, wire_key, text, digest, now, now))
            self.store.emit(connection, exp_id, 'delivery_enqueued', 'delivery', {'delivery_id': cursor.lastrowid, 'kind': kind, 'payload_hash': digest})
            return dict(connection.execute('SELECT * FROM delivery WHERE id=?', (cursor.lastrowid,)).fetchone())
        if conn is not None:
            return insert(conn)
        with self.store.transaction() as connection:
            return insert(connection)

    def get(self, delivery_id):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT * FROM delivery WHERE id=?', (delivery_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def claim(self, worker, *, exp_id=None, lease_seconds=60):
        self._require_write('claim（领取交付任务）')
        require_text(worker, 'worker')
        _duration(lease_seconds)
        now = self.clock()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM delivery WHERE\n                status IN ('queued','leased','sending','unknown')\n                AND (lease_until IS NULL OR lease_until<=?)\n                AND (? IS NULL OR exp_id=?)\n                ORDER BY CASE WHEN status IN ('queued','leased') THEN 0 ELSE 1 END,id\n                LIMIT 1", (now, exp_id, exp_id)).fetchone()
            if row is None:
                return None
            status = 'unknown' if row['status'] in ('sending', 'unknown') else 'leased'
            conn.execute('UPDATE delivery SET status=?,owner=?,fence=fence+1,\n                lease_until=?,updated_at=? WHERE id=?', (status, worker, now + lease_seconds, now, row['id']))
            self.store.emit(conn, row['exp_id'], 'delivery_claimed', worker, {'delivery_id': row['id'], 'fence': row['fence'] + 1, 'status': status})
            return dict(conn.execute('SELECT * FROM delivery WHERE id=?', (row['id'],)).fetchone())

    def _owned(self, conn, claim, statuses):
        row = conn.execute('SELECT * FROM delivery WHERE id=?', (claim['id'],)).fetchone()
        if row is None or row['exp_id'] != claim['exp_id'] or row['owner'] != claim['owner'] or (row['fence'] != claim['fence']) or (row['lease_until'] is None) or (row['lease_until'] <= self.clock()) or (row['status'] not in statuses):
            raise LeaseLost('租约过期、fence失效或任务状态已变')
        return dict(row)

    @staticmethod
    def _artifact(payload):
        if payload['artifact'] is None:
            return None
        try:
            data = Path(payload['artifact']).read_bytes()
        except OSError as exc:
            raise ArtifactChanged('artifact不可读') from exc
        if not data or hashlib.sha256(data).hexdigest() != payload['sha256']:
            raise ArtifactChanged('artifact已变，禁止发送或ACK')
        return data

    def _state(self, claim, status, error, *, release=False):
        with self.store.transaction() as conn:
            row = self._owned(conn, claim, ('leased', 'sending', 'unknown'))
            conn.execute('UPDATE delivery SET status=?,error=?,lease_until=?,updated_at=? WHERE id=?', (status, str(error), None if release else row['lease_until'], self.clock(), row['id']))
            self.store.emit(conn, row['exp_id'], 'delivery_' + status, row['owner'], {'delivery_id': row['id'], 'error': str(error), 'fence': row['fence']})
        return self.get(claim['id'])

    def send(self, claim, adapter: OfflineAdapter):
        self._require_write('send（发送交付）')
        with self.store.transaction() as conn:
            row = self._owned(conn, claim, ('leased',))
            payload = json.loads(row['payload_json'])
            try:
                data = self._artifact(payload)
            except ArtifactChanged as exc:
                conn.execute("UPDATE delivery SET status='blocked',error=?,lease_until=NULL WHERE id=?", (str(exc), row['id']))
                self.store.emit(conn, row['exp_id'], 'delivery_blocked', row['owner'], {'delivery_id': row['id'], 'error': str(exc)})
                return dict(conn.execute('SELECT * FROM delivery WHERE id=?', (row['id'],)).fetchone())
            conn.execute("UPDATE delivery SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?", (self.clock(), row['id']))
            self.store.emit(conn, row['exp_id'], 'delivery_sending', row['owner'], {'delivery_id': row['id'], 'fence': row['fence'], 'request_key': row['wire_key']})
        payload['request_key'] = row['wire_key']
        payload['payload_hash'] = row['payload_hash']
        payload['fence'] = row['fence']
        payload['lease_token'] = canonical_json([row['id'], row['owner'], row['fence']])
        payload['lease_until'] = row['lease_until']
        try:
            receipt = adapter.send(payload, data)
        except NotSent as exc:
            return self._state(claim, 'queued', exc, release=True)
        except Exception as exc:
            return self._state(claim, 'unknown', exc, release=True)
        try:
            self._artifact(payload)
            return self.ack(claim, receipt)
        except (ArtifactChanged, DeliveryError) as exc:
            if isinstance(exc, LeaseLost):
                raise
            return self._state(claim, 'unknown', exc, release=True)

    def ack(self, claim, receipt):
        self._require_write('ack（登记回执）')
        with self.store.transaction() as conn:
            row = self._owned(conn, claim, ('sending', 'unknown'))
            if not isinstance(receipt, Receipt) or not isinstance(receipt.message_id, str) or (not receipt.message_id.strip()) or ((receipt.request_key, receipt.exp_id, receipt.recipient, receipt.payload_hash) != (row['wire_key'], row['exp_id'], json.loads(row['payload_json'])['recipient'], row['payload_hash'])):
                raise DeliveryError('送达凭据为空或不属于此任务')
            if self.receipt_verifier is None or self.receipt_verifier(conn, row, receipt) is not True:
                raise DeliveryError('回执未经可信adapter验证')
            self._artifact(json.loads(row['payload_json']))
            receipt_json = canonical_json(receipt.__dict__)
            conn.execute('INSERT INTO delivery_receipts VALUES(?,?,?,?)', (row['id'], row['exp_id'], receipt_json, self.clock()))
            conn.execute("UPDATE delivery SET status='delivered',message_id=?,error=NULL,lease_until=NULL,updated_at=? WHERE id=?", (receipt.message_id, self.clock(), row['id']))
            self.store.emit(conn, row['exp_id'], 'delivery_acked', row['owner'], {'delivery_id': row['id'], 'kind': row['kind'], 'receipt': receipt.__dict__})
            if self.ack_hook is not None:
                self.ack_hook(conn, row, receipt)
        return self.get(claim['id'])

    def reconcile(self, claim, adapter: OfflineAdapter):
        self._require_write('reconcile（复核并登记交付）')
        with self.store.transaction() as conn:
            row = self._owned(conn, claim, ('unknown',))
        try:
            receipt = adapter.query(row['wire_key'])
        except Exception as exc:
            return self._state(claim, 'unknown', exc, release=True)
        if receipt is None:
            return self._state(claim, 'unknown', '未查到原message，禁止擅自重发', release=True)
        try:
            return self.ack(claim, receipt)
        except (ArtifactChanged, DeliveryError) as exc:
            if isinstance(exc, LeaseLost):
                raise
            return self._state(claim, 'unknown', exc, release=True)

    def status(self, exp_id):
        conn = self.store.connect()
        try:
            rows = [dict(row) for row in conn.execute('SELECT * FROM delivery WHERE exp_id=? ORDER BY id', (exp_id,))]
            return {'exp_id': exp_id, 'scope': 'delivery_only', 'blocked': any((row['status'] != 'delivered' for row in rows)), 'tasks': rows}
        finally:
            conn.close()
