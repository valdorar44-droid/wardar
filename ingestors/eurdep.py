"""EURDEP radiation monitoring ingestor

European Radiological Data Exchange Platform — ~5,000 gamma stations
across 39 European countries. Operated by JRC (European Commission).

Free, no API key required. Returns current dose rate measurements
from all participating stations.

Normal ambient gamma dose rate in Europe: 50–150 nSv/h
Alert thresholds (multiples of regional baseline):
  ELEVATED  : ≥ 500 nSv/h  (~3-5× normal)
  HIGH      : ≥ 2000 nSv/h (~10-20× normal)
  CRITICAL  : ≥ 10000 nSv/h (~100× normal — potential incident)

Only elevates events above EURDEP_ALERT_NSVH (default 500 nSv/h).
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

import httpx

from config import settings as C

# EURDEP/REMON public REST endpoint — latest dose rate per station
# Returns XML with gamma dose rate readings from all EU stations
_EURDEP_URL = (
    "https://remon.jrc.ec.europa.eu/Service/EurdepService/"
    "EurdepService.svc/REST/DoseRate/Country/EU/LastMeasurement"
)
_HEADERS = {"User-Agent": "Wardar/0.1 (+https://wardar.app) radiation-monitor"}

# XML namespaces used by REMON service
_NS = {
    "e": "http://schemas.datacontract.org/2004/07/EurdepWebService",
    "a": "http://www.w3.org/2005/Atom",
    "i": "http://www.w3.org/2001/XMLSchema-instance",
}


def _rad_label(nsvh: float) -> str:
    if nsvh >= 10000: return "CRITICAL RADIATION"
    if nsvh >= 2000:  return "HIGH RADIATION"
    return "ELEVATED RADIATION"


def _parse_xml(text: str) -> list[dict]:
    """Parse EURDEP XML response → list of station dicts with lat/lon/value."""
    stations = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    # REMON response wraps station data in DoseRateData elements
    # Try multiple possible element paths
    for elem in (
        root.iter("{http://schemas.datacontract.org/2004/07/EurdepWebService}DoseRateData")
        or root.iter("DoseRateData")
        or root.iter("Station")
        or root.iter("item")
    ):
        try:
            def _text(tag: str) -> str:
                e = elem.find(tag, _NS) or elem.find(tag)
                return (e.text or "").strip() if e is not None else ""

            lat_s  = _text("{http://schemas.datacontract.org/2004/07/EurdepWebService}Latitude")  or _text("Latitude")  or _text("lat")
            lon_s  = _text("{http://schemas.datacontract.org/2004/07/EurdepWebService}Longitude") or _text("Longitude") or _text("lon")
            val_s  = _text("{http://schemas.datacontract.org/2004/07/EurdepWebService}Value")     or _text("Value")     or _text("value")
            ts_s   = _text("{http://schemas.datacontract.org/2004/07/EurdepWebService}EndTime")   or _text("EndTime")   or _text("time")
            name_s = _text("{http://schemas.datacontract.org/2004/07/EurdepWebService}StationName") or _text("StationName") or ""
            ctry_s = _text("{http://schemas.datacontract.org/2004/07/EurdepWebService}CountryCode") or _text("CountryCode") or ""

            if not lat_s or not lon_s or not val_s:
                continue
            stations.append({
                "lat":     float(lat_s),
                "lon":     float(lon_s),
                "nsvh":    float(val_s),
                "ts":      ts_s,
                "station": name_s,
                "country": ctry_s,
            })
        except Exception:
            continue
    return stations


async def fetch() -> list[dict]:
    from core.engine import log, log_warn

    if not C.ENABLE_EURDEP:
        return []

    threshold = C.EURDEP_ALERT_NSVH

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(_EURDEP_URL, headers=_HEADERS)
        if r.status_code != 200:
            log_warn(f"eurdep: HTTP {r.status_code}")
            return []
        stations = _parse_xml(r.text)
    except Exception as exc:
        log_warn(f"eurdep: fetch error: {exc}")
        return []

    if not stations:
        # Log raw response snippet for debugging if no stations parsed
        log_warn("eurdep: 0 stations parsed — check URL/format")
        return []

    results = []
    seen_cells: set[str] = set()

    for s in stations:
        try:
            nsvh = s["nsvh"]
            if nsvh < threshold:
                continue

            lat_f = float(s["lat"])
            lon_f = float(s["lon"])

            # ~1° grid cell dedup to avoid flooding from dense station clusters
            cell = f"{int(lat_f)}_{int(lon_f)}"
            if cell in seen_cells:
                continue
            seen_cells.add(cell)

            label = _rad_label(nsvh)
            ts = s.get("ts") or datetime.now(timezone.utc).isoformat()
            station_name = s.get("station") or f"({lat_f:.2f}, {lon_f:.2f})"
            country = s.get("country", "")

            results.append({
                "source":      "eurdep",
                "title":       f"☢ {label}: {nsvh:.0f} nSv/h — {station_name}",
                "description": (
                    f"{nsvh:.0f} nSv/h ({nsvh/1000:.2f} µSv/h) | "
                    f"Threshold: {threshold} nSv/h | "
                    f"Station: {station_name}"
                )[:500],
                "lat":         lat_f,
                "lon":         lon_f,
                "country":     country,
                "category":    "radiation",
                "raw_ts_utc":  ts,
                "url":         "https://eurdep.jrc.ec.europa.eu/",
                "extra":       json.dumps({
                    "nsvh":        round(nsvh, 2),
                    "usvh":        round(nsvh / 1000, 4),
                    "station":     station_name,
                    "country":     country,
                }),
            })
        except Exception:
            continue

    log(f"eurdep: {len(results)} elevated radiation stations (≥{threshold} nSv/h) from {len(stations)} total")
    return results
