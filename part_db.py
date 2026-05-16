"""
Twin Shadow — Database helper.
Thin wrapper around psycopg2 using DATABASE_URL from env.
All functions are synchronous (called from Flask routes).
"""
from __future__ import annotations
import json
import logging
import os
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
    _URL = os.environ.get("DATABASE_URL", "")
    _AVAILABLE = bool(_URL)
except ImportError:
    _AVAILABLE = False

@contextmanager
def _conn():
    if not _AVAILABLE:
        raise RuntimeError("Database not available")
    conn = psycopg2.connect(_URL)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_ok() -> bool:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False

# ── CHAT HISTORY ────────────────────────────────────────────────────────────

def history_append(username: str, bot: str, role: str, content: str) -> None:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_history (username, bot, role, content) VALUES (%s,%s,%s,%s)",
                    (username, bot, role, content)
                )
    except Exception as e:
        log.warning("history_append failed: %s", e)

def history_load(username: str, bot: str = None, limit: int = 60) -> list[dict]:
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if bot:
                    cur.execute(
                        "SELECT role, content FROM chat_history WHERE username=%s AND bot=%s ORDER BY created_at DESC LIMIT %s",
                        (username, bot, limit)
                    )
                else:
                    cur.execute(
                        "SELECT role, content FROM chat_history WHERE username=%s ORDER BY created_at DESC LIMIT %s",
                        (username, limit)
                    )
                rows = cur.fetchall()
                return [dict(r) for r in reversed(rows)]
    except Exception as e:
        log.warning("history_load failed: %s", e)
        return []

def history_clear(username: str, bot: str = None) -> None:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                if bot:
                    cur.execute("DELETE FROM chat_history WHERE username=%s AND bot=%s", (username, bot))
                else:
                    cur.execute("DELETE FROM chat_history WHERE username=%s", (username,))
    except Exception as e:
        log.warning("history_clear failed: %s", e)

# ── SESSIONS (boardroom / brainstorm / podcast) ──────────────────────────────

def session_save(username: str, session_type: str, title: str, content: list) -> int | None:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (username, session_type, title, content) VALUES (%s,%s,%s,%s) RETURNING id",
                    (username, session_type, title[:120], json.dumps(content))
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        log.warning("session_save failed: %s", e)
        return None

def session_list(username: str, session_type: str = None, limit: int = 50) -> list[dict]:
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if session_type:
                    cur.execute(
                        "SELECT id, session_type, title, created_at FROM sessions WHERE username=%s AND session_type=%s ORDER BY created_at DESC LIMIT %s",
                        (username, session_type, limit)
                    )
                else:
                    cur.execute(
                        "SELECT id, session_type, title, created_at FROM sessions WHERE username=%s ORDER BY created_at DESC LIMIT %s",
                        (username, limit)
                    )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.warning("session_list failed: %s", e)
        return []

def session_get(session_id: int, username: str) -> dict | None:
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM sessions WHERE id=%s AND username=%s",
                    (session_id, username)
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.warning("session_get failed: %s", e)
        return None

def session_delete(session_id: int, username: str) -> None:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE id=%s AND username=%s", (session_id, username))
    except Exception as e:
        log.warning("session_delete failed: %s", e)

# ── PIPELINE JOBS ─────────────────────────────────────────────────────────────

def pipeline_load(username: str) -> list[dict]:
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, name, description, status, meta FROM pipeline_jobs WHERE username=%s ORDER BY created_at ASC",
                    (username,)
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.warning("pipeline_load failed: %s", e)
        return []

def pipeline_add(username: str, job: dict) -> dict:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                jid = job.get("id") or __import__("uuid").uuid4().hex[:8]
                cur.execute(
                    "INSERT INTO pipeline_jobs (id, username, name, description, status, meta) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (jid, username, job.get("name",""), job.get("description",""), job.get("status","queued"), json.dumps(job))
                )
                job["id"] = jid
                return job
    except Exception as e:
        log.warning("pipeline_add failed: %s", e)
        return job

def pipeline_update_status(job_id: str, status: str, username: str) -> None:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pipeline_jobs SET status=%s, updated_at=NOW() WHERE id=%s AND username=%s",
                    (status, job_id, username)
                )
    except Exception as e:
        log.warning("pipeline_update_status failed: %s", e)

def pipeline_delete(job_id: str, username: str) -> None:
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pipeline_jobs WHERE id=%s AND username=%s", (job_id, username))
    except Exception as e:
        log.warning("pipeline_delete failed: %s", e)
