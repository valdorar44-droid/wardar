# Wardar — Full System Audit & Agent Playbook

**Purpose:** Give another agent enough context to **navigate the repo**, **respect invariants**, **find bugs**, and **verify fixes** without rediscovering the architecture from scratch.  
**Root:** `/home/sean/.openclaw/workspace/wardar` (or `…/workspace/wardar`).

---

## 1. Executive summary

| Layer | Role | Key paths |
|-------|------|-----------|
| **API** | FastAPI app, REST, WebSocket, static SPA | `api/server.py` |
| **Engine** | Schedules ingestors, `apply_delay()`, broadcast | `core/engine.py` |
| **Store** | SQLite WAL, migrations, queries | `db/store.py` |
| **Config** | Env-driven settings (delays, keys, intervals) | `config/settings.py` |
| **Ingestors** | One module per feed → positions/events | `ingestors/*.py` |
| **Frontend** | Single-file Leaflet SPA | `dashboard/map.html` |
| **Intel / alerts** | SITREP, dark vessel, convergence, etc. | `core/intel_brief.py`, `core/alerts.py` |
| **E2E audit** | Playwright against local server | `tools/audit.py` |

**Deploy:** Railway — DB default `/data/wardar.db` when `RAILWAY_*` env present (`config/settings.py`).

**Tests:** No `pytest` suite in-repo; verification is **`python -m py_compile`**, **`tools/audit.py`** (needs server on `http://localhost:8080`), and manual map/WebSocket checks per `AGENTS.md`.

---

## 2. Directory structure (verified)

```
wardar/
├── run.py                 # Loads .env (non-overriding), uvicorn api.server:app
├── requirements.txt
├── .env / .env.example    # Local secrets (gitignore .env)
├── AGENTS.md              # Boot sequence, commands, rules
├── WARDAR_CLAUDE_RULES.md # Editing rules (map.html, server.py, delay policy)
├── BUGS.md                # Tracked bugs (append-only; mark fixed with date)
├── decisions.md, goals.md, soul.md, user.md, memory/ …
├── api/
│   └── server.py          # FastAPI, lifespan → engine_start(), routes, WS
├── config/
│   └── settings.py        # All env-overridable config
├── core/
│   ├── engine.py          # apply_delay, ingestor ticks, scheduler, _broadcast
│   ├── intel_brief.py     # AI SITREP
│   └── alerts.py          # Phase-3 alert engine
├── db/
│   └── store.py           # SQLite schema, migrations, CRUD
├── ingestors/             # adsb, ais, tle, notam, acled, osint, osint_geo, …
├── dashboard/
│   └── map.html           # Entire frontend SPA
├── docs/
│   └── competitive_research.md
└── tools/
    └── audit.py           # Playwright full audit (expects BASE localhost:8080)
```

**Note:** `Procfile` appears in some docs/user trees but **was not present** in this workspace snapshot; confirm Railway start command (`run.py` / uvicorn) in deployment config.

---

## 3. Non-negotiable invariants (do not violate)

1. **Delay policy** — Implemented only in `core/engine.py:apply_delay()` using `config/settings.py`:
   - `DELAY_CIVILIAN_SEC`, `DELAY_SENSITIVE_SEC`, `DELAY_CONFLICT_SEC`
   - No bypass flags, no admin exceptions (`engine.py` header + `WARDAR_CLAUDE_RULES.md`).

2. **Logging** — Use `log` / `log_warn` / `log_err` from `core/engine.py`; do not swallow exceptions in ingestors (return `[]` on failure where appropriate, but log).

3. **Frontend security** — `WARDAR_CLAUDE_RULES.md`: sanitize user/HTML in `map.html` (`esc()` pattern).

4. **DB migrations** — New columns/tables: append to `_MIGRATIONS` in `db/store.py` (idempotent patterns).

---

## 4. Runtime flow (for debugging)

1. **Startup:** `uvicorn api.server:app` → `lifespan` → `engine_start()` → `DB.get_conn()`, optional AIS WS listener, ~20 `asyncio.create_task(_run_every(...))` loops.

2. **Ingest path:** Ingestor `fetch()` → `_save_positions` / `_save_events` → `apply_delay` on each row → `upsert_*` → optional `_broadcast` JSON to WebSocket clients.

3. **Read path:** `/api/positions`, `/api/events` use **released** rows (`release_ts_utc <= now` logic in store helpers).

4. **WebSocket:** Clients registered via `register_ws_client` in `api/server.py`; payload shapes `{type, sources?, data}`.

---

## 5. API surface (quick reference)

| Area | Examples |
|------|----------|
| Health | `GET /api/health` — counts + `status` |
| Data | `GET /api/positions`, `GET /api/events` — bbox, sources, limit |
| Playback | `GET /api/playback/summary`, `GET /api/playback/frame` |
| Community | `GET/POST /api/community`, votes, upload |
| Layers | `GET /api/layers/{layer}` — nuclear, cables, mil_bases, pipelines, … |
| Chat | `GET/POST /api/chat/{country}` |
| Intel | `GET /api/brief`, `POST /api/brief/generate` |
| Static | `GET /` serves `dashboard/map.html`; `/static` → `dashboard/` |

Full list: read `api/server.py`.

---

## 6. Ingestor registry (engine `start()`)

Scheduled in `core/engine.py` `start()`: ADS-B, AIS drain, TLE, NOTAM, ACLED, OSINT/GDELT cadence, FIRMS, USGS, GPSJam, purge, static layers, OSINT geo, UNHCR, VIEWS, dark vessel, convergence, proximity, Pikud Haoref, Wikipedia spikes, Polymarket, Shodan, IODA, intel brief — each gated by `ENABLE_*` / API keys in `config/settings.py`.

---

## 7. How an agent should **find and fix bugs**

### 7.1 Before coding

1. Read **`AGENTS.md`** (boot + commands).
2. Read **`BUGS.md`** — add or update entries (never delete history).
3. Read **`WARDAR_CLAUDE_RULES.md`** if touching `api/server.py` or `dashboard/map.html`.
4. Locate subsystem: API → `server.py`; persistence → `db/store.py`; scheduling/delay → `engine.py`; feed → `ingestors/<name>.py`.

### 7.2 Reproduce

```bash
cd /path/to/wardar
# Optional: copy .env.example → .env and fill keys
python run.py
# or: uvicorn api.server:app --host 0.0.0.0 --port 8080 --reload
```

- Reproduce via **curl** (`/api/health`, `/api/positions?limit=10`) or **browser** + DevTools WebSocket.
- For frontend: hard-refresh `map.html` (cache).

### 7.3 Verify backend edits

```bash
python -m py_compile api/server.py core/engine.py db/store.py
# Broader (AGENTS.md):
# find . -name "*.py" -not -path "./venv/*" | xargs python -m py_compile
```

### 7.4 E2E smoke (optional)

With server on **port 8080**:

```bash
pip install playwright  # if not installed
python tools/audit.py
```

`tools/audit.py` checks health, positions/events APIs, noise heuristics, Polymarket filter, `/api/brief`, and UI-oriented flows. Adjust `BASE` at top if port differs.

### 7.5 After fix

1. Update **`BUGS.md`** with **Fixed: YYYY-MM-DD** under the entry.
2. Append non-obvious decisions to **`decisions.md`** if needed.

---

## 8. Risk notes (static review)

| Topic | Note |
|-------|------|
| **CORS** | `allow_origins=["*"]` in `api/server.py` — OK for many deployments; tighten if exposing sensitive ops. |
| **SECRET_KEY** | Default `change-me-in-production` in settings — override in prod. |
| **requirements.txt** | Does not pin `playwright`; audit script is dev tooling. |
| **WARDAR_CLAUDE_RULES** | Mentions position fields `ts_utc`; engine uses **`raw_ts_utc`** / **`release_ts_utc`** — align docs when editing ingestors. |
| **WebSocket message types** | Rules file shows `type: "event"`; engine often uses `"events"` — confirm `map.html` subscribers when debugging WS. |

---

## 9. Bug triage checklist

- [ ] API 500? Trace route in `server.py`, then `store.py` function.
- [ ] Empty map? Keys (`ENABLE_*`), API errors in logs, `release_ts_utc` (delay may hide fresh data).
- [ ] Ingestor silent? Look for `log_err` in engine tick; run ingestor `fetch()` in `asyncio.run` manually (`AGENTS.md` one-liner pattern).
- [ ] WS not updating? `register_ws_client`, `_broadcast`, client disconnect cleanup.
- [ ] SQLite locked? WAL + `_lock` in `store.py` — avoid long transactions.

---

## 10. Files another agent should read first

1. `AGENTS.md`  
2. `BUGS.md`  
3. `WARDAR_CLAUDE_RULES.md`  
4. `core/engine.py` (apply_delay + start)  
5. `api/server.py` (lifespan + routes)  
6. `db/store.py` (schema + query helpers)  
7. `config/settings.py` (env names)  
8. `tools/audit.py` (E2E expectations)

---

*End of audit document. Update this file when major subsystems or deploy layout change.*
