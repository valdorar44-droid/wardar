"""
Telegram OSINT ingestor — AI-filtered conflict intelligence from Telegram channels.

Uses RSSHub (rsshub.app) to read public Telegram channels without auth.
Uses Claude AI to filter noise and extract event/location data.
Replaces Reddit OSINT (blocked from Railway datacenter IPs).

Focuses on: Iran, Middle East, Gaza, Ukraine, Russia, Sudan, global conflicts.
"""
from __future__ import annotations
import asyncio
import html as _html
import json
import re
import xml.etree.ElementTree as _ET
from datetime import datetime, timezone

import httpx

from config import settings as C

# ── Telegram OSINT channels — trimmed to highest signal/noise ratio ────────────
# Removed: UkraineNow, GazaAlaan, MiddleEastSpectator, Flash_news_ua, AirAlerts_ua, warnewsua
# Reason: heavy overlap with remaining channels; fingerprint dedup was suppressing most of their
# unique posts anyway. Keeping the 7 highest-signal, lowest-overlap channels.
_CHANNELS = [
    ("osintdefender",    "OSINT Defender — global incidents & strikes"),
    ("intelslava",       "Intel Slava Z — Ukraine frontline reports"),
    ("wartranslated",    "War Translated — Russian mil blog translations"),
    ("Conflictnews",     "Conflict News — global incidents"),
    ("intelcrab",        "Intel Crab — global intelligence"),
    ("YemenWatch",       "Yemen Watch — Houthi/coalition activity"),
    ("SahelIntelligence","Sahel Intelligence — Mali/Niger/Burkina"),
]

# Pre-filter keywords (same as Reddit ingestor)
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

# Location lookup (lat, lon, country_name) — shared with reddit_osint
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
_SEEN_FPS:  set[str] = set()  # title fingerprints — cross-channel duplicate suppression
_MAX_SEEN = 3000
_ATOM = "http://www.w3.org/2005/Atom"


def _title_fp(title: str) -> str:
    """
    Normalize title to a short fingerprint.
    Catches near-identical posts where multiple channels copy-paste the same report.
    """
    import re as _re
    t = _re.sub(r'[^a-z0-9 ]', '', title.lower())
    t = _re.sub(r'\s+', ' ', t).strip()
    stops = {'the','a','an','is','was','in','on','at','to','for','of','and','or',
             'with','by','from','as','that','this','are','has','have','been','will','after','it'}
    words = [w for w in t.split() if w not in stops and len(w) > 2]
    return ' '.join(words[:7])

# ── RSSHub instances (try in order if one fails) ─────────────────────────────
_RSSHUB_HOSTS = [
    "https://rsshub.app",
    "https://rsshub.rssforever.com",
]


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
                txt += " | " + p["selftext"][:300]
            lines.append(f"{i}: {txt[:500]}")

        prompt = (
            "You are a military OSINT analyst. Classify these Telegram channel posts.\n"
            "For each return ONE JSON object per line:\n"
            '{"i":N,"keep":true/false,"type":"missile_strike|airstrike|explosion|aircraft_down|naval|troop_movement|equipment_loss|protest|drone_attack|journalist_killed|sanctions|nuclear|other","location":"city or region","country":"2-letter ISO","confidence":"high|medium|low","severity":1,"brief":"1 sentence if keep=true else null"}\n\n'
            "RULES:\n"
            "- keep=true ONLY for real, current military/conflict events (not opinion, rumor, historical, game)\n"
            "- Iran/Middle East/Houthi/IRGC/Hamas/Hezbollah: high priority\n"
            "- location = most specific real place name\n"
            "- confidence=low → keep=false\n"
            "Return ONLY JSON lines, no other text.\n\n"
            "Posts:\n" + "\n".join(lines)
        )
        msg = await client.messages.create(
            model=C.AI_MODEL,
            max_tokens=1600,  # 20 items × ~80 tokens/JSON line
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
            log_warn(f"telegram_osint: AI error: {exc}")
        except Exception:
            pass
        return []


def _resolve(location: str | None, country_iso: str | None) -> tuple[float, float, str] | None:
    if location:
        loc = location.lower().strip()
        if loc in _LOC:
            return _LOC[loc]
        for k, v in _LOC.items():
            if k in loc or loc in k:
                return v
    if country_iso:
        return _ISO_FALLBACK.get(country_iso.upper())
    return None


async def fetch() -> list[dict]:
    """Fetch Telegram OSINT posts via RSSHub, AI-filter, geocode, return event dicts."""
    global _SEEN_URLS, _SEEN_FPS

    if not C.ENABLE_TELEGRAM_OSINT:
        return []

    _SEEN_FPS.clear()  # fresh fingerprint set each run — URLs are the persistent dedup

    from core.engine import log, log_warn

    raw: list[dict] = []
    blocked = 0

    async with httpx.AsyncClient(
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; Wardar/1.0 OSINT aggregator)",
            "Accept": "application/rss+xml, application/atom+xml, text/xml, */*",
        },
        follow_redirects=True,
    ) as client:
        for channel, label in _CHANNELS:
            fetched = False
            for host in _RSSHUB_HOSTS:
                try:
                    r = await client.get(f"{host}/telegram/channel/{channel}", timeout=15)
                    if r.status_code == 429:
                        log_warn(f"telegram_osint: {channel} rate-limited at {host}")
                        await asyncio.sleep(10)
                        continue
                    if r.status_code == 403 or r.status_code == 404:
                        continue  # try next host
                    if r.status_code != 200:
                        log_warn(f"telegram_osint: {channel} HTTP {r.status_code} at {host}")
                        continue

                    try:
                        root = _ET.fromstring(r.content)
                    except Exception:
                        continue

                    # Atom or RSS 2.0
                    entries = root.findall(f"{{{_ATOM}}}entry") or root.findall(".//item")
                    if not entries:
                        continue

                    for entry in entries:
                        t_el = entry.find(f"{{{_ATOM}}}title") or entry.find("title")
                        title = (t_el.text or "").strip() if t_el is not None else ""
                        if not title or title == channel:
                            # Some feeds have channel name as title — use content
                            c_el = entry.find(f"{{{_ATOM}}}content") or entry.find("description")
                            raw_c = (c_el.text or "") if c_el is not None else ""
                            plain = re.sub(r"<[^>]+>", " ", _html.unescape(raw_c)).strip()
                            plain = re.sub(r"\s+", " ", plain)
                            title = plain[:200] if plain else title

                        if not title:
                            continue

                        l_el = entry.find(f"{{{_ATOM}}}link") or entry.find("link")
                        url = (l_el.get("href") if l_el is not None else None) or (l_el.text if l_el is not None else "") or ""
                        if not url:
                            continue
                        if url in _SEEN_URLS:
                            continue

                        title_lower = title.lower()
                        if not any(kw in title_lower for kw in _KEYWORDS):
                            continue

                        # Cross-channel fingerprint dedup — skip if near-identical to a post
                        # already seen this run (multiple channels copy-pasting same report)
                        fp = _title_fp(title)
                        if fp and fp in _SEEN_FPS:
                            continue
                        _SEEN_FPS.add(fp)

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

                        c_el = entry.find(f"{{{_ATOM}}}content") or entry.find("description")
                        content_raw = (c_el.text or "") if c_el is not None else ""
                        content_html = _html.unescape(content_raw)

                        selftext = re.sub(r"<[^>]+>", " ", content_html).strip()
                        selftext = re.sub(r"\s+", " ", selftext)[:500]

                        preview_url = ""
                        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_html, re.I)
                        if img_m:
                            preview_url = img_m.group(1)
                            if preview_url.startswith("//"):
                                preview_url = "https:" + preview_url

                        linked_url = ""
                        href_m = re.search(r'href=["\']([^"\']+)["\']', content_html, re.I)
                        if href_m:
                            linked_url = href_m.group(1)

                        is_video   = bool(re.search(r'\.(mp4|webm|m3u8)', linked_url or url, re.I))
                        media_url  = ""
                        if not is_video and linked_url:
                            if any(linked_url.lower().endswith(ext) for ext in (".jpg",".jpeg",".png",".gif",".webp")):
                                media_url = linked_url

                        raw.append({
                            "title":       title[:300],
                            "selftext":    selftext,
                            "url":         url,
                            "channel":     channel,
                            "channel_label": label,
                            "created":     created,
                            "is_video":    is_video,
                            "media_url":   media_url,
                            "preview_url": preview_url,
                            "linked_url":  linked_url,
                        })
                    fetched = True
                    break  # success — don't try next host
                except Exception as exc:
                    log_warn(f"telegram_osint: {channel}@{host}: {exc}")
                    continue

            if not fetched:
                blocked += 1
            await asyncio.sleep(1.2)

    if blocked:
        log_warn(f"telegram_osint: {blocked}/{len(_CHANNELS)} channels failed")
    if not raw:
        log_warn(f"telegram_osint: 0 posts passed keyword filter (blocked={blocked})")
        return []
    log(f"telegram_osint: {len(raw)} posts from {len(_CHANNELS)-blocked} channels")

    # AI classify — batch 20 to minimise API call count
    BATCH = 20
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
            "source":      "telegram_osint",
            "title":       f"[{post['channel']}] {post['title'][:250]}",
            "description": post.get("selftext", ""),
            "lat":         lat,
            "lon":         lon,
            "country":     country_name,
            "category":    "osint_crowd",
            "raw_ts_utc":  ts,
            "url":         post["url"],
            "extra":       json.dumps({
                "event_type":     ev_type,
                "confidence":     confidence,
                "channel":        post["channel"],
                "channel_label":  post.get("channel_label", ""),
                "brief":         ai.get("brief") or "",
                "severity":      ai.get("severity", 3),
                "ai_location":    ai.get("location"),
                "is_video":       post.get("is_video", False),
                "media_url":      post.get("media_url", ""),
                "preview_url":    post.get("preview_url", ""),
                "linked_url":     post.get("linked_url", ""),
            }),
        })

    if len(_SEEN_URLS) > _MAX_SEEN:
        _SEEN_URLS = set(list(_SEEN_URLS)[-1500:])
    if len(_SEEN_FPS) > 2000:
        _SEEN_FPS.clear()  # reset fingerprints each cycle (URLs remain the real dedup)

    log(f"telegram_osint: {len(events)} AI-verified events from {len(raw)} pre-filtered posts")
    return events
