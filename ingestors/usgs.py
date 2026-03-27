"""USGS Earthquake ingestor

Real-time GeoJSON feed, no API key required.
Cross-reference: underground explosions, industrial blasts, nuclear tests.
Shallow events (<10km) and non-earthquake types are flagged.
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta

import httpx

from config import settings as C

_USGS_URL   = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_MIN_MAG    = 2.5
_HOURS_BACK = 24


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_USGS:
        return []

    start  = (datetime.now(timezone.utc) - timedelta(hours=_HOURS_BACK)).strftime("%Y-%m-%dT%H:%M:%S")
    params = {
        "format":       "geojson",
        "starttime":    start,
        "minmagnitude": _MIN_MAG,
        "orderby":      "time",
        "limit":        200,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(_USGS_URL, params=params)
            if r.status_code != 200:
                log_warn(f"usgs: HTTP {r.status_code}")
                return []
            data = r.json()
    except Exception as exc:
        log_warn(f"usgs: fetch error: {exc}")
        return []

    results = []
    for feat in (data.get("features") or []):
        try:
            props  = feat.get("properties") or {}
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue

            lon      = float(coords[0])
            lat      = float(coords[1])
            depth_km = float(coords[2]) if len(coords) > 2 else None
            mag      = props.get("mag")
            place    = str(props.get("place") or "Unknown")
            ts_ms    = props.get("time")
            mag_type = str(props.get("magType") or "")
            evt_type = str(props.get("type") or "earthquake")
            url      = str(props.get("url") or props.get("detail") or "")

            if mag is None:
                continue

            ts_str = (
                datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
                if ts_ms else datetime.now(timezone.utc).isoformat()
            )

            # Flag: very shallow (<2km) or non-earthquake type → possible explosion/test
            unusual = evt_type not in ("earthquake", "quarry blast") or (
                depth_km is not None and depth_km < 2.0
            )
            prefix = "EXPLOSION?" if unusual else "SEISMIC"
            title  = f"{prefix} M{mag:.1f} — {place}"

            depth_str = f"{depth_km:.1f}km" if depth_km is not None else "unknown"

            results.append({
                "source":      "usgs",
                "title":       title[:200],
                "description": f"Mag: {mag} {mag_type} | Depth: {depth_str} | {evt_type}",
                "lat":         round(lat, 4),
                "lon":         round(lon, 4),
                "country":     "",
                "category":    "seismic",
                "raw_ts_utc":  ts_str,
                "url":         url,
                "extra":       json.dumps({
                    "mag": mag,
                    "mag_type": mag_type,
                    "depth_km": depth_km,
                    "event_type": evt_type,
                    "place": place,
                }),
            })
        except Exception:
            continue

    log(f"usgs: {len(results)} seismic events (M≥{_MIN_MAG})")
    return results
