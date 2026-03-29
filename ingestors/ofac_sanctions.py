"""OFAC SDN sanctions list ingestor

Downloads the US Treasury OFAC Specially Designated Nationals (SDN) list XML.
Cross-references AIS vessel MMSIs and ADS-B aircraft hex codes against
sanctioned entities to flag shadow fleet / sanctions-evading assets.

Free, no API key. XML file ~5MB, downloaded daily.
Also loads EU consolidated sanctions list for combined coverage.

This ingestor works differently from others:
- refresh() downloads and caches the sanctions data
- is_sanctioned(mmsi) / is_sanctioned_aircraft(hex) are called by ais.py / adsb.py
  in future enrichment (stub for now — currently emits events for sanctioned vessels
  found in current AIS/ADS-B position store)

Returns nothing directly — cross-reference happens at alert time.
"""
from __future__ import annotations
import json, re
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings as C

_OFAC_URL = "https://www.treasury.gov/ofac/downloads/sdnlist.txt"
_OFAC_XML  = "https://www.treasury.gov/ofac/downloads/sdn.xml"
# EU consolidated list (full XML snapshot)
_EU_URL    = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"

# In-memory cache of sanctioned vessel MMSIs and aircraft hex/reg
_SANCTIONED_MMSI: set[str] = set()
_SANCTIONED_HEX:  set[str] = set()
_SANCTIONED_NAMES: dict[str, str] = {}  # mmsi/hex → entity name
_last_refresh: Optional[datetime] = None


async def refresh() -> None:
    """Download and parse OFAC SDN list. Called daily by engine."""
    from core.engine import log, log_warn
    global _last_refresh

    log("ofac: downloading SDN list...")
    mmsi_set: set[str] = set()
    hex_set:  set[str] = set()
    names:    dict[str, str] = {}

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(_OFAC_XML, headers={"User-Agent": "Wardar/0.1"})
        if r.status_code != 200:
            log_warn(f"ofac: SDN XML HTTP {r.status_code} — trying text fallback")
            await _load_text_fallback(log, log_warn, mmsi_set, hex_set, names)
        else:
            _parse_sdn_xml(r.text, mmsi_set, hex_set, names)
    except Exception as exc:
        log_warn(f"ofac: SDN download error: {exc}")

    _SANCTIONED_MMSI.clear()
    _SANCTIONED_MMSI.update(mmsi_set)
    _SANCTIONED_HEX.clear()
    _SANCTIONED_HEX.update(hex_set)
    _SANCTIONED_NAMES.clear()
    _SANCTIONED_NAMES.update(names)
    _last_refresh = datetime.now(timezone.utc)

    log(f"ofac: loaded {len(_SANCTIONED_MMSI)} sanctioned vessels, "
        f"{len(_SANCTIONED_HEX)} aircraft/entities")


def _parse_sdn_xml(xml_text: str, mmsi_set: set, hex_set: set, names: dict) -> None:
    """Parse OFAC SDN XML for vessel MMSIs and aircraft registrations."""
    # MMSI pattern: 9-digit number in ID fields
    for m in re.finditer(r'<id[^>]*idType="MMSI"[^>]*>(\d{9})</id>', xml_text, re.IGNORECASE):
        mmsi = m.group(1)
        mmsi_set.add(mmsi)

    # Also grab MMSIs from remarks/comments
    for m in re.finditer(r'MMSI[:\s#]+(\d{9})', xml_text):
        mmsi_set.add(m.group(1))

    # Aircraft registration / ICAO hex codes
    for m in re.finditer(r'<id[^>]*idType="Aircraft[^"]*"[^>]*>([^<]+)</id>', xml_text, re.IGNORECASE):
        val = m.group(1).strip().upper()
        if val:
            hex_set.add(val)

    # Try to build name index
    # Simplified: extract sdnEntry blocks with firstName+lastName or title
    for block in re.finditer(r'<sdnEntry>(.*?)</sdnEntry>', xml_text, re.DOTALL):
        b = block.group(1)
        uid_m  = re.search(r'<uid>(\d+)</uid>', b)
        name_m = re.search(r'<lastName>([^<]+)</lastName>', b)
        if uid_m and name_m:
            names[uid_m.group(1)] = name_m.group(1)


async def _load_text_fallback(log, log_warn, mmsi_set, hex_set, names) -> None:
    """Fallback: parse OFAC plain text SDN list."""
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(_OFAC_URL, headers={"User-Agent": "Wardar/0.1"})
        if r.status_code == 200:
            for m in re.finditer(r'MMSI[:\s#]+(\d{9})', r.text):
                mmsi_set.add(m.group(1))
            log(f"ofac: text fallback — {len(mmsi_set)} MMSIs")
    except Exception as exc:
        log_warn(f"ofac: text fallback error: {exc}")


def is_sanctioned_vessel(mmsi: str) -> bool:
    """Check if an MMSI is on the OFAC SDN list."""
    return mmsi in _SANCTIONED_MMSI


def is_sanctioned_aircraft(hex_code: str) -> bool:
    """Check if an aircraft hex/registration is sanctioned."""
    return hex_code.upper() in _SANCTIONED_HEX


def get_sanctioned_count() -> dict:
    return {"vessels": len(_SANCTIONED_MMSI), "aircraft": len(_SANCTIONED_HEX)}


def get_last_refresh() -> Optional[str]:
    return _last_refresh.isoformat() if _last_refresh else None
