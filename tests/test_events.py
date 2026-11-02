"""Ereignisse wetter_warning_new / wetter_warning_ended: je Warnung genau einmal, auch über Neustarts."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wetter import warnings
from custom_components.wetter.const import API_URL, DOMAIN, EVENT_WARNING_ENDED, EVENT_WARNING_NEW, NINA_URL

from .conftest import ALERT, ARS, MINOR, MOWAS_ID, WARN_CELL, load


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED


async def _tick(hass: HomeAssistant, freezer: Any, minutes: int = 3) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _alerts(mock: AiohttpClientMocker, alerts: list[dict[str, Any]]) -> None:
    mock.clear_requests()
    mock.get(
        f"{API_URL}/alerts",
        json={"alerts": alerts, "location": {"name": "Gemeinde Musterstadt", "warn_cell_id": WARN_CELL}},
    )
    mock.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
    mock.get(f"{NINA_URL}/warnings/{MOWAS_ID}.json", json=load("nina_warning.json"))
    mock.get(f"{NINA_URL}/warnings/{MOWAS_ID}.geojson", json=load("nina_warning.geojson"))


def test_event_data() -> None:
    dwd = warnings.from_dwd(ALERT, "Gemeinde Musterstadt")
    assert warnings.event_data(dwd, "egal", "e1") == {
        "entry_id": "e1",
        "id": ALERT["alert_id"],
        "source": "DWD",
        "provider": "DWD",
        "severity": "Moderate",
        "headline": "Amtliche WARNUNG vor STURMBÖEN",
        "event": "STURMBÖEN",
        "area": "Gemeinde Musterstadt",
        "start": ALERT["onset"],
        "expires": ALERT["expires"],
    }
    bare = warnings.event_data({"id": "x", "provider": "KATWARN"}, None, "e1")
    assert bare["source"] == "NINA" and bare["area"] is None
    assert warnings.event_data({"id": "x"}, "Gemeinde Musterstadt", "e1")["area"] == "Gemeinde Musterstadt"


async def test_new_and_ended_exactly_once(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    new = async_capture_events(hass, EVENT_WARNING_NEW)
    ended = async_capture_events(hass, EVENT_WARNING_ENDED)
    await _setup(hass, entry)
    assert [(e.data["source"], e.data["provider"]) for e in new] == [("DWD", "DWD"), ("NINA", "MOWAS"), ("DWD", "DWD")]
    mowas = new[1].data
    assert mowas["id"] == MOWAS_ID and mowas["severity"] == "Minor"
    assert mowas["area"] == "Nord und Süd - Gde. Musterstadt", "Gebiet aus der Meldung selbst"
    assert mowas["entry_id"] == entry.entry_id
    assert new[0].data["area"] == "Gemeinde Musterstadt" and new[0].data["headline"] == "Amtliche WARNUNG vor STURMBÖEN"
    assert ended == []

    await _tick(hass, freezer)
    assert len(new) == 3 and ended == [], "unveränderte Lage: keine weiteren Ereignisse"

    _alerts(brightsky, [ALERT])
    await _tick(hass, freezer)
    assert ended == [], "erst nach 10 Min. ohne die Warnung ist sie zu Ende"
    await _tick(hass, freezer, 10)
    assert len(new) == 3
    assert len(ended) == 1 and ended[0].data["id"] == MINOR["alert_id"]
    assert ended[0].data["headline"] == "Amtliche WARNUNG vor WINDBÖEN", "Daten wie beim Erscheinen"

    await _tick(hass, freezer)
    assert len(new) == 3 and len(ended) == 1


async def test_outage_is_no_all_clear(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Fällt eine Quelle aus, bleiben ihre Warnungen stehen und es gibt kein Ende-Ereignis."""
    ended = async_capture_events(hass, EVENT_WARNING_ENDED)
    await _setup(hass, entry)
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/alerts", exc=TimeoutError)
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", exc=TimeoutError)
    await _tick(hass, freezer)
    assert ended == []
    # abgelaufen (20:00 Ortszeit) ist abgelaufen, auch ohne Quelle
    freezer.move_to("2026-10-15 18:00:30+00:00")
    await _tick(hass, freezer, 1)
    assert sorted(e.data["id"] for e in ended) == sorted([ALERT["alert_id"], MINOR["alert_id"]])


async def test_events_after_entities(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Beim Ereignis zeigen die Entitäten schon den neuen Stand (Automationen lesen sie gleich mit)."""
    seen: list[str] = []

    @callback
    def listener(_event: Event) -> None:
        seen.append(hass.states.get("sensor.wetter_warnungen").state)

    _alerts(brightsky, [ALERT])
    for path, name in (("/current_weather", "current.json"), ("/weather", "weather.json")):
        brightsky.get(f"{API_URL}{path}", json=load(name))
    await _setup(hass, entry)
    assert hass.states.get("sensor.wetter_warnungen").state == "2"
    hass.bus.async_listen(EVENT_WARNING_NEW, listener)
    _alerts(brightsky, [ALERT, MINOR])
    await _tick(hass, freezer)
    assert seen == ["3"]


async def test_no_repeat_after_restart(
    hass: HomeAssistant,
    brightsky: AiohttpClientMocker,
    entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: Any,
) -> None:
    """Gemeldete Warnungen stehen sofort im Speicher; nach einem Neustart kommen sie nicht noch einmal."""
    new = async_capture_events(hass, EVENT_WARNING_NEW)
    ended = async_capture_events(hass, EVENT_WARNING_ENDED)
    await _setup(hass, entry)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    announced = hass_storage[f"{DOMAIN}.{entry.entry_id}.warnings"]["data"]["announced"]
    assert set(announced) == {ALERT["alert_id"], MINOR["alert_id"], MOWAS_ID}, "ohne die üblichen 5 s Verzögerung"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    _alerts(brightsky, [ALERT])
    for path, name in (("/current_weather", "current.json"), ("/weather", "weather.json")):
        brightsky.get(f"{API_URL}{path}", json=load(name))
    await _setup(hass, entry)
    await _tick(hass, freezer)
    await _tick(hass, freezer, 10)
    assert len(new) == 3, "nach dem Neustart nichts doppelt"
    assert [e.data["id"] for e in ended] == [MINOR["alert_id"]], "während des Neustarts beendet → einmal Ende"


async def test_upgrade_without_announced(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Gespeicherter Stand einer älteren Version (ohne `announced`): aktuelle Warnungen einmal als neu melden."""
    key = f"{DOMAIN}.{entry.entry_id}.warnings"
    hass_storage[key] = {
        "version": 1,
        "key": key,
        "data": {"warnings": [], "area": None, "ars": ARS, "stale": [], "nina_hits": {}, "announced": {"x": "kaputt"}},
    }
    new = async_capture_events(hass, EVENT_WARNING_NEW)
    ended = async_capture_events(hass, EVENT_WARNING_ENDED)
    await _setup(hass, entry)
    assert len(new) == 3 and ended == [], "unlesbarer Eintrag wird verworfen"


async def test_flapping_source_is_no_end(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Fehlt eine Warnung nur in einer Antwort, gibt es weder Ende noch ein zweites Neu (Gegenprüfung, Fund 1)."""
    new = async_capture_events(hass, EVENT_WARNING_NEW)
    ended = async_capture_events(hass, EVENT_WARNING_ENDED)
    await _setup(hass, entry)
    _alerts(brightsky, [ALERT])
    await _tick(hass, freezer)
    _alerts(brightsky, [ALERT, MINOR])
    await _tick(hass, freezer)
    assert [e.data["id"] for e in new].count(MINOR["alert_id"]) == 1
    assert ended == []


async def test_upgrade_keeps_shown_warnings_quiet(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Stand einer älteren Version: schon angezeigte Warnungen kommen nicht noch einmal als neu (Fund 4)."""
    key = f"{DOMAIN}.{entry.entry_id}.warnings"
    hass_storage[key] = {
        "version": 1,
        "key": key,
        "data": {
            "warnings": [warnings.from_dwd(ALERT)],
            "area": "Gemeinde Musterstadt",
            "ars": ARS,
            "stale": [],
            "nina_hits": {},
        },
    }
    new = async_capture_events(hass, EVENT_WARNING_NEW)
    await _setup(hass, entry)
    assert sorted(e.data["id"] for e in new) == sorted([MINOR["alert_id"], MOWAS_ID])


async def test_grace_stops_while_source_is_down(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Fehlt eine Warnung einmal und fällt die Quelle dann aus: kein Ende, solange sie weg ist (Runde 2, A1)."""
    ended = async_capture_events(hass, EVENT_WARNING_ENDED)
    await _setup(hass, entry)
    _alerts(brightsky, [ALERT])
    await _tick(hass, freezer)
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/alerts", exc=TimeoutError)
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
    for _ in range(5):
        await _tick(hass, freezer)
    assert entry.runtime_data.warnings.stale == ["DWD"]
    assert ended == []
    _alerts(brightsky, [ALERT])
    await _tick(hass, freezer)
    assert ended == [], "Quelle wieder da: die Frist beginnt neu"
    await _tick(hass, freezer, 10)
    assert [e.data["id"] for e in ended] == [MINOR["alert_id"]]


async def test_upgrade_ignores_long_expired(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Gespeicherte, längst abgelaufene Warnung einer älteren Version: kein Ende-Ereignis (Runde 3, W1)."""
    old = {**warnings.from_dwd(ALERT), "id": "old.expired", "expires": "2026-10-10T20:00:00+02:00"}
    key = f"{DOMAIN}.{entry.entry_id}.warnings"
    hass_storage[key] = {
        "version": 1,
        "key": key,
        "data": {"warnings": [old, warnings.from_dwd(ALERT)], "area": "Gemeinde Musterstadt", "ars": ARS},
    }
    ended = async_capture_events(hass, EVENT_WARNING_ENDED)
    await _setup(hass, entry)
    assert ended == []


async def test_outage_during_grace_is_reported(
    hass: HomeAssistant,
    brightsky: AiohttpClientMocker,
    entry: MockConfigEntry,
    freezer: Any,
    hass_storage: dict[str, Any],
) -> None:
    """Beide Quellen weg, eine Warnung in der Frist: Ausfall wird gemeldet, Speicher nicht dauernd neu (W2)."""
    await _setup(hass, entry)
    _alerts(brightsky, [ALERT])
    await _tick(hass, freezer)
    brightsky.clear_requests()
    brightsky.get(f"{API_URL}/alerts", exc=TimeoutError)
    brightsky.get(f"{NINA_URL}/dashboard/{ARS}.json", exc=TimeoutError)
    await _tick(hass, freezer)
    with patch("custom_components.wetter.coordinator.Store.async_delay_save") as save:
        for _ in range(3):
            await _tick(hass, freezer)
    assert save.call_count == 0
    assert not entry.runtime_data.warnings.last_update_success
