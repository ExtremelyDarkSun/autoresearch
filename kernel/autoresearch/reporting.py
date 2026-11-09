# GENERATED-BY: portable reporting migration, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 18:40 `_lease_state` 只读连接委托 runtime.store.ReadonlyStore
# PURPOSE: 研究现状页的完整供料、来源字节版本、正文验收回执、运行/整轮事件、只读诊断与流程状态投影。
# ORIGIN: 上游供料与交付工具的通用机制（供料包、来源快照指纹、正文验收、只读诊断、流程状态分离、事件追加），
#         去掉全部私人实例事实（机器名、会话号、比赛数据、历史台账与真实读数）。
# CUMULATIVE-DELTA: 2026-11-09 首次发布，覆盖 I1–I10、J7、J8；回执与供料协议稳定，声明等级只有
#         declared / verified 两级。2026-11-09 真实宿主接线：`host_verifier` 必须配显式 `proof`；
#         验证器抛错即拒且不落回执；会话标识比较忽略 `session-` 前缀（宿主会归一）；
#         轮数上限记为「总量 3 轮（含当前实验）」，不改校验器。
#         2026-11-09 只读零副作用：`_lease_state` 不再拼裸 `?mode=ro`，委托 `ReadonlyStore`
#         （WAL 读不创建 -shm/-wal）；不可读仍返回 UNKNOWN，不推断租约。

"""研究现状页的供料、版本、验收回执与只读诊断。

这一层解决的问题：**页面上的字是人写的，但「这一版正文覆盖了哪些来源」不能靠自述。**

所以每次交付都绑定四样可核值：

* **正文字节哈希** `body_sha256`（页面原始字节，不是去头去尾后的正文）；
* **来源清单逐文件哈希** `sources` + `source_fingerprint`（按文件字节算，不按 mtime）；
* **事件截止** `event_cutoff`（事件文件当时的确切字节与条数）；
* **版本回执** `version`（上面三样 + 任务号 + 写页角色与会话的联合哈希）。

另有两样**不能混同**的东西：写页**角色与会话来源**（`declared` = 调用方自述；
`verified` = 注入的宿主验证器给过真实派工与写入证据），以及**流转状态**
（心跳、租约、checkpoint 时效）。心不心跳、mtime 变没变，都不参与「正文是否新鲜」的判断。

三条硬边界
==========

1. **不启动宿主子会话、不发消息、不起进程。** 供料包是文本，不是派工；
   回执默认只记「调用方自述」，没有注入 `host_verifier` 就不能标 `verified`。
   生成供料与自声明回执，都不能冒充真实派工或真实送达。
2. **版本一律按文件字节哈希**，不按 mtime、不看心跳。改内容才换版本；同内容 touch 不作废。
3. **只读诊断真的只读**：`diagnose()` 与 `check()` 不建目录、不写锁、不写日志、不写页面、不写数据库。

覆盖的行为预期编号：I1–I10、J7、J8。对应关系见 `docs/EXPECTATIONS_ACCEPTANCE.md`。

稳定协议（与宿主验证器对接用）
==============================

`prepare()` 返回 `prepared_id`（来源指纹 + 供料包哈希 + 事件截止的哈希，不含时间戳，重复供料不变）
与 `dispatch`；`acknowledge()` 返回 `host_payload`，它就是交给宿主 `verify('scribe', payload)`
的那一份载荷（含 `workspace`、`page_path`、`page_sha256`、`prepared_id`、`proof`）。

`proof = {session_id, seq}` 指向 scribe 会话里**可见的那份回传**，`seq` 不许留空；
⛔ 不接受调用方自选日志路径。没有 proof 的载荷标 `declared_only=true`，只能当自述。

宿主结果（evidence）的要求见 `HOST_EVIDENCE_FIELDS` 与 `_validate_host_evidence()`：
能定位到那份可见回传（`proof` 回显，或宿主派工回执 `tool_call_id` + `source`）、会话一致、
页面字节一致、有成功的写入类工具调用（带 `content_sha256` 时必须等于页面字节）、有父子谱系；
缺一条就拒，**不降级成 declared**（宁可不认，不许把没有证据的东西写进回执）。
⛔ 只回显「现在的磁盘哈希」不算证据：那是自证。
⚠️ 验证器**抛错**时 ack 直接失败、不落回执（撕裂或歧义证据不能变成一张收据）；
要记 declared 回执就不传 `host_verifier`，由调用方显式选择。

验证器四种可直插写法见 `_call_verifier()`：`DSHHost` 实例、`DSHHost(...).verify` 绑方法、
`functools.partial(host.verify, 'scribe')`、单参函数。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from kernel.autoresearch.governance import (
    LEGACY_STATUS_SECTIONS,
    STATUS_SECTIONS,
    validate_status_page,
)
from kernel.autoresearch.runtime.store import ReadonlyStore, StoreError  # noqa: E402

SCHEMA = 1

#: 研究页八节（顺序即页序）。与 `governance.STATUS_SECTIONS` 同源，只在这里导出以便调用方少一个 import。
PAGE_SECTIONS = tuple(STATUS_SECTIONS)

#: 写页角色。本模块只接受这一个角色：负责人自写不验收。
WRITER_ROLE = 'scribe'

#: 声明等级。`DECLARED` 是调用方自述；`VERIFIED` 必须由注入的宿主验证器给出真实证据才可能出现。
DECLARED = 'declared'
VERIFIED = 'verified'

#: 运行事件种类。`run` 范围是单臂/单次运行；`round` 范围是整轮。
EVENT_KINDS = ('running', 'partial', 'completed', 'failed', 'stopped', 'superseded', 'collected')

#: 运行/整轮终态。单臂 `completed` 只是那一臂结束，**不是**整轮结束。
TERMINAL_KINDS = ('completed', 'failed', 'stopped', 'superseded')

SCOPES = ('run', 'round')

#: 负责人输入种类：显式用户要求、整轮结束后的负责人判断。
INPUT_KINDS = ('user_requirement', 'leader_conclusion')

TASK_STATES = ('pending_owner', 'pending_writer', 'pending_writer_retry', 'accepted')
LEGACY_TASK_STATES = {'pending_scribe': 'pending_writer', 'pending_scribe_retry': 'pending_writer_retry'}

#: 第 3 节最多保留的轮数（**总量上限，含当前实验**）。
#: 口径记录（2026-11-09 核对上游与本地）：上游 `TOPICS_RUNBOOK.md` §11.1 的「上限」行、
#: `SCRIBE_STATUS_PROMPT.md` 与专题页模板都写「本页只留最近 3 轮」，其中「当前实验优先」是
#: **取舍顺序**（先保当前实验），不是额外名额；目标仓库 `governance.validate_status_page` 同样判 ≤3。
#: ⇒ 采用「总量 3 轮」，不自行改动校验器。
MAX_REPORTED_ROUNDS = 3

#: checkpoint 时效阈值（小时）。超过它只能写「状态待核」。
CHECKPOINT_MAX_AGE_HOURS = 2.0

#: 单个来源内联进供料包的上限。超过它**显式**标为未内联并给补读路径，⛔ 不静默截断。
INLINE_LIMIT_BYTES = 262144

#: 旧锁接管阈值（秒）。
LOCK_STALE_SECONDS = 120

#: 宿主写入页面的工具名（真实会话通常用其中一种）。
#: ⚠️ 只给「调用过写工具」不够：要么调用带 `content_sha256`（宿主从该调用 arguments 的完整内容
#: 算出的哈希，必须等于页面字节），要么由宿主自己重演写入（含后续 edit）并把结果标在 `tool_calls[].ok`。
#: ⛔ 不接受拿「现在的磁盘页面哈希」回显 —— 那是自证（页面本来就是这次写出来的）。
WRITE_TOOLS = ('write', 'write_file', 'edit', 'edit_file')

#: 宿主 evidence 必须认识的字段（缺失的按 `_validate_host_evidence` 拒）。
HOST_EVIDENCE_FIELDS = (
    'verified',
    'session_id',
    'proof{session_id, seq} 回显 —— 或宿主派工回执标识 tool_call_id + source（二选一，须能定位可见回传）',
    'spawned_by | parent_session | owner_session | spawned',
    'page_sha256（宿主独立算出的页面字节哈希）',
    'tool_calls[{tool, path, ok, session_id?, seq?, content_sha256?}]'
    '：写入类工具必须成功；带了 content_sha256 就必须等于 page_sha256',
    'prepared_id?',
    'page_path?',
    'delivery?（另由通道验，不作为 verified 依据）',
    'reason?',
)

#: 只有写进 `process_status/` 才允许出现的流程字样；研究页出现它们只给警告，不算研究进展。
FLOW_MARKERS = (
    '心跳', '自推进', '租约', '审计额度', '登记流程', '机器可用性',
    'AUTODL_AVAILABILITY', 'PROCESS_STATUS', 'watchdog',
)

TOPICS_DIR = 'topics'
STATE_DIR = '.autoresearch/state'
REPORTING_DIR = STATE_DIR + '/reporting'
PROCESS_STATUS_DIR = STATE_DIR + '/process_status'

_TOPIC_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_TOKEN_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_LEGACY_ONLY_MARKERS = ('自推进状态', '范围与目标', '需要你拍板', '当前结论')

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LOCK_HELD = threading.local()


class ReportingError(ValueError):
    """供料、验收或诊断被拒。消息里写清**缺哪一步**与下一步命令。"""

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = list(problems or [])


# ---------------------------------------------------------------------------
# 小工具：时间、哈希、JSON
# ---------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds') if value else None


def _parse_ts(value):
    """把 checkpoint 里的时间戳解析成带时区的 datetime；读不出来返回 None。

    ⚠️ 只认**内容里的时间戳**。文件的 mtime 不是证据：拷贝、检出与 touch 都会改它。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def _digest(value):
    """字节哈希。传 bytes 就直接哈希，传其它就按规范化 JSON 哈希。"""
    data = value if isinstance(value, bytes) else _canonical(value).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ReportingError(f'{name} 必须是非空文本')
    return value


def state_name(value):
    """任务状态中性名。旧值 `pending_scribe*` 只在**读时**归一，盘上旧文件一个字节不改。"""
    return LEGACY_TASK_STATES.get(str(value or ''), str(value or ''))


# ---------------------------------------------------------------------------
# 工作区隔离与路径
# ---------------------------------------------------------------------------


def workspace_root(root):
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ReportingError(f'工作区不存在：{resolved}')
    return resolved


def require_topic(topic):
    if not isinstance(topic, str) or '..' in topic or not _TOPIC_RE.match(topic):
        raise ReportingError('非法专题名：只允许字母、数字与 . _ -，且不许含 ..')
    return topic


def require_token(value, name):
    if not isinstance(value, str) or '..' in value or '/' in value or '\\' in value \
            or not _TOKEN_RE.match(value):
        raise ReportingError(f'非法{name}：{value!r}（只允许字母、数字与 . _ -）')
    return value


def inside(root, value):
    """把「工作区内相对路径」解析成绝对路径；越界一律拒（查 `..` 也查软链接后的真实位置）。"""
    root = Path(root).resolve()
    path = Path(value)
    target = (path if path.is_absolute() else root / path).resolve()
    if target != root and not target.is_relative_to(root):
        raise ReportingError(f'路径不在工作区内：{value}')
    return target


def relative(root, path):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def topic_root(root, topic):
    """人读侧专题目录：`topics/<topic>/`。

    专题主文档、用户要求、证据与事前计划都在这里（`workspace.provision_topic` 的产出），
    人读研究页也在这里：`topics/<topic>/STATUS.md`。
    """
    require_topic(topic)
    return inside(root, f'{TOPICS_DIR}/{topic}')


def machine_dir(root, topic):
    """机器侧专题目录：`.autoresearch/state/reporting/<topic>/`。

    放供料包、来源快照、任务、回执、事件与输入。⛔ 研究正文不在这里。
    """
    require_topic(topic)
    return inside(root, f'{REPORTING_DIR}/{topic}')


def topic_dir(root, topic):
    """旧名兼容：等同 `machine_dir()`（机器侧落点）。人读侧请用 `topic_root()`。"""
    return machine_dir(root, topic)


def topic_exists(root, topic):
    """人读侧或机器侧任一存在即算专题存在（供只读检查判「未开始」）。"""
    return topic_root(root, topic).is_dir() or machine_dir(root, topic).is_dir()


def page_path(root, topic):
    """人读研究页：`topics/<topic>/STATUS.md`（与专题主文档同一目录）。"""
    return topic_root(root, topic) / 'STATUS.md'


def feed_path(root, topic):
    return machine_dir(root, topic) / 'FEED.md'


def sources_path(root, topic):
    return machine_dir(root, topic) / 'SOURCES.json'


def task_path(root, topic):
    return machine_dir(root, topic) / 'TASK.json'


def receipt_path(root, topic):
    return machine_dir(root, topic) / 'RECEIPT.json'


def events_path(root, topic):
    return machine_dir(root, topic) / 'EVENTS.jsonl'


def inputs_path(root, topic):
    return machine_dir(root, topic) / 'INPUTS.jsonl'


def lock_path(root, topic):
    return machine_dir(root, topic) / '.reporting.lock'


def process_status_path(root, topic):
    require_topic(topic)
    return inside(root, f'{PROCESS_STATUS_DIR}/{topic}.json')


def process_status_note_path(root, topic):
    return process_status_path(root, topic).with_suffix('.md')


def page_rel(topic):
    """人读页的工作区相对路径（与专题主文档同目录）。"""
    return f'{TOPICS_DIR}/{topic}/STATUS.md'


def feed_rel(topic):
    return f'{REPORTING_DIR}/{topic}/FEED.md'


def topic_rel(topic):
    return f'{TOPICS_DIR}/{topic}'


def ack_command(topic, session='<scribe 会话ID>'):
    return (f'python research.py reporting ack --topic {topic} '
            f'--writer-role scribe --writer-session {session}')


def file_sha256(path):
    """任意文件的字节哈希。版本一律按字节算，⛔ 不看 mtime。"""
    return _digest(Path(path).read_bytes())


def page_sha256(root, topic):
    """研究页当前字节哈希；页面不存在返回 None。宿主核验与验收都用同一个值。"""
    page = page_path(root, topic)
    return file_sha256(page) if page.is_file() else None


# ---------------------------------------------------------------------------
# 读写：原子写、只读读、加锁
# ---------------------------------------------------------------------------


def _load_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    text = path.read_text(encoding='utf-8-sig')
    if not text.strip():
        return default
    return json.loads(text)


def _rows(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.{time.time_ns()}.tmp')
    temp.write_text(text, encoding='utf-8', newline='\n')
    os.replace(temp, path)


def _save_json(path, document):
    _atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def _thread_lock(key):
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


def _claim_lock(path):
    """同一专题互斥。跨进程用 O_EXCL，进程内先过线程锁；旧锁超过阈值才接管。"""
    for attempt in (0, 1):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if attempt:
                raise ReportingError(
                    f'同一专题的交付操作正在进行；锁文件 {path.name} 还在。'
                    f'请核实后重试（超过 {LOCK_STALE_SECONDS} 秒的旧锁会被接管）')
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                age = 0.0
            if age < LOCK_STALE_SECONDS:
                raise ReportingError(
                    f'同一专题的交付操作正在进行（锁龄 {age:.0f} 秒）；请等它结束再试')
            try:
                path.unlink()
            except OSError:
                pass
            continue
        os.write(handle, _canonical({'pid': os.getpid(), 'at': now_iso()}).encode('utf-8'))
        os.close(handle)
        return
    raise ReportingError('拿不到专题交付锁')


@contextmanager
def _exclusive(root, topic):
    """写路径互斥。读路径（check / diagnose）**不**进这里，保证只读。"""
    path = lock_path(root, topic)
    key = str(path)
    held = getattr(_LOCK_HELD, 'paths', frozenset())
    if key in held:
        yield path
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = _thread_lock(key)
    guard.acquire()
    try:
        _claim_lock(path)
        _LOCK_HELD.paths = held | {key}
        try:
            yield path
        finally:
            _LOCK_HELD.paths = held
            try:
                path.unlink()
            except OSError:
                pass
    finally:
        guard.release()


class _append_lock:
    """追加写的跨进程锁：锁在旁路 `.lock` 文件上，内容文件只追加、不重写。"""

    def __init__(self, path):
        self.path = Path(path).with_name(Path(path).name + '.lock')
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        self._acquire()
        return self

    def __exit__(self, *exc_info):
        try:
            self._release()
        finally:
            self.handle.close()
        return False

    def _acquire(self):
        if os.name == 'nt':
            import msvcrt
            if self.path.stat().st_size == 0:
                self.handle.write(b'0')
                self.handle.flush()
            while True:
                try:
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError:
                    time.sleep(0.01)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)

    def _release(self):
        if os.name == 'nt':
            import msvcrt
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _append_row(path, row):
    """追加一行 JSON。单次写入 + fsync；并发写由旁路锁串行化，不许出现半行。"""
    line = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _append_lock(path):
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())


# ---------------------------------------------------------------------------
# 来源：清单、字节版本、事件截止
# ---------------------------------------------------------------------------

#: 人读侧（`topics/<topic>/`）自动发现的来源模式：专题主文档、目标、用户要求、证据、事前计划与分支材料。
#: 页面 `STATUS.md` 是**产出**，不进来源指纹。
TOPIC_SOURCE_PATTERNS = (
    'TOPIC.md',
    'OBJECTIVE.md',
    'REQUIREMENTS.md',
    'EVIDENCE*',
    'NOTE*.md',
    'PLAN_*.json',
    'plans/**/*',
    'BRANCH*',
)

#: 机器侧（`.autoresearch/state/reporting/<topic>/`）自动发现的来源：事件与负责人输入、checkpoint。
MACHINE_SOURCE_PATTERNS = (
    'EVENTS.jsonl',
    'INPUTS.jsonl',
    'CHECKPOINT_*.json',
)

#: 人读侧固定来源表：JSON 字符串路径列表（`workspace.provision_topic` 生成）。
SOURCE_MANIFEST = 'sources.json'


def _extra_sources(root, topic):
    prepared = _load_json(sources_path(root, topic), {}) or {}
    spec = prepared.get('spec') or {}
    extras = spec.get('extra') or []
    if not isinstance(extras, list):
        raise ReportingError('SOURCES.json 的 spec.extra 不是列表；请重新 prepare')
    return [str(item) for item in extras]


def _files_under(directory, patterns):
    """按模式收集**文件**（目录不算）。`plans/**/*` 这类递归模式也在其中。"""
    found = []
    directory = Path(directory)
    if not directory.is_dir():
        return found
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file():
                found.append(path)
    return found


def _manifest_paths(root, topic):
    """读 `topics/<topic>/sources.json`：JSON 字符串路径列表，作为固定来源表。

    * 路径按工作区相对解析；也容忍相对专题目录的写法（先试工作区、再试专题目录）。
    * **越界一律拒**（`../` 或工作区外的绝对路径）；坏 JSON / 非字符串列表也拒，⛔ 不静默忽略。
    * 清单文件自身也进来源指纹（改清单 = 改来源）。
    """
    root = Path(root).resolve()
    path = topic_root(root, topic) / SOURCE_MANIFEST
    if not path.is_file():
        return [], None
    try:
        document = json.loads(path.read_text(encoding='utf-8-sig'))
    except ValueError as exc:
        raise ReportingError(f'{SOURCE_MANIFEST} 不是合法 JSON：{exc}')
    if not isinstance(document, list) or not all(isinstance(item, str) for item in document):
        raise ReportingError(f'{SOURCE_MANIFEST} 必须是 JSON 字符串路径列表')
    resolved, manifest = [], []
    for item in document:
        text = item.strip()
        if not text:
            raise ReportingError(f'{SOURCE_MANIFEST} 里有空路径')
        manifest.append(text)
        try:
            resolved.append(inside(root, text))
        except ReportingError:
            raise ReportingError(f'{SOURCE_MANIFEST} 里的路径越界（不在工作区内）：{text}')
        if not resolved[-1].is_file() and (topic_root(root, topic) / text).is_file():
            resolved[-1] = inside(root, (topic_root(root, topic) / text))
    return resolved, manifest


def source_snapshot(root, topic, *, extra_sources=None):
    """来源快照：逐文件**字节**哈希 + 聚合指纹。

    * 人读侧扫 `topics/<topic>/`：TOPIC / OBJECTIVE / REQUIREMENTS / EVIDENCE* / plans/ 与分支材料；
    * 机器侧扫 `.autoresearch/state/reporting/<topic>/`：EVENTS / INPUTS / CHECKPOINT；
    * 读 `topics/<topic>/sources.json` 作为**固定来源表**（清单自身也哈希）；
    * CLI 追加来源（`extra`）也算来源。

    ⚠️ 只用文件内容。mtime、心跳、页面自身都不参与指纹 ⇒ 同内容 touch 不作废回执。
    ⚠️ 声明过的追加来源清单（`extra`）也进指纹：改清单等于改来源，需要重新供料。
    """
    root = Path(root).resolve()
    extras = _extra_sources(root, topic) if extra_sources is None else list(extra_sources)
    manifest_paths, manifest = _manifest_paths(root, topic)
    candidates = list(_files_under(topic_root(root, topic), TOPIC_SOURCE_PATTERNS))
    candidates.extend(_files_under(machine_dir(root, topic), MACHINE_SOURCE_PATTERNS))
    manifest_file = topic_root(root, topic) / SOURCE_MANIFEST
    if manifest_file.is_file():
        candidates.append(manifest_file)   # 清单自身也是来源：改清单 = 改来源
    candidates.extend(manifest_paths)
    extra_paths = []
    for item in extras:
        path = inside(root, item)
        extra_paths.append(path)
        candidates.append(path)
    files, missing, seen = {}, [], set()
    for candidate in candidates:
        try:
            rel = relative(root, candidate)
        except ValueError:
            raise ReportingError(f'来源不在工作区内：{candidate}')
        if rel in seen:
            continue
        seen.add(rel)
        path = Path(candidate)
        if not path.is_file():
            if path in extra_paths:
                kind = 'extra'
            elif path in manifest_paths:
                kind = 'manifest'
            else:
                kind = 'expected'
            missing.append({'path': rel, 'kind': kind})
            continue
        data = path.read_bytes()
        files[rel] = {'sha256': _digest(data), 'bytes': len(data)}
    declared = sorted(str(item) for item in extras)
    return {
        'topic': topic,
        'files': files,
        'missing': missing,
        'extra': declared,
        'manifest': manifest,
        'fingerprint': _digest({'files': files, 'extra': declared}),
    }


def event_cutoff(root, topic):
    """事件截止：事件文件当时的字节与条数。回执绑定它，事后追加就会变。"""
    path = events_path(root, topic)
    data = path.read_bytes() if path.is_file() else b''
    rows = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    return {
        'count': len(rows),
        'last_event_id': rows[-1].get('event_id') if rows else None,
        'last_at': rows[-1].get('at') if rows else None,
        'file_sha256': _digest(data),
    }


def validate_source(root, source):
    """来源引用：要么是工作区内 `path + locator`，要么是宿主消息 `session_id + message_id`。

    ⚠️ `path` 型来源的哈希是按**当时字节**算的；它不是「内容正确」的证明，只证明引用到同一版。
    `session_id + message_id` 型来源一律标 declared：本模块核不了宿主消息。
    """
    if not isinstance(source, dict):
        raise ReportingError('来源必须是对象：{path, locator} 或 {session_id, message_id}')
    if source.get('session_id') and source.get('message_id'):
        return {
            'session_id': str(source['session_id']),
            'message_id': str(source['message_id']),
            'verification': DECLARED,
            'note': '引用由调用方提供；本模块未独立核验宿主消息',
        }
    if not source.get('path'):
        raise ReportingError('来源须含 path+locator，或 session_id+message_id')
    root = Path(root).resolve()
    path = inside(root, str(source['path']))
    if not path.is_file():
        raise ReportingError(f'来源文件不存在：{source["path"]}')
    if not str(source.get('locator') or '').strip():
        raise ReportingError('path 型来源必须给 locator（行号 / 节标题 / 消息序号 / 运行 ID）')
    data = path.read_bytes()
    return {
        'path': relative(root, path),
        'locator': str(source['locator']),
        'sha256': _digest(data),
        'bytes': len(data),
        'verification': 'hashed_at_invocation',
        'note': '哈希绑定当时字节；不判内容真假',
    }


# ---------------------------------------------------------------------------
# 事件与负责人输入（append-only）
# ---------------------------------------------------------------------------


def _event_rows(root, topic):
    return _rows(events_path(root, topic))


def _input_rows(root, topic):
    return _rows(inputs_path(root, topic))


def latest_event(root, topic):
    rows = _event_rows(root, topic)
    return rows[-1] if rows else None


def round_terminal(root, topic, exp=None):
    rows = [r for r in _event_rows(root, topic)
            if r.get('scope') == 'round' and r.get('kind') in TERMINAL_KINDS]
    if exp is not None:
        rows = [r for r in rows if r.get('exp') == exp]
    return rows[-1] if rows else None


def record_event(root, topic, exp, kind, event_id, source, *, scope='run', reason=None,
                 exit_code=None):
    """追加一条运行事件（run 或 round）。

    * `kind` 在 `EVENT_KINDS` 里；`scope` 只接受 run / round。
    * **终态**（completed / failed / stopped / superseded）必须写真实原因 `reason`：
      结束、失败、停跑、被替代都要留原因，⛔ 不许用「跑完了」占位。
    * 同一 `event_id` 重复提交：内容一致 ⇒ 幂等返回；内容不同 ⇒ 拒。
    * 单臂完成（scope=run）与 `partial` 都**不是**整轮结束；整轮交付看 `check_round()`。
    """
    root = Path(root).resolve()
    require_topic(topic)
    require_token(str(exp or ''), '实验号')
    if kind not in EVENT_KINDS:
        raise ReportingError(f'非法事件种类：{kind!r}；可用：{" / ".join(EVENT_KINDS)}')
    if scope not in SCOPES:
        raise ReportingError(f'非法 scope：{scope!r}；只接受 run / round')
    if not str(event_id or '').strip():
        raise ReportingError('事件必须有 event_id（同一 id 重复提交按幂等处理）')
    if kind in TERMINAL_KINDS and not str(reason or '').strip():
        raise ReportingError(f'{kind} 是终态事件，必须写真实原因 reason（为什么结束/失败/停跑/被替代）')
    payload_source = validate_source(root, source)
    with _exclusive(root, topic):
        for row in _event_rows(root, topic):
            if row.get('event_id') != event_id:
                continue
            stable = {key: row.get(key) for key in
                      ('topic', 'exp', 'kind', 'event_id', 'scope', 'reason', 'exit_code')}
            incoming = {'topic': topic, 'exp': exp, 'kind': kind, 'event_id': event_id,
                        'scope': scope, 'reason': reason, 'exit_code': exit_code}
            old_source = {k: v for k, v in (row.get('source') or {}).items()
                          if k not in ('sha256', 'bytes')}
            new_source = {k: v for k, v in payload_source.items() if k not in ('sha256', 'bytes')}
            if stable != incoming or old_source != new_source:
                raise ReportingError(f'event_id 重用但内容不同：{event_id}（事件是 append-only，'
                                     f'要改结论请换新 event_id）')
            return {'ok': True, 'idempotent': True, 'event': row,
                    'task': state_name((_load_json(task_path(root, topic), {}) or {}).get('state'))}
        row = {
            'topic': topic,
            'exp': exp,
            'kind': kind,
            'event_id': event_id,
            'scope': scope,
            'reason': str(reason or '').strip() or None,
            'exit_code': exit_code,
            'source': payload_source,
            'at': now_iso(),
        }
        _append_row(events_path(root, topic), row)
        task = request(root, topic)
        return {'ok': True, 'idempotent': False, 'event': row,
                'task': state_name(task.get('state')),
                'round_terminal': bool(round_terminal(root, topic, exp))}


def record_input(root, topic, kind, text, source, *, exp=None, input_id=None):
    """登记负责人的显式输入：用户要求，或整轮结束后的判断。

    * `user_requirement`：缺它就没法写第 1 节（不能只继承旧页）。
    * `leader_conclusion`：必须给 `exp`，且要晚于那一轮的最新事件。
    """
    root = Path(root).resolve()
    require_topic(topic)
    if kind not in INPUT_KINDS:
        raise ReportingError(f'非法输入种类：{kind!r}；可用：{" / ".join(INPUT_KINDS)}')
    if not str(text or '').strip():
        raise ReportingError('负责人输入必须有正文')
    if exp is not None:
        require_token(str(exp), '实验号')
    if kind == 'leader_conclusion' and not exp:
        raise ReportingError('负责人判断必须指定 exp（它是对哪一轮的结论）')
    payload_source = validate_source(root, source)
    with _exclusive(root, topic):
        events = _event_rows(root, topic)
        current_event = events[-1].get('event_id') if events and kind == 'leader_conclusion' else None
        row = {'kind': kind, 'text': text, 'source': payload_source, 'exp': exp}
        identifier = input_id or _digest([row, current_event])
        for old in _input_rows(root, topic):
            if old.get('input_id') != identifier:
                continue
            if any(old.get(key) != value for key, value in row.items()):
                raise ReportingError(f'input_id 重用但内容不同：{identifier}')
            return {'ok': True, 'idempotent': True, 'input': old}
        stored = dict(row, input_id=identifier, at=now_iso())
        _append_row(inputs_path(root, topic), stored)
        task = request(root, topic)
        return {'ok': True, 'idempotent': False, 'input': stored,
                'task': state_name(task.get('state')), 'missing': task.get('missing') or []}


# ---------------------------------------------------------------------------
# 交付任务与缺项
# ---------------------------------------------------------------------------


def request(root, topic, *, extra_sources=None):
    """按当前来源与事件算出交付任务。来源指纹没变就不重写（保住 attempts / 已验收状态）。"""
    root = Path(root).resolve()
    directory = topic_dir(root, topic)
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = source_snapshot(root, topic, extra_sources=extra_sources)
    old = _load_json(task_path(root, topic), {}) or {}
    if old.get('source_fingerprint') == snapshot['fingerprint']:
        return old
    events = _event_rows(root, topic)
    inputs = _input_rows(root, topic)
    latest = events[-1] if events else None
    conclusions = [row for row in inputs
                   if row.get('kind') == 'leader_conclusion'
                   and (not latest or (row.get('exp') == latest.get('exp')
                                       and str(row.get('at') or '') >= str(latest.get('at') or '')))]
    missing = []
    if not any(row.get('kind') == 'user_requirement' for row in inputs):
        missing.append('缺显式用户要求来源（不能只继承旧页）')
    if latest and not conclusions:
        missing.append(f'待负责人分析最新运行事件 {latest.get("event_id")}（登记 leader_conclusion）')
    for item in snapshot['missing']:
        if item.get('kind') in ('extra', 'manifest'):
            missing.append(f'来源清单里的文件不存在：{item["path"]}')
    cutoff = event_cutoff(root, topic)
    task = {
        'schema': SCHEMA,
        'topic': topic,
        'task_id': _digest([topic, snapshot['fingerprint']]),
        'source_fingerprint': snapshot['fingerprint'],
        'sources': snapshot['files'],
        'event_cutoff': cutoff,
        'state': 'pending_owner' if missing else 'pending_writer',
        'missing': missing,
        'exp': latest.get('exp') if latest else None,
        'latest_event': latest,
        'writer_call_required': True,
        'attempts': 0,
        'created_at': now_iso(),
        'instruction': '负责人补齐来源后，必须由实际派出的 scribe 子会话写页；'
                       '负责人不能自己写。本工具不启动宿主子会话，也不发消息。',
    }
    _save_json(task_path(root, topic), task)
    return task


def missing_items(root, topic, snapshot=None, task=None):
    """缺项与补读。`blocker` 会挡住验收（`pending_owner`），`info` 只提醒。"""
    root = Path(root).resolve()
    snapshot = snapshot or source_snapshot(root, topic)
    task = task if task is not None else (_load_json(task_path(root, topic), {}) or {})
    events = _event_rows(root, topic)
    inputs = _input_rows(root, topic)
    files = snapshot['files']
    items = []
    if not page_path(root, topic).is_file():
        items.append({'item': '旧页面', 'level': 'info',
                      'why': '还没有页面，这是首版',
                      'read': '不用补读；第 1 节可直接写'})
    if not any(row.get('kind') == 'user_requirement' for row in inputs):
        items.append({'item': '用户要求来源', 'level': 'blocker',
                      'why': '缺显式用户要求来源；不能只继承旧页（第 1 节要写给谁看、要什么）',
                      'read': 'python research.py reporting input --topic ' + topic
                              + ' --kind user_requirement --text "<用户原话>"'
                              + ' --source <工作区内相对路径> --locator <行号/消息序号>'})
    latest = events[-1] if events else None
    if latest and not any(row.get('kind') == 'leader_conclusion' and row.get('exp') == latest.get('exp')
                          and str(row.get('at') or '') >= str(latest.get('at') or '')
                          for row in inputs):
        items.append({'item': f'最新运行事件 {latest.get("event_id")} 的负责人判断', 'level': 'blocker',
                      'why': '最近一次运行还没有负责人结论；页面不许替负责人宣布结论',
                      'read': 'python research.py reporting input --topic ' + topic
                              + f' --kind leader_conclusion --exp {latest.get("exp")}'
                              + ' --text "<负责人结论>" --source <路径> --locator <行号>'})
    if not any(rel.endswith('TOPIC.md') for rel in files):
        items.append({'item': '专题主文档', 'level': 'info',
                      'why': '缺 TOPIC.md：研究问题、假设与边界没有落点',
                      'read': f'补读/补齐：{TOPICS_DIR}/{topic}/TOPIC.md'
                              '（`workspace.provision_topic` 会生成）'})
    if not any('EVIDENCE' in rel for rel in files):
        items.append({'item': '证据正文', 'level': 'info',
                      'why': '还没有证据文件；第 3 节只能写「暂无实验，未起跑」',
                      'read': f'补读/补齐：{TOPICS_DIR}/{topic}/EVIDENCE_<日期>.md'})
    if not any(('PLAN_' in rel or '/plans/' in rel) for rel in files):
        items.append({'item': '事前设计（设置 / 分支）', 'level': 'info',
                      'why': '缺 plan：第 3 节的「设置」与「结果分支」没有来源',
                      'read': f'补读/补齐：{TOPICS_DIR}/{topic}/plans/<实验号>.json'})
    if not (topic_root(root, topic) / SOURCE_MANIFEST).is_file():
        items.append({'item': '来源清单 sources.json', 'level': 'info',
                      'why': '缺固定来源表；自动扫描仍生效，但清单里的额外来源不会被纳入',
                      'read': f'补读/补齐：{TOPICS_DIR}/{topic}/{SOURCE_MANIFEST}'
                              '（JSON 字符串路径列表，工作区相对）'})
    if not events:
        items.append({'item': '运行事件', 'level': 'info',
                      'why': '还没有运行事件；第 3 节写「暂无实验，未起跑」及原因',
                      'read': 'python research.py reporting event --topic ' + topic
                              + ' --exp <实验号> --kind running --event-id <ID>'
                              + ' --source <路径> --locator <行号>'})
    checkpoint = latest_checkpoint(root, topic)
    if checkpoint['stale']:
        items.append({'item': 'checkpoint 时效', 'level': 'info',
                      'why': checkpoint['why'],
                      'read': f'补读/补齐：{REPORTING_DIR}/{topic}/CHECKPOINT_<时间>.json；'
                              f'过期只能写「状态待核」'})
    for item in snapshot['missing']:
        if item.get('kind') in ('extra', 'manifest'):
            items.append({'item': f'来源清单缺文件：{item["path"]}', 'level': 'blocker',
                          'why': '显式声明的来源不存在；供料不完整',
                          'read': f'补读/补齐：{item["path"]}（或从 --source / sources.json '
                                  f'里去掉它并重新 prepare）'})
    # 任务里的缺项已经由上面逐项判过；只补这里没有覆盖到的意外缺项，避免重复条目。
    covered = ('缺显式用户要求来源', '待负责人分析最新运行事件', '来源清单里的文件不存在')
    for message in task.get('missing') or []:
        if message.startswith(covered):
            continue
        if any(message == entry['why'] for entry in items):
            continue
        items.append({'item': message, 'level': 'blocker', 'why': message,
                      'read': '按 prepare 输出的 missing 补齐后重新 prepare'})
    return items


# ---------------------------------------------------------------------------
# 供料包
# ---------------------------------------------------------------------------


def _page_kinds(text):
    legacy = any(marker in text for marker in _LEGACY_ONLY_MARKERS) and '下一步研究' not in text
    research = all(name in text for name in ('你的要求', '一句话现状', '最近实验', '下一步研究'))
    return legacy, research


def legacy_page(text):
    return _page_kinds(text or '')[0]


def research_page(text):
    return _page_kinds(text or '')[1]


def page_problems(text, *, legacy=None):
    """研究正文验收：直接复用 `governance.validate_status_page`（八节、非空、四段、来源、≤3 轮）。"""
    if legacy is None:
        legacy = legacy_page(text)
    return list(validate_status_page(text or '', legacy=bool(legacy)))


def separation_warnings(text):
    """研究页里出现流程/心跳字样只是警告：流程状态该落 `process_status/`，且不算研究进展。"""
    found = [marker for marker in FLOW_MARKERS if marker in (text or '')]
    if not found:
        return []
    return ['研究页里出现流程字样（' + '、'.join(found) + '）：'
            '流程状态应落 .autoresearch/state/process_status/，心跳不算研究进展（旧页只读兼容，不追溯改写）']


def _source_text(path):
    data = Path(path).read_bytes()
    try:
        return data.decode('utf-8-sig'), None
    except UnicodeDecodeError:
        return None, '该来源不是 UTF-8 文本，未内联；请按路径读原始字节'


def build_feed(root, topic, snapshot, task, *, inline_limit=INLINE_LIMIT_BYTES):
    """供料包正文：来源清单完整、正文按上限显式内联、旧页全文、缺项写明补读。

    ⛔ 不静默截断：超过 `inline_limit` 的来源显式标「未内联」并给补读路径；
    旧页面**永远**全文附带，不受内联上限影响。
    """
    root = Path(root).resolve()
    if not isinstance(inline_limit, int) or isinstance(inline_limit, bool) or inline_limit < 0:
        raise ReportingError('inline_limit 必须是 ≥0 的整数')
    files = snapshot['files']
    page = page_path(root, topic)
    lines = [
        f'# 研究现状页供料包 · {topic}',
        '',
        '本包由 reporting 生成，供 scribe 会话组织研究现状页正文。',
        '⛔ 生成供料不等于真实派工：本包不启动任何子会话，也不发消息；'
        '回执默认只记「调用方自述」（declared）。',
        '⛔ 页面只写研究内容：目标 / 结果 / 方向 + 研究判断。'
        '流程状态（心跳、租约、checkpoint 时效、审计额度、登记）不进正文。',
        '',
        '保留 8 节：① 你的要求 ② 一句话现状（研究问题）③ 最近实验 '
        '④ 已定 / 已否 / 待定 ⑤ 本轮改了什么 ⑥ 这轮结论依赖的未验证前提 '
        '⑦ 证据在哪 ⑧ 下一步研究（只写未启动动作）。',
        '',
        '第 3 节每个实验用三级标题，按「假设 → 判据 → 设置 → 结果」写，每块附来源。'
        '判据写观察与解释边界；探索性实验可以不设通过门槛，确认性实验保留有依据的门槛（写清出处）。'
        f'第 3 节**总量最多 {MAX_REPORTED_ROUNDS} 轮（含当前实验）**。'
        '没有实验就写「暂无实验，未起跑」及原因。',
        '',
        f'## 1 来源清单（完整，共 {len(files)} 个）',
        '',
    ]
    total = sum(meta.get('bytes') or 0 for meta in files.values())
    inlined, skipped = [], []
    for rel in sorted(files):
        if (files[rel].get('bytes') or 0) <= inline_limit:
            inlined.append(rel)
        else:
            skipped.append(rel)
    lines.append(f'合计 {total} 字节；内联 {len(inlined)} 个，未内联 {len(skipped)} 个'
                 f'（每个未内联项都写明补读路径；本包不截断任何正文）。')
    lines.append('')
    lines.append('| # | 来源 | 字节 | sha256 | 内联 |')
    lines.append('|---|---|---|---|---|')
    for index, rel in enumerate(sorted(files), 1):
        meta = files[rel]
        mark = '是' if rel in inlined else '否（见下）'
        lines.append(f'| {index} | `{rel}` | {meta.get("bytes")} | `{meta.get("sha256")}` | {mark} |')
    lines.append('')
    lines += ['## 2 缺项与补读', '']
    items = missing_items(root, topic, snapshot, task)
    if not items:
        lines.append('本次供料没有发现缺项。')
    else:
        for item in items:
            lines.append(f'- [{item["level"]}] {item["item"]}：{item["why"]}')
            lines.append(f'  - 补读 / 补齐：{item["read"]}')
    lines += ['', '## 3 逐来源正文（完整内容，不截断）', '']
    if not files:
        lines.append('（还没有来源。先补 TOPIC.md / 证据 / plan，再重新 prepare。）')
    for index, rel in enumerate(sorted(files), 1):
        lines.append(f'### 来源 {index} · `{rel}`')
        if rel in skipped:
            lines.append(f'未内联：该来源 {files[rel].get("bytes")} 字节，超过内联上限 {inline_limit} 字节。'
                         f'补读路径：`{rel}`（必须整份读，不许只看摘要）。')
            lines.append('')
            continue
        text, problem = _source_text(root / rel)
        if problem:
            lines.append(problem + f' 补读路径：`{rel}`')
            lines.append('')
            continue
        lines.append(f'--- 来源正文开始：`{rel}` ---')
        lines.append(text.rstrip('\n'))
        lines.append(f'--- 来源正文结束：`{rel}` ---')
        lines.append('')
    lines += ['## 4 运行事件（全量，append-only 原始记录）', '']
    events = _event_rows(root, topic)
    if events:
        lines.append(json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        lines.append('（还没有运行事件；第 3 节写「暂无实验，未起跑」及原因。）')
    lines += ['', '## 5 负责人输入（全量，append-only 原始记录）', '']
    inputs = _input_rows(root, topic)
    if inputs:
        lines.append(json.dumps(inputs, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        lines.append('（还没有负责人输入；显式用户要求来源缺失时验收会被拒。）')
    lines += ['', '## 6 旧页面（全文，不截断；不是唯一事实源）', '']
    if page.is_file():
        old_text, problem = _source_text(page)
        if problem:
            lines.append(problem + f' 补读路径：`{page_rel(topic)}`')
        else:
            lines.append(f'本页读到 {page.stat().st_size} 字节，原样附在下面。'
                         '旧流程字段只读兼容，⛔ 不要带进新页。')
            lines.append('--- 旧页面开始 ---')
            lines.append(old_text.rstrip('\n'))
            lines.append('--- 旧页面结束 ---')
    else:
        lines.append('（还没有页面；这是首版。）')
    lines += [
        '',
        '## 7 验收（写完再交）',
        '',
        '- 8 节都在，每节都有研究内容；空节或只有模板说明不收。',
        f'- 第 3 节每块按 假设 → 判据 → 设置 → 结果 写，并标来源；总量最多 {MAX_REPORTED_ROUNDS} 轮（含当前实验）。',
        '- 没有实验写「暂无实验，未起跑」及原因；⛔ 不把「没看到差别」写成「一样好」。',
        '- 第 8 节只写未启动的研究动作；⛔ 不写审计额度、审批、登记、等人解决这类流程。',
        '- ⛔ 新页不写旧流程字段（拍板 / 正文覆盖版本 / 当前阶段 / 十一格 / 自推进心跳）；旧页只读兼容。',
        f'- 写完由 scribe 会话调用：`{ack_command(topic)}`',
        '- ⚠️ 回执默认只记调用方自述（declared）；真实派工要注入宿主验证器，'
        '真实送达要另取通道回执。',
    ]
    return '\n'.join(lines) + '\n'


def prepare(root, topic, *, extra_sources=None, inline_limit=INLINE_LIMIT_BYTES):
    """生成完整供料包并登记来源快照。返回稳定协议（`prepared_id` / `dispatch` / `missing`）。

    人读页默认落 `topics/<topic>/STATUS.md`（与专题主文档同目录）；写页前先建它的父目录，
    这样 `provision_topic` 之外的临时专题也能由 scribe 直接写页，⛔ 不必把研究正文拷进隐藏状态目录。
    """
    root = workspace_root(root)
    require_topic(topic)
    topic_root(root, topic).mkdir(parents=True, exist_ok=True)   # 人读页父目录（写页前就位）
    with _exclusive(root, topic):
        snapshot = source_snapshot(root, topic, extra_sources=extra_sources)
        task = request(root, topic, extra_sources=snapshot['extra'])
        text = build_feed(root, topic, snapshot, task, inline_limit=inline_limit)
        after = source_snapshot(root, topic, extra_sources=snapshot['extra'])
        if after['files'] != snapshot['files']:
            raise ReportingError('供料期间来源变化，请重试')
        feed = feed_path(root, topic)
        if not feed.is_file() or feed.read_text(encoding='utf-8') != text:
            _atomic_write(feed, text)
        feed_sha = _digest(feed.read_bytes())
        cutoff = event_cutoff(root, topic)
        prepared_id = _digest([topic, snapshot['fingerprint'], feed_sha, cutoff])
        items = missing_items(root, topic, snapshot, task)
        page = page_path(root, topic)
        page_sha = _digest(page.read_bytes()) if page.is_file() else None
        record = {
            'schema': SCHEMA,
            'topic': topic,
            'spec': {'extra': list(snapshot['extra']), 'inline_limit': inline_limit},
            'files': snapshot['files'],
            'fingerprint': snapshot['fingerprint'],
            'event_cutoff': cutoff,
            'prepared_id': prepared_id,
            'feed': {'path': feed_rel(topic), 'bytes': len(text.encode('utf-8')), 'sha256': feed_sha},
            'page': {'path': page_rel(topic), 'exists': page.is_file(), 'sha256': page_sha},
            'prepared_at': now_iso(),
        }
        if _load_json(sources_path(root, topic), None) != record:
            _save_json(sources_path(root, topic), record)
        return {
            'schema': SCHEMA,
            'ok': True,
            'topic': topic,
            'prepared_id': prepared_id,
            'source_fingerprint': snapshot['fingerprint'],
            'sources': {
                'count': len(snapshot['files']),
                'total_bytes': sum(meta.get('bytes') or 0 for meta in snapshot['files'].values()),
                'fingerprint': snapshot['fingerprint'],
                'files': snapshot['files'],
                'not_inlined': [rel for rel, meta in sorted(snapshot['files'].items())
                                if (meta.get('bytes') or 0) > inline_limit],
            },
            'event_cutoff': cutoff,
            'feed': record['feed'],
            'page': record['page'],
            'task': {'task_id': task.get('task_id'), 'state': state_name(task.get('state')),
                     'missing': task.get('missing') or []},
            'missing': items,
            'verification': DECLARED,
            'ack_command': ack_command(topic),
            'dispatch': {
                'prepared_id': prepared_id,
                'page_path': page_rel(topic),
                'page_sha256': page_sha,
                'must_mention': ['prepared_id', 'page_path'],
            },
        }


# ---------------------------------------------------------------------------
# 只读检查
# ---------------------------------------------------------------------------


def check(root, topic, *, required=False):
    """只读检查：页面结构、来源版本、回执一致性与写页角色。

    ⛔ 不写任何文件、不建目录、不加锁、不查 mtime、不看心跳。
    `required=True` 时不接受「历史页未接入」这条豁免（整轮交付用）。
    """
    root = Path(root).resolve()
    require_topic(topic)
    if not topic_exists(root, topic):
        return {
            'ok': not required,
            'problems': [] if not required else [f'专题目录不存在：{topic_rel(topic)}'],
            'warnings': [],
            'state': '未开始',
            'legacy_exempt': not required,
            'note': '还没有专题目录；未开始不代表已验收',
            'source_fingerprint': None,
            'task_state': None,
            'page': {'exists': False, 'body_sha256': None, 'legacy': False},
            'receipt': None,
        }
    receipt = _load_json(receipt_path(root, topic), None)
    task = _load_json(task_path(root, topic), None)
    page = page_path(root, topic)
    text = page.read_text(encoding='utf-8-sig') if page.is_file() else ''
    legacy = bool(text) and legacy_page(text)
    snapshot = source_snapshot(root, topic)
    problems, warnings = [], []
    if not receipt and not task and not required and (not text or legacy) and not research_page(text):
        return {
            'ok': True,
            'problems': [],
            'warnings': [],
            'state': '历史未接入' if text else '未供料',
            'legacy_exempt': True,
            'note': '历史页面未接入来源回执；不代表正文已验证（旧页只读兼容）',
            'source_fingerprint': snapshot['fingerprint'],
            'task_state': None,
            'page': {'exists': page.is_file(),
                     'body_sha256': _digest(page.read_bytes()) if page.is_file() else None,
                     'legacy': legacy},
            'receipt': None,
        }
    if task and state_name(task.get('state')) != 'accepted':
        problems.append(f'交付任务尚未完成：{state_name(task.get("state"))}；'
                        f'{task.get("last_error") or task.get("missing") or ""}')
    if not receipt:
        problems.append('缺正文来源验收回执；先 prepare 供料，再由派出的 scribe 子会话写页'
                        '（负责人不能自己写），最后 ack')
    else:
        if receipt.get('source_fingerprint') != snapshot['fingerprint']:
            was = (receipt.get('sources') or {})
            now = snapshot['files']
            changed = [rel for rel in sorted(set(was) | set(now)) if was.get(rel) != now.get(rel)]
            problems.append('正文未整合最新来源（新增或变化：'
                            + ('、'.join(changed[:8]) if changed else '来源指纹变化') + '）')
        body = _digest(page.read_bytes()) if page.is_file() else None
        if not page.is_file():
            problems.append(f'缺研究页正文：{page_rel(topic)}')
        elif receipt.get('body_sha256') != body:
            problems.append('正文与验收回执不一致（按字节比对：文件被改过，或回执不是这一版）')
        if task and receipt.get('task_id') != task.get('task_id'):
            problems.append('回执属于旧任务（任务号不一致，请重新 prepare 并重新 ack）')
        role = str(receipt.get('writer_role') or '')
        if role != WRITER_ROLE:
            problems.append('回执的写页角色是 '
                            + (role or '【未记录，旧回执】')
                            + f'，不是 {WRITER_ROLE} ⇒ 页面必须由实际派出的 scribe 会话写并重新 ack')
        if not str(receipt.get('writer_session') or '').strip():
            problems.append('回执没有写页会话（writer_session 为空）⇒ 不能证明是谁写的')
        if receipt.get('verification') != VERIFIED:
            warnings.append('回执只是调用方自述（declared）：真实派工与真实送达未被核验')
    if page.is_file():
        problems.extend(page_problems(text))
        warnings.extend(separation_warnings(text))
    elif not required and not problems:
        problems.append(f'缺研究页正文：{page_rel(topic)}')
    return {
        'ok': not problems,
        'problems': problems,
        'warnings': warnings,
        'state': '已验收' if (receipt and not problems) else '待更新',
        'legacy_exempt': False,
        'source_fingerprint': snapshot['fingerprint'],
        'task_state': state_name((task or {}).get('state')),
        'page': {'exists': page.is_file(),
                 'body_sha256': _digest(page.read_bytes()) if page.is_file() else None,
                 'legacy': legacy},
        'receipt': None if not receipt else {
            'writer_role': receipt.get('writer_role'),
            'writer_session': receipt.get('writer_session'),
            'writer_session_source': receipt.get('writer_session_source'),
            'verification': receipt.get('verification'),
            'session_verified': receipt.get('session_verified'),
            'dispatch_verified': receipt.get('dispatch_verified'),
            'delivery_verified': receipt.get('delivery_verified'),
            'proof_verified_by': receipt.get('proof_verified_by'),
            'content_bound': receipt.get('content_bound'),
            'version': receipt.get('version'),
            'prepared_id': receipt.get('prepared_id'),
            'accepted_at': receipt.get('accepted_at'),
            'event_cutoff': receipt.get('event_cutoff'),
        },
    }


# ---------------------------------------------------------------------------
# 验收回执
# ---------------------------------------------------------------------------


def _path_matches(value, root, expected):
    if not value:
        return False
    expected = Path(expected).resolve()
    candidate = Path(str(value))
    if candidate.is_absolute():
        return candidate.resolve() == expected
    return (Path(root) / candidate).resolve() == expected


def _session_key(value):
    """会话标识归一：宿主会补 `session-` 前缀，比较时忽略它。"""
    return str(value or '').strip().removeprefix('session-')


def _normalize_proof(proof, session):
    """`proof = {session_id, seq}`：指向 scribe 会话里**可见的那一份回传**。

    ⛔ `seq` 不许留空、不许用调用方自选的日志路径替代：那等于让被验方自己挑证据。
    """
    if proof is None:
        return None
    if not isinstance(proof, dict):
        raise ReportingError('proof 必须是对象 {session_id, seq}')
    proof_session = str(proof.get('session_id') or '').strip()
    if _session_key(proof_session) != _session_key(session):
        raise ReportingError(f'proof.session_id={proof_session!r} 与声明的写页会话 {session!r} 不一致')
    seq = proof.get('seq')
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ReportingError('proof.seq 必须是 ≥0 的整数（scribe 可见回传的序号）；'
                             '⛔ 留空或给路径都不算真实证据')
    return {'session_id': session, 'seq': seq}


def _call_verifier(verifier, payload):
    """宿主验证器的调用约定（四种写法都能直插，按下面顺序判定）：

    1. 对象带 `verify` 方法（例如 `DSHHost` 实例）⇒ `verifier.verify('scribe', payload)`；
    2. 可调用且能接两个位置参数（例如 `dsh.verify` 绑方法、`verify(role, payload)`）⇒ 传两个；
    3. 可调用且只接一个参数 ⇒ `verifier(payload)`（例如 `functools.partial(dsh.verify,'scribe')`）；
    4. 其余 ⇒ 抛错，不猜。

    ⚠️ 第 2 条是给 `host_verifier=DSHHost(...).verify` 用的：绑方法的签名仍是 `(role, payload)`，
    所以不能按单参数函数调。
    """
    if hasattr(verifier, 'verify') and callable(getattr(verifier, 'verify')):
        return verifier.verify(WRITER_ROLE, payload)
    if not callable(verifier):
        raise ReportingError('host_verifier 必须是可调用对象，或带 verify(role, payload) 的宿主对象')
    try:
        signature = inspect.signature(verifier)
    except (TypeError, ValueError):
        return verifier(payload)
    try:
        signature.bind(WRITER_ROLE, payload)
    except TypeError:
        pass
    else:
        return verifier(WRITER_ROLE, payload)
    return verifier(payload)


def _validate_host_evidence(evidence, *, root, topic, writer_session, prepared_id, page_sha256,
                            proof):
    """校验宿主证据的形状与绑定值，返回 `{proof_verified_by, content_bound}`。

    要求（全部满足才算 verified，缺一条就拒）：

    1. **能定位到那一份可见回传**：要么回显 `proof{session_id, seq}` 且逐键相等；
       要么给宿主派工回执标识 `tool_call_id` 与证据来源 `source`（宿主自己解析 proof 的形态）。
       ⛔ 两者都没有 ⇒ 拒：那说明没核到「scribe 回没回话」。
    2. `session_id` 等于声明的写页会话。
    3. `page_sha256` 等于磁盘页面当前字节哈希（证据之后页面又被改 ⇒ 拒）。
    4. `tool_calls` 里至少一条写入类调用成功、目标是本页、会话一致。
       若某条调用带 `content_sha256`，它必须等于页面哈希（那是宿主从工具 arguments 算的）；
       **只有 edit 片段、没有整页内容的调用不算**（宿主会拒，这里也不接受边缘情形）。
    5. 有父子/派工谱系证据。
    6. 若给 `prepared_id` / `page_path`，必须与本次一致。

    ⚠️ 强度如实：本函数核的是**形状与绑定值**，核不了验证器内部有没有真的去读宿主日志。
    可信性来自「验证器由部署注入」，回执里 `host_evidence` 原样落盘以便复核。
    会话标识比较忽略 `session-` 前缀（宿主返回的通常是补过前缀的规范值）。
    """
    if not isinstance(evidence, dict):
        raise ReportingError('宿主验证器必须返回字典证据')
    page = page_path(root, topic)
    problems = []
    proof_verified_by = None
    echoed = evidence.get('proof')
    if echoed is not None:
        if not isinstance(echoed, dict) \
                or _session_key(echoed.get('session_id')) != _session_key(proof['session_id']) \
                or echoed.get('seq') != proof['seq']:
            problems.append('证据里的 proof 与传入的 proof 不一致（没有核到那一份可见回传）')
        else:
            proof_verified_by = 'proof_echo'
    elif str(evidence.get('tool_call_id') or '').strip() and str(evidence.get('source') or '').strip():
        proof_verified_by = 'host_dispatch_receipt'
    else:
        problems.append('证据既没有回显 proof，也没有宿主派工回执标识（tool_call_id + source）'
                        '⇒ 不能证明核过那一份可见回传')
    if _session_key(evidence.get('session_id')) != _session_key(writer_session):
        problems.append(f'证据里的会话 {evidence.get("session_id")!r} ≠ 声明的写页会话 {writer_session!r}')
    if str(evidence.get('page_sha256') or '') != page_sha256:
        problems.append('证据里的 page_sha256 与磁盘页面字节不一致（页面在证据之后被改过？）')
    if not any(evidence.get(key) for key in ('spawned_by', 'parent_session', 'owner_session', 'spawned')):
        problems.append('缺派工/父子谱系证据（spawned_by / parent_session / owner_session / spawned 之一）')
    content_bound = None
    calls = evidence.get('tool_calls')
    if not isinstance(calls, list) or not calls:
        problems.append('缺 tool_calls：必须给出实际 write/edit 工具调用，'
                        '⛔ 不接受「assistant 最后一条消息等于整页正文」这种要求')
    else:
        usable, hashed = [], []
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool = str(call.get('tool') or '')
            if tool not in WRITE_TOOLS or call.get('ok') is not True:
                continue
            if not _path_matches(call.get('path'), root, page):
                continue
            if call.get('session_id') and _session_key(call['session_id']) != _session_key(writer_session):
                continue
            declared_content = str(call.get('content_sha256') or '')
            if declared_content:
                # 带了内容哈希就必须等于页面字节，否则那是另一次写的内容，不能拿来充数。
                if declared_content != page_sha256:
                    continue
                hashed.append(call)
            usable.append(call)
        if not usable:
            problems.append('tool_calls 里没有一条成功的写入类调用（tool ∈ '
                            + ' / '.join(WRITE_TOOLS) + '，path 指向本页，ok=true）')
        else:
            content_bound = 'argument_sha256' if hashed else 'host_replay'
    if evidence.get('prepared_id') and str(evidence['prepared_id']) != prepared_id:
        problems.append('证据里的 prepared_id 与本次供料不一致（派工用的不是这一版供料）')
    if evidence.get('page_path') and not _path_matches(evidence['page_path'], root, page):
        problems.append('证据里的 page_path 不是本页')
    if problems:
        raise ReportingError('宿主证据不能支持 verified：' + '；'.join(problems), problems)
    try:
        _canonical(evidence)
    except (TypeError, ValueError):
        raise ReportingError('宿主证据必须是 JSON 可序列化数据（回执要原样落盘）')
    return {'proof_verified_by': proof_verified_by, 'content_bound': content_bound}


def host_payload(root, topic, *, writer_session, proof=None, owner_session=None, report=None,
                 writer_role=WRITER_ROLE):
    """交给宿主 `verify('scribe', payload)` 的稳定载荷。页面哈希取**当前磁盘字节**。

    `workspace` 是工作区绝对路径：宿主应拿自己初始化的可信工作区核对它，⛔ 不认调用方自选日志路径。
    `proof` 缺省时 `seq=null` 并标 `declared_only=true`：那份载荷只能当自述，不能当真实证据。
    """
    root = Path(root).resolve()
    session = str(writer_session or '').strip()
    if not session:
        raise ReportingError('host_payload 需要写页会话 ID')
    prepared = _load_json(sources_path(root, topic), {}) or {}
    page = page_path(root, topic)
    if not page.is_file():
        raise ReportingError(f'缺研究页正文：{page_rel(topic)}；先让 scribe 写页再核验')
    snapshot = source_snapshot(root, topic)
    if prepared.get('fingerprint') != snapshot['fingerprint']:
        raise ReportingError('供料后来源已变化（写页用的来源 ≠ 现在的来源）；先重新 prepare')
    normalized = _normalize_proof(proof, session)
    return {
        'role': writer_role,
        'proof': normalized or {'session_id': session, 'seq': None},
        'declared_only': normalized is None,
        'owner_session': owner_session,
        'prepared_id': prepared.get('prepared_id'),
        'workspace': str(root),
        'page_path': page_rel(topic),
        'page_abs': str(page),
        'page_sha256': _digest(page.read_bytes()),
        'writer_session': session,
        'feed_sha256': (prepared.get('feed') or {}).get('sha256'),
        'source_fingerprint': snapshot['fingerprint'],
        'event_cutoff': prepared.get('event_cutoff') or event_cutoff(root, topic),
        'report': report,
    }


def acknowledge(root, topic, writer_session=None, *, writer_role=None, host_verifier=None,
                proof=None, owner_session=None, report=None):
    """验收研究正文并写回执。

    * `writer_role` **必须**是 `scribe`；负责人自写、或不声明角色，一律拒。
    * `writer_session` 必须非空。
    * 正文结构复用 `governance.validate_status_page`（八节、四段顺序、来源、≤3 轮）。
    * 供料后来源变过 ⇒ 拒；来源变过但正文没改 ⇒ 拒（「来源已变化但正文未变」）。
    * 注入 `host_verifier` 时**必须**同时给 `proof={session_id, seq}`（scribe 可见回传的序号），
      证据齐全 ⇒ `verified`；否则只能是 `declared`，回执里明写
      「自述不证明真实派工或真实送达」。
    """
    root = Path(root).resolve()
    require_topic(topic)
    if writer_role != WRITER_ROLE:
        raise ReportingError('写页必须由实际派出的 scribe 会话完成；'
                             f'收到 writer_role={writer_role!r}。负责人不能自己写页，'
                             '不给角色也不验收（--writer-role scribe）')
    session = str(writer_session or '').strip()
    if not session:
        raise ReportingError('必须给出实际写页会话 ID（--writer-session / writer_session）；'
                             '空值不能验收，回执要记清是谁写的')
    with _exclusive(root, topic):
        task = _load_json(task_path(root, topic), {}) or {}
        prepared = _load_json(sources_path(root, topic), {}) or {}
        if not task or not prepared:
            raise ReportingError('还没有供料包与交付任务：先 prepare 供料，再让 scribe 写页；'
                                 f'命令示例：python research.py reporting prepare --topic {topic}')
        snapshot = source_snapshot(root, topic)
        if prepared.get('fingerprint') != snapshot['fingerprint']:
            was, now = prepared.get('files') or {}, snapshot['files']
            changed = [rel for rel in sorted(set(was) | set(now)) if was.get(rel) != now.get(rel)]
            raise ReportingError(
                '供料后来源已变化（写页用的来源 ≠ 现在的来源）；先重新 prepare，'
                '再让 scribe 按新包复核/改页。变化：' + ('、'.join(changed[:8]) or '指纹变化'))
        if task.get('source_fingerprint') != snapshot['fingerprint']:
            raise ReportingError('任务记录的来源指纹与现在不一致；请重新 prepare')
        if task.get('missing'):
            raise ReportingError('负责人输入还缺：' + '；'.join(task['missing'])
                                 + '。缺项时 --ack 一定失败（正文结构可能全绿）')
        page = page_path(root, topic)
        if not page.is_file():
            raise ReportingError(f'缺研究页正文：{page_rel(topic)}')
        text = page.read_text(encoding='utf-8-sig')
        problems = page_problems(text)
        if problems:
            raise ReportingError('研究正文不合规：' + '；'.join(problems), problems)
        feed = prepared.get('feed') or {}
        feed_file = feed_path(root, topic)
        if not feed_file.is_file() or _digest(feed_file.read_bytes()) != feed.get('sha256'):
            raise ReportingError('供料包缺失或被改动；请重新 prepare（页面必须按同一版供料写）')
        body_sha = _digest(page.read_bytes())
        old = _load_json(receipt_path(root, topic), {}) or {}
        if old.get('source_fingerprint') != snapshot['fingerprint'] \
                and old.get('body_sha256') == body_sha:
            raise ReportingError('来源已变化但正文未变；请写页的人实际整合新增来源后重试'
                                 '（只读旧正文不能验收）')
        page_sha = body_sha
        normalized = _normalize_proof(proof, session)
        payload = None
        verification, session_source = DECLARED, 'caller_declaration'
        dispatch_verified, delivery_verified = False, False
        proof_verified_by = content_bound = None
        host_evidence, declaration_note = None, None
        if host_verifier is not None:
            if normalized is None:
                raise ReportingError(
                    '真实派工核验必须给出 proof={session_id, seq}（scribe 可见回传的序号）；'
                    '⛔ seq 不许留空，也不接受调用方自选日志路径。没有 proof 就只能走 declared')
            payload = host_payload(root, topic, writer_session=session, proof=normalized,
                                   owner_session=owner_session, report=report)
            # ⚠️ 验证器抛错 ⇒ ack 直接失败，不落回执（撕裂/歧义证据不能变成一张收据）。
            #     想要 declared 回执，就不传 host_verifier，由调用方显式选择。
            evidence = _call_verifier(host_verifier, payload)
            if not isinstance(evidence, dict):
                raise ReportingError('宿主验证器必须返回字典证据')
            if evidence.get('verified') is True:
                bound = _validate_host_evidence(
                    evidence, root=root, topic=topic, writer_session=session,
                    prepared_id=prepared.get('prepared_id'), page_sha256=page_sha,
                    proof=normalized)
                verification, session_source = VERIFIED, 'host_verifier'
                proof_verified_by = bound['proof_verified_by']
                content_bound = bound['content_bound']
                host_evidence = evidence
                dispatch_verified = bool(evidence.get('spawned_by')
                                         or evidence.get('parent_session')
                                         or evidence.get('owner_session')
                                         or evidence.get('spawned') is True)
            else:
                declaration_note = ('宿主核验未通过：'
                                    + str(evidence.get('reason') or '验证器未给出原因')
                                    + '；回执按 declared 记录，不升级')
                host_evidence = evidence
        else:
            payload = host_payload(root, topic, writer_session=session, proof=normalized,
                                   owner_session=owner_session, report=report)
            declaration_note = ('写页会话由调用方自述；本工具未核验真实派工，也未核验送达。'
                                'verified 只能由注入的宿主验证器产生（需要 proof={session_id, seq}）')
        cutoff = prepared.get('event_cutoff') or event_cutoff(root, topic)
        version = _digest([task.get('task_id'), snapshot['fingerprint'], body_sha, cutoff,
                           WRITER_ROLE, session])
        if old.get('version') == version and old.get('verification') == verification:
            if state_name(task.get('state')) != 'accepted':
                task.update(state='accepted', accepted_at=old.get('accepted_at'))
                _save_json(task_path(root, topic), task)
            return dict(old, idempotent=True)
        receipt = {
            'schema': SCHEMA,
            'topic': topic,
            'task_id': task.get('task_id'),
            'prepared_id': prepared.get('prepared_id'),
            'source_fingerprint': snapshot['fingerprint'],
            'sources': snapshot['files'],
            'event_cutoff': cutoff,
            'body_sha256': body_sha,
            'page_bytes': len(page.read_bytes()),
            'writer_role': WRITER_ROLE,
            'writer_session': session,
            'writer_session_source': session_source,
            'verification': verification,
            'session_verified': verification == VERIFIED,
            'dispatch_verified': dispatch_verified,
            'delivery_verified': delivery_verified,
            'proof': normalized,
            'proof_verified_by': proof_verified_by,
            'content_bound': content_bound,
            'host_evidence': host_evidence,
            'declaration_note': declaration_note,
            'version': version,
            'accepted_at': now_iso(),
            'exp': task.get('exp'),
            'host_payload': payload,
        }
        _save_json(receipt_path(root, topic), receipt)
        task.update(state='accepted', accepted_at=receipt['accepted_at'],
                    accepted_task_id=task.get('task_id'))
        _save_json(task_path(root, topic), task)
        return dict(receipt, idempotent=False)


def failed(root, topic, reason):
    """记录一次写页失败：保留原因与待办，任务回到 `pending_writer_retry`。"""
    root = Path(root).resolve()
    require_topic(topic)
    _text(reason, '失败原因')
    with _exclusive(root, topic):
        task = request(root, topic)
        task.update(state='pending_writer_retry', last_error=reason,
                    attempts=int(task.get('attempts') or 0) + 1)
        _save_json(task_path(root, topic), task)
        return {'ok': True, 'topic': topic, 'state': state_name(task.get('state')),
                'attempts': task.get('attempts'), 'last_error': reason,
                'next': '补齐原因后重新 prepare / 派 scribe 写页；文档失败不阻断安全停机'}


# ---------------------------------------------------------------------------
# 整轮交付
# ---------------------------------------------------------------------------


def check_round(root, topic, exp):
    """整轮交付：`round` 终态 + 负责人判断 + 当前页回执，三样都齐才算交付。

    ⛔ 单臂完成（scope=run）、`partial`、`collected` 都不算整轮结束。
    """
    root = Path(root).resolve()
    require_topic(topic)
    require_token(str(exp or ''), '实验号')
    problems, notes = [], []
    events = [row for row in _event_rows(root, topic) if row.get('exp') == exp]
    terminals = [row for row in events
                 if row.get('scope') == 'round' and row.get('kind') in TERMINAL_KINDS]
    run_only = [row for row in events
                if row.get('scope') == 'run' and row.get('kind') in TERMINAL_KINDS]
    partials = [row for row in events if row.get('kind') == 'partial']
    if not events:
        problems.append(f'{exp} 没有运行事件；整轮交付无从谈起')
    if not terminals:
        problems.append('缺整轮终态事件（scope=round 的 completed/failed/stopped/superseded）；'
                        '单臂完成或 partial 只是阶段结果，不能冒充整轮交付')
        if run_only:
            notes.append('已有单臂终态：' + '、'.join(sorted({row['kind'] for row in run_only}))
                         + '（只证明那一臂结束）')
        if partials:
            notes.append('已有 partial：' + '、'.join(sorted({row['event_id'] for row in partials})[:5])
                         + '（阶段结果，不能算整轮）')
    else:
        last = terminals[-1]
        if not str(last.get('reason') or '').strip():
            problems.append(f'整轮终态 {last.get("kind")} 没有真实原因（reason）')
        conclusions = [row for row in _input_rows(root, topic)
                       if row.get('kind') == 'leader_conclusion' and row.get('exp') == exp
                       and str(row.get('at') or '') > str(last.get('at') or '')]
        if not conclusions:
            problems.append('缺整轮结束后负责人的判断来源（leader_conclusion 要晚于整轮终态）')
    page_state = check(root, topic, required=True)
    problems.extend(page_state['problems'])
    receipt = _load_json(receipt_path(root, topic), {}) or {}
    if receipt.get('exp') != exp:
        problems.append(f'当前页面回执不对应本轮（回执 exp={receipt.get("exp")!r}，本轮 {exp!r}）')
    if receipt and receipt.get('verification') != VERIFIED:
        notes.append('回执只是 declared：真实派工未核验')
    return {
        'ok': not problems,
        'problems': problems,
        'notes': notes,
        'exp': exp,
        'round_terminal': None if not terminals else {
            'event_id': terminals[-1].get('event_id'),
            'kind': terminals[-1].get('kind'),
            'reason': terminals[-1].get('reason'),
            'at': terminals[-1].get('at'),
        },
        'page': page_state,
    }


# ---------------------------------------------------------------------------
# checkpoint 与只读诊断
# ---------------------------------------------------------------------------

#: checkpoint 里可当「内容时间」的键。⛔ 不用 mtime。
CHECKPOINT_TIME_KEYS = ('at', 'updated_at', 'created_at', 'projected_at', 'time', 'ts', 'timestamp')

WAIT_WORDS = ('等用户', '需要授权', '待批准', '等你', '等待授权', '等确认', '等人')
#: 只收「真的结束」的措辞。`收口` 单独一词太宽（它常表示「这一轮快完了」而非已停）。
STOP_WORDS = ('已收口', '不再跑', '已结束', '停手', '已完成并停止', '本轮结束')


def latest_checkpoint(root, topic, *, now=None, max_age_hours=CHECKPOINT_MAX_AGE_HOURS):
    """最新 checkpoint 的**内容时间**与新鲜度。

    ⚠️ 时间只从 checkpoint 文件内容里取（`at` / `updated_at` / …）。取不到 ⇒ 时效不可判 ⇒ 状态待核。
    ⛔ 不用 mtime 冒充运行时间。
    """
    directory = topic_dir(root, topic)
    files = sorted(directory.glob('CHECKPOINT_*.json'))
    now = now or datetime.now(timezone.utc)
    if not files:
        return {'file': None, 'path': None, 'age_hours': None, 'stale': True,
                'timestamp': None, 'next_action': '', 'summary': '',
                'why': '没有 checkpoint；只能写状态待核，不能写运行中'}
    entries = []
    for path in files:
        document = None
        try:
            document = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            document = None
        stamp = None
        if isinstance(document, dict):
            for key in CHECKPOINT_TIME_KEYS:
                stamp = _parse_ts(document.get(key))
                if stamp is not None:
                    break
        entries.append((stamp, path, document))
    known = [entry for entry in entries if entry[0] is not None]
    if known:
        stamp, path, document = max(known, key=lambda entry: entry[0])
    else:
        stamp, path, document = None, files[-1], entries[-1][2]
        if document is None:
            try:
                document = json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                document = None
    document = document if isinstance(document, dict) else {}
    age = None if stamp is None else (now - stamp).total_seconds() / 3600
    stale = age is None or age > max_age_hours
    if age is None:
        why = (f'{path.name} 里没有可判定的时间戳（at / updated_at / created_at）；'
               '时效不可判 ⇒ 状态待核')
    elif stale:
        why = f'{path.name} 的内容时间距今 {age:.2f}h，超过阈值 {max_age_hours}h ⇒ 状态待核'
    else:
        why = (f'{path.name} 的内容时间距今 {age:.2f}h（阈值 {max_age_hours}h）；'
               '未探测进程，runtime_verified=false')
    return {
        'file': path.name,
        'path': relative(root, path) if path.is_relative_to(Path(root).resolve()) else None,
        'age_hours': None if age is None else round(age, 3),
        'stale': stale,
        'timestamp': _iso(stamp),
        'next_action': str(document.get('next_action') or ''),
        'summary': str(document.get('summary') or ''),
        'why': why,
    }


def verdict_of(next_action, *, round_terminal=None, checkpoint_stale=False):
    """流程判定。判定只看 checkpoint 内容与整轮终态，不看心跳。

    整轮终态是**事件事实**，优先于 checkpoint 时效；过了时效只是「不能证明还活着」。
    """
    if round_terminal:
        return '已结束', f'整轮终态：{round_terminal.get("kind")}（{round_terminal.get("reason") or "无原因"}）'
    if checkpoint_stale:
        return '状态待核', '交接记录过期或时间不可判；不是实时活跃证据'
    action = next_action or ''
    if any(word in action for word in STOP_WORDS):
        return '已停', 'checkpoint 的 next_action 写「结束/不再跑」'
    if any(word in action for word in WAIT_WORDS):
        return '等人说话', 'checkpoint 的 next_action 写「等用户/需要授权」'
    if action:
        return '在推', 'checkpoint 的 next_action 有具体下一轮动作（未核验进程存活）'
    return '说不清', 'checkpoint 没有可用的 next_action'


def _lease_state(db, topic):
    """租约：只读数据库。不提供库、库不存在、表不可读 ⇒ 一律 UNKNOWN，不推断。"""
    if not db:
        return {'state': 'UNKNOWN', 'why': '未提供数据库；不推断租约与绑定', 'last_activity': None}
    path = Path(db)
    if not path.is_file():
        return {'state': 'UNKNOWN', 'why': '数据库不存在；不推断租约', 'last_activity': None}
    try:
        # 连接方式委托 runtime.store.ReadonlyStore：WAL 库按 sidecar 实况选
        # mode=ro / immutable=1，读不创建 -shm / -wal；不可读一律 UNKNOWN，不推断。
        connection = ReadonlyStore(path).connect()
        try:
            row = connection.execute(
                'SELECT leader_session_id, leader_since FROM topics WHERE id=?', (topic,)).fetchone()
        finally:
            connection.close()
    except (sqlite3.Error, StoreError) as exc:
        return {'state': 'UNKNOWN', 'why': f'租约不可读：{type(exc).__name__}',
                'last_activity': None}
    if row is None:
        return {'state': 'UNKNOWN', 'why': '专题不在数据库里', 'last_activity': None}
    leader = row[0]
    if not leader:
        return {'state': 'FREE', 'why': '没有负责人绑定', 'last_activity': None}
    return {'state': 'BOUND', 'why': '有负责人绑定（未核验存活）',
            'last_activity': str(row[1] or '') or None}


def _read_process_status(root, topic):
    try:
        document = _load_json(process_status_path(root, topic), None)
    except (OSError, ValueError):
        return None
    return document


def diagnose(root, topic, *, checkpoint_max_age_hours=CHECKPOINT_MAX_AGE_HOURS, now=None, db=None):
    """只读诊断。

    ⛔ 不建目录、不写锁、不写日志、不写页面、不写数据库；不探测进程或会话存活 ⇒ `runtime_verified` 恒 false。
    checkpoint 内容时间超过阈值（默认 2 小时）或时间不可判 ⇒ 判定「状态待核」，⛔ 不写「运行已验证」。
    """
    root = workspace_root(root)
    require_topic(topic)
    if not isinstance(checkpoint_max_age_hours, (int, float)) or checkpoint_max_age_hours <= 0:
        raise ReportingError('checkpoint_max_age_hours 必须 > 0')
    if db is not None:
        # 数据库也按工作区隔离：相对路径相对工作区，越界一律拒。
        db = str(inside(root, str(db)))
    report = {
        'read_only': True,
        'topic': topic,
        'root': str(root),
        'writes': [],
        'note': '只读诊断：不建目录、不写锁/日志/页面/DB；不探测进程或会话存活',
    }
    if not topic_exists(root, topic):
        report.update({'ok': False, 'verdict': '未开始', 'why': '专题目录不存在',
                       'runtime_verified': False, 'error': f'专题目录不存在：{topic_rel(topic)}'})
        return report
    checkpoint = latest_checkpoint(root, topic, now=now,
                                   max_age_hours=checkpoint_max_age_hours)
    terminal = round_terminal(root, topic)
    verdict, why = verdict_of(checkpoint['next_action'], round_terminal=terminal,
                              checkpoint_stale=checkpoint['stale'])
    page_state = check(root, topic)
    documentation = documentation_state(root, topic)
    lease = _lease_state(db, topic)
    monitor = _read_process_status(root, topic)
    report.update({
        'ok': page_state.get('ok') and verdict not in ('状态待核', '说不清'),
        'verdict': verdict,
        'why': why,
        'runtime_verified': False,
        'checkpoint': checkpoint,
        'checkpoint_max_age_hours': checkpoint_max_age_hours,
        'round_terminal': terminal and {'event_id': terminal.get('event_id'),
                                        'kind': terminal.get('kind'),
                                        'reason': terminal.get('reason')},
        'page': page_state,
        'documentation': documentation,
        'lease': lease,
        'monitor': {
            'source': f'{PROCESS_STATUS_DIR}/{topic}.json',
            'exists': monitor is not None,
            'last_projected_at': (monitor or {}).get('projected_at'),
            'note': '心跳只证明监控写过这份流程文件，不证明实验存活，也不算研究进展',
        },
    })
    return report


# ---------------------------------------------------------------------------
# 流程状态投影（与页面分开）
# ---------------------------------------------------------------------------


def documentation_state(root, topic):
    """页面交付状态：只看来源字节、正文哈希与回执，不看 mtime / 心跳。"""
    root = Path(root).resolve()
    require_topic(topic)
    prepared = _load_json(sources_path(root, topic), {}) or {}
    task = _load_json(task_path(root, topic), {}) or {}
    receipt = _load_json(receipt_path(root, topic), {}) or {}
    page = page_path(root, topic)
    snapshot = source_snapshot(root, topic)
    body = _digest(page.read_bytes()) if page.is_file() else None
    legacy = page.is_file() and legacy_page(page.read_text(encoding='utf-8-sig'))
    acknowledged = bool(receipt) and receipt.get('source_fingerprint') == snapshot['fingerprint'] \
        and receipt.get('body_sha256') == body
    if not prepared and not task:
        state = '历史未接入' if legacy else '未供料'
    elif snapshot['fingerprint'] != prepared.get('fingerprint'):
        state = '来源已变，需重新供料'
    elif acknowledged:
        state = '已验收'
    else:
        state = '待更新'
    return {
        'state': state,
        'prepared': bool(prepared),
        'acknowledged': acknowledged,
        'report_delivered': acknowledged,
        'task_state': state_name(task.get('state')),
        'prepared_id': prepared.get('prepared_id'),
        'body_sha256': body,
        'source_fingerprint': snapshot['fingerprint'],
        'verification': receipt.get('verification'),
        'writer_role': receipt.get('writer_role'),
        'writer_session': receipt.get('writer_session'),
        'note': '心跳与 mtime 不参与这里的判断',
    }


def run_report_state(root, topic):
    """运行结束 ≠ 报告交付：两件事分开报，避免「跑完了」被读成「页面写完了」。"""
    root = Path(root).resolve()
    require_topic(topic)
    events = _event_rows(root, topic)
    run_terminals = [row for row in events
                     if row.get('scope') == 'run' and row.get('kind') in TERMINAL_KINDS]
    round_terminals = [row for row in events
                       if row.get('scope') == 'round' and row.get('kind') in TERMINAL_KINDS]
    documentation = documentation_state(root, topic)
    delivered = bool(documentation['report_delivered'])
    return {
        'run_ended': bool(run_terminals or round_terminals),
        'run_reason': (run_terminals or round_terminals or [{}])[-1].get('reason'),
        'round_ended': bool(round_terminals),
        'report_delivered': delivered,
        'documentation': documentation,
        'next_action': None if delivered else
        '运行结束不等于报告交付：还须派 scribe 写页并 ack；文档失败不覆盖退出码，也不阻停',
    }


def process_status_projection(root, topic, *, checkpoint_max_age_hours=CHECKPOINT_MAX_AGE_HOURS,
                              now=None, db=None):
    """流程状态投影：租约、判定、checkpoint 时效、交付状态。**不进研究页正文。**"""
    root = Path(root).resolve()
    diag = diagnose(root, topic, checkpoint_max_age_hours=checkpoint_max_age_hours,
                    now=now, db=db)
    previous = _read_process_status(root, topic) or {}
    projection = {
        'schema': SCHEMA,
        'topic': topic,
        'projected_at': now_iso(),
        'verdict': diag.get('verdict'),
        'why': diag.get('why'),
        'runtime_verified': False,
        'checkpoint': diag.get('checkpoint'),
        'lease': diag.get('lease'),
        'page': {
            'path': page_rel(topic),
            'delivery': (diag.get('documentation') or {}).get('state'),
            'body_sha256': (diag.get('documentation') or {}).get('body_sha256'),
        },
        'documentation': diag.get('documentation'),
        'round_terminal': diag.get('round_terminal'),
        'heartbeat': {
            'source': f'{PROCESS_STATUS_DIR}/{topic}.json',
            'previous_at': previous.get('projected_at'),
            'note': '心跳只是监控写过这份文件的证据：不证明实验存活，也不算研究进展',
        },
        'note': '流程状态只落 process_status/；⛔ 不写进研究页 STATUS.md（页面只由 scribe 写研究内容）',
    }
    return projection


def write_process_status(root, topic, *, checkpoint_max_age_hours=CHECKPOINT_MAX_AGE_HOURS,
                         now=None, db=None):
    """把流程状态写进 `process_status/`（JSON + 人读摘要）。**不碰研究页任何字节。**"""
    root = Path(root).resolve()
    require_topic(topic)
    if not topic_exists(root, topic):
        raise ReportingError(f'专题目录不存在：{topic_rel(topic)}；先 prepare 再投影流程状态')
    projection = process_status_projection(root, topic,
                                           checkpoint_max_age_hours=checkpoint_max_age_hours,
                                           now=now, db=db)
    json_path = process_status_path(root, topic)
    _save_json(json_path, projection)
    note = [
        f'# 流程状态 · {topic}',
        '',
        '> 这份文件由脚本维护，是**流程状态**；⛔ 不写进研究页 `STATUS.md`。',
        f'> 投影时间：{projection["projected_at"]}。心跳只证明监控写过这份文件，'
        '不证明实验存活，也不算研究进展。',
        '',
        f'- 判定：{projection.get("verdict")}（{projection.get("why")}）',
        f'- checkpoint：{(projection.get("checkpoint") or {}).get("file")}；'
        f'时效 {((projection.get("checkpoint") or {}).get("age_hours"))} 小时；'
        f'runtime_verified = false（本工具不探测进程）',
        f'- 租约：{(projection.get("lease") or {}).get("state")}（{(projection.get("lease") or {}).get("why")}）',
        f'- 页面交付：{(projection.get("page") or {}).get("delivery")}',
    ]
    _atomic_write(process_status_note_path(root, topic), '\n'.join(note) + '\n')
    projection['written'] = {
        'json': relative(root, json_path),
        'note': relative(root, process_status_note_path(root, topic)),
    }
    return projection


# ---------------------------------------------------------------------------
# 文档层失败隔离（安全停机）
# ---------------------------------------------------------------------------


def document_without_blocking(step, *args, **kwargs):
    """执行一次文档层动作，失败只留证据。

    * ⛔ 不抛出：调用方拿到 `ok=False` 与原因即可；
    * ⛔ 不覆盖调用方退出码（配合 `exit_code_with_documentation`）；
    * ⛔ 不阻断安全停机：`blocked_stop` 恒 false。
    """
    try:
        return {'ok': True, 'result': step(*args, **kwargs),
                'blocked_stop': False, 'exit_code_unchanged': True}
    except (ReportingError, ValueError, OSError, sqlite3.Error, TypeError, KeyError) as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}',
                'blocked_stop': False, 'exit_code_unchanged': True}


def exit_code_with_documentation(original, document_result):
    """文档层结果**不许**改退出码：原样返回调用方原来的退出码。"""
    return int(original)
