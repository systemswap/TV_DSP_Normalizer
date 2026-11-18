# Die HiFiBerry DAC+ DSP – was man wissen muss

Die DAC+ DSP ist mehr als ein Digital-Analog-Wandler: Zwischen Pi und Analogausgang sitzt
ein eigener DSP-Chip (ADAU1451) mit eigenem Programm. Fast alle schwer zu findenden Fehler
dieses Projekts hatten hier ihre Ursache. Diese Seite fasst zusammen, was am echten Gerät
belegt ist. Der Code verweist auf die Abschnitte unten.

## Das DSP-Profil ist die halbe Karte

**Der Analogausgang hängt hinter dem DSP-Chip.** Der DAC bekommt sein Signal
ausschließlich vom DSP. Welche Quelle beim DAC ankommt (I2S vom Pi, Toslink oder nichts),
entscheidet allein das geladene DSP-Profil. Ohne passendes Profil bleibt der Ausgang
still, egal was der Pi spielt: `speaker-test` läuft fehlerfrei durch, CamillaDSP meldet
Ausgangspegel, denn ALSA nimmt die Daten an – und trotzdem kommt nichts.

- **Ab Werk** lag auf der Karte das Profil „Beocreate Universal“ (Prüfsumme
  `40FB6C92F57ABB70177CE053C73F54DC`). Es gehört zu einem anderen HiFiBerry-Verstärker,
  reicht zufällig Toslink direkt zum Analogausgang durch, routet aber nie den Ton vom Pi.
  Was wie ein Regressionsfehler aussah, war ein nie vorhandener Signalweg.
- **Die Lösung:** das Profil `dspdac-noautomute`. Die Datei `dsp/dspdac-noautomute.xml`
  ist byte-gleich mit dem Original von HiFiBerry
  (<https://github.com/hifiberry/hifiberry-dsp>, `sample_files/xml/dspdac-noautomute.xml`,
  MIT-Lizenz, siehe `THIRD-PARTY-NOTICES.md`). Sie liegt im Projekt, damit eine
  Reparatur nicht vom Netz abhängt.
- **Prüfsumme auf dem Chip:** `7F88CAAD8E8A7E4F05731605AE16EF38`. `install.sh` liest sie
  aus und schreibt das Profil nur bei Abweichung mit `dsptoolkit install-profile` ins
  EEPROM. Dort überlebt es jeden Kaltstart.
- **Register dieses Profils** (aus seinen eigenen Metadaten gelesen, nicht geraten):

  | Name | Adresse | Wirkung |
  |---|---|---|
  | `volumeLimitPiRegister` | 4542 | Lautstärke nur Pi-Weg |
  | `volumeLimitSPDIFRegister` | 4544 | Lautstärke nur Toslink-Direktweg |
  | `mutePiRegister` | 4545 | Stumm nur Pi-Weg |
  | `muteSPDIFRegister` | 4547 | Stumm nur Toslink-Direktweg |
  | `volumeControlRegister` | 4548 | Gesamtlautstärke |
  | `channelSelectRegister` | 4552 | |
  | `spdifSource` | 4553 | |

  Das sind Adressen im Parameter-RAM: 32 Bit, also `dsptoolkit write-mem`, **nie**
  `write-reg` (schreibt nur 2 Bytes). Vor jedem Schreiben den Originalwert mit
  `read-int`/`read-hex` notieren.
- Die Weboberfläche zeigt ein fremdes Profil rot an (Prüfung einmal pro Systemstart,
  siehe „get-checksum hält den DSP-Chip an“).

## Kaltstart-Stille

Nach einem echten Kaltstart (Netzstecker gezogen, nicht nur `reboot`) ist alles korrekt
eingerichtet – Toslink-Eingang frei, Register 4547 = 0, CamillaDSP läuft mit Ausgangspegel,
niemand hält die Karte exklusiv – und trotzdem kommt minutenlang kein Analogton.

- Ein einzelner `dsptoolkit get-checksum` bringt den Ton sofort. `read-reg` auf ein
  einzelnes Register tut es **nicht** (am Gerät sauber getrennt geprüft). Nur ein Zugriff
  auf das ganze Programm wirkt.
- `get-checksum` schreibt nichts. Der Ton ist ein Nebeneffekt davon, dass dieser Zugriff
  den Chip kurz anhält (siehe unten). Die genaue Ursache ist nicht abschließend geklärt;
  Hinweise im Umfeld von hifiberry-dsp deuten auf eine Sperre der Wiedergabe-Ressource,
  die erst eine solche Interaktion löst.
- **Lösung:** die Unit `tvdsp-kick.service`. Sie läuft **nach** CamillaDSP
  (`After=camilladsp.service`, kurze Wartezeit) und ruft einmal `dsptoolkit get-checksum`.
  Der Anstoß wirkt nur, wenn die Wiedergabe schon läuft – deshalb steht er nicht in
  `bin/enable-spdif-in.sh`, das vor CamillaDSP den Eingang einrichtet. Bei einer
  Installation von Hand stößt `install.sh` die Wiedergabe zusätzlich direkt an.
- Fehlt der Ton nach einem Kaltstart doch: einmal
  `sudo /opt/tvdsp/venv/bin/dsptoolkit get-checksum`.

## Doppelton: Register 4547 = 0

Das Profil `dspdac-noautomute` **mischt ab Werk** den Toslink-Direktweg (im DSP) mit dem
verarbeiteten Weg vom Pi. Aus den Cinch-Buchsen kommen dann beide gleichzeitig, leicht
versetzt. Nachweis am Gerät: Nach `systemctl stop camilladsp` bleibt genau ein Ton übrig,
der Direktweg.

```
dsptoolkit write-mem 4547 0
```

- Register 4547 (`muteSPDIF`) schaltet den Direktweg. Die Bedeutung ist **umgekehrt zum
  Namen**: 1 = Direktweg offen (Werkszustand, Doppelton), 0 = Direktweg stumm (nur der
  verarbeitete Ton).
- Der Wert ist flüchtig: Nach Kaltstart oder Profilwechsel steht wieder 1 da. Deshalb
  setzt `bin/enable-spdif-in.sh` ihn bei **jedem Start** und liest ihn mit `read-int`
  zurück.
- **Unveränderliche Regel:** Diese Logik darf nie entfernt, umbenannt oder durch
  `write-reg` ersetzt werden. `install.sh` prüft vor jeder Installation, dass sie im
  mitgelieferten Startskript steht, und bricht sonst ab. Auch Fernbedienung, Neustart,
  Ausschalten und Neuinstallation aus der Weboberfläche ändern den Wert nie als normalen
  Parameter. Nach einem Profil-Flash wird `tvdsp-spdif` neu gestartet und setzt ihn
  wieder.
- **Lehre:** Beim ersten Versuch stand 4547 auf 1, es wurde erneut 1 geschrieben – ohne
  Wirkung, und das Register galt fälschlich als wirkungslos. Steht ein Register schon auf
  dem vermuteten Zielwert, beweist erneutes Schreiben nichts: den Gegenwert testen.
- Am Gerät geprüft und **ohne** Wirkung auf den Doppelton: 4544 auf 0, 4552 auf 1 oder 2,
  4553 auf 1.
- Die Datei `sample_files/dspproj/dspdac-noautomute.dspproj` bei HiFiBerry ist laut
  Hersteller kein echtes SigmaStudio-Projekt, sondern eine umbenannte exportierte XML. Die
  Zuordnung Adresse → Funktion steht in den Metadaten der XML.

## get-checksum hält den DSP-Chip an

- Die HiFiBerry-Software liest den Programmspeicher nur bei angehaltenem Kern
  (hifiberrydsp, `sigmatcp.py`: „Must kill the core to read program memory“) und fragt
  dabei immer frisch an. Weil der Analogausgang hinter dem Chip hängt, ist **jede**
  Abfrage ein hörbarer Aussetzer von einigen Millisekunden.
- Darum nie regelmäßig abfragen. `tvdsp-kick` liest die Prüfsumme beim Systemstart ohnehin
  und legt sie in `config/dsp-profil-pruefsumme` ab; das Backend übernimmt sie von dort
  und liest nur als Rückfall einmal selbst. `install.sh` löscht die Datei vor dem
  Flashen.
- Aussetzer dieser Art findet man, ohne etwas zu ändern, über
  `/proc/asound/card0/pcm0p/sub0/status` (ändert sich `trigger_time`, wurde die Wiedergabe
  neu gestartet), über die Pegel je Block aus CamillaDSP und im Protokoll von
  `tvdsp-sigmatcp` („caching program memory checksum“ = Chip angehalten).

## Weitere Eigenheiten des Chips

- **Der DSP läuft weiter, wenn der Pi aus ist.** Nach `poweroff` liegen weiter 5 V an der
  Karte, der Chip reicht Toslink zum Analogausgang durch. Erst Stecker ziehen setzt ihn
  zurück; auch ein `reboot` erhält den Registerzustand.
- **sigmatcpserver bewahrt Parameter auf.** Er schreibt beim Beenden das Parameter-RAM in
  eine Datei (`/var/lib/hifiberry`) und spielt sie bei jedem Start zurück, auch nach einem
  Kaltstart. Fehlerhafte Schreibversuche überleben so das Stromziehen.
  `dsptoolkit reset` lädt das Programm aus dem EEPROM ausdrücklich ohne diese
  Wiederherstellung.
- **Parameteradressen gelten nur für ein Profil.** Nach einem Profilwechsel gelten
  ausschließlich die Metadaten des neuen Profils. Die Adressen 86 und 4862 gehörten zum
  falschen Werksprofil und werden nie beschrieben.
- **`dsptoolkit get-checksum` identifiziert das laufende Programm** – bei einer unbekannten
  Karte der erste Befehl (siehe Prüfsummen oben).
- **`dsptoolkit get-meta` taugt nicht als Erreichbarkeitsprüfung.** Es liest Metadaten, die
  nur existieren, wenn je ein Profil über dsptoolkit installiert wurde. Bootet der Chip aus
  dem EEPROM, bleibt die Ausgabe leer, obwohl er antwortet. Erreichbarkeit mit einem echten
  Registerzugriff prüfen.
- **`dsptoolkit read-reg` gibt Hexbytes mit Leerzeichen aus** („00 3B“). Vor Vergleichen
  Leerzeichen entfernen und `0x` voranstellen.
- **Steuerregister (ab 0xF000) sind 16 Bit, Parameter-RAM ist 32 Bit.**
- **Die fünf ASRC-Register für den Toslink-Eingang** stammen aus HiFiBerrys Anleitung zur
  Aufnahme von S/PDIF und wirken unabhängig vom geladenen Profil (am Gerät bestätigt).
  Voraussetzung ist nur `dtoverlay=hifiberry-dacplusdsp`.
- **`amixer -c sndrpihifiberry` ist leer** – normal, die Karte hat keine ALSA-Mixerregler.
- **`dsptoolkit` gilt bei HiFiBerry als veraltet**; empfohlen ist die REST-Schnittstelle von
  `sigmatcpserver --rest`. `bin/enable-spdif-in.sh` nutzt noch den alten Weg.
- **Erfolgsmeldungen müssen etwas prüfen.** `dsptoolkit write-reg` liefert auch ins Leere
  den Erfolgscode. `bin/enable-spdif-in.sh` prüft deshalb vorher die Erreichbarkeit und
  liest hinterher zurück.
- Der SigmaTCP-Dienst heißt in diesem Projekt `tvdsp-sigmatcp` (nicht `sigmatcpserver`).
  Alle Units: `tvdsp-sigmatcp`, `tvdsp-spdif`, `camilladsp`, `tvdsp-web`, `tvdsp-kick`,
  `tvdsp-flirc`, `tvdsp-quelle`.
