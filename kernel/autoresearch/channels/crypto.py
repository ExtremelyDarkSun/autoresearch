# GENERATED-BY: 飞书事件加密体默认解密器, 2026-11-10 +08:00
# LAST-EDIT: 2026-11-10 +08:00
# PURPOSE: 用可选 cryptography 依赖解飞书加密事件（AES-256-CBC），严格校验、缺失即 fail-closed
# ORIGIN: open.feishu.cn 加密策略文档（Encrypt Key / AES-256-CBC / base64(iv+密文)）
# CUMULATIVE-DELTA: 首版。只做解密，不做验签；密钥派生与填充按官方文档；不使用自写 AES、不调 openssl。
"""飞书事件加密体的默认解密器（可选 `cryptography` 依赖）。

## 平台事实（官方文档，2026-11-10 核对）

- 应用里**配置了 Encrypt Key ⇒ 平台加密推送所有事件**（请求网址校验的 `challenge` 也加密）；
  没配才明文推送。所以「要验签」和「会收到密文」是同一件事：签名算式
  `SHA256(timestamp + nonce + encrypt_key + 原始 body)` 本身就要求 Encrypt Key。
- 算法：**AES-256-CBC**，`key = SHA256(Encrypt Key 的 UTF-8 字节)`（32 字节），
  PKCS7 填充，随机 16 字节 IV，wire = `base64(iv + 密文)`。
- 解密后的内容才是明文事件 JSON（`{"schema":…,"header":…,"event":…}` 或
  `{"type":"url_verification","challenge":…,"token":…}`）。

## 本模块的边界

- **只解密，不验签。** 调用方必须先对**原始（密文）body**完成签名校验，再调这里 ——
  反过来就等于让未认证的字节驱动解密逻辑。
- **严格**：base64 只收标准字母表（`validate=True`）、长度必须 `≥32` 且 `(len-16) % 16 == 0`、
  PKCS7 填充必须合法、明文必须能按 UTF-8 解码、JSON 必须是**对象**。任一不成立都抛
  `DecryptError`（带原因标签，⛔ 不带密钥、不带明文、不带密文）。
- **缺依赖不降级**：没装 `cryptography` 时抛 `DecryptUnavailable`，
  ⛔ 绝不把密文当明文解析、也不假装成功。核心其他功能仍然只用标准库。
- 大小上限 `MAX_DECODED_BYTES`（1 MiB），与 webhook 请求体上限同量级。

安装（在仓库根执行；`python -m pip` 便于定位是哪个解释器）：

    python -m pip install -r requirements-feishu.txt      # 只有 cryptography

## 未验边界

- 真实租户的加密推送**没有**实测（没有真实凭据、没有往真实租户发过任何消息）。
- 已验的是：官方文档给的固定向量（`test key` → `hello world`）与本地生成的加密
  challenge / 文字 / 卡片点击走完整 `handle_webhook` → 认证 → `DecisionService.ingest`。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

#: 依赖名（doctor / 报错里用来指安装目标）。
BACKEND_NAME = 'cryptography'

#: 分组长度（AES 固定 16 字节；IV 也正好一个分组）。
BLOCK_BYTES = 16

#: 解密后明文上限（webhook 原始 body 另有 1 MiB 上限，见 feishu.MAX_WEBHOOK_BYTES）。
MAX_DECODED_BYTES = 1024 * 1024

try:  # pragma: no cover - 依赖是否存在由部署环境决定
    from cryptography import __version__ as _CRYPTOGRAPHY_VERSION
    from cryptography.hazmat.primitives import padding as _padding
    from cryptography.hazmat.primitives.ciphers import Cipher as _Cipher
    from cryptography.hazmat.primitives.ciphers import algorithms as _algorithms
    from cryptography.hazmat.primitives.ciphers import modes as _modes
    _BACKEND_ERROR = ''
except Exception as _exc:  # pragma: no cover - 没装可选依赖的部署
    _CRYPTOGRAPHY_VERSION = ''
    _padding = _Cipher = _algorithms = _modes = None
    _BACKEND_ERROR = f'{type(_exc).__name__}: {_exc}'


class CryptoError(RuntimeError):
    """解密层自身的拒绝。"""


class DecryptUnavailable(CryptoError):
    """缺解密依赖（`cryptography`）⇒ 配置不完整，⛔ 不降级成明文。"""


class DecryptError(CryptoError, ValueError):
    """密文不合法（base64 / 长度 / 填充 / UTF-8 / JSON 对象）⇒ 400 级拒绝。"""


def backend_available() -> bool:
    """解密后端是否可用。⛔ 不尝试安装、不联网。"""
    return _Cipher is not None and _padding is not None and _algorithms is not None \
        and _modes is not None


def backend_detail() -> dict:
    """给 doctor / health 用的能力说明。⛔ 不含任何密钥或密文。"""
    available = backend_available()
    return {'name': BACKEND_NAME,
            'available': available,
            'version': _CRYPTOGRAPHY_VERSION if available else '',
            'algorithm': 'AES-256-CBC / key=SHA256(Encrypt Key) / PKCS7 / base64(iv+ciphertext)',
            'install': 'python -m pip install -r requirements-feishu.txt',
            'requirement': '' if available else '需要可选依赖 cryptography（核心其余功能仍用标准库）',
            'error': '' if available else _BACKEND_ERROR}


def _require_backend():
    if not backend_available():
        raise DecryptUnavailable(
            '没有可用的 AES 解密后端 ⇒ 无法处理飞书加密事件；装可选依赖后再起服务：'
            'python -m pip install -r requirements-feishu.txt'
            '（或 python -m pip install cryptography）'
            '（⛔ 不会把密文当明文解析，也不假装成功）')


def _key_bytes(encrypt_key) -> bytes:
    """`key = SHA256(Encrypt Key UTF-8)`。⛔ 不 strip、不改写调用方的密钥。"""
    if not isinstance(encrypt_key, str) or not encrypt_key.strip():
        raise DecryptError('missing_key：没有 Encrypt Key 值，无法派生解密密钥')
    return hashlib.sha256(encrypt_key.encode('utf-8')).digest()


def _wire_bytes(wire) -> bytes:
    """严格 base64 解码。只收标准字母表，⛔ 不自动修补 / 不忽略非法字符。"""
    if isinstance(wire, (bytes, bytearray)):
        data = bytes(wire)
    elif isinstance(wire, str):
        data = wire.encode('utf-8')
    else:
        raise DecryptError(f'bad_wire_type：encrypt 字段必须是字符串（现在是 '
                           f'{type(wire).__name__}）')
    data = data.strip()
    if not data:
        raise DecryptError('bad_base64：encrypt 字段是空的')
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DecryptError(f'bad_base64：{type(exc).__name__}') from exc
    return decoded


def _split(decoded: bytes):
    if len(decoded) < BLOCK_BYTES * 2:
        raise DecryptError(f'too_short：解码后 {len(decoded)} 字节，至少要 {BLOCK_BYTES * 2}'
                           '（16 字节 IV + 至少一个分组）')
    if (len(decoded) - BLOCK_BYTES) % BLOCK_BYTES:
        raise DecryptError(f'bad_length：密文长度 {len(decoded) - BLOCK_BYTES} 不是 '
                           f'{BLOCK_BYTES} 的整数倍')
    if len(decoded) > MAX_DECODED_BYTES + BLOCK_BYTES:
        raise DecryptError(f'too_large：密文 {len(decoded)} 字节超过上限')
    return decoded[:BLOCK_BYTES], decoded[BLOCK_BYTES:]


def decrypt_wire(encrypt_key, wire) -> bytes:
    """`base64(iv+密文)` → 明文字节（已去 PKCS7 填充）。严格校验，⛔ 不猜。"""
    _require_backend()
    key = _key_bytes(encrypt_key)
    decoded = _wire_bytes(wire)
    iv, ciphertext = _split(decoded)
    try:
        decryptor = _Cipher(_algorithms.AES(key), _modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
    except Exception as exc:  # cryptography 的底层拒绝（长度/参数）
        raise DecryptError(f'cipher_failed：{type(exc).__name__}') from exc
    unpadder = _padding.PKCS7(BLOCK_BYTES * 8).unpadder()
    try:
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise DecryptError('bad_padding：PKCS7 填充不合法（密钥不对或密文被改）') from exc
    if len(plaintext) > MAX_DECODED_BYTES:
        raise DecryptError(f'too_large：明文 {len(plaintext)} 字节超过上限')
    return plaintext


def _no_duplicate_keys(pairs):
    """明文 JSON 里出现重复键 ⇒ 拒（`json` 默认后值覆盖前值，等于悄悄改内容）。"""
    seen = set()
    for key, _value in pairs:
        if key in seen:
            # ⛔ 不回显键名：键也是调用方内容的一部分
            raise DecryptError('duplicate_key：明文 JSON 里有重复键 ⇒ 拒（不猜哪一份为准）')
        seen.add(key)
    return dict(pairs)


def _no_non_finite(literal):
    """明文 JSON 里出现 NaN / Infinity / -Infinity ⇒ 拒（不是合法 JSON，且会比较恒假）。"""
    raise DecryptError('non_finite：明文 JSON 里有非有限字面量（NaN / Infinity）⇒ 拒')


def decode_event_json(plaintext: bytes) -> dict:
    """明文 → 事件 JSON 对象。严格 UTF-8、严格 JSON、必须是对象。

    ⛔ 额外拒绝：重复键、`NaN` / `Infinity` 这类非有限字面量 —— 它们会让后面的比较
    静默失效（例如时间戳闸），必须在这里就拒掉。
    """
    if not isinstance(plaintext, (bytes, bytearray)) or not plaintext:
        raise DecryptError('empty_plaintext：解密结果为空')
    try:
        text = bytes(plaintext).decode('utf-8')
    except UnicodeDecodeError as exc:
        raise DecryptError('bad_utf8：解密结果不是 UTF-8') from exc
    try:
        body = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                          parse_constant=_no_non_finite)
    except DecryptError:
        raise
    except ValueError as exc:
        raise DecryptError(f'bad_json：{type(exc).__name__}') from exc
    if not isinstance(body, dict):
        raise DecryptError('not_object：解密结果必须是 JSON 对象（事件体不是数组/标量）')
    return body


def decrypt_event_payload(encrypt_key, wire) -> dict:
    """飞书 `{"encrypt": …}` 的 payload → 明文事件字典。

    ⚠️ 调用方必须先对**原始密文 body**验签，再调这里；本函数不做任何身份判断。
    """
    return decode_event_json(decrypt_wire(encrypt_key, wire))
