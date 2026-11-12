"""The decision messages that travel between devices.

These are the only things that leave a sense device. Each is a few dozen
bytes of JSON; no audio sample or video frame is ever inside one.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

Sense = Literal["clap", "word", "hands"]
SENSES: tuple[Sense, ...] = ("clap", "word", "hands")

# The pipeline that carries each sense, named for what it does.
PIPELINE_FOR: dict[Sense, str] = {
    "clap": "hear-clap",
    "word": "hear-commands",
    "hands": "see-hands",
}


@dataclass(frozen=True)
class Decision:
    """One decision from one sense device."""

    sense: Sense
    t: float
    confidence: float = 0.0
    value: str | None = None
    up: int = 0
    seen: int = 0
    share: float = 0.0
    seq: int = 0

    @property
    def key(self) -> str:
        """Stable identity, so one decision is acted on at most once."""
        return f"{self.sense}:{self.seq}:{self.t:.3f}"

    def label(self) -> str:
        """The short form the audience reads: `clap 0.93`, `up 0.81`, `hands 62%`."""
        if self.sense == "clap":
            return f"clap {self.confidence:.2f}"
        if self.sense == "word":
            return f"{self.value} {self.confidence:.2f}"
        return f"hands {round(self.share * 100)}%"

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"sense": self.sense}
        if self.sense == "clap":
            body["confidence"] = round(self.confidence, 2)
        elif self.sense == "word":
            body["value"] = self.value
            body["confidence"] = round(self.confidence, 2)
        else:
            body.update(up=self.up, seen=self.seen, share=round(self.share, 2))
        body["t"] = round(self.t, 3)
        body["seq"] = self.seq
        return body


class MessageError(ValueError):
    """A message that is not a well-formed decision."""


def parse_decision(raw: bytes | str | dict[str, Any]) -> Decision:
    """Parse a decision from MQTT or HTTP; reject anything malformed."""
    data: Any = raw if isinstance(raw, dict) else json.loads(raw)
    if not isinstance(data, dict):
        raise MessageError("decision must be a JSON object")
    sense = data.get("sense")
    if sense not in SENSES:
        raise MessageError(f"unknown sense: {sense!r}")
    try:
        t = float(data.get("t", time.time()))
        seq = int(data.get("seq", 0))
        if sense == "clap":
            return Decision("clap", t, confidence=float(data["confidence"]), seq=seq)
        if sense == "word":
            value = str(data["value"])
            if value not in ("up", "down", "stop"):
                raise MessageError(f"unknown word: {value!r}")
            return Decision("word", t, confidence=float(data["confidence"]), value=value, seq=seq)
        up, seen = int(data["up"]), int(data["seen"])
        share = float(data.get("share", up / seen if seen else 0.0))
        return Decision("hands", t, up=up, seen=seen, share=share, seq=seq)
    except (KeyError, TypeError) as error:
        raise MessageError(f"incomplete {sense} decision: {error}") from None
