"""Wardar — FastAPI server + WebSocket endpoint"""
from __future__ import annotations
import asyncio, json, os
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

# Serve frontend + uploads
_DASH = os.path.join(os.path.dirname(__file__), "..", "dashboard")
_UPLOADS_DIR = os.path.join(_DASH, "uploads")
os.makedirs(_UPLOADS_DIR, exist_ok=True)
if os.path.isdir(_DASH):
    app.mount("/static", StaticFiles(directory=_DASH), name="static")

# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the map SPA."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "map.html")
    if os.path.exists(html_path):
        with open(html_path) as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Wardar — map.html not found</h1>", status_code=404)

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
        "extra":        "{}",
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

# ── Image Upload ─────────────────────────────────────────────────────────────

_ALLOWED_TYPES = {"image/jpeg","image/png","image/gif","image/webp","image/heic"}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

@app.post("/api/community/upload")
async def upload_image(file: UploadFile = File(...)):
    if file.content_type not in _ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Image files only (JPEG/PNG/GIF/WEBP/HEIC)")
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Max 10 MB")
    ext = (file.filename or "img").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "gif", "webp", "heic"):
        ext = "jpg"
    fname = f"{uuid.uuid4().hex}.{ext}"
    dest  = os.path.join(_UPLOADS_DIR, fname)
    with open(dest, "wb") as f:
        f.write(data)
    return JSONResponse({"url": f"/static/uploads/{fname}"})

# ── Static Infrastructure Layers ─────────────────────────────────────────────

@app.get("/api/layers/{layer}")
async def get_static_layer(response: Response, layer: str):
    """
    Serve cached GeoJSON for static infrastructure layers:
    nuclear, cables, mil_bases, pipelines
    """
    valid = {"nuclear", "cables", "mil_bases", "pipelines", "isw_ukraine", "isw_middle_east"}
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

# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    log(f"ws: client connected ({ws.client})")

    subscribed_sources: list[str] = []
    subscribed_bbox: tuple | None = None

    async def _send(payload: str):
        """Filtered send — only push data matching this client's subscription."""
        try:
            msg = json.loads(payload)
            sources = msg.get("sources") or []
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
        # Send initial snapshot — sampled so AIS doesn't crowd out aircraft/satellites
        positions = get_released_positions_sampled(per_source=400, limit=2000)
        events    = DB.get_released_events(limit=500)
        await ws.send_text(json.dumps({"type": "snapshot", "positions": positions, "events": events}))

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
