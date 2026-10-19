"""Einrichtung: Name + Ort; Optionen: Werte aus der Nachbarschaft (z. B. Netatmo-Wetterbereich)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_LATITUDE, CONF_LOCATION, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    LocationSelectorConfig,
    TextSelector,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import netatmo
from .api import BrightSkyClient, WetterError
from .const import (
    CONF_LOCAL_HUMIDITY,
    CONF_LOCAL_PRECIPITATION,
    CONF_LOCAL_PRESSURE,
    CONF_LOCAL_TEMPERATURE,
    CONF_LOCAL_WIND_GUST,
    CONF_LOCAL_WIND_SPEED,
    DOMAIN,
)

LOCAL_CLASSES: dict[str, str | list[str]] = {
    CONF_LOCAL_TEMPERATURE: SensorDeviceClass.TEMPERATURE,
    CONF_LOCAL_HUMIDITY: SensorDeviceClass.HUMIDITY,
    CONF_LOCAL_PRECIPITATION: [SensorDeviceClass.PRECIPITATION.value, SensorDeviceClass.PRECIPITATION_INTENSITY.value],
    CONF_LOCAL_PRESSURE: [SensorDeviceClass.ATMOSPHERIC_PRESSURE.value, SensorDeviceClass.PRESSURE.value],
    CONF_LOCAL_WIND_SPEED: SensorDeviceClass.WIND_SPEED,
    CONF_LOCAL_WIND_GUST: SensorDeviceClass.WIND_SPEED,
}


def _coords(user_input: dict[str, Any]) -> tuple[float, float]:
    return float(user_input[CONF_LOCATION][CONF_LATITUDE]), float(user_input[CONF_LOCATION][CONF_LONGITUDE])


class WetterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ort wählen, kurz bei Bright Sky prüfen."""

    VERSION = 1

    async def _check(self, lat: float, lon: float) -> dict[str, str]:
        """Kurz bei Bright Sky nachfragen, ob es für den Ort eine Vorhersage gibt."""
        try:
            today = dt_util.now().date()
            await BrightSkyClient(async_get_clientsession(self.hass), lat, lon).hourly(today, today)
        except WetterError:
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            lat, lon = _coords(user_input)
            await self.async_set_unique_id(f"{lat:.3f},{lon:.3f}")
            self._abort_if_unique_id_configured()
            if not (errors := await self._check(lat, lon)):
                # Netatmo-Wetterbereich in der Nähe? Dann gleich als Nachbarschaftswerte eintragen.
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_LATITUDE: lat, CONF_LONGITUDE: lon},
                    options=netatmo.suggest(self.hass, lat, lon),
                )
        home = {CONF_LATITUDE: self.hass.config.latitude, CONF_LONGITUDE: self.hass.config.longitude}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Wetter"): TextSelector(),
                vol.Required(CONF_LOCATION, default=home): LocationSelector(LocationSelectorConfig(radius=False)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ort ändern (z. B. nach einem Umzug) – Entitäten, Optionen und Verlauf bleiben."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            lat, lon = _coords(user_input)
            await self.async_set_unique_id(f"{lat:.3f},{lon:.3f}")
            self._abort_if_location_taken(entry)
            if not (errors := await self._check(lat, lon)):
                return self.async_update_reload_and_abort(
                    entry, unique_id=self.unique_id, data={CONF_LATITUDE: lat, CONF_LONGITUDE: lon}
                )
        current = {CONF_LATITUDE: entry.data[CONF_LATITUDE], CONF_LONGITUDE: entry.data[CONF_LONGITUDE]}
        schema = vol.Schema(
            {vol.Required(CONF_LOCATION, default=current): LocationSelector(LocationSelectorConfig(radius=False))}
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    def _abort_if_location_taken(self, entry: ConfigEntry) -> None:
        """Ein anderer schon eingerichteter Ort darf nicht doppelt entstehen; derselbe Eintrag ist ok."""
        for other in self._async_current_entries(include_ignore=False):
            if other.entry_id != entry.entry_id and other.unique_id == self.unique_id:
                raise AbortFlow("already_configured")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WetterOptionsFlow:
        return WetterOptionsFlow()


class WetterOptionsFlow(OptionsFlowWithReload):
    """Nachbarschaftswerte: gehen vor den DWD-Messwerten, solange sie frisch sind (höchstens 1 Std. alt)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={k: v for k, v in user_input.items() if v})
        fields: dict[Any, Any] = {}
        # Leere Felder: Sensoren eines Netatmo-Wetterbereichs in der Nähe vorschlagen
        data = self.config_entry.data
        proposal = (
            {}
            if self.config_entry.options
            else netatmo.suggest(self.hass, float(data[CONF_LATITUDE]), float(data[CONF_LONGITUDE]))
        )
        for key, device_class in LOCAL_CLASSES.items():
            current = self.config_entry.options.get(key) or proposal.get(key)
            marker = vol.Optional(key, description={"suggested_value": current} if current else None)
            fields[marker] = EntitySelector(EntitySelectorConfig(domain="sensor", device_class=device_class))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
