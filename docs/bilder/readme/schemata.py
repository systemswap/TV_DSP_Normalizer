#!/usr/bin/env python3
"""Zeichnet die Uebersichtsgrafiken der Startseite als SVG (ohne Fremdbilder).

    python3 docs/bilder/readme/schemata.py

Erzeugt prozessfluss.svg (Weg des Tons vom Fernseher bis zu den Lautsprechern)
und aufbau.svg (Verkabelung der Bauteile) neben diesem Skript. Alle Bauteile
sind selbst gezeichnet; Farben wie banner.svg.
"""
import os

HIER = os.path.dirname(os.path.abspath(__file__))
SCHRIFT = "Segoe UI, Helvetica, Arial, sans-serif"

# Farben
BG1, BG2 = "#141922", "#1f2733"
KARTE, RAND = "#222b38", "#334052"
TEXT, TEXT2, TEXT3 = "#ffffff", "#c9d1db", "#8b96a5"
GRUEN, ORANGE, GELB, BLAU = "#4fc3a1", "#e5644e", "#f0b44c", "#5aa9e6"
TOSLINK = "#ff9a3c"


class Svg:
    def __init__(self, b, h):
        self.b, self.h, self.teile = b, h, []

    def add(self, s):
        self.teile.append(s)

    def text(self, x, y, s, gr=20, farbe=TEXT, gew=400, anker="start", stil=""):
        zeilen = s.split("\n")
        for i, z in enumerate(zeilen):
            z = z.replace("&", "&amp;").replace("<", "&lt;")
            self.add(f'<text x="{x}" y="{y + i * gr * 1.25:.1f}" fill="{farbe}" font-size="{gr}" '
                     f'font-weight="{gew}" text-anchor="{anker}" {stil}>{z}</text>')

    def karte(self, x, y, b, h, rand=RAND, fuell=KARTE, r=16, schatten=True, strich=2, extra=""):
        f = ' filter="url(#schatten)"' if schatten else ""
        self.add(f'<rect x="{x}" y="{y}" width="{b}" height="{h}" rx="{r}" fill="{fuell}" '
                 f'stroke="{rand}" stroke-width="{strich}"{f} {extra}/>')

    def pfeil(self, d, farbe, breite=5, strich="", spitze=True):
        m = f' marker-end="url(#spitze-{farbe[1:]})"' if spitze else ""
        s = f' stroke-dasharray="{strich}"' if strich else ""
        self.add(f'<path d="{d}" fill="none" stroke="{farbe}" stroke-width="{breite}" '
                 f'stroke-linecap="round" stroke-linejoin="round"{s}{m}/>')

    def chip(self, x, y, s, farbe, gr=17, b=None):
        b = b or int(len(s) * gr * 0.58 + 28)
        self.add(f'<rect x="{x}" y="{y}" width="{b}" height="{gr + 16}" rx="{(gr + 16) / 2}" '
                 f'fill="{farbe}" fill-opacity="0.16" stroke="{farbe}" stroke-width="1.5"/>')
        self.text(x + b / 2, y + gr + 3, s, gr, farbe, 600, "middle")
        return b

    def speichern(self, name):
        kopf = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.b}" height="{self.h}" '
                f'viewBox="0 0 {self.b} {self.h}" font-family="{SCHRIFT}">', '<defs>',
                f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG1}"/>'
                f'<stop offset="1" stop-color="{BG2}"/></linearGradient>',
                '<filter id="schatten" x="-10%" y="-10%" width="130%" height="140%">'
                '<feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000" flood-opacity="0.35"/></filter>',
                '<linearGradient id="bildschirm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2b3d5c"/>'
                '<stop offset="1" stop-color="#101826"/></linearGradient>',
                '<linearGradient id="platine" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f8f4e"/>'
                '<stop offset="1" stop-color="#1f6b39"/></linearGradient>',
                '<linearGradient id="hat" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#25344a"/>'
                '<stop offset="1" stop-color="#18222f"/></linearGradient>',
                '<linearGradient id="metall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e3e7ec"/>'
                '<stop offset="1" stop-color="#9aa3ad"/></linearGradient>',
                '<linearGradient id="gehaeuse" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5b6675"/>'
                '<stop offset="1" stop-color="#3a434f"/></linearGradient>']
        for f in (GRUEN, ORANGE, GELB, BLAU, TEXT3, TOSLINK, "#ffffff", "#d64545", "#f2f2f2"):
            kopf.append(f'<marker id="spitze-{f[1:]}" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="4" '
                        f'markerHeight="4" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{f}"/></marker>')
        kopf.append('</defs>')
        kopf.append(f'<rect width="{self.b}" height="{self.h}" rx="22" fill="url(#bg)"/>')
        with open(os.path.join(HIER, name), "w", encoding="utf-8") as d:
            d.write("\n".join(kopf + self.teile + ["</svg>\n"]))


# ------------------------------------------------------------- Bauteile

def fernseher(s, x, y, b=200):
    h = b * 0.6
    s.add(f'<rect x="{x}" y="{y}" width="{b}" height="{h}" rx="8" fill="#0b0f16" stroke="#56627a" stroke-width="3"/>')
    s.add(f'<rect x="{x + 8}" y="{y + 8}" width="{b - 16}" height="{h - 16}" rx="4" fill="url(#bildschirm)"/>')
    # stilisierte Lautheitskurve auf dem Bildschirm
    pts = " ".join(f"{x + 16 + i * (b - 32) / 11:.0f},{y + h / 2 + (14 if i % 3 else -18) * (1 if i % 2 else -0.6):.0f}"
                   for i in range(12))
    s.add(f'<polyline points="{pts}" fill="none" stroke="{ORANGE}" stroke-width="3" stroke-opacity="0.8"/>')
    s.add(f'<rect x="{x + b / 2 - 8}" y="{y + h}" width="16" height="16" fill="#56627a"/>')
    s.add(f'<rect x="{x + b / 2 - 50}" y="{y + h + 14}" width="100" height="8" rx="4" fill="#56627a"/>')


def raspberry(s, x, y, b=230, mit_hat=True, beschriftung=True, cinch_rechts=False):
    """Raspberry Pi 4 von oben, darauf die HiFiBerry DAC+ DSP."""
    h = b * 0.66
    s.add(f'<rect x="{x}" y="{y}" width="{b}" height="{h}" rx="10" fill="url(#platine)" stroke="#16502b" stroke-width="2"/>')
    # Anschluesse an der rechten Kante: 2x USB, Ethernet
    for i, (hh, farbe) in enumerate(((0.22, "url(#metall)"), (0.22, "url(#metall)"), (0.26, "url(#metall)"))):
        yy = y + 8 + i * h * 0.31
        s.add(f'<rect x="{x + b - 30}" y="{yy:.0f}" width="40" height="{h * hh:.0f}" rx="3" fill="{farbe}" stroke="#6b7480"/>')
    # USB-C, Micro-HDMI an der unteren Kante
    for i, bb in enumerate((22, 18, 18)):
        s.add(f'<rect x="{x + 20 + i * 34}" y="{y + h - 8}" width="{bb}" height="14" rx="3" fill="url(#metall)" stroke="#6b7480"/>')
    # 40-Pin-Leiste
    for i in range(20):
        for j in range(2):
            s.add(f'<rect x="{x + 18 + i * 8.2:.1f}" y="{y + 8 + j * 8}" width="5" height="5" fill="#d9c27a"/>')
    if mit_hat:
        hx, hy, hb, hh = x + 10, y + 24, b - 52, h - 44
        s.add(f'<rect x="{hx}" y="{hy}" width="{hb}" height="{hh}" rx="8" fill="url(#hat)" stroke="#0e151f" '
              f'stroke-width="2" filter="url(#schatten)"/>')
        s.add(f'<rect x="{hx + hb * 0.38}" y="{hy + hh * 0.3}" width="{hb * 0.24}" height="{hb * 0.24}" rx="3" fill="#0b1018" stroke="#3b4a60"/>')
        # Cinch L/R (rot/weiss) und Toslink an der linken Kante
        s.add(f'<rect x="{hx - 14}" y="{hy + 14}" width="26" height="26" rx="3" fill="#111" stroke="#666"/>')
        s.add(f'<rect x="{hx - 8}" y="{hy + 21}" width="14" height="12" rx="2" fill="{ORANGE}" fill-opacity="0.85"/>')
        for k, farbe in enumerate(("#d64545", "#f2f2f2")):
            cy = hy + hh - 36 + k * 0
            cx = (hx + hb - 96 if cinch_rechts else hx + 30) + k * 34
            s.add(f'<circle cx="{cx}" cy="{hy + hh - 6}" r="13" fill="url(#metall)" stroke="#555"/>')
            s.add(f'<circle cx="{cx}" cy="{hy + hh - 6}" r="7" fill="{farbe}"/>')
        if beschriftung:
            s.text(hx + hb / 2 + 14, hy + 30, "HiFiBerry", 15, "#c9d1db", 600, "middle")
            s.text(hx + hb / 2 + 14, hy + 48, "DAC+ DSP", 13, TEXT3, 400, "middle")


def flirc(s, x, y, w=1.0):
    s.add(f'<rect x="{x}" y="{y}" width="{70 * w}" height="{26 * w}" rx="{8 * w}" fill="#2a2f36" stroke="#5c6672" stroke-width="2"/>')
    s.add(f'<rect x="{x - 22 * w}" y="{y + 5 * w}" width="{24 * w}" height="{16 * w}" rx="2" fill="url(#metall)" stroke="#6b7480"/>')
    s.add(f'<ellipse cx="{x + 70 * w}" cy="{y + 13 * w}" rx="{10 * w}" ry="{12 * w}" fill="#7a1d1d" stroke="#5c6672" stroke-width="2"/>')


def fernbedienung(s, x, y, w=1.0):
    s.add(f'<rect x="{x}" y="{y}" width="{46 * w}" height="{130 * w}" rx="{20 * w}" fill="#1a1e24" stroke="#5c6672" stroke-width="2"/>')
    s.add(f'<circle cx="{x + 23 * w}" cy="{y + 22 * w}" r="{6 * w}" fill="#d64545"/>')
    for i in range(3):
        for j in range(2):
            s.add(f'<circle cx="{x + 14 * w + j * 18 * w}" cy="{y + 50 * w + i * 18 * w}" r="{5 * w}" fill="#56627a"/>')
    s.add(f'<rect x="{x + 15 * w}" y="{y + 104 * w}" width="{16 * w}" height="{14 * w}" rx="3" fill="{BLAU}"/>')


def handy(s, x, y, w=1.0):
    s.add(f'<rect x="{x}" y="{y}" width="{70 * w}" height="{130 * w}" rx="{12 * w}" fill="#0b0f16" stroke="#5c6672" stroke-width="2.5"/>')
    s.add(f'<rect x="{x + 6 * w}" y="{y + 14 * w}" width="{58 * w}" height="{100 * w}" rx="4" fill="#1b2430"/>')
    pts = " ".join(f"{x + 10 * w + i * 5 * w:.0f},{y + 50 * w + (6 if i % 2 else -6) * w * (1 if i < 5 else 0.2):.0f}" for i in range(11))
    s.add(f'<polyline points="{pts}" fill="none" stroke="{GRUEN}" stroke-width="2.5"/>')
    for i in range(3):
        s.add(f'<rect x="{x + 12 * w}" y="{y + (72 + i * 12) * w}" width="{(46 - i * 10) * w}" height="{5 * w}" rx="2" fill="#3a4a5e"/>')


def mchstreamer(s, x, y, w=1.0, usb_rechts=False):
    """miniDSP MCHStreamer im gedruckten Gehaeuse (gehaeuse/mchstreamer).
    USB an einer Stirnseite, die beiden Toslink-Buchsen an der anderen."""
    s.add(f'<rect x="{x}" y="{y}" width="{150 * w}" height="{80 * w}" rx="{14 * w}" fill="url(#gehaeuse)" stroke="#262d36" stroke-width="2"/>')
    for i in range(5):
        s.add(f'<rect x="{x + 34 * w + i * 18 * w}" y="{y + 12 * w}" width="{9 * w}" height="{28 * w}" rx="3" fill="#2b323c"/>')
    ux, tx = (x + 138 * w, x - 8 * w) if usb_rechts else (x - 8 * w, x + 138 * w)
    s.add(f'<rect x="{ux}" y="{y + 26 * w}" width="{20 * w}" height="{26 * w}" rx="3" fill="url(#metall)" stroke="#6b7480"/>')
    for k in range(2):
        s.add(f'<rect x="{tx}" y="{y + 14 * w + k * 30 * w}" width="{20 * w}" height="{22 * w}" rx="3" fill="#111" stroke="#666"/>')
    s.add(f'<rect x="{tx + 5 * w}" y="{y + 20 * w}" width="{10 * w}" height="{10 * w}" fill="{ORANGE}" fill-opacity="0.85"/>')
    s.add(f'<circle cx="{x + 18 * w}" cy="{y + 16 * w}" r="{4 * w}" fill="{GRUEN}"/>')
    s.text(x + 75 * w, y + 64 * w, "miniDSP", 13 * w, "#dfe5ec", 600, "middle")


def lautsprecher(s, x, y, w=1.0):
    for k in range(2):
        xx = x + k * 120 * w
        s.add(f'<rect x="{xx}" y="{y}" width="{95 * w}" height="{165 * w}" rx="{10 * w}" fill="#1a1e24" stroke="#5c6672" stroke-width="2.5" filter="url(#schatten)"/>')
        s.add(f'<circle cx="{xx + 47.5 * w}" cy="{y + 38 * w}" r="{15 * w}" fill="#2b313a" stroke="#68727f" stroke-width="2"/>')
        s.add(f'<circle cx="{xx + 47.5 * w}" cy="{y + 108 * w}" r="{36 * w}" fill="#2b313a" stroke="#68727f" stroke-width="2"/>')
        s.add(f'<circle cx="{xx + 47.5 * w}" cy="{y + 108 * w}" r="{12 * w}" fill="#3c444f"/>')
        s.text(xx + 47.5 * w, y + 190 * w, "L" if k == 0 else "R", 18 * w, TEXT2, 700, "middle")


def netzteil(s, x, y):
    s.add(f'<rect x="{x}" y="{y}" width="64" height="70" rx="10" fill="#f2f4f7" stroke="#9aa3ad" stroke-width="2"/>')
    s.add(f'<rect x="{x + 18}" y="{y - 14}" width="6" height="16" fill="#9aa3ad"/><rect x="{x + 40}" y="{y - 14}" width="6" height="16" fill="#9aa3ad"/>')
    s.text(x + 32, y + 42, "5,1 V", 13, "#4a5361", 700, "middle")


def symbol_kette(s, x, y, farbe):
    """kleines Symbol fuer einen Verarbeitungsschritt (Wellenzug)."""
    s.add(f'<path d="M{x},{y} q6,-12 12,0 t12,0 t12,0" fill="none" stroke="{farbe}" stroke-width="3" stroke-linecap="round"/>')


# ------------------------------------------------------------- Prozessfluss

def prozessfluss():
    s = Svg(2000, 1180)
    s.text(60, 78, "So fließt der Ton", 44, TEXT, 700)
    s.text(60, 118, "Vom Fernseher bis zu den Lautsprechern – egal, was der Fernseher liefert: Am Ende kommt immer "
                    "ausgeglichenes Stereo heraus.", 21, TEXT3)

    # Fernseher
    s.karte(40, 420, 220, 300)
    fernseher(s, 50, 450, 200)
    s.text(150, 650, "Fernseher", 22, TEXT, 700, "middle")
    s.text(150, 680, "optischer Ausgang", 17, TEXT3, 400, "middle")

    yA, yB = 300, 800
    # Weg A
    s.text(300, yA - 92, "STANDARDWEG", 16, ORANGE, 700, stil='letter-spacing="2"')
    s.pfeil(f"M150,420 L150,{yA + 40} Q150,{yA} 190,{yA} L292,{yA}", ORANGE, 5)
    s.chip(300, yA - 66, "Tonausgang am TV: PCM", ORANGE, 15)
    s.chip(300, yA - 18, "PCM 2.0", ORANGE, 18, 130)
    s.text(300, yA + 46, "5.1 mischt der Fernseher\nselbst auf Stereo", 15, TEXT3)
    s.pfeil(f"M432,{yA} L512,{yA}", ORANGE, 5)
    s.karte(520, yA - 120, 300, 240)
    raspberry(s, 570, yA - 100, 180, True, True)
    s.text(670, yA + 70, "HiFiBerry DAC+ DSP", 20, TEXT, 700, "middle")
    s.text(670, yA + 96, "Toslink-Eingang → Pi", 16, TEXT3, 400, "middle")

    # Weg B
    s.text(300, yB - 196, "OPTIONAL · MIT MINIDSP MCHSTREAMER", 16, GELB, 700, stil='letter-spacing="2"')
    s.pfeil(f"M150,720 L150,{yB - 40} Q150,{yB} 190,{yB} L292,{yB}", GELB, 5)
    s.chip(300, yB - 170, "Tonausgang am TV: Auto / Bitstream", GELB, 15)
    for i, t in enumerate(("PCM 2.0", "Dolby Digital 2.0", "Dolby Digital 5.1", "DTS")):
        s.chip(300 + (i % 2) * 0, yB - 76 + i * 40, t, GELB, 16, 180)
    s.pfeil(f"M482,{yB} L512,{yB}", GELB, 5)
    s.karte(520, yB - 110, 300, 220)
    mchstreamer(s, 595, yB - 82, 1.0)
    s.text(670, yB + 40, "miniDSP MCHStreamer", 20, TEXT, 700, "middle")
    s.text(670, yB + 66, "per USB · Takt vom Fernseher", 16, TEXT3, 400, "middle")
    s.pfeil(f"M820,{yB} L872,{yB}", GELB, 5)
    s.karte(880, yB - 90, 230, 180)
    s.text(995, yB - 46, "Erkennen", 22, TEXT, 700, "middle")
    s.text(995, yB - 14, "PCM oder Dolby/DTS,\nlaufend, auch beim\nUmschalten mitten\nim Programm", 15, TEXT3, 400, "middle")
    # Verzweigung
    s.pfeil(f"M1110,{yB - 40} L1150,{yB - 40} L1150,{yB - 62} L1172,{yB - 62}", GELB, 4)
    s.pfeil(f"M1110,{yB + 40} L1150,{yB + 40} L1150,{yB + 62} L1172,{yB + 62}", GELB, 4)
    s.karte(1180, yB - 110, 170, 92, GELB, "#2a2a22", 12, False)
    s.text(1265, yB - 74, "PCM · 2.0", 18, TEXT, 700, "middle")
    s.text(1265, yB - 48, "unverändert", 15, TEXT3, 400, "middle")
    s.karte(1180, yB + 14, 170, 112, GELB, "#2a2a22", 12, False)
    s.text(1265, yB + 46, "5.1 → Stereo", 18, TEXT, 700, "middle")
    s.text(1265, yB + 70, "eigener Downmix", 15, TEXT3, 400, "middle")
    s.text(1265, yB + 92, "Kino · Neutral · Sprache", 13, GELB, 600, "middle")
    s.text(1265, yB + 112, "frei einstellbar", 13, TEXT3, 400, "middle")
    s.text(880, yB + 106, "Fehlt der MCHStreamer, schaltet die Box\nvon selbst auf den HiFiBerry-Eingang.", 14, TEXT3)

    # Zusammenfuehrung: Stereo 2.0
    mx, my = 1420, 520
    s.pfeil(f"M820,{yA} L{mx - 70},{yA} Q{mx - 40},{yA} {mx - 40},{yA + 30} L{mx - 40},{my - 50}", ORANGE, 5)
    s.pfeil(f"M1350,{yB - 64} L{mx - 40},{yB - 64} L{mx - 40},{my + 50}", GELB, 4)
    s.pfeil(f"M1350,{yB + 70} L{mx - 20},{yB + 70} L{mx - 20},{my + 50}", GELB, 4, spitze=False)
    s.add(f'<circle cx="{mx - 30}" cy="{my}" r="54" fill="#1a2a28" stroke="{GRUEN}" stroke-width="3" filter="url(#schatten)"/>')
    s.text(mx - 30, my - 6, "Stereo", 19, TEXT, 700, "middle")
    s.text(mx - 30, my + 18, "2.0", 19, GRUEN, 700, "middle")

    # Raspberry Pi: Verarbeitung
    px, py, pb, ph = 1480, 170, 480, 720
    s.karte(px, py, pb, ph, GRUEN, "#1d2a2c", 20, True, 2.5)
    s.pfeil(f"M{mx + 24},{my} L{px - 8},{my}", GRUEN, 5)
    s.text(px + 30, py + 50, "Raspberry Pi 4 · CamillaDSP", 23, TEXT, 700)
    s.text(px + 30, py + 80, "dieselbe Kette für beide Wege", 16, TEXT3)
    # Messung
    s.karte(px + 30, py + 104, pb - 60, 92, BLAU, "#1c2736", 12, False)
    s.text(px + 50, py + 140, "Lautheit messen (EBU R128)", 18, TEXT, 700)
    s.text(px + 50, py + 168, "Spracherkennung optional · regelt in Echtzeit", 15, TEXT3)
    schritte = [("Leveler", "Band −30 … −26 LUFS"), ("16-Band-EQ", "Klangkurve"),
                ("Loudness-Kompensation", "voller Klang auch leise"),
                ("Decke", "30 ms Vorlauf"), ("Höhendämpfung", "optional, gegen grelle Werbung"),
                ("Pegel", "Ihre Lautstärke"), ("Limiter", "−2 dBFS, Schutz")]
    sy = py + 232
    for i, (t1, t2) in enumerate(schritte):
        yy = sy + i * 66
        s.karte(px + 30, yy, pb - 60, 54, GRUEN, "#20302f", 12, False, 1.5)
        s.add(f'<circle cx="{px + 58}" cy="{yy + 27}" r="15" fill="{GRUEN}"/>')
        s.text(px + 58, yy + 33, str(i + 1), 16, "#10221d", 800, "middle")
        s.text(px + 86, yy + 25, t1, 18, TEXT, 700)
        s.text(px + 86, yy + 46, t2, 14, TEXT3)
        if i < len(schritte) - 1:
            s.pfeil(f"M{px + pb - 60},{yy + 54} L{px + pb - 60},{yy + 64}", GRUEN, 3, spitze=False)
    # Steuerpfeile von der Messung auf Leveler, Loudness, Hoehen
    for i in (0, 2, 4):
        yy = sy + i * 66 + 27
        s.pfeil(f"M{px + 16},{py + 150} L{px + 12},{py + 150} L{px + 12},{yy} L{px + 26},{yy}", BLAU, 2.5, "6 6")

    # Ausgang
    ay = 960
    s.pfeil(f"M{px + pb / 2},{py + ph} L{px + pb / 2},{ay - 8}", GRUEN, 5)
    s.karte(px - 20, ay, 260, 190)
    raspberry(s, px + 10, ay + 18, 170, True, False)
    s.text(px + 110, ay + 158, "HiFiBerry DAC", 19, TEXT, 700, "middle")
    s.text(px + 110, ay + 180, "Doppelton-Schutz aktiv", 14, TEXT3, 400, "middle")
    s.pfeil(f"M{px + 240},{ay + 95} L{px + 282},{ay + 95}", GRUEN, 5)
    lautsprecher(s, px + 292, ay + 14, 0.78)
    s.chip(1010, ay + 140, "Am Ende immer Stereo L/R über Cinch", GRUEN, 20)
    s.pfeil(f"M1440,{ay + 157} L{px - 28},{ay + 157}", GRUEN, 3, "2 8", spitze=False)

    # Bedienung: eine Steuerleitung unter den Eingangswegen zur Box
    s.text(60, ay + 2, "BEDIENUNG", 16, BLAU, 700, stil='letter-spacing="2"')
    s.karte(40, ay + 10, 430, 160)
    handy(s, 64, ay + 24, 1.0)
    s.text(160, ay + 62, "Handy · Browser", 20, TEXT, 700)
    s.text(160, ay + 90, "Band, EQ, Mix, Live-Diagramm,\nalle Geräte zeigen denselben\nStand", 15, TEXT3)
    s.karte(500, ay + 10, 470, 160)
    fernbedienung(s, 524, ay + 26, 0.98)
    flirc(s, 610, ay + 70, 1.0)
    s.text(700, ay + 62, "TV-Fernbedienung", 20, TEXT, 700)
    s.text(700, ay + 90, "über FLIRC-Empfänger (optional):\nLauter · Leiser · Stumm,\nangelernt im Browser", 15, TEXT3)
    bus = ay - 18
    s.pfeil(f"M255,{ay + 10} L255,{bus}", BLAU, 3, "8 8", spitze=False)
    s.pfeil(f"M735,{ay + 10} L735,{bus}", BLAU, 3, "8 8", spitze=False)
    s.pfeil(f"M255,{bus} L1440,{bus} L1440,{py + ph - 40} L{px - 8},{py + ph - 40}", BLAU, 3, "8 8")
    s.text(1430, bus - 10, "steuert", 14, BLAU, 600, "end")
    s.speichern("prozessfluss.svg")


# ------------------------------------------------------------- Aufbau

def aufbau():
    s = Svg(2000, 980)
    s.text(60, 78, "Aufbau", 44, TEXT, 700)
    s.text(60, 118, "Fernseher, Box, Lautsprecher – drei Kabel genügen. FLIRC und MCHStreamer sind optional.", 21, TEXT3)
    # Legende
    for i, (farbe, t, strich) in enumerate(((TOSLINK, "Toslink (optisch)", ""), ("#d64545", "Cinch L/R", ""),
                                            (TEXT3, "USB", ""), ("#ffffff", "Strom", ""), (BLAU, "WLAN / Netz", "8 8"),
                                            (GELB, "optionaler Weg", "10 8"))):
        x = 1100 + (i % 3) * 280
        y = 62 + (i // 3) * 40
        st = f' stroke-dasharray="{strich}"' if strich else ""
        s.add(f'<line x1="{x}" y1="{y}" x2="{x + 54}" y2="{y}" stroke="{farbe}" stroke-width="6" stroke-linecap="round"{st}/>')
        s.text(x + 68, y + 6, t, 17, TEXT2)

    # Fernseher
    s.karte(40, 200, 470, 420)
    fernseher(s, 95, 250, 360)
    s.text(275, 540, "Fernseher", 26, TEXT, 700, "middle")
    s.text(275, 572, "Tonausgang: PCM (mit MCHStreamer: Auto)", 17, TEXT3, 400, "middle")
    s.text(275, 598, "interne Lautsprecher aus", 17, TEXT3, 400, "middle")
    s.add('<rect x="497" y="380" width="18" height="22" rx="3" fill="#111" stroke="#888"/>')

    # Box: Pi + HiFiBerry
    bx, by = 760, 240
    s.karte(bx - 30, by - 40, 520, 470, GRUEN, "#1d2a2c", 20, True, 2.5)
    raspberry(s, bx + 40, by + 90, 380, True, True, cinch_rechts=True)
    s.text(bx, by + 6, "Die Box", 26, TEXT, 700)
    s.text(bx, by + 38, "Raspberry Pi 4 + HiFiBerry DAC+ DSP im Gehäuse", 17, TEXT3)

    # Toslink TV -> HiFiBerry (linke Kante der Karte: Toslink oben links)
    tx, ty = bx + 40 + 10 - 1, by + 90 + 24 + 27
    s.pfeil(f"M515,391 C620,391 640,{ty} {tx - 16},{ty}", TOSLINK, 7)
    s.text(560, 360, "Toslink", 18, TOSLINK, 700)

    # Netzteil
    netzteil(s, bx + 40, 840)
    s.pfeil(f"M{bx + 72},826 L{bx + 72},{by + 90 + 250}", "#ffffff", 5, spitze=False)
    s.text(bx + 120, 870, "USB-C-Netzteil", 17, TEXT2)
    s.text(bx + 120, 894, "5,1 V / 3 A", 15, TEXT3)

    # Cinch -> Lautsprecher (unter der Box entlang)
    lautsprecher(s, 1600, 300, 1.2)
    hx, hy, hb, hh = bx + 50, by + 90 + 24, 380 - 52, 380 * 0.66 - 44
    for k, (farbe, dy) in enumerate((("#d64545", 0), ("#f2f2f2", 18))):
        cx = hx + hb - 96 + k * 34
        zx = 1600 + 47 * 1.2 + k * 120 * 1.2
        s.pfeil(f"M{cx},{hy + hh + 8} L{cx},{700 + dy} L{zx},{700 + dy} L{zx},{300 + 165 * 1.2 + 14}", farbe, 6)
    s.text(1480, 690, "Cinch L/R", 18, "#d64545", 700)
    s.text(1600 + 130, 280, "Aktivboxen oder Verstärker", 18, TEXT2, 400, "middle")

    # USB-Seite: FLIRC und MCHStreamer (rechte Kante des Pi)
    ux = bx + 40 + 380 + 10
    flirc(s, ux + 40, by + 90 + 12, 1.1)
    s.text(ux + 64, by + 80, "FLIRC (optional)", 17, BLAU, 700)
    fernbedienung(s, ux + 210, by + 40, 0.8)
    s.add(f'<path d="M{ux + 205},{by + 90} q-30,10 -40,30" fill="none" stroke="{BLAU}" stroke-width="2" stroke-dasharray="4 5"/>')
    s.text(ux + 214, by + 168, "IR", 14, BLAU, 700)

    mchstreamer(s, 540, 760, 1.3, usb_rechts=True)
    s.text(637, 900, "miniDSP MCHStreamer (optional)", 18, GELB, 700, "middle")
    s.text(637, 926, "im gedruckten Gehäuse", 15, TEXT3, 400, "middle")
    uy = by + 90 + 380 * 0.66 * 0.62 + 18
    s.pfeil(f"M{540 + 206},{760 + 51} L{bx + 200},{760 + 51} L{bx + 200},{745} L{ux + 120},{745} L{ux + 120},{uy} L{ux + 16},{uy}", TEXT3, 5)
    s.text(bx + 212, 792, "USB", 16, TEXT3, 700)
    s.pfeil(f"M300,620 C300,700 420,786 518,786", GELB, 6, "10 8")
    s.text(60, 680, "statt zur HiFiBerry:\nToslink zum MCHStreamer\nfür 5.1 mit eigenem Downmix", 16, GELB)

    # Handy
    handy(s, 1420, 740, 1.15)
    s.text(1520, 790, "Handy", 20, TEXT, 700)
    s.text(1520, 816, "http://&lt;pi&gt;:8080".replace("&lt;", "<").replace("&gt;", ">"), 16, TEXT3)
    s.pfeil(f"M1410,800 C1340,800 1360,{by + 330} {bx + 500},{by + 330}", BLAU, 3, "8 8")
    s.speichern("aufbau.svg")


if __name__ == "__main__":
    prozessfluss()
    aufbau()
