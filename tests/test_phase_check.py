"""Noticing that a load has been plugged in on another phase."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.phase_map.phase_check import LoadCheck, PhaseChecker, classify, verdict

from .conftest import SOURCES, set_amps


def test_classify() -> None:
    assert classify(9.0, {"L1": 0.1, "L2": -0.2, "L3": 8.7}) == "L3"
    assert classify(-9.0, {"L1": 0.0, "L2": 0.1, "L3": -9.1}) == "L3"        # switching off
    assert classify(9.0, {"L1": 0.1, "L2": 4.0, "L3": 8.7}) is None          # something else moved too
    assert classify(9.0, {"L1": 0.1, "L2": 0.2, "L3": 3.0}) is None          # too little response
    assert classify(0.2, {"L1": 0.2, "L2": 0.0, "L3": 0.0}) is None          # step too small


def test_verdict_needs_most_of_the_evidence() -> None:
    check = LoadCheck("k", "L2", ["L1", "L2", "L3"], dict(SOURCES), "sensor.kettle")
    for vote in ("L3", "L3", "L3", "L3"):
        check.votes.append(vote)
    assert verdict(check) is None                   # 4 votes are not enough
    check.votes.append("L2")
    assert verdict(check) is None                   # 4 of 5 for L3
    check.votes.append("L3")
    assert verdict(check) == "L3"                   # 5 of the last 6
    behind = LoadCheck("k", "L2", ["L2"], {"L2": "sensor.garage_l2"}, "sensor.kettle")
    behind.votes.extend(["L3"] * 6)
    assert verdict(behind) is None                  # cannot move outside its parent's phases


def test_confounded_event_is_skipped() -> None:
    now = [1000.0]
    changes = []
    check = LoadCheck("k", "L2", ["L1", "L2", "L3"], dict(SOURCES), "sensor.kettle")
    checker = PhaseChecker([check], changes.append, clock=lambda: now[0])
    for e in (*SOURCES.values(), "sensor.kettle", "sensor.oven"):
        checker.record(e, 0.0, 900.0)
    checker.record("sensor.main_l3", 9.0, 1000.0)
    checker.record("sensor.oven", 9.0, 1001.0)              # the oven switched on at the same time
    t = checker.step("sensor.kettle", 0.0, 9.0, 1000.0)
    assert checker.evaluate("sensor.kettle", t, 9.0, dt_util.utcnow()) is None
    assert not check.votes


def _entry(hass: HomeAssistant, portable: bool) -> MockConfigEntry:
    data = {"name": "Kettle", "channels": {"L2": "sensor.kettle"}}
    if portable:
        data["portable"] = True
    entry = MockConfigEntry(
        domain="phase_map", title="Main fuse",
        data={"phases": ["L1", "L2", "L3"], "voltage": 230.0, "fuse_a": 20.0, "sources": SOURCES},
        subentries_data=[ConfigSubentryData(data=data, subentry_id="sub_kettle", subentry_type="load",
                                            title="Kettle", unique_id=None)],
        unique_id="sensor.main_l1",
    )
    entry.add_to_hass(hass)
    return entry


async def _boil(hass: HomeAssistant, freezer: FrozenDateTimeFactory, times: int, phase: str = "l3") -> None:
    """Switch the kettle on and off; the main fuse follows on `phase`."""
    for _ in range(times):
        for main, kettle in ((12.0, 2070), (3.0, 0)):
            freezer.tick(timedelta(seconds=30))
            async_fire_time_changed(hass)
            set_amps(hass, f"sensor.main_{phase}", main)
            set_amps(hass, "sensor.kettle", kettle, "W")
            await hass.async_block_till_done()
            freezer.tick(timedelta(seconds=11))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()


async def _setup(hass, portable):
    for e in SOURCES.values():
        set_amps(hass, e, 3.0)
    set_amps(hass, "sensor.kettle", 0, "W")
    entry = _entry(hass, portable)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_moved_load_raises_repair(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, portable=False)
    await _boil(hass, freezer, 2)                     # 4 events: not yet
    issue_id = f"moved_{entry.entry_id}_sub_kettle"
    assert ir.async_get(hass).async_get_issue("phase_map", issue_id) is None
    await _boil(hass, freezer, 1)                     # 6 events, all on L3
    issue = ir.async_get(hass).async_get_issue("phase_map", issue_id)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders["to"] == "L3"
    assert entry.subentries["sub_kettle"].data["channels"] == {"L2": "sensor.kettle"}   # not changed by itself
    row = next(r for r in entry.runtime_data.payload()["loads"] if r["id"] == "sub_kettle")
    assert row["phase_check"]["suspected"] == "L3"

    # Confirming the repair moves it
    from homeassistant.components.repairs import repairs_flow_manager
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    flow = await manager.async_init("phase_map", data={"issue_id": issue_id})
    assert flow["step_id"] == "confirm"
    flow = await manager.async_configure(flow["flow_id"], {})
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.subentries["sub_kettle"].data["channels"] == {"L3": "sensor.kettle"}
    assert ir.async_get(hass).async_get_issue("phase_map", issue_id) is None


async def test_back_on_the_configured_phase_clears_the_repair(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, portable=False)
    await _boil(hass, freezer, 3, "l3")
    issue_id = f"moved_{entry.entry_id}_sub_kettle"
    assert ir.async_get(hass).async_get_issue("phase_map", issue_id) is not None
    await _boil(hass, freezer, 2, "l2")               # moved back: L2 L2 L2 L2 among the last 6
    assert ir.async_get(hass).async_get_issue("phase_map", issue_id) is None


async def test_portable_load_follows(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, portable=True)
    await _boil(hass, freezer, 3)
    await hass.async_block_till_done()
    assert entry.subentries["sub_kettle"].data["channels"] == {"L3": "sensor.kettle"}
    assert ir.async_get(hass).async_get_issue("phase_map", f"moved_{entry.entry_id}_sub_kettle") is None
