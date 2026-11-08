# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/cli.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import argparse
import json
import sqlite3
from pathlib import Path
from kernel.autoresearch.runtime.store import Store
from kernel.autoresearch.runtime.scheduler import Scheduler
from kernel.autoresearch.runtime.legacy import LegacyImporter
from kernel.autoresearch.runtime.backend import BackendRegistry

def readonly_overview(path):
    db = Path(path).resolve()
    if not db.is_file():
        raise ValueError('数据库不存在。请先显式init。')
    conn = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {'experiments': [dict(r) for r in conn.execute('SELECT * FROM experiments ORDER BY id')], 'ownership': [], 'tasks': [], 'resources': [], 'budgets': [], 'active_experiments': [], 'controls': [], 'quarantines': []}
        for target, table in [('ownership', 'ownership'), ('tasks', 'tasks'), ('resources', 'resources'), ('budgets', 'budget_accounts'), ('active_experiments', 'scheduler_active'), ('controls', 'scheduler_control'), ('quarantines', 'scheduler_resource_quarantine')]:
            if table in tables:
                result[target] = [dict(r) for r in conn.execute(f'SELECT * FROM {table}')]
        quarantine_keys = {r['resource_key'] for r in result['quarantines']}
        for resource in result['resources']:
            resource['occupied'] = True
            resource['state'] = 'unknown' if resource['resource_key'] in quarantine_keys else 'leased'
        for target, table in [('executions', 'exec_attempts'), ('jobs', 'exec_jobs'), ('deliveries', 'delivery'), ('inbox', 'inbox_events'), ('questions', 'inbox_reply_tasks'), ('findings', 'policy_findings')]:
            result[target] = [dict(r) for r in conn.execute(f'SELECT * FROM {table}')] if table in tables else []
        delivery_states = {row['id']: row['status'] for row in result['deliveries']}
        for question in result['questions']:
            question['delivery_status'] = delivery_states.get(question.get('delivery_id'))
            question['answered'] = question['delivery_status'] == 'delivered'
        result['note'] = '租约过期不代表远端空闲。所有resources条目仍占用。'
        return result
    finally:
        conn.close()

def parser():
    p = argparse.ArgumentParser(description='HIL持久运行内核。默认不执行外部副作用。')
    p.add_argument('--db', required=True, help='显式数据库路径，禁止指向历史JSON证据')
    p.add_argument('--commit', action='store_true', help='确认本地状态变更，不授权GPU或外发')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    sub.add_parser('overview')
    a = sub.add_parser('register-new')
    a.add_argument('--exp', required=True)
    a.add_argument('--directory', required=True)
    a.add_argument('--goal', default='')
    a = sub.add_parser('activate')
    a.add_argument('--exp', required=True)
    a.add_argument('--goal', default='')
    a = sub.add_parser('import-legacy')
    a.add_argument('--source', required=True)
    a = sub.add_parser('takeover')
    a.add_argument('--exp', required=True)
    a.add_argument('--owner', required=True)
    a.add_argument('--lease-seconds', type=float, default=300)
    a.add_argument('--force', action='store_true')
    for name in ('enqueue', 'claim', 'ack', 'retry'):
        a = sub.add_parser(name)
        a.add_argument('--owner', required=True)
        a.add_argument('--generation', required=True, type=int)
        if name in ('enqueue', 'claim'):
            a.add_argument('--exp', required=True)
        if name == 'enqueue':
            a.add_argument('--key', required=True)
            a.add_argument('--payload-file', required=True)
            a.add_argument('--kind', default='message')
            a.add_argument('--target-agent-id')
        else:
            a.add_argument('--worker', required=True)
            if name in ('ack', 'retry'):
                a.add_argument('--task', required=True)
                a.add_argument('--token', required=True)
    a = sub.add_parser('control')
    a.add_argument('--pause-new', choices=('yes', 'no'))
    a.add_argument('--pause-paid', choices=('yes', 'no'))
    a.add_argument('--reason', required=True)
    return p

def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    try:
        if a.command == 'overview':
            out = readonly_overview(a.db)
        elif not a.commit:
            out = {'mode': 'plan', 'command': a.command, 'writes': False, 'external_actions': False, 'note': '添加全局--commit后才修改本地状态'}
        else:
            if a.command != 'init' and (not Path(a.db).is_file()):
                raise ValueError('数据库不存在。请先init --commit。')
            store = Store(a.db)
            scheduler = Scheduler(store)
            if a.command == 'init':
                out = {'database': store.db_path, 'initialized': True, 'external_actions': False}
            elif a.command == 'register-new':
                out = BackendRegistry(store).register_new(a.exp, a.directory, actor='cli', goal=a.goal)
            elif a.command == 'activate':
                out = scheduler.activate(a.exp, a.goal)
            elif a.command == 'import-legacy':
                out = LegacyImporter(store).import_experiment(a.source)
            elif a.command == 'takeover':
                out = scheduler.takeover(a.exp, a.owner, lease_seconds=a.lease_seconds, force=a.force)
            elif a.command == 'enqueue':
                payload = json.loads(Path(a.payload_file).read_text(encoding='utf-8-sig'))
                out = scheduler.enqueue(a.exp, a.key, payload, a.owner, a.generation, kind=a.kind, target_agent_id=a.target_agent_id)
            elif a.command == 'claim':
                out = scheduler.claim(a.exp, a.owner, a.generation, a.worker)
            elif a.command == 'ack':
                out = scheduler.ack(a.task, a.owner, a.generation, a.worker, a.token)
            elif a.command == 'retry':
                out = scheduler.retry(a.task, a.owner, a.generation, a.worker, a.token)
            elif a.command == 'control':
                out = scheduler.set_control(pause_new_experiments=None if a.pause_new is None else a.pause_new == 'yes', pause_paid=None if a.pause_paid is None else a.pause_paid == 'yes', reason=a.reason)
            else:
                raise ValueError('不支持的命令')
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'error': type(exc).__name__, 'reason': str(exc), 'command': a.command}, ensure_ascii=False))
        return 2
if __name__ == '__main__':
    raise SystemExit(main())
