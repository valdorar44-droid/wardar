"""
Wardar — Sonic Boom / Seismic Cross-Reference (Signal #5)
==========================================================
Supersonic and low-flying military aircraft produce infrasound and surface
pressure waves detectable by seismic networks.

Two approaches run in parallel:
  A. USGS FDSN API — query specifically for "sonic boom" and "explosion"
     event types. These are classified by USGS when pattern matches an
     airwave/Rayleigh-wave signature rather than a tectonic event.
     Events: shallow depth (<3km), short duration, horizontal propagation.

  B. Seismic DB cross-reference — check existing events table for USGS
     events that are near a ghost aircraft's dead-reckoned position.
     Sonic boom travel speed: ~340 m/s (Mach 1 at sea level), so a
     M1.8 event 200km away could be from an aircraft 600 seconds ago.

Returns correlation strings used to enrich ghost popups.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from config import settings as C

_USGS_URL     = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_HEADERS      = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}
_CACHE_TTL    = 300   # 5 min — sonic boom events are rare, cache is safe
_SONIC_RADIUS_KM = 400  # sonic boom detectable within this range of aircraft
_MAX_DEPTH_KM    = 3.0  # sonic booms register as near-surface events

# Cache: (monotonic_time, list_of_events)
_sonic_cache: tuple[float, list[dict]] = (0.0, [])


def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(min(1.0, a)))


async def _fetch_usgs_sonic() -> list[dict]:
    """
    Fetch sonic boom and explosion events from USGS FDSN in the last 24h.
    These are rare but definitive: USGS explicitly tags them when the
    wave signature matches an airwave (horizontal propagation, no S-wave).
    """
    global _sonic_cache

    now = time.monotonic()
    if now - _sonic_cache[0] < _CACHE_TTL:
        return _sonic_cache[1]

    start = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    events: list[dict] = []

    for evt_type in ("sonic+boom", "explosion", "acoustic+noise"):
        try:
            params = {
                "format":    "geojson",
                "starttime": start,
                "eventtype": evt_type,
                "limit":     200,
                "orderby":   "time",
            }
            async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as cl:
                r = await cl.get(_USGS_URL, params=params)
            if r.status_code != 200:
                continue
            feats = r.json().get("features") or []
            for f in feats:
                props  = f.get("properties") or {}
                coords = (f.get("geometry") or {}).get("coordinates") or []
                if len(coords) < 2:
                    continue
                depth = float(coords[2]) if len(coords) > 2 else None
                if depth is not None and depth > _MAX_DEPTH_KM:
                    continue   # too deep — tectonic, not surface
                events.append({
                    "lat":       float(coords[1]),
                    "lon":       float(coords[0]),
                    "depth_km":  depth,
                    "mag":       props.get("mag"),
                    "place":     props.get("place", ""),
                    "type":      evt_type.replace("+", " "),
                    "ts_utc":    datetime.fromtimestamp(
                                   props["time"] / 1000, tz=timezone.utc
                                 ).isoformat() if props.get("time") else "",
                })
        except Exception:
            continue

    _sonic_cache = (now, events)
    return events


def check_sonic_near_ghost(conn, ghost_lat: float, ghost_lon: float) -> str:
    """
    Check both the USGS DB events and sonic cache for detections near ghost.
    Returns a description string or "" if nothing found.
    Synchronous — uses cached data only (no network call here).
    """
    events = _sonic_cache[1]

    # Also check events already in DB (from normal USGS ingestor run)
    try:
        db_rows = conn.execute("""
            SELECT lat, lon, title, raw_ts_utc FROM events
            WHERE source = 'usgs'
              AND raw_ts_utc > datetime('now', '-24 hours')
              AND lat IS NOT NULL AND lon IS NOT NULL
              AND (title LIKE '%sonic%'
                   OR title LIKE '%explosion%'
                   OR title LIKE '%blast%'
                   OR title LIKE '%acoustic%')
            ORDER BY raw_ts_utc DESC
            LIMIT 50
        """).fetchall()

        for row in db_rows:
            km = _hav(ghost_lat, ghost_lon, float(row["lat"]), float(row["lon"]))
            if km <= _SONIC_RADIUS_KM:
                return f"seismic {row['title'][:40]} @ {int(km)}km"
    except Exception:
        pass

    # Check freshly fetched USGS sonic events
    for ev in events:
        km = _hav(ghost_lat, ghost_lon, ev["lat"], ev["lon"])
        if km <= _SONIC_RADIUS_KM:
            label = ev["type"].upper()
            return f"{label} M{ev.get('mag','?')} @ {int(km)}km ({ev.get('place','')[:30]})"

    return ""


async def refresh() -> int:
    """Refresh the sonic boom cache. Called from engine every 15 min."""
    events = await _fetch_usgs_sonic()
    return len(events)
