"""Automatic certificate renewal: expiry reading, key reuse, and the due/skip logic."""
import io
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from src import bticino_onboard as onboard

CN = "laura-me-C48105DC9F0A@1727541.bs.iotleg.com"
# Force the cryptography code paths (never the openssl CLI) so the test is hermetic.
NO_OPENSSL = "/nonexistent/openssl"


def write_key(path: Path):
    key = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return key


def cert_pem(public_key, signing_key, cn: str, days_valid: float) -> bytes:
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(public_key).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=days_valid))
            .sign(signing_key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM)


class FakeCloud:
    """Stands in for EliotClient: records the login and signs the CSR's public
    key (as the real CA does) into a far-future certificate, returned zipped."""

    created = []

    def __init__(self, portal):
        self.portal = portal
        self.login_args = None
        FakeCloud.created.append(self)

    def login(self, email, password):
        self.login_args = (email, password)

    def sign_certificate(self, common_name, csr_pem):
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
        assert csr.is_signature_valid
        ca_key = ec.generate_private_key(ec.SECP256R1())
        leaf = cert_pem(csr.public_key(), ca_key, common_name, 720)
        ca = cert_pem(ca_key.public_key(), ca_key, "BTicino Test CA", 3650)
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr(common_name + ".cert.pem", leaf)
            archive.writestr("ca-chain.cert.pem", ca)
        return blob.getvalue()


class CertificateDaysLeft(unittest.TestCase):
    def test_reads_days_until_expiry(self):
        with TemporaryDirectory() as tmp:
            cert = Path(tmp) / "c.pem"
            key = ec.generate_private_key(ec.SECP256R1())
            cert.write_bytes(cert_pem(key.public_key(), key, CN, 100))
            days = onboard.certificate_days_left(cert, openssl=NO_OPENSSL)
            self.assertIsNotNone(days)
            self.assertAlmostEqual(days, 100, delta=1)

    def test_missing_file_is_none(self):
        self.assertIsNone(onboard.certificate_days_left("/no/such.pem", openssl=NO_OPENSSL))


class RenewIfDue(unittest.TestCase):
    def setUp(self):
        FakeCloud.created = []
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cert = base / "client.cert.pem"
        self.key = base / "client.key"
        self.ca = base / "ca-chain.cert.pem"
        self.signing_key = write_key(self.key)
        self.ca.write_bytes(cert_pem(self.signing_key.public_key(), self.signing_key, "old CA", 3650))
        self.renewal = {"enabled": True, "email": "laura@example.com",
                        "password": "secret", "portal": "https://www.myhomeweb.com"}

    def tearDown(self):
        self.tmp.cleanup()

    def install_cert(self, days_valid):
        self.cert.write_bytes(cert_pem(self.signing_key.public_key(), self.signing_key, CN, days_valid))

    def renew(self, **over):
        kwargs = dict(cert_path=self.cert, key_path=self.key, ca_path=self.ca, common_name=CN,
                      renewal=self.renewal, openssl=NO_OPENSSL, client_factory=FakeCloud)
        kwargs.update(over)
        return onboard.renew_if_due(**kwargs)

    def test_renews_when_within_threshold(self):
        self.install_cert(10)  # expires in 10 days, threshold 30
        key_before = self.key.read_bytes()
        result = self.renew()
        self.assertTrue(result["renewed"])
        # The certificate now expires far in the future…
        self.assertGreater(onboard.certificate_days_left(self.cert, openssl=NO_OPENSSL), 700)
        # …the private key was NOT rewritten…
        self.assertEqual(self.key.read_bytes(), key_before)
        # …and the new certificate matches that same key.
        new_cert = x509.load_pem_x509_certificate(self.cert.read_bytes())
        on_disk = serialization.load_pem_private_key(key_before, password=None)
        self.assertEqual(
            new_cert.public_key().public_bytes(serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo),
            on_disk.public_key().public_bytes(serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo))
        self.assertEqual(FakeCloud.created[0].login_args, ("laura@example.com", "secret"))

    def test_skips_when_not_due(self):
        self.install_cert(200)  # well beyond the 30-day threshold
        result = self.renew()
        self.assertFalse(result["renewed"])
        self.assertEqual(result["reason"], "not_due")
        self.assertEqual(FakeCloud.created, [])  # the cloud was never contacted

    def test_disabled_does_nothing(self):
        self.install_cert(5)
        self.renewal["enabled"] = False
        result = self.renew()
        self.assertEqual(result, {"renewed": False, "reason": "disabled"})
        self.assertEqual(FakeCloud.created, [])

    def test_missing_password_does_nothing(self):
        self.install_cert(5)
        self.renewal.pop("password")
        self.assertEqual(self.renew()["reason"], "disabled")
        self.assertEqual(FakeCloud.created, [])

    def test_unreadable_certificate_skips(self):
        # No certificate on disk: skip, do not contact the cloud.
        result = self.renew()
        self.assertFalse(result["renewed"])
        self.assertEqual(result["reason"], "unknown_expiry")
        self.assertEqual(FakeCloud.created, [])

    def test_cloud_error_is_swallowed(self):
        self.install_cert(5)

        class Boom(FakeCloud):
            def sign_certificate(self, common_name, csr_pem):
                raise onboard.OnboardingError("Cloud HTTP 500")

        result = self.renew(client_factory=Boom)
        self.assertFalse(result["renewed"])
        self.assertEqual(result["reason"], "error")
        # The old certificate is left in place, unharmed.
        self.assertAlmostEqual(onboard.certificate_days_left(self.cert, openssl=NO_OPENSSL), 5, delta=1)


class RenewalFileIO(unittest.TestCase):
    def test_write_then_load_roundtrip_and_permissions(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / onboard.RENEWAL_FILENAME
            onboard.write_renewal(path, email="a@b.com", password="pw")
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            data = onboard.load_renewal(path)
            self.assertEqual(data["email"], "a@b.com")
            self.assertEqual(data["password"], "pw")
            self.assertTrue(data["enabled"])
            self.assertEqual(data["portal"], onboard.DEFAULT_PORTAL)

    def test_load_missing_is_empty(self):
        self.assertEqual(onboard.load_renewal("/no/such/renewal.json"), {})


if __name__ == "__main__":
    unittest.main()
