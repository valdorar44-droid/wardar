"""Bluesky Jetstream OSINT ingestor

Taps the Bluesky Jetstream WebSocket firehose, keyword-filters for
conflict/military posts, and AI-classifies them into geolocated events.

Bluesky is now the primary OSINT analyst community after X/Twitter
access restrictions. No API key required — Jetstream is public.

Listens for READ_WINDOW_SEC seconds per fetch() call, collects up to
MAX_COLLECT keyword-matched posts, then AI-classifies in batches.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone

import httpx  # noqa: F401 (imported for consistency with other ingestors)

from config import settings as C

# ── Shared keyword / location data ────────────────────────────────────────────
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
    "hormuz","brent","oil price","wti","tanker","cargo ship",
]

_LOC: dict[str, tuple[float, float, str]] = {
    "iran": (32.0, 53.0, "Iran"),
    "tehran": (35.69, 51.39, "Iran"),
    "isfahan": (32.66, 51.68, "Iran"),
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
    "iraq": (33.0, 44.0, "Iraq"),
    "baghdad": (33.34, 44.40, "Iraq"),
    "yemen": (15.5, 48.5, "Yemen"),
    "sanaa": (15.37, 44.19, "Yemen"),
    "hodeidah": (14.80, 42.95, "Yemen"),
    "ukraine": (49.0, 32.0, "Ukraine"),
    "kyiv": (50.45, 30.52, "Ukraine"),
    "kherson": (46.64, 32.62, "Ukraine"),
    "kharkiv": (49.99, 36.23, "Ukraine"),
    "zaporizhzhia": (47.85, 35.12, "Ukraine"),
    "donetsk": (47.99, 37.80, "Ukraine"),
    "russia": (61.5, 90.0, "Russia"),
    "moscow": (55.75, 37.62, "Russia"),
    "belgorod": (50.60, 36.59, "Russia"),
    "kursk": (51.73, 36.19, "Russia"),
    "crimea": (45.0, 34.0, "Ukraine"),
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
    "north korea": (40.0, 127.0, "North Korea"),
    "taiwan": (23.7, 121.0, "Taiwan"),
    "pakistan": (30.0, 70.0, "Pakistan"),
    "drc": (-4.0, 22.0, "DRC"),
    "congo": (-4.0, 22.0, "DRC"),
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
    "CD": (-4.0, 22.0, "DRC"),
    "TW": (23.7, 121.0, "Taiwan"),
}

# Jetstream endpoints — try in order
_JETSTREAM_URLS = [
    "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post",
    "wss://jetstream1.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post",
]

_READ_WINDOW_SEC = 25    # seconds to listen per fetch() call
_MAX_COLLECT     = 150   # cap keyword-matched posts before AI classify

_SEEN_URIS: set[str] = set()
_SEEN_FPS:  set[str] = set()
_MAX_SEEN   = 3000


def _title_fp(text: str) -> str:
    """Short fingerprint for near-duplicate suppression."""
    import re as _re
    t = _re.sub(r'[^a-z0-9 ]', '', text.lower())
    t = _re.sub(r'\s+', ' ', t).strip()
    stops = {'the','a','an','is','was','in','on','at','to','for','of','and','or',
             'with','by','from','as','that','this','are','has','have','been','will','after','it'}
    words = [w for w in t.split() if w not in stops and len(w) > 2]
    return ' '.join(words[:7])


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


async def _ai_classify(posts: list[dict]) -> list[dict]:
    """Send batch of Bluesky posts to Claude for conflict classification."""
    if not C.ANTHROPIC_API_KEY or not posts:
        return []
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=C.ANTHROPIC_API_KEY)
        lines = [f"{i}: {p['title'][:500]}" for i, p in enumerate(posts)]
        prompt = (
            "You are a military OSINT analyst. Classify these Bluesky posts.\n"
            "For each return ONE JSON object per line:\n"
            '{"i":N,"keep":true/false,"type":"missile_strike|airstrike|explosion|aircraft_down|naval|troop_movement|equipment_loss|protest|drone_attack|journalist_killed|sanctions|nuclear|other","location":"city or region","country":"2-letter ISO","confidence":"high|medium|low","severity":1,"brief":"1 sentence if keep=true else null"}\n\n'
            "RULES:\n"
            "- keep=true ONLY for real, current military/conflict events (not opinion, rumor, historical, game)\n"
            "- Iran/Middle East/Houthi/IRGC/Hamas/Hezbollah: high priority\n"
            "- OSINT analyst commentary and verified sightings: include\n"
            "- Speculation, hot takes, news retweets without new info: keep=false\n"
            "- location = most specific real place name\n"
            "- confidence=low → keep=false\n"
            "Return ONLY JSON lines, no other text.\n\n"
            "Posts:\n" + "\n".join(lines)
        )
        msg = await client.messages.create(
            model=C.AI_MODEL,
            max_tokens=1600,
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
            log_warn(f"bluesky_osint: AI error: {exc}")
        except Exception:
            pass
        return []


async def fetch() -> list[dict]:
    """Listen to Bluesky Jetstream for READ_WINDOW_SEC, return AI-classified conflict events."""
    global _SEEN_URIS, _SEEN_FPS

    if not C.ENABLE_BLUESKY_OSINT:
        return []

    from core.engine import log, log_warn
    import websockets

    raw: list[dict] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + _READ_WINDOW_SEC

    for ws_url in _JETSTREAM_URLS:
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=10,
                open_timeout=10,
            ) as ws:
                while loop.time() < deadline and len(raw) < _MAX_COLLECT:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        evt = json.loads(msg)
                    except Exception:
                        continue

                    # Only process post creates
                    if evt.get("kind") != "commit":
                        continue
                    commit = evt.get("commit", {})
                    if commit.get("operation") != "create":
                        continue
                    if commit.get("collection") != "app.bsky.feed.post":
                        continue

                    record = commit.get("record", {})
                    text = (record.get("text") or "").strip()
                    if not text or len(text) < 20:
                        continue

                    tl = text.lower()
                    if not any(kw in tl for kw in _KEYWORDS):
                        continue

                    uri = f"{evt.get('did', '')}/{commit.get('rkey', '')}"
                    if uri in _SEEN_URIS:
                        continue

                    fp = _title_fp(text)
                    if fp and fp in _SEEN_FPS:
                        continue
                    _SEEN_FPS.add(fp)

                    created_at = record.get("createdAt") or ""
                    ts = 0.0
                    if created_at:
                        try:
                            ts = datetime.fromisoformat(
                                created_at.replace("Z", "+00:00")
                            ).timestamp()
                        except Exception:
                            pass

                    did = evt.get("did", "")
                    rkey = commit.get("rkey", "")
                    raw.append({
                        "title":   text[:300],
                        "url":     f"https://bsky.app/profile/{did}/post/{rkey}",
                        "uri":     uri,
                        "created": ts,
                    })
            break  # connected and read successfully
        except Exception as exc:
            log_warn(f"bluesky_osint: WS error: {exc}")
            continue

    if not raw:
        return []

    log(f"bluesky_osint: {len(raw)} keyword-matched posts in {_READ_WINDOW_SEC}s window")

    # AI classify in batches of 20
    BATCH = 20
    ai_map: dict[str, dict] = {}
    for i in range(0, len(raw), BATCH):
        batch = raw[i: i + BATCH]
        results = await _ai_classify(batch)
        for r in results:
            idx = r.get("i")
            if idx is not None and 0 <= idx < len(batch):
                ai_map[batch[idx]["uri"]] = r

    events: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for post in raw:
        ai = ai_map.get(post["uri"], {})
        if not ai.get("keep"):
            continue
        if ai.get("confidence") in ("low", None):
            continue

        coords = _resolve(ai.get("location"), ai.get("country"))
        if not coords:
            continue

        lat, lon, country_name = coords
        _SEEN_URIS.add(post["uri"])

        ts = (
            datetime.fromtimestamp(post["created"], tz=timezone.utc).isoformat()
            if post.get("created") else now
        )

        events.append({
            "source":      "bluesky_osint",
            "title":       post["title"][:250],
            "description": ai.get("brief") or "",
            "lat":         lat,
            "lon":         lon,
            "country":     country_name,
            "category":    "osint_crowd",
            "raw_ts_utc":  ts,
            "url":         post["url"],
            "extra":       json.dumps({
                "event_type":  ai.get("type", "other"),
                "confidence":  ai.get("confidence", "medium"),
                "ai_location": ai.get("location"),
                "brief":       ai.get("brief") or "",
                "severity":    ai.get("severity", 3),
            }),
        })

    if len(_SEEN_URIS) > _MAX_SEEN:
        _SEEN_URIS = set(list(_SEEN_URIS)[-1500:])
    _SEEN_FPS.clear()

    log(f"bluesky_osint: {len(events)} AI-verified events from {len(raw)} posts")
    return events
