# The Drone With a Nervous System

**Three small devices, one tiny model each, and a few bytes between them are
enough to let a room full of people fly a drone without a data centre.**

A clap wakes the drone, a shouted word moves it, the room's applause lands it.
Each sense is its own box with one model and one Expanso Edge pipeline that
turns raw sound or pictures into one small decision. A fourth pipeline beside
the drone applies one rule table (land > stop > move > wake) and flies it.
Expanso Cloud deploys all four and shows they are running. The design is in
[`docs/BUILD_SPEC.md`](docs/BUILD_SPEC.md).

```bash
just workspace-init   # once: enrol four Edge nodes (identities stay in ./.expanso-edge)
just cameras          # list connected microphone and camera names/indices
just preflight        # verify CoDrone EDU + OBSBOT Tiny 2 Lite + live DJI/Mac audio
just up               # full live rig, pipelines started: CoDrone EDU + OBSBOT Tiny 2 Lite + live DJI/Mac mic
just start            # start all 4 Cloud jobs (`4/4 running`) for live flight
just stop-jobs        # stop all 4 Cloud jobs back to `0/4`
just run              # drive the five-minute arc against a fixture rig
just ready            # is the running rig ready to fly? one line per failure, with the fix
just again            # fast clean restart on the current code, then the readiness check
just down             # stop everything, delete the Cloud jobs
just up-sim           # override: simulated drone + deterministic fixtures (no hardware)
just preflight-sim    # override: check models and fixtures without hardware attached
just record-sim       # up-sim, record the whole arc headless, check every beat, down
just record-check     # full test/lint/UI gate plus live /api/state check
just check            # lint, types, tests, pipeline rules, UI rules, motion
```

## What runs

| Piece | Where | What it does |
|---|---|---|
| `ri/device.py` | each sense box | captures sound windows or camera frames, posts them to its local pipeline, serves the model at `/infer` |
| `pipelines/hear-clap.yaml` | hear-clap node | sound window -> YAMNet -> a clap message on `ri/sense/clap` |
| `pipelines/hear-commands.yaml` | hear-commands node | sound window -> keyword model -> up/down/stop on `ri/sense/word` |
| `pipelines/see-hands.yaml` | see-hands node | camera frame -> pose model -> hand count on `ri/sense/hands` |
| `pipelines/fly-drone.yaml` | fly-drone node | every sense message -> the rule table -> one act on `ri/act` |
| `ri/flight.py` | beside the drone | the rule table (`ri/decide.py`) and the drone adapter |
| `ri/drone/` | | one adapter interface: an on-screen drone, CoDrone EDU, and Tello |
| `ri/screen.py`, `dashboard/` | presenter laptop | the audience screen, localhost only |
| `ri/conductor.py` | | plays the room into the devices on the arc's clock |

Raw sound and frames never leave the box that captured them; only decisions
cross the bus. On stage each sense node is its own small computer; for
rehearsal all four Edge agents run on one laptop with the same pipelines.

Presenter controls on the screen:
- **Escape** lands the drone immediately; **Shift+Escape** cuts motors now
  (matching header buttons `land [Esc]` and `cut [Shift+Esc]`).
- **clicker** button arms/disarms presentation clicker keys (`ArrowLeft`,
  `ArrowRight`, `PageUp`, `PageDown`) for beat navigation.
- Hovering the `mic` card shows **LAPTOP | HANDHELD**; clicking it switches live between MacBook and DJI
  microphones (or start directly with `just up-live sim "OBSBOT Tiny 2 Lite" mac`
  / `just up-live`).
- Clicking **UP / DOWN / STOP** pills on `mic` triggers a cue for
  bench rehearsal; **Ctrl-click** on a sense node toggles its link fault.

## Drone adapters and stage hardware

```bash
just cameras                     # list attached mics and cameras by OS name
just preflight                   # default: CoDrone EDU + OBSBOT Tiny 2 Lite + live audio
just up                          # default: CoDrone EDU + OBSBOT Tiny 2 Lite + live DJI/Mac mic
just up codrone live "Brio 505"  # swap camera by parameter
just up codrone live "OBSBOT Tiny 2 Lite" mac   # force MacBook mic at launch
just up sim live                 # test live mic + OBSBOT camera on the on-screen drone
just up-sim                      # simulator + fixture audio/hands (no hardware)
just up tello live "OBSBOT Tiny 2 Lite" mac     # override airframe
```

Camera sources resolve dynamically by human name (for example `"OBSBOT Tiny 2 Lite"`
via macOS AVFoundation or Linux v4l2 sysfs) as well as numeric index or RTSP/HTTP URL,
so USB replugs never scramble OpenCV indices.

Advanced flags and distributed multi-host setup (`ri/stack.py`, `ri/preflight.py`,
and pipelines):
- `--offline` / `--skip-deploy`: run the rig with local agents only when
  rehearsing without Expanso Cloud access.
- `--hands-input fixture|live` (`RI_HANDS_INPUT`) and `--hands-device`
  (`RI_HANDS_DEVICE`): bind `see-hands` to a specific live camera independently
  of the microphones (`--clap-device` / `--word-device`).
- `--hands-roi x,y,w,h` (`RI_HANDS_ROI`) and `RI_PERSON_MIN`: crop the front
  camera onto the audience voting block and set minimum person confidence.
- `--stage-cam SOURCE` (`RI_STAGE_CAM`): stream an MJPEG camera preview inside
  the camera card on stage (accepts a camera name, index, or stream URL).
- Multi-host Raspberry Pi deployment: set `RI_MQTT_BIND` / `RI_MQTT_HOST`,
  `RI_FLIGHT_BIND` / `RI_FLIGHT_HOST`, `RI_DEVICE_BIND`, `RI_SCREEN_BIND`, and
  per-device `RI_CLAP_INPUT`, `RI_WORD_INPUT`, `RI_HANDS_INPUT` / `RI_*_DEVICE`.
- `just preflight codrone live` / `just preflight tello live --require-bus`
  checks battery, SDK link, and zero-media MQTT packets before launch.

`RI_DRONE=sim|codrone|tello` (or `uv run python -m ri.flight --drone ...`).
Both `CoDroneDrone` and `TelloDrone` pass unit tests against mock SDKs in
`tests/test_drone_adapters.py`; run `just preflight` with the physical airframe
connected before live flight in the theatre.

## Models and fixtures

See [`fixtures/README.md`](fixtures/README.md) for every model and input and
its licence. Camera fixtures are built locally with `just fixtures` and are
never committed.

## Recording

[`recordings/README.md`](recordings/README.md) has the one-command take and
how it is checked.

## Room size

`just up large` is the stage: lift to 1.0 m, three ups to 2.0 m, ceiling
2.0 m, legs of 1.0 m. `just up small` is a hotel room with about a
king-size bed of clear floor: lift to 0.4 m, five ups to 0.9 m, ceiling 1.0 m,
legs of 0.6 m. `just up hall` is a big hall: five claps to 2.0 m, two ups of a metre each
to 4.0 m, legs of 2.0 m, and a harder climb. The drone's height sensor reads
to about 2 m (Robolink rate it to 1.5 m), so above that a climb loses its
reading and stops where it is. The rig keeps the last size used, prints it at launch and in
`just ready`, and `just again` reuses it. The sizes live in `ri/room.py`.

## Run of show

The flight is fixed. The room only releases each part of it.

1. Clap five times within three seconds. The drone lifts to 1.0 m. Key: `1` or `C`.
2. Say "up, up, up". Each one is a third of a metre, to 2.0 m, which is
   also the ceiling: a fourth up is refused. Key: `2` says all three, one
   after each hover; up arrow says one. (Small room: five ups of 0.1 m.)
3. Point it left: both arms straight out to the camera's left (your right),
   at shoulder height, for about half a second. The drone flies its leg
   ahead and to the left, then stops. More points do nothing. Key: `3` or `A`.
4. Point it right: both arms out to the camera's right. Its leg ahead and to
   the right, then it stops over the pad. Key: `4` or `D`.
5. The room's applause brings it down, once both legs are flown: everyone
   claps, steady, for about three seconds (70% of the last 2.5 s must sound
   like clapping; `RI_APPLAUSE_LEVEL`, default 0.25, is a loudness floor).
   The bar under the camera fills as the room claps. Applause before the
   route is done is ignored, so the room can cheer the take-off. Jazz hands
   (both hands up beside your head, shaking) land it at any time. Either way
   the drone lands and its motors stop. Keys: `5` or `H` for applause, `J`
   for jazz hands.

Every rig start records its whole bus to `.runtime/flights/` (the last 60
starts are kept): each decision, each act, the drone's state ten times a
second with its barometer and range sensor, and the camera's arm readout.
`just flights` prints the last flight as a timeline, with how long each move
took to start and to finish and how high it really got; `just flights 2` is
the one before.

`just arms` prints, twice a second, what the camera reads off your arms
and which signal that makes, so the thresholds in `ri/senses/hands.py` can
be tuned against real arms.

`Esc` lands it at any point and holds it for 10 seconds. The reset button
does the same and clears the board. The numbers live in `ri/room.py`
(heights and legs) and `ri/decide.py` (`LAND_HANDS`).
