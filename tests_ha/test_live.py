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
            hass.async_create_background_task(feed(), "fake camera")
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
    response = await client.get(f"/api/bticino_hometouch/live/entry1?k={source.secret}")
    assert response.status == 200 and response.headers["Content-Type"] == "video/mp2t"
    data = await response.content.readexactly(188 * 5)
    assert data[0] == 0x47 and data[188] == 0x47
    response.close()
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
    sender.close()
