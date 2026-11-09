# GENERATED-BY: workspace layout resolution tests, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：标记 → 中文本地目录；无标记严格旧布局；非法标记与外部 symlink fail-closed
# PURPOSE: 证明三件事 —— ① 写了 `.autoresearch/workspace-layout.json` 的工作区里，
#          provision_topic / snapshot / execution_target / workflow / lifecycle / reporting
#          读写的都是同一份中文本地目录下的同一专题；② 没有标记的工作区（即使中文目录存在）
#          仍然严格走 topics/worktrees/environments 旧落点；③ 非法标记、越界目录与指向工作区外的
#          symlink 一律拒绝，⛔ 不静默回退旧布局。
# ORIGIN: 用户要求（公开项目模板保持本地中文目录；旧工作区兼容；状态库仍 .autoresearch 不迁移）
# CUMULATIVE-DELTA: 首版。全部是临时目录与合成数据：真实 git worktree / venv / sqlite，
#          不联网、不碰真实工作区、不读凭据。

"""工作区布局解析的集成测试。

判据（不是「目录存在就算过」）：

1. 中文布局初始化（写标记）后建专题：三份研究文档在 ``AutoDL实验/实验分析/topics/<id>``，
   代码工作树在 ``AutoDL实验/实验分析/worktrees/<id>``，环境在
   ``AutoDL实验/实验记录/environments/<id>``，快照在专题目录的 ``snapshots/`` 下。
2. 现有 `Workflow.check` 读到的专题主文档与 `reporting.page_path` / `lifecycle.topic_dir`
   指向**同一个** TOPIC.md（同一路径、同一字节哈希）。
3. 没有标记的工作区**不会**因为中文目录存在而改落点。
4. 标记里的绝对路径 / ``..`` / 未知键 / 别的 schema、以及经 symlink 逃出工作区的目录，
   一律 `LayoutError`（fail-closed）。
5. 专题内的 `plans/` 投影前缀保持相对专题目录（与标记里的框架级 `directories.plans` 不同）、
   显式 `topic_doc_pattern` 仍然优先。

跑法：

    python -X utf8 -B -m unittest discover -s tests -p 'test_workspace_layout.py' -v
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import lifecycle, reporting, workspace_layout  # noqa: E402
from kernel.autoresearch.execution_target import ExecutionTarget  # noqa: E402
from kernel.autoresearch.workspace import provision_topic, snapshot_topic  # noqa: E402
from kernel.autoresearch.workflow import Workflow  # noqa: E402

TOPIC = "T1"
SESSION = "session-owner"

CHINESE_TOPICS = "AutoDL实验/实验分析/topics"
CHINESE_WORKTREES = "AutoDL实验/实验分析/worktrees"
CHINESE_ENVIRONMENTS = "AutoDL实验/实验记录/environments"
CHINESE_PLANS = "AutoDL实验/框架/state/plans"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LayoutCase(unittest.TestCase):
    """每个用例一个临时工作区；是否写标记由用例决定。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "ws"
        self.root.mkdir()
        (self.root / ".autoresearch").mkdir()

    # -- 夹具 ---------------------------------------------------------------

    def mark(self, **kwargs):
        return workspace_layout.write_layout_marker(self.root, **kwargs)

    def assert_layout_error(self, callable_, *args, **kwargs):
        with self.assertRaises(workspace_layout.LayoutError):
            callable_(*args, **kwargs)

    def write_marker_document(self, document):
        path = self.root / workspace_layout.LAYOUT_MARKER_REL
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def plan(self, *, argv=None):
        entry = self.root / "submission" / "entry.py"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("def parse_args(argv):\n    return argv\n", encoding="utf-8")
        payload = {
            "id": "P1",
            "goal": "验证收紧入口参数是否提高通过题数",
            "hypothesis": {"id": "H1", "text": "收紧入口参数会让通过题数至少多 1 题"},
            "criteria": {"mode": "exploratory", "metric": "机制是否生效",
                         "observation": "看到调用记录",
                         "interpretation_boundary": "只判机制存在，不外推幅度",
                         "conditions": "同装置小样本",
                         "stop_condition": "取到调用记录或预算耗尽"},
            "budget": {"max_cost_units": 1, "max_seconds": 60},
            "input_files": [],
            "implementation": {
                "expected_behavior": "入口只接受整数时非法参数被拒绝",
                "load_path": "submission/entry.py 的 parse_args",
                "trigger": "评测器传入字符串参数时",
                "observation": "非法参数返回非零退出码并打印原因",
                "non_activation": "非法参数仍被接受并继续运行",
                "files": [{"path": "submission/entry.py", "locator": "L1-L2"}],
            },
            "execution_argv": list(argv if argv is not None else ["python", "-c", "pass"]),
            "authorization": {"scope": "small", "series_id": "S-1", "reservation_id": "R-1"},
        }
        return payload


# ---------------------------------------------------------------------------
# 标记本身：默认文档、写入、拒绝路径
# ---------------------------------------------------------------------------


class MarkerDocumentTests(LayoutCase):
    def test_default_document_is_chinese_layout_schema_1(self):
        document = workspace_layout.default_layout_document()
        self.assertEqual(document["schema"], 1)
        self.assertEqual(document["directories"], {
            "topics": CHINESE_TOPICS, "worktrees": CHINESE_WORKTREES,
            "environments": CHINESE_ENVIRONMENTS, "plans": CHINESE_PLANS})
        self.assertEqual(document["topic_doc_pattern"], "{topics}/{topic}/TOPIC.md")
        self.assertEqual(workspace_layout.LEGACY_LAYOUT_DIRECTORIES,
                         {"topics": "topics", "worktrees": "worktrees",
                          "environments": "environments", "plans": "plans"})

    def test_write_is_idempotent_and_never_overwrites(self):
        first = self.mark()
        self.assertFalse(first["idempotent"])
        self.assertTrue((self.root / workspace_layout.LAYOUT_MARKER_REL).is_file())
        second = self.mark()
        self.assertTrue(second["idempotent"])
        # 换成另一套目录 ⇒ 拒绝覆盖（不静默改已有工作区的落点）。
        self.assert_layout_error(self.mark, directories={"topics": "other/topics"})
        self.assertIn(CHINESE_TOPICS,
                      (self.root / workspace_layout.LAYOUT_MARKER_REL).read_text(encoding="utf-8"))

    def test_write_requires_state_directory(self):
        bare = Path(self._tmp.name) / "bare"
        bare.mkdir()
        self.assert_layout_error(workspace_layout.write_layout_marker, bare)

    def test_invalid_markers_fail_closed(self):
        cases = {
            "绝对路径": {"schema": 1, "directories": {"topics": "/abs/topics"}},
            "盘符绝对路径": {"schema": 1, "directories": {"topics": "C:/abs/topics"}},
            "含上跳": {"schema": 1, "directories": {"topics": "../outside/topics"}},
            "状态目录": {"schema": 1, "directories": {"topics": ".autoresearch/topics"}},
            "缺 schema": {"directories": {"topics": "x/topics"}},
            "别的 schema": {"schema": 2, "directories": {"topics": "x/topics"}},
            "未知键": {"schema": 1, "directories": {"topics": "x/topics"}, "extra": True},
            "未知目录键": {"schema": 1, "directories": {"topicz": "x/topics"}},
            "目录不是对象": {"schema": 1, "directories": []},
            "模式缺占位": {"schema": 1, "directories": {}, "topic_doc_pattern": "x/DOC.md"},
            "模式绝对": {"schema": 1, "directories": {},
                     "topic_doc_pattern": "/abs/{topic}/TOPIC.md"},
        }
        for label, document in cases.items():
            with self.subTest(case=label):
                path = self.write_marker_document(document)
                with self.assertRaises(workspace_layout.LayoutError):
                    workspace_layout.resolve_layout(self.root)
                path.unlink()
        # 坏 JSON 也拒，不静默回退旧布局。
        path = self.root / workspace_layout.LAYOUT_MARKER_REL
        path.write_text("{not json", encoding="utf-8")
        self.assert_layout_error(workspace_layout.resolve_layout, self.root)
        path.unlink()
        # 空文件同样拒（不是「没有标记」）。
        path.write_text("  \n", encoding="utf-8")
        self.assert_layout_error(workspace_layout.resolve_layout, self.root)

    def test_marker_symlink_is_refused(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        try:
            (self.root / workspace_layout.LAYOUT_MARKER_REL).symlink_to(
                outside / "marker.json")
        except OSError:
            self.skipTest("宿主未授予创建符号链接的权限")
        self.assert_layout_error(workspace_layout.resolve_layout, self.root)

    def test_layout_directory_symlink_escape_is_refused(self):
        outside = Path(self._tmp.name) / "outside"
        (outside / "topics").mkdir(parents=True)
        try:
            (self.root / "AutoDL实验").mkdir()
            (self.root / "AutoDL实验" / "实验分析").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("宿主未授予创建符号链接的权限")
        self.mark()
        self.assert_layout_error(workspace_layout.resolve_layout, self.root)
        # 连落点解析也要拒：topic_dir 不能跟着 symlink 出工作区。
        self.assert_layout_error(lambda: workspace_layout.read_layout(self.root).topic_dir(TOPIC))


# ---------------------------------------------------------------------------
# 旧工作区：无标记 ⇒ 严格旧落点（不看中文目录在不在）
# ---------------------------------------------------------------------------


class LegacyLayoutTests(LayoutCase):
    def test_no_marker_keeps_legacy_directories_even_if_chinese_ones_exist(self):
        # 故意先建中文目录：没有标记时它们**不能**改变落点。
        (self.root / CHINESE_TOPICS).mkdir(parents=True)
        (self.root / CHINESE_WORKTREES).mkdir(parents=True)
        (self.root / CHINESE_ENVIRONMENTS).mkdir(parents=True)
        layout = workspace_layout.resolve_layout(self.root)
        self.assertFalse(layout.marked)
        self.assertEqual(layout.topics, "topics")
        self.assertEqual(layout.topic_dir(TOPIC), (self.root / "topics" / TOPIC).resolve())
        self.assertEqual(layout.topic_doc_rel(TOPIC), f"topics/{TOPIC}/TOPIC.md")
        self.assertFalse((self.root / "topics").exists())

    def test_legacy_provision_uses_root_directories(self):
        if shutil.which("git") is None:
            self.skipTest("git 不可用，无法建真实工作树")
        record = provision_topic(self.root, topic=TOPIC, objective="目标", requirements="要求",
                                 owner_session=SESSION, create_environment=False)
        self.assertEqual(record["code_path"], f"worktrees/{TOPIC}")
        self.assertTrue((self.root / "topics" / TOPIC / "TOPIC.md").is_file())
        self.assertTrue((self.root / "worktrees" / TOPIC / ".git").exists())
        self.assertFalse((self.root / CHINESE_TOPICS / TOPIC).exists())
        self.assertEqual(reporting.page_rel(self.root, TOPIC), f"topics/{TOPIC}/STATUS.md")
        self.assertEqual(reporting.page_rel(TOPIC), f"topics/{TOPIC}/STATUS.md")  # 旧单参写法
        self.assertEqual(lifecycle.Lifecycle(self.root).topic_dir(TOPIC),
                         (self.root / "topics" / TOPIC).resolve())


# ---------------------------------------------------------------------------
# 中文布局：建专题 → 三份文档 → 代码/环境/快照 → 工作流与交付读同一个 TOPIC
# ---------------------------------------------------------------------------


class ChineseLayoutTests(LayoutCase):
    def setUp(self):
        super().setUp()
        if shutil.which("git") is None:
            self.skipTest("git 不可用，无法建真实工作树")
        self.mark()
        self.record = provision_topic(self.root, topic=TOPIC, objective="把通过题数从 3 提到 4",
                                      requirements="保留原始要求全文", owner_session=SESSION,
                                      create_environment=True)
        self.topic_dir = self.root / CHINESE_TOPICS / TOPIC
        self.doc = self.topic_dir / "TOPIC.md"

    def test_topic_three_documents_code_environment_and_snapshot_paths(self):
        # 三份研究文档都在中文本地目录的专题里。
        for name in ("TOPIC.md", "OBJECTIVE.md", "REQUIREMENTS.md"):
            self.assertTrue((self.topic_dir / name).is_file(), name)
        self.assertTrue(any(self.topic_dir.glob("EVIDENCE_*.md")))
        self.assertTrue((self.topic_dir / "STATUS.md").is_file())
        self.assertIn("保留原始要求全文",
                      (self.topic_dir / "REQUIREMENTS.md").read_text(encoding="utf-8"))
        # 代码工作树 / 独立环境在布局指定目录。
        self.assertEqual(self.record["code_path"], f"{CHINESE_WORKTREES}/{TOPIC}")
        self.assertTrue((self.root / CHINESE_WORKTREES / TOPIC / ".git").exists())
        self.assertEqual(self.record["python"], f"{CHINESE_ENVIRONMENTS}/{TOPIC}/"
                                               + ("Scripts/python.exe" if sys.platform == "win32"
                                                  else "bin/python"))
        self.assertTrue((self.root / CHINESE_ENVIRONMENTS / TOPIC / "pyvenv.cfg").is_file())
        self.assertTrue((self.topic_dir / "provision.json").is_file())
        # sources.json 里的路径也是布局相对。
        sources = json.loads((self.topic_dir / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(sources, [f"{CHINESE_TOPICS}/{TOPIC}/OBJECTIVE.md",
                                   f"{CHINESE_TOPICS}/{TOPIC}/TOPIC.md",
                                   f"{CHINESE_TOPICS}/{TOPIC}/REQUIREMENTS.md"])
        # 快照落在专题目录下，source/path 都是布局相对。
        before = snapshot_topic(self.root, topic=TOPIC, exp_id="E1", stage="before")
        self.assertEqual(before["path"], f"{CHINESE_TOPICS}/{TOPIC}/snapshots/E1/before.md")
        self.assertEqual(before["source"], f"{CHINESE_TOPICS}/{TOPIC}/TOPIC.md")
        self.assertEqual((self.root / before["path"]).read_bytes(), self.doc.read_bytes())

    def test_execution_target_binds_layout_worktree_and_environment(self):
        python = self.root / self.record["python"]
        target = ExecutionTarget(self.root, require_provision=True)
        resolved = target.resolve(TOPIC, {"execution": {"scope": "local", "resource_kind": "cpu"}},
                                  [str(python), "-c", "pass"])
        self.assertEqual(Path(resolved["cwd"]), (self.root / CHINESE_WORKTREES / TOPIC).resolve())
        self.assertEqual(resolved["provision"], f"{CHINESE_TOPICS}/{TOPIC}/provision.json")
        self.assertTrue(resolved["isolation_verified"])

    def test_workflow_check_reads_the_same_layout_topic_document(self):
        flow = Workflow(self.root, require_monitor_check=False,
                        require_authorization_check=False)
        self.assertEqual(flow.layout.topics, CHINESE_TOPICS)
        doc = flow._topic_doc(TOPIC)
        self.assertEqual(doc["path"], f"{CHINESE_TOPICS}/{TOPIC}/TOPIC.md")
        self.assertEqual(doc["sha256"], _sha(self.doc))
        # 现有工作流检查：读到的是同一份 TOPIC（路径 + 哈希），且不报「缺专题主文档」。
        plan = self.plan(argv=[str(self.root / self.record["python"]), "-c", "pass"])
        result = flow.check(topic=TOPIC, plan=plan, owner_session=SESSION, argv=plan["execution_argv"])
        self.assertIn("topic_doc", result["checks"])
        self.assertEqual(result["checks"]["topic_doc"]["path"], doc["path"])
        self.assertEqual(result["checks"]["topic_doc"]["sha256"], doc["sha256"])
        self.assertFalse(any("缺专题主文档" in item for item in result["problems"]),
                         result["problems"])
        # 交付侧（reporting）与接线侧（lifecycle）指向同一专题目录。
        self.assertEqual(reporting.topic_root(self.root, TOPIC), self.topic_dir.resolve())
        self.assertEqual(reporting.page_path(self.root, TOPIC),
                         self.topic_dir.resolve() / "STATUS.md")
        self.assertEqual(reporting.page_rel(self.root, TOPIC),
                         f"{CHINESE_TOPICS}/{TOPIC}/STATUS.md")
        self.assertEqual(lifecycle.Lifecycle(self.root).topic_dir(TOPIC), self.topic_dir.resolve())

    def test_reporting_discovers_layout_sources_and_keeps_machine_state(self):
        reporting.prepare(self.root, TOPIC)
        snapshot = reporting.source_snapshot(self.root, TOPIC)
        self.assertIn(f"{CHINESE_TOPICS}/{TOPIC}/TOPIC.md", snapshot["files"])
        self.assertIn(f"{CHINESE_TOPICS}/{TOPIC}/sources.json", snapshot["files"])
        feed = reporting.feed_path(self.root, TOPIC).read_text(encoding="utf-8")
        self.assertIn(f"{CHINESE_TOPICS}/{TOPIC}/TOPIC.md", feed)
        # 机器侧状态目录不跟着布局搬：仍是 .autoresearch/state（状态库也不迁移）。
        self.assertTrue(reporting.machine_dir(self.root, TOPIC).is_dir())
        self.assertTrue(str(reporting.machine_dir(self.root, TOPIC)).startswith(
            str(self.root / ".autoresearch")))
        self.assertEqual(reporting.feed_rel(TOPIC), f".autoresearch/state/reporting/{TOPIC}/FEED.md")

    def test_topic_plans_projection_prefix_stays_topic_relative(self):
        client = lifecycle.Lifecycle(self.root)
        self.assertEqual(lifecycle.PROJECTION_PREFIX, "plans/")
        self.assertEqual(client.plan_dir(TOPIC, "P1"),
                         self.topic_dir.resolve() / "plans" / "P1")
        self.assertEqual(client.ai_work(TOPIC, "P1"),
                         self.topic_dir.resolve() / "plans" / "P1" / "AI_WORK.md")
        self.assertEqual(client.receipt(TOPIC, "P1", "run", "R1"),
                         self.topic_dir.resolve() / "plans" / "P1" / "receipts" / "run-R1.json")
        # 布局里的 plans 是框架级目录，与专题内投影不同（也不是同一个绝对路径）。
        layout = workspace_layout.resolve_layout(self.root)
        self.assertEqual(layout.plans, CHINESE_PLANS)
        self.assertNotEqual(layout.plans_dir, client.plan_dir(TOPIC, "P1").parent)

    def test_cli_documents_uses_layout_topic_dir(self):
        # `workspace documents` 也必须按布局解析（无标记工作区仍是 topics/<id>）。
        from kernel.autoresearch.workspace_cli import main as workspace_main
        code = workspace_main(['documents', '--workspace', str(self.root), '--topic', TOPIC])
        self.assertEqual(code, 0)
        self.assertTrue(self.doc.is_file())

    def test_explicit_topic_doc_pattern_still_wins_over_layout(self):
        custom = self.root / "custom" / TOPIC
        custom.mkdir(parents=True)
        (custom / "DOC.md").write_text("# 自定义主文档\n", encoding="utf-8")
        flow = Workflow(self.root, require_monitor_check=False,
                        require_authorization_check=False,
                        topic_doc_pattern="custom/{topic}/DOC.md")
        self.assertEqual(flow._topic_doc(TOPIC)["path"], f"custom/{TOPIC}/DOC.md")
        # `{topics}` 占位由布局代入；显式自定义照样生效。
        flow2 = Workflow(self.root, require_monitor_check=False,
                         require_authorization_check=False,
                         topic_doc_pattern="{topics}/{topic}/TOPIC.md")
        self.assertEqual(flow2._topic_doc(TOPIC)["path"], f"{CHINESE_TOPICS}/{TOPIC}/TOPIC.md")


if __name__ == "__main__":
    unittest.main()
