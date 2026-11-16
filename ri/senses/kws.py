"""hear-commands: a keyword spotter that knows three words.

A small CNN over 40-band log-mel frames of one second of 16 kHz audio. It
knows up, down and stop, plus "something else" and "nothing". It does not
know English. Trained by `scripts/train_kws.py` on Google Speech Commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torchaudio
from torch import nn

from ri.types import Array

SAMPLE_RATE = 16000
CLIP = SAMPLE_RATE
LABELS: tuple[str, ...] = ("up", "down", "stop", "_unknown_", "_silence_")
WORDS: frozenset[str] = frozenset({"up", "down", "stop"})
MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "kws.pt"
MIN_RMS = 0.004
MIN_MARGIN = 0.25


@dataclass(frozen=True)
class WordMatch:
    word: str | None
    heard: str
    confidence: float
    margin: float
    rms: float


class Features(nn.Module):
    """Waveform (batch, samples) to log-mel (batch, 1, 40, 101)."""

    def __init__(self) -> None:
        super().__init__()
        self.mel = torchaudio.transforms.MelSpectrogram(
            sample_rate=SAMPLE_RATE, n_fft=480, win_length=480, hop_length=160, n_mels=40
        )

    def forward(self, wave: torch.Tensor) -> torch.Tensor:
        spec = torch.log(self.mel(wave) + 1e-6)
        spec = (spec - spec.mean(dim=(-2, -1), keepdim=True)) / (
            spec.std(dim=(-2, -1), keepdim=True) + 1e-5
        )
        return spec.unsqueeze(1)


class SEBlock(nn.Module):
    def __init__(self, channels: int, reduction: int = 4) -> None:
        super().__init__()
        mid = max(4, channels // reduction)
        self.fc = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(channels, mid),
            nn.ReLU(inplace=True),
            nn.Linear(mid, channels),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        scale: torch.Tensor = self.fc(x).unsqueeze(-1).unsqueeze(-1)
        return x * scale


def block(cin: int, cout: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(cin, cout, 3, padding=1, bias=False),
        nn.BatchNorm2d(cout),
        nn.SiLU(inplace=True),
        nn.Conv2d(cout, cout, 3, padding=1, bias=False),
        nn.BatchNorm2d(cout),
        nn.SiLU(inplace=True),
        SEBlock(cout),
    )


class KeywordNet(nn.Module):
    def __init__(self, classes: int = len(LABELS)) -> None:
        super().__init__()
        self.features = Features()
        self.body = nn.Sequential(
            block(1, 24),
            nn.MaxPool2d(2),
            block(24, 48),
            nn.MaxPool2d(2),
            block(48, 96),
            nn.MaxPool2d(2),
            block(96, 128),
        )
        self.head = nn.Sequential(
            nn.Dropout(0.25),
            nn.Linear(256, classes),
        )

    def forward(self, wave: torch.Tensor) -> torch.Tensor:
        feat = self.features(wave)
        return self.forward_features(feat)

    def forward_features(self, feat: torch.Tensor) -> torch.Tensor:
        out = self.body(feat)
        avg_p = out.mean(dim=(-2, -1))
        max_p = out.amax(dim=(-2, -1))
        return self.head(torch.cat([avg_p, max_p], dim=-1))  # type: ignore[no-any-return]


def parameter_count(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


class KeywordSpotter:
    """Loads the trained weights once; scores one-second windows."""

    def __init__(self, path: Path = MODEL_PATH) -> None:
        torch.set_num_threads(1)
        payload: dict[str, Any] = torch.load(path, map_location="cpu", weights_only=True)
        self.model = KeywordNet()
        self.model.load_state_dict(payload["state_dict"])
        self.model.eval()

    def scores(self, window: Array) -> dict[str, float]:
        wave = np.zeros(CLIP, dtype=np.float32)
        clip = window[-CLIP:].astype(np.float32)
        wave[-len(clip) :] = clip
        with torch.no_grad():
            logits = self.model(torch.from_numpy(wave).unsqueeze(0))
            probs = torch.softmax(logits, dim=-1)[0].numpy()
        return {label: float(p) for label, p in zip(LABELS, probs, strict=True)}

    def best(self, window: Array) -> tuple[str, float]:
        scores = self.scores(window)
        label = max(scores, key=lambda k: scores[k])
        return label, scores[label]

    def classify(
        self,
        window: Array,
        *,
        min_rms: float = MIN_RMS,
        min_conf: float = 0.75,
        min_margin: float = MIN_MARGIN,
    ) -> WordMatch:
        rms = float(np.sqrt(np.mean(window.astype(np.float64) ** 2)))
        if rms < min_rms:
            return WordMatch(None, "_silence_", 1.0, 1.0, rms)
        scores = self.scores(window)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        label, confidence = ranked[0]
        margin = confidence - ranked[1][1]
        accepted = (
            label if (label in WORDS and confidence >= min_conf and margin >= min_margin) else None
        )
        return WordMatch(accepted, label, confidence, margin, rms)
