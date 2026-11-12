"""Persistent local ports, from the demos workspace allocator."""

from __future__ import annotations

import json
import os
import subprocess
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"


@cache
def ports() -> dict[str, int]:
    """Every service port; children inherit them as RI_PORTS to skip the lookup."""
    inherited = os.environ.get("RI_PORTS")
    if inherited:
        return {k: int(v) for k, v in json.loads(inherited).items()}
    out = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            str(ROOT / "scripts" / "demo-ports.py"),
            "resolve",
            "--demo-dir",
            str(ROOT),
            "--allow-bound",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    return {k: int(v) for k, v in json.loads(out).items()}


def port(name: str) -> int:
    return ports()[name]


def url(name: str, path: str = "") -> str:
    return f"http://127.0.0.1:{port(name)}{path}"
