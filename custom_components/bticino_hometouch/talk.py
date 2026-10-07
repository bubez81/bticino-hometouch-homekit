"""Talking to the door from the dashboard card.

The card opens a WebSocket (signed path, Home Assistant login) and sends the
microphone as 16-bit PCM at 8 kHz. It is turned into A-law and sent to the
listener's talk port, the same input the Apple Home plugin uses: during a live
view the camera call carries it to the panel; during a ring the card first
answers the call, and the panel's sound comes back on the same WebSocket as
16-bit PCM at 16 kHz.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
from pathlib import Path

from aiohttp import WSMsgType, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS

from .const import DOMAIN
from .live import ipc_request

_LOGGER = logging.getLogger(__name__)

TALK_PORT = 22310
URL = "/api/bticino_hometouch/talk/{entry_id}"
MAX_FRAME = 16000  # bytes of 8-kHz PCM per message (1 s)


def _alaw_sample(sample: int) -> int:
    """G.711 A-law of one 16-bit sample."""
    magnitude = sample >> 3  # 13 bits, as in the ITU reference coder
    sign = 0x00 if magnitude >= 0 else 0x80
    if magnitude < 0:
        magnitude = -magnitude - 1
    if magnitude < 32:
        code = magnitude >> 1
    else:
        exponent = magnitude.bit_length() - 5
        code = (exponent << 4) | ((magnitude >> exponent) & 0x0F)
    return (code | sign) ^ 0xD5


ALAW = bytes(_alaw_sample(value - 65536 if value >= 32768 else value) for value in range(65536))


def pcm_to_alaw(pcm: bytes) -> bytes:
    """Little-endian 16-bit PCM to A-law (an odd trailing byte is dropped)."""
    return bytes(ALAW[pcm[i] | (pcm[i + 1] << 8)] for i in range(0, len(pcm) - 1, 2))


class _PanelAudio(asyncio.DatagramProtocol):
    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws

    def datagram_received(self, data: bytes, addr) -> None:
        if not self.ws.closed:
            asyncio.ensure_future(self.ws.send_bytes(data))


def _free_ports() -> tuple[int, int, int]:
    """Three loopback ports for the call attachment (video, audio) and the panel's sound."""
    sockets = []
    try:
        for _ in range(3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        video, audio, pcm = (s.getsockname()[1] for s in sockets)
    finally:
        for sock in sockets:
            sock.close()
    if abs(video - audio) < 2:
        audio = video + 10 if video < 65000 else video - 10
    return video, audio, pcm


class TalkSession:
    """One card connection: microphone in, panel sound out while a ring is answered."""

    def __init__(self, socket_path: Path, ws: web.WebSocketResponse) -> None:
        self.socket_path, self.ws = socket_path, ws
        self.owner = f"ha-talk-{secrets.token_hex(6)}"
        self.answered = False
        self.attached = False
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.panel: asyncio.DatagramTransport | None = None

    async def state(self) -> dict:
        try:
            incoming = (await ipc_request(self.socket_path, {"command": "incoming_status"})).get("incoming")
        except (OSError, ValueError, asyncio.TimeoutError):
            incoming = None
        if self.answered and not incoming:
            await self.release()  # the panel ended the call (about a minute)
        return {"type": "state", "ringing": bool(incoming) and not self.answered, "answered": self.answered}

    async def answer(self) -> dict:
        if self.answered:
            return {"type": "answered"}
        video, audio, pcm = _free_ports()
        loop = asyncio.get_running_loop()
        self.panel, _ = await loop.create_datagram_endpoint(
            lambda: _PanelAudio(self.ws), local_addr=("127.0.0.1", pcm))
        try:
            attached = await ipc_request(self.socket_path, {
                "command": "attach_incoming", "session_id": self.owner,
                "video_port": video, "audio_port": audio, "pcm_port": pcm})
            if not attached.get("ok"):
                raise RuntimeError(attached.get("error") or "attach_failed")
            self.attached = True
            answered = await ipc_request(self.socket_path, {
                "command": "answer_incoming", "session_id": self.owner, "enabled": True})
            if not answered.get("ok"):
                raise RuntimeError(answered.get("error") or "answer_failed")
        except (OSError, ValueError, RuntimeError, asyncio.TimeoutError) as err:
            await self.release()
            _LOGGER.info("Talk: ring not answered (%s)", err)
            return {"type": "error", "error": str(err)}
        self.answered = True
        _LOGGER.info("Talk: ring answered from the dashboard")
        return {"type": "answered", "panel_rate": 16000}

    def talk(self, pcm: bytes) -> None:
        if pcm:
            try:
                self.sender.sendto(pcm_to_alaw(pcm[:MAX_FRAME]), ("127.0.0.1", TALK_PORT))
            except OSError:
                pass

    async def release(self) -> None:
        if self.panel is not None:
            self.panel.close()
            self.panel = None
        if self.attached:
            try:
                await ipc_request(self.socket_path, {"command": "release_incoming", "session_id": self.owner})
            except (OSError, ValueError, asyncio.TimeoutError):
                pass
        if self.answered:
            _LOGGER.info("Talk: call ended from the dashboard")
        self.answered = self.attached = False

    def close(self) -> None:
        self.sender.close()


class TalkView(HomeAssistantView):
    """WebSocket for the card's microphone; requires a logged-in user (signed path)."""

    url = URL
    name = "api:bticino_hometouch:talk"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.StreamResponse:
        source = request.app[KEY_HASS].data.get(DOMAIN, {}).get(entry_id)
        if source is None:
            return web.Response(status=404)
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_FRAME * 2)
        await ws.prepare(request)
        session = TalkSession(source.socket_path, ws)
        try:
            await ws.send_json(await session.state())
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    session.talk(message.data)
                elif message.type == WSMsgType.TEXT:
                    try:
                        kind = json.loads(message.data).get("type")
                    except (ValueError, AttributeError):
                        continue
                    if kind == "answer":
                        await ws.send_json(await session.answer())
                    elif kind == "hangup":
                        await session.release()
                        await ws.send_json(await session.state())
                    elif kind == "state":
                        await ws.send_json(await session.state())
                elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            await session.release()
            session.close()
        return ws
