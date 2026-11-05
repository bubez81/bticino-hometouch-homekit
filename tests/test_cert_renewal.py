"""Automatic certificate renewal: expiry reading, key reuse, and the due/skip logic.

Uses the openssl CLI (like the production fallback) so it runs without the
optional `cryptography` package, as the unit-test CI job does.
"""
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_onboard as onboard

CN = "laura-me-C48105DC9F0A@1727541.bs.iotleg.com"
OPENSSL = shutil.which("openssl")


def gen_key(path):
    subprocess.run([OPENSSL, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(path)],
                   check=True, capture_output=True)


def self_signed(key, cert, cn, days):
    subprocess.run([OPENSSL, "req", "-x509", "-new", "-key", str(key), "-subj", f"/CN={cn}",
                    "-days", str(int(days)), "-sha256", "-out", str(cert)], check=True, capture_output=True)


def public_key_pem(path, from_cert):
    args = ([OPENSSL, "x509", "-in", str(path), "-pubkey", "-noout"] if from_cert
            else [OPENSSL, "pkey", "-in", str(path), "-pubout"])
    return subprocess.run(args, check=True, capture_output=True).stdout


class FakeCloud:
    """Stands in for EliotClient: records the login and signs the CSR into a
    far-future certificate with a test CA, returned zipped as the cloud does."""

    created = []

    def __init__(self, portal):
        self.portal = portal
        self.login_args = None
        FakeCloud.created.append(self)
        self.dir = Path(tempfile.mkdtemp())
        self.ca_key, self.ca_cert = self.dir / "ca.key", self.dir / "ca.pem"
        gen_key(self.ca_key)
        self_signed(self.ca_key, self.ca_cert, "BTicino Test CA", 3650)

    def login(self, email, password):
        self.login_args = (email, password)

    def sign_certificate(self, common_name, csr_pem):
        csr = self.dir / "in.csr"
        csr.write_text(csr_pem, encoding="ascii")
        leaf = self.dir / "leaf.pem"
        subprocess.run([OPENSSL, "x509", "-req", "-in", str(csr), "-CA", str(self.ca_cert),
                        "-CAkey", str(self.ca_key), "-CAcreateserial", "-days", "720", "-sha256",
                        "-out", str(leaf)], check=True, capture_output=True)
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr(common_name + ".cert.pem", leaf.read_bytes())
            archive.writestr("ca-chain.cert.pem", self.ca_cert.read_bytes())
        return blob.getvalue()


@unittest.skipUnless(OPENSSL, "openssl CLI required")
class CertificateDaysLeft(unittest.TestCase):
    def test_reads_days_until_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, cert = Path(tmp) / "k.pem", Path(tmp) / "c.pem"
            gen_key(key)
            self_signed(key, cert, CN, 100)
            days = onboard.certificate_days_left(cert, openssl=OPENSSL)
            self.assertIsNotNone(days)
            self.assertAlmostEqual(days, 100, delta=2)

    def test_missing_file_is_none(self):
        self.assertIsNone(onboard.certificate_days_left("/no/such.pem", openssl=OPENSSL))


@unittest.skipUnless(OPENSSL, "openssl CLI required")
class RenewIfDue(unittest.TestCase):
    def setUp(self):
        FakeCloud.created = []
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cert, self.key, self.ca = base / "client.cert.pem", base / "client.key", base / "ca-chain.cert.pem"
        gen_key(self.key)
        self_signed(self.key, self.ca, "old CA", 3650)
        self.renewal = {"enabled": True, "email": "laura@example.com",
                        "password": "secret", "portal": "https://www.myhomeweb.com"}

    def tearDown(self):
        self.tmp.cleanup()

    def install_cert(self, days):
        self_signed(self.key, self.cert, CN, days)

    def renew(self, **over):
        kwargs = dict(cert_path=self.cert, key_path=self.key, ca_path=self.ca, common_name=CN,
                      renewal=self.renewal, openssl=OPENSSL, client_factory=FakeCloud)
        kwargs.update(over)
        return onboard.renew_if_due(**kwargs)

    def test_renews_when_within_threshold(self):
        self.install_cert(10)  # expires in 10 days, threshold 30
        key_before = self.key.read_bytes()
        result = self.renew()
        self.assertTrue(result["renewed"])
        # The certificate now expires far in the future…
        self.assertGreater(onboard.certificate_days_left(self.cert, openssl=OPENSSL), 700)
        # …the private key was NOT rewritten…
        self.assertEqual(self.key.read_bytes(), key_before)
        # …and the new certificate matches that same key.
        self.assertEqual(public_key_pem(self.cert, from_cert=True), public_key_pem(self.key, from_cert=False))
        self.assertEqual(FakeCloud.created[0].login_args, ("laura@example.com", "secret"))

    def test_skips_when_not_due(self):
        self.install_cert(200)  # beyond the 30-day threshold
        result = self.renew()
        self.assertEqual(result["reason"], "not_due")
        self.assertEqual(FakeCloud.created, [])  # the cloud was never contacted

    def test_disabled_does_nothing(self):
        self.install_cert(5)
        self.renewal["enabled"] = False
        self.assertEqual(self.renew(), {"renewed": False, "reason": "disabled"})
        self.assertEqual(FakeCloud.created, [])

    def test_missing_password_does_nothing(self):
        self.install_cert(5)
        self.renewal.pop("password")
        self.assertEqual(self.renew()["reason"], "disabled")
        self.assertEqual(FakeCloud.created, [])

    def test_unreadable_certificate_skips(self):
        result = self.renew()  # no certificate on disk
        self.assertEqual(result["reason"], "unknown_expiry")
        self.assertEqual(FakeCloud.created, [])

    def test_cloud_error_is_swallowed(self):
        self.install_cert(5)

        class Boom(FakeCloud):
            def sign_certificate(self, common_name, csr_pem):
                raise onboard.OnboardingError("Cloud HTTP 500")

        result = self.renew(client_factory=Boom)
        self.assertEqual(result["reason"], "error")
        # The old certificate is left in place, unharmed.
        self.assertAlmostEqual(onboard.certificate_days_left(self.cert, openssl=OPENSSL), 5, delta=2)


class RenewalFileIO(unittest.TestCase):
    def test_write_then_load_roundtrip_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / onboard.RENEWAL_FILENAME
            onboard.write_renewal(path, email="a@b.com", password="pw")
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            data = onboard.load_renewal(path)
            self.assertEqual((data["email"], data["password"], data["enabled"], data["portal"]),
                             ("a@b.com", "pw", True, onboard.DEFAULT_PORTAL))

    def test_load_missing_is_empty(self):
        self.assertEqual(onboard.load_renewal("/no/such/renewal.json"), {})


if __name__ == "__main__":
    unittest.main()
