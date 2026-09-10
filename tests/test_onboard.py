import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from email.message import Message
from unittest.mock import Mock, patch
import urllib.error

from src.bticino_onboard import (
    EliotClient,
    OnboardingError,
    atomic_private_json,
    extract_certificates,
    discover_gateway_id,
    endpoint_summary,
    payload_json,
    split_sip_records,
    validate_identifier,
    main,
)


class OnboardTests(unittest.TestCase):
    class FakeClient:
        endpoint_count = 0
        created_requests = []

        def __init__(self, portal):
            self.auth_token = "test-session-token"

        def login(self, email, password):
            self.login_values = (email, password)

        def plants(self):
            return [{"PlantId": "plant-test", "PlantName": "Test plant"}]

        def gateways(self, plant_id):
            return [{"GatewayId": "gateway-test"}]

        def sip_accounts(self, plant_id, gateway_id):
            records = [
                {
                    "SipAccount": f"test-{index}@example.invalid",
                    "IdDevice": f"{index:012X}",
                }
                for index in range(self.endpoint_count)
            ]
            if self.created_requests:
                records.append({
                    **self.created_requests[-1],
                    "SipPassword": "generated-test-password",
                })
            return records

        def create_sip_account(self, account):
            self.created_requests.append(account)
            return {**account, "SipPassword": "generated-test-password"}

        def sign_certificate(self, common_name, csr):
            return b"test-certificate-archive"

    def setUp(self):
        self.FakeClient.endpoint_count = 0
        self.FakeClient.created_requests = []

    def test_login_lockout_warning(self):
        client = EliotClient()
        error = urllib.error.HTTPError(
            "https://example.invalid", 432, "", Message(),
            io.BytesIO(b'{"RemainingTries":1}'),
        )
        client.opener = Mock()
        client.opener.open.side_effect = error
        with self.assertRaisesRegex(OnboardingError, "Rimangono 1 tentativi"):
            client.login("user@example.invalid", "wrong")

    def test_cloud_error_body_is_never_exposed(self):
        client = EliotClient()
        client.auth_token = "test-token"
        error = urllib.error.HTTPError(
            "https://example.invalid", 500, "", Message(),
            io.BytesIO(b'{"email":"private-person@example.invalid"}'),
        )
        client.opener = Mock()
        client.opener.open.side_effect = error
        with self.assertRaises(OnboardingError) as raised:
            client.request("GET", "/test")
        self.assertEqual(str(raised.exception), "Cloud HTTP 500")
        self.assertNotIn("private-person", str(raised.exception))

    def test_pending_user_is_not_counted_as_endpoint(self):
        complete = {"SipAccount": "a@example", "IdDevice": "ABC"}
        pending = {"Username": "new@example"}
        provisioned, waiting = split_sip_records([complete, pending])
        self.assertEqual(provisioned, [complete])
        self.assertEqual(waiting, [pending])

    def test_endpoint_summary_redacts_secrets(self):
        account = {
            "DeviceName": "Old phone\nsecret",
            "SipAccount": "very-private-account@example.invalid",
            "IdDevice": "AABBCCDDEEFF",
            "Username": "private-user",
            "SipPassword": "must-never-appear",
        }
        summary = endpoint_summary(account)
        self.assertIn("Old phone secret", summary)
        self.assertNotIn("very-private-account", summary)
        self.assertNotIn("must-never-appear", summary)
        self.assertNotIn("private-user", summary)

    def test_gateway_fallback_for_invited_user(self):
        class Client:
            def gateways(self, plant_id):
                self.plant_id = plant_id
                return [{"GatewayId": "9876543"}]

        client = Client()
        self.assertEqual(
            discover_gateway_id(client, {"PlantId": "7"}, "7"), "9876543"
        )
        self.assertEqual(client.plant_id, "7")

    def test_payload_plain_and_wrapped(self):
        self.assertEqual(payload_json(b'[{"PlantId":"7"}]')[0]["PlantId"], "7")
        wrapped = json.dumps({"payload": '[{"PlantId":"8"}]'}).encode()
        self.assertEqual(payload_json(wrapped)[0]["PlantId"], "8")

    def test_identifier_validation(self):
        self.assertEqual(validate_identifier("abc", "id"), "abc")
        with self.assertRaises(OnboardingError):
            validate_identifier("../secret", "id")

    def test_private_json_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "value.json"
            atomic_private_json(path, {"secret": "value"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["secret"], "value")

    def test_certificate_zip_and_base64(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("nested/user.cert.pem", b"CERT")
            archive.writestr("ca-chain.cert.pem", b"CA")
        for blob in (stream.getvalue(), base64.b64encode(stream.getvalue())):
            with tempfile.TemporaryDirectory() as directory:
                cert = Path(directory) / "client.pem"
                ca = Path(directory) / "ca.pem"
                extract_certificates(blob, "user", cert, ca)
                self.assertEqual(cert.read_bytes(), b"CERT")
                self.assertEqual(ca.read_bytes(), b"CA")
                self.assertEqual(cert.stat().st_mode & 0o777, 0o600)

    def test_apply_refuses_to_mutate_at_capacity(self):
        self.FakeClient.endpoint_count = 20
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.bticino_onboard.EliotClient", self.FakeClient
        ), patch.dict(os.environ, {"TEST_BTICINO_PASSWORD": "test"}):
            with self.assertRaisesRegex(OnboardingError, "20/20"):
                main([
                    "--email", "bridge@example.invalid",
                    "--password-env", "TEST_BTICINO_PASSWORD",
                    "--output", directory,
                    "--apply",
                ])
        self.assertEqual(self.FakeClient.created_requests, [])

    def test_apply_writes_complete_private_configuration(self):
        def fake_key(_openssl, _common_name, key_path, csr_path):
            key_path.write_text("TEST KEY", encoding="ascii")
            key_path.chmod(0o600)
            csr_path.write_text("TEST CSR", encoding="ascii")
            return "TEST CSR"

        def fake_cert(_blob, _common_name, cert_path, ca_path):
            cert_path.write_text("TEST CERT", encoding="ascii")
            ca_path.write_text("TEST CA", encoding="ascii")
            cert_path.chmod(0o600)
            ca_path.chmod(0o600)

        with tempfile.TemporaryDirectory() as directory, patch(
            "src.bticino_onboard.EliotClient", self.FakeClient
        ), patch(
            "src.bticino_onboard.generate_key_and_csr", side_effect=fake_key
        ), patch(
            "src.bticino_onboard.extract_certificates", side_effect=fake_cert
        ), patch.dict(os.environ, {"TEST_BTICINO_PASSWORD": "test"}):
            self.assertEqual(main([
                "--email", "bridge@example.invalid",
                "--password-env", "TEST_BTICINO_PASSWORD",
                "--output", directory,
                "--device-id", "AABBCCDDEEFF",
                "--apply",
            ]), 0)
            output = Path(directory)
            config = json.loads((output / "config.json").read_text())
            credentials = json.loads(
                (output / "sip_credentials.json").read_text()
            )
            self.assertEqual(config["sip_domain"], "gateway-test.bs.iotleg.com")
            self.assertEqual(credentials["SipPassword"], "generated-test-password")
            for name in (
                "config.json", "sip_credentials.json", "selection.json",
                "client.key", "client.cert.pem", "ca-chain.cert.pem",
            ):
                self.assertEqual((output / name).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
