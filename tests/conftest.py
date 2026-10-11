"""Shared fixtures."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phase_map.const import DOMAIN

SOURCES = {"L1": "sensor.main_l1", "L2": "sensor.main_l2", "L3": "sensor.main_l3"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_update_throttle(request, monkeypatch):
    """Recompute on every change in tests, unless a test is about the throttle itself."""
    if "throttle" not in request.node.name:
        monkeypatch.setattr("custom_components.phase_map.engine.UPDATE_INTERVAL_S", 0)


def set_amps(hass: HomeAssistant, entity_id: str, value: float, unit: str = "A") -> None:
    hass.states.async_set(entity_id, str(value), {"unit_of_measurement": unit,
                                                 "device_class": "current" if unit in ("A", "mA") else "power"})


@pytest.fixture
def main_fuse_states(hass: HomeAssistant):
    set_amps(hass, "sensor.main_l1", 3.0)
    set_amps(hass, "sensor.main_l2", 12.0)
    set_amps(hass, "sensor.main_l3", 2.0)
    set_amps(hass, "sensor.dishwasher", 2070, "W")       # 9 A on L2
    set_amps(hass, "sensor.oven", 0, "W")                # L3
    set_amps(hass, "sensor.ev_l1", 0.0)                  # 3-phase charger channels
    set_amps(hass, "sensor.ev_l2", 0.0)
    set_amps(hass, "sensor.ev_l3", 0.0)


def make_entry(hass: HomeAssistant, with_subentries: bool = True) -> MockConfigEntry:
    subs = []
    if with_subentries:
        subs = [
            ConfigSubentryData(data={"name": "Dishwasher", "channels": {"L2": "sensor.dishwasher"}},
                               subentry_id="sub_dish", subentry_type="load", title="Dishwasher", unique_id=None),
            ConfigSubentryData(data={"name": "Oven", "channels": {"L3": "sensor.oven"}},
                               subentry_id="sub_oven", subentry_type="load", title="Oven", unique_id=None),
            ConfigSubentryData(data={"name": "EV charger", "channels": {"L1": "sensor.ev_l1", "L2": "sensor.ev_l2",
                                                                        "L3": "sensor.ev_l3"}},
                               subentry_id="sub_ev", subentry_type="load", title="EV charger", unique_id=None),
            ConfigSubentryData(data={"name": "Sauna", "channels": {}, "estimated_phases": ["L1", "L2", "L3"],
                                     "estimated_w": 8000},
                               subentry_id="sub_sauna", subentry_type="load", title="Sauna", unique_id=None),
            ConfigSubentryData(data={"name": "Sauna running", "detect_phases": ["L1", "L2", "L3"],
                                     "min_current_a": 6, "on_delay_s": 60, "off_delay_s": 300},
                               subentry_id="sub_det", subentry_type="detector", title="Sauna running", unique_id=None),
        ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Main fuse",
        data={"phases": ["L1", "L2", "L3"], "voltage": 230.0, "fuse_a": 20.0, "sources": SOURCES},
        options={"margin_a": 1.0, "overload_delay_s": 60},
        subentries_data=subs,
        unique_id="sensor.main_l1",
    )
    entry.add_to_hass(hass)
    return entry
