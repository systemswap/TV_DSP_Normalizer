#!/usr/bin/env bash
# TVDSP - Version hochzaehlen, committen, taggen und sichern (werkzeuge/backup.sh).
#
#   werkzeuge/release.sh          -> zaehlt die letzte Stelle hoch (z)
#   werkzeuge/release.sh 1.10.0   -> setzt genau diese Version
#   werkzeuge/release.sh -y       -> ohne Rueckfrage
#   (oder ueber den Kurzweg ~/tvdsp-release.sh)
#
# Projektregel: Bei JEDER Aenderung wird die Version hochgezaehlt,
# auch bei reiner Doku. Die Version steht an SECHS Stellen (die README ist die Startseite und traegt keine).
#
# WICHTIG: Es wird NIEMALS global ersetzt. "1.9.28" kommt auch in alten
# Changelog-Eintraegen und Code-Kommentaren vor ("Fehlte 1.9.25-1.9.28").
# Ein pauschales sed wuerde die Historie faelschen. Dieses Skript aendert
# ausschliesslich die sechs bekannten Ankerzeilen und prueft das nach.

set -uo pipefail

# Projektordner und Backup-Skript aus dem eigenen Ort ableiten.
EIGENER_ORT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO="${REPO:-$(dirname "$EIGENER_ORT")}"
BACKUP="${BACKUP:-$EIGENER_ORT/backup.sh}"

rot()   { printf '\033[31m%s\033[0m\n' "$*"; }
gruen() { printf '\033[32m%s\033[0m\n' "$*"; }
gelb()  { printf '\033[33m%s\033[0m\n' "$*"; }
abbruch() { rot "ABBRUCH: $*"; exit 1; }

OHNE_RUECKFRAGE=0
NEU=""
for ARG in "$@"; do
    case "$ARG" in
        -y|--ja) OHNE_RUECKFRAGE=1 ;;
        -h|--hilfe|--help) sed -n '2,10p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) NEU="$ARG" ;;
    esac
done

cd "$REPO" 2>/dev/null || abbruch "Projektverzeichnis $REPO nicht gefunden."
git rev-parse --git-dir >/dev/null 2>&1 || abbruch "$REPO ist kein Git-Repository."

# --- 1. Alte und neue Version bestimmen ------------------------------------
ALT=$(head -1 VERSION | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
[ -n "$ALT" ] || abbruch "Keine Version in der ersten Zeile von VERSION gefunden."

if [ -z "$NEU" ]; then
    NEU=$(echo "$ALT" | awk -F. '{printf "%d.%d.%d", $1, $2, $3+1}')
fi
echo "$NEU" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' || abbruch "Ungueltige Version '$NEU' (erwartet x.y.z)."
[ "$NEU" != "$ALT" ] || abbruch "Neue Version ist gleich der alten ($ALT)."
git rev-parse -q --verify "refs/tags/v${NEU}" >/dev/null && abbruch "Tag v${NEU} existiert bereits."

echo "Version: $ALT  ->  $NEU"
echo

# --- 2. Changelog-Eintrag muss VORHER geschrieben sein ---------------------
# Das ist Prosa und beschreibt, WAS sich geaendert hat - das kann kein Skript
# erfinden, und die Projektregel verlangt den Eintrag ausdruecklich.
if ! grep -q "^## ${NEU}\b" VERSION; then
    rot "In VERSION fehlt der Changelog-Eintrag fuer $NEU."
    echo
    echo "Erwartet wird eine Ueberschrift in dieser Form (ab Zeile 3):"
    echo
    echo "    ## ${NEU} — Kurze ehrliche Beschreibung der Aenderung"
    echo
    echo "    Was geaendert wurde und warum."
    echo
    abbruch "Erst den Changelog-Eintrag schreiben, dann erneut aufrufen."
fi
TITEL=$(grep -m1 "^## ${NEU}\b" VERSION | sed "s/^## ${NEU}[[:space:]]*[—-]*[[:space:]]*//")


# --- 3. Die sechs Anker setzen --------------------------------------------
setze() {  # setze <datei> <sed-ausdruck> <beschreibung>
    local datei="$1" ausdruck="$2" was="$3"
    sed -i "$ausdruck" "$datei" || abbruch "sed fehlgeschlagen: $was"
}

setze VERSION                   "1s|^.*$|${NEU}|"                                              "VERSION Kopfzeile"
setze install.sh                "s|^# install\.sh - TV-Dynamikprozessor .* einrichten$|# install.sh - TV-Dynamikprozessor ${NEU} einrichten|" "install.sh Kommentarkopf"
setze install.sh                "s|^VERSION=.*|VERSION=${NEU}|"                                 "install.sh VERSION="
setze backend/app.py            "s|^VERSION = \".*\"|VERSION = \"${NEU}\"|"                     "app.py VERSION"
setze backend/static/index.html "s|<meta name=\"version\" content=\"[^\"]*\">|<meta name=\"version\" content=\"${NEU}\">|" "index.html meta"
setze backend/static/index.html "s|<div class=\"build\">Version [^<]*</div>|<div class=\"build\">Version ${NEU}</div>|"    "index.html sichtbarer Build"

# --- 4. Nachpruefen: sitzen alle sechs Anker? -----------------------------
pruefe() {  # pruefe <beschreibung> <erwarteter text> <datei>
    if grep -qF "$2" "$3"; then
        printf "  OK   %-28s %s\n" "$1" "$2"
    else
        rot "  FEHLT $1 in $3 - erwartet: $2"
        FEHLER=1
    fi
}
FEHLER=0
echo "Gesetzte Versionsanker:"
# Kopfzeile gezielt pruefen - ein blosses grep wuerde auch die neue
# Changelog-Ueberschrift "## x.y.z — ..." als Treffer werten.
if [ "$(head -1 VERSION)" = "$NEU" ]; then
    printf "  OK   %-28s %s\n" "VERSION Kopfzeile" "$NEU"
else
    rot "  FEHLT VERSION Kopfzeile - Zeile 1 lautet: $(head -1 VERSION)"
    FEHLER=1
fi
pruefe "install.sh Kopf"       "# install.sh - TV-Dynamikprozessor ${NEU}"   install.sh
pruefe "install.sh VERSION="   "VERSION=${NEU}"                              install.sh
pruefe "app.py VERSION"        "VERSION = \"${NEU}\""                        backend/app.py
pruefe "index.html meta"       "content=\"${NEU}\""                          backend/static/index.html
pruefe "index.html Build"      ">Version ${NEU}<"                            backend/static/index.html
[ "$FEHLER" = 0 ] || abbruch "Nicht alle Versionsanker konnten gesetzt werden - nichts committet.
Die Dateien sind veraendert; mit 'git checkout -- .' zuruecksetzen."

# --- 5. Nachpruefen: ist irgendwo ein Versionsanker uebersehen worden? -----
# Gesucht wird die ALTE Nummer nur noch in Zeilen, die eine Version DEKLARIEREN
# (Zuweisung, meta-Tag, Build-Anzeige, Titelzeile). Fliesstext und Changelog
# duerfen die alte Nummer selbstverstaendlich weiter nennen - genau dafuer ist
# sie da ("Fehlte 1.9.25-1.9.28"). Ein frueherer, pauschaler Diff-Check schlug
# hier faelschlich an und hat den Commit blockiert.
ALT_ESC=$(printf '%s' "$ALT" | sed 's/\./\\./g')
UEBERSEHEN=$(git grep -nE "(^VERSION *=|^# install\.sh - TV-Dynamikprozessor |^# TV-Dynamikprozessor |content=\"|>Version )${ALT_ESC}" -- \
    VERSION README.md install.sh backend/app.py backend/static/index.html)
if [ -n "$UEBERSEHEN" ]; then
    rot "Es steht noch die alte Version $ALT in einer Deklarationszeile:"
    echo "$UEBERSEHEN" | sed 's/^/    /'
    abbruch "Nichts committet. Bitte pruefen."
fi

echo
echo "Geaenderte Dateien:"
git diff --stat | sed 's/^/  /'
echo
echo "Geaenderte Zeilen:"
git diff -U0 | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' | sed 's/^/  /'
echo

# --- 6. Bestaetigung -------------------------------------------------------
if [ "$OHNE_RUECKFRAGE" = 0 ]; then
    read -r -p "Committen als \"${NEU}\", Tag v${NEU} setzen und sichern? [j/N] " A
    case "$A" in
        j|J|y|Y) echo ;;
        *) gelb "Abgebrochen. Die Versionsaenderungen stehen noch im Arbeitsverzeichnis."
           gelb "Zuruecknehmen mit: git checkout -- ."
           exit 1 ;;
    esac
fi

# --- 7. Committen und taggen -----------------------------------------------
BETREFF="$NEU"
[ -n "$TITEL" ] && BETREFF="$NEU — $TITEL"

NACHRICHT="$BETREFF"

git add -A || abbruch "git add fehlgeschlagen."
git commit -qm "$NACHRICHT" || abbruch "git commit fehlgeschlagen."
git tag -a "v${NEU}" -m "$BETREFF" || abbruch "git tag fehlgeschlagen."
gruen "Committet: $(git log --oneline -1)"
gruen "Tag gesetzt: v${NEU}"
echo

# --- 8. Backup ------------------------------------------------------------
[ -x "$BACKUP" ] || abbruch "Backup-Skript $BACKUP nicht gefunden oder nicht ausfuehrbar.
Commit und Tag sind gesetzt - Backup bitte nachholen."
"$BACKUP"
