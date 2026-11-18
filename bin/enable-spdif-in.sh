#!/usr/bin/env bash
#
# Routet den Toslink-Eingang des DAC+ DSP zum Raspberry Pi.
#
# Ab Werk leitet die Karte den optischen Eingang direkt auf den
# Analogausgang - am Pi vorbei. Diese Registerschreibvorgaenge stellen
# den Sample-Rate-Konverter ASRC6 so um, dass die Daten zusaetzlich als
# ALSA-Aufnahmegeraet ankommen.
#
# Die Register sind fluechtig: nach jedem Neustart und nach jedem
# Profilwechsel erneut noetig. Deshalb als Dienst.
#
# WICHTIG - die Vorgaengerfassung hat gelogen: Sie wartete 15 Versuche
# lang auf den DSP, schrieb dann trotzdem und meldete Erfolg. Ohne
# aufgesteckte Karte kam so "Toslink-Eingang freigeschaltet." heraus,
# obwohl sigmatcpserver im selben Protokoll "did not detect ADAU14xx
# DSP" meldete. Eine Erfolgsmeldung, die auch im Fehlerfall erscheint,
# ist schlimmer als keine - die Fehlersuche beginnt dann an der
# falschen Stelle.
#
# Diese Fassung prueft vorher, ob ueberhaupt ein DSP da ist, und liest
# nach dem Schreiben zurueck, ob die Werte angekommen sind.

set -uo pipefail    # kein -e: Fehler werden hier einzeln behandelt

DT=${DSPTOOLKIT:-/opt/tvdsp/venv/bin/dsptoolkit}
command -v "$DT" >/dev/null 2>&1 || DT=$(command -v dsptoolkit 2>/dev/null || true)

if [[ -z "${DT:-}" || ! -x "$DT" ]]; then
    echo "dsptoolkit nicht gefunden - Toslink-Eingang bleibt inaktiv." >&2
    exit 1
fi

# --- Auf den DSP warten -----------------------------------------------
# Der sigmatcpserver braucht nach dem Start einen Moment, bis er den
# Chip erkannt hat. Ohne Karte laeuft die Schleife durch und wir brechen
# ab, statt ins Leere zu schreiben.
#
# WICHTIG - die Vorgaengerfassung (1.1.2) pruefte mit get-meta. Das
# liest Profil-METADATEN, die nur existieren, wenn je ein Profil ueber
# dsptoolkit installiert wurde. Bootet der DSP aus dem EEPROM (der
# Normalfall ab Werk), ist get-meta leer und das Skript hielt den Chip
# faelschlich fuer abwesend - es brach ab, BEVOR es schrieb. Am echten
# Geraet nachgewiesen: read-reg lieferte plausible Werte, get-meta
# nichts. Erreichbarkeit wird deshalb jetzt so geprueft, wie sie
# gebraucht wird: mit einem echten Registerzugriff.
GEFUNDEN=0
for i in $(seq 1 15); do
    probe=$("$DT" read-reg 0xF106 2>/dev/null | tr -d '[:space:]')
    if [[ "$probe" =~ ^[0-9a-fA-F]+$ ]]; then
        GEFUNDEN=1
        break
    fi
    sleep 2
done

if [[ $GEFUNDEN -eq 0 ]]; then
    echo "Kein DSP erreichbar (Karte aufgesteckt? sigmatcpserver laeuft?)." >&2
    echo "Toslink-Eingang bleibt inaktiv." >&2
    exit 1
fi

# --- Register setzen --------------------------------------------------
# Werte aus der HiFiBerry-Anleitung "Using the DAC+ DSP to record audio from
# S/PDIF" (profilunabhaengig, siehe THIRD-PARTY-NOTICES.md).
REGISTER=(
    "0xF106 0x0003 ASRC6-Quelle:SPDIF"
    "0xF146 0x0004 ASRC6-Ausgang:SDATA_OUT3"
    "0xF195 0x0000 SOUT-Quelle:oberes_Wort"
    "0xF194 0x0033 SOUT-Quelle:ASRC6"
    "0xF21C 0x6C40 SDATA_OUT3:Clock-Slave"
)

FEHLER=0
for zeile in "${REGISTER[@]}"; do
    read -r adr wert zweck <<< "$zeile"
    if ! "$DT" write-reg "$adr" "$wert" >/dev/null 2>&1; then
        echo "Schreiben von $adr fehlgeschlagen ($zweck)." >&2
        FEHLER=1
    fi
done

if [[ $FEHLER -eq 1 ]]; then
    echo "Toslink-Eingang nicht vollstaendig freigeschaltet." >&2
    exit 1
fi

# --- Zuruecklesen -----------------------------------------------------
# Erfolg wird nur gemeldet, wenn die Werte auch wirklich im Chip stehen.
# Manche dsptoolkit-Fassungen koennen nicht zuruecklesen; dann bleibt es
# bei einer vorsichtigen Formulierung statt einer Falschmeldung.
if ! "$DT" read-reg 0xF106 >/dev/null 2>&1; then
    echo "Register geschrieben. Zuruecklesen wird von dieser"
    echo "dsptoolkit-Fassung nicht unterstuetzt - bitte gegenpruefen mit:"
    echo "  arecord -D dsnoop_tv -f S32_LE -r 48000 -c 2 -d 5 /tmp/test.wav"
    exit 0
fi

ABWEICHUNG=0
for zeile in "${REGISTER[@]}"; do
    read -r adr wert zweck <<< "$zeile"
    # dsptoolkit gibt die Bytes hexadezimal mit Leerzeichen aus,
    # z. B. "00 3B". Leerzeichen entfernen und als Hex deuten - ohne
    # 0x-Praefix wuerde die Bash-Arithmetik "003B" nicht verstehen.
    ist=$("$DT" read-reg "$adr" 2>/dev/null | tr -d '[:space:]')
    if [[ "$ist" =~ ^(0[xX])?[0-9a-fA-F]+$ ]]; then
        ist="0x${ist#0[xX]}"
        if [[ $((ist)) -ne $((wert)) ]]; then
            echo "Register $adr steht auf $ist, erwartet $wert ($zweck)." >&2
            ABWEICHUNG=1
        fi
    fi
done

if [[ $ABWEICHUNG -eq 1 ]]; then
    echo "Toslink-Eingang nicht sicher freigeschaltet - bitte pruefen mit:" >&2
    echo "  arecord -D dsnoop_tv -f S32_LE -r 48000 -c 2 -d 5 /tmp/test.wav" >&2
    exit 1
fi

# --- Direktweg stummschalten (Doppelton beseitigen) -------------------
# PROJEKT-INVARIANTE / NIEMALS ENTFERNEN:
# MUTE_ADR=4547 muss per write-mem auf 0 gesetzt und mit read-int
# kontrolliert werden. Ohne diese Sequenz entsteht der am echten Geraet
# verifizierte Doppelton aus Toslink-Direktweg + verarbeitetem Pi-Weg.
# Das Profil dspdac-noautomute mischt ab Werk den Toslink-Direktweg
# (Toslink -> Analog, im DSP) auf den Analogausgang - ZUSAETZLICH zum
# verarbeiteten Pi-Weg. Ergebnis: Original und verarbeitetes Signal
# gleichzeitig (Doppelton).
#
# Register 4547 (muteSPDIF, Parameter-RAM, 32 Bit -> write-mem, NICHT
# write-reg) schaltet den Direktweg. Am echten Geraet verifiziert:
#   Wert 1 = Direktweg OFFEN (Default, Doppelton)
#   Wert 0 = Direktweg STUMM (nur verarbeiteter Ton)
# Die Konvention ist also invertiert zum Namen ("mute"=1 waere intuitiv,
# ist hier aber falsch). Fluechtig - nach Kaltstart/Profilwechsel steht
# wieder 1, deshalb bei jedem Start neu setzen.
MUTE_ADR=4547
if "$DT" write-mem "$MUTE_ADR" 0 >/dev/null 2>&1; then
    ist=$("$DT" read-int "$MUTE_ADR" 2>/dev/null | tr -d '[:space:]')
    if [[ "$ist" == "0" ]]; then
        echo "Toslink-Eingang freigeschaltet, Direktweg stumm - Kette komplett."
    else
        echo "Toslink-Eingang freigeschaltet und zurueckgelesen."
        echo "Hinweis: Direktweg-Mute (4547) steht auf '${ist:-?}' statt 0 -" >&2
        echo "der Doppelton koennte hoerbar sein." >&2
    fi
else
    # Kein harter Fehler: Eingang und Ausgang funktionieren auch ohne
    # den Mute, nur mit Doppelton. Deshalb nur warnen, nicht abbrechen.
    echo "Toslink-Eingang freigeschaltet und zurueckgelesen."
    echo "Hinweis: Direktweg-Mute (write-mem 4547 0) fehlgeschlagen -" >&2
    echo "der Doppelton koennte hoerbar sein." >&2
fi

# Hinweis: Der Kaltstart-Wiedergabe-Anstoss (get-checksum) passiert
# NICHT hier, sondern in der eigenen Unit tvdsp-kick - die laeuft NACH
# CamillaDSP. Grund: Der Anstoss gibt die Wiedergabe-Ressource frei und
# wirkt nur, wenn CamillaDSP bereits spielt. enable-spdif-in.sh laeuft
# aber VOR CamillaDSP (es richtet erst den Eingang ein). Siehe
# tvdsp-kick.service und docs/hifiberry-dsp.md, Abschnitt "Kaltstart-Stille".
