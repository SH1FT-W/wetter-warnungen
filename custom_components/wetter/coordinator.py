"""Wetter alle 10 Min., Warnungen alle 2 Min.; beides gespeichert für einen Start ohne Warten."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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

from . import health, model, netatmo, warnings
from .api import BrightSkyClient, DwdHealthClient, NinaClient, WetterError
from .const import (
    DOMAIN,
    EVENT_WARNING_ENDED,
    EVENT_WARNING_NEW,
    FORECAST_DAYS,
    FORECAST_HOURS,
    HEALTH_INTERVAL,
    HEALTH_LATE,
    HEALTH_MAX_AGE,
    HEALTH_RETRIES,
    LOCAL_KEYS,
    LOCAL_MAX_AGE,
    NINA_AREA_WAIT,
    UPDATE_INTERVAL,
    WARNING_END_GRACE,
    WARNINGS_INTERVAL,
)

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
    # None when pollen and UV are switched off in the options
    health: HealthCoordinator | None = None


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
        if sources.keys() & {"temperature", "humidity", "wind_speed"}:
            # Gefühlte Temperatur aus den Werten, die jetzt gelten (auch aus der Nachbarschaft)
            out["apparent_temperature"] = model.apparent_temperature(
                out["temperature"], out["humidity"], out["wind_speed"]
            )
        if "precipitation" in sources:
            # Regen vor Ort ändert auch das Symbol (die Station kann trocken melden, während es hier regnet)
            out["condition"] = self._condition_with_local_rain(out)
        # Minutes until rain: measured rain of the last hour (nearby, else the station) means it is raining already;
        # the forecast value that fills a missing station reading does not count as a measurement
        measured = (
            out["precipitation"]
            if "precipitation" in sources
            else model.measured_rain((self.data or {}).get("current"))
        )
        out["rain_in"] = model.rain_in(self.hourly, when, (measured or 0) > 0, FORECAST_HOURS)
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
        self._latitude, self._longitude = float(entry.data[CONF_LATITUDE]), float(entry.data[CONF_LONGITUDE])
        # Volltexte und Warngebiete der NINA-Meldungen je Kennung+Version (ändern sich nur mit neuer Version)
        self._details: dict[str, dict[str, Any]] = {}
        self._geometry: dict[str, dict[str, Any]] = {}
        # Last decision per NINA message id (does its area hit the home?), kept across versions and restarts,
        # so a failed GeoJSON download never flips a message on for one run (push + all-clear).
        self._hits: dict[str, bool] = {}
        # First time a new message was seen without a loadable area
        self._unknown_since: dict[str, datetime] = {}
        # Warnings already announced by event (id → event data), kept across restarts: each warning exactly once
        self._announced: dict[str, dict[str, Any]] = {}
        # Announced warnings missing from the answers since (id → ISO time); they end after WARNING_END_GRACE
        self._missing: dict[str, str] = {}
        # Events of the last update, fired after the entities show the new state
        self._pending: list[tuple[str, dict[str, Any]]] = []

    async def async_restore(self) -> bool:
        stored = await self._store.async_load()
        if not isinstance(stored, dict) or not isinstance(stored.get("warnings"), list):
            return False
        hits = stored.get("nina_hits")
        if isinstance(hits, dict):
            self._hits = {str(k): bool(v) for k, v in hits.items()}
        announced = stored.get("announced")
        if isinstance(announced, dict):
            self._announced = {str(k): dict(v) for k, v in announced.items() if isinstance(v, dict)}
        else:
            # state of an older version: the warnings shown so far count as announced (no second push after update)
            shown = warnings.drop_expired([w for w in stored["warnings"] if isinstance(w, dict)], dt_util.utcnow())
            self._announced = {
                str(w.get("id")): warnings.event_data(w, stored.get("area"), self.config_entry.entry_id) for w in shown
            }
        missing = stored.get("missing")
        if isinstance(missing, dict):
            self._missing = {str(k): str(v) for k, v in missing.items()}
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
            area = location.get("name") or area
            dwd = [warnings.from_dwd(a, area) for a in alerts]
            ars = warnings.ars_from_warn_cell(location.get("warn_cell_id")) or ars
        others = await self._nina(ars) if ars else []
        stale = [name for name, got in (("DWD", dwd), ("NINA", others)) if got is None]
        merged = (dwd if dwd is not None else self._previous(dwd=True)) + (
            others if others is not None else self._previous(dwd=False)
        )
        found = warnings.drop_expired(warnings.sort(merged), dt_util.utcnow())
        announced = self._announce(found, area, stale)
        data = {
            "warnings": found,
            "area": area,
            "ars": ars,
            "stale": stale,
            "nina_hits": dict(self._hits),
            "announced": announced,
            "missing": dict(self._missing),
        }
        # `missing` alone (grace clocks, restarted every run while a source is down) is no change worth a write
        changed = _changed(old, data, ignore="missing")
        if dwd is None and others is None and not changed:
            # beide Quellen weiter weg und nichts abgelaufen: Ausfall melden (einmal im Log), letzter Stand bleibt
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="warnings_failed")
        if changed:
            # New or ended warnings are written at once, so a restart right after the event never repeats it
            self._store.async_delay_save(lambda: data, 0 if self._pending else 5)
        return data

    def _announce(self, found: list[dict[str, Any]], area: Any, stale: list[str]) -> dict[str, dict[str, Any]]:
        """Queue one event per warning that appeared or ended since the last update; returns the new state."""
        entry_id = self.config_entry.entry_id
        now = dt_util.utcnow()
        current = {str(w.get("id")): warnings.event_data(w, area, entry_id) for w in found}
        self._pending = [(EVENT_WARNING_NEW, data) for wid, data in current.items() if wid not in self._announced]
        missing: dict[str, str] = {}
        for wid, data in self._announced.items():
            if wid in current:
                continue
            expires = model.parse_time(data.get("expires"))
            # while its source is down the grace time starts anew: no all-clear without an answer
            since = now if data.get("source") in stale else model.parse_time(self._missing.get(wid)) or now
            if (expires is not None and expires <= now) or now - since >= WARNING_END_GRACE:
                self._pending.append((EVENT_WARNING_ENDED, data))
            else:
                # gone for now: keep it announced (no second "new" if it returns), end it after the grace time
                missing[wid] = since.isoformat()
                current[wid] = data
        self._missing = missing
        self._announced = current
        return dict(current)

    @callback
    def async_update_listeners(self) -> None:
        """Entities first, then the events: an automation triggered by the event already sees the new state."""
        super().async_update_listeners()
        pending, self._pending = self._pending, []
        for event_type, data in pending:
            self.hass.bus.async_fire(event_type, data)

    async def _nina(self, ars: str) -> list[dict[str, Any]] | None:
        """Nicht-DWD-Meldungen des Kreises mit Volltext, deren Warngebiet das Zuhause trifft; None bei NINA-Ausfall."""
        try:
            items = warnings.nina_items(await self.nina.dashboard(ars))
        except WetterError as err:
            _LOGGER.debug("NINA nicht erreichbar, behalte die letzten Meldungen: %s", err)
            return None
        await self._fetch(items, self._details, self.nina.warning, "Volltext")
        await self._fetch(items, self._geometry, self.nina.geometry, "Warngebiet")
        keys = {warnings.item_key(i) for i in items}
        ids = {str(i.get("id")) for i in items}
        self._details = {k: v for k, v in self._details.items() if k in keys}
        self._geometry = {k: v for k, v in self._geometry.items() if k in keys}
        self._hits = {k: v for k, v in self._hits.items() if k in ids}
        self._unknown_since = {k: v for k, v in self._unknown_since.items() if k in ids}
        now = dt_util.utcnow()
        out = []
        for item in items:
            if not self._affected(item, now):
                _LOGGER.debug("NINA-Meldung %s trifft das Zuhause nicht (oder Warngebiet noch offen)", item.get("id"))
                continue
            out.append(warnings.from_nina(item, self._details.get(warnings.item_key(item))))
        return out

    def _affected(self, item: dict[str, Any], now: datetime) -> bool:
        """Does the message's area hit the home? Unknown area (download failed) never flips an earlier decision."""
        key, wid = warnings.item_key(item), str(item.get("id"))
        if key in self._geometry:
            # Area loaded: it decides. A loaded area without usable polygons counts for the whole district.
            self._hits[wid] = warnings.covers(self._geometry[key], self._latitude, self._longitude) is not False
            return self._hits[wid]
        if wid in self._hits:
            # Area of this version (or after a restart) not loadable: keep the decision for this message
            return self._hits[wid]
        # Brand-new message without area: wait a few retries (usually the next one works), then warn for the
        # whole district rather than stay silent
        if now - self._unknown_since.setdefault(wid, now) < NINA_AREA_WAIT:
            return False
        self._hits[wid] = True
        return True

    async def _fetch(
        self,
        items: list[dict[str, Any]],
        cache: dict[str, dict[str, Any]],
        getter: Callable[[str], Awaitable[dict[str, Any]]],
        what: str,
    ) -> None:
        """Fehlende Zusatzdaten (Volltext/Warngebiet) für neue oder geänderte Meldungen nachladen."""
        missing = [i for i in items if warnings.item_key(i) not in cache]
        results = await asyncio.gather(*(getter(str(i["id"])) for i in missing), return_exceptions=True)
        for item, result in zip(missing, results, strict=True):
            if isinstance(result, dict):
                cache[warnings.item_key(item)] = result
            else:
                # Meldung ohne diese Zusatzdaten anzeigen, beim nächsten Durchlauf erneut versuchen
                _LOGGER.debug("NINA-%s für %s fehlt: %s", what, item.get("id"), result)


class HealthCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Pollen and UV index of the DWD, published once a day.

    The hourly timer downloads only when new files are due (`next_update` passed, or older than `HEALTH_MAX_AGE`);
    otherwise it just refreshes the entities (a new day shifts today/tomorrow). A failed source keeps its last
    values; this coordinator never touches weather or warnings.
    """

    config_entry: WetterConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WetterConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} Pollen und UV", update_interval=HEALTH_INTERVAL
        )
        self.client = DwdHealthClient(async_get_clientsession(hass))
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.health")
        self._latitude, self._longitude = float(entry.data[CONF_LATITUDE]), float(entry.data[CONF_LONGITUDE])
        # Location the stored pollen region and UV place belong to (a new location looks both up again)
        self._where = f"{self._latitude:.4f},{self._longitude:.4f}"

    async def async_restore(self) -> bool:
        stored = await self._store.async_load()
        if not isinstance(stored, dict) or stored.get("where") != self._where:
            return False
        self.async_set_updated_data(stored)
        return True

    def _due(self, data: dict[str, Any], now: datetime) -> bool:
        """Download now? First time, after a failed download, at the latest after 12 h, and from `next_update` of
        a known part on hourly for HEALTH_LATE (then a missing new file is only retried with the 12 h).

        A location without pollen region or UV place is not retried hourly.
        """
        fetched = model.parse_time(data.get("fetched"))
        if fetched is None or 0 < int(data.get("failures") or 0) < HEALTH_RETRIES or now - fetched >= HEALTH_MAX_AGE:
            return True
        for part in (data.get("pollen"), data.get("uv")):
            next_update = health.parse_dwd_time((part or {}).get("next_update"))
            if next_update is not None and now >= next_update and fetched < next_update + HEALTH_LATE:
                return True
        return False

    async def _async_update_data(self) -> dict[str, Any]:
        old = self.data if (self.data or {}).get("where") == self._where else {}
        now = dt_util.utcnow()
        due = self._due(old, now)
        # Pollen region: looked up with every download until known; after an error of the service hourly on its
        # own (small request), without downloading the files again. An empty answer waits for the next download.
        cell, region_error = old.get("cell"), bool(old.get("region_error"))
        found = False
        if cell is None and (due or region_error):
            try:
                cell = health.region_cell(await self.client.pollen_region(self._latitude, self._longitude))
            except WetterError as err:
                _LOGGER.debug("Pollenregion nicht ermittelt, nächster Versuch später: %s", err)
                region_error = True
            else:
                region_error, found = False, cell is not None
        if not due and not found:
            return old if region_error == bool(old.get("region_error")) else {**old, "region_error": region_error}
        # A failed download or a file without our region/place keeps the last values (they age out by date)
        pollen, uv = old.get("pollen"), old.get("uv")
        failed = 0
        try:
            raw = await self.client.pollen()
        except WetterError as err:
            _LOGGER.debug("Pollenflug nicht erreichbar, behalte den letzten Stand: %s", err)
            failed += 1
        else:
            pollen = (health.pollen_for(raw, cell) if cell is not None else None) or pollen
        try:
            raw = await self.client.uv()
        except WetterError as err:
            _LOGGER.debug("UV-Index nicht erreichbar, behalte den letzten Stand: %s", err)
            failed += 1
        else:
            uv = health.uv_for(raw, self._latitude, self._longitude) or uv
        if failed == 2 and not pollen and not uv:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="health_failed")
        data = {
            "where": self._where,
            "cell": cell,
            "pollen": pollen,
            "uv": uv,
            "region_error": region_error,
            # consecutive failed downloads: retried hourly a few times, then only with HEALTH_MAX_AGE
            "failures": int(old.get("failures") or 0) + 1 if failed else 0,
            "fetched": now.isoformat(timespec="seconds"),
        }
        # downloads are rare: always keep the time, so a restart does not download again at once
        self._store.async_delay_save(lambda: data, 5)
        return data

    def pollen(self, key: str) -> list[Any]:
        """Pollen level `key` for today, tomorrow, day after tomorrow (German calendar days, like the DWD files)."""
        pollen = (self.data or {}).get("pollen") or {}
        values = (pollen.get("values") or {}).get(key) or []
        return health.by_day(values, pollen.get("issued"), dt_util.now(health.DWD_TZ).date())

    def uv(self) -> list[Any]:
        """UV index for today, tomorrow, day after tomorrow (German calendar days, like the DWD files)."""
        uv = (self.data or {}).get("uv") or {}
        return health.by_day(uv.get("values") or [], uv.get("issued"), dt_util.now(health.DWD_TZ).date())
