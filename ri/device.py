"""One sense device: the box with a microphone or a camera and one model.

    uv run python -m ri.device clap|word|hands

What runs on the box, apart from its Expanso Edge agent:

* capture: the room mic, the handheld mic or the camera. For rehearsal the
  feed is a fixture: room tone with cued clips, or the voting-block camera.
* telemetry: a level number ten times a second, so the screen shows the
  sense is alive before any pipeline exists. Never audio, never pixels.
* the pipeline's input: every capture window is posted to the local
  Expanso pipeline's http_server. If the pipeline is off, the window goes
  nowhere and is counted as dropped.
* the model: the pipeline's inference step calls POST /infer here.

Capture windows stay on this machine; only decisions leave it, through the
pipeline's MQTT output.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from ri.bus import DEVICE_TOPIC, Bus
from ri.messages import Decision
from ri.ports import port
from ri.types import Array

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "fixtures" / "audio"
SR = 16000
TICK_S = 0.05
log = logging.getLogger("ri.device")

DEVICES: dict[str, dict[str, Any]] = {
    "clap": {
        "name": "hear-clap",
        "ingest": "CLAP_INGEST_PORT",
        "control": "CLAP_DEVICE_PORT",
        "bed": 0.002,
        "hop_s": 0.25,
    },
    "word": {
        "name": "hear-commands",
        "ingest": "WORD_INGEST_PORT",
        "control": "WORD_DEVICE_PORT",
        "bed": 0.0015,
        "hop_s": 0.25,
    },
    "hands": {
        "name": "see-hands",
        "ingest": "HANDS_INGEST_PORT",
        "control": "HANDS_DEVICE_PORT",
        "fps": 5.0,
    },
}

CLIPS: dict[str, list[str]] = {
    "applause": ["applause.wav"],
    "up": ["word-up-1.wav", "word-up-2.wav", "word-up-3.wav"],
    "down": ["word-down-1.wav", "word-down-2.wav", "word-down-3.wav"],
    "stop": ["word-stop-1.wav", "word-stop-2.wav", "word-stop-3.wav"],
}


def load_clip(name: str) -> Array:
    audio, rate = sf.read(AUDIO / name, dtype="float32")
    if rate != SR:
        raise ValueError(f"{name}: expected {SR} Hz")
    return np.asarray(audio, dtype=np.float32)


def pink(n: int, rng: np.random.Generator) -> Array:
    spectrum = np.fft.rfft(rng.normal(0, 1, n))
    spectrum /= np.sqrt(np.arange(len(spectrum)) + 1.0)
    noise = np.fft.irfft(spectrum, n)
    return np.asarray(noise / (noise.std() + 1e-9), dtype=np.float32)


class Device:
    def __init__(self, sense: str) -> None:
        self.sense = sense
        self.spec = DEVICES[sense]
        self.name: str = self.spec["name"]
        self.lock = threading.Lock()
        self.link_cut = False
        self.sent = 0
        self.dropped = 0
        self.decisions = 0
        self.seq = 0
        self.windows = 0
        self.captured_bytes = 0
        self.level = 0.0
        self.bus = Bus(f"device-{self.name}").start()
        self.ingest = f"http://127.0.0.1:{port(self.spec['ingest'])}/frames"
        self.last_emit: dict[str, float] = {}
        self.clip_rotation: dict[str, int] = {}

    # --- capture --------------------------------------------------------
    def capture(self) -> None:
        raise NotImplementedError

    def cue(self, body: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    # --- model ----------------------------------------------------------
    def infer(self, payload: bytes) -> dict[str, Any]:
        raise NotImplementedError

    def next_seq(self) -> int:
        with self.lock:
            self.seq += 1
            self.decisions += 1
            return self.seq

    # --- pipeline input -------------------------------------------------
    def post(self, body: bytes, content_type: str) -> None:
        self.captured_bytes += len(body)
        if self.link_cut:
            self.dropped += 1
            return
        request = urllib.request.Request(self.ingest, data=body, method="POST")
        request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read()
            self.sent += 1
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            self.dropped += 1

    # --- telemetry ------------------------------------------------------
    def telemetry(self) -> dict[str, Any]:
        return {
            "device": self.name,
            "sense": self.sense,
            "level": round(self.level, 3),
            "sent": self.sent,
            "dropped": self.dropped,
            "windows": self.windows,
            "decisions": self.decisions,
            "captured_bytes": self.captured_bytes,
            "link": "cut" if self.link_cut else "ok",
            "t": round(time.time(), 3),
        }

    def telemetry_loop(self) -> None:
        topic = DEVICE_TOPIC.format(device=self.name)
        while True:
            self.bus.publish(topic, self.telemetry())
            time.sleep(0.1)


class AudioDevice(Device):
    def __init__(self, sense: str) -> None:
        super().__init__(sense)
        self.rng = np.random.default_rng(7 if sense == "clap" else 8)
        self.bed = pink(SR * 30, self.rng) * float(self.spec["bed"])
        self.bed_pos = 0
        self.ring: deque[Array] = deque(maxlen=int(1.0 / TICK_S))
        self.playing: list[tuple[Array, int]] = []
        self.model: Any = None

    def cue(self, body: dict[str, Any]) -> dict[str, Any]:
        clip_name = str(body.get("clip", ""))
        if clip_name not in CLIPS:
            raise ValueError(f"unknown clip {clip_name!r}")
        variants = CLIPS[clip_name]
        index = self.clip_rotation.get(clip_name, 0)
        self.clip_rotation[clip_name] = index + 1
        clip = load_clip(variants[index % len(variants)]) * float(body.get("gain", 1.0))
        with self.lock:
            self.playing.append((clip, 0))
        return {"cued": clip_name, "file": variants[index % len(variants)]}

    def chunk(self) -> Array:
        n = int(SR * TICK_S)
        if self.bed_pos + n > len(self.bed):
            self.bed_pos = 0
        out = self.bed[self.bed_pos : self.bed_pos + n].copy()
        self.bed_pos += n
        with self.lock:
            still: list[tuple[Array, int]] = []
            for clip, pos in self.playing:
                part = clip[pos : pos + n]
                out[: len(part)] += part
                if pos + n < len(clip):
                    still.append((clip, pos + n))
            self.playing = still
        return out

    def capture(self) -> None:
        hop_ticks = max(1, round(float(self.spec["hop_s"]) / TICK_S))
        tick = 0
        next_t = time.monotonic()
        while True:
            chunk = self.chunk()
            self.ring.append(chunk)
            rms = float(np.sqrt(np.mean(chunk**2)))
            self.level = max(0.0, min(1.0, (20 * np.log10(rms + 1e-9) + 60) / 60))
            tick += 1
            if tick % hop_ticks == 0 and len(self.ring) == self.ring.maxlen:
                window = np.concatenate(list(self.ring))
                pcm = np.clip(window * 32767, -32768, 32767).astype("<i2").tobytes()
                threading.Thread(
                    target=self.post, args=(pcm, "application/octet-stream"), daemon=True
                ).start()
            next_t += TICK_S
            time.sleep(max(0.0, next_t - time.monotonic()))

    def window_from(self, payload: bytes) -> Array:
        return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0


class ClapDevice(AudioDevice):
    MIN_CONFIDENCE = 0.5
    REFRACTORY_S = 3.5  # one round of applause is one clap

    def __init__(self) -> None:
        super().__init__("clap")
        from ri.senses.clap import ClapDetector

        self.model = ClapDetector()

    def infer(self, payload: bytes) -> dict[str, Any]:
        window = self.window_from(payload)
        score = self.model.score(window)
        now = time.time()
        with self.lock:
            self.windows += 1
            windows = self.windows
        fresh = now - self.last_emit.get("clap", 0.0) > self.REFRACTORY_S
        if score.onset and score.confidence >= self.MIN_CONFIDENCE and fresh:
            self.last_emit["clap"] = now
            decision = Decision("clap", now, confidence=score.confidence, seq=self.next_seq())
            return decision.to_json()
        return {"sense": None, "window": windows, "heard": round(score.confidence, 2)}


class WordDevice(AudioDevice):
    MIN_CONFIDENCE = 0.75
    MIN_RMS = 0.01
    REFRACTORY_S = 1.2

    def __init__(self) -> None:
        super().__init__("word")
        from ri.senses.kws import KeywordSpotter

        self.model = KeywordSpotter()

    def infer(self, payload: bytes) -> dict[str, Any]:
        window = self.window_from(payload)
        now = time.time()
        with self.lock:
            self.windows += 1
            windows = self.windows
        rms = float(np.sqrt(np.mean(window**2)))
        if rms < self.MIN_RMS:
            return {"sense": None, "window": windows, "heard": "_silence_"}
        label, confidence = self.model.best(window)
        fresh = now - self.last_emit.get("word", 0.0) > self.REFRACTORY_S
        if label in ("up", "down", "stop") and confidence >= self.MIN_CONFIDENCE and fresh:
            self.last_emit["word"] = now
            decision = Decision(
                "word", now, confidence=confidence, value=label, seq=self.next_seq()
            )
            return decision.to_json()
        return {"sense": None, "window": windows, "heard": label}


class HandsDevice(Device):
    EMIT_EVERY = 3  # frames; with 5 fps a hands decision every 0.6 s

    def __init__(self) -> None:
        super().__init__("hands")
        import cv2

        from ri.camera import VotingBlock
        from ri.senses.hands import HandCounter

        self.cv2: Any = cv2
        self.block = VotingBlock()
        self.block.set_scene("idle")
        self.counter = HandCounter()
        self.previous: Array | None = None
        self.fps = 0.0

    def cue(self, body: dict[str, Any]) -> dict[str, Any]:
        scene = str(body.get("scene", ""))
        self.block.set_scene(scene)
        return {"scene": scene}

    def capture(self) -> None:
        period = 1.0 / float(self.spec["fps"])
        next_t = time.monotonic()
        last = time.monotonic()
        while True:
            frame = self.block.frame(time.monotonic())
            small = self.cv2.resize(frame, (96, 60)).astype(np.float32)
            if self.previous is not None:
                motion = float(np.abs(small - self.previous).mean())
                # idle crowd shuffle reads about 12, hands going up about 20
                self.level = max(0.0, min(1.0, (motion - 6.0) / 24.0))
            self.previous = small
            ok, jpeg = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                threading.Thread(
                    target=self.post, args=(jpeg.tobytes(), "image/jpeg"), daemon=True
                ).start()
            now = time.monotonic()
            self.fps = 0.8 * self.fps + 0.2 * (1.0 / max(1e-3, now - last))
            last = now
            next_t += period
            time.sleep(max(0.0, next_t - time.monotonic()))

    def infer(self, payload: bytes) -> dict[str, Any]:
        image = self.cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), self.cv2.IMREAD_COLOR)
        with self.lock:
            count = self.counter.count_frame(image)
            smoothed = self.counter.add(count)
            self.windows += 1
            windows = self.windows
        if windows % self.EMIT_EVERY:
            return {"sense": None, "window": windows, "heard": f"{count.up}/{count.seen}"}
        share = smoothed.up / smoothed.seen if smoothed.seen else 0.0
        decision = Decision(
            "hands",
            time.time(),
            up=smoothed.up,
            seen=smoothed.seen,
            share=share,
            seq=self.next_seq(),
        )
        return decision.to_json()

    def telemetry(self) -> dict[str, Any]:
        body = super().telemetry()
        body["fps"] = round(self.fps, 1)
        return body


def make_handler(device: Device) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))

        def do_GET(self) -> None:
            if self.path == "/status":
                self._reply(200, device.telemetry())
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                if self.path == "/infer":
                    self._reply(200, device.infer(self._body()))
                elif self.path == "/cue":
                    self._reply(200, device.cue(json.loads(self._body() or b"{}")))
                elif self.path == "/link":
                    body = json.loads(self._body() or b"{}")
                    device.link_cut = bool(body.get("cut", not device.link_cut))
                    self._reply(200, {"link": "cut" if device.link_cut else "ok"})
                else:
                    self._reply(404, {"error": "not found"})
            except (ValueError, KeyError) as error:
                self._reply(400, {"error": str(error)})

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sense", choices=sorted(DEVICES))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    device: Device
    if args.sense == "clap":
        device = ClapDevice()
    elif args.sense == "word":
        device = WordDevice()
    else:
        device = HandsDevice()
    for target in (device.capture, device.telemetry_loop):
        threading.Thread(target=target, daemon=True).start()
    control = port(device.spec["control"])
    server = ThreadingHTTPServer(("127.0.0.1", control), make_handler(device))
    log.info("%s device on 127.0.0.1:%d, pipeline input %s", device.name, control, device.ingest)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
