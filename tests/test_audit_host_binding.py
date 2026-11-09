# GENERATED-BY: audit-host binding migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：K2/K3/K4/K8/K11/K12 的宿主证据绑定测试（真 DSHHost + 合成日志，不是一律 True 的夹具）
# PURPOSE: 证明敏感动作的 expected 由治理层构造、created_at 取自宿主、独立性绑定 owner/actor/prepared_id/报告、
#          extend 不能自扩、历史豁免只认真实过去启动记录、check_start 不给假绿、CLI 默认注入 DSHHost。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。
"""宿主证据绑定测试（K2/K3/K4/K8/K11/K12）。

为什么用**真** `DSHHost` + 合成日志：夹具一律返回 True 只能证明「有个函数被调过」。
这里把合成会话写成 DSHHost 能读的日志（`root/sessions/<project>/<sid>/session.v1.jsonl.zstd`
+ 注入 decoder），再由 DSHHost 自己核消息时间、命令逐键相等、子会话是 spawn 出来的。

覆盖的绕过路径：

* 拿**另一个动作**的真实用户命令去关审计 / 豁免 / 扩额；
* 拿**另一个方案**的 expected；
* 调用方自带 `expected` / `created_at`（本层只收 proof）；
* 用**过期**的用户消息（早于 prepare / 早于报告）；
* 宿主结果**缺时间**（旧式只返回 verified=True 的夹具）；
* 报告文件**被改过**（短声明 SHA 对不上）；
* 审计会话**换人**（actor 与宿主核过的会话号不一致）；
* extend **未耗尽 / 旧命令重放 / 每次都要新命令（不设固定次数上限）**；
* legacy_exempt 拿**新轮次**或**无历史记录**冒充；
* `check_start` 的 closed + 未评估、未登记来源。

跑法：

    python -X utf8 -B -m unittest discover -s tests -p 'test_audit_host_binding.py' -v
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import governance as g  # noqa: E402
from kernel.autoresearch.governance_cli import main as cli  # noqa: E402
from kernel.autoresearch.host_evidence import DSHHost  # noqa: E402
from kernel.autoresearch.runtime.store import Store  # noqa: E402


# ---------------------------------------------------------------------------
# 合成方案 / 报告
# ---------------------------------------------------------------------------


def make_plan(plan_id: str = "P1", **overrides) -> dict:
    plan = {
        "id": plan_id,
        "goal": "验证一个假设下的整组改动是否改变了那个量",
        "hypothesis": {"id": "H1", "text": "改了 c1 之后 metric 会变大"},
        "change_set": [
            {"id": "c1", "description": "把入口参数收紧到只接受整数", "hypothesis_id": "H1"}
        ],
        "criteria": {
            "metric": "通过题数",
            "direction": "increase",
            "threshold": 1,
            "source": "上一次读数 rules.md §2.1",
        },
        "baseline": {"label": "上一版工作区", "fingerprint": "sha256:baseline"},
        "case_sets": {"target": ["t1", "t2"], "regression": ["r1", "r2"], "control": ["c1"]},
        "previous_passes": ["r1"],
        "branches": {
            "support": {"action": "把 c1 进下一轮，继续同假设"},
            "contradict": {"action": "回退 c1，改假设"},
            "inconclusive": {"action": "先补一轮重复读数，不改配置"},
        },
        "budget": {"max_cost_units": 10, "max_seconds": 600},
        "input_files": ["docs/plan.md"],
    }
    for key, value in overrides.items():
        plan[key] = value
    return plan


def issue(issue_id: str = "AUD-1", *, blocking: bool = True, category: str = "cost") -> dict:
    item = {
        "id": issue_id,
        "category": category,
        "blocking": blocking,
        "problem": "成本偏高",
        "consequence": "多花机时，可能改变是否可跑",
        "suggestion": "减题或换更小的题单",
        "sources": [{"path": "plan.json", "locator": "L1"}],
    }
    if blocking:
        item["why_blocking"] = "预算超限会直接改变这一轮能不能跑"
    return item


def audit_report(prepared_id: str, *, decision: str = "proceed", issues=None,
                 coverage=None, resolves=None) -> dict:
    coverage = dict(coverage or {})
    for category in g.AUDIT_CATEGORIES:
        coverage.setdefault(category, {
            "verdict": "ok",
            "note": f"{category} 已核，没有问题",
            "sources": [{"path": "plan.json", "locator": "L1"}],
        })
    return {
        "prepared_id": prepared_id,
        "coverage": coverage,
        "issues": list(issues or []),
        "resolves": list(resolves or []),
        "decision": decision,
        "decision_reason": "七项都核过；没有会改变研究决定的重大问题",
        "focus": {
            "question_goal": "研究问题与目标相连",
            "design_interpretation": "设计与解释没有重大缺陷",
            "history_constraints": "核过历史证据与实际限制",
        },
    }


# ---------------------------------------------------------------------------
# 合成宿主日志
# ---------------------------------------------------------------------------


class HostLogs:
    """写 DSHHost 能读的会话日志；decoder 注入，不启动 Node.js。"""

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        self.rows: dict[str, list] = {}

    def _write(self, sid: str, rows: list) -> None:
        path = self.root / "sessions" / "project" / sid / "session.v1.jsonl.zstd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic-session-log")
        self.rows[str(path)] = rows

    def user(self, sid: str, command=None, *, at: int, text: str | None = None,
             rpc: str = "rpc-1") -> None:
        body = text if text is not None else json.dumps(command, ensure_ascii=False)
        self._write(sid, [
            {"type": "session", "id": sid, "delegationDepth": 0, "isSeeded": False,
             "createdAt": at},
            {"seq": 0, "type": "user/message", "time": at,
             "data": {"source": {"kind": "user", "rpcId": rpc},
                      "message": {"role": "user",
                                  "content": [{"type": "text", "text": body}]}}},
        ])

    def child(self, sid: str, owner: str, document: dict, *, created: int, at: int,
              provider: str = "spawn", mode: str = "one-shot") -> None:
        self._write(sid, [
            {"type": "session", "id": sid, "parentSession": owner, "origin": "subagent",
             "delegationDepth": 1, "isSeeded": False, "createdAt": created},
            {"seq": 0, "type": "subagent/descriptor", "time": created,
             "data": {"provider": provider, "mode": mode}},
            {"seq": 1, "type": "assistant/message", "time": at,
             "data": {"message": {"role": "assistant",
                                  "content": [{"type": "text", "text": json.dumps(
                                      document, ensure_ascii=False, sort_keys=True)}]}}},
        ])

    def parent(self, owner: str, child: str, prepared_id: str, *, created: int,
               dispatch: int, catalog: bool = True) -> None:
        rows = [
            {"type": "session", "id": owner, "delegationDepth": 0, "isSeeded": False,
             "createdAt": created},
            {"seq": 0, "type": "tool/call", "time": dispatch,
             "data": {"name": "subagent", "callId": "call-1",
                      "arguments": {"prompt": f"请独立审计这份材料 {prepared_id}"}}},
            {"seq": 1, "type": "tool/result", "time": dispatch,
             "data": {"message": {"toolCallId": "call-1",
                                  "content": [{"type": "text",
                                               "text": f"started {child}"}]}}},
        ]
        if catalog:
            rows.append({"seq": 2, "type": "subagent/catalog", "time": dispatch,
                         "data": {"childId": child, "mode": "one-shot"}})
        self._write(owner, rows)

    def host(self, workspace=None) -> DSHHost:
        return DSHHost(root=self.root, decoder=lambda path: self.rows[str(path)],
                       workspace=workspace)


# ---------------------------------------------------------------------------
# 旧式夹具：证明「只返回 verified=True 不够」
# ---------------------------------------------------------------------------


class NoTimeVerifier:
    """旧式假宿主：说核过了，但不给宿主动词时间 / 会话号 —— 不可判，必须被拒。"""

    def verify(self, kind, payload):
        return {"verified": True, "kind": kind, "command": payload.get("expected")}


class NoVerifierResult:
    def verify(self, kind, payload):
        return None


class TamperedHost:
    """包真 DSHost，但把返回字段改掉：证明治理层不盲信宿主结果的形状。"""

    def __init__(self, inner, **override):
        self.inner = inner
        self.override = override

    def verify(self, kind, payload):
        result = self.inner.verify(kind, payload)
        if not isinstance(result, dict):
            return result
        merged = dict(result)
        merged.update(self.override)
        return merged


# ---------------------------------------------------------------------------
# 基座
# ---------------------------------------------------------------------------


class HostCase(unittest.TestCase):
    owner = "session-main"
    auditor = "session-reviewer"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.db = str(self.dir / "gov.sqlite")
        self.store = Store(self.db)
        self.logs = HostLogs(self.dir / "host")
        self.host = self.logs.host()
        self.gov = g.Governance(self.store, verifier=self.host)
        self.gov.freeze_plan(make_plan(), actor=self.owner)

    def new_gov(self, name: str, **kwargs) -> tuple[str, g.Governance]:
        db = str(self.dir / f"{name}.sqlite")
        return db, g.Governance(Store(db), verifier=kwargs.get("verifier", self.host))

    def prepare(self, plan_id: str = "P1", gov=None, owner: str | None = None) -> str:
        gov = gov or self.gov
        return gov.prepare_audit(plan_id, actor=self.owner, owner=owner or self.owner)["prepared_id"]

    def record(self, prepared_id: str, *, report=None, actor: str | None = None,
               evidence=None, gov=None) -> dict:
        gov = gov or self.gov
        return gov.record_audit(prepared_id, report or audit_report(prepared_id),
                                actor=actor or self.auditor, evidence=evidence)

    def soon_ms(self, seconds: float = 0.2) -> int:
        """用户消息时间：在 prepare / 记录之后取的「现在 + 0.2 秒」。

        不伪造未来：真实宿主消息就是刚发生的，只要求它晚于最后一次 prepare / 报告。
        """
        return int(time.time() * 1000) + int(seconds * 1000)

    def future_ms(self, seconds: int = 1) -> int:
        """子会话创建时间（独立性核验用；不参与用户命令的「不得在未来」判定）。"""
        return int((time.time() + seconds) * 1000)

    def past_ms(self, seconds: int = 3600) -> int:
        return int((time.time() - seconds) * 1000)


# ---------------------------------------------------------------------------
# K2 / K3：关闭审计
# ---------------------------------------------------------------------------


class PolicyOffTests(HostCase):
    def _close(self, evidence, *, scope="plan", target="P1", gov=None):
        return (gov or self.gov).set_policy(scope=scope, target=target, mode="off",
                                            actor=self.owner, evidence=evidence)

    def test_close_uses_host_command_time_and_blocks_prepare(self):
        self.prepare()
        expected = self.gov.policy_command(scope="plan", target="P1", mode="off")
        self.logs.user("session-user", expected, at=self.soon_ms())
        out = self._close({"session_id": "session-user", "seq": 0})
        self.assertEqual(out["independence"], "verified")
        self.assertEqual(out["expected"], expected)
        self.assertEqual(self.gov.audit_policy("P1")["mode"], "off")
        with self.assertRaises(g.GovernanceError):
            self.prepare()

    def test_another_action_command_cannot_close_audit(self):
        self.prepare()
        unrelated = {"action": "plan_audit_extend", "plan_id": "P1", "used": 0, "limit": 2}
        self.logs.user("session-user", unrelated, at=self.soon_ms())
        with self.assertRaises(g.GovernanceError) as ctx:
            self._close({"session_id": "session-user", "seq": 0})
        self.assertIn("逐键相等", str(ctx.exception))

    def test_another_plan_command_cannot_close_this_plan(self):
        self.prepare()
        other_plan = self.gov.policy_command(scope="plan", target="P2", mode="off")
        self.logs.user("session-user", other_plan, at=self.soon_ms())
        with self.assertRaises(g.GovernanceError):
            self._close({"session_id": "session-user", "seq": 0})

    def test_caller_cannot_choose_expected_or_time(self):
        self.prepare()
        expected = self.gov.policy_command(scope="plan", target="P1", mode="off")
        for extra in ({"expected": expected}, {"created_at": 9e9},
                      {"quote": "以后不用审"}):
            evidence = {"session_id": "session-user", "seq": 0, **extra}
            with self.assertRaises(g.GovernanceError) as ctx:
                self._close(evidence)
            text = str(ctx.exception)
            self.assertTrue("只能含" in text or "proof" in text, text)

    def test_stale_user_message_before_prepare_is_rejected(self):
        self.prepare()
        expected = self.gov.policy_command(scope="plan", target="P1", mode="off")
        self.logs.user("session-user", expected, at=self.past_ms())
        with self.assertRaises(g.GovernanceError) as ctx:
            self._close({"session_id": "session-user", "seq": 0})
        self.assertIn("早于", str(ctx.exception))

    def test_missing_host_time_is_rejected(self):
        self.prepare()
        gov = g.Governance(self.store, verifier=NoTimeVerifier())
        expected = gov.policy_command(scope="plan", target="P1", mode="off")
        with self.assertRaises(g.GovernanceError) as ctx:
            self._close({"session_id": "session-user", "seq": 0}, gov=gov)
        self.assertIn("created_at", str(ctx.exception))

    def test_no_verifier_cannot_close(self):
        self.prepare()
        gov = g.Governance(self.store)
        with self.assertRaises(g.GovernanceError) as ctx:
            self._close({"session_id": "session-user", "seq": 0}, gov=gov)
        self.assertIn("核验器", str(ctx.exception))

    def test_same_user_message_cannot_close_twice(self):
        self.prepare()
        expected = self.gov.policy_command(scope="plan", target="P1", mode="off")
        self.logs.user("session-user", expected, at=self.soon_ms())
        self._close({"session_id": "session-user", "seq": 0})
        with self.assertRaises(g.GovernanceError) as ctx:
            self._close({"session_id": "session-user", "seq": 0})
        self.assertIn("已经用于关闭", str(ctx.exception))

    def test_host_must_echo_the_verified_command(self):
        self.prepare()
        expected = self.gov.policy_command(scope="plan", target="P1", mode="off")
        self.logs.user("session-user", expected, at=self.soon_ms())
        for override in ({"command": None}, {"command": dict(expected, target="P2")}):
            tampered = g.Governance(self.store, verifier=TamperedHost(self.host, **override))
            with self.assertRaises(g.GovernanceError) as ctx:
                self._close({"session_id": "session-user", "seq": 0}, gov=tampered)
            self.assertRegex(str(ctx.exception), "command|命令")

    def test_host_session_and_seq_must_match_proof(self):
        self.prepare()
        expected = self.gov.policy_command(scope="plan", target="P1", mode="off")
        self.logs.user("session-user", expected, at=self.soon_ms())
        for override, needle in (({"session_id": "session-other"}, "会话"),
                                 ({"session_id": None}, "session_id"),
                                 ({"seq": 99}, "seq"),
                                 ({"seq": "0"}, "seq")):
            tampered = g.Governance(self.store, verifier=TamperedHost(self.host, **override))
            with self.assertRaises(g.GovernanceError) as ctx:
                self._close({"session_id": "session-user", "seq": 0}, gov=tampered)
            self.assertIn(needle, str(ctx.exception))

    def test_future_host_message_is_rejected(self):
        self.prepare()
        expected = self.gov.policy_command(scope="plan", target="P1", mode="off")
        self.logs.user("session-user", expected, at=int((time.time() + 3600) * 1000))
        with self.assertRaises(g.GovernanceError) as ctx:
            self._close({"session_id": "session-user", "seq": 0})
        self.assertIn("未来", str(ctx.exception))

    def test_service_layer_constructs_full_policy_payload(self):
        """真实 DSHHost 正例 + 记录 payload：用户命令的 expected 由本层现算，调用方只给 proof。"""
        recorder = RecordingHost(self.host)
        gov = g.Governance(self.store, verifier=recorder)
        gov.prepare_audit("P1", actor=self.owner)
        expected = gov.policy_command(scope="plan", target="P1", mode="off")
        self.logs.user("session-user", expected, at=self.soon_ms())
        out = gov.set_policy(scope="plan", target="P1", mode="off", actor=self.owner,
                             evidence={"session_id": "session-user", "seq": 0})
        self.assertEqual(out["independence"], "verified")
        kind, payload = recorder.calls[-1]
        self.assertEqual(kind, "policy_off")
        self.assertEqual(set(payload), {"proof", "expected"})
        self.assertEqual(payload["proof"], {"session_id": "session-user", "seq": 0})
        self.assertEqual(payload["expected"], expected)
        self.assertEqual(payload["expected"]["action"], "plan_audit_policy")

    def test_off_record_without_host_time_does_not_apply(self):
        """历史库里一条没有宿主时间的 off 不能免审（审计政策回到 required）。"""
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO gov_policy(scope,target,mode,actor,evidence_json,independence,created_at)"
                " VALUES('plan','P1','off','legacy','{}','verified',0)")
        self.assertEqual(self.gov.audit_policy("P1")["mode"], "required")
        self.prepare()  # 仍然可以准备：off 不生效


# ---------------------------------------------------------------------------
# K8：waive
# ---------------------------------------------------------------------------


class WaiveTests(HostCase):
    def _blocking_report(self):
        prepared = self.prepare()
        report = audit_report(prepared, decision="blocked", issues=[issue("AUD-1")])
        return self.record(prepared, report=report)

    def test_waive_uses_constructed_command_and_report_time(self):
        recorded = self._blocking_report()
        expected = self.gov.waive_command(recorded["report_id"], "AUD-1")
        self.assertEqual(expected["action"], g.WAIVE_COMMAND)
        self.assertEqual(expected["key_fingerprint"], recorded["key_fingerprint"])
        self.logs.user("session-user", expected, at=self.soon_ms())
        out = self.gov.waive(recorded["report_id"], "AUD-1", reason="人同意多花",
                             evidence={"session_id": "session-user", "seq": 0},
                             actor=self.owner)
        self.assertEqual(out["independence"], "verified")
        self.assertTrue(self.gov.audit_state("P1")["ok"])

    def test_waiver_command_for_another_issue_cannot_close_this_one(self):
        recorded = self._blocking_report()
        other = dict(self.gov.waive_command(recorded["report_id"], "AUD-1"))
        other["issue_id"] = "AUD-other"
        self.logs.user("session-user", other, at=self.soon_ms())
        with self.assertRaises(g.GovernanceError):
            self.gov.waive(recorded["report_id"], "AUD-1", reason="x",
                           evidence={"session_id": "session-user", "seq": 0},
                           actor=self.owner)

    def test_user_message_before_report_cannot_waive(self):
        # 先生成报告，再构造一条「早于报告」的用户消息。
        prepared = self.prepare()
        report = audit_report(prepared, decision="blocked", issues=[issue("AUD-1")])
        recorded = self.record(prepared, report=report)
        expected = self.gov.waive_command(recorded["report_id"], "AUD-1")
        self.logs.user("session-user", expected, at=self.past_ms())
        with self.assertRaises(g.GovernanceError) as ctx:
            self.gov.waive(recorded["report_id"], "AUD-1", reason="x",
                           evidence={"session_id": "session-user", "seq": 0},
                           actor=self.owner)
        self.assertIn("早于", str(ctx.exception))

    def test_waiver_does_not_survive_key_change(self):
        recorded = self._blocking_report()
        expected = self.gov.waive_command(recorded["report_id"], "AUD-1")
        self.logs.user("session-user", expected, at=self.soon_ms())
        self.gov.freeze_plan(make_plan(budget={"max_cost_units": 99, "max_seconds": 600}),
                             actor=self.owner)
        with self.assertRaises(g.GovernanceError) as ctx:
            self.gov.waive(recorded["report_id"], "AUD-1", reason="x",
                           evidence={"session_id": "session-user", "seq": 0},
                           actor=self.owner)
        self.assertIn("变过", str(ctx.exception))


# ---------------------------------------------------------------------------
# K4：独立性
# ---------------------------------------------------------------------------


class RecordingHost:
    """包一层真 DSHHost：先记下治理层交给宿主的 payload，再交给真宿主核。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def verify(self, kind, payload):
        self.calls.append((kind, payload))
        return self.inner.verify(kind, payload)


class IndependenceTests(HostCase):
    def setUp(self):
        super().setUp()
        self.prepared = self.prepare()
        self.report = audit_report(self.prepared)
        created = self.future_ms(30)
        self.logs.parent(self.owner, self.auditor, self.prepared,
                         created=created, dispatch=created - 1000)
        self.logs.child(self.auditor, self.owner, self.report,
                        created=created, at=created + 1000)

    def test_spawned_child_report_binds_owner_actor_and_material(self):
        out = self.record(self.prepared, evidence={"session_id": self.auditor, "seq": 1})
        self.assertEqual(out["independence"], "verified")
        state = self.gov.audit_state("P1")
        self.assertTrue(state["ok"], state["problems"])
        self.assertTrue(state["audited"])

    def test_actor_must_equal_host_verified_session(self):
        with self.assertRaises(g.GovernanceError) as ctx:
            self.record(self.prepared, actor="session-other",
                        evidence={"session_id": self.auditor, "seq": 1})
        self.assertIn("不一致", str(ctx.exception))

    def strict(self) -> g.Governance:
        """要求 verified 独立性的只读口径：declared 审计在这里不算通过。"""
        return g.Governance(self.store, verifier=self.host,
                            policy={"require_verified_independence": True})

    def test_forked_child_is_not_independent(self):
        created = self.future_ms(30)
        self.logs.child(self.auditor, self.owner, self.report,
                        created=created, at=created + 1000, provider="fork")
        out = self.record(self.prepared, evidence={"session_id": self.auditor, "seq": 1})
        self.assertEqual(out["independence"], "declared")
        self.assertFalse(self.strict().audit_state("P1")["ok"])

    def test_dispatch_must_name_this_prepared_material(self):
        created = self.future_ms(30)
        self.logs.parent(self.owner, self.auditor, "PREP-someone-else",
                         created=created, dispatch=created - 1000)
        out = self.record(self.prepared, evidence={"session_id": self.auditor, "seq": 1})
        self.assertEqual(out["independence"], "declared")
        self.assertFalse(self.strict().audit_state("P1")["ok"])

    def test_child_message_must_equal_submitted_report(self):
        created = self.future_ms(30)
        other = audit_report(self.prepared)
        other["decision_reason"] = "另一份报告"
        self.logs.child(self.auditor, self.owner, other,
                        created=created, at=created + 1000)
        out = self.record(self.prepared, evidence={"session_id": self.auditor, "seq": 1})
        self.assertEqual(out["independence"], "declared")
        self.assertFalse(self.strict().audit_state("P1")["ok"])

    def test_caller_supplied_expected_cannot_fake_independence(self):
        evidence = {"session_id": self.auditor, "seq": 1, "expected": self.report}
        out = self.record(self.prepared, evidence=evidence)
        self.assertEqual(out["independence"], "declared")
        self.assertFalse(self.strict().audit_state("P1")["ok"])

    def test_without_evidence_independence_stays_declared(self):
        out = self.record(self.prepared)
        self.assertEqual(out["independence"], "declared")

    def test_service_layer_constructs_full_independence_payload(self):
        """真实 DSHHost 正例 + 记录 payload：proof / owner / prepared / 完整 report 都由本层构造。"""
        recorder = RecordingHost(self.host)
        gov = g.Governance(self.store, verifier=recorder)
        prepared = gov.prepare_audit("P1", actor=self.owner)["prepared_id"]
        report = audit_report(prepared)
        created = self.future_ms(30)
        self.logs.parent(self.owner, self.auditor, prepared,
                         created=created, dispatch=created - 1000)
        self.logs.child(self.auditor, self.owner, report,
                        created=created, at=created + 1000)
        out = gov.record_audit(prepared, report, actor=self.auditor,
                               evidence={"session_id": self.auditor, "seq": 1})
        self.assertEqual(out["independence"], "verified")
        kind, payload = recorder.calls[-1]
        self.assertEqual(kind, "audit_independence")
        self.assertEqual(set(payload), {"proof", "owner_session", "prepared_id", "report"})
        self.assertEqual(payload["proof"], {"session_id": self.auditor, "seq": 1})
        self.assertEqual(payload["owner_session"], self.owner)
        self.assertEqual(payload["prepared_id"], prepared)
        self.assertEqual(payload["report"], report)  # 完整报告，不是摘要或 id


# ---------------------------------------------------------------------------
# K14：报告文件 + 六键短声明
# ---------------------------------------------------------------------------


class ReportFileTests(HostCase):
    def _write(self, report: dict) -> tuple[pathlib.Path, dict]:
        path = self.dir / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        declaration = {
            "prepared_id": report["prepared_id"],
            "decision": report["decision"],
            "decision_reason": report["decision_reason"],
            "report_file": str(path),
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "issues": [{"id": item["id"], "blocking": item["blocking"]}
                       for item in report["issues"]],
        }
        return path, declaration

    def test_declaration_is_host_verified_and_file_sha_bound(self):
        prepared = self.prepare()
        report = audit_report(prepared)
        path, declaration = self._write(report)
        created = self.future_ms(30)
        self.logs.parent(self.owner, self.auditor, prepared,
                         created=created, dispatch=created - 1000)
        self.logs.child(self.auditor, self.owner, declaration,
                        created=created, at=created + 1000)
        out = self.gov.record_audit_file(prepared, str(path), declaration, actor=self.auditor,
                                         evidence={"session_id": self.auditor, "seq": 1})
        self.assertEqual(out["independence"], "verified")
        self.assertTrue(self.gov.audit_state("P1")["ok"])

    def test_modified_report_file_is_rejected(self):
        prepared = self.prepare()
        report = audit_report(prepared)
        path, declaration = self._write(report)
        report["decision_reason"] = "偷偷改一句"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(g.GovernanceError) as ctx:
            self.gov.record_audit_file(prepared, str(path), declaration,
                                       actor=self.auditor)
        self.assertIn("不一致", str(ctx.exception))

    def test_declaration_with_wrong_sha_is_rejected(self):
        prepared = self.prepare()
        report = audit_report(prepared)
        path, declaration = self._write(report)
        declaration["report_sha256"] = "0" * 64
        with self.assertRaises(g.GovernanceError):
            self.gov.record_audit_file(prepared, str(path), declaration,
                                       actor=self.auditor)

    def test_declaration_message_for_another_file_stays_declared(self):
        prepared = self.prepare()
        report = audit_report(prepared)
        path, declaration = self._write(report)
        created = self.future_ms(30)
        self.logs.parent(self.owner, self.auditor, prepared,
                         created=created, dispatch=created - 1000)
        forged = dict(declaration, report_file="other/report.json")
        self.logs.child(self.auditor, self.owner, forged,
                        created=created, at=created + 1000)
        out = self.gov.record_audit_file(prepared, str(path), declaration,
                                         actor=self.auditor,
                                         evidence={"session_id": self.auditor, "seq": 1})
        self.assertEqual(out["independence"], "declared")


# ---------------------------------------------------------------------------
# K12：可信 extend
# ---------------------------------------------------------------------------


class ExtendTests(HostCase):
    def _exhaust(self, count: int = 2) -> None:
        for index in range(count):
            prepared = self.prepare()
            self.record(prepared, actor=f"session-reviewer-{index}")

    def test_extend_requires_user_command_and_cannot_self_expand(self):
        self._exhaust()
        with self.assertRaises(g.GovernanceError) as ctx:
            self.prepare()
        self.assertIn("上限", str(ctx.exception))

        # 未耗尽前不能预加额度
        fresh = self.new_gov("fresh")[1]
        fresh.freeze_plan(make_plan(), actor=self.owner)
        with self.assertRaises(g.GovernanceError) as ctx:
            fresh.extend_command("P1")
        self.assertIn("还没用满", str(ctx.exception))

        command = self.gov.extend_command("P1")
        self.assertEqual((command["used"], command["limit"]), (2, 2))
        self.logs.user("session-user", command, at=self.soon_ms())
        out = self.gov.extend_audit("P1", evidence={"session_id": "session-user", "seq": 0},
                                    actor=self.owner)
        self.assertEqual(out["limit"], 3)
        self.assertEqual(out["extensions"], 1)
        third = self.prepare()
        self.record(third, actor="session-reviewer-2")

        # 旧命令重放：额度已变，对不上
        with self.assertRaises(g.GovernanceError):
            self.gov.extend_audit("P1", evidence={"session_id": "session-user", "seq": 0},
                                  actor=self.owner)
        self.logs.user("session-user", command, at=self.soon_ms(), rpc="rpc-2")
        with self.assertRaises(g.GovernanceError):
            self.gov.extend_audit("P1", evidence={"session_id": "session-user", "seq": 0},
                                  actor=self.owner)

        second = self.gov.extend_command("P1")
        self.assertEqual((second["used"], second["limit"]), (3, 3))
        self.logs.user("session-user-2", second, at=self.soon_ms(), rpc="rpc-3")
        out = self.gov.extend_audit("P1", evidence={"session_id": "session-user-2", "seq": 0},
                                    actor=self.owner)
        self.assertEqual(out["limit"], 4)
        self.assertEqual(out["extensions"], 2)
        fourth = self.prepare()
        self.record(fourth, actor="session-reviewer-3")

        with self.assertRaises(g.GovernanceError):
            self.prepare()
        # 扩额没有固定次数上限：用户再明确授权一条新命令就能再加一次（每次 +1）。
        third_command = self.gov.extend_command("P1")
        self.assertEqual((third_command["used"], third_command["limit"]), (4, 4))
        self.assertEqual(self.gov.audit_budget("P1")["can_extend"], True)
        self.logs.user("session-user-3", third_command, at=self.soon_ms(), rpc="rpc-4")
        out = self.gov.extend_audit("P1", evidence={"session_id": "session-user-3", "seq": 0},
                                    actor=self.owner)
        self.assertEqual((out["extensions"], out["limit"]), (3, 5))
        fifth = self.prepare()
        self.record(fifth, actor="session-reviewer-4")
        self.assertEqual(self.gov.audit_budget("P1")["limit"], 5)
        # 但旧命令在新额度下仍然对不上（重放保护不因没有固定上限而放松）
        with self.assertRaises(g.GovernanceError):
            self.gov.extend_audit("P1", evidence={"session_id": "session-user-2", "seq": 0},
                                  actor=self.owner)

    def test_untrusted_extend_cannot_raise_limit(self):
        self._exhaust()
        # 没有证据
        with self.assertRaises(g.GovernanceError):
            self.gov.extend_audit("P1", evidence=None, actor=self.owner)
        # 没有核验器
        gov = g.Governance(self.store)
        self.logs.user("session-user", gov.extend_command("P1"), at=self.soon_ms())
        with self.assertRaises(g.GovernanceError) as ctx:
            gov.extend_audit("P1", evidence={"session_id": "session-user", "seq": 0},
                             actor=self.owner)
        self.assertIn("核验器", str(ctx.exception))
        # 宿主说核过但没给时间
        gov = g.Governance(self.store, verifier=NoTimeVerifier())
        with self.assertRaises(g.GovernanceError) as ctx:
            gov.extend_audit("P1", evidence={"session_id": "session-user", "seq": 0},
                             actor=self.owner)
        self.assertIn("created_at", str(ctx.exception))
        self.assertEqual(self.gov.audit_budget("P1")["limit"], 2)

    def test_extend_message_before_last_audit_is_rejected(self):
        self._exhaust()
        command = self.gov.extend_command("P1")
        self.logs.user("session-user", command, at=self.past_ms())
        with self.assertRaises(g.GovernanceError) as ctx:
            self.gov.extend_audit("P1", evidence={"session_id": "session-user", "seq": 0},
                                  actor=self.owner)
        self.assertIn("早于", str(ctx.exception))


# ---------------------------------------------------------------------------
# K11：历史豁免
# ---------------------------------------------------------------------------


def create_runs(db: str, rows) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS wf_runs("
                     "run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, started_at REAL NOT NULL)")
        for run_id, plan_id, started in rows:
            conn.execute("INSERT INTO wf_runs(run_id,plan_id,started_at) VALUES(?,?,?)",
                         (run_id, plan_id, started))
        conn.commit()
    finally:
        conn.close()


class LegacyTests(HostCase):
    def test_real_historical_start_is_exempt_but_never_audited(self):
        db, gov = self.new_gov("real")
        create_runs(db, [("RUN-1", "P1", time.time() - 86400)])
        gov.freeze_plan(make_plan(), actor=self.owner)
        state = gov.legacy_state("P1")
        self.assertTrue(state["available"])
        self.assertFalse(state["legacy_exempt"])  # 还没登记
        out = gov.legacy_exempt("P1", actor=self.owner, reason="规则生效前已起跑")
        self.assertTrue(out["legacy_exempt"])
        self.assertFalse(out["audited"])
        self.assertIn("未审计", out["label"])
        audit = gov.audit_state("P1")
        self.assertTrue(audit["ok"], audit["problems"])
        self.assertFalse(audit["audited"])
        self.assertTrue(audit["legacy_exempt"])
        self.assertIsNone(audit["report_id"])

    def test_new_round_started_after_governance_cannot_be_exempt(self):
        db, gov = self.new_gov("new")
        gov.freeze_plan(make_plan(), actor=self.owner)
        create_runs(db, [("RUN-1", "P1", time.time())])
        with self.assertRaises(g.GovernanceError) as ctx:
            gov.legacy_exempt("P1", actor=self.owner, reason="想免审")
        self.assertIn("不能豁免", str(ctx.exception))
        self.assertIn("legacy_new_round", getattr(ctx.exception, "errors", []))

    def test_missing_history_fails_closed_with_unimplemented(self):
        _, gov = self.new_gov("nohist")
        gov.freeze_plan(make_plan(), actor=self.owner)
        with self.assertRaises(g.GovernanceError) as ctx:
            gov.legacy_exempt("P1", actor=self.owner, reason="没有记录")
        self.assertIn("unimplemented", getattr(ctx.exception, "errors", []))
        self.assertFalse(gov.legacy_state("P1")["legacy_exempt"])

    def test_later_run_revokes_legacy_exemption(self):
        db, gov = self.new_gov("revoke")
        create_runs(db, [("RUN-1", "P1", time.time() - 86400)])
        gov.freeze_plan(make_plan(), actor=self.owner)
        gov.legacy_exempt("P1", actor=self.owner, reason="历史轮次")
        self.assertTrue(gov.legacy_state("P1")["legacy_exempt"])
        create_runs(db, [("RUN-2", "P1", time.time())])
        state = gov.legacy_state("P1")
        self.assertFalse(state["legacy_exempt"])
        self.assertFalse(gov.audit_state("P1")["ok"])

    def test_cli_has_no_legacy_exempt_bypass(self):
        db, gov = self.new_gov("cli")
        create_runs(db, [("RUN-1", "P1", time.time() - 86400)])
        gov.freeze_plan(make_plan(), actor=self.owner)
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli(["--db", db, "--commit", "legacy-exempt", "--plan-id", "P1"])
        self.assertEqual(ctx.exception.code, 2)
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli(["--db", db, "--commit", "policy", "--scope", "plan",
                     "--target", "P1", "--mode", "off", "--legacy-exempt"])
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# L1 / L13：check_start 不给假绿
# ---------------------------------------------------------------------------


class CheckStartTests(HostCase):
    def _closed_intent_with_trigger(self) -> dict:
        self.gov.create_intent("I1", quote="直到那两道题也能过", source_session="session-1",
                               seq=12, actor=self.owner)
        trigger = self.gov.register_trigger("I1", "result_analysis", "第 3 轮读数收回",
                                            actor=self.owner)
        report = self.gov.record_monitor_report(
            "I1", {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12},
                   "verdict": "achieved", "basis": "两道题逐题核对通过"}, actor="monitor")
        self.gov.close_intent("I1", reason="achieved", evidence={"results": "results.jsonl"},
                              report_id=report["report_id"], actor=self.owner)
        return report

    def test_closed_intent_still_blocks_unassessed_user_quote(self):
        self._closed_intent_with_trigger()
        self.assertTrue(self.gov.check_start(intent_id="I1")["allow"])
        self.gov.sync_user_update(source_session="session-1", seq=20, quote="再盯一下那 5 题")
        state = self.gov.check_start(intent_id="I1")
        self.assertFalse(state["allow"])
        self.assertIn("未评估", " ".join(state["problems"]))

    def test_no_intent_and_no_source_is_not_green(self):
        state = self.gov.check_start()
        self.assertFalse(state["allow"])
        self.assertIn("没有来源会话", " ".join(state["problems"]))

    def test_unregistered_source_is_not_green(self):
        state = self.gov.check_start(source_session="session-ghost")
        self.assertFalse(state["allow"])
        self.assertIn("未登记", " ".join(state["problems"]))
        self.assertFalse(state["source_registered"])

    def test_registered_source_after_assessment_is_green(self):
        self.gov.sync_user_update(source_session="session-1", seq=20, quote="再盯一下那 5 题")
        self.assertFalse(self.gov.check_start(source_session="session-1")["allow"])
        self.gov.assess_update(source_session="session-1", covers_seq=20, decision="none",
                               quote="再盯一下那 5 题", actor=self.owner, answer_seq=21)
        self.assertTrue(self.gov.check_start(source_session="session-1")["allow"])

    def test_source_must_match_the_intent(self):
        self.gov.create_intent("I1", quote="保持回归", source_session="session-1", seq=12,
                               actor=self.owner)
        state = self.gov.check_start(intent_id="I1", source_session="session-other")
        self.assertFalse(state["allow"])
        self.assertIn("不一致", " ".join(state["problems"]))


# ---------------------------------------------------------------------------
# CLI：默认 DSHHost、workspace、无证据维持 declared、精确 help
# ---------------------------------------------------------------------------


class CliTests(HostCase):
    def _workspace(self) -> tuple[pathlib.Path, str, g.Governance]:
        ws = self.dir / "ws"
        (ws / ".autoresearch").mkdir(parents=True)
        db = str(ws / ".autoresearch" / "runtime.sqlite3")
        gov = g.Governance(Store(db))
        gov.freeze_plan(make_plan(), actor=self.owner)
        gov.prepare_audit("P1", actor=self.owner)
        return ws, db, gov

    def test_cli_injects_dsh_host_with_workspace_from_db(self):
        ws, db, gov = self._workspace()
        expected = gov.policy_command(scope="plan", target="P1", mode="off")
        logs = HostLogs(self.dir / "cli-host")
        logs.user("session-user", expected, at=self.soon_ms())
        seen = {}

        def factory(*args, **kwargs):
            seen.update(kwargs)
            return logs.host(workspace=kwargs.get("workspace"))

        proof = self.dir / "proof.json"
        proof.write_text(json.dumps({"session_id": "session-user", "seq": 0}),
                         encoding="utf-8")
        out = io.StringIO()
        with mock.patch("kernel.autoresearch.host_evidence.DSHHost", side_effect=factory), \
                contextlib.redirect_stdout(out):
            code = cli(["--db", db, "--commit", "--actor", self.owner, "policy",
                        "--scope", "plan", "--target", "P1", "--mode", "off",
                        "--proof", str(proof)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["independence"], "verified")
        self.assertEqual(pathlib.Path(seen["workspace"]).resolve(), ws.resolve())

    def test_cli_explicit_workspace_wins(self):
        ws, db, _ = self._workspace()
        other = self.dir / "other-ws"
        other.mkdir()
        seen = {}
        logs = HostLogs(self.dir / "cli-host-2")

        def factory(*args, **kwargs):
            seen.update(kwargs)
            return logs.host(workspace=kwargs.get("workspace"))

        with mock.patch("kernel.autoresearch.host_evidence.DSHHost", side_effect=factory), \
                contextlib.redirect_stdout(io.StringIO()):
            cli(["--db", db, "--workspace", str(other), "--commit", "audit-prepare",
                 "--id", "P1"])
        self.assertEqual(pathlib.Path(seen["workspace"]).resolve(), other.resolve())

    def test_cli_without_proof_keeps_declared(self):
        ws, db, gov = self._workspace()
        prepared = self._latest_prepared(db)
        report = audit_report(prepared)
        path = self.dir / "cli-report.json"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        declaration = {
            "prepared_id": prepared,
            "decision": report["decision"],
            "decision_reason": report["decision_reason"],
            "report_file": str(path),
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "issues": [],
        }
        dec_path = self.dir / "cli-declaration.json"
        dec_path.write_text(json.dumps(declaration, ensure_ascii=False), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli(["--db", db, "--commit", "--actor", "session-reviewer",
                        "audit-file", "--prepared-id", prepared,
                        "--report", str(path), "--declaration", str(dec_path)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["independence"], "declared")

    @staticmethod
    def _latest_prepared(db: str) -> str:
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT prepared_id FROM gov_audits").fetchone()[0]
        finally:
            conn.close()

    def test_cli_show_expected_does_not_write(self):
        ws, db, gov = self._workspace()
        before = pathlib.Path(db).read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli(["--db", db, "policy", "--scope", "plan", "--target", "P1",
                        "--mode", "off", "--show-expected"])
        self.assertEqual(code, 0, out.getvalue())
        expected = json.loads(out.getvalue())["expected"]
        self.assertEqual(expected, gov.policy_command(scope="plan", target="P1", mode="off"))
        self.assertEqual(before, pathlib.Path(db).read_bytes())

    def test_controlled_commands_have_precise_help(self):
        cases = [("policy", ("--proof", "--scope", "--mode")),
                 ("audit-waive", ("--report-id", "--issue-id", "--proof")),
                 ("audit-extend", ("--plan-id", "--proof", "--show-expected")),
                 ("audit-file", ("--declaration", "--report", "--prepared-id"))]
        for command, needles in cases:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    cli([command, "--help"])
            self.assertEqual(ctx.exception.code, 0)
            text = out.getvalue()
            for needle in needles:
                self.assertIn(needle, text, f"{command} 的 help 缺 {needle}")


# ---------------------------------------------------------------------------
# K9：authorization 是实质授权，不是措辞
# ---------------------------------------------------------------------------


class AuthorizationFingerprintTests(HostCase):
    def test_authorization_is_a_key_field_but_optional(self):
        self.assertIn("authorization", g.KEY_FIELDS)
        self.assertNotIn("authorization", g.REQUIRED_KEY_FIELDS)
        self.assertEqual(g.validate_plan(make_plan()), [])
        auth = dict(make_plan(), authorization={"scope": "small", "series_id": "S1",
                                                "reservation_id": "R1"})
        self.assertEqual(g.validate_plan(auth), [])
        fingerprint = g.plan_fingerprint(auth)
        for key, value in (("scope", "large"), ("series_id", "S2"),
                           ("reservation_id", "R2"), ("decision_id", "D1")):
            changed = dict(auth, authorization=dict(auth["authorization"], **{key: value}))
            self.assertNotEqual(g.plan_fingerprint(changed), fingerprint, key)
        self.assertEqual(g.plan_fingerprint(dict(auth, notes="换个说法")), fingerprint)

    def test_authorization_cannot_be_granted_as_minor_fix(self):
        prepared = self.prepare()
        report = audit_report(prepared, decision="minor_fix",
                              issues=[issue("AUD-1", blocking=False)])
        report["minor_fix_paths"] = [{"path": "authorization.scope", "issue_id": "AUD-1",
                                      "why": "把作用域从小样本调到大规模"}]
        with self.assertRaises(g.GovernanceError) as ctx:
            self.record(prepared, report=report)
        self.assertIn("硬实质", str(ctx.exception))
        # 对照：可授权的普通证据路径仍然允许（别把整条小修通道关死）
        allowed = audit_report(prepared, decision="minor_fix",
                               issues=[issue("AUD-1", blocking=False)])
        allowed["minor_fix_paths"] = [{"path": "input_files.0", "issue_id": "AUD-1",
                                       "why": "证据路径笔误"}]
        out = self.record(prepared, report=allowed)
        self.assertEqual(out["blocking_open"], [])


# ---------------------------------------------------------------------------
# 只读旧库兼容：新表缺失视为 0 次扩额，且读命令不建表、不写库
# ---------------------------------------------------------------------------


class ReadOnlyCompatTests(HostCase):
    def _old_db(self, name: str) -> str:
        db = str(self.dir / name)
        writer = g.Governance(Store(db))
        writer.freeze_plan(make_plan(), actor=self.owner)
        writer.prepare_audit("P1", actor=self.owner)
        conn = sqlite3.connect(db)
        try:
            conn.execute("DROP TABLE gov_audit_extensions")
            conn.execute("DROP TABLE gov_legacy_exemptions")
            conn.commit()
        finally:
            conn.close()
        return db

    def test_old_database_without_new_tables_is_readable(self):
        from kernel.autoresearch.governance_cli import _read_governance
        db = self._old_db("old.sqlite")
        before = pathlib.Path(db).read_bytes()
        github = _read_governance(db)  # 只读连接：不能建表，也不能写库
        state = github.audit_state("P1")
        self.assertEqual(state["extensions"], 0)
        self.assertEqual(state["limit"], 2)
        self.assertFalse(github.legacy_state("P1")["legacy_exempt"])
        self.assertFalse(state["ok"])  # 没审计仍然红
        self.assertEqual(before, pathlib.Path(db).read_bytes())

    def test_writing_instance_initializes_new_tables(self):
        db = self._old_db("migrate.sqlite")
        # 写库入口会重新初始化 schema（幂等 CREATE IF NOT EXISTS），随后旧库可继续用新功能。
        again = g.Governance(Store(db), verifier=self.host)
        budget = again.audit_budget("P1")
        self.assertEqual((budget["used"], budget["limit"], budget["extensions"]), (1, 2, 0))
        self.assertEqual(again.legacy_state("P1")["legacy_exempt"], False)


if __name__ == "__main__":
    unittest.main()
