# Band-Zweig — Konzept, Messungen, offene Punkte (Stand 2.3.0)

Dritter Normalisierungszweig neben RMS und EBU R128. Testzweig: RMS und R128
bleiben unveraendert; der Band-Zweig hat eigene Einstellungen, einen eigenen
Reiter und eine eigene DSP-Pipeline, die nur aktiv ist, solange `quelle=band`.

## Anforderung (Nutzer)

- Nur ein **Lautheitsband** vorgeben (z. B. −26…−22 LUFS). Zu laut → bis zur
  Oberkante herunter. Zu leise → bis zur Unterkante hoch. Dazwischen nichts.
- **Keine weiteren Regler.**
- **Kein Pumpen** bei leisen Stellen.
- Der bekannte R128-Fehlerfall darf nicht auftreten: leise Szene hochgeregelt,
  danach laute Szene deutlich zu laut, weil die Regelung nicht hinterherkommt.
- EQ beruecksichtigen; spaeter eine automatische psychoakustische
  Frequenzkorrektur bei starker Absenkung (dumpfer Klang). Trotz Kurve muss die
  Ziellautheit getroffen werden.

## Befund am R128-Zweig (Grund fuer den neuen Zweig)

Offline mit den echten Konstanten aus `app.py` nachgerechnet (Sprung von Ziel
auf einen konstant lauteren Sender, Abend-Modus):

| nach | +6 dB lauter | +10 dB lauter |
|---|---|---|
| 0,4 s | noch +3,6 dB | noch +5,4 dB |
| 1,0 s | noch +1,1 dB | noch +1,6 dB |
| unter 0,5 dB Rest | 1,9 s | 2,1 s |

Ursachen (strukturell, kein Tuning):

1. Steuergroesse `S + 0,75·(M−S)`: 25 % der Korrektur kriechen mit dem
   3-s-Fenster nach.
2. Dreifache Glaettung nach unten: M 400 ms + 5-Frame-Gewichtung (~100 ms) +
   Huelle τ 0,22 s.
3. Der 35-ms-Schutz greift nur oberhalb von Ziel + Dynamik (Abend: −9). Ein um
   5 dB zu lauter Sender hat keinen schnellen Pfad.
4. Fall „leise → laut“: Gain steht bei +12 dB; die laute Szene kommt mit −4
   heraus; der Schutz laesst sie auf −9 stehen (7 dB zu laut); die 5-Frame-
   Historie zeigt noch „anheben“ und verzoegert das Absenken zusaetzlich.

## Architektur des Band-Zweigs

```
Eingang → Vorpegel → Aux1 (Leveler) → 16-Band-EQ → Sidechain (Mid, HP 120 Hz)
       → [30 ms Vorlauf auf den Nutzkanaelen] → Decke (Kompressor) → Aux2 → Limiter
```

**A. Decke im DSP, nicht in Python.** Der CamillaDSP-Kompressor sitzt HINTER
Aux1; sein Eingang enthaelt den Leveler-Gain, seine Schwelle ist damit eine
feste Ausgangsgrenze. Schwelle = Oberkante + 1 dB, umgerechnet ueber die
automatische LUFS↔dBFS-Kalibrierung und den Vorpegel:
`thr_dBFS = oben + 1 − kalibrierung + vorpegel`. Attack 2 ms, Release 300 ms,
Ratio 20:1, Makeup = −Vorpegel.

**Lookahead** (schaltbar): Sidechain unverzoegert, Nutzkanaele 30 ms
verzoegert (`Delay`-Filter). Offline gegen CamillaDSP 4.1.3 im Datei-Modus
gemessen (rosa Rauschen, Sprung −53 → −23 dBFS, Aux1 +12 dB, Decke −20):

| Variante | ms ueber Decke+1 dB | Maximum ueber Decke | 1-ms-Burst |
|---|---|---|---|
| ohne Decke (heutiger Leveler) | dauerhaft | +11 dB bis Leveler greift | −2 dBFS (Limiter) |
| Decke, kein Vorlauf, Attack 5 ms | 43 | +11,4 dB | −2,0 |
| Decke, 25 ms Vorlauf, Attack 5 ms | 28 | +6,4 dB | −17,4 |
| **Decke, 30 ms Vorlauf, Attack 2 ms** | **7** | **+3,8 dB** | **−18,4** |
| Decke, 40 ms Vorlauf, Attack 2 ms | 5 | +3,7 dB | −17,1 |

30 ms / 2 ms ist der gewaehlte Punkt; 40 ms bringt nichts mehr. Latenz steigt
von 63 auf ~93 ms. ITU-R BT.1359: Ton-nach-Bild bis ~125 ms unauffaellig —
der Hoertest am Geraet entscheidet, deshalb der Schalter.

**B. Anheben nach gegateter Programmlautheit.** Gleitendes 8-s-Fenster aus den
10-Hz-M-Frames, absolutes Gate −70, relatives Gate 10 LU unter dem ungegateten
Mittel (EBU-R128-Logik). Dialogpausen und Atmosphaeren ziehen den Wert nicht
nach unten → kein Hochpumpen in Pausen. Verifiziert: Dialog −24 mit 40 % Pausen
bei −60 → Programm −24,0 (ungegatet −26,2). Anheben nur, wenn Programm UND
Short-Term unter der Unterkante liegen; τ 1 s und max. 2 dB/s; Deckel +15 dB;
nie so weit, dass M ueber die Oberkante rutscht.

**C. Absenken entlastet nur die Decke.** M + Gain > Oberkante → Soll =
(Oberkante − 0,5) − M, τ 0,2 s. Kein 5-Frame-Filter, kein S-Anteil. Der
Leveler muss nicht schnell sein, weil er nicht mehr die Deckelung ist.

**D. Im Band: Gain halten.** Zielpunkte liegen 0,5 dB innerhalb der Kanten
(kein Flackern an der Grenze).

**Fallbacks:** ohne frische R128-Frames haelt der Leveler den Gain
(„band-halten“); die Decke im DSP arbeitet unabhaengig davon weiter. Nach einer
Tonluecke ≥ 0,4 s wird die Programm-Historie verworfen (neuer Sender).

Regelgesetz-Simulation (ideales M, Band −26…−22): leise Szene −34 LUFS wird in
~5 s auf −26 gebracht und dort gehalten; Sprung auf −14 LUFS: Leveler nach
0,6 s bei −21,8, nach 1,0 s im Band — die DSP-Decke haelt in der Zwischenzeit
bei −21.

## Trennung von RMS/R128 (was im bestehenden Code angefasst wurde)

- `apply_preset()`: Verteiler am Anfang (`quelle == band` → eigener Aufbau) und
  `aktive_konfiguration()` → `aktive_grundkonfiguration()` (identisch, solange
  keine Band-Pipeline aktiv ist; sonst holt sie die Grundpipeline zurueck).
  Gleiches im Bypass-Block.
- Regelschleife: eigener `if quelle == band`-Ast VOR dem R128-Ast (`if` →
  `elif`); der R128/RMS-Code selbst ist unveraendert. Der Max-Anhebungs-Deckel
  von R128/RMS ist im Band-Zweig uebersprungen (eigener Deckel).
- `/api/schalter` akzeptiert `quelle=band`. `read_kompressor()` liefert im
  Band-Zweig die Decke.
- Getestet mit Flask-Testclient und Fake-CamillaDSP: band → r128 → rms stellt
  exakt die tv.yml-Pipeline und die RMS-Kompressorwerte wieder her.

Keine Aenderung an `tv.yml`, `install.sh` oder den Diensten: Die Band-Pipeline
wird zur Laufzeit aus der aktiven Konfiguration gebaut, mit `validate()`
geprueft und per `set_active` gesetzt. Ausrollen ueber `werkzeuge/deploy.sh`.

## Vorbereitet, noch nicht gebaut: psychoakustische Kompensation

Problem: Starke Absenkung klingt dumpf (Equal-Loudness-Kurven, ISO 226: bei
weniger Pegel fallen Bass und Praesenz subjektiv staerker ab).

Vorgesehener Weg, der OHNE Konfigurationstausch pro Regelschritt auskommt:
- Paralleler Pfad in der Pipeline: Kopie der Nutzkanaele → Formfilter
  (Loudness-Differenzkurve, z. B. Low-Shelf + leichte Praesenz) → **Aux3-Fader**
  → zurueckgemischt. Aux3 ist frei (Main = Nutzerpegel, Aux1 = Leveler, Aux2 =
  neutraler Ausgangspegel). Python setzt Aux3 im 20-ms-Takt als Funktion der
  aktuellen Gesamtabsenkung — ein Fader-Schreibvorgang wie beim Leveler, mit
  Rampe, ohne Pipeline-Neuaufbau.
- Lautheitsneutral by construction: die Formkurve wird so normiert, dass ihre
  K-gewichtete Lautheitsaenderung fuer typisches Programm ≈ 0 ist. Die Decke
  sitzt hinter dem EQ und haelt die Oberkante unabhaengig davon.
- Messung: Der ffmpeg-Messpfad spiegelt den statischen EQ bereits; die
  dynamische Kurve wird ebenfalls gespiegelt oder analytisch verrechnet.
- Erst bauen, wenn der Band-Zweig selbst im Hoertest bestaetigt ist.

## Offen / als Naechstes

1. Hoertest am Geraet: Band verwenden, Vorlauf an/aus vergleichen (Lippensync).
2. Messlatenz der ffmpeg-M-Frames am laufenden System bestimmen
   (Kreuzkorrelation gegen `capture_rms`); begrenzt τ nach unten.
3. Regelgesetz offline gegen echte TV-Mitschnitte (12-Sender-Zapping) fahren:
   Zeit bis ins Band je Senderwechsel, Anteil im Band, Gain-Richtungswechsel/min.
   Mitschnitte dafuer neu aufnehmen (Records-Reiter).
4. Modi als Bandbreiten (Tag breit, Nacht schmal) — erst nach dem Hoertest.

## v4 (1.10.1): Hoertest und Nachschaerfung

Testmaterial: ein Orchesterstueck ohne Dialog (I −15,5, LRA 16,1) und ein
Film-Trailer mit Dialog und Musik (I −22,8, LRA 4,8), Kette offline: Python-Leveler (exakt die app.py-Regeln,
100 ms angenommene Messlatenz) -> CamillaDSP 4.1.3 Decke mit 30 ms Vorlauf.

Pumpmass: "Atmer" = Gain-Umkehr um > 2 dB innerhalb von 3 s, pro Minute;
dazu der Gainweg in dB/min. Beides aus dem Gain-Verlauf berechenbar, BEVOR
etwas am Geraet laeuft.

| Variante (Orchester, −26…−22) | Atmer/min | Gainweg | im Band | > 6 dB drunter |
|---|---|---|---|---|
| v1 = 1.10.0 (Programm-Gate 8 s, 2 dB/s, halten) | 2,8 | 48 | 38 % | 25 % |
| v2 (+ Short-Term-Gate 2 s) | 3,2 | 57 | 44 % | 22 % |
| v3 (M-Gate 1,5 s, 4 dB/s, Einpendeln zur Mitte τ 2 s) | **6,9** | 97 | 54 % | 13 % |
| v4a (wie v3, aber halten statt Mitte) | 5,2 | 78 | 50 % | 15 % |
| **v4 (M-Gate 2,5 s, 3 dB/s, halten)** | **3,0** | 59 | 46 % | 19 % |

Der Nutzer hoerte v3 als "pumpt bereits zu stark" - deckt sich mit der Metrik.
Zwei Verursacher: das Einpendeln (staendige Bewegung im Band) und 4 dB/s.

Entscheidungen des Nutzers, jetzt Konzept:
- Im Band nichts regeln; das Band bestimmt die Dynamik.
- Zielpunkte 10 % der Bandbreite innerhalb der Kanten.
- Deckel +17 dB; unterhalb der Rauschsperre (Regler, Standard −40 LUFS
  Eingang) nie anheben - nur Gespraeche sollen hochkommen.
- Standardband −30…−26. Keine Modi im Band-Zweig; spaeter Presets
  geringe/mittlere/hohe Dynamik mit eigenen Bandwerten.

Bekannte Grenze: Stellen kuerzer als 2,5 s werden nicht angehoben (Film-
Trailer: 46 % der Frames unter dem Band, Gain nie > 0, weil die leisen
Dialogzeilen zwischen den Musik-Hits 1-2 s dauern). Das ist der Preis des
Pumpschutzes. Waere "Fluestern zwischen zwei Effekten" gewuenscht, braeuchte es
Aufwaertskompression - anderes Werkzeug, eigenes Pumprisiko. Nach dem Filmtest
entscheiden.

Bewertung (ehrlich): Oberkante garantiert (0,0-0,2 % ueber Kante), Regelgesetz
verstehbar, Pumpen messbar. Skeptisch: die Decke ist ein 20:1-Kompressor mit
2 ms Attack (LRA 16 -> 5-7 LU bei Orchester); falls sie im Film "gedeckelt"
klingt, sind Attack 5 ms / Ratio 8:1 die Stellschrauben. Unbewiesen: alles am
Ohr (Lippensync, Decke, 2,5-s-Gate) und die reale Messlatenz.

## Loudness-Kompensation (1.11.0) - gebaut

Wie oben vorbereitet, jetzt umgesetzt: Aux3-Parallelpfad, out = x + a*(H(x) - x).
Konstanten: BAND_LD_VOLL 20 dB, BAND_LD_TAU 2 s, Kurve LS +6 @120 / HS +2 @6k /
Trim -1,4 dB (lautheitsneutral auf rosa Rauschen, auch in der kompletten
App-Pipeline verifiziert). a = Staerke * clamp(-Leveler-Gain/20, 0, 1). Die
Decke-Reduktion selbst geht NICHT in a ein (nur kurze Transienten, denen eine
2-s-Glaettung ohnehin nicht folgen wuerde). Bezug ist die Absenkung, nicht der
Absolutpegel - den kennt niemand (Verstaerker).

## Spracherkennung - gemessen, offen

| Detektor | Trailer (Dialog+Musik) | Orchester (kein Dialog) | CPU |
|---|---|---|---|
| WebRTC-VAD Modus 3 | 93 % "Sprache" | 87 % "Sprache" | ~0 | 
| Silero-VAD v5 (ONNX) | 53 % Sekunden Sprache | 0 % | 5,4 % eines Kerns |

WebRTC ist unbrauchbar. Silero waere der Weg zu "anheben nur bei Sprache" und
zu einer sprach-gegateten Programmlautheit (Dialog-Anker).
Es fasst den Ton nicht an; es liefert nur ein Flag. Nicht eingebaut - der
Nutzer hatte KI wegen Rechenlast ausgeschlossen; die Messung widerspricht der
Rechenlast, die Entscheidung bleibt seine.

## Spracherkennung (1.13.0) - gebaut

Nutzerentscheidung nach den Messzahlen: Silero optional im Band-Reiter.
Sprache -> Gate 1,0 s, Deckel +20 dB, Rauschsperre ignoriert; ohne Sprache
Gate 2,5 s, Deckel +15 dB, Rauschsperre. Eigener ffmpeg-Prozess (16 kHz Mono
aus tvmess), onnxruntime, ~5 % eines Kerns, nur bei quelle=band. Fehlerfall
(kein Geraet, kein Modell, kein onnxruntime) getestet: Meldung im Status,
Verhalten wie "aus", Thread endet beim Verlassen des Band-Zweigs.

## 2.3.0 - Leveler nach Recherche (Juenger/Dolby) und R128-Rueckblick

Recherche: Juenger Level Magic = Transientenprozessor (schnell, begrenzt) +
traeger Leveler um ein Ziel (10 s ... Stunden) + Limiter; Freeze Level friert
den Gain unter einem Pegel ein. Dolby Volume: Ereignis- statt Dauerregelung.
Broadcast-AGC: Attack schnell, Release langsam, Gate friert.

Unser Leveler nach unten war viel zu schnell (tau 0,2 s auf Momentary) und
bounced an den Kanten. 1.9.28 (R128) hob nach oben ohne Gate mit tau 0,55 s
(95 % in ~1,7 s) auf Basis Short-Term + 35 % Momentary an - der Nutzer fand
das gut; gefaehrlich war nur die Folge "leise hoch, dann laut", die heute die
Decke faengt.

Neu (Variante C+D):
- Absenken: Bezug Short-Term (3 s), tau 0,2 s. Spitzen macht die Decke.
- Anheben: Bezug 2-s-Energiemittel der M-Frames, KEIN Szenen-Gate, tau 0,5 s
  und max. 6 dB/s (95 % in ~1,5 s), Deckel 15/20 dB, Rauschsperre bleibt,
  nie ueber die Oberkante (Short-Term-Sperre). Pausen-Atmen innerhalb des
  Bandes ist bewusst erlaubt.
- Sidechain-Hochpass der Decke 120 -> 50 Hz: Bass wird wie in der K-Bewertung
  begrenzt (vorher bis 7 dB Ueberschwingen bei bassreichen Effekten). Decke
  selbst (Mitte, 20:1, 2 ms) auf Wunsch unveraendert.
- Gemessen (Pink, Schwelle -30, Ratio 20): Mitten-Sidechain haelt unkorreliertes
  Stereo ~0,6 dB ueber der Schwelle, korreliertes (Dialog) 2,4 dB darunter;
  L/R-Monitor summiert kohaerent (+6 dB) - deshalb nicht umgestellt.

Offline (tv2, Band -25..-20, Sperre -35): v4 44 % im Band / 55 % darunter,
Gainweg 19 dB/min; Variante C 52 % / 46 %, 12,5 dB/min, 0,1 Atmer/min. Die
Aufwaerts-Variante D wurde im Fake-Harness geprueft (leise -30 -> in 2 s im
Band, laut -12 -> in 1 s im Band, unter der Sperre halten) - der Filmtest
entscheidet.

## 2.5.0 - R128 als Option zurueck

Auf Wunsch ist die 1.13.5-R128-Regelung wieder waehlbar (Klang-Reiter). Der
Code ist ein 1:1-Port des alten R128-Asts; ein Aequivalenzlauf
(werkzeuge/equiv.py: beide Schleifen gegen denselben Fake-CamillaDSP mit
Fake-Uhr und geskripteten M/S-Frames) ergab ueber 46 s bit-identische Aux1-
und Schutz-Traces. Unterschiede: kein RMS-Fallback (halten), Decke neutral,
kein Vorlauf, Loudness-Kompensation auch bei R128 nutzbar. Zweck: A/B-Hoertest
Band gegen R128 unter identischen Bedingungen (gleicher EQ, gleiche
Loudness-Kurve, gleicher Pegel).

## 2.7.0 - Decke griff live nie (Main-Lautstaerke vor der Pipeline)

Live-Daten (30 min): gruen 55 % ueber der Oberkante, (gruen-rot-Gain) nie
negativ. Offline klemmte dieselbe Config. CamillaDSP wendet Main VOR der
Pipeline an -> Sidechain 25 dB zu leise. Pegel jetzt Aux2-Filter "ausgang"
hinter der Decke; Main 0 dB, nur Startschutz. Zweiter Befund: der Kompressor
glaettet 20*log10|x| im dB-Bereich (kein RMS) und liest auf TV-Material
~3,6 dB heisser als der 400-ms-RMS -> BAND_DECKE_DETEKTOR_DB +3,6. Offline
(echte Aufnahme, Aux1 +9,7, Pegel -25,5 auf Aux2): Band unberuehrt,
Oberkante -1,3 dB, Ausgang p95 -21,1 / max -18,5 (ungeklemmt -15 / -11).
