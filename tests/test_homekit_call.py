import sys
import socket
import select
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from bticino_homekit_call import CallCommands, MediaAttachment


class CallTests(unittest.TestCase):
    def test_command_runs_on_listener_thread(self):
        caller = threading.get_ident()
        commands = CallCommands(lambda request: {'ok': True, 'thread': threading.get_ident()})
        results = []
        worker = threading.Thread(target=lambda: results.append(commands.request({'command':'test'})))
        worker.start()
        for _ in range(100):
            if not commands.queue.empty(): break
            time.sleep(.001)
        commands.drain(); worker.join(3)
        self.assertEqual(results, [{'ok': True, 'thread': caller}])
        commands.close()
        self.assertFalse(commands.request({})['ok'])

    def test_attachment_is_loopback_and_talk_gated(self):
        capture = SimpleNamespace(audio={'address':'192.0.2.1','port':23000,'rtcp_port':23001}, audio_sockets=[Mock(),Mock()])
        attachment = MediaAttachment(capture,'owner',30000,30002)
        sender = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        packet = b'\x80\x00' + bytes(18)
        try:
            self.assertEqual(attachment.returns[0].getsockname()[0],'127.0.0.1')
            sender.sendto(packet,attachment.returns[0].getsockname())
            select.select(attachment.returns,[],[],1)
            attachment.poll_return()
            capture.audio_sockets[0].sendto.assert_not_called()
            attachment.allowed=True
            sender.sendto(packet,attachment.returns[0].getsockname())
            select.select(attachment.returns,[],[],1)
            attachment.poll_return()
            capture.audio_sockets[0].sendto.assert_called_once_with(packet,('192.0.2.1',23000))
        finally:
            sender.close();attachment.close()

    def test_reject_invalid_ports_before_opening_sockets(self):
        for ports in [(80,30000),(30000,30001),(True,30000),(65535,30000)]:
            with self.assertRaises(ValueError):MediaAttachment(None,'owner',*ports)
