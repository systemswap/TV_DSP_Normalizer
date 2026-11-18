#!/usr/bin/env bash
# TVDSP - den committeten Stand auf den Pi ausrollen.
#
#   werkzeuge/deploy.sh        -> ausrollen (mit Rueckfrage)
#   werkzeuge/deploy.sh -y     -> ohne Rueckfrage
#
# Rollt NUR die Programmdateien aus:
#     backend/app.py, backend/static/index.html, backend/flirc_listener.py,
#     backend/silero_vad.onnx (Sprachmodell, 2,3 MB)
#
# Fuer alles andere ist install.sh zustaendig (config/tv.yml, bin/*, DSP-Profil,
# systemd-Units, venv, CamillaDSP). Dieses Skript meldet, wenn dort etwas
# abweicht, und aendert es NICHT - tv.yml wird vom Installer geraetespezifisch
# angepasst (Kartenname) und darf nicht blind ueberschrieben werden.
#
# Sicherheitsnetz: Der bisher installierte Stand wird vorher weggesichert.
# Kommt der Dienst danach nicht sauber hoch oder meldet die falsche Version,
# wird automatisch zurueckgerollt.
#
# Die Schutz-Invariante DSP-Parameter 4547=0 wird hier nicht beruehrt; sie
# haengt an bin/enable-spdif-in.sh und dem Dienst tvdsp-spdif.

set -uo pipefail

EIGENER_ORT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO="${REPO:-$(dirname "$EIGENER_ORT")}"
PREFIX="${PREFIX:-/opt/tvdsp}"
DIENST="tvdsp-web"
LAUSCHER="tvdsp-flirc"

rot()   { printf '\033[31m%s\033[0m\n' "$*"; }
gruen() { printf '\033[32m%s\033[0m\n' "$*"; }
gelb()  { printf '\033[33m%s\033[0m\n' "$*"; }
abbruch() { rot "ABBRUCH: $*"; exit 1; }

OHNE_RUECKFRAGE=0
[ "${1:-}" = "-y" ] && OHNE_RUECKFRAGE=1

cd "$REPO" 2>/dev/null || abbruch "Projektverzeichnis $REPO nicht gefunden."
[ -d "$PREFIX" ] || abbruch "$PREFIX existiert nicht - erst install.sh ausfuehren."

# --- 1. Nur committeten Stand ausrollen ------------------------------------
if [ -n "$(git status --porcelain)" ]; then
    rot "Es gibt nicht committete Aenderungen:"
    git status --short | sed 's/^/    /'
    abbruch "Erst werkzeuge/release.sh ausfuehren. Nur committete Staende werden ausgerollt,
sonst laeuft auf dem Pi etwas, das nirgends gesichert und nicht nachvollziehbar ist."
fi

VERSION=$(head -1 VERSION | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
[ -n "$VERSION" ] || abbruch "Keine Version in VERSION gefunden."

# Aktuell installierte Version (Symlink wird dabei aufgeloest)
INSTALLIERT=$(grep -m1 '^VERSION = ' "$PREFIX/backend/app.py" 2>/dev/null | cut -d'"' -f2)
[ -n "$INSTALLIERT" ] || INSTALLIERT="unbekannt"

echo "Auszurollen : $VERSION   (Commit $(git rev-parse --short HEAD))"
echo "Installiert : $INSTALLIERT"
LAEUFT=$(curl -s --max-time 3 "http://127.0.0.1:8080/api/status" 2>/dev/null \
         | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null)
[ -n "$LAEUFT" ] && echo "Laeuft jetzt: $LAEUFT"
echo

# --- 2. Was gehoert zu install.sh, nicht hierher? --------------------------
INSTALLER_NOETIG=0
for f in bin/detect-card.sh bin/enable-spdif-in.sh bin/install-flirc-cli.sh dsp/dspdac-noautomute.xml; do
    if [ -f "$PREFIX/$f" ] && ! cmp -s "$f" "$PREFIX/$f"; then
        gelb "Abweichung: $f   -> dafuer ist install.sh zustaendig"
        INSTALLER_NOETIG=1
    fi
done
[ "$INSTALLER_NOETIG" = 1 ] && { gelb "Dieses Skript rollt das NICHT aus. Danach 'sudo ./install.sh' ausfuehren."; echo; }

# --- 3. Sicherung des installierten Standes --------------------------------
DATEIEN=("backend/app.py:644" "backend/static/index.html:644" "backend/flirc_listener.py:755" "backend/silero_vad.onnx:644")
SICHERUNG="$PREFIX/.vorher/$(date +%Y%m%d-%H%M%S)-v${INSTALLIERT}"
sudo install -d -m755 "$SICHERUNG" || abbruch "Sicherungsordner konnte nicht angelegt werden."
for eintrag in "${DATEIEN[@]}"; do
    rel="${eintrag%%:*}"
    if [ -e "$PREFIX/$rel" ]; then
        sudo install -d -m755 "$SICHERUNG/$(dirname "$rel")"
        # --dereference: bei einem Symlink wird der INHALT gesichert, nicht der Verweis
        sudo cp --dereference "$PREFIX/$rel" "$SICHERUNG/$rel" || abbruch "Sicherung von $rel fehlgeschlagen."
    fi
done
echo "Vorheriger Stand gesichert: $SICHERUNG"

# --- 4. Bestaetigung -------------------------------------------------------
echo
echo "Wird ausgerollt:"
for eintrag in "${DATEIEN[@]}"; do
    rel="${eintrag%%:*}"
    if [ -L "$PREFIX/$rel" ]; then
        echo "    $rel   (ersetzt den Symlink -> $(readlink "$PREFIX/$rel"))"
    elif cmp -s "$rel" "$PREFIX/$rel" 2>/dev/null; then
        echo "    $rel   (unveraendert)"
    else
        echo "    $rel"
    fi
done
echo
if [ "$OHNE_RUECKFRAGE" = 0 ]; then
    read -r -p "Ausrollen und $DIENST neu starten? [j/N] " A
    case "$A" in j|J|y|Y) echo ;; *) abbruch "Auf Wunsch abgebrochen. Nichts veraendert." ;; esac
fi

# --- 5. Kopieren -----------------------------------------------------------
LAUSCHER_NEU=0
cmp -s backend/flirc_listener.py "$PREFIX/backend/flirc_listener.py" 2>/dev/null || LAUSCHER_NEU=1

for eintrag in "${DATEIEN[@]}"; do
    rel="${eintrag%%:*}"; modus="${eintrag##*:}"
    sudo install -d -m755 "$PREFIX/$(dirname "$rel")"
    # Ein vorhandener Symlink MUSS weg: 'install' wuerde sonst durch ihn
    # hindurch in die Quelldatei im Repo schreiben.
    [ -L "$PREFIX/$rel" ] && sudo rm -f "$PREFIX/$rel"
    sudo install -m"$modus" "$rel" "$PREFIX/$rel" || abbruch "Kopieren von $rel fehlgeschlagen."
done
gruen "Dateien kopiert."

# --- 6. Dienste neu starten ------------------------------------------------
sudo systemctl restart "$DIENST" || abbruch "Neustart von $DIENST fehlgeschlagen."
if [ "$LAUSCHER_NEU" = 1 ] && systemctl list-unit-files | grep -q "^${LAUSCHER}.service"; then
    sudo systemctl restart "$LAUSCHER" || gelb "Neustart von $LAUSCHER fehlgeschlagen (nicht kritisch fuer Audio)."
    echo "$LAUSCHER ebenfalls neu gestartet (flirc_listener.py hat sich geaendert)."
fi

# --- 7. Nachpruefen: laeuft es wirklich und meldet die neue Version? -------
echo -n "Warte auf $DIENST "
GEMELDET=""
for i in $(seq 1 20); do
    sleep 1; echo -n "."
    GEMELDET=$(curl -s --max-time 2 "http://127.0.0.1:8080/api/status" 2>/dev/null \
               | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null)
    [ "$GEMELDET" = "$VERSION" ] && break
done
echo

if [ "$GEMELDET" = "$VERSION" ]; then
    gruen "Ausgerollt und geprueft: Backend meldet $GEMELDET."
    echo "  CamillaDSP: $(systemctl is-active camilladsp)   $DIENST: $(systemctl is-active $DIENST)"
    echo "  Rueckweg bei Bedarf: sudo cp $SICHERUNG/backend/app.py $PREFIX/backend/app.py"
    exit 0
fi

# --- 8. Fehlschlag: automatisch zurueckrollen ------------------------------
rot "Der Dienst meldet nicht die erwartete Version (erwartet $VERSION, bekommen '${GEMELDET:-keine Antwort}')."
rot "Rolle auf den vorherigen Stand $INSTALLIERT zurueck ..."
for eintrag in "${DATEIEN[@]}"; do
    rel="${eintrag%%:*}"; modus="${eintrag##*:}"
    [ -f "$SICHERUNG/$rel" ] && sudo install -m"$modus" "$SICHERUNG/$rel" "$PREFIX/$rel"
done
sudo systemctl restart "$DIENST"
sleep 3
ZURUECK=$(curl -s --max-time 3 "http://127.0.0.1:8080/api/status" 2>/dev/null \
          | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null)
if [ -n "$ZURUECK" ]; then
    gruen "Zurueckgerollt - Backend meldet wieder $ZURUECK. Die Box ist bedienbar."
else
    rot "ACHTUNG: Auch nach dem Zurueckrollen antwortet der Dienst nicht."
fi
echo
echo "Letzte Meldungen aus dem Journal:"
sudo journalctl -u "$DIENST" -n 25 --no-pager | sed 's/^/    /'
exit 1
