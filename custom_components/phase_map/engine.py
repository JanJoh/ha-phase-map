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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    BASELINE_QUANTILE,
    BASELINE_SAMPLE_S,
    BASELINE_WINDOW,
    CONF_CHANNELS,
    CONF_ESTIMATED_PHASES,
    CONF_ESTIMATED_W,
    CONF_FUSE_A,
    CONF_MARGIN_A,
    CONF_NAME,
    CONF_PARENT,
    CONF_PHASES,
    CONF_SOURCES,
    CONF_VOLTAGE,
    DEFAULT_MARGIN_A,
    SIGNAL_UPDATE,
    SUBENTRY_DETECTOR,
    SUBENTRY_LOAD,
)
from .hierarchy import effective_parents, split

_LOGGER = logging.getLogger(__name__)


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

    @property
    def phases(self) -> list[str]:
        return list(self.channels) or list(self.estimated_phases)

    @property
    def estimated(self) -> bool:
        return not self.channels


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

    @property
    def tracked_entities(self) -> list[str]:
        ids = set(self.sources.values())
        for load in self.loads:
            ids.update(load.channels.values())
        return sorted(ids)

    async def async_start(self) -> None:
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

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        self.recompute()

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
        return {p: to_amps(self.hass.states.get(e), self.voltage) for p, e in load.channels.items()}

    @callback
    def recompute(self) -> None:
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

