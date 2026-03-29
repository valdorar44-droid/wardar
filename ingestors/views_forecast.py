"""
VIEWS (Violence & Impacts Early Warning System) conflict forecast ingestor.
Free academic REST API — no key required.
Provides monthly conflict probability forecasts 1-36 months ahead.
"""
from __future__ import annotations
from datetime import datetime, timezone

import httpx

from config import settings as C

# VIEWS API v2 — country-month forecasts (public beta)
_VIEWS_URL = "https://api.viewsforecasting.org/monthly_forecasts/cm/"
# Fallback: use pre-built VIEWS dataset endpoint
_VIEWS_FALLBACK = (
    "https://raw.githubusercontent.com/prio-data/viewser/main/"
    "tests/fixtures/cm_features.json"
)

# Country centroids for mapping forecast results
_COUNTRY_CENTROIDS = {
    "AFG": (33.0, 65.0, "Afghanistan"),
    "SYR": (35.0, 38.0, "Syria"),
    "IRQ": (33.0, 44.0, "Iraq"),
    "YEM": (16.0, 48.0, "Yemen"),
    "SOM": (6.0,  46.0, "Somalia"),
    "ETH": (9.0,  40.0, "Ethiopia"),
    "SDN": (15.0, 30.0, "Sudan"),
    "SSD": (7.0,  30.0, "South Sudan"),
    "COD": (-3.0, 23.0, "DR Congo"),
    "NGA": (10.0,  8.0, "Nigeria"),
    "MLI": (17.0, -4.0, "Mali"),
    "CAF": (7.0,  21.0, "Central African Republic"),
    "UKR": (49.0, 32.0, "Ukraine"),
    "RUS": (61.0, 60.0, "Russia"),
    "MMR": (17.0, 96.0, "Myanmar"),
    "PAK": (30.0, 70.0, "Pakistan"),
    "AFR": ( 8.0, 34.0, "Africa region"),
    "MEA": (25.0, 45.0, "Middle East"),
    "LBY": (27.0, 17.0, "Libya"),
    "MOZ": (-17.0, 35.0, "Mozambique"),
}


async def fetch() -> list[dict]:
    """Fetch VIEWS conflict forecast events."""
    if not C.ENABLE_VIEWS:
        return []

    from core.engine import log, log_warn
    now = datetime.now(timezone.utc).isoformat()
    results = []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                _VIEWS_URL,
                headers={"Accept": "application/json", "User-Agent": "Wardar/0.1"},
                follow_redirects=True,
            )
            if r.status_code != 200:
                log_warn(f"views: API HTTP {r.status_code} — skipping forecast layer")
                return []
            data = r.json()
            forecasts = data if isinstance(data, list) else (data.get("data") or data.get("forecasts") or [])
    except Exception as exc:
        log_warn(f"views: {exc}")
        return []

    for fc in forecasts[:50]:  # limit to top 50 high-conflict predictions
        try:
            country_id = fc.get("country_id") or fc.get("iso3") or fc.get("country") or ""
            prob = float(fc.get("prob_low") or fc.get("probability") or fc.get("fatality_risk") or 0)
            step = int(fc.get("step") or fc.get("month_ahead") or 1)

            if prob < 0.1:
                continue  # only show elevated risk

            coords = _COUNTRY_CENTROIDS.get(str(country_id).upper())
            if not coords:
                continue
            lat, lon, country_name = coords

            risk_pct = int(prob * 100)
            results.append({
                "source":      "views_forecast",
                "title":       f"VIEWS FORECAST: {country_name} — {risk_pct}% conflict risk in +{step}mo",
                "description": f"VIEWS conflict probability forecast. Step: +{step} months. Risk: {risk_pct}%",
                "lat":         lat,
                "lon":         lon,
                "country":     country_name,
                "category":    "forecast",
                "raw_ts_utc":  now,
                "url":         "https://viewsforecasting.org",
                "extra":       f'{{"prob":{prob:.3f},"step":{step}}}',
            })
        except Exception:
            pass

    log(f"views: {len(results)} conflict forecast markers")
    return results
