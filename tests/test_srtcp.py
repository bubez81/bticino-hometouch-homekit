import importlib.util
import unittest
from pathlib import Path


LISTENER = Path(__file__).parents[1] / "src" / "bticino_hometouch_listener.py"
SPEC = importlib.util.spec_from_file_location("bticino_listener", LISTENER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SrtcpTests(unittest.TestCase):
    def test_rfc3711_key_derivation_vector(self):
        master_key = bytes.fromhex("E1F97A0D3E018BE0D64FA32C06DE4139")
        master_salt = bytes.fromhex("0EC675AD498AFEEBB6960B3AABE6")
        expected = bytes.fromhex("CEBE321F6FF7716B6FD4AB49AF256A156D38BAA4")
        self.assertEqual(
            MODULE.aes_cm_prf(master_key, master_salt, 0x01, 20), expected
        )

    def test_srtcp_pli_shape_and_no_key_material(self):
        material = bytes(range(30))
        packet = MODULE.make_srtcp_pli(material, 0x11223344, 0x55667788)
        self.assertTrue(packet.startswith(bytes.fromhex("80c9000111223344")))
        self.assertIn(bytes.fromhex("81ce00021122334455667788"), packet)
        self.assertEqual(packet[-14:-10], bytes(4))
        self.assertEqual(len(packet[-10:]), 10)
        self.assertNotIn(material, packet)


if __name__ == "__main__":
    unittest.main()
