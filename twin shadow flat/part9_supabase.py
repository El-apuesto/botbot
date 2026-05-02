"""
Twin Shadow — Part 9: Supabase Storage + Metadata
Uses the Supabase REST API directly via `requests` — no supabase-py dependency,
so there are zero httpx version conflicts with python-telegram-bot.

Bucket:  twin-shadow-lab  (one public bucket, two folders)
  uploads/   ← lab file uploads
  renders/   ← final assembled MP4s

Run this SQL once in your Supabase project (SQL Editor):
──────────────────────────────────────────────────────────────────
  CREATE TABLE IF NOT EXISTS lab_uploads (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    name         TEXT NOT NULL,
    file_type    TEXT,
    size_bytes   BIGINT,
    duration     FLOAT,
    storage_path TEXT,
    public_url   TEXT
  );

  CREATE TABLE IF NOT EXISTS lab_renders (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    name         TEXT NOT NULL,
    size_bytes   BIGINT,
    storage_path TEXT,
    public_url   TEXT
  );
──────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import os
import mimetypes
import requests
from pathlib import Path

from urllib.parse import unquote
_SB_URL = unquote(os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
_SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
_BUCKET = "twin-shadow-lab"


def is_configured() -> bool:
    return bool(_SB_URL and _SB_KEY)


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey":        _SB_KEY,
        "Authorization": f"Bearer {_SB_KEY}",
    }
    if extra:
        h.update(extra)
    return h


# ═══════════════════════════════════════════════════════════════════════════════
# BUCKET SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def setup_bucket() -> bool:
    """
    Ensure the storage bucket exists.  Creates it as PUBLIC if missing.
    Called once at startup in a daemon thread — failures are non-fatal.
    """
    if not is_configured():
        print("[SUPABASE] Not configured — skipping bucket setup")
        return False
    try:
        # Check existing buckets
        r = requests.get(
            f"{_SB_URL}/storage/v1/bucket",
            headers=_headers(),
            timeout=10,
        )
        existing = [b["name"] for b in (r.json() if r.ok else [])]
        if _BUCKET not in existing:
            r2 = requests.post(
                f"{_SB_URL}/storage/v1/bucket",
                headers=_headers({"Content-Type": "application/json"}),
                json={"id": _BUCKET, "name": _BUCKET, "public": True},
                timeout=10,
            )
            if r2.ok:
                print(f"[SUPABASE] Created bucket '{_BUCKET}'")
            else:
                print(f"[SUPABASE] Bucket create failed: {r2.text[:120]}")
                return False
        else:
            print(f"[SUPABASE] Bucket '{_BUCKET}' ready")
        return True
    except Exception as e:
        print(f"[SUPABASE] Bucket setup error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# STORAGE UPLOAD
# ═══════════════════════════════════════════════════════════════════════════════

def upload_file(local_path: str, folder: str = "uploads") -> tuple[str | None, str | None]:
    """
    Upload a local file to Supabase Storage.
    Returns (public_url, storage_path) or (None, None) on failure.
    """
    if not is_configured():
        return None, None
    try:
        p       = Path(local_path)
        sname   = p.name
        spath   = f"{folder}/{sname}"
        ctype   = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

        with open(local_path, "rb") as fh:
            data = fh.read()

        # Try UPSERT first (update if exists), fall back to POST (new upload)
        r = requests.post(
            f"{_SB_URL}/storage/v1/object/{_BUCKET}/{spath}",
            headers=_headers({
                "Content-Type":     ctype,
                "x-upsert":         "true",
            }),
            data=data,
            timeout=120,
        )
        if not r.ok:
            print(f"[SUPABASE] upload failed ({r.status_code}): {r.text[:120]}")
            return None, None

        public_url = f"{_SB_URL}/storage/v1/object/public/{_BUCKET}/{spath}"
        return public_url, spath
    except Exception as e:
        print(f"[SUPABASE] upload_file error ({local_path}): {e}")
        return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — PostgREST
# ═══════════════════════════════════════════════════════════════════════════════

def _pg_headers(extra: dict | None = None) -> dict:
    h = _headers({"Content-Type": "application/json", "Prefer": "return=minimal"})
    if extra:
        h.update(extra)
    return h


def log_upload(
    name: str,
    file_type: str,
    size_bytes: int,
    duration: float,
    public_url: str | None,
    storage_path: str | None,
) -> bool:
    if not is_configured():
        return False
    try:
        r = requests.post(
            f"{_SB_URL}/rest/v1/lab_uploads",
            headers=_pg_headers(),
            json={
                "name":         name,
                "file_type":    file_type,
                "size_bytes":   size_bytes,
                "duration":     duration,
                "public_url":   public_url,
                "storage_path": storage_path,
            },
            timeout=10,
        )
        if not r.ok:
            print(f"[SUPABASE] log_upload failed ({r.status_code}): {r.text[:120]}")
        return r.ok
    except Exception as e:
        print(f"[SUPABASE] log_upload error: {e}")
        return False


def log_render(
    name: str,
    size_bytes: int,
    public_url: str | None,
    storage_path: str | None,
) -> bool:
    if not is_configured():
        return False
    try:
        r = requests.post(
            f"{_SB_URL}/rest/v1/lab_renders",
            headers=_pg_headers(),
            json={
                "name":         name,
                "size_bytes":   size_bytes,
                "public_url":   public_url,
                "storage_path": storage_path,
            },
            timeout=10,
        )
        if not r.ok:
            print(f"[SUPABASE] log_render failed ({r.status_code}): {r.text[:120]}")
        return r.ok
    except Exception as e:
        print(f"[SUPABASE] log_render error: {e}")
        return False


def list_uploads(limit: int = 100) -> list[dict]:
    if not is_configured():
        return []
    try:
        r = requests.get(
            f"{_SB_URL}/rest/v1/lab_uploads",
            headers=_headers({"Accept": "application/json"}),
            params={"order": "created_at.desc", "limit": limit},
            timeout=10,
        )
        return r.json() if r.ok else []
    except Exception as e:
        print(f"[SUPABASE] list_uploads error: {e}")
        return []


def list_renders(limit: int = 50) -> list[dict]:
    if not is_configured():
        return []
    try:
        r = requests.get(
            f"{_SB_URL}/rest/v1/lab_renders",
            headers=_headers({"Accept": "application/json"}),
            params={"order": "created_at.desc", "limit": limit},
            timeout=10,
        )
        return r.json() if r.ok else []
    except Exception as e:
        print(f"[SUPABASE] list_renders error: {e}")
        return []


def delete_file(storage_path: str) -> bool:
    """Remove a file from Supabase Storage by its storage_path."""
    if not is_configured():
        return False
    try:
        r = requests.delete(
            f"{_SB_URL}/storage/v1/object/{_BUCKET}/{storage_path}",
            headers=_headers(),
            timeout=10,
        )
        return r.ok
    except Exception as e:
        print(f"[SUPABASE] delete_file error: {e}")
        return False
