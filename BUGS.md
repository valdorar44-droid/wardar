# BUGS.md — Wardar

_Add bugs here. Mark fixed with date. Never delete._

---

## 2026-04-03 — Position ticks / military no-delay — INTENTIONAL DESIGN (NOT A BUG)

- Military aircraft data comes from public ADS-B transponder feeds. Delay was reduced from 24h → 5 min and then intentionally removed entirely for live WebSocket pushes.
- `_tick_adsb/ais/tle` broadcasting raw positions is by design — same for `get_released_positions_sampled` skipping release filter for military_flag=1 rows.
- **Decision by Sean, 2026-04-03.** Do not re-add a delay here.

---

## 2026-04-03 — AIS maritime data never arriving — ShipType always None (FIXED)

- **Symptom:** AIS buffer always empty — military, government, tanker vessels never appeared on the map despite `AISSTREAM_API_KEY` set and `ENABLE_AIS=true` in Railway.
- **Root cause:** `PositionReport` messages (AIS types 1/2/3/18) do NOT carry ShipType in the AIS protocol itself. `meta.get("ShipType")` returns `None` for almost all messages. `_is_accepted(None, lat, lon)` → always `(False, 0)` → every vessel dropped silently.
- **Fix:**
  1. Added `ShipStaticData` to `FilterMessageTypes` in the aisstream.io subscribe message so the API sends static broadcasts (type 24/5) which DO carry ShipType.
  2. Added `_mmsi_type_cache: dict[str, int]` (MMSI→ship_type) populated by new `_cache_static()` on every ShipStaticData message.
  3. `_norm_ais` falls back to `_mmsi_type_cache.get(mmsi)` when `MetaData.ShipType` is None.
  4. ShipStaticData messages are cache-only — no position emitted for them.

---

## 2026-04-03 — `/api/v1/alerts` always returned empty (FIXED)

- **Symptom:** The public API alerts endpoint returned `{"count": 0, "data": []}` always.
- **Cause:** Query used `source='alert'` — that source never exists. Alert events are stored under specific source names (`dark_vessel`, `convergence`, `nuclear_threat`, etc.).
- **Fix:** Query now uses `source IN ('dark_vessel','convergence','nuclear_threat','pipeline_threat','vessel_spoof','transponder_loss','route_dev','gpsjam_dark','firms_usgs')`.

---

## 2026-04-03 — WebSocket never recovered from network errors (FIXED)

- **Symptom:** If the WebSocket connection dropped due to a network error (not a clean close), the client showed "DISCONNECTED" permanently with no reconnection attempts.
- **Cause:** `ws.onerror` called `setConnStatus(false)` but did NOT call `scheduleReconnect()`. `ws.onclose` correctly called both.
- **Fix:** `ws.onerror` now calls `scheduleReconnect()` — `dashboard/map.html`.

---

## 2026-03-27 — WebSocket live updates missing for some sources (FIXED)

- **Symptom:** Live pushes for sources (e.g. polymarket, shodan, ioda) not appearing; snapshot still had data.
- **Cause:** Client `subscribe` used a **subset** of `domains`; server dropped broadcasts whose `sources` did not overlap.
- **Fix:** `dashboard/map.html` sends `domains: []` on connect so `subscribed_sources` stays empty and the server skips the filter (`api/server.py` `_send`). Documented in `_send` docstring.

---
