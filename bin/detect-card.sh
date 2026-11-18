#!/usr/bin/env bash
#
# Findet die HiFiBerry-Karte und traegt sie dort ein, wo sie gebraucht
# wird: als Playback-Geraet in tv.yml und als dsnoop-Slave in
# /etc/asound.conf. Der Capture-Eintrag bleibt "dsnoop_tv" - darueber
# liest ffmpeg parallel mit.
#
# Laeuft als ExecStartPre von camilladsp, damit eine abweichende
# Kartenbenennung nicht zum stillen Fehlstart fuehrt.
#
# ACHTUNG - hier lag ein Fehler: /proc/asound/cards sieht so aus
#
#    0 [sndrpihifiberry]: HifiberryDacp - snd_rpi_hifiberry_dacplus
#
# Das zweite Feld ist "[sndrpihifiberry]:" - mit eckigen Klammern und
# Doppelpunkt. Ungefiltert uebernommen landete in tv.yml
# "hw:[sndrpihifiberry]:,0", und ALSA meldete "Cannot get card index".
# Der Name muss also immer bereinigt werden, nicht nur im Ersatzzweig.

set -euo pipefail
CFG=${1:-/opt/tvdsp/config/tv.yml}

[[ -f "$CFG" ]] || { echo "detect-card: $CFG nicht gefunden" >&2; exit 1; }

# Namen sauber herausloesen: alles zwischen den eckigen Klammern, EIN Name
# je Zeile. Nur Leerzeichen/Tabs entfernen - bis 2.9.6 stand hier
# `tr -d '[:space:]'`, das auch die Zeilenumbrueche loeschte: mit einer
# zweiten Karte (USB-Empfaenger UR27) wurde daraus "sndrpihifiberryAudio",
# die Karte gab es nicht, CamillaDSP startete nie.
namen_aus() {
    sed -n 's/^[[:space:]]*[0-9]\+[[:space:]]*\[\([^]]*\)\].*/\1/p' \
        /proc/asound/cards 2>/dev/null | tr -d ' \t'
}

CARD=""
while read -r n; do
    [[ -z "$n" ]] && continue
    if [[ "${n,,}" == *hifiberry* ]]; then CARD="$n"; break; fi
done <<< "$(namen_aus)"

# Keine HiFiBerry gefunden? Dann die erste Karte ueberhaupt nehmen.
if [[ -z "$CARD" ]]; then
    CARD=$(namen_aus | head -n1)
fi

[[ -n "$CARD" ]] || { echo "detect-card: keine Soundkarte gefunden" >&2; exit 1; }

# Gegenprobe: der Name muss ein brauchbarer ALSA-Bezeichner sein.
# Klammern oder Doppelpunkte haben hier nichts zu suchen.
if [[ ! "$CARD" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "detect-card: unbrauchbarer Kartenname '$CARD'" >&2
    exit 1
fi

# Und die Karte muss auch wirklich existieren.
if [[ ! -d "/proc/asound/$CARD" ]]; then
    echo "detect-card: /proc/asound/$CARD gibt es nicht" >&2
    exit 1
fi

# Nur echte hw:-Eintraege anfassen. "dsnoop_tv" bleibt unberuehrt.
sed -i "s|device: \"hw:[^\"]*\"|device: \"hw:${CARD},0\"|g" "$CFG"

if [[ -f /etc/asound.conf ]]; then
    # Den Loopback-Eintrag des USB-Eingangs (dsnoop_usb, ab 3.0.0) nicht anfassen.
    sed -i "/Loopback/! s|pcm \"hw:[^\"]*\"|pcm \"hw:${CARD},0\"|g" /etc/asound.conf
fi

echo "detect-card: Karte ${CARD} (Capture ueber dsnoop_tv, Playback hw:${CARD},0)"
