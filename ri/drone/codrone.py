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

from ri.drone import AIRBORNE, DroneState, LedColor, LedMode, Leds, Phase, clamp_altitude
from ri.drone.threaded import CommandWorker

log = logging.getLogger("ri.drone.codrone")
# At or below this the drone is on the floor and its motors may be cut.
GROUND_M = 0.15

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
        self, sdk: Any | None = None, *, port_name: str | None = None, settle_s: float = 4.0
    ) -> None:
        self._settle_s = settle_s
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
            self._drone.takeoff()
            self._set_phase("hovering")

        self._worker.submit("take_off", run)

    def land(self) -> None:
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
            # Duration and throttle tuned for CoDrone EDU ~0.3m/s vertical step.
            self._drone.set_throttle(40 if delta > 0 else -40)
            self._drone.move(abs(delta) * 1.6)
            self._drone.set_throttle(0)
            self._drone.hover(0.3)
            with self._lock:
                if self._phase in ("climbing", "descending"):
                    self._phase = "hovering"

        self._worker.submit("move_vertical", run)

    def move_horizontal(self, forward_m: float, right_m: float) -> None:
        with self._lock:
            if self._phase != "hovering":
                return
            self._phase = "cruising"

        def run() -> None:
            # CONFIRM on the airframe: distances are the SDK's own estimate.
            if forward_m:
                cm = round(abs(forward_m) * 100)
                if forward_m > 0:
                    self._drone.move_forward(cm, "cm", 0.5)
                else:
                    self._drone.move_backward(cm, "cm", 0.5)
            if right_m:
                cm = round(abs(right_m) * 100)
                if right_m > 0:
                    self._drone.move_right(cm, "cm", 0.5)
                else:
                    self._drone.move_left(cm, "cm", 0.5)
            self._drone.hover(0.3)
            with self._lock:
                if self._phase == "cruising":
                    self._phase = "hovering"

        self._worker.submit("move_horizontal", run)

    def set_leds(self, color: LedColor, mode: LedMode) -> None:
        self._leds = Leds(color, mode)
        r, g, b = RGB[color]
        self._worker.submit("set_leds", lambda: self._drone.set_drone_LED(r, g, b, 100))

    def emergency(self) -> None:
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
