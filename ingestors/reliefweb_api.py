"""ReliefWeb structured API ingestor

Uses the ReliefWeb REST API (api.reliefweb.int) for structured humanitarian
intelligence — not the RSS feed already in osint.py. The API provides:
  - Geographic coordinates (country centroid level)
  - Disaster type classification (Conflict, Flood, Epidemic, etc.)
  - Relevance scoring
  - Cross-referenced OCHA/HDX data

Focuses on worst-current humanitarian crises:
  Sudan, DRC, Yemen, Syria, Myanmar, Somalia, Gaza, Ukraine, Sahel

Free, no API key required. Returns list of event dicts.
Source ID: 'reliefweb_api' (distinct from 'reliefweb' RSS in osint.py)
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta

import httpx

from config import settings as C

_BASE = "https://api.reliefweb.int/v1/reports"
_HEADERS = {"User-Agent": "Wardar/0.1 (+https://wardar.app) humanitarian-monitor"}

# Priority crisis countries (ISO3 codes)
_PRIORITY_COUNTRIES = [
    "SDN",  # Sudan
    "COD",  # DRC
    "YEM",  # Yemen
    "SYR",  # Syria
    "MMR",  # Myanmar
    "SOM",  # Somalia
    "PSE",  # Palestine/Gaza
    "ETH",  # Ethiopia
    "AFG",  # Afghanistan
    "HTI",  # Haiti
    "MLI",  # Mali
    "BFA",  # Burkina Faso
    "NER",  # Niger
    "TCD",  # Chad
    "CAF",  # Central African Republic
    "UKR",  # Ukraine
    "IRQ",  # Iraq
    "LBN",  # Lebanon
    "IRN",  # Iran
]

# Country ISO3 → (lat, lon, name)
_COUNTRY_GEO: dict[str, tuple[float, float, str]] = {
    "SDN": (15.0,  30.0,  "Sudan"),
    "COD": (-4.0,  22.0,  "DRC"),
    "YEM": (15.5,  48.5,  "Yemen"),
    "SYR": (35.0,  38.0,  "Syria"),
    "MMR": (19.0,  96.5,  "Myanmar"),
    "SOM": ( 5.0,  46.0,  "Somalia"),
    "PSE": (31.9,  35.2,  "Palestine"),
    "ETH": ( 9.0,  40.0,  "Ethiopia"),
    "AFG": (33.0,  65.0,  "Afghanistan"),
    "HTI": (19.0, -72.4,  "Haiti"),
    "MLI": (17.0,  -4.0,  "Mali"),
    "BFA": (12.4,  -1.5,  "Burkina Faso"),
    "NER": (17.0,   8.0,  "Niger"),
    "TCD": (15.5,  18.7,  "Chad"),
    "CAF": ( 7.0,  21.0,  "Central African Republic"),
    "UKR": (49.0,  32.0,  "Ukraine"),
    "IRQ": (33.0,  44.0,  "Iraq"),
    "LBN": (33.9,  35.5,  "Lebanon"),
    "IRN": (32.0,  53.0,  "Iran"),
}

# Disaster type → category
_TYPE_CATEGORY = {
    "Conflict":            "conflict",
    "Violence":            "conflict",
    "Insecurity":          "conflict",
    "Epidemic":            "humanitarian",
    "Flood":               "humanitarian",
    "Drought":             "humanitarian",
    "Displacement":        "humanitarian",
    "Humanitarian crisis": "humanitarian",
    "Food insecurity":     "humanitarian",
    "Famine":              "humanitarian",
    "Cholera":             "humanitarian",
}


async def fetch() -> list[dict]:
    from core.engine import log, log_warn

    if not C.ENABLE_RELIEFWEB_API:
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    payload = {
        "limit": 50,
        "sort": ["date:desc"],
        "filter": {
            "operator": "AND",
            "conditions": [
                {
                    "field":     "primary_country.iso3",
                    "value":     _PRIORITY_COUNTRIES,
                    "operator":  "OR",
                },
                {
                    "field": "date.created",
                    "value": {"from": since},
                },
            ],
        },
        "fields": {
            "include": [
                "title", "date.created", "primary_country.iso3",
                "primary_country.name", "disaster_type.name",
                "source.name", "body-html", "url",
            ],
        },
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                _BASE,
                json=payload,
                headers=_HEADERS,
                params={"appname": "wardar"},
            )
        if r.status_code != 200:
            log_warn(f"reliefweb_api: HTTP {r.status_code}")
            return []
        items = r.json().get("data") or []
    except Exception as exc:
        log_warn(f"reliefweb_api: fetch error: {exc}")
        return []

    results = []
    now = datetime.now(timezone.utc).isoformat()

    for item in items:
        try:
            fields = item.get("fields", {})
            title  = fields.get("title", "")
            if not title:
                continue

            # Country geo lookup
            ctry3    = (fields.get("primary_country") or {}).get("iso3", "")
            ctry_name = (fields.get("primary_country") or {}).get("name", "")
            geo = _COUNTRY_GEO.get(ctry3)
            if not geo:
                continue
            lat, lon, ctry_display = geo

            # Disaster type → category
            dtype_list  = fields.get("disaster_type") or []
            dtype_names = [d.get("name", "") for d in dtype_list] if isinstance(dtype_list, list) else []
            category = "humanitarian"
            for dt in dtype_names:
                for k, v in _TYPE_CATEGORY.items():
                    if k.lower() in dt.lower():
                        category = v
                        break

            sources = fields.get("source") or []
            src_name = sources[0].get("name", "") if sources else ""

            # Strip HTML from body
            import re
            body_html = fields.get("body-html", "") or ""
            body_text = re.sub(r"<[^>]+>", " ", body_html).strip()[:500]
            body_text = re.sub(r"\s+", " ", body_text)

            ts = (fields.get("date") or {}).get("created") or now
            url = fields.get("url") or f"https://reliefweb.int/node/{item.get('id','')}"

            results.append({
                "source":      "reliefweb_api",
                "title":       title[:200],
                "description": (body_text or f"Source: {src_name}")[:500],
                "lat":         lat,
                "lon":         lon,
                "country":     ctry_name or ctry_display,
                "category":    category,
                "raw_ts_utc":  ts,
                "url":         url,
                "extra":       json.dumps({
                    "country_iso3":    ctry3,
                    "disaster_types":  dtype_names,
                    "source_org":      src_name,
                }),
            })
        except Exception:
            continue

    log(f"reliefweb_api: {len(results)} humanitarian reports")
    return results
