#!/usr/bin/env python3
"""Live video source for go2rtc (`exec:` stream) backed by the listener.

go2rtc starts this program when the first viewer opens the stream and stops it
when the last one leaves. The program relays the listener's MPEG-TS over UDP
into FFmpeg's stdin, and FFmpeg publishes H.264 to go2rtc's RTSP `{output}`:

- during an incoming call, the call's early-media video (local UDP feed);
- otherwise an on-demand camera call through the listener IPC (`start_call`),
  closed again with `stop_call`. If no video arrives in time the call is
  closed and retried once, because a call started while the previous one is
  still being torn down may carry no media;
- if the camera call is refused (for example Apple Home is already viewing),
  the local feed, which then repeats the latest snapshot.

Relaying through stdin means FFmpeg ends as soon as this program ends. A small
watchdog process closes an on-demand call even if this program is killed.

Example go2rtc stream:
  videocitofono: "exec:/usr/bin/python3 /opt/bticino-go2rtc/bticino_live_source.py
                  --ffmpeg /opt/homebrew/bin/ffmpeg --output {output}"
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time

DEFAULT_SOCKET = '/tmp/bticino-hometouch.sock'
LOCAL_FEED_PORT = 22300
FIRST_VIDEO_TIMEOUT = 12.0
RETRY_PAUSE = 5.0
STALL_TIMEOUT = 15.0


def ipc_request(path, request, timeout=12):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall((json.dumps(request) + '\n').encode())
        sock.shutdown(socket.SHUT_WR)
        data = b''
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode() or '{}')


def udp_socket(port=0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', port))
    sock.settimeout(0.5)
    return sock


def ffmpeg_command(ffmpeg, output):
    return [ffmpeg, '-hide_banner', '-loglevel', 'warning',
            '-fflags', 'nobuffer+genpts', '-flags', 'low_delay',
            '-analyzeduration', '1000000', '-probesize', '500000',
            '-f', 'mpegts', '-i', 'pipe:0',
            '-map', '0:v:0', '-c:v', 'copy', '-an',
            '-f', 'rtsp', '-rtsp_transport', 'tcp', output]


def log(message):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), 'live-source:', message, file=sys.stderr, flush=True)


class LiveSource:
    def __init__(self, ipc, ffmpeg, output, candidate='1', spawn=subprocess.Popen,
                 open_udp=udp_socket, clock=time.monotonic, sleep=time.sleep, watchdog=None):
        self.ipc = ipc
        self.ffmpeg = ffmpeg
        self.output = output
        self.candidate = candidate
        self.spawn = spawn
        self.open_udp = open_udp
        self.clock = clock
        self.sleep = sleep
        self.watchdog = watchdog
        self.session = f'go2rtc-{os.getpid()}'
        self.call_started = False
        self.process = None
        self.udp = None
        self.guard = None
        self.mode = None

    def _start_call(self):
        try:
            result = self.ipc({'command': 'start_call', 'candidate': self.candidate,
                               'session_id': self.session, 'video_port': self.udp.getsockname()[1]})
        except (OSError, ValueError) as error:
            result = {'ok': False, 'error': str(error)}
        if result.get('ok'):
            self.call_started = True
            if self.watchdog and self.guard is None:
                self.guard = self.watchdog(self.session)
        return result

    def _stop_call(self):
        if not self.call_started:
            return
        self.call_started = False
        try:
            self.ipc({'command': 'stop_call', 'session_id': self.session})
        except (OSError, ValueError) as error:
            log(f'stop_call non riuscito: {error}')

    def choose_feed(self):
        """Open the UDP feed, starting an on-demand call when possible."""
        try:
            incoming = self.ipc({'command': 'incoming_status'}).get('incoming')
        except (OSError, ValueError) as error:
            log(f'listener IPC non disponibile ({error}); uso il flusso locale')
            incoming = None
            self.mode = 'local'
        if incoming:
            self.mode = 'incoming'
        if self.mode in ('incoming', 'local'):
            self.udp = self.open_udp(LOCAL_FEED_PORT)
            return self.mode
        self.udp = self.open_udp(0)
        result = self._start_call()
        if result.get('ok'):
            self.mode = 'on_demand'
        else:
            log(f'chiamata a richiesta non avviata ({result.get("error")}); uso il flusso locale')
            self.udp.close()
            self.udp = self.open_udp(LOCAL_FEED_PORT)
            self.mode = 'local'
        return self.mode

    def start(self):
        mode = self.choose_feed()
        log(f'sorgente {mode} su udp {self.udp.getsockname()[1]}')
        self.process = self.spawn(ffmpeg_command(self.ffmpeg, self.output), stdin=subprocess.PIPE)
        return mode

    def relay(self, running=lambda: True):
        """Copy UDP datagrams to FFmpeg until it exits, the feed stalls or we stop."""
        started = self.clock()
        last = None
        retried = False
        while running() and self.process.poll() is None:
            try:
                data, _ = self.udp.recvfrom(65536)
            except socket.timeout:
                now = self.clock()
                if last is None and self.mode == 'on_demand' and now - started > FIRST_VIDEO_TIMEOUT:
                    if retried:
                        log('nessun video dalla telecamera; chiudo')
                        return 'no_video'
                    log('nessun video dalla telecamera; richiamo')
                    self._stop_call()
                    self.sleep(RETRY_PAUSE)
                    retried = True
                    if not self._start_call().get('ok'):
                        return 'no_video'
                    started = self.clock()
                elif last is not None and now - last > STALL_TIMEOUT:
                    log('video interrotto; chiudo')
                    return 'stalled'
                continue
            last = self.clock()
            try:
                self.process.stdin.write(data)
            except (BrokenPipeError, OSError):
                return 'ffmpeg_closed'
        return 'stopped'

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._stop_call()
        if self.guard is not None:
            self.guard.terminate()
            self.guard = None
        if self.udp is not None:
            self.udp.close()
            self.udp = None


def watchdog_process(socket_path):
    """Start a process that closes the call if this program dies unexpectedly."""
    def start(session):
        code = ('import os,sys,time,json,socket\n'
                'parent,path,session=int(sys.argv[1]),sys.argv[2],sys.argv[3]\n'
                'while True:\n'
                '    try: os.kill(parent,0)\n'
                '    except OSError: break\n'
                '    time.sleep(1)\n'
                's=socket.socket(socket.AF_UNIX); s.settimeout(12); s.connect(path)\n'
                's.sendall((json.dumps({"command":"stop_call","session_id":session})+"\\n").encode())\n'
                's.shutdown(socket.SHUT_WR); s.recv(4096)\n')
        return subprocess.Popen([sys.executable, '-c', code, str(os.getpid()), socket_path, session],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    return start


def main():
    parser = argparse.ArgumentParser(description='go2rtc live source for BTicino HOMETOUCH')
    parser.add_argument('--output', required=True, help='go2rtc {output} RTSP URL')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--socket', default=DEFAULT_SOCKET)
    parser.add_argument('--candidate', default='1', choices=['1', '2', '3', '4'])
    args = parser.parse_args()
    source = LiveSource(lambda request: ipc_request(args.socket, request), args.ffmpeg,
                        args.output, args.candidate, watchdog=watchdog_process(args.socket))
    state = {'running': True}

    def finish(*_args):
        state['running'] = False

    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    source.start()
    try:
        log(f'fine: {source.relay(lambda: state["running"])}')
    finally:
        source.stop()


if __name__ == '__main__':
    main()
