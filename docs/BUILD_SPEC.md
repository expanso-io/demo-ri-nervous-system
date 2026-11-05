# RI demo build: the drone with a nervous system

---

## 1. The message

**For the room:** intelligence does not need a data centre. Three small
devices, one tiny model each, a few bytes between them, and the audience
flies a drone.

**For Eiman's talk (deck of 4 Nov, "The Future of AI is Tiny and
Physical"):** the demo sits in part 2 right after slide 7 (devices swapping
feature packets, no cloud dependency) and hands into slides 8 and 9 (search
and rescue). Arm's on-device demo belongs at the end of part 1
(microcontrollers, on-device processing). Device first, then devices.

**Expanso's role, in one sentence:** Expanso puts the right small model on
the right device, keeps it running, and carries the tiny decisions between
them. It is the wiring, not the brain.

---

## 2. The arc, five minutes

| Time | Beat | Sense | Device | Decision sent | Drone |
|---|---|---|---|---|---|
| 0:00 | Hook. Drone dark on the table. "This drone has no brain of its own. You are its nervous system." | | | | dark |
| 0:30 | **Clap to wake it** (the reflex) | room mic | hear-clap | `clap 0.93` | LEDs on, rises to hover |
| 1:15 | **Shout up or down** (one volunteer on a handheld mic) | handheld mic | hear-commands | `up 0.81` | turns that colour, climbs or descends (venue: 9 m ceiling, 4x4 m floor, nothing over the audience) |
| 2:15 | **Hands up to land it** (consensus) | camera on the front block | see-hands | `hands 62%` | lands on a majority; below it, hovers and the screen says "waiting" |
| 3:15 | **Conflict.** Volunteer shouts "up" while hands are up | both | both | `up 0.77` and `hands 71%` | hands win; it lands. "Landing is the safety reflex. The shout arrived. It lost." |
| 3:50 | **Reveal.** Three message cards on screen. | | | | parked, LEDs breathing |
| 4:30 | **Handoff.** "Three devices, three senses, one job. Now put them on a mountainside." Into her search-and-rescue slides. | | | | |

Script lines, David's voice, from the audience's side:

- Open: "Every AI you have used lives in a building the size of a football
  pitch, a long way from here. This one's intelligence lives on this table.
  And in you."
- After the clap: "That device heard a clap and sent one word. Not the
  sound. One word."
- After up/down: "This device knows three words. It does not know
  English. It does not need to."
- At "waiting": "Half of you want it down. It will not guess."
- At the conflict: "Two instructions arrived. We wrote one rule: landing
  wins. That rule is the whole safety system."
- Reveal: "Here is everything that travelled between these devices during
  that run. No picture of you left that camera. No sound left that
  microphone. Three decisions did."
- Handoff: "Three devices, three senses, one job, and nothing left the room
  but three decisions. Eiman, now put them on a mountainside."

Fallback arc (the original rover-and-card script in the Google Doc) stays on
file if flight permission is refused: card picks the station, voice says go
and stop, covered card means wait.

---

## 3. State storyboard

Per the demo invariants: every state screenshotted at 1920x1080 before David
sees anything.

| State | Visible | Moving |
|---|---|---|
| **Zero** | Three sense devices on and sensing: room mic level, handheld mic level, camera people count. Expanso nodes grey. Drone dark. Cloud lane grey. | Sense meters tick; nothing flows. Data is going nowhere. |
| **Pipelines off** | Devices joined to Cloud, lights dark grey. | Same. Cloud shows nodes joined, pipelines stopped. |
| **Pipelines on** (David toggles in Expanso Cloud) | Node lights turn green with a subtle pulse. | Particles flow from each sense into its pipeline and on to the decide node. Counters move in step. |
| **Beats** | Message cards appear as decisions are made. Drone state mirrors the real drone. | Flow continues; cards update. |
| **Fault** (ctrl-click the hear-commands node, or stop it in Cloud) | That node goes grey; "no fresh instruction" on its slot; drone holds position. | Other senses keep flowing. |
| **Recovery** (restart in Cloud) | Light returns green; next shout lands. | Flow resumes. |

A page refresh shows the true current state. No phantom particles.

---

## 4. Hardware

### Drone (decision pending, see part 10)

| Option | Price | For | Against |
|---|---|---|---|
| **Robolink CoDrone EDU** (recommended) | $249 direct from robolink.com, San Diego | Python SDK, laptop control over a USB dongle, optical-flow hover, prop guards, programmable RGB LEDs, about 60 g | no camera, no mic (fine: the room is the senses); order lead time from the US |
| DJI Tello (hedge) | $100 to $150, Amazon, third-party or renewed stock | 720p camera streamed to the laptop for a projected drone-cam; Python SDK; this week | discontinued, no warranty, no LEDs |
| Both | about $350 | rehearsal spare and a projected drone's-eye view | two control stacks to maintain |

Rejected on size, SDK or price: Crazyflie (invisible from row ten), DJI
Neo/Flip/Mini (no usable SDK), DEXI-3 (unknown lead time; revisit if the
date is far out), the rover kits ($350 to $660).

### Edge devices (three senses, three boxes)

| Role | Pipeline | Hardware | Peripherals |
|---|---|---|---|
| Hear the room | hear-clap | Raspberry Pi 5, 8 GB | USB condenser mic facing the audience |
| Hear the volunteer | hear-commands | Raspberry Pi 5, 8 GB | handheld dynamic mic (SM58 class) into a USB audio interface, 3 m cable |
| See the room | see-hands | Raspberry Pi 5 with an AI HAT, or a Jetson Orin Nano Super | wide-angle USB camera (90° or wider), on a stand aimed at the front block |
| Decide and act | fly-drone | the presenter laptop (or a fourth Pi) | drone USB dongle; physical stop key |

Three separate boxes is the honest version of "three devices". Label each
box with its one job in large type; the labels are part of the set.

### Stage and visibility

- Drone LEDs: white on wake, the direction's colour on turn, green breathing
  when parked.
- Stage camera: a phone on a tripod, projected in a corner of the screen as
  a labelled live view of the drone. If the Tello is bought, its own
  camera can serve.
- House lights up for the hands beat; ask the RI for a lighting cue.
- A landing pad and a clear zone on the table. Flight envelope: wake to 1 m, climb to the height the drone holds
  steadily in rehearsal (verify; expect about 2 m), always inside the 4x4 m
  stage box, never over people. Venue rule: under 250 g, no flight over the
  audience; audience sits on three sides.
- Presenter kill: one key on the laptop that lands or cuts motors through
  the SDK, plus the drone's own power switch within reach.

### Network and power

- Travel router for the devices' private network (the devices never touch
  venue Wi-Fi).
- Expanso Cloud reachability via a 5G hotspot; venue internet as backup.
- UK plugs, a powered USB hub, two spare drone batteries, spare props.
- Everything in one carry-on; lithium batteries in cabin baggage.

Rough budget: $900 to $1,100 for the recommended set (CoDrone, three Pi 5
with cases and power, mics and interface, camera, router, spares).

---

## 5. Models, one per device

| Pipeline | Model | Why this one | Output |
|---|---|---|---|
| hear-clap | A small audio event classifier with a clap or applause class (YAMNet class is the baseline), gated by an onset detector so a single clap triggers and speech does not | gives a real confidence number for the screen | `{"sense":"clap","confidence":0.93,"t":...}` |
| hear-commands | A keyword-spotting model on the vocabulary up, down, stop (go optional); all are in Google Speech Commands, so a small CNN trains in minutes | handheld mic only; a crowd shouting is too noisy | `{"sense":"word","value":"up","confidence":0.81,"t":...}` |
| see-hands | A multi-person pose model (YOLO-pose nano class) on a defined front block; a raised hand is a wrist above the shoulder line; count over a 1.5 s window | the only way to count a block of people without identifying anyone | `{"sense":"hands","up":37,"seen":60,"share":0.62,"t":...}` |
| fly-drone | No model. Explicit rules: **land > stop > move > wake**; a decision is fresh for 3 s, stale decisions hold the drone; land needs share > 0.5 with at least 10 people seen | the rule is the story; it must be readable on screen | drone SDK calls, plus `{"act":"land","because":"hands 0.71 > word up"}` |

The hands counter is the riskiest piece. The camera cannot count 300 seats
from a stage. Define a **voting block** (the first rows inside the camera's
field, roughly 60 to 80 people), show "37 of 60 hands" with the percentage,
and say that is the block that votes. If it is unreliable in rehearsal,
shrink the block to one row, or give the volunteer alone the landing vote.

Keyword spotting must be tested with British accents and the venue's
microphone before promising it. Fallback: a two-button clicker labelled
UP and DOWN, announced plainly as a substitution, not hidden.

---

## 6. Software architecture

```
 room mic ──▶ [Pi: hear-clap]     ──┐
 hand mic ──▶ [Pi: hear-commands] ──┼──▶ MQTT (laptop) ──▶ [laptop: fly-drone] ──▶ drone SDK ──▶ drone
 camera   ──▶ [Pi: see-hands]     ──┘          │                    │
                                               └──▶ presenter screen (localhost:1920x1080)
 Expanso Cloud ◀──── deploy, start/stop, logs, input/output monitoring ────▶ all four nodes
```

- **Expanso Edge** on every box, latest from PATH, never pinned, never a
  per-demo copy. Four pipelines, named for what they do: hear-clap,
  hear-commands, see-hands, fly-drone. Each pipeline: capture, one local
  inference step, emit a decision, write to MQTT and to Cloud logs so the
  Cloud logging view and input/output monitoring are part of the show.
- **Expanso Cloud** (the shared expanso-demos workspace, project-local
  profile, credentials from the demos `.env`, never the global profile,
  never the Keychain). `just up` deploys or updates the four pipelines,
  reuses existing ones, starts from zero, leaves them stopped. David starts
  them from Cloud on stage. The screen shows observed Cloud and Edge state.
- **MQTT broker** on the laptop (a real broker, as the NICU demo uses). The
  decide node subscribes to the three sense topics; the screen subscribes
  to all four.
- **Drone control**: the fly-drone pipeline holds the rule table and calls
  the drone SDK through a small Python service run with `uv`. Freshness and
  priority live here and nowhere else.
- **Presenter screen**: localhost only, one 1920x1080 page, no scrollbars.
- **Lifecycle**: `just up`, `just down` (stops every process this demo
  started, deletes its Cloud pipelines and local state, zero orphans),
  `just status`, `just verify`, plus the demo-kit gates (`just test`,
  `just check`, `just motion-check`, `just review-shots`).
- **Repo**: a new demo under the demos workspace, proposed name
  `demo-ri-nervous-system`, stamped from the demo-kit skeleton. Creating the
  repo needs David's call on org and visibility. Build happens in the
  permanent checkout and lands on main.

Decisions published between devices are the only thing that leaves a
device. Frames and audio are processed and discarded on the box that
captured them. This must be verified end to end, including the stage
camera feed, before the GDPR line is said on stage (part 8).

---

## 7. The screen

One frame, left to right, as the invariants require:

```
┌──────────────────────── Expanso Cloud: 4 nodes joined · 4 pipelines ───────────────────┐
│                                                                                         │
│  SENSES                 EXPANSO NODES              DECIDE                  ACT          │
│  room mic  ─ level ──▶ [hear-clap    ●] ──▶┐                                            │
│                                             ├─▶ [fly-drone ●]  rule:  ──▶  drone        │
│  hand mic  ─ level ──▶ [hear-commands●] ──▶┤    land > stop > move > wake  LEDs · state │
│                                             │                              stage cam    │
│  camera    ─ 60 seen ─▶ [see-hands   ●] ──▶┘                                            │
│                                                                                         │
│  MESSAGES (click to inspect)   clap 0.93 · 0.4s ago   up 0.81 · 2.1s ago   hands 62% waiting │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

- Sources are always alive: the mic meters move and the camera's people
  count updates before any pipeline is on. Expanso nodes are grey until
  live, then green with a pulse.
- Particles flow from each sense into its node and on to fly-drone while a
  pipeline is on. Counters match particles.
- The message shelf shows the last decision per sense, large, with its age.
  Click opens the full JSON vertically, syntax coloured. Ctrl-click on a
  node triggers the fault. No click is lost.
- The stage camera view is labelled as the drone view and is a separate feed
  from see-hands.
- Few words, large elements. Everything on screen must be explainable in one
  sentence.

**Palette and iconography** come from the subject, not from a house demo.
Subject: a nervous system on a theatre stage. Starting direction for
`DESIGN_BRIEF.md`: stage black ground, a warm impulse colour for nerve
signals and particles, one reflex colour for land, the drone's own LED
colours for up and down, real microphone, camera and drone drawings.
Composition borrowed from NICU (`demo-intel-nicu-edge`) for the inspect
modal and the message shelf, and from Space Force for the node lights.
Never their palettes. Swap test: cover the words and the board must still
read as this demo.

Per the demo-storytelling gate, three genuinely different 1920x1080
mockups go to David before any demo code; he picks one.

---

## 8. Things that must be true before they are said on stage

1. **No frame and no audio sample leaves its device.** Trace the data path
   for all three senses, including Cloud logs and the stage camera feed.
   Cloud input/output monitoring shows decisions, not media.
2. **The demo survives losing the internet.** Verify that deployed pipelines
   keep running on each box when Expanso Cloud is unreachable, and what the
   screen shows when the Cloud lane goes stale.
3. **Stale means hold.** Cut a sense mid-flight; the drone holds.
4. **Priority is enforced.** "up" during a majority of hands lands the
   drone every time.
5. **The physical stop works** from any state, on the first press.
6. **Real arrival, not animation.** Drone state on screen comes from the
   SDK, not a timer.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Palm-sized drone invisible from row ten | LEDs, stage camera projected, house lights up, the screen tells the story even if the drone is a dot |
| Indoor flight not permitted | Ask the RI now; fallback is the rover-and-card script |
| Crowd noise defeats keyword spotting | Handheld mic for one volunteer only; clicker fallback, announced |
| Hand count unreliable | Voting block; shrink the block; volunteer-only vote as last resort |
| British accents on a US-trained keyword model | Fine-tune with recordings from the team's UK contacts; test with strangers |
| Prop noise from a hovering drone next to the room mic defeats the clap detector | Room mic placed away from the table and pointed at the seats; rehearse with drone noise playing through a speaker before the airframe arrives; clap gate tuned on onset shape, not loudness |
| Drone battery (about 8 minutes of flight) | Fresh battery at the top of the slot, two spares, rehearse the swap |
| Venue internet | 5G hotspot primary; verified offline behaviour (part 8) |
| Lost click or stale screen during the reveal | Motion-check and ten consecutive clean runs before travel |
| Arm's demo overlaps ours | Agree the device-then-devices order and the handoff lines with Eiman and Arm |
| Volunteer freezes | David holds the mic with them; the words are on a card |

## 10. Before the drone arrives: the no-drone track

Roughly four fifths of the build needs no airframe. The drone is the last
link in the chain, behind one interface. Everything else runs for real now.

| Runs for real today | How |
|---|---|
| The three senses | Laptop mic, a handheld mic through a USB interface, the laptop webcam. The Pis ship in days from UK or US stock and slot in when they land. |
| The three models | Clap detector, keyword spotter, hand counter, each emitting its real decision message. Hand counter developed against a recorded audience video, then a real room. |
| Expanso Edge pipelines and Cloud | All four pipelines deployed to the expanso-demos workspace, started and stopped from Cloud, logs and input/output monitoring visible. |
| The decide node | The full rule table (land > stop > turn > wake), freshness, hold-on-stale, with unit tests. |
| The presenter screen | Three mockups, the chosen build, motion-check, review-shots, the five states screenshotted. |
| Drone state on screen | Driven by the drone adapter, whichever adapter is plugged in. |
| Eiman's slide and the handoff lines | Drafted and sent. |

**The drone adapter.** One small interface: take off, land, hover, slide
up or down, set LEDs, report state. Three implementations: CoDrone EDU
(SDK), Tello (djitellopy), and a development simulator used only in rehearsal
without hardware. On stage the real adapter is wired in and nothing on the
presenter surface changes. The simulator is a build tool; it never appears
in audience-facing language.

- **Tello:** `djitellopy-sim` on PyPI exposes the djitellopy API and renders
  the drone in a 3D window, so Tello control code is testable end to end
  today.
- **CoDrone EDU:** no official simulator. A third-party one exists on GitHub
  (10botics), of unknown fidelity. Safer to write our own adapter stub that
  drives the on-screen drone glyph and a small window, since the interface is
  six calls.

**What the no-drone track cannot answer:** visibility from row ten, hover
stability in a draughty theatre, the battery swap, and how loud the props
are next to the room mic. Those wait for the airframe and the venue.

**Dress rehearsal without hardware:** a human claps, shouts and raises
hands at a laptop; the screen and the simulated drone play the whole five
minutes. That is the gate for ordering the second drone or changing beats.
