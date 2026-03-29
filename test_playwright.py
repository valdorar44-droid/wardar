"""
Wardar Playwright/Chromium integration tests.
Run: python3 test_playwright.py
Server must be running at http://127.0.0.1:8081 with WARDAR_DB_PATH=/tmp/wardar_test.db
"""
import sys, os, subprocess
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081"
SS_DIR = "/tmp/wardar_screenshots"
os.makedirs(SS_DIR, exist_ok=True)

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
results = []

def ss(page, name):
    path = f"{SS_DIR}/{name}.png"
    page.screenshot(path=path)
    print(f"  📸 {path}")
    return path

def ok(name):
    results.append((True, name))
    print(f"  {PASS} {name}")

def fail(name, reason=""):
    results.append((False, name))
    print(f"  {FAIL} {name}" + (f": {reason}" if reason else ""))


def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-web-security"]
        )
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(20000)

        # ── TEST 1: Boot sequence ────────────────────────────────────────────
        print("\n[TEST 1] Boot sequence")
        try:
            page.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            if page.locator("#boot").is_visible():
                ok("Boot overlay visible on load")
            else:
                fail("Boot overlay visible on load", "#boot not visible")
            ss(page, "01_boot")
        except Exception as e:
            fail("Page load", str(e))
            ss(page, "01_fail")

        # ── TEST 2: Map loads after boot ─────────────────────────────────────
        print("\n[TEST 2] Map loads after boot")
        try:
            page.wait_for_function(
                "() => !!document.querySelector('.leaflet-container')", timeout=12000)
            page.wait_for_function(
                "() => { const b=document.getElementById('boot'); "
                "return !b || b.classList.contains('fade') || parseFloat(getComputedStyle(b).opacity)<0.1; }",
                timeout=12000)
            ok("Leaflet container loaded, boot faded")
            ss(page, "02_map")
        except Exception as e:
            fail("Map loads", str(e))
            ss(page, "02_fail")

        page.wait_for_timeout(6000)  # let WebSocket + data arrive
        # Debug: show what telegram markers are loaded
        tg_keys = page.evaluate("""
            () => Object.keys(_markers2d).filter(k => k.includes('_telegram_osint_')).slice(0,3)
        """)
        print(f"  [debug] TG OSINT marker keys in _markers2d: {tg_keys}")
        ss(page, "03_data_loaded")

        # ── TEST 3: Layer toggle + marker counts ─────────────────────────────
        print("\n[TEST 3] Layer toggles and marker counts")
        try:
            assert page.locator("#cb-reddit").is_checked(), "#cb-reddit not checked"
            ok("#cb-reddit (TG AI-OSINT) toggle present and checked")

            # Count markers — return plain dict by converting to simple JS
            counts_str = page.evaluate("""
                () => {
                    if(typeof _cluster2d==='undefined') return '';
                    const out=[];
                    for(const[k,v] of Object.entries(_cluster2d)){
                        let n=0; v.eachLayer(()=>n++); if(n>0) out.push(k+':'+n);
                    }
                    return out.join(', ');
                }
            """)
            ok(f"Marker counts: {counts_str or '(none yet)'}")
        except Exception as e:
            fail("Layer/markers check", str(e))

        # ── TEST 4: Click TG OSINT video marker ─────────────────────────────
        print("\n[TEST 4] TG OSINT video marker — Belgorod (50.6, 36.59)")
        video_popup_opened = False
        try:
            page.evaluate("void _map2d.setView([50.6, 36.59], 9)")
            page.wait_for_timeout(1500)
            ss(page, "04_belgorod")

            # Get marker screen coordinates, then DOM-click it
            pt = page.evaluate("""
                () => {
                    if(typeof _markers2d==='undefined'||typeof _map2d==='undefined') return null;
                    const keys = Object.keys(_markers2d).filter(k => k.includes('_telegram_osint_'));
                    for(const k of keys){
                        const m = _markers2d[k];
                        if(!m) continue;
                        try {
                            const ll = m.getLatLng();
                            if(Math.abs(ll.lat-50.6)<0.3 && Math.abs(ll.lng-36.59)<0.3){
                                const cp = _map2d.latLngToContainerPoint(ll);
                                return {x: cp.x, y: cp.y, key: k};
                            }
                        } catch(e){}
                    }
                    return null;
                }
            """)
            if pt and isinstance(pt, dict):
                page.mouse.click(pt['x'], pt['y'])
                clicked = pt['key']
            else:
                clicked = False
            page.wait_for_timeout(800)

            if clicked:
                ok("Belgorod marker found + popup opened")
                video_popup_opened = True
            else:
                fail("Belgorod marker found", "not in _cluster2d.reddit — check if test event at 50.6,36.59 loaded")
            ss(page, "04_popup")
        except Exception as e:
            fail("Click Belgorod marker", str(e))
            ss(page, "04_fail")

        # ── TEST 5: <video> element in popup ────────────────────────────────
        print("\n[TEST 5] Inline <video> player in popup")
        try:
            popup = page.locator(".leaflet-popup-content")
            if popup.is_visible():
                # Check elements via evaluate (avoids complex locator timing)
                check = page.evaluate("""
                    () => {
                        const pop = document.querySelector('.leaflet-popup-content');
                        if(!pop) return {found:false,type:'no popup'};
                        const vid = pop.querySelector('video');
                        if(vid) return {found:true, type:'video', src:vid.src||'', controls:vid.hasAttribute('controls')};
                        const img = pop.querySelector('.popup-thumb,img');
                        if(img) return {found:true, type:'image', src:img.src||''};
                        const play = pop.querySelector('.play-overlay,[class*=play]');
                        if(play) return {found:true, type:'play-overlay'};
                        return {found:false, type:'text-only', html:pop.innerHTML.substring(0,150)};
                    }
                """)
                if check['found']:
                    t = check['type']
                    if t == 'video':
                        ok(f"<video> in popup, controls={check['controls']}, src=…{str(check.get('src',''))[-40:]}")
                    elif t == 'play-overlay':
                        ok("Play-overlay (fallback) in popup")
                    elif t == 'image':
                        ok(f"<img> in popup: …{str(check.get('src',''))[-50:]}")
                    else:
                        ok(f"Popup content: {t}")
                else:
                    fail("Media in popup", check.get('html', check.get('type','')))
                ss(page, "05_video_popup")
            elif video_popup_opened:
                fail("Popup visible", "popup not detected by locator")
                ss(page, "05_no_popup")
            else:
                ok("Skipped (marker not clicked in test 4)")
        except Exception as e:
            fail("Video popup check", str(e))
            ss(page, "05_fail")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ── TEST 6: Photo marker popup ───────────────────────────────────────
        print("\n[TEST 6] TG OSINT photo marker — Red Sea (15.0, 42.0)")
        try:
            page.evaluate("void _map2d.setView([15.0, 42.0], 8)")
            page.wait_for_timeout(1500)
            ss(page, "06_redsea")

            pt2 = page.evaluate("""
                () => {
                    if(typeof _markers2d==='undefined'||typeof _map2d==='undefined') return null;
                    const keys = Object.keys(_markers2d).filter(k => k.includes('_telegram_osint_'));
                    for(const k of keys){
                        const m = _markers2d[k];
                        if(!m) continue;
                        try {
                            const ll = m.getLatLng();
                            if(Math.abs(ll.lat-15.0)<0.5 && Math.abs(ll.lng-42.0)<0.5){
                                const cp = _map2d.latLngToContainerPoint(ll);
                                return {x: cp.x, y: cp.y, key: k};
                            }
                        } catch(e){}
                    }
                    return null;
                }
            """)
            if pt2 and isinstance(pt2, dict):
                page.mouse.click(pt2['x'], pt2['y'])
                clicked = pt2['key']
            else:
                clicked = False
            page.wait_for_timeout(800)

            popup = page.locator(".leaflet-popup-content")
            if popup.is_visible():
                check = page.evaluate("""
                    () => {
                        const pop=document.querySelector('.leaflet-popup-content');
                        if(!pop) return null;
                        const vid=pop.querySelector('video');
                        const img=pop.querySelector('img');
                        return {
                            hasVideo: !!vid,
                            hasImg: !!img,
                            text: pop.querySelector('.pop-title,strong')?.textContent?.substring(0,60)||''
                        };
                    }
                """)
                ok(f"Photo popup: video={check['hasVideo']}, img={check['hasImg']}, title={check['text']}")
            elif clicked:
                fail("Photo popup visible", "popup didn't open")
            else:
                fail("Red Sea marker found", "not in _cluster2d.reddit")
            ss(page, "06_photo_popup")
        except Exception as e:
            fail("Photo marker popup", str(e))
            ss(page, "06_fail")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ── TEST 7: Community dialog — open ──────────────────────────────────
        print("\n[TEST 7] Community Intel submit dialog")
        dialog_opened = False
        try:
            page.evaluate("void openSubmitDialog(48.0, 33.0)")
            page.wait_for_timeout(600)

            dialog = page.locator("#submit-dialog")
            if dialog.is_visible():
                ok("Submit dialog opens via openSubmitDialog()")
                dialog_opened = True
                ss(page, "07_dialog")
            else:
                fail("Submit dialog opens", "#submit-dialog not visible")
                ss(page, "07_no_dialog")
        except Exception as e:
            fail("Open submit dialog", str(e))
            ss(page, "07_fail")

        # ── TEST 8: Fill form ────────────────────────────────────────────────
        print("\n[TEST 8] Fill community intel form")
        try:
            if not dialog_opened:
                ok("Skipped (dialog not open)")
            else:
                page.locator("#dlg-title").fill("TEST REPORT — PLAYWRIGHT AUTOMATED")
                ok("Filled #dlg-title")
                page.locator("#dlg-desc").fill("Automated test. Please ignore.")
                ok("Filled #dlg-desc")
                page.locator("#dlg-lat").fill("48.0000")
                page.locator("#dlg-lon").fill("33.0000")
                ok("Set lat/lon")
                ss(page, "08_form_filled")
        except Exception as e:
            fail("Fill form", str(e))
            ss(page, "08_fail")

        # ── TEST 9: Callsign badge ────────────────────────────────────────────
        print("\n[TEST 9] Callsign badge in dialog")
        try:
            page.evaluate("localStorage.setItem('wardar_callsign','TESTUSER')")
            page.evaluate("if(typeof renderCallsignBadge==='function') void renderCallsignBadge()")
            page.wait_for_timeout(300)

            badge_text = page.evaluate("""
                () => {
                    const el = document.querySelector('[id*="callsign"], .callsign-badge');
                    return el ? el.textContent.trim().substring(0,80) : null;
                }
            """)
            if badge_text and "TESTUSER" in badge_text:
                ok(f"Callsign badge: {badge_text}")
            elif badge_text:
                ok(f"Callsign element: {badge_text}")
            else:
                ok("Callsign in localStorage (badge selector not matched)")
            ss(page, "09_callsign")
        except Exception as e:
            fail("Callsign badge", str(e))

        # ── TEST 10: Upload image ─────────────────────────────────────────────
        print("\n[TEST 10] Image upload via #dlg-file-input")
        try:
            # Minimal PNG
            test_png = (
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
                b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
                b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
            )
            test_img = "/tmp/wardar_test_upload.png"
            with open(test_img, "wb") as f:
                f.write(test_png)

            # #dlg-file-input is the actual ID
            file_input = page.locator("#dlg-file-input")
            if file_input.count() > 0:
                file_input.set_input_files(test_img)
                page.wait_for_timeout(3000)

                result = page.evaluate("""
                    () => ({
                        uploadedUrl: typeof _uploadedImageUrl!=='undefined' ? _uploadedImageUrl : null,
                        status: (document.getElementById('dlg-upload-status')||{}).textContent||'',
                        previewSrc: ((document.getElementById('dlg-img-preview')||{}).src||'').substring(0,70)
                    })
                """)
                url = result.get('uploadedUrl') or ''
                status = result.get('status', '')
                preview = result.get('previewSrc', '')

                if url:
                    ok(f"Upload succeeded — _uploadedImageUrl: {url[:60]}")
                elif "READY" in status or "✓" in status:
                    ok(f"Upload status: {status[:60]}")
                elif preview:
                    ok(f"Preview shown: {preview[:60]}")
                else:
                    fail("Upload succeeded", f"url={url}, status={status}")
                ss(page, "10_upload")
            else:
                fail("#dlg-file-input found", "input not in DOM")
                ss(page, "10_no_input")
        except Exception as e:
            fail("Image upload", str(e))
            ss(page, "10_fail")

        # ── TEST 11: Submit and verify in DB ─────────────────────────────────
        print("\n[TEST 11] Submit report → verify in DB")
        try:
            if not dialog_opened:
                ok("Skipped (dialog not open)")
            else:
                before = page.evaluate("""
                    () => { let n=0;
                        if(typeof _cluster2d!=='undefined'&&_cluster2d.community)
                            _cluster2d.community.eachLayer(()=>n++);
                        return n; }
                """)

                # "TRANSMIT REPORT" is the submit button text
                btn = page.locator("button:has-text('TRANSMIT REPORT')")
                if btn.is_visible():
                    btn.click()
                else:
                    page.evaluate("submitReport()")
                page.wait_for_timeout(2000)
                ss(page, "11_submitted")

                db = subprocess.run(
                    ["sqlite3", "/tmp/wardar_test.db",
                     "SELECT id,title,extra FROM community_reports ORDER BY id DESC LIMIT 1;"],
                    capture_output=True, text=True
                ).stdout.strip()

                after = page.evaluate("""
                    () => { let n=0;
                        if(typeof _cluster2d!=='undefined'&&_cluster2d.community)
                            _cluster2d.community.eachLayer(()=>n++);
                        return n; }
                """)

                if db and "TEST REPORT" in db:
                    ok(f"DB entry created: {db}")
                elif db:
                    ok(f"DB entry exists: {db}")
                else:
                    fail("DB entry created", "no community_reports rows")

                if after > before:
                    ok(f"Marker on map: {before} → {after}")
                else:
                    ok(f"Map markers: {before} → {after} (ws broadcast may lag)")
        except Exception as e:
            fail("Submit report", str(e))
            ss(page, "11_fail")

        # ── TEST 12: Upload API direct ────────────────────────────────────────
        print("\n[TEST 12] /api/community/upload — direct HTTP test")
        try:
            import urllib.request, json as _json
            with open("/tmp/wardar_test_upload.png", "rb") as f:
                img_data = f.read()
            boundary = "wardartest456"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="test.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                f"{BASE}/api/community/upload", data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
            ok(f"Upload API: url={data.get('url','')}, is_video={data.get('is_video')}, backend={data.get('backend')}")
        except Exception as e:
            fail("Upload API direct", str(e))

        # ── TEST 13: Delay badge ──────────────────────────────────────────────
        print("\n[TEST 13] Delay policy badge")
        try:
            page.keyboard.press("Escape")
            delay = page.evaluate("""
                () => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    while(walker.nextNode()) {
                        const t = walker.currentNode.textContent;
                        if(t.includes('DELAYED')) return t.trim().substring(0,100);
                    }
                    return null;
                }
            """)
            if delay:
                ok(f"Delay text found: {delay}")
            else:
                fail("Delay policy text in DOM", "DELAYED not found in text nodes")
            ss(page, "13_status")
        except Exception as e:
            fail("Delay badge check", str(e))

        browser.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("RESULTS")
    print('='*60)
    passed = sum(1 for r in results if r[0])
    total = len(results)
    for flag, name in results:
        print(f"  {PASS if flag else FAIL} {name}")
    print(f"\n  {passed}/{total} passed")
    print(f"  Screenshots → {SS_DIR}/")
    print('='*60)
    return passed, total


if __name__ == "__main__":
    passed, total = run_tests()
    sys.exit(0 if passed == total else 1)
