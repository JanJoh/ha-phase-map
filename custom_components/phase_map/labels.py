"""Optional labels on load devices that show their phase, such as "Fas: 2".

The label is "<name>: <value>", where the name ("Fas") and the value per phase ("1", "2",
"3", or "R", "S", "T") are set in the options. Each load's meter entities lead to a device;
that device gets one label per phase its loads use. Estimated loads have no device.

Only what this integration added is ever removed: it records, per device, the labels it
added (not ones that were already there) and the labels it created in the label registry.
When the settings change or a load moves, the labels follow. Turning the option off asks
whether to remove them (async_remove_labels) or leave them (async_forget_labels).
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CHANNELS,
    CONF_LABEL_NAME,
    CONF_LABELS,
    CONF_PHASES,
    DEFAULT_LABEL_NAME,
    DOMAIN,
    SUBENTRY_LOAD,
    phase_value_key,
)

STORE_VERSION = 1
LABEL_ICON = "mdi:sine-wave"
LABEL_COLORS = ["brown", "blue", "green"]   # the card's L1 / L2 / L3 colours


def _store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, STORE_VERSION, f"{DOMAIN}.labels.{entry_id}")


def default_value(phases: list[str], phase: str) -> str:
    return str(phases.index(phase) + 1)


def label_names(entry: ConfigEntry) -> dict[str, str]:
    """Label name per phase, e.g. {"L1": "Fas: 1"}."""
    phases = entry.data[CONF_PHASES]
    name = (entry.options.get(CONF_LABEL_NAME) or DEFAULT_LABEL_NAME).strip() or DEFAULT_LABEL_NAME
    out = {}
    for p in phases:
        value = str(entry.options.get(phase_value_key(p)) or default_value(phases, p)).strip()
        out[p] = f"{name}: {value or default_value(phases, p)}"
    return out


def desired_labels(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, set[str]]:
    """Device id -> label names it should carry."""
    names = label_names(entry)
    entities = er.async_get(hass)
    out: dict[str, set[str]] = {}
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_LOAD:
            continue
        for phase, entity_id in (sub.data.get(CONF_CHANNELS) or {}).items():
            reg = entities.async_get(entity_id) if entity_id else None
            if reg is None or reg.device_id is None or phase not in names:
                continue
            out.setdefault(reg.device_id, set()).add(names[phase])
    return out


async def _load(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    data = await _store(hass, entry_id).async_load() or {}
    return {"created": dict(data.get("created", {})), "added": {k: list(v) for k, v in data.get("added", {}).items()}}


def _label_in_use(hass: HomeAssistant, label_id: str) -> bool:
    if dr.async_entries_for_label(dr.async_get(hass), label_id):
        return True
    if ar.async_entries_for_label(ar.async_get(hass), label_id):
        return True
    return bool(er.async_entries_for_label(er.async_get(hass), label_id))


async def async_sync_labels(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Make device labels match the configuration (or remove ours when the option is off)."""
    if not entry.options.get(CONF_LABELS):
        return
    state = await _load(hass, entry.entry_id)
    labels = lr.async_get(hass)
    devices = dr.async_get(hass)
    phases = entry.data[CONF_PHASES]
    names = label_names(entry)
    color = {names[p]: LABEL_COLORS[i % len(LABEL_COLORS)] for i, p in enumerate(phases)}

    def label_id(name: str) -> str:
        existing = labels.async_get_label_by_name(name)
        if existing is not None:
            return existing.label_id
        created = labels.async_create(name, icon=LABEL_ICON, color=color.get(name),
                                      description="Set by HA Phase Map: the phase this device is on.")
        state["created"][created.label_id] = name
        return created.label_id

    desired = {dev: {label_id(n) for n in wanted} for dev, wanted in desired_labels(hass, entry).items()}
    for device_id in set(desired) | set(state["added"]):
        device = devices.async_get(device_id)
        ours = set(state["added"].get(device_id, []))
        if device is None:
            state["added"].pop(device_id, None)
            continue
        want = desired.get(device_id, set())
        current = set(device.labels)
        add = want - current
        drop = ours - want
        if add or drop:
            devices.async_update_device(device_id, labels=(current | add) - drop)
        kept = (ours - drop) | add
        if kept:
            state["added"][device_id] = sorted(kept)
        else:
            state["added"].pop(device_id, None)
    # Labels we created that nothing uses any more (renamed name or values) go away again
    for lid in list(state["created"]):
        if labels.async_get_label(lid) is None:
            state["created"].pop(lid)
        elif not _label_in_use(hass, lid) and lid not in {x for v in desired.values() for x in v}:
            labels.async_delete(lid)
            state["created"].pop(lid)
    await _store(hass, entry.entry_id).async_save(state)


async def async_label_counts(hass: HomeAssistant, entry_id: str) -> tuple[int, int]:
    """(labels added, devices) that a removal would undo."""
    state = await _load(hass, entry_id)
    return sum(len(v) for v in state["added"].values()), len(state["added"])


async def async_remove_labels(hass: HomeAssistant, entry_id: str) -> None:
    """Take off every label this integration added, and delete labels it created if unused."""
    state = await _load(hass, entry_id)
    devices = dr.async_get(hass)
    labels = lr.async_get(hass)
    for device_id, ours in state["added"].items():
        device = devices.async_get(device_id)
        if device is not None:
            devices.async_update_device(device_id, labels=set(device.labels) - set(ours))
    for lid in state["created"]:
        if labels.async_get_label(lid) is not None and not _label_in_use(hass, lid):
            labels.async_delete(lid)
    await _store(hass, entry_id).async_remove()


async def async_forget_labels(hass: HomeAssistant, entry_id: str) -> None:
    """Leave the labels in place, but stop treating them as ours."""
    await _store(hass, entry_id).async_remove()
