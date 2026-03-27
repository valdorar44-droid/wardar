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

# ── Delay Policy (NON-NEGOTIABLE — never bypass) ──────
# All values in seconds. Applied by core/engine.py:apply_delay()
DELAY_CIVILIAN_SEC  = _i("DELAY_CIVILIAN_SEC",  30)      # ADS-B / AIS civilian feeds
DELAY_SENSITIVE_SEC = _i("DELAY_SENSITIVE_SEC", 86400)   # Military-flagged signals: 24h
DELAY_CONFLICT_SEC  = _i("DELAY_CONFLICT_SEC",  3600)    # Conflict zone event overlays: 1h

# ── ADS-B Sources ─────────────────────────────────────
ADSB_EXCHANGE_API_KEY = _s("ADSB_EXCHANGE_API_KEY", "")
ADSB_EXCHANGE_URL     = _s("ADSB_EXCHANGE_URL", "https://adsbexchange-com1.p.rapidapi.com/v2/lat/{lat}/lon/{lon}/dist/{dist}/")
OPENSKY_USERNAME      = _s("OPENSKY_USERNAME", "")
OPENSKY_PASSWORD      = _s("OPENSKY_PASSWORD", "")
ENABLE_ADSB           = _b("ENABLE_ADSB", True)
ENABLE_OPENSKY        = _b("ENABLE_OPENSKY", True)   # fallback if no ADS-B Exchange key

# ── AIS Sources ───────────────────────────────────────
AISSTREAM_API_KEY = _s("AISSTREAM_API_KEY", "")      # aisstream.io API key
ENABLE_AIS        = _b("ENABLE_AIS", True)

# ── Satellite ─────────────────────────────────────────
CELESTRAK_URL   = _s("CELESTRAK_URL", "https://celestrak.org/SOCRATES/query.php")
TLE_BASE_URL    = _s("TLE_BASE_URL", "https://celestrak.org/SOCRATES/")
ENABLE_SATELLITE = _b("ENABLE_SATELLITE", True)

# ── NOTAM / Airspace ──────────────────────────────────
FAA_NOTAM_URL   = _s("FAA_NOTAM_URL", "https://external-api.faa.gov/notamapi/v1/notams")
FAA_CLIENT_ID   = _s("FAA_CLIENT_ID", "")
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
NOTAM_INTERVAL_SEC  = _i("NOTAM_INTERVAL_SEC",  900)  # NOTAMs every 15 min
ACLED_INTERVAL_SEC  = _i("ACLED_INTERVAL_SEC",  3600) # conflict events hourly
GDELT_INTERVAL_SEC  = _i("GDELT_INTERVAL_SEC",  900)  # GDELT every 15 min

# ── Data Retention ────────────────────────────────────
POSITION_RETAIN_HOURS = _i("POSITION_RETAIN_HOURS", 72)   # keep 72h of positions
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

# ── Static Infrastructure Layers ──────────────────────
ENABLE_NUCLEAR      = _b("ENABLE_NUCLEAR",      True)
ENABLE_SUBCABLES    = _b("ENABLE_SUBCABLES",     True)
ENABLE_MIL_BASES    = _b("ENABLE_MIL_BASES",     True)
ENABLE_PIPELINES    = _b("ENABLE_PIPELINES",     True)
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
