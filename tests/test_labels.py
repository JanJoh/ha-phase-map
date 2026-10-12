"""Phase labels on load devices."""

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import SOURCES, set_amps

OPTS = {"margin_a": 1.0, "overload_delay_s": 60, "labels": True, "label_name": "Fas",
        "phase_value_L1": "1", "phase_value_L2": "2", "phase_value_L3": "3"}


def _meters(hass: HomeAssistant) -> dict[str, str]:
    """Kettle plug (one device) and garage Shelly (one device, three channels)."""
    other = MockConfigEntry(domain="test")
    other.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    ids = {}
    for name, keys in (("kettle", ["power"]), ("garage", ["l1", "l2", "l3"])):
        dev = devices.async_get_or_create(config_entry_id=other.entry_id, identifiers={("test", name)})
        ids[name] = dev.id
        for key in keys:
            entities.async_get_or_create("sensor", "test", f"{name}_{key}", suggested_object_id=f"{name}_{key}",
                                         device_id=dev.id)
            set_amps(hass, f"sensor.{name}_{key}", 0.0)
    for e in SOURCES.values():
        set_amps(hass, e, 3.0)
    return ids


def _entry(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="phase_map", title="Main fuse",
        data={"phases": ["L1", "L2", "L3"], "voltage": 230.0, "fuse_a": 20.0, "sources": SOURCES},
        options=options,
        subentries_data=[
            ConfigSubentryData(data={"name": "Kettle", "channels": {"L2": "sensor.kettle_power"}},
                               subentry_id="sub_kettle", subentry_type="load", title="Kettle", unique_id=None),
            ConfigSubentryData(data={"name": "Garage", "channels": {"L1": "sensor.garage_l3", "L2": "sensor.garage_l1",
                                                                    "L3": "sensor.garage_l2"}},
                               subentry_id="sub_garage", subentry_type="load", title="Garage", unique_id=None),
        ],
        unique_id="sensor.main_l1",
    )
    entry.add_to_hass(hass)
    return entry


def _names(hass: HomeAssistant, device_id: str) -> set[str]:
    labels = lr.async_get(hass)
    return {labels.async_get_label(lid).name for lid in dr.async_get(hass).async_get(device_id).labels}


async def _setup(hass, options=OPTS):
    ids = _meters(hass)
    entry = _entry(hass, options)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, ids


async def test_labels_follow_config(hass: HomeAssistant) -> None:
    entry, ids = await _setup(hass)
    assert _names(hass, ids["kettle"]) == {"Fas: 2"}
    assert _names(hass, ids["garage"]) == {"Fas: 1", "Fas: 2", "Fas: 3"}
    # the kettle moves to L3 (e.g. after a confirmed repair): its label follows
    sub = entry.subentries["sub_kettle"]
    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "channels": {"L3": "sensor.kettle_power"}})
    await hass.async_block_till_done()
    assert _names(hass, ids["kettle"]) == {"Fas: 3"}
    # new name and values: old labels are replaced, and the ones we created are deleted
    hass.config_entries.async_update_entry(entry, options={**OPTS, "label_name": "Inkommande fas",
                                                           "phase_value_L1": "R", "phase_value_L2": "S",
                                                           "phase_value_L3": "T"})
    await hass.async_block_till_done()
    assert _names(hass, ids["kettle"]) == {"Inkommande fas: T"}
    assert lr.async_get(hass).async_get_label_by_name("Fas: 2") is None


async def test_own_labels_are_left_alone(hass: HomeAssistant) -> None:
    ids = _meters(hass)
    mine = lr.async_get(hass).async_create("Fas: 2")
    dr.async_get(hass).async_update_device(ids["kettle"], labels={mine.label_id})
    entry = _entry(hass, OPTS)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sub = entry.subentries["sub_kettle"]
    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "channels": {"L3": "sensor.kettle_power"}})
    await hass.async_block_till_done()
    # "Fas: 2" was already there before HA Phase Map, so it stays
    assert _names(hass, ids["kettle"]) == {"Fas: 2", "Fas: 3"}


async def _disable(hass, entry, choice):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
    labels = {"labels": False, "label_name": "Fas", "phase_value_L1": "1", "phase_value_L2": "2",
              "phase_value_L3": "3"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"margin_a": 1.0, "overload_delay_s": 60, "phase_labels": labels}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["description_placeholders"] == {"labels": "4", "devices": "2"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": choice})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_disable_and_remove(hass: HomeAssistant) -> None:
    entry, ids = await _setup(hass)
    await _disable(hass, entry, "remove_labels")
    assert _names(hass, ids["kettle"]) == set() and _names(hass, ids["garage"]) == set()
    assert lr.async_get(hass).async_get_label_by_name("Fas: 1") is None
    assert entry.options["labels"] is False


async def test_disable_and_keep(hass: HomeAssistant) -> None:
    entry, ids = await _setup(hass)
    await _disable(hass, entry, "keep_labels")
    assert _names(hass, ids["kettle"]) == {"Fas: 2"}
    # turning it on again later does not claim them back for removal
    hass.config_entries.async_update_entry(entry, options=OPTS)
    await hass.async_block_till_done()
    sub = entry.subentries["sub_kettle"]
    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "channels": {"L1": "sensor.kettle_power"}})
    await hass.async_block_till_done()
    assert _names(hass, ids["kettle"]) == {"Fas: 2", "Fas: 1"}


async def test_options_validation(hass: HomeAssistant) -> None:
    entry, _ = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "margin_a": 1.0, "overload_delay_s": 60,
        "phase_labels": {"labels": True, "label_name": "Fas", "phase_value_L1": "R", "phase_value_L2": "R",
                         "phase_value_L3": "T"}})
    assert result["errors"] == {"base": "same_value_twice"}
