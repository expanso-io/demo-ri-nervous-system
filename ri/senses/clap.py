"""hear-clap: an audio event classifier gated by an onset detector.

YAMNet (Apache 2.0, about 4 MB) scores one second of room audio against
521 sound classes; the clap score is the strongest of Hands, Clapping and
Applause. A sharp-onset gate runs first: a clap rises to its peak in a few
milliseconds, speech and a hovering drone do not, so loudness alone never
triggers it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ai_edge_litert.interpreter import Interpreter

from ri.types import Array

SAMPLE_RATE = 16000
YAMNET_SAMPLES = 15600
MODELS = Path(__file__).resolve().parents[2] / "models"
CLAP_CLASSES = ("Hands", "Clapping", "Applause")

FRAME = 80  # 5 ms energy frames
ONSET_RATIO = 8.0  # peak energy over the preceding 100 ms
ONSET_RISE_FRAMES = 4  # 20 ms from a tenth of the peak to the peak
MIN_RMS = 0.003


@dataclass(frozen=True)
class ClapScore:
    confidence: float
    onset: bool
    onset_ratio: float
    rms: float


def onset_strength(audio: Array) -> tuple[bool, float]:
    """Sharp transient present? Returns (gate, strongest ratio)."""
    n = len(audio) // FRAME
    if n < 30:
        return False, 0.0
    energy = (audio[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1) + 1e-10
    best = 0.0
    gate = False
    for i in range(22, n):
        before = float(np.median(energy[i - 22 : i - 2]))
        ratio = float(energy[i] / before)
        if ratio > best:
            best = ratio
        if ratio >= ONSET_RATIO and energy[i] >= energy[max(0, i - 1) : i + 2].max():
            floor = energy[i] * 0.1
            rise = next((k for k in range(0, 12) if i - k - 1 < 0 or energy[i - k - 1] < floor), 12)
            if rise <= ONSET_RISE_FRAMES:
                gate = True
    return gate, best


class ClapDetector:
    def __init__(self, models: Path = MODELS) -> None:
        self.interpreter = Interpreter(model_path=str(models / "yamnet.tflite"), num_threads=1)
        self.interpreter.allocate_tensors()
        self._in = self.interpreter.get_input_details()[0]["index"]
        self._out = self.interpreter.get_output_details()[0]["index"]
        names = [
            line.split(",", 2)[2].strip().strip('"')
            for line in (models / "yamnet_class_map.csv").read_text().splitlines()[1:]
        ]
        self.indices = [names.index(name) for name in CLAP_CLASSES]

    def class_scores(self, audio: Array) -> Array:
        window = np.zeros(YAMNET_SAMPLES, dtype=np.float32)
        clip = audio[-YAMNET_SAMPLES:].astype(np.float32)
        window[-len(clip) :] = clip
        self.interpreter.set_tensor(self._in, window)
        self.interpreter.invoke()
        return np.asarray(self.interpreter.get_tensor(self._out)[0])

    def score(self, audio: Array) -> ClapScore:
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        onset, ratio = onset_strength(audio)
        if rms < MIN_RMS:
            return ClapScore(0.0, onset, ratio, rms)
        scores = self.class_scores(audio)
        confidence = float(max(scores[i] for i in self.indices))
        return ClapScore(confidence, onset, ratio, rms)
