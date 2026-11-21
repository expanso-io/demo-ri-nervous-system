"""see-hands: count raised hands in the voting block, never identify anyone.

A multi-person pose model (YOLO11n-pose, about 6 MB) finds people and their
shoulders and wrists. A hand is up when a wrist is above that person's
shoulder line. Counts are smoothed over a 1.5 s window. Frames are processed
and dropped on this device; only the counts leave it.
"""

from __future__ import annotations

import itertools
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from ri.types import Array

MODELS = Path(__file__).resolve().parents[2] / "models"
WEIGHTS = MODELS / "cache" / "yolo11n-pose.pt"
WINDOW_S = 1.5
KEYPOINT_MIN = 0.3
PERSON_MIN = 0.5
HAND_MARGIN_RATIO = 0.05
# COCO keypoints: 5/6 shoulders, 7/8 elbows, 9/10 wrists.
SHOULDERS = (5, 6)
ARMS = ((7, 9), (8, 10))
WRISTS = (9, 10)
ELBOWS = (7, 8)
# A keypoint this close to the frame's edge (of its shorter side) means the
# person is cut off by it.
EDGE_RATIO = 0.02
# Facing the camera, shoulders are about 1.2 upper arms wide; turned to point
# sideways they shrink to 0.2-0.6 of one on camera (rehearsal frames). So arms
# are measured against the larger of the shoulders and 1.2 upper arms, which
# is the shoulder width facing the camera and does not collapse turned away.
ARM_SCALE = 1.2
POINTS = (*SHOULDERS, *ELBOWS, *WRISTS)
Roi = tuple[float, float, float, float]
RAISE_REACH = 0.3
# A wave: a wrist that travels this many shoulder widths inside the window.
WAVE_TRAVEL = 1.2
WAVE_MIN_FRAMES = 4
# Pointing: over the last POINT_FRAMES frames (10 a second), both wrists on
# one side, each past POINT_EACH and on average past POINT_REACH shoulder
# widths from the middle, neither lower than POINT_DROP below the shoulders
# (hanging arms read about 1.5). Rehearsal frames of a natural point read
# about 2 across and 0.3 down.
POINT_FRAMES = 6
POINT_EACH = 0.5
POINT_REACH = 1.3
POINT_DROP = 1.2
# Jazz hands: both wrists up, one each side, each shaking inside a small box.
# The model sees wrists, not fingers, so the shake is what it reads: travel
# of at least JAZZ_TRAVEL inside a box no wider than JAZZ_EXTENT. A beckon
# travels as far but over a box twice the size; still arms barely travel.
JAZZ_TRAVEL = 0.5
JAZZ_EXTENT = 0.45
Arms = tuple[float, float, float, float]


@dataclass(frozen=True)
class HandBox:
    x1: int
    y1: int
    x2: int
    y2: int
    up: bool
    conf: float


@dataclass(frozen=True)
class FrameCount:
    up: int
    seen: int
    t: float
    raw_up: int | None = None
    raw_seen: int | None = None
    gesture: str | None = None
    arms: Arms | None = None


def parse_roi(spec: str | Roi | None) -> Roi | None:
    """Parse 'x1,y1,x2,y2' (normalized [0,1] or pixel coords) into a bounding box."""
    if spec is None:
        return None
    if not isinstance(spec, str):
        return spec
    raw = spec.strip()
    if not raw or raw.lower() in ("none", "full", "all"):
        return None
    parts = [float(p.strip()) for p in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"ROI must be 4 comma-separated numbers x1,y1,x2,y2: {spec!r}")
    x1, y1, x2, y2 = parts
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"ROI must satisfy x2 > x1 and y2 > y1: {spec!r}")
    return (x1, y1, x2, y2)


def filter_people(
    keypoints: Array,
    *,
    roi: Roi | None = None,
    shape: tuple[int, int] | None = None,
) -> Array:
    """Keep people with visible shoulders inside the configured voting-block ROI."""
    if len(keypoints) == 0:
        return np.zeros((0, 17, 3), dtype=np.float32)
    box = None
    if roi is not None:
        x1, y1, x2, y2 = roi
        if shape is not None and max(x2, y2) <= 1.0 + 1e-6:
            h, w = shape
            box = (x1 * w, y1 * h, x2 * w, y2 * h)
        else:
            box = roi
    kept = []
    for person in keypoints:
        shoulders = [person[i] for i in SHOULDERS if person[i][2] >= KEYPOINT_MIN]
        if not shoulders:
            continue
        if box is not None:
            sx = sum(float(s[0]) for s in shoulders) / len(shoulders)
            sy = sum(float(s[1]) for s in shoulders) / len(shoulders)
            if not (box[0] <= sx <= box[2] and box[1] <= sy <= box[3]):
                continue
        kept.append(person)
    if not kept:
        return np.zeros((0, 17, 3), dtype=np.float32)
    return np.asarray(kept, dtype=np.float32)


def hands_up(keypoints: Array, *, margin_ratio: float = 0.0) -> int:
    """Raised hands in view: every wrist above its owner's shoulders is one hand.

    Two hands in the air count twice, so ten people can raise twenty.

    keypoints: (people, 17, 3) array of x, y, confidence in image pixels.
    """
    votes = 0
    for person in keypoints:
        shoulders = [person[i] for i in SHOULDERS if person[i][2] >= KEYPOINT_MIN]
        wrists = [person[i] for i in WRISTS if person[i][2] >= KEYPOINT_MIN]
        if not shoulders or not wrists:
            continue
        shoulder_line = min(float(s[1]) for s in shoulders)
        span = abs(float(shoulders[0][0]) - float(shoulders[1][0])) if len(shoulders) == 2 else 0.0
        threshold = shoulder_line - margin_ratio * span
        votes += sum(1 for w in wrists if float(w[1]) < threshold)
    return votes


def marshal(keypoints: Array, shape: tuple[int, int] | None = None) -> Array | None:
    """The person giving signals: the nearest one whose arms can really be read.

    Nearest is the biggest body scale (see `body_scale`). Someone cut off by
    the edge of the frame (close to the camera, half out of shot) is passed
    over: they would always look nearest. Someone turned side-on is not: that
    is how a presenter stands to point to one side.
    """
    best: Array | None = None
    best_scale = 0.0
    for person in keypoints:
        if any(person[i][2] < KEYPOINT_MIN for i in (*SHOULDERS, *WRISTS)):
            continue
        if shape is not None and _cut_off(person, shape):
            continue
        scale = body_scale(person)
        if scale > best_scale:
            best, best_scale = person, scale
    return best


def _cut_off(person: Array, shape: tuple[int, int]) -> bool:
    h, w = shape
    margin = EDGE_RATIO * min(h, w)
    for i in (*SHOULDERS, *ELBOWS, *WRISTS):
        x, y, conf = (float(v) for v in person[i])
        if conf < KEYPOINT_MIN:
            continue
        if x < margin or x > w - margin or y < margin or y > h - margin:
            return True
    return False


def body_scale(person: Array) -> float:
    """A shoulder width that survives turning: shoulders, or 1.2 upper arms if larger."""
    span = abs(float(person[SHOULDERS[0]][0]) - float(person[SHOULDERS[1]][0]))
    arms = [
        math.dist(person[s][:2], person[e][:2])
        for s, e in zip(SHOULDERS, ELBOWS, strict=True)
        if person[s][2] >= KEYPOINT_MIN and person[e][2] >= KEYPOINT_MIN
    ]
    if not arms:
        return span
    return max(span, ARM_SCALE * sum(arms) / len(arms))


def arm_pose(keypoints: Array, shape: tuple[int, int] | None = None) -> Arms | None:
    """Where the signalling person's two wrists are, in shoulder widths.

    Each wrist is (across, down) from the middle of the shoulder line, so the
    numbers mean the same thing however far the person stands from the camera.
    """
    best = marshal(keypoints, shape)
    if best is None:
        return None
    best_span = body_scale(best)
    if best_span <= 0.0:
        return None
    centre = (float(best[SHOULDERS[0]][0]) + float(best[SHOULDERS[1]][0])) / 2
    line = min(float(best[i][1]) for i in SHOULDERS)
    a, b = (
        ((float(best[i][0]) - centre) / best_span, (float(best[i][1]) - line) / best_span)
        for i in WRISTS
    )
    return (a[0], a[1], b[0], b[1])


def _waving(track: list[tuple[float, float]]) -> bool:
    """A wrist is waving when it travels a long way and ends up near where it began.

    It must also sweep a box bigger than a shake's, or jazz hands done
    off-centre would read as a wave.
    """
    travel = sum(math.dist(p, q) for p, q in itertools.pairwise(track))
    swept = _extent(track) > JAZZ_EXTENT
    return travel >= WAVE_TRAVEL and swept and math.dist(track[0], track[-1]) <= 0.5 * travel


def _extent(track: list[tuple[float, float]]) -> float:
    """The larger side of the box a wrist's path stays inside."""
    xs, ys = [x for x, _ in track], [y for _, y in track]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _shaking(track: list[tuple[float, float]]) -> bool:
    travel = sum(math.dist(p, q) for p, q in itertools.pairwise(track))
    return travel >= JAZZ_TRAVEL and _extent(track) <= JAZZ_EXTENT


def arm_readout(poses: list[Arms]) -> dict[str, object] | None:
    """The numbers the signals are read from, for tuning in front of the camera.

    Per wrist, in shoulder widths, as [across, down, travel, box]: where it
    sits on average, how far it travelled in the window, and the size of the
    box it stayed in. Lists, not named fields: it rides in the camera's
    telemetry, which must stay under 512 bytes.
    """
    if len(poses) < WAVE_MIN_FRAMES:
        return None
    out: dict[str, object] = {"signal": marshal_signal(poses)}
    for name, track in (
        ("a", [(p[0], p[1]) for p in poses]),
        ("b", [(p[2], p[3]) for p in poses]),
    ):
        travel = sum(math.dist(p, q) for p, q in itertools.pairwise(track))
        out[name] = [
            round(sum(x for x, _ in track) / len(track), 2),
            round(sum(y for _, y in track) / len(track), 2),
            round(travel, 2),
            round(_extent(track), 2),
        ]
    return out


def marshal_signal(poses: list[Arms]) -> str | None:
    """The signal a ground marshal is giving, read from a second or so of arms.

    Both arms pointing out to one side is a turn to that side, as the camera
    (and the audience behind it) sees it: the way people point when they are
    not told how, read off rehearsal frames. Both hands up, one each side,
    shaking: jazz hands, which lands the drone. Both arms raised and
    beckoning is ahead. Arms hanging, however they move, are no signal.
    """
    if len(poses) < WAVE_MIN_FRAMES:
        return None
    wrists = [[(p[0], p[1]) for p in poses], [(p[2], p[3]) for p in poses]]
    mean = [(sum(x for x, _ in t) / len(t), sum(y for _, y in t) / len(t)) for t in wrists]
    waving = [_waving(t) for t in wrists]
    raised = [m[1] < -RAISE_REACH for m in mean]
    # Checked first: a fast shake can also pass for a short wave.
    apart = mean[0][0] * mean[1][0] < 0
    if all(raised) and apart and all(_shaking(t) for t in wrists):
        return "jazz"
    side = pointing(poses[-POINT_FRAMES:])
    if side is not None:
        return side
    if all(raised) and any(waving):
        return "forward"
    return None


def pointing(poses: list[Arms]) -> str | None:
    """Both arms out to the same side, at about shoulder height, just now."""
    if not poses:
        return None
    n = len(poses)
    xs = (sum(p[0] for p in poses) / n, sum(p[2] for p in poses) / n)
    downs = (sum(p[1] for p in poses) / n, sum(p[3] for p in poses) / n)
    if max(downs) > POINT_DROP:
        return None  # an arm hanging by the side is not pointing
    if all(x < -POINT_EACH for x in xs) and sum(xs) / 2 < -POINT_REACH:
        return "left"
    if all(x > POINT_EACH for x in xs) and sum(xs) / 2 > POINT_REACH:
        return "right"
    return None


def hand_boxes_from_pose(
    keypoints: Array,
    *,
    shape: tuple[int, int],
    margin_ratio: float = 0.0,
    allow_close_arm: bool = False,
) -> list[HandBox]:
    """Compute tight bounding boxes around detected hands tagged raised vs lowered."""
    h, w = shape
    min_dim = float(min(w, h))
    wrist_min = 0.22 if allow_close_arm else KEYPOINT_MIN
    boxes: list[HandBox] = []
    for person in keypoints:
        shoulders = [person[i] for i in SHOULDERS if person[i][2] >= KEYPOINT_MIN]
        weak_shoulders = (
            [person[i] for i in SHOULDERS if person[i][2] >= 0.12]
            if allow_close_arm and not shoulders
            else []
        )
        ref_shoulders = shoulders or weak_shoulders
        if not ref_shoulders and not allow_close_arm:
            continue
        shoulder_line = min(float(s[1]) for s in ref_shoulders) if ref_shoulders else None
        span = (
            abs(float(ref_shoulders[0][0]) - float(ref_shoulders[1][0]))
            if len(ref_shoulders) == 2
            else 0.0
        )
        threshold = shoulder_line - margin_ratio * span if shoulder_line is not None else None
        for elbow_idx, wrist_idx in ARMS:
            wrist = person[wrist_idx]
            if wrist[2] < wrist_min:
                continue
            wx, wy, conf = float(wrist[0]), float(wrist[1]), float(wrist[2])
            elbow = person[elbow_idx]
            arm_len = 0.0
            cx, cy = wx, wy
            elbow_y: float | None = None
            if elbow[2] >= 0.22:
                ex, ey = float(elbow[0]), float(elbow[1])
                elbow_y = ey
                arm_len = math.hypot(wx - ex, wy - ey)
                cx = wx + 0.20 * (wx - ex)
                cy = wy + 0.20 * (wy - ey)
            side = max(0.05 * min_dim, min(0.30 * min_dim, max(0.36 * span, 0.48 * arm_len)))
            half = side * 0.5
            x1 = max(0, round(cx - half))
            y1 = max(0, round(cy - half))
            x2 = min(w - 1, round(cx + half))
            y2 = min(h - 1, round(cy + half))
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            is_up = (
                wy < threshold
                if threshold is not None
                else (elbow_y is not None and wy < elbow_y - 0.1 * arm_len)
            )
            boxes.append(HandBox(x1, y1, x2, y2, is_up, conf))
    return boxes


def annotate_hands(
    image: Array,
    boxes: list[HandBox],
    *,
    roi: Roi | None = None,
) -> Array:
    """Draw crisp gallery-colored bounding boxes over hands onto a frame copy."""
    canvas = image.copy()
    h, w = canvas.shape[:2]
    if roi is not None:
        rx1, ry1, rx2, ry2 = roi
        if max(rx2, ry2) <= 1.0 + 1e-6:
            rx1, ry1, rx2, ry2 = rx1 * w, ry1 * h, rx2 * w, ry2 * h
        cv2.rectangle(canvas, (int(rx1), int(ry1)), (int(rx2), int(ry2)), (184, 99, 29), 2)
    thickness = max(2, min(4, min(w, h) // 220))
    for box in boxes:
        # BGR colors matching gallery palette: copper #d95d16 for raised, emerald #0d7a5f for down
        color = (22, 93, 217) if box.up else (95, 122, 13)
        cv2.rectangle(canvas, (box.x1, box.y1), (box.x2, box.y2), color, thickness)
        tag = "UP" if box.up else "HAND"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.55 if min(w, h) >= 480 else 0.45
        (tw, th), _ = cv2.getTextSize(tag, font, scale, 2)
        ty = max(th + 8, box.y1)
        cv2.rectangle(canvas, (box.x1, ty - th - 6), (box.x1 + tw + 10, ty), color, -1)
        cv2.putText(canvas, tag, (box.x1 + 5, ty - 4), font, scale, (244, 241, 234), 2, cv2.LINE_AA)
    return canvas


def _infer_device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


class HandCounter:
    def __init__(
        self,
        weights: Path = WEIGHTS,
        imgsz: int = 640,
        roi: str | Roi | None = None,
        person_min: float | None = None,
        margin_ratio: float = HAND_MARGIN_RATIO,
    ) -> None:
        from ultralytics import YOLO  # type: ignore[attr-defined]

        self.device = _infer_device()
        self.model: Any = YOLO(str(weights))
        self.imgsz = imgsz
        env_roi = os.environ.get("RI_HANDS_ROI")
        self.roi = parse_roi(env_roi if roi is None else roi)
        self.person_min = (
            float(os.environ.get("RI_PERSON_MIN", str(PERSON_MIN)))
            if person_min is None
            else person_min
        )
        self.margin_ratio = margin_ratio
        self.window: deque[FrameCount] = deque()
        self.readout: dict[str, object] | None = None
        # Shoulders, elbows and wrists (x, y pixels) of whoever the arms were
        # last read from, for `just arms --save` to draw.
        self.marshal_px: list[list[int]] | None = None

    def detect_frame(
        self, image: Array, now: float | None = None
    ) -> tuple[FrameCount, list[HandBox]]:
        result = self.model(
            image,
            imgsz=self.imgsz,
            conf=self.person_min,
            iou=0.5,
            device=self.device,
            verbose=False,
        )[0]
        empty = np.zeros((0, 17, 3), dtype=np.float32)
        raw = result.keypoints.data.cpu().numpy() if result.keypoints is not None else empty
        h, w = image.shape[:2]
        chosen = marshal(np.asarray(raw), (h, w))
        self.marshal_px = (
            None
            if chosen is None
            else [[round(float(chosen[i][0])), round(float(chosen[i][1]))] for i in POINTS]
        )
        filtered = filter_people(np.asarray(raw), roi=self.roi, shape=(h, w))
        seen = len(filtered)
        up = hands_up(filtered, margin_ratio=self.margin_ratio) if seen else 0
        boxes = hand_boxes_from_pose(filtered, shape=(h, w), margin_ratio=self.margin_ratio)
        if seen == 0 and len(raw) > 0:
            boxes = hand_boxes_from_pose(
                np.asarray(raw),
                shape=(h, w),
                margin_ratio=self.margin_ratio,
                allow_close_arm=True,
            )
            if boxes:
                seen = 1
                up = 1 if any(b.up for b in boxes) else 0
        return FrameCount(
            up,
            seen,
            time.time() if now is None else now,
            raw_up=up,
            raw_seen=seen,
            arms=arm_pose(np.asarray(raw), shape=(h, w)),
        ), boxes

    def count_frame(self, image: Array, now: float | None = None) -> FrameCount:
        count, _ = self.detect_frame(image, now=now)
        return count

    def add(self, count: FrameCount) -> FrameCount:
        """Add a frame and return the window's smoothed count."""
        self.window.append(count)
        while self.window and count.t - self.window[0].t > WINDOW_S:
            self.window.popleft()
        ups = sorted(c.up for c in self.window)
        seens = sorted(c.seen for c in self.window)
        mid = len(ups) // 2
        return FrameCount(
            ups[mid],
            seens[mid],
            count.t,
            raw_up=count.up if count.raw_up is None else count.raw_up,
            raw_seen=count.seen if count.raw_seen is None else count.raw_seen,
            gesture=self._held_signal(),
        )

    def _held_signal(self) -> str | None:
        poses = [c.arms for c in self.window if c.arms is not None]
        self.readout = arm_readout(poses)
        return marshal_signal(poses)
