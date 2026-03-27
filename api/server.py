"""Wardar — FastAPI server + WebSocket endpoint"""
from __future__ import annotations
import asyncio, json, os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config import settings as C
from db import store as DB
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
_DASH = os.path.join(os.path.dirname(__file__), "..", "dashboard")
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
    sources: str = "",
    w: float = -180, s: float = -90, e: float = 180, n: float = 90,
    limit: int = 5000,
):
    src_list = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    bbox     = (w, s, e, n)
    data     = DB.get_released_positions(bbox=bbox, sources=src_list, limit=limit)
    return JSONResponse({"count": len(data), "data": data})

@app.get("/api/events")
async def get_events(
    sources: str = "",
    w: float = -180, s: float = -90, e: float = 180, n: float = 90,
    limit: int = 1000,
):
    src_list = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    bbox     = (w, s, e, n)
    data     = DB.get_released_events(bbox=bbox, sources=src_list, limit=limit)
    return JSONResponse({"count": len(data), "data": data})

@app.get("/api/stats")
async def get_stats():
    return JSONResponse(DB.get_counts())

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
        # Send initial snapshot
        positions = DB.get_released_positions(limit=2000)
        events    = DB.get_released_events(limit=200)
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
