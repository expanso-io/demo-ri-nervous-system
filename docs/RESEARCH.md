# Research: The Drone With a Nervous System

Every quantitative claim or asset attribution spoken or rendered in this demo
cites a public source, model evaluation, or local run measurement below, or is
presented as representative (`../AGENTS.md`).

| Claim | Source | Accessed / Observed |
|---|---|---|
| `hear-clap` sound-event classifier combines Google YAMNet (`yamnet.tflite`, `3.9 MB`, AudioSet under Apache 2.0) with fine-tuned `ClapHead` (`models/clap_head.pt`, `6.0 KB`) achieving `0.9444` validation recall (`85 TP`, `5 FN`) and `0.9962` validation specificity (`1,823 TN`, `7 FP`) on `1,920` validation windows | Google YAMNet (`models/yamnet.tflite`, `models/yamnet_class_map.csv`) and `models/clap_head.json` (`5,748` train / `1,920` val windows) | 2026-11-16 |
| `hear-commands` keyword CNN (`models/kws.pt`, `1.7 MB`, `436,047` parameters) with squeeze-and-excitation blocks achieves `0.9876` validation and `0.9858` test accuracy on Speech Commands v0.02 (`up`, `down`, `stop`, `unknown`, `silence`) | `models/kws.json`; Speech Commands v0.02 (Pete Warden, arXiv:1804.03209, https://www.tensorflow.org/datasets/catalog/speech_commands, CC BY 4.0) | 2026-11-16 |
| `see-hands` pose estimator (`yolo11n-pose.pt`, `6.0 MB`) detects wrists above the shoulder line and renders hand bounding boxes at `8.8 ms` average frame latency (`imgsz=640`) on Apple Silicon MPS (`22.5 ms` CPU) | Ultralytics YOLO11n-pose (`https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n-pose.pt`) and local Apple M5 Max benchmark | 2026-11-16 |
| Rehearsal applause fixture (`fixtures/audio/applause.wav`, 3 s, 16 kHz mono) | "Applause ii.ogg" by thore, Wikimedia Commons (`fixtures/README.md`, Public domain) | 2026-11-12 |
| Audience voting-block camera tiles built from 38 openly licensed photos (CC0, CC BY, CC BY-SA) | `fixtures/hands-sources.json` (Wikimedia Commons file URLs pinned per entry) | 2026-11-12 |
| Offline stage typography uses Newsreader under SIL Open Font License 1.1 | `dashboard/fonts/NOTICE.txt` (`https://github.com/productiontype/Newsreader`, `https://openfontlicense.org`) | 2026-11-14 |
| Individual MQTT sense packets measure `61` B (`clap`), `73` B (`word`), and `75-76` B (`hands`); `fly-drone` act packets measure `134-164` B | Live dashboard `/api/state` and beat frame `recordings/frames/20261114-1647-08-reveal.png` | 2026-11-14 |
| During the recorded rehearsal run (`ri-nervous-system-20261114-1647.mp4`), sense devices processed `1072.6 MB` locally and emitted `30.2 KB` across `402` decisions by the 3:50 reveal (`1,235,741,347` B local vs `46,216` B in `616` decisions by run end) | `recordings/frames/20261114-1647-08-reveal.png` and post-run `/api/state` | 2026-11-14 |
| MQTT bus traffic contains only JSON decision messages and zero raw audio or video frames | `uv run python -m ri.preflight --drone sim --input fixture --stage-cam none --hands-device "" --hands-input fixture --require-bus` (`66` packets inspected on `127.0.0.1:18021`) | 2026-11-16 |
