#!/usr/bin/env python3
"""Validate a private listener configuration without printing its contents."""

import argparse
import json
import os
import shutil
from pathlib import Path


REQUIRED_TEXT = (
    "sip_server",
    "sip_domain",
    "credentials_file",
    "certificate_file",
    "private_key_file",
    "ca_file",
)
PLACEHOLDERS = ("example.invalid", "192.0.2.", "/path/to/")


def resolve_executable(value, name):
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise ValueError(f"{name} non eseguibile")
    if shutil.which(name):
        return Path(shutil.which(name))
    raise ValueError(f"{name} non trovato")


def validate(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("config.json assente, illeggibile o non valido") from exc
    if not isinstance(data, dict):
        raise ValueError("config.json deve contenere un oggetto JSON")

    for key in REQUIRED_TEXT:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"campo obbligatorio mancante: {key}")
        if any(marker in value for marker in PLACEHOLDERS):
            raise ValueError(f"sostituire il valore di esempio: {key}")

    port = data.get("sip_port", 5061)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("sip_port deve essere compreso tra 1 e 65535")

    for key in ("credentials_file", "certificate_file", "private_key_file", "ca_file"):
        if not Path(data[key]).expanduser().is_file():
            raise ValueError(f"file richiesto non trovato: {key}")

    try:
        credentials = json.loads(
            Path(data["credentials_file"]).expanduser().read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("credentials_file illeggibile o non valido") from exc
    account = credentials.get("SipAccount") or credentials.get("sip_account")
    password = (
        credentials.get("SipPassword")
        or credentials.get("Password")
        or credentials.get("sip_password")
    )
    if not account or not password:
        raise ValueError("credentials_file non contiene account e password SIP")

    resolve_executable(data.get("ffmpeg"), "ffmpeg")
    resolve_executable(data.get("openssl"), "openssl")
    classification = data.get("entrance_classification", {})
    if classification and not isinstance(classification, dict):
        raise ValueError("entrance_classification deve essere un oggetto")
    if classification.get("enabled", False):
        profiles = classification.get("profiles")
        if not isinstance(profiles, dict) or len(profiles) < 2:
            raise ValueError("servono almeno due profili visivi degli ingressi")
        for name, images in profiles.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("nome profilo ingresso non valido")
            if isinstance(images, str):
                images = [images]
            if not isinstance(images, list) or not images:
                raise ValueError(f"profilo ingresso privo di immagini: {name}")
            if any(not isinstance(image, str) or not Path(image).expanduser().is_file()
                   for image in images):
                raise ValueError(f"immagine privata non trovata per il profilo: {name}")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        validate(args.config.expanduser())
    except ValueError as exc:
        raise SystemExit(f"Configurazione non valida: {exc}")
    print("CONFIG_OK (contenuto privato non mostrato)")


if __name__ == "__main__":
    main()
