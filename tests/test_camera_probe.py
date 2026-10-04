import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('probe', Path(__file__).parents[1] / 'scripts/probe-camera.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_decoder_private_key_file_and_cleanup(self):
        from unittest.mock import Mock
        client = Mock()
        client.parse_video_offer.return_value = ('96', '', '1', 'PRIVATE_KEY')
        process = Mock()
        process.poll.return_value = 0
        with tempfile.TemporaryDirectory() as tmp, patch.object(probe.socket, 'socket', return_value=Mock()), patch.object(probe.tempfile, 'mkdtemp', return_value=tmp), patch.object(probe.subprocess, 'Popen', return_value=process) as launch:
            decoder = probe.FrameDecoder(SimpleNamespace(FFMPEG='ffmpeg'), client, b'')
            self.assertEqual(decoder.sdp.stat().st_mode & 0o777, 0o600)
            self.assertIn('PRIVATE_KEY', decoder.sdp.read_text())
            self.assertNotIn('PRIVATE_KEY', str(launch.call_args))
            decoder.close()
            self.assertFalse(decoder.sdp.exists())

    def test_media_destinations_and_prime(self):
        raw = b'SIP/2.0 200 OK\r\n\r\nv=0\r\nc=IN IP4 192.0.2.1\r\nm=audio 0 RTP/SAVP 0\r\nm=video 30000 RTP/SAVP 96\r\nc=IN IP4 192.0.2.2\r\na=rtcp:30003 IN IP4 192.0.2.3\r\n'
        self.assertEqual(probe.media_destinations(raw), (('192.0.2.2', 30000), ('192.0.2.3', 30003)))
        from unittest.mock import Mock
        udp, rtcp = Mock(), Mock()
        probe.prime_media(udp, rtcp, probe.media_destinations(raw))
        packet, destination = udp.sendto.call_args.args
        self.assertEqual(len(packet), 20)
        self.assertEqual(packet[:8], b'\x00\x11\x00\x00\x21\x12\xa4\x42')
        self.assertEqual(destination, ('192.0.2.2', 30000))
        with self.assertRaises(ValueError):
            probe.media_destinations(raw.replace(b'192.0.2.2', b'127.0.0.1'))

    def test_video_summary_redacts_and_detects_rejection(self):
        raw = b'SIP/2.0 200 OK\r\n\r\nv=0\r\nc=IN IP4 192.0.2.1\r\nm=video 0 RTP/SAVP 96\r\na=rtpmap:96 H264/90000\r\na=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:PRIVATE\r\na=inactive\r\n'
        result = probe.video_summary(raw)
        self.assertIn('enabled=False', result)
        self.assertIn('direction=inactive', result)
        self.assertNotIn('PRIVATE', result)
        self.assertNotIn('192.0.2.1', result)

    def test_offer_rejects_lock_and_header_injection(self):
        for row in ({'cid': '10060', 'devaddr': '200'},
                    {'cid': '10050', 'devaddr': '2\r\nBAD'}):
            with self.assertRaises(ValueError):
                probe.offer('127.0.0.1', 24000, row, 'test')
        sdp = probe.offer('127.0.0.1', 24000, {'cid': '10061', 'devaddr': '6001'}, 'test')
        self.assertIn('a=DEVADDR:6001\r\na=TVCC:1', sdp)

    def test_accepted_session_ack_and_bye(self):
        sent = []
        class Sock:
            def bind(self, *a): pass
            def sendto(self, *a): pass
            def close(self): pass
            def setblocking(self, *a): pass
            def recv(self, *a): raise BlockingIOError()
        class Client:
            username = 'test'
            account = 'test@gateway.example'
            local_ip = '127.0.0.1'
            local_port = 12345
            sock = Sock()
            def connect(self): pass
            def send(self, msg): sent.append(msg)
            def respond_basic(self, *a): pass
            @property
            def stream(self): return self
            def read_message(self, timeout):
                msg = sent[-1]
                call = next(x for x in msg.splitlines() if x.startswith('Call-ID:'))
                seq = next(x for x in msg.splitlines() if x.startswith('CSeq:'))
                body = 'v=0\r\nc=IN IP4 192.0.2.1\r\nm=video 30000 RTP/SAVP 96\r\n'
                return ('SIP/2.0 200 OK\r\n'+call+'\r\n'+seq+'\r\nTo: <sip:MHT@gateway.example>;tag=remote\r\nContact: <sip:MHT@gateway.example>\r\n\r\n'+body).encode()
        def headers(raw):
            return {k.lower(): v.strip() for k, v in (x.split(':', 1) for x in raw.decode().splitlines()[1:] if ':' in x)}, {}
        lib = SimpleNamespace(HomtouchListener=Client, DOMAIN='example',
                              sip_headers=headers, status_code=lambda raw: 200,
                              sip_first_line=lambda raw: raw.decode().splitlines()[0])
        ticks = iter(i * 0.25 for i in range(1000))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'candidates.json'
            path.write_text(json.dumps({'candidates': [{'cid': '10050', 'devaddr': '200'}]}))
            with patch.object(probe.importlib.util, 'module_from_spec', return_value=lib), patch.object(probe.importlib.util, 'spec_from_file_location', return_value=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda m: None))), patch.object(probe.socket, 'socket', return_value=Sock()), patch.object(probe.time, 'monotonic', side_effect=lambda: next(ticks)), patch.object(probe.time, 'sleep'), patch('sys.argv', ['probe', '--candidates', str(path), '--candidate', '1']), redirect_stdout(io.StringIO()) as out:
                probe.main()
        self.assertEqual([m.split()[0] for m in sent], ['INVITE', 'ACK', 'BYE'])
        self.assertIn('termination_confirmed=True', out.getvalue())
        self.assertNotIn('REGISTER', ''.join(sent))
