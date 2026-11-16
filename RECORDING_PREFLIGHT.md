---
# This is a declaration of readiness, not a command that starts anything.
# `recording-preflight.py` validates this front matter and the required sections.
status: PASS_WITH_NAMED_WARNINGS # PASS | PASS_WITH_NAMED_WARNINGS | BLOCKED
record_check: PASS # PASS only after `just record-check` has passed
record_check_evidence: "just record-check passed 2026-11-16 (ruff, mypy, 65 pytest, 4 Expanso Edge pipelines validated, pipeline-check, provider-check, video-check, motion-check 1089 px / 1142 px reduced, clean-check, GET /api/state OK); just review-shots passed all 11 visual/functional checks; responsive layout verified at 1920x1080, 1649x1158, 1440x900, and 1280x800 with zero right-edge clipping; just preflight verified CoDrone EDU (battery 86 percent on /dev/cu.usbmodem3067357E34341), OBSBOT Tiny 2 Lite (1280x720), and sounddevice MacBook Pro Microphone; --require-bus inspected 66 MQTT packets with zero media payloads"
accepted_warnings: ["CoDrone EDU controller connected and reported 86 percent battery under just preflight, while physical prop takeoff/landing was not flown at the desk (DJI Tello adapter also remains covered by unit tests).", "Wireless DJI mic was unattached during 2026-11-16 checks (sounddevice resolver selected built-in 5:MacBook Pro Microphone; live microphone capture requires launching from Ghostty or another terminal holding macOS Microphone TCC permission).", "Full five-minute unattended MP4 take (recordings/ri-nervous-system-20261114-1647.mp4) was recorded on 2026-11-14 before the 2026-11-16 square camera/hand-box UI, sounddevice live capture, and upgraded kws.pt/clap_head.pt models; re-run just record-sim when a refreshed unattended MP4 is needed."]
---

# Recording preflight: The Drone With a Nervous System

This is the final human review before a take. It does **not** start a recorder,
runtime, deployment, Cloud operation, publish action, or browser. It records why
the already-running, mechanically checked demo is ready to put on camera.

`just record-check` is the mechanical lane: tests, UI lint, and the demo's
live endpoint. This file is the story/proof lane. Passing one does not prove the
other.

## Claim

Three small edge devices run local AI models to convert raw sound and audience
video into compact JSON decisions over MQTT, flying a drone safely under a
deterministic rule table (land above stop above move above wake) without moving
media off the devices.

## Audience and objection

Public lecture audience members and systems engineers who doubt that local
tiny models running across distributed edge nodes can coordinate a physical
safety-critical rule table in real time without streaming raw audio or camera
video to a cloud server.

## Capture surface

`http://127.0.0.1:20170/` captured at `1920x1080` landscape (no 9:16 vertical
crop planned for this stage lecture screen).

## Opening state

Before beat 1 begins (`0:00`), the operator bar displays `#pipeline-status` as
`off` beside `Expanso Cloud`, with safety buttons at the right. Under it the
prompt line reads the hook in large type. The top tier is the flight arena
(drone on the `0` line, no trail yet) beside `dark` / `on table`. The bottom
tier reads left to right: `sense` holds `camera` (live view, hands-up count
and 50% bar) above `mic` (waveform and `UP/DOWN/STOP/CLAP` chips); `model`
holds `see-hands`, `hear-commands` and `hear-clap`, each muted with
`nothing sent` and `0 sent`; `decide` holds the `fly-drone` rule table, with
one nerve running up to the drone. Safety buttons and the microphone switch
appear when the pointer is on them.
The board is drawn at `1920x1080` and scales as a whole to the window.

## Operator action

`just up` now defaults to the live CoDrone EDU, DJI-preferred live `mic`, and
`OBSBOT Tiny 2 Lite` camera (`just up sim` overrides only the drone to `SimDrone`;
`just up-sim` keeps all simulator/fixture overrides for offline runs). `just start`
deploys/starts all four Expanso Cloud pipelines. For bench or live stage runs,
`just up-cam` starts the live `OBSBOT Tiny 2 Lite` camera feeding both `see-hands`
and the square `camera` preview with hand bounding boxes while keeping audio on
cueable fixtures. For automated five-minute runs, `just record-sim` advances the
clock via `ri.conductor` (starting Cloud pipelines at `0:08`, applause at `0:33`
and `3:00`, keyword clips `up`/`down` from `1:18`, partial hands below majority at
`2:18`, majority hands at `2:42`, simultaneous hands and `up` conflict at `3:16`,
Reveal at `3:50`, and Handoff at `4:30`). On stage, `ri.screen` clicker lock lets
presenter keys advance prompts while `Esc` and `Shift+Esc` remain instant
emergency stops.

## Visible outcome

Active verification on 2026-11-16 (`just up sim` with `4/4` pipelines running,
live `OBSBOT Tiny 2 Lite`, and `applause` / `up` / `stop` cues across
`.runtime/review/redesign-1920.png`, `.runtime/review/redesign-tall.png`,
`.runtime/review/redesign-1440.png`, `.runtime/review/redesign-1440-stop.png`, and
`.runtime/review/redesign-1280.png`) confirms compact vertical proportions with
zero wasted card space across `1280x800`, `1440x900`, `1649x1158`, and `1920x1080`
viewports, zero right-edge clipping, uncropped 16:9 `camera` video with close-up
and full-body hand bounding boxes active immediately from `just up`
(`.runtime/review/user-cam-boxes.jpg`), model descriptions in `models`,
branching `decide` arrows highlighting active `wake`, `move`, and `stop`
command boxes beside `SIM hovering`, and the clickable 4-card `messages` shelf.
Earlier full-arc `ri.arccheck` timing (`14` of `14` beats on time) also verified
amber `waiting: 6 of 18`, majority landing (`14/18`), conflict (`land WINS`
beating `word up`), and Reveal byte totals.

## Truth boundary

Local/real in this build: YAMNet plus fine-tuned `ClapHead` (`clap_head.pt`)
clap inference, fine-tuned Squeeze-and-Excitation `KeywordNet` (`kws.pt`),
YOLO11n-pose (`yolo11n-pose.pt`) on Apple Silicon MPS (`8.8 ms` per frame)
rendering live hand bounding boxes onto `/preview.jpg`, low-latency `sounddevice`
PortAudio live audio capture (macOS Microphone fallback when DJI mic is unplugged),
OpenCV capture from `OBSBOT Tiny 2 Lite StreamCamera` to the square viewport,
four live `expanso-edge` pipelines orchestrated by Expanso Cloud, the embedded
MQTT broker on port `18021`, `ri.decide` rule evaluation, `SimDrone` physics,
CoDrone EDU serial/USB preflight verification, and Playwright/Chromium visual checks.
Fixture/double boundaries: automated audio cue checks use Wikimedia Commons
applause and Speech Commands clips; multi-person hand quorum scenes use 18-seat
Wikimedia crowd tiles; physical CoDrone EDU and DJI Tello flight maneuvers
without live indoor flight remain verified against unit SDK test doubles
(`tests/test_drone_adapters.py`); unattended live microphone audio requires
launching from Ghostty with Microphone TCC permission and plugging in the
handheld DJI receiver before stage use.

## Proof evidence

- Live camera, uncropped 16:9 preview, hand bounding boxes, and UI checks (`2026-11-16`):
  `just preflight` detected CoDrone EDU on `/dev/cu.usbmodem3067357E34341`
  (`Battery = 86%`), `5:MacBook Pro Microphone`, and
  `0:OBSBOT Tiny 2 Lite StreamCamera (1280x720)`; `just record-check` and
  `just review-shots` both passed (`65` pytest tests, `ruff`, `mypy`,
  `1089 px` / `1142 px reduced` motion diff, `0` overlaps, all `11` visual checks);
  multi-viewport captures (`.runtime/review/redesign-1280.png`,
  `.runtime/review/redesign-1440.png`, `.runtime/review/redesign-tall.png`,
  `.runtime/review/redesign-1920.png`) and box checks
  (`.runtime/review/vb-majority.jpg`, `.runtime/review/hand-boxes-check.jpg`,
  `.runtime/review/user-cam-boxes.jpg`) verified the compact 16:9 `camera`
  card, close-up and crowd hand bounding boxes, branching per-command boxes, and
  clean right-boundary fit.
- Trained audio models (`2026-11-16`): `models/kws.pt` (`436,047` params) scored
  `0.9876` val and `0.9858` test accuracy on Speech Commands v0.02;
  `models/clap_head.pt` scored `0.9444` val recall and `0.9962` val specificity
  across `1,920` validation windows.
- Expanso Cloud and MQTT isolation (`2026-11-16`): `uv run python -m ri.preflight --drone sim --input fixture --stage-cam none --hands-device "" --hands-input fixture --require-bus`
  inspected `66` live MQTT packets on `127.0.0.1:18021` with zero media payloads.
- Prior unattended full-arc MP4 take (`2026-11-14`, pre-light-UI redesign):
  `recordings/ri-nervous-system-20261114-1647.mp4` (`34.9 MB`, `1920x1080`, `5:05`),
  `14` of `14` beats on time (`docs/RESEARCH.md` logs measured Reveal totals).

## Decision

`PASS_WITH_NAMED_WARNINGS` is accurate: all code, pipeline, privacy, UI,
motion, model, CoDrone EDU USB preflight, and live `OBSBOT Tiny 2 Lite` capture
checks passed after final edits. Three warnings remain explicitly named and
actionable before a full theatre launch: (1) power on the CoDrone EDU drone
airframe (in addition to the controller) and run `just preflight` before live flight,
(2) attach the wireless handheld DJI mic and run `just up` from Ghostty (with
Microphone TCC permission), and (3) re-run `just record-sim` if an updated
unattended MP4 of the light-gallery square-camera UI is needed.
