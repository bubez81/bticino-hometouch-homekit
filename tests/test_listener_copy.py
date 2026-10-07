import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import sync_listener


class ListenerCopyTests(unittest.TestCase):
    def test_integration_listener_matches_sources(self):
        stale = [s.name for s in sync_listener.sources()
                 if (sync_listener.TARGET / s.name).read_bytes() != s.read_bytes()]
        extra = {p.name for p in sync_listener.TARGET.iterdir()} - {s.name for s in sync_listener.sources()}
        self.assertEqual((stale, extra), ([], set()), "run tools/sync_listener.py")


if __name__ == "__main__":
    unittest.main()
