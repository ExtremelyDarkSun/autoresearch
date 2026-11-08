# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_legacy.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from kernel.autoresearch.runtime.store import Store
from kernel.autoresearch.runtime.legacy import LegacyImporter

class LegacyTests(unittest.TestCase):

    def setUp(self):
        root = Path(__file__).resolve().parents[3] / 'temp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='hil_legacy_', dir=root)
        self.root = Path(self.temp.name)
        self.exp = self.root / 'exp-historical'
        self.exp.mkdir()
        self.store = Store(self.root / 'runtime.sqlite')
        self.importer = LegacyImporter(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, obj):
        path = self.exp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj), encoding='utf-8')

    def fingerprints(self):
        return {p.relative_to(self.exp).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.exp.rglob('*') if p.is_file()}

    def test_conflict_is_evidence_not_progress(self):
        self.write('status.json', {'station': '4', 'blocked': True})
        self.write('stations/8.merge.json', {'station': '8', 'exit_code': 0, 'dry_run': False})
        self.write('decision.json', {'resolution': 'series_preapproved', 'series': {'active': True}})
        before = self.fingerprints()
        result = self.importer.import_experiment(self.exp)
        self.assertIn('status_conflict', [i['kind'] for i in result['issues']])
        self.assertEqual('planned', self.store.get_experiment(self.exp.name)['state'])
        self.assertFalse(result['authorization_imported'])
        self.assertFalse(result['budget_imported'])
        self.assertEqual(before, self.fingerprints())
        self.assertEqual(0, self.importer.import_experiment(self.exp)['inserted'])

    def test_empty_results_not_complete(self):
        path = self.exp / 'runs/run/results/task_results.jsonl'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'')
        result = self.importer.inspect(self.exp)
        self.assertIn('invalid_results', [i['kind'] for i in result['issues']])
        self.assertEqual('unknown', result['observations'][0]['summary']['completeness'])

    def test_bad_json_is_visible(self):
        (self.exp / 'status.json').write_bytes(b'{')
        result = self.importer.import_experiment(self.exp)
        self.assertIn('unreadable', [i['kind'] for i in result['issues']])
        self.assertEqual(0, result['inserted'])

    def test_dryrun_does_not_prove_merge(self):
        self.write('status.json', {'station': '4', 'blocked': True})
        self.write('stations/8.merge.json', {'station': '8', 'exit_code': 0, 'dry_run': True})
        result = self.importer.inspect(self.exp)
        self.assertNotIn('status_conflict', [i['kind'] for i in result['issues']])
if __name__ == '__main__':
    unittest.main()
