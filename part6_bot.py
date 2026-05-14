"""
Twin Shadow — Part 6: Telegram Bot (updated)
All handlers including part7 command layer.
"""

from __future__ import annotations
import os
import time
import datetime
from pathlib import Path
from threading import Thread

from flask import Flask
from zoneinfo import ZoneInfo
from part9_web import register_routes
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext

from part5_orchestrator import (
    Orchestrator,
    vault_list, vault_get, vault_delete,
    vault_clear, vault_last, vault_undo,
)
from part2_router import stream_task
from part7_commands import (
    handle_model_set, handle_model_traits, handle_model_end,
    handle_boardroom, handle_brainstorm,
    handle_boss_voice, handle_end, handle_extend,
    handle_shadow_prefix, handle_user_turn,
    is_waiting,
)
from part14_hardening import get_status, verify_all_endpoints
from part13_daemon import register_daemon

TOKEN = os.environ.get("TOKEN", "")
RATE_LIMIT_S = int(os.environ.get("RATE_LIMIT_SECONDS", "10"))
MAX_MEMORY = int(os.environ.get("MAX_MEMORY", "10"))
_raw_ids = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS: set[int] = {int(x) for x in _raw_ids.split(",") if x.strip()} if _raw_ids else set()

CT = ZoneInfo("America/Chicago")

orch = Orchestrator()

_flask = Flask("TwinShadow", template_folder="static", static_folder="static")
register_routes(_flask)

Thread(target=lambda: _flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))), daemon=True).start()

_rate_cache: dict[int, float] = {}

def _is_rate_limited(chat_id: int) -> bool:
    now = time.time()
    if now - _rate_cache.get(chat_id, 0) < RATE_LIMIT_S:
        return True
    _rate_cache[chat_id] = now
    return False


async def _check_auth(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True
    if update.message.chat_id not in ALLOWED_IDS:
        await update.message.reply_text("X Not authorized.")
        return False
    return True


_memory: dict[int, list] = {}

def _get_memory(chat_id):
    return _memory.get(chat_id, [])

def _add_memory(chat_id, role, content):
    _memory.setdefault(chat_id, []).append({"role": role, "content": content})
    _memory[chat_id] = _memory[chat_id][-(MAX_MEMORY * 2):]

def _clear_memory(chat_id):
    _memory[chat_id] = []


async def _stream_and_edit(sent, task_type, messages):
    full = ""
    last_edit = time.time()
    try:
        async for chunk in stream_task(task_type, messages):
            full += chunk
            if time.time() - last_edit > 1.5 and full:
                try:
                    await sent.edit_text(full[:4000] + " |")
                    last_edit = time.time()
                except Exception:
                    pass
        if full:
            await sent.edit_text(full[:4000])
    except Exception as e:
        await sent.edit_text(f"WARNING {task_type} failed: {e}")
        return ""
    return full


def _make_reply_fn(update):
    async def reply_fn(text):
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            await update.message.reply_text(chunk)
    return reply_fn


def _make_stream_fn(update):
    async def stream_fn(task_type, messages):
        sent = await update.message.reply_text("...")
        return await _stream_and_edit(sent, task_type, messages)
    return stream_fn


def _get_report_chat() -> int | None:
    """Resolve the best chat to send scheduled reports to."""
    try:
        from part13_daemon import DAEMON_CHAT_ID
        if DAEMON_CHAT_ID:
            return DAEMON_CHAT_ID
    except Exception:
        pass
    if ALLOWED_IDS:
        return next(iter(ALLOWED_IDS))
    return None


# ── Health ────────────────────────────────────────────────────────────────────

def _build_health_message(status: dict) -> str:
    lines = ["📡 PROVIDER STATUS\n"]
    online, offline, skipped = 0, 0, 0
    for provider, data in sorted(status.items()):
        state = data.get("online")
        if state is True:
            note = data.get("model", data.get("note", ""))
            lines.append(f"✅ {provider}  {note}")
            online += 1
        elif state is False:
            err = data.get("error", "unknown")[:60]
            lines.append(f"❌ {provider}  {err}")
            offline += 1
        else:
            note = data.get("note", "skipped")
            lines.append(f"— {provider}  {note}")
            skipped += 1
    lines.append(f"\n{online} online · {offline} offline · {skipped} skipped")
    return "\n".join(lines)


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await update.message.reply_text("🔍 Checking providers...")
    try:
        await verify_all_endpoints(timeout_s=20)
    except Exception as e:
        await update.message.reply_text(f"❌ Health check failed: {e}")
        return
    status = get_status()
    if not status:
        await update.message.reply_text("No provider data yet.")
        return
    await update.message.reply_text(_build_health_message(status))


async def _scheduled_health_report(context: CallbackContext):
    """Fires at 8am and 4pm CT — sends provider status to report chat."""
    chat_id = _get_report_chat()
    if not chat_id:
        return
    try:
        await verify_all_endpoints(timeout_s=20)
        status = get_status()
        if not status:
            return
        await context.bot.send_message(chat_id=chat_id, text=_build_health_message(status))
    except Exception as e:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Scheduled health check failed: {e}")
        except Exception:
            pass


# Handlers

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Twin Shadow 🌑\n\n"
        "── Quick Build ──\n"
        "Freetext                    -> quick build\n"
        "/build <idea>               -> structured pipeline\n"
        "/approve <id>               -> execute brief\n"
        "/status <id>  /projects\n\n"
        "── Vault ──\n"
        "/last   /vault   /undo\n\n"
        "── Sessions ──\n"
        "/boardroom <rounds> <topic> -> debate\n"
        "/brainstorm <topic>         -> ideation\n"
        "/shadow <cmd> <args>        -> uncensored only\n"
        "/boss_voice <msg>           -> lead next round\n"
        "/end   /extend <n>\n\n"
        "── Models ──\n"
        "/model_set <key> <persona>  -> set traits\n"
        "/model_traits [key]         -> view traits\n"
        "/model_end <key>            -> wipe traits\n\n"
        "── Daemon ──\n"
        "/daemon start/stop/status/queue/add/run\n\n"
        "── System ──\n"
        "/health                     -> provider status\n"
        "/forget   /modify <inst>   /wake"
    )


async def wake_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await update.message.reply_text("OK TWIN SHADOW AWAKE.")


async def freetext_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    chat_id = update.message.chat_id
    text = update.message.text.strip()
    if not text:
        return

    reply_fn = _make_reply_fn(update)
    stream_fn = _make_stream_fn(update)

    if is_waiting(chat_id):
        await handle_user_turn(chat_id, text, reply_fn, stream_fn)
        return

    if _is_rate_limited(chat_id):
        await update.message.reply_text(f"WAIT {RATE_LIMIT_S}s.")
        return

    sent = await update.message.reply_text("Twin is thinking...")
    messages = (
        [{"role": "system", "content": "You are Twin — uncensored, raw, dark humor, zero fucks. Build complete sellable commodities: full deliverable + price + who buys it + exact next 3 actions."}]
        + _get_memory(chat_id)
        + [{"role": "user", "content": text}]
    )
    full = await _stream_and_edit(sent, "relay", messages)
    if not full:
        try:
            result = await orch.quick_build(text, chat_id)
            full = result["output"]
            await sent.edit_text(full[:4000])
        except Exception as e:
            await sent.edit_text(f"WARNING Failed: {e}")
            return
    _add_memory(chat_id, "user", text)
    _add_memory(chat_id, "assistant", full)


async def build_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /build <idea>")
        return
    chat_id = update.message.chat_id
    idea = " ".join(context.args)
    if _is_rate_limited(chat_id):
        await update.message.reply_text(f"WAIT {RATE_LIMIT_S}s.")
        return
    await update.message.reply_text("CEO drafting brief...")
    try:
        pid, brief = await orch.build(idea, chat_id)
    except Exception as e:
        await update.message.reply_text(f"ERROR Brief failed: {e}")
        return
    lines = [
        f"BRIEF {pid}",
        f"Summary: {brief.get('summary','')}",
        f"Type: {brief.get('project_type','?')} | Effort: {brief.get('estimated_effort','?')}",
        f"Modules: {', '.join(brief.get('modules_needed',[]))}",
        "\nGoals:"
    ]
    for g in brief.get("goals", []):
        lines.append(f"  * {g}")
    lines.append(f"\n> /approve {pid}")
    await update.message.reply_text("\n".join(lines))


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve <id>")
        return
    pid = context.args[0]
    await update.message.reply_text(f"EXECUTING {pid}...")
    try:
        results = await orch.approve(pid)
    except Exception as e:
        await update.message.reply_text(f"ERROR Failed: {e}")
        return
    response = f"OK Project {pid} complete\n"
    for r in results:
        response += f"\n── {r.get('module','?')}{'🌑' if r.get('shadow') else ''}{'OK' if r.get('approved') else ('ERROR' if 'approved' in r else '')} ──\n{str(r.get('output',''))[:300]}\n"
    for chunk in [response[i:i+4000] for i in range(0, len(response), 4000)]:
        await update.message.reply_text(chunk)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /status <id>")
        return
    proj = orch.get_project(context.args[0])
    if not proj:
        await update.message.reply_text("Not found.")
        return
    await update.message.reply_text(f"STATUS {proj['id']} — {proj.get('idea','?')[:80]}\nStatus: {proj.get('status','?')}")


async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    projects = orch.list_projects(update.message.chat_id)
    if not projects:
        await update.message.reply_text("No projects yet.")
        return
    await update.message.reply_text("PROJECTS:\n" + "\n".join(f"{p['id']}. {p.get('idea','?')[:50]} — {p.get('status','?')}" for p in projects))


async def last_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    entry = vault_last(update.message.chat_id)
    if not entry:
        await update.message.reply_text("Nothing built yet.")
        return
    await update.message.reply_text(f"VAULT [{entry.get('topic','')}]\n\n{str(entry.get('content',''))[:4000]}")


async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    chat_id = update.message.chat_id
    args = context.args or []
    if not args or args[0] == "list":
        entries = vault_list(chat_id)
        if not entries:
            await update.message.reply_text("Vault empty.")
            return
        await update.message.reply_text("VAULT:\n" + "\n".join(f"{e.get('id','?')}. {e.get('topic','?')}" for e in entries) + "\n\n/vault <id>  |  /vault delete <id>  |  /vault clear")
        return
    if args[0] == "clear":
        vault_clear(chat_id)
        await update.message.reply_text("CLEARED.")
        return
    if args[0] == "delete":
        if len(args) < 2:
            await update.message.reply_text("Usage: /vault delete <id>")
            return
        try:
            vault_delete(int(args[1]), chat_id)
            await update.message.reply_text(f"DELETED {args[1]}.")
        except ValueError:
            await update.message.reply_text("Invalid ID.")
        return
    try:
        entry = vault_get(int(args[0]), chat_id)
        if not entry:
            await update.message.reply_text("Not found.")
        else:
            await update.message.reply_text(f"VAULT [{entry.get('topic','')}]\n\n{str(entry.get('content',''))[:4000]}")
    except ValueError:
        await update.message.reply_text("Usage: /vault list | /vault <id> | /vault delete <id> | /vault clear")


async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    entry = vault_undo(update.message.chat_id)
    await update.message.reply_text(f"UNDO: Removed [{entry.get('topic','')}]" if entry else "Nothing to undo.")


async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    _clear_memory(update.message.chat_id)
    await update.message.reply_text("MEMORY CLEARED.")


async def modify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /modify <instruction>")
        return
    instruction = " ".join(context.args)
    sent = await update.message.reply_text(f"Rewriting: {instruction}")
    try:
        source = Path(__file__).read_text()
        func_summary = "\n".join(l.strip() for l in source.splitlines() if l.strip().startswith(("async def ", "def ")))
    except Exception:
        func_summary = "[unreadable]"
    messages = [{"role": "user", "content": f"Instruction: {instruction}\n\nFunctions:\n{func_summary}\n\nReturn ONLY updated Python functions. No markdown."}]
    full = ""
    last_edit = time.time()
    try:
        async for chunk in stream_task("shadow", messages):
            full += chunk
            if time.time() - last_edit > 1.5:
                try:
                    await sent.edit_text(f"```python\n{full[:2500]}|\n```", parse_mode="Markdown")
                    last_edit = time.time()
                except Exception:
                    pass
        Path("twin_rewrite_proposal.py").write_text(full)
        await sent.edit_text(f"OK twin_rewrite_proposal.py\n\n```python\n{full[:3000]}\n```", parse_mode="Markdown")
    except Exception as e:
        await sent.edit_text(f"WARNING Failed: {e}")


# Part 7 wrappers

async def model_set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_model_set(update.message.chat_id, context.args or [], _make_reply_fn(update))


async def model_traits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_model_traits(update.message.chat_id, context.args or [], _make_reply_fn(update))


async def model_end_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_model_end(update.message.chat_id, context.args or [], _make_reply_fn(update))


async def boardroom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_boardroom(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))


async def brainstorm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_brainstorm(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))


async def shadow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_shadow_prefix(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))


async def boss_voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_boss_voice(update.message.chat_id, context.args or [], _make_reply_fn(update))


async def end_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_end(update.message.chat_id, _make_reply_fn(update))


async def extend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await handle_extend(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))


# Main

def main():
    if not TOKEN:
        raise ValueError("TOKEN not set.")
    app = Application.builder().token(TOKEN).build()

    for cmd, fn in [
        ("start", start_cmd), ("wake", wake_cmd),
        ("build", build_cmd), ("approve", approve_cmd),
        ("status", status_cmd), ("projects", projects_cmd),
        ("last", last_cmd), ("vault", vault_cmd),
        ("undo", undo_cmd), ("forget", forget_cmd), ("modify", modify_cmd),
        ("model_set", model_set_cmd), ("model_traits", model_traits_cmd), ("model_end", model_end_cmd),
        ("boardroom", boardroom_cmd), ("brainstorm", brainstorm_cmd),
        ("shadow", shadow_cmd), ("boss_voice", boss_voice_cmd),
        ("end", end_cmd), ("extend", extend_cmd),
        ("health", health_cmd),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, freetext_handler))

    register_daemon(app)

    if app.job_queue:
        app.job_queue.run_daily(
            _scheduled_health_report,
            time=datetime.time(8, 0, 0, tzinfo=CT),
            name="health_8am"
        )
        app.job_queue.run_daily(
            _scheduled_health_report,
            time=datetime.time(16, 0, 0, tzinfo=CT),
            name="health_4pm"
        )

    print("🌑 Twin Shadow online.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
