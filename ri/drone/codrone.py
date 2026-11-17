"""Robolink CoDrone EDU through its Python SDK (`codrone-edu` on PyPI).

Stub status: written against the published SDK calls, not yet flown. Every
call marked CONFIRM must be checked on the airframe before it is trusted on
stage. Install the SDK only on the presenter laptop:
`uv pip install codrone-edu`; the USB controller must be paired first.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

from ri.drone import (
    AIRBORNE,
    MAX_ALTITUDE_M,
    DroneState,
    LedColor,
    LedMode,
    Leds,
    Phase,
    clamp_altitude,
)
from ri.drone.threaded import CommandWorker

log = logging.getLogger("ri.drone.codrone")
# At or below this the drone is on the floor and its motors may be cut.
GROUND_M = 0.15
LOWEST_M = 0.3
SENSOR_MAX_M = 3.0
# The slowest the airframe's own position moves go, in metres a second.
MOVE_SPEED = 0.5
CEILING_SLACK_M = 0.15

RGB: dict[str, tuple[int, int, int]] = {
    "off": (0, 0, 0),
    "white": (255, 255, 255),
    "blue": (0, 140, 255),
    "amber": (255, 150, 0),
    "red": (255, 0, 0),
    "green": (0, 255, 60),
}


class CoDroneEdu:
    name = "codrone"

    def __init__(
        self,
        sdk: Any | None = None,
        *,
        port_name: str | None = None,
        settle_s: float = 10.0,
        grounded_s: float = 3.0,
    ) -> None:
        self._settle_s = settle_s
        self._grounded_s = grounded_s
        self._floor_since: float | None = None
        self._abort = threading.Event()
        if sdk is None:
            try:
                from codrone_edu.drone import Drone
            except ImportError as error:
                raise RuntimeError(
                    "CoDrone EDU SDK missing: uv pip install codrone-edu on the presenter laptop"
                ) from error
            self._drone: Any = Drone()
            self._drone.pair(port_name) if port_name else self._drone.pair()
        else:
            self._drone = sdk
        self._worker = CommandWorker(self.name)
        self._lock = threading.Lock()
        self._phase: Phase = "dark"
        self._target = 0.0
        self._altitude = 0.0
        self._battery: int | None = None
        self._leds = Leds()

    def _set_phase(self, phase: Phase) -> None:
        with self._lock:
            self._phase = phase

    def take_off(self, altitude_m: float) -> None:
        self._target = clamp_altitude(altitude_m)
        self._set_phase("taking_off")

        def run() -> None:
            self._abort.clear()
            self._drone.takeoff()
            # The airframe picks its own take-off height; bring it to ours.
            self._fly_to(self._target)
            with self._lock:
                if self._phase == "taking_off":
                    self._phase = "hovering"

        self._worker.submit("take_off", run)

    def land(self) -> None:
        self._abort.set()
        self._target = 0.0
        self._set_phase("landing")

        def run() -> None:
            self._drone.land()
            self._stop_props_on_the_ground()
            self._leds = Leds("green", "breathing")
            with contextlib.suppress(Exception):
                r, g, b = RGB["green"]
                self._drone.set_drone_LED(r, g, b, 100)
            self._set_phase("parked")

        self._worker.submit("land", run, preempt=True)

    def _stop_props_on_the_ground(self) -> None:
        """Cut the motors once the drone has landed, and only then.

        The motors are cut only after the height sensor reads the floor. If it
        never does, they are left alone: cutting them in the air is a fall.
        CONFIRM on the airframe.
        """
        deadline = time.monotonic() + self._settle_s
        while True:
            try:
                if float(self._drone.get_height("m")) <= GROUND_M:
                    self._drone.emergency_stop()
                    return
            except Exception:
                log.debug("height poll failed while settling", exc_info=True)
            if time.monotonic() >= deadline:
                log.warning("landed but height never read the floor; motors left alone")
                return
            time.sleep(0.1)

    def _notice_the_floor(self, now: float) -> None:
        """A drone that says it is hovering while it sits on the floor is not flying.

        The phase is kept here, not read from the airframe, so a drone that is
        picked up, unplugged, or lands on a low battery would otherwise be
        "hovering" for ever and ignore every wake. Called with the lock held.
        """
        if self._phase != "hovering" or self._altitude > GROUND_M:
            self._floor_since = None
            return
        if self._floor_since is None:
            self._floor_since = now
        elif now - self._floor_since >= self._grounded_s:
            log.warning("hovering but on the floor for %.0f s: parked", self._grounded_s)
            self._phase = "parked"
            self._target = 0.0
            self._floor_since = None

    def hover(self) -> None:
        def run() -> None:
            self._drone.hover(0.5)
            with self._lock:
                if self._phase in ("climbing", "descending", "taking_off", "cruising"):
                    self._phase = "hovering"

        self._worker.submit("hover", run)

    def move_vertical(self, dz_m: float) -> None:
        with self._lock:
            new_target = clamp_altitude(self._target + dz_m)
            delta = new_target - self._target
            if abs(delta) < 0.05:
                return
            self._target = new_target
            self._phase = "climbing" if delta > 0 else "descending"

        def run() -> None:
            self._abort.clear()
            self._fly_to(new_target)
            with self._lock:
                if self._phase in ("climbing", "descending"):
                    self._phase = "hovering"

        self._worker.submit("move_vertical", run)

    def _height(self) -> float | None:
        try:
            height = float(self._drone.get_height("m"))
        except Exception:
            log.debug("height read failed", exc_info=True)
            return None
        # The sensor answers 10 m when it sees nothing. That is not a height.
        return height if 0.0 <= height <= SENSOR_MAX_M else None

    def _move_by(self, ahead_m: float, left_m: float, up_m: float) -> None:
        """One position move, held on line by the airframe's own position hold.

        Raw throttle lets the drone wander sideways while it climbs. A
        position move tells the flight controller where to end up, so a
        climb is straight up and a leg is one straight line. The wait can be
        cut short by a land, and the ceiling is watched the whole way.
        """
        distance = (ahead_m**2 + left_m**2 + up_m**2) ** 0.5
        for _ in range(3):
            self._drone.sendControlPosition(
                float(ahead_m), float(left_m), float(up_m), MOVE_SPEED, 0, 0
            )
            time.sleep(0.05)
        deadline = time.monotonic() + distance / MOVE_SPEED + 1.0
        while time.monotonic() < deadline:
            if self._abort.wait(0.1):
                return
            height = self._height()
            if height is not None and height > MAX_ALTITUDE_M + CEILING_SLACK_M:
                log.warning("%.2f m is over the ceiling: holding", height)
                break
        self._drone.hover(0.3)

    def _fly_to(self, goal_m: float) -> None:
        """Climb or sink straight to `goal_m`, measured, under a hard ceiling."""
        start = self._height()
        if start is None:
            log.warning("no height reading: height move skipped")
            return
        goal = max(LOWEST_M, min(MAX_ALTITUDE_M, goal_m))
        if abs(goal - start) < 0.04:
            log.info("already at %.2f m (goal %.2f)", start, goal)
            return
        self._move_by(0.0, 0.0, goal - start)
        log.info("height %.2f -> %s m (goal %.2f)", start, self._height(), goal)

    def move_horizontal(self, forward_m: float, right_m: float) -> None:
        with self._lock:
            if self._phase != "hovering":
                return
            self._phase = "cruising"

        def run() -> None:
            # One straight line, diagonal when the leg is: the airframe's own
            # position move, ahead positive and left positive, at its slowest.
            # CONFIRM on the airframe: the distance is the SDK's own estimate.
            self._abort.clear()
            self._move_by(forward_m, -right_m, 0.0)
            with self._lock:
                if self._phase == "cruising":
                    self._phase = "hovering"

        self._worker.submit("move_horizontal", run)

    def set_leds(self, color: LedColor, mode: LedMode) -> None:
        self._leds = Leds(color, mode)
        r, g, b = RGB[color]
        self._worker.submit("set_leds", lambda: self._drone.set_drone_LED(r, g, b, 100))

    def emergency(self) -> None:
        self._abort.set()
        self._target = 0.0
        self._leds = Leds("red", "solid")
        self._set_phase("parked")
        self._worker.submit("emergency", lambda: self._drone.emergency_stop(), preempt=True)
        with contextlib.suppress(Exception):
            self._drone.emergency_stop()

    def close(self) -> None:
        with self._lock:
            needs_land = self._phase in AIRBORNE
        if needs_land:
            with contextlib.suppress(Exception):
                self._drone.land()
            self._set_phase("parked")
        self._worker.close()
        with contextlib.suppress(Exception):
            self._drone.close()

    def state(self) -> DroneState:
        try:
            altitude = float(self._drone.get_height("m"))
            battery: int | None = int(self._drone.get_battery())
            with self._lock:
                self._altitude = max(0.0, altitude)
                self._battery = battery
                self._notice_the_floor(time.monotonic())
        except Exception:
            log.debug("codrone telemetry poll failed", exc_info=True)
        with self._lock:
            phase = self._phase
            altitude = self._altitude if self._phase != "dark" else 0.0
            battery = self._battery
            target = self._target
            leds = self._leds
        return DroneState(
            adapter=self.name,
            phase=phase,
            altitude_m=altitude,
            target_m=target,
            leds=leds,
            battery=battery,
            t=time.time(),
        )
