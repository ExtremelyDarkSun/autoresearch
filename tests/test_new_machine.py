# GENERATED-BY: new-machine recovery integration, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Verify real initialization and deployment preparation keep the host untouched.
# ORIGIN: actual project template and bundled DSH generator
# CUMULATIVE-DELTA: Validate current-machine path substitution, policy semantics and refusal of conflicting setup.
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from kernel.autoresearch.project_layout import initialize_layout, validate_layout
from tools.prepare_new_machine import prepare

ROOT = Path(__file__).resolve().parents[1]


class NewMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name) / '新电脑工作区'
        initialize_layout(ROOT, self.workspace)

    def test_real_init_paths_formats_and_plugin_patch(self):
        self.assertTrue(validate_layout(self.workspace)['ok'])
        self.assertTrue((self.workspace / 'AutoDL实验/实验分析/topics/AGENTS.md').is_file())
        for unwanted in ('topics', 'worktrees', 'environments', 'plans', 'private', 'results'):
            self.assertFalse((self.workspace / unwanted).exists(), unwanted)
        result = prepare(self.workspace)
        self.assertEqual(prepare(self.workspace), result)
        deployment = json.loads(Path(result['deployment']).read_text(encoding='utf-8'))
        self.assertEqual(deployment['workspace'], self.workspace.as_posix())
        self.assertEqual(deployment['plugins']['agent-instruction-filter']['mode'], 'filter')
        self.assertFalse(deployment['plugins']['doc-gap']['enabled'])
        self.assertEqual(deployment['plugins']['autodl-events']['fallback'], 'hold')
        self.assertFalse(result['host_modified'])
        self.assertFalse(result['host_verified'])
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js未安装，参数生成已核，插件配置生成尚未验证')
        out = Path(self.tmp.name) / 'generator.log'
        argv = result['next_argv']; argv[0] = node
        with out.open('wb') as stream:
            run = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT)
        self.assertEqual(run.returncode, 0, out.read_text(encoding='utf-8'))
        patch = (self.workspace / '.autoresearch/setup/profile.patch.yml').read_text(encoding='utf-8')
        self.assertIn("mode: 'filter'", patch)
        self.assertIn('childOnly: true', patch)
        self.assertIn(self.workspace.as_posix(), patch)
        self.assertIn("fallback: 'hold'", patch)
        script = """import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {matchRule}=await import(pathToFileURL(process.argv[1]));
const policy=JSON.parse(readFileSync(process.argv[2], 'utf8'));
for (const [name, expected] of [['C:/user/.dsh/AGENTS.md','keep'],
 ['C:/project/AGENTS.md','drop'],['C:/project/AutoDL实验/AGENTS.md','drop'],
 ['C:/project/AutoDL实验/框架/AGENTS.md','drop'],['C:/project/Kaggle平台/AGENTS.md','drop'],
 ['C:/project/AutoDL实验/框架/roles/scribe.md','keep']]) {
 if(matchRule(policy,name).mode!==expected) throw Error(name);
}
"""
        with out.open('wb') as stream:
            policy_run = subprocess.run([node, '--input-type=module', '-e', script,
                str(ROOT / 'kernel/hosts/dsh/plugins/agent-instruction-filter/filter-core.mjs'),
                str(self.workspace / 'install/agent-instruction-filter.policy.json')],
                stdout=stream, stderr=subprocess.STDOUT)
        self.assertEqual(policy_run.returncode, 0, out.read_text(encoding='utf-8'))

    def test_conflicting_deployment_does_not_overwrite(self):
        result = prepare(self.workspace)
        p = Path(result['deployment'])
        p.write_text('{"manual":"keep"}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            prepare(self.workspace)
        self.assertEqual(p.read_text(encoding='utf-8'), '{"manual":"keep"}\n')

    def test_original_recovery_preserves_database_and_event_layout(self):
        original = Path(self.tmp.name) / 'restored-original'
        paths = ['AutoDL实验/框架/code/runtime/topic_cli.py',
                 'AutoDL实验/tool/topic_watchdog.py', 'AutoDL实验/框架/state/topics.sqlite']
        for name in paths:
            p = original / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'original-bytes')
        before = (original / paths[2]).read_bytes()
        result = prepare(original, original=True)
        deployment = json.loads(Path(result['deployment']).read_text(encoding='utf-8'))
        events = deployment['plugins']['autodl-events']
        self.assertEqual(events['targetsFile'], (original / 'temp/autodl_events/targets.json').as_posix())
        self.assertEqual(result['targets_db'], str(original / paths[2]))
        self.assertIn('--availability-only', result['schedule_argv'])
        self.assertIn(str(original), result['schedule_argv'])
        self.assertFalse((original / '.autoresearch/runtime.sqlite3').exists())
        self.assertEqual((original / paths[2]).read_bytes(), before)
        self.assertEqual(prepare(original, original=True), result)

    def test_original_recovery_missing_database_is_not_replaced(self):
        original = Path(self.tmp.name) / 'missing-original'
        original.mkdir()
        with self.assertRaisesRegex(ValueError, '不创建空库'):
            prepare(original, original=True)
        self.assertEqual(list(original.iterdir()), [])

    def test_incomplete_workspace_is_not_prepared(self):
        path = Path(self.tmp.name) / 'not-initialized'
        path.mkdir()
        with self.assertRaises(ValueError):
            prepare(path)
        self.assertEqual(list(path.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
