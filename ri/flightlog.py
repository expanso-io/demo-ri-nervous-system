"""The flight recorder: everything the rig says on its bus, kept for later.

    uv run python -m ri.flightlog            # the service; `just up` starts it
    uv run python -m ri.flightlog show [N]   # the Nth most recent flight (1 = last)

Every decision a sense sent, every act the drone took, the drone's own state
ten times a second (with its barometer and range sensor), and twice a second
what the camera read off the presenter's arms. One file per rig start, in
.runtime/flights/, one JSON line per message. Nothing here is audio or video:
the bus never carries either.

`show` cuts a file into flights, from a wake to the drone being down again,
and prints each one as a timeline: what was asked, how long the drone took to
start and to finish, how high it got against how high it was sent, and what
its two height sensors said on the way.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ri import room
from ri.bus import Bus
from ri.ports import RUNTIME

FLIGHTS = RUNTIME / "flights"
KEEP_FILES = 60
# Device telemetry is ten a second and mostly level meters; two is plenty.
DEVICE_EVERY_S = 0.5
# Big arrays that only draw the board.
DROP_FIELDS = ("wave", "pulses")


class Recorder:
    def __init__(self, folder: Path = FLIGHTS) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / time.strftime("%Y%m%d-%H%M%S.jsonl")
        self.out = self.path.open("a", buffering=1)
        self.lock = threading.Lock()
        self.device_at: dict[str, float] = {}
        self.write("rig", {"room": room.current().name, "summary": room.current().summary()})
        prune(folder)

    def write(self, topic: str, body: Any) -> None:
        line = json.dumps({"t": round(time.time(), 3), "topic": topic, "body": body})
        with self.lock:
            self.out.write(line + "\n")

    def take(self, topic: str, payload: bytes) -> None:
        try:
            body = json.loads(payload)
        except ValueError:
            return
        if topic.startswith("ri/device/"):
            now = time.monotonic()
            if now - self.device_at.get(topic, 0.0) < DEVICE_EVERY_S:
                return
            self.device_at[topic] = now
            if isinstance(body, dict):
                body = {k: v for k, v in body.items() if k not in DROP_FIELDS}
        self.write(topic, body)

    def close(self) -> None:
        with self.lock:
            self.out.close()


def prune(folder: Path, keep: int = KEEP_FILES) -> None:
    for old in sorted(folder.glob("*.jsonl"))[:-keep]:
        old.unlink(missing_ok=True)


def serve() -> int:
    recorder = Recorder()
    bus = Bus("flightlog", subscriptions=("ri/#",))
    bus.on(recorder.take)
    bus.start()
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    print(f"recording to {recorder.path}", flush=True)
    done.wait()
    bus.stop()
    recorder.close()
    return 0


# ------------------------------------------------------------------ reading


def load(path: Path) -> list[dict[str, Any]]:
    rows = []
    for raw in path.read_text().splitlines():
        try:
            rows.append(json.loads(raw))
        except ValueError:
            continue
    return rows


def flights(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cut a recording into flights: a wake up to the drone being down again."""
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    flew = False
    for row in rows:
        body = row.get("body") or {}
        if row["topic"] == "ri/act" and body.get("act") == "wake":
            current, flew = [row], False
            out.append(current)
            continue
        if current is None:
            continue
        current.append(row)
        if row["topic"] == "ri/drone/state":
            flew = flew or bool(body.get("airborne"))
            if flew and body.get("phase") in ("dark", "parked"):
                current = None
    return out


def _raw(state: dict[str, Any], key: str) -> float | None:
    raw = state.get("raw") or {}
    value = raw.get(key)
    return float(value) if value is not None else None


def timeline(flight: list[dict[str, Any]]) -> list[str]:
    """One line per thing worth knowing, timed from the wake."""
    t0 = flight[0]["t"]
    lines: list[str] = []
    phase = None
    move_began: tuple[float, str, float] | None = None
    asked_at: float | None = None
    peak = 0.0
    base_elev: float | None = None
    for row in flight:
        t, topic, body = row["t"] - t0, row["topic"], row.get("body") or {}
        if topic == "ri/act":
            act = body.get("act")
            if act == "hold" and not body.get("inputs"):
                continue
            asked_at = row["t"] if act != "hold" else asked_at
            lines.append(f"{t:6.1f}s  {act:5} {body.get('because', '')}  ({body.get('from', '')})")
        elif topic == "ri/drone/state":
            alt = float(body.get("altitude_m", 0.0))
            peak = max(peak, alt)
            elev = _raw(body, "elevation_m")
            # The barometer reads above sea level: heights are from the first
            # reading of the flight, taken with the drone still on the floor.
            if base_elev is None and elev is not None:
                base_elev = elev
            new = body.get("phase")
            if new == phase:
                continue
            leds = body.get("leds") or {}
            note = ""
            if new in ("taking_off", "climbing", "descending", "cruising"):
                move_began = (row["t"], new, alt)
                if asked_at is not None:
                    note = f"  started {row['t'] - asked_at:.1f} s after the ask"
            elif new == "hovering" and move_began is not None:
                took = row["t"] - move_began[0]
                note = f"  {move_began[1]} took {took:.1f} s, {move_began[2]:.2f} -> {alt:.2f} m"
                move_began = None
            baro = ""
            if elev is not None and base_elev is not None:
                baro = f"  baro {elev - base_elev:+.2f} m"
            rng = _raw(body, "range_m")
            sensed = f"  range {rng:.2f} m" if rng is not None else ""
            lines.append(
                f"{t:6.1f}s  {new:10} at {alt:.2f} m (goal {float(body.get('target_m', 0)):.2f})"
                f"  led {leds.get('color', '?')}/{leds.get('mode', '?')}{baro}{sensed}{note}"
            )
            phase = new
        elif topic == "ri/device/see-hands":
            readout = body.get("arms_readout")
            if readout and readout.get("signal"):
                lines.append(f"{t:6.1f}s  arms  read as {readout['signal']}")
    lines.append(f"highest {peak:.2f} m, {flight[-1]['t'] - t0:.1f} s from wake to down")
    return lines


def show(nth: int, folder: Path = FLIGHTS) -> int:
    files = sorted(folder.glob("*.jsonl"))
    found: list[tuple[Path, list[dict[str, Any]]]] = []
    for path in reversed(files):
        for flight in reversed(flights(load(path))):
            found.append((path, flight))
            if len(found) >= nth:
                break
        if len(found) >= nth:
            break
    if len(found) < nth:
        print(f"only {len(found)} flight(s) recorded in {folder}")
        return 1
    path, flight = found[nth - 1]
    rig = next((r for r in load(path) if r["topic"] == "rig"), None)
    when = time.strftime("%H:%M:%S", time.localtime(flight[0]["t"]))
    print(f"flight at {when} from {path.name}: {rig['body']['summary'] if rig else ''}")
    print("\n".join(timeline(flight)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="serve", choices=("serve", "show"))
    ap.add_argument("nth", nargs="?", type=int, default=1)
    args = ap.parse_args()
    return serve() if args.cmd == "serve" else show(args.nth)


if __name__ == "__main__":
    sys.exit(main())
