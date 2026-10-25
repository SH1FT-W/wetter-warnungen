# Changelog

## Unveröffentlicht

- **Warnungen nur, wenn sie das Zuhause treffen:** Kreis-Meldungen aus NINA (Hochwasser, MoWaS, KATWARN …) werden gegen ihr echtes Warngebiet (GeoJSON der Meldung) geprüft. Berührt das Gebiet nur einen anderen Zipfel des Kreises, etwa eine Sturmflutwarnung für die Küste, die im Tidebereich noch den Kreis streift, erscheint sie nicht mehr. Gut 1 km Rand zählt noch als betroffen. Ohne ladbare Geometrie gilt wie bisher der ganze Kreis.
## 26.10.8 (19.10.2026)

- **Öffentlich als „Wetter & Warnungen“** (Repo SH1FT-W/wetter-warnungen): Lizenz Apache 2.0, `NOTICE` mit den Datenquellen (DWD, CC BY 4.0, über Bright Sky; Warn-Schnittstelle des Bundes), neue README und Projektseite, HACS-Prüfung. Domain `wetter` und Ordner bleiben, bestehende Installationen laufen weiter.
- **Nachbarschaft Radius:** Den Regler gibt es nur noch, wenn ein Netatmo-Wetterbereich hinter den Nachbarschaftssensoren steht; ohne Bereich entfällt der dauerhaft „nicht verfügbare“ Regler (ein alter Eintrag wird aufgeräumt).
- **Datenschutz Tests:** Die NINA-Testmeldung ist jetzt ausgedacht (Musterstadt statt einer echten Meldung).

## 26.10.7 (19.10.2026)

- **Nachbarschaftswerte:** Meldet ein Sensor „nan“ oder „inf“, gilt der DWD-Wert statt eines ungültigen Messwerts.
- **Vorhersage:** Wird nur noch bei neuen Daten an Karten und Abonnenten geschickt, nicht bei jedem neuen Nachbarschaftswert.
- **Warnungen:** Fehlt der Volltext einer NINA-Meldung, steht das im Debug-Log (vorher still verworfen); neuer Versuch beim nächsten Durchlauf wie bisher.
- **Datenschutz Tests:** Koordinaten, Gemeinde, Warnzelle, Regionalschlüssel und Stationen in den Tests sind jetzt fiktiv (Musterstadt, Teststation).
- **Diagnose:** Warngebiet wird wie die Koordinaten geschwärzt; neu dabei `warn_stale` (ausgefallene Quellen).
- **Warnungen:** Abgelaufene Warnungen (`expires`) fallen weg, auch wenn ihre Quelle gerade nicht erreichbar ist; Sensor „Warnungen“ hat das Attribut `stale` (Quellen, deren letzte Warnungen noch stehen).
- **Speicher:** Stand wird nur geschrieben, wenn sich Messung, Vorhersage oder Warnungen geändert haben.
- **Werte:** Die zusammengeführten aktuellen Werte werden je Aktualisierung einmal gerechnet; alle Entitäten lesen denselben Stand.
- **Symbol:** Trocken vor Ort bei Gewitter zeigt „Gewitter“ (ohne Regen); Schnee, Schneeregen und Hagel bleiben, weil ein Regenmesser sie nicht zuverlässig erfasst.
- **Fix:** 0 % Luftfeuchte aus der Vorhersage galt als fehlend und wurde aus dem Taupunkt gerechnet.
- Entwicklung: `requirements_test.txt`, `tools/pruefe-uebersetzungen.py`, GitHub-Workflow „Prüfung“ (ruff, mypy, Übersetzungen, pytest mit Abdeckungsgrenze, hassfest, montags neueste HA), Vorlagen-Reste aus ruff/mypy/pytest-Konfiguration entfernt, dieses CHANGELOG.

## 26.10.6 (16.10.2026)

- **Neue Versionsnummer:** Ab jetzt zählt die Version nach **Jahr.Monat.Nummer des Releases im Monat**: `26.10.6` ist das sechste Release im Oktober 2026, im nächsten Monat geht es wieder bei `.1` los. Der Sprung von `1.3.4` auf `26.10.6` ist gewollt, Privat-Updates und Home Assistant erkennen `26.10.6` als neuer. Sonst keine Änderungen.

## 1.3.4 (15.10.2026)

- Sensoren **Regenwahrscheinlichkeit** (laufende Stunde) und **Regenwahrscheinlichkeit heute** (6-Std.-Wahrscheinlichkeit wie in der Tagesvorhersage)

## 1.3.3 (15.10.2026)

- Regler **Nachbarschaft Radius** (1 bis 20 km): legt den Netatmo-Wetterbereich als Quadrat um den Ort neu an; ohne Netatmo-Bereich nicht verfügbar

## 1.3.2 (15.10.2026)

- Diagnose-Sensoren **Nachbarschaftsbereich** (Name, Breite, Höhe), **Nachbarschaft Fläche** und **Nachbarschaft Abstand**

## 1.3.1 (15.10.2026)

- Erstes Update über Privat-Updates, keine Funktionsänderung
- Seit 1.3.0: Netatmo-Wetterbereich in der Nähe wird beim Einrichten automatisch als Nachbarschaftswerte eingetragen und in leeren Optionen vorgeschlagen

## 1.2.1 (15.10.2026)

Erste veröffentlichte Version, ersetzt die DWD-Integration und NINA.

- DWD-Vorhersage (MOSMIX) über Bright Sky: stündlich 48 Std., täglich 8 Tage; aktuelle Werte der nächsten DWD-Station, Nachbarschaftswerte mit Rückfall auf DWD
- Sensoren wie bei der DWD-Integration, dazu Sonneneinstrahlung und Diagnose-Sensor „Quelle“
- Alle amtlichen Warnungen (DWD für die Gemeinde, MoWaS/KATWARN/BIWAPP/Hochwasser/Polizei für den Kreis), Warn-Sensoren 1 bis 5 wie NINA plus `region`; letzte Warnungen bleiben bei Ausfällen stehen
- Platin-Runde: Ort neu konfigurieren, Erklärtexte, Speicher beim Entfernen löschen, quality_scale.yaml, 100 % Testabdeckung
