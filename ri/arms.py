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

    just arms --save

also keeps each frame in .runtime/arms/<time>/, with the shoulders, elbows
and wrists of the person the arms were read from drawn on it, and the line
beside it in readout.txt: what the camera saw, for working out later why a
signal was or was not read.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ri.ports import RUNTIME, url
from ri.senses import hands

# Shoulders, elbows, wrists, as hands.POINTS lists them.
LIMBS = ((0, 1), (0, 2), (2, 4), (1, 3), (3, 5))


def _wrist(w: list[float]) -> str:
    across, down, travel, box = w
    return f"across {across:+5.2f}  down {down:+5.2f}  travel {travel:4.2f}  box {box:4.2f}"


def line(readout: dict[str, Any] | None, arms: bool) -> str:
    if not arms:
        return "no shoulders and both wrists in view"
    if readout is None:
        return "arms in view, filling the window"
    signal = readout.get("signal") or "-"
    return f"{signal:8} | A {_wrist(readout['a'])} | B {_wrist(readout['b'])}"


def save(folder: Path, n: int, text: str, points: list[list[int]] | None) -> None:
    """Keep the camera frame with the chosen person's arms drawn, and the readout."""
    import cv2
    import numpy as np

    try:
        with urllib.request.urlopen(url("HANDS_DEVICE_PORT", "/preview.jpg"), timeout=2) as r:
            data = r.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return
    if points:
        for a, b in LIMBS:
            cv2.line(image, tuple(points[a]), tuple(points[b]), (0, 200, 255), 4)
        for x, y in points:
            cv2.circle(image, (x, y), 8, (0, 0, 255), -1)
    cv2.putText(image, text[:90], (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
    cv2.putText(image, text[:90], (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.imwrite(str(folder / f"{n:04d}.jpg"), image)
    with (folder / "readout.txt").open("a") as out:
        out.write(f"{n:04d}  {text}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="keep each frame, arms drawn")
    args = ap.parse_args()
    folder: Path | None = None
    if args.save:
        folder = RUNTIME / "arms" / time.strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        print(f"saving frames to {folder}")
    n = 0
    target = url("HANDS_DEVICE_PORT", "/status")
    print(
        f"wave {hands.WAVE_TRAVEL} travel, back near the start; raised: down < "
        f"-{hands.RAISE_REACH}; point: an arm across beyond +-{hands.POINT_REACH} "
        f"and down under {hands.POINT_DROP}; "
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
            text = line(body.get("arms_readout"), bool(body.get("arms")))
            print(text, flush=True)
            if folder is not None:
                n += 1
                save(folder, n, text, body.get("marshal_px"))
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
