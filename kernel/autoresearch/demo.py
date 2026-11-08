# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: Independent kernel and target profile integration.
"""Offline end-to-end demonstration. No real authorization or cloud adapter."""
from __future__ import annotations
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from .runtime.store import Store, canonical_json
from .runtime.accounts import ApprovalEvidence, SourcePolicy, HMACSource
from .runtime.backend import BackendRegistry
from .runtime.execution import RunSpec, FakeAdapter, ResultBundle, digest
from .runtime.service import Runtime


def run_demo(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    state = root / '.autoresearch'
    state.mkdir()
    store = Store(state / 'runtime.sqlite3')
    key = secrets.token_bytes(32)
    source = ApprovalEvidence({'offline-fixture': SourcePolicy(HMACSource(key), frozenset({'demo-human'}))})
    adapter = FakeAdapter()
    def idle(resource_key, evidence, snapshot):
        return evidence.get('offline_fixture') is True and all(j['state'] in {'completed', 'cancelled', 'failed'} for j in adapter.jobs.values())
    def audit(conn, exp_id, attempts, evidence):
        return evidence == {'offline_fixture': True} and all(a['state'] == 'completed' for a in attempts)
    runtime = Runtime(store, adapter, source, audit_verifier=audit, idle_verifier=idle)
    BackendRegistry(store).register_new('demo-experiment', root / 'demo-experiment', goal='验证研究生命周期可以从空状态走通')
    runtime.scheduler.activate('demo-experiment')
    runtime.request_approval('demo-experiment', 0)
    event = {'event_id': 'fixture-grant', 'principal': 'demo-human', 'exp_id': 'demo-experiment',
             'series_id': 'demo-series', 'scope': 'run', 'expires_at': time.time() + 3600,
             'total_units': 100, 'action': 'grant'}
    event['signature'] = hmac.new(key, canonical_json(event).encode(), hashlib.sha256).hexdigest()
    runtime.accounts.authorize('offline-fixture', event, 'fixture-grant')
    owner = runtime.scheduler.takeover('demo-experiment', 'demo-owner')
    runtime.accounts.reserve('demo-series', 'demo-experiment', 'run', 20, 'demo-reservation')
    runtime.prepare('demo-experiment', store.get_experiment('demo-experiment')['version'])
    runtime.scheduler.enqueue('demo-experiment', 'demo-task', {'request_key': 'demo-run'}, 'demo-owner',
                              owner['generation'], paid=True, resources=['fixture:worker'])
    claim = runtime.scheduler.claim('demo-experiment', 'demo-owner', owner['generation'], 'demo-worker')
    config = {'backend': 'offline-fake', 'timeout_seconds': 1}
    fingerprint = digest(canonical_json(config).encode())
    spec = RunSpec('demo-experiment', 'demo-run', 'a'*64, 'b'*64, 'c'*64,
                   '/demo-unit', '/demo-unit/code', '/demo-unit/env', '/demo-unit/input', '/demo-unit/output',
                   ('synthetic-case',), 'demo-reservation', claim['token'], True, False,
                   execution_config=config, execution_config_fingerprint=fingerprint)
    attempt = runtime.queue(spec)
    runtime.submit(attempt)
    adapter.jobs['demo-run']['state'] = 'completed'
    runtime.reconcile(attempt)
    raw = canonical_json({'kind': 'synthetic-result', 'value': 1}).encode()
    adapter.bundle = ResultBundle({'results.json': raw}, {'results.json': digest(raw)})
    runtime.collect(attempt)
    runtime.scheduler.ack(claim['id'], 'demo-owner', owner['generation'], 'demo-worker', claim['token'])
    runtime.accounts.settle('demo-reservation', 7, 'fixture-settlement')
    runtime.scheduler.confirm_resource_idle('fixture:worker', {'offline_fixture': True, 'resource_key': 'fixture:worker', 'task_id': claim['id'], 'token': claim['token']})
    final = runtime.finalize('demo-experiment', store.get_experiment('demo-experiment')['version'], audit_evidence={'offline_fixture': True})
    report = {'offline_only': True, 'state': final['state'], 'submit_calls': adapter.submit_calls,
              'budget': runtime.accounts.get_account('demo-series'), 'attempt': runtime.execution.get(attempt),
              'note': '所有来源、远端执行、独立审查均为离线夹具；不代表真实研究结论或真实用户授权。'}
    (root / 'REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'REPORT.md').write_text('# 离线演示结果\n\n本演示用合成结果走通登记、预算预留、执行、取回、对账和结案。\n\n'
        f"状态：{final['state']}。模拟远端启动 {adapter.submit_calls} 次。预算上限 100 单位，实际消耗 7 单位。\n\n"
        '所有执行与授权均为临时夹具，不调用真实服务。\n', encoding='utf-8')
    return {'directory': str(root), 'state': final['state'], 'submit_calls': adapter.submit_calls,
            'spent_units': report['budget']['spent_units'], 'offline_only': True}
