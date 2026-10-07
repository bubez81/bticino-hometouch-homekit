"""Talking from the dashboard card: microphone to the talk port, answering a ring."""
import asyncio
import json
import socket
from pathlib import Path
from unittest.mock import patch

from homeassistant.setup import async_setup_component

from custom_components.bticino_hometouch import live, talk
from custom_components.bticino_hometouch.const import DOMAIN


def test_pcm_to_alaw_matches_the_itu_reference():
    samples = [0, -1, 32767, -32768, 1000, -1000]
    pcm = b"".join(s.to_bytes(2, "little", signed=True) for s in samples) + b"\x01"
    assert talk.pcm_to_alaw(pcm) == bytes([0xD5, 0x55, 0xAA, 0x2A, 0xFA, 0x7A])


async def test_talk_answers_a_ring_and_relays_audio_both_ways(hass, hass_client, socket_enabled):
    assert await async_setup_component(hass, "http", {})
    talk_in = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    talk_in.bind(("127.0.0.1", 0))
    talk_in.settimeout(2)
    panel = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    requests = []
    ringing = {"value": True}

    async def fake_ipc(path, request, timeout=12):
        requests.append(request)
        command = request["command"]
        if command == "incoming_status":
            return {"ok": True, "incoming": {"state": "ringing"} if ringing["value"] else None}
        if command == "attach_incoming":
            ringing["pcm_port"] = request["pcm_port"]
            return {"ok": True, "talk": True, "pcm": True}
        return {"ok": True}

    source = live.LiveSource(Path("/tmp/unused.sock"))
    hass.data.setdefault(DOMAIN, {})["entry1"] = source
    hass.http.register_view(talk.TalkView())
    client = await hass_client()
    with patch.object(talk, "ipc_request", fake_ipc), patch.object(talk, "TALK_PORT", talk_in.getsockname()[1]):
        ws = await client.ws_connect("/api/bticino_hometouch/talk/entry1")
        assert (await ws.receive_json()) == {"type": "state", "ringing": True, "answered": False}

        await ws.send_str(json.dumps({"type": "answer"}))
        assert (await ws.receive_json())["type"] == "answered"
        attach = next(r for r in requests if r["command"] == "attach_incoming")
        answer = next(r for r in requests if r["command"] == "answer_incoming")
        assert answer == {"command": "answer_incoming", "session_id": attach["session_id"], "enabled": True}

        # Panel sound (PCM from the listener) reaches the card.
        panel.sendto(b"\x01\x02" * 160, ("127.0.0.1", ringing["pcm_port"]))
        message = await asyncio.wait_for(ws.receive(), 2)
        assert message.data == b"\x01\x02" * 160

        # The microphone reaches the talk port as A-law.
        await ws.send_bytes((0).to_bytes(2, "little", signed=True) * 160)
        data = await hass.async_add_executor_job(talk_in.recv, 2048)
        assert data == bytes([0xD5]) * 160

        await ws.send_str(json.dumps({"type": "hangup"}))
        await ws.receive_json()
        await ws.close()
    assert [r["command"] for r in requests].count("release_incoming") == 1
    talk_in.close()
    panel.close()


async def test_talk_needs_login(hass, hass_client_no_auth):
    assert await async_setup_component(hass, "http", {})
    hass.data.setdefault(DOMAIN, {})["entry1"] = live.LiveSource(Path("/tmp/unused.sock"))
    hass.http.register_view(talk.TalkView())
    client = await hass_client_no_auth()
    assert (await client.get("/api/bticino_hometouch/talk/entry1")).status == 401
