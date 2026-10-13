"""Per-phase sensors (unmetered current, headroom to the fuse), and each detector's estimated power and energy."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import PhaseMapConfigEntry
from .const import CONF_NAME, SUBENTRY_DETECTOR
from .engine import PhaseMapEngine
from .entity import PhaseMapEntity, detector_device, fuse_device

# The energy sensor also adds up on this interval, so a steady load counts while no meter changes
ENERGY_TICK = timedelta(seconds=60)


async def async_setup_entry(
    hass: HomeAssistant, entry: PhaseMapConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    entities: list[SensorEntity] = []
    for phase in engine.phases:
        entities.append(PhaseSensor(engine, phase, "unmetered_current", "unmetered_a"))
        entities.append(PhaseSensor(engine, phase, "headroom", "headroom_a"))
    async_add_entities(entities)
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_DETECTOR:
            name = sub.data.get(CONF_NAME, sub.title)
            async_add_entities(
                [DetectorPowerSensor(engine, sub.subentry_id, name), DetectorEnergySensor(engine, sub.subentry_id, name)],
                config_subentry_id=sub.subentry_id,
            )


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


class DetectorPowerSensor(PhaseMapEntity, SensorEntity):
    """What a detected load draws now, estimated from unmetered current above normal.

    0 W while the detector is off, and while it is on but its load pauses (a sauna thermostat).
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_translation_key = "detector_power"

    def __init__(self, engine: PhaseMapEngine, subentry_id: str, name: str) -> None:
        super().__init__(engine)
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{engine.entry.entry_id}_{subentry_id}_power"
        self._attr_device_info = detector_device(engine, subentry_id, name)

    @property
    def native_value(self) -> float | None:
        reading = self.engine.detector_reading(self._subentry_id)
        return None if reading is None else reading["power_w"]

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict:
        reading = self.engine.detector_reading(self._subentry_id) or {}
        return {"estimated": True, "current": reading.get("a"), "drawing": reading.get("heating")}


class DetectorEnergySensor(PhaseMapEntity, RestoreSensor):
    """The detected load's energy, the power sensor's reading added up over time (for the Energy dashboard)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_translation_key = "detector_energy"

    def __init__(self, engine: PhaseMapEngine, subentry_id: str, name: str) -> None:
        super().__init__(engine)
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{engine.entry.entry_id}_{subentry_id}_energy"
        self._attr_device_info = detector_device(engine, subentry_id, name)
        self._kwh = 0.0
        self._last_w: float | None = None
        self._last_t: float | None = None

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_sensor_data()) is not None and last.native_value is not None:
            try:
                self._kwh = float(last.native_value)
            except (TypeError, ValueError):
                self._kwh = 0.0
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, ENERGY_TICK))

    @callback
    def _tick(self, _now) -> None:
        self._handle_update()

    @callback
    def _handle_update(self) -> None:
        # Left Riemann sum: the power since the last update was what it was then. Nothing is
        # counted for the time before HA started: nobody knows what ran while it was down.
        now = dt_util.utcnow().timestamp()
        if self._last_w is not None and self._last_t is not None and now > self._last_t:
            self._kwh += self._last_w * (now - self._last_t) / 3_600_000
        reading = self.engine.detector_reading(self._subentry_id)
        self._last_w = None if reading is None else reading["power_w"]
        self._last_t = now
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return round(self._kwh, 4)
