"""Wetter: DWD-Vorhersage (Bright Sky), Nachbarschaftswerte, amtliche Warnungen, Pollen/UV, Start ohne Warten."""

from __future__ import annotations

from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from .const import CONF_POLLEN_UV, DOMAIN
from .coordinator import HealthCoordinator, WarningsCoordinator, WetterConfigEntry, WetterCoordinator, WetterData

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.WEATHER]
# Up to 26.11.2: slider that wrote the Netatmo weather area (removed, hacs/default#13248)
OLD_RADIUS = "neighbourhood_radius"


async def async_setup_entry(hass: HomeAssistant, entry: WetterConfigEntry) -> bool:
    """Letzten Stand sofort zeigen, Bright Sky und NINA erst nach dem HA-Start fragen."""
    # The old radius slider is gone; its registry entry would stay as a "no longer provided" entity
    reg = er.async_get(hass)
    if old := reg.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_{OLD_RADIUS}"):
        reg.async_remove(old)
    weather = WetterCoordinator(hass, entry)
    warnings = WarningsCoordinator(hass, entry, weather.client)
    later: list[WetterCoordinator | WarningsCoordinator | HealthCoordinator] = []
    for coordinator in (weather, warnings):
        if await coordinator.async_restore():
            later.append(coordinator)
        else:
            await coordinator.async_config_entry_first_refresh()
    health: HealthCoordinator | None = None
    if entry.options.get(CONF_POLLEN_UV, True):
        # Pollen and UV never hold up the start and never fail it: always fetched in the background
        health = HealthCoordinator(hass, entry)
        await health.async_restore()
        later.append(health)

        @callback
        def new_day(_now: datetime) -> None:
            # today/tomorrow follow the German calendar day: refresh at every full hour, so the switch at
            # German midnight is on time whatever time zone Home Assistant runs in
            assert health is not None
            health.async_update_listeners()

        entry.async_on_unload(async_track_time_change(hass, new_day, minute=0, second=5))
    if later:

        async def refresh(_hass: HomeAssistant) -> None:
            for coordinator in later:
                entry.async_create_background_task(hass, coordinator.async_refresh(), f"{DOMAIN} {coordinator.name}")

        entry.async_on_unload(async_at_started(hass, refresh))
    entry.runtime_data = WetterData(weather=weather, warnings=warnings, health=health)
    weather.async_track_local()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: WetterConfigEntry) -> None:
    """Gespeicherte Stände löschen, wenn die Integration entfernt wird."""
    for key in (
        f"{DOMAIN}.{entry.entry_id}",
        f"{DOMAIN}.{entry.entry_id}.warnings",
        f"{DOMAIN}.{entry.entry_id}.health",
    ):
        await Store[dict[str, object]](hass, 1, key).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: WetterConfigEntry) -> bool:
    """Entitäten entfernen."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
