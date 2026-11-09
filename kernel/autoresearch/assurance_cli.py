# GENERATED-BY: 统一只读检查视图 CLI，2026-11-09
# LAST-EDIT: 2026-11-09 首版：checklist 子命令（JSON / 人读两路），退出码 0/2/3
# PURPOSE: 让「这一轮绿不绿」从命令行一眼可查：每项带 name/status/exp_id/source/evidence/required。
# ORIGIN: kernel/autoresearch/assurance.py；⛔ 不含具体事故编号、机器、读数或个人通道。
# CUMULATIVE-DELTA: 首版。只读：不建库、不建目录、不迁移 schema、不签发授权。

"""统一只读检查清单的命令行（E2/E4）。

用法（参数以 `--help` 为准）：

    python -m kernel.autoresearch.assurance_cli --workspace W checklist \\
        [--topic T] [--exp exp-01] [--owner-session S] [--db P] [--now TS] [--required|--informational]

    python -m kernel.autoresearch.assurance_cli --workspace W checklist --human

判词
====

    ok = 没有任何 required 的项目是 blocked 或 unknown
    unknown 永不计绿：读不到库 / 缺 schema / 缺宿主证据 / 缺负责人会话都写 unknown
    applicable=false 只是「这条检查不成立」，⛔ 不是「已通过」

退出码
======

    0  全部 required 项都是 ok
    3  有 required 项 blocked（有反向证据）
    2  有 required 项 unknown（读不到 / 不可判），或输入错误（工作区不存在、参数非法）
    9  规则拒绝（services 键无法识别等调用方错误）

⛔ 本工具一个字节都不写：不建库、不建目录、不迁移 schema、不发消息、不签发授权。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch.assurance import AssuranceError, checklist, render_human  # noqa: E402


def _dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m kernel.autoresearch.assurance_cli",
        description="统一只读检查清单：把 workflow / monitoring / decisions / issues / "
                    "questions / reporting 的只读投影收成一张清单。⛔ 不写任何文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=argparse.SUPPRESS, help="工作区根目录")
    common.add_argument("--topic", default=argparse.SUPPRESS, help="专题号（报告页与整轮交付按它查）")
    common.add_argument("--exp", default=argparse.SUPPRESS, help="实验号（如 exp-01）")
    common.add_argument("--owner-session", default=argparse.SUPPRESS, help="负责人会话（意图监控按它记账）")
    common.add_argument("--db", default=argparse.SUPPRESS, help="状态库；默认 <workspace>/.autoresearch/runtime.sqlite3")
    common.add_argument("--now", type=float, default=argparse.SUPPRESS, help="判超期用的时间戳（复现用）")
    parser.add_argument("--workspace", default=None, help="工作区根目录")
    parser.add_argument("--topic", default=None)
    parser.add_argument("--exp", default=None)
    parser.add_argument("--owner-session", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--now", type=float, default=None)
    sub = parser.add_subparsers(dest="action", required=True)
    node = sub.add_parser("checklist", parents=[common],
                          help="只读：打印统一检查清单（默认 JSON）")
    mode = node.add_mutually_exclusive_group()
    mode.add_argument("--required", dest="required", action="store_true", default=True,
                      help="本实例用于判绿（默认）：unknown 也算不绿")
    mode.add_argument("--informational", dest="required", action="store_false",
                      help="只列 blocked 判词；unknown 不进判词（⛔ 仍然不会显示成绿）")
    node.add_argument("--human", action="store_true", help="打印一行一项的人读摘要（额外输出）")
    return parser


def main(argv=None):
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.workspace:
        _dump({"ok": False, "error": "UsageError", "reason": "必须给 --workspace"})
        return 2
    try:
        result = checklist(args.workspace, topic=args.topic, exp_id=args.exp,
                           owner_session=args.owner_session, db=args.db, now=args.now,
                           required=getattr(args, "required", True))
    except AssuranceError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    except (OSError, ValueError) as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    _dump(result)
    if getattr(args, "human", False):
        print(render_human(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
