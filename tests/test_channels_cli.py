# GENERATED-BY: 通道 CLI 离线验收, 2026-11-10 +08:00
# LAST-EDIT: 2026-11-10 +08:00
# PURPOSE: 验 --channel-config 装载、doctor 不泄密、listen 回环收件、只 ingest 已认证事件、poll 一次拉取
# ORIGIN: kernel/autoresearch/channels_cli.py + kernel/autoresearch/decisions_cli.py
# CUMULATIVE-DELTA: 首版。全部离线：假凭据字典、假传输、回环 HTTP；不发任何真实消息、不连外网。
"""通道 CLI 的离线验收。

⛔ 这里**没有**真实凭据、没有对外网络调用、没有真实租户：
`transport` 全是「一被调用就报错」的假实现，凭据是测试进程里的假值，
`listen` 的收件路径只在 `127.0.0.1` 上用系统分配的端口跑一次真实 HTTP 往返。

因此这些用例证明的是 **CLI 与适配层的接线行为**，不是平台行为 ——
真实租户上的送达、权限、群 @ 条件、事件加密与常驻守护仍是未验边界。

临时目录跑完就删（`temp/channels_cli_*`）；要留着排查就设 `AUTORESEARCH_KEEP_TMP=1`。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from kernel.autoresearch import channels_cli, decisions_cli
from kernel.autoresearch.channels import feishu
from kernel.autoresearch.channels.feishu import FeishuChannel, compute_signature
from kernel.autoresearch.decisions import ChannelUnavailable, DecisionService
from kernel.autoresearch.runtime.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXP = 'exp-01'
RECIPIENT = 'oc_test_chat'
ALICE = 'ou_alice'
MALLORY = 'ou_mallory'
SHORT_CODE = 'K7Q2'

#: 本次进程建过的临时目录（`tearDownModule` 统一清掉）。
_CREATED = []
_KEEP_TMP = bool(os.environ.get('AUTORESEARCH_KEEP_TMP'))


def make_tmp(prefix='channels_cli_case_'):
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


# ────────────────────────────── 假件：凭据 / 时钟 / 传输 ──────────────────────────────

#: 假凭据：只在测试进程里，⛔ 不是任何真实应用的值。
FAKE_CREDENTIALS = {'FEISHU_APP_ID': 'cli_test_only',
                    'FEISHU_APP_SECRET': 'secret_test_only',
                    'FEISHU_VERIFICATION_TOKEN': 'vt_test_only',
                    'FEISHU_ENCRYPT_KEY': 'ek_test_only'}


def fake_credential_source(name):
    return FAKE_CREDENTIALS.get(name)


def bomb_transport(method, url, body, headers, timeout):
    """一被调用就失败：任何真实发送都是本文件里的 bug。"""
    raise AssertionError(f'测试不允许发出网络请求：{method} {url}')


class Clock:
    def __init__(self, now=None):
        self.now = float(now if now is not None else time.time())

    def __call__(self):
        return self.now


# ────────────────────────────── 配置夹具 ──────────────────────────────

def config_mapping(tmp, **overrides):
    values = dict(app_id_ref='env:FEISHU_APP_ID', app_secret_ref='env:FEISHU_APP_SECRET',
                  verification_token_ref='env:FEISHU_VERIFICATION_TOKEN',
                  encrypt_key_ref='env:FEISHU_ENCRYPT_KEY', send_enabled=True,
                  receive_id_type='chat_id', allowed_recipients=[RECIPIENT],
                  allowed_sender_open_ids=[ALICE],
                  chat_bindings={RECIPIENT: EXP},
                  ledger_path=str(Path(tmp) / 'ledger.sqlite3'))
    values.update(overrides)
    return values


def config_json(tmp, **overrides):
    return json.dumps(config_mapping(tmp, **overrides), ensure_ascii=False)


def make_channel(tmp, **overrides):
    """直接构造适配层（用来预置台账，不经过 CLI）。"""
    return FeishuChannel(feishu.from_mapping(config_mapping(tmp, **overrides)),
                         transport=bomb_transport, credential_source=fake_credential_source,
                         clock=Clock())


def run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = channels_cli.main(argv)
    return code, out.getvalue()


def run_decisions(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = decisions_cli.main(argv)
    return code, out.getvalue()


# ────────────────────────────── 事件夹具 ──────────────────────────────

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
                      intent='approve', options=('A',), open_id=ALICE):
    value = {'channel': 'feishu_channel', 'intent': intent, 'exp_id': EXP,
             'decision_id': decision_id, 'recipient': RECIPIENT,
             'short_code': short_code, 'options': list(options)}
    return {'schema': '2.0',
            'header': {'event_id': event_id, 'event_type': 'card.action.trigger',
                       'create_time': str(int(time.time() * 1e6)),
                       'token': FAKE_CREDENTIALS['FEISHU_VERIFICATION_TOKEN']},
            'event': {'operator': {'open_id': open_id},
                      'action': {'tag': 'button', 'name': 'Button_x', 'value': value},
                      'context': {'open_chat_id': RECIPIENT}}}


# ────────────────────────────── 审批页方案夹具（照 test_feishu 的最小可用形状）──────────────────────────────

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
                      'progress': '进展：现在做到哪'},
            'reader_view': {
                'title': '要不要跑这一批', 'summary': '这一轮要你决定跑哪一条',
                'sections': [{'title': '为什么值得做', 'body': '因为上一轮留下了矛盾'}],
                'arms': [{'name': 'A 臂', 'settings': '设置 A', 'purpose': '目的 A',
                          'comparison': '对基线', 'option_ids': ['A']}],
                'choices': [{'option_id': 'A', 'summary': '做 A', 'tradeoff': '花 1.5 GPU 小时'},
                            {'option_id': 'B', 'summary': '做 B', 'tradeoff': '更贵'}],
                'budget': 'GPU 1.5 h · 墙钟 2 h · 10 题', 'risks': ['可能白跑']}}


def prepare_decision(db_path, tmp):
    """用 decisions_cli 建一条待批决策（零通道配置也能建页）。"""
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


# ────────────────────────────── 回环 HTTP ──────────────────────────────

def start_server(channel):
    server = feishu.serve(channel, host='127.0.0.1', port=0, block=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1], thread


def stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def post(port, path, headers, raw):
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


# ══════════════════════════ --channel-config（decisions_cli）══════════════════════════

class ChannelConfigTests(unittest.TestCase):
    """`--channel-config` 直接构造内核 FeishuChannel；`--channel` 工厂保留且互斥。"""

    def setUp(self):
        self.tmp = Path(make_tmp('channels_cli_cfg_'))

    def load(self, config_spec, **overrides):
        return decisions_cli.load_channel_config(
            config_spec, transport=overrides.get('transport', bomb_transport),
            credential_source=fake_credential_source, clock=Clock())

    def test_channel_config_builds_kernel_channel_and_only_opens_ledger(self):
        channel = self.load(config_json(self.tmp, send_enabled=False))
        self.assertIsInstance(channel, FeishuChannel)
        self.assertEqual(channel.name, 'feishu')
        self.assertEqual(channel.config.ledger_path, str(self.tmp / 'ledger.sqlite3'))
        self.assertTrue((self.tmp / 'ledger.sqlite3').exists())     # 只建本地台账
        self.assertEqual(channel.describe()['adapter'], 'feishu')
        self.assertFalse(channel.health()['ok'])                    # 默认禁发 ⇒ 不可用
        blob = json.dumps(channel.describe(), ensure_ascii=False)
        for secret in FAKE_CREDENTIALS.values():
            self.assertNotIn(secret, blob)

    def test_channel_config_accepts_inline_path_and_at_prefix(self):
        path = self.tmp / 'channel.json'
        path.write_text(config_json(self.tmp), encoding='utf-8')
        inline = self.load(config_json(self.tmp))
        by_path = self.load(str(path))
        by_at = self.load('@' + str(path))
        self.assertEqual(inline.config, by_path.config)
        self.assertEqual(inline.config, by_at.config)

    def test_literal_credentials_are_refused_and_never_echoed(self):
        secret = 'cli_literal_secret_value_001'
        with self.assertRaises(ChannelUnavailable) as caught:
            self.load(json.dumps({'app_secret_ref': secret}))
        text = str(caught.exception)
        self.assertNotIn(secret, text)
        self.assertIn('app_secret_ref', text)
        self.assertIn('env:NAME', text)
        # 显式的凭据值键：拒绝，但只回显键名
        with self.assertRaises(ChannelUnavailable) as caught:
            self.load(json.dumps({'app_secret': secret, 'app_id_ref': 'env:FEISHU_APP_ID'}))
        text = str(caught.exception)
        self.assertNotIn(secret, text)
        self.assertIn('app_secret', text)

    def test_config_must_be_a_json_object(self):
        with self.assertRaises(ChannelUnavailable):
            self.load('["not", "an", "object"]')
        with self.assertRaises(ChannelUnavailable):
            self.load('{not json')
        with self.assertRaises(ChannelUnavailable):
            self.load('@' + str(self.tmp / 'missing.json'))

    def test_channel_and_channel_config_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as caught:
            decisions_cli.build_parser().parse_args(
                ['--db', 'x.sqlite3', '--channel', 'a:b', '--channel-config', '{}', 'status'])
        self.assertEqual(caught.exception.code, 2)
        # 就算绕过 argparse，programmatic 入口也拒绝
        with self.assertRaises(ChannelUnavailable):
            decisions_cli.select_channel(argparse.Namespace(channel='a:b', channel_config='{}'))

    def test_prepare_with_channel_config_then_send_still_fails_closed(self):
        db = self.tmp / 'runtime.sqlite3'
        code, text = prepare_decision(db, self.tmp)
        self.assertEqual(code, 0, text)
        view = json.loads(text)
        self.assertTrue(Path(view['page_path']).exists())
        out = io.StringIO()
        # 凭据取不到值 + 传输层一被调用就报错：两条独立的闸，任一都足以证明没有发出去
        with mock.patch.dict(os.environ, {name: '' for name in FAKE_CREDENTIALS}), \
                mock.patch.object(feishu, 'urllib_transport', bomb_transport):
            with contextlib.redirect_stdout(out):
                code = decisions_cli.main(['--db', str(db),
                                           '--channel-config', config_json(self.tmp),
                                           'send', '--decision', 'E1.1'])
        self.assertEqual(code, 2, out.getvalue())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['error'], 'ChannelUnavailable')
        self.assertIn('健康检查未通过', payload['message'])
        store = Store(db)
        conn = store.connect()
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM delivery').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT status FROM decisions').fetchone()[0],
                             'prepared')
        finally:
            conn.close()

    def test_channel_config_does_not_bypass_selector_when_absent(self):
        channel = decisions_cli.load_channel_config(None)
        self.assertIsNone(channel)
        self.assertIsNone(decisions_cli.load_channel_config('  '))


# ══════════════════════════ doctor ══════════════════════════

class DoctorTests(unittest.TestCase):
    """本地诊断：只读配置、不联网、不建台账、不打印凭据值。"""

    #: 假的解密后端能力：让 doctor 的结论不依赖本机是否装了可选 cryptography。
    BACKEND = {'name': 'cryptography', 'available': True, 'version': 'test',
               'algorithm': 'AES-256-CBC / key=SHA256(Encrypt Key) / PKCS7 / base64(iv+ciphertext)',
               'install': 'python -m pip install -r requirements-feishu.txt',
               'requirement': '', 'error': ''}

    def setUp(self):
        self.tmp = Path(make_tmp('channels_cli_doctor_'))
        self.env = mock.patch.dict(os.environ, FAKE_CREDENTIALS)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.backend = mock.patch.object(channels_cli.channel_crypto, 'backend_detail',
                                         side_effect=lambda: dict(self.BACKEND))
        self.backend.start()
        self.addCleanup(self.backend.stop)

    def snapshot(self):
        """目录快照：文件名 → sha256（用来证明 doctor 一个字节都没碰）。"""
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(self.tmp.iterdir()) if path.is_file()}

    def make_ledger(self):
        """测试自己先把台账建起来（doctor 不建），再往里写一行，确保有内容可比字节。"""
        channel = FeishuChannel(feishu.from_mapping(config_mapping(self.tmp)),
                                transport=bomb_transport, credential_source=fake_credential_source,
                                clock=Clock())
        channel._ledger.mark_seen('doctor-probe', now=1.0)
        return channel

    def test_ready_config_reports_readiness_and_changes_nothing_on_disk(self):
        self.make_ledger()
        before = self.snapshot()
        report = channels_cli.doctor_report(feishu.from_mapping(config_mapping(self.tmp)),
                                            credential_source=fake_credential_source)
        self.assertEqual(self.snapshot(), before)             # 台账字节与文件数都没变
        self.assertTrue(report['ok'], report['blocking'])
        self.assertTrue(report['inbound_ready'])
        self.assertTrue(report['send_ready'])
        self.assertTrue(report['ledger']['initialized'])
        self.assertLessEqual({'inbound', 'outbound', 'seen'}, set(report['ledger']['tables']))
        self.assertEqual(report['network_calls'], 0)
        self.assertEqual(report['files_written'], 0)
        self.assertFalse(report['secrets_exposed'])
        self.assertTrue(report['encryption']['available'])
        self.assertTrue(report['encryption']['platform_encrypts_when_configured'])
        self.assertIn('aes-256-cbc', report['webhook']['encrypted_events'])
        self.assertTrue(all(item['present'] for item in report['credentials']))
        blob = json.dumps(report, ensure_ascii=False)
        for secret in FAKE_CREDENTIALS.values():
            self.assertNotIn(secret, blob)

    def test_doctor_never_constructs_a_feishu_channel(self):
        """构造通道会开 / 建台账并切 WAL ⇒ doctor 不许构造；这里把构造函数打成炸弹。"""
        def bomb(self, *args, **kwargs):
            raise AssertionError('doctor 不许构造 FeishuChannel')

        with mock.patch.object(feishu.FeishuChannel, '__init__', bomb):
            code, text = run_cli(['--config', config_json(self.tmp), 'doctor'])
        self.assertEqual(code, 0, text)
        report = json.loads(text)
        self.assertTrue(report['ledger']['configured'])
        self.assertFalse(report['ledger']['exists'])
        self.assertFalse((self.tmp / 'ledger.sqlite3').exists())

    def test_missing_ledger_is_reported_uninitialized_and_not_created(self):
        before = self.snapshot()
        code, text = run_cli(['--config', config_json(self.tmp), 'doctor'])
        self.assertEqual(code, 0, text)
        self.assertEqual(self.snapshot(), before)             # 一个文件都没建
        report = json.loads(text)
        self.assertFalse(report['ledger']['exists'])
        self.assertFalse(report['ledger']['initialized'])
        self.assertIn('尚未初始化', report['ledger']['detail'])
        self.assertIn('ledger', report['warnings'])           # 未初始化是警告，不是配置错误
        self.assertNotIn('ledger', report['blocking'])
        # 建台账是 listen / poll 首次运行的事：真发的时候通道会自己建，所以 readiness 仍然成立
        self.assertTrue(report['send_ready'])

    def test_existing_ledger_is_inspected_without_creating_side_files(self):
        self.make_ledger()
        before = self.snapshot()
        self.assertFalse((self.tmp / 'ledger.sqlite3-wal').exists())
        report = channels_cli.doctor_report(feishu.from_mapping(config_mapping(self.tmp)),
                                            credential_source=fake_credential_source)
        after = self.snapshot()
        self.assertEqual(after, before)                       # 没有多出 -wal / -shm
        self.assertNotIn('ledger.sqlite3-wal', after)
        self.assertNotIn('ledger.sqlite3-shm', after)
        self.assertTrue(report['ledger']['readable'])
        self.assertTrue(report['ledger']['initialized'])

    def test_corrupt_ledger_is_a_blocker_not_a_repair(self):
        path = self.tmp / 'ledger.sqlite3'
        path.write_bytes(b'this is not a sqlite database')
        before = self.snapshot()
        code, text = run_cli(['--config', config_json(self.tmp), 'doctor'])
        self.assertEqual(code, 2, text)
        report = json.loads(text)
        self.assertIn('ledger', report['blocking'])
        self.assertTrue(report['ledger']['error'])
        self.assertEqual(self.snapshot(), before)             # 没有尝试修它

    def test_default_config_is_exit_2_and_names_the_blockers(self):
        code, text = run_cli(['doctor'])
        self.assertEqual(code, 2, text)
        report = json.loads(text)
        self.assertFalse(report['ok'])
        self.assertFalse(report['send_ready'])
        self.assertFalse(report['inbound_ready'])
        for identifier in ('inbound_verification', 'inbound_token', 'inbound_senders',
                           'outbound_recipients', 'ledger'):
            self.assertIn(identifier, report['blocking'])
        self.assertEqual(report['credentials'], [])

    def test_cli_output_has_no_credential_values_but_keeps_names(self):
        code, text = run_cli(['--config', config_json(self.tmp), 'doctor'])
        self.assertEqual(code, 0, text)
        for secret in FAKE_CREDENTIALS.values():
            self.assertNotIn(secret, text)
        self.assertIn('FEISHU_APP_ID', text)
        report = json.loads(text)
        self.assertFalse(report['secrets_exposed'])
        self.assertEqual(report['webhook']['unauthenticated_events'], '不进 ingest ⇒ 不产生批准')

    def test_missing_env_value_is_a_blocker(self):
        missing = 'FEISHU_APP_ID_MISSING_IN_TEST'
        os.environ.pop(missing, None)
        code, text = run_cli(['--config', config_json(self.tmp, app_id_ref='env:' + missing),
                              'doctor'])
        self.assertEqual(code, 2, text)
        report = json.loads(text)
        self.assertIn(f'credential_present:{missing}', report['blocking'])
        self.assertFalse(report['send_ready'])

    def test_report_that_leaks_a_credential_is_withheld(self):
        hide = FAKE_CREDENTIALS['FEISHU_APP_SECRET']
        mapping = config_mapping(self.tmp)
        mapping['ledger_path'] = str(self.tmp / f'{hide}.sqlite3')   # 值被当成普通文本写进配置
        report = channels_cli.doctor_report(feishu.from_mapping(mapping),
                                            credential_source=fake_credential_source)
        self.assertFalse(report['ok'])
        self.assertEqual(report['error'], 'secret_leak_detected')
        self.assertTrue(report['secrets_exposed'])
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(hide, blob)

    def test_doctor_does_not_create_a_ledger_when_none_is_configured(self):
        """台账是检查项：没配 ledger_path 就不建文件，缺它直接算 blocking。"""
        mapping = config_mapping(self.tmp)
        mapping.pop('ledger_path')
        code, text = run_cli(['--config', json.dumps(mapping), 'doctor'])
        self.assertEqual(code, 2, text)
        report = json.loads(text)
        self.assertIn('ledger', report['blocking'])
        self.assertFalse((self.tmp / 'ledger.sqlite3').exists())

    def test_missing_decryption_backend_is_a_blocker(self):
        """装了 Encrypt Key 就必须能解密（平台一定加密推送）；缺依赖要 blocking。"""
        self.BACKEND = dict(self.BACKEND, available=False, version='',
                            requirement='需要可选依赖 cryptography', error='ImportError')
        code, text = run_cli(['--config', config_json(self.tmp), 'doctor'])
        self.assertEqual(code, 2, text)
        report = json.loads(text)
        self.assertIn('encryption_backend', report['blocking'])
        self.assertFalse(report['encryption']['available'])
        self.assertIn('cryptography', json.dumps(report['checks'], ensure_ascii=False))
        # webhook 说明必须跟着后端能力走，不能停在「标准库无 AES」的旧口径
        encrypted = report['webhook']['encrypted_events']
        self.assertIn('cryptography', encrypted)
        self.assertIn('python -m pip', encrypted)
        self.assertNotIn('标准库无 AES', encrypted)

    def test_doctor_needs_a_feishu_config(self):
        with self.assertRaises(ChannelUnavailable):
            channels_cli.doctor_report(object())


# ══════════════════════════ decisions_cli 的只读命令 ══════════════════════════

class ReadonlyCommandTests(unittest.TestCase):
    """status / overdue / authorize-check：不装通道；内核有只读构造时优先用它。"""

    def setUp(self):
        self.tmp = Path(make_tmp('channels_cli_readonly_'))
        self.db = self.tmp / 'runtime.sqlite3'
        self.env = mock.patch.dict(os.environ, FAKE_CREDENTIALS)
        self.env.start()
        self.addCleanup(self.env.stop)
        code, text = prepare_decision(self.db, self.tmp)
        self.assertEqual(code, 0, text)
        plan = self.tmp / 'plan.json'
        plan.write_text(json.dumps({'scope': 'small', 'goal': 'g'}), encoding='utf-8')
        self.plan = plan

    def test_readonly_commands_do_not_load_the_channel(self):
        def bomb(*args, **kwargs):
            raise AssertionError('只读命令不该装载通道')

        with mock.patch.object(decisions_cli, 'load_channel', bomb), \
                mock.patch.object(feishu.FeishuChannel, '__init__', bomb):
            code, text = run_decisions(['--db', str(self.db), '--channel-config',
                                        config_json(self.tmp), 'status', '--decision', 'E1.1'])
            self.assertEqual(code, 0, text)
            code, text = run_decisions(['--db', str(self.db), 'overdue', '--exp', EXP])
            self.assertEqual(code, 0, text)
            code, text = run_decisions(['--db', str(self.db), 'authorize-check',
                                        '--plan', str(self.plan), '--exp', EXP,
                                        '--owner-session', 'sess-01', '--scope', 'small'])
            self.assertEqual(code, 0, text)
        self.assertFalse((self.tmp / 'ledger.sqlite3').exists())   # 没有顺手建台账

    def test_readonly_factory_is_used_when_the_kernel_provides_it(self):
        calls = []

        def factory(db_path):
            calls.append(db_path)
            return DecisionService(Store(db_path))

        def bomb(*args, **kwargs):
            raise AssertionError('只读命令不该装载通道')

        with mock.patch.object(DecisionService, 'readonly', staticmethod(factory), create=True), \
                mock.patch.object(decisions_cli, 'load_channel', bomb):
            code, text = run_decisions(['--db', str(self.db), 'status', '--decision', 'E1.1'])
        self.assertEqual(code, 0, text)
        self.assertEqual(calls, [str(self.db)])
        self.assertEqual(json.loads(text)['state'], 'prepared')

    def test_readonly_commands_on_a_missing_db_are_undecidable_not_empty(self):
        """只读模式不建库：缺库要报「不可判」并给非零退出码，⛔ 不假装没有记录。"""
        missing = self.tmp / 'never-created.sqlite3'
        code, text = run_decisions(['--db', str(missing), 'status', '--decision', 'E1.1'])
        self.assertEqual(code, 2, text)
        self.assertTrue(json.loads(text)['undecidable'])
        code, text = run_decisions(['--db', str(missing), 'overdue', '--exp', EXP])
        self.assertEqual(code, 2, text)
        self.assertTrue(json.loads(text)['undecidable'])
        self.assertFalse(missing.exists())

    def test_channel_is_only_loaded_for_commands_that_use_it(self):
        loads = []

        def fake_select(args):
            loads.append(True)
            return None

        with mock.patch.object(decisions_cli, 'select_channel', fake_select):
            code, text = run_decisions(['--db', str(self.db), 'status', '--decision', 'E1.1'])
            self.assertEqual(code, 0, text)
            self.assertEqual(loads, [])
            code, text = run_decisions(['--db', str(self.db), 'send', '--decision', 'E1.1'])
        self.assertEqual(code, 2, text)
        self.assertEqual(json.loads(text)['error'], 'ChannelUnavailable')
        self.assertEqual(loads, [True])


# ══════════════════════════ listen ══════════════════════════

class ListenTests(unittest.TestCase):
    """默认只绑回环；只 ingest 验签通过的事件；未认证事件不产生批准。"""

    def setUp(self):
        self.tmp = Path(make_tmp('channels_cli_listen_'))
        self.db = self.tmp / 'runtime.sqlite3'
        self.env = mock.patch.dict(os.environ, FAKE_CREDENTIALS)
        self.env.start()
        self.addCleanup(self.env.stop)

    def listener(self, **overrides):
        return channels_cli.build_listener(
            config_json(self.tmp, **overrides), self.db, transport=bomb_transport,
            credential_source=fake_credential_source, clock=Clock())

    def test_listen_defaults_to_loopback_and_calls_serve(self):
        recorded = {}

        def fake_serve(channel, *, host, port, path, block=True):
            recorded.update({'channel': channel, 'host': host, 'port': port,
                             'path': path, 'block': block})

        with mock.patch.object(feishu, 'serve', fake_serve):
            code, text = run_cli(['--db', str(self.db), '--config', config_json(self.tmp),
                                  'listen'])
        self.assertEqual(code, 0, text)
        self.assertEqual(recorded['host'], '127.0.0.1')
        self.assertEqual(recorded['port'], channels_cli.DEFAULT_PORT)
        self.assertEqual(recorded['path'], feishu.WEBHOOK_PATH)
        self.assertIsInstance(recorded['channel'], channels_cli.IngestingFeishuChannel)
        self.assertEqual(str(recorded['channel'].service.store.db_path), str(self.db))
        startup = json.loads(text)
        self.assertEqual(startup['listening']['host'], '127.0.0.1')
        self.assertTrue(startup['security']['only_loopback'])
        self.assertFalse(startup['security']['sends_messages'])
        for secret in FAKE_CREDENTIALS.values():
            self.assertNotIn(secret, text)

    def test_listen_refuses_non_loopback_without_allow_remote(self):
        def fake_serve(*args, **kwargs):
            raise AssertionError('非回环地址在 --allow-remote 之前不该起服务')

        with mock.patch.object(feishu, 'serve', fake_serve):
            code, text = run_cli(['--db', str(self.db), '--config', config_json(self.tmp),
                                  'listen', '--host', '0.0.0.0'])
        self.assertEqual(code, 2, text)
        payload = json.loads(text)
        self.assertEqual(payload['error'], 'ChannelUnavailable')
        self.assertIn('--allow-remote', payload['message'])
        # 显式放行后才起服务（这里仍不真的监听）
        recorded = {}

        def fake_serve_ok(channel, *, host, port, path, block=True):
            recorded.update({'host': host, 'port': port})

        with mock.patch.object(feishu, 'serve', fake_serve_ok):
            code, text = run_cli(['--db', str(self.db), '--config', config_json(self.tmp),
                                  'listen', '--host', '0.0.0.0', '--port', '9001', '--allow-remote'])
        self.assertEqual(code, 0, text)
        self.assertEqual(recorded, {'host': '0.0.0.0', 'port': 9001})

    def test_listen_and_poll_need_db(self):
        code, text = run_cli(['--config', config_json(self.tmp), 'listen'])
        self.assertEqual(code, 2, text)
        self.assertIn('--db', json.loads(text)['message'])
        code, text = run_cli(['--db', str(self.db), 'listen'])
        self.assertEqual(code, 2, text)
        self.assertIn('--config', json.loads(text)['message'])

    def test_listen_refuses_without_decryption_backend_before_touching_disk(self):
        """缺解密后端时挂服务只会对每条真实事件 501 ⇒ 起服务之前就拒，且不建库、不建台账。"""
        def fake_serve(*args, **kwargs):
            raise AssertionError('缺解密后端时不许起服务')

        with mock.patch.object(channels_cli.channel_crypto, 'backend_available',
                               return_value=False), \
                mock.patch.object(feishu, 'serve', fake_serve):
            code, text = run_cli(['--db', str(self.db), '--config', config_json(self.tmp),
                                  'listen'])
        self.assertEqual(code, 2, text)
        payload = json.loads(text)
        self.assertEqual(payload['error'], 'ChannelUnavailable')
        self.assertIn('cryptography', payload['message'])
        self.assertIn('python -m pip', payload['message'])
        self.assertFalse(self.db.exists())                     # 没建决策库
        self.assertFalse((self.tmp / 'ledger.sqlite3').exists())   # 没建适配层台账

    def test_unsigned_webhook_is_not_ingested(self):
        channel, service = self.listener()
        result = channel.handle_webhook({}, json.dumps(message_event()).encode('utf-8'))
        self.assertFalse(result['ok'])
        self.assertFalse(result['ingest']['attempted'])
        self.assertEqual(channel.poll(None)['events'], [])
        self.assertEqual(service.inbox.pending_events(), [])
        # 签名不对：同样不进 ingest
        headers, raw = signed(message_event(event_id='ev-bad'), clock=channel.clock)
        headers['X-Lark-Signature'] = '0' * 64
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'signature_mismatch')
        self.assertFalse(result['ingest']['attempted'])
        self.assertEqual(service.inbox.pending_events(), [])

    def test_sender_outside_allowlist_is_rejected_before_ingest(self):
        channel, service = self.listener()
        headers, raw = signed(message_event(open_id=MALLORY), clock=channel.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['status'], 403)
        self.assertEqual(result['reason'], 'sender_not_allowed')
        self.assertFalse(result['ingest']['attempted'])
        self.assertEqual(service.inbox.pending_events(), [])

    def test_authenticated_message_is_ingested_over_loopback(self):
        channel, service = self.listener()
        server, port, thread = start_server(channel)
        try:
            headers, raw = signed(message_event(), clock=channel.clock)
            status, body = post(port, feishu.WEBHOOK_PATH, headers, raw)
            self.assertEqual(status, 200, body)
            self.assertEqual(body, {})
            questions = service.inbox.pending_questions()
            self.assertEqual([row['event_id'] for row in questions], ['ev-msg-1'])
            self.assertEqual(service.reply_status(exp_id=EXP)['text_events'], 1)
            self.assertEqual([item['event_id'] for item in channel.ingested], ['ev-msg-1'])
            # 换一份坏签名：401，且不新增任何入向
            bad, raw2 = signed(message_event(event_id='ev-msg-2'), clock=channel.clock)
            bad['X-Lark-Signature'] = 'f' * 64
            status2, body2 = post(port, feishu.WEBHOOK_PATH, bad, raw2)
            self.assertEqual(status2, 401, body2)
            self.assertEqual(len(service.inbox.pending_questions()), 1)
            self.assertEqual(len(channel.ingested), 1)
        finally:
            stop_server(server, thread)

    def test_wrong_path_is_404_and_never_ingested(self):
        channel, service = self.listener()
        server, port, thread = start_server(channel)
        try:
            headers, raw = signed(message_event(), clock=channel.clock)
            status, _ = post(port, '/not-the-webhook', headers, raw)
            self.assertEqual(status, 404)
            self.assertEqual(service.inbox.pending_events(), [])
        finally:
            stop_server(server, thread)

    def test_signed_but_unsent_card_approval_cannot_resolve_the_decision(self):
        code, text = prepare_decision(self.db, self.tmp)
        self.assertEqual(code, 0, text)
        channel, service = self.listener()
        self.assertEqual(service.status('E1.1')['state'], 'prepared')
        # 1) 签名对、发送人在允许列表里，但那张卡不是本适配层发的 ⇒ 403，不进 ingest
        headers, raw = signed(card_action_event(), clock=channel.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['reason'], 'card_not_sent_by_adapter')
        self.assertEqual(result['status'], 403)
        self.assertFalse(result['ingest']['attempted'])
        # 2) 签名不对的批准点击 ⇒ 401
        headers, raw = signed(card_action_event(event_id='ev-card-2'), clock=channel.clock)
        headers['X-Lark-Signature'] = 'a' * 64
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['status'], 401)
        # 3) 绕过适配层、直接把伪事件塞给核心 ⇒ authenticate 拒 ⇒ rejected
        forged = {'event_id': 'ev-forged-approve', 'exp_id': EXP,
                  'create_time': time.time() * 1000, 'recipient': RECIPIENT,
                  'principal': ALICE, 'decision_id': 'E1.1', 'short_code': SHORT_CODE,
                  'intent': 'approve', 'options': ['A']}
        row = service.ingest(feishu.SOURCE, forged)
        self.assertEqual(row['status'], 'rejected')
        self.assertFalse(row['consumed'])
        status = service.status('E1.1')
        self.assertEqual(status['state'], 'prepared')
        self.assertIsNone(status.get('resolution'))

    def test_unbound_listener_fails_closed(self):
        channel = channels_cli.IngestingFeishuChannel(
            feishu.from_mapping(config_mapping(self.tmp)), transport=bomb_transport,
            credential_source=fake_credential_source, clock=Clock())
        headers, raw = signed(message_event(), clock=channel.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertEqual(result['status'], 500)
        self.assertEqual(result['response']['reason'], 'ingest_failed')
        self.assertEqual(result['ingest']['error'], 'service_unbound')

    def test_build_listener_rejects_non_channel(self):
        with self.assertRaises(ChannelUnavailable):
            channels_cli.IngestingFeishuChannel(feishu.from_mapping(config_mapping(self.tmp))
                                                ).bind_service(object())


# ══════════════════════════ poll ══════════════════════════

class PollTests(unittest.TestCase):
    """一次性拉取：只吐适配层已认证的事件，游标由调用方保存。"""

    def setUp(self):
        self.tmp = Path(make_tmp('channels_cli_poll_'))
        self.db = self.tmp / 'runtime.sqlite3'
        self.env = mock.patch.dict(os.environ, FAKE_CREDENTIALS)
        self.env.start()
        self.addCleanup(self.env.stop)

    def seed(self, **overrides):
        channel = make_channel(self.tmp, **overrides)
        headers, raw = signed(message_event(create_time_ms=int(channel.clock() * 1000)),
                              clock=channel.clock)
        result = channel.handle_webhook(headers, raw)
        self.assertTrue(result['ok'], result)
        return channel

    def test_poll_ingests_authenticated_events_and_returns_cursor(self):
        self.seed()
        code, text = run_cli(['--db', str(self.db), '--config', config_json(self.tmp),
                              'poll', '--limit', '10'])
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['results'][0]['status'], 'applied')
        self.assertIsNotNone(payload['cursor'])
        code, text = run_cli(['--db', str(self.db), '--config', config_json(self.tmp),
                              'poll', '--cursor', str(payload['cursor'])])
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)['count'], 0)
        service = DecisionService(Store(self.db))
        self.assertEqual([row['event_id'] for row in service.inbox.pending_questions()],
                         ['ev-msg-1'])

    def test_poll_needs_config_and_db(self):
        code, text = run_cli(['--db', str(self.db), 'poll'])
        self.assertEqual(code, 2, text)
        self.assertIn('--config', json.loads(text)['message'])
        code, text = run_cli(['--config', config_json(self.tmp), 'poll'])
        self.assertEqual(code, 2, text)
        self.assertIn('--db', json.loads(text)['message'])

    def test_poll_with_bad_cursor_is_exit_2(self):
        self.seed()
        code, text = run_cli(['--db', str(self.db), '--config', config_json(self.tmp),
                              'poll', '--cursor', 'not-a-number'])
        self.assertEqual(code, 2, text)
        payload = json.loads(text)
        self.assertFalse(payload['ok'])
        self.assertIn('cursor', payload['message'])


# ══════════════════════════ 命令行形状 ══════════════════════════

class ParserTests(unittest.TestCase):
    """没有「伪造批准 / 自动批准」这类开关；默认值就是安全默认值。"""

    def collect_options(self, target):
        options = []
        for action in target._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    options.extend(self.collect_options(sub))
            else:
                options.extend(action.option_strings)
        return options

    def test_commands_and_no_fake_approval_switches(self):
        parser = channels_cli.build_parser()
        joined = ' '.join(self.collect_options(parser)).lower()
        for forbidden in ('--approve', '--fake', '--force', '--assume', '--auto-approve',
                          '--auto-approve-all'):
            self.assertNotIn(forbidden, joined)
        commands = set()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                commands = set(action.choices)
        self.assertEqual(commands, {'doctor', 'listen', 'poll'})

    def test_listen_defaults_are_safe(self):
        args = channels_cli.build_parser().parse_args(['listen'])
        self.assertEqual(args.host, '127.0.0.1')
        self.assertEqual(args.port, channels_cli.DEFAULT_PORT)
        self.assertEqual(args.path, feishu.WEBHOOK_PATH)
        self.assertFalse(args.allow_remote)
        self.assertFalse(args.verbose)
        self.assertIsNone(args.db)
        self.assertIsNone(args.config)


if __name__ == '__main__':
    (ROOT / 'temp').mkdir(exist_ok=True)
    unittest.main()
