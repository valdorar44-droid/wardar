"""Safecast radiation monitoring ingestor

Fetches global radiation measurements from the Safecast crowdsourced network.
5,000 devices, 100+ countries, ~66,000 measurements/day.
Free, no API key required, CC0 public domain data.

Normal ambient radiation: 10-40 CPM (Counts Per Minute) at sea level.
Alert threshold: configurable via SAFECAST_ALERT_CPM (default 100 CPM).

Only emits events for readings above the alert threshold to avoid noise.
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta

import httpx

from config import settings as C

_BASE = "https://api.safecast.org"
_HEADERS = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}

# Radiation level labels
def _rad_label(cpm: float) -> str:
    if cpm >= 1000: return "CRITICAL RADIATION"
    if cpm >= 300:  return "HIGH RADIATION"
    if cpm >= 100:  return "ELEVATED RADIATION"
    return "ABNORMAL RADIATION"


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_SAFECAST:
        return []

    threshold = C.SAFECAST_ALERT_CPM

    # Fetch recent measurements (last 2 hours, global)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "order":       "captured_at desc",
        "per_page":    1000,
        "captured_after": cutoff,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{_BASE}/measurements.json",
                params=params,
                headers=_HEADERS,
            )
        if r.status_code != 200:
            log_warn(f"safecast: HTTP {r.status_code}")
            return []
        measurements = r.json()
    except Exception as exc:
        log_warn(f"safecast: fetch error: {exc}")
        return []

    if not isinstance(measurements, list):
        log_warn("safecast: unexpected response format")
        return []

    results = []
    seen_cells: set[str] = set()  # deduplicate by ~1° grid cell

    for m in measurements:
        try:
            val  = m.get("value")
            unit = (m.get("unit") or "").lower()
            lat  = m.get("latitude")
            lon  = m.get("longitude")

            if val is None or lat is None or lon is None:
                continue

            cpm = float(val)

            # Convert µSv/h to CPM approximately (1 µSv/h ≈ 120 CPM for Cs-137)
            if "usv" in unit or "μsv" in unit:
                cpm = cpm * 120

            # Only report elevated readings
            if cpm < threshold:
                continue

            lat_f = float(lat)
            lon_f = float(lon)

            # Deduplicate within ~1° grid cells to avoid flooding
            cell = f"{int(lat_f)}_{int(lon_f)}"
            if cell in seen_cells:
                continue
            seen_cells.add(cell)

            ts = m.get("captured_at") or datetime.now(timezone.utc).isoformat()
            label = _rad_label(cpm)
            device = m.get("device_id") or m.get("id") or ""
            location_name = m.get("location_name") or ""

            results.append({
                "source":      "safecast",
                "title":       f"☢ {label}: {cpm:.0f} CPM",
                "description": (
                    f"{cpm:.0f} CPM ({cpm/120:.2f} µSv/h estimated) | "
                    f"Threshold: {threshold} CPM | "
                    f"{location_name or f'({lat_f:.2f}, {lon_f:.2f})'}"
                )[:500],
                "lat":         lat_f,
                "lon":         lon_f,
                "country":     "",
                "category":    "radiation",
                "raw_ts_utc":  ts,
                "url":         f"https://safecast.org/tilemap/?location={lat_f},{lon_f}&zoom=8",
                "extra": json.dumps({
                    "cpm":       round(cpm, 1),
                    "usvh":      round(cpm / 120, 4),
                    "unit":      unit,
                    "device_id": str(device),
                    "location":  location_name,
                }),
            })
        except Exception:
            continue

    log(f"safecast: {len(results)} elevated radiation readings (≥{threshold} CPM) from {len(measurements)} total")
    return results
