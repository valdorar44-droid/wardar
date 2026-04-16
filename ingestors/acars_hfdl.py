"""
Wardar — ACARS / HFDL Ingestor
================================
Pulls position reports from the airframes.io community aggregator.
Free, no API key required.

Two signal types:
  VHF ACARS  — 129.125 / 130.025 / 130.450 MHz
                Range ~200–300 nm from receiver
                C-17, KC-135, P-8, E-8, tankers
  HFDL        — 2–22 MHz shortwave (ionospheric skip = global)
                Covers oceanic / polar routes where ADS-B has no receivers
                C-17, C-5, KC-135 use HF heavily over oceans

API: https://api.airframes.io (free, community, no key)
  GET /messages            — recent messages, filter by label/source
  GET /aircraft/{hex}/messages — targeted lookup by ICAO24

Position-bearing ARINC 429 labels:
  H1  — FANS/ACARS, most common for position reports
  15  — position update (some operators)
  10  — departure/arrival + position
  58  — position report (cargo operators)

Position text formats (handled by parser):
  N30.5 W123.4         — degree decimal with hemisphere
  N3030.0W12324.0      — degree-minute with hemisphere
  3030.0N/12324.0W     — degree-minute slash-separated
  30.5,-123.4          — signed decimal
  N30°30'W123°24'      — degree-minute-second
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from config import settings as C

# ── Constants ──────────────────────────────────────────────────────────────────
_BASE_URL  = "https://api.airframes.io"
_HEADERS   = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}
_TIMEOUT   = 12

# Labels that commonly contain position data
_POS_LABELS = {"H1", "15", "10", "58", "H2", "16", "5Z", "QE", "QF", "QM"}
_POS_SOURCES = {"hfdl", "vhf", "aero", "vdl2"}

# Cache for per-aircraft lookups (hex → (monotonic_time, result))
_aircraft_cache: dict[str, tuple[float, Optional[dict]]] = {}
_AIRCRAFT_TTL = 90   # seconds

# Global messages cache (avoid re-fetching within window)
_global_cache: tuple[float, list[dict]] = (0.0, [])
_GLOBAL_TTL  = 120   # seconds


# ── Position text parsers ─────────────────────────────────────────────────────

# Pattern 1: N30.500 W123.400 or N30.500W123.400
_RE_DD_HEM = re.compile(
    r'([NS])\s*(\d{1,3}(?:\.\d+)?)\s*([EW])\s*(\d{1,3}(?:\.\d+)?)'
)
# Pattern 2: N3030.0 W12324.0 (degree-minute packed, used in ACARS)
_RE_DM_HEM = re.compile(
    r'([NS])(\d{2,3})(\d{2}\.\d+)\s*/?([EW])(\d{2,3})(\d{2}\.\d+)'
)
# Pattern 3: signed decimal  30.5,-123.4 or 30.5 -123.4
_RE_SIGNED = re.compile(
    r'([+-]?\d{1,3}\.\d{3,})\s*[,\s]\s*([+-]?\d{1,3}\.\d{3,})'
)
# Pattern 4: slash-separated DM  3030.0N/12324.0W
_RE_DM_SLASH = re.compile(
    r'(\d{4,6}\.\d+)([NS])/(\d{4,7}\.\d+)([EW])'
)
# Pattern 5: HFDL structured lat/lon field in decoded data
# (handled via dict field extraction, not regex)


def _dm_to_dd(deg_part: str, min_part: str, hemi: str) -> float:
    """Convert degree + minute strings + hemisphere to signed decimal degrees."""
    d = float(deg_part)
    m = float(min_part)
    dd = d + m / 60.0
    if hemi in ('S', 'W'):
        dd = -dd
    return dd


def _parse_position(text: str) -> Optional[tuple[float, float]]:
    """
    Attempt to extract (lat, lon) from an ACARS message text string.
    Returns None if no valid position found.
    """
    if not text:
        return None

    # Pattern 1: hemisphere + decimal degrees  N30.5 W123.4
    m = _RE_DD_HEM.search(text)
    if m:
        lat = float(m.group(2)) * (-1 if m.group(1) == 'S' else 1)
        lon = float(m.group(4)) * (-1 if m.group(3) == 'W' else 1)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon

    # Pattern 2: N3030.0W12324.0 (packed deg-min)
    m = _RE_DM_HEM.search(text)
    if m:
        lat = _dm_to_dd(m.group(2), m.group(3), m.group(1))
        lon = _dm_to_dd(m.group(5), m.group(6), m.group(4))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon

    # Pattern 3: slash-separated  3030.0N/12324.0W
    m = _RE_DM_SLASH.search(text)
    if m:
        raw_lat = m.group(1)          # e.g. "3030.0"
        lat_deg = raw_lat[:-7]        # all but last 7 chars = degrees
        lat_min = raw_lat[-7:]        # last 7 = MM.DDD
        try:
            lat = _dm_to_dd(lat_deg or '0', lat_min, m.group(2))
            raw_lon = m.group(3)
            lon_deg = raw_lon[:-7]
            lon_min = raw_lon[-7:]
            lon = _dm_to_dd(lon_deg or '0', lon_min, m.group(4))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except Exception:
            pass

    # Pattern 4: signed decimal  30.500,-123.400
    m = _RE_SIGNED.search(text)
    if m:
        try:
            lat = float(m.group(1))
            lon = float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
                return lat, lon
        except Exception:
            pass

    return None


def _parse_altitude(text: str) -> Optional[float]:
    """Extract altitude in feet from ACARS text. Handles FL and feet."""
    # FL350 → 35000 ft
    m = re.search(r'FL\s*(\d{2,3})', text, re.I)
    if m:
        return float(m.group(1)) * 100
    # F350, F35000
    m = re.search(r'F(\d{3,5})', text)
    if m:
        v = float(m.group(1))
        return v * 100 if v < 1000 else v
    # A350 (altitude in 100s)
    m = re.search(r'A(\d{3})\b', text)
    if m:
        return float(m.group(1)) * 100
    return None


def _parse_speed(text: str) -> Optional[float]:
    """Extract ground speed (kts) from ACARS text."""
    # S480, GS480
    m = re.search(r'(?:GS|S)(\d{3})\b', text)
    if m:
        v = float(m.group(1))
        if 100 <= v <= 650:
            return v
    return None


def _extract_from_data(data: dict) -> Optional[tuple[float, float, Optional[float], Optional[float], Optional[float]]]:
    """
    Pull structured position from HFDL/AERO decoded data dict.
    Returns (lat, lon, alt_ft, speed_kts, heading_deg) or None.
    """
    if not data:
        return None

    # HFDL decoded structure: data.pos.lat / data.pos.lon
    pos = data.get("pos") or data.get("position") or {}
    lat = pos.get("lat") or pos.get("latitude")
    lon = pos.get("lon") or pos.get("longitude")
    if lat is not None and lon is not None:
        try:
            lat_f, lon_f = float(lat), float(lon)
            if -90 <= lat_f <= 90 and -180 <= lon_f <= 180 and not (lat_f == 0 and lon_f == 0):
                alt_raw = pos.get("alt") or pos.get("altitude") or data.get("altitude")
                spd_raw = pos.get("spd") or pos.get("speed") or data.get("gs")
                hdg_raw = pos.get("hdg") or pos.get("heading") or data.get("track")
                return (
                    lat_f, lon_f,
                    float(alt_raw) * 100 if alt_raw and float(alt_raw) < 1000 else (float(alt_raw) if alt_raw else None),
                    float(spd_raw) if spd_raw is not None else None,
                    float(hdg_raw) if hdg_raw is not None else None,
                )
        except Exception:
            pass

    # Flat structure: data.latitude / data.longitude
    lat = data.get("latitude") or data.get("lat")
    lon = data.get("longitude") or data.get("lon")
    if lat is not None and lon is not None:
        try:
            lat_f, lon_f = float(lat), float(lon)
            if -90 <= lat_f <= 90 and -180 <= lon_f <= 180 and not (lat_f == 0 and lon_f == 0):
                return (lat_f, lon_f, None, None, None)
        except Exception:
            pass

    return None


def _norm_message(msg: dict) -> Optional[dict]:
    """
    Normalize one airframes.io message to a Wardar position dict.
    Returns None if no position could be extracted.
    """
    try:
        source_type = (msg.get("source") or "").lower()
        label       = (msg.get("label")  or "").upper()
        text        = msg.get("text") or msg.get("msg") or ""
        data        = msg.get("data") or {}
        tail        = (msg.get("tail") or msg.get("registration") or "").strip()
        flight      = (msg.get("flight") or msg.get("callsign") or "").strip()
        icao_hex    = (msg.get("icao") or msg.get("icao24") or "").strip()
        ts_str      = msg.get("timestamp") or msg.get("time") or ""

        # Skip non-position labels unless HFDL/AERO (those carry pos more broadly)
        if source_type in ("vhf", "vdl2") and label not in _POS_LABELS:
            return None

        # Try structured decode first (most reliable)
        pos_decoded = _extract_from_data(data)
        if pos_decoded:
            lat, lon, alt_ft, speed_kts, heading_deg = pos_decoded
        else:
            # Fall back to text parsing
            pos_text = _parse_position(text)
            if not pos_text:
                return None
            lat, lon = pos_text
            alt_ft    = _parse_altitude(text)
            speed_kts = _parse_speed(text)
            heading_deg = None

        callsign = flight or tail or icao_hex or "ACARS"
        if source_type == "hfdl":
            wardar_source = "hfdl"
        elif source_type == "aero":
            wardar_source = "aero"
        else:
            wardar_source = "acars"

        return {
            "source":        wardar_source,
            "callsign":      callsign,
            "type":          "aircraft",
            "lat":           round(lat, 5),
            "lon":           round(lon, 5),
            "altitude_ft":   round(alt_ft) if alt_ft is not None else None,
            "speed_kts":     round(speed_kts, 1) if speed_kts is not None else None,
            "heading_deg":   round(heading_deg, 1) if heading_deg is not None else None,
            "country":       "",
            "military_flag": 1,   # we only call this for mil aircraft
            "extra": json.dumps({
                "icao":         icao_hex,
                "label":        label,
                "freq":         msg.get("freq", ""),
                "acars_source": source_type,
                "tail":         tail,
                "text_snippet": text[:80] if text else "",
            }),
        }
    except Exception:
        return None


# ── Per-aircraft targeted lookup (used by ghost tracker) ─────────────────────

async def lookup_aircraft(hex_code: str, callsign: str = "") -> Optional[dict]:
    """
    Query airframes.io for the most recent position-bearing ACARS/HFDL message
    for a specific aircraft (by ICAO24 hex).
    Returns a partial position dict or None. Cached for _AIRCRAFT_TTL seconds.
    """
    if not hex_code:
        return None

    now = time.monotonic()
    cached = _aircraft_cache.get(hex_code)
    if cached and (now - cached[0]) < _AIRCRAFT_TTL:
        return cached[1]

    url = f"{_BASE_URL}/aircraft/{hex_code.lower()}/messages"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as cl:
            r = await cl.get(url, params={"limit": 50})
        if r.status_code == 404:
            _aircraft_cache[hex_code] = (now, None)
            return None
        if r.status_code != 200:
            _aircraft_cache[hex_code] = (now, None)
            return None
        body = r.json()
        messages = body.get("messages") or body if isinstance(body, list) else []
    except Exception:
        _aircraft_cache[hex_code] = (now, None)
        return None

    # Walk messages newest-first, find most recent with a position
    for msg in messages:
        p = _norm_message(msg)
        if p:
            # Stamp with source signal type for display in popup
            src_type = (msg.get("source") or "vhf").lower()
            if src_type == "hfdl":
                acars_src_label = "HFDL"
            elif src_type == "aero":
                acars_src_label = "AERO"
            else:
                acars_src_label = "ACARS"
            result = {
                "lat":          p["lat"],
                "lon":          p["lon"],
                "altitude_ft":  p["altitude_ft"],
                "speed_kts":    p["speed_kts"],
                "heading_deg":  p["heading_deg"],
                "acars_source": acars_src_label,
                "freq":         msg.get("freq", ""),
                "label":        (msg.get("label") or "").upper(),
            }
            _aircraft_cache[hex_code] = (now, result)
            return result

    _aircraft_cache[hex_code] = (now, None)
    return None


# ── Proactive global poll (standalone ingestor) ───────────────────────────────

async def fetch() -> list[dict]:
    """
    Poll airframes.io for recent ACARS/HFDL position reports.
    Returns list of Wardar position dicts (military only — filtered by DB hex list).
    Called every ACARS_HFDL_INTERVAL_SEC from engine.
    """
    from core.engine import log, log_warn
    from db import store as DB
    from ingestors.adsb import _is_military

    global _global_cache

    if not getattr(C, "ENABLE_ACARS_HFDL", True):
        return []

    now_mono = time.monotonic()
    if now_mono - _global_cache[0] < _GLOBAL_TTL:
        return []   # still fresh from last tick

    # Pull recent position-bearing messages from both VHF and HFDL
    results: list[dict] = []
    seen_callsigns: set[str] = set()

    # ── HFDL — oceanic / global ────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as cl:
            r = await cl.get(f"{_BASE_URL}/messages",
                             params={"source": "hfdl", "limit": 200})
        if r.status_code == 200:
            body  = r.json()
            msgs  = body.get("messages") or (body if isinstance(body, list) else [])
            for msg in msgs:
                icao = (msg.get("icao") or "").strip()
                cs   = (msg.get("flight") or msg.get("callsign") or "").strip()
                if not _is_military(icao, cs):
                    continue
                p = _norm_message(msg)
                if p and p["callsign"] not in seen_callsigns:
                    seen_callsigns.add(p["callsign"])
                    results.append(p)
    except Exception as exc:
        log_warn(f"acars_hfdl: HFDL poll error: {exc}")

    # ── VHF ACARS — label H1 (FANS position reports) ──────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as cl:
            r = await cl.get(f"{_BASE_URL}/messages",
                             params={"label": "H1", "limit": 200})
        if r.status_code == 200:
            body  = r.json()
            msgs  = body.get("messages") or (body if isinstance(body, list) else [])
            for msg in msgs:
                icao = (msg.get("icao") or "").strip()
                cs   = (msg.get("flight") or msg.get("callsign") or "").strip()
                if not _is_military(icao, cs):
                    continue
                p = _norm_message(msg)
                if p and p["callsign"] not in seen_callsigns:
                    seen_callsigns.add(p["callsign"])
                    results.append(p)
    except Exception as exc:
        log_warn(f"acars_hfdl: VHF ACARS poll error: {exc}")

    # ── Inmarsat AERO (L-band satellite — military transports over oceans) ────
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as cl:
            r = await cl.get(f"{_BASE_URL}/messages",
                             params={"source": "aero", "limit": 200})
        if r.status_code == 200:
            body  = r.json()
            msgs  = body.get("messages") or (body if isinstance(body, list) else [])
            for msg in msgs:
                icao = (msg.get("icao") or "").strip()
                cs   = (msg.get("flight") or msg.get("callsign") or "").strip()
                if not _is_military(icao, cs):
                    continue
                p = _norm_message(msg)
                if p and p["callsign"] not in seen_callsigns:
                    seen_callsigns.add(p["callsign"])
                    results.append(p)
    except Exception as exc:
        log_warn(f"acars_hfdl: AERO poll error: {exc}")

    _global_cache = (now_mono, results)

    if results:
        hfdl_ct = sum(1 for r in results if r['source'] == 'hfdl')
        aero_ct = sum(1 for r in results if r['source'] == 'aero')
        acars_ct = len(results) - hfdl_ct - aero_ct
        log(f"acars_hfdl: {len(results)} mil positions ({hfdl_ct} HFDL, {aero_ct} AERO, {acars_ct} ACARS)")
    return results
