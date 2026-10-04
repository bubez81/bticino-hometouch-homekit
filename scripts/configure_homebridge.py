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
parser.add_argument(
    "--allow-global-video-processor", action="store_true",
    help="replace the existing Camera-ffmpeg processor (can affect every camera)",
)
args = parser.parse_args()

ffmpeg = os.environ.get("BTICINO_FFMPEG", "ffmpeg")
config = args.config.expanduser().resolve()
if not config.is_file():
    raise SystemExit(f"Homebridge config not found: {config}")
homebridge_dir = config.parent
backup_dir = homebridge_dir / "backups" / "bticino-doorbell"
backup = backup_dir / f"config-{datetime.now():%Y-%m-%d_%H-%M-%S}.json"

try:
    original = config.read_text(encoding="utf-8")
    data = json.loads(original)
except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit(
        f"RIFIUTATO: configurazione Homebridge non valida o illeggibile ({exc}). "
        "Nessuna modifica eseguita."
    )
if not isinstance(data, dict):
    raise SystemExit("RIFIUTATO: config.json deve contenere un oggetto JSON. Nessuna modifica eseguita.")
if "platforms" in data and not isinstance(data["platforms"], list):
    raise SystemExit("RIFIUTATO: il campo platforms non è una lista. Nessuna modifica eseguita.")
platforms = data.setdefault("platforms", [])
camera_platforms = [p for p in platforms if p.get("platform") == "Camera-ffmpeg"]
platform = camera_platforms[0] if camera_platforms else None
if platform is None:
    platform = {
        "name": "Camera FFmpeg",
        "platform": "Camera-ffmpeg",
        "cameras": [],
    }
    platforms.append(platform)
elif len(camera_platforms) > 1:
    # Homebridge permits one dynamic Camera-ffmpeg platform per config.
    # Merge duplicate sections, preserving every camera, then remove extras.
    for duplicate in camera_platforms[1:]:
        if isinstance(duplicate.get("cameras"), list):
            platform.setdefault("cameras", []).extend(duplicate["cameras"])
        platforms.remove(duplicate)
if args.allow_global_video_processor and os.environ.get("BTICINO_VIDEO_PROCESSOR"):
    platform["videoProcessor"] = os.environ["BTICINO_VIDEO_PROCESSOR"]

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
validated = json.loads(encoded)
if not isinstance(validated, dict):
    raise SystemExit("RIFIUTATO: anteprima JSON non valida. Nessuna modifica eseguita.")
if not args.apply:
    print(f"CONFIG_OK={config}")
    print("No changes written. Repeat with --apply after reviewing the path.")
    raise SystemExit(0)

backup_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(config, backup)
try:
    json.loads(backup.read_text(encoding="utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    backup.unlink(missing_ok=True)
    raise SystemExit(f"RIFIUTATO: backup non verificabile ({exc}). Nessuna modifica eseguita.")
temp = config.with_suffix(".json.bticino-new")
with temp.open("w", encoding="utf-8") as handle:
    handle.write(encoded)
    handle.flush()
    os.fsync(handle.fileno())
temp.chmod(0o600)
temp.replace(config)
json.loads(config.read_text(encoding="utf-8"))
print(f"BACKUP={backup}")
print("CONFIG_OK=Camera-ffmpeg/Videocitofono")
