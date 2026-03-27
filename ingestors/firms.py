"""NASA FIRMS ingestor — fire/thermal anomaly detection

MODIS C6.1 active fire CSV, updated every ~3h. No API key required.
Detects: artillery fires, missile strikes, oil field burns, wildfire fronts.
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import csv, io, json
from datetime import datetime, timezone

import httpx

from config import settings as C

# MODIS C6.1 global 24h CSV — no key, updated every ~3h
_FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_24h.csv"

_TYPE_LABELS = {
    0: "Vegetation Fire",
    1: "Active Volcano",
    2: "Static Land Source",
    3: "Offshore",
}


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_FIRMS:
        return []
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            r = await client.get(_FIRMS_URL, headers={"User-Agent": "Wardar/0.1"})
            if r.status_code != 200:
                log_warn(f"firms: HTTP {r.status_code}")
                return []
    except Exception as exc:
        log_warn(f"firms: fetch error: {exc}")
        return []

    rows_parsed = []
    now = datetime.now(timezone.utc).isoformat()
    try:
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            try:
                lat        = float(row["latitude"])
                lon        = float(row["longitude"])
                brightness = float(row.get("brightness") or 0)
                frp        = float(row.get("frp") or 0)
                confidence = str(row.get("confidence") or "").strip().lower()
                acq_date   = str(row.get("acq_date") or "").strip()
                acq_time   = str(row.get("acq_time") or "").strip().zfill(4)
                satellite  = str(row.get("satellite") or "").strip()
                fire_type  = int(row.get("type") or 0)

                # Keep: high FRP, volcanoes/offshore, or high-confidence events
                # Minimum thresholds to reduce noise (17k+ detections globally)
                if fire_type not in (1, 3):  # not volcano / offshore
                    if confidence == "low" and frp < 20:
                        continue
                    if confidence == "nominal" and frp < 10:
                        continue

                rows_parsed.append((frp, lat, lon, brightness, frp, confidence, acq_date, acq_time, satellite, fire_type))
            except Exception:
                continue
    except Exception as exc:
        log_warn(f"firms: CSV parse error: {exc}")
        return []

    # Keep top 2000 by FRP (fire radiative power — most significant hotspots first)
    rows_parsed.sort(key=lambda x: x[0], reverse=True)
    rows_parsed = rows_parsed[:2000]

    results = []
    for (_, lat, lon, brightness, frp, confidence, acq_date, acq_time, satellite, fire_type) in rows_parsed:
        type_label = _TYPE_LABELS.get(fire_type, "Unknown")

        if acq_date and len(acq_time) >= 4:
            try:
                ts_str = f"{acq_date}T{acq_time[:2]}:{acq_time[2:]}:00+00:00"
                datetime.fromisoformat(ts_str)
            except Exception:
                ts_str = now
        else:
            ts_str = now

        title = f"THERMAL: {type_label} ({confidence.upper()}) FRP {frp:.0f}MW"

        results.append({
            "source":      "firms",
            "title":       title[:200],
            "description": f"Brightness {brightness:.0f}K | Confidence {confidence} | Satellite {satellite}",
            "lat":         round(lat, 4),
            "lon":         round(lon, 4),
            "country":     "",
            "category":    "fire",
            "raw_ts_utc":  ts_str,
            "url":         "",
            "extra":       json.dumps({
                "brightness": brightness,
                "frp": frp,
                "confidence": confidence,
                "satellite": satellite,
                "fire_type": fire_type,
            }),
        })

    log(f"firms: {len(results)} significant thermal detections (top by FRP)")
    return results
