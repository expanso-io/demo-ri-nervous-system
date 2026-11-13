"""The four jobs in Expanso Cloud: deploy, stop, start on stage, delete.

Every call goes through scripts/cloud_env.py, which reads the owner-only
.env at run time and points the CLI at ./.expanso-cli. Nothing here sees,
prints or passes a credential.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PIPELINES = ROOT / "pipelines"
JOBS = ("hear-clap", "hear-commands", "see-hands", "fly-drone")
NODES = JOBS
LABEL = "demo=demo-ri-nervous-system"
EDGE_API = {
    "hear-clap": "EDGE_API_CLAP",
    "hear-commands": "EDGE_API_WORD",
    "see-hands": "EDGE_API_HANDS",
    "fly-drone": "EDGE_API_FLY",
}


def cli(*args: str, check: bool = True, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    command = [
        "uv",
        "run",
        "--no-project",
        str(ROOT / "scripts" / "cloud_env.py"),
        "--",
        "expanso-cli",
        *args,
    ]
    out = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    if check and out.returncode != 0:
        raise RuntimeError(f"expanso-cli {' '.join(args[:2])}: {(out.stderr or out.stdout)[-400:]}")
    return out


def deploy(job: str) -> str:
    """Deploy one job. An unchanged spec adds no version and counts as current."""
    out = cli("job", "deploy", str(PIPELINES / f"{job}.yaml"), check=False)
    text = (out.stdout + out.stderr).strip()
    if out.returncode == 0:
        return text.splitlines()[-1] if text else "deployed"
    if "NO_CHANGES_DETECTED" in text or "unchanged" in text:
        return "unchanged"
    raise RuntimeError(f"deploy {job}: {text[-400:]}")


def stop(job: str) -> None:
    cli("job", "stop", job, "--force", check=False)


def rerun(job: str) -> None:
    cli("job", "rerun", job)


def delete(job: str) -> None:
    cli("job", "stop", job, "--force", check=False)
    cli("job", "delete", job, "--yes", "--force", check=False)


def executions(job: str) -> list[dict[str, Any]]:
    out = cli("job", "executions", job, "--format", "json", check=False)
    try:
        data = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def nodes() -> str:
    return cli("node", "list", "--label", LABEL, check=False).stdout


def jobs() -> str:
    return cli("job", "list", check=False).stdout


def running_on(node: str) -> bool:
    """True when that node's own Edge agent reports its pipeline running."""
    from ri.ports import port

    api = f"http://127.0.0.1:{port(EDGE_API[node])}/api/v1/executions"
    try:
        with urllib.request.urlopen(api, timeout=2) as response:
            items = json.loads(response.read() or b"{}").get("items", [])
    except (OSError, ValueError):
        return False
    for item in items:
        status = item.get("status", {})
        desired = status.get("desired_state", {}).get("state_type")
        observed = status.get("observed_state", {}).get("state_type")
        if desired == "running" and observed == "running":
            return True
    return False


def wait_running(count: int, timeout: float) -> bool:
    """Wait until exactly `count` of the four nodes run their pipeline."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sum(running_on(n) for n in NODES) == count:
            return True
        time.sleep(0.5)
    return False
