# Design brief: The Drone With a Nervous System

## Subject material

A small drone on a lecture-theatre stage that has no brain of its own. The
audience is its nervous system: a clap wakes it, a shouted word moves it, a
show of hands lands it. Two stage sensors feed three sense pipelines: a single
handheld mic feeds hear-clap and hear-commands, and one camera feeds see-hands
plus the live stage view. Each pipeline runs on an Expanso Edge node that turns
raw sound or pictures into one small decision. A fourth node beside the drone,
fly-drone, applies one rule table and flies it. Expanso Cloud deploys the four
pipelines and shows they are running.

The real source is the room: sound and faces. The destination is the drone.
What travels between them is a handful of decisions.

## Palette rationale

The screen uses a clean, un-heavy **light gallery ground** (`#f4f1ea` warm stone,
not pure white) with `#fbf9f4` cards and fine `#d6cfc2` borders so larger moving
particles, live waveforms, and the on-screen drone read effortlessly from row 10:

- **Copper `#d95d16`** is the nerve impulse: large glowing decision orbs and live
  waveform energy travelling across the board.
- **Crimson `#c92a35`** is the reflex: land. It appears only when the safety
  rule fires.
- **Cobalt `#1d63b8`** and **amber `#b4690e`** mirror the drone's up and down
  commands and LED states cleanly against warm paper.
- **Emerald `#0d7a5f`** means running: a live node, a parked drone.
- Raw grains (`#78695a`) and waveforms stay inside the sense-to-node boundary.

Type: Newsreader (OFL, vendored) for the one prompt line, monospace for concise
telemetry and inspected messages, keeping text minimal.

## Stage layout

The board is drawn at 1920 x 1080 and scaled as a whole to whatever screen it
is on, so every projector shows the same picture. There is no title bar and
no prompt line: the presenter says what to do, and the screen shows only what
the room is working on. Two tiers:

1. The show, across the top: a wide flight arena. The drone starts at the left
   and moves right only while it is flying, so the trace behind it is the whole
   session: every climb and landing, with one mark for each thing the room did
   (`wake`, `up`, `down`, `stop`, `hands up`). Beside it, the phase as one
   large word, the altitude, and the one reason for the last act in plain
   words (`hands 83% beats word up`). Asleep, it reads `say up to wake it`.
2. The nervous system, underneath, read left to right and then up: `sense`,
   `model`, `decide`. Rows are in priority order on both sides (camera and
   `see-hands` on top, clap at the bottom), so no lane crosses another and
   the highest rule is literally the highest row. One nerve runs from the rule
   table up to the drone, wherever it is.

Only the channel in use is at full strength. The beat names it, and any
channel that has just sent something joins it; the rest dim.

Nothing the room is meant to read is smaller than 18px. Operator controls
(pipeline status, battery when low, safety buttons, the microphone switch)
stay hidden until the pointer is on the top right corner or the `mic` card;
`Esc` still lands the drone. No device or model vendor names appear on the
board. `just up` starts the four pipelines, so the rig opens ready to fly;
a recorded take resets to the zero state and starts them on camera.

## Scenario cast

- **Sources (waveforms + live meters):** one `handheld mic` (auto-preferring a
  plugged-in DJI mic with built-in MacBook mic fallback; live waveform +
  `CLAP`/`UP`/`DOWN`/`STOP` chips) feeding both `hear-clap` and `hear-commands`, plus
  `camera` (live motion waveform + 50% threshold voting bar).
- **Expanso nodes:** `hear-clap`, `hear-commands`, `see-hands`. Compact cards
  stay light grey until active, then glow emerald with thick particle trails.
- **Decide:** `fly-drone`, an Expanso node with the concise rule table on its
  face (`land > stop > move > wake`), tagging the winning rule (`WINS`) and any
  beaten rule (`LOST`, struck through).
- **Act (Hero Drone):** wide flight arena (`0` to `2.0 m`, animated rotors,
  visible LED glow, dashed target line, altitude trail with command marks),
  the phase as one large word and the reason beside it. The live camera view
  sits in the `camera` sense card, fed from the single attached physical
  camera; its product name is never shown.(`OBSBOT Tiny 2 Lite`).
- **Orchestrator:** Expanso Cloud above the nodes, with dashed control lanes
  carrying deploy signals down and status up.
- **Operator bar and reveal:** header clicker lock toggle, `land [Esc]` and
  `cut [Shift+Esc]` emergency stops, clickable message inspector cards, and the
  Reveal screen comparing per-packet bytes against raw MB kept on devices.

## Lane semantics

- Sense to node: a dotted lane of dim grains, density from each device's measured window rate.
  They dissipate at the node's wall while it is off and enter it while it is
  on. They never continue past it.
- Node to fly-drone: one ember impulse per real decision message on the MQTT
  bus, so the counters and the impulses always agree. The nerve it travels
  lights up and fades, and a ring marks its arrival. The camera reports
  several times a second, so a routine count is a small impulse and only a
  count that could land the drone is a full one.
- fly-drone to drone: one impulse per act, in that act's colour, up the one
  nerve from the rule table to the drone's skids.
- Cloud lanes: deploy and status signals, never data.

## Deviations, with reasons

- The camera meter shows frame activity, not a people count. Counting people
  needs the model, and the model runs inside the pipeline, which is off in the
  zero state.
- The four per-command boxes, the act history list and the message shelf are
  gone. The marks on the flight trail carry the history, and each model node's
  message is clickable for the exact payload.
- Three mockups were not taken to David before the build because of the
  recording deadline. The layout follows BUILD_SPEC part 7 directly.
