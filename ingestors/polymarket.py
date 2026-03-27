"""Polymarket geopolitical prediction market ingestor.

Free public API — no key required.
Fetches active prediction markets and filters for geopolitical/conflict topics,
then geocodes them to lat/lon and emits Wardar-schema events.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings as C

_API_URL = "https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=200"

# ── Location map ─────────────────────────────────────────────────────────────
# (keyword_lower, lat, lon, country, label)
_LOCATION_MAP: list[tuple[str, float, float, str, str]] = [
    # Ukraine / Russia
    ("ukraine",      49.0,   32.0,  "Ukraine",      "Ukraine"),
    ("russia",       61.5,   90.0,  "Russia",       "Russia"),
    ("donbas",       48.0,   38.5,  "Ukraine",      "Donbas"),
    ("zaporizhzhia", 47.8,   35.2,  "Ukraine",      "Zaporizhzhia"),
    ("kharkiv",      50.0,   36.2,  "Ukraine",      "Kharkiv"),
    ("crimea",       45.3,   34.1,  "Ukraine",      "Crimea"),
    ("kyiv",         50.4,   30.5,  "Ukraine",      "Kyiv"),
    ("bakhmut",      48.6,   38.0,  "Ukraine",      "Bakhmut"),
    ("kherson",      46.6,   32.6,  "Ukraine",      "Kherson"),
    # Israel / Gaza / Middle East
    ("israel",       31.0,   35.0,  "Israel",       "Israel"),
    ("gaza",         31.4,   34.3,  "Palestine",    "Gaza"),
    ("hamas",        31.4,   34.3,  "Palestine",    "Gaza"),
    ("hezbollah",    33.9,   35.5,  "Lebanon",      "Lebanon"),
    ("west bank",    31.9,   35.2,  "Palestine",    "West Bank"),
    # Iran
    ("iran",         32.0,   53.0,  "Iran",         "Iran"),
    ("tehran",       35.7,   51.4,  "Iran",         "Tehran"),
    # Syria
    ("syria",        35.0,   38.0,  "Syria",        "Syria"),
    ("damascus",     33.5,   36.3,  "Syria",        "Damascus"),
    # Yemen
    ("yemen",        15.5,   48.5,  "Yemen",        "Yemen"),
    ("houthi",       15.5,   44.2,  "Yemen",        "Yemen (Houthi)"),
    # Iraq
    ("iraq",         33.0,   44.0,  "Iraq",         "Iraq"),
    ("baghdad",      33.3,   44.4,  "Iraq",         "Baghdad"),
    # Lebanon
    ("lebanon",      33.9,   35.5,  "Lebanon",      "Lebanon"),
    ("beirut",       33.9,   35.5,  "Lebanon",      "Beirut"),
    # Taiwan / China
    ("taiwan",       23.7,  121.0,  "Taiwan",       "Taiwan"),
    ("taiwan strait",23.9,  119.5,  "Taiwan",       "Taiwan Strait"),
    ("china",        35.0,  105.0,  "China",        "China"),
    ("beijing",      39.9,  116.4,  "China",        "Beijing"),
    ("south china sea", 12.0, 115.0,"China",        "South China Sea"),
    # North Korea
    ("north korea",  40.0,  127.0,  "North Korea",  "North Korea"),
    ("dprk",         40.0,  127.0,  "North Korea",  "North Korea"),
    ("kim jong",     40.0,  127.0,  "North Korea",  "North Korea"),
    # Pakistan
    ("pakistan",     30.0,   70.0,  "Pakistan",     "Pakistan"),
    # Sudan
    ("sudan",        15.0,   30.0,  "Sudan",        "Sudan"),
    ("khartoum",     15.6,   32.5,  "Sudan",        "Khartoum"),
    # Ethiopia
    ("ethiopia",      9.0,   40.0,  "Ethiopia",     "Ethiopia"),
    ("tigray",       14.0,   39.0,  "Ethiopia",     "Tigray"),
    # Somalia
    ("somalia",       5.0,   46.0,  "Somalia",      "Somalia"),
    ("mogadishu",     2.0,   45.3,  "Somalia",      "Mogadishu"),
    ("al-shabaab",    5.0,   46.0,  "Somalia",      "Somalia"),
    # Sahel — Niger / Mali / Burkina Faso
    ("niger",        17.0,    8.0,  "Niger",        "Niger"),
    ("mali",         17.0,   -2.0,  "Mali",         "Mali"),
    ("burkina",      12.0,   -2.0,  "Burkina Faso", "Burkina Faso"),
    ("sahel",        15.0,    0.0,  "Mali",         "Sahel"),
    # NATO / Europe
    ("nato",         50.0,   10.0,  "Belgium",      "NATO/Europe"),
    ("europe",       50.0,   10.0,  "Germany",      "Europe"),
    ("poland",       52.0,   20.0,  "Poland",       "Poland"),
    ("baltics",      56.9,   24.1,  "Latvia",       "Baltics"),
    ("finland",      65.0,   26.0,  "Finland",      "Finland"),
    # USA
    ("united states", 38.0, -97.0, "USA",           "United States"),
    ("usa",           38.0, -97.0, "USA",           "United States"),
    ("washington",    38.9, -77.0, "USA",           "Washington DC"),
]

# ── Conflict-relevant tag / keyword filters ───────────────────────────────────
_CONFLICT_TAGS = {
    "geopolitics", "politics", "war", "conflict", "international",
    "ukraine", "russia", "middle-east", "china", "israel", "iran",
    "nato", "taiwan",
}

_CONFLICT_KEYWORDS = {kw for kw, *_ in _LOCATION_MAP} | {
    "missile", "nuclear", "military", "sanction", "ceasefire", "invasion",
    "airstrike", "drone", "troops", "attack", "bomb", "refugee", "coup",
    "election", "treaty", "alliance", "weapon", "surrender", "offensive",
}


def _is_geopolitical(market: dict) -> bool:
    """Return True if this market looks geopolitically relevant."""
    question = (market.get("question") or "").lower()
    tags = {(t.get("label") or t.get("id") or "").lower()
            for t in (market.get("tags") or [])
            if isinstance(t, dict)}
    tags |= {(t if isinstance(t, str) else "").lower() for t in (market.get("tags") or [])}

    # Tag match
    if tags & _CONFLICT_TAGS:
        return True

    # Keyword match in question
    for kw in _CONFLICT_KEYWORDS:
        if kw in question:
            return True

    return False


def _geocode(question: str) -> tuple[float, float, str, str] | None:
    """
    Try to find a lat/lon/country/label for this question text.
    Returns (lat, lon, country, label) or None.
    """
    q = question.lower()
    for kw, lat, lon, country, label in _LOCATION_MAP:
        if kw in q:
            return lat, lon, country, label
    return None


def _parse_prob(outcome_prices: list) -> float | None:
    """Parse YES probability from outcomePrices[0] (e.g. '0.62' → 62.0)."""
    try:
        if outcome_prices:
            return round(float(outcome_prices[0]) * 100, 1)
    except (TypeError, ValueError, IndexError):
        pass
    return None


async def fetch() -> list[dict]:
    """Fetch Polymarket geopolitical markets and return Wardar events."""
    if not C.ENABLE_POLYMARKET:
        return []

    try:
        from core.engine import log, log_warn
    except Exception:
        return []

    try:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Wardar/0.1"}) as client:
            r = await client.get(_API_URL)
            if r.status_code != 200:
                log_warn(f"polymarket: HTTP {r.status_code}")
                return []
            markets: list[dict] = r.json()
    except Exception as exc:
        try:
            log_warn(f"polymarket: fetch error: {exc}")
        except Exception:
            pass
        return []

    events: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for market in markets:
        try:
            if not _is_geopolitical(market):
                continue

            question = (market.get("question") or "").strip()
            if not question:
                continue

            geo = _geocode(question)
            if not geo:
                continue

            lat, lon, country, label = geo

            outcome_prices = market.get("outcomePrices") or []
            prob = _parse_prob(outcome_prices)
            volume = market.get("volume")
            slug = market.get("slug") or ""
            end_date = market.get("endDate") or ""

            url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

            # Build description
            desc_parts = [question]
            if prob is not None:
                desc_parts.append(f"YES probability: {prob}%")
            if volume:
                try:
                    desc_parts.append(f"Volume: ${float(volume):,.0f}")
                except (TypeError, ValueError):
                    pass
            if end_date:
                desc_parts.append(f"Closes: {end_date[:10]}")
            description = " | ".join(desc_parts)

            extra: dict[str, Any] = {}
            if prob is not None:
                extra["prob_pct"] = prob
            if volume is not None:
                try:
                    extra["volume"] = float(volume)
                except (TypeError, ValueError):
                    extra["volume"] = volume
            if end_date:
                extra["end_date"] = end_date
            extra["location_label"] = label

            events.append({
                "source":      "polymarket",
                "title":       question[:200],
                "description": description[:1000],
                "lat":         lat,
                "lon":         lon,
                "country":     country,
                "category":    "prediction_market",
                "url":         url,
                "raw_ts_utc":  now,
                "extra":       json.dumps(extra),
            })
        except Exception:
            continue

    log(f"polymarket: {len(events)} geopolitical markets")
    return events
