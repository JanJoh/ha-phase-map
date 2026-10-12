"""Meter discovery: which sensors are candidates, how channels are mapped, and the suggestions."""

from __future__ import annotations

import random

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phase_map.const import DOMAIN
from custom_components.phase_map.detection import analyse, analyse_group
from custom_components.phase_map.discovery import (
    MAX_OPEN,
    channel_of,
    issue_id_for,
    phase_tag,
    select_candidates,
)

from .conftest import SOURCES, make_entry

STEP = 300.0


def sensor(hass: HomeAssistant, entity_id: str, value: float = 0, unit: str = "W",
           state_class: str = "measurement", device_class: str | None = None) -> None:
    hass.states.async_set(entity_id, str(value), {
        "unit_of_measurement": unit, "state_class": state_class,
        "device_class": device_class or ("current" if unit in ("A", "mA") else "power"),
        "friendly_name": entity_id.split(".")[1].replace("_", " "),
    })


def device_with(hass: HomeAssistant, name: str, entities: dict[str, str], platform: str = "shelly") -> str:
    """A device of another integration with registered sensors {object_id: unit}."""
    other = MockConfigEntry(domain=platform)
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(platform, name)}, name=name)
    reg = er.async_get(hass)
    for object_id, unit in entities.items():
        reg.async_get_or_create("sensor", platform, f"{name}-{object_id}", device_id=device.id,
                                suggested_object_id=object_id, config_entry=other)
        sensor(hass, f"sensor.{object_id}", 0, unit)
    return device.id


def main_states(hass: HomeAssistant) -> None:
    for e in SOURCES.values():
        sensor(hass, e, 5.0, "A")


# --- pure helpers ---------------------------------------------------------------------


def test_channel_and_phase_tags() -> None:
    assert channel_of("shellypro3em_abc_phase_a_current", ["shellypro3em_abc"]) == ("phase_a", 1)
    assert channel_of("shellypro3em_abc_phase_a_active_power", ["shellypro3em_abc"]) == ("phase_a", 2)
    assert channel_of("garaget_garaget_l3_effekt", ["garaget"]) == ("garaget_l3", 1)
    assert channel_of("kettle_power", ["kettle"]) == ("", 1)
    assert phase_tag("phase_a") == "a"
    assert phase_tag("garaget_l3") == "l3"
    assert phase_tag("fas_2") == "2"
    assert phase_tag("circuit_1") is None       # Emporia circuits are not phases
    assert phase_tag("ugn") is None


# --- candidate selection --------------------------------------------------------------


async def test_selection_rules(hass: HomeAssistant) -> None:
    main_states(hass)
    sensor(hass, "sensor.kettle_power", 0, "W")                                   # a candidate
    sensor(hass, "sensor.kettle_energy", 1.2, "kWh", "total_increasing", "energy")  # energy: no
    sensor(hass, "sensor.mains_voltage", 230, "V", device_class="voltage")       # voltage: no
    sensor(hass, "sensor.dishwasher", 0, "W")                                    # used by a load
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "template", "tpl", suggested_object_id="house_power")
    sensor(hass, "sensor.house_power", 900, "W")                                 # template: no
    reg.async_get_or_create("sensor", "shelly", "hidden", suggested_object_id="hidden_power",
                            hidden_by=er.RegistryEntryHider.USER)
    sensor(hass, "sensor.hidden_power", 10, "W")                                 # hidden: no
    cands, excluded = select_candidates(hass, SOURCES, {"sensor.dishwasher"}, set(), set(), set())
    assert [c.entities for c in cands] == [["sensor.kettle_power"]]
    assert "sensor.dishwasher" in excluded["used"]
    assert "sensor.house_power" in excluded["platform"]
    assert "sensor.hidden_power" in excluded["hidden"]
    assert set(SOURCES.values()) <= set(excluded["main_fuse"])


async def test_main_fuse_device_siblings_are_skipped(hass: HomeAssistant) -> None:
    dev = device_with(hass, "balance", {"main_l1": "A", "main_l2": "A", "main_l3": "A", "balance_power": "W"})
    assert dev
    cands, excluded = select_candidates(hass, SOURCES, set(), set(), set(), set())
    assert cands == []
    assert "sensor.balance_power" in excluded["main_fuse"]


async def test_three_channel_meter_is_one_group(hass: HomeAssistant) -> None:
    """A Shelly Pro 3EM: one candidate, current channels (the fuse is in A), total skipped."""
    main_states(hass)
    device_with(hass, "shellypro3em-abc", {
        "shellypro3em_abc_phase_a_current": "A", "shellypro3em_abc_phase_b_current": "A",
        "shellypro3em_abc_phase_c_current": "A",
        "shellypro3em_abc_phase_a_active_power": "W", "shellypro3em_abc_phase_b_active_power": "W",
        "shellypro3em_abc_phase_c_active_power": "W", "shellypro3em_abc_total_active_power": "W",
    })
    cands, excluded = select_candidates(hass, SOURCES, set(), set(), set(), set())
    assert len(cands) == 1 and cands[0].group
    assert cands[0].channels == {"phase_a": "sensor.shellypro3em_abc_phase_a_current",
                                 "phase_b": "sensor.shellypro3em_abc_phase_b_current",
                                 "phase_c": "sensor.shellypro3em_abc_phase_c_current"}
    assert len(excluded["twin"]) == 3
    assert excluded["aggregate"] == ["sensor.shellypro3em_abc_total_active_power"]


async def test_power_preferred_when_the_fuse_is_in_watts(hass: HomeAssistant) -> None:
    for e in SOURCES.values():
        sensor(hass, e, 1000, "W")
    device_with(hass, "plug", {"plug_power": "W", "plug_current": "A"})
    cands, _ = select_candidates(hass, SOURCES, set(), set(), set(), set())
    assert cands[0].entities == ["sensor.plug_power"]


async def test_partly_used_meter_offers_the_free_channels(hass: HomeAssistant) -> None:
    main_states(hass)
    device_with(hass, "garaget", {"garaget_garaget_l1_effekt": "W", "garaget_garaget_l2_effekt": "W",
                                  "garaget_garaget_l3_effekt": "W", "garaget_garaget_effekt": "W"})
    cands, excluded = select_candidates(hass, SOURCES, set(), set(), set(), set())
    assert [sorted(c.channels) for c in cands] == [["garaget_l1", "garaget_l2", "garaget_l3"]]
    assert excluded["aggregate"] == ["sensor.garaget_garaget_effekt"]
    cands, excluded = select_candidates(hass, SOURCES, {"sensor.garaget_garaget_l3_effekt"}, set(), set(), set())
    assert sorted(c.entities[0] for c in cands) == ["sensor.garaget_garaget_l1_effekt",
                                                    "sensor.garaget_garaget_l2_effekt"]


def shelly_3em(hass: HomeAssistant, root_name: str, prefix: str, phases: dict[str, str],
               total: str | None = None, user_names: dict[str, str] | None = None) -> None:
    """A Shelly Pro 3EM as HA models it: a root device and one sub-device per phase."""
    other = MockConfigEntry(domain="shelly")
    other.add_to_hass(hass)
    devs = dr.async_get(hass)
    reg = er.async_get(hass)
    root = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("shelly", prefix)}, name=root_name)
    for letter, object_id in phases.items():
        sub = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("shelly", f"{prefix}-{letter}")},
                                       name=f"{prefix} Phase {letter.upper()}", via_device_id=root.id)
        if user_names and letter in user_names:
            devs.async_update_device(sub.id, name_by_user=user_names[letter])
        reg.async_get_or_create("sensor", "shelly", f"{prefix}-{letter}-p", device_id=sub.id,
                                suggested_object_id=object_id, config_entry=other)
        sensor(hass, f"sensor.{object_id}", 0, "W")
    if total:
        reg.async_get_or_create("sensor", "shelly", f"{prefix}-total", device_id=root.id,
                                suggested_object_id=total, config_entry=other)
        sensor(hass, f"sensor.{total}", 0, "W")


async def test_shelly_sub_devices(hass: HomeAssistant) -> None:
    main_states(hass)
    # Heat pump: three free phases on sub-devices → one group
    shelly_3em(hass, "shellypro3em-hp", "shellypro3em-hp",
               {"a": "shellypro3em_hp_phase_a_active_power", "b": "shellypro3em_hp_phase_b_active_power",
                "c": "shellypro3em_hp_phase_c_active_power"}, total="effektmatare_bvp_total_active_power")
    # Laundry: one appliance per channel, two of them added → the third is offered alone
    shelly_3em(hass, "shellypro3em-laundry", "shellypro3em-laundry",
               {"a": "effektmatare_tvattmaskinen_effekt", "b": "shellypro3em_laundry_phase_b_effekt",
                "c": "effektmatare_torktumlaren_effekt"}, total="effektmatare_tvattstuga_effekt",
               user_names={"a": "Effektmätare Tvättmaskinen", "c": "Effektmätare Torktumlaren"})
    # CEE outlet added by its total: its phases are the same circuit
    shelly_3em(hass, "shellypro3em-cee", "shellypro3em-cee",
               {"a": "shellypro3em_cee_phase_a_effekt", "b": "shellypro3em_cee_phase_b_effekt",
                "c": "shellypro3em_cee_phase_c_effekt"}, total="garaget_cee_forbrukning_effekt")
    device_with(hass, "usw", {"usw_pro_max_16_poe_port_1_poe_power": "W"}, platform="unifi")
    used = {"sensor.effektmatare_tvattmaskinen_effekt", "sensor.effektmatare_torktumlaren_effekt",
            "sensor.garaget_cee_forbrukning_effekt"}
    cands, excluded = select_candidates(hass, SOURCES, used, set(), set(), set())
    by_kind = {c.entities[0] if not c.group else "group": c for c in cands}
    assert set(by_kind) == {"group", "sensor.shellypro3em_laundry_phase_b_effekt"}
    assert sorted(by_kind["group"].channels) == ["phase_a", "phase_b", "phase_c"]
    assert sorted(excluded["aggregate"]) == ["sensor.effektmatare_bvp_total_active_power",
                                             "sensor.effektmatare_tvattstuga_effekt"]
    assert sorted(excluded["taken_group"]) == ["sensor.shellypro3em_cee_phase_a_effekt",
                                               "sensor.shellypro3em_cee_phase_b_effekt",
                                               "sensor.shellypro3em_cee_phase_c_effekt"]
    assert excluded["not_a_load"] == ["sensor.usw_pro_max_16_poe_port_1_poe_power"]


async def test_ignored_and_energy_flags(hass: HomeAssistant) -> None:
    main_states(hass)
    device_with(hass, "kettle", {"kettle_power": "W"})
    device_with(hass, "dryer", {"dryer_power": "W"})
    cands, _ = select_candidates(hass, SOURCES, set(), set(), set(), {"sensor.dryer_power"})
    by_entity = {c.entities[0]: c for c in cands}
    assert by_entity["sensor.dryer_power"].energy and not by_entity["sensor.kettle_power"].energy
    key = by_entity["sensor.kettle_power"].key
    cands, excluded = select_candidates(hass, SOURCES, set(), {key}, {"sensor.dryer_power"}, set())
    assert cands == []
    assert set(excluded["ignored"]) == {"sensor.kettle_power", "sensor.dryer_power"}


# --- evidence ---------------------------------------------------------------------------


def synth(loads: dict[str, tuple[str, float]], n: int = 2000, seed: int = 1, together: bool = False):
    """5-minute series: each load switches at random, the main fuse phase follows it.

    loads: {name: (phase, amps)}. `together` makes them all switch at the same moments
    (a balanced 3-phase load).
    """
    rng = random.Random(seed)
    on = {k: False for k in loads}
    series = {k: {} for k in loads}
    main = {p: {} for p in ("L1", "L2", "L3")}
    for i in range(n):
        t = 1_700_000_000.0 + i * STEP
        if together:
            if rng.random() < 0.05:
                state = not next(iter(on.values()))
                on = dict.fromkeys(on, state)
        else:
            for k in loads:
                if rng.random() < 0.05:
                    on[k] = not on[k]
        for p in main:
            main[p][t] = 3.0 + rng.gauss(0, 0.05)
        for k, (phase, amps) in loads.items():
            series[k][t] = amps if on[k] else 0.0
            main[phase][t] += series[k][t]
    return series, main


def test_analyse_without_confounders_unchanged() -> None:
    series, main = synth({"kettle": ("L2", 8.0)})
    assert analyse(series["kettle"], main).phase == "L2"
    assert analyse(series["kettle"], main, confounders=None).as_dict() == analyse(series["kettle"], main).as_dict()


def test_group_mapping_follows_the_evidence_not_the_names() -> None:
    """Heat-pump meter: channel a on L1, c on L2, b on L3."""
    series, main = synth({"phase_a": ("L1", 2.0), "phase_b": ("L3", 4.0), "phase_c": ("L2", 3.0)})
    g = analyse_group(series, main)
    assert g.mapping == {"L1": "phase_a", "L2": "phase_c", "L3": "phase_b"}
    assert g.confidence in ("high", "medium")


def test_balanced_load_is_a_guess() -> None:
    series, main = synth({"l1": ("L1", 5.0), "l2": ("L2", 5.0), "l3": ("L3", 5.0)}, together=True)
    g = analyse_group(series, main)
    assert g.confidence == "guess"
    assert sorted(g.mapping) == ["L1", "L2", "L3"]


# --- the manager and the repairs -------------------------------------------------------------


@pytest.fixture
def fake_statistics(monkeypatch):
    """Serve synthetic statistics instead of the recorder."""
    data: dict[str, dict[float, float]] = {}

    async def fetch(hass, entity_units, voltage, days, period, key="mean"):
        return {e: data.get(e, {}) for e in entity_units}

    monkeypatch.setattr("custom_components.phase_map.discovery.async_fetch_means", fetch)
    monkeypatch.setattr("custom_components.phase_map.energy_prefs.async_energy_devices", _no_energy)
    return data


async def _no_energy(hass):
    return []


async def setup(hass: HomeAssistant, discovery: bool = True, **options):
    entry = make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "discovery": discovery, **options})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def meter_issues(hass: HomeAssistant, entry_id: str) -> list:
    prefix = f"meter_{entry_id}_"
    return [i for (d, iid), i in ir.async_get(hass).issues.items() if d == DOMAIN and iid.startswith(prefix)]


def plugs(hass: HomeAssistant, data, count: int, phases=("L1", "L2", "L3")) -> list[str]:
    """`count` plug meters with clear switch steps, and a main fuse that follows them."""
    loads = {f"plug_{i}": (phases[i % len(phases)], 1.5 + i) for i in range(count)}
    series, main = synth(loads, seed=7)
    for name in loads:
        device_with(hass, name, {f"{name}_current": "A"})
        data[f"sensor.{name}_current"] = series[name]
    for p, e in SOURCES.items():
        data[e] = main[p]
    return list(loads)


async def test_scan_offers_at_most_five(hass: HomeAssistant, main_fuse_states, fake_statistics) -> None:
    plugs(hass, fake_statistics, 7)
    entry = await setup(hass)
    manager = entry.runtime_data.discovery
    await manager.async_scan()
    await hass.async_block_till_done()
    issues = meter_issues(hass, entry.entry_id)
    assert len(issues) == MAX_OPEN
    offered = [c for c in manager.data["candidates"].values() if c["status"] == "offer"]
    assert len(offered) == 7
    assert {c["phase"] for c in offered} == {"L1", "L2", "L3"}
    # Resolving one brings the next from the cache, without another scan
    manager.ignore(issues[0].data["key"])
    assert len(meter_issues(hass, entry.entry_id)) == MAX_OPEN


async def test_off_by_default_but_energy_seed_works(hass: HomeAssistant, main_fuse_states, fake_statistics,
                                                    monkeypatch) -> None:
    names = plugs(hass, fake_statistics, 2)

    async def energy(hass):
        return [{"stat_consumption": f"sensor.{names[0]}_energy", "stat_rate": f"sensor.{names[0]}_current"}]

    monkeypatch.setattr("custom_components.phase_map.energy_prefs.async_energy_devices", energy)
    entry = await setup(hass, discovery=False)
    manager = entry.runtime_data.discovery
    await manager.async_scan()
    assert meter_issues(hass, entry.entry_id) == []           # discovery is off
    await manager.async_scan(energy_only=True)
    issues = meter_issues(hass, entry.entry_id)
    assert len(issues) == 1
    assert manager.candidate(issues[0].data["key"])["channels"] == {"": f"sensor.{names[0]}_current"}


async def test_add_from_the_repair(hass: HomeAssistant, main_fuse_states, fake_statistics) -> None:
    from homeassistant.components.repairs import repairs_flow_manager
    from homeassistant.setup import async_setup_component

    plugs(hass, fake_statistics, 1, phases=("L2",))
    entry = await setup(hass)
    assert await async_setup_component(hass, "repairs", {})
    manager = entry.runtime_data.discovery
    await manager.async_scan()
    (issue,) = meter_issues(hass, entry.entry_id)
    assert "L2" in issue.translation_placeholders["details"]
    flows = repairs_flow_manager(hass)
    result = await flows.async_init(DOMAIN, data={"issue_id": issue.issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "add"})
    assert result["step_id"] == "add"
    result = await flows.async_configure(result["flow_id"], {"name": "Kettle", "phase": "L2", "parent": "none"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    subs = [s for s in entry.subentries.values() if s.title == "Kettle"]
    assert subs and dict(subs[0].data) == {"name": "Kettle", "channels": {"L2": "sensor.plug_0_current"}}
    assert meter_issues(hass, entry.entry_id) == []


async def test_ignore_from_the_repair_and_in_repairs_ui(hass: HomeAssistant, main_fuse_states,
                                                        fake_statistics) -> None:
    from homeassistant.components.repairs import repairs_flow_manager
    from homeassistant.setup import async_setup_component

    plugs(hass, fake_statistics, 2, phases=("L1", "L3"))
    entry = await setup(hass)
    assert await async_setup_component(hass, "repairs", {})
    manager = entry.runtime_data.discovery
    await manager.async_scan()
    first, second = meter_issues(hass, entry.entry_id)
    flows = repairs_flow_manager(hass)
    result = await flows.async_init(DOMAIN, data={"issue_id": first.issue_id})
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert first.data["key"] in manager.data["ignored"]
    # The Repairs list's own "Ignore" is remembered too
    ir.async_ignore_issue(hass, DOMAIN, second.issue_id, True)
    manager.reconcile()
    assert second.data["key"] in manager.data["ignored"]
    await manager.async_scan()
    assert meter_issues(hass, entry.entry_id) == []


async def test_deleting_a_load_ignores_its_sensor(hass: HomeAssistant, main_fuse_states, fake_statistics) -> None:
    entry = await setup(hass)
    hass.config_entries.async_remove_subentry(entry, "sub_dish")
    await hass.async_block_till_done()
    manager = entry.runtime_data.discovery
    assert manager.data["ignored_entities"]["sensor.dishwasher"]["reason"] == "removed_load"


async def test_turning_discovery_off_removes_suggestions(hass: HomeAssistant, main_fuse_states,
                                                          fake_statistics) -> None:
    plugs(hass, fake_statistics, 1)
    entry = await setup(hass)
    manager = entry.runtime_data.discovery
    await manager.async_scan()
    assert len(meter_issues(hass, entry.entry_id)) == 1
    hass.config_entries.async_update_entry(entry, options={**entry.options, "discovery": False})
    await hass.async_block_till_done()
    assert meter_issues(hass, entry.entry_id) == []
    # what it found is kept for when it is turned on again
    assert entry.runtime_data.discovery.data["candidates"]


async def test_options_menu_and_suggestions(hass: HomeAssistant, main_fuse_states, fake_statistics) -> None:
    plugs(hass, fake_statistics, 1)
    entry = await setup(hass)
    await entry.runtime_data.discovery.async_scan()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "energy_seed", "suggestions", "ignored"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "suggestions"})
    assert "plug_0" in result["description_placeholders"]["candidates"]
    (key,) = entry.runtime_data.discovery.data["candidates"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"ignore": [key], "scan_now": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    manager = entry.runtime_data.discovery
    assert key in manager.data["ignored"] and manager.data["pending"] == "scan"
    # and back again
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "ignored"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"ignored": []})
    await hass.async_block_till_done()
    assert key not in entry.runtime_data.discovery.data["ignored"]


async def test_issue_id_is_stable() -> None:
    assert issue_id_for("e1", "dev:x:main") == issue_id_for("e1", "dev:x:main")
    assert issue_id_for("e1", "dev:x:main") != issue_id_for("e1", "dev:y:main")


async def test_parent_from_statistics(hass: HomeAssistant, main_fuse_states, fake_statistics) -> None:
    """A plug that only ever steps together with one channel of a multi-channel meter sits behind it."""
    series, main = synth({"heater": ("L1", 6.0)}, seed=3)
    device_with(hass, "heater", {"heater_current": "A"})
    fake_statistics["sensor.heater_current"] = series["heater"]
    # The EV charger's channels measure the branch the heater is on (its L1 channel follows it)
    fake_statistics["sensor.ev_l1"] = {t: v + 0.2 for t, v in series["heater"].items()}
    fake_statistics["sensor.ev_l2"] = dict.fromkeys(series["heater"], 0.0)
    fake_statistics["sensor.ev_l3"] = dict.fromkeys(series["heater"], 0.0)
    for p, e in SOURCES.items():
        fake_statistics[e] = main[p]
    entry = await setup(hass)
    manager = entry.runtime_data.discovery
    await manager.async_scan()
    (cand,) = manager.data["candidates"].values()
    assert cand["status"] == "offer" and cand["phase"] == "L1"
    assert cand["parent"] == "sub_ev" and cand["parent_source"] == "stats"
    assert "behind EV charger" in meter_issues(hass, entry.entry_id)[0].translation_placeholders["details"]


async def test_websocket_and_diagnostics(hass: HomeAssistant, hass_ws_client, main_fuse_states,
                                         fake_statistics) -> None:
    from custom_components.phase_map.diagnostics import async_get_config_entry_diagnostics

    plugs(hass, fake_statistics, 1)
    entry = await setup(hass)
    await entry.runtime_data.discovery.async_scan()
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "phase_map/discovery", "entry_id": entry.entry_id})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["enabled"] is True
    assert len(msg["result"]["candidates"]) == 1 and len(msg["result"]["open"]) == 1
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["discovery"]["counts_by_status"] == {"offer": 1}
    assert diag["discovery"]["open"] == list(diag["discovery"]["candidates"])
