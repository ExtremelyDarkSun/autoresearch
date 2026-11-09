# GENERATED-BY: project layout module tests, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: 用临时合成仓库验证模板安装的不覆盖、正文复制、回执、链接重写、空状态与只读校验。
# ORIGIN: 用户要求（有意义临时夹具；不依赖真实 project 模板即可运行）
# CUMULATIVE-DELTA: 2026-11-09 templates副本与框架链接一并安装；跨框架引用与命名模式按实际边界断言。
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from kernel.autoresearch.project_layout import initialize_layout, validate_layout
from kernel.autoresearch.runtime.store import Store
from kernel.autoresearch.runtime.topics import Topics

PROFILE_TOML = '''id = "agent-harness"
version = 1

[evaluation]
command = []
configured = false
result_file = "task_results.jsonl"

[instructions]
required = ["RULES.md"]

[limits]
'''

ROUTES = {
    'format': 1,
    'full_document': True,
    'history_is_current_instruction': False,
    'routes': [
        {'id': 'root', 'prefix': '', 'entry': 'AGENTS.md', 'required': ['RULES.md', 'PROJECT.md']},
        {'id': 'framework', 'prefix': 'AutoDL实验/框架/',
         'entry': 'AutoDL实验/框架/AGENTS.md',
         'required': ['../kernel/contracts/CONTRACT.md', 'AutoDL实验/框架/CONTEXT_LOADING.md']},
        {'id': 'platform', 'prefix': 'Kaggle平台/', 'entry': 'Kaggle平台/AGENTS.md',
         'required': ['../profiles/agent-harness/RULES.md']},
    ],
}


def build_framework(base):
    """合成一个最小框架仓库：kernel 三棵树、docs、profile 与 project 模板。"""
    root = Path(base) / 'framework'
    files = {
        'kernel/contracts/CONTRACT.md':
            '# 契约\n\n- [计划](<./plan.md>)\n- [角色](<../roles/researcher.md>)\n'
            '- [决策服务](<../autoresearch/decisions.py>)\n- [验收记录](<../../docs/EXPECTATIONS.md>)\n',
        'kernel/contracts/plan.md': '# 计划\n\n[规则](<../instructions/main_session.md>)\n',
        'kernel/contracts/reading_required.json': json.dumps(
            {'schema': 1, 'entries': [
                {'id': 'R1', 'role': 'researcher', 'path': 'kernel/contracts/CONTRACT.md',
                 'anchor': '## 1', 'scope': 'full_document'},
                {'id': 'R2', 'role': 'researcher', 'path': 'kernel/instructions/main_session.md',
                 'anchor': '## 2', 'scope': 'full_document'}]},
            ensure_ascii=False, indent=2) + '\n',
        'kernel/instructions/main_session.md': '# 主会话\n\n[契约](<../contracts/CONTRACT.md>)\n',
        'kernel/roles/researcher.md': '# 研究者\n\n[契约](<../contracts/plan.md>)\n',
        'kernel/templates/status_page.template.md': '# 现状页骨架\n\n[契约](<../contracts/CONTRACT.md>)\n',
        'kernel/autoresearch/decisions.py': '# 不在复制范围内的内核源码\n',
        'docs/EXPECTATIONS.md': '# 验收记录\n',
        'profiles/agent-harness/profile.toml': PROFILE_TOML,
        'profiles/agent-harness/RULES.md': '# profile 规则\n\n[契约](<../../kernel/contracts/CONTRACT.md>)\n',
        'project/AGENTS.md':
            '# 项目入口\n\n框架路径：@FRAMEWORK@\n\n'
            '- [规则](<RULES.md>)\n- [契约](<../kernel/contracts/CONTRACT.md>)\n'
            '- [profile 规则](<../profiles/agent-harness/RULES.md>)\n',
        'project/RULES.md': '# 规则\n\n[入口](<AGENTS.md>)\n[维护](<../kernel/contracts/plan.md>)\n',
        'project/PROJECT.md': '# 项目事实\n\n[入口](<AGENTS.md>)\n',
        'project/DOC_MAINTENANCE.md': '# 文档维护\n\n[入口](<AGENTS.md>)\n',
        'project/ORGANIZATION_MAP.md': '# 组织地图\n',
        'project/INSTALL.md': '# 安装\n\npython -B "@FRAMEWORK@/research.py" --help\n',
        'project/.gitignore': 'template-only/\n!README.md\n',
        'project/data/README.md': '# 数据\n\n[入口](<../AGENTS.md>)\n',
        'project/Kaggle平台/AGENTS.md':
            '# 平台入口\n\n[profile 规则](<../../profiles/agent-harness/RULES.md>)\n',
        'project/AutoDL实验/框架/AGENTS.md':
            '# 框架入口\n\n- [契约](<../../../kernel/contracts/CONTRACT.md>)\n'
            '- [契约副本](<../../../kernel/contracts/CONTRACT.md>)\n'
            '- [profile 规则](<../../../profiles/agent-harness/RULES.md>)\n'
            '- [加载边界](<CONTEXT_LOADING.md>)\n',
        'project/AutoDL实验/框架/CONTEXT_LOADING.md':
            '# 加载边界\n\n[契约](<../../../kernel/contracts/plan.md>)\n',
        'project/AutoDL实验/框架/context_routes.json':
            json.dumps(ROUTES, ensure_ascii=False, indent=2) + '\n',
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
    (root / 'project/binary.bin').write_bytes(b'\x00\x01\x02synthetic')
    (root / 'project/__pycache__').mkdir(parents=True, exist_ok=True)
    (root / 'project/__pycache__/cached.pyc').write_bytes(b'not-a-source')
    (root / 'project/.autoresearch').mkdir(parents=True, exist_ok=True)
    (root / 'project/.autoresearch/stale.json').write_text('{"old": true}\n', encoding='utf-8')
    return root


def links_of(path):
    """给出 Markdown 里的行内链接目标（去掉尖括号）。"""
    import re
    pattern = re.compile(r'!?\[[^\[\]\n]*\]\(\s*(<[^<>\n]*>|[^()\s]*)\s*\)')
    values = []
    for match in pattern.finditer(Path(path).read_text(encoding='utf-8')):
        target = match.group(1)
        values.append(target[1:-1] if target.startswith('<') and target.endswith('>') else target)
    return values


def tree_snapshot(root):
    """记录目录树的大小、mtime 与内容摘要，用于证明校验没有写文件。"""
    snapshot = {}
    for current, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(current) / name
            if path.is_symlink():
                continue
            raw = path.read_bytes()
            snapshot[path.relative_to(root).as_posix()] = (
                len(raw), path.stat().st_mtime_ns, hashlib.sha256(raw).hexdigest())
    return snapshot


class ProjectLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='layout-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.root = build_framework(self.tmp.name)
        self.dest = Path(self.tmp.name) / 'workspace'

    def install(self, **kwargs):
        return initialize_layout(self.root, self.dest, self.root / 'profiles/agent-harness', **kwargs)

    # ---- 目标边界 -------------------------------------------------------

    def test_installs_versioned_document_templates(self):
        manifest = self.install()
        relative = 'AutoDL实验/框架/templates/status_page.template.md'
        self.assertTrue((self.dest / relative).is_file())
        self.assertTrue(any(item['path'] == relative for item in manifest['files']))
        self.assertIn('../contracts/CONTRACT.md', (self.dest / relative).read_text(encoding='utf-8'))

    def test_refuses_existing_destination_without_touching_it(self):
        self.dest.mkdir()
        (self.dest / 'kept.txt').write_text('原样\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual([path.name for path in self.dest.iterdir()], ['kept.txt'])
        self.assertEqual((self.dest / 'kept.txt').read_text(encoding='utf-8'), '原样\n')

    def test_refuses_destination_inside_framework(self):
        with self.assertRaises(ValueError):
            initialize_layout(self.root, self.root / 'installed', self.root / 'profiles/agent-harness')
        with self.assertRaises(ValueError):
            initialize_layout(self.root, self.root, self.root / 'profiles/agent-harness')
        self.assertFalse((self.root / 'installed').exists())

    def test_allows_local_demo_directory_inside_framework(self):
        manifest = initialize_layout(self.root, self.root / '.local/demo',
                                     self.root / 'profiles/agent-harness')
        self.assertTrue((self.root / '.local/demo/AGENTS.md').is_file())
        self.assertTrue(validate_layout(self.root / '.local/demo')['ok'], manifest['files'])

    # ---- 复制与回执 -----------------------------------------------------

    def test_copies_bodies_and_receipts_match_written_files(self):
        manifest = self.install()
        self.assertTrue((self.dest / 'binary.bin').read_bytes() == b'\x00\x01\x02synthetic')
        self.assertEqual((self.dest / '.gitignore').read_text(encoding='utf-8'), 'template-only/\n!README.md\n')
        self.assertFalse((self.dest / '__pycache__').exists())
        self.assertFalse((self.dest / '.autoresearch/stale.json').exists())
        on_disk = json.loads((self.dest / '.autoresearch/layout-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(on_disk['files'], manifest['files'])
        for record in manifest['files']:
            raw = (self.dest / record['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record['sha256'], record['path'])
            self.assertEqual(len(raw), record['bytes'], record['path'])
            self.assertNotIn(':\\', record['source'], '回执不得含私有绝对源路径')
            self.assertFalse(record['source'].startswith('/'), record['source'])
        sources = {record['source'] for record in manifest['files']}
        self.assertIn('kernel/contracts/CONTRACT.md', sources)
        self.assertIn('profiles/agent-harness/RULES.md', sources)
        self.assertTrue(any(source.startswith('project/') for source in sources))
        self.assertEqual(manifest['state']['empty'], True)
        self.assertFalse(manifest['host_activation'].startswith('已加载'))

    def test_empty_state_runtime_and_no_monitoring_enable_file(self):
        self.install()
        database = self.dest / '.autoresearch/runtime.sqlite3'
        self.assertTrue(database.is_file())
        self.assertEqual(Topics(Store(database)).list_topics(), [])
        created = sorted(path.name for path in (self.dest / '.autoresearch').iterdir()
                         if path.suffix != '.sqlite3-wal' and path.suffix != '.sqlite3-shm')
        self.assertNotIn('monitoring.json', created)
        for current, _, filenames in os.walk(self.dest):
            for name in filenames:
                self.assertFalse(name.lower().startswith('monitoring'), name)

    # ---- 链接重写 -------------------------------------------------------

    def test_rewrites_template_links_and_placeholder_only_in_copies(self):
        self.install()
        agents = links_of(self.dest / 'AGENTS.md')
        self.assertIn('AutoDL实验/框架/contracts/CONTRACT.md', agents)
        self.assertIn('Kaggle平台/config/agent-harness/RULES.md', agents)
        text = (self.dest / 'AGENTS.md').read_text(encoding='utf-8')
        self.assertIn(self.root.resolve().as_posix(), text)
        self.assertNotIn('@FRAMEWORK@', text)
        template = (self.root / 'project/AGENTS.md').read_text(encoding='utf-8')
        self.assertIn('@FRAMEWORK@', template)
        self.assertIn('../kernel/contracts/CONTRACT.md', template)

    def test_rewrites_framework_contract_links_across_kernel_subdirectories(self):
        self.install()
        contract = self.dest / 'AutoDL实验/框架/contracts/CONTRACT.md'
        targets = links_of(contract)
        self.assertIn('plan.md', targets)
        self.assertIn('../roles/researcher.md', targets)
        plan = links_of(self.dest / 'AutoDL实验/框架/contracts/plan.md')
        self.assertIn('../instructions/main_session.md', plan)
        for document in (self.dest / 'AutoDL实验/框架').rglob('*.md'):
            for target in links_of(document):
                if target.startswith(('#', 'http', 'mailto')) or '<' in target:
                    continue
                resolved = Path(target) if Path(target).is_absolute() else (document.parent / target)
                self.assertTrue(resolved.exists(), f'{document} -> {target}')

    def test_rewrites_route_and_reading_manifests_to_local_paths(self):
        self.install()
        routes = json.loads((self.dest / 'AutoDL实验/框架/context_routes.json').read_text(encoding='utf-8'))
        self.assertIs(routes['full_document'], True)
        self.assertIs(routes['history_is_current_instruction'], False)
        by_id = {route['id']: route for route in routes['routes']}
        self.assertEqual(by_id['root']['entry'], 'AGENTS.md')
        self.assertEqual(by_id['framework']['required'][0], 'AutoDL实验/框架/contracts/CONTRACT.md')
        self.assertEqual(by_id['platform']['required'][0], 'Kaggle平台/config/agent-harness/RULES.md')
        for route in routes['routes']:
            for value in [route['entry'], *route['required']]:
                self.assertTrue((self.dest / value).is_file(), value)
        reading = json.loads((self.dest / 'AutoDL实验/框架/contracts/reading_required.json').read_text(encoding='utf-8'))
        self.assertEqual([entry['path'] for entry in reading['entries']],
                         ['contracts/CONTRACT.md', 'instructions/main_session.md'])
        for entry in reading['entries']:
            self.assertTrue((self.dest / 'AutoDL实验/框架' / entry['path']).is_file())

    # ---- 只读校验 -------------------------------------------------------

    def test_validate_reports_ok_and_counts_measured_paths(self):
        manifest = self.install()
        result = validate_layout(self.dest)
        self.assertTrue(result['ok'], result['errors'])
        self.assertTrue(result['read_only'])
        self.assertGreater(result['documents'], 10)
        self.assertEqual(result['routes_manifests'], 1)
        self.assertEqual(result['route_paths_checked'], 8)
        self.assertEqual(result['reading_manifests'], 1)
        self.assertEqual(result['reading_paths_checked'], 2)
        self.assertEqual(len(result['external_links']), 2)
        self.assertTrue(all(item['framework'].endswith('framework') for item in result['external_links']))
        measured = manifest['entry_measurement']['routes']
        self.assertEqual([route['id'] for route in measured], ['root', 'framework', 'platform'])
        for route in measured:
            self.assertTrue(route['entry']['workspace_local'], route)
            for item in route['required']:
                self.assertTrue(item['workspace_local'], item)
                self.assertGreater(item['bytes'], 0)
        self.assertEqual(manifest['measurement']['entry_bytes'],
                         sum(route['entry']['bytes'] for route in measured))
        self.assertIn('首轮注入', manifest['entry_measurement']['note'])

    def test_validate_detects_broken_link_and_pattern_is_not_error(self):
        self.install()
        rules = self.dest / 'RULES.md'
        rules.write_text(rules.read_text(encoding='utf-8') + '\n[断](<missing.md>)\n', encoding='utf-8')
        broken = validate_layout(self.dest)
        self.assertFalse(broken['ok'])
        self.assertTrue(any('missing.md' in error for error in broken['errors']))
        rules.write_text(rules.read_text(encoding='utf-8').replace('missing.md', '<热件>_cold/INDEX.md'),
                         encoding='utf-8')
        pattern = validate_layout(self.dest)
        self.assertTrue(pattern['ok'], pattern['errors'])
        self.assertEqual(len(pattern['patterns']), 1)

    def test_validate_rejects_absolute_path_outside_declared_framework(self):
        self.install()
        outside = Path(self.tmp.name) / 'outside.md'
        outside.write_text('# 外部\n', encoding='utf-8')
        rules = self.dest / 'RULES.md'
        rules.write_text(rules.read_text(encoding='utf-8') + '\n[外](<' + outside.as_posix() + '>)\n',
                         encoding='utf-8')
        result = validate_layout(self.dest)
        self.assertFalse(result['ok'])
        self.assertTrue(any('不属于已声明的框架仓库' in error for error in result['errors']))

    def test_validate_enforces_route_booleans_and_missing_required(self):
        self.install()
        path = self.dest / 'AutoDL实验/框架/context_routes.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['history_is_current_instruction'] = True
        data['routes'][0]['required'] = ['GONE.md']
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result = validate_layout(self.dest)
        self.assertFalse(result['ok'])
        self.assertTrue(any('history_is_current_instruction' in error for error in result['errors']))
        self.assertTrue(any('GONE.md' in error for error in result['errors']))
        data['history_is_current_instruction'] = False
        data['full_document'] = False
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        self.assertTrue(any('full_document' in error
                            for error in validate_layout(self.dest)['errors']))

    def test_validate_is_read_only(self):
        self.install()
        before = tree_snapshot(self.dest)
        validate_layout(self.dest)
        validate_layout(self.root / 'project')
        self.assertEqual(tree_snapshot(self.dest), before)

    def test_validate_empty_directory_is_not_ok(self):
        empty = Path(self.tmp.name) / 'empty'
        empty.mkdir()
        result = validate_layout(empty)
        self.assertFalse(result['ok'])
        self.assertEqual(result['documents'], 0)
        self.assertTrue(any('context_routes.json' in error for error in result['errors']))

    def test_validate_generated_project_requires_route_manifest(self):
        self.install()
        (self.dest / 'AutoDL实验/框架/context_routes.json').unlink()
        result = validate_layout(self.dest)
        self.assertFalse(result['ok'])
        self.assertTrue(any('context_routes.json' in error for error in result['errors']))

    def test_validate_generated_project_requires_reading_plan_and_root_documents(self):
        self.install()
        (self.dest / 'AutoDL实验/框架/contracts/reading_required.json').unlink()
        result = validate_layout(self.dest)
        self.assertFalse(result['ok'])
        self.assertTrue(any('reading_required.json' in error for error in result['errors']))

        second = Path(self.tmp.name) / 'workspace-two'
        initialize_layout(self.root, second, self.root / 'profiles/agent-harness')
        (second / 'DOC_MAINTENANCE.md').unlink()
        missing = validate_layout(second)
        self.assertFalse(missing['ok'])
        self.assertTrue(any('DOC_MAINTENANCE.md' in error for error in missing['errors']))

    def test_validate_generated_project_rejects_external_route_path(self):
        self.install()
        path = self.dest / 'AutoDL实验/框架/context_routes.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['routes'][0]['entry'] = (self.root / 'kernel/contracts/CONTRACT.md').resolve().as_posix()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result = validate_layout(self.dest)
        self.assertFalse(result['ok'])
        self.assertTrue(any('工作区本地路径' in error for error in result['errors']))

    def test_validate_template_allows_framework_references_but_flags_them(self):
        result = validate_layout(self.root / 'project')
        self.assertTrue(result['ok'], result['errors'])
        self.assertGreater(result['links_checked'], 0)
        self.assertTrue(result['external_links'])
        self.assertTrue(all(item['framework'].endswith('framework') for item in result['external_links']))

    # ---- 符号链接 -------------------------------------------------------

    def symlink_or_skip(self, link, target):
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('当前环境不允许建立符号链接')

    def test_broken_destination_symlink_is_refused(self):
        elsewhere = Path(self.tmp.name) / 'uncreated-target'
        self.symlink_or_skip(self.dest, elsewhere)
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(elsewhere.exists())
        self.assertTrue(self.dest.is_symlink())

    def test_profile_symlink_escape_is_refused_before_creating_destination(self):
        secret = Path(self.tmp.name) / 'secret.md'
        secret.write_text('# 私有\n', encoding='utf-8')
        self.symlink_or_skip(self.root / 'profiles/agent-harness/LEAK.md', secret)
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.dest.exists())

    def test_template_symlink_is_not_copied(self):
        secret = Path(self.tmp.name) / 'secret.md'
        secret.write_text('# 私有\n', encoding='utf-8')
        self.symlink_or_skip(self.root / 'project/leak.md', secret)
        manifest = self.install()
        self.assertFalse((self.dest / 'leak.md').exists())
        self.assertIn('project/leak.md', [item['source'] for item in manifest['skipped']])
        self.assertTrue(validate_layout(self.dest)['ok'])

    def test_validate_flags_workspace_symlink_escape(self):
        self.install()
        secret = Path(self.tmp.name) / 'secret.md'
        secret.write_text('# 私有\n', encoding='utf-8')
        self.symlink_or_skip(self.dest / 'linked.md', secret)
        rules = self.dest / 'RULES.md'
        rules.write_text(rules.read_text(encoding='utf-8') + '\n[逃](<linked.md>)\n', encoding='utf-8')
        result = validate_layout(self.dest)
        self.assertFalse(result['ok'])
        self.assertTrue(any('符号链接' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()
