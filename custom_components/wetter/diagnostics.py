"""Diagnose ohne genauen Ort: Koordinaten und Warngebiet (Gemeinde) geschwärzt."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

from .coordinator import WetterConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: WetterConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data.weather
    data = coordinator.data or {}
    warnings = entry.runtime_data.warnings
    out = {
        "entry": dict(entry.data),
        "options": dict(entry.options),
        "fetched": data.get("fetched"),
        "hours": len(data.get("hourly") or []),
        "current": data.get("current"),
        "warnings": warnings.warnings,
        # das Warngebiet ist die Gemeinde und damit fast so genau wie die Koordinaten
        "warn_area": (warnings.data or {}).get("area"),
        "warn_stale": (warnings.data or {}).get("stale"),
        "now": coordinator.values(),
    }
    return async_redact_data(out, {CONF_LATITUDE, CONF_LONGITUDE, "warn_area"})
