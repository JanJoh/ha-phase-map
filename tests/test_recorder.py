"""The statistics query runs against a real recorder (signature check for this HA version)."""

from homeassistant.core import HomeAssistant
import pytest

from custom_components.phase_map.detection import async_fetch_means, async_suggest_phase

from .conftest import SOURCES, set_amps


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder must start before custom integrations are enabled."""
    yield


async def test_fetch_means_with_recorder(hass: HomeAssistant) -> None:
    result = await async_fetch_means(hass, {"sensor.x": "W", "sensor.y": "A"}, 230, 1, "5minute")
    assert result == {"sensor.x": {}, "sensor.y": {}}
    result = await async_fetch_means(hass, {"sensor.x": "W"}, 230, 30, "hour")
    assert result == {"sensor.x": {}}


async def test_suggest_without_history(hass: HomeAssistant) -> None:
    set_amps(hass, "sensor.new_meter", 100, "W")
    for e in SOURCES.values():
        set_amps(hass, e, 1.0)
    result = await async_suggest_phase(hass, "sensor.new_meter", SOURCES, 230)
    assert result.phase is None and result.reason == "no_statistics"
