"""
Wardar — Ghost Tracker
======================
When military aircraft go dark (ADS-B / MLAT gap > GHOST_DARK_MIN minutes),
attempt to continue tracking via:

  1. OpenSky Network — independent receiver network, free anonymous access
  2. Dead-reckoning — last known heading + speed + computed turn-rate from
     position_history, iterated per-minute so banking/turning aircraft are
     extrapolated along their arc, not a straight line

Ghost positions are broadcast as type "ghost_positions" over WebSocket.
They are NEVER stored in the DB — they are ephemeral computed positions.

Algorithm:
  • Scan positions table for military aircraft with no update in >GHOST_DARK_MIN min
  • Pull last 20 history rows → compute avg turn_rate (deg/min) and speed
  • Detect orbit pattern (turn_rate > threshold over 180°+ of heading change)
  • For orbit: project the aircraft around the orbit circle
  • For transit: project straight/arcing path
  • Try OpenSky by ICAO24 hex — if found, use that as ground truth and reset DR
  • Emit ghost dict per dark aircraft
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings as C

# ── Constants ──────────────────────────────────────────────────────────────────
GHOST_DARK_MIN  = getattr(C, "GHOST_DARK_MIN",  5)     # minutes gap → "dark"
GHOST_MAX_MIN   = getattr(C, "GHOST_MAX_MIN",   240)   # give up after 4h
OPENSKY_URL     = "https://opensky-network.org/api/states/all"
_HEADERS        = {"User-Agent": "Wardar/0.1 (+https://wardar.app)"}
_OPENSKY_TTL    = 120   # seconds between OpenSky queries for same hex
_ORBIT_TURN_THRESHOLD = 0.8   # deg/min min to consider orbiting
_ORBIT_MIN_SPAN       = 120   # deg of heading span to detect orbit

# Rate-limit cache: hex → (query_monotonic, result)
_opensky_cache: dict[str, tuple[float, Optional[dict]]] = {}


# ── Math helpers ───────────────────────────────────────────────────────────────

def _dead_reckon_step(lat: float, lon: float, hdg_deg: float, speed_kts: float, minutes: float) -> tuple[float, float]:
    """Return (lat, lon) after flying heading/speed for `minutes` minutes."""
    if speed_kts < 1 or minutes <= 0:
        return lat, lon
    R = 6371.0
    dist_km = speed_kts * 1.852 * (minutes / 60.0)
    d   = dist_km / R
    brg = math.radians(hdg_deg)
    la1 = math.radians(lat)
    lo1 = math.radians(lon)
    la2 = math.asin(math.sin(la1)*math.cos(d) + math.cos(la1)*math.sin(d)*math.cos(brg))
    lo2 = lo1 + math.atan2(math.sin(brg)*math.sin(d)*math.cos(la1), math.cos(d) - math.sin(la1)*math.sin(la2))
    return math.degrees(la2), ((math.degrees(lo2) + 540) % 360) - 180


def _compute_turn_rate(history: list[dict]) -> float:
    """
    Compute average turn rate in deg/min from position_history rows.
    Positive = clockwise, negative = CCW. Uses last 8 points.
    """
    pts = [h for h in history if h.get("heading_deg") is not None][-8:]
    if len(pts) < 2:
        return 0.0
    total_turn = 0.0
    total_min  = 0.0
    for i in range(1, len(pts)):
        try:
            t0 = datetime.fromisoformat(pts[i-1]["raw_ts_utc"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(pts[i]["raw_ts_utc"].replace("Z", "+00:00"))
            dt_min = (t1 - t0).total_seconds() / 60.0
            if dt_min <= 0:
                continue
            dh = (float(pts[i]["heading_deg"]) - float(pts[i-1]["heading_deg"]) + 360) % 360
            if dh > 180:
                dh -= 360   # CCW is negative
            total_turn += dh
            total_min  += dt_min
        except Exception:
            continue
    return (total_turn / total_min) if total_min > 0 else 0.0


def _detect_orbit(history: list[dict]) -> bool:
    """Return True if the aircraft appears to be flying a circular orbit."""
    pts = [h for h in history if h.get("heading_deg") is not None][-20:]
    if len(pts) < 5:
        return False
    headings = [float(p["heading_deg"]) for p in pts]
    # Compute total unwrapped heading change
    total = 0.0
    for i in range(1, len(headings)):
        dh = (headings[i] - headings[i-1] + 360) % 360
        if dh > 180:
            dh -= 360
        total += dh
    return abs(total) >= _ORBIT_MIN_SPAN


def _project_ghost(last_lat: float, last_lon: float, last_hdg: float,
                   last_spd: float, turn_rate: float, elapsed_min: float,
                   is_orbit: bool) -> tuple[float, float, float]:
    """
    Project the ghost position forward.
    Returns (ghost_lat, ghost_lon, ghost_hdg).
    Uses per-minute iteration for accuracy with non-zero turn rates.
    """
    if elapsed_min <= 0:
        return last_lat, last_lon, last_hdg

    # Straight-line shortcut for low turn rates and short times
    if abs(turn_rate) < 0.1 or elapsed_min < 2:
        glat, glon = _dead_reckon_step(last_lat, last_lon, last_hdg, last_spd, elapsed_min)
        ghdg = (last_hdg + turn_rate * elapsed_min) % 360
        return glat, glon, ghdg

    # Iterative per-minute dead-reckoning with turn rate applied each step
    steps    = min(int(elapsed_min) + 1, 300)   # cap at 300 iterations
    step_min = elapsed_min / steps
    glat, glon, ghdg = last_lat, last_lon, last_hdg

    # If orbiting, clamp turn rate to actual orbit rate (no runaway)
    effective_tr = turn_rate
    if is_orbit and abs(turn_rate) > 4:
        effective_tr = math.copysign(4, turn_rate)   # max 4 deg/min = ~90s orbit

    for _ in range(steps):
        ghdg = (ghdg + effective_tr * step_min) % 360
        glat, glon = _dead_reckon_step(glat, glon, ghdg, last_spd, step_min)

    return glat, glon, ghdg


# ── OpenSky fallback ───────────────────────────────────────────────────────────

async def _try_opensky(hex_code: str) -> Optional[dict]:
    """
    Try to find the aircraft on OpenSky Network by ICAO24 hex.
    Anonymous access: 100 req/day, ~10 s/req limit.
    Returns a partial position dict or None.
    """
    if not hex_code:
        return None
    now = time.monotonic()
    cached = _opensky_cache.get(hex_code)
    if cached and (now - cached[0]) < _OPENSKY_TTL:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.get(OPENSKY_URL, params={"icao24": hex_code.lower()}, headers=_HEADERS)
        if r.status_code != 200:
            _opensky_cache[hex_code] = (now, None)
            return None
        data   = r.json()
        states = data.get("states") or []
        if not states:
            _opensky_cache[hex_code] = (now, None)
            return None
        # OpenSky state vector positions (index):
        # 0=icao24  1=callsign  2=origin_country  3=time_position  4=last_contact
        # 5=longitude  6=latitude  7=baro_altitude  8=on_ground  9=velocity(m/s)
        # 10=true_track  11=vertical_rate  12=sensors  13=geo_altitude  14=squawk
        s = states[0]
        if s[5] is None or s[6] is None:
            _opensky_cache[hex_code] = (now, None)
            return None
        pos = {
            "lat":         float(s[6]),
            "lon":         float(s[5]),
            "altitude_ft": round(float(s[13]) * 3.28084) if s[13] else (round(float(s[7]) * 3.28084) if s[7] else None),
            "speed_kts":   round(float(s[9]) * 1.944)   if s[9]  else None,
            "heading_deg": float(s[10])                  if s[10] is not None else None,
        }
        _opensky_cache[hex_code] = (now, pos)
        return pos
    except Exception:
        _opensky_cache[hex_code] = (now, None)
        return None


# ── Main tick ─────────────────────────────────────────────────────────────────

async def run_ghost_tick() -> list[dict]:
    """
    Detect dark military aircraft and compute ghost positions.
    Called every GHOST_TRACKER_INTERVAL_SEC seconds from engine.
    Returns a list of ghost position dicts ready for broadcast.
    """
    from core.engine import log, log_warn
    from db import store as DB

    now_utc = datetime.now(timezone.utc)
    conn    = DB.get_conn()

    rows = conn.execute(f"""
        SELECT p.callsign, p.lat, p.lon, p.heading_deg, p.speed_kts,
               p.altitude_ft, p.raw_ts_utc, p.extra, p.source, p.type
        FROM positions p
        WHERE p.military_flag = 1
          AND p.source IN ('airplaneslive', 'adsb', 'opensky')
          AND p.raw_ts_utc < datetime('now', '-{GHOST_DARK_MIN} minutes')
          AND p.raw_ts_utc > datetime('now', '-{GHOST_MAX_MIN} minutes')
          AND p.lat IS NOT NULL AND p.lon IS NOT NULL
          AND p.heading_deg IS NOT NULL
          AND p.speed_kts IS NOT NULL AND p.speed_kts > 10
        LIMIT 150
    """).fetchall()

    if not rows:
        return []

    ghosts = []
    for r in rows:
        try:
            callsign   = (r["callsign"] or "").strip()
            last_lat   = float(r["lat"])
            last_lon   = float(r["lon"])
            last_hdg   = float(r["heading_deg"])
            last_spd   = float(r["speed_kts"])
            alt_ft     = r["altitude_ft"]
            raw_ts_str = r["raw_ts_utc"]
            orig_src   = r["source"] or "airplaneslive"

            # Parse last-seen time
            try:
                last_seen = datetime.fromisoformat(raw_ts_str.replace("Z", "+00:00"))
            except Exception:
                continue

            elapsed_min = (now_utc - last_seen).total_seconds() / 60.0
            if elapsed_min < GHOST_DARK_MIN:
                continue

            # Pull position history for turn-rate + orbit detection
            history_rows = conn.execute("""
                SELECT heading_deg, speed_kts, altitude_ft, raw_ts_utc
                FROM position_history
                WHERE callsign = ?
                  AND raw_ts_utc > datetime('now', '-3 hours')
                ORDER BY raw_ts_utc ASC
                LIMIT 25
            """, (callsign,)).fetchall()
            history = [dict(h) for h in history_rows]

            turn_rate = _compute_turn_rate(history)
            is_orbit  = _detect_orbit(history)

            # Compute avg speed from history (more reliable than single point)
            spd_vals = [float(h["speed_kts"]) for h in history if h.get("speed_kts") and float(h["speed_kts"]) > 10]
            avg_spd  = (sum(spd_vals) / len(spd_vals)) if spd_vals else last_spd

            # Altitude trend (ft/min) — last 3 points
            alt_trend = 0.0
            alt_pts = [(h["altitude_ft"], h["raw_ts_utc"]) for h in history if h.get("altitude_ft")][-4:]
            if len(alt_pts) >= 2:
                try:
                    t0 = datetime.fromisoformat(alt_pts[0][1].replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(alt_pts[-1][1].replace("Z", "+00:00"))
                    dt_min = (t1 - t0).total_seconds() / 60.0
                    if dt_min > 0:
                        alt_trend = (float(alt_pts[-1][0]) - float(alt_pts[0][0])) / dt_min
                except Exception:
                    pass

            # Project ghost position using dead-reckoning + turn rate
            ghost_lat, ghost_lon, ghost_hdg = _project_ghost(
                last_lat, last_lon, last_hdg, avg_spd, turn_rate, elapsed_min, is_orbit
            )

            # Project altitude forward
            ghost_alt = alt_ft
            if alt_ft is not None and alt_trend != 0:
                ghost_alt = max(0, float(alt_ft) + alt_trend * elapsed_min)

            # Try alternative signal sources
            signal_sources  = [f"{orig_src}✗"]
            opensky_found   = False
            hex_code        = ""
            try:
                extra    = json.loads(r["extra"] or "{}")
                hex_code = extra.get("hex", "")
            except Exception:
                pass

            if hex_code:
                osky = await _try_opensky(hex_code)
                if osky:
                    opensky_found = True
                    signal_sources.append("OpenSky✓")
                    # Use OpenSky as ground truth — reset to its position
                    ghost_lat = osky["lat"]
                    ghost_lon = osky["lon"]
                    if osky.get("heading_deg") is not None:
                        ghost_hdg = osky["heading_deg"]
                    if osky.get("speed_kts") is not None:
                        avg_spd = osky["speed_kts"]
                    if osky.get("altitude_ft") is not None:
                        ghost_alt = osky["altitude_ft"]
                    # Small dead-reckon from OpenSky fix time to now (minimal)
                    elapsed_min = 0.5   # assume ~30s old OpenSky fix
                else:
                    signal_sources.append("OpenSky✗")
            else:
                signal_sources.append("OpenSky-")   # no hex, can't query

            # Confidence: decays with time; boosted if OpenSky confirmed
            if opensky_found:
                confidence = 0.95
            else:
                confidence = max(0.05, 1.0 - (elapsed_min / GHOST_MAX_MIN))

            # Behavior tag
            behavior = "ORBIT" if is_orbit else ("TURNING" if abs(turn_rate) > 0.5 else "TRANSIT")
            dark_min = round(elapsed_min)

            ghost = {
                "source":           "ghost_mil",
                "callsign":         callsign,
                "type":             r["type"] or "military",
                "lat":              round(ghost_lat, 5),
                "lon":              round(ghost_lon, 5),
                "altitude_ft":      round(ghost_alt) if ghost_alt is not None else None,
                "speed_kts":        round(avg_spd, 1),
                "heading_deg":      round(ghost_hdg, 1),
                "military_flag":    1,
                "ghost":            True,
                "dark_min":         dark_min,
                "turn_rate":        round(turn_rate, 2),
                "alt_trend":        round(alt_trend, 1),
                "is_orbit":         is_orbit,
                "behavior":         behavior,
                "confidence":       round(confidence, 3),
                "signal_sources":   signal_sources,
                "opensky_found":    opensky_found,
                "last_known_lat":   last_lat,
                "last_known_lon":   last_lon,
                "last_known_hdg":   last_hdg,
                "extra":            json.dumps({
                    "hex":            hex_code,
                    "dark_min":       dark_min,
                    "turn_rate":      round(turn_rate, 2),
                    "is_orbit":       is_orbit,
                    "behavior":       behavior,
                    "confidence_pct": round(confidence * 100),
                    "signal_sources": signal_sources,
                    "alt_trend_fpm":  round(alt_trend, 1),
                }),
            }
            ghosts.append(ghost)

        except Exception as exc:
            log_warn(f"ghost_tracker: {r.get('callsign','?')}: {exc}")
            continue

    if ghosts:
        log(f"ghost_tracker: {len(ghosts)} dark mil aircraft — {sum(1 for g in ghosts if g['opensky_found'])} recovered via OpenSky")
    return ghosts
