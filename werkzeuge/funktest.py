#!/usr/bin/env python3
"""TVDSP-Funktionstest: prueft die WIRKUNG, nicht nur die uebergebenen Werte.

Die exakte laufende CamillaDSP-Konfiguration wird mit den exakten laufenden
Fader-Werten offline durch das echte camilladsp geschickt und gemessen, ob jede
Funktion tut, was das Backend annimmt. Am laufenden Geraet wird nichts
veraendert (nur gelesen).

Warum es diesen Test gibt: Der 25-dB-Fehler (bis 2.6.x) hatte korrekt
berechnete und korrekt uebergebene Werte - falsch war die Annahme, wo
CamillaDSP die Hauptlautstaerke anwendet. Eine Werte-Pruefung findet so etwas
nie, dieser Test sofort (die Decke haette nichts abgesenkt).

Aufruf auf dem Pi (Backend und CamillaDSP laufen):
    /opt/tvdsp/venv/bin/python3 werkzeuge/funktest.py [TV-Mitschnitt.mp3]
Ohne Angabe wird der neueste ~/Downloads/tvdsp_*.mp3 benutzt (Records-Reiter).
Rueckgabewert 0 = alles in Ordnung, 1 = mindestens eine Abweichung.
"""
import copy, glob, importlib.util, json, logging, math, os, re, subprocess, sys, tempfile
import numpy as np
import yaml

FS = 48000
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARBEIT = tempfile.mkdtemp(prefix="tvdsp-funktest-")
os.chdir(ARBEIT)

# Backend-Modul mit einer KOPIE der Einstellungen laden (nichts wird zurueckgeschrieben)
os.environ["STATE_DIR"] = ARBEIT
einst = "/opt/tvdsp/config/settings.json"
if os.path.exists(einst):
    open(os.path.join(ARBEIT, "settings.json"), "w").write(open(einst).read())
spec = importlib.util.spec_from_file_location("app", os.path.join(REPO, "backend", "app.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logging.getLogger().setLevel(logging.ERROR)
if os.path.exists(einst):
    g = json.load(open(einst)); m.state.update({k: v for k, v in g.items() if k in m.state})

from camilladsp import CamillaClient
c = CamillaClient("127.0.0.1", 1234); c.connect()
LIVE = c.config.active(); FAD = [c.volume.volume(i) for i in range(4)]
m.geraet["vorpegel"] = float(LIVE["filters"]["vorpegel"]["parameters"]["gain"])
print("Live: Main %.1f | Aux1 %.2f | Aux2 %.1f | Aux3 %.1f dB | Band %.1f..%.1f LUFS | Decke-Schwelle %.2f dBFS"
      % (FAD[0], FAD[1], FAD[2], FAD[3], m.band_unten(), m.band_oben(),
         LIVE["processors"]["band_decke"]["parameters"]["threshold"]))
ERG = []


def ergebnis(name, ok, text):
    ERG.append(ok); print(("  OK  " if ok else "  !!  ") + name + ": " + text)


def variante(eq=None, neutral=False, decke_neutral=False, live=False):
    if live:
        return copy.deepcopy(LIVE)
    cfg, _ = m.band_konfiguration(copy.deepcopy(LIVE), list(m.state["eq"] if eq is None else eq), neutral)
    if decke_neutral:
        cfg["processors"]["band_decke"]["parameters"].update(threshold=0.0, factor=1.0)
    return cfg


def lauf(cfg, x, main=0.0, a1=0.0, a2=0.0, a3=-100.0, a4=-100.0):
    cfg = copy.deepcopy(cfg); x.astype(np.float32).tofile("ein.raw")
    d = cfg["devices"]; d["silence_timeout"] = 0.0
    d["capture"] = {"type": "RawFile", "channels": 2, "filename": "ein.raw", "format": "F32_LE"}
    d["playback"] = {"type": "File", "channels": 2, "filename": "aus.raw", "format": "F32_LE"}
    yaml.safe_dump(cfg, open("t.yml", "w"))
    r = subprocess.run(["camilladsp", "--loglevel=error", "--gain=%s" % main, "--gain1=%s" % a1,
                        "--gain2=%s" % a2, "--gain3=%s" % a3, "--gain4=%s" % a4, "t.yml"], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-600:]); sys.exit(1)
    return np.fromfile("aus.raw", dtype=np.float32).reshape(-1, 2)


def db(v): return 20 * math.log10(max(v, 1e-15))
def rms(x): return db(np.sqrt(np.mean(np.asarray(x, float) ** 2)))


def sinus(f, pegel, sek=3):
    t = np.arange(int(FS * sek)) / FS
    s = 10 ** (pegel / 20) * math.sqrt(2) * np.sin(2 * math.pi * f * t); return np.stack([s, s], 1)


def impuls(n=65536):
    x = np.zeros((n, 2)); x[100, :] = 0.5; return x


def antwort(y, f):
    H = np.fft.rfft(y[:, 0].astype(float)); fr = np.fft.rfftfreq(len(y), 1 / FS)
    return 20 * np.log10(np.abs(H[np.argmin(abs(fr - f))]) / 0.5 + 1e-15)


FLACH = [0.0] * 16
ergebnis("Betriebspunkt: Main frei (0 dB), Pegel auf Aux2", abs(FAD[0]) < 0.05, "Main %.2f dB" % FAD[0])

print("\n1) Fader wirken dort und so stark, wie das Backend annimmt (1 kHz, -40 dBFS, EQ flach, Decke neutral):")
cfg = variante(eq=FLACH, decke_neutral=True); x = sinus(1000, -40)
basis = rms(lauf(cfg, x)[FS:])
ergebnis("Kette neutral (Vorpegel -6 + Makeup +6)", abs(basis + 40) < 0.05, "Ausgang %.2f dBFS bei Eingang -40,00" % basis)
for name, kw, soll in (("Aux1 +6 (Regelung)", dict(a1=6.0), 6.0), ("Aux2 -27,5 (Pegel)", dict(a2=-27.5), -27.5),
                       ("Main -10", dict(main=-10.0), -10.0)):
    d = rms(lauf(cfg, x, **kw)[FS:]) - basis
    ergebnis(name, abs(d - soll) < 0.05, "wirkt %+.2f dB (soll %+.2f)" % (d, soll))

print("\n2) Klangkurven: gemessen = gezeichnet")
fq = [30, 60, 120, 250, 500, 1000, 3000, 6000, 10000, 16000]
json.dump({"f": fq, "g": list(m.state["eq"])}, open("fq.json", "w"))
HTML = os.path.join(REPO, "backend", "static", "index.html")
def js(ausdruck):
    code = """
const fs=require('fs');const h=fs.readFileSync(%r,'utf8');
const grab=n=>h.match(new RegExp('function '+n+'\\\\([\\\\s\\\\S]*?\\\\n}\\\\n'))[0];
const EQ_BW=%s; const B=%s; eval(grab('peakingDb')+grab('ldShelf')+grab('ldEval')+grab('ldKurveDb'));
const d=JSON.parse(fs.readFileSync('fq.json')); console.log(JSON.stringify(d.f.map(f=>%s)));""" % (
        HTML, m.EQ_BANDBREITE, json.dumps(m.BANDS), ausdruck)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None
y = lauf(variante(decke_neutral=True), impuls())
gem = [float(antwort(y, f)) for f in fq]
gez = js("B.reduce((s,f0,i)=>s+peakingDb(f,f0,d.g[i]),0)")
if gez is None:
    ergebnis("EQ", False, "Zeichenformel nicht ausfuehrbar (node?)")
else:
    ergebnis("EQ (deine Kurve)", max(abs(p - q) for p, q in zip(gem, gez)) < 0.05,
             "max. Abweichung %.3f dB" % max(abs(p - q) for p, q in zip(gem, gez)))
for a3 in (0.0, FAD[3]):
    y = lauf(variante(eq=FLACH, decke_neutral=True), impuls(), a3=a3)
    gem = [float(antwort(y, f)) for f in fq]; a = 10 ** (a3 / 20)
    gez = js("ldKurveDb(%s,f)" % a)
    if gez is None:
        ergebnis("Psychoakustik", False, "Zeichenformel nicht ausfuehrbar"); continue
    mx = max(abs(p - q) for p, q in zip(gem, gez))
    ergebnis("Psychoakustik bei Aux3 %.1f dB (%.0f %%)" % (a3, 100 * a), mx < 0.05, "max. Abweichung %.3f dB" % mx)

print("\n3) Vorlauf, Bypass, Limiter")
y = lauf(variante(eq=FLACH, decke_neutral=True), impuls())
soll = int(round(m.band_vorlauf_ms() * FS / 1000)) if m.band_vorlauf_aktiv() else 0
ergebnis("Vorlauf-Verzoegerung", int(np.argmax(abs(y[:, 0]))) - 100 == soll,
         "%d Samples (soll %d)" % (int(np.argmax(abs(y[:, 0]))) - 100, soll))
rng = np.random.default_rng(1); x = rng.normal(0, 0.1, (FS * 4, 2))
y = lauf(variante(neutral=True), x)
diff = np.max(np.abs(y[soll:len(x), :] - x[:len(x) - soll, :]))
ergebnis("Bypass bitgenau neutral", diff < 1e-5, "groesste Abweichung %.1e" % diff)
laut = rng.normal(0, 0.3, (FS * 4, 2))
y = lauf(variante(eq=FLACH, decke_neutral=True), laut, a1=20.0)
ergebnis("Limiter: nie ueber -2 dBFS", db(np.max(np.abs(y))) <= -2.0 + 1e-3, "Spitze %.2f dBFS" % db(np.max(np.abs(y))))

print("\n4) Decke am echten Arbeitspunkt (Live-Konfiguration inkl. EQ, Main/Aux2 wie live, Aux1 +9,7, TV-Mitschnitt)")
quelle = sys.argv[1] if len(sys.argv) > 1 else (sorted(glob.glob(os.path.expanduser("~/Downloads/tvdsp_*.mp3")), key=os.path.getmtime) or [None])[-1]
if not quelle or not os.path.exists(quelle):
    print("  --  kein Mitschnitt gefunden - Decke nicht geprueft (Records-Reiter: Mitschnitt aufnehmen)")
else:
    dauer = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", quelle],
                                 capture_output=True, text=True).stdout or 0)
    start = 300 if dauer > 400 else 0
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", "90", "-i", quelle, "-ar", "48000", "-ac", "2",
                    "-f", "f32le", "ein90.raw"], check=True)
    x = np.fromfile("ein90.raw", dtype=np.float32).reshape(-1, 2).astype(float)
    eqkette = ",".join("equalizer=f=%s:width_type=o:width=%s:g=%+.4f" % (m.BANDS[i], m.EQ_BANDBREITE, gg)
                       for i, gg in enumerate(m.state["eq"]) if abs(gg) >= 0.005)
    def ebur(datei, mit_eq=False):
        kette = ((eqkette + ",") if (mit_eq and eqkette) else "") + "ebur128=framelog=info"
        r = subprocess.run(["ffmpeg", "-v", "info", "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", datei, "-af", kette,
                            "-f", "null", "-"], capture_output=True, text=True)
        return [(float(t), float(mm), float(ss)) for t, mm, ss in
                re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+)\s+S:\s*(-?[\d.]+)", r.stderr)]
    # Kalibrierung dieses Materials genau wie im Backend: S (mit EQ-Spiegel) minus Leistungsmittel 3 s Roh-RMS
    ein_eq = ebur("ein90.raw", mit_eq=True); p = np.mean(x ** 2, axis=1); kk = []
    for t, mm, ss in ein_eq:
        i = int(t * FS)
        if t >= 3 and ss > -70:
            kk.append(ss - 10 * math.log10(np.mean(p[i - 3 * FS:i]) + 1e-20))
    kal = float(np.median(kk))
    thr = LIVE["processors"]["band_decke"]["parameters"]["threshold"]
    decke = thr + kal - m.geraet["vorpegel"] - m.BAND_DECKE_BEZUG_EFFEKTE - m.BAND_DECKE_DETEKTOR_DB
    y = lauf(variante(live=True), x, main=FAD[0], a1=9.7, a2=FAD[2], a3=-100.0)
    y.astype(np.float32).tofile("aus90.raw"); aus = ebur("aus90.raw")
    k = min(len(ein_eq), len(aus))
    paare = [(ein_eq[i][1] + 9.7, aus[i][1] - FAD[2]) for i in range(k) if ein_eq[i][1] > -60]
    ung = sorted(a for a, b in paare); ges = sorted(b for a, b in paare)
    p95u, p95g = ung[int(.95 * len(ung))], ges[int(.95 * len(ges))]
    anteil = 100 * sum(v > decke for v in ung) / len(ung)
    print("  Mitschnitt %s ab %d s | Kalibrierung dieses Materials %.2f dB -> Decke hier bei %.1f LUFS" % (
        os.path.basename(quelle), start, kal, decke))
    if anteil < 10:
        print("  --  zu wenig Material ueber der Decke (%.0f %%) - Aussage schwach" % anteil)
    ergebnis("Decke senkt ab (ohne sie waeren %.0f %% ueber ihr)" % anteil, p95g <= p95u - 2.0,
             "p95 %.1f -> %.1f LUFS, Maximum %.1f -> %.1f" % (p95u, p95g, ung[-1], ges[-1]))
    ergebnis("Decke haelt ihre Hoehe (Toleranz +4,5 dB fuer 400-ms-Messung/Detektor)", p95g <= decke + 4.5,
             "p95 %.1f LUFS bei Decke %.1f (%+.1f dB)" % (p95g, decke, p95g - decke))
    unter = [b - a for a, b in paare if a < decke - 3]
    if unter:
        ergebnis("unterhalb der Decke keine Absenkung", abs(float(np.median(unter))) < 1.0,
                 "Median %+.2f dB (Ausgang minus Eingang mit EQ)" % float(np.median(unter)))

print("\n5) Hoehendaempfung (3.2.0): Stufe hinter der Decke, Wirkung ueber Aux4")
alt = m.state.get("hoehen_daempfung")
m.state["hoehen_daempfung"] = False; aus_cfg = variante(eq=FLACH, decke_neutral=True)
m.state["hoehen_daempfung"] = True; an_cfg = variante(eq=FLACH, decke_neutral=True)
m.state["hoehen_daempfung"] = alt
ergebnis("Schalter aus: Stufe nicht in der Pipeline", "hoehen_expand" not in json.dumps(aus_cfg["pipeline"]), "")
y0 = lauf(aus_cfg, impuls()); y1 = lauf(an_cfg, impuls(), a4=m.HOEHEN_AUS_DB)
ergebnis("an, Daempfung 0 (Aux4 -100): wie aus", float(np.max(np.abs(y1 - y0))) < 1e-5,
         "groesste Abweichung %.1e" % float(np.max(np.abs(y1 - y0))))
for d in (3.0, 6.0):
    y = lauf(an_cfg, impuls(), a4=m.hoehen_fader_db(d))
    tief = antwort(y, 200) - antwort(y0, 200); hoch = antwort(y, 12000) - antwort(y0, 12000)
    ergebnis("Daempfung %.0f dB" % d, abs(tief) < 0.1 and abs(hoch + d) < 0.2,
             "200 Hz %+.2f dB, 12 kHz %+.2f dB (soll %+.1f)" % (tief, hoch, -d))

fehler = sum(1 for e in ERG if not e)
print("\nERGEBNIS: %d OK, %d Abweichungen   (Arbeitsordner %s)" % (len(ERG) - fehler, fehler, ARBEIT))
sys.exit(1 if fehler else 0)
