# AGENTS.md — Wardar Operating Manual

_Read this on every session start. This is the boot sequence._

---

## Boot Sequence

On every session start:
1. Read `soul.md` — who I am in this workspace
2. Read `user.md` — who Sean is and how he works
3. Read `memory/MEMORY.md` — what persists (index only, load files if needed)
4. Read `goals.md` — what phase we're in and what's next
5. Read `decisions.md` — what's already been decided (don't re-litigate)
6. **Search memory before acting** — never guess what was decided before

---

## What Is Wardar

Wardar is a multi-domain, real-time global situational awareness platform.
God's Eye for the open internet — fusing public signal feeds into one live map.

**Domain:** Aviation (ADS-B) + Maritime (AIS) + Satellites (TLE) + Conflict events (OSINT)
**Philosophy:** 100% public data sources. Time-delayed where sensitive. Never operational intel.
**Delay policy:** Military-adjacent signals delayed minimum 24h. Civilian signals near-real-time.
**Audience:** Journalists, researchers, NGOs, supply-chain analysts, crisis monitors, aviation/maritime watchers.

---

## Rules

### Must Do
- Check `BUGS.md` before fixing anything
- Follow `WARDAR_CLAUDE_RULES.md` when editing server.py or map.html
- Write important decisions to `decisions.md` before ending session
- Run `python -m py_compile <file>.py` after every backend edit
- Test WebSocket connections after any ingestor change

### Must Not Do
- Add real-time tracking of identified military assets without delay
- Store raw position streams longer than retention policy allows
- Re-explain things Sean already knows — move fast
- Swallow errors silently — log everything

### Delay Policy (non-negotiable)
- `DELAY_CIVILIAN_SEC = 30`    — ADS-B/AIS civilian, 30 second delay
- `DELAY_SENSITIVE_SEC = 86400` — Military-adjacent signals, 24 hour delay
- `DELAY_CONFLICT_SEC = 3600`   — Active conflict zone overlays, 1 hour delay
- All delay values live in `config/settings.py` — never hardcode them

---

## Project Quick Reference

| Field | Value |
|-------|-------|
| **Stack** | FastAPI + SQLite WAL + Leaflet.js SPA + WebSockets |
| **Deploy** | Railway (auto-deploy from master) |
| **DB** | SQLite at `/data/wardar.db` on Railway volume |
| **Key files** | `core/engine.py`, `api/server.py`, `dashboard/map.html`, `db/store.py` |
| **Live domains** | TBD — set when Railway project created |

## Key Commands

```bash
# Run locally
uvicorn api.server:app --host 0.0.0.0 --port 8080 --reload

# Test an ingestor manually
python -c "from ingestors.adsb import fetch; import asyncio; print(asyncio.run(fetch()))"

# Syntax check all Python
find . -name "*.py" | xargs python -m py_compile

# Watch logs
railway logs --tail
```

## Data Source Registry

| ID | Name | Feed | Latency | Delay Policy |
|----|------|------|---------|-------------|
| `adsb` | ADS-B Exchange | REST/WS | 2-5s | 30s civilian, 24h military-flagged |
| `opensky` | OpenSky Network | REST | 5-10s | 30s civilian |
| `ais` | aisstream.io | WebSocket | <60s | 30s civilian, 24h military-flagged |
| `tle` | CelesTrak | REST | Hours | None (orbital mechanics) |
| `notam` | FAA/EUROCONTROL | REST | Minutes | None (public safety) |
| `acled` | ACLED API | REST | Daily | 1h |
| `gdelt` | GDELT | REST | 15min | 1h |
| `osint_news` | NewsAPI/Brave | REST | Minutes | 1h |

## Memory Conventions

- `memory/MEMORY.md` — index only, points to files
- `memory/*.md` — specific topic files
- `decisions.md` — append-only log, never overwrite
- `goals.md` — current phase + next actions
- `BUGS.md` — tracked bugs, mark fixed with date

**Rule:** If it's not in a file, it doesn't exist after context compaction.
