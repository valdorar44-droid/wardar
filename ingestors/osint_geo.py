"""
OSINT geo-extractor ingestor — verified conflict geolocations from:
GeoConfirmed, Bellingcat, CenInfoRes, DefMon3, Texty.ua

Uses: pip install osint-geo-extractor
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone

from config import settings as C


async def fetch() -> list[dict]:
    """Fetch verified OSINT geolocations. Returns list of event dicts."""
    if not C.ENABLE_OSINT_GEO:
        return []

    from core.engine import log, log_warn
    results = []

    def _sync_extract():
        try:
            from osint_geo_extractor import (
                GeoConfirmedExtractor,
                BellingcatExtractor,
                CenInfoResExtractor,
                DefmonExtractor,
                TextyExtractor,
            )
            extractors = [
                ("geoconfirmed", GeoConfirmedExtractor),
                ("bellingcat",   BellingcatExtractor),
                ("ceninfores",   CenInfoResExtractor),
                ("defmon",       DefmonExtractor),
                ("texty",        TextyExtractor),
            ]
            all_events = []
            for src_name, cls in extractors:
                try:
                    ext = cls()
                    events = ext.extract()
                    for ev in (events or []):
                        all_events.append((src_name, ev))
                except Exception as e:
                    pass  # individual extractor failures are non-fatal
            return all_events
        except ImportError:
            return []

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, _sync_extract)
    if not raw:
        return []

    now = datetime.now(timezone.utc).isoformat()
    for src_name, ev in raw:
        try:
            lat = float(ev.get("latitude") or ev.get("lat") or 0)
            lon = float(ev.get("longitude") or ev.get("lon") or ev.get("lng") or 0)
            if not lat or not lon:
                continue
            results.append({
                "source":      "osint_geo",
                "title":       str(ev.get("title") or ev.get("name") or ev.get("description") or "")[:200],
                "description": str(ev.get("description") or "")[:500],
                "lat":         lat,
                "lon":         lon,
                "country":     str(ev.get("country") or ""),
                "category":    "osint_verified",
                "raw_ts_utc":  str(ev.get("date") or ev.get("timestamp") or now),
                "url":         str(ev.get("url") or ev.get("source_url") or ""),
                "extra":       f'{{"extractor":"{src_name}"}}',
            })
        except Exception:
            pass

    log(f"osint_geo: {len(results)} verified geolocations from {len(set(s for s,_ in raw))} sources")
    return results
