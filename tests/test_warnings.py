"""Warnungen ohne Home Assistant: Regionalschlüssel, Text, Stufen, NINA-Filter, Reihenfolge."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.wetter import warnings

from .conftest import ALERT, MINOR, MOWAS_ID, load


def test_ars_from_warn_cell() -> None:
    assert warnings.ars_from_warn_cell(812345678) == "123450000000", "Gemeinde-Zelle 8 + Gemeindeschlüssel → Kreis"
    assert warnings.ars_from_warn_cell("812345678") == "123450000000"
    assert warnings.ars_from_warn_cell(None) is None
    assert warnings.ars_from_warn_cell(112345678) is None, "keine Gemeinde-Zelle"
    assert warnings.ars_from_warn_cell("8123456") is None


def test_text_and_severity() -> None:
    assert warnings.text("A<br/>B <BR> C<b>!</b>") == "A\nB\nC!"
    assert warnings.text("   ") is None and warnings.text(None) is None
    assert warnings.severity("moderate") == "Moderate"
    assert warnings.severity("unknown") == "Minor"


def test_dwd() -> None:
    w = warnings.from_dwd(ALERT)
    assert w["provider"] == "DWD" and w["severity"] == "Moderate"
    assert w["headline"] == ALERT["headline_de"] and w["start"] == ALERT["onset"]
    assert w["recommended_actions"] is None


def test_nina_filter_and_detail() -> None:
    dashboard = load("nina_dashboard.json")
    items = warnings.nina_items(dashboard)
    assert [i["id"] for i in items] == [MOWAS_ID], "DWD-Doppel und Aufhebung raus"
    assert warnings.item_key(items[0]).startswith(MOWAS_ID + "#")
    w = warnings.from_nina(items[0], load("nina_warning.json"))
    assert w["provider"] == "MOWAS" and w["severity"] == "Minor"
    assert w["description"] and "<br" not in w["description"]
    bare = warnings.from_nina(items[0], None)
    assert bare["headline"].startswith("1. AKTUALISIERUNG") and bare["description"] is None
    assert warnings.nina_items([{"id": ""}, {"id": "x", "payload": {"data": {"valid": False}}}]) == []


def test_sort_worst_first() -> None:
    a = warnings.from_dwd(MINOR) | {"sent": "2026-10-15T08:00:00+02:00"}
    b = warnings.from_dwd(ALERT) | {"sent": "2026-10-15T07:00:00+02:00"}
    c = warnings.from_dwd(MINOR) | {"sent": "2026-10-15T09:00:00+02:00", "id": "neu"}
    assert [w["id"] for w in warnings.sort([a, b, c])] == [b["id"], "neu", a["id"]]


def test_drop_expired() -> None:
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    gone = warnings.from_dwd(ALERT), "endet 20:00+02:00 = 18:00 UTC, also jetzt abgelaufen"
    still = warnings.from_dwd({**ALERT, "expires": "2026-10-15T20:01:00+02:00", "alert_id": "noch"})
    open_end = warnings.from_dwd({**ALERT, "expires": None, "alert_id": "ohne"})
    broken = warnings.from_dwd({**ALERT, "expires": "kaputt", "alert_id": "kaputt"})
    kept = warnings.drop_expired([gone[0], still, open_end, broken], now)
    assert [w["id"] for w in kept] == ["noch", "ohne", "kaputt"], gone[1]


def test_region() -> None:
    assert warnings.region("Gemeinde Musterstadt") == "Musterstadt"
    assert warnings.region("Stadt Musterhausen") == "Musterhausen"
    assert warnings.region("Musterstadt (Muster)") == "Musterstadt (Muster)"
    assert warnings.region(None) is None and warnings.region("  ") is None


def test_covers_point_in_polygon() -> None:
    geo = load("nina_warning.geojson")
    assert warnings.covers(geo, 52.52, 13.41) is True
    assert warnings.covers(geo, 53.60, 7.20) is False, "anderer Zipfel des Kreises"
    assert warnings.covers(geo, 52.605, 13.41) is True, "knapp außerhalb (<1 km) zählt noch"
    assert warnings.covers(geo, 52.70, 13.41) is False
    # ohne brauchbare Geometrie: unbekannt → Kreis-Meldung bleibt
    assert warnings.covers(None, 52.52, 13.41) is None
    assert warnings.covers({"type": "FeatureCollection", "features": []}, 52.52, 13.41) is None
    point = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [13.41, 52.52]}}
    assert warnings.covers(point, 52.52, 13.41) is None
    broken = {"type": "Polygon", "coordinates": [[[1, 1], [2, 2]]]}
    assert warnings.covers(broken, 52.52, 13.41) is False, "kaputter Ring"


def test_covers_multipolygon_with_hole() -> None:
    outer = [[13.0, 52.0], [14.0, 52.0], [14.0, 53.0], [13.0, 53.0], [13.0, 52.0]]
    hole = [[13.3, 52.3], [13.7, 52.3], [13.7, 52.7], [13.3, 52.7], [13.3, 52.3]]
    second = [[[8.0, 50.0], [8.1, 50.0], [8.1, 50.1], [8.0, 50.1], [8.0, 50.0]]]
    geo = {"type": "MultiPolygon", "coordinates": [[outer, hole], second]}
    assert warnings.covers(geo, 52.1, 13.1) is True
    assert warnings.covers(geo, 52.5, 13.5) is False, "im Loch"
    assert warnings.covers(geo, 50.05, 8.05) is True, "zweites Polygon"
