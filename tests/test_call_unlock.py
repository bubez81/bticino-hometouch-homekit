import unittest
from types import SimpleNamespace as NS
from bticino_call_unlock import open_current_call


class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.sent=[]
        self.dialog=NS(state='established',owner='home')
        self.listener=NS(registered=True,incoming_dialogs={'call':self.dialog},
            media={'call':NS(attachment=NS(owner='home'))},username='test',
            local_ip='192.0.2.1',local_port=5000,cseq=1,send=self.sent.append)

    def run_open(self,owner='home',enabled=True):
        return open_current_call(self.listener,owner,enabled,'example.invalid')

    def test_pair_and_once_only(self):
        self.assertEqual(self.run_open()['state'],'sent_unconfirmed')
        self.assertEqual(len(self.sent),2)
        for packet,body in zip(self.sent,('*8*19*4##','*8*20*4##')):
            self.assertTrue(packet.startswith('MESSAGE sip:MHT@example.invalid SIP/2.0'))
            self.assertTrue(packet.endswith('\r\n\r\n'+body))
            self.assertIn('Content-Length: '+str(len(body)),packet)
        self.assertFalse(self.run_open()['ok'])
        self.assertEqual(len(self.sent),2)

    def test_reject_closed_ringing_unacknowledged(self):
        for state in ('ringing','answered','closing','closed'):
            self.dialog.state=state
            self.assertFalse(self.run_open()['ok'])
        self.assertEqual(self.sent,[])

    def test_disabled_wrong_owner_disconnected_multiple(self):
        self.assertFalse(self.run_open(enabled=False)['ok'])
        self.assertFalse(self.run_open(owner='other')['ok'])
        self.listener.registered=False
        self.assertFalse(self.run_open()['ok'])
        self.listener.registered=True
        self.listener.incoming_dialogs['other']=self.dialog
        self.assertFalse(self.run_open()['ok'])
        self.assertEqual(self.sent,[])

    def test_uncertain_send_is_not_retried(self):
        def fail(packet):raise OSError('Disconnected')
        self.listener.send=fail
        with self.assertRaises(OSError):self.run_open()
        self.listener.send=self.sent.append
        self.assertFalse(self.run_open()['ok'])
        self.assertEqual(self.sent,[])

    def test_missing_attachment_and_header_injection(self):
        self.listener.media['call'].attachment=None
        self.assertFalse(self.run_open()['ok'])
        self.listener.media['call'].attachment=NS(owner='home')
        self.listener.username='bad\r\nHeader'
        with self.assertRaises(ValueError):self.run_open()
        self.assertEqual(self.sent,[])

if __name__=='__main__':unittest.main()
