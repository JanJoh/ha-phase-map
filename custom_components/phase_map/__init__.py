"""HA Phase Map: which load is on which phase, headroom and unmetered load per phase."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .engine import PhaseMapEngine
from .entity import fuse_device
from .labels import async_sync_labels
from .websocket import async_register_websocket

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type PhaseMapConfigEntry = ConfigEntry[PhaseMapEngine]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PhaseMapConfigEntry) -> bool:
    engine = PhaseMapEngine(hass, entry)
    entry.runtime_data = engine
    # Register the fuse device up front so detector devices can point at it (via_device_id)
    engine.device_id = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **fuse_device(engine)).id
    await engine.async_start()
    entry.async_on_unload(engine.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_sync_labels(hass, entry)
    # Adding, editing or removing a load or detector (subentry) or changing options reloads the entry
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: PhaseMapConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PhaseMapConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
