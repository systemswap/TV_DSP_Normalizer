#!/opt/tvdsp/venv/bin/python3
"""tvdsp-quelle - USB-Toslink-Eingang fuer TVDSP, ab 3.0.0.

Geraete (USB_GERAETE, erkannt an der USB-ID, das erste gefundene gilt):
miniDSP MCHStreamer I2S TosLink (ab 3.3.0; Toslink = Kanaele 9/10 von 10,
Takt vom Toslink-Eingang, asynchron - verliert nichts) und Hifime UR27
(adaptiv, verliert Samples; bleibt fuer den Fall drin, dass er zurueckkommt).

Liest den Empfaenger als Stereo-S/PDIF-Strom und liefert IMMER fertiges Stereo an
den Loopback (hw:Loopback,0,0). Die bestehende Kette (CamillaDSP, Messung,
Aufnahme) liest es dort ueber dsnoop_usb/tvmess_usb, genau wie sonst die
HiFiBerry - Normalisierung, Decke, EQ und Limiter wirken also NACH dem Mix.

  PCM-Stereo            -> unveraendert durch (Mixer aus)
  Dolby Digital/DTS     -> IEC-61937-Bursts erkennen, decodieren (PyAV),
    2.0                 -> unveraendert (Mixer aus)
    5.1                 -> Mix 6->2 nach config/quelle.json (Reiter "Mix")

Eingang im Takt des Fernsehers, Loopback im Takt der Systemuhr: den
Unterschied gleicht der Feinresampler aus. Kaputte Bursts (verlorene Samples)
werden durch kurze Stille mit Blende ersetzt - nie durch Rauschen.

Laeuft nur, wenn in config/quelle.json "eingang": "usb" steht und ein
Empfaenger steckt; sonst schlaeft der Dienst (1 Pruefung/s). Laeuft im venv
(PyAV, numpy, pyalsaaudio in festen Versionen).
"""
import glob
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time

import numpy as np

PREFIX = os.environ.get("TVDSP_PREFIX", "/opt/tvdsp")
KONFIG = os.path.join(PREFIX, "config", "quelle.json")
# Im Arbeitsspeicher (systemd RuntimeDirectory), nicht auf der SD-Karte: 1x/s (3.3.6)
STATUS = os.environ.get("TVDSP_QUELLE_STATUS", "/run/tvdsp/quelle-status.json")
# USB-Empfaenger in Vorzugsreihenfolge. links = Index des linken Toslink-
# Kanals, bits = Abtastformat, takt = (ALSA-Regler, Wert), der den Takt auf
# den Toslink-Eingang stellt (None = Geraet kann das nicht).
USB_GERAETE = [
    {"name": "miniDSP MCHStreamer", "usbid": "2752:0019", "kanaele": 10, "links": 8, "bits": 32,
     "takt": ("miniDSP Clock Selector Clock Source", "miniDSP TOSLINK Clock"),
     "takt_intern": "miniDSP Internal Clock"},
    {"name": "Hifime UR27", "usbid": "262a:9227", "kanaele": 2, "links": 0, "bits": 16, "takt": None},
]
LOOP_GERAET = os.environ.get("TVDSP_LOOP", "hw:Loopback,0,0")
RATE = 48000
BLOCK = 256                       # Frames je Lese-/Schreibschritt (5,3 ms)
LOOP_PUFFER = 8 * BLOCK           # Puffer im Loopback (43 ms)
LOOP_ZIEL = 4 * BLOCK             # Soll der Gesamtverzoegerung Empfaenger + Loopback (21 ms)
RUECKSTAND_MAX = 2400             # mehr als 50 ms ueber dem Soll: Rueckstand verwerfen (3.3.4)

PA, PB = 0xF872, 0x4E1F           # IEC-61937-Praeambel
# Datentyp (Pc & 0x1f) -> (Burst-Periode in Frames, PyAV-Decoder)
BURST_TYPEN = {1: (1536, "ac3"), 11: (512, "dca"), 12: (1024, "dca"), 13: (2048, "dca")}
BITSTROM_HALTEN = 0.5             # s ohne Praeambel, bevor wieder PCM gilt
BLENDE = 240                      # Frames (5 ms) Ein-/Ausblenden um Luecken

# Voreinstellungen fuer den Mix (je Seite Summe 1 = kein Uebersteuern).
# "kino" = Standard: Center etwas staerker als Front, LFE spielt mit.
MIX_VOREINSTELLUNGEN = {
    "kino":    {"front": 0.258, "center": 0.323, "lfe": 0.274, "surround": 0.145},
    "neutral": {"front": 0.414, "center": 0.293, "lfe": 0.0,   "surround": 0.293},
    "sprache": {"front": 0.200, "center": 0.500, "lfe": 0.150, "surround": 0.150},
}
MIX_STANDARD = "kino"

log = logging.getLogger("tvdsp-quelle")


def mix_normiert(mix):
    """Vier Anteile >= 0, Summe 1 (fehlerhafte Werte -> Standard)."""
    try:
        w = {k: max(0.0, float(mix.get(k, 0.0))) for k in ("front", "center", "lfe", "surround")}
    except (AttributeError, TypeError, ValueError):
        return dict(MIX_VOREINSTELLUNGEN[MIX_STANDARD])
    s = sum(w.values())
    if s <= 1e-9:
        return dict(MIX_VOREINSTELLUNGEN[MIX_STANDARD])
    return {k: v / s for k, v in w.items()}


def mixe(planes, namen, mix):
    """Decodierte Kanaele (Kanal x Samples, float) auf Stereo (Samples x 2).

    Bei 1 oder 2 Kanaelen ist der Mixer aus (unveraendert durch)."""
    n = planes.shape[1]
    if len(namen) == 1:
        return np.repeat(planes[0][:, None], 2, axis=1)
    if len(namen) == 2:
        return planes[:2].T.copy()
    idx = {name: i for i, name in enumerate(namen)}

    def k(*kandidaten):
        for c in kandidaten:
            if c in idx:
                return planes[idx[c]]
        return np.zeros(n, dtype=planes.dtype)

    m = mix
    mitte = m["center"] * k("FC") + m["lfe"] * k("LFE", "LFE1")
    links = m["front"] * k("FL") + mitte + m["surround"] * k("SL", "BL")
    rechts = m["front"] * k("FR") + mitte + m["surround"] * k("SR", "BR")
    return np.stack([links, rechts], axis=1)


class Wandler:
    """Kern ohne ALSA: int16-Stereo rein, float32-Stereo gleicher Laenge raus."""

    def __init__(self, mix=None):
        self.mix = mix_normiert(mix or MIX_VOREINSTELLUNGEN[MIX_STANDARD])
        self.worte = np.zeros(0, dtype=np.uint16)   # noch nicht verarbeitete 16-bit-Worte
        self.aus = np.zeros((0, 2), dtype=np.float32)  # Ausgabe-Warteschlange (Bitstrom)
        self.modus = "stille"          # pcm | bitstrom | stille
        self.codec_name = None
        self.codec = None
        self.layout = None
        self.kanaele = 0
        self.letzter_burst = -1e9      # Frame-Zaehler des letzten Bursts
        self.frames = 0                # verarbeitete Eingangsframes gesamt
        self.fehler = []               # Frame-Zaehler kaputter Bursts
        self.bursts = 0
        self.ziel_puffer = None        # Soll-Laenge der Warteschlange (Latenz)
        self.einblenden = False

    # ------------------------------------------------------------ Decoder
    def _decoder(self, name):
        vorrat = getattr(self, "_vorrat", None)
        if self.codec_name != name and name == "ac3" and vorrat is not None:
            self.codec, self.codec_name, self._vorrat = vorrat, name, None   # vorgeladen
            return self.codec
        if self.codec_name != name:
            import av
            self.codec = av.CodecContext.create(name, "r")
            try:
                # crccheck allein erkennt die Pruefsumme, verdeckt den Fehler aber
                # (letzten Block wiederholen) und meldet nichts; erst +explode
                # liefert die Ausnahme -> 32 ms Stille mit Blende (3.3.0). "bitstream"/
                # "buffer" NICHT: verwarfen 290 von 507 heilen Frames (3.0.0).
                self.codec.options = {"err_detect": "crccheck+explode"}
            except Exception:
                pass
            self.codec_name = name
        return self.codec

    def _decodiere(self, name, nutzdaten):
        """Liefert (Samples x 2 float32, layout, kanaele) oder None bei Fehler."""
        import av
        try:
            codec = self._decoder(name)
            frames = codec.decode(av.Packet(nutzdaten))
        except Exception:
            self.codec_name = None      # Decoder nach Fehler frisch aufsetzen
            return None
        teile, layout, kanaele = [], None, 0
        for fr in frames:
            planes = fr.to_ndarray()
            if planes.dtype.kind == "i":
                planes = planes.astype(np.float32) / 32768.0
            if planes.ndim == 2 and planes.shape[0] != len(fr.layout.channels):
                planes = planes.reshape(-1, len(fr.layout.channels)).T   # verschachtelt
            namen = [c.name for c in fr.layout.channels]
            layout, kanaele = fr.layout.name, len(namen)
            teile.append(mixe(planes.astype(np.float32), namen, self.mix))
        if not teile:
            return None
        return np.concatenate(teile).astype(np.float32), layout, kanaele

    # ------------------------------------------------------- Warteschlange
    def _stille(self, n):
        """n Frames Stille anhaengen; das Ende davor ausblenden.

        Verliert der Empfaenger Samples, waechst die Warteschlange (es kommen weniger
        Eingangsframes als decodierte). Den Ueberhang an dieser Stelle - im
        ohnehin kaputten Burst - abziehen, statt spaeter hoerbar zu kuerzen."""
        if self.ziel_puffer is not None:
            n -= max(0, len(self.aus) - self.ziel_puffer)
        if len(self.aus):
            b = min(BLENDE, len(self.aus))
            self.aus[-b:] *= np.linspace(1.0, 0.0, b, dtype=np.float32)[:, None]
        self.aus = np.concatenate([self.aus, np.zeros((max(0, n), 2), np.float32)])
        self.einblenden = True

    def _anhaengen(self, pcm):
        if self.einblenden and len(pcm):
            b = min(BLENDE, len(pcm))
            pcm = pcm.copy()
            pcm[:b] *= np.linspace(0.0, 1.0, b, dtype=np.float32)[:, None]
            self.einblenden = False
        self.aus = np.concatenate([self.aus, pcm])

    # ------------------------------------------------------------ Kern
    def verarbeite(self, block, fein=None):
        """block: int16-Array (n, 2) -> float32-Array (n, 2). fein: dieselben
        Samples als float32 in hoeherer Aufloesung (24 bit) fuer PCM, optional."""
        n = len(block)
        start = self.frames
        worte = np.concatenate([self.worte, block.reshape(-1).view(np.uint16)])
        treffer = np.flatnonzero((worte[:-1] == PA) & (worte[1:] == PB))
        treffer = treffer[treffer % 2 == 0]              # Pa im linken Teilframe
        verbraucht = 0
        for pos in treffer:
            pos = int(pos)
            if pos < verbraucht or pos + 4 > len(worte):
                continue
            pc, pd = int(worte[pos + 2]), int(worte[pos + 3])
            typ = pc & 0x1F
            if typ in (0, 3):                             # Null-/Pausen-Burst
                self.letzter_burst = start + (pos - len(self.worte)) // 2
                verbraucht = pos + 4
                continue
            if typ not in BURST_TYPEN:
                continue
            laenge = (pd + 15) // 16                      # Pd in Bit -> 16-bit-Worte
            periode, name = BURST_TYPEN[typ]
            if self.modus != "bitstrom":
                # Sofort an der Praeambel umschalten, auch wenn der Burst noch
                # nicht vollstaendig ist - sonst liefe dieser Block als PCM,
                # also als lautes Rauschen, zu den Boxen. Zwei Bloecke Reserve,
                # damit die Warteschlange zwischen zwei Bursts nicht leerlaeuft.
                self.modus = "bitstrom"
                self.aus = np.zeros((2 * BLOCK, 2), np.float32)
                self.ziel_puffer = None
                self.einblenden = True
            self.letzter_burst = start + (pos - len(self.worte)) // 2
            if pos + 4 + laenge > len(worte):
                break                                     # Burst noch unvollstaendig
            nutz = worte[pos + 4:pos + 4 + laenge].astype(">u2").tobytes()[: (pd + 7) // 8]
            ergebnis = self._decodiere(name, nutz)
            self.bursts += 1
            burst_frame = start + (pos - len(self.worte)) // 2
            self.letzter_burst = burst_frame
            if ergebnis is None:
                self.fehler.append(burst_frame)
                self._stille(periode)
            else:
                pcm, self.layout, self.kanaele = ergebnis
                self._anhaengen(pcm)
            verbraucht = pos + 4 + laenge
        # Unverbrauchte Worte behalten (max. eine Burst-Periode), Rest verwerfen.
        rest = worte[verbraucht:]
        if len(rest) > 2 * 2048 * 2:
            rest = rest[-2 * 2048 * 2:]
        if len(rest) % 2:
            rest = rest[1:]
        self.worte = rest
        self.frames += n

        if self.modus == "bitstrom" and self.frames - self.letzter_burst > BITSTROM_HALTEN * RATE:
            self.modus = "pcm"
            self.aus = np.zeros((0, 2), np.float32)
            self.layout, self.kanaele = "stereo", 2
            self.einblenden = True

        if self.modus == "bitstrom":
            # Latenz halten: Soll = Fuellstand nach dem ersten Burst. Waechst die
            # Schlange trotz _stille() um mehr als 2048 Frames (Verluste nur in
            # der Fuellung), mit Blende kuerzen - selten, statt stetig mehr Latenz.
            if self.ziel_puffer is None and len(self.aus) >= n:
                self.ziel_puffer = len(self.aus)
            if self.ziel_puffer is not None and len(self.aus) > self.ziel_puffer + 2048:
                self.aus = self.aus[len(self.aus) - self.ziel_puffer:].copy()
                b = min(BLENDE, len(self.aus))
                self.aus[:b] *= np.linspace(0.0, 1.0, b, dtype=np.float32)[:, None]
            if len(self.aus) >= n:
                raus, self.aus = self.aus[:n], self.aus[n:]
            else:
                raus = np.concatenate([self.aus, np.zeros((n - len(self.aus), 2), np.float32)])
                self.aus = np.zeros((0, 2), np.float32)
                self.einblenden = True
            return raus

        # PCM: unveraendert (Stereo). Stille unterscheiden fuer die Anzeige.
        pcm = fein.copy() if fein is not None else block.astype(np.float32) / 32768.0
        if self.einblenden:
            b = min(BLENDE, n)
            pcm[:b] *= np.linspace(0.0, 1.0, b, dtype=np.float32)[:, None]
            self.einblenden = False
        if self.modus == "stille" and np.abs(block).max() > 8:
            self.modus = "pcm"
        self.layout, self.kanaele = "stereo", 2
        return pcm

    def status(self):
        jetzt = self.frames
        fehler_60 = sum(1 for f in self.fehler if jetzt - f <= 60 * RATE)
        self.fehler = [f for f in self.fehler if jetzt - f <= 60 * RATE]
        art = {"ac3": "Dolby Digital", "dca": "DTS"}.get(self.codec_name, "")
        return {
            "modus": self.modus,
            "format": (art or "Bitstrom") if self.modus == "bitstrom" else ("PCM" if self.modus == "pcm" else ""),
            "layout": self.layout, "kanaele": self.kanaele,
            "mixer_aktiv": self.modus == "bitstrom" and self.kanaele > 2,
            "fehler_60s": fehler_60, "bursts": self.bursts,
        }


# ---------------------------------------------------------------- Betrieb

def lies_konfig():
    try:
        with open(KONFIG) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def schreibe_status(d):
    d = dict(d, zeit=time.time())
    tmp = STATUS + ".neu"
    try:
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, STATUS)
    except OSError as exc:
        log.warning("Status nicht geschrieben: %s", exc)


def usb_geraet():
    """(Eintrag aus USB_GERAETE, ALSA-Kartenname) des bevorzugten gesteckten
    Empfaengers, sonst (None, None). Erkennung ueber die USB-ID, nicht den
    Namen (der UR27 heisst ohne modprobe-Option nur "Audio")."""
    gesteckt = {}
    for pfad in glob.glob("/proc/asound/card*/usbid"):
        try:
            with open(pfad) as f:
                usbid = f.read().strip()
            with open(os.path.join(os.path.dirname(pfad), "id")) as f:
                gesteckt[usbid] = f.read().strip()
        except OSError:
            pass
    for g in USB_GERAETE:
        if g["usbid"] in gesteckt:
            return g, gesteckt[g["usbid"]]
    return None, None


class Feinresampler:
    """Passt das Tempo um wenige ppm an (kubische Interpolation, stetig).

    Der Loopback zaehlt mit der Systemuhr, der UR27 mit der Uhr des
    Fernsehers. Einen Hardware-Timer hat der UR27 nicht (snd_aloop
    timer_source -> -19), und 'PCM Rate Shift' des Loopbacks aendert nur die
    Wiedergabeseite - die Leitung verwirft/verdoppelt dann Stuecke (am Geraet
    geprueft). Deshalb hier: PI-Regler auf die Gesamtverzoegerung (Rueckstand
    im UR27 + Loopback-Fuellstand + eigene Warteschlange), Stellgroesse ist das
    Verhaeltnis Ausgabe/Eingabe (kubische Interpolation). Die Strecke Loopback -> HiFiBerry gleicht
    CamillaDSP mit seinem Resampler aus.
    """

    def __init__(self):
        self.verhaeltnis = 1.0         # Ausgabeframes je Eingabeframe
        self.integral = 0.0
        self.pos = 1.0                 # Leseposition im Puffer (Bruchteil)
        self.puffer = np.zeros((3, 2), np.float32)   # letzte Frames als Stuetzstellen

    def regeln(self, verzoegerung):
        fehler = verzoegerung - LOOP_ZIEL            # >0: zu viel im Weg -> weniger ausgeben
        # 3.3.2: straffer (vorher 4e-7/4e-8, pendelte +-8 ms mit 2-3 min Periode;
        # Simulation 1-s-Takt mit Messrauschen: jetzt +-1 ms)
        self.integral = max(-1e-3, min(1e-3, self.integral - fehler * 1e-7))
        self.verhaeltnis = max(0.997, min(1.003, 1.0 + self.integral - fehler * 2e-6))
        return self.verhaeltnis

    def __call__(self, block):
        """Kubische Hermite-Interpolation (4 Stuetzstellen): bei 10 kHz
        schwankt der Frequenzgang so um < 0,3 dB (linear waeren es 2 dB)."""
        daten = np.concatenate([self.puffer, block])
        schritt = 1.0 / self.verhaeltnis
        stellen = np.arange(self.pos, len(daten) - 2, schritt)
        i = stellen.astype(np.int64)
        t = (stellen - i).astype(np.float32)[:, None]
        p0, p1, p2, p3 = daten[i - 1], daten[i], daten[i + 1], daten[i + 2]
        a = -0.5 * p0 + 1.5 * p1 - 1.5 * p2 + 0.5 * p3
        b = p0 - 2.5 * p1 + 2.0 * p2 - 0.5 * p3
        c = -0.5 * p0 + 0.5 * p2
        raus = ((a * t + b) * t + c) * t + p1
        naechste = (stellen[-1] + schritt) if len(stellen) else self.pos
        self.pos = naechste - (len(daten) - 3)
        self.puffer = daten[-3:]
        return raus.astype(np.float32)


def takt_setzen(geraet, karte, neu_einrasten=False):
    """Takt vom Toslink-Eingang: das Geraet folgt dem Fernseher, nichts geht
    verloren. neu_einrasten: intern stellen, KURZ LESEN, dann Toslink, 2 s
    warten. Nach dem Einschalten scheitert das Lesen mit Toslink-Takt mit
    "Input/output error"; blosses Umschalten ohne Lesen reicht NICHT (3.3.1
    war deshalb wirkungslos), erst das Lesen mit internem Takt loest es (3.3.8,
    am Geraet nach Neustart geprueft)."""
    regler, wert = geraet["takt"]

    def setze(w):
        subprocess.run(["amixer", "-q", "-c", karte, "cset", "iface=MIXER,name=%s" % regler, w],
                       timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if neu_einrasten:
        import alsaaudio
        log.info("Takt neu einrasten (intern lesen -> Toslink)")
        setze(geraet["takt_intern"])
        time.sleep(0.5)
        pcm = None
        try:
            fmt = alsaaudio.PCM_FORMAT_S32_LE if geraet["bits"] == 32 else alsaaudio.PCM_FORMAT_S16_LE
            pcm = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL, device="hw:%s,0" % karte,
                                rate=RATE, channels=geraet["kanaele"], format=fmt,
                                periodsize=BLOCK, periods=16)
            for _ in range(20):             # ~100 ms
                pcm.read()
        except alsaaudio.ALSAAudioError as exc:
            log.warning("Lesen mit internem Takt: %s", exc)
        finally:
            if pcm is not None:
                pcm.close()
    setze(wert)
    if neu_einrasten:
        time.sleep(2.0)


def lauf(wandler, geraet, karte, neu_einrasten=False):
    """Eine Sitzung: Empfaenger lesen, Loopback schreiben, bis Fehler/Abschaltung."""
    import alsaaudio
    if geraet["takt"]:
        takt_setzen(geraet, karte, neu_einrasten)
    # Decoder VORHER laden: import av + Codec dauern ~0,3 s - beim ersten
    # Dolby-Frame mitten im Betrieb lief dabei der Lesepuffer (85 ms) ueber,
    # hoerbar als Verzerrung beim Umschalten PCM -> Dolby (3.3.2).
    wandler._decoder("ac3")
    wandler.codec_name = None
    wandler._vorrat = wandler.codec
    fmt = alsaaudio.PCM_FORMAT_S32_LE if geraet["bits"] == 32 else alsaaudio.PCM_FORMAT_S16_LE
    cap = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL, device="hw:%s,0" % karte,
                        rate=RATE, channels=geraet["kanaele"], format=fmt,
                        periodsize=BLOCK, periods=64)   # 341 ms Reserve gegen Haenger (3.3.3; vorher 85 ms)
    play = alsaaudio.PCM(alsaaudio.PCM_PLAYBACK, alsaaudio.PCM_NORMAL, device=LOOP_GERAET,
                         rate=RATE, channels=2, format=alsaaudio.PCM_FORMAT_S32_LE,
                         periodsize=BLOCK, periods=LOOP_PUFFER // BLOCK)
    # Den Tempo-Stellwert des Loopbacks neutral halten (siehe Feinresampler).
    subprocess.run(["amixer", "-q", "-c", "Loopback", "cset",
                    "iface=PCM,name=PCM Rate Shift 100000,device=1", "100000"],
                   timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("Laeuft: %s (hw:%s,0, Kanaele %d/%d) -> %s", geraet["name"], karte,
             geraet["links"] + 1, geraet["links"] + 2, LOOP_GERAET)
    tempo = Feinresampler()
    fifo = np.zeros((0, 2), np.float32)
    letzter_status, ueberlaeufe = 0.0, 0
    verz_summe, verz_n = 0.0, 0
    # Fehlersuche: rohe Eingangsdaten mitschreiben (TVDSP_QUELLE_MITSCHNITT=datei)
    mitschnitt = open(os.environ["TVDSP_QUELLE_MITSCHNITT"], "wb") if os.environ.get("TVDSP_QUELLE_MITSCHNITT") else None
    # Dateizugriffe (Konfig lesen, Status schreiben) macht ein eigener Faden:
    # ein rename auf der SD-Karte blockierte unter Schreiblast mehrere hundert
    # ms und hielt die Tonschleife an -> Ueberlauf (3.3.5).
    ablage = {"konfig": lies_konfig(), "status": None, "stop": False}

    def verwalter():
        while not ablage["stop"]:
            ablage["konfig"] = lies_konfig()
            st, ablage["status"] = ablage["status"], None
            if st is not None:
                schreibe_status(st)
            time.sleep(0.5)

    threading.Thread(target=verwalter, daemon=True, name="verwalter").start()
    try:
        while True:
            laenge, daten = cap.read()
            if laenge < 0:
                ueberlaeufe += 1                      # Ueberlauf: ALSA erholt sich selbst
                continue
            if laenge == 0:
                continue
            if mitschnitt:
                mitschnitt.write(daten)
            if geraet["bits"] == 32:
                roh = np.frombuffer(daten, dtype="<i4").reshape(-1, geraet["kanaele"])
                roh = roh[:, geraet["links"]:geraet["links"] + 2]
                block = (roh >> 16).astype("<i2")                       # 16-bit-Worte fuer IEC 61937
                fein = (roh >> 8).astype(np.float32) / 8388608.0        # PCM mit 24 bit
            else:
                block = np.frombuffer(daten, dtype="<i2").reshape(-1, geraet["kanaele"])
                block = block[:, geraet["links"]:geraet["links"] + 2]
                fein = None
            fifo = np.concatenate([fifo, tempo(wandler.verarbeite(block, fein))])
            while len(fifo) >= BLOCK:                 # immer ganze Perioden schreiben
                teil, fifo = fifo[:BLOCK], fifo[BLOCK:]
                s32 = (np.clip(teil, -1.0, 0.9999999) * 2147483648.0).astype("<i4")
                play.write(s32.tobytes())
            try:
                verz_summe += cap.avail() + (LOOP_PUFFER - play.avail()) + len(fifo)
                verz_n += 1
            except alsaaudio.ALSAAudioError:
                pass
            jetzt = time.monotonic()
            if jetzt - letzter_status >= 1.0:
                letzter_status = jetzt
                k = ablage["konfig"]               # liest der Verwalter-Faden
                if k.get("eingang") != "usb":
                    log.info("Eingang ist nicht mehr USB - halte an")
                    return
                if not os.path.isdir("/proc/asound/" + karte):
                    log.warning("%s abgezogen", geraet["name"])
                    return
                if k.get("mix") and mix_normiert(k["mix"]) != wandler.mix:
                    wandler.mix = mix_normiert(k["mix"])
                    log.info("Mix: %s", ", ".join("%s %.3f" % kv for kv in wandler.mix.items()))
                verz = verz_summe / verz_n if verz_n else None
                verz_summe, verz_n = 0.0, 0
                if verz is not None and verz > LOOP_ZIEL + RUECKSTAND_MAX:
                    # Aufgestauter Rueckstand (z. B. nach Last beim Start): die
                    # Regelung baut nur 3000 ppm ab (300 ms = 30 min). Sofort
                    # verwerfen, einmal mit Blende - der Ton bleibt lippensynchron.
                    weg = 0
                    while cap.avail() > 2 * BLOCK:
                        l, _ = cap.read()
                        weg += max(l, 0)
                    fifo = fifo[:0]
                    wandler.einblenden = True
                    tempo.integral = 0.0
                    log.warning("Rueckstand %.0f ms: %d Frames verworfen", verz * 1000.0 / RATE, weg)
                    verz = None
                if verz is not None:
                    tempo.regeln(verz)
                ablage["status"] = (dict(wandler.status(), laeuft=True, geraet=geraet["name"], eingang="usb",
                                     verzoegerung_ms=None if verz is None else round(verz * 1000.0 / RATE, 1),
                                     tempo_ppm=round((tempo.verhaeltnis - 1.0) * 1e6), ueberlaeufe=ueberlaeufe))
    finally:
        ablage["stop"] = True
        for pcm in (cap, play):
            try:
                pcm.close()
            except Exception:
                pass
        if mitschnitt:
            mitschnitt.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    gemeldet = None
    fehlschlaege = 0                 # Abbrueche in Folge (fuer das Neu-Einrasten des Takts)
    erster_lauf = True               # beim Dienststart immer einrasten (Zustand nach dem Einschalten)
    while True:
        k = lies_konfig()
        eingang = k.get("eingang", "hifiberry")
        geraet, karte = usb_geraet()
        da = geraet["name"] if geraet else None
        if eingang == "usb" and geraet:
            wandler = Wandler(k.get("mix"))
            try:
                einrasten = (erster_lauf or fehlschlaege > 0) and bool(geraet["takt"])
                erster_lauf = False
                lauf(wandler, geraet, karte, neu_einrasten=einrasten)
                fehlschlaege = 0
            except Exception as exc:
                fehlschlaege += 1
                if fehlschlaege <= 3 or fehlschlaege % 60 == 0:
                    log.exception("Abbruch (%d. in Folge): %s", fehlschlaege, exc)
                if fehlschlaege > 3:
                    time.sleep(4.0)          # nicht jede Sekunde neu anlaufen
            gemeldet = None
            time.sleep(1.0)
            continue
        zustand = (eingang, da)
        if zustand != gemeldet:
            log.info("Wartet (Eingang %s, USB-Empfaenger %s)", eingang, da or "fehlt")
            gemeldet = zustand
        schreibe_status({"laeuft": False, "geraet": da, "eingang": eingang, "modus": "aus",
                         "format": "", "layout": None, "kanaele": 0, "mixer_aktiv": False,
                         "fehler_60s": 0, "bursts": 0})
        time.sleep(1.0)


if __name__ == "__main__":
    main()
