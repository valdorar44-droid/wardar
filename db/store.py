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
    """Insert or replace a position record."""
    with _lock:
        conn = get_conn()
        conn.execute("""
            INSERT INTO positions
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

# ── Playback ─────────────────────────────────────────────────────────────────

def get_playback_summary() -> dict:
    """Return time range and per-minute bucket counts for the playback timeline."""
    conn = get_conn()
    row = conn.execute("""
        SELECT MIN(raw_ts_utc) as oldest, MAX(raw_ts_utc) as newest, COUNT(*) as total
        FROM positions
    """).fetchone()
    if not row or not row["oldest"]:
        return {"oldest": None, "newest": None, "total": 0, "buckets": []}

    # Per-5-minute bucket counts — strftime rounds to 5-min interval
    buckets_raw = conn.execute("""
        SELECT
          strftime('%Y-%m-%dT%H:', raw_ts_utc) ||
            printf('%02d', (CAST(strftime('%M', raw_ts_utc) AS INTEGER) / 5) * 5)
            || ':00+00:00' AS bucket,
          COUNT(DISTINCT callsign) AS callsigns
        FROM positions
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

def _offset_ts(ts: str, delta_sec: int) -> str:
    from datetime import datetime, timedelta, timezone
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    return (dt + timedelta(seconds=delta_sec)).isoformat()

def purge_old_positions() -> int:
    """Delete positions older than POSITION_RETAIN_HOURS."""
    cutoff = _utcnow_minus_hours(C.POSITION_RETAIN_HOURS)
    with _lock:
        conn = get_conn()
        cur = conn.execute("DELETE FROM positions WHERE raw_ts_utc < ?", (cutoff,))
        conn.commit()
        return cur.rowcount

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
        clauses.append("lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
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
    return {
        "positions_total": pos_total,
        "positions_live":  pos_live,
        "events_total":    evt_total,
        "events_live":     evt_live,
        "by_source":       {r["source"]: r["n"] for r in by_source},
    }

# ── Helpers ──────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _utcnow_minus_hours(h: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()
