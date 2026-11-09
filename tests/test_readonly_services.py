# GENERATED-BY: readonly entry hardening, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：ReadonlyStore / Delivery / Inbox / DecisionService.readonly / workflow_cli check
# PURPOSE: 证明「明确只读入口」的**构造**没有副作用：不建库、不迁移 schema、写入口全拒；
#          同时证明真实的 small / large 批准在只读绑定下仍然可用（只读不等于放宽）。
# ORIGIN: tests/test_decisions.py 的假通道与方案夹具 + tests/test_workflow.py 的临时工作区夹具
#         （⛔ 不连真实平台、不发消息、不起云机、不读真实宿主日志）。
# CUMULATIVE-DELTA: 首版。覆盖 ① ReadonlyStore 的 mode=ro/query_only、缺库不建、写事务与 emit 拒绝
#   ② Delivery / Inbox 的 initialize=False 与 readonly（跳过 DDL；只读 Inbox 可省 Accounts）
#   ③ DecisionService.readonly(db) / readonly=True（跳过建表与 Accounts、写入口全拒、缺库缺表旧 schema 判不可判）
#   ④ 实际 `workflow_cli check` 前后工作区树与 DB 字节不变、缺库不创建、旧 schema 不迁移
#   ⑤ Monitoring 构造与 check_start 零写（核实「本来不 DDL」）
#   ⑥ 真实批准（页→卡→点击）在只读服务下的 small / large 判词与可写构造逐项一致。
#   ⑦ 真实 SQLite WAL 边界（SQLite 3.49.1 实测）：干净 WAL 库 mode=ro 会创建 -shm/-wal；
#      只读改走 immutable=1 且前后核对主库 identity；有 -wal 无 -shm 判不可判；活 WAL 只用已有 -shm。
#   ⑧ `workflow._ReadOnlyStore` / `_read_conn` 委托正规只读（干净 WAL 读不建 sidecar），
#      以及 `workflow_cli check` 在真实 WAL 工作区库上不新建任何文件、不改主库与 -wal。

"""只读入口的构造副作用验收。

这里的判据不是「函数能跑通」，而是四条可核的边界：

1. **构造零副作用**：`ReadonlyStore` / `Delivery(readonly=True)` / `Inbox(readonly=True)` /
   `DecisionService.readonly(db)` / `workflow_cli check` 都不建库、不建目录、不建表、不迁移 schema。
2. **读不到就说读不到**：缺库 / 缺表 / 旧 schema 返回「不可判」（`ok=False` + `undecidable=True`
   + 原因），⛔ 不把未知表读成空数据，⛔ 不为了跑通去建库补表。
3. **写入口全拒**：只读服务上的 prepare / send / tick / ingest / reply / record 全部抛
   `ReadonlyServiceError`；只读 `Delivery` / `Inbox` 抛各自的错误；`ReadonlyStore.transaction()`
   与 `emit()` 抛 `ReadonlyViolation`。
4. **只读不放宽**：真实的 small / large 批准在只读服务下得到与可写服务相同的判词；
   改 argv / 换会话 / 换范围照旧失效。

⛔ 本文件不制造「只读绿」：所有「不可判」的断言都要求原因文本，所有「不变」的断言都对比
**整个工作区树（含 DB 字节与审批页）**。

WAL 边界（实测 SQLite 3.49.1，记录在 `WalSidecarTests`）：

| 库状态 | `mode=ro` | `immutable=1` | 本类选择 |
|---|---|---|---|
| WAL 头 + 无 sidecar | **创建 `-shm` + `-wal`** | 不创建，读得对 | immutable + 前后核对 identity |
| WAL 头 + 有 `-wal`、无 `-shm` | **创建 `-shm`** | 不创建，但**读旧主库**（丢 WAL 数据） | 拒绝 ⇒ 不可判 |
| WAL 头 + `-wal` 与 `-shm` 都在 | 不创建（用已有共享内存） | 不创建但读旧主库 | `mode=ro` |
| WAL 头 + 只剩陈旧 `-shm` | **创建 `-wal`** | 不创建，读得对 | immutable + 前后核对 identity |
| rollback（DELETE）库 | 不创建 | —— | `mode=ro` |
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _candidate in (ROOT, ROOT / "tests"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from kernel.autoresearch.decisions import (  # noqa: E402
    DecisionError,
    DecisionService,
    ReadonlyServiceError,
    execution_fingerprint,
)
from kernel.autoresearch.monitoring import Monitoring  # noqa: E402
from kernel.autoresearch.runtime.delivery import Delivery, DeliveryError  # noqa: E402
from kernel.autoresearch.runtime.inbox import Inbox, InboxError  # noqa: E402
from kernel.autoresearch.runtime.store import (  # noqa: E402
    ReadonlyStore,
    ReadonlyViolation,
    Store,
    StoreError,
    is_readonly_store,
)
from kernel.autoresearch.workflow_cli import main as workflow_cli_main  # noqa: E402
from test_decisions import (  # noqa: E402
    Clock,
    FakeChannel,
    card_event,
    decision,
    plan_set,
    workflow_plan,
)
import test_workflow as flow_fixtures  # noqa: E402

EXP = "exp-01"
OWNER = "sess-01"
RECIPIENT = "chat-01"


# ---------------------------------------------------------------------------
# 纯工具：树快照 / 只读 schema 快照
# ---------------------------------------------------------------------------


def tree_snapshot(root):
    """工作区树快照：`{相对路径: '<dir>' | sha256}`。

    目录也记一条（`dir/`），这样「新建一个空目录」也算变化 —— 构造副作用常常就是
    `mkdir(parents=True)` 这类空目录。
    """
    root = Path(root)
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            out[rel + "/"] = "<dir>"
        else:
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def readonly_schema(db_path):
    """`sqlite_master` 快照（类型 / 名字 / 建表 SQL，按名字排序）。

    用 `mode=ro` 打开：⛔ 快照动作本身不能建库或写库。
    """
    path = Path(db_path)
    if not path.is_file():
        return []
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return [tuple(row) for row in conn.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name")]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# WAL 夹具：只在测试自己的临时目录里造，⛔ 不碰任何生产库、不删生产库的 sidecar
# ---------------------------------------------------------------------------


def wal_database(directory, name="state.sqlite3", rows=3):
    """测试夹具：WAL 模式库（`wal_autocheckpoint=0`）。返回 `(路径, 写连接)`。"""
    path = Path(directory) / name
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(index,) for index in range(rows)])
    conn.commit()
    return path, conn


def clean_wal_database(directory, name="state.sqlite3"):
    """夹具：`wal_checkpoint(TRUNCATE)` 后正常关闭，再删掉 sidecar。

    ⚠️ 删除只发生在测试的临时目录（`directory`）里，⛔ 不对任何真实库做这件事。
    """
    path, conn = wal_database(directory, name)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    return path


def wal_without_shared_memory(directory):
    """夹具：主库 + `-wal`，没有 `-shm`（写者还开着时复制主库与 wal，随后关闭写者）。"""
    source_dir = Path(directory) / "source"
    source_dir.mkdir()
    source, writer = wal_database(source_dir)
    fixture = Path(directory) / "fixture"
    fixture.mkdir()
    target = fixture / "state.sqlite3"
    shutil.copyfile(source, target)
    shutil.copyfile(str(source) + "-wal", str(target) + "-wal")
    writer.close()
    return target


class ReadonlyCase(unittest.TestCase):
    """一个临时目录 + 一个可写库 + 假通道（与 test_decisions 的夹具同形）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hil_readonly_")
        self.root = Path(self._tmp.name)
        self.case = self.root / "case"
        self.case.mkdir()
        self.db = self.case / "runtime.sqlite3"
        self.clock = Clock()
        self.store = Store(self.db)
        self.channel = FakeChannel(self.clock)
        self.service = DecisionService(self.store, channel=self.channel, clock=self.clock)

    def tearDown(self):
        self._tmp.cleanup()

    def prepare(self, plan=None, dec=None, execution=None):
        return self.service.prepare(exp_id=EXP, decision=dec or decision(),
                                    plan_set=plan if plan is not None else plan_set(),
                                    page_dir=self.case / "web",
                                    execution_plan=execution)

    def fresh_store(self, name="raw"):
        """一个**只**建了 `Store` 基础表的库：用来证明只读构造不补决策 / 交付 / 入向表。"""
        directory = self.case / name
        directory.mkdir()
        path = directory / "runtime.sqlite3"
        return Store(path), path

    def send_and_approve(self):
        """走完 页 → 卡 → 点击，留下一条**真实**批准（不是手写状态）。"""
        self.prepare(plan=workflow_plan())
        self.service.send("E1.1")
        return self.service.ingest("fake", card_event(self.service))


# ---------------------------------------------------------------------------
# 1. ReadonlyStore：不建库、不迁移、写事务与 emit 一律拒绝
# ---------------------------------------------------------------------------


class ReadonlyStoreTests(ReadonlyCase):

    def test_missing_database_is_never_created(self):
        missing = self.root / "nowhere" / "nested" / "state.sqlite3"
        store = ReadonlyStore(missing)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())
        self.assertTrue(store.readonly)
        self.assertFalse(store.is_file())
        self.assertEqual(store.tables(), frozenset())
        self.assertEqual(store.missing_tables(("experiments", "events")),
                         ["experiments", "events"])
        with self.assertRaises(StoreError) as caught:
            store.connect()
        self.assertIn("不建库", str(caught.exception))
        # 连异常路径也不许顺手建库。
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_write_transactions_and_emit_are_refused(self):
        missing = self.root / "absent.sqlite3"
        store = ReadonlyStore(missing)
        with self.assertRaises(ReadonlyViolation):
            with store.transaction():
                self.fail("只读 Store 不应提供写事务")
        with self.assertRaises(ReadonlyViolation):
            store.emit(None, EXP, "kind", "actor", {})
        self.assertFalse(missing.exists())

    def test_connection_is_query_only_on_an_existing_database(self):
        before = self.db.read_bytes()
        store = ReadonlyStore(self.db)
        conn = store.connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO experiments(id,goal,state,version,created_at,"
                             "updated_at) VALUES('exp-forged','','planned',0,0,0)")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forged(x)")
        finally:
            conn.close()
        self.assertEqual(self.db.read_bytes(), before)

    def test_store_and_readonly_store_are_distinguishable(self):
        self.assertFalse(is_readonly_store(self.store))
        self.assertTrue(is_readonly_store(ReadonlyStore(self.db)))


# ---------------------------------------------------------------------------
# 1b. 真实 SQLite WAL 边界：只读**不许创建** -shm / -wal（mode=ro 会）
# ---------------------------------------------------------------------------


class WalSidecarTests(unittest.TestCase):
    """实测边界（SQLite 3.49.1）：`mode=ro` 在缺 sidecar 的 WAL 库上会**创建文件**。

    | 库状态 | `mode=ro` | `immutable=1` |
    |---|---|---|
    | WAL 头 + 无 sidecar | 创建 `-shm` + `-wal` | 不创建，读得对 |
    | WAL 头 + `-wal` 无 `-shm` | 创建 `-shm` | 不创建，但读旧主库（丢 WAL 数据） |
    | WAL 头 + `-wal` 与 `-shm` | 不创建（用已有共享内存） | 不创建但读旧主库 |
    | WAL 头 + 只剩陈旧 `-shm` | 创建 `-wal` | 不创建，读得对 |
    | rollback（DELETE）库 | 不创建 | —— |

    ⇒ `ReadonlyStore.connect()` 按头里的日志模式与 sidecar 实况选路：
    缺 `-wal` 时用 `immutable=1` 并前后核对主库 identity；有 `-wal` 无 `-shm` 直接判不可判；
    有 `-wal` 与 `-shm` 时走已有共享内存。⛔ 不复制数据库，⛔ 不为了读下去创建文件。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hil_wal_")
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_wal_database_is_read_without_creating_sidecars(self):
        db = clean_wal_database(self.dir)
        self.assertEqual(sorted(path.name for path in self.dir.iterdir()), ["state.sqlite3"])
        before = tree_snapshot(self.dir)
        store = ReadonlyStore(db)
        self.assertEqual(store.tables(), frozenset({"t"}))
        self.assertEqual(store.schema()["t"], frozenset({"x"}))
        conn = store.connect()
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM t").fetchone()[0], 3)
        finally:
            conn.close()
        # ⛔ 没有新建 -shm / -wal；主库字节也没变（identity 核对在 connect 里）。
        self.assertEqual(tree_snapshot(self.dir), before)
        self.assertFalse(Path(str(db) + "-wal").exists())
        self.assertFalse(Path(str(db) + "-shm").exists())

    def test_wal_without_shared_memory_is_undecidable_and_creates_nothing(self):
        db = wal_without_shared_memory(self.dir)
        self.assertTrue(Path(str(db) + "-wal").is_file())
        self.assertFalse(Path(str(db) + "-shm").exists())
        before = tree_snapshot(self.dir)
        with self.assertRaises(StoreError) as caught:
            ReadonlyStore(db).connect()
        message = str(caught.exception)
        self.assertIn("-shm", message)
        self.assertIn("不可判", message)
        # 关键：没有为了读下去创建 -shm（那正是 mode=ro 的副作用）。
        self.assertEqual(tree_snapshot(self.dir), before)
        # 只读读路径给同一个判词：不可判，⛔ 不是「没有决策」。
        status = DecisionService.readonly(db).status("E1.1")
        self.assertTrue(status["undecidable"])
        self.assertIn("不可判", "；".join(status["problems"]))
        self.assertEqual(tree_snapshot(self.dir), before)

    def test_live_wal_uses_existing_shared_memory_without_creating_files(self):
        db, writer = wal_database(self.dir)
        try:
            self.assertTrue(Path(str(db) + "-wal").is_file())
            self.assertTrue(Path(str(db) + "-shm").is_file())
            before = tree_snapshot(self.dir)
            store = ReadonlyStore(db)
            self.assertEqual(store.tables(), frozenset({"t"}))
            conn = store.connect()
            try:
                self.assertEqual(conn.execute("SELECT count(*) FROM t").fetchone()[0], 3)
            finally:
                conn.close()
            after = tree_snapshot(self.dir)
            # ⛔ 没有新文件；主库与 -wal 字节不变。
            self.assertEqual(sorted(after), sorted(before))
            self.assertEqual(after["state.sqlite3"], before["state.sqlite3"])
            self.assertEqual(after["state.sqlite3-wal"], before["state.sqlite3-wal"])
            # `-shm` 是 SQLite 的锁文件：只读连接会用**已有**共享内存写锁定字节 ⇒ 允许它变，
            # 但它必须原本就存在（不是这次读创建的）。判据是「不创建文件」，不是「不碰 sidecar」。
            self.assertIn("state.sqlite3-shm", before)
            self.assertIn("state.sqlite3-shm", after)
        finally:
            writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            writer.close()

    def test_stale_shared_memory_without_wal_does_not_create_wal(self):
        db = clean_wal_database(self.dir)
        live_dir = self.dir / "live"
        live_dir.mkdir()
        live, writer = wal_database(live_dir)
        shutil.copyfile(str(live) + "-shm", str(db) + "-shm")
        writer.close()
        self.assertFalse(Path(str(db) + "-wal").exists())
        before = tree_snapshot(self.dir)
        self.assertEqual(ReadonlyStore(db).tables(), frozenset({"t"}))
        # ⛔ 没有为了读下去创建 -wal（mode=ro 会）。
        self.assertEqual(tree_snapshot(self.dir), before)
        self.assertFalse(Path(str(db) + "-wal").exists())

    def test_rollback_database_read_creates_no_sidecars(self):
        db = self.dir / "rollback.sqlite3"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()
        before = tree_snapshot(self.dir)
        self.assertEqual(ReadonlyStore(db).tables(), frozenset({"t"}))
        self.assertEqual(tree_snapshot(self.dir), before)

    def test_workflow_read_conn_on_clean_wal_creates_no_sidecars(self):
        """`Workflow._read_conn()` / `_ReadOnlyStore` 也走正规只读（不再自己拼 `mode=ro`）。"""
        from kernel.autoresearch.workflow import Workflow, _ReadOnlyStore

        workspace = self.dir / "ws"
        workspace.mkdir()
        db = workspace / ".autoresearch" / "runtime.sqlite3"
        db.parent.mkdir()
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE wf_runs(run_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO wf_runs VALUES ('RUN-1')")
        conn.commit()
        conn.close()
        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for sidecar in (wal, shm):
            if sidecar.exists():
                sidecar.unlink()
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())

        before = tree_snapshot(workspace)
        flow = Workflow(workspace, db=str(db), readonly=True)
        with flow._with_conn() as connection:
            self.assertEqual(connection.execute("SELECT run_id FROM wf_runs").fetchone()[0],
                             "RUN-1")
        with _ReadOnlyStore(db).transaction() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM wf_runs").fetchone()[0], 1)
        # ⛔ 没有创建 -shm / -wal，主库字节也没变。
        self.assertEqual(tree_snapshot(workspace), before)
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())

    def test_topics_readonly_on_clean_wal_creates_no_sidecars(self):
        """`Topics(readonly=True)` 的 `_ReadOnlyHandle` 也走正规只读（workflow 闸的第一步）。"""
        from kernel.autoresearch.runtime.topics import Topics

        workspace = self.dir / "topics-ws"
        workspace.mkdir()
        db = workspace / "state.sqlite3"
        store = Store(db)
        Topics(store).create_topic("T1", title="t", objective="o")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        for sidecar in (wal, shm):
            if sidecar.exists():
                sidecar.unlink()
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())

        before = tree_snapshot(workspace)
        row = Topics(db, readonly=True).get_topic("T1")
        self.assertEqual(row["id"], "T1")
        # ⛔ 没有创建 -shm / -wal（裸 mode=ro 会创建）。
        self.assertEqual(tree_snapshot(workspace), before)
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())

    def test_each_read_entry_creates_no_sidecars_on_clean_wal(self):
        """参数化：各只读入口在干净 WAL 库上都不创建 `-shm` / `-wal`。

        覆盖这次接线的主链只读连接：workflow `_read_conn` / `_ReadOnlyStore`、Topics、
        lifecycle `_read`、remote `_readonly_conn`、reporting `_lease_state`。
        每个入口单独一棵临时工作区，前后比整棵树（含主库字节）。
        """
        from kernel.autoresearch import reporting
        from kernel.autoresearch.lifecycle import Lifecycle
        from kernel.autoresearch.remote_workflow import _readonly_conn as remote_readonly
        from kernel.autoresearch.runtime.topics import Topics
        from kernel.autoresearch.workflow import Workflow, _ReadOnlyStore

        def workflow_read_conn(workspace, db):
            flow = Workflow(workspace, db=str(db), readonly=True)
            with flow._with_conn() as connection:
                return connection.execute("SELECT count(*) FROM topics").fetchone()[0]

        def workflow_store_transaction(workspace, db):
            with _ReadOnlyStore(db).transaction() as connection:
                return connection.execute("SELECT count(*) FROM topics").fetchone()[0]

        def topics_read(workspace, db):
            return Topics(db, readonly=True).get_topic("T1")["id"]

        def lifecycle_read(workspace, db):
            with Lifecycle(workspace, db=db)._read() as connection:
                return connection.execute("SELECT id FROM topics").fetchone()[0]

        def remote_read(workspace, db):
            with remote_readonly(db) as connection:
                return connection.execute("SELECT id FROM topics").fetchone()[0]

        def reporting_lease(workspace, db):
            return reporting._lease_state(db, "T1")["state"]

        entries = {
            "workflow._read_conn": workflow_read_conn,
            "workflow._ReadOnlyStore.transaction": workflow_store_transaction,
            "Topics(readonly=True).get_topic": topics_read,
            "lifecycle._read": lifecycle_read,
            "remote_workflow._readonly_conn": remote_read,
            "reporting._lease_state": reporting_lease,
        }
        for label, entry in entries.items():
            with self.subTest(entry=label):
                workspace = self.dir / ("entry-" + label.replace(".", "_").replace("(", "_")
                                        .replace(")", "_").replace("=", "_"))
                (workspace / ".autoresearch").mkdir(parents=True)
                db = workspace / ".autoresearch" / "runtime.sqlite3"
                store = Store(db)
                Topics(store).create_topic("T1", title="t", objective="o")
                conn = sqlite3.connect(str(db))
                conn.execute("PRAGMA journal_mode=WAL")
                conn.close()
                wal = Path(str(db) + "-wal")
                shm = Path(str(db) + "-shm")
                for sidecar in (wal, shm):
                    if sidecar.exists():
                        sidecar.unlink()
                self.assertFalse(wal.exists())
                self.assertFalse(shm.exists())
                before = tree_snapshot(workspace)
                result = entry(workspace, db)
                self.assertIsNotNone(result, label)
                self.assertNotEqual(result, "UNKNOWN", label)
                # ⛔ 没有创建 -shm / -wal；主库字节与整棵树不变。
                self.assertEqual(tree_snapshot(workspace), before, label)
                self.assertFalse(wal.exists(), label)
                self.assertFalse(shm.exists(), label)


# ---------------------------------------------------------------------------
# 2. Delivery / Inbox：initialize=False 与 readonly 跳过 DDL
# ---------------------------------------------------------------------------


class DeliveryInboxReadonlyTests(ReadonlyCase):

    def test_initialize_false_skips_ddl_but_stays_writable(self):
        store, db = self.fresh_store("delivery-skip")
        before = readonly_schema(db)
        delivery = Delivery(store, initialize=False)
        self.assertFalse(delivery.readonly)
        self.assertNotIn("delivery", ReadonlyStore(db).tables())
        self.assertEqual(readonly_schema(db), before)
        # 表真的不存在 ⇒ 读也报错，⛔ 不把未知表读成「没有交付」。
        with self.assertRaises(sqlite3.OperationalError):
            delivery.status(EXP)

    def test_readonly_delivery_and_inbox_skip_ddl_and_refuse_writes(self):
        store, db = self.fresh_store("delivery-readonly")
        before_schema = readonly_schema(db)
        before_bytes = db.read_bytes()
        readonly = ReadonlyStore(db)
        delivery = Delivery(readonly)
        inbox = Inbox(readonly, None, delivery)      # 只读：Accounts 可以不给
        self.assertTrue(delivery.readonly)
        self.assertTrue(inbox.readonly)
        self.assertIsNone(inbox.accounts)
        self.assertEqual(readonly_schema(db), before_schema)
        self.assertEqual(db.read_bytes(), before_bytes)
        # 缺表 ⇒ 读查询报错（不伪造空列表）。
        with self.assertRaises(sqlite3.OperationalError):
            inbox.pending_events()
        with self.assertRaises(InboxError):
            inbox.receive("fake", {"event_id": "ev-1", "exp_id": EXP})
        with self.assertRaises(DeliveryError):
            delivery.enqueue(EXP, "receipt", body="x", recipient=RECIPIENT,
                             request_key="rk-1")
        # 读方法本身可以调，但缺表时照样报错（⛔ 不把未知表读成「没有交付」）。
        with self.assertRaises(sqlite3.OperationalError):
            delivery.status(EXP)
        self.assertEqual(readonly_schema(db), before_schema)
        self.assertEqual(db.read_bytes(), before_bytes)

    def test_writable_inbox_still_requires_accounts_and_delivery(self):
        delivery = Delivery(self.store)
        with self.assertRaises(InboxError):
            Inbox(self.store, None, delivery)
        with self.assertRaises(InboxError):
            Inbox(self.store, None, None)

    def test_initialize_true_with_readonly_is_refused(self):
        store = ReadonlyStore(self.db)
        with self.assertRaises(DeliveryError):
            Delivery(store, initialize=True, readonly=True)
        with self.assertRaises(InboxError):
            Inbox(store, None, None, initialize=True, readonly=True)


# ---------------------------------------------------------------------------
# 3. DecisionService.readonly：构造零副作用 + 写入口全拒 + 不可判
# ---------------------------------------------------------------------------


class DecisionServiceReadonlyTests(ReadonlyCase):

    def test_factory_does_not_create_a_missing_database(self):
        missing = self.root / "no-such" / "decisions.sqlite3"
        service = DecisionService.readonly(missing)
        self.assertTrue(service.readonly)
        self.assertIsInstance(service.store, ReadonlyStore)
        # 只读构造不建 Accounts（只读查询用不到预算台账）。
        self.assertIsNone(service.inbox.accounts)
        status = service.status("E1.1")
        self.assertFalse(status["ok"])
        self.assertTrue(status["undecidable"])
        self.assertEqual(status["exit_code"], 2)
        self.assertIn("只读", "；".join(status["problems"]))
        listing = service.status(exp_id=EXP)
        self.assertTrue(listing["undecidable"])
        self.assertNotIn("decisions", listing)
        verdict = service.check_authorization(
            plan={"authorization": {"scope": "small"}}, owner_session=OWNER, exp_id=EXP)
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["undecidable"])
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())
        # 直接传路径是误用：只读要用工厂，写要先 Store —— ⛔ 不静默当只读。
        with self.assertRaises(DecisionError):
            DecisionService(missing)

    def test_readonly_true_on_a_writable_store_does_not_migrate(self):
        store, db = self.fresh_store("readonly-kwarg")
        before = readonly_schema(db)
        service = DecisionService(store, readonly=True)
        self.assertTrue(service.readonly)
        self.assertIsInstance(service.store, ReadonlyStore)
        self.assertEqual(service.store.db_path, str(db))
        self.assertNotIn("decisions", ReadonlyStore(db).tables())
        self.assertEqual(readonly_schema(db), before)
        status = service.status("E1.1")
        self.assertTrue(status["undecidable"])

    def test_legacy_schema_is_not_migrated_and_reads_are_undecidable(self):
        directory = self.case / "legacy"
        directory.mkdir()
        db = directory / "runtime.sqlite3"
        # 旧库：只有老的 experiments/events，外加一张**列不全**的 decisions。
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.executescript(
                "CREATE TABLE experiments(id TEXT PRIMARY KEY, goal TEXT, state TEXT,"
                " version INTEGER, created_at REAL, updated_at REAL);"
                "CREATE TABLE events(id INTEGER PRIMARY KEY, exp_id TEXT, kind TEXT,"
                " actor TEXT, payload_json TEXT, created_at REAL);"
                "CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, exp_id TEXT);"
                "INSERT INTO experiments VALUES('exp-01','','planned',0,0,0);")
            conn.commit()
        before_schema = readonly_schema(db)
        before_bytes = db.read_bytes()
        service = DecisionService.readonly(db)
        status = service.status("E1.1")
        self.assertFalse(status["ok"])
        self.assertTrue(status["undecidable"])
        self.assertIn("缺", "；".join(status["problems"]))
        verdict = service.check_authorization(
            plan=workflow_plan(), owner_session=OWNER, exp_id=EXP)
        self.assertTrue(verdict["undecidable"])
        self.assertFalse(verdict["ok"])
        # 一条 DDL 都不许跑：表 / 列 / 触发器 / 文件字节全不变。
        self.assertEqual(readonly_schema(db), before_schema)
        self.assertEqual(db.read_bytes(), before_bytes)

    def test_table_complete_but_stale_columns_are_undecidable(self):
        """旧 schema 的真实形态：表都在，但 `decisions` 少列 ⇒ 不可判，⛔ 不补列。"""
        directory = self.case / "stale-columns"
        directory.mkdir()
        db = directory / "runtime.sqlite3"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            for table, columns in DecisionService.READONLY_REQUIRED_COLUMNS:
                chosen = ("decision_id", "exp_id") if table == "decisions" else columns
                conn.execute(f'CREATE TABLE {table}({", ".join(chosen)})')
            conn.commit()
        before_schema = readonly_schema(db)
        before_bytes = db.read_bytes()
        service = DecisionService.readonly(db)
        status = service.status("E1.1")
        self.assertTrue(status["undecidable"])
        self.assertIn("旧 schema", "；".join(status["problems"]))
        self.assertEqual(readonly_schema(db), before_schema)
        self.assertEqual(db.read_bytes(), before_bytes)

    def test_all_write_entries_are_refused_and_leave_no_files(self):
        service = DecisionService.readonly(self.db)
        before_schema = readonly_schema(self.db)
        before_bytes = self.db.read_bytes()
        plan = workflow_plan()
        calls = [
            ("prepare", lambda: service.prepare(exp_id=EXP, decision=decision(),
                                                plan_set=plan_set())),
            ("send", lambda: service.send("E1.1")),
            ("send_page", lambda: service.send_page("E1.1")),
            ("send_card", lambda: service.send_card("E1.1")),
            ("tick", lambda: service.tick()),
            ("ingest", lambda: service.ingest("fake", {"event_id": "ev-1",
                                                       "exp_id": EXP})),
            ("apply_event", lambda: service.apply_event("fake", "ev-1")),
            ("poll_channel", lambda: service.poll_channel()),
            ("open_subscription", lambda: service.open_subscription()),
            ("claim_reply", lambda: service.claim_reply("worker-1")),
            ("deliver_reply", lambda: service.deliver_reply(1)),
            ("answer", lambda: service.answer({"source": "fake", "event_id": "ev-1"},
                                              f"答 {EXP}")),
            ("progress_verdict", lambda: service.progress_verdict(EXP)),
            ("authorization_check(record=True)",
             lambda: service.check_authorization(plan=plan, owner_session=OWNER,
                                                 exp_id=EXP, record=True)),
        ]
        for label, call in calls:
            with self.subTest(call=label):
                with self.assertRaises(ReadonlyServiceError):
                    call()
        self.assertFalse((self.case / "web").exists())
        self.assertEqual(readonly_schema(self.db), before_schema)
        self.assertEqual(self.db.read_bytes(), before_bytes)

    def test_status_and_authorize_check_leave_tree_and_database_byte_identical(self):
        self.send_and_approve()
        before = tree_snapshot(self.case)
        readonly = DecisionService.readonly(self.db)
        status = readonly.status("E1.1")
        listing = readonly.status(exp_id=EXP)
        reply = readonly.reply_status(EXP)
        verdict = readonly.check_authorization(plan=workflow_plan(), owner_session=OWNER,
                                               exp_id=EXP)
        small = readonly.check_authorization(
            plan=workflow_plan(authorization={"scope": "small"}),
            owner_session=OWNER, exp_id=EXP)
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["state"], "resolved")
        self.assertTrue(status["chain"]["complete"])
        self.assertEqual(listing["count"], 1)
        self.assertEqual(reply["text_events"], 0)
        self.assertEqual(reply["overdue"], [])
        self.assertTrue(verdict["ok"], verdict)
        self.assertTrue(small["ok"], small)
        self.assertEqual(small["resolution"], "scope_exempt")
        # 含 DB 字节、审批页字节、目录结构：一个字节都不改。
        self.assertEqual(tree_snapshot(self.case), before)

    def test_read_only_verdicts_match_the_writable_service(self):
        self.send_and_approve()
        readonly = DecisionService.readonly(self.db)
        full = workflow_plan()
        writable = self.service.check_authorization(plan=full, owner_session=OWNER,
                                                    exp_id=EXP)
        read_only = readonly.check_authorization(plan=full, owner_session=OWNER, exp_id=EXP)
        self.assertTrue(writable["ok"], writable)
        self.assertTrue(read_only["ok"], read_only)
        for key in ("decision_id", "resolution", "approved", "execution_sha256",
                    "research_sha256", "scope", "exp_id", "owner_session"):
            self.assertEqual(read_only[key], writable[key], key)
        # 改 argv / 换会话 / 换范围 ⇒ 只读绑定照样失效（不放宽）。
        changed = workflow_plan(argv=("python", "-m", "run_eval", "--tasks", "other.json"))
        self.assertFalse(readonly.check_authorization(plan=changed, owner_session=OWNER,
                                                      exp_id=EXP)["ok"])
        self.assertFalse(readonly.check_authorization(plan=full, owner_session="sess-other",
                                                      exp_id=EXP)["ok"])
        self.assertFalse(readonly.check_authorization(
            plan=workflow_plan(authorization={"scope": "submission"}),
            owner_session=OWNER, exp_id=EXP)["ok"])
        self.assertEqual(read_only["execution_sha256"], execution_fingerprint(full))

    def test_real_large_approval_is_bound_read_only(self):
        """真实 large 批准：页 / 卡 / 回执 / 短码消费 / 身份都在，只读服务仍认它。"""
        self.send_and_approve()
        readonly = DecisionService.readonly(self.db)
        verdict = readonly.check_authorization(plan=workflow_plan(), owner_session=OWNER,
                                               exp_id=EXP)
        self.assertTrue(verdict["authorized"], verdict)
        self.assertEqual(verdict["decision_id"], "E1.1")
        self.assertEqual(verdict["approved"], ["A"])
        self.assertTrue(verdict["evidence"]["event"]["principal"])
        self.assertTrue(readonly.chain("E1.1")["complete"])

    def test_small_plan_is_exempt_read_only_and_large_without_approval_is_refused(self):
        """small = 已授权范围内的小样本（免审批）；large = 必须有真实批准。"""
        readonly = DecisionService.readonly(self.db)
        small = readonly.check_authorization(
            plan=workflow_plan(authorization={"scope": "small"}),
            owner_session=OWNER, exp_id=EXP)
        self.assertTrue(small["ok"], small)
        self.assertEqual(small["resolution"], "scope_exempt")
        large = readonly.check_authorization(plan=workflow_plan(), owner_session=OWNER,
                                             exp_id=EXP)
        self.assertFalse(large["ok"])
        self.assertTrue(any("没有找到" in item for item in large["problems"]),
                        large["problems"])


# ---------------------------------------------------------------------------
# 4. 实际 `workflow_cli check`：不建库、不迁移、不改一个字节
# ---------------------------------------------------------------------------


class WorkflowCliReadonlyTests(flow_fixtures.FlowCase):
    """用主链自己的夹具造一个**真的过了 prepare + 审计**的工作区，再跑真实 CLI check。"""

    def setUp(self):
        super().setUp()
        from kernel.autoresearch import workflow_cli
        # 不为了测试去解码真实宿主日志：审计核验器给 None ⇒ 只会 `declared` ⇒ 仍然 fail-closed。
        # ⛔ 这不是放宽闸：只换掉「读真实宿主日志」这一步，监控 / 准入 / 只读 Store 全走生产构造。
        original = workflow_cli._host_verifier
        workflow_cli._host_verifier = lambda: None
        self.addCleanup(setattr, workflow_cli, "_host_verifier", original)

    def cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = workflow_cli_main(argv)
        return code, out.getvalue()

    def check_argv(self, workspace, plan="plans/P1.json"):
        return ["check", "--workspace", str(workspace), "--topic", flow_fixtures.TOPIC,
                "--plan", plan, "--owner-session", flow_fixtures.SESSION,
                "--intent-none", "--"] + self.argv

    def test_actual_cli_check_writes_nothing(self):
        self.ready()  # 真的 prepare + 审计（写盘发生在这一步，之后才拍快照）
        before = tree_snapshot(self.ws)
        code, output = self.cli(self.check_argv(self.ws))
        payload = json.loads(output)
        # 生产接线的监控 / 准入在这份合成工作区上判不放行 ⇒ 规则拒绝（9），
        # 但**不是**「不可判」：它确实读到了库并跑完了闸。
        self.assertEqual(code, 9, output)
        self.assertFalse(payload["ok"])
        self.assertNotIn("undecidable", payload)
        self.assertIn("monitor", payload["checks"])
        self.assertIn("authorization", payload["checks"])
        self.assertFalse(payload["checks"]["monitor"]["ok"])
        self.assertEqual(self.run_row_count(), 0)
        self.assertEqual(self.marker_lines(), [])
        # 含 DB 字节：只读检查一个字节都不改（也没建 monitoring 事件账）。
        self.assertEqual(tree_snapshot(self.ws), before)
        self.assertFalse((self.ws / ".autoresearch" / "monitoring.sqlite3").exists())

    def test_actual_cli_check_on_missing_database_creates_nothing(self):
        empty = self.ws.parent / "empty-workspace"
        empty.mkdir()
        before = tree_snapshot(empty)
        code, output = self.cli(self.check_argv(empty, plan="plans/absent.json"))
        payload = json.loads(output)
        self.assertEqual(code, 2, output)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["undecidable"])
        self.assertTrue(any("不可判" in item for item in payload["problems"]),
                        payload["problems"])
        # 不建 `.autoresearch/`、不建库、不建目录。
        self.assertEqual(tree_snapshot(empty), before)
        self.assertFalse((empty / ".autoresearch").exists())

    def test_actual_cli_check_on_old_schema_does_not_migrate(self):
        old = self.ws.parent / "old-workspace"
        (old / ".autoresearch").mkdir(parents=True)
        db = old / ".autoresearch" / "runtime.sqlite3"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.executescript(
                "CREATE TABLE experiments(id TEXT PRIMARY KEY, goal TEXT, state TEXT,"
                " version INTEGER, created_at REAL, updated_at REAL);"
                "CREATE TABLE events(id INTEGER PRIMARY KEY, exp_id TEXT, kind TEXT,"
                " actor TEXT, payload_json TEXT, created_at REAL);")
            conn.commit()
        (old / "plans").mkdir()
        (old / "plans" / "P1.json").write_text(json.dumps({"id": "P1"}), encoding="utf-8")
        before_tree = tree_snapshot(old)
        before_schema = readonly_schema(db)
        before_bytes = db.read_bytes()
        code, output = self.cli(self.check_argv(old))
        payload = json.loads(output)
        self.assertEqual(code, 2, output)
        self.assertTrue(payload["undecidable"], payload)
        # 旧 schema 不迁移：表、触发器、文件字节全不变。
        self.assertEqual(readonly_schema(db), before_schema)
        self.assertEqual(db.read_bytes(), before_bytes)
        self.assertEqual(tree_snapshot(old), before_tree)

    def test_actual_cli_check_on_wal_database_creates_no_files(self):
        """真实活 WAL fixture：`check` 不新建任何文件，主库与 `-wal` 字节不变。

        先把已 prepare 的工作区库切到 WAL 并保留一个写连接，让 `-wal` 与 `-shm` 在快照之前
        就存在（这条测「有共享内存的 WAL 库」整链不额外建文件、不动主库与 wal）。
        干净 WAL（无 sidecar）见下一条 `test_actual_cli_check_on_clean_wal_database...`。
        """
        self.ready()
        db = Path(self.db)
        writer = sqlite3.connect(str(db))
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE IF NOT EXISTS wal_probe(x)")
            writer.execute("INSERT INTO wal_probe VALUES (1)")
            writer.commit()
            wal = Path(str(db) + "-wal")
            shm = Path(str(db) + "-shm")
            self.assertTrue(wal.is_file())
            self.assertTrue(shm.is_file())
            before = tree_snapshot(self.ws)
            code, output = self.cli(self.check_argv(self.ws))
            payload = json.loads(output)
            self.assertEqual(code, 9, output)
            self.assertNotIn("undecidable", payload)
            self.assertIn("monitor", payload["checks"])
            self.assertEqual(self.run_row_count(), 0)
            self.assertEqual(self.marker_lines(), [])
            after = tree_snapshot(self.ws)
            # ⛔ 没有新文件；主库与 -wal 一个字节都不改；-shm 是 SQLite 锁文件（原本就在）。
            self.assertEqual(sorted(after), sorted(before))
            self.assertEqual(after[".autoresearch/runtime.sqlite3"],
                             before[".autoresearch/runtime.sqlite3"])
            self.assertEqual(after[".autoresearch/runtime.sqlite3-wal"],
                             before[".autoresearch/runtime.sqlite3-wal"])
            self.assertIn(".autoresearch/runtime.sqlite3-shm", before)
            self.assertIn(".autoresearch/runtime.sqlite3-shm", after)
            self.assertFalse(any(name.endswith("-journal") for name in after))
        finally:
            writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            writer.close()

    def test_actual_cli_check_on_clean_wal_database_creates_nothing(self):
        """真实 clean WAL（无任何 sidecar）：`workflow_cli check` 整条链路都不建文件。"""
        self.ready()
        db = Path(self.db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        for sidecar in (wal, shm):
            if sidecar.exists():
                sidecar.unlink()
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())
        before = tree_snapshot(self.ws)
        code, output = self.cli(self.check_argv(self.ws))
        payload = json.loads(output)
        self.assertEqual(code, 9, output)
        self.assertNotIn("undecidable", payload)
        self.assertIn("monitor", payload["checks"])
        self.assertEqual(self.marker_lines(), [])
        # ⛔ 没有任何路径（Topics / Admission / Workflow / DecisionService）创建 -shm / -wal。
        self.assertEqual(tree_snapshot(self.ws), before)
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())
        # 用只读句柄核 run 行数（⛔ 不用 read-write 连接，否则它自己会创建 sidecar）。
        store = ReadonlyStore(db)
        conn = store.connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wf_runs").fetchone()[0], 0)
        finally:
            conn.close()
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())


# ---------------------------------------------------------------------------
# 5. Monitoring 构造与 check_start 零写（核实「本来不 DDL」）
# ---------------------------------------------------------------------------


class MonitoringConstructionTests(unittest.TestCase):

    def test_construction_and_check_start_do_not_create_the_ledger(self):
        with tempfile.TemporaryDirectory(prefix="hil_monitoring_") as directory:
            workspace = Path(directory) / "ws"
            workspace.mkdir()
            before = tree_snapshot(workspace)
            monitor = Monitoring(workspace)          # host=None ⇒ 会话证据不可判
            result = monitor.check_start("session-owner", "P1", action="launch")
            self.assertIsInstance(result, dict)
            self.assertFalse(result.get("ok"))
            self.assertFalse((workspace / ".autoresearch").exists())
            self.assertEqual(tree_snapshot(workspace), before)


if __name__ == "__main__":
    unittest.main()
