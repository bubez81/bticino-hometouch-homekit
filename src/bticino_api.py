"""Authenticated local-network API for home automation integrations.

REST endpoints plus a Server-Sent Events stream, built on the standard library
so the listener keeps running on a plain Python 3.9 installation:

  GET  /api/v1/info                       version, entrances, capabilities
  GET  /api/v1/state                      current state snapshot
  GET  /api/v1/events                     text/event-stream of events
  GET  /api/v1/snapshot.jpg?width=&height= latest image, optionally scaled
  POST /api/v1/entrances/<name>/open      open an entrance

Every request needs `Authorization: Bearer <token>`. The token is read from a
private file and compared in constant time. Optional `allowed_clients` limits
source addresses. Events carry no SIP identifiers, keys or media addresses.
"""
import hmac
import json
import queue
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

API_VERSION = 1
EVENT_HISTORY = 50
KEEPALIVE_SECONDS = 15
MAX_SUBSCRIBERS = 8
ENTRANCE_PATH = re.compile(r'^/api/v1/entrances/([a-z0-9_-]{1,32})/open$')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class EventBus:
    """Thread-safe fan-out of events plus the derived current state."""

    def __init__(self):
        self.lock = threading.Lock()
        self.subscribers = set()
        self.history = []
        self.sequence = 0
        self.state = {'sip_registered': False, 'call_active': False, 'last_ring': None,
                      'last_entrance_open': None}

    def publish(self, event_type, **data):
        with self.lock:
            self.sequence += 1
            event = {'id': self.sequence, 'type': event_type, 'time': now_iso(), **data}
            self._apply(event)
            self.history = (self.history + [event])[-EVENT_HISTORY:]
            for subscriber in list(self.subscribers):
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    self.subscribers.discard(subscriber)
        return event

    def _apply(self, event):
        kind = event['type']
        if kind == 'sip_registered':
            self.state['sip_registered'] = True
        elif kind == 'sip_disconnected':
            self.state['sip_registered'] = False
        elif kind == 'ring':
            self.state['call_active'] = True
            self.state['last_ring'] = {'time': event['time'], 'call': event.get('call'), 'entrance': None}
        elif kind == 'entrance_detected':
            ring = self.state['last_ring']
            if ring and ring.get('call') == event.get('call'):
                ring['entrance'] = event.get('entrance')
        elif kind == 'call_ended':
            self.state['call_active'] = False
        elif kind == 'entrance_open':
            self.state['last_entrance_open'] = {k: event.get(k) for k in ('time', 'entrance', 'result')}

    def snapshot_state(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def subscribe(self):
        with self.lock:
            if len(self.subscribers) >= MAX_SUBSCRIBERS:
                return None
            subscriber = queue.Queue(maxsize=100)
            self.subscribers.add(subscriber)
            return subscriber

    def unsubscribe(self, subscriber):
        with self.lock:
            self.subscribers.discard(subscriber)

    def close_streams(self):
        """Wake every open stream so its handler thread ends promptly."""
        with self.lock:
            subscribers, self.subscribers = list(self.subscribers), set()
        for subscriber in subscribers:
            while True:
                try:
                    subscriber.put_nowait(None)
                    break
                except queue.Full:
                    try:
                        subscriber.get_nowait()
                    except queue.Empty:
                        pass


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token, bus, info, commands, snapshot, allowed_clients=None, log=print):
        if len(token or '') < 24:
            raise ValueError('API token must be at least 24 characters')
        self.token = token.encode()
        self.bus = bus
        self.info = info
        self.commands = commands
        self.snapshot = snapshot
        self.allowed_clients = set(allowed_clients or [])
        self.log = log
        super().__init__(address, ApiHandler)

    def shutdown(self):
        self.bus.close_streams()
        super().shutdown()


class ApiHandler(BaseHTTPRequestHandler):
    server_version = 'bticino-hometouch-api'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        return

    def authorized(self):
        if self.server.allowed_clients and self.client_address[0] not in self.server.allowed_clients:
            self.send_json(403, {'ok': False, 'error': 'client_not_allowed'})
            return False
        header = self.headers.get('Authorization', '')
        supplied = header[7:].encode() if header.startswith('Bearer ') else b''
        if not hmac.compare_digest(supplied, self.server.token):
            self.send_json(401, {'ok': False, 'error': 'unauthorized'})
            return False
        return True

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authorized():
            return
        url = urlsplit(self.path)
        if url.path == '/api/v1/info':
            self.send_json(200, {'ok': True, 'api_version': API_VERSION, **self.server.info()})
        elif url.path == '/api/v1/state':
            self.send_json(200, {'ok': True, **self.server.bus.snapshot_state()})
        elif url.path == '/api/v1/events':
            self.stream_events()
        elif url.path == '/api/v1/snapshot.jpg':
            self.send_snapshot(parse_qs(url.query))
        else:
            self.send_json(404, {'ok': False, 'error': 'not_found'})

    def do_POST(self):
        if not self.authorized():
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(min(length, 4096))
        match = ENTRANCE_PATH.match(urlsplit(self.path).path)
        if not match:
            self.send_json(404, {'ok': False, 'error': 'not_found'})
            return
        try:
            result = self.server.commands({'command': 'open_entrance', 'entrance': match.group(1)})
        except Exception as error:  # the listener may be reconnecting
            result = {'ok': False, 'error': f'listener_unavailable: {type(error).__name__}'}
        status = 200 if result.get('ok') else {'unknown_entrance': 404, 'entrance_busy': 409,
                                                 'opening_disabled': 403}.get(result.get('error'), 503)
        self.send_json(status, result)

    def send_snapshot(self, query):
        try:
            width = int(query.get('width', ['0'])[0])
            height = int(query.get('height', ['0'])[0])
        except ValueError:
            self.send_json(400, {'ok': False, 'error': 'invalid_size'})
            return
        if not (0 <= width <= 3840 and 0 <= height <= 2160) or bool(width) != bool(height):
            self.send_json(400, {'ok': False, 'error': 'invalid_size'})
            return
        image = self.server.snapshot(width or None, height or None)
        if not image:
            self.send_json(503, {'ok': False, 'error': 'snapshot_unavailable'})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(image)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(image)

    def stream_events(self):
        subscriber = self.server.bus.subscribe()
        if subscriber is None:
            self.send_json(503, {'ok': False, 'error': 'too_many_streams'})
            return
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.write_event({'id': 0, 'type': 'state', 'time': now_iso(), **self.server.bus.snapshot_state()})
            while True:
                try:
                    event = subscriber.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
                    continue
                if event is None:
                    break
                self.write_event(event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.bus.unsubscribe(subscriber)

    def write_event(self, event):
        self.wfile.write(f'id: {event["id"]}\nevent: {event["type"]}\ndata: {json.dumps(event)}\n\n'.encode())
        self.wfile.flush()


def read_token(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read().strip()


def start(config, bus, info, commands, snapshot, log=print):
    """Start the API from listener config['api']; returns the server or None."""
    api = config.get('api') or {}
    if api.get('enabled') is not True:
        return None
    server = ApiServer((api.get('bind', '127.0.0.1'), int(api.get('port', 8790))),
                       read_token(api['token_file']), bus, info, commands, snapshot,
                       api.get('allowed_clients'), log)
    threading.Thread(target=server.serve_forever, name='bticino-api', daemon=True).start()
    log(f"API di rete attiva su {api.get('bind', '127.0.0.1')}:{server.server_address[1]}"
        f"{' (client autorizzati: ' + ', '.join(sorted(server.allowed_clients)) + ')' if server.allowed_clients else ''}")
    return server
