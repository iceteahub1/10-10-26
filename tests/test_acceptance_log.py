"""Acceptance log privacy, bounded retention and independence from runtime logs."""
import json
from pathlib import Path
import tempfile
import unittest
from bistro.event_log import AcceptanceLog


class AcceptanceLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'acceptance.jsonl'
        self.log = AcceptanceLog(self.path)

    def test_allowlist_excludes_private_values_and_freeform_errors(self):
        self.log.start_run()
        self.log.record('cart_action', action='add_item', ok=True, source='controls',
                        cookie='secret-cookie', session_id='private-session', instructions='private-note')
        self.log.record('workflow_error', code='private user text', stage='request', message='private-message')
        self.log.record('private-event', message='private-data')
        report = self.log.report(); raw = json.dumps(report)
        for value in ('secret-cookie', 'private-session', 'private-note', 'private-message', 'private-data', 'private user text'):
            self.assertNotIn(value, raw)
        self.assertEqual(len(report['events']), 3)
        self.assertNotIn('code', report['events'][-1])
        self.assertEqual(report['events'][1]['action'], 'add_item')

    def test_latest_run_only_and_bounded_recent_events(self):
        self.log.start_run(); old = self.log.run_id
        self.log.record('ai_availability', available=True, code='ready')
        self.log.start_run(); self.assertNotEqual(old, self.log.run_id)
        for _ in range(1005):
            self.log.record('menu_availability', available=True, item_count=3, available_count=2)
        events = self.log.report()['events']
        self.assertEqual(len(events), 1000)
        self.assertTrue(all(e['run_id'] == self.log.run_id for e in events))

    def test_rotation_and_existing_application_log_remain_independent(self):
        application = self.path.parent/'application.log'; application.write_text('existing runtime log')
        log = AcceptanceLog(self.path, max_bytes=512); log.start_run()
        for _ in range(30):
            log.record('cart_action', action='add_item', ok=True, source='controls')
        self.assertTrue(self.path.with_suffix('.jsonl.1').is_file())
        self.assertLess(self.path.stat().st_size, 1024)
        self.assertLess(self.path.with_suffix('.jsonl.1').stat().st_size, 1024)
        self.assertTrue(log.report()['events'])
        self.assertEqual(application.read_text(), 'existing runtime log')


if __name__ == '__main__':
    unittest.main()
