# GENERATED-BY: feishu 通道适配层离线验收, 2026-11-10 +08:00
# LAST-EDIT: 2026-11-10 +08:00
# PURPOSE: 用假传输跑真实适配层 —— token 失败 / 上传成功发送失败 / 超时不重发 / 伪回执 / 验签顺序 / 重放 / 卡点击 / 允许列表
# ORIGIN: kernel/autoresearch/channels/feishu.py + decisions.ChannelReceipt 协议
# CUMULATIVE-DELTA: 首版。全部离线：不连网络、不读真实环境变量、不发任何真实消息、不起任何服务。
"""飞书通道适配层的离线验收。

⛔ 这里**没有**真实凭据、没有真实平台调用、没有启动 HTTP 服务：
`transport` 与 `credential_source` 都是注入的假实现，「真的」只剩适配层自己的代码路径。
所以这些用例证明的是**适配层行为**，不是**平台行为** —— 真实租户上的送达、权限、
群 @ 触发条件、加密事件都仍是未验边界（见模块 docstring 的「实测边界」）。

临时目录跑完就删（`temp/feishu_*`）；要留着排查就设 `AUTORESEARCH_KEEP_TMP=1`。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from kernel.autoresearch.channels import feishu
from kernel.autoresearch.channels.feishu import (AmbiguousSend, FeishuChannel, FeishuConfig,
                                                FeishuConfigError, NotAuthenticated,
                                                compute_signature)
from kernel.autoresearch.decisions import ChannelReceipt, DecisionService
from kernel.autoresearch.runtime.delivery import NotSent
from kernel.autoresearch.runtime.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXP = 'exp-01'
RECIPIENT = 'oc_test_chat'
ALICE = 'ou_alice'
MALLORY = 'ou_mallory'
SHORT_CODE = 'K7Q2'
PAGE_HASH = 'b' * 64
CARD_HASH = 'a' * 64
REPLY_HASH = 'c' * 64

#: 本次进程建过的临时目录（`tearDownModule` 统一清掉）。
_CREATED = []
_KEEP_TMP = bool(os.environ.get('AUTORESEARCH_KEEP_TMP'))


def make_tmp(prefix='feishu_case_'):
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=ROOT / 'temp'))
    _CREATED.append(path)
    return path


def tearDownModule():
    if _KEEP_TMP:
        return
    for path in _CREATED:
        shutil.rmtree(path, ignore_errors=True)
    _CREATED.clear()


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
    """假传输：照飞书 API 的形状回包，并记录每一次调用（用来断言「有没有发出去」）。"""

    def __init__(self):
        self.calls = []
        self.sent = []
        self.token_response = None     # (status, dict) 覆盖 token 回包
        self.token_error = None        # Exception 覆盖 token 调用
        self.upload_response = None
        self.upload_error = None
        self.send_response = None      # (status, dict) 覆盖发消息回包
        self.send_error = None         # Exception 覆盖发消息调用
        self.get_override = None       # callable(message_id) -> (status, dict)
        self._seq = 0

    # ── 计数 ──
    def posts(self, needle):
        return [call for call in self.calls
                if call['method'] == 'POST' and needle in call['path']]

    @property
    def message_posts(self):
        return [call for call in self.calls
                if call['method'] == 'POST' and '/im/v1/messages' in call['path']]

    # ── 入口 ──
    @staticmethod
    def _wire(result):
        """把 `(status, dict)` 变成真实传输的形态 `(status, bytes)`。"""
        status, payload = result
        if isinstance(payload, (bytes, bytearray)):
            return int(status), bytes(payload)
        return int(status), json.dumps(payload, ensure_ascii=False).encode('utf-8')

    def __call__(self, method, url, body, headers, timeout):
        path = urllib.parse.urlsplit(url).path
        self.calls.append({'method': method, 'url': url, 'path': path,
                           'body': body, 'headers': dict(headers or {})})
        if method == 'POST' and path.endswith('/tenant_access_token/internal'):
            if self.token_error is not None:
                raise self.token_error
            if self.token_response is not None:
                return self._wire(self.token_response)
            return self._wire((200, {'code': 0, 'msg': 'ok',
                                     'tenant_access_token': 't-test-only', 'expire': 7200}))
        if method == 'POST' and path.endswith('/im/v1/files'):
            if self.upload_error is not None:
                raise self.upload_error
            if self.upload_response is not None:
                return self._wire(self.upload_response)
            return self._wire((200, {'code': 0, 'msg': 'success',
                                     'data': {'file_key': 'file_key_1'}}))
        if method == 'POST' and '/im/v1/messages' in path:
            return self._send(path, body)
        if method == 'GET' and '/im/v1/messages/' in path:
            return self._get(path)
        raise AssertionError(f'未预料的请求：{method} {url}')

    def _send(self, path, body):
        if self.send_error is not None:
            raise self.send_error
        if self.send_response is not None:
            return self._wire(self.send_response)
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
                  'msg_type': payload['msg_type'],
                  'content': content, 'reply_to': reply_to, 'uuid': payload.get('uuid')}
        self.sent.append(record)
        return self._wire((200, {'code': 0, 'msg': 'success',
                                 'data': {'message_id': message_id,
                                          'chat_id': record['chat_id'],
                                          'msg_type': payload['msg_type']}}))

    def _get(self, path):
        message_id = urllib.parse.unquote(path.rsplit('/im/v1/messages/', 1)[-1])
        if self.get_override is not None:
            return self._wire(self.get_override(message_id))
        for record in self.sent:
            if record['message_id'] == message_id:
                return self._wire((200, {'code': 0, 'msg': 'success', 'data': {'items': [{
                    'message_id': message_id,
                    'chat_id': record['chat_id'],
                    'msg_type': record['msg_type'],
                    'deleted': False,
                    'sender': {'sender_type': 'app', 'id_type': 'app_id'},
                    'body': {'content': json.dumps(record['content'], ensure_ascii=False,
                                                   sort_keys=True)},
                }]}}))
        return self._wire((200, {'code': 0, 'msg': 'success', 'data': {'items': []}}))


# ────────────────────────────── 夹具 ──────────────────────────────

def make_config(tmp, **overrides):
    values = dict(app_id_ref='env:FEISHU_APP_ID', app_secret_ref='env:FEISHU_APP_SECRET',
                  verification_token_ref='env:FEISHU_VERIFICATION_TOKEN',
                  encrypt_key_ref='env:FEISHU_ENCRYPT_KEY', send_enabled=True,
                  receive_id_type='chat_id', allowed_recipients=(RECIPIENT,),
                  allowed_sender_open_ids=(ALICE,),
                  chat_bindings={RECIPIENT: EXP},
                  ledger_path=str(Path(tmp) / 'ledger.sqlite3'))
    values.update(overrides)
    return FeishuConfig(**values)


def make_channel(tmp, transport=None, clock=None, **overrides):
    transport = transport if transport is not None else Transport()
    channel = FeishuChannel(make_config(tmp, **overrides), transport=transport,
                            credential_source=fake_credential_source,
                            clock=clock or Clock())
    return channel, transport


def page_payload(request_key='hil:page-1', decision_id='E1.1', artifact='', **changes):
    payload = {'request_key': request_key, 'payload_hash': PAGE_HASH,
               'kind': 'approval_page', 'exp_id': EXP, 'recipient': RECIPIENT,
               'decision_id': decision_id, 'body': '', 'artifact': artifact,
               'sha256': PAGE_HASH, 'event_reply_to': None}
    payload.update(changes)
    return payload


def card_payload(request_key='hil:card-1', decision_id='E1.1',
                 options=(('A', '换抓手 A'),), **changes):
    payload = {'request_key': request_key, 'payload_hash': CARD_HASH, 'kind': 'card',
               'exp_id': EXP, 'recipient': RECIPIENT, 'decision_id': decision_id,
               'body': f'[exp-01 {decision_id} / {SHORT_CODE}] 要不要跑这一批',
               'short_code': SHORT_CODE,
               'card_options': [{'option_id': oid, 'label': label} for oid, label in options],
               'card_action_template': {'intent': 'approve', 'exp_id': EXP,
                                        'decision_id': decision_id,
                                        'recipient': RECIPIENT,
                                        'short_code': SHORT_CODE},
               'event_reply_to': None}
    payload.update(changes)
    return payload


def reply_payload(event_id, request_key='hil:reply-1', **changes):
    payload = {'request_key': request_key, 'payload_hash': REPLY_HASH, 'kind': 'reply',
               'exp_id': EXP, 'recipient': RECIPIENT, 'decision_id': '',
               'body': f'{EXP} 的答复正文',
               'event_reply_to': {'source': feishu.SOURCE, 'event_id': event_id,
                                  'exp_id': EXP, 'recipient': RECIPIENT}}
    payload.update(changes)
    return payload


def to_receipt(raw):
    return ChannelReceipt(message_id=raw['message_id'], request_key=raw['request_key'],
                          exp_id=raw['exp_id'], recipient=raw['recipient'],
                          payload_hash=raw['payload_hash'], kind=raw['kind'],
                          decision_id=raw['decision_id'], path=raw['path'],
                          bytes=raw['bytes'], sha256=raw['sha256'],
                          short_code=raw['short_code'], adapter=raw['adapter'],
                          conditions=raw['conditions'], ok=True, returncode=0,
                          sent_at=raw['sent_at'])


def message_event(event_id='ev-msg-1', *, text='现在进度怎么样了？', chat_id=RECIPIENT,
                  open_id=ALICE, message_id='om_in_1', parent_id=None,
                  create_time_ms=None, sender_type='user', mentions_bot=True):
    message = {'message_id': message_id, 'chat_id': chat_id, 'chat_type': 'group',
               'message_type': 'text',
               'content': json.dumps({'text': text}, ensure_ascii=False)}
    if parent_id:
        message['parent_id'] = parent_id
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
                      open_message_id='', create_time_us=None):
    value = {'channel': 'feishu_channel', 'intent': intent, 'exp_id': EXP,
             'decision_id': decision_id, 'recipient': RECIPIENT,
             'short_code': short_code, 'options': list(options)}
    context = {'open_chat_id': RECIPIENT}
    if open_message_id:
        context['open_message_id'] = open_message_id
    return {'schema': '2.0',
            'header': {'event_id': event_id, 'event_type': 'card.action.trigger',
                       'create_time': str(int(create_time_us or time.time() * 1e6)),
                       'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']},
            'event': {'operator': {'open_id': open_id},
                      'action': {'tag': 'button', 'name': 'Button_x', 'value': value},
                      'context': context}}


def signed(body, *, clock, encrypt_key=None, timestamp=None, nonce='nonce-1'):
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    stamp = str(int(clock() if callable(clock) else clock)) if timestamp is None else str(timestamp)
    key = FAKE_CREDENTIALS['FEISHU_ENCRYPT_KEY'] if encrypt_key is None else encrypt_key
    signature = compute_signature(stamp, nonce, key, raw)
    headers = {'X-Lark-Request-Timestamp': stamp, 'X-Lark-Request-Nonce': nonce,
               'X-Lark-Signature': signature,
               'Content-Type': 'application/json; charset=utf-8'}
    return headers, raw


# ══════════════════════════ 配置 ══════════════════════════

class ConfigTests(unittest.TestCase):

    def test_credentials_must_be_env_references_not_values(self):
        with self.assertRaises(FeishuConfigError):
            FeishuConfig(app_id_ref='cli_realvalue', app_secret_ref='env:SECRET')
        with self.assertRaises(FeishuConfigError):
            feishu.from_mapping({'app_id_ref': 'env:A', 'app_secret': 'oops'})

    def test_unknown_config_key_is_refused(self):
        with self.assertRaises(FeishuConfigError):
            feishu.from_mapping({'app_id_ref': 'env:A', 'whatever': 1})

    def test_default_channel_is_disabled_and_health_says_why(self):
        tmp = make_tmp('feishu_cfg_')
        channel, transport = make_channel(tmp, send_enabled=False)
        health = channel.health()
        self.assertFalse(health['ok'])
        self.assertTrue(any('send_enabled' in reason for reason in health['detail']['reasons']))
        self.assertTrue(health['mention_required'])
        self.assertEqual(transport.calls, [])

    def test_describe_has_only_references_and_config_fingerprint(self):
        tmp = make_tmp('feishu_cfg_')
        channel, _ = make_channel(tmp)
        self.assertIsInstance(channel, FeishuChannel)
        self.assertEqual(channel.name, 'feishu')
        described = channel.describe()
        self.assertEqual(sorted(described),
                         ['adapter', 'conditions', 'config_ref', 'identity', 'version'])
        blob = json.dumps(described, ensure_ascii=False)
        for secret in FAKE_CREDENTIALS.values():
            self.assertNotIn(secret, blob)
        self.assertEqual(described['conditions']['allowlist_fingerprint'],
                         make_config(tmp).allowlist_fingerprint)
        self.assertTrue(described['conditions']['mention_required'])


# ══════════════════════════ 发送 ══════════════════════════

class SendTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(make_tmp('feishu_send_'))
        (self.tmp / 'web').mkdir()
        self.page = self.tmp / 'web' / 'approval_E1.1.html'
        self.page.write_bytes('<html>审批页</html>'.encode('utf-8'))
        self.clock = Clock()

    def channel(self, **overrides):
        return make_channel(self.tmp, clock=self.clock, **overrides)

    def test_disabled_channel_never_touches_transport(self):
        channel, transport = self.channel(send_enabled=False)
        with self.assertRaises(NotSent):
            channel.send_file(page_payload(artifact=str(self.page)), b'<html>x</html>')
        self.assertEqual(transport.calls, [])

    def test_token_failure_is_definite_not_sent(self):
        channel, transport = self.channel()
        transport.token_response = (400, {'code': 99991663, 'msg': 'invalid app_secret'})
        with self.assertRaises(NotSent):
            channel.send_file(page_payload(artifact=str(self.page)), self.page.read_bytes())
        self.assertEqual(transport.message_posts, [])
        self.assertEqual(transport.posts('/im/v1/files'), [])
        self.assertIsNone(channel.query('hil:page-1'))

    def test_upload_ok_but_message_refused_is_not_sent(self):
        channel, transport = self.channel()
        transport.send_response = (400, {'code': 230002, 'msg': 'bot not in chat'})
        with self.assertRaises(NotSent):
            channel.send_file(page_payload(artifact=str(self.page)), self.page.read_bytes())
        self.assertEqual(len(transport.posts('/im/v1/files')), 1)
        self.assertEqual(len(transport.message_posts), 1)
        self.assertIsNone(channel.query('hil:page-1'))
        self.assertEqual(channel._ledger.by_request_key('hil:page-1')['state'], 'not_sent')
        # 确定没发出去 ⇒ 允许再试一次（NotSent 的语义）
        with self.assertRaises(NotSent):
            channel.send_file(page_payload(artifact=str(self.page)), self.page.read_bytes())
        self.assertEqual(len(transport.message_posts), 2)

    def test_timeout_is_ambiguous_and_never_resent(self):
        channel, transport = self.channel()
        transport.send_error = TimeoutError('read timed out')
        with self.assertRaises(AmbiguousSend):
            channel.send_file(page_payload(artifact=str(self.page)), self.page.read_bytes())
        self.assertEqual(len(transport.message_posts), 1)
        self.assertEqual(channel._ledger.by_request_key('hil:page-1')['state'], 'unknown')
        self.assertIsNone(channel.query('hil:page-1'))
        # 同一个 request_key 再来：⛔ 不再 POST，直接说不确定
        with self.assertRaises(AmbiguousSend):
            channel.send_file(page_payload(artifact=str(self.page)), self.page.read_bytes())
        self.assertEqual(len(transport.message_posts), 1)

    def test_server_error_is_ambiguous_not_retryable(self):
        channel, transport = self.channel()
        transport.send_response = (502, {'code': 500, 'msg': 'bad gateway'})
        with self.assertRaises(AmbiguousSend):
            channel.send_file(page_payload(artifact=str(self.page)), self.page.read_bytes())
        self.assertEqual(channel._ledger.by_request_key('hil:page-1')['state'], 'unknown')

    def test_confirmed_send_is_idempotent(self):
        channel, transport = self.channel()
        first = channel.send_file(page_payload(artifact=str(self.page)),
                                 self.page.read_bytes())
        second = channel.send_file(page_payload(artifact=str(self.page)),
                                  self.page.read_bytes())
        self.assertEqual(first['message_id'], second['message_id'])
        self.assertEqual(len(transport.message_posts), 1)
        self.assertEqual(channel.query('hil:page-1')['message_id'], first['message_id'])
        self.assertEqual(first['sha256'], hashlib.sha256(self.page.read_bytes()).hexdigest())
        self.assertEqual(first['path'], str(self.page))

    def test_recipient_outside_allowlist_is_refused_before_network(self):
        channel, transport = self.channel()
        with self.assertRaises(NotSent):
            channel.send_file(page_payload(artifact=str(self.page), recipient='oc_other'),
                              self.page.read_bytes())
        self.assertEqual(transport.calls, [])

    def test_card_requires_explicit_wire_fields(self):
        channel, transport = self.channel()
        for missing in ('short_code', 'card_options', 'card_action_template'):
            payload = card_payload()
            payload.pop(missing)
            with self.assertRaises(NotSent) as caught:
                channel.send_card(payload)
            self.assertIn(missing, str(caught.exception))
        self.assertEqual(transport.message_posts, [])
        self.assertEqual(transport.calls, [])

    def test_card_template_mismatch_is_refused(self):
        channel, _ = self.channel()
        payload = card_payload()
        payload['card_action_template'] = dict(payload['card_action_template'],
                                               exp_id='exp-99')
        with self.assertRaises(NotSent):
            channel.send_card(payload)

    def test_card_buttons_carry_template_and_single_option(self):
        channel, transport = self.channel()
        raw = channel.send_card(card_payload())
        self.assertEqual(raw['kind'], 'card')
        self.assertEqual(raw['short_code'], SHORT_CODE)
        self.assertEqual(raw['decision_id'], 'E1.1')
        self.assertEqual(len(transport.sent), 1)
        sent = transport.sent[0]
        self.assertEqual(sent['msg_type'], 'interactive')
        actions = [element for element in sent['content']['elements']
                   if element.get('tag') == 'action'][0]['actions']
        self.assertEqual(len(actions), 3)          # 批准 A / 否决 A / 全否
        approve = actions[0]['value']
        self.assertEqual(approve['intent'], 'approve')
        self.assertEqual(approve['options'], ['A'])
        self.assertEqual(approve['channel'], 'feishu_channel')
        template = card_payload()['card_action_template']
        for key in ('exp_id', 'decision_id', 'recipient', 'short_code'):
            self.assertEqual(approve[key], template[key])
        self.assertEqual(actions[1]['value']['intent'], 'reject')
        self.assertEqual(actions[1]['value']['options'], ['A'])
        self.assertEqual(actions[2]['value']['options'], [])
        self.assertTrue(channel.verify_receipt(to_receipt(raw)))

    def test_card_options_reject_duplicates_and_empty_ids(self):
        channel, _ = self.channel()
        with self.assertRaises(NotSent):
            channel.send_card(card_payload(options=(('A', 'A'), ('A', 'A'))))
        with self.assertRaises(NotSent):
            channel.send_card(card_payload(options=(('', 'x'),)))

    def test_reply_requires_persisted_inbound_message_mapping(self):
        channel, transport = self.channel()
        with self.assertRaises(NotSent):
            channel.send_text(reply_payload('ev-unknown'))
        self.assertEqual(transport.message_posts, [])


# ══════════════════════════ 回执核验 ══════════════════════════

class VerifyReceiptTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(make_tmp('feishu_verify_'))
        (self.tmp / 'web').mkdir()
        self.page = self.tmp / 'web' / 'approval_E1.1.html'
        self.page.write_bytes('<html>审批页</html>'.encode('utf-8'))
        self.clock = Clock()
        self.channel, self.transport = make_channel(self.tmp, clock=self.clock)
        self.raw = self.channel.send_file(page_payload(artifact=str(self.page)),
                                          self.page.read_bytes())

    def test_real_receipt_verifies(self):
        self.assertTrue(self.channel.verify_receipt(to_receipt(self.raw)))
        self.assertEqual(len(self.transport.posts('GET')), 0)
        gets = [call for call in self.transport.calls if call['method'] == 'GET']
        self.assertEqual(len(gets), 1)
        self.assertIn(self.raw['message_id'], gets[0]['path'])

    def test_forged_message_id_is_refused(self):
        forged = to_receipt(dict(self.raw, message_id='om_forged'))
        self.assertFalse(self.channel.verify_receipt(forged))

    def test_receipt_hash_mismatch_is_refused(self):
        forged = to_receipt(dict(self.raw, payload_hash='d' * 64))
        self.assertFalse(self.channel.verify_receipt(forged))

    def test_get_content_mismatch_is_refused(self):
        def wrong(message_id):
            return 200, {'code': 0, 'data': {'items': [{
                'message_id': message_id, 'chat_id': RECIPIENT, 'msg_type': 'file',
                'deleted': False, 'sender': {'sender_type': 'app'},
                'body': {'content': json.dumps({'file_key': 'file_from_elsewhere'})}}]}}
        self.transport.get_override = wrong
        self.assertFalse(self.channel.verify_receipt(to_receipt(self.raw)))

    def test_get_chat_mismatch_is_refused(self):
        def wrong_chat(message_id):
            return 200, {'code': 0, 'data': {'items': [{
                'message_id': message_id, 'chat_id': 'oc_someone_else', 'msg_type': 'file',
                'deleted': False, 'sender': {'sender_type': 'app'},
                'body': {'content': json.dumps({'file_key': 'file_key_1'})}}]}}
        self.transport.get_override = wrong_chat
        self.assertFalse(self.channel.verify_receipt(to_receipt(self.raw)))

    def test_deleted_message_is_refused(self):
        def deleted(message_id):
            return 200, {'code': 0, 'data': {'items': [{
                'message_id': message_id, 'chat_id': RECIPIENT, 'msg_type': 'file',
                'deleted': True, 'sender': {'sender_type': 'app'},
                'body': {'content': json.dumps({'file_key': 'file_key_1'})}}]}}
        self.transport.get_override = deleted
        self.assertFalse(self.channel.verify_receipt(to_receipt(self.raw)))

    def test_get_error_is_fail_closed(self):
        def boom(message_id):
            raise TimeoutError('read timeout')
        self.transport.get_override = boom
        self.assertFalse(self.channel.verify_receipt(to_receipt(self.raw)))


# ══════════════════════════ webhook：验签 / 解析 / 规范化 ══════════════════════════

class WebhookTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(make_tmp('feishu_hook_'))
        self.clock = Clock()

    def channel(self, **overrides):
        return make_channel(self.tmp, clock=self.clock, **overrides)

    def test_signature_is_checked_before_parsing(self):
        channel, _ = self.channel()
        headers, _ = signed(message_event(), clock=self.clock)
        # 签名算的是另一份 body，实际 body 连 JSON 都不是 ⇒ 必须报签名问题，不是解析问题
        result = channel.handle_webhook(headers, b'{ this is not json')
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'signature_mismatch')
        self.assertIsNone(result['event'])
        self.assertEqual(channel.poll(None)['events'], [])

    def test_valid_signature_but_broken_json_is_malformed(self):
        channel, _ = self.channel()
        raw = b'{not json'
        stamp, nonce = str(int(self.clock())), 'nonce-1'
        headers = {'X-Lark-Request-Timestamp': stamp, 'X-Lark-Request-Nonce': nonce,
                   'X-Lark-Signature': compute_signature(stamp, nonce,
                                                         FAKE_CREDENTIALS['FEISHU_ENCRYPT_KEY'],
                                                         raw)}
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'malformed_body')

    def test_missing_signature_headers_are_refused(self):
        channel, _ = self.channel()
        result = channel.handle_webhook({}, json.dumps(message_event()).encode('utf-8'))
        self.assertEqual(result['reason'], 'signature_missing')

    def test_timestamp_skew_is_refused(self):
        channel, _ = self.channel()
        headers, raw = signed(message_event(), clock=self.clock,
                              timestamp=int(self.clock()) - 400)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'timestamp_skew')
        self.assertGreater(result['verification']['skew_seconds'], 300)

    def test_replay_is_refused(self):
        channel, _ = self.channel()
        headers, raw = signed(message_event(), clock=self.clock)
        first = channel.handle_webhook(headers, raw)
        self.assertTrue(first['ok'], first)
        second = channel.handle_webhook(headers, raw)
        self.assertFalse(second['ok'])
        self.assertEqual(second['reason'], 'replay')
        self.assertEqual(len(channel.poll(None)['events']), 1)

    def test_encrypted_body_without_decrypt_backend_fails_closed(self):
        """缺可选解密依赖时：501 明说缺什么，⛔ 不降级成明文解析。"""
        channel, _ = self.channel()
        headers, raw = signed({'encrypt': 'base64-ciphertext'}, clock=self.clock)
        unavailable = feishu.channel_crypto.DecryptUnavailable(
            '没有可用的 AES 解密后端（python -m pip install cryptography）')
        with mock.patch.object(feishu.channel_crypto, 'decrypt_event_payload',
                               side_effect=unavailable):
            result = channel.handle_webhook(headers, raw)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'decryption_unavailable')
        self.assertEqual(result['status'], 501)
        self.assertIn('cryptography', result['detail'])
        self.assertTrue(result['verification']['encrypted'])
        self.assertFalse(result['verification']['decrypted'])
        self.assertEqual(channel.poll(None)['events'], [])

    def test_non_finite_request_timestamp_is_refused(self):
        """NaN / Infinity 会让「时差 > 上限」恒为 False ⇒ 必须在算签名前就拒。"""
        channel, _ = self.channel()
        for stamp in ('nan', 'inf', '-inf', '0'):
            headers, raw = signed(message_event(), clock=self.clock, timestamp=stamp,
                                  nonce=f'nonce-{stamp}')
            result = channel.handle_webhook(headers, raw)
            self.assertEqual(result['status'], 401, (stamp, result))
            self.assertEqual(result['reason'], 'signature_bad_timestamp', (stamp, result))
        self.assertEqual(channel.poll(None)['events'], [])

    def test_url_verification_returns_challenge(self):
        channel, _ = self.channel()
        body = {'challenge': 'ajls384kdj1234', 'type': 'url_verification',
                'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']}
        headers, raw = signed(body, clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'])
        self.assertEqual(result['kind'], 'url_verification')
        self.assertEqual(result['response'], {'challenge': 'ajls384kdj1234'})

    def test_url_verification_with_wrong_token_is_refused(self):
        channel, _ = self.channel()
        body = {'challenge': 'x', 'type': 'url_verification', 'token': 'not-the-token'}
        headers, raw = signed(body, clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'token_mismatch')

    def test_sender_outside_allowlist_is_refused(self):
        channel, _ = self.channel()
        headers, raw = signed(message_event(open_id=MALLORY), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'sender_not_allowed')
        self.assertEqual(result['status'], 403)
        self.assertEqual(channel.poll(None)['events'], [])

    def test_unbound_chat_is_refused(self):
        channel, _ = self.channel(chat_bindings={})
        headers, raw = signed(message_event(), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'unbound_chat')
        self.assertEqual(channel.poll(None)['events'], [])

    def test_bot_echo_is_ignored_not_applied(self):
        channel, _ = self.channel()
        headers, raw = signed(message_event(sender_type='bot'), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'])
        self.assertEqual(result['kind'], 'ignored')
        self.assertEqual(result['reason'], 'bot_echo')

    def test_message_event_is_normalized_for_ingest(self):
        channel, _ = self.channel()
        stamp = 1730000000000
        headers, raw = signed(message_event(create_time_ms=stamp), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'], result)
        event = result['event']
        self.assertEqual(event['event_id'], 'ev-msg-1')
        self.assertEqual(event['exp_id'], EXP)
        self.assertEqual(event['recipient'], RECIPIENT)
        self.assertEqual(event['principal'], ALICE)
        self.assertEqual(event['create_time'], float(stamp))
        self.assertEqual(event['intent'], 'question')
        self.assertIn('进度', event['body'])
        self.assertEqual(event['binding']['how'], 'chat_binding')
        self.assertTrue(event['mentioned_bot'])
        self.assertTrue(event['conditions']['mention_required'])
        self.assertEqual(channel.authenticate(feishu.SOURCE, event), ALICE)
        # 同一条事件再送一次（新签名）：按幂等返回，不重复入账
        headers2, raw2 = signed(message_event(create_time_ms=stamp), clock=self.clock,
                                nonce='nonce-2')
        again = channel.handle_webhook(headers2, raw2)
        self.assertTrue(again['ok'])
        self.assertTrue(again['duplicate'])

    def test_authenticate_rejects_forged_and_tampered_events(self):
        channel, _ = self.channel()
        forged = message_event()['event'] | {'event_id': 'ev-forged', 'exp_id': EXP}
        with self.assertRaises(NotAuthenticated):
            channel.authenticate(feishu.SOURCE, forged)
        headers, raw = signed(message_event(), clock=self.clock)
        event = channel.handle_webhook(headers, raw)['event']
        tampered = dict(event, body='换过的正文')
        with self.assertRaises(NotAuthenticated):
            channel.authenticate(feishu.SOURCE, tampered)
        with self.assertRaises(NotAuthenticated):
            channel.authenticate('other-source', event)

    def test_card_click_is_normalized_with_card_action(self):
        channel, _ = self.channel()
        raw_card = channel.send_card(card_payload())
        headers, raw = signed(card_action_event(open_message_id=raw_card['message_id']),
                              clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'], result)
        event = result['event']
        self.assertEqual(event['decision_id'], 'E1.1')
        self.assertEqual(event['short_code'], SHORT_CODE)
        self.assertEqual(event['exp_id'], EXP)
        self.assertEqual(event['recipient'], RECIPIENT)
        self.assertEqual(event['principal'], ALICE)
        self.assertEqual(event['intent'], 'approve')
        self.assertEqual(event['options'], ['A'])
        self.assertEqual(event['card_action']['tag'], 'button')
        self.assertEqual(event['card_action']['value']['channel'], 'feishu_channel')
        self.assertNotIn('body', event)                       # 卡点击不是文本
        self.assertEqual(result['response']['toast']['type'], 'success')
        self.assertEqual(channel.authenticate(feishu.SOURCE, event), ALICE)

    def test_card_click_for_unsent_card_is_refused(self):
        channel, _ = self.channel()
        headers, raw = signed(card_action_event(decision_id='E9.9'), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'card_not_sent_by_adapter')
        self.assertEqual(channel.poll(None)['events'], [])

    def test_card_click_with_short_code_mismatch_is_refused(self):
        channel, _ = self.channel()
        channel.send_card(card_payload())
        headers, raw = signed(card_action_event(short_code='ZZZZ'), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'card_short_code_mismatch')

    def test_card_click_for_option_not_on_card_is_refused(self):
        channel, _ = self.channel()
        channel.send_card(card_payload())
        headers, raw = signed(card_action_event(options=('Z',)), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'card_option_not_in_card')

    def test_card_click_from_other_sender_is_refused(self):
        channel, _ = self.channel()
        channel.send_card(card_payload())
        headers, raw = signed(card_action_event(open_id=MALLORY), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'sender_not_allowed')

    def test_unconfigured_encrypt_key_fails_closed(self):
        channel, _ = self.channel(encrypt_key_ref='')
        headers, raw = signed(message_event(), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'signature_unconfigured')
        self.assertFalse(channel.health()['ok'])

    def test_group_message_without_mention_is_recorded_in_conditions(self):
        """群 @ 是平台触达条件：适配层不假装满足，只如实记录。"""
        channel, _ = self.channel()
        headers, raw = signed(message_event(mentions_bot=False), clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'], result)
        event = result['event']
        self.assertFalse(event['mentioned_bot'])
        self.assertFalse(event['conditions']['mention_required_satisfied'])
        self.assertTrue(event['conditions']['mention_required'])
        health = channel.health()
        self.assertTrue(health['mention_required'])
        self.assertTrue(health['detail']['mention_required'])
        self.assertTrue(channel.describe()['conditions']['mention_required'])

    def test_unknown_event_type_is_ignored_not_rejected(self):
        channel, _ = self.channel()
        body = {'schema': '2.0',
                'header': {'event_id': 'ev-x', 'event_type': 'im.chat.updated_v1',
                           'create_time': str(int(self.clock() * 1000)),
                           'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']},
                'event': {}}
        headers, raw = signed(body, clock=self.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'])
        self.assertEqual(result['kind'], 'ignored')


class ProtocolTests(unittest.TestCase):
    """结构验收：适配层要真的满足核心的 `ApprovalChannel` 协议。"""

    def test_channel_satisfies_approval_channel_protocol(self):
        from kernel.autoresearch.decisions import ApprovalChannel
        tmp = make_tmp('feishu_proto_')
        channel, _ = make_channel(tmp)
        self.assertIsInstance(channel, ApprovalChannel)
        for name in ('describe', 'health', 'send_file', 'send_card', 'send_text',
                     'query', 'verify_receipt', 'authenticate', 'poll', 'subscribe'):
            self.assertTrue(callable(getattr(channel, name)), name)

    def test_poll_and_subscribe_contract(self):
        tmp = make_tmp('feishu_proto_')
        clock = Clock()
        channel, _ = make_channel(tmp, clock=clock)
        self.assertEqual(channel.poll(None, 10), {'cursor': None, 'events': []})
        headers, raw = signed(message_event(), clock=clock)
        event = channel.handle_webhook(headers, raw)['event']
        pulled = channel.poll(None, 10)
        self.assertEqual(pulled['events'], [event])
        self.assertIsNotNone(pulled['cursor'])
        self.assertEqual(channel.poll(pulled['cursor'], 10)['events'], [])
        seen = []
        handle = channel.subscribe(seen.append)
        handle.push(event)
        self.assertEqual(seen, [event])
        handle.close()
        self.assertTrue(handle.closed)
        with self.assertRaises(feishu.FeishuError):
            handle.push(event)


# ══════════════════════════ webhook 服务：只给 factory ══════════════════════════

class HttpHandlerTests(unittest.TestCase):
    """本模块**不起服务**：只验证 factory 造出的 handler 与状态码映射。"""

    def test_factory_returns_handler_class_without_binding(self):
        import http.server
        tmp = make_tmp('feishu_http_')
        channel, transport = make_channel(tmp)
        handler = feishu.make_http_handler(channel)
        self.assertTrue(issubclass(handler, http.server.BaseHTTPRequestHandler))
        self.assertTrue(callable(handler.do_POST))
        self.assertIsNot(handler, feishu.make_http_handler(channel))   # 每次一个新类
        self.assertEqual(transport.calls, [])                          # 造 handler 不发请求

    def test_status_mapping_is_taken_from_handle_webhook(self):
        self.assertEqual(feishu._http_response({'status': 501,
                                                'response': {'ok': False}}),
                         (501, {'ok': False}))
        self.assertEqual(feishu._http_response({'status': 200,
                                                'response': {'challenge': 'x'}}),
                         (200, {'challenge': 'x'}))
        self.assertEqual(feishu._http_response(None)[0], 500)


# ══════════════════════════ 与真实决策链联调（全离线）══════════════════════════

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
            'expected_effect': {'quantity': 'pass', 'unit': '题',
                                'predicted_delta': 2, 'scale_resolution': 1,
                                'scale_resolution_source': '自定',
                                'scale_resolution_why': '同一份提交包重复跑三次量出的跨度'}}


def plan_set():
    return {'exp_id': EXP, 'goal': '提高线上分数', 'scope': 'large',
            'question_family': 'Q1', 'from_baseline': 'base-01',
            'changed_faces': ['3'], 'version': 'v1', 'recommended': 'A',
            'options': [option('A'), option('B')],
            'brief': {'background': '背景：这一轮为什么存在',
                      'context': '上下文：和上一轮的关系',
                      'progress': '进展：现在做到哪了'},
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


class IntegrationTests(unittest.TestCase):
    """真 `DecisionService` + 真适配层 + 假传输：页 → 回执 → 入向 → 回复。"""

    def setUp(self):
        self.tmp = Path(make_tmp('feishu_e2e_'))
        self.web = self.tmp / 'web'
        self.web.mkdir()
        self.clock = Clock()
        self.store = Store(self.tmp / 'runtime.sqlite3')
        self.channel, self.transport = make_channel(self.tmp, clock=self.clock)
        self.service = DecisionService(self.store, channel=self.channel, clock=self.clock)

    def prepare(self):
        return self.service.prepare(exp_id=EXP, decision=decision(), plan_set=plan_set(),
                                    page_dir=str(self.web))

    def test_page_delivery_end_to_end(self):
        self.prepare()
        result = self.service.send_page('E1.1')
        self.assertTrue(result['ok'], result)
        receipt = result['receipt']
        self.assertTrue(receipt['path'].replace('\\', '/').endswith('/web/approval_E1.1.html'))
        self.assertEqual(receipt['bytes'], len(self.web.joinpath('approval_E1.1.html').read_bytes()))
        chain = self.service.chain('E1.1')
        self.assertTrue(chain['page']['ok'], chain['page']['problems'])
        self.assertEqual(len(self.transport.message_posts), 1)
        sent = self.transport.sent[0]
        self.assertEqual(sent['msg_type'], 'file')
        self.assertEqual(sent['content'], {'file_key': 'file_key_1'})
        self.assertEqual(sent['receive_id'], RECIPIENT)

    def test_text_reply_quotes_inbound_message(self):
        self.prepare()
        self.assertTrue(self.service.send_page('E1.1')['ok'])
        headers, raw = signed(message_event(), clock=self.clock)
        hook = self.channel.handle_webhook(headers, raw)
        self.assertTrue(hook['ok'], hook)
        row = self.service.ingest(feishu.SOURCE, hook['event'])
        self.assertEqual(row['status'], 'applied')
        claim = self.service.claim_reply('worker-1')
        self.assertIsNotNone(claim)
        task = self.service.answer(claim, f'{EXP} 的答复：进度正常')
        delivered = self.service.deliver_reply(task['id'], worker='worker-1')
        self.assertTrue(delivered['ok'], delivered)
        self.assertEqual(len(self.transport.message_posts), 2)      # 页 + 回复
        reply = self.transport.sent[1]
        self.assertEqual(reply['msg_type'], 'text')
        self.assertEqual(reply['reply_to'], 'om_in_1')              # 引用入向那条消息
        self.assertIn('进度正常', reply['content']['text'])
        self.assertEqual(self.service.reply_status(exp_id=EXP)['overdue'], [])

    def test_reply_without_inbound_mapping_is_refused(self):
        channel, transport = make_channel(self.tmp / 'other', clock=self.clock)
        with self.assertRaises(NotSent):
            channel.send_text(reply_payload('ev-msg-1'))
        self.assertEqual(transport.message_posts, [])

    def test_card_click_routes_to_card_path_not_text_reply(self):
        self.prepare()
        self.assertTrue(self.service.send_page('E1.1')['ok'])
        raw_card = self.channel.send_card(card_payload())
        headers, raw = signed(card_action_event(open_message_id=raw_card['message_id']),
                              clock=self.clock)
        hook = self.channel.handle_webhook(headers, raw)
        self.assertTrue(hook['ok'], hook)
        row = self.service.ingest(feishu.SOURCE, hook['event'])
        # 审批链缺卡的成功回执 ⇒ 点击被拒（不放行），但**没有**变成待回文本
        self.assertEqual(row['status'], 'rejected')
        self.assertEqual(row['category'], 'card_click')
        events = self.service.inbox.pending_questions()
        self.assertEqual([item for item in events if item['event_id'] == 'ev-card-1'], [])
        status = self.service.reply_status(exp_id=EXP)
        self.assertEqual(status['text_events'], 0)
        self.assertEqual(status['card_events'], 1)

    def test_card_send_refused_while_core_lacks_wire_fields(self):
        """适配层自己的闸：没有显式 wire 字段就不发卡（核心现已补上，所以这里直接打适配层）。"""
        self.prepare()
        self.assertTrue(self.service.send_page('E1.1')['ok'])
        bare = card_payload()
        for key in ('short_code', 'card_options', 'card_action_template'):
            bare.pop(key)
        with self.assertRaises(NotSent):
            self.channel.send_card(bare)

    def test_page_then_card_then_click_resolves_decision(self):
        """整条链：页 → 卡 → 点按钮 → 裁决。全部离线，传输是假的。"""
        self.prepare()
        page = self.service.send_page('E1.1')
        self.assertTrue(page['ok'], page)
        card = self.service.send_card('E1.1')
        self.assertTrue(card['ok'], card)
        self.assertTrue(self.service.chain('E1.1')['complete'])
        self.assertIn('short_code', card['receipt'])
        headers, raw = signed(card_action_event(
            open_message_id=card['receipt']['message_id'],
            short_code=card['receipt']['short_code']), clock=self.clock)
        hook = self.channel.handle_webhook(headers, raw)
        self.assertTrue(hook['ok'], hook)
        row = self.service.ingest(feishu.SOURCE, hook['event'])
        self.assertEqual(row['status'], 'applied', row)
        self.assertEqual(row['category'], 'card_click')
        status = self.service.status('E1.1')
        self.assertEqual(status['state'], 'resolved')
        self.assertEqual(status['resolution'], 'user_approved')
        # 卡点击不是文本回复：没有待回任务，也没有进文字回复窗口
        self.assertEqual(self.service.inbox.pending_questions(), [])
        self.assertEqual(self.service.reply_status(exp_id=EXP)['text_events'], 0)
        self.assertEqual(self.transport.sent[-1]['msg_type'], 'interactive')


if __name__ == '__main__':
    (ROOT / 'temp').mkdir(exist_ok=True)
    unittest.main(verbosity=2)
