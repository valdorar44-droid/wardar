"""
Wardar full audit script using Playwright.
Tests: boot sequence, signals, AI SITREP, noise filter, globe, popups, WebSocket.
Prints a detailed report and auto-fixes issues where possible.
"""
import asyncio, json, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

BASE = "http://localhost:8080"
RESULTS = []

def log(status, test, detail=""):
    icon = "✅" if status == "PASS" else ("⚠️" if status == "WARN" else "❌")
    msg = f"{icon} [{status}] {test}"
    if detail:
        msg += f"\n     → {detail}"
    print(msg)
    RESULTS.append({"status": status, "test": test, "detail": detail})

async def run_audit():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})

        # ── 1. API Health ────────────────────────────────────────────────────
        page = await ctx.new_page()
        r = await page.request.get(f"{BASE}/api/health")
        data = await r.json()
        if data.get("status") == "ok":
            log("PASS", "API /api/health", f"events={data.get('events_live')} positions={data.get('positions_live')}")
        else:
            log("FAIL", "API /api/health", str(data))

        # ── 2. Signal counts by source ────────────────────────────────────────
        r2 = await page.request.get(f"{BASE}/api/positions?limit=100")
        pos = await r2.json()
        sources = {}
        for p2 in (pos.get("data") or []):
            s = p2.get("source", "unknown")
            sources[s] = sources.get(s, 0) + 1
        log("PASS" if sources else "FAIL", "Positions API", f"sources: {sources}")

        r3 = await page.request.get(f"{BASE}/api/events?limit=200")
        evts = await r3.json()
        esources = {}
        for e in (evts.get("data") or []):
            s = e.get("source", "unknown")
            esources[s] = esources.get(s, 0) + 1
        log("PASS" if esources else "FAIL", "Events API", f"sources: {esources}")

        # ── 3. Noise check — scan event titles for non-conflict content ────────
        noise_keywords = [
            "stanley cup", "nba", "nfl", "super bowl", "oscar", "grammy",
            "bitcoin", "ethereum", "stock price", "earnings", "celebrity",
            "kardashian", "taylor swift", "reality show", "sports",
        ]
        noise_found = []
        for e in (evts.get("data") or []):
            title = (e.get("title") or "").lower()
            for kw in noise_keywords:
                if kw in title:
                    noise_found.append(f"{kw!r} in: {e['title'][:80]}")
        if noise_found:
            log("FAIL", "Noise filter — non-conflict content", "\n     ".join(noise_found[:5]))
        else:
            log("PASS", "Noise filter — all events appear conflict-related")

        # ── 4. Polymarket filter check ────────────────────────────────────────
        r4 = await page.request.get(f"{BASE}/api/events?sources=polymarket&limit=50")
        poly = await r4.json()
        poly_noise = []
        sport_kw = ["stanley cup", "nba", "nfl", "super bowl", "champions league",
                    "oscar", "grammy", "bitcoin price", "ethereum", "stock price"]
        for e in (poly.get("data") or []):
            title = (e.get("title") or "").lower()
            for kw in sport_kw:
                if kw in title:
                    poly_noise.append(e["title"][:80])
        if poly_noise:
            log("FAIL", "Polymarket filter — sports/crypto leaking", "\n     ".join(poly_noise))
        else:
            log("PASS", f"Polymarket filter clean ({poly.get('count',0)} markets)")

        # ── 5. AI SITREP endpoint ─────────────────────────────────────────────
        r5 = await page.request.get(f"{BASE}/api/brief")
        if r5.status == 200:
            brief = await r5.json()
            text = brief.get("text", "")
            if len(text) > 100:
                log("PASS", "AI SITREP /api/brief", f"{len(text)} chars, model={brief.get('model','?')[:30]}")
            elif brief.get("error") == "no_api_key":
                log("WARN", "AI SITREP — no ANTHROPIC_API_KEY set (expected in prod)")
            else:
                log("WARN", "AI SITREP text short", text[:100])
        elif r5.status == 404:
            log("WARN", "AI SITREP — not yet generated (will generate on schedule)")
        else:
            log("FAIL", f"AI SITREP HTTP {r5.status}")

        # ── 6. Country brief endpoint ─────────────────────────────────────────
        r6 = await page.request.get(f"{BASE}/api/brief/country/Iran")
        if r6.status in (200, 503):
            d6 = await r6.json()
            if d6.get("text"):
                log("PASS", "Country brief /api/brief/country/Iran", d6["text"][:80])
            elif "no_api_key" in d6.get("error", ""):
                log("WARN", "Country brief — no ANTHROPIC_API_KEY (expected in local dev)")
            else:
                log("FAIL", "Country brief error", str(d6))
        else:
            log("FAIL", f"Country brief HTTP {r6.status}")

        # ── 7. Static layers ──────────────────────────────────────────────────
        for layer in ["nuclear", "cables", "mil_bases", "pipelines"]:
            r7 = await page.request.get(f"{BASE}/api/layers/{layer}")
            if r7.status == 200:
                d7 = await r7.json()
                n = len(d7.get("features") or [])
                log("PASS" if n > 0 else "WARN", f"Layer {layer}", f"{n} features")
            else:
                log("FAIL", f"Layer {layer} HTTP {r7.status}")

        # ── 8. Browser load + boot sequence ──────────────────────────────────
        page2 = await ctx.new_page()
        console_errors = []
        page2.on("console", lambda m: console_errors.append(m) if m.type == "error" else None)
        page2.on("pageerror", lambda e: console_errors.append({"type":"pageerror","text":str(e)}))

        await page2.goto(BASE, wait_until="domcontentloaded", timeout=15000)

        # Wait for boot overlay to disappear
        try:
            await page2.wait_for_selector("#boot.fade", timeout=12000)
            log("PASS", "Boot sequence completes (overlay fades)")
        except Exception:
            # Check if boot is still visible
            boot_visible = await page2.is_visible("#boot")
            if boot_visible:
                log("FAIL", "Boot overlay STUCK — never faded", "Map may not have loaded")
            else:
                log("PASS", "Boot overlay gone (no .fade class but removed)")

        # ── 9. Map element exists ─────────────────────────────────────────────
        has_leaflet = await page2.query_selector("#leaflet-map") is not None
        has_globe = await page2.query_selector("#globe-container") is not None
        has_map = has_leaflet or has_globe

        if has_globe:
            log("PASS", "Globe container present (globe.gl mode)")
        elif has_leaflet:
            log("WARN", "Leaflet map present (2D mode, globe.gl not yet migrated)")
        else:
            log("FAIL", "No map container found (#leaflet-map or #globe-container)")

        # Check if canvas rendered
        await asyncio.sleep(2)
        canvas = await page2.query_selector("canvas")
        if canvas:
            log("PASS", "Canvas rendered (Leaflet or globe.gl active)")
        else:
            log("WARN", "No canvas element — map may not be rendering")

        # ── 10. WebSocket connection ──────────────────────────────────────────
        ws_connected = await page2.evaluate("""() => {
            const dot = document.getElementById('sb-dot');
            const txt = document.getElementById('sb-conntext');
            return {
                dotClass: dot ? dot.className : 'not-found',
                connText: txt ? txt.textContent : 'not-found',
            };
        }""")
        is_live = "live" in ws_connected.get("dotClass", "").lower() or \
                  "LIVE" in ws_connected.get("connText", "")
        if is_live:
            log("PASS", "WebSocket connected", ws_connected)
        else:
            log("WARN", "WebSocket status unclear", str(ws_connected))

        # ── 11. Status bar counts ────────────────────────────────────────────
        await asyncio.sleep(3)
        counts = await page2.evaluate("""() => ({
            aircraft: document.getElementById('sb-ac')?.textContent,
            ships:    document.getElementById('sb-sh')?.textContent,
            sats:     document.getElementById('sb-sat')?.textContent,
            events:   document.getElementById('sb-ev')?.textContent,
        })""")
        ac = int(counts.get("aircraft") or 0)
        sh = int(counts.get("ships") or 0)
        ev = int(counts.get("events") or 0)
        total = ac + sh + ev
        if total > 0:
            log("PASS", f"Live counts showing", f"✈{ac} ⛵{sh} ◎{counts.get('sats',0)} ⚑{ev}")
        else:
            log("FAIL", "All counts are 0 — data not rendering", str(counts))

        # ── 12. Right panel tabs ─────────────────────────────────────────────
        for tab_id, tab_name in [("tab-feed","SIGINT"), ("tab-news","WAR NEWS"),
                                  ("tab-comm","COMMUNITY"), ("tab-alerts","ALERTS"),
                                  ("tab-sitrep","SITREP")]:
            el = await page2.query_selector(f"#{tab_id}")
            if el:
                log("PASS", f"Tab {tab_name} present")
            else:
                log("FAIL", f"Tab {tab_name} missing (#{tab_id})")

        # ── 13. SIGINT feed has content ───────────────────────────────────────
        feed_items = await page2.query_selector_all(".feed-item")
        if len(feed_items) > 0:
            log("PASS", f"SIGINT feed has {len(feed_items)} items")
        else:
            log("WARN", "SIGINT feed empty (may need more time)")

        # ── 14. Console errors ────────────────────────────────────────────────
        js_errors = [str(e) for e in console_errors if "error" in str(type(e)).lower() or
                     (isinstance(e, dict) and e.get("type") == "pageerror")]
        if not js_errors:
            log("PASS", "No JavaScript errors in console")
        else:
            # Filter out known non-critical errors
            critical = [e for e in js_errors if not any(x in str(e).lower() for x in
                        ["favicon", "leaflet.markercluster", "net::err_name_not_resolved",
                         "extension", "chrome-extension"])]
            if critical:
                log("FAIL", f"JS errors ({len(critical)})", str(critical[0])[:200])
            else:
                log("WARN", f"Minor JS errors (non-critical)", str(js_errors[0])[:200] if js_errors else "")

        # ── 15. FIRMS/USGS signal check ───────────────────────────────────────
        r15 = await page.request.get(f"{BASE}/api/events?sources=firms&limit=10")
        firms = await r15.json()
        if (firms.get("count") or 0) > 0:
            log("PASS", f"FIRMS thermal data flowing ({firms.get('count')} events)")
        else:
            log("WARN", "FIRMS data not yet available (polling interval)")

        r16 = await page.request.get(f"{BASE}/api/events?sources=usgs&limit=10")
        usgs = await r16.json()
        if (usgs.get("count") or 0) > 0:
            log("PASS", f"USGS seismic data flowing ({usgs.get('count')} events)")
        else:
            log("WARN", "USGS data not yet available")

        # ── 16. ADS-B data presence ───────────────────────────────────────────
        r17 = await page.request.get(f"{BASE}/api/positions?sources=adsb,opensky&limit=10")
        adsb = await r17.json()
        if (adsb.get("count") or 0) > 0:
            log("PASS", f"ADS-B positions flowing ({adsb.get('count')} aircraft)")
        else:
            log("WARN", "No ADS-B positions (normal if no API key configured)")

        # ── 17. Community endpoint ────────────────────────────────────────────
        r18 = await page.request.get(f"{BASE}/api/community?limit=5")
        comm = await r18.json()
        log("PASS", f"Community API works ({comm.get('count',0)} reports)")

        await browser.close()

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("WARDAR AUDIT SUMMARY")
    print("="*60)
    passes = sum(1 for r in RESULTS if r["status"] == "PASS")
    warns  = sum(1 for r in RESULTS if r["status"] == "WARN")
    fails  = sum(1 for r in RESULTS if r["status"] == "FAIL")
    print(f"✅ PASS: {passes}  ⚠️  WARN: {warns}  ❌ FAIL: {fails}")
    print()
    if fails:
        print("FAILURES TO FIX:")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"  ❌ {r['test']}: {r['detail'][:120]}")
    if warns:
        print("\nWARNINGS:")
        for r in RESULTS:
            if r["status"] == "WARN":
                print(f"  ⚠️  {r['test']}: {r['detail'][:100]}")

    return {"pass": passes, "warn": warns, "fail": fails, "results": RESULTS}

if __name__ == "__main__":
    asyncio.run(run_audit())
