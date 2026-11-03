"""Warning slots 1–5 (on = a warning in this slot), with the attributes the NINA integration had up to HA 2026.10."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import warnings
from .const import WARNING_SLOTS
from .coordinator import WarningsCoordinator, WetterConfigEntry, WetterCoordinator
from .entity import WarningEntity, WetterEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: WetterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.warnings
    async_add_entities(
        [
            *(WarningSlot(coordinator, n) for n in range(1, WARNING_SLOTS + 1)),
            RainNextHour(entry.runtime_data.weather),
        ]
    )


class WarningSlot(WarningEntity, BinarySensorEntity):
    """Platz `n` der nach Schwere sortierten Warnungen."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_translation_key = "warning"

    def __init__(self, coordinator: WarningsCoordinator, slot: int) -> None:
        super().__init__(coordinator, f"warning_{slot}")
        self._slot = slot
        self._attr_translation_placeholders = {"slot": str(slot)}

    def _warning(self) -> dict[str, Any] | None:
        found = self.coordinator.warnings
        return found[self._slot - 1] if len(found) >= self._slot else None

    @property
    def is_on(self) -> bool:
        return self._warning() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # region immer (auch ohne Warnung): Dashboards zeigen „Aktuell keine Warnungen für …“
        area = warnings.region((self.coordinator.data or {}).get("area"))
        return {**(self._warning() or {}), "region": area}


class RainNextHour(WetterEntity, BinarySensorEntity):
    """On when the sensor "Regen in" is below 60 minutes (rain now, in the running hour, or starting within 60 min)."""

    _attr_translation_key = "rain_next_hour"

    def __init__(self, coordinator: WetterCoordinator) -> None:
        super().__init__(coordinator, "rain_next_hour")

    @property
    def is_on(self) -> bool:
        rain = self.coordinator.values().get("rain_in")
        return rain is not None and rain["minutes"] < 60
