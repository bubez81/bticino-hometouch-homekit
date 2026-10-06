import io
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import bticino_talk_relay as talk


class TalkRelayTests(unittest.TestCase):
    def test_relay_forwards_stdin_in_small_datagrams(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(1)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            total = talk.relay(io.BytesIO(b'a' * 700), sender, receiver.getsockname()[1])
        sizes = [len(receiver.recv(2048)) for _ in range(3)]
        receiver.close()
        self.assertEqual(total, 700)
        self.assertEqual(sizes, [320, 320, 60])


if __name__ == '__main__':
    unittest.main()
