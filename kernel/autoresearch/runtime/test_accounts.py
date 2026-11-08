# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: research-runtime/test_accounts.py
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.dont_write_bytecode = True
try:
    from kernel.autoresearch.runtime.store import Store, StoreError, VersionConflict, InvalidTransition, IdempotencyConflict, ALLOWED_TRANSITIONS, STATES, canonical_json
    from kernel.autoresearch.runtime.accounts import Accounts, ApprovalEvidence, SourcePolicy, HMACSource, ApprovalError, BudgetError
except ImportError:
    from kernel.autoresearch.runtime.store import Store, StoreError, VersionConflict, InvalidTransition, IdempotencyConflict, ALLOWED_TRANSITIONS, STATES, canonical_json
    from kernel.autoresearch.runtime.accounts import Accounts, ApprovalEvidence, SourcePolicy, HMACSource, ApprovalError, BudgetError
KEY = b'test-only-key-not-for-production!!'

def signed_event(**changes):
    event = dict(event_id='event-1', principal='human-1', exp_id='exp-a', series_id='series-1', scope='gpu-eval', expires_at=time.time() + 3600, total_units=100, action='grant')
    event.update(changes)
    event['signature'] = hmac.new(KEY, canonical_json(event).encode('utf-8'), hashlib.sha256).hexdigest()
    return event

def evidence():
    return ApprovalEvidence({'receiver': SourcePolicy(HMACSource(KEY), frozenset({'human-1'}))})

class RuntimeTest(unittest.TestCase):

    def setUp(self):
        root = Path(__file__).resolve().parents[3]
        self.tmp = tempfile.TemporaryDirectory(prefix='hil-accounts-', dir=root / 'temp')
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.store.ensure_experiment('exp-a', 'offline')
        self.store.ensure_experiment('exp-b', 'offline')
        self.accounts = Accounts(self.store, evidence())

    def grant(self, **changes):
        return self.accounts.authorize('receiver', signed_event(**changes), 'grant-1')

    def rows(self, table):
        conn = self.store.connect()
        try:
            return [dict(row) for row in conn.execute('SELECT * FROM ' + table)]
        finally:
            conn.close()

    def test_connection_contract(self):
        conn = self.store.connect()
        try:
            self.assertIs(conn.row_factory, sqlite3.Row)
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertGreater(conn.execute('PRAGMA busy_timeout').fetchone()[0], 0)
            self.assertEqual([r['name'] for r in conn.execute('PRAGMA table_info(experiments)')], ['id', 'goal', 'state', 'version', 'created_at', 'updated_at'])
        finally:
            conn.close()

    def test_ensure_and_get(self):
        initial = self.store.get_experiment('exp-a')
        self.assertEqual(initial, self.store.ensure_experiment('exp-a', 'do-not-overwrite'))
        self.assertIsNone(self.store.get_experiment('missing'))
        self.assertEqual(len(self.rows('events')), 2)

    def test_transaction_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as conn:
                conn.execute("UPDATE experiments SET goal='bad' WHERE id='exp-a'")
                self.store.emit(conn, 'exp-a', 'test', 'test', {})
                raise RuntimeError('injected failure')
        self.assertEqual(self.store.get_experiment('exp-a')['goal'], 'offline')
        self.assertEqual(len(self.rows('events')), 2)

    def test_keyboard_interrupt_rollback(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.store.transaction() as conn:
                conn.execute("UPDATE experiments SET goal='bad' WHERE id='exp-a'")
                raise KeyboardInterrupt()
        self.assertEqual(self.store.get_experiment('exp-a')['goal'], 'offline')

    def test_emit_requires_transaction_and_foreign_key(self):
        conn = self.store.connect()
        try:
            with self.assertRaises(StoreError):
                self.store.emit(conn, 'exp-a', 'test', 'actor', {})
        finally:
            conn.close()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as conn:
                self.store.emit(conn, 'absent', 'test', 'actor', {})

    def test_events_append_only(self):
        for sql in ('UPDATE events SET actor="fake"', 'DELETE FROM events'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as conn:
                    conn.execute(sql)

    def test_transition_path_and_replay(self):
        states = ['awaiting_approval', 'ready', 'running', 'collecting', 'auditing', 'completed']
        first = None
        for version, state in enumerate(states):
            result = self.store.transition('exp-a', version, state, 'actor', f't-{version}')
            if version == 0:
                first = result
            self.assertEqual(result['version'], version + 1)
        self.assertEqual(self.store.transition('exp-a', 0, states[0], 'actor', 't-0'), first)
        self.assertEqual(len(self.rows('events')), 8)

    def test_transition_conflicts(self):
        self.store.transition('exp-a', 0, 'awaiting_approval', 'actor', 't-0')
        with self.assertRaises(IdempotencyConflict):
            self.store.transition('exp-a', 0, 'paused', 'actor', 't-0')
        with self.assertRaises(VersionConflict):
            self.store.transition('exp-a', 0, 'ready', 'actor', 't-1')
        with self.assertRaises(InvalidTransition):
            self.store.transition('exp-b', 0, 'running', 'actor', 't-2')
        self.assertEqual(len(self.rows('events')), 3)

    def test_every_transition_pair(self):
        for old in STATES:
            for new in STATES:
                exp = old + '-' + new
                self.store.ensure_experiment(exp)
                with self.store.transaction() as conn:
                    conn.execute('UPDATE experiments SET state=? WHERE id=?', (old, exp))
                if new in ALLOWED_TRANSITIONS[old]:
                    self.assertEqual(self.store.transition(exp, 0, new, 'test', exp)['state'], new)
                else:
                    with self.assertRaises(InvalidTransition):
                        self.store.transition(exp, 0, new, 'test', exp)

    def test_transition_event_failure_rolls_back(self):
        with patch.object(self.store, 'emit', side_effect=RuntimeError('event failed')):
            with self.assertRaises(RuntimeError):
                self.store.transition('exp-a', 0, 'awaiting_approval', 'actor', 't-0')
        self.assertEqual(self.store.get_experiment('exp-a')['version'], 0)
        self.assertEqual(self.rows('store_requests'), [])

    def test_untrusted_gui_denied(self):
        with self.assertRaises(ApprovalError):
            self.accounts.authorize('gui', {'channel': 'gui', 'relayed_by': 'user'}, 'fake')
        self.assertEqual(self.rows('approval_events'), [])

    def test_tampered_and_wrong_principal_denied(self):
        event = signed_event()
        event['total_units'] = 10000
        with self.assertRaises(ApprovalError):
            self.accounts.authorize('receiver', event, 'tampered')
        with self.assertRaises(ApprovalError):
            self.accounts.authorize('receiver', signed_event(principal='runner'), 'runner')
        with self.assertRaises(ApprovalError):
            self.accounts.authorize('receiver', {'principal': 'human-1'}, 'unsigned')

    def test_default_evidence_fails_closed(self):
        with self.assertRaises(ApprovalError):
            Accounts(self.store).authorize('receiver', signed_event(), 'default')

    def test_authorize_idempotency_and_event_conflict(self):
        event = signed_event()
        result = self.accounts.authorize('receiver', event, 'a1')
        self.assertEqual(result, self.accounts.authorize('receiver', event, 'a1'))
        self.assertEqual(result, self.accounts.authorize('receiver', event, 'a2'))
        altered = signed_event(total_units=200)
        with self.assertRaises(IdempotencyConflict):
            self.accounts.authorize('receiver', altered, 'a3')
        with self.assertRaises(IdempotencyConflict):
            self.accounts.authorize('receiver', altered, 'a1')
        self.assertEqual(len(self.rows('approval_events')), 1)

    def test_authorization_and_consumption_atomic(self):
        with patch.object(self.store, 'emit', side_effect=RuntimeError('event failure')):
            with self.assertRaises(RuntimeError):
                self.grant()
        self.assertIsNone(self.accounts.get_account('series-1'))
        self.assertEqual(self.rows('approval_events'), [])
        self.assertEqual(self.rows('account_requests'), [])
        self.grant()

    def test_expiry_scope_and_missing_experiment(self):
        with self.assertRaises(ApprovalError):
            self.grant(expires_at=time.time() - 1)
        self.grant()
        with self.assertRaises(ApprovalError):
            self.accounts.reserve('series-1', 'exp-a', 'wrong-scope', 1, 'r1')
        with self.assertRaises(sqlite3.IntegrityError):
            self.accounts.reserve('series-1', 'missing', 'gpu-eval', 1, 'r2')
        self.assertEqual(self.accounts.get_account('series-1')['reserved_units'], 0)
        with patch.object(sys.modules[Accounts.__module__].time, 'time', return_value=time.time() + 7200):
            with self.assertRaises(ApprovalError):
                self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 1, 'r3')

    def test_integer_budget_validation(self):
        self.grant()
        for value in (True, 1.5, -1, '1', 2 ** 63):
            with self.assertRaises(ValueError):
                self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', value, 'r1')
        for value in (True, 1.5, -1, '1', 2 ** 63):
            with self.assertRaises(ValueError):
                self.accounts.settle('missing', value, 's1')

    def test_reserve_settle_release_shared_account(self):
        self.grant()
        a = self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 60, 'r1')
        b = self.accounts.reserve('series-1', 'exp-b', 'gpu-eval', 40, 'r2')
        with self.assertRaises(BudgetError):
            self.accounts.reserve('series-1', 'exp-b', 'gpu-eval', 1, 'r3')
        self.accounts.settle(a['id'], 30, 's1')
        self.accounts.release(b['id'], 's2')
        account = self.accounts.get_account('series-1')
        self.assertEqual((account['spent_units'], account['reserved_units']), (30, 0))
        self.assertEqual(self.accounts.get_reservation('r1')['status'], 'settled')
        self.assertEqual(self.accounts.get_reservation('r2')['status'], 'released')
        self.assertIsNone(self.accounts.get_reservation('missing'))

    def test_budget_request_replay(self):
        self.grant()
        result = self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 60, 'r1')
        self.assertEqual(result, self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 60, 'r1'))
        with self.assertRaises(IdempotencyConflict):
            self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 61, 'r1')
        final = self.accounts.settle('r1', 30, 's1')
        self.assertEqual(final, self.accounts.settle('r1', 30, 's1'))
        with self.assertRaises(IdempotencyConflict):
            self.accounts.settle('r1', 31, 's1')
        with self.assertRaises(BudgetError):
            self.accounts.release('r1', 'release-again')

    def test_oversettlement_and_failure_atomicity(self):
        self.grant()
        self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 20, 'r1')
        with self.assertRaises(BudgetError):
            self.accounts.settle('r1', 21, 's1')
        with patch.object(self.store, 'emit', side_effect=RuntimeError('event failure')):
            with self.assertRaises(RuntimeError):
                self.accounts.settle('r1', 20, 's2')
            with self.assertRaises(RuntimeError):
                self.accounts.reserve('series-1', 'exp-b', 'gpu-eval', 20, 'r2')
        self.assertEqual(self.accounts.get_account('series-1')['reserved_units'], 20)
        self.assertEqual(self.accounts.get_account('series-1')['spent_units'], 0)
        self.assertEqual(self.accounts.get_reservation('r1')['status'], 'reserved')
        self.assertIsNone(self.accounts.get_reservation('r2'))

    def test_renew_preserves_usage(self):
        self.grant()
        self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 50, 'r1')
        self.accounts.settle('r1', 30, 's1')
        self.accounts.reserve('series-1', 'exp-b', 'gpu-eval', 50, 'r2')
        with self.assertRaises(BudgetError):
            self.accounts.authorize('receiver', signed_event(action='renew', event_id='renew-bad', total_units=79), 'renew-bad')
        account = self.accounts.authorize('receiver', signed_event(action='renew', event_id='renew', total_units=200), 'renew')
        self.assertEqual((account['spent_units'], account['reserved_units']), (30, 50))
        with self.assertRaises(ApprovalError):
            self.accounts.authorize('receiver', signed_event(action='renew', event_id='scope', scope='another'), 'scope')

    def test_revoke_blocks_reserve_but_allows_finalization(self):
        self.grant()
        self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 20, 'r1')
        self.accounts.authorize('receiver', signed_event(action='revoke', event_id='stop'), 'stop')
        with self.assertRaises(ApprovalError):
            self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 1, 'r2')
        self.accounts.settle('r1', 10, 's1')
        self.assertEqual(self.accounts.get_account('series-1')['spent_units'], 10)

    def test_multiple_connections_cannot_overreserve(self):
        self.grant()
        barrier = threading.Barrier(12)

        def reserve(index):
            store = Store(self.store.db_path)
            accounts = Accounts(store, evidence())
            barrier.wait(timeout=20)
            try:
                accounts.reserve('series-1', 'exp-a' if index % 2 else 'exp-b', 'gpu-eval', 30, f'r-{index}')
                return True
            except BudgetError:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(results), 3)
        self.assertEqual(self.accounts.get_account('series-1')['reserved_units'], 90)
        self.assertEqual(len(self.rows('budget_reservations')), 3)

    def test_concurrent_same_request_creates_one_reservation(self):
        self.grant()
        barrier = threading.Barrier(8)

        def reserve(index):
            accounts = Accounts(Store(self.store.db_path), evidence())
            barrier.wait(timeout=20)
            return accounts.reserve('series-1', 'exp-a', 'gpu-eval', 80, 'same')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertTrue(all((r == results[0] for r in results)))
        self.assertEqual(len(self.rows('budget_reservations')), 1)

    def test_concurrent_transition_version_compare(self):
        barrier = threading.Barrier(8)

        def advance(index):
            store = Store(self.store.db_path)
            barrier.wait(timeout=20)
            try:
                store.transition('exp-a', 0, 'awaiting_approval', 'actor', f't-{index}')
                return True
            except VersionConflict:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(advance, range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.store.get_experiment('exp-a')['version'], 1)

    def test_concurrent_authorization_consumes_once(self):
        event = signed_event()
        barrier = threading.Barrier(8)

        def approve(index):
            accounts = Accounts(Store(self.store.db_path), evidence())
            barrier.wait(timeout=20)
            return accounts.authorize('receiver', event, f'a-{index}')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(approve, range(8)))
        self.assertTrue(all((r == results[0] for r in results)))
        self.assertEqual(len(self.rows('approval_events')), 1)
        self.assertEqual(len([r for r in self.rows('events') if r['kind'] == 'approval_grant']), 1)

    def test_concurrent_settle_or_release_finalizes_once(self):
        self.grant()
        self.accounts.reserve('series-1', 'exp-a', 'gpu-eval', 80, 'r1')
        barrier = threading.Barrier(8)

        def finish(index):
            accounts = Accounts(Store(self.store.db_path), evidence())
            barrier.wait(timeout=20)
            try:
                if index % 2:
                    accounts.settle('r1', 60, f's-{index}')
                else:
                    accounts.release('r1', f's-{index}')
                return True
            except BudgetError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(finish, range(8)))
        self.assertEqual(sum(results), 1)
        account = self.accounts.get_account('series-1')
        self.assertEqual(account['reserved_units'], 0)
        self.assertIn(account['spent_units'], (0, 60))

    def test_process_crash_rolls_back_all_writes(self):
        self.grant()
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--crash-worker', self.store.db_path], timeout=20)
        self.assertEqual(result.returncode, 23)
        reopened = Accounts(Store(self.store.db_path), evidence())
        self.assertEqual(reopened.get_account('series-1')['reserved_units'], 0)
        self.assertEqual(reopened.get_account('series-1')['spent_units'], 0)
        self.assertEqual(len(self.rows('approval_events')), 1)
        self.assertFalse(any((row['kind'] == 'crash_partial' for row in self.rows('events'))))
        reopened.reserve('series-1', 'exp-b', 'gpu-eval', 100, 'after-crash')

    def test_invalid_expiry_and_duplicate_grant_fail_closed(self):
        for expiry in (True, 'later', None):
            with self.assertRaises(ApprovalError):
                self.grant(expires_at=expiry)
        self.grant()
        with self.assertRaises(ApprovalError):
            self.accounts.authorize('receiver', signed_event(event_id='other-grant'), 'other-grant')
        self.assertEqual(len(self.rows('approval_events')), 1)

    def test_approval_events_append_only(self):
        self.grant()
        for sql in ('UPDATE approval_events SET principal="fake"', 'DELETE FROM approval_events'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as conn:
                    conn.execute(sql)
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--crash-worker':
        store = Store(sys.argv[2])
        with store.transaction() as conn:
            conn.execute("UPDATE budget_accounts SET reserved_units=80 WHERE series_id='series-1'")
            conn.execute("INSERT INTO approval_events VALUES('receiver','crashed','{}','human-1','{}',0)")
            store.emit(conn, 'exp-a', 'crash_partial', 'worker', {})
            os._exit(23)
    else:
        unittest.main(verbosity=2)
