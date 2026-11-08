# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_scheduler.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import os
import sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
if __package__:
    from kernel.autoresearch.runtime.scheduler import Scheduler, SchedulerError, FencingError, IdempotencyConflict, OwnershipConflict, PausedError
    from kernel.autoresearch.runtime.store import Store
else:
    from kernel.autoresearch.runtime.scheduler import Scheduler, SchedulerError, FencingError, IdempotencyConflict, OwnershipConflict, PausedError
    from kernel.autoresearch.runtime.store import Store

class Clock:

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

class SchedulerTests(unittest.TestCase):

    def setUp(self):
        temp_root = Path(__file__).resolve().parents[3] / 'temp'
        self.temp = tempfile.TemporaryDirectory(prefix='scheduler_test_', dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'runtime.sqlite'
        self.clock = Clock()
        self.store = Store(self.path)
        self.s = Scheduler(self.store, clock=self.clock)
        self.s.activate('e1', '实验一')
        self.owner = self.s.takeover('e1', 'o1', lease_seconds=100)

    def enqueue(self, key='key', payload=None, **kw):
        return self.s.enqueue('e1', key, payload or {'message': '继续'}, 'o1', 1, **kw)

    def claim(self, worker='w1', **kw):
        return self.s.claim('e1', 'o1', 1, worker, **kw)

    def ack(self, task, **kw):
        return self.s.ack(task['id'], 'o1', 1, task['worker_id'], task['token'], **kw)

    def test_duplicate_enqueue_is_persistent_and_canonical(self):
        first = self.enqueue(payload={'a': 1, 'b': 2})
        second = self.enqueue(payload={'b': 2, 'a': 1})
        reopened = Scheduler(Store(self.path), clock=self.clock)
        third = reopened.enqueue('e1', 'key', {'a': 1, 'b': 2}, 'o1', 1)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['id'], third['id'])
        with closing(self.store.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE kind='scheduler.enqueued'").fetchone()[0], 1)

    def test_same_key_different_payload_or_spec_conflicts(self):
        self.enqueue()
        with self.assertRaises(IdempotencyConflict):
            self.enqueue(payload={'message': '其它'})
        with self.assertRaises(IdempotencyConflict):
            self.enqueue(paid=True)
        with self.assertRaises(IdempotencyConflict):
            self.enqueue(resources=['machine:m1'])

    def test_expired_lease_redelivered_with_new_token(self):
        original = self.enqueue(resources=['machine:m1', 'env:m1/e'])
        first = self.claim(lease_seconds=5)
        self.clock.now += 5
        self.assertEqual(self.s.overview()['experiments'][0]['expired_tasks'], 1)
        with self.assertRaises(FencingError):
            self.ack(first)
        second = self.claim('w2', lease_seconds=5)
        self.assertEqual(original['id'], second['id'])
        self.assertNotEqual(first['token'], second['token'])
        self.assertEqual(second['attempts'], 2)
        with self.assertRaises(FencingError):
            self.ack(first)
        self.ack(second)
        self.assertEqual(self.s.overview()['resources'], [])

    def test_owner_takeover_fences_all_old_writes(self):
        task = self.enqueue()
        first = self.claim()
        with self.assertRaises(OwnershipConflict):
            self.s.takeover('e1', 'o2')
        new = self.s.takeover('e1', 'o2', force=True)
        self.assertEqual(new['generation'], 2)
        operations = [lambda: self.enqueue('new'), lambda: self.claim(), lambda: self.ack(first), lambda: self.s.retry(first['id'], 'o1', 1, 'w1', first['token']), lambda: self.s.cancel(task['id'], 'o1', 1, reason='旧编排者'), lambda: self.s.renew_owner('e1', 'o1', 1), lambda: self.s.renew_task(first['id'], 'o1', 1, 'w1', first['token'])]
        for operation in operations:
            with self.assertRaises(FencingError):
                operation()
        next_task = self.s.claim('e1', 'o2', 2, 'w2')
        self.assertEqual(next_task['id'], first['id'])
        self.assertEqual(next_task['generation'], 2)

    def test_owner_expiry_requires_takeover(self):
        self.enqueue()
        self.clock.now += 100
        with self.assertRaises(FencingError):
            self.claim()
        new = self.s.takeover('e1', 'o2')
        self.assertEqual(new['generation'], 2)
        self.assertIsNotNone(self.s.claim('e1', 'o2', 2, 'w2'))

    def test_ack_is_idempotent_but_result_cannot_change(self):
        self.enqueue()
        task = self.claim()
        first = self.ack(task, result={'ok': True})
        second = self.ack(task, result={'ok': True})
        self.assertEqual(first, second)
        with self.assertRaises(IdempotencyConflict):
            self.ack(task, result={'ok': False})
        with closing(self.store.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE kind='scheduler.acked'").fetchone()[0], 1)

    def test_retry_delay_and_token_fencing(self):
        self.enqueue(resources=['machine:m1'])
        first = self.claim()
        self.s.retry(first['id'], 'o1', 1, 'w1', first['token'], delay_seconds=5, error='暂时失败')
        self.assertIsNone(self.claim())
        with self.assertRaises(FencingError):
            self.ack(first)
        self.clock.now += 5
        second = self.claim()
        self.assertNotEqual(second['token'], first['token'])
        self.assertEqual(second['last_error'], '暂时失败')

    def test_concurrent_claim_single_task(self):
        self.enqueue()
        barrier = threading.Barrier(8)

        def claim(index):
            barrier.wait(timeout=10)
            return self.claim(f'w{index}')
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = list(pool.map(claim, range(8)))
        self.assertEqual(sum((t is not None for t in claimed)), 1)

    def test_concurrent_enqueue_one_identity(self):
        barrier = threading.Barrier(8)

        def enqueue(index):
            barrier.wait(timeout=10)
            return self.enqueue()['id']
        with ThreadPoolExecutor(max_workers=8) as pool:
            identities = list(pool.map(enqueue, range(8)))
        self.assertEqual(len(set(identities)), 1)

    def test_three_experiments_resource_contention_and_independence(self):
        for exp in ('e2', 'e3'):
            self.s.activate(exp)
            self.s.takeover(exp, 'o' + exp[-1])
        for index in (1, 2, 3):
            self.s.enqueue(f'e{index}', 'same-key', {'exp': index}, f'o{index}', 1, resources=['machine:shared', 'env:shared/e'])
        barrier = threading.Barrier(3)

        def claim(index):
            barrier.wait(timeout=10)
            return self.s.claim(f'e{index}', f'o{index}', 1, f'w{index}')
        with ThreadPoolExecutor(max_workers=3) as pool:
            claimed = list(pool.map(claim, (1, 2, 3)))
        winners = [t for t in claimed if t]
        self.assertEqual(len(winners), 1)
        task = winners[0]
        index = task['exp_id'][-1]
        self.s.ack(task['id'], f'o{index}', 1, f'w{index}', task['token'])
        overview = self.s.overview()
        self.assertEqual(overview['active_experiments'], ['e1', 'e2', 'e3'])
        counts = {e['id']: e['tasks'] for e in overview['experiments']}
        self.assertEqual(counts[f'e{index}'], {'acked': 1})
        for exp in {'e1', 'e2', 'e3'} - {f'e{index}'}:
            self.assertEqual(counts[exp], {'pending': 1})
            self.assertIsNotNone(self.s.claim(exp, 'o' + exp[-1], 1, 'new-worker'))
            break

    def test_resource_acquisition_all_or_nothing(self):
        self.enqueue('a', resources=['machine:m1'])
        first = self.claim()
        self.enqueue('b', resources=['env:free', 'machine:m1'])
        self.assertIsNone(self.claim('w2'))
        self.assertEqual([r['resource_key'] for r in self.s.overview()['resources']], ['machine:m1'])
        self.ack(first)
        self.assertEqual(len(self.claim('w2')['resources']), 2)

    def test_pauses_and_cancel_are_distinct(self):
        self.enqueue('paid', paid=True)
        self.enqueue('free')
        self.enqueue('new', starts_experiment=True)
        self.s.set_control(pause_new_experiments=True, pause_paid=True, reason='用户暂停')
        free = self.claim()
        self.assertEqual(free['idempotency_key'], 'free')
        self.ack(free)
        self.assertIsNone(self.claim())
        with self.assertRaises(PausedError):
            self.s.activate('new-experiment')
        self.assertIsNone(self.store.get_experiment('new-experiment'))
        self.s.set_control(pause_paid=False, reason='恢复付费')
        paid = self.claim()
        self.assertEqual(paid['idempotency_key'], 'paid')
        self.s.cancel(paid['id'], 'o1', 1, reason='只取消此任务')
        with self.assertRaises(FencingError):
            self.ack(paid)
        self.assertIsNone(self.claim())
        self.s.set_control(pause_new_experiments=False, reason='恢复新实验')
        self.assertEqual(self.claim()['idempotency_key'], 'new')

    def test_active_set_explicit_and_deactivation_does_not_cancel(self):
        task = self.enqueue()
        self.s.set_active('e1', False)
        self.assertEqual(self.s.active_experiments(), [])
        self.assertIsNone(self.claim())
        self.assertEqual(self.s.get_task(task['id'])['status'], 'pending')
        self.s.set_active('e1', True)
        self.assertIsNotNone(self.claim())

    def test_renewal_extends_resources_and_caps_at_owner_lease(self):
        self.enqueue(resources=['machine:m1'])
        task = self.claim(lease_seconds=5)
        self.clock.now += 4
        renewed = self.s.renew_task(task['id'], 'o1', 1, 'w1', task['token'], lease_seconds=500)
        self.assertEqual(renewed['lease_until'], self.owner['lease_until'])
        self.assertEqual(self.s.overview()['resources'][0]['lease_until'], renewed['lease_until'])

    def test_owner_renewal_never_shortens_existing_lease(self):
        before = self.owner['lease_until']
        self.clock.now += 1
        renewed = self.s.renew_owner('e1', 'o1', 1, lease_seconds=2)
        self.assertEqual(before, renewed['lease_until'])
        extended = self.s.renew_owner('e1', 'o1', 1, lease_seconds=200)
        self.assertGreater(extended['lease_until'], before)

    def test_env_lease_conflicts_even_on_different_machines(self):
        self.enqueue('a', resources=['machine:m1', 'env:shared/e'])
        first = self.claim()
        self.enqueue('b', resources=['machine:m2', 'env:shared/e'])
        self.assertIsNone(self.claim('w2'))
        self.assertNotIn('machine:m2', [r['resource_key'] for r in self.s.overview()['resources']])
        self.ack(first)
        self.assertIsNotNone(self.claim('w2'))

    def test_dispatch_is_structured_and_notification_does_not_ack(self):
        self.enqueue(target_agent_id='direct-child')
        task = self.claim()
        self.assertEqual(task['dispatch']['agent_id'], 'direct-child')
        self.assertEqual(task['dispatch']['tool'], 'send_message')
        self.assertEqual(self.s.get_task(task['id'])['status'], 'leased')
        self.assertIsNone(self.claim())

    def test_rejected_mutation_leaves_no_event(self):
        self.enqueue()
        with closing(self.store.connect()) as conn:
            before = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        with self.assertRaises(IdempotencyConflict):
            self.enqueue(payload={'different': True})
        with closing(self.store.connect()) as conn:
            self.assertEqual(before, conn.execute('SELECT COUNT(*) FROM events').fetchone()[0])

    def test_task_short_renewal_does_not_shorten_lease(self):
        self.enqueue(resources=['machine:m1'])
        task = self.claim(lease_seconds=40)
        self.clock.now += 1
        renewed = self.s.renew_task(task['id'], 'o1', 1, 'w1', task['token'], lease_seconds=2)
        self.assertEqual(renewed['lease_until'], task['lease_until'])
        self.assertEqual(self.s.overview()['resources'][0]['lease_until'], task['lease_until'])

    def test_paid_resources_quarantined_on_every_attempt_end(self):
        for mode in ('ack', 'retry', 'cancel', 'expiry', 'takeover'):
            with self.subTest(mode=mode):
                exp = 'paid-' + mode
                self.s.activate(exp)
                self.s.takeover(exp, 'owner')
                task = self.s.enqueue(exp, 'key', {}, 'owner', 1, kind='execution', paid=True, resources=['machine:' + mode, 'env:' + mode])
                held = self.s.claim(exp, 'owner', 1, 'worker', lease_seconds=5)
                if mode == 'ack':
                    self.s.ack(task['id'], 'owner', 1, 'worker', held['token'])
                elif mode == 'retry':
                    self.s.retry(task['id'], 'owner', 1, 'worker', held['token'])
                elif mode == 'cancel':
                    self.s.cancel(task['id'], 'owner', 1, reason='用户取消')
                elif mode == 'expiry':
                    self.clock.now += 5
                    self.assertIsNone(self.s.claim(exp, 'owner', 1, 'again'))
                else:
                    self.s.takeover(exp, 'replacement', force=True)
                    self.assertIsNone(self.s.claim(exp, 'replacement', 2, 'again'))
                leases = [r for r in self.s.overview()['resources'] if r['exp_id'] == exp]
                self.assertEqual(len(leases), 2)
                self.assertTrue(all((r['state'] == 'unknown' and r['occupied'] for r in leases)))
                self.enqueue('compete-' + mode, resources=['machine:' + mode])
                self.assertIsNone(self.claim())
                reopened = Scheduler(Store(self.path), clock=self.clock)
                self.assertEqual(len([r for r in reopened.overview()['resources'] if r['exp_id'] == exp]), 2)

    def test_idle_confirmation_requires_verifier_and_attempt_bound_evidence(self):
        self.enqueue(paid=True, kind='execution', resources=['machine:m1'])
        held = self.claim()
        evidence = {'resource_key': 'machine:m1', 'task_id': held['id'], 'token': held['token'], 'proof': 'offline-fixture'}
        self.ack(held)
        with self.assertRaises(SchedulerError):
            self.s.confirm_resource_idle('machine:m1', evidence)
        denied = Scheduler(self.store, clock=self.clock, idle_verifier=lambda *args: False)
        with self.assertRaises(SchedulerError):
            denied.confirm_resource_idle('machine:m1', evidence)
        verified = Scheduler(self.store, clock=self.clock, idle_verifier=lambda resource, proof, held: proof.get('proof') == 'offline-fixture')
        with self.assertRaises(FencingError):
            verified.confirm_resource_idle('machine:m1', {**evidence, 'token': 'stale'})
        self.assertEqual(verified.overview()['resources'][0]['state'], 'unknown')
        self.assertTrue(verified.confirm_resource_idle('machine:m1', evidence)['released'])
        self.assertEqual(self.s.overview()['resources'], [])
        self.enqueue('next', resources=['machine:m1'])
        self.assertIsNotNone(self.claim())

    def test_live_resource_cannot_be_released_by_idle_confirmation(self):
        self.enqueue(paid=True, kind='execution', resources=['machine:m1'])
        held = self.claim()
        verified = Scheduler(self.store, clock=self.clock, idle_verifier=lambda *args: True)
        with self.assertRaises(SchedulerError):
            verified.confirm_resource_idle('machine:m1', {'resource_key': 'machine:m1', 'task_id': held['id'], 'token': held['token']})
        self.assertEqual(self.s.overview()['resources'][0]['state'], 'leased')

    def test_terminal_experiment_cannot_enqueue_or_claim_paid(self):
        for state in ('failed', 'completed'):
            exp = 'terminal-' + state
            self.s.activate(exp)
            self.s.takeover(exp, 'owner')
            self.s.enqueue(exp, 'paid', {}, 'owner', 1, paid=True, kind='execution')
            with self.store.transaction() as conn:
                conn.execute('UPDATE experiments SET state=? WHERE id=?', (state, exp))
            with self.assertRaises(SchedulerError):
                self.s.enqueue(exp, 'new-paid', {}, 'owner', 1, paid=True)
            self.assertIsNone(self.s.claim(exp, 'owner', 1, 'worker'))
            self.s.enqueue(exp, 'report', {}, 'owner', 1, kind='report')
            self.assertEqual(self.s.claim(exp, 'owner', 1, 'worker')['kind'], 'report')
        with self.assertRaises(ValueError):
            self.enqueue('bad-plan', kind='plan', paid=True)

    def test_invalid_values_fail_closed(self):
        for lease in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.claim(lease_seconds=lease)
        with self.assertRaises(ValueError):
            self.enqueue(resources='machine:m1')
        with self.assertRaises(ValueError):
            self.enqueue(payload={'bad': float('nan')})
if __name__ == '__main__':
    unittest.main(verbosity=2)
