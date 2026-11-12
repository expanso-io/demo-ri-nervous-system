from __future__ import annotations

from collections import deque
from functools import cache
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ri.senses.clap import ClapDetector
from ri.senses.hands import FrameCount, HandCounter, hands_up
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
