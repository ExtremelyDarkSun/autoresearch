# GENERATED-BY: portable extraction / neutral decision layer, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 17:10 +08:00（只读构造：DecisionService.readonly(db) / readonly=True）
# PURPOSE: 中性决策审批链 —— 先页后卡、同决策双回执、两小时未批准自动取消、入向文字回复窗口与阻断
# ORIGIN: none（新文件）。机制取自上游「审批页 / 决策登记 / 入向回复诊断」三处通用实现，去掉具体平台与个人配置
# CUMULATIVE-DELTA: 首版把 B1–B10 / D1–D5 / E7–E8 的通用部分落成可调用机制；本轮补 ① authorization_check 默认
#   纯只读（可在 BEGIN IMMEDIATE 内调用）② 版本绑定改用单一 research_core 指纹（不信调用方自填哈希、不回写）
#   ③ 完整 Channel 协议（describe/health/send_*/query/verify_receipt/authenticate/poll/subscribe）与发送方指纹
#   ④ 选择卡 wire metadata（short_code / card_options / card_action_template，不改 payload hash）
#   ⑤ 探索性判据不强制数字门槛、不强制重复；单元素 options 合法、部分否决不消费短码。
#   本轮再补 ⑥ 审批页方案集与**实际执行规范**双绑定（execution_core 全对象保留 argv/实现/预算/
#   题集；large/submission 准入只认它）⑦ 页里渲染实际命令/预算/题集摘要与执行指纹 ⑧ verify_remote
#   用通道 GET 复核已发回执。⑨ 只读构造：`DecisionService.readonly(db)` / `readonly=True` 用同路径
#   `ReadonlyStore`（mode=ro）而不复制库，跳过建表与 Accounts，Delivery/Inbox 以 readonly 构造；
#   写入口（prepare/send/tick/ingest/reply/record）抛 ReadonlyServiceError；缺库/缺表/旧 schema
#   的读方法返回「不可判」（ok=False + undecidable=True），⛔ 不建库、不迁移、不把未知表读成空。
"""中性决策审批链。

这个模块把「要不要人拍板」做成一条可核对的数据链，不假设任何具体通道：

    prepare  → 生成自包含审批页（缺背景 / 上下文 / 进展就拒建）
    send     → 先送页拿到真实 channel 回执，再送同一决策的选择卡
    inbox    → 入向事件持久化；文字消息产生回复任务，卡片点击**不**产生
    overdue  → 文字消息超过 304 秒没回逐条点名，并阻断「继续推进」
    reply    → 只有原始事件、实验号、收件人一致时才允许回话
    status   → 双回执按台账重算（手写 success 不被承认）

## 边界（⛔ 不要越过）

- **平台传输不在核心里。** 本模块只认注入的 channel 对象（见 `ApprovalChannel`）；
  群聊要不要 @ 机器人、文件怎么上传，都是适配层自己声明的状态，核心只**记录**不解释。
- **审批只能来自一条真实入向事件。** 没有任何 `approve()` 快捷入口，CLI 也没有
  `--approve` / `--fake` / `--force`：想批准就得先落一条事件，再由注入的身份校验器判归属。
- **只写盘不算送达。** 决策状态里的 `awaiting_approval` 由台账里的两条回执重算得出，
  谁把状态字段手改成 awaiting 都不会让 `status()` 变绿。
- **无法 exactly-once 时隔离，不自动重发。** 通道报错但可能已经发出去 ⇒ 该交付落
  `unknown`，决策进 `quarantined`；再次 `send()` 直接拒绝，不重发。

退出码约定（CLI 与 `hook` 共用）：`0` 正常 · `2` 参数 / 通道 / 身份配置失败（fail-closed）·
`3` 需人工处置（隔离、状态不允许）· `9` 有超期未回的入向文字（RED）。
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable
try:
    from kernel.autoresearch.runtime.store import (ReadonlyStore, StoreError, canonical_json,
                                                   is_readonly_store, require_text)
    from kernel.autoresearch.runtime.delivery import Delivery, Receipt, NotSent, LeaseLost
    from kernel.autoresearch.runtime.inbox import Inbox
    from kernel.autoresearch.runtime.accounts import Accounts
except ImportError:  # pragma: no cover - 兼容包内相对导入
    from kernel.autoresearch.runtime.store import (ReadonlyStore, StoreError, canonical_json,
                                                   is_readonly_store, require_text)
    from kernel.autoresearch.runtime.delivery import Delivery, Receipt, NotSent, LeaseLost
    from kernel.autoresearch.runtime.inbox import Inbox
    from kernel.autoresearch.runtime.accounts import Accounts

__all__ = [
    'APPROVAL_TIMEOUT_SECONDS', 'TEXT_REPLY_GRACE_SECONDS', 'OPTION_FIELDS',
    'REQUIRED_PAGE_BLOCKS', 'DECISION_STATES', 'RESOLUTIONS', 'SCOPES',
    'SCOPE_POLICY_REQUIRES_APPROVAL', 'ApprovalChannel', 'SEND_PAYLOAD_KEYS',
    'RECEIPT_KEYS', 'EVENT_KEYS',
    'DecisionError', 'ReadinessError', 'DecisionConflict', 'OrderingViolated',
    'ChannelUnavailable', 'ReceiptRejected', 'IdentityRefused', 'QuarantineBlocked',
    'OverdueInbound', 'NotAuthorized', 'ReadonlyServiceError', 'ChannelReceipt',
    'DecisionService', 'readiness',
    'build_page_html', 'page_problems', 'derive_short_code', 'plan_fingerprint',
    'research_core', 'research_fingerprint', 'make_authorization_check',
]

# ────────────────────────────── 冻结值（一处写一次） ──────────────────────────────

#: 审批请求两小时未明确批准 ⇒ 自动取消（不消费短码、不采纳推荐项）。⛔ 不提供延长开关。
APPROVAL_TIMEOUT_SECONDS = 7200.0
#: 入向**文字**消息的回复窗口：300 秒宽限 + 4 秒取整余量 = 304 秒。
TEXT_REPLY_GRACE_SECONDS = 304.0

#: 审批作用的三个范围。范围决定「要不要人拍板」，⛔ 不由调用方自己声明。
SCOPES = ('small', 'large', 'submission')
#: `small` = 已授权范围内的小样本机制验证 ⇒ 免审批；`large` / `submission` 必须有真实批准。
#: ⚠️ 不在表里的范围一律按「需要批准且没有批准」处理（fail-closed）。
SCOPE_POLICY_REQUIRES_APPROVAL = {'small': False, 'large': True, 'submission': True}

#: 每条方案的 13 个字段（源契约字段清单；⛔ 不在这里增删名字）。
OPTION_FIELDS = (
    ('id', '编号'),
    ('what', '做什么'),
    ('fingerprint', '改动指纹'),
    ('sweep_group', '组标记'),
    ('cost', '代价'),
    ('criteria', '预期与判据'),
    ('tasklist', '题单'),
    ('branches', '结果分支表'),
    ('stop_point', '停机点'),
    ('not_doing', '本轮不做'),
    ('hypothesis', '验证的猜想'),
    ('expected_pass', '预期能过的题'),
    ('expected_effect', '预期效应与尺子分辨率'),
)
OPTION_FIELD_KEYS = tuple(key for key, _ in OPTION_FIELDS)
REQUIRED_BRANCH_KINDS = ('all_pass', 'partial', 'all_fail', 'device_failure')

#: `brief` 三段：背景 / 上下文 / 进展。缺任何一段 ⇒ 拒建审批页。
BRIEF_KEYS = (('background', '背景'), ('context', '上下文'), ('progress', '进展'))

#: 页里必须有的六个块（兼容锚：读取方按这几个 id 找块）。
REQUIRED_PAGE_BLOCKS = ('brief-background', 'brief-context', 'brief-progress',
                        'options', 'choose', 'evidence')
APPENDIX_ID = 'audit-appendix'

#: 自包含页里**不许出现**的外部依赖（它是当附件发出去的单文件）。
FORBIDDEN_PAGE_TOKENS = ('<script', '<link', '<img', '<iframe', '<object',
                         '@import', ' src=', ' href=', 'url(')

DECISION_STATES = ('prepared', 'awaiting_approval', 'resolved', 'expired',
                   'send_failed', 'quarantined')
OPEN_STATES = ('prepared', 'send_failed', 'awaiting_approval')
RESOLUTIONS = ('user_approved', 'user_all_reject', 'user_stopped', 'timeout_cancelled')
TRANSITIONS = {
    'prepared': frozenset(('prepared', 'send_failed', 'awaiting_approval',
                           'quarantined', 'expired', 'resolved')),
    'send_failed': frozenset(('send_failed', 'awaiting_approval', 'quarantined', 'expired')),
    'awaiting_approval': frozenset(('awaiting_approval', 'resolved', 'expired', 'quarantined')),
    'resolved': frozenset(),
    'expired': frozenset(),
    'quarantined': frozenset(),
}
CODE_ISSUED, CODE_CONSUMED, CODE_VOID = 'issued', 'consumed', 'void'
STATE_TEXT = {'prepared': '已备好，未送出', 'awaiting_approval': '待批',
              'resolved': '已裁决', 'expired': '已过期（超时取消）',
              'send_failed': '发送失败', 'quarantined': '已隔离'}

EXP_PATTERN = r'(?<![A-Za-z0-9_-])exp-[A-Za-z0-9][A-Za-z0-9_-]*'
EXP_RE = re.compile(EXP_PATTERN)
DECISION_ID_RE = re.compile(r'^[A-Za-z][0-9]{1,6}(?:\.[0-9]{1,3})?$')
SHORT_CODE_RE = re.compile(r'^[A-Z0-9]{4,12}$')
CARD_CLICK_INTENTS = frozenset(('approve', 'approval', 'reject', 'deny'))

#: `criteria.mode` 的两个取值。确认性要数字阈值，探索性不强制数字门槛。
CRITERIA_MODES = ('confirmatory', 'exploratory')

#: 适配层收到的发送载荷里**一定有**的键（回执必须原样回填 request_key / payload_hash）。
SEND_PAYLOAD_KEYS = ('request_key', 'payload_hash', 'kind', 'exp_id', 'recipient',
                     'decision_id', 'body', 'artifact', 'sha256', 'event_reply_to',
                     'fence', 'lease_until', 'lease_token')

#: 回执里**必须**有的键（缺一不认；核心还会覆写 sender 三件，防止适配层自报）。
RECEIPT_KEYS = ('message_id', 'request_key', 'exp_id', 'recipient', 'payload_hash',
                'kind', 'decision_id', 'ok', 'returncode', 'path', 'bytes', 'sha256',
                'short_code', 'adapter', 'identity', 'config_ref', 'conditions', 'sent_at')

#: 入向事件的**最小**形状（poll / subscribe 递进来的每条都要能过 Inbox）。
EVENT_KEYS = ('event_id', 'exp_id', 'create_time', 'recipient', 'principal')


# ────────────────────────────── 异常 ──────────────────────────────

class DecisionError(ValueError):
    """决策链上的通用拒绝（参数 / 状态 / 归属不成立）。"""


class ReadinessError(DecisionError):
    """审批页拒建：缺件就是缺件，⛔ 不用旧长字段替你拼一页。"""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('审批页**拒建**：\n  - ' + '\n  - '.join(self.problems))


class DecisionConflict(DecisionError):
    """同一个决策号换了方案内容 —— 必须新开决策号，⛔ 不静默覆盖。"""


class OrderingViolated(DecisionError):
    """先页后卡被违反：页没有成功回执就发卡。"""


class ChannelUnavailable(DecisionError):
    """没有真实通道 / 健康检查不过 ⇒ fail-closed，不发、不造假回执。"""


class ReceiptRejected(DecisionError):
    """通道返回的回执字段不全或对不上：空回执、伪 success 一律不认。"""


class IdentityRefused(DecisionError):
    """身份校验器缺失或判否 —— 审批 / 回复都不成立。"""


class QuarantineBlocked(DecisionError):
    """决策已隔离：结果不确定，⛔ 不自动重发。"""


class OverdueInbound(DecisionError):
    """有超过回复窗口还没回的用户文字 ⇒ 阻持续推进（不许报假绿）。"""

    def __init__(self, items, grace):
        self.items = list(items)
        self.grace = grace
        lines = [f'有 {len(self.items)} 条用户文字超过 {int(grace)}s 没回：']
        for item in self.items[:10]:
            lines.append(f"  · {item.get('event_id')}（{item.get('source')}）"
                         f" 年龄={item.get('age_sec')}s：「{item.get('excerpt')}」")
        super().__init__('\n'.join(lines))


class NotAuthorized(DecisionError):
    """准入判据不成立 ⇒ 不许启动。带逐条问题，便于主链直接打印。"""

    def __init__(self, result):
        self.result = dict(result or {})
        problems = self.result.get('problems') or ['没有可核的批准']
        super().__init__('未获授权：\n  - ' + '\n  - '.join(problems))


class ReadonlyServiceError(DecisionError):
    """只读 DecisionService 被要求写：⛔ 不降级、不静默跳过、不伪造回执。"""


class _CardRejected(DecisionError):
    """内部用：卡片点击不成立。抛出即回滚，随后落一条 rejected 记录。"""


# ────────────────────────────── 通道协议（部署适配层的唯一契约） ──────────────────────────────

@runtime_checkable
class ApprovalChannel(Protocol):
    """注入通道的**完整**接口。核心只调这 9 个方法，⛔ 不含任何平台传输代码。

    真实适配层（部署方实现，凭据只留**引用**不留值）必须满足：

    ```python
    class DeployChannel:
        name = 'chat'                      # 稳定短名，进回执与台账

        def describe(self) -> dict:
            '''返回 {"adapter": str, "identity": str, "config_ref": str,
                     "conditions": dict, "version": str}。
            config_ref 是凭据的**引用**（例如 "env:CHAT_APP_ID"），
            ⛔ 不许把 token/secret 本身放进任何一个字段。'''
            ...

        def health(self) -> dict:
            '''返回 {"ok": True|False, "detail": {...}}；ok 不为 True 时核心不发。'''
            ...

        def send_file(self, payload: dict, artifact: bytes) -> dict: ...
        def send_card(self, payload: dict, artifact: bytes | None) -> dict: ...
        def send_text(self, payload: dict, artifact: bytes | None) -> dict: ...

        def query(self, request_key: str) -> dict | None:
            '''按 wire key 查原消息；查到就返回与发送时同形的回执，否则 None。'''

        def verify_receipt(self, receipt) -> bool:
            '''只对**自己确实发出过**的消息回 True（按 message_id / request_key 查自家台账）。'''

        def authenticate(self, source: str, event: dict) -> str:
            '''核事件来源，返回 principal（非空字符串）；核不过就抛异常。⛔ 不许改事件。'''

        def poll(self, cursor: str | None, limit: int) -> dict:
            '''返回 {"cursor": str|None, "events": [event, ...]}；cursor 是下次起点。'''

        def subscribe(self, callback) -> object:
            '''推送式适配层：每收到一条事件调 callback(event)；返回带 close() 的句柄。'''
            ...
    ```

    **发送载荷**（`payload`，键见 `SEND_PAYLOAD_KEYS`）：

    ```json
    {"request_key": "hil:<sha256>", "payload_hash": "<sha256>",
     "kind": "approval_page|card|reply",
     "exp_id": "exp-01", "recipient": "chat-01",
     "decision_id": "E1.1",            // 回复为 ""（回复不绑决策，绑入向事件）
     "body": "…", "artifact": "/abs/web/approval_E1.1.html", "sha256": "<页 sha256>",
     "event_reply_to": {"source": "chat", "event_id": "om_…", "exp_id": "exp-01",
                        "recipient": "chat-01"},
     "fence": 3, "lease_until": 1730000060.0, "lease_token": "[7,\"worker\",3]"}
    ```

    **回执**（`send_*` 的返回值，键见 `RECEIPT_KEYS`；核心会覆写 `adapter` / `identity` /
    `config_ref` / `sender_fingerprint` 三件，适配层自报的那几个不作数）：

    ```json
    {"message_id": "om_xxx", "request_key": "<原样回填>", "exp_id": "exp-01",
     "recipient": "chat-01", "payload_hash": "<原样回填>",
     "kind": "approval_page|card|reply", "decision_id": "E1.1",
     "ok": true, "returncode": 0,
     "path": "<页绝对路径>", "bytes": 9402, "sha256": "<页 sha256>",   // 页必填，卡/回复可省
     "short_code": "K7Q2",                                             // 卡可选，给了就核
     "adapter": "chat", "identity": "bot-…", "config_ref": "env:CHAT_APP_ID",
     "conditions": {"mention_required": true}, "sent_at": 1730000000.0}
    ```

    **选择卡的按钮（wire metadata）**：`send_card` 收到的 payload 在这几个基础键之外，
    还带**结构化的**决策字段 —— ⛔ 不要从 `body` 的自然语言里解析：

    ```json
    {"short_code": "K7Q2",
     "card_options": [{"option_id": "A", "label": "换抓手 A"},
                      {"option_id": "B", "label": "换抓手 B"}],   // 只含本轮待批的
     "card_action_template": {"intent": "approve", "exp_id": "exp-01",
                              "decision_id": "E1.1", "recipient": "chat-01",
                              "short_code": "K7Q2"}}
    ```

    这三项只加在**交给适配层的副本**上，不动 delivery 行里的 `payload_json`，
    所以回执仍按原来的 `payload_hash` 核验（幂等 / 重放判定不受影响）。

    适配层造按钮 value 时必须带齐（`channel` 是适配层自己的平台名）：

    ```json
    {"channel": "chat", "intent": "approve|reject", "exp_id": "exp-01",
     "decision_id": "E1.1", "recipient": "chat-01", "short_code": "K7Q2",
     "options": ["A"]}
    ```

    `options` **单元素合法**（一个按钮一条选项）；点「全否」给空数组或全部编号。
    入向归一事件的字段与它**逐键相同**，另加一个 `card_action` 字典（原样保留平台回调）：

    ```json
    {"event_id": "om_evt_1", "exp_id": "exp-01", "decision_id": "E1.1",
     "intent": "approve", "options": ["A"], "recipient": "chat-01",
     "short_code": "K7Q2", "principal": "<身份校验器判出的主体>",
     "create_time": 1730000000.0, "card_action": {"action": "approve", "raw": {}}}
    ```

    **文字回复的定位**：`send_text` 的 payload 带 `event_reply_to.event_id`
    （原始入向事件号）。适配层必须用它查出**本平台的消息 id**（例如本地
    事件→message_id 的映射）再回复；**查不到映射就 ⛔ 不许发**，抛 `NotSent`
    让核心保持 queued（这不是「发失败」，是「没法定位回哪一条」）。

    **失败语义（最重要的一条）**：
    - 确定没发出去 ⇒ 抛 `kernel.autoresearch.runtime.delivery.NotSent`。核心会回到 queued，可重试。
    - 不确定（超时 / 连接中断 / 平台 5xx）⇒ 抛任何其它异常。核心会落 `unknown` 并**隔离**，
      ⛔ 绝不自动重发。
    - ⛔ 不许把「不确定」报成 success，也不许把「不确定」报成 NotSent（后者等于允许重发）。
    """

    name: str

    def describe(self) -> dict: ...

    def health(self) -> dict: ...

    def send_file(self, payload: dict, artifact: bytes) -> dict: ...

    def send_card(self, payload: dict, artifact: bytes | None) -> dict: ...

    def send_text(self, payload: dict, artifact: bytes | None) -> dict: ...

    def query(self, request_key: str) -> dict | None: ...

    def verify_receipt(self, receipt: 'ChannelReceipt') -> bool: ...

    def authenticate(self, source: str, event: dict) -> str: ...

    def poll(self, cursor: 'str | None', limit: int) -> dict: ...

    def subscribe(self, callback: Callable[[dict], object]) -> object: ...


# ────────────────────────────── 回执 ──────────────────────────────

@dataclass(frozen=True)
class ChannelReceipt(Receipt):
    """通道回执。继承 Delivery 的 `Receipt`，好让 `Delivery.ack()` 直接校验与落账。

    ⚠️ **它只能由真实适配层产生。** 核心不提供 `ok=True` 的默认值构造器：
    适配层没真发出去就该抛 `NotSent`（可重试），而不是返回一条看着成功的回执。
    """
    kind: str = 'approval_page'
    decision_id: str = ''
    path: str = ''
    bytes: int = 0
    sha256: str = ''
    short_code: str = ''
    adapter: str = ''
    identity: str = ''
    config_ref: str = ''
    #: 核心按通道 `describe()` 算出来的发送方指纹（适配层自报的不作数）。
    sender_fingerprint: str = ''
    ok: bool = False
    returncode: int = -1
    sent_at: float = 0.0
    #: 适配层自报的触达条件（例如「群聊文字必须 @ 机器人」）。核心只记录，不解释。
    conditions: dict = field(default_factory=dict)

    def as_dict(self):
        return dict(self.__dict__)


def _receipt_getter(raw):
    if isinstance(raw, dict):
        return lambda key, default=None: raw.get(key, default)
    return lambda key, default=None: getattr(raw, key, default)


def _coerce_receipt(raw, payload, kind, adapter_name, decision_id):
    """把适配层的返回值收紧成 `ChannelReceipt`。⛔ 字段不全一律拒收。"""
    if raw is None:
        raise ReceiptRejected('通道没有返回回执（空回执不算送达）')
    get = _receipt_getter(raw)
    message_id = get('message_id')
    if not isinstance(message_id, str) or not message_id.strip():
        raise ReceiptRejected('回执的 message_id 为空（空回执不算送达）')
    request_key = get('request_key')
    if request_key != payload.get('request_key'):
        raise ReceiptRejected('回执的 request_key 不是本次请求（可能是旧回执或被拼出来的）')
    for key in ('exp_id', 'recipient', 'payload_hash'):
        if get(key) != payload.get(key):
            raise ReceiptRejected(f'回执的 {key} 与本次交付不一致')
    ok = get('ok')
    if ok is not True:
        raise ReceiptRejected(f'回执 ok={ok!r} 不是 True（命令没报错 ≠ 送达）')
    returncode = get('returncode')
    if returncode != 0:
        raise ReceiptRejected(f'回执 returncode={returncode!r} 不是 0')
    declared_kind = get('kind') or kind
    if declared_kind != kind:
        raise ReceiptRejected(f'回执 kind={declared_kind!r} 与本次交付 {kind!r} 不一致')
    declared_decision = get('decision_id') or decision_id
    if declared_decision != decision_id:
        raise ReceiptRejected(f'回执 decision_id={declared_decision!r} 与本次决策 {decision_id!r} 不一致')
    path = get('path') or ''
    size = get('bytes') or 0
    digest = get('sha256') or ''
    short_code = get('short_code') or ''
    conditions = get('conditions') or {}
    if not isinstance(conditions, dict):
        raise ReceiptRejected('回执的 conditions 必须是对象（适配层状态原样记录）')
    return ChannelReceipt(
        message_id=message_id.strip(),
        request_key=payload['request_key'],
        exp_id=payload['exp_id'],
        recipient=payload['recipient'],
        payload_hash=payload['payload_hash'],
        kind=kind,
        decision_id=decision_id,
        path=str(path),
        bytes=int(size) if isinstance(size, int) else 0,
        sha256=str(digest).lower(),
        short_code=str(short_code),
        adapter=str(get('adapter') or adapter_name),
        ok=True,
        returncode=0,
        sent_at=float(get('sent_at') or time.time()),
        conditions=dict(conditions),
    )


class _ChannelAdapter:
    """把注入的 channel 接到 `Delivery.send()/reconcile()` 的适配器协议上。

    路由只按交付 kind 选方法：页走 `send_file`，卡走 `send_card`，回复走 `send_text`。
    回执回来后由核心**覆写**发送方三件（adapter / identity / config_ref）与指纹：
    ⛔ 适配层自报的发送方字段不作数，避免两个部署互相顶替。
    """

    def __init__(self, channel, *, decision_id, kind, sender=None, sender_fingerprint='',
                 wire_meta=None):
        self.channel = channel
        self.decision_id = decision_id
        self.kind = kind
        self.sender = dict(sender or {})
        self.sender_fingerprint = sender_fingerprint
        self.wire_meta = dict(wire_meta or {})
        self.name = str(self.sender.get('adapter')
                        or getattr(channel, 'name', '') or 'channel')

    def _stamp(self, receipt):
        conditions = receipt.conditions or self.sender.get('conditions') or {}
        return replace(receipt,
                       adapter=self.sender.get('adapter') or self.name,
                       identity=self.sender.get('identity') or '',
                       config_ref=self.sender.get('config_ref') or '',
                       sender_fingerprint=self.sender_fingerprint,
                       conditions=dict(conditions))

    def send(self, payload, artifact_bytes):
        payload = dict(payload)
        # 让适配层拿到决策号：回执要能自报「这是哪一份决策的送达」，
        # 而不是靠核心猜。⛔ 适配层不许改其它字段（回执字段会被逐项比对）。
        payload.setdefault('decision_id', self.decision_id)
        if payload.get('kind') == 'card' and self.wire_meta:
            # ⚠️ 这些是 **wire metadata**：从真实决策结构里取，⛔ 不从 body 自然语言解析。
            # 它们只加在交给适配层的这份副本上，**不动** delivery 行里的 payload_json，
            # 所以回执仍按原来的 payload_hash 核验（不破坏幂等与重放判定）。
            payload['short_code'] = self.wire_meta.get('short_code', '')
            payload['card_options'] = list(self.wire_meta.get('card_options') or [])
            payload['card_action_template'] = dict(
                self.wire_meta.get('card_action_template') or {})
        if payload.get('kind') == 'approval_page':
            sender = getattr(self.channel, 'send_file', None)
            if not callable(sender):
                raise NotSent('通道没有 send_file 能力：审批页没有送出（不写成功回执）')
        elif payload.get('kind') == 'card':
            sender = getattr(self.channel, 'send_card', None)
            if not callable(sender):
                raise NotSent('通道没有 send_card 能力：选择卡没有送出（不写成功回执）')
        elif payload.get('kind') == 'reply':
            sender = getattr(self.channel, 'send_text', None)
            if not callable(sender):
                raise NotSent('通道没有 send_text 能力：文字回复没有送出（不写成功回执）')
        else:
            raise NotSent(f"核心不通过通道发送 kind={payload.get('kind')!r} 的交付")
        raw = sender(payload, artifact_bytes)
        return self._stamp(_coerce_receipt(raw, payload, self.kind, self.name,
                                           self.decision_id))

    def query(self, request_key):
        query = getattr(self.channel, 'query', None)
        if not callable(query):
            return None
        raw = query(request_key)
        if raw is None:
            return None
        get = _receipt_getter(raw)
        echoed = {key: get(key) for key in ('exp_id', 'recipient', 'payload_hash')}
        echoed['request_key'] = get('request_key') or request_key
        # 真正的逐项比对在 Delivery.ack() 里做（对着 delivery 行），这里只把类型收紧。
        return self._stamp(_coerce_receipt(raw, echoed, self.kind, self.name,
                                           self.decision_id))


# ────────────────────────────── 小工具 ──────────────────────────────

def _esc(value):
    return html.escape('' if value is None else str(value), quote=True)


def _iso(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec='seconds')


def _nonempty(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(isinstance(item, str) and item.strip() for item in value)
    return False


def _nonempty_str(value):
    return isinstance(value, str) and bool(value.strip())


def _as_text(value):
    if isinstance(value, str):
        return True, ''
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, str) for item in value):
            return True, ''
        return False, '列表里必须全是字符串'
    return False, f'必须是字符串或字符串列表（现在是 {type(value).__name__}）'


def _lines(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append('；'.join(f'{k}={item[k]}' for k in item))
            else:
                out.append(str(item))
        return out
    if isinstance(value, dict):
        return [f'{k}：{v}' for k, v in value.items()]
    return [str(value)]


def derive_short_code(exp_id, decision_id):
    """确定性短码：同 (实验号, 决策号) 永远得同一个码，重试不会换码。"""
    digest = hashlib.sha256(f'{exp_id}\x00{decision_id}'.encode('utf-8')).hexdigest()
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(alphabet[int(digest[i:i + 2], 16) % len(alphabet)] for i in range(0, 8, 2))


def plan_fingerprint(plan):
    """方案指纹：对规范化 JSON 取 sha256。审批与之绑定，换方案即失效（不按 mtime 判）。"""
    if not isinstance(plan, dict):
        raise DecisionError('方案必须是 JSON 对象')
    return hashlib.sha256(canonical_json(plan).encode('utf-8')).hexdigest()


#: 计算「研究身份」时**必须剔除**的键：自指哈希、传入副本、执行引用。
CORE_STRIP_KEYS = ('plan_sha256', 'plan_set_sha256', 'plan_sha', 'plan_set', 'decision_id')


def research_core(plan):
    """从完整 plan 里抽出一份规范化「研究内核」，`prepare` 与准入检查用它算同一个指纹。

    为什么需要它：如果把哈希写回 plan 再比对，就是自引用；如果直接信调用方填的
    `plan_sha256`，复制一个旧哈希再改内容就能蒙过去。所以两边都**只对实际内容**重算。

    规则：
    - 剔除 `plan_sha256` / `plan_set_sha256` / `plan_sha` / `plan_set` / `decision_id`
      （前四个是自指或传递副本，最后一个只是指向某份批准的执行引用）。
    - `authorization` 整个剔除，但把它的 `scope` 提升到顶层 —— 范围属于研究身份，
      预算账号 / 预留号属于执行引用，换了账号不该让研究结论失效。
    - 若 plan 把研究内容包在 `research` 子对象里，则以它为内核，并补回顶层的
      `scope` / `exp_id`（若它自己没有）—— 这样「完整 plan」与「prepare 时的 plan_set」
      得到同一个内核，调用方不必把返回值写回计划。
    - 其余键（`options`、`brief`、`criteria`、`budget`、`execution_argv` …）原样保留：
      **完整 plan 的选项就是核心里的选项**，改任何一条都算换了研究内容。
    """
    if not isinstance(plan, dict):
        raise DecisionError('方案必须是 JSON 对象')
    core = json.loads(canonical_json(plan))
    authorization = core.pop('authorization', None)
    scope = None
    if isinstance(authorization, dict):
        scope = authorization.get('scope')
    for key in CORE_STRIP_KEYS:
        core.pop(key, None)
    if scope is not None and 'scope' not in core:
        core['scope'] = scope
    research = core.pop('research', None)
    if isinstance(research, dict):
        merged = {key: value for key, value in research.items()
                  if key not in CORE_STRIP_KEYS}
        if 'scope' in core:
            merged.setdefault('scope', core['scope'])
        if 'exp_id' in core:
            merged.setdefault('exp_id', core['exp_id'])
        return merged
    return core


def research_fingerprint(plan):
    """研究内核的指纹 —— 审批**文字部分**的版本绑定依据。"""
    return plan_fingerprint(research_core(plan))


#: 计算「实际执行规范」指纹时剔除的键：自指哈希、传递副本、决策引用。
EXECUTION_STRIP_KEYS = ('plan_sha256', 'plan_set_sha256', 'plan_sha', 'plan_set', 'decision_id')
#: 出现这些键，说明这份对象是**完整执行规范**，而不是只有方案集的描述。
EXECUTION_MARKER_KEYS = ('execution_argv', 'implementation', 'case_sets')


def execution_core(plan):
    """完整执行规范的规范化副本（**全对象保留**）。

    与 `research_core()` 的区别是这里**不取子对象**：`execution_argv` / `implementation` /
    `budget` / `case_sets` / `hypothesis` / `research` 全部留在里面 —— 否则「批准的是研究
    描述、跑的是另一条 argv」就能蒙过去。

    只剔除：自指哈希（`plan_sha256` 等）、传入副本（`plan_set`）、决策引用（`decision_id`），
    以及 `authorization` 里的执行引用（`decision_id` / `series_id` / `reservation_id`），
    只保留 `scope` —— 换个预算账号不该让批准失效，换 argv 必须失效。
    """
    if not isinstance(plan, dict):
        raise DecisionError('执行计划必须是 JSON 对象')
    core = json.loads(canonical_json(plan))
    authorization = core.pop('authorization', None)
    scope = authorization.get('scope') if isinstance(authorization, dict) else None
    for key in EXECUTION_STRIP_KEYS:
        core.pop(key, None)
    if scope is not None and 'scope' not in core:
        core['scope'] = scope
    return core


def execution_fingerprint(plan):
    """实际执行规范的指纹 —— large/submission 的准入**只认它**。"""
    return plan_fingerprint(execution_core(plan))


def looks_like_execution_plan(plan):
    """这份对象是否像一份完整执行规范（而不是只有方案集的旧 plan_set）。"""
    if not isinstance(plan, dict):
        return False
    if any(key in plan for key in EXECUTION_MARKER_KEYS):
        return True
    return isinstance(plan.get('research'), dict)


def page_plan(plan):
    """审批页正文用哪一份：完整 plan 里带合法方案集时取 `research`，否则用原对象。"""
    if not isinstance(plan, dict):
        return plan
    if isinstance(plan.get('options'), list):
        return plan
    research = plan.get('research')
    if isinstance(research, dict):
        return research
    return plan


def _option_ids(plan_set):
    out = []
    for option in plan_set.get('options') or []:
        if isinstance(option, dict):
            oid = str(option.get('id') or '').strip()
            if oid and oid not in out:
                out.append(oid)
    return out


# ────────────────────────────── 方案集判据 ──────────────────────────────

def _option_problems(option, index):
    problems = []
    if not isinstance(option, dict):
        return [f'`options[{index}]` 不是对象']
    oid = str(option.get('id') or '').strip()
    if not oid:
        problems.append(f'`options[{index}].id` 缺失（每条方案必须有编号）')
    for key, label in OPTION_FIELDS:
        if key in ('sweep_group',):
            continue
        if key not in option:
            problems.append(f'方案 {oid or index} 缺字段「{label}」({key})')
            continue
        if key in ('expected_pass', 'expected_effect'):
            continue
        if option.get(key) in (None, '', [], {}):
            problems.append(f'方案 {oid or index} 的「{label}」({key}) 为空')
    # expected_pass：键必须存在（空数组合法），值必须是题号数组
    if 'expected_pass' in option:
        value = option.get('expected_pass')
        if not isinstance(value, list):
            problems.append(f'方案 {oid} 的 expected_pass 要是题号数组')
        elif any(not isinstance(item, str) or not item.strip() for item in value):
            problems.append(f'方案 {oid} 的 expected_pass 里有非字符串或空值')
    # criteria：确认性要数字阈值；探索性不强制数字门槛（可写观察 / 停止条件 / 边界）
    criteria = option.get('criteria')
    effect = option.get('expected_effect')
    effect_observation = effect.get('observation') if isinstance(effect, dict) else None
    mode = 'confirmatory'
    if isinstance(criteria, dict):
        declared = str(criteria.get('mode') or '').strip().lower()
        if declared:
            if declared not in CRITERIA_MODES:
                problems.append(f'方案 {oid} 的 criteria.mode 非法：{declared!r}'
                                f'（只能是 {"/".join(CRITERIA_MODES)}）')
            else:
                mode = declared
        metrics = criteria.get('metrics') or []
        if metrics and not isinstance(metrics, list):
            problems.append(f'方案 {oid} 的 criteria.metrics 必须是列表')
        if mode == 'confirmatory':
            if not metrics:
                problems.append(f'方案 {oid} 是确认性判据，必须给 metrics（量 + 阈值 + 出处 + 理由）')
            rationale = str(criteria.get('rationale') or '').strip()
            for metric in metrics if isinstance(metrics, list) else []:
                if not isinstance(metric, dict) or not metric.get('metric'):
                    problems.append(f'方案 {oid} 的判据缺「量」：{metric}')
                    continue
                if metric.get('threshold') in (None, '', [], {}):
                    problems.append(f'方案 {oid} 的确认性判据缺阈值：{metric.get("metric")}'
                                    '（⛔ 确认性不许没有数字门槛）')
                if not metric.get('source'):
                    problems.append(f'方案 {oid} 的确认性判据缺阈值出处：{metric.get("metric")}')
                if not str(metric.get('rationale') or rationale).strip():
                    problems.append(f'方案 {oid} 的确认性判据缺理由（rationale）：'
                                    f'{metric.get("metric")} —— 阈值凭什么定在这里')
        else:
            # 探索性：不强制数字阈值，但必须能被读数否掉或有停止条件
            has_observation = _nonempty(criteria.get('observation')) \
                or _nonempty(effect_observation)
            if not (has_observation or _nonempty(criteria.get('stop_condition'))):
                problems.append(f'方案 {oid} 是探索性判据，必须至少给 observation（观察什么）'
                                '或 stop_condition（什么时候停）—— ⛔ 不许什么都没有就进审批')
            if metrics:
                for metric in metrics:
                    if not isinstance(metric, dict) or not metric.get('metric'):
                        problems.append(f'方案 {oid} 的判据缺「量」：{metric}')
                    elif metric.get('threshold') not in (None, '', [], {}) \
                            and not metric.get('source'):
                        problems.append(f'方案 {oid} 给了阈值就必须给出处：{metric.get("metric")}')
    elif 'criteria' in option:
        problems.append(f'方案 {oid} 的 criteria 必须是对象')
    # expected_effect：确认性要两个数字且强度不小于分辨率；探索性可写观察 / 解释边界
    if isinstance(effect, dict):
        if mode == 'exploratory' and 'predicted_delta' not in effect \
                and 'scale_resolution' not in effect:
            pass                      # 探索性只写观察 / 边界，不强制两个数字
        else:
            for sub in ('quantity', 'unit', 'predicted_delta', 'scale_resolution',
                        'scale_resolution_source'):
                if effect.get(sub) in (None, '', [], {}):
                    problems.append(f'方案 {oid} 的 expected_effect 缺子字段 {sub}')
            delta, resolution = effect.get('predicted_delta'), effect.get('scale_resolution')
            for name, value in (('predicted_delta', delta), ('scale_resolution', resolution)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                    problems.append(f'方案 {oid} 的 expected_effect.{name} 必须是正数')
            if (isinstance(delta, (int, float)) and not isinstance(delta, bool)
                    and isinstance(resolution, (int, float)) and not isinstance(resolution, bool)
                    and 0 < delta < resolution):
                problems.append(f'方案 {oid} 的预期变化量 {delta} 小于尺子分辨率 {resolution}'
                                ' ⇒ 跑完只能得「说不清」；先加大强度或换抓手')
    elif 'expected_effect' in option:
        problems.append(f'方案 {oid} 的 expected_effect 必须是对象')
    elif mode == 'confirmatory':
        problems.append(f'方案 {oid} 是确认性判据，缺 expected_effect（预期变化量与尺子分辨率）')
    # 分支表：3–5 支，四类齐全，每支要有「能带回什么 + 下一步」
    branches = option.get('branches')
    if isinstance(branches, list):
        kinds = [b.get('kind') for b in branches if isinstance(b, dict)]
        if not 3 <= len(branches) <= 5:
            problems.append(f'方案 {oid} 的分支表要有 3–5 支，现在是 {len(branches)}')
        for kind in REQUIRED_BRANCH_KINDS:
            if kind not in kinds:
                problems.append(f'方案 {oid} 缺分支「{kind}」')
        for branch in branches:
            if not isinstance(branch, dict) or not branch.get('info') or not branch.get('next'):
                problems.append(f'方案 {oid} 有分支缺「信息」或「下一步」')
    elif 'branches' in option:
        problems.append(f'方案 {oid} 的 branches 必须是列表')
    # 题单/回归题：不强制重复次数；确认性要至少一条声明了重复次数
    tasklist = option.get('tasklist')
    if isinstance(tasklist, dict):
        regressions = tasklist.get('regression')
        if not isinstance(regressions, list) or not regressions:
            problems.append(f'方案 {oid} 的题单里没有回归对照题')
        else:
            counts = []
            for item in regressions:
                if not isinstance(item, dict) or not str(item.get('id') or '').strip():
                    problems.append(f'方案 {oid} 有回归题缺 id：{item}')
                    continue
                repeats = item.get('in_arm_repeats', 0)
                if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 0:
                    problems.append(f'方案 {oid} 的回归题 {item.get("id")} '
                                    'in_arm_repeats 必须是非负整数')
                    continue
                counts.append(repeats)
            if mode == 'confirmatory' and counts and max(counts) < 1:
                problems.append(f'方案 {oid} 是确认性设计，至少一条回归题要声明重复次数（≥1）'
                                ' —— 单次读数撑不起确认性结论')
            # 探索性：小样本可以不声明重复（counts 全 0 合法），⛔ 不为了对称硬凑重复
    elif 'tasklist' in option:
        problems.append(f'方案 {oid} 的 tasklist 必须是对象')
    # 改动指纹
    fingerprint = option.get('fingerprint')
    if isinstance(fingerprint, list):
        if not fingerprint:
            problems.append(f'方案 {oid} 的改动指纹为空')
    elif 'fingerprint' in option:
        problems.append(f'方案 {oid} 的 fingerprint 必须是列表')
    return problems


def _reader_view_problems(plan_set):
    problems = []
    view = plan_set.get('reader_view')
    if not isinstance(view, dict):
        return ['方案集缺 `reader_view`（面向用户的标题 / 摘要 / 小节 / 每选项说明 / 预算）'
                ' —— ⛔ 不从 `goal` / `brief` 里替你凑摘要']
    for key, label in (('title', '标题'), ('summary', '摘要')):
        if not _nonempty_str(view.get(key)):
            problems.append(f'`reader_view.{key}`（{label}）缺失或为空')
    sections = view.get('sections')
    if not isinstance(sections, list) or not sections:
        problems.append('`reader_view.sections` 缺失或不是非空列表（至少写一节）')
    else:
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                problems.append(f'`reader_view.sections[{index}]` 不是对象')
                continue
            if not _nonempty_str(section.get('title')):
                problems.append(f'`reader_view.sections[{index}].title` 缺失或为空')
            body = section.get('body')
            ok, why = _as_text(body) if body is not None else (False, '缺失')
            if not ok:
                problems.append(f'`reader_view.sections[{index}].body` {why}')
            elif not _nonempty(body):
                problems.append(f'`reader_view.sections[{index}].body` 为空')
    opt_ids = _option_ids(plan_set)
    choices = view.get('choices')
    if not isinstance(choices, list) or not choices:
        problems.append('`reader_view.choices` 缺失或不是非空列表（每个选项恰好一条）')
    else:
        seen = []
        for index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                problems.append(f'`reader_view.choices[{index}]` 不是对象')
                continue
            oid = str(choice.get('option_id') or '').strip()
            if not oid:
                problems.append(f'`reader_view.choices[{index}].option_id` 缺失')
            elif oid in seen:
                problems.append(f'`reader_view.choices[{index}].option_id` = {oid} 重复')
            elif oid not in opt_ids:
                problems.append(f'`reader_view.choices[{index}].option_id` = {oid} 不是本方案集的选项')
            else:
                seen.append(oid)
            for key, label in (('summary', '这条做什么'), ('tradeoff', '代价与取舍')):
                if not _nonempty_str(choice.get(key)):
                    problems.append(f'`reader_view.choices[{index}].{key}`（{label}）缺失或为空')
        missing = [oid for oid in opt_ids if oid not in seen]
        if missing:
            problems.append('`reader_view.choices` 漏了这些选项：' + '、'.join(missing))
    if not opt_ids:
        problems.append('方案集里一个带 `id` 的 options 都没有')
    budget = view.get('budget')
    if budget is None:
        problems.append('`reader_view.budget`（预算与用时）缺失')
    else:
        ok, why = _as_text(budget)
        if not ok:
            problems.append(f'`reader_view.budget` {why}')
        elif not _nonempty(budget):
            problems.append('`reader_view.budget` 为空')
    risks = view.get('risks')
    if risks not in (None, [], ''):
        ok, why = _as_text(risks)
        if not ok:
            problems.append(f'`reader_view.risks` {why}')
    arms = view.get('arms')
    if arms is not None:
        if not isinstance(arms, list):
            problems.append('`reader_view.arms` 必须是列表（没有多臂就不写）')
        else:
            for index, arm in enumerate(arms):
                if not isinstance(arm, dict):
                    problems.append(f'`reader_view.arms[{index}]` 不是对象')
                    continue
                for key, label in (('name', '名称'), ('settings', '设置'),
                                   ('purpose', '目的'), ('comparison', '对照')):
                    if not _nonempty_str(arm.get(key)):
                        problems.append(f'`reader_view.arms[{index}].{key}`（{label}）缺失或为空')
                ids = arm.get('option_ids')
                if not isinstance(ids, list) or not ids:
                    problems.append(f'`reader_view.arms[{index}].option_ids` 必须是非空列表')
                else:
                    bad = [str(x) for x in ids if str(x) not in opt_ids]
                    if bad:
                        problems.append(f'`reader_view.arms[{index}].option_ids` 引用了不存在的选项：'
                                        + '、'.join(bad))
    return problems


def readiness(plan_set, decision):
    """建页之前的一次只读判定。返回 `(ok, problems)`。

    判据：① `brief` 三段非空（B2：缺背景 / 上下文 / 进展就拒建）
    ② 每条方案 13 个字段齐全且自洽 ③ `reader_view` 合法
    ④ 决策里短码 / 收件人 / 待批范围齐备（要人回话就必须有短码）
    """
    problems = []
    if not isinstance(plan_set, dict):
        return False, ['方案集必须是对象']
    brief = plan_set.get('brief')
    if not isinstance(brief, dict):
        problems.append('方案集缺 `brief`（背景 / 上下文 / 进展三段）—— 只有决策点的申请不许发')
    else:
        for key, label in BRIEF_KEYS:
            value = brief.get(key)
            empty = (value is None or (isinstance(value, str) and not value.strip())
                     or (isinstance(value, (list, dict)) and not value))
            if empty:
                problems.append(f'`brief.{key}`（{label}）是空的 —— 要么写清，要么别开这次审批')
    options = plan_set.get('options')
    if not isinstance(options, list) or not options:
        problems.append('方案集里一个 `options` 都没有')
    else:
        for index, option in enumerate(options):
            problems.extend(_option_problems(option, index))
        ids = _option_ids(plan_set)
        if len(ids) != len(options):
            problems.append('方案编号有重复或缺失（每个选项必须有唯一编号）')
    problems.extend(_reader_view_problems(plan_set))
    decision = decision or {}
    if not str(decision.get('short_code') or '').strip():
        problems.append('决策里没有 `short_code`（短码）⇒ 用户没法回话')
    if not _nonempty_str(decision.get('recipient')):
        problems.append('决策里没有 `recipient`（收件人）⇒ 回执与回复都无法归属')
    undecided = list(decision.get('undecided') or [])
    if decision.get('needs_user') is True:
        if not undecided:
            problems.append('声明需要人判断，但 `undecided` 为空（没有一条待批）')
        unknown = [item for item in undecided if str(item) not in _option_ids(plan_set)]
        if unknown:
            problems.append('`undecided` 里有不在方案集的编号：' + '、'.join(map(str, unknown)))
    return (not problems), problems


# ────────────────────────────── 文本渲染 ──────────────────────────────

def _inline(value):
    text = _esc(value)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text


def _block(value):
    if isinstance(value, (list, tuple)):
        items = ''.join(f'<li>{_inline(item)}</li>' for item in value if str(item).strip())
        return f'<ul>{items}</ul>' if items else ''
    lines = [line for line in str(value or '').splitlines()]
    out, paragraph, bullets = [], [], []
    for line in lines:
        if line.startswith('- '):
            bullets.append(line[2:])
            continue
        if bullets:
            out.append('<ul>' + ''.join(f'<li>{_inline(item)}</li>' for item in bullets) + '</ul>')
            bullets = []
        if not line.strip():
            if paragraph:
                out.append('<p>' + _inline('\n'.join(paragraph)) + '</p>')
                paragraph = []
            continue
        paragraph.append(line)
    if bullets:
        out.append('<ul>' + ''.join(f'<li>{_inline(item)}</li>' for item in bullets) + '</ul>')
    if paragraph:
        out.append('<p>' + _inline('\n'.join(paragraph)) + '</p>')
    return ''.join(out)


def _field_rows(key, value):
    """一个方案字段 → 若干行纯文本（渲染进 `data-field` 行）。"""
    if key == 'fingerprint' and isinstance(value, list):
        return [str(item.get('lever', '')) + '：' + str(item.get('from_to', ''))
                if isinstance(item, dict) else str(item) for item in value]
    if key == 'branches' and isinstance(value, list):
        rows = [f"{branch.get('kind')} → {branch.get('next')}"
                for branch in value if isinstance(branch, dict)]
        kinds = [branch.get('kind') for branch in value if isinstance(branch, dict)]
        missing = [kind for kind in REQUIRED_BRANCH_KINDS if kind not in kinds]
        if missing:
            rows.append(f'⚠️ 缺分支 {missing}')
        return rows
    if key == 'criteria' and isinstance(value, dict):
        rows = [f"{metric.get('metric')} {metric.get('threshold')}（出处 {metric.get('source')}）"
                for metric in (value.get('metrics') or []) if isinstance(metric, dict)]
        return rows or [canonical_json(value)]
    if key == 'cost' and isinstance(value, dict):
        return [' · '.join(f'{k}={v}' for k, v in value.items())]
    if key == 'expected_effect' and isinstance(value, dict):
        return [' · '.join(f'{k}={v}' for k, v in value.items())]
    if key == 'expected_pass' and isinstance(value, list):
        return ['、'.join(str(item) for item in value) or '（空数组：本设计无可声明的预期）']
    if key == 'tasklist' and isinstance(value, dict):
        return [' · '.join(f'{k}={v}' for k, v in value.items())]
    return _lines(value)


def _compact_value(value, limit=220):
    """把执行规范里的一个字段压成一行可读摘要（截图/手机上能读完）。"""
    if isinstance(value, dict):
        text = ' · '.join(f'{key}={_compact_value(item, 60)}' for key, item in value.items())
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            text = '、'.join(str(item) for item in value)
        else:
            head = '；'.join(_compact_value(item, 60) for item in value[:3])
            text = f'{len(value)} 项：{head}' + (' …' if len(value) > 3 else '')
    else:
        text = str(value)
    return text[:limit] + ('…' if len(text) > limit else '')


def _execution_rows(execution):
    """执行计划 → [(标签, 一行摘要)]。命令永远排第一：用户第一眼看到要跑什么。"""
    rows = []
    argv = execution.get('execution_argv')
    if argv not in (None, '', [], {}):
        command = ' '.join(str(item) for item in argv) if isinstance(argv, list) else str(argv)
        rows.append(('实际命令', command))
    implementation = execution.get('implementation')
    if implementation not in (None, '', [], {}):
        rows.append(('实现', _compact_value(implementation)))
    budget = execution.get('budget')
    if budget not in (None, '', [], {}):
        rows.append(('预算与上限', _compact_value(budget)))
    case_sets = execution.get('case_sets')
    if case_sets not in (None, '', [], {}):
        rows.append(('题集', _compact_value(case_sets)))
    research = execution.get('research')
    if isinstance(research, dict):
        if research.get('goal'):
            rows.append(('研究目标', _compact_value(research.get('goal'))))
        if research.get('hypothesis'):
            rows.append(('要验证的假设', _compact_value(research.get('hypothesis'))))
    return rows


def build_page_html(plan_set, decision, *, evidence=None, aggregate='', generated_at=None,
                    execution=None):
    """渲染自包含审批页。**调用方先过 `readiness()`**（本函数不重复判，也不吞异常）。

    `execution` 是**完整执行规范**（可选）。给了就渲染「实际执行计划」一节：
    命令 / 实现 / 预算 / 题集 / 指纹，并在页里写明它与方案集分开绑定。
    """
    ok, problems = readiness(plan_set, decision)
    if not ok:
        raise ReadinessError(problems)
    view = plan_set.get('reader_view') or {}
    brief = plan_set.get('brief') or {}
    dec = dict(decision or {})
    opt_ids = _option_ids(plan_set)
    undecided = [str(item) for item in (dec.get('undecided') or [])]
    short_code = str(dec.get('short_code') or '')
    expires = dec.get('expires_at') or plan_set.get('valid_until') or ''
    expires_text = _esc(expires)
    state = str(dec.get('status') or 'awaiting_approval')
    generated = generated_at or _iso(time.time())
    what_by_id = {str(o.get('id')): o.get('what') for o in plan_set.get('options') or []
                  if isinstance(o, dict)}
    by_id = {str(c.get('option_id')): c for c in (view.get('choices') or [])
             if isinstance(c, dict)}
    parts = []
    add = parts.append

    add("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    add("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    add(f"<title>{_esc(view.get('title') or '审批')} · {_esc(dec.get('decision_id'))}</title>")
    add("<style>"
        "body{font:15px/1.7 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
        "max-width:960px;margin:0 auto;padding:20px;color:#1a1a1a;background:#fafafa}"
        "h1{font-size:22px;margin:0 0 6px}h2{font-size:18px;margin:26px 0 8px;"
        "border-left:4px solid #2b6cb0;padding-left:10px}h3{font-size:16px;margin:16px 0 6px}"
        "h4{font-size:15px;margin:12px 0 6px}.meta{color:#555;font-size:13px;margin-bottom:16px}"
        ".box{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;margin:10px 0}"
        ".choose{background:#fffbea;border:1px solid #f6c343}"
        ".code{font:20px/1.4 ui-monospace,Consolas,monospace;font-weight:700;letter-spacing:2px}"
        "table.fields{width:100%;border-collapse:collapse;margin:6px 0 12px}"
        "table.fields th{text-align:left;vertical-align:top;width:170px;color:#444;"
        "font-weight:600;padding:4px 8px 4px 0;border-top:1px solid #eee}"
        "table.fields td{padding:4px 0;border-top:1px solid #eee}"
        ".tablewrap{overflow-x:auto;margin:6px 0 12px;max-width:100%}"
        "table.arms{width:100%;min-width:620px;border-collapse:collapse;background:#fff}"
        "table.arms th,table.arms td{border:1px solid #e2e8f0;padding:6px 8px;text-align:left;"
        "vertical-align:top;font-size:14px}table.arms th{background:#f1f5f9}"
        ".k{color:#94a3b8;font-weight:400;font-size:12px;margin-left:6px}"
        ".dim{color:#94a3b8}.opt{border:1px solid #e2e8f0;border-radius:8px;padding:8px 14px;"
        "background:#fff;margin:10px 0}"
        "code{background:#eef2f7;padding:1px 4px;border-radius:3px;overflow-wrap:anywhere;"
        "word-break:break-word}"
        "ol.choices{padding-left:22px}li.choice{margin:10px 0}.warn{color:#b7791f}"
        ".badge{font-size:12px;border-radius:3px;padding:1px 6px;margin-left:6px;"
        "border:1px solid #cbd5e1;color:#334155;background:#f1f5f9}"
        "details#audit-appendix{margin:24px 0 0;border:1px dashed #cbd5e1;border-radius:8px;"
        "padding:8px 14px;background:#f8fafc}"
        "details#audit-appendix>summary{cursor:pointer;color:#475569;font-size:14px}"
        "</style></head><body>")

    add(f"<h1>{_esc(view.get('title'))}</h1>")
    add(f"<div class='meta'>{_esc(plan_set.get('exp_id'))} · 决策 {_esc(dec.get('decision_id'))}"
        f" · 状态 <b>{_esc(STATE_TEXT.get(state, state))}</b>"
        f"　·　短码 <span class='code'>{_esc(short_code)}</span>"
        f"　·　有效期到 {expires_text}</div>")
    add(f"<div class='box'><b>这一轮要你决定什么：</b>{_block(view.get('summary'))}</div>")

    for index, section in enumerate(view.get('sections') or [], 1):
        if not isinstance(section, dict):
            continue
        add(f"<h2 id='rv-section-{index}'>{_esc(section.get('title'))}</h2><div class='box'>")
        add(_block(section.get('body')) or "<p class='dim'>（本节未写内容）</p>")
        add('</div>')

    arms = view.get('arms')
    if isinstance(arms, list) and arms:
        add("<h2 id='arms'>对照：每一臂各改了什么</h2><div class='tablewrap'><table class='arms'>"
            "<thead><tr><th>名称</th><th>设置</th><th>目的</th><th>与谁对照</th><th>对应选项</th>"
            "</tr></thead><tbody>")
        for arm in arms:
            add("<tr>"
                f"<td><b>{_esc(arm.get('name'))}</b></td>"
                f"<td>{_inline(arm.get('settings'))}</td>"
                f"<td>{_inline(arm.get('purpose'))}</td>"
                f"<td>{_inline(arm.get('comparison'))}</td>"
                f"<td>{_esc('、'.join(str(x) for x in (arm.get('option_ids') or [])))}</td>"
                "</tr>")
        add('</tbody></table></div>')

    add("<h2 id='budget'>预算与用时</h2><div class='box'>")
    add(_block(view.get('budget')) or "<p class='dim'>（未提供）</p>")
    add('</div>')
    if _nonempty(view.get('risks')):
        add("<h2 id='risks'>风险与不做的事</h2><div class='box'>")
        add(_block(view.get('risks')))
        add('</div>')

    # ③ 实际执行计划：批准后**真跑**的东西。与方案集分开绑定，改了任何一项批准即失效。
    if isinstance(execution, dict) and execution:
        execution_sha = execution_fingerprint(execution)
        add("<h2 id='execution'>实际执行计划（批准后真跑的东西）</h2>"
            f"<div class='box' data-execution-sha='{_esc(execution_sha)}'>")
        add("<p class='dim'>这一节取自 prepare 时的完整执行规范，与右侧方案集分开绑定："
            "改命令 / 实现 / 预算 / 题集里的任何一项，这次批准即失效（要重新申请）。</p>")
        add("<table class='fields'>")
        for label, summary in _execution_rows(execution):
            add(f"<tr data-exec='{_esc(label)}'><th>{_esc(label)}</th>"
                f"<td><code>{_esc(summary)}</code></td></tr>")
        add('</table>')
        add(f"<p class='dim'>执行规范指纹 <code>{_esc(execution_sha)}</code></p>")
        add('</div>')
    else:
        add("<h2 id='execution'>实际执行计划（批准后真跑的东西）</h2>"
            "<div class='box warn'>")
        add("<p>这份申请<b>没有绑定实际执行计划</b>（prepare 时没给完整执行规范）。"
            "它只能作为方案讨论与人工执行依据，<b>不能</b>用来批准 Workflow 起跑 ——"
            "要用它起跑，必须重新准备一次并带上完整执行规范。</p>")
        add('</div>')

    # ④ choose：待批范围 / 每个选项 / 短码 / 回复语法 / 超时机制
    add("<h2 id='choose'>要你选什么（回复我）</h2><div class='box choose'>")
    add(f"<p>待批范围：<b>{_esc('、'.join(undecided) or '（空）')}</b></p>")
    add("<ol class='choices'>")
    for oid in opt_ids:
        choice = by_id.get(oid)
        what = what_by_id.get(oid) or '（方案集里没写「做什么」）'
        badge = ("<span class='badge'>本轮待批</span>" if oid in undecided
                 else "<span class='badge'>不在本轮待批范围</span>")
        add(f"<li class='choice' data-choice='{_esc(oid)}'><b>{_esc(oid)}｜{_esc(what)}</b>{badge}")
        if choice is None:
            add("<div class='dim'>（这一条没有面向用户的说明）</div></li>")
            continue
        add(f"<div>{_inline(choice.get('summary'))}</div>")
        add(f"<div class='dim'>取舍：{_inline(choice.get('tradeoff'))}</div></li>")
    add('</ol>')
    add(f"<p>短码：<span class='code'>{_esc(short_code)}</span>　·　有效期到：{expires_text}</p>")
    if undecided:
        first = _esc(undecided[0])
        if len(undecided) == 1:
            add(f"<p>回复：<code>&lt;短码&gt; {first}</code></p>")
        else:
            all_ids = '+'.join(_esc(item) for item in undecided)
            add("<p>回复："
                f"<code>&lt;短码&gt; {first}</code> 批一条　·　"
                f"<code>&lt;短码&gt; {_esc(undecided[0])}+{_esc(undecided[1])}</code> 批多条　·　"
                f"<code>&lt;短码&gt; {all_ids}</code> 全部</p>")
        add("<p>打回：<code>改" + first + " &lt;一句&gt;</code>　·　"
            "全否：<code>N</code>　·　停机：<code>S</code></p>")
    else:
        add("<p class='warn'>本轮没有待批编号，不需要回复。</p>")
    add(f"<p>超时：<b>{int(APPROVAL_TIMEOUT_SECONDS / 3600)} 小时</b>内没有明确批准 ⇒ 自动取消；"
        "取消不消费短码，也不按推荐项自动推进。</p>")
    add('</div>')

    # ④ 审计附录（默认折叠）：六块里的三段 brief、options、evidence 都在这儿
    add(f"<details id='{APPENDIX_ID}'>")
    add("<summary>审计附录：背景 / 上下文 / 进展 · 方案集 13 个字段 · 证据索引"
        "（默认折叠；前台只讲要决定的事）</summary><div class='appendix'>")
    for key, label in BRIEF_KEYS:
        add(f"<h3 id='brief-{key}' data-brief='{key}'>{_esc(label)}</h3><div class='box'>")
        for line in _lines(brief.get(key)):
            add(f'<p>{_inline(line)}</p>')
        add('</div>')
    add("<h2 id='options'>方案集原文（每条 13 个字段全在这里）</h2>")
    add(f"<div class='meta'>问题族 {_esc(plan_set.get('question_family'))}"
        f"　· 基线 {_esc(plan_set.get('from_baseline'))}"
        f"　· 改动面 {_esc('/'.join(str(x) for x in (plan_set.get('changed_faces') or [])))}"
        f"　· 版本 {_esc(plan_set.get('version') or 'v1')}</div>")
    for option in plan_set.get('options') or []:
        oid = str(option.get('id') or '?')
        add(f"<div class='opt' data-option='{_esc(oid)}'>")
        add(f"<h4>{_esc(oid)}｜{_esc(option.get('what'))}</h4><table class='fields'>")
        for key, _label in OPTION_FIELDS:
            if key == 'id':
                continue
            rows = _field_rows(key, option.get(key))
            body = '<br>'.join(_inline(row) for row in rows) if rows else '<span class="dim">（空）</span>'
            add(f"<tr data-field='{_esc(key)}'><th>{_esc(dict(OPTION_FIELDS)[key])}"
                f"<span class='k'>{_esc(key)}</span></th><td>{body}</td></tr>")
        add('</table></div>')
    if aggregate:
        add(f"<div class='box'><b>合计：</b>{_inline(aggregate)}</div>")
    add("<h2 id='evidence'>证据索引</h2><div class='box'>")
    items = evidence or []
    if not items:
        add("<p class='dim'>本轮<b>未提供</b>证据索引（调用方没给 evidence）⇒ 这里不列路径，"
            '也不声称有文件可核。</p>')
    else:
        add('<ul>')
        for item in items:
            if isinstance(item, dict):
                mark = '✅' if item.get('exists') else '❌'
                note = f"　{_esc(item.get('note'))}" if item.get('note') else ''
                add(f"<li>{mark} <code>{_esc(item.get('path'))}</code>{note}</li>")
            else:
                add(f"<li><code>{_esc(item)}</code></li>")
        add('</ul>')
    add('</div>')
    facts = {key: dec.get(key) for key in ('decision_id', 'status', 'resolution',
                                          'recommended', 'undecided', 'approved',
                                          'short_code', 'expires_at') if key in dec}
    add("<h2 id='decision-facts'>本轮决策的原始字段（机读）</h2><table class='fields'>")
    for key, value in facts.items():
        shown = canonical_json(value) if isinstance(value, (dict, list)) else value
        add(f"<tr><th>{_esc(key)}</th><td><code>{_esc(shown)}</code></td></tr>")
    add('</table>')
    add(f"<p class='dim'>方案集 sha256 <code>{_esc(plan_set.get('sha256') or '—')}</code>"
        f"（规范化摘要；短码由它派生）</p>")
    add('</div></details>')
    add(f"<p class='dim'>本页由 <code>kernel/autoresearch/decisions.py</code> 生成于 {_esc(generated)}"
        '（自包含单文件，零外链）。</p>')
    add('</body></html>')
    return '\n'.join(parts) + '\n'


def page_problems(page_html, plan_set, decision, execution=None):
    """机器判据：六块在不在、是否自包含、每条方案的 13 个字段是否都在、执行规范是否绑对。"""
    problems = []
    text = str(page_html or '')
    if not text.strip():
        return ['审批页为空']
    for block in REQUIRED_PAGE_BLOCKS:
        count = text.count(f"id='{block}'")
        if count != 1:
            problems.append(f"块 `{block}` 出现 {count} 次（必须恰好一次）")
    low = text.lower()
    for token in FORBIDDEN_PAGE_TOKENS:
        if token in low:
            problems.append(f'页里出现外部依赖 / 不安全片段：{token.strip()}')
    if text.count("id='execution'") != 1:
        problems.append("页里必须有且只有一个「实际执行计划」块（id='execution'）")
    if isinstance(execution, dict) and execution:
        want = f"data-execution-sha='{_esc(execution_fingerprint(execution))}'"
        if want not in text:
            problems.append('页里的执行规范指纹与登记的完整执行规范不符')
        for label, _summary in _execution_rows(execution):
            if f"data-exec='{_esc(label)}'" not in text:
                problems.append(f'执行计划缺「{label}」一行')
    elif "没有绑定实际执行计划" not in text:
        problems.append('没有执行规范时必须在页里明说「没有绑定实际执行计划」')
    for option in plan_set.get('options') or []:
        oid = str(option.get('id') or '')
        if f"data-option='{_esc(oid)}'" not in text:
            problems.append(f'方案 {oid} 没有渲染块')
            continue
        for key in OPTION_FIELD_KEYS:
            if key == 'id':
                continue
            if f"data-field='{_esc(key)}'" not in text:
                problems.append(f'方案 {oid} 缺字段渲染：{key}')
    short_code = str((decision or {}).get('short_code') or '')
    if short_code and short_code not in text:
        problems.append('页里没有短码')
    expires = (decision or {}).get('expires_at')
    if expires and _esc(expires) not in text:
        problems.append('页里没有有效期')
    return problems


# ────────────────────────────── 服务 ──────────────────────────────

class DecisionService:
    """决策链的可调用入口。所有写操作都在 store 的事务里，读操作只读。

    构造参数：
        store      `runtime.store.Store`（同一份 sqlite）；也可以是 `ReadonlyStore`
        delivery   可选的 `runtime.delivery.Delivery`（不给就新建，共用同一时钟）
        inbox      可选的 `runtime.inbox.Inbox`（不给就按同一库新建；身份校验器由 authenticate 注入）
        channel    **可选的注入通道**。不配 ⇒ 发送类动作一律 fail-closed；离线建页 / 诊断照常可用。
        clock      时间源（测试注入假时钟）
        authenticate  身份校验器 `(source, event) -> principal`；用来判审批与回复的归属
        readonly   `True` ⇒ **只读构造**：不建表、不建 `Accounts`、写方法一律拒绝。
                   也可以直接用工厂 `DecisionService.readonly(db)`。

    ## 只读构造（诊断入口专用）

    `status` / `authorization_check`（`authorize-check`）是**明确只读**的入口，但写构造会在
    构造期 `CREATE TABLE IF NOT EXISTS` ⇒ 「只读看一眼」会把库建出来、把旧 schema 迁移掉。
    只读构造修的就是这件事：

    * store 换成本地同一路径的 `ReadonlyStore`（`mode=ro` + `query_only`），⛔ 不复制数据库；
    * 跳过 `_create_tables()`、跳过 `Accounts`（只读查询用不到预算台账）；
    * `Delivery` / `Inbox` 用 `readonly=True` 构造（跳过 DDL，Inbox 允许 `accounts=None`）；
    * 任何写方法（prepare / send / tick / ingest / reply…）抛 `ReadonlyServiceError`；
    * 缺库 / 缺表 / 旧 schema ⇒ 读方法返回「不可判」（`ok=False` + `problems`），
      ⛔ 不建库、不迁移、不把未知表读成空。
    """

    def __init__(self, store, delivery=None, inbox=None, channel=None, *,
                 clock=None, authenticate=None, readonly=False):
        # 路径直接传进来是误用：只读路径走 `DecisionService.readonly(db)`，写路径先造 Store。
        if store is None or isinstance(store, (str, bytes, Path)):
            raise DecisionError(
                'DecisionService 需要一个 store 实例（runtime.store.Store 或 ReadonlyStore）；'
                '给路径时只读请用 DecisionService.readonly(db)，要写请先 Store(db)')
        # 只读判据只认对象声明的标志 / 缺少写事务；⛔ 不从路径或文件权限猜。
        self.readonly = bool(readonly) or is_readonly_store(store) \
            or not hasattr(store, 'transaction')
        if self.readonly and not isinstance(store, ReadonlyStore):
            # 同一路径的只读句柄：不复制数据库，也不迁移 schema。
            store = ReadonlyStore(getattr(store, 'db_path', store))
        self.store = store
        self.clock = clock or time.time
        if self.readonly and delivery is not None and not is_readonly_store(delivery.store):
            raise DecisionError('只读 DecisionService 需要只读 Delivery')
        self.delivery = delivery or Delivery(store, clock=self.clock)
        # 身份校验器：显式注入优先；否则用通道自带的那一个（适配层最清楚自己平台的事件
        # 该怎么核）。两者都没有 ⇒ 审批与回复一律 fail-closed，⛔ 核心不自己判身份。
        if authenticate is None and channel is not None:
            candidate = getattr(channel, 'authenticate', None)
            if callable(candidate):
                authenticate = candidate
        if inbox is not None and bool(getattr(inbox, 'readonly', False)) != self.readonly:
            raise DecisionError('DecisionService 与 Inbox 的读写模式必须一致')
        if self.readonly and inbox is not None and inbox.accounts is not None:
            raise DecisionError('只读 DecisionService 的 Inbox 不应构造 Accounts')
        if inbox is None:
            # 只读：不建 Accounts（只读查询不需要预算台账），Inbox 也不建表。
            accounts = None if self.readonly else Accounts(store)
            inbox = Inbox(store, accounts, self.delivery, authenticate=authenticate,
                          readonly=self.readonly)
        elif authenticate is not None and inbox.authenticate is None:
            inbox.authenticate = authenticate
        self.inbox = inbox
        self.channel = channel
        #: 通道自述的发送方身份（`describe()`）。**由核心计算指纹**，不采信适配层自报。
        self.sender = None
        self._sender_fingerprint = ''
        if str(self.delivery.store.db_path) != str(store.db_path):
            raise DecisionError('DecisionService、Delivery 必须共用数据库')
        self.delivery.receipt_verifier = self._verify_receipt
        # 回复只认本服务认定的**文字**事件：卡片点击不是文本回复对象（D4）。
        self.delivery.event_verifier = self.verify_reply_event
        if not self.readonly:
            self._create_tables()

    @classmethod
    def readonly(cls, db, *, clock=None, authenticate=None):
        """只读工厂：`DecisionService.readonly(db)`。

        `db` 可以是路径字符串 / `Path` / 现有的 `Store` / `ReadonlyStore`；
        一律换成同一路径的 `ReadonlyStore`。⛔ 不建库、不建表、不建 `Accounts`。
        """
        store = db if isinstance(db, ReadonlyStore) else ReadonlyStore(
            getattr(db, 'db_path', db))
        return cls(store, clock=clock, authenticate=authenticate, readonly=True)

    # ── 只读保护 ──
    def _require_write(self, action):
        """只读服务上的写动作一律拒绝：⛔ 不降级成「只读事务」、不静默成功。"""
        if self.readonly:
            raise ReadonlyServiceError(
                f'只读 DecisionService 拒绝 {action}：只读入口只做 status / authorize-check 查询；'
                '写操作请用可写构造（DecisionService(store)）')

    #: 只读查询至少要有的表（缺任何一个都判「不可判」，⛔ 不当作「没有记录」）。
    READONLY_REQUIRED_TABLES = ('decisions', 'decision_options', 'decision_codes',
                                'decision_deliveries', 'decision_events',
                                'delivery', 'delivery_receipts', 'inbox_events')

    #: 只读查询会读到的列。旧 schema **缺列**也判不可判：⛔ 不迁移、不按新结构补。
    READONLY_REQUIRED_COLUMNS = (
        ('decisions', ('decision_id', 'exp_id', 'requested_at', 'expires_at', 'status',
                       'resolution', 'short_code', 'recipient', 'recommended', 'needs_user',
                       'scope', 'owner_session', 'judgment_reason', 'payload_json',
                       'payload_hash', 'page_path', 'page_sha256', 'page_bytes', 'version',
                       'decided_at', 'decided_source', 'decided_event_id', 'created_at',
                       'updated_at')),
        ('decision_options', ('decision_id', 'option_id', 'state', 'position')),
        ('decision_codes', ('code', 'decision_id', 'exp_id', 'state', 'consumed_by',
                            'consumed_at')),
        ('decision_deliveries', ('decision_id', 'kind', 'request_key', 'delivery_id')),
        ('decision_events', ('id', 'decision_id', 'kind', 'actor', 'from_state', 'to_state',
                             'payload_json', 'at')),
        ('delivery', ('id', 'exp_id', 'kind', 'request_key', 'wire_key', 'payload_json',
                      'payload_hash', 'status', 'owner', 'fence', 'lease_until', 'attempts',
                      'message_id', 'error', 'created_at', 'updated_at')),
        ('delivery_receipts', ('delivery_id', 'exp_id', 'receipt_json', 'acked_at')),
        ('inbox_events', ('source', 'event_id', 'exp_id', 'payload_json', 'payload_hash',
                          'status', 'category', 'principal', 'consumed', 'result_json',
                          'error', 'event_time', 'received_at', 'classified_at', 'applied_at')),
    )

    def _readonly_problem(self):
        """只读模式下「为什么读不了」的统一原因；可读时返回 None。

        ⛔ 缺库不替你建库、缺表不替你迁移、旧 schema 不补列：一律报不可判。
        ⛔ 也包括 WAL 边界（`-wal` 有、`-shm` 缺）、不是 SQLite 文件、主库在打开期间被写：
        这些由 `ReadonlyStore.connect()` 抛 `StoreError`，这里统一转成不可判，⛔ 不建文件。
        """
        if not self.readonly:
            return None
        if not Path(self.store.db_path).is_file():
            return (f'状态库不存在：{self.store.db_path}（只读模式不建库）⇒ 不可判；'
                    '先运行 init / prepare 建库')
        try:
            schema = self.store.schema()
        except (StoreError, sqlite3.Error) as exc:
            return f'状态库不可读（{type(exc).__name__}: {exc}）⇒ 不可判'
        missing = [name for name in self.READONLY_REQUIRED_TABLES if name not in schema]
        if missing:
            return ('状态库缺少决策台账表：' + '、'.join(missing)
                    + '（只读模式不迁移 schema）⇒ 不可判；这条库不是当前版本，先迁移再查')
        stale = []
        for table, columns in self.READONLY_REQUIRED_COLUMNS:
            present = schema.get(table, frozenset())
            absent = [column for column in columns if column not in present]
            if absent:
                stale.append(f'{table} 缺列 {", ".join(absent)}')
        if stale:
            return ('状态库是旧 schema：' + '；'.join(stale)
                    + '（只读模式不迁移）⇒ 不可判；先迁移到当前版本再查')
        return None

    # ── 建表 ──
    def _create_tables(self):
        with self.store.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS decisions(
                decision_id TEXT PRIMARY KEY,
                exp_id TEXT NOT NULL REFERENCES experiments(id),
                requested_at REAL NOT NULL, expires_at REAL NOT NULL,
                status TEXT NOT NULL, resolution TEXT,
                short_code TEXT NOT NULL, recipient TEXT NOT NULL,
                recommended TEXT, needs_user INTEGER NOT NULL,
                scope TEXT NOT NULL, owner_session TEXT,
                judgment_reason TEXT NOT NULL,
                payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                page_path TEXT NOT NULL, page_sha256 TEXT NOT NULL, page_bytes INTEGER NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                decided_at REAL, decided_source TEXT, decided_event_id TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS decision_options(
                decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                option_id TEXT NOT NULL, state TEXT NOT NULL, position INTEGER NOT NULL,
                PRIMARY KEY(decision_id, option_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS decision_codes(
                code TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                exp_id TEXT NOT NULL, state TEXT NOT NULL,
                consumed_by TEXT, consumed_at REAL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS decision_deliveries(
                decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                kind TEXT NOT NULL, request_key TEXT NOT NULL,
                delivery_id INTEGER NOT NULL REFERENCES delivery(id),
                PRIMARY KEY(decision_id, kind))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS decision_events(
                id INTEGER PRIMARY KEY, decision_id TEXT NOT NULL,
                kind TEXT NOT NULL, actor TEXT NOT NULL,
                from_state TEXT, to_state TEXT, payload_json TEXT NOT NULL,
                at REAL NOT NULL)""")
            for operation in ('UPDATE', 'DELETE'):
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS decision_events_no_{operation.lower()}
                    BEFORE {operation} ON decision_events BEGIN
                    SELECT RAISE(ABORT,'decision events are append-only'); END""")

    # ── 读 ──
    def _row(self, decision_id):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                               (str(decision_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _require(self, decision_id):
        row = self._row(decision_id)
        if row is None:
            raise DecisionError(f'决策不存在：{decision_id}')
        return row

    def _code_row(self, code):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT * FROM decision_codes WHERE code=?', (str(code),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── 状态机 ──
    def _transition(self, conn, row, to_state, *, resolution=None, actor='decisions',
                    reason='', extra=None):
        current = row['status']
        if to_state != current and to_state not in TRANSITIONS.get(current, frozenset()):
            raise DecisionError(f'非法的决策状态转移：{current} → {to_state}')
        payload = {'reason': reason, 'resolution': resolution}
        if extra:
            payload.update(extra)
        conn.execute('UPDATE decisions SET status=?, resolution=coalesce(?,resolution),'
                     'version=version+1, decided_at=coalesce(?,decided_at), updated_at=?'
                     ' WHERE decision_id=?',
                     (to_state, resolution, self.clock() if resolution else None,
                      self.clock(), row['decision_id']))
        conn.execute('INSERT INTO decision_events(decision_id,kind,actor,from_state,to_state,'
                     'payload_json,at) VALUES(?,?,?,?,?,?,?)',
                     (row['decision_id'], 'transition', actor, current, to_state,
                      canonical_json(payload), self.clock()))
        self.store.emit(conn, row['exp_id'], 'decision_' + to_state, actor,
                        {'decision_id': row['decision_id'], 'from': current,
                         'to': to_state, 'resolution': resolution, 'reason': reason})

    def _quarantine(self, row, reason, *, delivery_id=None):
        with self.store.transaction() as conn:
            fresh = conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                 (row['decision_id'],)).fetchone()
            if fresh is None:
                raise DecisionError('决策不存在')
            fresh = dict(fresh)
            if fresh['status'] == 'quarantined':
                return fresh
            self._transition(conn, fresh, 'quarantined', actor='decisions',
                             reason=f'交付结果不确定，隔离不重发：{reason}',
                             extra={'delivery_id': delivery_id, 'auto_resend': False})
            conn.execute('INSERT INTO decision_events(decision_id,kind,actor,from_state,to_state,'
                         'payload_json,at) VALUES(?,?,?,?,?,?,?)',
                         (fresh['decision_id'], 'quarantine', 'decisions', fresh['status'],
                          'quarantined', canonical_json({'reason': reason,
                                                         'delivery_id': delivery_id}),
                          self.clock()))
            return dict(conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                     (fresh['decision_id'],)).fetchone())

    # ── 准备 ──
    def prepare(self, *, exp_id, decision, plan_set, page_dir=None, evidence=None,
                aggregate='', now=None, execution_plan=None):
        """建决策记录并生成自包含审批页。缺件 ⇒ `ReadinessError`，一个字节都不写。

        `plan_set` 是**审批页的方案集**（可以是完整执行规范里的 `research` 子对象）。
        `execution_plan` 是**完整执行规范**（Workflow 起跑要用的那一份），可选：

        - 给了 ⇒ 记下它的规范化指纹，large/submission 的准入只认它（改 argv / 实现 /
          预算 / 题集里的任何一项都会失效）；
        - 没给，但 `plan_set` 本身带执行字段（`execution_argv` / `implementation` /
          `case_sets` / `research`）⇒ 就用它当执行规范（一份完整 plan 直接传进来即可）；
        - 都没有 ⇒ 只生成页，**不能**用于 Workflow 起跑（准入会明说缺 execution binding）。
        """
        self._require_write('prepare（建决策与审批页）')
        require_text(exp_id, 'exp_id')
        if not EXP_RE.fullmatch(exp_id):
            raise DecisionError('实验号形态错误（应当形如 exp-01）')
        decision = dict(decision or {})
        decision_id = str(decision.get('decision_id') or '').strip()
        if not DECISION_ID_RE.match(decision_id):
            raise DecisionError('决策号形态错误（应当形如 E014.2）')
        recipient = str(decision.get('recipient') or '').strip()
        short_code = str(decision.get('short_code') or derive_short_code(exp_id, decision_id)).upper()
        if not SHORT_CODE_RE.match(short_code):
            raise DecisionError('短码必须是 4–12 位大写字母或数字')
        needs_user = decision.get('needs_user')
        if needs_user is None:
            raise DecisionError('必须显式声明 `needs_user`（要不要人判断）')
        reason = str(decision.get('reason') or '').strip()
        if not reason:
            raise DecisionError('必须写明判断理由（`reason`）：需要人判断为什么需要，'
                                '不需要为什么不需要')
        if needs_user is False and (decision.get('undecided') or []):
            raise DecisionError('声明不需要人判断，却给了待批编号 ⇒ 两件事矛盾')
        scope = str(decision.get('scope') or 'large').strip().lower()
        if scope not in SCOPES:
            raise DecisionError(f'审批范围非法：{scope!r}（只能是 {"/".join(SCOPES)}）')
        owner_session = str(decision.get('owner_session') or '').strip()
        if SCOPE_POLICY_REQUIRES_APPROVAL[scope] and needs_user and not owner_session:
            raise DecisionError(
                f'{scope} 范围需要人拍板 ⇒ 必须写明 `owner_session`：'
                '批准要绑定到提出申请的会话，否则事后无法追溯它是批给谁的')
        now = float(now if now is not None else self.clock())
        normalized = {'decision_id': decision_id, 'exp_id': exp_id,
                      'short_code': short_code, 'recipient': recipient,
                      'needs_user': bool(needs_user), 'scope': scope,
                      'owner_session': owner_session,
                      'undecided': [str(x) for x in (decision.get('undecided') or [])],
                      'recommended': decision.get('recommended'),
                      'status': 'awaiting_approval',
                      'expires_at': _iso(now + APPROVAL_TIMEOUT_SECONDS)}
        # 审批页正文：完整 plan 带合法方案集时取 research，否则用原对象。
        approval_plan = page_plan(plan_set)
        ok, problems = readiness(approval_plan, normalized)
        if not ok:
            raise ReadinessError(problems)
        # 执行规范：显式传入优先；否则完整 plan 自带执行字段时用它自己。
        binding = execution_plan
        if binding is None and looks_like_execution_plan(plan_set):
            binding = plan_set
        if binding is not None and not isinstance(binding, dict):
            raise DecisionError('execution_plan 必须是 JSON 对象')
        if binding is not None:
            execution_sha = execution_fingerprint(binding)
        else:
            execution_sha = ''

        payload = {'decision': {key: normalized[key] for key in
                                ('decision_id', 'exp_id', 'short_code', 'recipient',
                                 'needs_user', 'scope', 'owner_session', 'undecided',
                                 'recommended')},
                   'plan_set': approval_plan, 'evidence': list(evidence or []),
                   'aggregate': aggregate}
        if binding is not None:
            payload['execution_plan'] = binding
        payload_text = canonical_json(payload)
        payload_hash = hashlib.sha256(payload_text.encode('utf-8')).hexdigest()

        existing = self._row(decision_id)
        if existing is not None:
            if existing['payload_hash'] != payload_hash:
                raise DecisionConflict(
                    f'决策 {decision_id} 已有不同内容（payload_hash 不符）⇒ 必须新开决策号，'
                    '⛔ 不静默覆盖')
            return self._prepared_view(existing)

        page_text = build_page_html(approval_plan, normalized, evidence=evidence,
                                    aggregate=aggregate, execution=binding)
        page_problems_list = page_problems(page_text, approval_plan, normalized,
                                           execution=binding)
        if page_problems_list:
            raise ReadinessError(['生成结果不过机器判据：' + item for item in page_problems_list])
        root = Path(page_dir) if page_dir else (Path(self.store.db_path).parent / 'web')
        root.mkdir(parents=True, exist_ok=True)
        page_path = root / f'approval_{decision_id}.html'
        _atomic_write(page_path, page_text.encode('utf-8'))
        data = page_path.read_bytes()
        page_sha = hashlib.sha256(data).hexdigest()

        self.store.ensure_experiment(exp_id)
        with self.store.transaction() as conn:
            again = conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                 (decision_id,)).fetchone()
            if again is not None:
                if again['payload_hash'] != payload_hash:
                    raise DecisionConflict(f'决策 {decision_id} 已被并发写入不同内容')
                return self._prepared_view(dict(again))
            code_row = conn.execute('SELECT * FROM decision_codes WHERE code=?',
                                    (short_code,)).fetchone()
            if code_row is not None:
                raise DecisionConflict(f'短码 {short_code} 已被别的决策占用（一次性）')
            conn.execute('INSERT INTO decisions(decision_id,exp_id,requested_at,expires_at,'
                         'status,resolution,short_code,recipient,recommended,needs_user,'
                         'scope,owner_session,judgment_reason,payload_json,payload_hash,'
                         'page_path,page_sha256,page_bytes,version,created_at,updated_at)'
                         " VALUES(?,?,?,?,'prepared',NULL,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                         (decision_id, exp_id, now, now + APPROVAL_TIMEOUT_SECONDS,
                          short_code, recipient, normalized['recommended'],
                          1 if needs_user else 0, scope, owner_session or None, reason,
                          payload_text, payload_hash, str(page_path), page_sha, len(data),
                          now, now))
            for position, oid in enumerate(_option_ids(approval_plan)):
                conn.execute('INSERT INTO decision_options(decision_id,option_id,state,position)'
                             " VALUES(?,?,'pending',?)", (decision_id, oid, position))
            conn.execute('INSERT INTO decision_codes(code,decision_id,exp_id,state)'
                         " VALUES(?,?,?,'issued')", (short_code, decision_id, exp_id))
            conn.execute('INSERT INTO decision_events(decision_id,kind,actor,from_state,to_state,'
                         'payload_json,at) VALUES(?,?,?,?,?,?,?)',
                         (decision_id, 'prepared', 'decisions', None, 'prepared',
                          canonical_json({'payload_hash': payload_hash,
                                          'page_sha256': page_sha, 'page_bytes': len(data),
                                          'needs_user': bool(needs_user),
                                          'judgment_reason': reason,
                                          'execution_sha256': execution_sha}), now))
            self.store.emit(conn, exp_id, 'decision_prepared', 'decisions',
                            {'decision_id': decision_id, 'payload_hash': payload_hash,
                             'page_sha256': page_sha,
                             'execution_sha256': execution_sha or None})
        return self._prepared_view(self._require(decision_id))

    def _prepared_view(self, row):
        try:
            document = json.loads(row['payload_json'])
            core_sha = research_fingerprint(document['plan_set'])
        except Exception:
            core_sha = ''
        binding = None
        try:
            binding = json.loads(row['payload_json']).get('execution_plan')
        except Exception:
            binding = None
        execution_sha = execution_fingerprint(binding) if isinstance(binding, dict) else ''
        return {
            'ok': True, 'decision_id': row['decision_id'], 'exp_id': row['exp_id'],
            'status': row['status'], 'short_code': row['short_code'],
            'recipient': row['recipient'], 'scope': row['scope'],
            'owner_session': row['owner_session'],
            'page_path': row['page_path'],
            # 研究内核指纹（审批页正文）与执行规范指纹（Workflow 起跑）。
            'research_sha256': core_sha, 'plan_sha256': core_sha,
            'execution_sha256': execution_sha,
            'execution_bound': bool(execution_sha),
            'page_sha256': row['page_sha256'], 'page_bytes': row['page_bytes'],
            'requested_at': _iso(row['requested_at']),
            'expires_at': _iso(row['expires_at']),
            'needs_user': bool(row['needs_user']),
            'judgment_reason': row['judgment_reason'],
        }

    # ── 通道 ──
    def _require_channel(self):
        if self.channel is None:
            raise ChannelUnavailable(
                '未配置真实通道 ⇒ fail-closed：不生成回执、不发页、不发卡。'
                '离线仍可用 `prepare` 生成审批页、用 `status` / `overdue` 做诊断。')
        if self.sender is None:
            self.sender = self._describe_channel()
            self._sender_fingerprint = hashlib.sha256(
                canonical_json(self.sender).encode('utf-8')).hexdigest()
        return self.channel

    def _describe_channel(self):
        """读通道自述的发送方身份。⛔ 凭据只允许是**引用**，核心把它算进指纹落账。"""
        describe = getattr(self.channel, 'describe', None)
        if not callable(describe):
            raise ChannelUnavailable('通道没有 describe() ⇒ 无法记录发送方身份，按 fail-closed 处理')
        raw = describe()
        if not isinstance(raw, dict):
            raise ChannelUnavailable('通道 describe() 必须返回对象')
        adapter = str(raw.get('adapter') or getattr(self.channel, 'name', '') or '').strip()
        if not adapter:
            raise ChannelUnavailable('通道 describe() 缺 adapter 名')
        conditions = raw.get('conditions') or {}
        if not isinstance(conditions, dict):
            raise ChannelUnavailable('通道 describe().conditions 必须是对象')
        return {'adapter': adapter,
                'identity': str(raw.get('identity') or '').strip(),
                'config_ref': str(raw.get('config_ref') or '').strip(),
                'conditions': dict(conditions),
                'version': str(raw.get('version') or '')}

    def _assert_healthy(self):
        channel = self._require_channel()
        health = getattr(channel, 'health', None)
        if not callable(health):
            raise ChannelUnavailable('通道没有 health() 能力 ⇒ 无法证明可用，按 fail-closed 处理')
        result = health()
        ok = result if isinstance(result, bool) else (
            isinstance(result, dict) and result.get('ok') is True)
        if not ok:
            detail = result if isinstance(result, dict) else {}
            raise ChannelUnavailable(f'通道健康检查未通过 ⇒ 不发：{canonical_json(detail)}')

    def _assert_open(self, row):
        if row['status'] == 'quarantined':
            raise QuarantineBlocked(
                f"决策 {row['decision_id']} 已隔离：上次交付结果不确定，⛔ 不自动重发；"
                '请新开决策号重新申请')
        if row['status'] in ('resolved', 'expired'):
            raise DecisionError(f"决策 {row['decision_id']} 已是 {row['status']}，不能再发送")

    def _assert_not_expired(self, row):
        if float(row['expires_at']) <= self.clock():
            self.tick()
            raise DecisionError(f"决策 {row['decision_id']} 已过有效期 ⇒ 已按超时取消，"
                                '要跑就重新申请（⛔ 不用旧短码）')

    # ── 交付 ──
    def _ensure_delivery(self, row, kind):
        with self.store.transaction() as conn:
            existing = conn.execute('SELECT * FROM decision_deliveries WHERE decision_id=?'
                                    ' AND kind=?', (row['decision_id'], kind)).fetchone()
            if existing is not None:
                return dict(conn.execute('SELECT * FROM delivery WHERE id=?',
                                         (existing['delivery_id'],)).fetchone())
            decision_id = row['decision_id']
            if kind == 'approval_page':
                body = f"{decision_id} 审批页（{row['exp_id']}）"
                task = self.delivery.enqueue(
                    row['exp_id'], 'approval_page', body=body, artifact=row['page_path'],
                    sha256=row['page_sha256'], recipient=row['recipient'],
                    request_key=f'decision:{decision_id}:page', conn=conn)
            else:
                body = self._card_body(row)
                task = self.delivery.enqueue(
                    row['exp_id'], 'card', body=body, recipient=row['recipient'],
                    request_key=f'decision:{decision_id}:card', conn=conn)
            conn.execute('INSERT INTO decision_deliveries(decision_id,kind,request_key,'
                         'delivery_id) VALUES(?,?,?,?)',
                         (decision_id, kind, task['request_key'], task['id']))
            conn.execute('INSERT INTO decision_events(decision_id,kind,actor,from_state,to_state,'
                         'payload_json,at) VALUES(?,?,?,?,?,?,?)',
                         (decision_id, 'delivery_enqueued', 'decisions', row['status'],
                          row['status'],
                          canonical_json({'kind': kind, 'delivery_id': task['id'],
                                          'request_key': task['request_key']}), self.clock()))
            self.store.emit(conn, row['exp_id'], 'decision_delivery_enqueued', 'decisions',
                            {'decision_id': decision_id, 'kind': kind,
                             'delivery_id': task['id']})
            return dict(conn.execute('SELECT * FROM delivery WHERE id=?',
                                     (task['id'],)).fetchone())

    def _card_body(self, row):
        payload = json.loads(row['payload_json'])
        plan_set = payload['plan_set']
        decision = payload['decision']
        title = (plan_set.get('reader_view') or {}).get('title') or plan_set.get('goal') or ''
        lines = [f"[{row['exp_id']} {row['decision_id']} / {row['short_code']}] {title}"]
        for option in plan_set.get('options') or []:
            oid = str(option.get('id'))
            if oid in decision['undecided']:
                lines.append(f"{oid}｜{option.get('what')}")
        lines.append('待批：' + '、'.join(decision['undecided']))
        lines.append(f"回复：<短码 {row['short_code']}> A　·　全否 N　·　停机 S")
        lines.append(f"有效期到：{_iso(row['expires_at'])}"
                     f"（{int(APPROVAL_TIMEOUT_SECONDS / 3600)} 小时未明确批准自动取消，"
                     '不消费短码、不按推荐项推进）')
        body = '\n'.join(lines)
        foreign = [item for item in EXP_RE.findall(body) if item != row['exp_id']]
        if foreign:
            raise DecisionError('卡片正文含其他实验号：' + '、'.join(sorted(set(foreign)))
                                + ' ⇒ 拒绝发送（会把别轮答复发到本轮）')
        return body

    def _card_wire_meta(self, row):
        """选择卡的**结构化**元数据（按钮用），全部取自真实决策，⛔ 不解析正文。"""
        document = json.loads(row['payload_json'])
        plan_set = document.get('plan_set') or {}
        decision = document.get('decision') or {}
        undecided = [str(item) for item in (decision.get('undecided') or [])]
        options = []
        for item in plan_set.get('options') or []:
            oid = str(item.get('id') or '')
            if oid in undecided:
                options.append({'option_id': oid,
                                'label': str(item.get('what') or oid)})
        return {'short_code': row['short_code'],
                'card_options': options,
                # 适配层按它造按钮 value：自己再补 `channel`（平台名）与 `options:[oid]`。
                'card_action_template': {'intent': 'approve',
                                         'exp_id': row['exp_id'],
                                         'decision_id': row['decision_id'],
                                         'recipient': row['recipient'],
                                         'short_code': row['short_code']}}

    def _claim_exact(self, delivery_id, worker, lease_seconds=60):
        """按 id 精确领取，租约与 fence 语义与 `Delivery.claim()` 逐条一致。"""
        require_text(worker, 'worker')
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) \
                or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise DecisionError('lease_seconds 必须为正有限数')
        now = self.clock()
        with self.store.transaction() as conn:
            row = conn.execute('SELECT * FROM delivery WHERE id=?', (delivery_id,)).fetchone()
            if row is None:
                return None
            if row['status'] not in ('queued', 'leased', 'sending', 'unknown'):
                return None
            if row['lease_until'] is not None and row['lease_until'] > now:
                return None
            status = 'unknown' if row['status'] in ('sending', 'unknown') else 'leased'
            conn.execute('UPDATE delivery SET status=?,owner=?,fence=fence+1,lease_until=?,'
                         'updated_at=? WHERE id=?',
                         (status, worker, now + float(lease_seconds), now, delivery_id))
            self.store.emit(conn, row['exp_id'], 'delivery_claimed', worker,
                            {'delivery_id': delivery_id, 'fence': row['fence'] + 1,
                             'status': status})
            return dict(conn.execute('SELECT * FROM delivery WHERE id=?',
                                     (delivery_id,)).fetchone())

    def _deliver(self, row, delivery_id, kind, worker, lease_seconds=60, decision_id=None):
        self._require_channel()
        if decision_id is None:
            decision_id = row['decision_id'] if row is not None and 'decision_id' in row.keys() else ''
        current = self.delivery.get(delivery_id)
        if current is None:
            return {'ok': False, 'status': 'missing', 'reason': '交付任务不存在',
                    'delivery_id': delivery_id}
        if current['status'] == 'delivered':
            return {'ok': True, 'status': 'delivered', 'delivery_id': delivery_id,
                    'receipt': self._receipt_of(delivery_id)}
        if current['status'] == 'blocked':
            return {'ok': False, 'status': 'blocked', 'delivery_id': delivery_id,
                    'reason': current['error'] or '附件已变化，禁止发送'}
        claim = self._claim_exact(delivery_id, worker, lease_seconds)
        if claim is None:
            fresh = self.delivery.get(delivery_id)
            if fresh is not None and fresh['status'] == 'unknown':
                if row is not None and 'decision_id' in row.keys():
                    self._quarantine(row, '交付结果未知，禁止自动重发', delivery_id=delivery_id)
                return {'ok': False, 'status': 'quarantined', 'delivery_id': delivery_id,
                        'reason': '结果未知 ⇒ 隔离，不重发'}
            return {'ok': False, 'status': 'in_flight', 'delivery_id': delivery_id,
                    'reason': '另一实例持有租约或该任务当前不可发送（不重发）'}
        wire_meta = None
        if kind == 'card' and row is not None and 'decision_id' in row.keys():
            wire_meta = self._card_wire_meta(row)
        adapter = _ChannelAdapter(self.channel, decision_id=decision_id, kind=kind,
                                  sender=self.sender,
                                  sender_fingerprint=self._sender_fingerprint,
                                  wire_meta=wire_meta)
        try:
            if claim['status'] == 'unknown':
                result = self.delivery.reconcile(claim, adapter)
            else:
                result = self.delivery.send(claim, adapter)
        except LeaseLost as exc:
            return {'ok': False, 'status': 'lease_lost', 'delivery_id': delivery_id,
                    'reason': str(exc)}
        status = result['status']
        if status == 'delivered':
            return {'ok': True, 'status': 'delivered', 'delivery_id': delivery_id,
                    'receipt': self._receipt_of(delivery_id)}
        if status in ('queued', 'leased'):
            # NotSent：确定没发出去 ⇒ 可以重试，但这一轮不算送达
            return {'ok': False, 'status': 'not_sent', 'delivery_id': delivery_id,
                    'reason': result['error'] or '通道报告未发出（可重试）'}
        if row is not None and 'decision_id' in row.keys():
            self._quarantine(row, f'交付状态 {status}：{result.get("error")}',
                             delivery_id=delivery_id)
        return {'ok': False, 'status': 'quarantined', 'delivery_id': delivery_id,
                'reason': result.get('error') or '交付结果未知'}

    def _receipt_of(self, delivery_id):
        conn = self.store.connect()
        try:
            row = conn.execute('SELECT receipt_json FROM delivery_receipts WHERE delivery_id=?',
                               (delivery_id,)).fetchone()
            return json.loads(row['receipt_json']) if row else None
        finally:
            conn.close()

    # ── 回执校验（装进 Delivery，假回执一律进 unknown，不进 delivered）──
    def _verify_receipt(self, conn, delivery_row, receipt):
        if self.channel is None or not isinstance(receipt, ChannelReceipt):
            return False
        if not isinstance(receipt.message_id, str) or not receipt.message_id.strip():
            return False
        if receipt.ok is not True or receipt.returncode != 0:
            return False
        # 发送方绑定：回执必须来自**当前这个通道实例自己的 describe() 身份**。
        # 指纹由核心算（见 _ChannelAdapter._stamp），适配层自报的 sender 字段不作数。
        if not self._sender_fingerprint or receipt.sender_fingerprint != self._sender_fingerprint:
            return False
        if self.sender and receipt.adapter != self.sender.get('adapter'):
            return False
        verifier = getattr(self.channel, 'verify_receipt', None)
        if not callable(verifier) or verifier(receipt) is not True:
            return False
        binding = conn.execute('SELECT decision_id,kind FROM decision_deliveries'
                               ' WHERE delivery_id=?', (delivery_row['id'],)).fetchone()
        if binding is None:
            # 文字回复不属于某份决策：它由「原始事件 + 实验号 + 收件人」绑定，
            # 那三条在 Delivery.enqueue 的事件核验里已经判过。
            return delivery_row['kind'] == 'reply' and receipt.kind == 'reply' \
                and receipt.payload_hash == delivery_row['payload_hash']
        decision = conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                               (binding['decision_id'],)).fetchone()
        if decision is None:
            return False
        kind = binding['kind']
        if receipt.kind != kind or receipt.decision_id != decision['decision_id']:
            return False
        if receipt.exp_id != decision['exp_id'] or receipt.recipient != decision['recipient']:
            return False
        if receipt.payload_hash != delivery_row['payload_hash']:
            return False
        if kind == 'approval_page':
            want = f"/web/approval_{decision['decision_id']}.html"
            if not str(receipt.path).replace('\\', '/').endswith(want):
                return False
            if receipt.sha256 != decision['page_sha256'] \
                    or int(receipt.bytes or 0) != int(decision['page_bytes']):
                return False
        if kind == 'card' and receipt.short_code and receipt.short_code != decision['short_code']:
            return False
        return True

    def verify_reply_event(self, conn, payload):
        """回复绑定判据：原始事件必须真实、已应用、是**文字**、有回复任务、且实验号与收件人一致。"""
        binding = payload.get('event_reply_to') or {}
        row = conn.execute('SELECT * FROM inbox_events WHERE source=? AND event_id=?',
                           (binding.get('source'), binding.get('event_id'))).fetchone()
        if row is None or not row['consumed']:
            return False
        event = json.loads(row['payload_json'])
        if _is_card_click(event):
            return False
        task = conn.execute('SELECT 1 FROM inbox_reply_tasks WHERE source=? AND event_id=?',
                            (row['source'], row['event_id'])).fetchone()
        if task is None:
            return False
        recipient = event.get('recipient')
        if not _nonempty_str(recipient):
            return False
        return (row['exp_id'] == payload.get('exp_id') == binding.get('exp_id')
                and recipient == payload.get('recipient') == binding.get('recipient'))

    # ── 发送 ──
    def send_page(self, decision_id, *, worker='decisions', lease_seconds=60):
        self._require_write('send_page（发审批页）')
        self._require_channel()
        self._assert_healthy()
        row = self._require(decision_id)
        self._assert_open(row)
        self._assert_not_expired(row)
        if not int(row['needs_user']):
            raise DecisionError('该决策已声明不需要人判断 ⇒ 不发审批页（要发就新开一个决策号）')
        task = self._ensure_delivery(row, 'approval_page')
        result = self._deliver(row, task['id'], 'approval_page', worker, lease_seconds)
        if not result['ok'] and result['status'] in ('not_sent', 'blocked'):
            with self.store.transaction() as conn:
                fresh = conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                     (decision_id,)).fetchone()
                if fresh is not None and dict(fresh)['status'] in ('prepared', 'send_failed'):
                    self._transition(conn, dict(fresh), 'send_failed',
                                     reason=f"审批页未送达：{result.get('reason')}")
        result['decision_id'] = decision_id
        result['kind'] = 'approval_page'
        return result

    def send_card(self, decision_id, *, worker='decisions', lease_seconds=60):
        self._require_write('send_card（发选择卡）')
        self._require_channel()
        self._assert_healthy()
        row = self._require(decision_id)
        self._assert_open(row)
        self._assert_not_expired(row)
        chain = self.chain(decision_id)
        if not chain['page']['ok']:
            raise OrderingViolated(
                '先页后卡：审批页没有成功回执，拒绝发卡（B1/B4）。'
                + ('问题：' + '；'.join(chain['page']['problems']) if chain['page']['problems'] else ''))
        task = self._ensure_delivery(row, 'card')
        result = self._deliver(row, task['id'], 'card', worker, lease_seconds)
        if result['ok']:
            with self.store.transaction() as conn:
                fresh = conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                     (decision_id,)).fetchone()
                fresh = dict(fresh)
                if fresh['status'] != 'awaiting_approval':
                    self._transition(conn, fresh, 'awaiting_approval',
                                     reason='页与卡都有成功回执')
        elif result['status'] in ('not_sent', 'blocked'):
            with self.store.transaction() as conn:
                fresh = dict(conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                          (decision_id,)).fetchone())
                if fresh['status'] in ('prepared', 'send_failed'):
                    self._transition(conn, fresh, 'send_failed',
                                     reason=f"选择卡未送达：{result.get('reason')}")
        result['decision_id'] = decision_id
        result['kind'] = 'card'
        return result

    def send(self, decision_id, *, worker='decisions', lease_seconds=60):
        """先页后卡：页没有成功回执就绝不发卡；任一步失败都不放行。"""
        self._require_write('send（先页后卡）')
        self._require_channel()
        self._assert_healthy()
        row = self._require(decision_id)
        self._assert_open(row)
        self.tick()
        row = self._require(decision_id)
        if row['status'] == 'expired':
            raise DecisionError('决策已按超时取消，要跑就重新申请')
        if not int(row['needs_user']):
            raise DecisionError('该决策已声明不需要人判断 ⇒ 不发审批')
        chain = self.chain(decision_id)
        if not chain['page']['ok']:
            page = self.send_page(decision_id, worker=worker, lease_seconds=lease_seconds)
            if not page['ok']:
                return {'ok': False, 'stage': 'page', 'decision_id': decision_id,
                        'status': page['status'], 'reason': page.get('reason')}
        chain = self.chain(decision_id)
        if not chain['card']['ok']:
            card = self.send_card(decision_id, worker=worker, lease_seconds=lease_seconds)
            if not card['ok']:
                return {'ok': False, 'stage': 'card', 'decision_id': decision_id,
                        'status': card['status'], 'reason': card.get('reason')}
        chain = self.chain(decision_id)
        return {'ok': chain['complete'], 'stage': None, 'decision_id': decision_id,
                'status': self._require(decision_id)['status'], 'chain': chain}

    # ── 双回执 ──
    def chain(self, decision_id):
        problem = self._readonly_problem()
        if problem:
            slot = {'ok': False, 'problems': [problem], 'receipt': None, 'delivery_id': None}
            return {'decision_id': decision_id, 'exp_id': None, 'complete': False,
                    'undecidable': True, 'problems': [problem],
                    'page': dict(slot), 'card': dict(slot)}
        row = self._require(decision_id)
        conn = self.store.connect()
        try:
            links = {r['kind']: dict(r) for r in conn.execute(
                'SELECT * FROM decision_deliveries WHERE decision_id=?', (decision_id,))}
            deliveries = {}
            receipts = {}
            for kind, link in links.items():
                task = conn.execute('SELECT * FROM delivery WHERE id=?',
                                    (link['delivery_id'],)).fetchone()
                deliveries[kind] = dict(task) if task else None
                receipt = conn.execute('SELECT * FROM delivery_receipts WHERE delivery_id=?',
                                       (link['delivery_id'],)).fetchone()
                receipts[kind] = json.loads(receipt['receipt_json']) if receipt else None
        finally:
            conn.close()
        result = {'decision_id': decision_id, 'exp_id': row['exp_id'], 'complete': False,
                  'page': {'ok': False, 'problems': [], 'receipt': None, 'delivery_id': None},
                  'card': {'ok': False, 'problems': [], 'receipt': None, 'delivery_id': None}}
        for kind, slot in (('approval_page', 'page'), ('card', 'card')):
            problems = []
            task = deliveries.get(kind)
            receipt = receipts.get(kind)
            if task is None:
                problems.append(f'{kind} 没有交付任务（从未发送）')
            else:
                result[slot]['delivery_id'] = task['id']
                if task['status'] != 'delivered':
                    problems.append(f"交付状态 {task['status']}（不是 delivered）")
                if receipt is None:
                    problems.append('没有回执（空回执不算送达）')
                else:
                    if not str(receipt.get('message_id') or '').strip():
                        problems.append('回执 message_id 为空')
                    if receipt.get('ok') is not True:
                        problems.append(f"回执 ok={receipt.get('ok')!r} 不是 True")
                    if receipt.get('returncode') != 0:
                        problems.append(f"回执 returncode={receipt.get('returncode')!r} 不是 0")
                    if receipt.get('decision_id') != decision_id:
                        problems.append('回执 decision_id 与本次决策不符')
                    if receipt.get('exp_id') != row['exp_id']:
                        problems.append('回执 exp 与本次实验不符')
                    if receipt.get('recipient') != row['recipient']:
                        problems.append('回执 recipient 与本次收件人不符')
                    if receipt.get('payload_hash') != task['payload_hash']:
                        problems.append('回执 payload_hash 与交付内容不符')
                    if receipt.get('kind') != kind:
                        problems.append(f"回执 kind={receipt.get('kind')!r} 不是 {kind!r}")
                    if kind == 'approval_page':
                        want = f"/web/approval_{decision_id}.html"
                        if not str(receipt.get('path') or '').replace('\\', '/').endswith(want):
                            problems.append('回执 path 指的不是本决策的审批页')
                        if receipt.get('sha256') != row['page_sha256']:
                            problems.append('回执 sha256 不是当前这一版审批页')
                    if kind == 'card' and receipt.get('short_code') \
                            and receipt.get('short_code') != row['short_code']:
                        problems.append('回执短码与决策短码不符')
                    result[slot]['receipt'] = receipt
            result[slot]['problems'] = problems
            result[slot]['ok'] = not problems
        # 审批页必须**先落磁盘**且与登记版本一致：发送确认后才算过（B1/B3）。
        # 页在发送后被改过 / 被删掉 ⇒ 这条链不成立，别拿一份改过的页当批准依据。
        page_file = Path(row['page_path'])
        if not page_file.is_file():
            result['page']['problems'].append('磁盘上找不到审批页（必须先 prepare 落盘）')
        else:
            data = page_file.read_bytes()
            if hashlib.sha256(data).hexdigest() != row['page_sha256'] \
                    or len(data) != int(row['page_bytes']):
                result['page']['problems'].append('磁盘上的审批页与登记版本不一致（被改过）')
            result['page']['bytes'] = len(data)
        result['page']['ok'] = not result['page']['problems']
        result['complete'] = result['page']['ok'] and result['card']['ok']
        return result

    # ── 远端 GET 复核 ──
    def verify_remote(self, decision_id):
        """向通道 GET 一遍已发出的交付，核它还记得那条消息（需要注入 channel）。

        返回 `{decision_id, ok, kinds: {page: {...}, card: {...}}, problems: [...]}`。
        ⚠️ 它**只读**：不改状态、不重发；查不到就如实报「通道上没有这条消息」。
        """
        channel = self._require_channel()
        row = self._require(decision_id)
        query = getattr(channel, 'query', None)
        if not callable(query):
            raise ChannelUnavailable('通道没有 query() 能力 ⇒ 无法用 GET 复核已发回执')
        conn = self.store.connect()
        try:
            links = {r['kind']: dict(r) for r in conn.execute(
                'SELECT * FROM decision_deliveries WHERE decision_id=?', (decision_id,))}
        finally:
            conn.close()
        result = {'decision_id': decision_id, 'ok': True, 'kinds': {}, 'problems': []}
        for kind, slot in (('approval_page', 'page'), ('card', 'card')):
            task = self.delivery.get(links[kind]['delivery_id']) if kind in links else None
            stored = self._receipt_of(task['id']) if task else None
            entry = {'delivery_id': task['id'] if task else None,
                     'stored_message_id': (stored or {}).get('message_id'),
                     'remote': None, 'ok': False, 'problems': []}
            if task is None or stored is None:
                entry['problems'].append('本地没有这条交付的回执')
            else:
                try:
                    raw = query(task['wire_key'])
                except Exception as exc:
                    entry['problems'].append(f'GET 失败：{type(exc).__name__}: {exc}')
                    raw = None
                if raw is None:
                    entry['problems'].append('通道上查不到这条消息（可能已被撤回/删除）')
                else:
                    get = _receipt_getter(raw)
                    entry['remote'] = {'message_id': get('message_id'),
                                       'request_key': get('request_key'),
                                       'payload_hash': get('payload_hash')}
                    if get('message_id') != stored.get('message_id'):
                        entry['problems'].append('通道上的 message_id 与本地回执不符')
                    if get('request_key') and get('request_key') != task['wire_key']:
                        entry['problems'].append('通道上的 request_key 与本地交付不符')
                    if get('payload_hash') and get('payload_hash') != task['payload_hash']:
                        entry['problems'].append('通道上的 payload_hash 与本地交付不符')
            entry['ok'] = not entry['problems']
            result['kinds'][slot] = entry
            if not entry['ok']:
                result['ok'] = False
                result['problems'].extend(f'{slot}: {item}' for item in entry['problems'])
        result['exp_id'] = row['exp_id']
        return result

    # ── 超时取消 ──
    def tick(self, *, now=None):
        """两小时到点：取消审批。⛔ 不消费短码、不采纳推荐项。"""
        self._require_write('tick（把过期决策转 expired）')
        now = float(now if now is not None else self.clock())
        conn = self.store.connect()
        try:
            due = [dict(r) for r in conn.execute(
                'SELECT * FROM decisions WHERE status IN (?,?,?) AND expires_at<=?',
                (*OPEN_STATES, now))]
        finally:
            conn.close()
        expired = []
        for row in due:
            with self.store.transaction() as conn:
                fresh = conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                     (row['decision_id'],)).fetchone()
                if fresh is None:
                    continue
                fresh = dict(fresh)
                if fresh['status'] not in OPEN_STATES or float(fresh['expires_at']) > now:
                    continue
                conn.execute('UPDATE decision_codes SET state=? WHERE decision_id=? AND state=?',
                             (CODE_VOID, fresh['decision_id'], CODE_ISSUED))
                self._transition(conn, fresh, 'expired', resolution='timeout_cancelled',
                                 reason=f'超过 {int(APPROVAL_TIMEOUT_SECONDS)}s 没有明确批准 ⇒ 自动取消',
                                 extra={'short_code_consumed': False,
                                        'recommended_applied': False,
                                        'recommended': fresh['recommended']})
                conn.execute('INSERT INTO decision_events(decision_id,kind,actor,from_state,'
                             'to_state,payload_json,at) VALUES(?,?,?,?,?,?,?)',
                             (fresh['decision_id'], 'timeout_cancelled', 'decisions',
                              fresh['status'], 'expired',
                              canonical_json({'short_code_consumed': False,
                                              'recommended_applied': False,
                                              'short_code': fresh['short_code'],
                                              'recommended': fresh['recommended']}), now))
                self.store.emit(conn, fresh['exp_id'], 'decision_timeout_cancelled', 'decisions',
                                {'decision_id': fresh['decision_id'],
                                 'short_code_consumed': False,
                                 'recommended_applied': False})
            expired.append(row['decision_id'])
        return expired

    # ── 入向 ──
    def ingest(self, source, event, sha256=None):
        """落一条真实入向事件（持久化 + 哈希 + 幂等），随后按类型应用。"""
        self._require_write('ingest（落一条入向事件）')
        require_text(source, 'source')
        if not isinstance(event, dict):
            raise DecisionError('事件必须是 JSON 对象')
        exp_id = str(event.get('exp_id') or '').strip()
        if exp_id:
            self.store.ensure_experiment(exp_id)
        row = self.inbox.receive(source, event, sha256)
        return self.apply_event(source, row['event_id'])

    def apply_event(self, source, event_id, *, command_handler=None):
        self._require_write('apply_event（应用入向事件）')
        row = self.inbox.get(source, event_id)
        if row is None:
            raise DecisionError(f'事件不存在：{source}/{event_id}')
        if row['consumed']:
            return row
        payload = json.loads(row['payload_json'])
        if _is_card_click(payload):
            return self._apply_card_click(source, event_id, payload)
        applied = self.inbox.apply(source, event_id, command_handler=command_handler)
        self._ensure_reply_task(applied)
        return applied

    def poll_channel(self, cursor=None, *, limit=50, source=None):
        """拉取式入向：把通道 `poll()` 给的事件逐条落盘并应用。

        返回 `{ok, cursor, count, results:[{event_id, status}|{event_id, error}]}`。
        `cursor` 原样透传，由适配层决定它的含义（时间戳 / 序列号 / 游标串）。
        ⚠️ 单条事件失败不中断整批：错误逐条记下，不假装成功。
        """
        self._require_write('poll_channel（拉取并落盘入向事件）')
        channel = self._require_channel()
        poll = getattr(channel, 'poll', None)
        if not callable(poll):
            raise ChannelUnavailable('通道没有 poll() 能力 ⇒ 不能拉取入向事件')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise DecisionError('limit 必须是 1–500 的整数')
        batch = poll(cursor, limit)
        if isinstance(batch, dict):
            events = batch.get('events')
            next_cursor = batch.get('cursor', cursor)
        elif isinstance(batch, list):
            events, next_cursor = batch, cursor
        else:
            raise ChannelUnavailable('通道 poll() 必须返回 {cursor, events} 或事件列表')
        if not isinstance(events, list):
            raise ChannelUnavailable('通道 poll().events 必须是列表')
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ChannelUnavailable('通道 poll().cursor 必须是字符串或 None')
        results = []
        for event in events:
            event_id = event.get('event_id') if isinstance(event, dict) else None
            try:
                row = self.ingest(source or str(getattr(channel, 'name', '') or 'channel'),
                                  event)
                results.append({'event_id': row['event_id'], 'status': row['status'],
                                'consumed': bool(row['consumed'])})
            except Exception as exc:
                results.append({'event_id': event_id,
                                'error': f'{type(exc).__name__}: {exc}'})
        return {'ok': True, 'cursor': next_cursor, 'count': len(events), 'results': results}

    def open_subscription(self, *, source=None):
        """推送式入向：让通道把每条事件交回 `ingest()`；返回带 `close()` 的句柄。

        ⚠️ 句柄必须由调用方在停机时 close()（核心不替你决定监听活多久）。
        """
        self._require_write('open_subscription（订阅推送入向事件）')
        channel = self._require_channel()
        subscribe = getattr(channel, 'subscribe', None)
        if not callable(subscribe):
            raise ChannelUnavailable('通道没有 subscribe() 能力 ⇒ 不能接收推送事件')

        def handler(event):
            return self.ingest(source or str(getattr(channel, 'name', '') or 'channel'), event)

        handle = subscribe(handler)
        if handle is not None and not callable(getattr(handle, 'close', None)):
            raise ChannelUnavailable('通道 subscribe() 必须返回带 close() 的句柄')
        return handle

    def _ensure_reply_task(self, row):
        """每一条已应用的**文字**事件都要有回复任务（D1：文字消息必须有人回）。"""
        if not row or not row['consumed']:
            return
        payload = json.loads(row['payload_json'])
        if _is_card_click(payload):
            return
        exp_id = str(payload.get('exp_id') or row['exp_id'] or '').strip()
        recipient = payload.get('recipient')
        if not exp_id or not _nonempty_str(recipient):
            return
        with self.store.transaction() as conn:
            exists = conn.execute('SELECT id FROM inbox_reply_tasks WHERE source=? AND event_id=?',
                                  (row['source'], row['event_id'])).fetchone()
            if exists:
                return
            conn.execute('INSERT INTO inbox_reply_tasks(source,event_id,exp_id,recipient,status)'
                         " VALUES(?,?,?,?,'pending')", (row['source'], row['event_id'],
                                                        exp_id, str(recipient)))
            self.store.emit(conn, exp_id, 'inbox_reply_task_created', 'decisions',
                            {'source': row['source'], 'event_id': row['event_id']})

    def _mark_event(self, conn, source, event_id, *, status, error=None, category=None,
                    principal=None, result=None):
        conn.execute('UPDATE inbox_events SET status=?,error=?,category=coalesce(?,category),'
                     'principal=coalesce(?,principal),classified_at=coalesce(classified_at,?),'
                     'result_json=coalesce(?,result_json),applied_at=?,'
                     'consumed=CASE WHEN ?=? THEN 1 ELSE consumed END'
                     ' WHERE source=? AND event_id=?',
                     (status, error, category, principal, self.clock(),
                      canonical_json(result) if result is not None else None,
                      self.clock() if status == 'applied' else None,
                      status, 'applied', source, event_id))

    def _emit(self, conn, exp_id, kind, actor, payload):
        """只在实验存在时写事件流（别轮的、或还没登记的实验不制造外键噪音）。"""
        if exp_id and conn.execute('SELECT 1 FROM experiments WHERE id=?',
                                   (exp_id,)).fetchone():
            self.store.emit(conn, exp_id, kind, actor, payload)

    def _apply_card_click(self, source, event_id, payload):
        """卡片点击：执行批准 / 拒绝 + 回执。⛔ 不创建文本回复任务（D4）。"""
        decision_id = str(payload.get('decision_id') or '').strip()
        exp_id = str(payload.get('exp_id') or '').strip()
        intent = str(payload.get('intent') or '').strip().lower()
        try:
            if not decision_id:
                raise DecisionError('卡片点击事件缺 decision_id')
            row = self._require(decision_id)
            if row['exp_id'] != exp_id:
                raise DecisionError('卡片点击的实验号与决策不符（会把别轮答复发到本轮）')
            if not _nonempty_str(payload.get('recipient')) \
                    or payload.get('recipient') != row['recipient']:
                raise DecisionError('卡片点击的收件人与审批发出对象不符')
            if str(payload.get('short_code') or '').upper() != row['short_code']:
                raise DecisionError('卡片点击没带本决策的短码 ⇒ 不构成授权')
            if self.inbox.authenticate is None:
                raise IdentityRefused('未配置身份校验器 ⇒ 禁止应用审批（fail-closed）')
            snapshot = json.loads(canonical_json(payload))
            principal = self.inbox.authenticate(source, snapshot)
            if canonical_json(snapshot) != canonical_json(payload):
                raise IdentityRefused('身份校验器不得修改原事件')
            require_text(principal, 'authenticated principal')
        except Exception as exc:
            with self.store.transaction() as conn:
                self._mark_event(conn, source, event_id, status='rejected', error=str(exc),
                                 category='card_click')
                self._emit(conn, exp_id, 'decision_card_rejected', 'decisions',
                           {'source': source, 'event_id': event_id, 'error': str(exc)})
            return self.inbox.get(source, event_id)

        partial = None
        try:
            with self.store.transaction() as conn:
                fresh = dict(conn.execute('SELECT * FROM decisions WHERE decision_id=?',
                                          (decision_id,)).fetchone())
                now = self.clock()
                if fresh['status'] == 'expired' or float(fresh['expires_at']) <= now:
                    raise _CardRejected('决策已过有效期（超时取消）⇒ 这次点击不构成批准')
                if not self.chain(decision_id)['complete']:
                    raise _CardRejected('审批链不完整（页或卡没有成功回执）⇒ 点击不放行')
                if fresh['status'] != 'awaiting_approval':
                    raise _CardRejected(f"决策当前状态是 {fresh['status']}，不是待批")
                options = [str(item) for item in (payload.get('options') or [])]
                pending = [item['option_id'] for item in conn.execute(
                    'SELECT option_id FROM decision_options WHERE decision_id=? AND state=?',
                    (decision_id, 'pending'))]
                if intent in ('reject', 'deny'):
                    # 单元素 options 合法：一个按钮对应一条选项；空 options = 全否。
                    targets = options or list(pending)
                    unknown = [oid for oid in targets if oid not in pending]
                    if unknown:
                        raise _CardRejected('否决了不在待批范围的选项：' + '、'.join(unknown))
                    if set(targets) >= set(pending):
                        resolution, options_state = 'user_all_reject', 'rejected'
                    else:
                        # 部分否决：只记这几条，决策继续待批，短码**不消费**
                        # （否决不是授权令牌的使用；剩下那条还要能被批）。
                        for oid in targets:
                            conn.execute('UPDATE decision_options SET state=?'
                                         ' WHERE decision_id=? AND option_id=?',
                                         ('rejected', decision_id, oid))
                        self._mark_event(conn, source, event_id, status='applied',
                                         category='card_click', principal=principal,
                                         result={'decision_id': decision_id,
                                                 'resolution': None,
                                                 'rejected': targets,
                                                 'status': 'awaiting_approval',
                                                 'short_code_consumed': False})
                        self._emit(conn, exp_id, 'decision_partial_reject', principal,
                                   {'decision_id': decision_id, 'rejected': targets,
                                    'source': source, 'event_id': event_id})
                        partial = targets
                else:
                    if not options:
                        raise _CardRejected('点击没有明确指名批准哪一条 ⇒ 不构成批准')
                    unknown = [oid for oid in options if oid not in pending]
                    if unknown:
                        raise _CardRejected('批准了不在待批范围的选项：' + '、'.join(unknown))
                    targets = options
                    resolution, options_state = 'user_approved', 'approved'
                if partial is None:
                    code = conn.execute('SELECT * FROM decision_codes WHERE decision_id=?',
                                        (decision_id,)).fetchone()
                    if code is None or code['state'] != CODE_ISSUED:
                        raise _CardRejected('短码已消费或已作废 ⇒ 重放不算授权')
                    binding = canonical_json([source, event_id])
                    cursor = conn.execute(
                        'UPDATE decision_codes SET state=?, consumed_by=?, consumed_at=?'
                        ' WHERE code=? AND state=?',
                        (CODE_CONSUMED, binding, now, code['code'], CODE_ISSUED))
                    if cursor.rowcount != 1:
                        raise _CardRejected('短码并发消费失败 ⇒ 不构成授权')
                    for oid in targets:
                        conn.execute('UPDATE decision_options SET state=?'
                                     ' WHERE decision_id=? AND option_id=?',
                                     (options_state, decision_id, oid))
                    conn.execute('UPDATE decisions SET decided_source=?, decided_event_id=?'
                                 ' WHERE decision_id=?', (source, event_id, decision_id))
                    self._transition(conn, fresh, 'resolved', resolution=resolution,
                                     actor=principal, reason=f'卡片点击（{principal}）',
                                     extra={'approved': options, 'source': source,
                                            'event_id': event_id})
                    self._mark_event(conn, source, event_id, status='applied',
                                     category='card_click', principal=principal,
                                     result={'decision_id': decision_id,
                                             'resolution': resolution,
                                             'approved': targets,
                                             'short_code_consumed': True})
                    self._emit(conn, exp_id, 'decision_resolved', principal,
                               {'decision_id': decision_id, 'resolution': resolution,
                                'approved': targets, 'source': source,
                                'event_id': event_id})
        except _CardRejected as exc:
            with self.store.transaction() as conn:
                self._mark_event(conn, source, event_id, status='rejected', error=str(exc),
                                 category='card_click', principal=principal)
                self._emit(conn, exp_id, 'decision_card_rejected', 'decisions',
                           {'source': source, 'event_id': event_id, 'error': str(exc)})
        return self.inbox.get(source, event_id)

    # ── 文字回复窗口 ──
    def reply_status(self, exp_id=None, *, now=None, grace=TEXT_REPLY_GRACE_SECONDS):
        """入向文字消息的回复窗口。逐条点名超期的，并给出阻断判词。

        只读构造下缺库 / 缺表 ⇒ 返回「不可判」（`ok=False` + `exit_code=2`），
        ⛔ 不建库、不迁移，也不把「读不到」读成「没有超期」。
        """
        problem = self._readonly_problem()
        if problem:
            out = {'ok': False, 'undecidable': True, 'problems': [problem], 'exp_id': exp_id,
                   'blocked': True, 'phase': 'undecidable', 'next': 'fix_state_database',
                   'exit_code': 2, 'overdue': [], 'unanswerable': [], 'replied_events': [],
                   'text_events': 0, 'card_events': 0, 'replied': 0, 'pending': 0}
            return out
        now = float(now if now is not None else self.clock())
        grace = float(grace)
        conn = self.store.connect()
        try:
            events = [dict(r) for r in conn.execute(
                'SELECT * FROM inbox_events WHERE (? IS NULL OR exp_id=?)'
                ' ORDER BY event_time IS NULL, event_time, event_id, source',
                (exp_id, exp_id))]
            replies = {}
            for row in conn.execute(
                    "SELECT d.id,d.exp_id,d.payload_json FROM delivery d"
                    " JOIN delivery_receipts r ON r.delivery_id=d.id"
                    " WHERE d.kind='reply' AND d.status='delivered'"):
                payload = json.loads(row['payload_json'])
                binding = payload.get('event_reply_to') or {}
                eid = str(binding.get('event_id') or '')
                if eid:
                    replies.setdefault(eid, []).append(dict(row))
        finally:
            conn.close()
        out = {'exp_id': exp_id, 'grace_sec': grace, 'now': _iso(now),
               'text_events': 0, 'card_events': 0, 'replied': 0, 'pending': 0,
               'overdue': [], 'unanswerable': [], 'replied_events': []}
        for row in events:
            payload = json.loads(row['payload_json'])
            if _is_card_click(payload):
                out['card_events'] += 1
                continue
            out['text_events'] += 1
            event_id = str(row['event_id'])
            if replies.get(event_id):
                out['replied'] += 1
                out['replied_events'].append({'event_id': event_id,
                                              'source': row['source'],
                                              'deliveries': [item['id'] for item in replies[event_id]]})
                continue
            excerpt = str(payload.get('body') or payload.get('content') or '').replace('\n', ' ')
            excerpt = excerpt[:80] + ('…' if len(excerpt) > 80 else '') or '（内容空 / 不是文本）'
            item = {'event_id': event_id, 'source': row['source'], 'exp_id': row['exp_id'],
                    'recipient': payload.get('recipient'), 'excerpt': excerpt,
                    'received_at': _iso(row['received_at']),
                    'status': row['status']}
            event_time = row['event_time']
            if not _nonempty_str(payload.get('recipient')):
                item['why'] = '事件没有 recipient ⇒ 无法回复（不可核，按超期算）'
                out['unanswerable'].append(item)
                out['overdue'].append(item)
                continue
            if event_time is None:
                item['why'] = '事件发生时间不可核 ⇒ 按超期算（不放行）'
                item['age_sec'] = None
                out['overdue'].append(item)
                continue
            age = max(0.0, (now - float(event_time)) / 1000.0 if float(event_time) > 1e11
                      else now - float(event_time))
            item['age_sec'] = round(age, 1)
            if age > grace:
                item['why'] = f'已经 {int(age)}s > grace {int(grace)}s'
                out['overdue'].append(item)
            else:
                out['pending'] += 1
        out['blocked'] = bool(out['overdue'])
        out['exit_code'] = 9 if out['overdue'] else 0
        out['phase'] = 'unanswered_inbound' if out['overdue'] else 'ok'
        out['next'] = 'reply_user' if out['overdue'] else 'continue'
        return out

    def assert_can_advance(self, exp_id=None, *, now=None, grace=TEXT_REPLY_GRACE_SECONDS):
        """持续推进前的硬判据：有超期未回的文字就**不许**报绿（D2）。"""
        status = self.reply_status(exp_id, now=now, grace=grace)
        if status.get('undecidable'):
            raise DecisionError('回复窗口不可判（只读查不到台账）⇒ 不放行推进：'
                                + '；'.join(status.get('problems') or ['不可判']))
        if status['overdue']:
            raise OverdueInbound(status['overdue'], grace)
        return status

    def progress_verdict(self, exp_id=None, *, now=None, grace=TEXT_REPLY_GRACE_SECONDS):
        """给推进器 / hook 用的单一判词：能不能继续。

        ⚠️ 它会 `tick()` 收口过期决策（写盘）⇒ 只读服务拒绝；只读诊断请用 `reply_status()`
        / `status()`（`expired` 字段来自台账现值，不改状态）。
        """
        self._require_write('progress_verdict（tick 会改决策状态）')
        status = self.reply_status(exp_id, now=now, grace=grace)
        expired = self.tick()
        quarantined = []
        undelivered = []
        conn = self.store.connect()
        try:
            quarantined = [r['decision_id'] for r in conn.execute(
                "SELECT decision_id FROM decisions WHERE status='quarantined'"
                ' AND (? IS NULL OR exp_id=?)', (exp_id, exp_id))]
            undelivered = [r['decision_id'] for r in conn.execute(
                "SELECT decision_id FROM decisions WHERE status IN ('prepared','send_failed')"
                ' AND (? IS NULL OR exp_id=?)', (exp_id, exp_id))]
        finally:
            conn.close()
        ok = not status['overdue'] and not quarantined
        return {'ok': ok, 'phase': ('unanswered_inbound' if status['overdue']
                                    else ('quarantined_delivery' if quarantined else 'ok')),
                'next': ('reply_user' if status['overdue']
                         else ('resolve_quarantine' if quarantined else 'continue')),
                'exit_code': 0 if ok else (9 if status['overdue'] else 3),
                'overdue': status['overdue'], 'counts': {key: status[key] for key in
                                                         ('text_events', 'card_events',
                                                          'replied', 'pending')},
                'expired_now': expired, 'quarantined': quarantined,
                'undelivered': undelivered}

    def claim_reply(self, worker, *, exp_id=None, lease_seconds=300):
        self._require_write('claim_reply（领取回复任务）')
        return self.inbox.claim_question(worker, exp_id=exp_id, lease_seconds=lease_seconds)

    def deliver_reply(self, delivery_id, *, worker='decisions', lease_seconds=60):
        """送一条已排队的文字回复（`answer()` 之后调用；回复不属于某份决策）。"""
        self._require_write('deliver_reply（发送文字回复）')
        return self._deliver(None, delivery_id, 'reply', worker, lease_seconds, decision_id='')

    def answer(self, claim, body):
        """回一条文字：原始事件、实验号、收件人三者一致才允许。"""
        self._require_write('answer（排一条文字回复）')
        require_text(body, 'answer body')
        if not isinstance(claim, dict) or claim.get('source') is None:
            raise DecisionError('回复必须绑定一条待回任务')
        event = self.inbox.get(claim['source'], claim['event_id'])
        if event is None or not event['consumed']:
            raise DecisionError('原始事件不存在或尚未应用 ⇒ 不许凭空引用回复')
        payload = json.loads(event['payload_json'])
        if _is_card_click(payload):
            raise DecisionError('卡片点击不是文本回复对象（它的答是执行批准/拒绝 + 回执）')
        if claim['exp_id'] != event['exp_id'] or claim['recipient'] != payload.get('recipient'):
            raise DecisionError('回复的归属与原事件不一致（实验号或收件人）')
        ids = EXP_RE.findall(body)
        if claim['exp_id'] not in ids:
            raise DecisionError(f"回复必须写明实验号 {claim['exp_id']}（B6）")
        foreign = [item for item in ids if item != claim['exp_id']]
        if foreign:
            raise DecisionError('回复里写入了其他实验号：' + '、'.join(sorted(set(foreign))))
        return self.inbox.answer(claim, body)

    # ── 汇总 ──
    def status(self, decision_id=None, *, exp_id=None):
        """按台账重算状态。只读构造下缺库 / 缺表 ⇒ 返回一条「不可判」，⛔ 不建库不迁移。"""
        problem = self._readonly_problem()
        if problem:
            return self._undecidable_row(decision_id, exp_id, problem)
        if decision_id is not None:
            row = self._require(decision_id)
            return self._status_one(row)
        conn = self.store.connect()
        try:
            rows = [dict(r) for r in conn.execute(
                'SELECT * FROM decisions WHERE (? IS NULL OR exp_id=?) ORDER BY created_at, '
                'decision_id', (exp_id, exp_id))]
        finally:
            conn.close()
        return {'exp_id': exp_id, 'decisions': [self._status_one(row) for row in rows],
                'count': len(rows)}

    @staticmethod
    def _undecidable_row(decision_id, exp_id, problem):
        """「读不到」的统一形状：`ok=False` + `undecidable=True` + 原因。

        ⛔ 不是「没有记录」：没有 `decisions: []` 这种看起来正常的空列表，
        所以按 `decisions` 键分派的调用方会走单行分支并拿到 `exit_code=2`。
        """
        return {'ok': False, 'undecidable': True,
                'decision_id': decision_id, 'exp_id': exp_id, 'state': None,
                'problems': [problem], 'exit_code': 2}

    def _status_one(self, row):
        chain = self.chain(row['decision_id'])
        code = self._code_row(row['short_code'])
        conn = self.store.connect()
        try:
            options = [dict(r) for r in conn.execute(
                'SELECT option_id,state FROM decision_options WHERE decision_id=?'
                ' ORDER BY position', (row['decision_id'],))]
            events = [dict(r) for r in conn.execute(
                'SELECT kind,actor,from_state,to_state,payload_json,at FROM decision_events'
                ' WHERE decision_id=? ORDER BY id', (row['decision_id'],))]
        finally:
            conn.close()
        expired = float(row['expires_at']) <= self.clock()
        problems = []
        warnings = []
        if row['status'] == 'awaiting_approval' and not chain['complete']:
            problems.append('状态写着待批，但页与卡的双回执不完整 ⇒ 不承认这条审批链（B4/B5）')
        if row['status'] == 'quarantined':
            problems.append('交付结果不确定 ⇒ 已隔离，⛔ 不自动重发')
        if expired and row['status'] in OPEN_STATES:
            problems.append('已过有效期但状态还没转 expired ⇒ 运行 tick 收口')
        if int(row['needs_user']) and not options:
            problems.append('声明需要人判断但没有选项')
        try:
            document = json.loads(row['payload_json'])
        except Exception:
            document = {}
        binding = document.get('execution_plan')
        execution_sha = execution_fingerprint(binding) if isinstance(binding, dict) else ''
        try:
            research_sha = research_fingerprint(document.get('plan_set') or {})
        except Exception:
            research_sha = ''
        if int(row['needs_user']) and not execution_sha:
            warnings.append('没有绑定实际执行计划 ⇒ 不能用于 Workflow 起跑（要重新 prepare）')
        return {'decision_id': row['decision_id'], 'exp_id': row['exp_id'],
                'state': row['status'], 'resolution': row['resolution'],
                'state_text': STATE_TEXT.get(row['status'], row['status']),
                'scope': row['scope'], 'owner_session': row['owner_session'],
                'execution_sha256': execution_sha, 'execution_bound': bool(execution_sha),
                'research_sha256': research_sha,
                'requested_at': _iso(row['requested_at']),
                'expires_at': _iso(row['expires_at']), 'expired': expired,
                'needs_user': bool(row['needs_user']),
                'judgment_reason': row['judgment_reason'],
                'short_code': {'code': row['short_code'],
                               'state': code['state'] if code else None,
                               'consumed_by': code['consumed_by'] if code else None,
                               'consumed': bool(code and code['state'] == CODE_CONSUMED)},
                'options': options, 'chain': chain, 'events': events,
                'problems': problems, 'warnings': warnings,
                'ok': not problems, 'exit_code': 0 if not problems else 3}

    # ── 主链准入 callback ──
    def authorization_check(self, plan, owner_session, exp_id, *, scope=None, now=None,
                            record=False):
        """`authorization_check(plan, owner_session, exp_id)` —— 主链启动前的准入判据。

        **默认纯只读**（`record=False`）：不写事件、不开写事务 ⇒ 可以在调用方已经持有
        `BEGIN IMMEDIATE` 的路径里反复调用（主链起跑前与领取事务里都会调它）。
        要留痕才显式 `record=True`，那一条会追加 `authorization_checked` 事件。

        返回**字典**（不抛「未授权」异常，便于主链直接落账与打印）：

            {'ok': bool, 'authorized': bool,        # 两个键同值，兼容两种调用方
             'scope': 'small'|'large'|'submission',
             'exp_id', 'owner_session', 'decision_id', 'resolution', 'approved': [...],
             'execution_sha256', 'research_sha256', # 执行规范 / 研究内核指纹
             'problems': [...], 'evidence': {...}, 'exit_code': 0|3}

        判据（全过才 `ok=True`）：

        1. 范围显式声明：`plan.authorization.scope`、`plan.scope` 或 `scope=` 参数之一；
           必须是三个范围之一。`small` 免审批，`large` / `submission` **必须**检索到真实批准。
        2. 批准必须是本实验、本范围、`owner_session` 相同的一份 `resolved` 决策。
        3. **双绑定里的执行那一半**：用 `execution_core()` 对**这一份实际 plan** 重算指纹
           （保留 argv / implementation / budget / case_sets / hypothesis / research 等全部执行
           字段，只剔除自指哈希、`plan_set` 副本与授权引用），必须等于 prepare 时登记的那份
           完整执行规范。⛔ 调用方自填 `plan_sha256` 不作数；⛔ 没绑执行规范的决策
           （只生成了审批页）**不能**用于 Workflow 起跑。
        4. 页与卡的双回执都要在（`chain.complete`，含磁盘审批页哈希校验）。
        5. 短码必须被一条**真实入向事件**消费过，且那次应用有身份校验结果。
        6. 批准不能发生在有效期之后（过点即超时取消，迟到的批准不成立）。
        7. 若 `plan` / `plan.authorization` 指名了 `decision_id`，它必须就是被批准的那一份；
           若指明 `option_id`，批准集合必须包含它。
        """
        now = float(now if now is not None else self.clock())
        result = {'ok': False, 'authorized': False, 'scope': None, 'exp_id': exp_id,
                  'owner_session': owner_session, 'decision_id': None, 'resolution': None,
                  'approved': [], 'execution_sha256': None, 'research_sha256': None,
                  'plan_sha256': None, 'problems': [], 'evidence': {},
                  'checked_at': _iso(now), 'exit_code': 3}
        if record and self.readonly:
            raise ReadonlyServiceError(
                '只读 DecisionService 不能 record=True：准入检查不写事件流；留痕请用可写构造')
        problem = self._readonly_problem()
        if problem:
            result['undecidable'] = True
            result['problems'] = [problem]
            result['exit_code'] = 2
            return result
        declared = plan.get('authorization') if isinstance(plan, dict) else None
        declared = declared if isinstance(declared, dict) else {}
        try:
            if not isinstance(plan, dict):
                raise DecisionError('plan 必须是 JSON 对象')
            require_text(str(exp_id or ''), 'exp_id')
            require_text(str(owner_session or ''), 'owner_session')
            derived = str(scope or declared.get('scope') or plan.get('scope') or '').strip().lower()
            if not derived:
                raise DecisionError('没有声明审批范围（plan.authorization.scope / plan.scope）'
                                    '⇒ 不知道该不该人批，按不放行处理')
            if derived not in SCOPES:
                raise DecisionError(f'审批范围非法：{derived!r}（只能是 {"/".join(SCOPES)}）')
            execution_sha = execution_fingerprint(plan)
            research_sha = research_fingerprint(plan)
        except Exception as exc:
            result['problems'].append(str(exc))
            self._record_authorization(result, plan, exp_id, record)
            return result
        result['scope'] = derived
        result['execution_sha256'] = execution_sha
        result['research_sha256'] = research_sha
        result['plan_sha256'] = execution_sha

        if not SCOPE_POLICY_REQUIRES_APPROVAL[derived]:
            result.update({'ok': True, 'authorized': True, 'resolution': 'scope_exempt',
                           'exit_code': 0,
                           'evidence': {'policy': '小样本机制验证在已授权范围内自主推进'}})
            self._record_authorization(result, plan, exp_id, record)
            return result

        conn = self.store.connect()
        try:
            rows = [dict(r) for r in conn.execute(
                'SELECT * FROM decisions WHERE exp_id=? ORDER BY created_at', (exp_id,))]
        finally:
            conn.close()
        candidates = []
        rejected = []
        unbound = []
        declared_decision = str(plan.get('decision_id')
                                or declared.get('decision_id') or '').strip()
        for row in rows:
            if row['scope'] != derived:
                continue
            if str(row['owner_session'] or '') != str(owner_session):
                continue
            if row['status'] != 'resolved' or row['resolution'] != 'user_approved':
                continue
            if declared_decision and row['decision_id'] != declared_decision:
                rejected.append(f"{row['decision_id']} 不是计划里指明的决策 {declared_decision}")
                continue
            try:
                document = json.loads(row['payload_json'])
            except Exception:
                rejected.append(f"{row['decision_id']} 的方案记录损坏")
                continue
            binding = document.get('execution_plan')
            if not isinstance(binding, dict):
                unbound.append(row['decision_id'])
                continue
            try:
                stored = execution_fingerprint(binding)
            except Exception:
                rejected.append(f"{row['decision_id']} 的执行规范记录损坏")
                continue
            if stored != execution_sha:
                rejected.append(
                    f"{row['decision_id']} 批的是另一版**执行计划**（执行规范指纹不同）："
                    '命令 / 实现 / 预算 / 题集 / 方案集任一项变了都要重新申请')
                continue
            candidates.append((row, stored))
        problems = list(rejected)
        if not candidates:
            if unbound:
                problems.append(
                    '缺 execution binding：' + '、'.join(unbound)
                    + ' 只绑了审批页方案（research），没有绑定实际执行计划 ⇒ 不能用于 Workflow 起跑；'
                    '重新 prepare 时给完整执行规范（`--execution-plan`，或直接传完整 plan）')
            problems.append(
                f'没有找到 {derived} 范围的真实批准（实验 {exp_id}，会话 {owner_session}）：'
                '必须先 prepare → 先页后卡送达 → 用户点击，批准才存在')
            result['problems'] = problems
            self._record_authorization(result, plan, exp_id, record)
            return result

        row, stored = candidates[-1]
        result['decision_id'] = row['decision_id']
        result['resolution'] = row['resolution']
        result['evidence']['execution_sha256'] = stored
        if not self.chain(row['decision_id'])['complete']:
            problems.append('审批页与选择卡的双回执不完整 ⇒ 不承认这次批准（B4/B5）')
        code = self._code_row(row['short_code'])
        if code is None or code['state'] != CODE_CONSUMED:
            problems.append('短码没有被真实入向事件消费 ⇒ 不构成批准')
        elif not code['consumed_by']:
            problems.append('短码消费记录没有事件引用')
        else:
            try:
                source, event_id = json.loads(code['consumed_by'])
            except Exception:
                problems.append('短码消费记录损坏，无法追到原始事件')
            else:
                event = self.inbox.get(source, event_id)
                if event is None or not event['consumed']:
                    problems.append('短码消费指向的事件不存在或未应用')
                else:
                    payload = json.loads(event['payload_json'])
                    if payload.get('decision_id') != row['decision_id'] \
                            or payload.get('exp_id') != exp_id:
                        problems.append('消费事件的决策号 / 实验号与本次不符')
                    if not event['principal']:
                        problems.append('消费事件没有身份校验结果 ⇒ 归属不可核')
                    result['evidence']['event'] = {'source': source, 'event_id': event_id,
                                                   'principal': event['principal']}
        conn = self.store.connect()
        try:
            approved = [r['option_id'] for r in conn.execute(
                "SELECT option_id FROM decision_options WHERE decision_id=? AND state='approved'"
                ' ORDER BY position', (row['decision_id'],))]
        finally:
            conn.close()
        result['approved'] = approved
        if not approved:
            problems.append('批准集合为空')
        wanted_option = plan.get('option_id') or plan.get('option')
        if wanted_option is not None and str(wanted_option) not in approved:
            problems.append(f'批准集合 {approved} 不含本次要跑的选项 {wanted_option}')
        if row['decided_at'] is not None and float(row['decided_at']) > float(row['expires_at']):
            problems.append('批准发生在有效期之后 ⇒ 那次审批已按超时取消，不成立')
        if now < float(row['decided_at'] or 0):
            problems.append('批准时间在将来 ⇒ 时间源不可信，不放行')
        result['problems'] = problems
        result['authorized'] = not problems
        result['ok'] = not problems
        result['exit_code'] = 0 if not problems else 3
        self._record_authorization(result, plan, exp_id, record)
        return result

    def check_authorization(self, plan=None, owner_session=None, exp_id=None, *, scope=None,
                            now=None, record=False):
        """`Admission.check` 调用的别名（关键字调用）：`check_authorization(plan=…, owner_session=…, exp_id=…)`。

        返回与 `authorization_check` 同一个字典（`ok` 与 `authorized` 同值）。
        **默认 `record=False`**：纯只读，可以在 `BEGIN IMMEDIATE` 里安全调用。
        """
        return self.authorization_check(plan, owner_session, exp_id, scope=scope, now=now,
                                        record=record)

    def _record_authorization(self, result, plan, exp_id, record):
        """把一次准入判词追加进事件流（可追溯整合；不改任何决策状态）。

        ⚠️ 只在显式 `record=True` 时写：默认的准入检查必须能在调用方的写事务里跑。
        """
        if not record:
            return
        try:
            with self.store.transaction() as conn:
                if conn.execute('SELECT 1 FROM experiments WHERE id=?', (exp_id,)).fetchone():
                    self.store.emit(conn, exp_id, 'authorization_checked', 'decisions',
                                    {'ok': bool(result.get('ok')),
                                     'authorized': bool(result.get('authorized')),
                                     'scope': result.get('scope'),
                                     'owner_session': result.get('owner_session'),
                                     'decision_id': result.get('decision_id'),
                                     'execution_sha256': result.get('execution_sha256'),
                                     'research_sha256': result.get('research_sha256'),
                                     'plan_sha256': result.get('plan_sha256'),
                                     'problems': list(result.get('problems') or [])})
        except Exception as exc:  # 记账失败不改变判词，但要留痕
            result.setdefault('evidence', {})['record_error'] = f'{type(exc).__name__}: {exc}'


def _is_card_click(payload):
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get('card_action'), dict):
        return True
    if str(payload.get('kind') or '') == 'card_action':
        return True
    return bool(payload.get('decision_id')) and \
        str(payload.get('intent') or '').strip().lower() in CARD_CLICK_INTENTS


def make_authorization_check(service):
    """把 `service.authorization_check` 包成主链要的裸回调。

    用法（主链注入）：

        from kernel.autoresearch.decisions import DecisionService, make_authorization_check
        check = make_authorization_check(service)
        verdict = check(plan, owner_session, exp_id)      # 位置参数，签名为 (plan, owner_session, exp_id)
        if not verdict['authorized']:                     # ⛔ 不准许「先跑再补批」
            raise SystemExit(f"未获授权：{verdict['problems']}")

    ⚠️ 回调**只读**台账、只返回判词；它不产生批准，也不消费短码。
    """
    if service is None or not callable(getattr(service, 'authorization_check', None)):
        raise DecisionError('make_authorization_check 需要一个 DecisionService')

    def authorization_check(plan, owner_session, exp_id):
        return service.authorization_check(plan, owner_session, exp_id)

    return authorization_check


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(prefix='.tmp-', dir=str(path.parent))
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return path
