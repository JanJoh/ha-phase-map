"""Read nested meters from the Energy dashboard's "upstream device" setting.

The Energy dashboard records, per individual device, which other device's meter already
includes it (`included_in_stat`). When a meter is added here we look it up there and
pre-select the matching parent load. Devices are matched by entity first and by the
device the entities belong to second, because the Energy dashboard usually points at
energy (kWh) sensors while loads here use power or current sensors of the same device.
"""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)


async def async_energy_devices(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The Energy dashboard's individual devices, or [] when energy is not set up."""
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
    except Exception:  # energy not loaded or not configured  # noqa: BLE001
        return []
    return list((manager.data or {}).get("device_consumption") or [])


def suggest_parent(
    hass: HomeAssistant,
    devices: list[dict[str, Any]],
    meter_entities: Iterable[str],
    candidates: dict[str, Iterable[str]],
    main_sources: Iterable[str] = (),
) -> str | None:
    """The candidate (subentry id → its meter entities) that the Energy dashboard puts upstream.

    Returns None when the meter is not in the Energy dashboard, has no upstream device,
    the upstream device is the main meter, or no candidate measures the upstream device.
    """
    registry = er.async_get(hass)

    def device_of(entity_id: str) -> str | None:
        entry = registry.async_get(entity_id)
        return entry.device_id if entry else None

    def entity_keys(entity_ids: Iterable[str]) -> set[str]:
        return {e for e in entity_ids if e}

    def device_keys(entity_ids: Iterable[str]) -> set[str]:
        return {d for e in entity_ids if e and (d := device_of(e))}

    def stats(dev: dict[str, Any]) -> list[str]:
        return [dev[k] for k in ("stat_consumption", "stat_rate") if dev.get(k)]

    meters = list(meter_entities)
    mine = [d for d in devices if entity_keys(stats(d)) & entity_keys(meters)]
    if not mine:
        # Fall back to the device, but only when that is unambiguous: one physical meter
        # (a Shelly Pro 3EM) can feed several Energy dashboard entries.
        mine = [d for d in devices if device_keys(stats(d)) & device_keys(meters)]
        if len(mine) != 1:
            return None
    upstream = {d.get("included_in_stat") for d in mine} - {None}
    if len(upstream) != 1:
        return None
    parent_stat = upstream.pop()
    parent = next((d for d in devices if d.get("stat_consumption") == parent_stat), {"stat_consumption": parent_stat})
    parent_entities = entity_keys(stats(parent))
    parent_devices = device_keys(stats(parent))
    if parent_devices & device_keys(main_sources) or parent_entities & entity_keys(main_sources):
        return None   # "upstream" is the main meter itself
    matches = [
        sub_id
        for sub_id, entities in candidates.items()
        if entity_keys(entities) & parent_entities or device_keys(entities) & parent_devices
    ]
    return matches[0] if len(matches) == 1 else None
