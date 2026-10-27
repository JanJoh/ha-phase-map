"""Adding loads and detectors."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.phase_map.detection import PhaseScore, Suggestion

from .conftest import make_entry


async def _setup(hass):
    entry = make_entry(hass, with_subentries=False)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_meter_with_suggestion(hass: HomeAssistant, main_fuse_states) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "load"), context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "meter"})
    suggestion = Suggestion("L2", "high", 47, {"L1": PhaseScore(0.0, 0.1, 47), "L2": PhaseScore(1.03, 12.0, 47),
                                                "L3": PhaseScore(0.02, 0.3, 47)}, "ok")
    with patch("custom_components.phase_map.config_flow.async_suggest_phase", return_value=suggestion):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "Dishwasher", "entity": "sensor.dishwasher"}
        )
    assert result["step_id"] == "phase"
    assert result["description_placeholders"]["phase"] == "L2"
    assert result["description_placeholders"]["steps"] == "47"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"phase": "L2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(entry.subentries.values()))
    assert sub.data == {"name": "Dishwasher", "channels": {"L2": "sensor.dishwasher"}}


async def test_meter_without_suggestion(hass: HomeAssistant, main_fuse_states) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "load"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "meter"})
    with patch("custom_components.phase_map.config_flow.async_suggest_phase",
               return_value=Suggestion(None, "none", 2, reason="too_few_steps")):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "Oven", "entity": "sensor.oven"}
        )
    assert result["step_id"] == "phase_manual"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"phase": "L3"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_multi_and_estimated(hass: HomeAssistant, main_fuse_states) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "load"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "multi"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "EV"})
    assert result["errors"] == {"base": "no_meter"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "EV", "L1": "sensor.ev_l1", "L2": "sensor.ev_l2", "L3": "sensor.ev_l3"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.subentries.async_init((entry.entry_id, "load"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "estimated"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Sauna", "estimated_phases": ["L1", "L2", "L3"], "estimated_w": 8000}
    )
    # EV is a metered load now, so the flow asks whether the sauna sits behind it
    assert result["step_id"] == "parent"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"parent": "none"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "parent" not in next(s for s in entry.subentries.values() if s.title == "Sauna").data
    names = sorted(s.title for s in entry.subentries.values())
    assert names == ["EV", "Sauna"]


async def test_reconfigure_load(hass: HomeAssistant, main_fuse_states) -> None:
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_RECONFIGURE, "subentry_id": "sub_oven"}
    )
    assert result["step_id"] == "reconfigure_meter"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Oven", "entity": "sensor.oven", "phase": "L1"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.subentries["sub_oven"].data == {"name": "Oven", "channels": {"L1": "sensor.oven"}}


async def test_detector_flow(hass: HomeAssistant, main_fuse_states) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "detector"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Sauna", "detect_phases": [], "min_current_a": 6, "on_delay_s": 60,
                            "off_delay_s": 900}
    )
    assert result["errors"] == {"detect_phases": "no_phase"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Sauna", "detect_phases": ["L1", "L2", "L3"], "min_current_a": 6,
                            "hold_current_a": 7, "on_delay_s": 60, "off_delay_s": 900}
    )
    assert result["errors"] == {"hold_current_a": "hold_above_min"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Sauna", "detect_phases": ["L1", "L2", "L3"], "min_current_a": 6,
                            "on_delay_s": 60, "off_delay_s": 900}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna") is not None


async def test_reconfigure_multi_info_entity(hass: HomeAssistant, main_fuse_states) -> None:
    """A load with a meter per phase can name the sensor the card opens on a click."""
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    row = lambda: next(r for r in entry.runtime_data.payload()["loads"] if r["id"] == "sub_ev")  # noqa: E731
    assert row()["entity_id"] == "sensor.ev_l1"
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_RECONFIGURE, "subentry_id": "sub_ev"}
    )
    assert result["step_id"] == "reconfigure" and "info_entity" in result["data_schema"].schema
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "EV charger", "L1": "sensor.ev_l1", "L2": "sensor.ev_l2", "L3": "sensor.ev_l3",
                            "info_entity": "sensor.ev_total"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.subentries["sub_ev"].data["info_entity"] == "sensor.ev_total"
    await hass.async_block_till_done()
    assert row()["entity_id"] == "sensor.ev_total"
