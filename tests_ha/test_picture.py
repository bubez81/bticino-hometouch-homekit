"""Still pictures without errors: latest live frame, ring picture, placeholder, "Refresh picture"."""
import asyncio
import os
import socket
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.bticino_hometouch import live
from custom_components.bticino_hometouch.camera import PLACEHOLDER, HometouchCamera, HometouchSecondCamera


def fake_hub(tmp_path, ring_time=None, snapshot=None):
    hub = MagicMock()
    hub.live_url, hub.talk_url, hub.info, hub.entry_id = None, None, {}, "entry1"
    hub.frames = {0: tmp_path / "camera-0.jpg", 1: tmp_path / "camera-1.jpg"}
    hub.second_frame = hub.frames[1]
    hub.state = {"last_ring": {"time": ring_time}} if ring_time else {}
    hub.api.snapshot = AsyncMock(return_value=snapshot)
    return hub


async def test_camera_picture_is_never_an_error(hass, tmp_path):
    # Nothing yet (no ring, no live view): a neutral picture instead of HTTP 500.
    camera = HometouchCamera(fake_hub(tmp_path))
    camera.hass = hass
    assert await camera.async_camera_image() == PLACEHOLDER.read_bytes()
    second = HometouchSecondCamera(fake_hub(tmp_path, snapshot=b"panel"), "Esterno")
    second.hass = hass
    # Not the panel's picture under the other camera's name.
    assert await second.async_camera_image() == PLACEHOLDER.read_bytes()


async def test_camera_shows_the_newer_of_ring_and_live_frame(hass, tmp_path):
    frame = tmp_path / "camera-0.jpg"
    frame.write_bytes(b"live-frame")
    old = time.time() - 3600
    os.utime(frame, (old, old))
    # A ring after the live view: its picture.
    camera = HometouchCamera(fake_hub(tmp_path, ring_time="2099-01-01T00:00:00+00:00", snapshot=b"ring"))
    camera.hass = hass
    assert await camera.async_camera_image() == b"ring"
    # A live view after the ring: its frame, without asking the listener.
    hub = fake_hub(tmp_path, ring_time="2000-01-01T00:00:00+00:00", snapshot=b"ring")
    camera = HometouchCamera(hub)
    camera.hass = hass
    assert await camera.async_camera_image() == b"live-frame"
    hub.api.snapshot.assert_not_awaited()


async def test_refresh_picture_takes_one_short_call(hass, tmp_path, socket_enabled):
    frame = tmp_path / "camera-0.jpg"
    requests = []
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    async def fake_ipc(path, request, timeout=12):
        requests.append(request)
        if request["command"] == "start_call":
            sender.sendto(b"\x47" + b"\x00" * 187, ("127.0.0.1", request["video_port"]))
        if request["command"] == "stop_call":
            frame.write_bytes(b"new-picture")  # the listener copies the call's last frame
        return {"ok": True}

    source = live.LiveSource(Path("/tmp/unused.sock"))
    with patch.object(live, "ipc_request", fake_ipc), patch.object(live, "CAPTURE_SETTLE", 0), \
         patch.object(live, "CALL_COOLDOWN", 0):
        assert await source.capture(frame) == ""
        assert frame.read_bytes() == b"new-picture"
        start = next(r for r in requests if r["command"] == "start_call")
        assert "audio" not in start and "camera" not in start
        assert [r["command"] for r in requests] == ["start_call", "stop_call"]
        # Pressed again within 30 s: the picture is fresh, no new call.
        assert await source.capture(frame) == ""
        assert [r["command"] for r in requests].count("start_call") == 1
        # This camera's own live view is running: no second call and no error —
        # the live already keeps the picture current.
        os.utime(frame, (time.time() - 60, time.time() - 60))
        source.relay = asyncio.get_running_loop().create_future()
        source.mode = "on_demand"
        with patch.object(live, "CAPTURE_LIVE_WAIT", 0.1):
            assert await source.capture(frame) == ""
        assert [r["command"] for r in requests].count("start_call") == 1
        source.relay.cancel()
    sender.close()


async def test_refresh_picture_during_a_local_replay_still_takes_a_call(hass, tmp_path, socket_enabled):
    """A relay only replaying the latest picture leaves the camera free, so a refresh works."""
    frame = tmp_path / "camera-0.jpg"
    requests = []
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    async def fake_ipc(path, request, timeout=12):
        requests.append(request)
        if request["command"] == "start_call":
            sender.sendto(b"\x47" + b"\x00" * 187, ("127.0.0.1", request["video_port"]))
        if request["command"] == "stop_call":
            frame.write_bytes(b"fresh")
        return {"ok": True}

    source = live.LiveSource(Path("/tmp/unused.sock"))
    source.relay = asyncio.get_running_loop().create_future()
    source.mode = "local"  # the frozen picture is relayed; the camera is not in a call
    with patch.object(live, "ipc_request", fake_ipc), patch.object(live, "CAPTURE_SETTLE", 0), \
         patch.object(live, "CALL_COOLDOWN", 0):
        assert await source.capture(frame) == ""
        assert frame.read_bytes() == b"fresh"
        assert [r["command"] for r in requests] == ["start_call", "stop_call"]
    source.relay.cancel()
    sender.close()


async def test_refresh_picture_waits_for_a_live_on_another_camera(hass, tmp_path, socket_enabled):
    """The gateway takes one call at a time: a real live on another camera blocks a refresh."""
    source = live.LiveSource(Path("/tmp/unused.sock"), camera=1)
    other = live.LiveSource(Path("/tmp/unused.sock"), camera=0)
    other.relay = asyncio.get_running_loop().create_future()
    other.mode = "on_demand"
    source.siblings = other.siblings = [other, source]
    with patch.object(live, "CALL_COOLDOWN", 0):
        assert await source.capture(tmp_path / "camera-1.jpg") == "live_running"
    other.relay.cancel()


async def test_refresh_picture_reports_a_busy_camera(hass, tmp_path, socket_enabled):
    async def busy_ipc(path, request, timeout=12):
        return {"ok": False, "error": "incoming_call_active"}

    source = live.LiveSource(Path("/tmp/unused.sock"))
    with patch.object(live, "ipc_request", busy_ipc), patch.object(live, "CALL_COOLDOWN", 0):
        assert await source.capture(tmp_path / "camera-0.jpg") == "incoming_call_active"
