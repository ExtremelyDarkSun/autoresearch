# GENERATED-BY: reply source binding acceptance, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: 防止不同通道同号事件、无来源旧回复和非有限时间误解除超期阻断。
# ORIGIN: real DecisionService/Inbox/Delivery fixture; progress_host read-only projection
# CUMULATIVE-DELTA: 增加复合事件身份与时间边界的回归测试；仅临时本地库和合成通道。
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kernel.autoresearch.decisions import DecisionService, DecisionError
from kernel.autoresearch.progress_host import read_overdue
from kernel.autoresearch.runtime.store import Store
from test_decisions import Clock, FakeChannel, EXP, decision, plan_set, question_event


class ReplyBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'runtime.sqlite3'
        self.clock = Clock()
        self.store = Store(self.db)
        self.channel = FakeChannel(self.clock)
        self.service = DecisionService(self.store, channel=self.channel, clock=self.clock)
        self.service.prepare(exp_id=EXP, decision=decision(), plan_set=plan_set(),
                             page_dir=self.root / 'web')
        self.assertTrue(self.service.send('E1.1')['ok'])

    def ingest(self, source, event_id='same-id'):
        return self.service.ingest(source, question_event(event_id, create_time=self.clock.now - 400))

    def reply_one(self):
        claim = self.service.claim_reply('worker')
        self.assertIsNotNone(claim)
        task = self.service.answer(claim, f'已核对当前实验 {EXP} 的进展')
        self.channel.reply_map['same-id'] = 'om_synthetic_original'
        result = self.service.deliver_reply(task['id'])
        self.assertTrue(result['ok'], result)
        return result

    def test_same_event_id_in_two_sources_requires_two_actual_replies(self):
        self.ingest('channel-a')
        self.ingest('channel-b')
        self.reply_one()
        status = self.service.reply_status(EXP)
        self.assertEqual(status['replied'], 1)
        self.assertEqual(len(status['overdue']), 1)
        self.assertEqual(status['overdue'][0]['source'], 'channel-b')
        host = read_overdue(self.db, now=self.clock.now)
        self.assertTrue(host['checked'], host)
        self.assertEqual(host['replied'], 1)
        self.assertEqual(host['count'], 1)
        self.assertEqual(host['overdue'][0]['source'], 'channel-b')
        self.reply_one()
        self.assertEqual(self.service.reply_status(EXP)['replied'], 2)
        self.assertFalse(self.service.reply_status(EXP)['blocked'])
        self.assertEqual(read_overdue(self.db, now=self.clock.now)['count'], 0)

    def test_legacy_reply_without_source_does_not_close_any_event(self):
        self.ingest('channel-a')
        self.reply_one()
        # 显式构造旧版不完整绑定，不把这样的历史行视为可信回复证明。
        with closing(self.store.connect()) as conn:
            row = conn.execute("SELECT id,payload_json FROM delivery WHERE kind='reply'").fetchone()
            payload = json.loads(row['payload_json'])
            payload['event_reply_to'].pop('source')
            conn.execute('UPDATE delivery SET payload_json=? WHERE id=?', (json.dumps(payload), row['id']))
            conn.commit()
        self.assertEqual(self.service.reply_status(EXP)['replied'], 0)
        self.assertTrue(self.service.reply_status(EXP)['blocked'])
        host = read_overdue(self.db, now=self.clock.now)
        self.assertTrue(host['checked'])
        self.assertEqual(host['replied'], 0)
        self.assertEqual(host['count'], 1)

    def test_nonfinite_or_invalid_event_time_never_becomes_no_overdue(self):
        self.ingest('channel-a')
        for bad in (None, float('nan'), float('inf'), float('-inf'), -1, 0, 'bad-time'):
            with self.subTest(value=repr(bad)):
                with closing(self.store.connect()) as conn:
                    conn.execute('UPDATE inbox_events SET event_time=? WHERE source=?', (bad, 'channel-a'))
                    conn.commit()
                status = self.service.reply_status(EXP)
                self.assertTrue(status['blocked'], status)
                self.assertEqual(status['replied'], 0)
                host = read_overdue(self.db, now=self.clock.now)
                self.assertFalse(host['checked'], host)
                self.assertIsNotNone(host['error'])

    def test_nonfinite_clock_or_grace_is_undecidable(self):
        for key in ('now', 'grace'):
            for value in (float('nan'), float('inf'), -1, 0):
                options = {'now': self.clock.now, 'grace': 304}
                options[key] = value
                with self.subTest(key=key, value=repr(value)):
                    with self.assertRaises(DecisionError):
                        self.service.reply_status(EXP, **options)
                    host = read_overdue(self.db, **options)
                    self.assertFalse(host['checked'], host)
                    self.assertIn('有限正数', host['error'])

    def test_queries_do_not_modify_the_real_database_or_add_sidecars(self):
        self.ingest('channel-a')
        self.ingest('channel-b')
        self.reply_one()
        before = {str(path.relative_to(self.root)): (path.stat().st_size, path.stat().st_mtime_ns)
                  for path in self.root.rglob('*') if path.is_file()}
        raw = self.db.read_bytes()
        service = DecisionService.readonly(self.db)
        self.assertTrue(service.reply_status(EXP, now=self.clock.now)['blocked'])
        self.assertEqual(read_overdue(self.db, now=self.clock.now)['count'], 1)
        after = {str(path.relative_to(self.root)): (path.stat().st_size, path.stat().st_mtime_ns)
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(raw, self.db.read_bytes())


if __name__ == '__main__':
    unittest.main()
