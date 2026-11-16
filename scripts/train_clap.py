"""Train a calibrated single-clap + applause head over YAMNet + onset physics.

    uv run python -m scripts.train_clap --data ~/.cache/demo-ri-nervous-system/sc

Trains `models/clap_head.pt` using sliding windows from `fixtures/audio/applause.wav`,
single/double transient hand claps across room acoustics and gains, real human speech
negatives (Speech Commands v0.02 plosives and words), background noise, and drone
propeller hum. Never commits training audio; writes only tiny weights (`< 20 KB`) and
`models/clap_head.json` evaluation metrics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from torch import nn

from ri.senses.clap import (
    CLAP_FEATURE_NAMES,
    HEAD_PATH,
    MODELS,
    SAMPLE_RATE,
    ClapDetector,
    build_clap_head,
    extract_clap_features,
    onset_strength,
)
from ri.types import Array

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "fixtures" / "audio"


def synthetic_clap(rng: np.random.Generator, count: int = 1) -> Array:
    """Create a realistic broadband hand-clap transient with short room reflections."""
    wave = np.zeros(SAMPLE_RATE, dtype=np.float32)
    base_pos = int(rng.integers(2400, 12800))
    for k in range(count):
        pos = base_pos + k * int(rng.integers(2200, 4200))
        if pos + 900 >= SAMPLE_RATE:
            break
        n = int(rng.integers(280, 900))
        noise = rng.normal(0, 1, n).astype(np.float32)
        # Hand claps center energy between ~700 Hz and ~5 kHz via two-stage diff/smooth
        shaped = np.diff(noise, prepend=0.0)
        tau = float(rng.uniform(45.0, 180.0))
        env = np.exp(-np.arange(n, dtype=np.float32) / tau)
        amp = float(rng.uniform(0.08, 0.85))
        burst = shaped * env
        burst = burst / (np.max(np.abs(burst)) + 1e-6) * amp
        wave[pos : pos + n] += burst
        # Early room reflection 10-35 ms later
        ref = pos + int(rng.integers(160, 560))
        rn = min(n, SAMPLE_RATE - ref)
        if rn > 0:
            wave[ref : ref + rn] += burst[:rn] * float(rng.uniform(0.15, 0.45))
    bed = rng.normal(0, float(rng.uniform(0.0005, 0.004)), SAMPLE_RATE).astype(np.float32)
    return np.clip(wave + bed, -1.0, 1.0).astype(np.float32)


def low_thud(rng: np.random.Generator) -> Array:
    """Low-frequency desk thump / mic bump negative that passes raw RMS spikes."""
    wave = np.zeros(SAMPLE_RATE, dtype=np.float32)
    pos = int(rng.integers(3200, 12000))
    n = 900
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    freq = float(rng.uniform(55.0, 180.0))
    thud = np.sin(2 * np.pi * freq * t) * np.exp(-np.arange(n) / 220.0)
    wave[pos : pos + n] = thud.astype(np.float32) * float(rng.uniform(0.1, 0.6))
    return wave


def build_samples(sc_root: Path, seed: int) -> dict[str, list[tuple[Array, int]]]:
    rng = np.random.default_rng(seed)
    applause, _ = sf.read(AUDIO_DIR / "applause.wav", dtype="float32")
    pos_train: list[Array] = []
    pos_val: list[Array] = []

    for start in range(0, max(1, len(applause) - SAMPLE_RATE + 1), SAMPLE_RATE // 8):
        seg = np.asarray(applause[start : start + SAMPLE_RATE], dtype=np.float32)
        for gain in (0.3, 0.7, 1.0, 1.3):
            aug = np.clip(seg * gain + rng.normal(0, 0.002, SAMPLE_RATE), -1.0, 1.0).astype(
                np.float32
            )
            (pos_val if start >= len(applause) // 2 else pos_train).append(aug)

    for i in range(280):
        c = 1 if rng.random() < 0.7 else 2
        (pos_val if i >= 210 else pos_train).append(synthetic_clap(rng, count=c))

    # Real speech negatives (especially plosives and our command words)
    neg_train: list[Array] = []
    neg_val: list[Array] = []
    words = ("stop", "up", "down", "yes", "no", "go", "two", "three", "six", "seven")
    wavs: list[Path] = []
    for w in words:
        folder = sc_root / w
        if folder.is_dir():
            wavs.extend(sorted(folder.glob("*.wav"))[:180])
    random_idx = rng.permutation(len(wavs))
    cutoff = int(len(wavs) * 0.75)
    for rank, idx in enumerate(random_idx):
        clip, _ = sf.read(wavs[int(idx)], dtype="float32")
        padded = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
        padded[8000 : 8000 + min(SAMPLE_RATE, len(clip))] = clip[:SAMPLE_RATE]
        padded += rng.normal(0, 0.002, len(padded)).astype(np.float32)
        # Include centered and trailing/leading word-edge slices matching real sliding hops
        for offset in (0, 4000, 8000, 12000):
            seg = padded[offset : offset + SAMPLE_RATE]
            gain = float(rng.uniform(0.5, 1.5))
            buf = np.clip(seg * gain, -1.0, 1.0).astype(np.float32)
            (neg_train if rank < cutoff else neg_val).append(buf)

    for i in range(80):
        (neg_train if i < 60 else neg_val).append(low_thud(rng))

    # Prop hum negatives
    t = np.arange(SAMPLE_RATE, dtype=np.float32) / SAMPLE_RATE
    for i in range(40):
        f0 = float(rng.uniform(140.0, 240.0))
        hum = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3, 4))
        clip = (0.25 * hum + rng.normal(0, 0.03, SAMPLE_RATE)).astype(np.float32)
        (neg_train if i < 30 else neg_val).append(clip)

    return {
        "train": [(x, 1) for x in pos_train] + [(x, 0) for x in neg_train],
        "val": [(x, 1) for x in pos_val] + [(x, 0) for x in neg_val],
    }


def extract_dataset(
    detector: ClapDetector,
    items: list[tuple[Array, int]],
) -> tuple[torch.Tensor, torch.Tensor, list[tuple[bool, int]]]:
    feats: list[Array] = []
    labels: list[int] = []
    gated_meta: list[tuple[bool, int]] = []
    for wave, label in items:
        onset, ratio = onset_strength(wave)
        gated_meta.append((onset, label))
        scores = detector.class_scores(wave)
        feats.append(extract_clap_features(wave, scores, ratio, detector.feature_indices))
        labels.append(label)
    return torch.from_numpy(np.stack(feats)), torch.tensor(labels, dtype=torch.float32), gated_meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=HEAD_PATH)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    detector = ClapDetector(models=MODELS, load_head=False)
    splits = build_samples(args.data, args.seed)
    x_tr, y_tr, _ = extract_dataset(detector, splits["train"])
    x_va, y_va, meta_va = extract_dataset(detector, splits["val"])

    dim = x_tr.shape[1]
    mean = x_tr.mean(dim=0)
    std = x_tr.std(dim=0).clamp_min(1e-4)
    x_tr_n = (x_tr - mean) / std
    x_va_n = (x_va - mean) / std

    head = build_clap_head(dim)
    pos_weight = torch.tensor([0.5])
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.AdamW(head.parameters(), lr=5e-3, weight_decay=1e-2)
    best_score = -1.0
    best_state = None
    for _ in range(350):
        head.train()
        opt.zero_grad()
        loss = loss_fn(head(x_tr_n).squeeze(-1), y_tr)
        loss.backward()
        opt.step()

        head.eval()
        with torch.no_grad():
            probs = torch.sigmoid(head(x_va_n).squeeze(-1)).numpy()
        tp = sum(
            1 for p, (onset, y) in zip(probs, meta_va, strict=True) if y == 1 and onset and p >= 0.5
        )
        fn = sum(
            1
            for p, (onset, y) in zip(probs, meta_va, strict=True)
            if y == 1 and (not onset or p < 0.5)
        )
        fp = sum(
            1 for p, (onset, y) in zip(probs, meta_va, strict=True) if y == 0 and onset and p >= 0.5
        )
        tn = sum(
            1
            for p, (onset, y) in zip(probs, meta_va, strict=True)
            if y == 0 and (not onset or p < 0.5)
        )
        recall = tp / max(1, tp + fn)
        spec = tn / max(1, tn + fp)
        bal = 0.25 * recall + 0.75 * spec if fp > 0 else 0.75 + 0.25 * recall
        if bal >= best_score:
            best_score = bal
            best_state = {k: v.cpu().clone() for k, v in head.state_dict().items()}

    assert best_state is not None
    head.load_state_dict(best_state)
    head.eval()
    with torch.no_grad():
        probs = torch.sigmoid(head(x_va_n).squeeze(-1)).numpy()
    tp = sum(
        1 for p, (onset, y) in zip(probs, meta_va, strict=True) if y == 1 and onset and p >= 0.5
    )
    fn = sum(
        1 for p, (onset, y) in zip(probs, meta_va, strict=True) if y == 1 and (not onset or p < 0.5)
    )
    fp = sum(
        1 for p, (onset, y) in zip(probs, meta_va, strict=True) if y == 0 and onset and p >= 0.5
    )
    tn = sum(
        1 for p, (onset, y) in zip(probs, meta_va, strict=True) if y == 0 and (not onset or p < 0.5)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": best_state,
            "mean": mean,
            "std": std,
            "features": list(CLAP_FEATURE_NAMES),
        },
        args.out,
    )
    report = {
        "train_samples": len(y_tr),
        "val_samples": len(y_va),
        "val_tp": tp,
        "val_fn": fn,
        "val_fp": fp,
        "val_tn": tn,
        "val_recall": round(tp / max(1, tp + fn), 4),
        "val_specificity": round(tn / max(1, tn + fp), 4),
    }
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print("saved", args.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
