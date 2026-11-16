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

## Scenario cast

- **Sources (waveforms + live meters):** one `handheld mic` (auto-preferring a
  plugged-in DJI mic with built-in MacBook mic fallback; live waveform + `onset`
  and `UP`/`DOWN`/`STOP` pills) feeding both `hear-clap` and `hear-commands`, plus
  `camera` (live motion waveform + 50% threshold voting bar).
- **Expanso nodes:** `hear-clap`, `hear-commands`, `see-hands`. Compact cards
  stay light grey until active, then glow emerald with thick particle trails.
- **Decide:** `fly-drone`, an Expanso node with the concise rule table on its
  face (`land > stop > move > wake`), tagging the winning rule (`WINS`) and any
  beaten rule (`LOST`, struck through).
- **Act (Hero Drone + Stage Cam):** oversized drone flight arena (`0` to `2.0 m`,
  animated rotors, visible LED glow) paired with a live `stage cam` viewport
  fed cleanly from the single attached physical camera (`OBSBOT Tiny 2 Lite`).
- **Orchestrator:** Expanso Cloud above the nodes, with dashed control lanes
  carrying deploy signals down and status up.
- **Operator bar and reveal:** header clicker lock toggle, `land [Esc]` and
  `cut [Shift+Esc]` emergency stops, clickable message inspector cards, and the
  Reveal screen comparing per-packet bytes against raw MB kept on devices.

## Lane semantics

- Sense to node: dim grains, density from each device's measured window rate.
  They dissipate at the node's wall while it is off and enter it while it is
  on. They never continue past it.
- Node to fly-drone: one bright ember impulse per real decision message on the
  MQTT bus, so the counters and the impulses always agree.
- fly-drone to drone: one impulse per act, in that act's colour.
- Cloud lanes: deploy and status signals, never data.

## Deviations, with reasons

- The camera meter shows frame activity, not a people count. Counting people
  needs the model, and the model runs inside the pipeline, which is off in the
  zero state.
- Three mockups were not taken to David before the build because of the
  recording deadline. The layout follows BUILD_SPEC part 7 directly.
