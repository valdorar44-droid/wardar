"""
UNHCR refugee/displacement statistics ingestor.
Free API, no credentials required.
Fetches country-level displacement numbers and maps them to capital city coordinates
(since UNHCR data is country-level, not event-level).
"""
from __future__ import annotations
from datetime import datetime, timezone

import httpx

from config import settings as C

# Country capital coordinates for anchoring displacement markers
_CAPITALS = {
    "SYR": (33.51,  36.29,  "Syria"),
    "AFG": (34.52,  69.18,  "Afghanistan"),
    "UKR": (50.45,  30.52,  "Ukraine"),
    "SDN": (15.55,  32.53,  "Sudan"),
    "SOM": (2.05,   45.34,  "Somalia"),
    "COD": (-4.32,  15.32,  "DR Congo"),
    "MMR": (16.87,  96.19,  "Myanmar"),
    "ETH": (9.02,   38.74,  "Ethiopia"),
    "YEM": (15.35,  44.21,  "Yemen"),
    "PAK": (33.72,  73.06,  "Pakistan"),
    "VEN": (10.48, -66.88,  "Venezuela"),
    "NGA": (9.07,    7.40,  "Nigeria"),
    "MOZ": (-25.97, 32.58,  "Mozambique"),
    "CAF": (4.36,   18.56,  "Central African Republic"),
    "HTI": (18.54, -72.34,  "Haiti"),
    "IRQ": (33.34,  44.39,  "Iraq"),
    "MLI": (12.65,  -8.00,  "Mali"),
    "CMR": (3.87,   11.52,  "Cameroon"),
    "SSD": (4.85,   31.62,  "South Sudan"),
    "LBY": (32.90,  13.18,  "Libya"),
    # Additional active displacement crises (2024+)
    "PSE": (31.90,  35.20,  "Palestine"),
    "LBN": (33.89,  35.50,  "Lebanon"),
    "MYS": (3.14,  101.69,  "Malaysia"),
    "IRN": (35.68,  51.39,  "Iran"),
    "RUS": (55.75,  37.62,  "Russia"),
    "BFA": (12.36,  -1.53,  "Burkina Faso"),
    "GIN": (9.54,  -13.68,  "Guinea"),
    "TCD": (12.11,  15.04,  "Chad"),
    "MDG": (-18.91, 47.54,  "Madagascar"),
    "ZWE": (-17.82, 31.05,  "Zimbabwe"),
    "ZMB": (-15.42, 28.28,  "Zambia"),
    "KEN": (-1.28,  36.82,  "Kenya"),
    "TZA": (-6.77,  39.27,  "Tanzania"),
    "UGA": (0.32,   32.58,  "Uganda"),
    "RWA": (-1.94,  30.06,  "Rwanda"),
    "GEO": (41.69,  44.83,  "Georgia"),
    "AZE": (40.41,  49.87,  "Azerbaijan"),
    "ARM": (40.18,  44.51,  "Armenia"),
}

_UNHCR_URL = (
    "https://api.unhcr.org/population/v1/population/"
    "?limit=100&dataset=population&displayType=totals"
    "&yearFrom=2022&yearTo=2024&sortBy=displaced_total&sortOrder=desc"
)


async def fetch() -> list[dict]:
    """Fetch UNHCR displacement data. Returns list of event dicts."""
    if not C.ENABLE_UNHCR:
        return []

    from core.engine import log, log_warn
    results = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                _UNHCR_URL,
                headers={"Accept": "application/json", "User-Agent": "Wardar/0.1"},
            )
            if r.status_code != 200:
                log_warn(f"unhcr: HTTP {r.status_code}")
                return []
            items = r.json().get("items") or []
    except Exception as exc:
        log_warn(f"unhcr: fetch failed: {exc}")
        return []

    for item in items:
        try:
            country_obj = item.get("country") or item.get("geomaster") or {}
            iso3 = country_obj.get("iso3Code") or item.get("iso3Code") or ""
            name = country_obj.get("name") or item.get("name") or iso3
            total_displaced = (
                (item.get("refugees_under_UNHCR_mandate") or 0) +
                (item.get("asylum_seekers") or 0) +
                (item.get("idps_UNHCR") or 0)
            )
            if total_displaced < 10000:
                continue  # skip trivially small numbers

            coords = _CAPITALS.get(iso3)
            if not coords:
                continue
            lat, lon, country_name = coords

            fmt = f"{total_displaced:,}"
            results.append({
                "source":      "unhcr",
                "title":       f"{country_name}: {fmt} displaced",
                "description": (
                    f"Refugees: {item.get('refugees_under_UNHCR_mandate', 0):,} | "
                    f"Asylum seekers: {item.get('asylum_seekers', 0):,} | "
                    f"IDPs: {item.get('idps_UNHCR', 0):,}"
                ),
                "lat":         lat,
                "lon":         lon,
                "country":     country_name,
                "category":    "displacement",
                "raw_ts_utc":  now,
                "url":         f"https://www.unhcr.org/countries/{iso3.lower()}",
                "extra":       "{}",
            })
        except Exception:
            pass

    log(f"unhcr: {len(results)} displacement markers")
    return results
