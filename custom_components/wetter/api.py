"""Bright-Sky-Zugriff – ohne Home Assistant, damit er sich einzeln testen lässt.

Bright Sky: aktuelle Messung der nächsten DWD-Station, stündliche Vorhersage (MOSMIX) und DWD-Warnungen für die
Gemeinde. NINA (warnung.bund.de): alle übrigen amtlichen Warnungen für den Kreis. Einheiten wie beim DWD.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp

from .const import API_URL, NINA_URL

TIMEOUT = aiohttp.ClientTimeout(total=20)


class WetterError(Exception):
    """Bright Sky nicht erreichbar oder unerwartete Antwort."""


class BrightSkyClient:
    """Kleiner Client für api.brightsky.dev (ohne Schlüssel)."""

    def __init__(self, session: aiohttp.ClientSession, latitude: float, longitude: float) -> None:
        self._session = session
        self._where = {"lat": f"{latitude:.4f}", "lon": f"{longitude:.4f}"}

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{API_URL}{path}", params={**self._where, **(params or {})}, timeout=TIMEOUT
            ) as r:
                if r.status >= 400:
                    raise WetterError(f"Bright Sky {r.status} für {path}")
                data = await r.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise WetterError(f"Bright Sky {path}: {err}") from err
        if not isinstance(data, dict):
            raise WetterError(f"Bright Sky {path}: unerwartete Antwort")
        return data

    async def current(self) -> dict[str, Any]:
        """Letzte Messung der nächsten Station (10/30/60-Minuten-Werte)."""
        data = await self._get("/current_weather")
        weather = data.get("weather")
        if not isinstance(weather, dict):
            raise WetterError("Bright Sky: keine aktuelle Messung")
        return weather

    async def hourly(self, first: date, last: date) -> list[dict[str, Any]]:
        """Stündliche Werte von `first` bis `last` (Vergangenheit gemessen, Zukunft vorhergesagt)."""
        data = await self._get("/weather", {"date": first.isoformat(), "last_date": last.isoformat()})
        hours = data.get("weather")
        if not isinstance(hours, list) or not hours:
            raise WetterError("Bright Sky: keine Vorhersage für diesen Ort")
        return [h for h in hours if isinstance(h, dict)]

    async def alerts(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Aktuelle DWD-Warnungen für die Gemeinde und deren Warnzelle (Name, warn_cell_id)."""
        data = await self._get("/alerts")
        alerts = data.get("alerts")
        location = data.get("location")
        return (
            [a for a in alerts if isinstance(a, dict)] if isinstance(alerts, list) else [],
            location if isinstance(location, dict) else {},
        )


class NinaClient:
    """Warn-Schnittstelle des Bundes (warnung.bund.de) – dieselben Daten wie die NINA-App, ohne Schlüssel."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(f"{NINA_URL}{path}", timeout=TIMEOUT) as r:
                if r.status >= 400:
                    raise WetterError(f"NINA {r.status} für {path}")
                return await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise WetterError(f"NINA {path}: {err}") from err

    async def dashboard(self, ars: str) -> list[dict[str, Any]]:
        """Aktuelle Meldungen für einen Kreis (12-stelliger Regionalschlüssel, Gemeindeteil 0000000)."""
        data = await self._get(f"/dashboard/{ars}.json")
        if not isinstance(data, list):
            raise WetterError("NINA: unerwartete Antwort")
        return [d for d in data if isinstance(d, dict)]

    async def warning(self, warning_id: str) -> dict[str, Any]:
        """Volltext einer Meldung (CAP: info[0] mit Beschreibung und Handlungsempfehlung)."""
        data = await self._get(f"/warnings/{warning_id}.json")
        if not isinstance(data, dict):
            raise WetterError(f"NINA: Meldung {warning_id} unlesbar")
        return data

    async def geometry(self, warning_id: str) -> dict[str, Any]:
        """Warngebiet einer Meldung als GeoJSON (FeatureCollection mit den echten Flächen)."""
        data = await self._get(f"/warnings/{warning_id}.geojson")
        if not isinstance(data, dict):
            raise WetterError(f"NINA: Warngebiet {warning_id} unlesbar")
        return data
