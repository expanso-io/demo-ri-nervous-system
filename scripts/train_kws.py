"""Train the hear-commands keyword spotter on Google Speech Commands v0.02.

    uv run python scripts/train_kws.py --data ~/.cache/demo-ri-nervous-system/sc

The dataset (CC BY 4.0, Warden 2018) is downloaded once into a cache outside
the repository; `just kws-clean` deletes it. Only the trained weights, about
300 KB, are committed as models/kws.pt.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from torch import nn

from ri.senses.kws import CLIP, LABELS, MODEL_PATH, KeywordNet, parameter_count
from ri.types import Array

WORDS = ("up", "down", "stop")


def load(path: Path) -> Array:
    audio, _ = sf.read(path, dtype="float32")
    out = np.zeros(CLIP, dtype=np.float32)
    out[: min(CLIP, len(audio))] = audio[:CLIP]
    return out


def split_lists(root: Path) -> tuple[set[str], set[str]]:
    val = set((root / "validation_list.txt").read_text().split())
    test = set((root / "testing_list.txt").read_text().split())
    return val, test


def build(root: Path, seed: int) -> dict[str, list[tuple[Path, int]]]:
    rng = random.Random(seed)
    val, test = split_lists(root)
    parts: dict[str, list[tuple[Path, int]]] = {"train": [], "val": [], "test": []}
    others = [
        d
        for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and d.name not in WORDS
    ]
    for label_index, word in enumerate(WORDS):
        for wav in sorted((root / word).glob("*.wav")):
            rel = f"{word}/{wav.name}"
            part = "test" if rel in test else "val" if rel in val else "train"
            parts[part].append((wav, label_index))
    unknown = LABELS.index("_unknown_")
    for d in others:
        for wav in sorted(d.glob("*.wav")):
            rel = f"{d.name}/{wav.name}"
            part = "test" if rel in test else "val" if rel in val else "train"
            keep = 0.12 if part == "train" else 0.06
            if rng.random() < keep:
                parts[part].append((wav, unknown))
    return parts


class Noise:
    def __init__(self, root: Path) -> None:
        self.clips = [load_long(p) for p in sorted((root / "_background_noise_").glob("*.wav"))]

    def sample(self, rng: np.random.Generator) -> Array:
        clip = self.clips[int(rng.integers(len(self.clips)))]
        start = int(rng.integers(0, len(clip) - CLIP))
        return clip[start : start + CLIP]


def load_long(path: Path) -> Array:
    audio, _ = sf.read(path, dtype="float32")
    return np.asarray(audio, dtype=np.float32)


def batch(
    items: list[tuple[Path, int]],
    noise: Noise,
    rng: np.random.Generator,
    augment: bool,
    silence_share: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    waves, labels = [], []
    for path, label in items:
        wave = load(path)
        if augment:
            shift = int(rng.integers(-1600, 1600))
            wave = np.roll(wave, shift)
            wave *= float(rng.uniform(0.4, 1.6))
            if rng.random() < 0.8:
                wave = wave + noise.sample(rng) * float(rng.uniform(0.0, 0.3))
        waves.append(wave)
        labels.append(label)
    silence = LABELS.index("_silence_")
    for _ in range(int(len(items) * silence_share)):
        waves.append(noise.sample(rng) * float(rng.uniform(0.0, 1.0)))
        labels.append(silence)
    return torch.from_numpy(np.stack(waves)), torch.tensor(labels)


def evaluate(
    model: nn.Module, items: list[tuple[Path, int]], noise: Noise, device: torch.device
) -> float:
    model.eval()
    rng = np.random.default_rng(7)
    correct = total = 0
    with torch.no_grad():
        for i in range(0, len(items), 256):
            x, y = batch(items[i : i + 256], noise, rng, augment=False, silence_share=0.1)
            pred = model(x.to(device)).argmax(-1).cpu()
            correct += int((pred == y).sum())
            total += len(y)
    return correct / max(total, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=14)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=MODEL_PATH)
    args = ap.parse_args()
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    parts = build(args.data, args.seed)
    noise = Noise(args.data)
    print({k: len(v) for k, v in parts.items()}, "device", device)
    model = KeywordNet().to(device)
    print("parameters", parameter_count(model))
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-3)
    steps = args.epochs * (len(parts["train"]) // 128 + 1)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=steps)
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    best = 0.0
    for epoch in range(args.epochs):
        model.train()
        train = parts["train"][:]
        random.Random(args.seed + epoch).shuffle(train)
        started = time.time()
        for i in range(0, len(train), 128):
            x, y = batch(train[i : i + 128], noise, rng, augment=True, silence_share=0.1)
            opt.zero_grad()
            loss = loss_fn(model(x.to(device)), y.to(device))
            loss.backward()
            opt.step()
            sched.step()
        acc = evaluate(model, parts["val"], noise, device)
        print(
            f"epoch {epoch + 1} val {acc:.3f} loss {loss.item():.3f} {time.time() - started:.0f}s"
        )
        if acc > best:
            best = acc
            state = {k: v.cpu() for k, v in model.state_dict().items()}
            args.out.parent.mkdir(parents=True, exist_ok=True)
            torch.save({"state_dict": state, "labels": list(LABELS)}, args.out)
    model.load_state_dict(torch.load(args.out, weights_only=True)["state_dict"])
    test = evaluate(model.to(device), parts["test"], noise, device)
    report = {"val": round(best, 4), "test": round(test, 4), "parameters": parameter_count(model)}
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print("saved", args.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
