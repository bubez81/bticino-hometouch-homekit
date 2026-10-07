"""The integration's listener runtime: FFmpeg download check, config and supervision."""
import asyncio
import hashlib
import io
import json
import stat
import tarfile
from unittest.mock import patch

import pytest

from custom_components.bticino_hometouch import entrance_ids, runtime


def ffmpeg_archive(content=b"#!/bin/sh\necho fake ffmpeg\n"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("usr/local/bin/ffmpeg"); info.size = len(content); info.mode = 0o755
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_ffmpeg_download_is_verified_and_cached(tmp_path):
    data = ffmpeg_archive()
    fetched = []
    with patch.dict(runtime.FFMPEG_BUILDS, {"aarch64": ("x.tar.gz", hashlib.sha256(data).hexdigest())}):
        path = runtime.ensure_ffmpeg(tmp_path, "aarch64", fetch=lambda url: fetched.append(url) or data)
        assert path.read_bytes().startswith(b"#!/bin/sh") and path.stat().st_mode & stat.S_IXUSR
        assert runtime.ensure_ffmpeg(tmp_path, "aarch64", fetch=lambda url: 1 / 0) == path   # cached
    assert fetched == [runtime.FFMPEG_URL.format(release=runtime.FFMPEG_RELEASE, name="x.tar.gz")]
    with patch.dict(runtime.FFMPEG_BUILDS, {"x86_64": ("y.tar.gz", "0" * 64)}), pytest.raises(runtime.SetupError):
        runtime.ensure_ffmpeg(tmp_path / "other", "x86_64", fetch=lambda url: data)
    with pytest.raises(runtime.SetupError):
        runtime.ensure_ffmpeg(tmp_path / "other", "sparc")


def test_listener_config_resolves_private_paths(tmp_path):
    (tmp_path / "onboarding.json").write_text(json.dumps({"sip_server": "192.0.2.1", "credentials_file": "private/sip/c.json",
                                                          "ca_file": "/abs/ca.pem"}))
    config = runtime.listener_config(tmp_path, tmp_path / "ffmpeg", {"scala": "20"}, 8791)
    assert config["credentials_file"] == str(tmp_path / "private/sip/c.json") and config["ca_file"] == "/abs/ca.pem"
    assert config["audio_ffmpeg"] == str(tmp_path / "ffmpeg") and config["incoming_audio"] is True
    assert config["entrances"] == {"scala": "20"} and config["entrance_open_enabled"] is True
    assert config["api"]["bind"] == "127.0.0.1" and config["api"]["port"] == 8791


def test_entrance_ids_match_the_plugin():
    assert entrance_ids([{"name": "Scala", "address": "20"}, {"name": "Cancello Esterno", "address": "21"},
                         {"name": "scala", "address": "22"}]) == {"scala": "20", "cancello_esterno": "21", "scala_2": "22"}


async def test_runtime_runs_restarts_and_stops_the_listener(tmp_path, caplog):
    (tmp_path / "onboarding.json").write_text("{}")
    fake = tmp_path / "python"
    marker = tmp_path / "runs"
    fake.write_text(f"#!/bin/sh\necho run >> {marker}\necho REGISTRAZIONE SIP OK\nexec sleep 30\n")
    fake.chmod(0o755)
    caplog.set_level("INFO")
    listener = runtime.ListenerRuntime(tmp_path, tmp_path / "ffmpeg", {}, 8791, python=str(fake))
    await listener.start()
    for _ in range(100):
        if "REGISTRAZIONE SIP OK" in caplog.text:
            break
        await asyncio.sleep(0.05)
    assert "[listener] REGISTRAZIONE SIP OK" in caplog.text
    assert (tmp_path / "listener.json").stat().st_mode & 0o777 == 0o600
    listener.process.kill()                               # crash: restarted after 1 s
    for _ in range(60):
        if marker.read_text().count("run") == 2:
            break
        await asyncio.sleep(0.05)
    assert marker.read_text().count("run") == 2
    await listener.stop()
    assert listener.process.returncode is not None
