"""Basis aller Entitäten: ein Dienst-Gerät je Ort."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN
from .coordinator import WarningsCoordinator, WetterCoordinator


class _Base[C: DataUpdateCoordinator[dict[str, Any]]](CoordinatorEntity[C]):
    _attr_has_entity_name = True
    _attr_attribution = "Deutscher Wetterdienst (über Bright Sky)"

    def __init__(self, coordinator: C, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry is not None
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Deutscher Wetterdienst",
            model="MOSMIX + SYNOP über Bright Sky",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://brightsky.dev",
        )


class WetterEntity(_Base[WetterCoordinator]):
    """Entität mit Wetterwerten."""


class WarningEntity(_Base[WarningsCoordinator]):
    """Entität mit Warnungen – bleibt verfügbar, wenn eine Abfrage scheitert (sonst gäbe es falsche Entwarnungen)."""

    _attr_attribution = "Deutscher Wetterdienst, Warnsystem des Bundes (NINA)"

    @property
    def available(self) -> bool:
        return True
