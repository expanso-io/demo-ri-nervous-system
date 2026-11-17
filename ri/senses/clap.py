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
from typing import Any

import numpy as np
import torch
from ai_edge_litert.interpreter import Interpreter
from torch import nn

from ri.types import Array

SAMPLE_RATE = 16000
YAMNET_SAMPLES = 15600
MODELS = Path(__file__).resolve().parents[2] / "models"
HEAD_PATH = MODELS / "clap_head.pt"
CLAP_CLASSES = ("Hands", "Clapping", "Applause")
YAMNET_CLASS_FEATURES = (
    "Hands",
    "Finger snapping",
    "Clapping",
    "Applause",
    "Slap, smack",
    "Crack",
    "Whip",
    "Knock",
    "Tap",
    "Cap gun",
    "Burst, pop",
    "Whack, thwack",
    "Speech",
    "Child speech, kid speaking",
    "Conversation",
    "Narration, monologue",
    "Shout",
    "Music",
    "Hum",
)
CLAP_FEATURE_NAMES: tuple[str, ...] = (
    *YAMNET_CLASS_FEATURES,
    "clap_group_max",
    "percussive_sum",
    "log_onset_ratio",
    "decay_ratio",
    "crest_factor",
    "hf_brightness",
)

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

    def triggered(self, floor: float = 0.5) -> bool:
        return self.onset and self.confidence >= floor


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


def onset_times(audio: Array, sr: int = 16000) -> list[float]:
    """When each sharp transient in `audio` peaks, in seconds from its start.

    The same test as `onset_strength`, but it keeps every hit, so a run of
    claps can be counted one by one.
    """
    n = len(audio) // FRAME
    if n < 30:
        return []
    energy = (audio[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1) + 1e-10
    hits: list[float] = []
    for i in range(22, n):
        before = float(np.median(energy[i - 22 : i - 2]))
        if energy[i] / before < ONSET_RATIO or energy[i] < energy[max(0, i - 1) : i + 2].max():
            continue
        floor = energy[i] * 0.1
        rise = next((k for k in range(0, 12) if i - k - 1 < 0 or energy[i - k - 1] < floor), 12)
        if rise <= ONSET_RISE_FRAMES:
            hits.append(i * FRAME / sr)
    return hits


def extract_clap_features(
    audio: Array,
    scores: Array,
    onset_ratio: float,
    feature_indices: list[int],
) -> Array:
    """Combine YAMNet semantic scores with transient envelope physics."""
    vals = [float(scores[i]) for i in feature_indices]
    clap_group_max = max(vals[:4])
    percussive_sum = sum(vals[:12])
    n = len(audio) // FRAME
    if n >= 30:
        energy = (audio[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1) + 1e-10
        peak_idx = int(np.argmax(energy))
        post = energy[min(n - 1, peak_idx + 12) : min(n, peak_idx + 45)]
        decay_ratio = float(np.mean(post) / energy[peak_idx]) if len(post) else 1.0
    else:
        decay_ratio = 1.0
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    crest = min(30.0, peak / (rms + 1e-6))
    hf = float(np.mean(np.abs(np.diff(audio))) / (np.mean(np.abs(audio)) + 1e-6))
    extra = [
        clap_group_max,
        percussive_sum,
        float(np.log10(max(1.0, onset_ratio) + 1.0)),
        min(2.0, decay_ratio),
        crest,
        hf,
    ]
    return np.asarray([*vals, *extra], dtype=np.float32)


def build_clap_head(dim: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(dim, 24),
        nn.SiLU(),
        nn.Dropout(0.15),
        nn.Linear(24, 1),
    )


class ClapDetector:
    def __init__(self, models: Path = MODELS, *, load_head: bool = True) -> None:
        self.interpreter = Interpreter(model_path=str(models / "yamnet.tflite"), num_threads=1)
        self.interpreter.allocate_tensors()
        self._in = self.interpreter.get_input_details()[0]["index"]
        self._out = self.interpreter.get_output_details()[0]["index"]
        names = [
            line.split(",", 2)[2].strip().strip('"')
            for line in (models / "yamnet_class_map.csv").read_text().splitlines()[1:]
        ]
        self.indices = [names.index(name) for name in CLAP_CLASSES]
        self.feature_indices = [names.index(name) for name in YAMNET_CLASS_FEATURES]
        self.head: nn.Module | None = None
        self.head_mean: torch.Tensor | None = None
        self.head_std: torch.Tensor | None = None
        head_file = models / HEAD_PATH.name
        if load_head and head_file.is_file():
            payload: dict[str, Any] = torch.load(head_file, map_location="cpu", weights_only=True)
            head = build_clap_head(len(payload["features"]))
            head.load_state_dict(payload["state_dict"])
            head.eval()
            self.head = head
            self.head_mean = payload["mean"].to(torch.float32)
            self.head_std = payload["std"].to(torch.float32)

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
        if rms < MIN_RMS or not onset:
            return ClapScore(0.0, onset, ratio, rms)
        scores = self.class_scores(audio)
        if self.head is not None and self.head_mean is not None and self.head_std is not None:
            feat = extract_clap_features(audio, scores, ratio, self.feature_indices)
            x = (torch.from_numpy(feat) - self.head_mean) / self.head_std
            with torch.no_grad():
                confidence = float(torch.sigmoid(self.head(x.unsqueeze(0)).squeeze()))
        else:
            confidence = float(max(scores[i] for i in self.indices))
        return ClapScore(confidence, onset, ratio, rms)
