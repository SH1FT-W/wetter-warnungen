"""Wetter: DWD-Vorhersage (Bright Sky), Werte aus der Nachbarschaft, amtliche Warnungen, Start ohne Warten."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .coordinator import WarningsCoordinator, WetterConfigEntry, WetterCoordinator, WetterData

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR, Platform.WEATHER]


async def async_setup_entry(hass: HomeAssistant, entry: WetterConfigEntry) -> bool:
    """Letzten Stand sofort zeigen, Bright Sky und NINA erst nach dem HA-Start fragen."""
    weather = WetterCoordinator(hass, entry)
    warnings = WarningsCoordinator(hass, entry, weather.client)
    later: list[WetterCoordinator | WarningsCoordinator] = []
    for coordinator in (weather, warnings):
        if await coordinator.async_restore():
            later.append(coordinator)
        else:
            await coordinator.async_config_entry_first_refresh()
    if later:

        async def refresh(_hass: HomeAssistant) -> None:
            for coordinator in later:
                entry.async_create_background_task(hass, coordinator.async_refresh(), f"{DOMAIN} {coordinator.name}")

        entry.async_on_unload(async_at_started(hass, refresh))
    entry.runtime_data = WetterData(weather=weather, warnings=warnings)
    weather.async_track_local()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: WetterConfigEntry) -> None:
    """Gespeicherte Stände löschen, wenn die Integration entfernt wird."""
    for key in (f"{DOMAIN}.{entry.entry_id}", f"{DOMAIN}.{entry.entry_id}.warnings"):
        await Store[dict[str, object]](hass, 1, key).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: WetterConfigEntry) -> bool:
    """Entitäten entfernen."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
