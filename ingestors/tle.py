"""Satellite TLE ingestor — CelesTrak

Fetches TLE data and computes current positions using sgp4.
Returns list of position dicts. Never raises.
"""
from __future__ import annotations
import asyncio, json
from datetime import datetime, timezone

import httpx

from config import settings as C

# CelesTrak catalog URLs (active satellites only)
_TLE_URLS = [
    "https://celestrak.org/SOCRATES/query.php?CATNR=25544&FORMAT=TLE",  # ISS
    "https://celestrak.org/pub/TLE/catalog/active.txt",                  # All active
    "https://celestrak.org/SOCRATES/",
]

# Use the "active" catalog — ~7000 objects, manageable
# Primary: tle.ivanstanojevic.me — JSON API, 24k+ satellites, no key required
# CelesTrak blocks automated requests from most IPs
_TLE_API_URL  = "https://tle.ivanstanojevic.me/api/tle/"
_TLE_HEADERS  = {"User-Agent": "Wardar/0.1 (global situational awareness platform)"}


def _parse_tle_lines(text: str) -> list[tuple[str, str, str]]:
    """Parse TLE 3-line format. Returns list of (name, line1, line2)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result = []
    i = 0
    while i < len(lines) - 2:
        if lines[i].startswith("1 ") or lines[i].startswith("2 "):
            i += 1
            continue
        name = lines[i]
        l1   = lines[i+1] if i+1 < len(lines) else ""
        l2   = lines[i+2] if i+2 < len(lines) else ""
        if l1.startswith("1 ") and l2.startswith("2 "):
            result.append((name, l1, l2))
            i += 3
        else:
            i += 1
    return result

def _propagate(name: str, line1: str, line2: str) -> dict | None:
    """Compute current lat/lon from TLE using sgp4."""
    try:
        from sgp4.api import Satrec, jday  # type: ignore
        sat = Satrec.twoline2rv(line1, line2)
        now = datetime.now(timezone.utc)
        jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second + now.microsecond/1e6)
        e, r, v = sat.sgp4(jd, fr)
        if e != 0:
            return None
        # Convert ECI (km) to lat/lon/alt
        from math import atan2, sqrt, pi, asin
        x, y, z = r
        lon_rad = atan2(y, x)
        lat_rad = asin(z / sqrt(x*x + y*y + z*z))
        alt_km  = sqrt(x*x + y*y + z*z) - 6371.0
        lat = lat_rad * 180 / pi
        lon = lon_rad * 180 / pi
        # Adjust for Earth rotation (approximate GMST)
        import time as _time
        gst_deg = (280.46061837 + 360.98564736629 * ((_time.time() - 946728000) / 86400.0)) % 360
        lon = (lon - gst_deg + 180) % 360 - 180

        military_flag = 0  # CelesTrak active catalog is mostly civilian/commercial

        return {
            "source":       "tle",
            "callsign":     name.strip()[:30],
            "type":         "satellite",
            "lat":          round(lat, 4),
            "lon":          round(lon, 4),
            "altitude_ft":  round(alt_km * 3280.84, 0),  # km → ft
            "speed_kts":    None,
            "heading_deg":  None,
            "country":      "",
            "military_flag": military_flag,
            "extra":        json.dumps({"alt_km": round(alt_km, 1)}),
        }
    except ImportError:
        return None  # sgp4 not installed — skip silently
    except Exception:
        return None

async def fetch() -> list[dict]:
    """Fetch TLE catalog and compute current positions."""
    from core.engine import log, log_warn

    if not C.ENABLE_SATELLITE:
        return []

    tles: list[tuple[str,str,str]] = []
    # API max page-size=100; fetch 5 pages = 500 satellites for Phase 1
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for page in range(1, 6):
                r = await client.get(
                    f"{_TLE_API_URL}?page={page}&page-size=100",
                    headers=_TLE_HEADERS,
                )
                if r.status_code != 200:
                    log_warn(f"tle: HTTP {r.status_code} on page {page}")
                    break
                data = r.json()
                for item in data.get("member") or []:
                    name = item.get("name", "")
                    l1   = item.get("line1", "")
                    l2   = item.get("line2", "")
                    if l1 and l2:
                        tles.append((name, l1, l2))
    except Exception as exc:
        log_warn(f"tle: fetch failed: {exc}")
        return []

    # Propagate positions (CPU-bound — run in executor)
    results = []
    loop = asyncio.get_event_loop()
    for name, l1, l2 in tles:
        p = await loop.run_in_executor(None, _propagate, name, l1, l2)
        if p:
            results.append(p)

    log(f"tle: {len(results)} satellite positions (from {len(tles)} TLEs)")
    return results
