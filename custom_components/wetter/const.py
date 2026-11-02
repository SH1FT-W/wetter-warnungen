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

# Pollen and UV index of the DWD (Open Data, updated once a day): own coordinator, rarely fetched
CONF_POLLEN_UV: Final = "pollen_uv"
POLLEN_URL: Final = "https://opendata.dwd.de/climate_environment/health/alerts/s31fg.json"
UV_URL: Final = "https://opendata.dwd.de/climate_environment/health/alerts/uvi.json"
# DWD GeoServer (WFS): which pollen region contains the location (asked once, then kept in the store)
POLLEN_REGION_URL: Final = "https://maps.dwd.de/geoserver/dwd/wfs"
# The timer only checks whether new data is due (`next_update` passed); the files are downloaded only then
HEALTH_INTERVAL: Final = timedelta(hours=1)
# Download again at the latest after this, even if `next_update` lies further ahead
HEALTH_MAX_AGE: Final = timedelta(hours=12)
# After `next_update` new files are tried hourly for this long (DWD publishes late now and then), then only
# HEALTH_MAX_AGE applies (file without our region, or no new file at all)
HEALTH_LATE: Final = timedelta(hours=2)
# After a failed download retry hourly this many times, then only HEALTH_MAX_AGE applies (a file gone for good)
HEALTH_RETRIES: Final = 3

# Event names: one event per warning when it appears and when it ends (state in the store, survives restarts)
EVENT_WARNING_NEW: Final = "wetter_warning_new"
EVENT_WARNING_ENDED: Final = "wetter_warning_ended"
# A warning missing from the answers ends only after this long (or at its `expires`): a source that drops it
# for one run, or a NINA message waiting for its area again, must not cause an all-clear plus a second "new"
WARNING_END_GRACE: Final = timedelta(minutes=10)
