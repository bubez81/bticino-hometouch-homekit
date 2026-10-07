import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_onboard as onboard
import bticino_plugin_setup as setup


def archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("archive.xml", '<root><obj cid="10050" dev="2" where="0" descr="x"/>'
                                       '<obj cid="1005070" id="1005070" descr="y"/></root>')
    return buffer.getvalue()


class FakeClient:
    def __init__(self, portal):
        self.portal = portal
    def login(self, email, password):
        assert password == "secret"
    def plants(self):
        return [{"PlantId": "P1", "PlantName": "Casa"}]
    def plant_configuration(self, plant_id, gateway_id):
        return archive()
    def sip_accounts(self, plant_id, gateway_id):
        return []


class PluginSetupTests(unittest.TestCase):
    def run_setup(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        env = {"BTICINO_DOORENTRY_EMAIL": "bridge@example.com", "BTICINO_DOORENTRY_PASSWORD": "secret"}
        with patch.dict(os.environ, env), patch.object(onboard, "EliotClient", FakeClient), \
                redirect_stdout(out), redirect_stderr(err):
            code = setup.main(list(argv))
        self.assertNotIn("secret", out.getvalue() + err.getvalue())
        return code, json.loads(out.getvalue())

    def test_plants(self):
        code, result = self.run_setup("plants")
        self.assertEqual((code, result), (0, {"ok": True, "plants": [{"id": "P1", "name": "Casa"}]}))

    def test_apply_writes_private_files_and_suggests_panel_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            def provision(client, email, plant, plant_id, gateway_id, provisioned, output_dir, openssl, device_name,
                          reuse_endpoint=None, log=print):
                self.assertIsNone(reuse_endpoint)
                output_dir.mkdir(parents=True)
                path = output_dir / "config.json"
                onboard.atomic_private_json(path, {"sip_domain": "gw.example", "credentials_file": str(output_dir / "c.json")})
                self.assertEqual(device_name, "Homebridge BTicino")
                return path
            with patch.object(onboard, "discover_gateway_id", return_value="GW"), patch.object(onboard, "provision", provision):
                code, result = self.run_setup("apply", "--plant-id", "P1", "--storage", tmp)
            self.assertEqual(code, 0)
            self.assertEqual(result["entrances"], [{"name": "Ingresso", "address": "20"}])
            self.assertTrue(result["camera"])
            storage = Path(tmp)
            onboarding = json.loads((storage / "onboarding.json").read_text())
            self.assertEqual(onboarding["sip_domain"], "gw.example")
            self.assertEqual(onboarding["credentials_file"], "private/sip/c.json")
            self.assertEqual((storage / "onboarding.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((storage / "camera-candidates.json").read_text())["candidates"][0]["devaddr"], "20")
            # A second setup must not overwrite the bridge's identity.
            with patch.object(onboard, "discover_gateway_id", return_value="GW"):
                code, result = self.run_setup("apply", "--plant-id", "P1", "--storage", tmp)
            self.assertEqual(code, 1)
            self.assertIn("Già configurato", result["error"])

    def test_apply_reuses_an_endpoint_with_the_same_name(self):
        seen = {}
        class Existing(FakeClient):
            def sip_accounts(self, plant_id, gateway_id):
                return [{"SipAccount": "a@gw", "IdDevice": "0123456789AB", "DeviceName": "Home Assistant BTicino"},
                        {"SipAccount": "b@gw", "IdDevice": "BA9876543210", "DeviceName": "Homebridge BTicino"}]
        def provision(*args, reuse_endpoint=None, log=print):
            seen["reuse"] = reuse_endpoint
            raise onboard.OnboardingError("stop")
        with tempfile.TemporaryDirectory() as tmp, patch.object(onboard, "discover_gateway_id", return_value="GW"), \
                patch.object(onboard, "provision", provision):
            out = io.StringIO()
            env = {"BTICINO_DOORENTRY_EMAIL": "bridge@example.com", "BTICINO_DOORENTRY_PASSWORD": "secret"}
            with patch.dict(os.environ, env), patch.object(onboard, "EliotClient", Existing), redirect_stdout(out), \
                    redirect_stderr(io.StringIO()):
                setup.main(["apply", "--plant-id", "P1", "--storage", tmp, "--device-name", "Home Assistant BTicino"])
        self.assertEqual(seen["reuse"], "Home Assistant BTicino")

    def test_refresh_updates_credentials_without_creating(self):
        class Cloud(FakeClient):
            def sip_accounts(self, plant_id, gateway_id):
                assert (plant_id, gateway_id) == ("P1", "GW")
                return [{"SipAccount": "a@gw", "SipPassword": "same", "Username": "digest-user"},
                        {"SipAccount": "b@gw", "SipPassword": "other", "Username": "x"}]
            def create_sip_account(self, account):
                raise AssertionError("refresh must not create")

        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            private = storage / "private" / "sip"
            private.mkdir(parents=True)
            onboard.atomic_private_json(private / "sip_credentials.json", {"SipAccount": "a@gw", "SipPassword": "same"})
            onboard.atomic_private_json(private / "selection.json", {"PlantId": "P1", "GatewayId": "GW"})
            onboard.atomic_private_json(storage / "onboarding.json", {"credentials_file": "private/sip/sip_credentials.json"})
            out = io.StringIO()
            env = {"BTICINO_DOORENTRY_EMAIL": "bridge@example.com", "BTICINO_DOORENTRY_PASSWORD": "secret"}
            with patch.dict(os.environ, env), patch.object(onboard, "EliotClient", Cloud), redirect_stdout(out):
                self.assertEqual(setup.main(["refresh", "--storage", tmp]), 0)
            self.assertEqual(json.loads(out.getvalue()), {"ok": True, "updated": ["Username"]})
            saved = json.loads((private / "sip_credentials.json").read_text())
            self.assertEqual(saved, {"SipAccount": "a@gw", "SipPassword": "same", "Username": "digest-user"})
            self.assertEqual((private / "sip_credentials.json").stat().st_mode & 0o777, 0o600)

    def test_missing_credentials_is_reported_not_raised(self):
        out = io.StringIO()
        with patch.dict(os.environ, {"BTICINO_DOORENTRY_EMAIL": "", "BTICINO_DOORENTRY_PASSWORD": ""}), redirect_stdout(out):
            self.assertEqual(setup.main(["plants"]), 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
