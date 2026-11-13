"""Record the whole five-minute run, headless, and check it.

    uv run python -m ri.record            # what `just record-sim` runs

1. Resets to a true zero state (pipelines stopped, drone dark, hook line).
2. Opens the screen in an isolated agent-browser session at 1920x1080 and
   starts recording. Never the presenter's own browser.
3. Runs the conductor and the beat checker side by side.
4. Stops the recording, then lays the sounds the devices actually heard
   (the applause, each spoken word) onto the video at the moment each was
   played, so the take is watchable with sound.
5. Pulls one frame per beat into recordings/frames/ and prints the beat
   report. A missed beat exits non-zero.

The rig must already be up (`just up`); `just record-sim` does both ends.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from ri import arccheck, conductor
from ri.ports import ROOT, RUNTIME, url

RECORDINGS = ROOT / "recordings"
AUDIO = ROOT / "fixtures" / "audio"
SESSION = "ri-record"
SR = 16000
FRAMES_AT = {
    "00-hook": 5,
    "01-pipelines-on": 24,
    "02-clap-wakes": 37,
    "03-shout-up": 82,
    "04-waiting": 155,
    "05-majority-lands": 168,
    "06-woken-again": 188,
    "07-conflict": 201,
    "08-reveal": 245,
    "09-handoff": 285,
}


def browser(*args: str, timeout: float = 60) -> str:
    out = subprocess.run(
        ["agent-browser", "--session", SESSION, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if out.returncode != 0:
        raise RuntimeError(f"agent-browser {' '.join(args[:2])}: {out.stderr or out.stdout}")
    return out.stdout


def soundtrack(video_start: float, length_s: float, path: Path) -> None:
    """Every clip the conductor played, placed where it was played."""
    track = np.zeros(int(SR * (length_s + 2)), dtype=np.float32)
    for line in (RUNTIME / "conductor.jsonl").read_text().splitlines()[1:]:
        event = json.loads(line)
        result = event.get("result")
        if not isinstance(result, dict) or "file" not in result:
            continue
        clip, _ = sf.read(AUDIO / result["file"], dtype="float32")
        start_t = float(json.loads((RUNTIME / "conductor.jsonl").read_text().splitlines()[0])["t"])
        at = int((start_t + float(event["offset"]) - video_start) * SR)
        if 0 <= at < len(track):
            part = clip[: len(track) - at]
            track[at : at + len(part)] += part * 0.9
    sf.write(path, np.clip(track, -1, 1), SR)


def main() -> int:
    if shutil.which("agent-browser") is None or shutil.which("ffmpeg") is None:
        raise SystemExit("needs agent-browser and ffmpeg on PATH")
    RECORDINGS.mkdir(exist_ok=True)
    frames = RECORDINGS / "frames"
    frames.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    silent = RUNTIME / f"take-{stamp}.mp4"
    final = RECORDINGS / f"ri-nervous-system-{stamp}.mp4"

    print("warm-up: start all four pipelines, see them run")
    conductor.warm_up()
    print("zero state")
    conductor.reset()
    browser("open", url("PORT", "/"))
    browser("set", "viewport", "1920", "1080")
    time.sleep(4)
    conductor.post(url("PORT", "/api/reset"), {})  # anything that landed late
    time.sleep(1)

    print(f"recording {final.name}")
    browser("record", "start", str(silent))
    video_start = time.time()
    watcher = threading.Thread(target=arccheck.watch, args=(315.0,), daemon=True)
    watcher.start()
    time.sleep(1.0)
    conductor.run(1.0)
    time.sleep(4.0)
    browser("record", "stop", timeout=180)
    length = time.time() - video_start
    watcher.join(timeout=20)
    browser("close")

    print("soundtrack")
    wav = RUNTIME / f"take-{stamp}.wav"
    soundtrack(video_start, length, wav)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(silent),
            "-i",
            str(wav),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(final),
        ],
        check=True,
    )
    silent.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)

    t0 = float(json.loads((RUNTIME / "conductor.jsonl").read_text().splitlines()[0])["t"])
    for name, at in FRAMES_AT.items():
        offset = max(0.0, t0 + at - video_start)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{offset:.2f}",
                "-i",
                str(final),
                "-frames:v",
                "1",
                str(frames / f"{stamp}-{name}.png"),
            ],
            check=True,
        )
    print(f"saved {final.relative_to(ROOT)} ({final.stat().st_size / 1e6:.1f} MB)")
    print(
        f"frames in {frames.relative_to(ROOT)}/ (offset of the arc in the video: "
        f"{t0 - video_start:.1f}s)"
    )
    return arccheck.report()


if __name__ == "__main__":
    raise SystemExit(main())
