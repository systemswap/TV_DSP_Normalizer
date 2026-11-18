# Fremde Bestandteile und ihre Lizenzen

Das Projekt selbst steht unter der GNU General Public License 3.0 (`LICENSE`). Die Rechte an den
folgenden Bestandteilen liegen bei ihren jeweiligen Autoren.

## Im Repository mitgeliefert

| Bestandteil | Stand | Lizenz | Quelle | Wo im Projekt |
|---|---|---|---|---|
| Silero VAD, Sprachmodell `silero_vad.onnx` | byte-gleich mit `src/silero_vad/data/silero_vad.onnx`, Commit `bfdc019` vom 06.11.2025 | MIT – `licenses/silero-vad-MIT.txt` | https://github.com/snakers4/silero-vad | `backend/silero_vad.onnx` (optionale Spracherkennung im Band-Reiter) |
| HiFiBerry-DSP-Profil `dspdac-noautomute.xml` | byte-gleich mit `sample_files/xml/dspdac-noautomute.xml`, Commit `0565c09` vom 09.04.2019 | MIT – `licenses/hifiberry-dsp-MIT.txt` | https://github.com/hifiberry/hifiberry-dsp | `dsp/dspdac-noautomute.xml` (wird von `install.sh` auf die Karte geschrieben) |

## Bei der Installation geladen (nicht im Repository)

`install.sh` installiert diese Programme und Bibliotheken in den Versionen aus
`abhaengigkeiten/requirements*.txt` bzw. `install.sh`:

| Bestandteil | Version | Lizenz | Quelle |
|---|---|---|---|
| CamillaDSP (eigenständiges Programm) | 4.1.3 | GPL-3.0 | https://github.com/HEnquist/camilladsp |
| pycamilladsp | Commit `fdc0d163` (4.0.0) | GPL-3.0 | https://github.com/HEnquist/pycamilladsp |
| hifiberrydsp (sigmatcpserver, dsptoolkit) | 0.21 | MIT | https://github.com/hifiberry/hifiberry-dsp |
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click | siehe `requirements.txt` | BSD-3-Clause | https://palletsprojects.com |
| blinker | 1.9.0 | MIT | https://github.com/pallets-eco/blinker |
| websocket-client | 1.9.0 | Apache-2.0 | https://github.com/websocket-client/websocket-client |
| PyYAML | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| PyAV (Dolby-Decoder, bringt eigene FFmpeg-Bibliotheken mit) | 12.3.0 | BSD-3-Clause; FFmpeg LGPL-2.1 | https://github.com/PyAV-Org/PyAV |
| numpy | 2.4.6 | BSD-3-Clause | https://numpy.org |
| onnxruntime (optional, Spracherkennung) | 1.30.0 | MIT | https://github.com/microsoft/onnxruntime |
| python-evdev | 1.9.3 | BSD-3-Clause | https://github.com/gvalkov/python-evdev |
| pyalsaaudio | 0.11.0 | PSF-2.0 | https://github.com/larsimmisch/pyalsaaudio |
| RPi.GPIO | 0.7.1 | MIT | https://sourceforge.net/projects/raspberry-gpio-python/ |
| spidev | 3.8 | MIT | https://github.com/doceme/py-spidev |
| lxml | 6.1.2 | BSD-3-Clause | https://lxml.de |
| requests | 2.34.2 | Apache-2.0 | https://github.com/psf/requests |
| zeroconf | 0.150.0 | LGPL-2.1 | https://github.com/python-zeroconf/python-zeroconf |
| übrige Hilfspakete (certifi, charset-normalizer, idna, urllib3, xmltodict, ifaddr, flatbuffers, packaging, protobuf) | siehe `requirements*.txt` | MPL-2.0 (certifi), sonst MIT, BSD oder Apache-2.0 | PyPI |
| FLIRC-Software `flirc_util` (optional, Fernbedienung) | 3.27.19 | Herstellerlizenz | https://flirc.tv |
| ffmpeg, Python, ALSA (Systempakete von Raspberry Pi OS) | aus dem System | LGPL/GPL bzw. jeweilige Lizenz | Debian / Raspberry Pi OS |

## Übertragener Code

Keiner. Die Register des DSP-Profils (z. B. 4547) stammen aus den Metadaten der Profildatei selbst;
die fünf ASRC-Register für den Toslink-Eingang (`bin/enable-spdif-in.sh`) aus der Anleitung von
HiFiBerry „Using the DAC+ DSP to record audio from S/PDIF“.
