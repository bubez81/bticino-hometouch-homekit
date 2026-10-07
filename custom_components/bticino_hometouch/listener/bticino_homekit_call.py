"""Private, owner-scoped media attachment to an existing incoming call."""
import base64
import queue
import socket
import threading


class CallCommands:
    def __init__(self, handler):
        self.handler = handler
        self.queue = queue.Queue(maxsize=8)
        self.closed = False

    def request(self, request):
        if self.closed:
            return {'ok': False, 'error': 'listener_closed'}
        item = [dict(request), threading.Event(), None, False]
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            return {'ok': False, 'error': 'listener_busy'}
        if not item[1].wait(2):
            item[3] = True
            return {'ok': False, 'error': 'listener_timeout'}
        return item[2]

    def drain(self):
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                return
            if item[3]:
                continue
            try:
                item[2] = self.handler(item[0])
            except (ValueError, OSError):
                item[2] = {'ok': False, 'error': 'invalid_call_request'}
            finally:
                item[1].set()

    def close(self):
        self.closed = True
        while not self.queue.empty():
            item = self.queue.get_nowait()
            item[2] = {'ok': False, 'error': 'listener_closed'}
            item[1].set()


class MediaAttachment:
    def __init__(self, capture, owner, video_port, audio_port):
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
            raise ValueError('Invalid owner')
        for port in (video_port, audio_port):
            if type(port) is not int or not 1024 <= port <= 65534:
                raise ValueError('Invalid loopback port')
        if abs(video_port - audio_port) < 2:
            raise ValueError('Overlapping ports')
        self.capture, self.owner = capture, owner
        self.video_port, self.audio_port = video_port, audio_port
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.returns = []
        self.allowed = False
        try:
            for _ in range(20):
                first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                first.bind(('127.0.0.1', 0))
                second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    second.bind(('127.0.0.1', first.getsockname()[1] + 1))
                except (OSError, OverflowError):
                    first.close(); second.close()
                    continue
                self.returns = [first, second]
                for sock in self.returns:
                    sock.setblocking(False)
                break
            if not self.returns:
                raise OSError('No audio pair available')
        except Exception:
            self.sender.close()
            raise

    def description(self):
        audio = self.capture.audio
        return dict(ok=True, video=dict(payload=self.capture.payload, fmtp=self.capture.fmtp,
                    material=self.capture.remote_key), audio=dict(codec=audio['codec'],
                    payload=audio['payload'], material=audio['remote_key'],
                    return_material=self.capture.audio_key, return_port=self.returns[0].getsockname()[1]))

    def forward(self, kind, offset, packet):
        port = self.video_port if kind == 'video' else self.audio_port
        try:
            self.sender.sendto(packet, ('127.0.0.1', port + offset))
        except OSError:
            pass

    def poll_return(self):
        for offset, sock in enumerate(self.returns):
            for _ in range(64):
                try:
                    packet, peer = sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    break
                if not self.allowed or peer[0] != '127.0.0.1' or len(packet) < 12:
                    continue
                audio = self.capture.audio
                port = audio['port'] if offset == 0 else audio['rtcp_port']
                self.capture.audio_sockets[offset].sendto(packet, (audio['address'], port))

    def close(self):
        self.allowed = False
        for sock in self.returns:
            sock.close()
        self.sender.close()
