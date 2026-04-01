---
name: Wardar Roadmap & Pending TODOs
description: All completed phases and next phases (11-18) for Wardar
type: project
---

## API Keys Still Needed (blocks live data)
- AIS maritime: https://aisstream.io/authenticate → GitHub OAuth → `railway variables set AISSTREAM_API_KEY=key`
- ACLED conflict: https://acleddata.com/register → `railway variables set ACLED_API_KEY=key ACLED_EMAIL=email`

## Completed Phases (1–10 + extras)

- **Phase 1** ✅ FastAPI scaffold, OpenSky ADS-B, OSINT/GDELT, WebSocket, Railway deploy
- **Phase 2** ✅ TLE satellites, 24h timeline/playback, NASA FIRMS thermal, USGS seismic, GPSJam EW
- **Phase 3** ✅ All RSS/OSINT feeds (TWZ, USNI, Bellingcat, Oryx, ISW, Al Jazeera, BBC, Telegram, breaking news)
- **Phase 4** ✅ Dark signal detectors: dark vessel, vessel spoofing, transponder loss, FIRMS+USGS, GPSJam compound
- **Phase 5** ✅ Temporal intelligence: position_history, /api/track biography, /api/chokepoints (10 straits, 1h/6h/24h throughput counts), route deviation alerts, chokepoint flow widget
- **Phase 6** ✅ EEZ boundaries (VLIZ), submarine cables (TeleGeography), military installations
- **Phase 7** ✅ Watchlist, daily digest (/api/digest), webhooks (Slack/Discord)
- **Phase 8** ✅ Entity annotations, community reports + voting, shared watchlists, public API v1 at /api/v1/
- **Phase 9** ✅ Mobile-responsive PWA: panels collapse, floating nav, manifest.json + sw.js
- **Phase 10** ✅ NASA GIBS atmospheric tile overlays
- **War-room UI** ✅ CSS Grid, corner brackets, scan lines, SIGINT feed, WW3 Pip-Boy header, threat level, cursor lat/lon
- **Admin dashboard** ✅ Login, stats, config, events, WW3 controls
- **Post-10** ✅ Geofence watchzone, conflict heatmap, OFAC sanctions badges, browser push notifications, live crisis ticker, aircraft trails

## Next Phases (none built yet)

### Phase 11 — Entity Identity Graph (Priority #1, Complexity: L)
Canonical UUID per tracked entity, cross-source identity resolution, alias tracking, unified timeline (positions + alerts + OSINT mentions + annotations). Foundational for phases 12, 14, 15.
- New: `entities` table, `core/entity_graph.py`, `/api/entities/{uuid}/timeline`, entity panel in frontend

### Phase 12 — Incident Rooms (Priority #5, Complexity: XL)
Group events into discrete incidents with lifecycle, spatial bounds, AI assessment every 4h, PDF/JSON export, private shareable links.
- New: `incidents` table, `core/incident_engine.py`, incident overlay on map, auto-created at convergence score 6+

### Phase 13 — API Tiers + Monetization (Priority #2, Complexity: M)
Tiered API keys for users (FREE/PRO/ENTERPRISE), rate limiting middleware, per-customer webhook subscriptions, usage analytics, Stripe hook.
- Note: server.py line 539 already has stub comment "Future: X-API-Key header will unlock real-time tier (currently no-op)"
- New: `api_keys` table, `webhook_subscriptions` table, `core/auth.py`, FastAPI middleware

### Phase 14 — ML Anomaly Detection (Priority #3, Complexity: XL)
Chokepoint throughput baselines (7-day rolling mean/std → anomaly when 2σ deviation), dark swarm detector (3+ vessels dark in same cell within 6h), compound multi-domain spike detector, conflict escalation trajectory → WW3 meter.
- Note: Phase 5 chokepoint COUNTS are built. These BASELINES are not.
- New: `grid_baselines`, `chokepoint_baselines`, `dark_pattern_episodes`, `core/anomaly.py`

### Phase 15 — Conversational Analyst (Priority #4, Complexity: L)
Query the DB in natural language. "What military aircraft were near Taiwan in 48h?" → grounded answer with map citations. Builds on intel_brief.py's _build_context() pattern.
- New: `chat_sessions` table, `core/analyst.py`, `/api/analyst/chat`, Analyst panel in frontend

### Phase 16 — Signal Expansion (Priority #6, Complexity: M per sub)
- 16A: AIS enrichment — vessel metadata, flag/owner mismatch dark signal, ETA vs actual divergence
- 16B: Space domain — SpaceTrack.org, IMINT satellite pass prediction over hotspots, conjunction alerts
- 16C: HF/ACARS — oceanic aircraft positions (fills ADS-B ocean gap)
- 16D: OpenSanctions — replace OFAC-only with 100+ sanctions lists (key exists in config, ENABLE_OPENSANCTIONS=False)

### Phase 17 — UX Overhaul (Priority #7, Complexity: L+M)
Vite build step, URL-encoded shareable map state, split-screen multi-viewport, command palette, theme variants, bearing lines/range rings

### Phase 18 — Scale Infrastructure (Priority #8, do last)
TimescaleDB migration, Redis for rate limiting + WS pub/sub, CDN for frontend assets

## Recommended Build Order
1. Phase 13 (API tiers) — before users arrive, every request gets attributed
2. Phase 11 (Entity graph) — makes AI layer defensible, foundational for 12/14/15
3. Phase 14 minimal (chokepoint baselines + dark swarm) — improves existing map signals
4. Phase 15 MVP (conversational chat) — builds on existing intel_brief.py context builder
5. Phase 12 (incidents), 16A (AIS enrichment), 17 (UX)
6. Phase 18 (only when growth forces it)

## Known Bugs
- **Mobile map not loading** — loading screen shows, map never appears. Under investigation.
