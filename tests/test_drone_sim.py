from __future__ import annotations

from ri.drone import make_adapter
from ri.drone.sim import SimDrone


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_take_off_climbs_then_hovers() -> None:
    clock = Clock()
    drone = SimDrone(clock)
    assert drone.state().phase == "dark"
    drone.take_off(1.0)
    clock.t = 0.5
    mid = drone.state()
    assert mid.phase == "taking_off"
    assert 0.0 < mid.altitude_m < 1.0
    clock.t = 3.0
    done = drone.state()
    assert (done.phase, done.altitude_m) == ("hovering", 1.0)


def test_move_and_land_report_real_progress() -> None:
    clock = Clock()
    drone = SimDrone(clock)
    drone.take_off(1.0)
    clock.t = 3.0
    drone.move_vertical(0.5)
    clock.t = 3.4
    assert drone.state().phase == "climbing"
    clock.t = 5.0
    assert drone.state().altitude_m == 1.5
    drone.land()
    clock.t = 6.0
    assert drone.state().phase == "landing"
    clock.t = 10.0
    parked = drone.state()
    assert (parked.phase, parked.altitude_m) == ("parked", 0.0)
    assert (parked.leds.color, parked.leds.mode) == ("green", "breathing")


def test_hover_stops_a_climb_where_it_is() -> None:
    clock = Clock()
    drone = SimDrone(clock)
    drone.take_off(1.0)
    clock.t = 3.0
    drone.move_vertical(0.5)
    clock.t = 3.25
    drone.hover()
    clock.t = 6.0
    state = drone.state()
    assert state.phase == "hovering"
    assert 1.0 < state.altitude_m < 1.5


def test_factory_names() -> None:
    assert make_adapter("sim").name == "sim"
