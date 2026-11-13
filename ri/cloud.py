"""The four jobs in Expanso Cloud: deploy, stop, start on stage, delete.

Every call goes through scripts/cloud_env.py, which reads the owner-only
.env at run time and points the CLI at ./.expanso-cli. Nothing here sees,
prints or passes a credential.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PIPELINES = ROOT / "pipelines"
JOBS = ("hear-clap", "hear-commands", "see-hands", "fly-drone")
NODES = JOBS
LABEL = "demo=demo-ri-nervous-system"


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
