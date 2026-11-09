# GENERATED-BY: 统一只读检查视图测试，2026-11-09
# LAST-EDIT: 2026-11-09 首版：只读零写 / 缺 schema 不绿 / 缺复盘·待监控·超期入向分列 / closed 不挡新轮 / 审批范围 / E5 交接
# PURPOSE: 证明 checklist 读的是**真实生产类**的真实台账：不建库、不建目录、不迁移 schema、不造宿主证明；
#          缺证据一律 unknown，且各缺口分开列而不是糊成一个「整体不绿」。
# ORIGIN: kernel/autoresearch/assurance.py + tests/test_decisions.py 的假通道夹具 + tests/test_workflow.py 的临时工作区夹具
# CUMULATIVE-DELTA: 首版。全部在临时目录里跑真实 Store / DecisionService / Issues / Questions / Workflow（含真实子进程），
#   ⛔ 不连真实云、不发消息、不读真实宿主日志；唯一假件是测试自己的通道与身份核验器。

"""统一只读检查视图（E2/E4/E5/E7）的集成测试。

判据（不是「跑通就行」）：

1. **只读零写**：`checklist` 前后整棵工作区树（含 DB 字节）不变；缺库不建库、缺 `.autoresearch`
   不建目录；干净 WAL 库上不创建 `-wal` / `-shm`。
2. **缺数据不绿**：缺库 / 缺 schema / 缺负责人会话 / 缺宿主适配器 ⇒ 对应项 `unknown`，
   `ok=False`，`exit_code=2`；⛔ 不把「读不到」读成「没问题」。
3. **缺口分列**：`workflow.review`（缺复盘）、`monitoring.intent`（待监控）、
   `decisions.inbound`（超期入向）、`questions.open`（待答/超期）各自一项，不合并。
4. **不挡新轮**：已结案（closed）的问题不算未结；未关联实验的通用工程问题只作
   `issues.exp_link` 诊断，不进本实验判词。
5. **审批只看真实范围**：`small`/`none` ⇒ `decisions.approval` 不适用（⛔ 不是「已批准」）；
   `large` 没有已批准决策 ⇒ blocked；`review.continuation` 里的 `needs_approval` 只是声明。
6. **E5 交接**：`review_continuation.project` 的 `recorded` 才算有下一步交接；
   旧记录（无 `schema_version`）⇒ unknown + legacy。
7. **可注入**：`services={'monitoring': …}` 时用注入对象，`sources` 记 `injected`。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _candidate in (ROOT, ROOT / 'tests'):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from kernel.autoresearch import reporting as reporting_module  # noqa: E402
from kernel.autoresearch.assurance import AssuranceError, checklist  # noqa: E402
from kernel.autoresearch.assurance_cli import main as assurance_main  # noqa: E402
from kernel.autoresearch.decisions import DecisionService  # noqa: E402
from kernel.autoresearch.issues import Issues  # noqa: E402
from kernel.autoresearch.questions import Questions  # noqa: E402
from kernel.autoresearch.runtime.accounts import Accounts  # noqa: E402
from kernel.autoresearch.runtime.delivery import Delivery  # noqa: E402
from kernel.autoresearch.runtime.inbox import Inbox  # noqa: E402
from kernel.autoresearch.runtime.store import Store  # noqa: E402
from kernel.autoresearch.runtime.topics import Topics  # noqa: E402
import test_decisions as decision_fixtures  # noqa: E402
import test_workflow as flow_fixtures  # noqa: E402

EXP = 'exp-01'
OTHER_EXP = 'exp-02'
OWNER = 'sess-01'
RECIPIENT = 'chat-01'


def tree_snapshot(root):
    root = Path(root)
    out = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        out[rel + '/' if path.is_dir() else rel] = (
            '<dir>' if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def items_of(result):
    return {item['name']: item for item in result['items']}


def clean_wal_database(database):
    """把库切成 WAL 并 checkpoint，再删 sidecar（只在本测试自己的临时目录里做）。"""
    conn = sqlite3.connect(str(database))
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.close()
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def workspace_with_state(test, name='ws'):
    """空工作区 + 真实 Store + 一个专题（Topics 是生产类）。"""
    ws = Path(test._tmp.name) / name
    (ws / '.autoresearch').mkdir(parents=True)
    store = Store(ws / '.autoresearch' / 'runtime.sqlite3')
    Topics(store).create_topic('T1', title='提高通过题数', objective='把通过题数从 3 提到 4')
    return ws, store


class AssuranceCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='hil_assurance_')
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# 1. 只读零写：不建库、不建目录、不迁移 schema、不创建 WAL sidecar
# ---------------------------------------------------------------------------


class ReadonlyTests(AssuranceCase):
    def test_missing_state_creates_nothing_and_stays_unknown(self):
        ws = self.root / 'empty'
        (ws / '.autoresearch').mkdir(parents=True)
        before = tree_snapshot(ws)
        result = checklist(ws, topic='T1', exp_id=EXP, owner_session=OWNER)
        self.assertEqual(tree_snapshot(ws), before, '只读清单不许建库/建目录/建文件')
        self.assertFalse((ws / '.autoresearch' / 'runtime.sqlite3').exists())
        self.assertFalse(result['ok'])
        self.assertEqual(result['exit_code'], 2)
        items = items_of(result)
        for name in ('workflow.preparation', 'decisions.inbound', 'decisions.approval',
                     'issues.registry', 'questions.open'):
            self.assertEqual(items[name]['status'], 'unknown', name)
            self.assertTrue(items[name]['problems'], name)
        self.assertEqual(items['issues.registry']['status'], 'unknown',
                         '台账不存在 ⇒ 不可判，⛔ 不是「没有问题」')
        self.assertEqual(items['checklist.scope']['status'], 'ok')
        self.assertEqual(set(result['sources'].values()), {'readonly'})
        self.assertTrue(result['unlinked_items'])

    def test_workspace_without_state_directory_is_not_created(self):
        ws = self.root / 'bare'
        ws.mkdir()
        before = tree_snapshot(ws)
        result = checklist(ws, topic='T1', exp_id=EXP, owner_session=OWNER)
        self.assertEqual(tree_snapshot(ws), before,
                         '连 .autoresearch/ 都不许被只读清单创建出来')
        self.assertFalse((ws / '.autoresearch').exists())
        self.assertFalse(result['ok'])
        self.assertEqual(result['exit_code'], 2)

    def test_database_without_decision_tables_is_unknown(self):
        ws, _store = workspace_with_state(self)
        before = tree_snapshot(ws)
        result = checklist(ws, exp_id=EXP, owner_session=OWNER)
        self.assertEqual(tree_snapshot(ws), before,
                         '读一个只建了 Store 基础表的库不许补表')
        items = items_of(result)
        self.assertEqual(items['decisions.inbound']['status'], 'unknown')
        self.assertIn('缺少决策台账表', '；'.join(items['decisions.inbound']['problems']))
        self.assertEqual(items['questions.open']['status'], 'unknown')
        self.assertFalse(result['ok'])
        self.assertEqual(result['exit_code'], 2)

    def test_clean_wal_database_read_creates_no_sidecars(self):
        ws, _store = workspace_with_state(self, 'wal')
        database = ws / '.autoresearch' / 'runtime.sqlite3'
        # 先把真实台账写进去（问题 + 提问），再切成干净 WAL
        (ws / 'results').mkdir()
        (ws / 'results' / 'run.log').write_text('boom\n', encoding='utf-8')
        issues = Issues(ws)
        issues.record(summary='跑测试崩了', owner=OWNER, command=['python', '-m', 'pytest'],
                      exitcode=1, sources=[{'path': 'results/run.log', 'locator': 'L1'}],
                      scope='tests', exp_id=EXP)
        store = Store(database)
        delivery = Delivery(store)
        inbox = Inbox(store, Accounts(store), delivery,
                      authenticate=lambda source, event: event.get('principal'))
        Questions(ws, store=store, delivery=delivery, inbox=inbox,
                  authenticate=lambda source, event: event.get('principal')).ask(
            EXP, '基线用哪一版？', owner=OWNER, asked_by='agent')
        clean_wal_database(database)
        self.assertFalse(Path(str(database) + '-wal').exists())
        self.assertFalse(Path(str(database) + '-shm').exists())
        before = tree_snapshot(ws)
        result = checklist(ws, exp_id=EXP, owner_session=OWNER)
        self.assertEqual(tree_snapshot(ws), before,
                         '干净 WAL 库上只读清单不许创建 -wal / -shm，也不许改主库')
        self.assertFalse(Path(str(database) + '-wal').exists())
        self.assertFalse(Path(str(database) + '-shm').exists())
        items = items_of(result)
        self.assertEqual(items['issues.registry']['status'], 'blocked',
                         '这条真实问题还没结案 ⇒ 未结问题要挡住')
        self.assertEqual(items['questions.open']['status'], 'ok',
                         '没有 deadline 的待答问题诚实列在 open，不算超期')

    def test_scope_is_required_for_a_verdict(self):
        ws, _store = workspace_with_state(self, 'scope')
        before = tree_snapshot(ws)
        unscoped = checklist(ws)
        self.assertEqual(tree_snapshot(ws), before)
        self.assertFalse(unscoped['ok'], '没有 topic/exp 就没有可判绿的轮次')
        self.assertEqual(items_of(unscoped)['checklist.scope']['status'], 'unknown')
        self.assertEqual(unscoped['exit_code'], 2)
        informational = checklist(ws, required=False)
        self.assertEqual(informational['exit_code'], 0)
        self.assertFalse(informational['items'][0]['required'])

    def test_cli_reports_json_and_exit_codes(self):
        ws = self.root / 'cli'
        (ws / '.autoresearch').mkdir(parents=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = assurance_main(['--workspace', str(ws), 'checklist', '--exp', EXP])
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload['ok'])
        self.assertIn('items', payload)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = assurance_main(['--workspace', str(self.root / 'nope'), 'checklist'])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out.getvalue())['ok'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = assurance_main(['--workspace', str(ws), 'checklist', '--exp', EXP,
                                   '--informational', '--human'])
        self.assertEqual(code, 0)
        self.assertIn('checklist.scope', out.getvalue())


# ---------------------------------------------------------------------------
# 2. 缺口分列：缺复盘 / 待监控 / 超期入向 / 待答问题
# ---------------------------------------------------------------------------


class SeparateItemsTests(AssuranceCase):
    def build(self, *, overdue_inbound=True, open_question=True):
        ws, store = workspace_with_state(self)
        (ws / 'results').mkdir()
        (ws / 'evidence').mkdir()
        (ws / 'results' / 'run.log').write_text('boom at step 3\n', encoding='utf-8')
        (ws / 'evidence' / 'quote.md').write_text('用户：用 v3\n', encoding='utf-8')
        store.ensure_experiment(EXP)
        store.ensure_experiment(OTHER_EXP)
        # 1) 已结案的问题（不许挡新轮）
        issues = Issues(ws)
        closed = issues.record(summary='旧崩溃', owner=OWNER, command=['python', '-m', 'pytest'],
                               exitcode=1, sources=[{'path': 'results/run.log', 'locator': 'L1'}],
                               scope='tests', exp_id=EXP)
        issues.update(closed['issue_id'], kind='fixed', actor='agent', text='改了入口校验',
                      sources=[{'path': 'results/run.log', 'locator': 'L1'}])
        issues.update(closed['issue_id'], kind='verified', actor='agent', text='复跑同一条',
                      sources=[{'path': 'results/run.log', 'locator': 'L1'}],
                      command=['python', '-m', 'pytest'], exitcode=0)
        # 2) 另一个实验里未结案的问题（不该挡本实验）
        issues.record(summary='别的实验崩了', owner=OWNER, command=['python', '-m', 'pytest'],
                      exitcode=1, sources=[{'path': 'results/run.log', 'locator': 'L1'}],
                      scope='tests', exp_id=OTHER_EXP)
        # 3) 通用工程问题：明确没有实验关联（unlinked，只作诊断）
        issues.record(summary='打包脚本 fail', owner=OWNER, command=['python', 'pack.py'],
                      exitcode=2, sources=[{'path': 'results/run.log', 'locator': 'L1'}],
                      scope='packaging')
        # 4) 提问：过期 + 已认证回复
        clock = decision_fixtures.Clock()
        delivery = Delivery(store, clock=clock)
        inbox = Inbox(store, Accounts(store), delivery,
                      authenticate=lambda source, event: event.get('principal'), clock=clock)
        ledger = Questions(ws, store=store, delivery=delivery, inbox=inbox,
                           authenticate=lambda source, event: event.get('principal'),
                           clock=clock)
        question = ledger.ask(EXP, '基线用哪一版？', owner=OWNER, asked_by='agent',
                              recipient=RECIPIENT,
                              deadline=clock.now + (10 if open_question else -10))
        # 5) 审批与入向：一条超期未回的文字（答复正文带可复制的问题标记）
        channel = decision_fixtures.FakeChannel(clock)
        service = DecisionService(store, channel=channel, clock=clock)
        event = decision_fixtures.question_event(
            event_id='ev-old', create_time=clock.now - (1000 if overdue_inbound else 0),
            body=f"[question:{question['question_id']}] 用 v3 那一版")
        service.ingest('fake', event)
        return {'ws': ws, 'store': store, 'clock': clock, 'issues': issues,
                'ledger': ledger, 'service': service, 'question': question}

    def test_gaps_are_reported_as_separate_items(self):
        fixture = self.build()
        result = checklist(fixture['ws'], exp_id=EXP, owner_session=OWNER,
                           now=fixture['clock'].now + 60)
        items = items_of(result)
        self.assertEqual(items['workflow.review']['status'], 'ok')
        self.assertFalse(items['workflow.review']['applicable'], '没有运行 ⇒ 缺复盘不适用')
        self.assertEqual(items['monitoring.intent']['status'], 'unknown')
        self.assertIn('register', ' '.join(items['monitoring.intent']['problems']),
                      '没登记意图监控 ⇒ 待监控要单独报，⛔ 不当作通过')
        self.assertEqual(items['decisions.inbound']['status'], 'blocked')
        self.assertIn('ev-old', ' '.join(items['decisions.inbound']['problems']))
        self.assertEqual(items['questions.open']['status'], 'ok',
                         '普通问题超期只是诊断：问了问题不代表实验要停等')
        self.assertFalse(items['questions.open']['required'])
        self.assertIn(fixture['question']['question_id'], ' '.join(items['questions.open']['notes']))
        self.assertTrue(any('非 blocking' in note
                            for note in items['questions.open']['notes']))
        self.assertEqual(items['issues.registry']['status'], 'ok',
                         '本实验只有已结案问题：closed 不许挡新轮')
        not_counted = [entry['issue_id'] for entry in
                       items['issues.registry']['evidence']['not_counted']]
        self.assertEqual(not_counted, ['ISSUE-000003'],
                         '只有「没有实验关联」的记录算未入台账')
        self.assertEqual(items['issues.registry']['evidence']['unresolved'], [],
                         '另一个实验的未结问题不许算进本实验')
        self.assertEqual(items['issues.exp_link']['status'], 'unknown',
                         '未关联实验的通用工程问题只能作诊断')
        self.assertFalse(items['issues.exp_link']['required'])
        self.assertEqual(set(result['blocked']) & {'decisions.inbound', 'questions.open'},
                         {'decisions.inbound'})
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['unlinked_items'][:1], [result['unlinked_items'][0]])

    def test_blocking_question_gates_the_dependent_step(self):
        fixture = self.build()
        blocking = fixture['ledger'].ask(EXP, '扩题单前先定基线？', owner=OWNER,
                                         asked_by='agent', blocking=True)
        result = checklist(fixture['ws'], exp_id=EXP, owner_session=OWNER,
                           now=fixture['clock'].now + 60)
        item = items_of(result)['questions.open']
        self.assertEqual(item['status'], 'blocked')
        self.assertTrue(item['required'], '明确 blocking 且关联本实验 ⇒ 才进判词')
        self.assertEqual(item['evidence']['blocking_open'], [blocking['question_id']])
        self.assertIn(blocking['question_id'], ' '.join(item['problems']))
        self.assertIn('questions.open', result['blocked'])

    def test_unrelated_open_question_does_not_block_other_experiment(self):
        fixture = self.build(overdue_inbound=False)
        self.assertEqual(
            [row['question_id'] for row in
             fixture['ledger'].open_questions(OTHER_EXP)['open']], [],
            '别的问题不属于这个实验')
        result = checklist(fixture['ws'], exp_id=OTHER_EXP, owner_session=OWNER,
                           now=fixture['clock'].now + 60)
        item = items_of(result)['questions.open']
        self.assertEqual(item['status'], 'ok')
        self.assertEqual(item['evidence']['open'], [])

    def test_answered_question_and_fresh_inbound_clear_their_own_items(self):
        fixture = self.build(overdue_inbound=False)
        fixture['ledger'].answer(fixture['question']['question_id'], source='fake',
                                 event_id='ev-old')
        result = checklist(fixture['ws'], exp_id=EXP, owner_session=OWNER,
                           now=fixture['clock'].now)
        items = items_of(result)
        self.assertEqual(items['questions.open']['status'], 'ok')
        self.assertEqual(items['questions.open']['evidence']['open'], [])
        self.assertEqual(items['decisions.inbound']['status'], 'ok')
        self.assertEqual(items['decisions.inbound']['evidence']['pending'], 1)

    def test_missing_review_is_its_own_item(self):
        ws, store = workspace_with_state(self, 'runs')
        # 用真实 Workflow 的只读投影：直接写 wf_runs + wf_finishes 需要真实启动，
        # 这里只证明「读了真实表并按 pending_reports 判」——用最小真实表结构。
        with contextlib.closing(sqlite3.connect(str(ws / '.autoresearch' / 'runtime.sqlite3'))) as conn:
            conn.execute('CREATE TABLE wf_runs(run_id TEXT PRIMARY KEY, prepared_id TEXT NOT NULL, '
                         'plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, topic TEXT NOT NULL, '
                         'owner_session TEXT NOT NULL, hypothesis_id TEXT, hypothesis_text TEXT, '
                         'argv_json TEXT NOT NULL, cwd TEXT NOT NULL, gates_json TEXT NOT NULL, '
                         'source_fingerprint TEXT NOT NULL, plan_sha256 TEXT NOT NULL, '
                         'started_at REAL NOT NULL)')
            conn.execute('CREATE TABLE wf_finishes(run_id TEXT PRIMARY KEY, reason TEXT NOT NULL, '
                         'exitcode INTEGER, signal TEXT, documentation_status TEXT NOT NULL, '
                         'documentation_detail TEXT NOT NULL, finished_at REAL NOT NULL)')
            conn.execute("INSERT INTO wf_runs VALUES('RUN-1','PREP-1','exp-01',1,'T1',?,NULL,NULL,"
                         "'[]','.', '{}','sha','sha',0)", (OWNER,))
            conn.execute("INSERT INTO wf_finishes VALUES('RUN-1','completed',0,NULL,'ok','',1)")
            conn.commit()
        result = checklist(ws, exp_id=EXP, owner_session=OWNER)
        items = items_of(result)
        self.assertEqual(items['workflow.review']['status'], 'blocked')
        self.assertEqual(items['workflow.review']['evidence']['pending_reports'], ['RUN-1'])
        self.assertIn('缺复盘', ' '.join(items['workflow.review']['problems']))


# ---------------------------------------------------------------------------
# 3. 审批只看真实范围；E5 交接里 needs_approval 只是声明
# ---------------------------------------------------------------------------


class ApprovalTests(AssuranceCase):
    def fixture(self):
        ws, store = workspace_with_state(self)
        (ws / 'evidence').mkdir()
        (ws / 'evidence' / 'readings.md').write_text('读数 3\n', encoding='utf-8')
        clock = decision_fixtures.Clock()
        channel = decision_fixtures.FakeChannel(clock)
        service = DecisionService(store, channel=channel, clock=clock)
        store.ensure_experiment(EXP)
        return ws, store, clock, channel, service

    def prepare(self, ws, service, decision_id='E1.1', **changes):
        return service.prepare(exp_id=EXP, decision=decision_fixtures.decision(decision_id, **changes),
                               plan_set=decision_fixtures.plan_set(),
                               page_dir=ws / 'web',
                               execution_plan=decision_fixtures.workflow_plan())

    def test_no_plan_no_decision_is_not_applicable_not_approved(self):
        ws, _store, clock, _channel, _service = self.fixture()
        result = checklist(ws, exp_id=EXP, owner_session=OWNER, now=clock.now)
        approval = items_of(result)['decisions.approval']
        self.assertEqual(approval['status'], 'unknown',
                         '没有计划范围 ⇒ 不判「需不需要批」，⛔ 也不当成已批准')
        self.assertFalse(approval['applicable'])
        self.assertFalse(approval['required'], '不知道要不要批 ⇒ 本实例不按它判绿')
        self.assertIn('不判「需不需要批准」', ' '.join(approval['problems']))
        self.assertIn('不当成已批准', ' '.join(approval['problems']))

    def test_large_scope_without_approval_is_blocked(self):
        ws, _store, clock, _channel, service = self.fixture()
        self.prepare(ws, service)
        result = checklist(ws, exp_id=EXP, owner_session=OWNER, now=clock.now)
        approval = items_of(result)['decisions.approval']
        self.assertEqual(approval['status'], 'blocked')
        self.assertEqual(approval['evidence']['plan_scope'], 'large')
        self.assertIn('没有本实验的已批准决策', ' '.join(approval['problems']))
        self.assertEqual(result['exit_code'], 3)

    def test_large_scope_with_real_approval_is_ok(self):
        ws, _store, clock, _channel, service = self.fixture()
        self.prepare(ws, service)
        service.send('E1.1')
        service.ingest('fake', decision_fixtures.card_event(service))
        result = checklist(ws, exp_id=EXP, owner_session=OWNER, now=clock.now)
        approval = items_of(result)['decisions.approval']
        self.assertEqual(approval['status'], 'ok')
        self.assertEqual([row['decision_id'] for row in approval['evidence']['approved']],
                         ['E1.1'])
        self.assertEqual(approval['evidence']['approved'][0]['resolution'], 'user_approved')

    def test_quarantined_delivery_is_blocked(self):
        ws, _store, clock, channel, service = self.fixture()
        self.prepare(ws, service)
        channel.fail_file = 'ambiguous'
        sent = service.send('E1.1')
        self.assertEqual(sent['status'], 'quarantined', '结果不确定 ⇒ 隔离，⛔ 不自动重发')
        result = checklist(ws, exp_id=EXP, owner_session=OWNER, now=clock.now)
        approval = items_of(result)['decisions.approval']
        self.assertEqual(approval['status'], 'blocked')
        self.assertTrue(approval['required'])
        self.assertIn('隔离', ' '.join(approval['problems']))


# ---------------------------------------------------------------------------
# 4. 真实一轮：缺复盘 → 复盘后 ok；E5 交接 v1 unknown / v2 recorded
# ---------------------------------------------------------------------------


class RoundCase(unittest.TestCase):
    """把 `tests/test_workflow.py` 的真实工作区夹具借过来（不继承它的测试方法）。"""

    setUp = flow_fixtures.FlowCase.setUp
    tearDown = flow_fixtures.FlowCase.tearDown
    make_flow = flow_fixtures.FlowCase.make_flow
    plan = flow_fixtures.FlowCase.plan
    write_plan = flow_fixtures.FlowCase.write_plan
    prepare = flow_fixtures.FlowCase.prepare
    audit = flow_fixtures.FlowCase.audit
    ready = flow_fixtures.FlowCase.ready
    launch = flow_fixtures.FlowCase.launch
    finished_run = flow_fixtures.FlowCase.finished_run
    review_record = flow_fixtures.FlowCase.review_record
    append_doc = flow_fixtures.FlowCase.append_doc
    monitor_ok = flow_fixtures.FlowCase.monitor_ok
    authorization_ok = flow_fixtures.FlowCase.authorization_ok

    TOPIC = flow_fixtures.TOPIC
    SESSION = flow_fixtures.SESSION


class WorkflowRoundTests(RoundCase):
    def run_round(self, plan_id, *, review=None):
        built, launch = self.finished_run(plan_id)
        return built, launch

    def test_missing_review_then_legacy_review(self):
        built, launched = self.finished_run('exp-01')
        self.assertTrue(launched['ok'])
        result = checklist(self.ws, topic=self.TOPIC, exp_id='exp-01')
        items = items_of(result)
        self.assertEqual(items['workflow.preparation']['status'], 'blocked',
                         '前轮已结束未复盘 ⇒ 下一轮被拦住（这是真实的 next_round 判词）')
        self.assertIn('不能重跑', ' '.join(items['workflow.preparation']['problems']))
        self.assertEqual(items['workflow.review']['status'], 'blocked')
        self.assertIn(launched['run_id'], items['workflow.review']['evidence']['pending_reports'])
        self.assertEqual(items['review.continuation']['status'], 'unknown')
        self.assertFalse(items['review.continuation']['applicable'],
                         '还没有复盘 ⇒ 没有下一步交接可判')
        self.assertEqual(items['decisions.approval']['status'], 'ok')
        self.assertFalse(items['decisions.approval']['applicable'])
        self.assertIn('small', ' '.join(items['decisions.approval']['notes']),
                      '计划声明的范围是 small ⇒ 不需要专项批准（⛔ 不是已批准）')
        self.assertEqual(items['reporting.page']['status'], 'blocked')
        self.assertEqual(items['reporting.round']['status'], 'blocked')
        self.assertFalse(result['ok'])
        self.assertEqual(result['exit_code'], 3)

        self.append_doc('supported')
        reviewed = self.flow.review(run_id=launched['run_id'],
                                    record=self.review_record())
        self.assertTrue(reviewed['ok'])
        after = checklist(self.ws, topic=self.TOPIC, exp_id='exp-01')
        items = items_of(after)
        self.assertEqual(items['workflow.review']['status'], 'ok')
        self.assertEqual(items['workflow.preparation']['status'], 'blocked')
        self.assertIn('不能重跑', ' '.join(items['workflow.preparation']['problems']),
                      '同一 plan id 不许重跑（J6）；复盘之后仍要换新轮次号')
        self.assertNotIn('未复盘', ' '.join(items['workflow.preparation']['problems']))
        continuation = items['review.continuation']
        self.assertEqual(continuation['status'], 'unknown', '旧记录没有 schema_version')
        self.assertTrue(continuation['evidence']['legacy'])
        self.assertEqual(continuation['evidence']['schema_version'], 1)
        self.assertIn('declared', ' '.join(continuation['notes']))

    def test_v2_continuation_is_recorded_but_never_an_approval(self):
        built, launched = self.finished_run('exp-01')
        self.append_doc('supported')
        record = self.review_record(
            schema_version=2,
            continuation={
                'criterion': '下一轮通过题数 ≥ 5',
                'criterion_evidence': [{'path': 'evidence/readings.md', 'locator': 'L1'}],
                'cost_scope': {'scope': 'large', 'max_cost_units': 5,
                               'basis': '同一题单一次全量比较，按上一轮单题耗时推算'},
                'next_owner': 'session-owner',
                'needs_approval': True,
                'approval_reason': '扩大题单属于需要人批准的范围',
            })
        reviewed = self.flow.review(run_id=launched['run_id'], record=record)
        self.assertTrue(reviewed['ok'])
        result = checklist(self.ws, topic=self.TOPIC, exp_id='exp-01')
        items = items_of(result)
        continuation = items['review.continuation']
        self.assertEqual(continuation['status'], 'ok')
        self.assertEqual(continuation['evidence']['status'], 'recorded')
        self.assertEqual(continuation['evidence']['approval_status'], 'pending')
        self.assertTrue(continuation['evidence']['needs_approval'])
        self.assertEqual(continuation['evidence']['cost_scope']['scope'], 'large')
        self.assertEqual(continuation['evidence']['next_owner'], 'session-owner')
        self.assertIn('不能当审批证据', ' '.join(continuation['notes']))
        # 声明需要批准 ≠ 已批准：本轮真实计划范围是 small（不适用），而下一步声明 pending
        approval = items['decisions.approval']
        self.assertEqual(approval['status'], 'ok')
        self.assertFalse(approval['applicable'],
                         '本轮计划范围 small ⇒ 不需要专项批准（⛔ 不是「已批准」）')
        self.assertIn('small', ' '.join(approval['notes']))
        self.assertIn('不能当审批证据', ' '.join(continuation['notes']))
        # 但缺复盘已经不是阻断项了
        self.assertEqual(items['workflow.review']['status'], 'ok')


# ---------------------------------------------------------------------------
# 5. 可注入服务：注入就用注入的，没注入才只读构造
# ---------------------------------------------------------------------------


class GateItemsTests(RoundCase):
    """E4 三分项：`workflow.audit` / `budget.reservation` / `remote.lease`。

    口径（按复核意见）：**已启动**的轮次只展示历史审计与绑定，⛔ 不强求「新的起跑可行」；
    **未启动**的轮次才核当前审计 / 当前可用预算预留 / 远端资源租约可用证据。任何一项
    缺证据都不许给出总 `ok=True`。
    """

    KEY = b'explicit-synthetic-fixture-key-000000000'

    # -- 夹具 ---------------------------------------------------------------

    def accounts(self):
        from kernel.autoresearch.runtime.accounts import (
            Accounts, ApprovalEvidence, HMACSource, SourcePolicy)
        return Accounts(self.store, ApprovalEvidence(
            {'fixture': SourcePolicy(HMACSource(self.KEY), frozenset({'fixture-user'}))}))

    def grant_and_reserve(self, *, series='series-small', exp='exp-01', units=3,
                          reservation='reservation-small', expires_in=3600, scope='small'):
        import hashlib
        import hmac
        from kernel.autoresearch.runtime.store import canonical_json
        self.store.ensure_experiment(exp)
        accounts = self.accounts()
        event = {'event_id': 'grant-' + series, 'series_id': series, 'exp_id': exp,
                 'scope': scope, 'action': 'grant', 'expires_at': time.time() + expires_in,
                 'total_units': 10, 'principal': 'fixture-user'}
        event['signature'] = hmac.new(self.KEY, canonical_json(event).encode(),
                                      hashlib.sha256).hexdigest()
        accounts.authorize('fixture', event, 'grant-request-' + series)
        accounts.reserve(series, exp, scope, units, reservation)
        return accounts

    def freeze(self, plan_id, *, execution=None, series='series-small',
               reservation='reservation-small', scope='small', budget=None):
        """冻结一份计划（治理层真校验 + 真冻结）；`execution` 给了就一起参与指纹。"""
        payload = self.plan(plan_id, budget=budget, authorization={'scope': scope, 'series_id': series,
                                                   'reservation_id': reservation})
        if execution is not None:
            payload['execution'] = dict(execution)
        rel = self.write_plan(plan_id, payload)
        self.flow.prepare(topic=self.TOPIC, plan=rel, owner_session=self.SESSION)
        return payload

    def remote_execution(self, resources=('gpu-1',), *, deployment='dep-a'):
        """一份形状合法的受管远端声明（正式资源键只写在 `execution.remote.resources`）。"""
        block = {'unit_root': '/srv/units/plan',
                 'code_root': '/srv/units/plan/code',
                 'environment_root': '/srv/units/plan/env',
                 'input_root': '/srv/units/plan/input',
                 'output_root': '/srv/units/plan/output',
                 'task_ids': ['t1'], 'required_artifacts': ['results.json'],
                 'code_fingerprint': 'a' * 64, 'task_fingerprint': 'b' * 64,
                 'environment_fingerprint': 'c' * 64}
        if resources is not None:
            block['resources'] = list(resources)
        return {'scope': 'remote', 'deployment_id': deployment, 'remote': block}

    def scheduler_tables(self):
        """用真实 `Scheduler` 建出 `resources` / quarantine 表（生产类，不是手抄 DDL）。"""
        from kernel.autoresearch.runtime.scheduler import Scheduler
        Scheduler(self.store)
        return self.store

    def checklist(self, plan_id):
        return checklist(self.ws, topic=self.TOPIC, exp_id=plan_id,
                         owner_session=self.SESSION)

    # -- workflow.audit -----------------------------------------------------

    def test_missing_current_audit_blocks(self):
        self.freeze('exp-01')
        result = self.checklist('exp-01')
        item = items_of(result)['workflow.audit']
        self.assertEqual(item['status'], 'blocked')
        self.assertTrue(item['required'])
        self.assertFalse(item['evidence']['historical'])
        self.assertIn('workflow.audit', result['blocked'])
        self.assertFalse(result['ok'], '缺当前独立审计 ⛔ 不能给总 ok')

    def test_current_audit_ok_after_independent_audit(self):
        self.ready('exp-01')
        result = self.checklist('exp-01')
        item = items_of(result)['workflow.audit']
        self.assertEqual(item['status'], 'ok', item)
        self.assertFalse(item['evidence']['historical'])
        self.assertTrue(item['evidence']['decision'])

    def test_started_round_shows_historical_audit_not_a_new_launch(self):
        built, launched = self.finished_run('exp-01')
        self.assertTrue(launched['ok'])
        result = self.checklist('exp-01')
        item = items_of(result)['workflow.audit']
        self.assertEqual(item['status'], 'ok', item)
        self.assertTrue(item['evidence']['historical'],
                        '已启动轮次只展示历史审计绑定')
        self.assertEqual(item['evidence']['run_id'], launched['run_id'])
        self.assertTrue(item['evidence']['audit']['ok'])
        self.assertIn('历史', ' '.join(item['notes']))
        self.assertNotIn('未启动', ' '.join(item['notes']),
                         '⛔ 不能对已启动轮次强求新的起跑条件')

    # -- budget.reservation -------------------------------------------------

    def test_missing_budget_ledger_is_unknown_and_not_green(self):
        self.freeze('exp-01')
        result = self.checklist('exp-01')
        item = items_of(result)['budget.reservation']
        self.assertEqual(item['status'], 'unknown', item)
        self.assertTrue(item['required'])
        self.assertFalse(result['ok'], '缺预算台账 ⛔ 不能给总 ok')
        self.assertIn('budget.reservation', result['required_missing'])

    def test_no_reservation_row_blocks_a_not_started_round(self):
        self.freeze('exp-01')
        # 预算台账存在（另一个实验批过额度），但本实验没有任何预留
        self.grant_and_reserve(exp='exp-99', reservation='reservation-other')
        result = self.checklist('exp-01')
        item = items_of(result)['budget.reservation']
        self.assertEqual(item['status'], 'blocked', item)
        self.assertTrue(item['required'])
        self.assertIn('预算账号或预留不存在', ' '.join(item['problems']))
        self.assertFalse(result['ok'])
        self.assertIn('budget.reservation', result['blocked'])

    def test_wrong_frozen_reservation_is_blocked_even_when_another_is_live(self):
        self.freeze('exp-01', reservation='missing-reservation')
        self.grant_and_reserve()
        item = items_of(self.checklist('exp-01'))['budget.reservation']
        self.assertEqual(item['status'], 'blocked', item)
        self.assertEqual(item['evidence']['gate'], 'admission.Admission.check_budget')

    def test_wrong_frozen_series_is_blocked_even_when_another_is_live(self):
        self.freeze('exp-01', series='missing-series')
        self.grant_and_reserve()
        item = items_of(self.checklist('exp-01'))['budget.reservation']
        self.assertEqual(item['status'], 'blocked', item)

    def test_wrong_frozen_scope_is_blocked_even_when_a_small_reservation_is_live(self):
        self.freeze('exp-01', scope='large')
        self.grant_and_reserve()
        item = items_of(self.checklist('exp-01'))['budget.reservation']
        self.assertEqual(item['status'], 'blocked', item)
        self.assertIn('作用域', ' '.join(item['problems']))

    def test_frozen_cost_exceeding_live_reservation_is_blocked(self):
        self.freeze('exp-01', budget={'max_cost_units': 4, 'max_seconds': 60})
        self.grant_and_reserve(units=3)
        item = items_of(self.checklist('exp-01'))['budget.reservation']
        self.assertEqual(item['status'], 'blocked', item)
        self.assertIn('最大成本', ' '.join(item['problems']))

    def test_checklist_now_reaches_the_real_budget_expiry_check(self):
        self.freeze('exp-01')
        self.grant_and_reserve(expires_in=60)
        result = checklist(self.ws, topic=self.TOPIC, exp_id='exp-01',
                           owner_session=self.SESSION, now=time.time() + 120)
        item = items_of(result)['budget.reservation']
        self.assertEqual(item['status'], 'blocked', item)
        self.assertIn('过期', ' '.join(item['problems']))

    def test_live_reservation_is_ok_for_a_not_started_round(self):
        self.freeze('exp-01')
        self.grant_and_reserve()
        result = self.checklist('exp-01')
        item = items_of(result)['budget.reservation']
        self.assertEqual(item['status'], 'ok', item)
        self.assertFalse(item['evidence']['started'])
        self.assertEqual(item['evidence']['reservations'][0]['status'], 'reserved')

    def test_finished_round_with_reserved_units_is_unknown_diagnostic(self):
        self.freeze('exp-01')
        self.grant_and_reserve()
        built, launched = self.finished_run('exp-01')
        self.assertTrue(launched['ok'])
        result = self.checklist('exp-01')
        item = items_of(result)['budget.reservation']
        self.assertEqual(item['status'], 'unknown', item)
        self.assertFalse(item['required'])
        self.assertIn('reserved', ' '.join(item['problems']))
        self.assertIn('budget.reservation', result['unknown'])
        self.assertNotIn('budget.reservation', result['required_missing'])

    def test_settled_budget_counts_as_historical_completion(self):
        self.freeze('exp-01')
        accounts = self.grant_and_reserve()
        built, launched = self.finished_run('exp-01')
        self.assertTrue(launched['ok'])
        accounts.settle('reservation-small', 3, 'settle-request-1', 'agent')
        result = self.checklist('exp-01')
        item = items_of(result)['budget.reservation']
        self.assertEqual(item['status'], 'ok', item)
        self.assertEqual(item['evidence']['reservations'][0]['status'], 'settled')
        self.assertTrue(any('历史完成' in note for note in item['notes']))

    # -- remote.lease -------------------------------------------------------

    def test_local_scope_needs_no_remote_lease(self):
        self.freeze('exp-01', execution={'scope': 'local'})
        result = self.checklist('exp-01')
        item = items_of(result)['remote.lease']
        self.assertEqual(item['status'], 'ok')
        self.assertFalse(item['applicable'], 'local 范围明确不适用')
        self.assertFalse(item['required'])
        self.assertIn('不需要远端资源租约', ' '.join(item['notes']))
        self.assertNotIn('remote.lease', result['blocked'])

    def test_remote_scope_without_declared_resource_keys_is_unknown(self):
        self.freeze('exp-01', execution=self.remote_execution(resources=None))
        result = self.checklist('exp-01')
        item = items_of(result)['remote.lease']
        self.assertEqual(item['status'], 'unknown', item)
        self.assertTrue(item['required'])
        self.assertEqual(item['evidence']['stage'], 'not_started')
        self.assertIn('execution.remote.resources', ' '.join(item['problems']))
        self.assertFalse(result['ok'])
        self.assertIn('remote.lease', result['required_missing'])

    def test_remote_not_started_with_free_resources_is_ok_not_unknown(self):
        """未启动计划**还没有** claim 租约；声明的资源键本地可核空闲 ⇒ ok（不得挡死可执行计划）。"""
        self.scheduler_tables()
        self.freeze('exp-01', execution=self.remote_execution(['gpu-1', 'gpu-2']))
        result = self.checklist('exp-01')
        item = items_of(result)['remote.lease']
        self.assertEqual(item['status'], 'ok', item)
        self.assertTrue(item['required'])
        self.assertEqual(item['evidence']['stage'], 'not_started')
        self.assertEqual(item['evidence']['declared_resources'], ['gpu-1', 'gpu-2'])
        self.assertEqual(item['evidence']['lease_view']['status'], 'free')
        self.assertIn('空闲', ' '.join(item['notes']))
        self.assertNotIn('remote.lease', result['required_missing'])
        self.assertNotIn('remote.lease', result['blocked'])

    def test_remote_not_started_with_a_held_resource_is_blocked(self):
        self.scheduler_tables()
        self.freeze('exp-01', execution=self.remote_execution(['gpu-1']))
        # 夹具：另一个任务当前持有 gpu-1（这里是读侧检查，直接落一行资源租约）
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute('INSERT INTO resources VALUES(?,?,?,?,?,?)',
                         ('gpu-1', 'TASK-other', 'exp-99', 1, 'token-other',
                          time.time() + 600))
            conn.commit()
        finally:
            conn.close()
        result = self.checklist('exp-01')
        item = items_of(result)['remote.lease']
        self.assertEqual(item['status'], 'blocked', item)
        self.assertEqual(item['evidence']['lease_view']['status'], 'held')
        self.assertIn('被其他任务持有', ' '.join(item['problems']))
        self.assertIn('remote.lease', result['blocked'])
        self.assertFalse(result['ok'])

    def test_remote_not_started_with_a_quarantined_resource_is_unknown(self):
        self.scheduler_tables()
        self.freeze('exp-01', execution=self.remote_execution(['gpu-1']))
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute('INSERT INTO scheduler_resource_quarantine VALUES(?,?,?)',
                         ('gpu-1', '上一个任务结束未经核空闲', time.time()))
            conn.commit()
        finally:
            conn.close()
        result = self.checklist('exp-01')
        item = items_of(result)['remote.lease']
        self.assertEqual(item['status'], 'unknown', item)
        self.assertEqual(item['evidence']['lease_view']['status'], 'quarantined')
        self.assertIn('quarantine', ' '.join(item['problems']))
        self.assertIn('remote.lease', result['required_missing'])
        self.assertFalse(result['ok'])

    def test_remote_not_started_without_lease_schema_is_unknown(self):
        self.freeze('exp-01', execution=self.remote_execution(['gpu-1']))
        result = self.checklist('exp-01')
        item = items_of(result)['remote.lease']
        self.assertEqual(item['status'], 'unknown', item)
        self.assertIn('缺表', ' '.join(item['problems']),
                      '⛔ 不能把「表都没有」读成资源空闲')
        self.assertFalse(result['ok'])
        self.assertIn('remote.lease', result['required_missing'])


class InjectionTests(AssuranceCase):
    def test_injected_monitoring_is_used_and_recorded(self):
        ws, _store = workspace_with_state(self, 'injected')
        before = tree_snapshot(ws)

        class FakeMonitoring:
            def __init__(self):
                self.calls = []

            def status(self, owner, *, exp=''):
                self.calls.append(('status', owner, exp))
                return {'ok': True, 'undecidable': False, 'state': 'active',
                        'registered': True, 'intent_id': 'I-1', 'pending': [],
                        'unassessed_user_updates': [], 'report_id': 'R-1',
                        'report_stale': [], 'issues': {}, 'problems': [],
                        'sync_watermark': 2, 'assessed_watermark': 2}

            def check_start(self, owner, exp, *, action='launch'):
                self.calls.append(('check_start', owner, exp))
                return {'ok': True, 'allow': True, 'undecidable': False, 'state': 'active',
                        'exempt': False, 'problems': [], 'note': '注入的判词'}

        fake = FakeMonitoring()
        result = checklist(ws, exp_id=EXP, owner_session=OWNER,
                           services={'monitoring': fake})
        items = items_of(result)
        self.assertEqual(result['sources']['monitoring'], 'injected')
        self.assertEqual(items['monitoring.intent']['status'], 'ok')
        self.assertEqual(items['monitoring.launch']['status'], 'ok')
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(result['sources']['workflow'], 'readonly')
        self.assertEqual(tree_snapshot(ws), before)

    def test_unknown_service_key_is_refused(self):
        ws, _store = workspace_with_state(self, 'bad')
        with self.assertRaises(AssuranceError):
            checklist(ws, exp_id=EXP, services={'teapot': object()})

    def test_missing_workspace_is_refused(self):
        with self.assertRaises(AssuranceError):
            checklist(self.root / 'nope')


if __name__ == '__main__':
    unittest.main()
