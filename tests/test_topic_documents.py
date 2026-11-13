# GENERATED-BY: research document integration tests
# LAST-EDIT: 2026-11-13 加 DESIGN_* 判红（含 archive/ 不追溯）、等待簿空格式与安装/索引接线
# PURPOSE: Verify versioned scaffold use, safe historical backfill, non-acceptance of empty status,
#          the waiting-book rule (no DESIGN_* beside research documents) and install wiring.
# ORIGIN: upstream three-document scaffold and public provisioning requirements
# CUMULATIVE-DELTA: New integration regression cases using synthetic research only.
#   2026-11-13 新增：专题直接层 DESIGN_* 判红且消息点名 WAITING.md；archive/ 内部历史件只回执不判红；
#   等待簿模板必须是真实空格式（四行 · 唯一出处 · 解掉删行 · 过期复核 · 无实例条目）；
#   真实模板安装后 WAITING.md 进 layout-manifest 且安装副本的索引链接仍可打开。
import hashlib
import contextlib
import io
import json
import re
from pathlib import Path
import tempfile
import unittest

from kernel.autoresearch.project_layout import initialize_layout, validate_layout
from kernel.autoresearch.reporting import page_problems
from kernel.autoresearch.topic_documents import ensure_topic_documents, topic_document_problems

ROOT = Path(__file__).resolve().parents[1]
#: 等待簿的项目模板落点（与 topic_documents.WAITING_BOOK 同路径）。
WAITING_RELATIVE = 'AutoDL实验/实验分析/WAITING.md'
#: 跨专题机制清单的项目模板落点（跨专题资产住在实验分析层，不在任何 topics/<专题>/ 下）。
MECHANISMS_RELATIVE = 'AutoDL实验/实验分析/MECHANISMS.md'
PROJECT_WAITING = ROOT / 'project' / WAITING_RELATIVE


def tree_snapshot(root):
    """目录树快照（名字 / 字节 / sha256），用来证明检查只读。"""
    snapshot = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            snapshot[path.relative_to(root).as_posix()] = (len(raw), hashlib.sha256(raw).hexdigest())
    return snapshot


class TopicDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'topic'

    def test_three_documents_use_templates_and_empty_page_is_not_report(self):
        result = ensure_topic_documents(self.directory, topic='synthetic', objective='研究目标', today='2026-01-02')
        self.assertEqual(set(result['created']), {'TOPIC.md', 'STATUS.md', 'EVIDENCE_2026-01-02.md'})
        self.assertFalse(result['report_accepted'])
        self.assertIn('研究目标', (self.directory / 'TOPIC.md').read_text(encoding='utf-8'))
        self.assertTrue(page_problems((self.directory / 'STATUS.md').read_text(encoding='utf-8')))
        self.assertIn('复算命令', (self.directory / 'EVIDENCE_2026-01-02.md').read_text(encoding='utf-8'))
        for entry in result['documents']:
            self.assertEqual(hashlib.sha256((self.directory / entry['path']).read_bytes()).hexdigest(), entry['sha256'])
            template = Path(__file__).resolve().parents[1] / 'kernel/templates' / entry['template']
            self.assertEqual(hashlib.sha256(template.read_bytes()).hexdigest(), entry['template_sha256'])

    def test_backfill_preserves_bytes_and_does_not_create_second_evidence(self):
        self.directory.mkdir()
        (self.directory / 'TOPIC.md').write_bytes(b'original\r\nresearch')
        (self.directory / 'EVIDENCE_2025-01-01.md').write_bytes(b'older measured observations')
        result = ensure_topic_documents(self.directory, topic='synthetic', today='2026-01-02')
        self.assertEqual(result['created'], ['STATUS.md'])
        self.assertFalse((self.directory / 'EVIDENCE_2026-01-02.md').exists())
        self.assertEqual((self.directory / 'TOPIC.md').read_bytes(), b'original\r\nresearch')
        self.assertIn('不表示没有历史数据', (self.directory / 'STATUS.md').read_text(encoding='utf-8'))
        again = ensure_topic_documents(self.directory, topic='synthetic', today='2026-01-03')
        self.assertEqual(again['created'], [])

    def test_bad_template_fails_before_directory_creation(self):
        with self.assertRaises(FileNotFoundError):
            ensure_topic_documents(self.directory, topic='synthetic', template_root=Path(self.tmp.name) / 'missing')
        self.assertFalse(self.directory.exists())

    def test_unsafe_topic_rejected(self):
        with self.assertRaises(ValueError):
            ensure_topic_documents(self.directory, topic='../escape')

    # ---- 等待簿规则：专题直接层不许 DESIGN_* --------------------------------

    def test_direct_design_document_is_rejected_and_names_the_waiting_book(self):
        ensure_topic_documents(self.directory, topic='synthetic', today='2026-01-02')
        (self.directory / 'DESIGN_WAIT_FOR_APPROVAL.md').write_text('# 等待期设计\n', encoding='utf-8')
        before = tree_snapshot(self.directory)
        result = topic_document_problems(self.directory)
        self.assertFalse(result['ok'])
        self.assertEqual(result['design'], ['DESIGN_WAIT_FOR_APPROVAL.md'])
        self.assertTrue(result['read_only'])
        self.assertTrue(any(WAITING_RELATIVE in problem for problem in result['problems']),
                        result['problems'])
        self.assertTrue(any('DESIGN_WAIT_FOR_APPROVAL.md' in problem for problem in result['problems']))
        self.assertEqual(result['waiting_book'], WAITING_RELATIVE)
        # 只读：目录里一个字节都不许变。
        self.assertEqual(tree_snapshot(self.directory), before)

    def test_archive_history_is_reported_but_not_judged(self):
        self.directory.mkdir()
        archive = self.directory / 'archive' / 'nested'
        archive.mkdir(parents=True)
        (self.directory / 'archive' / 'DESIGN_OLD_20260101.md').write_text('# 归档件\n', encoding='utf-8')
        (archive / 'DESIGN_DEEPER.md').write_text('# 更深的归档件\n', encoding='utf-8')
        (self.directory / 'snapshots').mkdir()
        (self.directory / 'snapshots' / 'DESIGN_SNAPSHOT.md').write_text('# 快照\n', encoding='utf-8')
        result = topic_document_problems(self.directory)
        self.assertTrue(result['ok'], result['problems'])
        self.assertEqual(result['design'], [])
        self.assertEqual(result['archived_design'],
                         ['DESIGN_OLD_20260101.md', 'nested/DESIGN_DEEPER.md'])

    def test_direct_directory_named_design_is_also_rejected(self):
        """按名字判：把设计塞进 `DESIGN_x/` 目录同样算第二落点，不能靠目录躲过。"""
        self.directory.mkdir()
        (self.directory / 'DESIGN_BUNDLE').mkdir()
        (self.directory / 'DESIGN_BUNDLE' / 'note.md').write_text('# 藏在目录里的设计\n', encoding='utf-8')
        result = topic_document_problems(self.directory)
        self.assertFalse(result['ok'])
        self.assertEqual(result['design'], ['DESIGN_BUNDLE'])

    def test_missing_topic_directory_is_not_created(self):
        result = topic_document_problems(self.directory)
        self.assertFalse(result['ok'])
        self.assertTrue(any('不存在' in problem for problem in result['problems']))
        self.assertFalse(self.directory.exists())

    # ---- CLI 接线：workspace documents --check ---------------------------

    def test_workspace_cli_documents_check_rejects_design_and_stays_read_only(self):
        from kernel.autoresearch.workspace_cli import main as workspace_main
        workspace = Path(self.tmp.name) / 'workspace'
        (workspace / '.autoresearch').mkdir(parents=True)
        topic_dir = workspace / 'topics' / 'synthetic'   # 无布局标记 ⇒ 旧布局 topics/<topic>
        topic_dir.mkdir(parents=True)
        (topic_dir / 'TOPIC.md').write_text('# 合成专题\n', encoding='utf-8')
        before = tree_snapshot(workspace)

        def run_check(topic='synthetic'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = workspace_main(['documents', '--workspace', str(workspace),
                                       '--topic', topic, '--check'])
            return code, json.loads(output.getvalue())

        code, payload = run_check()
        self.assertEqual(code, 0)
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['read_only'])
        self.assertEqual(tree_snapshot(workspace), before)   # --check 不补建任何文档

        (topic_dir / 'DESIGN_WAIT_FOR_APPROVAL.md').write_text('# 等待期设计\n', encoding='utf-8')
        code, payload = run_check()
        self.assertEqual(code, 2)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['design'], ['DESIGN_WAIT_FOR_APPROVAL.md'])
        self.assertTrue(any(WAITING_RELATIVE in problem for problem in payload['problems']))

        (topic_dir / 'DESIGN_WAIT_FOR_APPROVAL.md').unlink()
        (topic_dir / 'archive').mkdir()
        (topic_dir / 'archive' / 'DESIGN_OLD.md').write_text('# 归档件\n', encoding='utf-8')
        code, payload = run_check()
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload['archived_design'], ['DESIGN_OLD.md'])

        # 专题不存在时如实判红，但不再替它建目录。
        code, payload = run_check('absent')
        self.assertEqual(code, 2)
        self.assertFalse(payload['ok'])
        self.assertFalse((workspace / 'topics' / 'absent').exists())

    # ---- 等待簿模板：真实空格式，不是口号 --------------------------------

    def test_waiting_book_is_a_real_empty_template(self):
        self.assertTrue(PROJECT_WAITING.is_file(), WAITING_RELATIVE)
        text = PROJECT_WAITING.read_text(encoding='utf-8')
        # 四行条目格式（一条只回答四件事）。
        for line in ('- 在等什么：', '- 等到了立刻做什么：', '- 起跑前必须满足：', '- 设计细节在哪：'):
            self.assertIn(line, text)
        self.assertIn('<等审批 / 等读数 / 等机器 / 作废>', text)
        self.assertIn('登记：<YYYY-MM-DD>', text)   # 过期复核要从这一格算，不留无日期的僵尸条目
        # 唯一出处、完成后清理、过期复核三条硬规则都在。
        self.assertIn('只落在这里', text)
        self.assertIn('解掉的就删行', text)
        self.assertIn('超过 3 天没动过的条目必须复核', text)
        self.assertIn('DESIGN_', text)
        self.assertIn('archive/', text)
        # 真实空格式：没有任何实例条目（模板占位 `### W<n>` 可以，真的 W1/W2 不行）。
        self.assertIsNone(re.search(r'^### W\d', text, re.M), '等待簿模板里不许留实例条目')
        self.assertIn('现在没有挂着的条目', text)

    def test_mechanisms_list_is_a_real_empty_template(self):
        """跨专题机制清单：只给字段与两档结论，不复制任何历史机制条目。"""
        path = ROOT / 'project' / MECHANISMS_RELATIVE
        self.assertTrue(path.is_file(), MECHANISMS_RELATIVE)
        text = path.read_text(encoding='utf-8')
        for token in ('改善哪个过程指标', '适用与不适用', '结论与证据', 'M 号', '`成功`', '`未判`'):
            self.assertIn(token, text)
        # 空清单：没有任何 M<数字> 条目（表头与字段说明不算）。
        self.assertIsNone(re.search(r'(?m)^\|\s*M\d', text), '机制清单不许留实例条目')
        self.assertIn('当前没有机制条目', text)
        # 跨专题位置：文件不在任何 topics/<专题>/ 下。
        self.assertNotIn('topics', path.relative_to(ROOT / 'project').parts)

    def test_waiting_book_and_mechanisms_are_installed_and_indexed(self):
        """安装清单（layout-manifest）与索引链接都指向等待簿与机制清单。

        ⚠️ 只断言与这两份跨专题文档/分析索引有关的链接错误：整仓还有别人并行改的文档，
        拿「全工作区零错误」当本测试的条件会把无关改动变成假红。
        """
        def cross_topic_errors(result):
            return [error for error in result['errors']
                    if 'WAITING' in error or 'MECHANISMS' in error or '_INDEX' in error]

        with tempfile.TemporaryDirectory(prefix='cross-topic-install-') as tmp:
            destination = Path(tmp) / 'workspace'
            manifest = initialize_layout(ROOT, destination, ROOT / 'profiles/agent-harness')
            self.assertTrue((destination / WAITING_RELATIVE).is_file())
            self.assertTrue((destination / MECHANISMS_RELATIVE).is_file())
            # 回执里的 path 是目标相对路径（模板根 project/ 不进回执）。
            installed = [entry['path'] for entry in manifest['files']]
            self.assertIn(WAITING_RELATIVE, installed)
            self.assertIn(MECHANISMS_RELATIVE, installed)
            index = (destination / 'AutoDL实验/实验分析/_INDEX.md').read_text(encoding='utf-8')
            self.assertIn('WAITING.md', index)
            self.assertIn('MECHANISMS.md', index)
            framework = destination / 'AutoDL实验/框架'
            reading = json.loads((framework / 'contracts/reading_required.json').read_text(encoding='utf-8'))
            registered = {entry['id']: entry for entry in reading['entries']}
            for reading_id, relative in [('R8', MECHANISMS_RELATIVE), ('R9', WAITING_RELATIVE)]:
                entry = registered[reading_id]
                self.assertFalse(entry['path'].startswith('project/'))
                self.assertEqual((framework / entry['path']).resolve(), (destination / relative).resolve())
                self.assertIn(entry['anchor'], (framework / entry['path']).read_text(encoding='utf-8'))
                self.assertEqual(entry['scope'], 'full_document')
            self.assertEqual(cross_topic_errors(validate_layout(destination)), [])
        # 模板本身：索引里指向这两份文档的链接能打开。
        template_index = ROOT / 'project/AutoDL实验/实验分析/_INDEX.md'
        self.assertIn('WAITING.md', template_index.read_text(encoding='utf-8'))
        self.assertIn('MECHANISMS.md', template_index.read_text(encoding='utf-8'))
        self.assertEqual(cross_topic_errors(validate_layout(ROOT / 'project')), [])


if __name__ == '__main__':
    unittest.main()
