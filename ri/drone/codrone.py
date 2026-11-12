"""Robolink CoDrone EDU through its Python SDK (`codrone-edu` on PyPI).

Stub status: written against the published SDK calls, not yet flown. Every
call marked CONFIRM must be checked on the airframe before it is trusted on
stage. Install the SDK only on the presenter laptop:
`uv pip install codrone-edu`; the USB controller must be paired first.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ri.drone import DroneState, LedColor, LedMode, Leds, Phase
from ri.drone.threaded import CommandWorker

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

    def __init__(self) -> None:
        try:
            from codrone_edu.drone import Drone  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "CoDrone EDU SDK missing: uv pip install codrone-edu on the presenter laptop"
            ) from error
        self._drone: Any = Drone()
        self._drone.pair()  # CONFIRM: pairs over the USB controller
        self._worker = CommandWorker(self.name)
        self._lock = threading.Lock()
        self._phase: Phase = "dark"
        self._target = 0.0
        self._leds = Leds()

    def _set_phase(self, phase: Phase) -> None:
        with self._lock:
            self._phase = phase

    def take_off(self, altitude_m: float) -> None:
        self._target = altitude_m
        self._set_phase("taking_off")

        def run() -> None:
            self._drone.takeoff()  # CONFIRM: climbs to about 1 m and holds
            self._set_phase("hovering")

        self._worker.submit("take_off", run)

    def land(self) -> None:
        self._target = 0.0
        self._set_phase("landing")

        def run() -> None:
            self._drone.land()
            self._set_phase("parked")

        self._worker.submit("land", run, preempt=True)

    def hover(self) -> None:
        self._worker.submit("hover", lambda: self._drone.hover(0.5))

    def move_vertical(self, dz_m: float) -> None:
        self._target += dz_m
        self._set_phase("climbing" if dz_m > 0 else "descending")

        def run() -> None:
            # CONFIRM: throttle power and duration per 0.5 m on the real airframe.
            self._drone.set_throttle(40 if dz_m > 0 else -40)
            self._drone.move(abs(dz_m) * 1.6)
            self._drone.set_throttle(0)
            self._drone.hover(0.3)
            self._set_phase("hovering")

        self._worker.submit("move_vertical", run)

    def set_leds(self, color: LedColor, mode: LedMode) -> None:
        self._leds = Leds(color, mode)
        r, g, b = RGB[color]
        # CONFIRM: breathing needs set_drone_LED_mode on newer firmware.
        self._worker.submit("set_leds", lambda: self._drone.set_drone_LED(r, g, b, 100))

    def state(self) -> DroneState:
        try:
            altitude = float(self._drone.get_height("m"))  # CONFIRM: unit argument
            battery: int | None = int(self._drone.get_battery())
        except Exception:
            altitude, battery = self._target, None
        with self._lock:
            phase = self._phase
        return DroneState(
            adapter=self.name,
            phase=phase,
            altitude_m=altitude,
            target_m=self._target,
            leds=self._leds,
            battery=battery,
            t=time.time(),
        )
