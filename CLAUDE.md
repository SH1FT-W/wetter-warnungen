# Wetter & Warnungen: Projektregeln

HA-Integration „Wetter & Warnungen“ (`custom_components/wetter`, Domain `wetter`, Repo SH1FT-W/wetter-warnungen,
Projektseite https://sh1ft-w.github.io/wetter-warnungen/). Ersetzt `dwd_weather` (15 s Start, MOSMIX-S-Riesendatei)
und NINA. **Domain und Ordner bleiben `wetter`** (bestehende Installationen); nur der Anzeigename heißt so.

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten Koordinaten, Gemeinden, Warnzellen,
  Stationen, Entity-IDs aus echten Installationen, Heimnetz-Details, lokalen Pfade). Testdaten bleiben fiktiv.
- **Alles auf Deutsch** (nur für Deutschland): README, Projektseite, README-Karten, Titelbild, CHANGELOG, Commit-Texte.
  GitHub-Release-Notizen englisch (globale Regel). Keine Gedankenstriche als Satzzeichen in GitHub-Texten.
- Lokal committen ok, Push und Releases nur nach Rückfrage.
- Echte Installationen (Prod) nur auf ausdrücklichen Wunsch des Besitzers anfassen, nie selbst hineinkopieren.
- Datenquellen und Lizenzen in `NOTICE` (DWD-Daten CC BY 4.0 mit Quellenvermerk „Deutscher Wetterdienst“, über
  Bright Sky; Warn-Schnittstelle des BBK). Die Attribution der Entitäten bleibt.

**Versionen = Kalender-Versionen `YY.M.N`** (ab dem Release nach 1.3.4): Jahr zweistellig, Monat ohne führende Null,
N = laufende Nummer im Monat ab 1 – es zählen nur echte, veröffentlichte GitHub-Releases des Monats (auch alte X.Y.Z,
bloße Tags nicht), jedes Release also als GitHub-Release veröffentlichen. Nummer: `tools/next-version` (neuer Monat wieder `.1`). Kein
Major/Minor/Patch mehr, nie führende Nullen. Commit-Text `<version>: <was>`, Tag + Release-Titel ohne „v“ = genau die
manifest-Version. HA/HACS vergleichen per AwesomeVersion: `26.10.x` gilt als neuer als `1.3.4`.

## Aufbau
- `api.py` Bright-Sky-Client ohne HA (`/current_weather`, `/weather`, `/alerts`, kein Schlüssel).
- `model.py` reine Auswertung: aktuelle Werte (Station, Lücken aus der Vorhersage der laufenden Stunde),
  Vorhersage stündlich/täglich (Tag = Ortszeit, Symbol: tagsüber ≥1 mm → schlimmste Niederschlagsart, sonst häufigstes).
- `coordinator.py` alle 10 Min., Stand im Store → Start ohne Warten (`async_restore`, Abfrage nach `homeassistant_started`).
  Nachbarschaftswerte (Optionen) gehen vor, solange ≤1 Std. alt; Regen vor Ort korrigiert das Symbol.
- Netatmo-Bereich: nur Temperatur/Feuchte/Regen sinnvoll (Druck nicht auf Meereshöhe, Wind zu geschützt).
- Warnungen (`warnings.py` rein, `WarningsCoordinator` alle 2 Min.): DWD Gemeinde-genau aus Bright Sky + alle übrigen
  Anbieter (MoWaS, KATWARN, BIWAPP, LHP, Polizei) für den Kreis aus NINA (`warnung.bund.de/api31`, Kreis-ARS aus der
  DWD-Warnzelle 8+Gemeindeschlüssel). NINA-DWD-Meldungen und „Cancel“ werden übersprungen. Fällt eine Quelle aus,
  bleiben ihre letzten Warnungen (keine falsche Entwarnung), Warn-Entitäten bleiben immer verfügbar.
  `binary_sensor.<gerät>_warnung_1..5` mit denselben Attributen wie die NINA-Integration (Casora/Automationen).
- Bright-Sky-Zeitstempel = Ende der Stunde; `solar` kWh/m² der Stunde → ×1000 = W/m².
- `number.py` Regler „Nachbarschaft Radius“ nur, wenn beim Laden ein Netatmo-Bereich hinter den Nachbarschaftssensoren
  steht (`coordinator.neighbourhood()`); sonst kein Regler und alter Registry-Eintrag wird entfernt. Schreibt in die
  Optionen der Netatmo-Integration (README erklärt das).

## Prüfen
- `~/Software/evenlight/.venv/bin/python -m pytest -q -p no:logging --cov=custom_components` (100 %; HA 2026.9.3);
  CI-gleich: `uv venv --python 3.14 && uv pip install -r requirements_test.txt pytest-cov`
- `uvx ruff check . && uvx ruff format --check .`, `uvx mypy@2.3.1 --python-executable ~/Software/evenlight/.venv/bin/python`
- `python tools/pruefe-uebersetzungen.py` (strings.json = translations/*.json), hassfest per Docker
- GitHub-Workflow `.github/workflows/pruefung.yml` (Vorlage repo-vorlagen/vorlagen/ci-ha-integration.yml, COV_MIN 98)
  läuft bei Push/PR und montags gegen die neueste HA.
- Tests enthalten keine echten Heimdaten: Koordinaten, Gemeinde, Warnzelle und Stationen in `tests/` sind fiktiv
  (Musterstadt, Teststation). So bleibt es.
- Jede Änderung in `CHANGELOG.md` unter „Unveröffentlicht“ eintragen, beim Release in die Versionsüberschrift.
- GitHub Actions: `.github/workflows/pruefung.yml` und `.github/workflows/hacs.yml` (HACS-Prüfung, läuft nur im
  öffentlichen Repo).
- README-Bilder: Karten aus `docs/readme-images.json`, gerendert mit `theme/v1/readme-images/render.mjs <repo> cards`
  aus sh1ft-w.github.io. Titelbild `docs/images/readme-title.webp` (ein Bild für hell und dunkel) aus
  `tools/readme-title.html` (selbst gezeichnet, Werte ausgedacht; Rendern siehe Kommentar dort). Die Projektseite
  `docs/index.html` (nur Deutsch) zeigt dasselbe Bild im Hero, Versionszeile liest die manifest.json.
