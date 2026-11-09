# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 17:40 +08:00（只读句柄 ReadonlyStore + WAL sidecar 边界）
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/store.py
# CUMULATIVE-DELTA: 2026-11-09 ensure_experiment 增加受控内部连接参数，允许在调用方已开启的事务里登记实验（避免外层写事务再开连接自锁）。另新增 ReadonlyStore（不建库不建目录不迁移、transaction()/emit() 全拒、tables()/schema()/columns() 只报真实 schema）与 is_readonly_store()，供只读入口复用；Store 显式声明 readonly=False。只读连接按 SQLite 头里的日志模式与 -wal/-shm 实况选路：缺 -wal 用 immutable=1 并前后核对主库 identity，有 -wal 无 -shm 判不可判，有 -wal 与 -shm 走已有共享内存（实测 3.49.1：mode=ro 在缺 sidecar 的 WAL 库上会创建 -shm/-wal）。此前：Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path

class StoreError(ValueError):
    pass

class VersionConflict(StoreError):
    pass

class InvalidTransition(StoreError):
    pass

class IdempotencyConflict(StoreError):
    pass

class ReadonlyViolation(StoreError):
    """只读状态库拒绝了写入（写事务 / 事件流 / 任何 DDL）。"""
STATES = frozenset(('planned', 'awaiting_approval', 'ready', 'running', 'collecting', 'auditing', 'completed', 'failed', 'paused'))
ALLOWED_TRANSITIONS = {'planned': frozenset(('awaiting_approval', 'paused', 'failed')), 'awaiting_approval': frozenset(('ready', 'planned', 'paused', 'failed')), 'ready': frozenset(('running', 'awaiting_approval', 'paused', 'failed')), 'running': frozenset(('collecting', 'paused', 'failed')), 'collecting': frozenset(('auditing', 'paused', 'failed')), 'auditing': frozenset(('completed', 'paused', 'failed')), 'paused': frozenset(('awaiting_approval', 'failed')), 'failed': frozenset(), 'completed': frozenset()}

def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value

class Store:
    """可写状态库：构造即建库/迁移（`CREATE TABLE IF NOT EXISTS`）。"""

    #: 给通用「这个 store 能不能写」判据用；只读句柄显式声明 True（见 `ReadonlyStore`）。
    readonly = False

    def __init__(self, db_path):
        self.db_path = str(Path(db_path).resolve())
        if str(db_path) == ':memory:':
            raise ValueError('Store requires a file-backed database')
        self.busy_timeout_ms = 10000
        with self.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS experiments(\n                id TEXT PRIMARY KEY, goal TEXT, state TEXT,\n                version INTEGER DEFAULT 0, created_at REAL, updated_at REAL)')
            conn.execute('CREATE TABLE IF NOT EXISTS events(\n                id INTEGER PRIMARY KEY, exp_id TEXT REFERENCES experiments(id),\n                kind TEXT NOT NULL, actor TEXT NOT NULL,\n                payload_json TEXT NOT NULL, created_at REAL NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS store_requests(\n                request_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL,\n                result_json TEXT NOT NULL)')
            conn.execute("CREATE TRIGGER IF NOT EXISTS events_no_update\n                BEFORE UPDATE ON events BEGIN\n                SELECT RAISE(ABORT, 'events are append-only'); END")
            conn.execute("CREATE TRIGGER IF NOT EXISTS events_no_delete\n                BEFORE DELETE ON events BEGIN\n                SELECT RAISE(ABORT, 'events are append-only'); END")

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute(f'PRAGMA busy_timeout={self.busy_timeout_ms}')
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            raise
        finally:
            conn.close()

    def emit(self, conn, exp_id, kind, actor, payload):
        if not conn.in_transaction:
            raise StoreError('emit requires an active transaction')
        require_text(kind, 'kind')
        require_text(actor, 'actor')
        cursor = conn.execute('INSERT INTO events\n            (exp_id,kind,actor,payload_json,created_at) VALUES(?,?,?,?,?)', (exp_id, kind, actor, canonical_json(payload), time.time()))
        return cursor.lastrowid

    def ensure_experiment(self, exp_id, goal='', _connection=None):
        """登记实验；`_connection` 只给受控内部入口复用已开启事务的连接。

        不传 `_connection` 时保持原有的独立事务语义。传入时必须在调用方已开启的
        事务里执行，避免“外层已开写事务、这里再开连接”自锁；是否提交或回滚
        由调用方的事务决定。
        """
        require_text(exp_id, 'exp_id')
        if not isinstance(goal, str):
            raise ValueError('goal must be text')
        if _connection is not None:
            return self._ensure_experiment(_connection, exp_id, goal)
        with self.transaction() as conn:
            return self._ensure_experiment(conn, exp_id, goal)

    def _ensure_experiment(self, conn, exp_id, goal):
        now = time.time()
        cursor = conn.execute("INSERT OR IGNORE INTO experiments\n                (id,goal,state,version,created_at,updated_at)\n                VALUES(?,?,'planned',0,?,?)", (exp_id, goal, now, now))
        if cursor.rowcount:
            self.emit(conn, exp_id, 'experiment_created', 'store', {'goal': goal})
        return dict(conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone())

    def get_experiment(self, exp_id):
        conn = self.connect()
        try:
            row = conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def transition(self, exp_id, expected_version, new_state, actor, idempotency_key):
        require_text(exp_id, 'exp_id')
        require_text(actor, 'actor')
        require_text(idempotency_key, 'idempotency_key')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a non-negative integer')
        if new_state not in STATES:
            raise InvalidTransition('unknown experiment state')
        payload = canonical_json([exp_id, expected_version, new_state, actor])
        with self.transaction() as conn:
            old = conn.execute('SELECT * FROM store_requests WHERE request_key=?', (idempotency_key,)).fetchone()
            if old:
                if old['payload_json'] != payload:
                    raise IdempotencyConflict('request key has different payload')
                return json.loads(old['result_json'])
            row = conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
            if row is None:
                raise StoreError('experiment does not exist')
            if row['version'] != expected_version:
                raise VersionConflict('experiment version changed')
            if new_state not in ALLOWED_TRANSITIONS.get(row['state'], ()):
                raise InvalidTransition(f"transition {row['state']} -> {new_state} is forbidden")
            conn.execute('UPDATE experiments SET state=?,version=version+1,updated_at=?\n                WHERE id=? AND version=?', (new_state, time.time(), exp_id, expected_version))
            result = dict(conn.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone())
            self.emit(conn, exp_id, 'experiment_transition', actor, {'from': row['state'], 'to': new_state, 'version': result['version'], 'idempotency_key': idempotency_key})
            conn.execute('INSERT INTO store_requests VALUES(?,?,?)', (idempotency_key, payload, canonical_json(result)))
            return result


def is_readonly_store(store):
    """这个 store 句柄是不是只读的。`Store.readonly is False`，`ReadonlyStore.readonly is True`。

    只认**对象自己声明的**标志：⛔ 不从路径、文件权限或「库看起来像只读」去猜。
    """
    return bool(getattr(store, 'readonly', False))


#: SQLite 文件头魔数（前 16 字节）。用来判「这个文件是不是 SQLite」与它的日志模式。
SQLITE_MAGIC = b'SQLite format 3\x00'


def sqlite_header(path):
    """读 SQLite 文件头里做只读判据需要的字段；不是 SQLite / 读不到 ⇒ `None`。

    * `read_version`：偏移 18。`1` = rollback journal，`2` = WAL（持久写在头里）。
    * `change_counter`：偏移 24。每次提交 +1 —— 只读打开前后相等 ⇒ 主库没被写。
    * `page_count`：偏移 28。

    ⛔ 只读 100 字节，不建文件、不加锁、不打开 SQLite 连接。
    """
    try:
        with Path(path).open('rb') as stream:
            head = stream.read(100)
    except OSError:
        return None
    if len(head) < 100 or not head.startswith(SQLITE_MAGIC):
        return None
    return {'read_version': head[18], 'write_version': head[19],
            'change_counter': int.from_bytes(head[24:28], 'big'),
            'page_count': int.from_bytes(head[28:32], 'big')}


def file_identity(path):
    """主库的「没被改写」证据：大小 / mtime_ns / change_counter / page_count。"""
    stat = Path(path).stat()
    header = sqlite_header(path)
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'change_counter': header['change_counter'] if header else None,
            'page_count': header['page_count'] if header else None}


class ReadonlyStore:
    """只读状态库句柄（`mode=ro` + `PRAGMA query_only=ON`）。**构造与读都零副作用**。

    与 `Store` 的区别只有「不写」，但这几条是硬约束：

    * **不建库、不建目录、不迁移 schema。** 库文件不存在 ⇒ `connect()` 抛 `StoreError`；
      ⛔ 不 `mkdir`、不 `touch`、不 `CREATE TABLE`、不补触发器。
    * **读也不许创建文件（WAL 库的 `-shm` / `-wal` 陷阱）。** 实测（SQLite 3.49.1）：
      - WAL 头 + 无 sidecar：`mode=ro` **会创建** `-shm` 与 `-wal`；`immutable=1` 不创建。
      - WAL 头 + 有 `-wal`、无 `-shm`：`mode=ro` **会创建** `-shm`；`immutable=1` 不创建，
        但会**读旧主库**（丢掉 WAL 里未 checkpoint 的已提交数据，实测「表都看不到」）。
      - WAL 头 + `-wal` 与 `-shm` 都在：`mode=ro` 不创建文件（走已有共享内存锁）。
      - WAL 头 + 只有陈旧 `-shm`：`mode=ro` **会创建** `-wal`；`immutable=1` 不创建。
      - rollback（DELETE）库：`mode=ro` 不创建任何文件。
      所以本类按头里的日志模式与 sidecar 实况选连接方式，**⛔ 不为了读得下去创建文件**：
      - `-wal` 存在但 `-shm` 不在 ⇒ **不可判**（不静默 immutable 读旧主库、也不创建 `-shm`）；
      - `-wal` 与 `-shm` 都在 ⇒ `mode=ro`（已有共享内存读路径）；
      - 没有 `-wal`（含只剩陈旧 `-shm`、两边都没有）⇒ `immutable=1`，
        并在打开前后核对 sidecar 集合与主库 identity（大小 / mtime_ns / change_counter /
        page_count）不变；一有变化就拒 ⇒ 不可判。
    * **所有写事务拒绝。** `Store.transaction()` 是 `BEGIN IMMEDIATE`（写事务）；本类的
      `transaction()` 直接抛 `ReadonlyViolation`，⛔ 不提供「只读事务伪装成写事务」的口子。
    * **事件流写入拒绝。** `emit()` 抛 `ReadonlyViolation`。
    * **读不到的就说读不到。** `tables()` / `schema()` 只报告 `sqlite_master` 里**真实存在**
      的表与列；缺表缺列不返回伪值、不把未知表读成空数据，调用方据此返回「不可判」。

    ⛔ 不复制数据库、不 `object.__new__` 拼对象：只读就是同一个文件上的只读连接。
    """

    #: 通用判据（`is_readonly_store`）读这个标志。
    readonly = True

    def __init__(self, db_path, *, busy_timeout_ms=10000):
        if str(db_path) == ':memory:':
            raise ValueError('ReadonlyStore requires a file-backed database')
        self.db_path = str(Path(db_path).resolve())
        self.busy_timeout_ms = int(busy_timeout_ms)

    def is_file(self):
        """库文件是否存在。⛔ 只查，不创建。"""
        return Path(self.db_path).is_file()

    def _sidecars(self):
        path = Path(self.db_path)
        return path, Path(str(path) + '-wal'), Path(str(path) + '-shm')

    def connect(self):
        """只读连接。**不创建任何文件**（WAL 库按 sidecar 实况选 `mode=ro` / `immutable=1`）。"""
        path, wal, shm = self._sidecars()
        if not path.is_file():
            raise StoreError(f'状态库不存在（只读模式不建库）：{self.db_path}')
        header = sqlite_header(path) if path.stat().st_size else None
        if header is None and path.stat().st_size:
            raise StoreError(f'不是 SQLite 数据库（文件头不符）⇒ 不可判：{self.db_path}')
        immutable = False
        stable = None
        sidecars_before = (wal.is_file(), shm.is_file())
        if header is not None and header['read_version'] == 2:
            # WAL 模式：mode=ro 可能在缺 sidecar 时把它们创建出来。
            wal_exists, shm_exists = sidecars_before
            if wal_exists and not shm_exists:
                raise StoreError(
                    'WAL 主库存在 -wal 但没有 -shm（共享内存）：主库可能缺尚未 checkpoint 的'
                    '已提交数据，只读不能静默按旧主库读，也不能创建 -shm ⇒ 不可判；'
                    '先让写进程 checkpoint 并正常关闭，或提供已有的共享内存读路径')
            if wal_exists and shm_exists:
                immutable = False          # 已有共享内存：走 mode=ro，不创建文件
            else:
                # 没有 -wal（含只剩陈旧 -shm 与两边都没有）：主库就是完整状态。
                # 用 immutable=1 避免 mode=ro 创建 -shm / -wal；打开前后核对稳定性。
                stable = file_identity(path)
                immutable = True
        elif header is not None and header['read_version'] != 1:
            raise StoreError(f"SQLite 文件头的日志模式不可判（read_version="
                             f"{header['read_version']}）⇒ 不可判：{self.db_path}")
        query = 'mode=ro' + ('&immutable=1' if immutable else '')
        conn = sqlite3.connect(path.as_uri() + '?' + query, uri=True,
                               timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f'PRAGMA busy_timeout={self.busy_timeout_ms}')
            # 双保险：即使 URI 写错也不允许这条连接写入。
            conn.execute('PRAGMA query_only=ON')
            if immutable:
                after = file_identity(path)
                if (wal.is_file(), shm.is_file()) != sidecars_before or after != stable:
                    raise StoreError(
                        'WAL 主库在只读打开期间出现 -wal / -shm 或被改写：immutable 读可能'
                        '读到旧主库 ⇒ 不可判（本次没有创建任何文件）')
        except BaseException:
            conn.close()
            raise
        return conn

    def tables(self):
        """现有表名集合。库不存在 ⇒ 空集合（**不**读成「表存在但没数据」）。"""
        if not self.is_file():
            return frozenset()
        conn = self.connect()
        try:
            return frozenset(row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"))
        finally:
            conn.close()

    def missing_tables(self, names):
        """`names` 里缺哪些表（保持传入顺序）。库不存在 ⇒ 全部缺。"""
        present = self.tables()
        return [name for name in names if name not in present]

    def schema(self):
        """`{表名: 列名集合}`：一次只读连接读 `sqlite_master` + `PRAGMA table_info`。

        库不存在 ⇒ `{}`；⛔ 不补表、不补列、不把未知表读成空数据。
        """
        if not self.is_file():
            return {}
        conn = self.connect()
        try:
            names = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            out = {}
            for name in names:
                quoted = str(name).replace('"', '""')
                out[name] = frozenset(
                    row[1] for row in conn.execute(f'PRAGMA table_info("{quoted}")'))
            return out
        finally:
            conn.close()

    def columns(self, table):
        """一张表的列名集合。表不存在 ⇒ 空集合（⛔ 不按新表结构补）。"""
        return self.schema().get(str(table), frozenset())

    @contextmanager
    def transaction(self):
        """只读句柄没有写事务：⛔ 拒绝而不是降级成只读事务（那会让调用方以为写成功了）。"""
        raise ReadonlyViolation(
            '只读状态库拒绝写事务：只读入口只做查询；要写就用 runtime.store.Store。')
        yield  # pragma: no cover - 让 @contextmanager 保持生成器语义

    def emit(self, conn, exp_id, kind, actor, payload):
        raise ReadonlyViolation('只读状态库拒绝写事件流（emit）')
