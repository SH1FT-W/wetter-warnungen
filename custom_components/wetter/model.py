"""Auswertung der Bright-Sky-Daten – reine Funktionen ohne Home Assistant.

- `now_values`: aktuelle Werte (Messung der Station, Lücken aus der Vorhersage der laufenden Stunde)
- `hourly_forecast` / `daily_forecast`: Vorhersage im Format der HA-Wetter-Entität (`native_*`-Schlüssel)
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, tzinfo
import math
from typing import Any

# Bright-Sky-Symbol → HA-Wetterzustand
CONDITIONS = {
    "clear-day": "sunny",
    "clear-night": "clear-night",
    "partly-cloudy-day": "partlycloudy",
    "partly-cloudy-night": "partlycloudy",
    "cloudy": "cloudy",
    "fog": "fog",
    "wind": "windy",
    "rain": "rainy",
    "sleet": "snowy-rainy",
    "snow": "snowy",
    "hail": "hail",
    "thunderstorm": "lightning-rainy",
}
ALL_CONDITIONS = sorted({*CONDITIONS.values(), "pouring"})
# ab so viel Regen pro Stunde heißt es „Starkregen“
POURING_MM = 4.0
# Niederschlagsarten – die „schlimmste“ eines Tages bestimmt sein Symbol
PRECIP_RANK = {"rain": 1, "sleet": 2, "snow": 3, "hail": 4, "thunderstorm": 5}
# so viel Regen am Tag (tagsüber), damit der Tag als Regentag gilt
WET_DAY_MM = 1.0
DAYTIME = range(7, 21)


def parse_time(value: Any) -> datetime | None:
    """ISO-Zeitstempel von Bright Sky (mit Zeitzone) oder None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def condition(icon: Any, precipitation: Any = None) -> str | None:
    """HA-Wetterzustand aus dem Bright-Sky-Symbol; viel Regen wird „pouring“."""
    if not isinstance(icon, str):
        return None
    cond = CONDITIONS.get(icon)
    if cond == "rainy" and isinstance(precipitation, int | float) and precipitation >= POURING_MM:
        return "pouring"
    return cond


def humidity_from_dew_point(temperature: Any, dew_point: Any) -> float | None:
    """Relative Luftfeuchte aus Temperatur und Taupunkt (Magnus-Formel) – die Vorhersage liefert sie oft nicht."""
    if not isinstance(temperature, int | float) or not isinstance(dew_point, int | float):
        return None

    def vapour(t: float) -> float:
        return math.exp(17.62 * t / (243.12 + t))

    return round(max(0.0, min(100.0, 100 * vapour(dew_point) / vapour(temperature))), 0)


def apparent_temperature(temperature: Any, humidity: Any, wind_speed: Any) -> float | None:
    """Gefühlte Temperatur nach Steadman (Australian Bureau of Meteorology), wie bei Open-Meteo.

    Temperatur in °C, relative Feuchte in %, Wind in km/h (10 m). Fehlt der Wind, gilt Windstille: dann wirkt
    nur die Feuchte, das ist näher an der Wahrheit als gar kein Wert. Ohne Temperatur oder Feuchte kein Wert.
    """
    if not isinstance(temperature, int | float) or not isinstance(humidity, int | float):
        return None
    wind_ms = wind_speed / 3.6 if isinstance(wind_speed, int | float) else 0.0
    vapour_hpa = humidity / 100 * 6.105 * math.exp(17.27 * temperature / (237.7 + temperature))
    return round(temperature + 0.33 * vapour_hpa - 0.70 * wind_ms - 4.0, 1)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def hour_at(hourly: list[dict[str, Any]], when: datetime) -> dict[str, Any] | None:
    """Die Vorhersage-Stunde, die `when` enthält (Bright Sky stempelt das Ende der Stunde)."""
    best: dict[str, Any] | None = None
    for hour in hourly:
        ts = parse_time(hour.get("timestamp"))
        if ts is None:
            continue
        if ts > when:
            return hour if ts - when <= timedelta(hours=1) else best
        best = hour
    return best


def now_values(current: dict[str, Any] | None, hourly: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Aktuelle Werte: Messung der Station, fehlende Größen aus der Vorhersage der laufenden Stunde."""
    obs = current or {}
    fc = hour_at(hourly, now) or {}

    def pick(*keys: str) -> float | None:
        for key in keys:
            value = _num(obs.get(key))
            if value is not None:
                return value
        return None

    def fallback(obs_value: float | None, key: str) -> float | None:
        return obs_value if obs_value is not None else _num(fc.get(key))

    temperature = fallback(pick("temperature"), "temperature")
    dew_point = fallback(pick("dew_point"), "dew_point")
    precipitation = fallback(pick("precipitation_60"), "precipitation")
    visibility = fallback(pick("visibility"), "visibility")
    solar = _num(fc.get("solar"))
    humidity = pick("relative_humidity")
    if humidity is None:
        humidity = _num(fc.get("relative_humidity"))
    if humidity is None:
        # erst wenn auch die Vorhersage keine Feuchte hat (0 % ist ein Wert, kein Fehlen)
        humidity = humidity_from_dew_point(temperature, dew_point)
    icon = obs.get("icon") if isinstance(obs.get("icon"), str) else fc.get("icon")
    wind_speed = fallback(pick("wind_speed_10", "wind_speed_30", "wind_speed_60"), "wind_speed")
    return {
        "temperature": temperature,
        "apparent_temperature": apparent_temperature(temperature, humidity, wind_speed),
        "dew_point": dew_point,
        "humidity": humidity,
        "pressure": fallback(pick("pressure_msl"), "pressure_msl"),
        "wind_speed": wind_speed,
        "wind_bearing": fallback(pick("wind_direction_10", "wind_direction_30", "wind_direction_60"), "wind_direction"),
        "wind_gust": fallback(
            pick("wind_gust_speed_10", "wind_gust_speed_30", "wind_gust_speed_60"), "wind_gust_speed"
        ),
        "precipitation": precipitation,
        "cloud_cover": fallback(pick("cloud_cover"), "cloud_cover"),
        "visibility": None if visibility is None else round(visibility / 1000, 1),
        # kWh/m² der laufenden Stunde = mittlere Leistung in kW/m² → W/m²
        "solar": None if solar is None else round(solar * 1000, 0),
        "condition": condition(icon, precipitation),
        # Wahrscheinlichkeit für Niederschlag in der laufenden Stunde (DWD MOSMIX, ≥ 0,1 mm)
        "precipitation_probability": _num(fc.get("precipitation_probability")),
        "observed_at": obs.get("timestamp"),
    }


def _forecast_hour(hour: dict[str, Any], ts: datetime) -> dict[str, Any]:
    temperature, dew_point = _num(hour.get("temperature")), _num(hour.get("dew_point"))
    humidity = _num(hour.get("relative_humidity"))
    if humidity is None:
        humidity = humidity_from_dew_point(temperature, dew_point)
    wind_speed = _num(hour.get("wind_speed"))
    return {
        "datetime": ts.isoformat(),
        "condition": condition(hour.get("icon"), hour.get("precipitation")),
        "native_temperature": temperature,
        "native_apparent_temperature": apparent_temperature(temperature, humidity, wind_speed),
        "native_dew_point": dew_point,
        "humidity": humidity,
        "native_precipitation": _num(hour.get("precipitation")),
        "precipitation_probability": _num(hour.get("precipitation_probability")),
        "native_pressure": _num(hour.get("pressure_msl")),
        "native_wind_speed": wind_speed,
        "native_wind_gust_speed": _num(hour.get("wind_gust_speed")),
        "wind_bearing": _num(hour.get("wind_direction")),
        "cloud_coverage": _num(hour.get("cloud_cover")),
    }


def hourly_forecast(hourly: list[dict[str, Any]], now: datetime, hours: int) -> list[dict[str, Any]]:
    """Stündliche Vorhersage ab der laufenden Stunde."""
    start = now.replace(minute=0, second=0, microsecond=0)
    out: list[dict[str, Any]] = []
    for hour in hourly:
        ts = parse_time(hour.get("timestamp"))
        if ts is None or ts < start:
            continue
        out.append(_forecast_hour(hour, ts))
        if len(out) >= hours:
            break
    return out


def _day_condition(day: list[tuple[datetime, dict[str, Any]]]) -> str | None:
    """Symbol eines Tages: tagsüber nennenswerter Niederschlag → dessen schlimmste Art, sonst häufigstes Symbol."""
    daytime = [h for t, h in day if t.hour in DAYTIME] or [h for _, h in day]
    rain = sum(_num(h.get("precipitation")) or 0.0 for h in daytime)
    if rain >= WET_DAY_MM:
        kinds = [str(h.get("condition")) for h in daytime if h.get("condition") in PRECIP_RANK]
        if kinds:
            worst = max(kinds, key=lambda k: PRECIP_RANK[k])
            heavy = max((_num(h.get("precipitation")) or 0.0 for h in daytime), default=0.0)
            return condition(worst, heavy)
    icons = Counter(
        str(h.get("icon")).replace("-night", "-day") for h in daytime if isinstance(h.get("icon"), str)
    ).most_common(1)
    return condition(icons[0][0]) if icons else None


def daily_forecast(hourly: list[dict[str, Any]], now: datetime, tz: tzinfo, days: int) -> list[dict[str, Any]]:
    """Tägliche Vorhersage ab heute (Ortszeit): Höchst-/Tiefstwert, Regensumme, höchste Wahrscheinlichkeit."""
    by_day: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    today = now.astimezone(tz).date()
    for hour in hourly:
        ts = parse_time(hour.get("timestamp"))
        if ts is None:
            continue
        local = ts.astimezone(tz)
        if local.date() < today:
            continue
        by_day.setdefault(local.date().isoformat(), []).append((local, hour))

    out: list[dict[str, Any]] = []
    for key in sorted(by_day)[:days]:
        day = by_day[key]
        hours = [h for _, h in day]

        def values(field: str, hours: list[dict[str, Any]] = hours) -> list[float]:
            return [v for v in (_num(h.get(field)) for h in hours) if v is not None]

        temps, rain = values("temperature"), values("precipitation")
        # Regenrisiko des Tages: 6-Std.-Wahrscheinlichkeit (wie die DWD-Tagesvorhersage), sonst die stündliche
        prob = values("precipitation_probability_6h") or values("precipitation_probability")
        wind, gust, clouds = values("wind_speed"), values("wind_gust_speed"), values("cloud_cover")
        midnight = day[0][0].replace(hour=0, minute=0, second=0, microsecond=0)
        out.append(
            {
                "datetime": midnight.isoformat(),
                "condition": _day_condition(day),
                "native_temperature": max(temps) if temps else None,
                "native_templow": min(temps) if temps else None,
                "native_precipitation": round(sum(rain), 1) if rain else None,
                "precipitation_probability": max(prob) if prob else None,
                "native_wind_speed": max(wind) if wind else None,
                "native_wind_gust_speed": max(gust) if gust else None,
                "cloud_coverage": round(sum(clouds) / len(clouds)) if clouds else None,
            }
        )
    return out
