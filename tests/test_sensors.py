"""Live computations, overload warning and the unmetered-load detector."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import async_fire_time_changed, mock_restore_cache

from .conftest import make_entry, set_amps


async def _setup(hass):
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_unmetered_and_headroom(hass: HomeAssistant, main_fuse_states) -> None:
    await _setup(hass)
    # L2: 12 A at the fuse, dishwasher 2070 W = 9 A → 3 A unmetered, 8 A headroom
    assert float(hass.states.get("sensor.main_fuse_l2_unmetered_current").state) == 3.0
    assert float(hass.states.get("sensor.main_fuse_l2_headroom").state) == 8.0
    assert float(hass.states.get("sensor.main_fuse_l1_unmetered_current").state) == 3.0
    set_amps(hass, "sensor.oven", 3143, "W")     # 13.67 A on L3
    set_amps(hass, "sensor.main_l3", 15.7)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.main_fuse_l3_unmetered_current").state) == 2.03
    assert hass.states.get("sensor.main_fuse_l3_unmetered_current").attributes["metered_current"] == 13.67


async def test_unavailable_source(hass: HomeAssistant, main_fuse_states) -> None:
    await _setup(hass)
    hass.states.async_set("sensor.main_l1", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.main_fuse_l1_unmetered_current").state == "unavailable"


async def test_overload_with_delay(hass: HomeAssistant, main_fuse_states, freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "off"
    set_amps(hass, "sensor.main_l2", 19.5)       # within 1 A of 20 A
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "off"
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "on"
    set_amps(hass, "sensor.main_l2", 10)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.main_fuse_l2_overload_risk").state == "off"


async def test_detector(hass: HomeAssistant, main_fuse_states, freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    det = "binary_sensor.sauna_running"
    assert hass.states.get(det).state == "off"
    # sauna on: +11.5 A unmetered on every phase
    set_amps(hass, "sensor.main_l1", 14.5)
    set_amps(hass, "sensor.main_l2", 23.5)
    set_amps(hass, "sensor.main_l3", 13.5)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "off"           # on-delay 60 s not reached
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "on"
    # thermostat pauses the heater for 2 minutes: stays on (off-delay 300 s)
    for entity, value in (("sensor.main_l1", 3.0), ("sensor.main_l2", 12.0), ("sensor.main_l3", 2.0)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "on"
    freezer.tick(timedelta(seconds=200))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "off"


async def test_detector_needs_all_phases(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    await _setup(hass)
    set_amps(hass, "sensor.main_l1", 14.5)       # only L1 rises (e.g. a single-phase heater)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "off"


async def test_reload_on_new_subentry(hass: HomeAssistant, main_fuse_states) -> None:
    entry = await _setup(hass)
    assert len(entry.runtime_data.loads) == 4
    assert hass.config_entries.async_remove_subentry(entry, "sub_oven")
    await hass.async_block_till_done()
    assert len(entry.runtime_data.loads) == 3


async def test_long_session_is_not_learned(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    """While the detector is on, the quiet level stays where it was (a 2-hour sauna stays a sauna)."""
    entry = await _setup(hass)
    engine = entry.runtime_data
    for entity, value in (("sensor.main_l1", 14.5), ("sensor.main_l2", 23.5), ("sensor.main_l3", 13.5)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()
    for _ in range(3):   # on after the on-delay
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "on"
    before = dict(engine.baseline)
    for _ in range(120):   # two hours of one-minute samples
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert engine.baseline == before
    assert hass.states.get("binary_sensor.sauna_running").state == "on"


async def test_baseline_seeded_from_history_after_a_reload(hass: HomeAssistant, main_fuse_states,
                                                           monkeypatch) -> None:
    """A reload while the sauna runs starts from the recorded quiet level, not from zero."""
    entry = await _setup(hass)     # creates the unmetered sensors (and their registry entries)
    history = {f"sensor.main_fuse_{p.lower()}_unmetered_current": {float(i * 300): v for i, v in enumerate(vals)}
               for p, vals in (("L1", [1.4] * 60 + [11.0] * 12), ("L2", [0.8] * 72), ("L3", [0.1] * 72))}
    calls = []

    async def fetch(hass_, units, voltage, days, period, key="mean"):
        calls.append((sorted(units), round(days, 3), period))
        return {e: history.get(e, {}) for e in units}

    monkeypatch.setattr("custom_components.phase_map.engine.async_fetch_means", fetch)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    baseline = entry.runtime_data.baseline
    assert calls and calls[0][1:] == (0.25, "5minute")     # the last 6 hours
    assert baseline == {"L1": 1.4, "L2": 0.8, "L3": 0.1}  # an hour of sauna at the end doesn't move it


def _normal(entry) -> None:
    """The quiet level the detector has learned: what the fixture draws unmetered (3 / 3 / 2 A)."""
    entry.runtime_data.seed_baseline({"L1": [3.0] * 10, "L2": [3.0] * 10, "L3": [2.0] * 10})


async def _sauna_on(hass, freezer) -> None:
    # +11.5 A unmetered on every phase above the normal 3 / 3 / 2 A
    for entity, value in (("sensor.main_l1", 14.5), ("sensor.main_l2", 23.5), ("sensor.main_l3", 13.5)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()
    for _ in range(3):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def _normal_again(hass) -> None:
    for entity, value in (("sensor.main_l1", 3.0), ("sensor.main_l2", 12.0), ("sensor.main_l3", 2.0)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()


async def test_detector_power(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    """The detected load's power: 0 while off, the current above normal while on, 0 while it pauses."""
    _normal(await _setup(hass))
    power = "sensor.sauna_running_power"
    assert hass.states.get(power).state == "0"
    assert hass.states.get(power).attributes["unit_of_measurement"] == "W"
    set_amps(hass, "sensor.main_l1", 14.5)          # one phase is not the sauna
    await hass.async_block_till_done()
    assert hass.states.get(power).state == "0"
    await _sauna_on(hass, freezer)
    assert hass.states.get("binary_sensor.sauna_running").state == "on"
    assert float(hass.states.get(power).state) == round(3 * 11.5 * 230)
    assert hass.states.get(power).attributes["current"] == {"L1": 11.5, "L2": 11.5, "L3": 11.5}
    # the thermostat pauses the heater: the detector stays on, the power drops to 0
    await _normal_again(hass)
    assert hass.states.get("binary_sensor.sauna_running").state == "on"
    assert hass.states.get(power).state == "0"
    hass.states.async_set("sensor.main_l3", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(power).state == "unavailable"


async def test_detector_energy(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    """Energy adds up the power over time, also while no meter changes."""
    _normal(await _setup(hass))
    energy = "sensor.sauna_running_energy"
    assert hass.states.get(energy).attributes["unit_of_measurement"] == "kWh"
    assert hass.states.get(energy).attributes["state_class"] == "total_increasing"
    assert float(hass.states.get(energy).state) == 0
    await _sauna_on(hass, freezer)                  # on for the last 30 s of it
    for _ in range(30):                             # half an hour, nothing changes
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    kw = 3 * 11.5 * 230 / 1000
    assert abs(float(hass.states.get(energy).state) - kw * 30.5 / 60) < 0.01
    # paused: nothing more is added
    await _normal_again(hass)
    paused = float(hass.states.get(energy).state)
    for _ in range(3):
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert float(hass.states.get(energy).state) == paused


async def test_detector_follows_a_heater_that_drops_a_stage(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    """A sauna whose thermostat switches off L1 first keeps the detector on, and its power is the stage left."""
    _normal(await _setup(hass))
    det, power = "binary_sensor.sauna_running", "sensor.sauna_running_power"
    await _sauna_on(hass, freezer)
    assert hass.states.get(det).state == "on"
    assert hass.states.get(det).attributes["hold_current"] == 4.0      # two thirds of 6 A
    # L1 stage off: L1 back to normal, L2/L3 still 6.2 A above it
    for entity, value in (("sensor.main_l1", 3.0), ("sensor.main_l2", 18.2), ("sensor.main_l3", 8.2)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()
    for _ in range(10):                             # 10 minutes, well past the 300 s off-delay
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(det).state == "on"
    assert float(hass.states.get(power).state) == round((6.2 + 6.2) * 230)
    # the last stage off: off after the off-delay
    await _normal_again(hass)
    assert hass.states.get(power).state == "0"
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(det).state == "off"


async def test_one_phase_alone_does_not_start_it(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    """Holding needs one phase, starting still needs all of them."""
    await _setup(hass)
    set_amps(hass, "sensor.main_l2", 21.0)          # 9 A extra on L2 only
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "off"


async def test_a_glitch_on_one_phase_does_not_restart_the_on_delay(hass: HomeAssistant, main_fuse_states,
                                                                   freezer) -> None:
    await _setup(hass)
    for entity, value in (("sensor.main_l1", 14.5), ("sensor.main_l2", 23.5), ("sensor.main_l3", 13.5)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    set_amps(hass, "sensor.main_l1", 4.0)           # 10 s meter glitch on L1
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    set_amps(hass, "sensor.main_l1", 14.5)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))             # 61 s after the start
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "on"


async def test_glitches_on_one_phase_do_not_keep_it_on(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    """After the sauna, a spike on one phase every few minutes does not hold the detector on."""
    _normal(await _setup(hass))
    det = "binary_sensor.sauna_running"
    await _sauna_on(hass, freezer)
    await _normal_again(hass)
    for _ in range(4):                              # a 10 s L1 spike every 2 minutes, for 8 minutes
        set_amps(hass, "sensor.main_l1", 19.0)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        set_amps(hass, "sensor.main_l1", 3.0)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=110))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(det).state == "off"      # off-delay 300 s from the end of the session


async def test_detector_stays_on_across_a_restart(hass: HomeAssistant, main_fuse_states, freezer) -> None:
    """A restart while the heater runs at part power (two phases) keeps the detector on."""
    mock_restore_cache(hass, [State("binary_sensor.sauna_running", "on")])
    for entity, value in (("sensor.main_l2", 18.2), ("sensor.main_l3", 8.2)):   # L2/L3 +6.2 A
        set_amps(hass, entity, value)
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    _normal(entry)
    await _normal_again(hass)
    for entity, value in (("sensor.main_l2", 18.2), ("sensor.main_l3", 8.2)):
        set_amps(hass, entity, value)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "on"
    freezer.tick(timedelta(seconds=600))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "on"
    assert float(hass.states.get("sensor.sauna_running_power").state) == round(2 * 6.2 * 230)


async def test_detector_restored_on_turns_off_when_nothing_runs(hass: HomeAssistant, main_fuse_states,
                                                                freezer) -> None:
    mock_restore_cache(hass, [State("binary_sensor.sauna_running", "on")])
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    _normal(entry)
    await _normal_again(hass)
    assert hass.states.get("binary_sensor.sauna_running").state == "on"
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.sauna_running").state == "off"
