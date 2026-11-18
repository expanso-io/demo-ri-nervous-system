"""Is the rig ready to fly, right now? One line per thing that can let you down.

    uv run python -m ri.ready [--wait SECONDS]

It reads the running rig, the same state the board draws, twice a few seconds
apart, and answers from what the rig is doing rather than from what should be
true. It changes nothing. Exit status is non-zero when anything fails, so it
can gate a run-through.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ri import disk
from ri.ports import port

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
PIPELINES = ("hear-clap", "hear-commands", "see-hands", "fly-drone")
MIN_BATTERY_PCT = 40
MIN_DISK_FREE = 0.03
GROUND_M = 0.15
SAMPLE_GAP_S = 3.0
State = dict[str, Any]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _wrong_mic(device: dict[str, Any]) -> bool:
    """The rig falls back to another mic without a word when the asked-for one is absent."""
    wanted = str(device.get("mic_wanted") or "").lower()
    if wanted in ("", "auto", "default") or wanted.isdigit():
        return False
    return wanted not in str(device.get("mic") or "").lower()


def judge(
    first: State, last: State, *, stale_code: bool, disk_free: float, disk_used: int = 0
) -> list[Check]:
    """Every check, from two snapshots of the rig taken a few seconds apart."""
    checks: list[Check] = []
    nodes = last.get("nodes", {})
    live = [name for name in PIPELINES if nodes.get(name, {}).get("live")]
    checks.append(
        Check(
            "pipelines",
            len(live) == len(PIPELINES),
            f"{len(live)} of {len(PIPELINES)} running",
            "flip the collection switch on the board, or `just start`",
        )
    )
    checks.append(
        Check(
            "code",
            not stale_code,
            "rig is older than the code" if stale_code else "rig is running the current code",
            "`just again` to restart on the current code",
        )
    )
    for name in ("hear-clap", "hear-commands"):
        before, after = (
            first.get("devices", {}).get(name, {}),
            last.get("devices", {}).get(name, {}),
        )
        flowing = after.get("captured_bytes", 0) > before.get("captured_bytes", 0)
        loud = max([after.get("level", 0), before.get("level", 0), *(after.get("wave") or [0])])
        if not flowing:
            checks.append(Check(f"mic {name}", False, "no audio arriving", "`just again`"))
        elif loud <= 0:
            checks.append(
                Check(
                    f"mic {name}",
                    False,
                    f"{after.get('mic')}: audio is pure silence",
                    "System Settings > Privacy > Microphone: allow your terminal, reopen it",
                )
            )
        elif _wrong_mic(after):
            checks.append(
                Check(
                    f"mic {name}",
                    False,
                    f"asked for {after.get('mic_wanted')}, listening on {after.get('mic')}",
                    "switch on and plug in the mic receiver, then `just again`",
                )
            )
        else:
            checks.append(Check(f"mic {name}", True, f"{after.get('mic')}: hearing the room"))
    cam_before = first.get("devices", {}).get("see-hands", {})
    cam = last.get("devices", {}).get("see-hands", {})
    hands = last.get("latest", {}).get("hands") or {}
    if cam.get("captured_bytes", 0) <= cam_before.get("captured_bytes", 0):
        checks.append(Check("camera", False, "no frames arriving", "`just again`"))
    elif not cam.get("arms"):
        seen = hands.get("seen", 0)
        checks.append(
            Check(
                "camera",
                False,
                f"{seen} people seen, but no one with both shoulders and both wrists in view",
                "step back or raise the camera until both arms at full stretch are in frame",
            )
        )
    else:
        checks.append(Check("camera", True, "sees you, shoulders and both wrists"))
    drone = last.get("drone", {})
    phase, altitude = drone.get("phase"), float(drone.get("altitude_m") or 0.0)
    if not phase:
        checks.append(Check("drone", False, "no word from the flight service", "`just again`"))
    elif phase in ("dark", "parked") and altitude <= GROUND_M:
        checks.append(Check("drone", True, f"on the floor, {phase}, ready to wake"))
    elif altitude <= GROUND_M:
        checks.append(
            Check(
                "drone",
                False,
                f"rig thinks it is {phase}, but it is on the floor",
                "press Esc on the board, wait for it to read parked",
            )
        )
    else:
        checks.append(
            Check("drone", False, f"in the air: {phase} at {altitude:.1f} m", "press Esc to land")
        )
    battery = drone.get("battery")
    if battery is not None:
        checks.append(
            Check(
                "battery",
                battery >= MIN_BATTERY_PCT,
                f"{battery}%",
                f"swap or charge: below {MIN_BATTERY_PCT}% a run can end early",
            )
        )
    checks.append(
        Check(
            "disk",
            disk_free >= MIN_DISK_FREE,
            f"{disk_free:.0%} free",
            "free some space: the Edge agents throttle when the disk is nearly full",
        )
    )
    checks.append(
        Check(
            "disk budget",
            disk_used <= disk.BUDGET_BYTES,
            f"{disk_used / disk.GB:.1f} of {disk.BUDGET_BYTES // disk.GB} GB",
            "`just disk` shows what is large; `just disk-trim` deletes what can be rebuilt",
        )
    )
    return checks


def fetch(url: str) -> State:
    with urllib.request.urlopen(url, timeout=3) as response:
        body: State = json.loads(response.read())
    return body


def code_is_newer_than_rig() -> bool:
    """True when a source file changed after the running rig was started."""
    pid = RUNTIME / "flight.pid"
    if not pid.exists():
        return False
    started = pid.stat().st_mtime
    return any(path.stat().st_mtime > started for path in (ROOT / "ri").rglob("*.py"))


def survey(url: str) -> list[Check]:
    first = fetch(url)
    time.sleep(SAMPLE_GAP_S)
    usage = shutil.disk_usage(ROOT)
    return judge(
        first,
        fetch(url),
        stale_code=code_is_newer_than_rig(),
        disk_free=usage.free / usage.total,
        disk_used=disk.footprint(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=0.0, help="keep trying this many seconds")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{port('PORT')}/api/state"
    deadline = time.monotonic() + args.wait
    while True:
        try:
            checks = survey(url)
        except OSError as error:
            checks = [Check("screen", False, f"not reachable ({error})", "`just up` first")]
        if all(c.ok for c in checks) or time.monotonic() >= deadline:
            break
        time.sleep(2.0)
    for c in checks:
        print(f"  {'ok  ' if c.ok else 'FAIL'}  {c.name:18} {c.detail}")
        if not c.ok and c.fix:
            print(f"        {'':18} fix: {c.fix}")
    failed = [c.name for c in checks if not c.ok]
    print("READY" if not failed else f"NOT READY: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
