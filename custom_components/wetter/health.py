"""Pollen and UV index of the DWD (Open Data) – pure functions without Home Assistant.

- Pollen: `s31fg.json`, Pollenflug-Gefahrenindex per region (27 regions/sub-regions), today/tomorrow/day after
  tomorrow, levels "0" … "3" with half steps written as "0-1", "1-2", "2-3".
- UV: `uvi.json`, UV index for 38 places in Germany, today/tomorrow/day after tomorrow.

Both files are published once a day. Their "today" is the day of publication: a file that is a day old (no
update on that day) shifts by one day, so "today" is always the local calendar day.
"""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any
from zoneinfo import ZoneInfo

# Times in the DWD files are German local time without zone
DWD_TZ = ZoneInfo("Europe/Berlin")
DAYS = ("today", "tomorrow", "dayafter_to")

# DWD name → entity key (English, like all keys)
POLLEN = {
    "Ambrosia": "ambrosia",
    "Beifuss": "mugwort",
    "Birke": "birch",
    "Erle": "alder",
    "Esche": "ash",
    "Graeser": "grass",
    "Hasel": "hazel",
    "Roggen": "rye",
}
_POLLEN_LEVELS = {"0": 0.0, "0-1": 0.5, "1": 1.0, "1-2": 1.5, "2": 2.0, "2-3": 2.5, "3": 3.0}

# Places of the UV forecast with the coordinates of their DWD station (GeoServer layer dwd:Uv_Stationen, 11/2026)
UV_CITIES: dict[str, tuple[float, float]] = {
    "Arkona": (54.6817, 13.4367),
    "Berlin": (52.5656, 13.3108),
    "Bonn": (50.7, 7.0333),
    "Bremen": (53.0464, 8.7994),
    "Cottbus": (51.7686, 14.3169),
    "Dresden": (51.1292, 13.7561),
    "Düsseldorf": (51.2972, 6.77),
    "Frankfurt/Main": (50.0464, 8.5986),
    "Freiburg": (48.0017, 7.8461),
    "Hahn": (49.9475, 7.2647),
    "Hamburg": (53.635, 9.99),
    "Hannover": (52.4658, 9.6797),
    "Kassel": (51.2978, 9.4436),
    "Kiel": (54.3778, 10.1447),
    "Konstanz": (47.6781, 9.1911),
    "Leipzig": (51.4358, 12.2408),
    "List auf Sylt": (55.0125, 8.4125),
    "Magdeburg": (52.1033, 11.5844),
    "Marienleuchte": (54.5, 11.2333),
    "München": (48.1642, 11.5442),
    "Neubrandenburg": (53.5483, 13.1931),
    "Norderney": (53.7139, 7.1525),
    "Nürnberg": (49.5044, 11.0569),
    "Osnabrück": (52.2572, 8.0544),
    "Regensburg": (49.0428, 12.1044),
    "Rostock": (54.1817, 12.0822),
    "Sankt Peter-Ording": (54.3297, 8.6028),
    "Seehausen": (52.8928, 11.7306),
    "Stuttgart": (48.8294, 9.2011),
    "Ulm": (48.3847, 9.9539),
    "Waren": (53.5228, 12.6669),
    "Weimar": (50.9756, 11.3094),
    "Weinbiet": (49.3769, 8.1225),
    "Wernigerode": (51.8478, 10.7697),
    "Würzburg": (49.7717, 9.9586),
    # Mountain stations (839 m to 2960 m) are never chosen: UV up there is far higher than in the valley.
    # Kept here so the list matches the file; `nearest_uv_city` skips them.
    "Großer Arber": (49.1139, 13.1353),
    "Kahler Asten": (51.1814, 8.4897),
    "Zugspitze": (47.4222, 10.9867),
}
UV_MOUNTAINS = frozenset({"Großer Arber", "Kahler Asten", "Zugspitze"})
# Farther away than this no UV place is a fair stand-in (the forecast covers Germany only)
UV_MAX_KM = 200.0


def pollen_level(value: Any) -> float | None:
    """Pollen level "0" … "3" ("1-2" → 1.5); unknown values (DWD writes "-1" for "no forecast") → None."""
    return _POLLEN_LEVELS.get(str(value).strip()) if value is not None else None


def uv_level(value: Any) -> int | None:
    """UV index as whole number; negative or non-numeric values → None."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return round(value)


def parse_dwd_time(value: Any) -> datetime | None:
    """DWD time stamp ("2026-11-03 11:00 Uhr" or "2026-11-03T07:30:00", German local time) → aware datetime."""
    if not isinstance(value, str):
        return None
    text = value.removesuffix("Uhr").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=DWD_TZ)


def _issue_date(value: Any) -> str | None:
    parsed = parse_dwd_time(value)
    if parsed is None and isinstance(value, str):
        # `forecast_day` of the UV file is a bare date
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None if parsed is None else parsed.astimezone(DWD_TZ).date().isoformat()


def by_day(values: list[Any], issued: str | None, today: date) -> list[Any]:
    """Values for today, tomorrow, day after tomorrow, shifted when the file is older than today."""
    if issued is None:
        return [None] * len(DAYS)
    offset = max(0, (today - date.fromisoformat(issued)).days)
    shifted = list(values[offset:])
    return shifted + [None] * (len(DAYS) - len(shifted))


def _items(data: Any, key: str) -> list[dict[str, Any]]:
    """Dicts in the list `data[key]`; anything else (a reshaped file) gives none."""
    value = data.get(key) if isinstance(data, dict) else None
    return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []


def pollen_for(data: dict[str, Any], cell: int) -> dict[str, Any] | None:
    """Pollen of the region `cell` (sub-region id; regions without sub-regions use their region id)."""
    for item in _items(data, "content"):
        part, region = item.get("partregion_id"), item.get("region_id")
        if part != cell and not (part == -1 and region == cell):
            continue
        pollen = item.get("Pollen")
        pollen = pollen if isinstance(pollen, dict) else {}
        values = {
            key: [pollen_level(pollen[name].get(day)) for day in DAYS]
            for name, key in POLLEN.items()
            if isinstance(pollen.get(name), dict)
        }
        if not values:
            # region present but no readable pollen (reshaped file): no data, the last values stay
            return None
        return {
            "region": item.get("partregion_name") or item.get("region_name"),
            "issued": _issue_date(data.get("last_update")),
            "next_update": data.get("next_update"),
            "values": values,
        }
    return None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def nearest_uv_city(latitude: float, longitude: float, available: set[str]) -> str | None:
    """Nearest UV place in the file (no mountain stations), at most `UV_MAX_KM` away."""
    distances = [
        (_km(latitude, longitude, *UV_CITIES[city]), city)
        for city in available
        if city in UV_CITIES and city not in UV_MOUNTAINS
    ]
    if not distances:
        return None
    km, city = min(distances)
    return city if km <= UV_MAX_KM else None


def uv_for(data: dict[str, Any], latitude: float, longitude: float) -> dict[str, Any] | None:
    """UV index of the nearest place."""
    forecasts = {
        str(item.get("city")): item["forecast"]
        for item in _items(data, "content")
        if isinstance(item.get("forecast"), dict)
    }
    city = nearest_uv_city(latitude, longitude, set(forecasts))
    if city is None:
        return None
    return {
        "city": city,
        "issued": _issue_date(data.get("forecast_day")),
        "next_update": data.get("next_update"),
        "values": [uv_level(forecasts[city].get(day)) for day in DAYS],
    }


def region_cell(geojson: Any) -> int | None:
    """Pollen region id (`CELL_ID`) from the GeoServer answer for the location; None outside the regions."""
    for feature in _items(geojson, "features"):
        properties = feature.get("properties")
        cell = properties.get("CELL_ID") if isinstance(properties, dict) else None
        if isinstance(cell, int | float) and not isinstance(cell, bool):
            return int(cell)
    return None
