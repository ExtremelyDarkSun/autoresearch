# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/legacy.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
import time
from pathlib import Path

class LegacyImporter:

    def __init__(self, store):
        self.store = store
        with store.transaction() as conn:
            conn.execute('\n                CREATE TABLE IF NOT EXISTS legacy_evidence (\n                    exp_id TEXT NOT NULL REFERENCES experiments(id),\n                    path TEXT NOT NULL, sha256 TEXT NOT NULL,\n                    evidence_type TEXT NOT NULL, summary_json TEXT NOT NULL,\n                    imported_at REAL NOT NULL,\n                    PRIMARY KEY(exp_id,path,sha256)\n                );\n            ')

    @staticmethod
    def _read(path):
        raw = path.read_bytes()
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = raw.decode('utf-16')
        else:
            text = raw.decode('utf-8-sig')
        return (json.loads(text), hashlib.sha256(raw).hexdigest())

    def inspect(self, state_dir):
        directory = Path(state_dir).resolve()
        if not directory.is_dir():
            raise ValueError('旧实验目录不存在')
        observations, issues = ([], [])
        names = [('status.json', 'status'), ('decision.json', 'decision'), ('freeze.json', 'freeze'), (f'conclusions/{directory.name}.json', 'conclusion')]
        station_dir = directory / 'stations'
        if station_dir.is_dir():
            names += [(p.relative_to(directory).as_posix(), 'station') for p in sorted(station_dir.glob('*.json')) if p.is_file()]
        status = None
        merge_success = False
        for relative, kind in names:
            path = directory / relative
            if not path.is_file():
                if kind in ('status', 'decision', 'freeze', 'conclusion'):
                    issues.append({'kind': 'missing', 'path': relative})
                continue
            if not path.resolve().is_relative_to(directory):
                issues.append({'kind': 'outside_source', 'path': relative})
                continue
            try:
                obj, sha = self._read(path)
                if not isinstance(obj, dict):
                    raise ValueError('证据顶层必须是对象')
                summary = {k: obj[k] for k in ('exp', 'exp_id', 'station', 'state', 'blocked', 'exit_code', 'dry_run', 'status', 'resolution', 'decision_id', 'ts') if k in obj}
                observed_exp = obj.get('exp') or obj.get('exp_id')
                if observed_exp and observed_exp != directory.name:
                    issues.append({'kind': 'experiment_mismatch', 'path': relative, 'observed': observed_exp})
                observations.append({'path': relative, 'sha256': sha, 'type': kind, 'summary': summary})
                if kind == 'status':
                    status = obj
                if kind == 'station' and str(obj.get('station')) == '8' and (type(obj.get('exit_code')) is int) and (obj['exit_code'] == 0) and (obj.get('dry_run') is not True):
                    merge_success = True
            except (OSError, UnicodeError, ValueError) as exc:
                issues.append({'kind': 'unreadable', 'path': relative, 'error': f'{type(exc).__name__}: {exc}'})
        if status and merge_success and (status.get('blocked') is True or str(status.get('station')) not in ('8',)):
            issues.append({'kind': 'status_conflict', 'paths': ['status.json', 'stations/8.*.json'], 'reason': '状态记录与非dry-run归并记录不同步'})
        results = []
        for path in sorted(directory.glob('runs/**/task_results.jsonl')):
            if not path.resolve().is_relative_to(directory):
                continue
            rel = path.relative_to(directory).as_posix()
            raw = path.read_bytes()
            good, bad = (0, 0)
            try:
                lines = raw.decode('utf-8-sig').splitlines()
            except UnicodeError:
                lines, bad = ([], 1)
            for line in lines:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('结果行不是对象')
                    good += 1
                except ValueError:
                    bad += 1
            item = {'path': rel, 'sha256': hashlib.sha256(raw).hexdigest(), 'type': 'results', 'summary': {'rows': good, 'bad_rows': bad, 'completeness': 'unknown'}}
            observations.append(item)
            results.append(item)
            if good == 0 or bad:
                issues.append({'kind': 'invalid_results', 'path': rel, 'rows': good, 'bad_rows': bad})
        if not results:
            issues.append({'kind': 'no_results', 'reason': '没有可读结果文件，不推断执行完成'})
        return {'experiment_id': directory.name, 'source': str(directory), 'observations': observations, 'issues': issues, 'operational_state': 'unmigrated', 'authorization_imported': False, 'budget_imported': False}

    def import_experiment(self, state_dir):
        report = self.inspect(state_dir)
        exp = report['experiment_id']
        self.store.ensure_experiment(exp, goal='历史证据导入，未启用新运行链')
        inserted = 0
        with self.store.transaction() as conn:
            for item in report['observations']:
                cursor = conn.execute('INSERT OR IGNORE INTO legacy_evidence\n                    (exp_id,path,sha256,evidence_type,summary_json,imported_at)\n                    VALUES (?,?,?,?,?,?)', (exp, item['path'], item['sha256'], item['type'], json.dumps(item['summary'], ensure_ascii=False, sort_keys=True), time.time()))
                inserted += cursor.rowcount
            if inserted:
                self.store.emit(conn, exp, 'legacy_import', 'importer', {'inserted': inserted, 'issues': report['issues'], 'source': report['source'], 'authorization_imported': False})
        return dict(report, inserted=inserted)
