# The Drone With a Nervous System

**Three small devices, one tiny model each, and a few bytes between them are
enough to let a room full of people fly a drone without a data centre.**

A clap wakes the drone, a shouted word moves it, a show of hands lands it.
Each sense is its own box with one model and one Expanso Edge pipeline that
turns raw sound or pictures into one small decision. A fourth pipeline beside
the drone applies one rule table (land > stop > move > wake) and flies it.
Expanso Cloud deploys all four and shows they are running. The design is in
[`docs/BUILD_SPEC.md`](docs/BUILD_SPEC.md).

```bash
just workspace-init   # once: enrol four Edge nodes (identities stay in ./.expanso-edge)
just up               # rig up, jobs deployed in Expanso Cloud and left stopped
just run              # drive the five-minute arc against the running rig
just down             # stop everything, delete the Cloud jobs
just record-sim       # up, record the whole arc headless, check every beat, down
just check            # lint, types, tests, pipeline rules, UI rules, motion
```

## What runs

| Piece | Where | What it does |
|---|---|---|
| `ri/device.py` | each sense box | captures sound windows or camera frames, posts them to its local pipeline, serves the model at `/infer` |
| `pipelines/hear-clap.yaml` | hear-clap node | sound window → YAMNet → a clap message on `ri/sense/clap` |
| `pipelines/hear-commands.yaml` | hear-commands node | sound window → keyword model → up/down/stop on `ri/sense/word` |
| `pipelines/see-hands.yaml` | see-hands node | camera frame → pose model → hand count on `ri/sense/hands` |
| `pipelines/fly-drone.yaml` | fly-drone node | every sense message → the rule table → one act on `ri/act` |
| `ri/flight.py` | beside the drone | the rule table (`ri/decide.py`) and the drone adapter |
| `ri/drone/` | | one adapter interface: an on-screen drone, CoDrone EDU and Tello |
| `ri/screen.py`, `dashboard/` | presenter laptop | the audience screen, localhost only |
| `ri/conductor.py` | | plays the room into the devices on the arc's clock |

Raw sound and frames never leave the box that captured them; only decisions
cross the bus. On stage each sense node is its own small computer; for
rehearsal all four Edge agents run on one laptop with the same pipelines.

Presenter keys on the screen: **Escape** lands the drone now, from any state.
**Ctrl-click** a sense node cuts that device's link (the fault beat); again
restores it.

## Drone adapters

`RI_DRONE=sim|codrone|tello` (or `python -m ri.flight --drone ...`). The
CoDrone EDU and Tello adapters are written against their SDKs and marked
`CONFIRM` where a call needs checking on the real airframe.

## Models and fixtures

See [`fixtures/README.md`](fixtures/README.md) for every model and input and
its licence. Camera fixtures are built locally with `just fixtures` and are
never committed.

## Recording

[`recordings/README.md`](recordings/README.md) has the one-command take and
how it is checked.
