"""Wardar — entrypoint"""
import os

def _load_env():
    """Load .env file if present (dev convenience — Railway sets vars directly)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if k and k not in os.environ:  # don't override real env vars
                os.environ[k] = v

_load_env()

import uvicorn
from config import settings as C

if __name__ == "__main__":
    uvicorn.run(
        "api.server:app",
        host=C.APP_HOST,
        port=C.APP_PORT,
        reload=False,
        log_level="info",
    )
