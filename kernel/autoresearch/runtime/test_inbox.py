# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_inbox.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import hmac
import json
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.dont_write_bytecode = True
try:
    from kernel.autoresearch.runtime.store import Store, canonical_json
    from kernel.autoresearch.runtime.accounts import Accounts, ApprovalEvidence, SourcePolicy, HMACSource
    from kernel.autoresearch.runtime.delivery import Delivery
    from kernel.autoresearch.runtime.inbox import Inbox, InboxError, EventConflict, QuestionLeaseLost
except ImportError:
    from kernel.autoresearch.runtime.store import Store, canonical_json
    from kernel.autoresearch.runtime.accounts import Accounts, ApprovalEvidence, SourcePolicy, HMACSource
    from kernel.autoresearch.runtime.delivery import Delivery
    from kernel.autoresearch.runtime.inbox import Inbox, InboxError, EventConflict, QuestionLeaseLost

class InboxTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[3]
        cls.output = Path(tempfile.mkdtemp(prefix='hil_inbox_', dir=root / 'temp'))
        print('ARTIFACT_ROOT=' + str(cls.output))

    def setUp(self):
        self.case = self.output / self._testMethodName
        self.case.mkdir()
        self.store = Store(self.case / 'inbox.sqlite')
        self.store.ensure_experiment('exp-A')
        self.store.ensure_experiment('exp-B')
        self.key = b'offline-test-secret-never-production' * 2
        self.evidence = ApprovalEvidence({'fake': SourcePolicy(HMACSource(self.key), frozenset(('user',)))})
        self.accounts = Accounts(self.store, self.evidence)
        self.delivery = Delivery(self.store)
        self.inbox = Inbox(self.store, self.accounts, self.delivery, self.evidence.verify, authorize_command=lambda principal, intent, scope, event: principal == 'user')
        self.delivery.event_verifier = self.inbox.verify_reply_event

    def event(self, **changes):
        event = {'event_id': 'ev-1', 'exp_id': 'exp-A', 'principal': 'user', 'intent': 'question', 'body': '目前进度如何？', 'recipient': 'chat-A', 'create_time': time.time()}
        event.update(changes)
        event['signature'] = hmac.new(self.key, canonical_json(event).encode('utf-8'), hashlib.sha256).hexdigest()
        return event

    def receive(self, **changes):
        event = self.event(**changes)
        return self.inbox.receive('fake', event)

    def authorize(self, **changes):
        values = dict(intent='authorize', action='grant', series_id='series-A', scope='experiment:exp-A', expires_at=time.time() + 3600, total_units=100)
        values.update(changes)
        return self.receive(**values)

    def count(self, table):
        conn = self.store.connect()
        try:
            return conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
        finally:
            conn.close()

    def test_receive_classify_apply_are_separate(self):
        self.assertEqual(self.receive()['status'], 'received')
        self.assertEqual(self.inbox.classify('fake', 'ev-1')['status'], 'classified')
        self.assertEqual(self.count('inbox_reply_tasks'), 0)
        row = self.inbox.apply('fake', 'ev-1')
        self.assertEqual(row['status'], 'applied')
        self.assertTrue(row['consumed'])
        self.assertFalse(json.loads(row['result_json'])['answered'])
        self.assertEqual(self.count('inbox_reply_tasks'), 1)
        self.assertEqual(self.count('delivery'), 0)

    def test_duplicate_and_hash_conflict(self):
        event = self.event(create_time=1000)
        row = self.inbox.receive('fake', event)
        self.assertEqual(self.inbox.receive('fake', event)['payload_hash'], row['payload_hash'])
        with self.assertRaises(EventConflict):
            self.inbox.receive('fake', self.event(body='不同正文'))
        with self.assertRaises(EventConflict):
            self.inbox.receive('fake', self.event(event_id='ev-2'), sha256='0' * 64)
        with self.assertRaises(EventConflict):
            self.inbox.receive('fake', self.event(exp_id='exp-B'))
        self.assertEqual(self.count('inbox_events'), 1)

    def test_without_authentication_never_applies(self):
        self.authorize()
        unsafe = Inbox(self.store, self.accounts, self.delivery)
        result = unsafe.apply('fake', 'ev-1')
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(result['consumed'])
        self.assertEqual(self.count('approval_events'), 0)
        self.assertEqual(self.count('budget_accounts'), 0)

    def test_tampered_signed_event_never_authorizes(self):
        event = self.event(intent='authorize', action='grant', series_id='s', scope='x', expires_at=time.time() + 3600, total_units=10)
        event['total_units'] = 100000
        self.inbox.receive('fake', event)
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'rejected')
        self.assertEqual(self.count('approval_events'), 0)

    def test_authenticate_must_not_mutate_event(self):
        self.receive()

        def mutate(source, event):
            event['intent'] = 'authorize'
            return 'user'
        self.inbox.authenticate = mutate
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'rejected')
        self.assertEqual(self.count('inbox_reply_tasks'), 0)

    def test_authorization_idempotency(self):
        self.authorize()
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'applied')
        for _ in range(3):
            self.assertTrue(self.inbox.apply('fake', 'ev-1')['consumed'])
        self.assertEqual(self.count('approval_events'), 1)
        self.assertEqual(self.count('budget_accounts'), 1)

    def test_accounts_commit_then_inbox_ack_crash_recovers(self):
        self.authorize()
        original = self.inbox._finish

        def crash(*args, **kwargs):
            raise RuntimeError('Accounts成功后Inbox ACK前崩溃')
        self.inbox._finish = crash
        with self.assertRaises(RuntimeError):
            self.inbox.apply('fake', 'ev-1')
        self.assertEqual(self.count('approval_events'), 1)
        self.assertEqual(self.inbox.get('fake', 'ev-1')['status'], 'applying')
        self.assertFalse(self.inbox.get('fake', 'ev-1')['consumed'])
        recovered = Inbox(Store(self.case / 'inbox.sqlite'), self.accounts, self.delivery, self.evidence.verify)
        self.assertEqual(recovered.apply('fake', 'ev-1')['status'], 'applied')
        self.assertEqual(self.count('approval_events'), 1)
        self.assertEqual(self.count('budget_accounts'), 1)
        self.inbox._finish = original

    def test_authorization_failure_remains_retryable(self):
        self.authorize()
        original = self.accounts.authorize
        self.accounts.authorize = lambda *args: (_ for _ in ()).throw(RuntimeError('临时失败'))
        row = self.inbox.apply('fake', 'ev-1')
        self.assertEqual(row['status'], 'retryable')
        self.assertFalse(row['consumed'])
        self.assertEqual(self.count('approval_events'), 0)
        self.accounts.authorize = original
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'applied')

    def test_stop_without_handler_does_not_claim_stopped(self):
        self.receive(intent='stop', scope='experiment', body='stop')
        row = self.inbox.apply('fake', 'ev-1')
        self.assertEqual(row['status'], 'command_pending')
        self.assertFalse(row['consumed'])
        self.assertIn('尚未执行', row['error'])
        self.assertEqual(self.store.get_experiment('exp-A')['state'], 'planned')
        self.assertEqual(self.count('approval_events'), 0)

    def test_stop_handler_current_experiment_only(self):
        self.receive(intent='stop', scope='experiment')

        def handler(conn, event, request_key):
            conn.execute("UPDATE experiments SET state='paused' WHERE id=?", (event['exp_id'],))
            return {'applied': True, 'scope': event['scope']}
        self.assertEqual(self.inbox.apply('fake', 'ev-1', command_handler=handler)['status'], 'applied')
        self.assertEqual(self.store.get_experiment('exp-A')['state'], 'paused')
        self.assertEqual(self.store.get_experiment('exp-B')['state'], 'planned')
        self.assertEqual(self.count('approval_events'), 0)

    def test_stop_requires_explicit_scope_and_plain_stop_not_authorization(self):
        self.receive(intent='stop')
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'rejected')
        self.receive(event_id='ev-2', intent='other', body='stop')
        self.assertEqual(self.inbox.apply('fake', 'ev-2')['status'], 'command_pending')
        self.assertEqual(self.count('approval_events'), 0)

    def test_global_stop_is_explicit_and_handler_failure_rolls_back(self):
        self.receive(intent='stop', scope='global')

        def bad(conn, event, request_key):
            conn.execute("UPDATE experiments SET state='paused'")
            return {'applied': False}
        row = self.inbox.apply('fake', 'ev-1', command_handler=bad)
        self.assertEqual(row['status'], 'retryable')
        self.assertFalse(row['consumed'])
        self.assertEqual(self.store.get_experiment('exp-A')['state'], 'planned')
        self.assertEqual(self.store.get_experiment('exp-B')['state'], 'planned')

        def good(conn, event, request_key):
            self.assertEqual(event['scope'], 'global')
            conn.execute("UPDATE experiments SET state='paused'")
            return {'applied': True, 'scope': 'global'}
        self.assertTrue(self.inbox.apply('fake', 'ev-1', command_handler=good)['consumed'])
        self.assertEqual(self.store.get_experiment('exp-B')['state'], 'paused')

    def test_reject_and_late_are_recorded_without_authorization(self):
        for number, intent in enumerate(('reject', 'late')):
            event_id = 'ev-' + str(number + 1)
            self.receive(event_id=event_id, intent=intent, scope='experiment')
            handler = lambda conn, event, key: {'applied': True, 'decision_id': 'D-A', 'version': 1}
            row = self.inbox.apply('fake', event_id, command_handler=handler)
            if intent == 'late':
                self.assertEqual(json.loads(row['result_json'])['recorded'], intent)
            else:
                self.assertEqual(json.loads(row['result_json'])['decision_id'], 'D-A')
            self.assertTrue(row['consumed'])
        self.assertEqual(self.count('approval_events'), 0)

    def test_question_answer_atomic_enqueue_with_prefix(self):
        self.receive()
        self.inbox.apply('fake', 'ev-1')
        self.inbox.apply('fake', 'ev-1')
        pending = self.inbox.pending_questions('exp-A')
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['status'], 'pending')
        claim = self.inbox.claim_question('agent')
        with self.assertRaises(InboxError):
            self.inbox.answer(claim, '已收到')
        self.assertEqual(self.count('delivery'), 0)
        task = self.inbox.answer(claim, '评测已完成，正在整理证据。')
        self.assertEqual(task['kind'], 'reply')
        self.assertEqual(task['status'], 'queued')
        payload = json.loads(task['payload_json'])
        self.assertTrue(payload['body'].startswith('答你的提问（本轮 exp-A）：'))
        self.assertEqual(payload['event_reply_to']['event_id'], 'ev-1')
        self.assertEqual(len(self.inbox.pending_questions()), 1)
        self.assertEqual(self.inbox.pending_questions()[0]['delivery_status'], 'queued')
        self.assertIsNone(self.inbox.claim_question('another'))
        with self.assertRaises(QuestionLeaseLost):
            self.inbox.answer(claim, '重复答案')
        self.assertEqual(self.count('delivery'), 1)

    def test_question_enqueue_failure_rolls_back_answer(self):
        self.receive()
        self.inbox.apply('fake', 'ev-1')
        claim = self.inbox.claim_question('agent')
        original = self.delivery.enqueue

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('enqueue后崩溃')
        self.delivery.enqueue = crash
        with self.assertRaises(RuntimeError):
            self.inbox.answer(claim, '真实答案')
        self.assertEqual(self.count('delivery'), 0)
        self.assertEqual(self.inbox.pending_questions()[0]['status'], 'leased')
        self.assertIsNone(self.inbox.pending_questions()[0]['answer'])
        self.delivery.enqueue = original
        self.assertEqual(self.inbox.answer(claim, '真实答案')['status'], 'queued')

    def test_question_creation_and_consumption_are_atomic(self):
        self.receive()
        original = self.store.emit

        def fail(conn, exp_id, kind, actor, payload):
            if kind == 'inbox_applied':
                raise RuntimeError('消费事件时崩溃')
            return original(conn, exp_id, kind, actor, payload)
        self.store.emit = fail
        row = self.inbox.apply('fake', 'ev-1')
        self.assertFalse(row['consumed'])
        self.assertEqual(row['status'], 'retryable')
        self.assertEqual(self.count('inbox_reply_tasks'), 0)
        self.store.emit = original
        self.assertTrue(self.inbox.apply('fake', 'ev-1')['consumed'])
        self.assertEqual(self.count('inbox_reply_tasks'), 1)

    def test_question_claim_is_atomic_and_expired_worker_fenced(self):
        self.receive()
        self.inbox.apply('fake', 'ev-1')
        now = [1000.0]
        self.inbox.clock = lambda: now[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims = list(pool.map(lambda n: self.inbox.claim_question('w' + str(n), lease_seconds=1), range(6)))
        valid = [claim for claim in claims if claim is not None]
        self.assertEqual(len(valid), 1)
        old = valid[0]
        now[0] += 2
        new = self.inbox.claim_question('new')
        with self.assertRaises(QuestionLeaseLost):
            self.inbox.answer(old, '旧worker答案')
        self.assertEqual(self.inbox.answer(new, '新worker答案')['status'], 'queued')

    def test_command_permission_is_separate_from_authentication(self):
        self.receive(intent='stop', scope='global')
        self.inbox.authorize_command = None
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'rejected')
        self.inbox.authorize_command = lambda principal, intent, scope, event: scope == 'experiment'
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'rejected')
        self.assertFalse(self.inbox.get('fake', 'ev-1')['consumed'])

    def test_rejection_requires_handler_and_decision_version(self):
        self.receive(intent='reject', scope='experiment')
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'command_pending')
        row = self.inbox.apply('fake', 'ev-1', command_handler=lambda conn, event, key: {'applied': True})
        self.assertEqual(row['status'], 'retryable')
        self.assertFalse(row['consumed'])
        row = self.inbox.apply('fake', 'ev-1', command_handler=lambda conn, event, key: {'applied': True, 'decision_id': 'D1', 'version': 2})
        self.assertTrue(row['consumed'])

    def test_unverifiable_time_never_executes(self):
        self.authorize(create_time=None)
        self.assertEqual(self.inbox.apply('fake', 'ev-1')['status'], 'rejected')
        self.assertEqual(self.count('approval_events'), 0)

    def test_drain_orders_and_does_not_skip_earlier_rejection(self):
        self.authorize(event_id='new-approval', create_time=2000)
        self.receive(event_id='old-reject', intent='reject', scope='experiment', create_time=1000)
        self.assertEqual([r['event_id'] for r in self.inbox.pending_events()], ['old-reject', 'new-approval'])
        result = self.inbox.drain()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'command_pending')
        self.assertEqual(self.count('approval_events'), 0)
        result = self.inbox.drain(command_handler=lambda conn, event, key: {'applied': True, 'decision_id': 'D1', 'version': 1})
        self.assertEqual([r['category'] for r in result], ['reject', 'authorize'])
        self.assertEqual(self.count('approval_events'), 1)

    def test_queued_unknown_visible_until_real_ack(self):
        try:
            from .test_delivery import Fake
        except ImportError:
            from test_delivery import Fake
        self.receive()
        self.inbox.apply('fake', 'ev-1')
        self.inbox.answer(self.inbox.claim_question('agent'), '任务已经完成')
        fake = Fake(self.delivery, lost=True)
        self.delivery.send(self.delivery.claim('send'), fake)
        self.assertEqual(self.inbox.pending_questions()[0]['delivery_status'], 'unknown')
        self.assertIsNone(self.inbox.claim_question('agent2'))
        self.delivery.reconcile(self.delivery.claim('reconcile'), fake)
        self.assertEqual(self.inbox.pending_questions(), [])
        self.assertEqual(self.inbox.question_overview()[0]['answer_status'], 'answered')

    def test_source_binding_cannot_cross_same_event_id(self):
        self.receive()
        self.inbox.apply('fake', 'ev-1')
        binding = {'source': 'foreign', 'event_id': 'ev-1', 'exp_id': 'exp-A', 'recipient': 'chat-A'}
        with self.assertRaises(ValueError):
            self.delivery.enqueue('exp-A', 'reply', '真实答案', recipient='chat-A', event_reply_to=binding, request_key='forged')
        self.assertEqual(self.count('delivery'), 0)

    def test_questions_across_experiments_are_independent(self):
        self.receive()
        self.receive(event_id='ev-2', exp_id='exp-B', recipient='chat-B')
        self.inbox.apply('fake', 'ev-1')
        self.inbox.apply('fake', 'ev-2')
        a = self.inbox.claim_question('a', exp_id='exp-A')
        b = self.inbox.claim_question('b', exp_id='exp-B')
        with self.assertRaises(ValueError):
            self.inbox.answer(a, '另一个实验exp-B完成了')
        self.assertEqual(self.inbox.answer(a, '本轮已完成')['exp_id'], 'exp-A')
        self.assertEqual(self.inbox.answer(b, '本轮还在跑')['exp_id'], 'exp-B')
if __name__ == '__main__':
    unittest.main(verbosity=2)
