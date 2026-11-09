# ORIGIN: generic source framework mechanisms
# LAST-EDIT: latest framework synchronization, 2026-11-09
# CUMULATIVE-DELTA: 2026-11-09 补齐探索性停止条件及来源、方向证据边界、工具变更与上下文纪律。
# GENERATED-BY: framework synchronization, 2026-11-09
# PURPOSE: 升级的行为回归，全部使用合成材料。
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from test_governance import GovCase, make_plan, audit_report
from kernel.autoresearch import governance as g
from kernel.autoresearch.audit_support import load_bound_report


def issue(blocking=False, **extra):
    return dict(id='I1', category='design', blocking=blocking, why_blocking='解释被重大缺陷阻断' if blocking else '', problem='解释边界不足',
                consequence='无法支持外推', suggestion='补明条件',
                sources=[{'path':'docs/plan.md','locator':'L1'}], **extra)


def modern(prepared_id, decision='proceed', issues=None, **kw):
    return audit_report(prepared_id, decision=decision, decision_reason='目标与设计可核',
                        focus={'question_goal':'连接目标','design_interpretation':'设计可检验','history_constraints':'已检索历史'},
                        issues=issues or [], **kw)


class UpgradeTests(GovCase):
    def test_exploration_without_number(self):
        plan=make_plan(criteria={'mode':'exploratory','metric':'机制观察',
            'observation':'配置是否生效','interpretation_boundary':'不外推幅度','conditions':'同装置小样本',
            'stop_condition':'取得预定机制观察或预算耗尽','source':'docs/design.md'})
        self.assertEqual(g.validate_plan(plan), [])
        del plan['criteria']['conditions']
        self.assertTrue(g.validate_plan(plan))

    def test_exploration_requires_stop_and_typed_source(self):
        criteria = {'mode':'exploratory','metric':'机制观察','observation':'观察调用',
                    'interpretation_boundary':'不外推','conditions':'固定装置',
                    'stop_condition':'获得观察或预算耗尽','source':'docs/design.md'}
        for key in ('stop_condition',):
            for value in (None, '', '   ', 3):
                with self.subTest(key=key, value=value):
                    candidate = dict(criteria)
                    if value is None:
                        candidate.pop(key)
                    else:
                        candidate[key] = value
                    self.assertTrue(g.validate_plan(make_plan(criteria=candidate)))
        # 源 J1：source 可选（缺失/空串都放行），显式写了必须是字符串
        for value in (None, '', '   '):
            candidate = dict(criteria)
            if value is None:
                candidate.pop('source')
            else:
                candidate['source'] = value
            self.assertEqual(g.validate_plan(make_plan(criteria=candidate)), [])
        for value in (3, [], {}):
            candidate = dict(criteria, source=value)
            self.assertTrue(g.validate_plan(make_plan(criteria=candidate)))

    def test_stop_change_invalidates_audit_and_cannot_be_minor(self):
        p = make_plan()
        p['criteria']['stop_condition'] = '预算耗尽'
        self.frozen(p)
        prep = self.gov.prepare_audit('P1', actor='main')['prepared_id']
        self.gov.record_audit(prep, modern(prep), actor='r1')
        self.assertTrue(self.gov.audit_state('P1')['ok'])
        p['criteria']['stop_condition'] = '发现反例即停止'
        self.frozen(p)
        self.assertFalse(self.gov.audit_state('P1')['ok'])
        # 判据机器读项变了，不能当小修
        with self.assertRaises(g.GovernanceError):
            self.gov.correct_audit(self.gov.audit_state('P1')['report_id'],
                                   [{'what': '改停止条件', 'self_check': '想当措辞'}], actor='main')

    def test_context_budget_is_fully_included(self):
        from kernel.autoresearch.context import instruction_manifest, render_bundle, verify_injection
        root = Path(__file__).resolve().parents[1]
        manifest = instruction_manifest(root, root/'profiles/agent-harness')
        document = next(d for d in manifest['documents'] if d['source'] == 'kernel/contracts/context_budget.md')
        self.assertEqual(document['content'], (root/document['source']).read_text(encoding='utf-8'))
        bundle = render_bundle(manifest)
        self.assertTrue(verify_injection(manifest, bundle)['complete'])
        self.assertFalse(verify_injection(manifest, bundle.replace(document['content'], '# 只有摘要'))['complete'])

    def test_confirmatory_keeps_threshold(self):
        plan=make_plan();del plan['criteria']['threshold']
        self.assertTrue(any('threshold' in x for x in g.validate_plan(plan)))

    def test_execution_fingerprint(self):
        p=make_plan();fp=g.plan_fingerprint(p);p['execution_argv']=['run','--mode','new']
        self.assertNotEqual(fp,g.plan_fingerprint(p))
        p.pop('execution_argv');p['implementation']={'source':'module.py'}
        self.assertNotEqual(fp,g.plan_fingerprint(p))

    def test_limit_survives_version_changes(self):
        p=make_plan();self.frozen(p);self.gov.prepare_audit('P1',actor='main')
        p['criteria']['threshold']=2;self.frozen(p);self.gov.prepare_audit('P1',actor='main')
        p['criteria']['threshold']=3;self.frozen(p)
        with self.assertRaises(g.GovernanceError):self.gov.prepare_audit('P1',actor='main')

    def test_unknown_nonblocking_requires_reason(self):
        self.frozen();prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        r=modern(prep,'minor_fix',[issue()],coverage={'design':{'verdict':'unknown','note':'缺大样本','sources':[{'path':'a','locator':'L1'}]}})
        with self.assertRaises(g.GovernanceError):self.gov.record_audit(prep,r,actor='reviewer')
        r['issues'][0]['why_not_blocking']='本轮只观察机制，不外推幅度'
        self.gov.record_audit(prep,r,actor='reviewer')
        self.assertEqual(self.gov.audit_state('P1')['decision'],'minor_fix')

    def test_proceed_cannot_hide_blocking(self):
        self.frozen();prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        with self.assertRaises(g.GovernanceError):self.gov.record_audit(prep,modern(prep,issues=[issue(True)]),actor='reviewer')

    def test_history_block_survives_new_clean_report_and_version(self):
        p=make_plan();self.frozen(p);prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        first=self.gov.record_audit(prep,modern(prep,'blocked',[issue(True)]),actor='r1')
        self.gov.respond_audit(first['report_id'],[{'issue_id':'I1','disposition':'accepted','reason':'补充研究','sources':[{'path':'a','locator':'L1'}]}],actor='main')
        p['criteria']['threshold']=2;self.frozen(p)
        prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        self.gov.record_audit(prep,modern(prep),actor='r2')
        self.assertIn('I1',self.gov.audit_state('P1')['blocking_open'])

    def test_resolves_closes_old_block(self):
        self.frozen();prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        first=self.gov.record_audit(prep,modern(prep,'blocked',[issue(True)]),actor='r1')
        prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        self.gov.record_audit(prep,modern(prep,resolves=[{'report_id':first['report_id'],'issue_id':'I1','reason':'已核修正','sources':[{'path':'a','locator':'L1'}]}]),actor='r2')
        self.assertTrue(self.gov.audit_state('P1')['ok'])

    def test_fabricated_resolution_rejected(self):
        self.frozen();prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        with self.assertRaises(g.GovernanceError):self.gov.record_audit(prep,modern(prep,resolves=[{'report_id':'fake','issue_id':'I1','reason':'假关闭','sources':[{'path':'a','locator':'L1'}]}]),actor='r2')

    def test_minor_correction_without_reaudit(self):
        p=make_plan();p['input_files']=['docs/old.md'];self.frozen(p)
        prep=self.gov.prepare_audit('P1', actor='main')['prepared_id']
        rec=self.gov.record_audit(prep,modern(prep,'minor_fix',[issue()]),actor='r1')
        p['input_files']=['docs/new.md'];self.frozen(p)
        self.assertFalse(self.gov.audit_state('P1')['ok'])
        # 证据引用换文件＝自动小修：直接 correct，不需要任何授权表
        self.gov.correct_audit(rec['report_id'],[{'issue_id':'I1','what':'修正证据路径',
            'self_check':'新路径与读取内容一致'}],actor='main')
        state=self.gov.audit_state('P1');self.assertTrue(state['ok']);self.assertEqual(state['audits_used'],1)

    def test_hard_change_rejected_auto_minor_accepted(self):
        p=make_plan();p['input_files']=['docs/old.md'];self.frozen(p)
        prep=self.gov.prepare_audit('P1', actor='main')['prepared_id']
        rec=self.gov.record_audit(prep,modern(prep,'minor_fix',[issue()]),actor='r1')
        # 判据门槛是机器读项：改了不许记小修
        p['criteria']['threshold']=2;self.frozen(p)
        with self.assertRaises(g.GovernanceError):
            self.gov.correct_audit(rec['report_id'],[{'issue_id':'I1','what':'改门槛',
                'self_check':'只想小修'}],actor='main')
        # 改回去（门槛回到被审值），只留证据路径变化 ⇒ 自动小修通过
        p['criteria']['threshold']=1;p['input_files']=['docs/new.md'];self.frozen(p)
        self.gov.correct_audit(rec['report_id'],[{'issue_id':'I1','what':'修正证据路径',
            'self_check':'读取内容一致'}],actor='main')
        self.assertTrue(self.gov.audit_state('P1')['ok'])

    def test_minor_fix_paths_authorization_is_rejected(self):
        self.frozen();prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        report=modern(prep,'minor_fix',[issue()],
            minor_fix_paths=[{'path':'input_files.0','issue_id':'I1','why':'证据路径笔误'}])
        with self.assertRaises(g.GovernanceError) as ctx:
            self.gov.record_audit(prep,report,actor='r1')
        self.assertIn('已撤销',str(ctx.exception))

    def test_wording_only_requires_before_after_text(self):
        p=make_plan();self.frozen(p)
        prep=self.gov.prepare_audit('P1',actor='main')['prepared_id']
        rec=self.gov.record_audit(prep,modern(prep,'minor_fix',[issue()]),actor='r1')
        before=p['hypothesis']['text']
        after=before+'。'
        p['hypothesis']={'id':'H1','text':after};self.frozen(p)
        with self.assertRaises(g.GovernanceError):
            self.gov.correct_audit(rec['report_id'],[{'issue_id':'I1','what':'写顺假设',
                'self_check':'否定条件不变','wording_only':[{'path':'hypothesis.text',
                'before':'错的原文','after':after,'why':'同义'}]}],actor='main')
        # 实现文件路径/字节不在白名单
        with self.assertRaises(g.GovernanceError):
            self.gov.correct_audit(rec['report_id'],[{'issue_id':'I1','what':'改实现',
                'self_check':'只是措辞','wording_only':[{'path':'implementation.source',
                'before':'a','after':'b','why':'x'}]}],actor='main')
        self.gov.correct_audit(rec['report_id'],[{'issue_id':'I1','what':'写顺假设',
            'self_check':'否定条件不变','wording_only':[{'path':'hypothesis.text',
            'before':before,'after':after,'why':'同义订正'}]}],actor='main')
        self.assertTrue(self.gov.audit_state('P1')['ok'])

    def test_correction_table_append_only(self):
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO gov_audit_corrections(report_id,old_fingerprint,new_fingerprint,detail_json,actor,created_at) VALUES('r','a','a','[]','main',0)")
        with self.assertRaises(Exception):
            with self.store.transaction() as conn:
                conn.execute('UPDATE gov_audit_corrections SET actor="tamper"')


class ReportFileTests(unittest.TestCase):
    def test_digest_binding_and_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.json';r=modern('PREP-x')
            path.write_text(json.dumps(r),encoding='utf-8')
            decl={k:r[k] for k in ('prepared_id','decision','decision_reason')}
            decl.update(report_file=str(path.resolve()),report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),issues=[])
            self.assertEqual(load_bound_report(str(path),decl),r)
            path.write_text(json.dumps(r)+' ',encoding='utf-8')
            with self.assertRaises(ValueError):load_bound_report(str(path),decl)


class DeclarationTests(GovCase):
    def test_declaration_is_generated_and_bound(self):
        self.frozen()
        prep = self.gov.prepare_audit('P1', actor='main')['prepared_id']
        report = modern(prep, 'proceed', [])
        path = Path(self._tmp.name) / 'report.json'
        path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        decl = self.gov.audit_declaration('P1', str(path))
        self.assertEqual(set(decl), set(g.DECLARATION_KEYS))
        self.assertEqual(decl['prepared_id'], prep)
        self.assertEqual(decl['report_file'], str(path.resolve()))
        path.write_text(json.dumps(report, ensure_ascii=False) + ' ', encoding='utf-8')
        with self.assertRaises(g.GovernanceError):
            self.gov.record_audit_file(prep, str(path), decl, actor='reviewer')

    def test_report_may_omit_prepared_id(self):
        self.frozen()
        prep = self.gov.prepare_audit('P1', actor='main')['prepared_id']
        report = modern(prep)
        report.pop('prepared_id')
        self.gov.record_audit(prep, report, actor='reviewer')
        self.assertTrue(self.gov.audit_state('P1')['ok'])

    def test_declaration_refuses_stale_prepare(self):
        p = make_plan()
        self.frozen(p)
        prep = self.gov.prepare_audit('P1', actor='main')['prepared_id']
        report = modern(prep)
        path = Path(self._tmp.name) / 'report.json'
        path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        p['criteria']['threshold'] = 2
        self.frozen(p)
        with self.assertRaises(g.GovernanceError):
            self.gov.audit_declaration('P1', str(path))


class ImplementationBindingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name)
        (self.ws / 'submission').mkdir()
        self.impl = self.ws / 'submission' / 'impl.py'
        self.impl.write_text('x = 1\n', encoding='utf-8')
        from kernel.autoresearch.runtime.store import Store
        self.gov = g.Governance(Store(str(self.ws / 'gov.sqlite')), workspace=self.ws)

    def plan(self):
        return make_plan(implementation={'files': ['submission/impl.py']})

    def test_implementation_bytes_are_bound(self):
        self.gov.freeze_plan(self.plan(), actor='main')
        prep = self.gov.prepare_audit('P1', actor='main')['prepared_id']
        self.gov.record_audit(prep, modern(prep), actor='reviewer')
        self.assertTrue(self.gov.audit_state('P1')['ok'])
        self.impl.write_text('x = 2\n', encoding='utf-8')
        state = self.gov.audit_state('P1')
        self.assertFalse(state['ok'])
        self.assertTrue(any('实现文件' in item for item in state['problems']), state['problems'])
        with self.assertRaises(g.GovernanceError):
            self.gov.correct_audit(state['report_id'],
                                   [{'what': '小修', 'self_check': '已读'}], actor='main')

    def test_prepare_requires_existing_implementation_file(self):
        self.gov.freeze_plan(make_plan(implementation={'files': ['submission/none.py']}),
                             actor='main')
        with self.assertRaises(g.GovernanceError) as ctx:
            self.gov.prepare_audit('P1', actor='main')
        self.assertIn('实现文件', str(ctx.exception))

    def test_record_rejects_changed_bytes_since_prepare(self):
        self.gov.freeze_plan(self.plan(), actor='main')
        prep = self.gov.prepare_audit('P1', actor='main')['prepared_id']
        self.impl.write_text('x = 3\n', encoding='utf-8')
        with self.assertRaises(g.GovernanceError) as ctx:
            self.gov.record_audit(prep, modern(prep), actor='reviewer')
        self.assertIn('实现文件', str(ctx.exception))


class ResearchPageTests(unittest.TestCase):
    def page(self):
        return '\n\n'.join('## '+str(i+1)+' '+name+'\n'+('### 小样本机制观察\n假设：x\n判据：条件与解释边界\n设置：同装置\n结果：尚不能外推\n来源：evidence.md' if i==2 else '研究内容') for i,name in enumerate(g.STATUS_SECTIONS))

    def test_research_page_without_process_fields(self):
        self.assertEqual(g.validate_status_page(self.page()),[])

    def test_wrong_four_part_order_rejected(self):
        self.assertTrue(g.validate_status_page(self.page().replace('假设：x','结果：x').replace('结果：尚不能外推','假设：尚不能外推')))

    def test_missing_source_rejected(self):
        self.assertTrue(g.validate_status_page(self.page().replace('来源：evidence.md','')))

    def test_empty_section_rejected(self):
        self.assertTrue(g.validate_status_page(self.page().replace('## 1 你的要求\n研究内容','## 1 你的要求')))

    def test_no_experiment_yet(self):
        page=self.page();a=page.index('### 小样本');b=page.index('## 4 ')
        self.assertEqual(g.validate_status_page(page[:a]+'暂无实验，未起跑。原因：尚未形成可检验问题。\n\n'+page[b:]),[])
