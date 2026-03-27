# Wardar — Competitive Research & Build Roadmap
_Research conducted 2026-03-27 via deep web search_

## Direct Competitor: World Monitor
- GitHub: MrB4nz4i/worldmonitor-osint (41k stars, AGPL-3.0)
- Stack: TypeScript, Vercel Edge Functions, browser-side rendering
- Data: OpenSky + Wingbits ADS-B, AISStream.io, GDELT, FIRMS fire detection, conflict zones
- AI: Hybrid keyword/LLM pipeline, Country Instability Index (0-100)
- GAPS vs Wardar: No TLE satellite tracking, no GPS jamming layer, no dark vessel detection, NO historical replay, no delay policy, no anomaly ML

## Wardar's Advantages Over World Monitor (already built)
- Satellite TLE positions (470+ satellites live including military birds)
- 24h historical replay with trail polylines
- Enforced delay policy (the only platform with this)
- Python backend (easier to add ML than TypeScript edge functions)

## Key New Feeds to Add (all free)

### NASA FIRMS — fire/thermal anomalies
- Detects artillery fires, missile strikes, burning oil fields
- API: https://firms.modaps.eosdis.nasa.gov/api/
- Under 60 min latency, global coverage, no key required

### USGS Earthquake
- Cross-reference underground explosions, nuclear tests
- API: https://earthquake.usgs.gov/fdsnws/event/1/
- Real-time GeoJSON, free, no key

### GPSJam / GPS Spoofing
- Shows active electronic warfare zones (Kaliningrad, Black Sea, Middle East)
- GPSwise: https://gpswise.aero
- FR24 also publishes daily CSV

### Space-Track.org (upgrade from CelesTrak)
- Official USSF TLE data, 138M+ historical elsets
- Requires free registration: https://www.space-track.org
- Enables debris tracking, conjunction analysis, classified satellite detection

### Submarine Cable Map
- Undersea cable routes as GeoJSON
- API: https://www.submarinecablemap.com/api/v3/cable/cable-geo.json
- No key, instant value

### AISHub
- Free AIS if you share a feed
- API: https://www.aishub.net/api
- JSON/XML/CSV

## AI/ML Build Priority

### 1. Dark Vessel Detection (biggest moat)
- Physics-based first: if AIS speed > ship's known max, it's spoofed
- Gap detection: vessel disappears >2h near sensitive region = alert
- Research: arxiv.org/html/2404.07607v1 (F1=97%)

### 2. ADS-B Anomaly Detection
- Circling pattern = surveillance aircraft
- Transponder off over ocean = incident
- Route deviation from 180-day baseline = alert
- Research: arxiv.org/pdf/1711.10192 (LSTM, 93% accuracy)

### 3. Cross-Domain Convergence Alert
- Military aircraft surge + naval repositioning + ACLED spike in same region = compound alert
- This is the "God's Eye" capability no one else has

### 4. AI Situation Report
- Region → GDELT + AIS + ADS-B + ACLED → Claude Haiku → structured brief
- Already have Anthropic key in config

## Movie Comparisons (what to build toward)

| Movie | Capability | Wardar Equivalent |
|-------|-----------|-------------------|
| God's Eye (F&F) | Track any device globally | ADS-B + AIS + TLE fusion |
| Enemy of the State | Satellite retasking, WAMI | Sentinel SAR pass prediction |
| Person of Interest | Pattern-of-life baselines | 180-day track baseline + deviation |
| Zero Dark Thirty | Historical timeline reconstruction | Replay engine (BUILT) |
| WarGames | Multi-domain threat modeling | Convergence alert engine |

## Moat Summary
1. Transparent delay policy (trust with journalists/researchers)
2. Only platform with multi-domain replay
3. Dark vessel gap detection algorithm
4. Cross-domain compound alerts (aviation + maritime + conflict + jamming)
5. Satellite pass prediction over conflict zones
6. Public API for Bellingcat/Reuters/academics
7. AGPL-3.0 open source (World Monitor too, but Wardar has more features)
