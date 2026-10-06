import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import bticino_hometouch_listener as listener
from bticino_incoming_dialog import IncomingDialog

INVITE = (b'INVITE sip:local SIP/2.0\r\nVia: SIP/2.0/TLS gateway;branch=z9hG4bKtest\r\n'
          b'From: <sip:door>;tag=remote\r\nTo: <sip:local>\r\nCall-ID: test\r\n'
          b'CSeq: 1 INVITE\r\nContact: <sip:door@192.0.2.10>\r\nContent-Length: 0\r\n\r\n')


class ListenerTests(unittest.TestCase):
    def setUp(self):
        log_patch = patch.object(listener, 'log')
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.client = listener.HomtouchListener.__new__(listener.HomtouchListener)
        self.client.send = Mock()
        self.client.respond_basic = Mock()
        self.capture = Mock(answer_sdp='test-sdp')
        self.client.media = {'test': self.capture}
        self.client.dialog_tags = {'test': 'local'}
        self.dialog = IncomingDialog(INVITE, '<sip:local@192.0.2.2>', self.client.send, 'local')
        self.client.incoming_dialogs = {'test': self.dialog}

    def test_retransmission_reuses_receiver(self):
        self.client.start_early_media(INVITE)
        self.assertIs(self.client.media['test'], self.capture)
        self.client.respond_basic.assert_called_once_with(INVITE, 183, 'Session Progress', 'local', 'test-sdp')

    def test_cancel_closes_receiver_and_sends_487(self):
        cancel = INVITE.replace(b'INVITE', b'CANCEL')
        with patch.object(listener, 'IPC_MODULE') as ipc:
            self.client.handle_request(cancel)
            ipc.set_incoming_state.assert_called_with(None)
        self.assertEqual(self.client.media, {})
        self.assertEqual(self.client.incoming_dialogs, {})
        self.assertEqual(self.client.send.call_count, 2)
        self.assertIn('487 Request Terminated', self.client.send.call_args.args[0])
        self.capture.stop.assert_called_once()

    def test_cancel_after_answer_does_not_kill_call(self):
        self.dialog.answer('owner', 'sdp', audio_ready=True)
        with patch.object(listener, 'IPC_MODULE'):
            self.client.handle_request(INVITE.replace(b'INVITE', b'CANCEL'))
        self.assertIn('test', self.client.media)
        self.capture.stop.assert_not_called()

    def test_timeout_clears_published_call(self):
        self.capture.poll.return_value = False
        self.capture.started = 0
        with patch.object(listener, 'IPC_MODULE') as ipc:
            self.client.maintain_media()
            ipc.set_incoming_state.assert_called_with(None)
        self.assertEqual(self.client.incoming_dialogs, {})

    def test_answered_call_is_not_cut_after_35_seconds(self):
        self.capture.poll.return_value = False
        self.dialog.answer('owner', 'sdp', audio_ready=True)
        with patch.object(listener, 'IPC_MODULE'), patch.object(listener.time, 'time', return_value=1000.0):
            self.capture.started = 1000.0 - 60
            self.client.maintain_media()
            self.capture.stop.assert_not_called()
            self.capture.started = 1000.0 - listener.ANSWERED_MEDIA_TIMEOUT - 1
            self.client.maintain_media()
        self.capture.stop.assert_called_once_with('timeout')

    def test_explicit_answer_muted_preserves_microphone_gate(self):
        self.capture.attachment = Mock(owner='home', allowed=True)
        with patch.object(self.client, 'publish_incoming_state'):
            result = self.client.handle_call_command(dict(command='answer_incoming', session_id='home', enabled=False, answer=True))
        self.assertTrue(result['ok'])
        self.assertEqual(self.dialog.state, 'answered')
        self.assertFalse(self.capture.attachment.allowed)
        self.assertEqual(self.dialog.owner, 'home')

    def test_mute_alone_does_not_answer(self):
        self.capture.attachment = Mock(owner='home', allowed=True)
        result = self.client.handle_call_command(dict(command='answer_incoming', session_id='home', enabled=False))
        self.assertTrue(result['ok'])
        self.assertEqual(self.dialog.state, 'ringing')
        self.client.send.assert_not_called()

    def test_wrong_session_cannot_answer(self):
        self.capture.attachment = Mock(owner='home', allowed=False)
        result = self.client.handle_call_command(dict(command='answer_incoming', session_id='wrong', enabled=False, answer=True))
        self.assertEqual(result['error'], 'session_mismatch')
        self.client.send.assert_not_called()
