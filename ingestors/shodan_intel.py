"""Shodan conflict zone infrastructure monitoring ingestor.

Requires a paid SHODAN_API_KEY ($69/mo Membership plan or higher).
Queries Shodan for exposed industrial control systems and internet-facing
infrastructure in active conflict zones.

Only runs if C.ENABLE_SHODAN is True and C.SHODAN_API_KEY is set.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings as C

_API_BASE = "https://api.shodan.io/shodan/host/search"

# ── Conflict zone queries ─────────────────────────────────────────────────────
# Each entry: (query_string, default_country_if_no_geo)
_QUERIES: list[tuple[str, str]] = [
    ("country:UA org:military",         "Ukraine"),
    ("country:IL military",             "Israel"),
    ("country:RU port:502 OR port:102", "Russia"),   # ICS/SCADA — Modbus + S7
    ("country:SY",                      "Syria"),
    ("country:YE",                      "Yemen"),
]


def _flag(country: str) -> str:
    """Return a Unicode flag emoji for common conflict-zone countries."""
    flags = {
        "Ukraine":  "UA",
        "Israel":   "IL",
        "Russia":   "RU",
        "Syria":    "SY",
        "Yemen":    "YE",
    }
    code = flags.get(country, "")
    if code and len(code) == 2:
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())
    return "[!]"


async def fetch() -> list[dict]:
    """Fetch Shodan conflict zone data and return Wardar events."""
    if not C.ENABLE_SHODAN or not C.SHODAN_API_KEY:
        return []

    try:
        from core.engine import log, log_warn
    except Exception:
        return []

    key = C.SHODAN_API_KEY
    events: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for query, default_country in _QUERIES:
        try:
            params = {
                "key":    key,
                "query":  query,
                "minify": "true",
            }
            async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Wardar/0.1"}) as client:
                r = await client.get(_API_BASE, params=params)

            if r.status_code == 401:
                log_warn("shodan: invalid API key")
                return []
            if r.status_code == 402:
                log_warn("shodan: API plan does not support this query")
                continue
            if r.status_code != 200:
                log_warn(f"shodan: query '{query}' HTTP {r.status_code}")
                continue

            data = r.json()
            matches = data.get("matches") or []

            for host in matches:
                try:
                    lat = (host.get("location") or {}).get("latitude")
                    lon = (host.get("location") or {}).get("longitude")
                    if lat is None or lon is None:
                        continue

                    ip       = host.get("ip_str") or ""
                    org      = host.get("org") or host.get("isp") or "Unknown Org"
                    ports    = host.get("ports") or []
                    vulns    = list((host.get("vulns") or {}).keys())
                    country  = (host.get("location") or {}).get("country_name") or default_country
                    hostnames = host.get("hostnames") or []

                    flag = _flag(country)
                    port_str = ",".join(str(p) for p in ports[:10])
                    title = f"{flag} Infrastructure: {org} :{port_str}" if port_str else f"{flag} Infrastructure: {org}"

                    desc_parts = [
                        f"IP: {ip}",
                        f"Org: {org}",
                    ]
                    if ports:
                        desc_parts.append(f"Ports: {port_str}")
                    if vulns:
                        desc_parts.append(f"CVEs: {', '.join(vulns[:5])}")
                    if hostnames:
                        desc_parts.append(f"Hosts: {', '.join(hostnames[:3])}")
                    desc_parts.append(f"Query: {query}")
                    description = " | ".join(desc_parts)

                    extra: dict[str, Any] = {
                        "ip":        ip,
                        "org":       org,
                        "ports":     ports[:20],
                        "vulns":     vulns[:10],
                        "query":     query,
                        "hostnames": hostnames[:5],
                    }

                    events.append({
                        "source":      "shodan",
                        "title":       title[:200],
                        "description": description[:1000],
                        "lat":         float(lat),
                        "lon":         float(lon),
                        "country":     country,
                        "category":    "cyber_infra",
                        "url":         f"https://www.shodan.io/host/{ip}",
                        "raw_ts_utc":  now,
                        "extra":       json.dumps(extra),
                    })
                except Exception:
                    continue

            log(f"shodan: query '{query}' → {len(matches)} hosts, {len([e for e in events if query in (json.loads(e['extra']).get('query',''))])} with geo")

        except Exception as exc:
            try:
                log_warn(f"shodan: query '{query}' error: {exc}")
            except Exception:
                pass

        # Rate limit — 0.5s between queries
        await asyncio.sleep(0.5)

    log(f"shodan: {len(events)} total geo-located infrastructure events")
    return events
