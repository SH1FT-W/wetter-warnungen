# HA-Annahmen

<!-- Vorlage aus repo-vorlagen/vorlagen/ha-annahmen.md. Der HA-Beta-Bericht (repo-vorlagen/ha-beta-bericht.md)
     gleicht diese Liste jeden Monat mit den Neuerungen der HA-Beta ab. Nur festhalten, was bei einer Änderung
     in HA still kaputtginge. -->

Worauf sich dieses Repo bei Home Assistant verlässt. Bei jeder Änderung am Code mitpflegen.

**Getestet mit:** HA 2026.9.3 (Tests) · **Mindestversion:** siehe `hacs.json` (2026.9.0)

## Annahmen

| Annahme | Wo im Code | Abgesichert durch |
|---|---|---|
| `ConfigEntry[...]` als Generic mit `runtime_data` | `coordinator.py` `WetterConfigEntry`, `__init__.py` | `tests/test_init.py::test_entities` |
| `async_at_started` feuert nach `homeassistant_started` auch bei spätem Laden sofort | `__init__.py` `async_setup_entry` | `tests/test_init.py::test_start_from_saved_state` |
| `Store(hass, 1, key)` mit `async_delay_save`, Schlüssel frei wählbar | `coordinator.py` beide Coordinators | `tests/test_init.py::test_start_from_saved_state` |
| `UpdateFailed(translation_domain, translation_key, translation_placeholders)` übersetzt den Fehler | `coordinator.py` `_async_update_data` | `tests/test_init.py` Ausfall-Tests |
| `WeatherEntityFeature.FORECAST_DAILY/HOURLY` + `async_forecast_*` liefern `weather.get_forecasts` | `weather.py` | `tests/test_init.py::test_forecasts` |
| `BinarySensorDeviceClass.SAFETY`: An = Problem (Warnung), aus = sicher | `binary_sensor.py` | `tests/test_init.py` Warnungs-Tests |
| `async_redact_data` schwärzt genannte Schlüssel in der Diagnose | `diagnostics.py` | `tests/test_init.py::test_diagnostics_hide_location` |
| `LocationSelector` liefert `{latitude, longitude}` unter `CONF_LOCATION`; `start_reconfigure_flow` + `async_update_reload_and_abort` | `config_flow.py` | `tests/test_init.py::test_reconfigure` |
| Entity-Registry: `platform`, `config_entry_id`, `unique_id`, `disabled_by`, `async_entries_for_config_entry` | `netatmo.py` `sensors_for`, `area_info` | `tests/test_netatmo.py` |

## Fremde Integrationen

Integrationen, deren Entitäten oder Attribute dieses Repo liest (Domain, was genau, wo):

- `netatmo` (Kern): Optionen `weather_areas` (`lat_ne/lon_ne/lat_sw/lon_sw`, Modus) des Config-Eintrags; Sensoren
  mit `unique_id` `<Bereich>-temperature`, `-humidity`, `-rain`. Gelesen und geschrieben in `netatmo.py`
  (`sensors_for`, `area_info`, `set_radius`). Test `tests/test_netatmo.py::test_setup_fills_options`, `::test_radius_slider`.
- Beliebige `sensor`-Entitäten aus den Optionen (Temperatur, Feuchte, Regen): nur `state` und `last_updated`,
  keine integrationsspezifischen Attribute.

## Prüfbefehl

```sh
~/Software/evenlight/.venv/bin/python -m pytest -q -p no:logging
```
