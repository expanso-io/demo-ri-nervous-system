# Recording script: the drone with a nervous system

One take, five minutes, seven beats. This is the rehearsal take with no
human on stage: `ri.conductor` plays the room cues (a clap, a volunteer's
words, hands going up) into the real devices, and everything after that is the
real models, the real Expanso pipelines and the real rule table. The spoken
lines are David's, for when a human narrates over it.

## Before you roll

```bash
just up      # rig up with fixtures, four jobs deployed in Expanso Cloud and stopped
just up-cam  # optional: live OBSBOT Tiny 2 Lite camera feeding see-hands + stage cam
just down
```

Do the warm-up once. The first run of a session loads three models and starts
four Edge agents; neither belongs in a take.

Recording surface: `http://127.0.0.1:20170/` at 1920x1080. The headless take
is `just record-sim` (see `recordings/README.md`).

**Timing.** Offsets are seconds after the conductor's start. Each beat's time
comes from BUILD_SPEC part 2; `ri.arccheck` fails the take if a beat misses
its window.

---

## Beat 1: Hook · 0:00-0:30

**Screen state before you speak.** Drone dark on the table. Both live stage
senses are alive (waveform and the `CLAP` / `UP` / `DOWN` / `STOP` chips on
`mic`, the hands-up bar and live view on `camera`); the Expanso nodes are grey
and the prompt line reads the hook across the top of the screen. At 0:08 the four pipelines start in Expanso Cloud and
the nodes turn green.

> Every AI you have used lives in a building the size of a football pitch, a
> long way from here. This one's intelligence lives on this table. And in you.

---

## Beat 2: Clap to wake it · 0:30-1:15

**Screen state before you speak.** At 0:33 the room claps. The `CLAP` chip
lights on `mic`, hear-clap shows `clap 0.96` and sends one impulse down its
nerve to fly-drone, the wake rule lights `WINS`, and the drone rises to hover
at 1.0 m with white LEDs.

> That device heard a clap and sent one word. Not the sound. One word.

---

## Beat 3: Shout up or down · 1:15-2:15

**Screen state before you speak.** A volunteer says up, up, down, down, up.
Each recognized word lights its chip (`UP`, `DOWN`, or `STOP`), moves the
`move` rule to `WINS`, and steers climbs in blue and descents in amber
inside its 1.0 to 2.0 m envelope.

> This device knows three words. It does not know English. It does not need
> to.

---

## Beat 4: Hands up to land it · 2:15-3:15

**Screen state before you speak.** Some hands go up: the camera meter swaps to
an amber bar below the 50% tick, the `land` rule reads `NOT YET`, `fly-drone`
reads "waiting: N of M", and the drone holds. Then more than half go up: the bar
crosses the tick in red, `land` lights `WINS`, and it lands, parks, LEDs
breathing green. At 3:00 a clap wakes it again.

> Half of you want it down. It will not guess.

---

## Beat 5: Conflict · 3:15-3:50

**Screen state before you speak.** Hands go up and the volunteer shouts up at
the same time. Both `land` (`WINS`) and `move` (`LOST`, struck through) appear
in the rule table, and the reason reads `hands <share> > word up` (for example
`hands 0.72 > word up`). The drone lands.

> Two instructions arrived. We wrote one rule: landing wins. That rule is the
> whole safety system.

---

## Beat 6: Reveal · 3:50-4:30

**Screen state before you speak.** Three message cards show the last clap,
the last word, and the last hand count, each with its byte size. Under them, two bars on one scale: what stayed on the devices (MB,
a full bar) and what left them (KB, a sliver).

> Here is everything that travelled between these devices during that run. No
> picture of you left that camera. No sound left that microphone. Three
> decisions did.

---

## Beat 7: Handoff · 4:30-5:00

**Screen state before you speak.** "Three devices, three senses, one job."

> Three devices, three senses, one job, and nothing left the room but
> decisions.

---

## Required lines

- "That device heard a clap and sent one word."
- "Landing wins. That rule is the whole safety system."
- "Nothing left the room but decisions."

## Prohibited

Never spoken or shown: the name of the open-source project underneath,
program names, classification-adjacent claims, third-party vendor names,
details of the lecture this demo appears in. Every quantitative claim either
cites a public source in `docs/RESEARCH.md` or is introduced as
representative.

## After the take

```bash
uv run python -m ri.arccheck report
```

Non-zero exit means a beat was missed and the take is not usable.
