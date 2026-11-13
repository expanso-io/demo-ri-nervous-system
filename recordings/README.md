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

See the table below; each row is filled in after the take is checked.

| Take | Beats on time | Notes |
|---|---|---|
