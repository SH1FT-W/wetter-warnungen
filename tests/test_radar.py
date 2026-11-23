"""Rain radar: 5-minute steps from Bright Sky, next rain, rain within 15/30/60 minutes, outages."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.wetter import model

from .conftest import RADAR_FIRST, radar_answer, set_radar

NOW = datetime.fromisoformat("2026-10-15T09:40:00+00:00")
RADAR = "sensor.wetter_regenradar"
NEXT_RAIN = "sensor.wetter_nachster_regen_radar"
WITHIN = "binary_sensor.wetter_regen_in_{}_minuten"
# dry until 10:00, then 20 minutes of rain (0.6, 2.4, 1.2, 0.12 mm/h), dry again to the end
SHOWER = [0, 0, 0, 0, 0, 5, 20, 10, 1, *[0] * 16]


def _steps(values: list[Any], first: str = RADAR_FIRST) -> list[tuple[datetime, float]]:
    return model.radar_steps(radar_answer(values, first)["radar"])


def _at(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


# --- model ------------------------------------------------------------------------------------------------


def test_steps_in_mm_per_hour() -> None:
    """0.01 mm per 5 minutes → mm/h; unreadable records are skipped, the rest sorted by time."""
    records = radar_answer([10, 0])["radar"]
    broken = [
        {"timestamp": "gestern", "precipitation_5": [[5]]},
        {"timestamp": "2026-10-15T09:50:00+00:00", "precipitation_5": [[None]]},
        {"timestamp": "2026-10-15T09:55:00+00:00", "precipitation_5": []},
        {"timestamp": "2026-10-15T10:00:00+00:00", "precipitation_5": [[-1]]},
        {"timestamp": "2026-10-15T10:05:00+00:00"},
    ]
    assert model.radar_steps([records[1], *broken, records[0]]) == [(_at(-5), 1.2), (NOW, 0.0)]


def test_outlook_dry() -> None:
    outlook = model.radar_outlook(_steps([0] * 25), NOW)
    assert outlook is not None
    assert outlook["intensity"] == 0.0
    assert len(outlook["forecast"]) == 24, "the step that ended at 09:40 is over"
    assert next(iter(outlook["forecast"])) == NOW.isoformat()
    assert [outlook[k] for k in ("start", "end", "length", "max", "sum")] == [None] * 5
    assert model.rain_within(outlook, NOW, 60) is False


def test_outlook_shower() -> None:
    outlook = model.radar_outlook(_steps(SHOWER), NOW)
    assert outlook is not None
    assert outlook["intensity"] == 0.0
    assert outlook["start"] == _at(20) and outlook["end"] == _at(40)
    assert outlook["length"] == 20
    assert outlook["max"] == 2.4
    assert outlook["sum"] == 0.36, "5 + 20 + 10 + 1 hundredths of a millimetre"
    assert model.rain_within(outlook, NOW, 15) is False
    assert model.rain_within(outlook, NOW, 20) is True, "starts exactly in 20 minutes"
    assert model.rain_within(outlook, NOW, 30) is True


def test_outlook_raining_now_to_the_end() -> None:
    """Rain in the running step: start lies in the past; no dry step to the end → end and length unknown."""
    outlook = model.radar_outlook(_steps([3] * 25), _at(2))
    assert outlook is not None
    assert outlook["intensity"] == 0.36
    assert outlook["start"] == NOW, "the running step began at 09:40"
    assert outlook["end"] is None and outlook["length"] is None
    assert outlook["sum"] == 0.72, "24 steps of 0.03 mm"
    assert model.rain_within(outlook, _at(2), 15) is True


def test_outlook_without_current_data() -> None:
    """No steps, or only steps that are over (radar data older than two hours): no statement at all."""
    assert model.radar_outlook([], NOW) is None
    assert model.radar_outlook(_steps([5] * 25), _at(125)) is None
    assert model.rain_within(None, NOW, 60) is None


# --- entities ---------------------------------------------------------------------------------------------


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED


async def _tick(hass: HomeAssistant, freezer: Any, minutes: int = 5) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _within(hass: HomeAssistant) -> list[str]:
    return [hass.states.get(WITHIN.format(minutes)).state for minutes in (15, 30, 60)]


async def test_dry(hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    state = hass.states.get(RADAR)
    assert state.state == "0.0"
    assert state.attributes["unit_of_measurement"] == "mm/h"
    assert len(state.attributes["forecast"]) == 24
    assert state.attributes["next_start"] is None
    assert hass.states.get(NEXT_RAIN).state == "unknown"
    assert _within(hass) == ["off", "off", "off"]


async def test_asks_for_the_cell_of_the_place(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """One grid cell (distance 0), plain numbers, from one step before now (the running step is in the answer)."""
    await _setup(hass, entry)
    queries = [call[1].query for call in brightsky.mock_calls if call[1].path == "/radar"]
    assert queries
    assert dict(queries[0]) == {
        "lat": "52.5200",
        "lon": "13.4100",
        "distance": "0",
        "format": "plain",
        "date": "2026-10-15T09:35+00:00",
    }


async def test_shower_ahead(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    set_radar(brightsky, SHOWER)
    await _setup(hass, entry)
    state = hass.states.get(RADAR)
    assert state.state == "0.0"
    assert state.attributes["forecast"]["2026-10-15T10:05:00+00:00"] == 2.4
    assert state.attributes["next_start"] == "2026-10-15T10:00:00+00:00"
    assert state.attributes["next_end"] == "2026-10-15T10:20:00+00:00"
    assert state.attributes["next_length"] == 20
    nxt = hass.states.get(NEXT_RAIN)
    assert nxt.state == "2026-10-15T10:00:00+00:00"
    assert nxt.attributes["end"] == "2026-10-15T10:20:00+00:00"
    assert (nxt.attributes["length"], nxt.attributes["max"], nxt.attributes["sum"]) == (20, 2.4, 0.36)
    assert _within(hass) == ["off", "on", "on"]

    # 09:50: the next radar picture, the shower is 10 minutes away
    set_radar(brightsky, json=radar_answer(SHOWER[2:], "2026-10-15T09:45:00+00:00"))
    await _tick(hass, freezer, 10)
    assert _within(hass) == ["on", "on", "on"]

    # 10:05: it rains
    set_radar(brightsky, json=radar_answer(SHOWER[5:], "2026-10-15T10:00:00+00:00"))
    await _tick(hass, freezer, 15)
    assert hass.states.get(RADAR).state == "2.4"
    assert hass.states.get(NEXT_RAIN).state == "2026-10-15T10:05:00+00:00"
    assert hass.states.get(NEXT_RAIN).attributes["length"] == 15


async def test_outage_touches_only_the_radar(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, freezer: Any
) -> None:
    """Radar down (or the place outside the radar picture, 404): the start goes on, only its entities are away."""
    set_radar(brightsky, status=404)
    await _setup(hass, entry)
    assert hass.states.get(RADAR).state == "unavailable"
    assert hass.states.get(NEXT_RAIN).state == "unavailable"
    assert _within(hass) == ["unavailable"] * 3
    assert hass.states.get("weather.wetter").state != "unavailable"
    assert hass.states.get("sensor.wetter_warnungen").state == "3"

    set_radar(brightsky, json={"radar": []})
    await _tick(hass, freezer)
    assert hass.states.get(RADAR).state == "unavailable", "an answer without records is no radar data"

    set_radar(brightsky, json=radar_answer([2] * 25, "2026-10-15T09:45:00+00:00"))
    await _tick(hass, freezer)
    assert hass.states.get(RADAR).state == "0.24"
    assert _within(hass) == ["on", "on", "on"]


async def test_diagnostics(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry, hass_client: ClientSessionGenerator
) -> None:
    set_radar(brightsky, SHOWER)
    await _setup(hass, entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["radar"]["fetched"] == "2026-10-15T09:40:00+00:00"
    assert diag["radar"]["steps"][5] == ["2026-10-15T10:00:00+00:00", 0.6]
