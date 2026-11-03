# Wetter & Warnungen: Projektregeln

HA-Integration „Wetter & Warnungen“ (`custom_components/wetter`, Domain `wetter`, Repo SH1FT-W/wetter-warnungen,
Projektseite https://sh1ft-w.github.io/wetter-warnungen/). Ersetzt `dwd_weather` (15 s Start, MOSMIX-S-Riesendatei)
und NINA. **Domain und Ordner bleiben `wetter`** (bestehende Installationen); nur der Anzeigename heißt so.

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten Koordinaten, Gemeinden, Warnzellen,
  Stationen, Entity-IDs aus echten Installationen, Heimnetz-Details, lokalen Pfade). Testdaten bleiben fiktiv
  (Musterstadt, Teststation).
- **Alles auf Deutsch** (nur für Deutschland): README, Projektseite, README-Karten, Titelbild, CHANGELOG, Commit-Texte.
  GitHub-Release-Notizen englisch (globale Regel). Keine Gedankenstriche als Satzzeichen in GitHub-Texten.
- Lokal committen ok, Push und Releases nur nach Rückfrage.
- Echte Installationen (Prod) nur auf ausdrücklichen Wunsch des Besitzers anfassen, nie selbst hineinkopieren.
- Datenquellen und Lizenzen in `NOTICE` (DWD-Daten CC BY 4.0 mit Quellenvermerk „Deutscher Wetterdienst“, über
  Bright Sky; Warn-Schnittstelle des BBK). Die Attribution der Entitäten bleibt.

**Versionen** über `tools/next-version` (globale Regeln). Commit-Text `<version>: <was>`. HA/HACS vergleichen per
AwesomeVersion: `26.10.x` gilt als neuer als das alte `1.3.4`. Jede Änderung in `CHANGELOG.md` unter
„Unveröffentlicht“ eintragen, beim Release in die Versionsüberschrift.

## Aufbau
- `api.py` Bright-Sky-Client ohne HA, `model.py` reine Auswertung (aktuell, stündlich/täglich, „Regen in“),
  `coordinator.py` Wetter + `WarningsCoordinator` + `HealthCoordinator`, `warnings.py` (DWD + NINA) und
  `health.py` (Pollen/UV von opendata.dwd.de) rein, `netatmo.py` Nachbarschaftswerte, `number.py` Radius-Regler.

## Warum es so ist (steht nicht im Code)
- Start ohne Warten: Stand aus dem Store, erste Abfrage erst nach `homeassistant_started`; Pollen/UV nie blockierend.
- Netatmo-Bereich: nur Temperatur/Feuchte/Regen sinnvoll (Druck nicht auf Meereshöhe, Wind zu geschützt).
- Warnungen: DWD Gemeinde-genau aus Bright Sky, darum NINA-DWD-Meldungen übersprungen. NINA-Kreis-Meldungen nur, wenn
  ihr Warngebiet die Heimkoordinaten trifft. Fällt eine Quelle aus, bleiben ihre letzten Warnungen (keine falsche
  Entwarnung), Warn-Entitäten bleiben immer verfügbar.
- `binary_sensor.<gerät>_warnung_1..5` mit denselben Attributen wie die NINA-Integration (Casora/Automationen).
- Ereignisse `wetter_warning_new`/`wetter_warning_ended` je Warnungs-ID genau einmal, auch über Neustarts.
- Bright-Sky-Zeitstempel = Ende der Stunde; `solar` kWh/m² der Stunde → ×1000 = W/m².
- „Regen in“ = 0 nur bei gemessenem Regen der letzten Stunde (Nachbarschaft/Station), nie aus der Vorhersage.
- Der Regler „Nachbarschaft Radius“ schreibt in die Optionen der Netatmo-Integration (README erklärt das); ohne
  Netatmo-Bereich kein Regler.

## Prüfen
- Eigenes venv im Repo (`.venv`, in .gitignore), anlegen CI-gleich: `uv venv --python 3.14 && uv pip install -r requirements_test.txt pytest-cov`
- `LC_ALL=en_US TZ=UTC .venv/bin/python -m pytest -q -p no:logging --cov=custom_components` (100 %; HA 2026.9.3)
- `uvx ruff@0.16.9 check . && uvx ruff@0.16.9 format --check .` (Version wie in pruefung.yml),
  `uvx mypy@2.3.1 --python-executable .venv/bin/python`
- Snapshot aller Entitäten `tests/test_snapshot.py` (Stand `tests/snapshots/`): gewollte Änderungen mit
  `--snapshot-update` übernehmen und den Unterschied ansehen.
- `python tools/pruefe-uebersetzungen.py` (strings.json = translations/*.json), hassfest per Docker
- GitHub Actions: `.github/workflows/pruefung.yml` (Vorlage repo-vorlagen/vorlagen/ci-ha-integration.yml, COV_MIN 98)
  bei Push/PR und montags gegen die neueste HA; `.github/workflows/hacs.yml` (HACS-Prüfung, läuft nur im
  öffentlichen Repo).
- README-Bilder: Titelbild und Karten aus `docs/readme-images.json` mit `theme/v1/readme-images/render.mjs <repo>`
  aus sh1ft-w.github.io: Titel als `docs/images/readme-title-{light,dark}.webp` (2560 × 1280, Fenster zeigt
  `docs/images/title-shot-*.png`, den Funktionsblock der Projektseite mit 2× aufgenommen) plus `readme-title-social.png`
  für die GitHub-Vorschau; die Karten stehen als Markdown-Tabellen zwischen `<!-- cards:… -->` im README, Icons in
  `docs/images/icons/`. Die Projektseite `docs/index.html` (nur Deutsch) zeigt das Titelbild im Hero, Versionszeile liest die manifest.json.
