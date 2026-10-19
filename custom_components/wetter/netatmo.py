"""Netatmo-Wetterbereich in der Nähe finden und dessen Sensoren als Nachbarschaftswerte vorschlagen.

Die Netatmo-Integration legt für jeden „Wetterbereich“ (öffentliche Stationen in einem Rechteck, Optionen
`weather_areas`) Sensoren mit der Kennung `<Bereich>-temperature`, `-humidity` und `-rain` an. Bei mehreren
Bereichen gewinnt der, dessen Mittelpunkt dem Ort am nächsten liegt.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_LOCAL_HUMIDITY, CONF_LOCAL_PRECIPITATION, CONF_LOCAL_TEMPERATURE

NETATMO = "netatmo"
# Option → Endung der Netatmo-Kennung (Luftdruck und Wind taugen aus Bereichen nicht, siehe README)
SUFFIXES = {
    CONF_LOCAL_TEMPERATURE: "temperature",
    CONF_LOCAL_HUMIDITY: "humidity",
    CONF_LOCAL_PRECIPITATION: "rain",
}


def _center(area: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = (float(area["lat_ne"]) + float(area["lat_sw"])) / 2
        lon = (float(area["lon_ne"]) + float(area["lon_sw"])) / 2
    except (KeyError, TypeError, ValueError):
        return None
    return lat, lon


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Näherung in Grad – reicht zum Vergleichen naher Bereiche."""
    dlat = a[0] - b[0]
    dlon = (a[1] - b[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def suggest(hass: HomeAssistant, latitude: float, longitude: float) -> dict[str, str]:
    """Option → Entität des nächstgelegenen Netatmo-Wetterbereichs; leer, wenn es keinen gibt."""
    best: tuple[float, str, str] | None = None  # (Abstand, entry_id, Bereich)
    for entry in hass.config_entries.async_entries(NETATMO):
        areas = entry.options.get("weather_areas") or {}
        if not isinstance(areas, dict):
            continue
        for name, area in areas.items():
            center = _center(area) if isinstance(area, dict) else None
            if center is None:
                continue
            dist = _distance(center, (latitude, longitude))
            if best is None or dist < best[0]:
                best = (dist, entry.entry_id, str(name))
    if best is None:
        return {}
    _, entry_id, name = best
    by_unique = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
        if e.domain == "sensor" and e.disabled_by is None
    }
    return {
        option: by_unique[f"{name}-{suffix}"] for option, suffix in SUFFIXES.items() if f"{name}-{suffix}" in by_unique
    }


def _km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    return _distance((lat_a, lon_a), (lat_b, lon_b)) * 111.2


def area_info(hass: HomeAssistant, entity_ids: list[str], latitude: float, longitude: float) -> dict[str, Any] | None:
    """Netatmo-Bereich der eingetragenen Nachbarschaftssensoren: Name, Breite/Höhe, Fläche, Abstand zum Ort."""
    reg = er.async_get(hass)
    for entity_id in entity_ids:
        entry = reg.async_get(entity_id)
        if entry is None or entry.platform != NETATMO or entry.config_entry_id is None or "-" not in entry.unique_id:
            continue
        name = entry.unique_id.rsplit("-", 1)[0]
        config = hass.config_entries.async_get_entry(entry.config_entry_id)
        area = ((config.options.get("weather_areas") or {}) if config else {}).get(name)
        center = _center(area) if isinstance(area, dict) else None
        if area is None or center is None:
            continue
        width = _km(center[0], float(area["lon_sw"]), center[0], float(area["lon_ne"]))
        height = _km(float(area["lat_sw"]), center[1], float(area["lat_ne"]), center[1])
        return {
            "entry_id": config.entry_id if config else None,
            "name": name,
            "width_km": round(width, 1),
            "height_km": round(height, 1),
            "area_km2": round(width * height, 1),
            "distance_km": round(_km(center[0], center[1], latitude, longitude), 1),
            "radius_km": round((width + height) / 4, 1),
        }
    return None


def set_radius(hass: HomeAssistant, info: dict[str, Any], latitude: float, longitude: float, radius_km: float) -> None:
    """Netatmo-Bereich als Quadrat mit halber Kantenlänge `radius_km` um den Ort neu setzen (Netatmo lädt neu)."""
    config = hass.config_entries.async_get_entry(str(info["entry_id"]))
    if config is None:
        return
    areas = {k: dict(v) for k, v in (config.options.get("weather_areas") or {}).items() if isinstance(v, dict)}
    area = areas.get(str(info["name"]))
    if area is None:
        return
    dlat = radius_km / 111.2
    dlon = radius_km / (111.2 * math.cos(math.radians(latitude)))
    area.update(
        lat_ne=round(latitude + dlat, 6),
        lat_sw=round(latitude - dlat, 6),
        lon_ne=round(longitude + dlon, 6),
        lon_sw=round(longitude - dlon, 6),
    )
    hass.config_entries.async_update_entry(config, options={**config.options, "weather_areas": areas})
