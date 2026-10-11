"""A multi-phase load with only one meter, declared multi-phase whatever the evidence says."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phase_map.detection import PhaseScore, Suggestion
from custom_components.phase_map.engine import LoadModel, amps_for

from .conftest import SOURCES, set_amps


def test_split() -> None:
    get = {"sensor.ev": 9.0}.get
    equal = LoadModel("ev", "EV", {}, meter="sensor.ev", connected=["L1", "L2", "L3"], draw="equal")
    assert amps_for(equal, get) == {"L1": 3.0, "L2": 3.0, "L3": 3.0}
    on_l1 = LoadModel("ev", "EV", {}, meter="sensor.ev", connected=["L1", "L2", "L3"], draw="L1")
    assert amps_for(on_l1, get) == {"L1": 9.0, "L2": 0.0, "L3": 0.0}
    assert amps_for(on_l1, {}.get) == {"L1": None, "L2": None, "L3": None}
    assert on_l1.phases == ["L1", "L2", "L3"] and not on_l1.estimated


def _entry(hass: HomeAssistant, subs) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="phase_map", title="Main fuse",
        data={"phases": ["L1", "L2", "L3"], "voltage": 230.0, "fuse_a": 20.0, "sources": SOURCES},
        subentries_data=subs, unique_id="sensor.main_l1",
    )
    entry.add_to_hass(hass)
    return entry


async def test_evidence_says_l1_user_says_three_phases(hass: HomeAssistant) -> None:
    for e, a in (("sensor.main_l1", 12.0), ("sensor.main_l2", 3.0), ("sensor.main_l3", 3.0)):
        set_amps(hass, e, a)
    set_amps(hass, "sensor.charger_power", 2070, "W")       # a hybrid charging 9 A on L1
    entry = _entry(hass, [])
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "load"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "meter"})
    found = Suggestion("L1", "high", 30, {"L1": PhaseScore(1.0, 12.0, 30), "L2": PhaseScore(0.0, 0.1, 30),
                                          "L3": PhaseScore(0.0, 0.2, 30)}, "ok")
    with patch("custom_components.phase_map.config_flow.async_suggest_phase", return_value=found):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "EV charger", "entity": "sensor.charger_power"}
        )
    assert result["step_id"] == "phase"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"phase": "L1", "connected_phases": ["L2", "L3"], "draw": "phase"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(iter(entry.subentries.values()))
    assert sub.data == {"name": "EV charger", "channels": {}, "meter": "sensor.charger_power",
                        "connected_phases": ["L1", "L2", "L3"], "draw": "L1"}
    engine = entry.runtime_data
    row = next(r for r in engine.payload()["loads"] if r["id"] == sub.subentry_id)
    assert row["phases"] == ["L1", "L2", "L3"] and row["meter"] == {"draw": "L1"}
    assert row["w"] == {"L1": 2070, "L2": 0, "L3": 0}
    assert float(hass.states.get("sensor.main_fuse_l1_unmetered_current").state) == 3.0
    assert sub.subentry_id not in engine.checker.checks          # declared phases are not checked

    # A 3-phase car arrives: split equally
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id}
    )
    assert result["step_id"] == "reconfigure_meter"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {
        "name": "EV charger", "entity": "sensor.charger_power", "phase": "L1",
        "connected_phases": ["L2", "L3"], "draw": "equal"})
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert entry.subentries[sub.subentry_id].data["draw"] == "equal"
    row = next(r for r in entry.runtime_data.payload()["loads"] if r["id"] == sub.subentry_id)
    assert row["w"] == {"L1": 690, "L2": 690, "L3": 690}

    # ... and back to an ordinary single-phase load
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {
        "name": "EV charger", "entity": "sensor.charger_power", "phase": "L2", "connected_phases": []})
    assert entry.subentries[sub.subentry_id].data == {"name": "EV charger",
                                                       "channels": {"L2": "sensor.charger_power"}}


async def test_one_meter_load_as_parent(hass: HomeAssistant) -> None:
    for e in SOURCES.values():
        set_amps(hass, e, 5.0)
    set_amps(hass, "sensor.cee_total", 3.0)
    set_amps(hass, "sensor.welder", 1.0)
    entry = _entry(hass, [
        ConfigSubentryData(data={"name": "CEE", "channels": {}, "meter": "sensor.cee_total",
                                 "connected_phases": ["L1", "L2", "L3"], "draw": "equal"},
                           subentry_id="sub_cee", subentry_type="load", title="CEE", unique_id=None),
        ConfigSubentryData(data={"name": "Welder", "channels": {"L2": "sensor.welder"}, "parent": "sub_cee"},
                           subentry_id="sub_weld", subentry_type="load", title="Welder", unique_id=None),
    ])
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rows = {r["id"]: r for r in entry.runtime_data.payload()["loads"]}
    assert rows["sub_weld"]["parent"] == "sub_cee"
    # 1 A per phase from the CEE; the welder's 1 A on L2 is inside it
    assert rows["sub_cee"]["rest_w"] == {"L1": 230, "L2": 0, "L3": 230}
    assert float(hass.states.get("sensor.main_fuse_l2_unmetered_current").state) == 4.0
    # checked against the main fuse (no per-phase channels upstream), only on the CEE's phases
    check = entry.runtime_data.checker.checks["sub_weld"]
    assert check.reference == SOURCES and check.allowed == ["L1", "L2", "L3"]
