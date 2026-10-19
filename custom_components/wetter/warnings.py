"""Warnungen aus beiden Quellen in eine Form bringen – reine Funktionen ohne Home Assistant.

- DWD (Bright Sky): Gemeinde-genau, Schweregrad klein geschrieben
- NINA (warnung.bund.de): alle übrigen Anbieter für den Kreis; DWD-Meldungen dort werden übersprungen
  (die Gemeinde-genauen aus Bright Sky gehen vor), „Cancel“-Meldungen gelten als aufgehoben.

Die Felder heißen wie die Attribute der NINA-Integration, damit Dashboards/Automationen sie gleich lesen.
"""

from __future__ import annotations

from datetime import datetime
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
