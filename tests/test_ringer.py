import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from bticino_ringer import RingerControl


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class RingerControlTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.clock = Clock()
        self.logs = []
        self.ringer = RingerControl(enabled=True, volume_enabled=True, clock=self.clock,
                                    log=self.logs.append)
        self.sender = NS(username='test', local_ip='192.0.2.1', local_port=5000, cseq=7,
                         send=self.sent.append)

    def body(self, packet): return packet.split('\r\n\r\n', 1)[1]

    def call_id(self, packet):
        return next(l.split(': ', 1)[1] for l in packet.split('\r\n') if l.startswith('Call-ID: '))

    def test_mute_on_off_frames(self):
        self.assertEqual(self.ringer.request({'control': 'mute', 'value': True}, self.sender, 'example.invalid'),
                         {'ok': True, 'state': 'sent', 'control': 'mute', 'value': True})
        self.ringer.request({'control': 'mute', 'value': False}, self.sender, 'example.invalid')
        self.assertEqual([self.body(p) for p in self.sent], ['*#8**#33*0##', '*#8**#33*1##'])
        self.assertIs(self.ringer.status()['state']['mute'], False)
        for packet in self.sent:
            self.assertTrue(packet.startswith('MESSAGE sip:MHT@example.invalid SIP/2.0'))
            self.assertIn('Contact: <sip:test@192.0.2.1:5000;transport=tls>', packet)

    def test_voicemail_frames(self):
        self.ringer.request({'control': 'voicemail', 'value': True}, self.sender, 'example.invalid')
        self.ringer.request({'control': 'voicemail', 'value': False}, self.sender, 'example.invalid')
        self.assertEqual([self.body(p) for p in self.sent], ['*8*91##', '*8*92##'])

    def test_volume_maps_level_to_code_ends(self):
        self.ringer.request({'control': 'volume', 'value': 0}, self.sender, 'example.invalid')
        self.ringer.request({'control': 'volume', 'value': 100}, self.sender, 'example.invalid')
        self.assertEqual([self.body(p) for p in self.sent], ['*#8**#41*0##', '*#8**#41*7##'])
        self.assertEqual(self.ringer.status()['state']['volume'], 100)

    def test_volume_disabled_when_flag_off(self):
        ringer = RingerControl(enabled=True, volume_enabled=False, clock=self.clock, log=self.logs.append)
        result = ringer.request({'control': 'volume', 'value': 50}, self.sender, 'example.invalid')
        self.assertEqual(result['error'], 'invalid_value')
        self.assertEqual(self.sent, [])

    def test_disabled_sends_nothing(self):
        disabled = RingerControl(enabled=False, clock=self.clock, log=self.logs.append)
        self.assertEqual(disabled.request({'control': 'mute', 'value': True}, self.sender, 'example.invalid')['error'],
                         'ringer_disabled')
        self.assertEqual(self.sent, [])

    def test_unknown_control_and_bad_value(self):
        self.assertEqual(self.ringer.request({'control': 'xxx', 'value': True}, self.sender, 'x')['error'],
                         'unknown_control')
        self.assertEqual(self.ringer.request({'control': 'mute', 'value': 1}, self.sender, 'x')['error'],
                         'invalid_value')
        self.assertEqual(self.ringer.request({'control': 'volume', 'value': 101}, self.sender, 'x')['error'],
                         'invalid_value')
        self.assertEqual(self.sent, [])

    def test_responses_matched_and_recorded(self):
        self.ringer.request({'control': 'mute', 'value': True}, self.sender, 'example.invalid')
        call_id = self.call_id(self.sent[0])
        self.assertFalse(self.ringer.on_response('other@x', 'SIP/2.0 200 Ok'))
        self.assertTrue(self.ringer.on_response(call_id, 'SIP/2.0 100 Trying'))
        self.assertTrue(self.ringer.on_response(call_id, 'SIP/2.0 200 Ok'))
        self.assertFalse(self.ringer.on_response(call_id, 'SIP/2.0 200 Ok'))
        self.assertEqual(self.ringer.status()['results']['mute']['send'], 'SIP/2.0 200 Ok')

    def test_failed_send_reports_error_and_keeps_state_unknown(self):
        def fail(_packet): raise OSError('Disconnected')
        self.sender.send = fail
        result = self.ringer.request({'control': 'mute', 'value': True}, self.sender, 'example.invalid')
        self.assertEqual(result['error'], 'send_failed')
        self.assertIsNone(self.ringer.status()['state']['mute'])

    def test_header_injection_rejected_without_sending(self):
        self.sender.username = 'bad\r\nHeader'
        result = self.ringer.request({'control': 'mute', 'value': True}, self.sender, 'example.invalid')
        self.assertEqual(result['error'], 'send_failed')
        self.assertEqual(self.sent, [])

    def test_invalid_volume_codes_rejected(self):
        for codes in ([0, 1, 'x'], [1, 2, 1000], 'nope'):
            with self.assertRaises(ValueError):
                RingerControl(enabled=True, volume_codes=codes)


if __name__ == '__main__':
    unittest.main()
