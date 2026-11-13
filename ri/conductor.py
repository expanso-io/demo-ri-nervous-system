"""Drive the five-minute run with no human on stage.

    uv run python -m ri.conductor [--compress 1.0]

The conductor only does what a person in the room would do: start the
pipelines in Expanso Cloud, play a clap into the room mic, say a word into
the handheld mic, raise hands in front of the camera, and change the line
the audience is asked to act on. It never touches a decision. Every clap,
word and count still goes through the real models and the real pipelines,
and the drone only moves when fly-drone says so.

Each event is written to .runtime/conductor.jsonl with its wall time, so the
recording can be checked beat by beat.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ri import cloud
from ri.ports import RUNTIME, url

PROMPTS = {
    "hook": "This drone has no brain of its own. You are its nervous system.",
    "clap": "Clap to wake it.",
    "shout": "Shout up or down.",
    "hands": "Hands up to land it.",
    "again": "Clap to wake it again.",
    "conflict": "Hands up. And shout up.",
    "reveal": "",
    "handoff": "",
}


def post(target: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(target, data=json.dumps(body).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=10) as response:
        result: dict[str, Any] = json.loads(response.read() or b"{}")
        return result


def scene(beat: str, prompt: str | None = None) -> Callable[[], Any]:
    text = PROMPTS[beat] if prompt is None else PROMPTS[prompt]
    return lambda: post(url("PORT", "/api/scene"), {"beat": beat, "prompt": text})


def clip(device_port: str, name: str) -> Callable[[], Any]:
    return lambda: post(url(device_port, "/cue"), {"clip": name})


def camera(name: str) -> Callable[[], Any]:
    return lambda: post(url("HANDS_DEVICE_PORT", "/cue"), {"scene": name})


def start_pipelines() -> dict[str, Any]:
    """What the presenter does in Expanso Cloud: start the four jobs."""
    threads = [threading.Thread(target=cloud.rerun, args=(job,)) for job in cloud.JOBS]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return {"started": list(cloud.JOBS)}


@dataclass(frozen=True)
class Cue:
    at: float
    what: str
    run: Callable[[], Any]


# The arc from docs/BUILD_SPEC.md part 2, in seconds from the top.
ARC: tuple[Cue, ...] = (
    Cue(0.0, "hook", scene("hook")),
    Cue(0.0, "crowd sits, hands down", camera("down")),
    Cue(8.0, "presenter starts the pipelines in Expanso Cloud", start_pipelines),
    Cue(30.0, "beat: clap to wake", scene("clap")),
    Cue(33.0, "the room claps", clip("CLAP_DEVICE_PORT", "applause")),
    Cue(75.0, "beat: shout up or down", scene("shout")),
    Cue(78.0, "volunteer: up", clip("WORD_DEVICE_PORT", "up")),
    Cue(91.0, "volunteer: up", clip("WORD_DEVICE_PORT", "up")),
    Cue(104.0, "volunteer: down", clip("WORD_DEVICE_PORT", "down")),
    Cue(117.0, "volunteer: down", clip("WORD_DEVICE_PORT", "down")),
    Cue(126.0, "volunteer: up", clip("WORD_DEVICE_PORT", "up")),
    Cue(135.0, "beat: hands up to land", scene("hands")),
    Cue(138.0, "some hands go up", camera("half")),
    Cue(162.0, "most hands go up", camera("majority")),
    Cue(174.0, "hands down", camera("down")),
    Cue(178.0, "beat: wake it again", scene("hands", "again")),
    Cue(180.0, "the room claps", clip("CLAP_DEVICE_PORT", "applause")),
    Cue(195.0, "beat: conflict", scene("conflict")),
    Cue(196.0, "most hands go up", camera("conflict")),
    Cue(198.0, "volunteer: up, while the hands are up", clip("WORD_DEVICE_PORT", "up")),
    Cue(212.0, "hands down", camera("down")),
    Cue(230.0, "beat: reveal", scene("reveal")),
    Cue(270.0, "beat: handoff", scene("handoff")),
    Cue(300.0, "end", lambda: {"end": True}),
)


def reset() -> None:
    """A true zero state: pipelines stopped, drone dark, screen at the hook."""
    for job in cloud.JOBS:
        cloud.stop(job)
    post(url("FLIGHT_PORT", "/reset"), {})
    camera("down")()
    scene("hook")()


def run(compress: float, start_at: float = 0.0) -> int:
    RUNTIME.mkdir(exist_ok=True)
    log_path = RUNTIME / "conductor.jsonl"
    t0 = time.time() - start_at / compress
    with log_path.open("w") as log:
        log.write(json.dumps({"event": "start", "t": t0, "compress": compress}) + "\n")
        for cue in ARC:
            if cue.at < start_at:
                continue
            due = t0 + cue.at / compress
            time.sleep(max(0.0, due - time.time()))
            fired = time.time()
            try:
                result: Any = cue.run()
                ok = True
            except OSError as error:
                result, ok = str(error), False
            except RuntimeError as error:
                result, ok = str(error), False
            line = {
                "at": cue.at,
                "offset": round(fired - t0, 2),
                "what": cue.what,
                "ok": ok,
                "result": result,
            }
            log.write(json.dumps(line, default=str) + "\n")
            log.flush()
            mark = "ok " if ok else "ERR"
            print(f"{mark} {fired - t0:6.1f}s  {cue.what}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compress", type=float, default=1.0, help="2.0 runs the arc twice as fast")
    ap.add_argument("--from", dest="start_at", type=float, default=0.0)
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()
    if not args.no_reset:
        reset()
        time.sleep(2.0)
    return run(args.compress, args.start_at)


if __name__ == "__main__":
    raise SystemExit(main())
