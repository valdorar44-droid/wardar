"""NOTAM ingestor — FAA NOTAM API

Two-phase fetch:
  1. Global recent NOTAMs (last 200 worldwide) — catches major airspace events
  2. Targeted queries for conflict-zone FIRs — ensures war-relevant NOTAMs
     aren't buried under routine US domestic ones.

Deduplicates by NOTAM ID across both phases.
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import httpx

from config import settings as C

# ICAO FIR/UIR codes for active conflict zones and strategic chokepoints.
# Each gets a dedicated 50-NOTAM query on top of the global feed.
_CONFLICT_FIRS = [
    "UKOV",  # Kyiv FIR (Ukraine west)
    "UKBU",  # Dnipro FIR (Ukraine east)
    "LLLL",  # Israel / Tel Aviv FIR
    "OSTT",  # Damascus FIR (Syria)
    "ORBB",  # Baghdad FIR (Iraq)
    "OIIX",  # Tehran FIR (Iran)
    "OYSC",  # Sana'a FIR (Yemen)
    "OLBA",  # Beirut FIR (Lebanon)
    "OBBB",  # Bahrain UIR (Persian Gulf / Hormuz)
    "OOGP",  # Muscat FIR (Oman / Gulf of Oman)
    "RCAA",  # Taipei FIR (Taiwan Strait)
    "ZKKP",  # Pyongyang FIR (North Korea)
    "ULMM",  # Murmansk FIR (Kola Peninsula / Arctic)
    "UHBW",  # Khabarovsk FIR (Russian Far East)
    "DRRR",  # Niamey FIR (Sahel / Niger)
    "GOOO",  # Dakar FIR (West Africa / Gulf of Guinea)
]


async def fetch() -> list[dict]:
    from core.engine import log, log_warn

    if not C.ENABLE_NOTAM:
        return []

    token = await _get_faa_token()
    if not token:
        log_warn("notam: no FAA auth token — set FAA_CLIENT_ID + FAA_CLIENT_SECRET")
        return []

    headers   = {"Authorization": f"Bearer {token}"}
    seen_ids: set[str] = set()
    results:  list[dict] = []

    # Phase 1: global recent feed — catches high-profile airspace closures
    await _query(headers, {
        "pageSize":         200,
        "sortBy":           "effectiveStartDate",
        "sortOrder":        "Desc",
        "domesticFiltering": "false",
    }, seen_ids, results)

    # Phase 2: conflict-zone FIR targeted queries
    for fir in _CONFLICT_FIRS:
        await _query(headers, {
            "icaoLocation": fir,
            "pageSize":     50,
            "sortBy":       "effectiveStartDate",
            "sortOrder":    "Desc",
        }, seen_ids, results)

    log(f"notam: {len(results)} NOTAMs (global + {len(_CONFLICT_FIRS)} conflict FIRs)")
    return results


async def _query(headers: dict, params: dict, seen_ids: set, results: list) -> None:
    from core.engine import log_warn
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(C.FAA_NOTAM_URL, headers=headers, params=params)
        if r.status_code != 200:
            log_warn(f"notam: HTTP {r.status_code} (params={params})")
            return
        data = r.json()
    except Exception as exc:
        log_warn(f"notam: fetch error: {exc}")
        return

    now = datetime.now(timezone.utc).isoformat()
    for item in (data.get("items") or []):
        try:
            props          = item.get("properties", {})
            geo            = item.get("geometry", {})
            coords         = (geo.get("coordinates") or [None, None])[:2]
            lat = float(coords[1]) if len(coords) >= 2 and coords[1] is not None else None
            lon = float(coords[0]) if len(coords) >= 2 and coords[0] is not None else None

            core       = props.get("coreNOTAMData", {})
            notam_obj  = core.get("notam", {})
            notam_id   = notam_obj.get("id", "") or str(props.get("id", ""))
            text       = notam_obj.get("text", "") or notam_obj.get("issue", "")
            icao       = notam_obj.get("location", "")
            classif    = notam_obj.get("classification", "")
            eff_start  = props.get("effectiveStartDate", now)

            # Deduplicate across phases
            dedup = notam_id or f"{icao}_{text[:40]}"
            if dedup in seen_ids:
                continue
            seen_ids.add(dedup)

            results.append({
                "source":      "notam",
                "title":       f"NOTAM {icao}: {text[:80]}",
                "description": text[:500],
                "lat":         lat,
                "lon":         lon,
                "country":     icao[:2] if icao else "",
                "category":    "notam",
                "raw_ts_utc":  eff_start,
                "url":         "",
                "extra":       json.dumps({
                    "icao":           icao,
                    "classification": classif,
                    "notam_id":       notam_id,
                }),
            })
        except Exception:
            continue


async def _get_faa_token() -> str | None:
    if not C.FAA_CLIENT_ID or not C.FAA_CLIENT_SECRET:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://external-api.faa.gov/auth/oauth/token",
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     C.FAA_CLIENT_ID,
                    "client_secret": C.FAA_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code == 200:
                return r.json().get("access_token")
    except Exception:
        pass
    return None
