"""Local live stream for go2rtc: on-demand camera call, relay, stop when the viewer leaves."""
import asyncio
import json
import socket
import tempfile
from pathlib import Path

from homeassistant.setup import async_setup_component

from custom_components.bticino_hometouch import live
from custom_components.bticino_hometouch.const import DOMAIN


async def test_live_relays_the_camera_call_and_stops_it(hass, hass_client_no_auth, socket_enabled):
    assert await async_setup_component(hass, "http", {})
    requests = []
    feeds = []
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    async def fake_ipc(path, request, timeout=12):
        requests.append(request)
        if request["command"] == "incoming_status":
            return {"ok": True, "incoming": None}
        if request["command"] == "start_call":
            port = request["video_port"]

            async def feed():
                for _ in range(200):
                    sender.sendto(b"\x47" + b"\x00" * 187, ("127.0.0.1", port))
                    await asyncio.sleep(0.01)
            feeds.append(hass.async_create_background_task(feed(), "fake camera"))
        return {"ok": True}

    path = Path("/tmp/unused.sock")
    source = live.LiveSource(path)
    from unittest.mock import patch
    ipc_patch = patch.object(live, 'ipc_request', fake_ipc)
    ipc_patch.start()
    hass.data.setdefault(DOMAIN, {})["entry1"] = source
    hass.http.register_view(live.LiveView())
    client = await hass_client_no_auth()

    assert (await client.get("/api/bticino_hometouch/live/entry1?k=wrong")).status == 404
    # Two viewers at once (go2rtc and Home Assistant) share one camera call.
    response, second = await asyncio.gather(
        client.get(f"/api/bticino_hometouch/live/entry1?k={source.secret}"),
        client.get(f"/api/bticino_hometouch/live/entry1?k={source.secret}"))
    assert response.status == 200 and response.headers["Content-Type"] == "video/mp2t"
    data = await response.content.readexactly(188 * 5)
    assert data[0] == 0x47 and data[188] == 0x47
    assert (await second.content.readexactly(188))[0] == 0x47
    assert [r["command"] for r in requests].count("start_call") == 1
    response.close()
    second.close()
    for _ in range(100):
        if any(r["command"] == "stop_call" for r in requests):
            break
        await asyncio.sleep(0.05)
    start = next(r for r in requests if r["command"] == "start_call")
    assert start["audio"] is True
    assert [r for r in requests if r["command"] == "stop_call"] == [{"command": "stop_call", "session_id": start["session_id"]}]
    # Right after a call, a new viewer gets the local feed, not another camera call.
    assert source.last_call_end > 0
    ipc_patch.stop()
    for task in feeds:
        task.cancel()
    await asyncio.gather(*feeds, return_exceptions=True)
    sender.close()


def test_last_call_lines_keeps_only_the_latest_call(tmp_path):
    log = tmp_path / "camera-calls.log"
    log.write_text("=== 10:00 start_call\nSIP_STATUS=200\n=== 11:00 start_call\nSIP_STATUS=486\n"
                   "[h264 @ 0x1] no frame!\nPROBE accepted=False\n", encoding="utf-8")
    assert live.last_call_lines(log) == ["=== 11:00 start_call", "SIP_STATUS=486", "PROBE accepted=False"]
    assert live.last_call_lines(tmp_path / "missing.log") == []


async def test_live_ends_when_there_is_nothing_to_show(hass, hass_client_no_auth, socket_enabled):
    from unittest.mock import patch
    assert await async_setup_component(hass, "http", {})

    async def fake_ipc(path, request, timeout=12):
        if request["command"] == "start_call":
            return {"ok": False, "error": "call_already_running"}
        return {"ok": True, "incoming": None}

    source = live.LiveSource(Path("/tmp/unused.sock"))
    hass.data.setdefault(DOMAIN, {})["entry2"] = source
    hass.http.register_view(live.LiveView())
    client = await hass_client_no_auth()
    with patch.object(live, "ipc_request", fake_ipc), patch.object(live, "FIRST_VIDEO_TIMEOUT", 0.5), \
            patch.object(live, "LOCAL_FEED_PORT", 0), patch.object(live, "SWITCH_ATTEMPTS", 1):
        response = await client.get(f"/api/bticino_hometouch/live/entry2?k={source.secret}")
        assert await asyncio.wait_for(response.content.read(), 5) == b""


async def test_second_camera_source_asks_for_camera_one(hass, hass_client_no_auth, socket_enabled):
    from unittest.mock import patch
    assert await async_setup_component(hass, "http", {})
    requests = []

    async def fake_ipc(path, request, timeout=12):
        requests.append(request)
        if request["command"] == "incoming_status":
            return {"ok": True, "incoming": {"state": "ringing"}}
        return {"ok": False, "error": "busy"}

    source = live.LiveSource(Path("/tmp/unused.sock"), camera=1)
    hass.data.setdefault(DOMAIN, {})["entry3_1"] = source
    hass.http.register_view(live.LiveView())
    client = await hass_client_no_auth()
    with patch.object(live, "ipc_request", fake_ipc), patch.object(live, "FIRST_VIDEO_TIMEOUT", 0.5), \
            patch.object(live, "LOCAL_FEED_PORT", 0):
        response = await client.get(f"/api/bticino_hometouch/live/entry3_1?k={source.secret}")
        await asyncio.wait_for(response.content.read(), 5)
    # A ring does not hijack the second camera: it still asks for camera 1.
    start = next(r for r in requests if r["command"] == "start_call")
    assert start["camera"] == 1


async def test_switching_camera_ends_the_other_call_first(hass):
    from unittest.mock import patch
    requests = []
    running = {"owner": "ha-scale"}

    async def fake_ipc(path, request, timeout=12):
        requests.append(request)
        if request["command"] == "stop_call":
            running["owner"] = None
            return {"ok": True}
        if request["command"] == "start_call":
            if running["owner"]:
                return {"ok": False, "error": "call_already_running"}
            running["owner"] = request["session_id"]
            return {"ok": True}
        return {"ok": True}

    scale = live.LiveSource(Path("/tmp/unused.sock"))
    outside = live.LiveSource(Path("/tmp/unused.sock"), camera=1)
    scale.siblings = outside.siblings = [scale, outside]
    scale.active_session = "ha-scale"
    with patch.object(live, "ipc_request", fake_ipc):
        result = await outside._start_call("ha-outside", 40000)  # one retry, one second
    assert result["ok"]
    assert {"command": "stop_call", "session_id": "ha-scale"} in requests
    assert scale.active_session is None
    starts = [r for r in requests if r["command"] == "start_call"]
    assert starts[-1]["camera"] == 1 and len(starts) == 2
