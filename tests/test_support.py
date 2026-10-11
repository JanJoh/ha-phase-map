"""Throttled updates, the diagnostics download and the bundled card."""

from datetime import timedelta
import hashlib
import json
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.phase_map.diagnostics import async_get_config_entry_diagnostics

from .conftest import make_entry, set_amps

FRONTEND = Path(__file__).parent.parent / "custom_components" / "phase_map" / "frontend"


async def _setup(hass):
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_throttle_at_most_once_per_second(hass: HomeAssistant, main_fuse_states,
                                                freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass)
    pushes = []
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    async_dispatcher_connect(hass, f"phase_map_update_{entry.entry_id}", lambda: pushes.append(1))
    freezer.tick(timedelta(seconds=5))
    for value in (4.0, 5.0, 6.0, 7.0):                   # four changes within a second
        set_amps(hass, "sensor.main_l1", value)
        await hass.async_block_till_done()
    assert len(pushes) == 1                               # the first one at once ...
    assert float(hass.states.get("sensor.main_fuse_l1_unmetered_current").state) == 4.0
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(pushes) == 2                               # ... and the latest value at the end
    assert float(hass.states.get("sensor.main_fuse_l1_unmetered_current").state) == 7.0


async def test_diagnostics(hass: HomeAssistant, main_fuse_states) -> None:
    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["fuse_a"] == 20.0
    assert {s["title"] for s in diag["subentries"]} >= {"Dishwasher", "EV charger", "Sauna running"}
    assert diag["meters"]["sensor.dishwasher"]["unit"] == "W"
    assert "sub_dish" in diag["phase_check"] and diag["phase_check"]["sub_dish"]["votes"] == []
    assert diag["verified_phases"]["sub_ev"] == []
    assert diag["snapshot"]["phases"]["L2"]["unmetered_a"] == 3.0
    json.dumps(diag)                                      # must be serialisable


def test_bundled_card_matches_its_record() -> None:
    """Update the card with scripts/sync_card.sh, never by hand."""
    record = json.loads((FRONTEND / "card.json").read_text())
    card = (FRONTEND / "phase-bus-card.js").read_bytes()
    assert hashlib.sha256(card).hexdigest() == record["sha256"]
    assert f'const CARD_VERSION = "{record["card_version"]}";'.encode() in card
