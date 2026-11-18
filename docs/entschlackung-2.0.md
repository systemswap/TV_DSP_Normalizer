# Entschlackung auf Band-only (Plan fuer 2.0.0 - UMGESETZT in 2.0.0)

Stand der Analyse: 1.13.4. Backend 4456 Zeilen, Oberflaeche 2470, install.sh 756.
Ziel: Regelung ausschliesslich Band-Zweig. Reiter: Klang (mit Pegel), EQ, Band,
Settings, IR, Reboot. Keine Modi, kein Tag/Abend/Nacht, keine Nachtautomatik.

## A. Bleibt (Band haengt daran oder quellenunabhaengig)

Messung und Zustand
- ffmpeg/ebur128-Leser `r128_leser`, `r128_filterkette` (EQ-Spiegelung),
  `r128_eq_neu_laden`, `r128_frisch`, `R128_DEV`, `r128_frisch_max` (fest 0,8 s)
- Automatische Kalibrierung LUFS<->dBFS im 3-s-Fenster (`kalibrierung`,
  `kalibrierung_schnell`, Median, 0,1 dB/s Nachfuehrung) - die Decke rechnet daraus
- Stille-Gate (-55 dB), Audio-Idle (1 Hz, ffmpeg pausiert), Lueckenerkennung
  >= 0,4 s (`SCHNELL_EINZUG_GATE` -> umbenennen `LUECKE_GATE`)
- Regeltakt 20 ms, `REGEL_DT_MAX`, `GAIN_WRITE_EPS`, `AGC_MAX`
- Aux1-Fader `set_agc_gain`/`lies_agc_gain`; Hauptlautstaerke `set_gesamtpegel`
  (nur Main), Mute; `verbinder()` mit sicherer Startlautstaerke und verifizierter
  Wiederherstellung
- LRA-/Integrated-Anzeige (nur Anzeige), CPU-Last, Latenz/Chunksize, Diagnose
- Lautheitsverlauf (`verlauf_*`, /api/verlauf)
- Band komplett: `band_*`, Decke, Vorlauf, Loudness-Aux3, VAD, /api/band/*
- EQ: `read_eq`/`set_eq`, `spread`, /api/eq, Reiter EQ
- FLIRC/IR komplett (Backend, Listener, Units, sudoers/udev)
- Aufnahme (TV-Mitschnitt) und System (Reboot/Poweroff)
- DSP-Invariante 4547=0, tvdsp-spdif, tvdsp-kick, tvdsp-sigmatcp

## B. Weg

Regelgesetze und Schutz
- RMS-S-Kurve: `rms_mittelung_sek`, `rms_fenster_samples`, `rms_regelzeit`,
  `rms_beschleunigung`, `RMS_DEFAULTS`, `RMS_DEFAULT_ZIEL/RAMPE`, Totband, `tempo`,
  `_rms_roh_fenster` nur noch als 3-s-Kalibrierfenster
- R128-Huelle: `r128_steuer_lufs`, `r128_ziel_fenster`, `r128_max_anhebung_db`,
  `r128_schnell_einzug_sek`, `r128_gefiltertes_ziel`, `r128_huellenschritt`,
  `R128_DEFAULTS`, `SCHNELL_EINZUG_TAU_*`, `EINZUG_M_STABIL`, Einzug-Zustand
- 35-ms-Schutz: `dynamikschutz_ziel`, `dynamikschutz_huellenschritt`,
  `schnelle_level_35ms`, `_gueltige_leveldaten`, `FAST_RMS_SEK`, `SCHUTZ_MAX`,
  `PEAK_ATTACK_MIN`, `SCHUTZ_RELEASE_MIN`, `pipeline_vorlauf_db`,
  `_eq_gesamt_boost_cached`, `eq_gesamt_boost_db`, `schutz_gain`, Peak-Trigger
- Klassischer Kompressor: `dynamik_werte`, `DYNAMIK_TABELLE`, `RMS_MAKEUP_TRIM`,
  `read_kompressor`, /api/kompressor, Prozessor `kompressor` und 3-Kanal-Mixer
  `expand`/`collapse` in tv.yml

Modi und Presets
- `BUILTIN`, presets.json, `load_presets`, `apply_preset`, /api/modus,
  /api/presets, /api/preset/<name>[/standard], `nachtautomatik()`-Thread,
  state `modus`, `nachtautomatik`, `nacht_von`, `nacht_bis`
- `LIMITS`: dynamik, attack, release, ziel, rampe, spitze (bleiben: eq, volume)
- Oberflaeche: Panels modi, rms, r128; Klang-Karte "Modus"; Schalter Nachtautomatik;
  "Bearbeiteter Modus" (editSel*), `zeichneModi`, `ladeFelder`, `speichereModus`,
  `zeigeAbgeleitet`, RMS/R128-Regler samt Reset; /api/rms/*, /api/r128/*

Quellenumschaltung
- state `quelle`, quickSource/sourceBtn, `aktive_grundkonfiguration`,
  `_band_grund`, `band_pipeline_aktiv`, Umschalt-Logik in /api/schalter,
  `messung.regelquelle` (nur noch Band-Zustaende), Kopfkachel "RMS"
  (Kacheln: Eingang M, Short-Term, Regelung = gehoert, Spitze)

Aux2-Ausgangsboost (seit 1.9.7 immer 0 dB)
- Filter `ausgangspegel`, `set_ausgang_boost`, `lies_ausgang_boost`,
  `pegel_aufteilen` (Main direkt), `--gain2=0` im ExecStart, install.sh-Block
  AUSGANG_MIGRIEREN (1.9.4)

## C. Neu noetig

- EQ dauerhaft in settings.json (`eq`), /api/eq schreibt sie, Start setzt sie.
  Loest den EQ-Reset endgueltig (Kurve hing bisher am Modus).
- tv.yml = Band-Pipeline als Datei (Vorpegel, Aux1, EQ, Loudness-Pfad, Sidechain,
  Vorlauf, Decke, Limiter). Kaltstart korrekt ohne Aufbau zur Laufzeit; Backend
  setzt beim Start nur EQ, Decken-Schwelle, Vorlauf an/aus, Aux3 aus.
  install.sh: alte tv.yml sichern, neue nach `camilladsp --check` einsetzen,
  ExecStart `--gain=-35 --gain3=-100`. Erstmalig install.sh statt deploy.sh.
- Bypass neu: Decke 1:1, Aux1 0, Aux3 aus, EQ flach (A/B wie bisher).
- Regelung an/aus (`agc_aktiv`) bleibt als Schalter in Klang/Automatik.
- Vorlauf-Schalter braucht weiterhin ein set_active (Delay rein/raus) - einziger
  struktureller Pipeline-Wechsel, der bleibt.
- Optional (Wunsch): Presets "geringe/mittlere/hohe Dynamik" = drei Saetze
  Band-Werte im Band-Reiter, ein Klick.
- settings.json: verwaiste Schluessel fallen durch den Loader (`if k in state`)
  beim naechsten Speichern automatisch weg.
- Doku/README: RMS/R128-Regeln als Historie kennzeichnen.

## D. Reihenfolge, jeder Schritt einzeln ausrollbar

1. Backend: Regelschleife auf Band reduzieren, Modi/Presets/RMS/R128/Schutz/
   Kompressor entfernen, EQ nach settings, Bypass neu. Test: Flask-Client mit
   Fake-CamillaDSP + DOM-Stub-Lauf der Seite gegen echte Antworten.
2. Oberflaeche: Reiter und Karten entfernen, Kopfkacheln anpassen, Klang mit Pegel.
3. tv.yml + install.sh: Band-Pipeline als Datei, Migration, ExecStart. Einmal
   `sudo ./install.sh` am Geraet, dann Kaltstart-Test (Ton nach Neustart, 4547=0).
Rueckkehrpunkt: Tag v1.13.4.

Erwartete Groesse danach: Backend ~2900 Zeilen, Oberflaeche ~1700.
