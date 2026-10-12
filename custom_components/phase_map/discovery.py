"""Meter discovery: suggest power and current sensors that are not added as loads yet.

Off by default. When it is on, HA Phase Map looks once a day for power/current sensors that
no load uses, works out their phase (and the meter they sit behind) from the same 5-minute
statistics the phase suggestion uses, and offers the clearest ones as repair issues, at most
MAX_OPEN at a time. Nothing is ever added without the user confirming it.

The Energy dashboard is a seed: the devices listed there under "Individual devices" are
meters the user has already picked out, so they are offered first and with a lower bar,
and their "upstream device" decides the parent. "Suggest meters from the Energy dashboard"
in Configure runs that part on demand, also while discovery is off.

Multi-channel meters (a Shelly Pro 3EM, a garage meter with l1/l2/l3) become one candidate.
The channel names are only used for grouping, never as the phase: a channel called "c" or
"L3" can be on any phase, and the evidence decides. When it cannot (a balanced 3-phase
load that only ever switches on all phases at once), the mapping is offered as a guess.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import timedelta
import hashlib
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, slugify

from .const import (
    CONF_CHANNELS,
    CONF_DISCOVERY,
    CONF_DISCOVERY_CONFIDENCE,
    CONF_METER,
    CONF_SOURCES,
    CONF_VOLTAGE,
    DEFAULT_DISCOVERY,
    DEFAULT_DISCOVERY_CONFIDENCE,
    DOMAIN,
    ISSUE_NEW_METER,
    SUBENTRY_LOAD,
)
from .detection import analyse, analyse_group, async_fetch_means

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SCAN_INTERVAL = timedelta(hours=24)
STARTUP_DELAY_S = 600              # first scan 10 minutes after Home Assistant has started
FORCED_DELAY_S = 30                # "Scan now" and the Energy dashboard action
MAX_OPEN = 5                       # open suggestions at a time
MAX_ANALYSED = 100                 # candidates analysed per scan
BATCH = 10                         # entities per statistics query
DAYS = 10

SUPPORTED_UNITS = {"A", "mA", "W", "kW"}
CURRENT_UNITS = {"A", "mA"}
# Sensors that are computed from other sensors, or estimated rather than measured
DENY_PLATFORMS = {
    DOMAIN, "group", "min_max", "utility_meter", "integration", "derivative", "statistics",
    "filter", "template", "powercalc", "threshold", "trend",
}
# Trailing words that say what is measured, not which channel: phase_a_active_power → phase_a
MEASURE_WORDS = {
    "power", "active", "apparent", "reactive", "current", "watt", "watts", "load", "rms",
    "effekt", "aktiv", "skenbar", "reaktiv", "strom", "ström", "effektivvarde", "effektivvärde",
}
AGGREGATE_KEYS = {"", "total", "sum", "summa", "all", "combined"}
# A channel that names a phase: phase_a, l1, fas_2, garaget_l3, a
PHASE_TAG = re.compile(r"(?:^|_)(?:(?:phase|fas)_?([abc123])|(l[123])|([abc]))$")
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "guess": 2}


@dataclass
class Candidate:
    """A meter (one sensor, or the per-phase channels of one device) that no load uses."""

    key: str
    name: str
    channels: dict[str, str]          # channel tag (or "") -> entity_id
    device_id: str | None = None
    energy: bool = False              # listed under "Individual devices" in the Energy dashboard

    @property
    def group(self) -> bool:
        return len(self.channels) > 1

    @property
    def entities(self) -> list[str]:
        return list(self.channels.values())


@dataclass
class Result:
    """What the evidence says about a candidate."""

    status: str                       # "offer", "unclear", "no_data"
    confidence: str = "none"          # "high", "medium", "guess", "none"
    phase: str | None = None          # single meter
    mapping: dict[str, str] = field(default_factory=dict)   # group: phase -> entity_id
    parent: str | None = None         # subentry id of the load it sits behind
    parent_source: str | None = None  # "energy" or "stats"
    steps: int = 0
    gain: float | None = None
    reason: str = ""


def candidate_key(device_id: str | None, channel: str, entity_id: str, registry_id: str | None) -> str:
    if device_id:
        return f"dev:{device_id}:{channel}"
    return f"ent:{registry_id or entity_id}"


def issue_id_for(entry_id: str, key: str) -> str:
    return f"{ISSUE_NEW_METER}{entry_id}_{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def channel_of(object_id: str, device_slugs: list[str]) -> tuple[str, int]:
    """The channel part of an object id, and how many measurement words were stripped.

    `shellypro3em_ece334ea8c9c_phase_a_current` on device `shellypro3em_ece334ea8c9c` → phase_a.
    """
    rest = object_id
    for slug in sorted((s for s in device_slugs if s), key=len, reverse=True):
        if rest == slug:
            rest = ""
            break
        if rest.startswith(slug + "_"):
            rest = rest[len(slug) + 1:]
            break
    tokens = [t for t in rest.split("_") if t]
    stripped = 0
    while tokens and tokens[-1] in MEASURE_WORDS:
        tokens.pop()
        stripped += 1
    return "_".join(tokens), stripped


def phase_tag(channel: str) -> str | None:
    m = PHASE_TAG.search(channel)
    return next((g for g in m.groups() if g), None) if m else None


def used_entities(hass: HomeAssistant) -> set[str]:
    """Every entity any HA Phase Map load uses, in any entry, plus the main-fuse sources."""
    used: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        used.update(entry.data.get(CONF_SOURCES, {}).values())
        for sub in entry.subentries.values():
            if sub.subentry_type != SUBENTRY_LOAD:
                continue
            used.update(e for e in (sub.data.get(CONF_CHANNELS) or {}).values() if e)
            if sub.data.get(CONF_METER):
                used.add(sub.data[CONF_METER])
    return used


def select_candidates(
    hass: HomeAssistant,
    sources: dict[str, str],
    used: set[str],
    ignored_keys: set[str],
    ignored_entities: set[str],
    energy_rates: set[str],
) -> tuple[list[Candidate], dict[str, list[str]]]:
    """Which sensors could be added as loads, and why the others were skipped."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    excluded: dict[str, list[str]] = {}

    def skip(reason: str, entity_id: str) -> None:
        excluded.setdefault(reason, []).append(entity_id)

    first = hass.states.get(next(iter(sources.values()), ""))
    prefer_current = bool(first and first.attributes.get("unit_of_measurement") in CURRENT_UNITS)
    source_devices = {r.device_id for e in sources.values() if (r := ent_reg.async_get(e)) and r.device_id}

    # entity_id -> (device_id, registry id)
    pool: dict[str, tuple[str | None, str | None]] = {}
    for state in hass.states.async_all("sensor"):
        attrs = state.attributes
        entity_id = state.entity_id
        if attrs.get("device_class") not in ("power", "current"):
            continue
        if attrs.get("state_class") != "measurement" or attrs.get("unit_of_measurement") not in SUPPORTED_UNITS:
            continue
        reg = ent_reg.async_get(entity_id)
        if reg is not None:
            if reg.disabled_by or reg.hidden_by or reg.entity_category is not None:
                skip("hidden", entity_id)
                continue
            if reg.platform in DENY_PLATFORMS:
                skip("platform", entity_id)
                continue
        if "poe" in entity_id.split(".", 1)[1].split("_"):
            skip("not_a_load", entity_id)   # power a network switch delivers over its ports
            continue
        if entity_id in sources.values() or (reg and reg.device_id in source_devices):
            skip("main_fuse", entity_id)
            continue
        if entity_id in used:
            skip("used", entity_id)
            continue
        if entity_id in ignored_entities:
            skip("ignored", entity_id)
            continue
        pool[entity_id] = (reg.device_id if reg else None, reg.id if reg else None)

    def unit_rank(entity_id: str) -> int:
        state = hass.states.get(entity_id)
        unit = state.attributes.get("unit_of_measurement") if state else None
        return 0 if (unit in CURRENT_UNITS) == prefer_current else 1

    def best(entities: list[tuple[str, int]]) -> str:
        """Of several sensors for one channel: the fuse's unit, the Energy dashboard's, the plainest."""
        return min(entities, key=lambda x: (unit_rank(x[0]), 0 if x[0] in energy_rates else 1, x[1], x[0]))[0]

    def root_of(device_id: str | None) -> str | None:
        """Group by the physical meter: a Shelly Pro 3EM has one sub-device per phase."""
        device = dev_reg.async_get(device_id) if device_id else None
        return (device.via_device_id or device.id) if device else None

    def names_of(device_id: str | None) -> list[str]:
        device = dev_reg.async_get(device_id) if device_id else None
        return [n for n in ((device.name, device.name_by_user) if device else ()) if n]

    def channel_for(entity_id: str, device_id: str | None, root: str) -> tuple[str, int]:
        """The channel an entity measures: its sub-device's phase, or what its name says."""
        object_id = entity_id.split(".", 1)[1]
        if device_id and device_id != root:
            # A sub-device per channel: "shellypro3em-… Phase A" → phase_a
            for name in names_of(device_id):
                tag_source = slugify(name)
                if phase_tag(tag_source):
                    return PHASE_TAG.search(tag_source).group(0).strip("_"), 0
            return f"sub_{device_id}", channel_of(object_id, [slugify(n) for n in names_of(device_id)])[1]
        return channel_of(object_id, [slugify(n) for n in names_of(root)])

    # entity -> (root, channel, stripped); entities already used count for grouping too
    by_root: dict[str, dict[str, list[tuple[str, int]]]] = {}
    taken: dict[str, set[str]] = {}
    on_root: dict[str, set[str]] = {}       # channels of entities on the main device itself
    has_subs: set[str] = set()              # meters with a sub-device per channel

    def note(entity_id: str, device_id: str, root: str) -> str:
        ch = channel_for(entity_id, device_id, root)[0]
        if device_id == root:
            on_root.setdefault(root, set()).add(ch)
        else:
            has_subs.add(root)
        return ch

    for entity_id, (device_id, _) in pool.items():
        root = root_of(device_id)
        if root:
            ch = note(entity_id, device_id, root)
            by_root.setdefault(root, {}).setdefault(ch, []).append(
                (entity_id, channel_for(entity_id, device_id, root)[1]))
    for entity_id in used:
        reg = ent_reg.async_get(entity_id)
        root = root_of(reg.device_id) if reg else None
        if root in by_root:
            taken.setdefault(root, set()).add(note(entity_id, reg.device_id, root))

    candidates: list[Candidate] = []
    for root, channels in by_root.items():
        names = names_of(root)
        device_name = next((n for n in reversed(names)), root)   # the user's name first
        taken_channels = taken.get(root, set())
        all_channels = set(channels) | taken_channels
        phase_channels = sorted(ch for ch in channels if phase_tag(ch))
        # The meter's total: "total", or a phase channel's name without its tag (garaget_l1 → garaget)
        bases = {PHASE_TAG.sub("", ch) for ch in all_channels if phase_tag(ch)}
        totals = {ch for ch in all_channels
                  if not phase_tag(ch) and (ch in AGGREGATE_KEYS or ch in bases or ch.startswith("total")
                                            or (root in has_subs and ch in on_root.get(root, set())))}
        total_taken = bool(taken_channels & totals)
        any_phase_taken = any(phase_tag(t) for t in taken_channels)
        # All phases free: one multi-phase candidate. Some taken (a 3EM with one appliance per
        # channel): the free ones are single meters. The total taken (a CEE outlet added by
        # its total): its phases are the same circuit and are not offered.
        as_group = len(phase_channels) >= 2 and not any_phase_taken and not total_taken
        if as_group:
            chosen = {ch: best(channels[ch]) for ch in phase_channels}
            for ch in phase_channels:
                for entity_id, _ in channels[ch]:
                    if entity_id != chosen[ch]:
                        skip("twin", entity_id)
            candidates.append(Candidate(f"dev:{root}:phases", device_name, chosen, root))
        real_channels = [c for c in all_channels if c not in totals]
        for ch, members in channels.items():
            if as_group and ch in phase_channels:
                continue
            if ch in taken_channels:
                for entity_id, _ in members:
                    skip("used", entity_id)
                continue
            if total_taken and phase_tag(ch):
                for entity_id, _ in members:
                    skip("taken_group", entity_id)
                continue
            if ch in totals and len(all_channels) > 1:
                # A total next to its channels measures the same current twice
                for entity_id, _ in members:
                    skip("aggregate", entity_id)
                continue
            pick = best(members)
            for entity_id, _ in members:
                if entity_id != pick:
                    skip("twin", entity_id)
            sub_device = pool[pick][0]
            sub_names = names_of(sub_device) if sub_device != root else []
            state = hass.states.get(pick)
            if len(real_channels) <= 1:
                name = device_name
            elif sub_names:
                name = sub_names[-1]
            else:
                name = state.attributes.get("friendly_name") if state else pick
            candidates.append(Candidate(f"dev:{root}:{ch or 'main'}", name or pick, {"": pick}, root))
    for entity_id, (device_id, registry_id) in pool.items():
        if device_id:
            continue
        state = hass.states.get(entity_id)
        name = state.attributes.get("friendly_name") if state else entity_id
        candidates.append(Candidate(candidate_key(None, "", entity_id, registry_id), name or entity_id,
                                    {"": entity_id}))

    kept = []
    for cand in candidates:
        if cand.key in ignored_keys:
            excluded.setdefault("ignored", []).extend(cand.entities)
            continue
        cand.energy = bool(set(cand.entities) & energy_rates)
        kept.append(cand)
    return kept, excluded


def _unit(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return state.attributes.get("unit_of_measurement") if state else None


async def async_evaluate(
    hass: HomeAssistant,
    sources: dict[str, str],
    voltage: float,
    loads: list[Any],
    parents: dict[str, str | None],
    candidates: list[Candidate],
    energy_parent,
    allowed: set[str],
) -> dict[str, Result]:
    """Run the phase analysis for each candidate.

    `loads` are the engine's LoadModels; `energy_parent(candidate)` returns the subentry id
    the Energy dashboard puts upstream, or None. `allowed` are the confidence levels offered
    (Energy dashboard devices are always offered from "medium").
    """
    reference = {e for e in sources.values()}
    for load in loads:
        reference.update(load.channels.values())
    ref = await async_fetch_means(hass, {e: _unit(hass, e) for e in reference}, voltage, DAYS, "5minute")
    main = {p: ref.get(e, {}) for p, e in sources.items()}
    by_id = {load.subentry_id: load for load in loads}

    def depth(sub_id: str) -> int:
        n, up = 0, parents.get(sub_id)
        while up:
            n, up = n + 1, parents.get(up)
        return n

    results: dict[str, Result] = {}
    for i in range(0, len(candidates), BATCH):
        batch = candidates[i:i + BATCH]
        ids = {e for c in batch for e in c.entities}
        series = await async_fetch_means(hass, {e: _unit(hass, e) for e in ids}, voltage, DAYS, "5minute")
        for cand in batch:
            ok = allowed | ({"medium"} if cand.energy else set())
            if not any(series.get(e) for e in cand.entities):
                results[cand.key] = Result("no_data", reason="no_statistics")
                continue
            parent = energy_parent(cand)
            source = "energy" if parent else None
            if cand.group:
                refs = main
                if parent and by_id.get(parent) and len(by_id[parent].channels) >= len(cand.channels):
                    refs = {p: ref.get(e, {}) for p, e in by_id[parent].channels.items()}
                g = analyse_group({k: series.get(e, {}) for k, e in cand.channels.items()}, refs)
                if not g.mapping:
                    results[cand.key] = Result("unclear", reason=g.reason)
                    continue
                mapping = {p: cand.channels[k] for p, k in g.mapping.items()}
                status = "offer" if g.confidence in ok or g.confidence == "guess" else "unclear"
                results[cand.key] = Result(status, g.confidence, mapping=mapping, parent=parent,
                                           parent_source=source, reason=g.reason)
                continue
            (entity,) = cand.entities
            meter = series.get(entity, {})
            if not parent:
                # Behind a multi-channel meter? It then steps with exactly one of its channels.
                options = []
                for load in loads:
                    if len(load.channels) < 2 or entity in load.entities:
                        continue
                    s = analyse(meter, {p: ref.get(e, {}) for p, e in load.channels.items()})
                    gain = s.scores[s.phase].gain if s.phase else None
                    if s.confidence == "high" and gain is not None and 0.7 <= gain <= 1.5:
                        options.append((depth(load.subentry_id), load.subentry_id))
                if options:
                    parent, source = max(options)[1], "stats"
            refs = main
            if parent and by_id.get(parent) and by_id[parent].channels:
                refs = {p: ref.get(e, {}) for p, e in by_id[parent].channels.items()}
            sug = analyse(meter, refs)
            gain = sug.scores[sug.phase].gain if sug.phase else None
            status = "offer" if sug.confidence in ok else ("unclear" if sug.reason != "no_data" else "no_data")
            results[cand.key] = Result(status, sug.confidence, phase=sug.phase, parent=parent,
                                       parent_source=source, steps=sug.steps,
                                       gain=None if gain is None else round(gain, 2), reason=sug.reason)
    return results


class DiscoveryManager:
    """Keeps the candidate list, schedules scans and raises the suggestions as repair issues."""

    def __init__(self, hass: HomeAssistant, entry, engine) -> None:
        self.hass = hass
        self.entry = entry
        self.engine = engine
        self.store: Store = _store(hass, entry.entry_id)
        self.data: dict[str, Any] = {}
        self.excluded: dict[str, list[str]] = {}
        self._unsubs: list = []
        self._task = None
        self.scanning = False

    # --- settings ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.entry.options.get(CONF_DISCOVERY, DEFAULT_DISCOVERY))

    @property
    def min_confidence(self) -> str:
        return self.entry.options.get(CONF_DISCOVERY_CONFIDENCE, DEFAULT_DISCOVERY_CONFIDENCE)

    @property
    def allowed(self) -> set[str]:
        return {"high"} if self.min_confidence == "high" else {"high", "medium"}

    # --- lifecycle -----------------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self.store.async_load() or {}
        self.data = {
            "last_scan": stored.get("last_scan"),
            "candidates": stored.get("candidates", {}),
            "ignored": stored.get("ignored", {}),
            "ignored_entities": stored.get("ignored_entities", {}),
            "known_used": stored.get("known_used", []),
            "seeded": stored.get("seeded", []),
            "pending": stored.get("pending"),          # "scan" or "energy": run soon after start
        }
        self._forget_removed_loads()
        self.reconcile()
        self._save()
        self._schedule_from_state()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _save(self) -> None:
        self.store.async_delay_save(lambda: self.data, 1)

    def _forget_removed_loads(self) -> None:
        """A sensor whose load was deleted is not suggested again."""
        used = used_entities(self.hass)
        removed = set(self.data["known_used"]) - used
        now = dt_util.utcnow().isoformat()
        for entity_id in removed:
            self.data["ignored_entities"].setdefault(entity_id, {"at": now, "reason": "removed_load"})
        self.data["known_used"] = sorted(used)

    # --- scheduling ----------------------------------------------------------------

    def _schedule_from_state(self) -> None:
        pending = self.data.get("pending")
        if pending:
            self._schedule(FORCED_DELAY_S, energy_only=pending == "energy")
            return
        if not self.enabled:
            return
        last = self.data.get("last_scan")
        if last is None:
            delay = STARTUP_DELAY_S
        else:
            due = dt_util.parse_datetime(last) + SCAN_INTERVAL
            delay = max(FORCED_DELAY_S, (due - dt_util.utcnow()).total_seconds())
        self._schedule(delay)

    def _schedule(self, delay: float, energy_only: bool = False) -> None:
        @callback
        def _run(_now=None) -> None:
            self._task = self.hass.async_create_background_task(
                self.async_scan(energy_only=energy_only), f"phase_map discovery {self.entry.entry_id}"
            )

        @callback
        def _started(_hass) -> None:
            self._unsubs.append(async_call_later(self.hass, delay, _run))

        self._unsubs.append(async_at_started(self.hass, _started))

    def request(self, kind: str) -> None:
        """Ask for a scan ("scan") or an Energy dashboard pass ("energy") after the next reload."""
        self.data["pending"] = kind
        self._save()

    # --- scanning ------------------------------------------------------------------

    async def _energy_rates(self) -> tuple[list[dict[str, Any]], set[str]]:
        from .energy_prefs import async_energy_devices

        devices = await async_energy_devices(self.hass)
        return devices, {d["stat_rate"] for d in devices if d.get("stat_rate")}

    def _energy_parent_fn(self, devices: list[dict[str, Any]]):
        from .energy_prefs import suggest_parent

        loads = {load.subentry_id: load.entities for load in self.engine.loads if load.entities}
        sources = list(self.engine.sources.values())

        def parent(cand: Candidate) -> str | None:
            return suggest_parent(self.hass, devices, cand.entities, loads, sources) if loads else None

        return parent

    async def async_scan(self, energy_only: bool = False) -> None:
        """Find candidates, analyse them, and update the suggestions."""
        if self.scanning:
            return
        self.scanning = True
        try:
            devices, rates = await self._energy_rates()
            cands, self.excluded = select_candidates(
                self.hass, self.engine.sources, used_entities(self.hass),
                set(self.data["ignored"]), set(self.data["ignored_entities"]), rates,
            )
            if energy_only:
                cands = [c for c in cands if c.energy]
            cands.sort(key=lambda c: (not c.energy, c.key))
            cands = cands[:MAX_ANALYSED]
            results = await async_evaluate(
                self.hass, self.engine.sources, float(self.entry.data[CONF_VOLTAGE]), self.engine.loads,
                self.engine.parents, cands, self._energy_parent_fn(devices), self.allowed,
            )
        except Exception:  # recorder not ready, statistics missing: try again next time
            _LOGGER.exception("Meter discovery failed")
            return
        finally:
            self.scanning = False
        now = dt_util.utcnow().isoformat()
        known = self.data["candidates"]
        if not energy_only:
            known = {k: v for k, v in known.items() if k in results}
        for cand in cands:
            res = results.get(cand.key)
            if res is None:
                continue
            known[cand.key] = {"name": cand.name, "channels": cand.channels, "device_id": cand.device_id,
                               "energy": cand.energy, "analysed_at": now, **asdict(res)}
        self.data["candidates"] = known
        if energy_only:
            self.data["seeded"] = sorted(set(self.data["seeded"]) | {c.key for c in cands})
        else:
            self.data["last_scan"] = now
        self.data["pending"] = None
        self.reconcile()
        self._save()
        _LOGGER.debug("Meter discovery: %s candidates, %s offered", len(cands),
                      sum(1 for r in results.values() if r.status == "offer"))
        if self.enabled and not energy_only:
            self._schedule(SCAN_INTERVAL.total_seconds())

    # --- suggestions ---------------------------------------------------------------

    def ignore(self, key: str, reason: str = "user") -> None:
        cand = self.data["candidates"].get(key, {})
        self.data["ignored"][key] = {"name": cand.get("name", key), "entities": list(cand.get("channels", {}).values()),
                                     "at": dt_util.utcnow().isoformat(), "reason": reason}
        self.reconcile()
        self._save()

    def unignore(self, key: str) -> None:
        item = self.data["ignored"].pop(key, None)
        if item is None and key in self.data["ignored_entities"]:
            self.data["ignored_entities"].pop(key)
        self._save()

    def candidate(self, key: str) -> dict[str, Any] | None:
        return self.data["candidates"].get(key)

    def offerable(self) -> list[tuple[str, dict[str, Any]]]:
        used = used_entities(self.hass)
        seeded = set(self.data["seeded"])
        out = [
            (key, c) for key, c in self.data["candidates"].items()
            if c.get("status") == "offer"
            and key not in self.data["ignored"]
            and not set(c.get("channels", {}).values()) & used
            and not set(c.get("channels", {}).values()) & set(self.data["ignored_entities"])
            and (self.enabled or key in seeded)
        ]
        out.sort(key=lambda kc: (not kc[1].get("energy"), CONFIDENCE_ORDER.get(kc[1].get("confidence"), 3),
                                 -(kc[1].get("steps") or 0), kc[0]))
        return out

    @callback
    def reconcile(self) -> None:
        """Bring the repair issues in line with the candidate list."""
        registry = ir.async_get(self.hass)
        prefix = f"{ISSUE_NEW_METER}{self.entry.entry_id}_"
        existing = {iid: issue for (domain, iid), issue in registry.issues.items()
                    if domain == DOMAIN and iid.startswith(prefix)}
        # "Ignore" in the Repairs list: remember it here, so it stays ignored
        for issue in existing.values():
            key = (issue.data or {}).get("key")
            if issue.dismissed_version is not None and key and key not in self.data["ignored"]:
                cand = self.data["candidates"].get(key, {})
                self.data["ignored"][key] = {"name": cand.get("name", key),
                                             "entities": list(cand.get("channels", {}).values()),
                                             "at": dt_util.utcnow().isoformat(), "reason": "repairs_ignore"}
        offer = self.offerable()
        wanted = {issue_id_for(self.entry.entry_id, key): (key, c) for key, c in offer}
        for iid in existing:
            if iid not in wanted:
                ir.async_delete_issue(self.hass, DOMAIN, iid)
        open_ids = [iid for iid in wanted if iid in existing]
        for iid, (key, cand) in wanted.items():
            if iid not in existing and len(open_ids) >= MAX_OPEN:
                continue
            if iid not in existing:
                open_ids.append(iid)
            self._raise(iid, key, cand)

    def _raise(self, issue_id: str, key: str, cand: dict[str, Any]) -> None:
        lang = "sv" if (self.hass.config.language or "").startswith("sv") else "en"
        ir.async_create_issue(
            self.hass, DOMAIN, issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="new_multi_meter" if len(cand.get("channels", {})) > 1 else "new_meter",
            translation_placeholders={"name": cand.get("name", key), "details": self.describe(cand, lang)},
            data={"entry_id": self.entry.entry_id, "key": key},
        )

    def parent_title(self, parent: str | None) -> str | None:
        if not parent:
            return None
        sub = self.entry.subentries.get(parent)
        return sub.title if sub else None

    def describe(self, cand: dict[str, Any], lang: str) -> str:
        """One line of evidence for the issue and the confirm form."""
        sv = lang == "sv"
        conf = {"high": "hög" if sv else "high", "medium": "medel" if sv else "medium"}.get(cand.get("confidence"), "")
        parent = self.parent_title(cand.get("parent"))
        if len(cand.get("channels", {})) > 1:
            by_entity = {e: k for k, e in cand["channels"].items()}
            pairs = ", ".join(f"{by_entity.get(e, e)} → {p}" for p, e in sorted(cand.get("mapping", {}).items()))
            if cand.get("confidence") == "guess":
                text = (f"Kanaler (gissning, inget tydligt underlag): {pairs}" if sv
                        else f"Channels (a guess, the evidence is not clear): {pairs}")
            else:
                text = (f"Kanaler ({conf} säkerhet): {pairs}" if sv else f"Channels ({conf} confidence): {pairs}")
        else:
            gain = cand.get("gain")
            steps = cand.get("steps") or 0
            text = (f"Troligen på {cand.get('phase')} ({conf} säkerhet: {steps} till/från-steg, förstärkning {gain})"
                    if sv else
                    f"Probably on {cand.get('phase')} ({conf} confidence: {steps} on/off steps, gain {gain})")
        if parent:
            src = cand.get("parent_source")
            if sv:
                text += f", bakom {parent}" + (" (enligt Energipanelen)" if src == "energy" else "")
            else:
                text += f", behind {parent}" + (" (from the Energy dashboard)" if src == "energy" else "")
        if cand.get("energy"):
            text += ". Finns i Energipanelen" if sv else ". Listed in the Energy dashboard"
        return text + "."

    def as_dict(self) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        for c in self.data.get("candidates", {}).values():
            statuses[c.get("status", "?")] = statuses.get(c.get("status", "?"), 0) + 1
        return {
            "enabled": self.enabled,
            "min_confidence": self.min_confidence,
            "last_scan": self.data.get("last_scan"),
            "pending": self.data.get("pending"),
            "counts_by_status": statuses,
            "candidates": self.data.get("candidates", {}),
            "seeded": self.data.get("seeded", []),
            "ignored": self.data.get("ignored", {}),
            "ignored_entities": self.data.get("ignored_entities", {}),
            "excluded": {k: v[:50] for k, v in self.excluded.items()},
            "open": [k for k, _ in self.offerable()][:MAX_OPEN],
        }


def _store(hass: HomeAssistant, entry_id: str) -> Store:
    """One Store per entry, kept across reloads, so a delayed save is seen by the next setup."""
    stores = hass.data.setdefault(f"{DOMAIN}_discovery_stores", {})
    if entry_id not in stores:
        stores[entry_id] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.discovery.{entry_id}")
    return stores[entry_id]


async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
    await _store(hass, entry_id).async_remove()
    hass.data.get(f"{DOMAIN}_discovery_stores", {}).pop(entry_id, None)
