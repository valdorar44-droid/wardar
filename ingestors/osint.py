"""OSINT ingestor — GDELT + news headlines

Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json, re
from datetime import datetime, timedelta, timezone

import httpx

from config import settings as C

# GDELT 2.0 Doc API
_GDELT_THEMES = "CRISISLEX_CRISISLEXREC,MILITARY,TERROR,PROTEST,CONFLICT"
_GDELT_TIMESPAN = "1440"  # last 24h in minutes

async def _fetch_gdelt() -> list[dict]:
    from core.engine import log_warn
    if not C.ENABLE_GDELT:
        return []
    params = {
        "query":     f"themecode:{_GDELT_THEMES}",
        "mode":      "artlist",
        "maxrecords": 50,
        "timespan":  _GDELT_TIMESPAN,
        "format":    "json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(C.GDELT_URL, params=params)
            if r.status_code == 429:
                log_warn("gdelt: rate limited — skipping (news RSS will cover)")
                return []
            if r.status_code != 200:
                log_warn(f"gdelt: HTTP {r.status_code}")
                return []
            try:
                articles = r.json().get("articles") or []
            except Exception:
                log_warn("gdelt: invalid JSON response — skipping")
                return []
    except Exception as exc:
        log_warn(f"gdelt: fetch error: {exc}")
        return []

    results = []
    now = datetime.now(timezone.utc).isoformat()
    for a in articles:
        try:
            # GDELT doesn't include lat/lon in article list — use 0,0 as placeholder
            # Events without coordinates are still useful for the news ticker
            results.append({
                "source":      "gdelt",
                "title":       str(a.get("title", ""))[:200],
                "description": str(a.get("seendate", ""))[:200],
                "lat":         None,
                "lon":         None,
                "country":     str(a.get("sourcecountry") or ""),
                "category":    "news",
                "raw_ts_utc":  now,
                "url":         str(a.get("url", "")),
                "extra":       json.dumps({"domain": a.get("domain"), "language": a.get("language")}),
            })
        except Exception:
            continue
    return results

async def _fetch_news_rss(query: str = "war military conflict", count: int = 30) -> list[dict]:
    """Google News RSS as a free fallback news feed."""
    from core.engine import log_warn
    if not C.ENABLE_OSINT_NEWS:
        return []
    import urllib.parse
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL)[:count]:
            title = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
            link  = re.search(r"<link>(.*?)</link>",   block, re.DOTALL)
            desc  = re.search(r"<description>(.*?)</description>", block, re.DOTALL)
            if not title:
                continue
            t = re.sub(r"<[^>]+>", "", title.group(1)).strip()[:200]
            u = (link.group(1) if link else "").strip()
            d = re.sub(r"<[^>]+>", "", desc.group(1) if desc else "").strip()[:300]
            items.append({
                "source":      "osint_news",
                "title":       t,
                "description": d,
                "lat":         None,
                "lon":         None,
                "country":     "",
                "category":    "news",
                "raw_ts_utc":  now,
                "url":         u,
                "extra":       "{}",
            })
        return items
    except Exception as exc:
        log_warn(f"osint_news: RSS fetch error: {exc}")
        return []

async def fetch() -> list[dict]:
    from core.engine import log
    import asyncio
    gdelt, news = await asyncio.gather(_fetch_gdelt(), _fetch_news_rss())
    results = gdelt + news
    log(f"osint: {len(results)} articles (gdelt={len(gdelt)}, news={len(news)})")
    return results
