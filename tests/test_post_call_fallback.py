import importlib.util
import unittest
from pathlib import Path


LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_fallback", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PostCallFallbackTests(unittest.TestCase):
    def test_command_is_loopback_only_and_time_bounded(self):
        command = MODULE.post_call_fallback_command(Path("/private/latest.jpg"), 300)
        rendered = " ".join(command)
        self.assertIn("-re", command)
        self.assertIn("-loop 1", rendered)
        self.assertIn("-t 300", rendered)
        self.assertIn("udp://127.0.0.1:22300", rendered)
        self.assertIn("+resend_headers", rendered)
        self.assertNotIn("0.0.0.0", rendered)

    def test_negative_duration_runs_continuously(self):
        command = MODULE.post_call_fallback_command(Path("/private/latest.jpg"), -1)
        self.assertNotIn("-t", command)


if __name__ == "__main__":
    unittest.main()
