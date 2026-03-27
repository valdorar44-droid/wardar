---
name: Wardar Roadmap & Pending TODOs
description: All pending tasks, confirmed phases, and feed additions for Wardar
type: project
---

## API Keys Still Needed (blocks live data)
- AIS maritime: https://aisstream.io/authenticate → GitHub OAuth 60s → `railway variables set AISSTREAM_API_KEY=key`
- ACLED conflict: https://acleddata.com/register → `railway variables set ACLED_API_KEY=key ACLED_EMAIL=email`

## Completed Phases
- **Phase 1** ✅ — FastAPI scaffold, OpenSky ADS-B, OSINT/GDELT, WebSocket, Railway deploy
- **Phase 2** ✅ — TLE satellites, 24h timeline/playback, NASA FIRMS thermal, USGS seismic, GPSJam EW

## Active Work
- **UI redesign** — full war-room overhaul: CSS Grid, corner brackets, scan lines, SIGINT feed panel, cursor lat/lon readout, signal pulse animations, threat level header
- **Community Intel layer** — click country/region → community discussion + intel submission + verification that plots to map. Uses `community_reports` DB table, `/api/community` endpoints, anonymous tokens via localStorage

## Planned Phases

### Phase 3 — High-Value Free Feeds (no keys)
Add to osint.py as `_fetch_*_rss()` functions:
- The War Zone: https://www.thedrive.com/the-war-zone/rss
- USNI News: https://news.usni.org/feed
- Bellingcat: https://www.bellingcat.com/feed/
- Oryx OSINT: https://www.oryxspioenkop.com/feeds/posts/default
- Defense News: https://www.defensenews.com/arc/outboundfeeds/rss/
- Defense One: https://www.defenseone.com/rss/
- UK MOD: https://www.gov.uk/search/news-and-communications.atom?organisations[]=ministry-of-defence
- RUSI: https://rusi.org/rss.xml
- CSIS: https://www.csis.org/rss.xml
- Krebs Security: https://krebsonsecurity.com/feed/
- Ransomware.live: https://www.ransomware.live/rss
- gCaptain: https://gcaptain.com/feed/
- Janes public: https://www.janes.com/feeds/news

### Phase 4 — Dark Signal Detection (ML moat)
- Dark vessel detection: AIS gap >2h near conflict zone = alert
- Vessel spoofing: AIS reported speed > physics max for vessel class
- ADS-B anomaly: circling (surveillance), transponder loss over sensitive airspace, route deviation from 30-day baseline
- FIRMS + USGS cross-correlation: thermal + seismic in same region within 4h = possible strike
- GPSJam + AIS convergence: vessel goes dark in active jamming zone = compound alert
- New DB table: `alerts` with source signals, confidence score, resolution

### Phase 5 — Temporal Intelligence
- 180-day position baseline per callsign/MMSI → deviation alerts
- Vessel/aircraft "biography" popup: last 30 days as polyline
- Chokepoint throughput dashboard (Hormuz, Malacca, GIUK, Bab-el-Mandeb)
- Port activity baseline vs current detection

### Phase 6 — Geospatial Enrichment (static layers)
- Submarine cable routes: https://www.submarinecablemap.com/api/v3/cable/cable-geo.json
- EEZ boundaries: MarineRegions.org GeoJSON
- Military installations layer (OSM-derived public data)
- Space-Track.org upgrade (138M+ historical TLE, maneuver detection)
- Sentinel SAR pass prediction over alert regions

### Phase 7 — Intelligence Products (monetization)
- AI SITREP: region → all signals 24h → Claude Haiku → structured brief
- Daily digest: top 10 events ranked by anomaly + cross-domain convergence score
- Watchlist: track specific MMSI/callsign/NORAD/country, fire alerts on anomaly
- Public API v1: Free (24h delayed), Pro (near-realtime), Enterprise (raw + custom regions)
- Webhook delivery: POST alerts to Slack/Discord/PagerDuty
- OSINT report export: region + timeframe → PDF brief with map snapshot + AI synthesis

### Phase 8 — Collaborative Intelligence
- Analyst annotations on map events
- Community confidence voting on anomaly flags
- Shared watchlists for teams
- Open API for Bellingcat/academics (citation required, free tier)

**Why:** Dark signal detection is the primary moat — no competitor has multi-domain correlation. Community intel + verification makes Wardar the "open Bellingcat." Public API enables journalist/academic adoption which drives credibility.

**How to apply:** When building Phase 4, alerts must never bypass the delay policy. Military anomalies still hold 24h. Community-submitted intel goes through same delay rules.
