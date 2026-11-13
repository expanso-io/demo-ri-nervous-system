"""The flight service: the decide node's rule table bolted to a drone adapter.

    uv run python -m ri.flight [--drone sim|codrone|tello]

The fly-drone pipeline posts every sense decision to POST /decide. The answer
is the act (or the hold, and why), which the pipeline publishes to ri/act.
Ten times a second the airframe's own state goes to ri/drone/state; the
screen draws the drone from that and nothing else.

POST /kill is the presenter's Escape key: land now, whatever the senses say.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ri.bus import ACT_TOPIC, DRONE_TOPIC, Bus
from ri.decide import Action, Decider
from ri.drone import STEP_M, WAKE_ALTITUDE_M, DroneAdapter, LedColor, LedMode, make_adapter
from ri.messages import MessageError, parse_decision
from ri.ports import port

log = logging.getLogger("ri.flight")

LEDS: dict[str, tuple[LedColor, LedMode]] = {
    "wake": ("white", "solid"),
    "up": ("blue", "solid"),
    "down": ("amber", "solid"),
    "stop": ("white", "solid"),
    "land": ("red", "solid"),
}


class Flight:
    def __init__(self, drone_name: str) -> None:
        self.drone_name = drone_name
        self.lock = threading.Lock()
        self.drone: DroneAdapter = make_adapter(drone_name)
        self.decider = Decider()
        self.bus = Bus("flight").start()
        self.last_action: Action | None = None
        self.decisions = 0

    def apply(self, action: Action) -> None:
        state = self.drone.state()
        if action.act in LEDS:
            self.drone.set_leds(*LEDS[action.act])
        if action.act == "wake":
            self.drone.take_off(WAKE_ALTITUDE_M)
        elif action.act == "up":
            self.drone.move_vertical(STEP_M)
        elif action.act == "down":
            self.drone.move_vertical(-STEP_M)
        elif action.act == "stop":
            self.drone.hover()
        elif action.act == "land" and state.phase != "landing":
            self.drone.land()

    def decide(self, raw: bytes) -> dict[str, Any]:
        decision = parse_decision(raw)
        with self.lock:
            self.decider.observe(decision)
            now = time.time()
            action = self.decider.decide(self.drone.state(), now)
            self.apply(action)
            self.last_action = action
            self.decisions += 1
        body = action.to_json()
        body["from"] = decision.label()
        body["sense"] = decision.sense
        return body

    def kill(self) -> dict[str, Any]:
        with self.lock:
            action = Action("land", "presenter stop", "land", t=time.time())
            if self.drone.state().airborne:
                self.drone.set_leds(*LEDS["land"])
                self.drone.land()
            self.last_action = action
        body = action.to_json()
        body["from"] = "presenter"
        self.bus.publish(ACT_TOPIC, body)
        return body

    def reset(self) -> dict[str, Any]:
        """Back to a dark drone on the floor, for the next take."""
        with self.lock:
            if self.drone_name != "sim":
                raise ValueError("reset only applies to the on-screen drone")
            self.drone = make_adapter("sim")
            self.decider = Decider()
            self.last_action = None
            self.decisions = 0
        return {"reset": True}

    def state_loop(self) -> None:
        previous = ""
        while True:
            with self.lock:
                state = self.drone.state()
                # Back to white once a step finishes, so colour always means
                # "what it is doing now".
                if state.phase == "hovering" and previous in ("climbing", "descending"):
                    self.drone.set_leds("white", "solid")
                    state = self.drone.state()
                previous = state.phase
            body = state.to_json()
            body["decisions"] = self.decisions
            self.bus.publish(DRONE_TOPIC, body)
            time.sleep(0.1)


def make_handler(flight: Flight) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/state":
                self._reply(200, flight.drone.state().to_json())
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                if self.path == "/decide":
                    self._reply(200, flight.decide(body))
                elif self.path == "/kill":
                    self._reply(200, flight.kill())
                elif self.path == "/reset":
                    self._reply(200, flight.reset())
                else:
                    self._reply(404, {"error": "not found"})
            except (MessageError, ValueError) as error:
                self._reply(400, {"error": str(error)})

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drone", default=os.environ.get("RI_DRONE", "sim"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    flight = Flight(args.drone)
    threading.Thread(target=flight.state_loop, daemon=True).start()
    listen = port("FLIGHT_PORT")
    server = ThreadingHTTPServer(("127.0.0.1", listen), make_handler(flight))
    log.info("flight service on 127.0.0.1:%d, drone %s", listen, args.drone)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
