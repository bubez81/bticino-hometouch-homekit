import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validate_config import validate


class ValidateConfigTests(unittest.TestCase):
    def make_config(self, root):
        credentials = root / "credentials.json"
        credentials.write_text(json.dumps({
            "SipAccount": "redacted@example.test",
            "SipPassword": "redacted",
        }))
        files = {}
        for name in ("cert", "key", "ca"):
            files[name] = root / name
            files[name].write_text("test")
        return {
            "sip_server": "198.51.100.10",
            "sip_domain": "sip.example.test",
            "sip_port": 5061,
            "credentials_file": str(credentials),
            "certificate_file": str(files["cert"]),
            "private_key_file": str(files["key"]),
            "ca_file": str(files["ca"]),
        }

    def test_valid_private_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps(self.make_config(root)))
            with patch("scripts.validate_config.shutil.which", return_value="/usr/bin/tool"):
                self.assertEqual(validate(config)["sip_port"], 5061)

    def test_example_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.make_config(root)
            data["sip_domain"] = "example.invalid"
            config = root / "config.json"
            config.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "valore di esempio"):
                validate(config)

    def test_missing_secret_file_is_rejected_without_showing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.make_config(root)
            data["private_key_file"] = str(root / "private-secret-name.key")
            config = root / "config.json"
            config.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "private_key_file") as caught:
                validate(config)
            self.assertNotIn("private-secret-name", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
