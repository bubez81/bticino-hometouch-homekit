#!/bin/sh
set -eu

base=/opt/bticino-sniffer
legacy_label=${BTICINO_LEGACY_LABEL:-io.galanti.bticino-sniffer}
new_label=io.github.bubez81.bticino-hometouch
legacy_plist=/Library/LaunchDaemons/$legacy_label.plist
new_plist=/Library/LaunchDaemons/$new_label.plist
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
installer="$script_dir/install.sh"
backup="$base/backups/$(date +%Y-%m-%d_%H-%M-%S)-service-migration"
migrated=false

test "$(id -u)" -eq 0 || {
    echo "Eseguire con sudo" >&2
    exit 1
}
test -f "$installer"
test -f "$legacy_plist" || {
    echo "Servizio storico non trovato: $legacy_label" >&2
    exit 1
}
launchctl print "system/$legacy_label" >/dev/null 2>&1 || {
    echo "Il servizio storico non è caricato: $legacy_label" >&2
    exit 1
}
if launchctl print "system/$new_label" >/dev/null 2>&1; then
    echo "Il nuovo servizio è già caricato; migrazione annullata" >&2
    exit 1
fi
test -f "$base/config.json" || {
    echo "Configurazione privata esistente non trovata" >&2
    exit 1
}

mkdir -p "$backup"
chmod 700 "$base/backups" "$backup"
cp -p "$legacy_plist" "$backup/$legacy_label.plist"

rollback() {
    status=$?
    if test "$status" -ne 0 && test "$migrated" = false; then
        echo "Migrazione non riuscita; riattivazione del servizio storico" >&2
        launchctl bootout "system/$new_label" >/dev/null 2>&1 || true
        rm -f "$new_plist"
        if ! test -f "$legacy_plist"; then
            cp -p "$backup/$legacy_label.plist" "$legacy_plist"
        fi
        restored=false
        attempt=0
        while test "$attempt" -lt 10; do
            if launchctl print "system/$legacy_label" >/dev/null 2>&1; then
                launchctl kickstart -k "system/$legacy_label" || true
                restored=true
                break
            fi
            launchctl bootstrap system "$legacy_plist" >/dev/null 2>&1 || true
            attempt=$((attempt + 1))
            sleep 1
        done
        if test "$restored" != true && \
           ! launchctl print "system/$legacy_label" >/dev/null 2>&1; then
            echo "ATTENZIONE: servizio storico non ricaricato automaticamente" >&2
            echo "Eseguire: launchctl bootstrap system $legacy_plist" >&2
        fi
    fi
    exit "$status"
}
trap rollback EXIT HUP INT TERM

echo "Arresto controllato del servizio storico: $legacy_label"
launchctl bootout "system/$legacy_label"
attempt=0
while launchctl print "system/$legacy_label" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    test "$attempt" -lt 10 || {
        echo "Il servizio storico non si è arrestato entro 10 secondi" >&2
        false
    }
    sleep 1
done

BTICINO_PYTHON=${BTICINO_PYTHON:-/opt/bticino-gateway/venv/bin/python} \
    sh "$installer"

launchctl print "system/$new_label" | grep -q 'state = running'
launchctl print "system/$legacy_label" >/dev/null 2>&1 && {
    echo "Il servizio storico risulta ancora caricato" >&2
    false
}

mv "$legacy_plist" "$backup/$legacy_label.plist.disabled"
migrated=true
trap - EXIT HUP INT TERM

echo "MIGRATION_OK=$legacy_label -> $new_label"
echo "LEGACY_BACKUP=$backup/$legacy_label.plist.disabled"
echo "Il vecchio servizio è archiviato e può essere ripristinato dal backup."
