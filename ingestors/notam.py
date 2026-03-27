"""NOTAM ingestor — FAA NOTAM API

Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import httpx

from config import settings as C

async def fetch() -> list[dict]:
    from core.engine import log, log_warn

    if not C.ENABLE_NOTAM:
        return []

    # FAA OAuth token (Client Credentials)
    token = await _get_faa_token()
    if not token:
        log_warn("notam: no FAA auth token — skipping")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    params  = {
        "pageSize":     100,
        "sortBy":       "effectiveStartDate",
        "sortOrder":    "Desc",
        "domesticFiltering": "false",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(C.FAA_NOTAM_URL, headers=headers, params=params)
            if r.status_code != 200:
                log_warn(f"notam: HTTP {r.status_code}")
                return []
            data = r.json()
    except Exception as exc:
        log_warn(f"notam: fetch error: {exc}")
        return []

    items = data.get("items") or []
    results = []
    now = datetime.now(timezone.utc).isoformat()

    for item in items:
        try:
            props   = item.get("properties", {})
            geo     = item.get("geometry", {})
            coords  = (geo.get("coordinates") or [None, None])[:2]
            lat = float(coords[1]) if len(coords) >= 2 and coords[1] is not None else None
            lon = float(coords[0]) if len(coords) >= 2 and coords[0] is not None else None

            coreNOTAMData = props.get("coreNOTAMData", {})
            notam         = coreNOTAMData.get("notam", {})
            text          = notam.get("text", "") or notam.get("issue", "")
            icao          = notam.get("location", "")
            classification = notam.get("classification", "")
            effective_start = props.get("effectiveStartDate", now)

            results.append({
                "source":      "notam",
                "title":       f"NOTAM {icao}: {text[:80]}",
                "description": text[:500],
                "lat":         lat,
                "lon":         lon,
                "country":     icao[:2] if icao else "",
                "category":    "notam",
                "raw_ts_utc":  effective_start,
                "url":         "",
                "extra":       json.dumps({"icao": icao, "classification": classification}),
            })
        except Exception:
            continue

    log(f"notam: {len(results)} NOTAMs")
    return results

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
