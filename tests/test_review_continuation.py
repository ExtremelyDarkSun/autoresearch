# GENERATED-BY: continuation acceptance, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: 验证复盘版本2有真实来源、费用范围、负责人和审批说明，旧记录不补造。
# ORIGIN: workflow real subprocess fixture; framework expectations E5
# CUMULATIVE-DELTA: 新增纯校验与生产 Workflow/Lifecycle 接线测试；仅合成证据与本机子进程。
import copy
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kernel.autoresearch.review_continuation import validate, project
from kernel.autoresearch.workflow import WorkflowError
from kernel.autoresearch.lifecycle import Lifecycle
from test_workflow import FlowCase, TOPIC


def continuation():
    return {
        'criterion': '同题单逐题比较，确认收益后才扩大规模',
        'criterion_evidence': [{'path': 'evidence/readings.md', 'locator': 'L1'}],
        'cost_scope': {'scope': 'small', 'max_cost_units': 3,
                       'basis': '仅两道机制验证题，含失败重试的费用上限'},
        'next_owner': 'owner-next', 'needs_approval': False,
        'approval_reason': '小样本机制验证由负责人继续，实际执行前仍核共享预算'}


class ContinuationShapeTests(unittest.TestCase):
    def record(self):
        return {'schema_version': 2, 'continuation': continuation()}

    def test_legacy_is_unknown_and_never_upgraded(self):
        old = {'next_action': '继续核查'}
        self.assertEqual(validate(old), [])
        result = project(old)
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(result['legacy'])
        self.assertNotIn('cost_scope', result)
        self.assertEqual(old, {'next_action': '继续核查'})

    def test_new_fields_cannot_hide_in_legacy_version(self):
        record = self.record()
        record.pop('schema_version')
        self.assertTrue(validate(record))

    def test_missing_fields_and_placeholders_are_rejected(self):
        for key in ('criterion', 'criterion_evidence', 'cost_scope', 'next_owner',
                    'needs_approval', 'approval_reason'):
            record = self.record()
            del record['continuation'][key]
            with self.subTest(key=key):
                self.assertTrue(validate(record))
        record = self.record()
        record['continuation']['next_owner'] = '<负责人>'
        self.assertTrue(validate(record))

    def test_versions_and_budget_numbers_are_strict(self):
        for version in (True, '2', 0, 3):
            record = self.record()
            record['schema_version'] = version
            self.assertTrue(validate(record))
        for units in (True, -1, 0, 1.1, '3', None):
            record = self.record()
            record['continuation']['cost_scope']['max_cost_units'] = units
            self.assertTrue(validate(record))

    def test_large_and_submission_must_declare_approval_but_may_wait(self):
        for scope in ('large', 'submission'):
            record = self.record()
            record['continuation']['cost_scope']['scope'] = scope
            self.assertTrue(validate(record))
            record['continuation']['needs_approval'] = True
            self.assertEqual(validate(record), [])
            result = project(record)
            self.assertEqual(result['status'], 'recorded')
            self.assertEqual(result['approval_status'], 'pending')
            self.assertNotIn('authorized', result)

    def test_none_is_explicit_free_work_and_never_paid_zero(self):
        record = self.record()
        record['continuation']['cost_scope'].update(scope='none', max_cost_units=0,
                                                  basis='只读已有证据，不启动付费执行')
        self.assertEqual(validate(record), [])
        record['continuation']['cost_scope']['max_cost_units'] = 1
        self.assertTrue(validate(record))

    def test_caller_cannot_claim_self_approved(self):
        for key in ('approved', 'authorized', 'approval_status'):
            record = self.record()
            record['continuation'][key] = True
            self.assertTrue(validate(record))

    def test_project_is_a_copy(self):
        record = self.record()
        result = project(record)
        result['cost_scope']['max_cost_units'] = 999
        self.assertEqual(record['continuation']['cost_scope']['max_cost_units'], 3)


class ContinuationIntegrationTests(FlowCase):
    def new_record(self):
        return self.review_record(schema_version=2, continuation=continuation())

    def review_count(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute('SELECT COUNT(*) FROM wf_reviews').fetchone()[0]

    def test_v2_persists_exact_record_and_projects_to_ai_material(self):
        built, run = self.finished_run()
        self.append_doc('supported')
        record = self.new_record()
        result = self.flow.review(run_id=run['run_id'], record=record)
        self.assertTrue(result['ok'])
        self.assertEqual(result['continuation']['status'], 'recorded')
        self.assertEqual(result['continuation']['next_owner'], 'owner-next')
        status = self.flow.status(topic=TOPIC)
        self.assertEqual(status['runs'][0]['review']['continuation']['cost_scope']['max_cost_units'], 3)
        with closing(sqlite3.connect(self.db)) as conn:
            payload, evidence = conn.execute('SELECT record_json,evidence_json FROM wf_reviews').fetchone()
        self.assertEqual(json.loads(payload), record)
        self.assertTrue(any(ref.get('purpose') == 'next_action_criterion' and 'sha256' in ref
                            for ref in json.loads(evidence)))
        work = Lifecycle(self.ws).ai_work(TOPIC, 'P1').read_text(encoding='utf-8')
        self.assertIn('owner-next', work)
        self.assertIn('上限 3 单位', work)
        self.assertIn(record['continuation']['criterion'], work)
        self.assertTrue(result['lifecycle_sync']['ok'])

    def test_missing_or_forged_criterion_source_never_enters_ledger(self):
        built, run = self.finished_run()
        self.append_doc('supported')
        record = self.new_record()
        record['continuation']['criterion_evidence'][0]['locator'] = '不存在的原话'
        with self.assertRaises(WorkflowError):
            self.flow.review(run_id=run['run_id'], record=record)
        self.assertEqual(self.review_count(), 0)
        record['continuation']['criterion_evidence'][0] = {
            'path': 'evidence/readings.md', 'locator': 'L1', 'sha256': '0' * 64}
        with self.assertRaisesRegex(WorkflowError, '声明哈希'):
            self.flow.review(run_id=run['run_id'], record=record)
        self.assertEqual(self.review_count(), 0)
        record['continuation']['criterion_evidence'][0] = {'path': 'evidence/none.md', 'locator': 'L1'}
        with self.assertRaises(WorkflowError):
            self.flow.review(run_id=run['run_id'], record=record)
        self.assertEqual(self.review_count(), 0)

    def test_new_missing_fields_are_rejected_without_review_side_effects(self):
        built, run = self.finished_run()
        self.append_doc('supported')
        record = self.new_record()
        for key in ('next_owner', 'cost_scope', 'needs_approval'):
            bad = copy.deepcopy(record)
            bad['continuation'].pop(key)
            with self.subTest(key=key):
                with self.assertRaises(WorkflowError):
                    self.flow.review(run_id=run['run_id'], record=bad)
                self.assertEqual(self.review_count(), 0)

    def test_pending_large_approval_is_valid_handoff_not_authorization(self):
        built, run = self.finished_run()
        self.append_doc('supported')
        record = self.new_record()
        record['continuation']['cost_scope'].update(scope='large', max_cost_units=30)
        record['continuation'].update(needs_approval=True, approval_reason='先提交审批页，再等待用户明确批准')
        result = self.flow.review(run_id=run['run_id'], record=record)
        self.assertEqual(result['continuation']['approval_status'], 'pending')
        self.assertNotIn('authorized', result['continuation'])
        self.assertEqual(self.review_count(), 1)

    def test_production_version_requirement_rejects_legacy_before_writing(self):
        from kernel.autoresearch.workflow_cli import _parser
        args = _parser().parse_args(['review', '--workspace', str(self.ws), '--run-id', 'R', '--file', 'review.json'])
        self.assertFalse(args.legacy_record)
        args = _parser().parse_args(['review', '--workspace', str(self.ws), '--run-id', 'R', '--file', 'review.json', '--legacy-record'])
        self.assertTrue(args.legacy_record)
        built, run = self.finished_run()
        self.append_doc('supported')
        with self.assertRaisesRegex(WorkflowError, 'schema_version=2'):
            self.flow.review(run_id=run['run_id'], record=self.review_record(), require_version=2)
        self.assertEqual(self.review_count(), 0)
        result = self.flow.review(run_id=run['run_id'], record=self.review_record())
        self.assertEqual(result['continuation']['status'], 'unknown')

    def test_legacy_review_stays_accepted_but_handoff_is_unknown(self):
        built, run = self.finished_run()
        self.append_doc('supported')
        result = self.flow.review(run_id=run['run_id'], record=self.review_record())
        self.assertTrue(result['ok'])
        self.assertEqual(result['continuation']['status'], 'unknown')
        self.assertTrue(result['continuation']['legacy'])
        self.assertEqual(self.flow.status(topic=TOPIC)['runs'][0]['review']['continuation']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
