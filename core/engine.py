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
    # Invalidate snapshot cache so next new client gets fresh data
    try:
        from api.server import invalidate_snapshot
        invalidate_snapshot()
    except Exception:
        pass

# ── Ingestor helpers ──────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

# Phase 5: throttle history writes — dict mapping (source, callsign) → last write monotonic time
_last_hist_write: dict[tuple, float] = {}

def _save_positions(positions: list[dict]) -> int:
    saved = 0
    now_mono = time.monotonic()
    for p in positions:
        try:
            raw_ts = p.get("raw_ts_utc") or _now_utc()
            mil    = int(p.get("military_flag") or 0)
            src    = p.get("source", "")
            callsign = p.get("callsign", "")
            record = {
                "source":       src,
                "callsign":     callsign,
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
            # Phase 5: throttled history write — at most once per MIN_HIST_INTERVAL_SEC per entity
            key = (src, callsign)
            if callsign and (now_mono - _last_hist_write.get(key, 0)) >= C.MIN_HIST_INTERVAL_SEC:
                lat = p.get("lat")
                lon = p.get("lon")
                if lat is not None and lon is not None:
                    try:
                        DB.insert_position_history({
                            "source":       src,
                            "callsign":     callsign,
                            "lat":          lat,
                            "lon":          lon,
                            "altitude_ft":  p.get("altitude_ft"),
                            "speed_kts":    p.get("speed_kts"),
                            "heading_deg":  p.get("heading_deg"),
                            "military_flag": mil,
                            "raw_ts_utc":   raw_ts,
                        })
                        _last_hist_write[key] = now_mono
                    except Exception:
                        pass  # non-fatal — history is best-effort
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
        if positions:
            await _broadcast({"type": "positions", "sources": ["adsb", "opensky", "adsb_emergency"], "data": positions})
    except Exception as exc:
        log_err(f"tick_adsb: {exc}")

async def _tick_ais():
    try:
        from ingestors import ais
        positions = await ais.fetch()
        n = _save_positions(positions)
        if positions:
            await _broadcast({"type": "positions", "sources": ["ais"], "data": positions})
    except Exception as exc:
        log_err(f"tick_ais: {exc}")

async def _tick_tle():
    try:
        from ingestors import tle
        positions = await tle.fetch()
        n = _save_positions(positions)
        if positions:
            await _broadcast({"type": "positions", "sources": ["tle"], "data": positions})
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

_ALL_OSINT_SOURCES = [
    "gdelt", "osint_news", "twz", "usni", "bellingcat", "oryx",
    "defense_news", "defense_one", "ukmod", "rusi", "gcaptain", "krebs",
    "isw", "aljazeera", "mem", "toi", "ukrinform", "kyiv_ind",
    "reliefweb", "centcom", "reuters", "bbc",
    # Iran / Middle East focus
    "iran_intl", "mehr_news", "al_monitor", "tasnim", "jpost", "haaretz", "arab_news",
    "pentagon", "africom", "navy", "un_peace", "crisisgroup", "state_dept",
]

async def _tick_osint():
    try:
        from ingestors import osint
        events = await osint.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=_ALL_OSINT_SOURCES, limit=200)
            await _broadcast({"type": "events", "sources": _ALL_OSINT_SOURCES, "data": released})
    except Exception as exc:
        log_err(f"tick_osint: {exc}")

async def _tick_firms():
    try:
        from ingestors import firms
        events = await firms.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["firms"], limit=500)
            await _broadcast({"type": "events", "sources": ["firms"], "data": released})
    except Exception as exc:
        log_err(f"tick_firms: {exc}")

async def _tick_usgs():
    try:
        from ingestors import usgs
        events = await usgs.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["usgs"], limit=500)
            await _broadcast({"type": "events", "sources": ["usgs"], "data": released})
    except Exception as exc:
        log_err(f"tick_usgs: {exc}")

async def _tick_gpsjam():
    try:
        from ingestors import gpsjam
        events = await gpsjam.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["gpsjam"], limit=300)
            await _broadcast({"type": "events", "sources": ["gpsjam"], "data": released})
    except Exception as exc:
        log_err(f"tick_gpsjam: {exc}")

async def _tick_purge():
    try:
        p = DB.purge_old_positions()
        e = DB.purge_old_events()
        h = DB.purge_old_history()
        if p or e or h:
            log(f"purge: removed {p} positions, {e} events, {h} history rows")
    except Exception as exc:
        log_err(f"tick_purge: {exc}")

async def _tick_route_deviation():
    if not C.ENABLE_ROUTE_DEV or not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_route_deviation_check
        n = run_route_deviation_check()
        if n:
            released = DB.get_released_events(sources=["route_dev"], limit=50)
            await _broadcast({"type": "events", "sources": ["route_dev"], "data": released})
    except Exception as exc:
        log_err(f"tick_route_deviation: {exc}")

async def _tick_dark_vessel():
    if not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_dark_vessel_check
        n = run_dark_vessel_check()
        if n:
            released = DB.get_released_events(sources=["dark_vessel"], limit=50)
            await _broadcast({"type": "events", "sources": ["dark_vessel"], "data": released})
    except Exception as exc:
        log_err(f"tick_dark_vessel: {exc}")

async def _tick_convergence():
    if not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_convergence_check
        n = run_convergence_check()
        if n:
            released = DB.get_released_events(sources=["convergence"], limit=50)
            await _broadcast({"type": "events", "sources": ["convergence"], "data": released})
    except Exception as exc:
        log_err(f"tick_convergence: {exc}")

async def _tick_proximity_alerts():
    if not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_nuclear_proximity_check, run_pipeline_proximity_check
        n1 = await run_nuclear_proximity_check()
        n2 = await run_pipeline_proximity_check()
        if n1 + n2 > 0:
            released = DB.get_released_events(
                sources=["nuclear_threat", "pipeline_threat"], limit=50
            )
            await _broadcast({"type": "events",
                              "sources": ["nuclear_threat", "pipeline_threat"],
                              "data": released})
    except Exception as exc:
        log_err(f"tick_proximity_alerts: {exc}")

async def _tick_vessel_spoofing():
    if not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_vessel_spoofing_check
        n = run_vessel_spoofing_check()
        if n:
            released = DB.get_released_events(sources=["vessel_spoof"], limit=50)
            await _broadcast({"type": "events", "sources": ["vessel_spoof"], "data": released})
    except Exception as exc:
        log_err(f"tick_vessel_spoofing: {exc}")

async def _tick_transponder_loss():
    if not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_transponder_loss_check
        n = run_transponder_loss_check()
        if n:
            released = DB.get_released_events(sources=["transponder_loss"], limit=50)
            await _broadcast({"type": "events", "sources": ["transponder_loss"], "data": released})
    except Exception as exc:
        log_err(f"tick_transponder_loss: {exc}")

async def _tick_firms_usgs():
    if not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_firms_usgs_correlation
        n = run_firms_usgs_correlation()
        if n:
            released = DB.get_released_events(sources=["firms_usgs"], limit=50)
            await _broadcast({"type": "events", "sources": ["firms_usgs"], "data": released})
    except Exception as exc:
        log_err(f"tick_firms_usgs: {exc}")

async def _tick_gpsjam_dark():
    if not C.ENABLE_ALERTS:
        return
    try:
        from core.alerts import run_gpsjam_dark_vessel
        n = run_gpsjam_dark_vessel()
        if n:
            released = DB.get_released_events(sources=["gpsjam_dark"], limit=50)
            await _broadcast({"type": "events", "sources": ["gpsjam_dark"], "data": released})
    except Exception as exc:
        log_err(f"tick_gpsjam_dark: {exc}")

async def _tick_static_layers():
    try:
        from ingestors import static_layers
        await static_layers.refresh_all()
    except Exception as exc:
        log_err(f"tick_static_layers: {exc}")

async def _tick_osint_geo():
    try:
        from ingestors import osint_geo
        events = await osint_geo.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["osint_geo"], limit=300)
            await _broadcast({"type": "events", "sources": ["osint_geo"], "data": released})
    except Exception as exc:
        log_err(f"tick_osint_geo: {exc}")

async def _tick_unhcr():
    try:
        from ingestors import unhcr
        events = await unhcr.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["unhcr"], limit=100)
            await _broadcast({"type": "events", "sources": ["unhcr"], "data": released})
    except Exception as exc:
        log_err(f"tick_unhcr: {exc}")

async def _tick_views():
    try:
        from ingestors import views_forecast
        events = await views_forecast.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["views_forecast"], limit=100)
            await _broadcast({"type": "events", "sources": ["views_forecast"], "data": released})
    except Exception as exc:
        log_err(f"tick_views: {exc}")

async def _tick_pikud_haoref():
    try:
        from ingestors import pikud_haoref
        events = await pikud_haoref.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["pikud_haoref"], limit=200)
            await _broadcast({"type": "events", "sources": ["pikud_haoref"], "data": released})
    except Exception as exc:
        log_err(f"tick_pikud_haoref: {exc}")

async def _tick_wikipedia_spikes():
    try:
        from ingestors import wikipedia_spikes
        events = await wikipedia_spikes.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["wikipedia"], limit=50)
            await _broadcast({"type": "events", "sources": ["wikipedia"], "data": released})
    except Exception as exc:
        log_err(f"tick_wikipedia_spikes: {exc}")

async def _tick_polymarket():
    if not C.ENABLE_POLYMARKET:
        return
    try:
        from ingestors import polymarket
        events = await polymarket.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["polymarket"], limit=100)
            await _broadcast({"type": "events", "sources": ["polymarket"], "data": released})
    except Exception as exc:
        log_err(f"tick_polymarket: {exc}")

async def _tick_shodan():
    if not C.ENABLE_SHODAN or not C.SHODAN_API_KEY:
        return
    try:
        from ingestors import shodan_intel
        events = await shodan_intel.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["shodan"], limit=100)
            await _broadcast({"type": "events", "sources": ["shodan"], "data": released})
    except Exception as exc:
        log_err(f"tick_shodan: {exc}")

async def _tick_ioda():
    if not C.ENABLE_IODA:
        return
    try:
        from ingestors import ioda
        events = await ioda.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["ioda"], limit=50)
            await _broadcast({"type": "events", "sources": ["ioda"], "data": released})
    except Exception as exc:
        log_err(f"tick_ioda: {exc}")

async def _tick_mil_aircraft():
    if not C.ENABLE_MIL_AIRCRAFT:
        return
    try:
        from ingestors import mil_aircraft
        positions = await mil_aircraft.fetch()
        n = _save_positions(positions)
        if positions:
            await _broadcast({"type": "positions", "sources": ["airplaneslive"], "data": positions})
    except Exception as exc:
        log_err(f"tick_mil_aircraft: {exc}")

async def _tick_warspot():
    if not C.ENABLE_WARSPOT:
        return
    try:
        from ingestors import warspot
        events = await warspot.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["warspot"], limit=200)
            await _broadcast({"type": "events", "sources": ["warspot"], "data": released})
    except Exception as exc:
        log_err(f"tick_warspot: {exc}")

async def _tick_safecast():
    if not C.ENABLE_SAFECAST:
        return
    try:
        from ingestors import safecast
        events = await safecast.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["safecast"], limit=500)
            await _broadcast({"type": "events", "sources": ["safecast"], "data": released})
    except Exception as exc:
        log_err(f"tick_safecast: {exc}")

async def _tick_ofac():
    if not C.ENABLE_OFAC:
        return
    try:
        from ingestors import ofac_sanctions
        await ofac_sanctions.refresh()
    except Exception as exc:
        log_err(f"tick_ofac: {exc}")

async def _tick_ucdp():
    if not C.ENABLE_UCDP or not C.UCDP_TOKEN:
        return
    try:
        from ingestors import ucdp
        events = await ucdp.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["ucdp"], limit=300)
            await _broadcast({"type": "events", "sources": ["ucdp"], "data": released})
    except Exception as exc:
        log_err(f"tick_ucdp: {exc}")

async def _tick_reddit_osint():
    if not C.ENABLE_REDDIT_OSINT or not C.ANTHROPIC_API_KEY:
        return
    try:
        from ingestors import reddit_osint
        events = await reddit_osint.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["reddit_osint"], limit=200)
            await _broadcast({"type": "events", "sources": ["reddit_osint"], "data": released})
    except Exception as exc:
        log_err(f"tick_reddit_osint: {exc}")

async def _tick_telegram_osint():
    if not C.ENABLE_TELEGRAM_OSINT or not C.ANTHROPIC_API_KEY:
        return
    try:
        from ingestors import telegram_osint
        events = await telegram_osint.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["telegram_osint"], limit=200)
            await _broadcast({"type": "events", "sources": ["telegram_osint"], "data": released})
    except Exception as exc:
        log_err(f"tick_telegram_osint: {exc}")

async def _tick_breaking_news():
    if not C.ENABLE_BREAKING_NEWS or not C.ANTHROPIC_API_KEY:
        return
    try:
        from ingestors import breaking_news
        events = await breaking_news.fetch()
        n = _save_events(events)
        if n:
            released = DB.get_released_events(sources=["breaking_news"], limit=100)
            await _broadcast({"type": "events", "sources": ["breaking_news"], "data": released})
    except Exception as exc:
        log_err(f"tick_breaking_news: {exc}")


async def _tick_intel_brief():
    if not C.ENABLE_INTEL_BRIEF or not C.ANTHROPIC_API_KEY:
        return
    try:
        from core.intel_brief import generate_global_brief
        result = await generate_global_brief()
        if result.get("text"):
            log(f"intel_brief: generated ({len(result['text'])} chars, model={result.get('model','')})")
            await _broadcast({"type": "intel_brief", "data": result})
        elif result.get("error"):
            log_warn(f"intel_brief: {result['error']}")
    except Exception as exc:
        log_err(f"tick_intel_brief: {exc}")


async def _tick_ww3_meter():
    """Run once per UTC calendar day. Checks if today's reading exists; if not, generates."""
    if not C.ENABLE_WW3_METER or not C.ANTHROPIC_API_KEY:
        return
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        last  = DB.get_ww3_meter()
        if last and last.get("generated_at", "")[:10] == today:
            return  # already done today
        from core.ww3_meter import generate_ww3_score
        result = await generate_ww3_score()
        if result.get("score") is not None:
            log(f"ww3_meter: score={result['score']} level={result['level']}")
            await _broadcast({"type": "ww3_meter", "data": result})
        elif result.get("error"):
            log_warn(f"ww3_meter: {result['error']}")
    except Exception as exc:
        log_err(f"tick_ww3_meter: {exc}")


async def _tick_webhook_flush():
    if not C.ALERT_WEBHOOK_URL:
        return
    try:
        from core import webhooks
        n = await webhooks.flush()
        if n:
            log(f"webhooks: dispatched {n} alerts to {C.ALERT_WEBHOOK_URL[:40]}...")
    except Exception as exc:
        log_err(f"tick_webhook_flush: {exc}")

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
        asyncio.create_task(_run_every(_tick_firms,  C.FIRMS_INTERVAL_SEC,     "firms")),
        asyncio.create_task(_run_every(_tick_usgs,   C.USGS_INTERVAL_SEC,      "usgs")),
        asyncio.create_task(_run_every(_tick_gpsjam, C.GPSJAM_INTERVAL_SEC,    "gpsjam")),
        asyncio.create_task(_run_every(_tick_purge,  300,                      "purge")),
        asyncio.create_task(_run_every(_tick_static_layers,    C.STATIC_REFRESH_SEC,      "static_layers")),
        asyncio.create_task(_run_every(_tick_osint_geo,       C.OSINT_GEO_INTERVAL_SEC,  "osint_geo")),
        asyncio.create_task(_run_every(_tick_unhcr,           C.UNHCR_INTERVAL_SEC,      "unhcr")),
        asyncio.create_task(_run_every(_tick_views,           C.VIEWS_INTERVAL_SEC,      "views")),
        asyncio.create_task(_run_every(_tick_dark_vessel,     C.DARK_VESSEL_INTERVAL_SEC,"dark_vessel")),
        asyncio.create_task(_run_every(_tick_convergence,     C.CONVERGENCE_INTERVAL_SEC,"convergence")),
        asyncio.create_task(_run_every(_tick_proximity_alerts,  C.PROXIMITY_INTERVAL_SEC,       "proximity")),
        asyncio.create_task(_run_every(_tick_vessel_spoofing,  C.VESSEL_SPOOF_INTERVAL_SEC,    "vessel_spoof")),
        asyncio.create_task(_run_every(_tick_transponder_loss, C.TRANSPONDER_LOSS_INTERVAL_SEC,"transponder_loss")),
        asyncio.create_task(_run_every(_tick_firms_usgs,       C.FIRMS_USGS_INTERVAL_SEC,      "firms_usgs")),
        asyncio.create_task(_run_every(_tick_gpsjam_dark,      C.GPSJAM_DARK_INTERVAL_SEC,     "gpsjam_dark")),
        asyncio.create_task(_run_every(_tick_pikud_haoref,   C.PIKUD_HAOREF_INTERVAL_SEC,   "pikud_haoref")),
        asyncio.create_task(_run_every(_tick_wikipedia_spikes,C.WIKIPEDIA_SPIKE_INTERVAL_SEC,"wikipedia_spikes")),
        asyncio.create_task(_run_every(_tick_polymarket,   C.POLYMARKET_INTERVAL_SEC,  "polymarket")),
        asyncio.create_task(_run_every(_tick_shodan,       C.SHODAN_INTERVAL_SEC,      "shodan")),
        asyncio.create_task(_run_every(_tick_ioda,          C.IODA_INTERVAL_SEC,        "ioda")),
        asyncio.create_task(_run_every(_tick_intel_brief,  C.INTEL_BRIEF_INTERVAL_SEC, "intel_brief")),

        asyncio.create_task(_run_every(_tick_warspot,      C.WARSPOT_INTERVAL_SEC,     "warspot")),
        asyncio.create_task(_run_every(_tick_safecast,     C.SAFECAST_INTERVAL_SEC,    "safecast")),
        asyncio.create_task(_run_every(_tick_ofac,         C.OFAC_INTERVAL_SEC,        "ofac")),
        asyncio.create_task(_run_every(_tick_ucdp,          C.UCDP_INTERVAL_SEC,         "ucdp")),
        asyncio.create_task(_run_every(_tick_reddit_osint,    C.REDDIT_OSINT_INTERVAL_SEC,    "reddit_osint")),
        asyncio.create_task(_run_every(_tick_telegram_osint,  C.TELEGRAM_OSINT_INTERVAL_SEC,  "telegram_osint")),
        asyncio.create_task(_run_every(_tick_breaking_news,   C.BREAKING_NEWS_INTERVAL_SEC,   "breaking_news")),
        asyncio.create_task(_run_every(_tick_route_deviation, C.ROUTE_DEV_INTERVAL_SEC,       "route_deviation")),
        asyncio.create_task(_run_every(_tick_webhook_flush,  C.WEBHOOK_MIN_INTERVAL_SEC,      "webhook_flush")),
        asyncio.create_task(_run_every(_tick_ww3_meter,      C.WW3_METER_CHECK_SEC,           "ww3_meter")),
    ]
    log(f"engine: {len(tasks)} ingestor tasks scheduled")
    return tasks
