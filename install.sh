#!/usr/bin/env bash
#
# install.sh - TV-Dynamikprozessor 3.3.10 einrichten
#
#   Hardware:  Raspberry Pi 4 + HiFiBerry DAC+ DSP
#   System:    Raspberry Pi OS 64 Bit (mit Desktop)
#
# Alle Dateien auf den Pi kopieren, dann:
#
#     chmod +x install.sh
#     sudo ./install.sh
#
# Wiederholbar - ein zweiter Durchlauf aktualisiert nur, was sich
# geaendert hat, und laesst deine Presets in Ruhe.

set -euo pipefail

VERSION=3.3.10
CDSP_VERSION=v4.1.3     # geprueft, siehe README
HBDSP_VERSION=0.21      # auf PyPI sind alle Versionen zurueckgezogen,
                        # deshalb muss die Version fest angegeben werden
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/opt/tvdsp
VENV=$PREFIX/venv
HTTP_PORT=8080
CDSP_PORT=1234

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mAbbruch: %s\033[0m\n\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- Vorpruefung

[[ $EUID -eq 0 ]] || die "Bitte mit sudo starten."
# Laufzeitbenutzer: normalerweise der sudo-Aufrufer. Bei der Web-Neuinstallation
# (systemd-run, kein SUDO_USER) gibt das Backend ihn per TVDSP_RUN_USER mit;
# Fallback ist der Besitzer von /opt/tvdsp. 2.1.0-2.1.1 fielen hier auf root
# zurueck - die Dienste liefen danach als root, Aufnahmen landeten in /root.
RUN_USER="${TVDSP_RUN_USER:-${SUDO_USER:-}}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]] && [[ -d /opt/tvdsp ]]; then
    BESITZER=$(stat -c %U /opt/tvdsp 2>/dev/null || true)
    [[ -n "$BESITZER" && "$BESITZER" != "root" ]] && RUN_USER="$BESITZER"
fi
RUN_USER="${RUN_USER:-root}"

# Die Dateien duerfen in der Ordnerstruktur liegen ODER flach neben dem
# Skript - beim Einzeldownload aus dem Browser entfaellt die Struktur.
finde() {
    local rel="$1" basis
    basis="$(basename "$rel")"
    if   [[ -f "$SRC/$rel"   ]]; then printf '%s' "$SRC/$rel"
    elif [[ -f "$SRC/$basis" ]]; then printf '%s' "$SRC/$basis"
    else return 1; fi
}

declare -A QUELLE
for rel in config/tv.yml backend/app.py backend/static/index.html backend/flirc_listener.py \
           bin/detect-card.sh bin/enable-spdif-in.sh bin/install-flirc-cli.sh \
           bin/tvdsp-quelle.py dsp/dspdac-noautomute.xml; do
    if pfad=$(finde "$rel"); then
        QUELLE["$rel"]="$pfad"
    else
        die "$(basename "$rel") nicht gefunden - weder unter $rel noch direkt neben dem Skript."
    fi
done

# Fremdpakete in EXAKT den Versionen, mit denen die Anlage getestet ist (2.8.3).
# Liegt der Offline-Cache daneben (abhaengigkeiten/pakete, in jeder Backup-Zip),
# kommen Python-Pakete, CamillaDSP und FLIRC von dort - ohne Internet.
REQ=$(finde abhaengigkeiten/requirements.txt) \
    || die "abhaengigkeiten/requirements.txt nicht gefunden (feste Paketversionen)."
REQ_VAD=$(finde abhaengigkeiten/requirements-vad.txt) \
    || die "abhaengigkeiten/requirements-vad.txt nicht gefunden."
PAKETE=""
[[ -d "$SRC/abhaengigkeiten/pakete" ]] && PAKETE="$SRC/abhaengigkeiten/pakete"
if [[ -n "$PAKETE" ]]; then
    ( cd "$PAKETE" && sha256sum -c --quiet SHA256SUMS ) >/dev/null 2>&1 \
        || die "Offline-Pakete in $PAKETE sind beschaedigt (SHA256SUMS stimmt nicht)."
    # Veraltete Pakete (Version geaendert, aber nicht neu gebaut) nicht benutzen,
    # sondern die eingetragenen Versionen aus dem Internet holen.
    if ! cmp -s "$REQ" "$PAKETE/gebaut-aus/requirements.txt" \
       || ! cmp -s "$REQ_VAD" "$PAKETE/gebaut-aus/requirements-vad.txt" \
       || ! grep -qx "CDSP_VERSION=$CDSP_VERSION" "$PAKETE/gebaut-aus/versionen.txt" 2>/dev/null; then
        warn "Offline-Pakete passen nicht zu den eingetragenen Versionen - sie werden"
        warn "ignoriert, alles kommt aus dem Internet (neu bauen: werkzeuge/abhaengigkeiten.sh)."
        PAKETE=""
    fi
fi

# ---------------------------------------------------------------- Schutz-Invariante 4547
# NIEMALS ENTFERNEN: Das DAC+ DSP-Profil mischt ohne diesen Parameter den
# unverarbeiteten Toslink-Direktweg zum verarbeiteten Pi-Weg (Doppelton).
# Ein zukuenftiges Paket, aus dem die Schutzlogik versehentlich entfernt
# wurde, darf deshalb gar nicht erst installiert werden. Parameter-RAM 4547
# ist 32 Bit: write-mem verwenden, und den geschriebenen Wert zuruecklesen.
SPDIF_SCRIPT="${QUELLE[bin/enable-spdif-in.sh]}"
if ! grep -q '^MUTE_ADR=4547$' "$SPDIF_SCRIPT" \
   || ! grep -Fq '"$DT" write-mem "$MUTE_ADR" 0' "$SPDIF_SCRIPT" \
   || ! grep -Fq '"$DT" read-int "$MUTE_ADR"' "$SPDIF_SCRIPT"; then
    die "SICHERHEITSABBRUCH: Der geschuetzte Doppelton-Schutz fuer DSP-Parameter 4547=0 fehlt oder ist unvollstaendig in enable-spdif-in.sh. Dieses Paket wird nicht installiert."
fi
info "Doppelton-Schutz geprueft: DSP-Parameter 4547 wird per write-mem auf 0 gesetzt und zurueckgelesen."

if   [[ -f /boot/firmware/config.txt ]]; then BOOTCFG=/boot/firmware/config.txt
elif [[ -f /boot/config.txt ]];          then BOOTCFG=/boot/config.txt
else die "config.txt nicht gefunden. Laeuft hier Raspberry Pi OS?"; fi

case "$(uname -m)" in
    aarch64) CDSP_ARCH="linux-aarch64" ;;
    armv7l)  CDSP_ARCH="linux-armv7"   ;;
    x86_64)  CDSP_ARCH="linux-amd64"   ;;
    *) die "Architektur $(uname -m) wird nicht unterstuetzt." ;;
esac

log "TV-Dynamikprozessor $VERSION einrichten"
info "Quelle:     $SRC"
info "Ablage:     $([[ -d "$SRC/config" ]] && echo "Ordnerstruktur" || echo "flach")"
info "Ziel:       $PREFIX"
info "Benutzer:   $RUN_USER"
info "CamillaDSP: $CDSP_VERSION"

# ------------------------------------------------------------------ Pakete

log "Systempakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev alsa-utils \
    ffmpeg curl git sudo build-essential libasound2-dev pkg-config >/dev/null
info "erledigt"

# --------------------------------------------------------------- FLIRC CLI

# WICHTIG: Die FLIRC-Unterstuetzung wird vollstaendig vorbereitet, aber die
# Hersteller-CLI wird NICHT mehr synchron im Hauptinstaller installiert.
# Das Hersteller-Repository kann bei Netzwerk/APT mehrere Minuten warten.
# Stattdessen richtet der Installer spaeter einen eigenen systemd-Setupdienst
# ein. Dadurch geht die TVDSP-Installation sofort weiter; ein USB-Stick ist
# weder hier noch fuer den Setupdienst erforderlich.
log "FLIRC-Unterstuetzung"
info "FLIRC-Stick wird fuer die Installation nicht benoetigt."
if command -v flirc_util >/dev/null 2>&1; then
    info "FLIRC CLI bereits vorhanden: $(command -v flirc_util)"
else
    info "FLIRC CLI wird nach der TVDSP-Installation im Hintergrund eingerichtet."
    info "Der Hauptinstaller wartet NICHT auf Netzwerk, FLIRC-Repository oder USB-Hardware."
fi

# ----------------------------------------------------------------- Overlay

log "Treiber eintragen"
cp -n "$BOOTCFG" "${BOOTCFG}.tvdsp-backup" 2>/dev/null || true

set_cfg(){
    if grep -qE "^\s*$1" "$BOOTCFG"; then
        sed -i "s|^\s*$1.*|$2|" "$BOOTCFG"; info "angepasst: $2"
    else
        printf '%s\n' "$2" >> "$BOOTCFG"; info "ergaenzt:  $2"
    fi
}
set_cfg "dtparam=audio="       "dtparam=audio=off"
set_cfg "dtoverlay=hifiberry"  "dtoverlay=hifiberry-dacplusdsp"
set_cfg "dtparam=spi="         "dtparam=spi=on"
sed -i 's|^dtoverlay=vc4-kms-v3d$|dtoverlay=vc4-kms-v3d,noaudio|' "$BOOTCFG" || true

# USB-Eingang (ab 3.0.0): Der Hifime UR27 bekommt den festen Kartennamen
# "UR27" (sonst hiesse er nur "Audio"), der Loopback nimmt das fertige Stereo
# von tvdsp-quelle auf. Beides aendert nichts, solange der Eingang
# "HiFiBerry" gewaehlt ist. Der UR27 hat keinen Hardware-Timer, deshalb KEIN
# timer_source - den Takt gleicht tvdsp-quelle selbst aus.
cat > /etc/modprobe.d/tvdsp-usb-eingang.conf <<'MODEOF'
# Von install.sh (TV-Dynamikprozessor) angelegt.
options snd-usb-audio vid=0x262a pid=0x9227 id=UR27
options snd-aloop id=Loopback pcm_substreams=1
MODEOF
echo snd-aloop > /etc/modules-load.d/tvdsp-usb-eingang.conf
modprobe snd-aloop id=Loopback pcm_substreams=1 2>/dev/null || true
info "USB-Eingang: Loopback und fester Name UR27 (greift fuer den UR27 nach Neustart/Einstecken)"

# -------------------------------------------------------------- CamillaDSP

log "CamillaDSP $CDSP_VERSION"

# Feste Version. Das Konfigurationsformat aendert sich zwischen den
# Hauptversionen - "immer das neueste" holen fuehrt frueher oder spaeter
# zu Exitcode 101 (ungueltige Konfiguration).
IST=""
command -v camilladsp >/dev/null 2>&1 && IST=$(camilladsp --version 2>/dev/null | awk '{print $2}')

if [[ "$IST" == "${CDSP_VERSION#v}" ]]; then
    info "bereits vorhanden ($IST)"
else
    [[ -n "$IST" ]] && warn "Vorhanden: $IST - wird durch $CDSP_VERSION ersetzt."
    TMP=$(mktemp -d)
    LOKAL_CDSP="${PAKETE:+$PAKETE/camilladsp/camilladsp-${CDSP_ARCH}-${CDSP_VERSION}.tar.gz}"
    if [[ -n "$LOKAL_CDSP" && -f "$LOKAL_CDSP" ]]; then
        info "$(basename "$LOKAL_CDSP") (aus dem Offline-Paket)"
        cp "$LOKAL_CDSP" "$TMP/c.tar.gz"
    else
        URL="https://github.com/HEnquist/camilladsp/releases/download/${CDSP_VERSION}/camilladsp-${CDSP_ARCH}.tar.gz"
        info "$(basename "$URL")"
        curl -fsSL "$URL" -o "$TMP/c.tar.gz" || die "Download fehlgeschlagen: $URL"
    fi
    tar -xzf "$TMP/c.tar.gz" -C "$TMP" || die "Archiv nicht lesbar."
    install -m755 "$TMP/camilladsp" /usr/local/bin/camilladsp
    rm -rf "$TMP"
    info "installiert: $(camilladsp --version)"
fi

# Die Konfiguration gegen die Binaerdatei pruefen, bevor die Dienste
# starten. Eine abgelehnte Konfiguration heisst, dass gar nichts laeuft -
# deshalb wird sie ersetzt und nicht nur bemaengelt. Die alte Fassung
# bleibt als Sicherung liegen.
if [[ -f $PREFIX/config/tv.yml ]]; then
    # 2.0.0: Die Pipeline ist der Band-Zweig (Decke, Vorlauf, Loudness-Pfad).
    # Eine vorhandene 1.x-Pipeline (RMS/R128-Kompressor, Aux2) wird gesichert
    # und ersetzt - erkennbar am fehlenden Prozessor "band_decke".
    if ! grep -qE '^[[:space:]]*(band_decke|ausgang):' "$PREFIX/config/tv.yml" 2>/dev/null \
       || [[ $(grep -cE '^[[:space:]]*(band_decke|ausgang):' "$PREFIX/config/tv.yml") -lt 2 ]]; then
        warn "Vorhandene tv.yml ist ein alter Stand (kein band_decke/ausgang) - wird ersetzt."
        SICHERUNG="$PREFIX/config/tv.yml.pre-2.0.0-$(date +%Y%m%d-%H%M%S)"
        mv "$PREFIX/config/tv.yml" "$SICHERUNG"
        warn "Gesichert nach $(basename "$SICHERUNG")."
        TV_ERSETZEN=1
    # Ein von einer fruehen detect-card.sh verstuemmelter Geraetename
    # (mit eckigen Klammern) ist syntaktisch gueltiges YAML - camilladsp
    # --check meckert also nicht, ALSA scheitert aber beim Oeffnen.
    elif grep -q 'device: "hw:\[' "$PREFIX/config/tv.yml" 2>/dev/null; then
        warn "Vorhandene tv.yml enthaelt einen verstuemmelten Geraetenamen."
        SICHERUNG="$PREFIX/config/tv.yml.kaputt-$(date +%Y%m%d-%H%M%S)"
        mv "$PREFIX/config/tv.yml" "$SICHERUNG"
        warn "Gesichert nach $(basename "$SICHERUNG"), wird ersetzt."
        TV_ERSETZEN=1
    elif camilladsp --check "$PREFIX/config/tv.yml" 2>&1 | grep -q "^Config is valid"; then
        info "vorhandene tv.yml ist gueltig"
    else
        warn "Vorhandene tv.yml wird von CamillaDSP abgelehnt:"
        # "|| true" ist hier zwingend: das Skript laeuft mit pipefail,
        # und camilladsp liefert bei ungueltiger Konfiguration Exitcode
        # 101. Ohne den Abfang wuerde set -e das Skript genau in seiner
        # eigenen Fehlerbehandlung abbrechen.
        camilladsp --check "$PREFIX/config/tv.yml" 2>&1 \
            | grep -v "^20[0-9][0-9]-" | grep -v "^Config is not valid" \
            | sed 's/^/      /' || true
        SICHERUNG="$PREFIX/config/tv.yml.ungueltig-$(date +%Y%m%d-%H%M%S)"
        mv "$PREFIX/config/tv.yml" "$SICHERUNG"
        warn "Gesichert nach $(basename "$SICHERUNG"), wird jetzt ersetzt."
        TV_ERSETZEN=1
    fi
fi

# ------------------------------------------------------------------ Python

log "Python-Umgebung"
mkdir -p "$PREFIX"/bin "$PREFIX"/config "$PREFIX"/backend/static "$PREFIX"/dsp
[[ -d $VENV ]] || python3 -m venv "$VENV"

# Alle Python-Pakete in den festen Versionen aus abhaengigkeiten/requirements*.txt.
# Offline zuerst: die Raeder im Paket passen nur zu dem System, fuer das sie
# gebaut sind (siehe PLATTFORM.txt). Passt es nicht, dieselben Versionen aus
# dem Internet. pycamilladsp liegt NICHT auf PyPI - die Liste nagelt den
# GitHub-Commit fest; offline zeigt pakete/requirements.txt auf das daraus
# gebaute Rad.
pip_installiere() {   # $1 = online-Liste, $2 = offline-Liste
    if [[ -n "$PAKETE" && -f "$2" ]]; then
        if "$VENV/bin/pip" install --quiet --no-index --find-links "$PAKETE/python" -r "$2"; then
            return 0
        fi
        warn "Offline-Pakete passen nicht zu diesem System - gleiche Versionen aus dem Internet."
    fi
    "$VENV/bin/pip" install --quiet -r "$1"
}
if [[ -n "$PAKETE" ]]; then
    "$VENV/bin/pip" install --quiet --no-index --find-links "$PAKETE/python" --upgrade pip wheel \
        || "$VENV/bin/pip" install --quiet --upgrade pip wheel
else
    "$VENV/bin/pip" install --quiet --upgrade pip wheel
fi
info "Python-Pakete in festen Versionen ($([[ -n "$PAKETE" ]] && echo offline || echo aus dem Internet))"
info "Eine gelbe pip-Warnung ueber eine \"yanked version\" (hifiberrydsp) ist ERWARTET."
pip_installiere "$REQ" "${PAKETE:+$PAKETE/requirements.txt}" \
    || die "Python-Pakete liessen sich nicht installieren (Liste: $REQ)."
# Spracherkennung (Silero VAD, optional im Band-Zweig): Fehlschlag ist kein
# Installationsfehler - der Zweig meldet dann "nicht verfuegbar".
pip_installiere "$REQ_VAD" "${PAKETE:+$PAKETE/requirements-vad.txt}" \
    || warn "onnxruntime/numpy nicht installiert - Spracherkennung im Band-Zweig nicht verfuegbar"
"$VENV/bin/python3" -c "import flask, evdev, camilladsp" 2>/dev/null \
    || die "flask/evdev/pycamilladsp fehlen trotz Installation."
info "flask, evdev, pycamilladsp"

# Das PyPI-Paket heisst "hifiberrydsp" (das GitHub-Repository heisst
# anders). Entscheidend ist aber etwas anderes: Auf PyPI sind ALLE
# Versionen zurueckgezogen ("yanked"). pip ueberspringt solche Versionen
# und meldet dann "Could not find a version that satisfies the
# requirement" - was wie ein Netzwerk- oder Namensproblem aussieht, aber
# keines ist. Mit fester Versionsangabe installiert pip sie trotzdem.
#
# Das Paket uebersetzt pyalsaaudio aus dem Quelltext und braucht dafuer
# alsa/asoundlib.h - deshalb steht libasound2-dev weiter oben in der
# Paketliste.
HAVE_DSP=1
grep -q "^hifiberrydsp==$HBDSP_VERSION" "$REQ" \
    || warn "requirements.txt nennt eine andere hifiberrydsp-Version als HBDSP_VERSION=$HBDSP_VERSION."

if "$VENV/bin/python3" -c "import hifiberrydsp" 2>/dev/null; then
    info "hifiberrydsp $HBDSP_VERSION installiert"
else
    HAVE_DSP=0
    warn "hifiberrydsp liess sich nicht installieren. Von Hand:"
    warn "  sudo $VENV/bin/pip install hifiberrydsp==$HBDSP_VERSION"
    warn "Ohne das Paket bleibt der Toslink-Eingang stumm."
fi

# Pruefen, ob die benoetigten Programme wirklich da sind. "pip install"
# ohne Fehler heisst noch nicht, dass die Programme heissen wie erwartet.
if [[ $HAVE_DSP -eq 1 ]]; then
    for prog in sigmatcpserver dsptoolkit; do
        if [[ ! -x "$VENV/bin/$prog" ]]; then
            warn "$prog fehlt trotz installiertem Paket - Dienst wird uebersprungen."
            HAVE_DSP=0
        fi
    done
fi

# ------------------------------------------------------------- ALSA-Sharing

log "Geteilten Eingang einrichten"
# dsnoop erlaubt mehreren Prozessen, denselben Aufnahmekanal zu lesen.
# CamillaDSP verarbeitet, ffmpeg misst parallel mit.
if [[ -f /etc/asound.conf ]] && ! grep -q "dsnoop_tv" /etc/asound.conf; then
    cp /etc/asound.conf /etc/asound.conf.tvdsp-backup
    warn "Bestehende /etc/asound.conf gesichert und ersetzt."
fi
cat > /etc/asound.conf <<'ASOUNDEOF'
# Von install.sh (TV-Dynamikprozessor) angelegt.
# Die Kartenbezeichnung wird beim Start von detect-card.sh gesetzt.

# Geteilter Aufnahmekanal: CamillaDSP verarbeitet, ffmpeg misst mit.
# period_size 256 statt 1024, damit auch die kleinen Blockgroessen
# der Latenzeinstellung funktionieren - alle waehlbaren Werte sind
# Vielfache davon.
pcm.dsnoop_tv {
    type dsnoop
    ipc_key 3141
    ipc_perm 0666
    slave {
        pcm "hw:sndrpihifiberry,0"
        channels 2
        rate 48000
        format S32_LE
        period_size 256
        buffer_size 16384
    }
}

# Formatumsetzung fuer ffmpeg. Auf dsnoop_tv direkt wuerde ffmpeg
# scheitern, weil dort nicht konvertiert wird und ffmpeg ein eigenes
# Format aushandelt.
pcm.tvmess {
    type plug
    slave.pcm "dsnoop_tv"
}

# USB-Eingang (ab 3.0.0): tvdsp-quelle schreibt fertiges Stereo in den
# Loopback (Geraet 0), CamillaDSP, Messung und Aufnahme lesen es hier
# (Geraet 1) - genau wie sonst dsnoop_tv/tvmess. Format wie beim Schreiben.
pcm.dsnoop_usb {
    type dsnoop
    ipc_key 3142
    ipc_perm 0666
    slave {
        pcm "hw:Loopback,1,0"
        channels 2
        rate 48000
        format S32_LE
        period_size 256
        buffer_size 16384
    }
}

pcm.tvmess_usb {
    type plug
    slave.pcm "dsnoop_usb"
}
ASOUNDEOF
info "/etc/asound.conf (dsnoop_tv + tvmess, dsnoop_usb + tvmess_usb)"

# Pipewire (Desktop) soll UR27 und Loopback nicht belegen: Er fuehrte den UR27
# sonst als Standard-Aufnahmequelle.
mkdir -p /etc/wireplumber/main.lua.d
cat > /etc/wireplumber/main.lua.d/51-tvdsp-usb-eingang.lua <<'WPEOF'
-- Von install.sh (TV-Dynamikprozessor) angelegt: USB-Empfaenger und Loopback gehoeren TVDSP.
table.insert(alsa_monitor.rules, {
  matches = {
    { { "device.name", "matches", "alsa_card.usb-HiFimeDIY_SA9227*" } },
    { { "device.name", "matches", "alsa_card.usb-miniDSP_MCHStreamer*" } },
    { { "device.name", "matches", "alsa_card.platform-snd_aloop*" } },
  },
  apply_properties = { ["device.disabled"] = true },
})
WPEOF
info "Pipewire laesst UR27, miniDSP und Loopback in Ruhe (greift nach Neustart)"

# ------------------------------------------------------------------ Kopien

log "Dateien installieren"
install -m644 "${QUELLE[backend/app.py]}"            "$PREFIX/backend/app.py"
install -m644 "${QUELLE[backend/static/index.html]}" "$PREFIX/backend/static/index.html"
install -m755 "${QUELLE[backend/flirc_listener.py]}"   "$PREFIX/backend/flirc_listener.py"
# Sprachmodell (optional): fehlt es, bietet die Weboberflaeche einen Download an.
if pfad=$(finde backend/silero_vad.onnx); then install -m644 "$pfad" "$PREFIX/backend/silero_vad.onnx"; fi
install -m755 "${QUELLE[bin/detect-card.sh]}"        "$PREFIX/bin/detect-card.sh"
install -m755 "${QUELLE[bin/enable-spdif-in.sh]}"    "$PREFIX/bin/enable-spdif-in.sh"
install -m755 "${QUELLE[bin/install-flirc-cli.sh]}"     "$PREFIX/bin/install-flirc-cli.sh"
install -m755 "${QUELLE[bin/tvdsp-quelle.py]}"       "$PREFIX/bin/tvdsp-quelle.py"
# Das DSP-Profil wird mitgeliefert statt bei der Installation geladen -
# so haengt die Reparatur nicht am Netz. Quelle und Begruendung:
# docs/hifiberry-dsp.md, Abschnitt "Das DSP-Profil ist die halbe Karte".
install -m644 "${QUELLE[dsp/dspdac-noautomute.xml]}" "$PREFIX/dsp/dspdac-noautomute.xml"

if [[ -f $PREFIX/config/tv.yml && -z "${TV_ERSETZEN:-}" ]]; then
    info "tv.yml existiert und ist gueltig - bleibt unveraendert"
else
    install -m644 "${QUELLE[config/tv.yml]}" "$PREFIX/config/tv.yml"
    # Sofort gegenpruefen, damit ein Fehler hier auffaellt und nicht
    # erst beim Start der Dienste.
    if camilladsp --check "$PREFIX/config/tv.yml" 2>&1 | grep -q "^Config is valid"; then
        info "config/tv.yml eingespielt und geprueft"
    else
        die "Die mitgelieferte tv.yml wird abgelehnt - bitte melden."
    fi
fi
# ohne "|| true" wuerde set -e hier bei jeder Erstinstallation aussteigen
[[ -f $PREFIX/config/presets.json ]] && info "presets.json bleibt erhalten" || true

chown -R "$RUN_USER":"$RUN_USER" "$PREFIX" 2>/dev/null || true

# Ablageort fuer TV-Mitschnitte im Home des Benutzers vorbereiten, damit die
# Aufnahmen dort liegen, wo der Benutzer sie direkt sieht (Dateimanager/SSH).
# Bevorzugt der vorhandene Downloads-Ordner, sonst ein klar benannter Ordner.
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -n "$RUN_HOME" && -d "$RUN_HOME" ]]; then
    if [[ -d "$RUN_HOME/Downloads" ]]; then
        info "Mitschnitte landen in $RUN_HOME/Downloads"
    else
        mkdir -p "$RUN_HOME/tvdsp_captures" 2>/dev/null || true
        chown "$RUN_USER":"$RUN_USER" "$RUN_HOME/tvdsp_captures" 2>/dev/null || true
        info "Mitschnitt-Ordner angelegt: $RUN_HOME/tvdsp_captures"
    fi
fi

# Ohne Gruppe "audio" darf der Dienst die Karte nicht oeffnen.
if [[ "$RUN_USER" != "root" ]]; then
    usermod -aG audio,input,plugdev "$RUN_USER" 2>/dev/null || true
    info "$RUN_USER zu den Gruppen audio, input und plugdev hinzugefuegt"
fi

# ----------------------------------------------------------------- Dienste

log "Autostart"

if [[ $HAVE_DSP -eq 1 ]]; then
cat > /etc/systemd/system/tvdsp-sigmatcp.service <<EOF
[Unit]
Description=HiFiBerry SigmaTCP-Server (Zugriff auf den DSP-Chip)
After=network.target

[Service]
ExecStart=$VENV/bin/sigmatcpserver
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/tvdsp-spdif.service <<EOF
[Unit]
Description=Toslink-Eingang des DAC+ DSP freischalten
Requires=tvdsp-sigmatcp.service
After=tvdsp-sigmatcp.service
Before=camilladsp.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$PREFIX/bin/enable-spdif-in.sh

[Install]
WantedBy=multi-user.target
EOF
info "tvdsp-sigmatcp, tvdsp-spdif"
fi

cat > /etc/systemd/system/camilladsp.service <<EOF
[Unit]
Description=CamillaDSP TV-Dynamikprozessor
After=sound.target tvdsp-spdif.service
Wants=sound.target

[Service]
User=$RUN_USER
ExecStartPre=/bin/sleep 5
ExecStartPre=+$PREFIX/bin/detect-card.sh $PREFIX/config/tv.yml
# Kein -w: das bedeutet "auf Konfiguration ueber WebSocket warten" und
# ignoriert die angegebene Datei. CamillaDSP laeuft dann ohne aktive
# Konfiguration, und jeder Zugriff darauf liefert None.
ExecStart=/usr/local/bin/camilladsp --loglevel=error --gain=-35 --gain2=-35 --gain3=-100 -p $CDSP_PORT $PREFIX/config/tv.yml
Restart=always
RestartSec=3
Nice=-10

[Install]
WantedBy=multi-user.target
EOF

# journald-Ratenbegrenzung fuer CamillaDSP (1.13.4): Bei fehlendem Toslink-
# Signal liefert das Capture-Geraet Teilbloecke, CamillaDSP schreibt dann
# ~140 WARN-Zeilen/s - 4 GB Journal auf der SD-Karte in wenigen Tagen. Der
# journald-Standard (10000 Meldungen/30 s) greift dabei nicht.
install -d -m755 /etc/systemd/system/camilladsp.service.d
cat > /etc/systemd/system/camilladsp.service.d/10-tvdsp-logratelimit.conf <<'EOF'
[Service]
LogRateLimitIntervalSec=30
LogRateLimitBurst=300
EOF
# 2.8.8: CamillaDSP laeuft mit --loglevel=error (siehe ExecStart). Die harmlosen
# "Capture read ..."-Warnungen (~6.200/min) kosteten CPU, fuellten das Journal
# (1 GB) und verdeckten echte Warnungen - die gingen im Ratenlimit unter.
# Aussetzer findet man ueber /proc/asound/card0/pcm0p/sub0/status
# (docs/hifiberry-dsp.md, "get-checksum haelt den DSP-Chip an").
# Journal insgesamt auf 200 MB begrenzen (SD-Karte).
install -d -m755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/10-tvdsp.conf <<'EOF'
[Journal]
SystemMaxUse=200M
EOF
systemctl restart systemd-journald 2>/dev/null || true

cat > /etc/systemd/system/tvdsp-web.service <<EOF
[Unit]
Description=Weboberflaeche fuer den TV-Dynamikprozessor
After=camilladsp.service
Wants=camilladsp.service

[Service]
User=$RUN_USER
Environment=HTTP_PORT=$HTTP_PORT
Environment=CDSP_PORT=$CDSP_PORT
Environment=STATE_DIR=$PREFIX/config
# Keine Nutzungsdaten von onnxruntime an den Hersteller (Spracherkennung)
Environment=ORT_DISABLE_TELEMETRY=1
Environment=HOME=$(getent passwd "$RUN_USER" | cut -d: -f6)
WorkingDirectory=$PREFIX/backend
ExecStart=$VENV/bin/python3 app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
info "camilladsp, tvdsp-web"

# Die Weboberflaeche darf genau zwei privilegierte Aktionen ausloesen.
# Keine Shell und keine frei waehbaren Argumente: nur systemctl reboot/poweroff.
# Die eigentliche API akzeptiert ebenfalls ausschliesslich diese zwei Werte.
if [[ "$RUN_USER" != "root" ]]; then
    SYSTEMCTL_BIN="$(command -v systemctl)"
    SUDOERS_FILE=/etc/sudoers.d/tvdsp-system
    # Neuinstallation aus der Weboberflaeche (2.1.0): laeuft als eigene
    # transiente systemd-Unit, damit sie den Neustart von tvdsp-web (den sie
    # selbst ausloest) ueberlebt. Die Regel erlaubt genau diesen Aufruf.
    SYSTEMD_RUN_BIN=$(command -v systemd-run || echo /usr/bin/systemd-run)
    cat > "$SUDOERS_FILE" <<EOF
$RUN_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN reboot, $SYSTEMCTL_BIN poweroff
$RUN_USER ALL=(root) NOPASSWD: $SYSTEMD_RUN_BIN --unit=tvdsp-install --collect --setenv=TVDSP_RUN_USER=$RUN_USER -p StandardOutput=append\:$PREFIX/config/install.log -p StandardError=append\:$PREFIX/config/install.log $PREFIX/paket/install.sh
EOF
    chmod 0440 "$SUDOERS_FILE"
    visudo -cf "$SUDOERS_FILE" >/dev/null || die "sudo-Regel fuer Reboot/Poweroff ist ungueltig"
    info "Web-Reboot/Poweroff fuer $RUN_USER freigegeben"

    # flirc_util spricht den Stick per hidraw an; das gehoert ohne udev-Regel
    # nur root. Die Weboberflaeche programmiert den Stick deshalb ueber sudo
    # (nur dieses eine Programm, keine Shell). Die udev-Regel unten ist der
    # zweite Weg, falls sudo einmal nicht greift.
    SUDOERS_FLIRC=/etc/sudoers.d/tvdsp-flirc
    cat > "$SUDOERS_FLIRC" <<EOF
$RUN_USER ALL=(root) NOPASSWD: /usr/bin/flirc_util, /usr/local/bin/flirc_util
EOF
    chmod 0440 "$SUDOERS_FLIRC"
    visudo -cf "$SUDOERS_FLIRC" >/dev/null || die "sudo-Regel fuer flirc_util ist ungueltig"
    info "flirc_util (Stick programmieren) fuer $RUN_USER freigegeben"
fi

# FLIRC-Stick (USB 20a0:0001 Gen1 / 20a0:0006 Gen2) fuer die Gruppe plugdev
# lesbar/schreibbar machen, damit flirc_util auch ohne sudo funktioniert.
cat > /etc/udev/rules.d/99-tvdsp-flirc.rules <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="20a0", ATTRS{idProduct}=="0001", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="20a0", ATTRS{idProduct}=="0006", MODE="0660", GROUP="plugdev"
KERNEL=="hidraw*", ATTRS{idVendor}=="20a0", ATTRS{idProduct}=="0001", MODE="0660", GROUP="plugdev"
KERNEL=="hidraw*", ATTRS{idVendor}=="20a0", ATTRS{idProduct}=="0006", MODE="0660", GROUP="plugdev"
EOF
udevadm control --reload-rules >/dev/null 2>&1 || true
udevadm trigger >/dev/null 2>&1 || true
info "udev-Regel fuer FLIRC (hidraw, Gruppe plugdev)"

# Optionaler FLIRC-Lauscher. Ohne USB-Stick schlaeft er und probiert alle
# drei Sekunden erneut; TVDSP und Audio haengen nicht von ihm ab.
cat > /etc/systemd/system/tvdsp-flirc.service <<EOF
[Unit]
Description=FLIRC/IR-Fernbedienung fuer TVDSP
After=tvdsp-web.service
Wants=tvdsp-web.service

[Service]
User=$RUN_USER
Environment=HTTP_PORT=$HTTP_PORT
Environment=STATE_DIR=$PREFIX/config
ExecStart=$VENV/bin/python3 $PREFIX/backend/flirc_listener.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
info "tvdsp-flirc"

# USB-Eingang (ab 3.0.0): liest den UR27, decodiert Dolby/DTS, mischt 5.1 auf
# Stereo und schreibt in den Loopback. Schlaeft, solange der Eingang
# "HiFiBerry" gewaehlt ist oder der UR27 fehlt (1 Pruefung/s).
cat > /etc/systemd/system/tvdsp-quelle.service <<EOF
[Unit]
Description=TVDSP USB-Eingang (miniDSP/UR27: PCM/Dolby -> Stereo in den Loopback)
After=sound.target

[Service]
User=$RUN_USER
Environment=TVDSP_PREFIX=$PREFIX
ExecStart=$VENV/bin/python3 $PREFIX/bin/tvdsp-quelle.py
Restart=always
RestartSec=2
Nice=-5
# Echtzeit (3.3.3): startende Prozesse (ffmpeg-Neustart bei EQ-Aenderung,
# sudo flirc_util) nahmen dem Leser sonst 40-100 ms Rechenzeit -> Ueberlauf.
CPUSchedulingPolicy=fifo
CPUSchedulingPriority=20
# Statusdatei im Arbeitsspeicher (/run/tvdsp), nicht auf der SD-Karte (3.3.6)
RuntimeDirectory=tvdsp
RuntimeDirectoryPreserve=yes

[Install]
WantedBy=multi-user.target
EOF
rm -f "$PREFIX/config/quelle-status.json"     # bis 3.3.5 auf der SD-Karte
info "tvdsp-quelle"

# FLIRC-CLI separat im Hintergrund einrichten. Der Hauptinstaller startet
# diesen Oneshoot nur --no-block. Ein Timer versucht es spaeter erneut, falls
# beim ersten Lauf z. B. Netzwerk oder Hersteller-Repository fehlen.
cat > /etc/systemd/system/tvdsp-flirc-setup.service <<EOF
[Unit]
Description=FLIRC CLI fuer TVDSP installieren/aktualisieren
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$PREFIX/bin/install-flirc-cli.sh
TimeoutStartSec=160
EOF

cat > /etc/systemd/system/tvdsp-flirc-setup.timer <<EOF
[Unit]
Description=FLIRC CLI Setup fuer TVDSP erneut versuchen

[Timer]
OnBootSec=45s
OnUnitInactiveSec=6h
Persistent=true
Unit=tvdsp-flirc-setup.service

[Install]
WantedBy=timers.target
EOF
info "tvdsp-flirc-setup (Hintergrund)"

# tvdsp-kick: stoesst nach dem Kaltstart die Wiedergabe an. Siehe
# docs/hifiberry-dsp.md "Kaltstart-Stille". Laeuft NACH CamillaDSP, weil der
# get-checksum-Anstoss nur wirkt, wenn CamillaDSP schon spielt.
if [[ $HAVE_DSP -eq 1 ]]; then
cat > /etc/systemd/system/tvdsp-kick.service <<EOF
[Unit]
Description=Wiedergabe nach Kaltstart anstossen (DSP-Ressourcensperre loesen)
After=camilladsp.service tvdsp-sigmatcp.service
Wants=camilladsp.service
Requires=tvdsp-sigmatcp.service

[Service]
Type=oneshot
RemainAfterExit=yes
# Kurz warten, bis CamillaDSP wirklich spielt, dann den programmweiten
# DSP-Zugriff ausloesen, der die Wiedergabe-Ressource freigibt.
ExecStartPre=/bin/sleep 8
# get-checksum haelt den DSP-Chip kurz an (genau das loest die Sperre). Die
# Pruefsumme wird fuer das Backend abgelegt, damit es den Chip im Betrieb
# nicht erneut anhalten muss (2.8.7: sonst Knacken beim Profil-Pruefen).
ExecStart=/bin/sh -c 's=\$\$($VENV/bin/dsptoolkit get-checksum 2>/dev/null | tr -d "[:space:]"); if [ -n "\$\$s" ]; then echo "\$\$s" > $PREFIX/config/dsp-profil-pruefsumme.neu && mv -f $PREFIX/config/dsp-profil-pruefsumme.neu $PREFIX/config/dsp-profil-pruefsumme; fi'


[Install]
WantedBy=multi-user.target
EOF
info "tvdsp-kick"
fi

systemctl daemon-reload
if [[ $HAVE_DSP -eq 1 ]]; then
    systemctl enable tvdsp-sigmatcp tvdsp-spdif tvdsp-kick >/dev/null 2>&1 || true
fi
systemctl enable camilladsp tvdsp-web tvdsp-flirc tvdsp-quelle >/dev/null 2>&1 || true
systemctl enable tvdsp-flirc-setup.timer >/dev/null 2>&1 || true
info "aktiviert"

# ------------------------------------------------------------- Neustart

log "Dienste neu starten"

if grep -qi hifiberry /proc/asound/cards 2>/dev/null; then
    KARTE_DA=1
else
    KARTE_DA=0
fi

# Die Weboberflaeche braucht keine Hardware - immer neu starten,
# damit eine aktualisierte app.py sofort greift.
systemctl restart tvdsp-web || warn "tvdsp-web liess sich nicht starten"
info "tvdsp-web neu gestartet"
systemctl restart tvdsp-flirc >/dev/null 2>&1 || warn "tvdsp-flirc liess sich nicht starten"
info "tvdsp-flirc neu gestartet (wartet ohne Stick lastarm)"
systemctl restart tvdsp-quelle >/dev/null 2>&1 || warn "tvdsp-quelle liess sich nicht starten"
info "tvdsp-quelle neu gestartet (schlaeft, solange Eingang HiFiBerry)"
if command -v flirc_util >/dev/null 2>&1; then
    info "FLIRC CLI bereit"
else
    systemctl start --no-block tvdsp-flirc-setup.service >/dev/null 2>&1 || true
    info "FLIRC CLI Setup im Hintergrund gestartet (Installation laeuft ohne Warten weiter)"
fi

if [[ $KARTE_DA -eq 1 ]]; then
    if [[ $HAVE_DSP -eq 1 ]]; then
        systemctl restart tvdsp-sigmatcp >/dev/null 2>&1 || true

        # --- DSP-Profil pruefen, bei Bedarf flashen (neu in 1.1.4) ---
        # Der Analogausgang des DAC+ DSP haengt HINTER dem DSP-Chip.
        # Nur ein Profil, das I2S vom Pi zum DAC routet, erzeugt Ton.
        # Auf der Karte lag stattdessen "Beocreate Universal"
        # (Pruefsumme 40FB6C92...) - Folge: Eingangspegel ok, aber
        # voellige Stille am Analogausgang. Sollwert ist die am echten
        # Geraet verifizierte Pruefsumme von dspdac-noautomute.xml.
        SOLL_SUMME=7F88CAAD8E8A7E4F05731605AE16EF38
        # Pruefsumme des Backends verwerfen - ein Flash kann das Profil
        # aendern; tvdsp-kick legt am Ende eine frische ab.
        rm -f "$PREFIX/config/dsp-profil-pruefsumme"
        IST_SUMME=""
        for i in $(seq 1 10); do
            IST_SUMME=$("$VENV/bin/dsptoolkit" get-checksum 2>/dev/null \
                        | tr -d '[:space:]') || true
            [[ -n "$IST_SUMME" ]] && break
            sleep 2
        done
        if [[ "$IST_SUMME" == "$SOLL_SUMME" ]]; then
            info "DSP-Profil korrekt (dspdac-noautomute)"
        elif [[ -z "$IST_SUMME" ]]; then
            warn "DSP nicht erreichbar - Profil ungeprueft. Von Hand:"
            warn "  sudo $VENV/bin/dsptoolkit install-profile $PREFIX/dsp/dspdac-noautomute.xml"
        else
            warn "Fremdes DSP-Profil auf dem Chip ($IST_SUMME)"
            info "Flashe dspdac-noautomute (schreibt ins EEPROM, dauerhaft)"
            if "$VENV/bin/dsptoolkit" install-profile \
                   "$PREFIX/dsp/dspdac-noautomute.xml" >/dev/null 2>&1; then
                NEU=$("$VENV/bin/dsptoolkit" get-checksum 2>/dev/null \
                      | tr -d '[:space:]') || true
                if [[ "$NEU" == "$SOLL_SUMME" ]]; then
                    info "Profil geflasht und verifiziert"
                else
                    warn "Flashen unbestaetigt (Pruefsumme: ${NEU:-keine})"
                fi
            else
                warn "install-profile fehlgeschlagen - Toslink/Analog bleiben ggf. stumm"
            fi
        fi
        # Erst NACH dem Profil die Eingangsregister setzen - das Flashen
        # laedt den Chip neu und wuerde sie sonst wieder loeschen.
        systemctl restart tvdsp-spdif    >/dev/null 2>&1 || true
    fi
    systemctl restart camilladsp || warn "camilladsp liess sich nicht starten"
    sleep 3
    if systemctl is-active --quiet camilladsp; then
        info "camilladsp laeuft"
    else
        warn "camilladsp laeuft nicht - Ursache: journalctl -u camilladsp -n 30"
    fi
    # Wiedergabe anstossen (loest die DSP-Ressourcensperre; siehe
    # docs/hifiberry-dsp.md "Kaltstart-Stille"). get-checksum liest nur, stoesst aber
    # reproduzierbar die Analogausgabe an.
    if [[ $HAVE_DSP -eq 1 ]]; then
        systemctl restart tvdsp-kick >/dev/null 2>&1 \
            || "$VENV/bin/dsptoolkit" get-checksum >/dev/null 2>&1 || true
        info "Wiedergabe angestossen (tvdsp-kick)"
    fi
else
    # Ohne Karte wuerde camilladsp im Sekundentakt neu starten und wie
    # ein Fehler aussehen. Also gar nicht erst anfangen.
    systemctl stop camilladsp >/dev/null 2>&1 || true
    warn "Keine HiFiBerry-Karte gefunden - camilladsp bleibt vorerst gestoppt."
fi

# ---------------------------------------------------------------- Paketkopie
# Das installierte System traegt seinen eigenen Installer: die Weboberflaeche
# kann damit "Neu installieren" ausfuehren (prueft/flasht DSP-Profil, setzt
# tv.yml, Dienste, sudo-Regeln), ohne dass das Quellpaket noch vorliegen muss.
if [[ "$SRC" == "$PREFIX/paket" ]]; then
    # Laeuft bereits aus der Paketkopie (Web-Neuinstallation): nichts zu kopieren.
    # (2.1.1: 'install' auf dieselbe Datei brach den Lauf vor "Fertig" ab.)
    info "Installer laeuft aus der Paketkopie - Kopie bleibt wie sie ist"
else
    install -d -m755 "$PREFIX/paket" "$PREFIX/paket/backend/static" "$PREFIX/paket/bin" "$PREFIX/paket/config" "$PREFIX/paket/dsp"
    install -m755 "$SRC/install.sh" "$PREFIX/paket/install.sh"
    [[ -f "$SRC/VERSION" ]] && install -m644 "$SRC/VERSION" "$PREFIX/paket/VERSION"
    for rel in "${!QUELLE[@]}"; do
        case "$rel" in
            backend/*|bin/*|config/*|dsp/*) install -m644 "${QUELLE[$rel]}" "$PREFIX/paket/$rel" ;;
        esac
    done
    if pfad=$(finde backend/silero_vad.onnx); then install -m644 "$pfad" "$PREFIX/paket/backend/silero_vad.onnx"; fi
    # Feste Versionen + Offline-Pakete mitnehmen, damit auch die Web-Neuinstallation
    # und der FLIRC-Hintergrunddienst ohne Internet auskommen.
    install -d -m755 "$PREFIX/paket/abhaengigkeiten"
    install -m644 "$REQ" "$PREFIX/paket/abhaengigkeiten/requirements.txt"
    install -m644 "$REQ_VAD" "$PREFIX/paket/abhaengigkeiten/requirements-vad.txt"
    if [[ -n "$PAKETE" ]]; then
        rm -rf "$PREFIX/paket/abhaengigkeiten/pakete"
        cp -a "$PAKETE" "$PREFIX/paket/abhaengigkeiten/pakete"
    fi
    chmod 755 "$PREFIX/paket/bin/"*.sh "$PREFIX/paket/backend/flirc_listener.py" 2>/dev/null || true
    chown -R "$RUN_USER" "$PREFIX/paket" 2>/dev/null || true
    info "Paketkopie fuer die Web-Neuinstallation unter $PREFIX/paket"
fi

# ---------------------------------------------------------------- Abschluss

IP=$(hostname -I 2>/dev/null | awk '{print $1}')

if [[ $KARTE_DA -eq 1 ]]; then
    ABSCHLUSS="Fertig. Die Dienste laufen bereits - kein Neustart noetig."
else
    ABSCHLUSS="Fertig, aber die Soundkarte fehlt noch.
  Karte aufstecken, dann:   sudo reboot
  Der Treiber wird erst beim Booten geladen. Die Weboberflaeche
  laeuft schon jetzt und meldet dort, was noch fehlt."
fi

cat <<EOF

────────────────────────────────────────────────────────────
  $ABSCHLUSS

  Erreichbar:
    Oberflaeche    http://${IP:-<pi-ip>}:$HTTP_PORT
    Status         systemctl status camilladsp tvdsp-web --no-pager
    Protokoll      journalctl -u tvdsp-web -f
    Eingang testen arecord -D dsnoop_tv -f S32_LE \\
                     -r 48000 -c 2 -d 5 /tmp/test.wav

  AM FERNSEHER:
    - Digitaler Tonausgang auf PCM (nicht Auto, nicht Bitstream)
    - Interne Lautsprecher abwaehlen, nicht nur stummschalten
    - Klangmodus auf Standard oder Aus

  MESSQUELLE:
    Unter "Klang" kannst du zwischen RMS und EBU R128 umschalten.
    Die RMS-Skala gleicht sich automatisch an die R128-Messung an,
    sobald Ton anliegt.

────────────────────────────────────────────────────────────

EOF
