#!/usr/bin/env python3
"""go2rtc backchannel relay: speech from the viewer to the entrance panel.

go2rtc starts this program as an `exec:` backchannel source and writes the
viewer's microphone to its stdin as 8 kHz A-law. The program forwards the
bytes to the camera call on loopback UDP, where the probe mixes them into
the Speex audio it sends to the gateway. Nothing is sent while no
on-demand camera call is running.

Example go2rtc stream source:
  - "exec:/usr/bin/python3 /opt/bticino-go2rtc/bticino_talk_relay.py#backchannel=1#audio=alaw/8000"
"""
import argparse
import socket
import sys

TALK_PORT = 22310
CHUNK = 320  # 40 ms of 8 kHz A-law


def relay(source, sock, port=TALK_PORT, chunk=CHUNK):
    total = 0
    while True:
        data = source.read(chunk)
        if not data:
            return total
        sock.sendto(data, ('127.0.0.1', port))
        total += len(data)


def main():
    parser = argparse.ArgumentParser(description='go2rtc backchannel relay for BTicino HOMETOUCH')
    parser.add_argument('--port', type=int, default=TALK_PORT)
    args = parser.parse_args()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        relay(sys.stdin.buffer.raw if hasattr(sys.stdin.buffer, 'raw') else sys.stdin.buffer, sock, args.port)


if __name__ == '__main__':
    main()
