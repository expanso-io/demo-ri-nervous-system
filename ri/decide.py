"""The decide node: one readable rule table and nothing clever.

    land > stop > move > wake

* A decision is fresh for 3 s. A stale decision is never acted on; the drone
  holds instead. A late message cannot move the drone.
* Land needs more than half of the voting block with at least 10 people seen.
* Each word and each clap is acted on at most once.
* When a land and a word arrive together, the land wins and the word is
  recorded as having lost. That rule is the whole safety system.

Freshness and priority live here and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ri.drone import MAX_ALTITUDE_M, STEP_M, WAKE_ALTITUDE_M, DroneState
from ri.messages import Decision, Sense

FRESH_S = 3.0
LAND_SHARE = 0.5
LAND_MIN_SEEN = 10
CLAP_MIN = 0.5
WORD_MIN = 0.5
FLOOR_M = WAKE_ALTITUDE_M

Act = Literal["land", "stop", "up", "down", "wake", "hold"]
RULES: tuple[str, ...] = ("land", "stop", "move", "wake")


@dataclass(frozen=True)
class Action:
    """What the decide node did and why, in words the screen can show."""

    act: Act
    because: str
    rule: str | None = None
    inputs: tuple[str, ...] = ()
    waiting: bool = False
    t: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "act": self.act,
            "because": self.because,
            "rule": self.rule,
            "inputs": list(self.inputs),
            "waiting": self.waiting,
            "t": round(self.t, 3),
        }


@dataclass
class Decider:
    """Holds the latest decision per sense and applies the rule table."""

    latest: dict[Sense, Decision] = field(default_factory=dict)
    consumed: set[str] = field(default_factory=set)

    def observe(self, decision: Decision) -> None:
        current = self.latest.get(decision.sense)
        if current is None or decision.t >= current.t:
            self.latest[decision.sense] = decision

    def fresh(self, sense: Sense, now: float) -> Decision | None:
        decision = self.latest.get(sense)
        if decision is None or now - decision.t > FRESH_S or decision.t - now > FRESH_S:
            return None
        return decision

    def _pending(self, sense: Sense, now: float, floor: float) -> Decision | None:
        decision = self.fresh(sense, now)
        if decision is None or decision.key in self.consumed or decision.confidence < floor:
            return None
        return decision

    def _consume(self, *decisions: Decision | None) -> None:
        for decision in decisions:
            if decision is not None:
                self.consumed.add(decision.key)

    def decide(self, drone: DroneState, now: float) -> Action:
        hands = self.fresh("hands", now)
        word = self._pending("word", now, WORD_MIN)
        clap = self._pending("clap", now, CLAP_MIN)
        majority = hands is not None and hands.seen >= LAND_MIN_SEEN and hands.share > LAND_SHARE

        # Rule 1, land: the safety reflex beats everything.
        if drone.airborne and majority and hands is not None:
            because = f"hands {hands.share:.2f}"
            inputs = [hands.label()]
            if word is not None:
                because += f" > word {word.value}"
                inputs.append(word.label())
            self._consume(word, clap)
            return Action("land", because, "land", tuple(inputs), t=now)

        # Rule 2, stop: hold position now.
        if drone.airborne and word is not None and word.value == "stop":
            self._consume(word)
            return Action("stop", "word stop", "stop", (word.label(),), t=now)

        # Rule 3, move: one step up or down inside the flight envelope.
        if drone.airborne and word is not None and word.value in ("up", "down"):
            self._consume(word)
            act: Act = "up" if word.value == "up" else "down"
            if act == "up" and drone.target_m + STEP_M > MAX_ALTITUDE_M + 1e-6:
                return Action("hold", "ceiling reached", "move", (word.label(),), t=now)
            if act == "down" and drone.target_m - STEP_M < FLOOR_M - 1e-6:
                return Action("hold", "lowest safe height", "move", (word.label(),), t=now)
            return Action(act, f"word {word.value}", "move", (word.label(),), t=now)

        # Rule 4, wake: a clap brings a dark or parked drone up to hover.
        if not drone.airborne and drone.phase != "landing" and clap is not None:
            self._consume(clap)
            return Action("wake", "clap", "wake", (clap.label(),), t=now)

        # Nothing actionable: hold, and say why.
        self._consume(clap if drone.airborne else None)
        if word is not None and not drone.airborne:
            self._consume(word)
            return Action("hold", "asleep: clap to wake", None, (word.label(),), t=now)
        if drone.airborne and hands is not None and hands.up > 0:
            if hands.seen < LAND_MIN_SEEN:
                why = f"waiting: {hands.seen} seen"
            else:
                why = f"waiting: {hands.up} of {hands.seen}"
            return Action("hold", why, None, (hands.label(),), waiting=True, t=now)
        stale = [s for s in ("hands",) if s in self.latest and self.fresh(s, now) is None]
        if drone.airborne and stale:
            return Action("hold", "no fresh instruction", None, t=now)
        return Action("hold", "no instruction", None, t=now)
