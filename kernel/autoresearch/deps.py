"""把评测依赖装进专题的独立 venv，并更新 provision.json 的就绪状态。

## 为什么需要这个模块

`workspace.py:101` 用 `python -m venv --without-pip` 建环境，`:109` 把
`dependencies_installed` 直接写成 `False`，`:129` 把 `ready_for_evaluation` 写成 `False`。
全仓再无第二处写入点 —— 所以**即使你把依赖装齐了，这两个字段也永远不会翻转**，
它们显示的是"建专题那一刻"的静态文本，不是状态。

本模块提供那个缺失的写入点：装完之后真的把 `dependencies_installed` 改对。

## 边界（刻意不做的）

- 不改 `ready_for_evaluation`：它取决于 `profile.toml` 的 `[evaluation]` 是否配齐
  （`configured=true`、`command` 非空、四个 limits 齐全），那是 `workflow check` 的职责，
  不是"装了个包"就该翻的。
- 不联网装任何东西除非显式 `--install`。默认只检查。
- 不碰全局 Python，只动专题自己的 venv。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ENV_DIR = 'AutoDL实验/实验记录/environments'
TOPIC_DIR = 'AutoDL实验/实验分析/topics'


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def venv_python(workspace: Path, topic: str) -> Path:
    """专题独立环境里的解释器路径。"""
    base = Path(workspace) / ENV_DIR / topic
    return base / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def provision_path(workspace: Path, topic: str) -> Path:
    return Path(workspace) / TOPIC_DIR / topic / 'provision.json'


def _run(python: Path, args: list[str], timeout: int = 1800) -> tuple[int, str, str]:
    proc = subprocess.run(
        [str(python), *args],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode, proc.stdout or '', proc.stderr or ''


def ensure_pip(python: Path) -> dict:
    """确保 venv 里有 pip。`--without-pip` 建的 venv 需要先离线自举。"""
    code, out, _ = _run(python, ['-m', 'pip', '--version'], timeout=120)
    if code == 0:
        return {'ok': True, 'bootstrapped': False, 'version': out.strip()}
    code2, out2, err2 = _run(python, ['-m', 'ensurepip'], timeout=300)
    if code2 != 0:
        return {'ok': False, 'bootstrapped': False, 'error': (err2 or out2).strip()[-600:]}
    code3, out3, _ = _run(python, ['-m', 'pip', '--version'], timeout=120)
    return {'ok': code3 == 0, 'bootstrapped': True, 'version': out3.strip()}


def parse_requirements(path: Path) -> list[str]:
    """读依赖清单：跳过空行与 # 注释，其余每行当作一个 pip 需求。

    用 utf-8-sig 读取：Windows 上记事本 / PowerShell 的 Out-File 默认写
    UTF-8 **带 BOM**，若按 utf-8 读，BOM（\\ufeff）会粘在第一个字符前面，
    导致 `# 注释行` 被当成依赖包名。
    """
    items: list[str] = []
    for raw in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        items.append(line)
    return items


def _req_name(spec: str) -> str:
    """从 'numpy>=1.26' 里取出 'numpy'；取不到就原样返回小写。"""
    for sep in ('>=', '<=', '==', '~=', '!=', '>', '<', '[', ';'):
        if sep in spec:
            spec = spec.split(sep, 1)[0]
    return spec.strip().lower().replace('_', '-').replace('.', '-')


def installed_packages(python: Path) -> dict[str, str]:
    code, out, _ = _run(python, ['-m', 'pip', 'list', '--format=json'], timeout=180)
    if code != 0:
        return {}
    try:
        data = json.loads(out)
    except Exception:
        return {}
    found: dict[str, str] = {}
    for row in data if isinstance(data, list) else []:
        name = str(row.get('name', '')).strip()
        if name:
            found[name.lower().replace('_', '-').replace('.', '-')] = str(row.get('version', ''))
    return found


def _update_provision(prov: Path, installed: bool, reqs: list[str]) -> dict | None:
    """把 dependencies_installed 写回 provision.json。返回更新后的片段。

    读取用 utf-8-sig：文件若是被 Windows 工具改过，可能带 BOM。
    """
    if not prov.is_file():
        return None
    try:
        data = json.loads(prov.read_text(encoding='utf-8-sig'))
    except Exception:
        return None
    data['dependencies_installed'] = bool(installed)
    data['dependencies_checked_at'] = _now()
    data['dependencies_requirement_count'] = len(reqs)
    prov.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {
        'dependencies_installed': data['dependencies_installed'],
        'dependencies_checked_at': data['dependencies_checked_at'],
        'dependencies_requirement_count': data['dependencies_requirement_count'],
    }


def run_deps(workspace, topic: str, requirements, install: bool) -> dict:
    """检查 / 安装专题依赖。返回可直接 dump 成 JSON 的报告。"""
    ws = Path(workspace).resolve() if workspace else Path.cwd()
    python = venv_python(ws, topic)

    report: dict = {
        'ok': True,
        'workspace': str(ws),
        'topic': topic,
        'python': str(python),
        'action': 'install' if install else 'check',
        'pip': None,
        'requirements': None,
        'install': None,
        'missing': [],
        'provision': None,
    }

    if not python.is_file():
        report['ok'] = False
        report['error'] = f'专题 {topic} 没有独立 venv'
        report['hint'] = '该专题是用 --no-environment 建的；要环境只能用不带该参数的方式重建专题'
        return report

    pip_state = ensure_pip(python)
    report['pip'] = pip_state
    if not pip_state.get('ok'):
        report['ok'] = False
        report['error'] = '环境里没有 pip，且 ensurepip 自举失败'
        return report

    reqs: list[str] = []
    if requirements:
        rp = Path(requirements)
        if not rp.is_file():
            report['ok'] = False
            report['error'] = f'依赖清单不存在：{rp}'
            return report
        reqs = parse_requirements(rp)
        report['requirements'] = {'path': str(rp), 'count': len(reqs), 'items': reqs}
    else:
        report['requirements'] = {'path': None, 'count': 0, 'items': [], 'note': '未给 --requirements，只检查不安装'}

    if install:
        if not reqs:
            report['ok'] = False
            if requirements:
                report['error'] = '依赖清单里没有有效条目（全是空行或 # 注释），没有可装的东西'
            else:
                report['error'] = '--install 需要同时给 --requirements（否则不知道装什么）'
            return report
        code, out, err = _run(python, ['-m', 'pip', 'install', *reqs], timeout=1800)
        report['install'] = {'exit': code, 'stdout_tail': out[-2000:], 'stderr_tail': err[-2000:]}
        report['ok'] = code == 0

    installed = installed_packages(python)
    report['installed'] = {'count': len(installed), 'sample': sorted(installed)[:60]}

    if reqs:
        missing = [_req_name(r) for r in reqs if _req_name(r) not in installed]
        report['missing'] = missing
        report['all_requirements_satisfied'] = not missing
    else:
        report['all_requirements_satisfied'] = None

    # 只在"这次真的装成功"或"检查发现全部满足"时，才把状态写成 true
    should_mark = bool(reqs) and report.get('all_requirements_satisfied') is True
    report['provision'] = _update_provision(provision_path(ws, topic), should_mark, reqs)

    return report
