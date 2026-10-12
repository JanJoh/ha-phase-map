"""Work out which main-fuse phase a meter is on, from recorder statistics.

Every time the meter steps up or down, the matching main-fuse phase should step by
about the same current while the other phases stay flat. We collect those steps from
5-minute mean statistics and, per phase, average the main-fuse response aligned to the
direction of the meter's step:

- gain = average aligned response / average step size (about 1.0 on the right phase)
- t    = average aligned response / its standard error (how sure we are)

The best phase is suggested when it is clearly ahead of the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from itertools import pairwise, permutations
import math
import statistics
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

STEP_S = 300                 # 5-minute statistics
MIN_STEP_A = 0.065           # ≈ 15 W at 230 V
MAX_THRESHOLD_A = 0.8
MIN_STEPS = 4
MIN_T = 3.0
MIN_GAIN = 0.5
CONFOUND_RATIO = 0.3         # a sibling channel moving by more than this share spoils a step
GROUP_MARGIN = 1.5           # the best channel→phase mapping must beat the runner-up by this


@dataclass
class PhaseScore:
    gain: float | None
    t: float
    steps: int


@dataclass
class Suggestion:
    phase: str | None
    confidence: str            # "high", "medium", "none"
    steps: int
    scores: dict[str, PhaseScore] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "confidence": self.confidence,
            "steps": self.steps,
            "reason": self.reason,
            "scores": {
                p: {"gain": None if s.gain is None else round(s.gain, 2), "t": round(s.t, 1), "steps": s.steps}
                for p, s in self.scores.items()
            },
        }


def analyse(
    meter: dict[float, float],
    phases: dict[str, dict[float, float]],
    step_s: float = STEP_S,
    confounders: list[dict[float, float]] | None = None,
) -> Suggestion:
    """Score each phase for a meter. Series are {timestamp: amps}.

    `confounders` are other series (the sibling channels of a multi-channel meter) whose
    simultaneous steps would blur the picture: steps where one of them moved too are skipped.
    """
    common = sorted(set(meter).intersection(*[set(v) for v in phases.values()])) if phases else []
    pairs = [(a, b) for a, b in pairwise(common) if abs((b - a) - step_s) < 1]
    dx = [meter[b] - meter[a] for a, b in pairs]
    if not dx:
        return Suggestion(None, "none", 0, reason="no_data")
    biggest = max(abs(x) for x in dx)
    threshold = max(MIN_STEP_A, min(MAX_THRESHOLD_A, 0.25 * biggest))
    idx = [i for i, x in enumerate(dx) if abs(x) >= threshold]
    if confounders:
        def spoiled(i: int) -> bool:
            a, b = pairs[i]
            return any(a in c and b in c and abs(c[b] - c[a]) > CONFOUND_RATIO * abs(dx[i]) for c in confounders)
        idx = [i for i in idx if not spoiled(i)]
    if len(idx) < MIN_STEPS:
        return Suggestion(None, "none", len(idx), reason="too_few_steps")
    mag = statistics.mean(abs(dx[i]) for i in idx)
    scores: dict[str, PhaseScore] = {}
    for p, series in phases.items():
        aligned = [(series[pairs[i][1]] - series[pairs[i][0]]) * (1 if dx[i] > 0 else -1) for i in idx]
        m = statistics.mean(aligned)
        sd = statistics.stdev(aligned) if len(aligned) > 1 else 0.0
        se = sd / math.sqrt(len(aligned)) if sd else 0.0
        t = m / se if se else (math.copysign(99.0, m) if m else 0.0)
        scores[p] = PhaseScore(gain=m / mag if mag else None, t=t, steps=len(idx))
    ranked = sorted(scores.items(), key=lambda kv: kv[1].t, reverse=True)
    best_phase, best = ranked[0]
    runner_up = ranked[1][1].t if len(ranked) > 1 else 0.0
    clear = best.t >= MIN_T and (best.gain or 0) >= MIN_GAIN and (runner_up <= 0 or best.t >= 2 * runner_up)
    if not clear:
        return Suggestion(None, "none", len(idx), scores, reason="unclear")
    high = best.t >= 6 and 0.7 <= (best.gain or 0) <= 1.5 and len(idx) >= 10
    return Suggestion(best_phase, "high" if high else "medium", len(idx), scores, reason="ok")


@dataclass
class GroupSuggestion:
    """Which phase each channel of a multi-channel meter is on."""

    mapping: dict[str, str]            # phase -> channel key
    confidence: str                    # "high", "medium" or "guess"
    reason: str = ""
    channels: dict[str, Suggestion] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mapping": self.mapping,
            "confidence": self.confidence,
            "reason": self.reason,
            "channels": {k: v.as_dict() for k, v in self.channels.items()},
        }


def analyse_group(channels: dict[str, dict[float, float]], phases: dict[str, dict[float, float]],
                  step_s: float = STEP_S) -> GroupSuggestion:
    """Map the channels of one meter (a Shelly Pro 3EM, a garage meter) to phases.

    Each channel is scored on its own steps, skipping the ones where a sibling channel moved
    at the same time. The channel→phase permutation with the highest total t wins. When the
    evidence is not clear (a balanced 3-phase load only ever steps on all channels at once,
    or a channel never switches), the best permutation is still returned as a guess.
    """
    keys = sorted(channels)
    per = {
        k: analyse(channels[k], phases, step_s, [channels[o] for o in keys if o != k])
        for k in keys
    }
    names = list(phases)
    if len(keys) > len(names):
        return GroupSuggestion({}, "none", "too_many_channels", per)

    def t_of(k: str, p: str) -> float:
        score = per[k].scores.get(p)
        return score.t if score else 0.0

    ranked = sorted(
        ((sum(t_of(k, p) for k, p in zip(keys, perm, strict=True)), perm)
         for perm in permutations(names, len(keys))),
        key=lambda x: x[0], reverse=True,
    )
    best_sum, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    mapping = {p: k for k, p in zip(keys, best, strict=True)}

    def level(k: str, p: str) -> str:
        sug, score = per[k], per[k].scores.get(p)
        if not score or score.t < MIN_T or (score.gain or 0) < MIN_GAIN:
            return "none"
        return "high" if score.t >= 6 and 0.7 <= (score.gain or 0) <= 1.5 and sug.steps >= 10 else "medium"

    levels = [level(k, p) for p, k in mapping.items()]
    clear = best_sum > 0 and (runner_up <= 0 or best_sum >= GROUP_MARGIN * runner_up)
    if clear and all(lv != "none" for lv in levels):
        return GroupSuggestion(mapping, "high" if all(lv == "high" for lv in levels) else "medium", "ok", per)
    reason = "balanced" if all(per[k].steps == 0 for k in keys) or not clear else "unclear"
    if best_sum <= 0:
        # Nothing to go on: keep the channels in their own order (l1, l2, l3 or a, b, c)
        mapping = dict(zip(names, keys, strict=False))
    return GroupSuggestion(mapping, "guess", reason, per)


def _series(rows: list[dict[str, Any]], factor: float, key: str = "mean") -> dict[float, float]:
    out = {}
    for row in rows:
        mean = row.get(key)
        if mean is None:
            continue
        start = row["start"]
        ts = start if isinstance(start, (int, float)) else start.timestamp()
        out[float(ts)] = float(mean) * factor
    return out


def unit_factor(unit: str | None, voltage: float) -> float | None:
    """Factor that turns a sensor's unit into amps."""
    return {"A": 1.0, "mA": 0.001, "W": 1 / voltage, "kW": 1000 / voltage}.get(unit or "")


async def async_fetch_means(
    hass: HomeAssistant, entity_units: dict[str, str | None], voltage: float, days: float, period: str,
    key: str = "mean",
) -> dict[str, dict[float, float]]:
    """Mean (or max, min) statistics in amps for several entities over the last `days`."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    end = dt_util.utcnow()
    start = end - timedelta(days=days)
    ids = set(entity_units)
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, ids, period, None, {key}
    )
    out = {}
    for entity_id, unit in entity_units.items():
        factor = unit_factor(unit, voltage)
        out[entity_id] = _series(rows.get(entity_id, []), factor, key) if factor else {}
    return out


async def async_suggest_phase(
    hass: HomeAssistant, meter: str, sources: dict[str, str], voltage: float, days: float = 10
) -> Suggestion:
    """Suggest the phase for `meter` from the last `days` of 5-minute statistics."""
    def unit_of(entity_id: str) -> str | None:
        state = hass.states.get(entity_id)
        return state.attributes.get("unit_of_measurement") if state else None

    units = {meter: unit_of(meter), **{e: unit_of(e) for e in sources.values()}}
    try:
        data = await async_fetch_means(hass, units, voltage, min(days, 10), "5minute")
    except Exception:  # recorder missing or not ready
        return Suggestion(None, "none", 0, reason="no_recorder")
    if not data.get(meter):
        return Suggestion(None, "none", 0, reason="no_statistics")
    return analyse(data[meter], {p: data.get(e, {}) for p, e in sources.items()})
