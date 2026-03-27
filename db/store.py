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
        conn.execute(sql)
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
    with _lock:
        conn = get_conn()
        conn.execute("""
            INSERT INTO events
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
