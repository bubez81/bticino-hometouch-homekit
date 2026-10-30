import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_ipc
from bticino_api import ConnectionHealth, EventBus


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ConnectionHealthTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.health = ConnectionHealth(self.clock)

    def test_normal_reconnection_stays_connected(self):
        self.health.registration()
        self.assertEqual(self.health.status(), 'connected')
        # The usual drop every 17 minutes: registered again within seconds.
        self.health.drop()
        self.clock.now += 5
        self.assertEqual(self.health.status(), 'connected')
        self.health.registration()
        self.assertEqual(self.health.status(), 'connected')

    def test_no_registration_for_a_minute_is_disconnected(self):
        # Never registered since the start.
        self.clock.now += ConnectionHealth.DOWN_AFTER - 1
        self.assertEqual(self.health.status(), 'connected')
        self.clock.now += 1
        self.assertEqual(self.health.status(), 'disconnected')
        self.health.registration()
        self.health.drop()
        self.clock.now += ConnectionHealth.DOWN_AFTER
        self.assertEqual(self.health.status(), 'disconnected')

    def test_repeated_drops_are_unstable_until_five_quiet_minutes(self):
        self.health.registration()
        for _ in range(ConnectionHealth.UNSTABLE_DROPS):
            self.health.drop()
            self.clock.now += 16
            self.health.registration()
        self.assertEqual(self.health.status(), 'unstable')
        self.clock.now += ConnectionHealth.STABLE_AFTER
        self.assertEqual(self.health.status(), 'connected')
        # One more ordinary drop does not bring the warning back.
        self.health.drop()
        self.health.registration()
        self.assertEqual(self.health.status(), 'connected')

    def test_drops_spread_over_more_than_ten_minutes_are_normal(self):
        self.health.registration()
        for _ in range(ConnectionHealth.UNSTABLE_DROPS * 2):
            self.health.drop()
            self.health.registration()
            self.clock.now += ConnectionHealth.UNSTABLE_WINDOW / (ConnectionHealth.UNSTABLE_DROPS - 1)
        self.assertEqual(self.health.status(), 'connected')

    def test_event_bus_state_and_ipc_report_it(self):
        bus = EventBus()
        bus.health = self.health
        bus.publish('sip_registered')
        self.assertEqual(bus.snapshot_state()['connection'], 'connected')
        with patch.object(bticino_ipc, '_connection_status', bus.connection_status):
            self.assertEqual(bticino_ipc.handle_request({'command': 'connection_status'}),
                             {'ok': True, 'connection': 'connected'})
        with patch.object(bticino_ipc, '_connection_status', None):
            self.assertEqual(bticino_ipc.handle_request({'command': 'connection_status'})['connection'], 'unknown')


if __name__ == '__main__':
    unittest.main()
