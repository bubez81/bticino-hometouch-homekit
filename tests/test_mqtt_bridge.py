import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from bticino_mqtt_bridge import MqttBridge


class Client:
    def __init__(self):self.published=[];self.subscribed=[]
    def publish(self,topic,payload,qos,retain):self.published.append((topic,payload,retain))
    def subscribe(self,topic,qos):self.subscribed.append(topic)


class MqttBridgeTests(unittest.TestCase):
    def setUp(self):
        self.client=Client()
        self.requests=[]
        self.result={'ok':True,'state':'sent_unconfirmed'}
        self.timers=[]
        def ipc(request):
            self.requests.append(request)
            if request['command']=='entrance_status':
                return {'ok':True,'results':{'scala':{'press':'SIP/2.0 200 Ok','release':'SIP/2.0 200 Ok'}}}
            return self.result
        self.bridge=MqttBridge(self.client,ipc,['scala','esterno'],'hometouch',3.0,
            schedule=lambda delay,fn:self.timers.append((delay,fn)),log=lambda _m:None)

    def message(self,topic,payload,retain=False):
        self.bridge.on_message(None,None,NS(topic=topic,payload=payload.encode(),retain=retain))

    def states(self):return [(t,p) for t,p,r in self.client.published if t.endswith('/state')]

    def test_connect_publishes_online_subscribes_and_resets(self):
        self.bridge.on_connect()
        self.assertIn(('hometouch/status','online',True),self.client.published)
        self.assertEqual(self.client.subscribed,['hometouch/scala/set','hometouch/esterno/set'])
        self.assertEqual(self.states(),[('hometouch/scala/state','LOCK'),('hometouch/esterno/state','LOCK')])
        self.assertTrue(all(r for t,p,r in self.client.published))

    def test_unlock_opens_then_locks_after_display_time(self):
        self.message('hometouch/scala/set','unlock')
        self.assertEqual(self.requests,[{'command':'open_entrance','entrance':'scala'}])
        self.assertEqual(self.states(),[('hometouch/scala/state','UNLOCK')])
        self.assertEqual(self.timers[0][0],3.0)
        self.timers[0][1]()
        self.assertEqual(self.states()[-1],('hometouch/scala/state','LOCK'))

    def test_retained_command_never_opens(self):
        self.message('hometouch/scala/set','UNLOCK',retain=True)
        self.assertEqual(self.requests,[])
        self.assertEqual(self.client.published,[])

    def test_repeated_unlock_during_pulse_is_ignored(self):
        self.message('hometouch/scala/set','UNLOCK')
        self.message('hometouch/scala/set','UNLOCK')
        self.message('hometouch/scala/set','LOCK')
        self.assertEqual(len([r for r in self.requests if r['command']=='open_entrance']),1)
        self.assertEqual(self.states(),[('hometouch/scala/state','UNLOCK')])

    def test_rejected_open_returns_to_lock_immediately(self):
        self.result={'ok':False,'error':'entrance_busy'}
        self.message('hometouch/esterno/set','UNLOCK')
        self.assertEqual(self.states(),[('hometouch/esterno/state','UNLOCK'),('hometouch/esterno/state','LOCK')])
        self.assertEqual(self.timers,[])

    def test_ipc_failure_returns_to_lock(self):
        def broken(_request):raise OSError('no socket')
        self.bridge.ipc=broken
        self.message('hometouch/scala/set','UNLOCK')
        self.assertEqual(self.states()[-1],('hometouch/scala/state','LOCK'))

    def test_unknown_topics_and_payloads_are_ignored(self):
        self.message('hometouch/cantina/set','UNLOCK')
        self.message('other/scala/set','UNLOCK')
        self.message('hometouch/scala/set','OPEN')
        self.assertEqual(self.requests,[])


if __name__=='__main__':unittest.main()
