from __future__ import annotations

import time
from typing import Any

import pytest

from ri.drone import MAX_ALTITUDE_M, WAKE_ALTITUDE_M, clamp_altitude
from ri.drone.codrone import CoDroneEdu
from ri.drone.sim import SimDrone
from ri.drone.tello import TelloDrone


class FakeCoDroneSDK:
    def __init__(
        self, *, height_m: float = 1.0, battery: int = 85, lands_to: float | None = None
    ) -> None:
        self.height_m = height_m
        self.lands_to = lands_to
        self.battery = battery
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_telemetry = False
        self.throttle = 0
        self.lowest_m = height_m

    def takeoff(self) -> None:
        self.calls.append(("takeoff", ()))

    def land(self) -> None:
        self.calls.append(("land", ()))
        if self.lands_to is not None:
            self.height_m = self.lands_to

    def hover(self, seconds: float) -> None:
        self.calls.append(("hover", (seconds,)))

    def set_throttle(self, pct: int) -> None:
        self.calls.append(("set_throttle", (pct,)))
        self.throttle = pct

    def move(self, seconds: float) -> None:
        self.calls.append(("move", (seconds,)))
        # Six times livelier than the airframe measured (throttle 30 was
        # 5 cm a second), so a loop that is steady here is steady there.
        self.height_m += self.throttle / 100 * seconds
        self.lowest_m = min(self.lowest_m, self.height_m)

    def sendControlPosition(
        self, x: float, y: float, z: float, speed: float, heading: int, turn: int
    ) -> None:
        # The airframe is sent the move a few times over; it flies it once.
        if self.calls and self.calls[-1] == ("position", (x, y, z, speed)):
            return
        self.calls.append(("position", (x, y, z, speed)))
        self.height_m += z

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


def _wait_until(predicate: Any, timeout_s: float = 8.0) -> None:
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
        drone.move_vertical(2.0)  # clamped to MAX_ALTITUDE_M
        _wait_until(lambda: drone.state().phase == "hovering")
        assert drone.state().target_m == pytest.approx(MAX_ALTITUDE_M)

        # The climb is asked for only as far as the measured ceiling.
        reached = drone.state().altitude_m
        assert reached == pytest.approx(MAX_ALTITUDE_M, abs=0.05)
        assert reached <= MAX_ALTITUDE_M + 0.05

        # Transient telemetry glitch preserves cached battery & altitude.
        sdk.fail_telemetry = True
        st = drone.state()
        assert st.altitude_m == pytest.approx(reached)
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


def test_codrone_settles_to_the_wake_height_and_flies_a_leg_as_one_diagonal() -> None:
    from ri.route import MOVE

    # The airframe takes off to its own height; the rig brings it down to ours.
    sdk = FakeCoDroneSDK(height_m=0.9)
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(WAKE_ALTITUDE_M)
        _wait_until(lambda: drone.state().phase == "hovering")
        assert drone.state().altitude_m == pytest.approx(WAKE_ALTITUDE_M, abs=0.05)
        # It comes down on the measured height and never dips under the goal.
        assert sdk.lowest_m >= WAKE_ALTITUDE_M - 0.05
        drone.move_vertical(0.1)
        _wait_until(lambda: drone.state().phase == "hovering")
        assert drone.state().target_m == pytest.approx(WAKE_ALTITUDE_M + 0.1)

        drone.move_horizontal(*MOVE["left"])
        _wait_until(lambda: any(a[2] == 0 for name, a in sdk.calls if name == "position"))
        ahead, left, up, speed = next(
            a for name, a in sdk.calls if name == "position" and a[2] == 0
        )
        # Height is never flown as a position move: only the leg is.
        assert not [a for name, a in sdk.calls if name == "position" and a[2] != 0]
        assert sdk.throttle == 0
        assert (ahead, left) == pytest.approx((0.7071, 0.7071), abs=1e-4)
        assert ahead**2 + left**2 == pytest.approx(1.0)
        assert (up, speed) == (0, 0.5)
    finally:
        drone.close()


def test_codrone_cuts_its_motors_only_once_it_is_on_the_ground() -> None:
    grounded = FakeCoDroneSDK(height_m=0.9, lands_to=0.05)
    drone = CoDroneEdu(sdk=grounded, settle_s=0.3)
    try:
        drone.take_off(1.0)
        _wait_until(lambda: drone.state().phase == "hovering")
        drone.land()
        _wait_until(lambda: drone.state().phase == "parked")
        names = [name for name, _ in grounded.calls]
        assert names.index("emergency_stop") > names.index("land")
    finally:
        drone.close()

    aloft = FakeCoDroneSDK(height_m=0.9)
    drone = CoDroneEdu(sdk=aloft, settle_s=0.3)
    try:
        drone.take_off(1.0)
        _wait_until(lambda: drone.state().phase == "hovering")
        drone.land()
        _wait_until(lambda: drone.state().phase == "parked")
        assert not any(name == "emergency_stop" for name, _ in aloft.calls)
    finally:
        drone.close()


def test_codrone_on_the_floor_stops_claiming_to_hover() -> None:
    sdk = FakeCoDroneSDK(height_m=1.0)
    drone = CoDroneEdu(sdk=sdk, grounded_s=0.2)
    try:
        drone.take_off(1.0)
        _wait_until(lambda: drone.state().phase == "hovering")
        time.sleep(0.3)
        assert drone.state().phase == "hovering"
        sdk.height_m = 0.0  # picked up, unplugged, or down on a low battery
        _wait_until(lambda: drone.state().phase == "parked")
        assert drone.state().target_m == 0.0
        assert not drone.state().airborne
    finally:
        drone.close()


def test_codrone_that_does_not_lift_is_parked_not_hovering() -> None:
    sdk = FakeCoDroneSDK(height_m=0.0)
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(WAKE_ALTITUDE_M)
        _wait_until(lambda: drone.state().phase == "parked")
        assert not any(name == "set_throttle" for name, _ in sdk.calls)
        assert drone.state().target_m == 0.0
    finally:
        drone.close()


def test_codrone_ignores_a_height_the_drone_stopped_sending() -> None:
    class SilentSDK(FakeCoDroneSDK):
        """The real SDK repeats its last reading for ever once the drone is off."""

        def __init__(self) -> None:
            super().__init__(height_m=1.7, lands_to=1.7)
            self.timeStartProgram = time.time() - 60.0
            self.range_data = [5.0, 0, 1700]

    sdk = SilentSDK()
    drone = CoDroneEdu(sdk=sdk, settle_s=30.0)
    try:
        # A wake does not fly on a reading that is a minute old.
        drone.take_off(WAKE_ALTITUDE_M)
        _wait_until(lambda: drone.state().phase == "parked")
        assert not any(name == "set_throttle" for name, _ in sdk.calls)
        # A parked drone is shown on the floor, not at its last reading.
        assert drone.state().altitude_m == 0.0
        # A stop does not wait out the whole settle time on a silent drone.
        began = time.monotonic()
        drone.land()
        _wait_until(lambda: any(n == "land" for n, _ in sdk.calls[-3:]))
        _wait_until(lambda: drone.state().phase == "parked")
        assert time.monotonic() - began < 6.0
        assert not any(name == "emergency_stop" for name, _ in sdk.calls)
    finally:
        drone.close()


def test_codrone_gives_up_a_height_move_when_the_reading_goes() -> None:
    sdk = FakeCoDroneSDK(height_m=0.9)
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(0.9)
        _wait_until(lambda: drone.state().phase == "hovering")
        sdk.fail_telemetry = True
        drone.move_vertical(0.3)
        _wait_until(lambda: drone.state().phase == "hovering")
        assert sdk.throttle == 0
        assert sdk.height_m == pytest.approx(0.9, abs=0.05)
    finally:
        drone.close()


def test_codrone_leans_back_over_the_pad_while_it_finds_its_height() -> None:
    class DriftingSDK(FakeCoDroneSDK):
        """Comes off the pad 0.3 m ahead and 0.2 m to the left of it."""

        def __init__(self) -> None:
            super().__init__(height_m=0.9)
            self.timeStartProgram = time.time()
            self.position_data = [0.0, 0.3, 0.2, 0.0]
            self.roll = 0
            self.pitch = 0

        def set_roll(self, pct: int) -> None:
            self.roll = pct

        def set_pitch(self, pct: int) -> None:
            self.pitch = pct

        def get_position_data(self) -> list[float]:
            self.position_data[0] = time.time() - self.timeStartProgram
            return self.position_data

        def move(self, seconds: float) -> None:
            super().move(seconds)
            # Pitch carries it ahead, roll carries it right (so less left).
            self.position_data[1] += self.pitch / 100 * seconds * 5
            self.position_data[2] -= self.roll / 100 * seconds * 5

    sdk = DriftingSDK()
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(WAKE_ALTITUDE_M)
        _wait_until(lambda: drone.state().phase == "hovering")
        assert abs(sdk.position_data[1]) < 0.1
        assert abs(sdk.position_data[2]) < 0.1
        assert (sdk.roll, sdk.pitch, sdk.throttle) == (0, 0, 0)
        assert drone.state().altitude_m == pytest.approx(WAKE_ALTITUDE_M, abs=0.05)
    finally:
        drone.close()


def test_codrone_stops_steering_sideways_when_steering_makes_it_worse() -> None:
    class RunawaySDK(FakeCoDroneSDK):
        """A floor the position sensor cannot read: the reading runs away."""

        def __init__(self) -> None:
            super().__init__(height_m=0.9)
            self.timeStartProgram = time.time()
            self.position_data = [0.0, 0.1, 0.0, 0.0]
            self.tilts: list[tuple[int, int]] = []
            self.roll = 0
            self.pitch = 0

        def set_roll(self, pct: int) -> None:
            self.roll = pct

        def set_pitch(self, pct: int) -> None:
            self.pitch = pct
            self.tilts.append((self.roll, pct))

        def get_position_data(self) -> list[float]:
            self.position_data[0] = time.time() - self.timeStartProgram
            self.position_data[1] += 0.05
            return self.position_data

    sdk = RunawaySDK()
    drone = CoDroneEdu(sdk=sdk, fence_m=1e9)
    try:
        drone.take_off(WAKE_ALTITUDE_M)
        _wait_until(lambda: drone.state().phase == "hovering")
        # It leaned for a while, then gave up leaning and still found its height.
        assert any(tilt != (0, 0) for tilt in sdk.tilts)
        assert sdk.tilts[-1] == (0, 0)
        assert drone.state().altitude_m == pytest.approx(WAKE_ALTITUDE_M, abs=0.05)
    finally:
        drone.close()


def test_codrone_zeroes_its_gyro_only_when_asked() -> None:
    class GyroSDK(FakeCoDroneSDK):
        def reset_gyro(self) -> None:
            self.calls.append(("reset_gyro", ()))

    sdk = GyroSDK(height_m=0.9)
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(WAKE_ALTITUDE_M)
        _wait_until(lambda: drone.state().phase == "hovering")
        assert not any(name == "reset_gyro" for name, _ in sdk.calls)
        assert drone.level()
        assert any(name == "reset_gyro" for name, _ in sdk.calls)
    finally:
        drone.close()


def test_codrone_shows_down_as_soon_as_it_is_down_and_lands_once_per_stop() -> None:
    import threading

    class SlowLandSDK(FakeCoDroneSDK):
        """Like the real SDK, land() does not return for seconds."""

        def __init__(self) -> None:
            super().__init__(height_m=0.9)
            self.release = threading.Event()

        def land(self) -> None:
            self.calls.append(("land", ()))
            self.height_m = 0.02
            self.release.wait(10.0)

    sdk = SlowLandSDK()
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(0.9)
        _wait_until(lambda: drone.state().phase == "hovering")
        began = time.monotonic()
        for _ in range(6):  # a presenter leaning on Esc
            drone.land()
        assert drone.state().phase == "landing"
        _wait_until(lambda: drone.state().phase == "parked")
        assert time.monotonic() - began < 3.0
        assert not sdk.release.is_set()  # shown as down while the SDK still waits
        sdk.release.set()
        time.sleep(0.5)
        assert [name for name, _ in sdk.calls].count("land") == 1
        assert drone.state().phase == "parked"
        # Once that landing is over, a new stop sends a new one.
        drone.land()
        _wait_until(lambda: [name for name, _ in sdk.calls].count("land") == 2)
    finally:
        sdk.release.set()
        drone.close()


class PositionedSDK(FakeCoDroneSDK):
    """Reports where it is and, like the airframe, whether it is flying."""

    def __init__(self, *, height_m: float = 0.9) -> None:
        super().__init__(height_m=height_m)
        self.timeStartProgram = time.time()
        self.position_data = [0.0, 0.0, 0.0, 0.0]
        self.state_data: list[Any] = [0.0, None, None]
        self.mode = "Flight"
        self.deaf = False

    def get_position_data(self) -> list[float]:
        self.position_data[0] = time.time() - self.timeStartProgram
        return self.position_data

    def get_state_data(self) -> list[Any]:
        class Mode:
            name = self.mode

        self.state_data[0] = time.time() - self.timeStartProgram
        self.state_data[2] = Mode()
        return self.state_data

    def set_roll(self, pct: int) -> None:
        pass

    def set_pitch(self, pct: int) -> None:
        pass

    def move(self, seconds: float) -> None:
        if self.deaf:
            self.calls.append(("move", (seconds,)))
            return
        super().move(seconds)


def test_codrone_lands_when_it_is_past_the_fence_whatever_it_was_told() -> None:
    sdk = PositionedSDK()
    drone = CoDroneEdu(sdk=sdk, fence_m=1.35)
    try:
        drone.take_off(0.9)
        _wait_until(lambda: drone.state().phase == "hovering")
        sdk.position_data[1] = 1.2  # inside the fence: carries on
        assert drone.state().phase == "hovering"
        sdk.position_data[1], sdk.position_data[2] = 1.2, 0.9  # 1.5 m out
        _wait_until(lambda: drone.state().phase in ("landing", "parked"))
        _wait_until(lambda: any(name == "land" for name, _ in sdk.calls))
    finally:
        drone.close()


def test_codrone_stops_climbing_when_the_height_does_not_answer() -> None:
    # Over a bed the sensor reads the bed: the reading stays low however hard it climbs.
    sdk = PositionedSDK(height_m=0.4)
    drone = CoDroneEdu(sdk=sdk, fence_m=5.0)
    try:
        drone.take_off(0.4)
        _wait_until(lambda: drone.state().phase == "hovering")
        sdk.deaf = True
        sdk.calls.clear()
        began = time.monotonic()
        drone.move_vertical(0.5)
        _wait_until(lambda: any(name == "hover" for name, _ in sdk.calls))
        assert time.monotonic() - began < 4.0
        assert sdk.throttle == 0
    finally:
        drone.close()


def test_codrone_low_reading_does_not_park_a_drone_that_says_it_is_flying() -> None:
    sdk = PositionedSDK()
    drone = CoDroneEdu(sdk=sdk, grounded_s=0.2, fence_m=5.0)
    try:
        drone.take_off(0.9)
        _wait_until(lambda: drone.state().phase == "hovering")
        sdk.height_m = 0.05  # it has drifted over a bed
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            assert drone.state().phase == "hovering"
            time.sleep(0.02)
        sdk.mode = "Ready"  # now it really is down
        _wait_until(lambda: drone.state().phase == "parked")
    finally:
        drone.close()


def test_the_fence_is_just_past_the_end_of_the_route() -> None:
    from ri.room import ROOMS

    assert ROOMS["small"].fence_m == pytest.approx(1.35)
    assert ROOMS["large"].fence_m == pytest.approx(1.91)


def test_codrone_stops_waiting_for_take_off_the_moment_it_is_flying() -> None:
    class QuickSDK(PositionedSDK):
        def __init__(self) -> None:
            super().__init__()
            self.mode = "Ready"
            self.sent = 0

        def reset_move_values(self) -> None:
            self.calls.append(("reset_move_values", ()))

        def sendTakeOff(self) -> None:
            self.sent += 1
            if self.sent == 2:
                self.mode = "TakeOff"
                self.flying_at = time.monotonic() + 0.3

        def get_state_data(self) -> list[Any]:
            if self.mode == "TakeOff" and time.monotonic() >= self.flying_at:
                self.mode = "Flight"
            return super().get_state_data()

    sdk = QuickSDK()
    drone = CoDroneEdu(sdk=sdk, fence_m=5.0)
    try:
        began = time.monotonic()
        drone.take_off(0.9)
        _wait_until(lambda: drone.state().phase == "hovering")
        # Nowhere near the SDK's flat four-second wait, and its take-off is not used.
        assert time.monotonic() - began < 3.0
        assert not any(name == "takeoff" for name, _ in sdk.calls)
        assert sdk.sent == 2
    finally:
        drone.close()


class SteeredSDK(PositionedSDK):
    """Slides the way it is tilted, and overshoots every leg by half again."""

    def __init__(self, *, height_m: float = 0.9) -> None:
        super().__init__(height_m=height_m)
        self.roll = 0
        self.pitch = 0

    def set_roll(self, pct: int) -> None:
        self.roll = pct

    def set_pitch(self, pct: int) -> None:
        self.pitch = pct

    def move(self, seconds: float) -> None:
        super().move(seconds)
        self.position_data[1] += self.pitch / 100 * seconds * 4
        self.position_data[2] -= self.roll / 100 * seconds * 4

    def sendControlPosition(
        self, x: float, y: float, z: float, speed: float, heading: int, turn: int
    ) -> None:
        if self.calls and self.calls[-1] == ("position", (x, y, z, speed)):
            return
        super().sendControlPosition(x, y, z, speed, heading, turn)
        self.position_data[1] += x * 1.5
        self.position_data[2] += y * 1.5


def _off(sdk: PositionedSDK, ahead: float, left: float) -> float:
    return float(((sdk.position_data[1] - ahead) ** 2 + (sdk.position_data[2] - left) ** 2) ** 0.5)


def test_codrone_leans_a_hovering_drone_back_over_its_spot() -> None:
    sdk = SteeredSDK()
    drone = CoDroneEdu(sdk=sdk, fence_m=5.0)
    try:
        drone.take_off(0.9)
        _wait_until(lambda: drone.state().phase == "hovering")
        sdk.position_data[1], sdk.position_data[2] = 0.4, -0.3  # it drifts while it hovers
        _wait_until(lambda: _off(sdk, 0.0, 0.0) < 0.08)
        assert drone.state().phase == "hovering"
    finally:
        drone.close()


def test_a_leg_that_overshoots_is_stopped_and_brought_back_to_its_fixed_end() -> None:
    sdk = SteeredSDK()
    drone = CoDroneEdu(sdk=sdk, fence_m=5.0)
    try:
        drone.take_off(0.9)
        _wait_until(lambda: drone.state().phase == "hovering")
        began = time.time()
        drone.move_horizontal(0.42, -0.42)  # ahead and to the left
        _wait_until(lambda: any(name == "position" for name, _ in sdk.calls))
        _wait_until(lambda: drone.state().phase == "hovering")
        # Cut off on the measured distance, well inside the 2.2 s the leg is allowed.
        assert time.time() - began < 1.5
        _wait_until(lambda: _off(sdk, 0.42, 0.42) < 0.08)
    finally:
        drone.close()


def test_an_up_heard_mid_climb_is_added_to_the_climb_under_way() -> None:
    sdk = FakeCoDroneSDK(height_m=0.4)
    drone = CoDroneEdu(sdk=sdk)
    try:
        drone.take_off(0.4)
        _wait_until(lambda: drone.state().phase == "hovering")
        goal = drone.state().target_m + 0.3
        drone.move_vertical(0.1)
        drone.move_vertical(0.1)
        drone.move_vertical(0.1)
        assert drone.state().target_m == pytest.approx(goal)
        _wait_until(lambda: abs(sdk.height_m - goal) <= 0.05 and drone.state().phase == "hovering")
    finally:
        drone.close()
