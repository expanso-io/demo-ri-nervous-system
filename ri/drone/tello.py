"""DJI Tello through djitellopy.

Stub status: written against djitellopy's documented calls, not yet flown.
`djitellopy-sim` exposes the same API for desk testing. The Tello has no
programmable LEDs, so set_leds only records the intent for the screen.
Install on the presenter laptop only: `uv pip install djitellopy`.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ri.drone import DroneState, LedColor, LedMode, Leds, Phase
from ri.drone.threaded import CommandWorker

MIN_MOVE_CM = 20  # the Tello refuses smaller vertical moves


class TelloDrone:
    name = "tello"

    def __init__(self) -> None:
        try:
            from djitellopy import Tello  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "Tello SDK missing: uv pip install djitellopy on the presenter laptop"
            ) from error
        self._tello: Any = Tello()
        self._tello.connect()
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
            self._tello.takeoff()  # the Tello holds about 0.8 to 1 m after take-off
            self._set_phase("hovering")

        self._worker.submit("take_off", run)

    def land(self) -> None:
        self._target = 0.0
        self._set_phase("landing")

        def run() -> None:
            self._tello.land()
            self._set_phase("parked")

        self._worker.submit("land", run, preempt=True)

    def hover(self) -> None:
        self._worker.submit("hover", lambda: self._tello.send_rc_control(0, 0, 0, 0))

    def move_vertical(self, dz_m: float) -> None:
        self._target += dz_m
        self._set_phase("climbing" if dz_m > 0 else "descending")
        cm = max(MIN_MOVE_CM, round(abs(dz_m) * 100))

        def run() -> None:
            if dz_m > 0:
                self._tello.move_up(cm)
            else:
                self._tello.move_down(cm)
            self._set_phase("hovering")

        self._worker.submit("move_vertical", run)

    def set_leds(self, color: LedColor, mode: LedMode) -> None:
        self._leds = Leds(color, mode)

    def state(self) -> DroneState:
        try:
            altitude = self._tello.get_height() / 100.0
            battery: int | None = int(self._tello.get_battery())
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
