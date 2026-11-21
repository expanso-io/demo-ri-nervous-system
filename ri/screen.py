"""The audience screen's server: one state object, streamed ten times a second.

    uv run python -m ri.screen

Everything the screen draws comes from somewhere real: device levels and the
sense decisions from the MQTT bus, the acts from fly-drone's output, the
drone from its own state topic, and whether each node is live from that
Expanso Edge agent's local API. Bound to 127.0.0.1 only; the presenter keys
(kill, scene, link fault) live here too.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ri import cloud, disk, room
from ri.bus import Bus
from ri.capture import LiveCamera, list_physical_audio_inputs
from ri.messages import PIPELINE_FOR
from ri.ports import DEFAULT_CAMERA, port, url

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "dashboard"
log = logging.getLogger("ri.screen")

NODE_API = {
    "hear-clap": "EDGE_API_CLAP",
    "hear-commands": "EDGE_API_WORD",
    "see-hands": "EDGE_API_HANDS",
    "fly-drone": "EDGE_API_FLY",
}
DEVICE_PORT = {
    "hear-clap": "CLAP_DEVICE_PORT",
    "hear-commands": "WORD_DEVICE_PORT",
    "see-hands": "HANDS_DEVICE_PORT",
}
SENSE_PORT = {
    "clap": "CLAP_DEVICE_PORT",
    "word": "WORD_DEVICE_PORT",
    "hands": "HANDS_DEVICE_PORT",
}
# The show by number: each key sends what the room would have, through the
# same device and pipeline. Stage 2 is the room's separate "up"s (see `climb`).
STAGES: dict[int, tuple[str, str]] = {
    1: ("clap", "clap"),
    2: ("word", "up"),
    3: ("hands", "left"),
    4: ("hands", "right"),
    5: ("clap", "applause"),
}
GROUNDED = ("dark", "parked", "landing")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
}


class Board:
    def __init__(
        self, stage_cam: LiveCamera | None = None, *, stage_cam_name: str | None = None
    ) -> None:
        self.lock = threading.Lock()
        self.stage_cam = stage_cam
        self.stage_cam_label = stage_cam_name
        self.devices: dict[str, dict[str, Any]] = {}
        self.nodes: dict[str, dict[str, Any]] = {
            n: {"live": False, "agent": False, "sent": 0} for n in NODE_API
        }
        self.latest: dict[str, dict[str, Any]] = {}
        self.act: dict[str, Any] | None = None
        self.acts: deque[dict[str, Any]] = deque(maxlen=8)
        self.drone: dict[str, Any] = {}
        self.climbing = threading.Lock()
        self.scene: dict[str, Any] = {
            "beat": "hook",
            "prompt": "This drone has no brain of its own. You are its nervous system.",
        }
        self.pulses: deque[dict[str, Any]] = deque(maxlen=64)
        self.pulse_seq = 0
        self.decision_bytes = 0
        self.decision_count = 0
        self.started = time.time()

    def reset(self) -> None:
        """Back to a true zero state for the next take: no messages, no counts."""
        with self.lock:
            for node in self.nodes.values():
                node["sent"] = 0
            self.latest.clear()
            self.act = None
            self.acts.clear()
            self.decision_bytes = 0
            self.decision_count = 0

    def pulse(self, kind: str, node: str) -> None:
        self.pulse_seq += 1
        self.pulses.append({"id": self.pulse_seq, "kind": kind, "node": node, "t": time.time()})

    def on_message(self, topic: str, payload: bytes) -> None:
        try:
            body = json.loads(payload)
        except ValueError:
            return
        with self.lock:
            if topic.startswith("ri/device/"):
                self.devices[topic.rsplit("/", 1)[1]] = body
            elif topic.startswith("ri/sense/"):
                sense = topic.rsplit("/", 1)[1]
                body["raw"] = payload.decode()
                body["bytes"] = len(payload)
                body["seen_at"] = time.time()
                self.latest[sense] = body
                self.decision_bytes += len(payload)
                self.decision_count += 1
                node = PIPELINE_FOR.get(sense, sense)  # type: ignore[call-overload]
                self.nodes[node]["sent"] += 1
                self.pulse("sense", node)
            elif topic == "ri/act":
                body["raw"] = payload.decode()
                body["bytes"] = len(payload)
                body["seen_at"] = time.time()
                self.act = body
                self.nodes["fly-drone"]["sent"] += 1
                if body.get("act") != "hold" or not self.acts or self.acts[-1]["act"] != "hold":
                    self.acts.append(body)
                self.pulse("act", "fly-drone")
            elif topic == "ri/drone/state":
                self.drone = body

    def poll_nodes(self) -> None:
        while True:
            for node, port_name in NODE_API.items():
                live, agent = False, False
                try:
                    api = f"http://127.0.0.1:{port(port_name)}/api/v1/executions"
                    with urllib.request.urlopen(api, timeout=1) as response:
                        agent = True
                        live = running(json.loads(response.read() or b"[]"))
                except (OSError, ValueError):
                    pass
                with self.lock:
                    self.nodes[node]["live"] = live
                    self.nodes[node]["agent"] = agent
            time.sleep(0.5)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "t": time.time(),
                "devices": self.devices,
                "nodes": self.nodes,
                "latest": self.latest,
                "act": self.act,
                "acts": list(self.acts),
                "drone": self.drone,
                "scene": self.scene,
                "pulses": list(self.pulses),
                "stage_cam": self.stage_cam is not None,
                "stage_cam_name": (
                    (self.stage_cam_label or self.stage_cam.device_name)
                    if self.stage_cam is not None
                    else None
                ),
                "kept": {
                    "raw_bytes": sum(d.get("captured_bytes", 0) for d in self.devices.values()),
                    "decision_bytes": self.decision_bytes,
                    "decisions": self.decision_count,
                },
            }


def running(body: Any) -> bool:
    """True when the agent reports an execution it wants running and is running."""
    items = body.get("items", []) if isinstance(body, dict) else []
    for item in items if isinstance(items, list) else []:
        status = item.get("status", {}) if isinstance(item, dict) else {}
        desired = status.get("desired_state", {}).get("state_type")
        observed = status.get("observed_state", {}).get("state_type")
        if desired == "running" and observed == "running":
            return True
    return False


def post(target: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(target, data=json.dumps(body).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            result: dict[str, Any] = json.loads(response.read())
            return result
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        try:
            detail = str(json.loads(raw).get("error") or raw)
        except ValueError:
            detail = raw or str(error)
        raise ValueError(detail) from error


def force(sense: str, value: str) -> dict[str, Any]:
    """Have a device send the decision its model would have made."""
    return post(url(SENSE_PORT[sense], "/cue"), {"sense": sense, "force": value})


def climb(
    board: Board,
    say: Callable[[str, str], Any] = force,
    *,
    top_m: float | None = None,
    patience_s: float = 8.0,
    tick_s: float = 0.1,
) -> int:
    """Say "up" as the presenter would: once, wait for the hover, again, to the top.

    Stops at the room's top height, and stops at once if the drone lands or
    stops answering. Returns how many ups were said.
    """
    this = room.current()
    top = this.top_m if top_m is None else top_m
    said = 0

    def settle(done: Callable[[dict[str, Any]], bool]) -> bool:
        deadline = time.monotonic() + patience_s
        while time.monotonic() < deadline:
            drone = board.drone
            if not drone or drone.get("phase") in GROUNDED:
                return False
            if done(drone):
                return True
            time.sleep(tick_s)
        return False

    while said < this.ups + 2:
        if not settle(lambda d: d.get("phase") == "hovering"):
            break
        before = float(board.drone.get("target_m", 0.0))
        if before >= top - 0.01:
            break
        say("word", "up")
        said += 1

        def climbed(drone: dict[str, Any], was: float = before) -> bool:
            return float(drone.get("target_m", 0.0)) > was + 0.01

        if not settle(climbed):
            break
    return said


def run_stage(board: Board, stage: int) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"no stage {stage!r}: 1 to {len(STAGES)}")
    if stage != 2:
        return {"stage": stage, **force(*STAGES[stage])}
    if not board.climbing.acquire(blocking=False):
        return {"stage": stage, "already": True}

    def _climb() -> None:
        try:
            log.info("stage 2: said up %d times", climb(board))
        except Exception:
            log.exception("stage 2 stopped")
        finally:
            board.climbing.release()

    threading.Thread(target=_climb, daemon=True).start()
    return {"stage": stage, "started": True}


def make_handler(board: Board) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _json(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                self._json(200, board.snapshot())
                return
            if path == "/api/mics":
                self._json(200, {"mics": list_physical_audio_inputs()})
                return
            if path == "/api/stage-cam.jpg":
                frame = board.stage_cam.jpeg() if board.stage_cam is not None else None
                if frame is None:
                    self._json(404, {"error": "stage camera not active"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(frame)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(frame)
                return
            if path == "/api/stage-cam.mjpg":
                if board.stage_cam is None:
                    self._json(404, {"error": "stage camera not active"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    while True:
                        frame = board.stage_cam.jpeg()
                        if frame:
                            head = (
                                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                + str(len(frame)).encode()
                                + b"\r\n\r\n"
                            )
                            self.wfile.write(head + frame + b"\r\n")
                            self.wfile.flush()
                        time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError):
                    return
            if path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        data = json.dumps(board.snapshot(), separators=(",", ":"))
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    return
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            file = (STATIC / name).resolve()
            if STATIC.resolve() not in file.parents or not file.is_file():
                self._json(404, {"error": "not found"})
                return
            content = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", TYPES.get(file.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self) -> None:
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            try:
                if self.path == "/api/scene":
                    with board.lock:
                        board.scene.update(body)
                    self._json(200, board.scene)
                elif self.path == "/api/reset":
                    board.reset()
                    self._json(200, {"reset": True})
                elif self.path == "/api/reset-all":
                    # The presenter's reset stops first: land the drone, hold it
                    # down, then clear the rule table, the route and the board.
                    post(url("FLIGHT_PORT", "/kill"), {})
                    with contextlib.suppress(ValueError):
                        # Refused while a real drone is still coming down; the
                        # stop above has already spent whatever was pending.
                        post(url("FLIGHT_PORT", "/reset"), {})
                    board.reset()
                    self._json(200, {"reset": True})
                elif self.path == "/api/kill":
                    self._json(
                        200,
                        post(url("FLIGHT_PORT", "/kill"), {"cut": bool(body.get("cut", False))}),
                    )
                elif self.path == "/api/stage":
                    self._json(200, run_stage(board, int(body.get("stage", 0))))
                elif self.path == "/api/cue":
                    target_sense = str(body.get("sense", "word"))
                    if target_sense not in SENSE_PORT:
                        raise ValueError(f"unknown sense {target_sense!r}")
                    self._json(200, post(url(SENSE_PORT[target_sense], "/cue"), body))
                elif self.path == "/api/link":
                    node = str(body.get("node", ""))
                    if node not in DEVICE_PORT:
                        raise ValueError(f"no device behind {node!r}")
                    self._json(200, post(url(DEVICE_PORT[node], "/link"), body))
                elif self.path == "/api/pipelines":
                    action = str(body.get("action", "start")).strip().lower()
                    if action not in ("start", "stop"):
                        raise ValueError(f"unknown pipeline action {action!r}")

                    def _toggle(act: str = action) -> None:
                        fn = cloud.rerun if act == "start" else cloud.stop
                        workers = [
                            threading.Thread(target=fn, args=(job,), daemon=True)
                            for job in cloud.JOBS
                        ]
                        for t in workers:
                            t.start()
                        for t in workers:
                            t.join()

                    threading.Thread(target=_toggle, daemon=True).start()
                    self._json(202, {"action": action, "jobs": list(cloud.JOBS)})
                elif self.path == "/api/mic":
                    with board.lock:
                        prev_clap = dict(board.devices.get("hear-clap", {}))
                    clap_res = post(url("CLAP_DEVICE_PORT", "/mic"), body)
                    try:
                        word_res = post(url("WORD_DEVICE_PORT", "/mic"), body)
                    except (ValueError, OSError):
                        with contextlib.suppress(ValueError, OSError):
                            post(
                                url("CLAP_DEVICE_PORT", "/mic"),
                                {
                                    "device": prev_clap.get("mic") or "dji",
                                    "input_mode": prev_clap.get("input", "fixture"),
                                },
                            )
                        raise
                    self._json(
                        200,
                        {
                            "clap": clap_res,
                            "word": word_res,
                            "mic": word_res.get("mic") or clap_res.get("mic"),
                            "input": word_res.get("input") or clap_res.get("input"),
                        },
                    )
                else:
                    self._json(404, {"error": "not found"})
            except (ValueError, OSError) as error:
                self._json(400, {"error": str(error)})

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def _open_stage_cam(spec: str) -> LiveCamera | None:
    if not spec or spec.lower() in ("none", "off", "false"):
        return None
    try:
        return LiveCamera(spec)
    except Exception as error:
        log.warning("stage camera %r unavailable: %s", spec, error)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage-cam", default=os.environ.get("RI_STAGE_CAM", DEFAULT_CAMERA))
    ap.add_argument("--stage-cam-name", default=os.environ.get("RI_STAGE_CAM_NAME", ""))
    ap.add_argument("--host", default=os.environ.get("RI_SCREEN_BIND", "127.0.0.1"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cam = _open_stage_cam(args.stage_cam)
    cam_name = args.stage_cam_name.strip() or (cam.device_name if cam is not None else None)
    board = Board(stage_cam=cam, stage_cam_name=cam_name)
    bus = Bus("screen", ("ri/sense/+", "ri/act", "ri/drone/state", "ri/device/+"))
    bus.on(board.on_message)
    bus.start()
    threading.Thread(target=board.poll_nodes, daemon=True).start()
    threading.Thread(target=disk.watch, daemon=True).start()
    listen = port("PORT")
    server = ThreadingHTTPServer((args.host, listen), make_handler(board))
    server.daemon_threads = True

    def handle_stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle_stop)
    log.info("screen on http://%s:%d/", args.host, listen)
    try:
        server.serve_forever()
    finally:
        if cam is not None:
            cam.close()
        with contextlib.suppress(Exception):
            bus.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
