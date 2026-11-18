#!/usr/bin/env python3
"""
TV-Dynamikprozessor - Backend

Zwei Aufgaben:

  1. Regelung (ab 2.0.0 nur noch der Band-Zweig). Ein Hintergrundthread
     misst die Eingangslautheit (ffmpeg/EBU R128) und haelt sie in einem
     Lautheitsband: zu laut -> Aux1 herunter, zu leise -> langsam hoch, im
     Band nichts. Die Oberkante ist zusaetzlich eine Decke im DSP
     (Kompressor hinter Aux1, 30 ms Vorlauf). Psychoakustische Loudness-
     Kompensation ueber Aux3, optionale Spracherkennung fuer das Anheben.
     Konzept und Messungen: docs/band-konzept.md.

  2. Bedienung. REST-API fuer die Weboberflaeche.

Die frueheren Zweige RMS und EBU-R128-Huelle (bis 1.13.x, R128 als Option
nochmals 2.5.0-2.8.x) sind entfernt; ihre Messung, Kalibrierung und
Stille-Logik leben im Band-Zweig weiter.

Faellt dieser Prozess aus, laeuft der Ton weiter - nur ohne
Nachregelung. Ein Ausfall macht die Box schlechter, nicht stumm.

Geprueft gegen CamillaDSP v4.1.3 und pycamilladsp aus dem GitHub-Stand.
"""

import copy
import functools
import glob
import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# onnxruntime (Spracherkennung) sendet in den offiziellen Builds auch unter
# Linux Nutzungsdaten an den Hersteller, wenn man es nicht abschaltet. Muss
# gesetzt sein, bevor onnxruntime geladen wird (siehe install.sh, Unit tvdsp-web).
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

try:
    from camilladsp import CamillaClient
except ImportError:                      # aeltere Paketversion
    from camilladsp import CamillaConnection as CamillaClient

VERSION = "3.3.10"

CDSP_HOST = os.environ.get("CDSP_HOST", "127.0.0.1")
CDSP_PORT = int(os.environ.get("CDSP_PORT", "1234"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
STATE_DIR = os.environ.get("STATE_DIR", "/opt/tvdsp/config")
PRESET_FILE = os.path.join(STATE_DIR, "presets.json")   # nur noch fuer die EQ-Uebernahme aus 1.x
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")

log = logging.getLogger("tvdsp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
# Jede Webanfrage als Protokollzeile waren bei offener Seite 3-4 Zeilen/s im
# Journal (2.8.8). Fehler meldet werkzeug weiterhin.
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__, static_folder="static", static_url_path="")
cdsp = CamillaClient(CDSP_HOST, CDSP_PORT)
lock = threading.RLock()

# FLIRC ist optional und komplett von der Audiokette entkoppelt. Die CLI
# programmiert den USB-Stick, der Laufzeitdienst liest nur dessen Linux-
# Eingabegeraet. Fehlt der Stick, bleibt der Rest des Systems unveraendert.
FLIRC_LEARN_FILE = os.path.join(STATE_DIR, "flirc-learning")
flirc_last = {"action": None, "zeit": 0.0}

_flirc_save_timer = None

# ISO-Raster fuer 2/3-Oktav-Equalizer, Faktor 1,585 durchgehend.
# 16 kHz statt der sonst ueblichen 20 kHz, weil ein Peaking-Filter nahe
# der Nyquist-Grenze (24 kHz bei 48 kHz) verzerrt rechnet.
BANDS = [16, 25, 40, 63, 100, 160, 250, 400,
         630, 1000, 1600, 2500, 4000, 6300, 10000, 16000]
EQ_NAMES = ["eq%02d" % i for i in range(len(BANDS))]

# Pinselbreite: Sigma der Gaussverteilung auf die Nachbarbaender und das
# dazu passende Q. Anteil am Zug bei +-1 / +-2 / +-3 Baendern:
#   schmal    6 % /  0 % /  0 %   - praktisch nur das eine Band
#   normal   50 % /  6 % /  0 %   - saubere Glocke ueber drei Baender
#   breit    73 % / 29 % /  6 %   - Klangcharakter statt Eingriff
# Nachgemessen ergibt das Bandbreiten von 0,36 / 0,68 / 0,99 Oktaven.
SPREAD = {
    "einzeln": {"sigma": 0.6, "q": 4.0},
    "normal":  {"sigma": 1.2, "q": 2.1},
    "breit":   {"sigma": 1.8, "q": 1.4},
}


# Grenzen fuer alles, was das Frontend anfassen darf. Eine
# Weboberflaeche soll die Signalkette nicht kaputtmachen koennen.
LIMITS = {
    "vorpegel":     (-30.0, 6.0),
    "volume":       (-60.0, 0.0),   # kein positiver Boost: Lautheit kommt vom Verstaerker
    "eq":           (-12.0, 12.0),
    "kalibrierung": (-40.0, 40.0),  # nur intern fuer Auto-Kalibrierung
    "flirc_step":    (0.5, 5.0),
}
DEFAULT_KALIBRIERUNG = 16.0     # Startwert LUFS - RMS(dBFS), bis die Automatik ihn bestimmt hat


# ------------------------------------------------------------ Band-Zweig
# Seit 2.0.0 die einzige Regelung. Der Nutzer gibt NUR ein Lautheitsband vor.
# Alles andere sind feste Konstanten, die offline hergeleitet wurden (siehe
# docs/band-konzept.md).
BAND_DEFAULTS = {
    "band_oben": -26.0,      # Oberkante in LUFS: darueber wird abgesenkt
    "band_unten": -30.0,     # Unterkante in LUFS: darunter wird angehoben
    "band_vorlauf": True,    # Vorlauf fuer die Decke an/aus (kostet Latenz)
    "band_vorlauf_ms": 30.0, # Laenge des Vorlaufs 5..50 ms (Regler; offline: 25 ms -> Einsatz +6,4 dB/28 ms, 30 ms -> +3,8 dB/7 ms)
    "band_rauschsperre": -40.0,  # Eingang (vor Leveler) darunter wird NIE angehoben: Rauschen/Atmo, keine Sprache
    "band_loudness": True,       # psychoakustische Kompensation bei Absenkung (Aux3-Parallelpfad)
    "band_loudness_staerke": 70.0,   # Prozent der Kurve bei voller Absenkung
    "band_vad": False,           # Spracherkennung (Silero VAD) fuer das Anheben; optional
}
# USB-Eingang (ab 3.0.0): Mix 5.1 -> Stereo, Anteile je Seite, Summe 1 (kein
# Uebersteuern). Gleiche Tabelle in bin/tvdsp-quelle.py - beide gleich halten.
# "kino" = Standard: Center etwas staerker als Front, LFE spielt mit.
MIX_VOREINSTELLUNGEN = {
    "kino":    {"front": 0.258, "center": 0.323, "lfe": 0.274, "surround": 0.145},
    "neutral": {"front": 0.414, "center": 0.293, "lfe": 0.0,   "surround": 0.293},
    "sprache": {"front": 0.200, "center": 0.500, "lfe": 0.150, "surround": 0.150},
}
MIX_STANDARD = "kino"
# Hoehendaempfung (3.2.0, Schalter im Klang-Reiter; ersetzt die Mess-
# gewichtung aus 3.1.0, die nur den Pegel, nicht die Klangbalance aenderte und
# Sprache sogar anhob). Kippt das Verhaeltnis Hoehen/Rest, werden die Hoehen
# gedaempft:
#   Hoehenanteil = Lautheit ueber 3 kHz minus Gesamtlautheit (ffmpeg ebur128,
#   M-Frames 10 Hz, Energiemittel ueber HOEHEN_FENSTER_SEK, mit EQ-Spiegel).
#   Daempfung = Anteil - HOEHEN_SCHWELLE_DB, 0..HOEHEN_MAX_DB, Huelle
#   Attack/Release. Klangweg: Parallelpfad x + a*(Shelf(x) - x) nach der Decke,
#   a ueber Aux4 (wie die Loudness-Kurve ueber Aux3). Schalter aus = Pipeline
#   wie bisher (Rueckbau = Schalter aus).
# Offline (echter Messfaden, mit EQ-Spiegel) an 6 Mitschnitten: Anteil im
# Median Werbung -8,0/-9,0 dB, Programm -9,2..-12,6 dB. Daempfung > 1 dB:
# Werbung 30-41 % der Zeit, Programm 4-35 % (helle Stellen), Spitzen 2,6-5,4 dB.
HOEHEN_FREQ = 3000
HOEHEN_MAX_DB = 6.0
HOEHEN_SCHWELLE_DB = -8.0
HOEHEN_FENSTER_SEK = 3.0
HOEHEN_ATTACK = 0.3
HOEHEN_RELEASE = 1.5
HOEHEN_STILLE_LUFS = -45.0
HOEHEN_FADER = 4                 # Aux4
HOEHEN_AUS_DB = -100.0


class HoehenRegler:
    """Aus 10-Hz-M-Frames (gesamt, ueber 3 kHz) die Hoehendaempfung in dB."""

    def __init__(self):
        self.frames = deque(maxlen=max(1, int(round(HOEHEN_FENSTER_SEK * 10))))
        self.daempfung = 0.0
        self.anteil = None

    def schritt(self, m_voll, m_hoch, dt=0.1):
        if m_voll is None or m_hoch is None or m_voll < HOEHEN_STILLE_LUFS:
            return self.daempfung                  # Pause/Stille: halten
        self.frames.append((10 ** (m_voll / 10.0), 10 ** (max(m_hoch, -120.0) / 10.0)))
        ev = sum(f[0] for f in self.frames)
        eh = sum(f[1] for f in self.frames)
        self.anteil = 10.0 * math.log10(max(eh, 1e-30) / max(ev, 1e-30))
        soll = min(HOEHEN_MAX_DB, max(0.0, self.anteil - HOEHEN_SCHWELLE_DB))
        tau = HOEHEN_ATTACK if soll > self.daempfung else HOEHEN_RELEASE
        self.daempfung += (soll - self.daempfung) * (1.0 - math.exp(-dt / tau))
        return self.daempfung


def hoehen_fader_db(daempfung):
    """Aux4-Wert fuer eine Hoehendaempfung in dB (Shelf -HOEHEN_MAX_DB, parallel)."""
    d = max(0.0, min(HOEHEN_MAX_DB, float(daempfung)))
    if d < 0.05:
        return HOEHEN_AUS_DB
    a = (1.0 - 10 ** (-d / 20.0)) / (1.0 - 10 ** (-HOEHEN_MAX_DB / 20.0))
    return max(HOEHEN_AUS_DB, 20.0 * math.log10(a))

EINGANG_DEFAULTS = {
    "eingang": "hifiberry",                     # hifiberry | usb (miniDSP/UR27 ueber tvdsp-quelle)
    "mix": dict(MIX_VOREINSTELLUNGEN[MIX_STANDARD]),
    "mix_voreinstellung": MIX_STANDARD,          # Name oder "eigen"
    "hoehen_daempfung": False,                   # 3.2.0: Hoehen daempfen, wenn die Balance kippt
}

# Der Mitten-Sidechain (wie jeder Einzeldetektor) sieht korreliertes Signal
# (Sprache, L=R) ~3 dB heisser als breites Stereo. Gemessen mit rosa Rauschen
# (Schwelle -30, 20:1): unkorreliert klemmt bei Schwelle +0,6 dB, korreliert
# bei Schwelle -2,4 dB. Seit 2.9.0 ist die Decke fest auf "Effekte" geeicht
# (bis 2.8.x waehlbar: Sprache +2,4 / Mitte 0 / Effekte -0,6): breite Effekte
# und Musik landen exakt auf Oberkante + Marge, Sprache wird ~3 dB darunter
# begrenzt - die strengste der drei Varianten (Nutzerwunsch).
BAND_DECKE_BEZUG_EFFEKTE = -0.6
# CamillaDSPs Kompressor misst |x| je Sample in dB und glaettet im dB-Bereich
# (Attack 2 ms, Release 300 ms): das liest auf TV-Material ~3,6 dB heisser als
# der 400-ms-RMS, auf den die Kalibrierung bezogen ist. Offline (2.7.0, echte
# TV-Aufnahme, exakte Live-Config, Main 0): ohne Ausgleich wurde Material
# 3 dB UNTER der Oberkante schon um 3 dB gedrueckt; mit +3 (netto, Bezug
# "effekte") bleibt das Band unberuehrt, p95 des Ausgangs -22,5, max -20.
BAND_DECKE_DETEKTOR_DB = 3.6

# Loudness-Kompensation (1.11.0): Equal-Loudness-Effekt bei starker Absenkung.
# Paralleler Pfad out = x + a*(H(x) - x); H = Low-Shelf +6 dB @120 Hz, High-
# Shelf +2 dB @6 kHz, Trim -1.4 dB (lautheitsneutral: rosa Rauschen -14.6 ->
# -14.6 LUFS, offline mit CamillaDSP 4.1.3 gemessen). a kommt vom Aux3-Fader:
# a = staerke * clamp(-Leveler-Gain / BAND_LD_VOLL, 0, 1), geglaettet mit 2 s,
# damit man die Kurve nie als Klangflackern hoert. Bei 0 dB Absenkung nichts.
BAND_LD_VOLL = 20.0          # dB Absenkung, bei der die volle Kurve anliegt
BAND_LD_TAU = 2.0            # s Glaettung des Anteils
BAND_LD_AUS_DB = -100.0      # Aux3 "aus"
BAND_LD_SCHREIB_EPS = 0.2    # dB, ab der der Aux3-Fader neu geschrieben wird
# v4 (1.10.1, aus dem Hoertest an einem Orchesterstueck und einem Film-Trailer abgeleitet):
# Zielpunkte 10 % der Bandbreite innerhalb der Kanten, IM BAND WIRD NICHTS
# GEREGELT (ein "Einpendeln zur Mitte" pumpte hoerbar: 6,9 statt 2,8 Gain-
# Umkehrungen/min), leise Szene = Momentary 2,5 s unter der Unterkante, dann
# 3 dB/s bis zur Kante. Deckel +17 dB absolut.
BAND_EINZUG_ANTEIL = 0.10   # Zielpunkt = Kante + 10 % der Bandbreite nach innen
# 2.3.0 (nach Recherche Juenger/Dolby und Hoertest): Der Leveler nach UNTEN
# bezieht sich auf Short-Term (3 s) - er entlastet nur die Decke, die Spitzen
# faengt die Decke. Nach OBEN wie R128 in 1.9.28: Bezug ist das 2-s-Mittel der
# Momentary-Frames, kein Szenen-Gate, in ~1,5-2 s am Ziel (tau 0,5 s, max.
# 6 dB/s). Bezahlbar, weil die Decke den Fall "leise hochgezogen, dann laut"
# abfaengt; Pausen-Atmen innerhalb des Bandes ist bewusst erlaubt.
BAND_TAU_RUNTER = 0.20      # Leveler nach unten (Bezug Short-Term)
BAND_TAU_HOCH = 0.5         # Anheben: 63 % nach 0,5 s, 95 % nach 1,5 s
BAND_RATE_HOCH = 6.0        # dB/s beim Anheben (haertere Grenze als tau)
BAND_MITTEL_SEK = 2.0       # Fenster des Aufwaerts-Bezugs (Energiemittel der M-Frames)
# 2.9.5: Eine Anhebung traegt den Ton hoechstens bis zur Unterkante (+ Einzug).
# Wird es nach einer leisen Stelle lauter, nimmt die Regelung die Anhebung langsam
# zurueck (nie unter 0 dB), statt sie bis zur Oberkante mitzuschleppen. Nur nach
# unten, nur solange angehoben ist. RUECKBAU: auf False setzen = Verhalten bis 2.9.4.
# 2.9.6: Anheben nach Nutzer-Idee: Gain = (Unterkante + Einzug) - Mittel der
# letzten 1 s, nur >= 0, mit 1,5 s nachgefuehrt (sofort = 0,1 s pumpte offline:
# 5,6 Atmer/min). Gilt in beide Richtungen, solange nicht abgesenkt ist.
# RUECKBAU ueber den Modus:
#   "mittel"         2.9.6  Anhebung folgt dem 1-s-Mittel auf die Unterkante
#   "nur_bis_unten"  2.9.5  Anhebung wie bisher, zu viel wird in 4 s zurueckgenommen
#   "halten"         bis 2.9.4  Anhebung bleibt stehen, bis die Oberkante erreicht ist
BAND_ANHEBUNG_MODUS = "mittel"
BAND_ANHEBUNG_FENSTER = 1.0      # s, Mittel fuer "mittel"
BAND_TAU_ANHEBUNG = 1.5          # s, Nachfuehrung fuer "mittel"
BAND_ANHEBUNG_TOLERANZ = 1.0     # dB, "nur_bis_unten": ueber dem Anhebe-Ziel, bevor zurueckgenommen wird
BAND_TAU_ANHEBUNG_ZURUECK = 4.0  # s, "nur_bis_unten"
BAND_MAX_ANHEBUNG = 15.0    # Deckel des Aux1-Gains ohne erkannte Sprache (Rauschen bei sehr leisem Material)
BAND_MAX_ANHEBUNG_SPRACHE = 20.0   # Deckel bei erkannter Sprache (1.13.0)
BAND_SC_HP_HZ = 50.0        # Sidechain-Hochpass der Decke: nur Rumpeln unter 50 Hz bleibt draussen (vorher 120: Bass wurde nicht begrenzt)
BAND_GATE_FENSTER = 8.0     # Sekunden gleitende Programmlautheit fuer "zu leise"
BAND_GATE_REL = 10.0        # EBU-relatives Gate: Frames > 10 LU unter dem Mittel zaehlen nicht
BAND_DECKE_MARGE = 1.0      # Decke sitzt 1 dB ueber der Oberkante: der Leveler arbeitet, die Decke faengt nur
BAND_DECKE_ATTACK = 0.002   # offline gemessen: 2 ms + 30 ms Vorlauf -> Einsatz max. ~4 dB / 7 ms ueber Decke
BAND_DECKE_RELEASE = 0.30
BAND_DECKE_FACTOR = 20.0
BAND_VORLAUF_MIN_MS = 5.0
BAND_VORLAUF_MAX_MS = 50.0
BAND_DECKE_NACHFUEHR_DB = 0.5   # Decke neu setzen, wenn die Kalibrierung so weit gewandert ist
BAND_DECKE_NACHFUEHR_SEK = 30.0


# Laufzeitzustand, der nicht in der CamillaDSP-Konfiguration steht.
# PERSISTENZ: settings.json wird beim Start mit `if k in state` gefiltert -
# jeder gespeicherte Schluessel MUSS hier stehen (1.9.28).

state = {
    "agc_aktiv": True,       # Regelung an/aus
    "bypass": False,
    "spread": "normal",
    # Klangkurve (Lautsprecherkorrektur). Ab 2.0.0 eigenstaendig gespeichert,
    # vorher hing sie am Modus - das war die Ursache des "EQ-Reset".
    "eq": [0.0] * 16,
    "kalibrierung": DEFAULT_KALIBRIERUNG,          # Auto-Offset RMS(dB-Mittel) -> LUFS
    "kalibrierung_schnell": DEFAULT_KALIBRIERUNG,  # energiebasiert; die Decke rechnet daraus
    "kalibrierung_schnell_gueltig": False,
    # Haupt-Lautstaerke (CamillaDSP main_volume): reiner Laufzeitwert in
    # CamillaDSP, hier mitgefuehrt und beim Verbinden wiederhergestellt.
    # Default -35 dB, damit es beim ersten Start nicht unerwartet laut ist.
    "volume": -35.0,
    "flirc_step": 1.0,
    "ir_keys": {},
    # Filterform der EQ-Baender: "glatt-1okt" seit 2.8.6 (1 Oktave, Reglerwerte
    # auf eine glatte Linie durch die Punkte gerechnet). Aeltere Staende
    # ("bandbreite" = 2.8.5, fehlend = Q bis 2.8.4) werden beim Start einmalig
    # umgerechnet, die Punkte behalten dabei ihre Wirkung.
    "eq_filter": "glatt-1okt",
    **BAND_DEFAULTS,
    **EINGANG_DEFAULTS,
}


# ------------------------------------------------------------ Lautheitsverlauf
# Live-Diagramm "Eingang vs. geregelt" im Klang-Reiter (1.12.0). Nur im RAM,
# nie auf die SD-Karte. Zwei Aufloesungen (M4-/Min-Max-Dezimierung, wie bei
# Wellenformanzeigen): fein = 10 Hz fuer 2 min, grob = 1-s-Kaestchen mit
# Min/Max/Mittel fuer 30 min. So bleibt jeder Peak sichtbar, und das Handy
# zeichnet hoechstens ~1800 Kaestchen statt 18 000 Punkte.
#   rot  (ein):  K-gewichtete Momentary-Lautheit des Eingangs (ffmpeg), sonst
#                kalibrierter RMS
#   gruen (aus): ein + gemessene Gesamtverstaerkung (playback_rms - capture_rms
#                - Hauptlautstaerke) -> enthaelt Leveler, EQ, Loudness, Decke
#                und Limiter, aber nicht den Lautstaerkeregler
VERLAUF_FEIN_SEK = 120.0
VERLAUF_GROB_SEK = 1800.0
VERLAUF_BODEN = -60.0
_verlauf_fein = deque(maxlen=int(VERLAUF_FEIN_SEK * 10))
_verlauf_grob = deque(maxlen=int(VERLAUF_GROB_SEK))
_verlauf_akk = {"t0": None, "n": 0, "ein": 0.0, "aus": 0.0, "gain": 0.0,
                "sek": None, "sek_n": 0, "ein_min": 0.0, "ein_max": 0.0, "ein_sum": 0.0,
                "aus_min": 0.0, "aus_max": 0.0, "aus_sum": 0.0, "gain_sum": 0.0}
_verlauf_lock = threading.Lock()


def verlauf_aufzeichnen(jetzt, ein_db, aus_db, gain_db):
    """Einen 20-ms-Messwert in die 100-ms- und 1-s-Puffer einsortieren."""
    ein_db = max(VERLAUF_BODEN, float(ein_db))
    aus_db = max(VERLAUF_BODEN, float(aus_db))
    a = _verlauf_akk
    if a["t0"] is None:
        a["t0"] = jetzt
    # Leistungsmittel ueber 100 ms (Energie, nicht dB-Mittel)
    a["n"] += 1
    a["ein"] += 10 ** (ein_db / 10.0)
    a["aus"] += 10 ** (aus_db / 10.0)
    a["gain"] += float(gain_db)
    if jetzt - a["t0"] < 0.1:
        return
    ein = 10.0 * math.log10(max(1e-12, a["ein"] / a["n"]))
    aus = 10.0 * math.log10(max(1e-12, a["aus"] / a["n"]))
    gain = a["gain"] / a["n"]
    a.update(t0=jetzt, n=0, ein=0.0, aus=0.0, gain=0.0)
    with _verlauf_lock:
        _verlauf_fein.append((round(jetzt, 2), round(ein, 1), round(aus, 1), round(gain, 1)))
        # 1-s-Kaestchen: Min/Max/Mittel
        if a["sek"] is None or jetzt - a["sek"] >= 1.0:
            if a["sek"] is not None and a["sek_n"]:
                _verlauf_grob.append((round(a["sek"], 1),
                                      round(a["ein_min"], 1), round(a["ein_max"], 1), round(a["ein_sum"] / a["sek_n"], 1),
                                      round(a["aus_min"], 1), round(a["aus_max"], 1), round(a["aus_sum"] / a["sek_n"], 1),
                                      round(a["gain_sum"] / a["sek_n"], 1)))
            a.update(sek=jetzt, sek_n=0, ein_min=ein, ein_max=ein, ein_sum=0.0,
                     aus_min=aus, aus_max=aus, aus_sum=0.0, gain_sum=0.0)
        a["sek_n"] += 1
        a["ein_min"] = min(a["ein_min"], ein); a["ein_max"] = max(a["ein_max"], ein); a["ein_sum"] += ein
        a["aus_min"] = min(a["aus_min"], aus); a["aus_max"] = max(a["aus_max"], aus); a["aus_sum"] += aus
        a["gain_sum"] += gain


def verlauf_seit(seit_fein, seit_grob):
    with _verlauf_lock:
        fein = [e for e in _verlauf_fein if e[0] > seit_fein]
        grob = [e for e in _verlauf_grob if e[0] > seit_grob]
    return fein, grob


# ------------------------------------------------------------ Spracherkennung
# Silero VAD (MIT-Lizenz, 2,3-MB-ONNX-Modell) ueber onnxruntime. Liefert NUR ein
# Flag "hier wird gesprochen"; der Ton laeuft unveraendert durch CamillaDSP.
# Eigener ffmpeg-Prozess liest tvmess (dritter dsnoop-Leser) als 16 kHz Mono.
# Gemessen (1.11.0): Trailer 53 % Sekunden Sprache, Orchester 0 %, ~5 % eines
# Kerns. Laeuft nur, wenn quelle == band und band_vad an ist. Fehlt
# onnxruntime oder das Modell, verhaelt sich alles wie "aus".
VAD_MODELL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "silero_vad.onnx")
VAD_MODELL_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
VAD_MODELL_MIN_BYTES = 1_000_000
VAD_SR = 16000
VAD_CHUNK = 512              # Samples je Auswertung (32 ms)
VAD_KONTEXT = 64
VAD_SCHWELLE = 0.5
VAD_FENSTER_SEK = 1.0        # Sprache = > 50 % der letzten Sekunde ueber der Schwelle
VAD_HALTEN_SEK = 0.5         # so lange bleibt "Sprache" nach dem letzten Treffer stehen
_vad = {"proc": None, "thread": None, "soll": False, "probs": deque(maxlen=int(VAD_FENSTER_SEK * VAD_SR / VAD_CHUNK) + 2),
        "letzter_treffer": 0.0, "sprache": False, "anteil": 0.0, "fehler": None, "zeit": 0.0}
_vad_lock = threading.Lock()


def vad_verfuegbar():
    """(ok, meldung) - ohne den Ton anzufassen."""
    if not os.path.exists(VAD_MODELL) or os.path.getsize(VAD_MODELL) < VAD_MODELL_MIN_BYTES:
        return False, "Modell fehlt (silero_vad.onnx)"
    try:
        import onnxruntime  # noqa: F401
        import numpy  # noqa: F401
    except Exception as exc:
        return False, "onnxruntime/numpy fehlt: %s" % exc
    return True, "bereit"


def vad_status():
    ok, meldung = vad_verfuegbar()
    with _vad_lock:
        laeuft = _vad["thread"] is not None and _vad["thread"].is_alive() and _vad["soll"]
        frisch = time.monotonic() - _vad["zeit"] < 2.0
        return {"verfuegbar": ok, "meldung": _vad["fehler"] or meldung,
                "aktiv": laeuft and frisch,
                "sprache": bool(_vad["sprache"]) if laeuft and frisch else False,
                "anteil": round(100.0 * _vad["anteil"]) if laeuft and frisch else None,
                "modell_bytes": os.path.getsize(VAD_MODELL) if os.path.exists(VAD_MODELL) else 0}


_hoehen = {"daempfung": 0.0, "anteil": None, "faden": None, "proc": None,
           "geschrieben": None, "zeit": 0.0}
_hoehen_lock = threading.Lock()


def _hoehen_lauf():
    """Misst Gesamt- und Hoehenlautheit (ein ffmpeg, zwei ebur128) und fuehrt
    Aux4 nach. Laeuft nur, solange der Schalter an ist."""
    regler = HoehenRegler()
    muster = re.compile(r"\[Parsed_ebur128_(\d+) @ [^\]]*\] t: *([0-9.]+) .*?M: *(-?[0-9.inf]+)")
    while state.get("hoehen_daempfung"):
        if _audio_idle.is_set():
            time.sleep(0.5)
            continue
        kette = r128_filterkette().rsplit(",ebur128", 1)[0]     # EQ-Spiegel ohne Messung
        vorne = (kette + ",") if kette and not kette.startswith("ebur128") else ""
        k = vorne.count("equalizer=")
        graph = ("[0:a]%sasplit=2[v][h];[v]ebur128=framelog=info[vo];"
                 "[h]highpass=f=%d:p=2,highpass=f=%d:p=2,ebur128=framelog=info[ho]"
                 % (vorne, HOEHEN_FREQ, HOEHEN_FREQ))
        voll_id, hoch_id = str(k + 1), str(k + 4)
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
               "-f", "alsa", "-channels", "2", "-sample_rate", "48000", "-i", mess_geraet(),
               "-filter_complex", graph, "-map", "[vo]", "-f", "null", "-", "-map", "[ho]", "-f", "null", "-"]
        proc = None
        try:
            proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, text=True, bufsize=1)
            with _hoehen_lock:
                _hoehen["proc"] = proc
            log.info("Hoehendaempfung: Messung gestartet (%s)", mess_geraet())
            offen = {}
            for zeile in proc.stderr:
                if not state.get("hoehen_daempfung") or _audio_idle.is_set():
                    break
                t = muster.search(zeile)
                if not t:
                    continue
                inst, zeit = t.group(1), t.group(2)
                try:
                    m = float(t.group(3))
                except ValueError:
                    m = -120.0
                eintrag = offen.setdefault(zeit, {})
                eintrag["v" if inst == voll_id else "h" if inst == hoch_id else "x"] = m
                if "v" in eintrag and "h" in eintrag:
                    del offen[zeit]
                    for alt in [z for z in offen if float(z) < float(zeit)]:
                        del offen[alt]
                    d = regler.schritt(eintrag["v"], eintrag["h"])
                    _hoehen.update(daempfung=d, anteil=regler.anteil, zeit=time.monotonic())
                    hoehen_fader_schreiben()
        except Exception as exc:
            log.warning("Hoehendaempfung: Messung unterbrochen: %s", exc)
        finally:
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            with _hoehen_lock:
                _hoehen["proc"] = None
        time.sleep(1.0)
    _hoehen.update(daempfung=0.0, anteil=None)
    hoehen_fader_schreiben(immer=True)
    log.info("Hoehendaempfung: aus")


def hoehen_fader_schreiben(immer=False):
    """Aux4 nur bei Aenderung > 0,2 dB (bzw. alle 5 s) schreiben."""
    wert = hoehen_fader_db(_hoehen["daempfung"]) if state.get("hoehen_daempfung") else HOEHEN_AUS_DB
    alt = _hoehen["geschrieben"]
    jetzt = time.monotonic()
    if not immer and alt is not None and abs(wert - alt) < 0.2 and jetzt - _hoehen.get("schreibzeit", 0.0) < 5.0:
        return
    try:
        with lock:
            cdsp.volume.set_volume(HOEHEN_FADER, wert)
        _hoehen["geschrieben"] = wert
        _hoehen["schreibzeit"] = jetzt
    except Exception as exc:
        log.debug("Aux4 nicht gesetzt: %s", exc)


def hoehen_nachziehen():
    """Messfaden passend zum Schalter starten (er beendet sich selbst)."""
    faden = _hoehen.get("faden")
    if state.get("hoehen_daempfung") and not (faden and faden.is_alive()):
        faden = threading.Thread(target=_hoehen_lauf, daemon=True, name="hoehen")
        _hoehen["faden"] = faden
        faden.start()
    elif not state.get("hoehen_daempfung"):
        with _hoehen_lock:
            proc = _hoehen.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass


def vad_neustart():
    """Laufendes VAD-ffmpeg beenden; die Schleife startet es mit dem aktuellen
    Eingang neu (Eingangswechsel ab 3.0.0)."""
    with _vad_lock:
        proc = _vad.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def vad_sprache():
    """True, wenn gerade Sprache erkannt wird (frisch, laufend)."""
    with _vad_lock:
        return (_vad["soll"] and _vad["sprache"]
                and time.monotonic() - _vad["zeit"] < 2.0)


def _vad_lauf():
    import numpy as np
    import onnxruntime as ort
    try:
        sess = ort.InferenceSession(VAD_MODELL, providers=["CPUExecutionProvider"])
    except Exception as exc:
        with _vad_lock:
            _vad["fehler"] = "Modell nicht ladbar: %s" % exc
        return
    while True:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
               "-f", "alsa", "-channels", "2", "-sample_rate", "48000", "-i", mess_geraet(),
               "-ac", "1", "-ar", str(VAD_SR), "-f", "s16le", "pipe:1"]
        with _vad_lock:
            if not _vad["soll"]:
                return
            _vad["fehler"] = None
        if _audio_idle.is_set():
            time.sleep(0.5)
            continue
        proc = None
        try:
            err_datei = open(os.path.join(STATE_DIR, "vad-ffmpeg.err"), "w")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_datei,
                                    stdin=subprocess.DEVNULL, bufsize=0)
            with _vad_lock:
                _vad["proc"] = proc
            state_t = np.zeros((2, 1, 128), dtype=np.float32)
            kontext = np.zeros(VAD_KONTEXT, dtype=np.float32)
            sr = np.array(VAD_SR, dtype=np.int64)
            bedarf = VAD_CHUNK * 2
            log.info("VAD gestartet (Silero, %s)", mess_geraet())
            while True:
                with _vad_lock:
                    if not _vad["soll"]:
                        break
                if _audio_idle.is_set():
                    break
                puffer = b""
                while len(puffer) < bedarf:
                    teil = proc.stdout.read(bedarf - len(puffer))
                    if not teil:
                        try:
                            err_datei.flush()
                            with open(os.path.join(STATE_DIR, "vad-ffmpeg.err")) as fh:
                                grund = " | ".join(fh.read().strip().splitlines()[-2:])
                        except Exception:
                            grund = ""
                        raise RuntimeError("ffmpeg-VAD liefert keine Daten%s" % ((": " + grund) if grund else ""))
                    puffer += teil
                chunk = np.frombuffer(puffer, dtype=np.int16).astype(np.float32) / 32768.0
                x = np.concatenate([kontext, chunk])[None, :]
                out = sess.run(None, {"input": x, "state": state_t, "sr": sr})
                prob = float(np.asarray(out[0]).ravel()[0])
                state_t = np.asarray(out[1], dtype=np.float32)
                kontext = chunk[-VAD_KONTEXT:]
                jetzt = time.monotonic()
                with _vad_lock:
                    _vad["probs"].append(prob)
                    anteil = sum(1 for q in _vad["probs"] if q > VAD_SCHWELLE) / max(1, len(_vad["probs"]))
                    _vad["anteil"] = anteil
                    if anteil > 0.5:
                        _vad["letzter_treffer"] = jetzt
                    _vad["sprache"] = jetzt - _vad["letzter_treffer"] < VAD_HALTEN_SEK
                    _vad["zeit"] = jetzt
        except Exception as exc:
            with _vad_lock:
                _vad["fehler"] = str(exc)
            log.warning("VAD unterbrochen: %s", exc)
            time.sleep(3)
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            with _vad_lock:
                _vad["proc"] = None
                _vad["sprache"] = False
                _vad["anteil"] = 0.0
                _vad["probs"].clear()


def vad_setzen(an):
    """VAD-Thread starten/stoppen; idempotent."""
    ok, _ = vad_verfuegbar()
    an = bool(an) and ok
    with _vad_lock:
        _vad["soll"] = an
        laeuft = _vad["thread"] is not None and _vad["thread"].is_alive()
        if an and not laeuft:
            _vad["thread"] = threading.Thread(target=_vad_lauf, daemon=True, name="vad")
            _vad["thread"].start()
        proc = _vad["proc"] if not an else None
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass


def vad_modell_laden():
    """Modell von GitHub holen (nur auf ausdruecklichen Wunsch, Knopf im Reiter)."""
    import urllib.request
    tmp = VAD_MODELL + ".tmp"
    req = urllib.request.Request(VAD_MODELL_URL, headers={"User-Agent": "tvdsp"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        shutil.copyfileobj(r, fh)
    if os.path.getsize(tmp) < VAD_MODELL_MIN_BYTES:
        os.remove(tmp)
        raise RuntimeError("Download unvollstaendig")
    import onnxruntime as ort
    ort.InferenceSession(tmp, providers=["CPUExecutionProvider"])   # muss ladbar sein
    os.replace(tmp, VAD_MODELL)
    return os.path.getsize(VAD_MODELL)

messung = {
    "lautheit": None,        # gehoerte Lautheit (Momentary + Gain)
    "lra": None,
    "aus_wert": None,
    "r128_wert": None,       # S, sonst M (Anzeige)
    "r128_m": None,          # Momentary, 400 ms
    "r128_s": None,          # Short-Term, 3 s
    "r128_m_raw": None,
    "r128_s_raw": None,
    "r128_lra": None,
    "r128_i": None,
    "r128_aktiv": False,
    "r128_zeit": 0.0,        # monotonic-Zeit der letzten echten M/S-Zeile
    "regelquelle": "band",  # band / band-halten / aus / bypass / stille
    "agc_gain": 0.0,
    "agc_anschlag": False,
    "in_peak": -99.0,
    "out_peak": -99.0,
    "rate": 0,
    "status": "unbekannt",
    "latenz": None,
    "chunksize": None,
    "idle": False,          # nach 3 s Stille: Pegelabfrage nur noch 1 Hz
    "messintervall_ms": 20,
    "band": None,           # Live-Werte des Band-Reglers
}

# Zwischengespeicherte Geraetedaten, damit die Regelschleife nicht
# staendig die komplette Konfiguration abfragen muss.
geraet = {"chunk": 1024, "rate": 48000, "gelesen": 0.0}

# Die Haupt-Regelschleife laeuft passend zum 1024er CamillaDSP-Chunk
# (21,3 ms bei 48 kHz) mit 20 ms. ffmpeg liefert alle 100 ms einen neuen
# M/S-Frame; die Zwischenzyklen fahren die Huelle weiter.
REGEL_INTERVALL = 0.020
# Nach 3 s echter Stille wird nicht mehr 50-mal/s gepollt. Ein 1-Hz-
# Lebenszeichen reicht, um einen wiederkehrenden Toslink-Pegel zu erkennen.
IDLE_NACH_SEK = 3.0
IDLE_INTERVALL = 1.0
REGEL_DT_MAX = 0.050       # Aussetzer/Stille nie als Regelzeit nachholen
KALIB_FENSTER_SEK = 3.0    # feste 3-s-Basis der RMS<->LUFS-Kalibrierung (wie R128-S)
LRA_FENSTER_SEK = 120.0
LRA_MIN_SEK = 10.0
GAIN_WRITE_EPS = 0.005
AGC_MAX = 35.0             # Regelbereich des Aux1-Faders in dB
# Eine Tonluecke ab dieser Laenge gilt als Programm-/Senderwechsel (die
# Programm-Historie wird verworfen); kuerzere sind Sprechpausen.
LUECKE_GATE = 0.4

_roh_fenster = deque(maxlen=round(KALIB_FENSTER_SEK / REGEL_INTERVALL) + 5)
_lra_fenster = deque(maxlen=round(LRA_FENSTER_SEK / REGEL_INTERVALL))
_kalib_kandidaten = deque(maxlen=50)  # 5 s bei echten ~100-ms-R128-Frames
_kalib_schnell_kandidaten = deque(maxlen=50)
_kalib_schnell_gueltig = {"ok": False}
LRA_MIN_SAMPLES = round(LRA_MIN_SEK / REGEL_INTERVALL)


def _median(werte):
    sortiert = sorted(float(x) for x in werte)
    n = len(sortiert)
    if not n:
        return None
    mitte = n // 2
    return sortiert[mitte] if n % 2 else (sortiert[mitte-1] + sortiert[mitte]) / 2.0



# ------------------------------------------------------------ R128-Messung
#
# ffmpeg laeuft als Messgeraet, nicht als Filter. Es liest denselben
# ALSA-Eingang parallel mit (dsnoop erlaubt mehrere Leser) und gibt
# fortlaufend K-gewichtete Werte aus.
#
# Wichtig zum Verstaendnis: loudnorm als Filter im Signalweg wuerde rund
# drei Sekunden Latenz kosten, weil es mit Lookahead arbeitet. Als reines
# Messwerk entsteht keinerlei Verzoegerung im Ton - wir lesen nur mit.
#
# "tvmess" ist ein plug-Wrapper um dsnoop_tv (siehe /etc/asound.conf).
# Direkt auf dsnoop_tv wuerde ffmpeg scheitern, weil dort keine
# Formatumsetzung stattfindet und ffmpeg ein eigenes Format aushandelt.

R128_DEV = os.environ.get("R128_DEV", "tvmess")


# ---------------------------------------------------------------- USB-Eingang
# Ab 3.0.0. bin/tvdsp-quelle.py liest den UR27, decodiert Dolby/DTS, mischt
# 5.1 auf Stereo und schreibt fertiges Stereo in den Loopback. Von dort liest
# die ganze bestehende Kette ueber dsnoop_usb / tvmess_usb - wie sonst von der
# HiFiBerry. Normalisierung, Decke, EQ und Limiter wirken also NACH dem Mix.
# Der UR27 laeuft mit der Uhr des Fernsehers, die HiFiBerry mit ihrer eigenen:
# im USB-Betrieb gleicht CamillaDSP das mit dem AsyncSinc-Resampler aus.
# Nie stumm: fehlt der UR27 oder meldet sich die Quelle nicht (Status aelter
# als QUELLE_FRISCH_SEK), liest CamillaDSP wieder die HiFiBerry.
QUELLE_KONFIG = os.path.join(STATE_DIR, "quelle.json")
QUELLE_STATUS = os.environ.get("TVDSP_QUELLE_STATUS", "/run/tvdsp/quelle-status.json")   # RAM (3.3.6)
# USB-Empfaenger (gleiche USB-IDs wie USB_GERAETE in bin/tvdsp-quelle.py)
USB_EMPFAENGER = {"2752:0019": "miniDSP MCHStreamer", "262a:9227": "Hifime UR27"}
EINGANG_GERAETE = {             # Eingang -> (CamillaDSP-Capture, ffmpeg-Messung/Aufnahme)
    "hifiberry": ("dsnoop_tv", R128_DEV),
    "usb": ("dsnoop_usb", "tvmess_usb"),
}
QUELLE_FRISCH_SEK = 5.0
_eingang = {"aktiv": "hifiberry"}          # was CamillaDSP gerade liest
_quelle_cache = {"zeit": 0.0, "wert": {}}


def mix_normiert(mix):
    """Vier Anteile >= 0 mit Summe 1; Unbrauchbares -> Standard."""
    try:
        w = {k: max(0.0, float(mix.get(k, 0.0))) for k in ("front", "center", "lfe", "surround")}
    except (AttributeError, TypeError, ValueError):
        return dict(MIX_VOREINSTELLUNGEN[MIX_STANDARD])
    summe = sum(w.values())
    if summe <= 1e-9:
        return dict(MIX_VOREINSTELLUNGEN[MIX_STANDARD])
    return {k: round(v / summe, 4) for k, v in w.items()}


def quelle_konfig_schreiben():
    """Eingang und Mix fuer tvdsp-quelle (liest die Datei 1x/s)."""
    _save(QUELLE_KONFIG, {"eingang": state.get("eingang", "hifiberry"),
                          "mix": mix_normiert(state.get("mix", {}))})


def usb_empfaenger():
    """Name des gesteckten USB-Empfaengers (miniDSP vor UR27) oder None."""
    gesteckt = set()
    for pfad in glob.glob("/proc/asound/card*/usbid"):
        try:
            with open(pfad) as f:
                gesteckt.add(f.read().strip())
        except OSError:
            pass
    for usbid, name in USB_EMPFAENGER.items():
        if usbid in gesteckt:
            return name
    return None


def quelle_status():
    """Status von tvdsp-quelle (0,5 s gecacht) plus 'frisch'."""
    jetzt = time.monotonic()
    if jetzt - _quelle_cache["zeit"] > 0.5:
        d = _load(QUELLE_STATUS, {})
        if not isinstance(d, dict):
            d = {}
        d["frisch"] = time.time() - float(d.get("zeit") or 0.0) < QUELLE_FRISCH_SEK
        d["geraet"] = usb_empfaenger()
        _quelle_cache.update(zeit=jetzt, wert=d)
    return _quelle_cache["wert"]


def eingang_soll():
    """Welcher Eingang jetzt gelesen werden soll (mit Rueckfall)."""
    if state.get("eingang") == "usb":
        st = quelle_status()
        if st.get("frisch") and st.get("laeuft") and st.get("geraet"):
            return "usb"
    return "hifiberry"


def mess_geraet():
    """ALSA-Geraet fuer ffmpeg (Messung, Spracherkennung, Aufnahme)."""
    return EINGANG_GERAETE[_eingang["aktiv"]][1]


def eingang_in_konfig(cfg, eingang):
    """Capture-Geraet und Taktausgleich in eine CamillaDSP-Konfiguration."""
    d = cfg["devices"]
    d["capture"]["device"] = EINGANG_GERAETE[eingang][0]
    if eingang == "usb":
        d["enable_rate_adjust"] = True
        d["resampler"] = {"type": "AsyncSinc", "profile": "Balanced"}
    else:
        d["enable_rate_adjust"] = False
        d["resampler"] = None


def eingang_umschalten(neu, grund):
    """CamillaDSP, Messung und Spracherkennung auf den neuen Eingang (unter lock)."""
    alt = _eingang["aktiv"]
    _eingang["aktiv"] = neu
    band_anwenden(neutral=bool(state.get("bypass")))
    r128_eq_neu_laden(0.5)          # ffmpeg-Messung auf dem neuen Geraet neu starten
    vad_neustart()
    with _hoehen_lock:
        hp = _hoehen.get("proc")
    if hp is not None and hp.poll() is None:
        hp.terminate()              # Hoehenmessung startet mit dem neuen Geraet neu
    log.info("Eingang: %s -> %s (%s)", alt, neu, grund)
_r128_re = re.compile(
    r"M:\s*(-?[\d.]+)\s+S:\s*(-?[\d.]+)\s+I:\s*(-?[\d.]+)\s*LUFS\s+LRA:\s*(-?[\d.]+)")

# Der ffmpeg-Messpfad soll dieselbe Klangkurve sehen wie der Nutzpfad.
# Ein EQ-Zug kann viele HTTP-Updates erzeugen; deshalb wird der ffmpeg-
# Prozess erst 350 ms nach der letzten Aenderung neu gestartet.
_r128_eq_lock = threading.Lock()
_r128_eq_revision = 0
_r128_eq_neustart_ab = 0.0

# Im Idle soll nicht nur die Python-Regelschleife langsamer laufen:
# ffmpeg/ebur128 selbst wuerde sonst weiter mit 10 Hz rechnen. Der aktuelle
# Prozess wird beim Eintritt in Idle beendet und erst beim Aufwachen neu
# gestartet. Die Wiedergabe ist davon unabhaengig; RMS steht als Fallback.
_audio_idle = threading.Event()
_r128_proc_lock = threading.Lock()
_r128_proc = None


def audio_idle_setzen(aktiv):
    """Idle-Zustand umschalten und einen laufenden R128-Prozess stoppen."""
    global _r128_proc
    aktiv = bool(aktiv)
    war = _audio_idle.is_set()
    if aktiv == war:
        return
    if aktiv:
        _audio_idle.set()
        messung["idle"] = True
        messung["messintervall_ms"] = int(IDLE_INTERVALL * 1000)
        messung["r128_aktiv"] = False
        messung["r128_zeit"] = 0.0
        with _r128_proc_lock:
            proc = _r128_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        log.info("Audio-Idle: nach %.1f s Stille auf %.1f Hz, R128 pausiert",
                 IDLE_NACH_SEK, 1.0 / IDLE_INTERVALL)
    else:
        _audio_idle.clear()
        messung["idle"] = False
        messung["messintervall_ms"] = int(REGEL_INTERVALL * 1000)
        log.info("Audio wieder da: Regelschleife 50 Hz, R128 startet neu")


def r128_eq_neu_laden(verzoegerung=0.35):
    global _r128_eq_revision, _r128_eq_neustart_ab
    with _r128_eq_lock:
        _r128_eq_revision += 1
        _r128_eq_neustart_ab = time.monotonic() + max(0.0, verzoegerung)
    # R128 misst NACH der gespiegelten EQ-Kurve, der Capture-RMS dagegen
    # davor. Nach jeder EQ-Aenderung muss deshalb deren gemeinsamer Offset
    # (kalibrierung_schnell, Grundlage der Decke) neu bestimmt werden. Bis
    # dahin rechnet die Decke mit der normalen Kalibrierung.
    _kalib_schnell_kandidaten.clear()
    _kalib_schnell_gueltig["ok"] = False
    state["kalibrierung_schnell_gueltig"] = False


def _r128_eq_status():
    with _r128_eq_lock:
        return _r128_eq_revision, _r128_eq_neustart_ab


# Startet die Messung vor der Verbindung zu CamillaDSP, laeuft sie zunaechst
# ohne EQ. verbinder() startet sie dann einmal neu (2.9.0). Bis 2.8.8 geschah
# das nur, wenn sich der EQ in CamillaDSP gerade aenderte - nach einem Neustart
# NUR des Backends (Ausrollen) mass sie bis zur naechsten Tonpause ohne EQ.
_r128_eq = {"flach": False}


def r128_filterkette():
    """FFmpeg-Filter fuer die reine Messung bauen.

    Es werden nur EQ-Baender ungleich 0 dB gespiegelt. Die eigentliche
    Wiedergabe bleibt vollstaendig in CamillaDSP; ffmpeg gibt nichts aus
    und fuegt dem Ton daher keine Latenz hinzu.
    """
    try:
        # Nicht mit dem ersten CamillaDSP-Verbindungsaufbau konkurrieren.
        # Sobald verbinder() den Startmodus gesetzt hat, fordert
        # band_anwenden() ohnehin einen EQ-Neustart an.
        if not verbindung.get("bereit"):
            raise RuntimeError("CamillaDSP noch nicht bereit")
        with lock:
            cfg = aktive_konfiguration()
        teile = []
        for freq, fname in zip(BANDS, EQ_NAMES):
            par = cfg["filters"][fname]["parameters"]
            gain = float(par.get("gain", 0.0))
            if abs(gain) < 0.005:
                continue
            # Gleicher Filter wie in CamillaDSP: Bandbreite in Oktaven
            # (ffmpeg rechnet dann ebenfalls mit der Vorverzerrung, 2.8.5).
            bw = par.get("bandwidth")
            if bw is None:
                bw = eq_bandbreite(par.get("q", SPREAD[state["spread"]]["q"]))
            teile.append(
                "equalizer=f=%s:width_type=o:width=%.6f:g=%+.4f" %
                (freq, float(bw), gain))
    except Exception as exc:
        # Beim sehr fruehen Start kann CamillaDSP noch nicht bereit sein.
        # Dann lieber R128 ohne EQ starten; band_anwenden() fordert danach
        # automatisch einen Neustart mit der echten Kurve an.
        log.info("R128-EQ noch nicht lesbar, starte vorlaeufig flach: %s", exc)
        teile = []
        _r128_eq["flach"] = True
    else:
        _r128_eq["flach"] = False
    teile.append("ebur128=framelog=info")
    return ",".join(teile)


def r128_leser():
    """ffmpeg/ebur128 als Nebenlaeufer.

    M (Momentary, 400 ms) und S (Short-Term, 3 s) werden mit 10 Hz
    uebernommen. Anzeige und Regelung verwenden seit 1.6.0 direkt die
    echten LUFS-Werte; einen kuenstlichen R128-Anzeigeoffset gibt es nicht
    mehr.

    Nach drei Sekunden Eingangsstille setzt die Regelschleife _audio_idle.
    Dann wird ffmpeg komplett beendet, damit im TV-Standby keine R128-CPU-
    Last uebrig bleibt. Beim ersten wiedergefundenen Signal wird der Prozess
    neu gestartet; bis S wieder gueltig ist, bleibt RMS der Fallback.
    """
    global _r128_proc
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg fehlt - R128-Messung nicht verfuegbar")
        return

    while True:
        if _audio_idle.is_set():
            messung["r128_aktiv"] = False
            messung["r128_zeit"] = 0.0
            # Kurzes Warten statt 5-s-Restartpause: nach dem Aufwachen soll
            # R128 schnell wieder anlaufen. 200 ms sind im Idle bedeutungslos.
            while _audio_idle.is_set():
                time.sleep(0.2)
            continue

        revision, _ = _r128_eq_status()
        filterkette = r128_filterkette()
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
               "-f", "alsa", "-channels", "2", "-sample_rate", "48000",
               "-i", mess_geraet(), "-af", filterkette, "-f", "null", "-"]
        eq_neustart = False
        proc = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True,
                                    bufsize=1)
            with _r128_proc_lock:
                _r128_proc = proc
            log.info("R128-Messung gestartet auf %s (%s)", mess_geraet(),
                     "vorlaeufig ohne EQ" if _r128_eq["flach"] else "EQ gespiegelt")
            for zeile in proc.stderr:
                # audio_idle_setzen() terminiert den Prozess von der
                # Regelschleife aus. Diese Pruefung deckt den normalen Fall
                # ab, in dem noch eine letzte stderr-Zeile ankommt.
                if _audio_idle.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break

                m = _r128_re.search(zeile)
                if m:
                    momentary = float(m.group(1))
                    shortterm = float(m.group(2))
                    integriert = float(m.group(3))
                    lra = float(m.group(4))
                    messung["r128_m_raw"] = round(momentary, 2)
                    messung["r128_s_raw"] = round(shortterm, 2)
                    messung["r128_m"] = round(momentary, 1)
                    messung["r128_s"] = (None if shortterm <= -70 else
                                         round(shortterm, 1))
                    messung["r128_wert"] = (messung["r128_s"] if
                                             messung["r128_s"] is not None
                                             else messung["r128_m"])
                    messung["r128_i"] = round(integriert, 1)
                    messung["r128_lra"] = round(lra, 1)
                    messung["r128_aktiv"] = momentary > -70
                    messung["r128_zeit"] = time.monotonic()

                neu, neustart_ab = _r128_eq_status()
                if neu != revision and time.monotonic() >= neustart_ab:
                    eq_neustart = True
                    proc.terminate()
                    break

            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except Exception as exc:
            if not _audio_idle.is_set():
                log.warning("R128-Messung abgebrochen: %s", exc)
        finally:
            with _r128_proc_lock:
                if _r128_proc is proc:
                    _r128_proc = None
        messung["r128_aktiv"] = False
        messung["r128_zeit"] = 0.0
        if _audio_idle.is_set():
            continue
        if eq_neustart:
            time.sleep(0.05)
        else:
            time.sleep(5)


# ------------------------------------------------------------------ Helfer

def clamp(name, value):
    lo, hi = LIMITS[name]
    return max(lo, min(hi, float(value)))


def r128_frisch(jetzt=None):
    """Nur frische FFmpeg-Frames duerfen die Regelung steuern.

    Ein abgestuerzter Prozess setzt r128_aktiv beim Verlassen zurueck.
    Ein haengender Prozess ist schwieriger: dann bleibt der letzte Wert
    stehen. Der Zeitstempel sorgt dafuer, dass nach spaetestens 0,8 s
    automatisch der parallel weiterlaufende RMS-Pfad uebernimmt.
    """
    if jetzt is None:
        jetzt = time.monotonic()
    ts = float(messung.get("r128_zeit") or 0.0)
    return (messung.get("r128_aktiv", False) and ts > 0.0 and
            jetzt - ts <= float(state.get("r128_frisch_max", 0.8)))

def band_oben():
    try:
        return max(-40.0, min(-8.0, float(state.get("band_oben", -22.0))))
    except (TypeError, ValueError):
        return -22.0


def band_unten():
    try:
        return min(band_oben() - 1.0, max(-45.0, float(state.get("band_unten", -26.0))))
    except (TypeError, ValueError):
        return -26.0


def band_vorlauf_aktiv():
    return bool(state.get("band_vorlauf", True))


def band_vorlauf_ms():
    try:
        return max(BAND_VORLAUF_MIN_MS, min(BAND_VORLAUF_MAX_MS, float(state.get("band_vorlauf_ms", 30.0))))
    except (TypeError, ValueError):
        return 30.0


def band_rauschsperre():
    try:
        return max(-60.0, min(-20.0, float(state.get("band_rauschsperre", -40.0))))
    except (TypeError, ValueError):
        return -40.0


def band_einzug():
    return BAND_EINZUG_ANTEIL * (band_oben() - band_unten())


def band_vad_aktiv():
    return bool(state.get("band_vad", False))


def band_vad_nachziehen():
    """VAD laeuft genau dann, wenn band_vad an ist (und kein Bypass)."""
    try:
        vad_setzen(band_vad_aktiv() and not state.get("bypass"))
    except Exception as exc:
        log.warning("VAD nicht umgeschaltet: %s", exc)


def band_loudness_aktiv():
    return bool(state.get("band_loudness", True))


def band_loudness_staerke():
    try:
        return max(0.0, min(2.0, float(state.get("band_loudness_staerke", 70.0)) / 100.0))
    except (TypeError, ValueError):
        return 1.0


def band_loudness_db(anteil):
    """Aux3-Faderwert (dB) fuer einen Anteil 0..1 der Kompensationskurve."""
    a = max(0.0, min(1.0, float(anteil)))
    if a < 1e-3:
        return BAND_LD_AUS_DB
    return max(BAND_LD_AUS_DB, 20.0 * math.log10(a))


def band_settings_dict():
    return {
        "oben": band_oben(), "unten": band_unten(),
        "vorlauf": band_vorlauf_aktiv(), "vorlauf_ms": band_vorlauf_ms(),
        "rauschsperre": band_rauschsperre(),
        "loudness": band_loudness_aktiv(),
        "loudness_staerke": round(100.0 * band_loudness_staerke(), 0),
        "vad": band_vad_aktiv(),
        "vad_status": vad_status(),
    }


def set_loudness_fader(db):
    """Aux3 setzen (Fader 3). Nur im Band-Zweig in der Pipeline vorhanden."""
    db = max(BAND_LD_AUS_DB, min(0.0, float(db)))
    ensure_connected()
    cdsp.volume.set_volume(3, db)


def band_programmlautheit(hist):
    """Gleitende, gegatete Programmlautheit aus den 10-Hz-M-Frames.

    EBU-R128-Logik im Kleinen: absolutes Gate -70, dann relatives Gate 10 LU
    unter dem ungegateten Mittel. Dialogpausen, Atmosphaeren und Ausklingen
    ziehen den Wert dadurch NICHT nach unten - nur ein wirklich leiseres
    Programm tut das. Das ist die Anti-Pump-Massnahme des Band-Zweigs.
    """
    werte = [m for _, m in hist if m > -70.0]
    if len(werte) < 10:            # mindestens ~1 s echtes Signal
        return None
    energie = [10 ** (m / 10.0) for m in werte]
    ungated = 10.0 * math.log10(sum(energie) / len(energie))
    gate = ungated - BAND_GATE_REL
    sel = [e for e, m in zip(energie, werte) if m >= gate]
    if not sel:
        return None
    return 10.0 * math.log10(sum(sel) / len(sel))


def band_mittel(hist, sek=BAND_MITTEL_SEK):
    """Energiemittel der letzten `sek` Sekunden Momentary (Aufwaerts-Bezug)."""
    if not hist:
        return None
    t_ende = hist[-1][0]
    werte = [m for t, m in hist if t_ende - t <= sek and m > -70.0]
    if len(werte) < 5:              # mindestens ~0,5 s echtes Signal
        return None
    return 10.0 * math.log10(sum(10 ** (m / 10.0) for m in werte) / len(werte))


def band_decke_dbfs():
    """Absolute Kompressorschwelle in dBFS fuer die Oberkante.

    Der Kompressor sitzt HINTER Aux1, sein Eingang enthaelt also den Leveler-
    Gain: die Schwelle ist eine feste Ausgangsgrenze. Umrechnung LUFS -> dBFS
    ueber die automatische Kalibrierung (energiebasiert, wenn gueltig). Der
    feste Vorpegel liegt ebenfalls vor dem Kompressor und wird eingerechnet.
    """
    kal = float(state.get("kalibrierung_schnell", state.get("kalibrierung", 0.0))
                if state.get("kalibrierung_schnell_gueltig")
                else state.get("kalibrierung", 0.0))
    vor = float(geraet.get("vorpegel", -6.0) or -6.0)
    thr = (band_oben() + BAND_DECKE_MARGE - kal + vor
           + BAND_DECKE_BEZUG_EFFEKTE + BAND_DECKE_DETEKTOR_DB)
    return max(-100.0, min(-3.0, thr))


_band_decke = {"dbfs": None, "kal": None, "zeit": 0.0}


def eq_bandbreite(q):
    """Bandbreite in Oktaven, die einem RBJ-Glockenfilter mit Guete q entspricht."""
    q = max(0.05, float(q))
    return 2.0 / math.log(2.0) * math.asinh(1.0 / (2.0 * q))


def eq_band_setzen(par, gain):
    """Parameter eines EQ-Bandes setzen (CamillaDSP Biquad Peaking).

    Seit 2.8.5 mit Bandbreite in Oktaven statt Q. Dann rechnen CamillaDSP
    und ffmpeg mit der Vorverzerrung der Bandbreite (w0/sin w0). Mit Q wird
    die Glocke nahe 24 kHz gestaucht: zwei Punkte bei 10 und 16 kHz mit je
    -3 dB ergaben dazwischen nur -1,5 dB - ein Bogen, den man hoerte und den
    die Klangkurve richtig zeigte. Im Mittelton (1/1,6 kHz) ergab dieselbe
    Einstellung glatt -3,0 dB. Mit Bandbreite: -3,2 dB auch oben.
    """
    par["gain"] = gain
    par.pop("q", None)
    par["bandwidth"] = EQ_BANDBREITE


def _peaking_db(f, f0, gain, q, bandbreite, fs=48000.0):
    """Betrag eines Glockenfilters in dB - mit Q (bis 2.8.4) oder Bandbreite."""
    if abs(gain) < 0.001:
        return 0.0
    A = 10.0 ** (gain / 40.0)
    w0 = 2.0 * math.pi * f0 / fs
    if bandbreite:
        alpha = math.sin(w0) * math.sinh(math.log(2.0) / 2.0 * eq_bandbreite(q) * w0 / math.sin(w0))
    else:
        alpha = math.sin(w0) / (2.0 * q)
    b = (1.0 + alpha * A, -2.0 * math.cos(w0), 1.0 - alpha * A)
    a = (1.0 + alpha / A, -2.0 * math.cos(w0), 1.0 - alpha / A)
    w = 2.0 * math.pi * f / fs
    z1 = complex(math.cos(w), -math.sin(w))
    z2 = z1 * z1
    return 20.0 * math.log10(abs((b[0] + b[1] * z1 + b[2] * z2) / (a[0] + a[1] * z1 + a[2] * z2)))


EQ_BANDBREITE = 1.0       # Oktaven, fest fuer alle 16 Baender (2.8.6)
_EQ_TEILE = 12            # Rasterpunkte je Bandabstand fuer den Glatt-Loeser
_EQ_PUNKTGEWICHT = 30.0
_EQ_DAEMPFUNG = 0.02


def _band_db_bw(f, f0, gain, bw, fs=48000.0):
    """Glockenfilter mit Bandbreite bw (Oktaven) inkl. Vorverzerrung, in dB."""
    if abs(gain) < 1e-6:
        return 0.0
    A = 10.0 ** (gain / 40.0)
    w0 = 2.0 * math.pi * f0 / fs
    alpha = math.sin(w0) * math.sinh(math.log(2.0) / 2.0 * bw * w0 / math.sin(w0))
    c = -2.0 * math.cos(w0)
    w = 2.0 * math.pi * f / fs
    z1 = complex(math.cos(w), -math.sin(w))
    z2 = z1 * z1
    zaehler = (1.0 + alpha * A) + c * z1 + (1.0 - alpha * A) * z2
    nenner = (1.0 + alpha / A) + c * z1 + (1.0 - alpha / A) * z2
    return 20.0 * math.log10(abs(zaehler / nenner))


def _pchip(xk, yk, x):
    """Monotone kubische Interpolation (Fritsch-Carlson): beult zwischen zwei
    Stuetzpunkten nie ueber deren Verbindung hinaus aus."""
    n = len(xk)
    h = [xk[k + 1] - xk[k] for k in range(n - 1)]
    d = [(yk[k + 1] - yk[k]) / h[k] for k in range(n - 1)]
    m = [0.0] * n
    for k in range(1, n - 1):
        if d[k - 1] * d[k] > 0:
            w1 = 2 * h[k] + h[k - 1]
            w2 = h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    m[0] = d[0] if d[0] * d[0] > 0 else 0.0
    m[n - 1] = d[n - 2]
    if d[0] * m[0] <= 0:
        m[0] = 0.0
    if d[n - 2] * m[n - 1] <= 0:
        m[n - 1] = 0.0
    i = 0
    while i < n - 2 and x > xk[i + 1]:
        i += 1
    t = (x - xk[i]) / h[i]
    h00 = 2 * t ** 3 - 3 * t ** 2 + 1
    h10 = t ** 3 - 2 * t ** 2 + t
    h01 = -2 * t ** 3 + 3 * t ** 2
    h11 = t ** 3 - t ** 2
    return h00 * yk[i] + h10 * h[i] * m[i] + h01 * yk[i + 1] + h11 * h[i] * m[i + 1]


def eq_glatt_loesen(punkte, start=None, iterationen=40):
    """Reglerwerte der 16 Baender, deren echte Summe einer glatten Linie durch
    die 16 Punkte folgt (2.8.6).

    Die Linie ist die monotone Interpolation der Punktwerte; zwischen zwei
    Punkten beult sie nie aus. Gewichtete kleinste Quadrate auf 12 Raster-
    punkten je Bandabstand (Punkte selbst 30-fach gewichtet), Gauss-Newton mit
    Daempfung. Gleiches Verfahren wie eqGlattLoesen() in index.html.
    """
    xk = [math.log(f) for f in BANDS]
    xs = []
    for k in range(15):
        for t in range(_EQ_TEILE):
            xs.append(xk[k] + (xk[k + 1] - xk[k]) * t / _EQ_TEILE)
    xs.append(xk[15])
    fs_ = [math.exp(x) for x in xs]
    gew = [(_EQ_PUNKTGEWICHT if j % _EQ_TEILE == 0 else 1.0) for j in range(len(xs))]
    ziel = [_pchip(xk, [float(p) for p in punkte], x) for x in xs]
    g = [float(v) for v in (start if start is not None else punkte)]
    M = len(xs)
    for _ in range(iterationen):
        bd = [[_band_db_bw(fs_[j], BANDS[i], g[i], EQ_BANDBREITE) for j in range(M)] for i in range(16)]
        summe = [sum(bd[i][j] for i in range(16)) for j in range(M)]
        J = [[(_band_db_bw(fs_[j], BANDS[i], g[i] + 1e-3, EQ_BANDBREITE) - bd[i][j]) / 1e-3
              for j in range(M)] for i in range(16)]
        A = [[0.0] * 16 for _ in range(16)]
        rhs = [0.0] * 16
        for a in range(16):
            for j in range(M):
                wj = gew[j] * J[a][j]
                rhs[a] -= wj * (summe[j] - ziel[j])
                for b in range(a, 16):
                    A[a][b] += wj * J[b][j]
            A[a][a] += _EQ_DAEMPFUNG
        for a in range(16):
            for b in range(a):
                A[a][b] = A[b][a]
        for c in range(16):
            p = max(range(c, 16), key=lambda k: abs(A[k][c]))
            A[c], A[p] = A[p], A[c]
            rhs[c], rhs[p] = rhs[p], rhs[c]
            for k in range(c + 1, 16):
                f = A[k][c] / A[c][c]
                for b in range(c, 16):
                    A[k][b] -= f * A[c][b]
                rhs[k] -= f * rhs[c]
        dg = [0.0] * 16
        for c in range(15, -1, -1):
            v = rhs[c] - sum(A[c][b] * dg[b] for b in range(c + 1, 16))
            dg[c] = v / A[c][c]
        g = [clamp("eq", g[i] + dg[i]) for i in range(16)]
        if max(abs(x) for x in dg) < 1e-4:
            break
    return g


def band_konfiguration(grund, eq_gains, neutral=False):
    """Band-Pipeline aus der aktiven Konfiguration bauen.

    Seit 2.0.0 steht die Band-Pipeline auch in tv.yml (Kaltstart); hier
    werden Parameter gesetzt und die Pipeline-Reihenfolge fuer Vorlauf/
    Loudness an/aus erzeugt. `neutral` = Bypass: Decke 1:1, EQ flach.

    Eingang -> Vorpegel -> Aux1 (Leveler) -> 16-Band-EQ -> Sidechain
           -> [30 ms Vorlauf auf den Nutzkanaelen] -> Decke (Kompressor)
           -> Limiter

    EQ VOR der Dynamik: die Decke sieht, was man hoert. Der Sidechain ist
    unverzoegert, die Nutzkanaele verzoegert - so wird der CamillaDSP-
    Kompressor zum Lookahead-Begrenzer (offline gegen 4.1.3 verifiziert).
    """
    cfg = copy.deepcopy(grund)
    eingang_in_konfig(cfg, _eingang["aktiv"])
    f = cfg["filters"]
    f["agc"]["parameters"]["ramp_time"] = 20.0
    for i, fname in enumerate(EQ_NAMES):
        eq_band_setzen(f[fname]["parameters"], 0.0 if neutral else clamp("eq", eq_gains[i]))
    f["band_vorlauf"] = {"type": "Delay", "parameters": {
        "delay": band_vorlauf_ms(), "unit": "ms", "subsample": False}}
    f["sc_hp"] = {"type": "Biquad", "parameters": {"type": "Highpass", "freq": BAND_SC_HP_HZ, "q": 0.7}}
    # Pegel des Nutzers: Aux2-Fader HINTER der Decke, vor dem Limiter. CamillaDSPs
    # Main-Lautstaerke sitzt VOR der Pipeline; sie wuerde den Sidechain der Decke
    # mit absenken und die Decke bei -25 dB Pegel nie greifen lassen (2.7.0).
    f["ausgang"] = {"type": "Volume", "parameters": {"ramp_time": 400.0, "fader": "Aux2"}}
    # Loudness-Kompensation: Kanaele 3,4 = H(x), 5,6 = x; Aux3 auf allen vier;
    # zurueckgemischt als 0 + 3 - 5 => x + a*(H(x) - x).
    f["band_ld_ls"] = {"type": "Biquad", "parameters": {"type": "Lowshelf", "freq": 120, "q": 0.7, "gain": 6.0}}
    f["band_ld_hs"] = {"type": "Biquad", "parameters": {"type": "Highshelf", "freq": 6000, "q": 0.7, "gain": 2.0}}
    f["band_ld_trim"] = {"type": "Gain", "parameters": {"gain": -1.4, "inverted": False, "mute": False}}
    f["band_ld_aux"] = {"type": "Volume", "parameters": {"ramp_time": 200.0, "fader": "Aux3"}}
    def quelle(ch, g=0.0, inv=False):
        return {"channel": ch, "gain": g, "inverted": inv}
    cfg.setdefault("mixers", {})
    cfg["mixers"]["band_expand"] = {"channels": {"in": 2, "out": 7}, "mapping": [
        {"dest": 0, "sources": [quelle(0)]},
        {"dest": 1, "sources": [quelle(1)]},
        {"dest": 2, "sources": [quelle(0, -6.0), quelle(1, -6.0)]},
        {"dest": 3, "sources": [quelle(0)]},
        {"dest": 4, "sources": [quelle(1)]},
        {"dest": 5, "sources": [quelle(0)]},
        {"dest": 6, "sources": [quelle(1)]},
    ]}
    # Kanal 2 ist der Detektor der Decke. Er wird hier aus den FERTIGEN
    # Nutzkanaelen gebildet (0+3-5 bzw. 1+4-6, je -6 dB = Mitte), NICHT aus
    # dem Kanal 2 von band_expand: sonst sieht die Decke die Loudness-
    # Kompensation nicht und laesst den Ausgang bei Absenkung um deren
    # Anteil ueber die Oberkante (2.8.0). Ohne Loudness-Pfad liefert der
    # Mixer "expand" dieselbe Mitte - die Kalibrierung bleibt unveraendert.
    cfg["mixers"]["band_zusammen"] = {"channels": {"in": 7, "out": 3}, "mapping": [
        {"dest": 0, "sources": [quelle(0), quelle(3), quelle(5, 0.0, True)]},
        {"dest": 1, "sources": [quelle(1), quelle(4), quelle(6, 0.0, True)]},
        {"dest": 2, "sources": [quelle(0, -6.0), quelle(3, -6.0), quelle(5, -6.0, True),
                                quelle(1, -6.0), quelle(4, -6.0), quelle(6, -6.0, True)]},
    ]}
    vor = float(f["vorpegel"]["parameters"].get("gain", -6.0))
    thr = band_decke_dbfs()
    cfg["processors"] = {"band_decke": {
        "type": "Compressor",
        "description": "Band-Oberkante: feste Ausgangsgrenze hinter Aux1",
        "parameters": {
            "channels": 3,
            "attack": BAND_DECKE_ATTACK,
            "release": BAND_DECKE_RELEASE,
            "threshold": 0.0 if neutral else thr,
            "factor": 1.0 if neutral else BAND_DECKE_FACTOR,
            "makeup_gain": max(0.0, -vor),
            "clip_limit": None,
            "soft_clip": False,
            "monitor_channels": [2],
            "process_channels": [0, 1],
        }}}
    pipeline = [
        {"type": "Filter", "channels": [0, 1], "names": ["vorpegel", "agc"] + list(EQ_NAMES)},
    ]
    if band_loudness_aktiv() and not neutral:
        pipeline += [
            {"type": "Mixer", "name": "band_expand"},
            {"type": "Filter", "channels": [3, 4], "names": ["band_ld_ls", "band_ld_hs", "band_ld_trim"]},
            {"type": "Filter", "channels": [3, 4, 5, 6], "names": ["band_ld_aux"]},
            {"type": "Mixer", "name": "band_zusammen"},
        ]
    else:
        pipeline.append({"type": "Mixer", "name": "expand"})
    pipeline.append({"type": "Filter", "channels": [2], "names": ["sc_hp"]})
    if band_vorlauf_aktiv():
        pipeline.append({"type": "Filter", "channels": [0, 1], "names": ["band_vorlauf"]})
    pipeline += [
        {"type": "Processor", "name": "band_decke"},
        {"type": "Mixer", "name": "collapse"},
    ]
    if state.get("hoehen_daempfung") and not neutral:
        # Hoehendaempfung (3.2.0): x + a*(Shelf(x) - x), a = Aux4. Hinter der
        # Decke (daempft nur), vor Pegel und Limiter. Schalter aus: fehlt ganz.
        f["hoehen_shelf"] = {"type": "Biquad", "parameters": {
            "type": "Highshelf", "freq": HOEHEN_FREQ, "q": 0.7, "gain": -HOEHEN_MAX_DB}}
        f["hoehen_aux"] = {"type": "Volume", "parameters": {"ramp_time": 100.0, "fader": "Aux4"}}
        cfg["mixers"]["hoehen_expand"] = {"channels": {"in": 2, "out": 6}, "mapping": [
            {"dest": d, "sources": [quelle(d % 2)]} for d in range(6)]}
        cfg["mixers"]["hoehen_diff"] = {"channels": {"in": 6, "out": 4}, "mapping": [
            {"dest": 0, "sources": [quelle(0)]},
            {"dest": 1, "sources": [quelle(1)]},
            {"dest": 2, "sources": [quelle(2), quelle(4, 0.0, True)]},
            {"dest": 3, "sources": [quelle(3), quelle(5, 0.0, True)]},
        ]}
        cfg["mixers"]["hoehen_summe"] = {"channels": {"in": 4, "out": 2}, "mapping": [
            {"dest": 0, "sources": [quelle(0), quelle(2)]},
            {"dest": 1, "sources": [quelle(1), quelle(3)]},
        ]}
        pipeline += [
            {"type": "Mixer", "name": "hoehen_expand"},
            {"type": "Filter", "channels": [2, 3], "names": ["hoehen_shelf"]},
            {"type": "Mixer", "name": "hoehen_diff"},
            {"type": "Filter", "channels": [2, 3], "names": ["hoehen_aux"]},
            {"type": "Mixer", "name": "hoehen_summe"},
        ]
    pipeline.append({"type": "Filter", "channels": [0, 1], "names": ["ausgang", "limiter"]})
    cfg["pipeline"] = pipeline
    return cfg, thr


def band_anwenden(neutral=False, nur_parameter=False):
    """Band-Pipeline (EQ, Decke, Vorlauf, Loudness) in CamillaDSP setzen.

    neutral=True ist der Bypass: Decke 1:1, EQ flach, kein Loudness-Pfad.
    Der Aux1-Gain bleibt dabei stehen, damit A/B gleich laut ist.
    nur_parameter=True (Decke nachfuehren): weder Aux3 zuruecksetzen noch
    ffmpeg neu starten - 2.1.1 erzeugte sonst einen Regelkreis: Neustart
    verwirft die schnelle Kalibrierung, die kommt anders zurueck, die Decke
    wird "nachgefuehrt", Neustart ... alle 30-60 s, 300 % CPU.
    """
    aktiv = aktive_konfiguration()
    eq_neu = [0.0] * 16 if neutral else [clamp("eq", g) for g in state.get("eq", [0.0] * 16)]
    try:
        eq_alt = [float(aktiv["filters"][n]["parameters"]["gain"]) for n in EQ_NAMES]
    except Exception:
        eq_alt = None
    eq_geaendert = eq_alt is None or any(abs(a - b) > 0.005 for a, b in zip(eq_alt, eq_neu))
    cfg, thr = band_konfiguration(aktiv, list(state.get("eq", [0.0] * 16)), neutral)
    # Nie eine ungueltige Pipeline aktivieren (Lehre aus 1.9.0).
    cdsp.config.validate(cfg)
    if not nur_parameter:
        # Aux3 steht nach dem CamillaDSP-Start auf 0 dB = volle Loudness-Kurve.
        # Vor dem Aktivieren auf "aus", der Regler faehrt ihn danach weich hoch.
        try:
            set_loudness_fader(BAND_LD_AUS_DB)
        except Exception as exc:
            log.warning("Aux3 nicht vorbelegt: %s", exc)
        # Aux4 steht nach dem CamillaDSP-Start auf 0 dB = volle Hoehendaempfung.
        try:
            cdsp.volume.set_volume(HOEHEN_FADER, hoehen_fader_db(_hoehen["daempfung"]))
            _hoehen["geschrieben"] = None
        except Exception as exc:
            log.warning("Aux4 nicht vorbelegt: %s", exc)
    cdsp.config.set_active(cfg)
    # Die Regelschleife braucht Blockgroesse/Vorpegel/Rate - hier liegen sie
    # ohnehin vor, ein eigenes (teures) Nachlesen entfaellt damit (2.8.8).
    try:
        geraet["chunk"] = cfg["devices"]["chunksize"]
        geraet["rate"] = cfg["devices"]["samplerate"]
        geraet["vorpegel"] = float(cfg["filters"]["vorpegel"]["parameters"].get("gain", -6.0))
        geraet["gelesen"] = time.monotonic()
    except (KeyError, TypeError, ValueError):
        geraet["gelesen"] = 0.0
    # Tatsaechlich gesendete Schwelle merken: im Bypass steht die
    # Decke neutral (0 dB) - die Neustart-Erkennung vergleicht damit (2.8.7).
    _band_decke["gesendet"] = float(cfg["processors"]["band_decke"]["parameters"]["threshold"])
    _band_decke.update(dbfs=thr, zeit=time.monotonic(),
                       kal=float(state.get("kalibrierung_schnell", 0.0)
                                 if state.get("kalibrierung_schnell_gueltig")
                                 else state.get("kalibrierung", 0.0)))
    if nur_parameter:
        log.info("Band: Decke nachgefuehrt auf %.1f dBFS", thr)
        return
    band_vad_nachziehen()
    log.info("Band: %s, Band %.1f..%.1f LUFS, Decke %.1f dBFS, Vorlauf %s, EQ %s",
             "Bypass (neutral)" if neutral else "aktiv", band_unten(), band_oben(), thr,
             ("%.0f ms" % band_vorlauf_ms()) if band_vorlauf_aktiv() else "aus",
             "flach" if neutral else _eq_kurz(state.get("eq", [0.0] * 16)))
    if eq_geaendert:
        r128_eq_neu_laden()      # ffmpeg-Spiegelung nur, wenn sich die Kurve geaendert hat



def _load(path, fallback):
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception as exc:
            log.warning("%s unlesbar: %s", path, exc)
    return copy.deepcopy(fallback)


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def save_settings():
    _save(SETTINGS_FILE, state)


_save_timer = None


def save_settings_spaeter(sek=2.0):
    """Speichern entprellen (EQ-Zug erzeugt viele Aenderungen; SD-Karte schonen)."""
    global _save_timer
    with lock:
        if _save_timer is not None:
            _save_timer.cancel()
        _save_timer = threading.Timer(sek, save_settings)
        _save_timer.daemon = True
        _save_timer.start()


_cpu_lock = threading.Lock()


_cpu_cache = {"total": None, "idle": None, "wert": None, "zeit": 0.0}

def cpu_last_prozent():
    """Gesamte CPU-Auslastung des Raspberry Pi aus /proc/stat.

    Kein zusaetzliches Paket und kein Prozessstart pro Web-Poll. Der Wert
    wird hoechstens viermal pro Sekunde neu berechnet.
    """
    jetzt = time.monotonic()
    with _cpu_lock:
        if (_cpu_cache["wert"] is not None and
                jetzt - _cpu_cache["zeit"] < 0.25):
            return _cpu_cache["wert"]
        try:
            with open("/proc/stat", "r", encoding="ascii") as fh:
                teile = fh.readline().split()[1:]
            werte = [int(x) for x in teile]
            total = sum(werte)
            idle = werte[3] + (werte[4] if len(werte) > 4 else 0)
            alt_total, alt_idle = _cpu_cache["total"], _cpu_cache["idle"]
            wert = _cpu_cache["wert"]
            if alt_total is not None and total > alt_total:
                dt = total - alt_total
                di = idle - alt_idle
                wert = round(max(0.0, min(100.0, 100.0 * (dt - di) / dt)), 1)
            _cpu_cache.update(total=total, idle=idle, wert=wert, zeit=jetzt)
            return wert
        except Exception:
            return _cpu_cache["wert"]


def hole(daten, schluessel):
    """Pegelliste robust auslesen - die Schluesselnamen weichen
    zwischen den pycamilladsp-Versionen leicht ab."""
    for name in (schluessel, schluessel.replace("_", ""),
                 schluessel.replace("_", "").capitalize()):
        wert = daten.get(name) if isinstance(daten, dict) else None
        if wert:
            return wert
    return [-99.0, -99.0]


# --------------------------------------------------------------- Verbindung
#
# Verbindungszustand mit Begruendung. Ohne das sieht der Nutzer nur
# "getrennt" und weiss nicht, ob die Karte fehlt, CamillaDSP abgestuerzt
# ist oder das ASRC-Routing klemmt - drei Probleme, drei Loesungen.

verbindung = {"bereit": False, "diagnose": "Start läuft ...", "hinweis": ""}

# ------------------------------------------------------------ DSP-Profil-Waechter
# Der Analogausgang des DAC+ DSP haengt HINTER dem DSP-Chip: nur das Profil
# dspdac-noautomute routet I2S vom Pi zum DAC (docs/hifiberry-dsp.md "Das DSP-Profil
# ist die halbe Karte"). Ein fremdes Profil (z. B. Beocreate Universal ab Werk
# auf einer neuen Karte) heisst: Pegel da, Ausgang stumm. install.sh prueft
# und flasht; hier wird der Zustand zusaetzlich fuer die Weboberflaeche
# ueberwacht. get-checksum liest nur (und stoesst nebenbei die Wiedergabe an).
DSP_PROFIL_SOLL = "7F88CAAD8E8A7E4F05731605AE16EF38"
dsp_profil = {"ok": None, "ist": None, "geprueft": 0.0, "meldung": "noch nicht geprueft"}
# Pruefsumme dieses Systemstarts. tvdsp-kick schreibt sie beim Hochfahren (es
# liest sie ohnehin), das Backend uebernimmt sie von dort. WICHTIG (2.8.7):
# `dsptoolkit get-checksum` HAELT DEN DSP-CHIP AN, um sein Programm zu lesen
# (hifiberrydsp: "Must kill the core to read program memory"). Der Analog-
# ausgang haengt hinter dem Chip - jede Abfrage ist ein hoerbarer Aussetzer.
# Bis 2.8.6 lief sie alle 10 Minuten: Knacken "alle paar Minuten".
DSP_PROFIL_MARKER = os.path.join(STATE_DIR, "dsp-profil-pruefsumme")


def _dsptoolkit_pfad():
    kandidaten = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "venv", "bin", "dsptoolkit"),
                  "/opt/tvdsp/venv/bin/dsptoolkit", shutil.which("dsptoolkit") or ""]
    for k in kandidaten:
        if k and os.path.exists(k):
            return os.path.abspath(k)
    return None


def _dsp_profil_bewerten(ist, quelle):
    if not ist:
        dsp_profil.update(ok=None, ist=None, geprueft=time.monotonic(), meldung="DSP nicht erreichbar")
    elif ist == DSP_PROFIL_SOLL:
        dsp_profil.update(ok=True, ist=ist, geprueft=time.monotonic(), meldung="dspdac-noautomute")
        log.info("DSP-Profil korrekt (%s)", quelle)
    else:
        dsp_profil.update(ok=False, ist=ist, geprueft=time.monotonic(),
                          meldung="fremdes Profil (%s)" % ist[:8])
        log.warning("DSP-Profil fremd: %s (Soll %s) - Analogausgang bleibt stumm", ist, DSP_PROFIL_SOLL)


def dsp_profil_aus_marker(pfad=None):
    """Pruefsumme DIESES Systemstarts aus der Marker-Datei, sonst None.

    Eine Datei aus einem frueheren Start zaehlt nicht (Zeitstempel vor dem
    Hochfahren) - nach einem Stromausfall wird also neu gelesen.
    """
    pfad = pfad or DSP_PROFIL_MARKER
    try:
        hochgefahren = time.time() - float(open("/proc/uptime").read().split()[0])
        if os.stat(pfad).st_mtime < hochgefahren - 5:
            return None
        ist = "".join(open(pfad).read().split()).upper()
        return ist or None
    except (OSError, ValueError, IndexError):
        return None


def dsp_profil_pruefen():
    """Pruefsumme selbst lesen - haelt den DSP-Chip dabei kurz an (Aussetzer).

    Nur als Rueckfall, wenn tvdsp-kick in diesem Systemstart nichts
    hinterlegt hat. Das Ergebnis wird als Marker abgelegt, damit weitere
    Backend-Neustarts (Ausrollen) den Chip nicht erneut anhalten.
    """
    dt = _dsptoolkit_pfad()
    if not dt:
        dsp_profil.update(ok=None, ist=None, geprueft=time.monotonic(), meldung="dsptoolkit nicht vorhanden")
        return
    try:
        r = subprocess.run([dt, "get-checksum"], capture_output=True, text=True, timeout=20)
        ist = "".join(r.stdout.split()).upper()
        if ist:
            tmp = DSP_PROFIL_MARKER + ".neu"
            with open(tmp, "w") as fh:
                fh.write(ist + "\n")
            os.replace(tmp, DSP_PROFIL_MARKER)
        _dsp_profil_bewerten(ist, "selbst gelesen")
    except Exception as exc:
        dsp_profil.update(ok=None, ist=None, geprueft=time.monotonic(), meldung="Pruefung fehlgeschlagen: %s" % exc)


def dsp_profil_waechter():
    """Profil EINMAL pro Systemstart pruefen, nicht periodisch (2.8.7).

    Das Profil liegt im EEPROM der Karte und aendert sich im Betrieb nicht,
    nur durch install.sh (das den Marker dabei loescht). Zuerst die
    Pruefsumme von tvdsp-kick uebernehmen (bis ~100 s nach dem Start);
    nur wenn keine da ist, selbst lesen - das haelt den Chip einmal an.
    """
    time.sleep(40)
    for _ in range(12):
        ist = dsp_profil_aus_marker()
        if ist:
            _dsp_profil_bewerten(ist, "Pruefsumme dieses Starts, ohne DSP-Zugriff")
            return
        time.sleep(5)
    dsp_profil_pruefen()


# ------------------------------------------------------------ Web-Neuinstallation
# install.sh aus der Paketkopie (/opt/tvdsp/paket) als transiente systemd-
# Unit starten. So ueberlebt der Installer den Neustart von tvdsp-web, den
# er selbst ausloest. Der genaue Aufruf steht in /etc/sudoers.d/tvdsp-system.
INSTALL_PAKET = os.environ.get("INSTALL_SRC", "/opt/tvdsp/paket/install.sh")
INSTALL_LOG = os.path.join(STATE_DIR, "install.log")
INSTALL_UNIT = "tvdsp-install"


def install_laeuft():
    try:
        r = subprocess.run(["systemctl", "is-active", INSTALL_UNIT], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() in ("active", "activating")
    except Exception:
        return False


def install_starten():
    if not os.path.exists(INSTALL_PAKET):
        raise RuntimeError("Paketkopie %s fehlt - einmal von Hand 'sudo ./install.sh' ausfuehren" % INSTALL_PAKET)
    if install_laeuft():
        raise RuntimeError("Installation laeuft bereits")
    with open(INSTALL_LOG, "w") as fh:
        fh.write("== Neuinstallation gestartet %s ==\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    sudo = shutil.which("sudo") or "/usr/bin/sudo"
    sdr = shutil.which("systemd-run") or "/usr/bin/systemd-run"
    prefix = os.path.dirname(STATE_DIR)
    import getpass
    cmd = [sudo, "-n", sdr, "--unit=" + INSTALL_UNIT, "--collect",
           "--setenv=TVDSP_RUN_USER=%s" % getpass.getuser(),
           "-p", "StandardOutput=append:%s/config/install.log" % prefix,
           "-p", "StandardError=append:%s/config/install.log" % prefix,
           INSTALL_PAKET]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError("Start fehlgeschlagen: %s" % (r.stderr.strip() or r.stdout.strip() or r.returncode))
    log.info("Neuinstallation gestartet (%s)", INSTALL_UNIT)


def soundkarte():
    """(gefunden, Name) - liest /proc/asound/cards."""
    try:
        with open("/proc/asound/cards") as fh:
            inhalt = fh.read()
    except Exception:
        return False, None
    for zeile in inhalt.splitlines():
        if "hifiberry" in zeile.lower():
            teile = zeile.split()
            return True, teile[1] if len(teile) > 1 else "?"
    for zeile in inhalt.splitlines():
        if "[" in zeile:
            return False, zeile.split("[")[1].split("]")[0].strip()
    return False, None


def diagnose_stellen():
    """Warum keine Verbindung? Von der wahrscheinlichsten Ursache an."""
    gefunden, name = soundkarte()
    if not gefunden:
        if name:
            return ("Keine HiFiBerry-Karte gefunden (nur \"%s\")." % name,
                    "Karte aufgesteckt? Overlay in config.txt geprüft? Neu gestartet?")
        return ("Keine Soundkarte gefunden.",
                "Ist die HiFiBerry-Karte aufgesteckt und der Pi neu gestartet worden?")
    if cdsp.is_connected():
        return ("CamillaDSP hat keine Konfiguration geladen.",
                "Karte vorhanden, aber die Verarbeitung läuft nicht. "
                "Prüfen mit: journalctl -u camilladsp -n 30")
    return ("CamillaDSP läuft nicht.",
            "Prüfen mit: journalctl -u camilladsp -n 30")


def ensure_connected():
    if not cdsp.is_connected():
        cdsp.connect()


def aktive_konfiguration():
    """Aktive Konfiguration holen - oder verstaendlich scheitern.

    CamillaDSP kann verbunden sein und trotzdem keine Konfiguration
    geladen haben (Zustand INACTIVE, etwa nach Start mit -w). Der
    direkte Zugriff auf das Ergebnis liefert dann 'NoneType' object is
    not subscriptable - eine Meldung, mit der niemand etwas anfangen
    kann.
    """
    ensure_connected()
    cfg = cdsp.config.active()
    if cfg is None:
        raise RuntimeError(
            "CamillaDSP läuft, hat aber keine Konfiguration geladen. "
            "Meist fehlt die Soundkarte, oder der Dienst wurde mit -w "
            "gestartet. Prüfen: journalctl -u camilladsp -n 30")
    return cfg


def pufferstand():
    """Fuellstand des Ausgabepuffers in Frames. Der Name der Methode hat
    sich zwischen den pycamilladsp-Versionen geaendert."""
    for pfad in (("status", "buffer_level"), ("general", "buffer_level"),
                 ("status", "bufferlevel")):
        try:
            obj = getattr(cdsp, pfad[0], None)
            fn = getattr(obj, pfad[1], None) if obj else None
            if fn:
                wert = fn()
                if wert is not None:
                    return int(wert)
        except Exception:
            continue
    return None


def latenz_ms(chunk, rate):
    """Ueberschlag der Latenz in Millisekunden.

    Enthaelt Aufnahme-Chunk, Verarbeitung und Ausgabepuffer. NICHT
    enthalten: was der Fernseher zwischen Bild und optischem Ausgang
    braucht - das kann von aussen niemand messen.
    """
    if not chunk or not rate:
        return None
    puffer = pufferstand()
    frames = 2 * chunk + (puffer if puffer is not None else chunk)
    return round(frames / rate * 1000.0, 1)


# ------------------------------------------------------------ CamillaDSP

def set_agc_gain(db):
    """Regelverstaerkung setzen - ueber den Aux1-Fader.

    Ein Fader wird im laufenden Betrieb gesetzt und intern sanft
    gerampt. Ein Konfigurationstausch wuerde die Pipeline bei jedem
    Regelschritt neu aufbauen, also bis zu zehnmal pro Sekunde - mit
    hoerbaren Aussetzern.

    Fader 0 ist Main, 1 bis 4 sind Aux1 bis Aux4.
    """
    db = max(-AGC_MAX, min(AGC_MAX, float(db)))
    ensure_connected()
    for aufruf in (lambda: cdsp.volume.set_volume(1, db),
                   lambda: cdsp.volume.set_fader_volume(1, db),
                   lambda: cdsp.volume.set_aux1_volume(db)):
        try:
            aufruf()
            return
        except (AttributeError, TypeError):
            continue
    raise RuntimeError("Kein Fader-Zugriff in dieser pycamilladsp-Version")


def lies_agc_gain():
    for aufruf in (lambda: cdsp.volume.volume(1),
                   lambda: cdsp.volume.fader_volume(1),
                   lambda: cdsp.volume.aux1_volume()):
        try:
            wert = aufruf()
            if wert is not None:
                return float(wert)
        except (AttributeError, TypeError):
            continue
    return None

PEGEL_FADER = 2   # Aux2 = Pegel des Nutzers, Volume-Filter "ausgang" hinter der Decke


def set_gesamtpegel(db):
    """Pegel des Nutzers: Aux2-Fader (Filter "ausgang" hinter der Decke).

    Seit 2.7.0 nicht mehr CamillaDSPs Main: Main wird VOR der Pipeline
    angewendet und haette die Decke (Sidechain) mit abgesenkt. Main bleibt
    im Betrieb auf 0 dB und dient nur als sicherer Startwert (-35 dB), bis
    die Pipeline mit "ausgang" steht (siehe verbinder()).
    """
    db = clamp("volume", db)
    ensure_connected()
    cdsp.volume.set_volume(PEGEL_FADER, db)
    state["volume"] = db
    return db


def lies_gesamtpegel():
    try:
        wert = cdsp.volume.volume(PEGEL_FADER)
    except Exception:
        wert = None
    if wert is None:
        return float(state.get("volume", -35.0))
    return clamp("volume", float(wert))


def ausgang_in_pipeline():
    """True, wenn die aktive Pipeline den Aux2-Pegelfilter "ausgang" enthaelt.

    Nur dann darf Main auf 0 dB - sonst liefe der Ton ohne jede Daempfung.
    """
    try:
        cfg = cdsp.config.active() or {}
        return any("ausgang" in (schritt.get("names") or [])
                   for schritt in cfg.get("pipeline", [])
                   if schritt.get("type") == "Filter")
    except Exception:
        return False


def read_eq():
    cfg = aktive_konfiguration()
    return [cfg["filters"][n]["parameters"]["gain"] for n in EQ_NAMES]


def _eq_kurz(gains):
    """Kompakte Darstellung einer EQ-Kurve fuer das Journal."""
    try:
        werte = [float(g) for g in gains]
    except (TypeError, ValueError):
        return str(gains)
    if all(abs(g) < 0.005 for g in werte):
        return "flach"
    return "min %+.1f / max %+.1f dB" % (min(werte), max(werte))


def set_eq(gains, herkunft="api"):
    """Klangkurve setzen, im Zustand merken und (entprellt) speichern.

    Jeder Schreibvorgang wird mit Herkunft protokolliert (seit 1.9.31).
    Im Bypass bleibt der DSP flach; die Kurve wird nur gemerkt und beim
    Verlassen des Bypass gesetzt.
    """
    gains = [clamp("eq", g) for g in list(gains)[:16]]
    if len(gains) != 16:
        raise ValueError("EQ braucht 16 Werte")
    state["eq"] = gains
    save_settings_spaeter()
    if state.get("bypass"):
        log.info("EQ gemerkt (%s, Bypass aktiv): %s", herkunft, _eq_kurz(gains))
        return
    cfg = copy.deepcopy(aktive_konfiguration())
    for i, fname in enumerate(EQ_NAMES):
        eq_band_setzen(cfg["filters"][fname]["parameters"], gains[i])
    cdsp.config.validate(cfg)
    cdsp.config.set_active(cfg)
    log.info("EQ geschrieben (%s): %s", herkunft, _eq_kurz(gains))
    r128_eq_neu_laden()



# ------------------------------------------------------------ Regelschleife

def regelschleife():
    """Misst die Eingangslautheit und haelt sie im Band (Aux1-Fader).

    Ablauf je 20-ms-Zyklus: Pegel lesen -> Verlauf -> Stille-Gate/Idle ->
    Kalibrierung (3 s, gegen R128-S) -> Band-Regel (zu laut / im Band /
    zu leise) -> Loudness-Anteil (Aux3) -> Aux1 schreiben. Die Decke sitzt
    im DSP und braucht hier nichts.
    """
    gain = 0.0             # tatsaechlich an Aux1 gesendeter Gain
    uebernommen = False
    letzter = time.monotonic()
    letzte_regelquelle = None
    luecke_seit = None
    kalib_letzter_frame = 0.0
    kalib_session_bereit = False
    kalib_letzte_speicherung = time.monotonic()
    kalib_gespeichert_wert = float(state.get("kalibrierung", DEFAULT_KALIBRIERUNG))
    kalib_schnell_gespeichert_wert = float(state.get(
        "kalibrierung_schnell", DEFAULT_KALIBRIERUNG))
    band_hist = deque()          # (t, M) fuer die gegatete Programmlautheit
    band_frame = 0.0
    band_ld_anteil = 0.0         # geglaetteter Anteil der Loudness-Kurve (0..1)
    band_ld_db = None            # zuletzt geschriebener Aux3-Wert
    diagnose_zeit = 0.0
    lra_zeit = 0.0
    stille_seit = None
    naechster_takt = time.monotonic()
    # Fuer die gruene Linie: Gesamtverstaerkung der Kette als Messung ueber
    # DASSELBE 400-ms-Fenster fuer Ein- und Ausgang (levels_since), so wie
    # Momentary misst. Damit spielt die Pipeline-Latenz keine Rolle mehr
    # (sie liegt innerhalb des Fensters) - keine Chunk-Ausrichtung, keine
    # Klammerung. Wird mit 10 Hz abgefragt, nicht mit 50.
    gain_mess = None
    gain_mess_zeit = 0.0

    while True:
        # Fester 20-ms-Takt; nach groesseren Pausen neu synchronisieren statt
        # hektisch nachzuholen. Nach drei Sekunden Stille: 1 Hz.
        takt = IDLE_INTERVALL if _audio_idle.is_set() else REGEL_INTERVALL
        naechster_takt += takt
        pause = naechster_takt - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        elif pause < -0.10:
            naechster_takt = time.monotonic()
        try:
            with lock:
                ensure_connected()
                # Beim ersten Verbindungsaufbau uebernehmen, was der Fader
                # schon zeigt, sonst springt der Pegel bei einem reinen
                # Backend-Neustart.
                if not uebernommen:
                    uebernommen = True
                    vorhanden = lies_agc_gain()
                    if vorhanden is not None:
                        gain = max(-AGC_MAX, min(AGC_MAX, vorhanden))
                        messung["agc_gain"] = round(gain, 2)
                        log.info("Regelung uebernimmt vorhandene "
                                 "Verstaerkung: %+.1f dB", gain)
                # Einzige 50-Hz-WebSocket-Abfrage: Pegel.
                lv = cdsp.levels.levels()
                messung["in_peak"] = max(hole(lv, "capture_peak"))
                messung["out_peak"] = max(hole(lv, "playback_peak"))
                rms_ein = hole(lv, "capture_rms")
                rms_aus = hole(lv, "playback_rms")
                meta_jetzt = time.monotonic()
                if meta_jetzt - diagnose_zeit >= 0.5:
                    messung["rate"] = cdsp.rate.capture()
                    messung["status"] = str(cdsp.general.state())
                    diagnose_zeit = meta_jetzt
                # Blockgroesse/Vorpegel/Abtastrate aendern sich nur ueber eigene
                # Aufrufe (band_anwenden, /api/chunksize), die sie direkt
                # eintragen. config.active() kostet ~120 ms (YAML) und lief
                # hier bis 2.8.7 alle 2 s UNTER der Sperre - die 50-Hz-Regelung
                # stand dabei jedes Mal still. Jetzt nur zur Sicherheit 1x/min.
                if meta_jetzt - geraet["gelesen"] > GERAET_NACHLESEN_SEK:
                    _cfg = cdsp.config.active() or {}
                    if not _cfg:
                        raise RuntimeError("keine aktive Konfiguration")
                    geraet["chunk"] = _cfg["devices"]["chunksize"]
                    try:
                        geraet["vorpegel"] = float(
                            _cfg["filters"]["vorpegel"]["parameters"].get("gain", -6.0))
                    except (KeyError, TypeError, ValueError):
                        geraet["vorpegel"] = -6.0
                    geraet["rate"] = _cfg["devices"]["samplerate"]
                    geraet["gelesen"] = meta_jetzt
                    messung["latenz"] = latenz_ms(geraet["chunk"], geraet["rate"])
                messung["chunksize"] = geraet["chunk"]
        except Exception:
            messung["status"] = "getrennt"
            time.sleep(1)
            continue

        def energie(werte):
            lin = [10 ** (v / 20.0) for v in werte if v is not None and v > -120]
            if not lin:
                return None
            return 20 * math.log10(max(
                math.sqrt(sum(v * v for v in lin) / len(lin)), 1e-9))

        pegel = energie(rms_ein)
        pegel_aus = energie(rms_aus)
        if pegel is None:
            pegel = -99.0
        messung["aus_wert"] = None if pegel_aus is None else round(
            pegel_aus + state["kalibrierung"], 1)

        # Lautheitsverlauf fuer das Klang-Diagramm (immer, auch in Stille).
        try:
            kal_v = float(state.get("kalibrierung", 0.0))
            m_frisch = (r128_frisch() and messung.get("r128_m_raw") is not None
                        and float(messung["r128_m_raw"]) > -70)
            ein_v = float(messung["r128_m_raw"]) if m_frisch else (
                VERLAUF_BODEN if pegel < -90 else pegel + kal_v)
            jetzt_v = time.monotonic()
            if jetzt_v - gain_mess_zeit >= 0.1 and pegel > -90:
                gain_mess_zeit = jetzt_v
                try:
                    with lock:
                        lv400 = cdsp.levels.levels_since(0.4)
                    ein400 = energie(hole(lv400, "capture_rms"))
                    aus400 = energie(hole(lv400, "playback_rms"))
                    # Beide Fenster brauchen Signal; sonst ist der Quotient nicht definiert.
                    gain_mess = (aus400 - ein400) if (ein400 is not None and aus400 is not None
                                                      and ein400 > -70.0) else None
                except Exception:
                    gain_mess = None
            if pegel < -90 or gain_mess is None:
                aus_v = VERLAUF_BODEN
            else:
                aus_v = ein_v + gain_mess - float(state.get("volume", 0.0))
            verlauf_aufzeichnen(jetzt_v, ein_v, aus_v, gain)
        except Exception:
            pass

        # Gate: Stille darf nichts hochfahren. Nach drei Sekunden echter
        # Stille Messbetrieb auf 1 Hz, ffmpeg pausiert.
        if pegel < -55:
            still_jetzt = time.monotonic()
            if stille_seit is None:
                stille_seit = still_jetzt
            if luecke_seit is None:
                luecke_seit = still_jetzt
            if (not _audio_idle.is_set() and
                    still_jetzt - stille_seit >= IDLE_NACH_SEK):
                audio_idle_setzen(True)
                _roh_fenster.clear()
                _kalib_kandidaten.clear()
                _kalib_schnell_kandidaten.clear()
                kalib_session_bereit = False
                naechster_takt = still_jetzt
            messung["lautheit"] = None
            messung["regelquelle"] = "stille"
            letzte_regelquelle = "stille"
            letzter = still_jetzt        # Stille ist keine aufgesparte Regelzeit
            continue

        # Signal wieder da.
        if _audio_idle.is_set():
            audio_idle_setzen(False)
            naechster_takt = time.monotonic()
            letzter = time.monotonic()
        stille_seit = None
        if luecke_seit is not None:
            if time.monotonic() - luecke_seit >= LUECKE_GATE:
                band_hist.clear()        # Programmlautheit des alten Senders verwerfen
            luecke_seit = None

        # Kalibrierung: R128-S (3 s) gegen ein festes 3-s-Roh-RMS-Fenster;
        # dB-Mittel fuer die Anzeige, Leistungsmittel fuer die Decke.
        _roh_fenster.append(pegel)
        n_kal = round(KALIB_FENSTER_SEK / REGEL_INTERVALL)
        roh_liste = list(_roh_fenster)
        rms_roh_kal = None
        rms_roh_kal_energie = None
        if len(roh_liste) >= n_kal:
            letzte_kal = roh_liste[-n_kal:]
            rms_roh_kal = sum(letzte_kal) / len(letzte_kal)
            rms_roh_kal_energie = 10.0 * math.log10(max(1e-12,
                sum(10 ** (v / 10.0) for v in letzte_kal) / len(letzte_kal)))

        frame_zeit_kal = float(messung.get("r128_zeit") or 0.0)
        if (rms_roh_kal is not None and r128_frisch()
                and messung.get("r128_s_raw") is not None
                and float(messung["r128_s_raw"]) > -70
                and frame_zeit_kal > kalib_letzter_frame + 1e-6):
            kandidat = max(-40.0, min(40.0,
                float(messung["r128_s_raw"]) - rms_roh_kal))
            _kalib_kandidaten.append(kandidat)
            if rms_roh_kal_energie is not None:
                _kalib_schnell_kandidaten.append(max(-40.0, min(40.0,
                    float(messung["r128_s_raw"]) - rms_roh_kal_energie)))
            kalib_letzter_frame = frame_zeit_kal
            med = _median(_kalib_kandidaten)
            med_schnell = _median(_kalib_schnell_kandidaten)
            if (med_schnell is not None and not _kalib_schnell_gueltig["ok"]
                    and len(_kalib_schnell_kandidaten) >= 5):
                state["kalibrierung_schnell"] = round(med_schnell, 2)
                state["kalibrierung_schnell_gueltig"] = True
                _kalib_schnell_gueltig["ok"] = True
                kalib_schnell_gespeichert_wert = state["kalibrierung_schnell"]
                save_settings()
                log.info("Kalibrierung (energiebasiert) neu: %+.2f dB",
                         state["kalibrierung_schnell"])
            if med is not None:
                if not kalib_session_bereit:
                    state["kalibrierung"] = round(med, 2)
                    if med_schnell is not None:
                        state["kalibrierung_schnell"] = round(med_schnell, 2)
                        state["kalibrierung_schnell_gueltig"] = True
                        _kalib_schnell_gueltig["ok"] = True
                    kalib_session_bereit = True
                    kalib_letzte_speicherung = time.monotonic()
                    kalib_gespeichert_wert = state["kalibrierung"]
                    kalib_schnell_gespeichert_wert = float(state.get(
                        "kalibrierung_schnell", DEFAULT_KALIBRIERUNG))
                    save_settings()
                    log.info("Kalibrierung automatisch initialisiert: %+.2f dB",
                             state["kalibrierung"])
                elif len(_kalib_kandidaten) >= 10:
                    # Median der letzten 5 s; danach max. 0,1 dB/s nachfuehren.
                    diff_kal = med - float(state["kalibrierung"])
                    state["kalibrierung"] = round(max(-40.0, min(40.0,
                        float(state["kalibrierung"]) + max(-0.01, min(0.01, diff_kal)))), 3)
                    if med_schnell is not None:
                        diff_s = med_schnell - float(state.get("kalibrierung_schnell", med_schnell))
                        state["kalibrierung_schnell"] = round(max(-40.0, min(40.0,
                            float(state.get("kalibrierung_schnell", med_schnell))
                            + max(-0.01, min(0.01, diff_s)))), 3)
                    jetzt_kal = time.monotonic()
                    normal_geaendert = abs(state["kalibrierung"] - kalib_gespeichert_wert) >= 0.1
                    schnell_geaendert = abs(float(state.get("kalibrierung_schnell", DEFAULT_KALIBRIERUNG))
                                            - kalib_schnell_gespeichert_wert) >= 0.1
                    # Alle 10 min statt alle 30 s (bis 2.8.7: ~2.900 Schreib-
                    # vorgaenge am Tag auf die SD-Karte). Die Kalibrierung
                    # bestimmt sich nach einem Neustart ohnehin in Sekunden neu;
                    # beim Beenden des Dienstes wird zusaetzlich gesichert.
                    if (jetzt_kal - kalib_letzte_speicherung >= KALIB_SPEICHERN_SEK and
                            (normal_geaendert or schnell_geaendert)):
                        save_settings()
                        kalib_letzte_speicherung = jetzt_kal
                        kalib_gespeichert_wert = state["kalibrierung"]
                        kalib_schnell_gespeichert_wert = float(state.get(
                            "kalibrierung_schnell", DEFAULT_KALIBRIERUNG))

        lautheit_roh = pegel + state["kalibrierung"]
        _lra_fenster.append(lautheit_roh)
        lra_jetzt = time.monotonic()
        if (len(_lra_fenster) >= LRA_MIN_SAMPLES and lra_jetzt - lra_zeit >= 0.5):
            sortiert = sorted(_lra_fenster)
            lo = sortiert[int(len(sortiert) * 0.10)]
            hi = sortiert[int(len(sortiert) * 0.95)]
            messung["lra"] = round(hi - lo, 1)
            lra_zeit = lra_jetzt

        jetzt = time.monotonic()
        dt = min(REGEL_DT_MAX, max(0.001, jetzt - letzter))
        letzter = jetzt

        if not state["agc_aktiv"]:
            # /api/schalter setzt den Aux1-Fader beim Ausschalten auf 0 dB.
            gain = 0.0
            band_hist.clear()
            messung["agc_gain"] = 0.0
            messung["lautheit"] = None
            messung["regelquelle"] = "aus"
            messung["band"] = None
            letzte_regelquelle = "aus"
            continue
        if state["bypass"]:
            messung["lautheit"] = None
            messung["regelquelle"] = "bypass"
            messung["band"] = None
            letzte_regelquelle = "bypass"
            continue

        # ---------------------------------------------------------- Band-Regel
        if letzte_regelquelle not in ("band", "band-halten"):
            band_hist.clear()
            band_frame = 0.0
        oben = band_oben()
        unten = band_unten()
        einzug = band_einzug()
        frame_zeit = float(messung.get("r128_zeit") or 0.0)
        m_raw = messung.get("r128_m_raw")
        frisch = (r128_frisch(jetzt) and m_raw is not None and float(m_raw) > -70)
        if frisch and frame_zeit > band_frame + 1e-6:
            band_frame = frame_zeit
            band_hist.append((frame_zeit, float(m_raw)))
            while band_hist and frame_zeit - band_hist[0][0] > BAND_GATE_FENSTER:
                band_hist.popleft()
        gain_neu = gain
        programm = band_programmlautheit(band_hist)
        mittel = band_mittel(band_hist)
        sprache = False
        if frisch:
            m = float(m_raw)
            s_raw = messung.get("r128_s_raw")
            s_ok = s_raw is not None and float(s_raw) > -70
            bezug_runter = float(s_raw) if s_ok else m          # Short-Term; Spitzen macht die Decke
            sprache = band_vad_aktiv() and vad_sprache()
            deckel = BAND_MAX_ANHEBUNG_SPRACHE if sprache else BAND_MAX_ANHEBUNG
            if bezug_runter + gain > oben:
                zustand = "zu laut"
                soll = max(-AGC_MAX, (oben - einzug) - bezug_runter)
                if soll < gain:
                    gain_neu = gain + (soll - gain) * (1.0 - math.exp(-dt / BAND_TAU_RUNTER))
            elif BAND_ANHEBUNG_MODUS == "mittel":
                zustand = "im Band"
                mittel_kurz = band_mittel(band_hist, BAND_ANHEBUNG_FENSTER)
                if mittel_kurz is not None and not (gain < 0.0 and (mittel is None or mittel + gain >= unten)):
                    # abgesenkt (gain < 0) wird wie bisher im Band gehalten
                    ziel = min(deckel, (unten + einzug) - mittel_kurz)
                    soll = ziel if gain < 0.0 else max(0.0, ziel)
                    if soll > gain and not sprache and m < band_rauschsperre():
                        zustand = "zu leise (Rauschsperre)"
                    elif abs(soll - gain) > 0.05:
                        neu = gain + (soll - gain) * (1.0 - math.exp(-dt / BAND_TAU_ANHEBUNG))
                        if neu > gain:
                            neu = max(gain, min(neu, (oben - einzug) - bezug_runter))   # nie ueber die Oberkante
                            zustand = "zu leise"
                        else:
                            zustand = "Anhebung zurück"
                        gain_neu = neu
            elif mittel is not None and mittel + gain < unten and not sprache and m < band_rauschsperre():
                zustand = "zu leise (Rauschsperre)"
            elif mittel is not None and mittel + gain < unten:
                zustand = "zu leise"
                soll = min(deckel, (unten + einzug) - mittel)
                if soll > gain:
                    schritt = (soll - gain) * (1.0 - math.exp(-dt / BAND_TAU_HOCH))
                    schritt = min(schritt, BAND_RATE_HOCH * dt)
                    gain_neu = gain + schritt
                    gain_neu = min(gain_neu, (oben - einzug) - bezug_runter)   # nie ueber die Oberkante
                    gain_neu = max(gain_neu, gain)
            elif (BAND_ANHEBUNG_MODUS == "nur_bis_unten" and gain > 0.0
                  and bezug_runter + gain > unten + einzug + BAND_ANHEBUNG_TOLERANZ):
                zustand = "Anhebung zurück"
                soll = max(0.0, (unten + einzug) - bezug_runter)
                gain_neu = gain + (soll - gain) * (1.0 - math.exp(-dt / BAND_TAU_ANHEBUNG_ZURUECK))
            else:
                zustand = "im Band"          # bewusst: hier wird nichts geregelt
            messung["lautheit"] = round(m + gain, 1)
            messung["regelquelle"] = "band"
        else:
            zustand = "halten"
            messung["lautheit"] = None
            messung["regelquelle"] = "band-halten"
        letzte_regelquelle = messung["regelquelle"]

        # Loudness-Kompensation: Anteil aus der aktuellen Absenkung, 2-s-Glaettung.
        if band_loudness_aktiv():
            ziel_anteil = min(1.0, band_loudness_staerke() * max(0.0, min(1.0, -gain_neu / BAND_LD_VOLL)))
        else:
            ziel_anteil = 0.0
        band_ld_anteil += (ziel_anteil - band_ld_anteil) * (1.0 - math.exp(-dt / BAND_LD_TAU))
        ld_db = band_loudness_db(band_ld_anteil)
        erneuern = _fader_erneuern.is_set()
        if erneuern:
            _fader_erneuern.clear()
            band_ld_db = None          # Aux3 nach einem Neustart neu schreiben
        if band_ld_db is None or abs(ld_db - band_ld_db) > BAND_LD_SCHREIB_EPS:
            try:
                with lock:
                    set_loudness_fader(ld_db)
            except Exception as exc:
                log.warning("Loudness-Fader nicht gesetzt: %s", exc)
            else:
                band_ld_db = ld_db

        messung["band"] = {
            "zustand": zustand,
            "loudness_prozent": round(100.0 * band_ld_anteil, 0),
            "m": None if not frisch else round(float(m_raw) + gain, 1),
            "programm": None if mittel is None else round(mittel + gain, 1),
            "oben": oben, "unten": unten,
            "gain": round(gain_neu, 1),
            "rauschsperre": band_rauschsperre(),
            "sprache": bool(sprache) if frisch else False,
            "decke_dbfs": _band_decke.get("dbfs"),
            "vorlauf_ms": band_vorlauf_ms() if band_vorlauf_aktiv() else 0,
        }
        # Die Decke haengt an der Kalibrierung: ist die deutlich gewandert,
        # Pipeline (selten) nachziehen.
        kal_jetzt = float(state.get("kalibrierung_schnell", 0.0)
                          if state.get("kalibrierung_schnell_gueltig")
                          else state.get("kalibrierung", 0.0))
        if (_band_decke.get("kal") is not None
                and abs(kal_jetzt - _band_decke["kal"]) >= BAND_DECKE_NACHFUEHR_DB
                and jetzt - _band_decke.get("zeit", 0.0) >= BAND_DECKE_NACHFUEHR_SEK):
            try:
                with lock:
                    band_anwenden(nur_parameter=True)
            except Exception as exc:
                log.warning("Decke nicht nachgefuehrt: %s", exc)
                _band_decke["zeit"] = jetzt

        gain_neu = max(-AGC_MAX, min(AGC_MAX, gain_neu))
        messung["agc_anschlag"] = abs(gain_neu) >= AGC_MAX - 0.05
        if erneuern or abs(gain_neu - gain) > GAIN_WRITE_EPS:
            # Lokalen Zustand erst NACH erfolgreichem Schreiben uebernehmen.
            try:
                with lock:
                    set_agc_gain(gain_neu)
            except Exception as exc:
                log.warning("Regelung nicht gesetzt: %s", exc)
            else:
                gain = gain_neu
                messung["agc_gain"] = round(gain, 2)



_fader_erneuern = threading.Event()   # Regelschleife: Aux1/Aux3 einmal unbedingt schreiben
_main_frei = {"ok": False}            # Main steht nach unserer Freigabe auf 0 dB
GERAET_NACHLESEN_SEK = 60.0           # Regelschleife: Konfiguration nur zur Sicherheit nachlesen
KALIB_SPEICHERN_SEK = 600.0           # Kalibrierung hoechstens alle 10 min auf die SD-Karte
NEUSTART_TIEFPRUEFUNG_SEK = 60.0      # verbinder: zusaetzlich Schwelle der Decke vergleichen


def pipeline_und_pegel_herstellen(grund):
    """Band-Pipeline, Pegel (Aux2) und die Main-Freigabe herstellen.

    Wird beim Verbinden UND nach einem erkannten CamillaDSP-Neustart
    aufgerufen. CamillaDSP startet dann wieder mit der Kaltstart-tv.yml und
    den Dienst-Startwerten (Main -35, Aux2 -35, Aux1 0) - ohne diese
    Wiederherstellung bliebe die Anlage stumm und ohne Decke (2.8.1).
    """
    neu = eingang_soll()
    if neu != _eingang["aktiv"]:
        _eingang["aktiv"] = neu
        r128_eq_neu_laden(0.5)
        vad_neustart()
    try:
        band_anwenden(neutral=bool(state.get("bypass")))
    except Exception as exc:
        log.warning("Band-Pipeline (%s) nicht gesetzt: %s", grund, exc)
    # Haupt-Lautstaerke wiederherstellen. CamillaDSP startet den Dienst mit
    # --gain=-35 --gain2=-35 (sichere leise Startlautstaerke). Wichtig: ERST
    # warten, bis die neue Config wirklich aktiv ist - direkt nach set_active
    # laeuft der Reload noch und ein Faderschreiben verpufft (1.9.23).
    ziel_vol = float(state.get("volume", -35.0))
    for _ in range(40):                # bis ~4 s auf eine aktive Config warten
        try:
            if cdsp.config.active() is not None:
                break
        except Exception:
            pass
        time.sleep(0.1)
    gesetzt = False
    for versuch in range(5):
        try:
            set_gesamtpegel(ziel_vol)
            ist = cdsp.volume.volume(PEGEL_FADER)
            if ist is not None and abs(float(ist) - ziel_vol) <= 0.6:
                gesetzt = True
                break
        except Exception as exc:
            log.warning("Lautstaerke-Versuch %d: %s", versuch + 1, exc)
        time.sleep(0.3)
    # Main erst freigeben, wenn der Aux2-Pegel nachweislich steht UND der
    # Filter "ausgang" wirklich in der aktiven Pipeline liegt. set_active()
    # laedt asynchron, direkt danach liefert config.active() noch die ALTE
    # Pipeline - deshalb bis zu 5 s darauf warten statt einmal zu pruefen
    # (2.8.0). Schlaegt etwas fehl, bleibt der sichere Startwert -35 dB.
    pipeline_da = False
    for _ in range(50):
        if ausgang_in_pipeline():
            pipeline_da = True
            break
        time.sleep(0.1)
    if gesetzt and pipeline_da:
        main_ok = False
        for versuch in range(5):
            try:
                cdsp.volume.set_main_volume(0.0)
                ist = cdsp.volume.main_volume()
                if ist is not None and abs(float(ist)) <= 0.6:
                    main_ok = True
                    break
            except Exception as exc:
                log.warning("Main-Freigabe-Versuch %d: %s", versuch + 1, exc)
            time.sleep(0.3)
        _main_frei["ok"] = main_ok
        if main_ok:
            log.info("%s: Pegel %.1f dB auf Aux2, Main auf 0 dB freigegeben", grund, ziel_vol)
        else:
            log.warning("Main konnte nicht freigegeben werden; Startwert -35 dB bleibt.")
    else:
        _main_frei["ok"] = False
        log.warning("Pegel nicht sicher gesetzt (Aux2 %s, ausgang %s); "
                    "Dienst-Startwert Main -35 dB bleibt aktiv.", gesetzt, pipeline_da)
    # Aux1 und Aux3 haelt die Regelschleife lokal nach; nach einem Neustart
    # stehen sie real auf 0 bzw. -100. Einmal unbedingt neu schreiben lassen.
    _fader_erneuern.set()


def camilladsp_neu_gestartet():
    """True, wenn CamillaDSP eine Konfiguration faehrt, die wir nicht gesetzt haben.

    Erkennungsmerkmal ist die Schwelle der Decke: band_anwenden() merkt sich
    den zuletzt gesetzten Wert, die Kaltstart-tv.yml traegt einen festen
    Platzhalter. Ein Dienstneustart von CamillaDSP (z. B. durch install.sh)
    faellt damit sofort auf - vorher lief die Anlage danach stumm weiter,
    weil pycamilladsp die WebSocket-Verbindung still neu aufbaut (2.8.1).
    """
    # Verglichen wird mit der GESENDETEN Schwelle, nicht mit der berechneten:
    # Im Bypass ist die Decke neutral (0 dB). Bis 2.8.6 galt das als
    # "fremde Konfiguration" - dann wurde alle ~3 s neu gesetzt.
    soll = _band_decke.get("gesendet")
    if soll is None:
        return False
    try:
        with lock:        # WebSocket nie parallel zur Regelschleife benutzen
            cfg = cdsp.config.active() or {}
        ist = cfg["processors"]["band_decke"]["parameters"]["threshold"]
    except Exception:
        return False
    return abs(float(ist) - float(soll)) > 0.05


def neustart_verdacht():
    """Billiger Neustart-Hinweis (0,2 ms statt ~120 ms fuer die Konfiguration).

    Im Betrieb steht Main nach unserer Freigabe auf 0 dB; nur ein Neustart von
    CamillaDSP setzt es wieder auf den Dienst-Startwert (--gain=-35). Main
    verstellt sonst niemand - der Pegel des Nutzers ist Aux2 (2.7.0).
    """
    if not _main_frei["ok"]:
        return False
    try:
        with lock:
            main = cdsp.volume.main_volume()
    except Exception:
        return False
    return main is not None and float(main) < -1.0


def verbinder():
    """Verbindet im Hintergrund und stellt Band-Pipeline und Pegel her.

    Laeuft nebenher, damit der Webserver sofort erreichbar ist - auch
    wenn noch gar keine Karte steckt. Die Oberflaeche zeigt dann an,
    woran es liegt, statt nur zu schweigen. Zusaetzlich wacht die Schleife
    darueber, ob CamillaDSP zwischendurch neu gestartet wurde.
    """
    gemeldet = None
    verdacht = 0          # Neustart nur nach zwei Pruefungen in Folge annehmen
    tief_zeit = time.monotonic()
    while True:
        try:
            if not cdsp.is_connected():
                cdsp.connect()
                log.info("Mit CamillaDSP verbunden")
                with lock:
                    pipeline_und_pegel_herstellen("Verbindung")
            else:
                # Neustart-Wache (2.8.8): alle 2 s nur die Hauptlautstaerke
                # (0,2 ms), einmal pro Minute zusaetzlich die Schwelle der Decke
                # in der Konfiguration (~120 ms) - als Rueckfall, falls Main
                # einmal nicht auf dem Startwert hochkaeme. Bis 2.8.7 wurde die
                # Konfiguration zweimal alle 2 s geholt (18 % eines Kerns).
                tief = time.monotonic() - tief_zeit >= NEUSTART_TIEFPRUEFUNG_SEK
                if tief:
                    tief_zeit = time.monotonic()
                if neustart_verdacht() or (tief and camilladsp_neu_gestartet()):
                    # Erst beim zweiten Treffer in Folge handeln: set_active()
                    # laedt asynchron, ein echter Neustart bleibt bestehen.
                    verdacht += 1
                    if verdacht >= 2:
                        verdacht = 0
                        log.warning("CamillaDSP neu gestartet (Main nicht mehr freigegeben "
                                    "bzw. fremde Konfiguration) - Pipeline und Pegel werden neu gesetzt")
                        with lock:
                            pipeline_und_pegel_herstellen("Neustart erkannt")
                    else:
                        time.sleep(2)
                        continue
                else:
                    verdacht = 0
            # Verbunden allein reicht nicht - ohne geladene Konfiguration
            # scheitert jeder Zugriff, und die Oberflaeche soll das sagen
            # statt einen Typfehler anzuzeigen. Der Verarbeitungszustand ist
            # billig; INACTIVE heisst "keine Konfiguration geladen".
            with lock:
                zustand = cdsp.general.state()
            if getattr(zustand, "name", str(zustand)).endswith("INACTIVE"):
                raise RuntimeError("keine aktive Konfiguration")
            verbindung.update(bereit=True, diagnose="", hinweis="")
            # USB-Eingang (3.0.0): gewuenschten Eingang herstellen, bei
            # fehlendem UR27/stummer Quelle auf die HiFiBerry zurueckfallen.
            soll = eingang_soll()
            if soll != _eingang["aktiv"]:
                grund = ("Einstellung" if soll == state.get("eingang")
                         else "Rueckfall: USB-Empfaenger fehlt oder tvdsp-quelle meldet sich nicht")
                with lock:
                    eingang_umschalten(soll, grund)
            if _r128_eq["flach"]:
                _r128_eq["flach"] = False
                log.info("Lautheitsmessung lief ohne EQ - Neustart mit EQ-Spiegel")
                r128_eq_neu_laden()
            gemeldet = None
            time.sleep(2)
        except Exception:
            d, h = diagnose_stellen()
            verbindung.update(bereit=False, diagnose=d, hinweis=h)
            if d != gemeldet:          # nicht im Sekundentakt ins Journal
                log.info("%s %s", d, h)
                gemeldet = d
            time.sleep(3)

# --------------------------------------------------------------- FLIRC / IR
#
# Konzept (ab 1.9.28): Der FLIRC-Stick wird DIREKT aus der Weboberflaeche
# programmiert - genau wie mit der FLIRC-GUI am PC, nur per `flirc_util` am
# Pi. Jede Aktion wird als feste Tastatur-Taste im Stick hinterlegt
# (vol_up / vol_down / mute). Der evdev-Listener reagiert auf genau diese
# Tastencodes; ein Software-Lernen ist damit ueberfluessig.
#
# Regeln, die sich aus frueheren Fehlversuchen ergeben haben:
# - Immer nur EIN flirc_util-Prozess (Lock), sonst kreuzen sich USB-Antworten.
# - Waehrend record/delete/format keine Statusabfrage an die Hardware (busy).
# - Der Listener gibt den Stick waehrend des Programmierens frei
#   (FLIRC_LEARN_FILE), damit ein "button exists"-Tastendruck nicht als
#   Lautstaerkeaktion durchlaeuft.
# - NIE automatisch loeschen. delete_index/format nur auf ausdruecklichen
#   Nutzerwunsch (1.9.19 hatte so den Stick geleert).

# Slots im Stick: Jede Fernbedienungstaste wird als generische Tastaturtaste
# (a..l) programmiert. Der Scan zeigt, welcher Slot ankommt; die Zuweisung
# Slot -> Aktion (Lauter/Leiser/Mute/Ausschalten) liegt in state["ir_keys"].
FLIRC_SLOTS = [
    # (slot-nr, flirc_util-key, evdev-code)
    (1, "a", 30), (2, "b", 48), (3, "c", 46), (4, "d", 32),
    (5, "e", 18), (6, "f", 33), (7, "g", 34), (8, "h", 35),
    (9, "i", 23), (10, "j", 36), (11, "k", 37), (12, "l", 38),
]
SLOT_NAME = {nr: key for nr, key, _ in FLIRC_SLOTS}
SLOT_CODE = {nr: code for nr, _, code in FLIRC_SLOTS}
CODE_SLOT = {code: nr for nr, _, code in FLIRC_SLOTS}
# Mediatasten, falls der Stick am PC direkt mit vol_up/vol_down/mute
# programmiert wurde: werden ohne Zuweisung als Standard erkannt.
CODE_NAME = {115: "Lauter (Medientaste)", 114: "Leiser (Medientaste)",
             113: "Mute (Medientaste)", 116: "Power (Medientaste)"}
FLIRC_RECORD_TIMEOUT = 20

IR_AKTIONEN = ("vol_up", "vol_down", "mute", "poweroff")
IR_KEYS_FILE = os.path.join(STATE_DIR, "ir-keys.json")     # {code(str): aktion}
IR_LEARN_REQ = os.path.join(STATE_DIR, "ir-learn")          # {"action":..,"ts":..}
IR_SCAN_FLAG = os.path.join(STATE_DIR, "ir-scan")           # existiert = Scan an
_ir_learn = {"action": None, "ts": 0.0, "codes": [], "last": 0.0}
_ir_lock = threading.Lock()
_ir_events = deque(maxlen=60)


def _code_label(code):
    if code in CODE_SLOT:
        return f"Taste {CODE_SLOT[code]}"
    return CODE_NAME.get(code, f"Code {code}")


def _ir_keys_laden():
    """Aktion -> Codeliste aus den Einstellungen (immer Listen)."""
    km = state.get("ir_keys") or {}
    out = {}
    for a, v in km.items():
        codes = v if isinstance(v, (list, tuple)) else [v]
        cl = [int(c) for c in codes if isinstance(c, int) or (isinstance(c, str) and c.isdigit())]
        if cl:
            out[a] = sorted(dict.fromkeys(cl))
    return out


def _ir_keys_schreiben():
    """Code -> Aktion fuer den Listener schreiben."""
    km = _ir_keys_laden()
    code_zu_aktion = {}
    for akt, codes in km.items():
        for code in codes:
            code_zu_aktion[str(code)] = akt
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = IR_KEYS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(code_zu_aktion, f)
        os.replace(tmp, IR_KEYS_FILE)
    except OSError as exc:
        log.warning("IR-Keymap nicht schreibbar: %s", exc)


def _flirc_util():
    """Pfad der offiziellen FLIRC-Kommandozeile, falls installiert."""
    for pfad in (shutil.which("flirc_util"), "/usr/local/bin/flirc_util",
                 "/usr/bin/flirc_util"):
        if pfad and os.path.isfile(pfad) and os.access(pfad, os.X_OK):
            return pfad
    return None


_flirc_lock = threading.Lock()
_flirc_busy = threading.Event()
_flirc_busy_text = {"text": ""}
_flirc_sudo_ok = {"ok": None}
_flirc_letzte_ausgabe = {"cmd": "", "text": ""}
# Live-Fortschritt fuer die Oberflaeche: Phase, Startzeit, Zeitlimit und die
# zeilenweise gestreamte flirc_util-Ausgabe (Konsole).
_flirc_prog = {"phase": "", "start": 0.0, "timeout": 0, "log": [], "aktiv": False}
_flirc_prog_lock = threading.Lock()
# Cache fuer `settings`: nicht bei jedem Status-Poll die Hardware fragen.
_flirc_cache = {"t": 0.0, "info": None, "error": ""}
FLIRC_CACHE_SEK = 30.0


def _prog(phase=None, zeile=None, aktiv=None, timeout=None):
    with _flirc_prog_lock:
        if aktiv is not None:
            _flirc_prog["aktiv"] = aktiv
            if aktiv:
                _flirc_prog["log"] = []
                _flirc_prog["start"] = time.time()
        if phase is not None:
            _flirc_prog["phase"] = phase
        if timeout is not None:
            _flirc_prog["timeout"] = timeout
        if zeile:
            _flirc_prog["log"].append({"t": round(time.time() - _flirc_prog["start"], 1),
                                       "s": zeile[:160]})
            _flirc_prog["log"] = _flirc_prog["log"][-40:]


def _flirc_cache_leeren():
    _flirc_cache.update(t=0.0, info=None, error="")


def _flirc_input_devices():
    """FLIRC-event-Geraete finden, ohne einen festen USB-Pfad anzunehmen."""
    gefunden = []
    basis = "/dev/input/by-id"
    try:
        for name in os.listdir(basis):
            low = name.lower()
            if "flirc" in low and "event" in low:
                p = os.path.join(basis, name)
                if os.path.exists(p):
                    gefunden.append(p)
    except OSError:
        pass
    if not gefunden:
        for ev in sorted(os.listdir("/sys/class/input")) if os.path.isdir("/sys/class/input") else []:
            if not ev.startswith("event"):
                continue
            try:
                name = Path(f"/sys/class/input/{ev}/device/name").read_text().strip()
            except Exception:
                continue
            if "flirc" in name.lower():
                p = f"/dev/input/{ev}"
                if os.path.exists(p):
                    gefunden.append(p)
    return sorted(dict.fromkeys(gefunden))


def _flirc_usb_da():
    """Steckt ueberhaupt ein FLIRC am USB (unabhaengig von /dev/input)?"""
    try:
        for d in os.listdir("/sys/bus/usb/devices"):
            try:
                vid = Path(f"/sys/bus/usb/devices/{d}/idVendor").read_text().strip()
            except Exception:
                continue
            if vid.lower() == "20a0":
                return True
    except OSError:
        pass
    return False


def _flirc_cmd(args):
    """Kommandozeile fuer flirc_util. Als Nicht-root ueber sudo (Regel aus
    install.sh), weil hidraw ohne udev-Regel nur root gehoert."""
    util = _flirc_util()
    if not util:
        raise RuntimeError("flirc_util ist nicht installiert")
    base = [util] + list(args)
    if os.geteuid() == 0 or _flirc_sudo_ok["ok"] is False:
        return base
    sudo = shutil.which("sudo")
    if not sudo:
        return base
    return [sudo, "-n"] + base


def _flirc_exec(cmd, timeout):
    """Prozess in eigener Sitzung starten; bei Zeitueberschreitung die GANZE
    Prozessgruppe beenden (sudo + flirc_util), sonst bleibt flirc_util
    haengen und haelt den Stick im Record-Modus."""
    p = subprocess.Popen(cmd, text=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         start_new_session=True, bufsize=1)
    zeilen = []

    def lesen():
        try:
            for line in p.stdout:
                line = line.rstrip()
                zeilen.append(line)
                if line.strip():
                    _prog(zeile=line.strip())
        except Exception:
            pass

    leser = threading.Thread(target=lesen, daemon=True)
    leser.start()
    _prog(zeile="$ flirc_util " + " ".join(str(a) for a in cmd[cmd.index(_flirc_util()) + 1:]))
    try:
        p.wait(timeout=timeout)
        leser.join(timeout=1)
        out = "\n".join(zeilen)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=2)
        except Exception:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                pass
        leser.join(timeout=1)
        out = "\n".join(zeilen)
        _prog(zeile="Zeitlimit - Prozess beendet")
        raise RuntimeError("Zeitlimit abgelaufen - keine IR-Taste empfangen"
                           + (("\n" + out.strip()) if out and out.strip() else ""))
    return p.returncode, (out or "").strip()


def _flirc_run(args, timeout=4):
    """flirc_util ausfuehren; liefert die Textausgabe oder wirft RuntimeError."""
    with _flirc_lock:
        cmd = _flirc_cmd(args)
        try:
            rc, text = _flirc_exec(cmd, timeout)
        except RuntimeError as exc:
            _flirc_letzte_ausgabe.update(cmd=" ".join(args), text=str(exc))
            raise
        low = text.lower()
        if cmd[0].endswith("sudo") and ("sudo:" in low or "password" in low):
            _flirc_sudo_ok["ok"] = False
            cmd = _flirc_cmd(args)
            rc, text = _flirc_exec(cmd, timeout)
            low = text.lower()
        elif cmd[0].endswith("sudo") and _flirc_sudo_ok["ok"] is None:
            _flirc_sudo_ok["ok"] = True
        _flirc_letzte_ausgabe.update(cmd=" ".join(args), text=text or f"(keine Ausgabe, rc={rc})")
    fehlerwort = any(w in low for w in ("error", "failed", "not found",
                                        "no flirc", "permission", "unable",
                                        "invalid"))
    erfolg = any(w in low for w in ("successfully", "succesfully", "done"))
    if fehlerwort and not erfolg:
        raise RuntimeError(text or f"flirc_util Fehler {rc}")
    if rc != 0 and not erfolg and not text:
        raise RuntimeError(f"flirc_util Fehler {rc}")
    return text


def _flirc_pairs(text):
    """Recorded-Keys-Tabelle aus `flirc_util settings` robust extrahieren."""
    out = []
    # Gen1:  Index  hash      key
    # Gen2:  Index  hash      IK   ID  key
    # Der Tastenname steht immer im LETZTEN Feld.
    for line in (text or "").splitlines():
        teile = line.split()
        if len(teile) < 3 or not teile[0].isdigit():
            continue
        if not re.fullmatch(r"(?:0x)?[0-9A-Fa-f]{4,}", teile[1]):
            continue
        out.append({"index": int(teile[0]), "hash": teile[1], "key": teile[-1]})
    return out


def _flirc_slot_von_key(key):
    k = (key or "").strip().lower()
    for nr, name, _ in FLIRC_SLOTS:
        if k == name:
            return nr
    return None


def _flirc_settings(frisch=False):
    """settings lesen (gecacht): Firmware, Speicher, Eintraege je Slot."""
    if not frisch and _flirc_cache["info"] and time.time() - _flirc_cache["t"] < FLIRC_CACHE_SEK:
        return _flirc_cache["info"]
    try:
        txt = _flirc_run(["settings"], timeout=6)
    except Exception as exc:
        _flirc_cache.update(t=time.time(), info=None, error=str(exc))
        raise
    fw = ""
    m = re.search(r"FW Version:\s*(\S+)", txt)
    if m:
        fw = m.group(1)
    sku = ""
    m = re.search(r"SKU:\s*(.+)", txt)
    if m:
        sku = m.group(1).strip()
    recorded = None
    m = re.search(r"keys recorded:\s*(\d+)", txt)
    if m:
        recorded = int(m.group(1))
    pairs = _flirc_pairs(txt)
    slots = {nr: [] for nr, _, _ in FLIRC_SLOTS}
    andere = []
    for pr in pairs:
        nr = _flirc_slot_von_key(pr["key"])
        if nr:
            slots[nr].append({"index": pr["index"], "hash": pr["hash"]})
        else:
            andere.append(pr)
    info = {"firmware": fw, "sku": sku, "recorded": recorded,
            "slots": slots, "andere": andere, "roh": txt}
    _flirc_cache.update(t=time.time(), info=info, error="")
    return info


def _flirc_slot_indizes(nr):
    try:
        return [k["index"] for k in _flirc_settings(frisch=True)["slots"].get(nr, [])]
    except Exception:
        return []


def _flirc_service_active():
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    try:
        p = subprocess.run([systemctl, "is-active", "--quiet", "tvdsp-flirc.service"],
                           timeout=2, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return p.returncode == 0
    except Exception:
        return False


def _flirc_setup_active():
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    try:
        p = subprocess.run([systemctl, "is-active", "--quiet",
                            "tvdsp-flirc-setup.service"],
                           timeout=2, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return p.returncode == 0
    except Exception:
        return False


def _flirc_learning(an):
    try:
        if an:
            Path(FLIRC_LEARN_FILE).touch()
        elif os.path.exists(FLIRC_LEARN_FILE):
            os.unlink(FLIRC_LEARN_FILE)
    except OSError:
        pass


class _FlircExklusiv:
    """Kontext fuer record/delete/format: busy-Flag, Listener freigeben."""
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        if _flirc_busy.is_set():
            raise RuntimeError("Der Stick ist gerade beschaeftigt - bitte kurz warten")
        _flirc_busy.set()
        _flirc_busy_text["text"] = self.text
        _prog(aktiv=True, phase="Lauscher gibt den Stick frei")
        _flirc_learning(True)
        time.sleep(0.8)   # Listener gibt das evdev-Geraet frei
        _prog(phase=self.text)
        return self

    def __exit__(self, *exc):
        _flirc_learning(False)
        _flirc_busy_text["text"] = ""
        _flirc_cache_leeren()
        _prog(aktiv=False, phase="")
        _flirc_busy.clear()
        return False


def _flirc_save_spaeter():
    """Beim Gedrueckthalten nicht achtmal pro Sekunde auf die SD-Karte schreiben."""
    global _flirc_save_timer
    def speichern():
        global _flirc_save_timer
        with lock:
            save_settings()
            _flirc_save_timer = None
    with lock:
        if _flirc_save_timer is not None:
            _flirc_save_timer.cancel()
        _flirc_save_timer = threading.Timer(1.2, speichern)
        _flirc_save_timer.daemon = True
        _flirc_save_timer.start()

# ------------------------------------------------------------------- API

# Die Oberflaeche wird oft geaendert. Ohne diese Kopfzeilen liefert der
# Browser - besonders als Symbol auf dem Startbildschirm - hartnaeckig
# die alte Fassung aus, und man sucht den Fehler an der falschen Stelle.
@app.after_request
def nicht_zwischenspeichern(antwort):
    if antwort.mimetype in ("text/html", "application/json"):
        antwort.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        antwort.headers["Pragma"] = "no-cache"
        antwort.headers["Expires"] = "0"
    return antwort


@app.route("/")
def index():
    antwort = send_from_directory(app.static_folder, "index.html")
    antwort.headers["Cache-Control"] = "no-store, must-revalidate, max-age=0"
    return antwort


@app.route("/api/version")
def api_version():
    """Zeigt, welche Fassung der Oberflaeche wirklich ausgeliefert wird.
    Hilft, Cache-Probleme von echten Fehlern zu unterscheiden."""
    pfad = os.path.join(app.static_folder, "index.html")
    try:
        st = os.stat(pfad)
        return jsonify({
            "ok": True,
            "version": VERSION,
            "geaendert": time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(st.st_mtime)),
            "groesse": st.st_size,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

@app.route("/api/status")
def api_status():
    with lock:
        vol = float(state.get("volume", -35.0))
        # Der reale DSP-Zustand gewinnt (Regel aus 1.7.1): so zieht die
        # Oberflaeche nach, wenn die Fernbedienung oder ein zweites Geraet
        # den Pegel verstellt hat. Seit 2.7.0 ist das der Aux2-Fader.
        # ... aber NUR im normalen Betrieb, also wenn Main bereits auf 0 dB
        # freigegeben ist. Nach einem CamillaDSP-Neustart steht Aux2 auf dem
        # sicheren Dienst-Startwert (-35 dB); den als Nutzerwunsch zu
        # uebernehmen wuerde den gespeicherten Pegel loeschen (2.8.1).
        try:
            main_jetzt = cdsp.volume.main_volume()
            echt = cdsp.volume.volume(PEGEL_FADER) if (
                main_jetzt is not None and abs(float(main_jetzt)) <= 0.6) else None
        except Exception:
            echt = None
        if echt is not None and abs(float(echt) - vol) > 0.3:
            vol = clamp("volume", float(echt))
            state["volume"] = vol
        try:
            mute = cdsp.volume.main_mute()
        except Exception:
            mute = None
    abgeleitet = {
        "typ": "band-decke",
        "decke_dbfs": _band_decke.get("dbfs"),
        "decke_lufs": band_oben() + BAND_DECKE_MARGE,
        "rauschsperre": band_rauschsperre(),
        "vorlauf_ms": band_vorlauf_ms() if band_vorlauf_aktiv() else 0,
    }
    # EQ-Gains mitliefern, damit ein zweites Geraet die Klangkurve live
    # nachziehen kann. Massgeblich ist der gespeicherte Zustand; im Bypass
    # steht der DSP flach, die Oberflaeche soll trotzdem die Kurve zeigen.
    eq_gains = list(state.get("eq", [0.0] * 16))

    # Pegel-Hinweis: naehert sich der Regler dem oberen Anschlag (0 dB), ist
    # der einzige verbleibende Weg zu mehr Lautstaerke der Verstaerker.
    try:
        vol_jetzt = float(state.get("volume", -35.0))
    except (TypeError, ValueError):
        vol_jetzt = -35.0
    pegel_hinweis = None
    if not state.get("bypass") and state.get("agc_aktiv") and vol_jetzt >= -5.0:
        am_max = vol_jetzt >= -0.5
        pegel_hinweis = {
            "art": "pegel",
            "titel": ("Pegel am Maximum (0 dB)" if am_max
                      else "Pegel fast am Maximum"),
            "text": ("Der Pegel geht nicht über 0 dB – mehr Lautstärke gibt es auf "
                     "zwei Wegen: den Verstärker/die Anlage aufdrehen, oder im Reiter "
                     "Band die Ober- und Unterkante um denselben Betrag Richtung 0 dB "
                     "schieben (z. B. von −34/−28 auf −26/−20 = 8 dB lauter). Dann "
                     "regelt die Box selbst lauter, und der Pegel kann wieder runter. "
                     "Empfehlung: Oberkante nicht über etwa −20, sonst arbeitet der "
                     "Limiter dauernd und der Klang wird hart."),
        }
    return jsonify({
        "ok": True,
        "version": VERSION,
        "cpu": cpu_last_prozent(),
        "eingang": eingang_kurz(),
        "band_settings": band_settings_dict(),
        "band": messung.get("band"),
        "abgeleitet": abgeleitet,
        "dsp_profil": dict(dsp_profil, soll=DSP_PROFIL_SOLL),
        "bereit": verbindung["bereit"],
        "diagnose": verbindung["diagnose"],
        "hinweis": verbindung["hinweis"],
        "pegel_hinweis": pegel_hinweis,
        "spitze_grenze": -2.0,
        "messung": messung,
        "agc_aktiv": state["agc_aktiv"],
        "bypass": state["bypass"],
        "hoehen_daempfung": bool(state.get("hoehen_daempfung")),
        "hoehen": {"daempfung_db": round(_hoehen["daempfung"], 1),
                   "anteil_db": None if _hoehen["anteil"] is None else round(_hoehen["anteil"], 1)},
        "spread": state["spread"],
        "volume": vol,
        "mute": mute,
        "baender": BANDS,
        "eq_gains": eq_gains,
    })



def _fehler(exc):
    if not verbindung["bereit"]:
        return jsonify({"ok": False, "error": verbindung["diagnose"]}), 503
    return jsonify({"ok": False, "error": str(exc)}), 503



# ---------------------------------------------------------------------------
# TV-Mitschnitt: ein eigener, vom Messpfad unabhaengiger ffmpeg-Prozess liest
# denselben ALSA-Eingang (dsnoop erlaubt mehrere Leser) und kodiert live nach
# AAC (.m4a) oder OGG/Vorbis (.ogg). Zweck: echtes Sendematerial fuer die
# spaetere Offline-Analyse aufzeichnen. Der Ton-/Messpfad bleibt unberuehrt.
def _standard_rec_dir():
    """Sinnvoller, fuer den Pi-Benutzer sichtbarer Ablageort.

    Reihenfolge: fester REC_DIR aus der Umgebung, sonst der Download-Ordner
    des Benutzers, unter dem der Dienst laeuft (dort erwartet man Dateien und
    kommt per Dateimanager/SSH direkt dran), sonst ein klar benannter Ordner
    'tvdsp_captures' im Home. Der Home wird ueber HOME bzw. den Dienst-User
    ermittelt (systemd setzt HOME auf das Home des User=).
    """
    fest = os.environ.get("REC_DIR")
    if fest:
        return fest
    home = os.environ.get("HOME")
    if not home or home == "/":
        try:
            import pwd
            home = pwd.getpwuid(os.getuid()).pw_dir
        except Exception:
            home = None
    if home and os.path.isdir(home):
        dl = os.path.join(home, "Downloads")
        if os.path.isdir(dl):
            return dl
        # Kein Downloads-Ordner: eigener, klar sichtbarer Ordner im Home.
        return os.path.join(home, "tvdsp_captures")
    # Letzter Ausweg, falls kein Home ermittelbar ist.
    return os.path.join(os.path.dirname(STATE_DIR), "aufnahmen")


REC_DIR = _standard_rec_dir()
# Sicherheits-Obergrenze fuer eine einzelne Aufnahme (2 Stunden). ffmpeg
# beendet sich danach selbst und finalisiert die Datei.
REC_MAX_SEK = 2 * 60 * 60
_rec_lock = threading.Lock()
_rec = {"proc": None, "datei": None, "start": 0.0, "format": None, "bitrate": None}
_REC_NAME_RE = re.compile(r"^tvdsp_\d{8}_\d{6}\.mp3$")


def _rec_laeuft():
    p = _rec["proc"]
    return p is not None and p.poll() is None


def aufnahme_starten(bitrate=160):
    """Startet den Mitschnitt-ffmpeg als MP3.

    MP3 ist fuer diesen Zweck das robusteste Format: kein Container und kein
    abschliessender Header - jeder Frame ist eigenstaendig. Wird ffmpeg hart
    beendet (Absturz, Stromausfall, Kill), ist die Datei trotzdem bis zum
    letzten vollstaendigen Frame (~26 ms) gueltig und ueberall abspielbar.
    Es ist keinerlei Finalisierung noetig. Fuer Lautheits-/Pegelanalyse ist
    die verlustbehaftete Kompression bei 160 kbps voellig ausreichend.
    """
    if shutil.which("ffmpeg") is None:
        return False, "ffmpeg fehlt"
    try:
        br = int(bitrate)
    except (TypeError, ValueError):
        br = 160
    br = max(96, min(320, br))
    with _rec_lock:
        if _rec_laeuft():
            return False, "Aufnahme laeuft bereits"
        try:
            os.makedirs(REC_DIR, exist_ok=True)
        except OSError as exc:
            return False, "Verzeichnis nicht anlegbar: %s" % exc
        stamp = time.strftime("%Y%m%d_%H%M%S")
        datei = os.path.join(REC_DIR, "tvdsp_%s.mp3" % stamp)
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error",
               "-f", "alsa", "-channels", "2", "-sample_rate", "48000",
               "-i", mess_geraet(),
               # Sicherheits-Automatik: nach REC_MAX_SEK endet ffmpeg selbst und
               # finalisiert die Datei sauber. Verhindert versehentliche
               # Endlos-Aufnahmen, die die SD-Karte fuellen.
               "-t", str(REC_MAX_SEK),
               "-c:a", "libmp3lame", "-b:a", "%dk" % br, datei]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True)
        except Exception as exc:
            return False, "ffmpeg-Start fehlgeschlagen: %s" % exc
        # Kurz pruefen, ob ffmpeg nicht sofort scheitert (z.B. Geraet belegt).
        time.sleep(0.4)
        if proc.poll() is not None:
            fehler = ""
            try:
                fehler = (proc.stderr.read() or "").strip().splitlines()[-1]
            except Exception:
                pass
            return False, "ffmpeg beendet sofort: %s" % (fehler or "unbekannt")
        _rec.update(proc=proc, datei=datei, start=time.monotonic(),
                    format="mp3", bitrate=br)
        log.info("Mitschnitt gestartet: %s (mp3 %dk)", datei, br)
        return True, os.path.basename(datei)


def aufnahme_stoppen():
    """Beendet den Mitschnitt und stellt sicher, dass die Datei gueltig ist.

    Ablauf: 'q' auf stdin beendet ffmpeg regulaer. Da AAC als fragmentiertes
    MP4 und OGG als Stream geschrieben werden, ist die Datei ohnehin jederzeit
    abspielbar; das saubere Beenden schliesst nur den letzten Block ab. Wir
    warten auf das Prozessende und bestaetigen anschliessend eine nicht-leere
    Datei, bevor die HTTP-Antwort zurueckgeht - so ist der Download am Handy
    sofort funktionsfaehig.
    """
    with _rec_lock:
        proc = _rec["proc"]
        if proc is None or proc.poll() is not None:
            _rec.update(proc=None, datei=None)
            return False, "keine laufende Aufnahme"
        datei = _rec["datei"]
        try:
            if proc.stdin:
                proc.stdin.write("q")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
        _rec.update(proc=None, datei=None, start=0.0)
        groesse = 0
        try:
            groesse = os.path.getsize(datei) if datei else 0
        except OSError:
            groesse = 0
        name = os.path.basename(datei) if datei else None
        log.info("Mitschnitt beendet: %s (%d Bytes)", datei, groesse)
        if groesse <= 0:
            return False, "Aufnahme leer (kein Eingangssignal?)"
        return True, name


def aufnahme_liste():
    """Vorhandene Mitschnitte mit Groesse/Datum, neueste zuerst."""
    out = []
    try:
        for n in os.listdir(REC_DIR):
            if not _REC_NAME_RE.match(n):
                continue
            pfad = os.path.join(REC_DIR, n)
            try:
                st = os.stat(pfad)
            except OSError:
                continue
            out.append({"name": n, "groesse": st.st_size, "zeit": st.st_mtime})
    except FileNotFoundError:
        pass
    out.sort(key=lambda z: z["zeit"], reverse=True)
    return out


@app.route("/api/aufnahme/status", methods=["GET"])
def api_aufnahme_status():
    laeuft = _rec_laeuft()
    dauer = (time.monotonic() - _rec["start"]) if laeuft else 0.0
    aktuell = None
    if laeuft and _rec["datei"]:
        aktuell = os.path.basename(_rec["datei"])
        try:
            groesse = os.path.getsize(_rec["datei"])
        except OSError:
            groesse = 0
    else:
        groesse = 0
    return jsonify({"ok": True, "laeuft": laeuft, "dauer": round(dauer, 1),
                    "aktuell": aktuell, "groesse": groesse,
                    "format": _rec["format"], "bitrate": _rec["bitrate"],
                    "ordner": REC_DIR, "max_sek": REC_MAX_SEK,
                    "dateien": aufnahme_liste()})


@app.route("/api/aufnahme/start", methods=["POST"])
def api_aufnahme_start():
    daten = request.get_json(silent=True) or {}
    ok, info = aufnahme_starten(daten.get("bitrate", 160))
    return (jsonify({"ok": True, "datei": info}) if ok
            else (jsonify({"ok": False, "error": info}), 400))


@app.route("/api/aufnahme/stop", methods=["POST"])
def api_aufnahme_stop():
    ok, info = aufnahme_stoppen()
    return (jsonify({"ok": True, "datei": info}) if ok
            else (jsonify({"ok": False, "error": info}), 400))


@app.route("/api/aufnahme/download/<name>", methods=["GET"])
def api_aufnahme_download(name):
    if not _REC_NAME_RE.match(name or ""):
        return jsonify({"ok": False, "error": "ungueltiger Name"}), 400
    return send_from_directory(REC_DIR, name, as_attachment=True)


@app.route("/api/aufnahme/delete/<name>", methods=["POST"])
def api_aufnahme_delete(name):
    if not _REC_NAME_RE.match(name or ""):
        return jsonify({"ok": False, "error": "ungueltiger Name"}), 400
    if _rec_laeuft() and _rec["datei"] and os.path.basename(_rec["datei"]) == name:
        return jsonify({"ok": False, "error": "Aufnahme laeuft noch"}), 400
    try:
        os.remove(os.path.join(REC_DIR, name))
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "nicht gefunden"}), 404
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


def _system_aktion_ausfuehren(aktion):
    """Reboot/Poweroff leicht verzoegert, damit die HTTP-Antwort rausgeht."""
    time.sleep(0.7)
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    if os.geteuid() == 0:
        cmd = [systemctl, aktion]
    else:
        sudo = shutil.which("sudo") or "/usr/bin/sudo"
        cmd = [sudo, "-n", systemctl, aktion]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception as exc:
        log.error("Systemaktion %s fehlgeschlagen: %s", aktion, exc)


@app.route("/api/system/install", methods=["POST"])
def api_system_install():
    """install.sh aus der Paketkopie starten (prueft/flasht DSP-Profil, tv.yml, Dienste)."""
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return jsonify({"ok": False, "error": "Systemaktion nur same-origin"}), 403
    try:
        install_starten()
        return jsonify({"ok": True})
    except Exception as exc:
        return _fehler(exc)


@app.route("/api/system/install/status")
def api_system_install_status():
    zeilen = []
    try:
        with open(INSTALL_LOG, "r", errors="replace") as fh:
            zeilen = fh.read().splitlines()[-80:]
    except OSError:
        pass
    # ANSI-Farben des Installers entfernen
    zeilen = [re.sub(r"\x1b\[[0-9;]*m", "", z) for z in zeilen]
    laeuft = install_laeuft()
    fertig = (not laeuft) and any(z.strip().startswith("Fertig") for z in zeilen)
    return jsonify({"ok": True, "laeuft": laeuft, "fertig": fertig,
                    "paket": os.path.exists(INSTALL_PAKET), "log": zeilen})


@app.route("/api/system/<aktion>", methods=["POST"])
def api_system(aktion):
    """Nur zwei fest verdrahtete Systemaktionen, keine Shell-Eingabe."""
    if aktion not in ("reboot", "poweroff"):
        return jsonify({"ok": False, "error": "unbekannte Systemaktion"}), 400

    # Zusaetzlicher Browser-Schutz: eine fremde Webseite soll nicht per
    # Cross-Site-POST den Pi ausschalten koennen. Direkte lokale/API-Calls
    # ohne Sec-Fetch-Site bleiben moeglich; Browser muessen same-origin sein.
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return jsonify({"ok": False, "error": "Systemaktion nur same-origin"}), 403

    threading.Thread(target=_system_aktion_ausfuehren, args=(aktion,),
                     daemon=True).start()
    return jsonify({"ok": True, "aktion": aktion})



@app.route("/api/flirc/status")
def api_flirc_status():
    util = _flirc_util()
    devices = _flirc_input_devices()
    usb = bool(devices) or _flirc_usb_da()
    info = {"firmware": "", "sku": "", "recorded": None,
            "slots": {nr: [] for nr, _, _ in FLIRC_SLOTS}, "andere": []}
    hw_error = ""
    hw_ok = False
    frisch = request.args.get("refresh") == "1"
    if _flirc_busy.is_set():
        if _flirc_cache["info"]:
            info = _flirc_cache["info"]
            hw_ok = True
    elif util and usb:
        try:
            info = _flirc_settings(frisch=frisch)
            hw_ok = True
        except Exception as exc:
            hw_error = str(exc)
    km = _ir_keys_laden()
    with _ir_lock:
        learning = _ir_learn["action"] if (
            _ir_learn["action"] and time.time() - _ir_learn["ts"] <= 30) else None
    return jsonify({
        "ok": True,
        "cli_ok": bool(util),
        "setup_active": _flirc_setup_active(),
        "usb": usb,
        "devices": devices,
        "listener": _flirc_service_active(),
        "busy": _flirc_busy.is_set(),
        "busy_text": _flirc_busy_text["text"],
        "hw_ok": hw_ok,
        "hw_error": hw_error,
        "firmware": info.get("firmware", ""),
        "sku": info.get("sku", ""),
        "recorded": info.get("recorded"),
        "slots": [{"nr": nr, "key": key, "code": code,
                   "eintraege": info["slots"].get(nr, [])}
                  for nr, key, code in FLIRC_SLOTS],
        "andere": [{"index": p["index"], "key": p["key"]} for p in info.get("andere") or []],
        "zuweisung": {a: [{"code": c, "label": _code_label(c)} for c in km.get(a, [])]
                      for a in IR_AKTIONEN},
        "learning": learning,
        "scan": os.path.exists(IR_SCAN_FLAG),
        "letzte_ausgabe": dict(_flirc_letzte_ausgabe),
        "step": state.get("flirc_step", 1.0),
        "last_action": flirc_last.get("action"),
        "last_time": flirc_last.get("zeit", 0.0),
    })


@app.route("/api/flirc/progress")
def api_flirc_progress():
    """Live-Fortschritt fuer die Oberflaeche (record/delete/format und
    Zuweisung): Phase, verstrichene Zeit, Zeitlimit, Konsolenzeilen."""
    with _flirc_prog_lock:
        pr = dict(_flirc_prog)
        pr["log"] = list(pr["log"])
    with _ir_lock:
        lern = None
        if _ir_learn["action"] and time.time() - _ir_learn["ts"] <= 30:
            lern = {"action": _ir_learn["action"],
                    "elapsed": round(time.time() - _ir_learn["ts"], 1),
                    "codes": [_code_label(c) for c in _ir_learn["codes"]],
                    "seit_letztem": round(time.time() - _ir_learn["last"], 1) if _ir_learn["last"] else None}
    return jsonify({"ok": True, "aktiv": pr["aktiv"], "phase": pr["phase"],
                    "elapsed": round(time.time() - pr["start"], 1) if pr["aktiv"] else 0,
                    "timeout": pr["timeout"], "log": pr["log"], "learn": lern})


@app.route("/api/flirc/step", methods=["POST"])
def api_flirc_step():
    daten = request.get_json(force=True) or {}
    try:
        step = clamp("flirc_step", daten.get("step", 1.0))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with lock:
        state["flirc_step"] = step
        save_settings()
    return jsonify({"ok": True, "step": step})


@app.route("/api/flirc/record/<int:nr>", methods=["POST"])
def api_flirc_record(nr):
    """Eine Fernbedienungstaste in Slot nr programmieren (flirc_util record).

    Wartet bis zu 20 s auf die IR-Taste. Ein zweiter Aufruf fuer denselben
    Slot nimmt den zweiten Toggle-Code auf. "button exists" = dieser IR-Code
    ist bereits gespeichert (kein Fehler).
    """
    key = SLOT_NAME.get(nr)
    if not key:
        return jsonify({"ok": False, "error": "unbekannter Slot"}), 400
    if not _flirc_util():
        return jsonify({"ok": False, "error": "flirc_util ist nicht installiert"}), 503
    try:
        with _FlircExklusiv(f"Warte auf Taste {nr}"):
            _prog(phase=f"Jetzt Fernbedienungstaste fuer Taste {nr} druecken",
                  timeout=FLIRC_RECORD_TIMEOUT)
            try:
                text = _flirc_run(["record", key], timeout=FLIRC_RECORD_TIMEOUT)
                exists = False
            except RuntimeError as exc:
                if "exists" in str(exc).lower():
                    text = str(exc)
                    exists = True
                else:
                    raise
            _prog(phase="Pruefe Eintraege im Stick", timeout=0)
            anzahl = len(_flirc_slot_indizes(nr))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "ausgabe": _flirc_letzte_ausgabe.get("text", "")}), 503
    return jsonify({"ok": True, "slot": nr, "ausgabe": text,
                    "exists": exists, "anzahl": anzahl})


@app.route("/api/flirc/delete/<int:nr>", methods=["POST"])
def api_flirc_delete(nr):
    """Alle Eintraege eines Slots aus dem Stick entfernen (delete_index,
    absteigend, weil der Stick nach jedem Loeschen neu durchnummeriert)."""
    if nr not in SLOT_NAME:
        return jsonify({"ok": False, "error": "unbekannter Slot"}), 400
    if not _flirc_util():
        return jsonify({"ok": False, "error": "flirc_util ist nicht installiert"}), 503
    try:
        with _FlircExklusiv("Loesche Eintraege"):
            idx = sorted(_flirc_slot_indizes(nr), reverse=True)
            entfernt = 0
            fehler = ""
            for i in idx:
                try:
                    _flirc_run(["delete_index", str(i)], timeout=6)
                    entfernt += 1
                except Exception as exc:
                    fehler = str(exc)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    if fehler and not entfernt:
        return jsonify({"ok": False, "error": fehler}), 503
    # Zuweisungen dieses Slots ebenfalls entfernen.
    code = SLOT_CODE[nr]
    with lock:
        km = dict(state.get("ir_keys") or {})
        for a in list(km):
            v = km[a] if isinstance(km[a], list) else [km[a]]
            v = [c for c in v if int(c) != code]
            if v:
                km[a] = v
            else:
                km.pop(a)
        state["ir_keys"] = km
        _save(SETTINGS_FILE, state)
        _ir_keys_schreiben()
    return jsonify({"ok": True, "slot": nr, "entfernt": entfernt})


@app.route("/api/flirc/format", methods=["POST"])
def api_flirc_format():
    """Kompletten Stick leeren. Nur auf ausdrueckliche Bestaetigung."""
    daten = request.get_json(silent=True) or {}
    if daten.get("confirm") != "FORMAT":
        return jsonify({"ok": False, "error": "Bestaetigung fehlt"}), 400
    if not _flirc_util():
        return jsonify({"ok": False, "error": "flirc_util ist nicht installiert"}), 503
    try:
        with _FlircExklusiv("Leere Stick"):
            text = _flirc_run(["format"], timeout=10)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    with lock:
        state["ir_keys"] = {}
        _save(SETTINGS_FILE, state)
        _ir_keys_schreiben()
    return jsonify({"ok": True, "ausgabe": text})


# ---- Bereich 2: Scan (Listener meldet jedes Tastenereignis)

@app.route("/api/ir/scan/start", methods=["POST"])
def api_ir_scan_start():
    _ir_events.clear()
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(IR_SCAN_FLAG, "w") as f:
            f.write(str(time.time()))
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/api/ir/scan/stop", methods=["POST"])
def api_ir_scan_stop():
    try:
        os.remove(IR_SCAN_FLAG)
    except OSError:
        pass
    return jsonify({"ok": True})


@app.route("/api/ir/scan", methods=["GET"])
def api_ir_scan():
    km = _ir_keys_laden()
    code_zu_aktion = {}
    for a, codes in km.items():
        for c in codes:
            code_zu_aktion[c] = a
    ev = [{"code": e["code"], "label": _code_label(e["code"]), "value": e["value"],
           "device": e["device"], "t": e["t"], "aktion": code_zu_aktion.get(e["code"])}
          for e in list(_ir_events)]
    return jsonify({"ok": True, "active": os.path.exists(IR_SCAN_FLAG), "events": ev})


@app.route("/api/ir/event", methods=["POST"])
def api_ir_event():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"ok": False, "error": "nur lokal"}), 403
    d = request.get_json(silent=True) or {}
    try:
        code = int(d.get("code"))
        value = int(d.get("value"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "ungueltig"}), 400
    _ir_events.append({"code": code, "value": value,
                       "device": str(d.get("device", ""))[:80], "t": time.time()})
    return jsonify({"ok": True})


# ---- Bereich 3: Zuweisung Slot/Code -> Aktion

@app.route("/api/ir/learn/<aktion>", methods=["POST"])
def api_ir_learn(aktion):
    """Naechste(n) gedrueckte(n) Taste(n) der Aktion zuordnen. Sammelt bis
    ~4 s nach dem letzten neuen Code (max. 20 s), damit beide Toggle-Codes
    bzw. beide Slots einer Taste erfasst werden."""
    if aktion not in IR_AKTIONEN:
        return jsonify({"ok": False, "error": "unbekannte Aktion"}), 400
    ts = time.time()
    with _ir_lock:
        _ir_learn.update(action=aktion, ts=ts, codes=[], last=0.0)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(IR_LEARN_REQ, "w") as f:
            json.dump({"action": aktion, "ts": ts}, f)
    except OSError as exc:
        return jsonify({"ok": False, "error": "Anfrage nicht schreibbar: %s" % exc}), 500
    ende = ts + 20.0
    while time.time() < ende:
        with _ir_lock:
            codes = list(_ir_learn["codes"]) if _ir_learn["action"] == aktion else []
            last = _ir_learn["last"]
        if codes and (time.time() - last) > 4.0:
            break
        time.sleep(0.15)
    try:
        os.remove(IR_LEARN_REQ)
    except OSError:
        pass
    with _ir_lock:
        codes = sorted(dict.fromkeys(_ir_learn["codes"])) if _ir_learn["action"] == aktion else []
        _ir_learn.update(action=None, codes=[])
    if not codes:
        return jsonify({"ok": False, "error": "Zeitlimit - keine Taste empfangen"}), 408
    with lock:
        km = dict(state.get("ir_keys") or {})
        for a in list(km):
            if a == aktion:
                continue
            v = km[a] if isinstance(km[a], list) else [km[a]]
            v = [c for c in v if int(c) not in codes]
            if v:
                km[a] = v
            else:
                km.pop(a)
        km[aktion] = codes
        state["ir_keys"] = km
        _save(SETTINGS_FILE, state)
        _ir_keys_schreiben()
    return jsonify({"ok": True, "aktion": aktion, "codes": codes,
                    "labels": [_code_label(c) for c in codes]})


@app.route("/api/ir/learned", methods=["POST"])
def api_ir_learned():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"ok": False, "error": "nur lokal"}), 403
    daten = request.get_json(silent=True) or {}
    aktion = daten.get("action")
    code = daten.get("code")
    if aktion not in IR_AKTIONEN or not isinstance(code, int):
        return jsonify({"ok": False, "error": "ungueltig"}), 400
    with _ir_lock:
        if _ir_learn["action"] == aktion:
            if code not in _ir_learn["codes"]:
                _ir_learn["codes"].append(code)
            _ir_learn["last"] = time.time()
    return jsonify({"ok": True})


@app.route("/api/ir/clear", methods=["POST"])
def api_ir_clear():
    daten = request.get_json(silent=True) or {}
    aktion = daten.get("action")
    with lock:
        km = dict(state.get("ir_keys") or {})
        if aktion in IR_AKTIONEN:
            km.pop(aktion, None)
        else:
            km = {}
        state["ir_keys"] = km
        _save(SETTINGS_FILE, state)
        _ir_keys_schreiben()
    return jsonify({"ok": True})


def mute_aufheben(grund):
    """Stumm aus, wenn die Lautstaerke veraendert wird (3.3.7, Nutzerwunsch):
    sonst wundert man sich, warum es bei Lauter nicht lauter wird."""
    try:
        if cdsp.volume.main_mute():
            cdsp.volume.set_main_mute(False)
            log.info("Stumm aufgehoben (%s)", grund)
    except Exception as exc:
        log.debug("Stumm nicht aufgehoben: %s", exc)


@app.route("/api/flirc/action/<aktion>", methods=["POST"])
def api_flirc_action(aktion):
    """Nur fuer den lokalen evdev-Lauscher. Keine frei waehlenbaren Aktionen."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"ok": False, "error": "FLIRC-Aktion nur lokal"}), 403
    if aktion not in IR_AKTIONEN:
        return jsonify({"ok": False, "error": "unbekannte FLIRC-Aktion"}), 400
    if aktion == "poweroff":
        # Der Listener meldet das erst nach 1,5 s Halten der Taste.
        flirc_last["action"] = aktion
        flirc_last["zeit"] = time.time()
        log.info("IR: Ausschalten per Fernbedienung")
        threading.Thread(target=_system_aktion_ausfuehren, args=("poweroff",),
                         daemon=True).start()
        return jsonify({"ok": True, "aktion": aktion})
    with lock:
        try:
            if aktion in ("vol_up", "vol_down"):
                aktuell = state.get("volume")
                if aktuell is None:
                    aktuell = lies_gesamtpegel()
                aktuell = float(aktuell)
                delta = state.get("flirc_step", 1.0) * (1 if aktion == "vol_up" else -1)
                neu = clamp("volume", aktuell + delta)
                set_gesamtpegel(neu)
                mute_aufheben("Fernbedienung " + ("lauter" if aktion == "vol_up" else "leiser"))
                _flirc_save_spaeter()
            else:
                try:
                    mute = bool(cdsp.volume.main_mute())
                except Exception:
                    mute = False
                cdsp.volume.set_main_mute(not mute)
                neu = not mute
            flirc_last["action"] = aktion
            flirc_last["zeit"] = time.time()
            return jsonify({"ok": True, "aktion": aktion,
                            "volume": state.get("volume"),
                            "mute": neu if aktion == "mute" else None})
        except Exception as exc:
            return _fehler(exc)

@app.route("/api/eq", methods=["GET", "POST"])
def api_eq():
    with lock:
        try:
            if request.method == "POST":
                daten = request.get_json(force=True) or {}
                if daten.get("spread") in SPREAD:
                    state["spread"] = daten["spread"]
                    save_settings_spaeter()
                if "gains" in daten:
                    set_eq(daten["gains"], "Oberflaeche %s" % request.remote_addr)
            return jsonify({"ok": True, "gains": list(state.get("eq", [0.0] * 16)),
                            "spread": state["spread"], "baender": BANDS})
        except Exception as exc:
            return _fehler(exc)


@app.route("/api/schalter", methods=["POST"])
def api_schalter():
    daten = request.get_json(force=True) or {}
    with lock:
        try:
            if "agc_aktiv" in daten:
                state["agc_aktiv"] = bool(daten["agc_aktiv"])
                if not state["agc_aktiv"]:
                    set_agc_gain(0.0)
                    messung["agc_gain"] = 0.0
            if "bypass" in daten:
                state["bypass"] = bool(daten["bypass"])
                # Bypass = Decke 1:1, EQ flach, kein Loudness-Pfad; der Aux1-
                # Gain bleibt stehen, damit der A/B-Vergleich gleich laut ist.
                band_anwenden(neutral=state["bypass"])
            if "hoehen_daempfung" in daten:
                neu = bool(daten["hoehen_daempfung"])
                if neu != bool(state.get("hoehen_daempfung")):
                    state["hoehen_daempfung"] = neu
                    # Stufe in die Pipeline (oder heraus) und Messfaden starten.
                    band_anwenden(neutral=bool(state.get("bypass")))
                    hoehen_nachziehen()
            if "volume" in daten:
                vol = set_gesamtpegel(daten["volume"])
                state["volume"] = vol
                if "mute" not in daten:
                    mute_aufheben("Pegelregler")
            if "mute" in daten:
                cdsp.volume.set_main_mute(bool(daten["mute"]))
            save_settings()
            return jsonify({"ok": True})
        except Exception as exc:
            return _fehler(exc)



@app.route("/api/verlauf")
def api_verlauf():
    """Lautheitsverlauf fuer das Live-Diagramm, inkrementell.

    ?fein=<t>&grob=<t>: nur Eintraege mit Zeitstempel > t (monotonic-Sekunden
    des Servers). Erste Abfrage mit 0 liefert die vollen Puffer (2 min fein,
    30 min grob). Zeitstempel sind Server-monotonic; "jetzt" erlaubt dem
    Client, relativ zu zeichnen.
    """
    try:
        seit_f = float(request.args.get("fein", 0.0))
        seit_g = float(request.args.get("grob", 0.0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Ungültiger Zeitstempel"}), 400
    fein, grob = verlauf_seit(seit_f, seit_g)
    return jsonify({"ok": True, "jetzt": round(time.monotonic(), 2),
                    "fein": fein, "grob": grob,
                    "band": {"oben": band_oben(), "unten": band_unten(),
                             "decke": band_oben() + BAND_DECKE_MARGE,
                             "rauschsperre": band_rauschsperre()},
                    "boden": VERLAUF_BODEN})

def eingang_kurz():
    """Eingang fuer Status und Oberflaeche (billig: Status-Datei gecacht)."""
    st = quelle_status() if state.get("eingang") == "usb" else {}
    return {"gewaehlt": state.get("eingang", "hifiberry"), "aktiv": _eingang["aktiv"],
            "geraet": st.get("geraet") if st else usb_empfaenger(),
            "format": st.get("format") or "", "layout": st.get("layout"),
            "kanaele": st.get("kanaele") or 0, "mixer_aktiv": bool(st.get("mixer_aktiv")),
            "fehler_60s": st.get("fehler_60s"), "verzoegerung_ms": st.get("verzoegerung_ms"),
            "rueckfall": state.get("eingang") == "usb" and _eingang["aktiv"] != "usb"}


@app.route("/api/eingang", methods=["GET", "POST"])
def api_eingang():
    """Eingang waehlen: hifiberry | usb (UR27). Die Umschaltung selbst macht
    verbinder() innerhalb von 2 s - mit Rueckfall auf die HiFiBerry."""
    if request.method == "POST":
        daten = request.get_json(force=True) or {}
        wahl = daten.get("eingang")
        if wahl not in EINGANG_GERAETE:
            return jsonify({"ok": False, "error": "Unbekannter Eingang"}), 400
        with lock:
            state["eingang"] = wahl
            save_settings()
        quelle_konfig_schreiben()
        _quelle_cache["zeit"] = 0.0
    return jsonify({"ok": True, **eingang_kurz()})


@app.route("/api/mix", methods=["GET", "POST"])
def api_mix():
    """Mix 5.1 -> Stereo (Reiter Mix). POST {"mix": {...}} oder
    {"voreinstellung": "kino"|"neutral"|"sprache"}; Summe wird auf 1 normiert."""
    if request.method == "POST":
        daten = request.get_json(force=True) or {}
        name = daten.get("voreinstellung")
        if name is not None:
            if name not in MIX_VOREINSTELLUNGEN:
                return jsonify({"ok": False, "error": "Unbekannte Voreinstellung"}), 400
            neu, vname = dict(MIX_VOREINSTELLUNGEN[name]), name
        else:
            neu, vname = daten.get("mix"), "eigen"
            if not isinstance(neu, dict):
                return jsonify({"ok": False, "error": "mix fehlt"}), 400
        neu = mix_normiert(neu)
        for n, werte in MIX_VOREINSTELLUNGEN.items():
            if all(abs(neu[k] - werte[k]) < 0.0015 for k in neu):
                vname = n
        with lock:
            state["mix"] = neu
            state["mix_voreinstellung"] = vname
        quelle_konfig_schreiben()
        save_settings_spaeter()
    return jsonify({"ok": True, "mix": mix_normiert(state.get("mix", {})),
                    "voreinstellung": state.get("mix_voreinstellung", MIX_STANDARD),
                    "voreinstellungen": MIX_VOREINSTELLUNGEN, "eingang": eingang_kurz()})


@app.route("/api/band/settings", methods=["POST"])
def api_band_settings():
    """Band-Zweig: nur Ober-/Unterkante und Vorlauf-Schalter."""
    daten = request.get_json(force=True) or {}
    try:
        oben = max(-40.0, min(-8.0, float(daten.get("oben", state.get("band_oben", -22.0)))))
        unten = max(-45.0, min(oben - 1.0, float(daten.get("unten", state.get("band_unten", -26.0)))))
        vorlauf = bool(daten.get("vorlauf", state.get("band_vorlauf", True)))
        vorlauf_ms = max(BAND_VORLAUF_MIN_MS, min(BAND_VORLAUF_MAX_MS, float(daten.get("vorlauf_ms", state.get("band_vorlauf_ms", 30.0)))))
        sperre = max(-60.0, min(-20.0, float(daten.get("rauschsperre", state.get("band_rauschsperre", -40.0)))))
        loudness = bool(daten.get("loudness", state.get("band_loudness", True)))
        staerke = max(0.0, min(200.0, float(daten.get("loudness_staerke", state.get("band_loudness_staerke", 70.0)))))
        vad_an = bool(daten.get("vad", state.get("band_vad", False)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Ungültige Band-Einstellung"}), 400
    with lock:
        # Decke, Vorlauf und Loudness-Pfad leben in der Pipeline; Rauschsperre
        # und Staerke wirken sofort im Regler.
        geaendert = (abs(oben - float(state.get("band_oben", -26.0))) > 1e-6 or
                     abs(unten - float(state.get("band_unten", -30.0))) > 1e-6 or
                     vorlauf != bool(state.get("band_vorlauf", True)) or
                     abs(vorlauf_ms - float(state.get("band_vorlauf_ms", 30.0))) > 1e-6 or
                     loudness != bool(state.get("band_loudness", True)))
        state["band_oben"], state["band_unten"], state["band_vorlauf"] = oben, unten, vorlauf
        state["band_vorlauf_ms"] = vorlauf_ms
        state["band_rauschsperre"] = sperre
        state["band_loudness"], state["band_loudness_staerke"] = loudness, staerke
        state["band_vad"] = vad_an
        save_settings_spaeter()      # Regler-Ziehen: nicht bei jedem Zwischenwert auf die SD-Karte
        band_vad_nachziehen()
        # Decke und Vorlauf leben in der DSP-Pipeline: bei aktivem Band-Zweig
        # sofort neu aufbauen, sonst gilt der Wert ab dem naechsten Umschalten.
        if geaendert and not state.get("bypass"):
            try:
                band_anwenden()
            except Exception as exc:
                return _fehler(exc)
    return jsonify({"ok": True, "band_settings": band_settings_dict()})


@app.route("/api/band/vad/download", methods=["POST"])
def api_band_vad_download():
    """Silero-Modell nachladen (nur auf Knopfdruck, braucht Internet am Pi)."""
    try:
        with lock:
            groesse = vad_modell_laden()
            band_vad_nachziehen()
        log.info("VAD-Modell geladen: %d Bytes", groesse)
        return jsonify({"ok": True, "modell_bytes": groesse, "vad_status": vad_status()})
    except Exception as exc:
        return _fehler(exc)


@app.route("/api/band/reset", methods=["POST"])
def api_band_reset():
    with lock:
        # Loudness-Kompensation gehoert seit 2.6.1 zum EQ-Reiter und hat dort
        # ihren eigenen Reset - der Band-Reset laesst sie unangetastet.
        state.update({k: v for k, v in copy.deepcopy(BAND_DEFAULTS).items()
                      if k not in ("band_loudness", "band_loudness_staerke")})
        save_settings()
        band_vad_nachziehen()
        if not state.get("bypass"):
            try:
                band_anwenden()
            except Exception as exc:
                return _fehler(exc)
    return jsonify({"ok": True, "band_settings": band_settings_dict()})

@app.route("/api/chunksize", methods=["POST"])
def api_chunksize():
    """Chunksize umstellen - die einzige echte Stellschraube fuer die
    Latenz. Kostet einen kurzen Aussetzer, weil CamillaDSP die Pipeline
    dabei neu aufbaut."""
    daten = request.get_json(force=True) or {}
    try:
        wert = int(daten.get("chunksize", 1024))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Ungültiger Wert"}), 400
    # 128 faellt raus: die dsnoop-Periode in /etc/asound.conf steht auf
    # 256, kleinere Bloecke lehnt ALSA ab.
    if wert not in (256, 512, 1024, 2048):
        return jsonify({"ok": False, "error": "Nur 256/512/1024/2048"}), 400
    with lock:
        try:
            cfg = copy.deepcopy(aktive_konfiguration())
            cfg["devices"]["chunksize"] = wert
            cdsp.config.set_active(cfg)
            _roh_fenster.clear()
            geraet["gelesen"] = 0.0     # beim naechsten Durchlauf neu lesen
            return jsonify({"ok": True, "chunksize": wert})
        except Exception as exc:
            return _fehler(exc)

if __name__ == "__main__":
    def _beenden(signum, frame):
        # systemd stoppt den Dienst mit SIGTERM: Kalibrierung und Einstellungen
        # sichern, die seit 2.8.8 nur noch alle 10 min geschrieben werden.
        try:
            save_settings()
            log.info("Einstellungen beim Beenden gesichert")
        finally:
            raise SystemExit(0)
    signal.signal(signal.SIGTERM, _beenden)
    # Nur bekannte Einstellungen uebernehmen (verwaiste Schluessel aus 1.x
    # wie modus/quelle/rms_*/r128_* fallen damit automatisch weg).
    gespeichert = _load(SETTINGS_FILE, {})
    state.update({k: v for k, v in gespeichert.items() if k in state})
    try:
        if float(state.get("volume", -35.0)) > 0.0:
            state["volume"] = 0.0
    except (TypeError, ValueError):
        state["volume"] = -35.0
    if not (isinstance(state.get("eq"), list) and len(state["eq"]) == 16):
        state["eq"] = [0.0] * 16
    if state.get("eingang") not in EINGANG_GERAETE:
        state["eingang"] = "hifiberry"
    state["mix"] = mix_normiert(state.get("mix", {}))
    if state.get("mix_voreinstellung") not in MIX_VOREINSTELLUNGEN:
        state["mix_voreinstellung"] = "eigen"
    quelle_konfig_schreiben()
    state.pop("hoehen_gewichtung", None)
    hoehen_nachziehen()
    # Upgrade von 1.x: Die Klangkurve hing am Modus (presets.json). Einmalig
    # die Kurve des zuletzt aktiven Modus uebernehmen, wenn noch keine
    # eigene gespeichert ist.
    if "eq" not in gespeichert:
        try:
            alt_presets = _load(PRESET_FILE, {})
            alt_modus = gespeichert.get("modus")
            kurve = (alt_presets.get(alt_modus) or {}).get("eq")
            if isinstance(kurve, list) and len(kurve) == 16:
                state["eq"] = [clamp("eq", g) for g in kurve]
                log.info("EQ aus Modus %s uebernommen: %s", alt_modus, _eq_kurz(state["eq"]))
        except Exception as exc:
            log.warning("EQ-Uebernahme aus presets.json: %s", exc)
    # EQ-Filterform: aeltere Staende einmalig umrechnen. Die Wirkung an den 16
    # Punkten bleibt, die Linie dazwischen wird glatt (2.8.6).
    if gespeichert and gespeichert.get("eq_filter") != "glatt-1okt":
        alt = list(state["eq"])
        if any(abs(g) > 0.001 for g in alt):
            q_alt = SPREAD[state["spread"]]["q"]
            mit_bw = gespeichert.get("eq_filter") == "bandbreite"      # Stand 2.8.5
            punkte = [sum(_peaking_db(f, BANDS[i], alt[i], q_alt, mit_bw) for i in range(16))
                      for f in BANDS]
            state["eq"] = eq_glatt_loesen(punkte)
            log.info("EQ auf glatte Kurve umgerechnet (Punkte unveraendert): %s -> %s",
                     _eq_kurz(alt), _eq_kurz(state["eq"]))
        state["eq_filter"] = "glatt-1okt"
        save_settings()
    if "kalibrierung_schnell_gueltig" in gespeichert:
        _kalib_schnell_gueltig["ok"] = bool(gespeichert["kalibrierung_schnell_gueltig"])
    else:
        _kalib_schnell_gueltig["ok"] = "kalibrierung_schnell" in gespeichert
        state["kalibrierung_schnell_gueltig"] = _kalib_schnell_gueltig["ok"]
    threading.Thread(target=verbinder, daemon=True).start()
    threading.Thread(target=regelschleife, daemon=True).start()
    threading.Thread(target=r128_leser, daemon=True).start()
    threading.Thread(target=dsp_profil_waechter, daemon=True).start()
    # Eine liegengebliebene Anlern-Markierung aus einem vorherigen Lauf
    # wuerde den Listener dauerhaft vom Stick fernhalten.
    _flirc_learning(False)
    try:
        _ir_keys_schreiben()
        for f in (IR_LEARN_REQ, IR_SCAN_FLAG):
            if os.path.exists(f):
                os.remove(f)
    except OSError:
        pass
    log.info("TV-Dynamikprozessor %s, Weboberflaeche auf Port %s",
             VERSION, HTTP_PORT)
    app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True)
