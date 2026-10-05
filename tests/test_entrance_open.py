import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from bticino_entrance_open import EntranceOpener


class Clock:
    def __init__(self):self.now=100.0
    def __call__(self):return self.now


class EntranceOpenerTests(unittest.TestCase):
    def setUp(self):
        self.sent=[]
        self.clock=Clock()
        self.logs=[]
        self.opener=EntranceOpener({'scala':'20','esterno':21},pulse_seconds=1.0,enabled=True,
            clock=self.clock,log=self.logs.append)
        self.sender=NS(username='test',local_ip='192.0.2.1',local_port=5000,cseq=7,send=self.sent.append)

    def body(self,packet):return packet.split('\r\n\r\n',1)[1]

    def call_id(self,packet):
        return next(l.split(': ',1)[1] for l in packet.split('\r\n') if l.startswith('Call-ID: '))

    def test_press_then_release_after_pulse(self):
        result=self.opener.request('scala',self.sender,'example.invalid')
        self.assertEqual(result,{'ok':True,'state':'sent_unconfirmed','entrance':'scala'})
        self.assertEqual([self.body(p) for p in self.sent],['*8*19*20##'])
        self.opener.tick(self.sender,'example.invalid')
        self.assertEqual(len(self.sent),1)
        self.clock.now+=1.0
        self.opener.tick(self.sender,'example.invalid')
        self.assertEqual([self.body(p) for p in self.sent],['*8*19*20##','*8*20*20##'])
        self.assertEqual(self.sender.cseq,9)
        for packet in self.sent:
            self.assertTrue(packet.startswith('MESSAGE sip:MHT@example.invalid SIP/2.0'))
            self.assertIn('Contact: <sip:test@192.0.2.1:5000;transport=tls>',packet)
        self.assertFalse(self.opener.status()['busy'])

    def test_one_pulse_at_a_time(self):
        self.opener.request('scala',self.sender,'example.invalid')
        self.assertEqual(self.opener.request('esterno',self.sender,'example.invalid')['error'],'entrance_busy')
        self.clock.now+=1.0
        self.opener.tick(self.sender,'example.invalid')
        self.assertTrue(self.opener.request('esterno',self.sender,'example.invalid')['ok'])
        self.assertEqual(self.body(self.sent[-1]),'*8*19*21##')

    def test_disabled_and_unknown(self):
        self.assertEqual(self.opener.request('cantina',self.sender,'example.invalid')['error'],'unknown_entrance')
        disabled=EntranceOpener({'scala':'20'},clock=self.clock,log=self.logs.append)
        self.assertEqual(disabled.request('scala',self.sender,'example.invalid')['error'],'opening_disabled')
        self.assertEqual(self.sent,[])

    def test_invalid_configuration(self):
        for entrances in ({'Scala':'20'},{'scala':'2x'},{'scala':'12345'},{'a b':'1'}):
            with self.assertRaises(ValueError):EntranceOpener(entrances)
        with self.assertRaises(ValueError):EntranceOpener({'scala':'20'},pulse_seconds=0)

    def test_release_retried_after_reconnect_until_deadline(self):
        self.opener.request('scala',self.sender,'example.invalid')
        self.clock.now+=1.0
        self.opener.tick(None,'example.invalid')
        self.assertTrue(self.opener.status()['busy'])
        self.clock.now+=5
        self.opener.tick(self.sender,'example.invalid')
        self.assertEqual(self.body(self.sent[-1]),'*8*20*20##')
        self.assertFalse(self.opener.status()['busy'])

    def test_release_given_up_after_deadline(self):
        self.opener.request('scala',self.sender,'example.invalid')
        self.clock.now+=20
        self.opener.tick(None,'example.invalid')
        self.assertFalse(self.opener.status()['busy'])
        self.assertIn('send_failed',self.opener.status()['results']['scala']['release'])

    def test_failed_press_still_releases(self):
        def fail(_packet):raise OSError('Disconnected')
        self.sender.send=fail
        self.assertTrue(self.opener.request('scala',self.sender,'example.invalid')['ok'])
        self.sender.send=self.sent.append
        self.clock.now+=1.0
        self.opener.tick(self.sender,'example.invalid')
        self.assertEqual(self.body(self.sent[-1]),'*8*20*20##')
        self.assertIn('send_failed',self.opener.status()['results']['scala']['press'])

    def test_responses_are_matched_and_recorded(self):
        self.opener.request('scala',self.sender,'example.invalid')
        call_id=self.call_id(self.sent[0])
        self.assertFalse(self.opener.on_response('other@x','SIP/2.0 200 Ok'))
        self.assertTrue(self.opener.on_response(call_id,'SIP/2.0 100 Trying'))
        self.assertTrue(self.opener.on_response(call_id,'SIP/2.0 200 Ok'))
        self.assertFalse(self.opener.on_response(call_id,'SIP/2.0 200 Ok'))
        self.assertEqual(self.opener.status()['results']['scala']['press'],'SIP/2.0 200 Ok')
        self.assertTrue(any('pressione' in line or 'press ->' in line for line in self.logs))

    def test_header_injection_rejected_without_sending(self):
        self.sender.username='bad\r\nHeader'
        self.opener.request('scala',self.sender,'example.invalid')
        self.assertEqual(self.sent,[])
        self.assertIn('send_failed',self.opener.status()['results']['scala']['press'])


if __name__=='__main__':unittest.main()
