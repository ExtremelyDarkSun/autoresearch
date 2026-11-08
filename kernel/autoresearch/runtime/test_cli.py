# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_cli.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from kernel.autoresearch.runtime import cli

class CLITests(unittest.TestCase):

    def setUp(self):
        root = Path(__file__).resolve().parents[3] / 'temp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='hil_cli_', dir=root)
        self.db = Path(self.temp.name) / 'runtime.sqlite'

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = cli.main(['--db', str(self.db), *args])
        return (result, json.loads(out.getvalue()))

    def test_default_init_does_not_write(self):
        code, result = self.call('init')
        self.assertEqual(0, code)
        self.assertFalse(result['writes'])
        self.assertFalse(self.db.exists())

    def test_readonly_overview_missing_does_not_create(self):
        code, result = self.call('overview')
        self.assertEqual(2, code)
        self.assertFalse(self.db.exists())

    def test_overview_does_not_change_db(self):
        self.call('--commit', 'init')
        self.call('--commit', 'activate', '--exp', 'exp-a', '--goal', 'A')
        sha = hashlib.sha256(self.db.read_bytes()).hexdigest()
        code, result = self.call('overview')
        self.assertEqual(0, code)
        self.assertEqual('exp-a', result['experiments'][0]['id'])
        self.assertEqual(sha, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_queue_claim_ack(self):
        self.call('--commit', 'init')
        self.call('--commit', 'activate', '--exp', 'exp-a')
        _, own = self.call('--commit', 'takeover', '--exp', 'exp-a', '--owner', 'orchestrator')
        generation = str(own['generation'])
        payload = Path(self.temp.name) / 'payload.json'
        payload.write_text(json.dumps({'instruction': 'offline work only'}), encoding='utf-8')
        code, task = self.call('--commit', 'enqueue', '--exp', 'exp-a', '--owner', 'orchestrator', '--generation', generation, '--key', 'one', '--payload-file', str(payload))
        self.assertEqual(0, code)
        code, claimed = self.call('--commit', 'claim', '--exp', 'exp-a', '--owner', 'orchestrator', '--generation', generation, '--worker', 'runner')
        self.assertEqual(0, code)
        self.assertEqual(task['id'], claimed['id'])
        code, acked = self.call('--commit', 'ack', '--owner', 'orchestrator', '--generation', generation, '--worker', 'runner', '--task', claimed['id'], '--token', claimed['token'])
        self.assertEqual(0, code)
        self.assertEqual('acked', acked['status'])

    def test_register_new_requires_explicit_commit_and_exclusive_path(self):
        self.call('--commit', 'init')
        directory = Path(self.temp.name) / 'exp-new'
        self.call('register-new', '--exp', 'exp-new', '--directory', str(directory))
        self.assertFalse(directory.exists())
        code, result = self.call('--commit', 'register-new', '--exp', 'exp-new', '--directory', str(directory))
        self.assertEqual(0, code)
        self.assertTrue((directory / '.hil-runtime-owner.json').is_file())
        code, _ = self.call('--commit', 'register-new', '--exp', 'exp-new', '--directory', str(directory))
        self.assertEqual(2, code)

    def test_unknown_resources_remain_visible_and_occupied(self):
        self.call('--commit', 'init')
        from kernel.autoresearch.runtime.store import Store
        from kernel.autoresearch.runtime.scheduler import Scheduler
        scheduler = Scheduler(Store(self.db))
        scheduler.activate('exp-a')
        own = scheduler.takeover('exp-a', 'owner')
        scheduler.enqueue('exp-a', 'paid', {}, 'owner', own['generation'], paid=True, kind='execution', resources=('machine:test',))
        task = scheduler.claim('exp-a', 'owner', own['generation'], 'worker')
        scheduler.ack(task['id'], 'owner', own['generation'], 'worker', task['token'])
        code, result = self.call('overview')
        self.assertEqual(0, code)
        self.assertEqual('unknown', result['resources'][0]['state'])
        self.assertTrue(result['resources'][0]['occupied'])
        self.assertEqual(1, len(result['quarantines']))
        self.assertTrue(result['active_experiments'])

    def test_fencing_failure_is_structured(self):
        self.call('--commit', 'init')
        code, result = self.call('--commit', 'takeover', '--exp', 'missing', '--owner', 'owner')
        self.assertEqual(2, code)
        self.assertIn('error', result)
if __name__ == '__main__':
    unittest.main()
