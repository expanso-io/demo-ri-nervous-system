"""hear-commands: a keyword spotter that knows three words.

A small CNN over 40-band log-mel frames of one second of 16 kHz audio. It
knows up, down and stop, plus "something else" and "nothing". It does not
know English. Trained by `scripts/train_kws.py` on Google Speech Commands.
"""

from __future__ import annotations

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


def block(cin: int, cout: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(cin, cout, 3, padding=1, bias=False),
        nn.BatchNorm2d(cout),
        nn.ReLU(inplace=True),
    )


class KeywordNet(nn.Module):
    def __init__(self, classes: int = len(LABELS)) -> None:
        super().__init__()
        self.features = Features()
        self.body = nn.Sequential(
            block(1, 16),
            nn.MaxPool2d(2),
            block(16, 32),
            nn.MaxPool2d(2),
            block(32, 64),
            nn.MaxPool2d(2),
            block(64, 64),
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Dropout(0.2),
        )
        self.head = nn.Linear(64, classes)

    def forward(self, wave: torch.Tensor) -> torch.Tensor:
        return self.head(self.body(self.features(wave)))  # type: ignore[no-any-return]


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
