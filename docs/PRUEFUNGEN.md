# Prüfungen und Prüfwerkzeuge – TV_DSP_Normalizer

Alles, womit TV_DSP_Normalizer geprüft und gemessen wird. Neue Prüfungen hier ergänzen und
vorhandene Werkzeuge erweitern statt neu schreiben. Erzeugte Daten (Messläufe, Mitschnitte,
Auswertungen) kommen nie ins Git, sondern in einen lokalen Testordner außerhalb des Projekts
(im Folgenden `<testordner>`).

## Tests

Alle Testskripte liegen in `werkzeuge/`, brauchen keine Soundkarte (außer `funktest.py`) und
geben 0 zurück, wenn alles stimmt, sonst 1.

| Skript | Prüft | Wann laufen lassen | Braucht |
|---|---|---|---|
| `apitest.py` | Backend gegen ein nachgebautes CamillaDSP: Endpunkte, gespeicherte Schlüssel, berechnete Werte (Schwelle der Decke, Pipeline, Neustart-Erkennung, EQ-Spiegel, Eingang USB/HiFiBerry mit Rückfall, Mix, Höhendämpfung, Fernbedienung). Erzeugte Konfigurationen werden mit `camilladsp --check` geprüft. | nach jeder Änderung an `backend/app.py` | Flask, PyYAML, numpy, `camilladsp` |
| `quelletest.py` | `bin/tvdsp-quelle.py` ohne Soundkarte: erzeugt mit ffmpeg ein 5.1-Signal, codiert es als Dolby Digital und verpackt es als IEC 61937 – Mix-Mathematik (0,00 dB), Stereo bitgenau, Wechsel PCM ↔ Dolby ohne Rauschen, verlorene Samples → Stille, Latenz stabil | nach jeder Änderung an `bin/tvdsp-quelle.py` (USB-Eingang, Dolby, Mix) | numpy, PyAV, ffmpeg |
| `funktest.py` | die **Wirkung** am echten `camilladsp`: Die laufende Konfiguration wird mit den laufenden Fader-Werten offline durch das Programm geschickt – Pegel, Fader, EQ- und Loudness-Kurve (gemessen gegen gezeichnet), Vorlauf, Bypass, Limiter, Höhendämpfung und die Decke an einem TV-Mitschnitt. Ändert am Gerät nichts. | auf dem Pi nach jeder Änderung an Kette, Fadern oder Decke (zusätzlich zu `apitest.py`) | laufendes Backend und CamillaDSP, ein Mitschnitt |

Aufrufe:

    python3 werkzeuge/apitest.py
    python3 werkzeuge/quelletest.py [aufnahme.wav]
    /opt/tvdsp/venv/bin/python3 werkzeuge/funktest.py [mitschnitt.mp3]

`apitest.py` ruft `/usr/local/bin/camilladsp --check` auf (dort legt `install.sh` das
Programm ab). `APITEST_OUT=<ordner>` legt die vom API-Test
erzeugte Konfiguration (`neu_cfg.yml`) für Offline-Messungen ab. `funktest.py` nimmt ohne
Angabe den neuesten Mitschnitt `~/Downloads/tvdsp_*.mp3` (Records-Reiter).

**Warum zwei Ebenen:** Der Fehler „Main wirkt vor der Pipeline“ (Fehlergedächtnis Nr. 12)
hatte richtig berechnete und richtig übergebene Werte – falsch war die Annahme, wo CamillaDSP
die Hauptlautstärke anwendet. Eine Werteprüfung (`apitest.py`) sieht so etwas nie, die
Wirkungsprüfung (`funktest.py`) sofort.

## Prüfungen bei der Installation

- `install.sh` bricht ab, wenn der Doppelton-Schutz (Register 4547 = 0 per `write-mem` mit
  Rücklesen) in `bin/enable-spdif-in.sh` fehlt.
- `install.sh` prüft `config/tv.yml` mit `camilladsp --check` gegen die installierte Binärdatei
  und ersetzt eine abgelehnte Datei (mit Sicherung).
- `install.sh` vergleicht die Prüfsumme des DSP-Profils und flasht nur bei Abweichung.
- `werkzeuge/deploy.sh` prüft nach dem Ausrollen, ob der Dienst die erwartete Version meldet,
  und rollt sonst zurück.

## Messbefehle

| Aufruf | Wofür |
|---|---|
| `camilladsp --check config/tv.yml` | Konfiguration gegen die Binärdatei prüfen |
| `ffmpeg -f f32le -ar 48000 -ac 2 -i datei.f32 -af ebur128 -f null -` | Lautheit einer Offline-Ausgabe (nur den Abschnitt nach `Summary:` auswerten) |
| `curl -s http://localhost:8080/api/version` | laufende Version des Backends |
| `/proc/asound/card0/pcm0p/sub0/status` | Aussetzer der Wiedergabe (`trigger_time` ändert sich) |
| CPU je Prozess aus `/proc/<pid>/stat` über 60 s | Last von Backend, CamillaDSP, ffmpeg |
| `journalctl -u tvdsp-web -f`, `-u tvdsp-quelle -f`, `-u tvdsp-flirc -f` | Protokolle von Backend, USB-Quelle, Fernbedienung |
| `TVDSP_QUELLE_MITSCHNITT=<datei>` (Umgebung von `tvdsp-quelle`) | rohe Eingangsdaten des USB-Empfängers mitschreiben |

**Offline-Messaufbau:** CamillaDSP kann statt einer Soundkarte Dateien lesen und schreiben
(Capture `RawFile`, Playback `File`, Format `F32_LE`) – schneller als Echtzeit, gut für
Kompressor und Filter. Für die Regelung in echter Zeit: Capture `Stdin`, gespeist im
100-ms-Takt.

**Pumpen messen:** Gain-Umkehrungen um mehr als 2 dB innerhalb von 3 s pro Minute („Atmer“)
und der Gainweg in dB/min – beides aus dem Gain-Verlauf berechenbar, bevor etwas am Gerät
läuft ([band-konzept.md](band-konzept.md)).

## Testbestand

| Fall | Beschreibung (ohne Titel) | Warum schwierig |
|---|---|---|
| Orchester | Orchesterstück ohne Dialog, I −15,5 LUFS, LRA 16,1 LU | große Dynamik, keine Sprache – Pumpen und Spracherkennung |
| Trailer | Film-Trailer mit Dialog und Musik, I −22,8 LUFS, LRA 4,8 LU | kurze leise Dialogzeilen zwischen lauten Musikstellen |
| TV-Mitschnitte | Mitschnitte aus dem Records-Reiter (Senderwechsel, Werbung) | leises Sendematerial, Pegelsprünge |
| Dolby synthetisch | von `quelletest.py` erzeugtes 5.1 mit einem Ton je Kanal | Mix-Mathematik, Wechsel PCM/Dolby, verlorene Samples |

## Letzter Stand

| Datum | Version | Kennzahlen |
|---|---|---|
| unbekannt | 3.3.8 | Band-Decke offline: höchstens +3,8 dB für 7 ms über der Decke (30 ms Vorlauf, 2 ms Attack); miniDSP: 48 000,0 Hz, 0 Überläufe, 2 von 3750 Dolby-Frames mit echtem CRC-Fehler in 2 min |
