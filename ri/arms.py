"""Watch what the camera reads off your arms, to tune the signals.

    just arms

Stand where you will signal from, with the rig up, and do each signal. Twice
a second one line shows the last 1.5 s of each wrist, in shoulder widths:

    across  where it sits sideways from the middle of your shoulders
    down    how far below the shoulder line (up is negative)
    travel  how far it moved in the window
    box     the size of the box it stayed inside

and the signal those numbers make, if any. The thresholds they are read
against live at the top of ri/senses/hands.py.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from ri.ports import url
from ri.senses import hands


def _wrist(w: dict[str, Any]) -> str:
    return (
        f"across {w['across']:+5.2f}  down {w['down']:+5.2f}  "
        f"travel {w['travel']:4.2f}  box {w['extent']:4.2f}"
    )


def line(readout: dict[str, Any] | None, arms: bool) -> str:
    if not arms:
        return "no shoulders and both wrists in view"
    if readout is None:
        return "arms in view, filling the window"
    signal = readout.get("signal") or "-"
    return f"{signal:8} | A {_wrist(readout['a'])} | B {_wrist(readout['b'])}"


def main() -> int:
    target = url("HANDS_DEVICE_PORT", "/status")
    print(
        f"wave {hands.WAVE_TRAVEL} travel, back near the start; raised: down < "
        f"-{hands.RAISE_REACH}; side: across beyond +-{0.5 + hands.SIDE_REACH:.1f}; "
        f"jazz: travel >= {hands.JAZZ_TRAVEL} in a box <= {hands.JAZZ_EXTENT}"
    )
    try:
        while True:
            try:
                with urllib.request.urlopen(target, timeout=2) as response:
                    body = json.loads(response.read())
            except (urllib.error.URLError, TimeoutError, OSError):
                print("see-hands device not answering: just up first", flush=True)
                time.sleep(2.0)
                continue
            print(line(body.get("arms_readout"), bool(body.get("arms"))), flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
