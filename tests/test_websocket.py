"""Websocket API for the card."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import make_entry, set_amps


async def _setup(hass):
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entries_and_subscribe(hass: HomeAssistant, hass_ws_client, main_fuse_states) -> None:
    entry = await _setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "phase_map/entries"})
    msg = await client.receive_json()
    assert msg["result"] == [{"entry_id": entry.entry_id, "title": "Main fuse"}]

    await client.send_json_auto_id({"type": "phase_map/subscribe", "entry_id": entry.entry_id})
    msg = await client.receive_json()
    assert msg["success"]
    msg = await client.receive_json()
    payload = msg["event"]
    assert payload["phases_order"] == ["L1", "L2", "L3"]
    assert payload["phases"]["L2"]["unmetered_a"] == 3.0
    names = {l["name"]: l for l in payload["loads"]}
    assert names["Dishwasher"]["w"] == {"L2": 2070}
    assert names["Sauna"]["estimated"] is True and names["Sauna"]["estimated_w"] == 8000
    assert names["EV charger"]["phases"] == ["L1", "L2", "L3"]
    assert payload["detectors"][0]["name"] == "Sauna running"
    assert payload["detectors"][0]["power_w"] == 0 and payload["detectors"][0]["w"] == {"L1": 0, "L2": 0, "L3": 0}
    # Entity ids the card opens on a click
    assert names["Dishwasher"]["entity_id"] == "sensor.dishwasher"
    assert names["EV charger"]["entity_id"] == "sensor.ev_l1"
    assert names["Sauna"]["entity_id"] is None
    assert payload["phases"]["L2"]["unmetered_entity_id"] == "sensor.main_fuse_l2_unmetered_current"
    assert payload["detectors"][0]["entity_id"].startswith("sensor.") and payload["detectors"][0]["entity_id"].endswith("_power")

    set_amps(hass, "sensor.main_l2", 13.0)
    msg = await client.receive_json()
    assert msg["event"]["phases"]["L2"]["unmetered_a"] == 4.0


async def test_subscribe_unknown(hass: HomeAssistant, hass_ws_client) -> None:
    assert await async_setup_component(hass, "phase_map", {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "phase_map/subscribe", "entry_id": "nope"})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "not_found"


async def test_average(hass: HomeAssistant, hass_ws_client, main_fuse_states) -> None:
    entry = await _setup(hass)
    hour = 3600.0
    means = {
        "sensor.main_l1": {0: 3.0, hour: 5.0},          # avg 4 A
        "sensor.main_l2": {0: 2.0, hour: 2.0},
        "sensor.main_l3": {0: 1.0, hour: 1.0},
        "sensor.dishwasher": {0: 0.5, hour: 0.5},       # already converted to A
        "sensor.oven": {},
        "sensor.ev_l1": {0: 1.0, hour: 1.0},
        "sensor.ev_l2": {0: 0.0, hour: 0.0},
        "sensor.ev_l3": {0: 0.0, hour: 0.0},
    }
    client = await hass_ws_client(hass)
    with patch("custom_components.phase_map.websocket.async_fetch_means", return_value=means):
        await client.send_json_auto_id({"type": "phase_map/average", "entry_id": entry.entry_id, "days": 10})
        msg = await client.receive_json()
    res = msg["result"]
    assert res["phases"]["L1"] == {"total_w": 920, "unmetered_w": 690}
    loads = {l["id"]: l["w"] for l in res["loads"]}
    assert loads["sub_dish"] == {"L2": 115}
    assert loads["sub_oven"] == {"L3": None}
