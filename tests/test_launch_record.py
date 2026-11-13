# GENERATED-BY: R394/R395 公开回归（门槛可分辨性 + 起跑/预检记录），2026-11-13
# LAST-EDIT: 2026-11-13 首版：正反用例 + 「闸不过就一个子进程都不起 / 记录一个都不产生」
# PURPOSE: 证明三件事：① 门槛被对照读数满足时被拒（显式 gate/control 或显式 confirmatory 才新增义务，
#          探索性与旧格式不受影响）；② 记录只在全部当前必需检查通过时产生，且绑定计划内容 / 实际 argv /
#          UTC 时间 / 结果；③ 预检不冒充实际已启动，失败不给可起跑命令、不写记录。
# ORIGIN: 合成临时夹具（同 test_workflow.FlowCase）；⛔ 不联网、不连远端、不读凭据、不含任何真实读数。
# CUMULATIVE-DELTA: 首版。

"""R394（门槛可分辨性）与 R395（起跑 / 预检记录）的公开回归。

全部材料是临时目录里的合成夹具：一个专题、一份计划、一个只写一行日志的 marker 子进程。
这里**不验证科学结论**，只验证「闸有没有拦住」与「记录是不是只在全过时才出现」。
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import governance as g  # noqa: E402
from kernel.autoresearch.workflow import (  # noqa: E402
    RECORD_LAUNCH,
    RECORD_PREFLIGHT,
    WorkflowError,
    launch_record,
)
from kernel.autoresearch import workflow_cli  # noqa: E402
from test_governance import make_plan  # noqa: E402
from test_workflow import (  # noqa: E402
    REVIEWER_SESSION,
    SESSION,
    TOPIC,
    FlowCase,
    audit_report,
)

SHA = "b" * 64


def criteria(**overrides) -> dict:
    """一份健康的确认性判据：门槛 3/6 严格高于对照 1/6（direction=increase ⇒ ge）。"""
    payload = {
        "mode": "confirmatory", "metric": "通过题数", "direction": "increase",
        "threshold": 2, "rationale": "上一轮只差 1 题，门槛取 2 题",
        "gate": {"k": 3, "n": 6, "direction": "ge"},
        "control": {"k": 1, "n": 6, "source": "上一轮同 6 题基线实测 1/6"},
    }
    payload.update(overrides)
    return payload


def passed_gates() -> dict:
    return {"plan": {"version": 1, "key_fingerprint": "fp"},
            "plan_file": {"source": "plans/P1.json", "sha256": "c" * 64},
            "preparation": {"prepared_id": "PREP-1", "plan_sha256": SHA},
            "audit": {"ok": True, "decision": "proceed"},
            "monitor": {"ok": True, "state": "disabled"},
            "authorization": {"ok": True}}


# ---------------------------------------------------------------------------
# 1. R394：门槛不得被对照读数满足
# ---------------------------------------------------------------------------


class CriterionDistinguishabilityTests(unittest.TestCase):
    def test_exploratory_and_legacy_plans_keep_their_semantics(self):
        exploratory = {"mode": "exploratory", "metric": "机制是否生效", "observation": "看到调用记录",
                       "interpretation_boundary": "只判机制存在，不外推幅度",
                       "conditions": "同装置小样本", "stop_condition": "取到调用记录或预算耗尽"}
        # 探索性不强制数字阈值：合法计划零错误
        self.assertEqual(g.validate_plan(make_plan(criteria=exploratory)), [])
        # 旧格式（没写 mode、只有 threshold）：不凭空恢复阈值义务，也不被新检查拦住
        legacy = {"metric": "通过题数", "direction": "increase", "threshold": 2,
                  "source": "上一轮逐题读数"}
        self.assertEqual(g.validate_plan(make_plan(criteria=legacy)), [])
        self.assertEqual(g.criterion_distinguishability_problems({"criteria": legacy}), [])

    def test_explicit_confirmatory_without_material_is_refused(self):
        plan = make_plan(criteria={"mode": "confirmatory", "metric": "通过题数",
                                   "direction": "increase", "threshold": 2,
                                   "rationale": "门槛取 2 题"})
        problems = g.criterion_distinguishability_problems(plan)
        self.assertTrue(any("criteria.gate" in item for item in problems), problems)
        self.assertTrue(any("criteria.control" in item for item in problems), problems)
        errors = g.validate_plan(plan)
        self.assertTrue(any("control" in item for item in errors), errors)

    def test_declared_gate_without_control_is_refused(self):
        plan = make_plan(criteria={"metric": "从未写题数",
                                   "gate": {"k": 0, "n": 6, "direction": "le"}})
        problems = g.criterion_distinguishability_problems(plan)
        self.assertTrue(any("control" in item for item in problems), problems)

    def test_explicit_null_gate_or_control_cannot_bypass_the_check(self):
        """显式写 `gate: null` / `control: null` 也算「声明了」：必须触发义务并被拒。"""
        for mode in (None, "exploratory", "confirmatory"):
            for key in ("gate", "control"):
                with self.subTest(mode=mode, key=key):
                    criteria = {"metric": "量", key: None}
                    if mode == "exploratory":
                        criteria.update(observation="看到调用记录", interpretation_boundary="不外推",
                                        conditions="同装置小样本", stop_condition="取到观察或预算耗尽")
                    elif mode == "confirmatory":
                        criteria.update(direction="increase", threshold=1,
                                        rationale="上一轮只差 1 题")
                    if mode:
                        criteria["mode"] = mode
                    plan = make_plan(criteria=criteria)
                    problems = g.criterion_distinguishability_problems(plan)
                    self.assertTrue(problems, (mode, key))
                    self.assertTrue(any("null" in item for item in problems), (mode, key, problems))
                    self.assertTrue(g.validate_plan(plan), (mode, key))
        # 嵌套 `research.criterion` 写法同样不放过 null
        plan = make_plan()
        plan.pop("criteria")
        plan["research"] = {"criterion": {"gate": None, "control": None}}
        problems = g.criterion_distinguishability_problems(plan)
        self.assertTrue(any("research.criterion.gate" in item for item in problems), problems)
        self.assertTrue(any("research.criterion.control" in item for item in problems), problems)

    def test_ratio_comparison_is_exact_not_float(self):
        """比值用精确分数比较：巨大整数不溢出、近差与相等都不被 float 抹平。"""
        tiny = 10 ** 400  # float(tiny) 会 OverflowError；float 除法根本走不通
        # 2/10^400 严格高于 1/10^400 ⇒ ge 方向放行（float 路线会先溢出）
        allowed = make_plan(criteria={"metric": "量",
                                      "gate": {"k": 2, "n": tiny, "direction": "ge"},
                                      "control": {"k": 1, "n": tiny, "source": "同装置基线"}})
        self.assertEqual(g.criterion_distinguishability_problems(allowed), [])
        self.assertIsInstance(g.validate_plan(allowed), list)  # ⛔ 不许抛 OverflowError
        # 10^18/(10^18+1) 严格小于 1 ⇒ le 方向放行（float 会把两者都变成 1.0 而误拒）
        big = 10 ** 18
        near = make_plan(criteria={"metric": "量",
                                   "gate": {"k": big, "n": big + 1, "direction": "le"},
                                   "control": {"k": 1, "n": 1, "source": "同装置基线"}})
        self.assertEqual(g.criterion_distinguishability_problems(near), [])
        # 精确判等仍然拒：改成精确算法不许把「等于对照」放过
        equal = make_plan(criteria={"metric": "量",
                                    "gate": {"k": 1, "n": tiny, "direction": "le"},
                                    "control": {"k": 1, "n": tiny, "source": "同装置基线"}})
        self.assertTrue(g.criterion_distinguishability_problems(equal))
        # 巨大整数下仍然严格可分：0/10^400 严格小于 1/10^400
        strict = make_plan(criteria={"metric": "量",
                                     "gate": {"k": 0, "n": tiny, "direction": "le"},
                                     "control": {"k": 1, "n": tiny, "source": "同装置基线"}})
        self.assertEqual(g.criterion_distinguishability_problems(strict), [])

    def test_le_gate_looser_than_or_equal_to_control_is_refused(self):
        # 门槛 2/6 高于对照 1/6、门槛 1/6 恰等于对照 1/6：两种都必须拒（等于对照也没有分辨力）
        for gate_k in (2, 1):
            plan = make_plan(criteria={"metric": "从未写题数",
                                       "gate": {"k": gate_k, "n": 6, "direction": "le"},
                                       "control": {"k": 1, "n": 6, "source": "BASE 同 6 题"}})
            problems = g.criterion_distinguishability_problems(plan)
            self.assertTrue(any("门槛被对照读数满足" in item for item in problems),
                            (gate_k, problems))
        stricter = make_plan(criteria={"metric": "从未写题数",
                                       "gate": {"k": 0, "n": 6, "direction": "le"},
                                       "control": {"k": 1, "n": 6, "source": "BASE 同 6 题"}})
        self.assertEqual(g.criterion_distinguishability_problems(stricter), [])

    def test_ge_gate_lower_than_or_equal_to_control_is_refused(self):
        for gate_k in (5, 7):
            plan = make_plan(criteria={"metric": "通过题数",
                                       "gate": {"k": gate_k, "n": 26, "direction": "ge"},
                                       "control": {"k": 7, "n": 26, "source": "BASE 通过数"}})
            self.assertTrue(any("门槛被对照读数满足" in item
                                for item in g.criterion_distinguishability_problems(plan)))
        stricter = make_plan(criteria={"metric": "通过题数",
                                       "gate": {"k": 12, "n": 26, "direction": "ge"},
                                       "control": {"k": 7, "n": 26, "source": "BASE 通过数"}})
        self.assertEqual(g.criterion_distinguishability_problems(stricter), [])

    def test_invalid_material_is_refused(self):
        cases = {
            "n=0": ({"k": 0, "n": 0, "direction": "le"}, {"k": 1, "n": 6, "source": "BASE"}),
            "k>n": ({"k": 7, "n": 6, "direction": "le"}, {"k": 1, "n": 6, "source": "BASE"}),
            "k 是布尔": ({"k": True, "n": 6, "direction": "le"},
                       {"k": 1, "n": 6, "source": "BASE"}),
            "n 是无穷": ({"k": 1, "n": float("inf"), "direction": "le"},
                       {"k": 1, "n": 6, "source": "BASE"}),
            "方向非法": ({"k": 1, "n": 6, "direction": "equal"},
                       {"k": 1, "n": 6, "source": "BASE"}),
            "对照无出处": ({"k": 0, "n": 6, "direction": "le"}, {"k": 1, "n": 6}),
            "对照出处是数字": ({"k": 0, "n": 6, "direction": "le"},
                          {"k": 1, "n": 6, "source": 3}),
        }
        for label, (gate, control) in cases.items():
            with self.subTest(case=label):
                plan = make_plan(criteria={"metric": "量", "gate": gate, "control": control})
                self.assertTrue(g.criterion_distinguishability_problems(plan), label)

    def test_direction_conflict_between_gate_and_criteria_is_refused(self):
        plan = make_plan(criteria={"metric": "通过题数", "direction": "increase",
                                   "gate": {"k": 0, "n": 6, "direction": "le"},
                                   "control": {"k": 1, "n": 6, "source": "BASE"}})
        problems = g.criterion_distinguishability_problems(plan)
        self.assertTrue(any("方向声明冲突" in item for item in problems), problems)

    def test_nested_research_criterion_holder_is_supported(self):
        plan = make_plan()
        plan.pop("criteria")
        plan["research"] = {"criterion": {
            "gate": {"k": 1, "n": 6, "direction": "le"},
            "control": {"k": 1, "n": 6, "source": "BASE 同 6 题"}}}
        problems = g.criterion_distinguishability_problems(plan)
        self.assertTrue(any("research.criterion.gate" in item for item in problems), problems)


# ---------------------------------------------------------------------------
# 2. R395：记录只在全过时产生，且绑定计划 / argv / UTC / 结果
# ---------------------------------------------------------------------------


class LaunchRecordBuilderTests(unittest.TestCase):
    def test_preflight_record_binds_plan_argv_time_and_result(self):
        record = launch_record(kind=RECORD_PREFLIGHT, plan_id="P1", plan_version=2,
                               plan_sha256=SHA, plan_file_sha256="c" * 64,
                               argv=["python", "run.py"], gates=passed_gates(),
                               recorded_at=1_700_000_000)
        self.assertEqual(record["kind"], "preflight")
        self.assertFalse(record["launched"])
        self.assertNotIn("run_id", record)
        self.assertEqual(record["verdict"], "pass")
        self.assertEqual(record["plan"]["sha256"], SHA)
        self.assertEqual(record["plan"]["file_sha256"], "c" * 64)
        self.assertEqual(record["argv"], ["python", "run.py"])
        self.assertEqual(record["recorded_at_utc"], "2023-11-14T22:13:20Z")
        self.assertIn("预检", record["note"])

    def test_preflight_cannot_claim_a_run(self):
        with self.assertRaises(ValueError):
            launch_record(kind=RECORD_PREFLIGHT, plan_id="P1", plan_sha256=SHA,
                          argv=["python"], gates=passed_gates(), recorded_at=1_700_000_000,
                          run_id="RUN-1")

    def test_launch_record_requires_a_run_id(self):
        with self.assertRaises(ValueError):
            launch_record(kind=RECORD_LAUNCH, plan_id="P1", plan_sha256=SHA,
                          argv=["python"], gates=passed_gates(), recorded_at=1_700_000_000)

    def test_record_refuses_any_failed_check(self):
        gates = passed_gates()
        gates["audit"] = {"ok": False, "problems": ["审计在闸之后被撤回"]}
        with self.assertRaises(ValueError) as caught:
            launch_record(kind=RECORD_PREFLIGHT, plan_id="P1", plan_sha256=SHA,
                          argv=["python"], gates=gates, recorded_at=1_700_000_000)
        self.assertIn("audit", str(caught.exception))

        gates = passed_gates()
        gates["monitor"] = {"ok": True, "failures": [{"kind": "monitor"}]}
        with self.assertRaises(ValueError):
            launch_record(kind=RECORD_PREFLIGHT, plan_id="P1", plan_sha256=SHA,
                          argv=["python"], gates=gates, recorded_at=1_700_000_000)

    def test_record_refuses_bad_bindings(self):
        base = dict(kind=RECORD_PREFLIGHT, plan_id="P1", plan_sha256=SHA,
                    argv=["python"], gates=passed_gates(), recorded_at=1_700_000_000)
        for label, override in {
            "空 argv": {"argv": []},
            "argv 不是数组": {"argv": "python run.py"},
            "argv 含空项": {"argv": ["python", ""]},
            "计划哈希缺失": {"plan_sha256": None},
            "计划哈希太短": {"plan_sha256": "abc"},
            "没有 gates": {"gates": {}},
            "时间不是有限数": {"recorded_at": float("nan")},
            "记录类型未知": {"kind": "launched"},
            "附加字段覆盖绑定值": {"extra": {"launched": True}},
        }.items():
            with self.subTest(case=label):
                payload = dict(base, **override)
                with self.assertRaises(ValueError):
                    launch_record(**payload)


# ---------------------------------------------------------------------------
# 3. 主链接线：check 只产出 preflight 记录，start 才产出 launch 记录
# ---------------------------------------------------------------------------


class RecordChainTests(FlowCase):
    def test_check_full_pass_returns_preflight_record_and_starts_nothing(self):
        self.ready()
        result = self.check("plans/P1.json")
        self.assertTrue(result["ok"], result["problems"])
        record = result["preflight_record"]
        self.assertIsInstance(record, dict)
        self.assertEqual(record["kind"], "preflight")
        self.assertFalse(record["launched"])
        self.assertNotIn("run_id", record)
        self.assertEqual(record["plan"]["plan_id"], "P1")
        self.assertEqual(record["argv"], self.argv)
        self.assertEqual(record["verdict"], "pass")
        self.assertTrue(record["plan"]["sha256"])
        self.assertTrue(record["recorded_at_utc"].endswith("Z"))
        # 预检不冒充实际已启动：零 run 记录、零子进程
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_start_returns_launch_record_bound_to_the_run(self):
        built = self.ready()
        result = self.launch(built["plan"])
        record = result["launch_record"]
        self.assertEqual(record["kind"], "launch")
        self.assertTrue(record["launched"])
        self.assertEqual(record["run_id"], result["run_id"])
        self.assertEqual(record["argv"], self.argv)
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            row = conn.execute("SELECT plan_sha256, argv_json FROM wf_runs WHERE run_id=?",
                               (result["run_id"],)).fetchone()
        self.assertEqual(row[0], record["plan"]["sha256"])
        self.assertEqual(json.loads(row[1]), self.argv)

    def test_indistinguishable_criterion_blocks_check_and_start_without_side_effects(self):
        # ge 门槛 1/6 ≤ 对照 3/6：对照自己不做事也可能过线 ⇒ 拒绝（R394）
        payload = self.plan("P9", criteria=criteria(gate={"k": 1, "n": 6, "direction": "ge"},
                                                    control={"k": 3, "n": 6,
                                                             "source": "BASE 同 6 题"}))
        rel = self.write_plan("P9", payload)
        checked = self.check(rel)
        self.assertFalse(checked["ok"])
        self.assertIsNone(checked["preflight_record"])
        self.assertIn("门槛被对照读数满足", "；".join(checked["problems"]))
        with self.assertRaises(WorkflowError):
            self.launch(rel)
        # 拒绝路径：零子进程、零 run 记录
        self.assertEqual(self.marker_lines(), [])
        self.assertEqual(self.run_row_count(), 0)


class MinorFixProgressionTests(FlowCase):
    """R394/R395 的新闸**不**另立「只认 proceed」的标准：审计三态与小修自查照现行机制走。"""

    def test_minor_fix_self_check_still_opens_the_launch_gate(self):
        built = self.prepare()
        prepared = self.flow.gov.prepare_audit("P1", actor="main")
        report = audit_report(prepared["prepared_id"])
        report["decision"] = "minor_fix"
        report["decision_reason"] = "小修后可推进"
        report["issues"] = [{"id": "I1", "category": "design", "blocking": False,
                             "why_blocking": "", "why_not_blocking": "只影响观察项的写法",
                             "problem": "观察写得太笼统", "consequence": "解释边界偏弱",
                             "suggestion": "把观察项写成可核的一句",
                             "sources": [{"path": "evidence/readings.md", "locator": "L1"}]}]
        recorded = self.flow.gov.record_audit(
            prepared["prepared_id"], report, actor=REVIEWER_SESSION,
            evidence={"session_id": REVIEWER_SESSION, "seq": 7})
        state = self.flow.gov.audit_state("P1")
        self.assertEqual(state["audit_decision"], "minor_fix")
        self.assertFalse(state["ok"])  # 意见未处置：现行语义就是先小修，不起跑
        self.flow.gov.correct_audit(
            recorded["report_id"],
            [{"issue_id": "I1", "what": "把观察项改写成可核的一句",
              "self_check": "重读 criteria，确认观察与解释边界对得上"}],
            actor="main")  # 小修必须由 prepare_audit 的负责人（owner）登记
        state = self.flow.gov.audit_state("P1")
        self.assertEqual(state["audit_decision"], "minor_fix")  # 审计判词仍是小修
        checked = self.check(built["plan"])
        self.assertTrue(checked["ok"], checked["problems"])  # 但闸按小修自查机制放行
        self.assertEqual(checked["preflight_record"]["kind"], "preflight")
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])


# ---------------------------------------------------------------------------
# 4. CLI：preflight 入口 + 记录落盘口径 + 失败不泄露可起跑命令
# ---------------------------------------------------------------------------


class PreflightCliTests(FlowCase):
    def cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = workflow_cli.main(argv)
        return code, out.getvalue()

    def test_cli_preflight_refusal_writes_no_record_and_no_command(self):
        payload = self.plan("P9", criteria=criteria(gate={"k": 1, "n": 6, "direction": "ge"},
                                                    control={"k": 3, "n": 6,
                                                             "source": "BASE 同 6 题"}))
        rel = self.write_plan("P9", payload)
        record_path = self.ws / "preflight" / "P9.json"
        code, output = self.cli(["preflight", "--workspace", str(self.ws), "--topic", TOPIC,
                                 "--plan", rel, "--owner-session", SESSION, "--intent-none",
                                 "--record-out", "preflight/P9.json", "--"] + self.argv)
        self.assertEqual(code, 9, output)
        printed = json.loads(output)
        self.assertFalse(printed["ok"])
        self.assertIsNone(printed["record"])
        self.assertIsNone(printed["launch_command"])
        self.assertFalse(record_path.exists())
        for needle in ("ssh", "nohup", str(self.marker)):
            self.assertNotIn(needle, output)
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_preflight_payload_only_returns_record_when_all_checks_pass(self):
        record = launch_record(kind=RECORD_PREFLIGHT, plan_id="P1", plan_sha256=SHA,
                               argv=["python"], gates=passed_gates(),
                               recorded_at=1_700_000_000)
        payload, code = workflow_cli._preflight_payload(
            {"ok": True, "preflight_record": record})
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["launch_command"])
        self.assertEqual(payload["record"]["kind"], "preflight")

        payload, code = workflow_cli._preflight_payload(
            {"ok": False, "problems": ["门槛被对照读数满足"]})
        self.assertEqual(code, 9)
        self.assertIsNone(payload["record"])
        self.assertIsNone(payload["launch_command"])

        payload, code = workflow_cli._preflight_payload(
            {"ok": False, "undecidable": True, "problems": ["缺库"]})
        self.assertEqual(code, 2)
        self.assertTrue(payload["undecidable"])

    def test_record_out_writer_stays_inside_the_workspace(self):
        record = launch_record(kind=RECORD_PREFLIGHT, plan_id="P1", plan_sha256=SHA,
                               argv=["python"], gates=passed_gates(),
                               recorded_at=1_700_000_000)
        rel = workflow_cli._write_preflight_record(self.flow, "preflight/P1.json", record)
        self.assertEqual(rel, "preflight/P1.json")
        written = json.loads((self.ws / "preflight" / "P1.json").read_text(encoding="utf-8"))
        self.assertEqual(written["kind"], "preflight")
        for outside in ("../outside.json", str(self.ws / "absolute.json")):
            with self.assertRaises(WorkflowError):
                workflow_cli._write_preflight_record(self.flow, outside, record)


if __name__ == "__main__":
    unittest.main()
