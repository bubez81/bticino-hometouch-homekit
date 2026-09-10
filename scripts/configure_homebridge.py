#!/usr/bin/env python3
import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser(
    description="Add the HOMETOUCH doorbell to an existing Homebridge config"
)
parser.add_argument(
    "--config", type=Path,
    default=Path(os.environ.get(
        "HOMEBRIDGE_CONFIG", Path.home() / ".homebridge" / "config.json"
    )),
)
parser.add_argument(
    "--apply", action="store_true",
    help="write the change; otherwise only validate and preview",
)
args = parser.parse_args()

ffmpeg = os.environ.get("BTICINO_FFMPEG", "ffmpeg")
config = args.config.expanduser().resolve()
if not config.is_file():
    raise SystemExit(f"Homebridge config not found: {config}")
homebridge_dir = config.parent
backup_dir = homebridge_dir / "backups" / "bticino-doorbell"
backup = backup_dir / f"config-{datetime.now():%Y-%m-%d_%H-%M-%S}.json"

data = json.loads(config.read_text(encoding="utf-8"))
platforms = data.setdefault("platforms", [])
platform = next(
    (p for p in platforms if p.get("platform") == "Camera-ffmpeg"), None
)
if platform is None:
    platform = {
    "name": "Camera FFmpeg",
    "platform": "Camera-ffmpeg",
    "videoProcessor": ffmpeg,
    "porthttp": 8767,
    "localhttp": True,
    "cameras": []
    }
    platforms.append(platform)
platform["videoProcessor"] = ffmpeg

camera = {
        "name": "Videocitofono",
        "manufacturer": "BTicino",
        "model": "HOMETOUCH",
        "serialNumber": "HOMETOUCH-BRIDGE",
        "doorbell": True,
        "switches": False,
        "unbridge": False,
        "videoConfig": {
            "source": "-fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 -i udp://127.0.0.1:22300?fifo_size=1000000&overrun_nonfatal=1&timeout=5000000",
            "stillImageSource": "-i http://127.0.0.1:8766/snapshot.jpg",
            "maxStreams": 2,
            "maxWidth": 400,
            "maxHeight": 288,
            "maxFPS": 10,
            "maxBitrate": 300,
            "vcodec": "libx264",
            "audio": False,
            "debug": False
        }
    }
cameras = platform.setdefault("cameras", [])
cameras[:] = [c for c in cameras if c.get("name") != camera["name"]]
cameras.append(camera)

encoded = json.dumps(data, ensure_ascii=False, indent=4) + "\n"
json.loads(encoded)
if not args.apply:
    print(f"CONFIG_OK={config}")
    print("No changes written. Repeat with --apply after reviewing the path.")
    raise SystemExit(0)

backup_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(config, backup)
temp = config.with_suffix(".json.bticino-new")
temp.write_text(encoded, encoding="utf-8")
temp.chmod(0o600)
temp.replace(config)
print(f"BACKUP={backup}")
print("CONFIG_OK=Camera-ffmpeg/Videocitofono")
