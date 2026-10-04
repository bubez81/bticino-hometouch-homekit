#!/bin/sh
set -eu

# Camera-ffmpeg invokes this command for each requested stream.  The SIP
# session is short-lived by design; the existing listener remains untouched.
python=${BTICINO_PYTHON:-/opt/bticino-sniffer/python3}
probe=${BTICINO_CAMERA_PROBE:-/opt/bticino-sniffer/probe-camera.py}
candidates=${BTICINO_CAMERA_CANDIDATES:-/opt/bticino-sniffer/camera-candidates.json}
candidate=${BTICINO_CAMERA_CANDIDATE:-1}

test -x "$python"
test -f "$probe"
test -r "$candidates"

"$python" "$probe" --candidates "$candidates" --candidate "$candidate" \
  --prime-udp --decode-frame --stream >/dev/null 2>&1 &
probe_pid=$!
cleanup() {
  kill "$probe_pid" 2>/dev/null || true
  wait "$probe_pid" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

# Let SIP reach 200 OK and publish its media socket before HomeKit reads it.
sleep 1
ffmpeg=${BTICINO_FFMPEG:-/opt/homebrew/opt/ffmpeg/bin/ffmpeg}
test -x "$ffmpeg"
exec "$ffmpeg" "$@"
