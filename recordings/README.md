# Recordings

The dress rehearsal with no hardware and no human on stage: the whole
five-minute arc, recorded headless, with every beat checked by machine.

## Reproduce it

```bash
just workspace-init   # once per checkout: enrol the four Edge nodes
just fixtures         # once: build the camera tiles (needs network)
just record-sim
```

`just record-sim` brings the rig up, deploys the four pipelines through
Expanso Cloud and leaves them stopped, opens the screen in an isolated
headless `agent-browser` session at 1920x1080, records while
`ri.conductor` plays the room, checks every beat with `ri.arccheck`, writes
`ri-nervous-system-<stamp>.mp4` here with the sounds the devices heard laid
in at the moment they were played, pulls one frame per beat into `frames/`,
and takes the whole rig down again. A missed beat exits non-zero.

MP4s and frames are gitignored; they stay on the machine that made them.

## What is real in the take

- The clap, the words and the raised hands are real recordings and real
  photos (see `../fixtures/README.md`), played into the devices on the
  arc's clock.
- Every decision is made by the real models (YAMNet, the keyword CNN,
  YOLO11n-pose) inside the real Expanso Edge pipelines, deployed and started
  through Expanso Cloud, carried over a real MQTT broker.
- The drone is the on-screen adapter behind the same interface as the
  CoDrone EDU and Tello adapters. It moves only when fly-drone tells it to.

## Latest take

`ri-nervous-system-20261112-1636.mp4`: 1920x1080 H.264 with AAC sound,
5:05, 33.6 MB (ignored by git; it lives on the machine that recorded it).
14 of 14 beats on time:

| Beat | Due | Landed | What the screen read |
|---|---|---|---|
| Drone dark at the hook | 0:00 | 0:00 | dark, on the table |
| Pipelines started in Expanso Cloud | 0:08 | 0:10 | 4 of 4 pipelines running |
| Clap wakes it | 0:33 | 0:34 | wake: clap; hovering at 1.0 m |
| Shout up climbs | 1:18 | 1:19 | up: word up |
| Shout down descends | 1:44 | 1:45 | down: word down |
| Below majority, waiting | 2:18 | 2:20 | waiting: 7 of 19 |
| Majority lands it | 2:42 | 2:43 | land: hands 0.68; parked |
| Clap wakes it again | 3:00 | 3:01 | wake: clap |
| Conflict, landing beats the shout | 3:16 | 3:19 | land: hands 0.65 > word up |
| Reveal | 3:50 | 3:50 | three messages; MBs stayed, KBs left |
| Handoff | 4:30 | 4:30 | Three devices, three senses, one job. |

Known gap: the hand counter sees 19 to 20 people in the 18-seat block
(overlapping tiles), so shares read a few points low. It still lands on
every majority and waits below one.
