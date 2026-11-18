#!/usr/bin/env bash
# TVDSP - Komplett-Backup auf eine Netzwerkfreigabe (NAS, SMB).
# Server: Umgebungsvariable BACKUP_SERVER oder erste Zeile von
# ~/.config/tvdsp/backup-server; Freigabe "homes", Ordner TVDSP_GIT_Backup.
#
# Legt pro Version EINE Zip-Datei tvdsp-x_y_z.zip im Ordner TVDSP_GIT_Backup ab
# und prueft sie anschliessend VOM NAS aus zurueck. Die Zip enthaelt alles:
#
#   tvdsp-x.y.z/                 das komplette Projekt, direkt installierbar
#   tvdsp-x.y.z/.git/            die volle Git-Historie (alle Commits und Tags)
#   tvdsp-x.y.z/geraetestand/    Einstellungen vom Pi (EQ, Band, R128, IR,
#                                Kalibrierung, Pegel) und die FLIRC-Tasten
#
# Oeffnen laesst sie sich mit jedem Programm (Explorer, Handy, unzip). Seit
# 2.8.2 ersetzt sie das fruehere Git-Bundle, das nur mit git lesbar war.
#
# Aufruf: automatisch durch werkzeuge/release.sh, oder von Hand nach einem Commit.
#
# Es wird NIE etwas auf dem NAS geloescht oder ueberschrieben (Projektregel).

set -uo pipefail

# Projektordner aus dem eigenen Ort ableiten - das Skript liegt in werkzeuge/.
EIGENER_ORT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO="${REPO:-$(dirname "$EIGENER_ORT")}"
SERVER="${BACKUP_SERVER:-$(head -1 "${XDG_CONFIG_HOME:-$HOME/.config}/tvdsp/backup-server" 2>/dev/null)}"
[ -n "$SERVER" ] || [ -n "${BACKUP_ZIEL:-}" ] || { echo "BACKUP_SERVER setzen oder in ~/.config/tvdsp/backup-server eintragen." >&2; exit 1; }
FREIGABE="homes"
UNTERORDNER="TVDSP_GIT_Backup"
# Fester Einhaengepunkt (fstab + Automount, ab 2.9.3);
# ohne ihn wie bisher der gvfs-Mount der Desktop-Sitzung.
FEST="/mnt/nas"
if [ -d "${FEST}/${UNTERORDNER}" ]; then
    MOUNT="$FEST"
else
    MOUNT="/run/user/$(id -u)/gvfs/smb-share:server=${SERVER},share=${FREIGABE}"
fi
ZIEL="${BACKUP_ZIEL:-${MOUNT}/${UNTERORDNER}}"   # BACKUP_ZIEL: Testlauf in einen lokalen Ordner
ZUSATZ="${BACKUP_ZUSATZ:-}"   # z. B. BACKUP_ZUSATZ=vor_umbau -> tvdsp-x_y_z_vor_umbau.zip
[[ -z "$ZUSATZ" || "$ZUSATZ" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "BACKUP_ZUSATZ nur mit Buchstaben, Ziffern, _ und -" >&2; exit 1; }
GERAET="${GERAET:-/opt/tvdsp/config}"     # Laufzeitdaten des installierten Geraets

# Dateien, ohne die install.sh nicht laeuft (gleiche Liste wie dort) - die
# Pruefung am Ende stellt sicher, dass sie in der Zip wirklich drin sind.
PFLICHT="install.sh VERSION config/tv.yml backend/app.py backend/static/index.html
backend/flirc_listener.py backend/silero_vad.onnx bin/detect-card.sh
bin/enable-spdif-in.sh bin/install-flirc-cli.sh dsp/dspdac-noautomute.xml
abhaengigkeiten/requirements.txt abhaengigkeiten/requirements-vad.txt
abhaengigkeiten/pakete/SHA256SUMS abhaengigkeiten/pakete/requirements.txt"

rot()  { printf '\033[31m%s\033[0m\n' "$*"; }
gruen(){ printf '\033[32m%s\033[0m\n' "$*"; }
gelb() { printf '\033[33m%s\033[0m\n' "$*"; }

abbruch() { rot "ABBRUCH: $*"; exit 1; }

# Schreibt die Wiederherstellungs-Anleitung; nennt immer die NEUESTE Zip,
# die tatsaechlich auf dem NAS liegt.
anleitung_schreiben() {
    local neueste ordner
    neueste=$(ls -1t "${ZIEL}"/tvdsp-*.zip 2>/dev/null | head -1)
    neueste="${neueste##*/}"
    [ -n "$neueste" ] || return 0
    ordner=$(unzip -Z1 "${ZIEL}/${neueste}" 2>/dev/null | head -1 | cut -d/ -f1)
    cat > "${ZIEL}/WIEDERHERSTELLEN.txt" <<ANLEITUNG
TVDSP Backup
============

Jede Datei tvdsp-x_y_z.zip enthaelt ALLES zu dieser Version:

  ${ordner}/                  komplettes Projekt, direkt installierbar
  ${ordner}/.git/             volle Git-Historie (alle Versionen und Tags)
  ${ordner}/geraetestand/     Einstellungen vom Pi und die FLIRC-Tasten

Neuestes Backup: ${neueste}

1. Auspacken - mit jedem Programm, auf dem Pi z. B.:

       unzip ${neueste}
       cd ${ordner}

2. Installieren (Pi, Raspberry Pi OS 64 Bit, HiFiBerry DAC+ DSP):

       sudo ./install.sh

3. Eigene Einstellungen zurueckholen (EQ, Band, R128, IR-Zuweisung,
   Kalibrierung, Pegel):

       sudo systemctl stop tvdsp-web
       cp geraetestand/settings.json geraetestand/ir-keys.json /opt/tvdsp/config/
       sudo systemctl start tvdsp-web

4. FLIRC-Stick neu bespielen (nur falls er geleert oder ersetzt wurde):

       sudo systemctl stop tvdsp-flirc
       sudo flirc_util loadconfig geraetestand/flirc
       sudo systemctl start tvdsp-flirc

   (flirc_util haengt ".fcfg" selbst an - Dateiname ohne Endung angeben.)

Aeltere Version ansehen oder zurueckholen (im ausgepackten Ordner):

       git log --oneline
       git checkout v2.8.1

Aeltere Backups:
  tvdsp-*.bundle   bis 2.8.1: Git-Bundle, nur mit git lesbar:
                   git clone tvdsp-2.8.1.bundle tvdsp
  tvdsp-2_8_1.zip  nur der Stand von 2.8.1, ohne Historie

Fremdsoftware in exakt den getesteten Versionen liegt unter
${ordner}/abhaengigkeiten/pakete/ (Python-Pakete, CamillaDSP 4.1.3, FLIRC);
install.sh nimmt sie automatisch, ohne Internet. Sie passen zu dem System,
das in abhaengigkeiten/pakete/PLATTFORM.txt steht.

Erzeugt von werkzeuge/backup.sh am $(date '+%d.%m.%Y %H:%M') auf $(hostname).
ANLEITUNG
}

# --- 1. Repository pruefen -------------------------------------------------
cd "$REPO" 2>/dev/null || abbruch "Projektverzeichnis $REPO nicht gefunden."
git rev-parse --git-dir >/dev/null 2>&1 || abbruch "$REPO ist kein Git-Repository."

VERSION=$(head -1 VERSION | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
[ -n "$VERSION" ] || abbruch "Keine Version in $REPO/VERSION gefunden (erste Zeile erwartet: x.y.z)."
SHA=$(git rev-parse --short HEAD)
SHA_LANG=$(git rev-parse HEAD)
TAGS=$(git tag | wc -l)

echo "Projekt : $REPO"
echo "Version : $VERSION"
echo "Commit  : $SHA  $(git log -1 --pretty=%s)"
echo

# Nicht committete Aenderungen: die Zip wuerde sie als Arbeitsstand enthalten,
# die Historie aber nicht - das muss man wissen.
if [ -n "$(git status --porcelain)" ]; then
    gelb "WARNUNG: Es gibt nicht committete Aenderungen:"
    git status --short | sed 's/^/    /'
    echo
    read -r -p "Trotzdem fortfahren? [j/N] " ANTWORT
    case "$ANTWORT" in
        j|J|y|Y) echo ;;
        *) abbruch "Auf Wunsch abgebrochen. Erst committen, dann erneut aufrufen." ;;
    esac
fi

# Fremdpakete (ab 2.8.3) gehoeren in jede Zip - ohne sie ist das Backup nicht
# vollstaendig. Sie liegen nicht in git; werkzeuge/abhaengigkeiten.sh baut sie.
[ -f "$REPO/abhaengigkeiten/pakete/SHA256SUMS" ] || abbruch "Offline-Pakete fehlen (abhaengigkeiten/pakete).
Einmal bauen: werkzeuge/abhaengigkeiten.sh"
( cd "$REPO/abhaengigkeiten/pakete" && sha256sum -c --quiet SHA256SUMS ) >/dev/null 2>&1 \
    || abbruch "Offline-Pakete beschaedigt - neu bauen: werkzeuge/abhaengigkeiten.sh"
# Passen die Pakete noch zu den eingetragenen Versionen? (docs/wartung.md, 4.)
GB="$REPO/abhaengigkeiten/pakete/gebaut-aus"
for f in requirements.txt requirements-vad.txt; do
    cmp -s "$REPO/abhaengigkeiten/$f" "$GB/$f" || abbruch "Offline-Pakete passen nicht mehr zu abhaengigkeiten/$f.
Erst neu bauen: werkzeuge/abhaengigkeiten.sh"
done
SOLL_V="CDSP_VERSION=$(grep -oE '^CDSP_VERSION=v[0-9.]+' "$REPO/install.sh" | cut -d= -f2)
FLIRC_VERSION=$(grep -oE '^FLIRC_VERSION="[^"]+"' "$REPO/bin/install-flirc-cli.sh" | cut -d'"' -f2)"
[ "$SOLL_V" = "$(cat "$GB/versionen.txt" 2>/dev/null)" ] || abbruch "Offline-Pakete passen nicht zu CDSP_VERSION/FLIRC_VERSION.
Erst neu bauen: werkzeuge/abhaengigkeiten.sh"

# --- 2. Verbindung pruefen -------------------------------------------------
if [ -n "${BACKUP_ZIEL:-}" ]; then
    gelb "Testlauf: Ziel ist $ZIEL statt des NAS."
    mkdir -p "$ZIEL"
elif [ ! -d "$ZIEL" ]; then
    gelb "Freigabe nicht eingebunden - versuche sie zu oeffnen ..."
    gio mount "smb://${SERVER}/${FREIGABE}" >/dev/null 2>&1 || true
    sleep 2
fi
[ -n "${BACKUP_ZIEL:-}" ] || [ -d "$MOUNT" ] || abbruch "Freigabe //${SERVER}/${FREIGABE} ist nicht eingebunden.
Dauerhaft einbinden: per fstab unter /mnt/nas.
Sonst im Dateimanager einmal oeffnen, dann erneut versuchen."
mkdir -p "$ZIEL" 2>/dev/null
[ -d "$ZIEL" ] || abbruch "Ordner $UNTERORDNER auf der Freigabe nicht erreichbar."

PROBE="${ZIEL}/.schreibtest.$$"
echo test > "$PROBE" 2>/dev/null || abbruch "Keine Schreibrechte in $ZIEL."
rm -f "$PROBE"

# --- 3. Paket zusammenstellen ----------------------------------------------
TMP=$(mktemp -d) || abbruch "Kein temporaeres Verzeichnis."
trap 'rm -rf "$TMP"' EXIT

ORDNER="tvdsp-${VERSION}"
STAGE="${TMP}/${ORDNER}"
echo "Stelle Projekt mit kompletter Historie zusammen ..."
cp -a "$REPO" "$STAGE" || abbruch "Projekt konnte nicht kopiert werden."
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
find "$STAGE" -name '*.pyc' -delete 2>/dev/null

# Geraetestand: Einstellungen und FLIRC-Tasten leben NICHT im Repository.
# Sie liegen im Projektordner der Zip, sind fuer git aber ausgeblendet (nur in
# dieser Kopie, .git/info/exclude) - so bleibt "git status" im ausgepackten
# Backup sauber und niemand committet versehentlich Geraetedaten.
mkdir -p "${STAGE}/geraetestand"
echo "geraetestand/" >> "${STAGE}/.git/info/exclude"
GERAET_INFO=""
if [ -d "$GERAET" ]; then
    for f in settings.json ir-keys.json; do
        if [ -f "${GERAET}/${f}" ]; then
            cp "${GERAET}/${f}" "${STAGE}/geraetestand/" && GERAET_INFO+="$f "
        fi
    done
else
    gelb "WARNUNG: $GERAET nicht vorhanden - Einstellungen des Geraets fehlen in der Zip."
fi
# FLIRC: nur ein flirc_util-Prozess zugleich (docs/FEHLERGEDAECHTNIS.md) - laeuft gerade einer,
# kurz warten. saveconfig liest nur, am Stick wird nichts veraendert.
FLIRC_OK=0
if command -v flirc_util >/dev/null 2>&1; then
    for _ in 1 2 3 4 5; do pgrep -x flirc_util >/dev/null || break; sleep 2; done
    if sudo -n timeout 20 flirc_util saveconfig "${STAGE}/geraetestand/flirc" >/dev/null 2>&1 \
       && [ -s "${STAGE}/geraetestand/flirc.fcfg" ]; then
        sudo -n chown "$(id -u):$(id -g)" "${STAGE}/geraetestand/flirc.fcfg" 2>/dev/null
        FLIRC_OK=1
        GERAET_INFO+="flirc.fcfg "
    else
        gelb "WARNUNG: FLIRC-Stick nicht exportiert (nicht gesteckt oder belegt)."
    fi
fi
cat > "${STAGE}/geraetestand/LIESMICH.txt" <<INFO
Geraetestand von $(hostname) am $(date '+%d.%m.%Y %H:%M') (TVDSP ${VERSION})

settings.json   alle Einstellungen: EQ, Band, R128, Regelung, IR-Zuweisung,
                Kalibrierung, Pegel  -> /opt/tvdsp/config/
ir-keys.json    IR-Zuweisung fuer den Lauscher -> /opt/tvdsp/config/
flirc.fcfg      angelernte Tasten im FLIRC-Stick:
                sudo flirc_util loadconfig geraetestand/flirc

Anleitung: WIEDERHERSTELLEN.txt auf dem NAS.
INFO

STAMM="tvdsp-${VERSION//./_}${ZUSATZ:+_${ZUSATZ}}"
ZIPNAME="${STAMM}.zip"
LOKAL="${TMP}/${ZIPNAME}"
echo "Packe ${ZIPNAME} ..."
( cd "$TMP" && zip -qr -X "$ZIPNAME" "$ORDNER" ) || abbruch "zip fehlgeschlagen."

# Name belegt: vorhandenes Backup NIE ueberschreiben, freien Namen suchen.
NAME="$ZIPNAME"
if [ -e "${ZIEL}/${NAME}" ]; then
    NAME="${STAMM}-${SHA}.zip"
    [ -e "${ZIEL}/${NAME}" ] && NAME="${STAMM}-${SHA}-$(date +%Y%m%d-%H%M%S).zip"
    gelb "Fuer Version $VERSION liegt bereits eine Zip auf dem NAS - sie bleibt unangetastet."
    gelb "Das neue Backup heisst ${NAME}."
fi

# --- 4. Uebertragen und verifizieren ---------------------------------------
echo "Uebertrage nach ${UNTERORDNER}/${NAME} ..."
cp "$LOKAL" "${ZIEL}/${NAME}" || abbruch "Kopieren auf das NAS fehlgeschlagen."

MD5_LOKAL=$(md5sum "$LOKAL" | cut -d' ' -f1)
MD5_NAS=$(md5sum "${ZIEL}/${NAME}" | cut -d' ' -f1)
[ "$MD5_LOKAL" = "$MD5_NAS" ] || abbruch "Pruefsumme stimmt nicht ueberein - Uebertragung fehlerhaft!
lokal: $MD5_LOKAL
NAS  : $MD5_NAS"

# Echte Pruefung: die Zip VOM NAS auspacken und inhaltlich kontrollieren.
echo "Pruefe die Zip vom NAS aus ..."
unzip -tq "${ZIEL}/${NAME}" >/dev/null 2>&1 || abbruch "Zip auf dem NAS ist beschaedigt!"
PRUEF="${TMP}/pruef"
mkdir -p "$PRUEF" && unzip -q "${ZIEL}/${NAME}" -d "$PRUEF" || abbruch "Zip vom NAS laesst sich nicht auspacken!"
R="${PRUEF}/${ORDNER}"
git -C "$R" fsck --full --no-progress >/dev/null 2>&1 || abbruch "git fsck in der Zip fehlgeschlagen - Historie beschaedigt!"
[ "$(git -C "$R" rev-parse HEAD)" = "$SHA_LANG" ] || abbruch "Zip enthaelt nicht den aktuellen Commit $SHA!"
[ "$(git -C "$R" tag | wc -l)" = "$TAGS" ] || abbruch "Zip enthaelt nicht alle $TAGS Tags!"
[ -z "$(git -C "$R" status --porcelain)" ] || abbruch "Arbeitsstand in der Zip weicht vom Commit ab:
$(git -C "$R" status --short)"
for rel in $PFLICHT; do
    [ -s "${R}/${rel}" ] || abbruch "Pflichtdatei fehlt in der Zip: $rel"
done
[ -x "${R}/install.sh" ] || abbruch "install.sh ist in der Zip nicht ausfuehrbar!"
( cd "${R}/abhaengigkeiten/pakete" && sha256sum -c --quiet SHA256SUMS ) >/dev/null 2>&1 \
    || abbruch "Offline-Pakete in der Zip sind beschaedigt!"
if [ -f "${R}/geraetestand/settings.json" ]; then
    python3 -c "import json,sys; json.load(open(sys.argv[1]))" "${R}/geraetestand/settings.json" \
        || abbruch "geraetestand/settings.json in der Zip ist kein gueltiges JSON!"
fi

# --- 5. Anleitung mitschreiben ---------------------------------------------
anleitung_schreiben

# --- 6. Ergebnis -----------------------------------------------------------
echo
gruen "Backup erfolgreich und geprueft."
echo "  Datei    : ${ZIEL}/${NAME}"
echo "  Groesse  : $(du -h "${ZIEL}/${NAME}" | cut -f1)"
echo "  Pruefsum.: $MD5_NAS (lokal und NAS identisch)"
echo "  Historie : git fsck ok, Commit $SHA, $TAGS Tags"
echo "  Pflicht  : alle Dateien fuer install.sh vorhanden, install.sh ausfuehrbar"
echo "  Pakete   : Python/CamillaDSP/FLIRC offline, Pruefsummen ok ($(du -sh "$REPO/abhaengigkeiten/pakete" | cut -f1))"
echo "  Geraet   : ${GERAET_INFO:-nichts}"
echo
echo "Backups auf dem NAS (neueste zuerst):"
ls -1t "${ZIEL}"/tvdsp-* 2>/dev/null | head -6 | sed 's|.*/|    |'
