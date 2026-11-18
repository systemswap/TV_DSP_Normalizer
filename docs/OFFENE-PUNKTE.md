# Offene Punkte – TV_DSP_Normalizer

Was als Nächstes ansteht, was bewusst zurückgestellt ist und was nicht wieder
vorgeschlagen werden soll. Erledigtes wird gestrichen (die Geschichte steht in Git und
im Fehlergedächtnis).

## Als Nächstes

- **Kaltstart mit `tvdsp-kick` bestätigen.** Den Ton nach echter Netztrennung ohne Handgriff
  prüfen (bisher teils von Hand mit `dsptoolkit get-checksum` ausgelöst).

## Später

- **`enable-spdif-in.sh` auf die REST-Schnittstelle umstellen.** HiFiBerry erklärt
  `dsptoolkit` für veraltet; empfohlen ist `sigmatcpserver --rest`.
- **Ursache der Kaltstart-Stille klären.** Verdacht: Sperre der Wiedergabe-Ressource im
  Umfeld von `sigmatcpserver` (Register 0xF600, Signal USR1). Fällt die Sperre in einer neueren
  Version weg, kann `tvdsp-kick` entfallen.
- **Messlatenz der ffmpeg-M-Frames** am laufenden System bestimmen (Kreuzkorrelation gegen die
  Pegel aus CamillaDSP); sie begrenzt die Zeitkonstanten nach unten.
- **Regelgesetz gegen weitere TV-Mitschnitte** (Senderwechsel, Werbung, Nachrichten, Sport)
  offline fahren: Zeit bis ins Band, Anteil im Band, Gain-Umkehrungen pro Minute.
- **Kurze leise Stellen** (unter etwa 1–2,5 s zwischen lauten Effekten) werden nicht angehoben –
  der Preis des Pumpschutzes. Falls gewünscht, wäre Aufwärtskompression ein eigenes Werkzeug
  mit eigenem Pumprisiko.

## Bewusst nicht (nicht wieder vorschlagen)

- RMS- oder EBU-R128-Regelzweig wieder einbauen – durch die Band-Regelung ersetzt; die
  Band-Regelung läuft im Hörtest zufriedenstellend.
- „Einpendeln zur Mitte“ im Band – pumpt hörbar (gemessen 6,9 Gain-Umkehrungen/min).
- Weitere Regler im Band-Reiter – die Zeitkonstanten werden offline hergeleitet.
- Höhengewichtung nur in der Messung (3.1.0) – ändert den Pegel, nicht die Klangbalance.
- WebRTC-VAD als Spracherkennung – hält Orchestermusik zu 87 % für Sprache.
- `soft_clip` im Limiter oder Kompressor – verzerrt dauerhaft.
- Pegelregler auf Main – Main wirkt vor der Pipeline, die Decke greift dann nicht.
- Startoption `-w` für CamillaDSP – ohne aktive Konfiguration scheitert jeder Zugriff.
- `dsptoolkit get-checksum` periodisch – hält jedes Mal den DSP-Chip an (Knacken).
- HDMI-CEC oder IR-Empfänger an GPIO für die Fernbedienung (siehe Architektur).
- „PCM Rate Shift“ des ALSA-Loopbacks oder Pipes über `arecord`/`aplay` im USB-Weg.
