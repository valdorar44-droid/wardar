"""
Static infrastructure layer ingestor — nuclear plants, submarine cables,
military bases, and oil/gas pipelines.

Data is fetched from public free APIs and cached in-memory (24h TTL).
Served via /api/layers/{layer} endpoints in server.py.
"""
from __future__ import annotations
import asyncio, json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import settings as C

# ── In-memory cache ──────────────────────────────────────────────────────────
# {layer_name: {"geojson": {...}, "fetched_at": datetime}}
_cache: dict[str, dict] = {}
_TTL = timedelta(hours=24)

# ── Data source URLs ──────────────────────────────────────────────────────────
# Nuclear power plants — GeoNuclearData (CC BY-SA 4.0)
_NUCLEAR_URL = (
    "https://raw.githubusercontent.com/cristianst85/GeoNuclearData/"
    "master/docs/world.geojson"
)
# Fallback nuclear source — OpenNuclear project
_NUCLEAR_FALLBACK_URL = (
    "https://raw.githubusercontent.com/opennuclear/opennuclear/"
    "master/data/opennuclear.json"
)

# Submarine cables — TeleGeography (free public API)
_CABLES_URL = "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json"

# Military bases — USDOT / DoD dataset via ArcGIS (public)
_MIL_BASES_URL = (
    "https://services7.arcgis.com/Z3x04FMqNMPoWYU0/arcgis/rest/services/"
    "Military_Bases1/FeatureServer/0/query"
    "?where=1%3D1&outFields=INSTNAME,BRANCHNAME,COMPONENT,STATE_TERR,COUNTRY"
    "&returnGeometry=true&geometryPrecision=4&outSR=4326&f=geojson&resultRecordCount=5000"
)

# Major pipelines — OpenStreetMap Overpass (international oil/gas lines)
_PIPELINE_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_PIPELINE_QUERY = """
[out:json][timeout:60];
(
  way["man_made"="pipeline"]["substance"~"^(oil|gas|petroleum|crude|natural gas)$",i]
    (if: length() > 200000);
  relation["man_made"="pipeline"]["substance"~"^(oil|gas|petroleum|crude|natural gas)$",i];
);
out geom qt;
"""


async def _fetch_nuclear() -> dict:
    """Fetch nuclear power plant GeoJSON."""
    from core.engine import log, log_warn
    async with httpx.AsyncClient(timeout=30) as client:
        for url in (_NUCLEAR_URL, _NUCLEAR_FALLBACK_URL):
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    # Normalize to GeoJSON FeatureCollection
                    if "features" in data:
                        log(f"nuclear: loaded {len(data['features'])} reactors from {url}")
                        return data
                    # Some formats are just arrays of objects
                    if isinstance(data, list):
                        features = []
                        for p in data:
                            lat = p.get("latitude") or p.get("lat")
                            lon = p.get("longitude") or p.get("lon") or p.get("lng")
                            if lat and lon:
                                features.append({
                                    "type": "Feature",
                                    "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                                    "properties": {
                                        "name":    p.get("name", "Nuclear Plant"),
                                        "country": p.get("country", ""),
                                        "status":  p.get("status", ""),
                                        "type":    p.get("type", ""),
                                    }
                                })
                        log(f"nuclear: loaded {len(features)} reactors (array format)")
                        return {"type": "FeatureCollection", "features": features}
            except Exception as exc:
                log_warn(f"nuclear: {url} failed: {exc}")
    log_warn("nuclear: all sources failed")
    return {"type": "FeatureCollection", "features": []}


async def _fetch_cables() -> dict:
    """Fetch submarine cable GeoJSON from TeleGeography."""
    from core.engine import log, log_warn
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(_CABLES_URL)
            if r.status_code == 200:
                data = r.json()
                n = len(data.get("features", []))
                log(f"cables: loaded {n} cable routes")
                return data
            log_warn(f"cables: HTTP {r.status_code}")
    except Exception as exc:
        log_warn(f"cables: {exc}")
    return {"type": "FeatureCollection", "features": []}


async def _fetch_mil_bases() -> dict:
    """Fetch US military bases from USDOT/ArcGIS."""
    from core.engine import log, log_warn
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.get(_MIL_BASES_URL)
            if r.status_code == 200:
                data = r.json()
                n = len(data.get("features", []))
                log(f"mil_bases: loaded {n} installations")
                return data
            log_warn(f"mil_bases: HTTP {r.status_code}")
    except Exception as exc:
        log_warn(f"mil_bases: {exc}")
    return {"type": "FeatureCollection", "features": []}


async def _fetch_pipelines() -> dict:
    """Fetch major oil/gas pipelines from OpenStreetMap Overpass API."""
    from core.engine import log, log_warn
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(
                _PIPELINE_OVERPASS_URL,
                data={"data": _PIPELINE_QUERY},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code == 200:
                osm = r.json()
                features = []
                for el in (osm.get("elements") or []):
                    if el.get("type") == "way" and el.get("geometry"):
                        coords = [[g["lon"], g["lat"]] for g in el["geometry"]]
                        if len(coords) >= 2:
                            tags = el.get("tags", {})
                            features.append({
                                "type": "Feature",
                                "geometry": {"type": "LineString", "coordinates": coords},
                                "properties": {
                                    "name":      tags.get("name", "Pipeline"),
                                    "substance": tags.get("substance", ""),
                                    "operator":  tags.get("operator", ""),
                                    "id":        el["id"],
                                }
                            })
                log(f"pipelines: loaded {len(features)} pipeline segments")
                return {"type": "FeatureCollection", "features": features}
            log_warn(f"pipelines: HTTP {r.status_code}")
    except Exception as exc:
        log_warn(f"pipelines: {exc}")
    return {"type": "FeatureCollection", "features": []}


async def get_layer(name: str) -> dict:
    """Return cached layer GeoJSON, fetching fresh if TTL expired."""
    cached = _cache.get(name)
    if cached:
        age = datetime.now(timezone.utc) - cached["fetched_at"]
        if age < _TTL:
            return cached["geojson"]

    # Fetch fresh
    fetchers = {
        "nuclear":  _fetch_nuclear,
        "cables":   _fetch_cables,
        "mil_bases":_fetch_mil_bases,
        "pipelines":_fetch_pipelines,
    }
    fn = fetchers.get(name)
    if not fn:
        return {"type": "FeatureCollection", "features": []}

    data = await fn()
    _cache[name] = {"geojson": data, "fetched_at": datetime.now(timezone.utc)}
    return data


async def refresh_all():
    """Fetch all enabled static layers and prime the cache."""
    tasks = []
    if C.ENABLE_NUCLEAR:   tasks.append(get_layer("nuclear"))
    if C.ENABLE_SUBCABLES: tasks.append(get_layer("cables"))
    if C.ENABLE_MIL_BASES: tasks.append(get_layer("mil_bases"))
    if C.ENABLE_PIPELINES: tasks.append(get_layer("pipelines"))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
