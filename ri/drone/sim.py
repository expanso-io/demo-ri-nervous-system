"""Development stand-in for the airframe: kinematics, not a timer.

State advances from elapsed time and the last command, the way a real drone
reports altitude while it moves. Used for rehearsal without hardware; it is a
build tool and never named on an audience-facing surface.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

from ri.drone import DroneState, LedColor, LedMode, Leds, Phase, clamp_altitude

CLIMB_M_PER_S = 0.6
LAND_M_PER_S = 0.5
CRUISE_M_PER_S = 0.4
SETTLE_M = 0.01


class SimDrone:
    name = "sim"

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, battery: float | None = None
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._phase: Phase = "dark"
        self._altitude = 0.0
        self._target = 0.0
        self._leds = Leds()
        default_battery = float(os.environ.get("RI_SIM_BATTERY", "100"))
        self._battery = default_battery if battery is None else float(battery)
        self._last = clock()
        self._cruise_until = 0.0

    def _advance(self) -> None:
        now = self._clock()
        dt = max(0.0, now - self._last)
        self._last = now
        if self._phase in ("taking_off", "climbing", "descending", "hovering", "landing"):
            self._battery = max(0.0, self._battery - dt * 0.2)
        if self._phase == "cruising" and now >= self._cruise_until:
            self._phase = "hovering"
        speed = LAND_M_PER_S if self._phase == "landing" else CLIMB_M_PER_S
        gap = self._target - self._altitude
        step = speed * dt
        if abs(gap) <= max(step, SETTLE_M):
            self._altitude = self._target
            if self._phase in ("taking_off", "climbing", "descending"):
                self._phase = "hovering"
            elif self._phase == "landing":
                self._phase = "parked"
                self._leds = Leds("green", "breathing")
        else:
            self._altitude += step if gap > 0 else -step

    def take_off(self, altitude_m: float) -> None:
        with self._lock:
            self._advance()
            self._target = clamp_altitude(altitude_m)
            self._phase = "taking_off"

    def land(self) -> None:
        with self._lock:
            self._advance()
            self._target = 0.0
            self._phase = "landing"

    def hover(self) -> None:
        with self._lock:
            self._advance()
            if self._phase in ("climbing", "descending", "taking_off", "cruising"):
                self._target = self._altitude
                self._phase = "hovering"

    def move_horizontal(self, forward_m: float, right_m: float) -> None:
        with self._lock:
            self._advance()
            if self._phase != "hovering":
                return
            distance = abs(forward_m) + abs(right_m)
            self._cruise_until = self._clock() + distance / CRUISE_M_PER_S
            self._phase = "cruising"

    def move_vertical(self, dz_m: float) -> None:
        with self._lock:
            self._advance()
            self._target = clamp_altitude(self._target + dz_m)
            self._phase = "climbing" if dz_m > 0 else "descending"

    def set_leds(self, color: LedColor, mode: LedMode) -> None:
        with self._lock:
            self._leds = Leds(color, mode)

    def emergency(self) -> None:
        with self._lock:
            self._last = self._clock()
            self._target = 0.0
            self._altitude = 0.0
            self._phase = "parked"
            self._leds = Leds("red", "solid")

    def close(self) -> None:
        pass

    def state(self) -> DroneState:
        with self._lock:
            self._advance()
            return DroneState(
                adapter=self.name,
                phase=self._phase,
                altitude_m=self._altitude,
                target_m=self._target,
                leds=self._leds,
                battery=round(self._battery),
                t=time.time(),
            )
