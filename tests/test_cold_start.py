# GENERATED-BY: portable integration acceptance
# LAST-EDIT: 2026-11-09
# PURPOSE: Exercise real isolation, DSH evidence parsing, monitoring, budget, execution and scribe receipt together.
# ORIGIN: synthetic fixtures from test_lifecycle/test_audit_host_binding/test_reporting
# CUMULATIVE-DELTA: No always-true gate; all messages and credentials are synthetic, no network access.
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from kernel.autoresearch import reporting
from kernel.autoresearch.execution_target import ExecutionTarget
from kernel.autoresearch.monitoring import Monitoring
from kernel.autoresearch.workflow import Workflow, WorkflowError
import test_lifecycle as lifecycle_fixtures
from test_lifecycle import page_text
from test_workflow import TOPIC, SESSION, audit_report
from test_reporting import make_host, host_logs


class ColdStartTests(lifecycle_fixtures.ProvisionedDeliveryTests):
    """Reuse only temporary provisioning and signed budget fixtures; replace every execution gate with its actual service."""
    def setUp(self):
        super().setUp()
        self.owner = SESSION.removeprefix('session-')
        self.logs = {}
        self.host_root = self.ws.parent / 'synthetic-host'
        self.base_ms = int(time.time() * 1000) - 1000
        self.logs[self.owner] = [
            {'type': 'session', 'id': 'session-' + self.owner, 'delegationDepth': 0,
             'isSeeded': False, 'createdAt': self.base_ms},
            self.user(1, '只验证一次合成机制，不连接真实比赛与云机。'),
            self.user(2, '这句不立为跨轮意图，只是本轮目标。')]
        self.refresh_host()
        self.monitor = Monitoring(self.ws, host=self.host)
        self.monitor.register(SESSION, state='none', reason='用户回答不立为跨轮意图',
                              sources=[{'session': SESSION, 'seq': 2}])
        self.monitor.sync(SESSION)

    def user(self, seq, text):
        return {'seq': seq, 'type': 'user/message', 'time': self.base_ms + seq,
                'data': {'source': {'kind': 'user', 'rpcId': f'synthetic-user-{seq}'},
                         'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}}

    def refresh_host(self):
        self.host = make_host(self.host_root, self.ws, self.logs)

    def append_dispatch(self, owner_rows, child_rows, child):
        offset = max(row.get('seq', 0) for row in self.logs[self.owner])
        now = int(time.time() * 1000)
        for row in owner_rows[1:]:
            if row.get('type') == 'user/message':
                continue
            row = json.loads(json.dumps(row))
            row['seq'] = offset + row['seq']
            row['time'] = now + row.get('time', 1000) - 1000
            self.logs[self.owner].append(row)
        rows = json.loads(json.dumps(child_rows))
        for row in rows:
            if row.get('type') == 'session':
                row['createdAt'] = now + 3
            elif 'time' in row:
                row['time'] = now + row['time'] - 1000
        self.logs[child] = rows
        self.refresh_host()
        self.monitor.host = self.host

    def budget_flow(self):
        return Workflow(self.ws, store=self.store, verifier=self.host,
                        monitor_check=self.monitor.check_start,
                        authorization_check=self.admission.check,
                        execution_target=ExecutionTarget(self.ws, require_provision=True))

    def review_record(self, **kwargs):
        record = super().review_record(**kwargs)
        record['schema_version'] = 2
        record['continuation'] = {
            'criterion': '同题单复核机制是否稳定，保持结论的适用范围',
            'criterion_evidence': [{'path': 'evidence/readings.md', 'locator': 'L1'}],
            'cost_scope': {'scope': 'small', 'max_cost_units': 3,
                           'series_id': 'series-small',
                           'basis': '只覆盖下一轮合成小样本，未知实际费用继续保留预留'},
            'next_owner': SESSION, 'needs_approval': False,
            'approval_reason': '真实共享预算内的小样本验证，下一次执行仍核预留和授权'}
        return record

    def audit(self, plan_id='P1', *, flow=None):
        flow = flow or self.flow
        prepared = flow.gov.prepare_audit(plan_id, actor=SESSION)
        document = audit_report(prepared['prepared_id'])
        child = 'cold-auditor-' + plan_id
        logs = host_logs(prepared['prepared_id'], 'unused', 'unused',
                         child=child, owner=self.owner)
        # Replace scribe write with a visible complete audit report in a fresh spawned context.
        logs[child] = [logs[child][0], logs[child][1],
            {'seq': 2, 'type': 'assistant/message', 'time': 1004,
             'data': {'message': {'role': 'assistant', 'content': [
                 {'type': 'text', 'text': json.dumps(document, ensure_ascii=False)}]}}}]
        self.append_dispatch(logs[self.owner], logs[child], child)
        flow.verifier = self.host
        flow.gov.verifier = self.host
        result = flow.gov.record_audit(prepared['prepared_id'], document,
            actor='session-' + child, evidence={'session_id': 'session-' + child, 'seq': 2})
        self.assertEqual(result['independence'], 'verified')
        return prepared

    def _complete_first_round(self):
        flow, built = self.budget_round()
        self.assertTrue(built['prepared']['lifecycle_complete'])
        checked = flow.check(topic=TOPIC, plan=built['plan'], owner_session=SESSION,
                             argv=self.argv, intent_none=True)
        self.assertTrue(checked['ok'], checked['problems'])
        run = self.launch(built['plan'], flow=flow)
        self.assertEqual(run['exitcode'], 0)
        self.assertEqual(Path(run['execution']['cwd']), self.marker.parent)
        self.assertTrue(self.marker_log.is_file())
        with self.assertRaises(WorkflowError):
            flow.prepare(topic=TOPIC, plan=self.plan('P2'), owner_session=SESSION)
        self.append_doc('supported')
        review = flow.review(run_id=run['run_id'], record=self.review_record(), require_version=2)
        self.assertEqual(review['continuation']['status'], 'recorded')
        self.assertEqual(review['continuation']['next_owner'], SESSION)
        self.assertTrue(review['lifecycle_after']['ok'], review)
        reporting.record_input(self.ws, TOPIC, 'user_requirement',
            '只验证合成机制，不连接真实比赛与云机',
            {'path': f'topics/{TOPIC}/REQUIREMENTS.md', 'locator': 'L1'})
        pack = reporting.prepare(self.ws, TOPIC)
        self.assertTrue(pack['ok'], pack)
        page_rel = f'topics/{TOPIC}/STATUS.md'
        source = f'topics/{TOPIC}/plans/P1/receipts/finish-{run["run_id"]}.json'
        text = page_text(source)
        (self.ws / page_rel).write_text(text, encoding='utf-8', newline='')
        child = 'cold-scribe'
        logs = host_logs(pack['prepared_id'], page_rel, text, child=child, owner=self.owner)
        self.append_dispatch(logs[self.owner], logs[child], child)
        receipt = reporting.acknowledge(self.ws, TOPIC,
            writer_session='session-' + child, writer_role='scribe',
            owner_session=SESSION, host_verifier=self.host,
            proof={'session_id': 'session-' + child, 'seq': 5})
        self.assertEqual(receipt['verification'], 'verified')
        self.assertTrue(receipt['session_verified'])
        self.assertFalse(receipt['delivery_verified'])
        self.assertTrue(reporting.check_round(self.ws, TOPIC, 'P1')['ok'])

    def test_actual_chain_and_verified_scribe(self):
        self._complete_first_round()

    def test_next_round_uses_same_shared_account_without_settling_unknown_cost(self):
        self._complete_first_round()
        flow = self.budget_flow()
        self.store.ensure_experiment('P2')
        reservation = self.accounts.reserve('series-small', 'P2', 'small', 3,
                                            'reservation-small-P2')
        self.assertEqual(reservation['series_id'], 'series-small')
        with self.doc.open('a', encoding='utf-8') as stream:
            stream.write('\n- H2：在上一轮条件下验证第二组参数，先观察机制。\n')
        argv = self.argv[:-2] + ['B', '0']
        built = self.prepare('P2', flow=flow, argv=argv, hypothesis_id='H2',
            hypothesis_text='第二组入口参数在相同装置下能保持通过结果',
            budget={'max_cost_units': 3, 'max_seconds': 60},
            authorization={'scope': 'small', 'series_id': 'series-small',
                           'reservation_id': 'reservation-small-P2'})
        self.audit('P2', flow=flow)
        run = self.launch(built['plan'], flow=flow, argv=argv)
        self.assertEqual(run['exitcode'], 0)
        self.assertEqual(self.marker_lines(), ['A', 'B'])
        with contextlib.closing(self.store.connect()) as conn:
            account = conn.execute("SELECT * FROM budget_accounts WHERE series_id='series-small'").fetchone()
            self.assertEqual(account['reserved_units'], 6)
            self.assertEqual(account['spent_units'], 0)
            reservations = conn.execute("SELECT status FROM budget_reservations WHERE series_id='series-small'").fetchall()
            self.assertEqual([row['status'] for row in reservations], ['reserved', 'reserved'])
        # The subprocess has ended, but the second research round still requires its own review.
        with self.assertRaises(WorkflowError):
            flow.prepare(topic=TOPIC, plan=self.plan('P3'), owner_session=SESSION)

    def test_wrong_argv_and_changed_source_launch_no_process(self):
        flow, built = self.budget_round()
        with self.assertRaises(WorkflowError):
            flow.start(topic=TOPIC, plan=built['plan'], owner_session=SESSION,
                       argv=self.argv + ['unapproved'], intent_none=True)
        self.assertFalse(self.marker_log.exists())
        self.readings.write_text('依据已经变更，不能使用旧准备记录', encoding='utf-8')
        with self.assertRaises(WorkflowError):
            self.launch(built['plan'], flow=flow)
        self.assertFalse(self.marker_log.exists())

    def test_public_init_starts_from_empty_directory(self):
        fresh = self.ws.parent / 'public-init'
        root = Path(__file__).resolve().parents[1]
        output = self.ws.parent / 'init-output.txt'
        with output.open('wb') as stream:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(root / 'research.py'),
                'init', '--workspace', str(fresh)], cwd=root, stdout=stream, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0, output.read_text(encoding='utf-8'))
        self.assertTrue((fresh / 'AGENTS.md').is_file())
        self.assertTrue((fresh / '.autoresearch/runtime.sqlite3').is_file())


if __name__ == '__main__':
    unittest.main()
