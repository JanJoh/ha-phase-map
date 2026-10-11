"""Nested meters: a garage meter with meters of its own behind it."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phase_map.detection import Suggestion
from custom_components.phase_map.energy_prefs import suggest_parent
from custom_components.phase_map.engine import LoadModel
from custom_components.phase_map.hierarchy import effective_parents, split

from .conftest import SOURCES, set_amps

GARAGE = {"L1": "sensor.garage_l1", "L2": "sensor.garage_l2", "L3": "sensor.garage_l3"}
CEE = {"L1": "sensor.cee_l1", "L2": "sensor.cee_l2", "L3": "sensor.cee_l3"}


def _loads(**parents):
    return [
        LoadModel("garage", "Garage", GARAGE, parent=parents.get("garage")),
        LoadModel("cee", "CEE", CEE, parent=parents.get("cee", "garage")),
        LoadModel("heater", "Heater", {"L2": "sensor.heater"}, parent=parents.get("heater", "garage")),
        LoadModel("dish", "Dishwasher", {"L2": "sensor.dish"}),
    ]


def test_split_counts_children_once() -> None:
    loads = _loads()
    parents = effective_parents(loads)
    amps = {
        "garage": {"L1": 10.0, "L2": 14.0, "L3": 10.0},
        "cee": {"L1": 8.0, "L2": 8.0, "L3": 8.0},
        "heater": {"L2": 4.0},
        "dish": {"L2": 9.0},
    }
    metered, rest = split(loads, amps, parents, ["L1", "L2", "L3"])
    # The main fuse sees the garage once and the dishwasher, never the CEE or heater again
    assert metered == {"L1": 10.0, "L2": 23.0, "L3": 10.0}
    # What the garage meter measures beyond its own children: lights, sockets
    assert rest == {"garage": {"L1": 2.0, "L2": 2.0, "L3": 2.0}}


def test_unavailable_parent_phase() -> None:
    loads = _loads()
    amps = {"garage": {"L1": None, "L2": 6.0, "L3": 1.0}, "cee": {"L1": 0.0, "L2": 0.0, "L3": 0.0},
            "heater": {"L2": 4.0}, "dish": {"L2": None}}
    metered, rest = split(loads, amps, effective_parents(loads), ["L1", "L2", "L3"])
    assert metered == {"L1": 0.0, "L2": 6.0, "L3": 1.0}
    assert rest["garage"] == {"L1": None, "L2": 2.0, "L3": 1.0}


def test_invalid_parents_fall_back_to_main_fuse() -> None:
    # missing parent, estimated parent, phase the parent does not measure, and a cycle
    loads = [
        LoadModel("a", "A", {"L1": "sensor.a"}, parent="gone"),
        LoadModel("est", "Sauna", {}, 8000, ["L1", "L2", "L3"]),
        LoadModel("b", "B", {"L1": "sensor.b"}, parent="est"),
        LoadModel("c", "C", {"L3": "sensor.c"}, parent="d"),
        LoadModel("d", "D", {"L1": "sensor.d"}),
        LoadModel("x", "X", {"L1": "sensor.x"}, parent="y"),
        LoadModel("y", "Y", {"L1": "sensor.y"}, parent="x"),
    ]
    parents = effective_parents(loads)
    assert parents["a"] is None and parents["b"] is None and parents["c"] is None
    assert [parents["x"], parents["y"]].count(None) >= 1    # the cycle is broken


def _entry(hass: HomeAssistant, garage_children: bool = True) -> MockConfigEntry:
    subs = [
        ConfigSubentryData(data={"name": "Garage", "channels": GARAGE}, subentry_id="sub_garage",
                           subentry_type="load", title="Garage", unique_id=None),
        ConfigSubentryData(data={"name": "Dishwasher", "channels": {"L2": "sensor.dishwasher"}},
                           subentry_id="sub_dish", subentry_type="load", title="Dishwasher", unique_id=None),
    ]
    if garage_children:
        subs.append(ConfigSubentryData(data={"name": "CEE", "channels": CEE, "parent": "sub_garage"},
                                       subentry_id="sub_cee", subentry_type="load", title="CEE", unique_id=None))
    entry = MockConfigEntry(
        domain="phase_map", title="Main fuse",
        data={"phases": ["L1", "L2", "L3"], "voltage": 230.0, "fuse_a": 20.0, "sources": SOURCES},
        subentries_data=subs, unique_id="sensor.main_l1",
    )
    entry.add_to_hass(hass)
    return entry


async def test_sensors_and_payload(hass: HomeAssistant, main_fuse_states) -> None:
    for e, a in (("sensor.garage_l1", 2.0), ("sensor.garage_l2", 1.0), ("sensor.garage_l3", 9.0),
                 ("sensor.cee_l1", 0.0), ("sensor.cee_l2", 0.0), ("sensor.cee_l3", 8.0)):
        set_amps(hass, e, a)
    set_amps(hass, "sensor.main_l3", 11.0)
    entry = _entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # L3: 11 A at the fuse, garage 9 A (which includes the CEE's 8 A) → 2 A unmetered
    assert float(hass.states.get("sensor.main_fuse_l3_unmetered_current").state) == 2.0
    payload = entry.runtime_data.payload()
    rows = {row["id"]: row for row in payload["loads"]}
    assert rows["sub_cee"]["parent"] == "sub_garage"
    assert rows["sub_garage"]["parent"] is None
    assert rows["sub_garage"]["rest_w"] == {"L1": 460, "L2": 230, "L3": 230}
    assert "rest_w" not in rows["sub_dish"]


async def test_add_child_meter(hass: HomeAssistant, main_fuse_states) -> None:
    set_amps(hass, "sensor.heater", 0, "W")
    entry = _entry(hass, garage_children=False)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "load"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "meter"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Heater", "entity": "sensor.heater"}
    )
    assert result["step_id"] == "parent"
    with patch("custom_components.phase_map.config_flow.async_suggest_phase",
               return_value=Suggestion(None, "none", 0, reason="too_few_steps")) as suggest:
        result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"parent": "sub_garage"})
    # compared with the garage's channels, not the main fuse
    assert suggest.call_args.args[2] == GARAGE
    assert result["step_id"] == "phase_manual"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"phase": "L2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(s for s in entry.subentries.values() if s.title == "Heater")
    assert sub.data == {"name": "Heater", "channels": {"L2": "sensor.heater"}, "parent": "sub_garage"}


async def test_child_outside_parent_phases(hass: HomeAssistant, main_fuse_states) -> None:
    entry = _entry(hass, garage_children=False)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "load"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "estimated"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Welder", "estimated_phases": ["L1", "L3"], "estimated_w": 3000}
    )
    # The dishwasher only measures L2, so a load on L1 + L3 cannot sit behind it
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"parent": "sub_dish"})
    assert result["errors"] == {"parent": "outside_parent"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"parent": "sub_garage"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_cannot_create_cycle(hass: HomeAssistant, main_fuse_states) -> None:
    entry = _entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_RECONFIGURE, "subentry_id": "sub_garage"}
    )
    options = [o["value"] for o in result["data_schema"].schema["parent"].config["options"]]
    assert "sub_cee" not in options and "sub_garage" not in options    # not itself, not its child
    assert "sub_dish" in options


async def test_parent_from_energy_dashboard(hass: HomeAssistant) -> None:
    config = MockConfigEntry(domain="test")
    config.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    def meter(name: str, *keys: str) -> None:
        device = devices.async_get_or_create(config_entry_id=config.entry_id, identifiers={("test", name)})
        for key in keys:
            entities.async_get_or_create("sensor", "test", f"{name}_{key}", suggested_object_id=f"{name}_{key}",
                                         device_id=device.id)

    meter("garage", "energy", "power", "l1_power", "l2_power", "l3_power")
    meter("heater", "energy", "power")
    meter("main", "energy", "l1", "l2", "l3")
    prefs = [
        {"stat_consumption": "sensor.garage_energy", "included_in_stat": "sensor.main_energy"},
        {"stat_consumption": "sensor.heater_energy", "stat_rate": "sensor.heater_power",
         "included_in_stat": "sensor.garage_energy"},
    ]
    candidates = {"sub_garage": ["sensor.garage_l1_power", "sensor.garage_l2_power", "sensor.garage_l3_power"],
                  "sub_dish": ["sensor.dishwasher"]}
    main = ["sensor.main_l1", "sensor.main_l2", "sensor.main_l3"]
    # the heater's power sensor is in the Energy dashboard; its upstream is the garage device
    assert suggest_parent(hass, prefs, ["sensor.heater_power"], candidates, main) == "sub_garage"
    # the garage itself sits behind the main meter: no parent
    assert suggest_parent(hass, prefs, ["sensor.garage_l1_power"], candidates, main) is None
    # unknown meter
    assert suggest_parent(hass, prefs, ["sensor.dishwasher"], candidates, main) is None
