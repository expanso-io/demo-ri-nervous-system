"""Keep everything this demo owns on disk under one budget.

uv run python -m ri.disk          # what is using the space
uv run python -m ri.disk --trim   # delete rebuildable files if over budget

The demo owns the project folder and its training download under ~/.cache.
`just up` trims before it starts, the screen service keeps the logs short
while the rig runs, and `just ready` fails when the total is over budget.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shutil
import sys
import threading
from pathlib import Path

from ri.ports import ROOT, RUNTIME

log = logging.getLogger("ri.disk")

GB = 1024**3
MB = 1024**2
BUDGET_BYTES = 10 * GB
LOG_CAP_BYTES = 50 * MB
LOG_KEEP_BYTES = 1 * MB
LOG_CHECK_S = 60.0
BUDGET_CHECK_EVERY = 10
CACHE = Path.home() / ".cache" / "demo-ri-nervous-system"
OWNED = (ROOT, CACHE)
# Each of these is rebuilt by a command. Cheapest to rebuild comes first; the
# training download goes last because getting it back is a 2.3 GB fetch.
DISPOSABLE = (
    ROOT / ".mypy_cache",
    ROOT / ".ruff_cache",
    ROOT / ".pytest_cache",
    RUNTIME / "review",
    CACHE,
)


def size(path: Path) -> int:
    """Bytes the path really takes on disk. Links are not followed."""
    if path.is_symlink() or not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_blocks * 512
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(folder, name)).st_blocks * 512
    return total


def footprint() -> int:
    return sum(size(path) for path in OWNED)


def trim(budget: int = BUDGET_BYTES) -> list[Path]:
    """Delete rebuildable files, cheapest first, until the demo fits the budget."""
    removed: list[Path] = []
    used = footprint()
    for path in DISPOSABLE:
        if used <= budget:
            break
        freed = size(path)
        if not freed:
            continue
        shutil.rmtree(path, ignore_errors=True)
        used -= freed
        removed.append(path)
    return removed


def cap_logs(folder: Path = RUNTIME, cap: int = LOG_CAP_BYTES, keep: int = LOG_KEEP_BYTES) -> int:
    """Cut every log over the cap down to its last lines. Returns how many were cut.

    The services hold their logs open in append mode, so they keep writing at
    the new end of the file after a cut.
    """
    cut = 0
    for path in folder.glob("*.log"):
        with contextlib.suppress(OSError):
            if path.stat().st_size <= cap:
                continue
            with open(path, "rb+") as handle:
                handle.seek(-keep, os.SEEK_END)
                tail = handle.read()
                handle.seek(0)
                handle.truncate()
                handle.write(tail)
            cut += 1
    return cut


def watch(stop: threading.Event | None = None) -> None:
    """Hold the line while the rig runs: short logs always, the budget every so often."""
    stop = stop or threading.Event()
    rounds = 0
    while not stop.wait(LOG_CHECK_S):
        rounds += 1
        if cap_logs():
            log.warning("cut oversized logs in %s", RUNTIME)
        if rounds % BUDGET_CHECK_EVERY == 0 and footprint() > BUDGET_BYTES:
            removed = trim()
            log.warning(
                "over the %d GB disk budget: removed %s",
                BUDGET_BYTES // GB,
                ", ".join(str(p) for p in removed) or "nothing (nothing rebuildable left)",
            )


def report() -> str:
    parts = [(size(p), p) for p in (*sorted(ROOT.iterdir()), CACHE) if p.exists()]
    lines = [f"  {used / GB:6.2f} GB  {path}" for used, path in sorted(parts, reverse=True)[:8]]
    total = sum(used for used, _ in parts)
    lines.append(f"  {total / GB:6.2f} GB  of {BUDGET_BYTES // GB} GB budget")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trim", action="store_true", help="delete rebuildable files if over")
    args = ap.parse_args()
    if args.trim:
        for path in trim():
            print(f"  removed {path}")
    print(report())
    return 0 if footprint() <= BUDGET_BYTES else 1


if __name__ == "__main__":
    sys.exit(main())
