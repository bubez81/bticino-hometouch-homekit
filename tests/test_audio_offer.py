import base64
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from bticino_audio_offer import parse_audio_offer

KEY=base64.b64encode(bytes(range(30))).decode()
SDP=f'v=0\r\nc=IN IP4 192.0.2.164\r\nm=audio 24000 RTP/SAVP 18 0 8\r\na=rtpmap:18 G729/8000\r\na=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{KEY}\r\na=sendrecv\r\nm=video 25000 RTP/SAVP 96\r\na=recvonly\r\n'

class AudioOfferTests(unittest.TestCase):
    def test_real_gateway_speex_offer(self):
        offer=SDP.replace('18 0 8','97 101').replace('a=rtpmap:18 G729/8000','a=rtpmap:97 speex/8000\r\na=rtpmap:101 telephone-event/8000')
        result=parse_audio_offer(offer)
        self.assertEqual(result['codec'],'SPEEX')
        self.assertEqual(result['payload'],97)
        self.assertTrue(result['can_talk'])
    def test_select_supported_codec_not_first(self):
        result=parse_audio_offer(SDP)
        self.assertEqual(result['codec'],'PCMU')
        self.assertTrue(result['can_talk'])
        self.assertTrue(result['can_listen'])
        self.assertEqual(result['rtcp_port'],24001)
    def test_prefers_speex_over_earlier_g711(self):
        offer=SDP.replace('18 0 8','0 97 8').replace('a=rtpmap:18 G729/8000','a=rtpmap:97 speex/8000')
        self.assertEqual(parse_audio_offer(offer)['codec'],'SPEEX')
    def test_no_audio(self):
        self.assertIsNone(parse_audio_offer('v=0\nm=video 25000 RTP/SAVP 96\n'))
    def test_disabled(self):
        self.assertIsNone(parse_audio_offer(SDP.replace('audio 24000','audio 0')))
    def test_direction_is_media_scoped(self):
        result=parse_audio_offer(SDP.replace('a=sendrecv','a=sendonly'))
        self.assertTrue(result['can_listen']);self.assertFalse(result['can_talk'])
    def test_reject_unsupported_codec(self):
        with self.assertRaises(ValueError):parse_audio_offer(SDP.replace('18 0 8','18'))
    def test_reject_invalid_key(self):
        with self.assertRaises(ValueError):parse_audio_offer(SDP.replace(KEY,'AAAA'))
    def test_reject_loopback_destination(self):
        with self.assertRaises(ValueError):parse_audio_offer(SDP.replace('192.0.2.164','127.0.0.1'))
    def test_reject_plaintext(self):
        with self.assertRaises(ValueError):parse_audio_offer(SDP.replace('RTP/SAVP','RTP/AVP'))

if __name__=='__main__':unittest.main()
