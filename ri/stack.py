"""Bring the whole rehearsal rig up and down on this laptop.

    uv run python -m ri.stack up      # services, Edge agents; pipelines off
    uv run python -m ri.stack down    # stop everything, delete the Cloud jobs
    uv run python -m ri.stack status

`up` deploys the four jobs to Expanso Cloud while the Edge agents are still
offline, stops them, then starts the agents: the senses are alive and grey,
and nothing carries a decision until the conductor (or the presenter) starts
the jobs. `down` leaves no process, no port and no Cloud job behind.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from ri import cloud
from ri.ports import ROOT, RUNTIME, ports

EDGE_API = {
    "hear-clap": "EDGE_API_CLAP",
    "hear-commands": "EDGE_API_WORD",
    "see-hands": "EDGE_API_HANDS",
    "fly-drone": "EDGE_API_FLY",
}


def environment() -> dict[str, str]:
    env = dict(os.environ)
    resolved = ports()
    env["RI_PORTS"] = json.dumps(resolved)
    for name, value in resolved.items():
        env[f"RI_{name}"] = str(value)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def services() -> list[tuple[str, list[str]]]:
    py = [sys.executable, "-m"]
    found = [
        ("broker", [*py, "ri.broker"]),
        ("device-clap", [*py, "ri.device", "clap"]),
        ("device-word", [*py, "ri.device", "word"]),
        ("device-hands", [*py, "ri.device", "hands"]),
        ("flight", [*py, "ri.flight"]),
        ("screen", [*py, "ri.screen"]),
    ]
    for node, port_name in EDGE_API.items():
        found.append(
            (
                f"edge-{node}",
                [
                    "expanso-edge",
                    "run",
                    "--no-watch",
                    "--config",
                    str(ROOT / "edge" / f"{node}.yaml"),
                    "--data-dir",
                    str(ROOT / ".expanso-edge" / node),
                    "--api-listen",
                    f"127.0.0.1:{ports()[port_name]}",
                    "--log-format",
                    "text",
                    "--log-level",
                    "info",
                ],
            )
        )
    return found


def pid_file(name: str) -> Path:
    return RUNTIME / f"{name}.pid"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start(name: str, argv: list[str], env: dict[str, str]) -> None:
    existing = pid_file(name)
    if existing.exists() and alive(int(existing.read_text())):
        print(f"  {name}: already running")
        return
    log = open(RUNTIME / f"{name}.log", "w")  # noqa: SIM115 - the child owns it
    proc = subprocess.Popen(
        argv,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    existing.write_text(str(proc.pid))
    print(f"  {name}: pid {proc.pid}")


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for(port: int, what: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if listening(port):
            return
        time.sleep(0.5)
    raise SystemExit(f"{what} did not come up on 127.0.0.1:{port}; see .runtime/*.log")


def up(args: argparse.Namespace) -> int:
    RUNTIME.mkdir(exist_ok=True)
    missing = [n for n in cloud.NODES if not (ROOT / ".expanso-edge" / n / "auth").exists()]
    if missing:
        raise SystemExit(f"no node identity for {', '.join(missing)}: just workspace-init")
    env = environment()
    p = ports()
    table = dict(services())
    print("services")
    start("broker", table["broker"], env)
    wait_for(p["MQTT_PORT"], "broker")
    for name in ("device-clap", "device-word", "device-hands", "flight", "screen"):
        start(name, table[name], env)
    if not args.skip_deploy:
        print("expanso cloud: deploy the four jobs, then leave them stopped")
        for job in cloud.JOBS:
            print(f"  {job}: {cloud.deploy(job)}")
        for job in cloud.JOBS:
            cloud.stop(job)
    print("expanso edge agents")
    for node in cloud.NODES:
        start(f"edge-{node}", table[f"edge-{node}"], env)
    for name, port_name in (
        ("device-clap", "CLAP_DEVICE_PORT"),
        ("device-word", "WORD_DEVICE_PORT"),
        ("device-hands", "HANDS_DEVICE_PORT"),
        ("flight", "FLIGHT_PORT"),
        ("screen", "PORT"),
    ):
        wait_for(p[port_name], name, 120)
    for node, port_name in EDGE_API.items():
        wait_for(p[port_name], f"edge {node}", 60)
    print(f"ready: screen on http://127.0.0.1:{p['PORT']}/ (pipelines off)")
    return 0


def stop_process(name: str) -> None:
    path = pid_file(name)
    if not path.exists():
        return
    pid = int(path.read_text())
    if alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 30
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.25)
        if alive(pid):
            os.killpg(pid, signal.SIGKILL)
    path.unlink(missing_ok=True)


def down(args: argparse.Namespace) -> int:
    if not args.keep_jobs:
        print("expanso cloud: stop and delete the four jobs")
        for job in cloud.JOBS:
            cloud.delete(job)
    names = [name for name, _ in services()]
    # Edge agents first so they drain, then the services they call.
    for name in sorted(names, key=lambda n: (not n.startswith("edge-"), n == "broker")):
        stop_process(name)
    busy = [f"{k}={v}" for k, v in ports().items() if listening(v)]
    left = [p.stem for p in RUNTIME.glob("*.pid")]
    if busy or left:
        print(f"still bound: {busy or '-'}; pid files: {left or '-'}")
        return 1
    print("down: no processes, no ports, no Cloud jobs")
    return 0


def status(_: argparse.Namespace) -> int:
    for name, _argv in services():
        path = pid_file(name)
        state = "running" if path.exists() and alive(int(path.read_text())) else "stopped"
        print(f"  {name:20} {state}")
    for node, port_name in EDGE_API.items():
        url = f"http://127.0.0.1:{ports()[port_name]}/api/v1/executions"
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                body = r.read().decode()[:200]
        except OSError as error:
            body = f"unreachable ({error})"
        print(f"  edge {node:16} {body}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up")
    u.add_argument("--skip-deploy", action="store_true")
    d = sub.add_parser("down")
    d.add_argument("--keep-jobs", action="store_true")
    sub.add_parser("status")
    args = ap.parse_args()
    return {"up": up, "down": down, "status": status}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
