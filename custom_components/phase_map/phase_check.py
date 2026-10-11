"""Notice when a single-phase load has moved to another phase.

Someone plugs the kettle into a socket on another phase, and from then on its power is
subtracted from the wrong phase. We check every configured phase continuously, from live
switch-on/off events:

1. When a load's meter steps by at least LIVE_MIN_STEP_A, note the time and wait
   POST_S seconds for the reference meters to catch up.
2. Compare each reference phase (the main fuse, or the parent meter's channels for a
   load behind a parent) before (PRE_S before the step) and after. The phase that moved
   by about the same current, while the others stayed flat, is where the load is.
3. Events where another meter moved a lot in the same window are confounded and skipped.
4. When MOVE_VOTES of the last EVIDENCE events agree on another phase, the load has
   probably moved: a repair issue asks to confirm, or, for a portable load, the phase is
   updated directly.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Any

PRE_S = 15.0                # reference value this long before the step
POST_S = 10.0               # ... and this long after it
LIVE_MIN_STEP_A = 0.5       # ≈ 115 W at 230 V; smaller steps drown in main-fuse noise
CONFOUND_RATIO = 0.3        # another meter moving by more than this share of the step spoils it
MATCH_GAIN = 0.6            # the load's phase must move by at least this share of the step
OTHER_MAX_GAIN = 0.3        # ... and every other phase by less than this
EVIDENCE = 6
MOVE_VOTES = 5
BUFFER_S = 120.0


@dataclass
class _Series:
    """Recent (time, amps) samples of one entity."""

    times: list[float] = field(default_factory=list)
    values: list[float | None] = field(default_factory=list)

    def add(self, t: float, value: float | None) -> None:
        self.times.append(t)
        self.values.append(value)
        cut = bisect_right(self.times, t - BUFFER_S)
        if cut > 1:   # keep one sample older than the window so value_at still works
            del self.times[: cut - 1]
            del self.values[: cut - 1]

    def value_at(self, t: float) -> float | None:
        i = bisect_right(self.times, t)
        return self.values[i - 1] if i else None


@dataclass
class LoadCheck:
    """Evidence about one load's phase."""

    load_id: str
    configured: str
    allowed: list[str]                       # phases it could be on (main fuse or parent)
    reference: dict[str, str]                # phase -> entity measuring that phase upstream
    entity: str
    votes: deque = field(default_factory=lambda: deque(maxlen=EVIDENCE))
    last_event: datetime | None = None
    suspected: str | None = None

    def as_dict(self) -> dict[str, Any]:
        agree = sum(1 for v in self.votes if v == self.configured)
        return {
            "phase": self.configured,
            "agree": agree,
            "disagree": sum(1 for v in self.votes if v != self.configured),
            "events": len(self.votes),
            "suspected": self.suspected,
            "last_event": self.last_event.isoformat() if self.last_event else None,
        }


def classify(step_a: float, response: dict[str, float]) -> str | None:
    """The phase an event points at, or None when it is unclear."""
    if not response or abs(step_a) < LIVE_MIN_STEP_A:
        return None
    sign = 1 if step_a > 0 else -1
    gains = {p: r * sign / abs(step_a) for p, r in response.items()}
    best = max(gains, key=gains.__getitem__)
    if gains[best] < MATCH_GAIN:
        return None
    if any(g > OTHER_MAX_GAIN for p, g in gains.items() if p != best):
        return None
    return best


def verdict(check: LoadCheck) -> str | None:
    """The phase the load has moved to, if the evidence says so."""
    if len(check.votes) < MOVE_VOTES:
        return None
    counts: dict[str, int] = {}
    for v in check.votes:
        counts[v] = counts.get(v, 0) + 1
    phase, n = max(counts.items(), key=lambda kv: kv[1])
    if phase != check.configured and n >= MOVE_VOTES and phase in check.allowed:
        return phase
    return None


class PhaseChecker:
    """Feeds live state changes in, reports moved loads out."""

    def __init__(self, checks: list[LoadCheck], on_change: Callable[[LoadCheck], None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.checks = {c.load_id: c for c in checks}
        self._by_entity = {c.entity: c for c in checks}
        self._series: dict[str, _Series] = {}
        self._on_change = on_change
        self._clock = clock
        self.ignore_for: dict[str, set[str]] = {}   # load id -> entities that are not confounders

    def now(self) -> float:
        return self._clock()

    def record(self, entity_id: str, amps: float | None, t: float | None = None) -> None:
        self._series.setdefault(entity_id, _Series()).add(self._clock() if t is None else t, amps)

    def step(self, entity_id: str, old: float | None, new: float | None, t: float | None = None) -> float | None:
        """A load meter changed; returns the event time when it is worth evaluating."""
        check = self._by_entity.get(entity_id)
        if check is None or old is None or new is None or abs(new - old) < LIVE_MIN_STEP_A:
            return None
        return self._clock() if t is None else t

    def response(self, t_step: float, step_a: float, reference: dict[str, str],
                 skip: set[str]) -> dict[str, float] | None:
        """How much each reference phase moved around a step; None if spoiled or unknown.

        Any tracked meter outside `skip` and the reference that moved by more than
        CONFOUND_RATIO of the step in the same window spoils the event.
        """
        before_t, after_t = t_step - PRE_S, t_step + POST_S
        skip = skip | set(reference.values())
        for other, series in self._series.items():
            if other in skip:
                continue
            a, b = series.value_at(before_t), series.value_at(after_t)
            if a is not None and b is not None and abs(b - a) > CONFOUND_RATIO * abs(step_a):
                return None
        out: dict[str, float] = {}
        for phase, ref in reference.items():
            series = self._series.get(ref)
            a = series.value_at(before_t) if series else None
            b = series.value_at(after_t) if series else None
            if a is None or b is None:
                return None
            out[phase] = abs(b) - abs(a)
        return out

    def evaluate(self, entity_id: str, t_step: float, step_a: float, now: datetime) -> str | None:
        """Called POST_S after a step: record the vote, report a verdict change."""
        check = self._by_entity.get(entity_id)
        if check is None:
            return None
        skip = {entity_id, *self.ignore_for.get(check.load_id, set())}
        response = self.response(t_step, step_a, check.reference, skip)
        if response is None:
            return None
        vote = classify(step_a, response)
        if vote is None:
            return None
        check.votes.append(vote)
        check.last_event = now
        new = verdict(check)
        if new != check.suspected:
            check.suspected = new
            self._on_change(check)
        return vote
