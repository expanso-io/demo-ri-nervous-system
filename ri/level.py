"""Zero the drone's gyro on a level surface, before it goes where it will fly.

    just level

Do this on a hard, level table with the drone still, then carry it to where
it takes off. The rig never does it by itself: where the drone sits on the
day may slope, and zeroing it there would teach it that the slope is level.
"""

from __future__ import annotations

import socket
import sys

from ri.ports import ports


def rig_is_up() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", ports()["FLIGHT_PORT"])) == 0


def main() -> int:
    if rig_is_up():
        print("the rig is still running and holds the drone's radio: run `just level` again")
        return 1
    from ri.drone.codrone import CoDroneEdu

    drone = CoDroneEdu()
    try:
        state = drone.state()
        print(f"battery {state.battery}%. Hands off: zeroing the gyro")
        if not drone.level():
            print("FAIL  the drone did not finish in time: it was moving, or it is out of reach")
            return 1
        print("ok    gyro zeroed. Carry it to its spot, then `just again`")
        return 0
    finally:
        drone.close()


if __name__ == "__main__":
    sys.exit(main())
