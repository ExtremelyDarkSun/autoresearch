# GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
# LAST-EDIT: portable framework synchronization, 2026-11-09
# PURPOSE: 治理层的单元测试 —— 重点覆盖拒绝路径（方案不合规、审计次数上限、关闭审计要证据、意图闭合四理由、
#          经验修订使旧确认失效、委派哈希不符、冷件缺索引、append-only 被破坏）
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
"""治理层测试。

三条约束：

1. **只用标准库**（unittest / json / tempfile / pathlib / sqlite3）。
2. **全部合成数据**：方案号 `P1`、假设 `H1`、题号 `t1`/`r1`/`c1`，不引用任何真实实验与读数。
3. **不伪造授权**：需要 `verified` 的路径用注入的 `FakeVerifier` 证明「只有注入 verifier 才能核过」；
   没有 verifier 时一律只能 `declared`，且关闭审计/豁免/闭合这几条会直接抛错。

跑法：

    python -m unittest discover -s tests -p 'test_governance.py' -v
    python tests/test_governance.py
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import governance as gov  # noqa: E402
from kernel.autoresearch.runtime.store import Store  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def make_plan(**overrides) -> dict:
    plan = {
        "id": "P1",
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
        "case_sets": {
            "target": ["t1", "t2"],
            "regression": ["r1", "r2"],
            "control": ["c1"],
        },
        "previous_passes": ["r1"],
        "branches": {
            "support": {"action": "把 c1 进下一轮，继续同假设"},
            "contradict": {"action": "回退 c1，改假设"},
            "inconclusive": {"action": "先补一轮重复读数，不改配置"},
        },
        "budget": {"max_cost_units": 10, "max_seconds": 600},
        "input_files": ["docs/plan.md"],
        "notes": "叙述字段：改它不该触发重新审计",
    }
    for key, value in overrides.items():
        plan[key] = value
    return plan


def audit_report(prepared_id: str, **overrides) -> dict:
    coverage = {
        key: {"verdict": "ok", "note": "看过了，没问题",
              "sources": [{"path": "docs/plan.md", "locator": "L1"}]}
        for key in gov.AUDIT_CATEGORIES
    }
    report = {"prepared_id": prepared_id, "coverage": coverage, "issues": [], "resolves": []}
    for key, value in overrides.items():
        if key == "coverage":
            coverage.update(value)
        else:
            report[key] = value
    return report


class FakeVerifier:
    """假宿主：只证明「注入 verifier 这条路能走」，不代表现实里核过。"""

    def __init__(self, *, allow=True, kinds=None):
        self.allow = allow
        self.kinds = kinds
        self.calls = []

    def verify(self, kind, payload):
        self.calls.append((kind, payload))
        if not self.allow:
            return None
        if self.kinds and kind not in self.kinds:
            return None
        return {"verified": True, "kind": kind, "by": "fake-host", "payload_seen": True}


class BrokenVerifier:
    def verify(self, kind, payload):
        raise RuntimeError("host log unavailable")


class GovCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(pathlib.Path(self._tmp.name) / "gov.sqlite")
        self.store = Store(self.db)
        self.gov = gov.Governance(self.store)

    def frozen(self, plan: dict | None = None, actor: str = "main"):
        return self.gov.freeze_plan(plan or make_plan(), actor=actor)

    def audit_once(self, plan_id="P1", *, actor="main", reader="reviewer", report=None,
                   evidence=None):
        prepared = self.gov.prepare_audit(plan_id, actor=actor)
        payload = report or audit_report(prepared["prepared_id"])
        return self.gov.record_audit(prepared["prepared_id"], payload, actor=reader,
                                     evidence=evidence)


# ---------------------------------------------------------------------------
# 方案校验
# ---------------------------------------------------------------------------


class PlanValidationTests(unittest.TestCase):
    def test_valid_plan(self):
        self.assertEqual(gov.validate_plan(make_plan()), [])

    def test_missing_top_level_keys(self):
        for key in gov.KEY_FIELDS:
            plan = make_plan()
            plan.pop(key)
            errors = gov.validate_plan(plan)
            self.assertTrue(any(key in item for item in errors), f"{key} 应被点名：{errors}")

    def test_not_a_dict(self):
        self.assertEqual(len(gov.validate_plan([1, 2])), 1)

    def test_hypothesis_id_must_match(self):
        plan = make_plan()
        plan["change_set"][0]["hypothesis_id"] = "H2"
        self.assertTrue(any("不一致" in item for item in gov.validate_plan(plan)))

    def test_one_hypothesis_per_round(self):
        plan = make_plan()
        plan["change_set"] = [
            {"id": "c1", "description": "a", "hypothesis_id": "H1"},
            {"id": "c2", "description": "b", "hypothesis_id": "H2"},
        ]
        errors = gov.validate_plan(plan)
        self.assertTrue(any("只允许改**一个假设" in item for item in errors), errors)

    def test_hypothesis_id_without_object(self):
        plan = make_plan(hypothesis="一句话假设")
        errors = gov.validate_plan(plan)
        self.assertTrue(any("无法核对" in item for item in errors), errors)

    def test_empty_change_set(self):
        self.assertTrue(any("change_set 不能为空" in item
                            for item in gov.validate_plan(make_plan(change_set=[]))))

    def test_duplicate_change_ids(self):
        plan = make_plan()
        plan["change_set"] = [{"id": "c1", "description": "a"},
                              {"id": "c1", "description": "b"}]
        self.assertTrue(any("重复" in item for item in gov.validate_plan(plan)))

    def test_criteria_needs_source(self):
        plan = make_plan()
        plan["criteria"] = {"metric": "m", "direction": "increase", "threshold": 1}
        errors = gov.validate_plan(plan)
        self.assertTrue(any("criteria.source" in item for item in errors), errors)

    def test_criteria_direction_and_threshold(self):
        plan = make_plan()
        plan["criteria"] = {"metric": "m", "direction": "up", "threshold": 1, "source": "s"}
        self.assertTrue(any("direction" in item for item in gov.validate_plan(plan)))

        for bad in (float("nan"), float("inf"), True, "1", None):
            plan["criteria"] = {"metric": "m", "direction": "increase",
                                "threshold": bad, "source": "s"}
            errors = gov.validate_plan(plan)
            self.assertTrue(any("threshold" in item for item in errors),
                            f"threshold={bad!r} 应被判错：{errors}")

    def test_case_sets_target_required_and_unique(self):
        plan = make_plan()
        plan["case_sets"] = {"target": [], "regression": ["r1"], "control": []}
        self.assertTrue(any("target 不能为空" in item for item in gov.validate_plan(plan)))

        plan["case_sets"] = {"target": ["t1", "t1"], "regression": ["r1"], "control": []}
        self.assertTrue(any("重复题号" in item for item in gov.validate_plan(plan)))

    def test_case_sets_no_overlap(self):
        plan = make_plan()
        plan["case_sets"] = {"target": ["t1"], "regression": ["t1"], "control": []}
        self.assertTrue(any("重叠" in item for item in gov.validate_plan(plan)))

    def test_previous_passes_must_stay_in_regression(self):
        plan = make_plan(previous_passes=["r1", "r9"])
        errors = gov.validate_plan(plan)
        self.assertTrue(any("不许从回归集合里掉出去" in item for item in errors), errors)
        self.assertTrue(any("r9" in item for item in errors))

    def test_branches_must_differ(self):
        plan = make_plan()
        plan["branches"] = {key: {"action": "继续观察"} for key in gov.BRANCH_KINDS}
        errors = gov.validate_plan(plan)
        self.assertTrue(any("三支行动一样" in item or "完全相同" in item for item in errors),
                        errors)

    def test_branches_missing_kind(self):
        plan = make_plan()
        plan["branches"] = {"support": {"action": "a"}, "contradict": {"action": "b"}}
        self.assertTrue(any("inconclusive" in item for item in gov.validate_plan(plan)))

    def test_budget_rules(self):
        for bad_cost in (-1, True, 1.5, "10"):
            plan = make_plan(budget={"max_cost_units": bad_cost, "max_seconds": 60})
            self.assertTrue(any("max_cost_units" in item for item in gov.validate_plan(plan)),
                            f"max_cost_units={bad_cost!r} 应被判错")
        for bad_seconds in (0, -1, True):
            plan = make_plan(budget={"max_cost_units": 1, "max_seconds": bad_seconds})
            self.assertTrue(any("max_seconds" in item for item in gov.validate_plan(plan)))

    def test_input_files_must_be_relative(self):
        for bad in ("/abs/x.md", "C:\\abs\\x.md", "../up.md"):
            plan = make_plan(input_files=[bad])
            self.assertTrue(any("input_files[0]" in item for item in gov.validate_plan(plan)),
                            f"{bad} 应被判错")

    def test_fingerprint_ignores_wording_but_tracks_keys(self):
        plan = make_plan()
        base_key = gov.plan_fingerprint(plan)

        wording = make_plan(notes="换个说法", title="新标题")
        self.assertEqual(gov.plan_fingerprint(wording), base_key)
        self.assertNotEqual(gov._digest(wording), gov._digest(plan))

        changed = make_plan(budget={"max_cost_units": 11, "max_seconds": 600})
        self.assertNotEqual(gov.plan_fingerprint(changed), base_key)

    def test_order_does_not_change_fingerprint(self):
        plan = make_plan()
        shuffled = make_plan()
        shuffled["case_sets"] = {"target": ["t2", "t1"],
                                 "regression": ["r2", "r1"],
                                 "control": ["c1"]}
        self.assertEqual(gov.plan_fingerprint(plan), gov.plan_fingerprint(shuffled))


# ---------------------------------------------------------------------------
# 冻结与审计
# ---------------------------------------------------------------------------


class FreezeAndAuditTests(GovCase):
    def test_freeze_rejects_invalid_plan(self):
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.freeze_plan(make_plan(case_sets={"target": [], "regression": [],
                                                      "control": []}), actor="main")
        self.assertTrue(ctx.exception.errors)

    def test_wording_only_change_needs_no_reaudit(self):
        first = self.frozen()
        second = self.gov.freeze_plan(make_plan(notes="改一句动机"), actor="main")
        self.assertFalse(second["reaudit_required"])
        self.assertEqual(first["key_fingerprint"], second["key_fingerprint"])
        self.assertEqual(second["version"], 2)

    def test_key_change_requires_reaudit(self):
        self.frozen()
        second = self.gov.freeze_plan(
            make_plan(criteria={"metric": "通过题数", "direction": "increase",
                                "threshold": 2, "source": "新门槛 rules.md §3"}),
            actor="main",
        )
        self.assertTrue(second["reaudit_required"])
        self.assertNotEqual(second["key_fingerprint"], gov.plan_fingerprint(make_plan()))

    def test_prepare_requires_frozen_plan(self):
        with self.assertRaises(gov.GovernanceError):
            self.gov.prepare_audit("P404", actor="main")

    def test_audit_state_blocks_without_report(self):
        self.frozen()
        state = self.gov.audit_state("P1")
        self.assertFalse(state["ok"])
        self.assertTrue(any("没有审计" in item for item in state["problems"]))

    def test_clean_audit_passes(self):
        self.frozen()
        result = self.audit_once()
        self.assertEqual(result["independence"], "declared")
        state = self.gov.audit_state("P1")
        self.assertTrue(state["ok"], state["problems"])
        self.assertEqual(state["audits_used"], 1)

    def test_auditor_cannot_be_owner(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.record_audit(prepared["prepared_id"],
                                  audit_report(prepared["prepared_id"]), actor="main")
        self.assertIn("负责人", str(ctx.exception))

    def test_report_must_match_prepared_id(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        with self.assertRaises(gov.GovernanceError):
            self.gov.record_audit(prepared["prepared_id"],
                                  audit_report("PREP-other"), actor="reviewer")

    def test_report_needs_exactly_seven_categories(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"])
        report["coverage"].pop("history")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        self.assertTrue(any("history" in item for item in ctx.exception.errors))

        report = audit_report(prepared["prepared_id"])
        report["coverage"]["extra"] = {"verdict": "ok", "note": "n",
                                       "sources": [{"path": "p", "locator": "L1"}]}
        with self.assertRaises(gov.GovernanceError):
            self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")

    def test_issue_verdict_needs_matching_issue(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"],
                              coverage={"criterion": {"verdict": "issue",
                                                      "note": "门槛没出处",
                                                      "sources": [{"path": "p", "locator": "L1"}]}})
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        self.assertTrue(any("criterion" in item for item in ctx.exception.errors))

    def test_unknown_verdict_must_be_blocking(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"],
                              coverage={"activation": {"verdict": "unknown",
                                                       "note": "看不到生效链路",
                                                       "sources": [{"path": "p", "locator": "L1"}]}})
        report["issues"] = [{"id": "AUD-P1-1", "category": "activation", "blocking": False,
                             "problem": "生效链路不明", "consequence": "可能测的不是这份包",
                             "suggestion": "补一条可观察证据",
                             "sources": [{"path": "p", "locator": "L1"}]}]
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        self.assertTrue(any("unknown" in item for item in ctx.exception.errors))

    def test_second_report_on_same_prepare_rejected(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"])
        self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        with self.assertRaises(gov.GovernanceError):
            self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")

    def test_report_bound_to_current_version(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        self.gov.freeze_plan(
            make_plan(criteria={"metric": "通过题数", "direction": "increase",
                                "threshold": 3, "source": "改了门槛"}),
            actor="main",
        )
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.record_audit(prepared["prepared_id"],
                                  audit_report(prepared["prepared_id"]), actor="reviewer")
        self.assertIn("关键字段", str(ctx.exception))

    def test_audit_limit_is_two(self):
        self.frozen()
        self.audit_once(reader="reviewer")
        prepared2 = self.gov.prepare_audit("P1", actor="main")
        self.assertEqual(prepared2["audit_index"], 2)
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.prepare_audit("P1", actor="main")
        self.assertIn("上限", str(ctx.exception))

    def test_blocking_issue_keeps_state_red_until_resolved(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"],
                              coverage={"criterion": {"verdict": "issue",
                                                      "note": "门槛是拍的",
                                                      "sources": [{"path": "p", "locator": "L2"}]}})
        report["issues"] = [{"id": "AUD-P1-1", "category": "criterion", "blocking": True,
                             "problem": "门槛没有出处", "consequence": "读数无法解释",
                             "suggestion": "补一条规则出处",
                             "sources": [{"path": "p", "locator": "L2"}]}]
        self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        state = self.gov.audit_state("P1")
        self.assertFalse(state["ok"])
        self.assertEqual(state["blocking_open"], ["AUD-P1-1"])

        self.gov.respond_audit(state["report_id"],
                               [{"issue_id": "AUD-P1-1", "disposition": "accepted",
                                 "reason": "已改判据出处",
                                 "sources": [{"path": "docs/plan.md", "locator": "L9"}]}],
                               actor="main")
        state = self.gov.audit_state("P1")
        self.assertFalse(state["ok"])  # 采纳不等于关闭：还得有 resolves 或 waiver
        self.assertEqual(state["blocking_open"], ["AUD-P1-1"])

    def test_repeated_disposition_rejected(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"])
        report["issues"] = [{"id": "AUD-P1-1", "category": "cost", "blocking": False,
                             "problem": "成本偏高", "consequence": "多花机时",
                             "suggestion": "减题",
                             "sources": [{"path": "p", "locator": "L3"}]}]
        self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        item = {"issue_id": "AUD-P1-1", "disposition": "accepted", "reason": "已减题",
                "sources": [{"path": "docs/plan.md", "locator": "L5"}]}
        self.gov.respond_audit(self._report_id(), [item], actor="main")
        with self.assertRaises(gov.GovernanceError):
            self.gov.respond_audit(self._report_id(), [item], actor="main")

    def _report_id(self) -> str:
        conn = self.store.connect()
        try:
            return conn.execute("SELECT report_id FROM gov_audit_reports").fetchone()[0]
        finally:
            conn.close()

    def test_waiver_requires_verified_evidence(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"])
        report["issues"] = [{"id": "AUD-P1-1", "category": "cost", "blocking": True,
                             "problem": "成本偏高", "consequence": "多花机时",
                             "suggestion": "减题",
                             "sources": [{"path": "p", "locator": "L3"}]}]
        recorded = self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.waive(recorded["report_id"], "AUD-P1-1", reason="人同意多花",
                           evidence={"quote": "就这样跑"}, actor="main")
        self.assertIn("verifier", str(ctx.exception))

        gov_verified = gov.Governance(self.store, verifier=FakeVerifier())
        gov_verified.waive(recorded["report_id"], "AUD-P1-1", reason="人同意多花",
                           evidence={"quote": "就这样跑"}, actor="main")
        state = gov_verified.audit_state("P1")
        self.assertTrue(state["ok"], state["problems"])

        gov_verified.freeze_plan(make_plan(budget={"max_cost_units": 99, "max_seconds": 600}),
                                 actor="main")
        state = gov_verified.audit_state("P1")
        self.assertFalse(state["ok"])  # 关键字段变了：旧审计与旧豁免都不覆盖

    def test_policy_off_requires_verified_user_evidence(self):
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.set_policy(scope="global", target="*", mode="off", actor="main",
                                evidence={"quote": "以后不用审"})
        self.assertIn("verifier", str(ctx.exception))

        verified = gov.Governance(self.store, verifier=FakeVerifier())
        out = verified.set_policy(scope="global", target="*", mode="off", actor="main",
                                  evidence={"quote": "以后不用审", "created_at": 9e9})
        self.assertEqual(out["independence"], "verified")
        self.assertEqual(verified.audit_policy()["mode"], "off")
        with self.assertRaises(gov.GovernanceError):
            verified.prepare_audit("P1", actor="main")

    def test_locked_policy_cannot_be_disabled(self):
        with self.assertRaises(gov.GovernanceError):
            gov.Governance(self.store,
                           policy={"policy_off_requires_verified_evidence": False})

    def test_broken_verifier_degrades_to_declared(self):
        broken = gov.Governance(self.store, verifier=BrokenVerifier())
        result = broken.register_rolepack("planner", path="kernel/roles/planner.md",
                                          sha256="a" * 64, actor="main")
        self.assertEqual(result["sha256"], "a" * 64)
        read = broken.record_read(path="kernel/contracts/plan.md", sha256="b" * 64,
                                  role="planner", session="s1", evidence={"seq": 1})
        self.assertEqual(read["independence"], "declared")


# ---------------------------------------------------------------------------
# 专项意图与节点监控
# ---------------------------------------------------------------------------


class IntentTests(GovCase):
    def _intent(self, **kwargs):
        payload = {"quote": "直到那两道题也能过", "source_session": "session-1", "seq": 12,
                   "actor": "main"}
        payload.update(kwargs)
        return self.gov.create_intent("I1", **payload)

    def test_create_requires_quote_and_seq(self):
        for kwargs in ({"quote": ""}, {"seq": 0}, {"seq": True}):
            with self.assertRaises(gov.GovernanceError):
                self.gov.create_intent("I1", source_session="s", actor="main",
                                       **{"quote": "x", "seq": 1, **kwargs})

    def test_duplicate_intent_rejected(self):
        self._intent()
        with self.assertRaises(gov.GovernanceError):
            self._intent()

    def test_heartbeat_is_not_a_trigger(self):
        self._intent()
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.register_trigger("I1", "heartbeat", "巡检", actor="main")
        self.assertIn("心跳", str(ctx.exception))

    def test_report_must_cover_open_triggers(self):
        self._intent()
        trigger = self.gov.register_trigger("I1", "result_analysis", "第 3 轮读数收回", actor="main")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.record_monitor_report(
                "I1",
                {"covers": {"trigger_ids": ["TRG-other"], "user_seq": 12},
                 "verdict": "on_track", "basis": "读数与计划一致"},
                actor="monitor",
            )
        self.assertIn("没有覆盖", str(ctx.exception))

        report = self.gov.record_monitor_report(
            "I1",
            {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12},
             "verdict": "on_track", "basis": "逐题读数与计划一致"},
            actor="monitor",
        )
        self.assertEqual(report["independence"], "declared")

    def test_report_requires_basis_and_valid_verdict(self):
        self._intent()
        trigger = self.gov.register_trigger("I1", "result_analysis", "读数收回", actor="main")
        for bad in ({"verdict": "looks_fine", "basis": "x"},
                    {"verdict": "on_track", "basis": ""}):
            payload = {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12}}
            payload.update(bad)
            with self.assertRaises(gov.GovernanceError):
                self.gov.record_monitor_report("I1", payload, actor="monitor")

    def test_check_start_blocks_on_open_trigger(self):
        self._intent()
        self.gov.register_trigger("I1", "result_analysis", "第 3 轮读数收回", actor="main")
        state = self.gov.check_start(intent_id="I1")
        self.assertFalse(state["allow"])
        self.assertTrue(any("触发" in item for item in state["problems"]))

    def test_check_start_allows_after_report(self):
        self._intent()
        trigger = self.gov.register_trigger("I1", "result_analysis", "读数收回", actor="main")
        self.gov.record_monitor_report(
            "I1", {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12},
                   "verdict": "on_track", "basis": "与计划一致"}, actor="monitor")
        state = self.gov.check_start(intent_id="I1")
        self.assertTrue(state["allow"], state["problems"])

    def test_unassessed_user_update_blocks_without_intent(self):
        self.gov.sync_user_update(source_session="session-1", seq=20, quote="再盯一下那 5 题")
        state = self.gov.check_start(source_session="session-1")
        self.assertFalse(state["allow"])
        self.assertIn("未评估", " ".join(state["problems"]))

        with self.assertRaises(gov.GovernanceError):
            self.gov.assess_update(source_session="session-1", covers_seq=20,
                                   decision="none", quote="再盯一下那 5 题",
                                   actor="main", answer_seq=19)
        self.gov.assess_update(source_session="session-1", covers_seq=20, decision="none",
                               quote="再盯一下那 5 题", actor="main", answer_seq=21)
        self.assertTrue(self.gov.check_start(source_session="session-1")["allow"])

    def test_trigger_on_missing_intent(self):
        with self.assertRaises(gov.GovernanceError):
            self.gov.register_trigger("I404", "user_update", "纠正", actor="main")

    def test_close_rejects_illegal_reason(self):
        self._intent()
        trigger = self.gov.register_trigger("I1", "result_analysis", "读数收回", actor="main")
        report = self.gov.record_monitor_report(
            "I1", {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12},
                   "verdict": "on_track", "basis": "与计划一致"}, actor="monitor")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.close_intent("I1", reason="failed_once", evidence={"x": 1},
                                  report_id=report["report_id"], actor="main")
        self.assertIn("理由", str(ctx.exception))

    def test_unsatisfiable_needs_unachievable_verdict(self):
        self._intent()
        trigger = self.gov.register_trigger("I1", "result_analysis", "读数收回", actor="main")
        report = self.gov.record_monitor_report(
            "I1", {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12},
                   "verdict": "on_track", "basis": "与计划一致"}, actor="monitor")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.close_intent("I1", reason="unsatisfiable",
                                  evidence={"basis": "当前条件下做不到"},
                                  report_id=report["report_id"], actor="main")
        self.assertIn("unachievable", str(ctx.exception))

    def test_withdrawn_needs_user_quote(self):
        self._intent()
        trigger = self.gov.register_trigger("I1", "result_analysis", "读数收回", actor="main")
        report = self.gov.record_monitor_report(
            "I1", {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12},
                   "verdict": "on_track", "basis": "与计划一致"}, actor="monitor")
        with self.assertRaises(gov.GovernanceError):
            self.gov.close_intent("I1", reason="withdrawn", evidence={"note": "用户说不用了"},
                                  report_id=report["report_id"], actor="main")

    def test_close_achieved_ok(self):
        self._intent()
        trigger = self.gov.register_trigger("I1", "result_analysis", "读数收回", actor="main")
        report = self.gov.record_monitor_report(
            "I1", {"covers": {"trigger_ids": [trigger["trigger_id"]], "user_seq": 12},
                   "verdict": "achieved", "basis": "两道题已通过（结果文件逐题核对）"},
            actor="monitor")
        out = self.gov.close_intent("I1", reason="achieved",
                                    evidence={"results": "demo/results.jsonl"},
                                    report_id=report["report_id"], actor="main")
        self.assertEqual(out["state"], "closed")
        after = self.gov.check_start(intent_id="I1")
        self.assertTrue(after["allow"])
        self.assertTrue(any("closed" in item for item in after["diagnostics"]))
        with self.assertRaises(gov.GovernanceError):
            self.gov.register_trigger("I1", "user_update", "新话", actor="main")

    def test_blocked_is_not_closed(self):
        self._intent()
        self.gov.block_intent("I1", reason="机器排队", actor="main")
        state = self.gov.check_start(intent_id="I1")
        self.assertFalse(state["allow"])
        self.assertIn("blocked", " ".join(state["problems"]))
        self.gov.unblock_intent("I1", actor="main")
        self.assertEqual(self.gov.intent_state("I1")["state"], "active")


# ---------------------------------------------------------------------------
# 经验候选
# ---------------------------------------------------------------------------


class LessonTests(GovCase):
    def _candidate(self, **overrides):
        payload = {
            "candidate_id": "L-01",
            "title": "先冻结再改叙述",
            "method": "改叙述字段之前先确认关键字段没动",
            "premise": "只对同一份冻结版本成立",
            "applicable_when": "一轮实验内要改措辞时",
            "basis": "docs/research.md §3",
            "boundaries": "关键字段变了就不成立",
            "evidence_level": "supported",
        }
        payload.update(overrides)
        return self.gov.add_lesson_candidate(payload, actor="main")

    def test_candidate_requires_premise(self):
        with self.assertRaises(gov.GovernanceError) as ctx:
            self._candidate(premise="")
        self.assertTrue(any("premise" in item for item in ctx.exception.errors))

    def test_duplicate_candidate_rejected(self):
        self._candidate()
        with self.assertRaises(gov.GovernanceError):
            self._candidate()

    def test_confirm_keep(self):
        self._candidate()
        self.gov.confirm_lesson("L-01", rev=1, option="keep", quote="保留，测清楚再说",
                                source="gui", actor="main", source_session="s1",
                                source_seq=30)
        state = self.gov.lesson_state("L-01")
        self.assertEqual(state["status"], "confirmed")

    def test_revision_invalidates_confirmation(self):
        self._candidate()
        self.gov.confirm_lesson("L-01", rev=1, option="keep", quote="保留",
                                source="gui", actor="main")
        self.assertEqual(self.gov.lesson_state("L-01")["status"], "confirmed")

        self.gov.revise_lesson("L-01", {"method": "改成先看关键字段是否变化"}, actor="main")
        state = self.gov.lesson_state("L-01")
        self.assertEqual(state["rev"], 2)
        self.assertEqual(state["status"], "pending")
        self.assertEqual(len(state["stale_confirmations"]), 1)
        self.assertTrue(state["problems"])

        with self.assertRaises(gov.GovernanceError):
            self.gov.confirm_lesson("L-01", rev=1, option="keep", quote="保留",
                                    source="gui", actor="main")

    def test_confirm_needs_quote_and_valid_option(self):
        self._candidate()
        with self.assertRaises(gov.GovernanceError):
            self.gov.confirm_lesson("L-01", rev=1, option="keep", quote="", source="gui",
                                    actor="main")
        with self.assertRaises(gov.GovernanceError):
            self.gov.confirm_lesson("L-01", rev=1, option="maybe", quote="保留", source="gui",
                                    actor="main")

    def test_drop_is_recorded_not_deleted(self):
        self._candidate()
        self.gov.confirm_lesson("L-01", rev=1, option="drop", quote="这条不留",
                                source="gui", actor="main")
        state = self.gov.lesson_state("L-01")
        self.assertEqual(state["status"], "dropped")
        self.assertEqual(state["rev"], 1)
        self.assertEqual([item["status"] for item in self.gov.lessons()], ["dropped"])

    def test_keep_pending_marks_status(self):
        self._candidate()
        self.gov.confirm_lesson("L-01", rev=1, option="keep-pending", quote="先留着待验证",
                                source="gui", actor="main")
        self.assertEqual(self.gov.lesson_state("L-01")["status"], "confirmed-pending")
        self.assertEqual(
            [item["candidate_id"] for item in self.gov.lessons(status="confirmed-pending")],
            ["L-01"],
        )
        # 「待验证保留」不当成已证实的经验用；按 exact status 过滤，不混口径。
        self.assertEqual(self.gov.lessons(status="confirmed"), [])


# ---------------------------------------------------------------------------
# 角色包 / 委派 / 读台账
# ---------------------------------------------------------------------------


class DelegationTests(GovCase):
    def test_role_must_be_known(self):
        with self.assertRaises(gov.GovernanceError):
            self.gov.register_rolepack("intern", path="x", sha256="a" * 64, actor="main")

    def test_sha_shape(self):
        with self.assertRaises(gov.GovernanceError):
            self.gov.register_rolepack("planner", path="x", sha256="short", actor="main")

    def test_delegation_needs_registered_rolepack(self):
        with self.assertRaises(gov.GovernanceError):
            self.gov.record_delegation(child_session="s2", role="planner", parent_session="s1",
                                       rolepack_sha="a" * 64, prompt_sha="b" * 64,
                                       actor="main")

    def test_delegation_hash_mismatch(self):
        self.gov.register_rolepack("planner", path="kernel/roles/planner.md",
                                   sha256="a" * 64, actor="main")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.record_delegation(child_session="s2", role="planner",
                                       parent_session="s1", rolepack_sha="b" * 64,
                                       prompt_sha="c" * 64, actor="main")
        self.assertIn("rolepack_sha", str(ctx.exception))

    def test_delegation_declared_without_verifier(self):
        self.gov.register_rolepack("planner", path="kernel/roles/planner.md",
                                   sha256="a" * 64, actor="main")
        out = self.gov.record_delegation(child_session="s2", role="planner",
                                          parent_session="s1", rolepack_sha="a" * 64,
                                          prompt_sha="c" * 64, actor="main")
        self.assertEqual(out["independence"], "declared")

        verified = gov.Governance(self.store, verifier=FakeVerifier(kinds={"delegation"}))
        verified.register_rolepack("planner", path="kernel/roles/planner.md",
                                   sha256="a" * 64, actor="main")
        out = verified.record_delegation(child_session="s3", role="planner",
                                         parent_session="s1", rolepack_sha="a" * 64,
                                         prompt_sha="c" * 64, actor="main",
                                         evidence={"session_id": "s3"})
        self.assertEqual(out["independence"], "verified")

    def test_read_is_declared_by_default(self):
        out = self.gov.record_read(path="kernel/contracts/plan.md", sha256="d" * 64,
                                   role="planner", session="s1")
        self.assertEqual(out["independence"], "declared")
        state = self.gov.reading_state(role="planner")
        self.assertEqual(state["n_reads"], 1)
        self.assertEqual(state["by_independence"]["verified"], 0)


# ---------------------------------------------------------------------------
# 文档维护
# ---------------------------------------------------------------------------


class DocTests(GovCase):
    def test_hot_needs_limit(self):
        with self.assertRaises(gov.GovernanceError):
            self.gov.register_doc("RULES", tier="hot", path="RULES.md", bytes_=10,
                                  sha256="a" * 64, actor="main")

    def test_over_limit_is_flagged(self):
        self.gov.register_doc("RULES", tier="hot", path="RULES.md", bytes_=200,
                              sha256="a" * 64, limit_bytes=100, actor="main")
        status = self.gov.doc_status()
        self.assertEqual([item["doc_id"] for item in status["hot_over_limit"]], ["RULES"])
        self.assertTrue(status["problems"])

    def test_roll_requires_matching_hot_hash(self):
        self.gov.register_doc("RULES", tier="hot", path="RULES.md", bytes_=200,
                              sha256="a" * 64, limit_bytes=100, actor="main")
        with self.assertRaises(gov.GovernanceError) as ctx:
            self.gov.roll_cold("RULES", cold_path="archive/RULES_20261109.md",
                               cold_sha="b" * 64, index_path="archive/INDEX.md",
                               hot_sha_before="c" * 64, hot_bytes_before=200, actor="main")
        self.assertIn("哈希", str(ctx.exception))

    def test_roll_then_status(self):
        self.gov.register_doc("RULES", tier="hot", path="RULES.md", bytes_=200,
                              sha256="a" * 64, limit_bytes=100, actor="main")
        self.gov.roll_cold("RULES", cold_path="archive/RULES_20261109.md", cold_sha="b" * 64,
                           index_path="archive/INDEX.md", hot_sha_before="a" * 64,
                           hot_bytes_before=200, actor="main")
        status = self.gov.doc_status()
        self.assertEqual(status["last_rolls"]["RULES"]["index_path"], "archive/INDEX.md")
        self.assertEqual(status["cold_missing_index"], [])

    def test_cold_path_must_differ(self):
        self.gov.register_doc("RULES", tier="hot", path="RULES.md", bytes_=10,
                              sha256="a" * 64, limit_bytes=100, actor="main")
        with self.assertRaises(gov.GovernanceError):
            self.gov.roll_cold("RULES", cold_path="RULES.md", cold_sha="b" * 64,
                               index_path="archive/INDEX.md", hot_sha_before="a" * 64,
                               hot_bytes_before=10, actor="main")


# ---------------------------------------------------------------------------
# 现状页与 append-only
# ---------------------------------------------------------------------------


class StatusPageTests(unittest.TestCase):
    def _legacy(self, text):
        return gov.validate_status_page(text, legacy=True)

    def _render(self, sections) -> str:
        return "\n\n".join(f"## {index + 1} {name}"
                           for index, name in enumerate(sections))

    def test_eight_sections_ok(self):
        self.assertEqual(self._legacy(self._render(gov.LEGACY_STATUS_SECTIONS)), [])

    def test_missing_section(self):
        sections = [name for name in gov.LEGACY_STATUS_SECTIONS if name != "需要你拍板"]
        errors = self._legacy(self._render(sections))
        self.assertTrue(any("需要你拍板" in item for item in errors), errors)

    def test_wrong_order(self):
        sections = list(gov.LEGACY_STATUS_SECTIONS)
        sections[2], sections[3] = sections[3], sections[2]
        self.assertTrue(any("顺序" in item
                            for item in self._legacy(self._render(sections))))

    def test_evidence_sections_do_not_impersonate_each_other(self):
        """「证据」与「证据在哪」是两节：少一节时必须点名少的那一节。"""
        self.assertIn("证据", gov.LEGACY_STATUS_SECTIONS)
        self.assertIn("证据在哪", gov.LEGACY_STATUS_SECTIONS)
        sections = [name for name in gov.LEGACY_STATUS_SECTIONS if name != "证据"]
        errors = self._legacy(self._render(sections))
        self.assertTrue(any("「证据」" in item for item in errors), errors)
        self.assertFalse(any("「证据在哪」" in item for item in errors), errors)

    def test_numbering_variant_still_matches(self):
        sections = ["02 范围与目标"] + list(gov.LEGACY_STATUS_SECTIONS[1:])
        self.assertEqual(self._legacy(self._render(sections)), [])

    def test_empty(self):
        self.assertEqual(len(self._legacy("")), 1)


# ---------------------------------------------------------------------------
# 模板与机读必读清单：模板必须真的能用，不是空壳
# ---------------------------------------------------------------------------


class TemplateTests(GovCase):
    def _json(self, name: str) -> dict:
        path = ROOT / "kernel" / "templates" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("_template", "_usage"):
            data.pop(key, None)
        return data

    def test_plan_template_passes_validation(self):
        self.assertEqual(gov.validate_plan(self._json("plan.template.json")), [])

    def test_audit_report_template_is_recordable(self):
        self.frozen()
        prepared = self.gov.prepare_audit("P1", actor="main")
        report = self._json("plan_audit_report.template.json")
        report["prepared_id"] = prepared["prepared_id"]
        recorded = self.gov.record_audit(prepared["prepared_id"], report, actor="reviewer")
        self.assertTrue(recorded["report_id"])

        state = self.gov.audit_state("P1")
        self.assertFalse(state["ok"])  # 模板带一条 issue ⇒ 未处置就不放行
        self.assertTrue(any("还没有处置" in item for item in state["problems"]))

        issue_id = report["issues"][0]["id"]
        self.gov.respond_audit(
            recorded["report_id"],
            [{"issue_id": issue_id, "disposition": "accepted", "reason": "按建议改",
              "sources": [{"path": "docs/research.md", "locator": "L1"}]}],
            actor="main",
        )
        self.assertTrue(self.gov.audit_state("P1")["ok"])

    def test_intent_report_template_is_recordable(self):
        self.gov.create_intent("I1", quote="直到那两道题也能过", source_session="s1",
                               seq=3, actor="main")
        trigger = self.gov.register_trigger("I1", "result_analysis", "第 1 轮读数收回",
                                            actor="main")
        report = self._json("intent_report.template.json")
        report["covers"]["trigger_ids"] = [trigger["trigger_id"]]
        report["covers"]["user_seq"] = 3
        out = self.gov.record_monitor_report("I1", report, actor="monitor")
        self.assertEqual(out["independence"], "declared")
        self.assertTrue(self.gov.check_start(intent_id="I1")["allow"])

    def test_status_page_template_passes_section_check(self):
        text = (ROOT / "kernel" / "templates" / "status_page.template.md").read_text(
            encoding="utf-8")
        self.assertEqual(gov.validate_status_page(text), [])

    def test_lesson_template_keeps_binding_tokens(self):
        text = (ROOT / "kernel" / "templates" / "lesson_candidate.template.md").read_text(
            encoding="utf-8")
        for token in ("REV:", "STATUS:", "METHOD:", "前提：", "适用场景：",
                      "依据：", "不适用边界：", "证据等级：", "CONFIRMED_REV:"):
            self.assertIn(token, text, f"模板缺 {token}")

    def test_trajectory_template_has_nine_sections(self):
        text = (ROOT / "kernel" / "templates" / "trajectory_review.template.md").read_text(
            encoding="utf-8")
        for number in range(9):
            self.assertIn(f"## {number} ", text, f"复盘模板缺第 {number} 节")


class ReadingRequiredTests(unittest.TestCase):
    def test_entries_shape_and_anchors_exist(self):
        path = ROOT / "kernel" / "contracts" / "reading_required.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema"), 1)
        entries = data.get("entries")
        self.assertIsInstance(entries, list)
        self.assertTrue(entries)
        ids = [entry["id"] for entry in entries]
        self.assertEqual(len(ids), len(set(ids)), "必读条目 id 不许重复")
        for entry in entries:
            for key in ("id", "role", "phase", "path", "anchor", "why"):
                value = entry.get(key)
                self.assertTrue(isinstance(value, str) and value.strip(),
                                f"{entry.get('id')} 的 {key} 必须非空")
            self.assertIn(entry["role"], gov.ROLE_NAMES,
                          f"{entry['id']} 的 role 不在已知角色里")
            target = ROOT / entry["path"]
            self.assertTrue(target.is_file(), f"{entry['id']} 指向的文件不存在：{entry['path']}")
            self.assertIn(entry["anchor"], target.read_text(encoding="utf-8"),
                          f"{entry['id']} 的 anchor 在 {entry['path']} 里找不到")

    def test_reading_required_template_shape(self):
        data = json.loads((ROOT / "kernel" / "templates" /
                           "reading_required.template.json").read_text(encoding="utf-8"))
        for key in ("_template", "_usage"):
            data.pop(key, None)
        self.assertEqual(data.get("schema"), 1)
        for entry in data["entries"]:
            for key in ("id", "role", "phase", "path", "anchor", "why"):
                self.assertIn(key, entry)


class RoleFilesTests(unittest.TestCase):
    def test_every_role_has_prompt_file_with_frontmatter(self):
        for role in gov.ROLE_NAMES:
            path = ROOT / "kernel" / "roles" / f"{role}.md"
            self.assertTrue(path.is_file(), f"缺角色文件 {role}.md")
            text = path.read_text(encoding="utf-8")
            self.assertIn(f"role: {role}", text)
            for key in ("reads:", "writes:", "never:", "outputs:"):
                self.assertIn(key, text, f"{role}.md 的 frontmatter 缺 {key}")

    def test_instruction_files_exist(self):
        for name in ("main_session.md", "plan_authoring.md", "independent_review.md"):
            self.assertTrue((ROOT / "kernel" / "instructions" / name).is_file(),
                            f"缺指令文件 {name}")


class AppendOnlyTests(GovCase):
    def test_plan_rows_cannot_be_updated_or_deleted(self):
        self.frozen()
        conn = self.store.connect()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE gov_plans SET actor='x'")
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM gov_plans")
        finally:
            conn.close()

    def test_confirmations_cannot_be_deleted(self):
        self.gov.add_lesson_candidate(
            {"candidate_id": "L-01", "title": "t", "method": "m", "premise": "p",
             "applicable_when": "w", "basis": "b", "boundaries": "x",
             "evidence_level": "unverified"}, actor="main")
        self.gov.confirm_lesson("L-01", rev=1, option="keep", quote="保留", source="gui",
                                actor="main")
        conn = self.store.connect()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM gov_lesson_confirmations")
        finally:
            conn.close()

    def test_events_land_in_store_event_stream(self):
        self.frozen()
        conn = self.store.connect()
        try:
            kinds = [row[0] for row in conn.execute(
                "SELECT kind FROM events ORDER BY id").fetchall()]
        finally:
            conn.close()
        self.assertIn("governance_plan_frozen", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
