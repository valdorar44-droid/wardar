"""
Wardar — Media Storage Backend

Supports two backends, selected automatically by env vars:

  LOCAL (default — no config needed):
    Files saved to dashboard/uploads/, served via /static/uploads/

  S3-COMPATIBLE (Cloudflare R2, AWS S3, Backblaze B2):
    Set these Railway env vars:
      S3_ENDPOINT_URL      https://<accountid>.r2.cloudflarestorage.com  (R2)
                           https://s3.amazonaws.com                       (AWS)
                           https://s3.us-west-004.backblazeb2.com         (B2)
      S3_ACCESS_KEY_ID     R2 API token / AWS access key / B2 keyID
      S3_SECRET_ACCESS_KEY R2 API secret / AWS secret / B2 applicationKey
      S3_BUCKET_NAME       your-bucket-name
      S3_PUBLIC_URL        https://pub-xxx.r2.dev  (R2 public bucket URL)
                           https://your-bucket.s3.amazonaws.com          (AWS)
                           or your custom domain e.g. https://media.wardar.app
      S3_REGION            auto  (R2) | us-east-1 (AWS) | us-west-004 (B2)
"""
from __future__ import annotations
import asyncio
import os
import uuid

from config import settings as C

# ── Backend detection ─────────────────────────────────────────────────────────

def _s3_configured() -> bool:
    return bool(
        C.S3_BUCKET_NAME
        and C.S3_ACCESS_KEY_ID
        and C.S3_SECRET_ACCESS_KEY
        and C.S3_PUBLIC_URL
    )


def _get_s3_client():
    import boto3
    kwargs: dict = {
        "aws_access_key_id":     C.S3_ACCESS_KEY_ID,
        "aws_secret_access_key": C.S3_SECRET_ACCESS_KEY,
        "region_name":           C.S3_REGION or "auto",
    }
    if C.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = C.S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


# ── Local helpers ─────────────────────────────────────────────────────────────

_DASH        = os.path.join(os.path.dirname(__file__), "..", "dashboard")
_UPLOADS_DIR = os.path.join(_DASH, "uploads")


def _ensure_local_dir():
    os.makedirs(_UPLOADS_DIR, exist_ok=True)


# ── Public API ────────────────────────────────────────────────────────────────

async def save(data: bytes, ext: str, content_type: str) -> str:
    """
    Save media bytes, return the public URL string.

    ext          — file extension without dot (e.g. 'mp4', 'jpg')
    content_type — MIME type for S3 metadata
    """
    fname = f"{uuid.uuid4().hex}.{ext}"

    if _s3_configured():
        return await asyncio.to_thread(_upload_s3, data, fname, content_type)
    else:
        return _save_local(data, fname)


def _upload_s3(data: bytes, fname: str, content_type: str) -> str:
    client = _get_s3_client()
    client.put_object(
        Bucket=C.S3_BUCKET_NAME,
        Key=fname,
        Body=data,
        ContentType=content_type,
        # R2/S3 public read — needed if bucket is NOT set to public-read by default
        # Comment this line out if your bucket policy already grants public read.
        # ACL="public-read",
    )
    base = C.S3_PUBLIC_URL.rstrip("/")
    return f"{base}/{fname}"


def _save_local(data: bytes, fname: str) -> str:
    _ensure_local_dir()
    dest = os.path.join(_UPLOADS_DIR, fname)
    with open(dest, "wb") as f:
        f.write(data)
    return f"/static/uploads/{fname}"


def backend_name() -> str:
    return "s3" if _s3_configured() else "local"
