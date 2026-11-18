import sys, os, json, tempfile, importlib.util, time
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(); os.environ["STATE_DIR"] = tmp
class Vol:
    f = {0: -19.0, 1: 0.0, 3: -100.0}
    def set_volume(self, i, db): self.f[i] = db
    def volume(self, i): return self.f[i]
    def main_volume(self): return self.f[0]
    def set_main_volume(self, db): self.f[0] = db
    def main_mute(self): return False
    def set_main_mute(self, m): pass
class Cfg:
    aktiv = None; validiert = []
    def active(self): return self.aktiv
    def set_active(self, c): Cfg.aktiv = c
    def validate(self, c): Cfg.validiert.append(c); return c
    def read_and_parse_file(self, f): import yaml; return yaml.safe_load(open(f))
class D:
    def __getattr__(self, n): return D()
    def __call__(self, *a, **k): return None
class Cdsp:
    volume = Vol(); config = Cfg()
    def is_connected(self): return True
    def connect(self): pass
    def __getattr__(self, n): return D()
spec = importlib.util.spec_from_file_location("appmod", os.path.join(REPO, "backend", "app.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
import yaml
Cfg.aktiv = yaml.safe_load(open(os.path.join(REPO, "config", "tv.yml")))
mod.cdsp = Cdsp(); mod.verbindung["bereit"] = True
mod.CONFIG_YML = os.path.join(REPO, "config", "tv.yml") if hasattr(mod, "CONFIG_YML") else None
c = mod.app.test_client()
st = c.get("/api/status").get_json()
assert "regelung" not in st and "r128_settings" not in st, "R128-Reste im Status"
v = c.get("/api/verlauf").get_json(); assert "oben" in v["band"] and "regelung" not in v
r = c.post("/api/schalter", json={"regelung": "r128"}); assert "regelung" not in mod.state   # wird ignoriert
assert c.post("/api/r128/settings", json={"ziel": -18}).status_code in (404, 405)       # Endpunkt entfernt
mod.band_anwenden(); cfg = Cfg.aktiv
dk = cfg["processors"]["band_decke"]["parameters"]; assert dk["factor"] == 20.0
namen = [x for step in cfg["pipeline"] for x in (step.get("names") or [])]
assert "band_vorlauf" in namen and any("ld" in n for n in namen), namen
save = mod.save_settings(); gespeichert = json.load(open(os.path.join(tmp, "settings.json")))
assert not [k for k in gespeichert if k.startswith("r128_") or k in ("regelung", "band_decke_bezug")], "R128-/Bezug-Schluessel gespeichert"
print("API-Test OK (2.9.0: keine R128-Regelung, Decke fest)")
# ---- 2.7.0: Pegel auf Aux2 hinter der Decke, Main bleibt 0
cfg = Cfg.aktiv
namen = [x for step in cfg["pipeline"] for x in (step.get("names") or [])]
assert namen[-2:] == ["ausgang", "limiter"], namen[-4:]
assert cfg["filters"]["ausgang"]["parameters"]["fader"] == "Aux2"
assert mod.ausgang_in_pipeline()
r = c.post("/api/schalter", json={"volume": -20.0}); assert r.status_code == 200, r.get_json()
assert Vol.f[2] == -20.0 and Vol.f[0] == -19.0, Vol.f     # Aux2 gesetzt, Main unangetastet
assert mod.state["volume"] == -20.0
mod.state.update({"kalibrierung_schnell": 2.973, "kalibrierung_schnell_gueltig": True})
r = c.post("/api/band/settings", json={"oben": -23.0, "unten": -25.0, "decke_bezug": "dialog"}); assert r.status_code == 200  # Bezug wird ignoriert
thr = mod.band_decke_dbfs(); print("Decke-Schwelle (oben -23, effekte, kal 2.97): %.2f dBFS" % thr)
assert abs(thr - (-23 + 1 - 2.973 - 6 - 0.6 + 3.6)) < 1e-6
assert abs(Cfg.aktiv["processors"]["band_decke"]["parameters"]["threshold"] - thr) < 1e-6
import yaml as _y; _y.safe_dump(Cfg.aktiv, open(os.path.join(os.environ.get("APITEST_OUT", tmp), "neu_cfg.yml"), "w"))
print("Aux2-Pegel-Test OK")
# ---- 2.8.0/2.8.1: Pegel wird aus dem echten Aux2-Fader zurueckgelesen,
# aber nur im normalen Betrieb (Main frei). Sonst wuerde der sichere
# Startwert -35 dB den gespeicherten Pegel ueberschreiben.
Vol.f[0] = -35.0                      # Dienst-Startwert, Main noch nicht frei
Vol.f[2] = -35.0
mod.state["volume"] = -25.5
st = c.get("/api/status").get_json()
assert abs(st["volume"] - (-25.5)) < 0.01 and abs(mod.state["volume"] - (-25.5)) < 0.01, st["volume"]
Vol.f[0] = 0.0                        # Betrieb: Main frei
Vol.f[2] = -30.0                      # z. B. per Fernbedienung verstellt
st = c.get("/api/status").get_json()
assert abs(st["volume"] - (-30.0)) < 0.01 and abs(mod.state["volume"] - (-30.0)) < 0.01, st["volume"]
# ---- 2.8.1: CamillaDSP-Neustart wird an der Decke-Schwelle erkannt
assert mod._band_decke["gesendet"] == Cfg.aktiv["processors"]["band_decke"]["parameters"]["threshold"]
assert mod.camilladsp_neu_gestartet() is False
Cfg.aktiv["processors"]["band_decke"]["parameters"]["threshold"] = -47.0   # Kaltstart-tv.yml
assert mod.camilladsp_neu_gestartet() is True
mod.pipeline_und_pegel_herstellen("Test")
assert abs(Cfg.aktiv["processors"]["band_decke"]["parameters"]["threshold"] - mod.band_decke_dbfs()) < 1e-6
assert abs(Vol.f[2] - mod.state["volume"]) < 0.01 and abs(Vol.f[0]) < 0.01, (Vol.f, mod.state["volume"])
assert mod._fader_erneuern.is_set()
mod._fader_erneuern.clear()
# Detektor der Decke haengt hinter dem Loudness-Pfad
d2 = Cfg.aktiv["mixers"]["band_zusammen"]["mapping"][2]["sources"]
assert [q["channel"] for q in d2] == [0, 3, 5, 1, 4, 6] and all(q["gain"] == -6.0 for q in d2), d2
assert [q["inverted"] for q in d2] == [False, False, True, False, False, True], d2
print("2.8.0-Test OK (Pegel-Rueckmeldung, Detektor)")
# ---- 2.8.5/2.8.6: EQ-Baender mit 1 Oktave Bandbreite, glatte Kurve
mod.state["eq"] = [0, 0, 0, 0, 0.2, 0.6, 0.8, 0.6, 0.25, 0, -0.3, -0.6, -0.4, -1.2, -3.0, -2.9]
mod.band_anwenden()
for n in mod.EQ_NAMES:
    par = Cfg.aktiv["filters"][n]["parameters"]
    assert "q" not in par and par["bandwidth"] == 1.0, par
# Umrechnung: jeder Punkt behaelt seine Wirkung, dazwischen keine Beule
alt = list(mod.state["eq"]); q = mod.SPREAD["normal"]["q"]
punkte = [sum(mod._peaking_db(f, mod.BANDS[k], alt[k], q, True) for k in range(16)) for f in mod.BANDS]
neu = mod.eq_glatt_loesen(punkte)
wirk = lambda f, gg: sum(mod._band_db_bw(f, mod.BANDS[k], gg[k], 1.0) for k in range(16))
assert max(abs(wirk(f, neu) - p) for f, p in zip(mod.BANDS, punkte)) < 0.05
import math as _m
for k in range(15):                      # zwischen zwei Punkten nie ueber die hoehere hinaus
    lo, hi = mod.BANDS[k], mod.BANDS[k + 1]
    for t in (0.25, 0.5, 0.75):
        f = _m.exp(_m.log(lo) + t * (_m.log(hi) - _m.log(lo)))
        assert wirk(f, neu) <= max(punkte[k], punkte[k + 1]) + 0.25, (lo, hi, t)
print("12,6 kHz: vorher %+.2f dB, jetzt %+.2f dB (Punkte 10k %+.2f / 16k %+.2f)" % (
    sum(mod._peaking_db(12600, mod.BANDS[k], alt[k], q, True) for k in range(16)), wirk(12600, neu), punkte[14], punkte[15]))
print("2.8.6-Test OK (1 Oktave, glatte Kurve, Punkte unveraendert)")
# ---- 2.8.7: keine Fehlalarme bei Bypass/R128; Profil nur einmal pro Start
for bypass in (True, False):
    mod.state["bypass"] = bypass
    mod.band_anwenden(neutral=bypass)
    assert mod.camilladsp_neu_gestartet() is False, bypass
thr_neutral = Cfg.aktiv["processors"]["band_decke"]["parameters"]["threshold"]
mod.state["bypass"] = True; mod.band_anwenden(neutral=True)
assert Cfg.aktiv["processors"]["band_decke"]["parameters"]["threshold"] == 0.0
assert mod.camilladsp_neu_gestartet() is False            # bis 2.8.6: True (Fehlalarm)
Cfg.aktiv["processors"]["band_decke"]["parameters"]["threshold"] = -47.0   # echter Kaltstart
assert mod.camilladsp_neu_gestartet() is True
mod.state["bypass"] = False; mod.band_anwenden()
import tempfile as _tf, time as _t
_d = _tf.mkdtemp(); _m = os.path.join(_d, "dsp-profil-pruefsumme")
open(_m, "w").write("7f88caad8e8a7e4f05731605ae16ef38\n")
assert mod.dsp_profil_aus_marker(_m) == mod.DSP_PROFIL_SOLL
_alt = _t.time() - float(open("/proc/uptime").read().split()[0]) - 3600
os.utime(_m, (_alt, _alt))                                # aus einem frueheren Start
assert mod.dsp_profil_aus_marker(_m) is None
assert not hasattr(mod, "DSP_PROFIL_INTERVALL")
print("2.8.7-Test OK (Neustart-Erkennung ohne Fehlalarm, Profil einmal pro Start)")
# ---- 2.8.8: billige Neustart-Wache, seltene Konfig-Abfragen
mod.state["bypass"] = False
Vol.f[0] = -35.0; Vol.f[2] = -35.0
mod.pipeline_und_pegel_herstellen("Test")
assert mod._main_frei["ok"] and abs(Vol.f[0]) < 0.01
assert mod.neustart_verdacht() is False                   # Betrieb: Main 0 dB
Vol.f[0] = -35.0                                          # CamillaDSP neu gestartet
assert mod.neustart_verdacht() is True
mod._main_frei["ok"] = False                              # Freigabe gescheitert: kein Dauer-Alarm
assert mod.neustart_verdacht() is False
assert mod.GERAET_NACHLESEN_SEK >= 60 and mod.KALIB_SPEICHERN_SEK >= 600
assert mod.geraet["chunk"] == Cfg.aktiv["devices"]["chunksize"]   # von band_anwenden eingetragen
import logging as _lg
assert _lg.getLogger("werkzeug").level == _lg.WARNING
print("2.8.8-Test OK (Neustart-Wache ueber Main, Konfig selten, Protokoll ruhig)")
# ---- 2.9.0: EQ-Spiegel nach Start ohne CamillaDSP wird nachgeholt
mod.verbindung["bereit"] = False
k = mod.r128_filterkette(); assert "equalizer" not in k and mod._r128_eq["flach"] is True
mod.verbindung["bereit"] = True
mod.state["eq"] = [0.5] * 16; mod.band_anwenden()
k = mod.r128_filterkette(); assert k.count("equalizer=") == 16 and mod._r128_eq["flach"] is False
print("2.9.0-Test OK (EQ-Spiegel wird nach Verbindung nachgeholt)")
# ---- 3.0.0: USB-Eingang, Mix, Rueckfall auf die HiFiBerry
import subprocess as _sp
e = c.get("/api/eingang").get_json(); assert e["gewaehlt"] == "hifiberry" and e["aktiv"] == "hifiberry"
assert c.post("/api/eingang", json={"eingang": "quatsch"}).status_code == 400
e = c.post("/api/eingang", json={"eingang": "usb"}).get_json(); assert mod.state["eingang"] == "usb"
q = json.load(open(os.path.join(tmp, "quelle.json"))); assert q["eingang"] == "usb" and abs(sum(q["mix"].values()) - 1) < 1e-3
# ohne frischen Quellen-Status: Rueckfall
assert mod.eingang_soll() == "hifiberry"
_st = os.path.join(tmp, "quelle-status.json"); mod.QUELLE_STATUS = _st
json.dump({"zeit": time.time(), "laeuft": True, "format": "Dolby Digital", "layout": "5.1(side)",
           "kanaele": 6, "mixer_aktiv": True, "fehler_60s": 3}, open(_st, "w"))
mod._quelle_cache["zeit"] = 0.0
_usb = mod.usb_empfaenger; mod.usb_empfaenger = lambda: "miniDSP MCHStreamer"   # Empfaenger "steckt"
assert mod.eingang_soll() == "usb"
mod.eingang_umschalten("usb", "Test"); cfg = Cfg.aktiv
assert cfg["devices"]["capture"]["device"] == "dsnoop_usb" and cfg["devices"]["enable_rate_adjust"] is True
assert cfg["devices"]["resampler"]["type"] == "AsyncSinc" and mod.mess_geraet() == "tvmess_usb"
_yml = os.path.join(tmp, "usb.yml"); yaml.safe_dump(cfg, open(_yml, "w"))
_r = _sp.run(["/usr/local/bin/camilladsp", "--check", _yml], capture_output=True, text=True)
assert "Config is valid" in (_r.stdout + _r.stderr), _r.stderr[-400:]
k = c.get("/api/status").get_json()["eingang"]; assert k["mixer_aktiv"] and not k["rueckfall"] and k["layout"] == "5.1(side)"
# Status veraltet -> zurueck auf die HiFiBerry, bitgenau ohne Resampler
json.dump({"zeit": time.time() - 30, "laeuft": True}, open(_st, "w")); mod._quelle_cache["zeit"] = 0.0
assert mod.eingang_soll() == "hifiberry"
mod.eingang_umschalten("hifiberry", "Test"); cfg = Cfg.aktiv
assert cfg["devices"]["capture"]["device"] == "dsnoop_tv" and cfg["devices"]["enable_rate_adjust"] is False
assert cfg["devices"].get("resampler") is None and mod.mess_geraet() == mod.R128_DEV
assert c.get("/api/eingang").get_json()["rueckfall"] is True
assert c.get("/api/eingang").get_json()["geraet"] == "miniDSP MCHStreamer"
mod.usb_empfaenger = _usb
# Mix: Normierung, Voreinstellungen, Erkennung
m = c.post("/api/mix", json={"mix": {"front": 1, "center": 1, "lfe": 0, "surround": 2}}).get_json()
assert abs(m["mix"]["surround"] - 0.5) < 1e-3 and m["voreinstellung"] == "eigen"
m = c.post("/api/mix", json={"voreinstellung": "sprache"}).get_json(); assert m["mix"]["center"] == 0.5
m = c.post("/api/mix", json={"mix": dict(mod.MIX_VOREINSTELLUNGEN["kino"])}).get_json(); assert m["voreinstellung"] == "kino"
assert c.post("/api/mix", json={"voreinstellung": "gibtsnicht"}).status_code == 400
assert json.load(open(os.path.join(tmp, "quelle.json")))["mix"] == m["mix"]
mod.state["eingang"] = "hifiberry"
print("3.0.0-Test OK (Eingang USB/HiFiBerry mit Rueckfall, Resampler-Konfig gueltig, Mix)")
# ---- 3.2.0: Hoehendaempfung (ersetzt die Messgewichtung aus 3.1.0)
import subprocess as _sp2, math
mod.verbindung["bereit"] = True
assert not hasattr(mod, "HOEHEN_GEWICHTUNG") and "highshelf" not in mod.r128_filterkette()
mod.state["bypass"] = False; mod.state["hoehen_daempfung"] = False; mod.band_anwenden()
assert "hoehen_expand" not in json.dumps(Cfg.aktiv["pipeline"])          # aus = Pipeline wie bisher
_alt = mod.hoehen_nachziehen; mod.hoehen_nachziehen = lambda: None        # kein ffmpeg im Test
assert c.post("/api/schalter", json={"hoehen_daempfung": True}).get_json()["ok"]
cfg = Cfg.aktiv; namen = [s.get("name") for s in cfg["pipeline"]]
assert namen.index("collapse") < namen.index("hoehen_expand") < namen.index("hoehen_summe")
assert cfg["pipeline"][-1]["names"] == ["ausgang", "limiter"] and Vol.f[4] <= -99
_yml = os.path.join(tmp, "hoehen.yml"); yaml.safe_dump(cfg, open(_yml, "w"))
_r = _sp2.run(["/usr/local/bin/camilladsp", "--check", _yml], capture_output=True, text=True)
assert "Config is valid" in (_r.stdout + _r.stderr), _r.stderr[-400:]
mod.band_anwenden(neutral=True); assert "hoehen_expand" not in json.dumps(Cfg.aktiv["pipeline"])  # Bypass: aus
assert json.load(open(os.path.join(tmp, "settings.json")))["hoehen_daempfung"] is True
st = c.get("/api/status").get_json(); assert st["hoehen_daempfung"] is True and "daempfung_db" in st["hoehen"]
# Regel: Anteil -8 -> 0 dB, -5 -> 3 dB (eingeschwungen), Stille haelt, Deckel 6 dB
r = mod.HoehenRegler()
for _ in range(200): r.schritt(-20.0, -28.0)
assert abs(r.daempfung) < 0.01
for _ in range(200): r.schritt(-20.0, -25.0)
assert abs(r.daempfung - 3.0) < 0.05
d = r.daempfung; r.schritt(-60.0, -70.0); assert r.daempfung == d
for _ in range(200): r.schritt(-20.0, -10.0)
assert abs(r.daempfung - 6.0) < 0.05
# Fader: 6 dB Daempfung = volle Shelf-Kurve (0 dB), 0 dB = aus
assert mod.hoehen_fader_db(6.0) == 0.0 and mod.hoehen_fader_db(0.0) == mod.HOEHEN_AUS_DB
a3 = 10 ** (mod.hoehen_fader_db(3.0) / 20); g = 1 + a3 * (10 ** (-6 / 20) - 1)
assert abs(20 * math.log10(g) + 3.0) < 1e-6
c.post("/api/schalter", json={"hoehen_daempfung": False}); mod.state["bypass"] = False
assert "hoehen_expand" not in json.dumps(Cfg.aktiv["pipeline"])
mod.hoehen_nachziehen = _alt
print("3.2.0-Test OK (Hoehendaempfung: Stufe nur wenn an, nicht im Bypass, Regel und Fader exakt)")
# ---- 3.3.7: Lauter/Leiser hebt Stumm auf
class _M:
    m = True
_alt_mm, _alt_sm = Vol.main_mute, Vol.set_main_mute
Vol.main_mute = lambda self: _M.m
def _sm(self, m): _M.m = m
Vol.set_main_mute = _sm
import types as _t
_aufruf = lambda a: c.post("/api/flirc/action/" + a, environ_base={"REMOTE_ADDR": "127.0.0.1"})
mod.state["volume"] = -20.0
assert _aufruf("vol_up").get_json()["ok"] and _M.m is False                 # Fernbedienung lauter
_M.m = True; assert _aufruf("vol_down").get_json()["ok"] and _M.m is False  # leiser
_M.m = True; c.post("/api/schalter", json={"volume": -21.0}); assert _M.m is False   # Pegelregler
_M.m = False; assert _aufruf("mute").get_json()["mute"] is True and _M.m is True     # Mute-Taste schaltet ein
c.post("/api/schalter", json={"mute": True}); assert _M.m is True                     # Mute-Schalter bleibt
Vol.main_mute, Vol.set_main_mute = _alt_mm, _alt_sm
print("3.3.7-Test OK (Lauter/Leiser und Pegelregler heben Stumm auf, Mute-Taste unveraendert)")
