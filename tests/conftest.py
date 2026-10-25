"""Fixtures: eigene Integrationen erlauben, Fake-Bright-Sky mit echten Antworten vom 15.10.2026."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wetter.const import API_URL, DOMAIN, NINA_URL

FIXTURES = Path(__file__).parent / "fixtures"
# Zeitpunkt passend zu den Testdaten (Messung 09:30 UTC)
NOW = "2026-10-15 09:40:00+00:00"
LAT, LON = 52.52, 13.41
# fiktiver Kreis (aus der DWD-Warnzelle der Gemeinde Musterstadt, formatgleich) und eine echte MoWaS-Meldung
ARS = "123450000000"
WARN_CELL = 812345678
MOWAS_ID = "mow.DE-XX-MUS-W001-20260904-000"


# Warngebiet weit weg vom Zuhause (anderer Zipfel des Kreises)
FAR_AWAY = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[7.0, 53.5], [7.5, 53.5], [7.5, 53.9], [7.0, 53.9], [7.0, 53.5]]],
            },
            "properties": {},
        }
    ],
}


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


ALERT = {
    "alert_id": "2.49.0.0.276.0.DWD.PVW.1",
    "event_de": "STURMBÖEN",
    "headline_de": "Amtliche WARNUNG vor STURMBÖEN",
    "description_de": "Es treten Sturmböen mit Geschwindigkeiten um 70 km/h auf.",
    "instruction_de": None,
    "severity": "moderate",
    "onset": "2026-10-15T12:00:00+02:00",
    "expires": "2026-10-15T20:00:00+02:00",
}
MINOR = {
    **ALERT,
    "alert_id": "2.49.0.0.276.0.DWD.PVW.2",
    "event_de": "WINDBÖEN",
    "headline_de": "Amtliche WARNUNG vor WINDBÖEN",
    "severity": "minor",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Eigene Integrationen laden."""


@pytest.fixture(autouse=True)
async def berlin(hass: HomeAssistant, freezer: Any) -> None:
    """Fiktives Zuhause in Deutschland, Uhrzeit passend zu den Testdaten."""
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.latitude, hass.config.longitude = LAT, LON
    hass.config.language = "de"


@pytest.fixture
def brightsky(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Bright Sky mit Messung, Vorhersage und zwei Warnungen."""
    aioclient_mock.get(f"{API_URL}/current_weather", json=load("current.json"))
    aioclient_mock.get(f"{API_URL}/weather", json=load("weather.json"))
    aioclient_mock.get(
        f"{API_URL}/alerts",
        json={"alerts": [MINOR, ALERT], "location": {"name": "Gemeinde Musterstadt", "warn_cell_id": WARN_CELL}},
    )
    aioclient_mock.get(f"{NINA_URL}/dashboard/{ARS}.json", json=load("nina_dashboard.json"))
    aioclient_mock.get(f"{NINA_URL}/warnings/{MOWAS_ID}.json", json=load("nina_warning.json"))
    aioclient_mock.get(f"{NINA_URL}/warnings/{MOWAS_ID}.geojson", json=load("nina_warning.geojson"))
    return aioclient_mock


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        unique_id=f"{LAT:.3f},{LON:.3f}",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
    )
    e.add_to_hass(hass)
    return e
