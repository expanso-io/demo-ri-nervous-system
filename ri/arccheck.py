"""Watch a run and check every beat of the arc happened, and when.

    uv run python -m ri.arccheck watch --seconds 310   # alongside the conductor
    uv run python -m ri.arccheck report                # judge the last run

`watch` samples the screen's own state four times a second into
.runtime/arc-trace.jsonl. `report` lines that up against the conductor's
log and checks each beat from docs/BUILD_SPEC.md part 2 against the time it
is meant to land. Exits non-zero if any beat is missing or late, so a take
that went wrong never gets called good.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ri.ports import RUNTIME, url

TRACE = RUNTIME / "arc-trace.jsonl"
CONDUCTOR = RUNTIME / "conductor.jsonl"

Sample = dict[str, Any]


def watch(seconds: float) -> int:
    RUNTIME.mkdir(exist_ok=True)
    end = time.time() + seconds
    with TRACE.open("w") as out:
        while time.time() < end:
            try:
                with urllib.request.urlopen(url("PORT", "/api/state"), timeout=2) as r:
                    s = json.loads(r.read())
            except OSError:
                time.sleep(0.25)
                continue
            act = s.get("act") or {}
            sample = {
                "t": time.time(),
                "beat": s.get("scene", {}).get("beat"),
                "live": sorted(n for n, v in s.get("nodes", {}).items() if v.get("live")),
                "phase": s.get("drone", {}).get("phase"),
                "alt": s.get("drone", {}).get("altitude_m"),
                "target": s.get("drone", {}).get("target_m"),
                "led": s.get("drone", {}).get("leds", {}).get("color"),
                "act": act.get("act"),
                "because": act.get("because"),
                "waiting": act.get("waiting"),
                "act_t": act.get("seen_at"),
                "latest": {k: v.get("raw") for k, v in s.get("latest", {}).items()},
                "acts": [
                    [a.get("act"), a.get("because"), a.get("seen_at")] for a in s.get("acts", [])
                ],
            }
            out.write(json.dumps(sample) + "\n")
            out.flush()
            time.sleep(0.25)
    return 0


@dataclass(frozen=True)
class Beat:
    name: str
    start: float
    end: float
    test: Callable[[Sample], bool]
    act: tuple[str, str] | None = None


def acted(act: str, because: str = "") -> tuple[str, str]:
    return (act, because)


def never(_: Sample) -> bool:
    return False


BEATS: tuple[Beat, ...] = (
    Beat("drone dark at the hook", 0, 8, lambda s: s["phase"] == "dark"),
    Beat("all four pipelines live", 8, 30, lambda s: len(s["live"]) == 4),
    Beat("clap wakes it", 33, 40, never, acted("wake", "clap")),
    Beat("hovering at 1.0 m after the wake", 36, 75, lambda s: s["phase"] == "hovering"),
    Beat("shout up climbs", 78, 84, never, acted("up", "word up")),
    Beat("shout down descends", 104, 110, never, acted("down", "word down")),
    Beat("below majority: waiting", 140, 162, lambda s: s["act"] == "hold" and bool(s["waiting"])),
    Beat("majority lands it", 162, 170, never, acted("land", "hands")),
    Beat("parked after landing", 166, 180, lambda s: s["phase"] == "parked"),
    Beat("clap wakes it again", 180, 187, never, acted("wake", "clap")),
    Beat("conflict: landing beats the shout", 196, 205, never, acted("land", "> word up")),
    Beat("parked for the reveal", 225, 232, lambda s: s["phase"] == "parked"),
    Beat("reveal on screen", 230, 234, lambda s: s["beat"] == "reveal"),
    Beat("handoff on screen", 270, 274, lambda s: s["beat"] == "handoff"),
)


def report(_: argparse.Namespace | None = None) -> int:
    start = json.loads(CONDUCTOR.read_text().splitlines()[0])
    t0, compress = float(start["t"]), float(start.get("compress", 1.0))
    samples = [json.loads(line) for line in TRACE.read_text().splitlines()]
    failed = 0
    for beat in BEATS:
        lo, hi = t0 + beat.start / compress, t0 + beat.end / compress
        if beat.act is not None:
            acts = {a[2]: a for s in samples for a in s.get("acts", []) if a[2]}
            found = sorted(
                (
                    a
                    for a in acts.values()
                    if lo <= a[2] <= hi and a[0] == beat.act[0] and beat.act[1] in (a[1] or "")
                ),
                key=lambda a: a[2],
            )
            if found:
                print(f"ok    {(found[0][2] - t0) * compress:6.1f}s  {beat.name}  ({found[0][1]})")
            else:
                failed += 1
                near = sorted(
                    {(a[0], a[1], round((a[2] - t0) * compress, 1)) for a in acts.values()},
                    key=lambda a: abs(a[2] - beat.start),
                )[:3]
                print(f"MISS  {beat.start:6.1f}s  {beat.name}; nearest acts {near}")
            continue
        hits = [s for s in samples if lo <= s["t"] <= hi and beat.test(s)]
        if hits:
            first = (hits[0]["t"] - t0) * compress
            detail = hits[0]["because"] if hits[0]["act"] else hits[0]["phase"]
            print(f"ok    {first:6.1f}s  {beat.name}  ({detail})")
        else:
            failed += 1
            window = [s for s in samples if lo <= s["t"] <= hi]
            seen = {(s["act"], s["because"], s["phase"]) for s in window}
            print(f"MISS  {beat.start:6.1f}s  {beat.name}; saw {sorted(seen, key=str)[:4]}")
    print(f"{len(BEATS) - failed} of {len(BEATS)} beats on time")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch")
    w.add_argument("--seconds", type=float, default=310)
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "watch":
        return watch(args.seconds)
    return report(args)


if __name__ == "__main__":
    raise SystemExit(main())
