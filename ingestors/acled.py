"""ACLED conflict events ingestor — acleddata.com API

Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

import httpx

from config import settings as C

_ACLED_URL = "https://api.acleddata.com/acled/read"

# Event type → category
_CATEGORY_MAP = {
    "Battles":             "conflict",
    "Explosions/Remote violence": "conflict",
    "Violence against civilians": "conflict",
    "Protests":            "civil_unrest",
    "Riots":               "civil_unrest",
    "Strategic developments": "strategic",
}

async def fetch() -> list[dict]:
    """Fetch last 7 days of ACLED events."""
    from core.engine import log, log_warn

    if not C.ENABLE_ACLED or not C.ACLED_API_KEY:
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    params = {
        "key":        C.ACLED_API_KEY,
        "email":      C.ACLED_EMAIL,
        "event_date": since,
        "event_date_where": ">=",
        "limit":      500,
        "fields":     "event_date|event_type|sub_event_type|actor1|country|admin1|location|latitude|longitude|notes|source",
        "format":     "json",
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(_ACLED_URL, params=params)
            if r.status_code != 200:
                log_warn(f"acled: HTTP {r.status_code}")
                return []
            data = r.json().get("data") or []
    except Exception as exc:
        log_warn(f"acled: fetch error: {exc}")
        return []

    results = []
    for row in data:
        try:
            lat = float(row.get("latitude") or 0)
            lon = float(row.get("longitude") or 0)
            if lat == 0 and lon == 0:
                continue
            ev_type  = row.get("event_type", "")
            category = _CATEGORY_MAP.get(ev_type, "conflict")
            title    = f"{ev_type}: {row.get('actor1','')}"
            ts       = row.get("event_date", datetime.now(timezone.utc).date().isoformat())
            results.append({
                "source":      "acled",
                "title":       title[:200],
                "description": str(row.get("notes", ""))[:1000],
                "lat":         lat,
                "lon":         lon,
                "country":     row.get("country", ""),
                "category":    category,
                "raw_ts_utc":  ts + "T00:00:00+00:00",
                "url":         "",
                "extra":       json.dumps({
                    "sub_event_type": row.get("sub_event_type"),
                    "admin1":         row.get("admin1"),
                    "location":       row.get("location"),
                    "source":         row.get("source"),
                }),
            })
        except Exception:
            continue

    log(f"acled: {len(results)} conflict events")
    return results


async def fetch_iran() -> list[dict]:
    """Fetch last 30 days of ACLED events for Iran/Israel/Gaza war zone.

    Uses a broader 30-day window and country filter to capture the full
    US/Israel vs Iran conflict arc. Source tagged 'acled_iran' so the
    frontend can toggle it as its own layer.
    """
    from core.engine import log, log_warn

    if not C.ENABLE_ACLED or not C.ACLED_API_KEY:
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    # Countries in the active war theatre
    country_filter = "Iran|Israel|Iraq|Yemen|Syria|Lebanon"
    params = {
        "key":        C.ACLED_API_KEY,
        "email":      C.ACLED_EMAIL,
        "event_date": since,
        "event_date_where": ">=",
        "country":    country_filter,
        "country_where": "LIKE",
        "limit":      500,
        "fields":     "event_date|event_type|sub_event_type|actor1|country|admin1|location|latitude|longitude|notes|source",
        "format":     "json",
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(_ACLED_URL, params=params)
            if r.status_code != 200:
                log_warn(f"acled_iran: HTTP {r.status_code}")
                return []
            data = r.json().get("data") or []
    except Exception as exc:
        log_warn(f"acled_iran: fetch error: {exc}")
        return []

    results = []
    for row in data:
        try:
            lat = float(row.get("latitude") or 0)
            lon = float(row.get("longitude") or 0)
            if lat == 0 and lon == 0:
                continue
            ev_type  = row.get("event_type", "")
            category = _CATEGORY_MAP.get(ev_type, "conflict")
            title    = f"{ev_type}: {row.get('actor1','')}"
            ts       = row.get("event_date", datetime.now(timezone.utc).date().isoformat())
            results.append({
                "source":      "acled_iran",
                "title":       title[:200],
                "description": str(row.get("notes", ""))[:1000],
                "lat":         lat,
                "lon":         lon,
                "country":     row.get("country", ""),
                "category":    category,
                "raw_ts_utc":  ts + "T00:00:00+00:00",
                "url":         "",
                "extra":       json.dumps({
                    "sub_event_type": row.get("sub_event_type"),
                    "admin1":         row.get("admin1"),
                    "location":       row.get("location"),
                    "source":         row.get("source"),
                }),
            })
        except Exception:
            continue

    log(f"acled_iran: {len(results)} Iran-theatre conflict events")
    return results
