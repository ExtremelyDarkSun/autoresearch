# GENERATED-BY: 完整功能迁移（D2 出口闸 / 下一轮只读 preflight / 跨专题四阶段排序）
# LAST-EDIT: 2026-11-09（首版；真实 Workflow/Accounts/Admission/Governance 组合夹具 + 真实 Monitoring
#            事件账与合成 host + 真实 Runtime 资源租约；不连接真云机、不发消息、不跑外部评测）
# PURPOSE: 证明「超期未回复时不许记 advance 假绿」「下一轮只有五道真实闸全过才给可执行命令」
#          「跨专题按显式阶段排序、同机器/GPU 冲突不推荐、零 GPU 可并行」，并证明这些只读入口
#          不写盘、不新增文件、不改正在跑的冻结轮次。
# ORIGIN: kernel/autoresearch/progress.py 的 preflight / next_round / schedule / outcome(D2)；
#         行为预期 A1 / A6 / D2 / §0.6 / K1 / E5。
# CUMULATIVE-DELTA: 首版。夹具说明：审计独立性用同目录 `test_workflow.VerifiedHost`（合成核验器，
#         生产用 host_evidence.DSHHost）；Monitoring 的事件账与 check_start 是真实实现，
#         会话证据来自本文件的最小合成 host（顶层会话记录），⛔ 不声称真实宿主已核。
#         三个专题共用同一个真实独立 venv（只为验证排序/冲突，不声称依赖隔离）。

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from kernel.autoresearch import progress  # noqa: E402
from kernel.autoresearch.admission import Admission  # noqa: E402
from kernel.autoresearch.monitoring import Monitoring  # noqa: E402
from kernel.autoresearch.progress_host import read_overdue  # noqa: E402
from kernel.autoresearch.runtime.accounts import (  # noqa: E402
    Accounts,
    ApprovalEvidence,
    HMACSource,
    SourcePolicy,
)
from kernel.autoresearch.runtime.delivery import Delivery  # noqa: E402
from kernel.autoresearch.runtime.inbox import Inbox  # noqa: E402
from kernel.autoresearch.runtime.scheduler import Scheduler  # noqa: E402
from kernel.autoresearch.runtime.store import Store, canonical_json  # noqa: E402
from kernel.autoresearch.runtime.store import file_identity  # noqa: E402
from kernel.autoresearch.runtime.topics import Topics  # noqa: E402
from kernel.autoresearch.workflow import Workflow  # noqa: E402
from kernel.autoresearch.workspace import provision_topic  # noqa: E402

from test_workflow import (  # noqa: E402  (同目录夹具模块，非包)
    BRANCH_ACTIONS,
    REVIEWER_SESSION,
    VerifiedHost,
    audit_report,
)
from test_remote_workflow import RemoteCase, RESOURCE as REMOTE_RESOURCE  # noqa: E402

TOPIC = "T1"
TOPIC_B = "T2"
TOPIC_C = "T3"
SESSION = "session-owner"
OWNER = "owner-unit"
KEY = b"explicit-synthetic-fixture-key-000000000"
MARKER_SRC = (
    "import pathlib, sys\n"
    "out = pathlib.Path(sys.argv[1])\n"
    "with out.open('a', encoding='utf-8') as handle:\n"
    "    handle.write(sys.argv[2] + '\\n')\n"
    "raise SystemExit(int(sys.argv[3]) if len(sys.argv) > 3 else 0)\n"
)


class SyntheticHost:
    """最小宿主：只实现 `Monitoring` 需要的 `host.session(id)`。

    记录形状与 `monitoring.HOST_PROTOCOL` 的 DSHHost 一致（顶层会话 + 真实用户原话），
    ⛔ 它不是生产宿主：只为离线核「真实 Monitoring 事件账 + check_start」这条链路。
    """

    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = []

    def session(self, session_id):
        self.calls.append(session_id)
        record = self.sessions.get(session_id)
        if record is None:
            raise RuntimeError(f"合成宿主没有这个会话：{session_id}")
        return json.loads(json.dumps(record))


def synthetic_records(*sessions):
    records = {}
    for session in sessions:
        records[session] = {
            "session_id": session,
            "header": {"id": session, "parentSession": None, "origin": "top-level",
                       "delegationDepth": 0, "isSeeded": False},
            "events": [
                {"seq": 1, "type": "user/message", "text": "先把通过题数从 3 提到 4，别动题单",
                 "source": {"rpcId": f"rpc-{session}", "kind": "user"}, "is_user": True},
                {"seq": 2, "type": "assistant/message", "text": "收到"},
            ],
        }
    return records


def progress_spec(root):
    return {
        "version": 1,
        "name": "advancement",
        "owner": OWNER,
        "state": {"root": ".state/progress", "db": ".state/progress.sqlite3"},
        "poll_sec": 20,
        "quiet": {"window_sec": 600, "max_window_sec": 1200},
        "round": {"kind": "direct_instruction", "basis": "合成夹具：本轮由用户直接授权"},
        "overdue": {"max_age_sec": 304},
        "collectors": [
            {"key": "flag", "kind": "file", "path": str(Path(root) / "flag.txt"),
             "aspect": "exists"},
        ],
        "events": [{"key": "flag", "op": "appeared"}],
    }


class AdvanceFixture(unittest.TestCase):
    """真实工作区：init → provision_topic（真 venv / 真 git）→ 真实预算预留与真实监控账。"""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git 不可用，无法 provision_topic")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name) / "ws"
        init_output = self.ws.parent / "init-output.txt"
        with init_output.open("wb") as stream:
            initialized = subprocess.run(
                [sys.executable, "-X", "utf8", "-B", str(ROOT / "research.py"),
                 "init", "--workspace", str(self.ws)],
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        self.assertEqual(initialized.returncode, 0,
                         init_output.read_text(encoding="utf-8", errors="replace"))
        self.db = self.ws / ".autoresearch" / "runtime.sqlite3"
        self.store = Store(self.db)
        self.topics = Topics(self.store)
        # 预算链：真实 Accounts + 真实预留（每个 plan id 一笔）。
        self.accounts = Accounts(
            self.store,
            ApprovalEvidence({"fixture": SourcePolicy(HMACSource(KEY),
                                                      frozenset({"fixture-user"}))}),
        )
        self.admission = Admission(self.ws, store=self.store)
        # 监控：真实事件账 + 真实 check_start；会话证据来自合成 host。
        self.host = SyntheticHost(synthetic_records(SESSION))
        self.monitoring = Monitoring(self.ws, db_path=str(self.db), host=self.host)
        # 资源租约：真实 Scheduler（只读入口读它写下的 resources 行）。
        self.scheduler = Scheduler(self.store)
        # 专题与代码落点：T1 是真实 provision_topic；T2/T3 合成 provision 记录（共用 T1 的 venv）。
        self.provision = provision_topic(
            self.ws, topic=TOPIC, objective="把通过题数从 3 提到 4",
            requirements="用户要求：先小样本验证机制，别动题单",
            owner_session=SESSION, create_environment=True)
        self.python = str((self.ws / self.provision["python"]).absolute())
        self.topic_dir = self.ws / "topics" / TOPIC
        self.doc = self.topic_dir / "TOPIC.md"
        (self.ws / "evidence").mkdir(exist_ok=True)
        self.readings = self.ws / "evidence" / "readings.md"
        self.readings.write_text("上一轮读数：通过题数 3（逐题核对）\n", encoding="utf-8")
        (self.ws / "submission").mkdir(exist_ok=True)
        self.entry = self.ws / "submission" / "entry.py"
        self.entry.write_text("def parse_args(argv):\n    return argv\n", encoding="utf-8")
        (self.ws / "plans").mkdir(exist_ok=True)
        self.markers = {}
        for topic in (TOPIC, TOPIC_B, TOPIC_C):
            self._ensure_topic(topic)
        # 真实 Governance：真实审计记录 + 合成独立性核验器（生产是 DSHHost）。
        self.verifier = VerifiedHost()
        self.flow = Workflow(
            str(self.ws), db=str(self.db), verifier=self.verifier,
            monitor_check=self.monitoring.check_start,
            authorization_check=self.admission.check)
        self.flow.gov.sync_user_update(source_session=SESSION, seq=1,
                                       quote="先把通过题数从 3 提到 4，别动题单")
        self.flow.gov.assess_update(source_session=SESSION, covers_seq=1, decision="none",
                                    quote="这句不立为跨轮意图，只是本轮目标", answer_seq=2,
                                    actor="main")
        self.monitoring.register(SESSION, state="none",
                                 reason="合成夹具：用户回答不立专项意图")
        # 入向台账（真实 Inbox/Delivery 表）：read_overdue 的真实数据源。
        self.delivery = Delivery(self.store)
        self.inbox = Inbox(self.store, accounts=self.accounts, delivery=self.delivery)
        # progress 事件账：真实 spec + 只读 overdue 源。
        self.spec_path = self.ws / "progress-spec.json"
        self.spec_path.write_text(json.dumps(progress_spec(self.ws), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        self.progress = self._progress()

    # -- 基建 ---------------------------------------------------------------

    def _ensure_topic(self, topic):
        if topic == TOPIC:
            code = self.ws / self.provision["code_path"]
            record = self.provision
        else:
            self.topics.create_topic(topic, title=f"{topic} 的组合实验",
                                     objective=f"{topic}：准备组合与配对",
                                     actor=SESSION)
            code = self.ws / "worktrees" / topic
            code.mkdir(parents=True, exist_ok=True)
            (code / ".git").mkdir(exist_ok=True)
            topic_dir = self.ws / "topics" / topic
            topic_dir.mkdir(parents=True, exist_ok=True)
            (topic_dir / "TOPIC.md").write_text(
                f"# {topic}\n\n## 2 假设与假设迭代\n\n- H1：组合与对照逐题配对后可比较（待验证）\n",
                encoding="utf-8")
            record = {"topic": topic, "owner_session": SESSION,
                      "objective": f"{topic}：准备组合与配对",
                      "created_at": time.time(), "status": "provisioned",
                      "code_path": code.relative_to(self.ws).as_posix(),
                      # 合成夹具：共用 T1 的真实 venv（只为排序/冲突用例，不声称依赖隔离）。
                      "python": self.provision["python"],
                      "environment_verified": True}
            (self.ws / "topics" / topic / "provision.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        marker = code / "marker.py"
        marker.write_text(MARKER_SRC, encoding="utf-8")
        self.markers[topic] = marker
        return record

    def _progress(self, **kwargs):
        kwargs.setdefault("owner", OWNER)
        kwargs.setdefault("goal", {"state": "complete", "coverage": "none"})
        kwargs.setdefault("overdue", lambda: read_overdue(self.db))
        return progress.Progress(self.ws, json.loads(json.dumps(progress_spec(self.ws))),
                                 spec_path=self.spec_path, **kwargs)

    def argv_for(self, topic=TOPIC, label="run"):
        return [self.python, str(self.markers[topic]),
                str(self.ws / f"marker-{topic}.log"), label, "0"]

    def grant(self, exp_id, *, scope="small", units=3):
        series = f"series-{exp_id}"
        reservation = f"reservation-{exp_id}"
        self.store.ensure_experiment(exp_id)
        event = {"event_id": f"grant-{exp_id}", "series_id": series, "exp_id": exp_id,
                 "scope": scope, "action": "grant", "expires_at": time.time() + 3600,
                 "total_units": 10, "principal": "fixture-user"}
        event["signature"] = hmac.new(KEY, canonical_json(event).encode(),
                                      hashlib.sha256).hexdigest()
        self.accounts.authorize("fixture", event, f"grant-request-{exp_id}")
        self.accounts.reserve(series, exp_id, scope, units, reservation)
        return {"scope": scope, "series_id": series, "reservation_id": reservation}

    def plan(self, plan_id, *, topic=TOPIC, stage="mechanism", resources=None,
             authorization=True, **overrides):
        payload = {
            "id": plan_id,
            "goal": "验证收紧入口参数是否提高通过题数",
            "hypothesis": {"id": "H1",
                           "text": "收紧入口参数会让通过题数至少多 1 题"},
            "change_set": [{"id": "c1", "description": "把入口参数校验改成只接受整数",
                            "hypothesis_id": "H1"}],
            "criteria": {"mode": "confirmatory", "metric": "通过题数", "direction": "increase",
                         "threshold": 1, "source": "evidence/readings.md 上一轮逐题读数",
                         "rationale": "上一轮只差 1 题，门槛取 1"},
            "baseline": {"label": "上一轮冻结包", "fingerprint": "sha256:deadbeef"},
            "case_sets": {"target": ["t1", "t2"], "regression": ["r1", "r2"],
                          "control": ["c1"]},
            "previous_passes": ["r1"],
            "branches": {kind: {"action": action} for kind, action in BRANCH_ACTIONS.items()},
            "budget": {"max_cost_units": 1, "max_seconds": 60},
            "input_files": ["evidence/readings.md"],
            "execution_argv": list(self.argv_for(topic)),
            "implementation": {
                "expected_behavior": "入口只接受整数时非法参数被拒绝",
                "load_path": "submission/entry.py 的 parse_args",
                "trigger": "评测器传入字符串参数时",
                "observation": "非法参数返回非零退出码并打印原因",
                "non_activation": "非法参数仍被接受并继续运行",
                "files": [{"path": "submission/entry.py", "locator": "L1-L2"}],
            },
            "execution": {"scope": "local", "resource_kind": "cpu"},
        }
        if stage is not None:
            payload["execution"]["stage"] = stage
        if resources is not None:
            payload["execution"]["resources"] = resources
        if authorization is True:
            payload["authorization"] = self.grant(plan_id)
        elif isinstance(authorization, dict):
            payload["authorization"] = authorization
        payload.update(overrides)
        return payload

    def write_plan(self, plan_id, payload, *, directory="plans"):
        path = self.ws / directory / f"{plan_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"{directory}/{plan_id}.json"

    def prepare(self, plan_id, *, topic=TOPIC, **plan_kwargs):
        payload = self.plan(plan_id, topic=topic, **plan_kwargs)
        rel = self.write_plan(plan_id, payload)
        prepared = self.flow.prepare(topic=topic, plan=rel, owner_session=SESSION)
        return {"plan": rel, "payload": payload, "prepared": prepared}

    def audit(self, plan_id):
        prepared = self.flow.gov.prepare_audit(plan_id, actor="main")
        self.flow.gov.record_audit(
            prepared["prepared_id"], audit_report(prepared["prepared_id"]),
            actor=REVIEWER_SESSION,
            evidence={"session_id": REVIEWER_SESSION, "seq": 7})
        return prepared

    def ready(self, plan_id, *, topic=TOPIC, **plan_kwargs):
        built = self.prepare(plan_id, topic=topic, **plan_kwargs)
        self.audit(plan_id)
        return built

    def wake(self):
        """产生一次真实唤醒：基准观测 → 外部文件出现 → tick 判定 wake。"""
        self.progress.probe()
        (self.ws / "flag.txt").write_text("x", encoding="utf-8")
        decision = self.progress.tick()
        self.assertIn(decision["verdict"], ("wake", "undecidable"), decision)
        return decision

    def add_overdue_inbound(self, event_id="m1", age_sec=900):
        self.store.ensure_experiment("P1")
        self.inbox.receive("fixture", {
            "event_id": event_id, "exp_id": "P1", "create_time": time.time() - age_sec,
            "recipient": "user@example.com", "text": "用户问了一个问题（等回复）",
        })

    def add_trusted_reply(self, event_id="m1", source="fixture", exp_id="P1"):
        payload = {"exp_id": exp_id, "kind": "reply", "body": "已回复",
                   "artifact": None, "sha256": None, "recipient": "user@example.com",
                   "event_reply_to": {"event_id": event_id, "source": source,
                                      "exp_id": exp_id, "recipient": "user@example.com"}}
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO delivery(exp_id,kind,request_key,wire_key,payload_json,payload_hash,"
                "status,created_at,updated_at) VALUES(?,?,?,?,?,?,'delivered',?,?)",
                (exp_id, "reply", f"reply-{event_id}", f"wire-{event_id}",
                 canonical_json(payload),
                 hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
                 time.time(), time.time()))
            delivery_id = conn.execute(
                "SELECT id FROM delivery WHERE request_key=?",
                (f"reply-{event_id}",)).fetchone()[0]
            conn.execute(
                "INSERT INTO delivery_receipts(delivery_id,exp_id,receipt_json,acked_at)"
                " VALUES(?,?,?,?)",
                (delivery_id, exp_id, json.dumps({"message_id": "m-1"}), time.time()))
            conn.commit()
        finally:
            conn.close()
        return delivery_id

    def assurance_ok(self, **_kwargs):
        """正常返回的 assurance 汇总夹具：required 项全 ok，旧轮次诊断性 unknown 不阻。"""
        return {"ok": True, "exit_code": 0,
                "items": [
                    {"name": "workflow.preparation", "status": "ok",
                     "required": True, "applicable": True},
                    {"name": "workflow.review", "status": "unknown",
                     "required": False, "applicable": True,
                     "problems": ["历史轮次没有复盘（研究交付诊断，非本轮硬阻）"]},
                ],
                "counts": {"ok": 1, "blocked": 0, "unknown": 1},
                "blocked": [], "unknown": [], "required_missing": []}

    def preflight_kwargs(self, plan_rel, *, topic=TOPIC, argv=None, **extra):
        keyword = dict(next_plan=plan_rel, topic=topic, owner=SESSION,
                       intents={"none": True}, argv=argv or self.argv_for(topic),
                       workspace=str(self.ws),
                       assurance=self.assurance_ok,
                       # 真实 Monitoring（事件账在本 workspace 的 runtime 库）与真实 Admission。
                       monitor_check=self.monitoring.check_start,
                       authorization_check=self.admission.check,
                       verifier=self.verifier)
        keyword.update(extra)
        return keyword

    def schedule_kwargs(self, **extra):
        keyword = dict(workspace=str(self.ws), assurance=self.assurance_ok,
                       monitor_check=self.monitoring.check_start,
                       authorization_check=self.admission.check,
                       verifier=self.verifier)
        keyword.update(extra)
        return keyword

    def snapshot_tree(self):
        entries = {}
        for path in sorted(self.ws.rglob("*")):
            relative = path.relative_to(self.ws).as_posix()
            if path.is_file():
                stat = path.stat()
                entries[relative] = ("file", stat.st_size, stat.st_mtime_ns)
            else:
                entries[relative] = ("dir",)
        return entries


# ---------------------------------------------------------------------------
# D2：outcome('advance') 落账前核真实 overdue
# ---------------------------------------------------------------------------


class OutcomeOverdueTests(AdvanceFixture):
    def test_overdue_inbound_blocks_advance_and_trusted_reply_unblocks(self):
        self.wake()
        self.add_overdue_inbound()
        with self.assertRaises(progress.OutcomeBlocked) as caught:
            self.progress.outcome("advance", action="推进了下一轮")
        self.assertIn("先回复用户", str(caught.exception))
        self.assertEqual([event for event in self.progress.events()
                          if event["kind"] == "outcome"], [],
                         "超期未回复时不许落 advance（也不许落别的出口）")
        # 可信回复落账后（真实 read_overdue 的 join 判据）才允许 advance。
        self.add_trusted_reply()
        before = read_overdue(self.db)
        self.assertTrue(before["checked"], before)
        self.assertEqual(before["count"], 0, before)
        self.progress.outcome("advance", action="推进了下一轮")
        payload = self.progress.events()[-1]["payload"]
        self.assertEqual(payload["kind"], "advance")
        self.assertTrue(payload["overdue_gate"]["checked"])
        self.assertEqual(payload["overdue_gate"]["count"], 0)
        self.assertNotIn("next", payload, "没写显式下一动作时不能有 next")

    def test_unknown_overdue_source_blocks_advance(self):
        self.wake()
        # 1) 完全没有只读 overdue 源 ⇒ 不可判 ⇒ 拒
        no_source = self._progress(overdue=None)
        with self.assertRaises(progress.OutcomeBlocked):
            no_source.outcome("advance", action="推进")
        # 2) 源抛错 ⇒ 不可判 ⇒ 拒（⛔ 不许把异常读成「没有超期」）
        def boom():
            raise RuntimeError("inbox 读不到")

        broken = self._progress(overdue=boom)
        with self.assertRaises(progress.OutcomeBlocked) as caught:
            broken.outcome("advance", action="推进")
        self.assertIn("核不了当前入向超期", str(caught.exception))
        self.assertEqual([event for event in broken.events() if event["kind"] == "outcome"], [])

    def test_static_overdue_snapshot_cannot_advance(self):
        self.wake()
        fake = self._progress(overdue={"max_age_sec": 304, "items": []})
        with self.assertRaises(progress.OutcomeBlocked) as caught:
            fake.outcome("advance", action="推进")
        self.assertIn("不可信", str(caught.exception))
        self.assertEqual([event for event in fake.events()
                          if event["kind"] == "outcome"], [])

    def test_safe_action_exempts_d2_but_cannot_declare_next_round(self):
        self.wake()
        self.add_overdue_inbound()
        recorded = self.progress.outcome("advance", action="安全停机并取包", safe_action="stop")
        payload = self.progress.events()[-1]["payload"]
        self.assertEqual(payload["safe_action"], "stop")
        self.assertTrue(payload["overdue_gate"]["skipped"])
        self.assertTrue(recorded["safety_only"])
        # 安全收尾不是下一轮：不能同时声明 next_plan 五件
        with self.assertRaises(progress.ProgressError):
            self.progress.outcome("advance", action="停机", safe_action="stop",
                                  next_plan="plans/P1.json", topic=TOPIC, owner=SESSION,
                                  intents={"none": True}, argv=self.argv_for())
        # nothing / problem 本来就不受 D2 闸影响（安全退出通道）
        self.progress.rearm(note="安全收尾已记录，重挂监视")
        other = self._progress()
        (self.ws / "flag.txt").unlink(missing_ok=True)
        other.probe()
        (self.ws / "flag.txt").write_text("z", encoding="utf-8")
        self.assertIn(other.tick()["verdict"], ("wake", "undecidable"))
        other.outcome("nothing", next_condition="等用户回复后再说")


# ---------------------------------------------------------------------------
# 只读 preflight：五道真实闸 + 只读 overdue + assurance 解释清单
# ---------------------------------------------------------------------------


class PreflightTests(AdvanceFixture):
    def test_full_fixture_gives_executable_command_and_writes_nothing(self):
        built = self.ready("P1", resources={"keys": [], "gpu_units": 0})
        before = self.snapshot_tree()
        identity = file_identity(self.db)
        result = self.progress.preflight(**self.preflight_kwargs(built["plan"]))
        self.assertTrue(result["ok"], result["checks"])
        self.assertEqual(result["status"], "runnable", result)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["writes"], [])
        command = result["next_command"]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], sys.executable)
        self.assertIn("-X", command)
        self.assertTrue(Path(command[command.index("-B") + 1]).is_absolute())
        self.assertTrue(str(command[command.index("-B") + 1]).endswith("workflow_cli.py"))
        self.assertIn("launch", command)
        self.assertIn("--workspace", command)
        self.assertIn("--intent-none", command)
        self.assertEqual(command[command.index("--") + 1:], self.argv_for())
        for name in ("declarations", "plan_frozen", "plan_sources", "argv", "predecessor",
                     "execution_target", "audit", "monitoring", "admission", "overdue",
                     "command"):
            self.assertEqual(result["checks"][name]["state"], "pass",
                             (name, result["checks"][name]))
        self.assertEqual(result["plan"]["stage"]["stage"], "mechanism")
        self.assertEqual(result["plan"]["resources"]["gpu_units"], 0)
        # 只读：目录/文件集合、字节数、mtime、sqlite identity 全部不变
        self.assertEqual(self.snapshot_tree(), before, "preflight 不许新增 / 改任何文件")
        self.assertEqual(file_identity(self.db), identity, "preflight 不许写状态库")

    def test_missing_audit_and_missing_admission_are_per_item(self):
        built = self.prepare("P1")  # 故意不审计
        result = self.progress.preflight(**self.preflight_kwargs(built["plan"]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["audit"]["state"], "blocked",
                         result["checks"]["audit"])
        self.assertIn("audit", result["blocking_failures"])
        self.assertIsNone(result["next_command"])

        # 真实 Admission：预留不存在 ⇒ 执行准入阻断（不是夹具）
        second = self.write_plan("P2", self.plan(
            "P2", authorization={"scope": "small", "series_id": "series-P2",
                                 "reservation_id": "reservation-P2"}))
        self.flow.prepare(topic=TOPIC, plan=second, owner_session=SESSION)
        self.audit("P2")
        result2 = self.progress.preflight(**self.preflight_kwargs(second))
        self.assertFalse(result2["ok"])
        self.assertEqual(result2["checks"]["admission"]["state"], "blocked",
                         result2["checks"]["admission"])
        self.assertIn("预算", json.dumps(result2["checks"]["admission"], ensure_ascii=False))

    def test_unfrozen_plan_and_missing_db_are_honest(self):
        unfrozen = self.write_plan("P9", self.plan("P9"))
        result = self.progress.preflight(**self.preflight_kwargs(unfrozen))
        self.assertFalse(result["ok"])
        self.assertIn(result["checks"]["plan_frozen"]["state"], ("missing", "blocked"))
        self.assertIsNone(result["next_command"])

        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        unknown = self.progress.preflight(**self.preflight_kwargs(
            unfrozen, workspace=str(empty)))
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["status"], "unknown", unknown)
        self.assertIn("plan_frozen", unknown["unknown"])
        self.assertIsNone(unknown["next_command"])

    def test_no_overdue_source_never_greens_the_command(self):
        built = self.ready("P1")
        keyword = self.preflight_kwargs(built["plan"])
        bare = progress.Progress(self.ws, json.loads(json.dumps(progress_spec(self.ws))),
                                 spec_path=self.spec_path, owner=OWNER,
                                 goal={"state": "complete", "coverage": "none"})
        result = bare.preflight(**keyword)
        self.assertFalse(result["ok"], "缺只读 overdue 源时⛔ 不许给执行命令")
        self.assertEqual(result["checks"]["overdue"]["state"], "unknown")
        self.assertEqual(result["checks"]["overdue"]["blocking"], True)
        self.assertIsNone(result["next_command"])

    def test_static_overdue_snapshot_cannot_green_preflight(self):
        built = self.ready("P1")
        result = self.progress.preflight(**self.preflight_kwargs(
            built["plan"], overdue={"max_age_sec": 304, "items": []}))
        self.assertFalse(result["ok"], "静态快照 count=0 ⛔ 不能当 D2 依据")
        self.assertEqual(result["checks"]["overdue"]["state"], "unknown")
        self.assertEqual(result["checks"]["overdue"]["evidence"]["trust"], "untrusted")
        self.assertIsNone(result["next_command"])

    def test_real_inbound_blocks_preflight_and_trusted_reply_unblocks(self):
        built = self.ready("P1")
        self.add_overdue_inbound()
        blocked = self.progress.preflight(**self.preflight_kwargs(built["plan"]))
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["checks"]["overdue"]["state"], "blocked")
        self.assertIsNone(blocked["next_command"])
        self.add_trusted_reply()
        ready = self.progress.preflight(**self.preflight_kwargs(built["plan"]))
        self.assertTrue(ready["ok"], ready["checks"])
        self.assertEqual(ready["checks"]["overdue"]["state"], "pass")
        self.assertEqual(ready["checks"]["overdue"]["evidence"]["trust"], "trusted")

    def test_assurance_unknown_is_diagnostic_but_required_unknown_blocks(self):
        built = self.ready("P1")

        def diagnostic_only(**_kwargs):
            return {"ok": True, "exit_code": 0,
                    "items": [
                        {"name": "workflow.review", "status": "unknown",
                         "required": False, "applicable": True, "problems": ["旧轮次无复盘"]},
                        {"name": "monitoring.intent", "status": "ok",
                         "required": True, "applicable": True},
                    ],
                    "counts": {"ok": 1, "blocked": 0, "unknown": 1},
                    "blocked": [], "unknown": [], "required_missing": []}

        ok = self.progress.preflight(**self.preflight_kwargs(built["plan"],
                                                             assurance=diagnostic_only))
        self.assertTrue(ok["ok"], ok["checks"]["assurance"])
        self.assertEqual(ok["checks"]["assurance"]["state"], "pass")
        self.assertTrue(ok["checks"]["assurance"]["evidence"]["diagnostics"])

        def required_unknown(**_kwargs):
            return {"ok": True, "exit_code": 0,
                    "items": [{"name": "decisions.approval", "status": "unknown",
                               "required": True, "applicable": True,
                               "problems": ["审批链不可判"]}],
                    "counts": {"ok": 0, "blocked": 0, "unknown": 1},
                    "blocked": [], "unknown": [], "required_missing": []}

        blocked = self.progress.preflight(**self.preflight_kwargs(built["plan"],
                                                                  assurance=required_unknown))
        self.assertFalse(blocked["ok"], "required+applicable 的 unknown ⛔ 不自动放行")
        self.assertEqual(blocked["checks"]["assurance"]["state"], "blocked")
        self.assertIn("assurance", blocked["blocking_failures"])
        self.assertIsNone(blocked["next_command"])

        # 异常 / 未接线 / 显式跳过：不知道失效了哪些 required 检查 ⇒ unknown 且不给命令。
        def broken(**_kwargs):
            raise RuntimeError("assurance 内部坏了")

        failed = self.progress.preflight(**self.preflight_kwargs(
            built["plan"], assurance=broken))
        self.assertFalse(failed["ok"], "assurance 内部异常 ⛔ 不能当 optional 聚合器放行")
        self.assertEqual(failed["checks"]["assurance"]["state"], "unknown")
        self.assertTrue(failed["checks"]["assurance"]["blocking"])
        self.assertIsNone(failed["next_command"])

        skipped = self.progress.preflight(**self.preflight_kwargs(
            built["plan"], assurance=None))
        self.assertFalse(skipped["ok"], "没有 assurance 汇总 ⛔ 不能当放行")
        self.assertEqual(skipped["checks"]["assurance"]["state"], "unknown")
        self.assertIsNone(skipped["next_command"])

    def test_real_assurance_module_integrates_as_explanation(self):
        """真实 assurance.checklist 接线：它返回什么就按 required/applicable 判；⛔ 不吞异常。"""
        built = self.ready("P1")
        keyword = self.preflight_kwargs(built["plan"])
        keyword.pop("assurance")  # 走自动发现（kernel.autoresearch.assurance）
        result = self.progress.preflight(**keyword)
        check = result["checks"]["assurance"]
        self.assertIn(check["state"], ("pass", "blocked", "unknown"))
        if check.get("evidence", {}).get("source") == "assurance.checklist":
            self.assertIn("items", check["evidence"])
            self.assertIn("counts", check["evidence"])
        if check["state"] != "pass":
            self.assertIsNone(result["next_command"],
                              "assurance 未通过时不许给命令")

    def test_assurance_module_discovery_uses_readonly_checklist(self):
        built = self.ready("P1")
        calls = []

        def checklist(workspace, **kwargs):
            calls.append({"workspace": workspace, **kwargs})
            return {"ok": True, "items": [{"name": "workflow.preparation", "status": "ok",
                                           "required": True, "applicable": True}],
                    "counts": {"ok": 1, "blocked": 0, "unknown": 0},
                    "blocked": [], "unknown": [], "required_missing": [], "exit_code": 0}

        module = types.SimpleNamespace(checklist=checklist)
        import kernel.autoresearch as package
        with mock.patch.dict(sys.modules, {"kernel.autoresearch.assurance": module}), \
                mock.patch.object(package, "assurance", module, create=True):
            keyword = self.preflight_kwargs(built["plan"])
            keyword.pop("assurance")
            result = self.progress.preflight(**keyword)
        self.assertTrue(result["ok"], result["checks"]["assurance"])
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0]["topic"], TOPIC)
        self.assertEqual(calls[0]["owner_session"], SESSION)
        self.assertEqual(calls[0]["exp_id"], "P1")


# ---------------------------------------------------------------------------
# 跨轮 next_round：出口 / 下一动作 / 用户停止
# ---------------------------------------------------------------------------


class NextRoundTests(AdvanceFixture):
    def test_outcome_missing_then_legacy_unknown_then_declared_next(self):
        built = self.ready("P1")
        self.wake()
        waiting = self.progress.next_round(**self.preflight_kwargs(built["plan"]))
        self.assertFalse(waiting["ok"])
        self.assertEqual(waiting["round"]["state"], "outcome_missing")
        self.assertIsNone(waiting["next_command"])

        # 历史/简单 advance 没写显式下一动作 ⇒ 诚实 unknown，⛔ 不静默绿 advance
        self.progress.outcome("advance", action="推进了，但没写下一动作")
        legacy = self.progress.next_round(**self.preflight_kwargs(built["plan"]))
        self.assertFalse(legacy["ok"])
        self.assertEqual(legacy["round"]["state"], "legacy_next_unknown")
        self.assertIsNone(legacy["next_command"])

        # 新状态：显式下一动作 + rearm 后才能拿命令
        self.progress.rearm(note="记完历史出口")
        second = self._progress()
        (self.ws / "flag.txt").unlink(missing_ok=True)
        second.probe()
        (self.ws / "flag.txt").write_text("y", encoding="utf-8")
        self.assertIn(second.tick()["verdict"], ("wake", "undecidable"))
        second.outcome("advance", action="推进到下一轮",
                       next_plan=built["plan"], topic=TOPIC, owner=SESSION,
                       intents={"none": True}, argv=self.argv_for())
        ready = second.next_round(**self.preflight_kwargs(built["plan"]))
        self.assertTrue(ready["ok"], (ready["round"], ready["preflight"]["checks"]))
        self.assertEqual(ready["round"]["state"], "advance_declared")
        self.assertIsNotNone(ready["next_command"])
        self.assertIsNotNone(ready["rearm_command"], "先重挂再用命令（A6）")
        self.assertEqual(ready["rearm_command"][-3:], ["rearm", "--owner", OWNER])

    def test_user_stop_and_safety_only_never_produce_commands(self):
        built = self.ready("P1")
        self.wake()
        stopped = self.progress.next_round(user_stop=True,
                                           **self.preflight_kwargs(built["plan"]))
        self.assertEqual(stopped["verdict"], "stopped")
        self.assertIsNone(stopped["next_command"])
        self.assertIsNone(stopped["rearm_command"])

        self.add_overdue_inbound()
        self.progress.outcome("advance", action="安全停机", safe_action="halt")
        safety = self.progress.next_round(**self.preflight_kwargs(built["plan"]))
        self.assertEqual(safety["verdict"], "safety_only")
        self.assertIsNone(safety["next_command"])
        self.assertIn("安全收尾", "".join(safety["steps"]))


# ---------------------------------------------------------------------------
# schedule：四阶段排序 / 同机器 GPU 冲突 / 零 GPU 并行 / 只读
# ---------------------------------------------------------------------------


class ScheduleTests(AdvanceFixture):
    def _three_plans(self):
        self.ready("P-MECH", topic=TOPIC, stage="mechanism",
                   resources={"keys": [], "gpu_units": 0})
        self.ready("P-COMP", topic=TOPIC_B, stage="composition",
                   resources={"keys": ["gpu:0"], "gpu_units": 1})
        self.ready("P-LARGE", topic=TOPIC_C, stage="large",
                   resources={"keys": ["gpu:0"], "gpu_units": 1})
        return [
            {"topic": TOPIC, "next_plan": "plans/P-MECH.json", "owner": SESSION,
             "intents": {"none": True}, "argv": self.argv_for(TOPIC)},
            {"topic": TOPIC_B, "next_plan": "plans/P-COMP.json", "owner": SESSION,
             "intents": {"none": True}, "argv": self.argv_for(TOPIC_B)},
            {"topic": TOPIC_C, "next_plan": "plans/P-LARGE.json", "owner": SESSION,
             "intents": {"none": True}, "argv": self.argv_for(TOPIC_C)},
        ]

    def test_three_topic_stage_order_and_large_conflict_visibility(self):
        entries = self._three_plans()
        first = self.progress.schedule(entries=entries, **self.schedule_kwargs())
        self.assertTrue(first["ok"], first)
        self.assertEqual([item["topic"] for item in first["recommended"]],
                         [TOPIC, TOPIC_B, TOPIC_C])
        self.assertEqual([item["stage"]["stage"] for item in first["recommended"]],
                         ["mechanism", "composition", "large"])
        self.assertTrue(all(item["next_command"] for item in first["recommended"]))
        self.assertTrue(first["reasons"][0].startswith("#1 T1"), first["reasons"])
        # 候选之间也核资源：T2/T3 争用 gpu:0 ⇒ 只能依次执行，⛔ 不同时列可并行
        contention = {item["topic"]: item["contention_with"]
                      for item in first["recommended"]}
        self.assertTrue(contention[TOPIC_B], contention)
        self.assertTrue(contention[TOPIC_C], contention)
        self.assertFalse(any(item["parallel_safe"] for item in first["recommended"]))

        # 真题单在跑：同 GPU 的小实验不得推荐；零 GPU 的可以并行。
        self.flow.start(topic=TOPIC_C, plan="plans/P-LARGE.json", owner_session=SESSION,
                        argv=self.argv_for(TOPIC_C), intent_none=True, finish=False)
        self.scheduler.activate("EXP-LARGE", goal="large 题单在跑")
        owned = self.scheduler.takeover("EXP-LARGE", "worker-owner", lease_seconds=600)
        self.scheduler.enqueue("EXP-LARGE", "task-1", {"note": "large"},
                               "worker-owner", owned["generation"], resources=["gpu:0"])
        claimed = self.scheduler.claim("EXP-LARGE", "worker-owner",
                                      owned["generation"], "worker-1")
        self.assertIsNotNone(claimed)
        second = self.progress.schedule(
            entries=[entries[0], entries[1]], **self.schedule_kwargs())
        self.assertTrue(second["ok"], second)
        runnable = {item["topic"]: item for item in second["recommended"]}
        self.assertIn(TOPIC, runnable)
        self.assertTrue(runnable[TOPIC]["parallel_safe"], runnable[TOPIC])
        self.assertTrue(runnable[TOPIC]["zero_gpu"])
        blocked = {item["topic"]: item for item in second["blocked"]}
        self.assertIn(TOPIC_B, blocked, second["blocked"])
        self.assertIn("gpu:0", json.dumps(blocked[TOPIC_B]["problems"], ensure_ascii=False))
        self.assertTrue(second["running"], second["running"])
        self.assertEqual(second["running"][0]["topic"], TOPIC_C)
        self.assertEqual(second["running"][0]["stage"]["stage"], "large")
        self.assertTrue(any(item["resource_key"] == "gpu:0" for item in second["leases"]))
        # 并行结论各归各轮：推荐项只带自己的命令，不混入大题的读数
        self.assertEqual(len(second["next_commands"]), 1)
        self.assertNotIn(TOPIC_C, [item["topic"] for item in second["recommended"]])

    def test_explicit_resources_and_stage_cannot_override_frozen_plan(self):
        built = self.ready("P1", stage="mechanism", resources={"keys": [], "gpu_units": 0})
        # 调用方显式 resources 与冻结计划声明矛盾 ⇒ unknown、没有命令（不覆盖计划）
        contradicted = self.progress.preflight(**self.preflight_kwargs(
            built["plan"], resources={"keys": ["gpu:0"], "gpu_units": 1}))
        self.assertFalse(contradicted["ok"])
        self.assertEqual(contradicted["plan"]["resources"]["known"], False)
        self.assertIn("不一致", contradicted["plan"]["resources"]["why"])
        self.assertIsNone(contradicted["next_command"])
        # 显式 stage 与计划研究阶段矛盾 ⇒ 同样 unknown
        stage_conflict = self.progress.schedule(
            entries=[{"topic": TOPIC, "next_plan": built["plan"], "owner": SESSION,
                      "intents": {"none": True}, "argv": self.argv_for(TOPIC),
                      "stage": "large", "resources": {"keys": [], "gpu_units": 0}}],
            **self.schedule_kwargs())
        self.assertFalse(stage_conflict["ok"])
        self.assertEqual(stage_conflict["recommended"], [])
        items = stage_conflict["blocked"] + stage_conflict["unknown"]
        self.assertEqual(len(items), 1, stage_conflict)
        self.assertEqual(items[0]["stage"]["known"], False)
        self.assertIsNone(items[0]["next_command"])

    def test_machine_alias_is_not_guessed_as_no_conflict(self):
        self.ready("P-MACHINE", topic=TOPIC, stage="mechanism",
                   resources={"machine": "m1"})
        result = self.progress.schedule(
            entries=[{"topic": TOPIC, "next_plan": "plans/P-MACHINE.json", "owner": SESSION,
                      "intents": {"none": True}, "argv": self.argv_for(TOPIC)}],
            **self.schedule_kwargs())
        self.assertFalse(result["ok"])
        self.assertEqual(result["recommended"], [])
        item = result["unknown"][0]
        self.assertEqual(item["resources"]["known"], False)
        self.assertIn("machine", item["resources"]["why"])
        self.assertIn("resource_key", item["resources"]["why"])

    def test_unknown_stage_or_resources_are_not_recommended(self):
        # 旧计划没有 stage / resources：⛔ 不自动认 mechanism，也不判「没有资源」
        self.ready("P-OLD", topic=TOPIC, stage=None, resources=None)
        result = self.progress.schedule(
            entries=[{"topic": TOPIC, "next_plan": "plans/P-OLD.json", "owner": SESSION,
                      "intents": {"none": True}, "argv": self.argv_for(TOPIC)}],
            **self.schedule_kwargs())
        self.assertFalse(result["ok"])
        self.assertEqual(result["recommended"], [])
        self.assertEqual(len(result["unknown"]), 1)
        item = result["unknown"][0]
        self.assertEqual(item["stage"]["known"], False)
        self.assertEqual(item["resources"]["known"], False)
        self.assertIsNone(item["next_command"])
        joined = json.dumps(item["problems"], ensure_ascii=False)
        self.assertIn("研究阶段未知", joined)
        self.assertIn("资源占用未知", joined)

    def test_remote_scope_is_unknown_not_a_local_command(self):
        self.ready("P-REMOTE", topic=TOPIC, stage="large",
                   resources={"keys": ["gpu:0"], "gpu_units": 1})
        payload = json.loads((self.ws / "plans" / "P-REMOTE.json").read_text(encoding="utf-8"))
        payload["execution"]["scope"] = "remote"
        (self.ws / "plans" / "P-REMOTE.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 改计划会让准备快照过期 ⇒ 重新 prepare + audit（仍然 scope=remote）
        self.flow.prepare(topic=TOPIC, plan="plans/P-REMOTE.json", owner_session=SESSION)
        self.audit("P-REMOTE")
        result = self.progress.schedule(
            entries=[{"topic": TOPIC, "next_plan": "plans/P-REMOTE.json", "owner": SESSION,
                      "intents": {"none": True}, "argv": self.argv_for(TOPIC)}],
            **self.schedule_kwargs())
        self.assertFalse(result["ok"])
        self.assertEqual(result["recommended"], [])
        items = result["blocked"] + result["unknown"]
        self.assertEqual(len(items), 1, result)
        item = items[0]
        self.assertEqual(item["plan"]["execution_scope"], "remote")
        self.assertIsNone(item["next_command"])
        self.assertIn("远端", json.dumps(item["problems"], ensure_ascii=False))

    def test_schedule_is_readonly(self):
        entries = self._three_plans()
        before = self.snapshot_tree()
        identity = file_identity(self.db)
        result = self.progress.schedule(entries=entries, **self.schedule_kwargs())
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["writes"], [])
        self.assertEqual(self.snapshot_tree(), before, "schedule 不许新增 / 改任何文件")
        self.assertEqual(file_identity(self.db), identity, "schedule 不许写状态库")

    def test_user_stop_returns_no_recommendation(self):
        entries = self._three_plans()
        result = self.progress.schedule(entries=entries,
                                        **self.schedule_kwargs(user_stop=True))
        self.assertEqual(result["verdict"], "stopped")
        self.assertEqual(result["recommended"], [])
        self.assertEqual(result["next_commands"], [])


# ---------------------------------------------------------------------------
# 远端分支：真实 progress_remote.remote_preflight + 真实 RemoteWorkflow 夹具
# ---------------------------------------------------------------------------


class RemotePreflightIntegrationTests(RemoteCase):
    """用 `tests/test_remote_workflow.py` 的真实远端夹具（真预算 / 真所有权 / 假传输）。"""

    def _progress(self):
        return progress.Progress(
            self.ws, json.loads(json.dumps(progress_spec(self.ws))),
            owner="owner-unit", goal={"state": "complete", "coverage": "none"},
            overdue=lambda: {"checked": True, "max_age_sec": 304,
                             "items": [], "overdue": [], "count": 0})

    def assurance_ok(self, **_kwargs):
        return {"ok": True, "exit_code": 0,
                "items": [{"name": "workflow.preparation", "status": "ok",
                           "required": True, "applicable": True}],
                "counts": {"ok": 1, "blocked": 0, "unknown": 0},
                "blocked": [], "unknown": [], "required_missing": []}

    def _kwargs(self, plan_rel, **extra):
        keyword = dict(
            next_plan=plan_rel, topic=TOPIC, owner=SESSION,
            intents={"none": True}, argv=self.argv, workspace=str(self.ws),
            monitor_check=self.monitor_ok,
            authorization_check=Admission(self.ws, store=self.store).check,
            verifier=self.verifier, allow_fixture_gates=True,
            assurance=self.assurance_ok,
            overdue=lambda: {"checked": True, "max_age_sec": 304,
                             "items": [], "overdue": [], "count": 0},
        )
        keyword.update(extra)
        return keyword

    def test_real_helper_ready_gives_remote_start_command(self):
        rel = self.ready()
        self.provision()
        result = self._progress().preflight(**self._kwargs(rel))
        self.assertTrue(result["ok"], result["checks"])
        self.assertEqual(result["execution_scope"], "remote")
        remote = result["checks"]["remote"]
        self.assertEqual(remote["state"], "pass", remote)
        self.assertEqual(remote["evidence"]["wiring"]["fixture_gates"], True)
        command = result["next_command"]
        self.assertTrue(str(command[command.index("-B") + 1]).endswith("remote_cli.py"))
        self.assertIn("start", command)
        self.assertEqual(command[command.index("--resource") + 1], REMOTE_RESOURCE)
        self.assertIn("--intent-none", command)
        self.assertEqual(command[command.index("--") + 1:], self.argv)
        self.assertNotIn("workflow_cli.py", " ".join(command), "远端不能冒用本地 launch")

    def test_schedule_remote_candidate_uses_remote_command(self):
        rel = self.ready()
        self.provision()
        Scheduler(self.store)  # 建 Runtime 的 resources 表（只读排序要读真实租约表）
        result = self._progress().schedule(
            entries=[{"topic": TOPIC, "next_plan": rel, "owner": SESSION,
                      "intents": {"none": True}, "argv": self.argv,
                      "stage": "large", "resources": [REMOTE_RESOURCE]}],
            workspace=str(self.ws), monitor_check=self.monitor_ok,
            authorization_check=Admission(self.ws, store=self.store).check,
            verifier=self.verifier, allow_fixture_gates=True,
            assurance=self.assurance_ok,
            overdue=lambda: {"checked": True, "max_age_sec": 304,
                             "items": [], "overdue": [], "count": 0})
        self.assertTrue(result["ok"], result)
        item = result["recommended"][0]
        self.assertEqual(item["stage"]["stage"], "large")
        self.assertEqual(item["plan"]["execution_scope"], "remote")
        self.assertTrue(str(item["next_command"][item["next_command"].index("-B") + 1])
                        .endswith("remote_cli.py"))
        self.assertNotIn("workflow_cli.py", " ".join(item["next_command"]))

    def test_real_assurance_remote_positive(self):
        """真实 assurance.checklist（合成 host）与远端 helper 组合必须能给出远端命令。"""
        rel = self.ready()
        self.provision()
        services, host = self._prepare_assurance_ledgers()
        keyword = self._kwargs(rel)
        keyword["assurance"] = progress._UNSET      # 走自动发现：真实 kernel.autoresearch.assurance
        keyword["assurance_host"] = host
        keyword["assurance_services"] = services
        result = self._progress().preflight(**keyword)
        self.assertTrue(result["ok"], result["checks"])
        self.assertEqual(result["checks"]["assurance"]["state"], "pass",
                         result["checks"]["assurance"])
        self.assertEqual(result["checks"]["remote"]["state"], "pass")
        command = result["next_command"]
        self.assertTrue(str(command[command.index("-B") + 1]).endswith("remote_cli.py"))

    def _prepare_assurance_ledgers(self):
        """给真实 assurance.checklist 准备真实只读台账（资源 / 决策 / 监控 / 问题）。"""
        from kernel.autoresearch.decisions import DecisionService
        from kernel.autoresearch.issues import Issues
        Scheduler(self.store)
        Delivery(self.store)
        Inbox(self.store, accounts=self.accounts, delivery=Delivery(self.store))
        DecisionService(self.store)
        host = SyntheticHost(synthetic_records(SESSION))
        monitoring = Monitoring(self.ws, db_path=str(self.db), host=host)
        monitoring.register(SESSION, state="none",
                            reason="合成夹具：用户回答不立专项意图")
        evidence = {"path": "evidence/readings.md", "locator": "L1"}
        issues = Issues(self.ws)
        record = issues.record(summary="合成夹具：离线首错", owner="owner-a",
                               command=["python3", "job.py"], exitcode=1,
                               sources=[evidence], scope="local", exp_id="P1")
        issues.update(record["issue_id"], kind="fixed", actor="owner-a",
                      text="已修复", sources=[evidence])
        issues.update(record["issue_id"], kind="verified", actor="owner-a",
                      text="复验通过", sources=[evidence],
                      command=["python3", "job.py"], exitcode=0)
        return {"monitoring": monitoring}, host

    def test_real_helper_missing_deployment_is_blocked(self):
        rel = self.ready()
        self.provision()
        self.deployment_path.unlink()
        result = self._progress().preflight(**self._kwargs(rel))
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["remote"]["state"], "blocked",
                         result["checks"]["remote"])
        self.assertIsNone(result["next_command"])

    def test_missing_remote_preflight_interface_never_uses_a_fallback(self):
        rel = self.ready()
        self.provision()
        with mock.patch('kernel.autoresearch.progress_remote.remote_preflight', None):
            result = self._progress().preflight(**self._kwargs(rel))
        self.assertFalse(result['ok'])
        self.assertEqual(result['checks']['remote']['state'], 'unknown')
        self.assertIsNone(result['next_command'])

    def test_real_helper_resource_mismatch_is_not_a_command(self):
        rel = self.ready()
        self.provision()
        result = self._progress().preflight(
            **self._kwargs(rel, resources=["machine:other/gpu0"]))
        self.assertFalse(result["ok"])
        self.assertIn(result["checks"]["remote"]["state"], ("blocked", "unknown"))
        self.assertIsNone(result["next_command"])
        self.assertIn("逐项不一致",
                      json.dumps(result["checks"]["remote"], ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI：只读入口的退出码与 JSON 形状
# ---------------------------------------------------------------------------


class ProgressCliTests(AdvanceFixture):
    def run_cli(self, args, *, timeout=120):
        with tempfile.TemporaryDirectory() as folder:
            out_path = Path(folder) / "stdout.bin"
            err_path = Path(folder) / "stderr.bin"
            with out_path.open("wb") as out, err_path.open("wb") as err:
                completed = subprocess.run(
                    [sys.executable, "-B", "-X", "utf8",
                     str(ROOT / "kernel" / "autoresearch" / "progress_cli.py"), *args],
                    cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    timeout=timeout)
            return (completed.returncode,
                    out_path.read_text(encoding="utf-8", errors="replace"),
                    err_path.read_text(encoding="utf-8", errors="replace"))

    def test_preflight_cli_uses_trusted_overdue_and_ignores_static_snapshot(self):
        built = self.ready("P1")
        fake = self.ws / "fake-overdue.json"
        fake.write_text(json.dumps({"max_age_sec": 304, "items": []}), encoding="utf-8")
        before = self.snapshot_tree()
        code, stdout, stderr = self.run_cli(
            ["--root", str(self.ws), "--spec", str(self.spec_path),
             "--overdue-json", str(fake),
             "preflight", "--next-plan", built["plan"], "--topic", TOPIC,
             "--owner-session", SESSION, "--intent-none",
             "--workspace", str(self.ws), "--"] + self.argv_for())
        self.assertEqual(code, 9, stderr + stdout)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        overdue = payload["checks"]["overdue"]
        self.assertIn(overdue["state"], ("pass", "unknown"))
        self.assertEqual(overdue["evidence"]["trust"], "trusted",
                         "D2 必须走 workspace 库的可信只读源，⛔ 不用 --overdue-json")
        self.assertEqual(overdue["evidence"]["source"], "provider")
        self.assertIsNone(payload["next_command"])
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["writes"], [])
        self.assertEqual(self.snapshot_tree(), before, "CLI preflight 不许新增 / 改文件")

        # 缺失 workspace 库 ⇒ 可信源不可判 ⇒ unknown，而不是用静态快照绿
        empty = Path(self._tmp.name) / "empty-ws"
        empty.mkdir()
        code2, stdout2, _ = self.run_cli(
            ["--root", str(self.ws), "--spec", str(self.spec_path),
             "--overdue-json", str(fake),
             "preflight", "--next-plan", built["plan"], "--topic", TOPIC,
             "--owner-session", SESSION, "--intent-none",
             "--workspace", str(empty), "--"] + self.argv_for())
        self.assertEqual(code2, 9, stdout2)
        payload2 = json.loads(stdout2)
        self.assertEqual(payload2["checks"]["overdue"]["state"], "unknown", payload2)
        self.assertIsNone(payload2["next_command"])

        # 计划不存在 ⇒ 逐项 missing/blocked、没有命令（退出码 9，不是「通过」）
        code3, stdout3, _ = self.run_cli(
            ["--root", str(self.ws), "--spec", str(self.spec_path),
             "preflight", "--next-plan", "plans/nope.json", "--topic", TOPIC,
             "--owner-session", SESSION, "--intent-none",
             "--workspace", str(self.ws), "--"] + self.argv_for())
        self.assertEqual(code3, 9, stdout3)
        payload3 = json.loads(stdout3)
        self.assertIn(payload3["checks"]["plan_frozen"]["state"], ("missing", "blocked"))
        self.assertIsNone(payload3["next_command"])


if __name__ == "__main__":
    unittest.main()
