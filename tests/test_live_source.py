import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_live_source as live
from bticino_live_source import LOCAL_FEED_PORT, LiveSource, ffmpeg_command


class Pipe:
    def __init__(self):
        self.data = b''
        self.closed = False

    def write(self, data):
        self.data += data

    def close(self):
        self.closed = True


class Process:
    def __init__(self, args):
        self.args = args
        self.stdin = Pipe()
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class Udp:
    """Feeds scripted datagrams; None means one receive timeout."""

    def __init__(self, port, script):
        self.port = port or 40001
        self.script = script
        self.closed = False

    def getsockname(self):
        return ('127.0.0.1', self.port)

    def recvfrom(self, size):
        item = self.script.pop(0) if self.script else None
        if item is None:
            raise socket.timeout()
        return item, ('127.0.0.1', 1)

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.0
        return self.now


class LiveSourceTests(unittest.TestCase):
    def make(self, responses, udp_script=None):
        self.requests = []
        self.sockets = []
        self.spawned = []
        self.guards = []

        def ipc(request):
            self.requests.append(request)
            answer = responses.get(request['command'])
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, list):
                return answer.pop(0)
            return answer

        def open_udp(port):
            sock = Udp(port, udp_script if udp_script is not None else [])
            self.sockets.append(sock)
            return sock

        def spawn(args, stdin=None):
            process = Process(args)
            self.spawned.append(process)
            return process

        class Guard:
            def __init__(guard, session):
                guard.session = session
                guard.terminated = False
                self.guards.append(guard)

            def terminate(guard):
                guard.terminated = True

        self.cooldown = os.path.join(tempfile.mkdtemp(), 'last-call')
        self.wall_time = 1000.0
        return LiveSource(ipc, '/usr/bin/ffmpeg', 'rtsp://127.0.0.1:8554/out', spawn=spawn,
                          open_udp=open_udp, clock=Clock(), watchdog=Guard,
                          cooldown_file=self.cooldown, wall=lambda: self.wall_time)

    def commands(self):
        return [r['command'] for r in self.requests]

    def test_incoming_call_relays_local_feed_without_camera_call(self):
        source = self.make({'incoming_status': {'ok': True, 'incoming': {'state': 'ringing'}}},
                           [b'ts1', b'ts2'])
        self.assertEqual(source.start(), 'incoming')
        self.assertEqual(self.sockets[0].port, LOCAL_FEED_PORT)
        self.spawned[0].terminated = False
        calls = iter([True, True, False])
        source.relay(lambda: next(calls))
        self.assertEqual(self.spawned[0].stdin.data, b'ts1ts2')
        source.stop()
        self.assertEqual(self.commands(), ['incoming_status'])
        self.assertTrue(self.sockets[0].closed)

    def test_idle_starts_relays_and_stops_on_demand_call(self):
        source = self.make({'incoming_status': {'ok': True, 'incoming': None},
                            'start_call': {'ok': True}, 'stop_call': {'ok': True}}, [b'video'])
        self.assertEqual(source.start(), 'on_demand')
        start = self.requests[1]
        self.assertEqual((start['video_port'], start['session_id']), (40001, source.session))
        self.assertEqual(len(self.guards), 1)
        calls = iter([True, False])
        source.relay(lambda: next(calls))
        source.stop()
        source.stop()
        self.assertTrue(self.spawned[0].terminated)
        self.assertTrue(self.spawned[0].stdin.closed)
        self.assertTrue(self.guards[0].terminated)
        self.assertEqual(self.commands(), ['incoming_status', 'start_call', 'stop_call'])

    def test_no_video_closes_call_and_shows_snapshot_without_retrying(self):
        script = [None] * 13 + [b'snapshot']
        source = self.make({'incoming_status': {'ok': True, 'incoming': None},
                            'start_call': {'ok': True}, 'stop_call': {'ok': True}}, script)
        source.start()
        calls = iter([True] * 15 + [False])
        self.assertEqual(source.relay(lambda: next(calls)), 'stopped')
        self.assertEqual(source.mode, 'local')
        self.assertEqual(self.sockets[-1].port, LOCAL_FEED_PORT)
        self.assertEqual(self.spawned[0].stdin.data, b'snapshot')
        self.assertEqual(self.commands(), ['incoming_status', 'start_call', 'stop_call'])
        source.stop()
        self.assertEqual(self.commands().count('stop_call'), 1)

    def test_recent_call_shows_snapshot_without_calling(self):
        source = self.make({'incoming_status': {'ok': True, 'incoming': None},
                            'start_call': {'ok': True}})
        with open(self.cooldown, 'w') as handle:
            handle.write(str(self.wall_time - 5))
        self.assertEqual(source.start(), 'local')
        self.assertNotIn('start_call', self.commands())
        self.wall_time += 30
        other = self.make({'incoming_status': {'ok': True, 'incoming': None}, 'start_call': {'ok': True}})
        with open(self.cooldown, 'w') as handle:
            handle.write(str(self.wall_time - 30))
        self.assertEqual(other.start(), 'on_demand')

    def test_stop_records_call_end_for_cooldown(self):
        source = self.make({'incoming_status': {'ok': True, 'incoming': None},
                            'start_call': {'ok': True}, 'stop_call': {'ok': True}})
        source.start()
        source.stop()
        with open(self.cooldown) as handle:
            self.assertEqual(float(handle.read()), self.wall_time)

    def test_stall_after_video_ends_stream(self):
        source = self.make({'incoming_status': {'ok': True, 'incoming': {'state': 'established'}}},
                           [b'video'] + [None] * 30)
        source.start()
        self.assertEqual(source.relay(), 'stalled')

    def test_busy_camera_falls_back_to_local_feed(self):
        source = self.make({'incoming_status': {'ok': True, 'incoming': None},
                            'start_call': {'ok': False, 'error': 'call_already_running'}})
        self.assertEqual(source.start(), 'local')
        self.assertTrue(self.sockets[0].closed)
        self.assertEqual(self.sockets[1].port, LOCAL_FEED_PORT)
        source.stop()
        self.assertNotIn('stop_call', self.commands())
        self.assertEqual(self.guards, [])

    def test_listener_unavailable_falls_back_to_local_feed(self):
        source = self.make({'incoming_status': OSError('no socket')})
        self.assertEqual(source.start(), 'local')
        self.assertEqual(self.sockets[0].port, LOCAL_FEED_PORT)

    def test_ffmpeg_reads_stdin_and_copies_video_to_rtsp(self):
        command = ffmpeg_command('ffmpeg', 'rtsp://127.0.0.1:8554/x')
        self.assertEqual(command[command.index('-i') + 1], 'pipe:0')
        self.assertEqual(command[-5:], ['-f', 'rtsp', '-rtsp_transport', 'tcp', 'rtsp://127.0.0.1:8554/x'])
        self.assertIn('-an', command)


class WatchdogTests(unittest.TestCase):
    def test_watchdog_closes_call_after_parent_dies(self):
        import json, os, subprocess, tempfile, threading, time
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, 's')
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(15)
        received = []

        def serve():
            connection, _ = server.accept()
            data = b''
            while not data.endswith(b'\n'):
                data += connection.recv(4096)
            received.append(json.loads(data))
            connection.sendall(b'{"ok":true}')
            connection.close()

        thread = threading.Thread(target=serve)
        thread.start()
        parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        original = os.getpid
        try:
            os.getpid = lambda: parent.pid
            guard = live.watchdog_process(path)('go2rtc-test')
        finally:
            os.getpid = original
        parent.kill()
        parent.wait()
        thread.join(timeout=15)
        guard.wait(timeout=15)
        server.close()
        self.assertEqual(received, [{'command': 'stop_call', 'session_id': 'go2rtc-test'}])


if __name__ == '__main__':
    unittest.main()
