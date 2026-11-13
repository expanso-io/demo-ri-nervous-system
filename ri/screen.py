"""The audience screen's server: one state object, streamed ten times a second.

    uv run python -m ri.screen

Everything the screen draws comes from somewhere real: device levels and the
sense decisions from the MQTT bus, the acts from fly-drone's output, the
drone from its own state topic, and whether each node is live from that
Expanso Edge agent's local API. Bound to 127.0.0.1 only; the presenter keys
(kill, scene, link fault) live here too.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ri.bus import Bus
from ri.messages import PIPELINE_FOR
from ri.ports import port, url

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
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
}


class Board:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.devices: dict[str, dict[str, Any]] = {}
        self.nodes: dict[str, dict[str, Any]] = {
            n: {"live": False, "agent": False, "sent": 0} for n in NODE_API
        }
        self.latest: dict[str, dict[str, Any]] = {}
        self.act: dict[str, Any] | None = None
        self.acts: deque[dict[str, Any]] = deque(maxlen=8)
        self.drone: dict[str, Any] = {}
        self.scene: dict[str, Any] = {
            "beat": "hook",
            "prompt": "This drone has no brain of its own. You are its nervous system.",
        }
        self.pulses: deque[dict[str, Any]] = deque(maxlen=64)
        self.pulse_seq = 0
        self.decision_bytes = 0
        self.decision_count = 0
        self.started = time.time()

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
                body["seen_at"] = time.time()
                self.latest[sense] = body
                self.decision_bytes += len(payload)
                self.decision_count += 1
                node = PIPELINE_FOR.get(sense, sense)  # type: ignore[call-overload]
                self.nodes[node]["sent"] += 1
                self.pulse("sense", node)
            elif topic == "ri/act":
                body["raw"] = payload.decode()
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
    with urllib.request.urlopen(request, timeout=3) as response:
        result: dict[str, Any] = json.loads(response.read())
        return result


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
                elif self.path == "/api/kill":
                    self._json(200, post(url("FLIGHT_PORT", "/kill"), {}))
                elif self.path == "/api/link":
                    node = str(body.get("node", ""))
                    if node not in DEVICE_PORT:
                        raise ValueError(f"no device behind {node!r}")
                    self._json(200, post(url(DEVICE_PORT[node], "/link"), body))
                else:
                    self._json(404, {"error": "not found"})
            except (ValueError, OSError) as error:
                self._json(400, {"error": str(error)})

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    board = Board()
    bus = Bus("screen", ("ri/sense/+", "ri/act", "ri/drone/state", "ri/device/+"))
    bus.on(board.on_message)
    bus.start()
    threading.Thread(target=board.poll_nodes, daemon=True).start()
    listen = port("PORT")
    server = ThreadingHTTPServer(("127.0.0.1", listen), make_handler(board))
    server.daemon_threads = True
    log.info("screen on http://127.0.0.1:%d/", listen)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
