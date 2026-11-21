from __future__ import annotations

import pytest

from ri import room
from ri.decide import FLOOR_M, FRESH_S, Decider
from ri.drone import MAX_ALTITUDE_M, STEP_M, WAKE_ALTITUDE_M, DroneState, Phase
from ri.messages import Decision, MessageError, parse_decision

NOW = 1000.0


def drone(phase: Phase = "hovering", target: float = 1.0) -> DroneState:
    return DroneState("sim", phase, altitude_m=target, target_m=target)


def clap(t: float = NOW, conf: float = 0.93, seq: int = 1) -> Decision:
    return Decision("clap", t, confidence=conf, seq=seq)


def word(value: str, t: float = NOW, conf: float = 0.81, seq: int = 1) -> Decision:
    return Decision("word", t, confidence=conf, value=value, seq=seq)


def applause(t: float = NOW, share: float = 0.85, seq: int = 1) -> Decision:
    return Decision("clap", t, confidence=share, value="applause", seq=seq)


def hands(up: int, seen: int, t: float = NOW, seq: int = 1, gesture: str | None = None) -> Decision:
    return Decision("hands", t, up=up, seen=seen, share=up / seen, gesture=gesture, seq=seq)


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
    action = d.decide(drone(target=MAX_ALTITUDE_M), NOW)
    assert (action.act, action.because) == ("hold", "ceiling reached")
    d.observe(word("down", seq=2))
    assert d.decide(drone(target=FLOOR_M), NOW).because == "lowest safe height"


def test_a_word_never_wakes_a_sleeping_drone() -> None:
    d = Decider()
    d.observe(word("up"))
    action = d.decide(drone("dark", 0.0), NOW)
    assert (action.act, action.because) == ("hold", "asleep: clap to wake")


def test_the_shows_ups_take_the_drone_from_the_wake_height_to_the_top() -> None:
    d = Decider()
    height = WAKE_ALTITUDE_M
    ups = room.current().ups
    for i in range(ups):
        d.observe(word("up", t=NOW + i, seq=i + 1))
        assert d.decide(drone(target=height), NOW + i).act == "up"
        height += STEP_M
    assert height == pytest.approx(room.current().top_m)
    # One more is past the ceiling.
    d.observe(word("up", t=NOW + ups, seq=ups + 1))
    assert d.decide(drone(target=height), NOW + ups).because == "ceiling reached"


def test_jazz_hands_land_the_drone_on_their_own() -> None:
    d = Decider()
    d.observe(hands(2, 1, gesture="jazz"))
    d.observe(word("up", conf=0.9))
    action = d.decide(drone(), NOW, need="left")
    assert (action.act, action.rule) == ("land", "land")
    assert action.because == "jazz hands > word up"
    assert action.inputs == ("jazz hands", "up 0.90")


def test_jazz_hands_do_not_wake_a_sleeping_drone() -> None:
    d = Decider()
    d.observe(hands(2, 1, gesture="jazz"))
    assert d.decide(drone("dark", 0.0), NOW).act == "hold"


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


def test_the_rooms_applause_lands_the_drone() -> None:
    d = Decider()
    d.observe(applause())
    action = d.decide(drone(), NOW)
    assert (action.act, action.because, action.rule) == ("land", "applause", "land")
    assert action.inputs == ("applause 0.85",)


def test_hands_however_many_never_land_the_drone() -> None:
    d = Decider()
    d.observe(hands(40, 30))
    assert d.decide(drone(), NOW).act == "hold"


def test_applause_never_wakes_a_parked_drone_and_is_spent() -> None:
    d = Decider()
    d.observe(applause())
    assert d.decide(drone("parked", 0.0), NOW).act == "hold"
    assert d.decide(drone("parked", 0.0), NOW + 0.1).act == "hold"
    d.observe(clap(t=NOW + 0.2, seq=2))
    assert d.decide(drone("parked", 0.0), NOW + 0.2).act == "wake"


def test_a_marshal_waving_is_not_a_vote_to_land() -> None:
    d = Decider()
    d.observe(hands(2, 1, gesture="left"))
    action = d.decide(drone(), NOW, need="end")
    assert (action.act, action.waiting) == ("hold", False)


def test_conflict_landing_wins_and_the_word_loses() -> None:
    d = Decider()
    d.observe(applause())
    d.observe(word("up", conf=0.77))
    action = d.decide(drone(target=1.5), NOW)
    assert action.act == "land"
    assert action.because == "applause > word up"
    assert action.inputs == ("applause 0.85", "up 0.77")
    # the shout was heard and lost: it is not replayed once the applause stops
    assert d.decide(drone(target=1.5), NOW + 0.5).act == "hold"


def test_conflict_is_deterministic_regardless_of_arrival_order() -> None:
    for order in ((applause(), word("up")), (word("up"), applause())):
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
    d.observe(hands(2, 1, t=NOW, gesture="jazz"))
    d.observe(hands(1, 60, t=NOW - 1, seq=2))
    assert d.decide(drone(), NOW).act == "land"


def test_parse_round_trip_and_rejects() -> None:
    for decision in (clap(), applause(), word("down"), hands(37, 60)):
        assert parse_decision(decision.to_json()).label() == decision.label()
    with pytest.raises(MessageError):
        parse_decision({"sense": "smell"})
    with pytest.raises(MessageError):
        parse_decision({"sense": "word", "value": "left", "confidence": 0.9})
    with pytest.raises(MessageError):
        parse_decision({"sense": "hands", "up": 3})


def test_a_shout_during_the_descent_still_loses() -> None:
    d = Decider()
    d.observe(applause())
    assert d.decide(drone(target=1.5), NOW).act == "land"
    d.observe(word("up", t=NOW + 0.4))
    action = d.decide(drone("landing", 0.9), NOW + 0.4)
    assert (action.act, action.because) == ("hold", "landing: word up ignored")
    # Jazz hands keep holding the land through the descent and name the shout.
    d.observe(hands(2, 1, t=NOW + 0.5, gesture="jazz", seq=2))
    d.observe(word("up", t=NOW + 0.5, seq=3))
    action = d.decide(drone("landing", 0.8), NOW + 0.5)
    assert (action.act, action.because) == ("land", "jazz hands > word up")


def test_the_land_names_a_word_acted_on_a_moment_before() -> None:
    d = Decider()
    d.observe(word("up"))
    assert d.decide(drone(), NOW).act == "up"
    d.observe(applause(t=NOW + 0.6))
    action = d.decide(drone("climbing", 1.2), NOW + 0.6)
    assert action.because == "applause > word up"


def test_a_word_while_landing_without_a_majority_is_ignored() -> None:
    d = Decider()
    d.observe(word("up"))
    action = d.decide(drone("landing", 0.5), NOW)
    assert (action.act, action.because) == ("hold", "landing: word up ignored")


def test_one_stray_hand_is_not_a_vote_in_progress() -> None:
    d = Decider()
    d.observe(hands(1, 18))
    action = d.decide(drone(), NOW)
    assert (action.act, action.because, action.waiting) == ("hold", "no instruction", False)


@pytest.mark.parametrize("phase", ["taking_off", "cruising"])
def test_move_holds_until_hovering(phase: Phase) -> None:
    d = Decider()
    d.observe(word("up"))
    action = d.decide(drone(phase, 1.2), NOW)
    assert (action.act, action.because) == ("hold", f"{phase}: wait for hover")


@pytest.mark.parametrize("phase", ["taking_off", "climbing", "descending"])
def test_stop_and_land_still_preempt_non_hovering_flight(phase: Phase) -> None:
    d = Decider()
    d.observe(word("stop", seq=1))
    assert d.decide(drone(phase, 1.2), NOW).act == "stop"

    d.observe(applause(t=NOW + 0.2, seq=2))
    assert d.decide(drone(phase, 1.2), NOW + 0.2).act == "land"


def test_reset_and_suppress_pending() -> None:
    d = Decider()
    d.observe(clap(seq=1))
    d.observe(word("up", seq=2))
    d.suppress_pending()
    assert d.decide(drone("dark", 0.0), NOW).act == "hold"

    d.observe(clap(t=NOW + 0.1, seq=3))
    d.reset()
    assert d.decide(drone("dark", 0.0), NOW + 0.1).act == "hold"


def test_the_right_arm_signal_releases_one_step_of_the_route() -> None:
    d = Decider()
    d.observe(hands(1, 1, gesture="left"))
    action = d.decide(drone(), NOW, need="left")
    assert (action.act, action.because, action.rule) == ("go", "wave left", "route")
    # The same signal message is one step, not a stream of them.
    assert d.decide(drone(), NOW, need="left").act == "hold"


def test_the_wrong_arm_signal_holds_and_says_what_the_route_wants() -> None:
    d = Decider()
    d.observe(hands(1, 1, gesture="right"))
    action = d.decide(drone(), NOW, need="forward")
    assert (action.act, action.because) == ("hold", "wants a wave forward")
    assert action.waiting


def test_no_arm_signal_never_moves_the_drone() -> None:
    d = Decider()
    d.observe(hands(0, 1))
    assert d.decide(drone(), NOW, need="forward").act == "hold"


@pytest.mark.parametrize("phase", ["taking_off", "climbing", "cruising", "dark"])
def test_a_signal_waits_for_a_steady_hover(phase: Phase) -> None:
    d = Decider()
    d.observe(hands(1, 1, gesture="forward"))
    assert d.decide(drone(phase), NOW, need="forward").act == "hold"


def test_once_the_route_is_flown_no_wave_moves_the_drone() -> None:
    d = Decider()
    for seq, shown in enumerate(("left", "right", "forward"), start=1):
        d.observe(hands(1, 1, t=NOW + seq, seq=seq, gesture=shown))
        assert d.decide(drone(), NOW + seq, need="end").act == "hold"


def test_the_end_of_the_route_waits_for_the_room() -> None:
    d = Decider()
    assert d.decide(drone(), NOW, need="end").act == "hold"
    d.observe(applause())
    assert d.decide(drone(), NOW, need="end").act == "land"


def test_stop_beats_the_route() -> None:
    d = Decider()
    d.observe(hands(1, 1, gesture="forward"))
    d.observe(word("stop"))
    assert d.decide(drone(), NOW, need="forward").act == "stop"


def test_a_clap_heard_during_a_landing_does_not_wake_the_drone_when_it_lands() -> None:
    d = Decider()
    d.observe(clap(t=NOW, conf=0.99, seq=5))
    assert d.decide(drone("landing", 0.0), NOW).act == "hold"
    assert d.decide(drone("parked", 0.0), NOW + 1.0).act == "hold"


@pytest.mark.parametrize("phase", ["climbing", "descending"])
def test_an_up_heard_mid_climb_is_a_step_not_a_refusal(phase: Phase) -> None:
    d = Decider()
    d.observe(word("up"))
    action = d.decide(drone(phase, 0.6), NOW)
    assert (action.act, action.because) == ("up", "word up")


def test_applause_waits_for_the_route_and_is_spent_before_it() -> None:
    d = Decider()
    # The wake claps spilling into take-off, and a cheer mid-route: spent.
    d.observe(applause())
    action = d.decide(drone("taking_off", 0.5), NOW, need="left")
    assert (action.act, action.because) == ("hold", "applause: wants the left leg first")
    assert d.decide(drone(), NOW + 0.1, need="end").act == "hold"
    # Once the route is flown, the room's applause lands it.
    d.observe(applause(t=NOW + 1, seq=2))
    assert d.decide(drone(), NOW + 1, need="end").act == "land"


def test_jazz_hands_land_it_mid_route() -> None:
    d = Decider()
    d.observe(hands(2, 1, gesture="jazz"))
    assert d.decide(drone(), NOW, need="left").act == "land"
