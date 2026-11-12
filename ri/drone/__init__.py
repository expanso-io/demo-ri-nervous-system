"""The drone adapter: one small interface, three implementations.

`sim` is the development stand-in used for rehearsal without hardware.
`codrone` (Robolink CoDrone EDU) and `tello` (DJI Tello) drive real
airframes through their SDKs. On stage the real adapter is plugged in and
nothing on the presenter surface changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

Phase = Literal["dark", "taking_off", "hovering", "climbing", "descending", "landing", "parked"]
AIRBORNE: frozenset[str] = frozenset({"taking_off", "hovering", "climbing", "descending"})

# LED colours carry meaning on stage: white on wake, the direction's colour
# on a turn, the reflex colour on land, green breathing once parked.
LedColor = Literal["off", "white", "blue", "amber", "red", "green"]
LedMode = Literal["off", "solid", "breathing"]

WAKE_ALTITUDE_M = 1.0
MAX_ALTITUDE_M = 2.0
STEP_M = 0.5


@dataclass(frozen=True)
class Leds:
    color: LedColor = "off"
    mode: LedMode = "off"


@dataclass(frozen=True)
class DroneState:
    """What the airframe reports. The screen draws only this."""

    adapter: str
    phase: Phase
    altitude_m: float
    target_m: float
    leds: Leds = field(default_factory=Leds)
    battery: int | None = None
    t: float = 0.0

    @property
    def airborne(self) -> bool:
        return self.phase in AIRBORNE

    def to_json(self) -> dict[str, Any]:
        body = asdict(self)
        body["altitude_m"] = round(self.altitude_m, 2)
        body["target_m"] = round(self.target_m, 2)
        body["airborne"] = self.airborne
        return body


class DroneAdapter(Protocol):
    """Six calls. Every implementation must return promptly from each."""

    name: str

    def take_off(self, altitude_m: float) -> None: ...

    def land(self) -> None: ...

    def hover(self) -> None: ...

    def move_vertical(self, dz_m: float) -> None: ...

    def set_leds(self, color: LedColor, mode: LedMode) -> None: ...

    def state(self) -> DroneState: ...


def make_adapter(name: str) -> DroneAdapter:
    """Build the adapter named on the command line or in RI_DRONE."""
    if name == "sim":
        from ri.drone.sim import SimDrone

        return SimDrone()
    if name == "codrone":
        from ri.drone.codrone import CoDroneEdu

        return CoDroneEdu()
    if name == "tello":
        from ri.drone.tello import TelloDrone

        return TelloDrone()
    raise ValueError(f"unknown drone adapter: {name!r} (sim, codrone, tello)")
