"""Wardar — WW3 Risk Meter AI generator

Runs once per calendar day (UTC midnight check in engine.py).
Pulls a snapshot of today's signals from the DB and asks Claude to
estimate the current global escalation index on a 0-100 scale.

This is an ENTERTAINMENT/AWARENESS indicator — not military intelligence.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

from config import settings as C
from db import store as DB

_LEVELS = [
    (86, "CRITICAL"),
    (71, "SEVERE"),
    (51, "HIGH"),
    (31, "ELEVATED"),
    (16, "LOW"),
    (0,  "NOMINAL"),
]

def score_to_level(score: int) -> str:
    for threshold, label in _LEVELS:
        if score >= threshold:
            return label
    return "NOMINAL"


async def generate_ww3_score() -> dict:
    """Query available data and ask Claude for a WW3 escalation score."""
    if not C.ANTHROPIC_API_KEY:
        return {"error": "no_api_key"}

    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()

    conn = DB.get_conn()

    # ── Collect signal snapshot ───────────────────────────────────────────────

    # Recent alerts (dark vessel, spoofing, transponder loss, etc.)
    alert_rows = conn.execute(
        "SELECT source, title, description, country FROM events "
        "WHERE source IN ('dark_vessel','convergence','nuclear_threat','pipeline_threat',"
        "'vessel_spoof','transponder_loss','firms_usgs','gpsjam_dark','route_dev','proximity') "
        "AND raw_ts_utc >= ? ORDER BY raw_ts_utc DESC LIMIT 40",
        (cutoff_24h,)
    ).fetchall()
    alerts = [dict(r) for r in alert_rows]

    # Active conflict events in last 24h
    conflict_rows = conn.execute(
        "SELECT source, title, country, category FROM events "
        "WHERE source IN ('acled','gdelt','osint_news') AND raw_ts_utc >= ? LIMIT 60",
        (cutoff_24h,)
    ).fetchall()
    conflicts = [dict(r) for r in conflict_rows]

    # Breaking news
    news_rows = conn.execute(
        "SELECT title, description FROM events "
        "WHERE source='breaking_news' AND raw_ts_utc >= ? "
        "ORDER BY raw_ts_utc DESC LIMIT 20",
        (cutoff_24h,)
    ).fetchall()
    news = [dict(r) for r in news_rows]

    # GPSJam / EW interference events
    ew_rows = conn.execute(
        "SELECT country, description FROM events "
        "WHERE source='gpsjam' AND raw_ts_utc >= ? LIMIT 20",
        (cutoff_24h,)
    ).fetchall()
    ew = [dict(r) for r in ew_rows]

    # Military aircraft count (released)
    mil_count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE military_flag=1 AND release_ts_utc <= ?",
        (now.isoformat(),)
    ).fetchone()[0]

    # Seismic / nuclear test indicators
    seismic_rows = conn.execute(
        "SELECT description, country FROM events "
        "WHERE source='usgs' AND raw_ts_utc >= ? LIMIT 10",
        (cutoff_24h,)
    ).fetchall()
    seismic = [dict(r) for r in seismic_rows]

    # Previous score for context
    prev = DB.get_ww3_meter()

    # ── Build prompt ─────────────────────────────────────────────────────────

    def _fmt(rows: list[dict], keys: list[str], cap: int = 15) -> str:
        if not rows:
            return "  (none)"
        lines = []
        for r in rows[:cap]:
            parts = [str(r.get(k) or "—") for k in keys if r.get(k)]
            lines.append("  • " + " | ".join(parts))
        return "\n".join(lines)

    prev_note = ""
    if prev:
        prev_note = f"\nPrevious reading (for trend context): {prev['score']}/100 — {prev['level']} ({prev['generated_at'][:10]})"

    prompt = f"""You are the Wardar Global Escalation Index engine.

Today is {now.strftime('%Y-%m-%d %H:%M UTC')}.
Your task: produce a DAILY escalation score estimating the global risk of a World War 3 scenario erupting within the next 90 days.{prev_note}

This is an OSINT-based awareness indicator for a public intelligence dashboard — clearly labeled as entertainment/informational. Be honest and analytical.

──── SIGNAL SNAPSHOT (last 24h) ────

DARK INTELLIGENCE ALERTS ({len(alerts)}):
{_fmt(alerts, ['source','title','country'])}

CONFLICT EVENTS ({len(conflicts)}):
{_fmt(conflicts, ['source','title','country'])}

BREAKING NEWS ({len(news)}):
{_fmt(news, ['title'])}

ELECTRONIC WARFARE / GPS JAM ({len(ew)} zones):
{_fmt(ew, ['country','description'])}

MILITARY AIRCRAFT TRACKED: {mil_count}

SEISMIC EVENTS ({len(seismic)}):
{_fmt(seismic, ['description','country'])}

──── OUTPUT FORMAT ────

Respond with ONLY valid JSON, no markdown:
{{
  "score": <integer 0-100>,
  "level": "<NOMINAL|LOW|ELEVATED|HIGH|SEVERE|CRITICAL>",
  "assessment": "<2-3 sentence plain-English escalation assessment>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]
}}

Scoring guide:
0-15  NOMINAL   — business-as-usual tensions, no acute triggers
16-30 LOW       — minor escalatory incidents, isolated
31-50 ELEVATED  — active conflict zones, moderate inter-state tension
51-70 HIGH      — major power confrontation indicators, nuclear posturing signals
71-85 SEVERE    — direct superpower incidents, nuclear deployments
86-100 CRITICAL — imminent multi-state war indicators

Be conservative. Historical baseline should sit around 25-40 given ongoing conflicts.
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
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        score = int(result.get("score", 30))
        score = max(0, min(100, score))
        level = score_to_level(score)  # always derive from score, don't trust model's label
        assessment = str(result.get("assessment", ""))[:1000]
        factors = [str(f)[:200] for f in (result.get("key_factors") or [])[:5]]

        DB.upsert_ww3_meter(score, level, assessment, factors, C.AI_MODEL)
        return {
            "score": score,
            "level": level,
            "assessment": assessment,
            "key_factors": factors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": C.AI_MODEL,
        }
    except json.JSONDecodeError as exc:
        return {"error": f"parse_error: {exc}"}
    except Exception as exc:
        return {"error": str(exc)}
