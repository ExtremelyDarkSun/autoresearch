# GENERATED-BY: scribe handoff regression tests
# LAST-EDIT: 2026-11-13 首版：接料检查复用既有 blocker（缺负责人判断即 pending_owner + ack 拒收）、
#   建议采纳必须有可追溯来源、供料包写入接料四条、CLI handoff 退出码
# PURPOSE: 用合成临时工作区验证 scribe 交接行为：负责人判断缺项返核（不代选下一轮）、
#   采纳建议必须留来源、供料包与 CLI 都能读到同一批判据；⛔ 不新增手工台账、不碰私有源。
# ORIGIN: kernel/contracts/status_page.md §6 接料检查；上游 SCRIBE_STATUS_PROMPT 的接料检查与建议边界
# CUMULATIVE-DELTA: 首版。全部合成材料：临时工作区、合成事件与来源；不连宿主、不发消息、不跑实验。
import hashlib
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from kernel.autoresearch import reporting
from kernel.autoresearch.reporting_cli import main as reporting_main

TOPIC = 'synthetic'
EXP = 'exp1'


def tree_snapshot(root):
    """工作区文件快照（名字 / 字节 / sha256），用来证明只读检查不写盘。"""
    snapshot = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            snapshot[path.relative_to(root).as_posix()] = (len(raw), hashlib.sha256(raw).hexdigest())
    return snapshot


class HandoffCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='handoff-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workspace'
        self.root.mkdir()

    # ---- 夹具 -----------------------------------------------------------

    def source(self, name='evidence.md', locator='L1'):
        path = self.root / name
        if not path.exists():
            path.write_text('# 合成来源\n', encoding='utf-8')
        return {'path': name, 'locator': locator}

    def run_until_owner_judgment_missing(self):
        """负责人只给了用户要求与一条运行事件，还没给判断 —— 正常会被返核。"""
        reporting.record_input(self.root, TOPIC, 'user_requirement', '把通过题数从 3 提到 4',
                               self.source('requirements.md'))
        reporting.record_event(self.root, TOPIC, EXP, 'running', 'E1', self.source())

    # ---- 缺负责人判断 ⇒ 停在负责人身上，scribe 不能被放行 -----------------

    def test_missing_owner_judgment_returned_to_owner(self):
        self.run_until_owner_judgment_missing()
        handoff = reporting.handoff_check(self.root, TOPIC)
        self.assertFalse(handoff['ok'])
        self.assertFalse(handoff['owner_judgment_registered'])
        self.assertEqual(handoff['latest_event'], 'E1')
        self.assertTrue(any('负责人' in item['item'] for item in handoff['blockers']),
                        handoff['blockers'])
        # 既有验收判据（不另建台账）：任务停在 pending_owner，ack 直接拒收。
        prepared = reporting.prepare(self.root, TOPIC)
        self.assertEqual(prepared['task']['state'], 'pending_owner')
        self.assertTrue(any('待负责人分析' in item for item in prepared['task']['missing']))
        with self.assertRaises(reporting.ReportingError) as raised:
            reporting.acknowledge(self.root, TOPIC, 'session-scribe', writer_role='scribe')
        self.assertIn('负责人输入还缺', str(raised.exception))

    def test_owner_judgment_registration_clears_the_check(self):
        self.run_until_owner_judgment_missing()
        reporting.record_input(self.root, TOPIC, 'leader_conclusion',
                               '本轮只证明机制打开，判 H1 仍待检验', self.source('conclusion.md'),
                               exp=EXP)
        handoff = reporting.handoff_check(self.root, TOPIC)
        self.assertTrue(handoff['ok'], handoff['blockers'])
        self.assertTrue(handoff['owner_judgment_registered'])
        prepared = reporting.prepare(self.root, TOPIC)
        self.assertEqual(prepared['task']['state'], 'pending_writer')
        self.assertEqual(prepared['task']['missing'], [])

    def test_handoff_check_is_read_only(self):
        self.run_until_owner_judgment_missing()
        before = tree_snapshot(self.root)
        reporting.handoff_check(self.root, TOPIC)
        self.assertEqual(tree_snapshot(self.root), before)

    # ---- 建议采纳必须留来源（复用 record_input 的来源校验） -----------------

    def test_adopted_suggestion_requires_traceable_source(self):
        with self.assertRaises(reporting.ReportingError):
            reporting.record_input(self.root, TOPIC, 'leader_conclusion', '采纳建议 B',
                                   {'path': 'conclusion.md'})      # 缺 locator
        with self.assertRaises(reporting.ReportingError):
            reporting.record_input(self.root, TOPIC, 'leader_conclusion', '   ',
                                   self.source('conclusion.md'), exp=EXP)  # 空正文
        stored = reporting.record_input(
            self.root, TOPIC, 'leader_conclusion', '采纳建议 B，下一轮按 B 起跑',
            {'session_id': 'session-owner', 'message_id': 'm-1'}, exp=EXP)
        self.assertEqual(stored['input']['source']['verification'], reporting.DECLARED)
        self.assertIn('未独立核验宿主消息', stored['input']['source']['note'])

    # ---- 供料包与 CLI 读到同一批接料判据 --------------------------------

    def test_feed_carries_scribe_handoff_rules(self):
        self.run_until_owner_judgment_missing()
        prepared = reporting.prepare(self.root, TOPIC)
        self.assertEqual(prepared['feed']['path'],
                         f'.autoresearch/state/reporting/{TOPIC}/FEED.md')
        feed = reporting.feed_path(self.root, TOPIC).read_text(encoding='utf-8')
        for token in ('接料检查', '不代选下一轮', '单列', 'leader_conclusion'):
            self.assertIn(token, feed)

    def test_handoff_cli_exit_codes(self):
        self.run_until_owner_judgment_missing()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = reporting_main(['--workspace', str(self.root), 'handoff', '--topic', TOPIC])
        self.assertEqual(code, 9)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload['ok'])
        self.assertTrue(payload['blockers'])

        reporting.record_input(self.root, TOPIC, 'leader_conclusion', '判 H1 待检验',
                               self.source('conclusion.md'), exp=EXP)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = reporting_main(['--workspace', str(self.root), 'handoff', '--topic', TOPIC])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['owner_judgment_registered'])


if __name__ == '__main__':
    unittest.main()
