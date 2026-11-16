from __future__ import annotations

import time
from typing import Any

import pytest

from ri.drone import MAX_ALTITUDE_M, WAKE_ALTITUDE_M, clamp_altitude
from ri.drone.codrone import CoDroneEdu
from ri.drone.sim import SimDrone
from ri.drone.tello import TelloDrone


class FakeCoDroneSDK:
    def __init__(self, *, height_m: float = 1.0, battery: int = 85) -> None:
        self.height_m = height_m
        self.battery = battery
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_telemetry = False

    def takeoff(self) -> None:
        self.calls.append(("takeoff", ()))

    def land(self) -> None:
        self.calls.append(("land", ()))

    def hover(self, seconds: float) -> None:
        self.calls.append(("hover", (seconds,)))

    def set_throttle(self, pct: int) -> None:
        self.calls.append(("set_throttle", (pct,)))

    def move(self, seconds: float) -> None:
        self.calls.append(("move", (seconds,)))

    def set_drone_LED(self, r: int, g: int, b: int, brightness: int) -> None:
        self.calls.append(("set_drone_LED", (r, g, b, brightness)))

    def emergency_stop(self) -> None:
        self.calls.append(("emergency_stop", ()))

    def close(self) -> None:
        self.calls.append(("close", ()))

    def get_height(self, _unit: str) -> float:
        if self.fail_telemetry:
            raise OSError("usb timeout")
        return self.height_m

    def get_battery(self) -> int:
        if self.fail_telemetry:
            raise OSError("usb timeout")
        return self.battery


class FakeTelloSDK:
    def __init__(self, *, height_cm: int = 100, battery: int = 78) -> None:
        self.height_cm = height_cm
        self.battery = battery
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_telemetry = False

    def takeoff(self) -> None:
        self.calls.append(("takeoff", ()))

    def land(self) -> None:
        self.calls.append(("land", ()))

    def send_rc_control(self, lr: int, fb: int, ud: int, yaw: int) -> None:
        self.calls.append(("send_rc_control", (lr, fb, ud, yaw)))

    def move_up(self, cm: int) -> None:
        self.calls.append(("move_up", (cm,)))

    def move_down(self, cm: int) -> None:
        self.calls.append(("move_down", (cm,)))

    def emergency(self) -> None:
        self.calls.append(("emergency", ()))

    def end(self) -> None:
        self.calls.append(("end", ()))

    def get_height(self) -> int:
        if self.fail_telemetry:
            raise OSError("udp timeout")
        return self.height_cm

    def get_battery(self) -> int:
        if self.fail_telemetry:
            raise OSError("udp timeout")
        return self.battery


def _wait_until(predicate: Any, timeout_s: float = 1.5) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for worker")


def test_clamp_altitude() -> None:
    assert clamp_altitude(0.1) == WAKE_ALTITUDE_M
    assert clamp_altitude(1.5) == 1.5
    assert clamp_altitude(4.0) == MAX_ALTITUDE_M


def test_codrone_adapter_clamps_hovers_caches_and_cuts() -> None:
    sdk = FakeCoDroneSDK(height_m=1.1, battery=88)
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(1.0)
        _wait_until(lambda: drone.state().phase == "hovering")
        drone.move_vertical(2.0)  # clamped to MAX_ALTITUDE_M (2.0)
        _wait_until(lambda: drone.state().phase == "hovering")
        assert drone.state().target_m == pytest.approx(MAX_ALTITUDE_M)

        # Transient telemetry glitch preserves cached battery & altitude.
        sdk.fail_telemetry = True
        st = drone.state()
        assert st.altitude_m == pytest.approx(1.1)
        assert st.battery == 88

        drone.set_leds("blue", "solid")
        drone.emergency()
        st = drone.state()
        assert st.phase == "parked"
        assert st.target_m == 0.0
        assert any(name == "emergency_stop" for name, _ in sdk.calls)
    finally:
        drone.close()


def test_tello_adapter_clamps_moves_and_cuts() -> None:
    sdk = FakeTelloSDK(height_cm=100, battery=74)
    drone = TelloDrone(sdk=sdk)
    try:
        drone.take_off(1.0)
        _wait_until(lambda: drone.state().phase == "hovering")
        drone.move_vertical(0.1)  # clipped up to MIN_MOVE_CM = 20 cm
        _wait_until(lambda: drone.state().phase == "hovering")
        assert ("move_up", (20,)) in sdk.calls

        sdk.fail_telemetry = True
        st = drone.state()
        assert st.altitude_m == pytest.approx(1.0)
        assert st.battery == 74

        drone.emergency()
        assert drone.state().phase == "parked"
        assert any(name == "emergency" for name, _ in sdk.calls)
    finally:
        drone.close()


def test_sim_drone_emergency_and_battery() -> None:
    sim = SimDrone(battery=42)
    sim.take_off(1.0)
    assert sim.state().phase in ("taking_off", "hovering")
    assert sim.state().battery == 42
    sim.emergency()
    st = sim.state()
    assert st.phase == "parked"
    assert st.altitude_m == 0.0
    assert st.target_m == 0.0
