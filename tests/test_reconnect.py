import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_reconnect", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReconnectDelayTests(unittest.TestCase):
    def test_missing_sip_target_fails_before_reconnect_loop(self):
        with patch.object(MODULE, "SERVER_IP", ""), \
             patch.object(MODULE, "DOMAIN", ""):
            with self.assertRaisesRegex(
                RuntimeError, "sip_server, sip_domain"
            ):
                MODULE.validate_runtime_settings()

    def test_complete_sip_target_is_accepted(self):
        with patch.object(MODULE, "SERVER_IP", "198.51.100.10"), \
             patch.object(MODULE, "DOMAIN", "sip.example.test"):
            MODULE.validate_runtime_settings()

    def test_common_homebrew_executable_is_found_with_restricted_path(self):
        with patch.object(MODULE, "CONFIG", {}), \
             patch.object(MODULE.shutil, "which", return_value=None), \
             patch.object(MODULE.Path, "is_file", return_value=True), \
             patch.object(MODULE.os, "access", return_value=True):
            self.assertEqual(
                MODULE.resolve_executable(
                    "ffmpeg", "TEST_UNUSED_FFMPEG", "ffmpeg",
                    ("/opt/homebrew/bin/ffmpeg",),
                ),
                "/opt/homebrew/bin/ffmpeg",
            )

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
