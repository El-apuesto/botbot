"""
Twin Shadow — Part 6: Telegram Bot (updated)
All handlers including part7 command layer.
"""

from __future__ import annotations
import os
import time
import json
import queue
import asyncio
import socket
import threading
import tempfile
from pathlib import Path
from threading import Thread

from flask import Flask, send_from_directory, send_file, request, Response, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from part5_orchestrator import (
    Orchestrator,
    vault_list, vault_get, vault_delete,
    vault_clear, vault_last, vault_undo,
)
from part2_router import stream_task, call_task, direct_call
from part1_registry import (
    get_task_routing, get_provider_cfg,
    BOARD_MEMBERS, SHADOW_BOARD_MEMBERS,
    BRAINSTORM_MEMBERS, SHADOW_BRAINSTORM_MEMBERS,
    TTS_VOICES,
)
from part8_personas import (
    TWIN_SYSTEM, SHADOW_SYSTEM, CAPI_SYSTEM,
    board_member_system, brainstorm_system, BUILDER_SYSTEM, MODERATOR_SYSTEM,
)
from part7_commands import (
    handle_model_set, handle_model_traits, handle_model_end,
    handle_boardroom, handle_brainstorm,
    handle_boss_voice, handle_end, handle_extend,
    handle_shadow_prefix, handle_user_turn,
    is_waiting,
)

TOKEN        = os.environ.get("TOKEN", "")
RATE_LIMIT_S = int(os.environ.get("RATE_LIMIT_SECONDS", "10"))
MAX_MEMORY   = int(os.environ.get("MAX_MEMORY", "10"))
_raw_ids     = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS: set[int] = {int(x) for x in _raw_ids.split(",") if x.strip()} if _raw_ids else set()

orch = Orchestrator()

def _find_port(start: int = 5000) -> int:
    for p in range(start, start + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(('0.0.0.0', p)) != 0:
                return p
    return start

_static_dir = Path(__file__).parent / "static"
_audio_dir  = Path(__file__).parent / "audio"

_flask = Flask("TwinShadow", static_folder=str(_static_dir), static_url_path="/static")

@_flask.route("/")
def _home():
    return send_from_directory(str(_static_dir), "index.html")

@_flask.route("/audio/<path:filename>")
def _audio(filename):
    return send_from_directory(str(_audio_dir), filename, mimetype="audio/mpeg")

_port = _find_port(int(os.environ.get("PORT", 5000)))

# ── Pipeline store (in-memory, JSON-persisted) ────────────────────────────────
_pipeline_file = Path(__file__).parent / "pipeline.json"
def _pipeline_load() -> list:
    try:
        return json.loads(_pipeline_file.read_text()) if _pipeline_file.exists() else []
    except Exception:
        return []
def _pipeline_save(jobs: list):
    try: _pipeline_file.write_text(json.dumps(jobs, indent=2))
    except Exception: pass

# ── Async→Flask bridge (real streaming via queue) ─────────────────────────────
def _async_gen_to_queue(async_gen_fn, q: queue.Queue, *args, **kwargs):
    async def _run():
        try:
            async for chunk in async_gen_fn(*args, **kwargs):
                q.put(("data", chunk))
        except Exception as e:
            q.put(("error", str(e)))
        finally:
            q.put(("done", None))
    asyncio.run(_run())

def _sse_stream(async_gen_fn, *args, **kwargs):
    """Wrap an async generator into a Flask SSE Response."""
    q: queue.Queue = queue.Queue()
    t = threading.Thread(target=_async_gen_to_queue, args=(async_gen_fn, q, *args), kwargs=kwargs, daemon=True)
    t.start()
    def _gen():
        while True:
            kind, val = q.get()
            if kind == "data":
                yield f"data: {json.dumps({'text': val})}\n\n"
            elif kind == "error":
                yield f"data: {json.dumps({'error': val})}\n\n"
                break
            else:
                yield "data: {\"done\": true}\n\n"
                break
    return Response(_gen(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

def _run_async(coro):
    return asyncio.run(coro)

# ── /api/chat ─────────────────────────────────────────────────────────────────
@_flask.route("/api/chat", methods=["POST"])
def api_chat():
    data     = request.json or {}
    message  = data.get("message", "").strip()
    bot      = data.get("bot", "twin").lower()
    history  = data.get("history", [])
    username = data.get("username", "USER")
    sys_override = data.get("system_prompt", "")
    if not message:
        return jsonify({"error": "No message"}), 400

    if bot == "shadow":
        sys_prompt = sys_override or SHADOW_SYSTEM
        task_type  = "shadow_chat"
    else:
        sys_prompt = sys_override or TWIN_SYSTEM
        task_type  = "twin"

    msgs = [{"role": "system", "content": sys_prompt}]
    for h in history[-20:]:
        if h.get("role") in ("user", "assistant"):
            msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": message})

    async def _gen():
        async for chunk in stream_task(task_type, msgs):
            yield chunk

    return _sse_stream(_gen)

# ── /api/boardroom ────────────────────────────────────────────────────────────
@_flask.route("/api/boardroom", methods=["POST"])
def api_boardroom():
    data           = request.json or {}
    topic          = data.get("topic", "").strip()
    shadow_mode    = data.get("shadow_mode", False)
    sys_override   = data.get("system_prompt", "").strip()
    if not topic:
        return jsonify({"error": "No topic"}), 400

    members = SHADOW_BOARD_MEMBERS if shadow_mode else BOARD_MEMBERS

    async def _run():
        # Step 1: TWIN briefs the topic
        twin_sys = sys_override or TWIN_SYSTEM
        brief_msgs = [
            {"role": "system", "content": twin_sys},
            {"role": "user",   "content": f"Brief this topic for the boardroom in 3 sentences max: {topic}"},
        ]
        yield f"\x1eSPEAKER:TWIN\x1f"
        brief = ""
        async for chunk in stream_task("twin", brief_msgs):
            brief += chunk
            yield chunk
        yield "\x1eEND\x1f"

        # Step 2: Each board member responds
        context = f"Topic: {topic}\n\nTWIN's brief: {brief}"
        for member in members:
            name = member["name"]
            role = member.get("role", name)
            sys_p = board_member_system(name, role, shadow_mode)
            provider_key, model_key = get_task_routing(member["key"])
            msgs = [
                {"role": "system", "content": sys_p},
                {"role": "user",   "content": context + f"\n\nYour response as {name} ({role}):"},
            ]
            yield f"\x1eSPEAKER:{name}\x1f"
            member_resp = ""
            try:
                async for chunk in stream_task(member["key"], msgs):
                    member_resp += chunk
                    yield chunk
            except Exception as e:
                yield f"[{name} offline: {e}]"
            yield "\x1eEND\x1f"
            context += f"\n\n{name}: {member_resp[:300]}"

        # Step 3: SHADOW final word (always)
        shadow_msgs = [
            {"role": "system", "content": SHADOW_SYSTEM},
            {"role": "user",   "content": f"Boardroom discussed: {topic}\n\nContext:\n{context[:1500]}\n\nYour final word:"},
        ]
        yield f"\x1eSPEAKER:SHADOW\x1f"
        try:
            async for chunk in stream_task("shadow_chat", shadow_msgs):
                yield chunk
        except Exception as e:
            yield f"[SHADOW offline: {e}]"
        yield "\x1eEND\x1f"

    return _sse_stream(_run)

# ── /api/brainstorm ───────────────────────────────────────────────────────────
@_flask.route("/api/brainstorm", methods=["POST"])
def api_brainstorm():
    data         = request.json or {}
    topic        = data.get("topic", "").strip()
    shadow_mode  = data.get("shadow_mode", False)
    podcast_mode = data.get("podcast_mode", False)
    rounds       = min(int(data.get("rounds", 2)), 3)
    sys_override = data.get("system_prompt", "").strip()
    if not topic:
        return jsonify({"error": "No topic"}), 400

    members = SHADOW_BRAINSTORM_MEMBERS if shadow_mode else BRAINSTORM_MEMBERS

    async def _run():
        context = f"Topic: {topic}"
        for rnd in range(1, rounds + 1):
            for member in members:
                name = member["name"]
                sys_p = sys_override or brainstorm_system(name, podcast_mode)
                msgs = [
                    {"role": "system", "content": sys_p},
                    {"role": "user",   "content": context + f"\n\nRound {rnd} — {name}:"},
                ]
                yield f"\x1eSPEAKER:{name}\x1f"
                resp = ""
                try:
                    async for chunk in stream_task(member["key"], msgs):
                        resp += chunk
                        yield chunk
                except Exception as e:
                    yield f"[{name} offline: {e}]"
                yield "\x1eEND\x1f"
                context += f"\n\n{name} (round {rnd}): {resp[:250]}"

            # TWIN moderates between rounds
            if rnd < rounds:
                mod_msgs = [
                    {"role": "system", "content": MODERATOR_SYSTEM},
                    {"role": "user",   "content": context[-1200:]},
                ]
                yield f"\x1eSPEAKER:TWIN\x1f"
                mod = ""
                try:
                    async for chunk in stream_task("twin", mod_msgs):
                        mod += chunk
                        yield chunk
                except Exception as e:
                    yield f"[TWIN offline: {e}]"
                yield "\x1eEND\x1f"
                context += f"\n\nTWIN (mod): {mod[:200]}"

    return _sse_stream(_run)

# ── /api/tts ──────────────────────────────────────────────────────────────────
@_flask.route("/api/tts", methods=["POST"])
def api_tts():
    """
    Body: { "turns": [{"speaker": "TWIN", "text": "..."}, ...], "voice_overrides": {} }
    Returns: { "url": "/audio/<filename>" }
    Falls back to gTTS single-voice if Google Cloud TTS not configured.
    """
    data   = request.json or {}
    turns  = data.get("turns", [])
    voice_overrides = data.get("voice_overrides", {})
    if not turns:
        return jsonify({"error": "No turns"}), 400

    _audio_dir.mkdir(exist_ok=True)
    fname = f"session_{int(time.time())}.mp3"
    out_path = _audio_dir / fname

    gc_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

    if gc_creds:
        # Google Cloud TTS multi-voice
        try:
            from google.cloud import texttospeech
            from pydub import AudioSegment
            import io

            tts_client = texttospeech.TextToSpeechClient()
            segments = []
            for turn in turns:
                speaker = turn.get("speaker", "NARRATOR").upper()
                text    = turn.get("text", "").strip()
                if not text:
                    continue
                voice_name = voice_overrides.get(speaker) or TTS_VOICES.get(speaker, "en-US-Neural2-D")
                lang = voice_name[:5]
                synthesis_input = texttospeech.SynthesisInput(text=text)
                voice = texttospeech.VoiceSelectionParams(
                    language_code=lang,
                    name=voice_name,
                )
                audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
                response = tts_client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
                seg = AudioSegment.from_mp3(io.BytesIO(response.audio_content))
                segments.append(seg)

            if segments:
                combined = segments[0]
                for seg in segments[1:]:
                    combined += seg
                combined.export(str(out_path), format="mp3")
                return jsonify({"url": f"/audio/{fname}"})
            return jsonify({"error": "No audio generated"}), 500

        except Exception as e:
            print(f"[TTS] Google Cloud TTS failed: {e} — falling back to gTTS")

    # gTTS fallback — single narrator voice, concatenate all text
    try:
        from gtts import gTTS
        full_text = " ".join(
            f"{t.get('speaker','')}: {t.get('text','')}" for t in turns if t.get("text")
        )
        tts = gTTS(text=full_text, lang="en", slow=False)
        tts.save(str(out_path))
        return jsonify({"url": f"/audio/{fname}"})
    except Exception as e:
        return jsonify({"error": f"TTS failed: {e}"}), 500

# ── /api/builder ──────────────────────────────────────────────────────────────
@_flask.route("/api/builder", methods=["POST"])
def api_builder():
    data         = request.json or {}
    task         = data.get("task", "").strip()
    sys_override = data.get("system_prompt", "").strip()
    if not task:
        return jsonify({"error": "No task"}), 400

    async def _run():
        yield f"\x1eSPEAKER:BUILDER\x1f"
        code = ""
        msgs = [
            {"role": "system", "content": sys_override or BUILDER_SYSTEM},
            {"role": "user",   "content": task},
        ]
        try:
            async for chunk in stream_task("builder", msgs):
                code += chunk
                yield chunk
        except Exception as e:
            yield f"[BUILDER offline: {e}]"
        yield "\x1eEND\x1f"

        # Quick review by QWEN
        if code and len(code) > 20:
            review_msgs = [
                {"role": "system", "content": "Review this code in 2 sentences. Flag any critical bugs only."},
                {"role": "user",   "content": code[:3000]},
            ]
            yield f"\x1eSPEAKER:QWEN\x1f"
            try:
                async for chunk in stream_task("builder_check", review_msgs):
                    yield chunk
            except Exception as e:
                yield f"[QWEN review skipped: {e}]"
            yield "\x1eEND\x1f"

    return _sse_stream(_run)

# ── /api/pipeline ─────────────────────────────────────────────────────────────
@_flask.route("/api/pipeline", methods=["GET"])
def api_pipeline_get():
    return jsonify(_pipeline_load())

@_flask.route("/api/pipeline", methods=["POST"])
def api_pipeline_add():
    data = request.json or {}
    if not data.get("name"):
        return jsonify({"error": "name required"}), 400
    jobs = _pipeline_load()
    job  = {
        "id":         int(time.time() * 1000),
        "name":       data["name"],
        "status":     "queued",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    jobs.append(job)
    _pipeline_save(jobs)
    return jsonify(job)

@_flask.route("/api/pipeline/<int:job_id>", methods=["DELETE"])
def api_pipeline_delete(job_id):
    jobs = [j for j in _pipeline_load() if j.get("id") != job_id]
    _pipeline_save(jobs)
    return jsonify({"ok": True})

Thread(target=lambda: _flask.run(host="0.0.0.0", port=_port, debug=False, use_reloader=False), daemon=True).start()

_rate_cache: dict[int, float] = {}
def _is_rate_limited(chat_id: int) -> bool:
    now = time.time()
    if now - _rate_cache.get(chat_id, 0) < RATE_LIMIT_S:
        return True
    _rate_cache[chat_id] = now
    return False

async def _check_auth(update: Update) -> bool:
    if not ALLOWED_IDS: return True
    if update.message.chat_id not in ALLOWED_IDS:
        await update.message.reply_text("⛔ Not authorized.")
        return False
    return True

_memory: dict[int, list] = {}
def _get_memory(chat_id): return _memory.get(chat_id, [])
def _add_memory(chat_id, role, content):
    _memory.setdefault(chat_id, []).append({"role": role, "content": content})
    _memory[chat_id] = _memory[chat_id][-(MAX_MEMORY * 2):]
def _clear_memory(chat_id): _memory[chat_id] = []

async def _stream_and_edit(sent, task_type, messages):
    full = ""; last_edit = time.time()
    try:
        async for chunk in stream_task(task_type, messages):
            full += chunk
            if time.time() - last_edit > 1.5 and full:
                try:
                    await sent.edit_text(full[:4000] + " ▌"); last_edit = time.time()
                except Exception: pass
        if full: await sent.edit_text(full[:4000])
    except Exception as e:
        await sent.edit_text(f"⚠️ {task_type} failed: {e}"); return ""
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

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Twin Shadow 🌑\n\n"
        "── Quick Build ──\n"
        "Freetext                    → quick build\n"
        "/build <idea>               → structured pipeline\n"
        "/approve <id>               → execute brief\n"
        "/status <id>  /projects\n\n"
        "── Vault ──\n"
        "/last   /vault   /undo\n\n"
        "── Sessions ──\n"
        "/boardroom <rounds> <topic> → debate\n"
        "/brainstorm <topic>         → ideation\n"
        "/shadow <cmd> <args>        → uncensored only\n"
        "/boss_voice <msg>           → lead next round\n"
        "/end   /extend <n>\n\n"
        "── Models ──\n"
        "/model_set <key> <persona>  → set traits\n"
        "/model_traits [key]         → view traits\n"
        "/model_end <key>            → wipe traits\n\n"
        "── Misc ──\n"
        "/forget   /modify <inst>   /wake"
    )

async def wake_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await update.message.reply_text("✅ TWIN SHADOW AWAKE.")

async def freetext_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    chat_id = update.message.chat_id
    text    = update.message.text.strip()
    if not text: return

    reply_fn  = _make_reply_fn(update)
    stream_fn = _make_stream_fn(update)

    if is_waiting(chat_id):
        await handle_user_turn(chat_id, text, reply_fn, stream_fn)
        return

    if _is_rate_limited(chat_id):
        await update.message.reply_text(f"⏱ Wait {RATE_LIMIT_S}s."); return

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
            full = result["output"]; await sent.edit_text(full[:4000])
        except Exception as e:
            await sent.edit_text(f"⚠️ Failed: {e}"); return
    _add_memory(chat_id, "user", text)
    _add_memory(chat_id, "assistant", full)

async def build_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args: await update.message.reply_text("Usage: /build <idea>"); return
    chat_id = update.message.chat_id
    idea    = " ".join(context.args)
    if _is_rate_limited(chat_id): await update.message.reply_text(f"⏱ Wait {RATE_LIMIT_S}s."); return
    await update.message.reply_text("CEO drafting brief...")
    try:
        pid, brief = await orch.build(idea, chat_id)
    except Exception as e:
        await update.message.reply_text(f"❌ Brief failed: {e}"); return
    lines = [f"📋 Project {pid}", f"Summary: {brief.get('summary','')}", f"Type: {brief.get('project_type','?')} | Effort: {brief.get('estimated_effort','?')}", f"Modules: {', '.join(brief.get('modules_needed',[]))}", "\nGoals:"]
    for g in brief.get("goals", []): lines.append(f"  • {g}")
    lines.append(f"\n→ /approve {pid}")
    await update.message.reply_text("\n".join(lines))

async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args: await update.message.reply_text("Usage: /approve <id>"); return
    pid = context.args[0]
    await update.message.reply_text(f"⏳ Executing {pid}...")
    try:
        results = await orch.approve(pid)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}"); return
    response = f"✅ Project {pid} complete\n"
    for r in results:
        response += f"\n── {r.get('module','?')}{'🌑' if r.get('shadow') else ''}{'✅' if r.get('approved') else ('❌' if 'approved' in r else '')} ──\n{str(r.get('output',''))[:300]}\n"
    for chunk in [response[i:i+4000] for i in range(0, len(response), 4000)]:
        await update.message.reply_text(chunk)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args: await update.message.reply_text("Usage: /status <id>"); return
    proj = orch.get_project(context.args[0])
    if not proj: await update.message.reply_text("Not found."); return
    await update.message.reply_text(f"📊 {proj['id']} — {proj.get('idea','?')[:80]}\nStatus: {proj.get('status','?')}")

async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    projects = orch.list_projects(update.message.chat_id)
    if not projects: await update.message.reply_text("No projects yet."); return
    await update.message.reply_text("📚 Projects:\n" + "\n".join(f"{p['id']}. {p.get('idea','?')[:50]} — {p.get('status','?')}" for p in projects))

async def last_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    entry = vault_last(update.message.chat_id)
    if not entry: await update.message.reply_text("Nothing built yet."); return
    await update.message.reply_text(f"📦 [{entry.get('topic','')}]\n\n{str(entry.get('content',''))[:4000]}")

async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    chat_id = update.message.chat_id; args = context.args or []
    if not args or args[0] == "list":
        entries = vault_list(chat_id)
        if not entries: await update.message.reply_text("Vault empty."); return
        await update.message.reply_text("📦 VAULT:\n" + "\n".join(f"{e.get('id','?')}. {e.get('topic','?')}" for e in entries) + "\n\n/vault <id>  |  /vault delete <id>  |  /vault clear")
        return
    if args[0] == "clear": vault_clear(chat_id); await update.message.reply_text("🗑 Cleared."); return
    if args[0] == "delete":
        if len(args) < 2: await update.message.reply_text("Usage: /vault delete <id>"); return
        try: vault_delete(int(args[1]), chat_id); await update.message.reply_text(f"🗑 {args[1]} deleted.")
        except ValueError: await update.message.reply_text("Invalid ID.")
        return
    try:
        entry = vault_get(int(args[0]), chat_id)
        if not entry: await update.message.reply_text("Not found.")
        else: await update.message.reply_text(f"📦 [{entry.get('topic','')}]\n\n{str(entry.get('content',''))[:4000]}")
    except ValueError:
        await update.message.reply_text("Usage: /vault list | /vault <id> | /vault delete <id> | /vault clear")

async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    entry = vault_undo(update.message.chat_id)
    await update.message.reply_text(f"↩️ Removed: [{entry.get('topic','')}]" if entry else "Nothing to undo.")

async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    _clear_memory(update.message.chat_id); await update.message.reply_text("🧹 Memory cleared.")

async def modify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args: await update.message.reply_text("Usage: /modify <instruction>"); return
    instruction = " ".join(context.args)
    sent = await update.message.reply_text(f"Rewriting: {instruction}")
    try:
        source = Path(__file__).read_text()
        func_summary = "\n".join(l.strip() for l in source.splitlines() if l.strip().startswith(("async def ", "def ")))
    except Exception: func_summary = "[unreadable]"
    messages = [{"role": "user", "content": f"Instruction: {instruction}\n\nFunctions:\n{func_summary}\n\nReturn ONLY updated Python functions. No markdown."}]
    full = ""; last_edit = time.time()
    try:
        async for chunk in stream_task("shadow", messages):
            full += chunk
            if time.time() - last_edit > 1.5:
                try: await sent.edit_text(f"```python\n{full[:2500]}▌\n```", parse_mode="Markdown"); last_edit = time.time()
                except Exception: pass
        Path("twin_rewrite_proposal.py").write_text(full)
        await sent.edit_text(f"✅ twin_rewrite_proposal.py\n\n```python\n{full[:3000]}\n```", parse_mode="Markdown")
    except Exception as e: await sent.edit_text(f"⚠️ Failed: {e}")

# ── Part 7 wrappers ───────────────────────────────────────────────────────────

async def model_set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_model_set(update.message.chat_id, context.args or [], _make_reply_fn(update))

async def model_traits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_model_traits(update.message.chat_id, context.args or [], _make_reply_fn(update))

async def model_end_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_model_end(update.message.chat_id, context.args or [], _make_reply_fn(update))

async def boardroom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_boardroom(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))

async def brainstorm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_brainstorm(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))

async def shadow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_shadow_prefix(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))

async def boss_voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_boss_voice(update.message.chat_id, context.args or [], _make_reply_fn(update))

async def end_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_end(update.message.chat_id, _make_reply_fn(update))

async def extend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await handle_extend(update.message.chat_id, context.args or [], _make_reply_fn(update), _make_stream_fn(update))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN: raise ValueError("TOKEN not set.")
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
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, freetext_handler))
    print("🌑 Twin Shadow online.")
    app.run_polling(drop_pending_updates=True)
