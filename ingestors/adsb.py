"""ADS-B ingestor — multiple sources including military-capable community feeds

Sources (in priority order):
  1. ADS-B Exchange RapidAPI (paid, best coverage, military included)
  2. airplanes.live   (free, no key, community-fed, military included)
  3. adsb.lol         (free, no key, community-fed, military included)
  4. adsb.fi          (free, open data, military included)
  5. OpenSky          (free fallback, filters some military, rate-limited)

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
    # US military
    "RCH","CNV","JAKE","PACK","REACH","MARLIN","PANDA","KONGO",
    "GORDO","YANKY","BUCKY","ROCKY","FURY","VIPER","EAGLE",
    "BARON","ATLAS","SWORD","GHOST","DRACO","SPAR","IRON",
    "HAVOC","KNIFE","STING","TOPAZ","VALOR","RAPTOR","TALON",
    "RRR","USAF","USMC","USN",
    # NATO / allies
    "NATO","RAF","UKAF","FRENCH","GAF","IAF",
    # Israel
    "IAF","ISAF","ELBIT",
    # Russia
    "RFF","RSD","RU","ALMAZ",
    # Middle East / regional
    "UAE","SAF","RSAF","RJAF","KAF","BAF","QAF",
)
_MIL_ICAO_PREFIXES = (
    "AE",   # US military
    "43",   # Russia military
    "73",   # Israel (IAF range)
    "70",   # Saudi Arabia military
    "71",   # UAE military
    "A9",   # Bahrain
)

# Targeted region pulls for hotspots — (lat, lon, radius_nm, label)
_HOTSPOT_REGIONS = [
    ( 32.0,  35.0, 400, "middle_east"),   # Israel/Gaza/Lebanon/Syria
    ( 25.0,  45.0, 500, "gulf"),          # Saudi/Yemen/Gulf states
    ( 33.0,  44.0, 400, "iraq_iran"),     # Iraq/Iran
    ( 48.0,  35.0, 500, "ukraine"),       # Ukraine/Black Sea
    ( 55.0,  37.0, 500, "russia_west"),   # Western Russia/Moscow
    ( 65.0,  90.0, 600, "russia_east"),   # Siberia/Far East Russia
    ( 25.0, 121.0, 400, "taiwan_strait"), # Taiwan Strait
    (  5.0,  43.0, 500, "horn_africa"),   # Horn of Africa / Red Sea
]

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

def _norm_adsbfi(raw: dict) -> dict | None:
    """Normalize adsb.fi / airplanes.live / adsb.lol record (readsb JSON format)."""
    try:
        lat = float(raw.get("lat") or 0)
        lon = float(raw.get("lon") or 0)
        if lat == 0 and lon == 0:
            return None
        icao     = str(raw.get("hex") or raw.get("icao") or "")
        callsign = str(raw.get("flight") or raw.get("callsign") or "").strip()
        alt_ft   = raw.get("alt_baro") or raw.get("altitude")
        spd_kts  = raw.get("gs") or raw.get("speed")
        hdg      = raw.get("track") or raw.get("heading")
        country  = str(raw.get("r") or raw.get("country") or "")  # registration prefix
        # community feeds expose military flag directly or via category
        mil_raw  = raw.get("military") or (raw.get("category","") in ("A5","B1","B2","B3","B4","B5","B6","B7"))
        military = bool(mil_raw) or _is_military(icao, callsign)
        return {
            "source":       "adsb",
            "callsign":     callsign or icao or "UNKNOWN",
            "type":         "aircraft",
            "lat":          lat,
            "lon":          lon,
            "altitude_ft":  float(alt_ft) if alt_ft not in (None, "ground") else None,
            "speed_kts":    float(spd_kts) if spd_kts is not None else None,
            "heading_deg":  float(hdg) if hdg is not None else None,
            "country":      country,
            "military_flag": 1 if military else 0,
            "extra":        json.dumps({"icao": icao}),
        }
    except Exception:
        return None


async def _fetch_airplaneslive(lat: float, lon: float, radius_nm: int = 400) -> list[dict]:
    """airplanes.live — free, no key, community-fed, includes military."""
    url = f"https://api.airplanes.live/v2/point/{lat}/{lon}/{radius_nm}"
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Wardar/0.1"}) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            ac = r.json().get("ac") or []
            return [p for a in ac if (p := _norm_adsbfi(a))]
    except Exception:
        return []


async def _fetch_adsblol(lat: float, lon: float, radius_nm: int = 400) -> list[dict]:
    """adsb.lol — free, no key, community-fed global."""
    url = f"https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}"
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Wardar/0.1"}) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            ac = r.json().get("ac") or []
            return [p for a in ac if (p := _norm_adsbfi(a))]
    except Exception:
        return []


async def _fetch_adsbfi(lat: float, lon: float, radius_nm: int = 400) -> list[dict]:
    """adsb.fi open data — free, no key, includes military."""
    # adsb.fi uses km radius
    radius_km = int(radius_nm * 1.852)
    url = f"https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{radius_km}"
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Wardar/0.1"}) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            ac = r.json().get("aircraft") or r.json().get("ac") or []
            return [p for a in ac if (p := _norm_adsbfi(a))]
    except Exception:
        return []


async def _fetch_mil_global() -> list[dict]:
    """
    Fetch ALL global military aircraft from dedicated /mil endpoints.
    airplanes.live and adsb.fi both expose a /mil endpoint that returns
    every military-squawking aircraft worldwide — no lat/lon targeting needed.
    """
    for url in [
        "https://api.airplanes.live/v2/mil",
        "https://opendata.adsb.fi/api/v2/mil",
    ]:
        try:
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Wardar/0.1"}) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                ac = data.get("ac") or data.get("aircraft") or []
                results = []
                for a in ac:
                    p = _norm_adsbfi(a)
                    if p:
                        p["military_flag"] = 1  # all from /mil endpoint are military
                        results.append(p)
                if results:
                    return results
        except Exception:
            continue
    return []


async def _fetch_hotspots_community() -> list[dict]:
    """Pull all hotspot regions from community feeds (airplanes.live primary, adsb.lol backup)."""
    tasks = []
    for lat, lon, radius, _ in _HOTSPOT_REGIONS:
        tasks.append(_fetch_airplaneslive(lat, lon, radius))
    chunks = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    results: list[dict] = []
    for chunk in chunks:
        if isinstance(chunk, Exception):
            continue
        for p in chunk:
            key = p["callsign"] + f"{p['lat']:.2f}{p['lon']:.2f}"
            if key not in seen:
                seen.add(key)
                results.append(p)

    # If thin coverage, supplement with adsb.lol on key hotspots
    if len(results) < 200:
        backup_tasks = [
            _fetch_adsblol(32.0, 35.0, 400),   # Middle East
            _fetch_adsblol(48.0, 35.0, 500),   # Ukraine
            _fetch_adsblol(55.0, 37.0, 500),   # Russia
        ]
        backup_chunks = await asyncio.gather(*backup_tasks, return_exceptions=True)
        for chunk in backup_chunks:
            if isinstance(chunk, Exception):
                continue
            for p in chunk:
                key = p["callsign"] + f"{p['lat']:.2f}{p['lon']:.2f}"
                if key not in seen:
                    seen.add(key)
                    results.append(p)

    return results


async def fetch() -> list[dict]:
    """Main entry point. Returns normalized position list.
    Priority: ADS-B Exchange (paid) → community feeds (free, military-capable) → OpenSky (fallback)
    """
    from core.engine import log

    results: list[dict] = []
    seen: set[str] = set()

    def _merge(new_positions: list[dict]):
        for p in new_positions:
            key = p["callsign"] + f"{p['lat']:.2f}{p['lon']:.2f}"
            if key not in seen:
                seen.add(key)
                results.append(p)

    # 0. Global military aircraft via dedicated /mil endpoints (always, free)
    mil_global = await _fetch_mil_global()
    _merge(mil_global)
    log(f"adsb: mil_global={len(mil_global)} military aircraft")

    # 1. ADS-B Exchange (paid key — best global + military coverage)
    if C.ENABLE_ADSB and C.ADSB_EXCHANGE_API_KEY:
        tasks = [
            _fetch_adsbexchange( 45,  0,  500),
            _fetch_adsbexchange(-45,  0,  500),
            _fetch_adsbexchange( 45, 180, 500),
            _fetch_adsbexchange(-45, 180, 500),
        ]
        for chunk in await asyncio.gather(*tasks):
            _merge(chunk)
        log(f"adsb: adsbexchange={len(results)}")

    # 2. Community free feeds — targeted at conflict hotspots, includes military
    if C.ENABLE_ADSB:
        community = await _fetch_hotspots_community()
        before = len(results)
        _merge(community)
        log(f"adsb: community_hotspots=+{len(results)-before} (total {len(results)})")

    # 3. OpenSky fallback (global fill, rate-limited, some military filtered)
    if C.ENABLE_OPENSKY and len(results) < 500:
        opensky = await _fetch_opensky()
        before = len(results)
        _merge(opensky)
        log(f"adsb: opensky=+{len(results)-before} (total {len(results)})")

    log(f"adsb: {len(results)} total positions")
    return results
