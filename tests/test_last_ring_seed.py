import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_seed", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LastRingSeedTests(unittest.TestCase):
    def setUp(self):
        self.previous_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Rome"
        time.tzset()
        self.directory = tempfile.TemporaryDirectory()
        self.original = MODULE.SNAPSHOT_DIR
        MODULE.SNAPSHOT_DIR = Path(self.directory.name)

    def tearDown(self):
        MODULE.SNAPSHOT_DIR = self.original
        self.directory.cleanup()
        if self.previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.previous_tz
        time.tzset()

    def test_newest_snapshot_name_in_utc(self):
        for name in ("2026-10-04_16-26-40_a.jpg", "2026-10-05_17-03-14_b.jpg",
                     "2026-10-05_07-36-32_c.jpg", "notes.jpg", "2027-01-01_00-00-00_x.txt"):
            (MODULE.SNAPSHOT_DIR / name).write_bytes(b"x")
        self.assertEqual(MODULE.last_ring_from_snapshots(), "2026-10-05T15:03:14.000+00:00")

    def test_no_snapshots(self):
        self.assertIsNone(MODULE.last_ring_from_snapshots())


if __name__ == "__main__":
    unittest.main()
