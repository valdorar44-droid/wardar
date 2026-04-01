"""
Wardar — Phase 11: Entity Identity Graph

Resolves tracked positions into canonical entity records and links
OSINT event mentions back to those entities.

Resolution rules (MVP):
  1. Exact callsign match → same entity
  2. MMSI / ICAO24 from position `extra` JSON treated as alias
  3. One entity per unique callsign (cross-source merging via aliases)
"""
from __future__ import annotations
import json, uuid as _uuid_mod
from db import store as DB
from core.engine import log, log_warn


def _make_uuid() -> str:
    return str(_uuid_mod.uuid4())


def _ofac_flag(callsign: str, country: str, extra_json: str) -> int:
    """Check OFAC sanctions table for this entity. Non-blocking best-effort."""
    try:
        conn = DB.get_conn()
        # OFAC SDN list is stored in events with source='ofac_sanctions'
        row = conn.execute(
            "SELECT 1 FROM events WHERE source='ofac_sanctions' AND "
            "(title LIKE ? OR description LIKE ?) LIMIT 1",
            (f"%{callsign}%", f"%{callsign}%")
        ).fetchone()
        return 1 if row else 0
    except Exception:
        return 0


def resolve_or_create(position: dict) -> str | None:
    """
    Given a normalized position dict, find or create the entity record.
    Returns the entity UUID, or None if callsign is empty.
    """
    callsign = (position.get("callsign") or "").strip()
    if not callsign:
        return None

    source   = position.get("source", "")
    ptype    = position.get("type", "")
    country  = position.get("country", "")
    mil_flag = int(position.get("military_flag") or 0)
    lat      = position.get("lat")
    lon      = position.get("lon")

    # Parse extra for MMSI / ICAO24 — these become aliases
    extra = {}
    try:
        extra = json.loads(position.get("extra") or "{}")
    except Exception:
        pass
    aliases = []
    for key in ("mmsi", "icao24", "icao", "tail"):
        val = extra.get(key)
        if val and str(val) != callsign:
            aliases.append(str(val))

    # Check existing entity
    existing = DB.get_entity_by_callsign(callsign)
    if existing:
        ent_uuid = existing["uuid"]
    else:
        # Check if any alias resolves to an existing entity
        ent_uuid = None
        for alias in aliases:
            hit = DB.get_entity_by_callsign(alias)
            if hit:
                ent_uuid = hit["uuid"]
                existing = hit  # treat alias-resolved entity as existing
                break
        if not ent_uuid:
            ent_uuid = _make_uuid()

    # When resolved via alias, the existing row's callsign is the upsert key.
    # Add the incoming callsign as an alias so the cross-reference is preserved.
    # Using a different callsign with the same UUID would violate the PK constraint.
    upsert_callsign = existing["callsign"] if existing else callsign

    # Build updated source_refs
    try:
        refs = json.loads(existing["source_refs"]) if existing else []
    except Exception:
        refs = []
    ref = {"source": source, "callsign": callsign}
    if ref not in refs:
        refs.append(ref)
        if len(refs) > 20:
            refs = refs[-20:]

    # Merge aliases — include the incoming callsign when it differs from upsert key
    try:
        existing_aliases = json.loads(existing["aliases"]) if existing else []
    except Exception:
        existing_aliases = []
    extra_aliases = aliases + ([callsign] if upsert_callsign != callsign else [])
    merged_aliases = list(set(existing_aliases + extra_aliases))[:30]

    ofac = _ofac_flag(callsign, country, position.get("extra") or "{}")

    DB.upsert_entity({
        "uuid":         ent_uuid,
        "callsign":     upsert_callsign,
        "aliases":      json.dumps(merged_aliases),
        "source_refs":  json.dumps(refs),
        "type":         ptype,
        "country":      country,
        "military_flag": mil_flag,
        "ofac_flag":    ofac,
        "lat":          lat,
        "lon":          lon,
    })
    return ent_uuid


# ── Event mention linking ─────────────────────────────────────────────────────

# Minimum callsign length to attempt mention matching (avoid matching "US", "F" etc.)
_MIN_CALLSIGN_LEN = 4

def link_event_mentions(events: list[dict]) -> int:
    """
    Scan a batch of events for mentions of known entity callsigns.
    Inserts entity_mention records for confirmed matches.
    Returns number of links created.
    """
    if not events:
        return 0

    # Load all known callsigns from entity graph
    try:
        conn = DB.get_conn()
        rows = conn.execute(
            "SELECT uuid, callsign, aliases FROM entities WHERE LENGTH(callsign) >= ?",
            (_MIN_CALLSIGN_LEN,)
        ).fetchall()
    except Exception as exc:
        log_warn(f"entity_graph.link_event_mentions: DB read failed: {exc}")
        return 0

    # Build lookup: callsign/alias → uuid
    lookup: dict[str, str] = {}
    for row in rows:
        lookup[row["callsign"].upper()] = row["uuid"]
        try:
            for alias in json.loads(row["aliases"] or "[]"):
                if len(alias) >= _MIN_CALLSIGN_LEN:
                    lookup[alias.upper()] = row["uuid"]
        except Exception:
            pass

    if not lookup:
        return 0

    linked = 0
    for evt in events:
        event_id = evt.get("id")
        if not event_id:
            continue
        text = f"{evt.get('title','') or ''} {evt.get('description','') or ''}".upper()
        for token, ent_uuid in lookup.items():
            if token in text:
                try:
                    DB.insert_entity_mention(ent_uuid, event_id,
                                             confidence=1.0, match_type="exact")
                    linked += 1
                except Exception:
                    pass

    return linked


async def run_resolution_tick(positions: list[dict]) -> int:
    """
    Process a batch of positions through the entity graph.
    Called from engine.py after each ingestor tick.
    Returns number of entities resolved/updated.
    """
    resolved = 0
    for p in positions:
        try:
            result = resolve_or_create(p)
            if result:
                resolved += 1
        except Exception as exc:
            log_warn(f"entity_graph.resolve: {exc}")
    return resolved
