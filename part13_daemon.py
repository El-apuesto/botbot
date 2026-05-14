"""
Twin Shadow — Part 8: Autonomous Daemon

Self-driving task engine. Fires on a heartbeat, pulls its own queue,
executes tasks, generates follow-ups, and reports to Telegram.
No human trigger required.

Wire into part6_bot.py main():
    from part8_daemon import register_daemon
    register_daemon(app)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram.ext import Application, CommandHandler, ContextTypes, CallbackContext

from part2_router import stream_task

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DAEMON_CHAT_ID: Optional[int] = None          # set via /daemon setchat
DAEMON_RUNNING = False
HEARTBEAT_INTERVAL = int(os.environ.get("DAEMON_INTERVAL", "300"))  # seconds
DB_PATH = Path("daemon_tasks.db")

# ── DB (SQLite, no extra deps) ─────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task      TEXT NOT NULL,
            status    TEXT NOT NULL DEFAULT 'pending',
            priority  INTEGER NOT NULL DEFAULT 5,
            parent_id INTEGER,
            result    TEXT,
            created   REAL NOT NULL,
            executed  REAL
        )
    """)
    conn.commit()
    return conn


def _add_task(task: str, priority: int = 5, parent_id: Optional[int] = None) -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (task, priority, parent_id, created) VALUES (?, ?, ?, ?)",
            (task, priority, parent_id, time.time())
        )
        return cur.lastrowid


def _next_task() -> Optional[sqlite3.Row]:
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE status='pending' ORDER BY priority ASC, id ASC LIMIT 1"
        ).fetchone()


def _mark_done(task_id: int, result: str):
    with _db() as conn:
        conn.execute(
            "UPDATE tasks SET status='done', result=?, executed=? WHERE id=?",
            (result[:2000], time.time(), task_id)
        )


def _mark_failed(task_id: int, error: str):
    with _db() as conn:
        conn.execute(
            "UPDATE tasks SET status='failed', result=? WHERE id=?",
            (error[:500], task_id)
        )


def _queue_snapshot() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, task, status, priority, created FROM tasks ORDER BY id DESC LIMIT 20"
        ).fetchall()
        return [dict(r) for r in rows]


def _clear_done():
    with _db() as conn:
        conn.execute("DELETE FROM tasks WHERE status IN ('done','failed')")


# ── LLM calls ─────────────────────────────────────────────────────────────────

async def _run_task(task: str) -> str:
    """Execute a task through the model router and return the full result."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are Twin Shadow's autonomous execution engine. "
                "You receive a task and complete it fully. "
                "Produce a concrete, actionable deliverable — not a plan, an actual output. "
                "End your response with a JSON block tagged <NEXT_TASKS> containing an array "
                "of 1-3 follow-up task strings that logically continue this work. "
                "Example: <NEXT_TASKS>[\"Research competitors for X\",\"Draft pricing model\"]</NEXT_TASKS>"
            )
        },
        {"role": "user", "content": task}
    ]
    full = ""
    try:
        async for chunk in stream_task("relay", messages):
            full += chunk
    except Exception as e:
        raise RuntimeError(f"stream_task failed: {e}")
    return full


def _extract_next_tasks(result: str) -> list[str]:
    """Pull follow-up tasks from <NEXT_TASKS>[...] in model output."""
    try:
        start = result.find("<NEXT_TASKS>")
        end = result.find("</NEXT_TASKS>")
        if start == -1 or end == -1:
            return []
        raw = result[start + len("<NEXT_TASKS>"):end].strip()
        tasks = json.loads(raw)
        return [t for t in tasks if isinstance(t, str) and t.strip()][:3]
    except Exception:
        return []


# ── Heartbeat ─────────────────────────────────────────────────────────────────

async def _heartbeat(context: CallbackContext):
    global DAEMON_RUNNING, DAEMON_CHAT_ID

    if not DAEMON_RUNNING:
        return
    if not DAEMON_CHAT_ID:
        logger.warning("Daemon: no chat_id set, skipping beat")
        return

    task_row = _next_task()
    if not task_row:
        logger.info("Daemon: queue empty, generating seed task")
        _add_task(
            "Identify the single highest-value thing Twin Shadow could build or research right now "
            "to generate real-world results. Be specific. Produce a concrete output.",
            priority=9
        )
        task_row = _next_task()

    task_id = task_row["id"]
    task_text = task_row["task"]

    await context.bot.send_message(
        chat_id=DAEMON_CHAT_ID,
        text=f"⚙️ DAEMON EXECUTING [{task_id}]\n{task_text[:200]}"
    )

    try:
        result = await _run_task(task_text)
        _mark_done(task_id, result)

        # Queue follow-ups
        follow_ups = _extract_next_tasks(result)
        for ft in follow_ups:
            _add_task(ft, priority=5, parent_id=task_id)

        # Strip the NEXT_TASKS block from what we send
        clean_result = result
        nt_start = result.find("<NEXT_TASKS>")
        if nt_start != -1:
            clean_result = result[:nt_start].strip()

        report = (
            f"✅ DONE [{task_id}]\n\n"
            f"{clean_result[:3000]}"
        )
        if follow_ups:
            report += f"\n\n📋 Queued {len(follow_ups)} follow-ups:\n" + "\n".join(f"  • {t}" for t in follow_ups)

        # Telegram 4096 char limit
        for chunk in [report[i:i+4000] for i in range(0, len(report), 4000)]:
            await context.bot.send_message(chat_id=DAEMON_CHAT_ID, text=chunk)

    except Exception as e:
        _mark_failed(task_id, str(e))
        await context.bot.send_message(
            chat_id=DAEMON_CHAT_ID,
            text=f"❌ FAILED [{task_id}]: {e}"
        )


# ── Commands ───────────────────────────────────────────────────────────────────

async def daemon_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    global DAEMON_RUNNING, DAEMON_CHAT_ID

    args = context.args or []
    sub = args[0].lower() if args else "status"
    chat_id = update.message.chat_id

    if sub == "start":
        DAEMON_RUNNING = True
        DAEMON_CHAT_ID = chat_id
        await update.message.reply_text(
            f"🌑 DAEMON STARTED\n"
            f"Heartbeat every {HEARTBEAT_INTERVAL}s\n"
            f"Reports → this chat\n\n"
            f"Commands: /daemon stop | status | queue | add <task> | flush | setchat"
        )

    elif sub == "stop":
        DAEMON_RUNNING = False
        await update.message.reply_text("⏹ DAEMON STOPPED")

    elif sub == "setchat":
        DAEMON_CHAT_ID = chat_id
        await update.message.reply_text(f"📡 Report channel set to this chat ({chat_id})")

    elif sub == "status":
        rows = _queue_snapshot()
        pending = sum(1 for r in rows if r["status"] == "pending")
        done = sum(1 for r in rows if r["status"] == "done")
        failed = sum(1 for r in rows if r["status"] == "failed")
        status = "🟢 RUNNING" if DAEMON_RUNNING else "🔴 STOPPED"
        await update.message.reply_text(
            f"DAEMON {status}\n"
            f"Interval: {HEARTBEAT_INTERVAL}s\n"
            f"Queue: {pending} pending | {done} done | {failed} failed\n"
            f"Report chat: {DAEMON_CHAT_ID or 'not set'}"
        )

    elif sub == "queue":
        rows = _queue_snapshot()
        if not rows:
            await update.message.reply_text("Queue empty.")
            return
        lines = []
        for r in rows:
            status_icon = {"pending": "⏳", "done": "✅", "failed": "❌"}.get(r["status"], "?")
            ts = datetime.fromtimestamp(r["created"]).strftime("%H:%M")
            lines.append(f"{status_icon} [{r['id']}] p{r['priority']} {ts} — {r['task'][:60]}")
        await update.message.reply_text("QUEUE:\n" + "\n".join(lines))

    elif sub == "add":
        if len(args) < 2:
            await update.message.reply_text("Usage: /daemon add <task>")
            return
        task_text = " ".join(args[1:])
        tid = _add_task(task_text, priority=1)  # priority 1 = goes first
        await update.message.reply_text(f"📥 Task [{tid}] added with high priority:\n{task_text}")

    elif sub == "flush":
        _clear_done()
        await update.message.reply_text("🗑 Done/failed tasks cleared.")

    elif sub == "run":
        # Force immediate execution of next task
        if not DAEMON_CHAT_ID:
            DAEMON_CHAT_ID = chat_id
        DAEMON_RUNNING = True

        class FakeContext:
            bot = context.bot

        await _heartbeat(FakeContext())

    else:
        await update.message.reply_text(
            "DAEMON commands:\n"
            "/daemon start       — begin autonomous loop\n"
            "/daemon stop        — pause\n"
            "/daemon status      — health check\n"
            "/daemon queue       — show task queue\n"
            "/daemon add <task>  — inject task (runs next)\n"
            "/daemon run         — force immediate beat\n"
            "/daemon flush       — clear completed tasks\n"
            "/daemon setchat     — set this chat as report channel"
        )


# ── Registration ───────────────────────────────────────────────────────────────

def register_daemon(app: Application):
    """
    Call this from part6_bot.py main() after building the Application.

        from part8_daemon import register_daemon
        register_daemon(app)
    """
    app.add_handler(CommandHandler("daemon", daemon_cmd))

    if app.job_queue:
        app.job_queue.run_repeating(
            _heartbeat,
            interval=HEARTBEAT_INTERVAL,
            first=30,  # first beat 30s after startup
            name="twin_shadow_heartbeat"
        )
        logger.info(f"Daemon heartbeat registered: every {HEARTBEAT_INTERVAL}s, first in 30s")
    else:
        logger.warning("JobQueue not available — install 'python-telegram-bot[job-queue]'")
