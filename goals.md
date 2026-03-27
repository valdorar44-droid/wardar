# GOALS.md — Wardar Current Objectives

_Updated when phase shifts._

---

## Phase 1 — Live Map (CURRENT)

### Blocking
- [ ] Scaffold FastAPI backend + SQLite schema
- [ ] ADS-B Exchange ingestor (aircraft positions)
- [ ] aisstream.io ingestor (ship positions)
- [ ] Delay queue — 30s civilian, 24h military-flagged
- [ ] WebSocket endpoint `/ws/positions` — streams live positions to browser
- [ ] Leaflet.js map SPA — dark tiles, aircraft + ship icons, popups
- [ ] Deploy to Railway

### Done
- [x] Project workspace created (2026-03-26)
- [x] Boot sequence files created (2026-03-26)
- [x] Architecture decided (2026-03-26)

---

## Phase 2 — Intelligence Layers

- [ ] CelesTrak TLE satellite tracker overlay
- [ ] NOTAM / airspace closure layer
- [ ] ACLED conflict events overlay
- [ ] GDELT news event clustering
- [ ] Region situation cards ("Red Sea shipping risk")
- [ ] Historical playback (last 24h, 7d, 30d)

---

## Phase 3 — God's Eye Polish

- [ ] Anomaly detection ("vessel went dark near conflict zone")
- [ ] AI summary layer — regional synthesis from public OSINT
- [ ] Alerts — user-defined (country, region, asset type)
- [ ] Copernicus/Sentinel SAR imagery integration
- [ ] Pattern analysis — historical movement trends
- [ ] Mobile-responsive PWA

---

## Revenue (Phase 2+)

- Free tier: live map, 24h delay on all data
- Pro: near-real-time civilian feeds, alerts, playback
- Enterprise/API: raw feed access, custom regions, white-label

---

_Last updated: 2026-03-26_
