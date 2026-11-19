"""Bring the whole rehearsal rig up and down on this laptop.

    uv run python -m ri.stack up      # services, Edge agents; pipelines off
    uv run python -m ri.stack down    # stop everything, delete the Cloud jobs
    uv run python -m ri.stack status

`up` deploys the four jobs to Expanso Cloud while the Edge agents are still
offline, stops them, starts the agents, then starts the four jobs so the rig
is ready to fly. `--cold` leaves them stopped; a recorded take resets to that
zero state itself. `down` leaves no process, no port and no Cloud job behind.
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

from ri import cloud, disk, room
from ri.capture import resolve_camera_source
from ri.ports import DEFAULT_CAMERA, DEFAULT_DRONE, DEFAULT_INPUT, DEFAULT_MIC, ROOT, RUNTIME, ports

EDGE_API = {
    "hear-clap": "EDGE_API_CLAP",
    "hear-commands": "EDGE_API_WORD",
    "see-hands": "EDGE_API_HANDS",
    "fly-drone": "EDGE_API_FLY",
}


def environment(args: argparse.Namespace | None = None) -> dict[str, str]:
    env = dict(os.environ)
    resolved = ports()
    env["RI_PORTS"] = json.dumps(resolved)
    for name, value in resolved.items():
        env[f"RI_{name}"] = str(value)
    env.setdefault("YOLO_CONFIG_DIR", str(RUNTIME / "ultralytics"))
    chosen = getattr(args, "room", None) or env.get("RI_ROOM") or room.remembered()
    env["RI_ROOM"] = room.named(chosen).name
    if args is not None:
        drone = getattr(args, "drone", None) or env.get("RI_DRONE") or DEFAULT_DRONE
        input_mode = getattr(args, "input_mode", None) or env.get("RI_INPUT") or DEFAULT_INPUT
        hands_input = getattr(args, "hands_input", None) or env.get("RI_HANDS_INPUT") or input_mode
        stage_cam = getattr(args, "stage_cam", None)
        if stage_cam is None:
            stage_cam = env.get(
                "RI_STAGE_CAM",
                DEFAULT_CAMERA if (input_mode == "live" or hands_input == "live") else "none",
            )
        hands_device = getattr(args, "hands_device", None)
        if hands_device is None:
            hands_device = env.get(
                "RI_HANDS_DEVICE",
                DEFAULT_CAMERA if hands_input == "live" else "",
            )
        clap_device = getattr(args, "clap_device", None) or env.get("RI_CLAP_DEVICE") or DEFAULT_MIC
        word_device = getattr(args, "word_device", None) or env.get("RI_WORD_DEVICE") or DEFAULT_MIC
        env["RI_DRONE"] = drone
        env["RI_INPUT"] = input_mode
        env["RI_STAGE_CAM"] = stage_cam
        if getattr(args, "hands_roi", None):
            env["RI_HANDS_ROI"] = args.hands_roi
        if getattr(args, "hands_input", None):
            env["RI_HANDS_INPUT"] = args.hands_input
        if hands_device:
            env["RI_HANDS_DEVICE"] = hands_device
        else:
            env.pop("RI_HANDS_DEVICE", None)
        env["RI_CLAP_DEVICE"] = clap_device
        env["RI_WORD_DEVICE"] = word_device
        if getattr(args, "mqtt_bind", None):
            env["RI_MQTT_BIND"] = args.mqtt_bind
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


# Services this run started, so a start that dies can say why at once.
STARTED: dict[str, tuple[subprocess.Popen[bytes], Path]] = {}


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
    # Emptied, then opened for append: ri.disk can cut it short while it is written.
    (RUNTIME / f"{name}.log").write_bytes(b"")
    log = open(RUNTIME / f"{name}.log", "a")  # noqa: SIM115 - the child owns it
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
    STARTED[name] = (proc, RUNTIME / f"{name}.log")
    print(f"  {name}: pid {proc.pid}")


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def last_words(log: Path, lines: int = 6) -> str:
    try:
        tail = log.read_text(errors="replace").strip().splitlines()[-lines:]
    except OSError:
        return ""
    return "\n".join(f"    {line}" for line in tail)


def wait_for(port: int, what: str, seconds: float = 60) -> None:
    started = STARTED.get(what)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if listening(port):
            return
        if started and started[0].poll() is not None:
            raise SystemExit(f"{what} quit while starting. It said:\n{last_words(started[1])}")
        time.sleep(0.5)
    said = f" It said:\n{last_words(started[1])}" if started else ""
    raise SystemExit(f"{what} did not come up on 127.0.0.1:{port}.{said}")


def screen_environment(env: dict[str, str]) -> dict[str, str]:
    """Point screen stage_cam at device-hands HTTP preview when both share one camera."""
    out = dict(env)
    hands_live = (out.get("RI_HANDS_INPUT") or out.get("RI_INPUT", "fixture")) == "live"
    stage_spec = out.get("RI_STAGE_CAM", "none").strip()
    if not hands_live or stage_spec.lower() in ("", "none", "off", "false"):
        return out
    hands_spec = out.get("RI_HANDS_DEVICE", "").strip() or "0"
    share = stage_spec.lower() in ("hands", "auto") or stage_spec.lower() == hands_spec.lower()
    stage_name: str | None = None
    if not share:
        with contextlib.suppress(RuntimeError):
            h_res, h_name = resolve_camera_source(hands_spec)
            s_res, s_name = resolve_camera_source(stage_spec)
            if h_res == s_res:
                share = True
                stage_name = s_name or h_name
    else:
        with contextlib.suppress(RuntimeError):
            _, stage_name = resolve_camera_source(hands_spec)
    if share:
        hands_port = out.get("RI_HANDS_DEVICE_PORT") or str(ports()["HANDS_DEVICE_PORT"])
        out["RI_STAGE_CAM"] = f"http://127.0.0.1:{hands_port}/preview.jpg"
        out["RI_STAGE_CAM_NAME"] = stage_name or hands_spec
    return out


def up(args: argparse.Namespace) -> int:
    RUNTIME.mkdir(exist_ok=True)
    for removed in disk.trim():
        print(f"  disk budget: removed {removed}")
    used = disk.footprint()
    if used > disk.BUDGET_BYTES:
        print(f"  WARNING: {used / disk.GB:.1f} GB on disk, over the budget: just disk")
    missing = [n for n in cloud.NODES if not (ROOT / ".expanso-edge" / n / "auth").exists()]
    if missing:
        raise SystemExit(f"no node identity for {', '.join(missing)}: just workspace-init")
    env = environment(args)
    room.remember(env["RI_ROOM"])
    print(f"room {room.named(env['RI_ROOM']).summary()}")
    p = ports()
    table = dict(services())
    print("services")
    start("broker", table["broker"], env)
    wait_for(p["MQTT_PORT"], "broker")
    for name in ("device-clap", "device-word", "device-hands", "flight"):
        start(name, table[name], env)
    wait_for(p["HANDS_DEVICE_PORT"], "device-hands", 120)
    start("screen", table["screen"], screen_environment(env))
    if not (args.skip_deploy or args.offline):
        print("expanso cloud: deploy the four jobs, then leave them stopped")
        for job in cloud.JOBS:
            print(f"  {job}: {cloud.deploy(job)}")
        for job in cloud.JOBS:
            cloud.stop(job)
    elif args.offline:
        print("offline mode: skipping Expanso Cloud deploy (local Edge state only)")
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
    started = not (args.offline or args.cold)
    if started:
        print("expanso cloud: start the four jobs")
        for job in cloud.JOBS:
            cloud.rerun(job)
    drone = env.get("RI_DRONE", DEFAULT_DRONE)
    inp = env.get("RI_INPUT", DEFAULT_INPUT)
    hands_inp = env.get("RI_HANDS_INPUT", inp)
    hands_dev = env.get("RI_HANDS_DEVICE") or (DEFAULT_CAMERA if hands_inp == "live" else "fixture")
    stage_cam = env.get("RI_STAGE_CAM", DEFAULT_CAMERA)
    url_str = f"http://127.0.0.1:{p['PORT']}/"
    print(
        f"ready: screen on {url_str} (drone={drone}, input={inp}, "
        f"hands={hands_inp}:{hands_dev}, stage_cam={stage_cam}, "
        f"pipelines {'starting' if started else 'off'})"
    )
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


def reap_orphan_captures() -> int:
    """Stop this rig's microphone and camera readers that outlived their rig.

    A capture reader is an ffmpeg child of a device. If the device dies first
    the reader is adopted by init and keeps the microphone open for ever. Only
    readers with no parent left, and with exactly the arguments this rig
    starts them with, are stopped.
    """
    listing = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, check=False
    ).stdout
    count = 0
    for line in listing.splitlines():
        pid, ppid, command = [*line.split(None, 2), "", "", ""][:3]
        ours = "ffmpeg" in command and "-f avfoundation" in command and "pipe:1" in command
        ours = ours or (
            "ffmpeg" in command and "-f avfoundation" in command and "image2pipe" in command
        )
        if ours and ppid == "1":
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pid), signal.SIGTERM)
                count += 1
    return count


def down(args: argparse.Namespace) -> int:
    with contextlib.suppress(OSError):
        req = urllib.request.Request(
            f"http://127.0.0.1:{ports()['FLIGHT_PORT']}/kill", data=b"{}", method="POST"
        )
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=2).read()
    if not (args.keep_jobs or args.offline):
        print("expanso cloud: stop and delete the four jobs")
        for job in cloud.JOBS:
            cloud.delete(job)
    names = [name for name, _ in services()]
    # Edge agents first so they drain, then the services they call.
    for name in sorted(names, key=lambda n: (not n.startswith("edge-"), n == "broker")):
        stop_process(name)
    reaped = reap_orphan_captures()
    if reaped:
        print(f"stopped {reaped} leftover capture process(es) from earlier runs")
    busy = [f"{k}={v}" for k, v in ports().items() if listening(v)]
    left = [p.stem for p in RUNTIME.glob("*.pid")]
    if busy or left:
        print(f"still bound: {busy or '-'}; pid files: {left or '-'}")
        return 1
    print(
        "down: no processes, no ports"
        + ("" if (args.keep_jobs or args.offline) else ", no Cloud jobs")
    )
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
    u.add_argument(
        "--drone",
        choices=("sim", "codrone", "tello"),
        default=os.environ.get("RI_DRONE", DEFAULT_DRONE),
    )
    u.add_argument(
        "--input",
        dest="input_mode",
        choices=("fixture", "live"),
        default=os.environ.get("RI_INPUT", DEFAULT_INPUT),
    )
    u.add_argument(
        "--room",
        choices=("", *room.ROOMS),
        default="",
        help="size the flight for the room; empty keeps the last one used",
    )
    u.add_argument("--cold", action="store_true", help="leave the four pipelines stopped")
    u.add_argument("--stage-cam", default=None)
    u.add_argument("--hands-roi", default=os.environ.get("RI_HANDS_ROI", ""))
    u.add_argument("--hands-input", choices=("", "fixture", "live"), default="")
    u.add_argument("--hands-device", default=None)
    u.add_argument("--clap-device", default=os.environ.get("RI_CLAP_DEVICE", DEFAULT_MIC))
    u.add_argument("--word-device", default=os.environ.get("RI_WORD_DEVICE", DEFAULT_MIC))
    u.add_argument("--mqtt-bind", default=os.environ.get("RI_MQTT_BIND", "127.0.0.1"))
    u.add_argument("--skip-deploy", action="store_true")
    u.add_argument("--offline", action="store_true")
    d = sub.add_parser("down")
    d.add_argument("--keep-jobs", action="store_true")
    d.add_argument("--offline", action="store_true")
    sub.add_parser("status")
    args = ap.parse_args()
    return {"up": up, "down": down, "status": status}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
