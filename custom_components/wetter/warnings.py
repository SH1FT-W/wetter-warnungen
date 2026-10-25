"""Warnungen aus beiden Quellen in eine Form bringen – reine Funktionen ohne Home Assistant.

- DWD (Bright Sky): Gemeinde-genau, Schweregrad klein geschrieben
- NINA (warnung.bund.de): alle übrigen Anbieter für den Kreis; DWD-Meldungen dort werden übersprungen
  (die Gemeinde-genauen aus Bright Sky gehen vor), „Cancel“-Meldungen gelten als aufgehoben.

Die Felder heißen wie die Attribute der NINA-Integration, damit Dashboards/Automationen sie gleich lesen.
"""

from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any

from .model import parse_time

SEVERITIES = ("Extreme", "Severe", "Moderate", "Minor")
_RANK = {s: len(SEVERITIES) - i for i, s in enumerate(SEVERITIES)}
_BR = re.compile(r"\s*<br\s*/?>\s*", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def text(value: Any) -> str | None:
    """HTML-Zeilenumbrüche → Zeilen, übrige Tags weg."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _TAG.sub("", _BR.sub("\n", value)).strip() or None


def severity(value: Any) -> str:
    """Schweregrad wie bei NINA („Minor“ … „Extreme“), Unbekanntes als „Minor“."""
    s = str(value or "").strip().capitalize()
    return s if s in _RANK else "Minor"


_AREA_PREFIX = re.compile(r"^(Gemeinde|Stadt|Samtgemeinde|Markt|Verbandsgemeinde|Kreis|Landkreis)\s+", re.IGNORECASE)


def region(area: Any) -> str | None:
    """Warngebiet kurz zum Anzeigen: „Gemeinde Musterstadt“ → „Musterstadt“."""
    name = str(area or "").strip()
    return _AREA_PREFIX.sub("", name) or None


def ars_from_warn_cell(cell: Any) -> str | None:
    """Kreis-Regionalschlüssel (12 Stellen) aus der DWD-Gemeinde-Warnzelle (8 + 8-stelliger Gemeindeschlüssel)."""
    digits = str(cell or "")
    if len(digits) != 9 or not digits.isdigit() or digits[0] != "8":
        return None
    return digits[1:6] + "0000000"


def from_dwd(alert: dict[str, Any]) -> dict[str, Any]:
    """Bright-Sky-Warnung → einheitliche Warnung."""
    return {
        "id": str(alert.get("alert_id") or alert.get("id")),
        "provider": "DWD",
        "headline": text(alert.get("headline_de") or alert.get("headline_en")),
        "event": alert.get("event_de") or alert.get("event_en"),
        "description": text(alert.get("description_de") or alert.get("description_en")),
        "recommended_actions": text(alert.get("instruction_de") or alert.get("instruction_en")),
        "severity": severity(alert.get("severity")),
        "sender": "Deutscher Wetterdienst",
        "sent": alert.get("effective"),
        "start": alert.get("onset"),
        "expires": alert.get("expires"),
        "more_info_url": None,
    }


def nina_items(dashboard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gültige Nicht-DWD-Meldungen aus dem Kreis-Dashboard (ohne Aufhebungen)."""
    out = []
    for item in dashboard:
        data = ((item.get("payload") or {}).get("data")) or {}
        wid = str(item.get("id") or "")
        if not wid or wid.startswith("dwd.") or str(data.get("provider", "")).upper() == "DWD":
            continue
        if str(data.get("msgType", "")).lower() == "cancel" or data.get("valid") is False:
            continue
        out.append(item)
    return out


def item_key(item: dict[str, Any]) -> str:
    """Kennung inkl. Version – ändert sich bei jeder Aktualisierung der Meldung."""
    payload = item.get("payload") or {}
    return f"{item.get('id')}#{payload.get('version', '')}"


def from_nina(item: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    """NINA-Meldung (Dashboard-Eintrag + Volltext, falls geladen) → einheitliche Warnung."""
    data = ((item.get("payload") or {}).get("data")) or {}
    infos = (detail or {}).get("info") or []
    info = next((i for i in infos if str(i.get("language", "")).lower().startswith("de")), infos[0] if infos else {})
    title = (item.get("i18nTitle") or {}).get("de")
    web = info.get("web")
    return {
        "id": str(item.get("id")),
        "provider": str(data.get("provider") or "NINA").upper(),
        "headline": text(info.get("headline") or title or data.get("headline")),
        "event": info.get("event"),
        "description": text(info.get("description")),
        "recommended_actions": text(info.get("instruction")),
        "severity": severity(info.get("severity") or data.get("severity")),
        "sender": info.get("senderName") or (detail or {}).get("sender"),
        "sent": (detail or {}).get("sent") or item.get("sent"),
        "start": info.get("onset") or info.get("effective"),
        "expires": info.get("expires"),
        "more_info_url": web if isinstance(web, str) and web.startswith("http") else None,
    }


# Rand-Toleranz um die Warnpolygone in Grad (gut 1 km): Zuhause knapp außerhalb zählt noch als betroffen
_EDGE_TOLERANCE = 0.01


def _in_ring(lon: float, lat: float, ring: list[Any], tol: float) -> bool:
    """Punkt im Ring (Strahlverfahren) oder höchstens `tol` Grad (Länge um cos(Breite) gestaucht) von einer Kante."""
    pts = [(float(c[0]), float(c[1])) for c in ring if isinstance(c, (list, tuple)) and len(c) >= 2]
    if len(pts) < 3:
        return False
    inside = False
    kx = math.cos(math.radians(lat))
    j = len(pts) - 1
    for i, (xi, yi) in enumerate(pts):
        xj, yj = pts[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        # Abstand zur Kante i–j in gestauchten Grad
        dx, dy = (xj - xi) * kx, yj - yi
        px, py = (lon - xi) * kx, lat - yi
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy)))
        if math.hypot(px - t * dx, py - t * dy) <= tol:
            return True
        j = i
    return inside


def _polygons(geojson: Any) -> list[list[Any]]:
    """Alle Polygone (je Liste von Ringen) aus einer GeoJSON-FeatureCollection/Feature/Geometry."""
    if not isinstance(geojson, dict):
        return []
    kind = geojson.get("type")
    if kind == "FeatureCollection":
        return [p for f in geojson.get("features") or [] for p in _polygons(f)]
    if kind == "Feature":
        return _polygons(geojson.get("geometry"))
    coords = geojson.get("coordinates")
    if kind == "Polygon" and isinstance(coords, list):
        return [coords]
    if kind == "MultiPolygon" and isinstance(coords, list):
        return [p for p in coords if isinstance(p, list)]
    return []


def covers(geojson: Any, latitude: float, longitude: float) -> bool | None:
    """Liegt das Zuhause im Warngebiet? None, wenn die Geometrie fehlt oder unbrauchbar ist (dann gilt der Kreis).

    NINA liefert Kreis-Meldungen für den ganzen Kreis, auch wenn das Warngebiet (z. B. die Nordseeküste bis in den
    Tidebereich) nur einen Zipfel davon berührt. Das GeoJSON der Meldung zeigt die echten Flächen; Löcher gelten.
    """
    polygons = _polygons(geojson)
    rings = [r for p in polygons for r in p if isinstance(r, list)]
    if not rings:
        return None
    for poly in polygons:
        rings = [r for r in poly if isinstance(r, list)]
        if not rings or not _in_ring(longitude, latitude, rings[0], _EDGE_TOLERANCE):
            continue
        if not any(_in_ring(longitude, latitude, hole, -1.0) for hole in rings[1:]):
            return True
    return False


def drop_expired(warnings: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Warnungen ohne die abgelaufenen (`expires` in der Vergangenheit); ohne oder mit unlesbarem Ende bleiben sie.

    Wichtig, wenn eine Quelle ausfällt: ihre letzten Warnungen bleiben zwar stehen, enden aber trotzdem pünktlich.
    """
    out = []
    for w in warnings:
        expires = parse_time(w.get("expires"))
        if expires is None or expires > now:
            out.append(w)
    return out


def sort(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Schlimmste zuerst, bei gleicher Stufe die neueste."""
    by_sent = sorted(warnings, key=lambda w: str(w.get("sent") or ""), reverse=True)
    return sorted(by_sent, key=lambda w: _RANK.get(str(w.get("severity")), 0), reverse=True)
