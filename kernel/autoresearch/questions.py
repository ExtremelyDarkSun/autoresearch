# GENERATED-BY: 普通向用户提问台账（E7）迁移，2026-11-09
# LAST-EDIT: 2026-11-09 首版：ask / open / show / answer（认证入向）/ declare（仅 declared）/ enqueue / link / expire / cancel
# PURPOSE: 把「须向人提问的事项」落盘并关联实验、负责人、待答内容、deadline 与可信回复来源，不建审批页、不强制任何通道。
# ORIGIN: framework_expectations E7（+ E2 的实验关联、F7 的提问来源、F9 的答复身份核对）；复用 runtime.Inbox / runtime.Delivery
# CUMULATIVE-DELTA: 首版。同库 `.autoresearch/runtime.sqlite3` 建 `questions` / `question_events`，
#   ⛔ 不另造第二本账：入向事件只读现 `Inbox`（含 `authenticate` 的身份核验），外发只走现 `Delivery`。
#   本轮补：① `blocking`（默认 False：问问题不等于停等）；② 答复必须由已认证内容指出 `question_id`
#   （payload 字段 / 正文 `[question:Q-…]` 标记 / 回复线程），缺号错号歧义全拒；③ 答复时间取入向
#   事件自带的 epoch 秒且必须晚于 `asked_at`（⛔ 不用本地 received_at 顶替）；④ 通道/鉴权异常只报类型，
#   ⛔ 不把 `str(exc)` 拼进正文。

"""普通向用户提问的落盘台账（E7）。

为什么不是复用现有 Inbox
=======================

`runtime.inbox` 的 questions 是**反方向**的：`inbox_reply_tasks` / `pending_questions` /
`claim_question` / `answer` 记的是「**用户问我们、我们回**」。它答不了「**我们要问用户**」：

* 我们发出的提问没有入向事件可以挂（`Delivery.kind='reply'` 必须绑一条入向事件）；
* 用户的答复是**新的入向事件**，不是那条回复任务。

所以这里新建一张表 —— 但只有**一本账**：入向读现 `Inbox`（`get` + `authenticate` 的
`principal`），外发写现 `Delivery`（`kind='notice'`），身份核验、收件人、实验号都沿用
那边已有的判据。⛔ 不复制一套身份或送达逻辑。

它不是什么
==========

* **不是审批**：不生成审批页、不发选择卡、不消费短码、不批准任何执行；
  `enqueue` 只把待答内容排进交付台账，通道由部署配置，⛔ 不强制任何即时通讯工具。
* **不是 `declared` 冒充 `verified` 的通道**：没有可信 `authenticate` 时，手工文字只能
  `declare`（记为 `verification='declared'`，状态仍是 `open`），⛔ 不能写成用户已回答。
* **不改写历史**：状态变化全部是 `question_events` 追加行（`BEFORE UPDATE/DELETE` 触发器
  挡住改写）；旧表（没有 `exp_id` 列）只读投影成 `exp_link='unknown'`，⛔ 不猜、不迁移。

状态
====

    open --answer(认证入向事件)--> answered
    open --deadline 过期 + expire--> expired
    open --cancel(给理由)--> cancelled
    open --declare(手工文字)--> open（只多一条 declared 记录，⛔ 不改状态）

入向答复只认 `Inbox` 里**已通过可信 `authenticate`** 的事件：`principal` 非空、
状态不是 `received`/`rejected`、实验号与收件人跟问题一致、类别不是
`authorize`/`reject`/`stop`（审批与停机不走这里）。⛔ 伪造身份、错实验号、错收件人、
同一事件答两个问题、已回答后给不同答案，全部拒。

**「答的是哪一条问题」必须由已认证内容指出**（`--question-id` 由调用方给不算证据）：
payload 里的 `question_id` 字段、正文里的 `[question:Q-000001]`（或一个裸 `Q-000001`）、
或 `reply_to_message_id` 等于我们这条问题已送达消息的 `message_id`。缺号 / 错号 / 多号歧义 ⇒ 拒，
且在写任何状态之前拒。

`blocking`
=========

问用户问题**不代表整个实验要停等**。`blocking=False`（默认）只是把问题记下来：
超期只进诊断（`diagnostics`），不是「用户欠我们回复」，也不阻断已授权动作。只有
**明确依赖其答复才能继续**的动作才 `blocking=True`；那种问题超期才进 `problems`。
无 deadline 的 open 问题永远不会自动过期 / 自动取消。
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from kernel.autoresearch.runtime.delivery import Delivery, DeliveryError
from kernel.autoresearch.runtime.inbox import Inbox, InboxError
from kernel.autoresearch.runtime.store import ReadonlyStore, Store, StoreError

STATUSES = ('open', 'answered', 'expired', 'cancelled')
#: 入向事件里可以当「答复」的类别。⛔ 审批（authorize）、否决（reject）、停机（stop）不算答复。
ANSWER_CATEGORIES = (None, 'other', 'question', 'late')
#: 已经通过 `authenticate` 的状态；`received`（还没核身份）与 `rejected`（核失败）都不算。
AUTHENTICATED_STATES = ('classified', 'applied', 'command_pending')
#: 和 `runtime.delivery.Delivery.enqueue` 对 `exp_id` 的要求一致：早失败，别等到外发才报。
EXP_RE = re.compile(r'exp-[A-Za-z0-9][A-Za-z0-9_-]*')
QUESTION_RE = re.compile(r'Q-[0-9]{6}')
#: 答复里**明确指出答的是哪个问题**的两种可复制写法（真通道上用户能自己打出来）。
MARKER_RE = re.compile(r'\[\s*question\s*:\s*(Q-[0-9]{6})\s*\]', re.IGNORECASE)
BARE_RE = re.compile(r'(?<![A-Za-z0-9_-])(Q-[0-9]{6})(?![0-9])')
#: 外发正文里那句可复制的回复标记（`enqueue` 会原样发出去）。
REPLY_HINT = '回复时请保留开头标记：[question:{question_id}]'

REQUIRED_TABLES = ('questions', 'question_events')
#: 当前 schema 的列。旧表缺列 ⇒ 只读投影 unknown，⛔ 不 ALTER、不补默认值。
REQUIRED_COLUMNS = ('question_id', 'exp_id', 'topic', 'asked_by', 'owner', 'body', 'recipient',
                    'deadline', 'blocking', 'status', 'asked_at', 'updated_at', 'answered_by',
                    'answer_source', 'answer_event', 'request_key', 'delivery_id',
                    'verification', 'legacy')

DECLARED_NOTE = ('这是**手工文字**，没有可信入向事件支撑 ⇒ 只算 declared；'
                 '⛔ 不能当成用户已回答，状态仍是 open')


class QuestionError(ValueError):
    """**可判**的规则拒绝（状态不对、身份不可信、重放矛盾、只读写）。"""


class QuestionConflict(QuestionError):
    """同一问题已有不同结论，或同一条入向事件被复用（重放矛盾）。"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _token(value, label, *, limit=128):
    if not _text(value):
        raise QuestionError(f'须给{label}')
    text = value.strip()
    if any(char.isspace() for char in text):
        raise QuestionError(f'{label}不能含空白（只写标识本身，不要塞说明）')
    if len(text) > limit:
        raise QuestionError(f'{label}过长（>{limit} 字符）')
    return text


def _exp(value):
    text = _token(value, '实验号')
    if not EXP_RE.fullmatch(text):
        raise QuestionError(f'实验号形态错误（应形如 exp-01）：{text}')
    return text


def _deadline(value):
    """`None` ⇒ 没有 deadline（诚实 open，⛔ 不硬塞一个默认时限）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise QuestionError('deadline 只接受 epoch 秒数或 ISO 8601 时间串')
    if isinstance(value, (int, float)):
        stamp = float(value)
    elif _text(value):
        text = value.strip()
        try:
            stamp = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            except ValueError:
                raise QuestionError(f'deadline 不是 epoch 秒数也不是 ISO 8601：{value!r}') from None
            if parsed.tzinfo is None:
                raise QuestionError('deadline 的 ISO 时间必须带时区（否则不判超期）')
            stamp = parsed.timestamp()
    else:
        raise QuestionError('deadline 只接受 epoch 秒数或 ISO 8601 时间串')
    if stamp != stamp or stamp in (float('inf'), float('-inf')) or stamp <= 0:
        raise QuestionError('deadline 必须是有限正时间戳')
    return stamp


class Questions:
    """一个工作区（可选 `db`）上的提问台账。读写模式由 `store` / `readonly` 决定。

    * `readonly=False`（默认）⇒ 建 `questions` / `question_events` 两张表（`IF NOT EXISTS`），
      写动作走 `BEGIN IMMEDIATE` 事务；
    * `readonly=True` ⇒ 用 `ReadonlyStore`：⛔ 不建库、不建目录、不建表、不迁移 schema；
      读不到就报「不可判」，⛔ 不读成「没有问题」。

    入向与交付子对象（`Inbox` / `Delivery`）与台账**共用同一个数据库**；只读模式构造的
    子对象也是只读的。
    """

    def __init__(self, workspace, *, db=None, store=None, delivery=None, inbox=None,
                 authenticate=None, clock=time.time, readonly=False, initialize=True):
        self.root = Path(workspace).resolve()
        self.db = Path(db).resolve() if db is not None else (self.root / '.autoresearch' / 'runtime.sqlite3')
        self.clock = clock
        declared_readonly = bool(readonly) or bool(getattr(store, 'readonly', False))
        if not declared_readonly and self.db.is_file():
            # 先核旧表再构造任何写子对象：⛔ 诊断一个旧 schema 不许顺手建别的表。
            legacy = self._legacy_problem()
            if legacy:
                raise QuestionError(legacy)
        if store is not None:
            if str(Path(store.db_path).resolve()) != str(self.db):
                raise QuestionError('Questions 与传入的 store 必须指向同一个数据库')
            self.store = store
        elif declared_readonly:
            self.store = ReadonlyStore(self.db)
        else:
            if not (self.root / '.autoresearch').is_dir():
                raise QuestionError('工作区未初始化（缺 .autoresearch/）：先运行 init')
            self.store = Store(self.db)
        self.readonly = declared_readonly or bool(getattr(self.store, 'readonly', False))
        self.authenticate = authenticate
        if delivery is not None and str(Path(delivery.store.db_path).resolve()) != str(self.db):
            raise QuestionError('Delivery 必须与提问台账共用数据库')
        if inbox is not None and str(Path(inbox.store.db_path).resolve()) != str(self.db):
            raise QuestionError('Inbox 必须与提问台账共用数据库')
        if delivery is None:
            # 写模式让它建交付表（`enqueue` 要用）；只读模式跳过 DDL 并拒绝写。
            delivery = Delivery(self.store, readonly=self.readonly)
        if inbox is None:
            # 入向不归本层写：只拿一个只读视图去查「已认证事件」，⛔ 不建 `inbox_events`、不造事件。
            inbox = Inbox(self.store, None, delivery, authenticate=authenticate,
                          readonly=True)
        self.delivery = delivery
        self.inbox = inbox
        if self.readonly:
            return
        if initialize:
            self._ensure_schema()

    # -- schema -------------------------------------------------------------

    def _legacy_problem(self):
        """旧 `questions` 表缺当前 schema 的列 ⇒ 返回原因（读用 `mode=ro`，⛔ 不建不迁）。"""
        if not self.db.is_file():
            return None
        try:
            conn = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)
        except sqlite3.Error as exc:
            return f'状态库打不开（{type(exc).__name__}: {exc}）⇒ 不可判'
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'questions' not in tables:
                return None
            present = {row[1] for row in conn.execute('PRAGMA table_info("questions")')}
        except sqlite3.Error as exc:
            return f'状态库不可读（{type(exc).__name__}: {exc}）⇒ 不可判'
        finally:
            conn.close()
        absent = [column for column in REQUIRED_COLUMNS if column not in present]
        if not absent:
            return None
        return ('问题台账是旧 schema（缺列：' + '、'.join(absent)
                + '）⇒ 本层不静默 ALTER；旧记录只能只读投影成 unknown，'
                  '要写请先按当前 schema 迁移')

    def _ensure_schema(self):
        with closing(sqlite3.connect(self.db, timeout=30)) as conn:
            # 先核旧表：缺列 ⇒ 一个 DDL 都不执行（⛔ 不静默 ALTER、⛔ 不顺手建事件表）。
            present_tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'questions' in present_tables:
                present = {row[1] for row in conn.execute('PRAGMA table_info("questions")')}
                absent = [column for column in REQUIRED_COLUMNS if column not in present]
                if absent:
                    raise QuestionError(
                        '问题台账是旧 schema（缺列：' + '、'.join(absent)
                        + '）⇒ 本层不静默 ALTER；旧记录只能只读投影成 unknown，'
                          '要写请先按当前 schema 迁移')
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS questions(\n'
                    ' question_id TEXT PRIMARY KEY, exp_id TEXT, topic TEXT,\n'
                    ' asked_by TEXT NOT NULL, owner TEXT NOT NULL, body TEXT NOT NULL,\n'
                    ' recipient TEXT, deadline REAL, blocking INTEGER NOT NULL DEFAULT 0,\n'
                    ' status TEXT NOT NULL,\n'
                    ' asked_at REAL NOT NULL, updated_at REAL NOT NULL,\n'
                    ' answered_by TEXT, answer_source TEXT, answer_event TEXT,\n'
                    ' request_key TEXT, delivery_id INTEGER,\n'
                    ' verification TEXT, legacy INTEGER NOT NULL DEFAULT 0)')
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS question_events(\n'
                    ' id INTEGER PRIMARY KEY, question_id TEXT NOT NULL,\n'
                    ' kind TEXT NOT NULL, actor TEXT NOT NULL,\n'
                    ' from_state TEXT, to_state TEXT, payload_json TEXT NOT NULL,\n'
                    ' at REAL NOT NULL)')
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(
                        f'CREATE TRIGGER IF NOT EXISTS question_events_no_{operation.lower()} '
                        f'BEFORE {operation} ON question_events BEGIN '
                        f"SELECT RAISE(ABORT,'question events are append-only'); END")
                present = {row[1] for row in conn.execute('PRAGMA table_info("questions")')}
                absent = [column for column in REQUIRED_COLUMNS if column not in present]
                if absent:
                    raise QuestionError(
                        '问题台账是旧 schema（缺列：' + '、'.join(absent)
                        + '）⇒ 本层不静默 ALTER；旧记录只能只读投影成 unknown，'
                          '要写请先按当前 schema 迁移')
                conn.execute('COMMIT')
            except BaseException:
                if conn.in_transaction:
                    conn.execute('ROLLBACK')
                raise

    # -- 只读判据 -----------------------------------------------------------

    def _read_only_problem(self):
        """只读模式下「为什么读不了」；可读时返回 None。⛔ 缺库不建、缺表不迁。"""
        if not self.readonly:
            return None
        if not self.db.is_file():
            return (f'状态库不存在：{self.db}（只读模式不建库）⇒ 不可判；'
                    '先运行 init / ask 建库')
        try:
            schema = self.store.schema()
        except (StoreError, sqlite3.Error) as exc:
            return f'状态库不可读（{type(exc).__name__}: {exc}）⇒ 不可判'
        if 'questions' not in schema:
            return ('状态库没有提问台账表 questions（只读模式不建表）⇒ 不可判；'
                    '这条库不是当前版本，先建表再查')
        absent = [column for column in REQUIRED_COLUMNS if column not in schema['questions']]
        if absent:
            # 旧 schema：读得到行，但 exp / deadline 等列不可判 ⇒ 投影 unknown（⛔ 不报绿、不迁移）。
            return None
        if 'question_events' not in schema:
            return ('状态库没有 question_events 表（只读模式不建表）⇒ 不可判；'
                    '历史状态无法重算')
        return None

    def _legacy_columns(self):
        """当前库的 `questions` 列；读不到 ⇒ 空集合（按旧 schema 处理）。"""
        try:
            return self.store.schema().get('questions', frozenset())
        except (StoreError, sqlite3.Error):
            return frozenset()

    # -- 事务 ---------------------------------------------------------------

    def _write(self, operation):
        if self.readonly:
            raise QuestionError('只读提问台账：写动作一律拒绝')
        with closing(sqlite3.connect(self.db, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            try:
                result = operation(conn)
                conn.execute('COMMIT')
                return result
            except BaseException:
                if conn.in_transaction:
                    conn.execute('ROLLBACK')
                raise

    def _append(self, conn, question_id, kind, actor, payload, *, from_state, to_state):
        moment = float(self.clock())
        conn.execute('INSERT INTO question_events(question_id,kind,actor,from_state,to_state,'
                     'payload_json,at) VALUES(?,?,?,?,?,?,?)',
                     (question_id, kind, actor, from_state, to_state, canonical(payload), moment))
        return moment

    @staticmethod
    def _row(conn, question_id):
        row = conn.execute('SELECT * FROM questions WHERE question_id=?',
                           (str(question_id),)).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _next_id(conn):
        used = {row[0] for row in conn.execute(
            "SELECT question_id FROM questions WHERE question_id LIKE 'Q-%'")}
        index = len(used) + 1
        while f'Q-{index:06d}' in used:
            index += 1
        return f'Q-{index:06d}'

    def _evidence(self, sources):
        """源码证据：工作区内相对路径 + locator + 真实字节哈希。⛔ 给了就必须是真的。"""
        if not sources:
            return []
        if not isinstance(sources, list):
            raise QuestionError('证据须是数组（不给就传 None / 空数组）')
        rows = []
        for source in sources:
            if not isinstance(source, dict) or not _text(source.get('locator')):
                raise QuestionError('证据须给 path 与 locator')
            path = Path(str(source.get('path') or ''))
            target = (self.root / path).resolve() if not path.is_absolute() else path.resolve()
            if target == self.root or not target.is_relative_to(self.root):
                raise QuestionError('证据路径越出工作区')
            if not target.is_file():
                raise QuestionError(f'证据文件不存在：{source.get("path")}')
            data = target.read_bytes()
            rows.append({'path': target.relative_to(self.root).as_posix(),
                         'locator': str(source['locator']).strip(),
                         'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        return rows

    # -- 创建 ---------------------------------------------------------------

    def ask(self, exp_id, body, *, owner, asked_by, question_id=None, topic=None,
            recipient=None, deadline=None, blocking=False, note=None):
        """落一条**待答问题**。返回 `{question_id, status:'open', ...}`。

        * `exp_id` 必填：提问必须关联实验（E2）；
        * `owner` 必填：谁负责拿到答复；
        * `deadline` 可选：⛔ 不给就没有 deadline（诚实 open），不硬塞默认时限；没有 deadline 的
          open 问题**永远不会**自动过期或被自动取消（只有显式 `expire` / `cancel` 能关）；
        * `blocking` 默认 **False**：问了普通问题**不代表整个实验要停等**。只有「这一轮明确
          依赖这条答复才继续」的动作才 `blocking=True`；⛔ 别把它当成默认值；
        * ⛔ 不建审批页、不发卡、不批准任何东西；要不要发出去由 `enqueue` 显式决定。
        """
        experiment = _exp(exp_id)
        if not _text(body):
            raise QuestionError('须给待答内容（body）')
        text = body.strip()
        if len(text) > 4000:
            raise QuestionError('待答内容过长（>4000 字符）：分成多个问题')
        if not _text(owner):
            raise QuestionError('须给负责人（owner）：谁负责拿到答复')
        if not _text(asked_by):
            raise QuestionError('须给提问方（asked_by）')
        if type(blocking) is not bool:
            raise QuestionError('blocking 必须是布尔值：只有明确依赖答复的动作才设 True')
        if topic is not None:
            topic = _token(topic, '专题号')
        if recipient is not None:
            recipient = _token(recipient, '收件人')
        when = _deadline(deadline)
        identity = None if question_id is None else _token(question_id, '问题号')
        if identity is not None and not QUESTION_RE.fullmatch(identity):
            raise QuestionError(f'问题号形态错误（应形如 Q-000001）：{identity}')
        moment = float(self.clock())

        def op(conn):
            resolved = identity or self._next_id(conn)
            if self._row(conn, resolved) is not None:
                raise QuestionConflict(f'问题号 {resolved} 已存在：换一个编号，⛔ 不覆盖')
            conn.execute('INSERT INTO questions(question_id,exp_id,topic,asked_by,owner,body,'
                         'recipient,deadline,blocking,status,asked_at,updated_at,legacy)'
                         " VALUES(?,?,?,?,?,?,?,?,?,'open',?,?,0)",
                         (resolved, experiment, topic, asked_by.strip(), owner.strip(), text,
                          recipient, when, 1 if blocking else 0, moment, moment))
            self._append(conn, resolved, 'asked', asked_by.strip(),
                         {'exp_id': experiment, 'owner': owner.strip(), 'body': text,
                          'recipient': recipient, 'deadline': when, 'topic': topic,
                          'blocking': blocking, 'verified_reply_source': None,
                          'reply_hint': REPLY_HINT.format(question_id=resolved),
                          'note': DECLARED_NOTE},
                         from_state=None, to_state='open')
            return self._view(conn, resolved)

        return self._write(op)

    # -- 读 -----------------------------------------------------------------

    def _events(self, conn, question_id):
        try:
            rows = conn.execute('SELECT * FROM question_events WHERE question_id=? ORDER BY id',
                                (str(question_id),)).fetchall()
        except sqlite3.Error:
            # 旧 schema 没有事件表：状态只能按行投影（`legacy_schema=True`），⛔ 不建表补齐。
            return []
        return [{'seq': row['id'], 'kind': row['kind'], 'actor': row['actor'],
                 'from': row['from_state'], 'to': row['to_state'],
                 'payload': json.loads(row['payload_json']), 'at': row['at']} for row in rows]

    def _delivery_status(self, conn, delivery_id):
        if delivery_id is None:
            return None
        try:
            row = conn.execute('SELECT status,message_id FROM delivery WHERE id=?',
                               (int(delivery_id),)).fetchone()
        except sqlite3.Error:
            return None
        return None if row is None else {'status': row['status'], 'message_id': row['message_id']}

    def _view(self, conn, question_id):
        row = self._row(conn, question_id)
        if row is None:
            raise QuestionError(f'问题不存在：{question_id}')
        columns = self._legacy_columns() if self.readonly else {
            info[1] for info in conn.execute('PRAGMA table_info("questions")')}
        legacy_schema = any(column not in columns for column in ('exp_id', 'deadline', 'topic'))
        events = self._events(conn, question_id)
        links = [event for event in events if event['kind'] == 'linked_exp']
        declared = [event for event in events if event['kind'] == 'declared_answer']
        answered = [event for event in events if event['kind'] == 'answered']
        raw_exp = None if legacy_schema else row.get('exp_id')
        primary = raw_exp.strip() if isinstance(raw_exp, str) and raw_exp.strip() else None
        if primary is None:
            exp_link = 'unknown' if legacy_schema or (row.get('legacy') or 0) else 'unlinked'
        else:
            exp_link = 'linked'
        effective = {value for value in
                     [primary] + [event['payload'].get('linked_exp') for event in links]
                     if isinstance(value, str) and value.strip()}
        status = row.get('status')
        if status not in STATUSES:
            status = 'unknown'
        deadline = None if legacy_schema else row.get('deadline')
        overdue = bool(deadline and status == 'open' and float(deadline) <= float(self.clock()))
        return {
            'question_id': row['question_id'],
            'exp_id': primary,
            'exp_link': exp_link,
            'effective_exps': sorted(effective),
            'exp_links': [{'seq': event['seq'], 'exp_id': event['payload'].get('linked_exp'),
                           'actor': event['actor'], 'reason': event['payload'].get('text'),
                           'at': event['at']} for event in links],
            'topic': None if legacy_schema else row.get('topic'),
            'asked_by': row.get('asked_by'),
            'owner': row.get('owner'),
            'body': row.get('body'),
            'recipient': None if legacy_schema else row.get('recipient'),
            'deadline': deadline,
            'deadline_at': (None if deadline is None else
                            datetime.fromtimestamp(float(deadline)).astimezone().isoformat()),
            'deadline_overdue': overdue,
            'blocking': False if legacy_schema else bool(row.get('blocking')),
            'reply_hint': REPLY_HINT.format(question_id=row['question_id']),
            'status': status,
            'asked_at': row.get('asked_at'),
            'updated_at': row.get('updated_at'),
            'answered_by': row.get('answered_by'),
            'answer_source': row.get('answer_source'),
            'verification': row.get('verification'),
            'answer_event': (None if legacy_schema else row.get('answer_event')),
            'delivery_id': None if legacy_schema else row.get('delivery_id'),
            'delivery': None if legacy_schema else self._delivery_status(conn, row.get('delivery_id')),
            'request_key': None if legacy_schema else row.get('request_key'),
            'declared_only': bool(declared) and not answered and status != 'answered',
            'declared_answers': [{'seq': event['seq'], 'actor': event['actor'],
                                  'text': event['payload'].get('text'),
                                  'note': event['payload'].get('note'),
                                  'sources': event['payload'].get('sources'),
                                  'verification': event['payload'].get('verification'),
                                  'at': event['at']} for event in declared],
            'answered_events': [{'seq': event['seq'], 'actor': event['actor'],
                                 'principal': event['payload'].get('principal'),
                                 'source': event['payload'].get('source'),
                                 'event_id': event['payload'].get('event_id'),
                                 'category': event['payload'].get('category'),
                                 'text': event['payload'].get('text'),
                                 'binding': event['payload'].get('question_binding'),
                                 'note': event['payload'].get('note'),
                                 'at': event['at']} for event in answered],
            'events': events,
            'legacy_schema': legacy_schema,
        }

    def show(self, question_id):
        """只读：一条问题的完整视图（含事件、declared、认证答复与交付状态）。"""
        problem = self._read_only_problem()
        if problem:
            raise QuestionError(problem)
        conn = self.store.connect()
        try:
            return self._view(conn, question_id)
        finally:
            conn.close()

    def open_questions(self, exp_id=None, *, include_unlinked=True):
        """只读：待答问题、超期问题、未关联问题（分开列，⛔ 不糊成一个计数）。

        返回 `{ok, undecidable, problems, exp_id, open, overdue, blocking_overdue,
        blocking_open, declared_only, unlinked, diagnostics}`。
        * `ok` 只在「读得到且**没有明确 blocking 的超期问题**」时为 True；
          ⛔ 普通问题（`blocking=False`）超期只进 `diagnostics` —— 那不是 D2（D2 是
          我们欠用户回复的入向），也不代表实验必须停等；
        * 读不到 ⇒ `undecidable=True` 且 `ok=False`。

        `exp_id` 只作**过滤键**：⚠️ 不要求 `exp-` 前缀（上层可能用 plan id 过滤），
        没有匹配就是空；⛔ 但新建问题时仍要求合法实验号（交付台账按实验记账）。
        """
        wanted = None if exp_id is None else _token(exp_id, '实验号')
        problem = self._read_only_problem()
        if problem:
            return {'ok': False, 'undecidable': True, 'problems': [problem], 'exp_id': wanted,
                    'open': [], 'overdue': [], 'blocking_overdue': [], 'blocking_open': [],
                    'unlinked': [], 'declared_only': [], 'diagnostics': []}
        conn = self.store.connect()
        try:
            rows = [dict(row) for row in conn.execute(
                'SELECT question_id FROM questions ORDER BY question_id')]
            items = [self._view(conn, row['question_id']) for row in rows]
        finally:
            conn.close()
        # 「未关联」按**有效实验集合**判：旧行没有 exp 列，但显式 `linked_exp` 之后就算关联。
        unlinked = [item for item in items if not item['effective_exps']]
        scoped = [item for item in items if item['effective_exps']
                  and (wanted is None or wanted in item['effective_exps'])]
        opened = [item for item in scoped if item['status'] == 'open']
        overdue = [item for item in opened if item['deadline_overdue']]
        blocking_overdue = [item for item in overdue if item['blocking']]
        blocking_open = [item for item in opened if item['blocking']]
        out = {'ok': not blocking_overdue, 'undecidable': False, 'problems': [],
               'exp_id': wanted, 'open': opened, 'overdue': overdue,
               'blocking_overdue': blocking_overdue, 'blocking_open': blocking_open,
               'unlinked': unlinked if include_unlinked else [],
               'declared_only': [item for item in opened if item['declared_only']],
               'diagnostics': []}
        if blocking_overdue:
            out['problems'] = [
                f'{item["question_id"]} 是明确 blocking 的问题且已过 deadline：'
                '依赖它答复的动作不得开始' for item in blocking_overdue]
        if overdue and len(overdue) > len(blocking_overdue):
            out['diagnostics'].append(
                f'{len(overdue) - len(blocking_overdue)} 个**非 blocking** 问题已过 deadline：'
                '只作诊断，⛔ 不是用户欠我们回复，也不阻断已授权动作')
        if blocking_open and not blocking_overdue:
            out['diagnostics'].append(
                f'{len(blocking_open)} 个问题被声明为 blocking（尚未超期）：'
                '依赖它答复的动作在答复前不得开始')
        return out

    # -- 答复 ---------------------------------------------------------------

    def _authenticated_event(self, source, event_id):
        """把一条入向事件核成「可信答复来源」。⛔ 没核过就说没核过。"""
        if self.authenticate is None:
            raise QuestionError(
                '未配置可信 authenticate：入向身份不可核 ⇒ 不能把手工文字当成用户已回答；'
                '要记手工文字请用 declare（只会记成 declared）')
        try:
            row = self.inbox.get(source, event_id)
        except (InboxError, sqlite3.Error) as exc:
            raise QuestionError(
                f'入向台账读不到（{type(exc).__name__}）⇒ 不可判') from None
        if row is None:
            raise QuestionError(f'入向事件不存在：{source}:{event_id}（⛔ 不许凭空引用答复）')
        event_time = row.get('event_time')
        # 可信时间只认**入向事件自己**的时间（Inbox 记的 epoch 秒）。⛔ 不用本地 received_at
        # 补：那是我们收到的时刻，不是用户发这句的时刻。
        if isinstance(event_time, bool) or not isinstance(event_time, (int, float)) \
                or not math.isfinite(float(event_time)) or float(event_time) <= 0:
            raise QuestionError(
                f'入向事件的可信发生时间不可核（event_time={event_time!r}）'
                '⇒ 不拿本地 received_at 顶替，拒')
        if not str(row.get('principal') or '').strip():
            raise QuestionError('入向事件没有认证身份（principal 为空）⇒ 伪造或未认证，拒')
        if row.get('status') in ('received', 'rejected'):
            raise QuestionError(f'入向事件状态是 {row.get("status")}：还没通过身份核验 / 核验失败')
        if row.get('status') not in AUTHENTICATED_STATES:
            raise QuestionError(f'入向事件状态不可判：{row.get("status")}')
        payload = json.loads(row['payload_json'])
        try:
            principal = self.authenticate(source, json.loads(canonical(payload)))
        except Exception as exc:
            # ⛔ 不拼 `str(exc)`：验签 / HTTP / 鉴权异常可能把 token 带进正文。
            raise QuestionError(
                f'入向事件重新核身份失败（{type(exc).__name__}）⇒ 不可核，⛔ 不复用旧结论') from None
        if not _text(principal):
            raise QuestionError('可信 authenticate 没有给出主体 ⇒ 不当成用户答复')
        if principal.strip() != str(row['principal']).strip():
            raise QuestionError('入向事件的主体与台账里记的主体不一致 ⇒ 不可判')
        category = row.get('category')
        if category not in ANSWER_CATEGORIES:
            raise QuestionError(
                f'入向事件类别 {category!r} 是审批/否决/停机，不是问题答复：'
                '那类事件只能走审批链，⛔ 不能拿来答问题')
        text = payload.get('body')
        if not _text(text):
            text = payload.get('content')
        return {'source': source, 'event_id': event_id, 'principal': principal.strip(),
                'category': category, 'exp_id': row.get('exp_id'),
                'recipient': payload.get('recipient'), 'text': text,
                'payload_hash': row.get('payload_hash'),
                'event_time': float(event_time),
                'payload': payload,
                'reply_to_message_id': payload.get('reply_to_message_id'),
                'event_key': canonical([source, str(event_id)])}

    @staticmethod
    def _reply_binding(reply, *, question_id, delivery_message_id=None):
        """这条**已认证**答复指的是哪个问题？返回 `(via, problems)`。

        只认三种可追溯来源，`--question-id` 由调用方给的编号**不算证据**：

        1. 可信入向 payload 里显式的 `question_id` 字段（通道把它放在已签名正文里）；
        2. 已认证正文里的确定性标记：`[question:Q-000001]`（优先），或正文里一个裸的
           `Q-000001`（用户手打也能对上）；
        3. `reply_to_message_id` 等于**我们这条问题**已送达消息的 `message_id`（真通道的
           "回复那条消息"）。

        缺号、错号、多个问题号（歧义）一律拒 —— 调用方据此在**写任何状态之前**报错。
        """
        found, via, problems = set(), [], []
        declared = reply['payload'].get('question_id')
        if declared is not None:
            text = declared.strip() if isinstance(declared, str) else ''
            if not QUESTION_RE.fullmatch(text):
                problems.append(f'入向事件里的 question_id 形态非法：{declared!r}')
            else:
                found.add(text)
                via.append('payload_field')
        body = reply.get('text') or ''
        markers = {item.upper() for item in MARKER_RE.findall(body)}
        marker_kind = 'text_marker'
        if not markers:
            markers = {item.upper() for item in BARE_RE.findall(body)}
        if markers:
            found |= markers
            via.append(marker_kind)
        if delivery_message_id and reply.get('reply_to_message_id') == delivery_message_id:
            found.add(question_id)
            via.append('reply_to_message')
        if problems:
            return via, problems
        if not found:
            problems.append(
                '这条答复没有指明它答的是哪个问题：正文里要有 '
                f'`[question:{question_id}]`（或 Q 号），或由问题消息的回复线程带出；'
                '⛔ 只凭「同一实验」猜不算')
        elif len(found) > 1:
            problems.append('答复里出现了多个问题号（歧义）：'
                            + '、'.join(sorted(found)) + ' ⇒ 拒，先在会话里问清是哪一条')
        elif question_id not in found:
            problems.append(f'答复指的是 {"、".join(sorted(found))}，不是 {question_id} ⇒ 拒')
        return via, problems

    def _answered_event_reuse(self, conn, event_key, question_id):
        rows = conn.execute("SELECT question_id,payload_json FROM question_events "
                            "WHERE kind='answered'").fetchall()
        for row in rows:
            payload = json.loads(row['payload_json'])
            if payload.get('event_key') == event_key and row['question_id'] != question_id:
                return row['question_id']
        return None

    def answer(self, question_id, *, source, event_id, note=None):
        """用一条**已认证的入向事件**把问题答掉（`open -> answered`）。

        拒绝：未知问题、状态不是 open、实验号或收件人不符、事件没认证、事件类别是审批/停机、
        答复**没有指明是哪一条问题**（缺号 / 错号 / 多个号）、同一事件去答第二个问题、
        已回答后换一个答案（重放矛盾）。同一事件重放同一条 ⇒ 幂等返回。
        """
        identity = _token(question_id, '问题号')
        if not _text(source) or not _text(str(event_id or '')):
            raise QuestionError('答复必须给入向事件的 source 与 event_id')
        reply = self._authenticated_event(source, str(event_id))
        moment = float(self.clock())

        def op(conn):
            row = self._row(conn, identity)
            if row is None:
                raise QuestionError(f'问题不存在：{identity}')
            if row.get('exp_id') != reply['exp_id']:
                raise QuestionError(
                    f'入向事件属于实验 {reply["exp_id"]!r}，问题是 {row.get("exp_id")!r}'
                    ' ⇒ 实验号不符，拒')
            recipient = row.get('recipient')
            if recipient and reply['recipient'] != recipient:
                raise QuestionError(
                    f'入向事件的收件人 {reply["recipient"]!r} 与问题的 {recipient!r} 不符，拒')
            if not _text(reply['text']):
                raise QuestionError('入向事件里没有可读答复正文（body/content 都空），拒')
            asked_at = row.get('asked_at')
            if not isinstance(asked_at, (int, float)) or isinstance(asked_at, bool):
                raise QuestionError('问题缺少可靠 asked_at ⇒ 无法证明答复晚于发问，拒')
            if reply['event_time'] < float(asked_at):
                raise QuestionError(
                    f'答复事件时间（{reply["event_time"]}）早于这条问题的发问时间'
                    f'（{asked_at}）⇒ 不能算答复（先发生的事件不能回答后问的问题）')
            via, problems = self._reply_binding(
                reply, question_id=identity,
                delivery_message_id=(self._delivery_status(conn, row.get('delivery_id')) or {}
                                     ).get('message_id'))
            if problems:
                raise QuestionError('；'.join(problems))
            reused = self._answered_event_reuse(conn, reply['event_key'], identity)
            if reused is not None:
                raise QuestionConflict(
                    f'同一条入向事件已经答过 {reused}：⛔ 不许一事件两用（重放）')
            if row.get('status') == 'answered':
                if row.get('answer_event') == reply['event_key']:
                    return self._view(conn, identity)  # 同一事件重放：幂等
                raise QuestionConflict(
                    f'问题已经答过（{row.get("answered_by")}）：同一问题不接受第二个答案'
                    '（要改结论请新开一个问题）')
            if row.get('status') != 'open':
                raise QuestionError(f'只有 open 的问题能记录答复（当前 {row.get("status")}）')
            conn.execute("UPDATE questions SET status='answered',answered_by=?,answer_source=?,"
                         'answer_event=?,verification=?,updated_at=? WHERE question_id=?',
                         (reply['principal'], f'inbox:{reply["source"]}:{reply["event_id"]}',
                          reply['event_key'], 'verified', moment, identity))
            self._append(conn, identity, 'answered', reply['principal'],
                         {'principal': reply['principal'], 'source': reply['source'],
                          'event_id': reply['event_id'], 'event_key': reply['event_key'],
                          'category': reply['category'], 'text': reply['text'],
                          'payload_hash': reply['payload_hash'], 'exp_id': reply['exp_id'],
                          'recipient': reply['recipient'], 'verification': 'verified',
                          'question_binding': via, 'note': note},
                         from_state='open', to_state='answered')
            return self._view(conn, identity)

        return self._write(op)

    def declare(self, question_id, *, body, actor, sources=None, note=None):
        """记一条**手工文字**：只算 `declared`，⛔ 不改状态、⛔ 不冒充用户已回答。"""
        identity = _token(question_id, '问题号')
        if not _text(body):
            raise QuestionError('须给手工文字内容（body）')
        if not _text(actor):
            raise QuestionError('须给记录者（actor）')
        evidence = self._evidence(sources)
        moment = float(self.clock())

        def op(conn):
            row = self._row(conn, identity)
            if row is None:
                raise QuestionError(f'问题不存在：{identity}')
            if row.get('status') != 'open':
                raise QuestionError(f'只有 open 的问题能记手工文字（当前 {row.get("status")}）')
            self._append(conn, identity, 'declared_answer', actor.strip(),
                         {'text': body.strip(), 'actor': actor.strip(),
                          'verification': 'declared', 'sources': evidence,
                          'note': note or DECLARED_NOTE, 'status_after': 'open'},
                         from_state='open', to_state='open')
            conn.execute('UPDATE questions SET updated_at=? WHERE question_id=?',
                         (moment, identity))
            return self._view(conn, identity)

        return self._write(op)

    # -- 外发（复用现 Delivery；⛔ 不强制任何通道） ---------------------------

    def enqueue(self, question_id, *, actor='questions'):
        """把待答内容排进**现交付台账**（`kind='notice'`）。通道由部署配置，没通道就留在队列里。"""
        identity = _token(question_id, '问题号')
        if not _text(actor):
            raise QuestionError('须给执行者（actor）')
        moment = float(self.clock())

        def op(conn):
            row = self._row(conn, identity)
            if row is None:
                raise QuestionError(f'问题不存在：{identity}')
            if row.get('status') != 'open':
                raise QuestionError(f'只有 open 的问题能外发（当前 {row.get("status")}）')
            recipient = row.get('recipient')
            if not _text(recipient):
                raise QuestionError('问题没有收件人（recipient）：⛔ 不许「发给谁都不知道」')
            if row.get('delivery_id') is not None:
                return {'question_id': identity, 'delivery_id': row['delivery_id'],
                        'request_key': row.get('request_key'), 'status': 'already_enqueued',
                        'note': '这条问题已经排过交付；⛔ 不重复排队'}
            experiment = row.get('exp_id')
            if not _text(experiment) or not EXP_RE.fullmatch(str(experiment)):
                raise QuestionError('问题没有合法实验号 ⇒ 不能外发（交付台账按实验记账）')
            # 交付台账按实验记账：同一个写事务里补登实验（外键要求），⛔ 不另开连接自锁。
            self.store.ensure_experiment(str(experiment), _connection=conn)
            request_key = row.get('request_key') or f'question:{identity}:ask'
            # 正文给出**可复制**的回复标记：真通道上用户直接回这句，答复才能对上问题。
            body = (f"【问题 {identity}（实验 {experiment}），需要你回答】\n"
                    f"{REPLY_HINT.format(question_id=identity)}\n"
                    f"{row.get('body')}")
            try:
                task = self.delivery.enqueue(experiment, 'notice', body=body, recipient=recipient,
                                             request_key=request_key, conn=conn)
            except (DeliveryError, InboxError) as exc:
                raise QuestionError(f'排交付被拒（{type(exc).__name__}: {exc}）') from None
            conn.execute('UPDATE questions SET request_key=?,delivery_id=?,updated_at=? '
                         'WHERE question_id=?', (request_key, task['id'], moment, identity))
            self._append(conn, identity, 'enqueued', actor.strip(),
                         {'delivery_id': task['id'], 'request_key': request_key,
                          'recipient': recipient, 'kind': 'notice'},
                         from_state='open', to_state='open')
            view = self._view(conn, identity)
            return {'question_id': identity, 'delivery_id': task['id'],
                    'request_key': request_key, 'status': task['status'],
                    'delivery': view['delivery'], 'question': view}

        return self._write(op)

    # -- 关联 / 关闭 ---------------------------------------------------------

    def link(self, question_id, *, exp_id, actor, reason, sources=None):
        """把**旧/未关联**的问题显式关联到实验（追加 `linked_exp` 事件，⛔ 不改旧行）。"""
        identity = _token(question_id, '问题号')
        experiment = _exp(exp_id)
        if not _text(actor):
            raise QuestionError('关联实验须给执行者（actor）')
        if not _text(reason):
            raise QuestionError('关联实验须给理由（⛔ 不静默改归属）')
        evidence = self._evidence(sources)
        moment = float(self.clock())

        def op(conn):
            row = self._row(conn, identity)
            if row is None:
                raise QuestionError(f'问题不存在：{identity}')
            primary = row.get('exp_id')
            links = [json.loads(item['payload_json']).get('linked_exp')
                     for item in conn.execute(
                         "SELECT payload_json FROM question_events "
                         "WHERE question_id=? AND kind='linked_exp'", (identity,))]
            if primary == experiment or experiment in links:
                raise QuestionConflict(f'{identity} 已经关联到 {experiment}：不重复链接')
            self._append(conn, identity, 'linked_exp', actor.strip(),
                         {'linked_exp': experiment, 'text': reason.strip(),
                          'sources': evidence, 'status_after': row.get('status')},
                         from_state=row.get('status'), to_state=row.get('status'))
            conn.execute('UPDATE questions SET updated_at=? WHERE question_id=?',
                         (moment, identity))
            return self._view(conn, identity)

        return self._write(op)

    def expire(self, question_id, *, actor='questions'):
        """`open -> expired`：只认**过了 deadline** 的，⛔ 不凭感觉关。"""
        identity = _token(question_id, '问题号')
        if not _text(actor):
            raise QuestionError('须给执行者（actor）')
        moment = float(self.clock())

        def op(conn):
            row = self._row(conn, identity)
            if row is None:
                raise QuestionError(f'问题不存在：{identity}')
            if row.get('status') != 'open':
                raise QuestionError(f'只有 open 的问题能标超期（当前 {row.get("status")}）')
            deadline = row.get('deadline')
            if deadline is None:
                raise QuestionError('这条问题没有 deadline ⇒ 判不了超期（⛔ 不凭感觉关）')
            if float(deadline) > moment:
                raise QuestionError(f'deadline {row.get("deadline")} 还没到 ⇒ 不能标超期')
            conn.execute("UPDATE questions SET status='expired',updated_at=? WHERE question_id=?",
                         (moment, identity))
            self._append(conn, identity, 'expired', actor.strip(),
                         {'deadline': deadline, 'at': moment},
                         from_state='open', to_state='expired')
            return self._view(conn, identity)

        return self._write(op)

    def cancel(self, question_id, *, reason, actor='questions'):
        """`open -> cancelled`：必须给理由（⛔ 不静默丢弃）。"""
        identity = _token(question_id, '问题号')
        if not _text(reason):
            raise QuestionError('取消问题须给理由（⛔ 不静默丢弃）')
        if not _text(actor):
            raise QuestionError('须给执行者（actor）')
        moment = float(self.clock())

        def op(conn):
            row = self._row(conn, identity)
            if row is None:
                raise QuestionError(f'问题不存在：{identity}')
            if row.get('status') != 'open':
                raise QuestionError(f'只有 open 的问题能取消（当前 {row.get("status")}）')
            conn.execute("UPDATE questions SET status='cancelled',updated_at=? WHERE question_id=?",
                         (moment, identity))
            self._append(conn, identity, 'cancelled', actor.strip(),
                         {'reason': reason.strip()}, from_state='open', to_state='cancelled')
            return self._view(conn, identity)

        return self._write(op)
