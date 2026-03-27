"""Pikud HaOref — Israel Home Front Command real-time alert ingestor

Polls the official Israeli rocket/missile/drone/earthquake alert API.
Fires events for active alerts (rockets, missiles, UAVs, infiltrations).

Notes:
- This endpoint may be geo-blocked to Israeli IPs in some environments.
- If geo-blocked, all polls return empty quietly — no crash.
- Update frequency: every 10-15 seconds during active events.

Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import httpx

from config import settings as C

# Unofficial but widely used endpoint (mirrors official oref.org.il API)
_OREF_URL     = "https://www.oref.org.il/WarningMessages/alert/alerts.json"
_OREF_HISTORY = "https://www.oref.org.il/WarningMessages/History/AlertsHistory.json"

_HEADERS = {
    "Referer":    "https://www.oref.org.il/",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (compatible; Wardar/0.1)",
}

# Alert category IDs → human-readable labels + threat level
_ALERT_CATEGORIES = {
    1:  ("Rocket/Missile Attack",   "attack"),
    2:  ("Hostile UAV Intrusion",   "attack"),
    3:  ("Earthquake",              "hazard"),
    4:  ("Radiological/CBRN",       "attack"),
    6:  ("Tsunami Warning",         "hazard"),
    7:  ("Unconventional Weapons",  "attack"),
    13: ("Hostile Aircraft",        "attack"),
    101:("Infiltration Alert",      "attack"),
    102:("Situational Awareness",   "intel"),
}

# Hebrew/transliterated city name → (lat, lon, country)
# Key conflict-zone cities and major population centres
_CITY_COORDS: dict[str, tuple[float, float]] = {
    # South Israel / Gaza border
    "שדרות": (31.524, 34.597),       "Sderot": (31.524, 34.597),
    "אשקלון": (31.669, 34.571),      "Ashkelon": (31.669, 34.571),
    "אשדוד": (31.804, 34.655),       "Ashdod": (31.804, 34.655),
    "נתיבות": (31.421, 34.594),      "Netivot": (31.421, 34.594),
    "אופקים": (31.311, 34.621),      "Ofakim": (31.311, 34.621),
    "באר שבע": (31.252, 34.791),     "Beer Sheva": (31.252, 34.791),
    "קריית גת": (31.610, 34.763),    "Kiryat Gat": (31.610, 34.763),
    "רהט": (31.393, 34.754),         "Rahat": (31.393, 34.754),
    # North Israel / Hezbollah border
    "חיפה": (32.794, 34.989),        "Haifa": (32.794, 34.989),
    "נהריה": (33.005, 35.097),       "Nahariya": (33.005, 35.097),
    "קריית שמונה": (33.207, 35.571), "Kiryat Shmona": (33.207, 35.571),
    "צפת": (32.964, 35.497),         "Safed": (32.964, 35.497),
    "עכו": (32.924, 35.082),         "Acre": (32.924, 35.082),
    "נצרת": (32.700, 35.304),        "Nazareth": (32.700, 35.304),
    "טבריה": (32.794, 35.530),       "Tiberias": (32.794, 35.530),
    # Central Israel
    "תל אביב": (32.0853, 34.7818),   "Tel Aviv": (32.0853, 34.7818),
    "ירושלים": (31.7683, 35.2137),   "Jerusalem": (31.7683, 35.2137),
    "ראשון לציון": (31.964, 34.804), "Rishon LeZion": (31.964, 34.804),
    "פתח תקוה": (32.087, 34.888),    "Petah Tikva": (32.087, 34.888),
    "נתניה": (32.332, 34.860),       "Netanya": (32.332, 34.860),
    "הרצליה": (32.166, 34.843),      "Herzliya": (32.166, 34.843),
    "רמת גן": (32.068, 34.824),      "Ramat Gan": (32.068, 34.824),
    "גבעתיים": (32.073, 34.812),     "Givatayim": (32.073, 34.812),
    "בני ברק": (32.084, 34.834),     "Bnei Brak": (32.084, 34.834),
    "רחובות": (31.898, 34.808),      "Rehovot": (31.898, 34.808),
    "מודיעין": (31.898, 35.010),     "Modi'in": (31.898, 35.010),
    # Golan / East
    "קצרין": (32.993, 35.693),       "Katzrin": (32.993, 35.693),
    "בית שאן": (32.498, 35.499),     "Beit She'an": (32.498, 35.499),
    # Default Israel centroid for unknown cities
    "_default": (31.0, 34.8),
}

# Track seen alert IDs to avoid re-firing same alert
_seen_ids: set[str] = set()
_MAX_SEEN = 2000


def _resolve_city(name: str) -> tuple[float, float]:
    """Look up coordinates for a Hebrew or English city name."""
    if name in _CITY_COORDS:
        return _CITY_COORDS[name]
    # Try partial match
    name_lower = name.lower()
    for key, coords in _CITY_COORDS.items():
        if name_lower in key.lower() or key.lower() in name_lower:
            return coords
    return _CITY_COORDS["_default"]


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_PIKUD_HAOREF:
        return []

    results = []
    now = datetime.now(timezone.utc).isoformat()

    # Try active alerts first
    try:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS, follow_redirects=True) as client:
            r = await client.get(_OREF_URL)
            if r.status_code == 200 and r.text.strip():
                try:
                    data = r.json()
                except Exception:
                    data = {}

                alert_id = str(data.get("id", ""))
                cities   = data.get("data", []) or []
                cat_id   = int(data.get("cat", 1))
                title_he = data.get("title", "")

                cat_label, cat_type = _ALERT_CATEGORIES.get(cat_id, ("Alert", "attack"))

                if alert_id and alert_id not in _seen_ids and cities:
                    _seen_ids.add(alert_id)
                    if len(_seen_ids) > _MAX_SEEN:
                        _seen_ids.clear()

                    for city in cities:
                        lat, lon = _resolve_city(city)
                        results.append({
                            "source":      "pikud_haoref",
                            "title":       f"🚨 {cat_label}: {city}",
                            "description": f"Israel Home Front Command alert. Category: {cat_label}. Location: {city}.",
                            "lat":         lat,
                            "lon":         lon,
                            "country":     "Israel",
                            "category":    cat_type,
                            "raw_ts_utc":  now,
                            "url":         "https://www.oref.org.il/",
                            "extra":       json.dumps({
                                "alert_id": alert_id,
                                "cat_id":   cat_id,
                                "city_he":  city,
                                "title_he": title_he,
                            }),
                        })
                    if results:
                        log(f"pikud_haoref: {len(results)} ACTIVE alerts — {cat_label}")

    except httpx.ConnectError:
        pass  # geo-blocked or network issue — silent fail
    except Exception as exc:
        log_warn(f"pikud_haoref: {exc}")

    # If no active alerts, pull recent history for context
    if not results:
        try:
            async with httpx.AsyncClient(timeout=10, headers=_HEADERS, follow_redirects=True) as client:
                r = await client.get(_OREF_HISTORY)
                if r.status_code == 200:
                    history = r.json() if r.text.strip() else []
                    for item in (history or [])[:10]:
                        alert_id = str(item.get("alertDate", "") + item.get("title", ""))
                        if alert_id in _seen_ids:
                            continue
                        _seen_ids.add(alert_id)
                        cities = [item.get("data", "")] if item.get("data") else []
                        cat_label, cat_type = _ALERT_CATEGORIES.get(
                            int(item.get("category", 1)), ("Alert", "attack")
                        )
                        for city in cities:
                            lat, lon = _resolve_city(city)
                            ts = item.get("alertDate", now)
                            results.append({
                                "source":      "pikud_haoref",
                                "title":       f"🚨 {cat_label}: {city}",
                                "description": f"Israel Home Front Command — historical alert. {city}.",
                                "lat":         lat,
                                "lon":         lon,
                                "country":     "Israel",
                                "category":    cat_type,
                                "raw_ts_utc":  ts,
                                "url":         "https://www.oref.org.il/",
                                "extra":       json.dumps({"city_he": city, "cat_id": item.get("category")}),
                            })
        except Exception:
            pass

    return results
