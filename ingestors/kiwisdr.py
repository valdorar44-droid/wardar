"""
Wardar — KiwiSDR HF Receiver Proximity (Signal #8)
====================================================
KiwiSDR is a network of ~1,000+ public shortwave software-defined radio
receivers worldwide. Military HF voice (guard freq 8.992/11.175 MHz,
ANDVT, HFGCS) is detectable on these receivers.

This module answers: "Are there public HF monitoring stations near the
ghost aircraft's projected position that could detect its radio traffic?"

Two useful distances:
  ≤ 500 km  — ground-wave / near skip: receiver directly under the aircraft
  ≤ 2000 km — first ionospheric hop: most common HF skip range

Data source: receiverbook.de public API — free, no auth, JSON.
Fallback: sdr.hu public list.
Both are community aggregators for public SDR receivers.
Cache: 1 hour (receiver list changes slowly).
"""
from __future__ import annotations

import math
import time
from typing import Optional

import httpx

_RECEIVERBOOK_URL = "https://www.receiverbook.de/api/receivers"
_SDRHU_URL        = "https://sdr.hu/gsdr"
_HEADERS          = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}
_CACHE_TTL        = 3600   # 1 hour
_GW_KM            = 500    # ground-wave / near-skip range
_SKIP_KM          = 2000   # first ionospheric hop range

# Module-level cache
_recv_cache: tuple[float, list[dict]] = (0.0, [])


def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R   = 6371.0
    p1  = math.radians(lat1)
    p2  = math.radians(lat2)
    dp  = math.radians(lat2 - lat1)
    dl  = math.radians(lon2 - lon1)
    a   = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(min(1.0, a)))


async def _fetch_receivers() -> list[dict]:
    """
    Fetch public KiwiSDR / HF receiver list. Returns list of dicts with
    at least {lat, lon, name}.  Empty on error.
    """
    global _recv_cache

    now = time.monotonic()
    if now - _recv_cache[0] < _CACHE_TTL:
        return _recv_cache[1]

    receivers: list[dict] = []

    # ── Primary: receiverbook.de ─────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as cl:
            r = await cl.get(_RECEIVERBOOK_URL,
                             params={"type": "kiwisdr", "limit": 1000})
        if r.status_code == 200:
            data = r.json()
            items = data if isinstance(data, list) else (data.get("data") or data.get("receivers") or [])
            for item in items:
                lat = item.get("lat") or item.get("latitude")
                lon = item.get("lon") or item.get("longitude")
                if lat is None or lon is None:
                    continue
                try:
                    receivers.append({
                        "lat":  float(lat),
                        "lon":  float(lon),
                        "name": item.get("name") or item.get("callsign") or "KiwiSDR",
                    })
                except Exception:
                    pass
    except Exception:
        pass

    # ── Fallback: sdr.hu ────────────────────────────────────────────────────
    if not receivers:
        try:
            async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as cl:
                r = await cl.get(_SDRHU_URL, params={"fmt": "json"})
            if r.status_code == 200:
                items = r.json()
                if isinstance(items, list):
                    for item in items:
                        lat = item.get("lat") or item.get("gps_latitude")
                        lon = item.get("lon") or item.get("gps_longitude")
                        if lat is None or lon is None:
                            continue
                        try:
                            receivers.append({
                                "lat":  float(lat),
                                "lon":  float(lon),
                                "name": item.get("name") or "HF-SDR",
                            })
                        except Exception:
                            pass
        except Exception:
            pass

    _recv_cache = (now, receivers)
    return receivers


async def find_nearby(ghost_lat: float, ghost_lon: float) -> str:
    """
    Find public HF receivers near the ghost position.
    Returns a description string or "" if nothing relevant found.

    Examples:
      "HF MONITOR 180km NW (ground-wave)"
      "HF MONITOR 1200km SE (skip range, 3 receivers)"
      ""
    """
    receivers = await _fetch_receivers()
    if not receivers:
        return ""

    gw_hits:   list[tuple[float, dict]] = []   # (km, recv) within ground-wave
    skip_hits: list[tuple[float, dict]] = []   # (km, recv) within HF skip

    for recv in receivers:
        km = _hav(ghost_lat, ghost_lon, recv["lat"], recv["lon"])
        if km <= _GW_KM:
            gw_hits.append((km, recv))
        elif km <= _SKIP_KM:
            skip_hits.append((km, recv))

    if gw_hits:
        gw_hits.sort(key=lambda x: x[0])
        nearest_km, nearest = gw_hits[0]
        # Cardinal direction from receiver to ghost
        dlat   = ghost_lat - nearest["lat"]
        dlon   = ghost_lon - nearest["lon"]
        card   = _bearing_card(dlat, dlon)
        n_more = len(gw_hits) - 1
        suffix = f", +{n_more} more" if n_more else ""
        return f"HF MONITOR {int(nearest_km)}km {card} (ground-wave{suffix})"

    if skip_hits:
        skip_hits.sort(key=lambda x: x[0])
        nearest_km, nearest = skip_hits[0]
        dlat   = ghost_lat - nearest["lat"]
        dlon   = ghost_lon - nearest["lon"]
        card   = _bearing_card(dlat, dlon)
        n_more = len(skip_hits) - 1
        suffix = f", +{n_more} more" if n_more else ""
        return f"HF MONITOR {int(nearest_km)}km {card} (skip range{suffix})"

    return ""


def _bearing_card(dlat: float, dlon: float) -> str:
    """Return cardinal direction (N/NE/E/…) from bearing deltas."""
    bearing = math.degrees(math.atan2(dlon, dlat)) % 360
    dirs    = ["N","NE","E","SE","S","SW","W","NW"]
    return dirs[round(bearing / 45) % 8]
