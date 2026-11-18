#!/usr/bin/env python3
"""IR-Eingabedienst fuer TVDSP (FLIRC).

Konzept (drei Bereiche der IR-Seite):
 1. Stick anlernen: Das Backend programmiert per `flirc_util record` jede
    Fernbedienungstaste in einen Slot (Tastaturtaste a..l = "Taste 1..12").
    Solange FLIRC_LEARN_FILE existiert, gibt dieser Dienst das Geraet frei.
 2. Scan: Existiert IR_SCAN_FLAG, wird jedes Tastenereignis (code, value,
    Geraet) an /api/ir/event gemeldet.
 3. Zuweisung: IR_KEYS_FILE (Code -> Aktion) kommt vom Backend. Waehrend
    einer Zuweisung (IR_LEARN_REQ) wird jeder Druck an /api/ir/learned
    gemeldet statt eine Aktion auszuloesen.

Aktionen: vol_up / vol_down (Halten = weich wiederholen), mute (je Druck),
poweroff (erst nach 1,5 s HALTEN, damit kein versehentliches Herunterfahren).
Ohne Zuweisung gelten die Medientasten als Standard.
Jedes Ereignis geht ins Journal (journalctl -u tvdsp-flirc -f).
"""
import glob
import json
import os
import select
import sys
import time
import urllib.request

from evdev import InputDevice, ecodes, list_devices

HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
STATE_DIR = os.environ.get("STATE_DIR", "/opt/tvdsp/config")
FLIRC_LEARN_FILE = os.path.join(STATE_DIR, "flirc-learning")
IR_KEYS_FILE = os.path.join(STATE_DIR, "ir-keys.json")
IR_LEARN_REQ = os.path.join(STATE_DIR, "ir-learn")
IR_SCAN_FLAG = os.path.join(STATE_DIR, "ir-scan")
BASE = f"http://127.0.0.1:{HTTP_PORT}/api"
ACTION_URL = BASE + "/flirc/action/"
LEARNED_URL = BASE + "/ir/learned"
EVENT_URL = BASE + "/ir/event"

STANDARD = {
    ecodes.KEY_VOLUMEUP: "vol_up",
    ecodes.KEY_VOLUMEDOWN: "vol_down",
    ecodes.KEY_MUTE: "mute",
}
REPEAT_MIN = 0.12       # Halten = weich wiederholen, aber kein Event-Sturm
POWEROFF_HOLD = 1.5     # Sekunden halten fuer Ausschalten
_letzte_meldung = 0.0


def candidates():
    paths = []
    for p in glob.glob("/dev/input/by-id/*"):
        name = os.path.basename(p).lower()
        if "flirc" in name and "event" in name:
            paths.append(os.path.realpath(p))
    if not paths:
        for p in list_devices():
            try:
                d = InputDevice(p)
                name = (d.name or "").lower()
                d.close()
                if "flirc" in name:
                    paths.append(os.path.realpath(p))
            except Exception:
                pass
    return sorted(set(paths))


def open_devices():
    global _letzte_meldung
    out = []
    cands = candidates()
    if not cands:
        if time.monotonic() - _letzte_meldung > 30:
            print("IR: kein FLIRC-Eingabegeraet gefunden (/dev/input/by-id/*flirc*)",
                  flush=True)
            _letzte_meldung = time.monotonic()
        return out
    for p in cands:
        try:
            d = InputDevice(p)
            try:
                d.grab()
            except OSError:
                pass
            out.append(d)
            print(f"IR: lausche auf {d.path} ({d.name})", flush=True)
        except Exception as exc:
            print(f"IR: {p}: {exc}", file=sys.stderr, flush=True)
    return out


def close_all(devs):
    for d in devs:
        for fn in (d.ungrab, d.close):
            try:
                fn()
            except Exception:
                pass


def post(url, payload=None):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        json.load(r)


def learning():
    """Programmiert das Backend gerade den Stick? Eine liegengebliebene
    Markierung (Backend abgestuerzt) gilt nach 60 s nicht mehr."""
    try:
        return time.time() - os.path.getmtime(FLIRC_LEARN_FILE) < 60
    except OSError:
        return False


def lade_keymap():
    km = dict(STANDARD)
    try:
        with open(IR_KEYS_FILE) as f:
            roh = json.load(f)
        for k, v in roh.items():
            km[int(k)] = v
    except (FileNotFoundError, ValueError, TypeError):
        pass
    return km


def lese_lernanfrage():
    try:
        with open(IR_LEARN_REQ) as f:
            d = json.load(f)
        if time.time() - float(d.get("ts", 0)) <= 30:
            return d.get("action")
    except (FileNotFoundError, ValueError, TypeError):
        pass
    return None


def main():
    last = {}
    keymap = lade_keymap()
    keymap_mtime = 0.0
    poweroff_seit = None      # Zeitpunkt, seit dem die Ausschalt-Taste gehalten wird
    poweroff_gesendet = False
    while True:
        if learning():
            time.sleep(0.2)
            continue
        devs = open_devices()
        if not devs:
            time.sleep(3)
            continue
        try:
            while devs:
                if learning():
                    print("IR: Backend programmiert den Stick - Geraet freigegeben",
                          flush=True)
                    break
                try:
                    m = os.path.getmtime(IR_KEYS_FILE)
                    if m != keymap_mtime:
                        keymap = lade_keymap()
                        keymap_mtime = m
                except OSError:
                    pass
                ready, _, _ = select.select(devs, [], [], 0.5)
                if not ready:
                    if not all(os.path.exists(d.path) for d in devs):
                        break
                    continue
                for d in ready:
                    try:
                        events = list(d.read())
                    except OSError:
                        devs = []
                        break
                    scan = os.path.exists(IR_SCAN_FLAG)
                    for e in events:
                        if e.type != ecodes.EV_KEY:
                            continue
                        code = int(e.code)
                        print(f"IR: {d.name}: code={code} "
                              f"({ecodes.KEY.get(code, '?')}) value={e.value}",
                              flush=True)
                        if scan:
                            try:
                                post(EVENT_URL, {"code": code, "value": int(e.value),
                                                 "device": d.name or d.path})
                            except Exception:
                                pass

                        action = keymap.get(code)

                        # Ausschalten: Loslassen setzt den Haltezaehler zurueck.
                        if e.value == 0:
                            if action == "poweroff":
                                poweroff_seit = None
                                poweroff_gesendet = False
                            continue

                        # Zuweisung laeuft: Druck melden, keine Aktion.
                        lern_aktion = lese_lernanfrage()
                        if lern_aktion:
                            if e.value == 1:
                                try:
                                    post(LEARNED_URL, {"action": lern_aktion, "code": code})
                                    print(f"IR: Zuweisung {lern_aktion} <- Code {code}", flush=True)
                                except Exception as exc:
                                    print(f"IR: Zuweisung melden fehlgeschlagen: {exc}",
                                          file=sys.stderr, flush=True)
                            continue

                        if not action:
                            continue
                        now = time.monotonic()
                        if action == "poweroff":
                            if e.value == 1:
                                poweroff_seit = now
                                poweroff_gesendet = False
                                continue
                            if poweroff_seit is None or poweroff_gesendet:
                                continue
                            if now - poweroff_seit < POWEROFF_HOLD:
                                continue
                            poweroff_gesendet = True
                        elif action == "mute":
                            if e.value != 1:
                                continue
                        elif e.value == 2 and now - last.get(action, 0.0) < REPEAT_MIN:
                            continue
                        try:
                            post(ACTION_URL + action)
                            last[action] = now
                        except Exception as exc:
                            print(f"IR: Backend-Aktion {action} fehlgeschlagen: {exc}",
                                  file=sys.stderr, flush=True)
        finally:
            close_all(devs)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
