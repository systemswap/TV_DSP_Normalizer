#!/usr/bin/python3
"""Test fuer bin/tvdsp-quelle.py (USB-Eingang, ab 3.0.0) - ohne Soundkarte.

Erzeugt mit ffmpeg ein 5.1-Signal mit einem eigenen Ton je Kanal, codiert es
als Dolby Digital (640 kb/s) und verpackt es als IEC 61937 - so, wie es der
Fernseher ueber Toslink schickt. Das laeuft in 256er-Bloecken durch den
Wandler. Geprueft wird:

  1. Mix-Mathematik: Pegel jedes Tons in L/R = Mix-Anteil (Summe je Seite 1)
  2. Dolby 2.0 und PCM laufen unveraendert durch (Mixer aus)
  3. Wechsel PCM -> Dolby -> PCM: kein Bitstrom-Rauschen im Ausgang
  4. Verlorene Samples (wie beim UR27): kaputte Bursts werden Stille, nie
     Rauschen; Latenz waechst nicht
  5. optional: echte UR27-Aufnahme (WAV, 16 bit Stereo) als Argument

Aufruf: python3 werkzeuge/quelletest.py [aufnahme.wav]   (Exit 1 bei Fehler)
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("quelle", os.path.join(REPO, "bin", "tvdsp-quelle.py"))
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)

RATE = 48000
TOENE = {"FL": 300, "FR": 500, "FC": 700, "LFE": 60, "SL": 900, "SR": 1100}
AMP = 0.1                                   # -20 dBFS je Kanal
ARBEIT = tempfile.mkdtemp(prefix="quelletest-")
fehler = 0


def pruefe(ok, text):
    global fehler
    print(("  OK  " if ok else "  FEHLER  ") + text)
    if not ok:
        fehler += 1


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def lade_raw(pfad):
    return np.fromfile(pfad, dtype="<i2").reshape(-1, 2)


def durch_wandler(roh, mix=None, block=256):
    w = q.Wandler(mix)
    teile = [w.verarbeite(roh[i:i + block]) for i in range(0, len(roh) - block + 1, block)]
    return np.concatenate(teile), w


def pegel(sig, freq):
    """Amplitude eines Tons (Goertzel ueber das ganze Signal)."""
    n = len(sig)
    t = np.arange(n) / RATE
    fenster = np.hanning(n)
    re = np.sum(sig * fenster * np.cos(2 * np.pi * freq * t))
    im = np.sum(sig * fenster * np.sin(2 * np.pi * freq * t))
    return 2 * np.hypot(re, im) / np.sum(fenster)


def db(x):
    return 20 * np.log10(max(x, 1e-9))


# ------------------------------------------------------------ Testsignale
print("Erzeuge Testsignale in", ARBEIT)
quellen = []
for kanal in ("FL", "FR", "FC", "LFE", "SL", "SR"):
    quellen += ["-f", "lavfi", "-i", "sine=frequency=%d:sample_rate=48000:duration=8" % TOENE[kanal]]
filt = "".join("[%d]volume=%.4f[a%d];" % (i, AMP / 0.125, i) for i in range(6))   # sine-Quelle hat 1/8
# Feste Zuordnung: ohne map verteilt join Mono-Eingaenge nach Kanalnamen (alle "FC").
filt += ("[a0][a1][a2][a3][a4][a5]join=inputs=6:channel_layout=5.1(side)"
         ":map=0.0-FL|1.0-FR|2.0-FC|3.0-LFE|4.0-SL|5.0-SR[o]")
sechs = os.path.join(ARBEIT, "sechs.wav")
ffmpeg(*quellen, "-filter_complex", filt, "-map", "[o]", "-c:a", "pcm_f32le", sechs)
spdif51 = os.path.join(ARBEIT, "51.spdif")
ffmpeg("-i", sechs, "-c:a", "ac3", "-b:a", "640k", "-f", "spdif", spdif51)
spdif20 = os.path.join(ARBEIT, "20.spdif")
ffmpeg("-f", "lavfi", "-i", "sine=frequency=400:sample_rate=48000:duration=8", "-f", "lavfi", "-i", "sine=frequency=800:sample_rate=48000:duration=8",
       "-filter_complex", "[0][1]amerge=inputs=2,volume=0.8[o]", "-map", "[o]",
       "-c:a", "ac3", "-b:a", "192k", "-f", "spdif", spdif20)
pcm = os.path.join(ARBEIT, "pcm.raw")
ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:duration=4", "-f", "lavfi", "-i", "sine=frequency=1500:duration=4",
       "-filter_complex", "[0][1]amerge=inputs=2,volume=2[o]", "-map", "[o]",
       "-f", "s16le", "-ar", "48000", "-ac", "2", pcm)

roh51, roh20, rohpcm = lade_raw(spdif51), lade_raw(spdif20), lade_raw(pcm)

# ------------------------------------------------------------- 1. Mix
print("\n1) Dolby 5.1 -> Stereo, Mix-Anteile")
for name, mix in q.MIX_VOREINSTELLUNGEN.items():
    aus, w = durch_wandler(roh51, mix)
    m = q.mix_normiert(mix)
    mitte = aus[RATE:-RATE]                     # Ein-/Ausschwingen weglassen
    anteil = {"FL": ("front", 0), "FR": ("front", 1), "FC": ("center", None),
              "LFE": ("lfe", None), "SL": ("surround", 0), "SR": ("surround", 1)}
    abw = []
    for kanal, (gruppe, seite) in anteil.items():
        for s in (0, 1):
            soll = AMP * m[gruppe] if (seite is None or seite == s) else 0.0
            ist = pegel(mitte[:, s], TOENE[kanal])
            if soll > 0.005:
                abw.append(abs(db(ist) - db(soll)))
            else:
                abw.append(0.0 if ist < 0.003 else 99.0)
    pruefe(max(abw) < 1.0 and w.kanaele == 6 and w.status()["mixer_aktiv"],
           "%-8s max. Abweichung %.2f dB (Layout %s, %d Kanaele, %d Bursts, %d Fehler)"
           % (name, max(abw), w.layout, w.kanaele, w.bursts, len(w.fehler)))

# --------------------------------------------------- 2. Durchreichen
print("\n2) Dolby 2.0 und PCM laufen unveraendert durch")
aus, w = durch_wandler(roh20)
l400, r800 = pegel(aus[RATE:-RATE, 0], 400), pegel(aus[RATE:-RATE, 1], 800)
pruefe(w.kanaele == 2 and not w.status()["mixer_aktiv"] and abs(db(l400) - db(0.1)) < 1.0 and abs(db(r800) - db(0.1)) < 1.0,
       "Dolby 2.0: L 400 Hz %.1f dB, R 800 Hz %.1f dB (Soll -20), Mixer aus" % (db(l400), db(r800)))
aus, w = durch_wandler(rohpcm)
n = len(aus)
gleich = np.max(np.abs(aus[1024:] - rohpcm[1024:n].astype(np.float32) / 32768.0))
pruefe(w.modus == "pcm" and gleich < 1e-6, "PCM bitgenau durch (groesste Abweichung %.1e), Modus %s" % (gleich, w.modus))

# ------------------------------------------------------- 3. Wechsel
print("\n3) Wechsel PCM -> Dolby 5.1 -> PCM")
folge = np.concatenate([rohpcm, roh51, rohpcm])
aus, w = durch_wandler(folge)
grenze1, grenze2 = len(rohpcm), len(rohpcm) + len(roh51)
im_dolby = aus[grenze1 + RATE:grenze2 - RATE]
spitze = np.max(np.abs(im_dolby))
erwartet = AMP * sum(q.mix_normiert(q.MIX_VOREINSTELLUNGEN["kino"]).values()) * 1.5
pruefe(spitze < erwartet, "waehrend Dolby: Spitze %.3f (Bitstrom-Rauschen waere ~1.0, Grenze %.2f)" % (spitze, erwartet))
nachher = aus[grenze2 + RATE:grenze2 + 3 * RATE]
pruefe(abs(db(pegel(nachher[:, 0], 1000)) - db(pegel(rohpcm[RATE:3 * RATE, 0] / 32768.0, 1000))) < 0.5 and w.modus == "pcm",
       "danach wieder PCM (Modus %s)" % w.modus)
# Uebergang: nichts Lautes in der Umschaltzone
zone = aus[grenze1 - 512:grenze1 + RATE // 2]
pruefe(np.max(np.abs(zone)) <= 1.0 and np.max(np.abs(aus[grenze1:grenze1 + RATE // 2])) < erwartet,
       "Umschaltzone ohne Bitstrom-Rauschen (Spitze %.3f)" % np.max(np.abs(aus[grenze1:grenze1 + RATE // 2])))

# ------------------------------------------------ 4. Sample-Verluste
print("\n4) Verlorene Samples wie beim UR27 (Buendel alle 10 s, ~3/s)")
rng = np.random.default_rng(1)
lang = np.concatenate([roh51] * 4)             # 32 s
weg = []
for start in range(5 * RATE, len(lang), 10 * RATE):
    weg += list(rng.integers(start, start + 3 * RATE, 28))
kaputt = np.delete(lang, sorted(set(weg)), axis=0)
aus, w = durch_wandler(kaputt)
pruefe(len(w.fehler) > 0, "%d von %d Bursts kaputt erkannt (%d Samples entfernt)" % (len(w.fehler), w.bursts, len(set(weg))))
pruefe(np.max(np.abs(aus)) < erwartet, "nie Rauschen: Spitze %.3f < %.2f" % (np.max(np.abs(aus)), erwartet))
pruefe(len(w.aus) <= (w.ziel_puffer or 0) + 2048, "Latenz stabil: Warteschlange %d (Soll %s) Frames" % (len(w.aus), w.ziel_puffer))
pruefe(len(aus) == len(kaputt) - len(kaputt) % 256, "Ausgang so lang wie Eingang (Takt des UR27)")

# --------------------------------------------- 5. echte Aufnahme
if len(sys.argv) > 1:
    import wave
    print("\n5) Echte Aufnahme", sys.argv[1])
    with wave.open(sys.argv[1]) as f:
        roh = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").reshape(-1, 2)
    aus, w = durch_wandler(roh)
    st = w.status()
    print("     Format %s, Layout %s, %d Kanaele, %d Bursts, %d kaputt, Spitze %.3f"
          % (st["format"], w.layout, w.kanaele, w.bursts, len(w.fehler), np.max(np.abs(aus))))
    pruefe(w.bursts > 0 and np.max(np.abs(aus)) <= 1.0, "Aufnahme verarbeitet")

print("\nERGEBNIS:", "alles OK" if fehler == 0 else "%d Fehler" % fehler, " (Arbeitsordner %s)" % ARBEIT)
sys.exit(1 if fehler else 0)
