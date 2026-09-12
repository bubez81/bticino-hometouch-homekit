#!/usr/bin/env python3
"""Guided, privacy-preserving provisioning for BTicino HOMETOUCH.

The protocol implemented here was reconstructed from the Door Entry for
HOMETOUCH client.  Read-only discovery is the default.  Creating a SIP client
and requesting a certificate requires the explicit --apply switch.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import io
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PORTAL = "https://www.myhomeweb.com"
PROJECT_NAME = "MHT"
APP_VERSION = "doorentryforhometouch-android-legacy-1.5.1"
KNOWN_HOMETOUCH_SIP_LIMIT = 20


class OnboardingError(RuntimeError):
    pass


def redact(value: str) -> str:
    if not value:
        return "<vuoto>"
    if len(value) < 9:
        return "***"
    return value[:4] + "…" + value[-4:]


def payload_json(raw: bytes) -> Any:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnboardingError("Risposta cloud non valida") from exc
    # Some generations wrap the actual response in a payload field.
    if isinstance(value, dict) and "payload" in value:
        nested = value["payload"]
        if isinstance(nested, str):
            try:
                return json.loads(nested)
            except json.JSONDecodeError:
                return nested
        return nested
    return value


def records_with_identifier(value: Any, identifier: str) -> list[dict[str, Any]]:
    """Find API records across legacy response wrappers without logging values."""
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if identifier in item and id(item) not in seen:
                seen.add(id(item))
                found.append(item)
                return
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return found


def payload_shape(value: Any) -> str:
    """Return privacy-safe response structure for public diagnostics."""
    if isinstance(value, list):
        return f"lista({len(value)})"
    if isinstance(value, dict):
        child_lists = sorted(
            len(item) for item in value.values() if isinstance(item, list)
        )
        suffix = f", liste={child_lists}" if child_lists else ""
        return f"oggetto({len(value)} campi{suffix})"
    if value is None:
        return "null"
    return type(value).__name__


@dataclass
class CloudResponse:
    body: bytes
    headers: Any

    def json(self) -> Any:
        return payload_json(self.body)


class EliotClient:
    def __init__(self, portal: str = DEFAULT_PORTAL, timeout: float = 20.0):
        parsed = urllib.parse.urlparse(portal)
        if parsed.scheme != "https" or not parsed.netloc:
            raise OnboardingError("Il portale deve essere un URL HTTPS")
        self.portal = portal.rstrip("/")
        self.timeout = timeout
        self.auth_token = ""
        self.last_plants_payload: Any = None
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )

    def request(self, method: str, path: str, body: Any = None,
                authenticated: bool = True, response_kind: str = "json") -> CloudResponse:
        headers = {"prj_name": PROJECT_NAME, "Accept": "application/json"}
        if authenticated:
            if not self.auth_token:
                raise OnboardingError("Sessione non autenticata")
            headers["auth_token"] = self.auth_token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            self.portal + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                result = CloudResponse(response.read(), response.headers)
        except urllib.error.HTTPError as exc:
            raw_detail = exc.read(512)
            detail = raw_detail.decode("utf-8", "replace")
            if exc.code == 432:
                try:
                    remaining = json.loads(detail).get("RemainingTries")
                except (json.JSONDecodeError, AttributeError):
                    remaining = None
                suffix = (
                    f" Rimangono {remaining} tentativi."
                    if isinstance(remaining, int) else ""
                )
                raise OnboardingError(
                    "Password non valida: interrompere i tentativi per evitare "
                    f"il blocco dell’account.{suffix} Usare il recupero password."
                ) from exc
            # Cloud error bodies may echo account, plant, gateway or device
            # identifiers. Never print them in a tool intended for public bug
            # reports.
            raise OnboardingError(f"Cloud HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OnboardingError(f"Connessione al cloud non riuscita: {exc.reason}") from exc
        refreshed = result.headers.get("auth_token")
        if refreshed:
            self.auth_token = refreshed
        if response_kind == "json":
            result.json()  # fail early while no secrets have been written
        return result

    def login(self, email: str, password: str) -> None:
        response = self.request(
            "POST", "/eliot/users/sign_in",
            {"username": email, "pwd": password, "appVersion": APP_VERSION},
            authenticated=False,
        )
        token = response.headers.get("auth_token")
        if not token:
            raise OnboardingError("Login riuscito ma auth_token assente")
        self.auth_token = token

    def plants(self) -> list[dict[str, Any]]:
        value = self.request("GET", "/eliot/plants").json()
        self.last_plants_payload = value
        plants = records_with_identifier(value, "PlantId")
        if plants:
            return plants
        if isinstance(value, list):
            return []
        if isinstance(value, dict):
            return []
        raise OnboardingError("Elenco impianti in formato inatteso")

    def invitations(self) -> Any:
        return self.request("GET", "/eliot/invitations").json()

    def gateways(self, plant_id: str) -> list[dict[str, Any]]:
        path = "/eliot/plants/{}/gateway/".format(
            urllib.parse.quote(plant_id, safe="")
        )
        value = self.request("GET", path).json()
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            raise OnboardingError("Elenco gateway in formato inatteso")
        return [item for item in value if isinstance(item, dict)]

    def sip_accounts(self, plant_id: str, gateway_id: str) -> list[dict[str, Any]]:
        path = "/eliot/sip/users/plants/{}/gateway/{}".format(
            urllib.parse.quote(plant_id, safe=""),
            urllib.parse.quote(gateway_id, safe=""),
        )
        value = self.request("GET", path).json()
        if not isinstance(value, list):
            raise OnboardingError("Elenco SIP in formato inatteso")
        return [item for item in value if isinstance(item, dict)]

    def create_sip_account(self, account: dict[str, str]) -> dict[str, Any]:
        value = self.request("POST", "/eliot/sip/user", account).json()
        if isinstance(value, list) and value:
            value = value[0]
        if not isinstance(value, dict):
            raise OnboardingError("Account SIP restituito in formato inatteso")
        return value

    def sign_certificate(self, common_name: str, csr: str) -> bytes:
        return self.request(
            "POST", "/eliot/users/cert/signcert",
            {"CommonName": common_name, "CSR": csr}, response_kind="binary"
        ).body

    def probe_method(self, method: str, path: str) -> tuple[int, str]:
        """Ask an endpoint for supported verbs without changing its state."""
        headers = {
            "prj_name": PROJECT_NAME,
            "auth_token": self.auth_token,
            "Accept": "application/json",
        }
        req = urllib.request.Request(
            self.portal + path, headers=headers, method=method
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                return response.status, response.headers.get("Allow", "")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Allow", "")
        except urllib.error.URLError as exc:
            raise OnboardingError(f"Verifica OPTIONS non riuscita: {exc.reason}") from exc


def choose_plant(plants: list[dict[str, Any]], requested: str | None) -> dict[str, Any]:
    if requested:
        matches = [p for p in plants if str(p.get("PlantId")) == requested]
        if len(matches) != 1:
            raise OnboardingError("PlantId richiesto non trovato")
        return matches[0]
    if len(plants) == 1:
        return plants[0]
    if not sys.stdin.isatty():
        raise OnboardingError("Più impianti disponibili: specificare --plant-id")
    for index, plant in enumerate(plants, 1):
        print(f"  {index}. {plant.get('PlantName', 'Senza nome')}")
    try:
        selected = int(input("Scegli l’impianto: "))
        return plants[selected - 1]
    except (ValueError, IndexError) as exc:
        raise OnboardingError("Selezione non valida") from exc


def discover_gateway_id(client: EliotClient, plant: dict[str, Any],
                        plant_id: str) -> str:
    embedded = str(plant.get("GatewayId") or "").strip()
    if embedded:
        return validate_identifier(embedded, "GatewayId")
    gateways = client.gateways(plant_id)
    ids = []
    for gateway in gateways:
        gateway_id = str(gateway.get("GatewayId") or "").strip()
        if gateway_id and gateway_id not in ids:
            ids.append(gateway_id)
    if not ids:
        raise OnboardingError(
            "L’impianto è visibile ma non espone ancora alcun gateway; "
            "l’invito potrebbe non essere completamente attivo"
        )
    if len(ids) > 1:
        raise OnboardingError(
            "L’impianto contiene più gateway; la selezione guidata non è ancora supportata"
        )
    return validate_identifier(ids[0], "GatewayId")


def run_openssl(openssl: str, args: list[str]) -> None:
    try:
        subprocess.run([openssl, *args], check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise OnboardingError(f"OpenSSL non trovato: {openssl}") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace")[:240]
        raise OnboardingError(f"OpenSSL non riuscito: {message}") from exc


def generate_key_and_csr(openssl: str, common_name: str,
                         key_path: Path, csr_path: Path) -> str:
    run_openssl(openssl, ["ecparam", "-name", "prime256v1", "-genkey",
                          "-noout", "-out", str(key_path)])
    os.chmod(key_path, 0o600)
    safe_cn = common_name.replace("/", "_").replace("\x00", "")
    run_openssl(openssl, ["req", "-new", "-sha256", "-key", str(key_path),
                          "-subj", f"/CN={safe_cn}", "-out", str(csr_path)])
    return csr_path.read_text(encoding="ascii")


def extract_certificates(blob: bytes, common_name: str,
                         cert_path: Path, ca_path: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        try:
            archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(blob, validate=True)))
        except (ValueError, zipfile.BadZipFile) as exc:
            raise OnboardingError("Risposta certificato non riconosciuta") from exc
    wanted_cert = common_name + ".cert.pem"
    by_basename = {Path(name).name: name for name in archive.namelist()}
    cert_member = by_basename.get(wanted_cert)
    ca_member = by_basename.get("ca-chain.cert.pem")
    if not cert_member or not ca_member:
        raise OnboardingError("Archivio privo del certificato client o della CA")
    cert_path.write_bytes(archive.read(cert_member))
    ca_path.write_bytes(archive.read(ca_member))
    os.chmod(cert_path, 0o600)
    os.chmod(ca_path, 0o600)


def atomic_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".onboard-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def validate_identifier(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 200 or any(c in result for c in "\r\n/\\"):
        raise OnboardingError(f"{label} non valido")
    return result


def endpoint_summary(account: dict[str, Any]) -> str:
    name = str(account.get("DeviceName") or "Dispositivo senza nome")
    name = "".join(c if c.isprintable() and c not in "\r\n\t" else " " for c in name)
    name = name[:60]
    sip = redact(str(account.get("SipAccount") or ""))
    device_id = redact(str(account.get("IdDevice") or ""))
    username = redact(str(account.get("Username") or ""))
    return f"{name} — SIP {sip}, device {device_id}, utente {username}"


def split_sip_records(accounts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    provisioned = [a for a in accounts if a.get("SipAccount") and a.get("IdDevice")]
    pending = [a for a in accounts if a not in provisioned]
    return provisioned, pending


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Onboarding BTicino HOMETOUCH")
    parser.add_argument("--portal", default=DEFAULT_PORTAL)
    parser.add_argument("--email", help="account Door Entry dedicato")
    parser.add_argument("--password-env", default="BTICINO_DOORENTRY_PASSWORD",
                        help="variabile password; se assente viene richiesta senza eco")
    parser.add_argument("--plant-id")
    parser.add_argument("--device-name", default="Home Assistant Bridge")
    parser.add_argument("--device-id", help="ID esadecimale persistente di 12 caratteri")
    default_config_root = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ) / "bticino-hometouch"
    parser.add_argument("--output", type=Path, default=default_config_root)
    parser.add_argument("--openssl", default=shutil.which("openssl") or "openssl")
    parser.add_argument("--apply", action="store_true",
                        help="crea davvero account SIP e certificato")
    parser.add_argument("--list-endpoints", action="store_true",
                        help="mostra nomi e identificativi mascherati degli endpoint SIP")
    parser.add_argument("--probe-removal", type=int, metavar="NUMERO",
                        help="verifica in sola lettura il supporto alla rimozione dell’endpoint")
    parser.add_argument(
        "--diagnose-discovery", action="store_true",
        help="mostra solo la forma delle risposte impianti/inviti, senza valori",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    email = args.email or input("Email dell’account Door Entry dedicato: ").strip()
    if "@" not in email or any(c in email for c in "\r\n"):
        raise OnboardingError("Indirizzo email non valido")
    password = os.environ.get(args.password_env)
    if password is None:
        password = getpass.getpass("Password Door Entry (non verrà salvata): ")

    client = EliotClient(args.portal)
    print("Accesso al portale HOMETOUCH…")
    client.login(email, password)
    password = ""  # drop our reference as soon as login completes
    print(f"Sessione ottenuta: {redact(client.auth_token)}")

    plants = client.plants()
    if args.diagnose_discovery:
        invitations = client.invitations()
        invitation_plants = records_with_identifier(invitations, "PlantId")
        print(
            "Diagnosi discovery (nessun valore mostrato): "
            f"impianti={payload_shape(client.last_plants_payload)}, "
            f"record={len(plants)}; "
            f"inviti={payload_shape(invitations)}, "
            f"record-impianto={len(invitation_plants)}"
        )
    if not plants:
        suffix = (
            "; incollare soltanto la riga “Diagnosi discovery” nel bug report"
            if args.diagnose_discovery else
            "; riprovare con --diagnose-discovery"
        )
        raise OnboardingError("Nessun impianto visibile" + suffix)
    plant = choose_plant(plants, args.plant_id)
    plant_id = validate_identifier(plant.get("PlantId"), "PlantId")
    gateway_id = discover_gateway_id(client, plant, plant_id)
    print(f"Impianto: {plant.get('PlantName', 'Senza nome')}")
    print(f"Gateway: {redact(gateway_id)}")

    accounts = client.sip_accounts(plant_id, gateway_id)
    provisioned, pending = split_sip_records(accounts)
    print(f"Endpoint SIP provisionati: {len(provisioned)}")
    if pending:
        print(f"Utenti senza endpoint SIP: {len(pending)}")
    if args.list_endpoints:
        for index, account in enumerate(provisioned, 1):
            print(f"  {index:2}. {endpoint_summary(account)}")
        for index, account in enumerate(pending, len(provisioned) + 1):
            print(f"  {index:2}. IN ATTESA — {endpoint_summary(account)}")
    if args.probe_removal is not None:
        index = args.probe_removal
        if index < 1 or index > len(provisioned):
            raise OnboardingError("Numero endpoint da verificare non valido")
        target = provisioned[index - 1]
        sip_account = validate_identifier(target.get("SipAccount"), "SipAccount")
        device_id = validate_identifier(target.get("IdDevice"), "IdDevice")
        username = validate_identifier(target.get("Username"), "Username")
        encoded = urllib.parse.quote(sip_account, safe="")
        encoded_device = urllib.parse.quote(device_id, safe="")
        encoded_user = urllib.parse.quote(username, safe="")
        encoded_plant = urllib.parse.quote(plant_id, safe="")
        encoded_gateway = urllib.parse.quote(gateway_id, safe="")
        probes = [
            ("raccolta", "/eliot/sip/user"),
            ("singolare/account", f"/eliot/sip/user/{encoded}"),
            ("plurale/account", f"/eliot/sip/users/{encoded}"),
            ("singolare/device", f"/eliot/sip/user/{encoded_device}"),
            ("plurale/device", f"/eliot/sip/users/{encoded_device}"),
            ("utente/device", f"/eliot/sip/users/{encoded_user}/{encoded_device}"),
            (
                "impianto/gateway/device",
                f"/eliot/sip/users/plants/{encoded_plant}/gateway/"
                f"{encoded_gateway}/{encoded_device}",
            ),
            ("controllo inesistente", "/eliot/__bticino_onboard_route_probe__"),
        ]
        print(f"Verifica non distruttiva dell’endpoint {index}: {endpoint_summary(target)}")
        for label, path in probes:
            status, allow = client.probe_method("OPTIONS", path)
            methods = allow or "non dichiarati"
            head_status, _ = client.probe_method("HEAD", path)
            print(f"  {label}: OPTIONS HTTP {status}, metodi {methods}; HEAD HTTP {head_status}")
        print("Sono state inviate soltanto richieste OPTIONS e HEAD; nessun dato è stato modificato.")
    if not args.apply:
        print("Diagnosi completata. Nessuna modifica eseguita.")
        print("Ripetere con --apply soltanto dopo aver verificato account e impianto.")
        return 0

    if len(provisioned) >= KNOWN_HOMETOUCH_SIP_LIMIT:
        raise OnboardingError(
            f"Impianto pieno: {len(provisioned)}/{KNOWN_HOMETOUCH_SIP_LIMIT} "
            "endpoint SIP. Nessuna creazione tentata; liberare uno slot tramite "
            "assistenza BTicino o rimuovendo un utente dell’impianto non più usato."
        )

    device_id = (args.device_id or secrets.token_hex(6)).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", device_id):
        raise OnboardingError("--device-id deve contenere 12 caratteri esadecimali")
    local_part = email.replace("@", "-")
    sip_account = f"{local_part}-{device_id}@{gateway_id}.bs.iotleg.com"
    request_account = {
        "SipAccount": sip_account,
        "DeviceName": args.device_name,
        "GatewayId": gateway_id,
        "IdDevice": device_id,
    }
    print("Creazione endpoint SIP dedicato…")
    created = client.create_sip_account(request_account)
    # Some service versions return the account only on the subsequent GET.
    refreshed = client.sip_accounts(plant_id, gateway_id)
    matches = [a for a in refreshed if a.get("SipAccount") == sip_account]
    # Preserve one-time values, notably a SIP password returned only by POST.
    account = {**created, **matches[0]} if matches else created
    sip_password = account.get("SipPassword")
    if not sip_password:
        raise OnboardingError("Account creato ma password SIP non restituita")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    key_path = output / "client.key"
    csr_path = output / "client.csr.pem"
    cert_path = output / "client.cert.pem"
    ca_path = output / "ca-chain.cert.pem"
    print("Generazione della chiave privata locale e richiesta certificato…")
    csr = generate_key_and_csr(args.openssl, sip_account, key_path, csr_path)
    certificate_blob = client.sign_certificate(sip_account, csr)
    extract_certificates(certificate_blob, sip_account, cert_path, ca_path)
    csr_path.unlink(missing_ok=True)

    credentials = output / "sip_credentials.json"
    atomic_private_json(credentials, {
        "SipAccount": sip_account,
        "SipPassword": sip_password,
        "GatewayId": gateway_id,
        "IdDevice": device_id,
    })
    atomic_private_json(output / "selection.json", {
        "PlantId": plant_id,
        "GatewayId": gateway_id,
        "PlantName": plant.get("PlantName", ""),
    })
    sip_domain = sip_account.split("@", 1)[1]
    atomic_private_json(output / "config.json", {
        "base_dir": str(output / "runtime"),
        "ffmpeg": shutil.which("ffmpeg") or "ffmpeg",
        "openssl": args.openssl,
        "sip_server": "sipserver.bs.iotleg.com",
        "sip_port": 5061,
        "sip_domain": sip_domain,
        "credentials_file": str(credentials),
        "certificate_file": str(cert_path),
        "private_key_file": str(key_path),
        "ca_file": str(ca_path),
        "homekit_doorbell_name": "Videocitofono",
        "save_raw_sip": False,
    })
    print(f"Provisioning completato in {output}")
    print("I file sono privati (mode 600); non copiarli nel repository.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OnboardingError, KeyboardInterrupt) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        raise SystemExit(2)
