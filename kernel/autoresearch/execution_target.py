# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09
# PURPOSE: Bind actual local execution to provisioned code and Python environment.
# ORIGIN: workspace provisioning and research isolation expectations
# CUMULATIVE-DELTA: Read-only resolution; remote execution needs a separately verified runtime adapter.
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil


class ExecutionTarget:
    def __init__(self, workspace, *, require_provision=False):
        self.root = Path(workspace).resolve()
        self.require_provision = require_provision

    def _inside(self, value):
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('执行落点越出独立工作区')
        return path

    def resolve(self, topic, plan, argv):
        if not isinstance(topic, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', topic) or topic in ('.', '..'):
            raise ValueError('专题标识不合法')
        if not isinstance(plan, dict) or not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
            raise ValueError('执行目标需要计划与显式argv')
        executable_name = Path(argv[0]).name.lower()
        if executable_name in ('ssh', 'ssh.exe', 'plink', 'plink.exe'):
            raise ValueError('统一本地入口不能证明远端停止；远端任务须用带停止核验和资源租约的Runtime适配器')
        spec = plan.get('execution') or {}
        if not isinstance(spec, dict):
            raise ValueError('execution必须是对象')
        if spec.get('scope', 'local') != 'local':
            raise ValueError('远端执行须显式接入受管Runtime，不能冒用本地超时等于远端停机')
        if spec.get('resource_kind', 'cpu') != 'cpu':
            raise ValueError('GPU等共享资源须接实际资源租约；本地入口未配资源适配器时不放行')
        path = self._inside('topics/' + topic + '/provision.json')
        if not path.is_file():
            if self.require_provision:
                raise ValueError('专题尚未实际初始化独立代码环境：先执行workspace topic')
            return {'cwd': str(self.root), 'env_additions': {}, 'provision': None,
                    'python': None, 'execution_scope': 'local', 'remote_stop_verified': False,
                    'isolation_verified': False, 'source': 'legacy-explicit-library-fixture'}
        record = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(record, dict) or record.get('topic') != topic or record.get('status') != 'provisioned':
            raise ValueError('专题初始化记录不完整或专题不一致')
        code = self._inside(record['code_path'])
        if not code.is_dir() or not (code / '.git').exists():
            raise ValueError('真实独立代码仓库不存在')
        if spec.get('cwd') is not None and self._inside(spec['cwd']) != code:
            raise ValueError('计划执行目录与已初始化的独立代码落点不一致')
        python = (self.root / record['python']).absolute() if record.get('python') else None
        if python is not None and not python.parent.resolve().is_relative_to(self.root):
            raise ValueError('独立Python环境目录越出工作区')
        if self.require_provision and (python is None or record.get('environment_verified') is not True):
            raise ValueError('专题未创建独立Python环境；--no-environment只适用于资料初始化')
        additions = {'PYTHONNOUSERSITE': '1', 'PYTHONPATH': str(code)}
        if python is not None:
            if not python.is_file() or not (python.parent.parent / 'pyvenv.cfg').is_file():
                raise ValueError('独立环境解释器或pyvenv.cfg缺失')
            additions.update({'VIRTUAL_ENV': str(python.parent.parent),
                              'PATH': str(python.parent) + os.pathsep + os.environ.get('PATH', '')})
        found = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0], path=additions.get('PATH'))
        if not found:
            raise ValueError('显式执行程序不存在')
        executable = Path(found).resolve()
        is_python = re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?', executable_name) is not None
        if is_python:
            if python is None or os.path.normcase(os.path.abspath(found)) != os.path.normcase(str(python)):
                raise ValueError('实际Python解释器不是本专题独立环境中的解释器')
            if len(argv) > 1 and argv[1].endswith('.py'):
                script = (code / argv[1]).resolve()
                if not script.is_relative_to(code) or not script.is_file():
                    raise ValueError('执行脚本须来自本专题独立代码落点')
        return {'cwd': str(code), 'env_additions': additions,
                'provision': path.relative_to(self.root).as_posix(),
                'python': str(python) if python else None, 'executable': str(executable),
                'execution_scope': 'local', 'remote_stop_verified': False,
                'isolation_verified': True, 'dependencies_verified': False}
