# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: audit-host binding, 2026-11-09（默认注入 DSHHost；敏感动作精确参数；不提供 --legacy-exempt 绕过）
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
#   写库命令默认注入 host_evidence.DSHHost（workspace 由 --db 父目录判断，可用 --workspace 显式给）；
#   没有宿主证据的记录只标 declared。新增 policy / audit-waive / audit-extend / legacy-state 与
#   audit-file 的六键短声明路径；⛔ 不提供 --legacy-exempt 这类可自证豁免的开关。
"""Governance CLI with explicit local mutation and no trusted-verifier fallback.

约定
====
* `--db` 是必填：指向治理库（通常是 `<workspace>/.autoresearch/runtime.sqlite3`）。
* 写库命令默认注入 `host_evidence.DSHHost(workspace=…)`；**没有给 --proof 的记录只会是
  `declared`**，不会冒充 `verified`。
* `--workspace` 不给时按 `--db` 的父目录判断（父目录名是 `.autoresearch` 时再上一层）。
* `--proof` 文件只放宿主证据：`{"session_id": "...", "seq": 12}`。⛔ 不接受 expected /
  created_at / report —— expected 由治理层按动作参数现算，时间由宿主返回。
* ⛔ 没有 `--legacy-exempt` 这类开关：历史豁免只认同库真实 `wf_runs` 启动记录，且新轮次不能豁免。
"""
import argparse
import json
from pathlib import Path
import sqlite3

from .governance import Governance
from .runtime.store import Store

#: 会写库的命令。
_WRITE_ACTIONS = frozenset({
    'freeze', 'audit-prepare', 'audit-record', 'audit-file', 'audit-respond',
    'audit-correct', 'audit-waive', 'audit-extend', 'policy', 'trigger',
    'monitor-record', 'intent-new', 'sync-user', 'assess-update',
    'lesson-add', 'lesson-revise',
})

_EXPECTED_HELP = (
    'expected 命令由治理层按参数现算，调用方不能自己挑。先跑 --show-expected 看这条 JSON，'
    '再让用户把它原样发在真实消息里，然后用 --proof 指向那条消息。'
)


def _workspace_for(db: str, explicit: str | None) -> str:
    if explicit:
        return str(Path(explicit).resolve())
    base = Path(db).resolve().parent
    return str(base.parent if base.name == '.autoresearch' else base)


def _read_json(path: str | None):
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _read_governance(db: str) -> Governance:
    class ReadStore:
        def connect(self):
            conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row
            return conn

        from contextlib import contextmanager

        @contextmanager
        def transaction(self):
            conn = self.connect()
            try:
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                yield conn
            finally:
                conn.rollback()
                conn.close()

    gov = object.__new__(Governance)
    gov.store = ReadStore()
    gov.verifier = None
    gov.experiment_id = None
    gov.policy = dict(Governance.DEFAULT_POLICY)
    return gov


def _write_governance(db: str, workspace: str) -> Governance:
    from .host_evidence import DSHHost
    return Governance(Store(db), verifier=DSHHost(workspace=workspace))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='governance',
        description=(
            '研究治理登记。写库命令默认注入 DSHHost 核真实宿主证据；没有证据的记录只标 '
            'declared。审计政策默认 required（删配置也不免审）。'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '示例（关闭一条方案的审计；expected 由治理层构造）：\n'
            '  governance --db <ws>/.autoresearch/runtime.sqlite3 policy \\\n'
            '      --scope plan --target P1 --mode off --show-expected\n'
            '  governance --db <ws>/.autoresearch/runtime.sqlite3 --commit policy \\\n'
            '      --scope plan --target P1 --mode off --proof proof.json\n\n'
            + _EXPECTED_HELP
        ),
    )
    p.add_argument('--db', required=True,
                   help='治理库路径（例如 <workspace>/.autoresearch/runtime.sqlite3）')
    p.add_argument('--workspace',
                   help='显式可信工作区根（默认取 --db 的父目录；父目录名 .autoresearch 时取上一层）')
    p.add_argument('--commit', action='store_true',
                   help='真正写库；不加只打印计划（--show-expected 始终不写）')
    p.add_argument('--actor', default='local-cli',
                   help='登记者标识。核审计独立性时必须等于宿主核过的审计子会话号，否则拒绝登记')
    sub = p.add_subparsers(dest='action', required=True)

    a = sub.add_parser('freeze', help='校验并冻结一个方案版本')
    a.add_argument('--file', required=True, help='方案 JSON 文件')

    a = sub.add_parser('plan', help='只读：打印最新冻结的方案')
    a.add_argument('--id', required=True, help='方案号')

    a = sub.add_parser('audit-prepare', help='为当前方案版本准备一次独立审计（占一次额度）')
    a.add_argument('--id', required=True, help='方案号')
    a.add_argument('--owner', help='负责人会话号；默认等于 --actor')

    a = sub.add_parser('audit-record', help='直接登记一份完整审计报告 JSON（宿主证据核独立性）')
    a.add_argument('--prepared-id', '--id', dest='prepared_id', required=True,
                   help='prepare_audit 返回的 prepared_id')
    a.add_argument('--report', '--file', dest='report', required=True,
                   help='完整审计报告 JSON 文件（七项 coverage + issues + decision）')
    a.add_argument('--proof', help='宿主证据 JSON：{"session_id": "session-…", "seq": 12}；'
                                  '给了就核独立子会话的真实消息')

    a = sub.add_parser('audit-file', help='从报告文件登记：核六键短声明与报告文件字节 SHA256')
    a.add_argument('--prepared-id', '--id', dest='prepared_id', required=True,
                   help='prepare_audit 返回的 prepared_id')
    a.add_argument('--report', '--file', dest='report', required=True,
                   help='完整审计报告 JSON 文件')
    a.add_argument('--declaration', required=True,
                   help='六键短声明 JSON 文件：prepared_id / decision / decision_reason / '
                        'report_file / report_sha256 / issues[{id,blocking}]')
    a.add_argument('--proof', help='宿主证据 JSON：{"session_id": "session-…", "seq": 12}')

    a = sub.add_parser('audit-state', help='只读：三态投影（含 audited / legacy_exempt / 额度）')
    a.add_argument('--id', required=True, help='方案号')

    a = sub.add_parser('audit-respond', help='登记对审计每条意见的处置')
    a.add_argument('--id', required=True, help='审计报告号（AUD-…）')
    a.add_argument('--file', required=True, help='处置数组 JSON 文件')

    a = sub.add_parser('audit-correct', help='登记非阻断小修与自查（不占审计额度）')
    a.add_argument('--id', required=True, help='审计报告号（AUD-…）')
    a.add_argument('--file', required=True, help='小修数组 JSON 文件')

    a = sub.add_parser('audit-digest', help='只读：报告文件原始字节 SHA256（抄进六键短声明）')
    a.add_argument('--file', required=True, help='报告 JSON 文件')

    a = sub.add_parser(
        'audit-waive',
        help='用户可信 waive 一条审计意见（宿主核真实用户命令）',
        description='豁免一条审计意见。用户消息里必须原样包含由治理层构造的 waive 命令：'
                    '{"action":"plan_audit_waive","plan_id":…,"report_id":…,"issue_id":…,'
                    '"key_fingerprint":…}。时间必须不早于该报告。',
        epilog=_EXPECTED_HELP)
    a.add_argument('--report-id', required=True, help='审计报告号（AUD-…）')
    a.add_argument('--issue-id', required=True, help='该报告里的一条意见 id')
    a.add_argument('--reason', required=True, help='豁免理由（人话，非空）')
    a.add_argument('--proof', help='宿主证据 JSON：{"session_id": "session-…", "seq": 12}')
    a.add_argument('--show-expected', action='store_true',
                   help='只打印用户需要原样发出的命令 JSON，不写库')

    a = sub.add_parser(
        'audit-extend',
        help='可信扩额：额度用满后 +1 次审计（每次都要新的用户命令，不能自扩）',
        description='只在额度用满后生效。用户消息里必须原样包含：'
                    '{"action":"plan_audit_extend","plan_id":…,"used":…,"limit":…,'
                    '"key_fingerprint":…}；used/limit 变了旧指令就失效。',
        epilog=_EXPECTED_HELP)
    a.add_argument('--plan-id', required=True, help='方案号')
    a.add_argument('--reason', help='扩额理由（可选）')
    a.add_argument('--proof', help='宿主证据 JSON：{"session_id": "session-…", "seq": 12}')
    a.add_argument('--show-expected', action='store_true',
                   help='只打印用户需要原样发出的命令 JSON，不写库')

    a = sub.add_parser(
        'policy',
        help='写审计政策：required（默认）/ off（关闭必须宿主核过用户命令）',
        description='关闭审计：用户消息里必须原样包含 '
                    '{"action":"plan_audit_policy","scope":…,"target":…,"mode":"off"}，'
                    '且消息时间严格晚于该方案最后一次 prepare。',
        epilog=_EXPECTED_HELP)
    a.add_argument('--scope', required=True, choices=('global', 'plan', 'role'))
    a.add_argument('--target', required=True, help='global 用 *；plan 用方案号')
    a.add_argument('--mode', required=True, choices=('required', 'off'))
    a.add_argument('--proof', help='宿主证据 JSON：{"session_id": "session-…", "seq": 12}；off 必填')
    a.add_argument('--show-expected', action='store_true',
                   help='只打印用户需要原样发出的命令 JSON，不写库')

    a = sub.add_parser('start-check',
                       help='只读：起跑前核意图/未评估原话/未处置意见（不可判不放行）')
    a.add_argument('--intent-id', help='专项意图编号')
    a.add_argument('--intent-none', action='store_true',
                   help='显式声明本轮没有专项意图（与 --intent-id 互斥）')
    a.add_argument('--source-session',
                   help='来源会话号；没有意图时必须给，且该会话必须登记过用户原话')

    a = sub.add_parser('legacy-state',
                       help='只读：这个方案算不算真实历史轮次（只豁免，不称 audited）')
    a.add_argument('--plan-id', required=True, help='方案号')

    a = sub.add_parser('intent-new', help='新建专项意图（指向一条真实用户原话）')
    a.add_argument('--file', required=True, help='意图 JSON：intent_id/quote/source_session/seq')

    a = sub.add_parser('sync-user', help='登记一条真实用户原话（高水位同步）')
    a.add_argument('--file', required=True, help='JSON：source_session/seq/quote')

    a = sub.add_parser('assess-update', help='登记对一条用户原话的显式判断（intent / none）')
    a.add_argument('--file', required=True,
                   help='JSON：source_session/covers_seq/decision/quote[/answer_seq][/intent_id]')

    a = sub.add_parser('intent-state', help='只读：这条意图现在缺什么')
    a.add_argument('--id', required=True, help='意图编号')

    a = sub.add_parser('trigger', help='登记一个监控触发点（三类，心跳不算）')
    a.add_argument('--id', required=True, help='意图编号')
    a.add_argument('--file', required=True, help='JSON：kind/detail')

    a = sub.add_parser('monitor-record', help='登记一份独立监控报告并覆盖触发点')
    a.add_argument('--id', required=True, help='意图编号')
    a.add_argument('--file', required=True, help='监控报告 JSON')

    a = sub.add_parser('lesson-add', help='新增一条经验候选')
    a.add_argument('--file', required=True, help='经验候选 JSON')

    a = sub.add_parser('lesson-revise', help='修订一条经验候选（旧确认随即失效）')
    a.add_argument('--id', required=True, help='候选编号')
    a.add_argument('--file', required=True, help='要改的字段 JSON')

    a = sub.add_parser('lesson-state', help='只读：这条经验候选的确认状态')
    a.add_argument('--id', required=True, help='候选编号')

    a = sub.add_parser('lessons', help='只读：列出经验候选')
    a.add_argument('--status', help='按状态过滤（如 confirmed / pending / dropped）')
    return p


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.action == 'audit-digest':
            from .audit_support import report_digest
            print(report_digest(args.file))
            return 0

        # --show-expected 只读算命令，不写库、不需要 --commit。
        if getattr(args, 'show_expected', False):
            if not Path(args.db).is_file():
                raise ValueError('数据库不存在；先初始化独立工作区')
            gov = _read_governance(args.db)
            if args.action == 'policy':
                expected = gov.policy_command(scope=args.scope, target=args.target,
                                              mode=args.mode)
            elif args.action == 'audit-waive':
                expected = gov.waive_command(args.report_id, args.issue_id)
            elif args.action == 'audit-extend':
                expected = gov.extend_command(args.plan_id)
            else:
                raise ValueError(f'{args.action} 不支持 --show-expected')
            print(json.dumps({
                'expected': expected,
                'how_to_send': '把 expected 这段 JSON 原样发在真实用户消息里，'
                               '再用 --proof 指向那条消息（session_id + seq）。',
                'writes': False,
            }, ensure_ascii=False, indent=2))
            return 0

        if args.action in _WRITE_ACTIONS and not args.commit:
            print(json.dumps({'mode': 'plan', 'action': args.action, 'writes': False},
                             ensure_ascii=False))
            return 0
        if not Path(args.db).is_file():
            raise ValueError('数据库不存在；先初始化独立工作区')

        workspace = _workspace_for(args.db, args.workspace)
        if args.action in _WRITE_ACTIONS:
            gov = _write_governance(args.db, workspace)
        else:
            gov = _read_governance(args.db)

        if args.action == 'freeze':
            out = gov.freeze_plan(_read_json(args.file), actor=args.actor)
        elif args.action == 'plan':
            out = gov.latest_plan(args.id)
        elif args.action == 'audit-prepare':
            out = gov.prepare_audit(args.id, actor=args.actor, owner=args.owner)
        elif args.action == 'audit-record':
            out = gov.record_audit(args.prepared_id, _read_json(args.report),
                                   actor=args.actor, evidence=_read_json(args.proof))
        elif args.action == 'audit-file':
            out = gov.record_audit_file(args.prepared_id, args.report,
                                        _read_json(args.declaration),
                                        actor=args.actor,
                                        evidence=_read_json(args.proof))
        elif args.action == 'audit-state':
            out = gov.audit_state(args.id)
        elif args.action == 'audit-respond':
            out = gov.respond_audit(args.id, _read_json(args.file), actor=args.actor)
        elif args.action == 'audit-correct':
            out = gov.correct_audit(args.id, _read_json(args.file), actor=args.actor)
        elif args.action == 'audit-waive':
            out = gov.waive(args.report_id, args.issue_id, reason=args.reason,
                            evidence=_read_json(args.proof), actor=args.actor)
        elif args.action == 'audit-extend':
            out = gov.extend_audit(args.plan_id, evidence=_read_json(args.proof),
                                   actor=args.actor, reason=args.reason or '')
        elif args.action == 'policy':
            out = gov.set_policy(scope=args.scope, target=args.target, mode=args.mode,
                                 actor=args.actor, evidence=_read_json(args.proof))
        elif args.action == 'start-check':
            if args.intent_none and args.intent_id:
                raise ValueError('--intent-id 与 --intent-none 只能给一个')
            out = gov.check_start(intent_id=None if args.intent_none else args.intent_id,
                                  source_session=args.source_session)
        elif args.action == 'legacy-state':
            out = gov.legacy_state(args.plan_id)
        elif args.action == 'intent-new':
            body = _read_json(args.file) or {}
            out = gov.create_intent(body['intent_id'], quote=body['quote'],
                                    source_session=body['source_session'], seq=body['seq'],
                                    acceptance=body.get('acceptance', ''), actor=args.actor)
        elif args.action == 'sync-user':
            body = _read_json(args.file) or {}
            out = gov.sync_user_update(source_session=body['source_session'],
                                       seq=body['seq'], quote=body['quote'])
        elif args.action == 'assess-update':
            body = _read_json(args.file) or {}
            out = gov.assess_update(source_session=body['source_session'],
                                    covers_seq=body['covers_seq'],
                                    decision=body['decision'], quote=body['quote'],
                                    answer_seq=body.get('answer_seq'),
                                    intent_id=body.get('intent_id'), actor=args.actor)
        elif args.action == 'intent-state':
            out = gov.intent_state(args.id)
        elif args.action == 'trigger':
            body = _read_json(args.file) or {}
            out = gov.register_trigger(args.id, body['kind'], body['detail'], actor=args.actor)
        elif args.action == 'monitor-record':
            out = gov.record_monitor_report(args.id, _read_json(args.file), actor=args.actor)
        elif args.action == 'lesson-add':
            out = gov.add_lesson_candidate(_read_json(args.file), actor=args.actor)
        elif args.action == 'lesson-revise':
            out = gov.revise_lesson(args.id, _read_json(args.file), actor=args.actor)
        elif args.action == 'lesson-state':
            out = gov.lesson_state(args.id)
        elif args.action == 'lessons':
            out = gov.lessons(status=args.status)
        else:
            raise ValueError('不支持的命令')
        if out is None:
            print(json.dumps({'exists': False}, ensure_ascii=False))
            return 2
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        if isinstance(out, dict) and (out.get('ok') is False or out.get('allow') is False):
            return 9
        return 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'reason': str(exc),
                          'errors': getattr(exc, 'errors', [])}, ensure_ascii=False))
        return 2
