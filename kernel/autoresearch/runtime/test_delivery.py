# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_delivery.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.dont_write_bytecode = True
try:
    from kernel.autoresearch.runtime.delivery import Delivery, DeliveryError, LeaseLost, PayloadConflict, Receipt, NotSent
    from kernel.autoresearch.runtime.store import Store
except ImportError:
    from kernel.autoresearch.runtime.delivery import Delivery, DeliveryError, LeaseLost, PayloadConflict, Receipt, NotSent
    from kernel.autoresearch.runtime.store import Store

class Clock:

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

class Fake:

    def __init__(self, delivery, *, lost=False, queryable=True, mutate=None):
        self.delivery = delivery
        self.lost = lost
        self.queryable = queryable
        self.mutate = mutate
        self.messages = {}
        self.calls = 0
        self.last_bytes = None
        self.delivery.receipt_verifier = self.verify_receipt

    def verify_receipt(self, conn, task, receipt):
        return self.messages.get(task['wire_key']) == receipt

    def send(self, payload, artifact_bytes):
        self.calls += 1
        conn = self.delivery.store.connect()
        try:
            row = conn.execute('SELECT * FROM delivery WHERE wire_key=?', (payload['request_key'],)).fetchone()
            assert row['status'] == 'sending', 'sending必须先持久化'
        finally:
            conn.close()
        self.last_bytes = artifact_bytes
        key = payload['request_key']
        receipt = Receipt('fake-' + str(len(self.messages) + 1), key, payload['exp_id'], payload['recipient'], payload['payload_hash'])
        self.messages[key] = receipt
        if self.mutate:
            self.mutate()
        if self.lost:
            raise TimeoutError('发送成功但响应丢失')
        return receipt

    def query(self, request_key):
        if not self.queryable:
            raise NotImplementedError('查询不支持')
        return self.messages.get(request_key)

class DeliveryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[3]
        cls.output = Path(tempfile.mkdtemp(prefix='hil_delivery_', dir=root / 'temp'))
        print('ARTIFACT_ROOT=' + str(cls.output))

    def setUp(self):
        self.case = self.output / self._testMethodName
        self.case.mkdir()
        self.store = Store(self.case / 'outbox.sqlite')
        self.store.ensure_experiment('exp-A')
        self.store.ensure_experiment('exp-B')
        self.clock = Clock()
        self.delivery = Delivery(self.store, clock=self.clock)

    def enqueue(self, **kw):
        values = dict(exp_id='exp-A', kind='notice', body='完成通知', recipient='chat-A', request_key='notice-1')
        values.update(kw)
        return self.delivery.enqueue(**values)

    def count(self, table):
        conn = self.store.connect()
        try:
            return conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
        finally:
            conn.close()

    def artifact(self):
        path = self.case / 'exp-A-paper.html'
        path.write_bytes(b'<html>specified version</html>')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return (path, sha)

    def test_idempotent_duplicate_not_sent_twice(self):
        first = self.enqueue()
        self.assertEqual(first['id'], self.enqueue()['id'])
        fake = Fake(self.delivery)
        claim = self.delivery.claim('worker')
        self.assertEqual(self.delivery.send(claim, fake)['status'], 'delivered')
        self.assertEqual(self.enqueue()['status'], 'delivered')
        self.assertIsNone(self.delivery.claim('worker'))
        with self.assertRaises(LeaseLost):
            self.delivery.send(claim, fake)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(self.count('delivery_receipts'), 1)

    def test_conflicting_payload_is_rejected(self):
        self.enqueue()
        for change in ({'body': '其他正文'}, {'recipient': 'chat-B'}, {'kind': 'summary'}):
            with self.assertRaises(PayloadConflict):
                self.enqueue(**change)
        self.assertEqual(self.count('delivery'), 1)

    def test_response_lost_reconcile_persistent_restart(self):
        row = self.enqueue()
        fake = Fake(self.delivery, lost=True)
        self.assertEqual(self.delivery.send(self.delivery.claim('old'), fake)['status'], 'unknown')
        self.delivery = Delivery(Store(self.case / 'outbox.sqlite'), clock=self.clock, receipt_verifier=fake.verify_receipt)
        claim = self.delivery.claim('new')
        self.assertEqual(self.delivery.reconcile(claim, fake)['status'], 'delivered')
        self.assertEqual(fake.calls, 1)
        self.assertEqual(self.delivery.get(row['id'])['attempts'], 1)

    def test_no_query_unknown_never_resent(self):
        self.enqueue()
        fake = Fake(self.delivery, lost=True, queryable=False)
        self.delivery.send(self.delivery.claim('w1'), fake)
        for _ in range(3):
            claim = self.delivery.claim('w2')
            with self.assertRaises(LeaseLost):
                self.delivery.send(claim, fake)
            self.assertEqual(self.delivery.reconcile(claim, fake)['status'], 'unknown')
        self.assertEqual(fake.calls, 1)
        self.assertEqual(self.count('delivery_receipts'), 0)

    def test_query_no_match_remains_unknown(self):
        self.enqueue()
        fake = Fake(self.delivery, lost=True)
        self.delivery.send(self.delivery.claim('w1'), fake)
        fake.messages.clear()
        result = self.delivery.reconcile(self.delivery.claim('w2'), fake)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(fake.calls, 1)

    def test_expired_old_worker_cannot_ack(self):
        row = self.enqueue()
        old = self.delivery.claim('old', lease_seconds=5)
        fake = Fake(self.delivery, mutate=lambda: setattr(self.clock, 'now', 1006.0))
        with self.assertRaises(LeaseLost):
            self.delivery.send(old, fake)
        self.assertEqual(self.delivery.get(row['id'])['status'], 'sending')
        new = self.delivery.claim('new')
        self.assertEqual(new['status'], 'unknown')
        self.assertGreater(new['fence'], old['fence'])
        receipt = fake.messages[row['wire_key']]
        with self.assertRaises(LeaseLost):
            self.delivery.ack(old, receipt)
        self.assertEqual(self.delivery.reconcile(new, fake)['status'], 'delivered')
        self.assertEqual(fake.calls, 1)

    def test_concurrent_claim_is_atomic(self):
        self.enqueue()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda n: self.delivery.claim('w' + str(n)), range(8)))
        self.assertEqual(sum((item is not None for item in claims)), 1)

    def test_expired_unsent_claim_can_be_reclaimed(self):
        self.enqueue()
        old = self.delivery.claim('old', lease_seconds=1)
        self.clock.now += 2
        new = self.delivery.claim('new')
        fake = Fake(self.delivery)
        with self.assertRaises(LeaseLost):
            self.delivery.send(old, fake)
        self.assertEqual(self.delivery.send(new, fake)['status'], 'delivered')
        self.assertEqual(fake.calls, 1)

    def test_experiments_are_independent(self):
        a = self.enqueue()
        b = self.enqueue(exp_id='exp-B', recipient='chat-B')
        self.assertNotEqual(a['wire_key'], b['wire_key'])
        fake = Fake(self.delivery, lost=True)
        self.delivery.send(self.delivery.claim('w', exp_id='exp-A'), fake)
        fake.lost = False
        self.delivery.send(self.delivery.claim('w', exp_id='exp-B'), fake)
        self.assertEqual(self.delivery.get(a['id'])['status'], 'unknown')
        self.assertEqual(self.delivery.get(b['id'])['status'], 'delivered')
        self.assertEqual(self.delivery.reconcile(self.delivery.claim('r', exp_id='exp-A'), fake)['status'], 'delivered')
        self.assertEqual(len(fake.messages), 2)

    def test_empty_and_cross_experiment_rejected(self):
        for kw in ({'body': None}, {'body': '  '}, {'body': '来自exp-B'}, {'exp_id': 'exp-missing'}, {'kind': 'reply'}, {'kind': 'reply', 'event_reply_to': {'event_id': 'ev', 'exp_id': 'exp-B', 'recipient': 'chat-A'}}, {'kind': 'reply', 'event_reply_to': {'event_id': 'ev', 'exp_id': 'exp-A', 'recipient': 'chat-B'}}):
            with self.assertRaises((DeliveryError, ValueError)):
                self.enqueue(**kw)
        self.assertEqual(self.count('delivery'), 0)

    def test_reply_binding_and_all_kinds(self):
        original_event = {'source': 'fake', 'event_id': 'ev', 'exp_id': 'exp-A', 'recipient': 'chat-A'}
        self.delivery.event_verifier = lambda conn, payload: payload['event_reply_to'] == original_event
        for kind in ('receipt', 'notice', 'summary', 'reply', 'approval_page', 'report'):
            self.enqueue(kind=kind, request_key=kind, event_reply_to={'source': 'fake', 'event_id': 'ev', 'exp_id': 'exp-A', 'recipient': 'chat-A'} if kind == 'reply' else None)
        self.assertEqual(self.count('delivery'), 6)

    def test_artifact_modified_before_send_is_blocked(self):
        path, sha = self.artifact()
        self.enqueue(body=None, artifact=path, sha256=sha, kind='report')
        path.write_bytes(b'changed')
        fake = Fake(self.delivery)
        self.assertEqual(self.delivery.send(self.delivery.claim('w'), fake)['status'], 'blocked')
        self.assertEqual(fake.calls, 0)
        self.assertEqual(self.store.get_experiment('exp-A')['state'], 'planned')
        self.assertEqual(self.delivery.status('exp-A')['scope'], 'delivery_only')

    def test_artifact_modified_during_send_cannot_ack(self):
        path, sha = self.artifact()
        expected = path.read_bytes()
        self.enqueue(body=None, artifact=path, sha256=sha, kind='report')
        fake = Fake(self.delivery, mutate=lambda: path.write_bytes(b'changed'))
        self.assertEqual(self.delivery.send(self.delivery.claim('w'), fake)['status'], 'unknown')
        self.assertEqual(fake.last_bytes, expected)
        self.assertEqual(self.delivery.reconcile(self.delivery.claim('r'), fake)['status'], 'unknown')
        self.assertEqual(self.count('delivery_receipts'), 0)
        self.assertEqual(fake.calls, 1)

    def test_print_is_not_delivery_receipt(self):

        class Printer:

            def send(self, payload, data):
                return 'sent successfully'
        self.enqueue()
        self.assertEqual(self.delivery.send(self.delivery.claim('w'), Printer())['status'], 'unknown')
        self.assertEqual(self.count('delivery_receipts'), 0)

    def test_ack_and_task_and_ledger_are_one_transaction(self):
        with self.store.transaction() as conn:
            conn.execute('CREATE TABLE host_tasks(id INTEGER PRIMARY KEY, status TEXT)')
            conn.execute("INSERT INTO host_tasks VALUES(1,'pending')")

        def hook(conn, row, receipt):
            conn.execute("UPDATE host_tasks SET status='done' WHERE id=1")
            raise RuntimeError('模拟宿主任务记账崩溃')
        self.delivery.ack_hook = hook
        row = self.enqueue()
        fake = Fake(self.delivery)
        claim = self.delivery.claim('w')
        with self.assertRaises(RuntimeError):
            self.delivery.send(claim, fake)
        self.assertEqual(self.delivery.get(row['id'])['status'], 'sending')
        self.assertEqual(self.count('delivery_receipts'), 0)
        conn = self.store.connect()
        try:
            self.assertEqual(conn.execute('SELECT status FROM host_tasks').fetchone()[0], 'pending')
            self.assertEqual(conn.execute("SELECT count(*) FROM events WHERE kind='delivery_acked'").fetchone()[0], 0)
        finally:
            conn.close()
        self.clock.now += 61
        self.delivery.ack_hook = lambda conn, row, receipt: conn.execute("UPDATE host_tasks SET status='done' WHERE id=1")
        self.assertEqual(self.delivery.reconcile(self.delivery.claim('r'), fake)['status'], 'delivered')
        self.assertEqual(self.count('delivery_receipts'), 1)
        self.assertEqual(fake.calls, 1)

    def test_enqueue_participates_in_host_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as conn:
                self.enqueue(conn=conn)
                raise RuntimeError('回滚')
        self.assertEqual(self.count('delivery'), 0)

    def test_unknown_does_not_starve_other_deliveries(self):
        self.enqueue()
        fake = Fake(self.delivery, lost=True, queryable=False)
        self.delivery.send(self.delivery.claim('w1'), fake)
        second = self.enqueue(kind='summary', request_key='summary-2')
        claim = self.delivery.claim('w2')
        self.assertEqual(claim['id'], second['id'])
        fake.lost = False
        self.assertEqual(self.delivery.send(claim, fake)['status'], 'delivered')
        self.assertEqual(self.store.get_experiment('exp-A')['state'], 'planned')

    def test_wrong_experiment_receipt_is_rejected(self):
        self.enqueue()
        fake = Fake(self.delivery, lost=True)
        self.delivery.send(self.delivery.claim('w'), fake)
        claim = self.delivery.claim('r')
        good = fake.messages[claim['wire_key']]
        bad = Receipt(good.message_id, good.request_key, 'exp-B', good.recipient, good.payload_hash)
        with self.assertRaises(DeliveryError):
            self.delivery.ack(claim, bad)
        self.assertEqual(self.count('delivery_receipts'), 0)
        self.assertEqual(self.delivery.ack(claim, good)['status'], 'delivered')

    def test_foreign_artifact_and_wrong_hash_are_rejected(self):
        path, sha = self.artifact()
        with self.assertRaises(DeliveryError):
            self.enqueue(body=None, artifact=path, sha256='0' * 64)
        foreign = self.case / 'exp-B-paper.html'
        foreign.write_bytes(path.read_bytes())
        with self.assertRaises(DeliveryError):
            self.enqueue(body=None, artifact=foreign, sha256=sha)
        self.assertEqual(self.count('delivery'), 0)

    def test_default_ack_rejects_self_constructed_receipt(self):
        row = self.enqueue()
        fake = Fake(self.delivery, lost=True)
        self.delivery.send(self.delivery.claim('w'), fake)
        claim = self.delivery.claim('r')
        self.delivery.receipt_verifier = None
        receipt = fake.messages[row['wire_key']]
        with self.assertRaises(DeliveryError):
            self.delivery.ack(claim, receipt)
        self.assertEqual(self.count('delivery_receipts'), 0)
        self.delivery.receipt_verifier = fake.verify_receipt
        forged = Receipt('invented-message', receipt.request_key, receipt.exp_id, receipt.recipient, receipt.payload_hash)
        with self.assertRaises(DeliveryError):
            self.delivery.ack(claim, forged)
        self.assertEqual(self.delivery.ack(claim, receipt)['status'], 'delivered')

    def test_default_reply_event_rejected_and_child_path_rejected(self):
        with self.assertRaises(DeliveryError):
            self.enqueue(kind='reply', event_reply_to={'source': 'fake', 'event_id': 'fake-event', 'exp_id': 'exp-A', 'recipient': 'chat-A'})
        child = self.case / 'exp-A-child'
        child.mkdir()
        path = child / 'exp-A-paper.html'
        path.write_bytes(b'specified bytes')
        with self.assertRaises(DeliveryError):
            self.enqueue(body=None, artifact=path, sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_explicit_not_sent_can_retry(self):

        class Reject:

            def send(self, payload, data):
                raise NotSent('明确没有执行发送')
        self.enqueue()
        self.assertEqual(self.delivery.send(self.delivery.claim('w'), Reject())['status'], 'queued')
        fake = Fake(self.delivery)
        self.assertEqual(self.delivery.send(self.delivery.claim('w2'), fake)['status'], 'delivered')
if __name__ == '__main__':
    unittest.main(verbosity=2)
