# GENERATED-BY: portable host evidence migration
# LAST-EDIT: 2026-11-09
# PURPOSE: Synthetic persistent-log verification and refusal-path regression.
# ORIGIN: none
# CUMULATIVE-DELTA: Real zstd decode, exact report, client identity, spawn binding, torn frame checks.
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from kernel.autoresearch.host_evidence import DSHHost, EvidenceError, visible_text


class HostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = {'prepared_id': 'prepared-test', 'decision': 'proceed'}
        self.parent = [
            {'type': 'session', 'id': 'session-owner', 'createdAt': 1000, 'delegationDepth': 0},
            {'type': 'user/message', 'seq': 1, 'time': 1001, 'data': {'source': {'kind': 'user', 'rpcId': 'client-1'},
             'content': '请执行 {"action":"audit_off","plan":"P1"}'}},
            {'type': 'tool/call', 'seq': 2, 'time': 1002, 'data': {'name': 'subagent', 'callId': 'call-1',
             'arguments': {'prompt': '审查材料 prepared-test'}}},
            {'type': 'tool/result', 'seq': 3, 'time': 1004, 'data': {'message': {'toolCallId': 'call-1',
             'content': 'started subagent child'}}},
            {'type': 'subagent/catalog', 'seq': 4, 'time': 1004, 'data': {'childId': 'child', 'mode': 'continuable'}}]
        self.child = [
            {'type': 'session', 'id': 'session-child', 'parentSession': 'owner', 'origin': 'subagent',
             'delegationDepth': 1, 'isSeeded': False, 'createdAt': 1003},
            {'type': 'subagent/descriptor', 'seq': 1, 'time': 1003, 'data': {'provider': 'spawn', 'mode': 'continuable'}},
            {'type': 'session/end-seed', 'seq': 2, 'time': 1003, 'data': {}},
            {'type': 'assistant/message', 'seq': 3, 'time': 1005, 'data': {'message': {'role': 'assistant',
             'content': [{'type': 'reasoning', 'text': 'not visible'}, {'type': 'text', 'text': json.dumps(self.report)}]}}}]
        self.logs = {'owner': self.parent, 'child': self.child}
        for name in self.logs:
            path = self.root / 'sessions/project' / ('session-' + name)
            path.mkdir(parents=True)
            (path / 'session.v4.jsonl.zstd').write_bytes(b'injected-decoder-fixture')
        self.host = DSHHost(self.root, decoder=lambda path: copy.deepcopy(self.logs[path.parent.name.removeprefix('session-')]))

    def payload(self):
        return {'proof': {'session_id': 'child', 'seq': 3}, 'owner_session': 'owner',
                'prepared_id': 'prepared-test', 'report': self.report}

    def test_exact_report_and_empty_endseed_accepted(self):
        result = self.host.verify('monitor_independence', self.payload())
        self.assertTrue(result['verified'])
        self.assertEqual(result['source'], 'injected')
        self.assertEqual(result['tool_call_id'], 'call-1')
        self.assertEqual(self.host.session('owner')['events'][0]['text'], self.parent[1]['data']['content'])

    def test_real_user_command_exact(self):
        payload = {'proof': {'session_id': 'owner', 'seq': 1}, 'expected': {'action': 'audit_off', 'plan': 'P1'}}
        self.assertTrue(self.host.verify('user_command', payload)['verified'])
        payload['expected']['plan'] = 'P2'
        with self.assertRaises(EvidenceError):
            self.host.verify('user_command', payload)

    def test_user_injection_no_rpc_and_child_sources_refused(self):
        payload = {'proof': {'session_id': 'owner', 'seq': 1}, 'expected': {'action': 'audit_off', 'plan': 'P1'}}
        for source in ({'kind': 'injection', 'rpcId': 'client-1'}, {'kind': 'user'}, {'kind': 'automatic', 'rpcId': 'client-1'}):
            with self.subTest(source=source):
                self.parent[1]['data']['source'] = source
                with self.assertRaises(EvidenceError):
                    self.host.verify('user_command', payload)

    def test_report_mismatch_or_prose_refused(self):
        for text in ('{"prepared_id":"other"}', '说明\n' + json.dumps(self.report)):
            self.child[-1]['data']['message']['content'] = text
            with self.assertRaises(EvidenceError):
                self.host.verify('audit_independence', self.payload())

    def test_fork_markers_and_parent_depth_refused(self):
        original = copy.deepcopy(self.child)
        changes = [('seed', lambda: self.child[0].update(isSeeded=True)),
                   ('fork', lambda: self.child[1]['data'].update(provider='fork')),
                   ('inherited', lambda: self.child[2]['data'].update(inherited=True)),
                   ('depth', lambda: self.child[0].update(delegationDepth=2)),
                   ('parent', lambda: self.child[0].update(parentSession='elsewhere'))]
        for label, mutate in changes:
            self.logs['child'] = self.child = copy.deepcopy(original)
            mutate()
            with self.subTest(label=label), self.assertRaises(EvidenceError):
                self.host.verify('monitor_independence', self.payload())

    def test_spawn_result_requires_exact_child_token(self):
        for text in ('started subagent child-longer', 'completed task', 'started subagent another'):
            self.parent[3]['data']['message']['content'] = text
            with self.assertRaises(EvidenceError):
                self.host.verify('monitor_independence', self.payload())

    def test_fork_call_cannot_prove_spawn(self):
        self.parent[2]['data']['name'] = 'subagent_fork'
        with self.assertRaises(EvidenceError):
            self.host.verify('monitor_independence', self.payload())

    def test_proof_cannot_select_log_path(self):
        payload = self.payload()
        payload['proof']['log_path'] = 'pretend.jsonl'
        with self.assertRaises(EvidenceError):
            self.host.verify('monitor_independence', payload)
        for invalid in ('../child', 'child/other', 'child\\other', ''):
            with self.subTest(invalid=invalid), self.assertRaises(EvidenceError):
                self.host.session(invalid)

    def test_missing_ambiguous_corrupt_or_duplicate_seq_refused(self):
        with self.assertRaises(EvidenceError):
            self.host.session('missing')
        self.child[-1]['seq'] = 1
        with self.assertRaises(EvidenceError):
            self.host.session('child')
        duplicate = self.root / 'sessions/other/session-owner'
        duplicate.mkdir(parents=True)
        (duplicate / 'session.v4.jsonl.zstd').write_bytes(b'x')
        with self.assertRaises(EvidenceError):
            self.host.session('owner')

    def test_duplicate_command_keys_refused(self):
        self.parent[1]['data']['content'] = '{"action":"keep_audit","action":"audit_off","plan":"P1"}'
        with self.assertRaises(EvidenceError):
            self.host.verify('user_command', {'proof': {'session_id': 'owner', 'seq': 1}, 'expected': {'action': 'audit_off', 'plan': 'P1'}})

    def test_user_kind_may_be_missing_but_rpc_required(self):
        self.parent[1]['data']['source'] = {'rpcId': 'real-client'}
        result = self.host.user_message({'session_id': 'owner', 'seq': 1})
        self.assertTrue(result['is_user'])

    def test_decoder_real_multiframe_and_torn_tail(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js unavailable')
        path = self.root / 'sessions/project/session-owner/session.v4.jsonl.zstd'
        first, second = self.root / 'a.json', self.root / 'b.json'
        first.write_text(json.dumps(self.parent[0]) + '\n', encoding='utf-8')
        second.write_text('\n'.join(json.dumps(r) for r in self.parent[1:]) + '\n', encoding='utf-8')
        script = "const fs=require('node:fs'),z=require('node:zlib');if(!z.zstdCompressSync)process.exit(77);fs.writeFileSync(process.argv[1],Buffer.concat(process.argv.slice(2).map(p=>z.zstdCompressSync(fs.readFileSync(p)))))"
        result = subprocess.run([node, '-e', script, str(path), str(first), str(second)], stdin=subprocess.DEVNULL)
        if result.returncode == 77:
            self.skipTest('Node.js needs Zstandard support')
        self.assertEqual(result.returncode, 0)
        host = DSHHost(self.root, node=node)
        self.assertEqual(host.session('owner')['max_seq'], 4)
        original = path.read_bytes()
        path.write_bytes(original[:-1])
        with self.assertRaises(EvidenceError):
            host.session('owner')
        path.write_bytes(original + b'bad!')
        with self.assertRaises(EvidenceError):
            host.session('owner')

    def test_scribe_full_write_and_edit_replay(self):
        page = self.root / 'STATUS.md'
        page.write_text('final body', encoding='utf-8')
        import hashlib
        self.child[-1]['seq'] = 8
        self.child[-1]['data']['message']['content'] = '已写页面'
        self.child[3:3] = [
            {'type': 'tool/call', 'seq': 3, 'time': 1004, 'data': {'name': 'write', 'callId': 'w', 'arguments': {'file_path': 'STATUS.md', 'content': 'initial body'}}},
            {'type': 'tool/result', 'seq': 4, 'time': 1004, 'data': {'message': {'toolCallId': 'w', 'content': 'Created file'}}},
            {'type': 'tool/call', 'seq': 5, 'time': 1004, 'data': {'name': 'edit', 'callId': 'e', 'arguments': {'file_path': 'STATUS.md', 'old_string': 'initial', 'new_string': 'final'}}},
            {'type': 'tool/result', 'seq': 6, 'time': 1004, 'data': {'message': {'toolCallId': 'e', 'content': 'The file has been updated successfully.'}}}]
        host = DSHHost(self.root, workspace=self.root, decoder=self.host._decoder)
        payload = self.payload()
        payload.update(proof={'session_id': 'child', 'seq': 8}, writer_session='child', page_path='STATUS.md',
                       page_sha256=hashlib.sha256(page.read_bytes()).hexdigest())
        evidence = host.verify('scribe', payload)
        self.assertTrue(evidence['verified'])
        self.assertFalse(evidence['delivery']['delivered'])
        self.assertEqual([r['tool'] for r in evidence['tool_calls']], ['write', 'edit'])
        # Current bytes matching a caller hash does not prove who wrote them.
        page.write_text('forged replacement', encoding='utf-8')
        payload['page_sha256'] = hashlib.sha256(page.read_bytes()).hexdigest()
        with self.assertRaises(EvidenceError):
            host.verify('scribe', payload)

    def test_scribe_failed_write_cannot_verify(self):
        import hashlib
        page = self.root / 'STATUS.md'
        page.write_text('page', encoding='utf-8')
        self.child[-1]['seq'] = 6
        self.child[3:3] = [
            {'type': 'tool/call', 'seq': 3, 'time': 1004, 'data': {'name': 'write', 'callId': 'w', 'arguments': {'file_path': 'STATUS.md', 'content': 'page'}}},
            {'type': 'tool/result', 'seq': 4, 'time': 1004, 'data': {'message': {'toolCallId': 'w', 'content': 'permission denied'}}}]
        payload = self.payload()
        payload.update(proof={'session_id': 'child', 'seq': 6}, writer_session='child', page_path='STATUS.md', page_sha256=hashlib.sha256(page.read_bytes()).hexdigest())
        host = DSHHost(self.root, workspace=self.root, decoder=self.host._decoder)
        with self.assertRaises(EvidenceError):
            host.verify('scribe', payload)

    def test_control_event_string_source_does_not_become_user(self):
        self.parent.append({'type': 'sandbox/mode', 'seq': 5, 'time': 1006, 'data': {'source': 'policy'}})
        session = self.host.session('owner')
        self.assertFalse(session['events'][-1]['is_user'])
        self.assertEqual(session['events'][-1]['source'], {})

    def test_visible_reasoning_is_excluded(self):
        self.assertEqual(visible_text([{'type': 'reasoning', 'text': 'secret'}, {'type': 'text', 'text': 'answer'}]), 'answer')


if __name__ == '__main__':
    unittest.main()
