"""
Wardar — Breaking News ingestor

Polls free RSS feeds every 5 min for conflict-related breaking news.
Sources: Google News RSS keyword feeds + BBC/Al Jazeera/Reuters.
Uses Claude Haiku to extract location (with lat/lon), severity (1-5),
event type, and generate a 1-2 sentence plain-English brief.

No API keys required beyond ANTHROPIC_API_KEY.
"""
from __future__ import annotations
import asyncio
import html as _html
import json
import re
import xml.etree.ElementTree as _ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from config import settings as C

# ── RSS feeds to poll ─────────────────────────────────────────────────────────
_FEEDS = [
    # Google News RSS — free, no auth, near-real-time (updates every few min)
    ("https://news.google.com/rss/search?q=missile+strike+airstrike&hl=en-US&gl=US&ceid=US:en",   "keyword"),
    ("https://news.google.com/rss/search?q=drone+attack+military+killed&hl=en-US&gl=US&ceid=US:en", "keyword"),
    ("https://news.google.com/rss/search?q=ukraine+russia+shelling+attack&hl=en-US&gl=US&ceid=US:en", "keyword"),
    ("https://news.google.com/rss/search?q=israel+gaza+hezbollah+strike&hl=en-US&gl=US&ceid=US:en",  "keyword"),
    ("https://news.google.com/rss/search?q=iran+houthi+red+sea+attack&hl=en-US&gl=US&ceid=US:en",    "keyword"),
    ("https://news.google.com/rss/search?q=journalist+killed+reporter+war&hl=en-US&gl=US&ceid=US:en","keyword"),
    # Wire services — reliable, fast
    ("https://feeds.bbci.co.uk/news/world/rss.xml",          "BBC World"),
    ("https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "BBC Middle East"),
    ("https://www.aljazeera.com/xml/rss/all.xml",             "Al Jazeera"),
    ("https://feeds.reuters.com/reuters/worldNews",           "Reuters"),
]

# Pre-filter keywords
_KEYWORDS = [
    "attack", "missile", "drone", "strike", "bomb", "explosion", "airstrike",
    "killed", "dead", "casualties", "destroyed", "military", "troops", "army",
    "navy", "hamas", "hezbollah", "houthi", "ukraine", "russia", "gaza",
    "iran", "israel", "war", "fighting", "shelling", "offensive", "nuclear",
    "ballistic", "warship", "journalist killed", "reporter killed",
    "cyber attack", "infrastructure",
]

# Location lookup (lat, lon, country_name)
_LOC: dict[str, tuple[float, float, str]] = {
    "iran": (32.0, 53.0, "Iran"),
    "tehran": (35.69, 51.39, "Iran"),
    "isfahan": (32.66, 51.68, "Iran"),
    "shiraz": (29.61, 52.53, "Iran"),
    "tabriz": (38.08, 46.29, "Iran"),
    "natanz": (33.73, 51.73, "Iran"),
    "bandar abbas": (27.18, 56.27, "Iran"),
    "strait of hormuz": (26.56, 56.25, "International Waters"),
    "persian gulf": (26.0, 52.0, "International Waters"),
    "israel": (31.0, 35.0, "Israel"),
    "gaza": (31.52, 34.45, "Palestine"),
    "tel aviv": (32.07, 34.78, "Israel"),
    "jerusalem": (31.77, 35.22, "Israel"),
    "west bank": (32.0, 35.25, "Palestine"),
    "lebanon": (33.9, 35.5, "Lebanon"),
    "beirut": (33.89, 35.50, "Lebanon"),
    "syria": (35.0, 38.0, "Syria"),
    "damascus": (33.51, 36.29, "Syria"),
    "aleppo": (36.20, 37.16, "Syria"),
    "iraq": (33.0, 44.0, "Iraq"),
    "baghdad": (33.34, 44.40, "Iraq"),
    "mosul": (36.34, 43.13, "Iraq"),
    "yemen": (15.5, 48.5, "Yemen"),
    "sanaa": (15.37, 44.19, "Yemen"),
    "hodeidah": (14.80, 42.95, "Yemen"),
    "aden": (12.79, 45.04, "Yemen"),
    "ukraine": (49.0, 32.0, "Ukraine"),
    "kyiv": (50.45, 30.52, "Ukraine"),
    "kherson": (46.64, 32.62, "Ukraine"),
    "kharkiv": (49.99, 36.23, "Ukraine"),
    "zaporizhzhia": (47.85, 35.12, "Ukraine"),
    "donetsk": (47.99, 37.80, "Ukraine"),
    "mariupol": (47.10, 37.55, "Ukraine"),
    "bakhmut": (48.60, 38.00, "Ukraine"),
    "avdiivka": (48.14, 37.74, "Ukraine"),
    "russia": (61.5, 90.0, "Russia"),
    "moscow": (55.75, 37.62, "Russia"),
    "belgorod": (50.60, 36.59, "Russia"),
    "kursk": (51.73, 36.19, "Russia"),
    "crimea": (45.0, 34.0, "Ukraine"),
    "sevastopol": (44.60, 33.52, "Ukraine"),
    "red sea": (20.0, 38.0, "International Waters"),
    "bab el-mandeb": (12.58, 43.37, "International Waters"),
    "gulf of aden": (12.5, 47.5, "International Waters"),
    "sudan": (15.0, 30.0, "Sudan"),
    "khartoum": (15.55, 32.53, "Sudan"),
    "myanmar": (19.0, 96.5, "Myanmar"),
    "mali": (17.0, -4.0, "Mali"),
    "burkina faso": (12.36, -1.53, "Burkina Faso"),
    "niger": (17.0, 8.0, "Niger"),
    "somalia": (5.0, 46.0, "Somalia"),
    "ethiopia": (9.0, 40.0, "Ethiopia"),
    "north korea": (40.0, 127.0, "North Korea"),
    "taiwan": (23.7, 121.0, "Taiwan"),
    "pakistan": (30.0, 70.0, "Pakistan"),
}

_ISO_FALLBACK: dict[str, tuple[float, float, str]] = {
    "IL": (31.0, 35.0, "Israel"),
    "PS": (31.9, 35.2, "Palestine"),
    "UA": (49.0, 32.0, "Ukraine"),
    "RU": (61.5, 90.0, "Russia"),
    "SY": (35.0, 38.0, "Syria"),
    "YE": (15.5, 48.5, "Yemen"),
    "LB": (33.9, 35.5, "Lebanon"),
    "IQ": (33.0, 44.0, "Iraq"),
    "IR": (32.0, 53.0, "Iran"),
    "SD": (15.0, 30.0, "Sudan"),
    "MM": (19.0, 96.5, "Myanmar"),
    "AF": (33.0, 65.0, "Afghanistan"),
    "SO": (5.0, 46.0, "Somalia"),
    "LY": (27.0, 17.0, "Libya"),
    "ML": (17.0, -4.0, "Mali"),
    "BF": (12.36, -1.53, "Burkina Faso"),
    "NE": (17.0, 8.0, "Niger"),
    "KP": (40.0, 127.0, "North Korea"),
    "TW": (23.7, 121.0, "Taiwan"),
    "PK": (30.0, 70.0, "Pakistan"),
}

_SEEN_URLS: set[str] = set()
_MAX_SEEN = 5000
_TRIM_TO  = 3000

# XML namespace for media
_MEDIA_NS = "http://search.yahoo.com/mrss/"


def _parse_pubdate(text: str) -> float:
    """Parse RFC 2822 or ISO date string to UTC timestamp."""
    if not text:
        return 0.0
    text = text.strip()
    try:
        return parsedate_to_datetime(text).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    return 0.0


def _strip_html(raw: str) -> str:
    """Strip HTML tags and unescape entities."""
    cleaned = re.sub(r"<[^>]+>", " ", _html.unescape(raw))
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_thumbnail(item_el: _ET.Element, desc_html: str) -> str:
    """Try to extract a thumbnail URL from RSS item element or description HTML."""
    # 1. <media:content url="..."/>
    media_content = item_el.find(f"{{{_MEDIA_NS}}}content")
    if media_content is not None:
        url = media_content.get("url", "")
        if url:
            return url

    # 2. <enclosure url="..." type="image/..."/>
    enclosure = item_el.find("enclosure")
    if enclosure is not None:
        etype = enclosure.get("type", "")
        if "image" in etype:
            url = enclosure.get("url", "")
            if url:
                return url

    # 3. First <img src="..."> in description HTML
    img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_html, re.I)
    if img_m:
        url = img_m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        return url

    return ""


def _parse_google_news_title(title: str) -> tuple[str, str]:
    """
    Google News items have " - Source Name" appended to the title.
    Returns (clean_title, outlet_name).
    """
    m = re.match(r'^(.*?)\s+-\s+([^-]+)\s*$', title.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return title.strip(), ""


async def _ai_classify(items: list[dict]) -> list[dict]:
    """Send batch of RSS items to Claude Haiku for classification + geo-extraction."""
    if not C.ANTHROPIC_API_KEY or not items:
        return []
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=C.ANTHROPIC_API_KEY)

        lines = []
        for i, item in enumerate(items):
            txt = item["title"]
            if item.get("description"):
                txt += " | " + item["description"][:200]
            lines.append(f"{i}: {txt[:500]}")

        prompt = (
            "You are a military OSINT analyst. Classify these news headlines.\n"
            "For each return ONE JSON line:\n"
            '{"i":N,"keep":true/false,"event_type":"airstrike|missile|drone|explosion|naval|ground_attack|journalist_killed|cyber|nuclear|protest|diplomatic|other","location":"most specific place name","country":"ISO2 or null","lat":0.0,"lon":0.0,"severity":1,"brief":"1-2 sentence factual plain English summary of what happened and where","outlet":"news outlet name"}\n\n'
            "RULES:\n"
            "- keep=true ONLY for active military/conflict/security events\n"
            "- severity: 5=mass casualties/strategic strike, 4=significant attack, 3=confirmed incident, 2=minor/early reports, 1=unconfirmed\n"
            "- lat/lon: best coordinate estimate for the specific location; 0.0 only if truly unknown\n"
            "- brief: factual, specific, max 2 sentences — 'X struck Y, killing Z' style\n"
            "- journalist_killed: include any reporter, photographer, media worker killed\n"
            "- Return ONLY JSON lines, no other text\n\n"
            "Headlines:\n" + "\n".join(lines)
        )

        msg = await client.messages.create(
            model=C.AI_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        out = []
        for line in msg.content[0].text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out
    except Exception as exc:
        try:
            from core.engine import log_warn
            log_warn(f"breaking_news: AI error: {exc}")
        except Exception:
            pass
        return []


def _resolve_coords(ai_lat: float, ai_lon: float, location: str | None, country_iso: str | None) -> tuple[float, float, str] | None:
    """Use AI lat/lon if non-zero; otherwise fall back to _LOC dict or _ISO_FALLBACK."""
    if ai_lat and ai_lon and (ai_lat != 0.0 or ai_lon != 0.0):
        # Resolve country name from ISO
        country_name = ""
        if country_iso:
            fb = _ISO_FALLBACK.get((country_iso or "").upper())
            country_name = fb[2] if fb else country_iso
        if not country_name and location:
            loc_lower = location.lower().strip()
            for k, v in _LOC.items():
                if k in loc_lower or loc_lower in k:
                    country_name = v[2]
                    break
        return (ai_lat, ai_lon, country_name or location or "")

    # Fall back to _LOC dict
    if location:
        loc_lower = location.lower().strip()
        if loc_lower in _LOC:
            return _LOC[loc_lower]
        for k, v in _LOC.items():
            if k in loc_lower or loc_lower in k:
                return v

    # Fall back to ISO lookup
    if country_iso:
        return _ISO_FALLBACK.get((country_iso or "").upper())

    return None


async def fetch() -> list[dict]:
    """Fetch breaking news from RSS feeds, AI-classify, return event dicts."""
    global _SEEN_URLS

    if not C.ENABLE_BREAKING_NEWS:
        return []

    try:
        from core.engine import log, log_warn
    except Exception:
        def log(msg): print(msg, flush=True)
        def log_warn(msg): print(f"WARN {msg}", flush=True)

    raw: list[dict] = []
    feed_errors = 0

    async with httpx.AsyncClient(
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; Wardar/1.0 news aggregator)",
            "Accept": "application/rss+xml, text/xml, */*",
        },
        follow_redirects=True,
    ) as client:
        for feed_url, feed_label in _FEEDS:
            try:
                r = await client.get(feed_url, timeout=15)
                if r.status_code in (403, 404, 429):
                    feed_errors += 1
                    continue
                if r.status_code != 200:
                    feed_errors += 1
                    continue

                try:
                    # Register media namespace to avoid parse errors
                    _ET.register_namespace("media", _MEDIA_NS)
                    root = _ET.fromstring(r.content)
                except Exception:
                    feed_errors += 1
                    continue

                items = root.findall(".//item")
                if not items:
                    continue

                is_keyword_feed = feed_label == "keyword"

                for item_el in items:
                    # Extract title
                    t_el = item_el.find("title")
                    raw_title = (t_el.text or "").strip() if t_el is not None else ""
                    if not raw_title:
                        continue

                    # Extract link (dedup key)
                    l_el = item_el.find("link")
                    url = (l_el.text or "").strip() if l_el is not None else ""
                    # Google News sometimes uses <guid> as the canonical URL
                    if not url:
                        g_el = item_el.find("guid")
                        url = (g_el.text or "").strip() if g_el is not None else ""
                    if not url:
                        continue

                    # Dedup
                    if url in _SEEN_URLS:
                        continue

                    # Extract outlet from Google News title suffix or <source> element
                    clean_title = raw_title
                    outlet = feed_label if feed_label != "keyword" else ""

                    if is_keyword_feed:
                        clean_title, parsed_outlet = _parse_google_news_title(raw_title)
                        if parsed_outlet:
                            outlet = parsed_outlet
                    else:
                        # Wire service feeds may have " - Reuters" etc. in title; keep it
                        src_el = item_el.find("source")
                        if src_el is not None and src_el.text:
                            outlet = src_el.text.strip()

                    if not clean_title:
                        continue

                    # Keyword pre-filter
                    title_lower = clean_title.lower()
                    if not any(kw in title_lower for kw in _KEYWORDS):
                        continue

                    # Description / body
                    desc_el = item_el.find("description")
                    desc_html = (desc_el.text or "") if desc_el is not None else ""
                    description = _strip_html(desc_html)[:400]

                    # pubDate
                    pd_el = item_el.find("pubDate")
                    pub_ts = _parse_pubdate((pd_el.text or "") if pd_el is not None else "")

                    # Thumbnail
                    thumbnail_url = _extract_thumbnail(item_el, desc_html)

                    raw.append({
                        "title":         clean_title,
                        "description":   description,
                        "url":           url,
                        "outlet":        outlet,
                        "pub_ts":        pub_ts,
                        "thumbnail_url": thumbnail_url,
                    })

            except Exception as exc:
                feed_errors += 1
                log_warn(f"breaking_news: feed {feed_url[:60]}: {exc}")
                continue

    if not raw:
        if feed_errors:
            log_warn(f"breaking_news: 0 items (feed_errors={feed_errors})")
        return []

    log(f"breaking_news: {len(raw)} items pre-filtered from {len(_FEEDS)-feed_errors}/{len(_FEEDS)} feeds")

    # AI classify in batches of 10
    BATCH = 10
    ai_map: dict[str, dict] = {}
    for i in range(0, len(raw), BATCH):
        batch = raw[i: i + BATCH]
        results = await _ai_classify(batch)
        for r in results:
            idx = r.get("i")
            if idx is not None and 0 <= idx < len(batch):
                ai_map[batch[idx]["url"]] = r

    events: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for item in raw:
        ai = ai_map.get(item["url"], {})
        if not ai.get("keep"):
            continue

        ai_lat  = float(ai.get("lat") or 0.0)
        ai_lon  = float(ai.get("lon") or 0.0)
        ai_loc  = ai.get("location") or ""
        ai_iso  = ai.get("country") or ""

        coords = _resolve_coords(ai_lat, ai_lon, ai_loc, ai_iso)
        if not coords:
            continue

        lat, lon, country_name = coords

        # Use outlet from AI response if available, else from feed parsing
        outlet = ai.get("outlet") or item.get("outlet") or ""

        ts = (
            datetime.fromtimestamp(item["pub_ts"], tz=timezone.utc).isoformat()
            if item.get("pub_ts") else now
        )

        _SEEN_URLS.add(item["url"])

        events.append({
            "source":      "breaking_news",
            "title":       item["title"],
            "description": item.get("description", ""),
            "lat":         lat,
            "lon":         lon,
            "country":     country_name,
            "category":    "osint_crowd",
            "raw_ts_utc":  ts,
            "url":         item["url"],
            "source_url":  item["url"],
            "extra":       json.dumps({
                "event_type":    ai.get("event_type", "other"),
                "severity":      ai.get("severity", 3),
                "brief":         ai.get("brief") or "",
                "outlet":        outlet,
                "ai_location":   ai_loc,
                "thumbnail_url": item.get("thumbnail_url", ""),
            }),
        })

    # Trim seen set if too large
    if len(_SEEN_URLS) > _MAX_SEEN:
        _SEEN_URLS = set(list(_SEEN_URLS)[-_TRIM_TO:])

    log(f"breaking_news: {len(events)} AI-verified events from {len(raw)} pre-filtered items")
    return events
