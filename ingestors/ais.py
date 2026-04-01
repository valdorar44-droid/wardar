"""AIS ingestor — aisstream.io WebSocket

Maintains a persistent WebSocket connection. Positions accumulate in memory
and are drained by the engine on each tick. Never raises — reconnects on error.
"""
from __future__ import annotations
import asyncio, json
from collections import deque
from typing import Any

from config import settings as C

# Thread-safe position buffer — engine drains this (tight cap — only priority vessels)
_buffer: deque[dict] = deque(maxlen=2_000)
_running = False

# Ship type codes → human label
_SHIP_TYPES = {
    range(20, 30): "wing_in_ground",
    range(30, 40): "fishing",
    range(40, 50): "high_speed",
    range(50, 60): "government",
    range(60, 70): "passenger",
    range(70, 80): "cargo",
    range(80, 90): "tanker",
    range(90, 100): "other",
}

# ── HIGH-VALUE FILTER ─────────────────────────────────────────────────────────
# Only ingest vessels with strategic intelligence value:
#   1. Military / government / coast guard  → tracked worldwide, flagged
#   2. Oil/chemical/LNG tankers             → tracked only in conflict zones
# Everything else (cargo, fishing, passenger, pleasure) is dropped.

_MIL_SHIP_TYPES = frozenset([
    35,               # military operations
    *range(50, 60),   # pilot, SAR, tug, coast guard, law enforcement
])

_TANKER_TYPES = frozenset(range(80, 90))  # oil, chemical, LNG, LPG

_CONFLICT_ZONES: list[tuple[float, float, float, float]] = [
    ( 22,  28,  48,  60),   # Persian Gulf / Strait of Hormuz
    (  8,  22,  38,  58),   # Red Sea / Gulf of Aden / Bab-el-Mandeb
    ( 40,  48,  27,  42),   # Black Sea
    ( 30,  38,  24,  40),   # Eastern Mediterranean
    (  3,  26, 103, 125),   # South China Sea / Taiwan Strait
    ( -6,  11,  -6,  16),   # Gulf of Guinea
    ( 55,  70,  13,  32),   # Baltic Sea
    ( 50,  62,  -6,  12),   # North Sea / English Channel
]

def _in_conflict_zone(lat: float, lon: float) -> bool:
    for min_lat, max_lat, min_lon, max_lon in _CONFLICT_ZONES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return True
    return False

def _is_accepted(ship_type: int | None, lat: float, lon: float) -> tuple[bool, int]:
    """Returns (accepted, military_flag)."""
    st = ship_type if ship_type is not None else -1
    if st in _MIL_SHIP_TYPES:
        return True, 1
    if st in _TANKER_TYPES:
        return _in_conflict_zone(lat, lon), 0
    return False, 0

def _ship_label(type_code: int | None) -> str:
    if type_code is None:
        return "ship"
    for rng, label in _SHIP_TYPES.items():
        if type_code in rng:
            return label
    return "ship"

def _norm_ais(msg: dict) -> dict | None:
    """Normalize aisstream.io message to Wardar position schema."""
    try:
        meta = msg.get("MetaData", {})
        pos  = msg.get("Message", {}).get("PositionReport", {}) or \
               msg.get("Message", {}).get("StandardClassBPositionReport", {}) or {}
        if not pos:
            return None
        lat  = pos.get("Latitude")
        lon  = pos.get("Longitude")
        if lat is None or lon is None:
            return None
        # Skip obviously parked/default positions
        if abs(lat) < 0.001 and abs(lon) < 0.001:
            return None
        mmsi     = str(meta.get("MMSI") or pos.get("UserID") or "")
        name     = str(meta.get("ShipName") or "").strip()
        callsign = name or mmsi or "UNKNOWN"
        country  = str(meta.get("ShipCountry") or "")
        spd      = pos.get("Sog")   # speed over ground (knots)
        hdg      = pos.get("Cog") or pos.get("TrueHeading")
        ship_type = meta.get("ShipType")

        accepted, mil_flag = _is_accepted(ship_type, float(lat), float(lon))
        if not accepted:
            return None   # drop cargo, fishing, passenger, pleasure craft

        return {
            "source":       "ais",
            "callsign":     callsign,
            "type":         _ship_label(ship_type),
            "lat":          float(lat),
            "lon":          float(lon),
            "altitude_ft":  None,
            "speed_kts":    float(spd) if spd is not None else None,
            "heading_deg":  float(hdg) if hdg is not None else None,
            "country":      country,
            "military_flag": mil_flag,
            "extra":        json.dumps({"mmsi": mmsi, "ship_type": ship_type}),
        }
    except Exception:
        return None

async def _ws_loop():
    """WebSocket listener loop with auto-reconnect."""
    import websockets  # type: ignore
    from core.engine import log, log_warn

    while True:
        if not C.AISSTREAM_API_KEY:
            await asyncio.sleep(60)
            continue
        try:
            uri = "wss://stream.aisstream.io/v0/stream"
            async with websockets.connect(uri) as ws:
                subscribe = {
                    "APIKey":     C.AISSTREAM_API_KEY,
                    "BoundingBoxes": [[[-90, -180], [90, 180]]],  # global
                    "FiltersShipMMSI": [],
                    "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"],
                }
                await ws.send(json.dumps(subscribe))
                log("ais: WebSocket connected")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        p = _norm_ais(msg)
                        if p:
                            _buffer.append(p)
                    except Exception:
                        pass
        except Exception as exc:
            log_warn(f"ais: WebSocket error: {exc} — reconnecting in {C.AIS_RECONNECT_SEC}s")
            await asyncio.sleep(C.AIS_RECONNECT_SEC)

def start_ws_listener():
    """Start the AIS WebSocket listener coroutine (called once by engine)."""
    global _running
    if not _running and C.ENABLE_AIS:
        asyncio.create_task(_ws_loop())
        _running = True

async def fetch() -> list[dict]:
    """Drain buffered positions accumulated since last tick."""
    from core.engine import log
    results = []
    while _buffer:
        try:
            results.append(_buffer.popleft())
        except IndexError:
            break
    log(f"ais: {len(results)} positions (buffered)")
    return results
