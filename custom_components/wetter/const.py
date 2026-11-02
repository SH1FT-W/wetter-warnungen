"""Konstanten für Wetter."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "wetter"

# Bright Sky: freie JSON-Schnittstelle auf den Open Data des DWD (MOSMIX-Vorhersage, SYNOP-Messungen, Warnungen)
API_URL: Final = "https://api.brightsky.dev"
UPDATE_INTERVAL: Final = timedelta(minutes=10)

# Warnungen des Bundes (NINA): DWD, Bevölkerungsschutz (MoWaS), KATWARN, BIWAPP, Hochwasser, Polizei
NINA_URL: Final = "https://warnung.bund.de/api31"
WARNINGS_INTERVAL: Final = timedelta(minutes=2)
# A new district message whose area (GeoJSON) cannot be loaded is held back this long (a few retries), then it
# counts for the whole district: better a warning too many than a concealed one.
NINA_AREA_WAIT: Final = timedelta(minutes=5)
# so viele Warn-Sensoren (wie die Plätze bei NINA)
WARNING_SLOTS: Final = 5

FORECAST_DAYS: Final = 8
FORECAST_HOURS: Final = 48

# Werte aus der Nachbarschaft (z. B. Netatmo-Wetterbereich) – gehen vor den DWD-Messwerten, solange sie frisch sind
CONF_LOCAL_TEMPERATURE: Final = "local_temperature"
CONF_LOCAL_HUMIDITY: Final = "local_humidity"
CONF_LOCAL_PRECIPITATION: Final = "local_precipitation"
CONF_LOCAL_PRESSURE: Final = "local_pressure"
CONF_LOCAL_WIND_SPEED: Final = "local_wind_speed"
CONF_LOCAL_WIND_GUST: Final = "local_wind_gust"
LOCAL_KEYS: Final = {
    CONF_LOCAL_TEMPERATURE: "temperature",
    CONF_LOCAL_HUMIDITY: "humidity",
    CONF_LOCAL_PRECIPITATION: "precipitation",
    CONF_LOCAL_PRESSURE: "pressure",
    CONF_LOCAL_WIND_SPEED: "wind_speed",
    CONF_LOCAL_WIND_GUST: "wind_gust",
}
# älter als das gilt ein Nachbarschaftswert als ausgefallen
LOCAL_MAX_AGE: Final = timedelta(hours=1)
