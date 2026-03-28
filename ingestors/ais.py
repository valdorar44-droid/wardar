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

# ── PRIORITY FILTER ────────────────────────────────────────────────────────────
# Only ingest vessels with strategic/military significance.
# Drops ~95% of AIS traffic (fishing, cargo, pleasure craft, ferries).
# Tankers = critical infrastructure. Military MMSI = warships. Gov = coast guard.
_PRIORITY_TYPES = frozenset([
    *range(70, 90),   # cargo (70-79) + tankers (80-89): oil, chemical, LNG, LPG, bulk
    *range(40, 60),   # high-speed craft (40-49) + special (50-59): patrol, SAR, law enforcement
    35,               # diving ops / submarines surfaced
    0,                # unknown type — include, don't silently drop
])

# Ship types that indicate actual military/government vessels
_MIL_SHIP_TYPES = frozenset([50, 51, 52, 53, 55, 35])  # law enforcement, SAR, diving

def _is_priority(ship_type: int | None, mmsi: str) -> tuple[bool, int]:
    """Returns (is_priority, military_flag).
    Military flag is set ONLY based on ship_type, not MMSI.
    Over-broad MMSI-based detection incorrectly delayed civilian tankers 24h.
    Real warships rarely broadcast AIS; rely on type codes instead.
    """
    st = ship_type if ship_type is not None else 0
    if st in _PRIORITY_TYPES:
        is_mil = 1 if st in _MIL_SHIP_TYPES else 0
        return True, is_mil
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

        # ── Priority filter: drop civilian bulk traffic ─────────────────────
        priority, mil_flag = _is_priority(ship_type, mmsi)
        if not priority:
            return None   # drop fishing, cargo, passenger, pleasure craft

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

def start_ws_listener(loop: asyncio.AbstractEventLoop):
    """Start the AIS WebSocket listener coroutine (called once by engine)."""
    global _running
    if not _running and C.ENABLE_AIS:
        _running = True
        asyncio.ensure_future(_ws_loop(), loop=loop)

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
