"""Wardar — SQLite WAL store (raw parameterized SQL, no ORM)"""
from __future__ import annotations
import sqlite3, os, threading, time
from config import settings as C

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# ── Schema migrations (append-only) ─────────────────────────────────────────
_MIGRATIONS: list[str] = [
    # v1 — initial schema
    """CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)""",
    """INSERT OR IGNORE INTO schema_version VALUES (0)""",

    # positions — live aviation + maritime + satellite
    """CREATE TABLE IF NOT EXISTS positions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        source        TEXT NOT NULL,          -- adsb|opensky|ais|tle
        callsign      TEXT,
        type          TEXT,                   -- aircraft|ship|satellite
        lat           REAL NOT NULL,
        lon           REAL NOT NULL,
        altitude_ft   REAL,
        speed_kts     REAL,
        heading_deg   REAL,
        country       TEXT,
        military_flag INTEGER DEFAULT 0,      -- 1 if military-flagged
        raw_ts_utc    TEXT NOT NULL,          -- original signal timestamp
        release_ts_utc TEXT NOT NULL,         -- delayed release time (raw_ts + delay)
        extra         TEXT                    -- JSON blob for source-specific fields
    )""",
    """CREATE INDEX IF NOT EXISTS idx_pos_release  ON positions(release_ts_utc)""",
    """CREATE INDEX IF NOT EXISTS idx_pos_source   ON positions(source)""",
    """CREATE INDEX IF NOT EXISTS idx_pos_callsign ON positions(callsign)""",
    """CREATE INDEX IF NOT EXISTS idx_pos_raw_ts   ON positions(raw_ts_utc)""",  # playback queries

    # events — conflict/OSINT overlays
    """CREATE TABLE IF NOT EXISTS events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        source        TEXT NOT NULL,          -- acled|gdelt|notam|osint_news
        title         TEXT,
        description   TEXT,
        lat           REAL,
        lon           REAL,
        country       TEXT,
        category      TEXT,                   -- conflict|notam|news
        raw_ts_utc    TEXT NOT NULL,
        release_ts_utc TEXT NOT NULL,
        url           TEXT,
        extra         TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_evt_release ON events(release_ts_utc)""",
    """CREATE INDEX IF NOT EXISTS idx_evt_source  ON events(source)""",
    """CREATE INDEX IF NOT EXISTS idx_evt_country ON events(country)""",
    # v2 — dedup index for georeferenced events (FIRMS/USGS/GPSJam)
    # NULL lat/lon rows (news articles) are excluded by SQLite NULL semantics.
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_evt_dedup ON events(source, raw_ts_utc, lat, lon)""",

    # v3 — community intelligence reports
    """CREATE TABLE IF NOT EXISTS community_reports (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        author_token  TEXT NOT NULL,          -- hashed browser UUID, no personal data
        title         TEXT NOT NULL,
        description   TEXT,
        lat           REAL,
        lon           REAL,
        country       TEXT DEFAULT '',
        category      TEXT DEFAULT 'intel',   -- intel|sighting|movement|incident|analysis
        source_url    TEXT DEFAULT '',
        upvotes       INTEGER DEFAULT 0,
        downvotes     INTEGER DEFAULT 0,
        verified      INTEGER DEFAULT 0,      -- 1 when net_votes >= 3
        hidden        INTEGER DEFAULT 0,      -- 1 when net_votes <= -3
        created_at    TEXT NOT NULL,
        extra         TEXT DEFAULT '{}'
    )""",
    """CREATE INDEX IF NOT EXISTS idx_cr_location  ON community_reports(lat, lon)""",
    """CREATE INDEX IF NOT EXISTS idx_cr_created   ON community_reports(created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_cr_country   ON community_reports(country)""",

    """CREATE TABLE IF NOT EXISTS community_votes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id     INTEGER NOT NULL,
        voter_token   TEXT NOT NULL,
        vote          INTEGER NOT NULL,       -- +1 or -1
        created_at    TEXT NOT NULL,
        UNIQUE(report_id, voter_token)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_cv_report ON community_votes(report_id)""",

    # v4 — community_reports deep intel fields (ADD COLUMN is idempotent via migration runner)
    """ALTER TABLE community_reports ADD COLUMN report_type TEXT DEFAULT 'other'""",
    """ALTER TABLE community_reports ADD COLUMN severity    INTEGER DEFAULT 3""",
    """ALTER TABLE community_reports ADD COLUMN confidence  TEXT DEFAULT 'medium'""",
    """ALTER TABLE community_reports ADD COLUMN image_url   TEXT DEFAULT ''""",

    # v5 — country intel chat
    """CREATE TABLE IF NOT EXISTS country_chat (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        country      TEXT NOT NULL,
        author_token TEXT NOT NULL,
        message      TEXT NOT NULL,
        created_at   TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_chat_country ON country_chat(country, created_at)""",

    # v6 — composite indexes for sampled queries (source+release for per-source latest)
    """CREATE INDEX IF NOT EXISTS idx_pos_src_release ON positions(source, release_ts_utc DESC, raw_ts_utc DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_evt_src_release ON events(source, release_ts_utc DESC, raw_ts_utc DESC)""",
    # Regular index on release_ts_utc for fast released-position queries
    """CREATE INDEX IF NOT EXISTS idx_pos_released ON positions(release_ts_utc, source, raw_ts_utc DESC)""",

    # v8 — true upsert: one row per live entity (source, callsign)
    # Deduplicate first (safe even if column already unique)
    """DELETE FROM positions WHERE id NOT IN (
        SELECT MAX(id) FROM positions GROUP BY source, callsign
    )""",
    # Unique constraint enables INSERT OR REPLACE upsert → DB stays at ~3-5k rows not millions
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_upsert ON positions(source, callsign)""",

    # v7 — AI intelligence briefs (SITREP)
    """CREATE TABLE IF NOT EXISTS intel_briefs (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        text          TEXT NOT NULL,
        generated_at  TEXT NOT NULL,
        model         TEXT NOT NULL DEFAULT '',
        sources_used  TEXT NOT NULL DEFAULT '[]'
    )""",
    """CREATE INDEX IF NOT EXISTS idx_brief_ts ON intel_briefs(generated_at DESC)""",

    # v11 — WW3 Risk Meter
    """CREATE TABLE IF NOT EXISTS ww3_meter (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        score        INTEGER NOT NULL,
        level        TEXT NOT NULL,
        assessment   TEXT NOT NULL,
        key_factors  TEXT NOT NULL DEFAULT '[]',
        generated_at TEXT NOT NULL,
        model        TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE INDEX IF NOT EXISTS idx_ww3_ts ON ww3_meter(generated_at DESC)""",

    # v10 — Phase 8: entity annotations + shared watchlists
    """CREATE TABLE IF NOT EXISTS entity_annotations (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        source       TEXT NOT NULL,
        callsign     TEXT NOT NULL,
        author_token TEXT NOT NULL,
        body         TEXT NOT NULL,
        upvotes      INTEGER DEFAULT 0,
        downvotes    INTEGER DEFAULT 0,
        hidden       INTEGER DEFAULT 0,
        created_at   TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_ann_entity ON entity_annotations(source, callsign, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_ann_hidden ON entity_annotations(hidden)""",

    """CREATE TABLE IF NOT EXISTS shared_watchlists (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        share_token TEXT UNIQUE NOT NULL,
        name        TEXT NOT NULL,
        owner_token TEXT NOT NULL,
        entities    TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        last_viewed TEXT
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_swl_token ON shared_watchlists(share_token)""",
    """CREATE INDEX        IF NOT EXISTS idx_swl_owner ON shared_watchlists(owner_token)""",

    # v12 — Phase 11: Entity Identity Graph
    # One canonical row per real-world entity (aircraft/ship/satellite).
    # Resolution: exact callsign match + MMSI/ICAO from extra JSON.
    """CREATE TABLE IF NOT EXISTS entities (
        uuid          TEXT PRIMARY KEY,
        callsign      TEXT NOT NULL,
        aliases       TEXT NOT NULL DEFAULT '[]',   -- JSON array of alt callsigns/MMSIs
        source_refs   TEXT NOT NULL DEFAULT '[]',   -- JSON array of {source, callsign} seen
        type          TEXT NOT NULL DEFAULT '',     -- aircraft|ship|satellite
        country       TEXT NOT NULL DEFAULT '',
        military_flag INTEGER NOT NULL DEFAULT 0,
        ofac_flag     INTEGER NOT NULL DEFAULT 0,
        first_seen    TEXT NOT NULL,
        last_seen     TEXT NOT NULL,
        obs_count     INTEGER NOT NULL DEFAULT 1,
        lat           REAL,
        lon           REAL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_ent_callsign ON entities(callsign)""",
    """CREATE INDEX        IF NOT EXISTS idx_ent_type     ON entities(type)""",
    """CREATE INDEX        IF NOT EXISTS idx_ent_country  ON entities(country)""",
    """CREATE INDEX        IF NOT EXISTS idx_ent_mil      ON entities(military_flag)""",

    # entity_mentions: links an event to an entity when callsign appears in title/desc
    """CREATE TABLE IF NOT EXISTS entity_mentions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_uuid  TEXT NOT NULL,
        event_id     INTEGER NOT NULL,
        confidence   REAL NOT NULL DEFAULT 1.0,   -- 1.0=exact, 0.8=fuzzy
        match_type   TEXT NOT NULL DEFAULT 'exact',
        created_at   TEXT NOT NULL,
        UNIQUE(entity_uuid, event_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_em_entity ON entity_mentions(entity_uuid, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_em_event  ON entity_mentions(event_id)""",

    # v9 — Phase 5 position history (separate from upsert positions table)
    # Stores a throttled time-series of each entity's positions for track/biography.
    # One row per entity per ~2 min (throttled in engine.py to keep DB lean).
    """CREATE TABLE IF NOT EXISTS position_history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        source        TEXT NOT NULL,
        callsign      TEXT NOT NULL,
        lat           REAL NOT NULL,
        lon           REAL NOT NULL,
        altitude_ft   REAL,
        speed_kts     REAL,
        heading_deg   REAL,
        military_flag INTEGER DEFAULT 0,
        raw_ts_utc    TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_dedup  ON position_history(source, callsign, raw_ts_utc)""",
    """CREATE INDEX        IF NOT EXISTS idx_hist_track  ON position_history(source, callsign, raw_ts_utc)""",
    """CREATE INDEX        IF NOT EXISTS idx_hist_ts     ON position_history(raw_ts_utc)""",
    """CREATE INDEX        IF NOT EXISTS idx_hist_bbox   ON position_history(lat, lon, raw_ts_utc)""",

    # v13 — chokepoint throughput time-series (Task 6)
    # Hourly snapshot of vessel/aircraft count per chokepoint.
    # ts_utc is truncated to the hour: YYYY-MM-DDTHH:00:00
    """CREATE TABLE IF NOT EXISTS chokepoint_history (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT NOT NULL,
        ts_utc   TEXT NOT NULL,
        count_1h INTEGER NOT NULL DEFAULT 0,
        UNIQUE(name, ts_utc)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_cphs_name ON chokepoint_history(name, ts_utc DESC)""",
]

def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(C.DB_PATH)), exist_ok=True)
        _conn = sqlite3.connect(C.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA cache_size=-8000")    # 8 MB page cache (DB is small with upsert)
        _conn.execute("PRAGMA temp_store=MEMORY")   # temp tables in RAM
        _conn.execute("PRAGMA mmap_size=33554432")  # 32 MB mmap (DB stays lean)
        _conn.execute("PRAGMA optimize")
        _run_migrations(_conn)
    return _conn

def _run_migrations(conn: sqlite3.Connection):
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # ADD COLUMN already applied on a prior run
            else:
                raise
    conn.commit()

# ── Positions ────────────────────────────────────────────────────────────────

def upsert_position(p: dict) -> None:
    """True upsert — one live row per (source, callsign). Keeps DB tiny."""
    with _lock:
        conn = get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO positions
              (source, callsign, type, lat, lon, altitude_ft, speed_kts,
               heading_deg, country, military_flag, raw_ts_utc, release_ts_utc, extra)
            VALUES
              (:source,:callsign,:type,:lat,:lon,:altitude_ft,:speed_kts,
               :heading_deg,:country,:military_flag,:raw_ts_utc,:release_ts_utc,:extra)
        """, p)
        conn.commit()

def get_released_positions(bbox: tuple[float,float,float,float] | None = None,
                            sources: list[str] | None = None,
                            limit: int = 5000) -> list[dict]:
    """Return positions whose release_ts_utc <= now (i.e. delay has elapsed)."""
    now = _utcnow()
    conn = get_conn()
    clauses = ["release_ts_utc <= ?"]
    params: list = [now]
    if sources:
        placeholders = ",".join("?" * len(sources))
        clauses.append(f"source IN ({placeholders})")
        params.extend(sources)
    if bbox:
        w, s, e, n = bbox
        clauses.append("lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
        params.extend([w, e, s, n])
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM positions WHERE {where} ORDER BY raw_ts_utc DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    return [dict(r) for r in rows]


def get_released_positions_sampled(per_source: int = 600,
                                    limit: int = 3000) -> list[dict]:
    """
    Return a balanced sample across all sources — prevents AIS (7M+ records)
    from flooding all slots and hiding aircraft/satellites.
    Military aircraft are sampled separately to ensure they're always
    represented (they have a 24h delay so they'd otherwise be buried by
    civilian aircraft sorted by raw_ts_utc DESC).
    """
    now = _utcnow()
    conn = get_conn()
    # Get active sources
    src_rows = conn.execute(
        "SELECT DISTINCT source FROM positions WHERE release_ts_utc <= ? LIMIT 20",
        (now,)
    ).fetchall()
    all_rows: list[dict] = []
    seen_keys: set = set()

    for row in src_rows:
        src = row[0]
        # For aircraft sources, always pull military flag=1 separately first
        if src in ('adsb', 'opensky'):
            mil_rows = conn.execute(
                "SELECT * FROM positions WHERE source=? AND military_flag=1 "
                "ORDER BY raw_ts_utc DESC LIMIT ?",
                (src, per_source // 2)
            ).fetchall()
            for r in mil_rows:
                d = dict(r)
                k = d.get('id')
                if k not in seen_keys:
                    seen_keys.add(k)
                    all_rows.append(d)
            # Civilian portion
            civ_rows = conn.execute(
                "SELECT * FROM positions WHERE source=? AND military_flag=0 AND release_ts_utc<=? "
                "ORDER BY raw_ts_utc DESC LIMIT ?",
                (src, now, per_source // 2)
            ).fetchall()
            for r in civ_rows:
                d = dict(r)
                k = d.get('id')
                if k not in seen_keys:
                    seen_keys.add(k)
                    all_rows.append(d)
        else:
            rows = conn.execute(
                "SELECT * FROM positions WHERE source=? AND release_ts_utc<=? "
                "ORDER BY raw_ts_utc DESC LIMIT ?",
                (src, now, per_source)
            ).fetchall()
            for r in rows:
                d = dict(r)
                k = d.get('id')
                if k not in seen_keys:
                    seen_keys.add(k)
                    all_rows.append(d)

    all_rows.sort(key=lambda x: x.get("raw_ts_utc", ""), reverse=True)
    return all_rows[:limit]

# ── Playback ─────────────────────────────────────────────────────────────────

def get_playback_summary() -> dict:
    """Return time range and per-minute bucket counts for the playback timeline.

    Combines positions (aircraft/ships) AND geo-tagged events so the timeline
    initialises even when no ADS-B/AIS data is available yet.
    """
    conn = get_conn()
    row = conn.execute("""
        SELECT MIN(ts) as oldest, MAX(ts) as newest, COUNT(*) as total FROM (
            SELECT raw_ts_utc AS ts FROM positions
            UNION ALL
            SELECT raw_ts_utc AS ts FROM events
            WHERE lat IS NOT NULL AND lon IS NOT NULL
        )
    """).fetchone()
    if not row or not row["oldest"]:
        return {"oldest": None, "newest": None, "total": 0, "buckets": []}

    # Per-5-minute bucket counts — combined positions + events
    buckets_raw = conn.execute("""
        SELECT bucket, SUM(cnt) AS callsigns FROM (
            SELECT
              strftime('%Y-%m-%dT%H:', raw_ts_utc) ||
                printf('%02d', (CAST(strftime('%M', raw_ts_utc) AS INTEGER) / 5) * 5)
                || ':00+00:00' AS bucket,
              COUNT(DISTINCT callsign) AS cnt
            FROM positions
            GROUP BY bucket
            UNION ALL
            SELECT
              strftime('%Y-%m-%dT%H:', raw_ts_utc) ||
                printf('%02d', (CAST(strftime('%M', raw_ts_utc) AS INTEGER) / 5) * 5)
                || ':00+00:00' AS bucket,
              COUNT(*) AS cnt
            FROM events WHERE lat IS NOT NULL AND lon IS NOT NULL
            GROUP BY bucket
        )
        GROUP BY bucket
        ORDER BY bucket
    """).fetchall()

    return {
        "oldest":  row["oldest"],
        "newest":  row["newest"],
        "total":   row["total"],
        "buckets": [{"ts": r["bucket"], "callsigns": r["callsigns"]} for r in buckets_raw],
    }

def get_positions_at(ts: str, window_sec: int = 600,
                     sources: list[str] | None = None,
                     bbox: tuple | None = None,
                     limit: int = 5000) -> list[dict]:
    """
    Return the latest position per callsign/source visible at timestamp `ts`.

    Looks back up to `window_sec` to find the most recent fix per callsign.
    Default 600s: handles ADS-B (10s cycle), AIS (event-driven), TLE (hourly).
    Delay policy enforced: only returns positions where release_ts_utc <= ts.
    """
    conn = get_conn()
    window_start = _offset_ts(ts, -window_sec)

    base_clauses  = ["raw_ts_utc <= ?", "raw_ts_utc >= ?", "release_ts_utc <= ?"]
    base_params: list = [ts, window_start, ts]

    if sources:
        ph = ",".join("?" * len(sources))
        base_clauses.append(f"source IN ({ph})")
        base_params.extend(sources)
    if bbox:
        w, s, e, n = bbox
        base_clauses.append("lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
        base_params.extend([w, e, s, n])

    where = " AND ".join(base_clauses)

    # Latest fix per (callsign, source) in the window — single-pass with join
    rows = conn.execute(f"""
        SELECT p.*
        FROM positions p
        INNER JOIN (
            SELECT callsign, source, MAX(raw_ts_utc) AS max_ts
            FROM positions
            WHERE {where}
            GROUP BY callsign, source
        ) latest
          ON  p.callsign   = latest.callsign
          AND p.source     = latest.source
          AND p.raw_ts_utc = latest.max_ts
        LIMIT ?
    """, base_params + [limit]).fetchall()

    return [dict(r) for r in rows]

def get_events_at(ts: str, window_hours: int = 24, limit: int = 500) -> list[dict]:
    """Return geo-tagged events visible at playback timestamp `ts`.

    Returns events where raw_ts_utc is between (ts - window_hours) and ts,
    respecting the release delay (release_ts_utc <= ts).
    """
    conn = get_conn()
    window_start = _offset_ts(ts, -window_hours * 3600)
    rows = conn.execute("""
        SELECT * FROM events
        WHERE raw_ts_utc <= ?
          AND raw_ts_utc >= ?
          AND release_ts_utc <= ?
          AND lat IS NOT NULL
          AND lon IS NOT NULL
        ORDER BY raw_ts_utc DESC LIMIT ?
    """, (ts, window_start, ts, limit)).fetchall()
    return [dict(r) for r in rows]

def _offset_ts(ts: str, delta_sec: int) -> str:
    from datetime import datetime, timedelta, timezone
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    return (dt + timedelta(seconds=delta_sec)).isoformat()

def purge_old_positions() -> int:
    """Remove stale positions and VACUUM to reclaim memory. With upsert, table stays tiny."""
    cutoff_default = _utcnow_minus_hours(C.POSITION_RETAIN_HOURS)
    cutoff_ais     = _utcnow_minus_hours(C.AIS_RETAIN_HOURS)
    with _lock:
        conn = get_conn()
        n1 = conn.execute(
            "DELETE FROM positions WHERE source='ais' AND raw_ts_utc < ?", (cutoff_ais,)
        ).rowcount
        n2 = conn.execute(
            "DELETE FROM positions WHERE source!='ais' AND raw_ts_utc < ?", (cutoff_default,)
        ).rowcount
        conn.commit()
        # Reclaim freed pages on each purge (fast with WAL + small DB)
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return n1 + n2

# ── Events ───────────────────────────────────────────────────────────────────

def upsert_event(e: dict) -> None:
    """Insert event, skipping exact duplicates (same source/ts/lat/lon)."""
    with _lock:
        conn = get_conn()
        conn.execute("""
            INSERT OR IGNORE INTO events
              (source, title, description, lat, lon, country, category,
               raw_ts_utc, release_ts_utc, url, extra)
            VALUES
              (:source,:title,:description,:lat,:lon,:country,:category,
               :raw_ts_utc,:release_ts_utc,:url,:extra)
        """, e)
        conn.commit()

def get_released_events(bbox: tuple[float,float,float,float] | None = None,
                         sources: list[str] | None = None,
                         limit: int = 1000) -> list[dict]:
    now = _utcnow()
    conn = get_conn()
    clauses = ["release_ts_utc <= ?"]
    params: list = [now]
    if sources:
        placeholders = ",".join("?" * len(sources))
        clauses.append(f"source IN ({placeholders})")
        params.extend(sources)
    if bbox:
        w, s, e, n = bbox
        # Include events with NULL coords (news/SIGINT) OR within the bbox
        clauses.append("(lat IS NULL OR (lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?))")
        params.extend([w, e, s, n])
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM events WHERE {where} ORDER BY raw_ts_utc DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    return [dict(r) for r in rows]

def purge_old_events() -> int:
    cutoff_days = C.EVENT_RETAIN_DAYS
    conn = get_conn()
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).isoformat()
    with _lock:
        cur = conn.execute("DELETE FROM events WHERE raw_ts_utc < ?", (cutoff,))
        conn.commit()
        return cur.rowcount

# ── Phase 5: Position History ────────────────────────────────────────────────

def insert_position_history(p: dict) -> None:
    """Insert a history snapshot for an entity. IGNORE on exact duplicate ts."""
    with _lock:
        conn = get_conn()
        conn.execute("""
            INSERT OR IGNORE INTO position_history
              (source, callsign, lat, lon, altitude_ft, speed_kts, heading_deg,
               military_flag, raw_ts_utc)
            VALUES
              (:source,:callsign,:lat,:lon,:altitude_ft,:speed_kts,:heading_deg,
               :military_flag,:raw_ts_utc)
        """, p)
        conn.commit()

def get_position_track(source: str, callsign: str, hours: int = 24) -> list[dict]:
    """
    Return the throttled position history for (source, callsign) over the last `hours`.
    Delay policy enforced: military positions held back 300s, civilian 30s.
    Returns points in chronological order.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cutoff_old = (now - timedelta(hours=hours)).isoformat()
    # Apply release delay: military = 300s, civilian = 30s
    # We don't know military_flag without querying, so use a conservative 30s for civilian
    # and look it up per-row below.
    rows = get_conn().execute("""
        SELECT lat, lon, altitude_ft, speed_kts, heading_deg, military_flag, raw_ts_utc
        FROM position_history
        WHERE source = ? AND callsign = ? AND raw_ts_utc >= ?
        ORDER BY raw_ts_utc ASC
    """, (source, callsign, cutoff_old)).fetchall()

    now_iso = now.isoformat()
    result = []
    for r in rows:
        delay_s = 300 if r["military_flag"] else 30
        cutoff_ts = (now - timedelta(seconds=delay_s)).isoformat()
        if r["raw_ts_utc"] <= cutoff_ts:
            result.append(dict(r))
    return result

def get_position_track_for_deviation(source: str, callsign: str,
                                      recent_hours: float = 1.0,
                                      baseline_hours_min: float = 24.0,
                                      baseline_hours_max: float = 72.0) -> tuple[list, list]:
    """
    Returns (recent_points, baseline_points) for route deviation analysis.
    recent_points:   last `recent_hours` hours of positions
    baseline_points: positions between `baseline_hours_min` and `baseline_hours_max` ago
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    recent_cutoff   = (now - timedelta(hours=recent_hours)).isoformat()
    baseline_start  = (now - timedelta(hours=baseline_hours_max)).isoformat()
    baseline_end    = (now - timedelta(hours=baseline_hours_min)).isoformat()

    conn = get_conn()
    recent = conn.execute("""
        SELECT lat, lon FROM position_history
        WHERE source=? AND callsign=? AND raw_ts_utc >= ?
        ORDER BY raw_ts_utc ASC
    """, (source, callsign, recent_cutoff)).fetchall()

    baseline = conn.execute("""
        SELECT lat, lon FROM position_history
        WHERE source=? AND callsign=? AND raw_ts_utc BETWEEN ? AND ?
        ORDER BY raw_ts_utc ASC
    """, (source, callsign, baseline_start, baseline_end)).fetchall()

    return [dict(r) for r in recent], [dict(r) for r in baseline]

def get_active_callsigns_in_history(min_points: int = 5) -> list[dict]:
    """Return (source, callsign) pairs with enough history for deviation analysis."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT source, callsign, COUNT(*) as n
        FROM position_history
        GROUP BY source, callsign
        HAVING n >= ?
    """, (min_points,)).fetchall()
    return [dict(r) for r in rows]

def get_chokepoint_count(bbox_w: float, bbox_s: float, bbox_e: float, bbox_n: float,
                          hours: int = 24) -> int:
    """Count distinct callsigns seen in a bounding box over the last `hours`."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(DISTINCT callsign) as n
        FROM position_history
        WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND raw_ts_utc >= ?
    """, (bbox_s, bbox_n, bbox_w, bbox_e, cutoff)).fetchone()
    return row["n"] if row else 0

def store_chokepoint_snapshot(name: str, ts_hour: str, count_1h: int) -> None:
    """Upsert a single-hour chokepoint throughput snapshot."""
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO chokepoint_history (name, ts_utc, count_1h) VALUES (?,?,?)",
            (name, ts_hour, count_1h),
        )
        conn.commit()


def get_chokepoint_history(name: str, hours: int = 168) -> list[dict]:
    """Return hourly throughput snapshots for a chokepoint (default last 7 days)."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT ts_utc, count_1h FROM chokepoint_history WHERE name=? AND ts_utc>=? ORDER BY ts_utc ASC",
        (name, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def purge_old_history() -> int:
    """Delete position_history rows older than HISTORY_RETAIN_HOURS."""
    cutoff = _utcnow_minus_hours(C.HISTORY_RETAIN_HOURS)
    with _lock:
        conn = get_conn()
        cur = conn.execute("DELETE FROM position_history WHERE raw_ts_utc < ?", (cutoff,))
        conn.commit()
        return cur.rowcount

# ── Community Intel ──────────────────────────────────────────────────────────

def insert_community_report(r: dict) -> int:
    """Insert a new community report, return its id."""
    with _lock:
        conn = get_conn()
        cur = conn.execute("""
            INSERT INTO community_reports
              (author_token, title, description, lat, lon, country, category,
               source_url, report_type, severity, confidence, image_url,
               created_at, extra)
            VALUES
              (:author_token,:title,:description,:lat,:lon,:country,:category,
               :source_url,:report_type,:severity,:confidence,:image_url,
               :created_at,:extra)
        """, r)
        conn.commit()
        return cur.lastrowid

def get_community_report(report_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM community_reports WHERE id = ?", (report_id,)
    ).fetchone()
    return dict(row) if row else None

def get_community_reports_near(lat: float, lon: float, radius_km: float = 200,
                                limit: int = 100) -> list[dict]:
    """Return visible (not hidden) reports within ~radius_km of (lat, lon)."""
    # Rough degree delta: 1° lat ≈ 111 km
    delta = radius_km / 111.0
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM community_reports
        WHERE hidden = 0
          AND lat BETWEEN ? AND ?
          AND lon BETWEEN ? AND ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (lat - delta, lat + delta, lon - delta, lon + delta, limit)).fetchall()
    return [dict(r) for r in rows]

def get_community_reports_global(limit: int = 500) -> list[dict]:
    """Return all visible verified + recent community reports for map display."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM community_reports
        WHERE hidden = 0 AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]

def vote_community_report(report_id: int, voter_token: str, vote: int) -> dict:
    """
    Cast or flip a vote (+1/-1). Returns updated report dict.
    Raises ValueError if report not found.
    """
    if vote not in (1, -1):
        raise ValueError("vote must be +1 or -1")
    now = _utcnow()
    with _lock:
        conn = get_conn()
        # Upsert vote (UNIQUE constraint handles flip)
        conn.execute("""
            INSERT INTO community_votes (report_id, voter_token, vote, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(report_id, voter_token) DO UPDATE SET vote=excluded.vote
        """, (report_id, voter_token, vote, now))
        # Recount and update report
        counts = conn.execute("""
            SELECT
              SUM(CASE WHEN vote=1  THEN 1 ELSE 0 END) as ups,
              SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END) as downs
            FROM community_votes WHERE report_id=?
        """, (report_id,)).fetchone()
        ups   = counts["ups"]   or 0
        downs = counts["downs"] or 0
        net   = ups - downs
        verified = 1 if net >= 3 else 0
        hidden   = 1 if net <= -3 else 0
        conn.execute("""
            UPDATE community_reports
            SET upvotes=?, downvotes=?, verified=?, hidden=?
            WHERE id=?
        """, (ups, downs, verified, hidden, report_id))
        conn.commit()
    return get_community_report(report_id)

# ── Country Intel Chat ───────────────────────────────────────────────────────

def insert_chat_message(country: str, author_token: str, message: str) -> dict:
    now = _utcnow()
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO country_chat (country, author_token, message, created_at) VALUES (?,?,?,?)",
            (country.lower().strip(), author_token, message, now)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM country_chat WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

def get_chat_messages(country: str, limit: int = 100) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM country_chat WHERE country=? ORDER BY created_at DESC LIMIT ?",
        (country.lower().strip(), limit)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]  # oldest first for chat display

# ── Stats ────────────────────────────────────────────────────────────────────

def get_counts() -> dict:
    conn = get_conn()
    now = _utcnow()
    pos_total   = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    pos_live    = conn.execute("SELECT COUNT(*) FROM positions WHERE release_ts_utc <= ?", (now,)).fetchone()[0]
    evt_total   = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    evt_live    = conn.execute("SELECT COUNT(*) FROM events WHERE release_ts_utc <= ?", (now,)).fetchone()[0]
    by_source   = conn.execute(
        "SELECT source, COUNT(*) as n FROM positions GROUP BY source"
    ).fetchall()
    try:
        ann_total = conn.execute("SELECT COUNT(*) FROM entity_annotations").fetchone()[0]
    except Exception:
        ann_total = 0
    return {
        "positions_total": pos_total,
        "positions_live":  pos_live,
        "events_total":    evt_total,
        "events_live":     evt_live,
        "positions":       pos_live,   # alias for admin dashboard
        "events":          evt_total,  # alias for admin dashboard
        "annotations":     ann_total,
        "by_source":       {r["source"]: r["n"] for r in by_source},
    }

# ── Intel Briefs (SITREP) ────────────────────────────────────────────────────

def upsert_intel_brief(brief: dict) -> None:
    """Insert a new intel brief row. Keeps only the 10 most recent."""
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO intel_briefs (text, generated_at, model, sources_used) VALUES (?,?,?,?)",
            (brief["text"], brief["generated_at"],
             brief.get("model", ""), brief.get("sources_used", "[]"))
        )
        # Prune old briefs — keep 10 most recent
        conn.execute(
            "DELETE FROM intel_briefs WHERE id NOT IN "
            "(SELECT id FROM intel_briefs ORDER BY generated_at DESC LIMIT 10)"
        )
        conn.commit()

def get_latest_brief() -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM intel_briefs ORDER BY generated_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None

# ── WW3 Risk Meter ───────────────────────────────────────────────────────────

def upsert_ww3_meter(score: int, level: str, assessment: str,
                      key_factors: list, model: str) -> None:
    import json as _json
    now = _utcnow()
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO ww3_meter (score,level,assessment,key_factors,generated_at,model) VALUES (?,?,?,?,?,?)",
            (score, level, assessment, _json.dumps(key_factors), now, model)
        )
        # Keep only last 30 readings (30 days)
        conn.execute(
            "DELETE FROM ww3_meter WHERE id NOT IN "
            "(SELECT id FROM ww3_meter ORDER BY generated_at DESC LIMIT 30)"
        )
        conn.commit()

def get_ww3_meter() -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM ww3_meter ORDER BY generated_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None

def get_ww3_history(days: int = 30) -> list[dict]:
    cutoff = _utcnow_minus_hours(days * 24)
    conn = get_conn()
    rows = conn.execute(
        "SELECT score,level,assessment,key_factors,generated_at,model FROM ww3_meter "
        "WHERE generated_at>=? ORDER BY generated_at DESC",
        (cutoff,)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("key_factors"):
            try: d["key_factors"] = _json.loads(d["key_factors"])
            except Exception: pass
        result.append(d)
    return result

# ── Phase 8: Entity Annotations ──────────────────────────────────────────────

def insert_annotation(source: str, callsign: str, author_token: str, body: str) -> int:
    now = _utcnow()
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO entity_annotations (source,callsign,author_token,body,created_at) VALUES (?,?,?,?,?)",
            (source, callsign, author_token, body, now)
        )
        conn.commit()
        return cur.lastrowid

def get_annotations(source: str, callsign: str, limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM entity_annotations WHERE source=? AND callsign=? AND hidden=0 "
        "ORDER BY (upvotes-downvotes) DESC, created_at DESC LIMIT ?",
        (source, callsign, limit)
    ).fetchall()
    return [dict(r) for r in rows]

def vote_annotation(ann_id: int, delta_up: int, delta_down: int) -> dict | None:
    """Increment upvotes or downvotes. delta_up/delta_down should be +1."""
    with _lock:
        conn = get_conn()
        conn.execute(
            "UPDATE entity_annotations SET upvotes=upvotes+?, downvotes=downvotes+?, "
            "hidden=CASE WHEN (upvotes+?-downvotes-?)<=(-3) THEN 1 ELSE 0 END WHERE id=?",
            (delta_up, delta_down, delta_up, delta_down, ann_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM entity_annotations WHERE id=?", (ann_id,)).fetchone()
        return dict(row) if row else None

# ── Phase 8: Shared Watchlists ────────────────────────────────────────────────

def create_shared_watchlist(name: str, owner_token: str, entities_json: str) -> str:
    import secrets
    token = secrets.token_urlsafe(12)
    now = _utcnow()
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO shared_watchlists (share_token,name,owner_token,entities,created_at) VALUES (?,?,?,?,?)",
            (token, name, owner_token, entities_json, now)
        )
        conn.commit()
    return token

def get_shared_watchlist(share_token: str) -> dict | None:
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM shared_watchlists WHERE share_token=?", (share_token,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE shared_watchlists SET last_viewed=? WHERE share_token=?",
            (_utcnow(), share_token)
        )
        conn.commit()
    return dict(row)

# ── Phase 11: Entity Identity Graph ──────────────────────────────────────────

def upsert_entity(e: dict) -> str:
    """
    Insert or update an entity record. e must have: uuid, callsign, type, country,
    military_flag, ofac_flag, lat, lon, now (ISO timestamp).
    Returns the uuid.
    """
    now = e.get("now") or _utcnow()
    with _lock:
        conn = get_conn()
        params = {
            "uuid":         e["uuid"],
            "callsign":     e["callsign"],
            "aliases":      e.get("aliases", "[]"),
            "source_refs":  e.get("source_refs", "[]"),
            "type":         e.get("type", ""),
            "country":      e.get("country", ""),
            "military_flag": int(e.get("military_flag") or 0),
            "ofac_flag":    int(e.get("ofac_flag") or 0),
            "lat":          e.get("lat"),
            "lon":          e.get("lon"),
            "now":          now,
        }
        try:
            conn.execute("""
                INSERT INTO entities
                  (uuid, callsign, aliases, source_refs, type, country,
                   military_flag, ofac_flag, first_seen, last_seen, obs_count, lat, lon)
                VALUES
                  (:uuid,:callsign,:aliases,:source_refs,:type,:country,
                   :military_flag,:ofac_flag,:now,:now,1,:lat,:lon)
                ON CONFLICT(callsign) DO UPDATE SET
                  aliases       = excluded.aliases,
                  source_refs   = excluded.source_refs,
                  type          = CASE WHEN excluded.type != '' THEN excluded.type ELSE type END,
                  country       = CASE WHEN excluded.country != '' THEN excluded.country ELSE country END,
                  military_flag = MAX(military_flag, excluded.military_flag),
                  ofac_flag     = MAX(ofac_flag,     excluded.ofac_flag),
                  last_seen     = excluded.last_seen,
                  obs_count     = obs_count + 1,
                  lat           = excluded.lat,
                  lon           = excluded.lon
            """, params)
        except Exception as exc:
            if "entities.uuid" in str(exc):
                # UUID already exists under a different callsign (alias-resolution edge case).
                # Fall back to UPDATE by uuid so we don't lose the observation.
                conn.execute("""
                    UPDATE entities SET
                      aliases       = :aliases,
                      source_refs   = :source_refs,
                      military_flag = MAX(military_flag, :military_flag),
                      ofac_flag     = MAX(ofac_flag,     :ofac_flag),
                      last_seen     = :now,
                      obs_count     = obs_count + 1,
                      lat           = :lat,
                      lon           = :lon
                    WHERE uuid = :uuid
                """, params)
            else:
                raise
        conn.commit()
    return e["uuid"]

def get_entity_by_callsign(callsign: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM entities WHERE callsign=?", (callsign,)
    ).fetchone()
    return dict(row) if row else None

def get_entity(uuid: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM entities WHERE uuid=?", (uuid,)
    ).fetchone()
    return dict(row) if row else None

def get_entities(type_filter: str | None = None, country: str | None = None,
                 military_only: bool = False, limit: int = 200) -> list[dict]:
    conn = get_conn()
    clauses, params = [], []
    if type_filter:
        clauses.append("type=?"); params.append(type_filter)
    if country:
        clauses.append("country=?"); params.append(country)
    if military_only:
        clauses.append("military_flag=1")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM entities {where} ORDER BY obs_count DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    return [dict(r) for r in rows]

def get_entity_timeline(uuid: str, hours: int = 72) -> dict:
    """
    Return merged timeline for one entity:
      positions  — from position_history
      events     — from entity_mentions → events
      annotations — from entity_annotations (by callsign)
    """
    conn = get_conn()
    cutoff = _utcnow_minus_hours(hours)

    # Look up entity to get callsign
    ent = get_entity(uuid)
    if not ent:
        return {"positions": [], "events": [], "annotations": []}
    callsign = ent["callsign"]

    # Position track
    positions = conn.execute("""
        SELECT lat, lon, altitude_ft, speed_kts, heading_deg, military_flag, raw_ts_utc, source
        FROM position_history
        WHERE callsign=? AND raw_ts_utc >= ?
        ORDER BY raw_ts_utc ASC
    """, (callsign, cutoff)).fetchall()

    # Mentioned events
    events = conn.execute("""
        SELECT e.id, e.source, e.title, e.description, e.lat, e.lon,
               e.country, e.raw_ts_utc, e.url, em.confidence, em.match_type
        FROM entity_mentions em
        JOIN events e ON e.id = em.event_id
        WHERE em.entity_uuid=? AND e.raw_ts_utc >= ?
        ORDER BY e.raw_ts_utc DESC
        LIMIT 50
    """, (uuid, cutoff)).fetchall()

    # Analyst annotations
    annotations = conn.execute("""
        SELECT id, body, upvotes, downvotes, created_at, author_token
        FROM entity_annotations
        WHERE callsign=? AND hidden=0
        ORDER BY created_at DESC
        LIMIT 20
    """, (callsign,)).fetchall()

    return {
        "positions":    [dict(r) for r in positions],
        "events":       [dict(r) for r in events],
        "annotations":  [dict(r) for r in annotations],
    }

def insert_entity_mention(entity_uuid: str, event_id: int,
                          confidence: float = 1.0, match_type: str = "exact") -> None:
    now = _utcnow()
    with _lock:
        conn = get_conn()
        conn.execute("""
            INSERT OR IGNORE INTO entity_mentions
              (entity_uuid, event_id, confidence, match_type, created_at)
            VALUES (?,?,?,?,?)
        """, (entity_uuid, event_id, confidence, match_type, now))
        conn.commit()

# ── Helpers ──────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _utcnow_minus_hours(h: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()
