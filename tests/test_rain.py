"""Regen in X min: Grenzen der stündlichen Auflösung, Messung vor Ort, Binärsensor nächste Stunde."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wetter import model
from custom_components.wetter.const import CONF_LOCAL_PRECIPITATION, DOMAIN

from .conftest import LAT, LON, load

NOW = datetime.fromisoformat("2026-10-15T09:40:00+00:00")
RAIN_IN = "sensor.wetter_regen_in"
NEXT_HOUR = "binary_sensor.wetter_regen_in_der_nachsten_stunde"


def _hour(end: str, mm: Any, prob: Any = 50) -> dict[str, Any]:
    return {"timestamp": end, "precipitation": mm, "precipitation_probability": prob}


def test_raining_now() -> None:
    rain = model.rain_in([_hour("2026-10-15T12:00:00+00:00", 1.0)], NOW, True, 48)
    assert rain == {
        "minutes": 0,
        "start": NOW.isoformat(),
        "precipitation": None,
        "precipitation_probability": None,
        "source": "measurement",
    }


def test_first_wet_hour_starts_at_its_beginning() -> None:
    """Bright Sky stempelt das Ende der Stunde: 12:00 = Regen von 11:00 bis 12:00 → 80 Min. ab 09:40."""
    hours = [
        _hour("kaputt", 5.0),
        _hour("2026-10-15T09:00:00+00:00", 3.0),  # vorbei
        _hour("2026-10-15T10:00:00+00:00", 0.0),  # laufende Stunde trocken
        _hour("2026-10-15T11:00:00+00:00", None),  # ohne Wert zählt nicht
        _hour("2026-10-15T12:00:00+00:00", 0.4, 61),
    ]
    rain = model.rain_in(hours, NOW, False, 48)
    assert rain is not None and rain["minutes"] == 80 and rain["source"] == "forecast"
    assert rain["start"] == "2026-10-15T11:00:00+00:00"
    assert rain["precipitation"] == 0.4 and rain["precipitation_probability"] == 61.0


def test_rain_in_running_hour_is_zero() -> None:
    rain = model.rain_in([_hour("2026-10-15T10:00:00+00:00", model.RAIN_MM)], NOW, False, 48)
    assert rain is not None and rain["minutes"] == 0 and rain["start"] == NOW.isoformat()


def test_below_threshold_and_beyond_horizon() -> None:
    hours = [_hour("2026-10-15T11:00:00+00:00", 0.05), _hour("2026-10-15T13:00:00+00:00", 2.0)]
    assert model.rain_in(hours, NOW, False, 3) is None, "unter 0,1 mm zählt nicht, 13:00 liegt hinter 3 Std."
    assert model.rain_in(hours, NOW, False, 4) is not None
    assert model.rain_in([], NOW, False, 48) is None


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_sensors_from_forecast(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Testdaten: erster Regen in der Stunde bis 16.10. 15:00 UTC → ab 14:00 UTC, 28 Std. 20 Min. ab jetzt."""
    await _setup(hass, entry)
    state = hass.states.get(RAIN_IN)
    assert state.state == str(28 * 60 + 20)
    assert state.attributes["unit_of_measurement"] == "min"
    assert state.attributes["start"] == "2026-10-16T14:00:00+00:00"
    assert state.attributes["precipitation"] == 0.4 and state.attributes["precipitation_probability"] == 18.0
    assert state.attributes["source"] == "forecast"
    assert hass.states.get(NEXT_HOUR).state == "off"

    # 13:10 UTC: Regen beginnt in 50 Min. → nächste Stunde an
    freezer.move_to("2026-10-16 13:10:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(RAIN_IN).state == "50"
    assert hass.states.get(NEXT_HOUR).state == "on"


async def test_local_rain_means_zero(hass: HomeAssistant, brightsky: AiohttpClientMocker) -> None:
    """Regen vor Ort (Nachbarschaftssensor) → 0 Min., auch wenn die Vorhersage trocken ist."""
    hass.states.async_set("sensor.regen", "0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
        options={CONF_LOCAL_PRECIPITATION: "sensor.regen"},
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    assert hass.states.get(NEXT_HOUR).state == "off"
    hass.states.async_set("sensor.regen", "0.3")
    await hass.async_block_till_done()
    assert hass.states.get(RAIN_IN).state == "0"
    assert hass.states.get(RAIN_IN).attributes["source"] == "measurement"
    assert hass.states.get(NEXT_HOUR).state == "on"


async def test_no_rain_in_horizon(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Kein Regen in den nächsten 48 Std. → unbekannt, Binärsensor aus."""
    start = NOW.replace(minute=0)
    dry = [
        {"timestamp": (start + timedelta(hours=h)).isoformat(), "precipitation": 0.0, "temperature": 10.0}
        for h in range(-24, 72)
    ]
    aioclient_mock.get("https://api.brightsky.dev/current_weather", json={"weather": {"precipitation_60": 0.0}})
    aioclient_mock.get("https://api.brightsky.dev/weather", json={"weather": dry})
    aioclient_mock.get("https://api.brightsky.dev/alerts", json={"alerts": [], "location": None})
    await _setup(hass, entry)
    assert hass.states.get(RAIN_IN).state == "unknown"
    assert hass.states.get(RAIN_IN).attributes["start"] is None
    assert hass.states.get(NEXT_HOUR).state == "off"


async def test_forecast_drizzle_is_no_measurement(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Station ohne Regenmessung: der Vorhersagewert der laufenden Stunde gilt nicht als „regnet“ (Fund 5)."""
    start = NOW.replace(minute=0)
    hours = [
        {"timestamp": (start + timedelta(hours=h)).isoformat(), "precipitation": 0.0, "temperature": 10.0}
        for h in range(-24, 72)
    ]
    hours[25]["precipitation"] = 0.05  # running hour (ends 10:00), below the threshold
    aioclient_mock.get("https://api.brightsky.dev/current_weather", json={"weather": {"temperature": 10.0}})
    aioclient_mock.get("https://api.brightsky.dev/weather", json={"weather": hours})
    aioclient_mock.get("https://api.brightsky.dev/alerts", json={"alerts": [], "location": None})
    await _setup(hass, entry)
    assert hass.states.get("sensor.wetter_niederschlag").state == "0.05", "Niederschlag aus der Vorhersage"
    assert hass.states.get(RAIN_IN).state == "unknown"


async def test_station_rain_counts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get("https://api.brightsky.dev/current_weather", json={"weather": {"precipitation_60": 0.2}})
    aioclient_mock.get("https://api.brightsky.dev/weather", json=load("weather.json"))
    aioclient_mock.get("https://api.brightsky.dev/alerts", json={"alerts": [], "location": None})
    await _setup(hass, entry)
    assert hass.states.get(RAIN_IN).state == "0"
    assert hass.states.get(RAIN_IN).attributes["source"] == "measurement"
