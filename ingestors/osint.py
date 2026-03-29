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

def _make_rss_fetcher(source: str, url: str, count: int = 20, atom: bool = False):
    """Factory that creates an RSS/Atom fetch coroutine for a given source."""
    async def _fetch() -> list[dict]:
        from core.engine import log_warn
        if not C.ENABLE_DEFENSE_FEEDS:
            return []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "Wardar/0.1 (+https://wardar.app)"})
                if r.status_code != 200:
                    return []
            items = []
            now = datetime.now(timezone.utc).isoformat()
            pattern = r'<entry>(.*?)</entry>' if atom else r'<item>(.*?)</item>'
            for block in re.findall(pattern, r.text, re.DOTALL)[:count]:
                if atom:
                    title = re.search(r'<title[^>]*>(.*?)</title>', block, re.DOTALL)
                    link  = re.search(r'<link[^>]*href="([^"]+)"', block)
                    desc  = re.search(r'<summary[^>]*>(.*?)</summary>', block, re.DOTALL) or \
                            re.search(r'<content[^>]*>(.*?)</content>', block, re.DOTALL)
                else:
                    title = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
                    link  = re.search(r'<link>(.*?)</link>', block, re.DOTALL)
                    desc  = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
                if not title:
                    continue
                t = re.sub(r'<[^>]+>', '', title.group(1)).strip()[:200]
                if atom:
                    u = (link.group(1) if link else '').strip()
                else:
                    u = (link.group(1) if link else '').strip()
                d = re.sub(r'<[^>]+>', '', desc.group(1) if desc else '').strip()[:300]
                if not t:
                    continue
                # Noise filter — skip sports/entertainment articles
                tl = t.lower()
                if any(kw in tl for kw in (
                    'world cup','fifa','nba','nfl','nhl','premier league',
                    'champions league','super bowl','stanley cup','formula 1',
                    'grand prix','olympics','oscar award','grammy award',
                    'box office','celebrity','reality show','bitcoin price',
                    'ethereum price','stock market','earnings call',
                    'match preview','match report','warm-up match',
                    'sports news','transfer news','injury update',
                )):
                    continue
                items.append({
                    'source': source, 'title': t, 'description': d,
                    'lat': None, 'lon': None, 'country': '',
                    'category': 'news', 'raw_ts_utc': now, 'url': u, 'extra': '{}',
                })
            return items
        except Exception as exc:
            log_warn(f'{source}: RSS error: {exc}')
            return []
    _fetch.__name__ = f'_fetch_{source}'
    return _fetch


# ── Additional war/conflict RSS feeds ────────────────────────────────────────
_fetch_isw           = _make_rss_fetcher('isw',        'https://www.iswresearch.org/feeds/posts/default')
_fetch_aljazeera     = _make_rss_fetcher('aljazeera',  'https://www.aljazeera.com/xml/rss/all.xml')
_fetch_middleeastmon = _make_rss_fetcher('mem',        'https://www.middleeastmonitor.com/feed/')
_fetch_toi           = _make_rss_fetcher('toi',        'https://www.timesofisrael.com/feed/')
_fetch_ukrinform     = _make_rss_fetcher('ukrinform',  'https://www.ukrinform.net/rss/block-lastnews')
_fetch_kyivind       = _make_rss_fetcher('kyiv_ind',   'https://kyivindependent.com/feed/')
_fetch_reliefweb     = _make_rss_fetcher('reliefweb',  'https://reliefweb.int/updates/rss.xml')
_fetch_centcom       = _make_rss_fetcher('centcom',    'https://www.centcom.mil/RSS/CENTCOM-News/', atom=True)
_fetch_reuters_world = _make_rss_fetcher('reuters',    'https://feeds.reuters.com/reuters/worldNews')
_fetch_bbc_world     = _make_rss_fetcher('bbc',        'https://feeds.bbci.co.uk/news/world/rss.xml')

# ── New military command + expert feeds ──────────────────────────────────────
_fetch_pentagon      = _make_rss_fetcher('pentagon',   'https://www.defense.gov/News/RSS/')
_fetch_africom       = _make_rss_fetcher('africom',    'https://www.africom.mil/rss/press-releases')
_fetch_navy          = _make_rss_fetcher('navy',       'https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1&max=10')
_fetch_un_peace      = _make_rss_fetcher('un_peace',   'https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml')
_fetch_crisisgroup   = _make_rss_fetcher('crisisgroup','https://www.crisisgroup.org/rss/crisiswatch')
_fetch_state_dept    = _make_rss_fetcher('state_dept', 'https://www.state.gov/rss-feeds/', atom=False)


async def fetch() -> list[dict]:
    from core.engine import log
    import asyncio
    results_list = await asyncio.gather(
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
        _fetch_isw(),
        _fetch_aljazeera(),
        _fetch_middleeastmon(),
        _fetch_toi(),
        _fetch_ukrinform(),
        _fetch_kyivind(),
        _fetch_reliefweb(),
        _fetch_centcom(),
        _fetch_reuters_world(),
        _fetch_bbc_world(),
        # Military command + expert feeds
        _fetch_pentagon(),
        _fetch_africom(),
        _fetch_navy(),
        _fetch_un_peace(),
        _fetch_crisisgroup(),
        _fetch_state_dept(),
    )
    results = []
    for chunk in results_list:
        results.extend(chunk)
    log(f"osint: {len(results)} total articles from {len(results_list)} sources")
    return results
