"""Global Fishing Watch dark vessel ingestor

Detects vessels visible on SAR (Synthetic Aperture Radar) satellite imagery
but NOT transmitting AIS — classic sanctions-evasion / dark fleet signature.

Free non-commercial API from Global Fishing Watch (globalfishingwatch.org).
Requires a free API key: https://globalfishingwatch.org/our-apis/

Set GFW_API_KEY in Railway environment variables.

Targets:
- Iran oil sanctions evasion fleet (Persian Gulf, Hormuz, Indian Ocean)
- North Korea ship-to-ship transfers (Yellow Sea, East China Sea)
- Russia shadow fleet (Baltic, North Sea, Black Sea)

Returns list of event dicts with source='gfw_vessels', category='dark_vessel'.
Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta

import httpx

from config import settings as C

_GFW_BASE = "https://gateway.api.globalfishingwatch.org/v3"
_HEADERS_TMPL = {
    "User-Agent":    "Wardar/0.1 (+https://wardar.app) dark-vessel-detection",
    "Content-Type":  "application/json",
}

# Areas of interest: (name, min_lon, min_lat, max_lon, max_lat)
_AOI = [
    ("Persian Gulf / Hormuz",   50.0, 22.0, 60.0, 30.0),
    ("Red Sea",                 32.0, 12.0, 45.0, 30.0),
    ("Gulf of Oman",            55.0, 20.0, 65.0, 27.0),
    ("Yellow Sea / NKorea",    120.0, 32.0, 132.0, 42.0),
    ("East China Sea",         118.0, 24.0, 132.0, 34.0),
    ("Baltic Sea",               9.5, 53.5,  30.0, 65.0),
    ("Black Sea",               27.5, 40.5,  41.5, 46.5),
    ("North Sea",               -4.0, 51.0,   9.0, 59.0),
]

# Vessel type codes that suggest commercial/tanker traffic
# Types 1-9 are SAR vessel detections; we alert on any dark detection in AOI
_SUSPICIOUS_TYPES = {"CARRIER", "TANKER", "BUNKER", "FISHING", "OTHER"}


async def fetch() -> list[dict]:
    from core.engine import log, log_warn

    if not C.ENABLE_GFW or not C.GFW_API_KEY:
        return []

    headers = {**_HEADERS_TMPL, "Authorization": f"Bearer {C.GFW_API_KEY}"}

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = []
    seen_ids: set[str] = set()

    for aoi_name, min_lon, min_lat, max_lon, max_lat in _AOI:
        try:
            # Query SAR-detected vessels (non-broadcasting) in area
            payload = {
                "datasets": ["public-global-sar-presence:latest"],
                "startDate": since,
                "endDate":   until,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [min_lon, min_lat],
                        [max_lon, min_lat],
                        [max_lon, max_lat],
                        [min_lon, max_lat],
                        [min_lon, min_lat],
                    ]],
                },
            }
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{_GFW_BASE}/events",
                    json=payload,
                    headers=headers,
                    params={"limit": 100, "offset": 0},
                )

            if r.status_code == 401:
                log_warn("gfw_vessels: 401 Unauthorized — check GFW_API_KEY")
                return []
            if r.status_code == 404:
                log_warn(f"gfw_vessels: 404 for {aoi_name} — dataset may have changed")
                continue
            if r.status_code != 200:
                log_warn(f"gfw_vessels: HTTP {r.status_code} for {aoi_name}")
                continue

            entries = r.json().get("entries") or []

            for entry in entries:
                try:
                    eid = entry.get("id") or ""
                    if eid and eid in seen_ids:
                        continue
                    if eid:
                        seen_ids.add(eid)

                    position  = entry.get("position", {})
                    lat = float(position.get("lat") or 0)
                    lon = float(position.get("lon") or 0)
                    if lat == 0 and lon == 0:
                        continue

                    vessel     = entry.get("vessel", {}) or {}
                    mmsi       = vessel.get("mmsi") or ""
                    v_type     = vessel.get("vesselType") or "UNKNOWN"
                    flag       = vessel.get("flag") or ""
                    name       = vessel.get("name") or mmsi or "UNKNOWN"

                    # Only flag dark (non-AIS) detections
                    ais_status = entry.get("aisType") or ""
                    is_dark    = not ais_status or ais_status == "ABSENT"
                    if not is_dark:
                        continue

                    ts = entry.get("start") or entry.get("timestamp") or datetime.now(timezone.utc).isoformat()

                    results.append({
                        "source":      "gfw_vessels",
                        "title":       f"DARK VESSEL: {name} [{flag}] — {aoi_name}",
                        "description": (
                            f"SAR-detected vessel not transmitting AIS in {aoi_name}. "
                            f"Type: {v_type} | Flag: {flag or 'unknown'} | "
                            f"MMSI: {mmsi or 'none'} | Possible sanctions evasion."
                        ),
                        "lat":         lat,
                        "lon":         lon,
                        "country":     flag or "",
                        "category":    "dark_vessel",
                        "raw_ts_utc":  ts,
                        "url":         "https://globalfishingwatch.org/map/",
                        "extra":       json.dumps({
                            "gfw_id":    eid,
                            "mmsi":      mmsi,
                            "name":      name,
                            "type":      v_type,
                            "flag":      flag,
                            "aoi":       aoi_name,
                            "ais_status": ais_status,
                        }),
                    })
                except Exception:
                    continue

        except Exception as exc:
            log_warn(f"gfw_vessels: error in {aoi_name}: {exc}")
            continue

    log(f"gfw_vessels: {len(results)} dark vessel detections across {len(_AOI)} AOIs")
    return results
