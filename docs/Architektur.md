# Architektur – TV_DSP_Normalizer

Wozu: Wer etwas ändern oder austauschen will, findet hier, wo er ansetzen muss und
warum es so gebaut ist. Bei jeder Struktur- oder Technikentscheidung aktualisieren.

## Zweck

Eine kleine Box zwischen Fernseher und Aktivlautsprechern (oder Verstärker), die den
Dynamikumfang beim Fernsehen bändigt: Schüsse, Explosionen, Musik und Werbung werden
leiser, Flüsterszenen lauter. Bedienung über den Browser, Lautstärke zusätzlich über die
vorhandene Fernbedienung.

**Hardware:** Raspberry Pi 4 + HiFiBerry DAC+ DSP. Optional: FLIRC-USB-Empfänger
(Fernbedienung), miniDSP MCHStreamer I2S TosLink (USB-Eingang für Dolby Digital 5.1 mit
frei einstellbarem Downmix).
**System:** Raspberry Pi OS 12 „bookworm“, 64 Bit (auch mit Desktop; PipeWire wird von den
Audiogeräten ferngehalten).

## Signalweg

```
Standard:   Fernseher (PCM) → Toslink → HiFiBerry-Eingang → CamillaDSP → DSP/DAC → Cinch
USB-Weg:    Fernseher (Bitstream) → Toslink → miniDSP → tvdsp-quelle (Dolby decodieren,
            5.1 → Stereo mischen) → ALSA-Loopback → CamillaDSP → DSP/DAC → Cinch
```

Die Verarbeitung in CamillaDSP (Band-Regelung):

```
Eingang → Vorpegel → Aux1 (Leveler) → 16-Band-EQ
        → Loudness-Kompensation (Parallelpfad, Aux3)
        → Decke (Kompressor, 30 ms Vorlauf, Schwelle = Oberkante + 1 dB)
        → Höhendämpfung (Parallelpfad, Aux4, nur wenn eingeschaltet)
        → Pegel (Aux2, Filter „ausgang“) → Limiter −2 dBFS (hart) → Cinch
```

## Schichten

| Schicht | Ort | Aufgabe | darf benutzen |
|---|---|---|---|
| Oberfläche | `backend/static/index.html` | Weboberfläche, eine Datei ohne Build | REST-API |
| Abläufe und Regelung | `backend/app.py` | Regelschleife (50 Hz), Messung, REST-API (Flask), Einstellungen | CamillaDSP-Client, ffmpeg, `flirc_util`, systemd |
| Zugriff auf CamillaDSP | `backend/app.py` (Objekt `cdsp`) | Websocket-API | pycamilladsp |
| USB-Quelle | `bin/tvdsp-quelle.py` (Dienst `tvdsp-quelle`) | USB-Empfänger lesen, PCM/Dolby erkennen, decodieren, mischen, Takt halten | PyAV, numpy, pyalsaaudio |
| Fernbedienung | `backend/flirc_listener.py` (Dienst `tvdsp-flirc`) | Tasten des FLIRC-Sticks lesen, an das Backend melden | evdev, nur lokaler Endpunkt |
| Systemnahe Skripte | `bin/detect-card.sh`, `bin/enable-spdif-in.sh`, `bin/install-flirc-cli.sh` | Soundkarte finden, Toslink-Eingang und Doppelton-Schutz einrichten, FLIRC-Werkzeug installieren | dsptoolkit, ALSA |
| Einrichtung | `install.sh` | Pakete, Dienste, ALSA, DSP-Profil – wiederholbar | alles |

Weitere Dateien: `config/tv.yml` (CamillaDSP-Pipeline im Kaltstartzustand – im Betrieb
überschreibt das Backend die Werte), `dsp/dspdac-noautomute.xml` (DSP-Profil, Pflicht),
`backend/silero_vad.onnx` (Sprachmodell), `abhaengigkeiten/` (feste Paketversionen),
`werkzeuge/` (Tests, Ausrollen, Release), `/etc/asound.conf` (von `install.sh` angelegt:
`dsnoop_tv`, `tvmess`, `dsnoop_usb`, `tvmess_usb`).

## Fader-Belegung in CamillaDSP

| Fader | Belegung | Anmerkung |
|---|---|---|
| Main (0) | Startschutz und Stumm | Dienst startet mit `--gain=-35`; das Backend gibt Main erst auf 0 dB frei, wenn der Pegel auf Aux2 sitzt und „ausgang“ in der aktiven Pipeline steht. Main wirkt **vor** der Pipeline. |
| Aux1 (1) | Leveler der Band-Regelung | im 20-ms-Takt gesetzt, intern gerampt |
| Aux2 (2) | Pegelregler | Filter „ausgang“ hinter der Decke, höchstens 0 dB |
| Aux3 (3) | Anteil der Loudness-Kompensation | Dienst startet mit `--gain3=-100` (aus) |
| Aux4 (4) | Anteil der Höhendämpfung | wird vor `set_active` gesetzt; startet sonst mit 0 dB = volle Dämpfung |

## Austauschbare Technik

| Technik | Schnittstelle | Adapter | Warum diese | Alternativen | Wechsel, wenn … |
|---|---|---|---|---|---|
| CamillaDSP 4.1.3 (Audioverarbeitung, eigenständiges Programm, GPL-3.0) | Websocket-API (pycamilladsp) | `backend/app.py` | echtzeitfähig, Fader ohne Pipeline-Neuaufbau, Datei-Modus für Offline-Messungen | – | Konfigurationsformat ändert sich (Version in `install.sh` festgenagelt) |
| Lautheitsmessung | ffmpeg `ebur128` über `tvmess` | `backend/app.py` | EBU R128, M/S mit 10 Hz, EQ-Spiegel per `equalizer` | eigene Implementierung | – |
| Dolby-Decoder | PyAV | `bin/tvdsp-quelle.py` | bringt eigene ffmpeg-Bibliotheken mit | – | – |
| Spracherkennung | onnxruntime + Silero VAD | `backend/app.py` | nur ein Flag, nie im Signalweg; ~5 % eines Kerns | WebRTC-VAD (an Musik unbrauchbar) | – |
| DSP-Chip | `dsptoolkit` / `sigmatcpserver` | `bin/enable-spdif-in.sh`, `install.sh` | offizieller Weg | REST von `sigmatcpserver --rest` (empfohlen) | dsptoolkit entfällt |
| Fernbedienung | FLIRC als USB-Tastatur, `flirc_util` | `backend/flirc_listener.py`, `backend/app.py` | völlig getrennt von der Audiokette | HDMI-CEC, IR an GPIO (siehe Entscheidungen) | – |

## Plattformunterschiede

Nur Raspberry Pi OS (Debian 12, aarch64). Die Offline-Pakete in `abhaengigkeiten/pakete/`
passen nur zu dieser Python-Version und Architektur; sonst installiert `install.sh` dieselben
Versionen aus dem Internet.

## Gespeicherte Daten

| Datei | Ort | Formatversion | Übernahme älterer Stände |
|---|---|---|---|
| Einstellungen (Band, EQ, Pegel, Mix, IR) | `/opt/tvdsp/config/settings.json` | keine eigene | Beim Laden zählen nur Schlüssel, die in den Standardwerten von `state` stehen; ältere EQ-Formate werden einmalig umgerechnet; unbekannte Mix-Voreinstellungen werden an den Werten erkannt |
| Tastenzuordnung für den Lauscher | `/opt/tvdsp/config/ir-keys.json` | keine | wird aus `settings.json` neu geschrieben |
| Prüfsumme des DSP-Profils (Systemstart) | `/opt/tvdsp/config/dsp-profil-pruefsumme` | – | nur gültig, wenn seit dem letzten Start geschrieben |
| Status der USB-Quelle | `/run/tvdsp/quelle-status.json` | – | im RAM, nicht auf der SD-Karte |
| Mitschnitte (Records-Reiter) | `~/Downloads/tvdsp_JJJJMMTT_HHMMSS.mp3` | – | höchstens 2 Stunden je Aufnahme |

## Entscheidungen

Zeitlich geordnet, neue ans Ende. Die Entscheidungen bis 3.3.8 stammen aus der Entwicklung
vor der Veröffentlichung; Messwerte sind am Gerät oder offline mit dem echten CamillaDSP
ermittelt. Nicht ohne neue Messung umdrehen.

### Regelung über Fader, nicht über Konfigurationstausch

- **Frage:** Wie setzt die Regelschleife ihre Verstärkung?
- **Entschieden:** über den Aux1-Fader (`SetFaderVolume`), 20-ms-Takt.
- **Warum:** Ein Konfigurationstausch baut die Pipeline neu auf – bei bis zu zehn Schritten pro
  Sekunde hörbare Aussetzer.
- **Verworfen:** `set_active` je Regelschritt.

### Die Regelung rechnet vorwärts

- **Entschieden:** Gemessen wird der Eingang vor Aux1; die eigene Verstärkung wird eingerechnet,
  das Ziel ist ein absoluter Soll-Gain.
- **Warum:** Ohne das schrumpft die gemessene Abweichung nie, die Regelung läuft in den
  Anschlag (im Test bei +19,96 dB festgeklebt).

### Nur noch die Band-Regelung (ab 2.0.0, R128-Option ab 2.9.0 entfernt)

- **Entschieden:** Man gibt ein Lautheitsband vor (Standard −30 … −26 LUFS). Zu laut → herunter
  bis knapp unter die Oberkante; zu leise → hinauf bis knapp über die Unterkante; **im Band
  nichts**. Keine Modi, keine weiteren Regler; die Zeitkonstanten sind offline hergeleitet.
- **Warum:** Frühere Regelungen (RMS, EBU-R128-Leveler mit Hülle) ließen nach einer leisen
  Szene die folgende laute deutlich zu laut durch (gemessen: +6 dB Sprung nach 0,4 s noch
  +3,6 dB). Ein „Einpendeln zur Mitte“ pumpte hörbar.
- **Verworfen:** RMS- und R128-Zweig (bis 2.8.x), Einpendeln zur Mitte, Modi Tag/Abend/Nacht.
- **Neu prüfen, wenn:** Hörtests mit anderem Material dagegen sprechen. Konzept und
  Messungen: [band-konzept.md](band-konzept.md).

### Die Decke im DSP, mit Vorlauf

- **Entschieden:** Ein CamillaDSP-Kompressor hinter Aux1 (20:1, Attack 2 ms, Release 300 ms)
  mit Schwelle = Oberkante + 1 dB; die Nutzkanäle laufen 30 ms verzögert, die Sidechain
  (Mitte, Hochpass 50 Hz) unverzögert.
- **Warum:** Offline gemessen höchstens +3,8 dB für 7 ms über der Decke statt +11 dB, bis der
  Leveler greift. 40 ms Vorlauf bringen nichts mehr. Der Vorlauf ist abschaltbar
  (Lippensynchronität).
- **Detektor:** wird aus den fertigen Nutzkanälen gebildet (sieht so auch die
  Loudness-Kompensation); Ausgleich +3,6 dB, weil der Kompressor |x| je Sample in dB glättet
  und kein RMS misst.
- **Verworfen:** Sidechain mit Sprachband-Bandpass (eine bassbetonte Explosion ist dort fast
  unsichtbar: 17,3 → 16,4 LU, praktisch wirkungslos).

### Pegel hinter der Decke (ab 2.7.0)

- **Entschieden:** Der Pegelregler sitzt auf Aux2 hinter Decke und Mischung, vor dem
  Limiter; Main nur Startschutz und Stumm.
- **Warum:** CamillaDSP wendet Main vor der Pipeline an – jeder Detektor sah das Signal sonst
  um den Pegel zu leise, die Decke griff nie.

### Limiter hart, kein positiver Pegel

- **Entschieden:** `soft_clip: false`; Pegel höchstens 0 dB.
- **Warum:** CamillaDSPs Soft-Clip ist ein kubischer Waveshaper, der schon ab etwa −6 dBFS
  verzerrt. Mehr Lautstärke kommt vom Verstärker.

### Bypass gleicht den Vorpegel aus

- **Entschieden:** Im Bypass sind Decke, Loudness-Kompensation und EQ neutral, der Vorpegel
  (−6 dB) wird ausgeglichen.
- **Warum:** Sonst wäre der Bypass leiser und der A/B-Vergleich fiele systematisch zugunsten
  der Verarbeitung aus (vorher 5,9 dB, jetzt 0,4 dB Unterschied).

### Loudness-Kompensation als Parallelpfad (ab 1.11.0)

- **Entschieden:** `out = x + a·(H(x) − x)` mit Low-Shelf +6 dB bei 120 Hz, High-Shelf +2 dB bei
  6 kHz und Trim −1,4 dB (lautheitsneutral auf rosa Rauschen); `a` über Aux3 als Funktion der
  aktuellen Absenkung (volle Kurve ab 20 dB), 2 s geglättet.
- **Warum:** Stark abgesenkte laute Szenen klingen sonst dumpf (Kurven gleicher Lautheit nach
  ISO 226). Ein Fader statt Konfigurationstausch.

### EQ: Bänder mit Bandbreite, glatte Kurve durch die Punkte

- **Entschieden:** 16 Bänder von 16 Hz bis 16 kHz, Bandbreite fest 1 Oktave (`bandwidth`, nie
  `q`); die Reglerwerte werden so gelöst, dass die echte Summenkurve der monotonen kubischen
  Interpolation der gesetzten Punkte folgt. Backend und Oberfläche rechnen identisch.
- **Warum:** Mit `q` wird die Glocke nahe der Nyquist-Grenze gestaucht; schmalere Bänder lassen
  zwischen den Punkten Beulen. Gezeichnete und gemessene Kurve stimmen auf 0,00 dB überein.

### Fernbedienung über FLIRC (ab 1.7.0)

- **Entschieden:** FLIRC-USB-Empfänger, der als Tastatur auftritt; ein eigener Lauscher-Dienst
  mit `Wants=`, nie `Requires=`. Der Stick wird auf zwölf neutrale Plätze angelernt, die
  Zuordnung Platz → Aktion liegt in den Einstellungen.
- **Verworfen:** HDMI-CEC (viele Fernseher schalten dann den optischen Ausgang ab); IR-Empfänger
  an GPIO (die Steckerleiste der DAC+ DSP lässt keine Stifte nach oben durch, Löten am Board
  neben Mute/Reset ist zu riskant).

### USB-Eingang mit Dolby Digital 5.1 und Mix (ab 3.0.0)

- **Entschieden:** Ein eigener Dienst `tvdsp-quelle` liest den USB-Empfänger, erkennt PCM oder
  Dolby Digital/DTS (IEC-61937-Präambel), decodiert 5.1 und mischt nach dem Reiter „Mix“ auf
  Stereo. Ausgabe immer Stereo in den ALSA-Loopback; die gesamte Band-Kette wirkt **nach** dem
  Mix. Fehlt der Empfänger oder meldet sich der Dienst länger als 5 s nicht, liest CamillaDSP
  automatisch wieder die HiFiBerry – nie stumm.
- **Geräte:** miniDSP MCHStreamer I2S TosLink (USB-ID 2752:0019, Vorrang, asynchron mit
  Toslink-Takt: 48 000,0 Hz ohne Überläufe gemessen) und Hifime UR27 (262a:9227) als
  Ausweich; erkannt an der USB-ID. Die Tabellen `USB_GERAETE` (tvdsp-quelle) und
  `USB_EMPFAENGER` (app.py) gleich halten.
- **Takt:** Feinresampler in tvdsp-quelle (kubisch, PI-Regler auf 21 ms Gesamtverzögerung);
  die Strecke Loopback → HiFiBerry gleicht CamillaDSP im USB-Betrieb mit dem
  AsyncSinc-Resampler aus. Der HiFiBerry-Betrieb bleibt bitgenau ohne Resampler.
- **Mix:** Anteile Front, Center, LFE, Surround, Summe je Seite 1 (kein Übersteuern).
  Voreinstellungen „Kino“ (Standard: Center etwas stärker als Front, LFE spielt mit),
  „Neutral“ und „Sprache“. Die Tabelle `MIX_VOREINSTELLUNGEN` steht in `app.py` und
  `tvdsp-quelle.py` – gleich halten.
- **Kaputte Frames:** `err_detect=crccheck+explode` → 32 ms Stille mit Blende, nie Rauschen.

### Höhendämpfung (ab 3.2.0)

- **Entschieden:** Laufend wird der Höhenanteil gemessen (Lautheit über 3 kHz minus
  Gesamtlautheit, Energiemittel 3 s). Über −8 dB werden die Höhen um den Überschuss gedämpft,
  höchstens 6 dB (Attack 0,3 s, Release 1,5 s), über einen Parallelpfad mit High-Shelf auf
  Aux4. Schalter aus oder Bypass: Stufe nicht in der Pipeline.
- **Verworfen:** Eine Höhengewichtung nur in der Messung (3.1.0) – eine Lautheitsregelung
  ändert den Pegel, nicht die Klangbalance, und hob Sprache sogar an.

### Fremdsoftware in festen Versionen (ab 2.8.3)

- **Entschieden:** Alle Pakete in genau getesteten Versionen (`abhaengigkeiten/requirements*.txt`,
  `CDSP_VERSION` in `install.sh`, `FLIRC_VERSION` im Helfer); optionaler Offline-Cache.
- **Warum:** Das Konfigurationsformat von CamillaDSP ändert sich zwischen Hauptversionen
  (Exitcode 101 = ungültige Konfiguration).

### Anbindung über pycamilladsp, Projekt unter GPL-3.0

- **Frage:** Wie spricht das Backend mit CamillaDSP?
- **Entschieden:** über die Python-Bibliothek pycamilladsp (Websocket-API), festgenagelt auf
  einen GitHub-Commit in `abhaengigkeiten/requirements.txt`. Weil pycamilladsp unter GPL-3.0
  steht, steht auch das Projekt unter GPL-3.0.
- **Neu prüfen, wenn:** CamillaDSP oder pycamilladsp das Protokoll ändern (Versionen in
  `install.sh` und `requirements.txt` festgenagelt).
