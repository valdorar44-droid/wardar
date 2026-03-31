"""Wardar — FastAPI server + WebSocket endpoint"""
from __future__ import annotations
import asyncio, json, os, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import shutil, uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, field_validator
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config import settings as C
from db import store as DB
from db.store import insert_chat_message, get_chat_messages, get_released_positions_sampled
from core.engine import (
    log, log_warn, log_err,
    register_ws_client, unregister_ws_client,
    start as engine_start,
)

# ── Snapshot cache ────────────────────────────────────────────────────────────
# Pre-computed snapshot served instantly to every new WebSocket client.
# With upsert, the positions table stays tiny so this is always fast and small.
_snap_cache: str | None = None
_snap_ts: float = 0.0
_SNAP_TTL = 60.0  # rebuild at most once per minute

def _get_snapshot_json() -> str:
    global _snap_cache, _snap_ts
    now = time.monotonic()
    if _snap_cache is None or (now - _snap_ts) > _SNAP_TTL:
        positions = get_released_positions_sampled(per_source=150, limit=1000)
        events    = DB.get_released_events(limit=400)
        _snap_cache = json.dumps({"type":"snapshot","positions":positions,"events":events})
        _snap_ts = now
    return _snap_cache

def invalidate_snapshot():
    """Call whenever new data is ingested so next client gets fresh data."""
    global _snap_ts
    _snap_ts = 0.0

# ── Lifespan ──────────────────────────────────────────────────────────────────

_engine_tasks: list[asyncio.Task] = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    log("wardar: boot")
    tasks = await engine_start()
    _engine_tasks.extend(tasks)
    yield
    log("wardar: shutdown")
    for t in _engine_tasks:
        t.cancel()

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Wardar", version="0.1.0", lifespan=lifespan)

# GZip all JSON responses > 1KB — typically 5-10x smaller on the wire
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend + local uploads (local storage fallback)
_DASH = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if os.path.isdir(_DASH):
    app.mount("/static", StaticFiles(directory=_DASH), name="static")

# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the map SPA with ETag caching. Injects runtime config as JS constants."""
    import hashlib
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "map.html")
    if not os.path.exists(html_path):
        return HTMLResponse("<h1>Wardar — map.html not found</h1>", status_code=404)
    stat = os.stat(html_path)
    # ETag includes token hash so cache busts if Ion token changes
    tok_hash = hashlib.md5((C.CESIUM_ION_TOKEN or "").encode()).hexdigest()[:8]
    etag = f'"{int(stat.st_mtime)}-{stat.st_size}-{tok_hash}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    with open(html_path) as f:
        content = f.read()
    # Inject runtime config into the HTML template placeholder
    content = content.replace("__CESIUM_ION_TOKEN__", C.CESIUM_ION_TOKEN or "")
    content = content.replace("__SENTINEL_HUB_ID__", C.SENTINEL_HUB_INSTANCE_ID or "")
    return HTMLResponse(content, headers={
        "ETag": etag,
        "Cache-Control": "no-cache",
        "Vary": "Accept-Encoding",
    })

@app.get("/api/health")
async def health():
    counts = DB.get_counts()
    return {
        "status": "ok",
        "ts":     datetime.now(timezone.utc).isoformat(),
        **counts,
    }

@app.get("/api/positions")
async def get_positions(
    response: Response,
    sources: str = "",
    w: float = -180, s: float = -90, e: float = 180, n: float = 90,
    limit: int = 5000,
):
    response.headers["Cache-Control"] = "public, max-age=8"  # 8s — matches ADS-B poll
    src_list = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    bbox     = (w, s, e, n)
    if src_list:
        data = DB.get_released_positions(bbox=bbox, sources=src_list, limit=limit)
    else:
        # No source filter — use sampled query so AIS doesn't drown aircraft/satellites
        data = get_released_positions_sampled(per_source=600, limit=limit)
    return JSONResponse({"count": len(data), "data": data})

@app.get("/api/events")
async def get_events(
    response: Response,
    sources: str = "",
    w: float = -180, s: float = -90, e: float = 180, n: float = 90,
    limit: int = 1000,
):
    response.headers["Cache-Control"] = "public, max-age=15"
    src_list = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    bbox     = (w, s, e, n)
    data     = DB.get_released_events(bbox=bbox, sources=src_list, limit=limit)
    return JSONResponse({"count": len(data), "data": data})

@app.get("/api/stats")
async def get_stats():
    return JSONResponse(DB.get_counts())

# ── Playback endpoints ────────────────────────────────────────────────────────

@app.get("/api/playback/summary")
async def playback_summary():
    """Return time range + 5-minute bucket counts for the timeline scrubber."""
    return JSONResponse(DB.get_playback_summary())

@app.get("/api/playback/frame")
async def playback_frame(
    ts: str,
    window: int = 600,
    sources: str = "",
    w: float = -180, s: float = -90, e: float = 180, n: float = 90,
    limit: int = 5000,
):
    """
    Return the latest known position per callsign at timestamp `ts`.
    window = look-back seconds (default 30 — matches ADS-B poll interval).
    Delay policy is enforced: release_ts_utc <= ts.
    """
    src_list = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    bbox     = (w, s, e, n)
    data     = DB.get_positions_at(ts=ts, window_sec=window, sources=src_list, bbox=bbox, limit=limit)
    return JSONResponse({"ts": ts, "count": len(data), "data": data})

# ── Phase 5: Track / Biography endpoints ─────────────────────────────────────

@app.get("/api/track/{source}/{callsign}")
async def get_track(source: str, callsign: str, hours: int = 24):
    """
    Return the throttled position history for a single entity.
    Delay policy enforced — military positions held back 300s.
    Max `hours` capped at HISTORY_RETAIN_HOURS.
    """
    hours = min(hours, C.HISTORY_RETAIN_HOURS)
    data  = DB.get_position_track(source, callsign, hours=hours)
    return JSONResponse({"source": source, "callsign": callsign,
                         "hours": hours, "count": len(data), "track": data})

# Chokepoint definitions: (name, west, south, east, north)
_CHOKEPOINTS = [
    ("Strait of Hormuz",     55.8, 25.6, 57.0, 27.0),
    ("Strait of Malacca",   100.0,  1.0,104.5,  6.0),
    ("Suez Canal",           31.0, 30.0, 33.0, 32.5),
    ("Bab-el-Mandeb",        42.5, 11.0, 44.0, 13.5),
    ("GIUK Gap",            -30.0, 56.0,  0.0, 66.0),
    ("Gibraltar",            -6.0, 35.5, -4.5, 36.5),
    ("Taiwan Strait",       119.5, 22.0,122.5, 26.5),
    ("English Channel",      -2.5, 49.0,  2.5, 52.0),
    ("Danish Straits",        9.5, 54.5, 13.5, 58.0),
    ("Luzon Strait",        119.5, 18.5,123.0, 21.5),
]

@app.get("/api/chokepoints")
async def get_chokepoints():
    """
    Return entity counts for each strategic chokepoint over the last 1h, 6h, and 24h.
    Derived from position_history (Phase 5).
    """
    results = []
    for name, w, s, e, n in _CHOKEPOINTS:
        results.append({
            "name":   name,
            "bbox":   [w, s, e, n],
            "last1h":  DB.get_chokepoint_count(w, s, e, n, hours=1),
            "last6h":  DB.get_chokepoint_count(w, s, e, n, hours=6),
            "last24h": DB.get_chokepoint_count(w, s, e, n, hours=24),
        })
    return JSONResponse({"chokepoints": results})


@app.get("/api/digest")
async def get_digest(hours: int = 24, limit: int = 10):
    """
    Returns top events ranked by cross-domain convergence score.
    Convergence score = count of distinct event sources within DIGEST_CONV_RADIUS_KM
    and the past `hours` window around each event.
    Only returns released events (delay policy enforced).
    """
    import math
    events = DB.get_released_events(limit=3000)
    # Filter to events with coordinates and within time window
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=min(hours, 168))).isoformat()
    geo = [e for e in events if e.get("lat") and e.get("lon") and e.get("raw_ts_utc","") >= cutoff]
    if not geo:
        return JSONResponse({"hours": hours, "count": 0, "events": []})

    R_KM = float(C.DIGEST_CONV_RADIUS_KM)
    def hav(lat1, lon1, lat2, lon2):
        R = 6371.0
        p1,p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2-lat1); dl = math.radians(lon2-lon1)
        a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return 2*R*math.asin(min(1.0,a**0.5))

    scored = []
    for ev in geo:
        nearby_sources = set()
        for other in geo:
            if other is ev: continue
            if hav(ev["lat"], ev["lon"], other["lat"], other["lon"]) <= R_KM:
                nearby_sources.add(other.get("source",""))
        ev["_convergence"] = len(nearby_sources)
        scored.append(ev)

    scored.sort(key=lambda e: e["_convergence"], reverse=True)
    top = scored[:min(limit, C.DIGEST_LIMIT)]
    # Strip internal field
    for e in top:
        e.pop("_convergence", None)
    return JSONResponse({"hours": hours, "count": len(top), "events": top,
                         "radius_km": R_KM})

# ── Community Intel ───────────────────────────────────────────────────────────

_VALID_REPORT_TYPES = {
    "armor","troops","convoy","aircraft","drone","naval",
    "airstrike","artillery","explosion","checkpoint",
    "missile_site","radar","airbase","bunker",
    "jamming","cyber","damage","displacement","hazmat","other",
}

class CommunityReportIn(BaseModel):
    author_token: str = Field(..., min_length=8, max_length=128)
    title:        str = Field(..., min_length=3, max_length=200)
    description:  str = Field("", max_length=2000)
    lat:          float | None = None
    lon:          float | None = None
    country:      str = Field("", max_length=100)
    category:     str = Field("intel", pattern="^(intel|sighting|movement|incident|analysis)$")
    source_url:   str = Field("", max_length=500)
    report_type:  str = Field("other", max_length=50)
    severity:     int = Field(3, ge=1, le=5)
    confidence:   str = Field("medium", pattern="^(low|medium|high)$")
    image_url:    str = Field("", max_length=1000)
    nickname:     str = Field("", max_length=24)   # optional callsign / reporter handle

    @field_validator("report_type")
    @classmethod
    def validate_report_type(cls, v: str) -> str:
        if v not in _VALID_REPORT_TYPES:
            return "other"
        return v

class VoteIn(BaseModel):
    voter_token: str = Field(..., min_length=8, max_length=128)
    vote:        int = Field(..., ge=-1, le=1)

@app.get("/api/community")
async def get_community(
    lat: float | None = None,
    lon: float | None = None,
    radius: float = 200,
    limit: int = 200,
):
    if lat is not None and lon is not None:
        data = DB.get_community_reports_near(lat, lon, radius_km=radius, limit=limit)
    else:
        data = DB.get_community_reports_global(limit=limit)
    return JSONResponse({"count": len(data), "data": data})

@app.post("/api/community", status_code=201)
async def create_community_report(body: CommunityReportIn):
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "author_token": body.author_token,
        "title":        body.title,
        "description":  body.description,
        "lat":          body.lat,
        "lon":          body.lon,
        "country":      body.country,
        "category":     body.category,
        "source_url":   body.source_url,
        "report_type":  body.report_type,
        "severity":     body.severity,
        "confidence":   body.confidence,
        "image_url":    body.image_url,
        "created_at":   now,
        "extra":        json.dumps({"nickname": body.nickname}) if body.nickname else "{}",
    }
    report_id = DB.insert_community_report(row)
    return JSONResponse({"id": report_id}, status_code=201)

@app.get("/api/community/{report_id}")
async def get_community_report(report_id: int):
    report = DB.get_community_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(report)

@app.post("/api/community/{report_id}/vote")
async def vote_community_report(report_id: int, body: VoteIn):
    if body.vote == 0:
        raise HTTPException(status_code=400, detail="vote must be +1 or -1")
    try:
        updated = DB.vote_community_report(report_id, body.voter_token, body.vote)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(updated)

# ── Media Upload (images + video) ────────────────────────────────────────────

_ALLOWED_IMAGE_TYPES = {"image/jpeg","image/png","image/gif","image/webp","image/heic","image/heif"}
_ALLOWED_VIDEO_TYPES = {"video/mp4","video/webm","video/quicktime","video/x-msvideo","video/x-matroska"}
_ALLOWED_TYPES       = _ALLOWED_IMAGE_TYPES | _ALLOWED_VIDEO_TYPES
_MAX_IMAGE_BYTES     = 10 * 1024 * 1024   # 10 MB for images
_MAX_VIDEO_BYTES     = 50 * 1024 * 1024   # 50 MB for video

_IMAGE_EXTS = {"jpg","jpeg","png","gif","webp","heic","heif"}
_VIDEO_EXTS = {"mp4","webm","mov","avi","mkv"}

@app.post("/api/community/upload")
async def upload_media(file: UploadFile = File(...)):
    from core.storage import save as storage_save, backend_name as storage_backend
    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Accepted: JPEG/PNG/GIF/WEBP (10 MB) or MP4/WEBM/MOV (50 MB)"
        )
    is_video  = ct in _ALLOWED_VIDEO_TYPES
    max_bytes = _MAX_VIDEO_BYTES if is_video else _MAX_IMAGE_BYTES
    max_label = "50 MB" if is_video else "10 MB"

    data = await file.read()
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large — max {max_label}")

    ext = (file.filename or "file").rsplit(".", 1)[-1].lower()
    allowed_exts = _VIDEO_EXTS if is_video else _IMAGE_EXTS
    if ext not in allowed_exts:
        ext = "mp4" if is_video else "jpg"

    try:
        url = await storage_save(data, ext, ct)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Storage error: {exc}")

    return JSONResponse({
        "url":      url,
        "is_video": is_video,
        "size_kb":  len(data) // 1024,
        "backend":  storage_backend(),
    })

# ── WW3 Risk Meter ───────────────────────────────────────────────────────────

@app.get("/api/ww3")
async def get_ww3(response: Response):
    """Return the latest WW3 escalation index. Updates once per UTC calendar day."""
    response.headers["Cache-Control"] = "public, max-age=300"
    data = DB.get_ww3_meter()
    history = DB.get_ww3_history(days=30)
    if not data:
        return JSONResponse({
            "score": None, "level": "UNKNOWN",
            "assessment": "First analysis runs at midnight UTC — check back soon.",
            "key_factors": [], "generated_at": None, "history": []
        })
    try:
        data["key_factors"] = json.loads(data.get("key_factors") or "[]")
    except Exception:
        data["key_factors"] = []
    data["history"] = history
    return JSONResponse(data)

@app.post("/api/ww3/generate", status_code=202)
async def trigger_ww3():
    """Force-regenerate the WW3 meter now (bypasses daily gate — for testing)."""
    if not C.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    import asyncio
    from core.ww3_meter import generate_ww3_score
    from core.engine import _broadcast
    async def _run():
        result = await generate_ww3_score()
        if result.get("score") is not None:
            await _broadcast({"type": "ww3_meter", "data": result})
    asyncio.create_task(_run())
    return JSONResponse({"status": "generating"}, status_code=202)

# ── Phase 8: Entity Annotations ──────────────────────────────────────────────

class AnnotationIn(BaseModel):
    author_token: str = Field(..., min_length=8, max_length=128)
    body:         str = Field(..., min_length=3, max_length=500)

@app.get("/api/annotations/{source}/{callsign}")
async def get_annotations(source: str, callsign: str, limit: int = 20, response: Response = None):
    """Return analyst annotations for a tracked entity."""
    if response:
        response.headers["Cache-Control"] = "public, max-age=30"
    data = DB.get_annotations(source, callsign, limit=min(limit, 50))
    return JSONResponse({"source": source, "callsign": callsign, "count": len(data), "data": data})

@app.post("/api/annotations/{source}/{callsign}", status_code=201)
async def post_annotation(source: str, callsign: str, body: AnnotationIn):
    """Submit an analyst annotation for a tracked entity."""
    ann_id = DB.insert_annotation(source, callsign, body.author_token, body.body)
    return JSONResponse({"id": ann_id}, status_code=201)

@app.post("/api/annotations/{ann_id}/vote")
async def vote_annotation(ann_id: int, body: VoteIn):
    """Vote on an annotation (+1 upvote, -1 downvote)."""
    up   = 1 if body.vote == 1  else 0
    down = 1 if body.vote == -1 else 0
    result = DB.vote_annotation(ann_id, up, down)
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(result)

# ── Phase 8: Shared Watchlists ────────────────────────────────────────────────

class WatchlistShareIn(BaseModel):
    name:        str = Field(..., min_length=1, max_length=100)
    owner_token: str = Field(..., min_length=8, max_length=128)
    entities:    list[dict] = Field(...)

@app.post("/api/watchlists/share", status_code=201)
async def share_watchlist(body: WatchlistShareIn, request: Request):
    """Persist a named watchlist and return a share token."""
    if len(body.entities) > 200:
        raise HTTPException(status_code=400, detail="max 200 entities per shared list")
    entities_json = json.dumps(body.entities)
    token = DB.create_shared_watchlist(body.name, body.owner_token, entities_json)
    base = str(request.base_url).rstrip("/")
    return JSONResponse({"share_token": token, "url": f"{base}/?wl={token}"}, status_code=201)

@app.get("/api/watchlists/{share_token}")
async def get_shared_watchlist(share_token: str):
    """Return a shared watchlist by token."""
    wl = DB.get_shared_watchlist(share_token)
    if not wl:
        raise HTTPException(status_code=404, detail="watchlist not found")
    try:
        wl["entities"] = json.loads(wl.get("entities") or "[]")
    except Exception:
        wl["entities"] = []
    return JSONResponse(wl)

# ── Phase 8: Public API v1 ────────────────────────────────────────────────────
# Same data as the private endpoints with delay policy enforced.
# Future: X-API-Key header will unlock real-time tier (currently no-op).

_V1_DESCRIPTION = "Wardar Public API v1 — delayed release, same data as dashboard."

@app.get("/api/v1/positions", summary="Live positions (delayed)", tags=["Public API v1"],
         description=_V1_DESCRIPTION)
async def v1_positions(
    response: Response,
    sources: str = "",
    w: float = -180, s: float = -90, e: float = 180, n: float = 90,
    limit: int = 1000,
):
    """Delayed-release positions. Max 1000 rows. Filter by source (comma-separated) or bbox."""
    response.headers["Cache-Control"] = "public, max-age=30"
    src_list = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    bbox     = (w, s, e, n)
    if src_list:
        data = DB.get_released_positions(bbox=bbox, sources=src_list, limit=min(limit, 1000))
    else:
        data = get_released_positions_sampled(per_source=200, limit=min(limit, 1000))
    return JSONResponse({"count": len(data), "data": data, "api_version": "v1"})

@app.get("/api/v1/events", summary="Events (delayed)", tags=["Public API v1"],
         description=_V1_DESCRIPTION)
async def v1_events(
    response: Response,
    sources: str = "",
    w: float = -180, s: float = -90, e: float = 180, n: float = 90,
    limit: int = 500,
):
    """Delayed-release events. Max 500 rows."""
    response.headers["Cache-Control"] = "public, max-age=30"
    src_list = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    bbox     = (w, s, e, n)
    data     = DB.get_released_events(bbox=bbox, sources=src_list, limit=min(limit, 500))
    return JSONResponse({"count": len(data), "data": data, "api_version": "v1"})

@app.get("/api/v1/alerts", summary="Recent alerts", tags=["Public API v1"],
         description=_V1_DESCRIPTION)
async def v1_alerts(response: Response, hours: int = 24, limit: int = 100):
    """Recent triggered alerts (dark vessels, spoofing, route deviation, etc.)."""
    response.headers["Cache-Control"] = "public, max-age=30"
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=min(hours, 168))).isoformat()
    db = DB.get_conn()
    rows = db.execute(
        "SELECT source, title, description, lat, lon, raw_ts_utc FROM events "
        "WHERE source='alert' AND raw_ts_utc >= ? ORDER BY raw_ts_utc DESC LIMIT ?",
        (cutoff, min(limit, 100))
    ).fetchall()
    data = [dict(r) for r in rows]
    return JSONResponse({"count": len(data), "data": data, "api_version": "v1"})

@app.get("/api/v1/annotations/{source}/{callsign}", summary="Entity annotations",
         tags=["Public API v1"], description=_V1_DESCRIPTION)
async def v1_annotations(source: str, callsign: str, response: Response):
    """Analyst annotations for a specific tracked entity."""
    response.headers["Cache-Control"] = "public, max-age=60"
    data = DB.get_annotations(source, callsign, limit=50)
    return JSONResponse({"source": source, "callsign": callsign, "count": len(data),
                         "data": data, "api_version": "v1"})

# ── Static Infrastructure Layers ─────────────────────────────────────────────

@app.get("/api/layers/{layer}")
async def get_static_layer(response: Response, layer: str):
    """
    Serve cached GeoJSON for static infrastructure layers:
    nuclear, cables, mil_bases, pipelines
    """
    valid = {"nuclear", "cables", "mil_bases", "pipelines", "isw_ukraine", "isw_middle_east", "eez"}
    if layer not in valid:
        raise HTTPException(status_code=404, detail=f"Unknown layer: {layer}")
    try:
        from ingestors.static_layers import get_layer
        data = await get_layer(layer)
        response.headers["Cache-Control"] = "public, max-age=3600"  # 1h — static data
        return JSONResponse(data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

# ── Country Intel Chat ────────────────────────────────────────────────────────

class ChatMessageIn(BaseModel):
    author_token: str = Field(..., min_length=8, max_length=128)
    message:      str = Field(..., min_length=1, max_length=1000)

@app.get("/api/chat/{country}")
async def get_country_chat(country: str, limit: int = 100):
    if not country.strip():
        raise HTTPException(status_code=400, detail="country required")
    msgs = get_chat_messages(country, limit=min(limit, 200))
    return JSONResponse({"country": country, "count": len(msgs), "data": msgs})

@app.post("/api/chat/{country}", status_code=201)
async def post_country_chat(country: str, body: ChatMessageIn):
    if not country.strip():
        raise HTTPException(status_code=400, detail="country required")
    msg = insert_chat_message(country, body.author_token, body.message)
    return JSONResponse(msg, status_code=201)

# ── AI Intelligence Brief (SITREP) ───────────────────────────────────────────

@app.get("/api/brief")
async def get_intel_brief(response: Response):
    """Return the latest cached AI intelligence brief."""
    from core.intel_brief import get_cached_brief
    brief = get_cached_brief()
    if not brief.get("text"):
        # Fallback: try DB
        stored = DB.get_latest_brief()
        if stored:
            brief = {"text": stored["text"], "generated_at": stored["generated_at"],
                     "model": stored.get("model",""), "sources_used": [], "error": None}
    if not brief.get("text"):
        raise HTTPException(status_code=404, detail="No brief generated yet — check ANTHROPIC_API_KEY")
    response.headers["Cache-Control"] = "public, max-age=300"  # 5 min
    return JSONResponse(brief)

@app.post("/api/brief/generate", status_code=202)
async def trigger_intel_brief():
    """Trigger an immediate AI brief regeneration (async — result appears in /api/brief)."""
    if not C.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    import asyncio
    from core.intel_brief import generate_global_brief
    from core.engine import _broadcast
    async def _run():
        result = await generate_global_brief()
        if result.get("text"):
            await _broadcast({"type": "intel_brief", "data": result})
    asyncio.create_task(_run())
    return JSONResponse({"status": "generating", "message": "Brief will be ready in ~30s"}, status_code=202)

@app.post("/api/predict")
async def predict_asset(request: Request):
    """AI prediction for a tracked asset — identifies what it is, where it's going, threat assessment.
    Returns text assessment + predicted path waypoints for globe arc rendering.
    Body: {callsign, type, lat, lon, heading_deg, speed_kts, altitude_ft, country, source, military_flag, trail}
    """
    body = await request.json()
    if not C.ANTHROPIC_API_KEY:
        return JSONResponse({"error": "no_api_key", "text": "ANTHROPIC_API_KEY not configured."})

    callsign    = str(body.get("callsign") or "UNKNOWN").strip()[:32]
    asset_type  = str(body.get("type") or "").strip()[:32]
    lat         = float(body.get("lat") or 0)
    lon         = float(body.get("lon") or 0)
    heading     = float(body.get("heading_deg") or 0)
    speed       = float(body.get("speed_kts") or 0)
    altitude    = float(body.get("altitude_ft") or 0)
    country     = str(body.get("country") or "").strip()[:64]
    source      = str(body.get("source") or "").strip()[:32]
    military    = bool(int(body.get("military_flag") or 0))
    trail       = body.get("trail") or []  # [{lat,lon}] recent positions

    # Build prompt
    trail_desc = ""
    if trail and len(trail) >= 2:
        pts = trail[-10:]
        trail_desc = f"\nRecent trail ({len(pts)} positions): " + " → ".join(
            f"({p['lat']:.2f},{p['lon']:.2f})" for p in pts
        )

    prompt = f"""You are a military intelligence analyst. Analyze this tracked asset and provide a brief assessment.

ASSET DATA:
- Callsign: {callsign}
- Type: {asset_type or 'unknown'}
- Position: {lat:.4f}°N, {lon:.4f}°E
- Heading: {heading:.0f}°
- Speed: {speed:.0f} kts
- Altitude: {altitude:.0f} ft
- Country of registration: {country or 'unknown'}
- Data source: {source}
- Military flag: {'YES' if military else 'NO'}
{trail_desc}

Respond in this exact JSON format (no markdown, just raw JSON):
{{
  "assessment": "2-3 sentence plain-English assessment of what this asset likely is, its current activity, and any threat significance",
  "destination": "Most likely destination or operational area based on heading/speed/position",
  "threat_level": "LOW|MODERATE|HIGH|CRITICAL",
  "asset_id": "Best guess at platform type (e.g. P-8 Poseidon, Su-35, cargo vessel, etc.)",
  "path": [
    {{"lat": X, "lon": Y}},
    {{"lat": X, "lon": Y}},
    {{"lat": X, "lon": Y}},
    {{"lat": X, "lon": Y}},
    {{"lat": X, "lon": Y}}
  ]
}}

The "path" array must have exactly 5 waypoints projecting the asset's most likely trajectory over the next 2 hours based on heading {heading:.0f}° and speed {speed:.0f} kts. Use great-circle math. Start from current position ({lat:.4f}, {lon:.4f}).
Keep path realistic — account for known geography (don't fly through mountains, don't route ships over land).
"""

    try:
        import anthropic as _ant
        client = _ant.AsyncAnthropic(api_key=C.ANTHROPIC_API_KEY)
        msg = await client.messages.create(
            model=C.AI_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        result["callsign"] = callsign
        result["model"] = C.AI_MODEL
        return JSONResponse(result)
    except json.JSONDecodeError as exc:
        return JSONResponse({"error": f"parse_error: {exc}", "text": raw if 'raw' in dir() else ""})
    except Exception as exc:
        log_warn(f"predict: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/api/country-stats/{country}")
async def get_country_stats(country: str, response: Response):
    """Aggregate country-level intelligence: events, forecasts, displacement, severity score."""
    import re as _re
    country = country.strip()
    if not country:
        raise HTTPException(status_code=400, detail="country required")

    response.headers["Cache-Control"] = "public, max-age=120"
    now_iso = datetime.now(timezone.utc).isoformat()

    db = DB.get_conn()
    pat = f"%{country.lower()}%"

    # Source breakdown — exclude sensor/environment noise
    src_rows = db.execute(
        """SELECT source, COUNT(*) as n
           FROM events
           WHERE lower(country) LIKE ? AND release_ts_utc <= ?
             AND source NOT IN ('firms','gpsjam','usgs')
           GROUP BY source ORDER BY n DESC""",
        (pat, now_iso),
    ).fetchall()
    source_counts: dict[str, int] = {r["source"]: r["n"] for r in src_rows}
    total_events = sum(source_counts.values())

    # All-time event count including sensors (for severity)
    all_cnt = db.execute(
        "SELECT COUNT(*) as n FROM events WHERE lower(country) LIKE ? AND release_ts_utc <= ?",
        (pat, now_iso),
    ).fetchone()
    total_all = (all_cnt["n"] if all_cnt else 0)

    # VIEWS forecast
    vrow = db.execute(
        """SELECT extra FROM events
           WHERE source='views_forecast' AND lower(country) LIKE ?
           ORDER BY raw_ts_utc DESC LIMIT 1""",
        (pat,),
    ).fetchone()
    views_data: dict | None = None
    if vrow:
        try:
            views_data = json.loads(vrow["extra"])
        except Exception:
            pass

    # UNHCR displacement — parse from description field
    urow = db.execute(
        """SELECT description FROM events
           WHERE source='unhcr' AND lower(country) LIKE ?
           ORDER BY raw_ts_utc DESC LIMIT 1""",
        (pat,),
    ).fetchone()
    displaced = 0
    if urow and urow["description"]:
        try:
            nums = _re.findall(r"[\d,]+", urow["description"])
            displaced = sum(int(n.replace(",", "")) for n in nums[:3])  # refugees+asylum+IDPs
        except Exception:
            pass

    # Recent events feed (skip sensor floods and raw lat/lon-only rows)
    recent_rows = db.execute(
        """SELECT source, title, description, lat, lon, url, raw_ts_utc
           FROM events
           WHERE lower(country) LIKE ? AND release_ts_utc <= ?
             AND title IS NOT NULL AND title != ''
             AND source NOT IN ('firms','gpsjam','usgs','views_forecast','unhcr')
           ORDER BY raw_ts_utc DESC LIMIT 12""",
        (pat, now_iso),
    ).fetchall()
    recent_events = [dict(r) for r in recent_rows]

    # Severity score 0-10
    event_score  = min(5.0, total_events * 0.4)
    views_score  = float((views_data or {}).get("prob", 0)) * 4.0
    disp_score   = 1.0 if displaced > 2_000_000 else (0.5 if displaced > 200_000 else 0.0)
    severity     = round(min(10.0, event_score + views_score + disp_score), 1)

    return JSONResponse({
        "country":        country,
        "total_events":   total_events,
        "source_breakdown": source_counts,
        "severity_score": severity,
        "views_forecast": views_data,
        "displaced":      displaced,
        "recent_events":  recent_events,
    })


@app.get("/api/brief/country/{country}")
async def get_country_brief(country: str):
    """Generate an on-demand AI brief for a specific country."""
    if not C.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    if not country.strip():
        raise HTTPException(status_code=400, detail="country required")
    from core.intel_brief import generate_country_brief
    result = await generate_country_brief(country)
    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])
    return JSONResponse(result)

# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    log(f"ws: client connected ({ws.client})")

    subscribed_sources: list[str] = []
    subscribed_bbox: tuple | None = None

    async def _send(payload: str):
        """Filtered send — only push data matching this client's subscription.

        If subscribed_sources is empty (client sent subscribe with domains: [] or never
        narrowed), no source filter is applied — all engine broadcasts are delivered.
        """
        try:
            msg = json.loads(payload)
            sources = msg.get("sources") or []
            # Non-empty subscribed_sources + non-empty broadcast sources → require overlap
            if subscribed_sources and sources:
                if not any(s in subscribed_sources for s in sources):
                    return
            # If client has a bbox, filter positions server-side
            if subscribed_bbox and msg.get("type") == "positions":
                w, s_, e_, n_ = subscribed_bbox
                filtered = [
                    p for p in (msg.get("data") or [])
                    if w <= (p.get("lon") or 0) <= e_ and s_ <= (p.get("lat") or 0) <= n_
                ]
                msg["data"] = filtered
                payload = json.dumps(msg)
            await ws.send_text(payload)
        except Exception:
            raise  # bubble up so the outer loop can clean up

    register_ws_client(_send)

    try:
        # Send cached snapshot — same data for all clients, computed at most every 20s
        await ws.send_text(_get_snapshot_json())

        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "subscribe":
                    subscribed_sources = msg.get("domains") or []
                    bbox = msg.get("bbox")
                    if bbox and len(bbox) == 4:
                        subscribed_bbox = tuple(bbox)
                    log(f"ws: subscribe sources={subscribed_sources} bbox={subscribed_bbox}")
                elif mtype == "unsubscribe":
                    subscribed_sources = []
                    subscribed_bbox    = None
                    log("ws: unsubscribed")
                elif mtype == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        log(f"ws: client disconnected ({ws.client})")
    except Exception as exc:
        log_warn(f"ws: error: {exc}")
    finally:
        unregister_ws_client(_send)
