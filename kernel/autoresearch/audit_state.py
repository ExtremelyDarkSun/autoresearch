# ORIGIN: generic source framework mechanisms
# LAST-EDIT: audit-host binding, 2026-11-09（K11 历史豁免不称 audited；额度含可信 extend；政策用宿主时间判生效）
# CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
# GENERATED-BY: framework synchronization, 2026-11-09
# PURPOSE: 三态审计的只读投影。历史阻断跨版本存活；历史豁免只说明不追溯，`audited` 恒为 False。
import json

_DECISION_LABELS = {
    "proceed": "可以推进",
    "minor_fix": "主会话小修自查后推进",
    "blocked": "重大问题暂不能推进",
}

_LEGACY_NOTE = "历史轮次：规则生效前已起跑，不追溯新审计要求。⛔ 未审计，不等于审过。"


def audit_state(gov, plan_id):
    """只读投影。返回 ok / decision / audited / legacy_exempt / problems / 额度等。"""
    with gov.store.transaction() as conn:
        plan = gov._row(conn, 'SELECT * FROM gov_plans WHERE plan_id=? ORDER BY version DESC LIMIT 1', (plan_id,))
        audits = gov._rows(conn, 'SELECT * FROM gov_audits WHERE plan_id=? ORDER BY index_no', (plan_id,))
        reports = gov._rows(conn, 'SELECT * FROM gov_audit_reports WHERE plan_id=? ORDER BY created_at, rowid', (plan_id,))
        responses = gov._rows(conn, 'SELECT * FROM gov_audit_responses')
        corrections = gov._rows(conn, 'SELECT * FROM gov_audit_corrections ORDER BY id')
        waivers = gov._rows(conn, 'SELECT * FROM gov_waivers')
        # 旧库缺 gov_audit_extensions ⇒ 视为 0 次扩额（只读兼容；不在这里建表/迁移）。
        extensions = gov._count(conn, 'gov_audit_extensions', plan_id)
    fp = plan['key_fingerprint'] if plan else None
    policy = gov.audit_policy(plan_id)
    legacy = gov.legacy_state(plan_id)
    corrected = {(x['report_id'], x['new_fingerprint']) for x in corrections}
    current = [x for x in reports if x['key_fingerprint'] == fp or (x['report_id'], fp) in corrected]
    latest = current[-1] if current else None
    limit = int(gov.policy['max_audits']) + int(extensions)
    state = {
        'plan_id': plan_id,
        'ok': False,
        'decision': 'blocked',
        'decision_label': _DECISION_LABELS['blocked'],
        'audit_decision': None,
        'audited': False,
        'required': policy['mode'] != 'off',
        'legacy_exempt': False,
        'legacy': legacy,
        'problems': [],
        'blocking_open': [],
        'audits_used': len(audits),
        'extensions': int(extensions),
        'limit': limit,
        'remaining': max(limit - len(audits), 0),
        'report_id': latest['report_id'] if latest else None,
        'independence': latest['independence'] if latest else None,
        'key_fingerprint': fp,
        'policy': policy,
        'note': None,
    }
    if not plan:
        state['problems'].append('方案不存在')
        return state

    if policy['mode'] == 'off':
        # 关闭来自宿主核过的用户消息，且晚于该方案最后一次 prepare；未审计 ≠ 审过。
        state.update(ok=True, audited=False, required=False, decision='proceed',
                     decision_label='审计被用户关闭（未审计；不等于审过）',
                     note='审计政策是 off：关闭来自一条宿主核过的用户消息，且晚于最后一次准备。'
                          '⛔ 未审计 ≠ 审过。')
        return state

    if not current and legacy.get('legacy_exempt'):
        state.update(
            ok=True, audited=False, legacy_exempt=True, required=True,
            decision='proceed',
            decision_label=str(legacy.get('label') or '历史豁免（未审计）'),
            note=str(legacy.get('label') or _LEGACY_NOTE),
            problems=[],
            report_id=None, independence=None,
        )
        return state

    problems, open_blocking = [], []
    unresolved_minor = False
    if not current:
        problems.append('当前版本没有审计，请 prepare_audit + record_audit'
                        + ('；或先 legacy_exempt 登记真实历史启动记录' if legacy.get('runs') else ''))
    handled = {(x['report_id'], x['issue_id']): x for x in responses}
    waived = {(x['report_id'], x['issue_id']) for x in waivers if x['key_fingerprint'] == fp}
    fixed = set()
    for correction in corrections:
        if correction['new_fingerprint'] == fp:
            fixed.update((correction['report_id'], x.get('issue_id')) for x in json.loads(correction['detail_json']))
    resolved = set()
    for row in reports:
        payload = json.loads(row['report_json'])
        resolved.update((x['report_id'], x['issue_id']) for x in payload.get('resolves', []))
    for row in reports:
        payload = json.loads(row['report_json'])
        for issue in payload.get('issues', []):
            key = (row['report_id'], issue['id'])
            closed = key in waived or key in resolved
            response = handled.get(key)
            if issue.get('blocking') and not closed:
                open_blocking.append(issue['id'])
            if closed:
                continue
            if not response and key not in fixed:
                problems.append(f"意见 {issue['id']} 还没有处置")
                unresolved_minor = True
            elif response and response['disposition'] == 'needs_user':
                problems.append(f"意见 {issue['id']} 待用户决定（needs_user）")
                unresolved_minor = True
            elif response and response['disposition'] == 'declined' and issue.get('blocking'):
                problems.append(f"意见 {issue['id']} 被 declined，但阻断没有关闭依据")
    if open_blocking:
        problems.append(f'阻断项未关：{open_blocking}')
    independence = latest['independence'] if latest else None
    if latest and gov.policy.get('require_verified_independence') and independence != 'verified':
        problems.append('审计独立性只有 declared，而政策要求 verified'
                        '（没有宿主核过的子会话证据时这条不会通过）')
    decision = 'proceed' if not problems else ('minor_fix' if current and not open_blocking and unresolved_minor else 'blocked')
    state.update(
        ok=not problems,
        decision=decision,
        decision_label=_DECISION_LABELS[decision],
        audit_decision=json.loads(latest['report_json']).get('decision') if latest else None,
        audited=bool(current),
        problems=problems,
        blocking_open=open_blocking,
        report_id=latest['report_id'] if latest else None,
        independence=independence,
    )
    return state
