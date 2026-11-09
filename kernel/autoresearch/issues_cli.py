# GENERATED-BY: issues ledger CLI migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：record / status / assign / fix / verify / reopen / archive / index 八个子命令
# PURPOSE: §9 问题台账的命令行入口 —— 首错登记、查重、负责人、修复、复验（可核 argv + 退出码）、冷件与索引。
# ORIGIN: kernel/autoresearch/issues.py 的公共操作；⛔ 不含具体事故编号、机器、读数或个人通道。
# CUMULATIVE-DELTA: 首版。只读子命令（status / index）零写；写子命令一律走台账事务。

"""问题台账命令行（框架行为预期 §9）。

用法（参数的准确写法以 `--help` 为准）：

    python -m kernel.autoresearch.issues_cli --workspace W record \\
        --summary "…" --owner <人/会话> --scope <受影响范围> --exitcode N \\
        [--source PATH ...] [--sources FILE] [--dedupe-key K] [--actor A] -- <首错命令 argv...>

    ... status [--issue ISSUE-000001]
    ... assign --issue ID --owner <负责人> --text "…" (--source PATH | --sources FILE)
    ... fix    --issue ID --text "改了什么" (--source …) [--exitcode N -- <修复命令 argv...>]
    ... verify --issue ID --text "复验了什么" (--source …) --exitcode N -- <复验命令 argv...>
    ... reopen --issue ID --text "怎么又出现了" (--source …)
    ... archive --issue ID [--actor A]
    ... index

⛔ 本工具**不执行**任何命令：`verify` 记录的是「有人用这条 argv 跑出这个退出码」，
台账只证明这条完成记录；命令是否真跑过、是否针对这个 bug，仍由 agent / 人核。

证据
====

`--source PATH`（可重复）把整个文件当证据，locator 记成 `L1-L<行数>`；
需要精确到行就用 `--sources FILE`，文件是 JSON 数组：

    [{"path": "results/run.log", "locator": "L40-L58"}]

退出码
======

    0  已登记 / 只读查询成功
    9  规则拒绝（未结案不能归档、重复归档、复验前没修复、passed 与退出码不一致…）
    2  输入或数据错误（工作区未初始化、文件不存在、JSON 坏、台账损坏）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch.issues import IssueRuleError, Issues  # noqa: E402


def _dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _split_argv(tokens):
    """`--` 之后原样当作命令 argv（⛔ 不经过 argparse 改写）。"""
    if "--" in tokens:
        index = tokens.index("--")
        return tokens[:index], tokens[index + 1:]
    return tokens, None


def _sources(workspace, paths, sources_file):
    """把 `--source PATH` 与 `--sources FILE` 合成 `Issues._evidence` 要的形状。"""
    root = Path(workspace).resolve()
    refs = []
    for raw in paths or []:
        path = (root / raw).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"证据文件不存在或越出工作区：{raw}")
        try:
            count = len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            raise ValueError(f"证据文件必须是 UTF-8 文本：{raw}") from None
        refs.append({"path": raw, "locator": f"L1-L{max(count, 1)}"})
    if sources_file:
        path = Path(sources_file)
        if not path.is_absolute():
            path = (root / sources_file).resolve()
        if not path.is_file():
            raise ValueError(f"证据清单文件不存在：{sources_file}")
        try:
            payload = json.loads(path.read_bytes().decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"证据清单不是合法 JSON：{exc}") from None
        if not isinstance(payload, list):
            raise ValueError("证据清单必须是 JSON 数组")
        for item in payload:
            if isinstance(item, str):
                refs.append({"path": item, "locator": "L1"})
            elif isinstance(item, dict):
                refs.append(item)
            else:
                raise ValueError("证据清单的每一项必须是路径或 {path, locator}")
    return refs


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m kernel.autoresearch.issues_cli",
        description="§9 问题台账：首错登记 / 查重 / 负责人 / 修复 / 复验 / 冷件索引。"
                    "所有写入走台账事务；status 与 index 只读。⛔ 本工具不执行任何命令。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    # `--workspace` / `--actor` 放在 subparser 上但用 SUPPRESS 默认值：写在子命令前后都认，
    # 只有确实给了才覆盖根解析器的值。
    common.add_argument("--workspace", default=argparse.SUPPRESS,
                        help="工作区根目录（含 .autoresearch/）")
    common.add_argument("--actor", default=argparse.SUPPRESS, help="执行者标识（本地登记字段）")
    parser.add_argument("--workspace", default=None,
                        help="工作区根目录（含 .autoresearch/）；也可写在子命令之后")
    parser.add_argument("--actor", default="agent", help="执行者标识（本地登记字段）")

    sub = parser.add_subparsers(dest="action", required=True)
    node = sub.add_parser("record", parents=[common],
                          help="登记首错；同一 command+scope（或同一 dedupe-key）只算同一个 issue")
    node.add_argument("--summary", required=True)
    node.add_argument("--owner", required=True, help="负责人（人标识或会话标识）")
    node.add_argument("--scope", required=True, help="受影响范围（步骤/模块/题单…）")
    node.add_argument("--exitcode", type=int, required=True, help="首错的真实退出码（信号用负值）")
    node.add_argument("--source", action="append", default=[], help="证据文件（工作区内相对路径，可重复）")
    node.add_argument("--sources", default=None, help="证据清单 JSON 数组（元素是路径或 {path, locator}）")
    node.add_argument("--dedupe-key", default=None,
                      help="显式归并键（命令里带临时路径时用）；⛔ 不给就不做模糊合并")
    node.epilog = "`--` 之后是首错命令的完整 argv，必须与真实执行的一致。"

    status = sub.add_parser("status", parents=[common], help="只读：一个问题或全集当前状态")
    status.add_argument("--issue", default=None)

    assign = sub.add_parser("assign", parents=[common], help="改负责人（必须显式给 --owner）")
    assign.add_argument("--issue", required=True)
    assign.add_argument("--owner", required=True)
    assign.add_argument("--text", required=True, help="为什么换人（说明文字，不是负责人本身）")
    assign.add_argument("--source", action="append", default=[])
    assign.add_argument("--sources", default=None)

    fix = sub.add_parser("fix", parents=[common], help="记修复（说明 + 证据；可带修复命令与退出码）")
    fix.add_argument("--issue", required=True)
    fix.add_argument("--text", required=True)
    fix.add_argument("--exitcode", type=int, default=None, help="修复命令的退出码（给了就必须给命令）")
    fix.add_argument("--source", action="append", default=[])
    fix.add_argument("--sources", default=None)

    verify = sub.add_parser("verify", parents=[common],
                               help="复验：必须给可核 argv + 真实退出码 + 证据（退出码 0 才算通过）")
    verify.add_argument("--issue", required=True)
    verify.add_argument("--text", required=True)
    verify.add_argument("--exitcode", type=int, required=True)
    verify.add_argument("--passed", default=None, choices=["true", "false"],
                        help="可选自述；与退出码不一致会被拒（结论以退出码为准）")
    verify.add_argument("--source", action="append", default=[])
    verify.add_argument("--sources", default=None)
    verify.epilog = "`--` 之后是这次复验跑的完整 argv。"

    reopen = sub.add_parser("reopen", parents=[common], help="结案/已修复后又出问题：重开")
    reopen.add_argument("--issue", required=True)
    reopen.add_argument("--text", required=True)
    reopen.add_argument("--source", action="append", default=[])
    reopen.add_argument("--sources", default=None)

    archive = sub.add_parser("archive", parents=[common], help="把整段已结案历史归冷件（不删热件历史）")
    archive.add_argument("--issue", required=True)

    sub.add_parser("index", parents=[common], help="只读：冷件索引（来自台账的 archived 事件）")
    return parser


def main(argv=None):
    tokens = list(sys.argv[1:] if argv is None else argv)
    head, tail = _split_argv(tokens)
    args = _parser().parse_args(head)
    if not getattr(args, "workspace", None):
        _dump({"ok": False, "error": "UsageError", "reason": "必须给 --workspace（子命令前后都认）"})
        return 2
    workspace = Path(args.workspace)
    if not workspace.is_dir() or not (workspace / ".autoresearch").is_dir():
        _dump({"ok": False, "error": "NotInitialized",
               "reason": f"工作区未初始化或缺 .autoresearch/：{workspace}"})
        return 2
    try:
        if args.action in ("status", "index"):
            issues = Issues(workspace, readonly=True)
            if args.action == "index":
                _dump({"ok": True, "archive_index": issues.archive_index()})
            elif args.issue:
                item = issues.status(args.issue)
                _dump({"ok": True, "issue": {key: value for key, value in item.items() if key != "history"},
                       "history": item["history"]})
            else:
                _dump({"ok": True, "issues": [
                    {key: value for key, value in item.items() if key != "history"}
                    for item in issues.status()]})
            return 0

        issues = Issues(workspace)
        if args.action == "record":
            if not tail:
                raise ValueError("首错必须给真实 argv（`--` 之后）；只写说明不算登记")
            result = issues.record(summary=args.summary, owner=args.owner, command=tail,
                                   exitcode=args.exitcode,
                                   sources=_sources(workspace, args.source, args.sources),
                                   scope=args.scope, actor=args.actor, dedupe_key=args.dedupe_key)
            _dump({"ok": True, "event": result})
            return 0
        if args.action == "assign":
            result = issues.update(args.issue, kind="assigned", actor=args.actor, text=args.text,
                                   sources=_sources(workspace, args.source, args.sources),
                                   owner=args.owner)
            _dump({"ok": True, "event": result})
            return 0
        if args.action == "fix":
            if args.exitcode is not None and not tail:
                raise ValueError("给了 --exitcode 就必须给修复命令 argv（`--` 之后）")
            result = issues.update(args.issue, kind="fixed", actor=args.actor, text=args.text,
                                   sources=_sources(workspace, args.source, args.sources),
                                   command=tail if args.exitcode is not None else None,
                                   exitcode=args.exitcode)
            _dump({"ok": True, "event": result})
            return 0
        if args.action == "verify":
            if not tail:
                raise ValueError("复验必须给可核 argv（`--` 之后），⛔ 不接受只写一个 passed")
            passed = None if args.passed is None else (args.passed == "true")
            result = issues.update(args.issue, kind="verified", actor=args.actor, text=args.text,
                                   sources=_sources(workspace, args.source, args.sources),
                                   command=tail, exitcode=args.exitcode, passed=passed)
            _dump({"ok": True, "event": result})
            return 0
        if args.action == "reopen":
            result = issues.update(args.issue, kind="reopened", actor=args.actor, text=args.text,
                                   sources=_sources(workspace, args.source, args.sources))
            _dump({"ok": True, "event": result})
            return 0
        if args.action == "archive":
            result = issues.archive(args.issue, actor=args.actor)
            _dump({"ok": True, "event": result, "archive_index": issues.archive_index()})
            return 0
        raise ValueError(f"不支持的命令：{args.action}")
    except IssueRuleError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 9
    except (OSError, json.JSONDecodeError) as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    except ValueError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
