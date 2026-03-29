"""Military aircraft ingestor — Airplanes.live /v2/mil

Fetches all aircraft currently tagged as military in real-time.
Free, no API key, covers global ADS-B/MLAT including FAA-blocklisted flights.
Sources: RC-135, E-3 AWACS, P-8 Poseidon, U-2, B-52, tankers, ISR aircraft.

URL: https://api.airplanes.live/v2/mil
Returns list of position dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import httpx

from config import settings as C

_URL = "https://api.airplanes.live/v2/mil"
_HEADERS = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}

# Map Airplanes.live category codes to our labels
_CATEGORY = {
    "A1": "light", "A2": "small", "A3": "large",
    "A4": "high_vortex", "A5": "heavy", "A6": "high_perf",
    "A7": "rotorcraft", "B1": "glider", "B2": "balloon",
    "B4": "skydiver", "B6": "uav", "B7": "spacecraft",
}


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_MIL_AIRCRAFT:
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(_URL, headers=_HEADERS)
        if r.status_code != 200:
            log_warn(f"mil_aircraft: HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as exc:
        log_warn(f"mil_aircraft: fetch error: {exc}")
        return []

    results = []
    for ac in (data.get("ac") or []):
        try:
            lat = ac.get("lat")
            lon = ac.get("lon")
            if lat is None or lon is None:
                continue

            # Skip parked/default zero positions
            if abs(float(lat)) < 0.01 and abs(float(lon)) < 0.01:
                continue

            callsign  = (ac.get("flight") or "").strip() or ac.get("hex", "").upper()
            hex_code  = ac.get("hex", "")
            ac_type   = ac.get("t") or ac.get("type") or ""
            reg       = ac.get("r") or ""
            owner     = ac.get("ownOp") or ""
            alt_baro  = ac.get("alt_baro")
            alt_geom  = ac.get("alt_geom")
            gs        = ac.get("gs")   # ground speed knots
            track     = ac.get("track")
            squawk    = ac.get("squawk") or ""
            emergency = ac.get("emergency") or ""

            # Altitude preference: geometric > barometric (for military often blocked)
            altitude = None
            if alt_baro and alt_baro != "ground":
                try:
                    altitude = float(alt_baro)
                except Exception:
                    pass
            if altitude is None and alt_geom:
                try:
                    altitude = float(alt_geom)
                except Exception:
                    pass

            results.append({
                "source":       "airplaneslive",
                "callsign":     callsign,
                "type":         _CATEGORY.get(ac.get("category", ""), "military"),
                "lat":          float(lat),
                "lon":          float(lon),
                "altitude_ft":  altitude,
                "speed_kts":    float(gs) if gs is not None else None,
                "heading_deg":  float(track) if track is not None else None,
                "country":      "",
                "military_flag": 1,
                "extra": json.dumps({
                    "hex":      hex_code,
                    "ac_type":  ac_type,
                    "reg":      reg,
                    "owner":    owner,
                    "squawk":   squawk,
                    "emergency": emergency,
                }),
            })
        except Exception:
            continue

    log(f"mil_aircraft: {len(results)} military aircraft via Airplanes.live")
    return results
