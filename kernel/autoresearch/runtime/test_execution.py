# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_execution.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kernel.autoresearch.runtime.store import Store
from kernel.autoresearch.runtime.execution import FakeAdapter, SharedInput, ResultBundle, JobStatus, digest, canonical
from kernel.autoresearch.adapters.execution_contract import AgentExecution as Execution, AgentRunSpec as RunSpec
from kernel.autoresearch.runtime.policy import Action, ActionBlocked, ActionPolicy, Category, Finding, Scope, Severity

class RuntimeTest(unittest.TestCase):

    def setUp(self):
        root = Path(__file__).resolve().parents[3] / 'temp'
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='hil_execution_', dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'runtime.sqlite')
        self.store.ensure_experiment('exp-test', '离线测试')
        self.store.ensure_experiment('exp-other', '离线对照')
        self.adapter = FakeAdapter()
        self.callback_calls = []

        def check(conn, spec, action):
            self.assertTrue(conn.in_transaction)
            self.callback_calls.append((spec.request_key, action))
            return True
        self.check = check
        self.execution = Execution(self.store, self.adapter, validate_reservation=check, validate_lease=check)
        self.spec = RunSpec('exp-test', 'request-1', 'a' * 64, 'b' * 64, 'c' * 64, '/units/test', '/units/test/code', '/units/test/env', '/units/test/input', '/units/test/output', ('t1', 't2'), 'reservation-1', 'lease-1', True, False, model_id='fake-offline-model', execution_config={'temperature': 0.0, 'max_tokens': 64}, execution_config_fingerprint=digest(canonical({'temperature': 0.0, 'max_tokens': 64}).encode('utf-8')))

    def rows(self, table):
        conn = self.store.connect()
        try:
            return [dict(v) for v in conn.execute('SELECT * FROM ' + table)]
        finally:
            conn.close()

    def completed_remote(self):
        attempt = self.execution.queue(self.spec)
        self.execution.submit(attempt)
        self.adapter.jobs[self.spec.request_key]['state'] = 'completed'
        self.assertEqual('collecting', self.execution.reconcile(attempt)['state'])
        return attempt

    def bundle(self, ids=('t1', 't2')):
        content = '\n'.join((json.dumps({'instance_id': t, 'resolved': False}) for t in ids)).encode()
        sweep = '\n'.join((json.dumps({'instance_id': t}) for t in ids)).encode()
        artifacts = {'task_results.jsonl': content, 'sweep_results.jsonl': sweep, 'SUMMARY.json': b'{}'}
        for tid in ids:
            artifacts[f'traces/trace_{tid}.json'] = b'{}'
            artifacts[f'patches/{tid}.patch'] = b''
        return ResultBundle(artifacts, {p: digest(b) for p, b in artifacts.items()})

    def test_success_persists_intent_and_completion(self):
        attempt = self.completed_remote()
        self.adapter.bundle = self.bundle()
        self.assertEqual('completed', self.execution.collect(attempt)['state'])
        operations = [(r['operation'], r['phase']) for r in self.rows('exec_observations')]
        self.assertLess(operations.index(('submit', 'intent')), operations.index(('submit', 'response')))
        self.assertEqual(7, len(self.rows('exec_artifacts')))

    def test_response_lost_reconcile_does_not_restart(self):
        self.adapter.lose_submit_response = True
        attempt = self.execution.queue(self.spec)
        self.assertEqual('unknown', self.execution.submit(attempt)['state'])
        recreated = Execution(self.store, self.adapter, validate_reservation=self.check, validate_lease=self.check)
        self.assertEqual('unknown', recreated.submit(attempt)['state'])
        self.assertEqual('running', recreated.reconcile(attempt)['state'])
        self.assertEqual(1, self.adapter.submit_calls)
        self.assertEqual(1, len(self.adapter.jobs))

    def test_query_timeout_stays_unknown_and_retry_rejected(self):
        attempt = self.execution.queue(self.spec)
        self.execution.submit(attempt)
        self.adapter.timeout_query = True
        self.assertEqual('unknown', self.execution.reconcile(attempt)['state'])
        with self.assertRaises(ValueError):
            self.execution.retry(attempt, replace(self.spec, request_key='request-2'))
        self.assertEqual(1, self.adapter.submit_calls)

    def test_empty_results_not_completed(self):
        attempt = self.completed_remote()
        self.adapter.bundle = ResultBundle({}, {})
        self.assertEqual('partial', self.execution.collect(attempt)['state'])
        evidence = json.loads(self.rows('exec_observations')[-1]['payload_json'])
        self.assertIn('no_results', evidence['errors'])

    def test_partial_then_complete_preserves_both_evidence(self):
        attempt = self.completed_remote()
        self.adapter.bundle = self.bundle(('t1',))
        self.assertEqual('partial', self.execution.collect(attempt)['state'])
        self.adapter.bundle = self.bundle()
        self.assertEqual('completed', self.execution.collect(attempt)['state'])
        self.assertEqual(12, len(self.rows('exec_artifacts')))

    def test_manifest_corruption_not_completed(self):
        attempt = self.completed_remote()
        bundle = self.bundle()
        self.adapter.bundle = ResultBundle(bundle.artifacts, {p: '0' * 64 for p in bundle.manifest})
        self.assertEqual('partial', self.execution.collect(attempt)['state'])

    def test_wrong_task_ids_and_duplicates_rejected(self):
        for ids in (('t1', 'wrong'), ('t1', 't1', 't2')):
            with self.subTest(ids=ids):
                errors = Execution.validate_results(self.spec, self.bundle(ids))
                self.assertTrue(errors)

    def test_missing_boolean_resolved_rejected(self):
        bundle = self.bundle()
        artifacts = dict(bundle.artifacts)
        artifacts['task_results.jsonl'] = b'{"instance_id":"t1"}\n'
        errors = Execution.validate_results(self.spec, ResultBundle(artifacts, {p: digest(b) for p, b in artifacts.items()}))
        self.assertIn('invalid_results_rows', errors)

    def test_duplicate_start_and_payload_conflict(self):
        first = self.execution.queue(self.spec)
        self.assertEqual(first, self.execution.queue(self.spec))
        self.execution.submit(first)
        self.execution.submit(first)
        self.assertEqual(1, self.adapter.submit_calls)
        with self.assertRaises(ValueError):
            self.execution.queue(replace(self.spec, code_fingerprint='d' * 64))

    def test_concurrent_duplicate_submit_calls_remote_once(self):
        attempt = self.execution.queue(self.spec)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.execution.submit, (attempt, attempt)))
        self.assertEqual(1, self.adapter.submit_calls)
        self.assertEqual(2, len(self.callback_calls))

    def test_retry_new_attempt_distinct_output_preserves_old_evidence(self):
        old = self.completed_remote()
        self.adapter.bundle = self.bundle(('t1',))
        self.execution.collect(old)
        new = self.execution.retry(old, replace(self.spec, request_key='request-2'))
        self.assertNotEqual(old, new)
        self.assertNotEqual(self.execution.get(old)['output_dir'], self.execution.get(new)['output_dir'])
        self.assertEqual('partial', self.execution.get(old)['state'])
        self.assertEqual(5, len(self.rows('exec_artifacts')))

    def test_isolation_rejects_hardlinks_shared_environment_and_overlap(self):
        for spec in (replace(self.spec, shared_mutable_hardlinks=True), replace(self.spec, isolated_environment=False), replace(self.spec, environment_root='/shared/env'), replace(self.spec, input_root=self.spec.output_root), replace(self.spec, code_root='/units/test/../shared')):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    self.execution.queue(spec)
        self.assertEqual([], self.rows('exec_attempts'))

    def test_shared_input_only_readonly_immutable(self):
        allowed = SharedInput('/models/model', 'd' * 64, True, True)
        self.execution.queue(replace(self.spec, shared_inputs=(allowed,)))
        with self.assertRaises(ValueError):
            replace(self.spec, shared_inputs=(replace(allowed, immutable=False),)).validate()

    def test_missing_callbacks_cannot_launch(self):
        execution = Execution(self.store, self.adapter)
        attempt = execution.queue(self.spec)
        with self.assertRaises(PermissionError):
            execution.submit(attempt)
        self.assertEqual('queued', execution.get(attempt)['state'])
        self.assertEqual(0, self.adapter.submit_calls)

    def test_callback_mutations_rollback_on_lease_failure(self):
        with self.store.transaction() as conn:
            conn.execute('CREATE TABLE test_reservation(used INTEGER)')
            conn.execute('INSERT INTO test_reservation VALUES(0)')

        def reserve(conn, spec, action):
            conn.execute('UPDATE test_reservation SET used=1')
            return True
        execution = Execution(self.store, self.adapter, validate_reservation=reserve, validate_lease=lambda conn, spec, action: False)
        attempt = execution.queue(self.spec)
        with self.assertRaises(PermissionError):
            execution.submit(attempt)
        self.assertEqual(0, self.rows('test_reservation')[0]['used'])

    def finding(self, category, scope=None, evidence='e' * 64):
        return Finding(scope or Scope('experiment', 'exp-test'), evidence, category, Severity.ERROR, True, '测试发现')

    def test_delivery_and_communication_do_not_block_other_experiment(self):
        policy = self.execution.policy
        policy.record(self.finding(Category.DELIVERY))
        policy.record(self.finding(Category.COMMUNICATION, Scope('global', '*')))
        policy.record(self.finding(Category.DELIVERY, Scope('global', '*')))
        self.assertFalse(policy.evaluate('publish', Scope('experiment', 'exp-test')).allowed)
        self.assertTrue(policy.evaluate('publish', Scope('experiment', 'exp-other')).allowed)
        self.assertTrue(policy.evaluate('submit', Scope('experiment', 'exp-test')).allowed)
        self.assertTrue(policy.evaluate('submit', Scope('experiment', 'exp-other')).allowed)
        self.assertFalse(policy.evaluate('request_approval', Scope('experiment', 'exp-other')).allowed)
        self.execution.submit(self.execution.queue(self.spec))
        self.assertEqual(1, self.adapter.submit_calls)

    def test_integrity_blocks_specific_actions_not_query_collect_cancel(self):
        policy = self.execution.policy
        policy.record(self.finding(Category.INTEGRITY))
        for action in (Action.SUBMIT, Action.RETRY, Action.PUBLISH):
            self.assertFalse(policy.evaluate(action, Scope('experiment', 'exp-test')).allowed)
        for action in (Action.QUERY, Action.COLLECT, Action.CANCEL, Action.DELIVER):
            self.assertTrue(policy.evaluate(action, Scope('experiment', 'exp-test')).allowed)
        with self.assertRaises(ActionBlocked):
            self.execution.submit(self.execution.queue(self.spec))
        self.assertEqual(0, self.adapter.submit_calls)

    def test_acceptance_binds_version_without_erasing_history(self):
        policy = ActionPolicy(self.store, validate_decision=lambda conn, row, proof: conn.in_transaction and proof == {'decision': 'trusted-test'})
        first = self.finding(Category.INTEGRITY)
        policy.record(first)
        policy.accept(first.finding_id, first.evidence_hash, 'user', '保留该版本发现', {'decision': 'trusted-test'})
        self.assertTrue(policy.evaluate('publish', first.scope).allowed)
        policy.record(self.finding(Category.INTEGRITY, evidence='f' * 64))
        self.assertFalse(policy.evaluate('publish', first.scope).allowed)
        self.assertEqual(2, len(self.rows('policy_findings')))
        with self.assertRaises(ValueError):
            policy.accept(first.finding_id, 'f' * 64, 'user', '错版本')
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as conn:
                conn.execute('DELETE FROM policy_findings')

    def test_artifacts_and_observations_append_only(self):
        attempt = self.completed_remote()
        self.adapter.bundle = self.bundle()
        self.execution.collect(attempt)
        for table in ('exec_artifacts', 'exec_observations'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as conn:
                    conn.execute('DELETE FROM ' + table)

    def test_queued_cancel_no_remote_call(self):
        attempt = self.execution.queue(self.spec)
        self.assertEqual('cancelled', self.execution.cancel(attempt)['state'])
        self.execution.submit(attempt)
        self.assertEqual(0, self.adapter.submit_calls)

    def test_missing_patch_or_trace_not_completed(self):
        for path in ('patches/t1.patch', 'traces/trace_t2.json'):
            bundle = self.bundle()
            artifacts = dict(bundle.artifacts)
            artifacts.pop(path)
            errors = Execution.validate_results(self.spec, ResultBundle(artifacts, bundle.manifest))
            self.assertIn('missing:' + path, errors)

    def test_active_attempt_cannot_share_mutable_unit(self):
        self.execution.queue(self.spec)
        with self.assertRaises(ValueError):
            self.execution.queue(replace(self.spec, request_key='request-2', exp_id='exp-other'))

    def test_external_submit_observes_committed_intent(self):
        original = self.adapter.submit

        def submit(spec, attempt, output):
            self.assertEqual('submitting', self.execution.get(attempt)['state'])
            self.assertTrue(any((row['operation'] == 'submit' and row['phase'] == 'intent' for row in self.rows('exec_observations'))))
            return original(spec, attempt, output)
        self.adapter.submit = submit
        self.assertEqual('running', self.execution.submit(self.execution.queue(self.spec))['state'])

    def test_task_ids_cannot_escape_artifact_directory(self):
        for task_id in ('../escape', '/absolute', '.', '..'):
            with self.assertRaises(ValueError):
                replace(self.spec, task_ids=(task_id,)).validate()

    def test_collect_unknown_cannot_claim_completion(self):
        self.adapter.lose_submit_response = True
        attempt = self.execution.queue(self.spec)
        self.execution.submit(attempt)
        with self.assertRaises(ValueError):
            self.execution.collect(attempt)

    def test_accept_requires_trusted_decision_not_actor_claim(self):
        policy = self.execution.policy
        finding = self.finding(Category.INTEGRITY)
        policy.record(finding)
        with self.assertRaises(PermissionError):
            policy.accept(finding.finding_id, finding.evidence_hash, 'user', '自称已经接受', {'claimed': True})
        self.assertFalse(policy.evaluate('publish', finding.scope).allowed)

    def test_isolation_resolution_requires_trusted_reverification(self):
        finding = self.finding(Category.ISOLATION)
        policy = self.execution.policy
        policy.record(finding)
        with self.assertRaises(PermissionError):
            policy.resolve(finding.finding_id, finding.evidence_hash, 'f' * 64, 'runner', '自称修好了', {'ok': True})
        verified = ActionPolicy(self.store, validate_reverification=lambda conn, row, new_hash, proof: conn.in_transaction and proof == {'verified_hash': new_hash})
        verified.resolve(finding.finding_id, finding.evidence_hash, 'f' * 64, 'gate', '真实复验通过', {'verified_hash': 'f' * 64})
        result = verified.evaluate('submit', finding.scope)
        self.assertTrue(result.allowed)
        self.assertEqual((finding.finding_id,), result.resolved_findings)
        self.assertEqual(1, len(self.rows('policy_findings')))
        self.assertEqual(1, len(self.rows('policy_resolutions')))
        verified.record(self.finding(Category.ISOLATION, evidence='a' * 64))
        self.assertFalse(verified.evaluate('submit', finding.scope).allowed)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as conn:
                conn.execute('DELETE FROM policy_resolutions')

    def test_untrusted_not_found_does_not_resubmit(self):
        attempt = self.execution.queue(self.spec)
        with self.store.transaction() as conn:
            self.execution._state(conn, attempt, 'submitting')
        self.adapter.query = lambda key, remote: JobStatus(None, 'not_found', {'claim': 'missing'})
        self.assertEqual('unknown', self.execution.reconcile(attempt)['state'])
        self.execution.submit(attempt)
        self.assertEqual(0, self.adapter.submit_calls)

    def test_trusted_not_found_recovers_same_request_without_new_attempt(self):
        attempt = self.execution.queue(self.spec)
        with self.store.transaction() as conn:
            self.execution._state(conn, attempt, 'submitting')
        original_query = self.adapter.query
        self.adapter.query = lambda key, remote: JobStatus(None, 'not_found', {'request_key': key, 'fenced_absent': True})

        def verify_absence(conn, row, status):
            return conn.in_transaction and status.evidence == {'request_key': row['request_key'], 'fenced_absent': True}
        recovered = Execution(self.store, self.adapter, validate_reservation=self.check, validate_lease=self.check, validate_absence=verify_absence)
        self.assertEqual('queued', recovered.reconcile(attempt)['state'])
        self.assertEqual(0, self.adapter.submit_calls)
        self.adapter.query = original_query
        self.assertEqual('running', recovered.submit(attempt)['state'])
        self.assertEqual(1, self.adapter.submit_calls)
        self.assertEqual(1, len(self.rows('exec_attempts')))
        self.assertEqual(self.spec.request_key, recovered.get(attempt)['request_key'])

    def test_cancel_needs_lease_but_query_and_collect_do_not(self):
        attempt = self.completed_remote()
        unowned = Execution(self.store, self.adapter)
        with self.assertRaises(PermissionError):
            unowned.cancel(attempt)
        self.assertEqual('collecting', unowned.reconcile(attempt)['state'])
        self.adapter.bundle = self.bundle()
        self.assertEqual('completed', unowned.collect(attempt)['state'])

    def test_queue_external_transaction_commits_only_with_owner(self):
        with self.store.transaction() as conn:
            attempt = self.execution.queue(self.spec, conn=conn)
            self.assertTrue(conn.in_transaction)
            self.assertEqual('queued', conn.execute('SELECT state FROM exec_attempts WHERE id=?', (attempt,)).fetchone()[0])
            self.assertEqual([], self.rows('exec_attempts'))
        self.assertEqual('queued', self.execution.get(attempt)['state'])

    def test_queue_external_rollback_leaves_no_attempt_or_event(self):
        events_before = len(self.rows('events'))
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as conn:
                self.execution.queue(self.spec, conn=conn)
                raise RuntimeError('调用方撤销状态变更')
        self.assertEqual([], self.rows('exec_attempts'))
        self.assertEqual([], self.rows('exec_jobs'))
        self.assertEqual([], self.rows('exec_observations'))
        self.assertEqual(events_before, len(self.rows('events')))

    def test_queue_external_connection_requires_transaction(self):
        conn = self.store.connect()
        try:
            with self.assertRaises(ValueError):
                self.execution.queue(self.spec, conn=conn)
            self.assertEqual(1, conn.execute('SELECT 1').fetchone()[0])
        finally:
            conn.close()

    def test_queue_callback_checks_state_inside_same_transaction(self):

        def validate_queue(conn, spec):
            self.assertTrue(conn.in_transaction)
            return conn.execute('SELECT state FROM experiments WHERE id=?', (spec.exp_id,)).fetchone()[0] == 'ready'
        execution = Execution(self.store, self.adapter, validate_queue=validate_queue)
        with self.assertRaises(PermissionError):
            execution.queue(self.spec)
        with self.store.transaction() as conn:
            conn.execute("UPDATE experiments SET state='ready' WHERE id=?", (self.spec.exp_id,))
            attempt = execution.queue(self.spec, conn=conn)
            self.assertTrue(conn.in_transaction)
        self.assertEqual('queued', execution.get(attempt)['state'])
        self.assertEqual(0, self.adapter.submit_calls)

    def test_legacy_unspecified_model_can_queue_but_cannot_submit(self):
        legacy = replace(self.spec, model_id=None, execution_config=None, execution_config_fingerprint=None)
        attempt = self.execution.queue(legacy)
        with self.assertRaises(ValueError):
            self.execution.submit(attempt)
        self.assertEqual('queued', self.execution.get(attempt)['state'])
        self.assertEqual(0, self.adapter.submit_calls)
        self.assertEqual([], self.callback_calls)

    def test_execution_config_hash_and_version_are_bound(self):
        for bad in (replace(self.spec, spec_version=2), replace(self.spec, execution_config={'temperature': 1.0}), replace(self.spec, execution_config_fingerprint='0' * 64), replace(self.spec, execution_config={1: 'non-string-key'}), replace(self.spec, execution_config={'bad': float('nan')})):
            with self.subTest(spec=bad):
                with self.assertRaises(ValueError):
                    self.execution.queue(bad)
        self.execution.queue(self.spec)
        changed = {'temperature': 1.0, 'max_tokens': 64}
        with self.assertRaises(ValueError):
            self.execution.queue(replace(self.spec, execution_config=changed, execution_config_fingerprint=digest(canonical(changed).encode('utf-8'))))

    def test_cancel_running_job_never_restarts(self):
        attempt = self.execution.queue(self.spec)
        self.execution.submit(attempt)
        self.assertEqual('cancelled', self.execution.cancel(attempt)['state'])
        self.execution.submit(attempt)
        self.assertEqual(1, self.adapter.submit_calls)
if __name__ == '__main__':
    unittest.main(verbosity=2)
