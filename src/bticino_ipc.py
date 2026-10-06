#!/usr/bin/env python3
"""Small loopback-only IPC endpoint for the standalone Homebridge plugin."""
from __future__ import annotations

import json
import os
import socket
import socketserver
import subprocess
import threading
import time
import sys
from pathlib import Path

SOCKET_PATH = Path(os.environ.get("BTICINO_IPC_SOCKET", "/tmp/bticino-hometouch.sock"))
SNAPSHOT_DIR = Path(os.environ.get("BTICINO_SNAPSHOT_DIR", "~/.config/bticino-hometouch/snapshots")).expanduser()
CAMERA_PROBE = os.environ.get("BTICINO_CAMERA_PROBE", "/opt/bticino-sniffer/probe-camera.py")
CAMERA_CANDIDATES = os.environ.get("BTICINO_CAMERA_CANDIDATES", "/opt/bticino-sniffer/camera-candidates.json")
CAMERA_LOG = os.environ.get("BTICINO_CAMERA_LOG") or str(Path(CAMERA_PROBE).parent / "camera-calls.log")
CAMERA_LOG_MAX_BYTES = 1_000_000
_call_process = None
_call_lock = threading.Lock()
_last_event = None
_media_info = None
_call_owner = None
_incoming_state = None
_incoming_commands = None


def set_incoming_state(state):
    """Listener-only publication; never expose SDP, credentials or raw Call-ID."""
    global _incoming_state
    with _call_lock:
        _incoming_state = dict(state) if state else None


def open_camera_log(header):
    """Append-only call log; the probe prints no keys, addresses or credentials."""
    try:
        path = Path(CAMERA_LOG)
        if path.exists() and path.stat().st_size > CAMERA_LOG_MAX_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
        handle = open(path, "a", encoding="utf-8")
        os.chmod(path, 0o600)
        handle.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} {header}\n")
        handle.flush()
        return handle
    except OSError:
        return None


def handle_request(request: dict) -> dict:
    global _call_process, _last_event, _media_info, _call_owner
    command = request.get("command")
    if command in ('attach_incoming', 'answer_incoming', 'release_incoming', 'open_incoming',
                   'open_entrance', 'entrance_status'):
        commands = _incoming_commands
        if commands is None:
            return {'ok': False, 'error': 'incoming_audio_unavailable'}
        return commands.request(request)
    if command == "incoming_status":
        with _call_lock:
            return {"ok": True, "incoming": dict(_incoming_state) if _incoming_state else None}
    if command == "ping":
        return {"ok": True, "command": "ping"}
    if command == "status":
        with _call_lock:
            running = _call_process is not None and _call_process.poll() is None
        return {"ok": True, "command": "status", "state": "calling" if running else "idle"}
    if command == "start_call":
        if os.environ.get("BTICINO_IPC_ENABLE_CALLS") != "1":
            return {"ok": False, "error": "calls_disabled"}
        if not CAMERA_PROBE or not CAMERA_CANDIDATES:
            return {"ok": False, "error": "camera_probe_not_configured"}
        with _call_lock:
            if _incoming_state is not None:
                return {"ok": False, "error": "incoming_call_active"}
            if _call_process is not None and _call_process.poll() is None:
                return {"ok": False, "error": "call_already_running"}
            candidate = str(request.get("candidate", "1"))
            if candidate not in {"1", "2", "3", "4"}:
                return {"ok": False, "error": "invalid_candidate"}
            owner = request.get('session_id')
            port = request.get('video_port')
            if not isinstance(owner, str) or not owner or not isinstance(port, int) or not 1024 <= port <= 65535:
                return {"ok": False, "error": "invalid_stream_session"}
            environment = dict(os.environ, BTICINO_LIVE_VIDEO_PORT=str(port))
            environment.pop("BTICINO_LIVE_AUDIO_PORT", None)
            audio_port = request.get('audio_port')
            if audio_port is not None:
                if request.get('audio') is not True or not isinstance(audio_port, int) or not 1024 <= audio_port <= 65535:
                    return {"ok": False, "error": "invalid_audio_port"}
                environment["BTICINO_LIVE_AUDIO_PORT"] = str(audio_port)
            command = [
                sys.executable, CAMERA_PROBE, "--candidates", CAMERA_CANDIDATES,
                "--candidate", candidate, "--prime-udp", "--stream", "--duration", "300",
            ]
            if request.get('audio') is True:
                command.append("--audio")
            log = open_camera_log(f"start_call candidate={candidate} audio={'--audio' in command}")
            try:
                # Own session: a listener restart must not kill the call before it
                # sends BYE; the probe ends the call itself when the listener is gone.
                _call_process = subprocess.Popen(command, stdout=log or subprocess.DEVNULL,
                                                 stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
                                                 env=environment, start_new_session=True)
            finally:
                if log:
                    log.close()
            _call_owner = owner
        return {"ok": True, "command": command, "state": "calling"}
    if command == "stop_call":
        with _call_lock:
            if request.get('session_id') != _call_owner:
                return {"ok": False, "error": "session_mismatch"}
            if _call_process is None or _call_process.poll() is not None:
                return {"ok": True, "command": command, "state": "idle"}
            _call_process.terminate()
            try:
                # SIP BYE acknowledgement can take five seconds, followed by
                # decoder cleanup. Do not kill the call before that completes.
                _call_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _call_process.kill()
                _call_process.wait(timeout=2)
            _call_process = None
            _call_owner = None
        return {"ok": True, "command": command, "state": "idle"}
    if command == "latest_snapshot":
        snapshots = [p for p in SNAPSHOT_DIR.glob("*.jpg") if p.is_file()]
        latest = max(snapshots, key=lambda p: p.stat().st_mtime, default=None)
        return {"ok": True, "command": command, "path": str(latest) if latest else None}
    if command == "get_event":
        event = _last_event
        _last_event = None
        return {"ok": True, "command": command, "event": event}
    if command == "media_info":
        return {"ok": True, "command": command, "media": _media_info}
    if command == "notify_ring":
        _last_event = {"type": "ring", "entrance": request.get("entrance"), "snapshot": request.get("snapshot")}
        return {"ok": True, "command": command}
    if command == "notify_media":
        _media_info = {k: request.get(k) for k in ("call_id", "rtp_port", "rtcp_port")}
        return {"ok": True, "command": command}
    return {"ok": False, "error": "unsupported_command"}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(1024 * 1024)
        try:
            request = json.loads(line.decode("utf-8"))
            response = handle_request(request)
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {"ok": False, "error": "invalid_json"}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class IPCServer(socketserver.UnixStreamServer):
    allow_reuse_address = True


def serve_forever(path: Path = SOCKET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    server = IPCServer(str(path), _Handler)
    os.chmod(path, 0o600)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    serve_forever()
