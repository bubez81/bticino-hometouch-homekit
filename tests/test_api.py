import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_api
from bticino_api import ApiServer, EventBus

TOKEN = 'test-token-0123456789abcdefghij'


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.commands = []
        self.command_result = {'ok': True, 'state': 'sent_unconfirmed', 'entrance': 'scala'}
        self.snapshots = []

        def commands(request):
            self.commands.append(request)
            return self.command_result

        def snapshot(width, height):
            self.snapshots.append((width, height))
            return b'\xff\xd8jpeg'

        self.server = ApiServer(('127.0.0.1', 0), TOKEN, self.bus,
                                lambda: {'entrances': ['esterno', 'scala'], 'capabilities': ['open', 'events']},
                                commands, snapshot)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, method='GET', token=TOKEN):
        request = urllib.request.Request(self.base + path, method=method,
                                         headers={'Authorization': f'Bearer {token}'} if token else {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers.get('Content-Type'), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get('Content-Type'), error.read()

    def test_requires_token(self):
        for token in (None, 'wrong-token-0123456789abcdefgh'):
            status, _, body = self.request('/api/v1/info', token=token)
            self.assertEqual(status, 401)
            self.assertEqual(json.loads(body)['error'], 'unauthorized')

    def test_allowed_clients(self):
        self.server.allowed_clients = {'192.0.2.50'}
        status, _, body = self.request('/api/v1/state')
        self.assertEqual(status, 403)

    def test_short_token_rejected(self):
        with self.assertRaises(ValueError):
            ApiServer(('127.0.0.1', 0), 'short', self.bus, dict, None, None)

    def test_info_and_state(self):
        status, _, body = self.request('/api/v1/info')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['entrances'], ['esterno', 'scala'])
        self.bus.publish('sip_registered')
        self.bus.publish('ring', call='abc123')
        self.bus.publish('entrance_detected', call='abc123', entrance='scala')
        state = json.loads(self.request('/api/v1/state')[2])
        self.assertTrue(state['sip_registered'])
        self.assertTrue(state['call_active'])
        self.assertEqual(state['last_ring']['entrance'], 'scala')
        self.bus.publish('call_ended', call='abc123', reason='cancel')
        self.assertFalse(json.loads(self.request('/api/v1/state')[2])['call_active'])

    def test_open_entrance(self):
        status, _, body = self.request('/api/v1/entrances/scala/open', method='POST')
        self.assertEqual(status, 200)
        self.assertEqual(self.commands, [{'command': 'open_entrance', 'entrance': 'scala'}])
        self.command_result = {'ok': False, 'error': 'entrance_busy'}
        self.assertEqual(self.request('/api/v1/entrances/scala/open', method='POST')[0], 409)
        self.assertEqual(self.request('/api/v1/entrances/Bad%20Name/open', method='POST')[0], 404)
        self.assertEqual(self.request('/api/v1/entrances/SCALA/open', method='POST')[0], 404)
        self.assertEqual(self.request('/api/v1/entrances/scala/open', method='GET')[0], 404)

    def test_snapshot_sizes(self):
        status, content_type, body = self.request('/api/v1/snapshot.jpg?width=1280&height=720')
        self.assertEqual((status, content_type, body), (200, 'image/jpeg', b'\xff\xd8jpeg'))
        self.request('/api/v1/snapshot.jpg')
        self.assertEqual(self.snapshots, [(1280, 720), (None, None)])
        for query in ('?width=10', '?width=x&height=1', '?width=9999&height=10'):
            self.assertEqual(self.request('/api/v1/snapshot.jpg' + query)[0], 400)

    def test_event_stream(self):
        port = self.server.server_address[1]
        with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
            sock.sendall(f'GET /api/v1/events HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n\r\n'.encode())
            data = b''
            while b'event: state' not in data:
                data += sock.recv(4096)
            self.bus.publish('ring', call='def456')
            while b'event: ring' not in data:
                data += sock.recv(4096)
        self.assertIn(b'text/event-stream', data)
        payload = data.split(b'event: ring\ndata: ', 1)[1].split(b'\n', 1)[0]
        event = json.loads(payload)
        self.assertEqual((event['type'], event['call']), ('ring', 'def456'))

    def test_full_subscriber_is_dropped(self):
        subscriber = self.bus.subscribe()
        for index in range(101):
            self.bus.publish('ring', call=str(index))
        self.assertNotIn(subscriber, self.bus.subscribers)


class StartTests(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertIsNone(bticino_api.start({}, EventBus(), dict, None, None))


if __name__ == '__main__':
    unittest.main()
