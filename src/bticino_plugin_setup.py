#!/usr/bin/env python3
"""Setup steps for the Homebridge plugin's settings page, as JSON commands.

  plants                         list the plants the dedicated account can see
  apply --plant-id ID --storage  create the bridge's SIP endpoint, write the
                                 private files and suggest the first entrance

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
    config_path = onboard.provision(client, email, plant, plant_id, gateway_id, provisioned, private,
                                    args.openssl, DEVICE_NAME, log=log)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    onboard.atomic_private_json(storage / "onboarding.json", config)
    configuration = client.plant_configuration(plant_id, gateway_id)
    candidates = onboard.camera_candidates(configuration)
    if candidates:
        onboard.atomic_private_json(storage / "camera-candidates.json", {"version": 1, "candidates": candidates})
    return {"ok": True, "plant": plant.get("PlantName") or "Impianto",
            "entrances": suggested_entrances(configuration), "camera": bool(candidates)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["plants", "apply"])
    parser.add_argument("--portal", default=onboard.DEFAULT_PORTAL)
    parser.add_argument("--plant-id")
    parser.add_argument("--storage")
    parser.add_argument("--openssl", default=shutil.which("openssl") or "openssl")
    args = parser.parse_args(argv)
    try:
        if args.command == "apply" and not args.storage:
            raise onboard.OnboardingError("--storage mancante")
        result = plants_command(args) if args.command == "plants" else apply_command(args)
    except onboard.OnboardingError as error:
        result = {"ok": False, "error": str(error)}
    except Exception as error:  # report, never print secrets or tracebacks with values
        result = {"ok": False, "error": f"Errore imprevisto: {type(error).__name__}"}
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
