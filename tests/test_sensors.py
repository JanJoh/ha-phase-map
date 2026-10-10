"""Live computations, overload warning and the unmetered-load detector."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import make_entry, set_amps


async def _setup(hass):
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_unmetered_and_headroom(hass: HomeAssistant, main_fuse_states) -> None:
    await _setup(hass)
    # L2: 12 A at the fuse, dishwasher 2070 W = 9 A → 3 A unmetered, 8 A headroom
    assert float(hass.states.get("sensor.main_fuse_l2_unmetered_current").state) == 3.0
    assert float(hass.states.get("sensor.main_fuse_l2_headroom").state) == 8.0
    assert float(hass.states.get("sensor.main_fuse_l1_unmetered_current").state) == 3.0
    set_amps(hass, "sensor.oven", 3143, "W")     # 13.67 A on L3
    set_amps(hass, "sensor.main_l3", 15.7)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.main_fuse_l3_unmetered_current").state) == 2.03
    assert hass.states.get("sensor.main_fuse_l3_unmetered_current").attributes["metered_current"] == 13.67


async def test_unavailable_source(hass: HomeAssistant, main_fuse_states) -> None:
    await _setup(hass)
    hass.states.async_set("sensor.main_l1", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.main_fuse_l1_unmetered_current").state == "unavailable"


async def test_overload_with_delay(hass: HomeAssistant, main_fuse_states, freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "off"
    set_amps(hass, "sensor.main_l2", 19.5)       # within 1 A of 20 A
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "off"
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "on"
    set_amps(hass, "sensor.main_l2", 10)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "off"


async def test_detector(hass: HomeAssistant, main_fuse_states, freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    det = "binary_sensor.sauna_running"
    assert hass.states.get(det).state == "off"
    # sauna on: +11.5 A unmetered on every phase
    set_amps(hass, "sensor.main_l1", 14.5)
    set_amps(hass, "sensor.main_l2", 23.5)
    set_amps(hass, "sensor.main_l3", 13.5)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "off"           # on-delay 60 s not reached
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "on"
    # thermostat pauses the heater for 2 minutes: stays on (off-delay 300 s)
    for entity, value in (("sensor.main_l1", 3.0), ("sensor.main_l2", 12.0), ("sensor.main_l3", 2.0)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "on"
    freezer.tick(timedelta(seconds=200))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "off"


async def test_detector_needs_all_phases(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    await _setup(hass)
    set_amps(hass, "sensor.main_l1", 14.5)       # only L1 rises (e.g. a single-phase heater)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "off"


async def test_reload_on_new_subentry(hass: HomeAssistant, main_fuse_states) -> None:
    entry = await _setup(hass)
    assert len(entry.runtime_data.loads) == 4
    assert hass.config_entries.async_remove_subentry(entry, "sub_oven")
    await hass.async_block_till_done()
    assert len(entry.runtime_data.loads) == 3
