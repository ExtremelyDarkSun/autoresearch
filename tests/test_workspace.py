# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09
# PURPOSE: Verify real worktree, separate interpreter, research source snapshots and refusal paths.
# ORIGIN: none
# CUMULATIVE-DELTA: Actual Git and venv integration using synthetic local repositories.
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from kernel.autoresearch.workspace import provision_topic, snapshot_topic, _git
from kernel.autoresearch.runtime.store import Store
from kernel.autoresearch.runtime.topics import Topics


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workspace'
        (self.root / '.autoresearch').mkdir(parents=True)
        self.topics = Topics(Store(self.root / '.autoresearch/runtime.sqlite3'))

    def provision(self, **kwargs):
        return provision_topic(self.root, topic='topic-one', objective='检验有根据的机制', requirements='保留原始要求全文', owner_session='owner', **kwargs)

    def test_real_empty_repository_and_environment(self):
        record = self.provision()
        code = self.root / record['code_path']
        self.assertTrue((code / '.git').is_dir())
        self.assertEqual(_git(['symbolic-ref', '--short', 'HEAD'], code), 'research/topic-one')
        python = self.root / record['python']
        output = self.root / 'prefix.json'
        result = subprocess.run([str(python), '-c', 'import sys,json; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps({"prefix":sys.prefix,"base":sys.base_prefix}))', str(output)], stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0)
        prefixes = json.loads(output.read_text())
        self.assertNotEqual(prefixes['prefix'], prefixes['base'])
        self.assertFalse(record['ready_for_evaluation'])
        self.assertFalse(record['dependencies_installed'])
        self.assertEqual(self.topics.get_topic('topic-one')['worktree'], str(code))
        self.assertIn('保留原始要求全文', (self.root / 'topics/topic-one/REQUIREMENTS.md').read_text(encoding='utf-8'))
        with self.assertRaises((FileExistsError, ValueError)):
            self.provision()

    def test_worktree_is_actual_isolated_code(self):
        source = Path(self.tmp.name) / 'source'
        source.mkdir()
        _git(['init', '--quiet'], source)
        (source / 'code.py').write_text('original\n')
        _git(['add', 'code.py'], source)
        _git(['-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'synthetic'], source)
        record = self.provision(code_repo=source, create_environment=False)
        code = self.root / record['code_path']
        self.assertTrue((code / '.git').is_file())
        self.assertEqual(_git(['rev-parse', 'HEAD'], code), record['source_commit'])
        (code / 'code.py').write_text('independent edit\n')
        self.assertEqual((source / 'code.py').read_text(), 'original\n')
        self.assertIn(str(code).replace('\\', '/'), _git(['worktree', 'list', '--porcelain'], source).replace('\\', '/'))

    def test_snapshot_original_bytes_and_no_overwrite(self):
        self.provision(create_environment=False)
        source = self.root / 'topics/topic-one/TOPIC.md'
        before = snapshot_topic(self.root, topic='topic-one', exp_id='E1', stage='before')
        self.assertEqual((self.root / before['path']).read_bytes(), source.read_bytes())
        original = source.read_bytes()
        source.write_bytes(original + b'\nresearch update\n')
        after = snapshot_topic(self.root, topic='topic-one', exp_id='E1', stage='after')
        self.assertNotEqual(before['sha256'], after['sha256'])
        with self.assertRaises(FileExistsError):
            snapshot_topic(self.root, topic='topic-one', exp_id='E1', stage='before')
        self.assertEqual((self.root / before['path']).read_bytes(), original)

    def test_registered_topic_refused_before_code_creation(self):
        self.topics.create_topic('topic-one', title='Existing', objective='Existing goal')
        with self.assertRaises(ValueError):
            self.provision(create_environment=False)
        self.assertFalse((self.root / 'worktrees/topic-one').exists())
        self.assertFalse((self.root / 'topics/topic-one').exists())

    def test_path_escape_and_symlink_escape_refused(self):
        with self.assertRaises(ValueError):
            provision_topic(self.root, topic='../escape', objective='o', requirements='r', owner_session='s')
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        try:
            (self.root / 'topics').symlink_to(outside, target_is_directory=True)
        except OSError:
            return  # Host may not grant symlink privilege.
        with self.assertRaises(ValueError):
            self.provision(create_environment=False)
        self.assertFalse((outside / 'topic-one').exists())


if __name__ == '__main__':
    unittest.main()
