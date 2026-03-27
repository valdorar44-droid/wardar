---
name: Wardar Project Overview
description: What Wardar is, full scope, delay policy, data sources, target audience
type: project
---

Wardar is a multi-domain global situational awareness platform — "God's Eye for the open internet."
Fuses public ADS-B aviation, AIS maritime, TLE satellite, and OSINT conflict feeds into one live map.

**Why:** Nobody has fused these public sources into a single product. The data exists. The map doesn't.

**Delay policy (enforced in code):**
- Civilian signals: 30 second delay
- Military-flagged: 24 hour delay
- Conflict overlays: 1 hour delay

**Stack:** FastAPI + SQLite WAL + Leaflet.js SPA + WebSockets. Deploy Railway.

**Phase 1:** Live ADS-B + AIS fused map, WebSocket streaming, deploy
**Phase 2:** Satellite TLE, NOTAM, ACLED conflict events, historical playback
**Phase 3:** Anomaly detection, AI summaries, alerts, Copernicus SAR imagery

**Data sources:** ADS-B Exchange, aisstream.io, CelesTrak, FAA NOTAM, ACLED, GDELT, NewsAPI/Brave

**How to apply:** When working in this workspace, always enforce delay policy. Never add real-time military position tracking. Public data only.

**Full project workspace:** `/home/sean/.openclaw/workspace/wardar/`
**Project doc:** `/home/sean/.openclaw/workspace/docs/wardar.md`
