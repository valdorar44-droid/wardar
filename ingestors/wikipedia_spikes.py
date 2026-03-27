"""Wikipedia Edit Spike Detector

Monitors the Wikimedia RecentChanges stream (free SSE feed) for edit spikes
on conflict-zone articles. Edit rates spike within minutes of major events —
often before news wires publish.

Method:
  - Maintain a rolling 5-minute edit count per watched article
  - When count > SPIKE_THRESHOLD, fire an event
  - Reset count after firing (6-hour cooldown per article)

This runs as a background task reading from the SSE stream.
Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import json, time, re
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from config import settings as C

# Wikimedia EventStreams SSE endpoint
_STREAM_URL = "https://stream.wikimedia.org/v2/stream/recentchange"

# Article title → (lat, lon, country) for geolocation of spike events
_CONFLICT_ARTICLES: dict[str, tuple[float, float, str]] = {
    # Ukraine/Russia
    "Russian invasion of Ukraine":          (49.0, 32.0, "Ukraine"),
    "2022 Russian invasion of Ukraine":     (49.0, 32.0, "Ukraine"),
    "War in Donbass":                       (48.0, 37.5, "Ukraine"),
    "Zaporizhzhia Nuclear Power Plant":     (47.5, 35.1, "Ukraine"),
    "Bakhmut":                              (48.6, 38.0, "Ukraine"),
    "Kharkiv":                              (49.9, 36.2, "Ukraine"),
    "Odessa":                               (46.5, 30.7, "Ukraine"),
    "Kyiv":                                 (50.4, 30.5, "Ukraine"),
    "Kursk Oblast":                         (51.7, 36.2, "Russia"),
    "Belgorod":                             (50.6, 36.6, "Russia"),
    # Israel/Palestine/Lebanon
    "2023 Israel–Hamas war":               (31.5, 34.5, "Gaza"),
    "Gaza Strip":                           (31.4, 34.4, "Palestine"),
    "2024 Lebanon conflict":               (33.9, 35.5, "Lebanon"),
    "Hezbollah":                            (33.9, 35.5, "Lebanon"),
    "West Bank":                            (31.9, 35.2, "Palestine"),
    "Iron Dome":                            (31.8, 35.0, "Israel"),
    "Rafah":                                (31.3, 34.2, "Gaza"),
    "2024 Iran–Israel conflict":           (32.0, 35.0, "Israel"),
    "2025 Iran–Israel conflict":           (32.0, 35.0, "Israel"),
    "2026 Iran–Israel conflict":           (32.0, 35.0, "Israel"),
    # Yemen/Red Sea
    "Houthi movement":                      (15.5, 44.2, "Yemen"),
    "Red Sea attacks (2023–present)":       (15.0, 42.0, "Yemen"),
    "Yemen civil war (2014–present)":       (15.5, 48.5, "Yemen"),
    # Syria/Iraq/Iran
    "Syrian civil war":                     (34.8, 38.9, "Syria"),
    "Islamic State":                        (36.2, 43.4, "Iraq"),
    "Iran nuclear program":                 (35.7, 51.4, "Iran"),
    "2024 Iran nuclear crisis":            (35.7, 51.4, "Iran"),
    # Taiwan/China
    "2024 Taiwan Strait crisis":           (24.0, 120.0, "Taiwan"),
    "Cross-Strait relations":              (24.0, 120.0, "Taiwan"),
    "People's Liberation Army":            (39.9, 116.4, "China"),
    # Other hotspots
    "Sudan conflict (2023–present)":       (15.5, 32.5, "Sudan"),
    "Myanmar civil war":                   (19.7, 96.1, "Myanmar"),
    "Tigray War":                          (14.5, 38.0, "Ethiopia"),
    "Kashmir conflict":                    (34.0, 74.8, "India"),
    "2024 Nagorno-Karabakh war":           (39.8, 46.7, "Azerbaijan"),
    # Nuclear / infrastructure
    "nuclear terrorism":                   (0.0, 0.0, "Global"),
    "Nord Stream pipeline":               (55.5, 15.0, "Baltic Sea"),
    "Zaporizhzhia nuclear power plant":    (47.5, 35.1, "Ukraine"),
}

# Rolling edit counts: article → list of Unix timestamps
_edit_times: dict[str, list[float]] = defaultdict(list)
# Last fire time per article
_last_fired: dict[str, float] = {}

_WINDOW_SEC       = 300   # 5-minute rolling window
_SPIKE_THRESHOLD  = 5     # edits in window to trigger alert
_COOLDOWN_SEC     = 21600  # 6h cooldown after firing
_STREAM_TIMEOUT   = 55     # seconds to read stream per tick

# Cache of raw recent edits collected by the background reader
_pending_events: list[dict] = []


async def _read_stream_chunk():
    """Read the Wikimedia SSE stream for up to _STREAM_TIMEOUT seconds, collecting edits."""
    global _pending_events
    watched = set(_CONFLICT_ARTICLES.keys())
    collected: list[tuple[str, float]] = []  # (article_title, timestamp)

    try:
        async with httpx.AsyncClient(timeout=_STREAM_TIMEOUT + 5) as client:
            async with client.stream("GET", _STREAM_URL,
                                     params={"topics": "recentchange"},
                                     headers={"Accept": "text/event-stream"}) as resp:
                deadline = time.monotonic() + _STREAM_TIMEOUT
                buf = ""
                async for chunk in resp.aiter_text():
                    if time.monotonic() > deadline:
                        break
                    buf += chunk
                    while "\n\n" in buf:
                        event_str, buf = buf.split("\n\n", 1)
                        for line in event_str.splitlines():
                            if line.startswith("data:"):
                                try:
                                    data = json.loads(line[5:].strip())
                                    title  = data.get("title", "")
                                    ns     = data.get("namespace", -1)
                                    bot    = data.get("bot", False)
                                    if ns == 0 and not bot and title in watched:
                                        collected.append((title, time.time()))
                                except Exception:
                                    pass
    except Exception:
        pass

    # Add to rolling window
    for title, ts in collected:
        _edit_times[title].append(ts)


def _check_spikes() -> list[dict]:
    """Check rolling windows for spikes; return event dicts for any that exceed threshold."""
    now = time.time()
    cutoff = now - _WINDOW_SEC
    events = []

    for title, coords in _CONFLICT_ARTICLES.items():
        lat, lon, country = coords
        # Purge old timestamps
        _edit_times[title] = [t for t in _edit_times[title] if t > cutoff]
        count = len(_edit_times[title])

        if count < _SPIKE_THRESHOLD:
            continue

        # Check cooldown
        last = _last_fired.get(title, 0)
        if now - last < _COOLDOWN_SEC:
            continue

        _last_fired[title] = now
        _edit_times[title].clear()  # reset window

        wiki_url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        events.append({
            "source":      "wikipedia",
            "title":       f"📖 Wikipedia spike: {title} ({count} edits/5min)",
            "description": (
                f"Wikipedia article '{title}' received {count} edits in the last 5 minutes — "
                f"this often precedes breaking news. Possible active event in {country}."
            ),
            "lat":         lat if lat != 0 else None,
            "lon":         lon if lon != 0 else None,
            "country":     country,
            "category":    "intel",
            "raw_ts_utc":  datetime.now(timezone.utc).isoformat(),
            "url":         wiki_url,
            "extra":       json.dumps({"edit_count": count, "window_sec": _WINDOW_SEC}),
        })

    return events


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_WIKIPEDIA_SPIKES:
        return []
    try:
        await _read_stream_chunk()
        spikes = _check_spikes()
        if spikes:
            log(f"wikipedia: {len(spikes)} edit spike(s) detected")
        return spikes
    except Exception as exc:
        log_warn(f"wikipedia_spikes: {exc}")
        return []
