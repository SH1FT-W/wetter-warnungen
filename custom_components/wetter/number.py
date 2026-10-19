"""Regler „Nachbarschaft Radius“: Netatmo-Wetterbereich um den Ort vergrößern oder verkleinern.

Schreibt das Rechteck des Bereichs in die Optionen der Netatmo-Integration (wie deren eigener Dialog); Netatmo lädt
danach neu und liefert die Werte nach einigen Minuten wieder. Den Regler gibt es nur, wenn beim Laden ein
Netatmo-Bereich hinter den Nachbarschaftssensoren steht; verschwindet der Bereich später, ist er nicht verfügbar.
"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import netatmo
from .const import DOMAIN
from .coordinator import WetterConfigEntry, WetterCoordinator
from .entity import WetterEntity

PARALLEL_UPDATES = 1
RADIUS = "neighbourhood_radius"


async def async_setup_entry(
    hass: HomeAssistant, entry: WetterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.weather
    if coordinator.neighbourhood() is None:
        # Ohne Netatmo-Bereich kein dauerhaft „nicht verfügbarer“ Regler; einen alten Eintrag aufräumen
        reg = er.async_get(hass)
        if entity_id := reg.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_{RADIUS}"):
            reg.async_remove(entity_id)
        return
    async_add_entities([NeighbourhoodRadius(coordinator)])


class NeighbourhoodRadius(WetterEntity, NumberEntity):
    _attr_translation_key = "neighbourhood_radius"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_native_min_value = 1
    _attr_native_max_value = 20
    _attr_native_step = 0.5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: WetterCoordinator) -> None:
        super().__init__(coordinator, RADIUS)

    @property
    def available(self) -> bool:
        return self.coordinator.neighbourhood() is not None

    @property
    def native_value(self) -> float | None:
        info = self.coordinator.neighbourhood()
        return None if info is None else float(info["radius_km"])

    async def async_set_native_value(self, value: float) -> None:
        info = self.coordinator.neighbourhood()
        if info is None:
            return
        data = self.coordinator.config_entry.data
        netatmo.set_radius(self.hass, info, float(data[CONF_LATITUDE]), float(data[CONF_LONGITUDE]), value)
        self.async_write_ha_state()
