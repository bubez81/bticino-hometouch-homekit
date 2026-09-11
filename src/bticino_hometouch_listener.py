#!/usr/bin/env python3

import hashlib
import hmac
import base64
import json
import os
import re
import secrets
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


def load_public_config():
    path = Path(os.environ.get(
        "BTICINO_SNIFFER_CONFIG", "/opt/bticino-sniffer/config.json"
    ))
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


CONFIG = load_public_config()


def resolve_executable(config_key, environment_key, command, candidates):
    configured = CONFIG.get(config_key) or os.environ.get(environment_key)
    if configured:
        return configured
    discovered = shutil.which(command)
    if discovered:
        return discovered
    for candidate in candidates:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return command

# ============================================================
# HOMETOUCH SIP passive diagnostic listener
# ============================================================

BASE = Path(CONFIG.get("base_dir", "/opt/bticino-sniffer"))
LOGDIR = BASE / "logs"
SNAPSHOT_DIR = BASE / "snapshots"
RUNTIME_DIR = BASE / "runtime"
FFMPEG = resolve_executable(
    "ffmpeg", "BTICINO_FFMPEG", "ffmpeg",
    ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"),
)
OPENSSL = resolve_executable(
    "openssl", "BTICINO_OPENSSL", "openssl",
    ("/opt/homebrew/bin/openssl", "/usr/local/bin/openssl", "/usr/bin/openssl"),
)

CREDS_FILE = Path(CONFIG.get("credentials_file", "/opt/bticino-gateway/config/sip_credentials.json"))
CERT_FILE = Path(CONFIG.get("certificate_file", "/opt/bticino-gateway/certs/client.cert.pem"))
KEY_FILE = Path(CONFIG.get("private_key_file", "/opt/bticino-gateway/private/client.key"))
CA_FILE = Path(CONFIG.get("ca_file", "/opt/bticino-gateway/certs/ca-chain.cert.pem"))

SERVER_IP = CONFIG.get("sip_server") or os.environ.get("BTICINO_SIP_SERVER", "")
SERVER_PORT = int(CONFIG.get("sip_port", 5061))
DOMAIN = CONFIG.get("sip_domain") or os.environ.get("BTICINO_SIP_DOMAIN", "")

REGISTER_EXPIRES = 600
REFRESH_MARGIN = 90
RECONNECT_INITIAL_DELAY = float(CONFIG.get("reconnect_initial_delay", 0.25))
RECONNECT_MAX_DELAY = float(CONFIG.get("reconnect_max_delay", 10.0))
RECONNECT_STABLE_AFTER = float(CONFIG.get("reconnect_stable_after", 30.0))

USER_AGENT = "HOMETOUCH-Diagnostic-Listener/1.0"
MEDIA_TIMEOUT = 30
MEDIA_PORT_START = 2202
MEDIA_PORT_END = 2213
INTERNAL_MEDIA_PORT_START = 22202
INTERNAL_MEDIA_PORT_END = 22213
LIVE_VIDEO_HOST = "127.0.0.1"
LIVE_VIDEO_PORT = 22300
SNAPSHOT_HTTP_HOST = "127.0.0.1"
SNAPSHOT_HTTP_PORT = 8766
HOMEBRIDGE_HTTP_PORT = 8767
HOMEBRIDGE_DOORBELL_NAME = CONFIG.get("homekit_doorbell_name", "Videocitofono")
PLACEHOLDER_SNAPSHOT = RUNTIME_DIR / "snapshot-pending.jpg"
SAVE_RAW_SIP = bool(CONFIG.get("save_raw_sip", False))

RUNNING = True
PENDING_CALLS = set()
PENDING_LOCK = threading.Lock()


def next_reconnect_delay(previous_delay, connected_for):
    """Return a short delay after stable sessions and back off rapid failures."""
    initial = max(0.0, RECONNECT_INITIAL_DELAY)
    maximum = max(initial, RECONNECT_MAX_DELAY)
    if connected_for >= max(0.0, RECONNECT_STABLE_AFTER):
        return initial
    return min(maximum, max(initial, previous_delay * 2))


# ------------------------------------------------------------
# utilities
# ------------------------------------------------------------

def now():
    return datetime.now().isoformat(timespec="seconds")


def stamp():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def log(message):
    line = f"[{now()}] {message}"
    print(line, flush=True)

    BASE.mkdir(parents=True, exist_ok=True)

    with open(BASE / "listener.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def latest_snapshot():
    snapshots = [p for p in SNAPSHOT_DIR.glob("*.jpg") if p.is_file()]
    return max(snapshots, key=lambda p: p.stat().st_mtime, default=None)


def set_snapshot_pending(call_id, pending):
    with PENDING_LOCK:
        if pending:
            PENDING_CALLS.add(call_id)
        else:
            PENDING_CALLS.discard(call_id)


def snapshot_pending():
    with PENDING_LOCK:
        return bool(PENDING_CALLS)


def ensure_placeholder_snapshot():
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=0x252a33:s=400x288",
        "-frames:v", "1", "-q:v", "2", "-y",
        str(PLACEHOLDER_SNAPSHOT),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    os.chmod(PLACEHOLDER_SNAPSHOT, 0o600)


class SnapshotHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] != "/snapshot.jpg":
            self.send_error(404)
            return
        path = (PLACEHOLDER_SNAPSHOT if snapshot_pending() else latest_snapshot())
        if not path:
            self.send_error(503, "Snapshot non ancora disponibile")
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(503, "Snapshot temporaneamente non disponibile")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def start_snapshot_server():
    server = ThreadingHTTPServer(
        (SNAPSHOT_HTTP_HOST, SNAPSHOT_HTTP_PORT), SnapshotHandler
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"Snapshot HTTP locale: http://{SNAPSHOT_HTTP_HOST}:{SNAPSHOT_HTTP_PORT}/snapshot.jpg")
    return server


def ring_homekit_doorbell():
    url = (
        f"http://localhost:{HOMEBRIDGE_HTTP_PORT}/doorbell?"
        f"{quote(HOMEBRIDGE_DOORBELL_NAME)}"
    )
    try:
        with urlopen(url, timeout=3) as response:
            response.read(2048)
        log("HOMEKIT DOORBELL: evento inviato")
    except Exception as exc:
        log(f"HOMEKIT DOORBELL: invio fallito: {type(exc).__name__}: {exc}")


def md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def token(n=12):
    return secrets.token_hex(n)


def aes_cm_prf(master_key, master_salt, label, length):
    """RFC 3711 AES-CM PRF, with the default key-derivation rate of zero."""
    if len(master_key) != 16 or len(master_salt) != 14:
        raise ValueError("materiale master SRTP non valido")
    x = bytearray(master_salt)
    # label || r is right-aligned to the 112-bit master salt; r is zero.
    x[7] ^= label
    counter = int.from_bytes(x + b"\x00\x00", "big")
    blocks = b"".join(
        ((counter + i) & ((1 << 128) - 1)).to_bytes(16, "big")
        for i in range((length + 15) // 16)
    )
    result = subprocess.run(
        [OPENSSL, "enc", "-aes-128-ecb", "-K", master_key.hex(),
         "-nosalt", "-nopad"],
        input=blocks, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout[:length]


def make_srtcp_pli(master_material, sender_ssrc, media_ssrc, index=0):
    """Build authenticated, unencrypted compound SRTCP RR+SDES+PLI."""
    if len(master_material) != 30:
        raise ValueError("materiale SDES non valido")
    rr = b"\x80\xc9\x00\x01" + sender_ssrc.to_bytes(4, "big")
    cname = b"bticino-sniffer"
    sdes_body = (
        sender_ssrc.to_bytes(4, "big")
        + bytes((1, len(cname))) + cname + b"\x00"
    )
    sdes_body += b"\x00" * ((-len(sdes_body)) % 4)
    sdes = (
        b"\x81\xca"
        + (len(sdes_body) // 4).to_bytes(2, "big")
        + sdes_body
    )
    pli = (
        b"\x81\xce\x00\x02"
        + sender_ssrc.to_bytes(4, "big")
        + media_ssrc.to_bytes(4, "big")
    )
    plain = rr + sdes + pli
    # E=0: RTCP remains clear, but authentication is mandatory for SRTCP.
    trailer = (index & 0x7fffffff).to_bytes(4, "big")
    auth_key = aes_cm_prf(master_material[:16], master_material[16:], 0x04, 20)
    tag = hmac.new(auth_key, plain + trailer, hashlib.sha1).digest()[:10]
    return plain + trailer + tag


def load_credentials():
    data = json.loads(CREDS_FILE.read_text())

    account = (
        data.get("SipAccount")
        or data.get("sipAccount")
        or data.get("sip_account")
    )

    password = (
        data.get("SipPassword")
        or data.get("sipPassword")
        or data.get("sip_password")
    )

    if not account:
        raise RuntimeError("SipAccount non trovato in sip_credentials.json")

    if not password:
        raise RuntimeError("SipPassword non trovato in sip_credentials.json")

    # SipAccount può essere:
    # user@domain
    # sip:user@domain
    account = account.removeprefix("sip:")

    username = account.split("@", 1)[0]

    return username, account, password


def make_tls_context():
    ctx = ssl.create_default_context(cafile=str(CA_FILE))

    # Il certificato del 3488 usa CN sip:<domain>.
    # Manteniamo comunque la verifica della CA BTicino.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED

    ctx.load_cert_chain(
        certfile=str(CERT_FILE),
        keyfile=str(KEY_FILE)
    )

    return ctx


# ------------------------------------------------------------
# SIP parsing
# ------------------------------------------------------------

class SIPStream:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def read_message(self, timeout=5):
        self.sock.settimeout(timeout)

        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(16384)

            if not chunk:
                raise ConnectionError("connessione SIP chiusa")

            self.buffer += chunk

        header_end = self.buffer.index(b"\r\n\r\n") + 4

        header_blob = self.buffer[:header_end]
        header_text = header_blob.decode("utf-8", errors="replace")

        m = re.search(
            r"(?im)^Content-Length\s*:\s*(\d+)\s*$",
            header_text
        )

        body_len = int(m.group(1)) if m else 0
        total_len = header_end + body_len

        while len(self.buffer) < total_len:
            chunk = self.sock.recv(16384)

            if not chunk:
                raise ConnectionError("connessione SIP chiusa durante body")

            self.buffer += chunk

        raw = self.buffer[:total_len]
        self.buffer = self.buffer[total_len:]

        return raw


def sip_first_line(raw):
    return raw.split(b"\r\n", 1)[0].decode(
        "utf-8",
        errors="replace"
    )


def sip_headers(raw):
    text = raw.decode("utf-8", errors="replace")

    head = text.split("\r\n\r\n", 1)[0]

    result = {}
    multi = {}

    for line in head.split("\r\n")[1:]:
        if ":" not in line:
            continue

        name, value = line.split(":", 1)
        name = name.strip()
        value = value.strip()

        result[name.lower()] = value
        multi.setdefault(name.lower(), []).append(value)

    return result, multi


def sip_body(raw):
    parts = raw.split(b"\r\n\r\n", 1)

    if len(parts) != 2:
        return ""

    return parts[1].decode("utf-8", errors="replace")


def sdp_value(body, prefix):
    for line in body.replace("\r", "").split("\n"):
        if line.lower().startswith(prefix.lower()):
            return line[len(prefix):].strip()
    return None


def reserve_udp_pair(bind_ip, excluded_ports=(), start=MEDIA_PORT_START,
                     end=MEDIA_PORT_END):
    """Choose a free even RTP/RTCP pair inside the firewall-approved pool."""
    for port in range(start, end, 2):
        if port in excluded_ports or port + 1 in excluded_ports:
            continue
        rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rtcp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            rtp.bind((bind_ip, port))
            rtcp.bind((bind_ip, port + 1))
            return port
        except OSError:
            pass
        finally:
            rtp.close()
            rtcp.close()
    raise RuntimeError(
        f"nessuna coppia RTP/RTCP libera in UDP {start}-{end}"
    )


def status_code(raw):
    first = sip_first_line(raw)

    m = re.match(r"SIP/2\.0\s+(\d+)", first)

    return int(m.group(1)) if m else None


# ------------------------------------------------------------
# Digest authentication
# ------------------------------------------------------------

def parse_digest_challenge(value):
    params = {}

    if value.lower().startswith("digest "):
        value = value[7:]

    pattern = r'(\w+)=("([^"]*)"|([^,\s]+))'

    for match in re.finditer(pattern, value):
        key = match.group(1).lower()
        val = match.group(3) or match.group(4) or ""
        params[key] = val

    return params


def digest_authorization(
    username,
    password,
    method,
    uri,
    challenge
):
    realm = challenge["realm"]
    nonce = challenge["nonce"]

    qop_raw = challenge.get("qop", "")
    algorithm = challenge.get("algorithm", "MD5")

    if algorithm.upper() != "MD5":
        raise RuntimeError(
            f"Digest algorithm non supportato: {algorithm}"
        )

    ha1 = md5(f"{username}:{realm}:{password}")
    ha2 = md5(f"{method}:{uri}")

    parts = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
    ]

    if qop_raw:
        qops = [x.strip() for x in qop_raw.split(",")]

        qop = "auth" if "auth" in qops else qops[0]

        nc = "00000001"
        cnonce = token(8)

        response = md5(
            f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}"
        )

        parts += [
            f'response="{response}"',
            f"algorithm=MD5",
            f"qop={qop}",
            f"nc={nc}",
            f'cnonce="{cnonce}"',
        ]

    else:
        response = md5(f"{ha1}:{nonce}:{ha2}")

        parts += [
            f'response="{response}"',
            "algorithm=MD5",
        ]

    return "Digest " + ", ".join(parts)


# ------------------------------------------------------------
# SIP client
# ------------------------------------------------------------

class EarlyMediaCapture:
    """One short-lived FFmpeg receiver for a single INVITE."""

    def __init__(self, call_id, local_ip, local_port, internal_port, payload, fmtp,
                 remote_key, crypto_tag, media_order, audio, remote_ip,
                 remote_rtcp_port):
        self.call_id = call_id
        self.local_ip = local_ip
        self.local_port = local_port
        self.internal_port = internal_port
        self.payload = payload
        self.fmtp = fmtp
        self.remote_key = remote_key
        self.crypto_tag = crypto_tag
        self.media_order = media_order
        self.audio = audio
        self.remote_ip = remote_ip
        self.remote_rtcp_port = remote_rtcp_port
        self.audio_key = base64.b64encode(os.urandom(30)).decode("ascii")
        self.audio_sockets = []
        self.audio_packets = 0
        self.answer_key = base64.b64encode(os.urandom(30)).decode("ascii")
        self.process = None
        self.relay_sockets = []
        self.relay_sender = None
        self.relay_thread = None
        self.relay_stop = threading.Event()
        self.rtp_metadata_logged = False
        self.pli_sent = False
        self.feedback_ssrc = secrets.randbits(32) or 1
        self.snapshot_reported = False
        self.started = time.time()
        self.snapshot = SNAPSHOT_DIR / (
            f"{stamp()}_{re.sub(r'[^A-Za-z0-9_.-]', '_', call_id)[:60]}.jpg"
        )
        self.sdp_path = RUNTIME_DIR / f"media-{uuid.uuid4().hex}.sdp"

    @property
    def answer_sdp(self):
        fmtp_line = f"a=fmtp:{self.payload} {self.fmtp}\r\n" if self.fmtp else ""
        video = (
            f"m=video {self.local_port} RTP/SAVP {self.payload}\r\n"
            f"a=rtcp:{self.local_port + 1}\r\n"
            f"a=rtpmap:{self.payload} H264/90000\r\n"
            f"{fmtp_line}"
            "a=rtcp-fb:* trr-int 5000\r\n"
            "a=rtcp-fb:* ccm tmmbr\r\n"
            f"a=rtcp-fb:{self.payload} nack pli\r\n"
            f"a=rtcp-fb:{self.payload} ccm fir\r\n"
            "a=recvonly\r\n"
            f"a=crypto:{self.crypto_tag} AES_CM_128_HMAC_SHA1_80 "
            f"inline:{self.answer_key}\r\n"
        )
        media = []
        video_inserted = False
        for kind, proto, payloads in self.media_order:
            if kind == "audio" and self.audio:
                media.append(
                    f"m=audio {self.audio['local_port']} {proto} {self.audio['payload']}\r\n"
                    f"a=rtcp:{self.audio['local_port'] + 1}\r\n"
                    f"a=rtpmap:{self.audio['payload']} {self.audio['rtpmap']}\r\n"
                    "a=rtcp-fb:* trr-int 5000\r\n"
                    "a=rtcp-fb:* ccm tmmbr\r\n"
                    "a=recvonly\r\n"
                    f"a=crypto:{self.audio['crypto_tag']} AES_CM_128_HMAC_SHA1_80 "
                    f"inline:{self.audio_key}\r\n"
                )
            elif kind == "video" and not video_inserted:
                media.append(video)
                video_inserted = True
            else:
                media.append(f"m={kind} 0 {proto} {' '.join(payloads)}\r\n")
        return (
            "v=0\r\n"
            f"o=bticino-sniffer {int(time.time())} 1 IN IP4 {self.local_ip}\r\n"
            "s=Early media snapshot\r\n"
            f"c=IN IP4 {self.local_ip}\r\n"
            "t=0 0\r\n"
            + "".join(media)
        )

    def start(self):
        if self.audio:
            for port in (self.audio["local_port"], self.audio["local_port"] + 1):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((self.local_ip, port))
                sock.setblocking(False)
                self.audio_sockets.append(sock)
        for port in (self.local_port, self.local_port + 1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.local_ip, port))
            sock.setblocking(False)
            self.relay_sockets.append(sock)
        self.relay_sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.relay_thread = threading.Thread(target=self.relay_media, daemon=True)
        self.relay_thread.start()
        # FFmpeg reads the offerer's key because it decrypts packets sent by it.
        input_sdp = (
            "v=0\n"
            "o=- 0 0 IN IP4 127.0.0.1\n"
            "s=SRTP input\n"
            "c=IN IP4 127.0.0.1\n"
            "t=0 0\n"
            f"m=video {self.internal_port} RTP/SAVP {self.payload}\n"
            f"a=rtcp:{self.internal_port + 1}\n"
            f"a=rtpmap:{self.payload} H264/90000\n"
            + (f"a=fmtp:{self.payload} {self.fmtp}\n" if self.fmtp else "")
            + "a=rtcp-fb:* trr-int 5000\n"
            + "a=rtcp-fb:* ccm tmmbr\n"
            + f"a=rtcp-fb:{self.payload} nack pli\n"
            + f"a=rtcp-fb:{self.payload} ccm fir\n"
            + f"a=crypto:{self.crypto_tag} AES_CM_128_HMAC_SHA1_80 inline:{self.remote_key}\n"
            + "a=recvonly\n"
        )
        self.sdp_path.write_text(input_sdp, encoding="utf-8")
        os.chmod(self.sdp_path, 0o600)
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-protocol_whitelist", "file,udp,rtp,srtp,crypto",
            "-rw_timeout", str(MEDIA_TIMEOUT * 1000000),
            "-i", str(self.sdp_path),
            # Inoltra continuamente l'H.264 decifrato a Homebridge. Il flusso
            # resta confinato al loopback e termina con la sessione SIP.
            "-map", "0:v:0",
            "-c:v", "copy",
            "-mpegts_flags", "+resend_headers",
            "-f", "mpegts",
            f"udp://{LIVE_VIDEO_HOST}:{LIVE_VIDEO_PORT}?pkt_size=1316",
            "-map", "0:v:0",
            # Il primo IDR può essere incompleto: scarta l'avvio e salva
            # un frame successivo, quando il decoder si è stabilizzato.
            "-vf", "select=gte(n\\,10)",
            "-frames:v", "1", "-q:v", "2", "-y",
            str(self.snapshot),
        ]
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, close_fds=True,
        )
        set_snapshot_pending(self.call_id, True)

    def poll(self):
        for sock in self.audio_sockets:
            while True:
                try:
                    sock.recvfrom(65535)
                    self.audio_packets += 1
                except BlockingIOError:
                    break
        if (not self.snapshot_reported and self.snapshot.exists()
                and self.snapshot.stat().st_size > 0):
            os.chmod(self.snapshot, 0o600)
            set_snapshot_pending(self.call_id, False)
            self.snapshot_reported = True
            log(
                f"SNAPSHOT OK: {self.snapshot.name} "
                f"({self.snapshot.stat().st_size} byte); "
                f"audio_udp={self.audio_packets}; live=udp://{LIVE_VIDEO_HOST}:{LIVE_VIDEO_PORT}"
            )
        if not self.process or self.process.poll() is None:
            return False
        stderr = self.process.stderr.read()[-2000:].strip()
        if not self.snapshot_reported:
            set_snapshot_pending(self.call_id, False)
            log(f"SNAPSHOT FALLITO call-id={self.call_id} audio_udp={self.audio_packets}: {stderr or 'nessun frame decodificabile'}")
        self.close_audio()
        self.close_relay()
        self.cleanup_files()
        return True

    def stop(self, reason):
        set_snapshot_pending(self.call_id, False)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        log(f"Media chiuso call-id={self.call_id}: {reason}")
        self.close_audio()
        self.close_relay()
        self.cleanup_files()

    def relay_media(self):
        destinations = {
            self.relay_sockets[0]: ("127.0.0.1", self.internal_port),
            self.relay_sockets[1]: ("127.0.0.1", self.internal_port + 1),
        }
        while not self.relay_stop.is_set():
            try:
                ready, _, _ = select.select(self.relay_sockets, [], [], 0.25)
            except (OSError, ValueError):
                break
            for sock in ready:
                try:
                    packet, _ = sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    continue
                if sock is self.relay_sockets[0] and not self.rtp_metadata_logged:
                    if len(packet) >= 12 and packet[0] >> 6 == 2:
                        payload = packet[1] & 0x7f
                        marker = int(bool(packet[1] & 0x80))
                        sequence = int.from_bytes(packet[2:4], "big")
                        ssrc = int.from_bytes(packet[8:12], "big")
                        log(
                            f"RTP META call-id={self.call_id}: "
                            f"ssrc=0x{ssrc:08x} pt={payload} "
                            f"seq={sequence} marker={marker}"
                        )
                        self.rtp_metadata_logged = True
                        self.send_pli(ssrc)
                try:
                    self.relay_sender.sendto(packet, destinations[sock])
                except OSError:
                    pass

    def send_pli(self, media_ssrc):
        if self.pli_sent:
            return
        try:
            material = base64.b64decode(self.answer_key, validate=True)
            packet = make_srtcp_pli(
                material, self.feedback_ssrc, media_ssrc, index=0
            )
            self.relay_sockets[1].sendto(
                packet, (self.remote_ip, self.remote_rtcp_port)
            )
            self.pli_sent = True
            log(
                f"SRTCP PLI inviato call-id={self.call_id}: "
                f"media_ssrc=0x{media_ssrc:08x} -> "
                f"{self.remote_ip}:{self.remote_rtcp_port}"
            )
        except Exception as exc:
            log(
                f"SRTCP PLI fallito call-id={self.call_id}: "
                f"{type(exc).__name__}: {exc}"
            )

    def close_relay(self):
        self.relay_stop.set()
        if self.relay_thread and self.relay_thread.is_alive():
            self.relay_thread.join(timeout=1)
        for sock in self.relay_sockets:
            try:
                sock.close()
            except OSError:
                pass
        self.relay_sockets = []
        if self.relay_sender:
            try:
                self.relay_sender.close()
            except OSError:
                pass
            self.relay_sender = None

    def close_audio(self):
        for sock in self.audio_sockets:
            try:
                sock.close()
            except OSError:
                pass
        self.audio_sockets = []

    def cleanup_files(self):
        try:
            self.sdp_path.unlink()
        except FileNotFoundError:
            pass

class HomtouchListener:

    def __init__(self):
        self.username, self.account, self.password = load_credentials()

        self.sock = None
        self.stream = None

        self.local_ip = None
        self.local_port = None

        self.call_id = f"{uuid.uuid4().hex}@hometouch-listener"
        self.from_tag = token(8)
        self.instance_uuid = str(uuid.uuid4())

        self.cseq = 1

        self.registered = False
        self.next_refresh = 0
        self.media = {}
        self.dialog_tags = {}


    def connect(self):
        ctx = make_tls_context()

        raw = socket.create_connection(
            (SERVER_IP, SERVER_PORT),
            timeout=15
        )

        self.sock = ctx.wrap_socket(
            raw,
            server_hostname=DOMAIN
        )

        self.stream = SIPStream(self.sock)

        self.local_ip, self.local_port = self.sock.getsockname()[:2]

        log(
            f"TLS collegato: "
            f"{self.local_ip}:{self.local_port} -> "
            f"{SERVER_IP}:{SERVER_PORT}"
        )


    def send(self, text):
        self.sock.sendall(text.encode("utf-8"))


    def build_register(self, authorization=None, expires=REGISTER_EXPIRES):
        uri = f"sip:{DOMAIN}"

        identity = f"sip:{self.username}@{DOMAIN}"

        branch = f"z9hG4bK{token(8)}"

        contact = (
            f'<sip:{self.username}@'
            f'{self.local_ip}:{self.local_port};transport=tls>'
            f';+sip.instance="<urn:uuid:{self.instance_uuid}>"'
            f';reg-id=1'
        )

        cseq = self.cseq
        self.cseq += 1

        lines = [
            f"REGISTER {uri} SIP/2.0",
            (
                f"Via: SIP/2.0/TLS "
                f"{self.local_ip}:{self.local_port};"
                f"branch={branch};rport"
            ),
            "Max-Forwards: 70",
            f"From: <{identity}>;tag={self.from_tag}",
            f"To: <{identity}>",
            f"Call-ID: {self.call_id}",
            f"CSeq: {cseq} REGISTER",
            f"Contact: {contact};expires={expires}",
            f"Expires: {expires}",
            "Supported: replaces, outbound, gruu",
            (
                "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, "
                "REFER, NOTIFY, MESSAGE, SUBSCRIBE, INFO, UPDATE"
            ),
            f"User-Agent: {USER_AGENT}",
        ]

        if authorization:
            lines.append(f"Authorization: {authorization}")

        lines += [
            "Content-Length: 0",
            "",
            "",
        ]

        return "\r\n".join(lines)


    def wait_register_response(self):
        deadline = time.time() + 15

        while time.time() < deadline:
            remaining = max(1, deadline - time.time())

            raw = self.stream.read_message(
                timeout=min(remaining, 5)
            )

            first = sip_first_line(raw)

            log(f"REGISTER RX: {first}")

            code = status_code(raw)

            if code is not None:
                return raw

            # Se per assurdo arriva già qualche richiesta
            self.handle_request(raw)

        raise TimeoutError("nessuna risposta al REGISTER")


    def register(self):
        log("Invio REGISTER iniziale")

        self.send(self.build_register())

        response = self.wait_register_response()

        code = status_code(response)

        if code == 200:
            self.registration_success(response)
            return

        if code not in (401, 407):
            raise RuntimeError(
                f"REGISTER rifiutato: {sip_first_line(response)}"
            )

        headers, _ = sip_headers(response)

        challenge_value = (
            headers.get("www-authenticate")
            or headers.get("proxy-authenticate")
        )

        if not challenge_value:
            raise RuntimeError(
                "401/407 senza WWW-Authenticate/Proxy-Authenticate"
            )

        challenge = parse_digest_challenge(challenge_value)

        auth = digest_authorization(
            username=self.username,
            password=self.password,
            method="REGISTER",
            uri=f"sip:{DOMAIN}",
            challenge=challenge,
        )

        log("Challenge Digest ricevuta; invio REGISTER autenticato")

        self.send(
            self.build_register(
                authorization=auth
            )
        )

        response = self.wait_register_response()

        code = status_code(response)

        if code != 200:
            raise RuntimeError(
                "REGISTER autenticato fallito: "
                + sip_first_line(response)
            )

        self.registration_success(response)


    def registration_success(self, raw):
        headers, _ = sip_headers(raw)

        expiry = REGISTER_EXPIRES

        if headers.get("expires"):
            try:
                expiry = int(headers["expires"])
            except Exception:
                pass

        contact = headers.get("contact", "")

        match = re.search(
            r"expires\s*=\s*(\d+)",
            contact,
            flags=re.I,
        )

        if match:
            try:
                expiry = int(match.group(1))
            except Exception:
                pass

        # mai aspettare troppo vicino alla scadenza
        refresh_after = max(
            60,
            expiry - min(REFRESH_MARGIN, max(30, expiry // 5))
        )

        self.next_refresh = time.time() + refresh_after
        self.registered = True

        log(
            f"REGISTRAZIONE SIP OK — expires={expiry}s, "
            f"rinnovo fra circa {refresh_after}s"
        )


    def respond_basic(self, raw, code, reason, to_tag=None, body=None):
        headers, multi = sip_headers(raw)

        lines = [
            f"SIP/2.0 {code} {reason}"
        ]

        for via in multi.get("via", []):
            lines.append(f"Via: {via}")

        for hname, pretty in [
            ("from", "From"),
            ("to", "To"),
            ("call-id", "Call-ID"),
            ("cseq", "CSeq"),
        ]:
            if headers.get(hname):
                value = headers[hname]
                if hname == "to" and to_tag and ";tag=" not in value.lower():
                    value += f";tag={to_tag}"
                lines.append(
                    f"{pretty}: {value}"
                )

        lines.append(f"User-Agent: {USER_AGENT}")
        if body is not None:
            lines += ["Content-Type: application/sdp", f"Content-Length: {len(body.encode('utf-8'))}", "", body]
        else:
            lines += ["Content-Length: 0", "", ""]

        self.send("\r\n".join(lines))


    def parse_video_offer(self, raw):
        body = sip_body(raw)
        lines = body.replace("\r", "").split("\n")
        media_order = []
        for line in lines:
            if line.startswith("m="):
                parts = line[2:].split()
                if len(parts) >= 4:
                    media_order.append((parts[0], parts[2], parts[3:]))
        video_index = next((i for i, x in enumerate(lines) if x.startswith("m=video ")), None)
        if video_index is None:
            raise RuntimeError("INVITE senza m=video")
        media = lines[video_index:]
        next_media = next((i for i, x in enumerate(media[1:], 1) if x.startswith("m=")), len(media))
        media = media[:next_media]
        mparts = media[0].split()
        remote_rtp_port = int(mparts[1])
        remote_rtcp_port = remote_rtp_port + 1
        for line in media:
            match = re.match(r"a=rtcp:(\d+)", line, re.I)
            if match:
                remote_rtcp_port = int(match.group(1))
                break
        remote_ip = sdp_value("\n".join(media), "c=IN IP4 ")
        if not remote_ip:
            remote_ip = sdp_value(body, "c=IN IP4 ") or SERVER_IP
        offered_payloads = mparts[3:]
        payload = None
        for line in media:
            match = re.match(r"a=rtpmap:(\d+)\s+H264/90000", line, re.I)
            if match and match.group(1) in offered_payloads:
                payload = match.group(1)
                break
        if payload is None:
            raise RuntimeError("nessun payload H264/90000 offerto")
        fmtp = ""
        for line in media:
            match = re.match(rf"a=fmtp:{re.escape(payload)}\s+(.+)", line, re.I)
            if match:
                fmtp = match.group(1).strip()
                break
        selected = None
        for line in media:
            match = re.match(
                r"a=crypto:(\d+)\s+AES_CM_128_HMAC_SHA1_80\s+inline:([^|\s]+)",
                line, re.I,
            )
            if match:
                selected = (match.group(1), match.group(2))
                break
        if selected is None:
            raise RuntimeError("AES_CM_128_HMAC_SHA1_80 SDES non offerto")
        try:
            key = base64.b64decode(selected[1], validate=True)
        except Exception as exc:
            raise RuntimeError(f"chiave SDES non valida: {exc}") from exc
        if len(key) != 30:
            raise RuntimeError(f"chiave SDES lunga {len(key)} byte, attesi 30")
        audio = None
        audio_index = next((i for i, x in enumerate(lines) if x.startswith("m=audio ")), None)
        if audio_index is not None:
            audio_lines = lines[audio_index:]
            audio_end = next((i for i, x in enumerate(audio_lines[1:], 1) if x.startswith("m=")), len(audio_lines))
            audio_lines = audio_lines[:audio_end]
            audio_parts = audio_lines[0].split()
            audio_payloads = audio_parts[3:]
            audio_map = None
            for line in audio_lines:
                match = re.match(r"a=rtpmap:(\d+)\s+([^\s]+)", line, re.I)
                if match and match.group(1) in audio_payloads:
                    audio_map = (match.group(1), match.group(2))
                    break
            audio_crypto = None
            for line in audio_lines:
                match = re.match(
                    r"a=crypto:(\d+)\s+AES_CM_128_HMAC_SHA1_80\s+inline:([^|\s]+)",
                    line, re.I,
                )
                if match:
                    audio_crypto = (match.group(1), match.group(2))
                    break
            if audio_map and audio_crypto:
                audio = {
                    "payload": audio_map[0], "rtpmap": audio_map[1],
                    "crypto_tag": audio_crypto[0], "remote_key": audio_crypto[1],
                }
        return (payload, fmtp, selected[0], selected[1], media_order, audio,
                remote_ip, remote_rtcp_port)


    def start_early_media(self, raw):
        headers, _ = sip_headers(raw)
        call_id = headers.get("call-id", uuid.uuid4().hex)
        (payload, fmtp, crypto_tag, remote_key, media_order, audio,
         remote_ip, remote_rtcp_port) = self.parse_video_offer(raw)
        local_port = reserve_udp_pair(self.local_ip)
        internal_port = reserve_udp_pair(
            "127.0.0.1", start=INTERNAL_MEDIA_PORT_START,
            end=INTERNAL_MEDIA_PORT_END,
        )
        if audio:
            audio["local_port"] = reserve_udp_pair(
                self.local_ip, {local_port, local_port + 1}
            )
        capture = EarlyMediaCapture(
            call_id, self.local_ip, local_port, internal_port, payload, fmtp,
            remote_key, crypto_tag, media_order, audio, remote_ip,
            remote_rtcp_port,
        )
        capture.start()
        # Give FFmpeg a brief chance to bind before advertising the port.
        time.sleep(0.15)
        if capture.process.poll() is not None:
            error = capture.process.stderr.read()[-2000:].strip()
            set_snapshot_pending(call_id, False)
            capture.cleanup_files()
            raise RuntimeError(f"FFmpeg non ha aperto RTP: {error}")
        self.media[call_id] = capture
        to_tag = self.dialog_tags.setdefault(call_id, token(8))
        self.respond_basic(raw, 183, "Session Progress", to_tag, capture.answer_sdp)
        log(
            f"183 inviato: call-id={call_id} RTP/SRTP {self.local_ip}:{local_port} "
            f"H264 PT={payload} crypto-tag={crypto_tag} "
            f"audio={audio['local_port'] if audio else 'rifiutato'}"
        )
        # HomeKit deve notificare subito; la snapshot pulita continua a essere
        # generata in parallelo e sostituisce automaticamente quella precedente.
        threading.Thread(target=ring_homekit_doorbell, daemon=True).start()


    def maintain_media(self):
        for call_id, capture in list(self.media.items()):
            if capture.poll():
                self.media.pop(call_id, None)
            elif time.time() - capture.started > MEDIA_TIMEOUT + 5:
                capture.stop("timeout")
                self.media.pop(call_id, None)


    def stop_media_for(self, raw, reason):
        headers, _ = sip_headers(raw)
        call_id = headers.get("call-id", "")
        capture = self.media.pop(call_id, None)
        if capture:
            capture.stop(reason)


    def save_raw(self, raw, kind):
        if not SAVE_RAW_SIP:
            return None
        filename = (
            LOGDIR
            / f"{stamp()}_{kind}_{uuid.uuid4().hex[:8]}.sip"
        )

        filename.write_bytes(raw)

        os.chmod(filename, 0o600)

        return filename


    def analyse_invite(self, raw):
        headers, _ = sip_headers(raw)
        body = sip_body(raw)

        path = self.save_raw(raw, "INVITE")

        log("=" * 70)
        if path:
            log(f"INVITE SALVATO: {path.name}")
        else:
            log("INVITE RAW: salvataggio disabilitato")

        for key in (
            "from",
            "to",
            "call-id",
            "contact",
            "user-agent",
        ):
            if key in headers:
                log(
                    f"{key.upper()}: "
                    f"{headers[key]}"
                )

        # Cerca DEVADDR sia negli header sia nell'SDP
        whole = raw.decode(
            "utf-8",
            errors="replace"
        )

        devaddr_matches = re.findall(
            r"(?im)^.*DEVADDR.*$",
            whole
        )

        if devaddr_matches:
            for item in devaddr_matches:
                log(f"DEVADDR: {item.strip()}")
        else:
            log("DEVADDR: non trovato")

        video_lines = re.findall(
            r"(?im)^m=video.*$",
            body
        )

        if video_lines:
            for item in video_lines:
                log(f"VIDEO: {item.strip()}")
        else:
            log("VIDEO: nessuna riga m=video")

        h264 = re.findall(
            r"(?im)^a=rtpmap:.*H264.*$",
            body
        )

        for item in h264:
            log(f"H264: {item.strip()}")

        fmtp = re.findall(
            r"(?im)^a=fmtp:.*$",
            body
        )

        for item in fmtp:
            log(f"FMTP: {item.strip()}")

        crypto = re.findall(
            r"(?im)^a=crypto:.*$",
            body
        )

        if crypto:
            log(
                f"SRTP: presenti {len(crypto)} "
                f"parametri crypto "
                f"({'conservati nel file raw' if path else 'non salvati'})"
            )

        conn_lines = re.findall(
            r"(?im)^c=IN\s+IP4\s+.*$",
            body
        )

        for item in conn_lines:
            log(f"CONNECTION: {item.strip()}")

        log("=" * 70)


    def handle_request(self, raw):
        first = sip_first_line(raw)

        method = first.split(" ", 1)[0].upper()

        log(f"SIP RX: {first}")

        if method == "INVITE":
            self.analyse_invite(raw)
            self.respond_basic(
                raw,
                100,
                "Trying"
            )
            log("100 Trying inviato")
            try:
                self.start_early_media(raw)
            except Exception as exc:
                log(f"Early media non avviato: {type(exc).__name__}: {exc}")

        elif method == "OPTIONS":
            self.save_raw(raw, "OPTIONS")
            self.respond_basic(
                raw,
                200,
                "OK"
            )

        elif method == "CANCEL":
            self.save_raw(
                raw,
                "CANCEL"
            )

            self.respond_basic(
                raw,
                200,
                "OK"
            )
            self.stop_media_for(raw, "CANCEL ricevuto")

        elif method == "BYE":
            self.save_raw(
                raw,
                "BYE"
            )

            self.respond_basic(
                raw,
                200,
                "OK"
            )
            self.stop_media_for(raw, "BYE ricevuto")

        elif method in (
            "MESSAGE",
            "NOTIFY",
            "INFO",
        ):
            self.save_raw(
                raw,
                method
            )

            self.respond_basic(
                raw,
                200,
                "OK"
            )

        elif method == "ACK":
            self.save_raw(
                raw,
                "ACK"
            )

        else:
            self.save_raw(
                raw,
                method or "UNKNOWN"
            )


    def loop(self):
        self.connect()
        self.register()

        log(
            "Listener operativo. "
            "In attesa di chiamate..."
        )

        while RUNNING:

            self.maintain_media()

            if time.time() >= self.next_refresh:
                log("Rinnovo registrazione SIP")
                # Re-REGISTER sul medesimo socket TLS: nessun buco volontario.
                self.register()
                log("Rinnovo completato sulla stessa connessione TLS")

            try:
                raw = self.stream.read_message(
                    timeout=5
                )

            except socket.timeout:
                continue

            first = sip_first_line(raw)

            if first.startswith("SIP/2.0"):
                log(f"SIP response inattesa: {first}")
                try:
                    path = self.save_raw(raw, "RESPONSE")
                    log(f"Segnalazione successiva salvata: {path.name}")
                except Exception as exc:
                    log(f"Impossibile salvare response: {exc}")
                continue

            self.handle_request(raw)


    def close(self):
        for capture in list(self.media.values()):
            capture.stop("connessione SIP chiusa")
        self.media.clear()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

        self.sock = None
        self.stream = None
        self.registered = False


def signal_handler(signum, frame):
    global RUNNING

    RUNNING = False
    log(
        f"Segnale {signum} ricevuto; "
        "arresto listener"
    )


signal.signal(
    signal.SIGTERM,
    signal_handler
)

signal.signal(
    signal.SIGINT,
    signal_handler
)


def main():
    BASE.mkdir(
        parents=True,
        exist_ok=True
    )

    LOGDIR.mkdir(
        parents=True,
        exist_ok=True
    )
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LOGDIR, 0o700)
    os.chmod(SNAPSHOT_DIR, 0o700)
    os.chmod(RUNTIME_DIR, 0o700)
    ensure_placeholder_snapshot()
    snapshot_server = start_snapshot_server()

    log("=" * 70)
    log("HOMETOUCH SIP diagnostic listener")
    log(
        f"Server: {SERVER_IP}:{SERVER_PORT}"
    )
    log(
        f"Domain: {DOMAIN}"
    )
    log(f"Pool RTP/RTCP: UDP {MEDIA_PORT_START}-{MEDIA_PORT_END}")
    log("=" * 70)

    reconnect_delay = max(0.0, RECONNECT_INITIAL_DELAY)

    while RUNNING:

        client = HomtouchListener()
        connected_at = time.monotonic()
        delay = reconnect_delay

        try:
            client.loop()

        except Exception as e:
            connected_for = time.monotonic() - connected_at
            log(
                f"Connessione/listener interrotto: "
                f"{type(e).__name__}: {e}"
            )
            delay = next_reconnect_delay(reconnect_delay, connected_for)
            reconnect_delay = delay

        finally:
            client.close()

        if RUNNING:
            log(f"Riconnessione SIP fra {delay:.2f}s")
            time.sleep(delay)

    snapshot_server.shutdown()
    snapshot_server.server_close()
    log("Listener terminato")


if __name__ == "__main__":
    main()
