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
import contextlib
import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ri.bus import ACT_TOPIC, DRONE_TOPIC, Bus
from ri.decide import Action, Decider
from ri.drone import (
    MAX_ALTITUDE_M,
    STEP_M,
    WAKE_ALTITUDE_M,
    DroneAdapter,
    LedColor,
    LedMode,
    make_adapter,
)
from ri.messages import MessageError, parse_decision
from ri.ports import DEFAULT_DRONE, port
from ri.route import MOVE, ROUTE, need

LOW_BATTERY_PCT = 20
# After the presenter's stop nothing the room does can move the drone for
# this long: the stop has to hold while the landing is still noisy.
STOP_QUIET_S = 10.0
# The marshalled route is on unless RI_ROUTE=0, for a show where the camera
# faces a crowd and two raised arms must never move the drone.
ROUTE_ON = os.environ.get("RI_ROUTE", "1") != "0"
log = logging.getLogger("ri.flight")

# Each stage blinks one colour while the drone is getting there and shows
# another, solid, once it has arrived, so the room can see a step finish from
# the back of a hall. A stage is "wake", "up", or the leg being flown.
LEDS: dict[str, tuple[LedColor, LedMode]] = {
    "wake": ("amber", "blink"),
    "up": ("blue", "blink"),
    "down": ("blue", "blink"),
    "stop": ("white", "solid"),
    "left": ("cyan", "blink"),
    "right": ("pink", "blink"),
    "forward": ("cyan", "blink"),
    "land": ("red", "blink"),
}
ARRIVED: dict[str, LedColor] = {
    "wake": "green",  # at the start height
    "top": "purple",  # at the highest it may go
    "left": "yellow",
    "right": "white",  # route flown: waiting for the room's hands
    "forward": "yellow",
}
MOVING = ("taking_off", "climbing", "descending", "cruising")


class Flight:
    def __init__(self, drone_name: str) -> None:
        self.drone_name = drone_name
        self.lock = threading.Lock()
        self.drone: DroneAdapter = make_adapter(drone_name)
        self.decider = Decider()
        self.bus = Bus("flight").start()
        self.last_action: Action | None = None
        self.decisions = 0
        self.step = 0  # how much of the fixed route has been flown
        self.quiet_until = 0.0
        self.stage: str | None = None

    def apply(self, action: Action) -> None:
        state = self.drone.state()
        stage: str | None = action.act
        if action.act == "go":
            stage = ROUTE[self.step] if self.step < len(ROUTE) else None
        if stage in LEDS:
            self.drone.set_leds(*LEDS[stage])
            self.stage = "up" if stage == "down" else stage
        if action.act == "wake":
            self.step = 0
            self.drone.take_off(WAKE_ALTITUDE_M)
        elif action.act == "go" and self.step < len(ROUTE):
            self.drone.move_horizontal(*MOVE[ROUTE[self.step]])
            self.step += 1
        elif action.act == "up":
            self.drone.move_vertical(STEP_M)
        elif action.act == "down":
            self.drone.move_vertical(-STEP_M)
        elif action.act == "stop":
            self.drone.hover()
        elif action.act == "land" and state.phase != "landing":
            self.drone.land()

    def arrived_color(self, target_m: float) -> LedColor | None:
        """The colour for having finished the current stage, or None to keep it."""
        if self.stage == "up":
            return ARRIVED["top"] if target_m >= MAX_ALTITUDE_M - 0.01 else None
        return ARRIVED.get(self.stage or "")

    def decide(self, raw: bytes) -> dict[str, Any]:
        decision = parse_decision(raw)
        with self.lock:
            self.decider.observe(decision)
            now = time.time()
            if now < self.quiet_until:
                # Whatever was heard is spent, not saved for later.
                self.decider.suppress_pending()
                action = Action("hold", "presenter stop", None, t=now)
            else:
                wanted = need(self.step) if ROUTE_ON else None
                action = self.decider.decide(self.drone.state(), now, wanted)
                self.apply(action)
            self.last_action = action
            self.decisions += 1
            if action.act != "hold" or action.inputs:
                state = self.drone.state()
                log.info(
                    "%s: %s (from %s; %s at %.2f m)",
                    action.act,
                    action.because,
                    decision.label(),
                    state.phase,
                    state.altitude_m,
                )
        body = action.to_json()
        body["from"] = decision.label()
        body["sense"] = decision.sense
        return body

    def kill(self, *, cut: bool = False) -> dict[str, Any]:
        with self.lock:
            state = self.drone.state()
            self.decider.suppress_pending()
            self.quiet_until = time.time() + STOP_QUIET_S
            if cut:
                action = Action("land", "emergency motor cut", "land", t=time.time())
                self.drone.set_leds(*LEDS["land"])
                self.drone.emergency()
            else:
                action = Action("land", "presenter stop", "land", t=time.time())
                log.info("land: presenter stop (%s at %.2f m)", state.phase, state.altitude_m)
                # A real airframe is told to land whatever we believe its phase
                # is: the belief can be wrong, and the stop key must not be.
                if self.drone_name != "sim" or state.phase not in ("dark", "parked", "landing"):
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
            state = self.drone.state()
            if self.drone_name != "sim":
                if state.airborne or state.phase == "landing":
                    raise ValueError("cannot reset while real drone is airborne")
                self.decider.reset()
                self.last_action = None
                self.decisions = 0
                self.step = 0
                return {"reset": True}
            self.drone.close()
            self.drone = make_adapter("sim")
            self.decider = Decider()
            self.last_action = None
            self.decisions = 0
            self.step = 0
        return {"reset": True}

    def shutdown(self) -> None:
        with self.lock:
            with contextlib.suppress(Exception):
                self.drone.close()
            with contextlib.suppress(Exception):
                self.bus.stop()

    def state_loop(self) -> None:
        previous = ""
        warned_low = False
        while True:
            with self.lock:
                state = self.drone.state()
                # The colour changes the moment a step is finished.
                if state.phase == "hovering" and previous in MOVING:
                    arrived = self.arrived_color(state.target_m)
                    if arrived is not None:
                        self.drone.set_leds(arrived, "solid")
                        state = self.drone.state()
                previous = state.phase
            if state.battery is not None and state.battery < LOW_BATTERY_PCT and not warned_low:
                warned_low = True
                log.warning("low drone battery: %d%%", state.battery)
            body = state.to_json()
            body["decisions"] = self.decisions
            if ROUTE_ON:
                body["route"] = {"step": self.step, "legs": list(ROUTE), "need": need(self.step)}
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
                    parsed = json.loads(body) if body.strip() else {}
                    self._reply(200, flight.kill(cut=bool(parsed.get("cut", False))))
                elif self.path == "/reset":
                    self._reply(200, flight.reset())
                else:
                    self._reply(404, {"error": "not found"})
            except (MessageError, ValueError, json.JSONDecodeError) as error:
                self._reply(400, {"error": str(error)})

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drone", default=os.environ.get("RI_DRONE", DEFAULT_DRONE))
    ap.add_argument("--host", default=os.environ.get("RI_FLIGHT_BIND", "127.0.0.1"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    flight = Flight(args.drone)
    threading.Thread(target=flight.state_loop, daemon=True).start()
    listen = port("FLIGHT_PORT")
    server = ThreadingHTTPServer((args.host, listen), make_handler(flight))

    def handle_stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle_stop)
    log.info("flight service on %s:%d, drone %s", args.host, listen, args.drone)
    try:
        server.serve_forever()
    finally:
        flight.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
