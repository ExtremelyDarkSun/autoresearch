# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 17:10（initialize=False / readonly：跳过 DDL；只读 Inbox 可省 Accounts）
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/inbox.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
#   本轮补：`Inbox(store, accounts=None, delivery, initialize=False/readonly=True)` 跳过建表；
#   只读查询（get / pending_events / pending_questions / question_overview）不需要 Accounts；
#   写入口（receive / classify / apply / drain / claim_question / answer）一律拒绝。
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import math
import time
try:
    from kernel.autoresearch.runtime.store import canonical_json, require_text
    from kernel.autoresearch.runtime.store import is_readonly_store
except ImportError:
    from kernel.autoresearch.runtime.store import canonical_json, require_text
    from kernel.autoresearch.runtime.store import is_readonly_store

class InboxError(ValueError):
    pass

class EventConflict(InboxError):
    pass

class QuestionLeaseLost(InboxError):
    pass

class Inbox:

    def __init__(self, store, accounts=None, delivery=None, authenticate=None, *, clock=time.time,
                 authorize_command=None, initialize=None, readonly=None):
        """入向事件台账。`initialize` / `readonly` 决定构造时要不要建表（默认照旧建）。

        * `readonly=None`（默认）⇒ 按 `store` 自己声明的 `readonly` 判；
        * `initialize=False` ⇒ **跳过 DDL**（表已由别的进程建好）；
        * `readonly=True` ⇒ 跳过 DDL，所有写方法拒绝；**`Accounts` 可以不给**
          （只读查询 `get` / `pending_events` / `pending_questions` / `question_overview` 不需要它）。

        ⛔ 只读构造不建库、不建表、不迁移 schema；读到缺表时报错，不把未知表读成空。
        """
        self.store = store
        self.accounts, self.delivery = (accounts, delivery)
        self.authenticate, self.clock = (authenticate, clock)
        self.authorize_command = authorize_command
        self.readonly = is_readonly_store(store) if readonly is None else bool(readonly)
        if initialize is None:
            initialize = not self.readonly
        if initialize and self.readonly:
            raise InboxError('只读 Inbox 不能初始化建表（initialize 与 readonly 冲突）')
        self.initialize = bool(initialize)
        for label, owner in (('Accounts', accounts), ('Delivery', delivery)):
            if owner is not None and str(owner.store.db_path) != str(store.db_path):
                raise InboxError(f'Inbox、{label}必须共用数据库')
        if not self.readonly and (accounts is None or delivery is None):
            raise InboxError('可写 Inbox 需要 Accounts 与 Delivery；只读查询请用 readonly=True（可省 Accounts）')
        if self.initialize:
            with store.transaction() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS inbox_events(\n                source TEXT NOT NULL,event_id TEXT NOT NULL,exp_id TEXT NOT NULL REFERENCES experiments(id),\n                payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL,\n                category TEXT,principal TEXT,consumed INTEGER NOT NULL DEFAULT 0,\n                result_json TEXT,error TEXT,event_time REAL,received_at REAL NOT NULL,classified_at REAL,applied_at REAL,\n                PRIMARY KEY(source,event_id))')
                conn.execute('CREATE TABLE IF NOT EXISTS inbox_reply_tasks(\n                id INTEGER PRIMARY KEY,source TEXT NOT NULL,event_id TEXT NOT NULL,\n                exp_id TEXT NOT NULL REFERENCES experiments(id),recipient TEXT NOT NULL,\n                status TEXT NOT NULL,owner TEXT,fence INTEGER NOT NULL DEFAULT 0,lease_until REAL,\n                answer TEXT,delivery_id INTEGER REFERENCES delivery(id),\n                UNIQUE(source,event_id),FOREIGN KEY(source,event_id) REFERENCES inbox_events(source,event_id))')

    def _require_write(self, action):
        """只读 Inbox 的写动作一律拒绝：⛔ 不降级、不静默跳过、不伪造事件状态。"""
        if self.readonly:
            raise InboxError(f'只读 Inbox 拒绝 {action}：只读入口只能查询；要写就用可写构造')

    @staticmethod
    def _key(source, event_id):
        return 'inbox:' + hashlib.sha256(canonical_json([source, event_id]).encode('utf-8')).hexdigest()

    def get(self, source, event_id):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT * FROM inbox_events WHERE source=? AND event_id=?', (source, event_id)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def receive(self, source, event, sha256=None):
        self._require_write('receive（落一条入向事件）')
        require_text(source, 'source')
        if not isinstance(event, dict):
            raise InboxError('event必须为JSON对象')
        text = canonical_json(event)
        snapshot = json.loads(text)
        for field in ('event_id', 'exp_id'):
            require_text(snapshot.get(field), field)
        if 'source' in snapshot and snapshot['source'] != source:
            raise InboxError('事件source不符')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if sha256 is not None and sha256 != digest:
            raise EventConflict('原事件hash不符')
        event_time = snapshot.get('create_time')
        if type(event_time) not in (int, float) or not math.isfinite(event_time) or event_time <= 0:
            event_time = None
        with self.store.transaction() as conn:
            old = conn.execute('SELECT * FROM inbox_events WHERE source=? AND event_id=?', (source, snapshot['event_id'])).fetchone()
            if old:
                if old['payload_json'] != text:
                    raise EventConflict('事件ID对应不同原事件')
                return dict(old)
            conn.execute("INSERT INTO inbox_events\n                (source,event_id,exp_id,payload_json,payload_hash,status,event_time,received_at)\n                VALUES(?,?,?,?,?,'received',?,?)", (source, snapshot['event_id'], snapshot['exp_id'], text, digest, event_time, self.clock()))
            self.store.emit(conn, snapshot['exp_id'], 'inbox_received', 'inbox', {'source': source, 'event_id': snapshot['event_id'], 'hash': digest})
        return self.get(source, snapshot['event_id'])

    def _verify(self, source, event):
        if self.authenticate is None:
            raise InboxError('未配置可信authenticate，禁止应用')
        snapshot = json.loads(canonical_json(event))
        principal = self.authenticate(source, snapshot)
        if canonical_json(snapshot) != canonical_json(event):
            raise InboxError('authenticate不能修改原事件')
        require_text(principal, 'authenticated principal')
        return principal

    def _mark(self, source, event_id, status, *, error=None, category=None, principal=None):
        with self.store.transaction() as conn:
            row = conn.execute('SELECT * FROM inbox_events WHERE source=? AND event_id=?', (source, event_id)).fetchone()
            if row is None:
                raise InboxError('事件不存在')
            if row['consumed']:
                return dict(row)
            conn.execute('UPDATE inbox_events SET status=?,error=?,category=coalesce(?,category),\n                principal=coalesce(?,principal),classified_at=coalesce(classified_at,?)\n                WHERE source=? AND event_id=?', (status, error, category, principal, self.clock(), source, event_id))
            self.store.emit(conn, row['exp_id'], 'inbox_' + status, 'inbox', {'source': source, 'event_id': event_id, 'error': error, 'category': category})
        return self.get(source, event_id)

    def classify(self, source, event_id):
        self._require_write('classify（改事件分类）')
        row = self.get(source, event_id)
        if row is None:
            raise InboxError('事件不存在')
        if row['consumed']:
            return row
        event = json.loads(row['payload_json'])
        try:
            principal = self._verify(source, event)
            if row['event_time'] is None:
                raise InboxError('事件发生时间不可核，禁止执行')
            category = event.get('intent', 'other')
            if category not in ('authorize', 'reject', 'stop', 'question', 'late'):
                category = 'other'
            if category in ('stop', 'reject'):
                scope = event.get('scope')
                if scope not in ('experiment', 'global'):
                    raise InboxError('命令必须明确experiment或global范围')
                if self.authorize_command is None:
                    raise InboxError('未配置可信命令权限，禁止执行')
                command_event = json.loads(canonical_json(event))
                permitted = self.authorize_command(principal, category, scope, command_event)
                if permitted is not True or canonical_json(command_event) != canonical_json(event):
                    raise InboxError('命令权限不成立或校验器修改事件')
            if category == 'question':
                require_text(event.get('body'), 'question body')
                require_text(event.get('recipient'), 'recipient')
        except Exception as exc:
            return self._mark(source, event_id, 'rejected', error=str(exc))
        return self._mark(source, event_id, 'classified', category=category, principal=principal)

    def apply(self, source, event_id, *, command_handler=None):
        self._require_write('apply（应用入向事件）')
        row = self.get(source, event_id)
        if row is None:
            raise InboxError('事件不存在')
        if row['consumed']:
            return row
        row = self.classify(source, event_id)
        if row['status'] == 'rejected':
            return row
        event = json.loads(row['payload_json'])
        category = row['category']
        key = self._key(source, event_id)
        if category == 'authorize':
            self._mark(source, event_id, 'applying')
            try:
                result = self.accounts.authorize(source, event, key)
            except Exception as exc:
                return self._mark(source, event_id, 'retryable', error=str(exc))
            return self._finish(source, event_id, result=result)
        if category in ('stop', 'reject') and command_handler is None:
            return self._mark(source, event_id, 'command_pending', error='没有可信命令handler，尚未执行停止或否决')
        if category == 'other':
            return self._mark(source, event_id, 'command_pending', error='未知意图，需要主会话处理')
        try:
            return self._finish(source, event_id, command_handler=command_handler)
        except Exception as exc:
            return self._mark(source, event_id, 'retryable', error=str(exc))

    def _finish(self, source, event_id, *, result=None, command_handler=None):
        with self.store.transaction() as conn:
            row = conn.execute('SELECT * FROM inbox_events WHERE source=? AND event_id=?', (source, event_id)).fetchone()
            if row['consumed']:
                return dict(row)
            event = json.loads(row['payload_json'])
            category = row['category']
            if category == 'question':
                conn.execute("INSERT OR IGNORE INTO inbox_reply_tasks\n                    (source,event_id,exp_id,recipient,status) VALUES(?,?,?,?,'pending')", (source, event_id, row['exp_id'], event['recipient']))
                result = {'answer_status': 'pending', 'answered': False}
            elif category in ('stop', 'reject'):
                result = command_handler(conn, event, self._key(source, event_id))
                if not isinstance(result, dict) or result.get('applied') is not True:
                    raise InboxError('命令handler未返回applied=true')
                if category == 'reject':
                    require_text(result.get('decision_id'), 'decision_id')
                    if type(result.get('version')) is not int or result['version'] < 0:
                        raise InboxError('业务否决必须记录决策版本')
            elif category == 'late':
                result = {'recorded': category, 'authorization_applied': False}
            conn.execute("UPDATE inbox_events SET status='applied',consumed=1,result_json=?,\n                error=NULL,applied_at=? WHERE source=? AND event_id=?", (canonical_json(result), self.clock(), source, event_id))
            self.store.emit(conn, row['exp_id'], 'inbox_applied', row['principal'], {'source': source, 'event_id': event_id, 'category': category, 'result': result})
        return self.get(source, event_id)

    def pending_events(self, exp_id=None):
        conn = self.store.connect()
        try:
            return [dict(row) for row in conn.execute('SELECT * FROM inbox_events WHERE consumed=0\n                AND (? IS NULL OR exp_id=?) ORDER BY event_time IS NULL,event_time,event_id,source', (exp_id, exp_id))]
        finally:
            conn.close()

    def drain(self, *, exp_id=None, command_handler=None, limit=100):
        self._require_write('drain（批量应用入向事件）')
        if type(limit) is not int or limit <= 0:
            raise InboxError('limit必须为正整数')
        results = []
        for row in self.pending_events(exp_id)[:limit]:
            result = self.apply(row['source'], row['event_id'], command_handler=command_handler)
            results.append(result)
            if result['status'] in ('retryable', 'command_pending', 'applying'):
                break
        return results

    def pending_questions(self, exp_id=None):
        conn = self.store.connect()
        try:
            return [dict(row) for row in conn.execute("SELECT t.*,e.payload_json,d.status AS delivery_status,\n                CASE WHEN d.status='delivered' THEN 'answered' ELSE t.status END AS answer_status\n                FROM inbox_reply_tasks t\n                JOIN inbox_events e ON e.source=t.source AND e.event_id=t.event_id\n                LEFT JOIN delivery d ON d.id=t.delivery_id\n                WHERE (d.status IS NULL OR d.status!='delivered')\n                AND (? IS NULL OR t.exp_id=?) ORDER BY e.event_time,e.event_id,t.id", (exp_id, exp_id))]
        finally:
            conn.close()

    def question_overview(self, exp_id=None):
        conn = self.store.connect()
        try:
            return [dict(row) for row in conn.execute("SELECT t.*,d.status AS delivery_status,\n                CASE WHEN d.status='delivered' THEN 'answered' ELSE t.status END AS answer_status\n                FROM inbox_reply_tasks t LEFT JOIN delivery d ON d.id=t.delivery_id\n                WHERE (? IS NULL OR t.exp_id=?) ORDER BY t.id", (exp_id, exp_id))]
        finally:
            conn.close()

    def claim_question(self, worker, *, exp_id=None, lease_seconds=300):
        self._require_write('claim_question（领取回复任务）')
        require_text(worker, 'worker')
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise InboxError('lease_seconds必须为正有限数')
        with self.store.transaction() as conn:
            row = conn.execute("SELECT t.* FROM inbox_reply_tasks t JOIN inbox_events e\n                ON e.source=t.source AND e.event_id=t.event_id\n                WHERE t.status IN ('pending','leased')\n                AND (t.lease_until IS NULL OR t.lease_until<=?) AND (? IS NULL OR t.exp_id=?)\n                ORDER BY e.event_time,e.event_id,t.id LIMIT 1", (self.clock(), exp_id, exp_id)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE inbox_reply_tasks SET status='leased',owner=?,fence=fence+1,lease_until=? WHERE id=?", (worker, self.clock() + lease_seconds, row['id']))
            return dict(conn.execute('SELECT * FROM inbox_reply_tasks WHERE id=?', (row['id'],)).fetchone())

    def verify_reply_event(self, conn, payload):
        binding = payload.get('event_reply_to') or {}
        row = conn.execute('SELECT * FROM inbox_events WHERE source=? AND event_id=?', (binding.get('source'), binding.get('event_id'))).fetchone()
        if row is None or not row['consumed'] or row['category'] != 'question':
            return False
        event = json.loads(row['payload_json'])
        try:
            self._verify(row['source'], event)
        except Exception:
            return False
        return row['exp_id'] == payload['exp_id'] == binding.get('exp_id') and event.get('recipient') == payload['recipient'] == binding.get('recipient')

    def answer(self, claim, body):
        self._require_write('answer（排一条文字回复）')
        require_text(body, 'answer body')
        if body.strip() in ('已收到', '收到', '已收到。', '收到。'):
            raise InboxError('收到不是问题答案')
        with self.store.transaction() as conn:
            row = conn.execute('SELECT * FROM inbox_reply_tasks WHERE id=?', (claim['id'],)).fetchone()
            if row is None or row['exp_id'] != claim['exp_id'] or row['status'] != 'leased' or (row['owner'] != claim['owner']) or (row['fence'] != claim['fence']) or (row['lease_until'] <= self.clock()):
                raise QuestionLeaseLost('问答租约过期或fence失效')
            binding = {'source': row['source'], 'event_id': row['event_id'], 'exp_id': row['exp_id'], 'recipient': row['recipient']}
            task = self.delivery.enqueue(row['exp_id'], 'reply', body=f"答你的提问（本轮 {row['exp_id']}）：\n{body}", recipient=row['recipient'], event_reply_to=binding, request_key=self._key(row['source'], row['event_id']) + ':answer', conn=conn)
            conn.execute("UPDATE inbox_reply_tasks SET status='queued',answer=?,delivery_id=?,lease_until=NULL WHERE id=?", (body, task['id'], row['id']))
            self.store.emit(conn, row['exp_id'], 'inbox_answer_queued', row['owner'], {'source': row['source'], 'event_id': row['event_id'], 'delivery_id': task['id']})
            return task
