# TVDSP – Wartung: Fremdsoftware, DSP-Profil, Backup

Diese Seite beschreibt, was zum Betrieb der Anlage gehört, woher es kommt und
wie man es sicher aktualisiert. Hintergründe und Messungen stehen in
`docs/Architektur.md`, `docs/hifiberry-dsp.md`, `docs/band-konzept.md` und
`docs/FEHLERGEDAECHTNIS.md`.

## 1. Was im Projekt liegt

| Teil | Ort | In git | In der Backup-Zip |
|---|---|---|---|
| Programm, Oberfläche, Installer | `backend/`, `bin/`, `install.sh` | ja | ja |
| CamillaDSP-Kaltstartkonfiguration | `config/tv.yml` | ja | ja |
| **DSP-Profil der HiFiBerry DAC+ DSP** | `dsp/dspdac-noautomute.xml` | ja | ja |
| Sprachmodell (Silero VAD) | `backend/silero_vad.onnx` | ja | ja |
| Feste Versionen der Fremdsoftware | `abhaengigkeiten/requirements*.txt` | ja | ja |
| Fremdsoftware selbst (offline) | `abhaengigkeiten/pakete/` | nein | ja |
| Einstellungen des Geräts, FLIRC-Tasten | `/opt/tvdsp/config/`, FLIRC-Stick | nein | ja (`geraetestand/`) |
| Debian-Systempakete (ffmpeg, Python …) | apt | nein | nein – kommen aus Raspberry Pi OS 12 „bookworm“ |

## 2. Das DSP-Profil

- **Datei:** `dsp/dspdac-noautomute.xml`, byte-gleich mit dem Original von
  HiFiBerry (`hifiberry/hifiberry-dsp`, `sample_files/xml/dspdac-noautomute.xml`).
- **Warum genau dieses:** Der Analogausgang der DAC+ DSP hängt hinter dem
  DSP-Chip. Nur mit diesem Profil kommt der Ton vom Pi überhaupt am Ausgang an.
  Das früher auf der Karte liegende „Beocreate Universal“ (Prüfsumme
  `40FB6C92…`) reichte nur Toslink direkt durch – falsch für uns.
- **Prüfsumme auf dem Chip:** `7F88CAAD8E8A7E4F05731605AE16EF38`.
  `install.sh` liest sie aus und flasht das Profil nur bei Abweichung (ins
  EEPROM, dauerhaft). Die Weboberfläche prüft sie einmal pro Systemstart und
  meldet ein fremdes Profil rot.
- **Achtung:** Das Auslesen der Prüfsumme hält den DSP-Chip kurz an – ein
  hörbarer Aussetzer. Deshalb nie regelmäßig abfragen (bis 2.8.6 geschah das
  alle 10 Minuten und verursachte ein Knacken).
- **Pflicht danach: Register 4547 = 0** (`dsptoolkit write-mem 4547 0`). Sonst
  mischt das Profil den unbearbeiteten Toslink-Ton zum bearbeiteten (Doppelton).
  `bin/enable-spdif-in.sh` setzt das bei jedem Start. Diese Logik darf nie
  entfernt werden.

## 3. Fremdsoftware: feste Versionen

Alles läuft in genau den Versionen, die zusammen getestet sind:

| Software | Version | festgelegt in |
|---|---|---|
| Python-Pakete (Flask, pycamilladsp, hifiberrydsp, onnxruntime …) | siehe Datei | `abhaengigkeiten/requirements.txt`, `requirements-vad.txt` |
| pycamilladsp | GitHub-Commit `fdc0d163` | `abhaengigkeiten/requirements.txt` |
| CamillaDSP | 4.1.3 | `CDSP_VERSION` in `install.sh` |
| FLIRC | 3.27.19 | `FLIRC_VERSION` in `bin/install-flirc-cli.sh` |

`install.sh` nimmt die Pakete aus `abhaengigkeiten/pakete/`, wenn sie daneben
liegen – ohne Internet. Die Python-Pakete passen nur zu dem System, das in
`abhaengigkeiten/pakete/PLATTFORM.txt` steht; auf einem anderen System holt
`install.sh` dieselben Versionen aus dem Internet.

## 4. Fremdsoftware aktualisieren – immer in dieser Reihenfolge

1. **Version ändern**, und zwar nur an der Stelle aus der Tabelle oben:
   - Python-Paket: Zeile in `abhaengigkeiten/requirements.txt` bzw.
     `requirements-vad.txt` (bei pycamilladsp den Commit in der git-URL),
   - CamillaDSP: `CDSP_VERSION` in `install.sh` – vorher prüfen, ob die
     Konfiguration noch gültig ist (`camilladsp --check config/tv.yml`), das
     Format ändert sich zwischen Hauptversionen,
   - FLIRC: `FLIRC_VERSION` in `bin/install-flirc-cli.sh`.
2. **Offline-Pakete neu bauen:** `werkzeuge/abhaengigkeiten.sh`
   (holt genau diese Versionen, installiert die Python-Pakete zur Probe bei
   gesperrtem Netz, schreibt Prüfsummen).
3. **Am Pi installieren und testen:** `sudo ./install.sh`, danach Ton, Decke,
   Regelung, Fernbedienung und Weboberfläche prüfen,
   `werkzeuge/apitest.py` und `werkzeuge/funktest.py` laufen lassen.
4. **Erst dann veröffentlichen:** `werkzeuge/release.sh` – die Zip enthält
   damit die neuen Pakete.

**Nie eine Version ändern, ohne Schritt 2.** Sonst lägen in der Zip andere
Pakete als in der Liste steht. Das ist technisch abgesichert: Die Offline-
Pakete merken sich, aus welchen Versionen sie gebaut wurden
(`pakete/gebaut-aus/`). Passt das nicht mehr, bricht `werkzeuge/backup.sh`
(und damit `release.sh`) ab, und `install.sh` benutzt die veralteten Pakete
nicht, sondern holt die eingetragenen Versionen aus dem Internet.

## 5. Backup und Wiederherstellung

Jede Version legt `werkzeuge/release.sh` über `werkzeuge/backup.sh` als
`tvdsp-x_y_z.zip` auf eine Netzwerkfreigabe (Freigabe `homes`, Ordner
`TVDSP_GIT_Backup`; der Server steht in `~/.config/tvdsp/backup-server` oder
in `BACKUP_SERVER`, ein Testlauf in einen lokalen Ordner geht mit
`BACKUP_ZIEL=/pfad`). Die Zip enthält das Projekt mit kompletter
Git-Historie, die Offline-Pakete und den Gerätestand (Einstellungen,
FLIRC-Tasten) und wird nach dem Kopieren am Ziel geprüft. Die Schritte zum
Wiederherstellen schreibt das Skript als `WIEDERHERSTELLEN.txt` daneben.

Ist die Freigabe dauerhaft unter `/mnt/nas` eingebunden (fstab + Automount),
nimmt `backup.sh` diesen Pfad, sonst den gvfs-Mount der Desktop-Sitzung.

## 6. Tests nach jeder Änderung an Kette oder Regelung

Ab 3.0.0 zusätzlich `werkzeuge/quelletest.py` nach jeder Änderung an
`bin/tvdsp-quelle.py` (USB-Eingang, Dolby, Mix).

- `werkzeuge/apitest.py` – Backend gegen ein Schein-CamillaDSP: Endpunkte,
  gespeicherte Schlüssel, berechnete Werte (Decke-Schwelle, Pipeline,
  Neustart-Erkennung, EQ-Spiegel). Läuft ohne Gerät.
- `werkzeuge/funktest.py` – prüft die **Wirkung** am echten camilladsp: die
  laufende Konfiguration mit den laufenden Fader-Werten wird offline durch
  das Programm geschickt (Pegel, Fader, EQ- und Psychoakustik-Kurve gemessen
  gegen gezeichnet, Vorlauf, Bypass, Limiter und die Decke an einem
  TV-Mitschnitt). Aufruf am Pi bei laufendem Dienst:
  `/opt/tvdsp/venv/bin/python3 werkzeuge/funktest.py [mitschnitt.mp3]`
  (ohne Angabe der neueste Mitschnitt aus dem Records-Reiter). Ändert am
  Gerät nichts.

Warum beide: Der 25-dB-Fehler bis 2.6.x hatte richtig berechnete und richtig
übergebene Werte – falsch war die Annahme, wo CamillaDSP die Hauptlautstärke
anwendet. Eine Werte-Prüfung (apitest) sieht so etwas nie, der Funktionstest
sofort, weil die Decke dann nichts absenkt.

## 6a. USB-Eingang (Hifime UR27, ab 3.0.0)

Im Reiter Settings unter „Eingang“ wählbar: HiFiBerry (bisher) oder USB (UR27).
Der Dienst `tvdsp-quelle` liest den UR27, erkennt Stereo oder Dolby Digital,
mischt 5.1 nach dem Reiter „Mix“ auf Stereo und gibt es über den Loopback an
die gewohnte Kette weiter. Den Fernseher dafür auf „Automatisch“ oder
„Bitstream“ stellen; für den HiFiBerry-Eingang wieder auf **PCM**.

- Protokoll: `journalctl -u tvdsp-quelle -f`
- Zustand: `cat /opt/tvdsp/config/quelle-status.json` (Format, Layout,
  Aussetzer pro Minute, Verzögerung, Tempo-Ausgleich in ppm)
- Test ohne Soundkarte: `python3 werkzeuge/quelletest.py [aufnahme.wav]`
- Der UR27 verliert Samples (adaptiver USB-Endpunkt); bei Dolby entstehen
  dadurch kurze Aussetzer. Deshalb hat der miniDSP MCHStreamer Vorrang.
