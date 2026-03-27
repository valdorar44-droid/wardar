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

async def _fetch_twz_rss(count: int = 20) -> list[dict]:
    """The War Zone RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://www.thedrive.com/the-war-zone/rss'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'twz',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'twz: RSS error: {exc}')
        return []

async def _fetch_usni_rss(count: int = 20) -> list[dict]:
    """USNI News RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://news.usni.org/feed'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'usni',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'usni: RSS error: {exc}')
        return []

async def _fetch_bellingcat_rss(count: int = 20) -> list[dict]:
    """Bellingcat RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://www.bellingcat.com/feed/'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'bellingcat',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'bellingcat: RSS error: {exc}')
        return []

async def _fetch_oryx_rss(count: int = 20) -> list[dict]:
    """Oryx OSINT RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://www.oryxspioenkop.com/feeds/posts/default'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'oryx',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'oryx: RSS error: {exc}')
        return []

async def _fetch_defnews_rss(count: int = 20) -> list[dict]:
    """Defense News RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://www.defensenews.com/arc/outboundfeeds/rss/'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'defense_news',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'defense_news: RSS error: {exc}')
        return []

async def _fetch_defone_rss(count: int = 20) -> list[dict]:
    """Defense One RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://www.defenseone.com/rss/'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'defense_one',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'defense_one: RSS error: {exc}')
        return []

async def _fetch_ukmod_rss(count: int = 20) -> list[dict]:
    """UK Ministry of Defence Atom feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://www.gov.uk/search/news-and-communications.atom?organisations[]=ministry-of-defence'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<entry>(.*?)</entry>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link[^>]*href="([^"]+)"', block)
            desc  = re.search(r'<summary>(.*?)</summary>', block, re.DOTALL) or \
                    re.search(r'<content[^>]*>(.*?)</content>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'ukmod',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': 'GB',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'ukmod: RSS error: {exc}')
        return []

async def _fetch_rusi_rss(count: int = 20) -> list[dict]:
    """RUSI RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://rusi.org/rss.xml'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'rusi',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'rusi: RSS error: {exc}')
        return []

async def _fetch_gcaptain_rss(count: int = 20) -> list[dict]:
    """gCaptain maritime RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://gcaptain.com/feed/'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'gcaptain',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'gcaptain: RSS error: {exc}')
        return []

async def _fetch_krebs_rss(count: int = 20) -> list[dict]:
    """Krebs on Security RSS feed."""
    from core.engine import log_warn
    if not C.ENABLE_DEFENSE_FEEDS:
        return []
    url = 'https://krebsonsecurity.com/feed/'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:count]:
            title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>',   block, re.DOTALL)
            desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            if not title: continue
            t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
            u = (link.group(1) if link else '').strip()
            d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
            items.append({
                'source': 'krebs',
                'title': t, 'description': d,
                'lat': None, 'lon': None, 'country': '',
                'category': 'news',
                'raw_ts_utc': now, 'url': u, 'extra': '{}',
            })
        return items
    except Exception as exc:
        log_warn(f'krebs: RSS error: {exc}')
        return []

async def fetch() -> list[dict]:
    from core.engine import log
    import asyncio
    (
        gdelt, news,
        twz, usni, bellingcat, oryx,
        defnews, defone, ukmod, rusi,
        gcaptain, krebs,
    ) = await asyncio.gather(
        _fetch_gdelt(),
        _fetch_news_rss(),
        _fetch_twz_rss(),
        _fetch_usni_rss(),
        _fetch_bellingcat_rss(),
        _fetch_oryx_rss(),
        _fetch_defnews_rss(),
        _fetch_defone_rss(),
        _fetch_ukmod_rss(),
        _fetch_rusi_rss(),
        _fetch_gcaptain_rss(),
        _fetch_krebs_rss(),
    )
    results = (
        gdelt + news +
        twz + usni + bellingcat + oryx +
        defnews + defone + ukmod + rusi +
        gcaptain + krebs
    )
    log(
        f"osint: {len(results)} articles ("
        f"gdelt={len(gdelt)}, news={len(news)}, "
        f"twz={len(twz)}, usni={len(usni)}, bellingcat={len(bellingcat)}, "
        f"oryx={len(oryx)}, defnews={len(defnews)}, defone={len(defone)}, "
        f"ukmod={len(ukmod)}, rusi={len(rusi)}, "
        f"gcaptain={len(gcaptain)}, krebs={len(krebs)})"
    )
    return results
