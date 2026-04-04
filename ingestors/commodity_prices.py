"""Commodity price ingestor — oil/gas/gold spot prices

Uses Yahoo Finance public API (no key required).

Tracked instruments:
  BZ=F  — Brent Crude ($/bbl) — global oil benchmark
  CL=F  — WTI Crude ($/bbl)   — US benchmark
  NG=F  — Natural Gas ($/MMBtu)
  GC=F  — Gold ($/oz)

Returns a dict via fetch_prices() (NOT the events list pattern).
Called directly by the /api/prices endpoint in server.py.

Also exports fetch() returning price-spike events for the engine
when prices cross alert thresholds (e.g., Brent >$130 = Hormuz signal).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import httpx

from config import settings as C

_YF_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Wardar/1.0)",
    "Accept":     "application/json",
}

_SYMBOLS = ["BZ=F", "CL=F", "NG=F", "GC=F"]
_LABELS  = {
    "BZ=F": "Brent Crude",
    "CL=F": "WTI Crude",
    "NG=F": "Natural Gas",
    "GC=F": "Gold",
}
_UNITS = {
    "BZ=F": "$/bbl",
    "CL=F": "$/bbl",
    "NG=F": "$/MMBtu",
    "GC=F": "$/oz",
}

# Alert thresholds — emit an event when price exceeds these
_ALERT_HIGH = {
    "BZ=F": 120.0,   # Brent >$120 = energy crisis signal
    "CL=F": 115.0,   # WTI >$115
    "NG=F": 8.0,     # NG >$8/MMBtu = supply crunch
}

# In-memory cache — avoids hammering Yahoo Finance
_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL_SEC = 300  # 5 minutes


async def fetch_prices() -> dict:
    """Return latest commodity prices as a dict. Cached for 5 minutes.

    Returns:
        {
          "BZ=F": {"label": "Brent Crude", "price": 126.45, "unit": "$/bbl",
                   "change": 2.1, "change_pct": 1.69, "ts": "2026-04-03T12:00:00+00:00"},
          ...
        }
    """
    global _cache, _cache_ts
    from core.engine import log, log_warn
    import time

    now_ts = time.time()
    if _cache and (now_ts - _cache_ts) < _CACHE_TTL_SEC:
        return _cache

    symbols_str = ",".join(_SYMBOLS)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                _YF_QUOTE_URL,
                params={"symbols": symbols_str, "fields": "regularMarketPrice,regularMarketChange,regularMarketChangePercent,shortName"},
                headers=_YF_HEADERS,
            )
        if r.status_code != 200:
            log_warn(f"commodity_prices: HTTP {r.status_code}")
            return _cache or {}

        quotes = r.json().get("quoteResponse", {}).get("result", [])
    except Exception as exc:
        log_warn(f"commodity_prices: fetch error: {exc}")
        return _cache or {}

    result: dict = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for q in quotes:
        sym = q.get("symbol", "")
        if sym not in _SYMBOLS:
            continue
        price  = q.get("regularMarketPrice") or 0.0
        change = q.get("regularMarketChange") or 0.0
        pct    = q.get("regularMarketChangePercent") or 0.0
        result[sym] = {
            "label":      _LABELS.get(sym, sym),
            "price":      round(float(price), 2),
            "unit":       _UNITS.get(sym, ""),
            "change":     round(float(change), 2),
            "change_pct": round(float(pct), 2),
            "ts":         now_iso,
        }

    if result:
        _cache    = result
        _cache_ts = now_ts
        log(f"commodity_prices: {len(result)} symbols fetched")

    return result


async def fetch() -> list[dict]:
    """Emit price-spike events when oil crosses alert thresholds.

    These show up in the OSINT event stream to contextualise Hormuz/supply events.
    """
    if not C.ENABLE_COMMODITY_PRICES:
        return []

    prices = await fetch_prices()
    if not prices:
        return []

    events = []
    now = datetime.now(timezone.utc).isoformat()

    for sym, threshold in _ALERT_HIGH.items():
        data = prices.get(sym)
        if not data:
            continue
        price = data.get("price", 0.0)
        if price < threshold:
            continue
        label = data["label"]
        unit  = data["unit"]
        pct   = data.get("change_pct", 0.0)
        sign  = "+" if pct >= 0 else ""
        events.append({
            "source":      "commodity_prices",
            "title":       f"PRICE ALERT: {label} at {price:.2f} {unit} ({sign}{pct:.1f}%)",
            "description": (
                f"{label} has crossed the ${threshold} alert threshold. "
                f"Current: {price:.2f} {unit} | "
                f"Hormuz closure / Iran war impact on energy markets."
            ),
            "lat":         26.56,   # Strait of Hormuz centroid
            "lon":         56.25,
            "country":     "International Waters",
            "category":    "economic",
            "raw_ts_utc":  now,
            "url":         f"https://finance.yahoo.com/quote/{sym}/",
            "extra":       json.dumps({
                "symbol":     sym,
                "price":      price,
                "threshold":  threshold,
                "change_pct": pct,
                "unit":       unit,
            }),
        })

    return events
