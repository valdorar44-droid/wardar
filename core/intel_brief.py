"""Wardar — AI Intelligence Brief (SITREP)

Cross-domain synthesis via Claude Haiku. No competitor offers this at prosumer level.
Pulls recent released data across all active sources and generates a structured
tactical intelligence summary.

Cached in memory + stored in DB. Generated every INTEL_BRIEF_INTERVAL_SEC (default 6h).
Can also be triggered on-demand via POST /api/brief/generate.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from typing import Any

from config import settings as C

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict = {
    "text":         "",
    "generated_at": "",
    "model":        "",
    "sources_used": [],
    "error":        None,
}


def get_cached_brief() -> dict:
    return dict(_cache)


async def generate_global_brief() -> dict:
    """
    Pull recent data from all active sources, synthesize with Claude Haiku,
    cache the result, and return it.
    """
    if not C.ANTHROPIC_API_KEY:
        return {"error": "no_api_key", "text": "ANTHROPIC_API_KEY not configured.", "generated_at": ""}

    try:
        from db import store as DB
        context, sources_used = _build_context(DB)
    except Exception as exc:
        return {"error": f"db_error: {exc}", "text": "", "generated_at": ""}

    if not context.strip():
        return {"error": "no_data", "text": "Insufficient data to generate brief.", "generated_at": ""}

    now = datetime.now(timezone.utc).isoformat()

    prompt = f"""You are an intelligence analyst at a global situational awareness platform. Based on the following sensor data collected in the last 6 hours, produce a concise INTELLIGENCE BRIEF (SITREP) in the format below. Be direct, precise, and use military-style language. Do not speculate beyond the data. Do not mention sources by technical name (use "aviation tracking", "maritime AIS", "conflict monitoring", etc.).

FORMAT:
## EXECUTIVE SUMMARY
[2-3 sentence overview of the current global threat picture]

## ACTIVE HOTSPOTS
[Bullet list: one per active conflict zone or threat area. Include country, nature of activity, severity.]

## AVIATION & MARITIME ANOMALIES
[Notable military aircraft activity, dark vessels, emergency squawks, NOTAM closures. Skip if none.]

## PREDICTION MARKET SIGNALS
[What geopolitical outcomes are being priced by prediction markets. Focus on conflicts/tensions.]

## THREAT TRAJECTORY
[1-2 sentences: is the overall situation escalating, stable, or de-escalating in the next 24-48h?]

---
SENSOR DATA ({now[:10]}):
{context}

Respond with only the brief — no preamble, no "here is the brief", no markdown code blocks."""

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=C.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=C.AI_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip() if message.content else ""
        model = message.model or C.AI_MODEL
    except Exception as exc:
        error_msg = f"API error: {exc}"
        _cache.update({"error": error_msg, "text": "", "generated_at": now, "model": "", "sources_used": sources_used})
        return dict(_cache)

    _cache.update({
        "text":         text,
        "generated_at": now,
        "model":        model,
        "sources_used": sources_used,
        "error":        None,
    })

    # Persist to DB
    try:
        from db import store as DB
        DB.upsert_intel_brief({"text": text, "generated_at": now, "model": model,
                               "sources_used": json.dumps(sources_used)})
    except Exception:
        pass  # non-fatal — in-memory cache is sufficient

    return dict(_cache)


async def generate_country_brief(country: str) -> dict:
    """
    Generate a short country-specific intelligence brief on demand.
    Not cached — always fresh.
    """
    if not C.ANTHROPIC_API_KEY:
        return {"error": "no_api_key", "text": "ANTHROPIC_API_KEY not configured."}

    try:
        from db import store as DB
        context, _ = _build_context(DB, country=country, hours=72)
    except Exception as exc:
        return {"error": f"db_error: {exc}", "text": ""}

    if not context.strip():
        return {"error": "no_data", "text": f"No recent data found for {country}."}

    now = datetime.now(timezone.utc).isoformat()

    prompt = f"""You are an intelligence analyst. Based on the sensor data below for {country}, write a concise 3-5 sentence intelligence assessment covering: current threat level, active incidents, notable patterns, and outlook. Use direct, precise language. No preamble.

SENSOR DATA ({now[:10]}, last 72h, {country}):
{context}"""

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=C.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=C.AI_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip() if message.content else ""
    except Exception as exc:
        return {"error": f"API error: {exc}", "text": ""}

    return {"text": text, "country": country, "generated_at": now}


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(DB: Any, country: str | None = None, hours: int = 6) -> tuple[str, list[str]]:
    """
    Pull and format recent sensor data for the prompt.
    Returns (context_text, sources_used_list).
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    parts: list[str] = []
    sources_used: list[str] = []

    # ── Conflict events (ACLED) ───────────────────────────────────────────────
    conflict = _get_recent_events(DB, sources=["acled"], since=since, limit=30, country=country)
    if conflict:
        parts.append("CONFLICT EVENTS (last {}h):".format(hours))
        for e in conflict[:20]:
            parts.append(f"  - [{e.get('country','')}] {e.get('title','')} ({e.get('raw_ts_utc','')[:10]})")
        sources_used.append("acled")

    # ── OSINT / War news ─────────────────────────────────────────────────────
    osint = _get_recent_events(DB, sources=["gdelt","osint_news","twz","ukrinform","kyiv_ind",
                                            "isw","rusi","reuters","bbc"], since=since, limit=20, country=country)
    if osint:
        parts.append("\nOSINT NEWS EVENTS (last {}h):".format(hours))
        for e in osint[:15]:
            parts.append(f"  - [{e.get('country','')}] {e.get('title','')} ({e.get('source','')})")
        sources_used.append("osint")

    # ── Military aviation (adsb) ──────────────────────────────────────────────
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn = DB.get_conn()
        mil_rows = conn.execute(
            "SELECT source, callsign, type, lat, lon, country, extra FROM positions "
            "WHERE military_flag=1 AND release_ts_utc <= ? ORDER BY raw_ts_utc DESC LIMIT 30",
            (now,)
        ).fetchall()
        if mil_rows:
            parts.append("\nMILITARY AVIATION (active tracked aircraft):")
            countries_seen: dict[str, int] = {}
            for r in mil_rows:
                c = r["country"] or "UNKNOWN"
                countries_seen[c] = countries_seen.get(c, 0) + 1
            for c, n in sorted(countries_seen.items(), key=lambda x: -x[1])[:10]:
                parts.append(f"  - {c}: {n} military aircraft tracked")
            sources_used.append("adsb_military")
    except Exception:
        pass

    # ── Emergency squawks ─────────────────────────────────────────────────────
    emerg = _get_recent_events(DB, sources=["adsb_emergency"], since=since, limit=10, country=country)
    if emerg:
        parts.append("\nEMERGENCY SQUAWK ACTIVATIONS:")
        for e in emerg:
            extra = _parse_extra(e)
            squawk = extra.get("squawk", "")
            squawk_label = {"7700": "GENERAL EMERGENCY", "7600": "RADIO FAILURE",
                            "7500": "HIJACK"}.get(squawk, squawk)
            parts.append(f"  - [{e.get('country','')}] {e.get('title','')} — {squawk_label}")
        sources_used.append("adsb_emergency")

    # ── GPS jamming ───────────────────────────────────────────────────────────
    jam = _get_recent_events(DB, sources=["gpsjam"], since=since, limit=15, country=country)
    if jam:
        parts.append("\nGPS JAMMING / ELECTRONIC WARFARE:")
        for e in jam[:10]:
            extra = _parse_extra(e)
            pct = extra.get("jam_pct", "")
            parts.append(f"  - [{e.get('country','')}] lat={e.get('lat',''):.1f} lon={e.get('lon',''):.1f}"
                         + (f" jam={pct}%" if pct else ""))
        sources_used.append("gpsjam")

    # ── Rocket alerts (Pikud HaOref) ──────────────────────────────────────────
    rocket = _get_recent_events(DB, sources=["pikud_haoref"], since=since, limit=20, country=country)
    if rocket:
        parts.append(f"\nISRAEL ROCKET ALERTS ({len(rocket)} zones in last {hours}h):")
        for e in rocket[:10]:
            parts.append(f"  - {e.get('title','')}")
        sources_used.append("pikud_haoref")

    # ── Wikipedia edit spikes ─────────────────────────────────────────────────
    wiki = _get_recent_events(DB, sources=["wikipedia"], since=since, limit=10, country=country)
    if wiki:
        parts.append("\nWIKIPEDIA EDIT SPIKES (breaking news signal):")
        for e in wiki[:8]:
            parts.append(f"  - {e.get('title','')}")
        sources_used.append("wikipedia")

    # ── Dark vessel alerts ────────────────────────────────────────────────────
    dark = _get_recent_events(DB, sources=["dark_vessel"], since=since, limit=10, country=country)
    if dark:
        parts.append(f"\nDARK VESSEL DETECTIONS ({len(dark)} vessels went dark):")
        for e in dark[:8]:
            parts.append(f"  - {e.get('title','')}")
        sources_used.append("dark_vessel")

    # ── Cross-domain convergence alerts ──────────────────────────────────────
    conv = _get_recent_events(DB, sources=["convergence"], since=since, limit=5, country=country)
    if conv:
        parts.append(f"\nCROSS-DOMAIN CONVERGENCE ALERTS (high-confidence):")
        for e in conv[:5]:
            parts.append(f"  - [{e.get('country','')}] {e.get('title','')}")
        sources_used.append("convergence")

    # ── Nuclear/pipeline proximity alerts ────────────────────────────────────
    nuke = _get_recent_events(DB, sources=["nuclear_threat","pipeline_threat"], since=since, limit=5, country=country)
    if nuke:
        parts.append("\nINFRASTRUCTURE THREAT ALERTS:")
        for e in nuke[:5]:
            parts.append(f"  - [{e.get('source','').upper()}] {e.get('title','')}")
        sources_used.append("infrastructure_alerts")

    # ── Prediction markets (Polymarket) ──────────────────────────────────────
    poly = _get_recent_events(DB, sources=["polymarket"], since=since, limit=15, country=country)
    if poly:
        parts.append("\nPREDICTION MARKET SIGNALS (conflict probability):")
        for e in poly[:12]:
            extra = _parse_extra(e)
            prob = extra.get("prob_pct")
            vol  = extra.get("volume")
            line = f"  - {e.get('title','')}"
            if prob is not None:
                line += f" [YES: {prob}%]"
            if vol and vol > 10000:
                line += f" [Vol: ${vol:,.0f}]"
            parts.append(line)
        sources_used.append("polymarket")

    # ── VIEWS conflict forecast ───────────────────────────────────────────────
    views = _get_recent_events(DB, sources=["views_forecast"], since=since, limit=5, country=country)
    if views:
        parts.append("\nCONFLICT FORECAST (VIEWS model, high-risk countries):")
        for e in views[:5]:
            extra = _parse_extra(e)
            risk = extra.get("fatality_risk_pct")
            parts.append(f"  - [{e.get('country','')}] risk={risk}%" if risk else f"  - {e.get('title','')}")
        sources_used.append("views_forecast")

    # ── Active fires near facilities ──────────────────────────────────────────
    fires = _get_recent_events(DB, sources=["firms"], since=since, limit=10, country=country)
    if fires:
        parts.append(f"\nTHERMAL ANOMALIES / ACTIVE FIRES: {len(fires)} detected in period")
        sources_used.append("firms")

    # ── NOTAMs ────────────────────────────────────────────────────────────────
    notams = _get_recent_events(DB, sources=["notam"], since=since, limit=10, country=country)
    if notams:
        parts.append("\nACTIVE NOTAM AIRSPACE RESTRICTIONS:")
        for e in notams[:8]:
            parts.append(f"  - {e.get('title','')}")
        sources_used.append("notam")

    return "\n".join(parts), list(set(sources_used))


def _get_recent_events(DB: Any, sources: list[str], since: str,
                       limit: int, country: str | None = None) -> list[dict]:
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn = DB.get_conn()
        placeholders = ",".join("?" * len(sources))
        params: list = [now, since] + sources
        country_clause = ""
        if country:
            country_clause = " AND LOWER(country) LIKE ?"
            params.append(f"%{country.lower()}%")
        rows = conn.execute(
            f"SELECT source, title, description, lat, lon, country, raw_ts_utc, extra "
            f"FROM events "
            f"WHERE release_ts_utc <= ? AND raw_ts_utc >= ? AND source IN ({placeholders})"
            f"{country_clause} "
            f"ORDER BY raw_ts_utc DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _parse_extra(event: dict) -> dict:
    try:
        return json.loads(event.get("extra") or "{}")
    except Exception:
        return {}
