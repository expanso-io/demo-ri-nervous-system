from __future__ import annotations

from collections import deque
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from ri.senses.clap import ClapDetector, ClapScore
from ri.senses.hands import (
    FrameCount,
    HandCounter,
    annotate_hands,
    filter_people,
    hand_boxes_from_pose,
    hands_up,
    parse_roi,
)
from ri.senses.kws import KeywordSpotter
from ri.types import Array

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
SR = 16000


@cache
def clap_detector() -> ClapDetector:
    return ClapDetector()


@cache
def spotter() -> KeywordSpotter:
    return KeywordSpotter()


def load(name: str) -> Array:
    audio, rate = sf.read(AUDIO / name, dtype="float32")
    assert rate == SR
    return np.asarray(audio)


def in_room(clip: Array, seed: int = 0) -> Array:
    """The clip inside quiet room tone, padded either side."""
    rng = np.random.default_rng(seed)
    bed = rng.normal(0, 0.002, len(clip) + 2 * SR).astype(np.float32)
    bed[SR : SR + len(clip)] += clip
    return bed


def clap_triggers(audio: Array) -> int:
    hits = 0
    for start in range(0, len(audio) - SR, SR // 4):
        score = clap_detector().score(audio[start : start + SR])
        if score.onset and score.confidence >= 0.5:
            hits += 1
    return hits


@pytest.mark.parametrize("name", ["applause.wav"])
def test_clap_fixtures_trigger(name: str) -> None:
    assert clap_triggers(in_room(load(name))) >= 1


@pytest.mark.parametrize("word", ["up", "down", "stop"])
def test_speech_does_not_trigger_the_clap_detector(word: str) -> None:
    for i in (1, 2, 3):
        assert clap_triggers(in_room(load(f"word-{word}-{i}.wav"))) == 0


def test_drone_prop_hum_does_not_trigger() -> None:
    t = np.arange(4 * SR) / SR
    hum = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((180, 360, 540, 720), 1))
    noise = np.random.default_rng(1).normal(0, 0.05, len(t))
    audio = (0.3 * hum + noise).astype(np.float32)
    assert clap_triggers(audio) == 0


@pytest.mark.parametrize("word", ["up", "down", "stop"])
def test_keyword_spotter_reads_each_fixture(word: str) -> None:
    for i in (1, 2, 3):
        label, confidence = spotter().best(load(f"word-{word}-{i}.wav"))
        assert label == word
        assert confidence > 0.8


def test_keyword_spotter_hears_nothing_in_room_tone() -> None:
    quiet = np.random.default_rng(2).normal(0, 0.002, SR).astype(np.float32)
    label, _ = spotter().best(quiet)
    assert label == "_silence_"


def person(shoulder_y: float, wrist_y: float) -> Array:
    kp = np.zeros((17, 3), dtype=np.float32)
    for i in (5, 6):
        kp[i] = (100.0, shoulder_y, 0.9)
    for i in (9, 10):
        kp[i] = (100.0, wrist_y, 0.9)
    return kp


def test_a_hand_is_up_when_a_wrist_is_above_the_shoulder_line() -> None:
    block = np.stack([person(200, 120), person(200, 300), person(200, 199)])
    # Each raised wrist is a hand: two people with both hands up are four.
    assert hands_up(block) == 4


def test_unseen_wrists_do_not_vote() -> None:
    kp = person(200, 100)
    kp[9, 2] = kp[10, 2] = 0.1
    assert hands_up(kp[None]) == 0


def test_window_smooths_a_one_frame_flicker() -> None:
    counter = HandCounter.__new__(HandCounter)
    counter.window = deque()
    reads = [(7, 19), (7, 19), (12, 19), (7, 19), (7, 19)]
    smoothed = [counter.add(FrameCount(up, seen, i * 0.2)) for i, (up, seen) in enumerate(reads)]
    assert [s.up for s in smoothed] == [7, 7, 7, 7, 7]
    assert [s.raw_up for s in smoothed] == [7, 7, 12, 7, 7]
    assert [s.raw_seen for s in smoothed] == [19, 19, 19, 19, 19]


def test_clap_score_skips_tflite_when_onset_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    det = clap_detector()
    called = False

    def fail_invoke(_audio: Array) -> Array:
        nonlocal called
        called = True
        return np.zeros(521, dtype=np.float32)

    monkeypatch.setattr(det, "class_scores", fail_invoke)
    t = np.arange(SR, dtype=np.float32) / SR
    steady_tone = (0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    res = det.score(steady_tone)
    assert not called
    assert not res.triggered()
    assert ClapScore(0.8, True, 10.0, 0.05).triggered()
    assert not ClapScore(0.4, True, 10.0, 0.05).triggered()


def test_keyword_spotter_classify_gates_rms_and_margin() -> None:
    kw = spotter()
    for word in ("up", "down", "stop"):
        match = kw.classify(load(f"word-{word}-1.wav"))
        assert match.word == word
        assert match.confidence >= 0.75
        assert match.margin >= 0.25

    quiet = np.random.default_rng(3).normal(0, 0.002, SR).astype(np.float32)
    silent_match = kw.classify(quiet)
    assert silent_match.word is None
    assert silent_match.heard == "_silence_"

    ambiguous = kw.classify(load("word-up-1.wav"), min_margin=0.9999)
    assert ambiguous.word is None


def test_roi_filtering_and_shoulder_span_margin() -> None:
    assert parse_roi(None) is None
    assert parse_roi("full") is None
    assert parse_roi("0.1,0.2,0.9,0.8") == (0.1, 0.2, 0.9, 0.8)
    with pytest.raises(ValueError):
        parse_roi("0.5,0.5,0.2,0.8")

    # Person with shoulders spread 100px wide at y=200 -> 10% margin sets threshold y=190.
    kp = np.zeros((17, 3), dtype=np.float32)
    kp[5] = (50.0, 200.0, 0.9)
    kp[6] = (150.0, 200.0, 0.9)
    kp[9] = (50.0, 194.0, 0.9)  # barely above 200, but below 190
    kp[10] = (150.0, 220.0, 0.9)
    assert hands_up(kp[None], margin_ratio=0.0) == 1
    assert hands_up(kp[None], margin_ratio=0.1) == 0

    outside = person(100, 50)
    outside[5] = (10.0, 100.0, 0.9)
    outside[6] = (30.0, 100.0, 0.9)  # center x = 20
    inside = person(100, 50)
    inside[5] = (450.0, 100.0, 0.9)
    inside[6] = (550.0, 100.0, 0.9)  # center x = 500
    filtered = filter_people(
        np.stack([outside, inside]),
        roi=(0.2, 0.0, 0.9, 1.0),
        shape=(1000, 1000),
    )
    assert len(filtered) == 1


def test_hand_boxes_from_pose_and_annotate_hands() -> None:
    kp = np.zeros((1, 17, 3), dtype=np.float32)
    kp[0, 5] = (300.0, 300.0, 0.95)
    kp[0, 6] = (420.0, 300.0, 0.95)
    kp[0, 7] = (280.0, 220.0, 0.90)
    kp[0, 9] = (270.0, 140.0, 0.92)  # left wrist raised above shoulder line
    kp[0, 8] = (440.0, 360.0, 0.90)
    kp[0, 10] = (450.0, 420.0, 0.92)  # right wrist lowered

    boxes = hand_boxes_from_pose(kp, shape=(720, 1280), margin_ratio=0.05)
    assert len(boxes) == 2
    assert boxes[0].up is True
    assert boxes[1].up is False
    for box in boxes:
        assert 0 <= box.x1 < box.x2 < 1280
        assert 0 <= box.y1 < box.y2 < 720

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    drawn = annotate_hands(frame, boxes)
    assert frame.sum() == 0
    assert drawn.shape == frame.shape
    assert drawn.sum() > 0


def test_close_up_arm_boxes_work_without_confident_shoulders() -> None:
    kp = np.zeros((1, 17, 3), dtype=np.float32)
    kp[0, 6] = (250.0, 14.0, 0.19)  # shoulder clipped at top / low conf
    kp[0, 8] = (195.0, 186.0, 0.47)  # elbow
    kp[0, 10] = (305.0, 267.0, 0.29)  # wrist just below KEYPOINT_MIN

    assert hand_boxes_from_pose(kp, shape=(380, 420)) == []
    boxes = hand_boxes_from_pose(kp, shape=(380, 420), allow_close_arm=True)
    assert len(boxes) == 1
    assert boxes[0].up is False


def _arms(frames: int, left: Any, right: Any) -> list[tuple[float, float, float, float]]:
    """Wrist tracks in shoulder widths; each argument maps a frame number to (across, down)."""
    return [(*left(n), *right(n)) for n in range(frames)]


def _beckon(across: float) -> Any:
    return lambda n: (across, -1.0 + (0.5 if n % 2 else -0.5))


def _rise(across: float) -> Any:
    return lambda n: (across, 1.0 - 0.3 * n)


def _still(across: float, down: float) -> Any:
    return lambda _n: (across, down)


def test_marshal_signal_needs_a_wave_not_a_pose() -> None:
    from ri.senses.hands import marshal_signal

    assert marshal_signal(_arms(8, _beckon(-0.6), _beckon(0.6))) == "forward"
    # Both arms out to one side, as rehearsal frames read a natural point.
    assert marshal_signal(_arms(8, _still(-2.2, 0.3), _still(-1.9, 0.1))) == "left"
    assert marshal_signal(_arms(8, _still(2.2, 0.3), _still(2.1, 0.1))) == "right"
    # One arm out and the other hanging, or arms crossed to both sides, is not a point.
    assert marshal_signal(_arms(8, _still(-2.0, 0.2), _still(-0.6, 1.6))) is None
    assert marshal_signal(_arms(8, _still(-2.0, 0.2), _still(0.9, 0.2))) is None
    # Arms held up without moving are not a signal, and neither is pointing.
    assert marshal_signal(_arms(8, _still(-0.6, -1.0), _still(0.6, -1.0))) is None
    assert marshal_signal(_arms(8, _still(-1.6, 1.5), _still(-1.4, 1.6))) is None
    # Raising both arms once travels a long way but never comes back.
    assert marshal_signal(_arms(8, _rise(-0.6), _rise(0.6))) is None
    assert marshal_signal(_arms(2, _beckon(-0.6), _beckon(0.6))) is None


def _shake(across: float, down: float) -> Any:
    """A wrist shaking fast in a small box: jazz hands as the pose model sees them."""
    return lambda n: (across + (0.08 if n % 2 else -0.08), down + (0.06 if n % 3 else -0.06))


def test_jazz_hands_are_both_hands_up_and_shaking() -> None:
    from ri.senses.hands import arm_readout, marshal_signal

    assert marshal_signal(_arms(10, _shake(-0.8, -0.9), _shake(0.8, -0.9))) == "jazz"
    # Held still, both up, they are not jazz hands: nothing lands the drone.
    assert marshal_signal(_arms(10, _still(-0.8, -0.9), _still(0.8, -0.9))) is None
    # Shaking at the waist, or both on one side, is not jazz hands either.
    assert marshal_signal(_arms(10, _shake(-0.8, 0.8), _shake(0.8, 0.8))) is None
    assert marshal_signal(_arms(10, _shake(0.3, -0.9), _shake(0.9, -0.9))) is None
    # A beckon travels as far, over a box too big for a shake.
    assert marshal_signal(_arms(10, _beckon(-0.6), _beckon(0.6))) == "forward"
    readout = arm_readout(_arms(10, _shake(-0.8, -0.9), _shake(0.8, -0.9)))
    assert readout is not None and readout["signal"] == "jazz"
    assert readout["a"] == {
        "across": pytest.approx(-0.8),
        "down": pytest.approx(-0.9, abs=0.05),
        "travel": pytest.approx(1.6, abs=0.3),
        "extent": 0.16,
    }
    assert arm_readout(_arms(2, _still(0, 0), _still(0, 0))) is None


def test_arm_pose_measures_the_nearest_person_in_shoulder_widths() -> None:
    from ri.senses.hands import arm_pose

    person = np.zeros((17, 3), dtype=np.float32)
    person[5], person[6] = (40, 50, 0.9), (60, 50, 0.9)
    person[9], person[10] = (20, 30, 0.9), (70, 50, 0.9)
    assert arm_pose(person[None, ...]) == pytest.approx((-1.5, -1.0, 1.0, 0.0))
    assert arm_pose(np.zeros((1, 17, 3), dtype=np.float32)) is None


def test_a_forced_decision_is_what_the_model_would_have_sent() -> None:
    from ri.device import forced_decision
    from ri.messages import parse_decision

    assert parse_decision(forced_decision("word", "up", 7).to_json()).value == "up"
    assert parse_decision(forced_decision("hands", "left", 8).to_json()).gesture == "left"
    assert parse_decision(forced_decision("clap", "clap", 9).to_json()).sense == "clap"
    with pytest.raises(ValueError, match="cannot force"):
        forced_decision("word", "left", 1)


def _clapping(at_s: list[float], seconds: float) -> Array:
    rng = np.random.default_rng(3)
    audio = rng.normal(0, 0.002, int(seconds * SR)).astype(np.float32)
    burst = (rng.normal(0, 0.4, 400) * np.exp(-np.arange(400) / 80)).astype(np.float32)
    for t in at_s:
        audio[int(t * SR) : int(t * SR) + 400] += burst
    return audio


def _most_claps_counted(audio: Array) -> int:
    from ri.device import ClapDevice

    device = ClapDevice.__new__(ClapDevice)
    device.claps = deque()
    most = 0
    # The pipeline hands over one-second windows, a quarter second apart.
    for end in range(SR, len(audio) + 1, SR // 4):
        most = max(most, device.count_claps(audio[end - SR : end], 100.0 + end / SR))
    return most


def test_five_claps_in_a_row_are_counted_as_five() -> None:
    assert _most_claps_counted(_clapping([1.0, 1.4, 1.8, 2.2, 2.6], 4.0)) == 5


def test_one_bang_is_one_clap_however_many_windows_hear_it() -> None:
    assert _most_claps_counted(_clapping([1.5], 4.0)) == 1


def test_claps_too_far_apart_never_add_up_to_five() -> None:
    assert _most_claps_counted(_clapping([1.0, 2.6, 4.2, 5.8, 7.4], 9.0)) < 5


def _live_clap_device() -> Any:
    from ri.device import ClapDevice

    device = ClapDevice.__new__(ClapDevice)
    device.claps = deque()
    device.clap_windows = deque()
    device.near_miss_at = 0.0
    return device


def _wakes(audio: Array, heard_as_clapping: int) -> bool:
    """Feed the live counter as the pipeline does; the classifier agrees N times."""
    device = _live_clap_device()
    woke = False
    for n, end in enumerate(range(SR, len(audio) + 1, SR // 4)):
        clapping = n >= 4 and n - 4 < heard_as_clapping
        woke = woke or device.enough_claps(audio[end - SR : end], 100.0 + end / SR, clapping)
    return woke


def test_a_live_wake_needs_a_steady_beat_and_the_classifier_more_than_once() -> None:
    steady = _clapping([1.0, 1.4, 1.8, 2.2, 2.6], 4.0)
    assert _wakes(steady, heard_as_clapping=6)
    # One window mistaken for a clap is not enough, however many bangs there were.
    assert not _wakes(steady, heard_as_clapping=1)
    # Five bangs with no beat: a door, a cough, a chair, a cup, a step.
    assert not _wakes(_clapping([1.0, 1.2, 1.95, 2.15, 2.9], 4.0), heard_as_clapping=6)
    assert not _wakes(_clapping([1.0, 1.4, 1.8, 2.2], 4.0), heard_as_clapping=6)


def test_a_sharp_sound_that_carries_on_is_speech_not_a_clap() -> None:
    from ri.senses.clap import onset_times

    audio = _clapping([1.0], 2.0)
    assert len(onset_times(audio, SR)) == 1
    # The same burst with a voiced sound straight after it, as in "pah".
    tone = (0.2 * np.sin(2 * np.pi * 180 * np.arange(int(0.25 * SR)) / SR)).astype(np.float32)
    audio[int(1.02 * SR) : int(1.02 * SR) + len(tone)] += tone
    assert onset_times(audio, SR) == []


def _person(cx: float, cy: float, span: float, arm: float) -> Any:
    """Shoulders `span` apart around (cx, cy), elbows `arm` below, wrists below those."""
    p = np.zeros((17, 3), dtype=np.float32)
    p[5], p[6] = (cx - span / 2, cy, 0.9), (cx + span / 2, cy, 0.9)
    p[7], p[8] = (cx - span / 2, cy + arm, 0.9), (cx + span / 2, cy + arm, 0.9)
    p[9], p[10] = (cx - span / 2, cy + 2 * arm, 0.9), (cx + span / 2, cy + 2 * arm, 0.9)
    return p


def test_the_marshal_is_never_someone_cut_off_by_the_frame_or_side_on() -> None:
    from ri.senses.hands import arm_pose, marshal

    shape = (720, 1280)
    presenter = _person(640, 300, 100, 80)  # facing the camera, all in frame
    passer_by = _person(1230, 250, 240, 160)  # close, wide, half out of frame
    side_on = _person(400, 300, 20, 80)  # shoulders overlap: turned away
    crowd = np.stack([passer_by, side_on, presenter])
    chosen = marshal(crowd, shape)
    assert chosen is not None and float(chosen[5][0]) == 590.0
    # Without the frame size only the side-on rule applies, and the widest wins.
    chosen = marshal(crowd)
    assert chosen is not None and float(chosen[5][0]) == 1110.0
    assert marshal(np.stack([passer_by, side_on]), shape) is None
    assert arm_pose(np.stack([presenter]), shape) == pytest.approx((-0.5, 1.6, 0.5, 1.6))


def test_applause_needs_two_seconds_of_loud_clapping_and_waits_before_again() -> None:
    from collections import deque
    from types import SimpleNamespace

    from ri.device import ClapDevice

    dev = ClapDevice.__new__(ClapDevice)
    dev.applause = deque()
    dev.applause_meter = {"share": 0.0, "level": 0.0}
    dev.last_emit = {}
    loud = SimpleNamespace(confidence=1.0, typing=0.0, rms=0.1)  # level 0.67
    quiet = SimpleNamespace(confidence=1.0, typing=0.0, rms=0.003)  # level 0.16
    drone = SimpleNamespace(confidence=0.1, typing=0.0, rms=0.1)

    def run(score: SimpleNamespace, start: float, seconds: float) -> list[float]:
        fired = []
        for i in range(round(seconds / 0.25)):
            now = start + i * 0.25
            if dev.heard_applause(score, now):
                fired.append(now)
        return fired

    # A loud drone that does not sound like clapping never lands it.
    assert run(drone, 0.0, 5.0) == []
    # One person's quiet clapping is not the room.
    assert run(quiet, 10.0, 5.0) == []
    # The room: fires once the window has filled, then not again for 3 s.
    fired = run(loud, 20.0, 5.0)
    assert len(fired) == 2
    assert 21.0 <= fired[0] <= 22.0
    assert fired[1] - fired[0] > ClapDevice.APPLAUSE_AGAIN_S
