"""Phases of a multi-phase load are verified once they have been seen carrying it."""

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from .conftest import SOURCES, set_amps

EV = {"L1": "sensor.ev_l1", "L2": "sensor.ev_l2", "L3": "sensor.ev_l3"}


def _entry(hass, subs):
    entry = MockConfigEntry(
        domain="phase_map", title="Main fuse",
        data={"phases": ["L1", "L2", "L3"], "voltage": 230.0, "fuse_a": 20.0, "sources": SOURCES},
        subentries_data=subs, unique_id="sensor.main_l1",
    )
    entry.add_to_hass(hass)
    return entry


def _row(entry, load_id):
    return next(r for r in entry.runtime_data.payload()["loads"] if r["id"] == load_id)


async def _setup(hass, subs, peaks=None):
    async def fake_fetch(hass_, units, voltage, days, period, key="mean"):
        if key == "max":
            return {e: ({0.0: v} if v is not None else {}) for e, v in (peaks or {}).items() if e in units}
        return {e: {} for e in units}

    entry = _entry(hass, subs)
    with patch("custom_components.phase_map.engine.async_fetch_means", side_effect=fake_fetch):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_one_phase_car_on_three_phase_charger(hass: HomeAssistant) -> None:
    for e in SOURCES.values():
        set_amps(hass, e, 3.0)
    for e in EV.values():
        set_amps(hass, e, 0.0)
    sub = ConfigSubentryData(data={"name": "EV", "channels": EV}, subentry_id="sub_ev", subentry_type="load",
                             title="EV", unique_id=None)
    # history: the hybrid has only ever charged on L1
    entry = await _setup(hass, [sub], peaks={"sensor.ev_l1": 16.0, "sensor.ev_l2": 0.0, "sensor.ev_l3": 0.0})
    assert _row(entry, "sub_ev")["verified"] == ["L1"]
    # a 3-phase car charges for the first time
    set_amps(hass, "sensor.ev_l2", 10.0)
    set_amps(hass, "sensor.ev_l3", 0.05)               # meter noise, does not count
    await hass.async_block_till_done()
    assert _row(entry, "sub_ev")["verified"] == ["L1", "L2"]


async def test_single_phase_loads_have_no_verification(hass: HomeAssistant) -> None:
    for e in SOURCES.values():
        set_amps(hass, e, 3.0)
    set_amps(hass, "sensor.kettle", 0.0)
    entry = await _setup(hass, [ConfigSubentryData(data={"name": "Kettle", "channels": {"L2": "sensor.kettle"}},
                                                   subentry_id="sub_k", subentry_type="load", title="Kettle",
                                                   unique_id=None)])
    assert "verified" not in _row(entry, "sub_k")


async def test_one_meter_load_verified_from_main_fuse(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    for e in SOURCES.values():
        set_amps(hass, e, 3.0)
    set_amps(hass, "sensor.cee", 0.0)
    sub = ConfigSubentryData(data={"name": "CEE", "channels": {}, "meter": "sensor.cee",
                                   "connected_phases": ["L1", "L2", "L3"], "draw": "equal"},
                             subentry_id="sub_cee", subentry_type="load", title="CEE", unique_id=None)
    entry = await _setup(hass, [sub])
    assert _row(entry, "sub_cee")["verified"] == []
    # a welder on L1 + L2 only, switched on and off twice
    for _ in range(2):
        for cee, l1, l2 in ((12.0, 9.0, 9.0), (0.0, 3.0, 3.0)):
            freezer.tick(timedelta(seconds=30))
            async_fire_time_changed(hass)
            set_amps(hass, "sensor.main_l1", l1)
            set_amps(hass, "sensor.main_l2", l2)
            set_amps(hass, "sensor.cee", cee)
            await hass.async_block_till_done()
            freezer.tick(timedelta(seconds=11))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
    assert _row(entry, "sub_cee")["verified"] == ["L1", "L2"]
