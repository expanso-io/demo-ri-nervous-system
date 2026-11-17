"""The route: a fixed path the drone may follow and nothing else.

The path is decided here, before anyone is on stage. A marshal's wave never
steers the drone; it only releases the next step of this path, and only when
it is the signal that step asks for. Any other signal, or none, and the drone
holds where it is.
"""

from __future__ import annotations

from typing import Literal

Gesture = Literal["forward", "left", "right"]
GESTURES: tuple[Gesture, ...] = ("forward", "left", "right")

ROUTE_STEP_M = 0.25
# Ahead, a jog to the left, ahead, back onto the centre line, ahead.
ROUTE: tuple[Gesture, ...] = (
    "forward",
    "forward",
    "forward",
    "left",
    "left",
    "forward",
    "forward",
    "right",
    "right",
    "forward",
    "forward",
)
END = "end"

# (metres ahead, metres to the right) for one step of each signal.
MOVE: dict[Gesture, tuple[float, float]] = {
    "forward": (ROUTE_STEP_M, 0.0),
    "left": (0.0, -ROUTE_STEP_M),
    "right": (0.0, ROUTE_STEP_M),
}


def need(step: int) -> str:
    """The signal the next step asks for, or END once the path is flown."""
    return ROUTE[step] if 0 <= step < len(ROUTE) else END
