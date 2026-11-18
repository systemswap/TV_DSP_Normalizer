# Fremde Bestandteile und ihre Lizenzen

Das Projekt selbst steht unter der GNU General Public License 3.0 (`LICENSE`). Die Rechte an den
folgenden Bestandteilen liegen bei ihren jeweiligen Autoren.

## Mitgelieferte Dateien

| Bestandteil | Stand | Lizenz | Quelle | Wo im Projekt |
|---|---|---|---|---|
| Silero VAD, Sprachmodell `silero_vad.onnx` | byte-gleich mit `src/silero_vad/data/silero_vad.onnx`, Commit `bfdc019` vom 06.11.2025 | MIT – `licenses/silero-vad-MIT.txt` | https://github.com/snakers4/silero-vad | `backend/silero_vad.onnx` (optionale Spracherkennung im Band-Reiter) |
| HiFiBerry-DSP-Profil `dspdac-noautomute.xml` | byte-gleich mit `sample_files/xml/dspdac-noautomute.xml`, Commit `0565c09` vom 09.04.2019 | MIT – `licenses/hifiberry-dsp-MIT.txt` | https://github.com/hifiberry/hifiberry-dsp | `dsp/dspdac-noautomute.xml` (wird von `install.sh` auf die Karte geschrieben) |

## Offline-Pakete (`abhaengigkeiten/pakete/`)

Damit sich die Anlage ohne Internet und in genau den getesteten Versionen installieren lässt,
liegen die Fremdpakete unverändert, so wie von den Projekten veröffentlicht, im Repository. Sie
passen zu Raspberry Pi OS 12 „Bookworm“, 64 Bit, Python 3.11 (`PLATTFORM.txt`); auf anderen
Systemen lädt `install.sh` dieselben Versionen aus dem Internet. Die Lizenztexte aus den Paketen
liegen unter `licenses/pakete/<paket>/`.

| Bestandteil | Version | Lizenz | Quelle |
|---|---|---|---|
| CamillaDSP für aarch64 (eigenständiges Programm) | 4.1.3 | GPL-3.0 (Text wie `LICENSE`) | https://github.com/HEnquist/camilladsp |
| pycamilladsp (`camilladsp-4.0.0-py3-none-any.whl`) | Commit `fdc0d163` | GPL-3.0 | https://github.com/HEnquist/pycamilladsp |
| hifiberrydsp (sigmatcpserver, dsptoolkit) | 0.21 | MIT | https://github.com/hifiberry/hifiberry-dsp |
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click | siehe `requirements.txt` | BSD-3-Clause | https://palletsprojects.com |
| blinker | 1.9.0 | MIT | https://github.com/pallets-eco/blinker |
| websocket-client | 1.9.0 | Apache-2.0 | https://github.com/websocket-client/websocket-client |
| PyYAML | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| PyAV, mit FFmpeg und dessen Codec-Bibliotheken (u. a. x264, x265, dav1d, opus, lame, vorbis, vpx, aom) | 12.3.0 | BSD-3-Clause; FFmpeg als GPL-Build (enthält GPL-2.0+-Bibliotheken) | https://github.com/PyAV-Org/PyAV |
| numpy (mit OpenBLAS und Fortran-Laufzeit) | 2.4.6 | BSD-3-Clause u. a., siehe `licenses/pakete/numpy-2.4.6/` | https://numpy.org |
| onnxruntime (optional, Spracherkennung; Telemetrie im Projekt abgeschaltet) | 1.30.0 | MIT, Drittanbieter in `ThirdPartyNotices.txt` | https://github.com/microsoft/onnxruntime |
| flatbuffers, protobuf, packaging (für onnxruntime) | siehe `requirements-vad.txt` | Apache-2.0; BSD-3-Clause; Apache-2.0 oder BSD-2-Clause | PyPI |
| python-evdev | 1.9.3 | BSD-3-Clause | https://github.com/gvalkov/python-evdev |
| pyalsaaudio | 0.11.0 | PSF-2.0 | https://github.com/larsimmisch/pyalsaaudio |
| RPi.GPIO | 0.7.1 | MIT | https://sourceforge.net/projects/raspberry-gpio-python/ |
| spidev | 3.8 | MIT | https://github.com/doceme/py-spidev |
| lxml (mit libxml2, libxslt) | 6.1.2 | BSD-3-Clause, MIT | https://lxml.de |
| requests | 2.34.2 | Apache-2.0 | https://github.com/psf/requests |
| zeroconf | 0.150.0 | LGPL-2.1-or-later | https://github.com/python-zeroconf/python-zeroconf |
| certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi |
| charset-normalizer, idna, urllib3, xmltodict, ifaddr | siehe `requirements.txt` | MIT bzw. BSD-3-Clause | PyPI |
| pip, wheel (für die Offline-Installation) | 26.2.1, 0.48.0 | MIT | https://pip.pypa.io |

### Quellcode zu den mitgelieferten Programmdateien

Für die Programmdateien unter GPL oder LGPL gilt: Der vollständige Quellcode genau dieser
Versionen ist hier erhältlich.

| Bestandteil | Quellcode |
|---|---|
| CamillaDSP 4.1.3 | liegt bei: `abhaengigkeiten/quellcode/camilladsp-4.1.3-quellcode.tar.gz` (auch https://github.com/HEnquist/camilladsp/releases/tag/v4.1.3) |
| pycamilladsp | das Paket besteht selbst aus Quellcode (Python) |
| PyAV 12.3.0 mit FFmpeg | https://github.com/PyAV-Org/PyAV/tree/v12.3.0 und die Build-Rezepte samt Quellarchiven von FFmpeg und den Codec-Bibliotheken: https://github.com/PyAV-Org/pyav-ffmpeg |
| zeroconf 0.150.0 | https://pypi.org/project/zeroconf/0.150.0/#files (Quellarchiv) |

### Nicht im Repository: FLIRC

Das FLIRC-Programm `flirc_util` (optional, Fernbedienung) wird **nicht** mitgeliefert: Seine
Lizenzangaben sind widersprüchlich („License: None“ für die Programme), der Quellcode ist nicht
veröffentlicht. `install.sh` lädt es bei Bedarf aus der Paketquelle des Herstellers
(https://flirc.tv).

## Bei der Installation aus dem System

ffmpeg (Lautheitsmessung, Mitschnitt), Python, ALSA und die übrigen Systempakete kommen aus
Raspberry Pi OS bzw. Debian, jeweils unter ihren eigenen Lizenzen.

## Übertragener Code

Keiner. Die Register des DSP-Profils (z. B. 4547) stammen aus den Metadaten der Profildatei selbst;
die fünf ASRC-Register für den Toslink-Eingang (`bin/enable-spdif-in.sh`) aus der Anleitung von
HiFiBerry „Using the DAC+ DSP to record audio from S/PDIF“.
