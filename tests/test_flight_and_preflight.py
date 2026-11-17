from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from ri import capture, preflight
from ri import device as device_mod
from ri import flight as flight_mod
from ri import stack as stack_mod
from ri.bus import ACT_TOPIC
from ri.drone import DroneState, Leds
from ri.drone.sim import SimDrone


class FakeBus:
    def __init__(self, client_id: str, subscriptions: tuple[str, ...] = ()) -> None:
        self.client_id = client_id
        self.subscriptions = subscriptions
        self.published: list[tuple[str, dict[str, Any]]] = []

    def start(self) -> FakeBus:
        return self

    def publish(self, topic: str, body: dict[str, Any]) -> None:
        self.published.append((topic, body))

    def stop(self) -> None:
        pass


class AirborneStubDrone:
    name = "codrone"

    def __init__(self, *, phase: str = "hovering", battery: int | None = 80) -> None:
        self.phase = phase
        self.battery = battery
        self.emergencies = 0
        self.lands = 0

    def take_off(self, _altitude_m: float) -> None:
        pass

    def land(self) -> None:
        self.lands += 1
        self.phase = "landing"

    def hover(self) -> None:
        pass

    def move_vertical(self, _dz_m: float) -> None:
        pass

    def set_leds(self, _color: str, _mode: str) -> None:
        pass

    def emergency(self) -> None:
        self.emergencies += 1
        self.phase = "parked"

    def close(self) -> None:
        pass

    def state(self) -> DroneState:
        return DroneState(
            adapter=self.name,
            phase=self.phase,  # type: ignore[arg-type]
            altitude_m=1.0 if self.phase == "hovering" else 0.0,
            target_m=1.0 if self.phase == "hovering" else 0.0,
            leds=Leds(),
            battery=self.battery,
            t=time.time(),
        )


def test_flight_kill_lands_and_cut_cuts_motors(monkeypatch: pytest.MonkeyPatch) -> None:
    buses: list[FakeBus] = []

    def make_bus(cid: str, subscriptions: tuple[str, ...] = ()) -> FakeBus:
        bus = FakeBus(cid, subscriptions)
        buses.append(bus)
        return bus

    monkeypatch.setattr(flight_mod, "Bus", make_bus)
    fl = flight_mod.Flight("sim")
    try:
        fl.drone.take_off(1.0)
        res = fl.kill(cut=False)
        assert res["act"] == "land"
        assert res["because"] == "presenter stop"
        assert buses[-1].published[-1][0] == ACT_TOPIC

        fl.drone.take_off(1.0)
        cut_res = fl.kill(cut=True)
        assert cut_res["because"] == "emergency motor cut"
        assert fl.drone.state().phase == "parked"
    finally:
        fl.shutdown()


def test_flight_reset_protects_airborne_real_drone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flight_mod, "Bus", FakeBus)
    stub = AirborneStubDrone(phase="hovering", battery=82)
    monkeypatch.setattr(flight_mod, "make_adapter", lambda _name: stub)

    fl = flight_mod.Flight("codrone")
    try:
        with pytest.raises(ValueError, match="airborne"):
            fl.reset()
        stub.phase = "parked"
        assert fl.reset() == {"reset": True}
    finally:
        fl.shutdown()


def test_word_device_clicker_cue_posts_valid_pcm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_mod, "Bus", FakeBus)
    dev = device_mod.WordDevice(input_mode="fixture")
    posted = threading.Event()
    payloads: list[tuple[bytes, str]] = []

    def capture_post(body: bytes, content_type: str) -> None:
        payloads.append((body, content_type))
        posted.set()

    monkeypatch.setattr(dev, "post", capture_post)
    try:
        assert dev.cue({"click": "up"}) == {"clicked": "up"}
        assert posted.wait(timeout=1.5)
        body, ctype = payloads[0]
        assert ctype == "application/octet-stream"
        assert len(body) == device_mod.SR * 2
        with pytest.raises(ValueError, match="unknown clicker command"):
            dev.cue({"click": "sideways"})
    finally:
        dev.close()


def test_preflight_local_and_battery_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    ok_models, _ = preflight.check_models()
    ok_fixtures, _ = preflight.check_fixtures()
    ok_sim, sim_detail = preflight.check_drone("sim", 60)
    assert ok_models
    assert ok_fixtures
    assert ok_sim
    assert "sim" in sim_detail

    monkeypatch.setattr(preflight, "make_adapter", lambda _n: AirborneStubDrone(battery=45))
    ok_low, low_detail = preflight.check_drone("codrone", 60)
    assert not ok_low
    assert "45%" in low_detail

    monkeypatch.setattr(preflight, "make_adapter", lambda _n: SimDrone(battery=85))
    ok_high, _ = preflight.check_drone("codrone", 60)
    assert ok_high


def test_resolve_camera_source_matches_name_and_errors_cleanly(tmp_path: Any) -> None:
    devices = [
        (0, "MacBook Pro Camera"),
        (1, "OBS Virtual Camera"),
        (3, "OBSBOT Tiny 2 Lite StreamCamera"),
    ]
    assert capture.resolve_camera_source("OBSBOT Tiny 2 Lite", devices) == (
        3,
        "OBSBOT Tiny 2 Lite StreamCamera",
    )
    assert capture.resolve_camera_source("OBS", devices) == (
        3,
        "OBSBOT Tiny 2 Lite StreamCamera",
    )
    assert capture.resolve_camera_source("OBS Virtual Camera", devices) == (
        1,
        "OBS Virtual Camera",
    )
    assert capture.resolve_camera_source("1", devices) == (1, "OBS Virtual Camera")
    assert capture.resolve_camera_source("rtsp://127.0.0.1:8554/stage", devices) == (
        "rtsp://127.0.0.1:8554/stage",
        None,
    )

    sample = tmp_path / "clip.mp4"
    sample.write_bytes(b"")
    assert capture.resolve_camera_source(str(sample), devices) == (str(sample), None)

    with pytest.raises(RuntimeError, match="OBSBOT Tiny 2 Lite StreamCamera"):
        capture.resolve_camera_source("Missing Cam", devices)

    sysfs = tmp_path / "video4linux"
    (sysfs / "video2").mkdir(parents=True)
    (sysfs / "video0").mkdir(parents=True)
    (sysfs / "video2" / "name").write_text("OBSBOT Tiny 2 Lite\n")
    (sysfs / "video0" / "name").write_text("USB Camera\n")
    assert capture._linux_video_devices(sysfs) == [
        (0, "USB Camera"),
        (2, "OBSBOT Tiny 2 Lite"),
    ]


def test_resolve_audio_source_and_shared_stage_cam(monkeypatch: pytest.MonkeyPatch) -> None:
    mics_without_dji = [
        "0:ZoomAudioDevice",
        "1:OBSBOT Tiny 2 Lite Microphone",
        "2:MacBook Pro Microphone",
        "3:Microsoft Teams Audio",
    ]
    assert capture.resolve_audio_source("dji", mics_without_dji) == (
        "2",
        "MacBook Pro Microphone",
    )
    mics_with_dji = [*mics_without_dji, "4:DJI Mic 2"]
    assert capture.resolve_audio_source("dji", mics_with_dji) == ("4", "DJI Mic 2")
    assert capture.resolve_audio_source("auto", mics_with_dji) == ("4", "DJI Mic 2")

    monkeypatch.setattr(
        stack_mod,
        "resolve_camera_source",
        lambda _s: (3, "OBSBOT Tiny 2 Lite StreamCamera"),
    )
    relayed = stack_mod.screen_environment(
        {
            "RI_HANDS_INPUT": "live",
            "RI_HANDS_DEVICE": "OBSBOT Tiny 2 Lite",
            "RI_STAGE_CAM": "OBSBOT Tiny 2 Lite",
            "RI_HANDS_DEVICE_PORT": "20181",
        }
    )
    assert relayed["RI_STAGE_CAM"] == "http://127.0.0.1:20181/preview.jpg"
    assert relayed["RI_STAGE_CAM_NAME"] == "OBSBOT Tiny 2 Lite StreamCamera"


def test_stack_env_and_preflight_named_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    parser_args = type(
        "Args",
        (),
        {
            "drone": "sim",
            "input_mode": "fixture",
            "stage_cam": "OBSBOT Tiny 2 Lite",
            "hands_roi": "0.1,0.2,0.8,0.7",
            "hands_input": "live",
            "hands_device": "OBSBOT Tiny 2 Lite",
            "clap_device": "MacBook Pro Microphone",
            "word_device": "AB13X USB Audio",
            "mqtt_bind": "0.0.0.0",
        },
    )()
    env = stack_mod.environment(parser_args)
    assert env["RI_STAGE_CAM"] == "OBSBOT Tiny 2 Lite"
    assert env["RI_HANDS_INPUT"] == "live"
    assert env["RI_HANDS_DEVICE"] == "OBSBOT Tiny 2 Lite"
    assert env["RI_CLAP_DEVICE"] == "MacBook Pro Microphone"
    assert env["RI_WORD_DEVICE"] == "AB13X USB Audio"
    assert env["RI_MQTT_BIND"] == "0.0.0.0"

    seen_cameras: list[str] = []

    def fake_probe(source: str) -> tuple[bool, str]:
        seen_cameras.append(source)
        return True, f"3:{source} (1920x1080)"

    monkeypatch.setattr(preflight, "_probe_camera", fake_probe)
    report = preflight.run_preflight(
        "sim",
        "fixture",
        hands_input="live",
        hands_device="OBSBOT Tiny 2 Lite",
        stage_cam="OBSBOT Tiny 2 Lite",
    )
    assert report["passed"]
    assert seen_cameras == ["OBSBOT Tiny 2 Lite", "OBSBOT Tiny 2 Lite"]
    cam_check = next(c for c in report["checks"] if c["check"] == "live-camera")
    assert "hands=3:OBSBOT Tiny 2 Lite (1920x1080)" in cam_check["detail"]
    assert "stage_cam=3:OBSBOT Tiny 2 Lite (1920x1080)" in cam_check["detail"]


def test_strict_mic_resolution_and_live_device_swap(monkeypatch: pytest.MonkeyPatch) -> None:
    mics_no_dji = ["0:ZoomAudioDevice", "2:MacBook Pro Microphone"]
    assert capture.resolve_audio_source("macbook", mics_no_dji, strict=True) == (
        "2",
        "MacBook Pro Microphone",
    )
    with pytest.raises(RuntimeError, match="not found"):
        capture.resolve_audio_source("dji", mics_no_dji, strict=True)

    mics_with_dji = [*mics_no_dji, "4:DJI Mic Mini"]
    assert capture.resolve_audio_source("dji", mics_with_dji, strict=True) == ("4", "DJI Mic Mini")
    monkeypatch.setattr(capture, "list_audio_inputs", lambda: mics_with_dji)
    assert capture.list_physical_audio_inputs() == ["2:MacBook Pro Microphone", "4:DJI Mic Mini"]

    opened: list[str] = []
    closed: list[str] = []

    class FakeMic:
        def __init__(self, device: str | None = None, _sr: int = 16000, *, strict: bool = False):
            assert strict
            if device == "dji" and not dji_attached["on"]:
                raise RuntimeError("audio device 'dji' not found")
            self.device_name = "DJI Mic Mini" if device == "dji" else "MacBook Pro Microphone"
            opened.append(self.device_name)

        def read_chunk(self, n: int) -> Any:
            import numpy as np

            return np.zeros(n, dtype=np.float32)

        def close(self) -> None:
            closed.append(self.device_name)

    dji_attached = {"on": True}
    monkeypatch.setattr(device_mod, "Bus", FakeBus)
    monkeypatch.setattr(device_mod, "LiveMic", FakeMic)

    dev = device_mod.AudioDevice("word", input_mode="fixture")
    assert dev.set_source("macbook") == {"input": "live", "mic": "MacBook Pro Microphone"}
    assert dev.telemetry()["mic"] == "MacBook Pro Microphone"

    assert dev.set_source("dji") == {"input": "live", "mic": "DJI Mic Mini"}
    assert closed == ["MacBook Pro Microphone"]
    assert dev.telemetry()["mic"] == "DJI Mic Mini"

    dji_attached["on"] = False
    assert dev.set_source("macbook") == {"input": "live", "mic": "MacBook Pro Microphone"}
    with pytest.raises(RuntimeError, match="not found"):
        dev.set_source("dji")
    assert dev.telemetry()["mic"] == "MacBook Pro Microphone"

    assert dev.set_source("fixture", input_mode="fixture") == {"input": "fixture", "mic": None}
    assert dev.telemetry()["input"] == "fixture"
    assert opened == ["MacBook Pro Microphone", "DJI Mic Mini", "MacBook Pro Microphone"]


def test_default_live_hardware_and_parameter_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "RI_DRONE",
        "RI_INPUT",
        "RI_STAGE_CAM",
        "RI_HANDS_INPUT",
        "RI_HANDS_DEVICE",
        "RI_CLAP_DEVICE",
        "RI_WORD_DEVICE",
    ):
        monkeypatch.delenv(var, raising=False)

    default_args = type("Args", (), {"drone": None, "input_mode": None})()
    live_env = stack_mod.environment(default_args)
    assert live_env["RI_DRONE"] == "codrone"
    assert live_env["RI_INPUT"] == "live"
    assert live_env["RI_STAGE_CAM"] == "OBSBOT Tiny 2 Lite"
    assert live_env["RI_HANDS_DEVICE"] == "OBSBOT Tiny 2 Lite"
    assert live_env["RI_CLAP_DEVICE"] == "dji"
    assert live_env["RI_WORD_DEVICE"] == "dji"

    sim_args = type("Args", (), {"drone": "sim", "input_mode": "fixture"})()
    sim_env = stack_mod.environment(sim_args)
    assert sim_env["RI_DRONE"] == "sim"
    assert sim_env["RI_INPUT"] == "fixture"
    assert sim_env["RI_STAGE_CAM"] == "none"
    assert "RI_HANDS_DEVICE" not in sim_env


def test_after_the_presenters_stop_nothing_wakes_the_drone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flight_mod, "Bus", FakeBus)
    fl = flight_mod.Flight("sim")
    try:
        fl.drone.take_off(1.0)
        fl.kill(cut=False)
        clap = json.dumps({"sense": "clap", "confidence": 0.99, "t": time.time(), "seq": 1})
        assert fl.decide(clap.encode())["because"] == "presenter stop"
        # Once the quiet spell is over, that same clap is spent, and a new one wakes it.
        fl.quiet_until = 0.0
        fl.drone.emergency()
        assert fl.decide(clap.encode())["act"] == "hold"
        fresh = json.dumps({"sense": "clap", "confidence": 0.99, "t": time.time(), "seq": 2})
        assert fl.decide(fresh.encode())["act"] == "wake"
    finally:
        fl.shutdown()


def _rig(**over: Any) -> dict[str, Any]:
    mic = {"mic": "laptop", "captured_bytes": 100, "level": 0.2, "wave": [3, 9]}
    state: dict[str, Any] = {
        "nodes": {
            n: {"live": True} for n in ("hear-clap", "hear-commands", "see-hands", "fly-drone")
        },
        "devices": {
            "hear-clap": dict(mic),
            "hear-commands": dict(mic),
            "see-hands": {"captured_bytes": 100, "arms": True},
        },
        "latest": {"hands": {"seen": 1}},
        "drone": {"phase": "parked", "altitude_m": 0.0, "battery": 80},
    }
    state.update(over)
    return state


def _later(state: dict[str, Any]) -> dict[str, Any]:
    after: dict[str, Any] = json.loads(json.dumps(state))
    for device in after["devices"].values():
        device["captured_bytes"] += 500
    return after


def _failed(first: dict[str, Any], **kw: Any) -> list[str]:
    from ri.ready import judge

    checks = judge(
        first, _later(first), stale_code=kw.get("stale", False), disk_free=kw.get("disk", 0.5)
    )
    return [c.name for c in checks if not c.ok]


def test_ready_passes_a_healthy_rig_and_names_each_thing_that_is_wrong() -> None:
    assert _failed(_rig()) == []
    assert _failed(_rig(), stale=True) == ["code"]
    assert _failed(_rig(), disk=0.01) == ["disk"]
    # The two failures seen on 2026-11-16: a silent microphone, and a rig that
    # believed a drone on the floor was hovering.
    silent = _rig()
    silent["devices"]["hear-clap"].update(level=0.0, wave=[0, 0])
    assert _failed(silent) == ["mic hear-clap"]
    assert _failed(_rig(drone={"phase": "hovering", "altitude_m": 0.0, "battery": 80})) == ["drone"]
    assert _failed(_rig(drone={"phase": "parked", "altitude_m": 0.0, "battery": 20})) == ["battery"]
    chair = _rig()
    chair["devices"]["see-hands"]["arms"] = False
    assert _failed(chair) == ["camera"]
    off = _rig()
    off["nodes"]["fly-drone"]["live"] = False
    assert _failed(off) == ["pipelines"]
