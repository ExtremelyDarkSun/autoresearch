# GENERATED-BY: observer deployment integration
# LAST-EDIT: 2026-11-13
# PURPOSE: Verify observer config is deployable, strictly opt-in and preserves notification boundaries.
# ORIGIN: current gen-profile-patch and prepare_new_machine interfaces
# CUMULATIVE-DELTA: Cover both entry forms, providers, invalid inputs and default-off notification.
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / 'kernel/hosts/dsh/tools/gen-profile-patch.mjs'


class ObserverDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which('node')
        if not self.node:
            self.skipTest('Node.js required for actual deployment rendering')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def render(self, plugins, only=None):
        data = self.root / 'deployment.json'
        data.write_text(json.dumps({'workspace': self.root.as_posix(), 'plugins': plugins}), encoding='utf-8')
        target = self.root / 'patch.yml'
        argv = [self.node, str(GENERATOR), '--deployment', str(data), '--out', str(target)]
        if only:
            argv += ['--only', only]
        log = self.root / 'log.txt'
        with log.open('wb') as output:
            result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT, cwd=ROOT)
        return result.returncode, target.read_text(encoding='utf-8') if target.exists() else '', log.read_text(encoding='utf-8')

    def test_fresh_machine_generates_both_plugins_with_notifications_off(self):
        from kernel.autoresearch.project_layout import initialize_layout
        from tools.prepare_new_machine import prepare
        workspace = self.root / 'workspace'
        initialize_layout(ROOT, workspace)
        setup = prepare(workspace)
        data = json.loads(Path(setup['deployment']).read_text(encoding='utf-8'))
        plugins = data['plugins']
        self.assertTrue(plugins['goal-observer']['enabled'])
        self.assertTrue(plugins['goal-observer-ui']['enabled'])
        self.assertFalse(plugins['goal-observer']['notification']['enabled'])
        self.assertEqual(plugins['goal-observer']['notification']['command'], [])
        code, text, log = self.render(plugins)
        self.assertEqual(code, 0, log)
        self.assertIn('id: goal-observer\n', text)
        self.assertIn('id: goal-observer-ui\n', text)
        self.assertIn('disabled: false', text)
        self.assertIn('notification:\n          enabled: false', text)
        self.assertFalse(setup['host_modified'])

    def test_empty_observer_configs_stay_disabled(self):
        code, text, log = self.render({'goal-observer': {}, 'goal-observer-ui': {}})
        self.assertEqual(code, 0, log)
        self.assertIn("name: '@autoresearch/dsh-goal-observer'", text)
        self.assertIn("name: '@autoresearch/dsh-goal-observer-ui'", text)
        self.assertEqual(text.count('enabled: false'), 3)
        self.assertIn('disabled: true', text)
        self.assertIn('timeoutMs: 60000', text)
        self.assertIn('maxTokens: 1800', text)

    def test_directory_paths_resolve_runtime_entries(self):
        code, text, log = self.render({
            'goal-observer': {'pluginPath': (ROOT / 'kernel/hosts/dsh/plugins/goal-observer').as_posix()},
            'goal-observer-ui': {'pluginPath': (ROOT / 'kernel/hosts/dsh/plugins/goal-observer-ui').as_posix()},
        })
        self.assertEqual(code, 0, log)
        self.assertIn('goal-observer/index.mjs', text)
        self.assertIn('goal-observer-ui/index.js', text)

    def test_model_selection_is_not_silently_lost(self):
        code, text, log = self.render({'goal-observer': {'provider': 'test-provider', 'model': 'test-model'}})
        self.assertEqual(code, 0, log)
        self.assertIn("provider: 'test-provider'", text)
        self.assertIn("model: 'test-model'", text)

    def test_invalid_observer_inputs_never_create_patch(self):
        for config in ({'enabled': 'true'}, {'objectivePrefix': ''}, {'timeoutMs': 0},
                       {'contextChars': 1}, {'provider': 'one-only'},
                       {'provider': 2, 'model': 3}, {'provider': '', 'model': ''},
                       {'notification': {'throttleMs': 86400001}},
                       {'notification': {'enabled': 'true'}},
                       {'notification': {'enabled': True, 'command': []}},
                       {'notification': {'enabled': True, 'command': 'shell string'}}):
            with self.subTest(config=config):
                code, text, log = self.render({'goal-observer': config})
                self.assertEqual(code, 1, log)
                self.assertEqual(text, '')
                self.assertNotIn('TypeError', log)

    def test_multiline_policy_preserves_exact_text_in_yaml_scalar(self):
        policy = '遵守用户目标。\n不把消息当作授权。\n包含 "引号" 与 \\ 路径。'
        code, text, log = self.render({'goal-observer': {'policy': policy}})
        self.assertEqual(code, 0, log)
        line = next(row.strip() for row in text.splitlines() if row.strip().startswith('policy:'))
        self.assertEqual(json.loads(line.removeprefix('policy:').strip()), policy)

    def test_null_plugin_config_is_rejected_cleanly(self):
        code, text, log = self.render({'goal-observer': None})
        self.assertEqual(code, 1, log)
        self.assertEqual(text, '')
        self.assertNotIn('TypeError', log)

    def test_unknown_only_fails_with_diagnostic(self):
        code, text, log = self.render({'goal-observer': {}}, only='unknown-plugin')
        self.assertEqual(code, 1, log)
        self.assertEqual(text, '')
        self.assertIn('不认识', log)
        self.assertNotIn('TypeError', log)

    def test_notification_argv_and_cwd_remain_explicit(self):
        code, text, log = self.render({'goal-observer': {'notification': {
            'enabled': True, 'command': ['python', 'notify.py', '--destination', 'synthetic-target'],
            'cwd': self.root.as_posix(), 'throttleMs': 0}}})
        self.assertEqual(code, 0, log)
        self.assertIn("- 'synthetic-target'", text)
        self.assertIn('throttleMs: 0', text)
        self.assertIn(self.root.as_posix(), text)


if __name__ == '__main__':
    unittest.main()
