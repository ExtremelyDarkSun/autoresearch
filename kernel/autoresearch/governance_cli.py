# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: portable framework synchronization, 2026-11-09
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
"""Governance CLI with explicit local mutation and no trusted verifier fallback."""
import argparse
import json
from pathlib import Path
import sqlite3
from .governance import Governance, GovernanceError
from .runtime.store import Store


def main(argv=None):
    p=argparse.ArgumentParser(description='研究治理登记；CLI提交的资料默认只标declared，不构造真实宿主证明。')
    p.add_argument('--db',required=True)
    p.add_argument('--commit',action='store_true')
    p.add_argument('--actor',default='local-cli')
    sub=p.add_subparsers(dest='action',required=True)
    for action in ('freeze','audit-record','audit-respond','lesson-add','lesson-revise','trigger','monitor-record','intent-new','sync-user','assess-update','audit-correct','audit-file'):
        a=sub.add_parser(action);a.add_argument('--file',required=True)
        if action in ('audit-record','audit-respond','lesson-revise','trigger','monitor-record','audit-correct','audit-file'):a.add_argument('--id',required=True)
    for action in ('plan','audit-state','audit-prepare','intent-state','lesson-state'):
        a=sub.add_parser(action);a.add_argument('--id',required=True)
    a=sub.add_parser('start-check');a.add_argument('--intent-id');a.add_argument('--source-session')
    a=sub.add_parser('audit-digest');a.add_argument('--file',required=True)
    a=sub.add_parser('lessons')
    args=p.parse_args(argv)
    writes=args.action in {'audit-correct','audit-file','freeze','audit-record','audit-respond','lesson-add','lesson-revise','trigger','monitor-record','audit-prepare','intent-new','sync-user','assess-update'}
    try:
        if args.action=='audit-digest':
            from .audit_support import report_digest
            print(report_digest(args.file));return 0
        if writes and not args.commit:
            print(json.dumps({'mode':'plan','action':args.action,'writes':False},ensure_ascii=False));return 0
        if not Path(args.db).is_file():raise ValueError('数据库不存在；先初始化独立工作区')
        # Governance normally initializes its tables. Read commands use SQLite read-only
        # connections, so they cannot create schema or change a missing table silently.
        if writes:
            gov=Governance(Store(args.db))
        else:
            class ReadStore:
                def connect(self):
                    conn=sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro',uri=True)
                    conn.row_factory=sqlite3.Row
                    return conn
                from contextlib import contextmanager
                @contextmanager
                def transaction(self):
                    conn=self.connect()
                    try:
                        conn.execute('PRAGMA query_only=ON')
                        conn.execute('BEGIN')
                        yield conn
                    finally:
                        conn.rollback()
                        conn.close()
            gov=object.__new__(Governance)
            gov.store=ReadStore();gov.verifier=None;gov.experiment_id=None;gov.policy=dict(Governance.DEFAULT_POLICY)
        body=json.loads(Path(args.file).read_text(encoding='utf-8-sig')) if hasattr(args,'file') else None
        if args.action=='freeze':out=gov.freeze_plan(body,actor=args.actor)
        elif args.action=='plan':out=gov.latest_plan(args.id)
        elif args.action=='audit-prepare':out=gov.prepare_audit(args.id,actor=args.actor)
        elif args.action=='audit-record':out=gov.record_audit(args.id,body,actor=args.actor)
        elif args.action=='audit-respond':out=gov.respond_audit(args.id,body,actor=args.actor)
        elif args.action=='audit-correct':out=gov.correct_audit(args.id,body,actor=args.actor)
        elif args.action=='audit-file':out=gov.record_audit_file(args.id,body['report_file'],body,actor=args.actor)
        elif args.action=='audit-state':out=gov.audit_state(args.id)
        elif args.action=='start-check':out=gov.check_start(intent_id=args.intent_id,source_session=args.source_session)
        elif args.action=='intent-state':out=gov.intent_state(args.id)
        elif args.action=='lesson-add':out=gov.add_lesson_candidate(body,actor=args.actor)
        elif args.action=='lesson-revise':out=gov.revise_lesson(args.id,body,actor=args.actor)
        elif args.action=='lesson-state':out=gov.lesson_state(args.id)
        elif args.action=='lessons':out=gov.lessons()
        elif args.action=='trigger':out=gov.register_trigger(args.id,body['kind'],body['detail'],actor=args.actor)
        elif args.action=='monitor-record':out=gov.record_monitor_report(args.id,body,actor=args.actor)
        elif args.action=='intent-new':out=gov.create_intent(body['intent_id'],quote=body['quote'],source_session=body['source_session'],seq=body['seq'],acceptance=body.get('acceptance',''),actor=args.actor)
        elif args.action=='sync-user':out=gov.sync_user_update(source_session=body['source_session'],seq=body['seq'],quote=body['quote'])
        elif args.action=='assess-update':out=gov.assess_update(source_session=body['source_session'],covers_seq=body['covers_seq'],decision=body['decision'],quote=body['quote'],answer_seq=body.get('answer_seq'),intent_id=body.get('intent_id'),actor=args.actor)
        else:raise ValueError('不支持的命令')
        if out is None:
            print(json.dumps({'exists':False},ensure_ascii=False));return 2
        print(json.dumps(out,ensure_ascii=False,indent=2,default=str))
        if isinstance(out,dict) and (out.get('ok') is False or out.get('allow') is False):return 9
        return 0
    except (ValueError,RuntimeError,OSError,sqlite3.Error,KeyError,TypeError) as exc:
        print(json.dumps({'error':type(exc).__name__,'reason':str(exc),'errors':getattr(exc,'errors',[])},ensure_ascii=False));return 2
