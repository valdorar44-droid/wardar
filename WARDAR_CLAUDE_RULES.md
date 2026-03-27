# Wardar — Claude Operating Rules

## Stack (read this first)
- **Frontend**: Single HTML SPA — `dashboard/map.html`. Leaflet.js, no framework, no build step.
- **Backend**: Python FastAPI — `api/server.py`
- **DB**: SQLite WAL via `db/store.py` — no ORM, raw parameterized SQL
- **Ingestors**: `ingestors/` — one file per source, all return `Position` or `Event` dicts
- **Engine**: `core/engine.py` — orchestrates ingestors, applies delay policy, writes to DB
- **Config**: `config/settings.py` — ALL delay constants here, nowhere else
- **Deploy**: Railway, DB volume at `/data/wardar.db`

## Frontend Rules
- Dark map tiles only — use `CartoDB.DarkMatter` or equivalent
- Terminal/intelligence aesthetic — matches Argus identity
- Every layer needs: loading state, empty state, error state
- Use `esc()` helper for any user content in innerHTML (XSS)
- Aircraft icon ✈, ship icon ⛵ (or SVG equivalents), satellite icon ◎
- Cluster icons when >500 visible — Leaflet.markercluster
- Popup on click: callsign, type, speed, altitude/depth, last seen, delay status
- Show delay badge when data is >30s old — `[DELAYED 24H]` in amber

## Backend Rules
- Always read the route before editing
- `python -m py_compile <file>.py` after every edit
- Never swallow exceptions — log with `log_warn` or `log_err`
- Auth: `_require_user()` for any authenticated route
- **NEVER remove the delay policy check** — it is in `core/engine.py:apply_delay()`
- DB changes: SQLite won't auto-add columns — write a migration in `db/store.py`

## Ingestor Rules
- Each ingestor: `async def fetch() -> list[dict]` — returns normalized position list
- Every position dict must have: `source`, `lat`, `lon`, `ts_utc`, `callsign`, `type`, `country`
- Handle disconnects gracefully — ingestors must never crash the engine
- Log every fetch with count: `log(f"adsb: {len(results)} positions")`
- Return empty list on failure, never raise

## Delay Policy — NEVER BYPASS
```python
DELAY_CIVILIAN_SEC  = 30      # ADS-B / AIS civilian
DELAY_SENSITIVE_SEC = 86400   # Military-flagged assets, 24h
DELAY_CONFLICT_SEC  = 3600    # Conflict zone event overlays, 1h
```
The `apply_delay()` function in `core/engine.py` is the enforcement point.
**Do not add special cases. Do not add admin bypass. Delays are non-negotiable.**

## Source ID Map (ingestor → DB → frontend must match exactly)
`adsb`, `opensky`, `ais`, `tle`, `notam`, `acled`, `gdelt`, `osint_news`

## WebSocket Protocol
- Server pushes: `{"type": "positions", "data": [...positions]}`
- Server pushes: `{"type": "events", "sources": [...], "data": [...events]}`
- Client sends: `{"type": "subscribe", "domains": [], "bbox": [w,s,e,n]}` — **`domains: []` means all sources** (non-empty list filters to overlapping `sources` only)
- Client sends: `{"type": "unsubscribe"}`

## Output Format for Every Task
1. Files read
2. Plan (brief)
3. Edits made
4. Verification (`py_compile`, map screenshot, curl test)
5. Remaining risks
