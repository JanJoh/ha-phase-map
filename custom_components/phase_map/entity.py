"""Shared entity base."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .engine import PhaseMapEngine


def fuse_device(engine: PhaseMapEngine) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, engine.entry.entry_id)},
        name=engine.entry.title,
        manufacturer="HA Phase Map",
        model=f"{len(engine.phases)} × {engine.fuse_a:g} A",
        entry_type=DeviceEntryType.SERVICE,
    )


def detector_device(engine: PhaseMapEngine, subentry_id: str, name: str) -> DeviceInfo:
    """The device of one unmetered-load detector (e.g. "Sauna"), under the fuse device."""
    return DeviceInfo(
        identifiers={(DOMAIN, subentry_id)},
        name=name,
        manufacturer="HA Phase Map",
        model="Unmetered load detector",
        entry_type=DeviceEntryType.SERVICE,
        via_device_id=engine.device_id,
    )


class PhaseMapEntity(Entity):
    """Updates whenever the engine recomputes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: PhaseMapEngine) -> None:
        self.engine = engine

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE.format(self.engine.entry.entry_id), self._handle_update)
        )
        self._handle_update()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
