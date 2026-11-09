# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09 16:05 +08:00
# PURPOSE: Validate actual shared reservations at workflow launch, trusted ordering fencing and the public budget CLI boundaries.
# ORIGIN: none
# CUMULATIVE-DELTA: 2026-11-09 补可信时序 fencing 正反例：迟到旧 renew 不得在较新 revoke 后复活预算；真实更晚的 renew 可用；同 timestamp 同会话按 seq 判、跨会话不可判即拒；旧事件重放只回历史且 Admission.check 不被误导；无时序接口/时序不可判 fail-closed；传统 HMAC 来源仍按到达顺序。补实验登记事务化：时序拒绝且新 exp_id 不存在时 DB 行与文件都不新增；有效 grant 在事务内真实初始化实验；默认 API 仍要求实验存在；实验登记后续失败整体回滚。此前：合成 DSHHost 日志 + CLI patch 注入覆盖 grant/status/reserve/release/settle 全分支；wf_runs 未终态挡结算；事件/proof 重复键与异常库 exit 2 无 traceback。
import contextlib
import copy
import hashlib
import hmac
import io
import json
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from kernel.autoresearch import budget_cli
from kernel.autoresearch.admission import Admission, HostBudgetSource
from kernel.autoresearch.host_evidence import DSHHost
from kernel.autoresearch.runtime.accounts import (Accounts, ApprovalError, ApprovalEvidence,
                                                  BudgetError, HMACSource, SourcePolicy)
from kernel.autoresearch.runtime.store import Store, canonical_json


def table_names(db_path):
    with closing(sqlite3.connect(str(db_path))) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def table_rows(db_path, table):
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM ' + table)]


def make_host(host_root, workspace, rows):
    """真实 `DSHHost` + 注入解码器；不碰真实 ~/.dsh，也不发网络请求。"""
    for name in rows:
        directory = Path(host_root) / 'sessions/project' / ('session-' + name)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'session.v4.jsonl.zstd').write_bytes(b'injected-decoder-fixture')
    return DSHHost(host_root, workspace=workspace,
                   decoder=lambda path: copy.deepcopy(rows[path.parent.name.removeprefix('session-')]))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.autoresearch').mkdir()
        self.store = Store(self.root / '.autoresearch/runtime.sqlite3')
        self.store.ensure_experiment('P1')
        self.store.ensure_experiment('P2')
        self.key = b'explicit-synthetic-fixture-key-000000000'
        self.accounts = Accounts(self.store, ApprovalEvidence({'fixture': SourcePolicy(HMACSource(self.key), frozenset({'fixture-user'}))}))
        self.plan = {'id': 'P1', 'budget': {'max_cost_units': 3},
                     'authorization': {'scope': 'small', 'series_id': 'series-small', 'reservation_id': 'reservation-small'}}
        self.grant('small')

    def grant(self, scope):
        event = {'event_id': 'grant-' + scope, 'series_id': 'series-' + scope, 'exp_id': 'P1',
                 'scope': scope, 'action': 'grant', 'expires_at': time.time() + 3600,
                 'total_units': 10, 'principal': 'fixture-user'}
        event['signature'] = hmac.new(self.key, canonical_json(event).encode(), hashlib.sha256).hexdigest()
        self.accounts.authorize('fixture', event, 'grant-request-' + scope)
        self.accounts.reserve('series-' + scope, 'P1', scope, 3, 'reservation-' + scope)

    def test_actual_reservation_admits_without_writes(self):
        before = self.store.db_path
        path = Path(before)
        original = path.read_bytes(), path.stat().st_mtime_ns
        result = Admission(self.root).check(self.plan, 'owner', 'P1')
        self.assertTrue(result['ok'], result)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)

    def test_check_is_readable_inside_an_open_write_transaction(self):
        # Workflow 在 BEGIN IMMEDIATE 里再核一次；Admission 必须只读且不等待写锁。
        with self.store.transaction():
            result = Admission(self.root).check(self.plan, 'owner', 'P1')
            self.assertTrue(result['ok'], result)

    def test_wrong_experiment_scope_amount_and_released_reservation_refused(self):
        admission = Admission(self.root)
        self.assertFalse(admission.check(self.plan, 'owner', 'P2')['ok'])
        self.plan['budget']['max_cost_units'] = 4
        self.assertFalse(admission.check(self.plan, 'owner', 'P1')['ok'])
        self.plan['budget']['max_cost_units'] = 3
        self.plan['authorization']['scope'] = 'large'
        self.assertFalse(admission.check(self.plan, 'owner', 'P1')['ok'])
        self.plan['authorization']['scope'] = 'small'
        self.accounts.release('reservation-small', 'release')
        self.assertFalse(admission.check(self.plan, 'owner', 'P1')['ok'])

    def test_expired_revoked_and_unknown_budget_fail_closed(self):
        expired = Admission(self.root, clock=lambda: time.time() + 7200)
        self.assertFalse(expired.check(self.plan, 'owner', 'P1')['ok'])
        with self.store.transaction() as conn:
            conn.execute('UPDATE budget_accounts SET active=0 WHERE series_id=?', ('series-small',))
        self.assertFalse(Admission(self.root).check(self.plan, 'owner', 'P1')['ok'])
        self.plan['authorization']['series_id'] = 'missing'
        self.assertFalse(Admission(self.root).check(self.plan, 'owner', 'P1')['ok'])

    def test_shared_budget_check_does_not_claim_large_approval(self):
        self.grant('large')
        self.plan['authorization'] = {'scope': 'large', 'series_id': 'series-large',
                                      'reservation_id': 'reservation-large', 'decision_id': 'D1'}
        admission = Admission(self.root)
        budget = admission.check_budget(self.plan, 'owner', 'P1')
        self.assertTrue(budget['ok'], budget)
        self.assertIsNone(budget['decision'])
        self.assertFalse(admission.check(self.plan, 'owner', 'P1')['ok'])
        self.plan['budget']['max_cost_units'] = 4
        self.assertFalse(admission.check_budget(self.plan, 'owner', 'P1')['ok'])

    def test_shared_budget_check_is_the_small_execution_gate(self):
        admission = Admission(self.root)
        self.assertEqual(admission.check_budget(self.plan, 'owner', 'P1'),
                         admission.check(self.plan, 'owner', 'P1'))
        for key, value in (('reservation_id', 'wrong'), ('series_id', 'wrong'), ('scope', 'large')):
            changed = copy.deepcopy(self.plan)
            changed['authorization'][key] = value
            with self.subTest(key=key):
                self.assertFalse(admission.check_budget(changed, 'owner', 'P1')['ok'])
        later = Admission(self.root, clock=lambda: time.time() + 7200)
        self.assertFalse(later.check_budget(self.plan, 'owner', 'P1')['ok'])

    def test_large_requires_actual_decision_adapter(self):
        self.grant('large')
        self.plan['authorization'] = {'scope': 'large', 'series_id': 'series-large', 'reservation_id': 'reservation-large', 'decision_id': 'D1'}
        self.assertFalse(Admission(self.root).check(self.plan, 'owner', 'P1')['ok'])
        captured = {}

        class FixtureDecisions:
            def check_authorization(inner, **kwargs):
                captured.update(kwargs)
                self.assertEqual(kwargs['exp_id'], 'P1')
                self.assertEqual(kwargs['plan']['authorization']['decision_id'], 'D1')
                return {'ok': True, 'source': 'explicit-test-fixture'}
        self.assertTrue(Admission(self.root, decisions=FixtureDecisions()).check(self.plan, 'owner', 'P1')['ok'])
        # Admission 不得替调用方给 decisions 加参数（默认 record=False 由接口自己给）。
        self.assertEqual(set(captured), {'plan', 'owner_session', 'exp_id'})

        class Broken:
            def check_authorization(inner, **kwargs):
                raise OSError('unavailable')
        self.assertFalse(Admission(self.root, decisions=Broken()).check(self.plan, 'owner', 'P1')['ok'])

    def test_absent_policy_or_owner_is_not_permission(self):
        for plan in ({}, {'authorization': {'scope': 'small'}}, self.plan):
            self.assertFalse(Admission(self.root).check(plan, '', 'P1')['ok'])
        self.assertFalse(Admission(self.root).check({}, 'owner', 'P1')['ok'])

    def test_missing_database_or_tables_fail_closed_without_writes(self):
        missing = self.root / 'missing'
        missing.mkdir()
        result = Admission(missing).check(self.plan, 'owner', 'P1')
        self.assertFalse(result['ok'])
        self.assertFalse((missing / '.autoresearch/runtime.sqlite3').exists())
        empty = self.root / 'empty'
        (empty / '.autoresearch').mkdir(parents=True)
        db = empty / '.autoresearch/runtime.sqlite3'
        db.write_bytes(b'')
        result = Admission(empty).check(self.plan, 'owner', 'P1')
        self.assertFalse(result['ok'])
        self.assertEqual(db.read_bytes(), b'')

    def test_bad_plan_budget_is_a_problem_not_an_exception(self):
        for budget in ([], 'x', {}, {'max_cost_units': True}, {'max_cost_units': -1}):
            with self.subTest(budget=budget):
                result = Admission(self.root).check(dict(self.plan, budget=budget), 'owner', 'P1')
                self.assertFalse(result['ok'])
        self.assertFalse(Admission(self.root).check(self.plan, 'owner', None)['ok'])


class HostBudgetSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        self.now = time.time()
        self.command = {'action': 'grant', 'event_id': 'E', 'series_id': 'S', 'scope': 'small',
                        'exp_id': 'P1', 'expires_at': self.now + 3600, 'total_units': 3}
        self.rows = {'owner': [
            {'type': 'session', 'id': 'session-owner', 'createdAt': self.now - 10, 'delegationDepth': 0},
            {'type': 'user/message', 'seq': 1, 'time': self.now - 5,
             'data': {'source': {'kind': 'user', 'rpcId': 'client-1'},
                      'content': json.dumps(self.command)}}]}
        self.host = make_host(self.base / 'dsh', self.workspace, self.rows)

    def event(self, **changes):
        event = dict(self.command)
        event['proof'] = {'session_id': 'owner', 'seq': 1}
        event.update(changes)
        return event

    def source(self, principals=('session-owner',)):
        return HostBudgetSource(self.host, set(principals))

    def verdict(self, expected, **changes):
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False,
                                           allow_nan=False).encode('utf-8')).hexdigest()
        value = {'verified': True, 'kind': 'user_command', 'session_id': 'session-owner',
                 'seq': 1, 'rpc_id': 'client-1', 'created_at': self.now - 5,
                 'command': expected, 'command_sha256': digest, 'source': 'injected'}
        value.update(changes)
        return value

    def test_real_host_message_and_exact_command_are_required(self):
        self.assertEqual(self.source()(self.event()), 'session-owner')
        self.rows['owner'][1]['data']['content'] = json.dumps(dict(self.command, total_units=2))
        with self.assertRaises(ValueError):
            self.source()(self.event())
        self.rows['owner'][1]['data']['content'] = json.dumps(self.command) + '\n' + json.dumps(self.command)
        with self.assertRaises(ValueError):
            self.source()(self.event())
        body = json.dumps(self.command)
        self.rows['owner'][1]['data']['content'] = '{"action":"grant",' + body[1:]
        with self.assertRaises(ValueError):
            self.source()(self.event())

    def test_proof_must_be_exact_two_keys_and_event_has_no_extra_fields(self):
        for proof in ({'session_id': 'owner', 'seq': 1, 'log_path': 'x'},
                      {'session_id': 'owner'}, {'session_id': 'owner', 'seq': True},
                      {'session_id': 'owner', 'seq': -1}, {'session_id': 'owner', 'seq': '1'},
                      {'session_id': '../owner', 'seq': 1}, 'owner', []):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                self.source()(self.event(proof=proof))
        for changes in ({'expected': {}}, {'action': 'approve'}, {'scope': 'huge'},
                        {'total_units': True}, {'expires_at': 'soon'}, {'event_id': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.source()(self.event(**changes))

    def test_caller_principal_must_be_allowed_and_normalized(self):
        self.assertEqual(self.source(('owner',))(self.event()), 'session-owner')
        with self.assertRaises(ValueError):
            self.source(('session-other',))(self.event())
        for value in ('session-owner', [], ['bad path']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HostBudgetSource(self.host, value)

    def test_created_at_future_refused_but_old_message_not_given_arbitrary_age_limit(self):
        self.rows['owner'][1]['time'] = time.time() + 3600
        with self.assertRaises(ValueError):
            self.source()(self.event())
        self.rows['owner'][1]['time'] = 1001
        self.assertEqual(self.source()(self.event()), 'session-owner')

    def test_forged_verdict_provenance_is_refused(self):
        expected = {key: value for key, value in self.event().items() if key != 'proof'}

        class FixtureHost:
            evidence_source = 'injected'

            def __init__(inner, verdict):
                inner.verdict = verdict

            def verify(inner, kind, payload):
                return inner.verdict

        changes = ({'session_id': 'session-other'}, {'source': 'other'}, {'source': None},
                   {'seq': 2}, {'rpc_id': ''}, {'kind': 'scribe'},
                   {'command_sha256': '0' * 64},
                   {'command': dict(expected, total_units=9)},
                   {'created_at': time.time() + 3600}, {'verified': False})
        for change in changes:
            with self.subTest(change=change):
                host = FixtureHost(self.verdict(expected, **change))
                with self.assertRaises(ValueError):
                    HostBudgetSource(host, {'session-owner'})(self.event())

    def test_trusted_ordering_comes_only_from_verified_host_evidence(self):
        source = self.source()
        self.assertEqual(source(self.event()), 'session-owner')
        self.assertEqual(source.trusted_ordering(self.event()),
                         {'session': 'session-owner', 'seq': 1, 'host_time': self.now - 5,
                          'rpc_id': 'client-1', 'source': 'injected'})
        # 调用方往事件里塞自报时间或序号：字段集合被拒，不能当年时序。
        for extra in ({'host_time': self.now - 1}, {'created_at': self.now - 1}, {'seq': 2}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                source(self.event(**extra))
        # 换一条事件：不能复用上一事件的时序，必须重新核宿主证据。
        with self.assertRaises(ValueError):
            source.trusted_ordering(self.event(total_units=9))


class BudgetCliCase(unittest.TestCase):
    """grant/status/reserve/release/settle 的 CLI 分支；真实 `DSHHost` 只用合成日志。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ws = self.base / 'workspace'
        self.ws.mkdir()
        (self.ws / '.autoresearch').mkdir()
        self.db_path = self.ws / '.autoresearch/runtime.sqlite3'
        self.store = Store(self.db_path)
        self.now = time.time()
        self.command = {'action': 'grant', 'event_id': 'grant-small', 'series_id': 'series-small',
                        'scope': 'small', 'exp_id': 'P1', 'expires_at': self.now + 3600,
                        'total_units': 10}
        self.rows = {'owner': [
            {'type': 'session', 'id': 'session-owner', 'createdAt': self.now - 10, 'delegationDepth': 0},
            {'type': 'user/message', 'seq': 1, 'time': self.now - 5,
             'data': {'source': {'kind': 'user', 'rpcId': 'client-1'},
                      'content': json.dumps(self.command)}}]}
        self.host = make_host(self.base / 'dsh', self.ws, self.rows)
        self.host_calls = []

        def factory(**kwargs):
            self.host_calls.append(kwargs)
            return self.host

        self.host_factory = factory
        self.proof = json.dumps({'session_id': 'owner', 'seq': 1})
        self.next_seq = 1
        self.write_event(self.command)

    # -- 合成宿主 / CLI 执行 -------------------------------------------------
    def write_event(self, event, name='grant.json', text=None):
        path = self.ws / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text is not None else json.dumps(event), encoding='utf-8')

    def set_message(self, value):
        content = value if isinstance(value, str) else json.dumps(value)
        message = self.rows['owner'][1]
        if content != message['data']['content']:
            # 真实宿主日志里一条新消息就是一个新序号；同一条消息重放才复用旧序号。
            self.next_seq += 1
            message['seq'] = self.next_seq
            message['data']['content'] = content
            self.proof = json.dumps({'session_id': 'owner', 'seq': self.next_seq})

    def cli(self, argv):
        out = io.StringIO()
        with patch.object(budget_cli, 'DSHHost', self.host_factory):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = budget_cli.main(argv)
        return code, json.loads(out.getvalue())

    def grant_args(self, request_key='grant-rk', proof=None, event_file='grant.json'):
        return ['grant', '--workspace', str(self.ws), '--event-file', event_file,
                '--proof-json', self.proof if proof is None else proof,
                '--request-key', request_key]

    def reserve_args(self, request_key='res-rk', units=3, series='series-small',
                     exp='P1', scope='small'):
        return ['reserve', '--workspace', str(self.ws), '--series-id', series,
                '--exp-id', exp, '--scope', scope, '--units', str(units),
                '--request-key', request_key]

    def status_args(self, series='series-small', reservation=None):
        argv = ['status', '--workspace', str(self.ws), '--series-id', series]
        if reservation is not None:
            argv += ['--reservation-id', reservation]
        return argv

    def release_args(self, reservation='res-rk', request_key='release-rk', series=None):
        argv = ['release', '--workspace', str(self.ws), '--reservation-id', reservation,
                '--request-key', request_key]
        if series is not None:
            argv += ['--series-id', series]
        return argv

    def settle_args(self, reservation='res-rk', series='series-small', actual=2,
                    evidence='evidence/cost.md', locator='L1', request_key='settle-rk'):
        return ['settle', '--workspace', str(self.ws), '--reservation-id', reservation,
                '--series-id', series, '--actual-units', str(actual),
                '--evidence-file', evidence, '--locator', locator,
                '--request-key', request_key]

    def issue_grant(self, request_key='grant-rk', event=None, message=None):
        event = dict(self.command) if event is None else event
        self.write_event(event)
        self.set_message(event if message is None else message)
        return self.cli(self.grant_args(request_key=request_key))

    def reserve_ready(self):
        self.assertEqual(self.issue_grant()[0], 0)
        self.assertEqual(self.cli(self.reserve_args())[0], 0)

    def snapshot(self):
        return self.db_path.read_bytes(), self.db_path.stat().st_mtime_ns

    def rows_of(self, table):
        return table_rows(self.db_path, table)

    def tables(self):
        return table_names(self.db_path)

    def assert_no_grant_state(self, exp='P1'):
        names = self.tables()
        if 'budget_accounts' in names:
            self.assertEqual(self.rows_of('budget_accounts'), [])
        if 'approval_events' in names:
            self.assertEqual(self.rows_of('approval_events'), [])
        if 'experiments' in names:
            self.assertIsNone(self.store.get_experiment(exp))

    def write_evidence(self, text='measured cost units: 2\nnote: synthetic\n', name='evidence/cost.md'):
        path = self.ws / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # 写字节，避免 Windows 文本模式把 \n 换成 \r\n 而让哈希对不上。
        path.write_bytes(text.encode('utf-8'))

    def add_workflow(self, finished=False, run_id='RUN-1', plan_id='P1'):
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS wf_runs(run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS wf_finishes(run_id TEXT PRIMARY KEY REFERENCES wf_runs(run_id))')
            conn.execute('INSERT INTO wf_runs VALUES(?,?)', (run_id, plan_id))
            if finished:
                conn.execute('INSERT INTO wf_finishes VALUES(?)', (run_id,))
            conn.commit()

    def finish_workflow(self, run_id='RUN-1'):
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute('INSERT INTO wf_finishes VALUES(?)', (run_id,))
            conn.commit()

    # -- grant ---------------------------------------------------------------
    def test_grant_verifies_real_message_then_registers_experiment(self):
        before = self.snapshot()
        code, out = self.cli(self.grant_args())
        self.assertEqual(code, 0, out)
        self.assertEqual(out['series_id'], 'series-small')
        self.assertEqual(out['total_units'], 10)
        self.assertEqual(out['active'], 1)
        self.assertEqual(len(self.rows_of('budget_accounts')), 1)
        self.assertEqual(len(self.rows_of('approval_events')), 1)
        self.assertEqual(self.store.get_experiment('P1')['id'], 'P1')
        self.assertTrue(any(row['kind'] == 'approval_grant' for row in self.rows_of('events')))
        self.assertNotEqual(before, self.snapshot())
        # 部署入口只给工作区；proof 不能自选日志根。
        self.assertEqual(self.host_calls, [{'workspace': self.ws.resolve()}])

    def test_grant_replay_is_idempotent(self):
        first = self.cli(self.grant_args())
        self.assertEqual(first[0], 0, first[1])
        second = self.cli(self.grant_args())
        self.assertEqual(second, first)
        self.assertEqual(len(self.rows_of('approval_events')), 1)
        third = self.cli(self.grant_args(request_key='grant-other-key'))
        self.assertEqual(third[1], first[1])
        self.assertEqual(len(self.rows_of('approval_events')), 1)
        # 同 request_key 不同 payload：冲突，不产生第二个账号。
        changed = dict(self.command, total_units=11)
        self.write_event(changed)
        self.set_message(changed)
        code, out = self.cli(self.grant_args())
        self.assertEqual(code, 2, out)
        self.assertIn('different payload', out['error'])
        # 同 event_id 不同 payload：事件冲突。
        code, out = self.cli(self.grant_args(request_key='grant-third-key'))
        self.assertEqual(code, 2, out)
        self.assertIn('different payload', out['error'])
        self.assertEqual(self.rows_of('budget_accounts')[0]['total_units'], 10)

    def test_grant_rejects_duplicate_event_file_keys_before_any_write(self):
        before = self.snapshot()
        body = json.dumps(self.command)
        self.write_event(None, text='{"action":"grant",' + body[1:])
        code, out = self.cli(self.grant_args())
        self.assertEqual(code, 2, out)
        self.assertIn('重复键', out['error'])
        self.assertEqual(before, self.snapshot())
        self.assert_no_grant_state()
        # proof 的重复键同样拒绝。
        self.assertEqual(self.cli(self.grant_args(proof='{"session_id":"owner","seq":1,"seq":2}'))[0], 2)
        self.assert_no_grant_state()

    def test_grant_rejects_wrong_fields_and_proof_shapes(self):
        bad_events = [
            dict(self.command, expected={'action': 'grant'}),
            {'event_id': 'e', 'series_id': 's', 'scope': 'small', 'exp_id': 'P1',
             'expires_at': self.now + 60},  # 缺 action/total_units
            dict(self.command, scope='huge'),
            dict(self.command, action='approve'),
            dict(self.command, expires_at=self.now - 1),
            dict(self.command, total_units=True),
            dict(self.command, event_id=''),
        ]
        for index, event in enumerate(bad_events):
            with self.subTest(event=event):
                self.write_event(event)
                self.set_message(self.command)
                code, out = self.cli(self.grant_args(request_key='bad-event-' + str(index)))
                self.assertEqual(code, 2, out)
                self.assert_no_grant_state()
        self.write_event(self.command)
        bad_proofs = ['{"session_id":"owner","seq":1,"log_path":"x"}',
                      '{"session_id":"owner"}', '[]',
                      '{"session_id":"owner","seq":true}',
                      '{"session_id":"owner","seq":-1}',
                      '{"session_id":"owner","seq":"1"}',
                      '{"session_id":"../owner","seq":1}']
        for index, proof in enumerate(bad_proofs):
            with self.subTest(proof=proof):
                code, out = self.cli(self.grant_args(request_key='bad-proof-' + str(index), proof=proof))
                self.assertEqual(code, 2, out)
                self.assert_no_grant_state()

    def test_grant_requires_one_exact_user_command(self):
        variants = [dict(self.command, total_units=9),
                    json.dumps(self.command) + '\n' + json.dumps(self.command)]
        for message in variants:
            with self.subTest(message=message):
                self.write_event(self.command)
                self.set_message(message)
                code, out = self.cli(self.grant_args(request_key='mismatch'))
                self.assertEqual(code, 2, out)
                self.assert_no_grant_state()
        body = json.dumps(self.command)
        self.write_event(self.command)
        self.set_message('{"action":"grant",' + body[1:])
        self.assertEqual(self.cli(self.grant_args(request_key='dup-message'))[0], 2)
        self.assert_no_grant_state()
        # 消息里的命令多一个键也不算逐键相等。
        self.set_message(dict(self.command, extra='x'))
        self.assertEqual(self.cli(self.grant_args(request_key='extra-message'))[0], 2)
        self.assert_no_grant_state()
        # proof 指向不存在的消息序号。
        self.set_message(self.command)
        code, out = self.cli(self.grant_args(request_key='missing-seq',
                                             proof='{"session_id":"owner","seq":2}'))
        self.assertEqual(code, 2, out)
        self.assert_no_grant_state()

    def test_grant_on_corrupt_database_exits_2_without_traceback(self):
        corrupt = self.base / 'corrupt-grant'
        (corrupt / '.autoresearch').mkdir(parents=True)
        bad = corrupt / '.autoresearch/runtime.sqlite3'
        bad.write_bytes(b'not a sqlite database')
        (corrupt / 'grant.json').write_text(json.dumps(self.command), encoding='utf-8')
        code, out = self.cli(['grant', '--workspace', str(corrupt), '--event-file', 'grant.json',
                              '--proof-json', self.proof, '--request-key', 'x'])
        self.assertEqual(code, 2, out)
        self.assertEqual(bad.read_bytes(), b'not a sqlite database')

    def test_grant_rejects_absent_event_file_or_database(self):
        code, out = self.cli(self.grant_args(event_file='../outside.json'))
        self.assertEqual(code, 2, out)
        code, out = self.cli(['grant', '--workspace', str(self.base / 'no-workspace'),
                              '--event-file', 'grant.json', '--proof-json', self.proof,
                              '--request-key', 'x'])
        self.assertEqual(code, 2, out)
        other = self.base / 'no-db'
        other.mkdir()
        code, out = self.cli(['grant', '--workspace', str(other), '--event-file', 'grant.json',
                              '--proof-json', self.proof, '--request-key', 'x'])
        self.assertEqual(code, 2, out)
        self.assertIn('不存在', out['error'])

    def test_revoke_requires_prior_grant_and_blocks_reserve(self):
        revoke = dict(self.command, action='revoke', event_id='revoke-small')
        # 还没有任何预算表时：先 exit 2，且不自动建表。
        self.write_event(revoke)
        self.set_message(revoke)
        code, out = self.cli(self.grant_args(request_key='revoke-before-grant'))
        self.assertEqual(code, 2, out)
        self.assertIn('budget_accounts', out['error'])
        self.assert_no_grant_state()
        # 表存在后，对没有 grant 的系列 revoke 仍被拒（grant 必须先于 revoke）。
        self.assertEqual(self.issue_grant()[0], 0)
        unknown = dict(revoke, series_id='series-other')
        self.write_event(unknown)
        self.set_message(unknown)
        code, out = self.cli(self.grant_args(request_key='revoke-unknown'))
        self.assertEqual(code, 2, out)
        self.assertIn('不存在', out['error'])
        self.assertEqual(self.rows_of('budget_accounts')[0]['active'], 1)
        # 先 grant，再 revoke。
        self.write_event(revoke)
        self.set_message(revoke)
        code, out = self.cli(self.grant_args(request_key='revoke-rk'))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows_of('budget_accounts')[0]['active'], 0)
        # 同 event_id 换 request_key 仍按 Accounts 事件回放，不追加第二条授权事件。
        approvals = len(self.rows_of('approval_events'))
        self.assertEqual(self.cli(self.grant_args(request_key='revoke-replay'))[0], 0)
        self.assertEqual(len(self.rows_of('approval_events')), approvals)
        code, out = self.cli(self.reserve_args())
        self.assertEqual(code, 2, out)

    def test_renew_and_duplicate_grant(self):
        self.assertEqual(self.issue_grant()[0], 0)
        renew = dict(self.command, action='renew', event_id='renew-small', total_units=20)
        self.write_event(renew)
        self.set_message(renew)
        code, out = self.cli(self.grant_args(request_key='renew-rk'))
        self.assertEqual(code, 0, out)
        self.assertEqual(out['total_units'], 20)
        duplicate = dict(self.command, event_id='grant-second')
        self.write_event(duplicate)
        self.set_message(duplicate)
        code, out = self.cli(self.grant_args(request_key='grant-second-rk'))
        self.assertEqual(code, 2, out)
        self.assertIn('renew or revoke', out['error'])

    # -- status --------------------------------------------------------------
    def test_status_is_read_only_and_missing_tables_exit_2(self):
        before = self.snapshot()
        code, out = self.cli(self.status_args())
        self.assertEqual(code, 2, out)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('budget_accounts', self.tables())
        self.assertEqual(self.issue_grant()[0], 0)
        self.assertEqual(self.cli(self.reserve_args())[0], 0)
        before = self.snapshot()
        code, out = self.cli(self.status_args(reservation='res-rk'))
        self.assertEqual(code, 0, out)
        self.assertEqual(out['account']['total_units'], 10)
        self.assertEqual(out['reservation']['id'], 'res-rk')
        self.assertEqual(before, self.snapshot())
        code, out = self.cli(self.status_args(series='missing'))
        self.assertEqual(code, 0, out)
        self.assertIsNone(out['account'])
        self.assertIsNone(out['reservation'])

    def test_status_on_empty_or_corrupt_database_exits_2(self):
        empty = self.base / 'empty-ws'
        (empty / '.autoresearch').mkdir(parents=True)
        db = empty / '.autoresearch/runtime.sqlite3'
        db.write_bytes(b'')
        code, out = self.cli(['status', '--workspace', str(empty), '--series-id', 's'])
        self.assertEqual(code, 2, out)
        self.assertEqual(db.read_bytes(), b'')
        corrupt = self.base / 'corrupt-ws'
        (corrupt / '.autoresearch').mkdir(parents=True)
        bad = corrupt / '.autoresearch/runtime.sqlite3'
        bad.write_bytes(b'not a sqlite database')
        code, out = self.cli(['status', '--workspace', str(corrupt), '--series-id', 's'])
        self.assertEqual(code, 2, out)
        self.assertEqual(bad.read_bytes(), b'not a sqlite database')

    def test_malformed_table_columns_exit_2_without_traceback(self):
        broken = self.base / 'broken-ws'
        (broken / '.autoresearch').mkdir(parents=True)
        db = broken / '.autoresearch/runtime.sqlite3'
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute('CREATE TABLE budget_accounts(series_id TEXT)')
            conn.commit()
        code, out = self.cli(['status', '--workspace', str(broken), '--series-id', 's'])
        self.assertEqual(code, 2, out)
        self.assertIn('缺列', out['error'])

    # -- reserve -------------------------------------------------------------
    def test_reserve_branches(self):
        self.assertEqual(self.issue_grant()[0], 0)
        code, out = self.cli(self.reserve_args())
        self.assertEqual(code, 0, out)
        self.assertEqual(out['id'], 'res-rk')
        self.assertEqual(out['units'], 3)
        self.assertEqual(out['status'], 'reserved')
        self.assertEqual(self.rows_of('budget_accounts')[0]['reserved_units'], 3)
        # 幂等回放。
        self.assertEqual(self.cli(self.reserve_args()), self.cli(self.reserve_args()))
        self.assertEqual(len(self.rows_of('budget_reservations')), 1)
        # 同 key 不同 payload。
        code, out = self.cli(self.reserve_args(units=4))
        self.assertEqual(code, 2, out)
        self.assertIn('different payload', out['error'])
        # 额度不足。
        code, out = self.cli(self.reserve_args(request_key='res-rk2', units=8))
        self.assertEqual(code, 2, out)
        self.assertIn('insufficient', out['error'])
        self.assertEqual(self.rows_of('budget_accounts')[0]['reserved_units'], 3)
        # 作用域不符。
        code, out = self.cli(self.reserve_args(request_key='res-rk3', scope='large'))
        self.assertEqual(code, 2, out)
        # 未知系列 / 未知实验 / 负额度 / 空 request-key。
        self.assertEqual(self.cli(self.reserve_args(request_key='res-rk4', series='nope'))[0], 2)
        self.assertEqual(self.cli(self.reserve_args(request_key='res-rk5', exp='P9'))[0], 2)
        self.assertEqual(self.cli(self.reserve_args(request_key='res-rk6', units=-1))[0], 2)
        self.assertEqual(self.cli(self.reserve_args(request_key=''))[0], 2)
        self.assertEqual(len(self.rows_of('budget_reservations')), 1)

    def test_reserve_fails_closed_on_missing_database_or_tables(self):
        other = self.base / 'no-budget'
        (other / '.autoresearch').mkdir(parents=True)
        store = Store(other / '.autoresearch/runtime.sqlite3')
        store.ensure_experiment('P1')
        code, out = self.cli(['reserve', '--workspace', str(other), '--series-id', 'series-small',
                              '--exp-id', 'P1', '--scope', 'small', '--units', '3',
                              '--request-key', 'r'])
        self.assertEqual(code, 2, out)
        self.assertIn('budget_accounts', out['error'])
        self.assertNotIn('budget_accounts', table_names(other / '.autoresearch/runtime.sqlite3'))
        code, out = self.cli(['reserve', '--workspace', str(self.base / 'missing-ws'),
                              '--series-id', 'series-small', '--exp-id', 'P1',
                              '--scope', 'small', '--units', '3', '--request-key', 'r'])
        self.assertEqual(code, 2, out)

    # -- release -------------------------------------------------------------
    def test_release_waits_for_terminal_workflow(self):
        self.reserve_ready()
        self.add_workflow(finished=False)
        code, out = self.cli(self.release_args())
        self.assertEqual(code, 2, out)
        self.assertIn('terminal', out['error'])
        self.assertEqual(self.rows_of('budget_reservations')[0]['status'], 'reserved')
        self.finish_workflow()
        code, out = self.cli(self.release_args())
        self.assertEqual(code, 0, out)
        self.assertEqual(out['status'], 'released')
        self.assertEqual(self.rows_of('budget_accounts')[0]['reserved_units'], 0)
        # 同 request_key 幂等回放；换 key 再释放被拒。
        self.assertEqual(self.cli(self.release_args())[0], 0)
        self.assertEqual(self.cli(self.release_args(request_key='release-again'))[0], 2)

    def test_release_rejects_wrong_series_missing_reservation_and_missing_tables(self):
        self.reserve_ready()
        self.assertEqual(self.cli(self.release_args(series='series-large'))[0], 2)
        self.assertEqual(self.cli(self.release_args(reservation='missing'))[0], 2)
        other = self.base / 'no-release'
        (other / '.autoresearch').mkdir(parents=True)
        Store(other / '.autoresearch/runtime.sqlite3')
        code, out = self.cli(['release', '--workspace', str(other), '--reservation-id', 'r',
                              '--request-key', 'k'])
        self.assertEqual(code, 2, out)
        self.assertNotIn('budget_reservations',
                         table_names(other / '.autoresearch/runtime.sqlite3'))

    # -- settle --------------------------------------------------------------
    def test_settle_is_pending_and_never_writes(self):
        self.reserve_ready()
        text = 'measured cost units: 2\nnote: synthetic\n'
        self.write_evidence(text)
        before = self.snapshot()
        code, out = self.cli(self.settle_args())
        self.assertEqual(code, 3, out)
        self.assertEqual(out['status'], 'pending_manual_reconciliation')
        self.assertEqual(out['cost_trust'], 'declared-unverified')
        self.assertFalse(out['settled'])
        self.assertEqual(out['declared']['actual_units'], 2)
        self.assertEqual(out['declared']['series_id'], 'series-small')
        self.assertEqual(out['declared']['evidence']['locator'], 'L1')
        self.assertEqual(out['declared']['evidence']['sha256'],
                         hashlib.sha256(text.encode('utf-8')).hexdigest())
        self.assertIn('Accounts.settle', out['python_api'])
        self.assertEqual(self.rows_of('budget_reservations')[0]['status'], 'reserved')
        self.assertEqual(before, self.snapshot())
        # 原文片段 locator 同样可用；重复申报仍是 pending，不写状态。
        self.assertEqual(self.cli(self.settle_args(locator='measured cost units: 2'))[0], 3)
        self.assertEqual(self.cli(self.settle_args())[0], 3)
        self.assertEqual(before, self.snapshot())

    def test_settle_rejects_bad_evidence_or_identity(self):
        self.reserve_ready()
        self.write_evidence()
        self.assertEqual(self.cli(self.settle_args(evidence='evidence/missing.md'))[0], 2)
        outside = self.base / 'outside.md'
        outside.write_text('outside', encoding='utf-8')
        self.assertEqual(self.cli(self.settle_args(evidence='../outside.md'))[0], 2)
        self.assertEqual(self.cli(self.settle_args(locator='L9'))[0], 2)
        self.assertEqual(self.cli(self.settle_args(locator='not present'))[0], 2)
        self.assertEqual(self.cli(self.settle_args(actual=4))[0], 2)
        self.assertEqual(self.cli(self.settle_args(series='series-large'))[0], 2)
        self.assertEqual(self.cli(self.settle_args(reservation='missing'))[0], 2)
        self.assertEqual(self.cli(self.settle_args(actual=-1))[0], 2)
        self.assertEqual(self.cli(self.settle_args(request_key=''))[0], 2)
        bad = self.ws / 'evidence/binary.bin'
        bad.write_bytes(b'\xff\xfe\x00')
        self.assertEqual(self.cli(self.settle_args(evidence='evidence/binary.bin'))[0], 2)
        self.assertEqual(self.rows_of('budget_reservations')[0]['status'], 'reserved')

    def test_settle_rejects_unfinished_workflow_then_pends_after_finish(self):
        self.reserve_ready()
        self.write_evidence()
        self.add_workflow(finished=False)
        before = self.snapshot()
        code, out = self.cli(self.settle_args())
        self.assertEqual(code, 2, out)
        self.assertIn('未终态', out['error'])
        self.assertEqual(self.rows_of('budget_reservations')[0]['status'], 'reserved')
        self.assertEqual(before, self.snapshot())
        self.finish_workflow()
        code, out = self.cli(self.settle_args())
        self.assertEqual(code, 3, out)
        self.assertEqual(out['status'], 'pending_manual_reconciliation')

    def test_settle_rejects_finalized_reservation_and_missing_tables(self):
        self.reserve_ready()
        self.write_evidence()
        self.assertEqual(self.cli(self.release_args())[0], 0)
        code, out = self.cli(self.settle_args())
        self.assertEqual(code, 2, out)
        self.assertIn('已释放或结算', out['error'])
        other = self.base / 'no-settle'
        (other / '.autoresearch').mkdir(parents=True)
        Store(other / '.autoresearch/runtime.sqlite3')
        code, out = self.cli(['settle', '--workspace', str(other), '--reservation-id', 'r',
                              '--series-id', 's', '--actual-units', '1',
                              '--evidence-file', 'e.md', '--locator', 'L1'])
        self.assertEqual(code, 2, out)
        self.assertNotIn('budget_accounts', table_names(other / '.autoresearch/runtime.sqlite3'))


class AccountsFinalizationTests(unittest.TestCase):
    """runtime/accounts.py 的 wf_runs 守卫：未终态挡 release/settle，终态后才允许。"""

    KEY = b'explicit-synthetic-fixture-key-000000000'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.autoresearch').mkdir()
        self.db = self.root / '.autoresearch/runtime.sqlite3'
        self.store = Store(self.db)
        self.store.ensure_experiment('P1')
        self.accounts = Accounts(self.store, ApprovalEvidence(
            {'fixture': SourcePolicy(HMACSource(self.KEY), frozenset({'fixture-user'}))}))
        self.grant()

    def grant(self):
        event = {'event_id': 'grant-small', 'series_id': 'series-small', 'exp_id': 'P1',
                 'scope': 'small', 'action': 'grant', 'expires_at': time.time() + 3600,
                 'total_units': 10, 'principal': 'fixture-user'}
        event['signature'] = hmac.new(self.KEY, canonical_json(event).encode(),
                                      hashlib.sha256).hexdigest()
        self.accounts.authorize('fixture', event, 'grant-rk')
        self.accounts.reserve('series-small', 'P1', 'small', 3, 'reservation-small')

    def workflow(self, finished=False):
        with closing(sqlite3.connect(str(self.db))) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS wf_runs(run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS wf_finishes(run_id TEXT PRIMARY KEY REFERENCES wf_runs(run_id))')
            conn.execute("INSERT INTO wf_runs VALUES('RUN-1','P1')")
            if finished:
                conn.execute("INSERT INTO wf_finishes VALUES('RUN-1')")
            conn.commit()

    def reservation(self):
        return self.accounts.get_reservation('reservation-small')

    def test_unfinished_workflow_blocks_release_and_settle(self):
        self.workflow(finished=False)
        with self.assertRaises(BudgetError):
            self.accounts.release('reservation-small', 'release-1')
        with self.assertRaises(BudgetError):
            self.accounts.settle('reservation-small', 1, 'settle-1')
        self.assertEqual(self.reservation()['status'], 'reserved')
        with closing(sqlite3.connect(str(self.db))) as conn:
            conn.execute("INSERT INTO wf_finishes VALUES('RUN-1')")
            conn.commit()
        self.assertEqual(self.accounts.release('reservation-small', 'release-2')['status'], 'released')

    def test_finished_workflow_allows_settle(self):
        self.workflow(finished=True)
        result = self.accounts.settle('reservation-small', 1, 'settle-1')
        self.assertEqual(result['status'], 'settled')
        self.assertEqual(result['actual_units'], 1)
        self.assertEqual(self.accounts.get_account('series-small')['spent_units'], 1)


class BudgetOrderingTests(unittest.TestCase):
    """生产证明来源的可信时序 fencing：旧事件不得覆盖新状态，跨会话不拿 seq 当全局序。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ws = self.base / 'workspace'
        self.ws.mkdir()
        (self.ws / '.autoresearch').mkdir()
        self.db = self.ws / '.autoresearch/runtime.sqlite3'
        self.store = Store(self.db)
        self.store.ensure_experiment('P1')
        self.now = time.time()
        self.rows = {'owner': [], 'worker': []}
        self.host = make_host(self.base / 'dsh', self.ws, self.rows)

        def factory(**kwargs):
            return self.host

        self.host_factory = factory

    # -- 合成宿主 / CLI -------------------------------------------------------
    def messages(self, session, items):
        rows = [{'type': 'session', 'id': 'session-' + session,
                 'createdAt': self.now - 1000, 'delegationDepth': 0}]
        for seq, when, command in items:
            rows.append({'type': 'user/message', 'seq': seq, 'time': when,
                         'data': {'source': {'kind': 'user',
                                             'rpcId': 'client-' + session + '-' + str(seq)},
                                  'content': json.dumps(command)}})
        self.rows[session] = rows

    def command(self, action, event_id, total_units=10):
        return {'action': action, 'event_id': event_id, 'series_id': 'series-small',
                'scope': 'small', 'exp_id': 'P1', 'expires_at': self.now + 3600,
                'total_units': total_units}

    def grant(self, event, seq, request_key, session='owner'):
        (self.ws / 'event.json').write_text(json.dumps(event), encoding='utf-8')
        argv = ['grant', '--workspace', str(self.ws), '--event-file', 'event.json',
                '--proof-json', json.dumps({'session_id': session, 'seq': seq}),
                '--request-key', request_key]
        out = io.StringIO()
        with patch.object(budget_cli, 'DSHHost', self.host_factory):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = budget_cli.main(argv)
        return code, json.loads(out.getvalue())

    def reserve(self, units=3, request_key='reservation-small'):
        out = io.StringIO()
        argv = ['reserve', '--workspace', str(self.ws), '--series-id', 'series-small',
                '--exp-id', 'P1', '--scope', 'small', '--units', str(units),
                '--request-key', request_key]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = budget_cli.main(argv)
        return code, json.loads(out.getvalue())

    def account(self):
        rows = table_rows(self.db, 'budget_accounts')
        return rows[0] if rows else None

    def state(self):
        names = table_names(self.db)
        return {name: table_rows(self.db, name) for name in
                ('experiments', 'events', 'budget_accounts', 'budget_reservations',
                 'approval_events', 'approval_fences', 'approval_fence_marks',
                 'account_requests')
                if name in names}

    def workspace_files(self):
        """工作区里除事件文件以外的文件快照：被拒绝的授权不得新增或改动任何文件。"""
        return {(path.relative_to(self.ws).as_posix(), path.stat().st_size,
                 path.stat().st_mtime_ns)
                for path in self.ws.rglob('*') if path.is_file() and path.name != 'event.json'}

    def hmac_grant(self, series='series-small', total_units=10, event_id='legacy-grant'):
        key = b'explicit-synthetic-fixture-key-000000000'
        event = {'event_id': event_id, 'series_id': series, 'exp_id': 'P1',
                 'scope': 'small', 'action': 'grant', 'expires_at': self.now + 3600,
                 'total_units': total_units, 'principal': 'fixture-user'}
        event['signature'] = hmac.new(key, canonical_json(event).encode(), hashlib.sha256).hexdigest()
        accounts = Accounts(self.store, ApprovalEvidence(
            {'fixture': SourcePolicy(HMACSource(key), frozenset({'fixture-user'}))}))
        return accounts.authorize('fixture', event, event_id + '-rk')

    # -- 反例：迟到旧事件不能覆盖新状态 -------------------------------------
    def test_late_old_renew_after_revoke_is_refused_without_side_effects(self):
        grant = self.command('grant', 'grant-1')
        # 自报的 expires_at 给到很远：它是调用方字段，不参与排序，也不能让旧事件通过。
        renew = dict(self.command('renew', 'renew-1', total_units=20),
                     expires_at=self.now + 10 ** 9)
        revoke = self.command('revoke', 'revoke-1')
        # 旧 renew 的宿主时间反而比 revoke 更晚：同会话内只认 seq，时钟偏移不能让它越过 revoke。
        self.messages('owner', [(1, self.now - 300, grant), (2, self.now - 50, renew),
                                (3, self.now - 100, revoke)])
        self.assertEqual(self.grant(grant, 1, 'g')[0], 0)
        code, out = self.grant(revoke, 3, 'v')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.account()['active'], 0)
        before = self.state()
        code, out = self.grant(renew, 2, 'r-late')
        self.assertEqual(code, 2, out)
        self.assertIn('旧事件不能覆盖', out['error'])
        self.assertEqual(self.state(), before, '被拒的旧事件不得有任何副作用')
        self.assertEqual(self.account()['active'], 0)
        self.assertEqual(self.account()['total_units'], 10)

    def test_unknown_baseline_order_fails_closed(self):
        # 升级前的授权事件没有 proof：新的证明来源无法判定先后，必须拒绝而不是按到达顺序复活。
        self.hmac_grant()
        renew = self.command('renew', 'renew-1', total_units=20)
        self.messages('owner', [(1, self.now - 100, renew)])
        before = self.state()
        code, out = self.grant(renew, 1, 'r')
        self.assertEqual(code, 2, out)
        self.assertIn('时序', out['error'])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.account()['active'], 1)
        self.assertEqual(self.account()['total_units'], 10)

    def test_stale_reject_with_new_experiment_leaves_no_rows_or_files(self):
        # 迟到旧事件还指向一个从未登记的新实验：既不能生效，也不能先把它登记下来。
        grant = self.command('grant', 'grant-1')
        revoke = self.command('revoke', 'revoke-1')
        late = dict(self.command('renew', 'renew-late', total_units=20), exp_id='P2')
        self.messages('owner', [(1, self.now - 300, grant), (2, self.now - 50, late),
                                (3, self.now - 100, revoke)])
        self.assertEqual(self.grant(grant, 1, 'g')[0], 0)
        self.assertEqual(self.grant(revoke, 3, 'v')[0], 0)
        self.assertIsNone(self.store.get_experiment('P2'))
        before_rows, before_files = self.state(), self.workspace_files()
        code, out = self.grant(late, 2, 'late')
        self.assertEqual(code, 2, out)
        self.assertIn('旧事件不能覆盖', out['error'])
        self.assertIsNone(self.store.get_experiment('P2'), '时序拒绝不得顺手登记新实验')
        self.assertEqual(self.state(), before_rows, '被拒的旧事件不得新增任何数据库行')
        self.assertEqual(self.workspace_files(), before_files, '被拒的旧事件不得新增或改动任何文件')
        self.assertEqual(self.account()['active'], 0)
        self.assertEqual(self.account()['total_units'], 10)

    # -- 正例：真实更晚的事件必须可用 ---------------------------------------
    def test_valid_grant_registers_a_new_experiment_inside_the_transaction(self):
        grant = dict(self.command('grant', 'grant-p2'), exp_id='P2')
        self.messages('owner', [(1, self.now - 100, grant)])
        self.assertIsNone(self.store.get_experiment('P2'))
        code, out = self.grant(grant, 1, 'g')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.store.get_experiment('P2')['id'], 'P2')
        kinds = [row['kind'] for row in table_rows(self.db, 'events')]
        self.assertIn('experiment_created', kinds)
        self.assertIn('approval_grant', kinds)
        # 同一请求键重放：仍然成功，不会因为“本请求建了实验”被误判成 payload 冲突。
        code, replay = self.grant(grant, 1, 'g')
        self.assertEqual(code, 0, replay)
        self.assertEqual(replay['series_id'], 'series-small')
        self.assertEqual(len(table_rows(self.db, 'approval_events')), 1)

    def test_renew_genuinely_later_than_revoke_is_applied(self):
        grant = self.command('grant', 'grant-1')
        revoke = self.command('revoke', 'revoke-1')
        renew = self.command('renew', 'renew-1', total_units=30)
        self.messages('owner', [(1, self.now - 300, grant), (2, self.now - 200, revoke),
                                (3, self.now - 100, renew)])
        self.assertEqual(self.grant(grant, 1, 'g')[0], 0)
        self.assertEqual(self.grant(revoke, 2, 'v')[0], 0)
        code, out = self.grant(renew, 3, 'r')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['active'], 1)
        self.assertEqual(out['total_units'], 30)
        self.assertEqual(len(table_rows(self.db, 'approval_events')), 3)

    def test_cross_session_order_uses_verified_host_time_not_seq(self):
        grant = self.command('grant', 'grant-a')
        equal = self.command('renew', 'renew-equal', total_units=20)
        later = self.command('renew', 'renew-later', total_units=30)
        # 会话 owner 的 seq 是 9；会话 worker 的 seq 只有 1/2。seq 没有全局含义。
        self.messages('owner', [(9, self.now - 300, grant)])
        self.messages('worker', [(1, self.now - 300, equal), (2, self.now - 100, later)])
        self.assertEqual(self.grant(grant, 9, 'g', session='owner')[0], 0)
        # 同一 timestamp、跨会话：先后不可判，必须拒绝且无副作用。
        before = self.state()
        code, out = self.grant(equal, 1, 'equal', session='worker')
        self.assertEqual(code, 2, out)
        self.assertIn('先后不可判', out['error'])
        self.assertEqual(self.state(), before)
        # 可信宿主时间真实更晚：不能用 seq 更小来否掉它。
        code, out = self.grant(later, 2, 'later', session='worker')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['active'], 1)
        self.assertEqual(out['total_units'], 30)

    def test_same_timestamp_within_one_session_is_ordered_by_seq(self):
        grant = self.command('grant', 'grant-1')
        revoke = self.command('revoke', 'revoke-1')
        renew = self.command('renew', 'renew-1', total_units=25)
        self.messages('owner', [(1, self.now - 100, grant), (2, self.now - 100, revoke),
                                (3, self.now - 100, renew)])
        self.assertEqual(self.grant(grant, 1, 'g')[0], 0)
        self.assertEqual(self.grant(revoke, 2, 'v')[0], 0)
        code, out = self.grant(renew, 3, 'r')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['active'], 1)
        self.assertEqual(out['total_units'], 25)

    # -- 幂等重放：回历史结果，但不复活，也不误导 check ---------------------
    def test_old_event_replay_returns_history_but_never_revives(self):
        grant = self.command('grant', 'grant-1')
        renew = self.command('renew', 'renew-1', total_units=20)
        revoke = self.command('revoke', 'revoke-1')
        self.messages('owner', [(1, self.now - 300, grant), (2, self.now - 200, renew),
                                (3, self.now - 100, revoke)])
        self.assertEqual(self.grant(grant, 1, 'g')[0], 0)
        self.assertEqual(self.grant(renew, 2, 'r')[0], 0)
        self.assertEqual(self.reserve()[0], 0)
        self.assertEqual(self.grant(revoke, 3, 'v')[0], 0)
        approvals = len(table_rows(self.db, 'approval_events'))
        fences = table_rows(self.db, 'approval_fences')
        marks = table_rows(self.db, 'approval_fence_marks')
        # 旧 renew 重放：幂等返回历史结果，并标注已被后续事件取代。
        code, out = self.grant(renew, 2, 'r-replay')
        self.assertEqual(code, 0, out)
        self.assertTrue(out.get('replayed'))
        self.assertTrue(out.get('superseded'))
        self.assertEqual(out['current']['active'], 0)
        self.assertEqual(len(table_rows(self.db, 'approval_events')), approvals)
        self.assertEqual(table_rows(self.db, 'approval_fences'), fences)
        self.assertEqual(table_rows(self.db, 'approval_fence_marks'), marks)
        self.assertEqual(self.account()['active'], 0)
        self.assertEqual(self.account()['total_units'], 20)
        # 旧 grant 重放同样只回历史。
        code, out = self.grant(grant, 1, 'g-replay')
        self.assertEqual(code, 0, out)
        self.assertTrue(out.get('superseded'))
        self.assertEqual(self.account()['active'], 0)
        # 同一 request_key 再回放：走缓存也只回历史快照，不复活。
        code, out = self.grant(renew, 2, 'r-replay')
        self.assertEqual(code, 0, out)
        self.assertTrue(out.get('superseded'))
        self.assertEqual(out['current']['active'], 0)
        self.assertEqual(self.account()['active'], 0)
        # Admission.check 只看活库，不被历史结果误导。
        plan = {'id': 'P1', 'budget': {'max_cost_units': 3},
                'authorization': {'scope': 'small', 'series_id': 'series-small',
                                  'reservation_id': 'reservation-small'}}
        result = Admission(self.ws).check(plan, 'owner', 'P1')
        self.assertFalse(result['ok'])
        self.assertIn('撤回', ' '.join(result['problems']))

    # -- 升级路径：旧库没有 fence 行，但事件带 proof 时可恢复同会话基线 -------
    def test_upgrade_without_fence_recovers_same_session_baseline(self):
        grant = self.command('grant', 'grant-1')
        renew = self.command('renew', 'renew-1', total_units=20)
        stale = self.command('renew', 'renew-stale', total_units=15)
        self.messages('owner', [(4, self.now - 300, stale), (5, self.now - 200, grant),
                                (6, self.now - 100, renew)])
        self.assertEqual(self.grant(grant, 5, 'g')[0], 0)
        # 模拟升级前的库：已落账事件带 proof，但没有高水位行。
        with closing(sqlite3.connect(str(self.db))) as conn:
            conn.execute('DELETE FROM approval_fences')
            conn.execute('DELETE FROM approval_fence_marks')
            conn.commit()
        before = self.state()
        code, out = self.grant(stale, 4, 'stale')
        self.assertEqual(code, 2, out)
        self.assertIn('旧事件不能覆盖', out['error'])
        self.assertEqual(self.state(), before)
        code, out = self.grant(renew, 6, 'r')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['total_units'], 20)
        self.assertEqual(table_rows(self.db, 'approval_fences')[0]['seq'], 6)


class OrderingContractTests(unittest.TestCase):
    """Accounts 只信来源时序：来源声明要时序却不给、或给出不可判值，一律拒绝且无副作用。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.autoresearch').mkdir()
        self.db = self.root / '.autoresearch/runtime.sqlite3'
        self.store = Store(self.db)
        self.store.ensure_experiment('P1')
        self.now = time.time()
        self.event = {'action': 'grant', 'event_id': 'E', 'series_id': 'S', 'scope': 'small',
                      'exp_id': 'P1', 'expires_at': self.now + 3600, 'total_units': 3}

    def accounts(self, source):
        return Accounts(self.store, ApprovalEvidence(
            {'fixture': SourcePolicy(source, frozenset({'fixture-user'}))}))

    def test_missing_or_unusable_ordering_fails_closed(self):
        class Required:
            requires_trusted_ordering = True

            def __init__(inner, value):
                inner.value = value

            def __call__(inner, event):
                return 'fixture-user'

            def trusted_ordering(inner, event):
                if isinstance(inner.value, Exception):
                    raise inner.value
                return inner.value

        for value in (None, 'x', {'session': 'fixture-user'},
                      {'session': 'other', 'seq': 1, 'host_time': self.now},
                      {'session': 'fixture-user', 'seq': True, 'host_time': self.now},
                      {'session': 'fixture-user', 'seq': -1, 'host_time': self.now},
                      {'session': 'fixture-user', 'seq': 1, 'host_time': float('nan')},
                      RuntimeError('host log unavailable')):
            with self.subTest(value=value), self.assertRaises(ApprovalError):
                self.accounts(Required(value)).authorize('fixture', self.event, 'k')
            self.assertEqual(table_rows(self.db, 'budget_accounts'), [])
            self.assertEqual(table_rows(self.db, 'approval_events'), [])
            self.assertEqual(table_rows(self.db, 'approval_fences'), [])
            self.assertEqual(table_rows(self.db, 'account_requests'), [])

        # 声明“必须有时序”却根本没有接口：同样拒绝。
        class Missing:
            requires_trusted_ordering = True

            def __call__(inner, event):
                return 'fixture-user'

        with self.assertRaises(ApprovalError):
            self.accounts(Missing()).authorize('fixture', self.event, 'k')
        self.assertEqual(table_rows(self.db, 'budget_accounts'), [])

        # 合法时序才生效，并留下高水位。
        class Good:
            def __call__(inner, event):
                return 'fixture-user'

            def trusted_ordering(inner, event):
                return {'session': 'fixture-user', 'seq': 4, 'host_time': self.now - 10}

        account = self.accounts(Good()).authorize('fixture', self.event, 'k')
        self.assertEqual(account['active'], 1)
        self.assertEqual(table_rows(self.db, 'approval_fences')[0]['seq'], 4)

    def test_legacy_hmac_source_keeps_arrival_order(self):
        key = b'explicit-synthetic-fixture-key-000000000'

        def signed(action, event_id, total_units=10):
            event = dict(self.event, action=action, event_id=event_id,
                         total_units=total_units, principal='fixture-user')
            event['signature'] = hmac.new(key, canonical_json(event).encode(),
                                          hashlib.sha256).hexdigest()
            return event

        accounts = Accounts(self.store, ApprovalEvidence(
            {'fixture': SourcePolicy(HMACSource(key), frozenset({'fixture-user'}))}))
        self.assertEqual(accounts.authorize('fixture', signed('grant', 'g'), 'g')['active'], 1)
        self.assertEqual(accounts.authorize('fixture', signed('revoke', 'v'), 'v')['active'], 0)
        # 传统来源没有可信时序，仍按到达顺序：后到的 renew 重新激活。
        self.assertEqual(accounts.authorize('fixture', signed('renew', 'r', 5), 'r')['active'], 1)
        self.assertEqual(table_rows(self.db, 'approval_fences'), [])
        self.assertEqual(table_rows(self.db, 'approval_fence_marks'), [])

    def signed_fixture(self, **changes):
        key = b'explicit-synthetic-fixture-key-000000000'
        event = dict(self.event, **changes)
        event['principal'] = 'fixture-user'
        event['signature'] = hmac.new(key, canonical_json(event).encode(),
                                      hashlib.sha256).hexdigest()
        return event, Accounts(self.store, ApprovalEvidence(
            {'fixture': SourcePolicy(HMACSource(key), frozenset({'fixture-user'}))}))

    def test_default_api_still_requires_the_experiment(self):
        event, accounts = self.signed_fixture(exp_id='missing')
        with self.assertRaises(ApprovalError):
            accounts.authorize('fixture', event, 'g')
        self.assertIsNone(self.store.get_experiment('missing'))
        self.assertEqual(table_rows(self.db, 'budget_accounts'), [])
        self.assertEqual(table_rows(self.db, 'approval_events'), [])
        self.assertEqual(table_rows(self.db, 'account_requests'), [])

    def test_create_experiment_flag_registers_the_experiment(self):
        event, accounts = self.signed_fixture(exp_id='P9')
        account = accounts.authorize('fixture', event, 'g', create_experiment=True)
        self.assertEqual(account['active'], 1)
        self.assertIsNotNone(self.store.get_experiment('P9'))
        kinds = [row['kind'] for row in table_rows(self.db, 'events')]
        self.assertIn('experiment_created', kinds)
        self.assertIn('approval_grant', kinds)
        # 同一请求键重放：缓存里记了“建过实验”，不会误判 payload 冲突。
        self.assertEqual(accounts.authorize('fixture', event, 'g', create_experiment=True)['active'], 1)
        self.assertEqual(len(table_rows(self.db, 'approval_events')), 1)

    def test_create_experiment_rolls_back_when_a_later_step_fails(self):
        event, accounts = self.signed_fixture(exp_id='P9')
        real, calls = self.store.emit, []

        def emit(conn, exp_id, kind, actor, payload):
            calls.append(kind)
            if len(calls) > 1:
                raise RuntimeError('event failure')
            return real(conn, exp_id, kind, actor, payload)

        with patch.object(self.store, 'emit', side_effect=emit):
            with self.assertRaises(RuntimeError):
                accounts.authorize('fixture', event, 'g', create_experiment=True)
        self.assertEqual(calls[:2], ['experiment_created', 'approval_grant'])
        self.assertIsNone(self.store.get_experiment('P9'), '后续失败必须整体回滚实验登记')
        self.assertEqual([row['id'] for row in table_rows(self.db, 'experiments')], ['P1'])
        self.assertEqual(table_rows(self.db, 'budget_accounts'), [])
        self.assertEqual(table_rows(self.db, 'approval_events'), [])
        self.assertEqual(table_rows(self.db, 'account_requests'), [])


if __name__ == '__main__':
    unittest.main()
