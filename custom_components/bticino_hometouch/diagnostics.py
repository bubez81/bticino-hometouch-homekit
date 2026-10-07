"""Diagnostics: the shape of the phone's credentials and certificate, never their values."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from . import HometouchConfigEntry
from .const import CONF_ENTRANCES, CONF_STORAGE


def _password_shape(value: str) -> str:
    if not value:
        return "assente"
    kind = "hex" if re.fullmatch(r"[0-9a-fA-F]+", value) else "alfanumerica" if value.isalnum() else "mista"
    return f"{len(value)} caratteri, {kind}"


def describe_phone(storage: Path) -> dict[str, Any]:
    """Blocking: reads the private files and reports only their structure."""
    result: dict[str, Any] = {}
    try:
        onboarding = json.loads((storage / "onboarding.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"onboarding": "assente"}

    def private(key: str) -> Path | None:
        value = onboarding.get(key)
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else storage / path

    files = {key: private(key) for key in ("credentials_file", "certificate_file", "private_key_file", "ca_file")}
    result["files"] = {key: (path is not None and path.exists()) for key, path in files.items()}
    account = ""
    try:
        credentials = json.loads(files["credentials_file"].read_text(encoding="utf-8"))
        account = str(credentials.get("SipAccount") or "")
        result["password"] = _password_shape(str(credentials.get("SipPassword") or ""))
        domain = str(onboarding.get("sip_domain") or "")
        result["account_in_sip_domain"] = bool(domain) and account.endswith("@" + domain)
    except (OSError, ValueError, AttributeError):
        result["password"] = "non leggibile"
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        cert = x509.load_pem_x509_certificate(files["certificate_file"].read_bytes())
        names = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        try:
            cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            san = True
        except x509.ExtensionNotFound:
            san = False
        key = load_pem_private_key(files["private_key_file"].read_bytes(), password=None)
        result["certificate"] = {
            "subject_fields": [attribute.oid._name for attribute in cert.subject],
            "cn_matches_account": bool(names) and names[0].value == account,
            "san": san,
            "signature": cert.signature_hash_algorithm.name if cert.signature_hash_algorithm else None,
            "public_key": type(cert.public_key()).__name__,
            "key_matches_certificate": key.public_key().public_numbers() == cert.public_key().public_numbers(),
            "not_valid_before": cert.not_valid_before_utc.isoformat(),
            "not_valid_after": cert.not_valid_after_utc.isoformat(),
        }
    except Exception as err:  # noqa: BLE001 - report, never raise from diagnostics
        result["certificate"] = f"non leggibile ({type(err).__name__})"
    return result


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HometouchConfigEntry) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mode": "standalone" if entry.data.get(CONF_STORAGE) else "external listener",
        "entrances": [e.get("address") for e in entry.options.get(CONF_ENTRANCES, []) if isinstance(e, dict)],
    }
    if entry.data.get(CONF_STORAGE):
        data["phone"] = await hass.async_add_executor_job(describe_phone, Path(entry.data[CONF_STORAGE]))
    return data
