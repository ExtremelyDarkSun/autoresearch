# GENERATED-BY: public synchronization privacy checks
# LAST-EDIT: 2026-11-13
# PURPOSE: Catch deployment channel IDs in extracted source without rejecting neutral examples.
# ORIGIN: tools.check_repository known-pattern scanner
# CUMULATIVE-DELTA: Cover chat/user IDs, default-off neutral notification and ignored private state.
from pathlib import Path
import tempfile
import unittest
from tools.check_repository import inspect


class PublicPrivacyTests(unittest.TestCase):
    def test_recipient_ids_in_extracted_source_are_rejected(self):
        for prefix in ('oc_', 'ou_', 'on_'):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'plugin.mjs'
                path.write_text('const destination = "' + prefix + 'a' * 32 + '"\n', encoding='utf-8')
                result = inspect(folder)
                self.assertFalse(result['ok'])
                self.assertTrue(any('private channel identity' in error for error in result['errors']))

    def test_neutral_notification_configuration_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'deployment.json').write_text(
                '{"notification":{"enabled":false,"command":[]}}\n', encoding='utf-8')
            self.assertTrue(inspect(folder)['ok'])

    def test_private_runtime_artifacts_are_not_public_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder) / '.local' / 'notification.json'
            private.parent.mkdir()
            private.write_text('oc_' + 'a' * 32, encoding='utf-8')
            result = inspect(folder)
            self.assertTrue(result['ok'])
            self.assertEqual(result['files'], [])


if __name__ == '__main__':
    unittest.main()
