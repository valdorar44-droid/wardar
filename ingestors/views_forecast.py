"""
VIEWS (Violence & Impacts Early Warning System) conflict forecast ingestor.
Free academic REST API — no key required.
Provides monthly conflict probability forecasts 1-36 months ahead.
"""
from __future__ import annotations
from datetime import datetime, timezone

import httpx

from config import settings as C

# VIEWS API v3 — discover latest run, then fetch /{run}/cm
_VIEWS_BASE = "https://api.viewsforecasting.org"

# 2-letter ISO → 3-letter ISO mapping (subset covering conflict-prone countries)
_ISO2_TO_3 = {
    "AF": "AFG", "SY": "SYR", "IQ": "IRQ", "YE": "YEM", "SO": "SOM",
    "ET": "ETH", "SD": "SDN", "SS": "SSD", "CD": "COD", "NG": "NGA",
    "ML": "MLI", "CF": "CAF", "UA": "UKR", "RU": "RUS", "MM": "MMR",
    "PK": "PAK", "LY": "LBY", "MZ": "MOZ", "BI": "BDI", "CM": "CMR",
    "TD": "TCD", "ER": "ERI", "GN": "GIN", "HT": "HTI", "IN": "IND",
    "IR": "IRN", "KE": "KEN", "LB": "LBN", "MX": "MEX", "NE": "NER",
    "PS": "PSE", "TZ": "TZA", "UG": "UGA", "VE": "VEN", "ZW": "ZWE",
    "BF": "BFA", "MG": "MDG", "RW": "RWA", "GE": "GEO", "AZ": "AZE",
    "AM": "ARM", "ZM": "ZMB", "MR": "MRT", "GW": "GNB", "SL": "SLE",
    "LR": "LBR", "CI": "CIV", "KH": "KHM", "PH": "PHL", "LA": "LAO",
    "NP": "NPL", "BD": "BGD", "MZ": "MOZ",
}

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
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # Step 1: discover latest fatalities run
            meta = await client.get(f"{_VIEWS_BASE}/", headers={"Accept": "application/json", "User-Agent": "Wardar/0.1"})
            if meta.status_code != 200:
                log_warn(f"views: meta HTTP {meta.status_code}")
                return []
            all_runs = meta.json().get("runs", [])
            # Pick latest fatalities003 t01 run (monthly predictions)
            fat_runs = sorted([r for r in all_runs if r.startswith("fatalities003_") and r.endswith("_t01")], reverse=True)
            if not fat_runs:
                fat_runs = sorted([r for r in all_runs if "fatalities" in r and "_t01" in r], reverse=True)
            if not fat_runs:
                log_warn("views: no fatalities run found")
                return []
            run = fat_runs[0]

            # Step 2: fetch first 200 rows — country×month forecasts
            r = await client.get(
                f"{_VIEWS_BASE}/{run}/cm?limit=200",
                headers={"Accept": "application/json", "User-Agent": "Wardar/0.1"},
            )
            if r.status_code != 200:
                log_warn(f"views: forecast HTTP {r.status_code} (run={run})")
                return []
            data = r.json()
            forecasts = data.get("data") or []
    except Exception as exc:
        log_warn(f"views: {exc}")
        return []

    # Group by country — take first (earliest forecast) month per country
    seen_countries: set[str] = set()
    for fc in forecasts:
        try:
            isoab = fc.get("isoab") or ""            # 2-letter ISO
            name  = fc.get("name") or isoab
            # main_dich = probability of >25 battle deaths (0-1)
            # main_mean = expected fatalities
            prob  = float(fc.get("main_dich") or fc.get("main_mean_ln") or 0)
            mean  = float(fc.get("main_mean") or 0)
            month = int(fc.get("month") or 1)
            year  = int(fc.get("year") or 2025)

            if prob < 0.1:
                continue
            if isoab in seen_countries:
                continue
            seen_countries.add(isoab)

            # VIEWS isoab is already 3-letter ISO (e.g. "ETH", "UKR") — look up directly
            coords = _COUNTRY_CENTROIDS.get(isoab.upper())
            if not coords:
                continue
            lat, lon, country_name = coords

            risk_pct = int(prob * 100)
            results.append({
                "source":      "views_forecast",
                "title":       f"VIEWS FORECAST: {country_name} — {risk_pct}% conflict probability",
                "description": (
                    f"VIEWS fatality forecast for {name} in {year}-{month:02d}. "
                    f"Conflict probability: {risk_pct}%. Est. fatalities: {mean:.1f}. "
                    f"Model: {run}"
                ),
                "lat":         lat,
                "lon":         lon,
                "country":     country_name,
                "category":    "forecast",
                "raw_ts_utc":  now,
                "url":         "https://viewsforecasting.org",
                "extra":       f'{{"prob":{prob:.3f},"mean_fatalities":{mean:.1f},"forecast_month":"{year}-{month:02d}","run":"{run}"}}',
            })
        except Exception:
            pass

    log(f"views: {len(results)} conflict forecast markers")
    return results
