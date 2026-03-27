"""ADS-B ingestor — ADS-B Exchange (primary) + OpenSky (fallback)

Returns list of position dicts normalized to the Wardar schema.
Never raises — returns [] on any failure.
"""
from __future__ import annotations
import asyncio, json
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings as C

# Known military ICAO prefixes / callsign patterns (very basic heuristic)
_MIL_CALLSIGN_PREFIXES = (
    "RCH","CNV","JAKE","PACK","REACH","MARLIN","PANDA","KONGO",
    "GORDO","YANKY","BUCKY","ROCKY","FURY","VIPER","EAGLE",
    "BARON","ATLAS","SWORD","GHOST",
    "RRR","NATO","USAF","USMC","USN","RAF","UKAF",
)
_MIL_ICAO_PREFIXES = (
    "AE",  # US military
    "43",  # Russia military range
)

def _is_military(icao: str, callsign: str) -> bool:
    if not icao and not callsign:
        return False
    if icao:
        for pfx in _MIL_ICAO_PREFIXES:
            if icao.upper().startswith(pfx):
                return True
    if callsign:
        cs = callsign.upper().strip()
        for pfx in _MIL_CALLSIGN_PREFIXES:
            if cs.startswith(pfx):
                return True
    return False

def _norm(raw: dict, source: str) -> dict | None:
    """Normalize a raw aircraft record to the Wardar position schema."""
    try:
        lat = float(raw.get("lat") or raw.get("latitude") or 0)
        lon = float(raw.get("lon") or raw.get("longitude") or raw.get("lng") or 0)
        if lat == 0 and lon == 0:
            return None
        icao     = str(raw.get("icao") or raw.get("icao24") or "")
        callsign = str(raw.get("callsign") or raw.get("flight") or "").strip()
        country  = str(raw.get("country") or raw.get("origin_country") or "")
        alt_ft   = raw.get("alt_baro") or raw.get("altitude") or raw.get("geo_altitude")
        spd_kts  = raw.get("gs") or raw.get("velocity")
        hdg      = raw.get("track") or raw.get("true_track")
        military = bool(raw.get("military")) or _is_military(icao, callsign)
        return {
            "source":       source,
            "callsign":     callsign or icao or "UNKNOWN",
            "type":         "aircraft",
            "lat":          lat,
            "lon":          lon,
            "altitude_ft":  float(alt_ft) if alt_ft is not None else None,
            "speed_kts":    float(spd_kts) if spd_kts is not None else None,
            "heading_deg":  float(hdg) if hdg is not None else None,
            "country":      country,
            "military_flag": 1 if military else 0,
            "extra":        json.dumps({"icao": icao}),
        }
    except Exception:
        return None

async def _fetch_adsbexchange(lat: float, lon: float, dist_nm: int = 250) -> list[dict]:
    """Fetch from ADS-B Exchange RapidAPI."""
    if not C.ADSB_EXCHANGE_API_KEY:
        return []
    url = C.ADSB_EXCHANGE_URL.format(lat=lat, lon=lon, dist=dist_nm)
    headers = {
        "X-RapidAPI-Key":  C.ADSB_EXCHANGE_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return []
            data = r.json()
            aircraft = data.get("ac") or data.get("aircraft") or []
            results = []
            for a in aircraft:
                p = _norm(a, "adsb")
                if p:
                    results.append(p)
            return results
    except Exception:
        return []

async def _fetch_opensky() -> list[dict]:
    """Fetch all states from OpenSky Network REST API."""
    url = "https://opensky-network.org/api/states/all"
    auth = None
    if C.OPENSKY_USERNAME and C.OPENSKY_PASSWORD:
        auth = (C.OPENSKY_USERNAME, C.OPENSKY_PASSWORD)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, auth=auth)
            if r.status_code != 200:
                return []
            data = r.json()
            states = data.get("states") or []
            results = []
            # OpenSky state vector: [icao24, callsign, origin_country, time_pos, last_contact,
            #                         longitude, latitude, baro_altitude, on_ground, velocity,
            #                         true_track, vertical_rate, sensors, geo_altitude, ...]
            for s in states:
                if len(s) < 14:
                    continue
                raw = {
                    "icao24":         s[0],
                    "callsign":       (s[1] or "").strip(),
                    "origin_country": s[2],
                    "longitude":      s[5],
                    "latitude":       s[6],
                    "altitude":       s[7],
                    "velocity":       s[9],
                    "true_track":     s[10],
                    "geo_altitude":   s[13],
                }
                p = _norm(raw, "opensky")
                if p:
                    results.append(p)
            return results
    except Exception:
        return []

async def fetch() -> list[dict]:
    """Main entry point. Returns normalized position list.
    Tries ADS-B Exchange first; falls back to OpenSky if no key or empty result.
    """
    from core.engine import log

    results: list[dict] = []

    if C.ENABLE_ADSB and C.ADSB_EXCHANGE_API_KEY:
        # Fetch a global sample: 4 quadrant anchors at ±45 lat/lon, 500nm radius
        tasks = [
            _fetch_adsbexchange( 45,  0,  500),
            _fetch_adsbexchange(-45,  0,  500),
            _fetch_adsbexchange( 45, 180, 500),
            _fetch_adsbexchange(-45, 180, 500),
        ]
        chunks = await asyncio.gather(*tasks)
        seen: set[str] = set()
        for chunk in chunks:
            for p in chunk:
                key = p["callsign"] + str(p["lat"]) + str(p["lon"])
                if key not in seen:
                    seen.add(key)
                    results.append(p)

    if not results and C.ENABLE_OPENSKY:
        results = await _fetch_opensky()

    log(f"adsb: {len(results)} positions")
    return results
