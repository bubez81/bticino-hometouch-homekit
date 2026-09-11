import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_reconnect", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReconnectDelayTests(unittest.TestCase):
    def test_stable_connection_recovers_almost_immediately(self):
        with patch.object(MODULE, "RECONNECT_INITIAL_DELAY", 0.25), \
             patch.object(MODULE, "RECONNECT_MAX_DELAY", 10.0), \
             patch.object(MODULE, "RECONNECT_STABLE_AFTER", 30.0):
            self.assertEqual(MODULE.next_reconnect_delay(10.0, 480.0), 0.25)

    def test_rapid_failures_back_off_without_exceeding_cap(self):
        with patch.object(MODULE, "RECONNECT_INITIAL_DELAY", 0.25), \
             patch.object(MODULE, "RECONNECT_MAX_DELAY", 10.0), \
             patch.object(MODULE, "RECONNECT_STABLE_AFTER", 30.0):
            delays = []
            delay = 0.25
            for _ in range(8):
                delay = MODULE.next_reconnect_delay(delay, 0.1)
                delays.append(delay)
            self.assertEqual(delays[:4], [0.5, 1.0, 2.0, 4.0])
            self.assertEqual(delays[-1], 10.0)


if __name__ == "__main__":
    unittest.main()
