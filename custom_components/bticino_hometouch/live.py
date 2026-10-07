"""Live video for Home Assistant's built-in go2rtc, without a separate go2rtc.

The camera's stream source is a local HTTP URL served here. Each request
relays the listener's MPEG-TS (H.264 with AAC audio) over loopback UDP:
during a ring the call's own feed, otherwise an on-demand camera call that is
closed again when the request ends. As in the go2rtc exec source of earlier
versions, a camera call is never retried: when the camera is busy, the
previous call ended less than CALL_COOLDOWN seconds ago, or no video arrives
within FIRST_VIDEO_TIMEOUT, the listener's local feed (latest snapshot) is
relayed instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import time
from pathlib import Path

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LOCAL_FEED_PORT = 22300
FIRST_VIDEO_TIMEOUT = 12.0
STALL_TIMEOUT = 15.0
CALL_COOLDOWN = 20.0
URL = "/api/bticino_hometouch/live/{entry_id}"


async def ipc_request(path: Path, request: dict, timeout: float = 12) -> dict:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), timeout)
    try:
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        writer.write_eof()
        data = await asyncio.wait_for(reader.read(), timeout)
    finally:
        writer.close()
    return json.loads(data.decode() or "{}")


class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2000)

    def datagram_received(self, data: bytes, addr) -> None:
        if not self.queue.full():
            self.queue.put_nowait(data)


async def _open_udp(port: int):
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("127.0.0.1", port))
    transport, protocol = await loop.create_datagram_endpoint(_Datagrams, sock=sock)
    return transport, protocol


class LiveSource:
    """Per config entry: the listener socket and the last camera call's end."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.secret = secrets.token_urlsafe(24)
        self.last_call_end = 0.0

    def url(self, hass: HomeAssistant, entry_id: str) -> str:
        port = hass.http.server_port if hass.http else 8123
        return f"http://127.0.0.1:{port}{URL.format(entry_id=entry_id)}?k={self.secret}"

    async def stream(self, request: web.Request) -> web.StreamResponse:
        session = f"ha-{secrets.token_hex(6)}"
        call_started = False
        transport = None
        response = web.StreamResponse(headers={"Content-Type": "video/mp2t", "Cache-Control": "no-store"})
        await response.prepare(request)
        try:
            try:
                incoming = (await ipc_request(self.socket_path, {"command": "incoming_status"})).get("incoming")
            except (OSError, ValueError, asyncio.TimeoutError):
                incoming = None
            mode = "local"
            if incoming:
                mode = "incoming"
            elif time.monotonic() - self.last_call_end >= CALL_COOLDOWN:
                transport, protocol = await _open_udp(0)
                port = transport.get_extra_info("sockname")[1]
                try:
                    result = await ipc_request(self.socket_path, {"command": "start_call", "candidate": "1",
                                                                  "session_id": session, "video_port": port, "audio": True})
                except (OSError, ValueError, asyncio.TimeoutError) as err:
                    result = {"ok": False, "error": str(err)}
                if result.get("ok"):
                    call_started, mode = True, "on_demand"
                else:
                    _LOGGER.info("Live: camera call not started (%s); showing the latest picture", result.get("error"))
                    transport.close()
                    transport = None
            if transport is None:
                transport, protocol = await _open_udp(LOCAL_FEED_PORT)
            _LOGGER.debug("Live started (%s)", mode)
            started = time.monotonic()
            last = None
            while True:
                try:
                    data = await asyncio.wait_for(protocol.queue.get(), 0.5)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if last is None and mode == "on_demand" and now - started > FIRST_VIDEO_TIMEOUT:
                        _LOGGER.info("Live: no video from the camera; showing the latest picture")
                        await self._stop_call(session)
                        call_started, mode = False, "local"
                        transport.close()
                        transport, protocol = await _open_udp(LOCAL_FEED_PORT)
                        started = time.monotonic()
                    elif last is not None and now - last > STALL_TIMEOUT:
                        break
                    continue
                last = time.monotonic()
                await response.write(data)
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # the viewer left
        finally:
            if transport is not None:
                transport.close()
            if call_started:
                await self._stop_call(session)
        return response

    async def _stop_call(self, session: str) -> None:
        self.last_call_end = time.monotonic()
        try:
            await ipc_request(self.socket_path, {"command": "stop_call", "session_id": session})
        except (OSError, ValueError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Live: stop_call failed: %s", err)


class LiveView(HomeAssistantView):
    """Local MPEG-TS for go2rtc; protected by a per-start secret, not by login."""

    url = URL
    name = "api:bticino_hometouch:live"
    requires_auth = False

    async def get(self, request: web.Request, entry_id: str) -> web.StreamResponse:
        source: LiveSource | None = request.app[KEY_HASS].data.get(DOMAIN, {}).get(entry_id)
        local = request.remote in ("127.0.0.1", "::1")
        if not local or source is None or not secrets.compare_digest(request.query.get("k", ""), source.secret):
            return web.Response(status=404)
        return await source.stream(request)
