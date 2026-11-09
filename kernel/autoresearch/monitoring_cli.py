# GENERATED-BY: 完整功能迁移（意图与过程监控 CLI）
# LAST-EDIT: 2026-11-09（close 接通自然语言路径：--user-quote / --user-quote-file，与旧 proof 命令路径互斥；
#            原话原样交给 Monitor.close，独立报告约束不跳过）
# PURPOSE: 意图与过程监控的命令行入口。无 host adapter 时只做账本级诊断，会话层一律「不可判」，不返回假绿。
# ORIGIN: 上游单文件工具的 CLI 子命令集；契约 kernel/contracts/intent_monitor.md；
#         行为预期 L10 / L13（安全动作豁免、未登记拒绝、无 host 不可判）
# CUMULATIVE-DELTA: 首版。写命令默认落盘；check/status/diagnose/protocol 只读。
#         2026-11-09 补：`close` 的 withdrawn/replaced 除原有 `--proof/--proof-seq`（用户自己发
#         `close_intent` JSON 命令）外，可用 `--user-quote`（内联 JSON 或 @文件）/ `--user-quote-file`
#         给真实用户原话；两路互斥，原话原样交给 `Monitor.close(quote=…)`，仍必须有仍有效独立报告
#         在 `closure.user_command` 里绑定同一条原话。
"""意图与过程监控 CLI。

用法（`--help` 为准）：

    python -m kernel.autoresearch.monitoring_cli --root <workspace> \
        --adapter kernel/autoresearch/host_evidence.py --log-root <sessions> --session <id> <命令>

    # 用户自然语言撤回 / 替换（仍须有仍有效的独立报告绑定同一条原话）：
    ... close --reason withdrawn --to closed --report-id MON-… \
        --user-quote '{"seq": 6, "text": "逐字原话"}'      # 也可 --user-quote-file 或 --user-quote @文件
    # 用户自己发的 JSON 命令（旧路径，与上面互斥）：
    ... close --reason withdrawn --to closed --report-id MON-… --proof-seq 7

读命令（`status` / `diagnose` / `protocol` / `enabled` / `check`）**零写**：不建目录、不改事件库。
**默认关闭**：缺 `.autoresearch/intent_monitor_policy.json` 的 `{"enabled": true}` 时 `check` 直接放行
（`state=disabled`），写命令（`register` / `sync` / `intent` / `assess` / `prepare` / `record` /
`respond` / `waive` / `close`）**拒写**（exit 2，`event` 是 no-op）；`status` / `diagnose` 仍只读，
`enabled` 专看开关。只有用户明确要求才写这个开关文件。
没有 `--adapter` 时会话层一律判「不可判」（exit 2），绝不因为「读不到就说通过」。

退出码：0=通过；9=被拒（有未处理项）；2=错误或不可判。
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path

from .monitoring import (
    CLOSURE_REASONS,
    ENFORCED_ACTIONS,
    HOST_PROTOCOL,
    POLICY_NAME,
    SAFE_ACTIONS,
    STATE_DIR,
    STATES,
    TRIGGERS,
    Monitor,
    MonitoringError,
    Undecidable,
    _parse_ref,
    _safe_name,
    _same_session,
    enabled as monitoring_enabled,
)

DEFAULT_ROOT = Path.cwd()


def _dump(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _load_host(spec, log_root, root):
    """按 `--adapter` 构造宿主对象。

    * `<file.py>`：模块里必须有 `build_host(log_root)`；
    * `<module:attr>`：attr 是工厂（如 `kernel.autoresearch.host_evidence:DSHHost`），
      按签名填 `workspace` / `root`（或 `log_root`）。

    ⛔ 没有 adapter 就返回 None；调用方据此把会话层判为不可判，而不是放行。
    """
    if not spec:
        if log_root:
            raise MonitoringError("--log-root 需要同时给 --adapter：没有适配器就没有可核的日志来源")
        return None
    if spec.endswith(".py") or "/" in spec or "\\" in spec:
        path = Path(spec)
        if not path.is_file():
            raise MonitoringError("adapter 文件不存在：" + str(path))
        module_spec = importlib.util.spec_from_file_location("autoresearch_monitor_adapter", path)
        if module_spec is None or module_spec.loader is None:
            raise MonitoringError("adapter 文件无法作为 Python 模块加载：" + str(path))
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        factory = getattr(module, "build_host", None)
        if factory is None:
            raise MonitoringError("adapter 文件必须定义 build_host(log_root)")
    elif ":" in spec:
        module_name, _, attr = spec.rpartition(":")
        module = importlib.import_module(module_name)
        factory = getattr(module, attr, None)
        if factory is None:
            raise MonitoringError(f"adapter 模块里没有 {attr}：{module_name}")
    else:
        module = importlib.import_module(spec)
        factory = getattr(module, "build_host", None)
        if factory is None:
            raise MonitoringError("adapter 模块必须定义 build_host(log_root)")
    if not callable(factory):
        raise MonitoringError("adapter 工厂不可调用")
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        params = None
    host = None
    if params is not None:
        kwargs = {}
        if "workspace" in params:
            kwargs["workspace"] = str(root)
        if log_root is not None:
            for name in ("log_root", "root", "sessions_root"):
                if name in params:
                    kwargs[name] = str(log_root)
                    break
        try:
            host = factory(**kwargs)
        except TypeError:
            host = None
    if host is None:
        host = factory(str(log_root)) if log_root is not None else factory()
    if not callable(getattr(host, "session", None)) or not callable(getattr(host, "verify", None)):
        raise MonitoringError("adapter 返回的对象必须实现 session(session_id) 与 verify(kind, payload)")
    return host


def _resolve_owner(args, *, allow_foreign: bool) -> str:
    explicit = args.session
    env = os.environ.get("DSH_SESSION_ID")
    if explicit is not None and str(explicit).strip():
        owner = _safe_name(str(explicit).strip(), "session")
    elif env and str(env).strip():
        owner = _safe_name(str(env).strip(), "session")
    else:
        raise Undecidable(
            "无法确定负责人会话：显式传 --session，或设置环境变量 DSH_SESSION_ID")
    if (not allow_foreign) and explicit is not None and str(explicit).strip() and env and str(env).strip():
        if not _same_session(explicit, env):
            raise MonitoringError(
                "显式负责人会话与环境 DSH_SESSION_ID 不一致：写命令与 launch 闸拒绝用别的会话顶替"
                "（只读诊断可加 --allow-foreign）")
    return owner


def _refs(values):
    return [_parse_ref(item) for item in (values or [])]


def _read_json(path, label):
    if not path:
        raise MonitoringError(f"缺 {label} 文件")
    target = Path(path)
    if not target.is_file():
        raise MonitoringError(f"{label} 文件不存在：{path}")
    try:
        return json.loads(target.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise MonitoringError(f"{label} 不是合法 JSON：{exc}") from exc


def _user_quote(args, owner):
    """解析 close 的自然语言原话：`--user-quote`（内联 JSON / `@文件`）或 `--user-quote-file`。

    只做「读出来 + 补默认 session」，其余形状与逐字核验仍由 `Monitor.close` 做：
    原话必须出现在负责人会话里、文本逐字、seq 不得超过报告高水位，且报告
    `closure.user_command` 要绑定同一条原话。CLI 不提供任何绕过。
    """
    if args.user_quote_file is not None:
        quote = _read_json(args.user_quote_file, "user-quote")
    else:
        raw = str(args.user_quote).strip()
        if raw.startswith("@"):
            quote = _read_json(raw[1:], "user-quote")
        else:
            try:
                quote = json.loads(raw)
            except ValueError as exc:
                raise MonitoringError(
                    f"--user-quote 既不是合法 JSON（{exc}），也不是 @文件；写法示例："
                    "--user-quote '{\"seq\": 6, \"text\": \"逐字原话\"}'") from exc
    if not isinstance(quote, dict):
        raise MonitoringError("用户原话必须是 JSON 对象 {session, seq, text}")
    quote = dict(quote)
    quote.setdefault("session", owner)
    return quote


def _monitor(args, *, host_required: bool):
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT.resolve()
    host = _load_host(args.adapter, args.log_root, root)
    if host is None and host_required:
        raise Undecidable(
            "没有 --adapter：读不到负责人会话证据 ⇒ 本次操作不可判（不能当成通过）。"
            "配置真实 adapter（如 --adapter kernel/autoresearch/host_evidence.py）后再试")
    return Monitor(root, db_path=args.db, host=host)


def _common():
    # 默认用 SUPPRESS：否则 XML 式 argparse 里「写在子命令之前」的全局选项会被子解析器的
    # 默认值覆盖（子解析器用新命名空间解析再合并）。
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", default=argparse.SUPPRESS, help="工作区根（默认当前目录）")
    parser.add_argument("--db", default=argparse.SUPPRESS,
                        help="事件库路径（默认 <root>/.autoresearch/monitoring.sqlite3）")
    parser.add_argument("--adapter", default=argparse.SUPPRESS,
                        help="宿主适配器：<file.py>（须定义 build_host）或 <module:attr>；不配则不可判")
    parser.add_argument("--log-root", dest="log_root", default=argparse.SUPPRESS,
                        help="持久会话日志根，传给 adapter（真实证据来源，不是占位）")
    parser.add_argument("--session", "--owner", dest="session", default=argparse.SUPPRESS,
                        help="负责人会话号（默认环境变量 DSH_SESSION_ID）")
    parser.add_argument("--allow-foreign", dest="allow_foreign", action="store_true",
                        default=argparse.SUPPRESS,
                        help="只读诊断允许查看别的会话（写命令与 launch 闸拒绝）")
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="monitoring",
        description="意图与过程监控：四态 · 三触发 · 两个水位 · 固定截止快照 · 独立子会话报告核验 · "
                    "意见处置与跨报告阻断 · 四理由闭合 · 启动前只读检查。不调用模型、不自动派工。",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text, **kwargs):
        return sub.add_parser(name, help=help_text, parents=[common], **kwargs)

    p = add("protocol", "打印宿主注入协议（父 agent 按它实现 DSH adapter）")

    p = add("enabled", "只读查看开关状态（默认关闭：缺开关文件即 off；不改任何文件）")

    p = add("register", "登记负责人会话的监控状态（一次性；none 是一次显式评估，不是默认值）")
    p.add_argument("--state", choices=["none", "active"], default="none")
    p.add_argument("--intent", default="", help="专项意图正文（state=active 必填）")
    p.add_argument("--source", action="append", default=[], help="依据：路径::定位片段 或 会话号:seq（可重复）")
    p.add_argument("--reason", required=True, help="登记依据（必填：为何登记 / 为何无专项意图）")
    p.add_argument("--note", default="")
    p.add_argument("--exp", default="")
    p.add_argument("--intent-id", dest="intent_id", default="", help="意图标识（可选；替换时用它作新 id）")

    p = add("sync", "从真实会话按高水位登记新用户原话（只抬同步水位，不抬评估水位）")
    p.add_argument("--exp", default="")

    p = add("intent", "登记 / 替换专项意图（必须指到一句真实用户原话；替换要新原话）")
    p.add_argument("--text", required=True, help="意图正文")
    p.add_argument("--source", action="append", default=[], help="来源：会话号:seq（至少一条真实用户原话）")
    p.add_argument("--quote-seq", dest="quote_seq", type=int, help="逐字引文所在 seq（可选）")
    p.add_argument("--quote-text", dest="quote_text", default="", help="逐字引文（可选，须与原文一致）")
    p.add_argument("--replace", action="store_true", help="替换现有 active 意图（必须带用户新原话）")
    p.add_argument("--note", default="")
    p.add_argument("--exp", default="")
    p.add_argument("--intent-id", dest="intent_id", default="", help="意图标识（可选；replace 时用它）")

    p = add("assess", "对用户原话的一次显式判断（decision=none；立意图请用 intent）")
    p.add_argument("--decision", choices=["none", "intent"], default="none",
                   help="none=不立专项意图；intent=转给 intent 命令（需 --intent-text）")
    p.add_argument("--covers-through-seq", dest="covers_through_seq", type=int,
                   help="评估覆盖到哪条用户消息 seq")
    p.add_argument("--answer-seq", dest="answer_seq", type=int,
                   help="用户回答「要不要立意图」的那条消息 seq（必须晚于被评估原话）")
    p.add_argument("--rationale", default="", help="评估理由（必填）")
    p.add_argument("--intent-text", dest="intent_text", default="", help="decision=intent 时的意图正文")
    p.add_argument("--source", action="append", default=[], help="依据：路径::定位 或 会话号:seq")
    p.add_argument("--note", default="")
    p.add_argument("--exp", default="")

    p = add("event", "登记事件：三触发 / block / unblock。心跳不是触发（会被拒）")
    p.add_argument("--kind", required=True,
                   choices=list(TRIGGERS) + ["block", "unblock", "heartbeat"])
    p.add_argument("--exp", default="")
    p.add_argument("--stage", choices=["pending", "evidence"],
                   help="result_analysis 阶段：pending（finish 只标待分析）/ evidence（review 形成实际待审）")
    p.add_argument("--note", default="")
    p.add_argument("--text", default="")
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--seq", type=int, help="手工 user_update：真实用户消息 seq")
    p.add_argument("--issue-id", dest="issue_ids", action="append", default=[])

    p = add("prepare", "固定截止快照 + 派工材料包（只在确有要审的东西时可用；不派工、不调模型）")
    p.add_argument("--exp", default="")
    p.add_argument("--source", action="append", default=[], help="附加材料（结果版本）：路径::定位")
    p.add_argument("--direction", action="append", default=[],
                   help="方向源（必填，至少一条）：路径::定位")
    p.add_argument("--note", default="")

    p = add("record", "登记独立子会话报告（宿主证据核验；fork / 自造 JSON 一律拒）")
    p.add_argument("--report", required=True, help="报告 JSON 文件（字节会被绑定）")
    p.add_argument("--proof", help="proof JSON 文件：{\"session_id\": \"...\", \"seq\": N}")
    p.add_argument("--proof-session", dest="proof_session", default=None)
    p.add_argument("--proof-seq", dest="proof_seq", type=int, default=None)

    p = add("respond", "逐条处置监控意见（须理由与来源；declined 不消除阻断）")
    p.add_argument("--responses", required=True, help="responses JSON 文件")
    p.add_argument("--actor", default=None, help="处置者会话号（只接受负责人）")

    p = add("waive", "用户可信豁免（只对 blocking）：须 prove 一条真实用户命令消息，"
                     "正文含 {action:waive, report_id, issue_id} 且逐键相等")
    p.add_argument("--report-id", dest="report_id", required=True)
    p.add_argument("--issue-id", dest="issue_id", required=True)
    p.add_argument("--proof", help="proof JSON 文件：{\"session_id\": \"...\", \"seq\": N}")
    p.add_argument("--proof-seq", dest="proof_seq", type=int, default=None,
                   help="用户命令消息的 seq（会话默认就是负责人会话）")
    p.add_argument("--quote-text", dest="quote_text", default="",
                   help="可选：命令消息里的逐字片段（会与 proof.seq 那条消息比对）")
    p.add_argument("--reason", required=True)
    p.add_argument("--source", action="append", default=[])

    p = add("close", "闭合意图：四理由 + 最后一份仍有效报告 + 全部意见已处置。"
                     "withdrawn/replaced 两路互斥：--user-quote（真实用户原话，须由仍有效独立报告 "
                     "closure.user_command 绑定同一条原话）或 --proof/--proof-seq"
                     "（用户自己发的 close_intent JSON 命令）")
    p.add_argument("--reason", required=True, choices=list(CLOSURE_REASONS))
    p.add_argument("--to", required=True, choices=["closed", "none"])
    p.add_argument("--report-id", dest="report_id", required=True)
    proof_group = p.add_mutually_exclusive_group()
    proof_group.add_argument("--proof",
                             help="旧路径：命令消息 proof JSON 文件 {\"session_id\": \"...\", \"seq\": N}"
                                  "（与 --user-quote / --user-quote-file 互斥）")
    proof_group.add_argument("--proof-seq", dest="proof_seq", type=int, default=None,
                             help="旧路径：用户命令消息的 seq（会话默认就是负责人会话；与 --user-quote 互斥）")
    proof_group.add_argument("--user-quote", dest="user_quote", default=None,
                             help="自然语言路径：真实用户原话 JSON 对象（内联）或 @文件；"
                                  "形如 {\"seq\": 6, \"text\": \"逐字原话\"}（session 默认负责人会话）。"
                                  "与 --proof/--proof-seq 互斥；⛔ 主会话自己传的话不算："
                                  "必须有仍有效独立报告把同一条原话绑定成本次动作")
    proof_group.add_argument("--user-quote-file", dest="user_quote_file", default=None,
                             help="自然语言路径：原话 JSON 文件（格式同 --user-quote）")
    p.add_argument("--quote-text", dest="quote_text", default="",
                   help="仅旧路径：命令消息里的逐字片段（会与 proof.seq 那条消息比对）")
    p.add_argument("--replacement-intent", dest="replacement_intent", default=None)
    p.add_argument("--source", action="append", default=[])

    p = add("check", "启动前检查（零写）：安全动作 collect/stop… 豁免；launch 缺监控一律不放行")
    p.add_argument("--exp", required=True)
    p.add_argument("--action", default="launch", choices=list(SAFE_ACTIONS) + list(ENFORCED_ACTIONS),
                   help="安全动作（" + "/".join(SAFE_ACTIONS) + "）豁免；launch 走闸。"
                        "⛔ 换 action 就是换语义，不能用 collect 绕过 launch")

    p = add("status", "零写查看：状态、两个水位、待审、意见、过期报告、hook 缺口")
    p.add_argument("--exp", default="")

    p = add("diagnose", "零写诊断：事件账完整性 + 会话层用户消息（没有 adapter 时会话层不可判）")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for name in ("root", "db", "adapter", "log_root", "session"):
        if not hasattr(args, name):
            setattr(args, name, None)
    if not hasattr(args, "allow_foreign"):
        args.allow_foreign = False
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT.resolve()
    try:
        if args.command == "protocol":
            _dump(HOST_PROTOCOL)
            return 0
        if args.command == "enabled":
            # 只读开关：不建目录、不写盘、不需要会话 / adapter。
            on = monitoring_enabled(root)
            _dump({"ok": True, "enabled": on,
                   "state": "enabled" if on else "disabled",
                   "policy": f"{STATE_DIR}/{POLICY_NAME}",
                   "note": ("只有顶层 JSON 布尔 {\"enabled\": true} 才算启用；"
                            "缺文件 / 坏 JSON / 非布尔一律按关闭处理。"
                            "默认关闭时不要求登记 / 评估 / 派工，也不拦起跑。")})
            return 0
        if args.command == "check":
            host = _load_host(args.adapter, args.log_root, root)
            # ⛔ launch 闸不接受「拿别的会话顶替」：--allow-foreign 只对安全动作生效
            allow_foreign = bool(args.allow_foreign) and args.action in SAFE_ACTIONS
            owner = _resolve_owner(args, allow_foreign=allow_foreign)
            monitor = Monitor(root, db_path=args.db, host=host)
            out = monitor.check_start(owner, args.exp, action=args.action)
            _dump(out)
            if out.get("undecidable"):
                return 2
            return 0 if out.get("allow") else 9
        if args.command == "status":
            host = _load_host(args.adapter, args.log_root, root)
            owner = _resolve_owner(args, allow_foreign=True)
            monitor = Monitor(root, db_path=args.db, host=host)
            out = monitor.status(owner, exp=args.exp)
            _dump(out)
            if out.get("undecidable"):
                return 2
            return 0 if out.get("ok") else 9
        if args.command == "diagnose":
            host = _load_host(args.adapter, args.log_root, root)
            owner = _resolve_owner(args, allow_foreign=True)
            monitor = Monitor(root, db_path=args.db, host=host)
            out = monitor.diagnose(owner)
            _dump(out)
            return 0 if out.get("ok") else 2

        # 以下都是写命令：必须能确定负责人会话，并且真的写账（不接受无声 no-op）
        owner = _resolve_owner(args, allow_foreign=False)
        host = _load_host(args.adapter, args.log_root, root)
        monitor = Monitor(root, db_path=args.db, host=host)

        if args.command == "register":
            out = monitor.register(
                owner, state=args.state, intent=args.intent, sources=_refs(args.source),
                reason=args.reason, note=args.note, exp=args.exp, intent_id=args.intent_id)
        elif args.command == "sync":
            out = monitor.sync(owner, exp=args.exp)
        elif args.command == "intent":
            quote = None
            if args.quote_seq is not None:
                quote = {"seq": args.quote_seq, "text": args.quote_text}
            out = monitor.intent(owner, text=args.text, sources=_refs(args.source), quote=quote,
                                 replace=args.replace, exp=args.exp, note=args.note,
                                 intent_id=args.intent_id)
        elif args.command == "assess":
            if args.decision == "intent":
                if not args.intent_text:
                    raise MonitoringError("decision=intent 需要 --intent-text；也可以直接用 intent 命令")
                out = monitor.intent(owner, text=args.intent_text, sources=_refs(args.source),
                                     quote=None, exp=args.exp, note=args.note)
            else:
                if args.covers_through_seq is None:
                    raise MonitoringError("assess 必须写 --covers-through-seq")
                out = monitor.assess(
                    owner, decision="none", covers_through_seq=args.covers_through_seq,
                    answer_seq=args.answer_seq, rationale=args.rationale,
                    sources=_refs(args.source), exp=args.exp, note=args.note)
        elif args.command == "event":
            out = monitor.event(owner, args.kind, exp=args.exp, stage=args.stage, note=args.note,
                                sources=_refs(args.source), seq=args.seq,
                                issue_ids=args.issue_ids, text=args.text)
        elif args.command == "prepare":
            out = monitor.prepare(owner, exp=args.exp, sources=_refs(args.source),
                                  direction=_refs(args.direction), note=args.note)
        elif args.command == "record":
            proof = None
            if args.proof:
                if args.proof_session is not None or args.proof_seq is not None:
                    raise MonitoringError("--proof 与 --proof-session/--proof-seq 只能用一种")
                proof = _read_json(args.proof, "proof")
            elif args.proof_session is not None or args.proof_seq is not None:
                if args.proof_session is None or args.proof_seq is None:
                    raise MonitoringError("--proof-session 与 --proof-seq 必须一起给")
                proof = {"session_id": args.proof_session, "seq": args.proof_seq}
            else:
                raise MonitoringError("record 需要 --proof 或 --proof-session/--proof-seq")
            out = monitor.record(owner, report_path=args.report, proof=proof)
        elif args.command == "respond":
            out = monitor.respond(owner, _read_json(args.responses, "responses"), actor=args.actor)
        elif args.command == "waive":
            proof = None
            if args.proof:
                if args.proof_seq is not None:
                    raise MonitoringError("--proof 与 --proof-seq 只能用一种")
                proof = _read_json(args.proof, "proof")
            elif args.proof_seq is not None:
                proof = {"session_id": owner, "seq": args.proof_seq}
            else:
                raise MonitoringError(
                    "waive 需要 --proof 或 --proof-seq：必须是真实用户命令消息的 seq；"
                    "只给一段引文（哪条消息都可能含有同样字眼）不接受")
            quote = {"seq": proof["seq"], "text": args.quote_text} if args.quote_text else None
            out = monitor.waive(owner, report_id=args.report_id, issue_id=args.issue_id,
                                proof=proof, quote=quote,
                                reason=args.reason, sources=_refs(args.source))
        elif args.command == "close":
            # 旧路径与自然语言路径由 argparse 的互斥组保证不会同时出现。
            proof = None
            quote = None
            natural = args.user_quote is not None or args.user_quote_file is not None
            if args.proof:
                proof = _read_json(args.proof, "proof")
            elif args.proof_seq is not None:
                proof = {"session_id": owner, "seq": args.proof_seq}
            if natural:
                if args.quote_text:
                    raise MonitoringError(
                        "--user-quote 与 --quote-text 不要混用：原话文本写在 --user-quote 的 JSON 里")
                if args.reason in ("achieved", "unsatisfiable"):
                    raise MonitoringError(
                        f"{args.reason} 不接受用户原话：达成 / 不可满足必须由仍有效报告的 verdict 支撑")
                quote = _user_quote(args, owner)
            elif args.quote_text:
                if proof is None:
                    raise MonitoringError(
                        "--quote-text 只用于旧路径：需要同时给 --proof/--proof-seq")
                quote = {"seq": proof["seq"], "text": args.quote_text}
            if args.reason in ("withdrawn", "replaced") and proof is None and quote is None:
                raise MonitoringError(
                    "withdrawn/replaced 需要 --user-quote（真实用户原话，须由仍有效独立报告的 "
                    "closure.user_command 绑定同一条原话）或 --proof/--proof-seq"
                    "（用户自己发的 close_intent JSON 命令）；两条路都不能跳过独立报告")
            out = monitor.close(owner, reason=args.reason, target=args.to,
                                report_id=args.report_id, quote=quote,
                                replacement_intent_id=args.replacement_intent,
                                sources=_refs(args.source), proof=proof)
        else:
            raise MonitoringError("不支持的命令：" + str(args.command))
        _dump(out)
        return 0
    except Undecidable as exc:
        _dump({"ok": False, "undecidable": True, "problems": [str(exc)]})
        return 2
    except (MonitoringError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        _dump({"ok": False, "problems": [str(exc)], "error": type(exc).__name__})
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
