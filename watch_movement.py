"""
Wardar — Aircraft Movement Watcher
Verifies that military (and other) aircraft actually move on the map by:
  1. Connecting to the live site (or localhost)
  2. Waiting for positionStore to populate
  3. Recording each aircraft's lat/lon
  4. Waiting for the next position tick interval
  5. Recording positions again
  6. Reporting which aircraft moved and by how much

Usage:
  python3 watch_movement.py                        # defaults to http://127.0.0.1:8081
  python3 watch_movement.py https://wardar.app     # live site
  python3 watch_movement.py http://localhost:8081 --wait 90  # custom wait seconds

Requirements:
  pip install playwright
  playwright install chromium
"""
import sys, math, time, json, argparse
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

# ── CLI args ─────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("base", nargs="?", default="http://127.0.0.1:8081",
                    help="Base URL of the Wardar server")
parser.add_argument("--wait", type=int, default=75,
                    help="Seconds to wait between position snapshots (default 75)")
parser.add_argument("--mil-only", action="store_true", default=False,
                    help="Only report military aircraft (military_flag=1)")
parser.add_argument("--screenshots", action="store_true", default=True,
                    help="Save before/after screenshots (default: on)")
args = parser.parse_args()

BASE     = args.base.rstrip("/")
WAIT_SEC = args.wait
SS_DIR   = "/tmp/wardar_movement"

import os
os.makedirs(SS_DIR, exist_ok=True)

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
MOVE = "\033[33m→\033[0m"
INFO = "\033[36mi\033[0m"

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.asin(math.sqrt(a))


JS_SNAPSHOT = """
() => {
    // positionStore is the canonical map data store in map.html
    if (typeof positionStore === 'undefined') return null;
    const out = {};
    for (const [id, p] of Object.entries(positionStore)) {
        if (p && p.lat != null && p.lon != null) {
            out[id] = {
                lat:          p.lat,
                lon:          p.lon,
                callsign:     p.callsign || id,
                source:       p.source   || '',
                military_flag: p.military_flag || 0,
                altitude_ft:  p.altitude_ft,
                speed_kts:    p.speed_kts,
                heading_deg:  p.heading_deg,
                last_seen:    p.last_seen || null,
            };
        }
    }
    return out;
}
"""

JS_WS_INTERCEPT = """
() => {
    window._wardWarMovementMsgs = [];
    const _orig = WebSocket.prototype.send;
    // Intercept incoming messages by patching addEventListener on WS instances
    const _origOnMsg = Object.getOwnPropertyDescriptor(WebSocket.prototype, 'onmessage');

    // Patch native WebSocket to capture incoming messages
    const _NativeWS = window.WebSocket;
    class _TrackedWS extends _NativeWS {
        constructor(url, protocols) {
            super(url, protocols);
            this.addEventListener('message', (ev) => {
                try {
                    const d = JSON.parse(ev.data);
                    if (d && d.type === 'positions') {
                        window._wardWarMovementMsgs.push({
                            ts: Date.now(),
                            count: (d.data || []).length,
                            sources: d.sources || [],
                            mil_count: (d.data || []).filter(p => p.military_flag).length,
                        });
                    }
                } catch(_) {}
            });
        }
    }
    window.WebSocket = _TrackedWS;
    console.log('[watch_movement] WS intercept installed');
}
"""

JS_GET_WS_MSGS = "() => window._wardWarMovementMsgs || []"


def snapshot_positions(page):
    raw = page.evaluate(JS_SNAPSHOT)
    if not raw:
        return {}
    return raw


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def run():
    print(f"\n{'='*60}")
    print(f"  WARDAR MOVEMENT WATCHER")
    print(f"  Target : {BASE}")
    print(f"  Wait   : {WAIT_SEC}s between snapshots")
    print(f"  Mil-only filter: {args.mil_only}")
    print(f"{'='*60}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-web-security"]
        )
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            # Expose console to stdout for debugging
        )
        page = ctx.new_page()
        page.set_default_timeout(30000)

        # Forward console messages
        page.on("console", lambda m: print(f"  [browser] {m.text}") if m.type in ("error", "warn") else None)

        # ── Load page ────────────────────────────────────────────────────────
        print(f"[{ts()}] Loading {BASE} ...")
        page.goto(BASE, wait_until="domcontentloaded", timeout=30000)

        # Inject WS intercept before any WS connections are opened
        # (inject early via addInitScript would be better but page is already loaded)
        page.evaluate(JS_WS_INTERCEPT)

        # Wait for boot overlay to clear
        try:
            page.wait_for_selector("#boot", state="hidden", timeout=20000)
            print(f"[{ts()}] Boot complete, map loaded")
        except Exception:
            print(f"[{ts()}] WARN: Boot overlay didn't clear — continuing anyway")

        # Wait a moment for the initial WS snapshot to arrive
        print(f"[{ts()}] Waiting 8s for initial position snapshot ...")
        page.wait_for_timeout(8000)

        # ── Snapshot 1 ───────────────────────────────────────────────────────
        snap1 = snapshot_positions(page)
        t1 = time.monotonic()

        if not snap1:
            print(f"  {FAIL} positionStore is empty or not accessible. Is the server running?")
            if args.screenshots:
                page.screenshot(path=f"{SS_DIR}/before.png")
            browser.close()
            sys.exit(1)

        mil1  = {k: v for k, v in snap1.items() if v.get("military_flag")}
        civ1  = {k: v for k, v in snap1.items() if not v.get("military_flag")}

        print(f"\n[{ts()}] SNAPSHOT 1")
        print(f"  Total positions : {len(snap1)}")
        print(f"  Military        : {len(mil1)}")
        print(f"  Civilian        : {len(civ1)}")

        if args.mil_only and not mil1:
            print(f"  {FAIL} No military aircraft in positionStore — ensure MIL ONLY toggle is off")
            print(f"       (MIL ONLY hides civilian but military must still exist in the store)")

        if args.screenshots:
            path = f"{SS_DIR}/before.png"
            page.screenshot(path=path)
            print(f"  Screenshot: {path}")

        # Print top 10 military positions
        print(f"\n  Top military aircraft at T=0:")
        for i, (k, v) in enumerate(list(mil1.items())[:10]):
            print(f"    [{i+1:2d}] {v['callsign']:12s}  "
                  f"src={v['source']:15s}  "
                  f"lat={v['lat']:8.4f}  lon={v['lon']:9.4f}  "
                  f"alt={v.get('altitude_ft') or '?':>7}ft  "
                  f"spd={v.get('speed_kts') or '?':>5}kts")

        # ── Wait ─────────────────────────────────────────────────────────────
        print(f"\n[{ts()}] Waiting {WAIT_SEC}s for next position tick ...")
        for i in range(WAIT_SEC // 10):
            page.wait_for_timeout(10000)
            ws_msgs = page.evaluate(JS_GET_WS_MSGS)
            total_pos = sum(m["count"] for m in ws_msgs)
            total_mil = sum(m["mil_count"] for m in ws_msgs)
            print(f"  [{ts()}] WS messages so far: {len(ws_msgs)} "
                  f"| total positions rcvd: {total_pos} | mil positions: {total_mil}")

        remaining = WAIT_SEC % 10
        if remaining:
            page.wait_for_timeout(remaining * 1000)

        # ── Snapshot 2 ───────────────────────────────────────────────────────
        snap2 = snapshot_positions(page)
        t2    = time.monotonic()
        elapsed = t2 - t1

        mil2 = {k: v for k, v in snap2.items() if v.get("military_flag")}
        civ2 = {k: v for k, v in snap2.items() if not v.get("military_flag")}

        print(f"\n[{ts()}] SNAPSHOT 2  (Δt = {elapsed:.0f}s)")
        print(f"  Total positions : {len(snap2)}")
        print(f"  Military        : {len(mil2)}")
        print(f"  Civilian        : {len(civ2)}")

        if args.screenshots:
            path = f"{SS_DIR}/after.png"
            page.screenshot(path=path)
            print(f"  Screenshot: {path}")

        # ── Movement analysis ─────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"  MOVEMENT REPORT  (threshold: any change > 0 km)")
        print(f"{'='*60}")

        target1 = mil1 if args.mil_only else snap1
        target2 = mil2 if args.mil_only else snap2

        common = set(target1.keys()) & set(target2.keys())
        appeared  = set(target2.keys()) - set(target1.keys())
        vanished  = set(target1.keys()) - set(target2.keys())
        moved     = []
        stationary = []

        for k in sorted(common):
            p1 = target1[k]
            p2 = target2[k]
            try:
                dist = haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
            except Exception:
                dist = 0.0
            label = f"{p2.get('callsign', k):12s} src={p2.get('source',''):15s}"
            mil_marker = "[MIL]" if p2.get("military_flag") else "     "
            if dist > 0.01:  # moved more than ~10m
                moved.append((dist, k, p1, p2, label, mil_marker))
            else:
                stationary.append((k, label, mil_marker))

        # Sort by distance descending
        moved.sort(key=lambda x: -x[0])

        if moved:
            print(f"\n  {PASS} {len(moved)} aircraft MOVED:\n")
            for dist, k, p1, p2, label, mil_marker in moved:
                dlat = p2["lat"] - p1["lat"]
                dlon = p2["lon"] - p1["lon"]
                speed_est = (dist / (elapsed / 3600))  # km/h
                print(f"  {MOVE} {mil_marker} {label}")
                print(f"       ({p1['lat']:8.4f}, {p1['lon']:9.4f}) → "
                      f"({p2['lat']:8.4f}, {p2['lon']:9.4f})")
                print(f"       distance={dist:.2f} km  Δlat={dlat:+.4f}  Δlon={dlon:+.4f}  "
                      f"est_speed≈{speed_est:.0f} km/h")
        else:
            print(f"\n  {FAIL} No aircraft moved during the {elapsed:.0f}s window")
            print(f"       This may mean:")
            print(f"         • No position updates were pushed via WebSocket")
            print(f"         • The ADSB/AIS/TLE ingestor isn't running or returned no data")
            print(f"         • All aircraft are truly stationary (unlikely for this many)")

        mil_moved   = [x for x in moved if x[5].strip() == "[MIL]"]
        mil_static  = [(k, lbl, m) for k, lbl, m in stationary if m.strip() == "[MIL]"]

        print(f"\n  Military summary:")
        print(f"    Moved      : {len(mil_moved)}")
        print(f"    Stationary : {len(mil_static)}")
        print(f"    Appeared   : {sum(1 for k in appeared if (target2.get(k) or {}).get('military_flag'))}")
        print(f"    Vanished   : {sum(1 for k in vanished if (target1.get(k) or {}).get('military_flag'))}")

        if stationary:
            print(f"\n  Stationary ({len(stationary)} total — first 15):")
            for k, label, mil_marker in stationary[:15]:
                print(f"    - {mil_marker} {label}")
            if len(stationary) > 15:
                print(f"    ... and {len(stationary)-15} more")

        # ── WS message summary ───────────────────────────────────────────────
        ws_msgs = page.evaluate(JS_GET_WS_MSGS)
        print(f"\n  WebSocket position messages received: {len(ws_msgs)}")
        if ws_msgs:
            for m in ws_msgs[-5:]:
                ts_str = datetime.fromtimestamp(m['ts']/1000, tz=timezone.utc).strftime("%H:%M:%S")
                print(f"    [{ts_str}] {m['count']:4d} positions  "
                      f"({m['mil_count']} mil)  src={m['sources']}")
        else:
            print(f"    {FAIL} ZERO WebSocket position messages received!")
            print(f"         The server may not be broadcasting position updates.")
            print(f"         Check: core/engine.py _tick_adsb / _tick_mil_aircraft")

        print(f"\n{'='*60}\n")

        # Exit code: 0 if anything moved, 1 if nothing moved
        browser.close()
        if moved:
            print("RESULT: PASS — aircraft are moving on the map")
            sys.exit(0)
        else:
            print("RESULT: FAIL — no movement detected")
            sys.exit(1)


if __name__ == "__main__":
    run()
