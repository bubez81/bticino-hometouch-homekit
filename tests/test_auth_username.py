import importlib.util
import json
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
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


class SrtpKeyDerivationTests(unittest.TestCase):
    """RFC 3711 appendix B.3 vectors: same result with or without the openssl command."""

    KEY = bytes.fromhex("E1F97A0D3E018BE0D64FA32C06DE4139")
    SALT = bytes.fromhex("0EC675AD498AFEEBB6960B3AABE6")

    def test_rfc3711_vectors(self):
        self.assertEqual(MODULE.aes_cm_prf(self.KEY, self.SALT, 0x00, 16).hex().upper(),
                         "C61E7A93744F39EE10734AFE3FF7A087")
        self.assertEqual(MODULE.aes_cm_prf(self.KEY, self.SALT, 0x02, 14).hex().upper(),
                         "30CBBC08863D8C85D49DB34A9AE1")
        self.assertTrue(MODULE.aes_cm_prf(self.KEY, self.SALT, 0x01, 20).hex().upper()
                        .startswith("CEBE321F6FF7716B6FD4AB49AF256A15"))

    def test_without_the_openssl_command(self):
        from unittest.mock import patch
        with patch.object(MODULE, "OPENSSL", "/nonexistent/openssl"):
            try:
                import cryptography  # noqa: F401
            except ImportError:
                self.skipTest("cryptography not installed")
            self.assertEqual(MODULE.aes_cm_prf(self.KEY, self.SALT, 0x00, 16).hex().upper(),
                             "C61E7A93744F39EE10734AFE3FF7A087")
