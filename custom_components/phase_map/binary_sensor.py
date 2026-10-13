"""Binary sensors: overload risk per phase, and unmetered-load detectors."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import PhaseMapConfigEntry
from .const import (
    CONF_DETECT_PHASES,
    CONF_MIN_CURRENT_A,
    CONF_NAME,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_OVERLOAD_DELAY,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_OFF_DELAY,
    DEFAULT_ON_DELAY,
    DEFAULT_OVERLOAD_DELAY,
    SUBENTRY_DETECTOR,
)
from .engine import PhaseMapEngine
from .entity import PhaseMapEntity, detector_device, fuse_device


async def async_setup_entry(
    hass: HomeAssistant, entry: PhaseMapConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    delay = float(entry.options.get(CONF_OVERLOAD_DELAY, DEFAULT_OVERLOAD_DELAY))
    async_add_entities([OverloadSensor(engine, p, delay) for p in engine.phases])
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_DETECTOR:
            async_add_entities([DetectorSensor(engine, sub.subentry_id, dict(sub.data))],
                               config_subentry_id=sub.subentry_id)


class Debounced:
    """A condition that must hold for `on_delay` to turn on and fail for `off_delay` to turn off."""

    def __init__(self, hass: HomeAssistant, on_delay: float, off_delay: float, changed: Callable[[], None]) -> None:
        self.hass = hass
        self.on_delay = on_delay
        self.off_delay = off_delay
        self.changed = changed
        self.is_on = False
        self._pending: bool | None = None
        self._cancel: CALLBACK_TYPE | None = None

    @callback
    def feed(self, condition: bool) -> None:
        if condition == self.is_on:
            self._clear()
            return
        if self._pending == condition:
            return  # already waiting for this change
        self._clear()
        delay = self.on_delay if condition else self.off_delay
        if delay <= 0:
            self._apply(condition)
            return
        self._pending = condition

        @callback
        def _fire(_now) -> None:
            self._apply(condition)

        self._cancel = async_call_later(self.hass, delay, _fire)

    @callback
    def _apply(self, value: bool) -> None:
        self._cancel = None
        self._pending = None
        if value != self.is_on:
            self.is_on = value
            self.changed()

    def _clear(self) -> None:
        if self._cancel:
            self._cancel()
        self._cancel = None
        self._pending = None

    def stop(self) -> None:
        self._clear()


class OverloadSensor(PhaseMapEntity, BinarySensorEntity):
    """On when a phase stays within `margin` of the fuse rating for `delay` seconds."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "overload"

    def __init__(self, engine: PhaseMapEngine, phase: str, delay: float) -> None:
        super().__init__(engine)
        self._phase = phase
        self._attr_translation_placeholders = {"phase": phase}
        self._attr_unique_id = f"{engine.entry.entry_id}_{phase}_overload"
        self._attr_device_info = fuse_device(engine)
        self._state = Debounced(engine.hass, delay, 30, self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._state.stop()

    @callback
    def _handle_update(self) -> None:
        ph = self.engine.snapshot.get("phases", {}).get(self._phase, {})
        current = ph.get("current_a")
        self._state.feed(current is not None and current >= self.engine.fuse_a - self.engine.margin_a)
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._state.is_on

    @property
    def extra_state_attributes(self) -> dict:
        ph = self.engine.snapshot.get("phases", {}).get(self._phase, {})
        return {"phase": self._phase, "main_fuse_current": ph.get("current_a"),
                "limit": self.engine.fuse_a - self.engine.margin_a}


class DetectorSensor(PhaseMapEntity, BinarySensorEntity):
    """On while unmetered current on all its phases is at least `min_current` above normal."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "detector"
    _attr_name = None  # the entity is named after its device, e.g. "Sauna"

    def __init__(self, engine: PhaseMapEngine, subentry_id: str, data: dict) -> None:
        super().__init__(engine)
        self._subentry_id = subentry_id
        self._name = data.get(CONF_NAME, "Detector")
        self._phases = [p for p in data.get(CONF_DETECT_PHASES, engine.phases) if p in engine.phases]
        self._min = float(data.get(CONF_MIN_CURRENT_A, DEFAULT_MIN_CURRENT_A))
        self._attr_unique_id = f"{engine.entry.entry_id}_{subentry_id}_detector"
        self._attr_device_info = detector_device(engine, subentry_id, self._name)
        self._state = Debounced(
            engine.hass,
            float(data.get(CONF_ON_DELAY, DEFAULT_ON_DELAY)),
            float(data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY)),
            self._changed,
        )

    async def async_will_remove_from_hass(self) -> None:
        self._state.stop()
        self.engine.detectors.pop(self._subentry_id, None)

    @callback
    def _changed(self) -> None:
        self._publish()
        self.async_write_ha_state()
        self.engine.notify()

    def _publish(self) -> None:
        self.engine.detectors[self._subentry_id] = {
            "id": self._subentry_id, "name": self._name, "phases": self._phases, "on": self._state.is_on,
        }

    @callback
    def _handle_update(self) -> None:
        reading = self.engine.detector_reading(self._subentry_id)
        self._state.feed(bool(reading and reading["heating"]))
        self._publish()
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._state.is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "phases": self._phases,
            "min_current": self._min,
            "unmetered_above_normal": {p: None if (e := self.engine.excess(p)) is None else round(e, 2)
                                       for p in self._phases},
            "typical_unmetered": {p: round(self.engine.baseline.get(p, 0.0), 2) for p in self._phases},
        }
