"""Home Assistant tests: run with pytest and pytest-homeassistant-custom-component."""
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import bticino_api  # noqa: E402

TOKEN = "test-token-0123456789abcdefghij"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeListener:
    """The real listener API server, backed by in-memory commands and images."""

    def __init__(self):
        self.bus = bticino_api.EventBus()
        self.commands = []
        self.server = bticino_api.ApiServer(
            ("127.0.0.1", 0), TOKEN, self.bus,
            lambda: {"entrances": ["esterno", "scala"], "opening_enabled": True,
                     "entrance_classification": True, "capabilities": ["events", "snapshot", "open"]},
            self.command, self.snapshot)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def command(self, request):
        self.commands.append(request)
        return {"ok": True, "state": "sent_unconfirmed", "entrance": request["entrance"]}

    def snapshot(self, width, height):
        return b"\xff\xd8" + f"{width}x{height}".encode()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        for thread in threading.enumerate():
            if thread.name.endswith("(process_request_thread)"):
                thread.join(timeout=5)


@pytest.fixture
def listener(socket_enabled):
    fake = FakeListener()
    yield fake
    fake.close()
