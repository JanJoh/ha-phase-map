"""Setting up the main fuse, options and reconfigure."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.phase_map.const import DOMAIN

from .conftest import SOURCES, make_entry, set_amps


async def test_full_setup(hass: HomeAssistant, main_fuse_states) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Main fuse", "phase_count": "3", "voltage": 230, "fuse_a": 20}
    )
    assert result["step_id"] == "sources"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SOURCES)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Main fuse"
    assert result["data"] == {"phases": ["L1", "L2", "L3"], "voltage": 230.0, "fuse_a": 20.0, "sources": SOURCES}


async def test_power_sources_and_unit_check(hass: HomeAssistant) -> None:
    set_amps(hass, "sensor.p1", 900, "W")
    hass.states.async_set("sensor.energy", "12", {"unit_of_measurement": "kWh"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "House", "phase_count": "1", "voltage": 230, "fuse_a": 25}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"L1": "sensor.energy"})
    assert result["errors"] == {"L1": "unsupported_unit"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"L1": "sensor.p1"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["phases"] == ["L1"]


async def test_same_sensor_twice(hass: HomeAssistant, main_fuse_states) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "x", "phase_count": "2", "voltage": 120, "fuse_a": 100}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"L1": "sensor.main_l1", "L2": "sensor.main_l1"}
    )
    assert result["errors"] == {"base": "same_sensor_twice"}


async def test_already_configured(hass: HomeAssistant, main_fuse_states) -> None:
    make_entry(hass, with_subentries=False)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Main fuse", "phase_count": "3", "voltage": 230, "fuse_a": 20}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SOURCES)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_options(hass: HomeAssistant, main_fuse_states) -> None:
    entry = make_entry(hass, with_subentries=False)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"margin_a": 2.0, "overload_delay_s": 30,
                            "phase_labels": {"labels": False, "label_name": " Fas ", "phase_value_L1": "1",
                                             "phase_value_L2": "2", "phase_value_L3": "3"}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"margin_a": 2.0, "overload_delay_s": 30, "labels": False, "label_name": "Fas",
                             "phase_value_L1": "1", "phase_value_L2": "2", "phase_value_L3": "3",
                             "discovery": False, "discovery_min_confidence": "high"}


async def test_reconfigure(hass: HomeAssistant, main_fuse_states) -> None:
    entry = make_entry(hass, with_subentries=False)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"voltage": 230, "fuse_a": 25, **SOURCES}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.data["fuse_a"] == 25.0
