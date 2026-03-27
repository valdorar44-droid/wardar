"""
Wardar — Phase 3 Alert Engine
Four proprietary detection algorithms that no competitor has:

1. dark_vessel    — AIS ships going dark (>2h no signal) near conflict zones
2. convergence    — Multi-domain activity spike in the same 3° grid cell
3. nuclear_threat — FIRMS thermal anomaly within 10km of a nuclear reactor
4. pipeline_threat— ACLED conflict event within 5km of an oil/gas pipeline

All alerts are stored as events (source=alert type) and deduplicated
against a 6-hour window to prevent alert fatigue.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings as C
from db import store as DB

# ── Haversine distance ────────────────────────────────────────────────────────

def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in km between two (lat, lon) points."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(min(1.0, a)))


def _pt_to_segment_km(px: float, py: float,
                       ax: float, ay: float,
                       bx: float, by: float) -> float:
    """Approx distance in km from point (px,py) to line segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return _hav(py, px, ay, ax)
    t = max(0.0, min(1.0, ((px - ax)*dx + (py - ay)*dy) / (dx*dx + dy*dy)))
    return _hav(py, px, ay + t*dy, ax + t*dx)


# ── Dedup helper ──────────────────────────────────────────────────────────────

def _already_fired(source: str, key: str, hours: int = 6) -> bool:
    """Return True if an alert with this source+key was stored in the last N hours."""
    conn = DB.get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM events WHERE source=? AND title LIKE ? AND raw_ts_utc > ? LIMIT 1",
        (source, key + "%", cutoff),
    ).fetchone()
    return row is not None


def _save_alert(source: str, title: str, desc: str,
                lat: float, lon: float, country: str = "",
                extra: dict | None = None) -> bool:
    """
    Persist an alert as an event with minimal delay (civilian 30s).
    Returns True if saved, False if suppressed by dedup.
    """
    from core.engine import _now_utc, apply_delay, log
    key = title[:60]
    if _already_fired(source, key):
        return False
    now = _now_utc()
    record = {
        "source":        source,
        "title":         title[:200],
        "description":   desc[:1000],
        "lat":           round(lat, 4),
        "lon":           round(lon, 4),
        "country":       country,
        "category":      "alert",
        "raw_ts_utc":    now,
        "release_ts_utc": apply_delay(now, 0, source),
        "url":           "",
        "extra":         json.dumps(extra or {}),
    }
    DB.upsert_event(record)
    log(f"ALERT [{source}] {title[:80]}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# 1. DARK VESSEL DETECTION
# ══════════════════════════════════════════════════════════════════════════════

# Conflict-zone bounding boxes (W, S, E, N)
_CONFLICT_ZONES: list[dict] = [
    {"name": "Red Sea / Bab-el-Mandeb",  "bbox": (40,  10,  55,  22)},
    {"name": "Persian Gulf / Hormuz",    "bbox": (48,  22,  60,  30)},
    {"name": "Black Sea",                "bbox": (27,  40,  42,  47)},
    {"name": "Horn of Africa",           "bbox": (38,  -5,  55,  15)},
    {"name": "South China Sea",          "bbox": (105,  0, 125,  25)},
    {"name": "Taiwan Strait",            "bbox": (119, 21, 123,  27)},
    {"name": "Eastern Mediterranean",   "bbox": (25,  30,  40,  38)},
    {"name": "Ukraine Black Sea Coast",  "bbox": (28,  43,  38,  47)},
    {"name": "Gulf of Guinea",           "bbox": (-5,  -3,  10,  10)},
    {"name": "Strait of Malacca",        "bbox": (99,   0, 110,   7)},
]

# Min gap hours before flagging a vessel as "dark"
_DARK_HOURS_MIN = 2
_DARK_HOURS_MAX = 48   # older than this = stale, don't alert


def run_dark_vessel_check() -> int:
    """
    Find AIS vessels that went dark near conflict zones.
    Returns number of new alerts fired.
    """
    if not C.ENABLE_AIS:
        return 0

    conn = DB.get_conn()
    now  = datetime.now(timezone.utc)
    # Latest fix per AIS vessel
    rows = conn.execute("""
        SELECT callsign, lat, lon, country, MAX(raw_ts_utc) AS last_seen
        FROM positions
        WHERE source = 'ais'
          AND raw_ts_utc > datetime('now', '-48 hours')
        GROUP BY callsign
        HAVING last_seen < datetime('now', '-2 hours')
           AND callsign != ''
           AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY last_seen DESC
        LIMIT 200
    """).fetchall()

    fired = 0
    for r in rows:
        lat = r["lat"]
        lon = r["lon"]
        # Check if last position was inside a conflict zone
        zone_hit = None
        for z in _CONFLICT_ZONES:
            w, s, e, n = z["bbox"]
            if w <= lon <= e and s <= lat <= n:
                zone_hit = z["name"]
                break
        if not zone_hit:
            continue

        callsign = r["callsign"]
        last_seen = r["last_seen"]
        try:
            ls_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            hours_dark = (now - ls_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except Exception:
            continue

        title = f"DARK VESSEL: {callsign} — {zone_hit}"
        desc  = (
            f"AIS signal lost {hours_dark:.1f}h ago at "
            f"({lat:.3f}, {lon:.3f}). Last seen: {last_seen[:16]} UTC. "
            f"Possible AIS transponder disabled — potential spoofing or evasion."
        )
        saved = _save_alert(
            "dark_vessel", title, desc, lat, lon,
            country=r["country"] or zone_hit,
            extra={"callsign": callsign, "hours_dark": round(hours_dark, 1),
                   "zone": zone_hit, "last_lat": lat, "last_lon": lon},
        )
        if saved:
            fired += 1

    return fired


# ══════════════════════════════════════════════════════════════════════════════
# 2. CROSS-DOMAIN CONVERGENCE ALERT ENGINE
# ══════════════════════════════════════════════════════════════════════════════

# Grid cell size in degrees (≈330 km at equator)
_GRID = 3.0

# Domain weights: (table, source_filter, min_count, weight)
_DOMAINS = [
    ("events",    ["acled"],                          1,  3),   # conflict events   — highest weight
    ("events",    ["gpsjam"],                         1,  2),   # GPS jamming        — high
    ("events",    ["osint_geo"],                      1,  2),   # verified OSINT    — high
    ("events",    ["firms"],                          5,  2),   # fire/thermal       — medium
    ("events",    ["gdelt", "osint_news", "twz",
                   "usni", "bellingcat"],              3,  1),   # OSINT news         — low
    ("positions", ["adsb", "opensky"],               10,  1),   # aviation density   — low
    ("positions", ["ais"],                            5,  1),   # maritime density   — low
]

_CONVERGENCE_THRESHOLD = 4   # score >= 4 → ELEVATED
_CRITICAL_THRESHOLD    = 7   # score >= 7 → CRITICAL


def run_convergence_check() -> int:
    """
    Find 3°×3° grid cells where multiple data domains spike simultaneously.
    Fires convergence alert if score >= threshold.
    Returns number of new alerts fired.
    """
    conn   = DB.get_conn()
    fired  = 0

    # First pass: find all active grid cells in the last 6h
    # (must have at least 1 ACLED event to filter out peaceful regions)
    active_cells = set()
    acled_rows = conn.execute("""
        SELECT CAST(lat / ? AS INTEGER) AS bx,
               CAST(lon / ? AS INTEGER) AS by
        FROM events
        WHERE source = 'acled'
          AND lat IS NOT NULL AND lon IS NOT NULL
          AND raw_ts_utc > datetime('now', '-6 hours')
        GROUP BY bx, by
    """, (_GRID, _GRID)).fetchall()

    for r in acled_rows:
        active_cells.add((r["bx"], r["by"]))

    if not active_cells:
        return 0

    for (bx, by) in active_cells:
        w = bx * _GRID
        e = w + _GRID
        s = by * _GRID
        n = s + _GRID
        cell_lat = by * _GRID + _GRID / 2
        cell_lon = bx * _GRID + _GRID / 2

        score    = 0
        domains_active = []

        for (table, sources, min_cnt, weight) in _DOMAINS:
            ph = ",".join("?" * len(sources))
            if table == "events":
                row = conn.execute(f"""
                    SELECT COUNT(*) AS cnt FROM events
                    WHERE source IN ({ph})
                      AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
                      AND raw_ts_utc > datetime('now', '-6 hours')
                """, sources + [s, n, w, e]).fetchone()
            else:
                row = conn.execute(f"""
                    SELECT COUNT(DISTINCT callsign) AS cnt FROM positions
                    WHERE source IN ({ph})
                      AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
                      AND raw_ts_utc > datetime('now', '-6 hours')
                """, sources + [s, n, w, e]).fetchone()

            cnt = (row["cnt"] or 0) if row else 0
            if cnt >= min_cnt:
                score += weight
                domains_active.append(f"{sources[0]}({cnt})")

        if score < _CONVERGENCE_THRESHOLD:
            continue

        level = "CRITICAL" if score >= _CRITICAL_THRESHOLD else "ELEVATED"
        title = f"⚡ CONVERGENCE ALERT [{level}] — {cell_lat:.1f}°, {cell_lon:.1f}°"
        desc  = (
            f"Score {score} across {len(domains_active)} domains: "
            f"{', '.join(domains_active)}. "
            f"Cell: ({s:.0f}°–{n:.0f}°N, {w:.0f}°–{e:.0f}°E)"
        )

        # Try to get country from nearest ACLED event
        country_row = conn.execute("""
            SELECT country FROM events
            WHERE source='acled' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
            AND raw_ts_utc > datetime('now', '-6 hours')
            ORDER BY raw_ts_utc DESC LIMIT 1
        """, (s, n, w, e)).fetchone()
        country = (country_row["country"] or "") if country_row else ""

        saved = _save_alert(
            "convergence", title, desc, cell_lat, cell_lon,
            country=country,
            extra={"score": score, "level": level,
                   "domains": domains_active, "cell": f"{s},{n},{w},{e}"},
        )
        if saved:
            fired += 1

    return fired


# ══════════════════════════════════════════════════════════════════════════════
# 3. FIRMS THERMAL WITHIN 10km OF NUCLEAR FACILITY
# ══════════════════════════════════════════════════════════════════════════════

_NUCLEAR_ALERT_KM = 10.0

async def run_nuclear_proximity_check() -> int:
    """
    Cross-reference latest FIRMS hotspots with nuclear plant locations.
    Fires alert if a high-FRP thermal detection is within _NUCLEAR_ALERT_KM km.
    Returns number of new alerts fired.
    """
    if not (C.ENABLE_FIRMS and C.ENABLE_NUCLEAR):
        return 0

    try:
        from ingestors.static_layers import get_layer
        nuclear_geo = await get_layer("nuclear")
    except Exception:
        return 0

    reactors = []
    for f in (nuclear_geo.get("features") or []):
        if f.get("geometry", {}).get("type") != "Point":
            continue
        coords = f["geometry"]["coordinates"]
        props  = f.get("properties") or {}
        reactors.append({
            "lon":     coords[0],
            "lat":     coords[1],
            "name":    props.get("name", "Nuclear Plant"),
            "country": props.get("country", ""),
        })

    if not reactors:
        return 0

    conn = DB.get_conn()
    # Latest high-confidence FIRMS hits in last 24h
    firms_rows = conn.execute("""
        SELECT lat, lon, title, extra
        FROM events
        WHERE source = 'firms'
          AND raw_ts_utc > datetime('now', '-24 hours')
          AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY raw_ts_utc DESC
        LIMIT 3000
    """).fetchall()

    fired = 0
    for fr in firms_rows:
        try:
            extra = json.loads(fr["extra"] or "{}")
            frp   = float(extra.get("frp") or 0)
            conf  = str(extra.get("confidence") or "").lower()
            # Only high-significance thermals near nuclear sites
            if frp < 30 and conf != "high":
                continue
        except Exception:
            continue

        for reactor in reactors:
            km = _hav(fr["lat"], fr["lon"], reactor["lat"], reactor["lon"])
            if km > _NUCLEAR_ALERT_KM:
                continue

            title = (
                f"☢ NUCLEAR THREAT: Thermal anomaly {km:.1f}km from "
                f"{reactor['name']}"
            )
            desc = (
                f"NASA FIRMS detection at ({fr['lat']:.3f}, {fr['lon']:.3f}) — "
                f"FRP {frp:.0f}MW, confidence {conf}. "
                f"Reactor: {reactor['name']} ({reactor['country']}). "
                f"Distance: {km:.1f}km. Verify immediately."
            )
            saved = _save_alert(
                "nuclear_threat", title, desc,
                reactor["lat"], reactor["lon"],
                country=reactor["country"],
                extra={"reactor": reactor["name"], "distance_km": round(km, 2),
                       "frp": frp, "confidence": conf,
                       "fire_lat": fr["lat"], "fire_lon": fr["lon"]},
            )
            if saved:
                fired += 1

    return fired


# ══════════════════════════════════════════════════════════════════════════════
# 4. ACLED EVENT WITHIN 5km OF PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

_PIPELINE_ALERT_KM = 5.0


async def run_pipeline_proximity_check() -> int:
    """
    Cross-reference latest ACLED events with major oil/gas pipeline routes.
    Fires alert if a conflict event is within _PIPELINE_ALERT_KM km of any segment.
    Returns number of new alerts fired.
    """
    if not (C.ENABLE_ACLED and C.ENABLE_PIPELINES):
        return 0

    try:
        from ingestors.static_layers import get_layer
        pipe_geo = await get_layer("pipelines")
    except Exception:
        return 0

    # Collect all pipeline segments
    segments: list[list[tuple[float, float]]] = []
    pipe_meta: list[dict] = []
    for f in (pipe_geo.get("features") or []):
        geom = f.get("geometry") or {}
        props = f.get("properties") or {}
        coords_list = []
        if geom.get("type") == "LineString":
            coords_list = [geom["coordinates"]]
        elif geom.get("type") == "MultiLineString":
            coords_list = geom["coordinates"]
        for coords in coords_list:
            if len(coords) >= 2:
                segments.append(coords)
                pipe_meta.append(props)

    if not segments:
        return 0

    conn = DB.get_conn()
    # ACLED events in last 24h
    acled_rows = conn.execute("""
        SELECT lat, lon, title, country
        FROM events
        WHERE source = 'acled'
          AND raw_ts_utc > datetime('now', '-24 hours')
          AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY raw_ts_utc DESC
        LIMIT 500
    """).fetchall()

    fired = 0
    for ev in acled_rows:
        elat, elon = ev["lat"], ev["lon"]

        for seg_idx, coords in enumerate(segments):
            # Check proximity to each segment (check subset of waypoints for speed)
            hit_km = None
            step = max(1, len(coords) // 20)  # check at most 20 points per segment
            for i in range(0, len(coords) - 1, step):
                ax, ay = coords[i][0],   coords[i][1]
                bx, by = coords[i+1][0], coords[i+1][1]
                km = _pt_to_segment_km(elon, elat, ax, ay, bx, by)
                if km < _PIPELINE_ALERT_KM:
                    hit_km = km
                    break

            if hit_km is None:
                continue

            meta  = pipe_meta[seg_idx]
            pname = meta.get("name") or meta.get("operator") or "Pipeline"
            subst = meta.get("substance") or "oil/gas"

            title = (
                f"⊸ PIPELINE THREAT: {ev['title'][:60]} — "
                f"{hit_km:.1f}km from {pname}"
            )
            desc  = (
                f"ACLED conflict event {hit_km:.1f}km from {pname} ({subst}). "
                f"Event: {ev['title'][:200]}. Country: {ev['country'] or '—'}. "
                f"Potential infrastructure sabotage risk."
            )
            pipe_lat = (coords[0][1] + coords[-1][1]) / 2
            pipe_lon = (coords[0][0] + coords[-1][0]) / 2
            saved = _save_alert(
                "pipeline_threat", title, desc,
                pipe_lat, pipe_lon,
                country=ev["country"] or "",
                extra={"pipeline": pname, "substance": subst,
                       "distance_km": round(hit_km, 2),
                       "event_lat": elat, "event_lon": elon,
                       "event_title": ev["title"][:100]},
            )
            if saved:
                fired += 1
            break  # one alert per ACLED event (nearest pipeline)

    return fired
