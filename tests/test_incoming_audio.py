import base64
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_incoming_audio as incoming
from bticino_incoming_audio import ALAW_SILENCE, IncomingAudio

KEY = base64.b64encode(bytes(range(30))).decode()
AUDIO = {'codec': 'SPEEX', 'payload': 97, 'crypto_tag': 1, 'remote_key': KEY}


def udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1', 0))
    sock.setblocking(False)
    return sock


class IncomingAudioTests(unittest.TestCase):
    def make(self, clock=time.monotonic):
        self.local = [udp(), udp()]          # our call sockets (panel side)
        self.panel = [udp(), udp()]          # the panel
        self.forwarded = []
        talk = udp(); port = talk.getsockname()[1]; talk.close()
        audio = IncomingAudio('ffmpeg', 'ffmpeg-speex', AUDIO, KEY, self.local,
                              tuple(s.getsockname() for s in self.panel), talk_port=port,
                              on_packet=lambda offset, packet: self.forwarded.append((offset, packet)),
                              clock=clock)
        self.process = Mock(poll=Mock(return_value=0))
        with patch.object(incoming.subprocess, 'Popen', return_value=self.process) as spawn:
            audio.start()
            audio.stop_event.set(); audio.thread.join(timeout=1)   # drive step() by hand
        audio.started, audio.written = None, 0
        self.process.stdin.write.reset_mock()
        self.spawn = spawn
        return audio

    def tearDown(self):
        for sock in getattr(self, 'local', []) + getattr(self, 'panel', []):
            sock.close()

    def test_encoder_speex_with_local_key_never_logged_in_sdp(self):
        audio = self.make()
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[0], 'ffmpeg-speex')
        self.assertIn('libspeex', command)
        self.assertEqual(command[command.index('-payload_type') + 1], '97')
        self.assertEqual(command[command.index('-srtp_out_params') + 1], KEY)
        audio.close()

    def test_silence_until_talk_is_allowed(self):
        now = [100.0]
        audio = self.make(clock=lambda: now[0])
        self.assertEqual(audio.pump(), 160)
        self.assertEqual(self.process.stdin.write.call_args.args[0], bytes([ALAW_SILENCE]) * 160)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(b'\x11' * 80, ('127.0.0.1', audio.talk_port))
        time.sleep(0.05)
        now[0] += 0.02
        audio.pump()                                   # not allowed: speech discarded
        self.assertNotIn(b'\x11', self.process.stdin.write.call_args.args[0])
        audio.allow_talk(True)
        sender.sendto(b'\x22' * 80, ('127.0.0.1', audio.talk_port))
        time.sleep(0.05)
        now[0] += 0.02
        audio.pump()
        chunk = self.process.stdin.write.call_args.args[0]
        self.assertEqual(chunk[:80], b'\x22' * 80)
        self.assertEqual(audio.talk_bytes, 80)
        sender.close()
        audio.close()

    def test_step_relays_encoder_output_and_panel_audio(self):
        audio = self.make()
        encoder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        encoder.sendto(b'srtp-out', audio.encoded[0].getsockname())
        self.panel[0].sendto(b'panel-rtp', self.local[0].getsockname())
        time.sleep(0.05)
        audio.step()
        time.sleep(0.05)
        self.assertEqual(self.panel[0].recv(100), b'srtp-out')
        self.assertEqual(self.forwarded, [(0, b'panel-rtp')])
        self.assertEqual(audio.panel_packets, 1)
        encoder.close()
        audio.close()

    def test_listener_decoder_private_sdp_and_pcm_output(self):
        audio = self.make()
        with patch.object(incoming.subprocess, 'Popen', return_value=self.process) as spawn:
            audio.set_listener(40100)
            audio.set_listener(40100)                   # idempotent
        self.assertEqual(spawn.call_count, 1)
        command = spawn.call_args.args[0]
        sdp = Path(command[command.index('-i') + 1])
        self.assertEqual(sdp.stat().st_mode & 0o777, 0o600)
        self.assertIn('speex/8000', sdp.read_text())
        self.assertNotIn(KEY, ' '.join(command))
        self.assertIn('udp://127.0.0.1:40100?pkt_size=640', command)
        with self.assertRaises(ValueError):
            audio.set_listener(80)
        audio.close()
        self.assertFalse(sdp.exists())

    def test_rejects_unsupported_codec(self):
        with self.assertRaises(ValueError):
            IncomingAudio('ffmpeg', 'ffmpeg', dict(AUDIO, codec='OPUS'), KEY, [], ())


if __name__ == '__main__':
    unittest.main()


FFMPEG = '/opt/homebrew/bin/ffmpeg'


@unittest.skipUnless(Path(FFMPEG).exists(), 'needs FFmpeg')
class IncomingAudioRoundTrip(unittest.TestCase):
    def test_panel_audio_to_pcm_and_continuous_audio_to_panel(self):
        import subprocess, math
        panel_key = base64.b64encode(bytes(range(1, 31))).decode()
        local = [udp(), udp()]
        panel = [udp(), udp()]
        pcm = udp()
        talk = udp(); talk_port = talk.getsockname()[1]; talk.close()
        audio = IncomingAudio(FFMPEG, FFMPEG, {'codec': 'PCMA', 'payload': 8, 'crypto_tag': 1,
                                               'remote_key': panel_key}, KEY, local,
                              tuple(s.getsockname() for s in panel), talk_port=talk_port)
        relay = udp()
        audio.start()
        audio.set_listener(pcm.getsockname()[1])
        time.sleep(0.5)
        # The panel sends a 440-Hz tone, encrypted with its own key.
        source = subprocess.Popen([FFMPEG, '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi',
            '-i', 'sine=frequency=440:sample_rate=8000', '-t', '4', '-c:a', 'pcm_alaw', '-ar', '8000', '-ac', '1',
            '-payload_type', '8', '-f', 'rtp', '-srtp_out_suite', 'AES_CM_128_HMAC_SHA1_80',
            '-srtp_out_params', panel_key,
            f'srtp://127.0.0.1:{relay.getsockname()[1]}?rtcpport={relay.getsockname()[1]}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        received, to_panel = bytearray(), 0
        end = time.time() + 5
        while time.time() < end:
            try:
                # Deliver the tone from the panel's own address, as the gateway does.
                panel[0].sendto(relay.recv(65535), local[0].getsockname())
            except BlockingIOError:
                pass
            for sock, sink in ((pcm, 'pcm'), (panel[0], 'panel')):
                try:
                    data = sock.recv(65535)
                except BlockingIOError:
                    continue
                if sink == 'pcm':
                    received += data
                else:
                    to_panel += 1
            time.sleep(0.005)
        source.wait(timeout=5)
        audio.close()
        for sock in local + panel + [pcm, relay]:
            sock.close()
        samples = [int.from_bytes(received[i:i+2], 'little', signed=True) for i in range(0, len(received) - 1, 2)]
        self.assertGreater(len(samples), 16000, 'panel audio decoded to PCM')
        crossings = sum(1 for a, b in zip(samples, samples[1:]) if a <= 0 < b) / (len(samples) / 16000)
        self.assertLess(abs(crossings - 440), 35)
        # About 50 packets/s of our silence go to the panel for the whole call.
        self.assertGreater(to_panel, 150)
