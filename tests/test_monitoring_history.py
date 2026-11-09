# GENERATED-BY: portable monitoring acceptance
# LAST-EDIT: 2026-11-09
# PURPOSE: Ensure historical snapshots cannot import future facts through tool-name lookup.
# ORIGIN: monitoring.build_snapshot and synthetic public semantic cases
# CUMULATIVE-DELTA: Historical positive replay plus future metadata and result exclusion.
import json
import unittest
from kernel.autoresearch.monitoring import build_snapshot


class HistoricalSnapshotTests(unittest.TestCase):
    def test_future_tool_name_cannot_relabel_historical_result(self):
        events = [
            {'seq': 1, 'type': 'tool/call', 'data': {'callId': 'same', 'name': 'read_baseline'}},
            {'seq': 2, 'type': 'tool/result', 'text': 'baseline correct=8 total=10',
             'data': {'callId': 'same', 'content': 'baseline correct=8 total=10'}},
            {'seq': 8, 'type': 'tool/call', 'data': {'callId': 'same', 'name': 'future_secret_name'}},
            {'seq': 9, 'type': 'tool/result', 'text': 'future_secret_result',
             'data': {'callId': 'same', 'content': 'future_secret_result'}}]
        record = {'session_id': 'session-synthetic-history', 'events': events}
        past = build_snapshot(record, 2)
        serialized = json.dumps(past)
        self.assertEqual(past['tool_results'][0]['name'], 'read_baseline')
        self.assertNotIn('future_secret', serialized)
        self.assertEqual(past['excluded']['events_beyond_cutoff'], 2)
        future = build_snapshot(record, 9)
        self.assertIn('future_secret_result', json.dumps(future))
        self.assertNotEqual(past['events_sha256'], future['events_sha256'])


if __name__ == '__main__':
    unittest.main()
