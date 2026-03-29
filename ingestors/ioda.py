"""Internet Outage Detection and Analysis (IODA) ingestor.

Free public API from CAIDA — no auth required.
Detects internet blackouts in conflict zones (Iran, Gaza, Sudan, etc.)
These are critical signals: government-ordered shutdowns often precede or accompany military action.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from config import settings as C

# IODA public API — free, no auth
_IODA_URL = "https://api.ioda.caida.org/v2/outages/country"

# Country codes to monitor (conflict zones + Iran watch)
_CONFLICT_COUNTRIES: dict[str, dict] = {
    "IR":  {"name": "Iran",            "lat": 32.0,  "lon": 53.0},
    "PS":  {"name": "Palestine",       "lat": 31.9,  "lon": 35.2},
    "SY":  {"name": "Syria",           "lat": 35.0,  "lon": 38.0},
    "YE":  {"name": "Yemen",           "lat": 15.5,  "lon": 48.5},
    "IQ":  {"name": "Iraq",            "lat": 33.0,  "lon": 44.0},
    "SD":  {"name": "Sudan",           "lat": 15.0,  "lon": 30.0},
    "UA":  {"name": "Ukraine",         "lat": 49.0,  "lon": 32.0},
    "RU":  {"name": "Russia",          "lat": 61.5,  "lon": 90.0},
    "MM":  {"name": "Myanmar",         "lat": 19.0,  "lon": 96.5},
    "KP":  {"name": "North Korea",     "lat": 40.0,  "lon": 127.0},
    "AF":  {"name": "Afghanistan",     "lat": 33.0,  "lon": 65.0},
    "LB":  {"name": "Lebanon",         "lat": 33.9,  "lon": 35.5},
    "ET":  {"name": "Ethiopia",        "lat": 9.0,   "lon": 40.0},
    "SO":  {"name": "Somalia",         "lat": 5.0,   "lon": 46.0},
    "VE":  {"name": "Venezuela",       "lat": 8.0,   "lon": -66.0},
    "BY":  {"name": "Belarus",         "lat": 53.7,  "lon": 27.9},
}

# Minimum severity score to report (0-100+, higher = worse outage)
_MIN_SCORE = 10


async def fetch() -> list[dict]:
    """Fetch internet outage events for conflict-zone countries."""
    if not C.ENABLE_IODA:
        return []

    try:
        from core.engine import log, log_warn
    except Exception:
        return []

    # Fetch last 24h
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    from_epoch = int(since.timestamp())
    until_epoch = int(now.timestamp())

    events: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Wardar/0.1"}) as client:
            r = await client.get(
                _IODA_URL,
                params={
                    "from":  from_epoch,
                    "until": until_epoch,
                    "limit": 200,
                },
            )
            if r.status_code != 200:
                log_warn(f"ioda: HTTP {r.status_code}")
                return []

            data = r.json()
            outages = data.get("data") or []

    except Exception as exc:
        try:
            log_warn(f"ioda: fetch error: {exc}")
        except Exception:
            pass
        return []

    for outage in outages:
        try:
            entity = outage.get("entity") or {}
            code = entity.get("code") or ""
            country_info = _CONFLICT_COUNTRIES.get(code.upper())
            if not country_info:
                continue  # not a monitored country

            # Score / severity
            score = outage.get("score") or 0
            if score < _MIN_SCORE:
                continue

            country_name = country_info["name"]
            lat = country_info["lat"]
            lon = country_info["lon"]

            start_ts = outage.get("from") or from_epoch
            end_ts   = outage.get("until") or until_epoch
            start_str = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
            end_str   = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()

            severity = "SEVERE" if score > 80 else ("HIGH" if score > 50 else "MODERATE")
            title = f"INTERNET BLACKOUT — {country_name.upper()} [{severity}]"
            description = (
                f"{country_name} internet outage detected by IODA/CAIDA. "
                f"Score: {score:.0f}/100. "
                f"Start: {start_str[:16]}Z. "
                f"End: {end_str[:16]}Z. "
                f"Severity: {severity}."
            )

            events.append({
                "source":      "ioda",
                "title":       title,
                "description": description,
                "lat":         lat,
                "lon":         lon,
                "country":     country_name,
                "category":    "internet_blackout",
                "url":         f"https://ioda.live/country/{code}",
                "raw_ts_utc":  start_str,
                "extra":       json.dumps({
                    "score":    score,
                    "severity": severity,
                    "country_code": code,
                    "outage_start": start_str,
                    "outage_end":   end_str,
                }),
            })
        except Exception:
            continue

    log(f"ioda: {len(events)} internet outage events ({len(outages)} raw entries)")
    return events
