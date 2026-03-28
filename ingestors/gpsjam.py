"""GPSJam / GPS interference ingestor

Fetches H3-indexed GPS jamming data from gpsjam.org.
Data source: https://gpsjam.org/data/YYYY-MM-DD-h3_4.csv
Format: hex,count_good_aircraft,count_bad_aircraft
Free, no API key required.

Interference level thresholds (matching gpsjam.org legend):
  < 2%  → skip (low, too noisy)
  2-8%  → level 2 POSSIBLE
  ≥ 8%  → level 3 LIKELY/SEVERE

Returns list of event dicts. Never raises.
"""
from __future__ import annotations
import csv, io, json
from datetime import datetime, timezone, timedelta

import httpx

from config import settings as C

_BASE_URL = "https://gpsjam.org/data"
_H3_RES   = 4  # site uses resolution 4

_JAM_LABELS = {
    2: "POSSIBLE GPS JAM",
    3: "SEVERE GPS JAM",
}


def _date_str(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _h3_to_latlng(hex_id: str) -> tuple[float, float] | None:
    """Convert H3 cell index to (lat, lon) centroid. Requires h3 library."""
    try:
        import h3  # type: ignore
        lat, lon = h3.cell_to_latlng(hex_id)
        return round(lat, 4), round(lon, 4)
    except Exception:
        return None


async def fetch() -> list[dict]:
    from core.engine import log, log_warn
    if not C.ENABLE_GPSJAM:
        return []

    rows: list[dict] = []
    fetched_date = ""

    for day_offset in range(5):  # try today and up to 4 days back (gpsjam has ~2-day lag)
        date_str  = _date_str(day_offset)
        url       = f"{_BASE_URL}/{date_str}-h3_{_H3_RES}.csv"
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "Wardar/0.1"})
            if r.status_code == 200 and r.text.strip():
                reader = csv.DictReader(io.StringIO(r.text))
                rows   = list(reader)
                fetched_date = date_str
                log(f"gpsjam: fetched {len(rows)} hex cells for {date_str}")
                break
            else:
                log_warn(f"gpsjam: HTTP {r.status_code} for {date_str}")
        except Exception as exc:
            log_warn(f"gpsjam: fetch error ({date_str}): {exc}")

    if not rows:
        log_warn("gpsjam: no data available")
        return []

    results = []
    # Use data date as raw_ts_utc (noon UTC) so dedup index skips re-inserts
    # on subsequent hourly polls when data hasn't changed.
    data_ts = f"{fetched_date}T12:00:00+00:00"

    for row in rows:
        try:
            hex_id = row.get("hex", "").strip()
            good   = int(row.get("count_good_aircraft") or 0)
            bad    = int(row.get("count_bad_aircraft") or 0)
            total  = good + bad
            if total == 0 or not hex_id:
                continue

            frac = bad / total

            # Only include cells with ≥ 2% bad reception (skip noise)
            if frac < 0.02:
                continue

            # Interference level
            jam_level = 3 if frac >= 0.08 else 2
            label     = _JAM_LABELS[jam_level]

            centroid = _h3_to_latlng(hex_id)
            if centroid is None:
                continue
            lat, lon = centroid

            url_link = (
                f"https://gpsjam.org/?lat={lat:.2f}&lon={lon:.2f}&z=7&date={fetched_date}"
            )

            results.append({
                "source":      "gpsjam",
                "title":       f"EW: {label} ({frac*100:.0f}%)",
                "description": (
                    f"GPS degradation {frac*100:.0f}% of aircraft ({bad}/{total})"
                    f" | H3 cell {hex_id[:10]}"
                ),
                "lat":         lat,
                "lon":         lon,
                "country":     "",
                "category":    "ew",
                "raw_ts_utc":  data_ts,
                "url":         url_link,
                "extra":       json.dumps({
                    "jam_level": jam_level,
                    "bad_frac": round(frac, 4),
                    "count_bad": bad,
                    "count_good": good,
                    "hex": hex_id,
                    "date": fetched_date,
                }),
            })
        except Exception:
            continue

    log(f"gpsjam: {len(results)} interference zones (≥2% bad reception) from {fetched_date}")
    return results
