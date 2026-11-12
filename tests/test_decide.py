from __future__ import annotations

import pytest

from ri.decide import FRESH_S, Decider
from ri.drone import DroneState, Phase
from ri.messages import Decision, MessageError, parse_decision

NOW = 1000.0


def drone(phase: Phase = "hovering", target: float = 1.0) -> DroneState:
    return DroneState("sim", phase, altitude_m=target, target_m=target)


def clap(t: float = NOW, conf: float = 0.93, seq: int = 1) -> Decision:
    return Decision("clap", t, confidence=conf, seq=seq)


def word(value: str, t: float = NOW, conf: float = 0.81, seq: int = 1) -> Decision:
    return Decision("word", t, confidence=conf, value=value, seq=seq)


def hands(up: int, seen: int, t: float = NOW, seq: int = 1) -> Decision:
    return Decision("hands", t, up=up, seen=seen, share=up / seen, seq=seq)


def test_clap_wakes_a_dark_drone() -> None:
    d = Decider()
    d.observe(clap())
    action = d.decide(drone("dark", 0.0), NOW)
    assert (action.act, action.rule) == ("wake", "wake")


def test_clap_wakes_a_parked_drone_but_not_a_landing_one() -> None:
    d = Decider()
    d.observe(clap())
    assert d.decide(drone("landing", 0.4), NOW).act == "hold"
    d.observe(clap(seq=2))
    assert d.decide(drone("parked", 0.0), NOW).act == "wake"


def test_weak_clap_is_ignored() -> None:
    d = Decider()
    d.observe(clap(conf=0.2))
    assert d.decide(drone("dark", 0.0), NOW).act == "hold"


def test_each_decision_is_acted_on_once() -> None:
    d = Decider()
    d.observe(word("up"))
    assert d.decide(drone(), NOW).act == "up"
    assert d.decide(drone(target=1.5), NOW + 0.5).act == "hold"


def test_up_and_down_move_one_step() -> None:
    d = Decider()
    d.observe(word("up", seq=1))
    assert d.decide(drone(target=1.0), NOW).act == "up"
    d.observe(word("down", seq=2, t=NOW + 1))
    assert d.decide(drone(target=1.5), NOW + 1).act == "down"


def test_envelope_ceiling_and_floor_hold() -> None:
    d = Decider()
    d.observe(word("up"))
    action = d.decide(drone(target=2.0), NOW)
    assert (action.act, action.because) == ("hold", "ceiling reached")
    d.observe(word("down", seq=2))
    assert d.decide(drone(target=1.0), NOW).because == "lowest safe height"


def test_words_do_nothing_while_asleep() -> None:
    d = Decider()
    d.observe(word("up"))
    action = d.decide(drone("dark", 0.0), NOW)
    assert (action.act, action.because) == ("hold", "asleep: clap to wake")


def test_stop_beats_move() -> None:
    d = Decider()
    d.observe(word("stop"))
    assert d.decide(drone(), NOW).act == "stop"


def test_stale_decision_holds_the_drone() -> None:
    d = Decider()
    d.observe(word("up", t=NOW - FRESH_S - 0.1))
    assert d.decide(drone(), NOW).act == "hold"
    d.observe(clap(t=NOW - FRESH_S - 0.1))
    assert d.decide(drone("dark", 0.0), NOW).act == "hold"


def test_future_timestamps_are_not_fresh() -> None:
    d = Decider()
    d.observe(word("up", t=NOW + FRESH_S + 1))
    assert d.decide(drone(), NOW).act == "hold"


def test_majority_of_hands_lands() -> None:
    d = Decider()
    d.observe(hands(37, 60))
    action = d.decide(drone(), NOW)
    assert (action.act, action.because) == ("land", "hands 0.62")


def test_below_majority_waits() -> None:
    d = Decider()
    d.observe(hands(18, 60))
    action = d.decide(drone(), NOW)
    assert action.act == "hold"
    assert action.waiting
    assert action.because == "waiting: 18 of 60"


def test_exactly_half_does_not_land() -> None:
    d = Decider()
    d.observe(hands(30, 60))
    assert d.decide(drone(), NOW).act == "hold"


def test_too_few_people_seen_cannot_land() -> None:
    d = Decider()
    d.observe(hands(8, 9))
    action = d.decide(drone(), NOW)
    assert (action.act, action.because) == ("hold", "waiting: 9 seen")


def test_conflict_landing_wins_and_the_word_loses() -> None:
    d = Decider()
    d.observe(hands(43, 60))
    d.observe(word("up", conf=0.77))
    action = d.decide(drone(target=1.5), NOW)
    assert action.act == "land"
    assert action.because == "hands 0.72 > word up"
    assert action.inputs == ("hands 72%", "up 0.77")
    # the shout was heard and lost: it is not replayed once the hands drop
    d.observe(hands(5, 60, t=NOW + 0.5, seq=2))
    assert d.decide(drone(target=1.5), NOW + 0.5).act == "hold"


def test_conflict_is_deterministic_regardless_of_arrival_order() -> None:
    for order in ((hands(43, 60), word("up")), (word("up"), hands(43, 60))):
        d = Decider()
        for decision in order:
            d.observe(decision)
        assert d.decide(drone(), NOW).act == "land"


def test_stale_hands_cannot_land() -> None:
    d = Decider()
    d.observe(hands(50, 60, t=NOW - FRESH_S - 0.5))
    action = d.decide(drone(), NOW)
    assert (action.act, action.because) == ("hold", "no fresh instruction")


def test_land_does_nothing_on_the_ground() -> None:
    d = Decider()
    d.observe(hands(50, 60))
    assert d.decide(drone("parked", 0.0), NOW).act == "hold"


def test_older_message_never_replaces_newer() -> None:
    d = Decider()
    d.observe(hands(50, 60, t=NOW))
    d.observe(hands(1, 60, t=NOW - 1, seq=2))
    assert d.decide(drone(), NOW).act == "land"


def test_parse_round_trip_and_rejects() -> None:
    for decision in (clap(), word("down"), hands(37, 60)):
        assert parse_decision(decision.to_json()).label() == decision.label()
    with pytest.raises(MessageError):
        parse_decision({"sense": "smell"})
    with pytest.raises(MessageError):
        parse_decision({"sense": "word", "value": "left", "confidence": 0.9})
    with pytest.raises(MessageError):
        parse_decision({"sense": "hands", "up": 3})
