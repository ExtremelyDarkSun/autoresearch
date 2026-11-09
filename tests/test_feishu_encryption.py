# GENERATED-BY: 飞书加密事件离线验收, 2026-11-10 +08:00
# LAST-EDIT: 2026-11-10 +08:00
# PURPOSE: 验 AES-256-CBC 解密器（官方固定向量 / 严格拒绝）与加密 challenge/文字/卡片走完整链路
# ORIGIN: kernel/autoresearch/channels/crypto.py + feishu.handle_webhook + DecisionService.ingest
# CUMULATIVE-DELTA: 首版。全部离线：假凭据、假传输、回环 HTTP；不发任何真实消息。
"""飞书加密事件的离线验收。

⛔ 没有真实凭据、没有连外网、没有往任何租户发消息。所有密文都是本文件用
`cryptography` 现造的（或官方文档给的固定向量）。

覆盖两件事：

1. **解密器本身**：官方固定向量（`test key` + 官方示例密文 → `hello world`）、
   严格 base64 / 长度 / PKCS7 / UTF-8 / JSON 对象 / 重复键 / 非有限字面量的拒绝。
2. **整条入向链路**：加密的 `challenge` 走 HTTP 能换回 200 + challenge；加密的文字消息
   与卡片点击走 `handle_webhook` → `authenticate` → `DecisionService.ingest` 能认证入账，
   而错签名 / 错密钥 / 坏填充 / 错 token / 重放 / 缺依赖**一律拒绝且零批准**。

需要可选依赖 `cryptography`（`python -m pip install -r requirements-feishu.txt`）。没装时相关用例
跳过，但「缺依赖 ⇒ 501 且不 ingest」这一条仍然全机器可跑。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from kernel.autoresearch import decisions_cli
from kernel.autoresearch.channels import crypto, feishu
from kernel.autoresearch.channels.feishu import FeishuChannel, compute_signature
from kernel.autoresearch.decisions import DecisionService
from kernel.autoresearch.runtime.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXP = 'exp-01'
RECIPIENT = 'oc_test_chat'
ALICE = 'ou_alice'
MALLORY = 'ou_mallory'
SHORT_CODE = 'K7Q2'
PAGE_HASH = 'b' * 64
CARD_HASH = 'a' * 64

#: 可选依赖是否可用（决定哪些用例跳过）。
HAS_CRYPTO = crypto.backend_available()
CRYPTO_REASON = '需要可选依赖 cryptography（python -m pip install -r requirements-feishu.txt）'

#: 官方文档给的固定向量：Encrypt Key=`test key`，密文解出 `hello world`。
OFFICIAL_KEY = 'test key'
OFFICIAL_VECTOR = 'P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk='

_CREATED = []
_KEEP_TMP = bool(os.environ.get('AUTORESEARCH_KEEP_TMP'))


def make_tmp(prefix='feishu_crypto_'):
    (ROOT / 'temp').mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=ROOT / 'temp'))
    _CREATED.append(path)
    return path


def tearDownModule():
    if _KEEP_TMP:
        return
    for path in _CREATED:
        shutil.rmtree(path, ignore_errors=True)
    _CREATED.clear()


# ────────────────────────────── 接缝：假凭据 / 时钟 / 传输 ──────────────────────────────

#: 假凭据：只在测试进程里，⛔ 不是任何真实应用的值。
FAKE_CREDENTIALS = {'FEISHU_APP_ID': 'cli_test_only',
                    'FEISHU_APP_SECRET': 'secret_test_only',
                    'FEISHU_VERIFICATION_TOKEN': 'vt_test_only',
                    'FEISHU_ENCRYPT_KEY': 'ek_test_only'}


def fake_credential_source(name):
    return FAKE_CREDENTIALS.get(name)


class Clock:
    def __init__(self, now=None):
        self.now = float(now if now is not None else time.time())

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class Transport:
    """假传输：照飞书 API 的形状回包，并记录每一次调用。⛔ 不联网。"""

    def __init__(self):
        self.calls = []
        self.sent = []
        self._seq = 0

    @staticmethod
    def _wire(payload):
        return 200, json.dumps(payload).encode('utf-8')

    def __call__(self, method, url, body, headers, timeout):
        path = urllib.parse.urlsplit(url).path
        self.calls.append({'method': method, 'path': path})
        if method == 'POST' and path.endswith('/tenant_access_token/internal'):
            return self._wire({'code': 0, 'msg': 'ok', 'tenant_access_token': 't-test-only',
                               'expire': 7200})
        if method == 'POST' and path.endswith('/im/v1/files'):
            return self._wire({'code': 0, 'msg': 'success', 'data': {'file_key': 'file_key_1'}})
        if method == 'POST' and '/im/v1/messages' in path:
            payload = json.loads(bytes(body).decode('utf-8'))
            content = json.loads(payload['content'])
            self._seq += 1
            message_id = f'om_sent_{self._seq}'
            reply_to = None
            suffix = path.split('/im/v1/messages', 1)[1].lstrip('/')
            if suffix.endswith('/reply'):
                reply_to = urllib.parse.unquote(suffix[:-len('/reply')])
            record = {'message_id': message_id,
                      'receive_id': payload.get('receive_id') or RECIPIENT,
                      'chat_id': payload.get('receive_id') or RECIPIENT,
                      'msg_type': payload['msg_type'], 'content': content,
                      'reply_to': reply_to}
            self.sent.append(record)
            return self._wire({'code': 0, 'msg': 'success',
                               'data': {'message_id': message_id,
                                        'chat_id': record['chat_id'],
                                        'msg_type': payload['msg_type']}})
        if method == 'GET' and '/im/v1/messages/' in path:
            message_id = urllib.parse.unquote(path.rsplit('/im/v1/messages/', 1)[-1])
            for record in self.sent:
                if record['message_id'] == message_id:
                    return self._wire({'code': 0, 'msg': 'success', 'data': {'items': [{
                        'message_id': message_id, 'chat_id': record['chat_id'],
                        'msg_type': record['msg_type'], 'deleted': False,
                        'sender': {'sender_type': 'app', 'id_type': 'app_id'},
                        'body': {'content': json.dumps(record['content'], ensure_ascii=False,
                                                       sort_keys=True)},
                    }]}})
            return self._wire({'code': 0, 'msg': 'success', 'data': {'items': []}})
        raise AssertionError(f'未预料的请求：{method} {url}')


def make_config(tmp, **overrides):
    values = dict(app_id_ref='env:FEISHU_APP_ID', app_secret_ref='env:FEISHU_APP_SECRET',
                  verification_token_ref='env:FEISHU_VERIFICATION_TOKEN',
                  encrypt_key_ref='env:FEISHU_ENCRYPT_KEY', send_enabled=True,
                  receive_id_type='chat_id', allowed_recipients=(RECIPIENT,),
                  allowed_sender_open_ids=(ALICE,),
                  chat_bindings={RECIPIENT: EXP},
                  ledger_path=str(Path(tmp) / 'ledger.sqlite3'))
    values.update(overrides)
    return feishu.FeishuConfig(**values)


def make_channel(tmp, **overrides):
    transport = Transport()
    channel = FeishuChannel(make_config(tmp, **overrides), transport=transport,
                            credential_source=fake_credential_source, clock=Clock())
    return channel, transport


# ────────────────────────────── 密文夹具（测试侧加密，⛔ 生产不发）──────────────────────────────

def _crypto_primitives():
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return padding, Cipher, algorithms, modes


def seal(plaintext, encrypt_key, *, iv=None) -> str:
    """按飞书算法加密：`base64(iv + AES-256-CBC(PKCS7(明文)))`。仅测试用。"""
    padding, Cipher, algorithms, modes = _crypto_primitives()
    raw = plaintext if isinstance(plaintext, (bytes, bytearray)) \
        else json.dumps(plaintext, ensure_ascii=False).encode('utf-8')
    key = hashlib.sha256(encrypt_key.encode('utf-8')).digest()
    iv = iv if iv is not None else os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(bytes(raw)) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode('ascii')


def envelope(plaintext, encrypt_key=None) -> dict:
    return {'encrypt': seal(plaintext, encrypt_key or FAKE_CREDENTIALS['FEISHU_ENCRYPT_KEY'])}


def signed(body, *, clock, encrypt_key=None, timestamp=None, nonce='nonce-1'):
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    stamp = str(int(clock() if callable(clock) else clock)) if timestamp is None else str(timestamp)
    key = FAKE_CREDENTIALS['FEISHU_ENCRYPT_KEY'] if encrypt_key is None else encrypt_key
    signature = compute_signature(stamp, nonce, key, raw)
    headers = {'X-Lark-Request-Timestamp': stamp, 'X-Lark-Request-Nonce': nonce,
               'X-Lark-Signature': signature,
               'Content-Type': 'application/json; charset=utf-8'}
    return headers, raw


def message_event(event_id='ev-msg-1', *, text='现在进度怎么样了？', chat_id=RECIPIENT,
                  open_id=ALICE, message_id='om_in_1', create_time_ms=None,
                  sender_type='user', mentions_bot=True):
    message = {'message_id': message_id, 'chat_id': chat_id, 'chat_type': 'group',
               'message_type': 'text',
               'content': json.dumps({'text': text}, ensure_ascii=False)}
    if create_time_ms is not None:
        message['create_time'] = str(int(create_time_ms))
    if mentions_bot:
        message['mentions'] = [{'key': '@_user_1', 'mentioned_type': 'bot', 'name': 'bot'}]
    return {'schema': '2.0',
            'header': {'event_id': event_id, 'event_type': 'im.message.receive_v1',
                       'create_time': str(int(create_time_ms or time.time() * 1000)),
                       'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN'],
                       'app_id': 'cli_test_only', 'tenant_key': 'tk_test'},
            'event': {'sender': {'sender_id': {'open_id': open_id},
                                 'sender_type': sender_type},
                      'message': message}}


def card_action_event(event_id='ev-card-1', *, decision_id='E1.1', short_code=SHORT_CODE,
                      intent='approve', options=('A',), open_id=ALICE,
                      open_message_id=''):
    value = {'channel': 'feishu_channel', 'intent': intent, 'exp_id': EXP,
             'decision_id': decision_id, 'recipient': RECIPIENT,
             'short_code': short_code, 'options': list(options)}
    context = {'open_chat_id': RECIPIENT}
    if open_message_id:
        context['open_message_id'] = open_message_id
    return {'schema': '2.0',
            'header': {'event_id': event_id, 'event_type': 'card.action.trigger',
                       'create_time': str(int(time.time() * 1e6)),
                       'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']},
            'event': {'operator': {'open_id': open_id},
                      'action': {'tag': 'button', 'name': 'Button_x', 'value': value},
                      'context': context}}


def challenge_body(challenge='ajls384kdj1234'):
    return {'challenge': challenge, 'type': 'url_verification',
            'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']}


# ────────────────────────────── 决策链夹具（页 + 卡）──────────────────────────────

def option(oid, what=None):
    return {'id': oid, 'what': what or f'换抓手 {oid}',
            'fingerprint': [{'lever': 'agent.yaml tools', 'from_to': 'a→b'}],
            'sweep_group': '',
            'cost': {'gpu_hours': 1.5, 'wall_hours': 2.0, 'tasks': 10, 'machines': 1},
            'criteria': {'metrics': [{'metric': 'pass', 'threshold': '>=3', 'source': '自定',
                                      'rationale': '同一份提交包重复跑三次的跨度之内算噪声'}]},
            'tasklist': {'file': 'tasks.json', 'declared': ['t1', 't2'],
                         'regression': [{'id': 't1', 'in_arm_repeats': 2}]},
            'branches': [{'kind': kind, 'info': f'{kind} 能带回的信息', 'next': '下一步'}
                         for kind in ('all_pass', 'partial', 'all_fail', 'device_failure')],
            'stop_point': '第一题失败就停',
            'not_doing': ['不动题单', '不改判据'],
            'hypothesis': 'H1：换抓手能提高通过数',
            'expected_pass': ['t1'],
            'expected_effect': {'quantity': 'pass', 'unit': '题', 'predicted_delta': 2,
                                'scale_resolution': 1, 'scale_resolution_source': '自定',
                                'scale_resolution_why': '同一份提交包重复跑三次量出的跨度'}}


def plan_set():
    return {'exp_id': EXP, 'goal': '提高线上分数', 'scope': 'large',
            'question_family': 'Q1', 'from_baseline': 'base-01',
            'changed_faces': ['3'], 'version': 'v1', 'recommended': 'A',
            'options': [option('A'), option('B')],
            'brief': {'background': '背景：这一轮为什么存在',
                      'context': '上下文：和上一轮的关系',
                      'progress': '进展：现在做到哪'},
            'reader_view': {
                'title': '要不要跑这一批', 'summary': '这一轮要你决定跑哪一条',
                'sections': [{'title': '为什么值得做', 'body': '因为上一轮留下了矛盾'}],
                'arms': [{'name': 'A 臂', 'settings': '设置 A', 'purpose': '目的 A',
                          'comparison': '对基线', 'option_ids': ['A']}],
                'choices': [{'option_id': 'A', 'summary': '做 A', 'tradeoff': '花 1.5 GPU 小时'},
                            {'option_id': 'B', 'summary': '做 B', 'tradeoff': '更贵'}],
                'budget': 'GPU 1.5 h · 墙钟 2 h · 10 题', 'risks': ['可能白跑']}}


def decision():
    return {'decision_id': 'E1.1', 'recipient': RECIPIENT, 'scope': 'large',
            'owner_session': 'sess-01', 'needs_user': True,
            'reason': '大题单要人批', 'undecided': ['A', 'B'], 'recommended': 'A'}


def prepare_decision(db_path, tmp):
    plan_file = Path(tmp) / 'plan.json'
    plan_file.write_text(json.dumps(plan_set(), ensure_ascii=False), encoding='utf-8')
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = decisions_cli.main(['--db', str(db_path), 'prepare', '--exp', EXP,
                                   '--decision-id', 'E1.1', '--plan', str(plan_file),
                                   '--recipient', RECIPIENT, '--scope', 'large',
                                   '--owner-session', 'sess-01', '--reason', '大题单要人批',
                                   '--undecided', 'A,B'])
    return code, out.getvalue()


def start_server(channel):
    """走 `channels_cli listen` 用的同一个 `feishu.serve`（只是改成不阻塞、端口由系统分配）。"""
    server = feishu.serve(channel, host='127.0.0.1', port=0, block=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1], thread


def stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def post(port, headers, raw, path=feishu.WEBHOOK_PATH):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=bytes(raw),
                                     headers=dict(headers), method='POST')
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode('utf-8') or '{}')
        finally:
            exc.close()


# ══════════════════════════ 解密器：官方固定向量 + 缺依赖 ══════════════════════════

class FixedVectorTests(unittest.TestCase):

    def test_missing_backend_raises_unavailable_and_never_returns_plaintext(self):
        with mock.patch.object(crypto, 'backend_available', return_value=False):
            self.assertFalse(crypto.backend_detail()['available'])
            with self.assertRaises(crypto.DecryptUnavailable) as caught:
                crypto.decrypt_event_payload(OFFICIAL_KEY, OFFICIAL_VECTOR)
        text = str(caught.exception)
        self.assertIn('cryptography', text)
        self.assertNotIn(OFFICIAL_KEY, text)
        self.assertNotIn(OFFICIAL_VECTOR, text)

    @unittest.skipUnless(HAS_CRYPTO, CRYPTO_REASON)
    def test_official_fixed_vector_decrypts_to_hello_world(self):
        self.assertEqual(crypto.decrypt_wire(OFFICIAL_KEY, OFFICIAL_VECTOR), b'hello world')

    @unittest.skipUnless(HAS_CRYPTO, CRYPTO_REASON)
    def test_official_vector_is_not_a_json_event(self):
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decrypt_event_payload(OFFICIAL_KEY, OFFICIAL_VECTOR)
        self.assertIn('bad_json', str(caught.exception))
        self.assertNotIn('hello world', str(caught.exception))

    @unittest.skipUnless(HAS_CRYPTO, CRYPTO_REASON)
    def test_only_iv_plus_ciphertext_is_accepted(self):
        wire = seal({'a': 1}, OFFICIAL_KEY)
        decoded = base64.b64decode(wire)
        self.assertEqual(len(decoded) % 16, 0)
        self.assertEqual(crypto.decrypt_event_payload(OFFICIAL_KEY, wire), {'a': 1})


# ══════════════════════════ 解密器：严格拒绝 ══════════════════════════

class StrictJsonTests(unittest.TestCase):
    """明文侧严格性：不靠 AES，任何机器都能跑。"""

    def test_duplicate_keys_are_refused(self):
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decode_event_json(b'{"event_id": "a", "event_id": "b"}')
        self.assertIn('duplicate_key', str(caught.exception))
        self.assertNotIn('event_id', str(caught.exception))

    def test_non_finite_literals_are_refused(self):
        for literal in ('NaN', 'Infinity', '-Infinity'):
            with self.assertRaises(crypto.DecryptError) as caught:
                crypto.decode_event_json(f'{{"create_time": {literal}}}'.encode('utf-8'))
            self.assertIn('non_finite', str(caught.exception))

    def test_must_be_json_object(self):
        for raw in (b'[]', b'"x"', b'1', b'null'):
            with self.assertRaises(crypto.DecryptError) as caught:
                crypto.decode_event_json(raw)
            self.assertIn('not_object', str(caught.exception))

    def test_must_be_utf8_and_valid_json(self):
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decode_event_json(b'{"a": "\xff\xfe"}')
        self.assertIn('bad_utf8', str(caught.exception))
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decode_event_json(b'{"a":}')
        self.assertIn('bad_json', str(caught.exception))
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decode_event_json(b'')
        self.assertIn('empty_plaintext', str(caught.exception))


@unittest.skipUnless(HAS_CRYPTO, CRYPTO_REASON)
class StrictWireTests(unittest.TestCase):
    """密文侧严格性：base64 / 长度 / PKCS7。"""

    def test_bad_base64_is_refused(self):
        for wire in ('not base64!!', 'abc', '', '===='):
            with self.assertRaises(crypto.DecryptError) as caught:
                crypto.decrypt_wire(OFFICIAL_KEY, wire)
            self.assertIn('bad_base64', str(caught.exception))

    def test_non_string_wire_is_refused(self):
        for wire in (None, 123, {'x': 1}, [1, 2]):
            with self.assertRaises(crypto.DecryptError) as caught:
                crypto.decrypt_wire(OFFICIAL_KEY, wire)
            self.assertIn('bad_wire_type', str(caught.exception))

    def test_too_short_and_unaligned_are_refused(self):
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decrypt_wire(OFFICIAL_KEY, base64.b64encode(b'\x00' * 16).decode())
        self.assertIn('too_short', str(caught.exception))
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decrypt_wire(OFFICIAL_KEY, base64.b64encode(b'\x00' * 36).decode())
        self.assertIn('bad_length', str(caught.exception))

    def test_bad_padding_is_refused(self):
        padding, Cipher, algorithms, modes = _crypto_primitives()
        key = hashlib.sha256(OFFICIAL_KEY.encode('utf-8')).digest()
        iv = b'\x01' * 16
        padder = padding.PKCS7(128).padder()
        padded = padder.update(b'{"a": 1}' * 4) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = bytearray(encryptor.update(padded) + encryptor.finalize())
        ciphertext[-1] = 0x00                      # 破坏 PKCS7：末字节必须是 1..16
        wire = base64.b64encode(iv + bytes(ciphertext)).decode('ascii')
        with self.assertRaises(crypto.DecryptError) as caught:
            crypto.decrypt_wire(OFFICIAL_KEY, wire)
        self.assertIn('bad_padding', str(caught.exception))

    def test_missing_key_is_refused(self):
        for key in ('', '   ', None):
            with self.assertRaises(crypto.DecryptError) as caught:
                crypto.decrypt_wire(key, OFFICIAL_VECTOR)
            self.assertIn('missing_key', str(caught.exception))


# ══════════════════════════ 加密入向：端到端（handle_webhook / 回环 HTTP）══════════════════════════

@unittest.skipUnless(HAS_CRYPTO, CRYPTO_REASON)
class WebhookEncryptionTests(unittest.TestCase):
    """加密事件走真实 handle_webhook：challenge / 文字 / 卡片点击。"""

    def setUp(self):
        self.tmp = Path(make_tmp('feishu_crypto_e2e_'))
        self.web = self.tmp / 'web'
        self.web.mkdir()
        self.clock = Clock()
        self.store = Store(self.tmp / 'runtime.sqlite3')
        self.channel, self.transport = make_channel(self.tmp)
        self.channel.clock = self.clock
        self.service = DecisionService(self.store, channel=self.channel, clock=self.clock)
        self.key = FAKE_CREDENTIALS['FEISHU_ENCRYPT_KEY']

    def encrypted(self, plaintext, *, nonce='nonce-1', timestamp=None):
        body = envelope(plaintext, self.key)
        headers, raw = signed(body, clock=self.clock, nonce=nonce, timestamp=timestamp)
        return headers, raw

    def prepare_page_and_card(self):
        view = self.service.prepare(exp_id=EXP, decision=decision(), plan_set=plan_set(),
                                    page_dir=str(self.web))
        self.assertTrue(view['decision_id'])
        page = self.service.send_page('E1.1')
        self.assertTrue(page['ok'], page)
        card = self.service.send_card('E1.1')
        self.assertTrue(card['ok'], card)
        return card['receipt']

    def test_encrypted_challenge_over_http_is_answered(self):
        server, port, thread = start_server(self.channel)
        try:
            headers, raw = self.encrypted(challenge_body('ajls384kdj-challenge'))
            status, body = post(port, headers, raw)
        finally:
            stop_server(server, thread)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {'challenge': 'ajls384kdj-challenge'})

    def test_encrypted_text_message_is_authenticated_and_ingested(self):
        headers, raw = self.encrypted(message_event())
        result = self.channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['verification']['encrypted'])
        self.assertTrue(result['verification']['decrypted'])
        event = result['event']
        self.assertEqual(event['event_id'], 'ev-msg-1')
        self.assertEqual(event['exp_id'], EXP)
        self.assertEqual(event['principal'], ALICE)
        self.assertEqual(self.channel.authenticate(feishu.SOURCE, event), ALICE)
        row = self.service.ingest(feishu.SOURCE, event)
        self.assertEqual(row['status'], 'applied', row)
        self.assertEqual([item['event_id'] for item in self.service.inbox.pending_questions()],
                         ['ev-msg-1'])
        self.assertEqual(self.service.reply_status(exp_id=EXP)['text_events'], 1)

    def test_encrypted_card_click_resolves_the_decision(self):
        receipt = self.prepare_page_and_card()
        click = card_action_event(open_message_id=receipt['message_id'],
                                  short_code=receipt['short_code'])
        headers, raw = self.encrypted(click, nonce='nonce-card')
        result = self.channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['verification']['decrypted'])
        row = self.service.ingest(feishu.SOURCE, result['event'])
        self.assertEqual(row['status'], 'applied', row)
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'resolved')
        self.assertEqual(status['resolution'], 'user_approved')
        self.assertEqual(self.service.inbox.pending_questions(), [])

    def test_signature_is_checked_over_the_encrypted_body(self):
        """签名必须对密文 body 算：对明文算的签名不算数。"""
        plaintext = message_event()
        body = envelope(plaintext, self.key)
        stamp = str(int(self.clock()))
        raw_plaintext = json.dumps(plaintext, ensure_ascii=False).encode('utf-8')
        headers = {'X-Lark-Request-Timestamp': stamp, 'X-Lark-Request-Nonce': 'nonce-x',
                   'X-Lark-Signature': compute_signature(stamp, 'nonce-x', self.key,
                                                         raw_plaintext),
                   'Content-Type': 'application/json'}
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        result = self.channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'signature_mismatch')
        self.assertEqual(result['status'], 401)
        self.assertFalse(result['verification']['decrypted'])
        self.assertEqual(self.channel.poll(None)['events'], [])

    def test_wrong_encrypt_key_is_refused_without_ingest(self):
        code, text = prepare_decision(self.store.db_path, self.tmp)
        self.assertEqual(code, 0, text)
        body = {'encrypt': seal(message_event(), 'wrong-key-not-the-configured-one')}
        headers, raw = signed(body, clock=self.clock, nonce='nonce-wrong-key')
        result = self.channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'decryption_failed')
        self.assertEqual(result['status'], 400)
        self.assertFalse(result['verification']['decrypted'])
        self.assertEqual(self.channel.poll(None)['events'], [])
        self.assertEqual(self.service.inbox.pending_events(), [])
        self.assertEqual(self.service.status('E1.1')['state'], 'prepared')

    def test_bad_padding_is_refused_without_ingest(self):
        padding, Cipher, algorithms, modes = _crypto_primitives()
        key = hashlib.sha256(self.key.encode('utf-8')).digest()
        iv = b'\x02' * 16
        padder = padding.PKCS7(128).padder()
        padded = padder.update(b'{"challenge": "x"}') + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = bytearray(encryptor.update(padded) + encryptor.finalize())
        ciphertext[-1] = 0x00
        wire = base64.b64encode(iv + bytes(ciphertext)).decode('ascii')
        headers, raw = signed({'encrypt': wire}, clock=self.clock, nonce='nonce-padding')
        result = self.channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'decryption_failed')
        self.assertIn('bad_padding', result['detail'])
        self.assertEqual(self.channel.poll(None)['events'], [])

    def test_wrong_token_after_decryption_is_refused(self):
        body = challenge_body()
        body['token'] = 'not-the-configured-token'
        headers, raw = self.encrypted(body, nonce='nonce-token')
        result = self.channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'token_mismatch')
        self.assertEqual(result['status'], 401)
        self.assertEqual(self.channel.poll(None)['events'], [])

    def test_replay_of_the_same_encrypted_request_is_refused(self):
        headers, raw = self.encrypted(message_event(), nonce='nonce-replay')
        first = self.channel.handle_webhook(headers, raw)
        self.assertTrue(first['ok'], first)
        second = self.channel.handle_webhook(headers, raw)
        self.assertEqual(second['reason'], 'replay')
        self.assertEqual(second['status'], 409)
        self.assertEqual(len(self.channel.poll(None)['events']), 1)

    def test_encrypted_body_never_falls_back_to_plaintext_fields(self):
        """密文坏了，但同一 body 里塞了看起来合法的明文 challenge/token。"""
        body = {'encrypt': 'not-a-valid-wire', 'type': 'url_verification',
                'challenge': 'should-never-be-returned',
                'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']}
        headers, raw = signed(body, clock=self.clock, nonce='nonce-downgrade')
        result = self.channel.handle_webhook(headers, raw)
        self.assertEqual(result['status'], 400)
        self.assertEqual(result['reason'], 'decryption_failed')
        self.assertNotIn('should-never-be-returned', json.dumps(result, ensure_ascii=False))

    def test_encrypted_message_from_outsider_is_refused(self):
        headers, raw = self.encrypted(message_event(open_id=MALLORY), nonce='nonce-outsider')
        result = self.channel.handle_webhook(headers, raw)
        self.assertEqual(result['status'], 403)
        self.assertEqual(result['reason'], 'sender_not_allowed')
        self.assertEqual(self.channel.poll(None)['events'], [])


# ══════════════════════════ 缺依赖 ⇒ 501，且不 ingest（任何机器可跑）══════════════════════════

class MissingBackendWebhookTests(unittest.TestCase):
    """没装 cryptography 时：501 + 明说缺什么；⛔ 不降级明文、不 ingest、不产生批准。"""

    def setUp(self):
        self.tmp = Path(make_tmp('feishu_crypto_nobackend_'))
        self.clock = Clock()
        self.store = Store(self.tmp / 'runtime.sqlite3')
        self.channel, _transport = make_channel(self.tmp)
        self.channel.clock = self.clock
        self.service = DecisionService(self.store, channel=self.channel, clock=self.clock)
        code, text = prepare_decision(self.store.db_path, self.tmp)
        self.assertEqual(code, 0, text)

    def test_encrypted_request_is_501_and_decision_stays_prepared(self):
        body = {'encrypt': 'any-wire', 'type': 'url_verification', 'challenge': 'x',
                'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']}
        headers, raw = signed(body, clock=self.clock)
        unavailable = crypto.DecryptUnavailable(
            '没有可用的 AES 解密后端 ⇒ 装 cryptography')
        with mock.patch.object(feishu.channel_crypto, 'decrypt_event_payload',
                               side_effect=unavailable):
            result = self.channel.handle_webhook(headers, raw)
        self.assertEqual(result['status'], 501)
        self.assertEqual(result['reason'], 'decryption_unavailable')
        self.assertIn('cryptography', result['detail'])
        self.assertFalse(result['verification']['decrypted'])
        self.assertIsNone(result['event'])
        self.assertEqual(self.channel.poll(None)['events'], [])
        self.assertEqual(self.service.inbox.pending_events(), [])
        self.assertEqual(self.service.status('E1.1')['state'], 'prepared')


if __name__ == '__main__':
    (ROOT / 'temp').mkdir(exist_ok=True)
    unittest.main()
