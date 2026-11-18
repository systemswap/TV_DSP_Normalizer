#!/usr/bin/env bash
# FLIRC CLI im Hintergrund installieren. Dieser Helfer darf den
# TVDSP-Hauptinstaller niemals blockieren und benoetigt keinen USB-Stick.
set -u

LOG=/var/log/tvdsp-flirc-setup.log
exec >>"$LOG" 2>&1
printf '\n[%s] FLIRC-Setup gestartet\n' "$(date -Is)"

if command -v flirc_util >/dev/null 2>&1; then
    echo "flirc_util bereits vorhanden: $(command -v flirc_util)"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive

# Feste, getestete Version (2.8.3). Zuerst aus dem Offline-Paket, das install.sh
# unter /opt/tvdsp/paket ablegt - ohne Internet und ohne Herstellerserver.
FLIRC_VERSION="3.27.19"
LOKAL="${TVDSP_PAKETE:-/opt/tvdsp/paket/abhaengigkeiten/pakete}/flirc"
if ls "$LOKAL"/flirc_"${FLIRC_VERSION}"_*.deb >/dev/null 2>&1; then
    echo "Installiere FLIRC $FLIRC_VERSION aus dem Offline-Paket: $(ls "$LOKAL")"
    timeout --kill-after=5s 120s apt-get install -y -qq \
        -o DPkg::Lock::Timeout=10 "$LOKAL"/*.deb || true
    if command -v flirc_util >/dev/null 2>&1; then
        echo "flirc_util installiert (offline): $(command -v flirc_util)"
        systemctl try-restart tvdsp-flirc.service >/dev/null 2>&1 || true
        exit 0
    fi
fi

# Laufzeitbibliothek aus den normalen Debian/Raspberry-Pi-Quellen.
# Kleine, harte Zeitgrenzen: ein kaputtes Netz oder ein APT-Lock darf
# diesen Hintergrundjob nicht endlos festhalten.
timeout --kill-after=5s 45s apt-get install -y -qq \
    -o DPkg::Lock::Timeout=10 \
    -o Acquire::Retries=1 \
    -o Acquire::http::Timeout=10 \
    -o Acquire::https::Timeout=10 \
    libhidapi-hidraw0 || true

if command -v flirc_util >/dev/null 2>&1; then
    echo "flirc_util nach Abhaengigkeiten vorhanden"
    systemctl try-restart tvdsp-flirc.service >/dev/null 2>&1 || true
    exit 0
fi

# Falls das FLIRC-Repository von einem frueheren Lauf bereits eingerichtet
# wurde, zuerst direkt das Paket probieren. Kein apt update hier: der
# TVDSP-Hauptinstaller hat das bereits gemacht; ein spaeterer Timerlauf
# bekommt bei Bedarf erneut eine Chance.
if apt-cache show flirc >/dev/null 2>&1; then
    timeout --kill-after=5s 45s apt-get install -y -qq \
        -o DPkg::Lock::Timeout=10 \
        -o Acquire::Retries=1 \
        -o Acquire::http::Timeout=10 \
        -o Acquire::https::Timeout=10 \
        "flirc=$FLIRC_VERSION" || true
fi

if command -v flirc_util >/dev/null 2>&1; then
    echo "flirc_util installiert: $(command -v flirc_util)"
    systemctl try-restart tvdsp-flirc.service >/dev/null 2>&1 || true
    exit 0
fi

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# Offizieller Herstellerweg. Download maximal 15 s, das komplette
# Repository-Skript maximal 45 s. Es laeuft ausschliesslich in diesem
# Hintergrunddienst, niemals im interaktiven TVDSP-Installer.
if curl -fsSL --connect-timeout 5 --max-time 15 \
       https://apt.flirc.tv/install.sh -o "$TMP"; then
    echo "FLIRC-Repository-Installer geladen"
    timeout --kill-after=5s 45s bash -c \
        'yes y | env DEBIAN_FRONTEND=noninteractive bash "$1"' _ "$TMP" || true
else
    echo "FLIRC-Repository-Installer derzeit nicht erreichbar"
fi

# Nach Repository-Einrichtung Paket mit eigener Zeitgrenze versuchen.
timeout --kill-after=5s 45s apt-get install -y -qq \
    -o DPkg::Lock::Timeout=10 \
    -o Acquire::Retries=1 \
    -o Acquire::http::Timeout=10 \
    -o Acquire::https::Timeout=10 \
    flirc || true

hash -r 2>/dev/null || true
if command -v flirc_util >/dev/null 2>&1; then
    echo "FLIRC CLI erfolgreich installiert: $(command -v flirc_util)"
    systemctl try-restart tvdsp-flirc.service >/dev/null 2>&1 || true
    exit 0
fi

echo "FLIRC CLI noch nicht installiert; systemd-Timer versucht es spaeter erneut."
exit 1
