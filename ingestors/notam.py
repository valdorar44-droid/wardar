"""NOTAM ingestor — CheckWX API (checkwxapi.com)

Queries conflict-zone airports in sequential batches to stay within the
free-tier limit of 100 calls/day. Default interval is 6h = 4 polls/day.

Fallback: if CHECKWX_API_KEY is missing but FAA credentials exist,
uses the FAA Digital NOTAM API (OAuth2).

Requires: CHECKWX_API_KEY env var (or FAA_CLIENT_ID + FAA_CLIENT_SECRET)
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import httpx
from config import settings as C

# Conflict-zone airport ICAO codes.
# Grouped so we can log them by region. Each is one API call.
# At 6h interval: 24 calls × 4 polls/day = 96 calls/day (within 100/day free tier).
_CONFLICT_AIRPORTS = [
    # Ukraine / Eastern Europe
    "UKBB",  # Kyiv Boryspil
    "UKHH",  # Kharkiv
    "UKOO",  # Odesa
    "UKDD",  # Dnipro
    "UMMS",  # Minsk (Belarus staging)
    "ULMM",  # Murmansk (Kola Peninsula)
    # Israel / Levant
    "LLBG",  # Tel Aviv Ben Gurion
    "OSDI",  # Damascus
    "OLBA",  # Beirut
    # Iraq / Iran / Gulf
    "ORBI",  # Baghdad
    "OIIE",  # Tehran Imam Khomeini
    "OBBS",  # Bahrain (CENTCOM hub)
    "OMAA",  # Abu Dhabi
    "OOMS",  # Muscat (Gulf of Oman)
    # Yemen / Red Sea
    "OYSN",  # Sana'a
    "HSSS",  # Khartoum (Sudan)
    # Taiwan Strait / Pacific
    "RCTP",  # Taipei Taoyuan
    "VHHH",  # Hong Kong (PLA activity indicator)
    # Korea
    "RKSI",  # Seoul Incheon
    "ZKPY",  # Pyongyang
    # Sahel / West Africa
    "DRRR",  # Niamey FIR (Niger)
    "HAAB",  # Addis Ababa (Horn of Africa)
    # NATO Eastern flank
    "EPWA",  # Warsaw
    "EVRA",  # Riga (Baltic)
]

# ICAO → (lat, lon) for map pins.
# NOTAMs don't always carry geometry — airport position is close enough.
_ICAO_LL: dict[str, tuple[float, float]] = {
    "UKBB": (50.34, 30.89), "UKHH": (49.93, 36.29), "UKOO": (46.43, 30.67),
    "UKDD": (48.36, 35.10), "UMMS": (53.88, 28.03), "ULMM": (68.78, 32.75),
    "LLBG": (32.00, 34.89), "OSDI": (33.41, 36.51), "OLBA": (33.82, 35.49),
    "ORBI": (33.26, 44.23), "OIIE": (35.69, 51.31), "OBBS": (26.27, 50.63),
    "OMAA": (24.43, 54.65), "OOMS": (23.59, 58.28),
    "OYSN": (15.47, 44.22), "HSSS": (15.59, 32.55),
    "RCTP": (25.08,121.23), "VHHH": (22.31,113.91),
    "RKSI": (37.46,126.44), "ZKPY": (39.22,125.68),
    "DRRR": (13.48,  2.18), "HAAB": ( 8.98, 38.80),
    "EPWA": (52.17, 20.97), "EVRA": (56.92, 23.97),
}

_CHECKWX_BASE = "https://api.checkwx.com"


async def fetch() -> list[dict]:
    from core.engine import log, log_warn

    if not C.ENABLE_NOTAM:
        return []

    # CheckWX preferred — simple API key
    if C.CHECKWX_API_KEY:
        return await _fetch_checkwx(log, log_warn)

    # FAA fallback — OAuth2 (portal currently broken for public signups)
    if C.FAA_CLIENT_ID and C.FAA_CLIENT_SECRET:
        return await _fetch_faa(log, log_warn)

    log_warn("notam: no credentials — set CHECKWX_API_KEY in Railway")
    return []


# ── CheckWX ───────────────────────────────────────────────────────────────────

async def _fetch_checkwx(log, log_warn) -> list[dict]:
    headers  = {"X-API-Key": C.CHECKWX_API_KEY}
    seen_ids: set[str] = set()
    results:  list[dict] = []

    for icao in _CONFLICT_AIRPORTS:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{_CHECKWX_BASE}/notam/{icao}",
                    headers=headers,
                )
            if r.status_code == 401:
                log_warn("notam: CheckWX API key rejected — check CHECKWX_API_KEY")
                break
            if r.status_code == 429:
                log_warn("notam: CheckWX rate limit hit — reduce polling or upgrade plan")
                break
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception as exc:
            log_warn(f"notam: CheckWX error ({icao}): {exc}")
            continue

        for item in (data.get("data") or []):
            try:
                raw     = item.get("raw", "") or ""
                icao_loc = item.get("icao") or icao
                notam_id = item.get("id", "") or raw[:30]

                dedup = f"{icao_loc}_{notam_id}"
                if dedup in seen_ids:
                    continue
                seen_ids.add(dedup)

                # Timestamps
                start = (item.get("start_time") or {}).get("dt", "")
                if not start:
                    start = datetime.now(timezone.utc).isoformat()

                # Coordinates — airport fallback
                lat, lon = _ICAO_LL.get(icao_loc, (None, None))

                results.append({
                    "source":      "notam",
                    "title":       f"NOTAM {icao_loc}: {raw[:80]}",
                    "description": raw[:500],
                    "lat":         lat,
                    "lon":         lon,
                    "country":     icao_loc[:2] if icao_loc else "",
                    "category":    "notam",
                    "raw_ts_utc":  start,
                    "url":         "",
                    "extra":       json.dumps({
                        "icao":     icao_loc,
                        "notam_id": notam_id,
                        "source":   "checkwx",
                    }),
                })
            except Exception:
                continue

    log(f"notam: {len(results)} NOTAMs via CheckWX ({len(_CONFLICT_AIRPORTS)} airports)")
    return results


# ── FAA fallback ──────────────────────────────────────────────────────────────

async def _fetch_faa(log, log_warn) -> list[dict]:
    token = await _get_faa_token()
    if not token:
        log_warn("notam: FAA token failed")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    seen_ids: set[str] = set()
    results:  list[dict] = []

    await _faa_query(headers, {
        "pageSize": 200, "sortBy": "effectiveStartDate",
        "sortOrder": "Desc", "domesticFiltering": "false",
    }, seen_ids, results, log_warn)

    log(f"notam: {len(results)} NOTAMs via FAA")
    return results


async def _faa_query(headers, params, seen_ids, results, log_warn) -> None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(C.FAA_NOTAM_URL, headers=headers, params=params)
        if r.status_code != 200:
            log_warn(f"notam: FAA HTTP {r.status_code}")
            return
        data = r.json()
    except Exception as exc:
        log_warn(f"notam: FAA fetch error: {exc}")
        return

    now = datetime.now(timezone.utc).isoformat()
    for item in (data.get("items") or []):
        try:
            props     = item.get("properties", {})
            geo       = item.get("geometry", {})
            coords    = (geo.get("coordinates") or [None, None])[:2]
            lat = float(coords[1]) if len(coords) >= 2 and coords[1] is not None else None
            lon = float(coords[0]) if len(coords) >= 2 and coords[0] is not None else None
            core      = props.get("coreNOTAMData", {})
            notam_obj = core.get("notam", {})
            notam_id  = notam_obj.get("id", "")
            text      = notam_obj.get("text", "") or notam_obj.get("issue", "")
            icao      = notam_obj.get("location", "")
            dedup     = notam_id or f"{icao}_{text[:40]}"
            if dedup in seen_ids:
                continue
            seen_ids.add(dedup)
            results.append({
                "source": "notam", "title": f"NOTAM {icao}: {text[:80]}",
                "description": text[:500], "lat": lat, "lon": lon,
                "country": icao[:2] if icao else "", "category": "notam",
                "raw_ts_utc": props.get("effectiveStartDate", now), "url": "",
                "extra": json.dumps({"icao": icao, "notam_id": notam_id, "source": "faa"}),
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
                data={"grant_type": "client_credentials",
                      "client_id": C.FAA_CLIENT_ID,
                      "client_secret": C.FAA_CLIENT_SECRET},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code == 200:
                return r.json().get("access_token")
    except Exception:
        pass
    return None
