# GENERATED-BY: research execution main chain migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：J1–J6 / K1 起跑检查 / I7 的集成测试（真实子进程 marker）
# PURPOSE: 证明主链的三件事 —— ① 任何拒绝路径都不会启动子进程；② 通过后真的启动并保留退出码；
#          ③ 来源变更 / touch / 前轮未复盘 / 事后补造 / 并发重复启动都被拦住（含数据库层）。
# ORIGIN: 合成临时工作区夹具；⛔ 不含任何真实实验、机器、读数、渠道或比赛信息。
# CUMULATIVE-DELTA: 首版。宿主核验器与监控检查在本文件内是可验证夹具（不是生产实现）。

"""研究执行主链的集成测试。

所有材料都是**临时目录里的合成夹具**：

* 一个专题（`T1`）与它的主文档 `topics/T1/TOPIC.md`；
* 一份计划（四锚点 + `implementation` 五项/files + `execution_argv`）；
* 一个真实被启动的 marker 子进程（写一行日志并按给定退出码退出）；
* 审计核验器与监控检查的**夹具**（生产实现是 `host_evidence.DSHHost` 与 `Monitoring`）。

判据（不是「跑通就行」）：
1. 任何一条拒绝路径之后，`wf_runs` 为空、marker 日志不存在；
2. 通过之后 marker 日志真的出现，且 run 记录里的 `exitcode` 等于子进程真实退出码；
3. 只 `touch` 不改内容 **不**使准备过期；改一个字节就过期；
4. 同专题前轮未复盘 ⇒ 新轮拒绝；复盘后放行；
5. 已启动过的轮次不能再 prepare / 再启动；
6. 两个操作系统进程同时 launch ⇒ 只有一个真的启动。
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import governance as g  # noqa: E402
from kernel.autoresearch.admission import Admission  # noqa: E402
from kernel.autoresearch.runtime.accounts import (  # noqa: E402
    Accounts,
    ApprovalEvidence,
    HMACSource,
    SourcePolicy,
)
from kernel.autoresearch.runtime.store import Store, canonical_json  # noqa: E402
from kernel.autoresearch.runtime.topics import Topics  # noqa: E402
from kernel.autoresearch.workflow import Workflow, WorkflowError  # noqa: E402

TOPIC = "T1"
SESSION = "session-owner"
REVIEWER_SESSION = "session-reviewer"
MARKER_SRC = (
    "import pathlib, sys, time\n"
    "out = pathlib.Path(sys.argv[1])\n"
    "label = sys.argv[2]\n"
    "code = int(sys.argv[3])\n"
    "delay = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0\n"
    "if delay:\n"
    "    time.sleep(delay)\n"
    "with out.open('a', encoding='utf-8') as handle:\n"
    "    handle.write(label + '\\n')\n"
    "raise SystemExit(code)\n"
)

BRANCH_ACTIONS = {
    "support": "保留改动，进入下一轮",
    "contradict": "回退改动，改假设",
    "inconclusive": "补一轮重复读数，不改配置",
}


class VerifiedHost:
    """审计核验器夹具：证明「传对象、Governance 调 .verify」这条链路。

    ⛔ 它不是生产核验器。生产用 `host_evidence.DSHHost`（读真实宿主日志），
    没有真实证据时只会是 `declared` ⇒ 起跑被拒。

    协议跟 `Governance._verify_independence` 一致：证据只给 `{session_id, seq}` 的 proof，
    Governance 自己构造 `{proof, owner_session, prepared_id, report}` 交给核验器，
    核验器返回 `{'verified': True, 'session_id': …}`。

    两种 payload 形状都认（治理层正在迁移，夹具对两种都成立，**不**放宽任何真实规则）：
      * 未接线形状：`verify("audit_independence", evidence)`，evidence 本身就是 proof；
      * 已接线形状：`{proof, owner_session, prepared_id, report}`。
    令牌核验器若要求绑定 owner / prepared_id，把回显字段带上即可。
    """

    def __init__(self):
        self.calls = []

    def verify(self, kind, payload):
        self.calls.append((kind, payload))
        if kind != "audit_independence" or not isinstance(payload, dict):
            return None
        wrapped = payload.get("proof")
        proof = wrapped if isinstance(wrapped, dict) else payload
        session = proof.get("session_id")
        if not session:
            return None
        return {"verified": True, "kind": kind, "session_id": session,
                "prepared_id": payload.get("prepared_id") or proof.get("prepared_id"),
                "owner_session": payload.get("owner_session")}


def audit_report(prepared_id):
    return {
        "prepared_id": prepared_id,
        "coverage": {category: {"verdict": "ok", "note": f"{category} 已核",
                                "sources": [{"path": "evidence/readings.md", "locator": "L1"}]}
                     for category in g.AUDIT_CATEGORIES},
        "issues": [],
        "resolves": [],
        "decision": "proceed",
        "decision_reason": "目标连接、设计与历史约束都可核",
        "focus": {"question_goal": "与专题目标相连",
                  "design_interpretation": "设计与解释边界可核",
                  "history_constraints": "已检索历史与限制"},
    }


class FlowCase(unittest.TestCase):
    """每个测试一个临时工作区；计划 / 审计 / marker 都现造。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "ws"
        self.ws.mkdir()
        self.db = self.ws / ".autoresearch" / "runtime.sqlite3"
        self.db.parent.mkdir()
        self.store = Store(self.db)
        self.topics = Topics(self.store)
        self.topics.create_topic(TOPIC, title="提高通过题数", objective="把通过题数从 3 提到 4")
        self.topic_dir = self.ws / "topics" / TOPIC
        self.topic_dir.mkdir(parents=True)
        self.doc = self.topic_dir / "TOPIC.md"
        self.doc.write_text("# T1\n\n## 2 假设与假设迭代\n\n- H1：收紧入口参数能提高通过题数（待验证）\n",
                            encoding="utf-8")
        (self.ws / "evidence").mkdir()
        self.readings = self.ws / "evidence" / "readings.md"
        self.readings.write_text("上一轮读数：通过题数 3（逐题核对）\n", encoding="utf-8")
        (self.ws / "submission").mkdir()
        self.entry = self.ws / "submission" / "entry.py"
        self.entry.write_text("def parse_args(argv):\n    return argv\n", encoding="utf-8")
        (self.ws / "plans").mkdir()
        self.marker = self.ws / "marker.py"
        self.marker.write_text(MARKER_SRC, encoding="utf-8")
        self.marker_log = self.ws / "marker.log"
        self.argv = [sys.executable, str(self.marker), str(self.marker_log), "A", "0"]
        self.verifier = VerifiedHost()
        self.monitor_calls = []
        self.authorization_requests = []
        self.flow = self.make_flow()
        # 来源会话必须**已登记**（Governance.check_start 对未登记会话判「不可判 ⇒ 不放行」）：
        # 先同步一条真实原话，再按用户回答登记「不立为跨轮意图」。
        self.flow.gov.sync_user_update(source_session=SESSION, seq=1,
                                       quote="先把通过题数从 3 提到 4，别动题单")
        self.flow.gov.assess_update(source_session=SESSION, covers_seq=1, decision="none",
                                    quote="这句不立为跨轮意图，只是本轮目标", answer_seq=2,
                                    actor="main")

    def tearDown(self):
        self._tmp.cleanup()

    # -- 夹具 ---------------------------------------------------------------

    def monitor_ok(self, owner_session, exp_id, action="launch"):
        self.monitor_calls.append((owner_session, exp_id, action))
        # 完整监控协议：registered / state / intent_id 一个都不能少（裸 {"ok": True} 会被拒）。
        return {"ok": True, "problems": [], "registered": True,
                "state": "none", "intent_id": None}

    def monitor_active(self, intent_id):
        """夹具：监控当前有一条 active 意图（用于 --intent-id 与真实 id 核对）。"""
        def check(owner_session, exp_id, action="launch"):
            self.monitor_calls.append((owner_session, exp_id, action))
            return {"ok": True, "problems": [], "registered": True,
                    "state": "active", "intent_id": intent_id}
        return check

    def authorization_ok(self, plan, owner_session, exp_id):
        """执行准入夹具。协议与 `Admission.check(plan, owner_session, exp_id)` 一致。"""
        self.authorization_requests.append((plan.get("id"), owner_session, exp_id))
        return {"ok": True, "problems": [], "decision": "test-fixture", "scope": "small"}

    def make_flow(self, **kwargs):
        kwargs.setdefault("verifier", self.verifier)
        kwargs.setdefault("monitor_check", self.monitor_ok)
        # ⚠️ 只有测试夹具才这样构造：显式声明「这一轮的监控 / 准入检查是夹具」。
        kwargs.setdefault("require_monitor_check", False)
        kwargs.setdefault("authorization_check", self.authorization_ok)
        kwargs.setdefault("require_authorization_check", False)
        return Workflow(self.ws, **kwargs)

    def plan(self, plan_id="P1", *, argv=None, retry=None, criteria=None,
             hypothesis_id="H1", hypothesis_text=None, implementation=None,
             execution_argv="__default__", input_files=None, budget=None, authorization=None):
        payload = {
            "id": plan_id,
            "goal": "验证收紧入口参数是否提高通过题数",
            "hypothesis": {"id": hypothesis_id,
                           "text": hypothesis_text or "收紧入口参数会让通过题数至少多 1 题"},
            "change_set": [{"id": "c1", "description": "把入口参数校验改成只接受整数",
                            "hypothesis_id": hypothesis_id}],
            "criteria": criteria or {
                "mode": "confirmatory", "metric": "通过题数", "direction": "increase",
                "threshold": 1, "source": "evidence/readings.md 上一轮逐题读数",
                "rationale": "上一轮只差 1 题，门槛取 1"},
            "baseline": {"label": "上一轮冻结包", "fingerprint": "sha256:deadbeef"},
            "case_sets": {"target": ["t1", "t2"], "regression": ["r1", "r2"], "control": ["c1"]},
            "previous_passes": ["r1"],
            "branches": {kind: {"action": action} for kind, action in BRANCH_ACTIONS.items()},
            "budget": dict(budget) if budget is not None else {"max_cost_units": 1, "max_seconds": 60},
            "input_files": ["evidence/readings.md"] if input_files is None else input_files,
            "implementation": implementation or {
                "expected_behavior": "入口只接受整数时非法参数被拒绝",
                "load_path": "submission/entry.py 的 parse_args",
                "trigger": "评测器传入字符串参数时",
                "observation": "非法参数返回非零退出码并打印原因",
                "non_activation": "非法参数仍被接受并继续运行",
                "files": [{"path": "submission/entry.py", "locator": "L1-L2"}],
            },
            # 计划顶层声明的执行授权（Admission 核它；本层只记录与绑定）。
            "authorization": dict(authorization) if authorization is not None else {
                "scope": "small", "series_id": "S-1", "reservation_id": "R-1"},
        }
        if execution_argv == "__default__":
            payload["execution_argv"] = list(argv if argv is not None else self.argv)
        else:
            payload["execution_argv"] = execution_argv
        if retry is not None:
            payload["retry"] = retry
        return payload

    def write_plan(self, plan_id, payload):
        path = self.ws / "plans" / f"{plan_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"plans/{plan_id}.json"

    def prepare(self, plan_id="P1", *, flow=None, **plan_kwargs):
        flow = flow or self.flow
        payload = self.plan(plan_id, **plan_kwargs)
        rel = self.write_plan(plan_id, payload)
        result = flow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION)
        return {"plan": rel, "payload": payload, "prepared": result}

    def audit(self, plan_id="P1", *, flow=None):
        flow = flow or self.flow
        prepared = flow.gov.prepare_audit(plan_id, actor="main")
        flow.gov.record_audit(
            prepared["prepared_id"], audit_report(prepared["prepared_id"]),
            actor=REVIEWER_SESSION,
            # 宿主证据只给 proof（{session_id, seq}）；expected/report 由 Governance 自己构造。
            evidence={"session_id": REVIEWER_SESSION, "seq": 7})
        return prepared

    def ready(self, plan_id="P1", *, flow=None, **plan_kwargs):
        flow = flow or self.flow
        built = self.prepare(plan_id, flow=flow, **plan_kwargs)
        self.audit(plan_id, flow=flow)
        return built

    def check(self, plan, *, flow=None, argv=None, owner_session=SESSION, intent_none=True):
        flow = flow or self.flow
        return flow.check(topic=TOPIC, plan=plan, owner_session=owner_session,
                          argv=self.argv if argv is None else argv, intent_none=intent_none)

    def launch(self, plan, *, flow=None, argv=None, finish=True):
        flow = flow or self.flow
        return flow.start(topic=TOPIC, plan=plan, owner_session=SESSION,
                          argv=self.argv if argv is None else argv, intent_none=True,
                          finish=finish)

    def run_row_count(self):
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            return conn.execute("SELECT COUNT(*) FROM wf_runs").fetchone()[0]

    def marker_lines(self):
        if not self.marker_log.is_file():
            return []
        return [line for line in self.marker_log.read_text(encoding="utf-8").splitlines() if line]

    def review_record(self, *, verdict="supported", branch="support", next_action=None,
                      evidence="__default__", deviations="没有", locator="判决", **extra):
        record = {
            "result_summary": "逐题读数显示通过题数 3 → 4（同装置、同题单）",
            "verdict": verdict,
            "branch": branch,
            "next_action": next_action or BRANCH_ACTIONS[branch],
            "deviations": deviations,
            "evidence": ([{"path": "evidence/readings.md", "locator": "L1"}]
                         if evidence == "__default__" else evidence),
            "hypothesis_update": {"path": f"topics/{TOPIC}/TOPIC.md", "locator": locator},
        }
        record.update(extra)
        return record

    def append_doc(self, verdict="supported"):
        with self.doc.open("a", encoding="utf-8") as handle:
            handle.write(f"- H1 判决 {verdict}：P1 读数 3 → 4，条件同上一轮\n")

    def finished_run(self, plan_id="P1"):
        built = self.ready(plan_id)
        result = self.launch(built["plan"])
        return built, result


class PreparationTests(FlowCase):
    def test_incomplete_materials_are_rejected_before_any_record(self):
        cases = {}
        payload = self.plan()
        payload.pop("implementation")
        cases["缺实现声明"] = payload
        payload = self.plan()
        payload["hypothesis"]["text"] = "待填"
        cases["假设占位"] = payload
        payload = self.plan()
        payload["criteria"].pop("rationale")
        cases["确认性无理由"] = payload
        payload = self.plan()
        payload["criteria"]["threshold"] = True
        cases["阈值是布尔"] = payload
        payload = self.plan(execution_argv="python run.py --task-list x")
        cases["argv 是字符串"] = payload
        payload = self.plan()
        payload["implementation"]["files"] = [{"path": "../outside.py", "locator": "L1"}]
        cases["实现文件越界"] = payload
        payload = self.plan()
        payload["implementation"]["non_activation"] = "没有证据说明没生效"
        cases["非生效证据空话"] = payload
        for label, payload in cases.items():
            with self.subTest(case=label):
                rel = self.write_plan("PX", payload)
                with self.assertRaises(WorkflowError) as caught:
                    self.flow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION)
                self.assertEqual(caught.exception.code, 2)
                self.assertTrue(caught.exception.errors or str(caught.exception))
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_exploratory_needs_stop_condition_without_numeric_threshold(self):
        criteria = {"mode": "exploratory", "metric": "机制是否生效", "observation": "看到调用记录",
                    "interpretation_boundary": "只判机制存在，不外推幅度",
                    "conditions": "同装置小样本", "stop_condition": "取到调用记录或预算耗尽",
                    "source": "docs/design.md"}
        built = self.prepare(criteria=criteria)
        self.assertTrue(built["prepared"]["ok"])
        bad = dict(criteria)
        bad.pop("stop_condition")
        rel = self.write_plan("P2", self.plan("P2", criteria=bad))
        with self.assertRaises(WorkflowError) as caught:
            self.flow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION)
        self.assertIn("stop_condition", "；".join(caught.exception.errors or [str(caught.exception)]))

    def test_unregistered_topic_and_missing_topic_doc_are_rejected(self):
        rel = self.write_plan("P1", self.plan())
        with self.assertRaises(WorkflowError):
            self.flow.prepare(topic="T9", plan=rel, owner_session=SESSION)
        self.doc.unlink()
        with self.assertRaises(WorkflowError) as caught:
            self.flow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION)
        self.assertIn("专题主文档", "；".join(caught.exception.errors or [str(caught.exception)]))


class GateTests(FlowCase):
    def test_omitted_session_intent_and_argv_never_pass(self):
        built = self.ready()
        problems = self.check(built["plan"], owner_session="")["problems"]
        self.assertTrue(any("owner_session 必填" in item for item in problems), problems)
        gate = self.flow.check(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                               argv=self.argv, intent_id=None, intent_none=False)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("必须显式声明监控入口" in item for item in gate["problems"]), gate["problems"])
        gate = self.flow.check(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                               argv=None, intent_none=True)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("必须给出实际 argv" in item for item in gate["problems"]), gate["problems"])

    def test_argv_must_match_declared_exactly(self):
        built = self.ready()
        wrong = list(self.argv)
        wrong[3] = "B"
        gate = self.check(built["plan"], argv=wrong)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("argv" in item and "不一致" in item for item in gate["problems"]),
                        gate["problems"])
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"], argv=wrong)
        self.assertEqual(caught.exception.code, 9)
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_missing_audit_blocks_start(self):
        built = self.prepare()  # 故意不审计
        gate = self.check(built["plan"])
        self.assertFalse(gate["ok"])
        self.assertTrue(any("审计" in item for item in gate["problems"]), gate["problems"])
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"])
        self.assertEqual(caught.exception.code, 9)
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_declared_independence_is_refused_when_verifier_absent(self):
        # 没有核验器 ⇒ 审计只能记 declared ⇒ require_verified_independence 让它不算通过。
        flow = self.make_flow(verifier=None)
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        self.assertEqual(flow.gov.audit_state("P1")["independence"], "declared")
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("independence" in item or "独立性" in item for item in gate["problems"]),
                        gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"], flow=flow)
        self.assertEqual(self.run_row_count(), 0)

    def test_unwired_monitor_fails_closed(self):
        flow = self.make_flow(monitor_check=None, require_monitor_check=True)
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("监控检查未接线" in item for item in gate["problems"]), gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"], flow=flow)
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_monitor_rejection_blocks_and_is_recorded(self):
        flow = self.make_flow(monitor_check=lambda owner, exp, action="launch":
                              {"ok": False, "problems": ["意图报告过期"],
                               "registered": True, "state": "none", "intent_id": None})
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("意图报告过期" in item for item in gate["problems"]), gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"], flow=flow)
        self.assertEqual(self.marker_lines(), [])

    def test_monitor_fixture_must_return_full_protocol(self):
        """裸 `{"ok": True}` 不再算监控通过：必须带 registered / state / intent_id。"""
        flow = self.make_flow(monitor_check=lambda *a, **k: {"ok": True, "problems": []})
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("registered" in item for item in gate["problems"]), gate["problems"])
        self.assertFalse(flow.check(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                                    argv=self.argv, intent_none=True).get("checks", {})
                         .get("monitor", {}).get("ok", False))

    def test_declared_intent_must_match_monitoring_current_id(self):
        flow = self.make_flow(monitor_check=self.monitor_active("I1"))
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        wrong = flow.check(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                           argv=self.argv, intent_id="I2", intent_none=False)
        self.assertFalse(wrong["ok"])
        self.assertTrue(any("不一致" in item for item in wrong["problems"]), wrong["problems"])
        right = flow.check(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                           argv=self.argv, intent_id="I1", intent_none=False)
        self.assertTrue(right["ok"], right["problems"])
        self.assertEqual(right["checks"]["monitor"]["intent_id"], "I1")
        none = flow.check(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                          argv=self.argv, intent_none=True)
        self.assertFalse(none["ok"])
        self.assertTrue(any("intent-none" in item or "专项意图" in item for item in none["problems"]),
                        none["problems"])

    def test_monitoring_is_the_only_intent_authority(self):
        """不再叠加旧的 Governance.check_start：调它应当根本不发生。"""
        built = self.ready()
        def legacy_gate(**_kwargs):
            raise AssertionError("旧的 Governance.check_start 不该接在生产主链上")
        self.flow.gov.check_start = legacy_gate
        gate = self.check(built["plan"])
        self.assertTrue(gate["ok"], gate["problems"])
        self.assertEqual(gate["checks"]["intent"]["authority"], "monitoring")

    def test_prepare_is_not_repurposed_as_a_start(self):
        built = self.prepare()
        self.assertFalse((self.ws / "marker.log").exists())
        self.assertEqual(self.run_row_count(), 0)
        self.assertTrue(built["prepared"]["ok"])


class LaunchTests(FlowCase):
    def test_real_subprocess_marker_and_exitcode_preserved(self):
        built = self.ready()
        result = self.launch(built["plan"], argv=self.argv)
        self.assertTrue(result["ok"])
        self.assertEqual(result["exitcode"], 0)
        self.assertEqual(result["reason"], "completed")
        self.assertEqual(self.marker_lines(), ["A"])
        status = self.flow.status(topic=TOPIC)
        run = status["runs"][0]
        self.assertEqual(run["exitcode"], 0)
        self.assertEqual(run["reason"], "completed")
        self.assertTrue(run["finished"])
        self.assertFalse(run["reviewed"])
        self.assertEqual(status["pending_reports"], [run["run_id"]])
        # 运行结束 ≠ 报告交付：待复盘队列里还有它。
        self.assertEqual(run["documentation_status"], "skipped")

    def test_nonzero_exitcode_is_kept(self):
        argv = [sys.executable, str(self.marker), str(self.marker_log), "B", "3"]
        built = self.ready(argv=argv)
        result = self.launch(built["plan"], argv=argv)
        self.assertEqual(result["exitcode"], 3)
        self.assertEqual(result["reason"], "failed")
        status = self.flow.status(topic=TOPIC)
        self.assertEqual(status["runs"][0]["exitcode"], 3)
        self.assertEqual(status["runs"][0]["reason"], "failed")

    def test_signal_negative_exitcode_is_kept(self):
        argv = [sys.executable, str(self.marker), str(self.marker_log), "S", "-9"]
        built = self.ready(argv=argv)
        result = self.launch(built["plan"], argv=argv)
        self.assertEqual(result["exitcode"], -9)
        self.assertEqual(result["reason"], "interrupted")
        self.assertEqual(self.flow.status(topic=TOPIC)["runs"][0]["signal"], "signal:9")

    def test_same_round_cannot_be_launched_twice(self):
        built = self.ready()
        self.launch(built["plan"])
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"])
        self.assertEqual(caught.exception.code, 9)
        self.assertEqual(self.marker_lines(), ["A"])
        self.assertEqual(self.run_row_count(), 1)


class ExpiryTests(FlowCase):
    def test_touch_does_not_expire_but_content_change_does(self):
        built = self.ready()
        self.assertTrue(self.check(built["plan"])["ok"])
        future = time.time() + 3600
        os.utime(self.readings, (future, future))
        os.utime(self.doc, (future, future))
        gate = self.check(built["plan"])
        self.assertTrue(gate["ok"], gate["problems"])  # 只看 sha256，不看 mtime
        self.readings.write_text("上一轮读数：通过题数 3 → 4（逐题核对）\n", encoding="utf-8")
        gate = self.check(built["plan"])
        self.assertFalse(gate["ok"])
        self.assertTrue(any("来源内容变了" in item or "过期" in item for item in gate["problems"]),
                        gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"])
        self.assertEqual(self.marker_lines(), [])
        refreshed = self.prepare()
        self.assertTrue(refreshed["prepared"]["ok"])
        self.assertTrue(self.check(refreshed["plan"])["ok"])

    def test_topic_objective_and_doc_change_expire(self):
        built = self.ready()
        topic = self.topics.get_topic(TOPIC)
        self.topics.set_topic_fields(TOPIC, topic["version"], objective="把通过题数从 3 提到 5")
        self.assertFalse(self.check(built["plan"])["ok"])
        refreshed = self.prepare()
        self.assertTrue(self.check(refreshed["plan"])["ok"], self.check(refreshed["plan"])["problems"])
        self.doc.write_text(self.doc.read_text(encoding="utf-8") + "- 目标补充：只改入口校验\n",
                            encoding="utf-8")
        gate = self.check(refreshed["plan"])
        self.assertFalse(gate["ok"])
        self.assertTrue(any("专题主文档" in item for item in gate["problems"]), gate["problems"])

    def test_new_user_quote_expires_and_blocks_launch(self):
        built = self.ready()
        # 用户又发了一句（seq 3，晚于 setUp 里已评估的那条）：已同步但还没评估。
        self.flow.gov.sync_user_update(source_session=SESSION, seq=3,
                                       quote="再加一条：别改题单，只动入口校验")
        gate = self.check(built["plan"])
        self.assertFalse(gate["ok"])
        # J2：prepare 快照里的用户原话变了 ⇒ 事前材料过期。
        self.assertTrue(any("用户原话" in item for item in gate["problems"]), gate["problems"])
        # 意图权威已统一到监控：这里不再出现旧的 Governance 意图闸条目。
        self.assertFalse(any(item.startswith("意图监控：") for item in gate["problems"]),
                         gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"])
        self.assertEqual(self.run_row_count(), 0)

    def test_plan_edit_after_prepare_expires(self):
        built = self.ready()
        payload = json.loads((self.ws / built["plan"]).read_text(encoding="utf-8"))
        payload["notes"] = "补一句叙述"
        (self.ws / built["plan"]).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        gate = self.check(built["plan"])
        self.assertFalse(gate["ok"])
        self.assertTrue(any("已冻结版本不一致" in item for item in gate["problems"]), gate["problems"])


class RoundLifecycleTests(FlowCase):
    def test_previous_round_without_review_blocks_new_round(self):
        built = self.ready("P1")
        self.launch(built["plan"])
        with self.assertRaises(WorkflowError) as caught:
            self.prepare("P2")
        self.assertIn("未复盘", "；".join(caught.exception.errors or [str(caught.exception)]))
        self.append_doc("supported")
        self.flow.review(run_id=self.flow.status(topic=TOPIC)["runs"][0]["run_id"],
                         record=self.review_record())
        prepared = self.prepare("P2")
        self.assertTrue(prepared["prepared"]["ok"])

    def test_rejected_hypothesis_retry_needs_new_condition(self):
        built = self.ready("P1")
        self.launch(built["plan"])
        self.append_doc("rejected")
        self.flow.review(run_id=self.flow.status(topic=TOPIC)["runs"][0]["run_id"],
                         record=self.review_record(verdict="rejected", branch="contradict"))
        with self.assertRaises(WorkflowError) as caught:
            self.prepare("P2")
        self.assertIn("已被否定", "；".join(caught.exception.errors or [str(caught.exception)]))
        retry = {"of": "P1", "new_condition": "这次同时固定随机种子，只改入口校验",
                 "sources": [{"path": "evidence/readings.md", "locator": "L1"}]}
        prepared = self.prepare("P2", retry=retry)
        self.assertTrue(prepared["prepared"]["ok"])

    def test_runs_cannot_be_backfilled_with_prehoc_materials(self):
        built = self.ready("P1")
        self.launch(built["plan"])
        self.assertEqual(self.run_row_count(), 1)
        with self.assertRaises(WorkflowError) as caught:
            self.prepare("P1")
        self.assertIn("已有实际启动记录", "；".join(caught.exception.errors or [str(caught.exception)]))
        # 再 prepare 一次也不行（连来源都没改）。
        with self.assertRaises(WorkflowError):
            self.prepare("P1")
        self.assertEqual(self.run_row_count(), 1)

    def test_open_run_blocks_new_round_in_the_same_topic(self):
        built = self.ready("P1")
        self.launch(built["plan"], finish=False)
        self.assertEqual(self.run_row_count(), 1)
        with self.assertRaises(WorkflowError) as caught:
            self.prepare("P2")
        self.assertIn("未结束运行", "；".join(caught.exception.errors or [str(caught.exception)]))
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"])

    def test_other_topic_is_independent(self):
        self.topics.create_topic("T2", title="另一个专题", objective="减少推理时间")
        other_dir = self.ws / "topics" / "T2"
        other_dir.mkdir()
        (other_dir / "TOPIC.md").write_text("# T2\n\n- H9：减少推理时间\n", encoding="utf-8")
        built = self.ready("P1")
        self.launch(built["plan"])
        payload = self.plan("Q1")
        rel = self.write_plan("Q1", payload)
        prepared = self.flow.prepare(topic="T2", plan=rel, owner_session=SESSION)
        self.assertTrue(prepared["ok"])
        self.assertEqual(self.run_row_count(), 1)


class FinishTests(FlowCase):
    def test_finish_records_reason_and_documentation_failure_keeps_exitcode(self):
        built = self.ready()
        started = self.launch(built["plan"], finish=False)
        self.assertFalse(started["finished"])
        self.assertEqual(self.run_row_count(), 1)
        recorded = self.flow.finish(run_id=started["run_id"], exitcode=0, reason="completed",
                                    documentation={"status": "failed",
                                                  "detail": "scribe 页面没有更新"})
        self.assertEqual(recorded["exitcode"], 0)  # 文档失败不改退出码
        self.assertEqual(recorded["documentation"]["status"], "failed")
        status = self.flow.status(topic=TOPIC)
        self.assertEqual(status["runs"][0]["exitcode"], 0)
        self.assertEqual([item["run_id"] for item in status["documentation_failures"]],
                         [started["run_id"]])
        self.assertEqual(status["pending_reports"], [started["run_id"]])
        with self.assertRaises(WorkflowError):
            self.flow.finish(run_id=started["run_id"], exitcode=0, reason="completed")

    def test_finish_rejects_inconsistent_reason_and_exitcode(self):
        built = self.ready()
        started = self.launch(built["plan"], finish=False)
        for exitcode, reason in ((0, "failed"), (3, "completed"), (0, "timeout")):
            with self.subTest(exitcode=exitcode, reason=reason):
                with self.assertRaises(WorkflowError):
                    self.flow.finish(run_id=started["run_id"], exitcode=exitcode, reason=reason)
        with self.assertRaises(WorkflowError):
            self.flow.finish(run_id="RUN-missing", exitcode=0, reason="completed")

    def test_review_requires_a_finished_run(self):
        built = self.ready()
        started = self.launch(built["plan"], finish=False)
        with self.assertRaises(WorkflowError) as caught:
            self.flow.review(run_id=started["run_id"], record=self.review_record())
        self.assertIn("结束记录", "；".join(caught.exception.errors or [str(caught.exception)]))


class ReviewTests(FlowCase):
    def test_review_needs_actual_page_update_and_valid_evidence(self):
        built, run = self.finished_run()
        # 1) 主文档没更新 ⇒ 判决没落账（locator 本身在原文档里是有效的）
        with self.assertRaises(WorkflowError) as caught:
            self.flow.review(run_id=run["run_id"], record=self.review_record(locator="H1"))
        self.assertIn("没有实际落账", str(caught.exception))
        # 2) 更新了主文档，但证据指向不存在的文件
        self.append_doc("supported")
        with self.assertRaises(WorkflowError) as caught:
            self.flow.review(run_id=run["run_id"],
                             record=self.review_record(evidence=[{"path": "evidence/none.md",
                                                                  "locator": "L1"}]))
        self.assertEqual(caught.exception.code, 2)
        # 3) 下一步偏离事前分支却写「没有偏离」
        with self.assertRaises(WorkflowError) as caught:
            self.flow.review(run_id=run["run_id"],
                             record=self.review_record(next_action="直接换一个新方向"))
        self.assertIn("偏离", str(caught.exception))
        # 4) 合规
        sent = []
        flow = self.make_flow(event_sink=lambda kind, payload: sent.append((kind, payload["run_id"])))
        ok = flow.review(run_id=run["run_id"], record=self.review_record())
        self.assertTrue(ok["ok"])
        self.assertEqual([kind for kind, _ in sent], ["result_analysis", "evidence"])
        self.assertEqual(sent[0][1], run["run_id"])
        self.assertTrue(ok["event_sink"]["wired"])
        with self.assertRaises(WorkflowError):
            flow.review(run_id=run["run_id"], record=self.review_record())

    def test_event_sink_failure_does_not_undo_review(self):
        built, run = self.finished_run()
        self.append_doc("supported")

        def broken(kind, payload):
            raise RuntimeError("监控写不进去")

        flow = self.make_flow(event_sink=broken)
        result = flow.review(run_id=run["run_id"], record=self.review_record())
        self.assertTrue(result["ok"])
        self.assertFalse(result["event_sink"]["ok"])
        self.assertTrue(flow.status(topic=TOPIC)["runs"][0]["reviewed"])

    def test_review_rejects_rewritten_plan(self):
        built, run = self.finished_run()
        self.append_doc("supported")
        payload = self.plan()
        payload["notes"] = "事后补一句叙述"
        self.flow.gov.freeze_plan(payload, actor="main")
        with self.assertRaises(WorkflowError) as caught:
            self.flow.review(run_id=run["run_id"], record=self.review_record())
        self.assertIn("改写", str(caught.exception))

    def test_review_rejects_foreign_document_target(self):
        built, run = self.finished_run()
        other = self.ws / "evidence" / "other.md"
        other.write_text("- 判决 supported\n", encoding="utf-8")
        record = self.review_record()
        record["hypothesis_update"] = {"path": "evidence/other.md", "locator": "判决"}
        with self.assertRaises(WorkflowError) as caught:
            self.flow.review(run_id=run["run_id"], record=record)
        self.assertIn("专题主文档", str(caught.exception))


class PathAndLedgerTests(FlowCase):
    def test_path_escape_is_rejected(self):
        (self.ws.parent / "outside.md").write_text("外部文件", encoding="utf-8")
        with self.assertRaises(WorkflowError) as caught:
            self.flow.prepare(topic=TOPIC, plan=str(self.ws / "plans" / "P1.json"),
                              owner_session=SESSION)
        self.assertEqual(caught.exception.code, 2)  # 计划必须是工作区内相对路径
        rel = self.write_plan("P1", self.plan())
        with self.assertRaises(WorkflowError):
            self.flow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION,
                              sources=["../outside.md"])
        with self.assertRaises(WorkflowError):
            self.flow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION,
                              sources=[str(self.readings)])
        self.assertEqual(self.run_row_count(), 0)

    def test_check_does_not_write_or_launch(self):
        built = self.ready()
        before = self.db.read_bytes()
        gate = self.check(built["plan"])
        self.assertTrue(gate["ok"], gate["problems"])
        self.assertEqual(before, self.db.read_bytes())  # 闸是只读的：一个字节都不改
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_status_next_round_tracks_unreviewed_round(self):
        built, run = self.finished_run()
        status = self.flow.status(topic=TOPIC)
        self.assertFalse(status["next_round"]["allowed"])
        self.assertTrue(any("未复盘" in item for item in status["next_round"]["problems"]),
                        status["next_round"]["problems"])
        self.append_doc("supported")
        self.flow.review(run_id=run["run_id"], record=self.review_record())
        status = self.flow.status(topic=TOPIC)
        self.assertTrue(status["next_round"]["allowed"], status["next_round"]["problems"])

    def test_ledger_is_append_only(self):
        built, run = self.finished_run()
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE wf_runs SET owner_session='tamper'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM wf_events")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE wf_finishes SET exitcode=0")
        self.assertEqual(self.flow.status(topic=TOPIC)["runs"][0]["run_id"], run["run_id"])

    def test_database_refuses_a_second_active_run(self):
        built, run = self.finished_run()
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO wf_runs(run_id,prepared_id,plan_id,plan_version,topic,owner_session,"
                    "argv_json,cwd,gates_json,source_fingerprint,plan_sha256,started_at)"
                    " SELECT 'RUN-forged',prepared_id,plan_id,plan_version,topic,owner_session,"
                    " argv_json,cwd,gates_json,source_fingerprint,plan_sha256,started_at"
                    " FROM wf_runs WHERE run_id=?", (run["run_id"],))

    def test_status_is_read_only(self):
        built, run = self.finished_run()
        before = self.db.read_bytes()
        readonly = Workflow(self.ws, readonly=True)
        status = readonly.status(topic=TOPIC)
        self.assertTrue(status["ok"])
        self.assertEqual([item["run_id"] for item in status["runs"]], [run["run_id"]])
        self.assertEqual(status["runs"][0]["exitcode"], 0)
        self.assertEqual(before, self.db.read_bytes())
        missing = Workflow(self.ws, db=str(self.ws / "absent.sqlite3"), readonly=True)
        self.assertFalse(missing.status(topic=TOPIC)["ok"])

    def test_readonly_check_runs_the_same_gate_without_writing(self):
        """`readonly=True` 也能跑 check：⛔ 不因为「读一下」就建库建表或 `_require_write` 拒掉。"""
        built = self.ready()
        before = self.db.read_bytes()
        readonly = Workflow(self.ws, verifier=self.verifier,
                            monitor_check=self.monitor_ok, require_monitor_check=False,
                            authorization_check=self.authorization_ok,
                            require_authorization_check=False, readonly=True)
        gate = readonly.check(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                              argv=self.argv, intent_none=True)
        self.assertTrue(gate["ok"], gate["problems"])
        self.assertEqual(before, self.db.read_bytes())  # 一个字节都不改
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_injected_clock_and_runner_are_used(self):
        clock = lambda: 1234567890.0  # noqa: E731 - 固定时钟，便于断言时间戳来源
        calls = []

        def runner(argv, cwd, env):
            calls.append((list(argv), str(cwd), dict(env)))
            return 0

        flow = self.make_flow(clock=clock, runner=runner)
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        result = flow.start(topic=TOPIC, plan=built["plan"], owner_session=SESSION,
                            argv=self.argv, intent_none=True)
        self.assertEqual(result["exitcode"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], self.argv)
        self.assertEqual(calls[0][1], str(self.ws))
        self.assertEqual(calls[0][2]["AUTORESEARCH_TOPIC"], TOPIC)
        self.assertEqual(calls[0][2]["AUTORESEARCH_RUN_ID"], result["run_id"])
        self.assertFalse(self.marker_log.exists())  # 注入的 runner 代替了真实子进程
        status = flow.status(topic=TOPIC)
        self.assertEqual(status["preparations"][0]["created_at"], "2009-02-13T23:31:30Z")
        self.assertEqual(status["runs"][0]["started_at"], "2009-02-13T23:31:30Z")


class ConcurrencyTests(FlowCase):
    """两个**操作系统进程**同时 launch：只能有一个真的启动子进程。"""

    SCRIPT = (
        "import json,sys\n"
        "from kernel.autoresearch.workflow import Workflow, WorkflowError\n"
        "ws, plan, argv = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])\n"
        "flow = Workflow(ws, monitor_check=lambda *a, **k: {'ok': True, 'registered': True,"
        " 'state': 'none', 'intent_id': None}, require_monitor_check=False,\n"
        "                authorization_check=lambda plan, owner, exp: {'ok': True},\n"
        "                require_authorization_check=False)\n"
        "try:\n"
        "    result = flow.start(topic='" + TOPIC + "', plan=plan, owner_session='" + SESSION + "',\n"
        "                        argv=argv, intent_none=True)\n"
        "    print(json.dumps({'ok': True, 'exitcode': result['exitcode']}))\n"
        "    raise SystemExit(result['exitcode'])\n"
        "except WorkflowError as exc:\n"
        "    print(json.dumps({'ok': False, 'problems': exc.errors or [str(exc)]}))\n"
        "    raise SystemExit(9)\n"
    )

    def test_two_processes_one_winner(self):
        argv = [sys.executable, str(self.marker), str(self.marker_log), "C", "0", "2.0"]
        built = self.ready(argv=argv)
        command = [sys.executable, "-X", "utf8", "-B", "-c", self.SCRIPT,
                   str(self.ws), built["plan"], json.dumps(argv)]
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        first = subprocess.Popen(command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        outputs = [first.communicate(timeout=120), second.communicate(timeout=120)]
        codes = sorted([first.returncode, second.returncode])
        self.assertEqual(codes, [0, 9], outputs)
        self.assertEqual(self.marker_lines(), ["C"])
        self.assertEqual(self.run_row_count(), 1)
        loser = json.loads(outputs[0][0] or outputs[1][0])
        self.assertTrue(loser["ok"] or loser["problems"])


class AuthorizationTests(FlowCase):
    def test_unwired_authorization_fails_closed(self):
        flow = self.make_flow(authorization_check=None, require_authorization_check=True)
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("执行准入未接线" in item for item in gate["problems"]), gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"], flow=flow)
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_authorization_request_carries_budget_scope_and_reservation(self):
        built = self.ready()
        gate = self.check(built["plan"])
        self.assertTrue(gate["ok"], gate["problems"])
        request = gate["checks"]["authorization"]["request"]
        self.assertEqual(request["plan_id"], "P1")
        self.assertEqual(request["topic"], TOPIC)
        self.assertEqual(request["owner_session"], SESSION)
        self.assertEqual(request["argv"], self.argv)
        self.assertEqual(request["budget"], {"max_cost_units": 1, "max_seconds": 60})
        self.assertEqual(request["case_count"], 5)  # 2 target + 2 regression + 1 control
        self.assertTrue(request["reservation_key"].startswith("wf:"))
        self.assertEqual(request["prepared_id"], built["prepared"]["prepared_id"])
        self.assertEqual(request["authorization"], {"scope": "small", "series_id": "S-1",
                                                    "reservation_id": "R-1"})
        execution = gate["checks"]["execution"]
        self.assertEqual(execution["timeout_seconds"], 60)
        self.assertEqual(execution["cwd"], str(self.ws))
        self.assertFalse(execution["shell"])

    def test_authorization_denial_blocks_launch(self):
        flow = self.make_flow(authorization_check=lambda plan, owner_session, exp_id: {
            "ok": False, "problems": ["大规模题单需要 approved 决策"], "decision": "needs_approval"})
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("approved" in item for item in gate["problems"]), gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"], flow=flow)
        self.assertEqual(self.marker_lines(), [])

    def test_admission_is_rechecked_inside_the_claim_transaction(self):
        """check → release → spawn 的时序缝：占用事务里再核一次，失败就不启动。"""
        calls = []

        def release_after_gate(plan, owner_session, exp_id):
            calls.append(exp_id)
            if len(calls) == 1:  # 闸里那次通过
                return {"ok": True, "problems": [], "scope": "small"}
            return {"ok": False, "problems": ["预算预留已释放或结算"], "scope": "small"}

        flow = self.make_flow(authorization_check=release_after_gate)
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"], flow=flow)
        self.assertIn("复核失败", str(caught.exception))
        self.assertEqual(len(calls), 2)  # 闸一次 + 占用事务里一次
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_successful_start_records_two_admission_calls(self):
        built = self.ready()
        self.authorization_requests.clear()
        result = self.launch(built["plan"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.authorization_requests,
                         [("P1", SESSION, "P1"), ("P1", SESSION, "P1")])

    def test_authorization_change_after_prepare_expires_the_preparation(self):
        """`authorization` 不在 governance.KEY_FIELDS 里 ⇒ 本层靠绑整份冻结计划兜住。"""
        built = self.prepare()
        payload = self.plan()
        payload["authorization"] = {"scope": "large", "series_id": "S-1",
                                    "reservation_id": "R-1", "decision_id": "D-1"}
        self.flow.gov.freeze_plan(payload, actor="main")  # 只改 authorization 也会冻新版本
        gate = self.check(built["plan"])
        self.assertFalse(gate["ok"])
        self.assertTrue(any("已冻结版本不一致" in item for item in gate["problems"])
                        or any("过期" in item for item in gate["problems"]), gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"])
        self.assertEqual(self.marker_lines(), [])

    def test_local_execution_policy_enforces_caps(self):
        from kernel.autoresearch.workflow import local_execution_policy
        policy = local_execution_policy(authorized_by="deployment-config", max_seconds=30,
                                        max_cost_units=2)
        flow = self.make_flow(authorization_check=policy)
        built = self.prepare(flow=flow)
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])  # 计划 max_seconds=60 > 额度 30
        self.assertTrue(any("30" in item for item in gate["problems"]), gate["problems"])
        # policy 自己也要拒绝超额度请求，而不是靠外层
        verdict = policy({"budget": {"max_seconds": 31, "max_cost_units": 0}}, SESSION, "P1")
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["engine"], "local-synthetic")
        self.assertEqual(verdict["authorized_by"], "deployment-config")
        with self.assertRaises(ValueError):
            local_execution_policy(authorized_by="", max_seconds=1, max_cost_units=0)


class ClaimRecheckTests(FlowCase):
    """闸 → 占用事务之间的时序缝：来源 / 新原话 / 审计 / 监控任一变化都不得启动子进程。"""

    def test_source_change_between_gate_and_claim_never_spawns(self):
        calls = []

        def change_source(plan, owner_session, exp_id):
            calls.append(exp_id)
            if len(calls) == 1:  # 闸里那次通过，随后改掉来源文件
                self.readings.write_text("上一轮读数：通过题数 3 → 5（闸后改动）\n",
                                         encoding="utf-8")
            return {"ok": True, "problems": [], "scope": "small"}

        flow = self.make_flow(authorization_check=change_source)
        built = self.ready(flow=flow)
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"], flow=flow)
        self.assertIn("准备来源", str(caught.exception))
        self.assertEqual(len(calls), 1)  # 占用事务在复核来源时就拒绝，不再问准入
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_new_user_quote_between_gate_and_claim_never_spawns(self):
        calls = []

        def add_user(plan, owner_session, exp_id):
            calls.append(exp_id)
            if len(calls) == 1:
                self.flow.gov.sync_user_update(source_session=SESSION, seq=7,
                                               quote="闸后用户又说了一句：别改题单")
            return {"ok": True, "problems": [], "scope": "small"}

        flow = self.make_flow(authorization_check=add_user)
        built = self.ready(flow=flow)
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"], flow=flow)
        self.assertIn("用户原话", str(caught.exception))
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_audit_blocking_after_gate_never_spawns(self):
        calls = []

        def add_blocking_audit(plan, owner_session, exp_id):
            calls.append(exp_id)
            if len(calls) == 1:
                prepared = self.flow.gov.prepare_audit("P1", actor="main")
                report = audit_report(prepared["prepared_id"])
                report["issues"] = [{
                    "id": "B1", "blocking": True,
                    "problem": "闸后新发现实质设计缺陷",
                    "consequence": "该假设无法被当前判据否掉",
                    "suggestion": "先修判据再起跑",
                    "sources": [{"path": "evidence/readings.md", "locator": "L1"}],
                }]
                self.flow.gov.record_audit(
                    prepared["prepared_id"], report, actor=REVIEWER_SESSION,
                    evidence={"session_id": REVIEWER_SESSION, "seq": 9})
            return {"ok": True, "problems": [], "scope": "small"}

        flow = self.make_flow(authorization_check=add_blocking_audit)
        built = self.ready(flow=flow)
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"], flow=flow)
        self.assertIn("审计", str(caught.exception))
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_monitor_change_between_gate_and_claim_never_spawns(self):
        calls = []

        def change_monitor(owner_session, exp_id, action="launch"):
            calls.append(exp_id)
            if len(calls) == 1:
                return {"ok": True, "problems": [], "registered": True,
                        "state": "none", "intent_id": None}
            return {"ok": False, "problems": ["闸后出现新用户原话"], "registered": True,
                    "state": "none", "intent_id": None}

        flow = self.make_flow(monitor_check=change_monitor)
        built = self.ready(flow=flow)
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"], flow=flow)
        self.assertIn("监控状态在闸之后变了", str(caught.exception))
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])


class BudgetTimeoutTests(FlowCase):
    def test_plan_max_seconds_kills_the_run_and_records_timeout(self):
        argv = [sys.executable, str(self.marker), str(self.marker_log), "T", "0", "5"]
        built = self.ready(argv=argv, budget={"max_cost_units": 1, "max_seconds": 1})
        result = self.launch(built["plan"], argv=argv)
        self.assertTrue(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exitcode"])
        self.assertEqual(result["reason"], "timeout")
        self.assertEqual(self.marker_lines(), [])  # marker 还没写就被杀死
        status = self.flow.status(topic=TOPIC)
        self.assertEqual(status["runs"][0]["reason"], "timeout")
        self.assertIsNone(status["runs"][0]["exitcode"])


class AdmissionIntegrationTests(FlowCase):
    """主链 + **真实** `Admission` + **真实** `Accounts` 预算预留（不是 always-true 夹具）。

    预算授权用的是显式合成夹具：HMAC 签名的 grant 事件（同 `tests/test_admission.py` 的注入方式）。
    ⛔ 这不代表真实用户授权；它只证明「预算预留 → 闸 → 占用事务复核 → 真子进程」这条链有人核。
    """

    KEY = b"explicit-synthetic-fixture-key-000000000"

    def setUp(self):
        super().setUp()
        self.store.ensure_experiment("P1")
        self.accounts = Accounts(
            self.store,
            ApprovalEvidence({"fixture": SourcePolicy(HMACSource(self.KEY),
                                                      frozenset({"fixture-user"}))}))
        self.grant()
        self.admission = Admission(self.ws, store=self.store)

    def grant(self, scope="small", units=3):
        event = {"event_id": "grant-" + scope, "series_id": "series-" + scope, "exp_id": "P1",
                 "scope": scope, "action": "grant", "expires_at": time.time() + 3600,
                 "total_units": 10, "principal": "fixture-user"}
        event["signature"] = hmac.new(self.KEY, canonical_json(event).encode(),
                                      hashlib.sha256).hexdigest()
        self.accounts.authorize("fixture", event, "grant-request-" + scope)
        self.accounts.reserve("series-" + scope, "P1", scope, units, "reservation-" + scope)

    def reservation(self, reservation_id="reservation-small"):
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM budget_reservations WHERE id=?",
                                     (reservation_id,)).fetchone())

    def budget_plan(self, plan_id="P1", **kwargs):
        kwargs.setdefault("budget", {"max_cost_units": 3, "max_seconds": 60})
        kwargs.setdefault("authorization", {"scope": "small", "series_id": "series-small",
                                            "reservation_id": "reservation-small"})
        return self.plan(plan_id, **kwargs)

    def budget_flow(self, **kwargs):
        kwargs.setdefault("authorization_check", self.admission.check)
        kwargs.setdefault("require_authorization_check", True)
        return self.make_flow(**kwargs)

    def test_real_admission_and_reservation_admit_the_real_marker(self):
        flow = self.budget_flow()
        built = self.prepare(flow=flow, budget=self.budget_plan()["budget"],
                             authorization=self.budget_plan()["authorization"])
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertTrue(gate["ok"], gate["problems"])
        self.assertEqual(gate["checks"]["authorization"]["result"]["decision"], None)
        self.assertEqual((gate["checks"]["authorization"]["request"]["authorization"] or {}).get("scope"),
                         "small")
        result = self.launch(built["plan"], flow=flow)
        self.assertEqual(result["exitcode"], 0)
        self.assertEqual(result["reason"], "completed")
        self.assertEqual(self.marker_lines(), ["A"])
        # finish 不按 0 结算：预留还是 reserved，等有证据再结。
        self.assertEqual(self.reservation()["status"], "reserved")
        status = flow.status(topic=TOPIC)
        self.assertTrue(status["runs"][0]["finished"])

    def test_reservation_released_between_gate_and_claim_never_spawns(self):
        settled = []

        def admission_then_settle(plan, owner_session, exp_id):
            verdict = self.admission.check(plan, owner_session, exp_id)
            if not settled:  # 闸刚核过、占用还没开始 —— 模拟别处把预留结算掉
                self.accounts.settle("reservation-small", 1, "settle-after-gate")
                settled.append(True)
            return verdict

        flow = self.budget_flow(authorization_check=admission_then_settle)
        built = self.prepare(flow=flow, budget=self.budget_plan()["budget"],
                             authorization=self.budget_plan()["authorization"])
        self.audit(flow=flow)
        with self.assertRaises(WorkflowError) as caught:
            self.launch(built["plan"], flow=flow)
        self.assertIn("复核失败", str(caught.exception))
        self.assertEqual(self.reservation()["status"], "settled")
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_wrong_scope_reservation_is_refused_before_any_spawn(self):
        flow = self.budget_flow()
        built = self.prepare(flow=flow, budget={"max_cost_units": 3, "max_seconds": 60},
                             authorization={"scope": "large", "series_id": "series-small",
                                            "reservation_id": "reservation-small",
                                            "decision_id": "D1"})
        self.audit(flow=flow)
        gate = self.check(built["plan"], flow=flow)
        self.assertFalse(gate["ok"])
        self.assertTrue(any("作用域" in item or "预算" in item for item in gate["problems"]),
                        gate["problems"])
        with self.assertRaises(WorkflowError):
            self.launch(built["plan"], flow=flow)
        self.assertEqual(self.marker_lines(), [])


class CliTests(FlowCase):
    def cli(self, argv):
        from kernel.autoresearch.workflow_cli import main as cli_main
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli_main(argv)
        return code, out.getvalue()

    def test_cli_prepare_then_check_refuses_without_launch(self):
        self.ready()  # Python 侧准备好材料与已核审计
        plan = "plans/P1.json"
        code, output = self.cli(["check", "--workspace", str(self.ws), "--topic", TOPIC,
                                 "--plan", plan, "--owner-session", SESSION, "--intent-none",
                                 "--"] + self.argv)
        self.assertEqual(code, 9, output)
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])

    def test_cli_prepare_records_one_preparation(self):
        rel = self.write_plan("P1", self.plan())
        code, output = self.cli(["prepare", "--workspace", str(self.ws), "--topic", TOPIC,
                                 "--plan", rel, "--owner-session", SESSION,
                                 "--source", "evidence/readings.md"])
        self.assertEqual(code, 0, output)
        payload = json.loads(output)
        self.assertTrue(payload["prepared_id"].startswith("PREP-"))
        self.assertEqual(len(payload["argv"]), 1)
        self.assertIn("evidence/readings.md", payload["snapshot"]["sources"])
        with contextlib.closing(sqlite3.connect(str(self.db))) as conn:
            count = conn.execute("SELECT COUNT(*) FROM wf_preparations").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(self.run_row_count(), 0)

    def test_cli_launch_refuses_without_launch(self):
        self.ready()
        code, output = self.cli(["launch", "--workspace", str(self.ws), "--topic", TOPIC,
                                 "--plan", "plans/P1.json", "--owner-session", SESSION,
                                 "--intent-none", "--"] + self.argv)
        self.assertEqual(code, 9, output)
        self.assertEqual(self.marker_lines(), [])
        self.assertEqual(self.run_row_count(), 0)

    def test_cli_has_no_gate_bypass_flag(self):
        from kernel.autoresearch.workflow_cli import _parser
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                _parser().parse_args(["check", "--workspace", str(self.ws), "--topic", TOPIC,
                                      "--plan", "plans/P1.json", "--owner-session", SESSION,
                                      "--intent-none", "--allow-unwired-monitor",
                                      "--", "python"])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                _parser().parse_args(["check", "--workspace", str(self.ws), "--topic", TOPIC,
                                      "--plan", "plans/P1.json", "--owner-session", SESSION,
                                      "--intent-none", "--monitor-module", "x.y",
                                      "--", "python"])

    def test_cli_finish_review_status_use_the_same_ledger(self):
        built, run = self.finished_run()
        self.append_doc("supported")
        record = self.ws / "review.json"
        record.write_text(json.dumps(self.review_record(schema_version=2, continuation={
            'criterion': '同装置逐题复核收益',
            'criterion_evidence': [{'path': 'evidence/readings.md', 'locator': 'L1'}],
            'cost_scope': {'scope': 'small', 'max_cost_units': 1, 'basis': '仅一轮小样本费用上限'},
            'next_owner': SESSION, 'needs_approval': False,
            'approval_reason': '机制验证无需专项批准，实际执行仍核共享预算'}), ensure_ascii=False), encoding="utf-8")
        code, output = self.cli(["review", "--workspace", str(self.ws), "--run-id", run["run_id"],
                                 "--file", "review.json"])
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["verdict"], "supported")
        code, output = self.cli(["status", "--workspace", str(self.ws), "--topic", TOPIC])
        self.assertEqual(code, 0, output)
        status = json.loads(output)
        self.assertEqual(status["runs"][0]["run_id"], run["run_id"])
        self.assertTrue(status["runs"][0]["reviewed"])
        self.assertEqual(status["pending_reports"], [])

    def test_cli_finish_preserves_exitcode_in_payload(self):
        built = self.ready()
        started = self.launch(built["plan"], finish=False)
        code, output = self.cli(["finish", "--workspace", str(self.ws),
                                 "--run-id", started["run_id"], "--exitcode", "2",
                                 "--reason", "failed", "--documentation-status", "failed",
                                 "--documentation-detail", "页面没更新"])
        self.assertEqual(code, 0, output)  # 文档失败不改 CLI 退出码
        payload = json.loads(output)
        self.assertEqual(payload["exitcode"], 2)
        self.assertEqual(payload["documentation"]["status"], "failed")
        self.assertEqual(self.flow.status(topic=TOPIC)["runs"][0]["exitcode"], 2)


if __name__ == "__main__":
    unittest.main()
