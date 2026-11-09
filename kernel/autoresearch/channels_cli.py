# GENERATED-BY: 部署通道 CLI（飞书）: 2026-11-10 +08:00
# LAST-EDIT: 2026-11-10 +08:00
# PURPOSE: 通道配置诊断（doctor）、常驻收件（listen）与拉取入向（poll）的命令行入口
# ORIGIN: kernel/autoresearch/channels/feishu.py（make_http_handler / serve / authenticate）
#   + kernel/autoresearch/decisions.py 的 DecisionService.ingest
# CUMULATIVE-DELTA: 首版。doctor 只读本地配置、不联网、不回显凭据值；listen 默认只绑 127.0.0.1，
#   只把 handle_webhook 验签通过的事件交给核心 ingest；poll 一次性拉取，不自循环。
"""部署通道 CLI（目前只有飞书）。

    # 本地诊断：不联网、不打印任何凭据值、不需要 --db
    python -m kernel.autoresearch.channels_cli --config channel.json doctor
    python -m kernel.autoresearch.channels_cli --config '{"app_id_ref": "env:FEISHU_APP_ID", …}' doctor

    # 常驻收件：默认 127.0.0.1:8080，路由 /feishu/webhook
    python -m kernel.autoresearch.channels_cli --db state/runtime.sqlite3 \\
        --config channel.json listen [--host 127.0.0.1] [--port 8080] [--path /feishu/webhook]

    # 一次性拉取（游标由调用方保存；本命令不自循环、不做常驻）
    python -m kernel.autoresearch.channels_cli --db state/runtime.sqlite3 \\
        --config channel.json poll [--cursor 12] [--limit 50]

## 三条安全前提（都是「不成立就不做事」，不是建议）

1. **配置里只有凭据引用。** `--config` 的 JSON 走 `feishu.from_mapping`：出现
   `app_id` / `app_secret` / `verification_token` / `encrypt_key` 这类**值键**直接拒绝；
   引用必须是 `env:NAME`。doctor 只判「环境变量有没有值」，**从不打印值**，
   并在输出前自检一次（发现值泄漏就整份报告都不输出）。
2. **doctor 是只读诊断。** 不构造 `FeishuChannel`（构造会开 / 建台账并切 WAL），
   不建文件、不建目录、不发请求：台账不存在就报「尚未初始化」，存在则用 SQLite 只读 URI
   （无 `-wal` 时加 `immutable=1`，避免只读连接顺手建出 `-shm` / `-wal`）读表名。
   **不为了诊断成功去建状态** —— 台账由 `listen` / `poll` 首次运行建立。
3. **listen 默认只绑回环。** 非回环地址必须显式 `--allow-remote`，否则 exit 2。
   仓库不替你决定把 webhook 暴露到哪个网卡。
4. **只 ingest 验签通过的事件。** `handle_webhook` 返回 `ok=True, kind='event'` 才进
   `DecisionService.ingest`；验签失败、重放、未订阅类型、`url_verification` 一律不进。
   进了核心还有第二道：`Inbox.classify` 会调通道自己的 `authenticate()`，它只认本适配层
   持久过的已认证事件。**因此未认证事件不会产生任何批准** —— 这里没有任何「自动批准」路径。

## 加密推送：默认解密器（可选 `cryptography`）

飞书官方文档：**配置了 Encrypt Key 就一定会加密推送事件**（请求网址校验的 `challenge` 也一样），
未配置才明文；而签名校验本身**必须**用 Encrypt Key（`X-Lark-Signature` 的算式含 `encrypt_key`）。
两侧一合：「要验签」与「会收到密文」是同一件事 —— 不解密就等于收不到任何审批。

本仓库自带默认解密器 `channels/crypto.py`（AES-256-CBC，key=SHA256(Encrypt Key)，
PKCS7，wire=base64(iv+密文)；先验签原始 body，再解密，解密后照旧过 token / 身份 / 重放）：

    python -m pip install -r requirements-feishu.txt      # 只有 cryptography

缺依赖时**不降级**：`handle_webhook` 返回 501 `decryption_unavailable`，
`doctor` 的 `encryption_backend` 检查算 blocking。加密成功时 `verification.decrypted=true`。
⚠️ 未验：真实租户的加密推送（本地只用官方固定向量与自造密文验过）。

## 它不做什么

- **不发消息。** doctor / listen / poll 都不调 `send_*`；发送仍走
  `decisions_cli send`（要先过 `health()` 与 `send_enabled`）。
- **不做常驻守护。** 没有 systemd / 计划任务模板，也不重试起服务；挂了由部署方拉起。
- **不做游标持久化。** `poll` 打印下一游标，由调用方存。
- **不解密加密事件。** 标准库没有 AES ⇒ 收到 `{"encrypt": …}` 一律 501（见 feishu 模块）。
- **不代管身份。** 谁是允许拍板的人只由配置里的 `allowed_sender_open_ids` 决定。
- **不是「一点磁盘都不写」。** `doctor` 只读；但 `listen` / `poll` 是可运行组件，
  会建 / 写适配层台账与决策库。别把 doctor 的只读读成"整条 CLI 都是只读探针"。

退出码：`0` 正常 · `2` 参数 / 配置 / 通道 / 台账失败（fail-closed）。
`listen` 起服务成功后会阻塞；它正常返回只代表服务对象结束了（例如 `serve_forever` 被打断）。
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import argparse
import json
import os
import sqlite3
import urllib.parse

try:
    from kernel.autoresearch.channels import crypto as channel_crypto
    from kernel.autoresearch.channels import feishu
    from kernel.autoresearch.decisions import ChannelUnavailable, DecisionError, DecisionService
    from kernel.autoresearch.decisions_cli import (channel_error_text, load_channel_config,
                                                   load_feishu_config)
    from kernel.autoresearch.runtime.store import Store
except ImportError:  # pragma: no cover - 以包内相对导入之外的方式载入时的兜底
    from kernel.autoresearch.channels import crypto as channel_crypto
    from kernel.autoresearch.channels import feishu
    from kernel.autoresearch.decisions import ChannelUnavailable, DecisionError, DecisionService
    from kernel.autoresearch.decisions_cli import (channel_error_text, load_channel_config,
                                                   load_feishu_config)
    from kernel.autoresearch.runtime.store import Store

EXIT_OK, EXIT_CONFIG = 0, 2

#: `listen` 的默认绑定地址。默认只监听本机：要不要暴露到网络是部署决定。
LOOPBACK_HOST = '127.0.0.1'

#: `listen` 的默认端口。
DEFAULT_PORT = 8080

#: 算作回环的主机名 / 地址前缀。
LOOPBACK_NAMES = frozenset(('localhost', '127.0.0.1', '::1', '[::1]'))


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _require_db(args):
    db = getattr(args, 'db', None)
    if not db:
        raise ChannelUnavailable('这条命令需要 --db（runtime.sqlite3 路径）：'
                                 '已认证的入向事件要写进决策台账')
    return str(db)


def _is_loopback(host) -> bool:
    text = str(host or '').strip().lower()
    if text in LOOPBACK_NAMES:
        return True
    return text.startswith('127.')


# ────────────────────────────── 凭据：只判存在性 ──────────────────────────────

def ref_name(ref: str) -> str:
    """`env:FEISHU_APP_ID` → `FEISHU_APP_ID`。⛔ 只返回名字，不返回值。"""
    text = str(ref or '')
    return text[len(feishu.REF_PREFIX):] if text.startswith(feishu.REF_PREFIX) else text


def credential_state(name: str, source):
    """`(present, why)`。⛔ 只判存在性：值和长度都不往外带。"""
    try:
        value = source(name)
    except Exception as exc:
        return False, f'读取凭据源失败：{type(exc).__name__}'
    if isinstance(value, str) and value.strip():
        return True, ''
    return False, '凭据源里没有值（环境变量未设置或为空）'


def _credential_values(config, source) -> list:
    """`[(ref, value|None)]`，只给「输出前自检」用，⛔ 不进任何报告字段。"""
    values = []
    for ref in config.refs:
        try:
            value = source(ref_name(ref))
        except Exception:
            value = None
        values.append((ref, value.strip() if isinstance(value, str) else None))
    return values


# ────────────────────────────── 台账：只读探测 ──────────────────────────────

#: 适配层台账必须有的三张表（见 channels/feishu.py 的 `_SCHEMA`）。
LEDGER_TABLES = ('inbound', 'outbound', 'seen')


def _writable_dir(path: Path) -> bool:
    """从 `path` 往上找第一个已存在的目录，判它是否可写。不创建任何东西。"""
    probe = Path(path)
    while True:
        if probe.exists():
            return probe.is_dir() and os.access(probe, os.W_OK)
        if probe == probe.parent:
            return False
        probe = probe.parent


def ledger_state(config) -> dict:
    """只读探测适配层台账。⛔ **不建文件、不建目录、不改字节、不改 journal mode**。

    - 没有配置 `ledger_path` ⇒ 直接报未配置；
    - 文件不存在 ⇒ 报「尚未初始化」：那是 `listen` / `poll` 首次运行时才做的事，doctor 不代劳；
    - 文件存在 ⇒ 用 SQLite **只读 URI** 读 `sqlite_master`。没有 `-wal` 时加 `immutable=1`：
      实测普通 `mode=ro` 打开 WAL 库会顺手建出 `-shm` / `-wal` 两个文件，那不是只读。
    """
    path_text = str(getattr(config, 'ledger_path', '') or '')
    state = {'configured': bool(path_text), 'path': path_text, 'exists': False,
             'initialized': False, 'readable': False, 'tables': [], 'wal_present': False,
             'parent_writable': False, 'error': '', 'detail': ''}
    if not path_text:
        state['detail'] = '没有配置 ledger_path'
        return state
    path = Path(path_text).expanduser()
    state['exists'] = path.is_file()
    state['parent_writable'] = _writable_dir(path.parent)
    if not path.exists():
        state['detail'] = ('台账尚未初始化（文件不存在）：listen / poll 首次运行会建立；'
                           'doctor 不建文件、不建目录')
        return state
    if not path.is_file():
        state['error'] = '台账路径存在但不是文件'
        state['detail'] = f'不是文件：{path}'
        return state
    state['wal_present'] = (path.parent / (path.name + '-wal')).exists()
    query = '?mode=ro' + ('' if state['wal_present'] else '&immutable=1')
    uri = 'file:' + urllib.parse.quote(str(path.resolve())) + query
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        state['error'] = f'只读打开失败：{type(exc).__name__}: {exc}'
        state['detail'] = ('台账文件存在但读不了：可能正在被写 / 路径不对 / 不是 SQLite 库；'
                           'doctor 不去修它')
        return state
    tables = sorted(str(row[0]) for row in rows)
    state['tables'] = tables
    state['readable'] = True
    missing = [name for name in LEDGER_TABLES if name not in tables]
    state['initialized'] = not missing
    if missing:
        state['error'] = '缺表：' + '、'.join(missing)
        state['detail'] = '文件存在但不是本适配层的台账（可能路径配错）'
    else:
        state['detail'] = '台账已初始化：' + '、'.join(tables)
    return state


# ────────────────────────────── doctor ──────────────────────────────

def doctor_report(config, *, credential_source=None) -> dict:
    """本地配置诊断。⛔ **不构造 FeishuChannel**、不发网络请求、不打印任何凭据值。

    为什么不用 `FeishuChannel(config)`：它的构造会开（没有就建）`ledger_path` 指向的 sqlite
    台账，并 `PRAGMA journal_mode=WAL` —— 一次「诊断」不该留下状态。因此本函数只读配置、
    读环境变量判存在性、用只读 URI 看台账，**不建任何文件**（台账检查见 `ledger_state`）。

    字段口径：

    - `ok`：没有 **error 级**检查失败（配置自洽，不是"能上线"）。
    - `send_ready` / `inbound_ready`：按 `FeishuChannel.health()` 的同一组判据**自己算**的
      （doctor 不构造通道，所以不能直接调 health）；两者都不代表平台已验证。
    """
    if not isinstance(config, feishu.FeishuConfig):
        raise ChannelUnavailable('doctor 需要一个 feishu.FeishuConfig（channels/feishu.py）')
    source = credential_source or (lambda name: os.environ.get(name))

    checks = []
    credentials = []

    def check(identifier, ok, level, detail):
        checks.append({'id': identifier, 'ok': bool(ok), 'level': level, 'detail': detail})
        return bool(ok)

    refs = list(config.refs)
    check('credential_refs', bool(refs), 'info',
          (f'{len(refs)} 个 env: 引用：' + '、'.join(ref_name(ref) for ref in refs))
          if refs else '没有声明任何凭据引用')
    for ref in refs:
        name = ref_name(ref)
        present, why = credential_state(name, source)
        credentials.append({'ref': ref, 'name': name, 'present': present, 'note': why})
        check(f'credential_present:{name}', present, 'error',
              f'{name} 有值（值不打印）' if present else f'{name} {why}')

    check('inbound_verification', bool(config.encrypt_key_ref), 'error',
          'encrypt_key_ref 已给（参与签名校验）' if config.encrypt_key_ref
          else '没有 encrypt_key_ref：入向无法验签 ⇒ handle_webhook 一律 401（fail-closed），'
               '审批永远收不回来')
    check('inbound_token', bool(config.verification_token_ref), 'error',
          'verification_token_ref 已给（challenge 与 header.token 都要核）'
          if config.verification_token_ref
          else '没有 verification_token_ref：challenge / header.token 无法校验 ⇒ 入向全拒')
    check('inbound_senders', bool(config.allowed_sender_open_ids), 'error',
          f'{len(config.allowed_sender_open_ids)} 个允许拍板的 open_id'
          if config.allowed_sender_open_ids
          else 'allowed_sender_open_ids 为空：谁的入向都认证不了 ⇒ 不会产生任何批准')
    check('outbound_recipients', bool(config.allowed_recipients), 'error',
          f'{len(config.allowed_recipients)} 个允许的收件人'
          if config.allowed_recipients
          else 'allowed_recipients 为空：一条消息都发不出去（fail-closed）')

    ledger = ledger_state(config)
    if not ledger['configured']:
        check('ledger', False, 'error',
              '没有 ledger_path：无法持久 request_key↔message_id，也做不了入向重放保护 '
              '⇒ 不满足「同一 request_key 不重发」要求')
    elif ledger['error']:
        check('ledger', False, 'error', ledger['detail'] + '（' + ledger['error'] + '）')
    elif not ledger['exists']:
        # 尚未初始化：父目录可写就是正常首跑状态（警告）；不可写才是配置错误
        check('ledger', False, 'warn' if ledger['parent_writable'] else 'error',
              ledger['detail'] if ledger['parent_writable']
              else ledger['detail'] + '；而且父目录不可写 ⇒ 起服务时也建不起来')
    else:
        check('ledger', True, 'info', ledger['detail'])
    ledger_ready = bool(ledger['initialized']
                        or (not ledger['exists'] and ledger['parent_writable']))

    check('send_enabled', bool(config.send_enabled), 'info',
          'send_enabled=true：允许真实发送（doctor 自己一条都不发）'
          if config.send_enabled
          else 'send_enabled=false：默认禁发，核心因此拒发（要用必须显式打开）')
    check('chat_bindings', bool(config.chat_bindings), 'warn',
          f'{len(config.chat_bindings)} 条 chat/open_id → 实验号绑定'
          if config.chat_bindings
          else '没有任何绑定：文字入向只能靠「回复我们发过的消息」或「正文带已发卡短码」反查，'
               '其余一律 unbound_chat 拒收')
    check('mention_required', True, 'info',
          '群聊文字要 @ 机器人：这是平台触达条件，适配层只如实记录，不假装满足')
    check('encrypted_events', True, 'info',
          '配置了 encrypt_key_ref ⇒ 平台会加密推送事件（含 challenge，见官方加密策略文档）；'
          '解密后仍过全部 token / 身份 / 重放检查，失败不降级明文')
    backend = channel_crypto.backend_detail()
    check('encryption_backend', backend['available'], 'error',
          (f"{backend['name']} {backend['version']} 可用：{backend['algorithm']}")
          if backend['available'] else
          (f'没有可用的 AES 解密后端（可选依赖 {backend["name"]} 缺失）：配置 Encrypt Key 后'
           '飞书加密推送（含 challenge）⇒ 入向全部 501 decryption_unavailable。装：'
           + backend['install']))
    check('secret_handling', True, 'info',
          '配置只含 env: 引用；doctor 只判存在性，不打印值，也不发网络请求')

    app_id_ok, app_secret_ok = False, False
    if config.send_enabled:
        app_id_ok, _ = credential_state(ref_name(config.app_id_ref), source) \
            if config.app_id_ref else (False, '')
        app_secret_ok, _ = credential_state(ref_name(config.app_secret_ref), source) \
            if config.app_secret_ref else (False, '')
    outbound_ready = bool(config.send_enabled and config.allowed_recipients
                          and config.allowed_sender_open_ids
                          and config.encrypt_key_ref and config.verification_token_ref
                          and ledger_ready and app_id_ok and app_secret_ok)
    inbound_ready = bool(config.encrypt_key_ref and config.verification_token_ref
                         and config.allowed_sender_open_ids and ledger_ready)
    report = {
        'ok': True,
        'command': 'doctor',
        'adapter': feishu.ADAPTER,
        'network_calls': 0,
        'files_written': 0,
        'secrets_exposed': False,
        'blocking': [],
        'warnings': [],
        'send_ready': outbound_ready,
        'inbound_ready': inbound_ready,
        'readiness_basis': [
            'send_ready = send_enabled + allowed_recipients + allowed_sender_open_ids',
            '  + encrypt_key_ref + verification_token_ref + 台账可用 + app_id/app_secret 取到值',
            'inbound_ready = encrypt_key_ref + verification_token_ref',
            '  + allowed_sender_open_ids + 台账可用',
            '这是按 FeishuChannel.health() 同一组判据自己算的：doctor 不构造通道',
        ],
        'checks': checks,
        'credentials': credentials,
        'config': {
            'credential_refs': refs,
            'send_enabled': bool(config.send_enabled),
            'receive_id_type': config.receive_id_type,
            'allowed_recipients': len(config.allowed_recipients),
            'allowed_sender_open_ids': len(config.allowed_sender_open_ids),
            'chat_bindings': len(config.chat_bindings),
            'ledger_path': config.ledger_path,
            'mention_required': bool(config.mention_required),
            'timeout_seconds': config.timeout,
            'max_skew_seconds': config.max_skew_seconds,
            'allowlist_fingerprint': config.allowlist_fingerprint,
            'version': config.version,
        },
        'ledger': ledger,
        'encryption': dict(backend, platform_encrypts_when_configured=bool(config.encrypt_key_ref)),
        'channel': {
            'adapter': feishu.ADAPTER,
            'identity': 'feishu-bot:' + (config.app_id_ref or '<unset>'),
            'config_ref': config.config_ref,
            'version': config.version,
            'source': 'doctor 只读配置派生；没有构造 FeishuChannel（构造会开 / 建台账）',
        },
        'webhook': {
            'path': feishu.WEBHOOK_PATH,
            'signature': 'sha256(timestamp+nonce+encrypt_key+body)',
            'replay_guard': 'seen 表按 timestamp|nonce|signature 去重',
            'encrypted_events': (
                'aes-256-cbc 解密可用：先验签原始密文 body，再解密，再走 token / 身份 / 重放'
                if backend['available'] else
                '缺可选依赖 cryptography：加密事件一律 501 decryption_unavailable'
                '（⛔ 不降级明文）；装：' + backend['install']),
            'unauthenticated_events': '不进 ingest ⇒ 不产生批准',
        },
        'notes': [
            'doctor 不构造通道、不建台账、不建目录、不发网络请求。',
            '台账不存在时会报「尚未初始化」，由 listen / poll 首次运行去建。',
            'doctor 只证明本地配置自洽：平台上的送达、权限、群 @ 条件仍是未验边界。',
            'send_ready / inbound_ready 都不代表平台已验证。',
        ],
    }
    report['blocking'] = [item['id'] for item in checks
                          if item['level'] == 'error' and not item['ok']]
    report['warnings'] = [item['id'] for item in checks
                          if item['level'] == 'warn' and not item['ok']]
    report['ok'] = not report['blocking']

    # 输出前自检：报告里一旦出现某个凭据值，就整份都不输出（连值一起丢掉）。
    blob = json.dumps(report, ensure_ascii=False, sort_keys=True)
    leaked = [ref for ref, value in _credential_values(config, source)
              if value and len(value) >= 4 and value in blob]
    if leaked:
        return {'ok': False, 'command': 'doctor', 'error': 'secret_leak_detected',
                'refs': leaked, 'network_calls': 0, 'files_written': 0,
                'secrets_exposed': True,
                'note': '诊断结果里出现了某个凭据值 ⇒ 拒绝输出报告内容（不回显值，也不落盘）；'
                        '请查配置里是否有字段把值当成了普通文本（路径 / 允许列表 / 绑定键）'}
    return report


# ────────────────────────────── listen ──────────────────────────────

class IngestingFeishuChannel(feishu.FeishuChannel):
    """在**验签通过**之后把入向事件交给 `DecisionService.ingest`。

    ⛔ 只处理 `handle_webhook` 返回 `ok=True, kind='event'` 的事件。其它返回值
    （`url_verification` / `ignored` / `rejected`）一律不 ingest。这不是优化：
    没验签的 body 谁都能构造，把它送进决策链就等于允许伪造审批。

    第二个闸在核心里：`Inbox.classify` 调 `FeishuChannel.authenticate()`，它只认本适配层
    持久过的入向事件；caller 自己拼的 event 会得到 `rejected`，不会产生批准。
    """

    def __init__(self, config, *, service=None, log=None, **kwargs):
        super().__init__(config, **kwargs)
        self.service = service
        self.log = log
        #: 审计用：只记 event_id 与核心判定，不记正文、不记凭据。
        self.ingested = []

    def bind_service(self, service):
        """把核心服务接上。⛔ 不接也能起服务，但任何事件都不会被 ingest（fail-closed）。"""
        if not isinstance(service, DecisionService):
            raise ChannelUnavailable('bind_service 需要 DecisionService')
        self.service = service
        return self

    def handle_webhook(self, headers, raw_body):
        result = super().handle_webhook(headers, raw_body)
        if not isinstance(result, dict):
            return result
        if result.get('ok') and result.get('kind') == 'event' \
                and isinstance(result.get('event'), dict):
            ingest = self._ingest(result['event'])
            if not ingest.get('recorded'):
                # 事件已经在适配层台账里了，但没进核心台账 ⇒ 让平台重推。
                # 重推会走适配层的重复事件路径（同 event_id 同内容按幂等返回），不会重复入账。
                result['status'] = 500
                result['response'] = {'ok': False, 'reason': 'ingest_failed'}
        else:
            ingest = {'attempted': False, 'recorded': False,
                      'reason': str(result.get('reason') or result.get('kind') or ''),
                      'note': '没有验签通过的事件不进 ingest'}
        result['ingest'] = ingest
        if callable(self.log):
            self.log({'webhook': {'ok': bool(result.get('ok')),
                                  'kind': result.get('kind'),
                                  'status': result.get('status'),
                                  'reason': result.get('reason')},
                      'ingest': ingest})
        return result

    def _ingest(self, event):
        if self.service is None:
            return {'attempted': True, 'recorded': False, 'error': 'service_unbound',
                    'note': '没有绑定 DecisionService ⇒ 不 ingest（fail-closed）'}
        try:
            row = self.service.ingest(feishu.SOURCE, event)
        except Exception as exc:
            return {'attempted': True, 'recorded': False, 'error': type(exc).__name__,
                    'message': str(exc)[:400]}
        record = {'attempted': True, 'recorded': True,
                  'event_id': row['event_id'], 'status': row['status'],
                  'consumed': bool(row['consumed']), 'category': row['category'],
                  'error': row['error']}
        self.ingested.append({'event_id': record['event_id'], 'status': record['status']})
        return record


def build_listener(config_spec, db_path, *, transport=None, credential_source=None,
                   clock=None, log=None):
    """按 `listen` 的方式装配：`IngestingFeishuChannel` + `DecisionService`（不监听端口）。

    返回 `(channel, service)`。测试与部署共用这一条装配路径 —— 起服务那一步仍走
    `feishu.make_http_handler` / `feishu.serve`。
    """
    injected = {name: value for name, value in
                (('transport', transport), ('credential_source', credential_source),
                 ('clock', clock)) if value is not None}
    channel = IngestingFeishuChannel(load_feishu_config(config_spec), log=log, **injected)
    store = Store(Path(db_path))
    service = DecisionService(store, channel=channel, clock=clock)
    channel.bind_service(service)
    return channel, service


# ────────────────────────────── 子命令 ──────────────────────────────

def _cmd_doctor(args):
    # 没给 --config 时诊断默认配置：这样 doctor 能直接告诉你「还缺什么」。
    # ⛔ 不构造 FeishuChannel（构造会开 / 建台账），只读配置。
    config_spec = getattr(args, 'config', None)
    config = load_feishu_config(config_spec) if config_spec else feishu.FeishuConfig()
    report = doctor_report(config)
    _print(report)
    return EXIT_OK if report['ok'] else EXIT_CONFIG


def _cmd_listen(args):
    db = _require_db(args)
    if not getattr(args, 'config', None):
        raise ChannelUnavailable('listen 需要 --config：没有通道配置就验不了签名，'
                                 '收到的请求一律会被拒 ⇒ 起了也没用')
    host = str(getattr(args, 'host', None) or LOOPBACK_HOST)
    port = int(getattr(args, 'port', DEFAULT_PORT))
    path = str(getattr(args, 'path', None) or feishu.WEBHOOK_PATH)
    if not _is_loopback(host) and not getattr(args, 'allow_remote', False):
        raise ChannelUnavailable(
            f'拒绝把 webhook 绑到非回环地址 {host!r}：默认只绑 {LOOPBACK_HOST}。'
            '确实要经反向代理 / 内网暴露，先确认验签与传输层已就绪，再加 --allow-remote。')
    if not 0 <= port <= 65535:
        raise ChannelUnavailable(f'--port 必须在 0–65535 之间，现在是 {port!r}')
    if not channel_crypto.backend_available():
        # 配了 Encrypt Key 平台就加密推送 ⇒ 没有解密后端时这条服务对任何真实事件都只会 501。
        # 挂一个必然失败的服务比不挂更坏（人会以为在等）⇒ 起服务之前就拒，且不建库、不建台账。
        raise ChannelUnavailable(
            '拒绝启动 listen：没有可用的 AES 解密后端。配置 Encrypt Key 后飞书会加密推送'
            '全部事件（含请求网址校验的 challenge），挂一个必然 501 的服务没有意义 ⇒ '
            '本次没有建库、没有监听。先装可选依赖：'
            + channel_crypto.backend_detail()['install']
            + '（或 python -m pip install cryptography），再用 doctor 复核')
    log = _print if getattr(args, 'verbose', False) else None
    channel, _service_handle = build_listener(args.config, db, log=log)
    _print({'ok': True, 'command': 'listen',
            'listening': {'host': host, 'port': port, 'path': path},
            'database': db,
            'channel': channel.describe(),
            'security': {
                'only_loopback': _is_loopback(host),
                'ingest': '只接受 handle_webhook 验签通过的 im.message.receive_v1 与 card.action.trigger',
                'unauthenticated': '验签失败 / 重放 / 未知类型 / url_verification 一律不 ingest',
                'no_auto_approve': '未认证事件不入决策链；核心 authenticate 再核一次来源',
                'sends_messages': False,
            },
            'known_gaps': {
                'encrypted_events': '配置 Encrypt Key 后飞书加密推送全部事件（含 challenge），'
                                    '签名校验也必须有 Encrypt Key；解密需要可选依赖 cryptography'
                                    '（见 requirements-feishu.txt），缺它时入向一律 501。',
                'no_daemon': '本命令不做守护与重试：进程退出就没人收件。',
                'cursor_not_persisted': 'poll 的游标由调用方保存。',
            }})
    sys.stdout.flush()
    feishu.serve(channel, host=host, port=port, path=path)
    return EXIT_OK


def _cmd_poll(args):
    db = _require_db(args)
    if not getattr(args, 'config', None):
        raise ChannelUnavailable('poll 需要 --config：没有通道配置就拿不到已认证入向')
    channel = load_channel_config(args.config)
    service = DecisionService(Store(Path(db)), channel=channel)
    result = service.poll_channel(args.cursor, limit=int(args.limit), source=feishu.SOURCE)
    _print(result)
    failed = [item for item in result['results'] if item.get('error')]
    return EXIT_CONFIG if failed else EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(prog='channels_cli',
                                     description='部署通道（飞书）：本地诊断 · 常驻收件 · 拉取入向')
    parser.add_argument('--config', default=None,
                        help='通道配置：内联 JSON / JSON 文件 / @文件；凭据只写 env:NAME 引用')
    parser.add_argument('--db', default=None, help='runtime.sqlite3 路径（listen / poll 必填）')
    parser.add_argument('--verbose', action='store_true', default=False,
                        help='listen 时逐条打印验签与 ingest 摘要（不含凭据值）')
    sub = parser.add_subparsers(dest='command', required=True)

    doctor = sub.add_parser('doctor', help='本地配置诊断：不联网、不打印凭据值（可无 --config）')
    doctor.set_defaults(func=_cmd_doctor)

    listen = sub.add_parser('listen', help='起 webhook 收件服务（默认只绑 127.0.0.1）')
    listen.add_argument('--host', default=LOOPBACK_HOST)
    listen.add_argument('--port', type=int, default=DEFAULT_PORT)
    listen.add_argument('--path', default=feishu.WEBHOOK_PATH)
    listen.add_argument('--allow-remote', dest='allow_remote', action='store_true', default=False,
                        help='显式允许绑定非回环地址（默认拒绝）')
    listen.set_defaults(func=_cmd_listen)

    poll = sub.add_parser('poll', help='一次性拉取已认证入向并 ingest（不自循环）')
    poll.add_argument('--cursor', default=None, help='上次返回的游标（省略表示从头）')
    poll.add_argument('--limit', type=int, default=50, help='本次最多几条（1–500）')
    poll.set_defaults(func=_cmd_poll)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ChannelUnavailable, DecisionError, feishu.FeishuError,
            OSError, ValueError, KeyError) as exc:
        _print({'ok': False, 'error': type(exc).__name__, 'message': channel_error_text(exc)})
        return EXIT_CONFIG


if __name__ == '__main__':
    raise SystemExit(main())
