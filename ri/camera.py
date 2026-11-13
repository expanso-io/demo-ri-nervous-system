"""The voting-block camera for rehearsal without a room.

Composes frames of a seated block from the people tiles built by
`scripts/build_fixtures.py`. A scene names how many hands are up; hands
rise or fall one person at a time, so the counter sees a gradual vote, the
way a real room votes. The frames stay on the see-hands device.
"""

from __future__ import annotations

import random
from pathlib import Path

import cv2
import numpy as np

from ri.types import Array

BUILD_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "build"

COLS, ROWS = 6, 3
SEATS = COLS * ROWS
TILE_W, TILE_H = 240, 300
STEP_S = 0.12  # one more hand up (or down) every 120 ms

# How many of the 18 seats have a hand up in each scene.
SCENES: dict[str, int] = {
    "down": 0,
    "idle": 1,
    "half": 7,
    "majority": 14,
    "conflict": 15,
}


def tiles(folder: Path, rng: random.Random) -> list[Path]:
    """Every distinct person first; mirrored copies only fill any gap."""
    originals = sorted(folder.glob("*-o*.jpg"))
    mirrored = sorted(folder.glob("*-m*.jpg"))
    rng.shuffle(originals)
    rng.shuffle(mirrored)
    return originals + mirrored


class VotingBlock:
    def __init__(self, build: Path = BUILD_DIR, seed: int = 11) -> None:
        rng = random.Random(seed)
        ups = tiles(build / "people" / "up", rng)
        downs = tiles(build / "people" / "down", rng)
        if len(ups) < SEATS or len(downs) < SEATS:
            raise RuntimeError("people tiles missing: run `just fixtures` first")
        self.up_tiles = [cv2.imread(str(p)) for p in ups[:SEATS]]
        self.down_tiles = [cv2.imread(str(p)) for p in downs[:SEATS]]
        self.order = list(range(SEATS))
        rng.shuffle(self.order)  # who raises a hand first, second, ...
        self.rng = np.random.default_rng(seed)
        self.target = SCENES["down"]
        self.current = 0.0
        self.last_t: float | None = None

    def set_scene(self, scene: str) -> None:
        self.target = SCENES[scene]

    def frame(self, now: float) -> Array:
        if self.last_t is not None:
            steps = (now - self.last_t) / STEP_S
            if self.current < self.target:
                self.current = min(self.target, self.current + steps)
            elif self.current > self.target:
                self.current = max(self.target, self.current - steps)
        self.last_t = now
        raised = set(self.order[: int(self.current)])
        canvas = np.full((ROWS * TILE_H, COLS * TILE_W, 3), 32, dtype=np.uint8)
        for seat in range(SEATS):
            tile = self.up_tiles[seat] if seat in raised else self.down_tiles[seat]
            row, col = divmod(seat, COLS)
            dx, dy = (int(v) for v in self.rng.integers(-3, 4, size=2))
            y0 = max(0, row * TILE_H + dy)
            x0 = max(0, col * TILE_W + dx)
            h = min(TILE_H, canvas.shape[0] - y0)
            w = min(TILE_W, canvas.shape[1] - x0)
            canvas[y0 : y0 + h, x0 : x0 + w] = tile[:h, :w]
        noise = self.rng.normal(0, 3, canvas.shape)
        return np.clip(canvas.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    @property
    def hands_up_now(self) -> int:
        return int(self.current)
