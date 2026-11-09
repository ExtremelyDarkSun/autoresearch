# GENERATED-BY: feishu 真实通道适配层（标准库实现）, 2026-11-10 +08:00
# LAST-EDIT: 2026-11-10 +08:00（加密事件改为默认内核解密器：可选 cryptography，缺依赖 501 fail-closed）
# PURPOSE: 补齐通用决策链「只有 FakeChannel」的缺口 —— 可运行的飞书发送/回执核验/入向验签与规范化
# ORIGIN: kernel/autoresearch/decisions.py 的 ApprovalChannel 协议 + 飞书开放平台服务端 API 文档
# CUMULATIVE-DELTA: 首版。token、上传、file/interactive/text 三种消息、GET 核验、webhook 验签与规范化。
#   2026-11-10 补：接入 channels/crypto.py 解密飞书加密事件（先验签、再解密、再 token/identity/replay）；
#   缺 cryptography ⇒ reason=decryption_unavailable(501)，密文不合法 ⇒ decryption_failed(400)，不降级明文。
"""飞书（Feishu / Lark）通道适配层 —— 真实 HTTP 实现，默认不发、可离线验收。

## 它接到哪儿

`kernel/autoresearch/decisions.py` 的 `ApprovalChannel` 协议（10 个方法）：
`describe` / `health` / `send_file` / `send_card` / `send_text` / `query` /
`verify_receipt` / `authenticate` / `poll` / `subscribe`。
`DecisionService` 只按交付 kind 路由：`approval_page → send_file`、`card → send_card`、
`reply → send_text`，回执由核心用 `_coerce_receipt` 收紧、用 `_verify_receipt` 复核。

## 失败语义（最重要的一条，与协议原文一致）

- **确定没发出去** ⇒ `NotSent`（来自 `runtime.delivery`）。核心回到 `queued`，允许重试。
- **不确定**（超时 / 连接中断 / 5xx / 429）⇒ `AmbiguousSend`（任意非 `NotSent` 异常）。
  核心落 `unknown` 并**隔离**，⛔ 绝不自动重发；`query()` 对未确认记录一律返回 `None`。
- ⛔ 不把「不确定」报成成功，也不把「不确定」报成 `NotSent`。

适配层自己还有第二道闸：本地 SQLite 台账按 `request_key` 记账，
`in_flight → confirmed | not_sent | unknown`。同一个 `request_key` 再来一次时：
`confirmed` 直接回原回执（不重发）、`unknown` 直接抛 `AmbiguousSend`、`not_sent` 才允许再发一次。

## 凭据

配置文件里**只有引用**：`env:FEISHU_APP_ID` 这类字符串。值只从环境变量（或注入的
`credential_source`）取，且**不进任何日志、回执、异常文本或台账列**。
`FeishuConfig` 在构造时就拒绝字面量凭据（`app_id` / `app_secret` / `verification_token` /
`encrypt_key` 之类键一旦出现直接报错）。

## 收发两条线

- **发**：`POST /open-apis/auth/v3/tenant_access_token/internal` 取 token →
  `POST /open-apis/im/v1/files`（multipart，审批页 HTML）→
  `POST /open-apis/im/v1/messages`（`file` / `interactive` / `text`）。
  文字回复走 `POST /open-apis/im/v1/messages/:message_id/reply`，**引用入向那条消息**；
  `:message_id` 只从本地「已验入向事件 → message_id」映射取，⛔ 不收 caller 自报的 message_id。
- **收**：`handle_webhook(headers, raw_body)` 先验签、再解析；规范化成
  `DecisionService.ingest(source, event)` 认的形状；认证只能消费**本适配层持久过的已认证事件**。

## 卡片的 wire 字段（与决策侧 c465bf18 对齐后的**确定**契约，⛔ 不做正文解析、不派生短码）

`send_card(payload, artifact)` 要求载荷里有**显式**三项，缺任一就 `NotSent`（fail-closed）：

- `payload['short_code']`：本次决策短码（核心 `decisions` 表里的那一串）。
- `payload['card_options']`：**只含本轮待批**的选项，形态
  `[{"option_id": "A", "label": "换抓手 A"}, ...]`（适配层也接受纯字符串 `"A"`，
  但决策侧按前者发）。
- `payload['card_action_template']`：按钮回传的公共部分
  `{"intent": "approve", "exp_id": ..., "decision_id": ..., "recipient": ...,
  "short_code": ...}`。适配层只补 `channel` 与 `options` 两个键。

按钮回传 `value` = `card_action_template` + `{"channel": "feishu_channel",
"intent": "approve"|"reject", "options": [oid]}`。每个待批选项给两个按钮（批准 / 否决），
另有「全否」按钮（`reject` + `options: []`）。核心据此区分：
`approve [A]` 裁决并消费短码；`reject [A]` 部分否决、保持待批且**不消费**短码；
`reject []` 才是全否。

点击回调 `card.action.trigger` 到达后，适配层**用本地已发卡片台账**反查
`exp_id / decision_id / short_code / recipient / 选项集合`（⛔ 不采信 body 自述），再规范成
`{..., "card_action": {...}, "intent": ..., "options": [...]}` 交给核心。
回执照原 `payload_hash` 核（卡片 wire 字段不进 hash，`delivery.payload_hash` 不变）。

## 实测边界（⛔ 不要读成「已验证」）

本模块的**平台行为未实测**：没有真实凭据、没有往真实租户发过任何消息、没有加过真实 webhook。
已验的是「离线注入 transport / credential_source 时适配层自身的行为」：状态机、回执形状、
GET 复核、验签顺序、重放拒绝、允许列表，以及用可选 `cryptography` 解密**加密事件**
（官方固定向量 + 本地生成的加密 challenge / 文字 / 卡片点击走完整 handle_webhook → 认证 →
ingest）。真实租户上的送达、权限、群 @ 条件、加密推送仍需部署后实测。

## 加密事件：默认内核解密器（可选 `cryptography` 依赖）

飞书官方文档写明：**应用里配置 Encrypt Key ⇒ 平台加密推送所有事件**（请求网址校验的
`challenge` 也加密），没配才明文；而签名校验的算式 `SHA256(timestamp+nonce+encrypt_key+body)`
本身就要求 Encrypt Key。所以「要验签」和「会收到密文」是同一件事 —— 不解密就等于收不到任何
审批。处理顺序（与安全要求一致）：

1. **先对原始（密文）body 验签**（`compute_signature` + `hmac.compare_digest`）；
2. 再做重放去重（`seen` 表，按 `timestamp|nonce|signature`）；
3. 只有验签与去重都通过，才调 `kernel/autoresearch/channels/crypto.py` 解密；
4. 解密后的明文重新过 `header.token`、发送人允许列表、绑定、卡片台账等全部检查 ——
   **不因为「已经解密」而降低任何判据**。

失败语义（fail-closed，⛔ 不降级成明文解析）：

- 缺依赖 / 版本不可用 ⇒ `reason='decryption_unavailable'` + HTTP **501**，detail 指向
  `python -m pip install -r requirements-feishu.txt`；
- 密文不合法（base64 / 长度 / PKCS7 填充 / UTF-8 / 非 JSON 对象 / 重复键 / 非有限字面量）⇒
  `reason='decryption_failed'` + HTTP **400**；
- 两种都**不 ingest、不认证**；同一 `event_id` 也不会因为解密失败被当作已处理。

安装（核心其余功能仍只用标准库）：

    python -m pip install -r requirements-feishu.txt

`FeishuChannel.health()` 不把「解密后端缺失」列为不可用理由（发送路径仍可跑），
但 `channels doctor` 会把它算成 blocking（`channels_cli` 的 `encryption_backend` 检查），
且 `channels listen` 会在**起服务之前**就拒绝（不建库、不监听）。

## 引用来源（open.feishu.cn 官方文档，2026-11-10 核对）

- 自建应用获取 tenant_access_token：
  https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
  （POST `/open-apis/auth/v3/tenant_access_token/internal`，body `{app_id, app_secret}`，
  返回 `{code, msg, tenant_access_token, expire}`，有效期 7200s）
- 上传文件：https://open.feishu.cn/document/server-docs/im-v1/file/create
  （POST `/open-apis/im/v1/files`，multipart，`file_type` / `file_name` / `file`；
  `file_type` 枚举缺省用 `stream`；≤30MB 且不可为空；返回 `data.file_key`）
- 发送消息：https://open.feishu.cn/document/server-docs/im-v1/message/create
  （POST `/open-apis/im/v1/messages?receive_id_type=...`，body `{receive_id, msg_type, content, uuid}`；
  `msg_type` 取 `text|file|interactive`；`content` 是对应结构序列化后的**字符串**；
  `uuid` 用于请求去重，1 小时内同 uuid 至多成功一条）
- 回复消息：https://open.feishu.cn/document/server-docs/im-v1/message/reply
  （POST `/open-apis/im/v1/messages/:message_id/reply`，body `{content, msg_type, uuid}`）
- 获取指定消息：https://open.feishu.cn/document/server-docs/im-v1/message/get
  （GET `/open-apis/im/v1/messages/:message_id`，返回 `data.items[]`，含 `chat_id`/
  `msg_type`/`sender`/`deleted`/`body.content`；`card_msg_content_type=user_card_content`
  可返回发送时的原始卡片 JSON 1.0/2.0）
- 接收消息事件 `im.message.receive_v1`：
  https://open.feishu.cn/document/server-docs/im-v1/message/events/receive
  （`header.event_id` / `header.event_type` / `header.create_time`（毫秒）/`header.token`；
  `event.sender.sender_id.open_id` / `event.sender.sender_type`；`event.message.message_id` /
  `chat_id` / `chat_type` / `message_type` / `content`（JSON 字符串）/ `mentions[].mentioned_type`）
- 事件订阅安全校验与解密（签名 `SHA256(timestamp + nonce + encrypt_key + body)`、
  `X-Lark-Request-Timestamp` / `X-Lark-Request-Nonce` / `X-Lark-Signature`、AES-256-CBC 解密）：
  https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case
- 请求地址校验（`type=url_verification` 的 `challenge` 原样返回，1 秒内；加密时先解密）：
  https://open.feishu.cn/document/event-subscription-guide/event-subscriptions/faq
- 卡片回传交互回调 `card.action.trigger`：
  https://open.feishu.cn/document/feishu-cards/card-callback-communication
  （`header.event_id` / `header.event_type` / `event.operator.open_id` / `event.action.value` /
  `event.action.tag` / `event.context.open_message_id` / `event.context.open_chat_id`；
  3 秒内响应，可返回 `{"toast": {"type": "success", "content": "..."}}`）
- 卡片 JSON 结构：https://open.feishu.cn/document/feishu-cards/card-json-structure
  （本模块用 1.0 结构：`config` / `header` / `elements`，`button.value` 原样回传）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

try:
    from kernel.autoresearch.runtime.delivery import NotSent
    from kernel.autoresearch.runtime.store import canonical_json
    from kernel.autoresearch.channels import crypto as channel_crypto
except ImportError:  # pragma: no cover - 直接以脚本路径导入时的兜底
    from kernel.autoresearch.runtime.delivery import NotSent
    from kernel.autoresearch.runtime.store import canonical_json
    from kernel.autoresearch.channels import crypto as channel_crypto


# ────────────────────────────── 常量 ──────────────────────────────

#: 通道稳定短名。核心把 `describe()['adapter']` 算进发送方指纹并逐条比对回执。
ADAPTER = 'feishu'

#: 入向来源名。`DecisionService.ingest(source, event)` 用它做幂等键的一半。
SOURCE = 'feishu'

#: webhook 路由默认路径。父 agent CLI 自己决定挂哪儿。
WEBHOOK_PATH = '/feishu/webhook'

DEFAULT_API_BASE = 'https://open.feishu.cn'
TOKEN_PATH = '/open-apis/auth/v3/tenant_access_token/internal'
FILES_PATH = '/open-apis/im/v1/files'
MESSAGES_PATH = '/open-apis/im/v1/messages'

#: 单次请求的超时（秒），发送与读取共用 `FeishuConfig.timeout`。
#: ⛔ 必填，没有「等到天荒地老」这个选项。
SEND_TIMEOUT_S = 10.0

#: 单次响应体上限，防止被异常大包拖死。
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: webhook 请求体上限。
MAX_WEBHOOK_BYTES = 1024 * 1024

#: 允许的时钟偏差（秒）。超过就按重放拒（官方要求按收到的原始 body 验签，时差窗口是常见的补强）。
MAX_SKEW_S = 300.0

#: 飞书上传单文件上限（30MB）。超过就确定发不出去 ⇒ NotSent。
MAX_UPLOAD_BYTES = 30 * 1024 * 1024

#: 卡片消息体上限（30KB）。
MAX_CARD_BYTES = 30 * 1024

#: 凭证引用前缀。配置文件里只允许这一种形态。
REF_PREFIX = 'env:'

#: 卡片回传意图（与 decisions.CARD_CLICK_INTENTS 对齐）。
CLICK_INTENTS = frozenset(('approve', 'approval', 'reject', 'deny'))

#: 允许的 `receive_id_type`。
RECEIVE_ID_TYPES = frozenset(('open_id', 'union_id', 'user_id', 'email', 'chat_id'))

#: 台账状态：只向前，不回头（`not_sent` 是唯一允许重试的状态）。
ST_IN_FLIGHT = 'in_flight'
ST_CONFIRMED = 'confirmed'
ST_NOT_SENT = 'not_sent'
ST_UNKNOWN = 'unknown'

CARD_WIRE_KEYS = ('short_code', 'card_options', 'card_action_template')

#: 实验号形态（与 `runtime.delivery` 的 `exp-` 规则一致）。
EXP_ID_RE = re.compile(r'exp-[A-Za-z0-9][A-Za-z0-9_-]*')


# ────────────────────────────── 异常 ──────────────────────────────

class FeishuError(RuntimeError):
    """适配层自身的拒绝（配置、载荷、台账）。"""


class FeishuConfigError(FeishuError, ValueError):
    """部署配置不成立（含：配置文件里出现了凭据**值**）。"""


class AmbiguousSend(FeishuError):
    """发送结果**未知**（超时 / 中断 / 5xx / 429）。

    核心收到非 `NotSent` 的异常会把交付落 `unknown` 并隔离，⛔ 不自动重发。
    """


class NotAuthenticated(FeishuError):
    """入向事件不可信：没在本适配层认证过、与台账不一致、或发送人不在允许列表。"""


# ────────────────────────────── 小工具 ──────────────────────────────

def _require_text(value, name) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeishuError(f'{name} 必须是非空字符串（现在是 {value!r}）')
    return value.strip()


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(value) -> str:
    return _sha256_hex(canonical_json(value).encode('utf-8'))


def _lower_headers(headers) -> dict:
    """把 headers 统一成小写键的 dict。接受 dict 或 (key, value) 序列。"""
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, 'items') else headers
    out = {}
    for key, value in items:
        if not isinstance(key, str):
            continue
        out[key.strip().lower()] = (value or '').strip() if isinstance(value, str) else str(value)
    return out


def _millis(value, *, unit: str = 'ms'):
    """把事件时间戳统一成**毫秒**浮点。不可核就返回 None（核心按「时间不可核」处理）。"""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    if unit == 'unknown':
        # 卡片回调文档写的是微秒级，消息事件是毫秒级；用数量级区分。
        return number / 1000.0 if number >= 1e14 else number
    if unit == 'us':
        return number / 1000.0
    return number


def compute_signature(timestamp: str, nonce: str, encrypt_key: str, raw_body: bytes) -> str:
    """飞书签名：`SHA256(timestamp + nonce + encrypt_key + 原始 body)`。

    ⚠️ 必须对**收到的原始字节**算，⛔ 不能反序列化后重排再算
    （官方示例原话：body 指整个请求体，不要在反序列化后再计算）。
    """
    head = f'{timestamp}{nonce}{encrypt_key}'.encode('utf-8')
    return _sha256_hex(head + bytes(raw_body))


def env_ref(name: str) -> str:
    """写配置文件时用的凭据引用：`env:FEISHU_APP_ID`。"""
    return REF_PREFIX + _require_text(name, '环境变量名')


def urllib_transport(method: str, url: str, body, headers: dict, timeout: float):
    """默认传输：一次请求，超时必有。返回 `(status, raw_bytes)`。

    ⛔ 不打印 URL 查询串、不打印请求头（`Authorization` 在里面）。
    HTTP 4xx/5xx 也当**响应**返回，交给上层按业务码分类，避免把「平台明确拒绝」
    混成「网络不确定」。
    """
    request = urllib.request.Request(url, data=body, headers=dict(headers or {}),
                                     method=str(method).upper())
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            return int(response.status), response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), exc.read(MAX_RESPONSE_BYTES) or b''
        finally:
            exc.close()


def _multipart(fields: dict, *, file_name: str, file_bytes: bytes,
               content_type: str = 'application/octet-stream'):
    """手工拼 multipart/form-data（标准库没有现成编码器）。"""
    boundary = '----AutoresearchFeishu' + os.urandom(8).hex()
    safe_name = re.sub(r'[\r\n"]', '_', str(file_name)) or 'upload.bin'
    out = bytearray()
    for name, value in fields.items():
        out += (f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f'{value}\r\n').encode('utf-8')
    out += (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n').encode('utf-8')
    out += bytes(file_bytes)
    out += f'\r\n--{boundary}--\r\n'.encode('utf-8')
    return bytes(out), f'multipart/form-data; boundary={boundary}'


def _json_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')


# ────────────────────────────── 配置 ──────────────────────────────

@dataclass(frozen=True)
class FeishuConfig:
    """部署配置。**只放引用与允许列表，不放任何凭据值。**

    凭据字段（`app_id_ref` / `app_secret_ref` / `verification_token_ref` / `encrypt_key_ref`）
    必须是 `env:NAME` 形态；构造时校验，写错直接 `FeishuConfigError`。
    """

    app_id_ref: str = ''
    app_secret_ref: str = ''
    verification_token_ref: str = ''
    encrypt_key_ref: str = ''
    #: 默认不发。部署要发送必须显式打开。
    send_enabled: bool = False
    #: `chat_id`（群）/ `open_id`（单聊）。收件人一律按这个类型解释。
    receive_id_type: str = 'chat_id'
    api_base: str = DEFAULT_API_BASE
    #: 收件人允许列表。空 ⇒ 不发（fail-closed）。
    allowed_recipients: tuple = ()
    #: 允许让人拍板的 open_id 列表。空 ⇒ 谁的入向都不认证。
    allowed_sender_open_ids: tuple = ()
    #: 显式 chat/发送人 → 实验绑定。⛔ 否则只能靠「回复我们发过的消息」反查。
    chat_bindings: dict = field(default_factory=dict)
    #: 本地台账（SQLite）。request_key ↔ message_id 的持久绑定在这里。
    ledger_path: str = ''
    #: 群聊文字必须 @ 机器人才能触发。这是**记录**给 health/回执的触达条件。
    mention_required: bool = True
    timeout: float = SEND_TIMEOUT_S
    max_skew_seconds: float = MAX_SKEW_S
    version: str = 'feishu-channel/1'

    def __post_init__(self):
        for name in ('app_id_ref', 'app_secret_ref', 'verification_token_ref',
                     'encrypt_key_ref'):
            value = getattr(self, name)
            if value is None or value == '':
                continue
            if not isinstance(value, str) or not value.startswith(REF_PREFIX) \
                    or len(value) <= len(REF_PREFIX):
                raise FeishuConfigError(
                    f'{name} 必须写成 "{REF_PREFIX}NAME" 的环境变量引用：'
                    f'配置文件不许含凭据值（现在是 {value!r}）')
        if not isinstance(self.api_base, str) or not self.api_base.startswith(('https://', 'http://')):
            raise FeishuConfigError('api_base 必须是 http(s) URL')
        if self.receive_id_type not in RECEIVE_ID_TYPES:
            raise FeishuConfigError(
                f'receive_id_type 只能是 {sorted(RECEIVE_ID_TYPES)}，现在是 {self.receive_id_type!r}')
        for name in ('allowed_recipients', 'allowed_sender_open_ids'):
            value = getattr(self, name)
            if isinstance(value, str):
                raise FeishuConfigError(f'{name} 必须写成列表，不能是单个字符串')
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise FeishuConfigError(f'{name} 里每项都必须是非空字符串')
        if not isinstance(self.chat_bindings, dict):
            raise FeishuConfigError('chat_bindings 必须是对象（chat_id/open_id → exp_id）')
        for key, value in self.chat_bindings.items():
            if not isinstance(key, str) or not key.strip():
                raise FeishuConfigError('chat_bindings 的键必须是非空字符串')
            if not isinstance(value, str) or not EXP_ID_RE.fullmatch(value.strip()):
                raise FeishuConfigError(
                    f'chat_bindings[{key!r}] 必须是 exp-xx 形态的实验号，现在是 {value!r}')
        if self.timeout <= 0 or self.max_skew_seconds <= 0:
            raise FeishuConfigError('timeout / max_skew_seconds 必须为正数')

    # ── 派生量（都不含凭据值）──
    @property
    def refs(self) -> tuple:
        return tuple(ref for ref in (self.app_id_ref, self.app_secret_ref,
                                     self.verification_token_ref, self.encrypt_key_ref) if ref)

    @property
    def config_ref(self) -> str:
        return ','.join(self.refs)

    @property
    def allowlist_fingerprint(self) -> str:
        """按**真实配置值**（允许列表 + id 类型）算的指纹，不含凭据。"""
        return _canonical_sha256({'recipients': sorted(self.allowed_recipients),
                                  'senders': sorted(self.allowed_sender_open_ids),
                                  'receive_id_type': self.receive_id_type})[:16]


#: 明确属于「配置文件里写了凭据值」的键名 —— 见到就拒。
FORBIDDEN_CONFIG_KEYS = frozenset((
    'app_id', 'app_secret', 'tenant_access_token', 'token', 'secret',
    'verification_token', 'encrypt_key', 'password', 'bearer',
))

_MAPPING_FIELDS = frozenset((
    'app_id_ref', 'app_secret_ref', 'verification_token_ref', 'encrypt_key_ref',
    'send_enabled', 'receive_id_type', 'api_base', 'allowed_recipients',
    'allowed_sender_open_ids', 'chat_bindings', 'ledger_path', 'mention_required',
    'timeout', 'max_skew_seconds', 'version',
))


def from_mapping(mapping: dict) -> FeishuConfig:
    """从部署配置字典建配置。⛔ 出现凭据值直接拒（而不是悄悄忽略）。"""
    if not isinstance(mapping, dict):
        raise FeishuConfigError('配置必须是对象')
    bad = sorted(set(mapping) & FORBIDDEN_CONFIG_KEYS)
    if bad:
        raise FeishuConfigError(
            '部署配置里不许出现凭据值键：' + '、'.join(bad)
            + ' ⇒ 改成 app_id_ref / app_secret_ref / verification_token_ref / encrypt_key_ref 的 env: 引用')
    unknown = sorted(set(mapping) - _MAPPING_FIELDS)
    if unknown:
        raise FeishuConfigError('未知配置键：' + '、'.join(unknown))
    values = dict(mapping)
    for name in ('allowed_recipients', 'allowed_sender_open_ids'):
        if name in values:
            values[name] = tuple(values[name])
    return FeishuConfig(**values)


# ────────────────────────────── 台账 ──────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound(
  id INTEGER PRIMARY KEY,
  request_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL,
  kind TEXT NOT NULL,
  message_id TEXT,
  exp_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  chat_id TEXT,
  payload_hash TEXT NOT NULL,
  decision_id TEXT,
  short_code TEXT,
  options_json TEXT,
  path TEXT,
  bytes INTEGER,
  sha256 TEXT,
  msg_type TEXT,
  content_json TEXT,
  content_sha256 TEXT,
  receipt_json TEXT,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS outbound_message
  ON outbound(message_id) WHERE message_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS inbound(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  event_id TEXT NOT NULL,
  message_id TEXT,
  chat_id TEXT,
  principal TEXT,
  exp_id TEXT,
  decision_id TEXT,
  normalized_json TEXT NOT NULL,
  raw_sha256 TEXT,
  authenticated_at REAL NOT NULL,
  UNIQUE(source, event_id));
CREATE TABLE IF NOT EXISTS seen(
  seen_key TEXT PRIMARY KEY,
  at REAL NOT NULL);
"""


class _Ledger:
    """适配层自己的本地台账。

    三张表各管一件事，都不许被核心代劳：

    - `outbound`：`request_key` ↔ `message_id` 的**持久绑定**与状态机。
      它是「网络超时不重发」的唯一依据，也是 `query()` 只读的来源。
    - `inbound`：本适配层**亲自验签并规范化过**的入向事件。`authenticate()` 只认这张表。
    - `seen`：webhook 重放保护（同一签名只消费一次）。
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')
        return conn

    # ── 出向 ──
    def begin(self, record: dict, *, now: float):
        """占位。返回 `(state, row_dict|None)`；`state` ∈ new/retry/confirmed/ambiguous。"""
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM outbound WHERE request_key=?',
                               (record['request_key'],)).fetchone()
            if row is None:
                conn.execute(
                    'INSERT INTO outbound(request_key,state,kind,exp_id,recipient,payload_hash,'
                    'decision_id,short_code,options_json,path,bytes,sha256,attempts,'
                    'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)',
                    (record['request_key'], ST_IN_FLIGHT, record['kind'], record['exp_id'],
                     record['recipient'], record['payload_hash'], record.get('decision_id') or '',
                     record.get('short_code') or '', record.get('options_json') or '',
                     record.get('path') or '', int(record.get('bytes') or 0),
                     record.get('sha256') or '', now, now))
                conn.commit()
                return 'new', None
            state = str(row['state'])
            if state == ST_CONFIRMED:
                conn.commit()
                return 'confirmed', dict(row)
            if state == ST_NOT_SENT:
                conn.execute('UPDATE outbound SET state=?,error=NULL,attempts=attempts+1,'
                             'updated_at=? WHERE id=?', (ST_IN_FLIGHT, now, row['id']))
                conn.commit()
                return 'retry', dict(row)
            # in_flight（上一次进程死在半路）或 unknown ⇒ 结果不确定，⛔ 不重发
            conn.commit()
            return 'ambiguous', dict(row)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def confirm(self, *, request_key: str, message_id: str, chat_id: str, msg_type: str,
                content_json: str, content_sha256: str, receipt: dict, now: float):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE outbound SET state=?,message_id=?,chat_id=?,msg_type=?,content_json=?,"
                "content_sha256=?,receipt_json=?,error=NULL,updated_at=? WHERE request_key=?",
                (ST_CONFIRMED, message_id, chat_id, msg_type, content_json, content_sha256,
                 canonical_json(receipt), now, request_key))
            conn.commit()
        finally:
            conn.close()

    def fail(self, *, request_key: str, state: str, error: str, now: float):
        if state not in (ST_NOT_SENT, ST_UNKNOWN):
            raise FeishuError(f'台账状态非法：{state!r}')
        conn = self._connect()
        try:
            conn.execute('UPDATE outbound SET state=?,error=?,updated_at=? WHERE request_key=?',
                         (state, str(error)[:800], now, request_key))
            conn.commit()
        finally:
            conn.close()

    def by_request_key(self, request_key: str):
        conn = self._connect()
        try:
            row = conn.execute('SELECT * FROM outbound WHERE request_key=?',
                               (request_key,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def by_message_id(self, message_id: str):
        if not isinstance(message_id, str) or not message_id.strip():
            return None
        conn = self._connect()
        try:
            row = conn.execute('SELECT * FROM outbound WHERE message_id=?',
                               (message_id.strip(),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def card_by_decision(self, decision_id: str):
        """最近一次**已确认送达**的选择卡。卡片点击只认它。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM outbound WHERE kind='card' AND decision_id=? AND state=?"
                ' ORDER BY id DESC LIMIT 1', (decision_id, ST_CONFIRMED)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def card_by_short_code(self, short_code: str):
        """按短码找**已确认送达**的选择卡。文字回复里的短码只能靠它换回实验号。"""
        if not isinstance(short_code, str) or not short_code.strip():
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM outbound WHERE kind='card' AND short_code=? AND state=?"
                ' ORDER BY id DESC LIMIT 1', (short_code.strip().upper(), ST_CONFIRMED)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── 入向 ──
    def put_inbound(self, record: dict, *, now: float):
        """写入已认证事件。返回 `(row, created)`；同 event_id 不同内容抛 `FeishuError`。"""
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM inbound WHERE source=? AND event_id=?',
                               (record['source'], record['event_id'])).fetchone()
            if old is not None:
                conn.commit()
                if old['normalized_json'] != record['normalized_json']:
                    raise FeishuError(f"事件号 {record['event_id']!r} 对应不同内容（拒绝覆盖）")
                return dict(old), False
            cursor = conn.execute(
                'INSERT INTO inbound(source,event_id,message_id,chat_id,principal,exp_id,'
                'decision_id,normalized_json,raw_sha256,authenticated_at)'
                ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                (record['source'], record['event_id'], record.get('message_id') or '',
                 record.get('chat_id') or '', record.get('principal') or '',
                 record.get('exp_id') or '', record.get('decision_id') or '',
                 record['normalized_json'], record.get('raw_sha256') or '', now))
            conn.commit()
            row = conn.execute('SELECT * FROM inbound WHERE seq=?', (cursor.lastrowid,)).fetchone()
            return dict(row), True
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def inbound_by_event(self, source: str, event_id: str):
        conn = self._connect()
        try:
            row = conn.execute('SELECT * FROM inbound WHERE source=? AND event_id=?',
                               (source, event_id)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def inbound_after(self, after_seq: int, limit: int) -> list:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(
                'SELECT * FROM inbound WHERE seq>? ORDER BY seq LIMIT ?',
                (int(after_seq), int(limit)))]
        finally:
            conn.close()

    # ── 重放保护 ──
    def mark_seen(self, seen_key: str, *, now: float) -> bool:
        """首次见到返回 True；已见过（重放）返回 False。"""
        conn = self._connect()
        try:
            cursor = conn.execute('INSERT OR IGNORE INTO seen(seen_key,at) VALUES(?,?)',
                                  (seen_key, now))
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()


# ────────────────────────────── 卡片 ──────────────────────────────

def card_option_pairs(options: Iterable) -> list:
    """`card_options` → `[(option_id, label), ...]`。只接受显式 ID，⛔ 不猜。

    决策侧发的是 `[{"option_id": "A", "label": "换抓手 A"}]`；纯字符串也认（`"A"`），
    但两者都不允许出现空 ID —— 空 ID 会把「批准了哪一条」变成猜。
    """
    pairs = []
    if not isinstance(options, (list, tuple)) or not options:
        raise NotSent('选择卡缺 payload.card_options（本轮待批选项列表）⇒ fail-closed 不发卡')
    for item in options:
        if isinstance(item, dict):
            option_id = _text(item.get('option_id'))
            label = _text(item.get('label')) or option_id
        elif isinstance(item, str):
            option_id = item.strip()
            label = option_id
        else:
            raise NotSent(f'card_options 里有不认识的项：{item!r}（要 option_id 或字符串）')
        if not option_id:
            raise NotSent('card_options 里有空 option_id ⇒ 不发卡（无法指名批准哪一条）')
        if any(option_id == known for known, _ in pairs):
            raise NotSent(f'card_options 里 option_id 重复：{option_id!r}')
        pairs.append((option_id, label))
    return pairs


def _as_pairs(options) -> list:
    """`card_options` 原样（dict / str）或已归一的 `[(id, label)]` 都收。"""
    items = list(options or [])
    if items and all(isinstance(item, tuple) and len(item) == 2
                     and isinstance(item[0], str) for item in items):
        return [(option_id, label) for option_id, label in items]
    return card_option_pairs(items)


def build_decision_card(payload: dict, *, short_code: str, options: Iterable,
                        action_template: dict) -> dict:
    """把核心的卡片正文变成飞书 1.0 卡片 JSON。

    ⚠️ 短码、待批选项、按钮回传模板**只能来自载荷**（`short_code` / `card_options` /
    `card_action_template`）：适配层不做正文解析、不派生短码 —— 猜错会把别轮的答复认成本轮的授权。

    `options` 既接受 `card_options` 原样（`[{"option_id": ...}]`），也接受
    `card_option_pairs()` 的结果（`[(option_id, label)]`）。

    每个待批选项给两个按钮：「批准 X」(`approve [X]`) 与「否决 X」(`reject [X]`)，
    另加「全否」(`reject []`)。核心用 `options` 是否为空区分「部分否决」与「全否」。
    """
    body = str(payload.get('body') or '')
    if not body.strip():
        raise NotSent('选择卡正文为空 ⇒ 不发卡')
    pairs = _as_pairs(options)
    if not pairs:
        raise NotSent('选择卡缺 payload.card_options（本轮待批选项列表）⇒ fail-closed 不发卡')
    exp_id = _require_text(payload.get('exp_id'), 'payload.exp_id')
    recipient = _require_text(payload.get('recipient'), 'payload.recipient')
    decision_id = _require_text(payload.get('decision_id'), 'payload.decision_id')
    short_code = _require_text(short_code, 'payload.short_code').upper()
    if not isinstance(action_template, dict):
        raise NotSent('payload.card_action_template 必须是对象 ⇒ fail-closed 不发卡')
    template = {key: action_template[key] for key in action_template
                if key not in ('channel', 'options', 'intent')}
    for key, expected in (('exp_id', exp_id), ('decision_id', decision_id),
                          ('recipient', recipient), ('short_code', short_code)):
        declared = _text(template.get(key))
        if declared and declared.upper() != expected.upper():
            raise NotSent(f'card_action_template.{key}={declared!r} 与载荷 {expected!r} 不一致'
                          ' ⇒ 不发卡（会把别轮的答复认成本轮）')

    def button(intent, label, option_ids, button_type='default'):
        value = dict(template)
        value['channel'] = 'feishu_channel'
        value['intent'] = intent
        value['options'] = list(option_ids)
        return {'tag': 'button', 'type': button_type,
                'text': {'tag': 'plain_text', 'content': label},
                'value': value}

    actions = []
    for option_id, label in pairs:
        actions.append(button('approve', f'批准 {label}', [option_id], 'primary'))
        actions.append(button('reject', f'否决 {label}', [option_id]))
    actions.append(button('reject', '全否', [], 'danger'))
    title = f'{exp_id} {decision_id} / {short_code}'
    return {
        'config': {'wide_screen_mode': True, 'update_multi': True},
        'header': {'title': {'tag': 'plain_text', 'content': title[:120]},
                   'template': 'blue'},
        'elements': [
            {'tag': 'div', 'text': {'tag': 'lark_md', 'content': body}},
            {'tag': 'hr'},
            {'tag': 'action', 'actions': actions},
            {'tag': 'note', 'elements': [
                {'tag': 'plain_text',
                 'content': '点按钮即以本机身份作答；无回复或超时不构成批准。'}]},
        ],
    }


# ────────────────────────────── 通道 ──────────────────────────────

class FeishuChannel:
    """飞书通道适配层。默认 `send_enabled=False`：能构造、能验签、⛔ 不发消息。

    注入点（全部为了离线跑真实代码）：

    - `transport(method, url, body, headers, timeout) -> (status, bytes)`：默认 `urllib_transport`。
    - `credential_source(name) -> str|None`：默认读 `os.environ`。测试直接给字典，不碰真实环境。
    - `clock()`：默认 `time.time`。
    """

    name = ADAPTER

    def __init__(self, config: FeishuConfig, *, transport=None, credential_source=None,
                 clock=time.time, ledger=None):
        if not isinstance(config, FeishuConfig):
            raise FeishuConfigError('FeishuChannel 需要一个 FeishuConfig')
        self.config = config
        self.transport = transport or urllib_transport
        self.credential_source = credential_source or (lambda name: os.environ.get(name))
        self.clock = clock
        self._ledger_error = ''
        if isinstance(ledger, _Ledger):
            self._ledger = ledger
        elif config.ledger_path:
            try:
                self._ledger = _Ledger(config.ledger_path)
            except Exception as exc:
                # 台账打不开 ⇒ 不满足「request_key↔message_id 持久绑定」⇒ fail-closed，
                # 但不在构造时炸掉宿主：health 会说明原因，发送一律 NotSent。
                self._ledger = None
                self._ledger_error = f'台账打不开（{type(exc).__name__}）：{exc}'
        else:
            self._ledger = None
        self._token = None          # (token, expires_at) —— 只在内存里
        self._subscription = None

    # ── 构造 ──
    @classmethod
    def from_mapping(cls, mapping: dict, **kwargs) -> 'FeishuChannel':
        return cls(from_mapping(mapping), **kwargs)

    # ── 自述 ──
    def describe(self) -> dict:
        """核心把它算成发送方指纹。⚠️ 只放引用与配置形状，⛔ 不放凭据值。"""
        config = self.config
        return {
            'adapter': ADAPTER,
            'identity': 'feishu-bot:' + (config.app_id_ref or '<unset>'),
            'config_ref': config.config_ref,
            'conditions': {
                'mention_required': bool(config.mention_required),
                'receive_id_type': config.receive_id_type,
                'recipient_allowlist_size': len(config.allowed_recipients),
                'sender_allowlist_size': len(config.allowed_sender_open_ids),
                'chat_bindings_size': len(config.chat_bindings),
                'allowlist_fingerprint': config.allowlist_fingerprint,
                'card_wire': 'short_code+card_options（显式；缺则 fail-closed）',
                'card_click': 'card.action.trigger',
                'reply_quote': 'POST /im/v1/messages/:message_id/reply',
                'encrypted_webhook': ('aes-256-cbc:' + ('available' if channel_crypto.backend_available()
                                                        else 'missing-backend')),
                'send_enabled': bool(config.send_enabled),
            },
            'version': config.version,
        }

    def health(self) -> dict:
        """可用性判词。⛔ 不发网络请求；`ok` 不为 True 时核心不发。

        判定口径：**发得出去 + 收得回来**才算可用。
        没有 Encrypt Key 就验不了入向 ⇒ 发出去的审批没人能可信作答 ⇒ 一样算不可用
        （`approval.md` §2：通道不可用 ⇒ 不发卡，fail-closed）。
        """
        config = self.config
        reasons = []
        if not config.send_enabled:
            reasons.append('send_enabled=False：默认禁发，部署未显式打开')
        if not config.allowed_recipients:
            reasons.append('allowed_recipients 为空：没有允许的收件人 ⇒ 不发')
        if not config.allowed_sender_open_ids:
            reasons.append('allowed_sender_open_ids 为空：谁的入向都不认证 ⇒ 审批不可作答')
        if not config.ledger_path:
            reasons.append('没有 ledger_path：无法持久 request_key↔message_id ⇒ 不满足不重发要求')
        elif self._ledger is None:
            reasons.append('台账打不开：' + (self._ledger_error or 'ledger_path 指向的库不可用'))
        if not config.encrypt_key_ref:
            reasons.append('没有 encrypt_key_ref：webhook 无法验签 ⇒ 入向 fail-closed')
        if not config.verification_token_ref:
            reasons.append('没有 verification_token_ref：challenge/token 无法校验')
        if config.send_enabled:
            for ref, label in ((config.app_id_ref, 'app_id'), (config.app_secret_ref, 'app_secret')):
                if not ref:
                    reasons.append(f'没有 {label}_ref：发送凭据未配置')
                    continue
                value, why = self._resolve(ref)
                if why:
                    reasons.append(f'{label} 凭据引用取不到值：{why}')
        detail = {'adapter': ADAPTER, 'send_enabled': bool(config.send_enabled),
                  'receive_id_type': config.receive_id_type,
                  'mention_required': bool(config.mention_required),
                  'recipient_allowlist_size': len(config.allowed_recipients),
                  'sender_allowlist_size': len(config.allowed_sender_open_ids),
                  'chat_bindings_size': len(config.chat_bindings),
                  'credential_refs': list(config.refs),
                  'webhook': {'signature': 'sha256(timestamp+nonce+encrypt_key+body)',
                              'encrypt_key_ref': config.encrypt_key_ref or '',
                              'verification_token_ref': config.verification_token_ref or '',
                              'max_skew_seconds': config.max_skew_seconds,
                              'replay_guard': 'seen 表按 timestamp|nonce|signature 去重'},
                  'decryption': channel_crypto.backend_detail(),
                  'reasons': reasons}
        return {'ok': not reasons, 'detail': detail, 'adapter': ADAPTER,
                'mention_required': bool(config.mention_required)}

    # ── 凭据（只走引用）──
    def _resolve(self, ref: str):
        """把 `env:NAME` 解析成值。返回 `(value|None, why|None)`；⛔ 值不进任何文本。"""
        if not ref:
            return None, '没有配置引用'
        if not ref.startswith(REF_PREFIX) or len(ref) <= len(REF_PREFIX):
            return None, f'引用形态不对：{ref!r}（必须是 env:NAME）'
        name = ref[len(REF_PREFIX):]
        try:
            value = self.credential_source(name)
        except Exception as exc:  # 凭据源自己抛，只报类型
            return None, f'读环境变量 {name} 失败：{type(exc).__name__}'
        if not isinstance(value, str) or not value.strip():
            return None, f'环境变量 {name} 没有值'
        return value.strip(), None

    def _require_ledger(self) -> _Ledger:
        if self._ledger is None:
            raise NotSent('没有可用的本地台账（ledger_path 未配置）⇒ 不发送')
        return self._ledger

    # ── token ──
    def _tenant_token(self) -> str:
        """取 tenant_access_token。任何失败都是 `NotSent`：此时**一条消息都没发**，重试安全。"""
        cached = self._token
        now = float(self.clock())
        if cached and now < cached[1]:
            return cached[0]
        app_id, why = self._resolve(self.config.app_id_ref)
        if why:
            raise NotSent(f'取 tenant_access_token 失败（app_id）：{why}')
        secret, why = self._resolve(self.config.app_secret_ref)
        if why:
            raise NotSent(f'取 tenant_access_token 失败（app_secret）：{why}')
        url = self.config.api_base + TOKEN_PATH
        body = _json_body({'app_id': app_id, 'app_secret': secret})
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        try:
            status, raw = self.transport('POST', url, body, headers, float(self.config.timeout))
        except Exception as exc:
            # token 没拿到 ⇒ 后面什么都没发 ⇒ 确定未送达（可重试）
            raise NotSent(f'取 tenant_access_token 时连接失败：{type(exc).__name__}') from exc
        data = _parse_json(raw)
        if int(status) >= 400 or not isinstance(data, dict) or data.get('code') != 0:
            code = data.get('code') if isinstance(data, dict) else None
            raise NotSent(f'取 tenant_access_token 被拒（http={status}, code={code}）'
                          '：未发送任何消息')
        token = _text(data.get('tenant_access_token'))
        if not token:
            raise NotSent('取 tenant_access_token 返回空 token：未发送任何消息')
        try:
            expire = float(data.get('expire') or 0)
        except (TypeError, ValueError):
            expire = 0.0
        # 提前 60 秒过期，避免踩着边界用废 token
        self._token = (token, now + max(60.0, expire - 60.0))
        return token

    def _auth_headers(self, token: str) -> dict:
        return {'Authorization': f'Bearer {token}'}

    # ══════════════════════════ 发送 ══════════════════════════

    def send_file(self, payload: dict, artifact: bytes) -> dict:
        """审批页：上传 HTML 当附件，再发一条 `file` 消息。回执带页 path/bytes/sha256。"""
        rk, exp, recipient, ph, ledger = self._precheck(payload, 'approval_page')
        if not isinstance(artifact, (bytes, bytearray)) or not artifact:
            raise NotSent('审批页附件是空的 ⇒ 不发（空附件不算送达）')
        artifact = bytes(artifact)
        if len(artifact) > MAX_UPLOAD_BYTES:
            raise NotSent(f'审批页超过上传上限 {MAX_UPLOAD_BYTES} 字节 ⇒ 发不出去')
        decision_id = _text(payload.get('decision_id'))
        page_path = _text(payload.get('artifact'))
        name = f'approval_{decision_id}.html' if decision_id else ''
        if not name:
            raise NotSent('审批页载荷缺 decision_id ⇒ 无法给出可核的回执 path')
        file_name = Path(page_path).name if page_path else name
        if not file_name.lower().endswith('.html'):
            file_name = name
        digest = _sha256_hex(artifact)

        def do_send():
            token = self._tenant_token()
            file_key = self._upload(token, file_name, artifact)
            content = {'file_key': file_key}
            message = self._post_message(token, recipient=recipient, msg_type='file',
                                         content=content, uuid_seed=rk,
                                         chat_hint=recipient)
            message['content'] = content
            return message

        return self._execute(
            ledger, rk=rk, kind='approval_page', exp=exp, recipient=recipient,
            payload_hash=ph, decision_id=decision_id, short_code='', options=[],
            path=page_path, size=len(artifact), digest=digest, msg_type='file',
            content_obj={'file_key': '<uploaded>', 'artifact_sha256': digest,
                         'file_name': file_name},
            send=do_send)

    def send_card(self, payload: dict, artifact=None) -> dict:
        """交互选择卡。⛔ 拒绝从正文解析短码或待批选项：缺显式字段就 fail-closed。

        载荷必须带 `short_code` / `card_options` / `card_action_template` 三项（见模块 docstring）。
        回报的 `payload_hash` 仍是核心交付那一份（卡片 wire 字段不参与 hash）。
        """
        rk, exp, recipient, ph, ledger = self._precheck(payload, 'card')
        decision_id = _text(payload.get('decision_id'))
        missing = [key for key in CARD_WIRE_KEYS if not payload.get(key)]
        if missing:
            raise NotSent(
                '选择卡载荷缺显式 wire 字段：' + '、'.join('payload.' + k for k in missing)
                + ' ⇒ fail-closed 不发卡（⛔ 适配层不解析正文、不派生短码）')
        short_code = _require_text(payload.get('short_code'), 'payload.short_code').upper()
        pairs = card_option_pairs(payload.get('card_options'))
        template = payload.get('card_action_template')
        if not isinstance(template, dict):
            raise NotSent('payload.card_action_template 必须是对象 ⇒ fail-closed 不发卡')
        option_ids = [option_id for option_id, _ in pairs]
        card = build_decision_card({**payload, 'exp_id': exp, 'recipient': recipient,
                                    'decision_id': decision_id, 'body': payload.get('body')},
                                   short_code=short_code, options=pairs,
                                   action_template=template)
        card_json = json.dumps(card, ensure_ascii=False, sort_keys=True)
        if len(card_json.encode('utf-8')) > MAX_CARD_BYTES:
            raise NotSent(f'卡片 JSON 超过 {MAX_CARD_BYTES} 字节上限 ⇒ 发不出去')

        def do_send():
            token = self._tenant_token()
            message = self._post_message(token, recipient=recipient, msg_type='interactive',
                                         content=card, uuid_seed=rk, chat_hint=recipient)
            message['content'] = card
            return message

        return self._execute(
            ledger, rk=rk, kind='card', exp=exp, recipient=recipient, payload_hash=ph,
            decision_id=decision_id, short_code=short_code, options=option_ids,
            path='', size=0, digest='', msg_type='interactive', content_obj=card,
            send=do_send)

    def send_text(self, payload: dict, artifact=None) -> dict:
        """文字回复：走 reply 接口**引用入向那条消息**。

        ⛔ `:message_id` 只从本地「已验入向事件 → message_id」映射取；
        没有映射就不发（不发不带引用的文字，免得把答复挂到错的地方）。
        """
        rk, exp, recipient, ph, ledger = self._precheck(payload, 'reply')
        body = _require_text(payload.get('body'), 'payload.body')
        binding = payload.get('event_reply_to')
        if not isinstance(binding, dict):
            raise NotSent('文字回复必须绑定入向事件（载荷缺 event_reply_to）⇒ 不发')
        binding_source = _text(binding.get('source')) or SOURCE
        binding_event = _text(binding.get('event_id'))
        inbound = ledger.inbound_by_event(binding_source, binding_event) if binding_event else None
        if inbound is None:
            raise NotSent('本地台账没有这条已验入向事件 ⇒ 无法引用原消息，拒绝发送')
        inbound_message = _text(inbound.get('message_id'))
        if not inbound_message:
            raise NotSent('已验入向事件没有 message_id 映射 ⇒ 无法引用原消息，拒绝发送')
        if _text(inbound.get('exp_id')) != exp:
            raise NotSent('入向事件绑定的实验号与回复载荷不一致 ⇒ 不发（会把别轮答复发到本轮）')
        content = {'text': body}

        def do_send():
            token = self._tenant_token()
            url = f'{self.config.api_base}{MESSAGES_PATH}/' \
                  f'{urllib.parse.quote(inbound_message, safe="")}/reply'
            # 回复接口的 content 同样是「JSON 结构序列化后的字符串」，不是对象。
            message = self._post(token, url,
                                 {'content': json.dumps(content, ensure_ascii=False,
                                                        sort_keys=True),
                                  'msg_type': 'text', 'uuid': _uuid_for(rk)},
                                 chat_hint=recipient, msg_type='text')
            message['content'] = content
            return message

        return self._execute(
            ledger, rk=rk, kind='reply', exp=exp, recipient=recipient, payload_hash=ph,
            decision_id=_text(payload.get('decision_id')), short_code='', options=[],
            path='', size=0, digest='', msg_type='text', content_obj=content, send=do_send)

    # ── 发送公共骨架 ──
    def _precheck(self, payload: dict, kind: str):
        """发送前**本地**判据。这里失败一定没发出去 ⇒ `NotSent`。"""
        if not isinstance(payload, dict):
            raise FeishuError('发送载荷必须是对象')
        rk = _require_text(payload.get('request_key'), 'payload.request_key')
        exp = _require_text(payload.get('exp_id'), 'payload.exp_id')
        recipient = _require_text(payload.get('recipient'), 'payload.recipient')
        ph = _require_text(payload.get('payload_hash'), 'payload.payload_hash')
        declared = payload.get('kind')
        if declared != kind:
            raise FeishuError(f'载荷 kind={declared!r} 与本次交付 {kind!r} 不一致')
        if not EXP_ID_RE.fullmatch(exp):
            raise FeishuError(f'实验号形态不合法：{exp!r}')
        if not self.config.send_enabled:
            raise NotSent('send_enabled=False：本部署默认禁发（需部署显式打开）')
        if recipient not in self.config.allowed_recipients:
            raise NotSent(f'收件人不在 allowed_recipients 允许列表：{recipient!r} ⇒ 不发')
        return rk, exp, recipient, ph, self._require_ledger()

    def _execute(self, ledger, *, rk, kind, exp, recipient, payload_hash, decision_id,
                 short_code, options, path, size, digest, msg_type, content_obj, send):
        """台账状态机 + 发送 + 回执。三类失败严格分开（见模块 docstring）。"""
        now = float(self.clock())
        state, row = ledger.begin({'request_key': rk, 'kind': kind, 'exp_id': exp,
                                   'recipient': recipient, 'payload_hash': payload_hash,
                                   'decision_id': decision_id, 'short_code': short_code,
                                   'options_json': canonical_json(list(options)),
                                   'path': path, 'bytes': size, 'sha256': digest}, now=now)
        if state == 'confirmed':
            # 同一 request_key 已经送过：回原回执，⛔ 不重发（这会重新走一遍核心的回执复核）。
            return _receipt_from_row(row)
        if state == 'ambiguous':
            raise AmbiguousSend(
                '同一 request_key 上一次结果未知（超时/中断）⇒ ⛔ 不重发；'
                '只能 query() 只读核对或人工处理')
        try:
            message = send()
        except NotSent as exc:
            ledger.fail(request_key=rk, state=ST_NOT_SENT, error=str(exc), now=float(self.clock()))
            raise
        except AmbiguousSend as exc:
            ledger.fail(request_key=rk, state=ST_UNKNOWN, error=str(exc), now=float(self.clock()))
            raise
        except Exception as exc:
            ledger.fail(request_key=rk, state=ST_UNKNOWN,
                        error=f'{type(exc).__name__}: {exc}', now=float(self.clock()))
            raise AmbiguousSend(
                f'发送结果未知（{type(exc).__name__}）⇒ ⛔ 不重发，等人工核对') from exc
        content_json = canonical_json(message.get('content', content_obj))
        content_sha256 = _sha256_hex(content_json.encode('utf-8'))
        receipt = {
            'message_id': message['message_id'],
            'request_key': rk,
            'exp_id': exp,
            'recipient': recipient,
            'payload_hash': payload_hash,
            'kind': kind,
            'decision_id': decision_id,
            'ok': True,
            'returncode': 0,
            'path': path,
            'bytes': int(size),
            'sha256': digest,
            'short_code': short_code,
            'adapter': ADAPTER,
            'identity': self.describe()['identity'],
            'config_ref': self.config.config_ref,
            'conditions': {
                'mention_required': bool(self.config.mention_required),
                'msg_type': msg_type,
                'chat_id': message.get('chat_id') or recipient,
                'reply_quoted': bool(message.get('quoted')),
            },
            'sent_at': float(self.clock()),
        }
        ledger.confirm(request_key=rk, message_id=receipt['message_id'],
                       chat_id=message.get('chat_id') or recipient, msg_type=msg_type,
                       content_json=content_json, content_sha256=content_sha256,
                       receipt=receipt, now=float(self.clock()))
        return receipt

    def _upload(self, token: str, file_name: str, data: bytes) -> str:
        """上传文件拿 `file_key`。⛔ 这里失败时**一条消息都没发** ⇒ `NotSent`（重试安全）。"""
        url = self.config.api_base + FILES_PATH
        body, content_type = _multipart({'file_type': 'stream', 'file_name': file_name},
                                        file_name=file_name, file_bytes=data,
                                        content_type='text/html')
        headers = dict(self._auth_headers(token), **{'Content-Type': content_type})
        try:
            status, raw = self.transport('POST', url, body, headers, float(self.config.timeout))
        except Exception as exc:
            raise NotSent(f'上传附件失败（{type(exc).__name__}）：消息未发送，可重试') from exc
        payload = _parse_json(raw)
        code = payload.get('code') if isinstance(payload, dict) else None
        if int(status) >= 400 or not isinstance(payload, dict) or code != 0:
            raise NotSent(f'上传附件被拒（http={status}, code={code}）：消息未发送')
        file_key = _text((payload.get('data') or {}).get('file_key'))
        if not file_key:
            raise NotSent('上传附件没有返回 file_key：消息未发送')
        return file_key

    def _post_message(self, token: str, *, recipient: str, msg_type: str, content: dict,
                      uuid_seed: str, chat_hint: str):
        url = (f'{self.config.api_base}{MESSAGES_PATH}'
               f'?receive_id_type={urllib.parse.quote(self.config.receive_id_type)}')
        return self._post(token, url, {'receive_id': recipient, 'msg_type': msg_type,
                                       'content': json.dumps(content, ensure_ascii=False,
                                                             sort_keys=True),
                                       'uuid': _uuid_for(uuid_seed)},
                          chat_hint=chat_hint, msg_type=msg_type)

    def _post(self, token: str, url: str, body: dict, *, chat_hint: str, msg_type: str):
        """发一条消息并分类失败：明确拒绝 ⇒ `NotSent`；不确定 ⇒ `AmbiguousSend`。"""
        headers = dict(self._auth_headers(token),
                       **{'Content-Type': 'application/json; charset=utf-8'})
        try:
            status, raw = self.transport('POST', url, _json_body(body), headers,
                                         float(self.config.timeout))
        except (TimeoutError, socket.timeout) as exc:
            raise AmbiguousSend(f'发送超时（{type(exc).__name__}）⇒ 结果未知') from exc
        except urllib.error.URLError as exc:
            raise _classify_urlerror(exc) from exc
        except OSError as exc:
            raise _classify_urlerror(exc) from exc
        status = int(status)
        if status == 429 or status >= 500:
            raise AmbiguousSend(f'平台返回 http={status}：可能已发出，⛔ 不重发')
        payload = _parse_json(raw)
        code = payload.get('code') if isinstance(payload, dict) else None
        if status >= 400 or not isinstance(payload, dict) or code != 0:
            raise NotSent(f'平台明确拒绝（http={status}, code={code}）：未送达')
        data = payload.get('data') or {}
        message_id = _text(data.get('message_id'))
        if not message_id:
            raise AmbiguousSend('平台返回成功但缺 message_id：结果不可核，⛔ 不重发')
        return {'message_id': message_id,
                'chat_id': _text(data.get('chat_id')) or chat_hint,
                'msg_type': _text(data.get('msg_type')) or msg_type,
                'quoted': '/reply' in url}

    # ══════════════════════════ 查询 / 核验 ══════════════════════════

    def query(self, request_key: str):
        """按 wire key 查原消息。**只读已确认记录**；未知/未发一律 `None`。"""
        if not isinstance(request_key, str) or not request_key.strip():
            return None
        if self._ledger is None:
            return None
        row = self._ledger.by_request_key(request_key.strip())
        if row is None or row.get('state') != ST_CONFIRMED:
            return None
        return _receipt_from_row(row)

    def verify_receipt(self, receipt) -> bool:
        """核回执。⛔ 不信 caller 给的 message_id：本地台账 + `GET /im/v1/messages/:id` 双查。

        判据（任一不成立就是 False，核心随后会把交付落 `unknown` 并隔离）：

        1. 台账里确有这条 `message_id`，状态是 `confirmed`；
        2. 台账记录的 `request_key / payload_hash / exp_id / recipient / kind / decision_id`
           与回执逐项相等；页还核 `path / bytes / sha256`，卡核 `short_code`；
        3. `GET` 回来的消息：`code=0`、`message_id` 相同、未被撤回、
           `chat_id` 与台账一致、`sender_type=app`、`msg_type` 与台账一致、
           **内容规范化后 sha256 与台账里发出去的那份一致**。
        """
        if receipt is None:
            return False
        message_id = getattr(receipt, 'message_id', None)
        if not isinstance(message_id, str) or not message_id.strip():
            return False
        ledger = self._ledger
        if ledger is None:
            return False
        row = ledger.by_message_id(message_id.strip())
        if row is None or row.get('state') != ST_CONFIRMED:
            return False
        for name in ('request_key', 'payload_hash', 'exp_id', 'recipient', 'kind'):
            if getattr(receipt, name, None) != row.get(name):
                return False
        if (getattr(receipt, 'decision_id', '') or '') != (row.get('decision_id') or ''):
            return False
        if row['kind'] == 'approval_page':
            if (getattr(receipt, 'sha256', '') or '') != (row.get('sha256') or ''):
                return False
            if int(getattr(receipt, 'bytes', 0) or 0) != int(row.get('bytes') or 0):
                return False
            if str(getattr(receipt, 'path', '') or '') != (row.get('path') or ''):
                return False
        short_code = getattr(receipt, 'short_code', '') or ''
        if short_code and short_code != (row.get('short_code') or ''):
            return False
        return self._verify_with_get(row)

    def _verify_with_get(self, row: dict) -> bool:
        """真的去平台读一遍这条消息。任何异常/不一致都返回 False（fail-closed）。"""
        try:
            token = self._tenant_token()
        except Exception:
            return False
        url = f"{self.config.api_base}{MESSAGES_PATH}/" \
              f"{urllib.parse.quote(str(row['message_id']), safe='')}"
        if (row.get('msg_type') or '') == 'interactive':
            # 只有带上这个参数才会返回「发送时的原始卡片 JSON」，否则返回的是渲染结构。
            url += '?card_msg_content_type=user_card_content'
        try:
            status, raw = self.transport('GET', url, None, self._auth_headers(token),
                                         float(self.config.timeout))
        except Exception:
            return False
        if int(status) != 200:
            return False
        payload = _parse_json(raw)
        if not isinstance(payload, dict) or payload.get('code') != 0:
            return False
        items = (payload.get('data') or {}).get('items') or []
        if not isinstance(items, list) or not items:
            return False
        item = items[0]
        if not isinstance(item, dict):
            return False
        if _text(item.get('message_id')) != str(row['message_id']):
            return False
        if item.get('deleted') is True:
            return False
        chat_id = _text(item.get('chat_id'))
        if chat_id and chat_id not in (row.get('chat_id'), row.get('recipient')):
            return False
        if _text(item.get('msg_type')) and _text(item.get('msg_type')) != (row.get('msg_type') or ''):
            return False
        sender = item.get('sender') or {}
        if _text(sender.get('sender_type')) and _text(sender.get('sender_type')) != 'app':
            return False
        content = _text((item.get('body') or {}).get('content'))
        if not content:
            return False
        try:
            got = json.loads(content)
        except ValueError:
            return False
        return _canonical_sha256(got) == (row.get('content_sha256') or '')

    # ══════════════════════════ 入向：验签 + 规范化 ══════════════════════════

    def handle_webhook(self, headers, raw_body) -> dict:
        """处理一条飞书 webhook 请求。**先验签，再解析**。

        返回结构（父 agent 的 HTTP 层直接照着发响应）：

        ```python
        {'ok': bool, 'kind': 'event'|'url_verification'|'ignored'|'rejected',
         'status': int, 'reason': str, 'detail': str,
         'verification': {...}, 'event': dict|None,
         'response': dict, 'duplicate': bool}
        ```

        拒绝理由（`reason`）一览：
        `signature_unconfigured` / `signature_missing` / `signature_bad_timestamp` /
        `timestamp_skew`（>5 分钟时差）/ `signature_mismatch` / `replay`（同一签名二次）/
        `malformed_body` / `decryption_unavailable`（没装可选的 `cryptography` ⇒ 501）/
        `decryption_failed`（密文/填充/UTF-8/JSON 不合法 ⇒ 400）/
        `token_unconfigured` / `token_mismatch` / `sender_not_allowed` / `unbound_chat` /
        `bot_echo` / `card_not_sent_by_adapter` / `event_conflict` …
        """
        verification = {'signature': 'not_checked', 'token': 'not_checked',
                        'encrypted': False, 'decrypted': False, 'skew_seconds': None,
                        'event_type': '', 'event_id': '', 'source': SOURCE}
        if not isinstance(raw_body, (bytes, bytearray)):
            raise FeishuError('raw_body 必须是原始字节（验签要对原始字节算 SHA256）')
        raw = bytes(raw_body)
        if len(raw) > MAX_WEBHOOK_BYTES:
            return _reject('body_too_large', verification, status=413)

        lower = _lower_headers(headers)
        encrypt_key, why = self._resolve(self.config.encrypt_key_ref)
        if why:
            # 没配 Encrypt Key ⇒ 无法验签 ⇒ fail-closed（不猜、不放行）
            return _reject('signature_unconfigured', verification, status=401, detail=why)
        timestamp = lower.get('x-lark-request-timestamp', '')
        nonce = lower.get('x-lark-request-nonce', '')
        signature = lower.get('x-lark-signature', '').lower()
        if not (timestamp and nonce and signature):
            return _reject('signature_missing', verification, status=401,
                           detail='缺 X-Lark-Request-Timestamp / Nonce / Signature')
        try:
            stamp = float(timestamp)
        except ValueError:
            return _reject('signature_bad_timestamp', verification, status=401)
        if not math.isfinite(stamp) or stamp <= 0:
            # NaN / inf / 0 / 负数：`abs(clock - nan) > limit` 恒为 False，不先挡住就能绕过时差闸。
            return _reject('signature_bad_timestamp', verification, status=401,
                           detail='timestamp 必须是有限正数（NaN / Infinity / 0 一律拒）')
        skew = abs(float(self.clock()) - stamp)
        verification['skew_seconds'] = round(skew, 3)
        if not math.isfinite(skew) or skew > float(self.config.max_skew_seconds):
            return _reject('timestamp_skew', verification, status=401,
                           detail=f'与本地时钟相差 {verification["skew_seconds"]}s '
                                  f'> {self.config.max_skew_seconds}s')
        expected = compute_signature(timestamp, nonce, encrypt_key, raw)
        if not hmac.compare_digest(expected, signature):
            return _reject('signature_mismatch', verification, status=401)
        verification['signature'] = 'ok'
        ledger = self._ledger
        if ledger is None:
            return _reject('ledger_unavailable', verification, status=500,
                           detail='没有本地台账，无法做重放保护与已认证事件存档')
        seen_key = _sha256_hex(f'{timestamp}|{nonce}|{signature}'.encode('utf-8'))
        if not ledger.mark_seen(seen_key, now=float(self.clock())):
            return _reject('replay', verification, status=409,
                           detail='同一 timestamp|nonce|signature 已处理过')

        # ── 验签通过之后才解析 ──
        try:
            body = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            return _reject('malformed_body', verification, status=400)
        if not isinstance(body, dict):
            return _reject('malformed_body', verification, status=400)
        if 'encrypt' in body:
            # ⚠️ 只有验签 + 重放去重都过了才会走到这里；解密失败一律 fail-closed，⛔ 不降级成明文。
            verification['encrypted'] = True
            try:
                body = channel_crypto.decrypt_event_payload(encrypt_key, body.get('encrypt'))
            except channel_crypto.DecryptUnavailable as exc:
                return _reject('decryption_unavailable', verification, status=501, detail=str(exc))
            except channel_crypto.DecryptError as exc:
                return _reject('decryption_failed', verification, status=400, detail=str(exc))
            verification['decrypted'] = True

        token, why = self._resolve(self.config.verification_token_ref)
        if why:
            return _reject('token_unconfigured', verification, status=401, detail=why)

        if _text(body.get('type')) == 'url_verification':
            challenge = body.get('challenge')
            if not isinstance(challenge, str) or not challenge:
                return _reject('malformed_body', verification, status=400,
                               detail='url_verification 缺 challenge')
            if not _constant_eq(body.get('token'), token):
                return _reject('token_mismatch', verification, status=401)
            verification['token'] = 'ok'
            return {'ok': True, 'kind': 'url_verification', 'status': 200, 'reason': '',
                    'detail': '', 'verification': verification, 'event': None,
                    'response': {'challenge': challenge}, 'duplicate': False}

        header = body.get('header') or {}
        if not isinstance(header, dict):
            return _reject('malformed_body', verification, status=400)
        verification['event_type'] = _text(header.get('event_type'))
        verification['event_id'] = _text(header.get('event_id'))
        if not _constant_eq(header.get('token'), token):
            return _reject('token_mismatch', verification, status=401)
        verification['token'] = 'ok'
        event_type = verification['event_type']
        try:
            if event_type == 'im.message.receive_v1':
                outcome = self._normalize_message(header, body.get('event') or {}, raw)
            elif event_type in ('card.action.trigger', 'card.action.trigger_v1'):
                outcome = self._normalize_card(header, body.get('event') or {}, raw)
            else:
                return {'ok': True, 'kind': 'ignored', 'status': 200, 'reason': '',
                        'detail': f'未订阅的事件类型 {event_type!r}', 'verification': verification,
                        'event': None, 'response': {}, 'duplicate': False}
        except _Ignore as exc:
            return {'ok': True, 'kind': 'ignored', 'status': 200, 'reason': exc.reason,
                    'detail': '', 'verification': verification, 'event': None,
                    'response': {}, 'duplicate': False}
        except _Reject as exc:
            return _reject(exc.reason, verification, status=exc.status, detail=exc.detail)
        try:
            row, created = ledger.put_inbound(outcome['record'], now=float(self.clock()))
        except FeishuError as exc:
            # 同 event_id 换了内容 ⇒ 不覆盖、不静默
            return _reject('event_conflict', verification, status=409, detail=str(exc))
        except Exception as exc:
            return _reject('ledger_error', verification, status=500,
                           detail=f'写已认证事件失败：{type(exc).__name__}')
        return {'ok': True, 'kind': 'event', 'status': 200, 'reason': '',
                'detail': '' if created else 'duplicate event_id（已落过，按幂等返回）',
                'verification': verification,
                'event': json.loads(row['normalized_json']),
                'response': outcome['response'], 'duplicate': not created}

    def authenticate(self, source: str, event: dict) -> str:
        """核入向事件来源，返回 principal。

        ⛔ 只认**本适配层持久过的已认证事件**：caller 自己拼一个 event 进来（哪怕字段全对）
        也拿不到认证 —— 否则没有密钥的一方可以伪造审批。
        """
        if source != SOURCE:
            raise NotAuthenticated(f'未知来源：{source!r}（本适配层只认证 {SOURCE!r}）')
        if not isinstance(event, dict):
            raise NotAuthenticated('入向事件必须是对象')
        event_id = _text(event.get('event_id'))
        if not event_id:
            raise NotAuthenticated('入向事件缺 event_id')
        ledger = self._ledger
        if ledger is None:
            raise NotAuthenticated('没有本地台账：无法证明这条入向事件被验签过')
        row = ledger.inbound_by_event(SOURCE, event_id)
        if row is None:
            raise NotAuthenticated(
                f'事件 {event_id!r} 不在本地已认证台账 ⇒ 拒绝（伪 caller event）')
        if canonical_json(json.loads(row['normalized_json'])) != canonical_json(event):
            raise NotAuthenticated(f'事件 {event_id!r} 与本地台账不一致 ⇒ 拒绝')
        principal = _text(row.get('principal'))
        if not principal:
            raise NotAuthenticated(f'事件 {event_id!r} 没有 principal ⇒ 拒绝')
        if principal not in self.config.allowed_sender_open_ids:
            raise NotAuthenticated(f'发送人 {principal!r} 不在 allowed_sender_open_ids')
        return principal

    # ── 规范化：文字消息 ──
    def _normalize_message(self, header, event, raw: bytes) -> dict:
        ledger = self._require_ledger_or_reject()
        sender = event.get('sender') or {}
        message = event.get('message') or {}
        if not isinstance(sender, dict) or not isinstance(message, dict):
            raise _Reject('malformed_message')
        sender_id = sender.get('sender_id') or {}
        principal = _text((sender_id or {}).get('open_id'))
        sender_type = _text(sender.get('sender_type'))
        if sender_type == 'bot':
            raise _Ignore('bot_echo')
        if sender_type != 'user':
            raise _Reject('sender_type_unknown', detail=f'sender_type={sender_type!r}')
        if not principal:
            raise _Reject('no_principal', detail='sender.sender_id.open_id 为空（无法核身份）')
        if principal not in self.config.allowed_sender_open_ids:
            raise _Reject('sender_not_allowed', status=403,
                          detail=f'{principal!r} 不在 allowed_sender_open_ids')
        message_id = _text(message.get('message_id'))
        chat_id = _text(message.get('chat_id'))
        if not message_id or not chat_id:
            raise _Reject('malformed_message', detail='缺 message_id 或 chat_id')
        message_type = _text(message.get('message_type')) or 'unknown'
        body = ''
        if message_type == 'text':
            try:
                content = json.loads(_text(message.get('content')) or '{}')
            except ValueError:
                content = {}
            body = _text((content or {}).get('text')) if isinstance(content, dict) else ''
        mentions = message.get('mentions') or []
        mentioned_bot = any(isinstance(item, dict) and _text(item.get('mentioned_type')) == 'bot'
                            for item in mentions)
        chat_type = _text(message.get('chat_type'))
        bound = self._bind_text(ledger, message=message, chat_id=chat_id,
                               principal=principal, body=body)
        if bound is None:
            raise _Reject(
                'unbound_chat', status=403,
                detail='这条入向没有受控的实验绑定：既不在 chat_bindings，也不是回复我们发过的'
                       '消息，正文也没有指向本地已发决策的短码 ⇒ 不从用户自述里取实验号')
        event_time = _millis(header.get('create_time'), unit='ms')
        if event_time is None:
            event_time = _millis(message.get('create_time'), unit='ms')
        normalized = {
            'event_id': _text(header.get('event_id')) or message_id,
            'exp_id': bound['exp_id'],
            'create_time': event_time,
            'recipient': chat_id,
            'principal': principal,
            'intent': 'question' if body else 'other',
            'body': body,
            'message_id': message_id,
            'chat_id': chat_id,
            'chat_type': chat_type,
            'message_type': message_type,
            'mentioned_bot': mentioned_bot,
            'reply_to_message_id': _text(message.get('parent_id'))
                                   or _text(message.get('root_id')),
            'binding': bound,
            'conditions': {'mention_required': bool(self.config.mention_required),
                           'mention_required_satisfied': (chat_type != 'group') or mentioned_bot},
        }
        if bound.get('decision_id'):
            normalized['decision_id'] = bound['decision_id']
        if bound.get('short_code'):
            normalized['short_code'] = bound['short_code']
        return {'record': self._record(normalized, principal=principal, raw=raw,
                                       message_id=message_id, chat_id=chat_id,
                                       exp_id=bound['exp_id'],
                                       decision_id=bound.get('decision_id') or ''),
                'response': {}}

    # ── 规范化：卡片点击 ──
    def _normalize_card(self, header, event, raw: bytes) -> dict:
        ledger = self._require_ledger_or_reject()
        if not isinstance(event, dict):
            raise _Reject('malformed_card')
        operator = event.get('operator') or {}
        principal = _text((operator or {}).get('open_id'))
        if not principal:
            raise _Reject('no_principal', detail='event.operator.open_id 为空')
        if principal not in self.config.allowed_sender_open_ids:
            raise _Reject('sender_not_allowed', status=403)
        action = event.get('action') or {}
        value = (action or {}).get('value')
        if not isinstance(value, dict):
            raise _Reject('card_value_not_object',
                          detail='按钮回传 value 必须是对象（本适配层发卡时就是这么写的）')
        decision_id = _text(value.get('decision_id'))
        short_code = _text(value.get('short_code')).upper()
        intent = _text(value.get('intent')).lower()
        options = value.get('options') or []
        if not decision_id:
            raise _Reject('card_missing_decision')
        if intent not in CLICK_INTENTS:
            raise _Reject('card_bad_intent', detail=f'intent={intent!r}')
        if not isinstance(options, list) or any(not isinstance(item, str) or not item.strip()
                                                for item in options):
            raise _Reject('card_bad_options', detail='options 必须是非空字符串列表')
        options = [item.strip() for item in options]
        if intent not in ('reject', 'deny') and not options:
            raise _Reject('card_no_option', detail='批准类点击必须指名批准哪一条选项')
        # ⛔ 只用本地已发卡片台账反查，不采信 body 自述
        row = ledger.card_by_decision(decision_id)
        if row is None:
            raise _Reject('card_not_sent_by_adapter', status=403,
                          detail=f'决策 {decision_id!r} 的选择卡不在本适配层台账里')
        if (row.get('short_code') or '') != short_code:
            raise _Reject('card_short_code_mismatch', status=403)
        try:
            sent_options = json.loads(row.get('options_json') or '[]')
        except ValueError:
            sent_options = []
        if not isinstance(sent_options, list):
            sent_options = []
        unknown = [item for item in options if item not in sent_options]
        if unknown:
            raise _Reject('card_option_not_in_card', status=403,
                          detail='点了不在本卡待批范围的选项：' + '、'.join(unknown))
        exp_id = row['exp_id']
        recipient = row['recipient']
        context = event.get('context') or {}
        open_chat = _text((context or {}).get('open_chat_id'))
        if open_chat and open_chat != recipient:
            raise _Reject('card_chat_mismatch', status=403,
                          detail='点击所在会话与审批发出对象不符')
        open_message = _text((context or {}).get('open_message_id'))
        if open_message and row.get('message_id') and open_message != row['message_id']:
            raise _Reject('card_message_mismatch', status=403,
                          detail='被点击的卡片不是本适配层发出的那一条')
        event_time = _millis(header.get('create_time'), unit='unknown')
        normalized = {
            'event_id': _text(header.get('event_id')),
            'exp_id': exp_id,
            'create_time': event_time,
            'recipient': recipient,
            'principal': principal,
            'decision_id': decision_id,
            'short_code': short_code,
            'intent': intent,
            'options': options,
            'card_action': {'tag': _text(action.get('tag')),
                            'name': _text(action.get('name')),
                            'open_message_id': open_message,
                            'asked_at': _text(header.get('create_time')),
                            'value': value},
            'message_id': open_message or row.get('message_id') or '',
            'chat_id': recipient,
            'binding': {'how': 'confirmed_card', 'decision_id': decision_id,
                        'short_code': short_code},
        }
        if not normalized['event_id']:
            raise _Reject('malformed_card', detail='缺 header.event_id')
        return {'record': self._record(normalized, principal=principal, raw=raw,
                                       message_id=normalized['message_id'],
                                       chat_id=recipient, exp_id=exp_id,
                                       decision_id=decision_id),
                'response': {'toast': {'type': 'success', 'content': '已记录',
                                       'i18n': {'zh_cn': '已记录', 'en_us': 'Recorded'}}}}

    # ── 绑定 ──
    def _bind_text(self, ledger, *, message, chat_id, principal, body):
        """受控绑定：⛔ 不从用户正文里直接取实验号。

        三条来源，按可信度排：显式配置 > 回复我们发过的消息 > 正文指向本地已发决策的短码。
        """
        bindings = self.config.chat_bindings
        for key in (chat_id, principal):
            exp = bindings.get(key)
            if exp:
                return {'how': 'chat_binding', 'key': key, 'exp_id': exp,
                        'decision_id': '', 'short_code': ''}
        for candidate in (_text(message.get('parent_id')), _text(message.get('root_id'))):
            if not candidate:
                continue
            row = ledger.by_message_id(candidate)
            if row is not None:
                return {'how': 'reply_to_sent_message', 'message_id': candidate,
                        'exp_id': row['exp_id'], 'decision_id': row.get('decision_id') or '',
                        'short_code': row.get('short_code') or ''}
        for code in _short_codes_in(body):
            row = ledger.card_by_short_code(code)
            if row is not None:
                return {'how': 'short_code_of_sent_card', 'short_code': code,
                        'exp_id': row['exp_id'], 'decision_id': row.get('decision_id') or ''}
        return None

    def _require_ledger_or_reject(self) -> _Ledger:
        if self._ledger is None:
            raise _Reject('ledger_unavailable', status=500)
        return self._ledger

    @staticmethod
    def _record(normalized: dict, *, principal, raw: bytes, message_id, chat_id, exp_id,
                decision_id) -> dict:
        return {'source': SOURCE,
                'event_id': _require_text(normalized.get('event_id'), 'event_id'),
                'message_id': message_id, 'chat_id': chat_id,
                'principal': principal, 'exp_id': exp_id, 'decision_id': decision_id,
                'normalized_json': canonical_json(normalized),
                'raw_sha256': _sha256_hex(raw)}

    # ══════════════════════════ poll / subscribe ══════════════════════════

    def poll(self, cursor, limit: int = 50) -> dict:
        """拉模式：按 `seq` 顺序吐已认证事件。`cursor` 是上次最后一个 seq。"""
        if not isinstance(limit, int) or limit <= 0:
            raise FeishuError('limit 必须是正整数')
        ledger = self._require_ledger_or_reject()
        try:
            after = int(str(cursor)) if cursor not in (None, '') else 0
        except (TypeError, ValueError):
            raise FeishuError(f'cursor 必须是数字或 None，现在是 {cursor!r}')
        rows = ledger.inbound_after(after, limit)
        events = [json.loads(row['normalized_json']) for row in rows]
        next_cursor = str(rows[-1]['seq']) if rows else (str(after) if after else None)
        return {'cursor': next_cursor, 'events': events}

    def subscribe(self, callback: Callable[[dict], object]):
        """推模式句柄。真正的推送来自 `handle_webhook`（部署方的 HTTP 层负责调 `push`）。"""
        if not callable(callback):
            raise FeishuError('subscribe 需要一个 callable')
        handle = _Subscription(callback)
        self._subscription = handle
        return handle


class _Subscription:
    """`subscribe()` 返回的句柄。`push(event)` 调回调，`close()` 停。"""

    def __init__(self, callback):
        self._callback = callback
        self.closed = False
        self.delivered = 0

    def push(self, event: dict):
        if self.closed:
            raise FeishuError('订阅已关闭')
        self._callback(event)
        self.delivered += 1
        return event

    def close(self):
        self.closed = True
        self._callback = None


# ────────────────────────────── 内部：分类与控制流 ──────────────────────────────

class _Reject(Exception):
    """内部用：这条 webhook 请求不成（带原因与建议状态码）。"""

    def __init__(self, reason: str, *, status: int = 400, detail: str = ''):
        self.reason, self.status, self.detail = reason, status, detail
        super().__init__(f'{reason}: {detail}' if detail else reason)


class _Ignore(Exception):
    """内部用：正常但不需要处理的入向（机器人自己的消息、未订阅事件）。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _classify_urlerror(exc) -> Exception:
    """连接层异常分类：**根本没送到** ⇒ NotSent；可能送到了 ⇒ AmbiguousSend。"""
    reason = getattr(exc, 'reason', exc)
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror, FileNotFoundError,
                           NotADirectoryError)):
        return NotSent(f'连接未建立（{type(reason).__name__}）：消息未送达')
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return AmbiguousSend(f'连接超时（{type(reason).__name__}）⇒ 结果未知')
    if isinstance(exc, OSError) and isinstance(reason, OSError):
        return AmbiguousSend(f'连接中断（{type(reason).__name__}）⇒ 结果未知')
    return AmbiguousSend(f'传输异常（{type(exc).__name__}）⇒ 结果未知')


def _parse_json(raw):
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode('utf-8')
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _uuid_for(seed: str) -> str:
    """平台侧去重用的 uuid（≤50 字符）。同 request_key ⇒ 同 uuid，重试也不会出两条。"""
    return 'ar-' + _sha256_hex(str(seed).encode('utf-8'))[:40]


def _constant_eq(left, right) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left.strip().encode('utf-8'), right.strip().encode('utf-8'))


def _short_codes_in(text: str) -> list:
    """从正文里挑「像短码」的候选。⚠️ 是否可信由台账判定，不是这里。"""
    if not isinstance(text, str) or not text:
        return []
    found = []
    for pattern in (r'短码\s*[<＜]?\s*([A-Za-z0-9]{4,12})',
                    r'[\[【]\s*([A-Za-z0-9]{4,12})\s*[\]】]',
                    r'^\s*([A-Z0-9]{4,12})\s+[A-Z]\b'):
        for match in re.findall(pattern, text, flags=re.MULTILINE):
            code = str(match).strip().upper()
            if code and code not in found:
                found.append(code)
    return found


def _reject(reason: str, verification: dict, *, status: int = 400, detail: str = '') -> dict:
    return {'ok': False, 'kind': 'rejected', 'status': int(status), 'reason': reason,
            'detail': detail, 'verification': dict(verification), 'event': None,
            'response': {'ok': False, 'reason': reason},
            'duplicate': False}


def _receipt_from_row(row: dict) -> dict:
    """台账行 → 回执字典（**结构必须与首发的回执完全一致**）。"""
    stored = row.get('receipt_json')
    if stored:
        try:
            receipt = json.loads(stored)
            if isinstance(receipt, dict):
                return receipt
        except ValueError:
            pass
    return {'message_id': row.get('message_id') or '',
            'request_key': row['request_key'],
            'exp_id': row['exp_id'],
            'recipient': row['recipient'],
            'payload_hash': row['payload_hash'],
            'kind': row['kind'],
            'decision_id': row.get('decision_id') or '',
            'ok': True,
            'returncode': 0,
            'path': row.get('path') or '',
            'bytes': int(row.get('bytes') or 0),
            'sha256': row.get('sha256') or '',
            'short_code': row.get('short_code') or '',
            'adapter': ADAPTER,
            'identity': '',
            'config_ref': '',
            'conditions': {'mention_required': False},
            'sent_at': float(row.get('updated_at') or 0.0)}


# ────────────────────────────── HTTP webhook（只给 factory，不自己起服务）─────────────────────────────

def _http_response(result: dict):
    """`handle_webhook` 的结果 → `(status, json_body_dict)`。"""
    if not isinstance(result, dict):
        return 500, {'ok': False, 'reason': 'bad_result'}
    return int(result.get('status') or 500), dict(result.get('response') or {})


def make_http_handler(channel: FeishuChannel, *, path: str = WEBHOOK_PATH,
                      max_body: int = MAX_WEBHOOK_BYTES):
    """构造 `http.server` 的 handler 类。⛔ 本函数不监听端口、不起线程。

    父 agent 的 CLI 自己决定 `HTTPServer(addr, handler)` 与生命周期：

    ```python
    handler = make_http_handler(channel, path='/feishu/webhook')
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 8080), handler)
    ```

    行为：只收 `POST path`；`Content-Length` 必填且 ≤ `max_body`；
    把**原始字节**与小写化后的请求头交给 `handle_webhook`；非 200 时同样返回 JSON 原因
    （部署方可以据此选 4xx/5xx，飞书会重推非 200）。
    """

    if not isinstance(channel, FeishuChannel):
        raise FeishuError('make_http_handler 需要一个 FeishuChannel')

    class FeishuWebhookHandler(BaseHTTPRequestHandler):
        server_version = 'AutoresearchFeishuWebhook/1'
        sys_version = ''

        def _respond(self, status: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
            self.send_response(int(status))
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def do_POST(self):  # noqa: N802 - http.server 的约定名
            route = urllib.parse.urlsplit(self.path).path
            if path and route != path:
                self._respond(404, {'ok': False, 'reason': 'not_found'})
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except (TypeError, ValueError):
                self._respond(400, {'ok': False, 'reason': 'bad_content_length'})
                return
            if length <= 0:
                self._respond(400, {'ok': False, 'reason': 'empty_body'})
                return
            if length > int(max_body):
                self._respond(413, {'ok': False, 'reason': 'body_too_large'})
                return
            raw = self.rfile.read(length)
            headers = {str(key).lower(): str(value) for key, value in self.headers.items()}
            try:
                result = channel.handle_webhook(headers, raw)
            except FeishuError as exc:
                self._respond(400, {'ok': False, 'reason': type(exc).__name__})
                return
            except Exception:  # 不把内部异常细节回给外网
                self._respond(500, {'ok': False, 'reason': 'internal_error'})
                return
            status, payload = _http_response(result)
            self._respond(status, payload)

        def do_GET(self):  # noqa: N802
            self._respond(405, {'ok': False, 'reason': 'method_not_allowed'})

        def log_message(self, fmt, *args):  # 不把请求细节写进 stderr
            return

    return FeishuWebhookHandler


def serve(channel: FeishuChannel, *, host: str = '127.0.0.1', port: int = 8080,
          path: str = WEBHOOK_PATH, block: bool = True):
    """起 webhook 服务。**只由父 agent CLI 调用**；本模块与测试都不启动它。

    `block=True` 时进 `serve_forever()`（进程会被占住）；`block=False` 时返回 server 对象，
    由调用方自己管线程与 `shutdown()`。
    """
    handler = make_http_handler(channel, path=path)
    server = ThreadingHTTPServer((host, int(port)), handler)
    server.daemon_threads = True
    if block:
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return server
    return server
