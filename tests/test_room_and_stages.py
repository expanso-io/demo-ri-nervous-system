from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest

from ri import room
from ri import screen as screen_mod


def _numbers(name: str) -> dict[str, Any]:
    """The flight numbers a service started for this room really uses."""
    code = (
        "import json; from ri import drone, route; print(json.dumps({"
        "'wake': drone.WAKE_ALTITUDE_M, 'step': drone.STEP_M, 'max': drone.MAX_ALTITUDE_M,"
        "'left': route.MOVE['left'], 'right': route.MOVE['right']}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "RI_ROOM": name},
        capture_output=True,
        text=True,
        check=True,
    )
    numbers: dict[str, Any] = json.loads(out.stdout)
    return numbers


def test_a_small_room_shrinks_every_height_and_both_legs() -> None:
    large, small = _numbers("large"), _numbers("small")
    # The stage: a clap to 1 m, then "up, up, up" to 2 m.
    assert (large["wake"], large["step"], large["max"]) == pytest.approx((1.0, 1 / 3, 2.0))
    assert (small["wake"], small["step"], small["max"]) == (0.4, 0.1, 1.0)
    assert (room.ROOMS["large"].ups, room.ROOMS["large"].top_m) == (3, 2.0)
    assert (room.ROOMS["small"].ups, room.ROOMS["small"].top_m) == (5, 0.9)
    # The hall: five claps to 2 m, two ups of a metre to 4 m, 2 m legs, a hard climb.
    hall = room.ROOMS["hall"]
    assert (hall.wake_m, hall.step_m, hall.ups, hall.top_m, hall.ceiling_m) == (2, 1, 2, 4, 4)
    assert (hall.leg_m, hall.climb_max) == (2.0, 90)
    # Two 0.6 m diagonals: 0.85 m ahead of takeoff, back on the centre line.
    ahead = small["left"][0] + small["right"][0]
    assert ahead == pytest.approx(0.849, abs=0.001)
    assert small["left"][1] == pytest.approx(-0.424, abs=0.001)
    assert small["left"][1] + small["right"][1] == pytest.approx(0.0)
    assert large["left"][0] + large["right"][0] == pytest.approx(1.414, abs=0.001)


def test_the_room_is_remembered_between_launches(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(room, "REMEMBERED", tmp_path / "room")
    assert room.remembered() == "large"
    room.remember("small")
    assert room.remembered() == "small"
    (tmp_path / "room").write_text("ballroom\n")
    assert room.remembered() == "large"
    with pytest.raises(ValueError, match="large or small"):
        room.named("ballroom")


class _Board:
    """A drone that climbs one step per "up", like the real rule table."""

    def __init__(self, phase: str = "hovering", target: float | None = None) -> None:
        start = room.current().wake_m if target is None else target
        self.drone: dict[str, Any] = {"phase": phase, "target_m": start}
        self.said: list[tuple[str, str]] = []
        self.deaf = False

    def say(self, sense: str, value: str) -> None:
        self.said.append((sense, value))
        if not self.deaf:
            step = room.current().step_m
            self.drone = {**self.drone, "target_m": round(self.drone["target_m"] + step, 3)}


def _climb(board: _Board, **kw: Any) -> int:
    return screen_mod.climb(board, board.say, patience_s=0.3, tick_s=0.01, **kw)  # type: ignore[arg-type]


def test_stage_two_says_each_up_and_stops_at_the_top() -> None:
    board = _Board()
    ups = room.current().ups
    assert _climb(board) == ups
    assert board.said == [("word", "up")] * ups
    assert board.drone["target_m"] == pytest.approx(room.current().top_m, abs=0.01)
    # Pressed again at the top, it says nothing.
    assert _climb(board) == 0


def test_stage_two_never_speaks_to_a_drone_on_the_floor_or_one_that_stops_answering() -> None:
    for phase in ("dark", "parked", "landing"):
        assert _climb(_Board(phase=phase)) == 0
    deaf = _Board()
    deaf.deaf = True
    assert _climb(deaf) == 1
    landed_midway = _Board()
    original = landed_midway.say

    def say_then_land(sense: str, value: str) -> None:
        original(sense, value)
        if len(landed_midway.said) == 2:
            landed_midway.drone = {**landed_midway.drone, "phase": "landing"}

    assert screen_mod.climb(landed_midway, say_then_land, patience_s=0.3, tick_s=0.01) == 2  # type: ignore[arg-type]


def test_each_number_key_sends_what_the_room_would_have(monkeypatch: Any) -> None:
    sent: list[tuple[str, str]] = []

    def fake_force(sense: str, value: str) -> dict[str, Any]:
        sent.append((sense, value))
        return {"ok": True}

    monkeypatch.setattr(screen_mod, "force", fake_force)
    board = screen_mod.Board()
    for stage in (1, 3, 4, 5):
        screen_mod.run_stage(board, stage)
    assert sent == [("clap", "clap"), ("hands", "left"), ("hands", "right"), ("hands", "crowd")]
    with pytest.raises(ValueError, match="no stage"):
        screen_mod.run_stage(board, 6)
