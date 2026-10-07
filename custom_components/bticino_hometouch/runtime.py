"""Runs the bundled listener inside Home Assistant, as the Homebridge plugin does.

The listener (Python, in ./listener) registers with the HOMETOUCH gateway as
one more phone. It needs an FFmpeg with libspeex, which Home Assistant's own
FFmpeg lacks, so a static build from ffmpeg-for-homebridge is downloaded once
and verified against its published SHA-256.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

_LOGGER = logging.getLogger(__package__)

LISTENER_DIR = Path(__file__).parent / "listener"
FFMPEG_RELEASE = "v2.2.2"
FFMPEG_URL = "https://github.com/homebridge/ffmpeg-for-homebridge/releases/download/{release}/{name}"
FFMPEG_BUILDS = {
    "aarch64": ("ffmpeg-alpine-aarch64.tar.gz", "3ed1724533921b6b54d1fd35802af62abd29e8672e0afb94bb2f8c18158323a3"),
    "arm64": ("ffmpeg-alpine-aarch64.tar.gz", "3ed1724533921b6b54d1fd35802af62abd29e8672e0afb94bb2f8c18158323a3"),
    "x86_64": ("ffmpeg-alpine-x86_64.tar.gz", "b29b9d64111410a322e5e5558d1c4f968864ca44630329dc6cd31295997999a3"),
    "amd64": ("ffmpeg-alpine-x86_64.tar.gz", "b29b9d64111410a322e5e5558d1c4f968864ca44630329dc6cd31295997999a3"),
    "armv7l": ("ffmpeg-alpine-arm32v7.tar.gz", "7ca3a11389c3f1f06d620a3a1949a5ecfdc16112dd88ba2e7e9cdc8ec278d3af"),
}
RESTART_DELAYS = (1, 2, 5, 10, 30, 60)


class SetupError(Exception):
    """Setup problem shown to the user."""


def ensure_ffmpeg(directory: Path, machine: str | None = None, fetch=None) -> Path:
    """Path of the verified FFmpeg, downloading it the first time (blocking)."""
    target = directory / "ffmpeg" / FFMPEG_RELEASE / "ffmpeg"
    if target.exists():
        return target
    machine = (machine or platform.machine()).lower()
    if machine not in FFMPEG_BUILDS:
        raise SetupError(f"Processore {machine} non supportato")
    name, digest = FFMPEG_BUILDS[machine]
    url = FFMPEG_URL.format(release=FFMPEG_RELEASE, name=name)
    if fetch is None:
        def fetch(address):
            with urllib.request.urlopen(address, timeout=120) as response:
                return response.read()
    data = fetch(url)
    if hashlib.sha256(data).hexdigest() != digest:
        raise SetupError("FFmpeg scaricato non valido (impronta SHA-256 diversa)")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = next((m for m in archive.getmembers() if m.isfile() and Path(m.name).name == "ffmpeg"), None)
        if member is None:
            raise SetupError("FFmpeg non trovato nell'archivio")
        content = archive.extractfile(member).read()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o755)
    temporary.replace(target)
    for old in (directory / "ffmpeg").iterdir():
        if old.is_dir() and old.name != FFMPEG_RELEASE:
            shutil.rmtree(old, ignore_errors=True)
    return target


def write_private_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(value, handle, indent=2)
    temporary.replace(path)


def listener_config(storage: Path, ffmpeg: Path, entrances: dict[str, str], api_port: int,
                    gateway: str | None = None) -> dict:
    """onboarding.json with relative private paths resolved, plus our settings."""
    base = json.loads((storage / "onboarding.json").read_text(encoding="utf-8"))
    for key, value in list(base.items()):
        if key.endswith("_file") and isinstance(value, str) and value and not os.path.isabs(value):
            base[key] = str(storage / value)
    if gateway:
        # The gateway at home answers camera calls directly; the cloud server
        # asks for a password the endpoint cannot provide.
        base["sip_server"] = gateway
    return {
        **base,
        "base_dir": str(storage),
        "ffmpeg": str(ffmpeg),
        "audio_ffmpeg": str(ffmpeg),
        "incoming_audio": True,
        "entrance_open_enabled": bool(entrances),
        "entrances": entrances,
        "api": {"enabled": True, "bind": "127.0.0.1", "port": api_port,
                "token_file": str(storage / "private" / "api_token"), "allowed_clients": ["127.0.0.1"]},
    }


class ListenerRuntime:
    """The listener as a supervised child process of Home Assistant."""

    def __init__(self, storage: Path, ffmpeg: Path, entrances: dict[str, str], api_port: int,
                 python: str = sys.executable, gateway: str | None = None) -> None:
        self.storage, self.ffmpeg, self.entrances, self.api_port, self.python = storage, ffmpeg, entrances, api_port, python
        self.gateway = gateway
        self.socket = storage / "hometouch.sock"
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.stopping = False

    def environment(self, config_file: Path) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "BTICINO_SNIFFER_CONFIG": str(config_file),
            "BTICINO_IPC_ENABLED": "1",
            "BTICINO_IPC_ENABLE_CALLS": "1",
            "BTICINO_IPC_SOCKET": str(self.socket),
            "BTICINO_SNAPSHOT_DIR": str(self.storage / "snapshots"),
            "BTICINO_CAMERA_CANDIDATES": str(self.storage / "camera-candidates.json"),
            "BTICINO_CAMERA_LOG": str(self.storage / "camera-calls.log"),
            "BTICINO_CAMERA_PROBE": str(LISTENER_DIR / "probe-camera.py"),
            "BTICINO_LISTENER": str(LISTENER_DIR / "bticino_hometouch_listener.py"),
            "PYTHONPATH": str(LISTENER_DIR),
            # go2rtc reads AAC reliably inside MPEG-TS over HTTP.
            "BTICINO_LIVE_AUDIO_CODEC": "aac",
            "PYTHONUNBUFFERED": "1",
        })
        return env

    async def start(self) -> None:
        self.stopping = False
        self.task = asyncio.create_task(self._run(), name="bticino_hometouch listener")

    async def _run(self) -> None:
        restarts = 0
        loop = asyncio.get_running_loop()
        while not self.stopping:
            config_file = self.storage / "listener.json"
            config = await loop.run_in_executor(None, listener_config, self.storage, self.ffmpeg,
                                                self.entrances, self.api_port, self.gateway)
            await loop.run_in_executor(None, write_private_json, config_file, config)
            started = loop.time()
            self.process = await asyncio.create_subprocess_exec(
                self.python, str(LISTENER_DIR / "bticino_hometouch_listener.py"),
                cwd=str(self.storage), env=self.environment(config_file),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            assert self.process.stdout is not None
            async for raw in self.process.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    _LOGGER.info("[listener] %s", line)
            code = await self.process.wait()
            if self.stopping:
                break
            if loop.time() - started > 120:
                restarts = 0
            delay = RESTART_DELAYS[min(restarts, len(RESTART_DELAYS) - 1)]
            restarts += 1
            _LOGGER.warning("Listener ended (%s); restarting in %s s", code, delay)
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        """SIGTERM lets the listener end calls with BYE; kill only if it hangs."""
        self.stopping = True
        process = self.process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown
                pass
