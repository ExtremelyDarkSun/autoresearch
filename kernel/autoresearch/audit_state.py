# ORIGIN: generic source framework mechanisms
# LAST-EDIT: portable framework synchronization, 2026-11-09
# CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
# GENERATED-BY: framework synchronization, 2026-11-09
# PURPOSE: 三态审计的只读投影。历史阻断跨版本存活。
import json


def audit_state(gov, plan_id):
    with gov.store.transaction() as conn:
        plan = gov._row(conn, 'SELECT * FROM gov_plans WHERE plan_id=? ORDER BY version DESC LIMIT 1', (plan_id,))
        audits = gov._rows(conn, 'SELECT * FROM gov_audits WHERE plan_id=? ORDER BY index_no', (plan_id,))
        reports = gov._rows(conn, 'SELECT * FROM gov_audit_reports WHERE plan_id=? ORDER BY created_at, rowid', (plan_id,))
        responses = gov._rows(conn, 'SELECT * FROM gov_audit_responses')
        corrections = gov._rows(conn, 'SELECT * FROM gov_audit_corrections ORDER BY id')
        waivers = gov._rows(conn, 'SELECT * FROM gov_waivers')
    fp = plan['key_fingerprint'] if plan else None
    policy = gov.audit_policy(plan_id)
    corrected = {(x['report_id'], x['new_fingerprint']) for x in corrections}
    current = [x for x in reports if x['key_fingerprint'] == fp or (x['report_id'], fp) in corrected]
    latest = current[-1] if current else None
    problems, open_blocking = [], []
    unresolved_minor = False
    if not plan:
        problems.append('方案不存在')
    if policy['mode'] == 'off':
        problems.append('审计政策是 off，不代表审过')
    elif not current:
        problems.append('当前版本没有审计，请 prepare_audit + record_audit')
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
        problems.append('审计独立性只有 declared，而政策要求 verified')
    decision = 'proceed' if not problems else ('minor_fix' if current and not open_blocking and unresolved_minor else 'blocked')
    return {'ok': not problems, 'decision': decision,
            'decision_label': {'proceed':'可以推进', 'minor_fix':'主会话小修自查后推进', 'blocked':'重大问题暂不能推进'}[decision],
            'audit_decision': json.loads(latest['report_json']).get('decision') if latest else None,
            'problems': problems, 'blocking_open': open_blocking, 'audits_used': len(audits),
            'limit': int(gov.policy['max_audits']), 'report_id': latest['report_id'] if latest else None,
            'independence': independence, 'key_fingerprint': fp, 'policy': policy}
