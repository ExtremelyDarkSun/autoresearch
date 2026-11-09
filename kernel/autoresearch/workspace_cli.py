# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09 documents 分支也按布局标记解析专题目录
# PURPOSE: Explicit CLI for topic provisioning and research snapshots.
# ORIGIN: none
# CUMULATIVE-DELTA: 2026-11-09 documents只补缺失文档，不覆写；Actual Git worktrees and Python environments, separately from metadata registration.
#   2026-11-09 专题目录由 workspace-layout 标记决定（无标记仍是 topics/<id>）；⛔ 不按目录存在与否猜布局。
import argparse
import json
from pathlib import Path
from .workspace import provision_topic, snapshot_topic
from .workspace_layout import resolve_layout


def main(argv=None):
    parser = argparse.ArgumentParser(description='建立专题代码隔离、独立Python环境及研究文档；不启动评测。')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('topic', help='新建专题文档、真实工作树和独立环境')
    p.add_argument('--workspace', required=True)
    p.add_argument('--topic', required=True)
    p.add_argument('--objective', required=True)
    p.add_argument('--requirements-file', required=True, help='原始用户要求文字；保留原文')
    p.add_argument('--owner-session', required=True)
    p.add_argument('--code-repo', help='明确指定代码源仓库；不指定则建立空的独立Git仓库')
    p.add_argument('--revision', default='HEAD')
    p.add_argument('--no-environment', action='store_true', help='只建代码与文档；回执明确未创建环境')
    p = sub.add_parser('documents', help='只补建缺失的TOPIC、EVIDENCE、STATUS骨架，不覆盖已有同类文档')
    p.add_argument('--workspace', required=True)
    p.add_argument('--topic', required=True)
    p = sub.add_parser('snapshot', help='实验前后保留专题主文档原始字节快照')
    p.add_argument('--workspace', required=True)
    p.add_argument('--topic', required=True)
    p.add_argument('--exp-id', required=True)
    p.add_argument('--stage', choices=('before', 'after'), required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'topic':
            requirements = Path(args.requirements_file).read_text(encoding='utf-8-sig')
            result = provision_topic(args.workspace, topic=args.topic, objective=args.objective,
                                     requirements=requirements, owner_session=args.owner_session,
                                     code_repo=args.code_repo, revision=args.revision,
                                     create_environment=not args.no_environment)
        elif args.command == 'documents':
            from .workspace import _id
            from .topic_documents import ensure_topic_documents
            root = Path(args.workspace).resolve()
            directory = resolve_layout(root).topic_dir(_id(args.topic))
            if not (root / '.autoresearch').is_dir() or not directory.is_dir():
                raise ValueError('只允许为现有工作区中的专题补建文档')
            result = ensure_topic_documents(directory, topic=args.topic)
        else:
            result = snapshot_topic(args.workspace, topic=args.topic, exp_id=args.exp_id, stage=args.stage)
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
