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


def test_a_route_step_cruises_then_hovers_and_only_from_a_hover() -> None:
    clock = Clock()
    drone = SimDrone(clock)
    drone.move_horizontal(0.25, 0.0)
    assert drone.state().phase == "dark"
    drone.take_off(1.0)
    clock.t = 3.0
    drone.move_horizontal(0.25, 0.0)
    clock.t = 3.2
    assert drone.state().phase == "cruising"
    clock.t = 4.0
    assert (drone.state().phase, drone.state().altitude_m) == ("hovering", 1.0)


def test_the_whole_show_wave_left_wave_right_then_the_room_lands_it() -> None:
    from ri.decide import Decider
    from ri.messages import Decision
    from ri.route import MOVE, ROUTE, need

    clock = Clock()
    drone = SimDrone(clock)
    drone.take_off(0.5)
    clock.t = 3.0
    decider = Decider()
    step = 0
    flown: list[str] = []
    # Every signal is shown three times over: extra waves must not move it.
    shows = ["right", "right", "left", "left", "left", "forward", "right", "right", "left", "left"]
    for seq, shown in enumerate(shows, start=1):
        clock.t += 4.0
        now = 1000.0 + seq * 4
        decider.observe(Decision("hands", now, up=2, seen=1, share=1.0, gesture=shown, seq=seq))
        action = decider.decide(drone.state(), now, need(step))
        assert action.act in ("go", "hold")
        if action.act == "go":
            flown.append(shown)
            drone.move_horizontal(*MOVE[ROUTE[step]])
            step += 1
    assert flown == ["left", "right"]
    clock.t += 4.0
    assert drone.state().phase == "hovering"

    # Over the pad: nineteen hands hold it there, twenty bring it down.
    for seq, up in ((90, 19), (91, 20)):
        now = 2000.0 + seq
        decider.observe(Decision("hands", now, up=up, seen=14, share=1.0, seq=seq))
        action = decider.decide(drone.state(), now, need(step))
        assert action.act == ("land" if up >= 20 else "hold")
    drone.land()
    clock.t += 5.0
    assert drone.state().phase == "parked"
