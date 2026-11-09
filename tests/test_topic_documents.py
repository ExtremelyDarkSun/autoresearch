# GENERATED-BY: research document integration tests
# LAST-EDIT: 2026-11-09
# PURPOSE: Verify versioned scaffold use, safe historical backfill and non-acceptance of empty status.
# ORIGIN: upstream three-document scaffold and public provisioning requirements
# CUMULATIVE-DELTA: New integration regression cases using synthetic research only.
import hashlib
from pathlib import Path
import tempfile
import unittest
from kernel.autoresearch.topic_documents import ensure_topic_documents
from kernel.autoresearch.reporting import page_problems


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
