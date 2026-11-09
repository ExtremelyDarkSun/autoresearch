# GENERATED-BY: versioned research continuation, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: 校验复盘中的下一步判据、证据、费用范围、负责人和审批需要；兼容旧记录但不补造信息。
# ORIGIN: framework_expectations E5; workflow review record_json
# CUMULATIVE-DELTA: 新增 schema_version=2；仅记录研究决定，不签发预算或执行授权。
"""复盘的继续推进信息。记录「需要审批」不等于已经获批。"""
from __future__ import annotations

import copy

SCOPES = ('none', 'small', 'large', 'submission')


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not (
        value.strip().startswith('<') and value.strip().endswith('>')) and value.strip().lower() not in {
        'todo', 'tbd', 'unknown', '未知', '待定', '待填', '待补', '<placeholder>'}


def validate(record, *, file_ref=None):
    """返回错误清单。旧记录只读兼容；新版本须完整，来源可由调用方核真实文件。"""
    if not isinstance(record, dict):
        return ['review 必须是对象']
    version = record.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2):
        return ['review.schema_version 只支持整数 1 或 2']
    if version == 1:
        if 'continuation' in record:
            return ['带 continuation 的新记录必须声明 schema_version=2']
        return []
    value = record.get('continuation')
    if not isinstance(value, dict):
        return ['review.continuation 必须是对象（schema_version=2）']
    errors = []
    for key in ('criterion', 'next_owner', 'approval_reason'):
        if not _text(value.get(key)):
            errors.append(f'review.continuation.{key} 必填且不能是占位')
    evidence = value.get('criterion_evidence')
    if not isinstance(evidence, list) or not evidence:
        errors.append('review.continuation.criterion_evidence 必须给下一步判据的证据来源')
    else:
        for index, source in enumerate(evidence):
            label = f'review.continuation.criterion_evidence[{index}]'
            if not isinstance(source, dict) or not _text(source.get('path')) or not _text(source.get('locator')):
                errors.append(label + ' 必须给 path 与可定位的文件来源')
            elif file_ref is not None:
                try:
                    checked = file_ref(source, label)
                    if 'sha256' in source and source['sha256'] != checked.get('sha256'):
                        errors.append(label + ' 的声明哈希与实际文件不符')
                except (ValueError, RuntimeError) as exc:
                    errors.append(str(exc))
    cost = value.get('cost_scope')
    if not isinstance(cost, dict):
        errors.append('review.continuation.cost_scope 必须是对象')
        cost = {}
    scope = cost.get('scope')
    if scope not in SCOPES:
        errors.append('review.continuation.cost_scope.scope 必须是 none/small/large/submission')
    units = cost.get('max_cost_units')
    if type(units) is not int or units < 0:
        errors.append('review.continuation.cost_scope.max_cost_units 必须是非负整数；未知不得写成 0')
    elif scope == 'none' and units != 0:
        errors.append('scope=none 必须明确无付费执行，max_cost_units=0')
    elif scope in SCOPES[1:] and units == 0:
        errors.append('付费执行的 max_cost_units 必须大于 0')
    if not _text(cost.get('basis')):
        errors.append('review.continuation.cost_scope.basis 必须说明费用上限的适用范围与依据')
    for key in ('series_id', 'reservation_id'):
        if key in cost and not _text(cost[key]):
            errors.append(f'review.continuation.cost_scope.{key} 不能是空引用')
    needed = value.get('needs_approval')
    if type(needed) is not bool:
        errors.append('review.continuation.needs_approval 必须显式为布尔值')
    elif scope in ('large', 'submission') and not needed:
        errors.append('large/submission 的下一步必须声明 needs_approval=true；执行前另核可信批准')
    if 'decision_id' in value and not _text(value['decision_id']):
        errors.append('review.continuation.decision_id 不能是空引用')
    # 本对象只作研究交接，不能借自报 approved 跳过下一次真实准入。
    if any(key in value for key in ('approved', 'authorized', 'approval_status')):
        errors.append('continuation 不接受自报 approved/authorized/approval_status；真实批准由准入服务核验')
    return errors


def project(record):
    """忠实投影已存记录，不声称审批已过，不对旧记录补造下一轮信息。"""
    problems = validate(record)
    version = record.get('schema_version', 1) if isinstance(record, dict) else None
    if version != 2 or problems:
        return {'schema_version': version, 'status': 'unknown', 'legacy': version == 1,
                'approval_status': 'unknown',
                'problems': problems or ['历史复盘未记录下一步成本、负责人及审批范围']}
    result = copy.deepcopy(record['continuation'])
    result.update(schema_version=2, status='recorded', legacy=False,
                  approval_status='pending' if result['needs_approval'] else 'not_required',
                  problems=[])
    return result


def render(record):
    """供 AI_WORK 与可追溯结论材料使用，页面作者仍自行组织研究叙述。"""
    value = project(record)
    if value['status'] != 'recorded':
        return ['下一步交接信息：unknown（旧记录缺字段或记录不可判，不自动视为可继续）']
    cost = value['cost_scope']
    return [f"下一步判据：{value['criterion']}",
            f"下一步负责人：{value['next_owner']}",
            f"下一步费用范围：{cost['scope']}，上限 {cost['max_cost_units']} 单位；{cost['basis']}",
            f"下一步是否需批准：{'需要，尚待可信准入核验' if value['needs_approval'] else '无需专项批准，执行仍核预算与权限'}；{value['approval_reason']}",
            '下一步判据证据：' + '; '.join(ref['path'] for ref in value['criterion_evidence'])]
