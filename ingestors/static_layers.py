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
# Nuclear power plants — try multiple sources in order
_NUCLEAR_URLS = [
    # GeoNuclearData — correct v2 path
    "https://raw.githubusercontent.com/cristianst85/GeoNuclearData/master/docs/GeoNuclearData.geojson",
    # PRIS-derived GeoJSON (community maintained)
    "https://raw.githubusercontent.com/nicholasmr/PRIS-world-nuclear-power-reactors/main/PRIS_reactors.geojson",
    # OpenNuclear fallback
    "https://raw.githubusercontent.com/opennuclear/opennuclear/master/data/opennuclear.json",
]

# Hardcoded conflict-relevant nuclear/radiological sites — guaranteed fallback.
# Covers Iran program, Ukraine power plants, North Korea Yongbyon, key NATO/Russia facilities.
_NUCLEAR_FALLBACK: list[dict] = [
    # Iran nuclear program
    {"name":"Natanz Enrichment Complex","lat":33.73,"lon":51.73,"country":"Iran","status":"Operational","type":"Enrichment"},
    {"name":"Fordow Fuel Enrichment Plant","lat":34.88,"lon":51.13,"country":"Iran","status":"Operational","type":"Enrichment"},
    {"name":"Bushehr Nuclear Power Plant","lat":28.83,"lon":50.88,"country":"Iran","status":"Operational","type":"Power"},
    {"name":"Isfahan Nuclear Technology Centre","lat":32.63,"lon":51.65,"country":"Iran","status":"Operational","type":"Research"},
    {"name":"Parchin Military Complex","lat":35.52,"lon":51.77,"country":"Iran","status":"Military","type":"Research"},
    {"name":"Arak Heavy Water Reactor","lat":34.24,"lon":49.23,"country":"Iran","status":"Operational","type":"Research"},
    # North Korea
    {"name":"Yongbyon Nuclear Research Centre","lat":39.79,"lon":125.75,"country":"North Korea","status":"Operational","type":"Research/Weapons"},
    {"name":"Punggye-ri Test Site","lat":41.27,"lon":129.09,"country":"North Korea","status":"Closed","type":"Test Site"},
    # Ukraine (conflict zone)
    {"name":"Zaporizhzhia Nuclear Power Plant","lat":47.51,"lon":34.59,"country":"Ukraine","status":"IAEA Monitoring","type":"Power"},
    {"name":"Chernobyl Nuclear Power Plant","lat":51.39,"lon":30.10,"country":"Ukraine","status":"Decommissioned","type":"Power"},
    {"name":"Rivne Nuclear Power Plant","lat":51.33,"lon":25.89,"country":"Ukraine","status":"Operational","type":"Power"},
    {"name":"Khmelnytskyi Nuclear Power Plant","lat":50.30,"lon":26.65,"country":"Ukraine","status":"Operational","type":"Power"},
    # Russia (conflict-relevant)
    {"name":"Leningrad Nuclear Power Plant","lat":59.88,"lon":29.07,"country":"Russia","status":"Operational","type":"Power"},
    {"name":"Smolensk Nuclear Power Plant","lat":54.17,"lon":32.95,"country":"Russia","status":"Operational","type":"Power"},
    {"name":"Kursk Nuclear Power Plant","lat":51.67,"lon":35.61,"country":"Russia","status":"Operational","type":"Power"},
    # Israel
    {"name":"Negev Nuclear Research Centre (Dimona)","lat":31.00,"lon":35.15,"country":"Israel","status":"Military","type":"Research/Weapons"},
    # Pakistan
    {"name":"Khushab Nuclear Complex","lat":32.07,"lon":71.97,"country":"Pakistan","status":"Operational","type":"Plutonium Production"},
    {"name":"Chasma Nuclear Power Plant","lat":32.38,"lon":71.44,"country":"Pakistan","status":"Operational","type":"Power"},
    # Key NATO / EU plants near Russia
    {"name":"Ignalina Nuclear Power Plant (decommissioning)","lat":55.61,"lon":26.56,"country":"Lithuania","status":"Decommissioned","type":"Power"},
    {"name":"Loviisa Nuclear Power Plant","lat":60.40,"lon":26.37,"country":"Finland","status":"Operational","type":"Power"},
]

# Submarine cables — TeleGeography (free public API)
_CABLES_URL = "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json"

# Military bases — multiple sources in order of preference
_MIL_BASES_URLS = [
    # USDOT ArcGIS — updated service ID
    (
        "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/"
        "Military_Installations_Ranges_and_Training_Areas/FeatureServer/0/query"
        "?where=1%3D1&outFields=SITE_NAME,COMPONENT,COUNTRY,STATE_TERR"
        "&returnGeometry=true&geometryPrecision=4&outSR=4326&f=geojson&resultRecordCount=2000"
    ),
    # Alternative ArcGIS source
    (
        "https://services7.arcgis.com/Z3x04FMqNMPoWYU0/arcgis/rest/services/"
        "Military_Bases1/FeatureServer/0/query"
        "?where=1%3D1&outFields=INSTNAME,BRANCHNAME,COMPONENT,STATE_TERR,COUNTRY"
        "&returnGeometry=true&geometryPrecision=4&outSR=4326&f=geojson&resultRecordCount=5000"
    ),
]

# OSM Overpass fallback for military bases globally
_MIL_BASES_OSM_QUERY = """
[out:json][timeout:45];
(
  node["military"="base"][name];
  node["military"="airfield"][name];
  node["military"="naval_base"][name];
  way["military"="base"][name];
  way["military"="airfield"][name];
);
out center qt 1000;
"""

# Hardcoded major global military bases — guaranteed fallback when ArcGIS/OSM fail
_MIL_BASES_FALLBACK = [
    # USA
    {"name":"Pentagon","lat":38.8719,"lon":-77.0563,"country":"USA","branch":"DoD HQ"},
    {"name":"Naval Station Norfolk","lat":36.9376,"lon":-76.2988,"country":"USA","branch":"Navy"},
    {"name":"Joint Base Pearl Harbor-Hickam","lat":21.3549,"lon":-157.9781,"country":"USA","branch":"Navy/Air Force"},
    {"name":"Ramstein Air Base","lat":49.4369,"lon":7.6003,"country":"Germany","branch":"USAF"},
    {"name":"Incirlik Air Base","lat":37.0021,"lon":35.4258,"country":"Turkey","branch":"USAF"},
    {"name":"Diego Garcia","lat":-7.3133,"lon":72.4228,"country":"BIOT","branch":"USN/RAF"},
    {"name":"Camp Humphreys","lat":36.9722,"lon":127.0289,"country":"South Korea","branch":"USA"},
    {"name":"Kadena Air Base","lat":26.3556,"lon":127.7689,"country":"Japan","branch":"USAF"},
    {"name":"Guantanamo Bay","lat":19.9062,"lon":-75.0988,"country":"Cuba (US lease)","branch":"USN"},
    {"name":"Al Udeid Air Base","lat":25.1173,"lon":51.3148,"country":"Qatar","branch":"USAF"},
    {"name":"Ali Al Salem Air Base","lat":29.3467,"lon":47.5208,"country":"Kuwait","branch":"USAF"},
    {"name":"NSA Bahrain (5th Fleet)","lat":26.2285,"lon":50.5899,"country":"Bahrain","branch":"USN"},
    {"name":"Andersen Air Force Base","lat":13.5838,"lon":144.9278,"country":"Guam","branch":"USAF"},
    # Russia
    {"name":"RVSN HQ (Vlasikha)","lat":55.7219,"lon":37.1536,"country":"Russia","branch":"Strategic Rocket Forces"},
    {"name":"Severomorsk Naval Base","lat":69.0767,"lon":33.4194,"country":"Russia","branch":"Northern Fleet"},
    {"name":"Tartus Naval Base","lat":34.9064,"lon":35.8869,"country":"Syria","branch":"Russia Navy"},
    {"name":"Hmeimim Air Base","lat":35.4014,"lon":37.2356,"country":"Syria","branch":"Russia Air Force"},
    {"name":"Engels Air Base","lat":51.5608,"lon":46.1756,"country":"Russia","branch":"Long-Range Aviation"},
    {"name":"Kubinka Air Base","lat":55.6076,"lon":36.6561,"country":"Russia","branch":"Russia Air Force"},
    # China
    {"name":"Sanya Naval Base (Yulin)","lat":18.2292,"lon":109.5664,"country":"China","branch":"PLAN South Sea Fleet"},
    {"name":"Djibouti Military Base","lat":11.5567,"lon":43.1592,"country":"Djibouti","branch":"PLA Navy"},
    {"name":"Ream Naval Base (Cambodia)","lat":10.5247,"lon":103.6731,"country":"Cambodia","branch":"PLA Navy"},
    {"name":"Zhanjiang Naval Base","lat":21.1899,"lon":110.3908,"country":"China","branch":"PLAN"},
    # NATO / Europe
    {"name":"RAF Brize Norton","lat":51.7501,"lon":-1.5836,"country":"UK","branch":"RAF"},
    {"name":"SHAPE (NATO HQ)","lat":50.5097,"lon":4.4806,"country":"Belgium","branch":"NATO"},
    {"name":"Aviano Air Base","lat":46.0319,"lon":12.5961,"country":"Italy","branch":"USAF/NATO"},
    {"name":"Mihail Kogalniceanu AB","lat":44.3619,"lon":28.4881,"country":"Romania","branch":"NATO"},
    # Middle East
    {"name":"Nevatim Air Base","lat":31.2083,"lon":34.9928,"country":"Israel","branch":"IAF"},
    {"name":"Tel Nof Air Base","lat":31.8394,"lon":34.8219,"country":"Israel","branch":"IAF"},
    {"name":"Hatzerim Air Base","lat":31.2331,"lon":34.6642,"country":"Israel","branch":"IAF"},
    {"name":"Al-Tanf Garrison","lat":33.5086,"lon":38.6831,"country":"Syria","branch":"US Army"},
    {"name":"King Faisal Air Base","lat":17.1442,"lon":42.6564,"country":"Saudi Arabia","branch":"RSAF"},
    # North Korea
    {"name":"Sunchon Air Base","lat":39.4322,"lon":125.9072,"country":"North Korea","branch":"KPAF"},
    {"name":"Wonsan-Kalma Airport (military)","lat":39.1667,"lon":127.4864,"country":"North Korea","branch":"KPAF"},
]

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
        for url in _NUCLEAR_URLS:
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
    # All network sources failed — return hardcoded conflict-relevant fallback
    log_warn("nuclear: all sources failed — using conflict-relevant fallback dataset")
    features = []
    for p in _NUCLEAR_FALLBACK:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": {
                "name":    p["name"],
                "country": p["country"],
                "status":  p["status"],
                "type":    p["type"],
            },
        })
    log(f"nuclear: {len(features)} conflict-relevant facilities (fallback)")
    return {"type": "FeatureCollection", "features": features}


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
    """Fetch military bases — ArcGIS primary, OSM Overpass fallback."""
    from core.engine import log, log_warn
    # Try ArcGIS sources first
    async with httpx.AsyncClient(timeout=10) as client:
        for url in _MIL_BASES_URLS:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    features = data.get("features", [])
                    if features:
                        log(f"mil_bases: loaded {len(features)} installations (ArcGIS)")
                        return data
            except Exception as exc:
                log_warn(f"mil_bases: ArcGIS error: {exc}")

    # OSM Overpass fallback
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                _PIPELINE_OVERPASS_URL,
                data={"data": _MIL_BASES_OSM_QUERY},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code == 200:
                osm = r.json()
                features = []
                for el in osm.get("elements", []):
                    lat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
                    lon = el.get("lon") or (el.get("center", {}) or {}).get("lon")
                    tags = el.get("tags", {})
                    if lat and lon and tags.get("name"):
                        features.append({
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [lon, lat]},
                            "properties": {
                                "name":      tags.get("name", "Military Base"),
                                "country":   tags.get("country", ""),
                                "military":  tags.get("military", "base"),
                                "operator":  tags.get("operator", ""),
                            },
                        })
                log(f"mil_bases: loaded {len(features)} installations (OSM)")
                return {"type": "FeatureCollection", "features": features}
    except Exception as exc:
        log_warn(f"mil_bases: OSM fallback error: {exc}")

    # Hardcoded fallback — guaranteed coverage for key global military installations
    log("mil_bases: using hardcoded fallback list")
    features = []
    for b in _MIL_BASES_FALLBACK:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [b["lon"], b["lat"]]},
            "properties": {
                "name":    b["name"],
                "country": b["country"],
                "military": "base",
                "operator": b.get("branch", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features}


async def _fetch_pipelines() -> dict:
    """Fetch major oil/gas pipelines from OpenStreetMap Overpass API."""
    from core.engine import log, log_warn
    try:
        async with httpx.AsyncClient(timeout=12) as client:
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


# ISW Ukraine frontline data — multi-source with fallbacks
_ISW_UKRAINE_URLS = [
    # DeepState Map — the authoritative Ukrainian OSINT frontline tracker
    "https://deepstatemap.live/api/history/last",
    # Fallback: community-maintained GeoJSON
    "https://raw.githubusercontent.com/simonbilskyrollins/russia-ukraine-war-geojson/main/ua_frontline.geojson",
]

async def _fetch_isw_ukraine() -> dict:
    """Fetch Ukraine frontline/control GeoJSON."""
    from core.engine import log, log_warn
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Wardar/0.1"}) as client:
        for url in _ISW_UKRAINE_URLS:
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                # DeepStateMap API returns {"id":..., "map": {FeatureCollection}}
                if "map" in data and isinstance(data["map"], dict):
                    fc = data["map"]
                    if fc.get("features"):
                        log(f"isw_ukraine: {len(fc['features'])} features (DeepStateMap)")
                        return fc
                # Direct GeoJSON FeatureCollection
                if "features" in data:
                    log(f"isw_ukraine: {len(data['features'])} features")
                    return data
                # DeepState wrapped format
                if "ua" in data or "ru" in data or "state" in data:
                    features = []
                    for key, geom in data.items():
                        if isinstance(geom, dict) and geom.get("type") in ("Polygon","MultiPolygon","LineString","MultiLineString","GeometryCollection"):
                            features.append({"type":"Feature","geometry":geom,"properties":{"zone":key}})
                    if features:
                        log(f"isw_ukraine: {len(features)} zones (DeepState format)")
                        return {"type":"FeatureCollection","features":features}
            except Exception as exc:
                log_warn(f"isw_ukraine: {url} failed: {exc}")
    log_warn("isw_ukraine: all sources failed, returning empty")
    return {"type":"FeatureCollection","features":[]}

# ISW Middle East — placeholder, add working URL when available
async def _fetch_isw_middle_east() -> dict:
    """Fetch Middle East/Gaza frontline GeoJSON."""
    from core.engine import log, log_warn
    # UNOCHA Gaza situation maps
    try:
        url = "https://data.humdata.org/api/3/action/datastore_search?resource_id=e7e2dc59-8bca-4a73-b806-fcc7dca5f88a&limit=100"
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Wardar/0.1"}) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                records = (data.get("result") or {}).get("records") or []
                features = []
                for rec in records:
                    lat = rec.get("latitude") or rec.get("lat")
                    lon = rec.get("longitude") or rec.get("lon")
                    if lat and lon:
                        features.append({
                            "type":"Feature",
                            "geometry":{"type":"Point","coordinates":[float(lon),float(lat)]},
                            "properties":{"name":rec.get("name",""),"zone":rec.get("zone","")},
                        })
                if features:
                    log(f"isw_middle_east: {len(features)} features")
                    return {"type":"FeatureCollection","features":features}
    except Exception as exc:
        log_warn(f"isw_middle_east: {exc}")
    return {"type":"FeatureCollection","features":[]}


async def get_layer(name: str) -> dict:
    """Return cached layer GeoJSON, fetching fresh if TTL expired."""
    cached = _cache.get(name)
    if cached:
        age = datetime.now(timezone.utc) - cached["fetched_at"]
        if age < _TTL:
            return cached["geojson"]

    # Fetch fresh
    fetchers = {
        "nuclear":         _fetch_nuclear,
        "cables":          _fetch_cables,
        "mil_bases":       _fetch_mil_bases,
        "pipelines":       _fetch_pipelines,
        "isw_ukraine":     _fetch_isw_ukraine,
        "isw_middle_east": _fetch_isw_middle_east,
    }
    fn = fetchers.get(name)
    if not fn:
        return {"type": "FeatureCollection", "features": []}

    data = await fn()
    # Only cache non-empty results so we retry on the next request if failed
    if data.get("features"):
        _cache[name] = {"geojson": data, "fetched_at": datetime.now(timezone.utc)}
    return data


async def refresh_all():
    """Fetch all enabled static layers and prime the cache."""
    tasks = []
    if C.ENABLE_NUCLEAR:   tasks.append(get_layer("nuclear"))
    if C.ENABLE_SUBCABLES: tasks.append(get_layer("cables"))
    if C.ENABLE_MIL_BASES: tasks.append(get_layer("mil_bases"))
    if C.ENABLE_PIPELINES: tasks.append(get_layer("pipelines"))
    if C.ENABLE_ISW:
        tasks.append(get_layer("isw_ukraine"))
        tasks.append(get_layer("isw_middle_east"))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
