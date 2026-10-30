import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_ipc
from bticino_entrance_open import OPEN_WAIT_SECONDS, open_and_confirm


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = None

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


SENT = {'ok': True, 'state': 'sent_unconfirmed', 'entrance': 'esterno'}
DOWN = {'ok': False, 'error': 'sip_unavailable'}


class OpenAndConfirmTests(unittest.TestCase):
    def setUp(self):
        self.time = FakeTime()
        self.results = {}

    def run_open(self, send):
        return open_and_confirm(send, self.results, 'esterno', clock=self.time.clock, sleep=self.time.sleep)

    def test_confirmed_by_the_gateway(self):
        def send():
            self.results['esterno'] = {'press': 'sent'}
            return SENT
        self.time.on_sleep = lambda: self.results['esterno'].update(press='SIP/2.0 200 OK')
        self.assertEqual(self.run_open(send)['state'], 'confirmed')

    def test_waits_for_the_reconnection(self):
        # Down for 12 seconds (an unstable HOMETOUCH), then registered again.
        attempts = []

        def send():
            attempts.append(self.time.now)
            if self.time.now < 12:
                return DOWN
            self.results['esterno'] = {'press': 'SIP/2.0 200 OK'}
            return SENT
        self.assertEqual(self.run_open(send)['state'], 'confirmed')
        self.assertGreater(len(attempts), 1)

    def test_gives_up_after_the_wait(self):
        result = self.run_open(lambda: DOWN)
        self.assertEqual(result, DOWN)
        self.assertGreaterEqual(self.time.now, OPEN_WAIT_SECONDS)

    def test_other_errors_are_not_retried(self):
        calls = []
        result = self.run_open(lambda: calls.append(1) or {'ok': False, 'error': 'unknown_entrance'})
        self.assertEqual((result['error'], len(calls)), ('unknown_entrance', 1))

    def test_rejected_or_unanswered_press(self):
        def rejected():
            self.results['esterno'] = {'press': 'SIP/2.0 403 Forbidden'}
            return SENT
        self.assertEqual(self.run_open(rejected)['error'], 'rejected')

        def unanswered():
            self.results['esterno'] = {'press': 'sent'}
            return SENT
        self.assertEqual(self.run_open(unanswered)['state'], 'sent_unconfirmed')

    def test_ipc_routes_the_opening_through_the_listener(self):
        with patch.object(bticino_ipc, '_open_entrance', lambda request: {'ok': True, 'state': 'confirmed'}):
            self.assertEqual(bticino_ipc.handle_request({'command': 'open_entrance', 'entrance': 'esterno'})['state'],
                             'confirmed')


if __name__ == '__main__':
    unittest.main()
