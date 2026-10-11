"""HA Phase Map: which load is on which phase, headroom and unmetered load per phase."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

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


_LOGGER = logging.getLogger(__name__)
CARD_URL = "/phase_map/phase-bus-card.js"
CARD_FILE = Path(__file__).parent / "frontend" / "phase-bus-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_websocket(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled phase-bus card and load it in the frontend: no separate install.

    The URL carries a hash of the file, so browsers fetch a new card after an update.
    """
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return   # no web server (tests, safe mode): nothing to serve
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    digest = await hass.async_add_executor_job(lambda: hashlib.sha256(CARD_FILE.read_bytes()).hexdigest()[:12])
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), True)])
    add_extra_js_url(hass, f"{CARD_URL}?v={digest}")
    _LOGGER.debug("Serving the phase-bus card at %s", CARD_URL)


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
