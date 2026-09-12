import importlib.util
import json
import unittest
from pathlib import Path


LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_entrance", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def invite(source="panel-a", address="10.0.0.10", call_id="random-call"):
    message = (
        "INVITE sip:private-user@private.example SIP/2.0\r\n"
        f"From: <sip:{source}@private.example>;tag=random-tag\r\n"
        "To: <sip:private-user@private.example>\r\n"
        f"Call-ID: {call_id}\r\n"
        f"Contact: <sip:{source}@{address}:5060>\r\n"
        "User-Agent: Secret Panel Model\r\n"
        "X-Device-Address: household-secret\r\n"
        "Content-Type: application/sdp\r\n\r\n"
        "v=0\r\n"
        f"o={source} 123 456 IN IP4 {address}\r\n"
        "s=Private entrance\r\n"
        f"c=IN IP4 {address}\r\n"
        "m=video 5000 RTP/SAVP 96\r\n"
        "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:super-secret-key\r\n"
    )
    return message.encode()


class EntranceFingerprintTests(unittest.TestCase):
    def test_same_entrance_is_stable_despite_random_call_id(self):
        key = b"local-test-key-that-is-never-published"
        first = MODULE.entrance_fingerprints(invite(call_id="one"), key)
        second = MODULE.entrance_fingerprints(invite(call_id="two"), key)
        self.assertEqual(first, second)

    def test_different_entrances_have_different_features(self):
        key = b"local-test-key-that-is-never-published"
        first = MODULE.entrance_fingerprints(invite("panel-a", "10.0.0.10"), key)
        second = MODULE.entrance_fingerprints(invite("panel-b", "10.0.0.11"), key)
        self.assertNotEqual(first["from"], second["from"])
        self.assertNotEqual(first["origin_address"], second["origin_address"])

    def test_output_contains_no_original_private_values_or_srtp_key(self):
        result = MODULE.entrance_fingerprints(invite(), b"test-key-at-least-16")
        encoded = json.dumps(result)
        for secret in (
            "panel-a", "10.0.0.10", "private-user", "private.example",
            "household-secret", "Secret Panel Model", "super-secret-key",
        ):
            self.assertNotIn(secret, encoded)


class EntranceVisualClassificationTests(unittest.TestCase):
    def signature(self, values):
        repeated = values * (MODULE.ENTRANCE_SIGNATURE_SIZE // len(values) + 1)
        return MODULE.normalized_luma_signature(bytes(repeated[:MODULE.ENTRANCE_SIGNATURE_SIZE]))

    def test_matches_nearest_profile(self):
        external = self.signature([10, 20, 30, 220])
        stairs = self.signature([220, 30, 20, 10])
        observed = self.signature([12, 22, 32, 218])
        name, distance, reason = MODULE.classify_entrance(
            observed, {"external": [external], "stairs": [stairs]}
        )
        self.assertEqual(name, "external")
        self.assertLess(distance, 0.01)
        self.assertEqual(reason, "matched")

    def test_rejects_ambiguous_match(self):
        reference = self.signature([10, 20, 30, 220])
        name, _, reason = MODULE.classify_entrance(
            reference, {"one": [reference], "two": [reference]}
        )
        self.assertIsNone(name)
        self.assertEqual(reason, "ambiguous")

    def test_rejects_incomplete_frame(self):
        with self.assertRaises(ValueError):
            MODULE.normalized_luma_signature(b"too short")
        with self.assertRaises(ValueError):
            MODULE.gradient_signature(b"too short")

    def test_gradient_ignores_uniform_brightness_offset(self):
        base = bytes((index % 180 for index in range(MODULE.ENTRANCE_SIGNATURE_SIZE)))
        brighter = bytes((value + 50 for value in base))
        first = MODULE.gradient_signature(base)
        second = MODULE.gradient_signature(brighter)
        self.assertLess(MODULE.signature_distance(first, second), 1e-9)


if __name__ == "__main__":
    unittest.main()
