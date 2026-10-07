"""Diagnostics describe the phone's files without revealing them."""
import datetime
import json

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from custom_components.bticino_hometouch.diagnostics import describe_phone, last_camera_call


def test_describe_phone_reports_shapes_not_values(tmp_path):
    account = "bridge-example.com-0123456789AB@123.bs.iotleg.com"
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, account)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    private = tmp_path / "private" / "sip"
    private.mkdir(parents=True)
    (private / "client.cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (private / "client.key").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                           serialization.PrivateFormat.TraditionalOpenSSL,
                                                           serialization.NoEncryption()))
    (private / "sip_credentials.json").write_text(json.dumps({"SipAccount": account, "SipPassword": "Ab3dE6gH"}))
    (tmp_path / "onboarding.json").write_text(json.dumps({
        "sip_domain": "123.bs.iotleg.com", "credentials_file": "private/sip/sip_credentials.json",
        "certificate_file": "private/sip/client.cert.pem", "private_key_file": "private/sip/client.key",
        "ca_file": "private/sip/ca.pem"}))
    result = describe_phone(tmp_path)
    assert result["password"] == "8 caratteri, alfanumerica"
    assert result["account_in_sip_domain"] is True
    assert result["files"]["ca_file"] is False
    assert result["certificate"]["cn_matches_account"] is True
    assert result["certificate"]["key_matches_certificate"] is True
    assert result["certificate"]["subject_fields"] == ["commonName"]
    text = json.dumps(result)
    assert "Ab3dE6gH" not in text and "bridge" not in text and "123.bs" not in text


def test_last_camera_call_keeps_only_the_summary(tmp_path):
    (tmp_path / "camera-calls.log").write_text(
        "=== 2026-10-07 15:00:00 start_call candidate=1 audio=True\nSIP_STATUS=200\n"
        "[h264 @ 0x1] no frame!\nFotogramma privato: /tmp/x/frame.jpg\n"
        "15:00:10 MEDIA_PROGRESS rtp_packets=200 audio_packets=400 talk_bytes=8000 decoder_running=True\n"
        "15:00:20 PROBE accepted=True rtp_packets=400 audio_packets=800 cancel_sent=False termination_confirmed=True\n",
        encoding="utf-8")
    lines = last_camera_call(tmp_path)
    assert lines[0].startswith("=== ") and "talk_bytes=8000" in lines[2] and len(lines) == 4
    assert not any("Fotogramma" in line for line in lines)
