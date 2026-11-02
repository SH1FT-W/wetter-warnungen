"""Pollen und UV-Index: reine Auswertung und Einbindung (eigener Takt, Tageswechsel, Ausfälle, abschaltbar)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.wetter import health
from custom_components.wetter.api import DwdHealthClient, WetterError
from custom_components.wetter.const import (
    API_URL,
    CONF_POLLEN_UV,
    DOMAIN,
    NINA_URL,
    POLLEN_REGION_URL,
    POLLEN_URL,
    UV_URL,
)

from .conftest import ARS, LAT, LON, MOWAS_ID, load

POLLEN = load("dwd_pollen.json")
UV = load("dwd_uv.json")
HAZEL = "sensor.wetter_pollen_hasel"
UV_TODAY = "sensor.wetter_uv_index"
UV_TOMORROW = "sensor.wetter_uv_index_morgen"


# --- pure functions -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "level"),
    [
        ("0", 0.0),
        ("0-1", 0.5),
        ("1", 1.0),
        ("1-2", 1.5),
        ("2-3", 2.5),
        ("3", 3.0),
        ("-1", None),
        ("x", None),
        (None, None),
    ],
)
def test_pollen_level(raw: Any, level: float | None) -> None:
    assert health.pollen_level(raw) == level


@pytest.mark.parametrize(("raw", "level"), [(3, 3), (2.6, 3), (0, 0), (-1, None), (True, None), ("3", None)])
def test_uv_level(raw: Any, level: int | None) -> None:
    assert health.uv_level(raw) == level


def test_parse_dwd_time() -> None:
    assert health.parse_dwd_time("2026-10-16 11:00 Uhr") == datetime(2026, 10, 16, 11, tzinfo=health.DWD_TZ)
    assert health.parse_dwd_time("2026-10-16T07:30:00") == datetime(2026, 10, 16, 7, 30, tzinfo=health.DWD_TZ)
    assert health.parse_dwd_time("2026-10-16T07:30:00+00:00").utcoffset() == timedelta(0), "Zone bleibt"  # type: ignore[union-attr]
    assert health.parse_dwd_time("morgen") is None
    assert health.parse_dwd_time(None) is None


def test_by_day_shifts_old_files() -> None:
    values = [1, 2, 3]
    assert health.by_day(values, "2026-10-15", date(2026, 10, 15)) == [1, 2, 3]
    assert health.by_day(values, "2026-10-14", date(2026, 10, 15)) == [2, 3, None], "Datei von gestern"
    assert health.by_day(values, "2026-10-11", date(2026, 10, 15)) == [None, None, None], "zu alt"
    assert health.by_day(values, "2026-10-16", date(2026, 10, 15)) == [1, 2, 3], "Uhr der Quelle vorn"
    assert health.by_day(values, None, date(2026, 10, 15)) == [None, None, None]


def test_pollen_for_region() -> None:
    berlin = health.pollen_for(POLLEN, 50)
    assert berlin is not None
    assert berlin["region"] == "Brandenburg und Berlin", "Region ohne Teilregionen (partregion_id -1)"
    assert berlin["issued"] == "2026-10-15" and berlin["next_update"] == "2026-10-16 11:00 Uhr"
    assert berlin["values"]["hazel"] == [0.5, 1.0, 1.5]
    assert berlin["values"]["alder"] == [0.0, 0.5, None], "„-1“ = keine Vorhersage"
    assert set(berlin["values"]) == set(health.POLLEN.values()), "alle acht Pollenarten in der echten Datei"
    west = health.pollen_for(POLLEN, 31)
    assert west is not None and west["region"] == "Westl. Niedersachsen/Bremen"
    assert health.pollen_for(POLLEN, 30) is None, "Region mit Teilregionen nur über die Teilregion"
    assert health.pollen_for({"content": ["kaputt"], "last_update": "kaputt"}, 50) is None
    assert health.pollen_for({"content": [{"region_id": 50, "partregion_id": -1}]}, 50) is None, "keine Pollen"
    odd = health.pollen_for(
        {"content": [{"region_id": 50, "partregion_id": -1, "Pollen": {"Hasel": {}}}], "last_update": "kaputt"}, 50
    )
    assert odd == {"region": None, "issued": None, "next_update": None, "values": {"hazel": [None, None, None]}}


def test_uv_for_nearest_place() -> None:
    berlin = health.uv_for(UV, LAT, LON)
    assert berlin == {
        "city": "Berlin",
        "issued": "2026-10-15",
        "next_update": "2026-10-16T07:30:00",
        "values": [3, 2, 1],
    }
    garmisch = health.uv_for(UV, 47.49, 11.10)
    assert garmisch is not None and garmisch["city"] == "München", "nie die Zugspitze, Gipfel haben viel mehr UV"
    assert health.uv_for(UV, 40.0, 0.0) is None, "weiter als 200 km: kein Ort"
    assert health.uv_for({"content": [{"city": "Atlantis", "forecast": {"today": 1}}]}, LAT, LON) is None
    assert health.uv_for({"forecast_day": "kaputt", "content": [{"city": "Berlin", "forecast": {}}]}, LAT, LON) == {
        "city": "Berlin",
        "issued": None,
        "next_update": None,
        "values": [None, None, None],
    }
    assert set(health.UV_CITIES) == {c["city"] for c in UV["content"]}, "Tabelle passt zur echten Datei"


def test_region_cell() -> None:
    assert health.region_cell(load("dwd_pollen_region.json")) == 50
    assert health.region_cell({"type": "FeatureCollection", "features": []}) is None, "außerhalb der Regionen"
    assert health.region_cell({"features": [None, {"properties": {"CELL_ID": True}}, {"properties": {}}]}) is None
    assert health.region_cell([]) is None


# --- integration ----------------------------------------------------------------------------------------


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED


def _brightsky(mock: AiohttpClientMocker) -> None:
    """Bright Sky again after `clear_requests` (weather and an empty warning list)."""
    for path, data in (
        ("/current_weather", load("current.json")),
        ("/weather", load("weather.json")),
        ("/alerts", {"alerts": [], "location": None}),
    ):
        mock.get(f"{API_URL}{path}", json=data)


def _calls(mock: AiohttpClientMocker, url: str) -> int:
    return sum(1 for _method, called, *_ in mock.mock_calls if str(called).startswith(url))


async def _tick(hass: HomeAssistant, freezer: Any, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_entities(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    hazel = hass.states.get(HAZEL)
    assert hazel.state == "0.5"
    assert hazel.attributes["today"] == 0.5 and hazel.attributes["tomorrow"] == 1.0
    assert hazel.attributes["day_after_tomorrow"] == 1.5
    assert hazel.attributes["region"] == "Brandenburg und Berlin"
    assert hazel.attributes["attribution"] == "Deutscher Wetterdienst"
    assert hass.states.get("sensor.wetter_pollen_graser").state == "2.5"
    assert hass.states.get("sensor.wetter_pollen_erle").attributes["day_after_tomorrow"] is None
    assert hass.states.get(UV_TODAY).state == "3"
    assert hass.states.get(UV_TODAY).attributes["city"] == "Berlin"
    assert hass.states.get(UV_TODAY).attributes["unit_of_measurement"] == "UV index"
    assert hass.states.get(UV_TOMORROW).state == "2"
    assert _calls(brightsky, POLLEN_REGION_URL) == 1


async def test_fetch_only_when_due_and_shift_at_midnight(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Stündlicher Takt lädt nur, wenn neue Dateien fällig sind; um Mitternacht rückt morgen auf heute."""
    await _setup(hass, entry)
    assert _calls(brightsky, POLLEN_URL) == 1 and _calls(brightsky, UV_URL) == 1
    await _tick(hass, freezer, timedelta(hours=1, minutes=1))
    assert _calls(brightsky, POLLEN_URL) == 1, "next_update noch nicht erreicht → nichts geladen"

    # 23:40 Ortszeit (12 Std. nach dem Laden): einmal neu laden, auch wenn next_update noch aussteht
    freezer.move_to("2026-10-15 21:40:00+00:00")
    await _tick(hass, freezer, timedelta(minutes=1))
    assert _calls(brightsky, UV_URL) == 2, "spätestens nach 12 Std."

    # 16.10. 00:00:05 Ortszeit = 15.10. 22:00:05 UTC: neuer Tag, alte Dateien, ohne Warten auf den Takt
    freezer.move_to("2026-10-15 22:00:05+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(HAZEL).state == "1.0", "gestern „morgen“ ist heute"
    assert hass.states.get(HAZEL).attributes["day_after_tomorrow"] is None
    assert hass.states.get(UV_TODAY).state == "2" and hass.states.get(UV_TOMORROW).state == "1"
    assert _calls(brightsky, UV_URL) == 2

    # 16.10. 07:30 Ortszeit: neue UV-Datei fällig → beide Dateien laden, Region bleibt gespeichert
    freezer.move_to("2026-10-16 05:30:00+00:00")
    await _tick(hass, freezer, timedelta(hours=1))
    assert _calls(brightsky, UV_URL) == 3 and _calls(brightsky, POLLEN_URL) == 3
    assert _calls(brightsky, POLLEN_REGION_URL) == 1


async def test_refetch_after_max_age(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Steht `next_update` weit in der Zukunft oder ist unlesbar, wird spätestens nach 12 Std. neu geladen."""
    brightsky.clear_requests()
    brightsky.get(POLLEN_URL, json={**POLLEN, "next_update": "2027-01-01 11:00 Uhr"})
    brightsky.get(UV_URL, json={**UV, "next_update": "kaputt"})
    brightsky.get(POLLEN_REGION_URL, json=load("dwd_pollen_region.json"))
    _brightsky(brightsky)
    await _setup(hass, entry)
    coordinator = entry.runtime_data.health
    assert coordinator is not None
    assert not coordinator._due(coordinator.data, datetime.fromisoformat("2026-10-15T10:00:00+00:00")), (
        "unlesbarer UV-Termin: kein Dauerabruf, nur die 12 Std. gelten"
    )
    data = {**coordinator.data, "uv": {**coordinator.data["uv"], "next_update": "2027-01-01T07:30:00"}}
    assert not coordinator._due(data, datetime.fromisoformat("2026-10-15T21:00:00+00:00"))
    assert coordinator._due(data, datetime.fromisoformat("2026-10-15T21:40:00+00:00")), "12 Std. nach dem Laden"
    assert coordinator._due({**data, "fetched": None}, datetime.fromisoformat("2026-10-15T10:00:00+00:00"))


async def test_failures_keep_last_values(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Fällt eine Quelle aus, bleiben ihre letzten Werte; Wetter und Warnungen merken davon nichts."""
    await _setup(hass, entry)
    brightsky.clear_requests()
    brightsky.get(POLLEN_URL, exc=TimeoutError)
    brightsky.get(UV_URL, status=500)
    _brightsky(brightsky)
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
    brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.json", json=load("nina_warning.json"))
    brightsky.get(f"{NINA_URL}/warnings/{MOWAS_ID}.geojson", json=load("nina_warning.geojson"))
    freezer.move_to("2026-10-16 09:30:00+00:00")
    await _tick(hass, freezer, timedelta(minutes=31))
    coordinator = entry.runtime_data.health
    assert coordinator is not None and coordinator.last_update_success, "alter Stand gilt weiter"
    assert hass.states.get(HAZEL).state == "1.0", "alter Stand, um einen Tag verschoben"
    assert hass.states.get(UV_TODAY).state == "2"
    assert entry.runtime_data.warnings.last_update_success, "Warnungen unberührt"
    assert entry.runtime_data.weather.last_update_success


async def test_first_fetch_fails(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    """Ohne jeden Stand und ohne DWD: Pollen/UV nicht verfügbar, die Integration startet trotzdem sofort."""
    brightsky.clear_requests()
    brightsky.get(POLLEN_URL, exc=TimeoutError)
    brightsky.get(UV_URL, exc=TimeoutError)
    brightsky.get(POLLEN_REGION_URL, exc=TimeoutError)
    _brightsky(brightsky)
    await _setup(hass, entry)
    assert hass.states.get(HAZEL).state == "unavailable"
    assert hass.states.get(UV_TODAY).state == "unavailable"
    assert hass.states.get("weather.wetter").state == "cloudy"


async def test_region_lookup_fails_then_recovers(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Region nicht ermittelbar: UV gibt es trotzdem, Pollen erst nach dem nächsten Versuch."""
    brightsky.clear_requests()
    brightsky.get(POLLEN_REGION_URL, status=503)
    brightsky.get(POLLEN_URL, json=POLLEN)
    brightsky.get(UV_URL, json=UV)
    _brightsky(brightsky)
    await _setup(hass, entry)
    assert hass.states.get(HAZEL).state == "unknown"
    assert hass.states.get(UV_TODAY).state == "3"
    brightsky.clear_requests()
    brightsky.get(POLLEN_REGION_URL, json=load("dwd_pollen_region.json"))
    brightsky.get(POLLEN_URL, json=POLLEN)
    brightsky.get(UV_URL, json=UV)
    await _tick(hass, freezer, timedelta(hours=1, minutes=1))
    assert hass.states.get(HAZEL).state == "0.5", "keine Pollen → beim nächsten Takt neu versucht"


async def test_start_from_store(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Zweiter Start: Werte sofort aus dem Speicher, kein Download, Region nicht neu gefragt."""
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    async_fire_time_changed(hass, datetime.fromisoformat("2026-10-15T09:41:00+00:00"))
    await hass.async_block_till_done()
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.health"]["data"]
    assert stored["cell"] == 50 and stored["uv"]["city"] == "Berlin"
    calls = len(brightsky.mock_calls)
    await _setup(hass, entry)
    assert hass.states.get(HAZEL).state == "0.5"
    assert _calls(brightsky, POLLEN_URL) == 1 and _calls(brightsky, POLLEN_REGION_URL) == 1
    assert len(brightsky.mock_calls) > calls, "Wetter und Warnungen schon"


async def test_moved_location_looks_up_again(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Gespeicherter Stand für einen anderen Ort zählt nicht."""
    hass_storage[f"{DOMAIN}.{entry.entry_id}.health"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}.health",
        "data": {"where": "1.0000,2.0000", "cell": 31, "pollen": None, "uv": None, "fetched": None},
    }
    await _setup(hass, entry)
    assert _calls(brightsky, POLLEN_REGION_URL) == 1
    assert hass.states.get(HAZEL).attributes["region"] == "Brandenburg und Berlin"


async def test_switched_off(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    """Abgeschaltet: keine Abfragen, keine Entitäten, alte Registry-Einträge weg."""
    await _setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get(HAZEL) is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_POLLEN_UV: False})
    assert entry.options == {CONF_POLLEN_UV: False}, "Aus bleibt gespeichert"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.health is None
    assert registry.async_get(HAZEL) is None and registry.async_get(UV_TODAY) is None
    assert hass.states.get(HAZEL) is None
    assert hass.states.get("sensor.wetter_temperatur") is not None, "übrige Sensoren bleiben"
    again = await hass.config_entries.options.async_init(entry.entry_id)
    assert again["data_schema"]({})[CONF_POLLEN_UV] is False, "Formular zeigt den gespeicherten Schalter"


async def test_off_from_the_start(hass: HomeAssistant, brightsky: AiohttpClientMocker) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Wetter", data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON}, options={CONF_POLLEN_UV: False}
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    assert _calls(brightsky, POLLEN_URL) == 0 and _calls(brightsky, UV_URL) == 0
    assert hass.states.get(UV_TODAY) is None


async def test_remove_deletes_store(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    await _setup(hass, entry)
    async_fire_time_changed(hass, datetime.fromisoformat("2026-10-15T09:41:00+00:00"))
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}.health" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}.health" not in hass_storage


async def test_diagnostics(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_client: ClientSessionGenerator
) -> None:
    await _setup(hass, entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["health"]["where"] == "**REDACTED**"
    assert diag["health"]["cell"] == 50 and diag["health"]["uv"]["city"] == "Berlin"


async def test_client_rejects_odd_answers(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(POLLEN_URL, json=["keine Tabelle"])
    aioclient_mock.get(UV_URL, text="kein JSON")
    client = DwdHealthClient(async_get_clientsession(hass))
    with pytest.raises(WetterError, match="unerwartete Antwort"):
        await client.pollen()
    with pytest.raises(WetterError):
        await client.uv()


async def test_empty_answer_keeps_last_values(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Gültige, aber leere DWD-Datei löscht die letzten Werte nicht (Gegenprüfung, Fund 2)."""
    await _setup(hass, entry)
    brightsky.clear_requests()
    brightsky.get(POLLEN_URL, json={"content": []})
    brightsky.get(UV_URL, json={"content": []})
    _brightsky(brightsky)
    freezer.move_to("2026-10-16 09:30:00+00:00")
    await _tick(hass, freezer, timedelta(minutes=31))
    assert _calls(brightsky, POLLEN_URL) == 1, "neu geladen"
    assert hass.states.get(HAZEL).state == "1.0", "alter Stand, um einen Tag verschoben"
    assert hass.states.get(UV_TODAY).state == "2"


async def test_outside_regions_is_not_fetched_hourly(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Keine Pollenregion am Ort: gemerkt, kein stündlicher Dauerabruf (Gegenprüfung, Fund 3)."""
    brightsky.clear_requests()
    brightsky.get(POLLEN_REGION_URL, json={"type": "FeatureCollection", "features": []})
    brightsky.get(POLLEN_URL, json=POLLEN)
    brightsky.get(UV_URL, json=UV)
    _brightsky(brightsky)
    await _setup(hass, entry)
    for _ in range(5):
        await _tick(hass, freezer, timedelta(minutes=61))
    assert _calls(brightsky, POLLEN_REGION_URL) == 1
    assert _calls(brightsky, UV_URL) == 1 and _calls(brightsky, POLLEN_URL) == 1
    assert hass.states.get(HAZEL).state == "unknown" and hass.states.get(UV_TODAY).state == "3"


async def test_today_is_german_date(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """HA in einer anderen Zeitzone: heute/morgen folgen dem deutschen Kalendertag der DWD-Dateien (Fund 7)."""
    await hass.config.async_set_time_zone("America/New_York")
    await _setup(hass, entry)
    # 16.10. 01:00 in Berlin, in New York noch der 15.10.
    freezer.move_to("2026-10-15 23:00:00+00:00")
    coordinator = entry.runtime_data.health
    assert coordinator is not None
    assert coordinator.pollen("hazel")[0] == 1.0


async def test_empty_region_answer_is_asked_again(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Leere WFS-Antwort (Ebene gerade im Tageswechsel) ist nicht endgültig: beim nächsten Download neu (B1)."""
    brightsky.clear_requests()
    brightsky.get(POLLEN_REGION_URL, json={"type": "FeatureCollection", "features": []})
    brightsky.get(POLLEN_URL, json=POLLEN)
    brightsky.get(UV_URL, json=UV)
    _brightsky(brightsky)
    await _setup(hass, entry)
    brightsky.clear_requests()
    brightsky.get(POLLEN_REGION_URL, json=load("dwd_pollen_region.json"))
    brightsky.get(POLLEN_URL, json=POLLEN)
    brightsky.get(UV_URL, json=UV)
    _brightsky(brightsky)
    for _ in range(30):
        await _tick(hass, freezer, timedelta(hours=1))
    assert _calls(brightsky, POLLEN_REGION_URL) == 1
    assert hass.states.get(HAZEL).attributes["region"] == "Brandenburg und Berlin"


async def test_region_service_down_does_not_download_hourly(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Geodienst weg: nur die Region wird stündlich neu gefragt, die Dateien nicht (B2)."""
    brightsky.clear_requests()
    brightsky.get(POLLEN_REGION_URL, status=503)
    brightsky.get(POLLEN_URL, json=POLLEN)
    brightsky.get(UV_URL, json=UV)
    _brightsky(brightsky)
    await _setup(hass, entry)
    for _ in range(6):
        await _tick(hass, freezer, timedelta(minutes=61))
    assert _calls(brightsky, UV_URL) == 1 and _calls(brightsky, POLLEN_URL) == 1
    assert _calls(brightsky, POLLEN_REGION_URL) == 7


async def test_late_or_reshaped_file_is_not_fetched_forever(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Nach next_update ohne passende neue Datei: ein paar Versuche, dann nur noch alle 12 Std. (B3)."""
    await _setup(hass, entry)
    brightsky.clear_requests()
    brightsky.get(POLLEN_URL, json={**POLLEN, "content": []})
    brightsky.get(UV_URL, json={"content": []})
    _brightsky(brightsky)
    freezer.move_to("2026-10-16 09:30:00+00:00")
    for _ in range(8):
        await _tick(hass, freezer, timedelta(minutes=61))
    assert 1 <= _calls(brightsky, POLLEN_URL) <= 2


async def test_one_file_broken_for_good(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """UV-Datei dauerhaft weg: wenige neue Versuche, kein stündlicher Download der Pollendatei (Runde 3, H1)."""
    await _setup(hass, entry)
    brightsky.clear_requests()
    brightsky.get(POLLEN_URL, json=POLLEN)
    brightsky.get(UV_URL, status=404)
    _brightsky(brightsky)
    for _ in range(24):
        await _tick(hass, freezer, timedelta(minutes=61))
    # 1 + 3 retries after the 12 h, then one try per `next_update` window of each file: bounded, not hourly
    assert _calls(brightsky, POLLEN_URL) <= 8


async def test_reshaped_file_keeps_last_values(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Umgebaute Datei (Pollen als Liste): kein Absturz, alter Stand bleibt, kein Dauerabruf (Runde 3, H2)."""
    await _setup(hass, entry)
    reshaped = {**POLLEN, "content": [{**c, "Pollen": ["Hasel"]} for c in POLLEN["content"]]}
    brightsky.clear_requests()
    brightsky.get(POLLEN_URL, json=reshaped)
    brightsky.get(UV_URL, json={"content": "kaputt"})
    _brightsky(brightsky)
    freezer.move_to("2026-10-15 21:41:00+00:00")
    for _ in range(6):
        await _tick(hass, freezer, timedelta(minutes=61))
    coordinator = entry.runtime_data.health
    assert coordinator is not None and coordinator.last_update_success
    assert _calls(brightsky, POLLEN_URL) <= 2
    assert hass.states.get(HAZEL).state == "1.0", "alter Stand (am 16.10. um einen Tag verschoben)"


def test_region_cell_odd_properties() -> None:
    assert health.region_cell({"features": [{"properties": ["CELL_ID", 50]}]}) is None
    assert health.uv_for({"content": 5}, LAT, LON) is None
    assert health.pollen_for({"content": {"x": 1}}, 50) is None
