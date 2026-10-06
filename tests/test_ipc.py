import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_ipc


class IpcTests(unittest.TestCase):
    def test_incoming_state_is_copy_and_blocks_second_call(self):
        state = {"state": "ringing", "audio_ready": False}
        with patch.object(bticino_ipc, '_incoming_state', None), patch.dict('os.environ', BTICINO_IPC_ENABLE_CALLS='1'), patch.object(bticino_ipc.subprocess, 'Popen') as spawn:
            bticino_ipc.set_incoming_state(state)
            state['state'] = 'closed'
            result = bticino_ipc.handle_request({'command': 'incoming_status'})
            self.assertEqual(result['incoming']['state'], 'ringing')
            result['incoming']['state'] = 'closed'
            self.assertEqual(bticino_ipc.handle_request({'command': 'incoming_status'})['incoming']['state'], 'ringing')
            self.assertEqual(bticino_ipc.handle_request({'command': 'start_call'})['error'], 'incoming_call_active')
            spawn.assert_not_called()
            bticino_ipc.set_incoming_state(None)
            self.assertIsNone(bticino_ipc.handle_request({'command': 'incoming_status'})['incoming'])

    def test_start_call_adds_audio_only_when_requested(self):
        for audio, expected in ((True, True), (None, False), ('yes', False)):
            with patch.object(bticino_ipc, '_incoming_state', None), patch.object(bticino_ipc, '_call_process', None), patch.dict('os.environ', BTICINO_IPC_ENABLE_CALLS='1'), patch.object(bticino_ipc.subprocess, 'Popen') as spawn:
                request = {'command': 'start_call', 'session_id': 's', 'video_port': 40000}
                if audio is not None:
                    request['audio'] = audio
                self.assertTrue(bticino_ipc.handle_request(request)['ok'])
                self.assertEqual('--audio' in spawn.call_args.args[0], expected)

    def test_start_call_logs_to_private_camera_log(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bticino_ipc, 'CAMERA_LOG', str(Path(tmp) / 'calls.log')), patch.object(bticino_ipc, '_incoming_state', None), patch.object(bticino_ipc, '_call_process', None), patch.dict('os.environ', BTICINO_IPC_ENABLE_CALLS='1'), patch.object(bticino_ipc.subprocess, 'Popen') as spawn:
            bticino_ipc.handle_request({'command': 'start_call', 'session_id': 's', 'video_port': 40000, 'audio': True})
            log = Path(tmp) / 'calls.log'
            self.assertIn('start_call candidate=1 audio=True', log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertEqual(spawn.call_args.kwargs['stderr'], bticino_ipc.subprocess.STDOUT)

    def test_stop_allows_sip_cleanup_before_kill(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(bticino_ipc, '_call_process', process), patch.object(bticino_ipc, '_call_owner', 'test'):
            result = bticino_ipc.handle_request({'command':'stop_call', 'session_id':'test'})
            self.assertTrue(result['ok'])
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=8)
            process.kill.assert_not_called()

    def test_entrance_commands_go_to_listener_queue(self):
        commands = Mock()
        commands.request.return_value = {'ok': True, 'state': 'sent_unconfirmed', 'entrance': 'scala'}
        with patch.object(bticino_ipc, '_incoming_commands', commands):
            result = bticino_ipc.handle_request({'command': 'open_entrance', 'entrance': 'scala'})
            self.assertEqual(result['entrance'], 'scala')
            commands.request.assert_called_once_with({'command': 'open_entrance', 'entrance': 'scala'})
            bticino_ipc.handle_request({'command': 'entrance_status'})
            self.assertEqual(commands.request.call_count, 2)
        with patch.object(bticino_ipc, '_incoming_commands', None):
            self.assertFalse(bticino_ipc.handle_request({'command': 'open_entrance', 'entrance': 'scala'})['ok'])

    def test_ping(self):
        self.assertEqual(bticino_ipc.handle_request({"command": "ping"})["ok"], True)

    def test_unknown_command_is_rejected(self):
        result = bticino_ipc.handle_request({"command": "open_gate"})
        self.assertEqual(result, {"ok": False, "error": "unsupported_command"})

    def test_latest_snapshot_does_not_expose_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            old = bticino_ipc.SNAPSHOT_DIR
            bticino_ipc.SNAPSHOT_DIR = Path(directory)
            try:
                result = bticino_ipc.handle_request({"command": "latest_snapshot"})
                self.assertIsNone(result["path"])
            finally:
                bticino_ipc.SNAPSHOT_DIR = old


if __name__ == "__main__":
    unittest.main()
