# Changelog

## Unveröffentlicht

- **Regler „Nachbarschaft Radius“ entfernt.** Er schrieb den Netatmo-Wetterbereich in die Optionen der Netatmo-Integration; deren Config-Eintrag gehört aber Netatmo (Prüfung für den Marketplace, hacs/default#13248). Die Größe des Bereichs stellst du jetzt im Optionsdialog von Netatmo ein. Die Nachbarschaftssensoren (Quelle, Fläche, Abstand) bleiben; Netatmo wird nur noch gelesen. Der alte Regler wird beim Start aus der Entitätenliste entfernt.

## 26.11.2 (02.11.2026)

- **Warnungen höchster Stufe sofort:** Eine neue NINA-Meldung der höchsten Stufe (*Extreme*, etwa MoWaS bei Lebensgefahr) erscheint ohne Wartezeit für den ganzen Kreis, wenn sich ihr Warngebiet nicht laden lässt. Bisher wartete auch sie bis zu 5 Minuten auf das Warngebiet. Die Stufe kommt aus dem Volltext, sonst aus der Kreis-Übersicht. Niedrigere Stufen warten wie bisher; trifft das später geladene Warngebiet das Zuhause nicht, verschwindet die Meldung wieder.

## 26.11.1 (02.11.2026)

- **Pollenflug und UV-Index:** Neue Sensoren *Pollen Ambrosia, Beifuß, Birke, Erle, Esche, Gräser, Hasel, Roggen* (Pollenflug-Gefahrenindex des DWD, 0 bis 3 in halben Stufen, Attribute `today`, `tomorrow`, `day_after_tomorrow`, `region`) und *UV-Index*, *UV-Index morgen* (nächster der 38 DWD-Orte ohne Bergstationen, Attribut `city`). Die Pollenregion ergibt sich einmalig aus dem Ort (Geodienst des DWD) und bleibt gespeichert. Eigener Takt: stündliche Prüfung, Download nur, wenn neue Dateien fällig sind; um Mitternacht rückt morgen auf heute. Fehler und leere Antworten lassen die letzten Werte stehen und stören Wetter und Warnungen nicht; ein Ort ohne Pollenregion wird gemerkt und nicht stündlich neu gefragt. „Heute“ ist der deutsche Kalendertag der DWD-Dateien. In den Optionen abschaltbar (Entitäten werden dann entfernt).
- **Regen in X Minuten:** Sensor *Regen in* (Minuten bis zum nächsten Niederschlag aus der stündlichen Vorhersage, 0 bei gemessenem Regen vor Ort oder an der Station) und *Regen in der nächsten Stunde*. Auflösung eine Stunde, Regen gilt ab Beginn der ersten Stunde mit mindestens 0,1 mm; ohne Regen in 48 Stunden unbekannt. Grenzen im README.
- **Ereignisse:** `wetter_warning_new` und `wetter_warning_ended` mit `id`, `source` (DWD/NINA), `provider`, `severity`, `headline`, `event`, `area`, `start`, `expires`, je Warnung genau einmal. Der Stand liegt im Speicher und wird sofort geschrieben, ein Neustart wiederholt nichts. Die Ereignisse kommen, nachdem die Entitäten den neuen Stand zeigen. Das Ende kommt mit dem Ablauf der Warnung oder, wenn sie aus den Antworten verschwindet, erst nach 10 Minuten ohne sie: Fehlt sie nur kurz, gibt es weder Ende noch ein zweites Neu. Nach dem Update gelten die schon angezeigten Warnungen als gemeldet (kein zweiter Push).
- **Warnungen:** Neues Attribut `affected_areas` (wie bei der NINA-Integration): bei DWD die Gemeinde, bei NINA das Gebiet aus der Meldung.
- **Optionen:** Titel jetzt „Optionen“, neuer Schalter *Pollenflug und UV-Index*.
- Entwicklung: Snapshot-Test über alle Entitäten (unique_id, device_class, state_class, Einheit, Zustand, Attribute), eigenes `.venv` im Repo.

- **Fix Warnungen:** Ließ sich das Warngebiet (GeoJSON) einer NINA-Meldung nicht laden, etwa nach einer Aktualisierung der Meldung oder nach einem Neustart, galt sie für einen Durchlauf im ganzen Kreis: Eine Meldung für einen anderen Teil des Kreises (z. B. Sturmflut an der Küste) tauchte kurz auf, samt Push und Entwarnung. Jetzt bleibt bei unbekanntem Warngebiet die letzte Entscheidung für diese Meldung stehen, auch über Neustarts. Eine ganz neue Meldung ohne ladbares Warngebiet wird bis zu 5 Minuten zurückgehalten (neue Versuche alle 2 Minuten) und gilt danach für den ganzen Kreis, lieber eine Warnung zu viel als eine verschwiegene. Ein geladenes Warngebiet ohne Flächen gilt wie bisher sofort für den Kreis. Fehlt eine Meldung einmal in der Kreis-Übersicht von NINA, bleibt ihre Entscheidung 10 Minuten erhalten; vorher fing sie bei ihrer Rückkehr neu an und galt bei nicht ladbarem Warngebiet nach 5 Minuten doch für den ganzen Kreis.

## 26.10.10 (25.10.2026)

- **Gefühlte Temperatur:** Neuer Sensor und Wert in der Wetter-Entität (`apparent_temperature`), auch in der stündlichen Vorhersage. Gerechnet nach der Steadman-Formel (wie Open-Meteo) aus Temperatur, Luftfeuchte und Wind; Nachbarschaftswerte zählen mit. Der DWD liefert den Wert nicht selbst.

## 26.10.9 (25.10.2026)

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
