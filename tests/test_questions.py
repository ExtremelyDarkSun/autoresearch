# GENERATED-BY: 普通向用户提问台账测试，2026-11-09
# LAST-EDIT: 2026-11-09 首版：创建 / 查待答 / 已认证回复闭合 / declared 不冒充 / 外发 / 旧 schema / 只读零写 / CLI
# PURPOSE: 证明「我们要问用户」这类问题真的落盘并关联实验、负责人、待答内容、deadline 与可信回复来源；
#          同时证明没有可信身份时**不会**被记成用户已回答。
# ORIGIN: kernel/autoresearch/questions.py；⛔ 不含真实通道、个人身份或具体机器。
# CUMULATIVE-DELTA: 首版。全部用真实生产类（Store / Accounts / Delivery / Inbox / Questions）在临时库里跑，
#   ⛔ 不 mock 自己的入向或交付；唯一假件是测试自己的 `authenticate`（生产必须由部署注入）。

"""普通向用户提问台账（E7）的集成测试。

判据（不是「跑通就行」）：

1. **提问必须带实验号、负责人、待答内容**；deadline 可选（不给就是诚实 open，⛔ 不硬塞默认时限）；
2. **答复只认已认证入向事件**：`Inbox.authenticate` 给出 `principal`、状态不是
   `received`/`rejected`、实验号与收件人跟问题一致、类别不是审批/停机；
3. **`declared` 不能冒充 `answered`**：手工文字只追加一条 `declared` 事件，状态仍是 `open`；
4. **重放矛盾要拒**：同一事件答两个问题、已回答后换答案；同一事件重放同一条 ⇒ 幂等；
5. **旧 schema 兼容 unknown**：没有 `exp_id` 列的旧表读成 `exp_link='unknown'`，⛔ 不改写、⛔ 不迁移；
6. **只读零写**：`readonly=True` 不建库、不建表、DB 字节不变；
7. **外发复用现交付台账**：`enqueue` 落的是同一库里的 `delivery` 行，没有收件人就拒。

所有材料都是临时目录里的合成夹具：一个工作区、一个真实 sqlite、两三条假入向事件。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch.questions import (  # noqa: E402
    QuestionConflict,
    QuestionError,
    Questions,
)
from kernel.autoresearch.questions_cli import main as questions_main  # noqa: E402
from kernel.autoresearch.runtime.accounts import Accounts  # noqa: E402
from kernel.autoresearch.runtime.delivery import Delivery  # noqa: E402
from kernel.autoresearch.runtime.inbox import Inbox  # noqa: E402
from kernel.autoresearch.runtime.store import ReadonlyStore, Store  # noqa: E402

EXP = 'exp-01'
OWNER = 'sess-01'
RECIPIENT = 'chat-01'


class Clock:
    def __init__(self, now=None):
        self.now = float(time.time() if now is None else now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


def authenticate(source, event):
    """测试用的可信身份核验器：`token != 'ok'` ⇒ 核不出来（生产由部署注入真实验签）。"""
    if event.get('token') != 'ok':
        raise ValueError('签名不可核')
    return event.get('principal')


def tree_snapshot(root):
    root = Path(root)
    out = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            out[rel + '/'] = '<dir>'
        else:
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class QuestionCase(unittest.TestCase):
    """一个临时工作区 + 真实 Store / Accounts / Delivery / Inbox + 提问台账。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='hil_questions_')
        self.ws = Path(self._tmp.name) / 'ws'
        (self.ws / '.autoresearch').mkdir(parents=True)
        self.db = self.ws / '.autoresearch' / 'runtime.sqlite3'
        self.clock = Clock()
        self.store = Store(self.db)
        self.delivery = Delivery(self.store, clock=self.clock)
        self.accounts = Accounts(self.store)
        self.inbox = Inbox(self.store, self.accounts, self.delivery,
                           authenticate=authenticate, clock=self.clock)
        self.ledger = Questions(self.ws, store=self.store, delivery=self.delivery,
                                inbox=self.inbox, authenticate=authenticate, clock=self.clock)
        self.store.ensure_experiment(EXP)
        self.store.ensure_experiment('exp-02')

    def tearDown(self):
        self._tmp.cleanup()

    # -- 夹具 ---------------------------------------------------------------

    def ask(self, body='基线用哪一版？', *, exp=EXP, owner=OWNER, blocking=False, **changes):
        return self.ledger.ask(exp, body, owner=owner, asked_by='agent', blocking=blocking,
                               **{'recipient': RECIPIENT, **changes})

    def inbound(self, event_id='ev-1', *, question_id=None, exp=EXP, body='用 v3 那一版',
                token='ok', recipient=RECIPIENT, intent='other', principal='user', extra=None):
        """落一条真实入向事件；`question_id` 给了就在正文里加可复制标记（模拟用户照抄）。"""
        text = body if question_id is None else f'[question:{question_id}] {body}'
        event = {'event_id': event_id, 'exp_id': exp, 'recipient': recipient,
                 'create_time': self.clock.now, 'intent': intent, 'body': text,
                 'principal': principal, 'token': token}
        event.update(extra or {})
        self.inbox.receive('chat', event)
        return self.inbox.classify('chat', event_id)

    def db_bytes(self):
        return self.db.read_bytes()

    def events(self, question_id):
        return [row['kind'] for row in self.ledger.show(question_id)['events']]


class CreateTests(QuestionCase):
    def test_ask_records_exp_owner_body_and_no_default_deadline(self):
        item = self.ask()
        self.assertEqual(item['status'], 'open')
        self.assertEqual(item['exp_link'], 'linked')
        self.assertEqual(item['exp_id'], EXP)
        self.assertEqual(item['owner'], OWNER)
        self.assertEqual(item['body'], '基线用哪一版？')
        self.assertEqual(item['asked_by'], 'agent')
        self.assertIsNone(item['deadline'], 'deadline 不给就必须是 None（⛔ 不硬塞默认时限）')
        self.assertFalse(item['deadline_overdue'])
        self.assertEqual(item['question_id'], 'Q-000001')
        self.assertEqual(self.events(item['question_id']), ['asked'])
        self.assertEqual(self.ask()['question_id'], 'Q-000002')

    def test_deadline_accepts_epoch_and_iso_with_timezone(self):
        epoch = self.clock.now + 3600
        item = self.ask(deadline=epoch)
        self.assertAlmostEqual(item['deadline'], epoch, places=3)
        iso = '2031-01-01T00:00:00+00:00'
        item = self.ask(deadline=iso)
        self.assertIsNotNone(item['deadline'])
        self.assertIn('2031', item['deadline_at'])
        with self.assertRaises(QuestionError):
            self.ask(deadline='2031-01-01T00:00:00')  # 没有时区 ⇒ 判不了超期
        with self.assertRaises(QuestionError):
            self.ask(deadline='不是时间')

    def test_ask_requires_exp_owner_and_body(self):
        for kwargs in ({'exp': None}, {'exp': 'plain'}, {'exp': 'exp-01 说明'},
                       {'owner': None}, {'owner': ' '}, {'body': ''}):
            with self.subTest(kwargs=kwargs):
                payload = {'body': 'x', 'exp': EXP, 'owner': OWNER}
                payload.update(kwargs)
                with self.assertRaises((QuestionError, ValueError)):
                    self.ledger.ask(payload['exp'], payload['body'],
                                    owner=payload['owner'], asked_by='agent')

    def test_explicit_question_id_rules(self):
        self.ask(question_id='Q-000042')
        self.assertEqual(self.ledger.show('Q-000042')['status'], 'open')
        with self.assertRaises(QuestionConflict):
            self.ask(question_id='Q-000042')
        with self.assertRaises(QuestionError):
            self.ask(question_id='Q-42')

    def test_uninitialised_workspace_is_refused(self):
        bare = Path(self._tmp.name) / 'bare'
        bare.mkdir()
        with self.assertRaises(QuestionError):
            Questions(bare)
        self.assertFalse((bare / '.autoresearch').exists())


class AnswerTests(QuestionCase):
    def test_answer_without_trusted_authenticate_is_refused_and_points_to_declare(self):
        item = self.ask()
        self.inbound(question_id=item['question_id'])
        naked = Questions(self.ws, store=self.store, delivery=self.delivery,
                          inbox=self.inbox, authenticate=None, clock=self.clock)
        before = self.db_bytes()
        with self.assertRaises(QuestionError) as caught:
            naked.answer(item['question_id'], source='chat', event_id='ev-1')
        self.assertIn('declare', str(caught.exception))
        self.assertEqual(before, self.db_bytes(), '被拒的答复不许写库')
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'open')

    def test_answer_rejects_unauthenticated_forged_and_mismatched_events(self):
        item = self.ask()
        qid = item['question_id']
        # 1) 身份核不过 ⇒ 事件是 rejected，principal 为空
        self.inbound('ev-bad', question_id=qid, token='bad')
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(qid, source='chat', event_id='ev-bad')
        self.assertIn('principal', str(caught.exception))
        # 2) 事件根本不存在
        with self.assertRaises(QuestionError):
            self.ledger.answer(qid, source='chat', event_id='ev-none')
        # 3) 错实验号
        self.inbound('ev-other-exp', question_id=qid, exp='exp-02')
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(qid, source='chat', event_id='ev-other-exp')
        self.assertIn('实验号不符', str(caught.exception))
        # 4) 错收件人
        self.inbound('ev-other-chat', question_id=qid, recipient='chat-99')
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(qid, source='chat', event_id='ev-other-chat')
        self.assertIn('收件人', str(caught.exception))
        # 5) 审批类事件不是答复
        self.inbound('ev-approve', question_id=qid, intent='authorize')
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(qid, source='chat', event_id='ev-approve')
        self.assertIn('审批', str(caught.exception))
        # 6) 未知问题
        with self.assertRaises(QuestionError):
            self.ledger.answer('Q-999999', source='chat', event_id=f'ev-1')
        self.assertEqual(self.ledger.show(qid)['status'], 'open')

    def test_answer_reverifies_identity_and_refuses_when_verifier_now_fails(self):
        item = self.ask()
        self.inbound(question_id=item['question_id'])

        def broken(source, event):
            raise RuntimeError('签名不可核')

        ledger = Questions(self.ws, store=self.store, delivery=self.delivery,
                           inbox=self.inbox, authenticate=broken, clock=self.clock)
        with self.assertRaises(QuestionError) as caught:
            ledger.answer(item['question_id'], source='chat', event_id='ev-1')
        self.assertIn('重新核身份失败', str(caught.exception))
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'open')

    def test_authenticated_reply_closes_the_question(self):
        item = self.ask()
        self.inbound(question_id=item['question_id'], body='用 v3')
        answered = self.ledger.answer(item['question_id'], source='chat', event_id='ev-1')
        self.assertEqual(answered['status'], 'answered')
        self.assertEqual(answered['answered_by'], 'user')
        self.assertEqual(answered['verification'], 'verified')
        self.assertEqual(answered['answer_source'], 'inbox:chat:ev-1')
        self.assertFalse(answered['declared_only'])
        self.assertEqual(answered['answered_events'][0]['principal'], 'user')
        self.assertEqual(answered['answered_events'][0]['text'],
                         f"[question:{item['question_id']}] 用 v3")
        self.assertIn('text_marker', answered['answered_events'][0]['binding'])
        self.assertEqual(self.events(item['question_id']), ['asked', 'answered'])
        self.assertEqual(self.ledger.open_questions(EXP)['open'], [])

    def test_replay_semantics(self):
        first = self.ask('一？')
        second = self.ask('二？')
        self.inbound(question_id=first['question_id'])
        self.ledger.answer(first['question_id'], source='chat', event_id='ev-1')
        count = len(self.ledger.show(first['question_id'])['events'])
        # 同一事件再放一次 ⇒ 幂等，不追加事件
        again = self.ledger.answer(first['question_id'], source='chat', event_id='ev-1')
        self.assertEqual(again['status'], 'answered')
        self.assertEqual(len(self.ledger.show(first['question_id'])['events']), count)
        # 同一事件去答第二个问题 ⇒ 先因「标记指的是第一条」被拒，也不会被记成第二条
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(second['question_id'], source='chat', event_id='ev-1')
        self.assertIn(first['question_id'], str(caught.exception))
        self.assertEqual(self.ledger.show(second['question_id'])['status'], 'open')
        # 已回答后换一个答案 ⇒ 拒
        self.inbound('ev-2', question_id=first['question_id'], body='用 v4')
        with self.assertRaises(QuestionConflict):
            self.ledger.answer(first['question_id'], source='chat', event_id='ev-2')
        self.assertEqual(self.ledger.show(first['question_id'])['answered_by'], 'user')


class BindingTests(QuestionCase):
    """「答的是哪一条问题」必须由已认证内容指出，⛔ 不由调用方给个 Q 号就算。"""

    def test_missing_question_marker_is_refused_with_zero_state_change(self):
        item = self.ask()
        self.inbound('ev-nomarker', body='用 v3 那一版')  # 同实验、同收件人，但没指问题
        before = self.db_bytes()
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(item['question_id'], source='chat', event_id='ev-nomarker')
        self.assertIn('没有指明它答的是哪个问题', str(caught.exception))
        self.assertEqual(before, self.db_bytes())
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'open')

    def test_wrong_question_number_is_refused(self):
        first = self.ask('一？')
        second = self.ask('二？')
        self.inbound('ev-wrong', question_id=second['question_id'], body='用它')
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(first['question_id'], source='chat', event_id='ev-wrong')
        self.assertIn(second['question_id'], str(caught.exception))
        self.assertEqual(self.ledger.show(first['question_id'])['status'], 'open')
        self.assertEqual(self.ledger.show(second['question_id'])['status'], 'open')

    def test_multiple_question_numbers_are_ambiguous(self):
        first = self.ask('一？')
        second = self.ask('二？')
        self.inbound('ev-two', body=f"Q {first['question_id']} 和 {second['question_id']} 都按 v3")
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(first['question_id'], source='chat', event_id='ev-two')
        self.assertIn('多个问题号', str(caught.exception))
        self.assertEqual(self.ledger.show(first['question_id'])['status'], 'open')

    def test_bare_question_number_in_text_is_accepted(self):
        item = self.ask('哪一版？')
        self.inbound('ev-bare', body=f"{item['question_id']} 用 v3 那一版")
        answered = self.ledger.answer(item['question_id'], source='chat', event_id='ev-bare')
        self.assertEqual(answered['status'], 'answered')
        self.assertIn('text_marker', answered['answered_events'][0]['binding'])

    def test_authenticated_payload_field_is_accepted_and_wrong_field_is_refused(self):
        item = self.ask()
        self.inbound('ev-field', question_id=None, body='用 v3',
                     extra={'question_id': item['question_id']})
        answered = self.ledger.answer(item['question_id'], source='chat', event_id='ev-field')
        self.assertIn('payload_field', answered['answered_events'][0]['binding'])
        other = self.ask('另一个问题？')
        self.inbound('ev-field-bad', question_id=None, body='用 v3',
                     extra={'question_id': 'Q-000099'})
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(other['question_id'], source='chat', event_id='ev-field-bad')
        self.assertIn('Q-000099', str(caught.exception))

    def test_reply_to_our_sent_message_is_accepted_without_marker(self):
        item = self.ask()
        sent = self.ledger.enqueue(item['question_id'])
        # 模拟真实送达：通道回执会把 message_id 写进交付台账（这里直接置位，等价于 ack 之后的状态）
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            conn.execute("UPDATE delivery SET status='delivered',message_id='om_q_1' WHERE id=?",
                         (sent['delivery_id'],))
            conn.commit()
        self.inbound('ev-reply', question_id=None, body='用 v3',
                     extra={'reply_to_message_id': 'om_q_1'})
        answered = self.ledger.answer(item['question_id'], source='chat', event_id='ev-reply')
        self.assertEqual(answered['status'], 'answered')
        self.assertIn('reply_to_message', answered['answered_events'][0]['binding'])

    def test_enqueue_body_carries_a_copyable_reply_marker(self):
        item = self.ask('基线用哪一版？')
        sent = self.ledger.enqueue(item['question_id'])
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            body = json.loads(conn.execute('SELECT payload_json FROM delivery WHERE id=?',
                                           (sent['delivery_id'],)).fetchone()[0])['body']
        self.assertIn(f"[question:{item['question_id']}]", body)
        self.assertIn(item['question_id'], body)


class ReplyTimeTests(QuestionCase):
    """答复必须晚于提问：可信时间只认入向事件自己的时间，⛔ 不用本地 received_at 顶替。"""

    def test_event_earlier_than_the_question_is_not_an_answer(self):
        self.clock.advance(100)
        item = self.ask()
        self.inbound('ev-early', question_id=item['question_id'])
        # 把入向事件时间改成「发问之前」：先发生的消息不能回答后问的问题
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            conn.execute('UPDATE inbox_events SET event_time=? WHERE event_id=?',
                         (self.clock.now - 500, 'ev-early'))
            conn.commit()
        before = self.db_bytes()
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(item['question_id'], source='chat', event_id='ev-early')
        self.assertIn('早于', str(caught.exception))
        self.assertEqual(before, self.db_bytes(), '被拒的答复不许写库')
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'open')

    def test_unverifiable_event_time_is_refused_not_backfilled(self):
        item = self.ask()
        self.inbound('ev-notime', question_id=item['question_id'])
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            conn.execute("UPDATE inbox_events SET event_time=NULL WHERE event_id='ev-notime'")
            conn.commit()
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(item['question_id'], source='chat', event_id='ev-notime')
        self.assertIn('不可核', str(caught.exception))
        self.assertIn('received_at', str(caught.exception))
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'open')

    def test_answered_event_records_the_trusted_time(self):
        item = self.ask()
        self.clock.advance(30)
        self.inbound('ev-later', question_id=item['question_id'])
        answered = self.ledger.answer(item['question_id'], source='chat', event_id='ev-later')
        self.assertEqual(answered['status'], 'answered')
        event = answered['answered_events'][0]
        self.assertGreater(event['at'], 0)


class DeclareTests(QuestionCase):
    def test_declare_records_only_declared_and_keeps_question_open(self):
        item = self.ask()
        log = self.ws / 'evidence' / 'quote.md'
        log.parent.mkdir()
        log.write_text('用户原话：用 v3\n', encoding='utf-8')
        declared = self.ledger.declare(item['question_id'], body='用户口头说用 v3',
                                       actor='agent',
                                       sources=[{'path': 'evidence/quote.md', 'locator': 'L1'}])
        self.assertEqual(declared['status'], 'open', 'declared ⛔ 不改状态')
        self.assertTrue(declared['declared_only'])
        self.assertEqual(declared['declared_answers'][0]['verification'], 'declared')
        self.assertIn('不能当成用户已回答', declared['declared_answers'][0]['note'])
        digest = hashlib.sha256(log.read_bytes()).hexdigest()
        self.assertEqual(declared['declared_answers'][0]['sources'][0]['sha256'], digest)
        opened = self.ledger.open_questions(EXP)
        self.assertEqual([row['question_id'] for row in opened['declared_only']],
                         [item['question_id']])
        self.assertEqual([row['question_id'] for row in opened['open']],
                         [item['question_id']], '只有 declared 的问题仍算待答')

    def test_declare_refused_after_answer(self):
        item = self.ask()
        self.inbound(question_id=item['question_id'])
        self.ledger.answer(item['question_id'], source='chat', event_id='ev-1')
        with self.assertRaises(QuestionError):
            self.ledger.declare(item['question_id'], body='补充说明', actor='agent')
        with self.assertRaises(QuestionError):
            self.ledger.declare('Q-999999', body='x', actor='agent')


class DeadlineTests(QuestionCase):
    def test_overdue_questions_are_listed_separately(self):
        overdue = self.ask('过期了？', deadline=self.clock.now + 10)
        fresh = self.ask('还没过期？', deadline=self.clock.now + 3600)
        open_ended = self.ask('没有 deadline')
        self.clock.advance(60)
        state = self.ledger.open_questions(EXP)
        self.assertEqual([row['question_id'] for row in state['overdue']],
                         [overdue['question_id']])
        self.assertEqual(len(state['open']), 3)
        self.assertIsNone(open_ended['deadline'])
        self.assertIsNotNone(fresh['deadline'])
        # 普通问题（blocking=False）超期只作诊断：⛔ 不是用户欠我们回复，也不阻断已授权动作
        self.assertTrue(state['ok'])
        self.assertEqual(state['problems'], [])
        self.assertEqual(state['blocking_overdue'], [])
        self.assertTrue(any('非 blocking' in note for note in state['diagnostics']))

    def test_only_explicit_blocking_questions_gate(self):
        blocking = self.ask('必须答了才能跑？', blocking=True, deadline=self.clock.now + 10)
        ordinary = self.ask('顺便问一句')
        state = self.ledger.open_questions(EXP)
        self.assertTrue(state['ok'], 'blocking 问题还没超期 ⇒ 不阻断')
        self.assertEqual([row['question_id'] for row in state['blocking_open']],
                         [blocking['question_id']])
        self.assertTrue(any('声明为 blocking' in note for note in state['diagnostics']))
        self.clock.advance(60)
        state = self.ledger.open_questions(EXP)
        self.assertFalse(state['ok'])
        self.assertEqual([row['question_id'] for row in state['blocking_overdue']],
                         [blocking['question_id']])
        self.assertIn('已过 deadline', state['problems'][0])
        self.assertEqual([row['question_id'] for row in state['overdue']],
                         [blocking['question_id']])
        self.assertEqual(ordinary['blocking'], False)
        self.assertEqual(self.ledger.show(blocking['question_id'])['blocking'], True)
        with self.assertRaises(QuestionError):
            self.ask('blocking 必须是布尔', blocking='yes')

    def test_expire_requires_a_passed_deadline(self):
        no_deadline = self.ask('没有 deadline')
        with self.assertRaises(QuestionError):
            self.ledger.expire(no_deadline['question_id'])
        future = self.ask('未来的 deadline', deadline=self.clock.now + 3600)
        with self.assertRaises(QuestionError):
            self.ledger.expire(future['question_id'])
        past = self.ask('过去的 deadline', deadline=self.clock.now + 10)
        self.clock.advance(60)
        expired = self.ledger.expire(past['question_id'])
        self.assertEqual(expired['status'], 'expired')
        with self.assertRaises(QuestionError):
            self.ledger.answer(past['question_id'], source='chat', event_id='ev-never')
        # 没有 deadline 的问题永远不会自动过期
        self.assertEqual(self.ledger.show(no_deadline['question_id'])['status'], 'open')

    def test_cancel_requires_reason(self):
        item = self.ask()
        with self.assertRaises(QuestionError):
            self.ledger.cancel(item['question_id'], reason='  ')
        cancelled = self.ledger.cancel(item['question_id'], reason='改主意了，不问了')
        self.assertEqual(cancelled['status'], 'cancelled')
        with self.assertRaises(QuestionError):
            self.ledger.cancel(item['question_id'], reason='再取消一次')


class EnqueueTests(QuestionCase):
    def test_enqueue_requires_recipient(self):
        item = self.ask(recipient=None)
        with self.assertRaises(QuestionError) as caught:
            self.ledger.enqueue(item['question_id'])
        self.assertIn('收件人', str(caught.exception))

    def test_enqueue_reuses_the_real_delivery_ledger(self):
        item = self.ask()
        result = self.ledger.enqueue(item['question_id'])
        self.assertEqual(result['status'], 'queued')
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            rows = conn.execute('SELECT id,kind,exp_id,status,payload_json FROM delivery').fetchall()
        self.assertEqual(len(rows), 1, '只允许一本交付账：同一库里的 delivery 行')
        self.assertEqual(rows[0][1], 'notice')
        self.assertEqual(rows[0][2], EXP)
        self.assertEqual(rows[0][3], 'queued')
        payload = json.loads(rows[0][4])
        self.assertIn(item['question_id'], payload['body'])
        self.assertEqual(payload['recipient'], RECIPIENT)
        # 第二次外发不重复排队
        again = self.ledger.enqueue(item['question_id'])
        self.assertEqual(again['status'], 'already_enqueued')
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM delivery').fetchone()[0], 1)


class ReadonlyAndLegacyTests(QuestionCase):
    def test_readonly_missing_database_is_undecidable_and_creates_nothing(self):
        empty = Path(self._tmp.name) / 'empty'
        empty.mkdir()
        before = tree_snapshot(empty)
        ledger = Questions(empty, readonly=True)
        state = ledger.open_questions()
        self.assertTrue(state['undecidable'])
        self.assertFalse(state['ok'])
        self.assertIn('不可判', state['problems'][0])
        with self.assertRaises(QuestionError):
            ledger.show('Q-000001')
        with self.assertRaises(QuestionError):
            ledger.ask(EXP, '要写吗？', owner=OWNER, asked_by='agent')
        self.assertEqual(tree_snapshot(empty), before, '只读入口不许建库建目录')

    def test_readonly_existing_database_is_byte_identical(self):
        item = self.ask()
        self.inbound(question_id=item['question_id'])
        self.ledger.answer(item['question_id'], source='chat', event_id='ev-1')
        before = tree_snapshot(self.ws)
        readonly = Questions(self.ws, readonly=True)
        state = readonly.open_questions(EXP)
        self.assertFalse(state['undecidable'])
        self.assertEqual(readonly.show(item['question_id'])['status'], 'answered')
        self.assertEqual(ReadonlyStore(self.db).tables() >= {'questions', 'question_events'}, True)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_legacy_schema_projects_unknown_and_refuses_writes(self):
        legacy = Path(self._tmp.name) / 'legacy'
        (legacy / '.autoresearch').mkdir(parents=True)
        path = legacy / '.autoresearch' / 'runtime.sqlite3'
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            conn.execute('CREATE TABLE questions(question_id TEXT PRIMARY KEY, body TEXT, '
                         'status TEXT, owner TEXT, asked_at REAL)')
            conn.execute("INSERT INTO questions VALUES('Q-000001','旧问题','open','sess-9',0)")
            conn.commit()
        before = path.read_bytes()
        ledger = Questions(legacy, readonly=True)
        state = ledger.open_questions()
        self.assertFalse(state['undecidable'])
        self.assertEqual([row['question_id'] for row in state['unlinked']], ['Q-000001'])
        self.assertEqual(state['unlinked'][0]['exp_link'], 'unknown')
        self.assertEqual(state['open'], [], '未关联问题不许被算进任何实验')
        self.assertEqual(path.read_bytes(), before, '只读投影不许改库')
        before_sql = self._schema(path)
        before_rows = self._rows(path)
        with self.assertRaises(QuestionError) as caught:
            Questions(legacy)
        self.assertIn('旧 schema', str(caught.exception))
        self.assertEqual(self._schema(path), before_sql, '写模式发现旧 schema 也不许建表/改表')
        self.assertEqual(self._rows(path), before_rows, '旧行一个字节都不许改写')

    @staticmethod
    def _schema(path):
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            return [tuple(row) for row in conn.execute(
                'SELECT type,name,sql FROM sqlite_master ORDER BY type,name')]

    @staticmethod
    def _rows(path):
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            return [tuple(row) for row in conn.execute('SELECT * FROM questions ORDER BY 1')]

    def test_link_adopts_legacy_question_with_an_explicit_event(self):
        item = self.ask()
        # 造一条「旧」问题：直接把 exp_id 置空（等价于历史里没有 exp 关联）
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            conn.execute("UPDATE questions SET exp_id=NULL WHERE question_id=?",
                         (item['question_id'],))
            conn.commit()
        self.assertEqual(self.ledger.show(item['question_id'])['exp_link'], 'unlinked')
        log = self.ws / 'evidence' / 'legacy.md'
        log.parent.mkdir()
        log.write_text('历史台账里这条属于 exp-01\n', encoding='utf-8')
        linked = self.ledger.link(item['question_id'], exp_id=EXP, actor='agent',
                                  reason='历史台账显示它属于该实验',
                                  sources=[{'path': 'evidence/legacy.md', 'locator': 'L1'}])
        self.assertIn(EXP, linked['effective_exps'])
        self.assertEqual(linked['exp_links'][0]['reason'], '历史台账显示它属于该实验')
        self.assertEqual([row['question_id'] for row in self.ledger.open_questions(EXP)['open']],
                         [item['question_id']], '显式链接后可以算进该实验')
        with self.assertRaises(QuestionConflict):
            self.ledger.link(item['question_id'], exp_id=EXP, actor='agent',
                             reason='再链一次', sources=[{'path': 'evidence/legacy.md',
                                                          'locator': 'L1'}])


class CliTests(QuestionCase):
    def cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = questions_main(argv)
        return code, out.getvalue()

    def test_cli_ask_open_show_declare_and_cancel(self):
        code, output = self.cli(['--workspace', str(self.ws), 'ask', '--exp', EXP,
                                 '--body', '基线用哪一版？', '--owner', OWNER,
                                 '--recipient', RECIPIENT])
        self.assertEqual(code, 0, output)
        question_id = json.loads(output)['question']['question_id']
        code, output = self.cli(['open', '--workspace', str(self.ws), '--exp', EXP])
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['open'][0]['question_id'], question_id)
        code, output = self.cli(['show', question_id, '--workspace', str(self.ws)])
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['question']['status'], 'open')
        code, output = self.cli(['declare', question_id, '--workspace', str(self.ws),
                                 '--body', '用户口头说用 v3', '--actor', 'agent'])
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['question']['status'], 'open')
        code, output = self.cli(['cancel', question_id, '--workspace', str(self.ws),
                                 '--text', '改主意了'])
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['question']['status'], 'cancelled')

    def test_cli_answer_without_authenticate_is_a_rule_refusal(self):
        code, output = self.cli(['--workspace', str(self.ws), 'ask', '--exp', EXP,
                                 '--body', '基线用哪一版？', '--owner', OWNER])
        self.assertEqual(code, 0, output)
        question_id = json.loads(output)['question']['question_id']
        code, output = self.cli(['answer', question_id, '--workspace', str(self.ws),
                                 '--source', 'chat', '--event-id', 'ev-1'])
        self.assertEqual(code, 9, output)
        payload = json.loads(output)
        self.assertEqual(payload['error'], 'NoTrustedChannel')
        self.assertIn('--channel-config', payload['reason'])

    def test_cli_errors_use_input_codes(self):
        code, output = self.cli(['open', '--workspace', str(Path(self._tmp.name) / 'nope')])
        self.assertEqual(code, 2, output)
        code, output = self.cli(['show', 'Q-999999', '--workspace', str(self.ws)])
        self.assertEqual(code, 9, output)


class FeishuChannelReplyTests(unittest.TestCase):
    """真通道适配层：用**飞书已验签的规范入向正文**闭合问题（离线，不出网）。

    这条用例只证明「正文里带 `[question:Q-…]` 的真通道规范化 body 能被认成答复」；
    ⛔ 不证明真实租户送达（那要部署方提供凭据与现场证据）。
    """

    def setUp(self):
        import test_feishu as feishu_fixtures
        self.feishu = feishu_fixtures
        self._tmp = tempfile.TemporaryDirectory(prefix='hil_questions_feishu_')
        self.feishu_tmp = Path(self._tmp.name)
        self.ws = self.feishu_tmp / 'ws'
        (self.ws / '.autoresearch').mkdir(parents=True)
        self.db = self.ws / '.autoresearch' / 'runtime.sqlite3'
        self.store = Store(self.db)
        self.store.ensure_experiment(feishu_fixtures.EXP)
        self.clock = feishu_fixtures.Clock()
        self.channel, _transport = feishu_fixtures.make_channel(self.feishu_tmp,
                                                                clock=self.clock)
        self.delivery = Delivery(self.store, clock=self.clock)
        self.inbox = Inbox(self.store, Accounts(self.store), self.delivery,
                           authenticate=self.channel.authenticate, clock=self.clock)
        self.ledger = Questions(self.ws, store=self.store, delivery=self.delivery,
                                inbox=self.inbox,
                                authenticate=self.channel.authenticate, clock=self.clock)

    def tearDown(self):
        self._tmp.cleanup()

    def webhook(self, text, *, event_id='ev-msg-1', parent_id=None):
        headers, raw = self.feishu.signed(
            self.feishu.message_event(event_id=event_id, text=text, parent_id=parent_id),
            clock=self.clock)
        result = self.channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'], result)
        event = result['event']
        self.inbox.receive(self.feishu.feishu.SOURCE, event)
        self.inbox.classify(self.feishu.feishu.SOURCE, event['event_id'])
        return event

    def test_normalized_body_with_marker_prefix_closes_the_question(self):
        item = self.ledger.ask(self.feishu.EXP, '基线用哪一版？', owner='sess-01',
                               asked_by='agent', recipient=self.feishu.RECIPIENT)
        self.assertEqual(item['recipient'], self.feishu.RECIPIENT)
        event = self.webhook(f"[question:{item['question_id']}] 用 v3 那一版")
        self.assertIn('body', event, '飞书规范化的正文字段是 body')
        answered = self.ledger.answer(item['question_id'],
                                      source=self.feishu.feishu.SOURCE,
                                      event_id=event['event_id'])
        self.assertEqual(answered['status'], 'answered')
        self.assertEqual(answered['answered_by'], self.feishu.ALICE)
        self.assertEqual(answered['verification'], 'verified')
        self.assertIn('text_marker', answered['answered_events'][0]['binding'])

    def test_normalized_body_without_marker_is_refused(self):
        item = self.ledger.ask(self.feishu.EXP, '基线用哪一版？', owner='sess-01',
                               asked_by='agent', recipient=self.feishu.RECIPIENT)
        event = self.webhook('用 v3 那一版')
        before = self.db.read_bytes()
        with self.assertRaises(QuestionError) as caught:
            self.ledger.answer(item['question_id'],
                               source=self.feishu.feishu.SOURCE,
                               event_id=event['event_id'])
        self.assertIn('没有指明它答的是哪个问题', str(caught.exception))
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'open')

    def channel_config_json(self):
        """部署配置：`--channel-config` 用的同一份 JSON（只含 env: 凭据引用与允许列表）。"""
        return json.dumps({
            'app_id_ref': 'env:FEISHU_APP_ID',
            'app_secret_ref': 'env:FEISHU_APP_SECRET',
            'verification_token_ref': 'env:FEISHU_VERIFICATION_TOKEN',
            'encrypt_key_ref': 'env:FEISHU_ENCRYPT_KEY',
            'send_enabled': False,
            'receive_id_type': 'chat_id',
            'allowed_recipients': [self.feishu.RECIPIENT],
            'allowed_sender_open_ids': [self.feishu.ALICE],
            'chat_bindings': {self.feishu.RECIPIENT: self.feishu.EXP},
            'ledger_path': str(self.feishu_tmp / 'ledger.sqlite3'),
        }, ensure_ascii=False)

    def test_research_py_answer_with_channel_config(self):
        """公开入口 `research.py questions answer --channel-config <JSON>`（本地合成验签）。"""
        item = self.ledger.ask(self.feishu.EXP, '基线用哪一版？', owner='sess-01',
                               asked_by='agent', recipient=self.feishu.RECIPIENT)
        event = self.webhook(f"[question:{item['question_id']}] 用 v3 那一版")
        command = [sys.executable, '-X', 'utf8', '-B', str(ROOT / 'research.py'),
                   'questions', '--workspace', str(self.ws), 'answer', item['question_id'],
                   '--source', self.feishu.feishu.SOURCE, '--event-id', event['event_id'],
                   '--channel-config', self.channel_config_json()]
        completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                                   encoding='utf-8', timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload['ok'], payload)
        self.assertEqual(payload['question']['status'], 'answered')
        self.assertEqual(payload['question']['answered_by'], self.feishu.ALICE)
        self.assertEqual(payload['question']['verification'], 'verified')
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'answered')

    def test_research_py_answer_without_channel_config_is_refused(self):
        item = self.ledger.ask(self.feishu.EXP, '基线用哪一版？', owner='sess-01',
                               asked_by='agent', recipient=self.feishu.RECIPIENT)
        event = self.webhook(f"[question:{item['question_id']}] 用 v3 那一版")
        command = [sys.executable, '-X', 'utf8', '-B', str(ROOT / 'research.py'),
                   'questions', '--workspace', str(self.ws), 'answer', item['question_id'],
                   '--source', self.feishu.feishu.SOURCE, '--event-id', event['event_id']]
        completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                                   encoding='utf-8', timeout=120)
        self.assertEqual(completed.returncode, 9, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['error'], 'NoTrustedChannel')
        self.assertEqual(self.ledger.show(item['question_id'])['status'], 'open')


if __name__ == '__main__':
    unittest.main()
