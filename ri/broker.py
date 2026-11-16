"""The MQTT broker on the presenter laptop (a real broker, loopback only)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from amqtt.broker import Broker

from ri.ports import port


async def serve() -> None:
    bind = os.environ.get("RI_MQTT_BIND", "127.0.0.1")
    mqtt_port = port("MQTT_PORT")
    broker = Broker(
        {
            "listeners": {
                "default": {"type": "tcp", "bind": f"{bind}:{mqtt_port}"},
            },
            "sys_interval": 0,
            "auth": {"allow-anonymous": True, "plugins": ["auth_anonymous"]},
            "topic-check": {"enabled": False},
        }
    )
    await broker.start()
    print(f"mqtt broker on {bind}:{mqtt_port}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await broker.shutdown()


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
