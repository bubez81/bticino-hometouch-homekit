import io
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
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])

    def test_start_call_passes_audio_port_only_with_audio(self):
        with patch.object(bticino_ipc, '_incoming_state', None), patch.object(bticino_ipc, '_call_process', None), patch.dict('os.environ', BTICINO_IPC_ENABLE_CALLS='1'), patch.object(bticino_ipc.subprocess, 'Popen') as spawn:
            base = {'command': 'start_call', 'session_id': 's', 'video_port': 40000}
            self.assertEqual(bticino_ipc.handle_request(dict(base, audio_port=40002))['error'], 'invalid_audio_port')
            self.assertEqual(bticino_ipc.handle_request(dict(base, audio=True, audio_port=80))['error'], 'invalid_audio_port')
            spawn.assert_not_called()
            self.assertTrue(bticino_ipc.handle_request(dict(base, audio=True, audio_port=40002))['ok'])
            self.assertEqual(spawn.call_args.kwargs['env']['BTICINO_LIVE_AUDIO_PORT'], '40002')

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


class StaleSocketTests(unittest.TestCase):
    def test_removes_dead_socket_and_refuses_a_live_one(self):
        import socket
        directory = tempfile.mkdtemp(dir='/tmp')
        path = Path(directory) / 's'
        dead = socket.socket(socket.AF_UNIX); dead.bind(str(path)); dead.close()   # file left behind
        bticino_ipc.remove_stale_socket(path)
        self.assertFalse(path.exists())
        live = socket.socket(socket.AF_UNIX); live.bind(str(path)); live.listen(1)
        with self.assertRaises(RuntimeError):
            bticino_ipc.remove_stale_socket(path)
        self.assertTrue(path.exists())
        live.close(); path.unlink()
        bticino_ipc.remove_stale_socket(path)   # nothing there: no error


class HandlerTests(unittest.TestCase):
    def test_a_client_that_left_does_not_raise(self):
        class Gone(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError(32, "Broken pipe")
        handler = bticino_ipc._Handler.__new__(bticino_ipc._Handler)
        handler.rfile = io.BytesIO(b'{"command":"ping"}\n')
        handler.wfile = Gone()
        handler.handle()  # no exception, nothing printed by socketserver
