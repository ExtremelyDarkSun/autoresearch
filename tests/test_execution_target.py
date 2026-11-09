# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09
# PURPOSE: Verify actual execution cwd and Python environment, including refusal of false remote-stop claims.
# ORIGIN: none
# CUMULATIVE-DELTA: Real venv and Git provisioning in temporary workspaces.
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from kernel.autoresearch.execution_target import ExecutionTarget
from kernel.autoresearch.workspace import provision_topic


class ExecutionTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.autoresearch').mkdir()
        self.record = provision_topic(self.root, topic='T', objective='验证实际执行落点',
                                      requirements='仅合成测试，不连接外部评测', owner_session='fixture-owner')
        self.python = self.root / self.record['python']
        self.code = self.root / self.record['code_path']
        self.target = ExecutionTarget(self.root, require_provision=True)

    def test_uses_real_code_directory_and_venv(self):
        argv = [str(self.python), '-c', 'import os,sys,json; print(json.dumps([os.getcwd(),sys.prefix,sys.base_prefix]))']
        result = self.target.resolve('T', {}, argv)
        self.assertEqual(Path(result['cwd']), self.code)
        env = dict(os.environ, **result['env_additions'])
        output = self.root / 'probe.json'
        with output.open('wb') as stream:
            run = subprocess.run(argv, cwd=result['cwd'], env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.assertEqual(run.returncode, 0)
        cwd, prefix, base = json.loads(output.read_text())
        self.assertEqual(Path(cwd), self.code)
        self.assertNotEqual(prefix, base)
        self.assertTrue(result['isolation_verified'])
        self.assertFalse(result['dependencies_verified'])

    def test_system_interpreter_and_wrong_cwd_refused(self):
        with self.assertRaisesRegex(ValueError, '解释器'):
            self.target.resolve('T', {}, [sys.executable, '-c', 'pass'])
        with self.assertRaisesRegex(ValueError, '执行目录'):
            self.target.resolve('T', {'execution': {'cwd': '.'}}, [str(self.python), '-c', 'pass'])

    def test_external_script_and_remote_or_gpu_scope_refused(self):
        (self.root / 'outside.py').write_text('pass\n')
        with self.assertRaisesRegex(ValueError, '脚本'):
            self.target.resolve('T', {}, [str(self.python), str(self.root / 'outside.py')])
        for argv, plan in ((['ssh', 'host.invalid', 'sleep', '60'], {}),
                           ([str(self.python), '-c', 'pass'], {'execution': {'scope': 'remote'}}),
                           ([str(self.python), '-c', 'pass'], {'execution': {'resource_kind': 'gpu'}})):
            with self.assertRaises(ValueError):
                self.target.resolve('T', plan, argv)

    def test_missing_environment_or_invalid_provision_refused(self):
        p = self.root / 'topics/T/provision.json'
        record = json.loads(p.read_text())
        record['status'] = 'incomplete'
        p.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, '初始化记录'):
            self.target.resolve('T', {}, [str(self.python), '-c', 'pass'])
        with self.assertRaisesRegex(ValueError, '尚未实际初始化'):
            self.target.resolve('other', {}, [sys.executable, '-c', 'pass'])

    def test_library_fixture_fallback_is_explicitly_unverified(self):
        view = ExecutionTarget(self.root).resolve('unprovisioned', {}, [sys.executable, '-c', 'pass'])
        self.assertFalse(view['isolation_verified'])
        self.assertFalse(view['remote_stop_verified'])
        self.assertEqual(view['cwd'], str(self.root))


if __name__ == '__main__':
    unittest.main()
