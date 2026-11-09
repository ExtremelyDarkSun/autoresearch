# GENERATED-BY: portable extraction / neutral decision layer, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00（补探索性判据、只读准入、卡片 wire metadata、poll/subscribe、发送方绑定）
# PURPOSE: 决策审批链的离线验收 —— 先页后卡 / 双回执 / 超时取消 / 入向窗口 / 隔离不重发
# ORIGIN: none（新文件）。全部用显式假通道，不连真实平台、不发真实消息
# CUMULATIVE-DELTA: 首版覆盖 B1–B10 / D1–D5 / E7–E8 的通用机制与主要负例；本轮补 ① 探索性无阈值通过、
#   小样本 zero-repeat、确认性要 rationale ② 准入默认只读（写事务内可调用）③ 研究内核指纹防自填旧哈希
#   ④ 页落盘后被改即断链 ⑤ 通道 describe/回执发送方绑定 ⑥ poll/subscribe 两条入向路径
#   ⑦ 卡片 wire metadata 不破坏原 payload hash ⑧ 单元素 options 与部分否决语义。
"""决策审批链的行为验收。

⚠️ 这里的 `FakeChannel` 就是「显式假通道」：它只在测试里实现，接口与部署适配层一致，
返回的回执也是真结构（`message_id` / `request_key` / `payload_hash` / `path` / `sha256`），
所以被判成「送达」的路径与真实适配层走的是同一段代码。⛔ 没有任何测试走「直接写
`ok: True` 到一个字段」的捷径 —— 那正是本模块要拒绝的东西。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kernel.autoresearch.runtime.store import Store
from kernel.autoresearch.runtime.delivery import Delivery, NotSent, DeliveryError
from kernel.autoresearch.decisions import (APPROVAL_TIMEOUT_SECONDS, TEXT_REPLY_GRACE_SECONDS,
                                           REQUIRED_PAGE_BLOCKS, OPTION_FIELD_KEYS,
                                           ChannelReceipt, DecisionService, DecisionError,
                                           ReadinessError, DecisionConflict, OrderingViolated,
                                           ChannelUnavailable, QuarantineBlocked,
                                           OverdueInbound, plan_fingerprint,
                                           research_fingerprint, research_core,
                                           execution_core, execution_fingerprint,
                                           make_authorization_check)
from kernel.autoresearch import decisions_cli

ROOT = Path(__file__).resolve().parents[1]
EXP = 'exp-01'
RECIPIENT = 'chat-01'
OWNER = 'sess-01'


class Clock:
    """可控时钟：审批超时与回复窗口都要能在测试里确定地跨过。

    默认从**现在**开始 —— 这样 CLI（用真实时钟）读同一个库时不会把这条决策判成过期。
    """

    def __init__(self, now=None):
        self.now = float(time.time() if now is None else now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class FakeChannel:
    """显式假通道。接口 = 部署适配层要实现的那一组。"""

    name = 'fake'

    def __init__(self, clock):
        self.clock = clock
        self.issued = {}
        self.files = []
        self.cards = []
        self.texts = []
        self.verified = []
        self.authenticated = []
        self.fail_file = None          # None | 'not_sent' | 'ambiguous'
        self.fail_card = None
        self.verify = True
        self.identity = 'ok'           # 'ok' | 'none' | 'raise'
        self.receipt_override = None
        self.queued_events = []
        self.callbacks = []
        self.handles = []
        self.reply_map = {}

    # ── 自述身份（核心据此算发送方指纹）──
    def describe(self):
        return {'adapter': self.name, 'identity': 'bot-1',
                'config_ref': 'env:FAKE_APP_ID',
                'conditions': {'mention_required': True}, 'version': '0.1'}

    # ── 健康 ──
    def health(self):
        return {'ok': True, 'adapter': self.name, 'mention_required': True}

    # ── 发送 ──
    def send_file(self, payload, artifact):
        self.files.append(dict(payload))
        return self._emit(payload, 'approval_page', artifact)

    def send_card(self, payload, artifact=None):
        self.cards.append(dict(payload))
        return self._emit(payload, 'card', None)

    def send_text(self, payload, artifact=None):
        self.texts.append(dict(payload))
        mapping = self.reply_map.get((payload.get('event_reply_to') or {}).get('event_id'))
        if not mapping:
            raise NotSent('没有本地事件→message_id 映射，拒绝发送')
        return self._emit(payload, 'reply', None)

    # ── 入向：拉取与推送 ──
    def poll(self, cursor, limit):
        start = int(cursor or 0)
        batch = self.queued_events[start:start + limit]
        return {'cursor': str(start + len(batch)), 'events': batch}

    def subscribe(self, callback):
        self.callbacks.append(callback)
        outer = self

        class Handle:
            closed = False

            def close(self):
                self.closed = True
                outer.handles.append(self)

        return Handle()

    def push(self, event):
        return [callback(event) for callback in self.callbacks]

    def _emit(self, payload, kind, artifact):
        failure = self.fail_file if kind == 'approval_page' else (
            self.fail_card if kind == 'card' else None)
        if failure == 'not_sent':
            raise NotSent('通道报告未发出（确定没送出去）')
        if failure == 'ambiguous':
            raise RuntimeError('通道超时：结果未知（可能已经发出去了）')
        receipt = {'message_id': f'om_{kind}_{len(self.issued) + 1}',
                   'request_key': payload['request_key'],
                   'exp_id': payload['exp_id'],
                   'recipient': payload['recipient'],
                   'payload_hash': payload['payload_hash'],
                   'kind': kind,
                   'decision_id': payload.get('decision_id', ''),
                   'adapter': self.name,
                   'ok': True, 'returncode': 0,
                   'conditions': {'mention_required': True},
                   'sent_at': self.clock.now}
        if kind == 'approval_page' and artifact is not None:
            receipt['path'] = payload['artifact']
            receipt['bytes'] = len(artifact)
            receipt['sha256'] = hashlib.sha256(artifact).hexdigest()
        if self.receipt_override is not None:
            receipt = self.receipt_override(receipt, payload, kind) or receipt
        self.issued[receipt['message_id']] = receipt
        return receipt

    def query(self, request_key):
        for receipt in self.issued.values():
            if receipt['request_key'] == request_key:
                return receipt
        return None

    def verify_receipt(self, receipt):
        self.verified.append(receipt.message_id)
        return bool(self.verify) and receipt.message_id in self.issued

    def authenticate(self, source, event):
        self.authenticated.append((source, event.get('event_id')))
        if self.identity == 'raise':
            raise RuntimeError('签名不可核')
        if self.identity == 'none':
            return ''
        return event.get('principal')


# ────────────────────────────── 夹具 ──────────────────────────────

def option(oid, what=None):
    return {
        'id': oid,
        'what': what or f'换抓手 {oid}',
        'fingerprint': [{'lever': 'agent.yaml tools', 'from_to': 'a→b'}],
        'sweep_group': '',
        'cost': {'gpu_hours': 1.5, 'wall_hours': 2.0, 'tasks': 10, 'machines': 1},
        'criteria': {'mode': 'confirmatory',
                     'metrics': [{'metric': 'pass', 'threshold': '>=3', 'source': '自定',
                                  'rationale': '上一轮同题通过数是 1，3 明显超过抖动'}],
                     'rationale': '确认性设计：阈值来自同包重复量出的跨度'},
        'tasklist': {'file': 'tasks.json', 'declared': ['t1', 't2'],
                     'regression': [{'id': 't1', 'in_arm_repeats': 1}]},
        'branches': [{'kind': kind, 'info': f'{kind} 能带回的信息', 'next': '下一步'}
                     for kind in ('all_pass', 'partial', 'all_fail', 'device_failure')],
        'stop_point': '第一题失败就停',
        'not_doing': ['不动题单', '不改判据'],
        'hypothesis': 'H1：换抓手能提高通过数',
        'expected_pass': ['t1'],
        'expected_effect': {'quantity': 'pass', 'unit': '题',
                            'predicted_delta': 2, 'scale_resolution': 1,
                            'scale_resolution_source': '自定',
                            'scale_resolution_why': '同一份提交包重复跑三次量出的跨度'},
    }


def exploratory_option(oid='A'):
    """探索性方案：**没有数字阈值**，只写观察 / 停止条件 / 解释边界。"""
    return {
        'id': oid,
        'what': '先看失败题长什么样',
        'fingerprint': [{'lever': 'prompts/system.md', 'from_to': '观察→记录报错类型'}],
        'sweep_group': '',
        'cost': {'gpu_hours': 0.4, 'wall_hours': 0.6, 'tasks': 4, 'machines': 1},
        'criteria': {'mode': 'exploratory',
                     'observation': '逐题记录失败时的报错类型与首次编辑耗时',
                     'stop_condition': '连续两题同类报错就停下重设抓手',
                     'conditions': '本机同题单',
                     'boundary': '只解释这一台装置上的现象，不外推到线上'},
        'tasklist': {'file': 'tasks.json', 'declared': ['t1'],
                     'regression': [{'id': 't1', 'in_arm_repeats': 0}]},
        'branches': [{'kind': kind, 'info': f'{kind} 能带回的信息', 'next': '下一步'}
                     for kind in ('all_pass', 'partial', 'all_fail', 'device_failure')],
        'stop_point': '报错类型不再变化就停',
        'not_doing': ['不动题单'],
        'hypothesis': 'H2：失败集中在同一类报错',
        'expected_pass': ['t1'],
        'expected_effect': {'observation': '报错类型分布', 'boundary': '不外推到线上'},
    }


def single_option_plan(option_dict):
    plan = plan_set()
    plan['options'] = [option_dict]
    plan['reader_view']['choices'] = [{'option_id': option_dict['id'],
                                       'summary': '做这一条', 'tradeoff': '取舍'}]
    plan['reader_view']['arms'] = [{'name': '臂', 'settings': '设置', 'purpose': '目的',
                                    'comparison': '对基线',
                                    'option_ids': [option_dict['id']]}]
    return plan


def plan_set(exp=EXP, what='换抓手 A'):
    return {
        'exp_id': exp, 'goal': '提高线上分数', 'scope': 'large',
        'question_family': 'Q1', 'from_baseline': 'base-01',
        'changed_faces': ['3'], 'version': 'v1', 'recommended': 'A',
        'options': [option('A', what), option('B')],
        'brief': {'background': '背景：这一轮为什么存在',
                  'context': '上下文：和上一轮的关系',
                  'progress': '进展：现在做到哪了'},
        'reader_view': {
            'title': '要不要跑这一批', 'summary': '这一轮要你决定跑哪一条',
            'sections': [{'title': '为什么值得做', 'body': '因为上一轮留下了矛盾'}],
            'arms': [{'name': 'A 臂', 'settings': '设置 A', 'purpose': '目的 A',
                      'comparison': '对基线', 'option_ids': ['A']}],
            'choices': [{'option_id': 'A', 'summary': '做 A', 'tradeoff': '花 1.5 GPU 小时'},
                        {'option_id': 'B', 'summary': '做 B', 'tradeoff': '更贵'}],
            'budget': 'GPU 1.5 h · 墙钟 2 h · 10 题', 'risks': ['可能白跑'],
        },
    }


def decision(decision_id='E1.1', **changes):
    values = {'decision_id': decision_id, 'recipient': RECIPIENT, 'scope': 'large',
              'owner_session': OWNER, 'needs_user': True,
              'reason': '大题单要人批', 'undecided': ['A', 'B'], 'recommended': 'A'}
    values.update(changes)
    return values


def workflow_plan(research=None, argv=None, implementation=None, budget=None,
                  case_sets=None, authorization=None):
    """一份**完整执行规范**：研究内容在 `research` 里，执行字段在顶层。"""
    return {
        'id': 'WF-1', 'exp_id': EXP, 'owner_session': OWNER,
        'execution_argv': list(argv or ('python', '-m', 'run_eval', '--tasks', 'tasks.json')),
        'implementation': implementation or {'files': ['agent.yaml', 'prompts/system.md'],
                                            'fingerprint': 'impl-v1'},
        'budget': budget or {'max_cost_units': 10, 'max_seconds': 3600},
        'case_sets': case_sets or [{'id': 'cs-1', 'file': 'tasks.json'}],
        'hypothesis': 'H1：换抓手能提高通过数',
        'research': research if research is not None else plan_set(),
        'authorization': authorization or {'scope': 'large', 'decision_id': 'E1.1',
                                           'series_id': 'S-1', 'reservation_id': 'R-1'},
    }


def card_event(service, decision_id='E1.1', event_id='ev-card-1', **changes):
    row = service._require(decision_id)
    event = {'event_id': event_id, 'exp_id': row['exp_id'], 'decision_id': decision_id,
             'intent': 'approve', 'options': ['A'], 'recipient': row['recipient'],
             'short_code': row['short_code'], 'principal': 'user',
             'create_time': service.clock.now, 'card_action': {'action': 'approve'}}
    event.update(changes)
    return event


def question_event(event_id='ev-q-1', **changes):
    event = {'event_id': event_id, 'exp_id': EXP, 'intent': 'question',
             'body': '现在进度怎么样了？', 'recipient': RECIPIENT,
             'principal': 'user', 'create_time': time.time()}
    event.update(changes)
    return event


class DecisionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        (ROOT / 'temp').mkdir(parents=True, exist_ok=True)
        cls.output = Path(tempfile.mkdtemp(prefix='hil_decisions_', dir=ROOT / 'temp'))
        print('ARTIFACT_ROOT=' + str(cls.output))

    def setUp(self):
        self.case = self.output / self._testMethodName
        self.case.mkdir()
        self.clock = Clock()
        self.store = Store(self.case / 'runtime.sqlite3')
        self.channel = FakeChannel(self.clock)
        self.service = DecisionService(self.store, channel=self.channel, clock=self.clock)

    # ── 工具 ──
    def prepare(self, plan=None, dec=None, exp=EXP, execution=None):
        return self.service.prepare(exp_id=exp, decision=dec or decision(),
                                    plan_set=plan if plan is not None else plan_set(),
                                    page_dir=self.case / 'web',
                                    execution_plan=execution)

    def prepare_and_send(self, plan=None, dec=None, execution=None):
        self.prepare(plan=plan, dec=dec, execution=execution)
        result = self.service.send('E1.1')
        self.assertTrue(result['ok'], result)
        return result

    def approve(self):
        """走完 页→卡→点击，留下一份真实批准（用于准入测试）。"""
        return self.service.ingest('fake', card_event(self.service))

    def count(self, table):
        conn = self.store.connect()
        try:
            return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        finally:
            conn.close()

    def event_row(self, source='fake', event_id='ev-card-1'):
        return self.service.inbox.get(source, event_id)

    # ── B1 / B4：先页后卡 ──
    def test_page_must_be_delivered_before_card(self):
        self.prepare()
        self.channel.fail_file = 'not_sent'
        result = self.service.send('E1.1')
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'page')
        self.assertEqual(len(self.channel.files), 1)
        self.assertEqual(self.channel.cards, [])
        with self.assertRaises(OrderingViolated):
            self.service.send_card('E1.1')
        status = self.service.status('E1.1')
        self.assertNotEqual(status['state'], 'awaiting_approval')
        self.assertFalse(status['chain']['complete'])
        self.assertTrue(any('没有回执' in item for item in status['chain']['page']['problems']))

    def test_page_failure_does_not_release_the_decision(self):
        self.prepare()
        self.channel.fail_file = 'not_sent'
        self.service.send('E1.1')
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'send_failed')
        self.assertFalse(status['chain']['page']['ok'])
        self.assertFalse(status['chain']['card']['ok'])
        # 页没送到 ⇒ 没有任何成功回执，也不能被当成待批
        self.assertEqual(self.count('delivery_receipts'), 0)
        verdict = self.service.authorization_check(plan_set(), OWNER, EXP)
        self.assertFalse(verdict['authorized'])

    def test_card_failure_keeps_chain_incomplete_and_retry_resends_only_the_card(self):
        self.prepare()
        self.channel.fail_card = 'not_sent'
        result = self.service.send('E1.1')
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'card')
        self.assertEqual(len(self.channel.files), 1)
        self.assertEqual(len(self.channel.cards), 1)
        status = self.service.status('E1.1')
        self.assertTrue(status['chain']['page']['ok'])
        self.assertFalse(status['chain']['card']['ok'])
        self.assertFalse(status['chain']['complete'])
        self.assertNotEqual(status['state'], 'awaiting_approval')
        self.channel.fail_card = None
        retry = self.service.send('E1.1')
        self.assertTrue(retry['ok'])
        self.assertEqual(len(self.channel.files), 1)   # 页不重发
        self.assertEqual(len(self.channel.cards), 2)   # 只补卡
        self.assertEqual(self.service.status('E1.1')['state'], 'awaiting_approval')

    # ── B2 / B3：缺件拒建 + 六块自包含 ──
    def test_missing_brief_progress_refuses_to_generate(self):
        broken = plan_set()
        broken['brief'] = {'background': '只有背景', 'context': '只有上下文', 'progress': '   '}
        with self.assertRaises(ReadinessError) as caught:
            self.prepare(plan=broken)
        self.assertIn('brief.progress', ' '.join(caught.exception.problems))
        self.assertEqual(self.count('decisions'), 0)
        self.assertFalse((self.case / 'web' / 'approval_E1.1.html').exists())

    def test_missing_reader_view_section_refuses_to_generate(self):
        broken = plan_set()
        broken['reader_view'] = {'title': '只有标题'}
        with self.assertRaises(ReadinessError) as caught:
            self.prepare(plan=broken)
        joined = ' '.join(caught.exception.problems)
        self.assertIn('summary', joined)
        self.assertIn('choices', joined)

    def test_page_is_self_contained_with_six_blocks_and_every_option_field(self):
        view = self.prepare()
        page = Path(view['page_path']).read_text(encoding='utf-8')
        for block in REQUIRED_PAGE_BLOCKS:
            self.assertEqual(page.count(f"id='{block}'"), 1, block)
        self.assertIn("id='audit-appendix'", page)
        for token in ('<script', '<link', '<img', '<iframe', 'href=', ' src=', '@import'):
            self.assertNotIn(token, page.lower())
        for oid in ('A', 'B'):
            self.assertIn(f"data-option='{oid}'", page)
            for key in OPTION_FIELD_KEYS:
                if key == 'id':
                    continue
                self.assertIn(f"data-field='{key}'", page, (oid, key))
        row = self.service._require('E1.1')
        self.assertIn(row['short_code'], page)
        self.assertIn('背景：这一轮为什么存在', page)
        self.assertIn('上下文：和上一轮的关系', page)
        self.assertIn('进展：现在做到哪了', page)
        self.assertIn('超时', page)
        self.assertIn('自动取消', page)

    # ── B10：两小时未批准自动取消 ──
    def test_timeout_cancels_without_consuming_short_code_or_recommended(self):
        self.prepare_and_send()
        row = self.service._require('E1.1')
        code = row['short_code']
        self.clock.advance(APPROVAL_TIMEOUT_SECONDS + 1)
        expired = self.service.tick()
        self.assertEqual(expired, ['E1.1'])
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'expired')
        self.assertEqual(status['resolution'], 'timeout_cancelled')
        self.assertTrue(status['expired'])
        self.assertEqual(status['short_code']['state'], 'void')
        self.assertIsNone(status['short_code']['consumed_by'])
        self.assertFalse(status['short_code']['consumed'])
        # 推荐项没有被采纳：选项一个都没批
        self.assertEqual({item['state'] for item in status['options']}, {'pending'})
        kinds = [event['kind'] for event in status['events']]
        self.assertIn('timeout_cancelled', kinds)
        payload = json.loads(status['events'][-1]['payload_json'])
        # 迟到的点击不成立，短码仍然没有被消费
        self.service.ingest('fake', card_event(self.service, event_id='ev-late',
                                               short_code=code))
        late = self.event_row(event_id='ev-late')
        self.assertEqual(late['status'], 'rejected')
        self.assertEqual(self.service.status('E1.1')['short_code']['state'], 'void')
        self.assertEqual(self.service.status('E1.1')['state'], 'expired')
        self.assertEqual(payload.get('short_code_consumed'), False)

    # ── D4 / E7：问句与卡片点击的区别 ──
    def test_question_creates_reply_task_but_card_click_does_not(self):
        self.prepare_and_send()
        before = self.count('inbox_reply_tasks')
        question = self.service.ingest('fake', question_event())
        self.assertEqual(question['status'], 'applied')
        self.assertEqual(self.count('inbox_reply_tasks'), before + 1)
        self.assertEqual(self.service.status('E1.1')['state'], 'awaiting_approval')
        click = self.service.ingest('fake', card_event(self.service))
        self.assertEqual(click['status'], 'applied')
        self.assertEqual(json.loads(click['result_json'])['resolution'], 'user_approved')
        # 卡片点击的「答」是执行 + 回执，⛔ 不再要求一条文本回复
        self.assertEqual(self.count('inbox_reply_tasks'), before + 1)
        self.assertEqual(self.service.status('E1.1')['state'], 'resolved')

    # ── B7 / E8：批准必须来自真实事件 + 身份 + 归属 ──
    def test_approval_requires_a_real_event_with_matching_numbers_and_identity(self):
        self.prepare_and_send()
        self.assertFalse(hasattr(self.service, 'approve'))
        base = card_event(self.service)
        cases = [
            ('wrong-recipient', dict(base, recipient='chat-other')),
            ('wrong-exp', dict(base, exp_id='exp-99')),
            ('wrong-code', dict(base, short_code='ZZZZ')),
            ('no-options', dict(base, options=[])),
            ('unknown-decision', dict(base, decision_id='E9.9')),
        ]
        for index, (label, event) in enumerate(cases):
            event = dict(event, event_id=f'ev-bad-{index}')
            row = self.service.ingest('fake', event)
            self.assertEqual(row['status'], 'rejected', label)
            self.assertFalse(row['consumed'], label)
        self.assertEqual(self.service.status('E1.1')['state'], 'awaiting_approval')
        self.assertEqual(self.service._code_row(self.service._require('E1.1')['short_code'])['state'],
                         'issued')
        # 身份校验器判否 ⇒ 拒绝，不消费短码
        self.channel.identity = 'none'
        rejected = self.service.ingest('fake', card_event(self.service, event_id='ev-noid'))
        self.assertEqual(rejected['status'], 'rejected')
        self.channel.identity = 'raise'
        rejected = self.service.ingest('fake', card_event(self.service, event_id='ev-bad-sign'))
        self.assertEqual(rejected['status'], 'rejected')
        self.channel.identity = 'ok'
        good = self.service.ingest('fake', card_event(self.service, event_id='ev-good'))
        self.assertEqual(good['status'], 'applied')
        self.assertEqual(good['principal'], 'user')

    def test_judgment_must_be_stated_explicitly(self):
        with self.assertRaises(DecisionError):
            self.service.prepare(exp_id=EXP, decision={'decision_id': 'E1.2',
                                                       'recipient': RECIPIENT},
                                 plan_set=plan_set(), page_dir=self.case / 'web')
        with self.assertRaises(DecisionError):
            self.service.prepare(exp_id=EXP,
                                 decision=decision('E1.3', needs_user=False, undecided=[],
                                                   reason=''),
                                 plan_set=plan_set(), page_dir=self.case / 'web')
        view = self.service.prepare(
            exp_id=EXP,
            decision=decision('E1.4', needs_user=False, undecided=[], reason='本轮免审批'),
            plan_set=plan_set(), page_dir=self.case / 'web')
        self.assertFalse(view['needs_user'])
        with self.assertRaises(DecisionError):
            self.service.send('E1.4')   # 声明不需要人判断 ⇒ 不许发审批

    # ── 回执完整性：空回执 / 伪 success ──
    def test_empty_receipt_is_never_accepted_as_delivery(self):
        self.prepare()
        self.channel.receipt_override = lambda receipt, payload, kind: {
            **receipt, 'message_id': '   '}
        result = self.service.send('E1.1')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'quarantined')
        self.assertEqual(self.count('delivery_receipts'), 0)
        status = self.service.status('E1.1')
        self.assertFalse(status['chain']['page']['ok'])
        self.assertNotEqual(status['state'], 'awaiting_approval')

    def test_unverified_receipt_is_never_accepted_as_delivery(self):
        self.prepare()
        self.channel.verify = False
        result = self.service.send('E1.1')
        self.assertFalse(result['ok'])
        self.assertEqual(self.count('delivery_receipts'), 0)
        self.assertNotEqual(self.service.status('E1.1')['state'], 'awaiting_approval')

    def test_handwritten_success_state_is_not_trusted(self):
        self.prepare()
        with self.store.transaction() as conn:
            conn.execute("UPDATE decisions SET status='awaiting_approval'"
                         " WHERE decision_id='E1.1'")
        status = self.service.status('E1.1')
        self.assertFalse(status['ok'])
        self.assertTrue(any('双回执' in item for item in status['problems']))
        self.assertFalse(self.service.authorization_check(plan_set(), OWNER, EXP)['authorized'])

    # ── 多实例：不重复发送 ──
    def test_concurrent_send_does_not_duplicate_delivery(self):
        self.prepare()

        def run():
            return self.service.send('E1.1')

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in [pool.submit(run), pool.submit(run)]]
        self.assertTrue(any(item['ok'] for item in results), results)
        self.assertEqual(len(self.channel.files), 1)
        self.assertEqual(len(self.channel.cards), 1)
        self.assertEqual(self.count('delivery'), 2)
        self.assertEqual(self.count('delivery_receipts'), 2)
        self.assertTrue(self.service.chain('E1.1')['complete'])

    # ── 结果未知：隔离，不自动重发 ──
    def test_ambiguous_delivery_quarantines_and_never_resends(self):
        self.prepare()
        self.channel.fail_file = 'ambiguous'
        result = self.service.send('E1.1')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'quarantined')
        self.assertEqual(len(self.channel.files), 1)
        with self.assertRaises(QuarantineBlocked):
            self.service.send('E1.1')
        self.assertEqual(len(self.channel.files), 1)   # ⛔ 没有再发一次
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'quarantined')
        self.assertTrue(any('隔离' in item for item in status['problems']))
        kinds = [event['kind'] for event in status['events']]
        self.assertIn('quarantine', kinds)
        self.assertEqual(self.service.delivery.get(1)['status'], 'unknown')

    # ── D1 / D2：304 秒回复窗口与阻断 ──
    def test_overdue_text_reply_is_diagnosed_and_blocks_progress(self):
        self.prepare_and_send()
        self.service.ingest('fake', question_event(create_time=self.clock.now - 400))
        status = self.service.reply_status(EXP)
        self.assertEqual(status['text_events'], 1)
        self.assertEqual(status['replied'], 0)
        self.assertEqual(len(status['overdue']), 1)
        item = status['overdue'][0]
        self.assertEqual(item['event_id'], 'ev-q-1')
        self.assertEqual(item['age_sec'], 400.0)
        self.assertEqual(status['exit_code'], 9)
        self.assertEqual(status['phase'], 'unanswered_inbound')
        self.assertEqual(status['next'], 'reply_user')
        with self.assertRaises(OverdueInbound):
            self.service.assert_can_advance(EXP)
        verdict = self.service.progress_verdict(EXP)
        self.assertFalse(verdict['ok'])
        self.assertEqual(verdict['exit_code'], 9)

    def test_reply_closes_the_window_only_when_it_really_arrives(self):
        self.prepare_and_send()
        self.service.ingest('fake', question_event(create_time=self.clock.now - 400))
        claim = self.service.claim_reply('worker-1')
        self.assertIsNotNone(claim)
        task = self.service.answer(claim, f'进度正常（本轮 {EXP}）')
        self.assertEqual(self.service.reply_status(EXP)['overdue'][0]['event_id'], 'ev-q-1')
        # 没有本地事件→message_id 映射 ⇒ 适配层拒发，回复仍算「没送到」
        refused = self.service.deliver_reply(task['id'])
        self.assertFalse(refused['ok'])
        self.assertEqual(refused['status'], 'not_sent')
        self.assertEqual(self.service.reply_status(EXP)['replied'], 0)
        self.channel.reply_map['ev-q-1'] = 'om_ev_q_1'
        delivered = self.service.deliver_reply(task['id'])
        self.assertTrue(delivered['ok'], delivered)
        self.assertEqual(self.channel.texts[-1]['event_reply_to']['event_id'], 'ev-q-1')
        status = self.service.reply_status(EXP)
        self.assertEqual(status['replied'], 1)
        self.assertEqual(status['overdue'], [])
        self.assertEqual(status['exit_code'], 0)
        self.assertFalse(status['blocked'])
        self.assertTrue(self.service.progress_verdict(EXP)['ok'])

    # ── D3 / B6 / B7：回复必须引用真实事件、实验号、收件人 ──
    def test_reply_requires_real_event_numbering_and_recipient(self):
        self.prepare_and_send()
        self.service.ingest('fake', question_event(create_time=self.clock.now - 10))
        claim = self.service.claim_reply('worker-1')
        with self.assertRaises(DecisionError):
            self.service.answer(claim, '进度正常')                    # 没写实验号
        with self.assertRaises(DecisionError):
            self.service.answer(claim, f'进度正常（本轮 {EXP}，参照 exp-99）')   # 串了别轮
        # 凭空引用一条不存在的事件来发回复 ⇒ 交付层直接拒
        with self.assertRaises(DeliveryError):
            with self.store.transaction() as conn:
                self.service.delivery.enqueue(
                    EXP, 'reply', body=f'答你的提问（本轮 {EXP}）：进度正常',
                    recipient=RECIPIENT,
                    event_reply_to={'source': 'fake', 'event_id': 'ev-ghost',
                                    'exp_id': EXP, 'recipient': RECIPIENT},
                    request_key='reply:ghost', conn=conn)
        # 卡片点击不是文本回复对象：它不产生待回任务
        self.service.ingest('fake', card_event(self.service, event_id='ev-click-2'))
        self.assertIsNone(self.service.claim_reply('worker-2'))

    # ── 主链准入 callback（执行规范绑定） ──
    def test_authorization_check_searches_real_approvals_only(self):
        full = workflow_plan()
        self.prepare(plan=full)
        check = make_authorization_check(self.service)
        small = workflow_plan(authorization={'scope': 'small'})
        self.assertTrue(check(small, OWNER, EXP)['authorized'])
        self.assertEqual(check(small, OWNER, EXP)['resolution'], 'scope_exempt')
        large = check(full, OWNER, EXP)
        self.assertFalse(large['authorized'])
        self.assertTrue(any('没有找到' in item for item in large['problems']))
        self.service.send('E1.1')
        self.assertFalse(check(full, OWNER, EXP)['authorized'])   # 未点击
        self.service.ingest('fake', card_event(self.service))
        approved = check(full, OWNER, EXP)
        self.assertTrue(approved['authorized'], approved)
        self.assertEqual(approved['decision_id'], 'E1.1')
        self.assertEqual(approved['approved'], ['A'])
        self.assertEqual(approved['execution_sha256'], execution_fingerprint(full))
        self.assertEqual(approved['research_sha256'],
                         research_fingerprint(full['research']))
        # 换研究内容 ⇒ 失效（不按文件 mtime 判）
        self.assertFalse(check(workflow_plan(research=plan_set(what='换抓手 A2')),
                               OWNER, EXP)['authorized'])
        # 换会话 / 换范围 ⇒ 失效
        self.assertFalse(check(full, 'sess-other', EXP)['authorized'])
        self.assertFalse(check(workflow_plan(authorization={'scope': 'submission'}),
                               OWNER, EXP)['authorized'])
        # 没声明范围 ⇒ 不放行
        no_scope = dict(full)
        no_scope.pop('authorization')
        verdict = check(no_scope, OWNER, EXP)
        self.assertFalse(verdict['authorized'])
        self.assertEqual(verdict['exit_code'], 3)
        # 跑的不是被批准的那一条 ⇒ 不放行
        self.assertFalse(check(dict(full, option_id='B'), OWNER, EXP)['authorized'])

    def test_execution_binding_rejects_any_change_in_the_actual_plan(self):
        """研究描述一个字不改，只改实际执行 —— 必须拒（这是本轮要堵的洞）。"""
        research = plan_set()
        full = workflow_plan(research=research)
        self.prepare(plan=full)
        self.service.send('E1.1')
        self.approve()
        self.assertTrue(self.service.check_authorization(
            plan=full, owner_session=OWNER, exp_id=EXP)['ok'])
        variants = {
            'argv': workflow_plan(research=research,
                                  argv=('python', '-m', 'run_eval', '--tasks', 'other.json')),
            'implementation': workflow_plan(research=research,
                                            implementation={'files': ['agent.yaml'],
                                                            'fingerprint': 'impl-v2'}),
            'budget': workflow_plan(research=research,
                                    budget={'max_cost_units': 99, 'max_seconds': 3600}),
            'case_sets': workflow_plan(research=research,
                                       case_sets=[{'id': 'cs-2', 'file': 'tasks2.json'}]),
        }
        for label, plan in variants.items():
            self.assertEqual(plan['research'], research, label)
            verdict = self.service.check_authorization(plan=plan, owner_session=OWNER,
                                                       exp_id=EXP)
            self.assertFalse(verdict['ok'], label)
            self.assertTrue(any('执行计划' in item for item in verdict['problems']), label)
        # 抄旧 sha 也救不了：指纹是对实际内容重算的
        forged = workflow_plan(research=research,
                               argv=('python', '-m', 'run_eval', '--tasks', 'other.json'))
        forged['plan_sha256'] = execution_fingerprint(full)
        verdict = self.service.check_authorization(plan=forged, owner_session=OWNER,
                                                   exp_id=EXP)
        self.assertFalse(verdict['ok'])
        self.assertTrue(any('执行计划' in item for item in verdict['problems']))
        # 只换预算账号 / 预留号（执行引用）不该失效
        rebooked = workflow_plan(research=research,
                                 authorization={'scope': 'large', 'decision_id': 'E1.1',
                                                'series_id': 'S-9',
                                                'reservation_id': 'R-9'})
        self.assertTrue(self.service.check_authorization(
            plan=rebooked, owner_session=OWNER, exp_id=EXP)['ok'])

    def test_legacy_plan_only_is_not_accepted_for_workflow_launch(self):
        """只生成审批页、没绑执行规范的决策：能发能批，但不能给 Workflow 起跑。"""
        view = self.prepare()
        self.assertEqual(view['execution_sha256'], '')
        self.assertFalse(view['execution_bound'])
        self.service.send('E1.1')
        self.approve()
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'resolved')
        self.assertFalse(status['execution_bound'])
        self.assertTrue(any('执行计划' in item for item in status['warnings']))
        verdict = self.service.check_authorization(plan=plan_set(), owner_session=OWNER,
                                                   exp_id=EXP)
        self.assertFalse(verdict['ok'])
        self.assertTrue(any('execution binding' in item for item in verdict['problems']))
        # 换成一份完整执行规范去核 ⇒ 没有对应的批准
        self.assertFalse(self.service.check_authorization(
            plan=workflow_plan(), owner_session=OWNER, exp_id=EXP)['ok'])

    def test_check_authorization_accepts_main_chain_plan_shape(self):
        """主链的计划形状：研究内容在顶层，`authorization` 只是执行引用。"""
        full = workflow_plan(research=plan_set())
        self.prepare(plan=full)
        self.service.send('E1.1')
        self.approve()
        verdict = self.service.check_authorization(plan=full, owner_session=OWNER,
                                                   exp_id=EXP)
        self.assertTrue(verdict['ok'], verdict)
        self.assertTrue(verdict['authorized'])
        self.assertEqual(verdict['decision_id'], 'E1.1')
        # decision_id 指错 ⇒ 不放行
        wrong = workflow_plan(research=plan_set(),
                              authorization={'scope': 'large', 'decision_id': 'E1.9',
                                             'series_id': 'S-1'})
        self.assertFalse(self.service.check_authorization(
            plan=wrong, owner_session=OWNER, exp_id=EXP)['ok'])
        # 只写 decision_id、没有执行内容 ⇒ 不放行（引用不是证明）
        bare = {'authorization': {'scope': 'large', 'decision_id': 'E1.1'}}
        self.assertFalse(self.service.check_authorization(
            plan=bare, owner_session=OWNER, exp_id=EXP)['ok'])
        # 直接传完整 plan（研究内容在 research 里）也是同一种绑定
        self.assertTrue(self.service.check_authorization(
            plan=workflow_plan(research=plan_set()), owner_session=OWNER, exp_id=EXP)['ok'])

    def test_caller_supplied_hash_is_not_proof(self):
        """复制一份旧哈希再改内容 —— 必须被拒（对实际内容重算，不信自报）。"""
        full = workflow_plan()
        self.prepare(plan=full)
        self.service.send('E1.1')
        self.approve()
        good = execution_fingerprint(full)
        changed = workflow_plan(research=plan_set(what='换了内容'))
        changed['plan_sha256'] = good
        verdict = self.service.check_authorization(plan=changed, owner_session=OWNER,
                                                   exp_id=EXP)
        self.assertFalse(verdict['ok'])
        self.assertTrue(any('执行计划' in item for item in verdict['problems']))
        # 内核计算必须与「authorization 的其它字段」无关，只保留 scope
        self.assertEqual(research_core({'authorization': {'scope': 'small', 'series_id': 'S'},
                                        'options': []}),
                         {'scope': 'small', 'options': []})
        self.assertEqual(research_core({'plan_sha256': 'deadbeef', 'options': []}),
                         {'options': []})
        self.assertEqual(plan_fingerprint({'options': []}),
                         research_fingerprint({'plan_sha256': 'x', 'options': []}))
        # 执行内核相反：**保留** argv / 实现 / 预算 / 题集 / research，只去掉授权引用
        core = execution_core(dict(full, plan_sha256='deadbeef'))
        self.assertIn('execution_argv', core)
        self.assertIn('implementation', core)
        self.assertIn('budget', core)
        self.assertIn('case_sets', core)
        self.assertIn('research', core)
        self.assertEqual(core['scope'], 'large')
        self.assertNotIn('decision_id', core)

    def test_check_authorization_is_read_only_by_default(self):
        full = workflow_plan()
        self.prepare(plan=full)
        self.service.send('E1.1')
        events_before = self.count('events')
        decision_events_before = self.count('decision_events')
        verdict = self.service.check_authorization(plan=full, owner_session=OWNER,
                                                   exp_id=EXP)
        self.assertFalse(verdict['ok'])
        self.assertEqual(self.count('events'), events_before)
        self.assertEqual(self.count('decision_events'), decision_events_before)
        self.service.check_authorization(plan=full, owner_session=OWNER, exp_id=EXP,
                                         record=True)
        self.assertEqual(self.count('events'), events_before + 1)

    def test_check_authorization_runs_inside_an_open_write_transaction(self):
        """主链在 `BEGIN IMMEDIATE` 里会再调一次：它必须只读，不能抢写锁。"""
        full = workflow_plan()
        self.prepare(plan=full)
        self.service.send('E1.1')
        self.approve()
        with self.store.transaction() as conn:          # 持有 RESERVED 锁
            conn.execute("UPDATE experiments SET goal='held' WHERE id=?", (EXP,))
            first = self.service.check_authorization(plan=full, owner_session=OWNER,
                                                     exp_id=EXP)
            second = self.service.check_authorization(plan=full, owner_session=OWNER,
                                                      exp_id=EXP)
        self.assertTrue(first['ok'], first)
        self.assertTrue(second['ok'], second)

    def test_authorization_check_does_not_consume_or_create_approvals(self):
        full = workflow_plan()
        self.prepare(plan=full)
        self.service.send('E1.1')
        code_before = self.service._code_row(self.service._require('E1.1')['short_code'])['state']
        self.service.authorization_check(full, OWNER, EXP)
        self.assertEqual(self.service._code_row(
            self.service._require('E1.1')['short_code'])['state'], code_before)
        self.assertEqual(self.service.status('E1.1')['state'], 'awaiting_approval')

    def test_verify_remote_checks_delivered_messages_via_get(self):
        self.prepare_and_send()
        result = self.service.verify_remote('E1.1')
        self.assertTrue(result['ok'], result)
        page = result['kinds']['page']
        self.assertEqual(page['stored_message_id'], page['remote']['message_id'])
        # 通道那边查不到了 ⇒ 如实报出来，⛔ 不重发
        self.channel.issued.clear()
        result = self.service.verify_remote('E1.1')
        self.assertFalse(result['ok'])
        self.assertTrue(any('查不到' in item for item in result['problems']))
        self.assertEqual(len(self.channel.files), 1)

    # ── 探索性判据：不强制数字门槛 ──
    def test_exploratory_option_needs_no_numeric_threshold(self):
        plan = single_option_plan(exploratory_option())
        view = self.service.prepare(
            exp_id=EXP,
            decision=decision('E2.1', scope='small', needs_user=True,
                              undecided=['A'], reason='探索性小样本，自己推进'),
            plan_set=plan, page_dir=self.case / 'web')
        self.assertEqual(view['status'], 'prepared')
        page = Path(view['page_path']).read_text(encoding='utf-8')
        self.assertIn('逐题记录失败时的报错类型', page)
        self.assertIn("data-option='A'", page)

    def test_exploratory_without_observation_or_stop_condition_is_refused(self):
        broken = exploratory_option()
        broken['criteria'] = {'mode': 'exploratory'}
        broken['expected_effect'] = {}
        with self.assertRaises(ReadinessError) as caught:
            self.service.prepare(exp_id=EXP,
                                 decision=decision('E2.2', scope='small', undecided=['A']),
                                 plan_set=single_option_plan(broken),
                                 page_dir=self.case / 'web')
        self.assertIn('探索性', ' '.join(caught.exception.problems))

    def test_small_sample_exploratory_may_declare_zero_repeats(self):
        plan = single_option_plan(exploratory_option())
        self.assertEqual(plan['options'][0]['tasklist']['regression'][0]['in_arm_repeats'], 0)
        self.service.prepare(exp_id=EXP,
                             decision=decision('E2.3', scope='small', undecided=['A']),
                             plan_set=plan, page_dir=self.case / 'web')

    def test_confirmatory_needs_rationale_and_one_repeat(self):
        no_rationale = option('A')
        no_rationale['criteria'] = {'mode': 'confirmatory',
                                    'metrics': [{'metric': 'pass', 'threshold': '>=3',
                                                 'source': '自定'}]}
        with self.assertRaises(ReadinessError) as caught:
            self.service.prepare(exp_id=EXP, decision=decision('E2.4', undecided=['A']),
                                 plan_set=single_option_plan(no_rationale),
                                 page_dir=self.case / 'web')
        self.assertIn('rationale', ' '.join(caught.exception.problems))
        no_repeat = option('A')
        no_repeat['tasklist']['regression'] = [{'id': 't1', 'in_arm_repeats': 0}]
        with self.assertRaises(ReadinessError) as caught:
            self.service.prepare(exp_id=EXP, decision=decision('E2.5', undecided=['A']),
                                 plan_set=single_option_plan(no_repeat),
                                 page_dir=self.case / 'web')
        self.assertIn('重复次数', ' '.join(caught.exception.problems))

    # ── 页先落盘 + 发送确认 + 发送方绑定 ──
    def test_page_tampered_on_disk_breaks_the_chain(self):
        full = workflow_plan()
        view = self.prepare(plan=full)
        self.service.send('E1.1')
        chain = self.service.chain('E1.1')
        self.assertTrue(chain['complete'])
        page = Path(view['page_path'])
        page.write_text(page.read_text(encoding='utf-8').replace('换抓手 A', '换抓手 X'),
                        encoding='utf-8')
        chain = self.service.chain('E1.1')
        self.assertFalse(chain['complete'])
        self.assertTrue(any('不一致' in item for item in chain['page']['problems']))
        self.approve()
        self.assertFalse(self.service.authorization_check(full, OWNER, EXP)['authorized'])

    def test_receipt_binds_sender_identity_from_description(self):
        self.prepare_and_send()
        chain = self.service.chain('E1.1')
        page_receipt = chain['page']['receipt']
        card_receipt = chain['card']['receipt']
        for receipt in (page_receipt, card_receipt):
            self.assertEqual(receipt['adapter'], 'fake')
            self.assertEqual(receipt['identity'], 'bot-1')
            self.assertEqual(receipt['config_ref'], 'env:FAKE_APP_ID')
            self.assertEqual(receipt['conditions'], {'mention_required': True})
            self.assertTrue(receipt['sender_fingerprint'])
        self.assertEqual(page_receipt['sender_fingerprint'],
                         card_receipt['sender_fingerprint'])
        # 适配层自报的 adapter 会被核心覆写：回执里的发送方只认 describe()
        self.channel.files.clear()
        self.channel.receipt_override = lambda receipt, payload, kind: {
            **receipt, 'adapter': 'someone-else', 'identity': 'hijack',
            'config_ref': 'env:OTHER'}
        self.prepare(dec=decision('E3.1'), plan=plan_set())
        self.service.send('E3.1')
        receipt = self.service.chain('E3.1')['page']['receipt']
        self.assertEqual(receipt['adapter'], 'fake')
        self.assertEqual(receipt['identity'], 'bot-1')

    def test_channel_without_describe_fails_closed(self):
        class Bare:
            name = 'bare'

            def health(self):
                return {'ok': True}

        service = DecisionService(self.store, channel=Bare(), clock=self.clock)
        self.prepare()
        with self.assertRaises(ChannelUnavailable):
            service.send('E1.1')
        self.assertEqual(self.count('delivery_receipts'), 0)

    def test_card_payload_carries_structured_short_code_and_options(self):
        self.prepare()
        self.service.send('E1.1')
        payload = self.channel.cards[0]
        row = self.service._require('E1.1')
        self.assertEqual(payload['short_code'], row['short_code'])
        self.assertEqual([item['option_id'] for item in payload['card_options']], ['A', 'B'])
        self.assertEqual(payload['card_options'][0]['label'], '换抓手 A')
        self.assertEqual(payload['card_action_template']['decision_id'], 'E1.1')
        self.assertEqual(payload['card_action_template']['short_code'], row['short_code'])
        self.assertEqual(payload['card_action_template']['recipient'], RECIPIENT)
        # 这些是 wire metadata：delivery 行里的 payload 与 hash 一个字节都没动
        chain = self.service.chain('E1.1')
        task = self.service.delivery.get(chain['card']['delivery_id'])
        stored = json.loads(task['payload_json'])
        self.assertNotIn('card_options', stored)
        self.assertNotIn('short_code', stored)
        self.assertNotIn('card_action_template', stored)
        self.assertEqual(payload['payload_hash'], task['payload_hash'])
        self.assertTrue(chain['card']['ok'])

    def test_single_option_click_and_partial_reject_keep_decision_open(self):
        self.prepare_and_send()
        row = self.service._require('E1.1')
        event = {'event_id': 'ev-chat-1', 'exp_id': EXP, 'decision_id': 'E1.1',
                 'intent': 'reject', 'options': ['B'], 'recipient': RECIPIENT,
                 'short_code': row['short_code'], 'principal': 'user',
                 'create_time': self.clock.now, 'channel': 'chat_adapter',
                 'card_action': {'action': 'reject', 'raw': {'operator': 'u1'}}}
        applied = self.service.ingest('fake', event)
        self.assertEqual(applied['status'], 'applied')
        result = json.loads(applied['result_json'])
        self.assertEqual(result['rejected'], ['B'])
        self.assertFalse(result['short_code_consumed'])
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'awaiting_approval')
        self.assertEqual(status['short_code']['state'], 'issued')
        self.assertEqual({item['option_id']: item['state'] for item in status['options']},
                         {'A': 'pending', 'B': 'rejected'})
        approve = dict(event, event_id='ev-chat-2', intent='approve', options=['A'],
                       card_action={'action': 'approve'})
        applied = self.service.ingest('fake', approve)
        self.assertEqual(json.loads(applied['result_json'])['resolution'], 'user_approved')
        self.assertEqual(self.service.status('E1.1')['state'], 'resolved')

    def test_reject_without_options_rejects_all_and_closes(self):
        self.prepare_and_send()
        row = self.service._require('E1.1')
        event = {'event_id': 'ev-chat-3', 'exp_id': EXP, 'decision_id': 'E1.1',
                 'intent': 'reject', 'options': [], 'recipient': RECIPIENT,
                 'short_code': row['short_code'], 'principal': 'user',
                 'create_time': self.clock.now, 'channel': 'chat_adapter',
                 'card_action': {'action': 'reject'}}
        applied = self.service.ingest('fake', event)
        result = json.loads(applied['result_json'])
        self.assertEqual(result['resolution'], 'user_all_reject')
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'resolved')
        self.assertEqual({item['state'] for item in status['options']}, {'rejected'})
        self.assertTrue(status['short_code']['consumed'])

    # ── 入向：poll 与 subscribe ──
    def test_poll_ingests_events_and_advances_cursor(self):
        self.prepare_and_send()
        self.channel.queued_events = [question_event(event_id='ev-p1')]
        result = self.service.poll_channel(None, limit=10)
        self.assertTrue(result['ok'])
        self.assertEqual(result['cursor'], '1')
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['results'][0]['status'], 'applied')
        self.assertEqual(self.count('inbox_reply_tasks'), 1)
        again = self.service.poll_channel('1', limit=10)
        self.assertEqual(again['count'], 0)
        self.assertEqual(again['cursor'], '1')

    def test_subscription_pushes_events_into_the_inbox(self):
        self.prepare_and_send()
        handle = self.service.open_subscription()
        self.channel.push(card_event(self.service, event_id='ev-push'))
        self.assertEqual(self.service.status('E1.1')['state'], 'resolved')
        handle.close()
        self.assertTrue(handle.closed)

    # ── 审批页要写清「真跑什么」 ──
    def test_page_shows_actual_execution_command_and_budget(self):
        full = workflow_plan()
        view = self.prepare(plan=full)
        page = Path(view['page_path']).read_text(encoding='utf-8')
        self.assertEqual(view['execution_sha256'], execution_fingerprint(full))
        self.assertTrue(view['execution_bound'])
        self.assertIn("id='execution'", page)
        self.assertIn(f"data-execution-sha='{execution_fingerprint(full)}'", page)
        self.assertIn("data-exec='实际命令'", page)
        self.assertIn("data-exec='预算与上限'", page)
        self.assertIn('run_eval', page)
        self.assertIn('max_cost_units', page)
        self.assertIn('tasks.json', page)
        # 旧式（只给方案集）的页必须明说没有绑定执行计划
        legacy = self.prepare(dec=decision('E9.9'))
        legacy_page = Path(legacy['page_path']).read_text(encoding='utf-8')
        self.assertFalse(legacy['execution_bound'])
        self.assertIn('没有绑定实际执行计划', legacy_page)

    # ── CLI ──
    def test_cli_generates_page_offline_and_send_fails_closed(self):
        plan_file = self.case / 'plan.json'
        plan_file.write_text(json.dumps(plan_set(), ensure_ascii=False), encoding='utf-8')
        db = str(self.case / 'cli.sqlite3')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = decisions_cli.main(['--db', db, 'prepare', '--exp', EXP,
                                       '--decision-id', 'E1.1', '--plan', str(plan_file),
                                       '--recipient', RECIPIENT, '--scope', 'large',
                                       '--owner-session', OWNER, '--reason', '大题单要人批',
                                       '--undecided', 'A,B'])
        self.assertEqual(code, 0, out.getvalue())
        view = json.loads(out.getvalue())
        self.assertTrue(Path(view['page_path']).exists())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = decisions_cli.main(['--db', db, 'send', '--decision', 'E1.1'])
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['error'], 'ChannelUnavailable')
        store = Store(db)
        conn = store.connect()
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM delivery').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT status FROM decisions').fetchone()[0],
                             'prepared')
        finally:
            conn.close()

    def test_cli_has_no_way_to_fake_an_approval_or_a_delivery(self):
        parser = decisions_cli.build_parser()
        options = []

        def collect(target):
            for action in target._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for sub in action.choices.values():
                        collect(sub)
                else:
                    options.extend(action.option_strings)

        collect(parser)
        joined = ' '.join(options).lower()
        for forbidden in ('--approve', '--fake', '--force', '--assume', '--dry-run-success'):
            self.assertNotIn(forbidden, joined)
        commands = set()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                commands = set(action.choices)
        self.assertEqual(commands, {'prepare', 'send', 'status', 'inbox', 'overdue',
                                    'reply', 'hook', 'authorize-check'})

    def test_cli_authorize_check_reports_verdict(self):
        full = workflow_plan()
        self.prepare(plan=full)
        self.service.send('E1.1')
        self.approve()
        plan_file = self.case / 'plan.json'
        plan_file.write_text(json.dumps(full, ensure_ascii=False), encoding='utf-8')
        db = str(self.case / 'runtime.sqlite3')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = decisions_cli.main(['--db', db, 'authorize-check',
                                       '--plan', str(plan_file)])
        self.assertEqual(code, 0, out.getvalue())
        verdict = json.loads(out.getvalue())
        self.assertTrue(verdict['ok'])
        self.assertEqual(verdict['decision_id'], 'E1.1')
        # 改了 argv（研究内容没动）⇒ exit 3
        changed = workflow_plan(argv=('python', '-m', 'run_eval', '--tasks', 'other.json'))
        plan_file.write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = decisions_cli.main(['--db', db, 'authorize-check',
                                       '--plan', str(plan_file)])
        self.assertEqual(code, 3, out.getvalue())
        self.assertFalse(json.loads(out.getvalue())['ok'])

    def test_cli_status_and_overdue_run_with_zero_external_config(self):
        self.prepare()
        db = str(self.case / 'runtime.sqlite3')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = decisions_cli.main(['--db', db, 'status', '--decision', 'E1.1'])
        self.assertEqual(code, 0, out.getvalue())
        status = json.loads(out.getvalue())
        self.assertEqual(status['state'], 'prepared')
        self.assertFalse(status['chain']['complete'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = decisions_cli.main(['--db', db, 'overdue', '--exp', EXP])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['overdue'], [])

    def test_cli_inbox_persists_event_but_refuses_to_apply_without_verifier(self):
        self.prepare_and_send()
        event = card_event(self.service)
        event_file = self.case / 'event.json'
        event_file.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
        db = str(self.case / 'runtime.sqlite3')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = decisions_cli.main(['--db', db, 'inbox', '--source', 'fake',
                                       '--event', str(event_file)])
        self.assertEqual(code, 2, out.getvalue())
        payload = json.loads(out.getvalue())
        self.assertFalse(payload['consumed'])
        self.assertEqual(self.service.status('E1.1')['state'], 'awaiting_approval')

    # ── 同号换内容：拒绝覆盖 ──
    def test_same_decision_id_with_different_content_is_refused(self):
        self.prepare()
        with self.assertRaises(DecisionConflict):
            self.prepare(plan=plan_set(what='换了内容'))
        self.assertEqual(self.count('decisions'), 1)

    def test_receipt_conditions_are_recorded_not_interpreted(self):
        self.prepare_and_send()
        receipt = self.service.chain('E1.1')['page']['receipt']
        self.assertEqual(receipt['conditions'], {'mention_required': True})
        self.assertEqual(receipt['adapter'], 'fake')


if __name__ == '__main__':
    unittest.main()
