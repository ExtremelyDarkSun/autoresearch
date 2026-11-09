# GENERATED-BY: portable remote environment acceptance
# LAST-EDIT: 2026-11-09
# PURPOSE: Verify real package metadata changes invalidate a remote environment fingerprint.
# ORIGIN: backends.ssh_worker.environment_fingerprint
# CUMULATIVE-DELTA: Actual temporary venv, no package installation and no network.
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from kernel.autoresearch.backends import ssh_worker as worker


class EnvironmentFingerprintTests(unittest.TestCase):
    def test_actual_distribution_change_invalidates_even_unchanged_declared_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'env'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(root)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            python = root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            cfg = root / 'pyvenv.cfg'
            first = worker.environment_fingerprint(str(python), [], str(cfg))
            with (Path(temporary) / 'site-path.txt').open('wb') as stream:
                subprocess.run([str(python), '-I', '-c',
                    "import sysconfig;print(sysconfig.get_path('purelib'))"],
                    check=True, stdout=stream, stderr=subprocess.DEVNULL)
            site = Path((Path(temporary) / 'site-path.txt').read_text().strip())
            self.assertTrue(site.resolve().is_relative_to(root.resolve()))
            dist = site / 'synthetic_probe-1.0.dist-info'
            dist.mkdir()
            metadata = dist / 'METADATA'
            metadata.write_text('Metadata-Version: 2.1\nName: synthetic-probe\nVersion: 1.0\n', encoding='utf-8')
            second = worker.environment_fingerprint(str(python), [], str(cfg))
            self.assertNotEqual(first, second)
            self.assertIn('synthetic-probe==1.0', worker._probe_python(str(python))['packages'])
            metadata.write_text('Metadata-Version: 2.1\nName: synthetic-probe\nVersion: 2.0\n', encoding='utf-8')
            third = worker.environment_fingerprint(str(python), [], str(cfg))
            self.assertNotEqual(second, third)


if __name__ == '__main__':
    unittest.main()
