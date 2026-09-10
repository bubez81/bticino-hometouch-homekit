#!/bin/sh
set -eu

base=/opt/bticino-sniffer
service_label=io.github.bubez81.bticino-hometouch
plist=/Library/LaunchDaemons/$service_label.plist
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
python=${BTICINO_PYTHON:-/usr/bin/python3}
ffmpeg=${BTICINO_FFMPEG:-/opt/homebrew/bin/ffmpeg}
generated_config=${BTICINO_CONFIG:-}
candidate=${1:-"$script_dir/../src/bticino_hometouch_listener.py"}
plist_candidate="$script_dir/../packaging/$service_label.plist"
backup="$base/backups/$(date +%Y-%m-%d_%H-%M-%S)"

test "$(id -u)" -eq 0 || { echo "Eseguire con sudo" >&2; exit 1; }
test -f "$candidate"
test -f "$plist_candidate"
test -x "$python"
test -x "$ffmpeg"
if test -n "$generated_config"; then
    test -f "$generated_config"
fi

"$python" -m py_compile "$candidate"
mkdir -p "$backup"
chmod 700 "$base/backups" "$backup"
if test -f "$base/listener.py"; then
    cp -p "$base/listener.py" "$backup/listener.py"
fi
if test -f "$base/config.json"; then
    cp -p "$base/config.json" "$backup/config.json"
fi
if test -f "$plist"; then
    cp -p "$plist" "$backup/$service_label.plist"
fi

rollback() {
    status=$?
    if test "$status" -ne 0; then
        echo "Installazione non riuscita; ripristino $backup/listener.py" >&2
        if test -f "$backup/listener.py"; then
            cp -p "$backup/listener.py" "$base/listener.py"
        fi
        if test -f "$backup/config.json"; then
            cp -p "$backup/config.json" "$base/config.json"
        fi
        if test -f "$backup/$service_label.plist"; then
            cp -p "$backup/$service_label.plist" "$plist"
        fi
        if launchctl print system/$service_label >/dev/null 2>&1; then
            launchctl kickstart -k system/$service_label || true
        else
            launchctl bootstrap system "$plist" || true
        fi
    fi
    exit "$status"
}
trap rollback EXIT HUP INT TERM

install -o root -g wheel -m 700 "$candidate" "$base/listener.py"
ln -sfn "$python" "$base/python3"
chown -h root:wheel "$base/python3"
install -o root -g wheel -m 644 "$plist_candidate" "$plist"
if test -n "$generated_config"; then
    install -o root -g wheel -m 600 "$generated_config" "$base/config.json"
fi
mkdir -p "$base/logs" "$base/snapshots" "$base/runtime"
chown root:wheel "$base/logs" "$base/snapshots" "$base/runtime"
chmod 700 "$base/logs" "$base/snapshots" "$base/runtime"
"$python" -m py_compile "$base/listener.py"

if launchctl print system/$service_label >/dev/null 2>&1; then
    launchctl kickstart -k system/$service_label
else
    launchctl bootstrap system "$plist"
fi
sleep 4
launchctl print system/$service_label | grep -q 'state = running'

trap - EXIT HUP INT TERM
echo "BACKUP=$backup"
launchctl print system/$service_label | sed -n '1,45p'
echo "===== LOG ====="
tail -80 "$base/listener.log"
