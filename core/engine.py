"""Wardar — Core Engine

Orchestrates all ingestors, applies the delay policy, writes to DB,
and broadcasts released data to connected WebSocket clients.

DELAY POLICY IS NON-NEGOTIABLE.
Do not add bypass flags. Do not add admin exceptions.
See: config/settings.py for constants.
     WARDAR_CLAUDE_RULES.md for policy statement.
"""
from __future__ import annotations
import asyncio, json, time, traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from config import settings as C
from db import store as DB

# ── Logging ──────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def log(msg: str)      -> None: print(f"[{_ts()}] {msg}", flush=True)
def log_warn(msg: str) -> None: print(f"[{_ts()}] WARN  {msg}", flush=True)
def log_err(msg: str)  -> None: print(f"[{_ts()}] ERROR {msg}", flush=True)

# ── Delay Policy ─────────────────────────────────────────────────────────────
# NEVER BYPASS — see WARDAR_CLAUDE_RULES.md

def apply_delay(raw_ts_utc: str, military_flag: int, source: str) -> str:
    """
    Compute release_ts_utc for a record based on its type.

    Rules (from config/settings.py — NON-NEGOTIABLE):
    - military_flag=1  → +DELAY_SENSITIVE_SEC (24h)
    - ACLED/conflict   → +DELAY_CONFLICT_SEC  (1h)
    - all others       → +DELAY_CIVILIAN_SEC  (30s)

    Returns ISO-8601 UTC string.
    """
    try:
        ts = datetime.fromisoformat(raw_ts_utc.replace("Z", "+00:00"))
    except Exception:
        ts = datetime.now(timezone.utc)

    if military_flag:
        delay_sec = C.DELAY_SENSITIVE_SEC
    elif source in ("acled", "gdelt", "osint_news"):
        delay_sec = C.DELAY_CONFLICT_SEC
    else:
        delay_sec = C.DELAY_CIVILIAN_SEC

    return (ts + timedelta(seconds=delay_sec)).isoformat()

# ── WebSocket broadcast ───────────────────────────────────────────────────────

# Set of active WebSocket send callbacks registered by api/server.py
_ws_clients: set[Callable] = set()

def register_ws_client(send_fn: Callable):
    _ws_clients.add(send_fn)

def unregister_ws_client(send_fn: Callable):
    _ws_clients.discard(send_fn)

async def _broadcast(msg: dict):
    if not _ws_clients:
        return
    payload = json.dumps(msg)
    dead = set()
    for fn in list(_ws_clients):
        try:
            await fn(payload)
        except Exception:
            dead.add(fn)
    for fn in dead:
        _ws_clients.discard(fn)

# ── Ingestor helpers ──────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _save_positions(positions: list[dict]) -> int:
    saved = 0
    for p in positions:
        try:
            raw_ts = p.get("raw_ts_utc") or _now_utc()
            mil    = int(p.get("military_flag") or 0)
            src    = p.get("source", "")
            record = {
                "source":       src,
                "callsign":     p.get("callsign", ""),
                "type":         p.get("type", ""),
                "lat":          p.get("lat"),
                "lon":          p.get("lon"),
                "altitude_ft":  p.get("altitude_ft"),
                "speed_kts":    p.get("speed_kts"),
                "heading_deg":  p.get("heading_deg"),
                "country":      p.get("country", ""),
                "military_flag": mil,
                "raw_ts_utc":   raw_ts,
                "release_ts_utc": apply_delay(raw_ts, mil, src),
                "extra":        p.get("extra") or "{}",
            }
            DB.upsert_position(record)
            saved += 1
        except Exception as exc:
            log_err(f"save_position: {exc}")
    return saved

def _save_events(events: list[dict]) -> int:
    saved = 0
    for e in events:
        try:
            raw_ts = e.get("raw_ts_utc") or _now_utc()
            src    = e.get("source", "")
            record = {
                "source":       src,
                "title":        e.get("title", "")[:200],
                "description":  e.get("description", "")[:1000],
                "lat":          e.get("lat"),
                "lon":          e.get("lon"),
                "country":      e.get("country", ""),
                "category":     e.get("category", ""),
                "raw_ts_utc":   raw_ts,
                "release_ts_utc": apply_delay(raw_ts, 0, src),
                "url":          e.get("url", ""),
                "extra":        e.get("extra") or "{}",
            }
            DB.upsert_event(record)
            saved += 1
        except Exception as exc:
            log_err(f"save_event: {exc}")
    return saved

# ── Ingestor tick functions ───────────────────────────────────────────────────

async def _tick_adsb():
    try:
        from ingestors import adsb
        positions = await adsb.fetch()
        n = _save_positions(positions)
        if n:
            released = DB.get_released_positions(sources=["adsb", "opensky"], limit=2000)
            await _broadcast({"type": "positions", "sources": ["adsb", "opensky"], "data": released})
    except Exception as exc:
        log_err(f"tick_adsb: {exc}")

async def _tick_ais():
    try:
        from ingestors import ais
        positions = await ais.fetch()
        n = _save_positions(positions)
        if n:
            released = DB.get_released_positions(sources=["ais"], limit=2000)
            await _broadcast({"type": "positions", "sources": ["ais"], "data": released})
    except Exception as exc:
        log_err(f"tick_ais: {exc}")

async def _tick_tle():
    try:
        from ingestors import tle
        positions = await tle.fetch()
        n = _save_positions(positions)
        if n:
            released = DB.get_released_positions(sources=["tle"], limit=1000)
            await _broadcast({"type": "positions", "sources": ["tle"], "data": released})
    except Exception as exc:
        log_err(f"tick_tle: {exc}")

async def _tick_notam():
    try:
        from ingestors import notam
        events = await notam.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["notam"], limit=500)
            await _broadcast({"type": "events", "sources": ["notam"], "data": released})
    except Exception as exc:
        log_err(f"tick_notam: {exc}")

async def _tick_acled():
    try:
        from ingestors import acled
        events = await acled.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["acled"], limit=500)
            await _broadcast({"type": "events", "sources": ["acled"], "data": released})
    except Exception as exc:
        log_err(f"tick_acled: {exc}")

async def _tick_osint():
    try:
        from ingestors import osint
        events = await osint.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["gdelt", "osint_news"], limit=200)
            await _broadcast({"type": "events", "sources": ["gdelt", "osint_news"], "data": released})
    except Exception as exc:
        log_err(f"tick_osint: {exc}")

async def _tick_purge():
    try:
        p = DB.purge_old_positions()
        e = DB.purge_old_events()
        if p or e:
            log(f"purge: removed {p} positions, {e} events")
    except Exception as exc:
        log_err(f"tick_purge: {exc}")

# ── Scheduler ─────────────────────────────────────────────────────────────────

async def _run_every(coro_fn: Callable, interval_sec: int, name: str):
    """Run a coroutine every interval_sec seconds, forever."""
    while True:
        t0 = time.monotonic()
        try:
            await coro_fn()
        except Exception as exc:
            log_err(f"{name}: unhandled: {exc}\n{traceback.format_exc()}")
        elapsed = time.monotonic() - t0
        wait    = max(0, interval_sec - elapsed)
        await asyncio.sleep(wait)

async def start():
    """Start all ingestor loops. Call once from api/server.py lifespan."""
    log("engine: starting")

    # Init DB
    DB.get_conn()
    log("engine: DB ready")

    # Start AIS WebSocket listener (event-driven, not polled)
    if C.ENABLE_AIS:
        from ingestors import ais as _ais_mod
        loop = asyncio.get_event_loop()
        _ais_mod.start_ws_listener(loop)
        log("engine: AIS WebSocket listener started")

    # Schedule polling ingestors
    tasks = [
        asyncio.create_task(_run_every(_tick_adsb,   C.ADSB_INTERVAL_SEC,      "adsb")),
        asyncio.create_task(_run_every(_tick_ais,    C.ADSB_INTERVAL_SEC,      "ais_drain")),
        asyncio.create_task(_run_every(_tick_tle,    C.SATELLITE_INTERVAL_SEC, "tle")),
        asyncio.create_task(_run_every(_tick_notam,  C.NOTAM_INTERVAL_SEC,     "notam")),
        asyncio.create_task(_run_every(_tick_acled,  C.ACLED_INTERVAL_SEC,     "acled")),
        asyncio.create_task(_run_every(_tick_osint,  C.GDELT_INTERVAL_SEC,     "osint")),
        asyncio.create_task(_run_every(_tick_purge,  3600,                     "purge")),
    ]
    log(f"engine: {len(tasks)} ingestor tasks scheduled")
    return tasks
