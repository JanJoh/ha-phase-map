"""Live model of the main fuse: per-phase current, metered and unmetered load, headroom.

The engine listens to the main-fuse sensors and every load meter, and recomputes
whenever one of them changes. Platforms and the websocket API read `engine.snapshot`
and subscribe to `SIGNAL_UPDATE`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    BASELINE_QUANTILE,
    BASELINE_SAMPLE_S,
    BASELINE_WINDOW,
    CONF_CHANNELS,
    CONF_CONNECTED,
    CONF_DRAW,
    CONF_ESTIMATED_PHASES,
    CONF_ESTIMATED_W,
    CONF_FUSE_A,
    CONF_MARGIN_A,
    CONF_METER,
    CONF_NAME,
    CONF_PARENT,
    CONF_PHASES,
    CONF_PORTABLE,
    CONF_SOURCES,
    CONF_VOLTAGE,
    DEFAULT_MARGIN_A,
    DOMAIN,
    DRAW_EQUAL,
    SIGNAL_UPDATE,
    SUBENTRY_DETECTOR,
    SUBENTRY_LOAD,
)
from .detection import analyse, async_fetch_means
from .hierarchy import descendants, effective_parents, split
from .phase_check import LIVE_MIN_STEP_A, POST_S, LoadCheck, PhaseChecker

_LOGGER = logging.getLogger(__name__)

# A phase of a multi-phase load counts as verified once it has been seen carrying the load:
VERIFY_A = 0.1            # a per-phase meter showed at least this much current (≈ 23 W; above meter noise)
VERIFY_SHARE = 0.2        # one meter for several phases: the phase moved by this share of a step
VERIFY_EVENTS = 2         # ... in this many live events
VERIFY_HISTORY_DAYS = 30

# Meters can report every second; recompute (and push to the card) at most this often.
UPDATE_INTERVAL_S = 1.0


def to_amps(state: State | None, voltage: float) -> float | None:
    """Convert a sensor state (A, mA, W, kW) to amps. None when unavailable."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit == UnitOfElectricCurrent.AMPERE:
        return value
    if unit == UnitOfElectricCurrent.MILLIAMPERE:
        return value / 1000
    if unit == UnitOfPower.WATT:
        return value / voltage
    if unit == UnitOfPower.KILO_WATT:
        return value * 1000 / voltage
    return None


@dataclass
class LoadModel:
    """A load as configured in a subentry."""

    subentry_id: str
    name: str
    channels: dict[str, str]                 # phase -> entity_id
    estimated_w: float | None = None
    estimated_phases: list[str] = field(default_factory=list)
    parent: str | None = None                # subentry id of the meter this load sits behind
    portable: bool = False                   # follow it to a new phase without asking

    meter: str | None = None                 # one meter for several connected phases
    connected: list[str] = field(default_factory=list)
    draw: str | None = None                  # DRAW_EQUAL, or the one phase it draws from

    @property
    def phases(self) -> list[str]:
        return list(self.channels) or list(self.connected) or list(self.estimated_phases)

    @property
    def estimated(self) -> bool:
        return not self.channels and not self.meter

    @property
    def entities(self) -> list[str]:
        return [*self.channels.values(), *([self.meter] if self.meter else [])]


def amps_for(load: LoadModel, get) -> dict[str, float | None]:
    """A load's current per phase, given `get(entity_id) -> amps | None`.

    A load with one meter for several phases splits the total as configured: equally over
    the connected phases, or all of it on the phase it draws from (a 1-phase car on a
    3-phase charger).
    """
    if load.channels:
        return {p: get(e) for p, e in load.channels.items()}
    if not load.meter:
        return {}
    total = get(load.meter)
    if total is None:
        return {p: None for p in load.connected}
    if load.draw in load.connected:
        return {p: (total if p == load.draw else 0.0) for p in load.connected}
    share = total / len(load.connected) if load.connected else 0.0
    return {p: share for p in load.connected}


class PhaseMapEngine:
    """Computes the per-phase picture from live states."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        data = entry.data
        self.phases: list[str] = list(data[CONF_PHASES])
        self.voltage: float = float(data[CONF_VOLTAGE])
        self.fuse_a: float = float(data[CONF_FUSE_A])
        self.margin_a: float = float(entry.options.get(CONF_MARGIN_A, data.get(CONF_MARGIN_A, DEFAULT_MARGIN_A)))
        self.sources: dict[str, str] = dict(data[CONF_SOURCES])
        self.loads: list[LoadModel] = [
            LoadModel(
                subentry_id=sub.subentry_id,
                name=sub.data.get(CONF_NAME, sub.title),
                channels={p: e for p, e in sub.data.get(CONF_CHANNELS, {}).items() if e and p in self.phases},
                estimated_w=sub.data.get(CONF_ESTIMATED_W),
                estimated_phases=[p for p in sub.data.get(CONF_ESTIMATED_PHASES, []) if p in self.phases],
                parent=sub.data.get(CONF_PARENT) or None,
                meter=sub.data.get(CONF_METER) or None,
                connected=[p for p in self.phases if p in (sub.data.get(CONF_CONNECTED) or [])],
                draw=sub.data.get(CONF_DRAW) or DRAW_EQUAL,
                portable=bool(sub.data.get(CONF_PORTABLE)),
            )
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_LOAD
        ]
        self.parents: dict[str, str | None] = effective_parents(self.loads)
        self.detector_ids = [s.subentry_id for s in entry.subentries.values() if s.subentry_type == SUBENTRY_DETECTOR]
        self.snapshot: dict[str, Any] = {}
        self._history: dict[str, deque[float]] = {p: deque(maxlen=BASELINE_WINDOW) for p in self.phases}
        self.baseline: dict[str, float] = {p: 0.0 for p in self.phases}
        self.detectors: dict[str, dict[str, Any]] = {}   # filled in by the detector binary sensors
        self._unsubs: list = []
        self.device_id: str | None = None   # the fuse device, set at setup
        self.checker = self._build_checker()
        self._pending: set = set()          # phase-check evaluations waiting for POST_S
        # Multi-phase loads: the phases seen carrying them (others are drawn dashed)
        self.verified: dict[str, set[str]] = {
            load.subentry_id: set() for load in self.loads if len(load.phases) > 1 and not load.estimated
        }
        self._verify_hits: dict[str, dict[str, int]] = {lid: {} for lid in self.verified}
        self._seed_task = None
        self._last_compute = 0.0
        self._recompute_timer = None

    # --- phase check ---------------------------------------------------------------

    def _build_checker(self) -> PhaseChecker:
        """One check per single-phase metered load, against its parent or the main fuse."""
        by_id = {load.subentry_id: load for load in self.loads}
        checks = []
        ignore: dict[str, set[str]] = {}
        for load in self.loads:
            if len(load.channels) != 1:
                continue
            parent = self.parents.get(load.subentry_id)
            # A parent with one meter for several phases cannot tell phases apart: compare
            # with the main fuse instead, but only allow the parent's phases.
            reference = dict(by_id[parent].channels) if parent and by_id[parent].channels else dict(self.sources)
            allowed = by_id[parent].phases if parent else list(self.sources)
            (phase, entity), = load.channels.items()
            checks.append(LoadCheck(load.subentry_id, phase, allowed, reference, entity))
            # Meters that move with this load are not confounders: its ancestors, its
            # descendants and the main fuse.
            skip = set(self.sources.values())
            up = parent
            while up:
                skip.update(by_id[up].entities)
                up = self.parents.get(up)
            for child in descendants(self.loads, load.subentry_id):
                if child in by_id:
                    skip.update(by_id[child].entities)
            ignore[load.subentry_id] = skip
        checker = PhaseChecker(checks, self._on_phase_verdict, clock=lambda: dt_util.utcnow().timestamp())
        checker.ignore_for = ignore
        return checker

    def _issue_id(self, load_id: str) -> str:
        return f"moved_{self.entry.entry_id}_{load_id}"

    def _restore_issues(self) -> None:
        """Keep open move reports that still apply; drop the ones that no longer do."""
        registry = ir.async_get(self.hass)
        prefix = f"moved_{self.entry.entry_id}_"
        for (domain, issue_id), issue in list(registry.issues.items()):
            if domain != DOMAIN or not issue_id.startswith(prefix):
                continue
            load_id = issue_id[len(prefix):]
            check = self.checker.checks.get(load_id)
            data = issue.data or {}
            if check is None or check.configured != data.get("from") or data.get("to") not in check.allowed:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            else:
                check.suspected = data.get("to")

    @callback
    def _on_phase_verdict(self, check: LoadCheck) -> None:
        load = next(item for item in self.loads if item.subentry_id == check.load_id)
        issue_id = self._issue_id(load.subentry_id)
        if check.suspected is None:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            self.recompute()
            return
        votes = sum(1 for v in check.votes if v == check.suspected)
        if load.portable:
            _LOGGER.info("%s moved from %s to %s (%s of %s switch events); following it",
                         load.name, check.configured, check.suspected, votes, len(check.votes))
            self.hass.bus.async_fire("logbook_entry", {
                "name": load.name, "domain": DOMAIN,
                "message": f"moved from {check.configured} to {check.suspected}; HA Phase Map follows it",
            })
            self.hass.async_create_task(async_move_load(self.hass, self.entry.entry_id, load.subentry_id,
                                                        check.suspected))
            return
        ir.async_create_issue(
            self.hass, DOMAIN, issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="load_moved",
            translation_placeholders={"name": load.name, "from": check.configured, "to": check.suspected,
                                      "votes": str(votes), "events": str(len(check.votes))},
            data={"entry_id": self.entry.entry_id, "subentry_id": load.subentry_id, "name": load.name,
                  "from": check.configured, "to": check.suspected},
        )
        self.recompute()

    # --- verified phases of multi-phase loads ---------------------------------------

    def _reference_for(self, load: LoadModel) -> tuple[dict[str, str], set[str]]:
        """Upstream meters per phase for `load`, and meters that move with it anyway."""
        by_id = {item.subentry_id: item for item in self.loads}
        parent = self.parents.get(load.subentry_id)
        reference = dict(by_id[parent].channels) if parent and by_id[parent].channels else dict(self.sources)
        skip = set(self.sources.values()) | set(load.entities)
        up = parent
        while up:
            skip.update(by_id[up].entities)
            up = self.parents.get(up)
        for child in descendants(self.loads, load.subentry_id):
            if child in by_id:
                skip.update(by_id[child].entities)
        return reference, skip

    def _verify_live(self, entity_id: str, old: float | None, new: float | None) -> None:
        for load in self.loads:
            if load.subentry_id not in self.verified:
                continue
            for phase, ent in load.channels.items():
                if ent == entity_id and new is not None and abs(new) >= VERIFY_A:
                    self.verified[load.subentry_id].add(phase)
            if load.meter == entity_id and old is not None and new is not None and abs(new - old) >= LIVE_MIN_STEP_A:
                self._schedule_meter_verify(load, self.checker.now(), new - old)

    def _schedule_meter_verify(self, load: LoadModel, t_step: float, step_a: float) -> None:
        @callback
        def _evaluate(_now) -> None:
            self._pending.discard(cancel)
            reference, skip = self._reference_for(load)
            response = self.checker.response(t_step, step_a, reference, skip)
            if response is None:
                return
            sign = 1 if step_a > 0 else -1
            hits = self._verify_hits[load.subentry_id]
            changed = False
            for phase, moved in response.items():
                if phase in load.phases and moved * sign / abs(step_a) >= VERIFY_SHARE:
                    hits[phase] = hits.get(phase, 0) + 1
                    if hits[phase] >= VERIFY_EVENTS and phase not in self.verified[load.subentry_id]:
                        self.verified[load.subentry_id].add(phase)
                        changed = True
            if changed:
                self.recompute()

        cancel = async_call_later(self.hass, POST_S, _evaluate)
        self._pending.add(cancel)

    async def _async_seed_verified(self) -> None:
        """Phases already seen carrying each multi-phase load, from recorder statistics."""
        def unit_of(entity_id: str) -> str | None:
            state = self.hass.states.get(entity_id)
            return state.attributes.get("unit_of_measurement") if state else None

        try:
            channel_ids = {e for load in self.loads if load.subentry_id in self.verified for e in load.channels.values()}
            if channel_ids:
                peaks = await async_fetch_means(self.hass, {e: unit_of(e) for e in channel_ids}, self.voltage,
                                                VERIFY_HISTORY_DAYS, "hour", key="max")
                for load in self.loads:
                    for phase, ent in load.channels.items():
                        if load.subentry_id in self.verified and any(abs(v) >= VERIFY_A for v in peaks.get(ent, {}).values()):
                            self.verified[load.subentry_id].add(phase)
            for load in self.loads:
                if load.subentry_id not in self.verified or not load.meter:
                    continue
                reference, _ = self._reference_for(load)
                ids = {load.meter, *reference.values()}
                means = await async_fetch_means(self.hass, {e: unit_of(e) for e in ids}, self.voltage, 10, "5minute")
                result = analyse(means.get(load.meter, {}), {p: means.get(e, {}) for p, e in reference.items()})
                for phase, score in result.scores.items():
                    if phase in load.phases and (score.gain or 0) >= VERIFY_SHARE and score.t >= 3:
                        self.verified[load.subentry_id].add(phase)
        except Exception as err:  # recorder missing or not ready  # noqa: BLE001
            _LOGGER.debug("Could not read history to verify phases: %s", err)
        self.recompute()

    @property
    def tracked_entities(self) -> list[str]:
        ids = set(self.sources.values())
        for load in self.loads:
            ids.update(load.entities)
        return sorted(ids)

    async def async_start(self) -> None:
        for entity_id in self.tracked_entities:
            self.checker.record(entity_id, to_amps(self.hass.states.get(entity_id), self.voltage))
        self._restore_issues()
        if self.verified:
            self._seed_task = self.hass.async_create_background_task(
                self._async_seed_verified(), f"phase_map verify phases {self.entry.entry_id}"
            )
        self._unsubs.append(
            async_track_state_change_event(self.hass, self.tracked_entities, self._on_state_change)
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._sample_baseline, timedelta(seconds=BASELINE_SAMPLE_S))
        )
        self.recompute()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for cancel in self._pending:
            cancel()
        self._pending.clear()
        if self._recompute_timer is not None:
            self._recompute_timer()
            self._recompute_timer = None
        if self._seed_task is not None and not self._seed_task.done():
            self._seed_task.cancel()

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new = to_amps(event.data["new_state"], self.voltage)
        old = to_amps(event.data["old_state"], self.voltage)
        self.checker.record(entity_id, new)
        if self.verified:
            self._verify_live(entity_id, old, new)
        t_step = self.checker.step(entity_id, old, new)
        if t_step is not None:
            step_a = new - old

            @callback
            def _evaluate(_now) -> None:
                self._pending.discard(cancel)
                self.checker.evaluate(entity_id, t_step, step_a, dt_util.utcnow())

            cancel = async_call_later(self.hass, POST_S, _evaluate)
            self._pending.add(cancel)
        self._schedule_recompute()

    @callback
    def _sample_baseline(self, _now=None) -> None:
        """Learn the quiet level of unmetered current per phase."""
        for p in self.phases:
            value = self.snapshot.get("phases", {}).get(p, {}).get("unmetered_a")
            if value is None:
                continue
            hist = self._history[p]
            hist.append(value)
            if len(hist) >= 10:
                ordered = sorted(hist)
                self.baseline[p] = ordered[int(BASELINE_QUANTILE * (len(ordered) - 1))]

    def load_amps(self, load: LoadModel) -> dict[str, float | None]:
        return amps_for(load, lambda e: to_amps(self.hass.states.get(e), self.voltage))

    @callback
    def _schedule_recompute(self) -> None:
        """Recompute now if the last one was long enough ago, otherwise once at the end of the window."""
        if UPDATE_INTERVAL_S <= 0:
            self.recompute()
            return
        if self._recompute_timer is not None:
            return
        wait = self._last_compute + UPDATE_INTERVAL_S - dt_util.utcnow().timestamp()
        if wait <= 0:
            self.recompute()
            return

        @callback
        def _trailing(_now) -> None:
            self._recompute_timer = None
            self.recompute()

        self._recompute_timer = async_call_later(self.hass, wait, _trailing)

    @callback
    def recompute(self) -> None:
        self._last_compute = dt_util.utcnow().timestamp()
        phases: dict[str, dict[str, Any]] = {}
        load_rows = []
        amps = {load.subentry_id: self.load_amps(load) for load in self.loads if not load.estimated}
        metered, rest = split(self.loads, amps, self.parents, self.phases)
        for load in self.loads:
            own = amps.get(load.subentry_id, {})
            row = {
                "id": load.subentry_id,
                "name": load.name,
                "phases": load.phases,
                "parent": self.parents.get(load.subentry_id),
                "estimated": load.estimated,
                "estimated_w": load.estimated_w,
                "a": {p: (round(a, 3) if a is not None else None) for p, a in own.items()},
                "w": {p: (round(max(a, 0.0) * self.voltage) if a is not None else None) for p, a in own.items()},
            }
            if load.subentry_id in self.verified:
                row["verified"] = [p for p in load.phases if p in self.verified[load.subentry_id]]
            if load.meter:
                row["meter"] = {"draw": load.draw if load.draw in load.connected else DRAW_EQUAL}
            if (check := self.checker.checks.get(load.subentry_id)) is not None:
                row["phase_check"] = check.as_dict()
            if load.subentry_id in rest:
                row["rest_w"] = {p: (None if a is None else round(a * self.voltage))
                                 for p, a in rest[load.subentry_id].items()}
            load_rows.append(row)
        for p in self.phases:
            total = to_amps(self.hass.states.get(self.sources.get(p, "")), self.voltage)
            if total is not None:
                total = abs(total)   # export (solar) still loads the fuse
            unmetered = None if total is None else total - metered[p]
            phases[p] = {
                "current_a": None if total is None else round(total, 2),
                "metered_a": round(metered[p], 2),
                "unmetered_a": None if unmetered is None else round(unmetered, 2),
                "headroom_a": None if total is None else round(self.fuse_a - total, 2),
                "baseline_a": round(self.baseline[p], 2),
            }
        self.snapshot = {
            "entry_id": self.entry.entry_id,
            "title": self.entry.title,
            "phases_order": self.phases,
            "voltage": self.voltage,
            "fuse_a": self.fuse_a,
            "margin_a": self.margin_a,
            "phases": phases,
            "loads": load_rows,
            "updated": dt_util.utcnow().isoformat(),
        }
        self.notify()

    @callback
    def notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    def payload(self) -> dict[str, Any]:
        """Snapshot plus detector states, for the websocket API."""
        return {**self.snapshot, "detectors": list(self.detectors.values())}

    def excess(self, phase: str) -> float | None:
        """Unmetered current above the learned quiet level."""
        unmetered = self.snapshot.get("phases", {}).get(phase, {}).get("unmetered_a")
        if unmetered is None:
            return None
        return unmetered - self.baseline.get(phase, 0.0)

    def seed_baseline(self, values: dict[str, list[float]]) -> None:
        """Pre-fill the baseline history (e.g. from recorder statistics at startup)."""
        for p, vals in values.items():
            if p in self._history and vals:
                self._history[p].extend(vals[-BASELINE_WINDOW:])
                ordered = sorted(self._history[p])
                self.baseline[p] = ordered[int(BASELINE_QUANTILE * (len(ordered) - 1))]



async def async_move_load(hass: HomeAssistant, entry_id: str, subentry_id: str, phase: str) -> bool:
    """Put a single-phase load on another phase (reloads the entry)."""
    entry = hass.config_entries.async_get_entry(entry_id)
    sub = entry.subentries.get(subentry_id) if entry else None
    channels = dict(sub.data.get(CONF_CHANNELS, {})) if sub else {}
    if sub is None or len(channels) != 1:
        return False
    (_, entity), = channels.items()
    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, CONF_CHANNELS: {phase: entity}})
    return True
