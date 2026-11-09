# GENERATED-BY: portable host evidence migration
# LAST-EDIT: 2026-11-09
# PURPOSE: Verify DSH identity and messages from host-owned persistent logs.
# ORIGIN: generic mechanisms from upstream plan_audit_host.py
# CUMULATIVE-DELTA: Configurable trusted adapter boundary, no proof-selected paths; fail closed on ambiguous or torn logs.
"""Read-only DSH adapter. A workspace record cannot select its own evidence root.

The default root is ~/.dsh, not a caller-controlled environment variable. An
administrator may construct this adapter with another trusted root. This does
not attest semantic review quality, or replace OS protection of the host logs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime


class EvidenceError(ValueError):
    pass


def session_id(value):
    if not isinstance(value, str):
        raise EvidenceError('会话标识必须是文字')
    value = value.strip()
    if not value.startswith('session-'):
        value = 'session-' + value
    if not re.fullmatch(r'session-[A-Za-z0-9][A-Za-z0-9-]*', value):
        raise EvidenceError('会话标识不能包含路径或空白')
    return value


def visible_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(x if isinstance(x, str) else x.get('text', '')
                       for x in content if isinstance(x, str) or
                       (isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str)))
    return ''


def _message(row):
    value = row.get('data') or {}
    return value.get('message', value) if isinstance(value, dict) else {}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError('JSON含重复键，不能作为可信命令或报告')
        result[key] = value
    return result


def _json_document(text):
    body = text.strip()
    if body.startswith('```') and body.endswith('```'):
        body = body[3:-3].strip()
        if body.startswith('json') and (len(body) == 4 or body[4].isspace()):
            body = body[4:].strip()
    try:
        return json.loads(body, object_pairs_hook=_unique_object, parse_constant=lambda x: (_ for _ in ()).throw(EvidenceError('JSON不允许非有限数')))
    except (ValueError, TypeError) as exc:
        raise EvidenceError('会话可见正文必须是完整 JSON 文档') from exc


def _json_objects(text):
    decoder = json.JSONDecoder()
    result = []
    position = 0
    while position < len(text):
        if text[position] not in '{[':
            position += 1
            continue
        try:
            value, length = decoder.raw_decode(text[position:])
        except ValueError:
            position += 1
            continue
        result.append(_json_document(text[position:position + length]))
        position += length
    return result


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _timestamp(value):
    if type(value) in (int, float):
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    raise EvidenceError('宿主消息没有可判定的时间')


class DSHHost:
    def __init__(self, root=None, *, node=None, decoder=None, workspace=None):
        self.workspace = Path(workspace).resolve() if workspace is not None else None
        self.root = (Path.home() / '.dsh' if root is None else Path(root)).resolve()
        self.node = node
        self._decoder = decoder
        self.evidence_source = 'injected' if decoder is not None else 'dsh-persistent-log'

    def _decode(self, path):
        if self._decoder is not None:
            return self._decoder(path)
        node = self.node or shutil.which('node')
        if not node:
            for candidate in (self.root / 'runtimes/node/bin/node.exe',
                              self.root / 'dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe'):
                if candidate.is_file():
                    node = str(candidate)
                    break
        if not node:
            raise EvidenceError('没有可解码宿主日志的 Node.js；需要 Node.js 24 或更新版本')
        script = Path(__file__).resolve().parents[1] / 'hosts/dsh/tools/decode-session.cjs'
        with tempfile.TemporaryDirectory(prefix='autoresearch-host-') as directory:
            output, error = Path(directory) / 'log.jsonl', Path(directory) / 'error.txt'
            with error.open('wb') as stream:
                result = subprocess.run([str(node), str(script), str(path), str(output)],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=stream, cwd=directory, timeout=60)
            if result.returncode:
                detail = error.read_text(encoding='utf-8', errors='replace')[-800:]
                raise EvidenceError('宿主日志不可判：' + detail)
            try:
                return [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines() if line.strip()]
            except (ValueError, UnicodeError) as exc:
                raise EvidenceError('解码后的宿主日志不是完整 UTF-8 JSONL') from exc

    def session(self, identity):
        sid = session_id(identity)
        base = self.root / 'sessions'
        names = {sid, sid.removeprefix('session-')}
        candidates = []
        if base.is_dir():
            for project in base.iterdir():
                if not project.is_dir():
                    continue
                for directory in project.iterdir():
                    if directory.name not in names or not directory.is_dir():
                        continue
                    # Reject symlinks leaving the administrator-selected host root.
                    if not directory.resolve().is_relative_to(base.resolve()):
                        raise EvidenceError('宿主日志目录越出可信根')
                    files = []
                    for path in directory.iterdir():
                        match = re.fullmatch(r'session\.v(\d+)\.jsonl\.zstd', path.name)
                        if match and path.is_file():
                            if not path.resolve().is_relative_to(base.resolve()):
                                raise EvidenceError('宿主日志文件越出可信根')
                            files.append((int(match.group(1)), path))
                    if files:
                        candidates.append(max(files, key=lambda pair: pair[0])[1])
        if len(candidates) != 1:
            raise EvidenceError('宿主会话日志缺失或有歧义；拒绝选择旧版本猜测放行')
        path = candidates[0]
        before = path.stat()
        rows = self._decode(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise EvidenceError('读取期间宿主日志变化，请重试')
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise EvidenceError('宿主日志为空或含非法事件')
        header = rows[0]
        if header.get('type') != 'session' or session_id(header.get('id')) != sid:
            raise EvidenceError('宿主日志首条身份不符')
        events, seen, previous = [], set(), -1
        for row in rows:
            seq = row.get('seq')
            if seq is None and row is header:
                continue
            if type(seq) is not int or seq < 0 or seq in seen or seq <= previous:
                raise EvidenceError('宿主事件序号缺失、重复或逆序')
            previous = seq
            seen.add(seq)
            data, message = row.get('data') or {}, _message(row)
            kind = row.get('type')
            source = data.get('source') or {}
            if kind in ('user/message', 'assistant/message') and not isinstance(message, dict):
                raise EvidenceError('宿主消息形状不合法')
            if kind == 'user/message' and not isinstance(source, dict):
                raise EvidenceError('宿主用户消息来源不可判')
            # Control records such as sandbox/mode use a string `source`; they are not messages.
            source = source if isinstance(source, dict) else {}
            message = message if isinstance(message, dict) else {}
            rpc = source.get('rpcId')
            is_user = kind == 'user/message' and source.get('kind') in (None, 'user') and isinstance(rpc, str) and bool(rpc.strip())
            role = 'user' if kind == 'user/message' else 'assistant' if kind == 'assistant/message' else message.get('role')
            events.append({'seq': seq, 'type': kind, 'role': role,
                           'text': visible_text(message.get('content')),
                           'source': source, 'rpc_id': rpc, 'is_user': is_user,
                           'time': row.get('time'), 'data': data})
        descriptors = [row.get('data') for row in rows if row.get('type') == 'subagent/descriptor']
        if len(descriptors) > 1:
            raise EvidenceError('宿主子会话身份描述有歧义')
        return {'session_id': sid, 'id': sid, 'header': header,
                'descriptor': descriptors[0] if descriptors else None,
                'events': events, 'raw_events': rows, 'max_seq': previous,
                'source': self.evidence_source}

    @staticmethod
    def _no_inheritance(session):
        header = session['header']
        if header.get('isSeeded') is True or (session.get('descriptor') or {}).get('provider') == 'fork':
            raise EvidenceError('继承会话不能作独立来源')
        if any(row.get('type') == 'session/end-seed' and (row.get('data') or {}).get('inherited') is True for row in session['raw_events']):
            raise EvidenceError('宿主日志含继承切口')

    @staticmethod
    def _proof(payload):
        proof = payload.get('proof')
        if not isinstance(proof, dict) or set(proof) != {'session_id', 'seq'} or type(proof['seq']) is not int or proof['seq'] < 0:
            raise EvidenceError('proof只能是{session_id,seq}，不接受证据文件路径')
        return session_id(proof['session_id']), proof['seq']

    def _event(self, sid, seq):
        session = self.session(sid)
        matches = [row for row in session['events'] if row['seq'] == seq]
        if len(matches) != 1:
            raise EvidenceError('宿主日志没有指定消息')
        return session, matches[0]

    def user_message(self, proof):
        sid, seq = self._proof({'proof': proof})
        session, event = self._event(sid, seq)
        self._no_inheritance(session)
        header = session['header']
        if header.get('parentSession') or header.get('delegationDepth') not in (0, None) or not event['is_user']:
            raise EvidenceError('需要顶层会话中带客户端rpcId的真实用户消息')
        return event

    def _child(self, payload):
        sid, seq = self._proof(payload)
        owner = session_id(payload.get('owner_session'))
        if sid == owner:
            raise EvidenceError('独立角色不能是负责人自己')
        child, event = self._event(sid, seq)
        self._no_inheritance(child)
        header, descriptor = child['header'], child.get('descriptor') or {}
        if session_id(header.get('parentSession')) != owner or header.get('origin') != 'subagent' or header.get('delegationDepth') != 1:
            raise EvidenceError('不是负责人的直接子会话')
        if descriptor.get('provider') != 'spawn' or descriptor.get('mode') not in ('continuable', 'one-shot'):
            raise EvidenceError('需要宿主证明新spawn，fork不算独立')
        prepared = payload.get('prepared_id')
        if not isinstance(prepared, str) or not prepared.strip():
            raise EvidenceError('缺少被审材料标识')
        parent = self.session(owner)
        calls = []
        for row in parent['raw_events']:
            data = row.get('data') or {}
            if row.get('type') != 'tool/call' or data.get('name') not in ('subagent', 'functions.subagent'):
                continue
            arguments = data.get('arguments')
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    continue
            if not isinstance(arguments, dict) or prepared not in str(arguments.get('prompt', '')):
                continue
            call_id = data.get('callId')
            if not call_id:
                continue
            for result in parent['raw_events']:
                message = _message(result)
                if result.get('type') != 'tool/result' or message.get('toolCallId') != call_id:
                    continue
                text = visible_text(message.get('content'))
                # Exact token prevents one child ID being a prefix of another.
                bare = re.escape(sid.removeprefix('session-'))
                if re.search(r'(?<![A-Za-z0-9-])(?:session-)?' + bare + r'(?![A-Za-z0-9-])', text):
                    if row.get('time') is None or _timestamp(row['time']) > _timestamp(header.get('createdAt')):
                        raise EvidenceError('派工时间晚于子会话创建')
                    calls.append(call_id)
        if len(set(calls)) != 1:
            raise EvidenceError('缺少与材料和子会话精确绑定的唯一派工回执')
        catalog = [r.get('data') or {} for r in parent['raw_events'] if r.get('type') == 'subagent/catalog']
        if not any(str(item.get('childId', '')).removeprefix('session-') == sid.removeprefix('session-') for item in catalog):
            raise EvidenceError('负责人日志没有子会话目录记录')
        if event['type'] != 'assistant/message':
            raise EvidenceError('回传来源不是子会话可见消息')
        return child, event, calls[0]

    def _scribe(self, payload):
        if self.workspace is None:
            raise EvidenceError('scribe核验需要由部署配置提供可信工作区根')
        child, event, dispatch_id = self._child(payload)
        if session_id(payload.get('writer_session')) != child['session_id']:
            raise EvidenceError('页面作者与宿主子会话不符')
        relative = payload.get('page_path')
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise EvidenceError('页面必须指向工作区相对路径')
        page = (self.workspace / relative).resolve()
        if not page.is_relative_to(self.workspace) or not page.is_file():
            raise EvidenceError('页面不存在或越出工作区')
        actual_bytes = page.read_bytes()
        digest = hashlib.sha256(actual_bytes).hexdigest()
        if payload.get('page_sha256') != digest:
            raise EvidenceError('页面字节已变化')
        accepted, reconstructed = [], None
        for row in child['raw_events']:
            if row.get('type') != 'tool/call' or row.get('seq', event['seq'] + 1) >= event['seq']:
                continue
            data = row.get('data') or {}
            name = str(data.get('name', '')).removeprefix('functions.')
            if name not in ('write', 'write_file', 'edit', 'edit_file', 'read', 'read_file'):
                continue
            arguments = data.get('arguments')
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    continue
            if not isinstance(arguments, dict):
                continue
            file_path = arguments.get('file_path', arguments.get('path'))
            if not isinstance(file_path, str):
                continue
            target = (self.workspace / file_path).resolve()
            if target != page:
                continue
            results = [r for r in child['raw_events'] if r.get('type') == 'tool/result'
                       and _message(r).get('toolCallId') == data.get('callId')
                       and row.get('seq', -1) < r.get('seq', -1) < event['seq']]
            if len(results) != 1:
                reconstructed = None
                continue
            message = _message(results[0])
            text = visible_text(message.get('content'))
            if message.get('isError') is True or message.get('is_error') is True or '[sandbox:' in text:
                reconstructed = None
                continue
            # Require tool-specific success text; mere existence of a result is not success.
            if name in ('write', 'write_file'):
                content = arguments.get('content')
                success = ('Created file' in text or 'has been written' in text or 'has been updated successfully' in text)
                reconstructed = content if success and isinstance(content, str) else None
            elif name in ('edit', 'edit_file'):
                old, new = arguments.get('old_string'), arguments.get('new_string')
                success = 'has been updated successfully' in text
                if not success or reconstructed is None or not isinstance(old, str) or not old or not isinstance(new, str):
                    reconstructed = None
                elif arguments.get('replace_all') is True and old in reconstructed:
                    reconstructed = reconstructed.replace(old, new)
                elif reconstructed.count(old) == 1:
                    reconstructed = reconstructed.replace(old, new, 1)
                else:
                    reconstructed = None
            else:
                # Reads may be truncated/line-numbered; they cannot prove full pre-edit bytes.
                continue
            accepted.append({'tool': name, 'session_id': child['session_id'],
                             'path': relative, 'ok': reconstructed is not None, 'seq': row['seq']})
        if reconstructed is None or hashlib.sha256(reconstructed.encode('utf-8')).hexdigest() != digest:
            raise EvidenceError('没有可重演到当前页面字节的完整写入及成功回执；仅edit片段不足以证明作者')
        return {'verified': True, 'session_id': child['session_id'],
                'spawned_by': session_id(payload['owner_session']), 'parent_session': session_id(payload['owner_session']),
                'owner_session': session_id(payload['owner_session']), 'spawned': True,
                'prepared_id': payload['prepared_id'], 'page_path': relative, 'page_sha256': digest,
                'mentions': ['prepared_id'], 'tool_calls': accepted,
                'tool_call_id': dispatch_id, 'delivery': {'delivered': False, 'channel': None},
                'source': self.evidence_source, 'reason': None}

    def verify(self, kind, payload):
        if not isinstance(payload, dict):
            raise EvidenceError('核验材料必须是对象')
        if kind == 'scribe':
            return self._scribe(payload)
        if kind in ('user_command', 'policy_off', 'waiver', 'audit_extend'):
            event = self.user_message(payload.get('proof'))
            expected = payload.get('expected')
            if not isinstance(expected, dict) or not expected:
                raise EvidenceError('核验器需要调用者提供完整预期命令')
            commands = [item for item in _json_objects(event['text']) if isinstance(item, dict) and 'action' in item]
            if len(commands) != 1 or commands[0] != expected:
                raise EvidenceError('真实用户消息里的命令与执行动作不逐键相等')
            return {'verified': True, 'kind': kind, 'session_id': session_id(payload['proof']['session_id']),
                    'seq': event['seq'], 'rpc_id': event['rpc_id'], 'created_at': _timestamp(event['time']),
                    'command': expected, 'command_sha256': _sha(expected), 'source': self.evidence_source}
        if kind in ('audit_independence', 'monitor_independence', 'independent_report'):
            child, event, call_id = self._child(payload)
            expected = payload.get('declaration', payload.get('report'))
            if not isinstance(expected, dict) or _json_document(event['text']) != expected:
                raise EvidenceError('独立子会话的完整可见JSON与提交报告不相等')
            return {'verified': True, 'kind': kind, 'session_id': child['session_id'],
                    'seq': event['seq'], 'owner_session': session_id(payload['owner_session']),
                    'prepared_id': payload['prepared_id'], 'tool_call_id': call_id,
                    'report_sha256': _sha(expected), 'created_at': _timestamp(event['time']),
                    'source': self.evidence_source}
        raise EvidenceError('不支持的宿主核验类型：' + str(kind))
