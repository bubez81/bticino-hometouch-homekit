import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from bticino_incoming_dialog import IncomingDialog

INVITE=b'INVITE sip:local@example SIP/2.0\r\nVia: SIP/2.0/TLS peer;branch=z9hG4bKa\r\nFrom: <sip:peer@example>;tag=remote\r\nTo: <sip:local@example>\r\nCall-ID: private-test\r\nCSeq: 22 INVITE\r\nContact: <sip:peer@192.0.2.2:5061;transport=tls>\r\nRecord-Route: <sip:proxy@192.0.2.3;lr>\r\nContent-Length: 0\r\n\r\n'

class DialogTests(unittest.TestCase):
    def setUp(self):
        self.sent=[]
        self.dialog=IncomingDialog(INVITE,'<sip:local@192.0.2.1:5061;transport=tls>',self.sent.append,'stable')
    def test_answer_requires_ready_audio(self):
        with self.assertRaises(ValueError):self.dialog.answer('owner','v=0\r\n')
        self.assertFalse(self.sent)
    def test_answer_ack_hangup(self):
        self.dialog.answer('owner','v=0\r\n',True)
        self.assertIn('SIP/2.0 200 OK',self.sent[0])
        self.assertIn('Contact: <sip:local@',self.sent[0])
        self.assertIn(';tag=stable',self.sent[0])
        self.assertIn('Content-Length: 5',self.sent[0])
        self.dialog.receive(INVITE.replace(b'INVITE',b'ACK'))
        self.assertEqual(self.dialog.state,'established')
        self.assertTrue(self.dialog.hangup('owner'))
        self.assertIn('Route: <sip:proxy@192.0.2.3;lr>',self.sent[-1])
        self.assertFalse(self.dialog.hangup('owner'))
    def test_repeated_answer_does_not_duplicate(self):
        self.dialog.answer('owner','v=0',True)
        self.assertFalse(self.dialog.answer('owner','v=0',True))
        self.assertEqual(len(self.sent),1)
    def test_other_owner_rejected(self):
        self.dialog.answer('owner','v=0',True)
        with self.assertRaises(ValueError):self.dialog.hangup('other')
    def test_cancel_before_answer(self):
        self.dialog.receive(INVITE.replace(b'INVITE',b'CANCEL'))
        self.assertEqual(self.dialog.state,'closed')
        self.assertIn('487 Request Terminated',self.sent[-1])
        with self.assertRaises(ValueError):self.dialog.answer('owner','v=0',True)
    def test_cancel_after_answer_preserves_dialog(self):
        self.dialog.answer('owner','v=0',True)
        self.dialog.receive(INVITE.replace(b'INVITE',b'CANCEL'))
        self.assertEqual(self.dialog.state,'answered')
    def test_remote_bye(self):
        self.dialog.answer('owner','v=0',True)
        self.dialog.receive(INVITE.replace(b'INVITE',b'BYE'))
        self.assertEqual(self.dialog.state,'closed')
    def test_unrelated_ack_does_not_establish(self):
        self.dialog.answer('owner','v=0',True)
        self.dialog.receive(INVITE.replace(b'INVITE',b'ACK').replace(b'22 ACK',b'23 ACK'))
        self.assertEqual(self.dialog.state,'answered')

if __name__=='__main__':unittest.main()
