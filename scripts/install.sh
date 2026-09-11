#!/bin/sh
set -eu

base=/opt/bticino-sniffer
service_label=io.github.bubez81.bticino-hometouch
plist=/Library/LaunchDaemons/$service_label.plist
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
resolve_program() {
    explicit=$1
    name=$2
    shift 2
    if test -n "$explicit"; then
        test -x "$explicit" || return 1
        printf '%s\n' "$explicit"
        return
    fi
    found=$(command -v "$name" 2>/dev/null || true)
    if test -n "$found" && test -x "$found"; then
        printf '%s\n' "$found"
        return
    fi
    for found in "$@"; do
        if test -x "$found"; then
            printf '%s\n' "$found"
            return
        fi
    done
    return 1
}
python=$(resolve_program "${BTICINO_PYTHON:-}" python3 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3) || {
    echo "Python 3 non trovato; impostare BTICINO_PYTHON" >&2; exit 1;
}
generated_config=${BTICINO_CONFIG:-}
candidate=${1:-"$script_dir/../src/bticino_hometouch_listener.py"}
plist_candidate="$script_dir/../packaging/$service_label.plist"
validator="$script_dir/validate_config.py"
backup="$base/backups/$(date +%Y-%m-%d_%H-%M-%S)"

test "$(id -u)" -eq 0 || { echo "Eseguire con sudo" >&2; exit 1; }
test -f "$candidate"
test -f "$plist_candidate"
test -f "$validator"
test -x "$python"
if test -n "$generated_config"; then
    test -f "$generated_config"
elif ! test -f "$base/config.json"; then
    echo "Prima installazione: indicare BTICINO_CONFIG=/percorso/config.json" >&2
    exit 1
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
had_listener=false
had_config=false
had_plist=false
was_loaded=false
test -f "$backup/listener.py" && had_listener=true
test -f "$backup/config.json" && had_config=true
test -f "$backup/$service_label.plist" && had_plist=true
launchctl print "system/$service_label" >/dev/null 2>&1 && was_loaded=true

rollback() {
    status=$?
    if test "$status" -ne 0; then
        echo "Installazione non riuscita; ripristino $backup/listener.py" >&2
        if test -f "$backup/listener.py"; then
            cp -p "$backup/listener.py" "$base/listener.py"
        elif test "$had_listener" = false; then
            rm -f "$base/listener.py"
        fi
        if test -f "$backup/config.json"; then
            cp -p "$backup/config.json" "$base/config.json"
        elif test "$had_config" = false; then
            rm -f "$base/config.json"
        fi
        if test -f "$backup/$service_label.plist"; then
            cp -p "$backup/$service_label.plist" "$plist"
        elif test "$had_plist" = false; then
            rm -f "$plist"
        fi
        if test "$was_loaded" = true; then
            launchctl kickstart -k "system/$service_label" || true
        else
            launchctl bootout "system/$service_label" >/dev/null 2>&1 || true
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
"$python" "$validator" --config "$base/config.json"

log_lines=0
if test -f "$base/listener.log"; then
    log_lines=$(wc -l < "$base/listener.log" | tr -d ' ')
fi

if launchctl print system/$service_label >/dev/null 2>&1; then
    launchctl kickstart -k system/$service_label
else
    launchctl bootstrap system "$plist"
fi
attempt=0
registered=false
while test "$attempt" -lt 15; do
    if test -f "$base/listener.log" && \
       sed -n "$((log_lines + 1)),\$p" "$base/listener.log" | \
       grep -q 'REGISTRAZIONE SIP OK'; then
        registered=true
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if test "$registered" != true; then
    echo "Registrazione SIP non confermata entro 15 secondi" >&2
    test -f "$base/listener.log" && tail -40 "$base/listener.log" >&2
    false
fi
launchctl print "system/$service_label" | grep -q 'state = running'

trap - EXIT HUP INT TERM
echo "BACKUP=$backup"
echo "HEALTHCHECK_OK=TLS/SIP registration"
launchctl print system/$service_label | sed -n '1,45p'
echo "===== LOG ====="
tail -80 "$base/listener.log"
