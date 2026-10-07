#!/usr/bin/env python3
"""Setup steps for the Homebridge plugin's settings page, as JSON commands.

  plants                         list the plants the dedicated account can see
  apply --plant-id ID --storage  create the bridge's SIP endpoint, write the
                                 private files and suggest the first entrance
  refresh --storage              re-read the bridge's endpoint from the cloud and
                                 update its saved credentials (creates nothing)

The email and password come from BTICINO_DOORENTRY_EMAIL and
BTICINO_DOORENTRY_PASSWORD and are never written. Progress goes to stderr; the
only stdout line is the JSON result.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import bticino_onboard as onboard

DEVICE_NAME = "Homebridge BTicino"


def log(message):
    print(message, file=sys.stderr, flush=True)


def session(portal):
    email = os.environ.get("BTICINO_DOORENTRY_EMAIL", "").strip()
    password = os.environ.get("BTICINO_DOORENTRY_PASSWORD", "")
    if "@" not in email or any(c in email for c in "\r\n") or not password:
        raise onboard.OnboardingError("Email e password dell'account Door Entry dedicato sono obbligatorie")
    client = onboard.EliotClient(portal)
    client.login(email, password)
    return client, email


def suggested_entrances(configuration):
    """The entrance panel's own lock: address = dev + where, as the official app builds it."""
    entrances = []
    for row in onboard.camera_candidates(configuration):
        if row["cid"] == "10050" and not any(e["address"] == row["devaddr"] for e in entrances):
            entrances.append({"name": "Ingresso" if not entrances else f"Ingresso {len(entrances) + 1}",
                              "address": row["devaddr"]})
    return entrances


def plants_command(args):
    client, _ = session(args.portal)
    plants = client.plants()
    return {"ok": True, "plants": [{"id": str(p.get("PlantId")), "name": p.get("PlantName") or "Impianto"}
                                   for p in plants if p.get("PlantId")]}


def apply_command(args):
    client, email = session(args.portal)
    plants = client.plants()
    plant = onboard.choose_plant(plants, args.plant_id)
    plant_id = onboard.validate_identifier(plant.get("PlantId"), "PlantId")
    gateway_id = onboard.discover_gateway_id(client, plant, plant_id)
    storage = Path(args.storage).resolve()
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(storage, 0o700)
    private = storage / "private" / "sip"
    if private.exists() and any(private.iterdir()):
        raise onboard.OnboardingError("Già configurato: i file del bridge esistono già")
    accounts = client.sip_accounts(plant_id, gateway_id)
    provisioned, _ = onboard.split_sip_records(accounts)
    # An endpoint with this name left by an interrupted setup is reused, not duplicated.
    existing = [a for a in provisioned if a.get("DeviceName") == args.device_name]
    config_path = onboard.provision(client, email, plant, plant_id, gateway_id, provisioned, private,
                                    args.openssl, args.device_name,
                                    reuse_endpoint=args.device_name if existing else None, log=log)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    # Paths relative to the data folder: the plugin resolves them, so the
    # folder can be moved without editing them.
    for key, value in list(config.items()):
        if key.endswith("_file") and isinstance(value, str) and Path(value).is_absolute():
            try:
                config[key] = str(Path(value).resolve().relative_to(storage))
            except ValueError:
                pass
    onboard.atomic_private_json(storage / "onboarding.json", config)
    configuration = client.plant_configuration(plant_id, gateway_id)
    candidates = onboard.camera_candidates(configuration)
    if candidates:
        onboard.atomic_private_json(storage / "camera-candidates.json", {"version": 1, "candidates": candidates})
    return {"ok": True, "plant": plant.get("PlantName") or "Impianto",
            "entrances": suggested_entrances(configuration), "camera": bool(candidates)}


def refresh_command(args):
    """Bring the saved credentials up to date with the cloud's record of the same endpoint."""
    storage = Path(args.storage).resolve()
    try:
        onboarding = json.loads((storage / "onboarding.json").read_text(encoding="utf-8"))
        credentials_file = Path(onboarding["credentials_file"])
        if not credentials_file.is_absolute():
            credentials_file = storage / credentials_file
        credentials = json.loads(credentials_file.read_text(encoding="utf-8"))
        selection = json.loads((credentials_file.parent / "selection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as error:
        raise onboard.OnboardingError("Configurazione del bridge non trovata") from error
    client, _ = session(args.portal)
    plant_id = onboard.validate_identifier(selection.get("PlantId"), "PlantId")
    gateway_id = onboard.validate_identifier(selection.get("GatewayId"), "GatewayId")
    matches = [a for a in client.sip_accounts(plant_id, gateway_id)
               if a.get("SipAccount") == credentials.get("SipAccount")]
    if len(matches) != 1:
        raise onboard.OnboardingError("Il telefono del bridge non è più nell'elenco dell'impianto")
    updated = []
    for key in ("Username", "SipPassword"):
        value = matches[0].get(key)
        if isinstance(value, str) and value.strip() and credentials.get(key) != value.strip():
            credentials[key] = value.strip()
            updated.append(key)
    if updated:
        onboard.atomic_private_json(credentials_file, credentials)
    return {"ok": True, "updated": updated}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["plants", "apply", "refresh"])
    parser.add_argument("--portal", default=onboard.DEFAULT_PORTAL)
    parser.add_argument("--plant-id")
    parser.add_argument("--storage")
    parser.add_argument("--openssl", default=shutil.which("openssl") or "openssl")
    parser.add_argument("--device-name", default=DEVICE_NAME, help="name of the phone shown in the Door Entry app")
    args = parser.parse_args(argv)
    try:
        if args.command in ("apply", "refresh") and not args.storage:
            raise onboard.OnboardingError("--storage mancante")
        result = {"plants": plants_command, "apply": apply_command, "refresh": refresh_command}[args.command](args)
    except onboard.OnboardingError as error:
        result = {"ok": False, "error": str(error)}
    except Exception as error:  # report, never print secrets or tracebacks with values
        result = {"ok": False, "error": f"Errore imprevisto: {type(error).__name__}"}
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
