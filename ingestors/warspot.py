"""WarSpotting ingestor — Ukraine equipment losses

Fetches photo/video-confirmed Russian equipment losses from ukr.warspotting.net.
Every entry is visually verified (destroyed, damaged, abandoned, or captured).
Free, no API key (User-Agent header required).

URL: https://ukr.warspotting.net/api/losses/russia/recent/
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import httpx

from config import settings as C

_BASE = "https://ukr.warspotting.net/api"
_HEADERS = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}

# Status → emoji label
_STATUS_LABEL = {
    "destroyed":  "DESTROYED",
    "damaged":    "DAMAGED",
    "abandoned":  "ABANDONED",
    "captured":   "CAPTURED",
}

# Ukraine oblasts → approximate centroids for map placement when no coords given
_OBLAST_LL: dict[str, tuple[float, float]] = {
    "Zaporizhzhia": (47.84, 35.14), "Donetsk": (48.01, 37.80),
    "Luhansk": (48.57, 39.30),      "Kherson": (46.64, 32.62),
    "Mykolaiv": (46.98, 31.99),     "Kharkiv": (49.99, 36.23),
    "Dnipropetrovsk": (48.46, 35.04),"Zhytomyr": (50.25, 28.66),
    "Kyiv": (50.45, 30.52),          "Sumy": (50.91, 34.80),
    "Chernihiv": (51.49, 31.29),     "Poltava": (49.59, 34.55),
    "Odesa": (46.48, 30.73),         "Kharkiv Oblast": (49.99, 36.23),
}


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_WARSPOT:
        return []

    # Fetch recent losses + daily stats
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{_BASE}/losses/russia/recent/", headers=_HEADERS)
        if r.status_code != 200:
            log_warn(f"warspot: HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as exc:
        log_warn(f"warspot: fetch error: {exc}")
        return []

    results = []
    now = datetime.now(timezone.utc).isoformat()

    losses = data if isinstance(data, list) else (data.get("results") or data.get("losses") or [])

    for item in losses:
        try:
            item_id   = str(item.get("id", ""))
            status    = (item.get("status") or "destroyed").lower()
            category  = item.get("category") or item.get("type") or "equipment"
            date_str  = item.get("date") or item.get("lost_date") or ""
            location  = item.get("location") or item.get("area") or ""
            url       = item.get("url") or item.get("source_url") or ""
            comment   = item.get("comment") or item.get("description") or ""

            # Coordinates — warspot may provide or we use oblast lookup
            lat = item.get("lat") or item.get("latitude")
            lon = item.get("lon") or item.get("longitude")

            if lat is None or lon is None:
                # Try oblast name lookup
                for oblast, coords in _OBLAST_LL.items():
                    if oblast.lower() in (location or "").lower():
                        lat, lon = coords
                        break

            # Skip if truly no location
            if lat is None or lon is None:
                continue

            label = _STATUS_LABEL.get(status, status.upper())
            title = f"🇺🇦 {label}: {category}"
            if location:
                title += f" — {location}"

            ts = now
            if date_str:
                try:
                    ts = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
                except Exception:
                    pass

            results.append({
                "source":      "warspot",
                "title":       title[:200],
                "description": f"{category} {label.lower()} near {location}. {comment}"[:500],
                "lat":         float(lat),
                "lon":         float(lon),
                "country":     "UA",
                "category":    "conflict",
                "raw_ts_utc":  ts,
                "url":         url,
                "extra": json.dumps({
                    "id":       item_id,
                    "status":   status,
                    "category": category,
                    "location": location,
                }),
            })
        except Exception:
            continue

    log(f"warspot: {len(results)} confirmed equipment losses")
    return results
