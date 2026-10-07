#!/usr/bin/env python3
"""Copy the listener (src/*.py and the camera probe) into the Home Assistant
integration, which HACS installs on its own. Run after changing src/."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "custom_components" / "bticino_hometouch" / "listener"


def sources():
    yield from sorted((ROOT / "src").glob("*.py"))
    yield ROOT / "scripts" / "probe-camera.py"


def main():
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir()
    for source in sources():
        shutil.copy2(source, TARGET / source.name)
    print(f"{len(list(TARGET.iterdir()))} listener files copied")


if __name__ == "__main__":
    main()
