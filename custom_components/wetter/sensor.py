"""Sensoren mit den aktuellen Werten (gleiche Namen wie bei der DWD-Integration) und den Warnungen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    UV_INDEX,
    UnitOfArea,
    UnitOfIrradiance,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import health, model
from .const import LOCAL_KEYS
from .coordinator import HealthCoordinator, WarningsCoordinator, WetterConfigEntry, WetterCoordinator
from .entity import HealthEntity, WarningEntity, WetterEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WetterSensorDescription(SensorEntityDescription):
    """Wert aus den zusammengeführten aktuellen Werten."""

    value_key: str | None = None
    value_fn: Callable[[WetterCoordinator, dict[str, Any]], Any] | None = None
    attributes_fn: Callable[[WetterCoordinator, dict[str, Any]], dict[str, Any]] | None = None


def _local_source(key: str) -> Callable[[WetterCoordinator, dict[str, Any]], dict[str, Any]]:
    def attrs(_c: WetterCoordinator, now: dict[str, Any]) -> dict[str, Any]:
        source = now["local_sources"].get(key)
        return {"source": source or "dwd"}

    return attrs


def _measurement(
    key: str, device_class: SensorDeviceClass, unit: str, *, local: bool = False, **kw: Any
) -> WetterSensorDescription:
    return WetterSensorDescription(
        key=key,
        translation_key=key,
        value_key=key,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=SensorStateClass.MEASUREMENT,
        attributes_fn=_local_source(key) if local else None,
        **kw,
    )


def _source(c: WetterCoordinator, now: dict[str, Any]) -> str:
    """Woher die aktuellen Werte kommen: alle eingetragenen aus der Nachbarschaft, ein Teil davon, oder nur DWD."""
    used = now["local_sources"]
    if not used:
        return "dwd"
    return "local" if len(used) == len(c.local) else "mixed"


def _source_attrs(c: WetterCoordinator, now: dict[str, Any]) -> dict[str, Any]:
    return {key: now["local_sources"].get(key, "dwd") for key in LOCAL_KEYS.values()}


def _rain_in_attrs(_c: WetterCoordinator, now: dict[str, Any]) -> dict[str, Any]:
    rain = now.get("rain_in") or {}
    return {k: rain.get(k) for k in ("start", "precipitation", "precipitation_probability", "source")}


SENSORS: tuple[WetterSensorDescription, ...] = (
    _measurement("temperature", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, local=True),
    _measurement("humidity", SensorDeviceClass.HUMIDITY, PERCENTAGE, local=True),
    _measurement(
        "apparent_temperature", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, suggested_display_precision=1
    ),
    _measurement("dew_point", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    _measurement("pressure", SensorDeviceClass.ATMOSPHERIC_PRESSURE, UnitOfPressure.HPA, local=True),
    _measurement("wind_speed", SensorDeviceClass.WIND_SPEED, UnitOfSpeed.KILOMETERS_PER_HOUR, local=True),
    _measurement("wind_gust", SensorDeviceClass.WIND_SPEED, UnitOfSpeed.KILOMETERS_PER_HOUR, local=True),
    _measurement(
        "precipitation",
        SensorDeviceClass.PRECIPITATION_INTENSITY,
        UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR,
        local=True,
    ),
    _measurement("solar", SensorDeviceClass.IRRADIANCE, UnitOfIrradiance.WATTS_PER_SQUARE_METER),
    _measurement("visibility", SensorDeviceClass.DISTANCE, UnitOfLength.KILOMETERS, suggested_display_precision=1),
    WetterSensorDescription(
        key="wind_bearing",
        translation_key="wind_bearing",
        value_key="wind_bearing",
        device_class=SensorDeviceClass.WIND_DIRECTION,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
    ),
    WetterSensorDescription(
        key="precipitation_probability",
        translation_key="precipitation_probability",
        value_key="precipitation_probability",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    WetterSensorDescription(
        key="precipitation_probability_today",
        translation_key="precipitation_probability_today",
        value_fn=lambda c, _now: c.today().get("precipitation_probability"),
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    WetterSensorDescription(
        key="rain_in",
        translation_key="rain_in",
        value_fn=lambda _c, now: (now.get("rain_in") or {}).get("minutes"),
        attributes_fn=_rain_in_attrs,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    WetterSensorDescription(
        key="cloud_cover",
        translation_key="cloud_cover",
        value_key="cloud_cover",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    WetterSensorDescription(
        key="condition",
        translation_key="condition",
        value_key="condition",
        device_class=SensorDeviceClass.ENUM,
        options=model.ALL_CONDITIONS,
    ),
    WetterSensorDescription(
        key="neighbourhood_area",
        translation_key="neighbourhood_area",
        value_fn=lambda c, _now: (c.neighbourhood() or {}).get("name") or "none",
        attributes_fn=lambda c, _now: {
            k: v for k, v in (c.neighbourhood() or {}).items() if k in ("width_km", "height_km")
        },
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    WetterSensorDescription(
        key="neighbourhood_size",
        translation_key="neighbourhood_size",
        value_fn=lambda c, _now: (c.neighbourhood() or {}).get("area_km2"),
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_KILOMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    WetterSensorDescription(
        key="neighbourhood_distance",
        translation_key="neighbourhood_distance",
        value_fn=lambda c, _now: (c.neighbourhood() or {}).get("distance_km"),
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    WetterSensorDescription(
        key="source",
        translation_key="source",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_source,
        attributes_fn=_source_attrs,
        device_class=SensorDeviceClass.ENUM,
        options=["local", "mixed", "dwd"],
    ),
)


# Pollen and UV: keys of the entities (unique_id = <entry_id>_<key>)
HEALTH_KEYS = (*(f"pollen_{key}" for key in health.POLLEN.values()), "uv_index", "uv_index_tomorrow")


async def async_setup_entry(
    hass: HomeAssistant, entry: WetterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data
    entities: list[SensorEntity] = [*(WetterSensor(data.weather, d) for d in SENSORS), WarningsSensor(data.warnings)]
    if data.health is not None:
        entities += [PollenSensor(data.health, key) for key in health.POLLEN.values()]
        entities += [UvSensor(data.health, 0), UvSensor(data.health, 1)]
    else:
        # switched off in the options: remove the entities instead of leaving them unavailable
        registry = er.async_get(hass)
        unique_ids = {f"{entry.entry_id}_{key}" for key in HEALTH_KEYS}
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg.domain == "sensor" and reg.unique_id in unique_ids:
                registry.async_remove(reg.entity_id)
    async_add_entities(entities)


class WetterSensor(WetterEntity, SensorEntity):
    entity_description: WetterSensorDescription

    def __init__(self, coordinator: WetterCoordinator, description: WetterSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._update_from(coordinator.values())

    def _update_from(self, now: dict[str, Any]) -> None:
        d = self.entity_description
        if d.value_fn is not None:
            self._attr_native_value = d.value_fn(self.coordinator, now)
        elif d.value_key is not None:
            self._attr_native_value = now.get(d.value_key)
        self._attr_extra_state_attributes = d.attributes_fn(self.coordinator, now) if d.attributes_fn else {}

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from(self.coordinator.values())
        super()._handle_coordinator_update()


class WarningsSensor(WarningEntity, SensorEntity):
    """Anzahl der gültigen Warnungen; alle Warnungen (schlimmste zuerst) im Attribut."""

    _attr_translation_key = "warnings"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WarningsCoordinator) -> None:
        super().__init__(coordinator, "warnings")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.warnings)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        found = self.coordinator.warnings
        return {
            "area": (self.coordinator.data or {}).get("area"),
            "highest_severity": found[0]["severity"] if found else None,
            "warnings": found,
            # Quellen, die zuletzt nicht erreichbar waren (ihre alten Warnungen stehen noch)
            "stale": self.coordinator.stale,
        }


class PollenSensor(HealthEntity, SensorEntity):
    """Pollen level of one kind today (0 … 3 in half steps); tomorrow and the day after as attributes."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: HealthCoordinator, key: str) -> None:
        super().__init__(coordinator, f"pollen_{key}")
        self._key = key
        self._attr_translation_key = f"pollen_{key}"

    @property
    def native_value(self) -> float | None:
        return cast(float | None, self.coordinator.pollen(self._key)[0])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        today, tomorrow, after = self.coordinator.pollen(self._key)
        region = ((self.coordinator.data or {}).get("pollen") or {}).get("region")
        return {"today": today, "tomorrow": tomorrow, "day_after_tomorrow": after, "region": region}


class UvSensor(HealthEntity, SensorEntity):
    """UV index of the nearest DWD place, today (`day` 0) or tomorrow (`day` 1)."""

    _attr_native_unit_of_measurement = UV_INDEX
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: HealthCoordinator, day: int) -> None:
        key = "uv_index" if day == 0 else "uv_index_tomorrow"
        super().__init__(coordinator, key)
        self._day = day
        self._attr_translation_key = key

    @property
    def native_value(self) -> int | None:
        return cast(int | None, self.coordinator.uv()[self._day])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"city": ((self.coordinator.data or {}).get("uv") or {}).get("city")}
