#!/usr/bin/env python3
"""Install pre-provisioned credentials on the test host, preserving rollback files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

LABEL = "system/io.github.bubez81.bticino-hometouch"
FIELDS = {
    "sip_credentials.json": "credentials_file",
    "client.cert.pem": "certificate_file",
    "client.key": "private_key_file",
    "ca-chain.cert.pem": "ca_file",
}


def same_gateway(old, new):
    def domain(data):
        account = data.get("SipAccount")
        if not isinstance(account, str) or account.count("@") != 1:
            return ""
        return account.partition("@")[2].strip().lower()

    gateway = new.get("GatewayId")
    if gateway is None or not str(gateway).strip():
        return False
    expected = str(gateway).strip().lower() + ".bs.iotleg.com"
    if domain(new) != expected or domain(old) != expected:
        return False
    previous = old.get("GatewayId")
    return previous is None or str(previous).strip().lower() == str(gateway).strip().lower()


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit("Eseguire con sudo e la directory dei nuovi file.")
    source = Path(sys.argv[1]).resolve()
    config = json.loads(Path("/opt/bticino-sniffer/config.json").read_text())
    targets = {name: Path(config[field]) for name, field in FIELDS.items()}
    for name, target in targets.items():
        if not (source / name).is_file() or not target.is_file():
            raise SystemExit("File richiesto mancante: " + name)
    new = json.loads((source / "sip_credentials.json").read_text())
    old = json.loads(targets["sip_credentials.json"].read_text())
    if not same_gateway(old, new):
        raise SystemExit("Gateway diverso: nessuna modifica.")
    if not all(new.get(k) for k in ("SipAccount", "SipPassword", "IdDevice")):
        raise SystemExit("Credenziali incomplete: nessuna modifica.")
    if new["SipAccount"] == old.get("SipAccount"):
        raise SystemExit("Account già installato: nessuna modifica.")
    cert = source / "client.cert.pem"
    key = source / "client.key"
    subprocess.run(["/usr/bin/openssl", "x509", "-in", str(cert), "-checkend", "0", "-noout"], check=True, stdout=subprocess.DEVNULL)
    pub_cert = subprocess.check_output(["/usr/bin/openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
    pub_key = subprocess.check_output(["/usr/bin/openssl", "pkey", "-in", str(key), "-pubout"])
    if pub_cert != pub_key:
        raise SystemExit("Certificato e chiave non corrispondono: nessuna modifica.")
    subprocess.run(["/bin/launchctl", "print", LABEL], check=True, stdout=subprocess.DEVNULL)
    backup_root = Path("/opt/bticino-sniffer/backups")
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="dedicated-account-", dir=str(backup_root)))
    for name, target in targets.items():
        shutil.copy2(target, backup / name)
        os.chmod(backup / name, 0o600)
    shutil.copy2("/opt/bticino-sniffer/config.json", backup / "listener-config.json")
    os.chmod(backup / "listener-config.json", 0o600)
    print("Backup: " + str(backup), flush=True)

    def install(directory):
        for name, target in targets.items():
            stat = target.stat()
            fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=str(target.parent))
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write((directory / name).read_bytes())
                os.chown(temporary, stat.st_uid, stat.st_gid)
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    try:
        install(source)
        subprocess.run(["/bin/launchctl", "kickstart", "-k", LABEL], check=True)
    except Exception:
        install(backup)
        subprocess.run(["/bin/launchctl", "kickstart", "-k", LABEL], check=False)
        raise SystemExit("Migrazione fallita: ripristinati i file precedenti. Verificare il servizio.")
    print("Credenziali dedicate installate; riavvio richiesto. HomeKit non modificato.")
    print("Da verificare nei log: registrazione SIP e connessione IPC, poi prova funzionale.")


if __name__ == "__main__":
    main()
