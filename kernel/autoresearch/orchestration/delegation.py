# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-orchestration/delegation.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat
MARKER_NAME = 'delegation_policy.json'

def read_policy_marker(state_dir, exp_id):
    path = Path(state_dir).resolve() / exp_id / MARKER_NAME
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return (False, None, 'marker absent; legacy experiment is not migrated')
    except OSError:
        return (True, None, 'marker metadata unavailable')
    if not stat.S_ISREG(metadata.st_mode):
        return (True, None, 'marker must be a regular file')
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or config.get('enabled') is not True:
            return (True, None, 'marker enabled=true is required')
        owner = config.get('policy_owner')
        db_path = config.get('db_path')
        if not isinstance(owner, str) or not owner.strip() or (not isinstance(db_path, str)) or (not db_path.strip()):
            return (True, None, 'marker requires policy_owner and db_path')
        if not Path(db_path).is_absolute():
            return (True, None, 'marker db_path must be absolute')
        return (True, {'policy_owner': owner, 'db_path': db_path}, 'marker valid')
    except (OSError, UnicodeError, ValueError, TypeError):
        return (True, None, 'marker unreadable or malformed')

def check_marked_delegation(state_dir, exp_id, *, required='registered'):
    present, config, reason = read_policy_marker(state_dir, exp_id)
    if not present:
        return {'exp_id': exp_id, 'marked': False, 'phase': 'legacy_unchecked', 'exit': 0, 'reason': reason}
    if config is None:
        return {'exp_id': exp_id, 'marked': True, 'phase': 'delegation_unavailable', 'exit': 9, 'reason': reason}
    result = check_delegation(config['db_path'], exp_id, policy_owner=config['policy_owner'], required=required)
    return {**result, 'marked': True, 'reason': result.get('reason', reason)}

def check_delegation(db_path, exp_id, *, policy_owner=None, required='registered'):
    if required not in ('registered', 'waiting', 'accepted'):
        raise ValueError('required must be registered, waiting or accepted')
    result = dict(exp_id=exp_id, registered=False, waiting=False, accepted=False, phase='delegation_unavailable', exit=9, reason='policy owner missing')
    if not isinstance(policy_owner, str) or not policy_owner.strip():
        return result
    path = Path(db_path).resolve()
    if not path.is_file():
        return {**result, 'reason': 'delegation database missing'}
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            owner = conn.execute('SELECT * FROM delegation_owners WHERE exp_id=?', (exp_id,)).fetchone()
            if owner is None:
                return {**result, 'reason': 'no persistent orchestrator registration'}
            if owner['root_session'] != policy_owner or owner['parent_session'] != policy_owner or owner['depth'] != 1 or (owner['generation'] < 1) or (owner['orchestrator_session'] == policy_owner):
                return {**result, 'reason': 'policy owner or registration binding mismatch'}
            tasks = [dict(row) for row in conn.execute('SELECT * FROM delegation_tasks\n                WHERE exp_id=? AND generation=? ORDER BY task_id', (exp_id, owner['generation']))]
            waiting = any((t['status'] in ('requested', 'dispatched', 'settled') for t in tasks))
            accepted = bool(tasks) and all((t['status'] == 'accepted' for t in tasks))
            result.update(registered=True, waiting=waiting, accepted=accepted, owner=dict(owner), tasks=tasks)
            if any((t['status'] == 'rejected' for t in tasks)):
                return {**result, 'phase': 'delegation_rejected', 'reason': 'orchestrator rejected a task'}
            if required == 'accepted' and (not accepted):
                return {**result, 'phase': 'delegation_waiting', 'exit': 3, 'reason': 'task settlement is not orchestrator acceptance'}
            if required == 'waiting' and (not waiting):
                return {**result, 'phase': 'delegation_not_waiting', 'exit': 3, 'reason': 'no current task awaits dispatch, settlement or acceptance'}
            return {**result, 'phase': 'delegation_' + required, 'exit': 0, 'reason': 'persistent delegation check passed'}
    except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
        return {**result, 'reason': 'delegation ledger unreadable: ' + type(error).__name__}

def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only persistent delegation check')
    parser.add_argument('--db', required=True)
    parser.add_argument('--exp', required=True)
    parser.add_argument('--policy-owner')
    parser.add_argument('--required', choices=('registered', 'waiting', 'accepted'), default='registered')
    args = parser.parse_args(argv)
    result = check_delegation(args.db, args.exp, policy_owner=args.policy_owner, required=args.required)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result['exit']
if __name__ == '__main__':
    raise SystemExit(main())
