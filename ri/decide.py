"""The decide node: one readable rule table and nothing clever.

    land > stop > move > route > wake

* A decision is fresh for 3 s. A stale decision is never acted on; the drone
  holds instead. A late message cannot move the drone.
* Land needs the room's applause once the route is flown, or the
  presenter's jazz hands at any time. Applause before then is spent: the
  wake claps and the cheer at take-off must not bring it straight back down.
  Raised hands are counted and shown, but no number of them lands it.
* Each word and each clap is acted on at most once.
* The route is fixed. An arm signal releases its next leg only when it is
  the signal that leg asks for; it never steers, and once the route is flown
  no wave moves the drone at all.
* Only the presenter wakes a sleeping drone, from the board. A clap heard
  in the room never wakes it: claps are for landing it. Each up is one step higher, and an up
  heard while it is still climbing is added to that climb.
* When a land and a word arrive together, the land wins and the word is
  recorded as having lost. That rule is the whole safety system.

Freshness and priority live here and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ri.drone import MAX_ALTITUDE_M, STEP_M, WAKE_ALTITUDE_M, DroneState
from ri.messages import Decision, Sense
from ri.route import END

FRESH_S = 3.0
CLAP_MIN = 0.5
WORD_MIN = 0.5
FLOOR_M = WAKE_ALTITUDE_M

Act = Literal["land", "stop", "up", "down", "go", "wake", "hold"]
RULES: tuple[str, ...] = ("land", "stop", "move", "route", "wake")


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

    def reset(self) -> None:
        self.latest.clear()
        self.consumed.clear()

    def suppress_pending(self) -> None:
        self._consume(self.latest.get("clap"), self.latest.get("word"))

    def decide(self, drone: DroneState, now: float, need: str | None = None) -> Action:
        hands = self.fresh("hands", now)
        word = self._pending("word", now, WORD_MIN)
        clap = self._pending("clap", now, CLAP_MIN)
        jazz = hands is not None and hands.gesture == "jazz"
        heard = clap if clap is not None and clap.value == "applause" else None
        landing = drone.phase == "landing"
        route_flown = need is None or need == END
        applause = heard if route_flown or landing else None

        # Rule 1, land: the safety reflex beats everything. It keeps holding
        # while the drone comes down, so a shout mid-descent still loses.
        if (drone.airborne or landing) and (jazz or applause is not None):
            source = applause if applause is not None else hands
            assert source is not None
            because = "applause" if applause is not None else "jazz hands"
            inputs = [source.label()]
            # Name the word that lost, even one already acted on a moment ago.
            rival = word or self.fresh("word", now)
            if rival is not None and rival.value in ("up", "down", "stop"):
                because += f" > word {rival.value}"
                inputs.append(rival.label())
            self._consume(word, clap)
            return Action("land", because, "land", tuple(inputs), t=now)
        if landing and word is not None:
            self._consume(word, clap)
            return Action(
                "hold", f"landing: word {word.value} ignored", None, (word.label(),), t=now
            )

        # Rule 2, stop: hold position now.
        if drone.airborne and word is not None and word.value == "stop":
            self._consume(word)
            return Action("stop", "word stop", "stop", (word.label(),), t=now)

        # Rule 3, move: one step up or down inside the flight envelope.
        if drone.airborne and word is not None and word.value in ("up", "down"):
            self._consume(word)
            # A step asked for mid-climb is added to the climb under way, so
            # five quick "up"s are five steps, not one and four refusals.
            if drone.phase not in ("hovering", "climbing", "descending"):
                return Action(
                    "hold", f"{drone.phase}: wait for hover", None, (word.label(),), t=now
                )
            act: Act = "up" if word.value == "up" else "down"
            if act == "up" and drone.target_m + STEP_M > MAX_ALTITUDE_M + 1e-6:
                return Action("hold", "ceiling reached", "move", (word.label(),), t=now)
            if act == "down" and drone.target_m - STEP_M < FLOOR_M - 1e-6:
                return Action("hold", "lowest safe height", "move", (word.label(),), t=now)
            return Action(act, f"word {word.value}", "move", (word.label(),), t=now)

        # Rule 4, route: the next leg of the fixed path, released only by the
        # arm signal that leg asks for. Once the path is flown the drone waits
        # over the pad for the room's hands, and no wave moves it.
        signal = hands if hands is not None and hands.key not in self.consumed else None
        flying_route = need is not None and need != END
        if drone.phase == "hovering" and flying_route and signal is not None and signal.gesture:
            self._consume(signal)
            if signal.gesture == need:
                return Action("go", f"wave {need}", "route", (signal.label(),), t=now)
            return Action(
                "hold", f"wants a wave {need}", "route", (signal.label(),), waiting=True, t=now
            )

        # Rule 5, wake: the presenter's wake brings a dark or parked drone up
        # to hover. Claps heard in the room are spent below, never a wake.
        grounded = not drone.airborne and drone.phase != "landing"
        if grounded and clap is not None and clap.value == "wake":
            self._consume(clap)
            return Action("wake", "presenter", "wake", (clap.label(),), t=now)

        # Nothing actionable: hold, and say why.
        # A clap heard while flying or coming down is spent. Left pending, a
        # clap during a landing (or the thump of the landing itself) would
        # wake the drone the instant it touched the floor.
        # On the ground every heard clap and applause is spent: never a wake.
        self._consume(clap)
        if drone.airborne and heard is not None:
            why = f"applause: wants the {need} leg first"
            return Action("hold", why, None, (heard.label(),), t=now)
        if word is not None and not drone.airborne:
            self._consume(word)
            return Action("hold", "asleep: wake it from the board", None, (word.label(),), t=now)
        stale = [s for s in ("hands",) if s in self.latest and self.fresh(s, now) is None]
        if drone.airborne and stale:
            return Action("hold", "no fresh instruction", None, t=now)
        return Action("hold", "no instruction", None, t=now)
