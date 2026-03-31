"""Wardar — Alert webhook dispatcher (Slack/Discord/custom incoming webhook)"""
from __future__ import annotations
from datetime import datetime, timezone
from config import settings as C

_queue: list[dict] = []

def enqueue(source: str, title: str, desc: str, lat: float, lon: float) -> None:
    """Queue an alert for webhook dispatch. Called synchronously from alerts.py."""
    if not C.ALERT_WEBHOOK_URL:
        return
    _queue.append({
        "source": source, "title": title, "desc": desc,
        "lat": lat, "lon": lon,
        "ts": datetime.now(timezone.utc).isoformat()[:19] + " UTC",
    })

async def flush() -> int:
    """Dispatch all queued alerts to the webhook URL. Returns count dispatched."""
    if not _queue or not C.ALERT_WEBHOOK_URL:
        _queue.clear()
        return 0
    import httpx
    items = _queue[:]
    _queue.clear()
    dispatched = 0
    async with httpx.AsyncClient(timeout=8) as client:
        for it in items:
            payload = _format_payload(it)
            try:
                r = await client.post(C.ALERT_WEBHOOK_URL, json=payload)
                if r.status_code < 300:
                    dispatched += 1
            except Exception:
                pass  # best-effort, never crash
    return dispatched

def _format_payload(it: dict) -> dict:
    """Format as Slack/Discord-compatible incoming webhook JSON."""
    source_upper = it["source"].upper().replace("_", " ")
    color_map = {
        "dark_vessel": "#06b6d4", "vessel_spoof": "#f59e0b",
        "transponder_loss": "#f97316", "firms_usgs": "#ef4444",
        "gpsjam_dark": "#a855f7", "route_dev": "#22c55e",
        "nuclear_threat": "#ef4444", "pipeline_threat": "#f97316",
        "convergence": "#eab308",
    }
    color = color_map.get(it["source"], "#b8bcc8")
    return {
        "text": f"*\u26a1 WARDAR \u2014 {source_upper}*",
        "attachments": [{
            "color": color,
            "title": it["title"],
            "text": it["desc"][:500],
            "footer": f"Wardar Intel | {it['ts']}",
            "fields": [
                {"title": "Source", "value": source_upper, "short": True},
                {"title": "Location", "value": f"({it['lat']:.3f}, {it['lon']:.3f})", "short": True},
            ],
        }],
    }
