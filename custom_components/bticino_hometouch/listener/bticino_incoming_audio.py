"""Two-way audio for an incoming entrance-panel call.

The gateway sends the panel's sound only while it receives audio from the
client, as observed on on-demand camera calls. This module therefore sends a
continuous encrypted stream to the panel from the moment the call is set up:
silence, or the viewer's speech (8-kHz A-law on loopback TALK_PORT) while
talking is allowed. The panel's audio is decrypted and decoded to raw 16-kHz
PCM for a local player (the Homebridge plugin) once it attaches.

FFmpeg does the codec and SRTP work; keys stay in private SDP files or FFmpeg
arguments and are never logged.
"""
import base64
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

TALK_PORT = 22310
ALAW_SILENCE = 0xD5
TALK_MAX_BACKLOG = 4000  # 0.5 s of 8-kHz A-law
CODECS = {
    'SPEEX': ('libspeex', 'speex/8000', ['-frames_per_packet', '1', '-vad', '0', '-dtx', '0']),
    'PCMA': ('pcm_alaw', 'PCMA/8000', []),
    'PCMU': ('pcm_mulaw', 'PCMU/8000', []),
}


def loopback_pair():
    """Two consecutive free loopback UDP ports (RTP, RTCP)."""
    for _ in range(20):
        first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        first.bind(('127.0.0.1', 0))
        port = first.getsockname()[1]
        second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            second.bind(('127.0.0.1', port + 1))
        except (OSError, OverflowError):
            first.close(); second.close()
            continue
        return first, second
    raise OSError('No loopback audio ports available')


class IncomingAudio:
    def __init__(self, ffmpeg, encoder_ffmpeg, audio, local_key, sockets, destinations,
                 talk_port=TALK_PORT, on_packet=None, clock=time.monotonic, log=None):
        if audio['codec'] not in CODECS:
            raise ValueError('Unsupported incoming audio codec')
        self.ffmpeg, self.encoder_ffmpeg = ffmpeg, encoder_ffmpeg
        self.audio, self.local_key = audio, local_key
        self.sockets, self.destinations = sockets, destinations
        self.talk_port, self.on_packet, self.clock, self.log = talk_port, on_packet, clock, log
        self.talk_allowed = False
        self.backlog = bytearray()
        self.started = None
        self.written = 0
        self.panel_packets = 0
        self.talk_bytes = 0
        self.encoder = self.decoder = self.talk = None
        self.encoded = []
        self.decoder_input = None
        self.directory = None
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        encoder, rtpmap, options = CODECS[self.audio['codec']]
        self.encoded = list(loopback_pair())
        for sock in self.encoded:
            sock.setblocking(False)
        port = self.encoded[0].getsockname()[1]
        self.encoder = subprocess.Popen(
            [self.encoder_ffmpeg, '-hide_banner', '-loglevel', 'error',
             '-f', 'alaw', '-ar', '8000', '-ac', '1', '-i', 'pipe:0',
             '-c:a', encoder, '-ar', '8000', '-ac', '1', *options,
             '-payload_type', str(self.audio['payload']), '-flush_packets', '1',
             '-f', 'rtp', '-srtp_out_suite', 'AES_CM_128_HMAC_SHA1_80',
             '-srtp_out_params', self.local_key,
             # 20-ms packets: 12-byte RTP header, 160 samples, 10-byte SRTP tag.
             f'srtp://127.0.0.1:{port}?rtcpport={port + 1}&pkt_size={1316 if encoder == "libspeex" else 182}'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.talk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.talk.bind(('127.0.0.1', self.talk_port))
            self.talk.setblocking(False)
        except OSError:
            self.talk.close()
            self.talk = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def set_listener(self, pcm_port):
        """Start decoding the panel's audio to raw PCM on a loopback port."""
        if type(pcm_port) is not int or not 1024 <= pcm_port <= 65535:
            raise ValueError('Invalid PCM port')
        with self.lock:
            if self.decoder is not None:
                return
            first, second = loopback_pair()
            port = first.getsockname()[1]
            first.close(); second.close()
            _, rtpmap, _ = CODECS[self.audio['codec']]
            self.directory = Path(tempfile.mkdtemp(prefix='bticino-incoming-audio-'))
            sdp = self.directory / 'panel.sdp'
            with os.fdopen(os.open(sdp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
                handle.write(f"v=0\no=- 0 0 IN IP4 127.0.0.1\ns=Panel audio\nc=IN IP4 127.0.0.1\nt=0 0\n"
                             f"m=audio {port} RTP/SAVP {self.audio['payload']}\na=rtcp:{port + 1}\n"
                             f"a=rtpmap:{self.audio['payload']} {rtpmap}\n"
                             f"a=crypto:{self.audio['crypto_tag']} AES_CM_128_HMAC_SHA1_80 "
                             f"inline:{self.audio['remote_key']}\na=recvonly\n")
            self.decoder = subprocess.Popen(
                [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                 '-protocol_whitelist', 'file,udp,rtp,srtp,crypto',
                 '-probesize', '32768', '-analyzeduration', '0', '-i', str(sdp),
                 '-map', '0:a:0', '-c:a', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                 '-f', 's16le', f'udp://127.0.0.1:{pcm_port}?pkt_size=640'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.decoder_input = port

    def allow_talk(self, allowed):
        self.talk_allowed = bool(allowed)

    def pump(self):
        """Write the samples due since the start: talk first if allowed, else silence."""
        if self.talk is not None:
            while True:
                try:
                    data = self.talk.recv(65535)
                except (BlockingIOError, OSError):
                    break
                if self.talk_allowed:
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
            self.encoder.stdin.write(chunk)
            self.encoder.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return 0
        self.written += due
        return due

    def step(self):
        """One cycle: panel audio in, our audio out."""
        for offset, sock in enumerate(self.sockets):
            while True:
                try:
                    packet, peer = sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    break
                if peer[0] != self.destinations[0][0]:
                    continue
                if offset == 0:
                    self.panel_packets += 1
                if self.on_packet:
                    self.on_packet(offset, packet)
                if self.decoder_input:
                    try:
                        self.sockets[offset].sendto(packet, ('127.0.0.1', self.decoder_input + offset))
                    except OSError:
                        pass
        self.pump()
        for offset, sock in enumerate(self.encoded):
            while True:
                try:
                    packet = sock.recv(65535)
                except (BlockingIOError, OSError):
                    break
                try:
                    self.sockets[offset].sendto(packet, self.destinations[offset])
                except OSError:
                    pass

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.step()
            except Exception as exc:  # never break SIP handling
                if self.log:
                    self.log(f'Audio chiamata: {type(exc).__name__}')
            self.stop_event.wait(0.02)

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
        for process in (self.encoder, self.decoder):
            if process is None:
                continue
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
        for sock in self.encoded + ([self.talk] if self.talk else []):
            try:
                sock.close()
            except OSError:
                pass
        self.encoded, self.talk = [], None
        if self.directory:
            for path in self.directory.glob('*'):
                path.unlink(missing_ok=True)
            self.directory.rmdir()
            self.directory = None
