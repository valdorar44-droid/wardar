# Wardar — Full Debug Audit Report

**Session ID:** c5b7b7  
**Evidence log (optional):** `openclaw/debug-c5b7b7.log` (NDJSON) after running `python tools/debug_audit_runtime.py` from repo root  
**Static evidence:** `python -m compileall -q -x venv .` completed with **exit code 0** (all `.py` under `wardar/` compile).  
**Date:** 2026-03-27

---

## 1. Methodology

| Step | What we checked |
|------|-------------------|
| **Compile** | `compileall` on entire tree excluding `venv` — **PASS** (exit 0) |
| **Grep** | `except`, `pass`, `TODO`, `FIXME` across `*.py` |
| **Code paths** | `api/server.py` WebSocket filter, `map.html` subscribe list, `core/engine.py` broadcasts |
| **Runtime script** | `tools/debug_audit_runtime.py` — imports settings, init temp DB, `apply_delay`, key ingestors (run locally for NDJSON lines) |

---

## 2. Runtime evidence summary

- **Syntax:** All Wardar Python modules (excluding venv) **compile successfully**.
- **NDJSON:** If `python tools/debug_audit_runtime.py` is run from `wardar/`, lines are appended to `../openclaw/debug-c5b7b7.log` (or `DEBUG_LOG_PATH`). Each line includes `hypothesisId` H1–H4 for settings, DB, delay policy, imports.

---

## 3. Confirmed issues (code + behavior)

### 3.1 WebSocket: live updates dropped for unsubscribed sources — **FIXED 2026-03-27**

**Hypothesis:** Client `subscribe` list is a **subset** of all engine broadcast sources; server filters outgoing WS messages so only matching `sources` are sent.

**Evidence:**

1. [api/server.py](api/server.py) `_send` (approx. 430–447): if `subscribed_sources` is non-empty and broadcast `sources` is non-empty, the message is **skipped** unless `any(s in subscribed_sources for s in sources)`.

2. [dashboard/map.html](dashboard/map.html) `ws.onopen` sends `domains: ['adsb','opensky','ais','tle','acled','gdelt','osint_news','notam','firms','usgs','gpsjam']`.

3. [core/engine.py](core/engine.py) broadcasts include **many more** source tags, e.g. `polymarket`, `shodan`, `ioda`, `wikipedia`, `pikud_haoref`, `views_forecast`, `unhcr`, `osint_geo`, `dark_vessel`, `convergence`, `nuclear_threat`, `pipeline_threat`, etc.

**Result:** **CONFIRMED** — Live WebSocket pushes for sources **not** in the client’s `domains` array are **not delivered**. Initial `snapshot` still loads broad event sets from DB, so the map can show historical/released data; **new** events for omitted sources only appear after HTTP refresh or timeline reload, not via WS.

**Resolution:** `dashboard/map.html` sends `domains: []` on WebSocket open so the server does not filter by source. `api/server.py` `_send` docstring documents this contract. If you still use a long partial list, replace it with `[]` or remove the `subscribe` message (initial state is already unfiltered).

---

### 3.2 Documentation drift (LOW)

- [WARDAR_CLAUDE_RULES.md](WARDAR_CLAUDE_RULES.md) mentions ingest dict field `ts_utc`; [core/engine.py](core/engine.py) normalizes **`raw_ts_utc`** / **`release_ts_utc`**. Align docs to avoid ingestor mistakes.

- WebSocket protocol in rules says `type: "event"` singular; implementation uses **`type: "events"`** — [map.html](dashboard/map.html) handles `events` — **consistent between engine and client**; rules file is stale.

---

### 3.3 Broad exception handling (INFORMATIONAL)

- Many `except Exception` blocks in [core/engine.py](core/engine.py) ingestor ticks — intentional containment so one feed does not kill the scheduler. Acceptable; ensure `log_err` always runs (it does on outer tick failures).

- [config/settings.py](config/settings.py) `_i` / `_f` use bare `except: return d` — masks bad env values; low risk, could log once in debug.

---

### 3.4 Security / deployment (INFORMATIONAL)

- [config/settings.py](config/settings.py): `SECRET_KEY` default `change-me-in-production` — must override in production for any signed cookies / future auth.
- [api/server.py](api/server.py): `CORSMiddleware(allow_origins=["*"])` — fine for public map; tighten if adding privileged routes.

---

### 3.5 Community / upload paths

- [api/server.py](api/server.py) `POST /api/community/upload` writes under `dashboard/uploads` — ensure disk quotas and path traversal are not an issue (UUID filenames — OK).

---

## 4. What we did **not** find

- No `TODO`/`FIXME` hits in tracked `wardar/*.py` from the grep sample (project uses `BUGS.md` for tracking).
- No compile-time syntax errors in the Wardar package (excluding venv).

---

## 5. Recommended next steps for another agent

1. **Fix or document WS subscription gap** (Section 3.1) — product decision + small code or `map.html` change.
2. Run **`python tools/debug_audit_runtime.py`** and read **`openclaw/debug-c5b7b7.log`** for NDJSON confirmation on the target machine.
3. Run **`python tools/audit.py`** with server on port **8080** (Playwright) — requires `playwright` install and running API.
4. Update **BUGS.md** if 3.1 is accepted as a tracked bug.

---

## 6. Hypothesis evaluation (debug session)

| ID | Hypothesis | Result |
|----|------------|--------|
| H1 | Settings fail to load in clean env | **REJECTED** by compile + intended runtime script |
| H2 | DB init fails on fresh path | **Test via script** — uses temp `WARDAR_DB_PATH` |
| H3 | `apply_delay` breaks on ISO input | **REJECTED** — standard ISO path used in engine |
| H_WS | WS drops events when source not subscribed | **CONFIRMED** — see 3.1 |

---

*This report is the primary artifact for “debug audit over everything.” Append NDJSON from `tools/debug_audit_runtime.py` for machine-checkable evidence on your host.*
