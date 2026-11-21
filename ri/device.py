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
import contextlib
import json
import logging
import os
import signal
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
from ri.capture import LiveCamera, LiveMic
from ri.messages import Decision
from ri.ports import DEFAULT_CAMERA, DEFAULT_INPUT, DEFAULT_MIC, port
from ri.senses.hands import HandBox, annotate_hands
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
        "fps": 10.0,
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


FORCEABLE: dict[str, tuple[str, ...]] = {
    "clap": ("clap",),
    "word": ("up", "down", "stop"),
    "hands": ("forward", "left", "right", "jazz", "crowd"),
}


def forced_decision(sense: str, value: str, seq: int) -> Decision:
    """The decision a model would have sent, typed by the presenter instead.

    The stage fallback: when a microphone or camera lets the presenter down,
    a key sends the decision the model should have made. It leaves the device
    the same way a real one does, through the pipeline.
    """
    if value not in FORCEABLE.get(sense, ()):
        raise ValueError(f"cannot force {value!r} on {sense!r}")
    now = time.time()
    if sense == "clap":
        return Decision("clap", now, confidence=0.99, seq=seq)
    if sense == "word":
        return Decision("word", now, confidence=0.99, value=value, seq=seq)
    if value == "crowd":
        return Decision("hands", now, up=24, seen=28, share=24 / 28, seq=seq)
    return Decision("hands", now, up=1, seen=1, share=1.0, gesture=value, seq=seq)


class Device:
    def __init__(self, sense: str, *, input_mode: str = "fixture") -> None:
        self.sense = sense
        self.spec = DEVICES[sense]
        self.name: str = self.spec["name"]
        self.input_mode = input_mode
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
        self.forced: deque[str] = deque()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.bus.stop()

    def force(self, value: str) -> dict[str, Any]:
        """Queue a presenter-typed decision; the next window carries it out."""
        if value not in FORCEABLE.get(self.sense, ()):
            raise ValueError(f"cannot force {value!r} on {self.sense!r}")
        with self.lock:
            self.forced.append(value)
        return {"forced": value}

    def answer(self, payload: bytes) -> dict[str, Any]:
        """What the pipeline's model step gets back: a forced decision, or the model's."""
        with self.lock:
            value = self.forced.popleft() if self.forced else None
        if value is None:
            return self.infer(payload)
        with self.lock:
            self.windows += 1
        return forced_decision(self.sense, value, self.next_seq()).to_json()

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
            "input": self.input_mode,
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
    def __init__(
        self, sense: str, *, input_mode: str = "fixture", device_spec: str | None = None
    ) -> None:
        super().__init__(sense, input_mode=input_mode)
        self.rng = np.random.default_rng(7 if sense == "clap" else 8)
        self.bed = pink(SR * 30, self.rng) * float(self.spec["bed"])
        self.bed_pos = 0
        self.ring: deque[Array] = deque(maxlen=int(1.0 / TICK_S))
        self.wave: deque[int] = deque([0] * 32, maxlen=32)
        self.playing: list[tuple[Array, int]] = []
        self.model: Any = None
        self.mic_wanted = device_spec or DEFAULT_MIC
        self.mic: LiveMic | None = (
            LiveMic(device_spec or DEFAULT_MIC, SR) if input_mode == "live" else None
        )

    def telemetry(self) -> dict[str, Any]:
        with self.lock:
            mic = self.mic
            body = super().telemetry()
            body["wave"] = list(self.wave)
        if mic is not None and mic.device_name:
            body["mic"] = mic.device_name
            body["mic_wanted"] = self.mic_wanted
        return body

    def set_source(
        self,
        device_spec: str | None = None,
        *,
        input_mode: str | None = None,
    ) -> dict[str, Any]:
        spec = (device_spec or "").strip()
        mode = (input_mode or ("fixture" if spec.lower() == "fixture" else "live")).strip().lower()
        if mode not in ("fixture", "live"):
            raise ValueError(f"unknown input_mode {mode!r}")
        new_mic: LiveMic | None = None
        if mode == "live":
            target = spec or "dji"
            new_mic = LiveMic(target, SR, strict=target.lower() not in ("auto", "default"))
        with self.lock:
            old_mic = self.mic
            self.mic = new_mic
            if new_mic is not None:
                self.mic_wanted = spec or "dji"
            self.input_mode = mode
        if old_mic is not None:
            old_mic.close()
        return {
            "input": mode,
            "mic": new_mic.device_name if new_mic is not None else None,
        }

    def close(self) -> None:
        with self.lock:
            mic = self.mic
            self.mic = None
        if mic is not None:
            mic.close()
        super().close()

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
        with self.lock:
            mic = self.mic
        if mic is not None:
            out = mic.read_chunk(n).copy()
        else:
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
            if tick % 2 == 0:
                self.wave.append(max(0, min(99, round(self.level * 99))))
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
    # On a live mic one bang is not a wake: a door, a dropped cup and the
    # drone's own landing all pass for a single clap. Five in a row do not.
    CLAPS_NEEDED = 5
    CLAPS_WITHIN_S = 3.0
    SAME_CLAP_S = 0.12  # one clap is heard again in each overlapping window
    # Five bangs are still not five claps. They must come at a steady beat,
    # as a person claps and as speech and clatter do not, and the classifier
    # must have called several of those windows clapping, not just one.
    GAP_S = (0.15, 0.8)
    GAP_SPREAD = 2.5  # the longest gap over the shortest
    WINDOWS_NEEDED = 3
    TYPING_MAX = 0.3
    NEAR_MISS_EVERY_S = 2.0

    def __init__(self, *, input_mode: str = "fixture", device_spec: str | None = None) -> None:
        super().__init__("clap", input_mode=input_mode, device_spec=device_spec)
        self.claps: deque[float] = deque()
        self.clap_windows: deque[float] = deque()
        self.near_miss_at = 0.0
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
        clapping = score.triggered(self.MIN_CONFIDENCE) and score.typing < self.TYPING_MAX
        enough = self.input_mode != "live" or self.enough_claps(window, now, clapping)
        if clapping and fresh and enough:
            self.last_emit["clap"] = now
            self.claps.clear()
            self.clap_windows.clear()
            decision = Decision("clap", now, confidence=score.confidence, seq=self.next_seq())
            return decision.to_json()
        return {"sense": None, "window": windows, "heard": round(score.confidence, 2)}

    def enough_claps(self, window: Array, now: float, clapping: bool) -> bool:
        """A live wake: five claps at a steady beat, heard as clapping more than once."""
        counted = self.count_claps(window, now)
        if clapping:
            self.clap_windows.append(now)
        while self.clap_windows and now - self.clap_windows[0] > self.CLAPS_WITHIN_S:
            self.clap_windows.popleft()
        steady = self.steady_claps()
        votes = len(self.clap_windows)
        woke = steady >= self.CLAPS_NEEDED and votes >= self.WINDOWS_NEEDED
        if woke:
            log.info("wake: %d steady claps, %d windows heard as clapping", steady, votes)
        elif counted >= 3 and now - self.near_miss_at > self.NEAR_MISS_EVERY_S:
            self.near_miss_at = now
            log.info(
                "no wake: %d sharp sounds, %d at a steady beat (need %d), "
                "%d windows heard as clapping (need %d)",
                counted,
                steady,
                self.CLAPS_NEEDED,
                votes,
                self.WINDOWS_NEEDED,
            )
        return woke

    def steady_claps(self) -> int:
        """The longest run of counted claps that keeps one beat."""
        times = sorted(self.claps)
        best = min(len(times), 1)
        for first in range(len(times)):
            gaps: list[float] = []
            for at, after in zip(times[first:], times[first + 1 :], strict=False):
                trial = [*gaps, after - at]
                if not self.GAP_S[0] <= trial[-1] <= self.GAP_S[1]:
                    break
                if max(trial) > self.GAP_SPREAD * min(trial):
                    break
                gaps = trial
            best = max(best, len(gaps) + 1)
        return best

    def count_claps(self, window: Array, now: float) -> int:
        """How many separate claps the last few seconds held, this window included."""
        from ri.senses.clap import onset_times

        start = now - len(window) / SR
        for offset in onset_times(window, SR):
            at = start + offset
            if all(abs(at - seen) > self.SAME_CLAP_S for seen in self.claps):
                self.claps.append(at)
        while self.claps and now - min(self.claps) > self.CLAPS_WITHIN_S:
            self.claps.remove(min(self.claps))
        return len(self.claps)


class WordDevice(AudioDevice):
    MIN_CONFIDENCE = 0.75
    MIN_MARGIN = 0.25
    MIN_RMS = 0.004
    REFRACTORY_S = 1.2

    def __init__(self, *, input_mode: str = "fixture", device_spec: str | None = None) -> None:
        super().__init__("word", input_mode=input_mode, device_spec=device_spec)
        from ri.senses.kws import KeywordSpotter

        self.model = KeywordSpotter()

    def cue(self, body: dict[str, Any]) -> dict[str, Any]:
        click = body.get("click")
        if click is not None:
            word = str(click)
            if word not in ("up", "down", "stop"):
                raise ValueError(f"unknown clicker command {word!r}")
            clip = load_clip(CLIPS[word][0])
            window = np.zeros(SR, dtype=np.float32)
            window[-min(SR, len(clip)) :] = clip[:SR]
            pcm = np.clip(window * 32767, -32768, 32767).astype("<i2").tobytes()
            threading.Thread(
                target=self.post, args=(pcm, "application/octet-stream"), daemon=True
            ).start()
            return {"clicked": word}
        return super().cue(body)

    def infer(self, payload: bytes) -> dict[str, Any]:
        window = self.window_from(payload)
        now = time.time()
        with self.lock:
            self.windows += 1
            windows = self.windows
        match = self.model.classify(
            window,
            min_rms=self.MIN_RMS,
            min_conf=self.MIN_CONFIDENCE,
            min_margin=self.MIN_MARGIN,
        )
        fresh = now - self.last_emit.get("word", 0.0) > self.REFRACTORY_S
        if match.word is not None and fresh:
            self.last_emit["word"] = now
            decision = Decision(
                "word",
                now,
                confidence=match.confidence,
                value=match.word,
                seq=self.next_seq(),
            )
            return decision.to_json()
        return {"sense": None, "window": windows, "heard": match.heard}


class HandsDevice(Device):
    EMIT_EVERY = 5  # frames; with 10 fps a hands decision every 0.5 s

    def __init__(
        self,
        *,
        input_mode: str = "fixture",
        device_spec: str | None = None,
        roi: str | None = None,
    ) -> None:
        super().__init__("hands", input_mode=input_mode)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".runtime" / "ultralytics"))
        import cv2

        from ri.camera import VotingBlock
        from ri.senses.hands import HandCounter

        self.cv2: Any = cv2
        self.camera: LiveCamera | None = (
            LiveCamera(device_spec or DEFAULT_CAMERA) if input_mode == "live" else None
        )
        self.block: VotingBlock | None = None if input_mode == "live" else VotingBlock()
        if self.block is not None:
            self.block.set_scene("idle")
        self.counter = HandCounter(roi=roi)
        self.previous: Array | None = None
        self.wave: deque[int] = deque([0] * 32, maxlen=32)
        self.fps = 0.0
        self._last_boxes: list[HandBox] = []
        self._boxes_at = 0.0
        # Rehearsal only: the fixture crowd has no presenter, so a cue stands in.
        self.forced_signal: str | None = None
        self.arms_in_view = False

    def close(self) -> None:
        if self.camera is not None:
            self.camera.close()
        super().close()

    def cue(self, body: dict[str, Any]) -> dict[str, Any]:
        scene = str(body.get("scene", ""))
        if self.block is None:
            return {"scene": scene, "input": "live"}
        if "gesture" in body:
            self.forced_signal = body["gesture"] or None
        if scene:
            self.block.set_scene(scene)
        return {"scene": scene, "gesture": self.forced_signal}

    def capture(self) -> None:
        period = 1.0 / float(self.spec["fps"])
        next_t = time.monotonic()
        last = time.monotonic()
        while True:
            frame = (
                self.camera.read()
                if self.camera is not None
                else (self.block.frame(time.monotonic()) if self.block is not None else None)
            )
            if frame is None:
                time.sleep(0.05)
                next_t = time.monotonic() + period
                continue
            small = self.cv2.resize(frame, (96, 60)).astype(np.float32)
            if self.previous is not None:
                motion = float(np.abs(small - self.previous).mean())
                # idle crowd shuffle reads about 12, hands going up about 20
                self.level = max(0.0, min(1.0, (motion - 6.0) / 24.0))
            self.wave.append(max(0, min(99, round(self.level * 99))))
            self.previous = small
            now = time.monotonic()
            if self.camera is not None and (now - self._boxes_at) >= 0.18:
                with self.lock:
                    if (time.monotonic() - self._boxes_at) >= 0.18:
                        _, boxes = self.counter.detect_frame(frame)
                        self._last_boxes = boxes
                        self._boxes_at = time.monotonic()
            ok, jpeg = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                threading.Thread(
                    target=self.post, args=(jpeg.tobytes(), "image/jpeg"), daemon=True
                ).start()
            self.fps = 0.8 * self.fps + 0.2 * (1.0 / max(1e-3, now - last))
            last = now
            next_t += period
            time.sleep(max(0.0, next_t - time.monotonic()))

    def infer(self, payload: bytes) -> dict[str, Any]:
        image = self.cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), self.cv2.IMREAD_COLOR)
        with self.lock:
            count, boxes = self.counter.detect_frame(image)
            smoothed = self.counter.add(count)
            self.arms_in_view = count.arms is not None
            self._last_boxes = boxes
            self._boxes_at = time.monotonic()
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
            gesture=smoothed.gesture if self.block is None else self.forced_signal,
            seq=self.next_seq(),
        )
        return decision.to_json()

    def preview_jpeg(self) -> bytes | None:
        if self.camera is None:
            return None
        frame = self.camera.read()
        if frame is None:
            return self.camera.jpeg()
        with self.lock:
            boxes = list(self._last_boxes) if (time.monotonic() - self._boxes_at) < 1.5 else []
        if not boxes and self.counter.roi is None:
            return self.camera.jpeg()
        drawn = annotate_hands(frame, boxes, roi=self.counter.roi)
        ok, buf = self.cv2.imencode(".jpg", drawn, [self.cv2.IMWRITE_JPEG_QUALITY, 82])
        return buf.tobytes() if ok else self.camera.jpeg()

    def telemetry(self) -> dict[str, Any]:
        body = super().telemetry()
        body["fps"] = round(self.fps, 1)
        body["wave"] = list(self.wave)
        # Whether someone's shoulders and both wrists were in the last frame:
        # without them no wave can ever be read.
        body["arms"] = self.arms_in_view
        # What the last window's arms measured, for `just arms`.
        body["arms_readout"] = self.counter.readout
        body["marshal_px"] = self.counter.marshal_px
        if self.camera is not None and self.camera.device_name:
            body["camera"] = self.camera.device_name
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
            elif self.path == "/preview.jpg":
                jpg = device.preview_jpeg() if isinstance(device, HandsDevice) else None
                if jpg is None:
                    self._reply(404, {"error": "live camera preview unavailable"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jpg)
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                if self.path == "/infer":
                    self._reply(200, device.answer(self._body()))
                elif self.path == "/cue":
                    cue = json.loads(self._body() or b"{}")
                    if "force" in cue:
                        self._reply(200, device.force(str(cue["force"])))
                    else:
                        self._reply(200, device.cue(cue))
                elif self.path == "/link":
                    body = json.loads(self._body() or b"{}")
                    device.link_cut = bool(body.get("cut", not device.link_cut))
                    self._reply(200, {"link": "cut" if device.link_cut else "ok"})
                elif self.path == "/mic":
                    if not isinstance(device, AudioDevice):
                        raise ValueError("audio device only")
                    body = json.loads(self._body() or b"{}")
                    self._reply(
                        200,
                        device.set_source(
                            body.get("device"),
                            input_mode=body.get("input_mode"),
                        ),
                    )
                else:
                    self._reply(404, {"error": "not found"})
            except (ValueError, KeyError, RuntimeError) as error:
                self._reply(400, {"error": str(error)})

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sense", choices=sorted(DEVICES))
    ap.add_argument("--input", dest="input_mode", choices=("fixture", "live"))
    ap.add_argument("--device", dest="device_spec")
    ap.add_argument("--roi", dest="roi")
    ap.add_argument("--host", default=os.environ.get("RI_DEVICE_BIND", "127.0.0.1"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sense_key = args.sense.upper()
    mode = (
        args.input_mode
        or os.environ.get(f"RI_{sense_key}_INPUT")
        or os.environ.get("RI_INPUT", DEFAULT_INPUT)
    )
    dev_spec = args.device_spec or os.environ.get(f"RI_{sense_key}_DEVICE")
    device: Device
    if args.sense == "clap":
        device = ClapDevice(input_mode=mode, device_spec=dev_spec)
    elif args.sense == "word":
        device = WordDevice(input_mode=mode, device_spec=dev_spec)
    else:
        device = HandsDevice(input_mode=mode, device_spec=dev_spec, roi=args.roi)
    for target in (device.capture, device.telemetry_loop):
        threading.Thread(target=target, daemon=True).start()
    control = port(device.spec["control"])
    server = ThreadingHTTPServer((args.host, control), make_handler(device))

    def handle_stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle_stop)
    log.info(
        "%s device (%s) on %s:%d, pipeline input %s",
        device.name,
        mode,
        args.host,
        control,
        device.ingest,
    )
    try:
        server.serve_forever()
    finally:
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
