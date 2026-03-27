# BUGS.md — Wardar

_Add bugs here. Mark fixed with date. Never delete._

---

## 2026-03-27 — WebSocket live updates missing for some sources (FIXED)

- **Symptom:** Live pushes for sources (e.g. polymarket, shodan, ioda) not appearing; snapshot still had data.
- **Cause:** Client `subscribe` used a **subset** of `domains`; server dropped broadcasts whose `sources` did not overlap.
- **Fix:** `dashboard/map.html` sends `domains: []` on connect so `subscribed_sources` stays empty and the server skips the filter (`api/server.py` `_send`). Documented in `_send` docstring.

---
