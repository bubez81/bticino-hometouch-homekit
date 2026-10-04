import sys
import json
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from bticino_signaling_observation import observe


def message(first='INVITE sip:secret SIP/2.0',method='INVITE',body=''):
    return (first+'\r\nCall-ID: private-call\r\nCSeq: 42 '+method+
            '\r\nAuthorization: secret-password\r\nContent-Type: application/sdp\r\n\r\n'+body).encode()


class ObservationTests(unittest.TestCase):
    def test_late_sdp_matches_config_without_exposing_address(self):
        initial=observe(message(),b'key')
        late=observe(message('SIP/2.0 200 OK','UPDATE','a=DEVADDR:2001\r\na=crypto:secret-key\r\n'),b'key',[{'devaddr':'2001'}])
        self.assertEqual(initial['call'],late['call'])
        self.assertEqual(initial['devices'],[])
        self.assertEqual(late['devices'][0]['candidate_indices'],[1])
        self.assertEqual(late['status'],200)
        for secret in ['2001','private-call','secret-password','secret-key']:
            self.assertNotIn(secret,json.dumps(late))

    def test_session_and_media_scope(self):
        record=observe(message(body='a=DEVADDR:2001\r\nm=video 1 RTP/SAVP 96\r\na=devaddr:2002\r\n'),b'key')
        self.assertEqual([v['scope'] for v in record['devices']],['session','video'])
        self.assertNotEqual(record['devices'][0]['token'],record['devices'][1]['token'])

    def test_register_is_excluded(self):
        self.assertIsNone(observe(message(method='REGISTER'),b'key'))

    def test_methods_are_correlatable(self):
        records=[observe(message(method=m),b'key') for m in ['INVITE','UPDATE','ACK','MESSAGE','NOTIFY','INFO','BYE','CANCEL']]
        self.assertEqual(len({r['call'] for r in records}),1)
