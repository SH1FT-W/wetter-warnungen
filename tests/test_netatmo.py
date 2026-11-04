"""Netatmo-Wetterbereich in der Nähe als Nachbarschaftswerte vorschlagen."""

from __future__ import annotations

from copy import deepcopy

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_LATITUDE, CONF_LOCATION, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wetter import netatmo
from custom_components.wetter.const import (
    CONF_LOCAL_HUMIDITY,
    CONF_LOCAL_PRECIPITATION,
    CONF_LOCAL_TEMPERATURE,
    DOMAIN,
)

from .conftest import LAT, LON


def _area(lat: float, lon: float, d: float = 0.03) -> dict[str, float]:
    return {"lat_ne": lat + d, "lat_sw": lat - d, "lon_ne": lon + d, "lon_sw": lon - d}


def _netatmo(hass: HomeAssistant, areas: dict[str, dict[str, float]], disabled: str | None = None) -> None:
    entry = MockConfigEntry(domain="netatmo", options={"weather_areas": areas})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    for name in areas:
        for suffix in ("temperature", "humidity", "rain", "pressure"):
            reg.async_get_or_create(
                "sensor",
                "netatmo",
                f"{name}-{suffix}",
                config_entry=entry,
                suggested_object_id=f"{name.lower()}_{suffix}",
                disabled_by=er.RegistryEntryDisabler.USER if suffix == disabled else None,
            )
    # eine eigene Netatmo-Station (Kennung = Modul) darf nicht gewählt werden
    reg.async_get_or_create("sensor", "netatmo", "70:ee:50:00:00:01-temperature", config_entry=entry)


def test_no_netatmo(hass: HomeAssistant) -> None:
    assert netatmo.suggest(hass, LAT, LON) == {}


def test_nearest_area_wins(hass: HomeAssistant) -> None:
    _netatmo(hass, {"Weit": _area(LAT + 1, LON + 1), "Nah": _area(LAT, LON), "Kaputt": {"lat_ne": "x"}})
    assert netatmo.suggest(hass, LAT, LON) == {
        CONF_LOCAL_TEMPERATURE: "sensor.nah_temperature",
        CONF_LOCAL_HUMIDITY: "sensor.nah_humidity",
        CONF_LOCAL_PRECIPITATION: "sensor.nah_rain",
    }, "Druck nicht vorgeschlagen (Bereiche liefern keinen Meereshöhen-Wert)"


def test_disabled_sensor_left_out(hass: HomeAssistant) -> None:
    _netatmo(hass, {"Nah": _area(LAT, LON)}, disabled="humidity")
    assert CONF_LOCAL_HUMIDITY not in netatmo.suggest(hass, LAT, LON)


def test_odd_options_ignored(hass: HomeAssistant) -> None:
    MockConfigEntry(domain="netatmo", options={"weather_areas": ["kein", "dict"]}).add_to_hass(hass)
    MockConfigEntry(domain="netatmo", options={"weather_areas": {"A": "kaputt"}}).add_to_hass(hass)
    assert netatmo.suggest(hass, LAT, LON) == {}


async def test_setup_fills_options(hass: HomeAssistant, brightsky: AiohttpClientMocker) -> None:
    """Beim Einrichten werden die Bereichssensoren gleich eingetragen."""
    _netatmo(hass, {"Umgebung": _area(LAT, LON)})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Wetter", CONF_LOCATION: {CONF_LATITUDE: LAT, CONF_LONGITUDE: LON}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options[CONF_LOCAL_TEMPERATURE] == "sensor.umgebung_temperature"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wetter_quelle") is not None


async def test_options_suggest_when_empty(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Ohne eigene Optionen schlägt der Dialog den Bereich vor; eigene Einträge bleiben stehen."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    _netatmo(hass, {"Umgebung": _area(LAT, LON)})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema[CONF_LOCAL_TEMPERATURE].description == {"suggested_value": "sensor.umgebung_temperature"}

    hass.config_entries.async_update_entry(entry, options={CONF_LOCAL_TEMPERATURE: "sensor.eigener"})
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema[CONF_LOCAL_TEMPERATURE].description == {"suggested_value": "sensor.eigener"}
    assert schema[CONF_LOCAL_HUMIDITY].description is None, "eigene Optionen: nichts nachschieben"


async def test_neighbourhood_diagnostics(hass: HomeAssistant, brightsky: AiohttpClientMocker) -> None:
    """Bereich hinter den eingetragenen Sensoren: Name, Breite/Höhe, Fläche, Abstand zum Ort."""
    _netatmo(hass, {"Umgebung": _area(LAT + 0.01, LON, d=0.03)})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
        options={CONF_LOCAL_TEMPERATURE: "sensor.umgebung_temperature"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    area = hass.states.get("sensor.wetter_nachbarschaftsbereich")
    assert area.state == "Umgebung"
    assert area.attributes["height_km"] == 6.7 and 4.0 < area.attributes["width_km"] < 4.2, "±0,03° bei 52,6° Breite"
    assert 26.5 < float(hass.states.get("sensor.wetter_nachbarschaft_flache").state) < 27.5
    assert hass.states.get("sensor.wetter_nachbarschaft_abstand").state == "1.1", "Mittelpunkt 0,01° nördlich"
    assert er.async_get(hass).async_get("sensor.wetter_nachbarschaft_flache").entity_category == "diagnostic"


async def test_neighbourhood_without_netatmo(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Kein Netatmo-Bereich (oder ein anderer Sensor eingetragen) → „Keiner“, Fläche/Abstand unbekannt."""
    er.async_get(hass).async_get_or_create("sensor", "ecowitt", "station-temp", suggested_object_id="garten")
    hass.config_entries.async_update_entry(entry, options={CONF_LOCAL_TEMPERATURE: "sensor.garten"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wetter_nachbarschaftsbereich").state == "none"
    assert hass.states.get("sensor.wetter_nachbarschaft_flache").state == "unknown"
    assert hass.states.get("sensor.wetter_nachbarschaft_abstand").state == "unknown"
    assert netatmo.area_info(hass, ["sensor.gibt_es_nicht"], LAT, LON) is None
    _netatmo(hass, {"Kaputt": {"lat_ne": "x"}})
    assert netatmo.area_info(hass, ["sensor.kaputt_temperature"], LAT, LON) is None, "Bereich ohne Koordinaten"


async def test_netatmo_entry_never_written(hass: HomeAssistant, brightsky: AiohttpClientMocker) -> None:
    """The Netatmo entry belongs to Netatmo: no slider, nothing writes its options (hacs/default#13248).

    Up to 26.11.2 a "Neighbourhood radius" slider rewrote the weather area in Netatmo's options.
    """
    area = {**_area(LAT, LON, d=0.03), "mode": "avg", "uuid": "abc"}
    _netatmo(hass, {"Umgebung": area})
    netatmo_entry = hass.config_entries.async_entries("netatmo")[0]
    before = deepcopy((dict(netatmo_entry.data), dict(netatmo_entry.options)))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
        options={CONF_LOCAL_TEMPERATURE: "sensor.umgebung_temperature"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids("number") == []
    assert not hasattr(netatmo, "set_radius")
    # The neighbourhood sensors still show the area (read only)
    assert hass.states.get("sensor.wetter_nachbarschaft_abstand") is not None
    assert (dict(netatmo_entry.data), dict(netatmo_entry.options)) == before


async def test_old_radius_entity_removed(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """The registry entry of the old slider is cleaned up at setup."""
    reg = er.async_get(hass)
    reg.async_get_or_create("number", DOMAIN, f"{entry.entry_id}_neighbourhood_radius", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_neighbourhood_radius") is None
