"""ACLED conflict events ingestor — acleddata.com API

Auth (two methods, tried in order):
  1. Email + API Access Key (preferred):
       Set ACLED_USERNAME=<email> and ACLED_API_KEY=<access_key>
       Key is shown on your ACLED portal page under "API Access Key"
       Passed as ?email=&key= query params — no OAuth needed

  2. OAuth Bearer token (fallback):
       Set ACLED_USERNAME=<email> and ACLED_PASSWORD=<website_password>
       POST https://acleddata.com/oauth/token → Bearer token (24h TTL)

Set variables in Railway. ACLED_API_KEY is the standard credential.

Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from config import settings as C

_ACLED_URL   = "https://api.acleddata.com/acled/read"
_TOKEN_URL   = "https://acleddata.com/oauth/token"

# In-process OAuth token cache: (access_token, expires_at_epoch)
_token_cache: tuple[str, float] | None = None


async def _get_token(client: httpx.AsyncClient) -> str | None:
    """Return a valid Bearer token via OAuth, refreshing if needed."""
    from core.engine import log_warn

    global _token_cache

    now = time.time()
    if _token_cache and _token_cache[1] > now + 60:
        return _token_cache[0]

    try:
        r = await client.post(
            _TOKEN_URL,
            data={
                "username":   C.ACLED_USERNAME,
                "password":   C.ACLED_PASSWORD,
                "grant_type": "password",
                "client_id":  "acled",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent":   "Mozilla/5.0 (compatible; Wardar/0.1)",
            },
            timeout=15,
        )
        if r.status_code != 200:
            log_warn(f"acled: OAuth token error HTTP {r.status_code}: {r.text[:200]}")
            return None
        body = r.json()
        token = body.get("access_token")
        expires_in = int(body.get("expires_in", 86400))
        if not token:
            log_warn(f"acled: OAuth response missing access_token: {body}")
            return None
        _token_cache = (token, now + expires_in)
        return token
    except Exception as exc:
        log_warn(f"acled: OAuth fetch error: {exc}")
        return None


def _base_params(extra: dict) -> dict:
    """Build ACLED request params. Uses API key auth if available, else Bearer via headers."""
    params = {**extra, "format": "json"}
    if C.ACLED_API_KEY and C.ACLED_USERNAME:
        # Preferred: email + access key as query params (no OAuth needed)
        params["email"] = C.ACLED_USERNAME
        params["key"]   = C.ACLED_API_KEY
    return params


def _auth_headers(token: str | None) -> dict:
    """Return auth headers for OAuth mode (empty dict when using key params)."""
    h = {"User-Agent": "Mozilla/5.0 (compatible; Wardar/0.1)"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# Event type → category
_CATEGORY_MAP = {
    "Battles":             "conflict",
    "Explosions/Remote violence": "conflict",
    "Violence against civilians": "conflict",
    "Protests":            "civil_unrest",
    "Riots":               "civil_unrest",
    "Strategic developments": "strategic",
}


async def fetch() -> list[dict]:
    """Fetch last 7 days of ACLED events."""
    from core.engine import log, log_warn

    has_key  = bool(C.ACLED_API_KEY and C.ACLED_USERNAME)
    has_oauth = bool(C.ACLED_USERNAME and C.ACLED_PASSWORD)
    if not C.ENABLE_ACLED or not (has_key or has_oauth):
        log_warn("acled: no credentials — set ACLED_USERNAME + ACLED_API_KEY in Railway")
        return []

    async with httpx.AsyncClient(timeout=20) as client:
        # Try OAuth only when no direct API key is configured
        token = None
        if not has_key and has_oauth:
            token = await _get_token(client)
            if not token:
                return []

        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        params = _base_params({
            "event_date":       since,
            "event_date_where": ">=",
            "limit":            500,
            "fields":           "event_date|event_type|sub_event_type|actor1|country|admin1|location|latitude|longitude|notes|source",
        })

        try:
            r = await client.get(
                _ACLED_URL,
                params=params,
                headers=_auth_headers(token),
            )
            if r.status_code == 401:
                global _token_cache
                _token_cache = None
                log_warn("acled: 401 Unauthorized — check ACLED_API_KEY or credentials")
                return []
            if r.status_code != 200:
                log_warn(f"acled: HTTP {r.status_code}: {r.text[:200]}")
                return []
            data = r.json().get("data") or []
        except Exception as exc:
            log_warn(f"acled: fetch error: {exc}")
            return []

    results = []
    for row in data:
        try:
            lat = float(row.get("latitude") or 0)
            lon = float(row.get("longitude") or 0)
            if lat == 0 and lon == 0:
                continue
            ev_type  = row.get("event_type", "")
            category = _CATEGORY_MAP.get(ev_type, "conflict")
            title    = f"{ev_type}: {row.get('actor1','')}"
            ts       = row.get("event_date", datetime.now(timezone.utc).date().isoformat())
            results.append({
                "source":      "acled",
                "title":       title[:200],
                "description": str(row.get("notes", ""))[:1000],
                "lat":         lat,
                "lon":         lon,
                "country":     row.get("country", ""),
                "category":    category,
                "raw_ts_utc":  ts + "T00:00:00+00:00",
                "url":         "",
                "extra":       json.dumps({
                    "sub_event_type": row.get("sub_event_type"),
                    "admin1":         row.get("admin1"),
                    "location":       row.get("location"),
                    "source":         row.get("source"),
                }),
            })
        except Exception:
            continue

    log(f"acled: {len(results)} conflict events")
    return results


async def fetch_iran() -> list[dict]:
    """Fetch last 30 days of ACLED events for Iran/Israel/Gaza war zone."""
    from core.engine import log, log_warn

    has_key  = bool(C.ACLED_API_KEY and C.ACLED_USERNAME)
    has_oauth = bool(C.ACLED_USERNAME and C.ACLED_PASSWORD)
    if not C.ENABLE_ACLED or not (has_key or has_oauth):
        return []

    async with httpx.AsyncClient(timeout=20) as client:
        token = None
        if not has_key and has_oauth:
            token = await _get_token(client)
            if not token:
                return []

        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        params = _base_params({
            "event_date":       since,
            "event_date_where": ">=",
            "country":          "Iran|Israel|Iraq|Yemen|Syria|Lebanon",
            "country_where":    "LIKE",
            "limit":            500,
            "fields":           "event_date|event_type|sub_event_type|actor1|country|admin1|location|latitude|longitude|notes|source",
        })

        try:
            r = await client.get(
                _ACLED_URL,
                params=params,
                headers=_auth_headers(token),
            )
            if r.status_code == 401:
                global _token_cache
                _token_cache = None
                log_warn("acled_iran: 401 Unauthorized — check ACLED_API_KEY or credentials")
                return []
            if r.status_code != 200:
                log_warn(f"acled_iran: HTTP {r.status_code}: {r.text[:200]}")
                return []
            data = r.json().get("data") or []
        except Exception as exc:
            log_warn(f"acled_iran: fetch error: {exc}")
            return []

    results = []
    for row in data:
        try:
            lat = float(row.get("latitude") or 0)
            lon = float(row.get("longitude") or 0)
            if lat == 0 and lon == 0:
                continue
            ev_type  = row.get("event_type", "")
            category = _CATEGORY_MAP.get(ev_type, "conflict")
            title    = f"{ev_type}: {row.get('actor1','')}"
            ts       = row.get("event_date", datetime.now(timezone.utc).date().isoformat())
            results.append({
                "source":      "acled_iran",
                "title":       title[:200],
                "description": str(row.get("notes", ""))[:1000],
                "lat":         lat,
                "lon":         lon,
                "country":     row.get("country", ""),
                "category":    category,
                "raw_ts_utc":  ts + "T00:00:00+00:00",
                "url":         "",
                "extra":       json.dumps({
                    "sub_event_type": row.get("sub_event_type"),
                    "admin1":         row.get("admin1"),
                    "location":       row.get("location"),
                    "source":         row.get("source"),
                }),
            })
        except Exception:
            continue

    log(f"acled_iran: {len(results)} Iran-theatre conflict events")
    return results
