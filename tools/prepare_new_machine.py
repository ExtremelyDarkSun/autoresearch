# GENERATED-BY: new-machine recovery integration, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-13（当前框架同步）
# PURPOSE: Render machine-specific plugin deployment without modifying the host.
# ORIGIN: existing DSH deployment generator and explicit project filter policy
# CUMULATIVE-DELTA: 2026-11-13 接入观察者、补足推进器与当前读取导航。 Bind installed workspace paths, preserve global preferences and emit verifiable setup receipts.
"""Prepare deployment inputs. Does not install plugins, register tasks or connect to services."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(workspace, *, framework=ROOT, original=False):
    from kernel.autoresearch.project_layout import validate_layout
    from kernel.autoresearch.workspace_layout import resolve_layout
    root, framework = Path(workspace).resolve(), Path(framework).resolve()
    if original:
        required = [root / 'AutoDL实验/框架/code/runtime/topic_cli.py',
                    root / 'AutoDL实验/tool/topic_watchdog.py',
                    root / 'AutoDL实验/框架/state/topics.sqlite']
        if not all(path.is_file() for path in required):
            raise ValueError('原工程恢复模式缺源码入口或原topics数据库，不创建空库替代。')
        topics_relative = 'AutoDL实验/实验分析/topics'
        database = required[2]
        policy = root / '.autoresearch/setup/agent-instruction-filter.policy.json'
        source_policy = framework / 'project/install/agent-instruction-filter.policy.json'
        policy_text = source_policy.read_text(encoding='utf-8')
        if policy.exists() and policy.read_text(encoding='utf-8') != policy_text:
            raise ValueError('已有恢复过滤策略不同，不覆盖。')
        policy.parent.mkdir(parents=True, exist_ok=True)
        if not policy.exists():
            with policy.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(policy_text)
        schedule_argv = ['python', str(required[1]), '--availability-only', '--all', '--once',
                         '--root', str(root)]
    else:
        if not (root / '.autoresearch/layout-manifest.json').is_file():
            raise ValueError('请先用init建立分层工作区；恢复原工程请显式使用--original。')
        layout = resolve_layout(root)
        checked = validate_layout(root)
        if not checked['ok']:
            raise ValueError('工作区链接或路由未通过：' + '; '.join(checked['errors']))
        topics_relative = layout.topics
        database = root / '.autoresearch/runtime.sqlite3'
        policy = root / 'install/agent-instruction-filter.policy.json'
        schedule_argv = ['python', str(framework / 'kernel/autoresearch/backends/autodl/worker.py'),
                         '--workspace', str(root), '--targets-db', str(database), '--once']
    policy_data = json.loads(policy.read_text(encoding='utf-8'))
    expected = [('**/.dsh/AGENTS.md', 'keep'), ('**/AutoDL实验/框架/AGENTS.md', 'drop'),
                ('**/AutoDL实验/AGENTS.md', 'drop'), ('**/Kaggle平台/AGENTS.md', 'drop'),
                ('**/AGENTS.md', 'drop')]
    if [(r.get('match'), r.get('mode')) for r in policy_data.get('rules', [])] != expected:
        raise ValueError('四份项目入口过滤策略与恢复基线不同，请人工核对。')
    plugins = framework / 'kernel/hosts/dsh/plugins'
    entries = {'agent-instruction-filter': plugins / 'agent-instruction-filter/index.js',
               'doc-gap': plugins / 'dsh-doc-gap/index.js',
               'autodl-events': plugins / 'autodl-events/lib/index.js',
               'goal-observer': plugins / 'goal-observer/index.mjs',
               'goal-observer-ui': plugins / 'goal-observer-ui/index.js'}
    for name, path in entries.items():
        if not path.is_file():
            raise ValueError('缺少插件入口：' + name)
    deployment = {'workspace': root.as_posix(), 'plugins': {
        'agent-instruction-filter': {'enabled': True, 'mode': 'filter', 'childOnly': True,
                                     'policyPath': policy.as_posix(), 'onError': 'keep',
                                     'pluginPath': str(entries['agent-instruction-filter'].parent.as_posix())},
        'doc-gap': {'enabled': False, 'pluginPath': str(entries['doc-gap'].parent.as_posix()),
                    'projects': [root.as_posix()], 'docs': [],
                    'policy': {'scope': {'include': [], 'exclude': []}, 'anchor': ''}},
        'goal-observer': {'enabled': True, 'pluginPath': entries['goal-observer'].as_posix(),
                           'objectivePrefix': '[观察者]', 'timeoutMs': 240000,
                           'maxTokens': 16000, 'contextChars': 24000,
                           'notification': {'enabled': False, 'command': [], 'throttleMs': 300000}},
         'goal-observer-ui': {'enabled': True, 'pluginPath': entries['goal-observer-ui'].as_posix()},
         'autodl-events': {'enabled': True, 'pluginPath': entries['autodl-events'].as_posix(),
                          'pollMs': 5000, 'maxTargets': 1, 'fallback': 'hold',
                          'delivery': 'steer', 'projects': [root.as_posix()]}}}
    if original:
        events = root / 'temp/autodl_events'
        config = deployment['plugins']['autodl-events']
        for key, relative in {'eventDir': 'pending', 'deliveredDir': 'delivered',
                              'expiredDir': 'expired', 'logFile': 'delivered.jsonl',
                              'targetsFile': 'targets.json'}.items():
            config[key] = (events / relative).as_posix()
    directory = root / '.autoresearch/setup'
    path = directory / 'deployment.json'
    rendered = json.dumps(deployment, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') != rendered:
        raise ValueError('已有部署输入不同，不覆盖。先保留旧文件并核对机器配置。')
    directory.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(rendered)
    return {'ok': True, 'deployment': str(path),
            'sha256': hashlib.sha256(rendered.encode('utf-8')).hexdigest(),
            'topics': topics_relative, 'original_implementation': original,
            'targets_db': str(database), 'schedule_argv': schedule_argv,
            'host_preferences_to_verify': {'compaction-basic.auto': True},
            'next_argv': ['node', str(framework / 'kernel/hosts/dsh/tools/gen-profile-patch.mjs'),
                          '--deployment', str(path), '--out', str(directory / 'profile.patch.yml')],
            'host_modified': False, 'host_verified': False, 'tasks_registered': False,
            'live_validation': ['宿主加载apply与新pid', '主会话保留及子会话过滤实际输入',
                                 '观察者三轮continue与证据complete、真人暂停、额度停止',
                                 '目标通知显示的goal id、revision与round匹配',
                                '新专题与新会话targets绑定', '两次availability投影不改STATUS',
                                '真实凭据、授权及一个正式评测样例'],
            'note': '只生成当前机器参数。不能据此宣称新电脑恢复完成。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--original', action='store_true', help='恢复原工程，保留原库与temp/autodl_events，不做schema迁移')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.workspace, original=args.original), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'ok': False, 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
