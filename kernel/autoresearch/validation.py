# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: Independent kernel and target profile integration.
"""Run genuine isolated regression checks without subprocess output pipes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run_tests(root, include_plugins=False):
    root = Path(root)
    (root/'temp').mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    commands = [[sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', 'discover', '-s', 'kernel/autoresearch/runtime', '-p', 'test_*.py', '-q'],
                [sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-q']]
    if include_plugins:
        node = shutil.which('node')
        if not node:
            print('缺少 Node.js，无法执行插件自检。', file=sys.stderr)
            return 2
        for plugin in sorted((root/'kernel/hosts/dsh/plugins').glob('*')):
            candidates = set(plugin.glob('selftest*.mjs')) | set(plugin.glob('selftest*.js'))
            for test in sorted(candidates):
                commands.append([node, str(test)])
    for argv in commands:
        print('RUN:', ' '.join(argv), flush=True)
        result = subprocess.run(argv, cwd=root, env=env, stdin=subprocess.DEVNULL)
        if result.returncode:
            return result.returncode
    return 0
