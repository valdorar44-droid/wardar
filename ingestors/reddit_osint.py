"""
Reddit OSINT ingestor — AI-filtered conflict intelligence from crowd-sourced reports.

No Reddit API key required (uses public JSON endpoint).
Uses Claude AI to filter noise and extract event/location data.
Focuses on: Iran, Middle East, Ukraine, Gaza, Yemen, Sudan, Myanmar + global conflicts.
"""
from __future__ import annotations
import asyncio
import html as _html
import json
import re
import xml.etree.ElementTree as _ET
from datetime import datetime, timezone

import httpx

_ATOM = "http://www.w3.org/2005/Atom"

from config import settings as C

# ── Subreddits ordered by signal quality ─────────────────────────────────────
_SUBREDDITS = [
    # High signal — verified footage and geolocated reports
    "CombatFootage",
    "UkraineWarVideoReport",
    "war",
    # Iran / Middle East priority
    "iran",
    "middleeast",
    "IsraelPalestine",
    "syriancivilwar",
    "Yemen",
    "lebanon",
    "geopolitics",
    # Ukraine
    "ukraine",
    # Africa conflicts
    "Sudan",
    # Broad news with conflict filtering
    "worldnews",
]

# Pre-filter keywords — must match at least one (case-insensitive)
_KEYWORDS = [
    "attack","missile","drone","strike","bomb","explosion","airstrike","air strike",
    "killed","dead","casualties","fatalities","wounded","destroyed","shot down",
    "military","troops","soldiers","army","navy","air force","irgc","revolutionary guard",
    "hamas","hezbollah","houthi","wagner","isis","isil","al-qaeda",
    "ukraine","russia","gaza","iran","israel","idf","raf","usaf",
    "tank","artillery","warship","frigate","destroyer","submarine","carrier",
    "conflict","war","fighting","offensive","shelling","advance","retreat",
    "captured","frontline","front line","counter-offensive",
    "nuclear","ballistic","cruise missile","hypersonic","icbm","patriot","s-400",
    "f-35","su-57","mig","himars","iskander","shahed","kalibr",
    "sanctions","embargo","blockade","seized","intercepted",
    "red sea","strait of hormuz","bab el-mandeb","black sea",
]

# ── Location coordinate lookup (city/region → lat, lon, country_name) ────────
_LOC = {
    # Iran (priority)
    "iran": (32.0, 53.0, "Iran"),
    "tehran": (35.69, 51.39, "Iran"),
    "isfahan": (32.66, 51.68, "Iran"),
    "shiraz": (29.61, 52.53, "Iran"),
    "tabriz": (38.08, 46.29, "Iran"),
    "mashhad": (36.31, 59.60, "Iran"),
    "ahvaz": (31.32, 48.67, "Iran"),
    "bandar abbas": (27.18, 56.27, "Iran"),
    "qom": (34.64, 50.88, "Iran"),
    "natanz": (33.73, 51.73, "Iran"),
    "fordow": (34.88, 50.60, "Iran"),
    "arak": (34.09, 49.69, "Iran"),
    "kharg island": (29.26, 50.33, "Iran"),
    "strait of hormuz": (26.56, 56.25, "International Waters"),
    "persian gulf": (26.0, 52.0, "International Waters"),
    # Israel / Palestine
    "israel": (31.0, 35.0, "Israel"),
    "gaza": (31.52, 34.45, "Palestine"),
    "tel aviv": (32.07, 34.78, "Israel"),
    "jerusalem": (31.77, 35.22, "Israel"),
    "haifa": (32.82, 34.99, "Israel"),
    "west bank": (32.0, 35.25, "Palestine"),
    "ramallah": (31.90, 35.21, "Palestine"),
    "rafah": (31.28, 34.24, "Palestine"),
    "khan younis": (31.35, 34.31, "Palestine"),
    "jenin": (32.46, 35.30, "Palestine"),
    "nablus": (32.22, 35.26, "Palestine"),
    "eilat": (29.56, 34.95, "Israel"),
    "negev": (30.7, 34.8, "Israel"),
    # Lebanon
    "lebanon": (33.9, 35.5, "Lebanon"),
    "beirut": (33.89, 35.50, "Lebanon"),
    "southern lebanon": (33.3, 35.4, "Lebanon"),
    "south lebanon": (33.3, 35.4, "Lebanon"),
    "tyre": (33.27, 35.20, "Lebanon"),
    "sidon": (33.56, 35.37, "Lebanon"),
    # Syria
    "syria": (35.0, 38.0, "Syria"),
    "damascus": (33.51, 36.29, "Syria"),
    "aleppo": (36.20, 37.16, "Syria"),
    "idlib": (35.93, 36.63, "Syria"),
    "deir ez-zor": (35.34, 40.14, "Syria"),
    "homs": (34.73, 36.72, "Syria"),
    "latakia": (35.52, 35.78, "Syria"),
    "raqqa": (35.95, 39.00, "Syria"),
    # Iraq
    "iraq": (33.0, 44.0, "Iraq"),
    "baghdad": (33.34, 44.39, "Iraq"),
    "mosul": (36.34, 43.13, "Iraq"),
    "erbil": (36.19, 44.01, "Iraq"),
    "kirkuk": (35.47, 44.39, "Iraq"),
    "basra": (30.51, 47.82, "Iraq"),
    "fallujah": (33.36, 43.78, "Iraq"),
    # Yemen
    "yemen": (15.5, 48.5, "Yemen"),
    "sanaa": (15.35, 44.21, "Yemen"),
    "hodeidah": (14.80, 42.95, "Yemen"),
    "aden": (12.78, 45.04, "Yemen"),
    "marib": (15.47, 45.33, "Yemen"),
    "taiz": (13.58, 44.02, "Yemen"),
    # Red Sea / Houthis
    "red sea": (20.0, 38.0, "International Waters"),
    "bab el-mandeb": (12.58, 43.37, "International Waters"),
    "gulf of aden": (12.0, 46.0, "International Waters"),
    "aden gulf": (12.0, 46.0, "International Waters"),
    # Ukraine
    "ukraine": (49.0, 32.0, "Ukraine"),
    "kyiv": (50.45, 30.52, "Ukraine"),
    "kharkiv": (49.99, 36.23, "Ukraine"),
    "kherson": (46.64, 32.61, "Ukraine"),
    "zaporizhzhia": (47.85, 35.11, "Ukraine"),
    "donetsk": (48.0, 37.8, "Ukraine"),
    "mariupol": (47.10, 37.55, "Ukraine"),
    "bakhmut": (48.60, 37.99, "Ukraine"),
    "avdiivka": (48.14, 37.75, "Ukraine"),
    "odessa": (46.49, 30.74, "Ukraine"),
    "mykolaiv": (46.98, 31.99, "Ukraine"),
    "sumy": (50.91, 34.80, "Ukraine"),
    "kursk": (51.73, 36.19, "Russia"),
    # Russia
    "russia": (61.5, 90.0, "Russia"),
    "moscow": (55.75, 37.62, "Russia"),
    "belgorod": (50.60, 36.59, "Russia"),
    "st. petersburg": (59.95, 30.32, "Russia"),
    "crimea": (45.0, 34.0, "Ukraine/Russia"),
    "sevastopol": (44.60, 33.53, "Ukraine/Russia"),
    # Sudan
    "sudan": (15.0, 30.0, "Sudan"),
    "khartoum": (15.55, 32.53, "Sudan"),
    "darfur": (13.0, 24.0, "Sudan"),
    "el fasher": (13.63, 25.36, "Sudan"),
    "port sudan": (19.61, 37.22, "Sudan"),
    # Myanmar
    "myanmar": (19.0, 96.5, "Myanmar"),
    "rangoon": (16.87, 96.19, "Myanmar"),
    "yangon": (16.87, 96.19, "Myanmar"),
    "mandalay": (21.97, 96.08, "Myanmar"),
    "rakhine": (20.0, 93.5, "Myanmar"),
    # Libya
    "libya": (27.0, 17.0, "Libya"),
    "tripoli": (32.90, 13.18, "Libya"),
    "benghazi": (32.12, 20.07, "Libya"),
    # Somalia
    "somalia": (5.0, 46.0, "Somalia"),
    "mogadishu": (2.05, 45.34, "Somalia"),
    # Ethiopia
    "ethiopia": (9.0, 40.0, "Ethiopia"),
    "addis ababa": (9.02, 38.74, "Ethiopia"),
    "tigray": (14.0, 39.0, "Ethiopia"),
    # DRC
    "drc": (-4.0, 21.0, "DR Congo"),
    "congo": (-4.0, 21.0, "DR Congo"),
    "goma": (-1.68, 29.22, "DR Congo"),
    "eastern congo": (-1.5, 28.5, "DR Congo"),
    # Pakistan/Afghanistan
    "pakistan": (30.0, 70.0, "Pakistan"),
    "afghanistan": (33.0, 65.0, "Afghanistan"),
    "kabul": (34.52, 69.18, "Afghanistan"),
    # North Korea
    "north korea": (40.0, 127.0, "North Korea"),
    "dprk": (40.0, 127.0, "North Korea"),
    # Taiwan
    "taiwan": (23.7, 121.0, "Taiwan"),
    "taipei": (25.04, 121.51, "Taiwan"),
    "taiwan strait": (24.0, 119.5, "International Waters"),
    # South China Sea
    "south china sea": (12.0, 115.0, "International Waters"),
    "spratly": (10.0, 114.0, "International Waters"),
    # Venezuela
    "venezuela": (8.0, -66.0, "Venezuela"),
    # Sahel
    "niger": (17.0, 8.0, "Niger"),
    "mali": (17.0, -4.0, "Mali"),
    "burkina faso": (12.36, -1.53, "Burkina Faso"),
    "sahel": (15.0, 0.0, "West Africa"),
    "nigeria": (9.07, 7.40, "Nigeria"),
}

# Country ISO → fallback centroid
_ISO_FALLBACK = {
    "IR": (32.0, 53.0, "Iran"),
    "IL": (31.5, 35.0, "Israel"),
    "PS": (31.9, 35.2, "Palestine"),
    "UA": (49.0, 32.0, "Ukraine"),
    "RU": (61.5, 90.0, "Russia"),
    "SY": (35.0, 38.0, "Syria"),
    "YE": (15.5, 48.5, "Yemen"),
    "LB": (33.9, 35.5, "Lebanon"),
    "IQ": (33.0, 44.0, "Iraq"),
    "SD": (15.0, 30.0, "Sudan"),
    "MM": (19.0, 96.5, "Myanmar"),
    "AF": (33.0, 65.0, "Afghanistan"),
    "PK": (30.0, 70.0, "Pakistan"),
    "KP": (40.0, 127.0, "North Korea"),
    "TW": (23.7, 121.0, "Taiwan"),
    "VE": (8.0, -66.0, "Venezuela"),
    "SO": (5.0, 46.0, "Somalia"),
    "ET": (9.0, 40.0, "Ethiopia"),
    "CD": (-4.0, 21.0, "DR Congo"),
    "LY": (27.0, 17.0, "Libya"),
    "NE": (17.0, 8.0, "Niger"),
    "ML": (17.0, -4.0, "Mali"),
    "BF": (12.36, -1.53, "Burkina Faso"),
    "NG": (9.07, 7.40, "Nigeria"),
    "BY": (53.7, 27.9, "Belarus"),
    "GE": (41.7, 44.8, "Georgia"),
    "AZ": (40.4, 49.9, "Azerbaijan"),
}

_SEEN_URLS: set[str] = set()
_MAX_SEEN = 3000


async def _ai_classify(posts: list[dict]) -> list[dict]:
    """Send batch of posts to Claude for conflict classification + location extraction."""
    if not C.ANTHROPIC_API_KEY or not posts:
        return []
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=C.ANTHROPIC_API_KEY)

        lines = []
        for i, p in enumerate(posts):
            txt = p["title"]
            if p.get("selftext"):
                txt += " | " + p["selftext"][:150]
            lines.append(f"{i}: {txt[:280]}")

        prompt = (
            "You are a military OSINT analyst. Classify these Reddit posts.\n"
            "For each return ONE JSON object per line:\n"
            '{"i":N,"keep":true/false,"type":"missile_strike|airstrike|explosion|aircraft_down|naval|troop_movement|equipment_loss|protest|drone_attack|sanctions|nuclear|other","location":"city or region or null","country":"2-letter ISO or null","confidence":"high|medium|low"}\n\n'
            "RULES:\n"
            "- keep=true ONLY for real, current military/conflict events (not opinion, meme, historical, game, or fiction)\n"
            "- Iran/Middle East/Houthi/IRGC/Hamas/Hezbollah events: flag as high priority\n"
            "- location = most specific real place name (city preferred over country)\n"
            "- confidence=low → keep=false\n"
            "Return ONLY JSON lines, no other text.\n\n"
            "Posts:\n" + "\n".join(lines)
        )

        msg = await client.messages.create(
            model=C.AI_MODEL,
            max_tokens=1200,
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
            log_warn(f"reddit_osint: AI error: {exc}")
        except Exception:
            pass
        return []


def _resolve(location: str | None, country_iso: str | None) -> tuple[float, float, str] | None:
    if location:
        loc = location.lower().strip()
        if loc in _LOC:
            return _LOC[loc]
        # Partial match
        for k, v in _LOC.items():
            if k in loc or loc in k:
                return v
    if country_iso:
        return _ISO_FALLBACK.get(country_iso.upper())
    return None


async def fetch() -> list[dict]:
    """Fetch Reddit conflict posts, AI-filter, geocode, return event dicts."""
    global _SEEN_URLS

    if not C.ENABLE_REDDIT_OSINT:
        return []

    from core.engine import log, log_warn

    raw: list[dict] = []

    blocked_subs = 0
    async with httpx.AsyncClient(
        timeout=20,
        headers={
            # RSS endpoint is less aggressively rate-limited from cloud IPs than JSON
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/atom+xml, text/xml, */*",
        },
        follow_redirects=True,
    ) as client:
        for sub in _SUBREDDITS:
            try:
                r = await client.get(
                    f"https://www.reddit.com/r/{sub}/new.rss",
                    params={"limit": 25},
                )
                if r.status_code == 429:
                    log_warn(f"reddit_osint: r/{sub} rate-limited (429) — sleeping 15s")
                    await asyncio.sleep(15)
                    continue
                if r.status_code == 403:
                    log_warn(f"reddit_osint: r/{sub} RSS blocked (403)")
                    blocked_subs += 1
                    continue
                if r.status_code != 200:
                    log_warn(f"reddit_osint: r/{sub} HTTP {r.status_code}")
                    continue

                # Parse Atom/RSS XML
                try:
                    root = _ET.fromstring(r.content)
                except Exception as xe:
                    log_warn(f"reddit_osint: r/{sub} XML parse error: {xe}")
                    blocked_subs += 1
                    continue

                # Support both Atom (<feed>) and RSS 2.0 (<rss><channel><item>)
                entries = root.findall(f"{{{_ATOM}}}entry")
                if not entries:
                    # RSS 2.0 fallback
                    entries = root.findall(".//item")
                if not entries:
                    log_warn(f"reddit_osint: r/{sub} RSS returned 0 entries (soft-blocked?)")
                    blocked_subs += 1
                    continue

                for entry in entries:
                    # ── Title ──────────────────────────────────────────────────
                    t_el = entry.find(f"{{{_ATOM}}}title") or entry.find("title")
                    title = (t_el.text or "") if t_el is not None else ""
                    if not title:
                        continue

                    # ── URL ────────────────────────────────────────────────────
                    l_el = entry.find(f"{{{_ATOM}}}link") or entry.find("link")
                    if l_el is not None:
                        url = l_el.get("href") or l_el.text or ""
                    else:
                        url = ""
                    if not url:
                        continue
                    # Normalize to https://reddit.com/...
                    url = url.replace("https://www.reddit.com", "https://reddit.com")
                    if url in _SEEN_URLS:
                        continue

                    # ── Keyword pre-filter ─────────────────────────────────────
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in _KEYWORDS):
                        continue

                    # ── Timestamp ──────────────────────────────────────────────
                    ts_el = (entry.find(f"{{{_ATOM}}}updated")
                             or entry.find(f"{{{_ATOM}}}published")
                             or entry.find("pubDate"))
                    created = 0.0
                    if ts_el is not None and ts_el.text:
                        try:
                            created = datetime.fromisoformat(
                                ts_el.text.replace("Z", "+00:00")
                            ).timestamp()
                        except Exception:
                            pass

                    # ── Content HTML (for image + selftext extraction) ──────────
                    c_el = entry.find(f"{{{_ATOM}}}content") or entry.find("description")
                    content_raw = (c_el.text or "") if c_el is not None else ""
                    # Atom content is HTML-entity-encoded; decode it
                    content_html = _html.unescape(content_raw)

                    # Extract selftext (strip HTML tags)
                    selftext = re.sub(r"<[^>]+>", " ", content_html).strip()
                    selftext = re.sub(r"\s+", " ", selftext)[:300]

                    # Extract thumbnail / preview image from content HTML
                    preview_url = ""
                    img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_html, re.I)
                    if img_m:
                        preview_url = img_m.group(1)
                        if preview_url.startswith("//"):
                            preview_url = "https:" + preview_url

                    # Extract linked URL (first href inside content)
                    linked_url = ""
                    href_m = re.search(r'href=["\']([^"\']+)["\']', content_html, re.I)
                    if href_m:
                        linked_url = href_m.group(1)

                    # Media classification
                    is_video   = "v.redd.it" in linked_url or "video" in title_lower
                    is_gallery = "gallery" in (linked_url or "") or "gallery" in title_lower
                    media_url  = ""
                    if not is_video and linked_url:
                        if any(linked_url.lower().endswith(ext) for ext in (".jpg",".jpeg",".png",".gif",".webp")):
                            media_url = linked_url
                        elif "i.redd.it" in linked_url or "i.imgur.com" in linked_url:
                            media_url = linked_url

                    raw.append({
                        "title":       title[:300],
                        "selftext":    selftext,
                        "url":         url,
                        "score":       50,   # RSS has no score; use placeholder so AI filter sees it
                        "sub":         sub,
                        "created":     created,
                        "is_video":    is_video,
                        "is_gallery":  is_gallery,
                        "media_url":   media_url,
                        "preview_url": preview_url,
                        "video_thumb": preview_url if is_video else "",
                        "linked_url":  linked_url,
                    })
            except Exception as exc:
                log_warn(f"reddit_osint: r/{sub}: {exc}")
            # Respect Reddit rate limit
            await asyncio.sleep(1.5)

    if blocked_subs:
        log_warn(f"reddit_osint: {blocked_subs}/{len(_SUBREDDITS)} subs blocked/unreachable — Railway IP may be banned by Reddit")
    if not raw:
        log_warn(f"reddit_osint: 0 posts passed keyword pre-filter (blocked_subs={blocked_subs})")
        return []
    log(f"reddit_osint: {len(raw)} posts passed keyword filter from {len(_SUBREDDITS)-blocked_subs} accessible subs")

    # AI classify in batches of 12 (keeps prompt concise)
    BATCH = 12
    ai_map: dict[str, dict] = {}
    for i in range(0, len(raw), BATCH):
        batch = raw[i : i + BATCH]
        results = await _ai_classify(batch)
        for r in results:
            idx = r.get("i")
            if idx is not None and 0 <= idx < len(batch):
                ai_map[batch[idx]["url"]] = r

    events: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for post in raw:
        ai = ai_map.get(post["url"], {})
        if not ai.get("keep"):
            continue
        if ai.get("confidence") in ("low", None):
            continue

        coords = _resolve(ai.get("location"), ai.get("country"))
        if not coords:
            continue

        lat, lon, country_name = coords
        ev_type    = ai.get("type", "other")
        confidence = ai.get("confidence", "medium")

        _SEEN_URLS.add(post["url"])

        ts = (
            datetime.fromtimestamp(post["created"], tz=timezone.utc).isoformat()
            if post.get("created") else now
        )

        events.append({
            "source":      "reddit_osint",
            "title":       f"[r/{post['sub']}] {post['title'][:180]}",
            "description": post.get("selftext", ""),
            "lat":         lat,
            "lon":         lon,
            "country":     country_name,
            "category":    "osint_crowd",
            "raw_ts_utc":  ts,
            "url":         post["url"],
            "extra":       json.dumps({
                "event_type":  ev_type,
                "confidence":  confidence,
                "subreddit":   post["sub"],
                "score":       post.get("score", 0),
                "ai_location": ai.get("location"),
                "is_video":    post.get("is_video", False),
                "is_gallery":  post.get("is_gallery", False),
                "media_url":   post.get("media_url", ""),
                "preview_url": post.get("preview_url", ""),
                "video_thumb": post.get("video_thumb", ""),
                "linked_url":  post.get("linked_url", ""),
            }),
        })

    # Trim seen-URL cache
    if len(_SEEN_URLS) > _MAX_SEEN:
        _SEEN_URLS = set(list(_SEEN_URLS)[-1500:])

    log(f"reddit_osint: {len(events)} AI-verified events from {len(raw)} pre-filtered posts ({len(_SUBREDDITS)} subs)")
    return events
