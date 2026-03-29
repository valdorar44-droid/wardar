"""UCDP conflict events ingestor — Uppsala University

Fetches georeferenced conflict events from the Uppsala Conflict Data Program (UCDP).
The GED (Georeferenced Events Dataset) covers every violent incident with
lat/lon, actors, casualties, and conflict type since 1989.

Two modes:
1. Candidate events (near-real-time, monthly releases) — requires UCDP_TOKEN
2. Historical GED (annual, fully verified) — same endpoint, more complete

API: https://ucdpapi.pcr.uu.se/api/gedevents/25.1
Token: free, email mertcan.yilmaz@pcr.uu.se with a brief description of use.
Set UCDP_TOKEN env var once you have it.

Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta

import httpx

from config import settings as C

_BASE = "https://ucdpapi.pcr.uu.se/api"
_VERSION = "25.1"

# Type of violence codes
_VIOLENCE_TYPE = {
    1: "State-based conflict",
    2: "Non-state conflict",
    3: "One-sided violence",
}

# Best/low/high estimates for deaths
def _deaths_label(item: dict) -> str:
    best = item.get("best") or 0
    low  = item.get("low") or 0
    high = item.get("high") or 0
    if best:
        return f"{best} deaths (range {low}–{high})"
    return "unknown casualties"


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_UCDP:
        return []
    if not C.UCDP_TOKEN:
        log_warn("ucdp: no UCDP_TOKEN set — skipping (email mertcan.yilmaz@pcr.uu.se for free token)")
        return []

    headers = {
        "Authorization": f"Token {C.UCDP_TOKEN}",
        "User-Agent": "Wardar/0.1 (+https://wardar.app)",
    }

    # Fetch events from the last 90 days (candidate near-real-time dataset)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    params = {
        "StartDate":  cutoff,
        "pagesize":   1000,
        "page":       1,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{_BASE}/gedevents_candidate/{_VERSION}",
                params=params,
                headers=headers,
            )
        if r.status_code == 401:
            log_warn("ucdp: invalid token — check UCDP_TOKEN")
            return []
        if r.status_code == 404:
            # Fall back to main GED dataset
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{_BASE}/gedevents/{_VERSION}",
                    params=params,
                    headers=headers,
                )
        if r.status_code != 200:
            log_warn(f"ucdp: HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as exc:
        log_warn(f"ucdp: fetch error: {exc}")
        return []

    results = []
    for item in (data.get("Result") or []):
        try:
            lat = item.get("latitude")
            lon = item.get("longitude")
            if lat is None or lon is None:
                continue

            event_id    = str(item.get("id", ""))
            date_str    = item.get("date_start") or item.get("year", "")
            country     = item.get("country") or ""
            conflict_name = item.get("conflict_name") or item.get("dyad_name") or ""
            side_a      = item.get("side_a") or ""
            side_b      = item.get("side_b") or ""
            viol_type   = _VIOLENCE_TYPE.get(item.get("type_of_violence", 0), "Conflict")
            deaths      = _deaths_label(item)
            source      = item.get("source_article") or ""

            ts = datetime.now(timezone.utc).isoformat()
            if date_str:
                try:
                    ts = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
                except Exception:
                    pass

            title = f"UCDP: {viol_type}"
            if conflict_name:
                title += f" — {conflict_name}"

            desc = f"{side_a} vs {side_b}. {deaths}."
            if country:
                desc += f" Location: {country}."

            results.append({
                "source":      "ucdp",
                "title":       title[:200],
                "description": desc[:500],
                "lat":         float(lat),
                "lon":         float(lon),
                "country":     country[:2].upper() if len(country) >= 2 else country,
                "category":    "conflict",
                "raw_ts_utc":  ts,
                "url":         source[:500] if source else "",
                "extra": json.dumps({
                    "id":            event_id,
                    "conflict_name": conflict_name,
                    "side_a":        side_a,
                    "side_b":        side_b,
                    "deaths_best":   item.get("best", 0),
                    "viol_type":     item.get("type_of_violence", 0),
                }),
            })
        except Exception:
            continue

    log(f"ucdp: {len(results)} conflict events (last 90d)")
    return results
