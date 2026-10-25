"""Integration: Einrichtung, Entitäten, Vorhersage, Nachbarschaftswerte, schneller Start, Optionen, Diagnose."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN, SERVICE_GET_FORECASTS
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_LATITUDE, CONF_LOCATION, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.wetter import model
from custom_components.wetter.api import NinaClient, WetterError
from custom_components.wetter.const import (
    API_URL,
    CONF_LOCAL_PRECIPITATION,
    CONF_LOCAL_TEMPERATURE,
    DOMAIN,
    NINA_URL,
)

from .conftest import ARS, FAR_AWAY, LAT, LON, MOWAS_ID, WARN_CELL, load

WEATHER = "weather.wetter"
CURRENT = load("current.json")["weather"]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_entities(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    w = hass.states.get(WEATHER)
    assert w is not None and w.state == "cloudy"
    assert w.attributes["temperature"] == CURRENT["temperature"]
    assert w.attributes["humidity"] == CURRENT["relative_humidity"]
    assert w.attributes["attribution"] == "Deutscher Wetterdienst (über Bright Sky)"
    assert hass.states.get("sensor.wetter_temperatur").state == str(CURRENT["temperature"])
    assert hass.states.get("sensor.wetter_temperatur").attributes["source"] == "dwd"
    assert hass.states.get("sensor.wetter_wetterzustand").state == "cloudy"
    assert hass.states.get("sensor.wetter_windboen").state == str(CURRENT["wind_gust_speed_10"])
    assert hass.states.get("sensor.wetter_niederschlag").attributes["unit_of_measurement"] == "mm/h"
    hour = next(h for h in load("weather.json")["weather"] if h["timestamp"] == "2026-10-15T10:00:00+00:00")
    assert hass.states.get("sensor.wetter_regenwahrscheinlichkeit").state == str(
        float(hour["precipitation_probability"])
    )
    assert hass.states.get("sensor.wetter_regenwahrscheinlichkeit").attributes["unit_of_measurement"] == "%"
    daily = await hass.services.async_call(
        WEATHER_DOMAIN,
        SERVICE_GET_FORECASTS,
        {"entity_id": WEATHER, "type": "daily"},
        blocking=True,
        return_response=True,
    )
    today = daily[WEATHER]["forecast"][0]  # type: ignore[index]
    assert (
        float(hass.states.get("sensor.wetter_regenwahrscheinlichkeit_heute").state)
        == today["precipitation_probability"]
    )
    solar = hass.states.get("sensor.wetter_sonneneinstrahlung")
    assert solar.attributes["unit_of_measurement"] == "W/m²" and float(solar.state) >= 0
    warn = hass.states.get("sensor.wetter_warnungen")
    assert warn.state == "3", "2× DWD (Gemeinde) + 1× MoWaS (Kreis); DWD-Doppel und Aufhebung aus NINA übersprungen"
    assert warn.attributes["highest_severity"] == "Moderate"
    assert warn.attributes["area"] == "Gemeinde Musterstadt"
    assert [w["provider"] for w in warn.attributes["warnings"]] == ["DWD", "MOWAS", "DWD"]
    first = hass.states.get("binary_sensor.wetter_warnung_1")
    assert first.state == "on" and first.attributes["headline"] == "Amtliche WARNUNG vor STURMBÖEN"
    assert first.attributes["severity"] == "Moderate" and first.attributes["sender"] == "Deutscher Wetterdienst"
    mowas = hass.states.get("binary_sensor.wetter_warnung_2").attributes
    assert mowas["provider"] == "MOWAS" and "Trinkwasser" in mowas["headline"]
    assert "<br" not in mowas["description"] and "\n" in mowas["description"], "HTML-Umbrüche → Zeilen"
    assert mowas["recommended_actions"].startswith("Informieren Sie sich")
    assert hass.states.get("binary_sensor.wetter_warnung_4").state == "off"
    assert hass.states.get("binary_sensor.wetter_warnung_4").attributes["region"] == "Musterstadt", "auch ohne Warnung"
    assert first.attributes["region"] == "Musterstadt"
    assert hass.states.get("binary_sensor.wetter_warnung_5").attributes.get("headline") is None
    assert hass.states.get("sensor.wetter_quelle").state == "dwd", "nichts eingetragen"
    assert warn.attributes["warnings"][0]["event"] == "STURMBÖEN"


async def test_forecasts(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    for kind, count in (("daily", 8), ("hourly", 48)):
        resp = await hass.services.async_call(
            WEATHER_DOMAIN,
            SERVICE_GET_FORECASTS,
            {"entity_id": WEATHER, "type": kind},
            blocking=True,
            return_response=True,
        )
        fc = resp[WEATHER]["forecast"]  # type: ignore[index]
        assert len(fc) == count
        assert fc[0]["temperature"] is not None
    daily = resp  # noqa: F841 – Aufbau für die Tagesübersicht: temperature, templow, precipitation_probability
    resp = await hass.services.async_call(
        WEATHER_DOMAIN,
        SERVICE_GET_FORECASTS,
        {"entity_id": WEATHER, "type": "daily"},
        blocking=True,
        return_response=True,
    )
    today = resp[WEATHER]["forecast"][0]  # type: ignore[index]
    assert {"temperature", "templow", "precipitation_probability", "condition"} <= set(today)


async def test_local_values_take_precedence(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, hass_storage: dict[str, Any], freezer: Any
) -> None:
    """Netatmo-Werte gehen vor, solange sie frisch sind; Regen vor Ort ändert das Symbol."""
    hass.states.async_set("sensor.umgebung_temperatur", "15.24")
    hass.states.async_set("sensor.umgebung_niederschlagsmenge", "0.0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
        options={
            CONF_LOCAL_TEMPERATURE: "sensor.umgebung_temperatur",
            CONF_LOCAL_PRECIPITATION: "sensor.umgebung_niederschlagsmenge",
        },
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    temp = hass.states.get("sensor.wetter_temperatur")
    assert temp.state == "15.2" and temp.attributes["source"] == "sensor.umgebung_temperatur"
    source = hass.states.get("sensor.wetter_quelle")
    assert source.state == "local", "beide eingetragenen Werte aus der Nachbarschaft"
    assert source.attributes["temperature"] == "sensor.umgebung_temperatur"
    assert source.attributes["humidity"] == "dwd", "nicht eingetragen → DWD"
    assert hass.states.get(WEATHER).attributes["temperature"] == 15.2

    hass.states.async_set("sensor.umgebung_niederschlagsmenge", "1.2")
    await hass.async_block_till_done()
    assert hass.states.get(WEATHER).state == "rainy", "Regen vor Ort, obwohl die Station trocken meldet"
    hass.states.async_set("sensor.umgebung_niederschlagsmenge", "6")
    await hass.async_block_till_done()
    assert hass.states.get(WEATHER).state == "pouring"

    hass.states.async_set("sensor.umgebung_temperatur", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wetter_temperatur").state == str(CURRENT["temperature"]), "Ausfall → DWD"
    assert hass.states.get("sensor.wetter_quelle").state == "mixed", "Regen noch von nebenan, Temperatur vom DWD"
    hass.states.async_set("sensor.umgebung_temperatur", "kaputt")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wetter_temperatur").attributes["source"] == "dwd"
    hass.states.async_set("sensor.umgebung_temperatur", "nan")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wetter_temperatur").attributes["source"] == "dwd", "NaN ist kein Messwert"

    hass.states.async_set("sensor.umgebung_temperatur", "16.0")
    await hass.async_block_till_done()
    calls = brightsky.call_count
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert brightsky.call_count > calls, "Aktualisierung nach 10 Min. gelaufen"
    assert hass.states.get("sensor.wetter_temperatur").state == str(CURRENT["temperature"]), "veraltet → DWD"


async def test_local_rain_stops_station_rain(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Station meldet Regen, vor Ort ist es trocken → bewölkt."""
    aioclient_mock.get(f"{API_URL}/current_weather", json={"weather": {**CURRENT, "icon": "rain"}})
    aioclient_mock.get(f"{API_URL}/weather", json=load("weather.json"))
    aioclient_mock.get(f"{API_URL}/alerts", json={"alerts": [], "location": None})
    hass.states.async_set("sensor.regen", "0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
        options={CONF_LOCAL_PRECIPITATION: "sensor.regen"},
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    assert hass.states.get(WEATHER).state == "cloudy"
    assert hass.states.get("sensor.wetter_warnungen").state == "0"


@pytest.mark.parametrize(
    ("icon", "expected"),
    [
        ("thunderstorm", "lightning"),
        ("sleet", "snowy-rainy"),
        ("snow", "snowy"),
        ("hail", "hail"),
    ],
)
async def test_local_dry_keeps_snow_and_hail(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, icon: str, expected: str
) -> None:
    """Trocken vor Ort: Gewitter bleibt Gewitter ohne Regen; Schnee und Hagel sieht ein Regenmesser nicht."""
    aioclient_mock.get(f"{API_URL}/current_weather", json={"weather": {**CURRENT, "icon": icon}})
    aioclient_mock.get(f"{API_URL}/weather", json=load("weather.json"))
    aioclient_mock.get(f"{API_URL}/alerts", json={"alerts": [], "location": None})
    hass.states.async_set("sensor.regen", "0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
        options={CONF_LOCAL_PRECIPITATION: "sensor.regen"},
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    assert hass.states.get(WEATHER).state == expected


async def test_values_cached_per_update(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Die zusammengeführten Werte werden je Aktualisierung einmal gerechnet; alle Entitäten lesen denselben Stand."""
    await _setup(hass, entry)
    coordinator = entry.runtime_data.weather
    with patch("custom_components.wetter.model.now_values", wraps=model.now_values) as spy:
        first = coordinator.values()
        assert coordinator.values() is first and spy.call_count == 0, "aus dem Cache"
        coordinator.async_update_listeners()
        assert coordinator.values() is not first and spy.call_count == 1, "neue Daten → neu gerechnet"
        assert coordinator.values(dt_util.utcnow()) is not coordinator.values(), "mit Zeitpunkt immer frisch"


async def test_store_written_only_on_change(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Unveränderte Antworten schreiben den Speicher nicht neu (Flash schonen); geänderte schon."""
    await _setup(hass, entry)

    async def tick(minutes: int) -> None:
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    with patch("custom_components.wetter.coordinator.Store.async_delay_save") as save:
        await tick(11)
        assert brightsky.call_count > 0
        assert save.call_count == 0, "Wetter 1× und Warnungen 5× geholt, nichts geändert → kein Schreiben"

        brightsky.clear_requests()
        brightsky.get(f"{API_URL}/current_weather", json={"weather": {**CURRENT, "temperature": 1.5}})
        brightsky.get(f"{API_URL}/weather", json=load("weather.json"))
        brightsky.get(f"{API_URL}/alerts", json={"alerts": [], "location": {"warn_cell_id": WARN_CELL}})
        brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
        brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.json", json=load("nina_warning.json"))
        await tick(11)
        assert save.call_count == 2, "Messung und Warnliste geändert → je einmal geschrieben"
        assert hass.states.get("sensor.wetter_temperatur").state == "1.5"


async def test_expired_warnings_disappear_even_when_sources_fail(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Fällt die Quelle aus, bleibt ihre Warnung stehen, endet aber pünktlich; `stale` nennt die Quelle."""
    await _setup(hass, entry)
    warn = hass.states.get("sensor.wetter_warnungen")
    assert warn.state == "3" and warn.attributes["stale"] == []

    async def tick(minutes: int) -> None:
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/alerts", exc=TimeoutError)
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", exc=TimeoutError)
    await tick(3)
    warn = hass.states.get("sensor.wetter_warnungen")
    assert warn.state == "3", "beide Quellen weg, Warnungen bleiben"
    assert warn.attributes["stale"] == ["DWD", "NINA"]

    # 20:00 Ortszeit laufen beide DWD-Warnungen aus, die MoWaS-Meldung hat kein Ende
    freezer.move_to("2026-10-15 18:00:30+00:00")
    assert entry.runtime_data.warnings.warnings[0]["provider"] == "MOWAS", "schon vor der Abfrage gefiltert"
    await tick(1)
    warn = hass.states.get("sensor.wetter_warnungen")
    assert warn.state == "1" and warn.attributes["warnings"][0]["provider"] == "MOWAS"
    assert hass.states.get("binary_sensor.wetter_warnung_2").state == "off"
    assert entry.runtime_data.warnings.last_update_success, "Aufräumen zählt als Stand, nicht als Ausfall"
    await tick(2)
    assert not entry.runtime_data.warnings.last_update_success, "weiter nichts erreichbar und nichts abgelaufen"


async def test_start_from_saved_state(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Zweiter Start: letzter Stand sofort, Bright Sky erst nach dem HA-Start."""
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["hourly"]

    calls = brightsky.call_count
    hass.set_state(CoreState.starting)
    await _setup(hass, entry)
    assert brightsky.call_count == calls, "beim Start keine Abfrage"
    assert hass.states.get(WEATHER).state == "cloudy"

    hass.set_state(CoreState.running)
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done(wait_background_tasks=True)
    # Messung + Vorhersage, DWD-Warnungen + NINA-Kreis + Volltext und Warngebiet der MoWaS-Meldung
    assert brightsky.call_count == calls + 6, "nach dem Start frisch"


async def test_not_ready_when_bright_sky_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{API_URL}/current_weather", status=503)
    aioclient_mock.get(f"{API_URL}/weather", json=load("weather.json"))
    aioclient_mock.get(f"{API_URL}/alerts", json={"alerts": []})
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/current_weather", {"weather": None}),
        ("/weather", {"weather": []}),
        ("/weather", ["keine", "dict"]),
    ],
)
async def test_bad_answers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, path: str, payload: Any
) -> None:
    good = {
        "/current_weather": load("current.json"),
        "/weather": load("weather.json"),
        "/alerts": {"alerts": "kaputt", "location": "kaputt"},
    }
    good[path] = payload
    for p, body in good.items():
        aioclient_mock.get(f"{API_URL}{p}", json=body)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_config_flow(hass: HomeAssistant, brightsky: AiohttpClientMocker) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    user = {CONF_NAME: "Wetterstation", CONF_LOCATION: {CONF_LATITUDE: LAT, CONF_LONGITUDE: LON}}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wetterstation"
    assert result["data"] == {CONF_LATITUDE: LAT, CONF_LONGITUDE: LON}
    await hass.async_block_till_done()

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    again = await hass.config_entries.flow.async_configure(again["flow_id"], user)
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "already_configured"


async def test_config_flow_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{API_URL}/weather", exc=TimeoutError)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Wetter", CONF_LOCATION: {CONF_LATITUDE: 1.0, CONF_LONGITUDE: 2.0}}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}


async def test_options_flow(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    hass.states.async_set("sensor.umgebung_temperatur", "15.0", {"device_class": "temperature"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LOCAL_TEMPERATURE: "sensor.umgebung_temperatur"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_LOCAL_TEMPERATURE: "sensor.umgebung_temperatur"}
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wetter_temperatur").state == "15.0", "nach dem Neuladen aktiv"
    again = await hass.config_entries.options.async_init(entry.entry_id)
    assert again["type"] is FlowResultType.FORM


async def test_diagnostics_hide_location(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_client: ClientSessionGenerator
) -> None:
    await _setup(hass, entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["entry"][CONF_LATITUDE] == "**REDACTED**"
    assert diag["warn_area"] == "**REDACTED**", "die Gemeinde verrät den Ort fast genauso"
    assert diag["warn_stale"] == []
    assert diag["hours"] > 48 and len(diag["warnings"]) == 3
    assert diag["now"]["temperature"] == CURRENT["temperature"]


async def test_warnings_survive_outages(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Fällt eine Quelle aus, bleiben ihre Warnungen stehen – keine falsche Entwarnung, Sensoren bleiben verfügbar."""
    await _setup(hass, entry)
    assert hass.states.get("sensor.wetter_warnungen").state == "3"

    async def tick() -> None:
        freezer.tick(timedelta(minutes=3))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/alerts", status=500)
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json=[])
    await tick()
    warn = hass.states.get("sensor.wetter_warnungen")
    assert warn.state == "2", "DWD weg → letzte DWD-Warnungen bleiben; NINA leer → MoWaS aufgehoben"
    assert {w["provider"] for w in warn.attributes["warnings"]} == {"DWD"}

    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/alerts", json={"alerts": [], "location": {"warn_cell_id": WARN_CELL}})
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", exc=TimeoutError)
    await tick()
    assert hass.states.get("sensor.wetter_warnungen").state == "0", "DWD meldet Ende, NINA (ohne Meldung) bleibt leer"

    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/alerts", exc=TimeoutError)
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", exc=TimeoutError)
    await tick()
    slot = hass.states.get("binary_sensor.wetter_warnung_1")
    assert slot.state == "off", "beide weg → verfügbar mit letztem Stand statt „nicht verfügbar“"


async def test_nina_detail_missing(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    """Volltext nicht ladbar → Meldung trotzdem da (Überschrift aus dem Dashboard), nächstes Mal neuer Versuch."""
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/current_weather", json=load("current.json"))
    brightsky.get(f"{API_URL}/weather", json=load("weather.json"))
    brightsky.get(f"{API_URL}/alerts", json={"alerts": [], "location": {"warn_cell_id": WARN_CELL}})
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
    brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.json", status=404)
    await _setup(hass, entry)
    slot = hass.states.get("binary_sensor.wetter_warnung_1")
    assert slot.state == "on" and "Trinkwasser" in slot.attributes["headline"]
    assert slot.attributes["description"] is None and slot.attributes["severity"] == "Minor"


async def test_nina_warning_elsewhere_in_district(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Warngebiet trifft das Zuhause nicht (anderer Zipfel des Kreises) → Meldung weg; ohne Geometrie bleibt sie."""
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/current_weather", json=load("current.json"))
    brightsky.get(f"{API_URL}/weather", json=load("weather.json"))
    brightsky.get(f"{API_URL}/alerts", json={"alerts": [], "location": {"warn_cell_id": WARN_CELL}})
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
    brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.json", json=load("nina_warning.json"))
    brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.geojson", json=FAR_AWAY)
    await _setup(hass, entry)
    assert hass.states.get("sensor.wetter_warnungen").state == "0"
    assert hass.states.get("binary_sensor.wetter_warnung_1").state == "off"

    # Geometrie nicht ladbar → Kreis-Meldung wie bisher anzeigen
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/current_weather", json=load("current.json"))
    brightsky.get(f"{API_URL}/weather", json=load("weather.json"))
    brightsky.get(f"{API_URL}/alerts", json={"alerts": [], "location": {"warn_cell_id": WARN_CELL}})
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
    brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.json", json=load("nina_warning.json"))
    brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.geojson", status=500)
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Zweitwohnung",
        unique_id="52.520,13.420",
        data={CONF_LATITUDE: 52.52, CONF_LONGITUDE: 13.42},
    )
    other.add_to_hass(hass)
    await _setup(hass, other)
    assert hass.states.get("sensor.zweitwohnung_warnungen").state == "1"

    brightsky.get(f"{NINA_URL}/warnings/x.geojson", json=["kein", "geojson"])
    with pytest.raises(WetterError):
        await NinaClient(async_get_clientsession(hass)).geometry("x")


async def test_reconfigure(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    """Ort ändern: Daten + Kennung neu, Eintrag lädt neu; ein anderer schon eingerichteter Ort wird abgelehnt."""
    await _setup(hass, entry)
    other = MockConfigEntry(domain=DOMAIN, unique_id="50.000,8.000", data={CONF_LATITUDE: 50.0, CONF_LONGITUDE: 8.0})
    other.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: {CONF_LATITUDE: 50.0, CONF_LONGITUDE: 8.0}}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: {CONF_LATITUDE: LAT, CONF_LONGITUDE: LON}}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful", "gleicher Ort"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: {CONF_LATITUDE: 52.6, CONF_LONGITUDE: 13.5}}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == {CONF_LATITUDE: 52.6, CONF_LONGITUDE: 13.5}
    assert entry.unique_id == "52.600,13.500"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_cannot_connect(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/weather", exc=TimeoutError)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: {CONF_LATITUDE: 52.6, CONF_LONGITUDE: 13.5}}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}


async def test_remove_entry_deletes_saved_state(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    await _setup(hass, entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage
    assert f"{DOMAIN}.{entry.entry_id}.warnings" in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage
    assert f"{DOMAIN}.{entry.entry_id}.warnings" not in hass_storage


async def test_source_sensor_is_diagnostic(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    assert er.async_get(hass).async_get("sensor.wetter_quelle").entity_category == "diagnostic"


async def test_nina_bad_answers(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    """Unbrauchbare NINA-Antworten: Kreis-Liste kein Array → wie Ausfall; Volltext kein Objekt → ohne Volltext."""
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/current_weather", json=load("current.json"))
    brightsky.get(f"{API_URL}/weather", json=load("weather.json"))
    brightsky.get(f"{API_URL}/alerts", json={"alerts": [], "location": {"warn_cell_id": WARN_CELL}})
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json={"kaputt": True})
    await _setup(hass, entry)
    assert hass.states.get("sensor.wetter_warnungen").state == "0"

    brightsky.get(f"{NINA_URL}/warnings/x.json", json=["keine", "meldung"])
    with pytest.raises(WetterError):
        await NinaClient(async_get_clientsession(hass)).warning("x")
