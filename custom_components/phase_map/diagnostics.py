"""Diagnostics download: configuration, live values and the evidence behind them.

Everything a bug report needs: the main fuse and every load and detector as configured,
how loads nest, the live snapshot the card draws, the phase check's evidence per load,
which phases of multi-phase loads are verified, the state of every meter involved, and what
meter discovery found and skipped.
Load names are kept (they are what the reporter will refer to); nothing here is a secret.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import PhaseMapConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: PhaseMapConfigEntry) -> dict[str, Any]:
    engine = entry.runtime_data
    meters = {}
    for entity_id in engine.tracked_entities:
        state = hass.states.get(entity_id)
        meters[entity_id] = None if state is None else {
            "state": state.state,
            "unit": state.attributes.get("unit_of_measurement"),
            "device_class": state.attributes.get("device_class"),
            "state_class": state.attributes.get("state_class"),
            "last_updated": state.last_updated.isoformat(),
        }
    return {
        "entry": {"title": entry.title, "version": entry.version, "data": dict(entry.data),
                  "options": dict(entry.options)},
        "subentries": [
            {"id": sub.subentry_id, "type": sub.subentry_type, "title": sub.title, "data": dict(sub.data)}
            for sub in entry.subentries.values()
        ],
        "effective_parents": engine.parents,
        "verified_phases": {load_id: sorted(phases) for load_id, phases in engine.verified.items()},
        "phase_check": {load_id: check.as_dict() | {"votes": list(check.votes), "allowed": check.allowed,
                                                    "reference": check.reference}
                        for load_id, check in engine.checker.checks.items()},
        "baseline_a": engine.baseline,
        "snapshot": engine.payload(),
        "meters": meters,
        "discovery": engine.discovery.as_dict() if getattr(engine, "discovery", None) else None,
    }
