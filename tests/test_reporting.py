# GENERATED-BY: portable reporting migration, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00（人读页迁 topics/、真实 DSHHost 集成、provision 集成）
# PURPOSE: reporting 层的正反例测试 —— 完整供料、字节版本（不看 mtime）、来源变化、scribe 角色、
#          三轮四段、事件级联、整轮交付、只读诊断、流程状态分离、安全停，真实 DSHHost 集成，
#          以及 workspace.provision_topic → prepare 的真实目录集成。
# ORIGIN: none（新文件）。全部合成夹具与合成宿主日志，不含任何真实实验、读数、机器名或会话号。
# CUMULATIVE-DELTA: 2026-11-09 人读页改 topics/<topic>/STATUS.md，来源默认扫 provision 产物与
#          sources.json 清单（清单自身也哈希、越界拒）；I3 记为「总量 3 轮（含当前实验）」。

"""reporting 层测试。

三条约束（与治理层测试一致）：

1. **只用标准库**（unittest / json / tempfile / pathlib / sqlite3 / threading / unittest.mock）。
2. **全部合成数据**：专题 `t1`、实验 `exp-001`、读数都是编的，不引用任何真实实验与历史台账。
3. **不伪造授权**：需要 verified 的路径用合成宿主证明「只有宿主证据齐全才能核过」；
   没有验证器时只能 declared，回执与载荷里都要写清是自述。

跑法：

    python -X utf8 -B -m unittest discover -s tests -p 'test_reporting.py' -v
    python -X utf8 -B tests/test_reporting.py
"""

from __future__ import annotations

import contextlib
import copy
import functools
import io
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import reporting as rep  # noqa: E402
from kernel.autoresearch import reporting_cli  # noqa: E402

PAGE_SECTIONS = list(rep.PAGE_SECTIONS)
TOPIC = 't1'
EXP = 'exp-001'
SESSION = 'session-scribe-1'
OWNER = 'session-owner-1'

FIELD_VALUES = {
    '假设': '改一个入口参数后同题单通过数会变大',
    '判据': '观察同题单通过数；只在同装置、同题单条件下解释，不外推',
    '设置': '同题单 6 题、重复 2 次、对照为上一版',
    '结果': '通过数不变（阶段结果，不能外推）',
}
DEFAULT_ORDER = ('假设', '判据', '设置', '结果')


def block_text(name='实验1', order=DEFAULT_ORDER, source=True):
    lines = [f'### {name}'] + [f'- {label}：{FIELD_VALUES[label]}' for label in order]
    if source:
        lines.append('- 来源：`EVIDENCE_20260101.md` 第 1 节')
    return '\n'.join(lines)


def page_text(blocks=1, orders=None, source=True, empty=None):
    """合成研究页：八节齐全，第 3 节按需给 0–n 个实验块。"""
    contents = {index: '研究内容' for index in range(1, 9)}
    if blocks:
        contents[3] = '\n\n'.join(
            block_text(f'实验{index}', (orders or {}).get(index, DEFAULT_ORDER), source)
            for index in range(1, blocks + 1))
    else:
        contents[3] = '暂无实验，未起跑。原因：尚未形成可检验问题。'
    if empty:
        contents[empty] = ''
    return '\n\n'.join(f'## {index} {name}\n{contents[index]}'
                       for index, name in enumerate(PAGE_SECTIONS, 1)) + '\n'


def manifest(root):
    """目录字节清单：相对路径 → (字节数, sha256, mtime_ns)。只读检查不许改动它。"""
    result = {}
    for path in sorted(pathlib.Path(root).rglob('*')):
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (
                stat.st_size, rep.file_sha256(path), stat.st_mtime_ns)
    return result


class Fixture:
    """一个合成专题的工作区夹具。

    人读侧 = `topics/<topic>/`（专题主文档、目标、要求、证据、plans、人读页）；
    机器侧 = `.autoresearch/state/reporting/<topic>/`（供料包、快照、任务、回执、事件、输入）。
    """

    def __init__(self, root, topic=TOPIC):
        self.root = pathlib.Path(root)
        self.topic = topic
        self.human = self.root / 'topics' / topic
        self.dir = self.root / '.autoresearch/state/reporting' / topic
        self.human.mkdir(parents=True, exist_ok=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prepared = None
        self.prepared_id = None

    def write(self, name, text=''):
        """写人读侧文件（专题正文、证据、plans、人读页）。"""
        path = self.human / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
        return path

    def write_machine(self, name, text=''):
        """写机器侧文件（checkpoint 等）。"""
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
        return path

    def rel(self, path):
        return pathlib.Path(path).relative_to(self.root).as_posix()

    def source(self, name='records/run.md', text='运行记录\n'):
        return {'path': self.rel(self.write(name, text)), 'locator': 'L1'}

    def topic_doc(self, text='# 专题\n\n研究问题：这个改动有没有用。\n'):
        return self.write('TOPIC.md', text)

    def objective(self, text='# 目标\n\n提高线上分数。\n'):
        return self.write('OBJECTIVE.md', text)

    def requirements(self, text='# 用户要求\n\n先小样本验证机制。\n'):
        return self.write('REQUIREMENTS.md', text)

    def evidence(self, name='EVIDENCE_20260101.md', text='## 节1\n\n同题单通过数 3。\n'):
        return self.write(name, text)

    def plan(self, exp=EXP):
        document = {
            'id': exp,
            'hypothesis': {'id': 'H1', 'text': '改一个入口参数后通过数会变大'},
            'criteria': {'metric': '通过题数', 'direction': 'increase', 'threshold': 1,
                         'source': '上一次读数（合成）'},
            'branches': {'support': {'action': '继续同假设'},
                         'contradict': {'action': '回退改动'},
                         'inconclusive': {'action': '补一轮重复读数'}},
            'budget': {'max_cost_units': 1, 'max_seconds': 60},
        }
        return self.write(f'plans/{exp}.json', json.dumps(document, ensure_ascii=False))

    def page(self, text=None):
        return self.write('STATUS.md', page_text() if text is None else text)

    def requirement(self):
        return rep.record_input(self.root, self.topic, 'user_requirement',
                                '用户要求：验证这个改动有没有用',
                                self.source('records/user.md', '用户原话\n'))

    def conclusion(self, exp=EXP):
        return rep.record_input(self.root, self.topic, 'leader_conclusion',
                                '负责人结论：阶段结果不足以判方向',
                                self.source('records/leader.md', '负责人结论\n'), exp=exp)

    def prepare(self, **kwargs):
        self.prepared = rep.prepare(self.root, self.topic, **kwargs)
        self.prepared_id = self.prepared['prepared_id']
        return self.prepared

    def ack(self, session=SESSION, **kwargs):
        kwargs.setdefault('writer_role', rep.WRITER_ROLE)
        return rep.acknowledge(self.root, self.topic, session, **kwargs)


class FakeHost:
    """合成宿主验证器：默认给出齐全证据；`drop` 删字段，`**overrides` 改字段。"""

    def __init__(self, drop=(), **overrides):
        self.drop = tuple(drop)
        self.overrides = overrides
        self.roles = []
        self.payloads = []

    def verify(self, role, payload):
        self.roles.append(role)
        self.payloads.append(payload)
        evidence = {
            'verified': True,
            'proof': dict(payload['proof']),
            'session_id': payload['writer_session'],
            'spawned_by': OWNER,
            'parent_session': OWNER,
            'owner_session': OWNER,
            'spawned': True,
            'prepared_id': payload['prepared_id'],
            'page_path': payload['page_path'],
            'page_sha256': payload['page_sha256'],
            'tool_calls': [{'tool': 'write', 'session_id': payload['writer_session'],
                            'path': payload['page_path'], 'ok': True,
                            'content_sha256': payload['page_sha256'], 'seq': 3}],
            'delivery': {'delivered': False, 'channel': None},
        }
        for key in self.drop:
            evidence.pop(key, None)
        evidence.update(self.overrides)
        return evidence


def host_logs(prepared_id, page_rel_path, page_value, *, child='child1', owner='owner',
              write_path=None, write_text=None):
    """构造一份合成宿主日志：负责人派工 → 子会话整页 write → 可见回传。

    形状照 `kernel/autoresearch/host_evidence.py` 读的事件结构：
    `session` 头 + 严格递增 `seq` 的行；写入绑定靠 `tool/call` 的 arguments 与 `tool/result` 成功文本。
    """
    return {
        owner: [
            {'type': 'session', 'id': f'session-{owner}', 'createdAt': 1000, 'delegationDepth': 0},
            {'type': 'user/message', 'seq': 1, 'time': 1001,
             'data': {'source': {'kind': 'user', 'rpcId': 'client-1'},
                      'content': '请派 scribe 写研究页'}},
            {'type': 'tool/call', 'seq': 2, 'time': 1002,
             'data': {'name': 'subagent', 'callId': 'call-dispatch',
                      'arguments': {'prompt': f'写研究页；材料 prepared_id={prepared_id}；'
                                              f'页面 {page_rel_path}'}}},
            {'type': 'tool/result', 'seq': 3, 'time': 1003,
             'data': {'message': {'toolCallId': 'call-dispatch',
                                  'content': f'started subagent {child}'}}},
            {'type': 'subagent/catalog', 'seq': 4, 'time': 1004,
             'data': {'childId': child, 'mode': 'continuable'}},
        ],
        child: [
            {'type': 'session', 'id': f'session-{child}', 'parentSession': f'session-{owner}',
             'origin': 'subagent', 'delegationDepth': 1, 'isSeeded': False, 'createdAt': 1003},
            {'type': 'subagent/descriptor', 'seq': 1, 'time': 1003,
             'data': {'provider': 'spawn', 'mode': 'continuable'}},
            {'type': 'session/end-seed', 'seq': 2, 'time': 1003, 'data': {}},
            {'type': 'tool/call', 'seq': 3, 'time': 1004,
             'data': {'name': 'write', 'callId': 'call-write',
                      'arguments': {'file_path': write_path or page_rel_path,
                                    'content': page_value if write_text is None else write_text}}},
            {'type': 'tool/result', 'seq': 4, 'time': 1005,
             'data': {'message': {'toolCallId': 'call-write',
                                  'content': f'Created file: {write_path or page_rel_path}'}}},
            {'type': 'assistant/message', 'seq': 5, 'time': 1006,
             'data': {'message': {'role': 'assistant',
                                  'content': [{'type': 'text',
                                               'text': f'已写页：{page_rel_path}'}]}}},
        ],
    }


def make_host(host_root, workspace, logs):
    """用真实 `DSHHost` + 注入解码器造一个合成宿主（不碰真实 ~/.dsh）。"""
    from kernel.autoresearch.host_evidence import DSHHost
    for name in logs:
        path = pathlib.Path(host_root) / 'sessions/project' / f'session-{name}'
        path.mkdir(parents=True, exist_ok=True)
        (path / 'session.v4.jsonl.zstd').write_bytes(b'injected-decoder-fixture')
    return DSHHost(host_root, workspace=workspace,
                   decoder=lambda path: copy.deepcopy(
                       logs[path.parent.name.removeprefix('session-')]))


class ReportingTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = pathlib.Path(self._temp.name) / 'workspace'
        self.root.mkdir()
        self.fx = Fixture(self.root)
        self.fx.topic_doc()
        self.fx.evidence()
        self.fx.plan()

    def run_cli(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = reporting_cli.main(argv)
        text = buffer.getvalue()
        try:
            payload = json.loads(text) if text.strip() else None
        except ValueError:
            payload = None
        return code, payload

    def prepared(self, **kwargs):
        self.fx.requirement()
        return self.fx.prepare(**kwargs)

    def acked(self, **kwargs):
        self.prepared()
        self.fx.page()
        return self.fx.ack(**kwargs)


# ---------------------------------------------------------------------------
# 供料
# ---------------------------------------------------------------------------


class FeedTests(ReportingTestCase):
    def test_feed_lists_every_source_with_sha256_and_full_text(self):
        prepared = self.prepared()
        feed = (self.fx.dir / 'FEED.md').read_text(encoding='utf-8')
        files = prepared['sources']['files']
        self.assertGreaterEqual(len(files), 3)
        for rel, meta in files.items():
            self.assertIn(f'`{rel}`', feed)
            self.assertIn(meta['sha256'], feed)
        self.assertIn('同题单通过数 3。', feed)          # 证据正文完整内联
        self.assertIn('"branches"', feed)                # 设置 / 分支随 plan 全文进来
        self.assertEqual(prepared['feed']['sha256'], rep.file_sha256(self.fx.dir / 'FEED.md'))
        self.assertTrue(any(rel.startswith('topics/t1/') for rel in files))

    def test_feed_never_truncates_old_page(self):
        self.fx.evidence(text='## 节1\n\n' + '证据' * 200 + '\n')
        self.prepared(inline_limit=50)
        self.fx.page(page_text() + '页面尾部填充' * 400 + '\n')
        prepared = self.fx.prepare(inline_limit=50)
        feed = (self.fx.dir / 'FEED.md').read_text(encoding='utf-8')
        page = (self.fx.human / 'STATUS.md').read_text(encoding='utf-8')
        self.assertIn(page.rstrip('\n'), feed)
        self.assertIn('页面尾部填充' * 400, feed)
        self.assertGreater(prepared['feed']['bytes'], len(page.encode('utf-8')))

    def test_feed_marks_oversized_source_as_not_inlined(self):
        self.fx.evidence(text='## 节1\n\n' + '证据' * 200 + '\n')
        self.prepared(inline_limit=10)
        feed = (self.fx.dir / 'FEED.md').read_text(encoding='utf-8')
        self.assertIn('未内联', feed)
        self.assertIn('补读路径', feed)
        self.assertNotIn('证据证据证据', feed)          # 没内联就不许假装内联
        self.assertIn('EVIDENCE_20260101.md', feed)      # 但仍列出清单与补读位置

    def test_missing_items_state_where_to_read(self):
        prepared = self.fx.prepare()                     # 故意不登记用户要求
        items = {item['item']: item for item in prepared['missing']}
        self.assertIn('用户要求来源', items)
        self.assertEqual(items['用户要求来源']['level'], 'blocker')
        self.assertIn('reporting input', items['用户要求来源']['read'])
        self.assertIn('运行事件', items)
        self.assertEqual(items['运行事件']['level'], 'info')
        self.assertEqual(prepared['task']['state'], 'pending_owner')
        feed = (self.fx.dir / 'FEED.md').read_text(encoding='utf-8')
        self.assertIn('[blocker] 用户要求来源', feed)
        self.assertIn('总量最多 3 轮（含当前实验）', feed)   # I3 口径：总量 3 含当前
        self.assertNotIn('当前实验与最近 3 轮', feed)

    def test_prepare_is_stable_and_idempotent(self):
        first = self.prepared()
        second = rep.prepare(self.root, TOPIC)
        self.assertEqual(first['prepared_id'], second['prepared_id'])
        self.assertEqual(first['feed']['sha256'], second['feed']['sha256'])
        self.assertEqual(first['task']['task_id'], second['task']['task_id'])
        self.assertEqual(first['dispatch']['must_mention'], ['prepared_id', 'page_path'])
        self.assertEqual(first['dispatch']['page_path'], 'topics/t1/STATUS.md')
        self.assertEqual(first['verification'], 'declared')

    def test_prepare_rejects_source_outside_workspace(self):
        outside = pathlib.Path(self._temp.name) / 'outside.md'
        outside.write_text('不该被当来源\n', encoding='utf-8')
        with self.assertRaises(rep.ReportingError) as caught:
            rep.prepare(self.root, TOPIC, extra_sources=[str(outside)])
        self.assertIn('不在工作区内', str(caught.exception))

    def test_missing_extra_source_is_blocker(self):
        self.fx.requirement()
        prepared = self.fx.prepare(extra_sources=['records/does-not-exist.md'])
        self.assertEqual(prepared['task']['state'], 'pending_owner')
        self.assertTrue(any('来源清单缺文件' in item['item'] for item in prepared['missing']))

    def test_prepare_creates_human_page_parent(self):
        bare = pathlib.Path(self._temp.name) / 'bare'
        bare.mkdir()
        rep.prepare(bare, 't2')
        self.assertTrue(rep.topic_root(bare, 't2').is_dir())
        self.assertTrue(rep.machine_dir(bare, 't2').is_dir())
        self.assertFalse(rep.page_path(bare, 't2').is_file())
        self.assertEqual(rep.page_rel('t2'), 'topics/t2/STATUS.md')


# ---------------------------------------------------------------------------
# 版本：字节哈希，不看 mtime
# ---------------------------------------------------------------------------


class VersionTests(ReportingTestCase):
    def test_touch_does_not_invalidate(self):
        receipt = self.acked()
        before = rep.source_snapshot(self.root, TOPIC)['fingerprint']
        future = time.time() + 7200
        for name in ('TOPIC.md', 'EVIDENCE_20260101.md', f'plans/{EXP}.json', 'STATUS.md'):
            os.utime(self.fx.human / name, (future, future))
        self.assertEqual(before, rep.source_snapshot(self.root, TOPIC)['fingerprint'])
        state = rep.check(self.root, TOPIC)
        self.assertTrue(state['ok'], state['problems'])
        self.assertEqual(state['page']['body_sha256'], receipt['body_sha256'])
        again = self.fx.ack()
        self.assertTrue(again.get('idempotent'))
        self.assertEqual(again['version'], receipt['version'])

    def test_source_change_after_prepare_is_rejected(self):
        self.prepared()
        self.fx.page()
        self.fx.evidence('EVIDENCE_20260102.md', '## 节2\n\n新增证据：通过数 5。\n')
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack()
        self.assertIn('供料后来源已变化', str(caught.exception))

    def test_source_change_with_unchanged_body_is_rejected(self):
        receipt = self.acked()
        self.fx.evidence('EVIDENCE_20260102.md', '## 节2\n\n新增证据：通过数 5。\n')
        self.fx.prepare()
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack()
        self.assertIn('来源已变化但正文未变', str(caught.exception))
        self.assertEqual(rep.page_sha256(self.root, TOPIC), receipt['body_sha256'])

    def test_integrated_body_is_accepted_after_source_change(self):
        old = self.acked()
        self.fx.evidence('EVIDENCE_20260102.md', '## 节2\n\n新增证据：通过数 5。\n')
        self.fx.prepare()
        with self.assertRaises(rep.ReportingError):
            self.fx.ack()
        self.fx.page(page_text(blocks=2))
        new = self.fx.ack()
        self.assertNotEqual(new['version'], old['version'])
        self.assertEqual(new['verification'], 'declared')
        state = rep.check(self.root, TOPIC)
        self.assertTrue(state['ok'], state['problems'])

    def test_appended_event_invalidates_receipt(self):
        self.acked()
        rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1',
                         self.fx.source('records/run.md'))
        state = rep.check(self.root, TOPIC)
        self.assertFalse(state['ok'])
        self.assertTrue(any('未整合最新来源' in problem for problem in state['problems']))


# ---------------------------------------------------------------------------
# 回执与 scribe 角色
# ---------------------------------------------------------------------------


class AckTests(ReportingTestCase):
    def test_requires_scribe_role(self):
        self.prepared()
        self.fx.page()
        with self.assertRaises(rep.ReportingError) as caught:
            rep.acknowledge(self.root, TOPIC, SESSION, writer_role=None)
        self.assertIn('scribe', str(caught.exception))
        with self.assertRaises(rep.ReportingError):
            rep.acknowledge(self.root, TOPIC, SESSION, writer_role='leader')

    def test_requires_writer_session(self):
        self.prepared()
        self.fx.page()
        for value in ('', '   ', None):
            with self.assertRaises(rep.ReportingError) as caught:
                rep.acknowledge(self.root, TOPIC, value, writer_role=rep.WRITER_ROLE)
            self.assertIn('writer_session', str(caught.exception))

    def test_receipt_binds_body_source_cutoff_version(self):
        receipt = self.acked()
        snapshot = rep.source_snapshot(self.root, TOPIC)
        self.assertEqual(receipt['body_sha256'], rep.page_sha256(self.root, TOPIC))
        self.assertEqual(receipt['source_fingerprint'], snapshot['fingerprint'])
        self.assertEqual(receipt['sources'], snapshot['files'])
        self.assertEqual(receipt['event_cutoff'], rep.event_cutoff(self.root, TOPIC))
        self.assertEqual(receipt['writer_role'], 'scribe')
        self.assertEqual(receipt['writer_session'], SESSION)
        self.assertEqual(receipt['writer_session_source'], 'caller_declaration')
        self.assertEqual(receipt['verification'], 'declared')
        self.assertFalse(receipt['session_verified'])
        self.assertFalse(receipt['dispatch_verified'])
        self.assertFalse(receipt['delivery_verified'])
        self.assertIsNone(receipt['proof'])
        self.assertEqual(len(receipt['version']), 64)
        self.assertIn('自述', receipt['declaration_note'])
        payload = receipt['host_payload']
        self.assertTrue(payload['declared_only'])
        self.assertEqual(payload['proof'], {'session_id': SESSION, 'seq': None})
        self.assertEqual(payload['workspace'], str(self.root.resolve()))
        self.assertEqual(payload['page_path'], 'topics/t1/STATUS.md')
        self.assertEqual(payload['page_sha256'], receipt['body_sha256'])

    def test_page_structure_is_reused(self):
        self.prepared()
        cases = {
            '四段顺序不对': page_text(orders={1: ('判据', '假设', '设置', '结果')}),
            '缺来源': page_text(source=False),
            '空节': page_text(empty=6),
        }
        for label, text in cases.items():
            self.fx.page(text)
            with self.assertRaises(rep.ReportingError, msg=label) as caught:
                self.fx.ack()
            self.assertIn('研究正文不合规', str(caught.exception), label)
        self.fx.page()
        self.assertTrue(self.fx.ack())

    def test_three_blocks_accepted_four_rejected(self):
        """I3 轮数口径记录。

        源侧 `TOPICS_RUNBOOK.md` §11.1「上限」行、`SCRIBE_STATUS_PROMPT.md` 与专题页模板都写
        「本页只留最近 3 轮」，「当前实验优先」是取舍顺序而非额外名额；目标仓库
        `governance.validate_status_page` 同样判 ≤3。⇒ 采用**总量 3 轮（含当前实验）**。
        本测试把这条边界钉住：3 块收、4 块拒。
        """
        self.prepared()
        self.fx.page(page_text(blocks=4))
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack()
        self.assertIn('最近 3 轮', str(caught.exception))
        self.fx.page(page_text(blocks=3))
        self.assertTrue(self.fx.ack())

    def test_empty_experiment_body_is_rejected(self):
        self.prepared()
        self.fx.page(page_text(blocks=0))
        self.assertTrue(self.fx.ack())                  # 「暂无实验，未起跑」明确写了就通过
        self.fx.page(page_text(blocks=0).replace('暂无实验，未起跑。原因：尚未形成可检验问题。', ''))
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack()
        self.assertIn('研究正文不合规', str(caught.exception))

    def test_verified_receipt_from_host_evidence(self):
        self.prepared()
        self.fx.page()
        host = FakeHost()
        receipt = self.fx.ack(host_verifier=host,
                              proof={'session_id': SESSION, 'seq': 11},
                              owner_session=OWNER)
        self.assertEqual(receipt['verification'], 'verified')
        self.assertTrue(receipt['session_verified'])
        self.assertTrue(receipt['dispatch_verified'])
        self.assertFalse(receipt['delivery_verified'])
        self.assertEqual(receipt['writer_session_source'], 'host_verifier')
        self.assertEqual(receipt['proof_verified_by'], 'proof_echo')
        self.assertEqual(receipt['content_bound'], 'argument_sha256')
        self.assertEqual(host.roles, ['scribe'])
        self.assertEqual(host.payloads[0]['workspace'], str(self.root.resolve()))
        self.assertEqual(host.payloads[0]['proof'], {'session_id': SESSION, 'seq': 11})
        self.assertFalse(host.payloads[0]['declared_only'])
        self.assertTrue(rep.check(self.root, TOPIC)['ok'])

    def test_verified_requires_proof(self):
        self.prepared()
        self.fx.page()
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack(host_verifier=FakeHost(), owner_session=OWNER)
        self.assertIn('proof', str(caught.exception))
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack(host_verifier=FakeHost(), owner_session=OWNER,
                        proof={'session_id': SESSION, 'seq': None})
        self.assertIn('proof.seq', str(caught.exception))

    def test_host_dispatch_receipt_shape_is_accepted(self):
        """真实宿主形态：不回显 proof，改回 tool_call_id + source；tool_calls 不带内容哈希。"""
        self.prepared()
        self.fx.page()
        host = FakeHost(drop=('proof',), tool_call_id='call-42', source='host-log',
                        tool_calls=[{'tool': 'write', 'session_id': SESSION,
                                     'path': rep.page_rel(TOPIC), 'ok': True, 'seq': 3}])
        receipt = self.fx.ack(host_verifier=host,
                              proof={'session_id': SESSION, 'seq': 5},
                              owner_session=OWNER)
        self.assertEqual(receipt['verification'], 'verified')
        self.assertEqual(receipt['proof_verified_by'], 'host_dispatch_receipt')
        self.assertEqual(receipt['content_bound'], 'host_replay')

    def test_verifier_without_visible_return_is_rejected(self):
        self.prepared()
        self.fx.page()
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack(host_verifier=FakeHost(drop=('proof',)), owner_session=OWNER,
                        proof={'session_id': SESSION, 'seq': 5})
        self.assertIn('tool_call_id', str(caught.exception))

    def test_verifier_content_mismatch_is_rejected(self):
        self.prepared()
        self.fx.page()
        host = FakeHost(tool_calls=[{'tool': 'write', 'session_id': SESSION,
                                     'path': rep.page_rel(TOPIC), 'ok': True,
                                     'content_sha256': '0' * 64, 'seq': 3}])
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack(host_verifier=host, owner_session=OWNER,
                        proof={'session_id': SESSION, 'seq': 5})
        self.assertIn('tool_calls', str(caught.exception))

    def test_verifier_session_mismatch_is_rejected(self):
        self.prepared()
        self.fx.page()
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack(host_verifier=FakeHost(session_id='session-other'),
                        owner_session=OWNER, proof={'session_id': SESSION, 'seq': 5})
        self.assertIn('会话', str(caught.exception))

    def test_verifier_stale_page_hash_is_rejected(self):
        self.prepared()
        self.fx.page()
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack(host_verifier=FakeHost(page_sha256='f' * 64),
                        owner_session=OWNER, proof={'session_id': SESSION, 'seq': 5})
        self.assertIn('page_sha256', str(caught.exception))

    def test_verifier_proof_echo_mismatch_is_rejected(self):
        self.prepared()
        self.fx.page()
        with self.assertRaises(rep.ReportingError) as caught:
            self.fx.ack(host_verifier=FakeHost(proof={'session_id': SESSION, 'seq': 99}),
                        owner_session=OWNER, proof={'session_id': SESSION, 'seq': 5})
        self.assertIn('可见回传', str(caught.exception))

    def test_verifier_declines_stays_declared(self):
        self.prepared()
        self.fx.page()
        receipt = self.fx.ack(host_verifier=FakeHost(verified=False, reason='缺写入回执'),
                              owner_session=OWNER, proof={'session_id': SESSION, 'seq': 5})
        self.assertEqual(receipt['verification'], 'declared')
        self.assertFalse(receipt['session_verified'])
        self.assertEqual(receipt['host_evidence']['reason'], '缺写入回执')
        self.assertIn('宿主核验未通过', receipt['declaration_note'])

    def test_verifier_error_fails_ack_without_receipt(self):
        self.prepared()
        self.fx.page()

        def broken(payload):
            raise ValueError('宿主日志撕裂')

        with self.assertRaises(ValueError) as caught:
            self.fx.ack(host_verifier=broken, owner_session=OWNER,
                        proof={'session_id': SESSION, 'seq': 5})
        self.assertIn('撕裂', str(caught.exception))
        self.assertFalse(rep.receipt_path(self.root, TOPIC).is_file())

    def test_all_four_verifier_injection_shapes(self):
        self.prepared()
        self.fx.page()
        host = FakeHost()
        proof = {'session_id': SESSION, 'seq': 9}
        receipts = [
            self.fx.ack(host_verifier=host, proof=proof, owner_session=OWNER),
            self.fx.ack(host_verifier=host.verify, proof=proof, owner_session=OWNER),
            self.fx.ack(host_verifier=functools.partial(host.verify, 'scribe'),
                        proof=proof, owner_session=OWNER),
            self.fx.ack(host_verifier=lambda payload: host.verify('scribe', payload),
                        proof=proof, owner_session=OWNER),
        ]
        for receipt in receipts:
            self.assertEqual(receipt['verification'], 'verified')
        self.assertEqual(host.roles, ['scribe'] * 4)

    def test_flow_fields_only_warn(self):
        self.prepared()
        page = page_text().replace('## 8 下一步研究\n研究内容',
                                   '## 8 下一步研究\n研究内容\n\n- 自推进：心跳 12:00 刷新')
        self.fx.page(page)
        self.fx.ack()
        state = rep.check(self.root, TOPIC)
        self.assertTrue(state['ok'], state['problems'])
        self.assertTrue(any('心跳' in warning for warning in state['warnings']))

    def test_legacy_page_is_read_only_compatible(self):
        legacy = '\n\n'.join(f'## {index} {name}\n旧页内容'
                             for index, name in enumerate(
                                 ('范围与目标', '你的要求', '当前结论', '证据',
                                  '还没做成的事', '需要你拍板', '证据在哪', '自推进状态'), 1)) + '\n'
        self.fx.write('STATUS.md', legacy)
        state = rep.check(self.root, TOPIC)
        self.assertTrue(state['ok'])
        self.assertTrue(state['legacy_exempt'])
        self.assertIn('不代表正文已验证', state['note'])
        self.assertFalse(rep.check(self.root, TOPIC, required=True)['ok'])


# ---------------------------------------------------------------------------
# 事件、级联与整轮交付
# ---------------------------------------------------------------------------


class EventTests(ReportingTestCase):
    def test_terminal_event_requires_reason(self):
        source = self.fx.source('records/run.md')
        for kind in ('completed', 'failed', 'stopped', 'superseded'):
            with self.assertRaises(rep.ReportingError) as caught:
                rep.record_event(self.root, TOPIC, EXP, kind, f'evt-{kind}', source)
            self.assertIn('reason', str(caught.exception))
        rep.record_event(self.root, TOPIC, EXP, 'partial', 'evt-partial', source)

    def test_event_idempotent_and_conflicting(self):
        source = self.fx.source('records/run.md')
        first = rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source)
        second = rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source)
        self.assertFalse(first['idempotent'])
        self.assertTrue(second['idempotent'])
        with self.assertRaises(rep.ReportingError) as caught:
            rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source, scope='round')
        self.assertIn('event_id 重用', str(caught.exception))

    def test_run_and_round_terminals_are_persisted(self):
        source = self.fx.source('records/run.md')
        rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source)
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-2', source,
                         scope='run', reason='单臂跑完')
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-3', source,
                         scope='round', reason='整轮两臂回收完成')
        rows = [json.loads(line) for line in
                rep.events_path(self.root, TOPIC).read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertEqual([row['scope'] for row in rows], ['run', 'run', 'round'])
        self.assertEqual([row['kind'] for row in rows], ['running', 'completed', 'completed'])
        self.assertEqual(rep.round_terminal(self.root, TOPIC)['event_id'], 'evt-3')
        self.assertEqual(rep.round_terminal(self.root, TOPIC, EXP)['reason'], '整轮两臂回收完成')

    def test_round_check_rejects_run_only(self):
        source = self.fx.source('records/run.md')
        rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source)
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-2', source,
                         scope='run', reason='单臂跑完')
        self.fx.requirement()
        self.fx.conclusion()
        self.fx.prepare()
        self.fx.page()
        self.fx.ack()
        result = rep.check_round(self.root, TOPIC, EXP)
        self.assertFalse(result['ok'])
        self.assertTrue(any('单臂完成或 partial' in problem for problem in result['problems']))
        self.assertTrue(any('单臂终态' in note for note in result['notes']))

    def test_round_check_rejects_partial_only(self):
        source = self.fx.source('records/run.md')
        rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source)
        rep.record_event(self.root, TOPIC, EXP, 'partial', 'evt-2', source)
        self.fx.requirement()
        self.fx.conclusion()
        self.fx.prepare()
        self.fx.page()
        self.fx.ack()
        result = rep.check_round(self.root, TOPIC, EXP)
        self.assertFalse(result['ok'])
        self.assertTrue(any('partial' in note for note in result['notes']))
        self.assertTrue(any('整轮终态' in problem for problem in result['problems']))

    def test_round_check_requires_leader_conclusion(self):
        source = self.fx.source('records/run.md')
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-1', source,
                         scope='round', reason='整轮回收完成')
        self.fx.requirement()
        prepared = self.fx.prepare()
        self.assertEqual(prepared['task']['state'], 'pending_owner')
        result = rep.check_round(self.root, TOPIC, EXP)
        self.assertFalse(result['ok'])
        self.assertTrue(any('负责人的判断' in problem for problem in result['problems']))

    def test_round_check_ok_needs_terminal_judgment_and_receipt(self):
        source = self.fx.source('records/run.md')
        rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source)
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-2', source,
                         scope='run', reason='单臂跑完')
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-3', source,
                         scope='round', reason='整轮两臂回收完成')
        self.fx.requirement()
        self.fx.conclusion()
        self.fx.prepare()
        self.fx.page()
        self.fx.ack()
        result = rep.check_round(self.root, TOPIC, EXP)
        self.assertTrue(result['ok'], result['problems'])
        self.assertEqual(result['round_terminal']['kind'], 'completed')
        self.assertTrue(any('declared' in note for note in result['notes']))

    def test_append_is_append_only(self):
        source = self.fx.source('records/run.md')
        rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-1', source)
        before = rep.events_path(self.root, TOPIC).read_bytes()
        rep.record_event(self.root, TOPIC, EXP, 'running', 'evt-2', source)
        after = rep.events_path(self.root, TOPIC).read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(after.count(b'\n'), 2)

    def test_concurrent_appends_are_serialized(self):
        source = self.fx.source('records/run.md')
        errors = []

        def worker(prefix):
            try:
                for index in range(6):
                    rep.record_event(self.root, TOPIC, EXP, 'running', f'{prefix}-{index}', source)
            except Exception as exc:      # noqa: BLE001 —— 测试要把并发错误收上来断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(prefix,)) for prefix in ('a', 'b', 'c')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        lines = [line for line in
                 rep.events_path(self.root, TOPIC).read_text(encoding='utf-8').splitlines() if line.strip()]
        rows = [json.loads(line) for line in lines]
        self.assertEqual(len(rows), 18)
        self.assertEqual(len({row['event_id'] for row in rows}), 18)


# ---------------------------------------------------------------------------
# 只读诊断、checkpoint 时效与流程状态分离
# ---------------------------------------------------------------------------


class DiagnoseTests(ReportingTestCase):
    def test_diagnose_is_read_only(self):
        self.acked()
        rep.write_process_status(self.root, TOPIC)
        before = manifest(self.root)
        report = rep.diagnose(self.root, TOPIC)
        self.assertEqual(manifest(self.root), before)
        self.assertTrue(report['read_only'])
        self.assertEqual(report['writes'], [])
        self.assertIn('只读', report['note'])

    def test_stale_checkpoint_is_pending_not_verified(self):
        stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.fx.write_machine('CHECKPOINT_20260101.json',
                              json.dumps({'at': stale, 'next_action': '继续下一轮', 'summary': 'x'}))
        self.acked()
        report = rep.diagnose(self.root, TOPIC, checkpoint_max_age_hours=2.0)
        self.assertEqual(report['verdict'], '状态待核')
        self.assertFalse(report['runtime_verified'])
        self.assertTrue(report['checkpoint']['stale'])
        self.assertGreater(report['checkpoint']['age_hours'], 2)
        self.assertFalse(report['ok'])

    def test_fresh_checkpoint_is_not_runtime_verified(self):
        fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.fx.write_machine('CHECKPOINT_20260101.json',
                              json.dumps({'at': fresh, 'next_action': '继续下一轮', 'summary': 'x'}))
        self.acked()
        report = rep.diagnose(self.root, TOPIC)
        self.assertEqual(report['verdict'], '在推')
        self.assertFalse(report['runtime_verified'])
        self.assertFalse(report['checkpoint']['stale'])
        self.assertTrue(report['ok'])

    def test_checkpoint_without_timestamp_is_pending(self):
        self.fx.write_machine('CHECKPOINT_20260101.json',
                              json.dumps({'next_action': '继续下一轮', 'summary': 'x'}))
        self.acked()
        report = rep.diagnose(self.root, TOPIC)
        self.assertEqual(report['verdict'], '状态待核')
        self.assertFalse(report['runtime_verified'])
        self.assertIn('时间戳', report['checkpoint']['why'])

    def test_no_checkpoint_is_pending(self):
        self.acked()
        report = rep.diagnose(self.root, TOPIC)
        self.assertEqual(report['verdict'], '状态待核')
        self.assertTrue(report['checkpoint']['stale'])

    def test_round_terminal_verdict_is_finished(self):
        source = self.fx.source('records/run.md')
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-1', source,
                         scope='round', reason='整轮回收完成')
        self.fx.requirement()
        self.fx.conclusion()
        self.fx.prepare()
        self.fx.page()
        self.fx.ack()
        report = rep.diagnose(self.root, TOPIC)
        self.assertEqual(report['verdict'], '已结束')
        self.assertEqual(report['round_terminal']['kind'], 'completed')

    def test_lease_is_unknown_or_readonly(self):
        self.acked()
        report = rep.diagnose(self.root, TOPIC)
        self.assertEqual(report['lease']['state'], 'UNKNOWN')
        database = self.root / '.autoresearch/runtime.sqlite3'
        connection = sqlite3.connect(str(database))
        try:
            connection.execute('CREATE TABLE topics(id TEXT PRIMARY KEY, status TEXT, '
                               'leader_session_id TEXT, leader_since TEXT)')
            connection.execute('INSERT INTO topics VALUES(?,?,?,?)', (TOPIC, 'active', None, None))
            connection.commit()
        finally:
            connection.close()
        before = database.read_bytes()
        report = rep.diagnose(self.root, TOPIC, db=database)
        self.assertEqual(report['lease']['state'], 'FREE')
        self.assertEqual(database.read_bytes(), before)
        connection = sqlite3.connect(str(database))
        try:
            connection.execute('UPDATE topics SET leader_session_id=?, leader_since=?',
                               (OWNER, datetime.now(timezone.utc).isoformat()))
            connection.commit()
        finally:
            connection.close()
        before = database.read_bytes()
        report = rep.diagnose(self.root, TOPIC, db=database)
        self.assertEqual(report['lease']['state'], 'BOUND')
        self.assertEqual(database.read_bytes(), before)

    def test_process_status_is_written_outside_page(self):
        self.acked()
        page_before = rep.page_path(self.root, TOPIC).read_bytes()
        result = rep.write_process_status(self.root, TOPIC)
        self.assertEqual(rep.page_path(self.root, TOPIC).read_bytes(), page_before)
        self.assertNotIn('心跳', page_before.decode('utf-8'))
        path = rep.process_status_path(self.root, TOPIC)
        self.assertTrue(path.is_file())
        self.assertEqual(rep.relative(self.root, path),
                         '.autoresearch/state/process_status/t1.json')
        projection = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(projection['page']['delivery'], '已验收')
        self.assertIn('heartbeat', projection)
        self.assertFalse(projection['runtime_verified'])
        self.assertTrue(rep.process_status_note_path(self.root, TOPIC).is_file())
        self.assertEqual(result['written']['json'],
                         '.autoresearch/state/process_status/t1.json')
        self.assertEqual(rep.page_rel(TOPIC), 'topics/t1/STATUS.md')

    def test_heartbeat_is_not_research_progress(self):
        self.acked()
        page_sha = rep.page_sha256(self.root, TOPIC)
        receipt_version = json.loads(
            rep.receipt_path(self.root, TOPIC).read_text(encoding='utf-8'))['version']
        first = rep.write_process_status(self.root, TOPIC)
        second = rep.write_process_status(self.root, TOPIC)
        self.assertEqual(rep.page_sha256(self.root, TOPIC), page_sha)
        self.assertEqual(json.loads(
            rep.receipt_path(self.root, TOPIC).read_text(encoding='utf-8'))['version'],
            receipt_version)
        self.assertEqual(second['heartbeat']['previous_at'], first['projected_at'])
        state = rep.check(self.root, TOPIC)
        self.assertTrue(state['ok'], state['problems'])
        self.assertEqual(state['receipt']['version'], receipt_version)
        documentation = rep.documentation_state(self.root, TOPIC)
        self.assertEqual(documentation['state'], '已验收')
        self.assertIn('心跳与 mtime 不参与', documentation['note'])


# ---------------------------------------------------------------------------
# 安全停与隔离
# ---------------------------------------------------------------------------


class SafetyTests(ReportingTestCase):
    def test_document_failure_keeps_exit_code(self):
        result = rep.document_without_blocking(
            rep.acknowledge, self.root, TOPIC, 'session-scribe-2', writer_role=rep.WRITER_ROLE)
        self.assertFalse(result['ok'])
        self.assertFalse(result['blocked_stop'])
        self.assertTrue(result['exit_code_unchanged'])
        self.assertIn('还没有供料包', result['error'])
        self.assertEqual(rep.exit_code_with_documentation(3, result), 3)
        self.assertEqual(rep.exit_code_with_documentation(0, {'ok': True}), 0)

    def test_document_success_carries_result(self):
        self.fx.requirement()
        result = rep.document_without_blocking(self.fx.prepare)
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['topic'], TOPIC)

    def test_run_end_is_not_report_delivery(self):
        rep.record_event(self.root, TOPIC, EXP, 'completed', 'evt-1',
                         self.fx.source('records/run.md'), scope='run', reason='单臂跑完')
        state = rep.run_report_state(self.root, TOPIC)
        self.assertTrue(state['run_ended'])
        self.assertFalse(state['report_delivered'])
        self.assertEqual(state['run_reason'], '单臂跑完')
        self.assertIn('运行结束不等于报告交付', state['next_action'])

    def test_failed_page_records_reason_and_retry_state(self):
        self.fx.requirement()
        self.fx.prepare()
        result = rep.failed(self.root, TOPIC, 'scribe 会话中断')
        self.assertEqual(result['state'], 'pending_writer_retry')
        self.assertEqual(result['attempts'], 1)
        state = rep.check(self.root, TOPIC)
        self.assertFalse(state['ok'])
        self.assertTrue(any('pending_writer_retry' in problem for problem in state['problems']))

    def test_topic_and_source_isolation(self):
        for topic in ('../escape', 'a/b', '', '..'):
            with self.assertRaises(rep.ReportingError):
                rep.check(self.root, topic)
        outside = pathlib.Path(self._temp.name) / 'outside.md'
        outside.write_text('越界来源\n', encoding='utf-8')
        with self.assertRaises(rep.ReportingError) as caught:
            rep.prepare(self.root, TOPIC, extra_sources=[str(outside)])
        self.assertIn('不在工作区内', str(caught.exception))


# ---------------------------------------------------------------------------
# 真实 provision_topic → prepare 集成
# ---------------------------------------------------------------------------


class ProvisionedWorkspaceTests(unittest.TestCase):
    """真实 `workspace.provision_topic` → `reporting.prepare` 集成：不手工复制任何文件。

    覆盖目录孤岛：专题正文由 provision 生成在 `topics/<topic>/`，reporting 默认扫那里，
    人读页也落同一目录；机器供料/回执留在隐藏状态目录。
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = pathlib.Path(self._temp.name) / 'workspace'
        self.root.mkdir()
        (self.root / '.autoresearch').mkdir()

    def provision(self):
        if shutil.which('git') is None:
            self.skipTest('git 不可用，无法 provision_topic')
        from kernel.autoresearch.workspace import provision_topic
        return provision_topic(self.root, topic=TOPIC, objective='提高同题单通过数',
                               requirements='用户要求：先小样本验证机制',
                               owner_session='session-owner-9', create_environment=False)

    def test_prepare_reads_provisioned_topic_without_manual_copy(self):
        self.provision()
        prepared = rep.prepare(self.root, TOPIC)
        files = prepared['sources']['files']
        for rel in ('topics/t1/TOPIC.md', 'topics/t1/OBJECTIVE.md',
                    'topics/t1/REQUIREMENTS.md', 'topics/t1/sources.json'):
            self.assertIn(rel, files)
        feed = rep.feed_path(self.root, TOPIC).read_text(encoding='utf-8')
        self.assertIn('用户要求：先小样本验证机制', feed)
        self.assertIn('提高同题单通过数', feed)
        self.assertIn('topics/t1/sources.json', feed)
        self.assertEqual(rep.page_rel(TOPIC), 'topics/t1/STATUS.md')
        self.assertTrue(rep.feed_path(self.root, TOPIC).is_file())
        self.assertFalse((rep.machine_dir(self.root, TOPIC) / 'TOPIC.md').exists())

    def test_manifest_is_hashed_and_out_of_bounds_refused(self):
        self.provision()
        manifest = self.root / 'topics/t1/sources.json'
        self.assertIn('topics/t1/sources.json', rep.source_snapshot(self.root, TOPIC)['files'])
        before = rep.source_snapshot(self.root, TOPIC)['fingerprint']
        manifest.write_text(json.dumps(['topics/t1/TOPIC.md'], ensure_ascii=False),
                            encoding='utf-8')
        self.assertNotEqual(before, rep.source_snapshot(self.root, TOPIC)['fingerprint'])
        manifest.write_text(json.dumps(['../outside.md']), encoding='utf-8')
        with self.assertRaises(rep.ReportingError) as caught:
            rep.source_snapshot(self.root, TOPIC)
        self.assertIn('越界', str(caught.exception))

    def test_manifest_adds_paths_outside_auto_patterns(self):
        self.provision()
        extra = self.root / 'topics/t1/records/run.md'
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text('运行记录\n', encoding='utf-8')
        (self.root / 'topics/t1/sources.json').write_text(
            json.dumps(['topics/t1/TOPIC.md', 'topics/t1/records/run.md']), encoding='utf-8')
        self.assertIn('topics/t1/records/run.md',
                      rep.source_snapshot(self.root, TOPIC)['files'])

    def test_manifest_missing_file_is_blocker(self):
        self.provision()
        (self.root / 'topics/t1/sources.json').write_text(
            json.dumps(['topics/t1/TOPIC.md', 'topics/t1/NOT_HERE.md']), encoding='utf-8')
        prepared = rep.prepare(self.root, TOPIC)
        self.assertEqual(prepared['task']['state'], 'pending_owner')
        self.assertTrue(any('NOT_HERE' in item['item'] for item in prepared['missing']))


# ---------------------------------------------------------------------------
# 真实 DSHHost 集成
# ---------------------------------------------------------------------------


class HostIntegrationTests(ReportingTestCase):
    """真实 `DSHHost` 集成：用合成宿主日志走真核路径，不是「调一下报个错」。"""

    def verified_scenario(self, **log_options):
        prepared = self.prepared()
        self.fx.page()
        page_value = (self.fx.human / 'STATUS.md').read_text(encoding='utf-8')
        logs = host_logs(prepared['prepared_id'], rep.page_rel(TOPIC), page_value, **log_options)
        host_root = pathlib.Path(self._temp.name) / 'host'
        return prepared, make_host(host_root, self.root, logs)

    def test_ack_verified_by_real_dshhost(self):
        _, host = self.verified_scenario()
        receipt = self.fx.ack('child1', host_verifier=host, owner_session='owner',
                              proof={'session_id': 'child1', 'seq': 5})
        self.assertEqual(receipt['verification'], 'verified')
        self.assertTrue(receipt['session_verified'])
        self.assertTrue(receipt['dispatch_verified'])
        self.assertFalse(receipt['delivery_verified'])
        self.assertEqual(receipt['writer_session_source'], 'host_verifier')
        self.assertEqual(receipt['proof_verified_by'], 'host_dispatch_receipt')
        self.assertEqual(receipt['content_bound'], 'host_replay')
        self.assertEqual(receipt['proof'], {'session_id': 'child1', 'seq': 5})
        evidence = receipt['host_evidence']
        self.assertEqual(evidence['source'], 'injected')
        self.assertEqual(evidence['session_id'], 'session-child1')
        self.assertEqual(evidence['tool_call_id'], 'call-dispatch')
        self.assertEqual(evidence['page_sha256'], rep.page_sha256(self.root, TOPIC))
        self.assertEqual(evidence['delivery'], {'delivered': False, 'channel': None})
        self.assertTrue(evidence['tool_calls'])
        state = rep.check(self.root, TOPIC)
        self.assertTrue(state['ok'], state['problems'])
        self.assertEqual(state['receipt']['verification'], 'verified')
        self.assertFalse(any('declared' in warning for warning in state['warnings']))

    def test_tampered_page_after_host_write_is_rejected(self):
        _, host = self.verified_scenario()
        page = self.fx.human / 'STATUS.md'
        page.write_text(page.read_text(encoding='utf-8') + '\n- 事后偷偷加的一行\n',
                        encoding='utf-8')
        with self.assertRaises(ValueError) as caught:
            self.fx.ack('child1', host_verifier=host, owner_session='owner',
                        proof={'session_id': 'child1', 'seq': 5})
        self.assertIn('可重演', str(caught.exception))
        self.assertFalse(rep.receipt_path(self.root, TOPIC).is_file())

    def test_wrong_proof_is_rejected(self):
        _, host = self.verified_scenario()
        for seq in (99, 4):
            with self.subTest(seq=seq), self.assertRaises(ValueError) as caught:
                self.fx.ack('child1', host_verifier=host, owner_session='owner',
                            proof={'session_id': 'child1', 'seq': seq})
            message = str(caught.exception)
            self.assertTrue('宿主日志' in message or '可见消息' in message, message)
        self.assertFalse(rep.receipt_path(self.root, TOPIC).is_file())

    def test_page_never_actually_written_is_rejected(self):
        _, host = self.verified_scenario(write_path='topics/t1/OTHER.md')
        with self.assertRaises(ValueError) as caught:
            self.fx.ack('child1', host_verifier=host, owner_session='owner',
                        proof={'session_id': 'child1', 'seq': 5})
        self.assertIn('可重演', str(caught.exception))
        self.assertFalse(rep.receipt_path(self.root, TOPIC).is_file())

    def test_short_write_not_matching_page_is_rejected(self):
        _, host = self.verified_scenario(write_text='# 只写了个空壳\n')
        with self.assertRaises(ValueError) as caught:
            self.fx.ack('child1', host_verifier=host, owner_session='owner',
                        proof={'session_id': 'child1', 'seq': 5})
        self.assertIn('可重演', str(caught.exception))

    def test_owner_cannot_verify_itself(self):
        _, host = self.verified_scenario()
        with self.assertRaises(ValueError) as caught:
            self.fx.ack('owner', host_verifier=host, owner_session='owner',
                        proof={'session_id': 'owner', 'seq': 1})
        self.assertIn('负责人自己', str(caught.exception))

    def test_cli_proof_path_verified_with_injected_host(self):
        """CLI 走 proof 时确实调用注入的宿主验证器（CLI 内部只换 `_host` 的构造，不改参数面）。"""
        _, host = self.verified_scenario()
        with mock.patch.object(reporting_cli, '_host', return_value=host):
            code, receipt = self.run_cli([
                '--workspace', str(self.root), 'ack', '--topic', TOPIC,
                '--writer-role', 'scribe', '--writer-session', 'child1',
                '--owner-session', 'owner',
                '--proof-json', json.dumps({'session_id': 'child1', 'seq': 5})])
        self.assertEqual(code, 0)
        self.assertEqual(receipt['verification'], 'verified')
        self.assertEqual(receipt['proof_verified_by'], 'host_dispatch_receipt')


class CliHostTests(ReportingTestCase):
    """CLI 接真实宿主：有 proof 就必须走 DSHHost，且不许自选证据根。"""

    def probe(self):
        return f'cli-probe-{os.getpid()}'

    def test_proof_requires_owner_session(self):
        self.prepared()
        self.fx.page()
        probe = self.probe()
        code, payload = self.run_cli(['--workspace', str(self.root), 'ack', '--topic', TOPIC,
                                      '--writer-role', 'scribe', '--writer-session', probe,
                                      '--proof-json', json.dumps({'session_id': probe, 'seq': 5})])
        self.assertEqual(code, 2)
        self.assertIn('owner-session', payload['reason'])
        self.assertFalse(rep.receipt_path(self.root, TOPIC).is_file())

    def test_proof_runs_real_host_and_refuses_without_logs(self):
        """不存在的会话 ⇒ 真宿主拒绝（exit 2，无回执）；不是悄悄退回 declared。"""
        self.prepared()
        self.fx.page()
        probe = self.probe()
        code, payload = self.run_cli(['--workspace', str(self.root), 'ack', '--topic', TOPIC,
                                      '--writer-role', 'scribe', '--writer-session', probe,
                                      '--owner-session', 'cli-owner-probe',
                                      '--proof-json', json.dumps({'session_id': probe, 'seq': 5})])
        self.assertEqual(code, 2)
        self.assertIn('宿主', payload['reason'])
        self.assertFalse(rep.receipt_path(self.root, TOPIC).is_file())

    def test_no_proof_stays_declared(self):
        self.prepared()
        self.fx.page()
        code, receipt = self.run_cli(['--workspace', str(self.root), 'ack', '--topic', TOPIC,
                                      '--writer-role', 'scribe', '--writer-session', 's-child'])
        self.assertEqual(code, 0)
        self.assertEqual(receipt['verification'], 'declared')
        self.assertTrue(receipt['host_payload']['declared_only'])
        self.assertEqual(receipt['host_payload']['proof'],
                         {'session_id': 's-child', 'seq': None})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class CliTests(ReportingTestCase):
    def test_each_command_help(self):
        for command in ('prepare', 'check', 'ack', 'event', 'round-check', 'diagnose'):
            buffer = io.StringIO()
            with self.assertRaises(SystemExit) as caught:
                with contextlib.redirect_stdout(buffer):
                    reporting_cli.main([command, '--help'])
            self.assertEqual(caught.exception.code, 0, command)
            self.assertIn('--topic', buffer.getvalue(), command)

    def test_round_trip(self):
        source = self.fx.source('records/run.md')
        code, payload = self.run_cli(['--workspace', str(self.root), 'input', '--topic', TOPIC,
                                      '--kind', 'user_requirement', '--text', '用户要求：验证改动',
                                      '--source', source['path'], '--locator', 'L1'])
        self.assertEqual(code, 0)
        self.assertFalse(payload['idempotent'])
        code, _ = self.run_cli(['--workspace', str(self.root), 'event', '--topic', TOPIC,
                                '--exp', EXP, '--kind', 'running', '--event-id', 'evt-1',
                                '--source', source['path'], '--locator', 'L1'])
        self.assertEqual(code, 0)
        code, _ = self.run_cli(['--workspace', str(self.root), 'event', '--topic', TOPIC,
                                '--exp', EXP, '--kind', 'completed', '--event-id', 'evt-2',
                                '--scope', 'run', '--reason', '单臂跑完',
                                '--source', source['path'], '--locator', 'L1'])
        self.assertEqual(code, 0)
        code, _ = self.run_cli(['--workspace', str(self.root), 'input', '--topic', TOPIC,
                                '--kind', 'leader_conclusion', '--exp', EXP,
                                '--text', '负责人结论：阶段结果不足以判方向',
                                '--source', source['path'], '--locator', 'L2'])
        self.assertEqual(code, 0)
        code, prepared = self.run_cli(['--workspace', str(self.root), 'prepare', '--topic', TOPIC])
        self.assertEqual(code, 0)
        self.assertTrue(prepared['prepared_id'])
        self.fx.page()
        code, receipt = self.run_cli(['--workspace', str(self.root), 'ack', '--topic', TOPIC,
                                      '--writer-role', 'scribe', '--writer-session', 's-child'])
        self.assertEqual(code, 0)
        self.assertEqual(receipt['verification'], 'declared')
        code, state = self.run_cli(['--workspace', str(self.root), 'check', '--topic', TOPIC])
        self.assertEqual(code, 0)
        self.assertTrue(state['ok'])
        code, result = self.run_cli(['--workspace', str(self.root), 'round-check',
                                     '--topic', TOPIC, '--exp', EXP])
        self.assertEqual(code, 9)
        self.assertTrue(any('整轮' in problem for problem in result['problems']))
        code, report = self.run_cli(['--workspace', str(self.root), 'diagnose', '--topic', TOPIC])
        self.assertEqual(code, 9)
        self.assertEqual(report['verdict'], '状态待核')
        code, projection = self.run_cli(['--workspace', str(self.root), 'process-status',
                                         '--topic', TOPIC])
        self.assertEqual(code, 0)
        self.assertIn('written', projection)
        code, failed = self.run_cli(['--workspace', str(self.root), 'fail', '--topic', TOPIC,
                                     '--reason', '写页中断'])
        self.assertEqual(code, 0)
        self.assertEqual(failed['state'], 'pending_writer_retry')

    def test_rejects_non_scribe_role(self):
        buffer = io.StringIO()
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(buffer):
                reporting_cli.main(['--workspace', str(self.root), 'ack', '--topic', TOPIC,
                                    '--writer-role', 'leader',
                                    '--writer-session', 'session-x'])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('invalid choice', buffer.getvalue())

    def test_missing_topic_is_parameter_error(self):
        buffer = io.StringIO()
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(buffer):
                reporting_cli.main(['prepare'])
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
