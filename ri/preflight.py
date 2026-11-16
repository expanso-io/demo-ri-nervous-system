"""Hardware, model, bus, and battery preflight before a stage flight or take.

    uv run python -m ri.preflight [--drone sim|codrone|tello] [--input fixture|live]

Verifies local models, Expanso Edge binary, configured inputs, drone battery
(for real airframes or when connected), and that live MQTT topics carry only
tiny JSON decisions and telemetry (never audio samples or camera pixels).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import threading
import time
import urllib.request
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from ri.capture import (
    LiveCamera,
    describe_video_inputs,
    list_audio_inputs,
    resolve_audio_source,
    resolve_camera_source,
)
from ri.drone import make_adapter
from ri.ports import DEFAULT_CAMERA, DEFAULT_DRONE, DEFAULT_INPUT, DEFAULT_MIC, ROOT, ports

MIN_BATTERY_PCT = 60
FORBIDDEN_KEYS = frozenset({"audio", "pcm", "wav", "samples", "frame", "image", "jpeg", "pixels"})
MAX_MQTT_BYTES = 512
REQUIRED_MODELS = (
    ROOT / "models" / "yamnet.tflite",
    ROOT / "models" / "yamnet_class_map.csv",
    ROOT / "models" / "kws.pt",
    ROOT / "models" / "cache" / "yolo11n-pose.pt",
)


def _port_open(host: str, port_num: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port_num)) == 0


def check_models() -> tuple[bool, str]:
    missing = [
        str(p.relative_to(ROOT)) for p in REQUIRED_MODELS if not p.is_file() or not p.stat().st_size
    ]
    if missing:
        return False, f"missing models: {', '.join(missing)}"
    return True, f"all {len(REQUIRED_MODELS)} local models present"


def check_fixtures() -> tuple[bool, str]:
    audio_dir = ROOT / "fixtures" / "audio"
    wavs = (
        "applause.wav",
        *(f"word-{w}-{i}.wav" for w in ("up", "down", "stop") for i in (1, 2, 3)),
    )
    missing = [w for w in wavs if not (audio_dir / w).is_file()]
    ups = list((ROOT / "fixtures" / "build" / "people" / "up").glob("*.jpg"))
    downs = list((ROOT / "fixtures" / "build" / "people" / "down").glob("*.jpg"))
    if missing or len(ups) + len(downs) < 18:
        return (
            False,
            f"incomplete fixtures: missing_audio={missing}, people={len(ups)}+{len(downs)}",
        )
    return True, f"10 audio clips and {len(ups)} up + {len(downs)} down person tiles ready"


def _probe_camera(source: str) -> tuple[bool, str]:
    try:
        resolved, name = resolve_camera_source(source)
    except RuntimeError as error:
        return False, str(error)
    try:
        cam = LiveCamera(resolved)
    except Exception as error:
        return False, str(error)
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            frame = cam.read()
            if frame is not None:
                h, w = frame.shape[:2]
                label = name or str(resolved)
                return True, f"{resolved}:{label} ({w}x{h})"
            if cam.last_error:
                return False, f"camera {resolved} ({name or source}): {cam.last_error}"
            time.sleep(0.05)
        return False, f"camera {resolved} ({name or source}) yielded no frames"
    finally:
        cam.close()


def check_live_inputs(
    hands_device: str | None = None,
    stage_cam: str | None = None,
    *,
    check_audio: bool = True,
) -> tuple[bool, str]:
    parts: list[str] = []
    if check_audio:
        mics = list_audio_inputs()
        if not mics:
            return False, "no live audio input devices visible"
        preferred = (
            os.environ.get("RI_WORD_DEVICE") or os.environ.get("RI_CLAP_DEVICE") or DEFAULT_MIC
        )
        try:
            idx, mic_name = resolve_audio_source(preferred, mics)
        except RuntimeError as error:
            return False, f"audio: {error}"
        label = f"{idx}:{mic_name}" if mic_name else str(idx)
        if preferred.lower() == "dji" and mic_name and "dji" not in mic_name.lower():
            label += " (DJI mic not attached; fallback)"
        parts.append(f"audio={label}")
    if hands_device:
        ok, detail = _probe_camera(hands_device)
        if not ok:
            return False, f"hands camera: {detail}"
        parts.append(f"hands={detail}")
    else:
        cams = describe_video_inputs()
        if not cams:
            return False, "no live camera device readable"
        parts.append(f"cameras={cams}")
    if stage_cam and stage_cam.lower() not in ("none", "off", "false"):
        ok, detail = _probe_camera(stage_cam)
        if not ok:
            return False, f"stage camera: {detail}"
        parts.append(f"stage_cam={detail}")
    return True, ", ".join(parts)


def check_drone(adapter_name: str, min_battery: int) -> tuple[bool, str]:
    adapter = make_adapter(adapter_name)
    try:
        state = adapter.state()
    finally:
        adapter.close()
    if adapter_name != "sim":
        if state.battery is None:
            return False, f"{adapter_name}: telemetry returned battery=None"
        if state.battery < min_battery:
            return False, f"{adapter_name}: battery {state.battery}% < {min_battery}% minimum"
    bat = f"{state.battery}%" if state.battery is not None else "n/a"
    return True, f"{adapter_name} ({state.phase}, battery={bat})"


def sniff_mqtt(host: str, mqtt_port: int, duration_s: float = 1.5) -> tuple[bool, str]:
    seen = 0
    violations: list[str] = []
    connected = threading.Event()
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"ri-preflight-{os.getpid()}")

    def on_connect(cli: mqtt.Client, *_: Any) -> None:
        cli.subscribe("ri/#", qos=0)
        connected.set()

    def on_message(_cli: mqtt.Client, _ud: Any, msg: mqtt.MQTTMessage) -> None:
        nonlocal seen
        seen += 1
        size = len(msg.payload)
        if size > MAX_MQTT_BYTES:
            violations.append(f"{msg.topic} exceeded {MAX_MQTT_BYTES} B ({size} B)")
            return
        try:
            body = json.loads(msg.payload)
        except ValueError:
            violations.append(f"{msg.topic} emitted non-JSON payload")
            return
        if isinstance(body, dict):
            bad = FORBIDDEN_KEYS & set(body.keys())
            if bad:
                violations.append(f"{msg.topic} contains raw media keys: {sorted(bad)}")

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(host, mqtt_port, keepalive=10)
    except OSError as error:
        return False, f"MQTT unreachable on {host}:{mqtt_port}: {error}"
    client.loop_start()
    connected.wait(timeout=2.0)
    time.sleep(duration_s)
    client.loop_stop()
    client.disconnect()
    if violations:
        return False, "; ".join(violations[:3])
    return True, f"checked {seen} MQTT packets on {host}:{mqtt_port}; zero media payloads"


def run_preflight(
    drone: str,
    input_mode: str,
    *,
    min_battery: int = MIN_BATTERY_PCT,
    require_live_bus: bool = False,
    hands_input: str | None = None,
    hands_device: str | None = None,
    stage_cam: str | None = None,
) -> dict[str, Any]:
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".runtime" / "ultralytics"))
    eff_hands_input = hands_input or os.environ.get("RI_HANDS_INPUT") or input_mode
    eff_hands_device = (
        hands_device
        if hands_device is not None
        else (
            os.environ.get("RI_HANDS_DEVICE")
            or (DEFAULT_CAMERA if eff_hands_input == "live" else "")
        )
    )
    eff_stage_cam = (
        stage_cam
        if stage_cam is not None
        else (
            os.environ.get("RI_STAGE_CAM")
            or (DEFAULT_CAMERA if (input_mode == "live" or eff_hands_input == "live") else "none")
        )
    )
    results: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str, *, required: bool = True) -> None:
        results.append({"check": name, "ok": ok, "required": required, "detail": detail})

    record("models", *check_models())
    edge_bin = shutil.which("expanso-edge")
    record("expanso-edge", bool(edge_bin), edge_bin or "expanso-edge missing from PATH")
    if input_mode == "live":
        record(
            "live-inputs",
            *check_live_inputs(eff_hands_device or None, eff_stage_cam, check_audio=True),
        )
    else:
        record("fixture-inputs", *check_fixtures())
        if eff_hands_input == "live" or eff_stage_cam.lower() not in ("", "none", "off", "false"):
            record(
                "live-camera",
                *check_live_inputs(
                    eff_hands_device or (DEFAULT_CAMERA if eff_hands_input == "live" else None),
                    eff_stage_cam,
                    check_audio=False,
                ),
            )
    try:
        record("drone", *check_drone(drone, min_battery))
    except Exception as error:
        record("drone", False, str(error))

    p = ports()
    mqtt_host = os.environ.get("RI_MQTT_HOST", "127.0.0.1")
    bus_up = _port_open(mqtt_host, p["MQTT_PORT"])
    if bus_up or require_live_bus:
        record("mqtt-privacy", *sniff_mqtt(mqtt_host, p["MQTT_PORT"]))
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p['PORT']}/api/state", timeout=2) as r:
                snap = json.loads(r.read())
            record("screen", True, f"screen reachable (beat={snap.get('scene', {}).get('beat')})")
        except OSError as error:
            record("screen", False, f"screen check failed: {error}", required=require_live_bus)
    else:
        record("mqtt-privacy", True, "stack stopped; skipped live bus sniff", required=False)

    passed = all(item["ok"] or not item["required"] for item in results)
    return {"passed": passed, "drone": drone, "input": input_mode, "checks": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--drone",
        choices=("sim", "codrone", "tello"),
        default=os.environ.get("RI_DRONE", DEFAULT_DRONE),
    )
    ap.add_argument(
        "--input",
        dest="input_mode",
        choices=("fixture", "live"),
        default=os.environ.get("RI_INPUT", DEFAULT_INPUT),
    )
    ap.add_argument("--hands-input", choices=("", "fixture", "live"), default="")
    ap.add_argument("--hands-device", default=None)
    ap.add_argument("--stage-cam", default=None)
    ap.add_argument("--min-battery", type=int, default=MIN_BATTERY_PCT)
    ap.add_argument("--require-bus", action="store_true")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()
    report = run_preflight(
        args.drone,
        args.input_mode,
        min_battery=args.min_battery,
        require_live_bus=args.require_bus,
        hands_input=args.hands_input or None,
        hands_device=args.hands_device,
        stage_cam=args.stage_cam,
    )
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        for item in report["checks"]:
            tag = "PASS" if item["ok"] else ("WARN" if not item["required"] else "FAIL")
            print(f"[{tag}] {item['check']:16} {item['detail']}")
    with contextlib.suppress(Exception):
        return 0 if report["passed"] else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
