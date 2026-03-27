# DECISIONS.md — Wardar

_Append-only. Never overwrite or delete entries._

---

## 2026-03-26

**Project name:** Wardar
_Decided: Sean. "War radar" — god's eye view platform._

**Stack:** FastAPI + SQLite WAL + Leaflet.js SPA + WebSockets
_Rationale: Mirrors Argus stack Sean knows. Fast to build. SQLite sufficient for MVP; can migrate to PostGIS for geo queries later._

**Delay policy (non-negotiable):**
- Civilian ADS-B/AIS: 30 second delay
- Military-flagged signals: 24 hour delay
- Conflict zone overlays: 1 hour delay
_Rationale: Time delay is the safety control that makes this buildable responsibly. 24h delay makes military data useless for operational purposes but still valuable for analysis._

**Data sources for Phase 1:**
- ADS-B Exchange — aviation, unfiltered
- aisstream.io — maritime AIS via WebSocket
_Rationale: Both are free tier, both are real-time, both are public safety broadcasts._

**Frontend approach:** Single HTML file SPA (like Argus dashboard.html)
_Rationale: No build step, no npm, fast iteration. Leaflet.js for mapping._

**Map aesthetic:** Dark terminal/intelligence style (matching Argus identity)
_Rationale: Sean's aesthetic preference. Consistent brand across projects._

**Scope boundary:**
_Decided: Only public, broadcast signal data. No scraping of classified sources, no proprietary military feeds, no live identification of active military operations. Time-delayed OSINT for conflict context only._
