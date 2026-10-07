import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_auth_username", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AuthUsernameTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original = MODULE.CREDS_FILE
        MODULE.CREDS_FILE = Path(self.directory.name) / "sip_credentials.json"

    def tearDown(self):
        MODULE.CREDS_FILE = self.original
        self.directory.cleanup()

    def write(self, data):
        MODULE.CREDS_FILE.write_text(json.dumps(data))

    def test_digest_user_is_the_cloud_username(self):
        self.write({"SipAccount": "long-account@gw", "SipPassword": "p", "Username": "short"})
        self.assertEqual(MODULE.load_credentials()[0], "long-account")
        self.assertEqual(MODULE.load_auth_username("long-account"), "short")

    def test_older_files_fall_back_to_the_account_user(self):
        self.write({"SipAccount": "long-account@gw", "SipPassword": "p"})
        self.assertEqual(MODULE.load_auth_username("long-account"), "long-account")


if __name__ == "__main__":
    unittest.main()
