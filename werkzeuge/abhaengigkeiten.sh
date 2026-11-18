#!/usr/bin/env bash
# TVDSP - Fremdpakete fuer die Offline-Installation zusammenstellen.
#
# Legt unter abhaengigkeiten/pakete/ (in git und in jeder Backup-Zip; flirc/
# nur lokal, Lizenz unklar) genau die Versionen ab, mit denen die Anlage laeuft:
#
#   python/*.whl                  alle Python-Pakete aus requirements*.txt
#                                 (+ pip und wheel), fuer diese Plattform gebaut
#   camilladsp/...tar.gz          CamillaDSP-Release (Version aus install.sh)
#   flirc/*.deb                   FLIRC-Programm + libhidapi-hidraw0
#   SHA256SUMS                    Pruefsummen aller Dateien ausser flirc/
#   PLATTFORM.txt                 fuer welches System die Pakete gebaut sind
#
# install.sh benutzt diese Dateien automatisch, wenn sie neben ihm liegen,
# und braucht dann fuer diese Teile kein Internet.
#
# Aufruf: werkzeuge/abhaengigkeiten.sh   (baut den Cache komplett neu)
set -euo pipefail
# Auch die Probe-Installation laedt onnxruntime: keine Nutzungsdaten an den Hersteller.
export ORT_DISABLE_TELEMETRY=1

EIGENER_ORT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO="$(dirname "$EIGENER_ORT")"
A="$REPO/abhaengigkeiten"
P="$A/pakete"
VENV_PY="${VENV_PY:-/opt/tvdsp/venv/bin/python3}"

gruen(){ printf '\033[32m%s\033[0m\n' "$*"; }
abbruch(){ printf '\033[31mABBRUCH: %s\033[0m\n' "$*"; exit 1; }

[ -f "$A/requirements.txt" ] || abbruch "$A/requirements.txt fehlt."
[ -x "$VENV_PY" ] || abbruch "Python der Anlage ($VENV_PY) nicht gefunden."

CDSP_VERSION=$(grep -oE '^CDSP_VERSION=v[0-9.]+' "$REPO/install.sh" | cut -d= -f2)
FLIRC_VERSION=$(grep -oE '^FLIRC_VERSION="[^"]+"' "$REPO/bin/install-flirc-cli.sh" | cut -d'"' -f2)
[ -n "$CDSP_VERSION" ] && [ -n "$FLIRC_VERSION" ] || abbruch "CDSP_VERSION/FLIRC_VERSION nicht gefunden."
case "$(uname -m)" in
    aarch64) CDSP_ARCH="linux-aarch64" ;;
    armv7l)  CDSP_ARCH="linux-armv7"   ;;
    x86_64)  CDSP_ARCH="linux-amd64"   ;;
    *) abbruch "Unbekannte Architektur $(uname -m)" ;;
esac

NEU="$A/pakete.neu"
rm -rf "$NEU"; mkdir -p "$NEU/python" "$NEU/camilladsp" "$NEU/flirc"

echo "== Python-Pakete (exakte Versionen) ..."
PIPV=$("$VENV_PY" -m pip --version | awk '{print $2}')
WHEELV=$("$VENV_PY" -m pip show wheel 2>/dev/null | awk '/^Version:/{print $2}')
"$VENV_PY" -m pip wheel --quiet --no-deps -w "$NEU/python" \
    -r "$A/requirements.txt" -r "$A/requirements-vad.txt" \
    "pip==$PIPV" ${WHEELV:+"wheel==$WHEELV"}
# Offline-Listen: pycamilladsp steht in requirements.txt als git-URL - die
# wuerde pip auch mit --no-index aus dem Netz holen. Offline zeigt die Zeile
# deshalb auf das Rad, das genau aus diesem Commit gebaut wurde.
CDSPV=$(ls "$NEU/python"/camilladsp-*.whl | head -1 | sed -E 's|.*/camilladsp-([^-]+)-.*|\1|')
[ -n "$CDSPV" ] || abbruch "Kein camilladsp-Rad gebaut."
sed -E "s|^camilladsp @ git\+.*|camilladsp==${CDSPV}|" "$A/requirements.txt" > "$NEU/requirements.txt"
cp "$A/requirements-vad.txt" "$NEU/requirements-vad.txt"
# Probe: laesst sich alles WIRKLICH ohne Internet in eine frische Umgebung
# installieren? Jeder Netzzugriff (Proxy ins Leere, git nur lokal) scheitert.
PROBE=$(mktemp -d); trap 'rm -rf "$PROBE"' EXIT
"$VENV_PY" -m venv "$PROBE/venv" >/dev/null
http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 GIT_ALLOW_PROTOCOL=file \
"$PROBE/venv/bin/pip" install --quiet --no-index --find-links "$NEU/python" \
    -r "$NEU/requirements.txt" -r "$NEU/requirements-vad.txt" \
    || abbruch "Offline-Installation der Python-Pakete schlaegt fehl."
"$PROBE/venv/bin/python3" -c "import flask, evdev, camilladsp, hifiberrydsp, onnxruntime, numpy" \
    || abbruch "Offline installierte Pakete lassen sich nicht importieren."

echo "== CamillaDSP $CDSP_VERSION ($CDSP_ARCH) ..."
CDSP_DATEI="camilladsp-${CDSP_ARCH}-${CDSP_VERSION}.tar.gz"
curl -fsSL "https://github.com/HEnquist/camilladsp/releases/download/${CDSP_VERSION}/camilladsp-${CDSP_ARCH}.tar.gz" \
    -o "$NEU/camilladsp/$CDSP_DATEI" || abbruch "CamillaDSP-Download fehlgeschlagen."
tar -xzf "$NEU/camilladsp/$CDSP_DATEI" -C "$PROBE" camilladsp
"$PROBE/camilladsp" --version | grep -q "${CDSP_VERSION#v}" || abbruch "CamillaDSP-Archiv hat die falsche Version."
if command -v camilladsp >/dev/null && ! cmp -s "$PROBE/camilladsp" "$(command -v camilladsp)"; then
    printf '\033[33mHinweis: das Archiv-Binary unterscheidet sich vom installierten.\033[0m\n'
fi

echo "== FLIRC ..."
( cd "$NEU/flirc" && apt-get download "flirc=$FLIRC_VERSION" libhidapi-hidraw0 >/dev/null 2>&1 ) \
    || printf '\033[33mWARNUNG: FLIRC-Pakete nicht geladen (Quelle apt.flirc.tv eingerichtet?).\033[0m\n'

. /etc/os-release
cat > "$NEU/PLATTFORM.txt" <<INFO
Gebaut am $(date '+%d.%m.%Y %H:%M')
System : ${PRETTY_NAME} ($(uname -m))
Python : $("$VENV_PY" --version 2>&1)
Die Python-Pakete mit C-Anteil (numpy, onnxruntime, lxml, pyalsaaudio,
RPi.GPIO, spidev, ...) passen nur zu genau dieser Python-Version und
Architektur. Auf einem neueren System faellt install.sh automatisch auf
die Installation aus dem Internet zurueck (gleiche Versionen).
INFO
# Woraus gebaut: backup.sh und install.sh vergleichen das mit den aktuellen
# Versionsangaben und verweigern veraltete Pakete (docs/wartung.md, Abschnitt 4).
mkdir -p "$NEU/gebaut-aus"
cp "$A/requirements.txt" "$A/requirements-vad.txt" "$NEU/gebaut-aus/"
printf 'CDSP_VERSION=%s\nFLIRC_VERSION=%s\n' "$CDSP_VERSION" "$FLIRC_VERSION" > "$NEU/gebaut-aus/versionen.txt"
# flirc/ bleibt ausserhalb der Pruefsummen: Es liegt nur lokal (Lizenz unklar, nicht im
# oeffentlichen Repository), ein Klon ohne flirc/ muss die Pruefung trotzdem bestehen.
( cd "$NEU" && find . -type f ! -name SHA256SUMS ! -path './flirc/*' -printf '%P\n' | sort | xargs sha256sum > SHA256SUMS )

rm -rf "$P"; mv "$NEU" "$P"
gruen "Fertig: $P  ($(du -sh "$P" | cut -f1))"
du -sh "$P"/* | sed 's|'"$P"'/|    |'
