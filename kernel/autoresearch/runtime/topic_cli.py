# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: Independent kernel and target profile integration.
"""Explicit topic ownership CLI; reads never initialize a missing database."""
import argparse
import json
from pathlib import Path
import sqlite3
from .store import Store
from .topics import Topics, readonly_board, readonly_topic


def main(argv=None):
    p=argparse.ArgumentParser(description='长期专题、工作区绑定和可接续交接；会话名只是本地协调字段。')
    p.add_argument('--db',required=True)
    p.add_argument('--commit',action='store_true',help='写本地状态，不授予远端操作权限')
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('board')
    a=sub.add_parser('show');a.add_argument('--topic',required=True)
    a=sub.add_parser('create');a.add_argument('--topic',required=True);a.add_argument('--title',required=True);a.add_argument('--objective',required=True);a.add_argument('--workspace',required=True);a.add_argument('--branch',required=True)
    a=sub.add_parser('claim');a.add_argument('--topic',required=True);a.add_argument('--session',required=True);a.add_argument('--generation',type=int,required=True)
    for action in ('checkpoint','handoff','recover','complete'):
        a=sub.add_parser(action);a.add_argument('--topic',required=True);a.add_argument('--session',required=True);a.add_argument('--generation',type=int,required=True)
        a.add_argument('--file',required=True,help='完整checkpoint JSON；保留completed/pending/inflight/evidence/next_action')
        if action=='handoff':a.add_argument('--next-session',required=True)
        if action=='recover':a.add_argument('--reason',required=True)
    args=p.parse_args(argv)
    try:
        if args.action=='board':out=readonly_board(args.db)
        elif args.action=='show':out=readonly_topic(args.db,args.topic)
        elif not args.commit:out={'mode':'plan','action':args.action,'writes':False}
        else:
            path=Path(args.db)
            if args.action!='create' and not path.is_file():raise ValueError('数据库不存在；先create')
            path.parent.mkdir(parents=True,exist_ok=True)
            topics=Topics(Store(path))
            if args.action=='create':
                topic=topics.create_topic(args.topic,title=args.title,objective=args.objective)
                out=topics.configure_workspace(args.topic,topic['version'],absolute_path=str(Path(args.workspace).resolve()),branch=args.branch)
            elif args.action=='claim':out=topics.open_session(args.topic,args.session,expected_generation=args.generation)
            else:
                cp=json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
                if args.action=='checkpoint':out=topics.write_checkpoint(args.topic,args.session,args.generation,**cp)
                elif args.action=='handoff':out=topics.handoff(args.topic,args.session,args.generation,next_session_id=args.next_session,checkpoint=cp)
                elif args.action=='complete':out=topics.complete_topic(args.topic,args.session,args.generation,checkpoint=cp)
                else:out=topics.recover(args.topic,args.session,expected_generation=args.generation,reason=args.reason,inflight=cp.get('inflight',[]))
        print(json.dumps(out,ensure_ascii=False,indent=2,default=str));return 0
    except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'error':type(exc).__name__,'reason':str(exc)},ensure_ascii=False));return 2
