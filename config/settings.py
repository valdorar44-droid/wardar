"""Wardar — Configuration (all env-overridable)"""
from __future__ import annotations
import os

def _s(k, d=""): return os.environ.get(k, d)
def _i(k, d=0):
    v = os.environ.get(k)
    try: return int(v) if v else d
    except: return d
def _b(k, d=False):
    v = os.environ.get(k)
    if not v: return d
    return str(v).strip().lower() in ("1","true","yes","y","on")
def _f(k, d=0.0):
    v = os.environ.get(k)
    try: return float(v) if v else d
    except: return d

# ── App ───────────────────────────────────────────────
APP_HOST  = _s("WARDAR_HOST", "0.0.0.0")
APP_PORT  = _i("WARDAR_PORT", 8080)
SECRET_KEY = _s("SECRET_KEY", "change-me-in-production")

# ── Database ──────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
_default_db = "/data/wardar.db" if _on_railway else os.path.join(_ROOT, "wardar.db")
DB_PATH = _s("WARDAR_DB_PATH", _default_db)

# ── Delay Policy ──────────────────────────────────────
# All delays set to 0 — all data sources are public broadcast feeds.
# apply_delay() still runs (release_ts_utc = raw_ts_utc) so the release
# filter works correctly; data just appears immediately.
DELAY_CIVILIAN_SEC  = _i("DELAY_CIVILIAN_SEC",  0)
DELAY_SENSITIVE_SEC = _i("DELAY_SENSITIVE_SEC", 0)
DELAY_CONFLICT_SEC  = _i("DELAY_CONFLICT_SEC",  0)

# ── ADS-B Sources ─────────────────────────────────────
ADSB_EXCHANGE_API_KEY = _s("ADSB_EXCHANGE_API_KEY", "")
ADSB_EXCHANGE_URL     = _s("ADSB_EXCHANGE_URL", "https://adsbexchange-com1.p.rapidapi.com/v2/lat/{lat}/lon/{lon}/dist/{dist}/")
OPENSKY_USERNAME      = _s("OPENSKY_USERNAME", "")
OPENSKY_PASSWORD      = _s("OPENSKY_PASSWORD", "")
ENABLE_ADSB           = _b("ENABLE_ADSB", True)
ENABLE_OPENSKY        = _b("ENABLE_OPENSKY", True)   # fallback if no ADS-B Exchange key

# ── Airplanes.live (free military aircraft feed) ──────
ENABLE_MIL_AIRCRAFT       = _b("ENABLE_MIL_AIRCRAFT",       False)  # covered by adsb.fetch() /mil at 10s
MIL_AIRCRAFT_INTERVAL_SEC = _i("MIL_AIRCRAFT_INTERVAL_SEC", 30)

# ── AIS Sources ───────────────────────────────────────
AISSTREAM_API_KEY = _s("AISSTREAM_API_KEY", "")      # aisstream.io API key
ENABLE_AIS        = _b("ENABLE_AIS", True)

# ── Satellite ─────────────────────────────────────────
CELESTRAK_URL   = _s("CELESTRAK_URL", "https://celestrak.org/SOCRATES/query.php")
TLE_BASE_URL    = _s("TLE_BASE_URL", "https://celestrak.org/SOCRATES/")
ENABLE_SATELLITE = _b("ENABLE_SATELLITE", True)

# ── NOTAM / Airspace ──────────────────────────────────
# Primary source: CheckWX (https://checkwxapi.com) — simple API key, free 100 calls/day
CHECKWX_API_KEY = _s("CHECKWX_API_KEY", "")
# Fallback: FAA Digital NOTAM API (OAuth2 — portal currently broken for public signups)
FAA_NOTAM_URL     = _s("FAA_NOTAM_URL", "https://external-api.faa.gov/notamapi/v1/notams")
FAA_CLIENT_ID     = _s("FAA_CLIENT_ID", "")
FAA_CLIENT_SECRET = _s("FAA_CLIENT_SECRET", "")
ENABLE_NOTAM    = _b("ENABLE_NOTAM", True)

# ── Conflict / OSINT ──────────────────────────────────
ACLED_API_KEY   = _s("ACLED_API_KEY", "")            # ACLED conflict events
ACLED_EMAIL     = _s("ACLED_EMAIL", "")
GDELT_URL       = _s("GDELT_URL", "https://api.gdeltproject.org/api/v2/doc/doc")
BRAVE_API_KEY   = _s("BRAVE_API_KEY", "")            # reuse from Argus if same key
ENABLE_ACLED    = _b("ENABLE_ACLED", True)
ENABLE_GDELT    = _b("ENABLE_GDELT", True)
ENABLE_OSINT_NEWS = _b("ENABLE_OSINT_NEWS", True)
ENABLE_DEFENSE_FEEDS = _b("ENABLE_DEFENSE_FEEDS", True)

# ── Scan Intervals ────────────────────────────────────
ADSB_INTERVAL_SEC   = _i("ADSB_INTERVAL_SEC",   10)   # pull aviation every 10s
AIS_RECONNECT_SEC   = _i("AIS_RECONNECT_SEC",   30)   # AIS WebSocket reconnect
SATELLITE_INTERVAL_SEC = _i("SATELLITE_INTERVAL_SEC", 3600)  # TLE refresh hourly
NOTAM_INTERVAL_SEC  = _i("NOTAM_INTERVAL_SEC",  21600) # NOTAMs every 6h (CheckWX free tier: 100 calls/day, 24 airports × 4 polls = 96)
ACLED_INTERVAL_SEC  = _i("ACLED_INTERVAL_SEC",  3600) # conflict events hourly
GDELT_INTERVAL_SEC  = _i("GDELT_INTERVAL_SEC",  900)  # GDELT every 15 min

# ── Data Retention ────────────────────────────────────
POSITION_RETAIN_HOURS = _i("POSITION_RETAIN_HOURS", 6)    # 6h — with upsert, stale rows = gone entities
AIS_RETAIN_HOURS      = _i("AIS_RETAIN_HOURS",       2)   # AIS upserts per-MMSI; 2h removes gone ships
EVENT_RETAIN_DAYS     = _i("EVENT_RETAIN_DAYS",     30)    # keep 30d of events

# ── Fire / Thermal (NASA FIRMS) ───────────────────────
ENABLE_FIRMS        = _b("ENABLE_FIRMS",       True)
FIRMS_INTERVAL_SEC  = _i("FIRMS_INTERVAL_SEC", 10800)  # MODIS updates every ~3h

# ── Seismic (USGS) ────────────────────────────────────
ENABLE_USGS         = _b("ENABLE_USGS",        True)
USGS_INTERVAL_SEC   = _i("USGS_INTERVAL_SEC",  900)    # every 15 min

# ── GPS Jamming / Electronic Warfare (GPSJam) ─────────
ENABLE_GPSJAM       = _b("ENABLE_GPSJAM",      True)
GPSJAM_INTERVAL_SEC = _i("GPSJAM_INTERVAL_SEC", 3600)  # hourly

# ── AI (optional — for OSINT summaries) ──────────────
ANTHROPIC_API_KEY = _s("ANTHROPIC_API_KEY", "")
AI_MODEL          = _s("AI_MODEL", "claude-haiku-4-5-20251001")  # fast + cheap for summaries

# ── Cesium Ion (enables real terrain + satellite imagery + 3D buildings) ──
# Free tier at https://ion.cesium.com → Account → Access Tokens → Default Token
# Without this token the globe uses CartoDB dark tiles + flat terrain (still functional)
CESIUM_ION_TOKEN  = _s("CESIUM_ION_TOKEN", "")

# ── Sentinel Hub (optional — 5-day 10m near-real-time imagery) ─────────────
# Free trial or paid plan: https://www.sentinel-hub.com/
# Create a WMTS configuration instance, paste the Instance ID below.
SENTINEL_HUB_INSTANCE_ID = _s("SENTINEL_HUB_INSTANCE_ID", "")

# ── Static Infrastructure Layers ──────────────────────
ENABLE_NUCLEAR      = _b("ENABLE_NUCLEAR",      True)
ENABLE_SUBCABLES    = _b("ENABLE_SUBCABLES",     True)
ENABLE_MIL_BASES    = _b("ENABLE_MIL_BASES",     True)
ENABLE_PIPELINES    = _b("ENABLE_PIPELINES",     True)
ENABLE_EEZ          = _b("ENABLE_EEZ",          True)
STATIC_REFRESH_SEC  = _i("STATIC_REFRESH_SEC",   86400)  # daily

# ── OSINT Geo-Extractor ────────────────────────────────
ENABLE_OSINT_GEO    = _b("ENABLE_OSINT_GEO",     True)
OSINT_GEO_INTERVAL_SEC = _i("OSINT_GEO_INTERVAL_SEC", 3600)

# ── UNHCR Refugees ─────────────────────────────────────
ENABLE_UNHCR        = _b("ENABLE_UNHCR",         True)
UNHCR_INTERVAL_SEC  = _i("UNHCR_INTERVAL_SEC",   86400)  # daily

# ── VIEWS Conflict Forecast ────────────────────────────
ENABLE_VIEWS        = _b("ENABLE_VIEWS",         True)
VIEWS_INTERVAL_SEC  = _i("VIEWS_INTERVAL_SEC",   86400)  # daily

# ── Phase 3 Alert Engine ───────────────────────────────
ENABLE_ALERTS              = _b("ENABLE_ALERTS",              True)
DARK_VESSEL_INTERVAL_SEC   = _i("DARK_VESSEL_INTERVAL_SEC",   900)   # every 15 min
CONVERGENCE_INTERVAL_SEC   = _i("CONVERGENCE_INTERVAL_SEC",   1800)  # every 30 min
PROXIMITY_INTERVAL_SEC     = _i("PROXIMITY_INTERVAL_SEC",     3600)  # hourly
DARK_VESSEL_GAP_HOURS      = _i("DARK_VESSEL_GAP_HOURS",      2)     # hours before "dark"
NUCLEAR_ALERT_KM           = _i("NUCLEAR_ALERT_KM",           10)    # km radius
PIPELINE_ALERT_KM          = _i("PIPELINE_ALERT_KM",          5)     # km radius
CONVERGENCE_SCORE_THRESHOLD= _i("CONVERGENCE_SCORE_THRESHOLD", 4)    # min score

# ── Phase 4 Dark Signal Detectors ─────────────────────
VESSEL_SPOOF_INTERVAL_SEC     = _i("VESSEL_SPOOF_INTERVAL_SEC",     600)   # every 10 min
TRANSPONDER_LOSS_INTERVAL_SEC = _i("TRANSPONDER_LOSS_INTERVAL_SEC", 600)   # every 10 min
FIRMS_USGS_INTERVAL_SEC       = _i("FIRMS_USGS_INTERVAL_SEC",       1800)  # every 30 min
GPSJAM_DARK_INTERVAL_SEC      = _i("GPSJAM_DARK_INTERVAL_SEC",      900)   # every 15 min

# ── Phase 5 Temporal Intelligence ──────────────────────
HISTORY_RETAIN_HOURS     = _i("HISTORY_RETAIN_HOURS",     72)   # 3 days of track history
MIN_HIST_INTERVAL_SEC    = _i("MIN_HIST_INTERVAL_SEC",    120)  # ≥2 min between history writes per entity
ENABLE_ROUTE_DEV         = _b("ENABLE_ROUTE_DEV",         True)
ROUTE_DEV_INTERVAL_SEC   = _i("ROUTE_DEV_INTERVAL_SEC",  1800)  # route deviation check every 30 min
ROUTE_DEV_THRESHOLD_KM   = _i("ROUTE_DEV_THRESHOLD_KM",  300)   # km off baseline before alerting
CHOKEPOINT_INTERVAL_SEC  = _i("CHOKEPOINT_INTERVAL_SEC", 1800)  # chokepoint stats every 30 min

# ── Pikud HaOref (Israel rocket alerts) ────────────────
ENABLE_PIKUD_HAOREF        = _b("ENABLE_PIKUD_HAOREF",        True)
PIKUD_HAOREF_INTERVAL_SEC  = _i("PIKUD_HAOREF_INTERVAL_SEC",  15)    # poll every 15s

# ── Wikipedia Edit Spike Detector ──────────────────────
ENABLE_WIKIPEDIA_SPIKES      = _b("ENABLE_WIKIPEDIA_SPIKES",      True)
WIKIPEDIA_SPIKE_INTERVAL_SEC = _i("WIKIPEDIA_SPIKE_INTERVAL_SEC", 120)  # run every 2 min

# ── Polymarket (prediction markets) ────────────────────────
ENABLE_POLYMARKET       = _b("ENABLE_POLYMARKET",        True)
POLYMARKET_INTERVAL_SEC = _i("POLYMARKET_INTERVAL_SEC",  3600)  # hourly

# ── ISW Frontlines (control maps) ──────────────────────────
ENABLE_ISW              = _b("ENABLE_ISW",               True)
ISW_REFRESH_SEC         = _i("ISW_REFRESH_SEC",          21600)  # 6h

# ── Shodan (infrastructure intelligence) ───────────────────
SHODAN_API_KEY          = _s("SHODAN_API_KEY",           "")
ENABLE_SHODAN           = _b("ENABLE_SHODAN",            False)  # requires paid key ($69/mo)
SHODAN_INTERVAL_SEC     = _i("SHODAN_INTERVAL_SEC",      14400)  # 4h

# ── OpenSanctions (entity screening) ───────────────────────
OPENSANCTIONS_API_KEY   = _s("OPENSANCTIONS_API_KEY",    "")
ENABLE_OPENSANCTIONS    = _b("ENABLE_OPENSANCTIONS",     False)

# ── AI Intelligence Brief (SITREP) ──────────────────────────
ENABLE_INTEL_BRIEF        = _b("ENABLE_INTEL_BRIEF",        True)   # requires ANTHROPIC_API_KEY
INTEL_BRIEF_INTERVAL_SEC  = _i("INTEL_BRIEF_INTERVAL_SEC",  21600)  # 6h

# ── IODA Internet Blackout Monitoring ────────────────────────────────────────
ENABLE_IODA         = _b("ENABLE_IODA",        True)   # free, no auth
IODA_INTERVAL_SEC   = _i("IODA_INTERVAL_SEC",  1800)   # every 30 min

# ── WarSpotting (Ukraine equipment losses) ─────────────────────────────────────
ENABLE_WARSPOT         = _b("ENABLE_WARSPOT",        True)   # free, User-Agent required
WARSPOT_INTERVAL_SEC   = _i("WARSPOT_INTERVAL_SEC",  3600)   # hourly

# ── Safecast (global radiation monitoring) ────────────────────────────────────
ENABLE_SAFECAST         = _b("ENABLE_SAFECAST",        True)   # free, CC0 data
SAFECAST_INTERVAL_SEC   = _i("SAFECAST_INTERVAL_SEC",  3600)   # hourly
SAFECAST_ALERT_CPM      = _i("SAFECAST_ALERT_CPM",     100)    # alert threshold (normal ~20-30 CPM)

# ── OFAC / EU Sanctions (vessel + aircraft cross-reference) ───────────────────
ENABLE_OFAC           = _b("ENABLE_OFAC",           True)   # free XML download
OFAC_INTERVAL_SEC     = _i("OFAC_INTERVAL_SEC",     86400)  # daily

# ── UCDP Conflict Events (Uppsala University) ─────────────────────────────────
UCDP_TOKEN            = _s("UCDP_TOKEN",            "")     # free token via email
ENABLE_UCDP           = _b("ENABLE_UCDP",           True)   # enable if token set
UCDP_INTERVAL_SEC     = _i("UCDP_INTERVAL_SEC",     3600)   # hourly

# ── Media Storage (S3-compatible: Cloudflare R2 / AWS S3 / Backblaze B2) ──────
# Leave all blank to use local filesystem (uploads lost on redeploy).
# See core/storage.py for full setup instructions.
S3_ENDPOINT_URL      = _s("S3_ENDPOINT_URL",      "")   # e.g. https://<id>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID     = _s("S3_ACCESS_KEY_ID",     "")
S3_SECRET_ACCESS_KEY = _s("S3_SECRET_ACCESS_KEY", "")
S3_BUCKET_NAME       = _s("S3_BUCKET_NAME",       "")
S3_PUBLIC_URL        = _s("S3_PUBLIC_URL",        "")   # e.g. https://pub-xxx.r2.dev
S3_REGION            = _s("S3_REGION",            "auto")

# ── Reddit OSINT — DISABLED (Railway datacenter IPs are hard-blocked by Reddit)
ENABLE_REDDIT_OSINT       = _b("ENABLE_REDDIT_OSINT",       False)  # blocked from cloud IPs
REDDIT_OSINT_INTERVAL_SEC = _i("REDDIT_OSINT_INTERVAL_SEC", 900)

# ── Telegram OSINT (replaces Reddit — uses RSSHub, not blocked from Railway) ───
# No Telegram API key needed — reads public channels via RSSHub RSS proxy
# Requires ANTHROPIC_API_KEY for AI noise filtering
ENABLE_TELEGRAM_OSINT       = _b("ENABLE_TELEGRAM_OSINT",       True)
TELEGRAM_OSINT_INTERVAL_SEC = _i("TELEGRAM_OSINT_INTERVAL_SEC", 900)  # 15 min — RSS refreshes slowly, batching reduces token cost

# ── Breaking News (Google News RSS + wire services) ───────────────────────────
ENABLE_BREAKING_NEWS        = _b("ENABLE_BREAKING_NEWS",        True)
BREAKING_NEWS_INTERVAL_SEC  = _i("BREAKING_NEWS_INTERVAL_SEC",  600)   # 10 min — reduces AI calls ~3x

# ── Phase 7 Intelligence Products ─────────────────────────────────────────────
ALERT_WEBHOOK_URL       = _s("ALERT_WEBHOOK_URL",       "")   # Slack/Discord/custom incoming webhook
WEBHOOK_MIN_INTERVAL_SEC = _i("WEBHOOK_MIN_INTERVAL_SEC", 30)  # flush webhooks every 30s
DIGEST_HOURS            = _i("DIGEST_HOURS",            24)    # daily digest lookback window
DIGEST_LIMIT            = _i("DIGEST_LIMIT",            10)    # top N events
DIGEST_CONV_RADIUS_KM   = _i("DIGEST_CONV_RADIUS_KM",  200)   # convergence radius

# ── Phase 11: Entity Identity Graph ──────────────────────────────────────────
ENTITY_GRAPH_INTERVAL_SEC = _i("ENTITY_GRAPH_INTERVAL_SEC", 300)  # resolve entities every 5 min

# ── WW3 Risk Meter (Phase 8+) ─────────────────────────────────────────────────
# AI-generated daily global escalation index (0-100).
# Updates once per calendar day UTC at midnight; uses ANTHROPIC_API_KEY.
ENABLE_WW3_METER         = _b("ENABLE_WW3_METER",         True)
WW3_METER_CHECK_SEC      = _i("WW3_METER_CHECK_SEC",       3600)  # check hourly, update if date rolled

# ── Bluesky Jetstream OSINT ───────────────────────────────────────────────────
# Free WebSocket firehose — no key required. Requires ANTHROPIC_API_KEY for AI filter.
ENABLE_BLUESKY_OSINT        = _b("ENABLE_BLUESKY_OSINT",        True)
BLUESKY_OSINT_INTERVAL_SEC  = _i("BLUESKY_OSINT_INTERVAL_SEC",  900)   # 15 min (25s listen per call)

# ── EURDEP EU Radiation Network ────────────────────────────────────────────────
# Free REST API — 5,000 gamma stations across Europe. No key required.
ENABLE_EURDEP       = _b("ENABLE_EURDEP",       True)
EURDEP_INTERVAL_SEC = _i("EURDEP_INTERVAL_SEC", 3600)  # hourly
EURDEP_ALERT_NSVH  = _i("EURDEP_ALERT_NSVH",   500)   # alert above 500 nSv/h (≥3× normal)

# ── Commodity Prices (Yahoo Finance, no key) ───────────────────────────────────
ENABLE_COMMODITY_PRICES        = _b("ENABLE_COMMODITY_PRICES",        True)
COMMODITY_PRICES_INTERVAL_SEC  = _i("COMMODITY_PRICES_INTERVAL_SEC",  3600)  # hourly

# ── ReliefWeb Humanitarian API ────────────────────────────────────────────────
# Free API at api.reliefweb.int. No key required.
ENABLE_RELIEFWEB_API        = _b("ENABLE_RELIEFWEB_API",        True)
RELIEFWEB_API_INTERVAL_SEC  = _i("RELIEFWEB_API_INTERVAL_SEC",  21600)  # 6h

# ── Global Fishing Watch (SAR dark vessel detection) ──────────────────────────
# Free non-commercial API. Register at globalfishingwatch.org.
# Set GFW_API_KEY in Railway environment variables.
GFW_API_KEY         = _s("GFW_API_KEY",         "")
ENABLE_GFW          = _b("ENABLE_GFW",          False)  # enable once key is set
GFW_INTERVAL_SEC    = _i("GFW_INTERVAL_SEC",    3600)   # hourly

# ── Admin Dashboard ────────────────────────────────────────────────────────────
# Set ADMIN_PASSWORD in Railway env vars. Default is "wardar-admin" (change it!).
# Stored as SHA-256 hex digest. To generate: python3 -c "import hashlib; print(hashlib.sha256(b'yourpassword').hexdigest())"
ADMIN_PASSWORD_HASH = _s("ADMIN_PASSWORD_HASH", "")  # if blank, defaults to hash of "wardar-admin"
ADMIN_TOKEN_TTL_SEC = _i("ADMIN_TOKEN_TTL_SEC", 28800)  # 8 hours
