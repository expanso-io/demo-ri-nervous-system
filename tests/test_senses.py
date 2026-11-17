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
    assert hands_up(block) == 2


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


def _pose(left_wrist: tuple[float, float], right_wrist: tuple[float, float]) -> Any:
    """One person, shoulders at x 40 and 60, y 50; wrists where told."""
    person = np.zeros((17, 3), dtype=np.float32)
    person[5] = (40, 50, 0.9)
    person[6] = (60, 50, 0.9)
    person[9] = (*left_wrist, 0.9)
    person[10] = (*right_wrist, 0.9)
    return person[None, ...]


def test_marshal_signal_reads_the_three_arm_signals() -> None:
    from ri.senses.hands import marshal_signal

    assert marshal_signal(_pose((38, 20), (62, 20))) == "forward"
    assert marshal_signal(_pose((10, 50), (20, 45))) == "left"
    assert marshal_signal(_pose((80, 50), (90, 45))) == "right"
    assert marshal_signal(_pose((38, 80), (62, 80))) is None
    # One arm out and one arm down is not a signal.
    assert marshal_signal(_pose((10, 50), (62, 80))) is None
