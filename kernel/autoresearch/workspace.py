# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09
# PURPOSE: Create genuine isolated topic code, environment, source references and research snapshots.
# ORIGIN: generic topic/bootstrap requirements from framework expectations
# CUMULATIVE-DELTA: 2026-11-09 从版本模板建立TOPIC、EVIDENCE与STATUS，骨架不代表验收；Initialization is separate from registration; preserve incomplete provisioning for diagnosis.
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from .runtime.store import Store
from .runtime.topics import Topics


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', value):
        raise ValueError('专题标识只能含字母、数字、下划线和短横线')
    return value


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(content)


def _git(argv, cwd):
    # File handles avoid named-pipe assumptions on confined Windows hosts.
    with tempfile.TemporaryDirectory(prefix='autoresearch-git-') as directory:
        out, err = Path(directory) / 'stdout', Path(directory) / 'stderr'
        with out.open('wb') as stdout, err.open('wb') as stderr:
            result = subprocess.run(['git', *argv], cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        if result.returncode:
            raise ValueError('Git操作失败：' + err.read_text(encoding='utf-8', errors='replace'))
        return out.read_text(encoding='utf-8', errors='strict').strip()


def _inside(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('路径越出独立工作区')
    return target


def provision_topic(workspace, *, topic, objective, requirements, owner_session,
                    code_repo=None, revision='HEAD', create_environment=True):
    root = Path(workspace).resolve()
    topic = _id(topic)
    if not root.is_dir() or not (root / '.autoresearch').is_dir():
        raise ValueError('请先用 init 建立独立工作区')
    for name, value in (('objective', objective), ('requirements', requirements), ('owner_session', owner_session)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(name + '必须明确给出')
    target = _inside(root, 'topics/' + topic)
    database = root / '.autoresearch/runtime.sqlite3'
    if database.is_file() and Topics(database, readonly=True).get_topic(topic) is not None:
        raise ValueError('专题已登记；不能重新建立它的代码或环境')
    # mkdir is the reservation: a failed provision remains inspectable and is never silently overwritten.
    target.mkdir(parents=True, exist_ok=False)
    record = {'topic': topic, 'owner_session': owner_session, 'objective': objective,
              'created_at': time.time(), 'status': 'provisioning', 'external_actions': False}
    _write(target / 'provision.json', json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    code = _inside(root, 'worktrees/' + topic)
    environment = _inside(root, 'environments/' + topic)
    branch = 'research/' + topic
    try:
        code.parent.mkdir(parents=True, exist_ok=True)
        if code.exists() or environment.exists():
            raise ValueError('代码或环境落点已存在；不能覆盖')
        if code_repo is not None:
            source = Path(code_repo).resolve()
            if not source.is_dir() or source == code or source.is_relative_to(code):
                raise ValueError('代码源仓库无效')
            if not isinstance(revision, str) or not revision or revision.startswith('-'):
                raise ValueError('代码版本不能为空或选项')
            commit = _git(['rev-parse', '--verify', revision + '^{commit}'], source)
            if not re.fullmatch('[0-9a-fA-F]{40,64}', commit):
                raise ValueError('Git版本未解析为提交')
            _git(['worktree', 'add', '-b', branch, str(code), commit], source)
            record['source_commit'] = commit
            record['isolation'] = 'git-worktree'
        else:
            code.mkdir()
            _git(['init', '--quiet'], code)
            _git(['symbolic-ref', 'HEAD', 'refs/heads/' + branch], code)
            record['source_commit'] = None
            record['isolation'] = 'empty-independent-git'
        record['code_path'] = code.relative_to(root).as_posix()
        if create_environment:
            environment.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(environment)], stdin=subprocess.DEVNULL)
            if result.returncode:
                raise ValueError('独立Python环境创建失败')
            python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            if not python.is_file():
                raise ValueError('独立Python环境没有解释器')
            record['python'] = python.relative_to(root).as_posix()
            record['environment_verified'] = True
            record['dependencies_installed'] = False
        else:
            record['python'] = None
            record['environment_verified'] = False
        header = '<!-- GENERATED-BY: topic provisioning; LAST-EDIT: initial; PURPOSE: research source; ORIGIN: explicit user input; CUMULATIVE-DELTA: initial -->\n'
        _write(target / 'REQUIREMENTS.md', header + '# 用户要求\n\n' + requirements + '\n')
        _write(target / 'OBJECTIVE.md', header + '# 目标\n\n' + objective + '\n')
        from .topic_documents import ensure_topic_documents
        record['research_documents'] = ensure_topic_documents(target, topic=topic, objective=objective)
        source_list = [f'topics/{topic}/OBJECTIVE.md', f'topics/{topic}/TOPIC.md', f'topics/{topic}/REQUIREMENTS.md']
        _write(target / 'sources.json', json.dumps(source_list, ensure_ascii=False, indent=2) + '\n')
        store = Store(root / '.autoresearch/runtime.sqlite3')
        topics = Topics(store)
        existing = topics.get_topic(topic)
        if existing is not None:
            raise ValueError('专题已登记；不把现有研究接到新建代码上')
        created = topics.create_topic(topic, title=objective, objective=objective, actor=owner_session)
        topics.configure_workspace(topic, created['version'], absolute_path=str(code), branch=branch, actor=owner_session)
        record['status'] = 'provisioned'
        record['ready_for_evaluation'] = False
        record['next'] = '准备研究方案及真实宿主证据；安装并验证评测依赖；不得把空专题称为已运行。'
    except BaseException as exc:
        record['status'] = 'incomplete'
        record['error'] = str(exc)
        raise
    finally:
        # This file was created by us and is a provisioning diagnostic, not a research ledger.
        (target / 'provision.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return record


def snapshot_topic(workspace, *, topic, exp_id, stage):
    root = Path(workspace).resolve()
    topic, exp_id = _id(topic), _id(exp_id)
    if stage not in ('before', 'after'):
        raise ValueError('快照stage必须是before或after')
    directory = _inside(root, 'topics/' + topic)
    document = directory / 'TOPIC.md'
    raw = document.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    dest = directory / 'snapshots' / exp_id
    dest.mkdir(parents=True, exist_ok=True)
    snapshot = dest / (stage + '.md')
    with snapshot.open('xb') as stream:
        stream.write(raw)
    receipt = {'topic': topic, 'exp_id': exp_id, 'stage': stage, 'sha256': digest,
               'source': document.relative_to(root).as_posix(), 'path': snapshot.relative_to(root).as_posix(),
               'bytes': len(raw), 'at': time.time()}
    _write(dest / (stage + '.json'), json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return receipt
