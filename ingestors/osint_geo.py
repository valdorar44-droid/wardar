"""
OSINT geo-extractor ingestor — verified conflict geolocations from:
GeoConfirmed, Bellingcat, CenInfoRes, DefMon3, Texty.ua

Uses: pip install osint-geo-extractor
Package installs as `geo_extractor` (not `osint_geo_extractor`).
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
        # Package installs as 'geo_extractor', not 'osint_geo_extractor'
        try:
            from geo_extractor import (
                get_geoconfirmed_data,
                get_bellingcat_data,
                get_ceninfores_data,
                get_defmon_data,
            )
        except ImportError:
            return []

        sources = [
            ("geoconfirmed", get_geoconfirmed_data),
            ("bellingcat",   get_bellingcat_data),
            ("ceninfores",   get_ceninfores_data),
            ("defmon",       get_defmon_data),
        ]
        all_events = []
        for src_name, fn in sources:
            try:
                events = fn()
                for ev in (events or []):
                    all_events.append((src_name, ev))
            except Exception:
                pass  # individual source failures are non-fatal
        return all_events

    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, _sync_extract)
    if not raw:
        return []

    now = datetime.now(timezone.utc).isoformat()
    for src_name, ev in raw:
        try:
            # Event is a dataclass — access fields as attributes, not dict keys
            lat = float(ev.latitude or 0)
            lon = float(ev.longitude or 0)
            if not lat or not lon:
                continue
            ts = now
            if ev.date:
                try:
                    ts = ev.date.isoformat()
                except Exception:
                    ts = str(ev.date)
            url = ""
            if ev.links:
                url = ev.links[0] if isinstance(ev.links, list) else str(ev.links)
            results.append({
                "source":      "osint_geo",
                "title":       str(ev.title or ev.place_desc or "")[:200],
                "description": str(ev.description or ev.place_desc or "")[:500],
                "lat":         lat,
                "lon":         lon,
                "country":     "",
                "category":    "osint_verified",
                "raw_ts_utc":  ts,
                "url":         url,
                "extra":       f'{{"extractor":"{src_name}"}}',
            })
        except Exception:
            pass

    log(f"osint_geo: {len(results)} verified geolocations from {len(set(s for s,_ in raw))} sources")
    return results
