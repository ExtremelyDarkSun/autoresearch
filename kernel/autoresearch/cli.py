# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: Independent kernel and target profile integration.
"""CLI for source validation, complete instructions and isolated local state."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .profiles import load_profile, validate_profile, evaluation_state, build_command

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT / 'profiles/agent-harness'


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def write_new(path, text):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    modules = {'workflow': '.workflow_cli', 'reporting': '.reporting_cli',
               'monitoring': '.monitoring_cli', 'decisions': '.decisions_cli',
               'progress': '.progress_cli', 'workspace': '.workspace_cli', 'budget': '.budget_cli',
               'issues': '.issues_cli', 'progress-host': '.progress_host_cli',
               'channels': '.channels_cli', 'remote': '.remote_cli'}
    if args and args[0] in modules:
        from importlib import import_module
        return import_module(modules[args[0]], __package__).main(args[1:])
    if args and args[0] == 'governance':
        from .governance_cli import main as governance_main
        return governance_main(args[1:])
    if args and args[0] == 'topics':
        from .runtime.topic_cli import main as topic_main
        return topic_main(args[1:])
    if args and args[0] == 'runtime':
        from .runtime.cli import main as runtime_main
        return runtime_main(args[1:])
    p = argparse.ArgumentParser(description='通用研究内核与 agent 比赛配置；外部部署需要显式配置。')
    sub = p.add_subparsers(dest='command', required=True)
    for name, help_text in (
        ('workflow', '事前材料、实际启动、结束与研究复盘'),
        ('reporting', '研究页完整供料、版本验收回执与只读诊断'),
        ('monitoring', '原话水位、独立意图监控与启动检查'),
        ('decisions', '审批HTML、双回执、入向回复与超时'),
        ('progress', '声明式进展采集与受管推进作业'),
        ('workspace', '专题真实代码隔离、环境和研究快照'),
        ('issues', '首错查重、负责人、修复复验和冷件索引'),
        ('progress-host', '真实宿主goal、入向与受管作业的推进适配'),
        ('channels', '通道配置诊断与认证收件监听'),
        ('remote', '受管远端运行与资源租约'),
        ('budget', '真实用户预算授权、共享额度预留、结算和只读状态')):
        sub.add_parser(name, help=help_text)
    a = sub.add_parser('doctor', help='检查代码、配置和实际部署边界')
    a.add_argument('--profile', default=str(DEFAULT_PROFILE))
    a = sub.add_parser('demo', help='在新目录运行合成离线研究生命周期')
    a.add_argument('--output', required=True)
    a = sub.add_parser('profile', help='校验配置或生成评测 argv，不执行')
    a.add_argument('--path', default=str(DEFAULT_PROFILE)); a.add_argument('--tasks'); a.add_argument('--output')
    a = sub.add_parser('instructions', help='完整合并内核和配置要求，测量预算或核对捕获文本')
    a.add_argument('--profile', default=str(DEFAULT_PROFILE)); a.add_argument('--bundle'); a.add_argument('--manifest'); a.add_argument('--captured')
    a = sub.add_parser('init', help='建立独立研究工作区，不修改此源码仓库')
    a.add_argument('--workspace', required=True); a.add_argument('--profile', default=str(DEFAULT_PROFILE))
    a = sub.add_parser('check-plan', help='检查事前计划结构与回归集合')
    a.add_argument('path')
    a = sub.add_parser('results', help='读取中性的逐题结果')
    a.add_argument('path'); a.add_argument('--profile', default=str(DEFAULT_PROFILE))
    a = sub.add_parser('compare', help='同题集、同测量条件下比较结果')
    a.add_argument('before'); a.add_argument('after'); a.add_argument('--profile', default=str(DEFAULT_PROFILE))
    a = sub.add_parser('selftest', help='运行内核与配置的离线测试')
    a.add_argument('--plugins', action='store_true', help='同时运行插件模拟宿主测试')
    a = p.parse_args(args)
    try:
        if a.command == 'doctor':
            from .context import instruction_manifest, public_manifest
            profile = load_profile(a.profile)
            errors = validate_profile(profile)
            out = {'python': sys.version.split()[0], 'profile': profile['id'], 'errors': errors,
                   'evaluation': evaluation_state(profile), 'source_root': str(ROOT),
                   'plugins_bundled': [x.name for x in (ROOT/'kernel/hosts/dsh/plugins').glob('*') if x.is_dir()],
                   'host_activation': '未检查当前宿主；源码存在不表示插件已加载',
                   'cloud': '未连接；需配置凭据引用、机器范围与绑定会话',
                   'feishu': '默认关闭；通用收发账本可接可信适配器'}
            if not errors:
                out['instructions'] = public_manifest(instruction_manifest(ROOT, a.profile))
            dump(out)
            return 2 if errors else 0
        if a.command == 'demo':
            from .demo import run_demo
            dump(run_demo(a.output)); return 0
        if a.command == 'profile':
            profile = load_profile(a.path)
            if a.tasks or a.output:
                if not a.tasks or not a.output:
                    raise ValueError('--tasks 和 --output 必须一起给出')
                dump({'argv': build_command(profile, tasks=a.tasks, output=a.output), 'executed': False})
            else:
                dump({'profile': profile['id'], 'errors': validate_profile(profile), 'evaluation': evaluation_state(profile)})
            return 2 if validate_profile(profile) else 0
        if a.command == 'instructions':
            from .context import instruction_manifest, render_bundle, public_manifest, verify_injection
            manifest = instruction_manifest(ROOT, a.profile)
            if a.bundle: write_new(a.bundle, render_bundle(manifest))
            if a.manifest: write_new(a.manifest, json.dumps(public_manifest(manifest), ensure_ascii=False, indent=2)+'\n')
            out = public_manifest(manifest)
            if a.captured:
                out['verification'] = verify_injection(manifest, Path(a.captured).read_text(encoding='utf-8-sig'))
            dump(out)
            return 2 if a.captured and not out['verification']['complete'] else 0
        if a.command == 'init':
            from .context import instruction_manifest, render_bundle, public_manifest
            from .runtime.store import Store
            destination = Path(a.workspace).resolve()
            if destination == ROOT or destination.is_relative_to(ROOT) and not destination.is_relative_to(ROOT/'.local'):
                raise ValueError('工作区应独立于源码；仓库内演示请使用 .local 下的新目录')
            manifest = instruction_manifest(ROOT, a.profile)
            destination.mkdir(parents=True, exist_ok=False)
            state = destination/'.autoresearch'; state.mkdir()
            write_new(destination/'AGENTS.md', render_bundle(manifest))
            write_new(state/'instruction-manifest.json', json.dumps(public_manifest(manifest), ensure_ascii=False, indent=2)+'\n')
            write_new(destination/'.gitignore', '.autoresearch/\nprivate/\ndata/\nresults/\n.env\n')
            store = Store(state/'runtime.sqlite3')
            from .governance import Governance
            from .runtime.topics import Topics
            from .runtime.service import Runtime
            Governance(store)
            Topics(store)
            Runtime(store)
            dump({'workspace':str(destination),'profile':manifest['profile'],'required_instruction_bytes':manifest['bundle_bytes'],
                  'suggested_host_max_bytes':manifest['suggested_max_bytes'],
                  'host_budget_hint': f"完整材料 {manifest['bundle_bytes']} B；参考宿主默认总预算 65536 B。安装前必须核对并提高总预算和单来源上限；本命令未修改宿主配置。",'external_actions':False,
                  'next':'配置真实评测与云机适配器后，核对宿主实际完整注入。'})
            return 0
        if a.command == 'check-plan':
            from .governance import validate_plan
            errors = validate_plan(json.loads(Path(a.path).read_text(encoding='utf-8-sig')))
            dump({'valid':not errors,'errors':errors,'scientific_verdict':None})
            return 2 if errors else 0
        if a.command in {'results','compare'}:
            from .adapters.agent_harness import read_results, compare_results
            profile = load_profile(a.profile)
            if a.command == 'results': out = read_results(a.path, profile)
            else: out = compare_results(read_results(a.before, profile), read_results(a.after, profile), profile=profile)
            dump(out)
            return 4 if out.get('comparable') is False else 0
        if a.command == 'selftest':
            from .validation import run_tests
            return run_tests(ROOT, include_plugins=a.plugins)
    except (ValueError, OSError, RuntimeError) as exc:
        dump({'error':type(exc).__name__,'reason':str(exc)})
        return 2
    return 2
