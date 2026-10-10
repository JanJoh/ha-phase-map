"""Websocket API used by the phase-bus card.

- phase_map/entries                  → configured main fuses
- phase_map/subscribe {entry_id}     → live layout and values, pushed on every change
- phase_map/average {entry_id, days} → average power per phase and per load from statistics
- phase_map/suggest_phase {entry_id, entity_id, days} → which phase a meter is probably on
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import voluptuous as vol

from .const import DOMAIN, SIGNAL_UPDATE
from .detection import async_fetch_means, async_suggest_phase


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_entries)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_average)
    websocket_api.async_register_command(hass, ws_suggest_phase)


def _engine(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        return None
    return getattr(entry, "runtime_data", None)


@websocket_api.websocket_command({vol.Required("type"): "phase_map/entries"})
@callback
def ws_entries(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entries = [
        {"entry_id": e.entry_id, "title": e.title}
        for e in hass.config_entries.async_entries(DOMAIN)
        if getattr(e, "runtime_data", None) is not None
    ]
    connection.send_result(msg["id"], entries)


@websocket_api.websocket_command({vol.Required("type"): "phase_map/subscribe", vol.Required("entry_id"): str})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry_id = msg["entry_id"]
    if _engine(hass, entry_id) is None:
        connection.send_error(msg["id"], "not_found", "No loaded HA Phase Map entry with that id")
        return

    @callback
    def forward() -> None:
        engine = _engine(hass, entry_id)
        if engine is not None:
            connection.send_message(websocket_api.event_message(msg["id"], engine.payload()))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATE.format(entry_id), forward)
    connection.send_result(msg["id"])
    forward()


@websocket_api.websocket_command(
    {
        vol.Required("type"): "phase_map/average",
        vol.Required("entry_id"): str,
        vol.Optional("days", default=10): vol.All(vol.Coerce(float), vol.Range(min=1, max=365)),
    }
)
@websocket_api.async_response
async def ws_average(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    engine = _engine(hass, msg["entry_id"])
    if engine is None:
        connection.send_error(msg["id"], "not_found", "No loaded HA Phase Map entry with that id")
        return
    units: dict[str, str | None] = {}
    for entity_id in engine.tracked_entities:
        state = hass.states.get(entity_id)
        units[entity_id] = state.attributes.get("unit_of_measurement") if state else None
    try:
        means = await async_fetch_means(hass, units, engine.voltage, msg["days"], "hour")
    except Exception as err:  # recorder not available
        connection.send_error(msg["id"], "no_statistics", str(err))
        return

    def avg_amps(entity_id: str) -> float | None:
        series = means.get(entity_id) or {}
        return sum(series.values()) / len(series) if series else None

    v = engine.voltage
    loads = []
    metered = {p: 0.0 for p in engine.phases}
    for load in engine.loads:
        w = {}
        for p, e in load.channels.items():
            a = avg_amps(e)
            w[p] = None if a is None else round(max(a, 0.0) * v)
            if a is not None:
                metered[p] += max(a, 0.0)
        loads.append({"id": load.subentry_id, "w": w})
    phases = {}
    for p in engine.phases:
        a = avg_amps(engine.sources.get(p, ""))
        phases[p] = {
            "total_w": None if a is None else round(abs(a) * v),
            "unmetered_w": None if a is None else round((abs(a) - metered[p]) * v),
        }
    connection.send_result(msg["id"], {"days": msg["days"], "phases": phases, "loads": loads})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "phase_map/suggest_phase",
        vol.Required("entry_id"): str,
        vol.Required("entity_id"): str,
        vol.Optional("days", default=10): vol.All(vol.Coerce(float), vol.Range(min=1, max=10)),
    }
)
@websocket_api.async_response
async def ws_suggest_phase(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    engine = _engine(hass, msg["entry_id"])
    if engine is None:
        connection.send_error(msg["id"], "not_found", "No loaded HA Phase Map entry with that id")
        return
    result = await async_suggest_phase(hass, msg["entity_id"], engine.sources, engine.voltage, msg["days"])
    connection.send_result(msg["id"], result.as_dict())
