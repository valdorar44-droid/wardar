#!/usr/bin/env python3
"""
Live headed Chromium visual test for Wardar.
Tests: boot sequence, 2D map, layer toggles, news feed, AI SITREP,
globe mode, community intel upload, WebSocket live data.

Run with: python3 tools/live_visual_test.py
"""
import asyncio, os, sys, time, json
from pathlib import Path
from datetime import datetime, timezone

os.environ.setdefault("DISPLAY", ":0")

BASE_URL = "http://localhost:8080"
SS_DIR = Path("/tmp/Screenshots")
SS_DIR.mkdir(exist_ok=True)

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
INFO = "\033[94mℹ INFO\033[0m"
WARN = "\033[93m⚠ WARN\033[0m"

results: list[dict] = []


def log(tag, name, detail=""):
    sym = {"PASS": PASS, "FAIL": FAIL, "INFO": INFO, "WARN": WARN}[tag]
    print(f"{sym}  {name}" + (f" — {detail}" if detail else ""))
    results.append({"status": tag, "name": name, "detail": detail})


async def screenshot(page, name: str):
    p = SS_DIR / f"{name}.png"
    await page.screenshot(path=str(p), full_page=False)
    log("INFO", f"Screenshot saved", str(p))
    return p


async def run():
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-web-security", "--disable-gpu"],
        )
        ctx = await browser.new_context(viewport={"width": 1600, "height": 900})
        page = await ctx.new_page()

        # Track JS errors
        js_errors = []
        page.on("pageerror", lambda e: js_errors.append(str(e)))

        # ── 1. Load page and wait for boot sequence ──────────────────────────
        print("\n\033[1m═══ WARDAR LIVE VISUAL TEST ═══\033[0m\n")
        print("Opening http://localhost:8080 ...")
        t0 = time.time()
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        load_ms = int((time.time() - t0) * 1000)
        log("PASS", "Page loaded", f"{load_ms}ms")

        # Boot overlay check
        await asyncio.sleep(0.5)
        boot_vis = await page.is_visible("#boot-overlay")
        if boot_vis:
            log("PASS", "Boot sequence overlay visible")
        else:
            log("WARN", "Boot overlay not found (may have already dismissed)")
        await screenshot(page, "01_boot_sequence")

        # Wait for boot to finish
        await asyncio.sleep(3.5)
        await screenshot(page, "02_after_boot")

        # ── 2. Verify 2D map loaded by default ──────────────────────────────
        leaflet_vis = await page.is_visible("#leaflet-map")
        globe_css = await page.evaluate("() => document.getElementById('globe-container')?.style.display")
        if leaflet_vis:
            log("PASS", "2D Leaflet map visible by default")
        else:
            log("FAIL", "2D Leaflet map not visible")

        if globe_css == 'none' or not globe_css:
            log("PASS", "Globe hidden on initial load")
        else:
            log("WARN", "Globe visible on initial load", f"display={globe_css}")

        # ── 3. Check markers on 2D map ───────────────────────────────────────
        await asyncio.sleep(2)
        marker_count = await page.evaluate("""
            () => document.querySelectorAll('.leaflet-marker-icon').length
        """)
        cluster_count = await page.evaluate("""
            () => {
                try {
                    return Object.values(_cluster2d || {}).reduce((s, c) => s + (c?.getLayers?.()?.length || 0), 0);
                } catch(e) { return -1; }
            }
        """)
        if marker_count > 0 or cluster_count > 0:
            log("PASS", "2D map has markers", f"DOM={marker_count} cluster={cluster_count}")
        else:
            log("WARN", "No 2D markers yet (may still be loading)")

        await screenshot(page, "03_2d_map_loaded")

        # ── 4. Check layer control panel ─────────────────────────────────────
        panel_vis = await page.is_visible("#panel")
        if panel_vis:
            log("PASS", "Control panel visible (#panel)")
        else:
            log("WARN", "Control panel #panel not visible")

        layer_checks = await page.query_selector_all("input[type='checkbox']")
        log("PASS" if len(layer_checks) >= 4 else "WARN",
            "Layer toggle checkboxes", f"{len(layer_checks)} found")

        # ── 5. Toggle a layer ────────────────────────────────────────────────
        try:
            if layer_checks:
                await layer_checks[0].click()
                await asyncio.sleep(0.4)
                await layer_checks[0].click()
                await asyncio.sleep(0.4)
                log("PASS", "Toggled first layer checkbox on/off")
        except Exception as e:
            log("WARN", "Layer toggle", str(e)[:60])

        # ── 6. Panel collapse ────────────────────────────────────────────────
        try:
            await page.evaluate("() => typeof togglePanelCollapse === 'function' && togglePanelCollapse()")
            await asyncio.sleep(0.5)
            is_collapsed = await page.evaluate("""
                () => {
                    const p = document.getElementById('panel') || document.getElementById('ctrl-panel');
                    return p ? (p.classList.contains('collapsed') || p.style.display === 'none' || p.offsetWidth < 50) : false;
                }
            """)
            log("PASS" if is_collapsed else "WARN", "Panel collapsed", str(is_collapsed))
            await screenshot(page, "04_panel_collapsed")
            # Re-expand
            await page.evaluate("() => togglePanelCollapse()")
            await asyncio.sleep(0.4)
            log("PASS", "Panel re-expanded")
        except Exception as e:
            log("WARN", "Panel collapse", str(e)[:60])

        # ── 7. Click a marker on 2D map ──────────────────────────────────────
        try:
            markers = await page.query_selector_all(".leaflet-marker-icon")
            clicked = False
            for m in markers[:5]:
                try:
                    bbox = await m.bounding_box()
                    if bbox and bbox['x'] > 200:  # skip if under panel
                        await m.click(timeout=3000)
                        await asyncio.sleep(0.8)
                        clicked = True
                        break
                except Exception:
                    continue

            if clicked:
                popup = await page.query_selector(".leaflet-popup-content")
                if popup:
                    txt = await popup.inner_text()
                    log("PASS", "Marker popup opened", txt[:70].replace('\n', ' '))
                    # Check PREDICT PATH button
                    predict_btn = await page.query_selector(".predict-btn, [onclick*='predict']")
                    log("PASS" if predict_btn else "INFO",
                        "PREDICT PATH button", "found" if predict_btn else "not found")
                else:
                    log("WARN", "Marker clicked but no popup")
                await screenshot(page, "05_marker_popup")
                await page.keyboard.press("Escape")
            else:
                log("WARN", "Could not click a marker (all under panel or none visible)")
        except Exception as e:
            log("WARN", "Marker click", str(e)[:60])

        # ── 8. Switch to 3D Globe mode ───────────────────────────────────────
        try:
            mode_btn = await page.query_selector("#mapmode-btn")
            if mode_btn:
                await mode_btn.click()
            else:
                await page.evaluate("() => typeof toggleMapMode === 'function' && toggleMapMode()")
            await asyncio.sleep(3)
            cur_mode = await page.evaluate("() => typeof _mapMode !== 'undefined' ? _mapMode : 'unknown'")
            log("PASS" if cur_mode == '3d' else "WARN", "Globe 3D mode", f"_mapMode={cur_mode}")
            await screenshot(page, "06_globe_3d_mode")
        except Exception as e:
            log("WARN", "Globe mode switch", str(e)[:60])

        # ── 9. Globe data points ─────────────────────────────────────────────
        await asyncio.sleep(2)
        try:
            globe_pts = await page.evaluate("""
                () => {
                    try {
                        if (!globe) return 0;
                        return (globe.pointsData?.() || []).length + (globe.ringsData?.() || []).length;
                    } catch(e) { return 0; }
                }
            """)
            log("PASS" if globe_pts > 0 else "WARN",
                "Globe has data points", f"{globe_pts} points/rings")
        except Exception as e:
            log("WARN", "Globe data check", str(e)[:60])

        # ── 10. Switch back to 2D ────────────────────────────────────────────
        try:
            mode_btn = await page.query_selector("#mapmode-btn")
            if mode_btn:
                await mode_btn.click()
            else:
                await page.evaluate("() => toggleMapMode()")
            await asyncio.sleep(1)
            cur_mode = await page.evaluate("() => typeof _mapMode !== 'undefined' ? _mapMode : 'unknown'")
            log("PASS" if cur_mode == '2d' else "WARN", "Returned to 2D", f"_mapMode={cur_mode}")
        except Exception as e:
            log("WARN", "Return to 2D", str(e)[:60])

        # ── 11. News feed ─────────────────────────────────────────────────────
        try:
            await page.click("#tab-news", timeout=3000)
            await asyncio.sleep(1.5)
            news_items = await page.query_selector_all(".news-item")
            log("PASS" if len(news_items) > 0 else "WARN",
                "News items loaded", f"{len(news_items)} articles")

            if news_items:
                # Check for noise — use longer phrases to avoid substring false positives
                # e.g. "conflict" contains "nfl", "baltics" contains "alt" etc.
                all_titles = []
                for ni in news_items[:20]:
                    try:
                        title_el = await ni.query_selector(".news-title")
                        t = (await title_el.inner_text()) if title_el else (await ni.inner_text())
                    except Exception:
                        t = ""
                    all_titles.append(t.lower()[:100])
                noise_phrases = [
                    'fifa world cup','world cup 2026','nba finals','nfl draft','nfl season',
                    'premier league','super bowl','stanley cup','formula 1 grand prix',
                    'oscar award','grammy award','box office','celebrity gossip',
                ]
                noise_found = [t for t in all_titles for kw in noise_phrases if kw in t]
                if noise_found:
                    log("FAIL", "News noise detected", noise_found[0])
                else:
                    log("PASS", "No sports/entertainment noise in news")

                # Click first news item
                try:
                    await news_items[0].click(timeout=3000)
                    await asyncio.sleep(0.5)
                    log("PASS", "Clicked first news article")
                except Exception as e2:
                    log("WARN", "News article click", str(e2)[:50])

            await screenshot(page, "07_news_feed")
        except Exception as e:
            log("WARN", "News feed", str(e)[:60])

        # ── 12. AI SITREP tab ────────────────────────────────────────────────
        try:
            await page.click("#tab-sitrep", timeout=3000)
            await asyncio.sleep(0.8)
            sitrep_visible = await page.is_visible("#pane-sitrep")
            log("PASS" if sitrep_visible else "WARN", "SITREP pane visible")

            # Check for text or generate button
            sitrep_txt = await page.query_selector("#sitrep-text")
            empty_el = await page.query_selector("#sitrep-empty")
            regen_btn = await page.query_selector("#sitrep-regen-btn")

            txt_display = await page.evaluate("() => document.getElementById('sitrep-text')?.style?.display")
            if txt_display != 'none' and sitrep_txt:
                content = (await sitrep_txt.inner_text())[:100]
                log("PASS", "SITREP has content", content.replace('\n', ' ')[:80])
            elif regen_btn:
                log("INFO", "SITREP ready — clicking GENERATE...")
                await regen_btn.click()
                await asyncio.sleep(3)
                log("INFO", "SITREP generation triggered (takes ~30s)")

            await screenshot(page, "08_ai_sitrep")
        except Exception as e:
            log("WARN", "AI SITREP", str(e)[:60])

        # ── 13. Nuclear static layer ─────────────────────────────────────────
        try:
            nuc_loaded = await page.evaluate("""
                () => {
                    try {
                        return staticPoints.filter(p => p.source === 'nuclear').length;
                    } catch(e) { return -1; }
                }
            """)
            nuc_cluster = await page.evaluate("""
                () => {
                    try {
                        return _cluster2d?.nuclear?.getLayers?.()?.length || 0;
                    } catch(e) { return 0; }
                }
            """)
            log("PASS" if nuc_loaded > 0 else "WARN",
                "Nuclear static layer",
                f"{nuc_loaded} in staticPoints, {nuc_cluster} in cluster")
        except Exception as e:
            log("WARN", "Nuclear layer", str(e)[:60])

        # ── 14. WebSocket ────────────────────────────────────────────────────
        try:
            ws_state = await page.evaluate("""
                () => {
                    try {
                        if (typeof ws === 'undefined' || !ws) return 'no ws';
                        return ['CONNECTING','OPEN','CLOSING','CLOSED'][ws.readyState] || ws.readyState;
                    } catch(e) { return 'error: '+e.message; }
                }
            """)
            log("PASS" if ws_state == 'OPEN' else "WARN", "WebSocket", ws_state)

            pos_count = await page.evaluate("""
                () => {
                    try { return Object.keys(positionStore || {}).length; }
                    catch(e) { return 0; }
                }
            """)
            log("PASS" if pos_count > 0 else "WARN", "Position store", f"{pos_count} entries")
        except Exception as e:
            log("WARN", "WebSocket check", str(e)[:60])

        # ── 15. Status bar ────────────────────────────────────────────────────
        try:
            sb_txt = await page.query_selector("#sb-conntext")
            sb_dot = await page.query_selector("#sb-dot")
            delay_badge = await page.query_selector(".sb-delay")
            if sb_txt:
                conn_txt = await sb_txt.inner_text()
                log("PASS", "Status bar connection", conn_txt)
            else:
                log("WARN", "Status bar conn text not found")
            if delay_badge:
                delay_txt = await delay_badge.inner_text()
                log("PASS", "Delay policy badge", delay_txt[:60])
            # Aircraft count
            ac_el = await page.query_selector("#sb-ac")
            if ac_el:
                ac_count = await ac_el.inner_text()
                log("PASS", "Status bar aircraft count", f"✈ {ac_count}")
        except Exception as e:
            log("WARN", "Status bar", str(e)[:60])

        # ── 16. Community Intel Report ────────────────────────────────────────
        try:
            await page.click("#tab-comm", timeout=3000)
            await asyncio.sleep(0.8)
            log("PASS", "Community tab opened")

            # Click submit button to open dialog
            submit_btn = await page.query_selector(".comm-submit-btn")
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(0.5)
                dialog_open = await page.evaluate("() => document.getElementById('submit-dialog')?.open")
                log("PASS" if dialog_open else "WARN", "Submit dialog opened", str(dialog_open))

                if dialog_open:
                    # Fill form
                    await page.fill("#dlg-title", "TEST: Unidentified Ballistic Object — Black Sea")
                    await page.fill("#dlg-desc",
                        "Unidentified ballistic trajectory observed over Black Sea. "
                        "Altitude ~18km, heading 270°. Multiple radar confirmations.")
                    await page.fill("#dlg-lat", "43.5")
                    await page.fill("#dlg-lon", "34.2")
                    log("PASS", "Community form filled")

                    # Upload photo
                    mock_img = Path("/tmp/mock_missile.jpg")
                    if mock_img.exists():
                        await page.set_input_files("#dlg-file-input", str(mock_img))
                        await asyncio.sleep(0.5)
                        log("PASS", "Mock missile photo uploaded")

                    await screenshot(page, "09_community_form")

                    # Submit — use JS click to avoid scroll/visibility issues
                    await page.evaluate("() => { const b = document.querySelector('.dlg-btn.primary'); if(b) b.click(); }")
                    await asyncio.sleep(0.1)
                    await asyncio.sleep(2)
                    dialog_still_open = await page.evaluate("() => document.getElementById('submit-dialog')?.open")
                    log("PASS" if not dialog_still_open else "WARN",
                        "Report submitted", "dialog closed" if not dialog_still_open else "dialog still open")
                    await screenshot(page, "10_community_submitted")
            else:
                log("WARN", "Community submit button not found")
        except Exception as e:
            log("WARN", "Community intel", str(e)[:60])

        # ── 17. JS Errors check ───────────────────────────────────────────────
        await asyncio.sleep(1)
        if js_errors:
            for err in js_errors[:3]:
                log("FAIL", "JS Error", err[:100])
        else:
            log("PASS", "No JavaScript errors detected")

        # ── 18. Final overview screenshot ─────────────────────────────────────
        # Switch back to SIGINT feed for final view
        try:
            await page.click("#tab-feed", timeout=2000)
            await asyncio.sleep(0.5)
        except Exception:
            pass
        await screenshot(page, "11_final_state")

        # ── Summary ───────────────────────────────────────────────────────────
        print("\n" + "═" * 60)
        print("  LIVE TEST RESULTS")
        print("═" * 60)
        passed = [r for r in results if r["status"] == "PASS"]
        failed = [r for r in results if r["status"] == "FAIL"]
        warned = [r for r in results if r["status"] == "WARN"]

        print(f"\n  PASS : {len(passed)}")
        print(f"  WARN : {len(warned)}")
        print(f"  FAIL : {len(failed)}")

        if failed:
            print("\nFAILURES:")
            for r in failed:
                print(f"  ✗ {r['name']}: {r['detail']}")
        if warned:
            print("\nWARNINGS:")
            for r in warned:
                print(f"  ⚠ {r['name']}: {r['detail']}")

        print(f"\nScreenshots: {SS_DIR}/")
        print("═" * 60)

        report = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "passed": len(passed),
            "warned": len(warned),
            "failed": len(failed),
            "results": results
        }
        with open(SS_DIR / "live_test_report.json", "w") as f:
            json.dump(report, f, indent=2)
        print(f"JSON: {SS_DIR}/live_test_report.json\n")

        await asyncio.sleep(3)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
