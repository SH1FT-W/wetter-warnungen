"""Auswertung ohne Home Assistant: Symbole, aktuelle Werte, Vorhersage, Warnungen."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.wetter import model

from .conftest import load

NOW = datetime(2026, 10, 15, 9, 40, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")
HOURLY = load("weather.json")["weather"]
CURRENT = load("current.json")["weather"]


@pytest.mark.parametrize(
    ("icon", "rain", "expected"),
    [
        ("clear-day", None, "sunny"),
        ("clear-night", 0, "clear-night"),
        ("partly-cloudy-night", None, "partlycloudy"),
        ("rain", 1.2, "rainy"),
        ("rain", 4.0, "pouring"),
        ("thunderstorm", 9, "lightning-rainy"),
        ("sleet", None, "snowy-rainy"),
        ("unbekannt", None, None),
        (None, None, None),
    ],
)
def test_condition(icon: str | None, rain: float | None, expected: str | None) -> None:
    assert model.condition(icon, rain) == expected


def test_apparent_temperature() -> None:
    """Steadman-Formel: Feuchte wärmt, Wind kühlt, ohne Wind gilt Windstille."""
    assert model.apparent_temperature(30.0, 70.0, 0.0) == 35.8
    assert model.apparent_temperature(5.0, 80.0, 36.0) == -3.7, "36 km/h = 10 m/s"
    assert model.apparent_temperature(20.0, 50.0, None) == model.apparent_temperature(20.0, 50.0, 0.0)
    assert model.apparent_temperature(None, 50.0, 0.0) is None
    assert model.apparent_temperature(20.0, None, 0.0) is None


def test_humidity_from_dew_point() -> None:
    assert model.humidity_from_dew_point(20.0, 20.0) == 100
    assert model.humidity_from_dew_point(20.0, 10.0) == 53
    assert model.humidity_from_dew_point(None, 10.0) is None


def test_parse_time() -> None:
    assert model.parse_time("2026-10-15T10:00:00+00:00") == datetime(2026, 10, 15, 10, tzinfo=UTC)
    assert model.parse_time("2026-10-15T10:00:00") is None, "ohne Zeitzone unbrauchbar"
    assert model.parse_time("kaputt") is None
    assert model.parse_time(None) is None


def test_now_values_from_station_and_forecast() -> None:
    now = model.now_values(CURRENT, HOURLY, NOW)
    assert now["temperature"] == CURRENT["temperature"]
    assert now["humidity"] == CURRENT["relative_humidity"]
    assert now["pressure"] == CURRENT["pressure_msl"]
    assert now["wind_speed"] == CURRENT["wind_speed_10"]
    assert now["wind_gust"] == CURRENT["wind_gust_speed_10"]
    assert now["visibility"] == round(CURRENT["visibility"] / 1000, 1)
    assert now["condition"] == "cloudy"
    assert now["precipitation_probability"] is not None, "aus der Vorhersage der laufenden Stunde"
    assert now["apparent_temperature"] == model.apparent_temperature(
        now["temperature"], now["humidity"], now["wind_speed"]
    )
    # Sonne: Station misst keine → Vorhersage der laufenden Stunde (Stempel 10:00 = Stunde 09–10 Uhr UTC)
    hour = next(h for h in HOURLY if h["timestamp"] == "2026-10-15T10:00:00+00:00")
    assert now["solar"] == round(hour["solar"] * 1000)


def test_now_values_without_station() -> None:
    """Fällt die Station aus, kommen alle Werte aus der Vorhersage der laufenden Stunde."""
    now = model.now_values(None, HOURLY, NOW)
    hour = next(h for h in HOURLY if h["timestamp"] == "2026-10-15T10:00:00+00:00")
    assert now["temperature"] == hour["temperature"]
    assert now["precipitation"] == hour["precipitation"]
    assert now["humidity"] == model.humidity_from_dew_point(hour["temperature"], hour["dew_point"])
    assert now["condition"] == model.condition(hour["icon"])
    empty = model.now_values(None, [], NOW)
    assert empty["temperature"] is None and empty["condition"] is None and empty["solar"] is None


def test_now_values_zero_humidity_is_a_value() -> None:
    """0 % aus der Vorhersage ist ein Wert und wird nicht durch den Taupunkt ersetzt."""
    hour = {"timestamp": "2026-10-15T10:00:00+00:00", "relative_humidity": 0, "temperature": 20.0, "dew_point": 10.0}
    assert model.now_values({}, [hour], NOW)["humidity"] == 0
    assert model.now_values({}, [{**hour, "relative_humidity": None}], NOW)["humidity"] == 53, "fehlt → Taupunkt"


def test_hour_at_edges() -> None:
    assert model.hour_at(HOURLY, datetime(2030, 1, 1, tzinfo=UTC)) is HOURLY[-1], "nach dem Ende: letzte Stunde"
    assert model.hour_at([{"timestamp": "2026-10-15T20:00:00+00:00"}], NOW) is None, "Lücke > 1 Std."
    assert model.hour_at([{"timestamp": None}], NOW) is None


def test_hourly_forecast() -> None:
    fc = model.hourly_forecast(HOURLY, NOW, 48)
    assert len(fc) == 48
    assert fc[0]["datetime"] == "2026-10-15T09:00:00+00:00", "ab der laufenden Stunde"
    first = next(h for h in HOURLY if h["timestamp"] == "2026-10-15T09:00:00+00:00")
    assert fc[0]["native_temperature"] == first["temperature"]
    assert fc[0]["precipitation_probability"] == first["precipitation_probability"]
    assert all(f["humidity"] is not None for f in fc), "Feuchte aus dem Taupunkt, wenn sie fehlt"
    assert all(f["native_apparent_temperature"] is not None for f in fc), "gefühlt aus Temperatur, Feuchte, Wind"
    assert model.hourly_forecast([{"timestamp": "x"}, *HOURLY], NOW, 1)[0]["datetime"] == fc[0]["datetime"]


def test_daily_forecast() -> None:
    fc = model.daily_forecast(HOURLY, NOW, BERLIN, 8)
    assert len(fc) == 8
    assert fc[0]["datetime"] == "2026-10-15T00:00:00+02:00", "heute, Mitternacht Ortszeit"
    today = [
        h
        for h in HOURLY
        if model.parse_time(h["timestamp"]).astimezone(BERLIN).date() == NOW.astimezone(BERLIN).date()  # type: ignore[union-attr]
    ]
    assert fc[0]["native_temperature"] == max(h["temperature"] for h in today)
    assert fc[0]["native_templow"] == min(h["temperature"] for h in today)
    assert fc[0]["native_precipitation"] == round(sum(h["precipitation"] for h in today), 1)
    assert fc[0]["precipitation_probability"] == max(
        p for h in today if (p := h["precipitation_probability_6h"]) is not None
    ), "6-Std.-Wahrscheinlichkeit wie beim DWD"
    assert fc[0]["cloud_coverage"] == round(sum(h["cloud_cover"] for h in today) / len(today))
    assert all(f["condition"] in model.ALL_CONDITIONS for f in fc)


def test_day_condition_rain_beats_clouds() -> None:
    def hour(h: int, icon: str, cond: str, rain: float) -> dict[str, object]:
        return {"timestamp": f"2026-10-16T{h:02d}:00:00+00:00", "icon": icon, "condition": cond, "precipitation": rain}

    wet = [hour(h, "cloudy", "dry", 0.0) for h in range(6, 18)] + [hour(14, "rain", "rain", 1.5)]
    assert model.daily_forecast(wet, NOW, UTC, 2)[0]["condition"] == "rainy"
    storm = [*wet, hour(15, "thunderstorm", "thunderstorm", 5.0)]
    assert model.daily_forecast(storm, NOW, UTC, 1)[0]["condition"] == "lightning-rainy"
    dry = [hour(h, "partly-cloudy-night" if h < 7 else "clear-day", "dry", 0.0) for h in range(24)]
    assert model.daily_forecast(dry, NOW, UTC, 1)[0]["condition"] == "sunny"
    assert model.daily_forecast([{"timestamp": "2026-10-16T02:00:00+00:00"}], NOW, UTC, 1)[0]["condition"] is None


def test_daily_forecast_skips_bad_and_past_hours() -> None:
    hours = [
        {"timestamp": None},
        {"timestamp": "2026-10-14T12:00:00+00:00", "temperature": 30.0},
        {"timestamp": "2026-10-16T12:00:00+00:00", "temperature": 10.0},
    ]
    fc = model.daily_forecast(hours, NOW, UTC, 3)
    assert [f["native_temperature"] for f in fc] == [10.0], "gestern und kaputte Stempel fallen weg"
