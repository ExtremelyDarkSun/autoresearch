# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_service.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from dataclasses import replace
sys.dont_write_bytecode = True
from kernel.autoresearch.runtime.store import Store, canonical_json
from kernel.autoresearch.runtime.accounts import ApprovalEvidence, SourcePolicy, HMACSource, ApprovalError, BudgetError
from kernel.autoresearch.runtime.execution import FakeAdapter, ResultBundle, digest
from kernel.autoresearch.adapters.execution_contract import AgentRunSpec as RunSpec
from kernel.autoresearch.runtime.service import RuntimeErrorBase
from kernel.autoresearch.adapters.execution_contract import AgentRuntime as Runtime
from kernel.autoresearch.runtime.backend import BackendRegistry
from kernel.autoresearch.runtime.policy import Finding, Scope, Category, Severity, ActionBlocked
KEY = b'test-only-key-not-for-production!!'

class ServiceTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hil-service-', dir=Path(__file__).resolve().parents[3] / 'temp')
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.adapter = FakeAdapter()
        self.now = time.time()
        evidence = ApprovalEvidence({'receiver': SourcePolicy(HMACSource(KEY), frozenset({'human'}))})
        self.runtime = Runtime(self.store, self.adapter, evidence, clock=lambda: self.now, audit_verifier=self.audit_verified, idle_verifier=self.idle_verified)
        BackendRegistry(self.store).register_new('exp', Path(self.tmp.name) / 'exp', actor='test')
        self.runtime.scheduler.activate('exp')
        self.runtime.request_approval('exp', 0)
        event = dict(event_id='auth', principal='human', exp_id='exp', series_id='series', scope='run', expires_at=self.now + 3600, total_units=1000, action='grant')
        event['signature'] = hmac.new(KEY, canonical_json(event).encode(), hashlib.sha256).hexdigest()
        self.runtime.accounts.authorize('receiver', event, 'auth')
        self.owner = self.runtime.scheduler.takeover('exp', 'owner', lease_seconds=300)

    def audit_verified(self, conn, exp_id, attempts, evidence):
        return evidence == {'audit_id': 'offline-independent-audit'} and bool(attempts) and all((a['state'] == 'completed' and a['remote_state'] == 'completed' for a in attempts))

    def idle_verified(self, resource_key, evidence, snapshot):
        request_key = resource_key.split(':', 1)[1]
        job = self.adapter.jobs.get(request_key)
        return job is not None and job['state'] in ('completed', 'failed', 'cancelled')

    def attempt(self, name='one', reservation=None, resource=None, previous=None):
        reservation = reservation or 'reserve-' + name
        if self.runtime.accounts.get_reservation(reservation) is None:
            self.runtime.accounts.reserve('series', 'exp', 'run', 100, reservation)
        if self.store.get_experiment('exp')['state'] == 'awaiting_approval':
            self.runtime.prepare('exp', self.store.get_experiment('exp')['version'])
        task = self.runtime.scheduler.enqueue('exp', 'task-' + name, {'request_key': name}, 'owner', self.owner['generation'], paid=True, resources=[resource or 'machine:' + name])
        claim = self.runtime.scheduler.claim('exp', 'owner', self.owner['generation'], 'worker-' + name)
        spec = RunSpec('exp', name, 'a' * 64, 'b' * 64, 'c' * 64, '/unit/' + name, '/unit/' + name + '/code', '/unit/' + name + '/env', '/unit/' + name + '/input', '/unit/' + name + '/output', ('t1',), reservation, claim['token'], True, False, model_id='offline-fake-model', execution_config={'temperature': 0.0, 'max_tokens': 64}, execution_config_fingerprint=digest(canonical_json({'temperature': 0.0, 'max_tokens': 64}).encode()))
        attempt = self.runtime.execution.retry(previous, spec) if previous else self.runtime.queue(spec)
        return (attempt, spec, claim)

    def complete(self, attempt, spec):
        self.adapter.jobs[spec.request_key]['state'] = 'completed'
        self.runtime.reconcile(attempt)
        artifacts = {'task_results.jsonl': b'{"instance_id":"t1","resolved":true}\n', 'sweep_results.jsonl': b'{"instance_id":"t1"}\n', 'SUMMARY.json': b'{}', 'traces/trace_t1.json': b'{}', 'patches/t1.patch': b''}
        self.adapter.bundle = ResultBundle(artifacts, {k: digest(v) for k, v in artifacts.items()})
        return self.runtime.collect(attempt)

    def ack_and_settle(self, claim, spec):
        self.runtime.scheduler.ack(claim['id'], 'owner', self.owner['generation'], claim['worker_id'], claim['token'], result={'completed': True})
        self.runtime.accounts.settle(spec.reservation_id, 50, 'settle-' + spec.request_key)
        for resource_key in claim['resources']:
            self.runtime.scheduler.confirm_resource_idle(resource_key, {'resource_key': resource_key, 'task_id': claim['id'], 'token': claim['token']}, actor='test')

    def test_real_callbacks_and_state_atomic(self):
        attempt, spec, claim = self.attempt()
        self.assertEqual(self.store.get_experiment('exp')['state'], 'ready')
        self.runtime.submit(attempt)
        self.assertEqual(self.store.get_experiment('exp')['state'], 'running')
        conn = self.store.connect()
        try:
            bound = conn.execute('SELECT * FROM reservation_bindings').fetchall()
            self.assertEqual(len(bound), 1)
            self.assertEqual(bound[0]['request_key'], spec.request_key)
        finally:
            conn.close()
        self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 1)

    def test_same_reservation_second_attempt_rejected(self):
        a, spec, claim = self.attempt()
        self.runtime.submit(a)
        b, other, other_claim = self.attempt('two', reservation=spec.reservation_id)
        with self.assertRaises(BudgetError):
            self.runtime.submit(b)
        self.assertEqual(self.adapter.submit_calls, 1)
        self.assertEqual(self.runtime.execution.get(b)['state'], 'queued')

    def test_stale_task_token_rejected_and_binding_rolled_back(self):
        attempt, spec, claim = self.attempt()
        self.now += 61
        with self.assertRaises(PermissionError):
            self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 0)
        self.assertEqual(self.store.get_experiment('exp')['state'], 'ready')
        conn = self.store.connect()
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM reservation_bindings').fetchone()[0], 0)
        finally:
            conn.close()

    def test_takeover_invalidates_token(self):
        attempt, spec, claim = self.attempt()
        self.runtime.scheduler.takeover('exp', 'new-owner', force=True)
        with self.assertRaises(PermissionError):
            self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 0)

    def test_resource_lease_mismatch_rejected(self):
        attempt, spec, claim = self.attempt()
        with self.store.transaction() as conn:
            conn.execute('UPDATE resources SET token=? WHERE task_id=?', ('stale', claim['id']))
        with self.assertRaises(PermissionError):
            self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 0)

    def test_expired_approval_rejected(self):
        attempt, spec, claim = self.attempt()
        with self.store.transaction() as conn:
            conn.execute('UPDATE budget_accounts SET expires_at=?', (self.now - 1,))
        with self.assertRaises(ApprovalError):
            self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 0)

    def test_multiple_attempts_aggregate_only_on_explicit_finalize(self):
        a, first, c1 = self.attempt()
        b, second, c2 = self.attempt('two')
        self.runtime.submit(a)
        self.runtime.submit(b)
        self.complete(a, first)
        self.assertEqual(self.store.get_experiment('exp')['state'], 'running')
        self.ack_and_settle(c1, first)
        with self.assertRaises(RuntimeErrorBase):
            self.runtime.finalize('exp', self.store.get_experiment('exp')['version'])
        self.complete(b, second)
        self.assertEqual(self.store.get_experiment('exp')['state'], 'collecting')
        with self.assertRaises(BudgetError):
            self.runtime.finalize('exp', self.store.get_experiment('exp')['version'])
        self.ack_and_settle(c2, second)
        result = self.runtime.finalize('exp', self.store.get_experiment('exp')['version'], audit_evidence={'audit_id': 'offline-independent-audit'})
        self.assertEqual(result['state'], 'completed')
        with self.assertRaises(PermissionError):
            self.runtime.queue(replace(second, request_key='after-terminal'))

    def test_submit_timeout_reconciles_original_without_false_completion(self):
        a, spec, claim = self.attempt()
        self.adapter.lose_submit_response = True
        self.runtime.submit(a)
        self.assertEqual(self.runtime.execution.get(a)['state'], 'unknown')
        with self.assertRaises(BudgetError):
            self.runtime.accounts.release(spec.reservation_id, 'unsafe-release')
        with self.assertRaises(BudgetError):
            self.runtime.accounts.settle(spec.reservation_id, 0, 'unsafe-settle')
        self.assertEqual(self.store.get_experiment('exp')['state'], 'running')
        with self.assertRaises(RuntimeErrorBase):
            self.runtime.finalize('exp', self.store.get_experiment('exp')['version'])
        self.runtime.reconcile(a)
        self.runtime.submit(a)
        self.assertEqual(self.adapter.submit_calls, 1)
        self.assertEqual(self.runtime.execution.get(a)['state'], 'running')

    def test_partial_evidence_does_not_complete_experiment(self):
        a, spec, claim = self.attempt()
        self.runtime.submit(a)
        self.adapter.jobs[spec.request_key]['state'] = 'completed'
        self.runtime.reconcile(a)
        self.runtime.collect(a)
        self.assertEqual(self.runtime.execution.get(a)['state'], 'partial')
        with self.assertRaises(RuntimeErrorBase):
            self.runtime.finalize('exp', self.store.get_experiment('exp')['version'])
        self.assertNotEqual(self.store.get_experiment('exp')['state'], 'completed')

    def test_no_default_adapter_external_actions(self):
        local = Runtime(self.store)
        for method in (local.submit, local.reconcile, local.collect, local.cancel):
            with self.assertRaises(PermissionError):
                method('missing')

    def test_unrelated_scope_cannot_prepare(self):
        with self.store.transaction() as conn:
            conn.execute("UPDATE budget_accounts SET scope='gpu-eval'")
        with self.assertRaises(ApprovalError):
            self.runtime.prepare('exp', self.store.get_experiment('exp')['version'])

    def test_default_audit_refuses_completed_execution(self):
        attempt, spec, claim = self.attempt()
        self.runtime.submit(attempt)
        self.complete(attempt, spec)
        self.ack_and_settle(claim, spec)
        self.runtime.audit_verifier = None
        with self.assertRaises(PermissionError):
            self.runtime.finalize('exp', self.store.get_experiment('exp')['version'], audit_evidence={'audit_id': 'offline-independent-audit'})
        self.assertEqual(self.store.get_experiment('exp')['state'], 'collecting')

    def test_publish_finding_blocks_aggregation(self):
        attempt, spec, claim = self.attempt()
        self.runtime.submit(attempt)
        self.complete(attempt, spec)
        self.ack_and_settle(claim, spec)
        self.runtime.execution.policy.record(Finding(Scope('attempt', attempt), 'f' * 64, Category.RESULTS, Severity.ERROR, True, 'independent results integrity failure'))
        with self.assertRaises(ActionBlocked):
            self.runtime.finalize('exp', self.store.get_experiment('exp')['version'], audit_evidence={'audit_id': 'offline-independent-audit'})

    def test_ack_does_not_prove_resource_idle(self):
        attempt, spec, claim = self.attempt()
        self.runtime.submit(attempt)
        self.complete(attempt, spec)
        self.runtime.accounts.settle(spec.reservation_id, 50, 'settle')
        self.runtime.scheduler.ack(claim['id'], 'owner', self.owner['generation'], claim['worker_id'], claim['token'])
        with self.assertRaises(RuntimeErrorBase):
            self.runtime.finalize('exp', self.store.get_experiment('exp')['version'], audit_evidence={'audit_id': 'offline-independent-audit'})

    def test_unregistered_backend_cannot_start(self):
        attempt, spec, claim = self.attempt()
        with self.store.transaction() as conn:
            conn.execute("UPDATE backend_migrations SET backend='pending' WHERE exp_id='exp'")
        with self.assertRaises(PermissionError):
            self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 0)

    def test_real_resource_scope_blocks_submit(self):
        attempt, spec, claim = self.attempt()
        self.runtime.execution.policy.record(Finding(Scope('resource', 'machine:one'), 'd' * 64, Category.RESOURCE, Severity.CRITICAL, True, 'independent resource fault'))
        with self.assertRaises(ActionBlocked):
            self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 0)

    def test_task_payload_mismatch_cannot_borrow_token(self):
        attempt, spec, claim = self.attempt()
        with self.store.transaction() as conn:
            conn.execute('UPDATE tasks SET payload=? WHERE id=?', (canonical_json({'request_key': 'another-request'}), claim['id']))
        with self.assertRaises(PermissionError):
            self.runtime.submit(attempt)
        self.assertEqual(self.adapter.submit_calls, 0)

    def test_paused_and_resource_findings_do_not_block_safe_cancel(self):
        attempt, spec, claim = self.attempt()
        self.runtime.submit(attempt)
        exp = self.store.get_experiment('exp')
        self.store.transition('exp', exp['version'], 'paused', 'human', 'pause')
        self.runtime.execution.policy.record(Finding(Scope('resource', 'machine:one'), 'e' * 64, Category.RESOURCE, Severity.CRITICAL, True, 'resource problem requires cancel'))
        self.runtime.execution.policy.record(Finding(Scope('experiment', 'exp'), 'c' * 64, Category.ISOLATION, Severity.CRITICAL, True, 'isolation problem requires cancel'))
        self.runtime.cancel(attempt)
        self.assertEqual(self.runtime.execution.get(attempt)['state'], 'cancelled')
        self.assertEqual(self.store.get_experiment('exp')['state'], 'paused')
        self.assertEqual(self.adapter.submit_calls, 1)

    def test_stale_owner_cannot_cancel_original_job(self):
        attempt, spec, claim = self.attempt()
        self.runtime.submit(attempt)
        self.runtime.scheduler.takeover('exp', 'another-owner', force=True)
        with self.assertRaises(PermissionError):
            self.runtime.cancel(attempt)
        self.assertEqual(self.adapter.jobs[spec.request_key]['state'], 'running')

    def test_failed_history_does_not_override_successful_retry_leaves(self):
        old, old_spec, old_claim = self.attempt()
        parallel, parallel_spec, parallel_claim = self.attempt('parallel')
        self.runtime.submit(old)
        self.adapter.jobs[old_spec.request_key]['state'] = 'failed'
        self.runtime.reconcile(old)
        self.ack_and_settle(old_claim, old_spec)
        retry, retry_spec, retry_claim = self.attempt('retry', previous=old)
        self.runtime.submit(parallel)
        self.runtime.submit(retry)
        self.complete(parallel, parallel_spec)
        self.complete(retry, retry_spec)
        self.ack_and_settle(parallel_claim, parallel_spec)
        self.ack_and_settle(retry_claim, retry_spec)
        result = self.runtime.finalize('exp', self.store.get_experiment('exp')['version'], audit_evidence={'audit_id': 'offline-independent-audit'})
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.runtime.execution.get(old)['state'], 'failed')
        self.assertEqual(self.runtime.execution.get(retry)['previous_attempt'], old)
        conn = self.store.connect()
        try:
            audit = json.loads(conn.execute("SELECT payload_json FROM events WHERE kind='runtime_audit_verified'").fetchone()[0])
        finally:
            conn.close()
        self.assertIn(old, audit['history_attempt_ids'])
        self.assertNotIn(old, audit['attempt_ids'])
        self.assertIn(retry, audit['attempt_ids'])
if __name__ == '__main__':
    unittest.main(verbosity=2)
