"""Per-phase sensors: unmetered current and headroom to the fuse."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PhaseMapConfigEntry
from .engine import PhaseMapEngine
from .entity import PhaseMapEntity, fuse_device


async def async_setup_entry(
    hass: HomeAssistant, entry: PhaseMapConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    entities: list[SensorEntity] = []
    for phase in engine.phases:
        entities.append(PhaseSensor(engine, phase, "unmetered_current", "unmetered_a"))
        entities.append(PhaseSensor(engine, phase, "headroom", "headroom_a"))
    async_add_entities(entities)


class PhaseSensor(PhaseMapEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_suggested_display_precision = 1

    def __init__(self, engine: PhaseMapEngine, phase: str, key: str, field: str) -> None:
        super().__init__(engine)
        self._phase = phase
        self._field = field
        self._attr_translation_key = key
        self._attr_translation_placeholders = {"phase": phase}
        self._attr_unique_id = f"{engine.entry.entry_id}_{phase}_{key}"
        self._attr_device_info = fuse_device(engine)

    @property
    def native_value(self) -> float | None:
        return self.engine.snapshot.get("phases", {}).get(self._phase, {}).get(self._field)

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict:
        ph = self.engine.snapshot.get("phases", {}).get(self._phase, {})
        if self._field == "unmetered_a":
            return {"phase": self._phase, "main_fuse_current": ph.get("current_a"),
                    "metered_current": ph.get("metered_a"), "typical_unmetered": ph.get("baseline_a")}
        return {"phase": self._phase, "fuse_rating": self.engine.fuse_a, "main_fuse_current": ph.get("current_a")}
