"""The laptop's MQTT bus: topics and a small client wrapper.

Sense pipelines publish decisions to ri/sense/<sense>; fly-drone publishes
what it did to ri/act; the flight service publishes the airframe's own state
to ri/drone/state. Device telemetry (mic levels, frame activity) goes to
ri/device/<device> straight from the device: a level number, never audio.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from ri.ports import port

SENSE_TOPIC = "ri/sense/{sense}"
ACT_TOPIC = "ri/act"
DRONE_TOPIC = "ri/drone/state"
DEVICE_TOPIC = "ri/device/{device}"

Handler = Callable[[str, bytes], None]


class Bus:
    def __init__(self, client_id: str, subscriptions: tuple[str, ...] = ()) -> None:
        self.client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.reconnect_delay_set(min_delay=1, max_delay=2)
        self.subscriptions = subscriptions
        self.handlers: list[Handler] = []
        self.connected = threading.Event()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client: mqtt.Client, *_: Any) -> None:
        for topic in self.subscriptions:
            client.subscribe(topic, qos=0)
        self.connected.set()

    def _on_disconnect(self, *_: Any) -> None:
        self.connected.clear()

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        for handler in self.handlers:
            handler(msg.topic, bytes(msg.payload))

    def on(self, handler: Handler) -> None:
        self.handlers.append(handler)

    def start(self) -> Bus:
        self.client.connect_async("127.0.0.1", port("MQTT_PORT"), keepalive=10)
        self.client.loop_start()
        return self

    def publish(self, topic: str, body: dict[str, Any]) -> None:
        if self.connected.is_set():
            self.client.publish(topic, json.dumps(body, separators=(",", ":")), qos=0)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
