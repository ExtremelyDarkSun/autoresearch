# GENERATED-BY: 完整功能迁移（意图与过程监控测试）
# LAST-EDIT: 2026-11-09（补自然语言撤回 / 替换的正反例：真实原话 + 独立报告 closure.user_command 绑定；
#            原有 JSON 用户命令路径的用例原样保留）
# PURPOSE: 用注入的假宿主对象明确模拟证据，验证迁移后的监控机制；不连真实宿主、不调用模型、不跑实验。
# ORIGIN: 契约 kernel/contracts/intent_monitor.md 与行为预期 L1–L14 / V1–V3；上游测试场景的中性化重写
# CUMULATIVE-DELTA: 首版。全部合成数据：会话 owner-alpha / monitor-bravo、实验 E1、题 t1/t2/r1。
#         2026-11-09 补：withdrawn/replaced 的自然语言路径（quote + 报告绑定）正例与反例
#         （无绑定 / 非用户原话 / 转述 / 不同一条 / 旧话 / 错意图 / 错理由 / 缺 closure.sources /
#         绑定形状非法 / 超出截止 / 报告过期 / 未处置阻断 / 非最后一份报告），并确认 JSON 路径仍可用。
"""意图与过程监控测试。

三条约束（与仓库其它测试一致）：

1. **只用标准库**（unittest / json / sqlite3 / tempfile / pathlib）。
2. **全部合成数据**：会话号、实验号、题号都是编的，不引用任何真实实验与读数。
3. **证据显式模拟**：`FakeHost` 是注入的宿主对象，按父 agent 的 DSHHost 形状返回
   `{session_id, header, descriptor, events}[]`；`verify("monitor_independence", …)` 返回 verified。
   ⚠️ 这些测试证明**本模块的判据**，不证明真实宿主日志、真实子会话或真实用户消息。

跑法：

    python -X utf8 -B -m unittest discover -s tests -p 'test_monitoring.py' -v
    python -X utf8 -B tests/test_monitoring.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import monitoring_cli  # noqa: E402
from kernel.autoresearch.host_evidence import DSHHost  # noqa: E402
from kernel.autoresearch.monitoring import (  # noqa: E402
    HOST_PROTOCOL,
    Monitor,
    Monitoring,
    MonitoringError,
    Undecidable,
)

OWNER = "owner-alpha"
MONITOR = "monitor-bravo"
EXP = "E1"
USER_TEXT = "请守住已经通过的回归题，别把翻正的又弄丢"
NEW_WORDS = "继续按计划推进，不用额外授权"
ANSWER_TEXT = "不用单独盯，按计划继续推进"
WITHDRAW_TEXT = "这个方向先停掉，回归题不用再盯了"
REPLACE_TEXT = "别盯回归题了，改成盯逐题读数"


# ---------------------------------------------------------------------------
# 假宿主（形状对齐 host_evidence.DSHHost）
# ---------------------------------------------------------------------------


def _canonical(identity: str) -> str:
    return identity if identity.startswith("session-") else "session-" + identity


def _visible(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content
                       if isinstance(block, dict) and block.get("type") in (None, "text"))
    return ""


def _normalize(raw):
    data = raw.get("data") or {}
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    content = message.get("content", data.get("content"))
    text = _visible(content)
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    rpc = source.get("rpcId")
    is_user = (raw.get("type") == "user/message" and source.get("kind") in (None, "user")
               and isinstance(rpc, str) and bool(rpc.strip()) and bool(text.strip()))
    role = ("user" if raw.get("type") == "user/message"
            else "assistant" if raw.get("type") == "assistant/message"
            else message.get("role"))
    return {"seq": raw["seq"], "type": raw["type"], "role": role, "text": text,
            "source": source, "rpc_id": rpc, "is_user": is_user,
            "time": raw.get("time"), "data": data}


def make_session(identity, *, events, origin="top-level", parent=None, depth=0, seeded=False,
                 provider=None, mode="one-shot"):
    sid = _canonical(identity)
    header = {"id": sid, "type": "session", "origin": origin,
              "parentSession": _canonical(parent) if parent else None,
              "delegationDepth": depth, "isSeeded": seeded,
              "createdAt": "2026-01-01T00:00:00Z"}
    descriptor = None
    if origin == "subagent":
        descriptor = {"provider": provider, "mode": mode, "label": "monitor"}
    normalized = [_normalize(event) for event in events]
    return {"session_id": sid, "id": sid, "header": header, "descriptor": descriptor,
            "events": normalized, "raw_events": list(events),
            "max_seq": max((item["seq"] for item in normalized), default=0)}


def _json_objects(text):
    """与 host_evidence._json_objects 同口径：从正文里抠出可解析 JSON 值。"""
    decoder = json.JSONDecoder()
    found, position = [], 0
    while position < len(text):
        if text[position] not in "{[":
            position += 1
            continue
        try:
            value, length = decoder.raw_decode(text[position:])
        except ValueError:
            position += 1
            continue
        found.append(value)
        position += length
    return found


class FakeHost:
    """注入的宿主：只按协议返回会话与核验结果，不写任何文件。

    `verify` 按 host_evidence.DSHHost 的判据模拟两种 kind：
    `monitor_independence`（放行）与 `user_command`（顶层 + 真实用户 + 唯一命令 + 逐键相等）。
    """

    def __init__(self):
        self.sessions = {}
        self.calls = []

    def session(self, identity):
        bare = identity[len("session-"):] if identity.startswith("session-") else identity
        record = self.sessions.get(bare)
        if record is None:
            raise KeyError(f"未知会话：{identity}")
        return record

    def verify(self, kind, payload):
        self.calls.append({"kind": kind, "payload": payload})
        if kind == "user_command":
            proof = payload.get("proof") if isinstance(payload, dict) else None
            if not isinstance(proof, dict) or set(proof) != {"session_id", "seq"} \
                    or type(proof["seq"]) is not int or proof["seq"] < 0:
                raise ValueError("proof 只能是 {session_id, seq}")
            record = self.session(proof["session_id"])
            header = record["header"]
            if header.get("parentSession") or header.get("delegationDepth") not in (0, None) \
                    or header.get("isSeeded") is True:
                raise ValueError("需要顶层会话的真实用户命令")
            event = next((item for item in record["events"] if item["seq"] == proof["seq"]), None)
            if event is None or not event.get("is_user"):
                raise ValueError("需要真实用户消息")
            expected = payload.get("expected")
            if not isinstance(expected, dict) or not expected:
                raise ValueError("核验器需要调用者提供完整预期命令")
            commands = [item for item in _json_objects(event.get("text") or "")
                        if isinstance(item, dict) and "action" in item]
            if len(commands) != 1 or commands[0] != expected:
                raise ValueError("真实用户消息里的命令与执行动作不逐键相等")
            return {"verified": True, "kind": kind, "session_id": proof["session_id"],
                    "seq": proof["seq"], "rpc_id": event.get("rpc_id"), "command": expected}
        proof = payload.get("proof") if isinstance(payload, dict) else {}
        return {"verified": True, "kind": kind,
                "session_id": (proof or {}).get("session_id"),
                "seq": (proof or {}).get("seq"),
                "owner_session": (payload or {}).get("owner_session"),
                "prepared_id": (payload or {}).get("prepared_id")}


class RealHostLogs:
    """把合成会话写成**真 `DSHHost`** 能读的日志（decoder 注入，不启动 Node.js）。

    与 `FakeHost` 的分工：`FakeHost` 只证明本模块的判据；这个夹具让
    `host_evidence.DSHHost` 自己去读、自己去核（原话逐字、spawn 独立性），
    用来证明自然语言闭合路径读的是宿主日志，不是夹具自报。
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.rows = {}

    def write(self, sid, rows):
        path = self.root / "sessions" / "project" / sid / "session.v1.jsonl.zstd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic-session-log")
        self.rows[str(path)] = rows

    def host(self):
        return DSHHost(root=self.root, decoder=lambda path: self.rows[str(path)])


# ---------------------------------------------------------------------------
# 会话事件构造
# ---------------------------------------------------------------------------


def user(seq, text, rpc="rpc-1"):
    return {"seq": seq, "type": "user/message", "time": f"2026-01-01T00:00:{seq:02d}Z",
            "data": {"content": text, "source": {"kind": "user", "rpcId": rpc}}}


def injected(seq, text, kind="agent-instructions"):
    return {"seq": seq, "type": "user/message", "time": f"2026-01-01T00:00:{seq:02d}Z",
            "data": {"content": text, "source": {"kind": kind}}}


def assistant(seq, text):
    return {"seq": seq, "type": "assistant/message", "time": f"2026-01-01T00:00:{seq:02d}Z",
            "data": {"message": {"role": "assistant",
                                 "content": [{"type": "reasoning", "text": "隐藏思维"},
                                             {"type": "text", "text": text}]}}}


def tool_call(seq, name="read_file", arguments=None):
    return {"seq": seq, "type": "tool/call", "time": f"2026-01-01T00:00:{seq:02d}Z",
            "data": {"name": name, "arguments": json.dumps(arguments or {"path": "results.json"}),
                     "callId": f"call-{seq}"}}


def tool_result(seq, call_id, text):
    return {"seq": seq, "type": "tool/result", "time": f"2026-01-01T00:00:{seq:02d}Z",
            "data": {"message": {"toolCallId": call_id, "content": text}}}


def base_events():
    return [user(1, USER_TEXT), assistant(2, "收到，继续守回归。"),
            tool_call(3), tool_result(4, "call-3", "t1 通过；t2 通过")]


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


class Harness:
    def __init__(self, root):
        self.root = Path(root)
        self.host = FakeHost()
        self._monitor_seq = 0
        self.write("results.json", "t1 通过\nt2 通过\nr1 通过\n")
        self.write("plan.md", "# 计划\n第 1 步：守住回归\n第 2 步：小样本验证\n")
        self.set_owner(base_events())
        self.mon = Monitor(self.root, host=self.host)

    # -- 文件 / 会话 --------------------------------------------------------

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def ref(self, relative, locator="L1"):
        return {"path": relative, "locator": locator}

    def set_owner(self, events, **kwargs):
        self.host.sessions[OWNER] = make_session(OWNER, events=events, **kwargs)

    def owner(self):
        return self.host.sessions[OWNER]

    def add(self, event):
        record = self.owner()
        record["events"].append(_normalize(event))
        record["raw_events"].append(event)
        record["max_seq"] = max(record["max_seq"], event["seq"])

    # -- 命令 --------------------------------------------------------------

    def register_none(self, reason="用户没有要求跨轮盯住这件事"):
        return self.mon.register(OWNER, state="none", reason=reason,
                                 sources=[self.ref("results.json", "L1")])

    def register_active(self, *, intent="守住已经通过的回归题", seq=1, intent_id="INT-1"):
        return self.mon.register(OWNER, state="active", intent=intent,
                                 sources=[{"session": OWNER, "seq": seq}],
                                 reason="用户原话要求盯住回归", intent_id=intent_id)

    def trigger(self, *, exp=EXP, stage="pending", note="结果已回收，待分析"):
        return self.mon.event(OWNER, "result_analysis", exp=exp, stage=stage, note=note,
                              sources=[self.ref("results.json", "L1")])

    def prepare(self, *, exp=EXP):
        return self.mon.prepare(OWNER, exp=exp, sources=[self.ref("results.json", "L1")],
                                direction=[self.ref("plan.md", "L1")])

    def issue(self, issue_id="M1", blocking=False):
        return {"id": issue_id, "blocking": blocking,
                "problem": "结果文件只留了汇总，没有逐题读数",
                "consequence": "无法判断收益来自哪几道题，下一轮改方向没有依据",
                "suggestion": "补齐逐题 JSONL 并在下一次材料里引用",
                "sources": [self.ref("results.json", "L2")]}

    def report(self, prep, *, quote_seq=1, quote_text=USER_TEXT, issues=(), resolves=(),
               verdict="on_track", closure_target="keep",
               closure_basis="方向仍成立，本轮只提非阻塞意见，因此保持 active",
               monitor_session=MONITOR, evidence_reads=None, steps=None, rounds=None,
               intent_sources=None, direction_text="继续同一假设，先补逐题读数",
               closure_sources=None, closure_binding=None):
        trajectory = {"summary": "先确认基线，再改一处抓手，最后跑小样本验证",
                      "verdict": "on_track", "note": "每一步都能对上意图，没有偏离到别的抓手",
                      "steps": steps or [{"seq": 1, "what": "从用户原话里确认了意图与判据"}]}
        if rounds is not None:
            trajectory["rounds"] = rounds
        closure = {"target": closure_target, "basis": closure_basis,
                   "sources": (closure_sources if closure_sources is not None
                               else [self.ref("plan.md", "L1")])}
        if closure_binding is not None:
            closure["user_command"] = closure_binding
        return {
            "prepared_id": prep["prepared_id"],
            "monitor_session": monitor_session,
            "owner_session": OWNER,
            "exp": prep["exp"],
            "verdict": verdict,
            "covers": json.loads(json.dumps(prep["covers"])),
            "direction_sha256": prep["direction_sha256"],
            "intent": {"stated": "守住已经通过的回归题", "verdict": "consistent",
                       "note": "原话与最近几轮动作一致；没有出现新的目标或转向",
                       "quote": {"seq": quote_seq, "text": quote_text},
                       "sources": intent_sources or [{"session": OWNER, "seq": quote_seq}]},
            "trajectory": trajectory,
            "findings": [{"text": "本轮只跑了小样本，读数不能外推到全量",
                          "sources": [self.ref("results.json", "L1")]}],
            "issues": list(issues),
            "next_direction": {"text": direction_text, "direction_sha256": prep["direction_sha256"],
                               "sources": [self.ref("plan.md", "L1")]},
            "closure": closure,
            "evidence_reads": evidence_reads if evidence_reads is not None else [
                {"kind": "raw_session", "session": OWNER, "seq": 1},
                {"kind": "snapshot", "path": prep["snapshot"]}],
            "resolves": list(resolves),
        }

    def child(self, body, *, monitor=MONITOR, seq=1, provider="spawn", seeded=False,
              parent=OWNER, depth=1):
        self.host.sessions[monitor] = make_session(
            monitor, origin="subagent", parent=parent, depth=depth, provider=provider,
            seeded=seeded, events=[assistant(seq, body)])
        return monitor

    def record(self, report, *, monitor=None, seq=1, text=None, provider="spawn",
               seeded=False, parent=OWNER, depth=1):
        # 每次核验都是一次**新的**独立子会话：复用同一个会话 id 会让上一份报告「正文已变」。
        if monitor is None:
            self._monitor_seq += 1
            monitor = f"monitor-{self._monitor_seq}"
        report = json.loads(json.dumps(report))
        report["monitor_session"] = monitor
        body = json.dumps(report, ensure_ascii=False, indent=2)
        visible = body if text is None else text
        self.child(visible, monitor=monitor, seq=seq, provider=provider, seeded=seeded,
                   parent=parent, depth=depth)
        return self.mon.record(OWNER, report_bytes=body.encode("utf-8"),
                               proof={"session_id": monitor, "seq": seq})

    def respond(self, report_id, issue_id, disposition="accepted", reason="已核对逐题读数"):
        return self.mon.respond(OWNER, [{"report_id": report_id, "issue_id": issue_id,
                                         "disposition": disposition, "reason": reason,
                                         "sources": [self.ref("results.json", "L2")]}])

    def bind(self, *, seq, text, intent_id="INT-1", reason="withdrawn",
             replacement_intent_id=None, target="closed"):
        """报告 closure.user_command 的绑定对象（自然语言闭合路径）。"""
        return {"expected": {"action": "close_intent", "intent_id": intent_id, "reason": reason,
                             "replacement_intent_id": replacement_intent_id, "target": target},
                "user_quote": {"session": OWNER, "seq": seq, "text": text}}

    def bound_close_setup(self, *, quote_seq=6, quote_text=WITHDRAW_TEXT, reason="withdrawn",
                          replacement_intent_id=None, target="closed", intent_id="INT-1",
                          binding="auto", closure_sources="auto", issues=(), extra_users=()):
        """一次「用户原话 + 绑定报告」：立意图 → 登记原话 → 触发 → prepare → record。"""
        self.register_active(seq=1, intent_id=intent_id)
        self.add(user(quote_seq, quote_text))
        for seq, text in extra_users:
            self.add(user(seq, text))
        self.trigger()
        prep = self.prepare()
        bound = (self.bind(seq=quote_seq, text=quote_text, intent_id=intent_id, reason=reason,
                           replacement_intent_id=replacement_intent_id, target=target)
                 if binding == "auto" else binding)
        sources = ([{"session": OWNER, "seq": quote_seq}] if closure_sources == "auto"
                   else closure_sources)
        recorded = self.record(self.report(prep, issues=issues, closure_target=target,
                                           closure_binding=bound, closure_sources=sources))
        return recorded, prep

    def active_with_report(self, *, issues=(), verdict="on_track", closure_target="keep",
                           reports=1, extra_events=None):
        self.register_active()
        if extra_events:
            for event in extra_events:
                self.add(event)
        self.trigger()
        prep = self.prepare()
        recorded = self.record(self.report(prep, issues=issues, verdict=verdict,
                                           closure_target=closure_target))
        for _ in range(reports - 1):
            prep = self.prepare()
            recorded = self.record(self.report(prep, issues=issues, verdict=verdict,
                                               closure_target=closure_target))
        return recorded, prep


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.h = Harness(self.root)

    def _fresh(self):
        """换一个干净的工作区与假宿主（同一个测试内做多条互不干扰的场景）。"""
        self._directory.cleanup()
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.h = Harness(self.root)
        return self.h

    # -- L1 / L7 两个水位 ---------------------------------------------------

    def test_register_active_requires_real_user_source(self):
        with self.assertRaises(MonitoringError):
            self.h.mon.register(OWNER, state="active", intent="盯住回归",
                                reason="没有指到用户原话")
        # 注入消息（agent-instructions）带 kind，不是真实用户原话
        self.h.set_owner([injected(1, "系统注入的说明"), assistant(2, "ok")])
        with self.assertRaises(MonitoringError):
            self.h.mon.register(OWNER, state="active", intent="盯住回归",
                                sources=[{"session": OWNER, "seq": 1}], reason="拿注入当原话")
        # 真实用户原话可以登记
        self.h.set_owner(base_events())
        out = self.h.register_active()
        self.assertEqual(out["state"], "active")
        self.assertEqual(out["baseline_seq"], 1)

    def test_sync_only_moves_sync_watermark(self):
        self.h.register_none()
        self.h.add(user(5, NEW_WORDS))
        self.h.add(user(6, ANSWER_TEXT))
        sync = self.h.mon.sync(OWNER)
        self.assertEqual(sync["through_seq"], 6)
        state = self.h.mon.status(OWNER)
        self.assertEqual(state["sync_watermark"], 6)
        self.assertEqual(state["assessed_watermark"], 1)
        self.assertEqual([item["seq"] for item in state["unassessed_user_updates"]], [5, 6])
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        self.assertTrue(any("未评估的用户原话" in item for item in blocked["problems"]))
        # 判 none 必须指用户回答那句
        with self.assertRaises(MonitoringError):
            self.h.mon.assess(OWNER, decision="none", covers_through_seq=5,
                              rationale="主 agent 自己认为不用盯",
                              sources=[self.h.ref("results.json", "L1")])
        self.h.mon.assess(OWNER, decision="none", covers_through_seq=5, answer_seq=6,
                          rationale="用户回答不用单独盯",
                          sources=[{"session": OWNER, "seq": 6}])
        state = self.h.mon.status(OWNER)
        self.assertEqual(state["assessed_watermark"], 5)
        self.assertEqual(state["unassessed_user_updates"], [])
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])

    def test_assessment_must_follow_registered_words(self):
        self.h.register_none()  # 登记时只有 seq=1 这一句
        self.h.add(user(5, NEW_WORDS))
        self.h.add(user(6, ANSWER_TEXT))
        # 先登记「用户回答那句」，再做评估
        self.h.mon.event(OWNER, "user_update", exp=EXP, seq=6, note="用户回答先入账",
                         sources=[self.h.ref("results.json", "L1")])
        self.h.mon.assess(OWNER, decision="none", covers_through_seq=5, answer_seq=6,
                          rationale="先评估，再登记被评估那句",
                          sources=[{"session": OWNER, "seq": 6}])
        # 模拟登记晚到的 hook：被评估的那句在评估之后才进账 ⇒ 评估不算覆盖它
        self.h.mon.event(OWNER, "user_update", exp=EXP, seq=5, note="补登记",
                         sources=[self.h.ref("results.json", "L1")])
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        self.assertEqual([item["seq"] for item in blocked["unassessed_user_updates"]], [5])
        self.h.mon.assess(OWNER, decision="none", covers_through_seq=5, answer_seq=6,
                          rationale="登记之后再评估",
                          sources=[{"session": OWNER, "seq": 6}])
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])

    def test_none_does_not_block_pending_triggers(self):
        self.h.register_none()
        self.h.trigger(exp=EXP)
        self.h.mon.event(OWNER, "material_deviation", exp=EXP, note="远端读数与本地不一致",
                         sources=[self.h.ref("results.json", "L1")])
        state = self.h.mon.status(OWNER)
        self.assertEqual(len(state["pending"]), 2)
        out = self.h.mon.check_start(OWNER, EXP)
        self.assertTrue(out["allow"], out["problems"])
        self.assertEqual(out["state"], "none")

    # -- L5 / L6 触发与报告 -------------------------------------------------

    def test_active_needs_current_report_for_pending(self):
        self.h.register_active()
        self.h.trigger()
        before = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(before["allow"])
        self.assertTrue(any("没有仍有效的独立监控报告" in item for item in before["problems"]))
        prep = self.h.prepare()
        still = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(still["allow"])
        recorded = self.h.record(self.h.report(prep))
        self.assertTrue(recorded["host_verified"])
        after = self.h.mon.check_start(OWNER, EXP)
        self.assertTrue(after["allow"], after["problems"])
        self.assertEqual(after["report_id"], recorded["report_id"])

    def test_result_analysis_two_stages_and_covers_equality(self):
        self.h.register_active()
        pending = self.h.trigger(stage="pending")
        prep1 = self.h.prepare()
        self.assertEqual(len(prep1["trigger_ids"]), 1)
        report1 = self.h.report(prep1)
        # covers 必须与材料包相等
        tampered = json.loads(json.dumps(report1))
        tampered["covers"]["trigger_ids"] = []
        with self.assertRaises(MonitoringError):
            self.h.record(tampered)
        report1["covers"]["user_seq"] = prep1["covers"]["user_seq"] + 1
        with self.assertRaises(MonitoringError):
            self.h.record(report1)
        # 恢复正确 covers 后登记
        report1 = self.h.report(prep1)
        recorded = self.h.record(report1)
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])
        # review 追加 evidence 后形成新的待审
        self.h.trigger(stage="evidence", note="复盘证据已更新")
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        self.assertTrue(any("待审触发" in item for item in blocked["problems"]))
        prep2 = self.h.prepare()
        self.h.record(self.h.report(prep2))
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])
        self.assertNotEqual(recorded["prepared_id"], prep2["prepared_id"])
        self.assertTrue(pending["created"])

    def test_prepare_ids_stay_unique_with_frozen_clock_and_nonce(self):
        """材料标识不靠时间也不靠随机：墙钟与 nonce 都冻住时，仍靠追加序号区分。"""
        self.h.register_active()
        self.h.trigger()
        fixed = "2026-01-01T00:00:00.000000+00:00"
        with mock.patch("kernel.autoresearch.monitoring._now", return_value=fixed), \
                mock.patch("kernel.autoresearch.monitoring.secrets.token_hex",
                           return_value="0000000000000000"):
            first = self.h.prepare()
            second = self.h.prepare()
        # 其余输入完全相同（状态、covers、方向 sha、事件摘要、墙钟、nonce），只有追加序号不同
        self.assertEqual(first["covers"], second["covers"])
        self.assertEqual(first["direction_sha256"], second["direction_sha256"])
        self.assertNotEqual(first["prepared_id"], second["prepared_id"])
        self.assertNotEqual(first["prepared_event_id"], second["prepared_event_id"])
        self.assertNotEqual(first["pack"], second["pack"])
        # 两次准备各留自己的材料包与快照：旧版本没有被覆盖
        self.assertNotEqual((self.root / first["pack"]).read_bytes(),
                            (self.root / second["pack"]).read_bytes())
        for prep in (first, second):
            self.assertTrue((self.root / prep["pack"]).is_file())
            self.assertTrue((self.root / prep["snapshot"]).is_file())
        # 账上两份准备记录各指自己的 id：没有「同一 prepared_id 命中多条」的歧义
        rows = [event for event in self.h.mon._read(OWNER) if event["kind"] == "prepared"]
        self.assertEqual([event["payload"]["prepared_id"] for event in rows],
                         [first["prepared_id"], second["prepared_id"]])
        self.assertEqual([event["id"] for event in rows],
                         [first["prepared_event_id"], second["prepared_event_id"]])

    def test_prepare_refuses_to_overwrite_existing_material_version(self):
        """id 真撞车时宁可拒绝，也不覆盖旧材料包（否则旧报告会变成「材料包已被改写」）。"""
        self.h.register_active()
        self.h.trigger()
        with mock.patch("kernel.autoresearch.monitoring._material_id",
                        return_value="PREP-0000000000000000"):
            first = self.h.prepare()
            with self.assertRaises(MonitoringError) as caught:
                self.h.prepare()
        self.assertIn("撞车", str(caught.exception))
        self.assertIn("拒绝覆盖", str(caught.exception))
        # 旧材料包还在，未被第二次 prepare 改写
        self.assertTrue((self.root / first["pack"]).is_file())
        rows = [event for event in self.h.mon._read(OWNER) if event["kind"] == "prepared"]
        self.assertEqual([event["payload"]["prepared_id"] for event in rows],
                         [first["prepared_id"]])

    def test_quote_must_be_verbatim_and_from_user(self):
        self.h.set_owner([user(1, USER_TEXT), assistant(2, "收到，继续守回归。"),
                          user(5, NEW_WORDS), tool_call(6), tool_result(7, "call-6", "t1 通过")])
        self.h.register_active(seq=1)
        self.h.trigger()
        prep = self.h.prepare()
        paraphrased = self.h.report(prep, quote_text="把已经通过的回归题都保住")
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(paraphrased)
        self.assertIn("逐字", str(caught.exception))
        future = self.h.report(prep, quote_seq=99, quote_text=USER_TEXT)
        with self.assertRaises(MonitoringError):
            self.h.record(future)
        # 引文 seq 必须出现在 intent.sources 里
        not_cited = self.h.report(prep, quote_seq=5, quote_text=NEW_WORDS,
                                  intent_sources=[{"session": OWNER, "seq": 1}])
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(not_cited)
        self.assertIn("intent.sources", str(caught.exception))
        self.h.record(self.h.report(prep, quote_seq=5, quote_text=NEW_WORDS,
                                    intent_sources=[{"session": OWNER, "seq": 5}]))
        # 引文指向注入消息：intent.sources 先被拒（不是真实用户原话）
        h = self._fresh()
        h.set_owner([user(1, USER_TEXT), injected(2, "这是系统注入的一段说明文字"), assistant(3, "ok")])
        h.register_active(seq=1)
        h.trigger()
        prep = h.prepare()
        injected_quote = h.report(prep, quote_seq=2, quote_text="这是系统注入的一段说明文字",
                                  intent_sources=[{"session": OWNER, "seq": 2}])
        with self.assertRaises(MonitoringError) as caught:
            h.record(injected_quote)
        self.assertIn("真实用户", str(caught.exception))

    def test_report_text_must_match_child_visible_json(self):
        self.h.register_active()
        self.h.trigger()
        prep = self.h.prepare()
        report = self.h.report(prep)
        compact = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(report, text=compact)
        self.assertIn("不相等", str(caught.exception))
        # 正文里夹一句解释：不是整段 JSON
        with self.assertRaises(MonitoringError):
            self.h.record(report, text=json.dumps(report, ensure_ascii=False, indent=2) + "\n补充一句")
        self.h.record(report)

    def test_fork_and_seeded_child_rejected(self):
        self.h.register_active()
        self.h.trigger()
        prep = self.h.prepare()
        report = self.h.report(prep)
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(report, provider="fork")
        self.assertIn("provider", str(caught.exception))
        with self.assertRaises(MonitoringError):
            self.h.record(report, seeded=True)
        with self.assertRaises(MonitoringError):
            self.h.record(report, parent="someone-else")
        with self.assertRaises(MonitoringError):
            self.h.record(report, depth=2)

    def test_require_raw_session_read(self):
        self.h.register_active()
        self.h.trigger()
        prep = self.h.prepare()
        report = self.h.report(prep, evidence_reads=[{"kind": "snapshot", "path": prep["snapshot"]}])
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(report)
        self.assertIn("raw_session", str(caught.exception))

    def test_trajectory_cannot_read_future_or_fabricate_rounds(self):
        self.h.register_active()
        self.h.trigger()
        prep = self.h.prepare()
        report = self.h.report(prep, steps=[{"seq": 99, "what": "读到了未来的事件"}])
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(report)
        self.assertIn("截止", str(caught.exception))
        round_sources = [self.h.ref("results.json", "L1")]
        report = self.h.report(prep, rounds=[
            {"label": "R1", "observed": "t1 通过", "interpretation": "没偏离",
             "at_seq": 3, "sources": round_sources},
            {"label": "R2", "observed": "t2 通过", "interpretation": "没偏离",
             "at_seq": 2, "sources": round_sources},
        ])
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(report)
        self.assertIn("递增", str(caught.exception))
        report = self.h.report(prep, rounds=[
            {"label": "R1", "observed": "t1 通过", "interpretation": "没偏离",
             "at_seq": 99, "sources": round_sources},
        ])
        with self.assertRaises(MonitoringError):
            self.h.record(report)

    # -- L7 过期与防篡改 ---------------------------------------------------

    def test_pack_tamper_blocks_record_and_invalidates_report(self):
        self.h.register_active()
        self.h.trigger()
        prep = self.h.prepare()
        pack = self.root / prep["pack"]
        original = pack.read_bytes()
        pack.write_bytes(original + b"x")
        with self.assertRaises(MonitoringError) as caught:
            self.h.record(self.h.report(prep))
        self.assertIn("材料包", str(caught.exception))
        pack.write_bytes(original)
        recorded = self.h.record(self.h.report(prep))
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])
        pack.write_bytes(original + b"y")
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        stale = blocked["report_stale"]
        self.assertEqual(stale[0]["report_id"], recorded["report_id"])
        self.assertTrue(any("材料包" in reason for reason in stale[0]["reasons"]))

    def test_result_version_change_invalidates_report(self):
        self.h.register_active()
        self.h.trigger()
        prep = self.h.prepare()
        self.h.record(self.h.report(prep))
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])
        self.h.write("results.json", "t1 未通过\nt2 通过\nr1 通过\n")
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        reasons = "；".join(blocked["report_stale"][0]["reasons"])
        self.assertIn("结果", reasons)

    # -- L5 / L8 意见与阻断 -------------------------------------------------

    def test_old_report_blocking_issue_inherited_until_resolved(self):
        self.h.register_active()
        self.h.trigger()
        first = self.h.record(self.h.report(self.h.prepare(),
                                            issues=[self.h.issue("M1", blocking=True)],
                                            verdict="drifting"))
        self.assertFalse(self.h.mon.check_start(OWNER, EXP)["allow"])
        self.h.respond(first["report_id"], "M1", disposition="declined",
                       reason="我认为这条不成立")
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        self.assertTrue(any("declined 不消除阻断" in item for item in blocked["problems"]))
        # 新用户原话让旧报告过期，但阻断意见不随报告消失
        self.h.add(user(5, NEW_WORDS))
        self.h.mon.sync(OWNER)
        second = self.h.record(self.h.report(self.h.prepare()))
        self.assertNotEqual(first["report_id"], second["report_id"])
        state = self.h.mon.status(OWNER)
        stale_ids = [item["report_id"] for item in state["stale_reports"]]
        self.assertIn(first["report_id"], stale_ids)
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        self.assertTrue(any("M1" in item and "仍有效" in item for item in blocked["problems"]))
        # 只有仍有效报告的 explicit resolves 能闭合
        resolve = {"report_id": first["report_id"], "issue_id": "M1",
                   "reason": "逐题读数已补齐并复核，问题不再成立",
                   "sources": [self.h.ref("results.json", "L2")]}
        third = self.h.record(self.h.report(self.h.prepare(), resolves=[resolve]))
        self.assertNotEqual(second["report_id"], third["report_id"])
        after = self.h.mon.check_start(OWNER, EXP)
        self.assertTrue(after["allow"], after["problems"])

    def test_user_waive_requires_bound_user_command(self):
        self.h.register_active()
        self.h.trigger()
        first = self.h.record(self.h.report(self.h.prepare(),
                                            issues=[self.h.issue("M1", blocking=True)]))
        self.assertFalse(self.h.mon.check_start(OWNER, EXP)["allow"])
        # 无关用户消息（只是继续），不能因为「字眼像」就当豁免
        self.h.add(user(5, NEW_WORDS))
        with self.assertRaises(MonitoringError) as caught:
            self.h.mon.waive(OWNER, report_id=first["report_id"], issue_id="M1",
                             proof={"session_id": OWNER, "seq": 5},
                             reason="用户口头同意", sources=[self.h.ref("results.json", "L1")])
        self.assertIn("命令", str(caught.exception))
        # 真正的用户命令：正文是一个含 action/report_id/issue_id 的 JSON 对象
        command = {"action": "waive", "report_id": first["report_id"], "issue_id": "M1"}
        self.h.add(user(6, json.dumps(command, ensure_ascii=False)))
        # 加宽 expected 不接受（必须逐键相等）
        with self.assertRaises(MonitoringError) as caught:
            self.h.mon.waive(OWNER, report_id=first["report_id"], issue_id="M1",
                             proof={"session_id": OWNER, "seq": 6},
                             expected=dict(command, note="顺便"), reason="放宽命令",
                             sources=[self.h.ref("results.json", "L1")])
        self.assertIn("逐键", str(caught.exception))
        # 让新用户原话进账并由一份仍有效报告覆盖
        self.h.mon.sync(OWNER)
        second = self.h.record(self.h.report(self.h.prepare()))
        self.assertNotEqual(first["report_id"], second["report_id"])
        # 正确的绑定命令可以闭合这条阻断意见
        out = self.h.mon.waive(OWNER, report_id=first["report_id"], issue_id="M1",
                               proof={"session_id": OWNER, "seq": 6},
                               reason="用户明确命令豁免这条", sources=[self.h.ref("results.json", "L1")])
        self.assertEqual(out["command"], command)
        after = self.h.mon.check_start(OWNER, EXP)
        self.assertTrue(after["allow"], after["problems"])
        self.assertEqual(len(after["issues"]["blocking_open"]), 0)

    def test_user_waive_binds_issue_id(self):
        self.h.register_active()
        self.h.trigger()
        first = self.h.record(self.h.report(self.h.prepare(),
                                            issues=[self.h.issue("M1", blocking=True)]))
        # 命令说的是别的问题：即使报告 id 一样，也不能当 M1 的豁免
        other = {"action": "waive", "report_id": first["report_id"], "issue_id": "M9"}
        self.h.add(user(5, json.dumps(other, ensure_ascii=False)))
        with self.assertRaises(MonitoringError) as caught:
            self.h.mon.waive(OWNER, report_id=first["report_id"], issue_id="M1",
                             proof={"session_id": OWNER, "seq": 5},
                             reason="拿别的问题的命令顶替",
                             sources=[self.h.ref("results.json", "L1")])
        self.assertIn("逐键", str(caught.exception))
        # proof 不允许夹带日志路径或其它键
        with self.assertRaises(MonitoringError):
            self.h.mon.waive(OWNER, report_id=first["report_id"], issue_id="M1",
                             proof={"session_id": OWNER, "seq": 5, "log_path": "x"},
                             reason="换字段", sources=[self.h.ref("results.json", "L1")])

    def test_normal_exploration_allows_progress_without_new_authorization(self):
        self.h.register_active()
        self.h.trigger()
        prep = self.h.prepare()
        recorded = self.h.record(self.h.report(prep, issues=[self.h.issue("M2", blocking=False)]))
        self.h.respond(recorded["report_id"], "M2", disposition="accepted")
        out = self.h.mon.check_start(OWNER, EXP)
        self.assertTrue(out["allow"], out["problems"])
        self.assertEqual(out["issues"]["blocking_open"], [])
        # 没有新增任何用户原话，也没有额外授权记录
        self.assertEqual(out["sync_watermark"], out["assessed_watermark"])
        self.assertEqual(out["pending"], [])

    def test_issue_three_state_disposition(self):
        self.h.register_active()
        self.h.trigger()
        recorded = self.h.record(self.h.report(self.h.prepare(),
                                               issues=[self.h.issue("M3", blocking=False)]))
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        self.assertTrue(any("未处理" in item for item in blocked["problems"]))
        self.h.respond(recorded["report_id"], "M3", disposition="needs_user",
                       reason="需要用户决定要不要补逐题读数")
        waiting = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(waiting["allow"])
        self.assertTrue(any("等待人类决定" in item for item in waiting["problems"]))
        self.h.respond(recorded["report_id"], "M3", disposition="accepted",
                       reason="负责人自己补了逐题读数")
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])

    def test_heartbeat_and_block_unblock(self):
        self.h.register_active()
        with self.assertRaises(MonitoringError):
            self.h.mon.event(OWNER, "heartbeat", exp=EXP, note="例行巡检")
        self.h.mon.event(OWNER, "block", exp=EXP, text="远端机器暂时不可用",
                         sources=[self.h.ref("results.json", "L1")])
        blocked = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(blocked["allow"])
        self.assertEqual(blocked["state"], "blocked")
        self.assertTrue(any("blocked" in item for item in blocked["problems"]))
        self.h.mon.event(OWNER, "unblock", exp=EXP, text="机器恢复")
        self.assertTrue(self.h.mon.check_start(OWNER, EXP)["allow"])

    def test_result_analysis_one_registration_per_round(self):
        self.h.register_active()
        first = self.h.trigger(stage="pending")
        second = self.h.trigger(stage="pending")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(self.h.mon.status(OWNER)["pending"]), 1)

    def test_hook_gap_diagnostic_vs_enforced(self):
        # 会话里有**两轮**结束信号，账上只登记了一轮的 result_analysis ⇒ 第二轮是 hook 缺口
        events = [user(1, USER_TEXT), assistant(2, "收到"),
                  tool_call(3, name="finish"), tool_result(4, "call-3", "第一轮结束"),
                  tool_call(5, name="review"), tool_result(6, "call-5", "复盘完成")]
        self.h.set_owner(events)
        self.h.register_none()
        out = self.h.mon.check_start(OWNER, EXP)
        self.assertTrue(out["allow"], out["problems"])
        self.assertTrue(out["hook_gaps"])
        self.assertFalse(out["hook_gaps_enforced"])
        self.assertTrue(out["hook_gaps_diagnostic"])
        # active 下同一缺口变成阻断（补登记 stage=pending 才能过）
        h = self._fresh()
        h.set_owner(events)
        h.register_active()
        h.trigger()
        h.record(h.report(h.prepare()))
        active = h.mon.check_start(OWNER, EXP)
        self.assertFalse(active["allow"])
        self.assertTrue(any("监控缺口" in item for item in active["problems"]))

    # -- L10 安全动作 -------------------------------------------------------

    def test_safe_actions_exempt_but_launch_enforced(self):
        fresh = self.h.mon.check_start(OWNER, EXP, action="stop")
        self.assertTrue(fresh["allow"])
        self.assertTrue(fresh["exempt"])
        self.assertFalse(fresh["registered"])
        for action in ("collect", "stop", "package", "rollback", "halt", "checkpoint"):
            self.assertTrue(self.h.mon.check_start(OWNER, EXP, action=action)["allow"])
        launch = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(launch["allow"])
        self.h.register_active()
        self.h.trigger()
        self.assertFalse(self.h.mon.check_start(OWNER, EXP)["allow"])
        self.assertTrue(self.h.mon.check_start(OWNER, EXP, action="collect")["allow"])
        with self.assertRaises(MonitoringError):
            self.h.mon.check_start(OWNER, EXP, action="deploy")

    def test_workflow_alias_and_allow_key(self):
        self.assertIs(Monitoring, Monitor)
        self.h.register_active()
        self.h.trigger()
        out = self.h.mon.monitor_check(OWNER, EXP, action="launch")
        self.assertFalse(out["allow"])
        self.assertTrue(out["exempt"] is False)

    # -- I5 只读零写 --------------------------------------------------------

    def test_readonly_commands_do_not_write(self):
        self.h.register_active()
        self.h.trigger()
        self.h.record(self.h.report(self.h.prepare()))
        db = self.h.mon.db_path
        before_bytes = db.read_bytes()
        before_files = {path.relative_to(self.root).as_posix():
                        (path.stat().st_size, path.stat().st_mtime_ns)
                        for path in self.root.rglob("*") if path.is_file()}
        self.h.mon.status(OWNER)
        self.h.mon.check_start(OWNER, EXP)
        self.h.mon.check_start(OWNER, EXP, action="stop")
        self.h.mon.diagnose(OWNER)
        self.assertEqual(before_bytes, db.read_bytes())
        after_files = {path.relative_to(self.root).as_posix():
                       (path.stat().st_size, path.stat().st_mtime_ns)
                       for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before_files, after_files)

    # -- L8 闭合 ------------------------------------------------------------

    def test_close_achieved_and_unsatisfiable_need_report(self):
        # achieved：必须有 verdict=achieved
        self.h.register_active()
        self.h.trigger()
        wrong = self.h.record(self.h.report(self.h.prepare(), closure_target="closed"))
        with self.assertRaises(MonitoringError) as caught:
            self.h.mon.close(OWNER, reason="achieved", target="closed",
                             report_id=wrong["report_id"])
        self.assertIn("achieved", str(caught.exception))
        # 达成不能靠用户一句话（不接受 proof）
        with self.assertRaises(MonitoringError) as caught:
            self.h.mon.close(OWNER, reason="achieved", target="closed",
                             report_id=wrong["report_id"],
                             proof={"session_id": OWNER, "seq": 1})
        self.assertIn("不接受用户命令", str(caught.exception))

        h = self._fresh()
        h.register_active()
        h.trigger()
        achieved = h.record(h.report(h.prepare(), verdict="achieved", closure_target="closed",
                                     closure_basis="逐题读数达到计划判据"))
        closed = h.mon.close(OWNER, reason="achieved", target="closed",
                             report_id=achieved["report_id"])
        self.assertEqual(closed["state"], "closed")
        after = h.mon.check_start(OWNER, EXP)
        self.assertEqual(after["state"], "closed")
        self.assertTrue(after["allow"], after["problems"])

        # unsatisfiable：必须有 verdict=unachievable
        h = self._fresh()
        h.register_active()
        h.trigger()
        kept = h.record(h.report(h.prepare(), closure_target="closed"))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="unsatisfiable", target="closed",
                        report_id=kept["report_id"])
        self.assertIn("unachievable", str(caught.exception))

        # 过期报告不能用于闭合
        h = self._fresh()
        h.register_active()
        h.trigger()
        kept = h.record(h.report(h.prepare(), verdict="achieved", closure_target="closed"))
        h.add(user(5, NEW_WORDS))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="achieved", target="closed", report_id=kept["report_id"])
        self.assertIn("过期", str(caught.exception))

    def test_close_withdrawn_requires_bound_user_command(self):
        h = self._fresh()
        command = {"action": "close_intent", "intent_id": "INT-1", "reason": "withdrawn",
                   "replacement_intent_id": None, "target": "closed"}
        h.set_owner(base_events() + [user(5, json.dumps(command, ensure_ascii=False)),
                                     user(6, NEW_WORDS)])
        h.register_active(seq=1, intent_id="INT-1")
        h.trigger()
        kept = h.record(h.report(h.prepare(), closure_target="closed"))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed", report_id=kept["report_id"])
        self.assertIn("proof", str(caught.exception))
        # 无关的「继续」不能证明撤回
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed", report_id=kept["report_id"],
                        proof={"session_id": OWNER, "seq": 6})
        self.assertIn("逐键", str(caught.exception))
        # 加宽 expected 不接受
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed", report_id=kept["report_id"],
                        proof={"session_id": OWNER, "seq": 5},
                        expected=dict(command, note="顺便"))
        self.assertIn("逐键", str(caught.exception))
        # 正例：命令逐键绑定
        out = h.mon.close(OWNER, reason="withdrawn", target="closed", report_id=kept["report_id"],
                          proof={"session_id": OWNER, "seq": 5})
        self.assertEqual(out["state"], "closed")
        self.assertEqual(out["command"], command)
        self.assertEqual(out["proof_seq"], 5)
        self.assertTrue(h.mon.check_start(OWNER, EXP)["allow"])

    def test_close_replaced_binds_new_intent_id(self):
        h = self._fresh()
        command = {"action": "close_intent", "intent_id": "INT-1", "reason": "replaced",
                   "replacement_intent_id": "INT-2", "target": "closed"}
        h.set_owner(base_events() + [user(5, json.dumps(command, ensure_ascii=False))])
        h.register_active(seq=1, intent_id="INT-1")
        h.trigger()
        kept = h.record(h.report(h.prepare(), closure_target="closed"))
        # 目标只能是 closed
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="replaced", target="none", report_id=kept["report_id"],
                        replacement_intent_id="INT-2", proof={"session_id": OWNER, "seq": 5})
        self.assertIn("closed", str(caught.exception))
        # 替换 id 与用户命令不一致：宿主逐键拒
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="replaced", target="closed", report_id=kept["report_id"],
                        replacement_intent_id="INT-3", proof={"session_id": OWNER, "seq": 5})
        self.assertIn("逐键", str(caught.exception))
        # 拿现行 id 替换自己：服务层拒
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="replaced", target="closed", report_id=kept["report_id"],
                        replacement_intent_id="INT-1", proof={"session_id": OWNER, "seq": 5})
        self.assertIn("新", str(caught.exception))
        # 用旧消息（意图原话本身）不能当替换命令
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="replaced", target="closed", report_id=kept["report_id"],
                        replacement_intent_id="INT-2", proof={"session_id": OWNER, "seq": 1})
        self.assertIn("晚于", str(caught.exception))
        # 正例
        out = h.mon.close(OWNER, reason="replaced", target="closed", report_id=kept["report_id"],
                          replacement_intent_id="INT-2", proof={"session_id": OWNER, "seq": 5})
        self.assertEqual(out["state"], "closed")
        self.assertEqual(out["command"]["replacement_intent_id"], "INT-2")
        self.assertTrue(h.mon.check_start(OWNER, EXP)["allow"])

    # -- L8 / L9 自然语言撤回 / 替换（真实原话 + 独立报告绑定）----------------

    def test_close_withdrawn_accepts_natural_quote_bound_by_report(self):
        h = self._fresh()
        recorded, prep = h.bound_close_setup()
        out = h.mon.close(OWNER, reason="withdrawn", target="closed",
                          report_id=recorded["report_id"],
                          quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        self.assertEqual(out["state"], "closed")
        self.assertIsNone(out["proof_seq"])
        self.assertIsNone(out["verified"])
        self.assertEqual(out["command"], {"action": "close_intent", "intent_id": "INT-1",
                                          "reason": "withdrawn", "replacement_intent_id": None,
                                          "target": "closed"})
        self.assertEqual(out["binding"]["report_id"], recorded["report_id"])
        self.assertEqual(out["binding"]["user_quote"]["seq"], 6)
        self.assertEqual(out["binding"]["user_quote"]["text"], WITHDRAW_TEXT)
        self.assertEqual(out["evidence"]["text"], WITHDRAW_TEXT)
        # 自然语言路径不冒充「用户发过 JSON 命令」：不走 user_command 宿主核验
        kinds = [call["kind"] for call in h.host.calls]
        self.assertNotIn("user_command", kinds)
        self.assertIn("monitor_independence", kinds)
        after = h.mon.check_start(OWNER, EXP)
        self.assertTrue(after["allow"], after["problems"])
        self.assertEqual(after["state"], "closed")

    def test_close_replaced_accepts_natural_quote_bound_by_report(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup(quote_text=REPLACE_TEXT, reason="replaced",
                                          replacement_intent_id="INT-2")
        out = h.mon.close(OWNER, reason="replaced", target="closed",
                          report_id=recorded["report_id"], replacement_intent_id="INT-2",
                          quote={"session": OWNER, "seq": 6, "text": REPLACE_TEXT})
        self.assertEqual(out["state"], "closed")
        self.assertEqual(out["command"]["replacement_intent_id"], "INT-2")
        self.assertEqual(out["binding"]["expected"]["reason"], "replaced")
        self.assertTrue(h.mon.check_start(OWNER, EXP)["allow"])

    def test_close_natural_quote_to_none_with_binding(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup(target="none")
        out = h.mon.close(OWNER, reason="withdrawn", target="none",
                          report_id=recorded["report_id"],
                          quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        self.assertEqual(out["state"], "none")
        self.assertTrue(h.mon.check_start(OWNER, EXP)["allow"])

    def test_close_withdrawn_uses_real_host_quote(self):
        """真 `DSHHost` + 合成日志：原话是宿主会话里逐字读到的，spawn 独立性也由宿主核。"""
        owner, child = "owner-real", "monitor-real"
        logs = RealHostLogs(self.root)
        owner_rows = [
            {"type": "session", "id": owner, "delegationDepth": 0, "isSeeded": False,
             "createdAt": 1000},
            {"seq": 0, "type": "user/message", "time": 1100,
             "data": {"source": {"kind": "user", "rpcId": "rpc-1"},
                      "message": {"role": "user",
                                  "content": [{"type": "text", "text": USER_TEXT}]}}},
            {"seq": 1, "type": "assistant/message", "time": 1200,
             "data": {"message": {"role": "assistant",
                                  "content": [{"type": "text", "text": "收到，继续守回归。"}]}}},
        ]
        logs.write(owner, owner_rows)
        mon = Monitor(self.root, host=logs.host())
        mon.register(owner, state="active", intent="守住已经通过的回归题",
                     sources=[{"session": owner, "seq": 0}],
                     reason="用户原话要求盯住回归", intent_id="INT-1")
        owner_rows.append(
            {"seq": 2, "type": "user/message", "time": 1300,
             "data": {"source": {"kind": "user", "rpcId": "rpc-2"},
                      "message": {"role": "user",
                                  "content": [{"type": "text", "text": WITHDRAW_TEXT}]}}})
        logs.write(owner, owner_rows)
        mon.event(owner, "result_analysis", exp=EXP, stage="pending", note="结果回收待分析",
                  sources=[{"path": "results.json", "locator": "L1"}])
        prep = mon.prepare(owner, exp=EXP, sources=[{"path": "results.json", "locator": "L1"}],
                           direction=[{"path": "plan.md", "locator": "L1"}])
        binding = {"expected": {"action": "close_intent", "intent_id": "INT-1",
                                "reason": "withdrawn", "replacement_intent_id": None,
                                "target": "closed"},
                   "user_quote": {"session": owner, "seq": 2, "text": WITHDRAW_TEXT}}
        report = {
            "prepared_id": prep["prepared_id"], "monitor_session": child,
            "owner_session": owner, "exp": EXP, "verdict": "on_track",
            "covers": json.loads(json.dumps(prep["covers"])),
            "direction_sha256": prep["direction_sha256"],
            "intent": {"stated": "守住已经通过的回归题", "verdict": "consistent",
                       "note": "用户原话与最近几轮动作一致",
                       "quote": {"seq": 0, "text": USER_TEXT},
                       "sources": [{"session": owner, "seq": 0}]},
            "trajectory": {"summary": "先确认基线，再跑小样本验证", "verdict": "on_track",
                           "note": "每一步都能对上意图，没有换抓手",
                           "steps": [{"seq": 0, "what": "从用户原话确认意图与判据"}]},
            "findings": [{"text": "样本太小，读数不能外推到全量",
                          "sources": [{"path": "results.json", "locator": "L1"}]}],
            "issues": [],
            "next_direction": {"text": "先停这个方向，等负责人登记新意图",
                               "direction_sha256": prep["direction_sha256"],
                               "sources": [{"path": "plan.md", "locator": "L1"}]},
            "closure": {"target": "closed",
                        "basis": "用户最新原话撤回了这个方向，没有新原话要求继续",
                        "sources": [{"session": owner, "seq": 2},
                                    {"path": "plan.md", "locator": "L1"}],
                        "user_command": binding},
            "evidence_reads": [{"kind": "raw_session", "session": owner, "seq": 0},
                               {"kind": "snapshot", "path": prep["snapshot"]}],
            "resolves": [],
        }
        body = json.dumps(report, ensure_ascii=False, sort_keys=True)
        logs.write(child, [
            {"type": "session", "id": child, "parentSession": owner, "origin": "subagent",
             "delegationDepth": 1, "isSeeded": False, "createdAt": 1400},
            {"seq": 0, "type": "subagent/descriptor", "time": 1400,
             "data": {"provider": "spawn", "mode": "one-shot"}},
            {"seq": 1, "type": "assistant/message", "time": 1500,
             "data": {"message": {"role": "assistant",
                                  "content": [{"type": "text", "text": body}]}}},
        ])
        owner_rows.extend([
            {"seq": 3, "type": "tool/call", "time": 1350,
             "data": {"name": "subagent", "callId": "call-1",
                      "arguments": {"prompt": "请独立审计 " + prep["prepared_id"]}}},
            {"seq": 4, "type": "tool/result", "time": 1360,
             "data": {"message": {"toolCallId": "call-1",
                                  "content": [{"type": "text", "text": "started " + child}]}}},
            {"seq": 5, "type": "subagent/catalog", "time": 1370,
             "data": {"childId": child, "mode": "one-shot"}},
        ])
        logs.write(owner, owner_rows)
        recorded = mon.record(owner, report_bytes=body.encode("utf-8"),
                              proof={"session_id": child, "seq": 1})
        # 真宿主逐字核验：转述 / 拼凑的文本被拒
        with self.assertRaises(MonitoringError) as caught:
            mon.close(owner, reason="withdrawn", target="closed",
                      report_id=recorded["report_id"],
                      quote={"session": owner, "seq": 2, "text": "用户说不想做了"})
        self.assertIn("逐字", str(caught.exception))
        out = mon.close(owner, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": owner, "seq": 2, "text": WITHDRAW_TEXT})
        self.assertEqual(out["state"], "closed")
        self.assertIsNone(out["proof_seq"])
        self.assertEqual(out["evidence"]["session"], owner)
        self.assertEqual(out["evidence"]["text"], WITHDRAW_TEXT)
        self.assertEqual(out["binding"]["user_quote"]["seq"], 2)
        self.assertEqual(out["binding"]["user_quote"]["sha256"],
                         "sha256:" + hashlib.sha256(
                             WITHDRAW_TEXT.encode("utf-8")).hexdigest())
        self.assertTrue(mon.check_start(owner, EXP)["allow"])

    def test_close_quote_alone_cannot_close_intent(self):
        # 用户原话 + 报告，但报告**没有**把这条原话解释成撤回：主会话自己传一段话不算
        h = self._fresh()
        h.register_active(seq=1)
        h.add(user(6, WITHDRAW_TEXT))
        h.trigger()
        kept = h.record(h.report(h.prepare(), closure_target="closed"))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=kept["report_id"],
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        self.assertIn("closure.user_command", str(caught.exception))
        # 连报告都不给：报错要同时指出两条路径
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed", report_id=kept["report_id"])
        self.assertIn("proof", str(caught.exception))
        self.assertIn("quote", str(caught.exception))
        # 只给 quote、不给报告：主会话单句关不掉
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed", report_id="MON-missing",
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        self.assertIn("找不到该监控报告", str(caught.exception))
        # 失败后状态仍是 active：没有半闭合
        self.assertEqual(h.mon.status(OWNER)["state"], "active")

    def test_close_quote_must_be_real_user_message(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup()
        # 转述 / 拼凑的文本：逐字比对失败
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 6, "text": "用户说不想做了"})
        self.assertIn("逐字", str(caught.exception))
        # 指到 assistant 消息：不是真实用户原话
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 2, "text": "收到，继续守回归。"})
        self.assertIn("真实用户原话", str(caught.exception))
        # 必须写明 session
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"seq": 6, "text": WITHDRAW_TEXT})
        self.assertIn("session", str(caught.exception))
        # 指到别人（监控子会话）的会话：拒绝串会话
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": "monitor-1", "seq": 6, "text": WITHDRAW_TEXT})
        self.assertIn("session", str(caught.exception))

    def test_close_quote_must_be_the_same_one_bound_by_report(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup()
        # 同一 seq 的另一个片段：不是「同一条 quote」
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT[:6]})
        self.assertIn("同一条", str(caught.exception))

        h = self._fresh()
        recorded, _ = h.bound_close_setup(extra_users=((7, NEW_WORDS),))
        # 报告绑的是 seq=6，close 却拿 seq=7：不是同一条
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 7, "text": NEW_WORDS})
        self.assertIn("同一条", str(caught.exception))

    def test_close_quote_must_be_newer_than_intent_source(self):
        h = self._fresh()
        h.register_active(seq=1)
        h.trigger()
        prep = h.prepare()
        kept = h.record(h.report(prep, closure_target="closed",
                                 closure_binding=h.bind(seq=1, text=USER_TEXT),
                                 closure_sources=[{"session": OWNER, "seq": 1}]))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=kept["report_id"],
                        quote={"session": OWNER, "seq": 1, "text": USER_TEXT})
        self.assertIn("晚于", str(caught.exception))

    def test_close_binding_must_point_at_current_intent_and_action(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup(binding=h.bind(seq=6, text=WITHDRAW_TEXT,
                                                         intent_id="INT-9"))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        text = str(caught.exception)
        self.assertIn("逐键", text)
        self.assertIn("INT-9", text)

        # 报告绑 withdrawn，本次却按 replaced 关：理由不同就是不逐键相等
        h = self._fresh()
        recorded, _ = h.bound_close_setup()
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="replaced", target="closed",
                        report_id=recorded["report_id"], replacement_intent_id="INT-2",
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        self.assertIn("逐键", str(caught.exception))

    def test_close_binding_requires_same_quote_in_closure_sources(self):
        h = self._fresh()
        h.register_active(seq=1)
        h.add(user(6, WITHDRAW_TEXT))
        h.trigger()
        prep = h.prepare()
        with self.assertRaises(MonitoringError) as caught:
            h.record(h.report(prep, closure_target="closed",
                              closure_binding=h.bind(seq=6, text=WITHDRAW_TEXT)))
        self.assertIn("closure.sources", str(caught.exception))

    def test_report_binding_shape_checked_at_record(self):
        h = self._fresh()
        h.register_active(seq=1)
        h.add(user(6, WITHDRAW_TEXT))
        h.trigger()
        prep = h.prepare()
        good = h.bind(seq=6, text=WITHDRAW_TEXT)
        sources = [{"session": OWNER, "seq": 6}]
        cases = [
            (dict(good, extra=1), "不允许的字段"),
            ({"expected": good["expected"]}, "user_quote"),
            ({"expected": dict(good["expected"], action="keep_intent"),
              "user_quote": good["user_quote"]}, "close_intent"),
            ({"expected": dict(good["expected"], note="顺便"),
              "user_quote": good["user_quote"]}, "不允许的字段"),
            ({"expected": dict(good["expected"], reason="achieved"),
              "user_quote": good["user_quote"]}, "withdrawn / replaced"),
            ({"expected": dict(good["expected"], target="none"),
              "user_quote": good["user_quote"]}, "closure.target"),
            ({"expected": dict(good["expected"], reason="replaced",
                               replacement_intent_id=None),
              "user_quote": good["user_quote"]}, "replacement_intent_id"),
            ({"expected": good["expected"], "user_quote": 6}, "user_quote"),
            ({"expected": good["expected"],
              "user_quote": dict(good["user_quote"], session="monitor-1")}, "负责人会话"),
            ({"expected": good["expected"],
              "user_quote": dict(good["user_quote"], text="用户说不用了")}, "逐字"),
        ]
        for binding, expected_text in cases:
            with self.subTest(binding=binding):
                with self.assertRaises(MonitoringError) as caught:
                    h.record(h.report(prep, closure_target="closed",
                                      closure_binding=binding, closure_sources=sources))
                self.assertIn(expected_text, str(caught.exception))
        # 这些失败都不该落账：状态仍是 active，报告数仍是 0
        status = h.mon.status(OWNER)
        self.assertEqual(status["state"], "active")
        self.assertEqual(status["reports"], [])

    def test_report_binding_cannot_claim_message_beyond_cutoff(self):
        h = self._fresh()
        h.register_active(seq=1)
        h.trigger()
        prep = h.prepare()
        h.add(user(9, WITHDRAW_TEXT))  # prepare 之后才出现：不在报告证据内
        with self.assertRaises(MonitoringError) as caught:
            h.record(h.report(prep, closure_target="closed",
                              closure_binding=h.bind(seq=9, text=WITHDRAW_TEXT),
                              closure_sources=[{"session": OWNER, "seq": 9}]))
        self.assertIn("截止", str(caught.exception))

    def test_new_user_message_after_report_blocks_natural_close(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup()
        h.add(user(9, NEW_WORDS))  # 报告之后的新原话：没有评估过
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        self.assertIn("过期", str(caught.exception))
        self.assertEqual(h.mon.status(OWNER)["state"], "active")

    def test_open_blocking_issue_blocks_natural_close(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup(issues=(h.issue(blocking=True),))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        text = str(caught.exception)
        self.assertTrue("未处理" in text or "阻断" in text, text)
        self.assertEqual(h.mon.status(OWNER)["state"], "active")

    def test_binding_report_must_be_the_current_one(self):
        h = self._fresh()
        recorded, _ = h.bound_close_setup()
        # 再出一份更新的独立报告：旧报告不再是「最后一份仍有效」
        h.trigger(exp="E2")
        h.record(h.report(h.prepare(exp="E2")))
        with self.assertRaises(MonitoringError) as caught:
            h.mon.close(OWNER, reason="withdrawn", target="closed",
                        report_id=recorded["report_id"],
                        quote={"session": OWNER, "seq": 6, "text": WITHDRAW_TEXT})
        self.assertIn("最后一份仍有效", str(caught.exception))

    def test_json_command_path_still_works_with_binding(self):
        # 无独立绑定时的原路径保持可用：用户自己发完整 JSON 命令
        h = self._fresh()
        command = {"action": "close_intent", "intent_id": "INT-1", "reason": "withdrawn",
                   "replacement_intent_id": None, "target": "closed"}
        h.register_active(seq=1, intent_id="INT-1")
        h.add(user(6, WITHDRAW_TEXT))
        h.add(user(7, json.dumps(command, ensure_ascii=False)))
        h.trigger()
        prep = h.prepare()
        kept = h.record(h.report(prep, closure_target="closed",
                                 closure_binding=h.bind(seq=6, text=WITHDRAW_TEXT),
                                 closure_sources=[{"session": OWNER, "seq": 6}]))
        out = h.mon.close(OWNER, reason="withdrawn", target="closed",
                          report_id=kept["report_id"], proof={"session_id": OWNER, "seq": 7})
        self.assertEqual(out["state"], "closed")
        self.assertEqual(out["command"], command)
        self.assertEqual(out["proof_seq"], 7)
        self.assertIsNone(out["binding"])
        self.assertIn("user_command", [call["kind"] for call in h.host.calls])

    # -- L13 未登记 / 无 host ----------------------------------------------

    def test_unregistered_and_missing_host_fail_closed(self):
        out = self.h.mon.check_start(OWNER, EXP)
        self.assertFalse(out["allow"])
        self.assertTrue(any("未登记" in item for item in out["problems"]))
        self.assertFalse(out["undecidable"])
        blind = Monitor(self.root)
        self.h.register_none()
        blind_out = blind.check_start(OWNER, EXP)
        self.assertFalse(blind_out["allow"])
        self.assertTrue(blind_out["undecidable"])
        self.assertTrue(any("host" in item for item in blind_out["problems"]))
        self.assertFalse(blind.status(OWNER)["ok"])
        # 安全动作在无 host 时仍可执行（L10）
        self.assertTrue(blind.check_start(OWNER, EXP, action="stop")["allow"])

    def test_tampered_ledger_detected(self):
        self.h.register_none()
        conn = sqlite3.connect(self.h.mon.db_path)
        try:
            conn.execute("DROP TRIGGER monitor_events_no_update")
            conn.execute("UPDATE monitor_events SET payload_json=? WHERE id=1",
                         ('{"baseline_seq": 999, "reason": "改过"}',))
            conn.commit()
        finally:
            conn.close()
        out = self.h.mon.status(OWNER)
        self.assertFalse(out["ok"])
        self.assertTrue(out["undecidable"])
        text = "；".join(out["problems"])
        self.assertIn("指纹", text)
        self.assertFalse(self.h.mon.check_start(OWNER, EXP)["allow"])

    # -- CLI ---------------------------------------------------------------

    def _adapter(self, sessions_dir):
        adapter = self.root / "adapter.py"
        adapter.write_text(
            "import json\n"
            "from pathlib import Path\n"
            "class Host:\n"
            "    def __init__(self, log_root):\n"
            "        self.root = Path(log_root) if log_root else None\n"
            "    def session(self, identity):\n"
            "        if self.root is None:\n"
            "            raise RuntimeError('没有日志根')\n"
            "        bare = identity[len('session-'):] if identity.startswith('session-') else identity\n"
            "        path = self.root / (bare + '.json')\n"
            "        if not path.is_file():\n"
            "            raise KeyError('没有这个会话')\n"
            "        return json.loads(path.read_text(encoding='utf-8'))\n"
            "    def verify(self, kind, payload):\n"
            "        return {'verified': True, 'kind': kind}\n"
            "def build_host(log_root=None):\n"
            "    return Host(log_root)\n",
            encoding="utf-8")
        sessions = self.root / "sessions"
        sessions.mkdir(exist_ok=True)
        return adapter, sessions

    def _cli(self, argv, sessions, adapter):
        full = ["--root", str(self.root), "--adapter", str(adapter),
                "--log-root", str(sessions), "--session", OWNER] + argv
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"DSH_SESSION_ID": ""}):
            code = monitoring_cli.main(full)
        return code, json.loads(out.getvalue() or "{}")

    def test_cli_with_adapter_reads_real_log_root(self):
        adapter, sessions = self._adapter(self.root)
        (sessions / f"{OWNER}.json").write_text(
            json.dumps(make_session(OWNER, events=base_events()), ensure_ascii=False),
            encoding="utf-8")
        code, out = self._cli(["register", "--state", "none", "--reason", "用户没有要求盯住"], sessions, adapter)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["state"], "none")
        code, out = self._cli(["status"], sessions, adapter)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["state"], "none")
        code, out = self._cli(["check", "--exp", EXP], sessions, adapter)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["allow"])
        code, out = self._cli(["check", "--exp", EXP, "--action", "stop"], sessions, adapter)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["exempt"])
        code, out = self._cli(["diagnose"], sessions, adapter)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["session"]["real_user"], 1)
        code, out = self._cli(["protocol"], sessions, adapter)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, HOST_PROTOCOL)

    def test_cli_without_adapter_is_undecidable(self):
        self.h.register_none()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"DSH_SESSION_ID": ""}):
            code = monitoring_cli.main(["--root", str(self.root), "--session", OWNER,
                                        "check", "--exp", EXP])
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["undecidable"])

    def test_help_lists_required_commands(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                monitoring_cli.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        text = out.getvalue()
        for command in ("register", "sync", "assess", "intent", "event", "prepare", "record",
                        "respond", "close", "check", "status", "diagnose", "protocol"):
            self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
