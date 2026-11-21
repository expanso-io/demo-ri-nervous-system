"""The route: a fixed path the drone may follow and nothing else.

The path is decided here, before anyone is on stage. A marshal's point never
steers the drone; it only releases a leg of this path that has not been
flown yet. The legs go in either order (left then right, or right then left,
end in the same place), so whichever way the presenter points first is
flown. A leg already flown, or no signal, and the drone holds where it is.
"""

from __future__ import annotations

import math
from typing import Literal

from ri import room

Gesture = Literal["forward", "left", "right"]
GESTURES: tuple[Gesture, ...] = ("forward", "left", "right")

ROUTE_LEG_M = room.current().leg_m
_DIAGONAL = ROUTE_LEG_M * math.sqrt(0.5)
# Two legs, each one metre (on the stage) at 45 degrees: ahead and to the left, then ahead
# and to the right, which puts the drone over the landing pad.
ROUTE: tuple[Gesture, ...] = ("left", "right")
END = "end"

# (metres ahead, metres to the right) for the leg each signal releases.
MOVE: dict[Gesture, tuple[float, float]] = {
    "forward": (ROUTE_LEG_M, 0.0),
    "left": (_DIAGONAL, -_DIAGONAL),
    "right": (_DIAGONAL, _DIAGONAL),
}


def need(step: int) -> str:
    """The signal the next step asks for, or END once the path is flown."""
    return ROUTE[step] if 0 <= step < len(ROUTE) else END


def wanted(flown: list[str]) -> str:
    """Every leg not yet flown, as `left|right`, or END once the path is flown."""
    left = [g for g in ROUTE if g not in flown]
    return "|".join(left) if left else END
