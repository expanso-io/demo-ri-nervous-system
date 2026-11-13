# Design brief: The Drone With a Nervous System

## Subject material

A small drone on a lecture-theatre stage that has no brain of its own. The
audience is its nervous system: a clap wakes it, a shouted word moves it, a
show of hands lands it. Three small boxes each own one sense (room mic,
handheld mic, camera on the front block) and run one model. Each runs an
Expanso Edge node that turns raw sound or pictures into one small decision.
A fourth node beside the drone, fly-drone, applies one rule table and flies
it. Expanso Cloud deploys the four pipelines and shows they are running.

The real source is the room: sound and faces. The destination is the drone.
What travels between them is a handful of decisions.

## Palette rationale

The screen renders standalone on a projector in a dark theatre, so it carries
its own ground: stage black, warmed slightly (`#0a0908`), lit by one soft
spotlight from above. Dark is chosen for the room, not by default.

- **Ember `#ff9a5a`** is the nerve impulse: a decision travelling. It is the
  only bright thing that moves between nodes.
- **Crimson `#ff5d6c`** is the reflex: land. It appears only when the safety
  rule fires.
- **Blue `#74b8ff`** and **amber `#f2c14e`** are the drone's own LED colours
  for up and down, so the screen and the airframe agree.
- **Green `#8fe0a0`** means running: a live node, a parked drone.
- Raw sound and frames are drawn in warm grey and never leave their node.

Type: Newsreader (OFL, vendored) for the one line the audience is asked to
act on, like a playbill. Monospace for every number and message, because the
messages are the point.

## Scenario cast

- **Sources:** room mic, handheld mic, camera, each always sensing.
- **Expanso nodes:** hear-clap, hear-commands, see-hands. Raw windows flow
  into each and stop there. Grey until its pipeline runs, then green.
- **Decide:** fly-drone, an Expanso node with the rule table on its face
  (land > stop > move > wake), the winning rule lit and a beaten rule struck.
- **Act:** the drone, drawn from its own reported state: altitude, target,
  LEDs, phase.
- **Orchestrator:** Expanso Cloud above the nodes, with dashed control lanes
  carrying deploy signals down and status up.

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
