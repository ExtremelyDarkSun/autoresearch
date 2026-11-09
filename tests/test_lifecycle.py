# GENERATED-BY: research lifecycle projection migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：lifecycle 接线层 —— 投影、收据、reporting 事件、before/after 快照、hook 诊断。
# PURPOSE: 证明权威 wf_* 记录能被投影成人读/AI 材料与 reporting 事件，且 before/after、幂等、防篡改、
#          hook 失败不挡安全停；并跑通真实 Workflow + provision_topic + 真 marker 的 I4 整轮交付。
# ORIGIN: 合成临时工作区夹具（复用同目录 `test_workflow` 的夹具，见文件内说明）；⛔ 无真实实验/机器/读数。
# CUMULATIVE-DELTA: 首版。审计用显式夹具，Admission/Accounts 用真实实现；scribe 回执只到 declared。2026-11-09 默认init改分层模板，夹具复用已生成的submission/plans，完整主链仍走默认模式。

"""lifecycle 接线层测试。

夹具来源说明（test import）：
`FlowCase` / `AdmissionIntegrationTests` / `VerifiedHost` 来自同目录 `tests/test_workflow.py`
（`tests` 不是包，所以这里按同目录模块 import）。它们是**合成夹具**，不是生产实现：
审计核验器是夹具，监控与准入在 `FlowCase` 里也是夹具；`ProvisionedDeliveryTests` 用真实
`Admission` + 真实 `Accounts` 预算预留 + 真实子进程 marker。

判据（不是「文件存在就行」）：
1. `sync` 只读权威表：DB 逐字节不变；投影/收据内容能回指 wf_* 行；run/round 事件带同一 `run_id`。
2. 幂等：再跑一次不追加事件、不改文件；手改收据 ⇒ 拒（同名源内容不符）。
3. `before` 只能启动前；启动后拒绝；`after` 缺 `before` 时拒绝，⛔ 不用当前 TOPIC 补造。
4. 来源清单只加投影，⛔ 不加 STATUS/FEED/RECEIPT（否则自证循环）；ack 后再 `sync` 不破坏验收。
5. hook 失败只留 diagnostic，不抛、不挡安全停、不回滚已完成的 review。

跑法：

    python -X utf8 -B -m unittest discover -s tests -p 'test_lifecycle.py' -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import shutil
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
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from kernel.autoresearch import lifecycle, reporting, workspace_layout  # noqa: E402
from kernel.autoresearch.admission import Admission  # noqa: E402
from kernel.autoresearch.runtime.accounts import (  # noqa: E402
    Accounts,
    ApprovalEvidence,
    HMACSource,
    SourcePolicy,
)
from kernel.autoresearch.runtime.store import Store, canonical_json  # noqa: E402
from kernel.autoresearch.runtime.topics import Topics  # noqa: E402

# 同目录夹具（tests 不是包）：按**模块**导入，避免把 test_workflow 的 TestCase 类
# 带进本模块命名空间（unittest discover 会把导入的 TestCase 再收集一遍）。
# 见文件头「夹具来源说明」：这些是合成夹具，不是生产实现。
import test_workflow as fixtures  # noqa: E402

TOPIC = fixtures.TOPIC
SESSION = fixtures.SESSION
MARKER_SRC = fixtures.MARKER_SRC

SECTIONS = ("你的要求", "一句话现状", "最近实验", "已定 / 已否 / 待定",
            "本轮改了什么", "这轮结论依赖的未验证前提", "证据在哪", "下一步研究")


def page_text(receipt_rel):
    """合成研究页：八节齐全，第 3 节一块四段，来源指向生成出来的 finish 收据。"""
    block = ("### 实验 P1\n"
             "- 假设：收紧入口参数会让通过题数至少多 1 题\n"
             "- 判据：同题单通过数；只解释同装置、同题单条件\n"
             "- 设置：同题单 2 题 × 重复 2 次，对照为上一版\n"
             "- 结果：通过题数 3 → 4（阶段结果，不能外推）\n"
             f"- 来源：`{receipt_rel}` 的 reason / exitcode 字段")
    bodies = {index: "研究内容" for index in range(1, 9)}
    bodies[3] = block
    return "\n\n".join(f"## {index} {name}\n{bodies[index]}"
                       for index, name in enumerate(SECTIONS, 1)) + "\n"


class LifecycleCase(fixtures.FlowCase):
    """复用 `test_workflow.FlowCase`（夹具见文件头）；这里只加 lifecycle 的便捷方法。"""

    def lifecycle(self):
        return lifecycle.Lifecycle(self.ws)

    def finished_review(self, plan_id="P1"):
        built = self.ready(plan_id)
        launched = self.launch(built["plan"])
        self.append_doc("supported")
        review = self.flow.review(run_id=launched["run_id"], record=self.review_record())
        return built, launched, review

    def events(self):
        path = reporting.events_path(self.ws, TOPIC)
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def inputs(self):
        path = reporting.inputs_path(self.ws, TOPIC)
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def receipt_rel(self, plan_id, kind, run_id):
        return lifecycle.Lifecycle(self.ws).receipt(TOPIC, plan_id, kind, run_id).relative_to(
            self.ws).as_posix()


class ProjectionTests(LifecycleCase):
    def test_sync_projects_plan_ai_work_receipts_and_events(self):
        _, launched, review = self.finished_review()
        before = self.db.read_bytes()
        result = self.lifecycle().sync(TOPIC, plan_id="P1")
        self.assertTrue(result["ok"], result["diagnostics"])
        self.assertEqual(self.db.read_bytes(), before, "sync 不许写权威库")

        plan_path = self.topic_dir / "plans" / "P1" / "plan.json"
        frozen = self.flow.gov.latest_plan("P1")["plan"]
        self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8")), frozen)

        ai = (self.topic_dir / "plans" / "P1" / "AI_WORK.md").read_text(encoding="utf-8")
        self.assertIn("不是手工台账", ai)
        self.assertIn(launched["run_id"], ai)
        self.assertIn("结束原因：`completed`", ai)
        self.assertIn("verdict `supported`", ai)

        finish = json.loads((self.topic_dir / "plans" / "P1" / "receipts"
                             / f"finish-{launched['run_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(finish["reason"], "completed")
        self.assertEqual(finish["exitcode"], 0)
        review_receipt = json.loads(
            (self.topic_dir / "plans" / "P1" / "receipts"
             / f"review-{launched['run_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(review_receipt["record_sha256"], review["record_sha256"])

        manifest = json.loads((self.topic_dir / "sources.json").read_text(encoding="utf-8"))
        for expected in (f"topics/{TOPIC}/plans/P1/plan.json",
                         f"topics/{TOPIC}/plans/P1/AI_WORK.md",
                         f"topics/{TOPIC}/plans/P1/receipts/run-{launched['run_id']}.json",
                         f"topics/{TOPIC}/plans/P1/receipts/finish-{launched['run_id']}.json",
                         f"topics/{TOPIC}/plans/P1/receipts/review-{launched['run_id']}.json"):
            self.assertIn(expected, manifest)
        for forbidden in ("STATUS.md", "FEED.md", "RECEIPT.json"):
            self.assertFalse(any(item.endswith(forbidden) for item in manifest), manifest)

        events = self.events()
        self.assertEqual([event["scope"] for event in events], ["run", "run", "round"])
        self.assertEqual([event['kind'] for event in events], ['running', 'completed', 'completed'])
        for event in events:
            self.assertIn(launched["run_id"], event["event_id"])
            self.assertEqual(event["exp"], "P1")
            kind = 'run' if event['kind'] == 'running' else 'finish'
            self.assertTrue(event['source']['path'].endswith(f'{kind}-{launched["run_id"]}.json'))
        conclusions = [row for row in self.inputs() if row["kind"] == "leader_conclusion"]
        self.assertEqual(len(conclusions), 1)
        self.assertEqual(conclusions[0]["exp"], "P1")
        self.assertTrue(conclusions[0]["input_id"].startswith(f"wf-review-{launched['run_id']}-"))

    def test_sync_is_idempotent(self):
        self.finished_review()
        client = self.lifecycle()
        first = client.sync(TOPIC, plan_id="P1")
        self.assertFalse(first['written'], 'Workflow已自动同步，手工sync不应产生新版本')
        event_lines = reporting.events_path(self.ws, TOPIC).read_text(encoding="utf-8")
        second = client.sync(TOPIC, plan_id="P1")
        self.assertEqual(second["written"], [])
        self.assertEqual(reporting.events_path(self.ws, TOPIC).read_text(encoding="utf-8"),
                         event_lines)
        for plan in second["plans"]:
            for event in plan["reporting"]["events"]:
                self.assertTrue(event["idempotent"])
            for item in plan["reporting"]["inputs"]:
                self.assertTrue(item["idempotent"])

    def test_receipt_tamper_is_rejected(self):
        _, launched, _ = self.finished_review()
        client = self.lifecycle()
        client.sync(TOPIC, plan_id="P1")
        receipt = client.receipt(TOPIC, "P1", "finish", launched["run_id"])
        receipt.write_text('{"schema": 1, "kind": "finish", "reason": "completed"}\n',
                           encoding="utf-8")
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            client.sync(TOPIC, plan_id="P1")
        self.assertIn("同名源内容不符", str(caught.exception))

    def test_reprepare_new_plan_preserves_version_and_updates_projection(self):
        built = self.prepare()
        client = self.lifecycle()
        initial = client.plan_projection(TOPIC, 'P1').read_bytes()
        payload = self.plan()
        payload['goal'] = '同一专题的新问题，须重新审查'
        rel = self.write_plan('P1', payload)
        renewed = self.flow.prepare(topic=TOPIC, plan=rel, owner_session=SESSION)
        self.assertTrue(renewed['lifecycle_sync']['ok'], renewed)
        self.assertEqual((client.plan_dir(TOPIC, 'P1') / 'plan-v1.json').read_bytes(), initial)
        latest = client.plan_projection(TOPIC, 'P1').read_bytes()
        self.assertNotEqual(latest, initial)
        self.assertIn('新问题', latest.decode('utf-8'))
        self.assertEqual(len(list((self.ws / 'topics' / TOPIC / 'snapshots/P1').glob('*/before.*'))), 4)

    def test_before_only_prestart_and_after_binds_before(self):
        built = self.ready()
        raw = self.doc.read_bytes()
        client = self.lifecycle()
        before = client.before(built["prepared"])
        self.assertTrue(before["ok"])
        self.assertEqual(before["sha256"], reporting.file_sha256(client.snapshot(TOPIC, "P1", "before")))
        self.assertEqual(client.snapshot(TOPIC, "P1", "before").read_bytes(), raw)
        self.assertEqual(client.before(built["prepared"])["state"], "identical")

        launched = self.launch(built["plan"])
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            client.before(built["prepared"])
        self.assertIn("已经启动过", str(caught.exception))

        self.append_doc("supported")
        review = self.flow.review(run_id=launched["run_id"], record=self.review_record())
        after = client.after(review)
        self.assertEqual(after["before_sha256"], before["sha256"])
        self.assertEqual(after["review_record_sha256"], review["record_sha256"])
        self.assertEqual(client.snapshot(TOPIC, "P1", "after").read_bytes(),
                         self.doc.read_bytes())
        self.assertNotEqual(after["sha256"], before["sha256"])
        self.assertEqual(client.after(review)["state"], "identical")

    def test_snapshot_mismatch_is_rejected(self):
        built = self.ready()
        client = self.lifecycle()
        client.before(built["prepared"])
        snapshot = client.snapshot(TOPIC, "P1", "before")
        snapshot.write_bytes(snapshot.read_bytes() + b"\nchanged\n")
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            client.before(built["prepared"])
        self.assertIn("同名源内容不符", str(caught.exception))

    def test_after_without_before_is_refused_and_sync_diagnoses(self):
        _, launched, review = self.finished_review()
        client = self.lifecycle()
        before_path = client.snapshot(TOPIC, 'P1', 'before')
        self.assertTrue(before_path.resolve().is_relative_to(self.ws.resolve()))
        before_path.rename(before_path.with_suffix('.lost'))
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            client.after(review)
        self.assertIn("缺 before", str(caught.exception))
        route = client.sync(TOPIC, plan_id="P1")
        codes = {item["code"] for item in route["diagnostics"]}
        self.assertIn("before_missing", codes)
        self.assertFalse(client.snapshot(TOPIC, "P1", "before").exists(),
                         "sync 不许从当前 TOPIC 补造 before")

    def test_sources_manifest_excludes_outputs_and_ack_survives_sync(self):
        built = self.ready()
        client = self.lifecycle()
        client.before(built["prepared"])
        launched = self.launch(built["plan"])
        self.append_doc("supported")
        self.flow.review(run_id=launched["run_id"], record=self.review_record())
        post = client.post("result_analysis",
                           {"run_id": launched["run_id"], "topic": TOPIC, "plan_id": "P1"})
        self.assertTrue(post["ok"], post)
        reporting.record_input(self.ws, TOPIC, "user_requirement",
                               "用户要求：把通过题数从 3 提到 4",
                               {"path": "evidence/readings.md", "locator": "L1"})
        prepared = reporting.prepare(self.ws, TOPIC)
        self.assertTrue(prepared["ok"])
        receipt_rel = self.receipt_rel("P1", "finish", launched["run_id"])
        (self.topic_dir / "STATUS.md").write_text(page_text(receipt_rel), encoding="utf-8")
        ack = reporting.acknowledge(self.ws, TOPIC, "session-scribe-lifecycle",
                                    writer_role="scribe")
        self.assertEqual(ack["verification"], "declared")
        self.assertTrue(reporting.check(self.ws, TOPIC)["ok"])

        fingerprint = reporting.source_snapshot(self.ws, TOPIC)["fingerprint"]
        client.sync(TOPIC, plan_id="P1")
        self.assertEqual(reporting.source_snapshot(self.ws, TOPIC)["fingerprint"], fingerprint)
        self.assertTrue(reporting.check(self.ws, TOPIC)["ok"])
        manifest = json.loads((self.topic_dir / "sources.json").read_text(encoding="utf-8"))
        for forbidden in ("STATUS.md", "FEED.md", "RECEIPT.json"):
            self.assertFalse(any(item.endswith(forbidden) for item in manifest), manifest)
        snapshot = reporting.source_snapshot(self.ws, TOPIC)
        self.assertFalse(any(rel.endswith("STATUS.md") for rel in snapshot["files"]))

    def test_before_failure_blocks_start(self):
        """before 失败 ⇒ 父流程不得调用 start（这里断言：没启动、没 run 行）。"""
        built = self.ready()
        self.doc.unlink()
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            self.lifecycle().before(built["prepared"])
        self.assertIn("缺专题主文档", str(caught.exception))
        self.assertEqual(self.run_row_count(), 0)

    def test_post_unknown_kind_is_diagnostic(self):
        _, launched, _ = self.finished_review()
        result = self.lifecycle().post("weird-kind", {"run_id": launched["run_id"]})
        self.assertFalse(result["ok"])
        self.assertIn("未支持的 hook kind", result["diagnostic"]["error"])

    def test_post_failure_is_diagnostic_and_sync_still_runs(self):
        _, launched, _ = self.finished_review()
        client = self.lifecycle()
        bad = client.post("result_analysis",
                          {"run_id": "RUN-does-not-exist", "topic": TOPIC, "plan_id": "P1"})
        self.assertFalse(bad["ok"])
        self.assertIn("没有这个运行记录", bad["diagnostic"]["error"])
        log = client.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)

        # 模拟已存快照丢失：after 失败，sync仍保留终态，不补造before。
        before_path = client.snapshot(TOPIC, 'P1', 'before')
        self.assertTrue(before_path.resolve().is_relative_to(self.ws.resolve()))
        before_path.rename(before_path.with_suffix('.lost'))
        again = client.post("result_analysis",
                            {"run_id": launched["run_id"], "topic": TOPIC, "plan_id": "P1"})
        self.assertFalse(again["ok"])
        self.assertIn("after_error", again)
        self.assertIn("sync", again)
        self.assertEqual(len([event for event in self.events() if event["scope"] == "round"]), 1)
        self.assertEqual(client.post("evidence", {"run_id": launched["run_id"]})["ok"], True)


class ProvisionedDeliveryTests(fixtures.FlowCase):
    """真实 `provision_topic` + 真实 `Admission`/`Accounts` + 真 marker → lifecycle → reporting。

    `setUp` 复制自 `test_workflow.FlowCase.setUp`（同目录 import，见文件头），
    只把「手工建专题」换成 `workspace.provision_topic(create_environment=False)`；
    预算/准入的三个 helper（`KEY` / `grant` / `budget_plan` / `budget_flow`）复制自
    `test_workflow.AdmissionIntegrationTests`，用**真实** Accounts/Admission，不是 always-true 夹具。
    """

    KEY = b"explicit-synthetic-fixture-key-000000000"

    def grant(self, scope="small", units=3):
        event = {"event_id": "grant-" + scope, "series_id": "series-" + scope, "exp_id": "P1",
                 "scope": scope, "action": "grant", "expires_at": time.time() + 3600,
                 "total_units": 10, "principal": "fixture-user"}
        event["signature"] = hmac.new(self.KEY, canonical_json(event).encode(),
                                      hashlib.sha256).hexdigest()
        self.accounts.authorize("fixture", event, "grant-request-" + scope)
        self.accounts.reserve("series-" + scope, "P1", scope, units, "reservation-" + scope)

    def budget_plan(self, plan_id="P1", **kwargs):
        kwargs.setdefault("budget", {"max_cost_units": 3, "max_seconds": 60})
        kwargs.setdefault("authorization", {"scope": "small", "series_id": "series-small",
                                            "reservation_id": "reservation-small"})
        return self.plan(plan_id, **kwargs)

    def budget_flow(self, **kwargs):
        kwargs.setdefault("authorization_check", self.admission.check)
        kwargs.setdefault("require_authorization_check", True)
        return self.make_flow(**kwargs)

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git 不可用，无法 provision_topic")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name) / "ws"
        init_output = self.ws.parent / 'init-output.txt'
        with init_output.open('wb') as stream:
            initialized = subprocess.run([sys.executable, '-X', 'utf8', '-B',
                str(Path(__file__).resolve().parents[1] / 'research.py'), 'init', '--workspace', str(self.ws)],
                stdout=stream, stderr=subprocess.STDOUT)
        self.assertEqual(initialized.returncode, 0, init_output.read_text(encoding='utf-8'))
        from kernel.autoresearch.workspace import provision_topic
        provision = provision_topic(self.ws, topic=TOPIC, objective="把通过题数从 3 提到 4",
                        requirements="用户要求：先小样本验证机制，别动题单",
                        owner_session=SESSION, create_environment=True)
        self.db = self.ws / ".autoresearch" / "runtime.sqlite3"
        self.store = Store(self.db)
        self.topics = Topics(self.store)
        # `init` 之后专题落点由 `.autoresearch/workspace-layout.json` 决定（旧无标记工作区仍是 topics/<id>），
        # 所以夹具按布局解析，而不是硬编码 topics/。
        layout = workspace_layout.resolve_layout(self.ws)
        self.layout = layout
        self.topic_dir = layout.topic_dir(TOPIC)
        self.doc = self.topic_dir / "TOPIC.md"
        (self.ws / "evidence").mkdir()
        self.readings = self.ws / "evidence" / "readings.md"
        self.readings.write_text("上一轮读数：通过题数 3（逐题核对）\n", encoding="utf-8")
        (self.ws / "submission").mkdir(exist_ok=True)
        self.entry = self.ws / "submission" / "entry.py"
        self.entry.write_text("def parse_args(argv):\n    return argv\n", encoding="utf-8")
        (self.ws / "plans").mkdir(exist_ok=True)
        self.marker = self.ws / provision['code_path'] / "marker.py"
        self.marker.write_text(MARKER_SRC, encoding="utf-8")
        self.marker_log = self.ws / "marker.log"
        self.argv = [str(self.ws / provision['python']), str(self.marker), str(self.marker_log), "A", "0"]
        self.verifier = fixtures.VerifiedHost()
        self.monitor_calls = []
        self.authorization_requests = []
        self.flow = self.make_flow()
        self.flow.gov.sync_user_update(source_session=SESSION, seq=1,
                                       quote="先把通过题数从 3 提到 4，别动题单")
        self.flow.gov.assess_update(source_session=SESSION, covers_seq=1, decision="none",
                                    quote="这句不立为跨轮意图，只是本轮目标", answer_seq=2,
                                    actor="main")
        # 真实预算/准入（复制自 test_workflow.AdmissionIntegrationTests.setUp）。
        self.store.ensure_experiment("P1")
        self.accounts = Accounts(
            self.store,
            ApprovalEvidence({"fixture": SourcePolicy(HMACSource(self.KEY),
                                                      frozenset({"fixture-user"}))}))
        self.grant()
        self.admission = Admission(self.ws, store=self.store)

    def budget_round(self):
        flow = self.budget_flow()
        built = self.prepare(flow=flow, budget=self.budget_plan()["budget"],
                             authorization=self.budget_plan()["authorization"])
        self.audit(flow=flow)
        return flow, built

    def test_full_round_delivery_end_to_end(self):
        flow, built = self.budget_round()
        client = lifecycle.Lifecycle(self.ws)
        before = client.before(built["prepared"])
        self.assertTrue(before["ok"])
        self.assertTrue(client.sync(TOPIC, plan_id="P1")["ok"])

        launched = self.launch(built["plan"], flow=flow)
        self.assertEqual(launched["exitcode"], 0)
        self.assertEqual(self.marker_lines(), ["A"])
        self.append_doc("supported")
        review = flow.review(run_id=launched["run_id"], record=self.review_record())

        reporting.record_input(self.ws, TOPIC, "user_requirement",
                               "用户要求：先小样本验证机制，别动题单",
                               {"path": self.layout.relative(self.topic_dir / "REQUIREMENTS.md"),
                                "locator": "L1"})
        post = client.post("result_analysis",
                           {"run_id": launched["run_id"], "topic": TOPIC, "plan_id": "P1",
                            "record_sha256": review["record_sha256"]})
        self.assertTrue(post["ok"], post)
        self.assertTrue(client.snapshot_receipt(TOPIC, "P1", "after").is_file())

        ai = (self.topic_dir / "plans" / "P1" / "AI_WORK.md").read_text(encoding="utf-8")
        self.assertIn(launched["run_id"], ai)
        self.assertIn("verdict `supported`", ai)

        ready = reporting.prepare(self.ws, TOPIC)
        files = ready["sources"]["files"]
        for rel in (self.layout.relative(self.topic_dir / "plans" / "P1" / "plan.json"),
                    self.layout.relative(self.topic_dir / "plans" / "P1" / "AI_WORK.md"),
                    self.layout.relative(self.topic_dir / "plans" / "P1" / "receipts"
                                         / f"finish-{launched['run_id']}.json"),
                    self.layout.relative(self.topic_dir / "plans" / "P1" / "receipts"
                                         / f"review-{launched['run_id']}.json")):
            self.assertIn(rel, files)

        receipt_rel = client.receipt(TOPIC, "P1", "finish", launched["run_id"]).relative_to(
            self.ws).as_posix()
        (self.topic_dir / "STATUS.md").write_text(page_text(receipt_rel), encoding="utf-8")
        ack = reporting.acknowledge(self.ws, TOPIC, "session-scribe-lifecycle",
                                    writer_role="scribe")
        self.assertEqual(ack["verification"], "declared",
                         "Lifecycle 不能自己核 scribe verified")

        round_check = reporting.check_round(self.ws, TOPIC, "P1")
        self.assertTrue(round_check["ok"], round_check["problems"])
        self.assertEqual(round_check["round_terminal"]["kind"], "completed")

        # 再同步一次：文件与事件都幂等，已验收的页面不回退。
        again = client.sync(TOPIC, plan_id="P1")
        self.assertTrue(again["ok"])
        self.assertTrue(reporting.check(self.ws, TOPIC)["ok"])
        self.assertEqual(reporting.documentation_state(self.ws, TOPIC)["state"], "已验收")

    def test_round_check_needs_real_review_conclusion(self):
        flow, built = self.budget_round()
        client = lifecycle.Lifecycle(self.ws)
        client.before(built["prepared"])
        launched = self.launch(built["plan"], flow=flow)
        self.assertEqual(launched["exitcode"], 0)
        # 只投影到「运行结束」：没有 review ⇒ 没有 leader_conclusion，任务停在 pending_owner。
        projected = client.sync(TOPIC, plan_id="P1")
        self.assertTrue(projected["ok"])
        inputs_path = reporting.inputs_path(self.ws, TOPIC)
        inputs = ([json.loads(line) for line in
                   inputs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                  if inputs_path.is_file() else [])
        self.assertEqual([row for row in inputs if row["kind"] == "leader_conclusion"], [],
                         "没有 review 时不许伪造 leader_conclusion")
        task = json.loads(reporting.task_path(self.ws, TOPIC).read_text(encoding="utf-8"))
        self.assertEqual(task["state"], "pending_owner")
        self.assertTrue(any("leader_conclusion" in item for item in task["missing"]), task)

        # 就算页面结构全绿，缺 review 结论时 round-check 也拒（I4）。
        early = reporting.check_round(self.ws, TOPIC, "P1")
        self.assertFalse(early["ok"])
        self.assertTrue(any("负责人的判断" in problem for problem in early["problems"]),
                        early["problems"])

        self.append_doc("supported")
        flow.review(run_id=launched["run_id"], record=self.review_record())
        client.sync(TOPIC, plan_id="P1")
        reporting.record_input(self.ws, TOPIC, "user_requirement",
                               "用户要求：先小样本验证机制，别动题单",
                               {"path": self.layout.relative(self.topic_dir / "REQUIREMENTS.md"),
                                "locator": "L1"})
        reporting.prepare(self.ws, TOPIC)
        receipt_rel = client.receipt(TOPIC, "P1", "finish", launched["run_id"]).relative_to(
            self.ws).as_posix()
        (self.topic_dir / "STATUS.md").write_text(page_text(receipt_rel), encoding="utf-8")
        reporting.acknowledge(self.ws, TOPIC, "session-scribe-lifecycle", writer_role="scribe")
        accepted = reporting.check_round(self.ws, TOPIC, "P1")
        self.assertTrue(accepted["ok"], accepted["problems"])


if __name__ == "__main__":
    unittest.main()
