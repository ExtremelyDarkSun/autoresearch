# LAST-EDIT: portable framework synchronization, 2026-11-09
# CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
# GENERATED-BY: framework synchronization, 2026-11-09
# PURPOSE: 审计叶子路径及报告文件绑定的纯函数。
# ORIGIN: generic mechanisms from plan_audit
import hashlib
import json


def leaves(value, prefix=""):
    if isinstance(value, dict) and value:
        out = {}
        for key, item in value.items():
            out.update(leaves(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list) and value:
        out = {}
        for index, item in enumerate(value):
            out.update(leaves(item, f"{prefix}.{index}"))
        return out
    return {prefix: value}


def changes(before, after):
    left, right = leaves(before), leaves(after)
    return [key for key in sorted(set(left) | set(right))
            if key not in left or key not in right or left[key] != right[key]]


def hard_path(path):
    parts = path.split('.')
    if parts[0] == 'criteria':
        return len(parts) < 2 or parts[1] in {'mode', 'metric', 'direction', 'threshold', 'stop_condition'}
    return (parts[0] in {'case_sets', 'previous_passes', 'change_set',
                         'baseline', 'hypothesis', 'goal', 'budget', 'execution_argv',
                         'implementation', 'id'}
            and parts[-1] != 'locator') or 'execution_argv' in parts or 'implementation' in parts


def report_digest(path):
    from pathlib import Path
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_bound_report(path, declaration):
    """核文件字节与短声明。真实消息来源仍由宿主 verifier 核验。"""
    from pathlib import Path
    raw = Path(path).read_bytes()
    report = json.loads(raw.decode('utf-8-sig'))
    expected = {'prepared_id': report['prepared_id'], 'decision': report['decision'],
                'decision_reason': report['decision_reason'], 'report_file': str(path),
                'report_sha256': hashlib.sha256(raw).hexdigest(),
                'issues': [{'id': x['id'], 'blocking': x['blocking']} for x in report['issues']]}
    if declaration != expected:
        raise ValueError('短声明与报告文件不一致')
    return report
