# Fixtures and models: what they are and where they came from

The rehearsal plays the room into the real devices. Every input below is
real, openly licensed material; nothing here was recorded at, or describes,
any lecture.

## Sound: `audio/`

| File | Source | Licence |
|---|---|---|
| `applause.wav` | "Applause ii.ogg" by thore, Wikimedia Commons, trimmed to 3 s, loudness-normalised, 16 kHz mono | Public domain |
| `word-{up,down,stop}-{1,2,3}.wav` | Held-out test-set clips from the Speech Commands dataset v0.02 (Warden, 2018), 16 kHz mono | CC BY 4.0 |

Speech Commands: Pete Warden, "Speech Commands: A Dataset for
Limited-Vocabulary Speech Recognition", arXiv:1804.03209,
<https://www.tensorflow.org/datasets/catalog/speech_commands>. The word clips
come from the test split, which the keyword model never trained on.

## Camera: `hands-sources.json`, built into `build/`

The camera on the front block is a voting block of 18 seats composed from
people tiles. `just fixtures` downloads the 38 Wikimedia Commons photos pinned
in `hands-sources.json` (each entry records its title, URL and licence: CC0,
CC BY or CC BY-SA), finds people with a pose model, and keeps only the tiles
the model reads correctly as hand up or hand down. The photos and tiles land
in `build/` and are never committed; the screen never shows them. Sources
were filtered to exclude politicians, celebrities, children and artworks.
Each photo's author is on its Commons file page.

## Models: `../models/`

| File | What | Licence |
|---|---|---|
| `yamnet.tflite`, `yamnet_class_map.csv` | YAMNet sound-event classifier (Google, AudioSet classes); the clap score is the best of Hands, Clapping and Applause, gated by an onset detector | Apache 2.0 |
| `kws.pt`, `kws.json` | A 60,725-parameter keyword CNN trained here on Speech Commands for up, down, stop, unknown and silence: 95.9% test accuracy (`just kws-train`) | Trained weights from CC BY 4.0 data |
| `cache/yolo11n-pose.pt` | YOLO11n-pose, fetched by `just fixtures`, never committed; counts a hand as up when a wrist is above the shoulder line | AGPL-3.0 (Ultralytics) |
