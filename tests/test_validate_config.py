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

    def test_entrance_opening_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            base = self.make_config(root)
            cases = [
                ({"entrance_open_enabled": True, "entrances": {"scala": "20", "esterno": "21"}}, None),
                ({"entrance_open_enabled": True, "entrances": {}}, "almeno un ingresso"),
                ({"entrances": {"Scala": "20"}}, "ingressi non valida"),
                ({"entrances": {"scala": "2O"}}, "ingressi non valida"),
                ({"entrances": ["20"]}, "nome -> indirizzo"),
                ({"entrances": {"scala": "20"}, "entrance_pulse_seconds": 30}, "ingressi non valida"),
            ]
            with patch("scripts.validate_config.shutil.which", return_value="/usr/bin/tool"):
                for extra, error in cases:
                    config.write_text(json.dumps({**base, **extra}))
                    if error is None:
                        validate(config)
                    else:
                        with self.assertRaisesRegex(ValueError, error):
                            validate(config)

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
