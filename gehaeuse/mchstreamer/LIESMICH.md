# Gehäuse für den miniDSP MCHStreamer (3D-Druck)

Der MCHStreamer wird als nackte Platine geliefert. Dieses Gehäuse schützt sie, lässt USB-Buchse,
beide Toslink-Buchsen und die LED frei und ist an den langen Seiten belüftet.

| Datei | Inhalt |
|---|---|
| `MCHStreamer_Unterteil.stl` | Unterteil, druckfertig |
| `MCHStreamer_Deckel.stl` | Deckel, druckfertig |
| `MCHStreamer_*.step` | dieselben Teile und der Zusammenbau als STEP zum Weiterbearbeiten im CAD |
| `mchstreamer_gehaeuse.py` | das parametrische Modell (CadQuery), aus dem alle Dateien entstehen |

**Maße:** außen etwa 50 × 69 × 22 mm.

**Zusammenbau:**

- Die Platine liegt auf einem schmalen Steg, die Lötpins darunter haben Luft.
- Die beiden Vorbohrungen in der Toslink-Stirnwand (2 mm) auf 3,2 mm aufbohren und senken. Damit
  wird die Platine an den Gewinden ihrer Winkel festgeschraubt.
- Der Deckel rastet mit einem Wulst an den langen Seiten ein.
- Kleine Gummifüße (etwa 3 mm hoch) unterkleben.

**Anpassen:** Alle Maße stehen oben in `mchstreamer_gehaeuse.py` (Platine gemessen, Spiel,
Wandstärken). `python3 mchstreamer_gehaeuse.py` erzeugt im Unterordner `export/` die Teile neu,
dazu drei kleine Testteile (Stirnwände), mit denen sich die Passung ohne den ganzen Druck prüfen
lässt. Dafür wird [CadQuery](https://github.com/CadQuery/cadquery) gebraucht.
