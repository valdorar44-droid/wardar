# Wardar — Competitive Research & Build Intelligence
_Deep research conducted 2026-03-26_

---

## 1. DIRECT COMPETITORS

### 1.1 World Monitor (worldmonitor.app)

**What it is:** The closest existing competitor. A real-time global intelligence dashboard — open source, actively maintained, 41k+ GitHub stars as of early 2026.

**GitHub:** https://github.com/koala73/worldmonitor (AGPL-3.0, non-commercial self-hosting allowed; commercial use requires paid license)

**Features:**
- 3D WebGL globe (globe.gl + Three.js) AND flat 2D map (deck.gl + MapLibre GL)
- 45 map layers, 435+ curated data sources
- Country Intelligence Index — composite risk scoring across 12 signal categories
- Finance radar covering 92 stock exchanges, commodities, and crypto
- AI-synthesized overnight briefings with cross-stream signal correlation
- 21-language support including RTL
- 5 site variants from one codebase: World, Tech, Finance, Commodity, Happy
- Native desktop app via Tauri 2 (Windows/macOS/Linux)
- Offline AI via Ollama (local model inference)
- Model Context Protocol (MCP) — lets Claude, GPT, or custom LLMs use World Monitor as a tool
- Maritime AIS ship monitoring (via AISStream)
- Flight tracking (via Wingbits — decentralized DePIN ADS-B network)
- NASA FIRMS fire/thermal detection
- Infrastructure mapping (220+ military bases, 111 AI datacenters, 88 pipelines, 55 undersea cables)
- Pro: Slack/Telegram/WhatsApp/email delivery, equity research, geopolitical analysis frameworks
- Enterprise: API access, Android TV, MCP for AI agents, team shared workflows

**Tech stack:** Vanilla TypeScript, Vite, Vercel Edge Functions (60+), Railway relay, Redis/Upstash 3-tier caching, Protocol Buffers (92 definitions, 22 services), PWA

**Data sources:** Wingbits ADS-B, AISStream.io, GDELT, ACLED, UCDP, NASA FIRMS, OpenSky, FRED, Finnhub

**Pricing:** Free tier (435+ sources, 45 layers, BYOK AI). Pro (waitlisted, subscription). Enterprise (custom).

**Target audience:** Investors, energy/commodities traders, researchers, journalists, government

**Wardar gaps vs World Monitor:**
- No TLE satellite tracking (no orbital layer)
- No GPS jamming/spoofing detection layer
- No dark vessel detection or AIS gap analysis
- No multi-domain historical replay
- No enforced delay policy (World Monitor shows near-real-time military data without disclosure)
- No anomaly detection or pattern-of-life analysis
- No cross-domain convergence alerts
- TypeScript + edge functions = harder to add Python ML models

---

### 1.2 LiveUAmap (liveuamap.com)

**What it is:** The original conflict mapping platform. Started as Ukraine-focused, now global. Built by Ukrainian developer team. Dominant brand in OSINT conflict mapping.

**Features:**
- Global coverage with dedicated regional conflict maps (Ukraine, Israel-Palestine, Syria, Venezuela, etc.)
- Event type filtering: military actions, protests, government decisions, natural disasters
- Timeline scrubbing — view events at any point in history
- AI web crawlers feeding to human analysts for fact-checking
- Mobile apps (iOS/Android)
- REST API with examples in multiple programming languages

**Data sources:** Proprietary AI web crawlers, social media, news outlets, direct reports, human analysts

**Pricing:** Free basic access. Pro tier (paywall for API and advanced features)

**Target audience:** Journalists, researchers, conflict analysts, government

**API:** Yes — available, documented, language examples provided

**Wardar gaps vs LiveUAmap:**
- No aviation/maritime fusion
- No satellite orbital layer
- Events are manually curated (slower, more accurate); Wardar can be automated
- No anomaly detection
- No infrastructure layer

**What Wardar should steal:** Regional conflict map concept, event timeline scrubbing, mobile-first responsive design

---

### 1.3 ISW (Institute for the Study of War) Maps

**What it is:** The gold standard for Ukraine/Iraq/Syria conflict analysis. Used by NATO, Pentagon, and every major newsroom. High-credibility, manually produced.

**URL:** https://www.iswresearch.org / ArcGIS Story Maps

**Features:**
- Daily static + interactive ArcGIS maps (Ukraine front lines, Kursk incursion, etc.)
- High-fidelity street-level accuracy on control-of-terrain
- Layer toggle (equipment, supply routes, fighting areas)
- Timeline playback of historical control changes
- Daily written assessment paired with maps
- Iran threat map (in partnership with AEI's Critical Threats Project)
- Myanmar conflict map (via IISS)

**Data sources:** ISW research team, military analysts, intelligence specialists, regional experts, geolocated open source evidence

**Pricing:** Free, nonprofit-funded

**API:** None public. Data not machine-readable. ArcGIS format but locked behind their platform.

**Wardar gaps vs ISW:**
- ISW produces narrative analysis; Wardar can't replicate the human expertise
- ISW is static/manual; Wardar is real-time automated

**What Wardar should do differently:** ISW is the reference for accuracy. Wardar should ingest ISW's daily map annotations as an optional layer (their GeoJSON is exported via ArcGIS). Wardar provides the live signal layer on top of ISW's authoritative front-line polygons.

---

### 1.4 DeepStateMap (deepstatemap.live)

**What it is:** Most-watched Ukraine war map. 900k daily views at peak (Aug 2025). Over 1 billion total views by Feb 2024. Made by anonymous Ukrainian analysts ("Pohorilyi" et al).

**Features:**
- Interactive front-line map with polygon shading for control zones
- News feed integration
- Distance measurement tool
- Draw-on-map annotation layer
- Offline mode
- Mobile apps (iOS/Android), 4.5+ star ratings
- Historical front-line change replay

**Data sources:** Ministry of Defense of Ukraine, geolocated social media photo/video, Ukrainian soldiers (hundreds of direct sources), other verified Ukrainian sources

**API:** Yes, but restricted. They introduced individual client keys after Polymarket used their API without permission to create betting markets on territory control.

**Pricing:** Free, donation/grant supported

**Target audience:** Public, Ukrainian military, journalists, diaspora

**Wardar gaps vs DeepStateMap:**
- No aviation or maritime layer
- Ukraine-only (not global)
- No automated signal ingestion

**What Wardar should do:** For Ukraine specifically, ingest DeepStateMap front-line polygon data (if API access approved) as a conflict zone layer. Their polygons are the most accurate available for Ukraine.

---

### 1.5 GeoConfirmed (geoconfirmed.org)

**What it is:** Volunteer-driven OSINT geolocation project. Verifies photos/videos from conflict zones to precise coordinates using satellite imagery, Mapillary, Sentinel Hub, Google Earth.

**Features:**
- Geolocated and verified conflict events (missile strikes, troop movements, infrastructure damage)
- ORBAT (order of battle) databases for military unit structures
- QGIS plugin for GIS integration: `geoconfirmed_qgis`
- Regional sections: Ukraine, Syria, Yemen, Myanmar, etc.
- All geolocations include visual evidence + reasoning for independent verification
- Crowdsourced model — volunteers double-check each other

**API:** No public API, no subscription. Data accessible via QGIS plugin and website.

**Data format:** GeoJSON via QGIS plugin (queryable)

**Key library:** `osint-geo-extractor` (PyPI) — Python lib that pulls from Bellingcat, GeoConfirmed, Cen4InfoRes, DefMon3, Texty.org.ua and returns unified GeoJSON

```python
# Install: pip install osint-geo-extractor
from geo_extractor import get_geoconfirmed_data, get_bellingcat_data
```

**Wardar action:** Integrate `osint-geo-extractor` directly. This gives Wardar 5 OSINT verification databases in one pull, returned as GeoJSON.

---

### 1.6 Global Conflict Awareness (globalconflictawareness.com)

**What it is:** Newly launched (Feb 2026) real-time OSINT dashboard, triggered by US-Israel-Iran conflict. MIT-licensed.

**Features:**
- 89 OSINT feeds: 26 Telegram channels + 63 RSS feeds (CENTCOM, IDF, AP, Reuters, Al Jazeera, BBC, CNN, France 24, Tasnim, Fars News, IRNA, Press TV, Al Mayadeen)
- Auto-updated every 5 minutes
- NLP-based event detection against 150+ target database
- Color-coded faction markers with pulsar animation
- Live TV embed (Al Jazeera, France 24, DW News) inside the map
- Strike statistics overlay (counts by attacking side)
- User-submitted incident reports with auto-geolocation
- Multi-conflict switching (Iran, Ukraine, Taiwan)
- 11-language interface
- Daily newsletter with country-focus customization

**Data format:** JSON (MIT-licensed data distribution)

**Pricing:** Free

**Wardar gaps vs GCA:** GCA has no aviation/maritime/satellite layer. Very new, single-developer project.

**What Wardar should steal:** Live TV embed inside map dashboard, faction-based color coding, user-submitted incident reporting

---

### 1.7 Echosec by Flashpoint

**What it is:** Enterprise OSINT platform. Acquired by Flashpoint (dark web intelligence firm). The professional-grade commercial competitor.

**Features:**
- Real-time geospatial social media intelligence
- Posts from mainstream social, fringe networks, Telegram, news, discussion forums
- Digital perimeter ("geofence") analysis — capture chatter around any area of interest
- Highly customizable alerts (keyword + location + source + threshold)
- Alert types: New Results, Results Summary, Proximity Alerts, AI Summary, Webhooks
- Dark web intelligence integration (Flashpoint's core product)
- Physical threat intelligence layer

**Data sources:** Mainstream social media, Telegram, dark web forums, news outlets, fringe networks

**Pricing:** Enterprise only. No public pricing. Institutional budgets required.

**Target audience:** Government agencies, law enforcement, corporate security, military intelligence

**Wardar opportunity:** Echosec's geofence + Telegram monitoring is locked behind enterprise pricing. Wardar can provide a free/pro tier version of geospatial social monitoring using public Telegram feeds (channels publish publicly) + GDELT.

---

### 1.8 Crisis24 (GardaWorld)

**What it is:** Enterprise travel risk and critical event management platform. Owned by GardaWorld security firm.

**Features:**
- Security assessments for 200+ countries, 800+ provinces, 400+ cities
- Rated across 27 threat categories on 0.25-increment scale
- Geo-located threat zones down to street level
- Two-way mass communications during crises
- Travel risk intelligence for 1,400+ destinations
- Real-time risk intelligence — "always-on" monitoring
- 2025 Global Risk Forecast report (annual)

**Pricing:** Enterprise, institutional. Not public.

**Target audience:** Corporations with mobile workforces, security directors, travel managers

**Wardar opportunity:** The "threat score per city/region" concept is valuable. Wardar can replicate a lightweight version using ACLED density + GDELT tone + NASA FIRMS + earthquake data as inputs to a per-region risk score. This is the Country Intelligence Index equivalent ISW doesn't provide.

---

### 1.9 CrisisWatch (International Crisis Group)

**What it is:** Monthly global conflict tracker maintained by ICG, a highly credible NGO. Non-partisan, field-based reporting.

**Features:**
- Tracks 70+ active conflicts monthly
- 50+ "standby" situations (watching for escalation)
- Historical entries dating back to 2003 (searchable)
- "On the Horizon" — 3-6 month early warning forecasts
- Monthly escalation/deterioration ratings per conflict

**Pricing:** Free, grant-funded

**API:** None public. Manual download only.

**Wardar action:** Scrape/parse the CrisisWatch monthly dataset and use conflict status ratings as a layer overlay. Their 3-6 month horizon alerts are unique and could be a Wardar "Early Warning" card feature.

---

## 2. FLIGHT AND MARITIME TRACKING PLATFORMS

### 2.1 ADS-B Exchange (adsbexchange.com)

**What it is:** The only major flight tracker that does NOT filter military/government aircraft. Critical for conflict monitoring.

**Key differentiator:** Shows military aircraft, FAA blocklist aircraft, state aircraft, VIP aircraft that all other platforms hide.

**API:**
- REST API: aircraft within radius, by Mode-S hex, by callsign
- Enterprise API: live global in-memory array of all aircraft
- RapidAPI: low-cost self-serve tier (approved for personal projects/apps)
- Historical data: available

**Data:** ADS-B + Mode-S + MLAT, global receiver network

**Pricing:** Free tier via RapidAPI (rate-limited). Enterprise (paid, custom).

**Wardar action (Phase 1 BLOCKING):** Use ADS-B Exchange REST API for all aircraft. Do NOT use Flightradar24 or FlightAware (they filter military). Apply 30s delay civilian, 24h delay military-flagged.

---

### 2.2 OpenSky Network (opensky-network.org)

**What it is:** Academic-grade free ADS-B network. Best for research use.

**API:**
- REST API: state vectors, flights, tracks
- Python and Java bindings
- SQL-like query interface (Trino/Minio) for university-affiliated researchers
- Fully free for academic/government use
- CC BY 4.0 licensed data

**Note (2026):** Token-based auth introduced Feb 2026. Free but requires registration + token.

**Wardar action:** Use OpenSky as backup/cross-validation to ADS-B Exchange. Their bulk historical data (SQL interface) is useful for building movement baselines for anomaly detection.

---

### 2.3 Wingbits (wingbits.com)

**What it is:** Decentralized DePIN (Decentralized Physical Infrastructure Network) ADS-B network. Rewards contributors in $WINGS tokens. 5,500+ receivers across 120+ countries.

**Features:**
- Self-serve API dashboard — no onboarding, instant access
- 1-month free trial: 20,000 requests, 5-second refresh, 150 NM range
- Plans to launch satellite cross-check with Spire Global (SpaceX Transporter-13)
- Used by World Monitor as their primary flight data source

**Pricing:** Self-serve paid tiers after trial

**Wardar note:** Wingbits is the most modern ADS-B API. World Monitor already uses it. Consider as alternative to ADS-B Exchange for civilian data; keep ADS-B Exchange for military/unfiltered.

---

### 2.4 Flightradar24 / FlightAware

**Conflict context:** Flightradar24 hit 20 million visits in a single day during US-Israel-Iran strikes (Feb 2026). Site traffic quadrupled. It became a primary real-time awareness tool for the public.

**Military tracking limitation:** Both filter most military aircraft. Not suitable as Wardar's primary source.

**Wardar use case:** FR24 publishes daily GPS spoofing/jamming CSVs — this IS useful for Wardar's GPS jamming detection layer.

---

### 2.5 AISStream.io (aisstream.io)

**What it is:** Free WebSocket API for real-time global AIS ship tracking.

**API:**
- WebSocket endpoint: `wss://stream.aisstream.io/v0/stream`
- JSON subscription with bounding box filtering
- Filter by MMSI or AIS message type
- Free with API key registration
- Can track search and rescue aircraft at sea, maritime accidents

**Wardar action (Phase 1 BLOCKING):** Already in plan. Implement WebSocket consumer with bounding box subscriptions for high-value regions (Red Sea, Black Sea, Strait of Hormuz, South China Sea, Baltic).

---

### 2.6 MarineTraffic / VesselFinder

**MarineTraffic:** 550,000+ vessel database. Paid API (credit-based). Extensive port call data, vessel particulars. Now operated by Kpler (data analytics firm).

**VesselFinder:** Free tier available. API in JSON/XML, credit system. Real-time AIS positions + voyage data.

**AISHub (aishub.net):** Free AIS if you contribute a receiver feed. JSON/XML/CSV. Good backup source.

**Military vessel limitation:** Military ships can (and do) disable AIS. Dark vessel tracking requires detecting ABSENCE of signal plus cross-referencing with satellite SAR imagery.

**Wardar strategy:** Primary = AISStream.io (free, WebSocket). Backup = AISHub (free if feeding). MarineTraffic/VesselFinder for vessel identity/particulars lookup only.

---

## 3. SATELLITE IMAGERY PLATFORMS

### 3.1 Copernicus / Sentinel Hub

**What it is:** ESA's free satellite imagery program. Sentinel satellites provide 10m resolution optical (Sentinel-2) and SAR (Sentinel-1).

**Access:**
- Copernicus Data Space Ecosystem: https://dataspace.copernicus.eu/
- Sentinel Hub REST APIs: process imagery, statistical analysis, custom scripts
- Free tier: Sentinel data only (EO Browser discontinued Feb 2025, replaced by Copernicus Browser)
- Paid tier (Planet Insights Platform): commercial satellite data from €25/month

**Resolution:** 10m (Sentinel-2 optical), 5-20m (Sentinel-1 SAR). Cannot distinguish individual people/vehicles — only buildings and larger structures.

**Conflict monitoring use cases:** Infrastructure damage assessment, refugee camp monitoring, military installation changes, ship detection in ports

**Wardar Phase 3 action:** Integrate Copernicus Browser links as "view this region in satellite imagery" deep links. For the satellite SAR pass prediction feature — use Sentinel-1's orbital schedule to tell users when the next SAR pass will image a given conflict zone.

---

### 3.2 Maxar / Planet Labs (commercial)

**Pricing:** VHR archive imagery: $15-30/km². New tasking: $40-60/km² and up.

**Conflict use:** Maxar used by Pentagon; Planet Labs used for Ukraine ISR throughout conflict.

**Wardar relevance:** Too expensive for integration except for partnership/enterprise tier. Use Copernicus for free layer; offer Maxar/Planet access as enterprise add-on.

---

### 3.3 NASA FIRMS (Fire Information for Resource Management System)

**What it is:** Near-real-time satellite fire/thermal hotspot detection globally.

**API:** https://firms.modaps.eosdis.nasa.gov/api/
- Free API key required (free registration)
- Rate limit: 5,000 transactions per 10-minute interval
- Ultra Real-Time (URT) data: under 60 seconds for US/Canada
- Global coverage: MODIS + VIIRS sensors

**Conflict monitoring application:** Artillery fires produce thermal signatures. Burning oil fields. Missile impact fires. Industrial explosion hotspots. When correlated with ACLED events in same region, FIRMS data becomes a near-real-time artillery detector.

**Wardar action (Phase 2):** Already in plan. High priority — this is the most underutilized public signal for conflict monitoring. Filter to known conflict zone bounding boxes to reduce noise.

---

## 4. CONFLICT EVENT DATA SOURCES

### 4.1 ACLED (Armed Conflict Location & Event Data)

**What it is:** The highest-quality conflict event database. Near-real-time political violence and protest data worldwide.

**API:** https://acleddata.com/acled-api-documentation
- Free with myACLED account registration
- Authentication: Cookie-based or OAuth token
- Data: event type, location (lat/lon), actors, fatalities, date, source
- Tools: CAST (6-month conflict forecast), Early Warning Dashboard, Conflict Exposure Calculator

**Coverage:** Global. Events coded to city/district level.

**Wardar action (Phase 2):** Already in plan. Key endpoint: `/acled/read` with filters for country, event type, date range. Use as the primary conflict event overlay layer. Display as heatmap + individual event markers.

**CAST integration:** ACLED's 6-month conflict forecast (CAST endpoint) gives Wardar a predictive layer — show not just where conflict is happening but where it's predicted to escalate.

---

### 4.2 GDELT (Global Database of Events, Language, and Tone)

**What it is:** Monitors world's news in 100+ languages. 300+ event categories. Updated every 15 minutes.

**API:** https://www.gdeltproject.org/
- DOC 2.0 API: full-text news search, JSON/JSONP output
- Stability Dashboard API: 15-minute live stability timeline, national/ADM1 level
- BigQuery integration for large-scale analysis
- Python wrapper: `gdelt` on PyPI

**Coverage:** Global. 1979 to present. 15-minute update cycle.

**Wardar action:** Use GDELT Stability API to show per-country/region instability scores as a background heatmap. Use DOC 2.0 API to surface breaking news articles when user clicks a region. This is the "news context" layer.

---

### 4.3 UCDP (Uppsala Conflict Data Program)

**What it is:** Academic gold standard for conflict data. Free, CC BY 4.0. Partner with PRIO.

**API:** https://ucdp.uu.se/apidocs/
- As of Feb 2026: token required (free, apply with brief project description)
- Datasets: UCDP/PRIO Armed Conflict, Dyadic, Battle Deaths, Non-State Conflict, One-Sided Violence

**Wardar use:** Best for historical conflict analysis and baseline building, not real-time monitoring. Good for the "historical context" panel when user investigates a region.

---

### 4.4 VIEWS (Violence Early-Warning System)

**What it is:** Open-source AI conflict forecasting. Predicts armed conflict 1-36 months ahead. Developed by PRIO + Uppsala University.

**API/Data:** Interactive forecasting tool + downloadable datasets + API
- Monthly forecasts, country + sub-national level
- Resolution: 0.5° grid (55 sq km at equator)
- Latest model: Fatalities003 (deployed Nov 2025)
- Data: Africa + Middle East at sub-national level; global at country level
- Forecasts available through Dec 2028

**Wardar opportunity:** Integrate VIEWS conflict probability forecasts as a "risk heatmap" layer. This is a unique differentiator — no other mapping platform displays VIEWS data. Show where violence is predicted to increase over next 3/6/12 months.

---

## 5. INFRASTRUCTURE DATA SOURCES (Free/Open)

### 5.1 Nuclear Facilities

**IAEA PRIS (Power Reactor Information System):**
- https://pris.iaea.org/
- Comprehensive: all reactors worldwide, status, location, operator, reactor type, capacity
- Integrated mapping system included
- Web-scrappable; no formal API documented

**Global Nuclear Power Tracker (Global Energy Monitor):**
- https://globalenergymonitor.org/projects/global-nuclear-power-tracker/
- Free download: CC BY 4.0
- September 2025 release
- Includes: operating, under construction, planned, cancelled reactors
- New: microreactor category added
- GeoJSON available via: https://github.com/open-energy-transition/gem_per_country

**GeoNuclearData (GitHub):**
- https://github.com/cristianst85/GeoNuclearData
- Simple lat/lon CSV/JSON database of nuclear power plants worldwide
- Easiest format for Wardar ingestor

**DOE International Energy Data Nuclear API:**
- https://catalog.data.gov/dataset/international-energy-data-nuclear-application-programming-interface-api
- Country-level nuclear capacity and generation data

**Wardar action:** Ingest GeoNuclearData as static layer. Overlay with NASA FIRMS to detect thermal anomalies near nuclear facilities. This is the "nuclear facility monitoring" feature that no platform currently offers.

---

### 5.2 Oil & Gas Infrastructure

**Global Energy Monitor Oil Infrastructure Tracker (GOIT):**
- https://globalenergymonitor.org/projects/global-oil-infrastructure-tracker/
- All global crude oil + NGL transmission pipelines
- Interactive map + downloadable table
- Free

**Global Gas Infrastructure Tracker (GGIT):**
- https://globalenergymonitor.org/projects/global-gas-infrastructure-tracker/
- Natural gas pipelines + LNG terminals worldwide
- Free

**OGIM via Google Earth Engine:**
- ~6.7 million infrastructure features
- 4.5 million oil/gas wells + 1.2 million km of pipelines
- Free for research/education/nonprofit via GEE

**EIA Open Data (US):**
- https://www.eia.gov/opendata/
- Free with API key registration
- US-focused but has international datasets

**OpenStreetMap (pipeline tag):**
- `pipeline=*` and `man_made=pipeline` tags
- Free, community-maintained, globally inconsistent quality

**Wardar action:** Load GOIT + GGIT pipeline data as static GeoJSON layer. Cross-reference with ACLED events and NASA FIRMS to auto-highlight infrastructure under threat.

---

### 5.3 Military Bases

**USDOT Military Bases Dataset (DoD-sourced):**
- https://catalog.data.gov/dataset/military-bases1
- US DoD official, FY2024 data, updated Nov 2025
- Formats: GeoJSON, Shapefile, CSV, KML, GeoPackage, SQLite
- US and overseas DoD installations
- Free, no restrictions

**HKU Overseas Military Bases Dataset:**
- https://datahub.hku.hk/articles/dataset/Overseas_Military_Bases/20438805
- Eight great powers: US, China, Russia, UK, Japan, India, UAE, France
- Tabular + geospatial, through Nov 2020

**OpenStreetMap Military Layer:**
- `landuse=military`: 70,641+ mapped objects globally
- `military=*` tags: airbases, barracks, training areas, ranges, bunkers
- Query via Overpass API: `[out:json]; node[military]; out;`
- WARNING: OSM military data is contested — some Ukrainian mappers removed data after airstrikes followed OSM edits

**Wardar action:** Combine USDOT (US/allies) + OSM Overpass query (global, filter obvious errors) as military base layer. This is already partially in plan (220 bases in World Monitor).

---

### 5.4 Submarine Cable Infrastructure

**Submarine Cable Map API:**
- https://www.submarinecablemap.com/api/v3/cable/cable-geo.json
- Free, no key required
- Returns GeoJSON of all global submarine cable routes

**Already in existing competitive_research.md plan.** Priority: high, effort: 30 minutes.

---

### 5.5 Buildings (Global)

**Overture Maps Foundation:**
- 2.3 billion global buildings in GeoParquet format
- CC BY 4.0 license
- Hosted on AWS S3 and Azure BLOB (free to query)
- Also: transportation, addresses, places, administrative boundaries
- GERS (Global Entity Reference System) — unique IDs for every feature

**Use case for Wardar:** Cross-reference Overture building footprints with satellite imagery change detection to identify destroyed/damaged structures in conflict zones.

---

## 6. ADDITIONAL OSINT SIGNAL SOURCES

### 6.1 USGS Earthquake Feed

**API:** https://earthquake.usgs.gov/fdsnws/event/1/
- Real-time GeoJSON, free, no key required
- Filters: time range, magnitude, region
- Update lag: minutes

**Conflict use:** Underground explosions (bunker busters, tunnel collapses) produce seismic signatures. Nuclear test detection. Cross-reference with known conflict coordinates and depth (<10km = likely surface/subsurface explosion).

---

### 6.2 GPS Jamming / Electronic Warfare Detection

**GPSJam (gpsjam.org):** Free daily heatmaps of GPS interference. Areas of active jamming visible — Kaliningrad, Black Sea, Middle East, GPS spoofing corridors.

**FR24 GPS Spoofing Data:** Flightradar24 publishes daily CSV of GPS spoofing reports from aircraft.

**Wardar value:** GPS jamming layer is a direct indicator of electronic warfare activity. No other conflict map shows this as a layer. This is a genuine differentiator.

---

### 6.3 CelesTrak / Space-Track.org (Satellite Orbital Data)

**CelesTrak:** Free TLE data. Good for quick integration. https://celestrak.org/

**Space-Track.org (upgrade):**
- Official USSF (US Space Force) source
- 138M+ historical TLEs
- Free registration required: https://www.space-track.org
- Enables: debris tracking, conjunction analysis, detection of classified/unknown satellites, Sentinel SAR pass prediction

**Wardar Phase 2 plan:** Use Space-Track to predict when Sentinel-1 (SAR) will pass over a given conflict zone. Show users: "Next SAR pass over Zaporizhzhia in 4h 23m."

---

### 6.4 FAA NOTAM (Notices to Air Missions)

**What it is:** Official airspace restriction notices. When conflict starts, NOTAMs appear blocking civilian air traffic over conflict zones.

**API:** FAA provides NOTAM API. International NOTAMs via ICAO NOTAM offices.

**Conflict value:** NOTAMs are often the FIRST official signal that military action is imminent or ongoing. They appear before news reports. This is a leading indicator.

---

### 6.5 UNHCR Refugee Statistics API

**API:** https://api.unhcr.org/docs/refugee-statistics.html
- Free, no credentials required
- JSON responses
- Population data by country of origin/asylum
- Nowcasting estimates (monthly rolling)
- Filters by country, year, population type

**Wardar use:** Show refugee flow arrows between countries as an overlay. Correlation with conflict events shows humanitarian impact. Useful for Wardar's "humanitarian situation" card.

---

## 7. ANALYTICAL / AI FORECAST PLATFORMS (NON-MAPPING)

### 7.1 VIEWS Forecasting (viewsforecasting.org)

Already covered in Section 4.4. Integrate as predictive risk heatmap.

### 7.2 Bellingcat Toolkit

**What it is:** Not a platform — a curated methodology toolkit + training program.

**Relevant tools from their toolkit:**
- LiveUAmap, ACLED, MarineTraffic, Sentinel Hub Playground, GeoConfirmed
- `osint-geo-extractor` Python library (MIT) — unifies Bellingcat + GeoConfirmed + DefMon3 + Cen4InfoRes + Texty.ua data into single GeoJSON pull

**Wardar action:** Treat Bellingcat's toolkit as the authoritative list of data sources to integrate. Any tool that appears in their toolkit has been vetted by the OSINT community.

---

## 8. SUMMARY: COMPETITOR FEATURE MATRIX

| Feature | World Monitor | LiveUAmap | ISW Maps | DeepStateMap | GeoConfirmed | GCA | Wardar |
|---------|--------------|-----------|----------|-------------|--------------|-----|--------|
| ADS-B flight tracking | Yes (Wingbits) | No | No | No | No | No | Yes (ADS-B Exch) |
| Military aircraft (unfiltered) | Partial | No | No | No | No | No | Yes |
| AIS ship tracking | Yes (AISStream) | No | No | No | No | No | Yes (AISStream) |
| Dark vessel detection | No | No | No | No | No | No | Planned |
| TLE satellite tracking | No | No | No | No | No | No | Planned |
| SAR pass prediction | No | No | No | No | No | No | Planned |
| GPS jamming layer | No | No | No | No | No | No | Planned |
| ACLED conflict events | Yes | No | No | No | No | No | Planned |
| GDELT news signals | Yes | No | No | No | No | No | Planned |
| NASA FIRMS thermal | Yes | No | No | No | No | No | Planned |
| Earthquake detection | No | No | No | No | No | No | Planned |
| VIEWS AI forecasting | No | No | No | No | No | No | Planned |
| Front-line polygons | No | No | Yes (Ukraine) | Yes (Ukraine) | Partial | Partial | Integrate ISW/DSM |
| Nuclear facility layer | No | No | No | No | No | No | Planned |
| Pipeline/energy layer | Partial (pipelines) | No | No | No | No | No | Planned |
| Military base layer | Yes (220) | No | No | No | No | No | Planned |
| Submarine cable layer | Partial | No | No | No | No | No | Planned |
| Historical replay | No | Partial | Yes (manual) | Yes | No | No | Planned |
| Cross-domain alerts | No | No | No | No | No | No | Planned |
| Enforced delay policy | No | No | No | No | No | No | YES (unique) |
| NOTAM layer | No | No | No | No | No | No | Planned |
| Refugee flow layer | No | No | No | No | No | No | Planned |
| Open source | Yes (AGPL) | No | No | No | No | Yes (MIT) | Yes (planned) |
| Public API | Pro/Enterprise | Pro | No | Restricted | No | Partial | Enterprise |
| Python ML-ready | No (TypeScript) | No | No | No | No | No | YES |

---

## 9. WARDAR'S UNIQUE MOAT — PRIORITIZED

These are features no existing platform has. Build these.

### MOAT 1: Enforced Delay Policy (Trust differentiator)
- Every other platform shows near-real-time military data without disclosure
- Wardar's 24h military delay is a responsible design feature to publicize
- Journalists, academics, NGOs will trust Wardar over competitors specifically because of this
- No operational value to adversaries; full analytical value preserved

### MOAT 2: Multi-Domain Historical Replay
- LiveUAmap has partial timeline. DeepStateMap has front-line replay. Nobody has ADS-B + AIS + TLE + ACLED events all replayable together.
- "What was happening in the Red Sea on October 7, 2024 at 0300?" — only Wardar can answer this across all domains simultaneously

### MOAT 3: Dark Vessel Detection
- Physics-based AIS gap detection: vessel disappears >2h in conflict zone = alert
- Speed spoofing detection: AIS speed > vessel's known max speed from registry
- Cross-reference with NASA FIRMS (thermal) to confirm vessel presence when AIS dark
- Research basis: arxiv.org/html/2404.07607v1 (97% F1 score)

### MOAT 4: Cross-Domain Convergence Alert
- Military aircraft surge (ADS-B) + naval repositioning (AIS) + ACLED event spike + GDELT instability rise + NASA FIRMS hotspots — all in same region in same 6h window = compound alert
- This is "God's Eye" capability. No existing platform correlates across all 5 signals simultaneously.

### MOAT 5: GPS Jamming + Electronic Warfare Layer
- No conflict map currently shows GPS jamming zones
- GPSJam daily data is free
- Jamming is a direct indicator of electronic warfare / military operations prep
- Correlate with NOTAM airspace closures for compound signal

### MOAT 6: Nuclear/Energy Infrastructure Threat Detection
- FIRMS thermal anomaly detected within 10km of IAEA-registered nuclear facility = alert
- ACLED event within 5km of pipeline = infrastructure threat alert
- No platform does automated critical infrastructure threat correlation

### MOAT 7: VIEWS Conflict Forecast Layer
- No mapping platform displays VIEWS 1-36 month conflict probability heatmaps
- This is the only open-source, peer-reviewed conflict AI forecast
- Makes Wardar the only platform where you can see current conflict AND predicted future conflict on same map

### MOAT 8: Public API for Researchers (with delay enforcement)
- Open API that Bellingcat, Reuters, academics can build on
- Competitor: DeepStateMap restricted their API after abuse. Wardar's delay policy makes abuse concerns irrelevant.
- This drives organic growth — every paper/article using Wardar data cites the platform

---

## 10. IMMEDIATE BUILD PRIORITIES (Quick wins)

### Phase 1 additions (before first deploy):
1. **Submarine cable layer** — 30 min: fetch `submarinecablemap.com/api/v3/cable/cable-geo.json`, render as polylines. No key required.
2. **NASA FIRMS endpoint** — 2h: free API key, query bounding boxes around active conflict zones, render as orange/red markers.
3. **USGS earthquake endpoint** — 1h: no key required, filter by depth (<10km) and magnitude (>2.0), cross-reference with conflict coordinates.

### Phase 2 additions (intelligence layer):
4. **ACLED API** — register at acleddata.com, filter to active conflict countries, heatmap + individual event markers.
5. **GDELT Stability API** — per-country instability score as color-coded country polygons.
6. **GPSJam layer** — fetch daily CSVs from gpsjam.org, show as purple interference zones.
7. **osint-geo-extractor** — `pip install osint-geo-extractor`, pull unified OSINT event data from 5 databases.
8. **UNHCR refugee flow arrows** — register at api.unhcr.org, show displacement flows as animated arrows.

### Phase 3 additions (moat-building):
9. **VIEWS forecast heatmap** — register at viewsforecasting.org, display 6-month conflict probability grid.
10. **Dark vessel detection algorithm** — AIS gap detection logic in Python.
11. **Cross-domain convergence alerts** — event aggregation across ADS-B + AIS + ACLED + FIRMS + GDELT.
12. **NOTAM layer** — FAA NOTAM API + ICAO international NOTAMs.

---

## 11. DATA SOURCE REFERENCE TABLE

| Source | Type | Cost | API | Key Required | Update Frequency |
|--------|------|------|-----|-------------|-----------------|
| ADS-B Exchange | Aviation (unfiltered military) | Free/Paid tiers | REST | RapidAPI key | Real-time |
| OpenSky Network | Aviation | Free (academic) | REST + SQL | Token (free) | Real-time |
| Wingbits | Aviation (DePIN) | Free trial / Paid | REST | Yes (free trial) | 5 seconds |
| AISStream.io | Maritime | Free | WebSocket | Yes (free) | Real-time |
| AISHub | Maritime | Free (share feed) | REST | Yes | Real-time |
| CelesTrak | Orbital TLE | Free | REST | No | 24h |
| Space-Track.org | Orbital TLE (USSF) | Free | REST | Yes (free) | Continuous |
| NASA FIRMS | Fire/Thermal | Free | REST | Yes (free) | <60 seconds |
| USGS Earthquakes | Seismic | Free | REST | No | Minutes |
| ACLED | Conflict events | Free | REST | Yes (free) | Daily |
| GDELT | News events | Free | REST | No | 15 minutes |
| UCDP | Conflict data | Free | REST | Token (free, 2026) | Annual |
| VIEWS | AI conflict forecast | Free | REST + Download | No | Monthly |
| UNHCR | Refugee data | Free | REST | No | Monthly |
| GeoConfirmed | OSINT verification | Free | QGIS plugin | No | Manual |
| osint-geo-extractor | Multi-OSINT | Free (MIT) | Python lib | No | On-demand |
| Submarine Cable Map | Infrastructure | Free | REST | No | Static (updates annually) |
| Global Nuclear Power Tracker (GEM) | Nuclear facilities | Free (CC BY 4.0) | Download | No | Annual (Sept 2025) |
| IAEA PRIS | Nuclear facilities | Free | Web only | No | Continuous |
| GeoNuclearData (GitHub) | Nuclear facilities | Free | Download | No | Static |
| Global Oil Infra Tracker (GEM) | Pipelines | Free | Download | No | Periodic |
| OGIM (Google Earth Engine) | Oil/gas infra | Free (research) | GEE API | Yes (free) | Annual |
| OpenStreetMap | Everything | Free (ODbL) | Overpass API | No | Continuous |
| Overture Maps | Buildings/roads | Free (CC BY 4.0) | S3/Azure | No | Periodic |
| USDOT Military Bases | Military bases | Free | Download | No | Annual |
| GPSJam | GPS interference | Free | Download (CSV) | No | Daily |
| MarineTraffic | Maritime | Paid | REST | Paid | Real-time |
| Copernicus/Sentinel Hub | Satellite imagery | Free (Sentinel) | REST | Yes (free) | 5-12 days |
| CrisisWatch (ICG) | Conflict monthly | Free | None | No | Monthly |
| DeepStateMap | Ukraine front-lines | Free | Restricted API | Client key | Daily |
| ISW Maps | Ukraine/global | Free | None (ArcGIS export) | No | Daily |

---

_Research compiled 2026-03-26. Sources: live web research via WebSearch + WebFetch._
