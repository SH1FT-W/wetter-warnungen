"""Wetter alle 10 Min., Warnungen alle 2 Min.; beides gespeichert für einen Start ohne Warten."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import model, netatmo, warnings
from .api import BrightSkyClient, NinaClient, WetterError
from .const import DOMAIN, FORECAST_DAYS, LOCAL_KEYS, LOCAL_MAX_AGE, UPDATE_INTERVAL, WARNINGS_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _changed(old: dict[str, Any] | None, new: dict[str, Any], ignore: str | None = None) -> bool:
    """Unterscheiden sich zwei Stände abseits des Schlüssels `ignore`? (Store nur dann schreiben.)"""
    if old is None:
        return True
    return {k: v for k, v in old.items() if k != ignore} != {k: v for k, v in new.items() if k != ignore}


@dataclass(slots=True)
class WetterData:
    """Laufzeitdaten eines Eintrags."""

    weather: WetterCoordinator
    warnings: WarningsCoordinator


type WetterConfigEntry = ConfigEntry[WetterData]


class WetterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holt Messung und Vorhersage; liefert die zusammengeführten aktuellen Werte."""

    config_entry: WetterConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WetterConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = BrightSkyClient(
            async_get_clientsession(hass), float(entry.data[CONF_LATITUDE]), float(entry.data[CONF_LONGITUDE])
        )
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        # zusammengeführte Werte, einmal je Aktualisierung gerechnet (alle Entitäten lesen denselben Stand)
        self._values: dict[str, Any] | None = None
        # Option → Entität aus der Nachbarschaft (z. B. Netatmo-Wetterbereich)
        self.local: dict[str, str] = {
            key: str(entry.options[option]) for option, key in LOCAL_KEYS.items() if entry.options.get(option)
        }

    async def async_restore(self) -> bool:
        """Gespeicherten Stand laden; True, wenn es einen gibt (dann startet die Integration ohne Warten)."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict) or not isinstance(stored.get("hourly"), list) or not stored["hourly"]:
            return False
        self.async_set_updated_data(stored)
        return True

    @callback
    def async_track_local(self) -> None:
        """Bei jeder Änderung eines Nachbarschaftswerts die Entitäten neu schreiben."""
        if not self.local:
            return

        @callback
        def changed(_event: Event[EventStateChangedData]) -> None:
            self.async_update_listeners()

        self.config_entry.async_on_unload(async_track_state_change_event(self.hass, list(self.local.values()), changed))

    async def _async_update_data(self) -> dict[str, Any]:
        today = dt_util.now().date()
        try:
            current, hourly = await asyncio.gather(
                self.client.current(),
                # ab gestern, damit die Stunde um Mitternacht und der heutige Tag vollständig sind
                self.client.hourly(today - timedelta(days=1), today + timedelta(days=FORECAST_DAYS)),
            )
        except WetterError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="update_failed", translation_placeholders={"error": str(err)}
            ) from err
        data = {"current": current, "hourly": hourly, "fetched": dt_util.utcnow().isoformat(timespec="seconds")}
        if _changed(self.data, data, ignore="fetched"):
            # nur schreiben, wenn sich Messung oder Vorhersage geändert haben, nicht wegen des Zeitstempels allein
            self._store.async_delay_save(lambda: data, 5)
        return data

    @callback
    def async_update_listeners(self) -> None:
        """Vor jeder Benachrichtigung den Wertecache leeren: neue Daten oder geänderter Nachbarschaftswert."""
        self._values = None
        super().async_update_listeners()

    # --- zusammengeführte Werte ------------------------------------------------------------------------

    @property
    def hourly(self) -> list[dict[str, Any]]:
        return list((self.data or {}).get("hourly") or [])

    def today(self) -> dict[str, Any]:
        """Tagesvorhersage für heute (Ortszeit) – wie in weather.get_forecasts."""
        days = model.daily_forecast(self.hourly, dt_util.utcnow(), dt_util.get_default_time_zone(), 1)
        return days[0] if days else {}

    def neighbourhood(self) -> dict[str, Any] | None:
        """Netatmo-Bereich hinter den eingetragenen Nachbarschaftssensoren (für die Diagnose-Sensoren)."""
        data = self.config_entry.data
        return netatmo.area_info(
            self.hass, list(self.local.values()), float(data[CONF_LATITUDE]), float(data[CONF_LONGITUDE])
        )

    def _local_value(self, key: str) -> float | None:
        entity_id = self.local.get(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        if dt_util.utcnow() - state.last_reported > LOCAL_MAX_AGE:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        # „nan“ oder „inf“ vom Sensor nicht als Messwert übernehmen, dann gilt der DWD-Wert
        return value if math.isfinite(value) else None

    def values(self, now: datetime | None = None) -> dict[str, Any]:
        """Aktuelle Werte; Nachbarschaftswerte gehen vor, solange sie frisch sind.

        Ohne `now` kommt der Stand aus dem Cache, der bei jeder Benachrichtigung der Entitäten neu entsteht.
        """
        if now is None and self._values is not None:
            return self._values
        when = now or dt_util.utcnow()
        out = model.now_values((self.data or {}).get("current"), self.hourly, when)
        sources: dict[str, str] = {}
        for key in LOCAL_KEYS.values():
            local = self._local_value(key)
            if local is not None:
                out[key] = round(local, 1)
                sources[key] = self.local[key]
        out["local_sources"] = sources
        if "precipitation" in sources:
            # Regen vor Ort ändert auch das Symbol (die Station kann trocken melden, während es hier regnet)
            out["condition"] = self._condition_with_local_rain(out)
        if now is None:
            self._values = out
        return out

    def _condition_with_local_rain(self, out: dict[str, Any]) -> str | None:
        rain = out.get("precipitation") or 0.0
        cond = out.get("condition")
        if rain > 0 and cond not in ("rainy", "pouring", "snowy", "snowy-rainy", "hail", "lightning-rainy"):
            return model.condition("rain", rain)
        if rain == 0 and cond in ("rainy", "pouring"):
            return "cloudy"
        if rain == 0 and cond == "lightning-rainy":
            # Gewitter ohne Regen vor Ort: Blitze bleiben, der Regen fällt weg
            return "lightning"
        # snowy, snowy-rainy und hail bleiben: ein Regenmesser erfasst Schnee und Hagel nicht zuverlässig,
        # 0 mm sagt dann nichts über das Wetter
        return str(cond) if cond else None


class WarningsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Amtliche Warnungen: DWD für die Gemeinde (Bright Sky) + alle übrigen Anbieter für den Kreis (NINA).

    Fällt eine Quelle aus, bleiben ihre zuletzt bekannten Warnungen stehen, sonst gäbe es eine falsche Entwarnung.
    Abgelaufene Warnungen (`expires`) fallen trotzdem weg; `stale` nennt die ausgefallenen Quellen.
    """

    config_entry: WetterConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WetterConfigEntry, client: BrightSkyClient) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} Warnungen", update_interval=WARNINGS_INTERVAL
        )
        self.client = client
        self.nina = NinaClient(async_get_clientsession(hass))
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.warnings")
        # Volltexte der NINA-Meldungen je Kennung+Version (ändern sich nur mit neuer Version)
        self._details: dict[str, dict[str, Any]] = {}

    async def async_restore(self) -> bool:
        stored = await self._store.async_load()
        if not isinstance(stored, dict) or not isinstance(stored.get("warnings"), list):
            return False
        self.async_set_updated_data(stored)
        return True

    @property
    def warnings(self) -> list[dict[str, Any]]:
        """Gültige Warnungen (abgelaufene fallen auch zwischen zwei Abfragen weg)."""
        return warnings.drop_expired(list((self.data or {}).get("warnings") or []), dt_util.utcnow())

    @property
    def stale(self) -> list[str]:
        """Quellen, deren letzte Abfrage scheiterte und deren alte Warnungen noch stehen."""
        return list((self.data or {}).get("stale") or [])

    def _previous(self, dwd: bool) -> list[dict[str, Any]]:
        return [w for w in self.warnings if (w.get("provider") == "DWD") == dwd]

    async def _async_update_data(self) -> dict[str, Any]:
        old = self.data or {}
        area, ars = old.get("area"), old.get("ars")
        dwd: list[dict[str, Any]] | None = None
        try:
            alerts, location = await self.client.alerts()
        except WetterError as err:
            _LOGGER.debug("DWD-Warnungen nicht erreichbar, behalte die letzten: %s", err)
        else:
            dwd = [warnings.from_dwd(a) for a in alerts]
            area = location.get("name") or area
            ars = warnings.ars_from_warn_cell(location.get("warn_cell_id")) or ars
        others = await self._nina(ars) if ars else []
        stale = [name for name, got in (("DWD", dwd), ("NINA", others)) if got is None]
        merged = (dwd if dwd is not None else self._previous(dwd=True)) + (
            others if others is not None else self._previous(dwd=False)
        )
        data = {
            "warnings": warnings.drop_expired(warnings.sort(merged), dt_util.utcnow()),
            "area": area,
            "ars": ars,
            "stale": stale,
        }
        changed = _changed(old, data)
        if dwd is None and others is None and not changed:
            # beide Quellen weiter weg und nichts abgelaufen: Ausfall melden (einmal im Log), letzter Stand bleibt
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="warnings_failed")
        if changed:
            self._store.async_delay_save(lambda: data, 5)
        return data

    async def _nina(self, ars: str) -> list[dict[str, Any]] | None:
        """Nicht-DWD-Meldungen des Kreises mit Volltext; None, wenn NINA nicht erreichbar ist."""
        try:
            items = warnings.nina_items(await self.nina.dashboard(ars))
        except WetterError as err:
            _LOGGER.debug("NINA nicht erreichbar, behalte die letzten Meldungen: %s", err)
            return None
        missing = [i for i in items if warnings.item_key(i) not in self._details]
        results = await asyncio.gather(*(self.nina.warning(str(i["id"])) for i in missing), return_exceptions=True)
        for item, result in zip(missing, results, strict=True):
            if isinstance(result, dict):
                self._details[warnings.item_key(item)] = result
            else:
                # Meldung ohne Volltext anzeigen, beim nächsten Durchlauf erneut versuchen
                _LOGGER.debug("NINA-Volltext für %s fehlt: %s", item.get("id"), result)
        keys = {warnings.item_key(i) for i in items}
        self._details = {k: v for k, v in self._details.items() if k in keys}
        return [warnings.from_nina(i, self._details.get(warnings.item_key(i))) for i in items]
