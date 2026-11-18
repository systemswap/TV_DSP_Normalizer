# Fehlergedächtnis – TV_DSP_Normalizer

Jeder behobene Fehler bekommt hier einen Eintrag – auch kleine, sobald man aus ihnen
etwas lernen kann. Zweck: Wer später an derselben Stelle arbeitet, soll wissen, was schon
schiefging, warum, und warum die Lösung so aussieht – damit derselbe Fehler bei einem Umbau
nicht wiederkommt.

Regeln:

- Zeitlich geordnet nach der Behebung, neue Einträge ans Ende, fortlaufend nummeriert.
- Versionen und Commits aus `git log` nachsehen, nicht schätzen; Unbekanntes so
  hinschreiben („unbekannt“).
- Einträge bleiben stehen, auch wenn der Code inzwischen anders aussieht.
- Ohne Titel und Namen aus dem Testmaterial und ohne persönliche Daten – Testmaterial
  technisch beschreiben.
- Vor Änderungen an einer Stelle hier nach ihr suchen (Datei- oder Funktionsname).

**Zu den Einträgen Nr. 1–21:** Sie stammen aus der Entwicklung vor der Veröffentlichung.
Die Versionsnummern kommen aus dem damaligen Änderungsprotokoll; die zugehörigen Commits
liegen nicht im öffentlichen Repository (die öffentliche Historie beginnt mit 3.3.9).
Hintergründe zur Soundkarte: [hifiberry-dsp.md](hifiberry-dsp.md).

---

## Nr. 1 – HiFiBerry: kein Ton vom Pi, obwohl alles läuft

- **Entdeckt:** Version 1.1.x (vor 1.1.4)
- **Behoben:** Version 1.1.4
- **Wie bemerkt:** Hörtest am Gerät – Toslink-Ton kam an, der verarbeitete Ton nie.
- **Was passiert ist:** CamillaDSP lief mit Ausgangspegel, `speaker-test` lief fehlerfrei,
  aus den Cinch-Buchsen kam aber nur der unverarbeitete Fernsehton oder nichts.
- **Wo aufgetreten:** neue HiFiBerry DAC+ DSP mit Werksprofil.
- **Warum:** Der Analogausgang hängt hinter dem DSP-Chip. Das Werksprofil („Beocreate
  Universal“, Prüfsumme `40FB6C92…`) routet nur Toslink → Analog, nie Pi → Analog.
- **Lösung:** Profil `dspdac-noautomute` (unverändert von HiFiBerry) liegt im Projekt;
  `install.sh` prüft die Prüfsumme (`7F88CAAD8E8A7E4F05731605AE16EF38`) und flasht nur bei
  Abweichung ins EEPROM.
- **Warum diese Lösung:** Ein eigenes SigmaStudio-Profil wäre aufwendig (nur Windows) und
  nicht nötig. Das Profil im Projekt macht die Reparatur unabhängig vom Netz.
- **Nicht tun:** Die Ursache in der Kette (ALSA, CamillaDSP) suchen, bevor die Prüfsumme
  des Profils feststeht. Adressen des alten Profils (86, 4862) beschreiben.
- **Im Code:** `dsp/dspdac-noautomute.xml`, `install.sh` (Profilprüfung),
  `backend/app.py` (`DSP_PROFIL_SOLL`, `dsp_profil_pruefen`).
- **Abgesichert durch:** Prüfsummenvergleich in `install.sh` und Anzeige in der
  Weboberfläche; kein automatischer Test, weil Hardware nötig.
- **Verweise:** [hifiberry-dsp.md](hifiberry-dsp.md), „Das DSP-Profil ist die halbe Karte“.

## Nr. 2 – Toslink-Eingang: Freischaltung brach vor dem Schreiben ab

- **Entdeckt:** Version 1.1.1
- **Behoben:** Version 1.1.3 (genaue Version unbekannt, vor 1.1.4)
- **Wie bemerkt:** Kein Eingangssignal am Gerät; im Protokoll stand gleichzeitig eine
  Erfolgsmeldung und „did not detect ADAU14xx DSP“.
- **Was passiert ist:** Der Toslink-Eingang blieb tot.
- **Wo aufgetreten:** `bin/enable-spdif-in.sh` beim Systemstart, DSP aus dem EEPROM gebootet.
- **Warum:** Die Erreichbarkeit wurde mit `dsptoolkit get-meta` geprüft. Das liefert nur
  Metadaten, wenn je ein Profil über dsptoolkit installiert wurde – sonst leer, obwohl der
  Chip antwortet. Umgekehrt meldet `write-reg` auch ins Leere Erfolg.
- **Lösung:** Erreichbarkeit mit einem echten Registerzugriff prüfen, nach dem Schreiben
  zurücklesen, Erfolg nur nach bestandener Rückleseprüfung melden.
- **Warum diese Lösung:** Nur ein Registerzugriff beweist, dass der Chip antwortet.
- **Nicht tun:** `get-meta` als Erreichbarkeitsprüfung; Erfolgsmeldungen ohne Prüfung.
- **Im Code:** `bin/enable-spdif-in.sh`.
- **Abgesichert durch:** Rückleseprüfung im Skript selbst; kein automatischer Test
  (Hardware nötig).
- **Verweise:** [hifiberry-dsp.md](hifiberry-dsp.md), „Weitere Eigenheiten des Chips“.

## Nr. 3 – Kartenerkennung: Gerätename mit eckigen Klammern

- **Entdeckt:** Version 1.1.x (unbekannt)
- **Behoben:** Version 1.1.2
- **Wie bemerkt:** CamillaDSP meldete „Cannot get card index for [sndrpihifiberry]“.
- **Was passiert ist:** Kein Ton, CamillaDSP startete im Sekundentakt neu.
- **Wo aufgetreten:** `bin/detect-card.sh` mit dem Overlay `hifiberry-dacplusdsp`.
- **Warum:** In `/proc/asound/cards` steht das Kurzfeld als `[sndrpihifiberry]:`. Ungefiltert
  übernommen entstand `hw:[sndrpihifiberry]:,0` – gültiges YAML, `camilladsp --check`
  schlägt nicht an, der Fehler zeigt sich erst beim Öffnen des Geräts.
- **Lösung:** Name gegen `^[A-Za-z0-9_-]+$` und gegen die Existenz von
  `/proc/asound/<name>` prüfen. `install.sh` ersetzt alte, fehlerhafte Einträge.
- **Warum diese Lösung:** Die Prüfung gegen das echte Verzeichnis fängt jede Form von Rest ab.
- **Nicht tun:** Kartennamen ungeprüft in `tv.yml` schreiben.
- **Im Code:** `bin/detect-card.sh`, `install.sh`.
- **Abgesichert durch:** kein automatischer Test.
- **Verweise:** Nr. 18.

## Nr. 4 – HiFiBerry: Doppelton aus Originalton und verarbeitetem Ton

- **Entdeckt:** Version 1.1.4
- **Behoben:** Version 1.1.5
- **Wie bemerkt:** Hörtest – zwei leicht versetzte Töne. Nach `systemctl stop camilladsp`
  blieb genau einer übrig.
- **Was passiert ist:** Aus den Cinch-Buchsen kam der unbearbeitete Toslink-Ton zusammen
  mit dem bearbeiteten.
- **Wo aufgetreten:** Profil `dspdac-noautomute`, ab Werk.
- **Warum:** Das Profil mischt den Toslink-Direktweg im DSP mit dem Weg vom Pi.
- **Lösung:** `dsptoolkit write-mem 4547 0` bei jedem Start (Wert ist flüchtig), mit
  Rücklesen. `install.sh` verweigert die Installation, wenn diese Logik fehlt.
- **Warum diese Lösung:** Andere Register (4544, 4552, 4553) wirkten am Gerät nicht. Ein
  eigenes Profil wäre aufwendig. Ein Toslink-Cinch-Wandler wäre ein Umweg mit Zusatzgerät.
- **Nicht tun:** Die Logik entfernen, umbenennen oder durch `write-reg` ersetzen (schreibt
  nur 2 Bytes). Ein Register, das schon auf dem vermuteten Zielwert steht, erneut mit
  diesem Wert beschreiben und daraus „wirkungslos“ schließen – die 1 bedeutet hier „offen“.
- **Im Code:** `bin/enable-spdif-in.sh` (`MUTE_ADR=4547`), `install.sh` (Schutzprüfung).
- **Abgesichert durch:** Abbruch in `install.sh`, wenn die Schutzlogik im Startskript fehlt.
- **Verweise:** [hifiberry-dsp.md](hifiberry-dsp.md), „Doppelton: Register 4547 = 0“.

## Nr. 5 – HiFiBerry: kein Ton nach einem Kaltstart

- **Entdeckt:** Version 1.1.5
- **Behoben:** Version 1.1.6 (Umgehung)
- **Wie bemerkt:** Am Gerät nach Netztrennung; erst ein von Hand aufgerufenes
  `dsptoolkit get-checksum` brachte den Ton.
- **Was passiert ist:** Minutenlang kein Analogton, obwohl die ganze Kette korrekt lief.
- **Wo aufgetreten:** nur nach echtem Kaltstart, nicht nach `reboot`.
- **Warum:** Nicht abschließend geklärt. Ein programmweiter Zugriff (Chip kurz anhalten)
  löst offenbar eine Sperre der Wiedergabe-Ressource; ein einzelner Registerzugriff nicht.
- **Lösung:** Unit `tvdsp-kick.service` ruft nach CamillaDSP einmal `get-checksum`.
- **Warum diese Lösung:** Belegt reproduzierbar (Registerzugriff → kein Ton, get-checksum →
  Ton). Der Anstoß wirkt nur bei laufender Wiedergabe, deshalb eine eigene Unit nach
  CamillaDSP und nicht in `enable-spdif-in.sh`.
- **Nicht tun:** Den Anstoß in `enable-spdif-in.sh` verlegen (läuft vor CamillaDSP).
- **Im Code:** `install.sh` (Unit `tvdsp-kick`), `bin/enable-spdif-in.sh` (Hinweis).
- **Abgesichert durch:** kein automatischer Test (Kaltstart nötig).
- **Verweise:** [hifiberry-dsp.md](hifiberry-dsp.md), „Kaltstart-Stille“; Nr. 15.

## Nr. 6 – Klang: Verzerrung durch den „weichen“ Limiter

- **Entdeckt:** Version 1.9.6
- **Behoben:** Version 1.9.7
- **Wie bemerkt:** Hörtest – „ausgefranster“ Ton; danach offline gegen den Quelltext von
  CamillaDSP 4.1.3 und eine Testaufnahme gemessen.
- **Was passiert ist:** Dauerhafte, hörbare Verzerrung, besonders bei hohem Pegel.
- **Wo aufgetreten:** finaler Limiter und Kompressor mit `soft_clip: true`, dazu ein
  digitaler Ausgangsboost vor dem Limiter.
- **Warum:** CamillaDSPs Soft-Clip ist ein gedächtnisloser kubischer Waveshaper, kein
  Limiter: Er krümmt jedes Sample schon ab etwa −6 dBFS (bei −2 dBFS rund 4 % Klirr). Der
  Boost trieb das Signal genau in diesen Bereich.
- **Lösung:** `soft_clip: false` überall; Pegel höchstens 0 dB (mehr Lautstärke kommt vom
  Verstärker).
- **Warum diese Lösung:** Hartes Clipping ist bis zur Grenze vollständig transparent und
  greift im Normalbetrieb praktisch nie.
- **Nicht tun:** `soft_clip` wieder einschalten; positiven digitalen Pegel vor dem Limiter.
- **Im Code:** `config/tv.yml`, `backend/app.py` (Pipeline-Aufbau, Grenzen des Pegels).
- **Abgesichert durch:** `werkzeuge/funktest.py` (Limiter an echter CamillaDSP gemessen).

## Nr. 7 – Fernbedienung: Anlernen des FLIRC-Sticks scheiterte

- **Entdeckt:** Version 1.9.16
- **Behoben:** Version 1.9.17 und 1.9.18
- **Wie bemerkt:** Meldung „Wrong response: 'getversion' != 'ir_state'“; danach wurde der
  Tastendruck gar nicht mehr erkannt.
- **Was passiert ist:** Anlernen brach ab, nichts wurde im Stick gespeichert.
- **Wo aufgetreten:** IR-Reiter, während `flirc_util record` auf die Taste wartete.
- **Warum:** (1) Der Stick verträgt nur einen `flirc_util`-Zugriff gleichzeitig; der
  Status-Poll startete parallel weitere. (2) Der evdev-Lauscher hielt den Stick exklusiv
  (grab) auch während des Anlernens.
- **Lösung:** Alle `flirc_util`-Aufrufe über eine Sperre; während record/delete/format keine
  Statusabfrage an die Hardware; der Lauscher gibt das Gerät frei, solange
  `config/flirc-learning` existiert (höchstens 60 s alt).
- **Warum diese Lösung:** Serialisieren ist der einzige sichere Weg bei einem Gerät, das
  nur einen Partner kennt.
- **Nicht tun:** `flirc_util` parallel aufrufen; den Status-Poll bei jedem Aufruf die
  Hardware fragen lassen (Cache 30 s).
- **Im Code:** `backend/app.py` (`_flirc_run`, `_flirc_exec`), `backend/flirc_listener.py`.
- **Abgesichert durch:** kein automatischer Test (Stick nötig).

## Nr. 8 – Sicherheit: Ton nach Installation oder Neustart extrem laut

- **Entdeckt:** Version 1.9.22
- **Behoben:** Version 1.9.23
- **Wie bemerkt:** Am Gerät – laut, bis man den Pegelregler kurz bewegte.
- **Was passiert ist:** CamillaDSP kam mit 0 dB hoch.
- **Wo aufgetreten:** Dienststart von CamillaDSP, Verbindungsaufbau im Backend.
- **Warum:** (1) CamillaDSP 4.1.3 kennt kein `initial_volume` in der Konfiguration; die
  Startlautstärke geht nur per Kommandozeile, und `-g -35` wird falsch gelesen („-3“ als
  Option). (2) Das Backend setzte den Pegel direkt nach `set_active`, das asynchron lädt –
  der Aufruf verpuffte still.
- **Lösung:** Dienst startet mit `--gain=-35` (Gleichheitszeichen); das Backend wartet auf
  die aktive Konfiguration, setzt den Pegel und liest ihn zurück. Schlägt das fehl, bleibt
  der leise Startwert.
- **Warum diese Lösung:** Leiser Start ist immer sicher; der gespeicherte Pegel kommt erst
  nach bestätigter Konfiguration.
- **Nicht tun:** Startlautstärke der Konfigurationsdatei überlassen; Pegel unmittelbar nach
  `set_active` setzen, ohne zu warten und zurückzulesen.
- **Im Code:** `install.sh` (`ExecStart` von CamillaDSP), `backend/app.py` (`verbinder`).
- **Abgesichert durch:** `werkzeuge/apitest.py` (Aux2-Pegel-Test, Main-Freigabe).
- **Verweise:** Nr. 12, 13.

## Nr. 9 – Fernbedienung: Tastendrücke kamen nie im Backend an

- **Entdeckt:** Version 1.9.23
- **Behoben:** Version 1.9.24
- **Wie bemerkt:** Protokoll von `tvdsp-web`: HTTP 500 „unexpected keyword argument
  'aktion'“ bei jedem Tastendruck.
- **Was passiert ist:** Lauter/Leiser/Stumm wirkten nie, auch bei korrekt angelerntem Code.
- **Wo aufgetreten:** Endpunkt `/api/flirc/action/<aktion>`.
- **Warum:** Beim Umbau hing der Route-Dekorator an einer falschen Funktion mit anderer
  Signatur.
- **Lösung:** Dekorator wieder an `api_flirc_action`; der Lauscher schreibt jedes
  Tastenereignis ins Journal.
- **Warum diese Lösung:** Direkte Ursache; das Journal macht künftig sichtbar, ob der Stick
  überhaupt sendet.
- **Nicht tun:** Route-Dekoratoren beim Umbauen verschieben, ohne die Endpunkte mit dem
  Flask-Testclient aufzurufen.
- **Im Code:** `backend/app.py` (`api_flirc_action`), `backend/flirc_listener.py`.
- **Abgesichert durch:** `werkzeuge/apitest.py` (Teil 3.3.7 ruft `/api/flirc/action/…` auf).

## Nr. 10 – Fernbedienung: Stick geleert, flirc_util hing nach Zeitüberschreitung

- **Entdeckt:** Version 1.9.19 bis 1.9.24
- **Behoben:** Version 1.9.25 und 1.9.26
- **Wie bemerkt:** Eine zuvor funktionierende Taste war plötzlich leer; weitere Aufrufe von
  `flirc_util` scheiterten.
- **Was passiert ist:** (1) Vor jedem Anlernen wurde die vorhandene Zuordnung per
  `delete_index` gelöscht; lief `record` danach in die Zeitüberschreitung, blieb die Taste
  leer. (2) Bei Zeitüberschreitung beendete `subprocess.run(timeout)` nur `sudo`, `flirc_util`
  lief weiter und hielt den Stick im Aufnahmemodus.
- **Wo aufgetreten:** IR-Reiter, Anlernen.
- **Warum:** Automatisches Löschen ohne Rückfrage; Prozessgruppe nicht beendet.
- **Lösung:** Nie automatisch löschen (delete/format nur auf ausdrückliche Bestätigung);
  `flirc_util` per `Popen` in eigener Prozesssitzung, bei Zeitüberschreitung die ganze
  Gruppe beenden (`killpg`). Rohausgabe von `flirc_util` in der Oberfläche zeigen.
- **Warum diese Lösung:** Ein leerer FLIRC-Stick sendet gar nichts – Software am Pi kann ihm
  nichts ablauschen. Gelöschtes lässt sich also nicht von hier wiederherstellen.
- **Nicht tun:** `subprocess.run(timeout=…)` für `sudo flirc_util`; automatisch löschen.
  `flirc_util settings` mit fester Spaltenzahl lesen (Gen2-Firmware: Tastenname ist das
  letzte Feld).
- **Im Code:** `backend/app.py` (`_flirc_exec`, `_flirc_pairs`).
- **Abgesichert durch:** kein automatischer Test (Stick nötig).

## Nr. 11 – Fernbedienung: Zuweisungen überlebten keinen Neustart

- **Entdeckt:** Version 1.9.26
- **Behoben:** Version 1.9.28
- **Wie bemerkt:** Nach Neustart standen alle Zuweisungen auf „nicht zugewiesen“.
- **Was passiert ist:** Die Zuordnung Taste → Aktion ging verloren.
- **Wo aufgetreten:** Laden von `settings.json`.
- **Warum:** Beim Laden übernimmt das Backend nur Schlüssel, die in den Standardwerten von
  `state` stehen. `ir_keys` fehlte dort nach einem Umbau.
- **Lösung:** `ir_keys` wieder in die Standardwerte.
- **Warum diese Lösung:** Der Filter selbst ist gewollt (verwirft veraltete Schlüssel).
- **Nicht tun:** Einen neuen gespeicherten Schlüssel einführen, ohne ihn in die
  Standardwerte von `state` einzutragen.
- **Im Code:** `backend/app.py` (`state`, Laden der Einstellungen).
- **Abgesichert durch:** kein eigener Test.

## Nr. 12 – Regelung: Die Decke griff im Betrieb nie

- **Entdeckt:** Version 2.6.x
- **Behoben:** Version 2.7.0
- **Wie bemerkt:** Live-Daten über 30 min: Der Ausgang lag 55 % der Zeit über der Oberkante;
  dieselbe Konfiguration klemmte offline hart.
- **Was passiert ist:** Laute Szenen kamen ungebremst durch.
- **Wo aufgetreten:** Pegelregler lag auf der Main-Lautstärke von CamillaDSP.
- **Warum:** CamillaDSP wendet die Main-Lautstärke **vor** der Pipeline an. Bei Pegel −25 dB
  sah der Detektor der Decke das Signal 25 dB zu leise; die Schwelle war unerreichbar.
  Zweiter Befund: Der Kompressor-Detektor misst |x| je Sample in dB (kein RMS) und liest auf
  TV-Material rund 3,6 dB heißer als der 400-ms-RMS.
- **Lösung:** Der Pegelregler ist ein Volume-Filter `ausgang` auf Aux2 hinter der Decke,
  vor dem Limiter. Main ist im Betrieb 0 dB und nur noch Startschutz und Stumm.
  Detektor-Ausgleich `BAND_DECKE_DETEKTOR_DB = +3,6 dB`.
- **Warum diese Lösung:** So sieht jeder Detektor das Signal unabhängig vom Pegel.
- **Nicht tun:** Pegel wieder auf Main legen; Main freigeben, ohne dass `ausgang` in der
  aktiven Pipeline steht.
- **Im Code:** `config/tv.yml`, `backend/app.py` (`set_gesamtpegel`, `lies_gesamtpegel`,
  `ausgang_in_pipeline`, `band_decke_dbfs`), `install.sh` (`--gain2=-35`).
- **Abgesichert durch:** `werkzeuge/funktest.py` (misst die Wirkung an der echten
  CamillaDSP), `werkzeuge/apitest.py` (Aux2-Pegel-Test).

## Nr. 13 – Regelung: Main-Freigabe beim Kaltstart, Decke sah die Loudness-Kurve nicht

- **Entdeckt:** Version 2.7.0
- **Behoben:** Version 2.8.0
- **Wie bemerkt:** Nachprüfung der CamillaDSP-Anbindung, offline an TV-Material.
- **Was passiert ist:** (1) Der Ton konnte nach dem Start 25–35 dB zu leise bleiben.
  (2) Bei starker Absenkung lag der Ausgang um den Anteil der Loudness-Kompensation über
  der Oberkante.
- **Wo aufgetreten:** `verbinder()` beim Kaltstart; Detektorkanal der Decke.
- **Warum:** (1) `set_active()` lädt asynchron; die einmalige Prüfung auf den Filter
  `ausgang` sah noch die alte Pipeline. (2) Der Detektor entstand vor dem Aux3-Parallelpfad.
- **Lösung:** Bis zu 5 s warten, bis die neue Pipeline aktiv ist, erst dann Main freigeben;
  Detektor in `band_zusammen` aus den fertigen Nutzkanälen bilden (0+3−5 bzw. 1+4−6, je
  −6 dB).
- **Warum diese Lösung:** Offline mit voller Kurve: Anteil über der Decke 22,0 % → 11,8 %,
  ohne Kurve unverändert.
- **Nicht tun:** Nach `set_active` einmalig prüfen statt warten; Detektor aus `band_expand`.
- **Im Code:** `backend/app.py` (`verbinder`, `band_anwenden`, Mixer `band_zusammen`).
- **Abgesichert durch:** `werkzeuge/apitest.py` (Teil 2.8.0).

## Nr. 14 – Regelung: Neustart von CamillaDSP blieb unbemerkt

- **Entdeckt:** Version 2.8.0 (beim Ausrollen)
- **Behoben:** Version 2.8.1
- **Wie bemerkt:** Nach `install.sh` kein Ton, Eingang zeigte Pegel, Ausgang nicht.
- **Was passiert ist:** CamillaDSP lief mit der Kaltstart-`tv.yml` und den leisen
  Startwerten; das Backend stellte nichts wieder her. Zusätzlich übernahm die
  Pegel-Rückmeldung den Startwert −35 dB als gespeicherten Pegel.
- **Wo aufgetreten:** `install.sh` startet CamillaDSP nach `tvdsp-web` neu.
- **Warum:** Die damalige Bibliothek baute die Websocket-Verbindung still neu auf,
  `is_connected()` blieb wahr; die Wiederherstellung hing aber an diesem Zweig.
- **Lösung:** `camilladsp_neu_gestartet()` vergleicht die Schwelle der Decke in der aktiven
  Konfiguration mit der zuletzt gesendeten; bei Abweichung stellt
  `pipeline_und_pegel_herstellen()` Pipeline, Aux2, Main-Freigabe her und schreibt Aux1/Aux3
  neu. Pegel nur zurücklesen, wenn Main auf 0 dB steht.
- **Warum diese Lösung:** Die Schwelle ist ein eindeutiger Fingerabdruck der selbst
  gesetzten Pipeline.
- **Nicht tun:** Wiederherstellung nur an den Verbindungsaufbau hängen.
- **Im Code:** `backend/app.py` (`camilladsp_neu_gestartet`, `pipeline_und_pegel_herstellen`,
  `neustart_verdacht`, `verbinder`).
- **Abgesichert durch:** `werkzeuge/apitest.py` (Teile 2.8.0, 2.8.7, 2.8.8).
- **Verweise:** Nr. 16 (heute billigere Wache über Main).

## Nr. 15 – Ton: Knacken alle 10 Minuten

- **Entdeckt:** Version 2.8.6
- **Behoben:** Version 2.8.7
- **Wie bemerkt:** Gelegentliche Aussetzer von wenigen Millisekunden, im Diagramm
  unsichtbar; das Protokoll von `tvdsp-sigmatcp` zeigte Abfragen exakt alle 10 Minuten, eine
  fiel auf den gehörten Knacks.
- **Was passiert ist:** Kurzes Knacken.
- **Wo aufgetreten:** DSP-Profil-Wache im Backend.
- **Warum:** `dsptoolkit get-checksum` hält den DSP-Chip an, um sein Programm zu lesen; der
  Analogausgang hängt dahinter.
- **Lösung:** Profil nur einmal pro Systemstart prüfen: `tvdsp-kick` legt die Prüfsumme in
  `config/dsp-profil-pruefsumme` ab, das Backend übernimmt sie. Die Neustart-Erkennung
  vergleicht seitdem mit der tatsächlich gesendeten Schwelle (keine Fehlalarme im Bypass)
  und handelt erst beim zweiten Treffer in Folge.
- **Warum diese Lösung:** Der Zugriff beim Start ist ohnehin nötig (Nr. 5).
- **Nicht tun:** `get-checksum` periodisch aufrufen.
- **Im Code:** `backend/app.py` (`dsp_profil_aus_marker`, `dsp_profil_pruefen`), `install.sh`.
- **Abgesichert durch:** `werkzeuge/apitest.py` (Teil 2.8.7).
- **Verweise:** [hifiberry-dsp.md](hifiberry-dsp.md), „get-checksum hält den DSP-Chip an“.

## Nr. 16 – Last: Backend brauchte mehr Rechenzeit als CamillaDSP

- **Entdeckt:** Version 2.8.7 (Messung)
- **Behoben:** Version 2.8.8
- **Wie bemerkt:** Messung am Gerät (CPU je Prozess über 60 s, Profiler): Backend 23 % eines
  Kerns.
- **Was passiert ist:** Hohe Last; die 50-Hz-Regelung stand alle 2 s rund 0,12 s still.
- **Wo aufgetreten:** Neustart-Wache und Regelschleife.
- **Warum:** Die aktive Konfiguration zu holen kostete rund 120 ms (YAML-Text, langsamer
  Leser) – und geschah 1,5-mal pro Sekunde, teils unter der Sperre. Dazu 2.900
  Schreibvorgänge am Tag auf die SD-Karte und ein volles Journal.
- **Lösung:** Neustart-Wache über die Main-Lautstärke (0 dB im Betrieb, −35 dB nach Neustart),
  Vergleich der Konfiguration nur noch einmal pro Minute; Kalibrierung höchstens alle 10 min
  speichern; CamillaDSP mit `--loglevel=error`, Journal begrenzt.
- **Warum diese Lösung:** Pegel abzufragen kostet 0,2–0,3 ms.
- **Nicht tun:** Die aktive Konfiguration im Regeltakt holen.
- **Im Code:** `backend/app.py` (`neustart_verdacht`, `GERAET_NACHLESEN_SEK`,
  `KALIB_SPEICHERN_SEK`).
- **Abgesichert durch:** `werkzeuge/apitest.py` (Teil 2.8.8).

## Nr. 17 – Messung lief ohne EQ-Spiegel

- **Entdeckt:** Version 2.8.x
- **Behoben:** Version 2.9.0
- **Wie bemerkt:** Prüfung der Lautheitsmessung nach dem Start.
- **Was passiert ist:** Startete das Backend vor CamillaDSP, maß die Lautheitsmessung ohne
  die Klangkurve – bis zur nächsten EQ-Änderung.
- **Wo aufgetreten:** Start der ffmpeg-Messung.
- **Warum:** Die Filterkette für den Spiegel kam aus der noch nicht verfügbaren
  Konfiguration.
- **Lösung:** `r128_filterkette()` merkt sich „flach“; `verbinder()` startet die Messung nach
  der Verbindung einmal mit EQ neu.
- **Nicht tun:** Annehmen, dass CamillaDSP beim Start des Backends schon bereit ist.
- **Im Code:** `backend/app.py` (`r128_filterkette`, `verbinder`).
- **Abgesichert durch:** `werkzeuge/apitest.py` (Teil 2.9.0).

## Nr. 18 – Kartenerkennung: CamillaDSP startete mit zweiter Soundkarte nicht

- **Entdeckt:** Version 2.9.6
- **Behoben:** Version 2.9.7
- **Wie bemerkt:** Nach Neustart mit gestecktem USB-Empfänger kein Ton, Weboberfläche ohne
  Gerät.
- **Was passiert ist:** `detect-card.sh` brach ab.
- **Wo aufgetreten:** zwei Soundkarten (HiFiBerry und USB-Empfänger).
- **Warum:** Beim Bereinigen wurden mit `[:space:]` auch die Zeilenumbrüche entfernt; alle
  Kartennamen klebten zu einem zusammen. Mit nur einer Karte fiel das nie auf.
- **Lösung:** `namen_aus()` entfernt nur Leerzeichen und Tabs innerhalb der Zeile.
- **Nicht tun:** `[:space:]` zum Bereinigen zeilenweiser Daten.
- **Im Code:** `bin/detect-card.sh` (`namen_aus`).
- **Abgesichert durch:** kein automatischer Test.

## Nr. 19 – USB-Eingang: kaputte Dolby-Frames wurden als Schnarren hörbar

- **Entdeckt:** Version 3.0.0
- **Behoben:** Version 3.3.0
- **Wie bemerkt:** Hörtest („Verzerrung“) mit einem USB-Empfänger, der Samples verliert; der
  Test fand 0 von 1000 künstlichen Lücken.
- **Was passiert ist:** Beschädigte Dolby-Digital-Frames kamen als verzerrter Ton heraus.
- **Wo aufgetreten:** Decoder in `bin/tvdsp-quelle.py`.
- **Warum:** `err_detect=crccheck` allein erkennt die Prüfsumme, ffmpeg verdeckt den Fehler
  aber (letzten Block wiederholen) und gibt den Frame trotzdem aus.
- **Lösung:** `err_detect=crccheck+explode`: Ein kaputter Frame wird verworfen und durch
  32 ms Stille mit 5-ms-Blenden ersetzt.
- **Warum diese Lösung:** Strengere Prüfungen (`bitstream`, `buffer`) verwarfen auch heile
  Frames (290 von 507). An den Testdateien keine Fehlalarme.
- **Nicht tun:** `crccheck` ohne `explode`; Bitstrom-Blöcke als PCM ausgeben (an der Präambel
  sofort umschalten, auch wenn der Burst unvollständig ist – sonst volles Rauschen).
- **Im Code:** `bin/tvdsp-quelle.py` (Decoder-Optionen, `_decodiere`).
- **Abgesichert durch:** `werkzeuge/quelletest.py` (verlorene Samples → Stille, nie Rauschen).

## Nr. 20 – USB-Eingang: Aussetzer, wenn der Pi kurz beschäftigt war

- **Entdeckt:** Version 3.3.2
- **Behoben:** Version 3.3.3 bis 3.3.6
- **Wie bemerkt:** Hörbare Verzerrung an bestimmten Stellen; ein Mitschnitt des rohen
  Eingangs war fehlerfrei.
- **Was passiert ist:** Der Loopback lief leer.
- **Wo aufgetreten:** Start anderer Prozesse (jede EQ-Änderung startet ffmpeg neu, der
  IR-Reiter ruft `flirc_util`), Schreiblast auf der SD-Karte.
- **Warum:** Der Leser bekam 40–100 ms keine Rechenzeit, der Lesepuffer (85 ms) lief über.
  Später blockierte auch das Schreiben der Statusdatei (rename auf der SD-Karte) die
  Tonschleife.
- **Lösung:** Echtzeit-Priorität (`CPUSchedulingPolicy=fifo`, Priorität 20), Lesepuffer
  341 ms ohne Zusatzlatenz; Rückstand über 50 ms über Soll sofort verwerfen; Tonschleife
  fasst keine Dateien an (eigener Verwalter-Faden); Statusdatei in `/run/tvdsp` (RAM).
- **Warum diese Lösung:** Die Taktregelung hält die Verzögerung bei 21 ms; der große Puffer
  wird nur bei Bedarf genutzt.
- **Nicht tun:** Dateizugriffe in der Tonschleife; Pipes über `arecord`/`aplay` (puffern
  unsichtbar rund 340 ms); „PCM Rate Shift“ des Loopbacks verstellen.
- **Im Code:** `bin/tvdsp-quelle.py`, `install.sh` (Unit `tvdsp-quelle`).
- **Abgesichert durch:** Lasttest am Gerät (je 6 Python-Starts und 2 ffmpeg, 3 Runden, kein
  Überlauf); `werkzeuge/quelletest.py` prüft, dass die Latenz nicht wächst.

## Nr. 21 – miniDSP: USB-Eingang nach Neustart stumm

- **Entdeckt:** Version 3.3.0
- **Behoben:** Version 3.3.8 (3.3.1 war wirkungslos)
- **Wie bemerkt:** Nach Neustart des Pi fiel TVDSP auf die HiFiBerry zurück; jedes Lesen
  scheiterte mit „Input/output error“ (dmesg: `retire_capture_urb`).
- **Was passiert ist:** Kein Ton über den miniDSP MCHStreamer.
- **Wo aufgetreten:** Taktquelle „miniDSP TOSLINK Clock“ direkt nach dem Systemstart.
- **Warum:** Blankes Umschalten intern → Toslink löste den Zustand nicht. Am Gerät belegt:
  Erst Lesen mit internem Takt, danach Toslink.
- **Lösung:** `takt_setzen(…, neu_einrasten=True)` beim Dienststart und nach jedem Abbruch:
  intern stellen, kurz lesen, dann Toslink.
- **Warum diese Lösung:** Nur sie half am echten Gerät. Eine USB-Neuanmeldung stellt den
  Fehler nicht nach – nur ein echter Neustart prüft das.
- **Nicht tun:** Annehmen, dass Umschalten allein den Takt einrastet.
- **Im Code:** `bin/tvdsp-quelle.py` (`takt_setzen`, `lauf`).
- **Abgesichert durch:** Neustart am Gerät; kein automatischer Test (Hardware nötig).
