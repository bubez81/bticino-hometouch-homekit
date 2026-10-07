#!/usr/bin/env python3
"""Explicit outbound video signalling probe; no registration or door commands."""
import argparse
import base64
import importlib.util
import json
import ipaddress
import os
import re
import socket
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


class FrameDecoder:
    """Isolated FFmpeg receiver: key in private temporary SDP, no public stream."""
    def __init__(self, lib, client, raw, stream=False, audio=None):
        payload, fmtp, tag, key, *_ = client.parse_video_offer(raw)
        self.audio = audio
        self.directory = Path(tempfile.mkdtemp(prefix='bticino-video-probe-'))
        self.sdp = self.directory / 'input.sdp'
        self.frame = self.directory / 'frame.jpg'
        self.stream_port = int(os.environ.get('BTICINO_LIVE_VIDEO_PORT', '22300'))
        # Optional raw PCM copy of the panel audio for players that need their own
        # audio pipeline (the Homebridge plugin re-encodes it for HomeKit).
        audio_port = os.environ.get('BTICINO_LIVE_AUDIO_PORT', '')
        self.audio_port = int(audio_port) if audio_port.isdigit() and 1024 <= int(audio_port) <= 65535 else None
        self.process = None
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Find a free loopback pair outside the production receiver's range.
        for port in range(25000, 25200, 4):
            probes = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(4)]
            try:
                for offset, sock in enumerate(probes):
                    sock.bind(('127.0.0.1', port+offset))
                self.port = port
                break
            except OSError:
                continue
            finally:
                for sock in probes:
                    sock.close()
        else:
            self.sender.close()
            raise RuntimeError('No decoder ports')
        text = (f'v=0\no=- 0 0 IN IP4 127.0.0.1\ns=Private probe\nc=IN IP4 127.0.0.1\nt=0 0\n'
                f'm=video {port} RTP/SAVP {payload}\na=rtcp:{port+1}\na=rtpmap:{payload} H264/90000\n'
                + (f'a=fmtp:{payload} {fmtp}\n' if fmtp else '')
                + f'a=crypto:{tag} AES_CM_128_HMAC_SHA1_80 inline:{key}\na=recvonly\n')
        if audio:
            apayload, atag, akey = audio
            text += (f'm=audio {port+2} RTP/SAVP {apayload}\na=rtcp:{port+3}\na=rtpmap:{apayload} speex/8000\n'
                     f'a=crypto:{atag} AES_CM_128_HMAC_SHA1_80 inline:{akey}\na=recvonly\n')
        try:
            with os.fdopen(os.open(self.sdp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
                file.write(text)
            command = [lib.FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-protocol_whitelist', 'file,udp,rtp,srtp,crypto', '-rw_timeout', '12000000',
                '-probesize', '32768', '-analyzeduration', '0',
                '-i', str(self.sdp), '-map', '0:v:0']
            if stream:
                # Entrance-panel audio is transcoded to Opus, the codec WebRTC players accept.
                # With a separate audio port the stream stays video only: a declared
                # but silent audio track would stop the player from starting the video.
                # AAC instead (BTICINO_LIVE_AUDIO_CODEC=aac) for players reading MPEG-TS over HTTP.
                audio_codec = (['-c:a', 'aac', '-ar', '16000'] if os.environ.get('BTICINO_LIVE_AUDIO_CODEC') == 'aac'
                               else ['-c:a', 'libopus', '-ar', '48000'])
                command += (['-map', '0:a:0', *audio_codec, '-ac', '1', '-b:a', '32k']
                            if audio and not self.audio_port else ['-an'])
                command += ['-c:v', 'copy', '-flush_packets', '1', '-muxdelay', '0', '-f', 'mpegts',
                            f'udp://127.0.0.1:{self.stream_port}?pkt_size=1316']
            else:
                command += ['-an']
            if stream and audio and self.audio_port:
                command += ['-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                            '-f', 's16le', f'udp://127.0.0.1:{self.audio_port}?pkt_size=640']
            command += ['-map', '0:v:0', '-vf', 'select=gte(n\\,10)',
                        '-frames:v', '1', '-q:v', '2', '-y', str(self.frame)]
            # FFmpeg errors go to the probe's stderr (the camera call log); the key is
            # only in the private SDP file, never on the command line.
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL)
        except Exception:
            self.close()
            raise

    def feed(self, packet):
        self.sender.sendto(packet, ('127.0.0.1', self.port))

    def feed_audio(self, packet, rtcp=False):
        self.sender.sendto(packet, ('127.0.0.1', self.port + (3 if rtcp else 2)))

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
        self.sender.close()
        self.sdp.unlink(missing_ok=True)
        if self.frame.exists():
            self.frame.chmod(0o600)


def media_destinations(raw, kind='video'):
    """Read IPv4 media destinations from authenticated SIP answer, fail closed."""
    lines = raw.partition(b'\r\n\r\n')[2].decode('utf-8', 'replace').splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f'm={kind} ')), None)
    if start is None:
        raise ValueError(f'Missing {kind}')
    end = next((i for i in range(start+1, len(lines)) if lines[i].startswith('m=')), len(lines))
    media = lines[start:end]
    session = lines[:next(i for i, line in enumerate(lines) if line.startswith('m='))]
    def address(section):
        return next((x[9:] for x in section if x.startswith('c=IN IP4 ')), None)
    host = address(media) or address(session)
    def validate_host(value):
        ip = ipaddress.IPv4Address(value)
        if ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local or str(ip) == '255.255.255.255':
            raise ValueError('Unsupported media destination')
        return str(ip)
    host = validate_host(host)
    port = int(media[0].split()[1])
    rtcp_host, rtcp_port = host, port+1
    for line in media:
        if line.startswith('a=rtcp:'):
            fields = line[7:].split()
            rtcp_port = int(fields[0])
            if len(fields) > 1:
                if len(fields) != 4 or fields[1:3] != ['IN', 'IP4']:
                    raise ValueError('Unsupported RTCP address')
                rtcp_host = validate_host(fields[3])
    if 'a=rtcp-mux' in media:
        rtcp_host, rtcp_port = host, port
    if not (1024 <= port <= 65535 and 1024 <= rtcp_port <= 65535):
        raise ValueError('Unsupported media port')
    return (host, port), (rtcp_host, rtcp_port)


SPEEX_PAYLOAD = 97


def audio_answer(raw):
    """Accepted Speex audio from the SIP answer: (payload, crypto tag, key) or None."""
    lines = raw.partition(b'\r\n\r\n')[2].decode('utf-8', 'replace').splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith('m=audio ')), None)
    if start is None:
        return None
    end = next((i for i in range(start+1, len(lines)) if lines[i].startswith('m=')), len(lines))
    media = lines[start:end]
    fields = media[0].split()
    if len(fields) < 4 or not fields[1].isdigit() or int(fields[1]) == 0:
        return None
    payload = next((m.group(1) for m in (re.fullmatch(r'a=rtpmap:(\d+) speex/8000(?:/1)?', line, re.I) for line in media)
                    if m and m.group(1) in fields[3:]), None)
    crypto = next((m for m in (re.fullmatch(r'a=crypto:(\d+) AES_CM_128_HMAC_SHA1_80 inline:([A-Za-z0-9+/]{38}==|[A-Za-z0-9+/]{40})(?:\|\S*)?', line)
                               for line in media) if m), None)
    if payload is None or crypto is None:
        return None
    return payload, crypto.group(1), crypto.group(2)


TALK_PORT = 22310
ALAW_SILENCE = 0xD5
TALK_MAX_BACKLOG = 4000  # 0.5 s of 8 kHz A-law: older talk audio is dropped


class AudioSender:
    """Sends the client's audio as encrypted Speex, as a phone would.

    The gateway only transmits the entrance panel's audio while it receives
    audio from the client, so the probe always sends a continuous 8 kHz A-law
    stream: talk audio received on loopback UDP TALK_PORT (from the go2rtc
    backchannel relay) when there is any, silence otherwise. FFmpeg encodes it
    to Speex and SRTP-protects it to a local port; the probe relays those
    packets from its own audio sockets, so the gateway sees one symmetric
    RTP/RTCP flow. FFmpeg reads stdin, so it ends with the probe.
    """
    def __init__(self, ffmpeg, local_material, talk_port=TALK_PORT, clock=time.monotonic):
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtcp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind(('127.0.0.1', 0))
        self.rtcp.bind(('127.0.0.1', self.rtp.getsockname()[1] + 1))
        self.rtp.setblocking(False)
        self.rtcp.setblocking(False)
        self.talk = None
        if talk_port:
            self.talk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self.talk.bind(('127.0.0.1', talk_port))
                self.talk.setblocking(False)
            except OSError:
                self.talk.close()
                self.talk = None
        self.backlog = bytearray()
        self.talk_bytes = 0
        self.clock = clock
        self.started = None
        self.written = 0
        port = self.rtp.getsockname()[1]
        self.process = subprocess.Popen([ffmpeg, '-hide_banner', '-loglevel', 'error',
            '-f', 'alaw', '-ar', '8000', '-ac', '1', '-i', 'pipe:0', '-c:a', 'libspeex', '-ar', '8000', '-ac', '1',
            '-frames_per_packet', '1', '-vad', '0', '-dtx', '0', '-payload_type', str(SPEEX_PAYLOAD),
            '-flush_packets', '1', '-f', 'rtp', '-srtp_out_suite', 'AES_CM_128_HMAC_SHA1_80',
            '-srtp_out_params', base64.b64encode(local_material).decode(),
            f'srtp://127.0.0.1:{port}?rtcpport={port+1}&pkt_size=1316'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)

    def pump(self):
        """Write the samples due since the start: talk audio first, then silence."""
        if self.talk is not None:
            while True:
                try:
                    data = self.talk.recv(65535)
                except BlockingIOError:
                    break
                self.backlog += data
                self.talk_bytes += len(data)
            if len(self.backlog) > TALK_MAX_BACKLOG:
                del self.backlog[:len(self.backlog) - TALK_MAX_BACKLOG]
        now = self.clock()
        if self.started is None:
            self.started = now
        due = round((now - self.started) * 8000) + 160 - self.written
        if due < 160:
            return 0
        chunk = bytes(self.backlog[:due])
        del self.backlog[:len(chunk)]
        chunk += bytes([ALAW_SILENCE]) * (due - len(chunk))
        try:
            self.process.stdin.write(chunk)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return 0
        self.written += due
        return due

    def relay(self, audio, audio_rtcp, destinations):
        self.pump()
        sent = 0
        for source, target, destination in ((self.rtp, audio, destinations[0]), (self.rtcp, audio_rtcp, destinations[1])):
            while True:
                try:
                    data = source.recv(65535)
                except BlockingIOError:
                    break
                target.sendto(data, destination)
                sent += 1
        return sent

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
        self.rtp.close(); self.rtcp.close()
        if self.talk is not None:
            self.talk.close()


DIALOG_FILE = 'camera-dialog.json'


def dialog_path(lib):
    directory = getattr(lib, 'RUNTIME_DIR', None)
    return Path(directory) / DIALOG_FILE if directory else None


def save_dialog(path, record):
    """Remember how to end the accepted call, in case this process is killed."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
            json.dump(record, handle)
        temporary.replace(path)
    except OSError:
        pass


def load_dialog(path):
    if path is None:
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    keys = ('call_id', 'from_tag', 'to', 'target', 'routes', 'cseq')
    if not isinstance(record, dict) or any(key not in record for key in keys):
        return None
    if not all(isinstance(record[key], str) for key in keys[:4]) or not isinstance(record['cseq'], int):
        return None
    if any(c in ''.join([record[key] for key in keys[:4]] + list(record['routes'])) for c in '\r\n'):
        return None
    return record


def clear_dialog(path):
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass


def sip_request(client, identity, method, target, to, call_id, from_tag, seq, via_branch,
                content='', auth=None, routes=()):
    lines = [f'{method} {target} SIP/2.0',
             f'Via: SIP/2.0/TLS {client.local_ip}:{client.local_port};branch={via_branch};rport',
             'Max-Forwards: 70', f'From: {identity};tag={from_tag}',
             f'To: {to}', f'Call-ID: {call_id}', f'CSeq: {seq} {method}',
             f'Contact: <sip:{client.username}@{client.local_ip}:{client.local_port};transport=tls>']
    lines.extend('Route: '+r for r in routes)
    if auth:
        lines.append(auth)
    if content:
        lines.append('Content-Type: application/sdp')
    return '\r\n'.join(lines + [f'Content-Length: {len(content.encode())}', '', content])


def end_stale_dialog(lib, client, identity, path, wait=3.0):
    """Send BYE for a call a killed probe left open, so the panel is free again."""
    record = load_dialog(path)
    if record is None:
        clear_dialog(path)
        return None
    client.send(sip_request(client, identity, 'BYE', record['target'], record['to'], record['call_id'],
                            record['from_tag'], record['cseq'] + 1, 'z9hG4bK' + uuid.uuid4().hex,
                            routes=record['routes']))
    status = None
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            raw = client.stream.read_message(timeout=0.2)
        except socket.timeout:
            continue
        headers, _ = lib.sip_headers(raw)
        if headers.get('call-id') == record['call_id'] and lib.status_code(raw) is not None:
            status = lib.status_code(raw)
            if status >= 200:
                break
    clear_dialog(path)
    print(f'STALE_BYE status={status}', flush=True)
    return status


def prime_media(udp, rtcp, destinations):
    # STUN Binding Indications carry no credentials and request no response.
    # This probes UDP return-path opening, not ICE connectivity or consent.
    for sock, destination in zip((udp, rtcp), destinations):
        sock.sendto(b'\x00\x11\x00\x00\x21\x12\xa4\x42'+os.urandom(12), destination)


def challenge_summary(lib, code, headers, raw):
    """Who asked for authentication, without values that identify the account."""
    challenge = lib.parse_digest_challenge(headers.get('www-authenticate') or headers.get('proxy-authenticate') or '')
    realm = challenge.get('realm', '')
    kind = ('assente' if not realm else 'dominio_impianto' if realm == getattr(lib, 'DOMAIN', None)
            else 'iotleg' if 'iotleg' in realm else 'altro')
    server = re.sub(r'[^A-Za-z0-9 ./_-]', '', raw_header(raw, 'server') or raw_header(raw, 'user-agent') or '-')[:40]
    reason = re.sub(r'[^A-Za-z0-9 ./_-]', '', raw_header(raw, 'reason') or raw_header(raw, 'warning') or '-')[:60]
    return (f'SIP_CHALLENGE status={code} realm={kind} algorithm={challenge.get("algorithm", "MD5")} '
            f'qop={challenge.get("qop", "-")} stale={challenge.get("stale", "-")} server={server} reason={reason}')


def raw_header(raw, name):
    for line in raw.decode('utf-8', 'replace').splitlines():
        if line.lower().startswith(name + ':'):
            return line.split(':', 1)[1].strip()
    return None


def video_summary(raw):
    """Report negotiated capabilities only, never addresses or SDES material."""
    body = raw.partition(b'\r\n\r\n')[2].decode('utf-8', 'replace')
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith('m=video ')), None)
    if start is None:
        return 'VIDEO_SDP present=False'
    end = next((i for i in range(start+1, len(lines)) if lines[i].startswith('m=')), len(lines))
    video = lines[start:end]
    parts = video[0].split()
    enabled = len(parts) >= 4 and parts[1].isdigit() and int(parts[1]) > 0
    directions = ('sendrecv', 'sendonly', 'recvonly', 'inactive')
    session = lines[:next((i for i, line in enumerate(lines) if line.startswith('m=')), start)]
    direction = next((d for d in directions if 'a='+d in video),
                     next((d for d in directions if 'a='+d in session), 'sendrecv'))
    h264 = any(re.fullmatch(r'a=rtpmap:[0-9]+ H264/90000', line, re.I) for line in video)
    crypto = any(line.startswith('a=crypto:') for line in video)
    return f'VIDEO_SDP present=True enabled={enabled} h264={h264} crypto={crypto} direction={direction}'


def offer(ip, port, candidate, key, audio=False):
    address = candidate['devaddr']
    if not re.fullmatch(r'[0-9]{2,13}', address):
        raise ValueError('Invalid camera address')
    if candidate['cid'] not in ('10050', '10061'):
        raise ValueError('Unsupported camera type')
    extra = 'a=TVCC:1\r\n' if candidate['cid'] == '10061' else ''
    return (f'v=0\r\no=probe 1 1 IN IP4 {ip}\r\ns=Camera probe\r\n'
            f'c=IN IP4 {ip}\r\nt=0 0\r\na=DEVADDR:{address}\r\n{extra}'
            + (f'm=audio {port+2} RTP/SAVP {SPEEX_PAYLOAD} 101\r\na=rtcp:{port+3}\r\n'
               f'a=rtpmap:{SPEEX_PAYLOAD} speex/8000\r\na=rtpmap:101 telephone-event/8000\r\n'
               f'a=sendrecv\r\na=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{key}\r\n'
               if audio else 'm=audio 0 RTP/SAVP 0\r\na=inactive\r\n') +
            f'm=video {port} RTP/SAVP 96\r\na=rtcp:{port+1}\r\n'
            'a=rtpmap:96 H264/90000\r\na=fmtp:96 packetization-mode=1\r\n'
            'a=recvonly\r\na=crypto:1 AES_CM_128_HMAC_SHA1_80 '
            f'inline:{key}\r\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--listener', default=os.environ.get('BTICINO_LISTENER') or '/opt/bticino-sniffer/listener.py')
    p.add_argument('--candidates', required=True)
    p.add_argument('--candidate', type=int, required=True)
    p.add_argument('--prime-udp', action='store_true', help='test UDP return path using two STUN indications')
    p.add_argument('--decode-frame', action='store_true', help='decifra e salva un fotogramma privato')
    p.add_argument('--stream', action='store_true', help='inoltra il video decifrato a MPEG-TS localhost:22300')
    p.add_argument('--rtcp-feedback', action='store_true', help='invia feedback SRTCP autenticato durante il video')
    p.add_argument('--audio', action='store_true',
                   help='riceve anche l\'audio del posto esterno (offre Speex e invia silenzio)')
    p.add_argument('--audio-ffmpeg', help='FFmpeg con libspeex e SRTP; default audio_ffmpeg in config')
    p.add_argument('--duration', type=int, default=10)
    p.add_argument('--gateway', help='private gateway SIP host; default from SIP account')
    args = p.parse_args()
    if not 1 <= args.duration <= 300:
        p.error('duration must be between 1 and 300 seconds')
    rows = json.loads(Path(args.candidates).read_text())['candidates']
    if not 1 <= args.candidate <= len(rows):
        raise ValueError('Invalid candidate index')
    spec = importlib.util.spec_from_file_location('listener', args.listener)
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    # Install handlers after loading the listener module, which installs its own
    # at import time. Otherwise SIGTERM is swallowed, the call is killed without
    # BYE and its FFmpeg children are orphaned.
    stopping = False
    parent = os.getppid()
    def stop_requested(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    # Suppress library messages that could include account/network identifiers.
    lib.log = lambda *a, **k: None
    client = lib.HomtouchListener()
    gateway = args.gateway or client.account.rsplit('@', 1)[-1]
    if not re.fullmatch(r'[A-Za-z0-9.-]+', gateway) or '.' not in gateway:
        raise ValueError('Invalid gateway')
    audio_ffmpeg = (args.audio_ffmpeg or getattr(lib, 'CONFIG', {}).get('audio_ffmpeg')
                    or os.environ.get('BTICINO_AUDIO_FFMPEG') or getattr(lib, 'FFMPEG', 'ffmpeg'))
    # Video RTP/RTCP on port/port+1, audio RTP/RTCP on port+2/port+3.
    sockets = []
    decoder = None
    sender = None
    accepted = finished = False
    dialog_file = identity = None
    try:
        for port in range(24000, 24100, 4):
            sockets = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(4)]
            try:
                for offset, sock in enumerate(sockets):
                    sock.bind(('0.0.0.0', port+offset))
                break
            except OSError:
                for sock in sockets:
                    sock.close()
        else:
            raise RuntimeError('No free probe ports')
        udp, rtcp, audio, audio_rtcp = sockets
        for sock in (udp, audio, audio_rtcp):
            sock.setblocking(False)
        audio_packets = 0
        client.connect()
        uri = f'sip:MHT@{gateway}'
        identity = f'<sip:{client.username}@{lib.DOMAIN}>'
        dialog_file = dialog_path(lib)
        end_stale_dialog(lib, client, identity, dialog_file)
        call = uuid.uuid4().hex + '@camera-probe'
        tag = uuid.uuid4().hex
        branch = 'z9hG4bK' + uuid.uuid4().hex
        cseq = 1
        local_material = os.urandom(30)
        feedback_ssrc = int.from_bytes(os.urandom(4), 'big')
        body = offer(client.local_ip, port, rows[args.candidate-1],
                     base64.b64encode(local_material).decode(), audio=args.audio)
        def request(method, target, to, seq, via_branch, content='', auth=None, routes=()):
            return sip_request(client, identity, method, target, to, call, tag, seq, via_branch,
                               content, auth, routes)
        client.send(request('INVITE', uri, f'<{uri}>', cseq, branch, body))
        deadline = time.monotonic()+20
        accepted = False
        cancelled = False
        packets = 0
        finished = False
        while time.monotonic() < deadline:
            if not accepted and not cancelled and (stopping or time.monotonic() > deadline-5):
                client.send(request('CANCEL', uri, f'<{uri}>', cseq, branch))
                cancelled = True
            try:
                data = udp.recv(65535)
                if len(data) >= 12 and data[0] >> 6 == 2:
                    packets += 1
            except BlockingIOError:
                pass
            try:
                raw = client.stream.read_message(timeout=0.2)
            except socket.timeout:
                continue
            headers, _ = lib.sip_headers(raw)
            if headers.get('call-id') != call:
                continue
            code = lib.status_code(raw)
            if code is None:
                client.respond_basic(raw, 200 if lib.sip_first_line(raw).startswith('BYE ') else 501, 'OK')
                if lib.sip_first_line(raw).startswith('BYE '):
                    break
                continue
            if headers.get('cseq', '').endswith('BYE') and code == 200:
                finished = True
                break
            if not headers.get('cseq', '').endswith('INVITE'):
                continue
            print(f'SIP_STATUS={code}', flush=True)
            if code in (183, 200):
                print(video_summary(raw), flush=True)
            to = headers.get('to', f'<{uri}>')
            if code < 200:
                continue
            if code >= 300:
                client.send(request('ACK', uri, to, cseq, branch))
                if code in (401, 407):
                    print(challenge_summary(lib, code, headers, raw), flush=True)
                if code in (401, 407) and cseq == 1 and not cancelled:
                    challenge = lib.parse_digest_challenge(headers.get('www-authenticate') or headers.get('proxy-authenticate'))
                    auth = lib.digest_authorization(username=getattr(client, 'auth_username', client.username), password=client.password,
                        method='INVITE', uri=uri, challenge=challenge)
                    cseq += 1
                    branch = 'z9hG4bK'+uuid.uuid4().hex
                    label = 'Proxy-Authorization' if code == 407 else 'Authorization'
                    client.send(request('INVITE', uri, f'<{uri}>', cseq, branch, body, label+': '+auth))
                    continue
                finished = True
                break
            contact = headers.get('contact', '')
            match = re.search(r'<([^>]+)>', contact)
            target = match.group(1) if match else contact.strip() or uri
            routes = [line.split(':', 1)[1].strip() for line in raw.decode('utf-8', 'replace').splitlines()
                      if line.lower().startswith('record-route:')][::-1]
            client.send(request('ACK', target, to, cseq, 'z9hG4bK'+uuid.uuid4().hex, routes=routes))
            if not accepted:
                save_dialog(dialog_file, {'call_id': call, 'from_tag': tag, 'to': to, 'target': target,
                                          'routes': routes, 'cseq': cseq})
            if not accepted:
                accepted = True
                remote_audio = audio_answer(raw) if args.audio and not cancelled else None
                audio_destinations = None
                if args.audio:
                    print(f'AUDIO_ACCEPTED={remote_audio is not None}', flush=True)
                if remote_audio:
                    try:
                        audio_destinations = media_destinations(raw, 'audio')
                        sender = AudioSender(audio_ffmpeg, local_material)
                    except (ValueError, OSError) as exc:
                        print(f'AUDIO_START_FAILED={type(exc).__name__}', flush=True)
                        remote_audio = None
                if (args.decode_frame or args.stream) and not cancelled:
                    try:
                        decoder = FrameDecoder(lib, client, raw, stream=args.stream,
                                               audio=remote_audio if args.stream else None)
                        time.sleep(0.5)
                    except Exception as exc:
                        print(f'DECODER_START_FAILED={type(exc).__name__}', flush=True)
                if args.prime_udp:
                    try:
                        prime_media(udp, rtcp, media_destinations(raw))
                        if audio_destinations:
                            prime_media(audio, audio_rtcp, audio_destinations)
                        print('UDP_PRIME sent=True', flush=True)
                    except (ValueError, OSError, IndexError, TypeError):
                        print('UDP_PRIME sent=False (destinazione non supportata o invio fallito)', flush=True)
                # Keep the accepted session briefly to count encrypted RTP.
                finish = time.monotonic()+(0 if cancelled else (args.duration if args.stream else (10 if args.decode_frame else 5)))
                next_signal_poll = time.monotonic()
                next_media_report = time.monotonic()+10
                remote_ended = False
                feedback_index = 0
                next_feedback = 0
                destinations = media_destinations(raw)
                while time.monotonic() < finish and not stopping:
                    try:
                        data = udp.recv(65535)
                        if len(data) >= 12 and data[0] >> 6 == 2:
                            packets += 1
                            if decoder:
                                decoder.feed(data)
                            if args.rtcp_feedback and time.monotonic() >= next_feedback:
                                feedback = lib.make_srtcp_pli(local_material, feedback_ssrc,
                                    int.from_bytes(data[8:12], 'big'), feedback_index)
                                rtcp.sendto(feedback, destinations[1])
                                feedback_index += 1
                                next_feedback = time.monotonic()+5
                                print(f'SRTCP_FEEDBACK sent={feedback_index}', flush=True)
                    except BlockingIOError:
                        time.sleep(0.02)
                    if sender:
                        # The gateway sends the panel's audio only while it receives ours.
                        sender.relay(audio, audio_rtcp, audio_destinations)
                        for sock, is_rtcp in ((audio, False), (audio_rtcp, True)):
                            while True:
                                try:
                                    data = sock.recv(65535)
                                except BlockingIOError:
                                    break
                                audio_packets += not is_rtcp
                                if decoder and decoder.audio:
                                    decoder.feed_audio(data, rtcp=is_rtcp)
                    now = time.monotonic()
                    if now >= next_media_report:
                        print(f'{time.strftime("%H:%M:%S")} MEDIA_PROGRESS rtp_packets={packets} audio_packets={audio_packets} talk_bytes={sender.talk_bytes if sender else 0} decoder_running={decoder is not None and decoder.process.poll() is None}', flush=True)
                        next_media_report = now+10
                    if now >= next_signal_poll:
                        next_signal_poll = now+0.2
                        if os.getppid() != parent:
                            # The listener that started this call is gone: end it cleanly.
                            print(f'{time.strftime("%H:%M:%S")} PARENT_GONE', flush=True)
                            stopping = True
                        try:
                            incoming = client.stream.read_message(timeout=0.001)
                        except socket.timeout:
                            continue
                        except (ConnectionError, OSError) as exc:
                            # The gateway periodically closes every TLS connection of
                            # this account. Media does not use it: reconnect and keep
                            # the call; BYE then goes over the new connection.
                            if isinstance(exc, socket.timeout):
                                continue
                            print(f'{time.strftime("%H:%M:%S")} SIP_CONNECTION_LOST', flush=True)
                            try:
                                client.sock.close()
                            except Exception:
                                pass
                            try:
                                client.connect()
                                print(f'{time.strftime("%H:%M:%S")} SIP_RECONNECTED', flush=True)
                            except Exception:
                                time.sleep(1)
                                client.connect()
                                print(f'{time.strftime("%H:%M:%S")} SIP_RECONNECTED', flush=True)
                            continue
                        incoming_headers, _ = lib.sip_headers(incoming)
                        if incoming_headers.get('call-id') != call:
                            continue
                        method = lib.sip_first_line(incoming).split(' ', 1)[0]
                        if method == 'BYE':
                            client.respond_basic(incoming, 200, 'OK')
                            print(f'{time.strftime("%H:%M:%S")} REMOTE_BYE received=True', flush=True)
                            finished = remote_ended = True
                            break
                        if method == 'OPTIONS':
                            client.respond_basic(incoming, 200, 'OK')
                            print('REMOTE_OPTIONS answered=True', flush=True)
                        elif method in ('INVITE', 'UPDATE'):
                            # Do not silently ignore a mid-dialog refresh.
                            print(f'REMOTE_REFRESH method={method}', flush=True)
                            client.respond_basic(incoming, 501, 'Not Implemented')
                if remote_ended:
                    break
                client.send(request('BYE', target, to, cseq+1, 'z9hG4bK'+uuid.uuid4().hex, routes=routes))
                deadline = time.monotonic()+5
        if finished:
            clear_dialog(dialog_file)
        print(f'{time.strftime("%H:%M:%S")} PROBE accepted={accepted} rtp_packets={packets} audio_packets={audio_packets} cancel_sent={cancelled} termination_confirmed={finished}')
        print('Prova isolata: non conferma HomeKit live.')
    finally:
        if sender:
            sender.close()
        if decoder:
            decoder.close()
            valid = decoder.frame.exists() and decoder.frame.stat().st_size > 0
            print(f'FRAME_SAVED={valid}')
            if valid:
                print(f'Fotogramma privato: {decoder.frame}')
        for sock in sockets:
            sock.close()
        if accepted and not finished and dialog_file is not None and dialog_file.exists():
            # The SIP connection failed before the call was confirmed ended (for
            # example the TLS link dropped): reconnect and end it now, otherwise
            # the panel answers 486 Busy until it times the call out.
            try:
                if client.sock:
                    client.sock.close()
                client.connect()
                end_stale_dialog(lib, client, identity, dialog_file, wait=5.0)
            except Exception as exc:
                print(f'STALE_BYE_FAILED={type(exc).__name__}', flush=True)
        if client.sock:
            client.sock.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'PROBE_ERROR={type(exc).__name__} (dettagli privati omessi)')
        raise SystemExit(1)
