"""
Parametrisches Gehäuse für den miniDSP MCHStreamer
---------------------------------------------------
Erzeugt: Unterteil, Deckel, drei Testteile (STL) und STEP-Dateien.
Aufruf:  python3 mchstreamer_gehaeuse.py
Alle Maße in mm. Koordinaten: x = Breite, y = Länge (USB bei -y), z = Höhe.
"""
import os
import cadquery as cq

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export")
os.makedirs(OUT, exist_ok=True)

# ---------------- Gemessene Platine ----------------
PCB_W = 40.0          # Platinenbreite
PCB_L = 60.5          # Platinenlänge
PCB_T = 1.6           # Platinendicke
PIN_BELOW = 2.0       # Lötpins unter der Platine
USB_OVER = 2.3        # USB-Buchse steht über Platinenkante
TOS_OVER = 2.3        # Toslink steht über Platinenkante
BRACKET_OVER = 0.5    # Winkel stehen über Platinenkante
USB_W = 12.0          # Breite USB-Buchse
USB_H = 13.0 - PCB_T  # Höhe USB über Platinenoberseite (15 inkl. Pins - 2 Pins - Platine)
TOS_SPAN = 40.5       # Toslink außen über beide Buchsen
TOS_GAP = 19.5        # Toslink Innenabstand
TOS_H = 11.5 - PCB_T  # Höhe Toslink über Platinenoberseite
SCREW_SPACING = 29.5  # Abstand Gewinde in den Winkeln
SCREW_Z = 4.3         # Gewindemitte über Platinenoberseite
LED_ABOVE_SCREW = 4.5 # LED-Mitte über Gewindemitte
LED_SIDE = +1         # +1 = rechts, -1 = links (Blick von außen auf die USB-Seite)

# ---------------- Design ----------------
CLR_SIDE = 0.5        # Spiel seitlich
CLR_END = -0.1        # v4: +1,8 mm länger als v3 (negativ = Toslink ragt in die Stirnwand)
LEN_CORR = -2.1       # Korrektur nach Testdrucken (v2: 3,2 mm Toslink-Front bis Außenfläche gemessen)
RAIL_W = 0.7          # Breite Auflagesteg unter der Platinenkante (Pins nah am Rand)
TOS_TONGUE_CLR = 0.3  # Luft zwischen Deckelzunge und Toslink-Oberseite
WALL_LONG = 4.0       # Wand lange Seiten (2 + 2 für Clip)
WALL_END = 2.5        # Stirnwände
FLOOR = 2.0           # Boden
LID_T = 2.0           # Deckelplatte
SUPPORT_H = 3.0       # Auflage unter der Platine (Pins 2 mm)
TOP_GAP = 1.5         # Luft über dem höchsten Bauteil
R_OUT = 5.0           # Außenradius Ecken
R_IN = 1.0            # Innenradius Ecken (max. 2,4 x Spiel)
SEAM_Z = 12.0         # Höhe der Trennfuge an den langen Seiten
JOINT_CLR = 0.15      # Spiel zwischen Lippe und Deckelrand
BEAD_R = 1.5          # Radius Wulst
BEAD_P = 0.5          # Überstand Wulst
GROOVE_EXTRA = 0.1    # Nut etwas größer als Wulst
SLIT = 1.0            # Schlitz zwischen Lippe und Ecke
PILOT_D = 2.0         # Vorbohrung Schrauben (selbst auf 3,2 aufbohren + senken)
LED_HOLE_D = 3.5      # LED-Loch
CUT_CLR = 0.5         # Spiel um Buchsen in den Ausschnitten
FEET_H = 0.0          # keine gedruckten Füße (Gummifüße kleben, ca. 3 mm hoch)
FEET_D = 8.0
SLOT_W = 2.0          # Lüftungsschlitzbreite

# ---------------- Abgeleitete Maße ----------------
TOS_W = (TOS_SPAN - TOS_GAP) / 2           # 10,5
TOS_X = TOS_GAP / 2 + TOS_W / 2            # 15
TOTAL_L = PCB_L + USB_OVER + TOS_OVER + LEN_CORR   # real gemessen am Druck: 63,2
USB_SETBACK = USB_OVER - BRACKET_OVER      # 1,8: so weit ragt USB in die Wand
Wi = max(PCB_W, TOS_SPAN) + 2 * CLR_SIDE   # 41,5
Li = TOTAL_L - USB_SETBACK + CLR_END       # 64,3
W = Wi + 2 * WALL_LONG
L = Li + 2 * WALL_END
Z_PCB_BOT = FLOOR + SUPPORT_H
Z_PCB_TOP = Z_PCB_BOT + PCB_T
Z_LID = Z_PCB_TOP + max(USB_H, TOS_H) + TOP_GAP   # Unterseite Deckelplatte
H = Z_LID + LID_T
Y_USB_IN = -Li / 2                          # Innenfläche USB-Wand
Z_SCREW = Z_PCB_TOP + SCREW_Z
X_SCREW = SCREW_SPACING / 2
X_HALF = W / 2 - WALL_LONG / 2              # Grenze innere/äußere Wandhälfte
Y_STRAIGHT = L / 2 - R_OUT                  # Ende des geraden Wandstücks
Z_BEAD = (SEAM_Z + Z_LID) / 2
X_BEAD = X_HALF + BEAD_R - BEAD_P           # Achse Wulst/Nut (rechte Seite)


# ---------------- Helfer ----------------
def box(x0, x1, y0, y1, z0, z1):
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))


def cyl_y(x, z, r, y0, y1):
    return cq.Solid.makeCylinder(r, y1 - y0, cq.Vector(x, y0, z), cq.Vector(0, 1, 0))


def cyl_x(y, z, r, x0, x1):
    return cq.Solid.makeCylinder(r, x1 - x0, cq.Vector(x0, y, z), cq.Vector(1, 0, 0))


def rbox(w, l, z0, z1, r):
    return (cq.Workplane("XY").box(w, l, z1 - z0, centered=(True, True, False))
            .edges("|Z").fillet(r).translate((0, 0, z0)))


def mirror_x(s):
    return s.mirror("YZ")


def slots(n, length, pitch, z0, z1, y_center=0.0):
    wp = cq.Workplane("XY").workplane(offset=z0)
    ys = [y_center + (i - (n - 1) / 2) * pitch for i in range(n)]
    return wp.pushPoints([(0, y) for y in ys]).slot2D(length, SLOT_W, 0).extrude(z1 - z0).val()


def chamfer_edge_y(x, z, c, y0, y1):
    """Prisma zum Anfasen einer Kante parallel zu y bei (x, z), Schenkel c."""
    s = c * 2 ** 0.5
    b = box(x - s / 2, x + s / 2, y0, y1, z - s / 2, z + s / 2)
    return b.rotate(cq.Vector(x, 0, z), cq.Vector(x, 1, z), 45)


# ---------------- Unterteil ----------------
base = rbox(W, L, 0, Z_LID, R_OUT).faces("<Z").edges().chamfer(0.8).val()
cavity = rbox(Wi, Li, FLOOR, H + 5, R_IN).val()
base = base.cut(cavity)

for sgn in (1, -1):
    def m(s):
        return s if sgn > 0 else mirror_x(s)
    # Platz für Deckelrand (äußere Wandhälfte oberhalb der Fuge)
    base = base.cut(m(box(X_HALF - JOINT_CLR, W / 2 + 1, -Y_STRAIGHT, Y_STRAIGHT, SEAM_Z, Z_LID + 1)))
    # Schlitze zwischen Lippe und Ecken
    for ys in (1, -1):
        y0, y1 = sorted((ys * (Y_STRAIGHT - SLIT), ys * Y_STRAIGHT))
        base = base.cut(m(box(Wi / 2 - 0.5, X_HALF, y0, y1, SEAM_Z, Z_LID + 1)))
    # Nut in der Lippe
    base = base.cut(m(cyl_y(X_BEAD, Z_BEAD, BEAD_R + GROOVE_EXTRA, -Y_STRAIGHT, Y_STRAIGHT)))
    # Einführfase oben an der Lippe
    base = base.cut(m(chamfer_edge_y(X_HALF - JOINT_CLR, Z_LID, 0.8, -Y_STRAIGHT, Y_STRAIGHT)))
    # Griffmulde unter der Fuge
    base = base.cut(m(cyl_x(0, SEAM_Z + 6.5, 8.0, W / 2 - 1.2, W / 2 + 1)))
    # Auflageleisten für die Platine
    base = base.fuse(m(box(PCB_W / 2 - RAIL_W, PCB_W / 2, -14, 14, FLOOR - 0.1, Z_PCB_BOT)))

# USB-Stirnwand: Schlitz nach oben offen (Deckel schließt ihn mit einer Zunge)
usb_x0, usb_x1 = -USB_W / 2 - 0.6, USB_W / 2 + 0.6
base = base.cut(box(usb_x0, usb_x1, -L / 2 - 1, Y_USB_IN + 0.1, Z_PCB_TOP - 0.3, Z_LID + 1))
for xs in (1, -1):
    base = base.cut(cyl_y(xs * X_SCREW, Z_SCREW, PILOT_D / 2, -L / 2 - 1, Y_USB_IN + 0.1))
base = base.cut(cyl_y(LED_SIDE * X_SCREW, Z_SCREW + LED_ABOVE_SCREW, LED_HOLE_D / 2,
                      -L / 2 - 1, Y_USB_IN + 0.1))
# Hinweis: Blick von außen auf die USB-Wand (von -y) -> +x ist rechts.

# Toslink-Stirnwand: zwei Schlitze, nach oben offen (Platine von oben einlegen)
TOS_CUTS = []
for xs in (1, -1):
    x0, x1 = xs * TOS_X - TOS_W / 2 - CUT_CLR, xs * TOS_X + TOS_W / 2 + CUT_CLR
    x1 = min(x1, Wi / 2) if xs > 0 else x1
    x0 = max(x0, -Wi / 2) if xs < 0 else x0
    TOS_CUTS.append((x0, x1))
    base = base.cut(box(x0, x1, Li / 2 - 0.1, L / 2 + 1,
                        Z_PCB_TOP - CUT_CLR, Z_LID + 1))

# Bodenschlitze
base = base.cut(slots(6, 24, 4, -1, FLOOR + 1))

# Füße: entfallen, Gummifüße werden aufgeklebt (FEET_H = 0)
if FEET_H > 0:
    foot = (cq.Workplane("XY").circle(FEET_D / 2).extrude(FEET_H + 0.2)
            .faces("<Z").chamfer(0.5).val())
    fx, fy = W / 2 - 6.5, L / 2 - 6.5
    for x in (fx, -fx):
        for y in (fy, -fy):
            base = base.fuse(foot.translate(cq.Vector(x, y, -FEET_H)))

base = base.clean()

# ---------------- Deckel ----------------
lid = (cq.Workplane("XY").box(W, L, LID_T, centered=(True, True, False))
       .edges("|Z").fillet(R_OUT).faces(">Z").edges().fillet(1.2)
       .translate((0, 0, Z_LID)).val())
for sgn in (1, -1):
    def m(s):
        return s if sgn > 0 else mirror_x(s)
    ys = Y_STRAIGHT - 0.2
    skirt = box(X_HALF, W / 2, -ys, ys, SEAM_Z + 0.1, Z_LID + 0.01)
    bead = cyl_y(X_BEAD, Z_BEAD, BEAD_R, -ys + 1, ys - 1).intersect(
        box(X_HALF - BEAD_P - 0.01, X_HALF + 0.01, -ys, ys, 0, H))
    skirt = skirt.fuse(bead)
    skirt = skirt.cut(chamfer_edge_y(X_HALF, SEAM_Z + 0.1, 0.6, -ys - 1, ys + 1))
    lid = lid.fuse(m(skirt))
# Zunge, die den USB-Schlitz oben schließt
tongue_bot = Z_PCB_TOP + USB_H + 0.4
lid = lid.fuse(box(usb_x0 + 0.15, usb_x1 - 0.15, -L / 2 + 0.3, Y_USB_IN - 0.1, tongue_bot, Z_LID + 0.01))
# Zungen, die die Toslink-Schlitze oben schließen
tos_tongue_bot = Z_PCB_TOP + TOS_H + TOS_TONGUE_CLR
for x0, x1 in TOS_CUTS:
    lid = lid.fuse(box(x0 + 0.15, x1 - 0.15, Li / 2 + 0.1, L / 2 - 0.3, tos_tongue_bot, Z_LID + 0.01))
# Lüftungsschlitze im Deckel
lid = lid.cut(slots(7, 28, 4, Z_LID - 1, H + 1))
lid = lid.clean()


# ---------------- Platinen-Dummy zur Prüfung ----------------
def pcb_dummy():
    y_pcb0 = Y_USB_IN + BRACKET_OVER
    y_pcb1 = Y_USB_IN + TOTAL_L - USB_SETBACK - TOS_OVER   # reale Länge inkl. Korrektur
    parts = [box(-PCB_W / 2, PCB_W / 2, y_pcb0, y_pcb1, Z_PCB_BOT, Z_PCB_TOP)]
    parts.append(box(-USB_W / 2, USB_W / 2, Y_USB_IN - USB_SETBACK, Y_USB_IN + 14,
                     Z_PCB_TOP, Z_PCB_TOP + USB_H))
    for xs in (1, -1):
        parts.append(box(xs * X_SCREW - 3.6, xs * X_SCREW + 3.6, Y_USB_IN, Y_USB_IN + 7,
                         Z_PCB_TOP, Z_PCB_TOP + USB_H - 0.4))
        y_front = Y_USB_IN + TOTAL_L - USB_SETBACK
        parts.append(box(xs * TOS_X - TOS_W / 2, xs * TOS_X + TOS_W / 2, y_front - 14, y_front,
                         Z_PCB_TOP, Z_PCB_TOP + TOS_H))
    d = parts[0]
    for p in parts[1:]:
        d = d.fuse(p)
    return d.clean()


if __name__ == "__main__":
    print(f"Außen: {W:.1f} x {L:.1f} x {H:.1f} mm")
    print(f"Innen: {Wi:.1f} x {Li:.1f} mm, Fuge bei z={SEAM_Z}")

    def export(shape, name):
        cq.exporters.export(cq.Workplane().add(shape), os.path.join(OUT, name),
                            tolerance=0.01, angularTolerance=0.1)

    # STEP in Einbaulage
    export(base, "MCHStreamer_Unterteil.step")
    export(lid, "MCHStreamer_Deckel.step")
    export(cq.Compound.makeCompound([base, lid]), "MCHStreamer_Gehaeuse_komplett.step")

    # STL in Drucklage
    export(base.translate(cq.Vector(0, 0, FEET_H)), "MCHStreamer_Unterteil.stl")
    lid_print = lid.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), 180).translate(cq.Vector(0, 0, H))
    export(lid_print, "MCHStreamer_Deckel.stl")

    # Testteile
    clip_b = base.intersect(box(-W, W, -5, 5, -10, H + 5))
    clip_l = lid_print.intersect(box(-W, W, -5, 5, -10, H + 5))
    export(clip_b.translate(cq.Vector(0, 0, -clip_b.BoundingBox().zmin)), "Test_Clip_Unterteil.stl")
    export(clip_l, "Test_Clip_Deckel.stl")
    usb_test = base.intersect(box(-W, W, -L / 2 - 1, -L / 2 + 6, -10, H + 5))
    usb_test = usb_test.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), 90)   # Außenseite aufs Druckbett
    bb = usb_test.BoundingBox()
    export(usb_test.translate(cq.Vector(0, 0, -bb.zmin)), "Test_USB_Stirnwand.stl")
    tos_test = base.intersect(box(-W, W, L / 2 - 6, L / 2 + 1, -10, H + 5))
    tos_test = tos_test.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), -90)  # Außenseite aufs Druckbett
    bb = tos_test.BoundingBox()
    export(tos_test.translate(cq.Vector(0, 0, -bb.zmin)), "Test_Toslink_Stirnwand.stl")

    # Passungsprüfung
    d = pcb_dummy()
    for nm, s in (("Unterteil", base), ("Deckel", lid)):
        v = d.intersect(s).Volume()
        print(f"Kollision Platine/{nm}: {v:.3f} mm³")
    print(f"Kollision Unterteil/Deckel: {base.intersect(lid).Volume():.3f} mm³")
    print("Volumen Unterteil", round(base.Volume()), "Deckel", round(lid.Volume()))
