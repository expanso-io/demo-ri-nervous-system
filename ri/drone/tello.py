"""DJI Tello through djitellopy.

Stub status: written against djitellopy's documented calls, not yet flown.
`djitellopy-sim` exposes the same API for desk testing. The Tello has no
programmable LEDs, so set_leds only records the intent for the screen.
Install on the presenter laptop only: `uv pip install djitellopy`.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

from ri.drone import AIRBORNE, DroneState, LedColor, LedMode, Leds, Phase, clamp_altitude
from ri.drone.threaded import CommandWorker

MIN_MOVE_CM = 20  # the Tello refuses smaller vertical moves
log = logging.getLogger("ri.drone.tello")


class TelloDrone:
    name = "tello"

    def __init__(self, sdk: Any | None = None) -> None:
        if sdk is None:
            try:
                from djitellopy import Tello  # type: ignore[import-not-found]
            except ImportError as error:
                raise RuntimeError(
                    "Tello SDK missing: uv pip install djitellopy on the presenter laptop"
                ) from error
            self._tello: Any = Tello()
            self._tello.connect()
        else:
            self._tello = sdk
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
            self._tello.takeoff()  # the Tello holds about 0.8 to 1 m after take-off
            self._set_phase("hovering")

        self._worker.submit("take_off", run)

    def land(self) -> None:
        self._target = 0.0
        self._set_phase("landing")

        def run() -> None:
            self._tello.land()
            self._leds = Leds("green", "breathing")
            self._set_phase("parked")

        self._worker.submit("land", run, preempt=True)

    def hover(self) -> None:
        def run() -> None:
            self._tello.send_rc_control(0, 0, 0, 0)
            with self._lock:
                if self._phase in ("climbing", "descending", "taking_off"):
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
        cm = max(MIN_MOVE_CM, round(abs(delta) * 100))

        def run() -> None:
            if delta > 0:
                self._tello.move_up(cm)
            else:
                self._tello.move_down(cm)
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
            if forward_m:
                cm = max(MIN_MOVE_CM, round(abs(forward_m) * 100))
                if forward_m > 0:
                    self._tello.move_forward(cm)
                else:
                    self._tello.move_back(cm)
            if right_m:
                cm = max(MIN_MOVE_CM, round(abs(right_m) * 100))
                if right_m > 0:
                    self._tello.move_right(cm)
                else:
                    self._tello.move_left(cm)
            with self._lock:
                if self._phase == "cruising":
                    self._phase = "hovering"

        self._worker.submit("move_horizontal", run)

    def set_leds(self, color: LedColor, mode: LedMode) -> None:
        self._leds = Leds(color, mode)

    def emergency(self) -> None:
        self._target = 0.0
        self._leds = Leds("red", "solid")
        self._set_phase("parked")
        self._worker.submit("emergency", lambda: self._tello.emergency(), preempt=True)
        with contextlib.suppress(Exception):
            self._tello.emergency()

    def close(self) -> None:
        with self._lock:
            needs_land = self._phase in AIRBORNE
        if needs_land:
            with contextlib.suppress(Exception):
                self._tello.land()
            self._set_phase("parked")
        self._worker.close()
        with contextlib.suppress(Exception):
            self._tello.end()

    def state(self) -> DroneState:
        try:
            altitude = float(self._tello.get_height()) / 100.0
            battery: int | None = int(self._tello.get_battery())
            with self._lock:
                self._altitude = max(0.0, altitude)
                self._battery = battery
        except Exception:
            log.debug("tello telemetry poll failed", exc_info=True)
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
