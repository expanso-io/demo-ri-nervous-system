"""Fetch model weights and build the camera fixture for a hardware-free run.

    uv run python -m scripts.build_fixtures

The voting-block camera feed is composed from people in openly licensed
photographs on Wikimedia Commons (pinned with licences in
fixtures/hands-sources.json). Every
person is cropped where the pose model sees them clearly, then re-checked on
their own tile; only tiles the model reads back correctly are kept. Nothing
built here is committed: it lands in fixtures/build/ and models/cache/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ri.senses.hands import WEIGHTS, hands_up
from ri.types import Array

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "fixtures" / "build"
TILE_W, TILE_H = 240, 300
UA = {"User-Agent": "demo-fixture-fetch/1.0"}

WEIGHTS_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n-pose.pt"
SOURCES_FILE = ROOT / "fixtures" / "hands-sources.json"


def fetch(url: str, dest: Path) -> Path:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    dest.write_bytes(data)
    print(f"fetched {dest.relative_to(ROOT)} ({len(data) // 1024} KB)")
    return dest


def tile_from(image: Array, box: Array) -> Array:
    x1, y1, x2, y2 = (float(v) for v in box)
    w, h = x2 - x1, y2 - y1
    x1, x2 = x1 - 0.12 * w, x2 + 0.12 * w
    y1, y2 = y1 - 0.18 * h, y2 + 0.05 * h
    hh, ww = image.shape[:2]
    crop = image[max(0, int(y1)) : min(hh, int(y2)), max(0, int(x1)) : min(ww, int(x2))]
    scale = min(TILE_W / crop.shape[1], TILE_H / crop.shape[0])
    resized = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
    tile = np.full((TILE_H, TILE_W, 3), 38, dtype=np.uint8)
    oy, ox = (TILE_H - resized.shape[0]) // 2, (TILE_W - resized.shape[1]) // 2
    tile[oy : oy + resized.shape[0], ox : ox + resized.shape[1]] = resized
    return tile


def people(model: Any, image: Array, imgsz: int) -> list[tuple[Array, int, float]]:
    result = model(image, imgsz=imgsz, conf=0.3, verbose=False)[0]
    if result.keypoints is None:
        return []
    keypoints = result.keypoints.data.cpu().numpy()
    boxes = result.boxes.xyxy.cpu().numpy()
    confs = result.boxes.conf.cpu().numpy()
    found = []
    for kp, box, conf in zip(keypoints, boxes, confs, strict=True):
        shoulders = kp[[5, 6], 2].min()
        if conf < 0.45 or shoulders < 0.4:
            continue
        found.append((box, hands_up(kp[None]), float(conf)))
    return found


def recheck(model: Any, tile: Array) -> int | None:
    """The state the model reads on the tile alone, or None if ambiguous."""
    result = model(tile, imgsz=640, conf=0.35, verbose=False)[0]
    if result.keypoints is None or len(result.keypoints.data) != 1:
        return None
    return hands_up(result.keypoints.data.cpu().numpy())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    fetch(WEIGHTS_URL, WEIGHTS)
    from ultralytics import YOLO  # type: ignore[attr-defined]

    model = YOLO(str(WEIGHTS))
    tiles_dir = BUILD / "people"
    if args.force and tiles_dir.exists():
        for old in tiles_dir.rglob("*.jpg"):
            old.unlink()
    kept = {"up": 0, "down": 0}
    manifest: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = json.loads(SOURCES_FILE.read_text())
    for number, source in enumerate(sources):
        source["name"] = f"s{number:02d}"
        path = fetch(source["url"], BUILD / "sources" / f"{source['name']}.jpg")
        image = cv2.imread(str(path))
        if image is None:
            continue
        for flip in (False, True):
            frame = cv2.flip(image, 1) if flip else image
            for index, (box, up, conf) in enumerate(people(model, frame, 960)):
                tile = tile_from(frame, box)
                if recheck(model, tile) != up:
                    continue
                state = "up" if up else "down"
                name = f"{source['name']}-{'m' if flip else 'o'}{index:02d}.jpg"
                out = tiles_dir / state / name
                out.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out), tile)
                kept[state] += 1
                manifest.append(
                    {"tile": f"{state}/{name}", "source": source["name"], "conf": round(conf, 2)}
                )
    (BUILD / "people.json").write_text(
        json.dumps({"kept": kept, "sources": sources, "tiles": manifest}, indent=2) + "\n"
    )
    digest = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()[:16]
    print(f"tiles kept: {kept}; pose weights sha256 {digest}")
    if kept["up"] < 6 or kept["down"] < 10:
        print("not enough clean tiles for a 16-person voting block")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
