# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_delegation.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from kernel.autoresearch.runtime.delegation import Delegation, DelegationError, FencingError, HostVerificationError, HostSession, VerifiedHostEvent, payload_hash
from kernel.autoresearch.runtime.store import Store, IdempotencyConflict

class FakeTrustedHost:

    def __init__(self):
        self.events = {}
        self.sessions = (HostSession('root', None, 0), HostSession('orch', 'root', 1), HostSession('child', 'orch', 2))

    def issue(self, action, principal, expected):
        ref = 'event-' + str(len(self.events))
        self.events[ref] = VerifiedHostEvent(action, principal, ref, payload_hash(expected), self.sessions)
        return ref

    def verify(self, action, principal, evidence, expected):
        if not isinstance(evidence, str):
            return None
        return self.events.get(evidence)

class DelegationTests(unittest.TestCase):

    def setUp(self):
        root = Path(__file__).resolve().parents[3]
        self.temp = tempfile.TemporaryDirectory(prefix='delegation-', dir=root / 'temp')
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.ensure_experiment('exp-test')
        self.host = FakeTrustedHost()
        self.ledger = Delegation(self.store, host_verifier=self.host)
        self.h = 'a' * 64

    def tearDown(self):
        self.temp.cleanup()

    def register(self, generation=1, orch='orch', key='register'):
        expected = dict(action='register', exp_id='exp-test', root_session='root', orchestrator_session=orch, parent_session='root', depth=1, rolepack_hash=self.h, generation=generation, principal='root')
        ref = self.host.issue('register', 'root', expected)
        return self.ledger.register('exp-test', 'root', orch, self.h, generation, principal='root', evidence=ref, request_key=key)

    def request(self, key='request', generation=1, role='planner', after_task_id=None):
        expected = dict(action='request', exp_id='exp-test', generation=generation, role=role, input_hash=self.h, expected_artifact_hash=self.h, orchestrator_session='orch', principal='orch', after_task_id=after_task_id)
        ref = self.host.issue('request', 'orch', expected)
        return self.ledger.request_task('exp-test', generation, role, self.h, self.h, principal='orch', evidence=ref, request_key=key, after_task_id=after_task_id)

    def change(self, task, action, changes, *, principal='orch', key=None):
        child = changes.get('child_session', task['child_session'])
        expected = dict(action=action, exp_id=task['exp_id'], task_id=task['task_id'], generation=task['generation'], orchestrator_session=task['orchestrator_session'], role=task['role'], input_hash=task['input_hash'], expected_artifact_hash=task['expected_artifact_hash'], child_session=child, parent_session=task['orchestrator_session'], depth=2, principal=principal, **{k: v for k, v in changes.items() if k != 'child_session'})
        if action == 'review':
            expected.update(result_hash=task['result_hash'], outcome=task['outcome'])
        ref = self.host.issue(action, principal, expected)
        return getattr(self.ledger, action)(task['task_id'], **changes, principal=principal, evidence=ref, request_key=key or action)

    def test_lifecycle_and_persistence(self):
        self.register()
        task = self.request()
        self.assertEqual(task['status'], 'requested')
        task = self.change(task, 'dispatch', {'child_session': 'child'})
        self.assertEqual(task['parent_session'], 'orch')
        self.assertEqual(task['depth'], 2)
        task = self.change(task, 'settle', {'result_hash': self.h, 'outcome': 'finished'}, principal='child')
        status = self.ledger.status('exp-test')
        self.assertTrue(status['waiting'])
        self.assertFalse(status['accepted'])
        self.assertEqual(task['status'], 'settled')
        task = self.change(task, 'review', {'accept': True, 'reason': 'artifacts checked'})
        self.assertEqual(task['status'], 'accepted')
        reopened = Delegation(Store(self.store.db_path))
        self.assertTrue(reopened.status('exp-test')['accepted'])
        self.assertEqual(self.store.get_experiment('exp-test')['state'], 'planned')

    def test_default_denial_and_boolean_verifier_denial(self):
        with self.assertRaises(HostVerificationError):
            Delegation(self.store, host_verifier=lambda *args: True)
        with self.assertRaises(HostVerificationError):
            Delegation(self.store).register('exp-test', 'root', 'orch', self.h, 1, principal='root', evidence={'parent': 'root'}, request_key='x')
        self.assertFalse(self.ledger.status('exp-test')['registered'])

    def test_caller_json_cannot_self_certify(self):
        with self.assertRaises(HostVerificationError):
            self.ledger.register('exp-test', 'root', 'orch', self.h, 1, principal='root', evidence={'trusted': True, 'parent': 'root', 'depth': 1}, request_key='x')

    def test_exact_host_parent_and_root_metadata_required(self):
        for sessions in ((HostSession('orch', 'root', 1),), (HostSession('root', 'other', 0), HostSession('orch', 'root', 1)), (HostSession('root', None, 0), HostSession('orch', 'other', 1)), (HostSession('root', None, 0), HostSession('orch', 'root', 2))):
            with self.subTest(sessions=sessions):
                self.host.sessions = sessions
                with self.assertRaises(HostVerificationError):
                    self.register()
        self.assertFalse(self.ledger.status('exp-test')['registered'])

    def test_child_direct_parent_depth_and_content_binding(self):
        self.register()
        task = self.request()
        original = self.host.sessions
        for bad in (HostSession('child', 'root', 1), HostSession('child', 'other', 2), HostSession('child', 'orch', 3)):
            self.host.sessions = original[:2] + (bad,)
            with self.assertRaises(HostVerificationError):
                self.change(task, 'dispatch', {'child_session': 'child'})
        self.host.sessions = original
        self.host.events['tree'] = VerifiedHostEvent('dispatch', 'orch', 'tree', self.h, original)
        with self.assertRaises(HostVerificationError):
            self.ledger.dispatch(task['task_id'], 'child', principal='orch', evidence='tree', request_key='tree')
        self.assertEqual(self.ledger.status('exp-test')['tasks'][0]['status'], 'requested')

    def test_root_cannot_dispatch_or_review_or_request(self):
        self.register()
        task = self.request()
        with self.assertRaises(FencingError):
            self.ledger.request_task('exp-test', 1, 'runner', self.h, self.h, principal='root', evidence=None, request_key='illegal')
        with self.assertRaises(FencingError):
            self.change(task, 'dispatch', {'child_session': 'child'}, principal='root')
        task = self.change(task, 'dispatch', {'child_session': 'child'})
        task = self.change(task, 'settle', {'result_hash': self.h, 'outcome': 'finished'})
        for principal in ('root', 'child', 'sibling'):
            with self.assertRaises(FencingError):
                self.change(task, 'review', {'accept': True, 'reason': 'claim'}, principal=principal)

    def test_generation_fences_even_idempotent_old_requests(self):
        self.register()
        task = self.request()
        task = self.change(task, 'dispatch', {'child_session': 'child'})
        self.register(generation=2, key='register-2')
        with self.assertRaises(FencingError):
            self.change(task, 'settle', {'result_hash': self.h, 'outcome': 'finished'})
        with self.assertRaises(FencingError):
            self.request()
        with self.assertRaises(FencingError):
            self.register()
        self.assertFalse(self.ledger.status('exp-test')['accepted'])

    def test_idempotency_and_conflicting_key(self):
        self.register()
        first = self.request()
        again = self.request()
        self.assertEqual(first, again)
        with self.assertRaises(IdempotencyConflict):
            self.request(role='runner')
        first = self.change(first, 'dispatch', {'child_session': 'child'})
        self.assertEqual(first, self.change(first, 'dispatch', {'child_session': 'child'}))

    def test_result_requires_dispatch_and_review_requires_settle(self):
        self.register()
        task = self.request()
        with self.assertRaises(DelegationError):
            self.change(task, 'settle', {'result_hash': self.h, 'outcome': 'finished'})
        task = self.change(task, 'dispatch', {'child_session': 'child'})
        with self.assertRaises(DelegationError):
            self.change(task, 'review', {'accept': True, 'reason': 'premature'})
        task = self.change(task, 'settle', {'result_hash': self.h, 'outcome': 'failed'})
        task = self.change(task, 'review', {'accept': False, 'reason': 'missing artifacts'})
        self.assertEqual(task['status'], 'rejected')
        self.assertFalse(self.ledger.status('exp-test')['accepted'])

    def test_next_task_requires_explicit_previous_acceptance(self):
        self.register()
        first = self.request()
        with self.assertRaises(DelegationError):
            self.request(key='dependent-before', after_task_id=first['task_id'])
        first = self.change(first, 'dispatch', {'child_session': 'child'})
        first = self.change(first, 'settle', {'result_hash': self.h, 'outcome': 'finished'})
        with self.assertRaises(DelegationError):
            self.request(key='dependent-settled', after_task_id=first['task_id'])
        first = self.change(first, 'review', {'accept': True, 'reason': 'verified'})
        second = self.request(key='dependent-accepted', after_task_id=first['task_id'])
        self.assertEqual(second['status'], 'requested')

    def test_append_only_evidence_and_atomic_emit_failure(self):
        self.register()
        original = self.store.emit

        def fail(*args):
            raise RuntimeError('emit failure')
        self.store.emit = fail
        with self.assertRaises(RuntimeError):
            self.request()
        self.store.emit = original
        self.assertEqual(self.ledger.status('exp-test')['tasks'], [])
        with self.store.transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delegation_evidence').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 1)
        for table in ('delegation_evidence', 'delegation_requests'):
            for operation in ('UPDATE', 'DELETE'):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.store.transaction() as conn:
                        sql = f'DELETE FROM {table}' if operation == 'DELETE' else f'UPDATE {table} SET payload_json=payload_json'
                        conn.execute(sql)

    def test_host_principal_cannot_be_claimed_and_event_replay_denied(self):
        self.register()
        self.host.events['wrong'] = VerifiedHostEvent('request', 'root', 'wrong', self.h, self.host.sessions)
        with self.assertRaises(HostVerificationError):
            self.ledger.request_task('exp-test', 1, 'planner', self.h, self.h, principal='orch', evidence='wrong', request_key='x')
        task = self.request()
        ref = next((k for k, v in self.host.events.items() if v.action == 'request' and v.principal == 'orch'))
        with self.assertRaises(HostVerificationError):
            self.ledger.request_task('exp-test', 1, 'planner', self.h, self.h, principal='orch', evidence=ref, request_key='second-key')
        self.assertEqual(len(self.ledger.status('exp-test')['tasks']), 1)

    def test_nonfinished_outcomes_cannot_be_accepted(self):
        self.register()
        for outcome in ('failed', 'stopped', 'declined', 'exhausted', 'abnormal'):
            with self.subTest(outcome=outcome):
                child = 'child-' + outcome
                self.host.sessions += (HostSession(child, 'orch', 2),)
                task = self.request(key='request-' + outcome)
                task = self.change(task, 'dispatch', {'child_session': child}, key='dispatch-' + outcome)
                task = self.change(task, 'settle', {'result_hash': self.h, 'outcome': outcome}, key='settle-' + outcome)
                with self.assertRaises(DelegationError):
                    self.change(task, 'review', {'accept': True, 'reason': 'bad acceptance'}, key='bad-' + outcome)
                task = self.change(task, 'review', {'accept': False, 'reason': outcome}, key='reject-' + outcome)
                self.assertEqual(task['status'], 'rejected')
        with self.assertRaises(DelegationError):
            self.ledger.settle('anything', self.h, 'made-up', principal='orch', evidence=None, request_key='bad-enum')

    def test_unique_child_binding(self):
        self.register()
        one = self.request()
        self.change(one, 'dispatch', {'child_session': 'child'})
        two = self.request(key='second')
        with self.assertRaises(DelegationError):
            self.change(two, 'dispatch', {'child_session': 'child'}, key='second-dispatch')

    def test_bridge_read_only_missing_policy_and_settlement(self):
        path = Path(__file__).resolve().parents[1] / 'orchestration' / 'delegation.py'
        spec = importlib.util.spec_from_file_location('delegation_bridge_test', path)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        missing = Path(self.temp.name) / 'missing.sqlite'
        self.assertEqual(bridge.check_delegation(missing, 'exp-test', policy_owner='root')['exit'], 9)
        self.assertFalse(missing.exists())
        self.register()
        self.assertEqual(bridge.check_delegation(self.store.db_path, 'exp-test')['exit'], 9)
        self.assertEqual(bridge.check_delegation(self.store.db_path, 'exp-test', policy_owner='other')['exit'], 9)
        before = Path(self.store.db_path).read_bytes()
        self.assertEqual(bridge.check_delegation(self.store.db_path, 'exp-test', policy_owner='root')['exit'], 0)
        self.assertEqual(before, Path(self.store.db_path).read_bytes())
        task = self.change(self.request(), 'dispatch', {'child_session': 'child'})
        task = self.change(task, 'settle', {'result_hash': self.h, 'outcome': 'finished'})
        self.assertEqual(bridge.check_delegation(self.store.db_path, 'exp-test', policy_owner='root', required='accepted')['exit'], 3)
        self.change(task, 'review', {'accept': True, 'reason': 'verified'})
        self.assertEqual(bridge.check_delegation(self.store.db_path, 'exp-test', policy_owner='root', required='accepted')['exit'], 0)
        self.assertEqual(bridge.check_delegation(self.store.db_path, 'exp-040', policy_owner='root')['exit'], 9)
if __name__ == '__main__':
    unittest.main()
