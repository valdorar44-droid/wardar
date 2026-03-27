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

# ── Hard blocklist — disqualifies a market immediately ───────────────────────
# Any market containing these terms is rejected before any other check.
_BLOCKLIST = {
    # Sports — leagues, trophies, events
    "stanley cup","nba finals","super bowl","world series","nfl draft",
    "nba draft","mlb","nhl","nba championship","nfl championship",
    "champions league","premier league","la liga","serie a","bundesliga",
    "fifa world cup","world cup winner","world cup champion","world cup 2026","world cup 2030",
    "win the world cup","world cup qualifier","world cup final","world cup group",
    "world cup golden boot","ballon d'or","pga tour","masters tournament",
    "wimbledon","us open","australian open","french open","formula 1",
    "grand prix","nascar","indy 500","ufc","mma title","boxing champion",
    "olympic gold","olympic medal","winter games","summer games",
    "march madness","college football playoff","heisman","ncaa",
    "basketball championship","football championship","soccer championship",
    # Entertainment / pop culture
    "oscar","academy award","emmy award","grammy award","golden globe",
    "box office","album of the year","song of the year","best actor",
    "best actress","best picture","spotify","netflix original",
    "reality show","reality tv","american idol","the voice","survivor",
    "celebrity","kardashian","taylor swift","beyonce","drake","kanye",
    # Crypto price speculation
    "bitcoin price","ethereum price","btc price","eth price","sol price",
    "crypto price","market cap","defi protocol","nft collection",
    "token price","coin price",
    # Pure finance (non-geopolitical)
    "stock price","earnings report","ipo","interest rate cut",
    "federal reserve rate","s&p 500","dow jones","nasdaq",
    # Weather (non-conflict context)
    "hurricane season","tropical storm","tornado outbreak",
}

# ── Specific conflict locations — presence alone is enough ───────────────────
_SPECIFIC_CONFLICT_LOCS = {
    "ukraine","donbas","zaporizhzhia","kharkiv","crimea","kyiv","bakhmut",
    "kherson","mariupol","avdiivka","kursk",
    "gaza","hamas","hezbollah","west bank","rafah","jenin",
    "iran","tehran","irgc","revolutionary guard",
    "syria","damascus","aleppo","idlib",
    "yemen","houthi","sanaa","hodeidah",
    "iraq","baghdad","mosul","isis","isil",
    "sudan","darfur","rsf","khartoum",
    "somalia","al-shabaab","mogadishu",
    "north korea","dprk","kim jong",
    "taiwan strait","south china sea","pla navy",
    "myanmar","coup","junta","shan state",
    "sahel","burkina faso","mali junta","niger coup",
    "eritrea","tigray","amhara",
    "nagorno","karabakh","armenia azerbaijan",
}

# ── Hard conflict keywords — presence alone is enough ────────────────────────
_HARD_CONFLICT_KW = {
    "war","invasion","ceasefire","airstrike","missile strike",
    "nuclear weapon","nuclear warhead","drone strike","troop withdrawal",
    "troop deployment","military offensive","ground offensive",
    "sanctions imposed","arms embargo","war crimes","genocide",
    "siege","blockade","annexation","occupation force",
    "terrorist attack","insurgency","coup attempt","regime change",
    "refugee crisis","humanitarian corridor",
}

# ── Broad locations that need a supporting conflict keyword ──────────────────
_BROAD_LOCS = {
    "russia","china","israel","nato","europe","united states","usa",
    "pakistan","taiwan","beijing","poland","finland","baltics","estonia",
    "latvia","lithuania","turkey","saudi arabia","venezuela","cuba",
    "belarus","moldova","georgia",
}
_SOFT_CONFLICT_KW = {
    "conflict","military","troops","attack","bomb","nuclear","sanction",
    "weapon","coup","opposition","protest","regime","election interference",
    "election fraud","disinformation","territorial","sovereignty",
    "ceasefire","peace deal","negotiation","escalation",
}
_CONFLICT_TAGS = {
    "war","conflict","geopolitics","ukraine","russia","middle-east",
    "israel","iran","nato","taiwan","north-korea","military",
}


def _is_geopolitical(market: dict) -> bool:
    """
    Three-stage filter:
    1. BLOCKLIST — reject sports/entertainment/crypto immediately
    2. SPECIFIC conflict locations — accept unconditionally
    3. HARD conflict keywords — accept unconditionally
    4. BROAD location + supporting conflict keyword/tag — accept
    """
    question = (market.get("question") or "").lower()
    tags = set()
    for t in (market.get("tags") or []):
        if isinstance(t, dict):
            tags.add((t.get("label") or t.get("id") or "").lower())
        elif isinstance(t, str):
            tags.add(t.lower())

    # Stage 1 — hard blocklist (sports, entertainment, crypto)
    for term in _BLOCKLIST:
        if term in question:
            return False

    # Stage 2 — specific conflict locations need no further checks
    for loc in _SPECIFIC_CONFLICT_LOCS:
        if loc in question:
            return True

    # Stage 3 — hard conflict keywords
    for kw in _HARD_CONFLICT_KW:
        if kw in question:
            return True

    # Stage 4 — broad location must also have a conflict keyword or tag
    has_broad = any(loc in question for loc in _BROAD_LOCS)
    has_soft  = any(kw in question for kw in _SOFT_CONFLICT_KW)
    has_tag   = bool(tags & _CONFLICT_TAGS)

    return has_broad and (has_soft or has_tag)


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
