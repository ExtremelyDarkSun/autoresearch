# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_topics.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import os
import sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest
if __package__:
    from kernel.autoresearch.runtime.store import Store
    from kernel.autoresearch.runtime.topics import Topics, TopicsError, TopicNotFound, TopicConflict, VersionConflict, StaleGeneration, SelfFence, NotLeader, SessionConflict, CheckpointError, ExperimentConflict, PathConflict, BranchConflict, BindingRequired, IdempotencyConflict, CHECKPOINT_FIELDS, normalize_worktree_path, paths_overlap, readonly_board, readonly_topic
else:
    from kernel.autoresearch.runtime.store import Store
    from kernel.autoresearch.runtime.topics import Topics, TopicsError, TopicNotFound, TopicConflict, VersionConflict, StaleGeneration, SelfFence, NotLeader, SessionConflict, CheckpointError, ExperimentConflict, PathConflict, BranchConflict, BindingRequired, IdempotencyConflict, CHECKPOINT_FIELDS, normalize_worktree_path, paths_overlap, readonly_board, readonly_topic
ROOT = Path(__file__).resolve().parents[3]
FULL_CP = dict(summary='完成了一半', completed=['a', 'b'], pending=['c'], inflight=[{'task': 't1', 'status': 'open', 'owner_session_id': 's1', 'evidence': {'trace': 'x.json'}}], next_action='接着写 c', evidence=['evidence/1.log'], extra={'note': '关键节点'})

class TopicsCase(unittest.TestCase):

    def setUp(self):
        temp_root = ROOT / 'temp'
        temp_root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='topics_test_', dir=temp_root)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db_path = self.dir / 'topics.sqlite'
        self.store = Store(self.db_path)
        self.t = Topics(self.store)
        self.wt_a = str(self.dir / 'wt_a')
        self.wt_b = str(self.dir / 'wt_b')
        self.topic_a = self.start_topic('topic-a', '专题甲', self.wt_a, 'topic-a-b1')
        self.topic_b = self.start_topic('topic-b', '专题乙', self.wt_b, 'topic-b-b1')

    def start_topic(self, topic_id, title, worktree, branch):
        topic = self.t.create_topic(topic_id, title=title, objective='离线目标')
        self.t.configure_workspace(topic_id, topic['version'], absolute_path=worktree, branch=branch)
        return self.t.open_session(topic_id, topic_id + '-s1', expected_generation=0)

    def cp(self, topic_id='topic-a', session_id=None, generation=1, **kw):
        payload = copy.deepcopy(FULL_CP)
        payload.update(kw)
        return self.t.write_checkpoint(topic_id, session_id or topic_id + '-s1', generation, **payload)

    def session_rows(self, topic_id=None):
        conn = self.store.connect()
        try:
            sql = 'SELECT * FROM topic_sessions'
            args = ()
            if topic_id:
                sql += ' WHERE topic_id=?'
                args = (topic_id,)
            return [dict(r) for r in conn.execute(sql + ' ORDER BY topic_id,generation', args)]
        finally:
            conn.close()

    def raw(self, sql, args=()):
        conn = self.store.connect()
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

class BootstrapAndTopicTests(TopicsCase):

    def test_bootstrap_order_and_leader(self):
        topic = self.t.get_topic('topic-a')
        self.assertEqual(topic['status'], 'active')
        self.assertEqual(topic['generation'], 1)
        self.assertEqual(topic['leader_session_id'], 'topic-a-s1')
        self.assertEqual(topic['worktree'], str(Path(self.wt_a).resolve()))
        self.assertEqual(topic['branch'], 'topic-a-b1')
        leader = self.t.leader('topic-a')
        self.assertEqual(leader['session_id'], 'topic-a-s1')
        self.assertEqual(leader['generation'], 1)
        self.assertEqual(leader['predecessor_session_id'], None)

    def test_binding_required_before_work(self):
        topic = self.t.create_topic('topic-c', title='无绑定')
        self.assertEqual(topic['worktree'], None)
        with self.assertRaises(BindingRequired):
            self.t.open_session('topic-c', 's1', expected_generation=0)
        self.assertEqual(self.t.check_binding('topic-c')['ok'], False)
        board = readonly_board(self.db_path)
        self.assertIn('topic-c', board['unbound_worktree_topics'])

    def test_configure_workspace_requires_no_live_leader(self):
        with self.assertRaises(SessionConflict):
            self.t.configure_workspace('topic-a', self.t.get_topic('topic-a')['version'], absolute_path=str(self.dir / 'wt_a2'), branch='topic-a-b2')

    def test_configure_workspace_is_version_cas(self):
        topic = self.t.create_topic('topic-d', title='CAS')
        with self.assertRaises(VersionConflict):
            self.t.configure_workspace('topic-d', topic['version'] + 5, absolute_path=str(self.dir / 'wt_d'), branch='topic-d-b1')
        self.assertEqual(self.t.get_topic('topic-d')['worktree'], None)

    def test_editable_fields_need_expected_version(self):
        topic = self.t.get_topic('topic-a')
        updated = self.t.set_topic_fields('topic-a', topic['version'], title='专题甲·改', objective='新目标', completion_criteria='全绿', priority=1)
        self.assertEqual(updated['title'], '专题甲·改')
        self.assertEqual(updated['version'], topic['version'] + 1)
        with self.assertRaises(VersionConflict):
            self.t.set_topic_fields('topic-a', topic['version'], title='过期写')
        self.assertEqual(self.t.get_topic('topic-a')['title'], '专题甲·改')

    def test_created_topic_duplicate_rejected(self):
        with self.assertRaises(TopicConflict):
            self.t.create_topic('topic-a', title='撞号')

    def test_status_change_and_terminal_stops_work(self):
        topic = self.t.get_topic('topic-a')
        with self.assertRaises(TopicConflict):
            self.t.finalize_topic('topic-a', 'completed', topic['version'], actor='main', reason='快捷完成已取消')
        result = self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '达标', 'completed': ['全部做完'], 'pending': [], 'inflight': [], 'next_action': '', 'evidence': ['evidence/round.log']})
        self.assertEqual(result['topic']['status'], 'completed')
        self.assertIsNone(self.t.get_topic('topic-a')['leader_session_id'])
        with self.assertRaises(TopicConflict):
            self.t.open_session('topic-a', 'late', expected_generation=1)
        with self.assertRaises(TopicConflict):
            self.t.finalize_topic('topic-a', 'completed', result['topic']['version'], actor='main', reason='二次快捷完成')

class SessionOwnershipTests(TopicsCase):

    def test_open_session_requires_explicit_expected_generation(self):
        self.t.close_session('topic-a', 'topic-a-s1', 1, reason='释放')
        with self.assertRaises(ValueError):
            self.t.open_session('topic-a', 's2', expected_generation=None)
        self.assertEqual(self.t.get_topic('topic-a')['generation'], 1)
        self.assertIsNone(self.t.leader('topic-a'))
        opened = self.t.open_session('topic-a', 's2', expected_generation=1)
        self.assertEqual(opened['generation'], 2)

    def test_open_session_never_evicts_live_leader(self):
        with self.assertRaises(SessionConflict):
            self.t.open_session('topic-a', 'other', expected_generation=1)
        live = self.t.leader('topic-a')
        self.assertEqual(live['session_id'], 'topic-a-s1')
        self.assertEqual(live['generation'], 1)
        self.assertEqual(self.t.get_topic('topic-a')['generation'], 1)

    def test_self_fence_rejected(self):
        with self.assertRaises(SelfFence):
            self.t.open_session('topic-a', 'topic-a-s1', expected_generation=1)

    def test_stale_generation_rejected_without_side_effects(self):
        before = self.t.get_topic('topic-a')
        with self.assertRaises(StaleGeneration):
            self.t.recover('topic-a', 's-new', expected_generation=0, reason='崩溃', inflight=[])
        with self.assertRaises(StaleGeneration):
            self.cp(generation=7)
        after = self.t.get_topic('topic-a')
        self.assertEqual(after, before)
        self.assertEqual(self.t.checkpoint_count('topic-a'), 0)

    def test_concurrent_open_same_generation_single_winner(self):
        self.t.close_session('topic-a', 'topic-a-s1', 1, reason='释放')

        def attempt(index):
            try:
                return ('ok', self.t.open_session('topic-a', f'race-{index}', expected_generation=1)['generation'])
            except TopicsError as exc:
                return ('rejected', type(exc).__name__)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        winners = [r for r in results if r[0] == 'ok']
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(winners[0][1], 2)
        for status, name in results:
            if status == 'rejected':
                self.assertIn(name, ('StaleGeneration', 'SessionConflict', 'SelfFence', 'NotLeader'))
        rows = self.session_rows('topic-a')
        self.assertEqual(len([r for r in rows if r['status'] == 'open']), 1)
        self.assertEqual(len([r for r in rows if r['generation'] == 2]), 1)

    def test_concurrent_recover_single_generation_bump(self):
        self.t.close_session('topic-a', 'topic-a-s1', 1, reason='释放')

        def attempt(index):
            try:
                return ('ok', self.t.recover('topic-a', f'rec-{index}', expected_generation=1, reason='宿主崩溃', inflight=[]))
            except TopicsError as exc:
                return ('rejected', type(exc).__name__)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        winners = [r for r in results if r[0] == 'ok']
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(self.t.get_topic('topic-a')['generation'], 2)
        self.assertEqual(len([r for r in self.session_rows('topic-a') if r['status'] == 'open']), 1)

class IsolationTests(TopicsCase):

    def test_cross_topic_checkpoint_rejected(self):
        with self.assertRaises(NotLeader):
            self.cp(topic_id='topic-b', session_id='topic-a-s1', generation=1)
        self.assertEqual(self.t.checkpoint_count('topic-b'), 0)

    def test_wrong_generation_is_not_leader(self):
        with self.assertRaises(StaleGeneration):
            self.cp(topic_id='topic-b', session_id='topic-b-s1', generation=99)

    def test_unknown_topic_and_session_rejected(self):
        self.assertIsNone(self.t.get_topic('topic-missing'))
        with self.assertRaises(TopicNotFound):
            self.t.open_session('topic-missing', 's', expected_generation=0)
        with self.assertRaises(TopicNotFound):
            self.t.write_checkpoint('topic-missing', 's', 1, summary='x')
        with self.assertRaises(NotLeader):
            self.cp(session_id='someone-else', generation=1)

    def test_sessions_and_bindings_are_per_topic(self):
        self.assertEqual([s['session_id'] for s in self.t.list_sessions('topic-a')], ['topic-a-s1'])
        self.assertEqual([s['session_id'] for s in self.t.list_sessions('topic-b')], ['topic-b-s1'])
        self.assertEqual([w['topic_id'] for w in self.t.list_worktrees('topic-a')], ['topic-a'])
        self.assertEqual([b['branch'] for b in self.t.list_branches('topic-b')], ['topic-b-b1'])

class HandoffTests(TopicsCase):

    def handoff_full(self, **kw):
        spec = copy.deepcopy(FULL_CP)
        spec.update(kw)
        return spec

    def test_handoff_opens_next_generation_and_keeps_checkpoint(self):
        result = self.t.handoff('topic-a', 'topic-a-s1', 1, next_session_id='topic-a-s2', checkpoint=self.handoff_full(kind='handoff'))
        self.assertEqual(result['closed']['status'], 'handed_off')
        self.assertEqual(result['opened']['session_id'], 'topic-a-s2')
        self.assertEqual(result['opened']['generation'], 2)
        self.assertEqual(result['opened']['predecessor_session_id'], 'topic-a-s1')
        self.assertEqual(result['generation'], 2)
        cp = self.t.latest_checkpoint('topic-a')
        self.assertEqual(cp['id'], result['checkpoint_id'])
        self.assertEqual(cp['kind'], 'handoff')
        self.assertEqual(cp['summary'], FULL_CP['summary'])
        self.assertEqual(cp['inflight'], FULL_CP['inflight'])
        with self.assertRaises(NotLeader):
            self.cp(generation=2)

    def test_handoff_release_only_leaves_no_session(self):
        result = self.t.handoff('topic-a', 'topic-a-s1', 1, checkpoint=self.handoff_full())
        self.assertTrue(result['released'])
        self.assertIsNone(result['opened'])
        self.assertIsNone(self.t.leader('topic-a'))
        self.assertIsNone(self.t.get_topic('topic-a')['leader_session_id'])
        self.assertEqual(len([r for r in self.session_rows('topic-a') if r['status'] == 'open']), 0)
        self.assertEqual([r['session_id'] for r in self.session_rows('topic-a')], ['topic-a-s1'])
        claimed = self.t.open_session('topic-a', 'topic-a-s3', expected_generation=1)
        self.assertEqual(claimed['generation'], 2)

    def test_handoff_requires_summary_and_inflight(self):
        with self.assertRaises(CheckpointError):
            self.t.handoff('topic-a', 'topic-a-s1', 1, checkpoint=None)
        with self.assertRaises(CheckpointError):
            self.t.handoff('topic-a', 'topic-a-s1', 1, checkpoint={'kind': 'handoff'})
        with self.assertRaises(CheckpointError):
            self.t.handoff('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '只给摘要'})
        with self.assertRaises(ValueError):
            self.t.handoff('topic-a', 'topic-a-s1', 1, checkpoint={'summary': 's', 'inflight': [], 'unexpected': 1})
        self.assertEqual(self.t.leader('topic-a')['session_id'], 'topic-a-s1')

    def test_handoff_carries_unspecified_fields_from_previous_checkpoint(self):
        self.cp(summary='上一条进展', completed=['已完成一', '已完成二'], pending=['待办一'], next_action='做待办一', evidence=['evidence/a.log'], extra={'round': 3}, inflight=[{'task': 'old-task', 'status': 'open'}])
        result = self.t.handoff('topic-a', 'topic-a-s1', 1, next_session_id='topic-a-s2', checkpoint={'summary': '交接正文', 'inflight': []})
        cp = self.t.latest_checkpoint('topic-a')
        self.assertEqual(cp['id'], result['checkpoint_id'])
        previous = self.t.list_checkpoints('topic-a', limit=2)[1]
        self.assertEqual(previous['summary'], '上一条进展')
        self.assertEqual(cp['summary'], '交接正文')
        self.assertEqual(cp['completed'], ['已完成一', '已完成二'])
        self.assertEqual(cp['pending'], ['待办一'])
        self.assertEqual(cp['next_action'], '做待办一')
        self.assertEqual(cp['evidence'], ['evidence/a.log'])
        self.assertEqual(cp['extra']['round'], 3)
        self.assertEqual(cp['inflight'], [])
        self.assertEqual(result['carried_from_checkpoint_id'], cp['id'] - 1)
        self.assertEqual(cp['extra']['carried_from_checkpoint_id'], cp['id'] - 1)
        self.assertEqual(cp['extra']['handoff_to_session_id'], 'topic-a-s2')
        for field in CHECKPOINT_FIELDS:
            self.assertIn(field, cp)

    def test_handoff_without_previous_checkpoint_fills_empty_structures(self):
        result = self.t.handoff('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '第一次交接', 'inflight': []})
        cp = self.t.latest_checkpoint('topic-a')
        self.assertEqual(cp['id'], result['checkpoint_id'])
        self.assertEqual(cp['completed'], [])
        self.assertEqual(cp['pending'], [])
        self.assertEqual(cp['inflight'], [])
        self.assertEqual(cp['next_action'], '')
        self.assertEqual(cp['evidence'], [])
        self.assertEqual(cp['extra']['carried_from_checkpoint_id'], None)

    def test_handoff_rejects_same_session_and_non_leader(self):
        with self.assertRaises(SessionConflict):
            self.t.handoff('topic-a', 'topic-a-s1', 1, next_session_id='topic-a-s1', checkpoint=self.handoff_full())
        with self.assertRaises(NotLeader):
            self.t.handoff('topic-b', 'topic-a-s1', 1, checkpoint=self.handoff_full())

class RecoverTests(TopicsCase):

    def test_recover_crashed_leader_bumps_generation(self):
        result = self.t.recover('topic-a', 'topic-a-s9', expected_generation=1, reason='宿主进程被杀', inflight=[{'task': 'run-7', 'status': 'open', 'owner_session_id': 'topic-a-s1'}], note='接着核在途作业')
        self.assertEqual(result['generation'], 2)
        self.assertEqual(result['reason'], '宿主进程被杀')
        self.assertEqual(result['inflight'], {'total': 1, 'open': 1, 'released': 0, 'lost': 0})
        rows = {r['session_id']: r for r in self.session_rows('topic-a')}
        self.assertEqual(rows['topic-a-s1']['status'], 'crashed')
        self.assertEqual(rows['topic-a-s1']['ended_reason'], 'recovery: 宿主进程被杀')
        self.assertEqual(rows['topic-a-s9']['status'], 'open')
        self.assertIsNotNone(rows['topic-a-s1']['ended_at'])
        cp = self.t.latest_checkpoint('topic-a')
        self.assertEqual(cp['id'], result['checkpoint_id'])
        self.assertEqual(cp['kind'], 'recovery')
        self.assertEqual(cp['reason'], '宿主进程被杀')
        self.assertEqual(cp['extra']['inflight'], [{'task': 'run-7', 'status': 'open', 'owner_session_id': 'topic-a-s1', 'evidence': None}])
        self.assertEqual(cp['extra']['takeover_from_generation'], 1)
        with self.assertRaises(NotLeader):
            self.cp(generation=2)

    def test_recover_requires_reason_and_inflight(self):
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='   ', inflight=[])
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='崩溃', inflight=None)
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='崩溃', inflight=[{'status': 'open'}])
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='崩溃', inflight=[{'task': 't', 'status': 'unknown'}])
        self.assertEqual(self.t.leader('topic-a')['session_id'], 'topic-a-s1')

    def test_recover_empty_inflight_is_explicit(self):
        result = self.t.recover('topic-a', 'topic-a-s9', expected_generation=1, reason='会话上下文满', inflight=[])
        self.assertEqual(result['inflight'], {'total': 0, 'open': 0, 'released': 0, 'lost': 0})

    def test_recover_from_released_topic(self):
        self.t.close_session('topic-a', 'topic-a-s1', 1, reason='释放')
        result = self.t.recover('topic-a', 'topic-a-s9', expected_generation=1, reason='上一位没留 checkpoint', inflight=[])
        self.assertEqual(result['generation'], 2)
        self.assertIsNotNone(result['checkpoint_id'])

    def test_recover_checkpoint_continues_previous_progress(self):
        long_text = '上一段的详细进展' * 40
        self.cp(summary=long_text, completed=['改完内核', '跑过并发用例'], pending=['补 README', '跑反向对照'], next_action='先补 README', evidence=['evidence/round3.log'], extra={'round': 4, 'owner_note': '别丢'}, inflight=[{'task': 'old-task', 'status': 'open'}])
        result = self.t.recover('topic-a', 'topic-a-s9', expected_generation=1, reason='宿主进程被杀', inflight=[{'task': 'run-9', 'status': 'open'}, {'task': 'run-3', 'status': 'released'}], note='接着跑反向对照')
        cp = self.t.latest_checkpoint('topic-a')
        self.assertEqual(cp['id'], result['checkpoint_id'])
        self.assertEqual(cp['kind'], 'recovery')
        self.assertEqual(cp['completed'], ['改完内核', '跑过并发用例'])
        self.assertEqual(cp['pending'], ['补 README', '跑反向对照'])
        self.assertEqual(cp['next_action'], '先补 README')
        self.assertEqual(cp['evidence'], ['evidence/round3.log'])
        self.assertEqual(cp['extra']['round'], 4)
        self.assertEqual(cp['extra']['owner_note'], '别丢')
        self.assertEqual([i['task'] for i in cp['inflight']], ['run-9', 'run-3'])
        self.assertEqual(cp['extra']['previous_checkpoint_id'], cp['id'] - 1)
        self.assertEqual(cp['extra']['reason'], '宿主进程被杀')
        self.assertEqual(cp['extra']['takeover_from_session_id'], 'topic-a-s1')
        self.assertIn('接续上一条进展', cp['summary'])
        self.assertIn('宿主进程被杀', cp['summary'])
        self.assertIn(long_text, cp['summary'])
        self.assertNotEqual(cp['summary'], 'crash takeover: 宿主进程被杀')
        detail = readonly_topic(self.db_path, 'topic-a', history_limit=1)
        self.assertEqual(detail['checkpoints'][0]['completed'], cp['completed'])
        self.assertEqual(detail['checkpoints'][0]['summary'], cp['summary'])
        self.assertGreater(len(detail['checkpoints'][0]['summary']), 200)

    def test_recover_by_current_owner_is_self_fence(self):
        with self.assertRaises(SelfFence):
            self.t.recover('topic-a', 'topic-a-s1', expected_generation=1, reason='我自己崩了', inflight=[])
        self.assertEqual(self.t.get_topic('topic-a')['generation'], 1)
        self.assertEqual(self.t.leader('topic-a')['session_id'], 'topic-a-s1')
        self.assertEqual(self.t.checkpoint_count('topic-a'), 0)

    def test_recover_without_previous_checkpoint_says_so(self):
        result = self.t.recover('topic-a', 'topic-a-s9', expected_generation=1, reason='第一次就崩', inflight=[])
        cp = self.t.latest_checkpoint('topic-a')
        self.assertEqual(cp['id'], result['checkpoint_id'])
        self.assertEqual(cp['completed'], [])
        self.assertEqual(cp['extra']['previous_checkpoint_id'], None)
        self.assertIn('此前没有 checkpoint', cp['summary'])

    def test_recover_requires_reason_and_inflight(self):
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='   ', inflight=[])
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='崩溃', inflight=None)
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='崩溃', inflight=[{'status': 'open'}])
        with self.assertRaises(ValueError):
            self.t.recover('topic-a', 's9', expected_generation=1, reason='崩溃', inflight=[{'task': 't', 'status': 'unknown'}])
        self.assertEqual(self.t.leader('topic-a')['session_id'], 'topic-a-s1')

class PausedAndFinalizeTests(TopicsCase):

    def pause(self):
        topic = self.t.get_topic('topic-a')
        return self.t.set_topic_status('topic-a', 'paused', topic['version'], session_id='topic-a-s1', generation=1, actor='main', reason='先暂停')

    def test_runtime_status_needs_current_owner(self):
        topic = self.t.get_topic('topic-a')
        with self.assertRaises(TypeError):
            self.t.set_topic_status('topic-a', 'paused', topic['version'], actor='main', reason='没有负责人凭据')
        with self.assertRaises(NotLeader):
            self.t.set_topic_status('topic-a', 'paused', topic['version'], session_id='someone-else', generation=1, actor='main', reason='冒充')
        with self.assertRaises(StaleGeneration):
            self.t.set_topic_status('topic-a', 'paused', topic['version'], session_id='topic-a-s1', generation=9, actor='main', reason='过时世代')
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')

    def test_paused_topic_rejects_new_sessions(self):
        self.pause()
        with self.assertRaises(TopicConflict):
            self.t.open_session('topic-a', 'topic-a-s2', expected_generation=1)
        result = self.t.handoff('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '暂停前交接', 'inflight': []})
        self.assertTrue(result['released'])
        with self.assertRaises(TopicConflict):
            self.t.recover('topic-a', 'topic-a-s9', expected_generation=1, reason='崩了', inflight=[])
        with self.assertRaises(TopicConflict):
            self.t.open_session('topic-a', 'topic-a-s9', expected_generation=1)

    def test_paused_topic_reads_are_fine(self):
        self.pause()
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'paused')
        board = readonly_board(self.db_path)
        self.assertEqual([x['status'] for x in board['topics'] if x['id'] == 'topic-a'], ['paused'])
        self.assertIsNotNone(readonly_topic(self.db_path, 'topic-a')['topic'])

    def test_finalize_rejects_completed_status_even_after_release(self):
        self.t.close_session('topic-a', 'topic-a-s1', 1, reason='释放')
        topic = self.t.get_topic('topic-a')
        self.assertIsNone(topic['leader_session_id'])
        with self.assertRaises(TopicConflict) as caught:
            self.t.finalize_topic('topic-a', 'completed', topic['version'], reason='想快捷完成', actor='main')
        self.assertIn('complete_topic', str(caught.exception))
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')

    def test_finalize_rejects_completed_when_pending_or_no_evidence(self):
        self.cp(pending=['遗留一'], evidence=['evidence/a.log'], inflight=[{'task': 'run-1', 'status': 'released'}])
        topic = self.t.get_topic('topic-a')
        with self.assertRaises(TopicConflict):
            self.t.finalize_topic('topic-a', 'completed', topic['version'], reason='还有 pending', actor='main')
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '还有 pending', 'pending': ['遗留一'], 'inflight': [], 'next_action': '', 'evidence': ['evidence/a.log']})
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '没证据', 'pending': [], 'inflight': [], 'next_action': '', 'evidence': []})
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')
        self.assertEqual(self.t.leader('topic-a')['session_id'], 'topic-a-s1')

    def test_finalize_rejects_open_inflight(self):
        self.cp(inflight=[{'task': 'run-1', 'status': 'open'}])
        self.t.close_session('topic-a', 'topic-a-s1', 1, reason='释放')
        topic = self.t.get_topic('topic-a')
        with self.assertRaises(CheckpointError):
            self.t.finalize_topic('topic-a', 'archived', topic['version'], reason='想归档', actor='main')
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')

    def test_finalize_rejects_lost_inflight(self):
        self.cp(inflight=[{'task': 'run-1', 'status': 'lost'}])
        self.t.close_session('topic-a', 'topic-a-s1', 1, reason='释放')
        topic = self.t.get_topic('topic-a')
        with self.assertRaises(CheckpointError):
            self.t.finalize_topic('topic-a', 'archived', topic['version'], reason='丢掉的那条还没处置', actor='main')
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')

    def test_complete_then_archive(self):
        self.cp(inflight=[{'task': 'run-1', 'status': 'open'}])
        self.t.recover('topic-a', 'topic-a-s2', expected_generation=1, reason='核查在途', inflight=[{'task': 'run-1', 'status': 'released'}])
        self.t.handoff('topic-a', 'topic-a-s2', 2, checkpoint={'summary': '在途已结清', 'inflight': [{'task': 'run-1', 'status': 'released', 'owner_session_id': 'topic-a-s2'}]})
        latest = self.t.latest_checkpoint('topic-a')
        self.assertEqual([i['status'] for i in latest['inflight']], ['released'])
        claimed = self.t.open_session('topic-a', 'topic-a-s3', expected_generation=2)
        result = self.t.complete_topic('topic-a', 'topic-a-s3', claimed['generation'], checkpoint={'summary': '全部结清', 'pending': [], 'inflight': [], 'next_action': '', 'evidence': ['evidence/final.log']})
        self.assertEqual(result['topic']['status'], 'completed')
        archived = self.t.finalize_topic('topic-a', 'archived', result['topic']['version'], reason='结题后归档', actor='main')
        self.assertEqual(archived['status'], 'archived')
        board = readonly_board(self.db_path)
        self.assertNotIn('topic-a', [x['id'] for x in board['topics']])
        board_all = self.t.overview(include_archived=True)
        self.assertIn('topic-a', [x['id'] for x in board_all['topics']])
        with self.assertRaises(TopicConflict):
            self.t.finalize_topic('topic-a', 'archived', archived['version'], reason='重复收尾', actor='main')
        with self.assertRaises(TopicConflict):
            self.t.open_session('topic-a', 'late', expected_generation=archived['generation'])

    def test_finalize_rejects_live_leader(self):
        topic = self.t.get_topic('topic-a')
        with self.assertRaises(SessionConflict):
            self.t.finalize_topic('topic-a', 'archived', topic['version'], reason='还没释放', actor='main')

    def test_finalize_rejects_other_statuses(self):
        topic = self.t.get_topic('topic-a')
        for status in ('paused', 'active'):
            with self.assertRaises(TopicConflict):
                self.t.finalize_topic('topic-a', status, topic['version'], reason='不是归档', actor='main')
        with self.assertRaises(TopicConflict) as caught:
            self.t.finalize_topic('topic-a', 'completed', topic['version'], reason='快捷完成已取消', actor='main')
        self.assertIn('complete_topic', str(caught.exception))

    def test_set_topic_status_cannot_complete(self):
        topic = self.t.get_topic('topic-a')
        with self.assertRaises(TopicConflict):
            self.t.set_topic_status('topic-a', 'completed', topic['version'], session_id='topic-a-s1', generation=1, actor='main', reason='绕过收尾')

class CompleteTopicTests(TopicsCase):
    GOOD = {'summary': '目标达成', 'completed': ['内核', '测试'], 'pending': [], 'inflight': [], 'next_action': '', 'evidence': ['evidence/final-report.log'], 'extra': {'round': 9}}

    def cp_full(self, **kw):
        spec = copy.deepcopy(self.GOOD)
        spec.update(kw)
        return spec

    def test_complete_topic_closes_session_and_sets_completed(self):
        result = self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=self.cp_full())
        self.assertEqual(result['topic']['status'], 'completed')
        self.assertEqual(result['closed_session']['status'], 'closed')
        self.assertEqual(result['closed_session']['ended_reason'], 'completed')
        self.assertIsNone(result['topic']['leader_session_id'])
        cp = result['checkpoint']
        self.assertEqual(cp['kind'], 'close')
        self.assertEqual(cp['evidence'], ['evidence/final-report.log'])
        self.assertEqual(cp['extra']['completion_declaration'], True)
        self.assertEqual(self.t.latest_checkpoint('topic-a')['id'], cp['id'])
        with self.assertRaises(TopicConflict):
            self.t.open_session('topic-a', 'topic-a-s2', expected_generation=result['topic']['generation'])
        with self.assertRaises(TopicConflict):
            self.t.recover('topic-a', 'topic-a-s9', expected_generation=result['topic']['generation'], reason='想接着干', inflight=[])
        with self.assertRaises(NotLeader):
            self.t.write_checkpoint('topic-a', 'topic-a-s1', 1, summary='结题后还想写')

    def test_complete_topic_rejects_unfinished_work(self):
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=self.cp_full(pending=['还没做完的活']))
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=self.cp_full(inflight=[{'task': 'run-1', 'status': 'open'}]))
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=self.cp_full(inflight=[{'task': 'run-1', 'status': 'lost'}]))
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=self.cp_full(next_action='下一步还要做'))
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=self.cp_full(evidence=[]))
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')
        self.assertEqual(self.t.leader('topic-a')['session_id'], 'topic-a-s1')
        self.assertEqual(self.t.checkpoint_count('topic-a'), 0)

    def test_complete_topic_cannot_hide_work_by_omitting_fields(self):
        self.cp(pending=['遗留一'], inflight=[{'task': 'run-1', 'status': 'open'}], evidence=['evidence/a.log'])
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '想结题'})
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')

    def test_complete_topic_requires_current_owner_and_evidence(self):
        with self.assertRaises(NotLeader):
            self.t.complete_topic('topic-a', 'topic-b-s1', 1, checkpoint=self.cp_full())
        with self.assertRaises(StaleGeneration):
            self.t.complete_topic('topic-a', 'topic-a-s1', 5, checkpoint=self.cp_full())
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=None)

    def test_archived_after_completion_is_allowed_once(self):
        result = self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint=self.cp_full())
        version = result['topic']['version']
        archived = self.t.finalize_topic('topic-a', 'archived', version, reason='结题后归档', actor='main')
        self.assertEqual(archived['status'], 'archived')
        with self.assertRaises(TopicConflict):
            self.t.finalize_topic('topic-a', 'archived', archived['version'], reason='重复归档', actor='main')
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'archived')

class CheckpointTests(TopicsCase):

    def test_checkpoint_keeps_all_fields(self):
        cp = self.cp()
        for field in CHECKPOINT_FIELDS:
            self.assertIn(field, cp)
        self.assertEqual(cp['summary'], FULL_CP['summary'])
        self.assertEqual(cp['completed'], FULL_CP['completed'])
        self.assertEqual(cp['pending'], FULL_CP['pending'])
        self.assertEqual(cp['next_action'], FULL_CP['next_action'])
        self.assertEqual(cp['evidence'], FULL_CP['evidence'])
        self.assertEqual(cp['extra'], FULL_CP['extra'])
        self.assertEqual(cp['inflight'][0]['task'], 't1')
        self.assertEqual(cp['inflight'][0]['evidence'], {'trace': 'x.json'})
        self.assertEqual(cp['inflight_open'], 1)
        self.assertEqual(cp['inflight_total'], 1)
        self.assertEqual(len(cp['payload_sha256']), 64)

    def test_checkpoint_missing_fields_become_empty_not_missing(self):
        cp = self.t.write_checkpoint('topic-a', 'topic-a-s1', 1, summary='只给摘要')
        self.assertEqual(cp['completed'], [])
        self.assertEqual(cp['pending'], [])
        self.assertEqual(cp['inflight'], [])
        self.assertEqual(cp['next_action'], '')
        self.assertEqual(cp['evidence'], [])
        self.assertEqual(cp['extra'], {})

    def test_inflight_without_status_defaults_to_open(self):
        cp = self.t.write_checkpoint('topic-a', 'topic-a-s1', 1, summary='在途未标注', inflight=[{'task': 'run-unmarked'}])
        self.assertEqual(cp['inflight'][0]['status'], 'open')
        self.assertEqual(cp['inflight_open'], 1)
        with self.assertRaises(CheckpointError):
            self.t.complete_topic('topic-a', 'topic-a-s1', 1, checkpoint={'summary': '想结题', 'evidence': ['evidence/a.log'], 'inflight': [{'task': 'run-unmarked'}]})
        self.assertEqual(self.t.get_topic('topic-a')['status'], 'active')

    def test_checkpoint_is_append_only(self):
        first = self.cp(summary='关键节点一')
        second = self.cp(summary='关键节点二', extra={'round': 2})
        self.assertGreater(second['id'], first['id'])
        self.assertEqual(self.t.checkpoint_count('topic-a'), 2)
        conn = self.store.connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE topic_checkpoints SET summary=? WHERE id=?', ('篡改', first['id']))
        finally:
            conn.close()
        conn = self.store.connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM topic_checkpoints WHERE id=?', (first['id'],))
        finally:
            conn.close()
        self.assertEqual(len(self.t.list_checkpoints('topic-a')), 2)

    def test_checkpoint_summary_is_never_truncated(self):
        long_summary = '交接正文' * 400
        cp = self.cp(summary=long_summary)
        self.assertEqual(len(cp['summary']), len(long_summary))
        detail = readonly_topic(self.db_path, 'topic-a', history_limit=5)
        self.assertEqual(detail['checkpoints'][0]['summary'], long_summary)
        self.assertEqual(len(detail['checkpoints'][0]['summary']), len(long_summary))

    def test_checkpoint_pagination_is_keyset(self):
        ids = [self.cp(summary=f'节点 {i}')['id'] for i in range(5)]
        page1 = self.t.list_checkpoints('topic-a', limit=2)
        self.assertEqual([c['id'] for c in page1], [ids[4], ids[3]])
        page2 = self.t.list_checkpoints('topic-a', limit=2, before_id=page1[-1]['id'])
        self.assertEqual([c['id'] for c in page2], [ids[2], ids[1]])
        detail = readonly_topic(self.db_path, 'topic-a', history_limit=2, history_before=page1[-1]['id'])
        self.assertEqual([c['id'] for c in detail['checkpoints']], [ids[2], ids[1]])
        self.assertTrue(detail['has_more_checkpoints'])

    def test_overview_keeps_long_history_off_the_front_page(self):
        for i in range(30):
            self.cp(summary=f'第 {i} 个关键节点 ' + '细节' * 200)
        board = readonly_board(self.db_path)
        item = [x for x in board['topics'] if x['id'] == 'topic-a'][0]
        self.assertEqual(item['checkpoint_count'], 30)
        self.assertEqual(len(item['recent_checkpoints']), 1)
        self.assertLessEqual(len(item['recent_checkpoints'][0]['summary']), 201)
        self.assertNotIn('completed', item['recent_checkpoints'][0])

class ExperimentLinkTests(TopicsCase):

    def test_register_experiment_registers_and_links_in_one_transaction(self):
        result = self.t.register_experiment('topic-a', 'topic-a-s1', 1, 'exp-001', goal='首个实验')
        self.assertEqual(result['exp_id'], 'exp-001')
        self.assertTrue(result['linked']['active'])
        self.assertEqual(result['linked']['topic_id'], 'topic-a')
        self.assertEqual(result['registered']['id'], 'exp-001')
        self.assertEqual(self.t.list_experiments('topic-a')[0]['exp_id'], 'exp-001')

    def test_register_experiment_rolls_back_when_link_is_refused(self):
        self.t.register_experiment('topic-a', 'topic-a-s1', 1, 'exp-777')
        before = self.raw('SELECT id FROM experiments ORDER BY id')
        with self.assertRaises(ExperimentConflict):
            self.t.register_experiment('topic-b', 'topic-b-s1', 1, 'exp-777')
        after = self.raw('SELECT id FROM experiments ORDER BY id')
        self.assertEqual(before, after)
        self.assertEqual([e['topic_id'] for e in self.t.list_experiments('exp-777')], [])

    def test_link_requires_registered_experiment(self):
        with self.assertRaises(ExperimentConflict):
            self.t.link_experiment('topic-a', 'topic-a-s1', 1, 'exp-404')

    def test_link_first_binding_inserts_row_and_never_returns_none(self):
        self.store.ensure_experiment('exp-store-1', 'Store 里已有的实验')
        linked = self.t.link_experiment('topic-a', 'topic-a-s1', 1, 'exp-store-1', note='首次绑定')
        self.assertIsNotNone(linked)
        self.assertEqual(linked['exp_id'], 'exp-store-1')
        self.assertEqual(linked['topic_id'], 'topic-a')
        self.assertTrue(linked['active'])
        self.assertEqual(linked['bound_session_id'], 'topic-a-s1')
        self.assertEqual(len(self.raw("SELECT exp_id FROM topic_experiments WHERE active=1 AND exp_id='exp-store-1'")), 1)
        self.assertEqual([e['exp_id'] for e in self.t.list_experiments('topic-a')], ['exp-store-1'])

    def test_link_existing_store_experiment_then_replay(self):
        self.store.ensure_experiment('exp-store-2')
        first = self.t.link_experiment('topic-b', 'topic-b-s1', 1, 'exp-store-2')
        self.assertIsNotNone(first)
        replay = self.t.link_experiment('topic-b', 'topic-b-s1', 1, 'exp-store-2')
        self.assertEqual(first, replay)
        with self.assertRaises(ExperimentConflict):
            self.t.link_experiment('topic-a', 'topic-a-s1', 1, 'exp-store-2')

    def test_experiment_cannot_be_double_booked(self):
        self.t.register_experiment('topic-a', 'topic-a-s1', 1, 'exp-002')
        with self.assertRaises(ExperimentConflict):
            self.t.link_experiment('topic-b', 'topic-b-s1', 1, 'exp-002')
        with self.assertRaises(ExperimentConflict):
            self.t.register_experiment('topic-b', 'topic-b-s1', 1, 'exp-002')
        self.assertEqual([e['exp_id'] for e in self.t.list_experiments('topic-b')], [])

    def test_unlinked_experiment_cannot_move_to_another_topic(self):
        self.t.register_experiment('topic-a', 'topic-a-s1', 1, 'exp-003')
        self.t.unlink_experiment('topic-a', 'topic-a-s1', 1, 'exp-003', reason='改路线')
        self.assertFalse(self.t.list_experiments('topic-a')[0]['active'])
        with self.assertRaises(ExperimentConflict):
            self.t.link_experiment('topic-b', 'topic-b-s1', 1, 'exp-003')
        back = self.t.link_experiment('topic-a', 'topic-a-s1', 1, 'exp-003')
        self.assertTrue(back['active'])

    def test_experiment_write_requires_current_owner(self):
        with self.assertRaises(NotLeader):
            self.t.link_experiment('topic-a', 'topic-b-s1', 1, 'exp-005')
        with self.assertRaises(NotLeader):
            self.t.register_experiment('topic-a', 'topic-b-s1', 1, 'exp-005')
        self.assertEqual(self.t.list_experiments('exp-005'), [])

    def test_sync_experiments_is_read_only(self):
        self.t.register_experiment('topic-a', 'topic-a-s1', 1, 'exp-006')
        before = self.raw('SELECT * FROM experiments ORDER BY id')
        report = self.t.sync_experiments(['exp-006', 'exp-absent'])
        after = self.raw('SELECT * FROM experiments ORDER BY id')
        self.assertEqual(before, after)
        self.assertEqual(report, {'registered': ['exp-006'], 'missing': ['exp-absent']})

class WorkspaceBindingTests(TopicsCase):

    def test_same_path_across_topics_rejected(self):
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, self.wt_a)

    def test_nested_path_across_topics_rejected_both_directions(self):
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, str(Path(self.wt_a) / 'inner'))
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, str(Path(self.wt_a).parent))

    def test_sibling_path_is_fine(self):
        row = self.t.bind_worktree('topic-b', 'topic-b-s1', 1, str(self.dir / 'wt_bb'))
        self.assertTrue(row['active'])
        paths = {w['path'] for w in self.t.list_worktrees(active_only=True)}
        self.assertEqual(paths, {str(Path(self.wt_a).resolve()), str((self.dir / 'wt_bb').resolve())})

    def test_relative_path_rejected(self):
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, 'wt_relative')

    def test_filesystem_root_is_refused(self):
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, Path.cwd().anchor)
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, '/')
        self.assertEqual(self.t.get_topic('topic-b')['worktree'], str(Path(self.wt_b).resolve()))

    def test_root_path_overlaps_any_descendant(self):
        anchor = Path.cwd().anchor
        self.assertTrue(paths_overlap(anchor, str(Path(anchor) / 'topic' / 'wt')))
        self.assertTrue(paths_overlap(anchor, anchor))
        self.assertTrue(paths_overlap(anchor, anchor + '/'))
        self.assertTrue(paths_overlap(self.wt_a, str(Path(self.wt_a) / 'inner')))
        self.assertTrue(paths_overlap(str(Path(self.wt_a).parent), self.wt_a))
        self.assertFalse(paths_overlap(self.wt_a, self.wt_b))

    def test_descendant_of_bound_path_rejected(self):
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, str(Path(self.wt_a) / 'child' / 'grandchild'))
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, str(Path(self.wt_a) / 'child'))

    def test_root_bound_topic_blocks_descendant_and_same_path(self):
        anchor = Path.cwd().anchor
        conn = self.store.connect()
        try:
            conn.execute("UPDATE topic_worktrees SET path=? WHERE active=1 AND topic_id='topic-a'", (anchor,))
            conn.execute("UPDATE topics SET worktree=? WHERE id='topic-a'", (anchor,))
        finally:
            conn.close()
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, str(Path(anchor) / 'some' / 'wt'))
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, anchor)

    def test_trailing_separator_forms_are_the_same_path(self):
        path, key = normalize_worktree_path(self.wt_a)
        self.assertEqual(key, key.rstrip('/'))
        with self.assertRaises(PathConflict):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 1, self.wt_a + '/')

    def test_rebinding_within_topic_keeps_history(self):
        row = self.t.bind_worktree('topic-a', 'topic-a-s1', 1, str(self.dir / 'wt_a2'))
        self.assertEqual(self.t.get_topic('topic-a')['worktree'], row['path'])
        history = self.t.list_worktrees('topic-a')
        self.assertEqual(len(history), 2)
        self.assertEqual([h['active'] for h in history].count(True), 1)

    def test_previously_released_path_can_be_reused(self):
        self.t.bind_worktree('topic-a', 'topic-a-s1', 1, str(self.dir / 'wt_a3'))
        row = self.t.bind_worktree('topic-b', 'topic-b-s1', 1, self.wt_a)
        self.assertEqual(row['topic_id'], 'topic-b')

    def test_branch_is_not_reusable_across_topics(self):
        with self.assertRaises(BranchConflict):
            self.t.bind_branch('topic-b', 'topic-b-s1', 1, 'topic-a-b1')
        row = self.t.bind_branch('topic-b', 'topic-b-s1', 1, 'topic-b-b2')
        self.assertEqual(row['branch'], 'topic-b-b2')
        history = self.t.list_branches('topic-b')
        self.assertEqual([h['active'] for h in history].count(True), 1)

    def test_binding_requires_current_owner(self):
        with self.assertRaises(NotLeader):
            self.t.bind_worktree('topic-b', 'topic-a-s1', 1, str(self.dir / 'wt_x'))
        with self.assertRaises(StaleGeneration):
            self.t.bind_worktree('topic-b', 'topic-b-s1', 7, str(self.dir / 'wt_x'))

class ReadOnlyTests(TopicsCase):

    def test_readonly_board_does_not_create_a_database(self):
        missing = self.dir / 'never-created.sqlite'
        board = readonly_board(missing)
        self.assertFalse(board['db_present'])
        self.assertEqual(board['topics'], [])
        self.assertEqual(board['counts']['topics'], 0)
        self.assertFalse(missing.exists())

    def test_readonly_topic_on_missing_database(self):
        missing = self.dir / 'never-created-2.sqlite'
        detail = readonly_topic(missing, 'topic-a', history_limit=3)
        self.assertFalse(detail['db_present'])
        self.assertIsNone(detail['topic'])
        self.assertEqual(detail['checkpoints'], [])
        self.assertFalse(missing.exists())

    def test_readonly_topic_unknown_id_does_not_raise(self):
        detail = readonly_topic(self.db_path, 'topic-nope')
        self.assertTrue(detail['db_present'])
        self.assertIsNone(detail['topic'])

    def test_readonly_calls_leave_state_untouched(self):
        before = self.raw('SELECT * FROM topics ORDER BY id')
        before_sessions = self.raw('SELECT * FROM topic_sessions ORDER BY topic_id,generation')
        readonly_board(self.db_path)
        readonly_topic(self.db_path, 'topic-a')
        self.t.overview()
        self.t.show('topic-a')
        after = self.raw('SELECT * FROM topics ORDER BY id')
        after_sessions = self.raw('SELECT * FROM topic_sessions ORDER BY topic_id,generation')
        self.assertEqual(before, after)
        self.assertEqual(before_sessions, after_sessions)
        conn = self.t._read_connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE topics SET title='x' WHERE id='topic-a'")
        finally:
            conn.close()

    def test_overview_lists_leader_and_counts(self):
        self.cp()
        board = readonly_board(self.db_path)
        item = [x for x in board['topics'] if x['id'] == 'topic-a'][0]
        self.assertEqual(item['leader_session_id'], 'topic-a-s1')
        self.assertEqual(item['open_session']['generation'], 1)
        self.assertEqual(item['checkpoint_count'], 1)
        self.assertEqual(item['experiment_count'], 0)
        self.assertEqual(board['counts']['open_sessions'], 2)
        self.assertEqual(board['counts']['active_branches'], 2)
        self.assertEqual(board['unbound_worktree_topics'], [])

    def test_has_more_is_per_topic_not_global_id(self):
        self.t.write_checkpoint('topic-b', 'topic-b-s1', 1, summary='别的专题的节点')
        ids = [self.cp(summary=f'节点 {i}')['id'] for i in range(3)]
        page = readonly_topic(self.db_path, 'topic-a', history_limit=2)
        self.assertEqual([c['id'] for c in page['checkpoints']], [ids[2], ids[1]])
        self.assertTrue(page['has_more_checkpoints'])
        last = readonly_topic(self.db_path, 'topic-a', history_limit=2, history_before=ids[1])
        self.assertEqual([c['id'] for c in last['checkpoints']], [ids[0]])
        self.assertFalse(last['has_more_checkpoints'])
        exact = readonly_topic(self.db_path, 'topic-a', history_limit=3)
        self.assertEqual(len(exact['checkpoints']), 3)
        self.assertFalse(exact['has_more_checkpoints'])
        other = readonly_topic(self.db_path, 'topic-b', history_limit=2)
        self.assertEqual(len(other['checkpoints']), 1)
        self.assertFalse(other['has_more_checkpoints'])

    def test_limit_none_means_unlimited_everywhere(self):
        for i in range(4):
            self.cp(summary=f'节点 {i}')
        for i in range(3):
            self.t.register_experiment('topic-a', 'topic-a-s1', 1, f'exp-{i:03d}')
        detail = readonly_topic(self.db_path, 'topic-a', history_limit=None)
        self.assertEqual(len(detail['checkpoints']), 4)
        self.assertTrue(detail['has_more_checkpoints'] is False)
        full = self.t.show('topic-a', checkpoint_limit=None, session_limit=None, experiment_limit=None, worktree_limit=None, branch_limit=None)
        self.assertEqual(len(full['experiments']), 3)
        self.assertEqual(len(full['worktrees']), 1)
        self.assertEqual(len(full['sessions']), 1)
        limited = self.t.show('topic-a', experiment_limit=0)
        self.assertEqual(limited['experiments'], [])
        self.assertEqual(len(self.t.list_experiments('topic-a', limit=None)), 3)

    def test_show_reports_history_and_limits(self):
        for i in range(4):
            self.cp(summary=f'节点 {i}')
        detail = readonly_topic(self.db_path, 'topic-a', history_limit=2)
        self.assertEqual(len(detail['checkpoints']), 2)
        self.assertEqual(detail['checkpoints_total'], 4)
        self.assertEqual(len(detail['sessions']), 1)
        self.assertEqual(detail['experiments'], [])
        self.assertEqual([w['path'] for w in detail['worktrees']], [str(Path(self.wt_a).resolve())])
        self.assertEqual([b['branch'] for b in detail['branches']], ['topic-a-b1'])

class IdempotencyTests(TopicsCase):

    def test_same_key_same_payload_returns_same_result(self):
        first = self.t.create_topic('topic-i', title='幂等', idempotency_key='k1')
        second = self.t.create_topic('topic-i', title='幂等', idempotency_key='k1')
        self.assertEqual(first, second)

    def test_same_key_different_payload_conflicts(self):
        self.t.create_topic('topic-i', title='幂等', idempotency_key='k1')
        with self.assertRaises(IdempotencyConflict):
            self.t.create_topic('topic-i', title='别的标题', idempotency_key='k1')

    def test_checkpoint_idempotency(self):
        first = self.t.write_checkpoint('topic-a', 'topic-a-s1', 1, summary='同一节点', idempotency_key='cp-1')
        second = self.t.write_checkpoint('topic-a', 'topic-a-s1', 1, summary='同一节点', idempotency_key='cp-1')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(self.t.checkpoint_count('topic-a'), 1)

class ContractShapeTests(TopicsCase):

    def test_topic_and_session_keys_are_stable(self):
        topic = self.t.get_topic('topic-a')
        self.assertEqual(set(topic), {'id', 'title', 'objective', 'completion_criteria', 'priority', 'status', 'version', 'worktree', 'branch', 'generation', 'leader_session_id', 'leader_generation', 'leader_since', 'created_at', 'updated_at', 'updated_by'})
        session = self.t.leader('topic-a')
        self.assertEqual(set(session), {'topic_id', 'session_id', 'generation', 'predecessor_session_id', 'predecessor_generation', 'status', 'opened_at', 'ended_at', 'ended_reason', 'note', 'actor'})

    def test_json_payloads_are_canonical(self):
        cp = self.cp(extra={'b': 1, 'a': 2})
        row = self.raw('SELECT extra_json,payload_sha256 FROM topic_checkpoints WHERE id=?', (cp['id'],))[0]
        self.assertEqual(row['extra_json'], '{"a":2,"b":1}')

    def test_topic_ids_are_isolated_not_interpolated(self):
        weird = "topic-x'; DROP TABLE topics; --"
        created = self.t.create_topic(weird, title='注入尝试')
        self.assertEqual(created['id'], weird)
        self.assertIsNotNone(self.t.get_topic('topic-a'))
if __name__ == '__main__':
    unittest.main(verbosity=2)
