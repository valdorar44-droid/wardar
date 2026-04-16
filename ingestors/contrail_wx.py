"""
Wardar — Contrail Favorability (Signal #6)
==========================================
High-altitude aircraft (FL350+) produce *persistent* contrails when the upper
troposphere is cold AND humid enough. Persistent contrails are a visual signature
that confirms the aircraft is actively at cruise altitude — useful corroboration
for a ghost track.

Appleman Criterion (simplified):
  200 hPa level ≈ FL350-FL380 (typical cruise for military transports)
  • Temperature < −40 °C  →  cold enough for ice crystal formation
  • Relative humidity (ice) > 60 %  →  moist enough for persistence

  Both  → CONTRAIL LIKELY   (persistent, trackable on satellite)
  One   → CONTRAIL POSSIBLE (short-lived)
  Neither → not returned (not worth noting)

Data source: open-meteo.com — free, no API key, global coverage.
  GET /v1/forecast?latitude=…&longitude=…&hourly=temperature_200hPa,relative_humidity_200hPa
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_HEADERS        = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}
_CACHE_TTL      = 1800   # 30 min — upper-air data changes slowly

# Grid snap (degrees) — avoids duplicate queries for nearby positions
_GRID_DEG = 2.0

# Cache: (grid_lat, grid_lon) → (monotonic_time, result_string)
_cache: dict[tuple[float, float], tuple[float, str]] = {}


def _snap(v: float, grid: float = _GRID_DEG) -> float:
    """Snap to nearest grid cell centre."""
    return round(round(v / grid) * grid, 1)


async def get_contrail_status(lat: float, lon: float,
                               altitude_ft: Optional[float] = None) -> str:
    """
    Return contrail favorability string for the given position.
    Returns one of:
      "CONTRAIL LIKELY (−52°C / 74% RH@200hPa)"
      "CONTRAIL POSSIBLE (−38°C / 72% RH@200hPa)"
      ""   — below FL250, data unavailable, or contrail unlikely
    """
    # Only meaningful for high-altitude aircraft
    if altitude_ft is not None and altitude_ft < 25_000:
        return ""

    glat = _snap(lat)
    glon = _snap(lon)
    key  = (glat, glon)

    now    = time.monotonic()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    try:
        params = {
            "latitude":       glat,
            "longitude":      glon,
            "hourly":         "temperature_200hPa,relative_humidity_200hPa",
            "forecast_days":  1,
            "timezone":       "UTC",
        }
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as cl:
            r = await cl.get(_OPEN_METEO_URL, params=params)
        if r.status_code != 200:
            return ""

        hourly = r.json().get("hourly") or {}
        temps  = hourly.get("temperature_200hPa") or []
        humids = hourly.get("relative_humidity_200hPa") or []

        if not temps or not humids:
            return ""

        temp_c = float(temps[0])
        humid  = float(humids[0])

        cold  = temp_c < -40.0
        moist = humid  > 60.0

        if cold and moist:
            result = f"CONTRAIL LIKELY ({temp_c:.0f}°C/{humid:.0f}%RH)"
        elif cold or moist:
            result = f"CONTRAIL POSSIBLE ({temp_c:.0f}°C/{humid:.0f}%RH)"
        else:
            result = ""

        _cache[key] = (now, result)
        return result

    except Exception:
        return ""
