import importlib.util
import json
import tempfile
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_gateway_discovery", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

INVITE = (b"INVITE sip:me@1.2.3.4 SIP/2.0\r\n"
          b"Contact: <sip:gw@192.168.100.164:5061;transport=tls>\r\n\r\n")


class GatewayDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [patch.object(MODULE, "GATEWAY_FILE", Path(self.directory.name) / "runtime" / "gateway-address.json"),
                        patch.object(MODULE, "SERVER_IP", "sipserver.bs.iotleg.com"),
                        patch.object(MODULE, "GATEWAY_DISCOVERY", True)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def learn(self, verified):
        listener = MODULE.HomtouchListener.__new__(MODULE.HomtouchListener)
        with patch.object(MODULE, "verify_gateway", side_effect=lambda a: a in verified) as verify, \
                patch.object(MODULE, "log"), patch.object(MODULE.threading, "Thread") as thread:
            thread.side_effect = lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})()
            listener.learn_gateway(INVITE, "203.0.113.9")
        return verify

    def test_verified_private_address_is_saved_and_used(self):
        verify = self.learn({"192.168.100.164"})
        verify.assert_called_once_with("192.168.100.164")  # the public media address is not tried
        self.assertEqual(MODULE.SERVER_IP, "192.168.100.164")
        saved = json.loads(MODULE.GATEWAY_FILE.read_text())
        self.assertEqual(saved["address"], "192.168.100.164")
        self.assertEqual(MODULE.GATEWAY_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(MODULE.discovered_gateway(), "192.168.100.164")

    def test_unverified_address_is_ignored(self):
        self.learn(set())
        self.assertEqual(MODULE.SERVER_IP, "sipserver.bs.iotleg.com")
        self.assertFalse(MODULE.GATEWAY_FILE.exists())

    def test_nothing_is_learnt_with_a_local_server(self):
        with patch.object(MODULE, "SERVER_IP", "192.168.100.164"):
            verify = self.learn({"192.168.100.164"})
        verify.assert_not_called()

    def test_saved_file_must_hold_a_private_address(self):
        MODULE.GATEWAY_FILE.parent.mkdir(parents=True)
        MODULE.GATEWAY_FILE.write_text(json.dumps({"address": "8.8.8.8", "seen": int(time.time())}))
        self.assertIsNone(MODULE.discovered_gateway())


if __name__ == "__main__":
    unittest.main()
