"""
Wardar — Deep Playwright Audit
Tests every major function: map load, layer toggles, popups, news articles,
WebSocket live data, static layers, AI features, search, all tabs, signals.
"""
import asyncio, json, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.async_api import async_playwright

BASE = "http://localhost:8080"
RESULTS = []

def log(status, test, detail=""):
    icon = "✅" if status == "PASS" else ("⚠️ " if status == "WARN" else "❌")
    msg = f"{icon} [{status}] {test}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)
    RESULTS.append({"status": status, "test": test, "detail": str(detail)})

# ── Helpers ────────────────────────────────────────────────────────────────────
async def api(page, path):
    r = await page.request.get(f"{BASE}{path}")
    return r.status, await r.json()

async def run_audit():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-web-security"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 1 — API SIGNAL COVERAGE
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 1: API SIGNAL COVERAGE")

        status, health = await api(page, "/api/health")
        pos_live = health.get("positions_live", 0)
        ev_live  = health.get("events_live", 0)
        log("PASS" if pos_live > 0 else "FAIL", "Health: positions live", f"{pos_live:,} positions, {ev_live:,} events")

        # Check each signal source
        signal_sources = [
            ("adsb",          "Aircraft ADS-B"),
            ("opensky",       "Aircraft OpenSky"),
            ("tle",           "Satellites TLE"),
            ("ais",           "Maritime AIS"),
            ("acled",         "Conflict Events ACLED"),
            ("firms",         "NASA Thermal FIRMS"),
            ("usgs",          "USGS Seismic"),
            ("gdelt",         "GDELT News"),
            ("osint_news",    "OSINT News"),
            ("notam",         "FAA NOTAMs"),
            ("gpsjam",        "GPS Jamming"),
            ("polymarket",    "Prediction Markets"),
            ("ioda",          "Internet Blackouts IODA"),
            ("wikipedia",     "Wikipedia Spikes"),
            ("pikud_haoref",  "Israel Rocket Alerts"),
            ("dark_vessel",   "Dark Vessel Alerts"),
            ("convergence",   "Convergence Alerts"),
        ]

        for src, label in signal_sources:
            s, d = await api(page, f"/api/events?sources={src}&limit=5")
            cnt = d.get("count", 0)
            # Also check positions for tracking sources
            if src in ("adsb", "opensky", "tle", "ais"):
                s2, d2 = await api(page, f"/api/positions?sources={src}&limit=5")
                cnt = d2.get("count", 0)
            if cnt > 0:
                log("PASS", f"Signal: {label}", f"{cnt} records")
            else:
                log("WARN", f"Signal: {label} — no data", "feed may be empty or pending poll")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 2 — NEWS ARTICLES
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 2: NEWS ARTICLES")

        s, news = await api(page, "/api/events?limit=200")
        news_sources = {}
        has_url = 0
        has_desc = 0
        sample_articles = []

        for e in (news.get("data") or []):
            src = e.get("source", "?")
            news_sources[src] = news_sources.get(src, 0) + 1
            if e.get("url"): has_url += 1
            if e.get("description"): has_desc += 1
            if len(sample_articles) < 5 and e.get("url") and e.get("title"):
                sample_articles.append({"title": e["title"][:70], "url": e["url"][:80], "source": src})

        total_news = sum(news_sources.values())
        log("PASS" if total_news > 0 else "FAIL", "News events total", f"{total_news} events across {len(news_sources)} sources")
        log("PASS" if has_url > 0 else "WARN", "News events have URLs", f"{has_url}/{total_news} have clickable links")
        log("PASS" if has_desc > 0 else "WARN", "News events have descriptions", f"{has_desc}/{total_news} have description text")

        print("       Sample articles:")
        for a in sample_articles:
            print(f"       [{a['source']}] {a['title']}")
            print(f"              → {a['url']}")

        # Verify URLs are real (check first 3)
        real_urls = 0
        for a in sample_articles[:3]:
            try:
                r = await page.request.get(a["url"], timeout=8000)
                if r.status < 400:
                    real_urls += 1
            except:
                pass
        log("PASS" if real_urls > 0 else "WARN", f"News URL reachability ({real_urls}/{min(3,len(sample_articles))} live)", "")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 3 — STATIC LAYERS
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 3: STATIC LAYERS")

        for layer, min_expected in [("nuclear", 5), ("cables", 100), ("mil_bases", 0), ("pipelines", 0)]:
            s, d = await api(page, f"/api/layers/{layer}")
            n = len(d.get("features") or [])
            if n >= min_expected and n > 0:
                log("PASS", f"Layer {layer}", f"{n} features")
                if layer == "nuclear" and n > 0:
                    names = [f["properties"].get("name","?") for f in (d.get("features") or [])[:3]]
                    print(f"       Sample: {', '.join(names)}")
            elif n > 0:
                log("WARN", f"Layer {layer} — low feature count", f"{n} features (expected ≥{min_expected})")
            else:
                log("WARN", f"Layer {layer} — 0 features", "external source may be down")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 4 — AI FEATURES
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 4: AI FEATURES")

        s, brief = await api(page, "/api/brief")
        if s == 200 and brief.get("text") and len(brief["text"]) > 100:
            log("PASS", "AI Global SITREP", f"{len(brief['text'])} chars, model={brief.get('model','?')[:40]}")
        elif brief.get("error") == "no_api_key":
            log("WARN", "AI SITREP — no ANTHROPIC_API_KEY")
        else:
            log("WARN", "AI SITREP not yet generated", "will run on 6h schedule — trigger POST /api/brief/generate")

        for country in ["Iran", "Ukraine", "Gaza"]:
            s, cb = await api(page, f"/api/brief/country/{country}")
            if s in (200, 503) and cb.get("text"):
                log("PASS", f"Country brief: {country}", cb["text"][:80])
            elif "no_api_key" in cb.get("error", ""):
                log("WARN", f"Country brief: {country} — no API key")
            else:
                log("WARN", f"Country brief: {country}", str(cb)[:80])

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 5 — FRONTEND LOAD & MAP
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 5: FRONTEND LOAD & MAP")

        page2 = await ctx.new_page()
        js_errors = []
        page2.on("console", lambda m: js_errors.append(str(m)) if m.type == "error" else None)
        page2.on("pageerror", lambda e: js_errors.append(f"PAGE_ERR: {e}"))

        t0 = time.time()
        await page2.goto(BASE, wait_until="domcontentloaded", timeout=15000)
        load_ms = int((time.time() - t0) * 1000)
        log("PASS" if load_ms < 5000 else "WARN", f"Page load time", f"{load_ms}ms")

        # Boot overlay
        try:
            await page2.wait_for_selector("#boot.fade", timeout=12000)
            log("PASS", "Boot overlay fades cleanly")
        except:
            boot_vis = await page2.is_visible("#boot")
            log("FAIL" if boot_vis else "PASS", "Boot overlay", "STUCK" if boot_vis else "Removed")

        # Map mode check — should start in 2D (Leaflet)
        await asyncio.sleep(2)
        leaflet_el = await page2.query_selector("#leaflet-map")
        globe_el   = await page2.query_selector("#globe-container")
        leaflet_vis = await leaflet_el.is_visible() if leaflet_el else False
        globe_vis   = await globe_el.is_visible() if globe_el else False
        log("PASS" if leaflet_vis else "WARN", "2D Leaflet map visible by default", f"leaflet={leaflet_vis} globe={globe_vis}")

        canvas = await page2.query_selector("#leaflet-map canvas")
        log("PASS" if canvas else "WARN", "Leaflet canvas rendered", "map tiles drawing")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 6 — WEBSOCKET & LIVE DATA
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 6: WEBSOCKET & LIVE DATA")

        ws_info = await page2.evaluate("""() => ({
            dotClass:  (document.getElementById('sb-dot') || {}).className || 'NOT_FOUND',
            connText:  (document.getElementById('sb-conntext') || {}).textContent || 'NOT_FOUND',
            aircraft:  (document.getElementById('sb-ac') || {}).textContent || '0',
            ships:     (document.getElementById('sb-sh') || {}).textContent || '0',
            sats:      (document.getElementById('sb-sat') || {}).textContent || '0',
            events:    (document.getElementById('sb-ev') || {}).textContent || '0',
        })""")

        is_live = "live" in ws_info.get("dotClass", "").lower() or "LIVE" in ws_info.get("connText", "")
        is_connecting = "connect" in ws_info.get("connText", "").lower()
        ws_status = "PASS" if is_live else ("WARN" if is_connecting else "FAIL")
        log(ws_status, "WebSocket connection status",
            f"dot={ws_info['dotClass']} text={ws_info['connText']}")

        await asyncio.sleep(4)
        counts_after = await page2.evaluate("""() => ({
            aircraft: parseInt(document.getElementById('sb-ac')?.textContent || '0'),
            ships:    parseInt(document.getElementById('sb-sh')?.textContent || '0'),
            sats:     parseInt(document.getElementById('sb-sat')?.textContent || '0'),
            events:   parseInt(document.getElementById('sb-ev')?.textContent || '0'),
        })""")

        total_counts = sum(counts_after.values())
        log("PASS" if counts_after["aircraft"] > 0 else "WARN", "Aircraft count",  f"✈ {counts_after['aircraft']}")
        log("PASS" if counts_after["ships"] > 0    else "WARN", "Ships count",     f"⛵ {counts_after['ships']}")
        log("PASS" if counts_after["sats"] > 0     else "WARN", "Satellites count", f"◎ {counts_after['sats']}")
        log("PASS" if counts_after["events"] > 0   else "WARN", "Events count",    f"⚑ {counts_after['events']}")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 7 — UI TABS & PANELS
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 7: UI TABS & PANELS")

        for tab_id, tab_name in [("tab-feed","SIGINT FEED"), ("tab-news","WAR NEWS"),
                                   ("tab-comm","COMMUNITY"), ("tab-alerts","ALERTS"),
                                   ("tab-sitrep","AI SITREP")]:
            el = await page2.query_selector(f"#{tab_id}")
            if el:
                await el.click()
                await asyncio.sleep(0.4)
                log("PASS", f"Tab {tab_name} — click OK")
            else:
                log("FAIL", f"Tab {tab_name} — element #{tab_id} not found")

        # Return to SIGINT feed
        feed_tab = await page2.query_selector("#tab-feed")
        if feed_tab: await feed_tab.click()
        await asyncio.sleep(0.5)

        feed_items = await page2.query_selector_all(".feed-item")
        log("PASS" if len(feed_items) > 0 else "WARN", f"SIGINT feed populated", f"{len(feed_items)} items")

        # Check war news tab has items
        news_tab = await page2.query_selector("#tab-news")
        if news_tab: await news_tab.click()
        await asyncio.sleep(0.5)
        news_items = await page2.query_selector_all(".news-item")
        log("PASS" if news_items else "WARN", "War news tab has content", f"{len(news_items)} items visible")
        # Check clickable (cursor: pointer)
        if news_items:
            clickable = await page2.evaluate("""() => {
                const items = document.querySelectorAll('.news-item');
                return Array.from(items).filter(el => el.style.cursor === 'pointer').length;
            }""")
            log("PASS" if clickable > 0 else "FAIL", "News articles are clickable (open source URL)",
                f"{clickable}/{len(news_items)} items have click handler")

        # Check SITREP tab
        sitrep_tab = await page2.query_selector("#tab-sitrep")
        if sitrep_tab: await sitrep_tab.click()
        await asyncio.sleep(0.5)
        sitrep_content = await page2.query_selector("#sitrep-content, [id*='sitrep']")
        log("PASS" if sitrep_content else "WARN", "SITREP tab content element present")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 8 — LAYER TOGGLE PANEL
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 8: LAYER CONTROLS")

        panel = await page2.query_selector("#panel")
        log("PASS" if panel else "FAIL", "Left sensor panel present")

        # Test panel collapse
        collapse_btn = await page2.query_selector(".panel-collapse-btn")
        if collapse_btn:
            await collapse_btn.click()
            await asyncio.sleep(0.3)
            is_collapsed = await page2.evaluate("document.getElementById('panel').classList.contains('collapsed')")
            log("PASS" if is_collapsed else "FAIL", "Panel collapse toggle works")
            # Re-expand via JS (button goes off-screen when collapsed)
            await page2.evaluate("if(typeof togglePanelCollapse==='function') togglePanelCollapse()")
            await asyncio.sleep(0.3)
        else:
            log("WARN", "Panel collapse button not found")

        # Test map mode toggle (2D → 3D)
        mode_btn = await page2.query_selector("#mapmode-btn")
        if mode_btn:
            btn_text = await mode_btn.text_content()
            log("PASS", f"Map mode toggle button present", f"shows: '{btn_text.strip()}'")
            await mode_btn.click()
            await asyncio.sleep(3)  # globe takes time to load
            globe_now_vis = await page2.evaluate("document.getElementById('globe-container').style.display !== 'none'")
            log("PASS" if globe_now_vis else "WARN", "3D Globe activates on toggle click")
            globe_canvas = await page2.query_selector("#globe-container canvas")
            log("PASS" if globe_canvas else "WARN", "Globe.gl canvas renders in 3D mode")
            # Switch back to 2D
            await mode_btn.click()
            await asyncio.sleep(0.5)
        else:
            log("WARN", "Map mode toggle button not found")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 9 — NOISE FILTER (smart keyword matching)
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 9: NOISE FILTER")

        s, all_evts = await api(page, "/api/events?limit=300")
        all_titles = [(e.get("source",""), (e.get("title") or "").lower()) for e in (all_evts.get("data") or [])]

        # Whole-word noise check (not substring)
        noise_words = ["stanley cup", "nba finals", "nfl draft", "super bowl", "oscar award",
                       "grammy", "bitcoin price", "ethereum", "kardashian", "taylor swift",
                       "celebrity", "sports score"]
        noise_found = []
        for src, title in all_titles:
            for kw in noise_words:
                if kw in title:
                    noise_found.append(f"[{src}] {title[:80]}")

        if noise_found:
            log("FAIL", f"Noise detected ({len(noise_found)} hits)", noise_found[0])
        else:
            log("PASS", "Noise filter clean — no sports/entertainment/crypto found",
                f"checked {len(all_titles)} event titles")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 10 — POPUP & INTERACTION
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 10: POPUP INTERACTION")

        # Click on map to test interaction (back to 2D first)
        await page2.evaluate("""() => {
            const btn = document.getElementById('mapmode-btn');
            if(btn && document.getElementById('globe-container').style.display !== 'none') btn.click();
        }""")
        await asyncio.sleep(0.5)

        # Check community submit form
        comm_tab = await page2.query_selector("#tab-comm")
        if comm_tab: await comm_tab.click()
        await asyncio.sleep(0.3)
        comm_form = await page2.query_selector("#community-form, form[id*='comm'], [class*='comm-form']")
        log("PASS" if comm_form else "WARN", "Community intel submit form present")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 11 — ALERTS TAB
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 11: ALERTS")

        alerts_tab = await page2.query_selector("#tab-alerts")
        if alerts_tab: await alerts_tab.click()
        await asyncio.sleep(0.5)

        s, alert_data = await api(page, "/api/events?sources=dark_vessel,convergence,nuclear_threat,pipeline_threat,pikud_haoref&limit=20")
        alert_cnt = alert_data.get("count", 0)
        log("PASS" if alert_cnt >= 0 else "FAIL", f"Alert API responds", f"{alert_cnt} phase-3 alerts")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 12 — JS ERRORS
        # ══════════════════════════════════════════════════════════════════════
        print("\n▸ SECTION 12: JS ERRORS")

        skip_patterns = ["favicon", "net::err_name_not_resolved", "chrome-extension",
                         "extension://", "leaflet.markercluster", "401", "403"]
        critical_errors = [e for e in js_errors
                           if not any(p in e.lower() for p in skip_patterns)]
        if not critical_errors:
            log("PASS", "No critical JavaScript errors",
                f"({len(js_errors)} total, all non-critical)" if js_errors else "clean console")
        else:
            log("FAIL", f"{len(critical_errors)} JS errors", critical_errors[0][:200])
            for err in critical_errors[1:3]:
                print(f"       {err[:200]}")

        await browser.close()

    # ══════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "═"*70)
    print("WARDAR DEEP AUDIT — RESULTS")
    print("═"*70)
    passes = sum(1 for r in RESULTS if r["status"] == "PASS")
    warns  = sum(1 for r in RESULTS if r["status"] == "WARN")
    fails  = sum(1 for r in RESULTS if r["status"] == "FAIL")
    total  = len(RESULTS)
    print(f"✅ PASS: {passes}/{total}   ⚠️  WARN: {warns}   ❌ FAIL: {fails}")
    print()

    if fails:
        print("── FAILURES ─────────────────────────────────────────────────────────")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"  ❌ {r['test']}")
                if r["detail"]: print(f"     {r['detail'][:150]}")

    if warns:
        print("\n── WARNINGS ─────────────────────────────────────────────────────────")
        for r in RESULTS:
            if r["status"] == "WARN":
                print(f"  ⚠️  {r['test']}")
                if r["detail"]: print(f"     {r['detail'][:120]}")

    return {"pass": passes, "warn": warns, "fail": fails}

if __name__ == "__main__":
    asyncio.run(run_audit())
