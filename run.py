"""Wardar — entrypoint"""
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
