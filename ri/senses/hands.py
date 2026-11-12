"""see-hands: count raised hands in the voting block, never identify anyone.

A multi-person pose model (YOLO11n-pose, about 6 MB) finds people and their
shoulders and wrists. A hand is up when a wrist is above that person's
shoulder line. Counts are smoothed over a 1.5 s window. Frames are processed
and dropped on this device; only the counts leave it.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ri.types import Array

MODELS = Path(__file__).resolve().parents[2] / "models"
WEIGHTS = MODELS / "cache" / "yolo11n-pose.pt"
WINDOW_S = 1.5
KEYPOINT_MIN = 0.3
PERSON_MIN = 0.35
# COCO keypoints: 5/6 shoulders, 9/10 wrists.
SHOULDERS = (5, 6)
WRISTS = (9, 10)


@dataclass(frozen=True)
class FrameCount:
    up: int
    seen: int
    t: float


def hands_up(keypoints: Array) -> int:
    """Raised hands among people, one vote per person.

    keypoints: (people, 17, 3) array of x, y, confidence in image pixels.
    """
    votes = 0
    for person in keypoints:
        shoulders = [person[i] for i in SHOULDERS if person[i][2] >= KEYPOINT_MIN]
        wrists = [person[i] for i in WRISTS if person[i][2] >= KEYPOINT_MIN]
        if not shoulders or not wrists:
            continue
        shoulder_line = min(float(s[1]) for s in shoulders)
        if min(float(w[1]) for w in wrists) < shoulder_line:
            votes += 1
    return votes


class HandCounter:
    def __init__(self, weights: Path = WEIGHTS, imgsz: int = 960) -> None:
        from ultralytics import YOLO  # type: ignore[attr-defined]

        self.model: Any = YOLO(str(weights))
        self.imgsz = imgsz
        self.window: deque[FrameCount] = deque()

    def count_frame(self, image: Array, now: float | None = None) -> FrameCount:
        result = self.model(image, imgsz=self.imgsz, conf=PERSON_MIN, verbose=False)[0]
        keypoints = result.keypoints.data.cpu().numpy() if result.keypoints is not None else []
        seen = len(keypoints)
        up = hands_up(np.asarray(keypoints)) if seen else 0
        return FrameCount(up, seen, time.time() if now is None else now)

    def add(self, count: FrameCount) -> FrameCount:
        """Add a frame and return the window's smoothed count."""
        self.window.append(count)
        while self.window and count.t - self.window[0].t > WINDOW_S:
            self.window.popleft()
        ups = sorted(c.up for c in self.window)
        seens = sorted(c.seen for c in self.window)
        mid = len(ups) // 2
        return FrameCount(ups[mid], seens[mid], count.t)
