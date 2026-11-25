import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import bticino_hometouch_listener as listener


class HksvFeedTests(unittest.TestCase):
    def test_hksv_feed_port_is_dedicated(self):
        # HKSV must read its own loopback port, never the shared live/snapshot
        # feed (LIVE_VIDEO_PORT) — a stuck recorder on the shared port took down
        # notifications and the live view in the first HKSV attempt.
        self.assertEqual(listener.HKSV_FEED_PORT, listener.LIVE_VIDEO_PORT + 1)
        self.assertNotEqual(listener.HKSV_FEED_PORT, listener.LIVE_VIDEO_PORT)

    def test_ring_relay_command_tees_to_both_ports(self):
        # The ring's decrypted H.264 is copied to the shared feed AND to the
        # dedicated HKSV feed. Guard the source so the dedicated tee is not
        # dropped by a refactor.
        source = (Path(__file__).parents[1] / 'src' / 'bticino_hometouch_listener.py').read_text()
        self.assertIn('{LIVE_VIDEO_HOST}:{LIVE_VIDEO_PORT}?pkt_size=1316', source)
        self.assertIn('{LIVE_VIDEO_HOST}:{HKSV_FEED_PORT}?pkt_size=1316', source)


if __name__ == '__main__':
    unittest.main()
