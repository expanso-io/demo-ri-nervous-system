from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ri import flightlog


def _state(t: float, phase: str, alt: float, target: float, airborne: bool) -> dict[str, Any]:
    body = {
        "phase": phase,
        "altitude_m": alt,
        "target_m": target,
        "airborne": airborne,
        "leds": {"color": "blue", "mode": "blink"},
        "raw": {"elevation_m": 40.0 + alt, "range_m": alt},
    }
    return {"t": t, "topic": "ri/drone/state", "body": body}


def _flight(t0: float) -> list[dict[str, Any]]:
    return [
        _state(t0 - 1, "dark", 0.0, 0.0, False),
        {"t": t0, "topic": "ri/act", "body": {"act": "wake", "because": "clap", "from": "clap"}},
        _state(t0 + 0.4, "taking_off", 0.0, 2.0, True),
        _state(t0 + 6.0, "hovering", 2.0, 2.0, True),
        {"t": t0 + 7, "topic": "ri/act", "body": {"act": "up", "because": "word up", "from": "up"}},
        _state(t0 + 7.8, "climbing", 2.0, 3.0, True),
        _state(t0 + 10.0, "hovering", 3.0, 3.0, True),
        {"t": t0 + 11, "topic": "ri/act", "body": {"act": "land", "because": "jazz hands"}},
        _state(t0 + 12.0, "landing", 2.9, 0.0, True),
        _state(t0 + 18.0, "parked", 0.0, 0.0, False),
    ]


def test_the_recorder_keeps_every_message_and_thins_device_telemetry(tmp_path: Path) -> None:
    rec = flightlog.Recorder(tmp_path)
    rec.take("ri/act", b'{"act":"wake"}')
    rec.take("ri/device/see-hands", b'{"wave":[1,2],"arms":true}')
    rec.take("ri/device/see-hands", b'{"wave":[1,2],"arms":true}')  # too soon: dropped
    rec.take("ri/sense/word", b"not json")
    rec.close()
    rows = [json.loads(line) for line in rec.path.read_text().splitlines()]
    assert [r["topic"] for r in rows] == ["rig", "ri/act", "ri/device/see-hands"]
    assert rows[2]["body"] == {"arms": True}


def test_show_cuts_flights_and_times_each_move(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "20261121-170000.jsonl"
    rows = [{"t": 0, "topic": "rig", "body": {"summary": "hall"}}, *_flight(100), *_flight(200)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert len(flightlog.flights(flightlog.load(path))) == 2
    assert flightlog.show(1, tmp_path) == 0
    out = capsys.readouterr().out
    assert "climbing" in out and "started 0.8 s after the ask" in out
    assert "climbing took 2.2 s, 2.00 -> 3.00 m" in out
    assert "baro +3.00 m" in out
    assert "highest 3.00 m, 18.0 s from wake to down" in out
    assert flightlog.show(3, tmp_path) == 1


def test_old_recordings_are_pruned(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"2026112{i}-000000.jsonl").write_text("")
    flightlog.prune(tmp_path, keep=2)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "20261123-000000.jsonl",
        "20261124-000000.jsonl",
    ]
