"""The room the flight is sized for.

    just up large    the stage: lift to 0.5 m, climb to 1.0 m, legs of 1.0 m
    just up small    a hotel room: lift to 0.4 m, climb to 0.9 m, legs of 0.6 m

The launcher hands the choice to every service as RI_ROOM and remembers it in
.runtime/room, so `just again` flies the same room until told otherwise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ri.ports import RUNTIME

UPS = 5
DEFAULT = "large"
REMEMBERED = RUNTIME / "room"


@dataclass(frozen=True)
class Room:
    name: str
    wake_m: float  # where a clap lifts the drone to
    step_m: float  # one "up"
    ceiling_m: float  # never above this, whatever is said
    leg_m: float  # each diagonal leg of the route

    @property
    def top_m(self) -> float:
        """Where the drone sits after the five ups of the show."""
        return round(self.wake_m + UPS * self.step_m, 3)

    @property
    def fence_m(self) -> float:
        """Further than this from where it took off, the drone lands.

        The two legs end `leg_m` times root two ahead; half a metre is slack.
        """
        return round(self.leg_m * 2**0.5 + 0.5, 2)

    def summary(self) -> str:
        return (
            f"{self.name}: lift {self.wake_m:.1f} m, climb to {self.top_m:.1f} m, "
            f"ceiling {self.ceiling_m:.1f} m, legs {self.leg_m:.1f} m, "
            f"lands {self.fence_m:.1f} m from take-off"
        )


ROOMS: dict[str, Room] = {
    "large": Room("large", wake_m=0.5, step_m=0.1, ceiling_m=1.5, leg_m=1.0),
    # About a king-size bed of clear floor: the two legs end 0.85 m ahead of
    # takeoff and swing 0.42 m to the left on the way.
    "small": Room("small", wake_m=0.4, step_m=0.1, ceiling_m=1.0, leg_m=0.6),
}


def named(name: str) -> Room:
    try:
        return ROOMS[name.strip().lower()]
    except KeyError:
        raise ValueError(f"no room {name!r}: choose {' or '.join(ROOMS)}") from None


def remembered() -> str:
    """The room the rig was last started in, or the default."""
    try:
        name = REMEMBERED.read_text().strip().lower()
    except OSError:
        return DEFAULT
    return name if name in ROOMS else DEFAULT


def remember(name: str) -> None:
    REMEMBERED.parent.mkdir(exist_ok=True)
    REMEMBERED.write_text(named(name).name + "\n")


def current() -> Room:
    """The room this process was started for."""
    return named(os.environ.get("RI_ROOM") or DEFAULT)
