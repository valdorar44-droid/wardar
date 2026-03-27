#!/usr/bin/env python3
"""
One-shot runtime audit for Wardar — writes NDJSON to DEBUG_LOG_PATH or workspace openclaw/debug-c5b7b7.log.
Run from repo root:  python tools/debug_audit_runtime.py
Uses a temp DB file so it does not touch production wardar.db unless WARDAR_DB_PATH is set.
"""
from __future__ import annotations
import json, os, sys, time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

_LOG_DEFAULT = os.path.join(_ROOT, "..", "openclaw", "debug-c5b7b7.log")
LOG_PATH = os.environ.get("DEBUG_LOG_PATH", _LOG_DEFAULT)
_TMP_DB = os.path.join(_ROOT, ".wardar_audit_tmp.db")
os.environ.setdefault("WARDAR_DB_PATH", _TMP_DB)


def dbg(hyp: str, msg: str, data: dict | None = None) -> None:
    line = {
        "sessionId": "c5b7b7",
        "hypothesisId": hyp,
        "location": "tools/debug_audit_runtime.py",
        "message": msg,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def main() -> None:
    dbg("BOOT", "audit_start", {"wardar_root": _ROOT, "log": LOG_PATH, "db": os.environ.get("WARDAR_DB_PATH")})

    try:
        from config import settings as C
        dbg("H1", "settings", {"APP_PORT": C.APP_PORT, "default_secret": C.SECRET_KEY == "change-me-in-production"})
    except Exception as e:
        dbg("H1", "settings_fail", {"err": str(e)[:200]})
        return

    try:
        from db import store
        store.init_db()
        store.get_conn()
        dbg("H2", "db", dict(store.get_counts()))
    except Exception as e:
        dbg("H2", "db_fail", {"err": str(e)[:300]})
        return

    try:
        from core.engine import apply_delay
        dbg("H3", "delay_civilian", {"out": apply_delay("2026-01-01T12:00:00+00:00", 0, "adsb")[:28]})
        dbg("H3", "delay_military", {"out": apply_delay("2026-01-01T12:00:00+00:00", 1, "adsb")[:28]})
    except Exception as e:
        dbg("H3", "delay_fail", {"err": str(e)[:200]})

    for name in ("ingestors.adsb", "ingestors.ais", "ingestors.tle"):
        try:
            __import__(name)
            dbg("H4", f"import_ok", {"module": name})
        except Exception as e:
            dbg("H4", f"import_fail", {"module": name, "err": str(e)[:150]})

    dbg("BOOT", "audit_done", {})


if __name__ == "__main__":
    main()
