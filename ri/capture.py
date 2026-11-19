"""Live audio and camera capture for stage hardware and desktop tests.

Used only when a device runs with `--input live` (or `--stage-cam ...`).
Audio uses `sounddevice` when installed and falls back to `ffmpeg` PCM pipes
on macOS/Linux; camera streams use OpenCV in a background drain thread so
inference or MJPEG clients never read stale buffered frames.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import platform
import re
import subprocess
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from ri.types import Array

log = logging.getLogger("ri.capture")

VIRTUAL_VIDEO_TERMS = ("obs virtual", "desk view", "capture screen", "virtual")
VIRTUAL_AUDIO_TERMS = (
    "zoom",
    "teams",
    "blackhole",
    "loopback",
    "soundflower",
    "joycast",
    "obs",
    "ndi",
    "virtual",
    "aggregate",
)


def _ffmpeg_avfoundation_devices() -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Parse macOS AVFoundation video and audio `(index, name)` lists from ffmpeg."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return [], []
    video: list[tuple[int, str]] = []
    audio: list[tuple[int, str]] = []
    section: str | None = None
    for line in (proc.stdout + proc.stderr).splitlines():
        if "AVFoundation video devices:" in line:
            section = "video"
            continue
        if "AVFoundation audio devices:" in line:
            section = "audio"
            continue
        match = re.search(r"\[(\d+)\]\s+(.+)$", line)
        if not match or section is None:
            continue
        entry = (int(match.group(1)), match.group(2).strip())
        (video if section == "video" else audio).append(entry)
    return video, audio


def _audio_source_args(device: str | None) -> list[str]:
    system = platform.system()
    if system == "Darwin":
        resolved, _ = resolve_audio_source(device)
        spec = (
            resolved
            if (resolved and ":" in resolved)
            else f":{resolved if resolved is not None else '0'}"
        )
        return ["-f", "avfoundation", "-i", spec]
    return ["-f", "alsa", "-i", device or "default"]


def _sounddevice_audio_inputs() -> list[str]:
    try:
        sd: Any = importlib.import_module("sounddevice")
        return [
            f"{idx}:{dev['name']}"
            for idx, dev in enumerate(sd.query_devices())
            if int(dev.get("max_input_channels", 0)) > 0
        ]
    except Exception:
        return []


def list_audio_inputs() -> list[str]:
    """Enumerate available audio capture devices as `index:name` without raising."""
    sd_inputs = _sounddevice_audio_inputs()
    if sd_inputs:
        return sd_inputs
    if platform.system() == "Darwin":
        _, audio = _ffmpeg_avfoundation_devices()
        if audio:
            return [f"{idx}:{name}" for idx, name in audio]
    return []


def list_physical_audio_inputs() -> list[str]:
    """Enumerate non-virtual `index:name` audio inputs."""
    return [
        entry
        for entry in list_audio_inputs()
        if not _is_virtual_audio(entry.partition(":")[2] or entry)
    ]


def _parse_indexed(entries: list[str]) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for item in entries:
        idx, sep, name = item.partition(":")
        parsed.append((idx.strip(), name.strip()) if sep else (item.strip(), item.strip()))
    return parsed


def _is_virtual_audio(name: str) -> bool:
    low = name.lower()
    return any(term in low for term in VIRTUAL_AUDIO_TERMS)


def resolve_audio_source(
    preferred: str | None = None,
    devices: list[str] | None = None,
    *,
    strict: bool = False,
) -> tuple[str | None, str | None]:
    """Resolve an audio input, preferring a handheld DJI mic and skipping virtual mixers."""
    raw = list_audio_inputs() if devices is None else devices
    parsed = _parse_indexed(raw)
    if not parsed:
        if strict:
            raise RuntimeError("no audio input devices visible")
        return None, None
    query = (preferred or "auto").strip()
    low = query.lower()
    if query.isdigit():
        for idx, name in parsed:
            if idx == query:
                return idx, name
        return query, None
    real = [(idx, name) for idx, name in parsed if not _is_virtual_audio(name)]
    pool = real or parsed
    if low in ("", "auto", "default") or (low == "dji" and not strict):
        for needle in ("dji", "macbook", "built-in", "internal"):
            for idx, name in pool:
                if needle in name.lower():
                    return idx, name
        return pool[0]
    if low in ("mac", "macbook", "built-in", "internal"):
        for needle in ("macbook", "built-in", "internal"):
            for idx, name in pool:
                if needle in name.lower():
                    return idx, name
        available = ", ".join(f"{idx}:{name}" for idx, name in parsed)
        raise RuntimeError(f"audio device {query!r} not found (available: {available})")
    for idx, name in parsed:
        if name.lower() == low:
            return idx, name
    for idx, name in parsed:
        if low in name.lower():
            return idx, name
    available = ", ".join(f"{idx}:{name}" for idx, name in parsed)
    raise RuntimeError(f"audio device {query!r} not found (available: {available})")


def _linux_video_devices(sysfs: Path = Path("/sys/class/video4linux")) -> list[tuple[int, str]]:
    if not sysfs.is_dir():
        return []
    found: list[tuple[int, str]] = []
    for entry in sysfs.iterdir():
        match = re.fullmatch(r"video(\d+)", entry.name)
        name_file = entry / "name"
        if match and name_file.is_file():
            found.append((int(match.group(1)), name_file.read_text().strip()))
    return sorted(found, key=lambda item: item[0])


def list_video_devices() -> list[tuple[int, str]]:
    """Enumerate camera `(index, name)` pairs in the host capture backend's order."""
    try:
        system = platform.system()
        if system == "Darwin":
            video, _ = _ffmpeg_avfoundation_devices()
            return video
        if system == "Linux":
            return _linux_video_devices()
    except Exception:
        log.debug("video device name enumeration failed", exc_info=True)
    return []


def _is_virtual_video(name: str) -> bool:
    low = name.lower()
    return any(term in low for term in VIRTUAL_VIDEO_TERMS)


def list_video_inputs(limit: int = 6) -> list[int]:
    """Probe local camera indices cleanly."""
    if platform.system() == "Darwin":
        devices = list_video_devices()
        return [idx for idx, name in devices if not _is_virtual_video(name)]
    import cv2

    found: list[int] = []
    for idx in range(limit):
        cap = cv2.VideoCapture(idx)
        try:
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    found.append(idx)
        finally:
            cap.release()
    return found


def describe_video_inputs(limit: int = 6) -> list[str]:
    """Return readable `index:name` entries for attached physical cameras."""
    devices = list_video_devices()
    if devices:
        physical = [f"{idx}:{name}" for idx, name in devices if not _is_virtual_video(name)]
        return physical or [f"{idx}:{name}" for idx, name in devices]
    return [f"{idx}:camera {idx}" for idx in list_video_inputs(limit=limit)]


def resolve_camera_source(
    source: str | int,
    devices: list[tuple[int, str]] | None = None,
) -> tuple[int | str, str | None]:
    """Resolve an index, path/URL, or human camera name to a capture source."""
    known = list_video_devices() if devices is None else devices
    named = dict(known)
    if isinstance(source, int):
        return source, named.get(source)
    text = str(source).strip()
    if "|" in text:
        # A preference list: the first camera that is plugged in wins.
        wanted = [part.strip() for part in text.split("|") if part.strip()]
        for choice in wanted[:-1]:
            with contextlib.suppress(RuntimeError):
                return resolve_camera_source(choice, known)
        return resolve_camera_source(wanted[-1], known)
    if text.isdigit():
        idx = int(text)
        return idx, named.get(idx)
    if text.startswith(("rtsp://", "http://", "https://")) or Path(text).exists():
        return text, None
    query = text.lower()
    allows_virtual = any(term in query for term in ("virtual", "desk", "screen"))
    candidates = [
        (idx, name) for idx, name in known if allows_virtual or not _is_virtual_video(name)
    ]
    for idx, name in candidates:
        if name.lower() == query:
            return idx, name
    for idx, name in candidates:
        if query in name.lower():
            return idx, name
    if known:
        available = ", ".join(f"{idx}:{name}" for idx, name in known)
        raise RuntimeError(f"camera {text!r} not found (available: {available})")
    raise RuntimeError(f"cannot resolve camera {text!r}: no video devices visible")


class LiveMic:
    """Non-blocking mono 16 kHz float32 stream from hardware microphone."""

    def __init__(
        self,
        device: str | None = None,
        sample_rate: int = 16000,
        *,
        strict: bool = False,
    ) -> None:
        resolved, device_name = resolve_audio_source(device, strict=strict)
        self.device = resolved
        self.device_name = device_name
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        self._chunks: deque[Array] = deque(maxlen=64)
        self._closed = False
        self._stream: Any = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self.frames_read = 0
        self.last_error: str | None = None
        self._capture_sr = sample_rate
        self._nonzero = False
        self._silence_warned = False
        self._start()

    def _start(self) -> None:
        dev: int | str | None = self.device
        try:
            sd: Any = importlib.import_module("sounddevice")
            capture_sr = self.sample_rate
            if self.device_name:
                target = self.device_name.lower()
                for idx, entry in enumerate(sd.query_devices()):
                    if int(entry.get("max_input_channels", 0)) <= 0:
                        continue
                    name = str(entry.get("name", "")).lower()
                    if name == target or target in name:
                        dev = idx
                        break
            elif isinstance(dev, str) and dev.isdigit():
                dev = int(dev)
            try:
                dev_info = sd.query_devices(dev)
                native_sr = round(float(dev_info.get("default_samplerate") or 0))
                if native_sr >= self.sample_rate:
                    capture_sr = native_sr
            except Exception:
                capture_sr = self.sample_rate
            self._capture_sr = capture_sr
            block = max(128, round(800 * capture_sr / self.sample_rate))
            self._stream = sd.InputStream(
                samplerate=capture_sr,
                channels=1,
                dtype="float32",
                device=dev,
                blocksize=block,
                callback=self._sd_callback,
            )
            self._stream.start()
            log.info(
                "live mic via sounddevice (%s @ %d Hz -> %d Hz)",
                self.device_name or dev or "default",
                capture_sr,
                self.sample_rate,
            )
            return
        except Exception as error:
            self.last_error = str(error)
            log.warning("sounddevice capture unavailable (%s); trying ffmpeg", error)
            self._stream = None
            self._start_ffmpeg()

    def _sd_callback(self, indata: Array, _frames: int, _t: Any, status: Any) -> None:
        if status:
            self.last_error = str(status)
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        if self._capture_sr != self.sample_rate and len(mono) > 1:
            target_len = max(1, round(len(mono) * self.sample_rate / self._capture_sr))
            x_old = np.linspace(0.0, 1.0, len(mono), endpoint=False, dtype=np.float64)
            x_new = np.linspace(0.0, 1.0, target_len, endpoint=False, dtype=np.float64)
            mono = np.interp(x_new, x_old, mono).astype(np.float32)
        if not self._nonzero:
            if float(np.max(np.abs(mono))) > 1e-6:
                self._nonzero = True
            elif not self._silence_warned and self.frames_read >= 25:
                self._silence_warned = True
                self.last_error = "microphone frames are all zeros (check macOS Mic permission)"
                log.warning("%s", self.last_error)
        with self._lock:
            self._chunks.append(mono)
            self.frames_read += 1

    def _start_ffmpeg(self) -> None:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            *_audio_source_args(self.device),
            "-ac",
            "1",
            "-ar",
            str(self.sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._thread = threading.Thread(target=self._read_pipe, name="live-mic-ffmpeg", daemon=True)
        self._thread.start()
        log.info("live mic via ffmpeg (%s @ %d Hz)", self.device or "default", self.sample_rate)

    def _read_pipe(self) -> None:
        byte_count = 800 * 4
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while not self._closed:
                raw = proc.stdout.read(byte_count)
                if not raw:
                    if proc.stderr is not None:
                        err = proc.stderr.read().decode(errors="replace").strip()
                        if err:
                            self.last_error = err
                    break
                if len(raw) % 4 != 0:
                    raw = raw[: len(raw) - (len(raw) % 4)]
                if not raw:
                    continue
                samples = np.frombuffer(raw, dtype="<f4").copy()
                with self._lock:
                    self._chunks.append(samples)
                    self.frames_read += 1
        except Exception as error:
            self.last_error = str(error)

    def read_chunk(self, n: int = 800) -> Array:
        with self._lock:
            if not self._chunks:
                return np.zeros(n, dtype=np.float32)
            head = self._chunks[0]
            if len(head) >= n:
                out = head[:n].copy()
                if len(head) == n:
                    self._chunks.popleft()
                else:
                    self._chunks[0] = head[n:]
                return out
            merged = np.concatenate(list(self._chunks))
            self._chunks.clear()
        if len(merged) <= n:
            padded = np.zeros(n, dtype=np.float32)
            padded[: len(merged)] = merged
            return padded
        with self._lock:
            self._chunks.appendleft(merged[n:])
        return np.asarray(merged[:n], dtype=np.float32)

    def close(self) -> None:
        self._closed = True
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
                self._proc.wait(timeout=1.5)
            if self._proc.poll() is None:
                with contextlib.suppress(Exception):
                    self._proc.kill()
                    self._proc.wait(timeout=1.0)


class LiveCamera:
    """Latest-frame camera reader using exact AVFoundation names, HTTP relay, or OpenCV."""

    def __init__(self, source: str | int = 0) -> None:
        import cv2

        self._cv2: Any = cv2
        resolved, device_name = resolve_camera_source(source)
        self.source: int | str = resolved
        self.device_name: str | None = device_name
        self._lock = threading.Lock()
        self._frame: Array | None = None
        self._jpeg: bytes | None = None
        self._cap: Any = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._closed = False
        self.frames_read = 0
        self.last_error: str | None = None

        src_text = str(resolved)
        if src_text.startswith(("http://", "https://")) and src_text.endswith(".jpg"):
            target = self._run_http
            log.info("live camera via HTTP relay (%s)", src_text)
        elif platform.system() == "Darwin" and (isinstance(resolved, int) or device_name):
            cam_spec = device_name or str(resolved)
            self._start_ffmpeg_camera(cam_spec)
            target = self._run_ffmpeg
        else:
            self._cap = cv2.VideoCapture(resolved)
            if not self._cap.isOpened():
                raise RuntimeError(
                    f"cannot open camera source {resolved!r} ({device_name or source})"
                )
            with contextlib.suppress(Exception):
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            log.info("live camera opened (%s -> %s)", source, device_name or resolved)
            target = self._run_cv2

        self._thread = threading.Thread(target=target, name="live-camera", daemon=True)
        self._thread.start()

    def _start_ffmpeg_camera(self, cam_spec: str) -> None:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-framerate",
            "30",
            "-video_size",
            "1280x720",
            "-i",
            cam_spec,
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "5",
            "-",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        log.info("live camera via ffmpeg (%s)", cam_spec)

    def _run_ffmpeg(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        buf = bytearray()
        try:
            while not self._closed:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    if proc.stderr is not None:
                        err = proc.stderr.read().decode(errors="replace").strip()
                        if err:
                            self.last_error = err
                    break
                buf.extend(chunk)
                latest: bytes | None = None
                while True:
                    soi = buf.find(b"\xff\xd8")
                    if soi < 0:
                        buf.clear()
                        break
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if eoi < 0:
                        if soi > 0:
                            del buf[:soi]
                        break
                    latest = bytes(buf[soi : eoi + 2])
                    del buf[: eoi + 2]
                if latest is not None:
                    with self._lock:
                        self._jpeg = latest
                        self._frame = None
                        self.frames_read += 1
        except Exception as error:
            self.last_error = str(error)

    def _run_http(self) -> None:
        url_str = str(self.source)
        while not self._closed:
            try:
                with urllib.request.urlopen(url_str, timeout=1.5) as response:
                    data = response.read()
                if data.startswith(b"\xff\xd8"):
                    with self._lock:
                        self._jpeg = data
                        self._frame = None
                        self.frames_read += 1
            except Exception as error:
                self.last_error = str(error)
            time.sleep(0.08)

    def _run_cv2(self) -> None:
        while not self._closed:
            ok, frame = self._cap.read()
            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._jpeg = None
                    self.frames_read += 1
            else:
                time.sleep(0.05)

    def read(self) -> Array | None:
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            jpg = self._jpeg if frame is None else None
        if frame is not None:
            return frame
        if jpg is None:
            return None
        decoded = self._cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), self._cv2.IMREAD_COLOR)
        if decoded is None:
            return None
        arr: Array = np.asarray(decoded, dtype=np.uint8)
        with self._lock:
            self._frame = arr
            return arr.copy()

    def jpeg(self, quality: int = 75) -> bytes | None:
        with self._lock:
            if self._jpeg is not None:
                return self._jpeg
            frame = None if self._frame is None else self._frame.copy()
        if frame is None:
            return None
        ok, buf = self._cv2.imencode(".jpg", frame, [self._cv2.IMWRITE_JPEG_QUALITY, quality])
        return bytes(buf) if ok else None

    def close(self) -> None:
        self._closed = True
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
                self._proc.wait(timeout=2)
            if self._proc.poll() is None:
                with contextlib.suppress(Exception):
                    self._proc.kill()
                    self._proc.wait(timeout=1)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.0)
        if self._cap is not None and not self._thread.is_alive():
            with contextlib.suppress(Exception):
                self._cap.release()


def main() -> int:
    print("audio inputs:")
    for item in list_audio_inputs():
        print(f"  {item}")
    print("video inputs:")
    devices = list_video_devices()
    if devices:
        for idx, name in devices:
            print(f"  {idx}: {name}")
    else:
        for item in describe_video_inputs():
            print(f"  {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
