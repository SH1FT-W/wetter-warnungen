"""Wetter-Entität: aktuelle Werte + stündliche (48 h) und tägliche (8 Tage) Vorhersage."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfLength, UnitOfPrecipitationDepth, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import model
from .const import FORECAST_DAYS, FORECAST_HOURS
from .coordinator import WetterConfigEntry, WetterCoordinator
from .entity import WetterEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: WetterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([WetterWeather(entry.runtime_data.weather)])


class WetterWeather(WetterEntity, WeatherEntity):
    _attr_name = None
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_visibility_unit = UnitOfLength.KILOMETERS
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY

    def __init__(self, coordinator: WetterCoordinator) -> None:
        super().__init__(coordinator, "weather")
        self._now: dict[str, Any] = coordinator.values()
        # Stand, für den die Vorhersage zuletzt an Abonnenten ging
        self._forecast_for: dict[str, Any] | None = coordinator.data

    @callback
    def _handle_coordinator_update(self) -> None:
        self._now = self.coordinator.values()
        super()._handle_coordinator_update()
        # Die Vorhersage ändert sich nur mit neuen Daten, nicht bei jedem neuen Nachbarschaftswert
        if self.coordinator.data is not self._forecast_for:
            self._forecast_for = self.coordinator.data
            self.hass.async_create_task(self.async_update_listeners(None))

    @property
    def condition(self) -> str | None:
        return cast(str | None, self._now.get("condition"))

    @property
    def native_temperature(self) -> float | None:
        return cast(float | None, self._now.get("temperature"))

    @property
    def native_dew_point(self) -> float | None:
        return cast(float | None, self._now.get("dew_point"))

    @property
    def humidity(self) -> float | None:
        return cast(float | None, self._now.get("humidity"))

    @property
    def native_pressure(self) -> float | None:
        return cast(float | None, self._now.get("pressure"))

    @property
    def native_wind_speed(self) -> float | None:
        return cast(float | None, self._now.get("wind_speed"))

    @property
    def native_wind_gust_speed(self) -> float | None:
        return cast(float | None, self._now.get("wind_gust"))

    @property
    def wind_bearing(self) -> float | None:
        return cast(float | None, self._now.get("wind_bearing"))

    @property
    def cloud_coverage(self) -> float | None:
        return cast(float | None, self._now.get("cloud_cover"))

    @property
    def native_visibility(self) -> float | None:
        return cast(float | None, self._now.get("visibility"))

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        return cast(list[Forecast], model.hourly_forecast(self.coordinator.hourly, dt_util.utcnow(), FORECAST_HOURS))

    async def async_forecast_daily(self) -> list[Forecast] | None:
        return cast(
            list[Forecast],
            model.daily_forecast(
                self.coordinator.hourly, dt_util.utcnow(), dt_util.get_default_time_zone(), FORECAST_DAYS
            ),
        )
