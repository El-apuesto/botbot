"""
Twin Shadow - Part 6: Telegram Bot (updated)
All handlers including part7 command layer.
"""

from __future__ import annotations
import os
import re
import time
import json
import uuid
import queue
import shutil
import asyncio
import socket
import threading
import tempfile
from pathlib import Path
from threading import Thread

from flask import Flask, send_from_directory, send_file, request, Response, jsonify, session, redirect, render_template
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from part5_orchestrator import (
    Orchestrator,
    vault_list, vault_get, vault_delete,
    vault_clear, vault_last, vault_undo,
    inject_capi_identity, hermes_exchange, committee_discuss,
)
from runtime.events import subscribe, unsubscribe, iter_events
from part2_router import stream_task, call_task, direct_call
import part9_supabase as _sb
from part4_video import (
    fal_generate_image, extract_last_frame, download_file,
    images_to_slideshow, concatenate_clips, concatenate_clips_with_fade,
    mix_audio_onto_video, get_video_duration,
)
from part1_registry import (
    get_task_routing, get_provider_cfg,
    BOARD_MEMBERS, SHADOW_BOARD_MEMBERS,
    BRAINSTORM_MEMBERS, SHADOW_BRAINSTORM_MEMBERS,
    TTS_VOICES, TOKEN_LIMITS, get_specialists_for_topic, COMMITTEE_STRUCTURE,
)
import part8_hardening as _hardening
from part8_personas import (
    TWIN_SYSTEM, SHADOW_SYSTEM, CAPI_SYSTEM,
    board_member_system, brainstorm_system, BUILDER_SYSTEM,
    MODERATOR_SYSTEM, SHADOW_MODERATOR_SYSTEM, SHADOW_BRIEF_SYSTEM,
)
from part7_commands import (
    handle_model_set, handle_model_traits, handle_model_end,
    handle_boardroom, handle_brainstorm,
    handle_boss_voice, handle_end, handle_extend,
    handle_shadow_prefix, handle_user_turn,
    is_waiting,
    build_system_prompt,
)

TOKEN        = os.environ.get("TOKEN", "")
RATE_LIMIT_S = int(os.environ.get("RATE_LIMIT_SECONDS", "10"))
MAX_MEMORY   = int(os.environ.get("MAX_MEMORY", "10"))
_raw_ids     = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS: set[int] = {int(x) for x in _raw_ids.split(",") if x.strip()} if _raw_ids else set()

orch = Orchestrator()

def _find_port(start: int = 5000) -> int:
    return start

_static_dir = Path(__file__).parent / "static"
_audio_dir  = Path(__file__).parent / "audio"

_flask = Flask("TwinShadow", static_folder=str(_static_dir), static_url_path="/static",
               template_folder=str(Path(__file__).parent / "static"))
_flask.secret_key = os.environ.get("SB_SECRET") or os.environ.get("WEB_PASSWORD", "") or os.urandom(24).hex()

# -- Web authentication --------------------------------------------------------
_WEB_PASS = os.environ.get("WEB_PASSWORD") or os.environ.get("SB_SECRET", "")

def _is_web_authed() -> bool:
    return not _WEB_PASS or session.get("web_authed") is True

_AUTH_EXEMPT = {
    "/login",
    "/logout",
    "/health",
    "/api/video/status",
    "/api/video/upload",
    "/api/video/render",
    "/api/video/progress",
}

@_flask.before_request
def _check_web_auth():
    if request.path in _AUTH_EXEMPT:
        return None
    if request.path.startswith("/static/"):
        return None
    if not _is_web_authed():
        if request.path.startswith("/api/"):
            token = request.headers.get("Authorization", "")

            if token == f"Bearer {_WEB_PASS}":
                return None

            return jsonify({
                "error": "Not authenticated"
            }), 401
        return redirect("/login")

def _require_web_auth(fn):
    return fn  # kept for decorator syntax on / and /audio - before_request covers all

@_flask.route("/login", methods=["GET", "POST"])
def _login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == _WEB_PASS:
            session["web_authed"] = True
            session.permanent = True
            return redirect("/")
        error = "INCORRECT ACCESS CODE."
    return render_template("login.html", error=error)

@_flask.route("/logout")
def _logout():
    session.clear()
    return redirect("/login")

@_flask.route("/")
@_require_web_auth
def _home():
    og_url = request.url_root.rstrip("/")
    return render_template("index.html", og_url=og_url)

@_flask.route("/audio/<path:filename>")
@_require_web_auth
def _audio(filename):
    return send_from_directory(str(_audio_dir), filename, mimetype="audio/mpeg")


_port = _find_port(int(os.environ.get("PORT", 5000)))

# -- Pipeline store (in-memory, JSON-persisted) --------------------------------
_pipeline_file = Path(__file__).parent / "pipeline.json"
def _pipeline_load() -> list:
    try:
        return json.loads(_pipeline_file.read_text()) if _pipeline_file.exists() else []
    except Exception:
        return []
def _pipeline_save(jobs: list):
    try: _pipeline_file.write_text(json.dumps(jobs, indent=2))
    except Exception: pass

# -- Async-Flask bridge (real streaming via queue) -----------------------------
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
            try:
                kind, val = q.get(timeout=12)
            except queue.Empty:
                # Keepalive comment - prevents mobile Safari / proxy timeouts
                yield ": keepalive\n\n"
                continue
            if kind == "data":
                yield f"data: {json.dumps({'text': val})}\n\n"
            elif kind == "error":
                yield f"data: {json.dumps({'error': val})}\n\n"
                break
            else:
                yield "data: {\"done\": true}\n\n"
                break
    return Response(_gen(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})

def _run_async(coro):
    return asyncio.run(coro)

# -- /api/chat -----------------------------------------------------------------
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
        task_type  = "shadow_chat"
        sys_prompt = build_system_prompt(task_type, sys_override or SHADOW_SYSTEM)
    elif bot == "capi":
        task_type  = "capi"
        sys_prompt = build_system_prompt(task_type, sys_override or CAPI_SYSTEM)
    else:
        task_type  = "twin"
        sys_prompt = build_system_prompt(task_type, sys_override or TWIN_SYSTEM)

    # Apply CAPI identity context based on who is receiving the message
    sys_prompt = inject_capi_identity(sys_prompt, bot)

    from part1_registry import TOKEN_LIMITS as _TL
    chat_max_tokens = _TL.get("capi" if bot == "capi" else "shadow" if bot == "shadow" else "twin", 300)

    msgs = [{"role": "system", "content": sys_prompt}]
    for h in history[-20:]:
        if h.get("role") in ("user", "assistant"):
            msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": message})

    async def _gen():
        async for chunk in stream_task(task_type, msgs, max_tokens=chat_max_tokens):
            yield chunk

    return _sse_stream(_gen)

# -- /api/boardroom ------------------------------------------------------------
@_flask.route("/api/boardroom", methods=["POST"])
def api_boardroom():
    data           = request.json or {}
    topic          = data.get("topic", "").strip()
    shadow_mode    = data.get("shadow_mode", False)
    sys_override   = data.get("system_prompt", "").strip()
    steer          = data.get("steer", "").strip()          # user steering injection
    prior_context  = data.get("prior_context", "").strip()  # accumulated prior rounds
    round_num      = int(data.get("round_num", 1))
    if not topic:
        return jsonify({"error": "No topic"}), 400

    base_members = SHADOW_BOARD_MEMBERS if shadow_mode else BOARD_MEMBERS
    main_token_cap = TOKEN_LIMITS["shadow_board"] if shadow_mode else TOKEN_LIMITS["board_main"]

    async def _run():
        # Step 1: Brief the topic - SHADOW leads in shadow mode, TWIN in normal mode
        if shadow_mode:
            brief_sys = sys_override or SHADOW_BRIEF_SYSTEM
            brief_task = "shadow_chat"
            brief_speaker = "SHADOW"
        else:
            brief_sys = sys_override or TWIN_SYSTEM
            brief_task = "twin"
            brief_speaker = "TWIN"

        if round_num == 1:
            brief_prompt = f"Brief this topic for the boardroom in 2 sentences max: {topic}"
        else:
            steer_note = f" The group has been steered: {steer}" if steer else ""
            brief_prompt = (
                f"Round {round_num} on: {topic}.{steer_note}\n\n"
                f"Previous discussion:\n{prior_context[-800:]}\n\n"
                f"Pivot the discussion forward in 2 sentences max."
            )
        brief_msgs = [
            {"role": "system", "content": brief_sys},
            {"role": "user",   "content": brief_prompt},
        ]
        yield f"\x1eSPEAKER:{brief_speaker}\x1f"
        if round_num > 1:
            yield f"[Round {round_num}]"
            if steer:
                yield f" - Steering: {steer}\n\n"
            else:
                yield "\n\n"
        brief = ""
        async for chunk in stream_task(brief_task, brief_msgs, max_tokens=TOKEN_LIMITS["twin"]):
            brief += chunk
            yield chunk
        yield "\x1eEND\x1f"

        # Step 2: Get topic-matched specialists (non-shadow mode only)
        specialist_members = [] if shadow_mode else get_specialists_for_topic(topic)
        all_members = base_members + specialist_members

        # Step 3: Run pre-discussion committees for NVIDIA seats in background (round 1 only)
        committee_context: dict[str, str] = {}
        if not shadow_mode and round_num == 1:
            nvidia_seats = [m for m in base_members if COMMITTEE_STRUCTURE.get(m["key"])]
            if nvidia_seats:
                pre_results = await asyncio.gather(
                    *[committee_discuss(m["key"], topic, brief) for m in nvidia_seats],
                    return_exceptions=True,
                )
                for m, result in zip(nvidia_seats, pre_results):
                    if isinstance(result, str) and result:
                        committee_context[m["key"]] = result

        # Step 4: Build context - seed with prior rounds + steer + this round's brief
        context_turns: list[str] = []
        if prior_context:
            context_turns.append(f"[Prior rounds]\n{prior_context[-800:]}")
        if steer:
            context_turns.append(f"[USER DIRECTIVE - Round {round_num}]: {steer}")
        context_turns += [f"Topic: {topic}", f"TWIN brief: {brief}"]

        hermes_turn_count = 0
        for member in all_members:
            name = member["name"]
            role = member.get("role", name)
            member_key = member["key"]

            # HERMES gets the capped dialogue protocol instead of stream_task
            if name == "HERMES":
                context = "\n\n".join(context_turns[-8:])
                yield f"\x1eSPEAKER:HERMES\x1f"
                try:
                    exchange = await hermes_exchange(topic, context, session_type="boardroom", turn_count=hermes_turn_count)
                    hermes_turn_count = exchange.get("turns_used", hermes_turn_count + 1)
                    spark = exchange.get("hermes_spark", "")
                    interp = exchange.get("interpretation", "")
                    clarify = exchange.get("clarification")
                    retry_interp = exchange.get("retry_interpretation")
                    silenced = exchange.get("silenced", False)
                    if spark:
                        yield spark
                    if interp:
                        yield f"\n\n[Interpretation]\n{interp}"
                    if clarify:
                        yield f"\n\n[Hermes clarifies] {clarify}"
                    if retry_interp:
                        yield f"\n\n[Revised] {retry_interp}"
                    if silenced:
                        yield "\n\n[HERMES silenced - intent unresolved]"
                    context_turns.append(f"HERMES: {spark[:200]}")
                except Exception as e:
                    yield f"[HERMES offline: {e}]"
                yield "\x1eEND\x1f"
                continue

            # Build system prompt with CAPI identity injection for non-HERMES members
            sys_p = sys_override or board_member_system(name, role, shadow_mode)
            sys_p = build_system_prompt(member_key, sys_p)
            sys_p = inject_capi_identity(sys_p, name)

            # Rolling 8-turn context window
            context = "\n\n".join(context_turns[-8:])

            # Prepend committee brief for NVIDIA seats
            user_content = context + f"\n\nYour response as {name} ({role}):"
            if member_key in committee_context:
                user_content = (
                    f"[Pre-session committee brief]\n{committee_context[member_key]}\n\n"
                    + user_content
                )

            msgs = [
                {"role": "system", "content": sys_p},
                {"role": "user",   "content": user_content},
            ]
            yield f"\x1eSPEAKER:{name}\x1f"
            member_resp = ""
            try:
                async for chunk in stream_task(member_key, msgs, max_tokens=main_token_cap):
                    member_resp += chunk
                    yield chunk
            except Exception as e:
                yield f"[{name} offline: {e}]"
            yield "\x1eEND\x1f"
            context_turns.append(f"{name}: {member_resp[:300]}")

        # Step 5: SHADOW final word - only if SHADOW is not already a regular board member
        board_names = {m["name"] for m in base_members}
        if not shadow_mode and "SHADOW" not in board_names:
            final_context = "\n\n".join(context_turns[-8:])
            shadow_msgs = [
                {"role": "system", "content": inject_capi_identity(build_system_prompt("shadow_chat", SHADOW_SYSTEM), "shadow")},
                {"role": "user",   "content": f"Boardroom discussed: {topic}\n\nContext:\n{final_context}\n\nYour final word:"},
            ]
            yield f"\x1eSPEAKER:SHADOW\x1f"
            try:
                async for chunk in stream_task("shadow_chat", shadow_msgs, max_tokens=TOKEN_LIMITS["shadow_board"]):
                    yield chunk
            except Exception as e:
                yield f"[SHADOW offline: {e}]"
            yield "\x1eEND\x1f"

    return _sse_stream(_run)

# -- /api/brainstorm -----------------------------------------------------------
@_flask.route("/api/brainstorm", methods=["POST"])
def api_brainstorm():
    data         = request.json or {}
    topic        = data.get("topic", "").strip()
    shadow_mode  = data.get("shadow_mode", False)
    podcast_mode = data.get("podcast_mode", False)
    rounds       = min(int(data.get("rounds", 1)), 3)
    sys_override = data.get("system_prompt", "").strip()
    prior_context = data.get("prior_context", "").strip()  # accumulated from prior sessions
    steer         = data.get("steer", "").strip()          # user extension directive
    extend_round  = int(data.get("extend_round", 0))       # which round we're extending from
    if not topic:
        return jsonify({"error": "No topic"}), 400

    members = SHADOW_BRAINSTORM_MEMBERS if shadow_mode else BRAINSTORM_MEMBERS
    brainstorm_cap = TOKEN_LIMITS.get("brainstorm", TOKEN_LIMITS["board_main"])

    async def _run():
        # Seed context with prior session content + user steering directive
        context_turns: list[str] = [f"Topic: {topic}"]
        if prior_context:
            context_turns.append(f"[Prior session]\n{prior_context[-800:]}")
        if steer:
            context_turns.append(f"[USER EXTENSION DIRECTIVE]: {steer}")
        hermes_turn_count = 0

        for rnd in range(1, rounds + 1):
            for member in members:
                name       = member["name"]
                member_key = member["key"]
                context    = "\n\n".join(context_turns[-8:])

                # HERMES: capped dialogue protocol
                if name == "HERMES":
                    yield f"\x1eSPEAKER:HERMES\x1f"
                    try:
                        exchange = await hermes_exchange(topic, context, session_type="brainstorm", turn_count=hermes_turn_count)
                        hermes_turn_count = exchange.get("turns_used", hermes_turn_count + 1)
                        spark = exchange.get("hermes_spark", "")
                        interp = exchange.get("interpretation", "")
                        clarify = exchange.get("clarification")
                        retry_interp = exchange.get("retry_interpretation")
                        silenced = exchange.get("silenced", False)
                        if spark:
                            yield spark
                        if interp:
                            yield f"\n\n[Interpretation]\n{interp}"
                        if clarify:
                            yield f"\n\n[Hermes clarifies] {clarify}"
                        if retry_interp:
                            yield f"\n\n[Revised] {retry_interp}"
                        if silenced:
                            yield "\n\n[HERMES silenced - intent unresolved]"
                        context_turns.append(f"HERMES (round {rnd}): {spark[:200]}")
                    except Exception as e:
                        yield f"[HERMES offline: {e}]"
                    yield "\x1eEND\x1f"
                    continue

                # All other members: apply inject_capi_identity on system prompt
                sys_p = sys_override or brainstorm_system(name, podcast_mode)
                sys_p = build_system_prompt(member_key, sys_p)
                sys_p = inject_capi_identity(sys_p, name)

                msgs = [
                    {"role": "system", "content": sys_p},
                    {"role": "user",   "content": context + f"\n\nRound {rnd} - {name}:"},
                ]
                yield f"\x1eSPEAKER:{name}\x1f"
                resp = ""
                try:
                    async for chunk in stream_task(member_key, msgs, max_tokens=brainstorm_cap):
                        resp += chunk
                        yield chunk
                except Exception as e:
                    yield f"[{name} offline: {e}]"
                yield "\x1eEND\x1f"
                context_turns.append(f"{name} (round {rnd}): {resp[:250]}")

            # Moderate between rounds - SHADOW leads in shadow mode, TWIN in normal
            if rnd < rounds:
                context = "\n\n".join(context_turns[-8:])
                if shadow_mode:
                    mod_sys  = SHADOW_MODERATOR_SYSTEM
                    mod_task = "shadow_chat"
                    mod_speaker = "SHADOW"
                else:
                    mod_sys  = inject_capi_identity(MODERATOR_SYSTEM, "twin")
                    mod_task = "twin"
                    mod_speaker = "TWIN"
                mod_msgs = [
                    {"role": "system", "content": mod_sys},
                    {"role": "user",   "content": context},
                ]
                yield f"\x1eSPEAKER:{mod_speaker}\x1f"
                mod = ""
                try:
                    async for chunk in stream_task(mod_task, mod_msgs, max_tokens=TOKEN_LIMITS["twin"]):
                        mod += chunk
                        yield chunk
                except Exception as e:
                    yield f"[{mod_speaker} offline: {e}]"
                yield "\x1eEND\x1f"
                context_turns.append(f"{mod_speaker} (mod): {mod[:200]}")

    return _sse_stream(_run)

# -- /api/transcript/save ------------------------------------------------------
@_flask.route("/api/transcript/save", methods=["POST"])
def api_transcript_save():
    data         = request.json or {}
    title        = data.get("title", "session").strip()
    content      = data.get("content", "").strip()
    session_type = data.get("session_type", "boardroom")
    if not content:
        return jsonify({"ok": False, "error": "No content"}), 400
    result = _sb.save_transcript(title, content, session_type)
    return jsonify(result)

# -- /api/audio/list -----------------------------------------------------------
@_flask.route("/api/audio/list", methods=["GET"])
def api_audio_list():
    _audio_dir.mkdir(exist_ok=True)
    files = []
    for p in sorted(_audio_dir.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = p.stat()
        files.append({
            "name": p.name,
            "url":  f"/audio/{p.name}",
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    return jsonify(files)

# -- /api/transcripts/list -----------------------------------------------------
@_flask.route("/api/transcripts/list", methods=["GET"])
def api_transcripts_list():
    return jsonify(_sb.list_transcripts(50))

# -------------------------------------------------------------------------------
# SMART EDITOR - /api/lab/transcribe|highlights|cut|metadata|reformat|thumbnail
# -------------------------------------------------------------------------------

@_flask.route("/api/lab/transcribe", methods=["POST"])
def api_lab_transcribe():
    from part4_video import extract_audio
    import mimetypes, requests as _req
    data      = request.json or {}
    file_path = data.get("path", "").strip()
    if not file_path or not Path(file_path).exists():
        return jsonify({"error": "File not found"}), 400

    groq_key = (os.environ.get("GROQ_API_KEY_1") or
                os.environ.get("GROQ_API_KEY_2") or
                os.environ.get("GROQ_API_KEY_3") or "")
    if not groq_key:
        return jsonify({"error": "No Groq API key configured"}), 500

    send_path = file_path
    tmp_audio = None
    video_exts = {".mp4", ".mov", ".webm", ".mkv"}
    if Path(file_path).suffix.lower() in video_exts:
        tmp_audio = str(_lab_renders_dir / f"_audio_{int(time.time())}.mp3")
        ok, err = extract_audio(file_path, tmp_audio)
        if ok:
            send_path = tmp_audio
        else:
            print(f"[SMART EDIT] audio extract failed: {err}")

    try:
        # Route through task registry: fast=true - whisper_turbo, else whisper_v3
        fast_mode   = data.get("fast", False)
        from part1_registry import get_provider_cfg
        _groq_cfg   = get_provider_cfg("groq")
        _model_key  = "whisper_turbo" if fast_mode else "whisper_v3"
        whisper_model = _groq_cfg["models"].get(_model_key, "whisper-large-v3")
        ctype = mimetypes.guess_type(send_path)[0] or "audio/mpeg"
        with open(send_path, "rb") as fh:
            resp = _req.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": (Path(send_path).name, fh, ctype)},
                data={"model": whisper_model,
                      "response_format": "verbose_json",
                      "timestamp_granularities[]": "segment"},
                timeout=180,
            )
        if tmp_audio:
            Path(tmp_audio).unlink(missing_ok=True)
        if not resp.ok:
            return jsonify({"error": resp.text[:300]}), 500
        r = resp.json()
        return jsonify({
            "text":     r.get("text", ""),
            "segments": r.get("segments", []),
            "duration": r.get("duration", 0),
            "language": r.get("language", "en"),
        })
    except Exception as e:
        if tmp_audio:
            Path(tmp_audio).unlink(missing_ok=True)
        return jsonify({"error": str(e)}), 500


@_flask.route("/api/lab/highlights", methods=["POST"])
def api_lab_highlights():
    import re as _re
    data     = request.json or {}
    segments = data.get("segments", [])
    focus    = data.get("focus", "best moments")
    if not segments:
        return jsonify({"error": "No segments"}), 400

    lines = []
    for seg in segments:
        s, e, txt = seg.get("start", 0), seg.get("end", 0), seg.get("text", "").strip()
        ms, ss = int(s // 60), int(s % 60)
        me, se_ = int(e // 60), int(e % 60)
        lines.append(f"[{ms:02d}:{ss:02d}-{me:02d}:{se_:02d}] {txt}")
    transcript = "\n".join(lines)

    prompt = (
        f"Transcription with timestamps:\n\n{transcript}\n\n"
        f"Find the {focus}. Return ONLY a JSON array, no other text:\n"
        '[{"start":12.5,"end":45.0,"title":"Short title","reason":"Why compelling"}]\n'
        "Rules: 3-8 highlights, each 10-90 seconds. start/end are floats (seconds)."
    )
    try:
        result = _run_async(call_task("twin", [
            {"role": "system", "content": "You are a video editor AI. Identify compelling moments. Respond with valid JSON only."},
            {"role": "user",   "content": prompt},
        ]))
        m = _re.search(r'\[[\s\S]*\]', result)
        if not m:
            return jsonify({"error": "Could not parse JSON", "raw": result[:400]}), 500
        return jsonify({"highlights": json.loads(m.group())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@_flask.route("/api/lab/cut", methods=["POST"])
def api_lab_cut():
    from part4_video import cut_segment, concatenate_clips_with_fade, concatenate_clips
    data       = request.json or {}
    source     = data.get("source_path", "")
    segments   = data.get("segments", [])
    transition = data.get("transition", "fade")
    if not source or not Path(source).exists():
        return jsonify({"error": "Source file not found"}), 400
    if not segments:
        return jsonify({"error": "No segments"}), 400

    clip_paths, errors = [], []
    for i, seg in enumerate(segments):
        start = float(seg.get("start", 0))
        end   = float(seg.get("end", 0))
        out   = str(_lab_renders_dir / f"_clip_{int(time.time()*1000)}_{i}.mp4")
        ok, result = cut_segment(source, start, end, out)
        if ok:
            clip_paths.append(out)
        else:
            errors.append(f"Segment {i}: {result}")

    if not clip_paths:
        return jsonify({"error": "No clips cut", "details": errors}), 500

    final = str(_lab_renders_dir / f"highlight_{int(time.time())}.mp4")
    if len(clip_paths) == 1:
        import shutil; shutil.copy2(clip_paths[0], final); ok = True
    elif transition == "fade":
        ok, result = concatenate_clips_with_fade(clip_paths, final)
    else:
        ok, result = concatenate_clips(clip_paths, final)

    for p in clip_paths:
        try: Path(p).unlink(missing_ok=True)
        except: pass

    if not ok:
        return jsonify({"error": f"Assembly failed: {result}"}), 500

    fname = Path(final).name
    def _bg():
        url, sp = _sb.upload_file(final, folder="renders")
        if url: _sb.log_render(fname, Path(final).stat().st_size, url, sp)
    Thread(target=_bg, daemon=True).start()

    return jsonify({"ok": True, "url": f"/renders/{fname}", "path": final,
                    "clips": len(clip_paths), "errors": errors})


@_flask.route("/api/lab/metadata", methods=["POST"])
def api_lab_metadata():
    import re as _re
    data       = request.json or {}
    transcript = data.get("transcript", "").strip()
    platform   = data.get("platform", "youtube").lower()
    hint       = data.get("title_hint", "").strip()
    if not transcript:
        return jsonify({"error": "No transcript"}), 400

    specs = {
        "youtube":   "YouTube (title -100 chars, description -5000 chars, 10-15 SEO hashtags, engaging hook in first line)",
        "tiktok":    "TikTok (title -150 chars, caption -2200 chars, 5-8 trending viral hashtags, casual punchy tone)",
        "instagram": "Instagram (caption -2200 chars, 20-30 hashtags, aesthetic engaging tone)",
        "facebook":  "Facebook (title -80 chars, description -500 chars, 5-10 hashtags, conversational friendly tone)",
    }
    spec   = specs.get(platform, specs["youtube"])
    hint_l = f"\nVideo hint: {hint}" if hint else ""
    prompt = (
        f"Video transcript:\n\n{transcript[:3000]}{hint_l}\n\n"
        f"Generate optimized metadata for {spec}.\n"
        "Return ONLY valid JSON:\n"
        '{"title":"...","description":"...","hashtags":["#tag1","#tag2"]}'
    )
    try:
        result = _run_async(call_task("twin", [
            {"role": "system", "content": f"You are a social media content strategist. Generate compelling {platform} metadata. Respond with valid JSON only."},
            {"role": "user",   "content": prompt},
        ]))
        m = _re.search(r'\{[\s\S]*\}', result)
        if not m:
            return jsonify({"error": "Could not parse JSON", "raw": result[:400]}), 500
        meta = json.loads(m.group())
        meta["platform"] = platform
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@_flask.route("/api/lab/reformat", methods=["POST"])
def api_lab_reformat():
    from part4_video import reformat_for_platform
    data     = request.json or {}
    source   = data.get("source_path", "")
    platform = data.get("platform", "youtube")
    if not source or not Path(source).exists():
        return jsonify({"error": "Source file not found"}), 400

    out   = str(_lab_renders_dir / f"reformat_{platform}_{int(time.time())}.mp4")
    ok, result = reformat_for_platform(source, platform, out)
    if not ok:
        return jsonify({"error": result}), 500

    fname = Path(out).name
    def _bg():
        url, sp = _sb.upload_file(out, folder="renders")
        if url: _sb.log_render(fname, Path(out).stat().st_size, url, sp)
    Thread(target=_bg, daemon=True).start()
    return jsonify({"ok": True, "url": f"/renders/{fname}", "path": out})


_fonts_dir = Path(__file__).parent / "static" / "fonts"

_FONT_LABELS = {
    "BebasNeue":       "BEBAS NEUE",
    "Oswald-Bold":     "OSWALD BOLD",
    "Anton":           "ANTON",
    "Bangers":         "BANGERS",
    "PermanentMarker": "PERM. MARKER",
    "PressStart2P":    "PRESS START 2P",
}

@_flask.route("/api/lab/fonts", methods=["GET"])
def api_lab_fonts():
    fonts = []
    for f in sorted(_fonts_dir.glob("*.ttf")):
        key   = f.stem
        label = _FONT_LABELS.get(key, key)
        fonts.append({"key": key, "label": label,
                       "path": str(f), "url": f"/static/fonts/{f.name}"})
    return jsonify(fonts)


@_flask.route("/api/lab/thumbnail", methods=["POST"])
def api_lab_thumbnail():
    from part4_video import make_thumbnail
    data       = request.json or {}
    source     = data.get("source_path", "")
    timestamp  = float(data.get("timestamp", 0))
    text       = data.get("text", "")
    position   = data.get("position", "bottom")
    font_key   = data.get("font", "")
    font_size  = int(data.get("font_size", 52))
    font_color = data.get("font_color", "white")
    outline    = bool(data.get("outline", True))

    if not source or not Path(source).exists():
        return jsonify({"error": "Source file not found"}), 400

    font_path = None
    if font_key:
        candidate = _fonts_dir / f"{font_key}.ttf"
        if candidate.exists():
            font_path = str(candidate)

    out = str(_lab_renders_dir / f"thumb_{int(time.time())}.jpg")
    ok, result = make_thumbnail(source, out, timestamp, text, position,
                                font_path=font_path, font_size=font_size,
                                font_color=font_color, outline=outline)
    if not ok:
        return jsonify({"error": result}), 500
    return jsonify({"ok": True, "url": f"/renders/{Path(out).name}", "path": out})


# -- /api/tts ------------------------------------------------------------------
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
            print(f"[TTS] Google Cloud TTS failed: {e} - falling back to gTTS")

    # Groq PlayAI TTS - multi-voice via Groq audio endpoint
    groq_key = os.environ.get("GROQ_API_KEY_1", "") or os.environ.get("GROQ_API_KEY_3", "")
    if groq_key:
        try:
            import httpx as _httpx
            from pydub import AudioSegment
            import io

            PLAYAI_VOICES = {
                "TWIN":         "Ava-PlayAI",
                "SHADOW":       "Fritz-PlayAI",
                "CAPI":         "Atlas-PlayAI",
                "BUILDER":      "Chip-PlayAI",
                "GEMMA":        "Celeste-PlayAI",
                "HERMES":       "Briggs-PlayAI",
                "NARRATOR":     "Ava-PlayAI",
            }
            segments = []
            for turn in turns:
                speaker = turn.get("speaker", "NARRATOR").upper()
                text    = turn.get("text", "").strip()
                if not text:
                    continue
                voice = voice_overrides.get(speaker) or PLAYAI_VOICES.get(speaker, "Ava-PlayAI")
                resp = _httpx.post(
                    "https://api.groq.com/openai/v1/audio/speech",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={"model": "playai-tts", "input": text[:4096], "voice": voice},
                    timeout=30,
                )
                if resp.status_code == 200:
                    seg = AudioSegment.from_mp3(io.BytesIO(resp.content))
                    segments.append(seg)
                else:
                    print(f"[TTS] PlayAI {speaker} failed: {resp.status_code} {resp.text[:200]}")

            if segments:
                combined = segments[0]
                for seg in segments[1:]:
                    combined += seg
                combined.export(str(out_path), format="mp3")
                return jsonify({"url": f"/audio/{fname}", "provider": "groq_playai"})
        except Exception as e:
            print(f"[TTS] Groq PlayAI failed: {e} - falling back to gTTS")

    # gTTS fallback - single narrator voice, concatenate all text
    try:
        from gtts import gTTS
        full_text = " ".join(
            f"{t.get('speaker','')}: {t.get('text','')}" for t in turns if t.get("text")
        )
        tts = gTTS(text=full_text, lang="en", slow=False)
        tts.save(str(out_path))
        return jsonify({"url": f"/audio/{fname}", "provider": "gtts"})
    except Exception as e:
        return jsonify({"error": f"TTS failed: {e}"}), 500

# -- /api/builder --------------------------------------------------------------
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

        # Persist last builder output for /deploy command
        if code and len(code) > 20:
            _last_builder_code['web'] = code

        # Pass 1 review: MiniMax M2.5
        if code and len(code) > 20:
            review_msgs = [
                {"role": "system", "content": "You are a senior code reviewer. In 2-3 sentences flag any critical bugs, security holes, or incomplete logic only. Skip style nits."},
                {"role": "user",   "content": code[:3000]},
            ]
            yield f"\x1eSPEAKER:MINIMAX\x1f"
            try:
                async for chunk in stream_task("builder_review", review_msgs):
                    yield chunk
            except Exception as e:
                yield f"[MINIMAX review skipped: {e}]"
            yield "\x1eEND\x1f"

        # Pass 2 review: Qwen 3 235B
        if code and len(code) > 20:
            review_msgs2 = [
                {"role": "system", "content": "Review this code in 2 sentences. Flag any critical bugs only."},
                {"role": "user",   "content": code[:3000]},
            ]
            yield f"\x1eSPEAKER:QWEN\x1f"
            try:
                async for chunk in stream_task("builder_check", review_msgs2):
                    yield chunk
            except Exception as e:
                yield f"[QWEN review skipped: {e}]"
            yield "\x1eEND\x1f"

    return _sse_stream(_run)


# -- Builder deploy - shared logic (called from Flask route AND Telegram command) -
_SAFE_SCRIPT_NAME = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
_MAX_DEPLOY_BYTES = 65_536   # 64 KB
_last_builder_code: dict = {}   # keyed by chat_id (int) or 'web' (str)


def _do_deploy_script(name: str, code: str) -> dict:
    """
    Write code to scripts/<name>.py and register a pipeline job.
    Returns {"ok": True, "path": ...} or {"error": ...}.
    Called directly (no HTTP hop) from both the Flask route and Telegram /deploy.
    """
    if not _SAFE_SCRIPT_NAME.match(name):
        return {"error": "name must be 1-64 chars: letters, digits, _ or -"}
    if len(code.encode()) > _MAX_DEPLOY_BYTES:
        return {"error": f"code exceeds {_MAX_DEPLOY_BYTES // 1024} KB limit"}
    scripts_dir = Path(__file__).parent / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    try:
        (scripts_dir / f"{name}.py").write_text(code)
    except Exception as e:
        return {"error": f"Write failed: {e}"}
    job = {
        "id":         int(time.time() * 1000),
        "name":       name,
        "status":     "deployed",
        "path":       f"scripts/{name}.py",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    jobs = _pipeline_load()
    jobs.append(job)
    _pipeline_save(jobs)
    return {"ok": True, "path": f"scripts/{name}.py", "job": job}


@_flask.route("/api/deploy/web", methods=["POST"])
def api_deploy_web():
    """
    Web-accessible deploy - reads the last builder output stored server-side.
    Body: { "name": "my_script" }
    Returns: { "ok": True, "path": "scripts/my_script.py", "job": {...} }
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    code = _last_builder_code.get("web", "")
    if not code:
        return jsonify({"error": "No builder output - run the Builder first"}), 400
    result = _do_deploy_script(name, code)
    return jsonify(result), (200 if result.get("ok") else 400)


@_flask.route("/api/builder/deploy", methods=["POST"])
def api_builder_deploy():
    """
    Loopback-only endpoint - callable from server-side code only.
    Browser requests via the Replit proxy arrive with a non-loopback remote_addr and are rejected.
    Body: { "name": "my_script", "code": "..." }
    """
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "Forbidden: server-side only"}), 403
    data = request.json or {}
    name = data.get("name", "").strip()
    code = data.get("code", "").strip()
    if not name or not code:
        return jsonify({"error": "name and code required"}), 400
    result = _do_deploy_script(name, code)
    return jsonify(result), (200 if result.get("ok") else 400)

# -- /api/pipeline -------------------------------------------------------------
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
        "id":          int(time.time() * 1000),
        "name":        data["name"],
        "description": data.get("description", ""),
        "status":      "queued",
        "created_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    jobs.append(job)
    _pipeline_save(jobs)
    return jsonify(job)

@_flask.route("/api/pipeline/<int:job_id>", methods=["DELETE"])
def api_pipeline_delete(job_id):
    jobs = [j for j in _pipeline_load() if j.get("id") != job_id]
    _pipeline_save(jobs)
    return jsonify({"ok": True})


# -------------------------------------------------------------------------------
# VIDEO LAB - /lab + /api/lab/*
# -------------------------------------------------------------------------------

_lab_uploads_dir = Path(__file__).parent / "uploads"
_lab_renders_dir = Path(__file__).parent / "renders"
_lab_music_dir   = Path(__file__).parent / "static" / "music"
_lab_music_meta  = Path(__file__).parent / "static" / "music" / "_meta.json"
_lab_concept_store: dict = {}   # latest concept pushed from bots; cleared on GET
_lab_jobs: dict = {}            # job_id - {status, url, error, provider, local_path}

for _d in (_lab_uploads_dir, _lab_renders_dir, _lab_music_dir):
    _d.mkdir(exist_ok=True)


def _music_meta_load() -> list:
    try:
        return json.loads(_lab_music_meta.read_text()) if _lab_music_meta.exists() else []
    except Exception:
        return []

def _music_meta_save(tracks: list):
    try:
        _lab_music_meta.write_text(json.dumps(tracks, indent=2))
    except Exception:
        pass


# -- /lab ----------------------------------------------------------------------
@_flask.route("/lab")
def lab_page():
    return send_from_directory(str(_static_dir), "lab.html")

@_flask.route("/renders/<path:filename>")
def _renders(filename):
    return send_from_directory(str(_lab_renders_dir), filename, mimetype="video/mp4")


# -- /api/lab/upload -----------------------------------------------------------
_ALLOWED_LAB_EXT = {".mp4", ".mov", ".webm", ".mkv",
                    ".mp3", ".wav", ".ogg", ".m4a",
                    ".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024   # 200 MB

@_flask.route("/api/lab/upload", methods=["POST"])
def api_lab_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file in request"}), 400
    f    = request.files["file"]
    name = f.filename or "upload"
    ext  = Path(name).suffix.lower()
    if ext not in _ALLOWED_LAB_EXT:
        return jsonify({"error": f"File type {ext} not allowed"}), 400

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    dest = _lab_uploads_dir / f"{int(time.time() * 1000)}_{safe_name}"
    f.save(str(dest))

    if dest.stat().st_size > _MAX_UPLOAD_BYTES:
        dest.unlink(missing_ok=True)
        return jsonify({"error": "File exceeds 200 MB limit"}), 400

    video_exts = {".mp4", ".mov", ".webm", ".mkv"}
    audio_exts = {".mp3", ".wav", ".ogg", ".m4a"}
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    if ext in video_exts:
        file_type = "video"
        duration  = get_video_duration(str(dest))
    elif ext in audio_exts:
        file_type = "audio"
        duration  = 0.0
    else:
        file_type = "image"
        duration  = 0.0

    size_bytes = dest.stat().st_size

    def _bg_upload(lpath, fname, ftype, dur, sz):
        url, spath = _sb.upload_file(lpath, folder="uploads")
        _sb.log_upload(fname, ftype, sz, dur, url, spath)

    Thread(target=_bg_upload, args=(str(dest), safe_name, file_type, duration, size_bytes), daemon=True).start()

    return jsonify({
        "ok":        True,
        "name":      safe_name,
        "path":      str(dest),
        "file_type": file_type,
        "duration":  duration,
    })


# -- /api/lab/files - disk + Supabase cloud, merged ----------------------------
@_flask.route("/api/lab/files", methods=["GET"])
def api_lab_files():
    """
    Return all known upload files:
    1. Files on local disk (newest first) - immediately usable.
    2. Supabase cloud records whose file has been pruned from disk -
       shown as cloud=True; downloaded lazily on first use via /api/lab/ensure_local.
    """
    video_exts = {".mp4", ".mov", ".webm", ".mkv"}
    audio_exts = {".mp3", ".wav", ".ogg", ".m4a"}
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    # -- 1. Disk files ----------------------------------------------------------
    disk_files   = []
    disk_names   = set()
    try:
        for p in sorted(_lab_uploads_dir.iterdir(),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file() or p.name.startswith("."):
                continue
            ext = p.suffix.lower()
            if ext in video_exts:
                ftype, duration = "video", get_video_duration(str(p))
            elif ext in audio_exts:
                ftype, duration = "audio", 0.0
            elif ext in image_exts:
                ftype, duration = "image", 0.0
            else:
                continue
            disk_names.add(p.name)
            disk_files.append({
                "name":      p.name,
                "path":      str(p),
                "url":       None,
                "file_type": ftype,
                "duration":  duration,
                "size":      p.stat().st_size,
                "cloud":     False,
            })
    except Exception as e:
        print(f"[LAB FILES] disk scan error: {e}")

    # -- 2. Supabase cloud-only records -----------------------------------------
    cloud_files = []
    try:
        records = _sb.list_uploads(limit=200)
        for rec in records:
            name = rec.get("name", "")
            url  = rec.get("public_url") or ""
            if not name or not url:
                continue
            if name in disk_names:
                continue          # already listed from disk
            ftype = rec.get("file_type", "video")
            if ftype not in ("video", "audio", "image"):
                continue
            cloud_files.append({
                "name":      name,
                "path":      None,    # not on disk - needs ensure_local first
                "url":       url,
                "file_type": ftype,
                "duration":  float(rec.get("duration") or 0),
                "size":      int(rec.get("size_bytes") or 0),
                "cloud":     True,
            })
    except Exception as e:
        print(f"[LAB FILES] supabase fetch error: {e}")

    files = disk_files + cloud_files
    return jsonify({"files": files, "count": len(files)})


# -- /api/lab/ensure_local - lazy download from Supabase when user picks file --
@_flask.route("/api/lab/ensure_local", methods=["POST"])
def api_lab_ensure_local():
    """
    Download a cloud file to local disk on demand (only when user actually
    clicks to use it). Returns the local path for subsequent processing.
    """
    import urllib.request as _ulr
    data = request.json or {}
    url  = data.get("url", "").strip()
    name = data.get("name", "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", name) if name else f"cloud_{int(time.time())}"
    dest = _lab_uploads_dir / safe_name
    if dest.exists():
        return jsonify({"ok": True, "path": str(dest), "cached": True})

    try:
        req = _ulr.Request(url, headers={"User-Agent": "TwinShadow/1.0"})
        with _ulr.urlopen(req, timeout=120) as resp, open(str(dest), "wb") as f:
            while chunk := resp.read(1024 * 64):
                f.write(chunk)
        return jsonify({"ok": True, "path": str(dest), "cached": False})
    except Exception as e:
        dest.unlink(missing_ok=True)
        return jsonify({"error": str(e)}), 500


# -- /api/lab/music ------------------------------------------------------------
@_flask.route("/api/lab/music", methods=["GET"])
def api_lab_music_list():
    tracks = _music_meta_load()
    # Also scan for any untracked files in static/music/
    known  = {t["name"] for t in tracks}
    for p in _lab_music_dir.glob("*"):
        if p.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a"} and p.name not in known and not p.name.startswith("_"):
            tracks.append({"name": p.name, "path": f"/static/music/{p.name}", "tags": ""})
    return jsonify(tracks)


@_flask.route("/api/lab/music/add", methods=["POST"])
def api_lab_music_add():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f    = request.files["file"]
    tags = request.form.get("tags", "").strip()
    ext  = Path(f.filename or "").suffix.lower()
    if ext not in {".mp3", ".wav", ".ogg", ".m4a"}:
        return jsonify({"error": "Audio files only (.mp3/.wav/.ogg/.m4a)"}), 400
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", f.filename or "track")
    dest = _lab_music_dir / safe_name
    f.save(str(dest))
    tracks = _music_meta_load()
    tracks.append({"name": safe_name, "path": f"/static/music/{safe_name}", "tags": tags})
    _music_meta_save(tracks)
    return jsonify({"ok": True, "name": safe_name, "path": f"/static/music/{safe_name}"})


# -- /api/lab/storyboard -------------------------------------------------------
_STORYBOARD_SYSTEM = (
    "You are a short-form video director specializing in esoteric, occult, and dark comedy content. "
    "Given a video concept, output ONLY a JSON object (no markdown, no commentary) in this exact format:\n"
    '{"scenes":[{"id":1,"description":"...","duration":5,"voiceover":"...","style":"..."}]}\n'
    "Generate 4-6 scenes. duration is seconds (3-8 each). style: e.g. 'dark cinematic', 'lo-fi grainy', 'neon occult'. "
    "Voiceover is the spoken line for that scene (1-2 sentences max). Be vivid and specific."
)

@_flask.route("/api/lab/storyboard", methods=["POST"])
def api_lab_storyboard():
    data    = request.json or {}
    concept = data.get("concept", "").strip()
    if not concept:
        return jsonify({"error": "No concept provided"}), 400

    msgs = [
        {"role": "system", "content": _STORYBOARD_SYSTEM},
        {"role": "user",   "content": f"Video concept: {concept}"},
    ]
    try:
        result = _run_async(call_task("twin", msgs))
        text   = result.get("output", "") if isinstance(result, dict) else str(result)
        # Extract JSON from response
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            return jsonify({"error": "Model did not return valid JSON storyboard", "raw": text[:500]}), 500
        raw_json = text[start:end]
        board    = json.loads(raw_json)
        if "scenes" not in board:
            return jsonify({"error": "Missing 'scenes' key in storyboard", "raw": raw_json[:500]}), 500
        return jsonify(board)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON parse error: {e}", "raw": text[:500] if 'text' in dir() else ""}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -- /api/lab/image ------------------------------------------------------------
@_flask.route("/api/lab/image", methods=["POST"])
def api_lab_image():
    data            = request.json or {}
    description     = data.get("description", "").strip()
    style           = data.get("style", "dark cinematic occult aesthetic")
    scene_id        = data.get("scene_id")
    reference_clip  = data.get("reference_clip")
    if not description:
        return jsonify({"error": "No description"}), 400

    ref_frame = None
    if reference_clip and Path(reference_clip).exists():
        frame_out = str(_lab_uploads_dir / f"refframe_{int(time.time()*1000)}.png")
        ref_frame = extract_last_frame(reference_clip, frame_out)

    try:
        result = _run_async(fal_generate_image(description, style, ref_frame))
        if result.get("error"):
            return jsonify(result), 500

        url   = result["url"]
        lpath = None
        if url and url.startswith("http"):
            fname = f"scene_{scene_id or int(time.time())}_{int(time.time()*100)}.jpg"
            lpath = download_file(url, str(_lab_uploads_dir), fname)

        return jsonify({"url": url, "local_path": lpath, "scene_id": scene_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -- /api/lab/video/generate ---------------------------------------------------
def _run_video_job_sync(job_id: str, prompt: str, image_url: str | None):
    """
    Synchronous provider chain (runs in a daemon thread - no asyncio needed).
    FAL WAN-2.1 T2V - FAL MiniMax - Replicate MiniMax - HuggingFace Wan2.1.
    """
    errors: dict = {}

    # -- Provider 1: FAL WAN-2.1 text-to-video --------------------------------
    _lab_jobs[job_id] = {"status": "processing", "provider": "fal / wan-2.1"}
    try:
        import fal_client
        args: dict = {"prompt": prompt}
        if image_url:
            args["image_url"] = image_url
        result = fal_client.run("fal-ai/wan/v2.1/text-to-video", arguments=args)
        vid    = result.get("video", {})
        url    = (vid.get("url") if isinstance(vid, dict) else str(vid)) or ""
        if not url:
            raise RuntimeError("FAL returned no video URL")
        lpath = download_file(url, str(_lab_renders_dir), f"aivideo_{job_id[:8]}.mp4")
        _lab_jobs[job_id] = {"status": "done", "url": url, "local_path": lpath, "provider": "fal/wan-2.1"}
        return
    except Exception as e:
        errors["fal_wan"] = str(e)
        print(f"[VIDEO LAB] FAL WAN failed: {e}")

    # -- Provider 2: FAL MiniMax Video-01 -------------------------------------
    _lab_jobs[job_id] = {"status": "processing", "provider": "fal / minimax"}
    try:
        import fal_client
        args2: dict = {"prompt": prompt}
        if image_url:
            args2["first_frame_image"] = image_url
        result2 = fal_client.run("fal-ai/minimax/video-01-live", arguments=args2)
        vid2    = result2.get("video", {})
        url2    = (vid2.get("url") if isinstance(vid2, dict) else str(vid2)) or ""
        if not url2:
            raise RuntimeError("FAL MiniMax returned no video URL")
        lpath2 = download_file(url2, str(_lab_renders_dir), f"aivideo_{job_id[:8]}.mp4")
        _lab_jobs[job_id] = {"status": "done", "url": url2, "local_path": lpath2, "provider": "fal/minimax"}
        return
    except Exception as e:
        errors["fal_minimax"] = str(e)
        print(f"[VIDEO LAB] FAL MiniMax failed: {e}")

    # -- Provider 3: Replicate MiniMax -----------------------------------------
    _lab_jobs[job_id] = {"status": "processing", "provider": "replicate"}
    try:
        import replicate
        inp: dict = {"prompt": prompt}
        if image_url:
            inp["first_frame_image"] = image_url
        output = replicate.run("minimax/video-01", input=inp)
        url3   = output[0] if isinstance(output, list) else str(output)
        lpath3 = download_file(url3, str(_lab_renders_dir), f"aivideo_{job_id[:8]}.mp4")
        _lab_jobs[job_id] = {"status": "done", "url": url3, "local_path": lpath3, "provider": "replicate"}
        return
    except Exception as e:
        errors["replicate"] = str(e)
        print(f"[VIDEO LAB] Replicate failed: {e}")

    # -- Provider 4: HuggingFace Wan2.1-T2V -----------------------------------
    _lab_jobs[job_id] = {"status": "processing", "provider": "huggingface"}
    try:
        from huggingface_hub import InferenceClient
        client  = InferenceClient(token=os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY", ""))
        result4 = client.text_to_video(prompt, model="Wan-AI/Wan2.1-T2V-14B")
        url4    = result4.url if hasattr(result4, "url") else str(result4)
        lpath4  = download_file(url4, str(_lab_renders_dir), f"aivideo_{job_id[:8]}.mp4")
        _lab_jobs[job_id] = {"status": "done", "url": url4, "local_path": lpath4, "provider": "huggingface"}
        return
    except Exception as e:
        errors["huggingface"] = str(e)
        print(f"[VIDEO LAB] HuggingFace failed: {e}")

    # -- All providers exhausted -----------------------------------------------
    err_detail = " | ".join(f"{k}: {str(v)[:120]}" for k, v in errors.items())
    _lab_jobs[job_id] = {
        "status": "error",
        "error": f"All providers failed - {err_detail}",
        "errors": errors,
    }
    print(f"[VIDEO LAB] All providers failed for job {job_id}: {errors}")


def _video_job_thread(job_id: str, prompt: str, image_url: str | None):
    _run_video_job_sync(job_id, prompt, image_url)


@_flask.route("/api/lab/video/generate", methods=["POST"])
def api_lab_video_generate():
    data      = request.json or {}
    prompt    = data.get("prompt", "").strip()
    image_url = data.get("image_url")
    if not prompt:
        return jsonify({"error": "No prompt"}), 400

    job_id = str(uuid.uuid4())
    _lab_jobs[job_id] = {"status": "processing", "provider": "fal / wan-2.1"}
    Thread(target=_video_job_thread, args=(job_id, prompt, image_url), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "processing", "provider": "fal / wan-2.1"})


@_flask.route("/api/lab/video/status/<job_id>", methods=["GET"])
def api_lab_video_status(job_id):
    job = _lab_jobs.get(job_id)
    if not job:
        return jsonify({"status": "unknown", "error": "Job not found"}), 404
    return jsonify(job)


# -- /api/lab/pexels -----------------------------------------------------------
@_flask.route("/api/lab/pexels", methods=["GET"])
def api_lab_pexels():
    from part4_video import search_pexels_videos
    import asyncio
    query       = request.args.get("q", "").strip()
    orientation = request.args.get("orientation", "landscape")
    if not query:
        return jsonify({"error": "query required"}), 400
    if not os.environ.get("PEXELS_API_KEY"):
        return jsonify({"error": "PEXELS_API_KEY not configured"}), 503
    try:
        loop    = asyncio.new_event_loop()
        results = loop.run_until_complete(search_pexels_videos(query, per_page=12, orientation=orientation))
        loop.close()
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -- /api/lab/pexels/import ----------------------------------------------------
@_flask.route("/api/lab/pexels/import", methods=["POST"])
def api_lab_pexels_import():
    from part4_video import download_file, get_video_duration
    data         = request.json or {}
    url          = data.get("url", "").strip()
    photographer = data.get("photographer", "pexels")
    duration     = data.get("duration", 0)
    if not url:
        return jsonify({"error": "url required"}), 400
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", photographer.lower()[:20])
    filename  = f"pexels_{safe_name}_{int(time.time())}.mp4"
    local     = download_file(url, str(_lab_uploads_dir), filename)
    if not local:
        return jsonify({"error": "Download failed"}), 500
    real_dur = get_video_duration(local) or duration
    url_path = f"/uploads/{Path(local).name}"
    cloud_url, _ = _sb.upload_file(local, folder="uploads")
    return jsonify({
        "name":      Path(local).name,
        "path":      local,
        "url":       cloud_url or url_path,
        "file_type": "video",
        "duration":  real_dur,
        "size":      Path(local).stat().st_size,
        "cloud":     bool(cloud_url),
    })


# -- /api/lab/voice ------------------------------------------------------------
@_flask.route("/api/lab/voice", methods=["POST"])
def api_lab_voice():
    data    = request.json or {}
    text    = data.get("text", "").strip()
    speaker = data.get("speaker", "NARRATOR").upper()
    if not text:
        return jsonify({"error": "No text"}), 400

    _audio_dir.mkdir(exist_ok=True)
    fname    = f"lab_vo_{int(time.time())}.mp3"
    out_path = _audio_dir / fname

    PLAYAI_VOICES = {
        "TWIN": "Ava-PlayAI", "SHADOW": "Fritz-PlayAI", "CAPI": "Atlas-PlayAI",
        "GEMMA": "Celeste-PlayAI", "HERMES": "Briggs-PlayAI", "NARRATOR": "Ava-PlayAI",
    }

    groq_key = os.environ.get("GROQ_API_KEY_1", "") or os.environ.get("GROQ_API_KEY_3", "")
    if groq_key:
        try:
            import httpx as _httpx
            voice = PLAYAI_VOICES.get(speaker, "Ava-PlayAI")
            resp  = _httpx.post(
                "https://api.groq.com/openai/v1/audio/speech",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={"model": "playai-tts", "input": text[:4096], "voice": voice},
                timeout=30,
            )
            if resp.status_code == 200:
                out_path.write_bytes(resp.content)
                return jsonify({"url": f"/audio/{fname}", "path": str(out_path), "provider": "groq_playai"})
            print(f"[LAB VOICE] PlayAI {resp.status_code} - falling back to gTTS")
        except Exception as e:
            print(f"[LAB VOICE] PlayAI failed: {e} - falling back to gTTS")

    try:
        from gtts import gTTS
        tts = gTTS(text=text[:2000], lang="en", slow=False)
        tts.save(str(out_path))
        return jsonify({"url": f"/audio/{fname}", "path": str(out_path), "provider": "gtts"})
    except Exception as e:
        return jsonify({"error": f"TTS failed: {e}"}), 500


# -- /api/lab/render (SSE) -----------------------------------------------------
@_flask.route("/api/lab/render", methods=["POST"])
def api_lab_render():
    """
    Assemble final video using FFMPEG.
    Body: {clips, image_paths, voiceover, music, transition, output_name}
    SSE: text progress lines; final line "DONE:/renders/<file>"
    """
    data        = request.json or {}
    clip_paths  = [p for p in (data.get("clips") or []) if p and Path(p).exists()]
    image_paths = [p for p in (data.get("image_paths") or []) if p and Path(p).exists()]
    voiceover   = data.get("voiceover")
    music       = data.get("music")
    output_name = re.sub(r"[^a-zA-Z0-9_-]", "_", data.get("output_name", f"render_{int(time.time())}"))
    transition  = data.get("transition", "fade")

    if not clip_paths and not image_paths:
        return jsonify({"error": "No clips or images to render"}), 400

    if voiceover and not Path(voiceover).exists():
        voiceover = None
    if music and not (music.startswith("/static/") or Path(music).exists()):
        music = None
    if music and music.startswith("/static/"):
        music = str(Path(__file__).parent / music.lstrip("/"))

    async def _run():
        step_num = [0]
        def step(msg):
            step_num[0] += 1
            return msg

        tmp_video = None
        try:
            # Step 1: Build base video from images or clips
            if image_paths:
                yield step("BUILDING SLIDESHOW FROM SCENE IMAGES...")
                tmp1 = str(_lab_renders_dir / f"_slide_{output_name}.mp4")
                ok, err = images_to_slideshow(image_paths, tmp1, duration=3.0)
                if not ok:
                    yield f"- SLIDESHOW FAILED: {err[:120]}"
                    return
                yield "- SLIDESHOW BUILT"
                # Prepend to clips
                clip_paths.insert(0, tmp1)

            if len(clip_paths) > 1:
                concat_out = str(_lab_renders_dir / f"_concat_{output_name}.mp4")
                _SUPPORTED_TRANSITIONS = {"fade", "cut"}
                if transition not in _SUPPORTED_TRANSITIONS:
                    yield (
                        f"- TRANSITION '{transition.upper()}' NOT SUPPORTED "
                        f"(supported: FADE, CUT) - USING HARD CUT"
                    )
                    effective_transition = "cut"
                else:
                    effective_transition = transition
                if effective_transition == "fade":
                    yield step("CONCATENATING CLIPS WITH FADE TRANSITIONS...")
                    ok, err = concatenate_clips_with_fade(clip_paths, concat_out)
                else:
                    yield step("CONCATENATING CLIPS (HARD CUT)...")
                    ok, err = concatenate_clips(clip_paths, concat_out)
                if not ok:
                    yield f"- CONCAT FAILED: {err[:120]}"
                    return
                yield f"- CLIPS CONCATENATED ({effective_transition.upper()})"
                tmp_video = concat_out
            elif clip_paths:
                tmp_video = clip_paths[0]
            else:
                yield "- NO VIDEO SOURCE"
                return

            # Step 2: Mix audio
            final_out = str(_lab_renders_dir / f"{output_name}.mp4")
            if voiceover or music:
                yield step("MIXING AUDIO (VOICEOVER + MUSIC)...")
                ok, err = mix_audio_onto_video(tmp_video, voiceover, music, final_out)
                if ok:
                    yield "- AUDIO MIXED"
                else:
                    yield f"- AUDIO MIX FAILED: {err[:120]} - SAVING WITHOUT AUDIO"
                    shutil.copy2(tmp_video, final_out)
            else:
                shutil.copy2(tmp_video, final_out)

            # Step 3: Upload to Supabase Storage (persistent backup)
            final_path = Path(final_out)
            if final_path.exists() and _sb.is_configured():
                yield "UPLOADING TO SUPABASE..."
                try:
                    sb_url, sb_path = _sb.upload_file(str(final_path), folder="renders")
                    _sb.log_render(output_name + ".mp4", final_path.stat().st_size, sb_url, sb_path)
                    yield f"- SAVED TO SUPABASE"
                except Exception as sb_err:
                    yield f"- SUPABASE UPLOAD FAILED: {str(sb_err)[:80]} - FILE STILL IN LOCAL RENDERS"

            yield f"DONE:/renders/{output_name}.mp4"

        except Exception as e:
            yield f"- RENDER ERROR: {str(e)[:200]}"

    return _sse_stream(_run)


# -- /api/lab/concept ---------------------------------------------------------
@_flask.route("/api/lab/concept", methods=["GET"])
def api_lab_concept_get():
    """Return and clear the queued concept (set by boardroom/builder bots)."""
    concept = _lab_concept_store.copy()
    _lab_concept_store.clear()
    if not concept:
        return jsonify({}), 200
    return jsonify(concept)


@_flask.route("/api/lab/concept", methods=["POST"])
def api_lab_concept_set():
    """Bot bridge: boardroom/builder POSTs a video concept JSON here."""
    data = request.json or {}
    if not data.get("concept"):
        return jsonify({"error": "concept field required"}), 400
    _lab_concept_store.clear()
    _lab_concept_store.update(data)
    return jsonify({"ok": True})


# -- /api/lab/storage - Supabase-backed file library --------------------------

@_flask.route("/api/lab/storage/uploads", methods=["GET"])
def api_lab_storage_uploads():
    """Return all upload records from Supabase DB (newest first)."""
    if not _sb.is_configured():
        return jsonify({"error": "Supabase not configured", "records": []}), 200
    records = _sb.list_uploads(limit=int(request.args.get("limit", 100)))
    return jsonify({"records": records, "count": len(records)})


@_flask.route("/api/lab/storage/renders", methods=["GET"])
def api_lab_storage_renders():
    """Return all render records from Supabase DB (newest first)."""
    if not _sb.is_configured():
        return jsonify({"error": "Supabase not configured", "records": []}), 200
    records = _sb.list_renders(limit=int(request.args.get("limit", 50)))
    return jsonify({"records": records, "count": len(records)})


@_flask.route("/api/status", methods=["GET"])
def api_status():
    """Provider health status - updated every 6h by part8_hardening monitor."""
    return jsonify(_hardening.get_status())


@_flask.route("/api/events")
def api_events():
    """
    SSE stream of all execution events from the event bus.

    Frontend usage:
        const es = new EventSource('/api/events');
        es.onmessage = (e) => {
            const event = JSON.parse(e.data);
            console.log(event.event_type, event.task_id, event.payload);
        };

    Events fired during build/approve:
        BUILD_STARTED, BUILD_COMPLETED, BUILD_FAILED
        APPROVE_STARTED, APPROVE_COMPLETED, APPROVE_FAILED
        MODULE_STARTED, MODULE_COMPLETED, MODULE_FAILED
        AUDIT_STARTED, AUDIT_PASSED, AUDIT_FLAGGED
        BOARDROOM_STARTED, BOARDROOM_ROUND_DONE, BOARDROOM_COMPLETED
        CROSS_VAL_STARTED, CROSS_VAL_COMPLETED
        HEARTBEAT (every 30s - keepalive)
    """
    q = subscribe()

    async def _gen():
        try:
            async for event in iter_events(q, timeout=30.0):
                yield json.dumps(event)
        finally:
            unsubscribe(q)

    return _sse_stream(_gen)


@_flask.route("/api/job/<job_id>/status", methods=["GET"])
def api_job_status(job_id: str):
    """
    Poll Arq background job status.
    Returns: { "status": "queued|processing|done|failed", "result": ..., "error": ... }

    Used by the frontend to track /approve jobs queued via Arq.
    Falls back gracefully if Redis is not available.
    """
    try:
        from arq import create_pool
        from runtime.workers import _redis_settings

        async def _check():
            try:
                redis = await create_pool(_redis_settings())
                job = await redis.job(job_id)
                if not job:
                    return {"status": "unknown", "error": "Job not found"}
                info = await job.info()
                if info is None:
                    return {"status": "unknown", "error": "No job info"}
                score = getattr(info, "score", None)
                result_str = None
                try:
                    result = await job.result(timeout=0)
                    result_str = str(result)[:500] if result else None
                    return {"status": "done", "result": result_str}
                except Exception:
                    # Job still running or queued
                    return {"status": "processing", "job_id": job_id}
            except Exception as e:
                return {"status": "unknown", "error": str(e)}

        result = _run_async(_check())
        return jsonify(result)

    except ImportError:
        return jsonify({"status": "unavailable", "error": "Arq/Redis not installed"})
    except Exception as e:
        return jsonify({"status": "unknown", "error": str(e)})


@_flask.route("/api/lab/storage/delete", methods=["POST"])
def api_lab_storage_delete():
    """Delete a file from Supabase Storage by storage_path."""
    data         = request.json or {}
    storage_path = data.get("storage_path", "").strip()
    if not storage_path:
        return jsonify({"error": "storage_path required"}), 400
    ok = _sb.delete_file(storage_path)
    return jsonify({"ok": ok})


def _auto_prune_local(max_age_hours: float = 24.0):
    """Background thread: delete local upload/render files older than max_age_hours."""
    import time as _time
    while True:
        _time.sleep(3600)   # run every hour
        cutoff = _time.time() - max_age_hours * 3600
        for d in (_lab_uploads_dir, _lab_renders_dir):
            try:
                for f in d.iterdir():
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        try:
                            f.unlink()
                            print(f"[PRUNE] Deleted {f.name}")
                        except Exception as e:
                            print(f"[PRUNE] Could not delete {f.name}: {e}")
            except Exception as e:
                print(f"[PRUNE] Scan error ({d}): {e}")


Thread(target=_sb.setup_bucket, daemon=True).start()
Thread(target=_auto_prune_local, daemon=True).start()
Thread(target=lambda: _flask.run(host="0.0.0.0", port=_port, debug=False, use_reloader=False), daemon=True).start()

_rate_cache:  dict[int, float] = {}
_shadow_mode: dict[int, bool]  = {}   # per-chat freetext voice toggle

def _is_rate_limited(chat_id: int) -> bool:
    now = time.time()
    if now - _rate_cache.get(chat_id, 0) < RATE_LIMIT_S:
        return True
    _rate_cache[chat_id] = now
    return False

async def _check_auth(update: Update) -> bool:
    if not ALLOWED_IDS: return True
    if update.message.chat_id not in ALLOWED_IDS:
        await update.message.reply_text("- Not authorized.")
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
                    await sent.edit_text(full[:4000] + " -"); last_edit = time.time()
                except Exception: pass
        if full: await sent.edit_text(full[:4000])
    except Exception as e:
        await sent.edit_text(f"-- {task_type} failed: {e}"); return ""
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

# -- Handlers ------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Twin Shadow -\n\n"
        "-- Quick Build --\n"
        "Freetext                    - quick build\n"
        "/build <idea>               - structured pipeline\n"
        "/approve <id>               - execute brief\n"
        "/status <id>  /projects\n\n"
        "-- Vault --\n"
        "/last   /vault   /undo\n\n"
        "-- Sessions --\n"
        "/boardroom <rounds> <topic> - debate\n"
        "/brainstorm <topic>         - ideation\n"
        "/shadow <cmd> <args>        - uncensored only\n"
        "/boss_voice <msg>           - lead next round\n"
        "/end   /extend <n>\n\n"
        "-- Models --\n"
        "/model_set <key> <persona>  - set traits\n"
        "/model_traits [key]         - view traits\n"
        "/model_end <key>            - wipe traits\n\n"
        "-- Video Lab --\n"
        "/vlab <concept>             - storyboard + queue for /lab\n\n"
        "-- Voice --\n"
        "/mode              - show active voice\n"
        "/mode twin         - freetext - TWIN (default)\n"
        "/mode shadow       - freetext - SHADOW\n\n"
        "-- Misc --\n"
        "/forget   /modify <inst>   /wake"
    )

async def wake_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    await update.message.reply_text("- TWIN SHADOW AWAKE.")

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
        await update.message.reply_text(f"- Wait {RATE_LIMIT_S}s."); return

    use_shadow = _shadow_mode.get(chat_id, False)
    if use_shadow:
        task_type  = "shadow_chat"
        sys_prompt = build_system_prompt(task_type, SHADOW_SYSTEM)
        thinking   = "Shadow is thinking..."
    else:
        task_type  = "twin"
        sys_prompt = build_system_prompt(task_type, TWIN_SYSTEM)
        thinking   = "Twin is thinking..."

    sent = await update.message.reply_text(thinking)
    messages = (
        [{"role": "system", "content": sys_prompt}]
        + _get_memory(chat_id)
        + [{"role": "user", "content": text}]
    )
    full = await _stream_and_edit(sent, task_type, messages)
    if not full:
        try:
            result = await orch.quick_build(text, chat_id)
            full = result["output"]; await sent.edit_text(full[:4000])
        except Exception as e:
            await sent.edit_text(f"-- Failed: {e}"); return
    _add_memory(chat_id, "user", text)
    _add_memory(chat_id, "assistant", full)


async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /mode          - show current freetext voice
    /mode twin     - route freetext through TWIN (default)
    /mode shadow   - route freetext through SHADOW
    """
    if not await _check_auth(update): return
    chat_id = update.message.chat_id
    arg = (context.args[0].lower() if context.args else "").strip()
    if arg == "shadow":
        _shadow_mode[chat_id] = True
        await update.message.reply_text("- SHADOW mode active - freetext now routes through SHADOW.")
    elif arg == "twin":
        _shadow_mode[chat_id] = False
        await update.message.reply_text("- TWIN mode active - freetext now routes through TWIN.")
    else:
        current = "- SHADOW" if _shadow_mode.get(chat_id) else "- TWIN"
        await update.message.reply_text(
            f"Current freetext voice: {current}\n"
            "/mode twin   - switch to TWIN\n"
            "/mode shadow - switch to SHADOW"
        )

async def build_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args: await update.message.reply_text("Usage: /build <idea>"); return
    chat_id = update.message.chat_id
    idea    = " ".join(context.args)
    if _is_rate_limited(chat_id): await update.message.reply_text(f"- Wait {RATE_LIMIT_S}s."); return
    await update.message.reply_text("CEO drafting brief...")
    try:
        pid, brief = await orch.build(idea, chat_id)
    except Exception as e:
        await update.message.reply_text(f"- Brief failed: {e}"); return
    lines = [f"- Project {pid}", f"Summary: {brief.get('summary','')}", f"Type: {brief.get('project_type','?')} | Effort: {brief.get('estimated_effort','?')}", f"Modules: {', '.join(brief.get('modules_needed',[]))}", "\nGoals:"]
    for g in brief.get("goals", []): lines.append(f"  - {g}")
    lines.append(f"\n- /approve {pid}")
    await update.message.reply_text("\n".join(lines))

async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args: await update.message.reply_text("Usage: /approve <id>"); return
    pid = context.args[0]
    await update.message.reply_text(f"- Queuing {pid}...")
    try:
        job_id = await orch.enqueue_approve(pid)
        if job_id.startswith("direct:"):
            # Redis not available - ran synchronously
            project = orch.get_project(pid)
            results = project.results if project else []
            response = f"- Project {pid} complete\n"
            for r in results:
                response += f"\n-- {r.module}{'-' if r.shadow else ''}{'-' if r.approved else ('-' if r.approved is not None else '')} --\n{r.output[:300]}\n"
            for chunk in [response[i:i+4000] for i in range(0, len(response), 4000)]:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(
                f"- {pid} queued - job `{job_id}`\n"
                f"Results will appear in vault when complete.\n"
                f"Poll status: /api/job/{job_id}/status"
            )
    except Exception as e:
        await update.message.reply_text(f"- Failed: {e}")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args: await update.message.reply_text("Usage: /status <id>"); return
    proj = orch.get_project(context.args[0])
    if not proj: await update.message.reply_text("Not found."); return
    await update.message.reply_text(f"- {proj['id']} - {proj.get('idea','?')[:80]}\nStatus: {proj.get('status','?')}")

async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    projects = orch.list_projects(update.message.chat_id)
    if not projects: await update.message.reply_text("No projects yet."); return
    await update.message.reply_text("- Projects:\n" + "\n".join(f"{p['id']}. {p.get('idea','?')[:50]} - {p.get('status','?')}" for p in projects))

async def last_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    entry = vault_last(update.message.chat_id)
    if not entry: await update.message.reply_text("Nothing built yet."); return
    await update.message.reply_text(f"- [{entry.get('topic','')}]\n\n{str(entry.get('content',''))[:4000]}")

async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    chat_id = update.message.chat_id; args = context.args or []
    if not args or args[0] == "list":
        entries = vault_list(chat_id)
        if not entries: await update.message.reply_text("Vault empty."); return
        await update.message.reply_text("- VAULT:\n" + "\n".join(f"{e.get('id','?')}. {e.get('topic','?')}" for e in entries) + "\n\n/vault <id>  |  /vault delete <id>  |  /vault clear")
        return
    if args[0] == "clear": vault_clear(chat_id); await update.message.reply_text("- Cleared."); return
    if args[0] == "delete":
        if len(args) < 2: await update.message.reply_text("Usage: /vault delete <id>"); return
        try: vault_delete(int(args[1]), chat_id); await update.message.reply_text(f"- {args[1]} deleted.")
        except ValueError: await update.message.reply_text("Invalid ID.")
        return
    try:
        entry = vault_get(int(args[0]), chat_id)
        if not entry: await update.message.reply_text("Not found.")
        else: await update.message.reply_text(f"- [{entry.get('topic','')}]\n\n{str(entry.get('content',''))[:4000]}")
    except ValueError:
        await update.message.reply_text("Usage: /vault list | /vault <id> | /vault delete <id> | /vault clear")

async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    entry = vault_undo(update.message.chat_id)
    await update.message.reply_text(f"-- Removed: [{entry.get('topic','')}]" if entry else "Nothing to undo.")

async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    _clear_memory(update.message.chat_id); await update.message.reply_text("- Memory cleared.")

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
                try: await sent.edit_text(f"```python\n{full[:2500]}-\n```", parse_mode="Markdown"); last_edit = time.time()
                except Exception: pass
        Path("twin_rewrite_proposal.py").write_text(full)
        await sent.edit_text(f"- twin_rewrite_proposal.py\n\n```python\n{full[:3000]}\n```", parse_mode="Markdown")
    except Exception as e: await sent.edit_text(f"-- Failed: {e}")

# -- Part 7 wrappers -----------------------------------------------------------

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

async def deploy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /deploy <name>  - deploy the last /build output as scripts/<name>.py.
    Called from Telegram; invokes _do_deploy_script() directly (no HTTP hop).
    """
    if not await _check_auth(update): return
    if not context.args:
        await update.message.reply_text("Usage: /deploy <script_name>\nDeploys the last builder output.")
        return
    name    = context.args[0].strip()
    chat_id = update.message.chat_id
    code    = _last_builder_code.get(chat_id) or _last_builder_code.get('web', '')
    if not code:
        await update.message.reply_text("No builder output found. Run /build <idea> first."); return
    result = _do_deploy_script(name, code)
    if result.get("ok"):
        await update.message.reply_text(f"- Deployed - {result['path']}\nJob ID: {result['job']['id']}")
    else:
        await update.message.reply_text(f"- Deploy failed: {result.get('error','unknown error')}")

# -- /vlab command -------------------------------------------------------------

async def vlab_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update): return
    if not context.args:
        await update.message.reply_text("Usage: /vlab <concept>\nExample: /vlab a dark occult cooking show"); return
    chat_id = update.message.chat_id
    concept = " ".join(context.args).strip()
    if _is_rate_limited(chat_id):
        await update.message.reply_text(f"- Wait {RATE_LIMIT_S}s."); return

    _lab_concept_store.clear()
    _lab_concept_store.update({"concept": concept})

    sent = await update.message.reply_text("- Generating storyboard...")

    msgs = [
        {"role": "system", "content": _STORYBOARD_SYSTEM},
        {"role": "user",   "content": f"Video concept: {concept}"},
    ]
    try:
        result = await call_task("twin", msgs)
        text   = result.get("output", "") if isinstance(result, dict) else str(result)
        start  = text.find("{")
        end    = text.rfind("}") + 1
        if start == -1 or end == 0:
            await sent.edit_text("-- Model did not return a valid storyboard.\nYour concept was queued - open /lab to continue."); return
        board = json.loads(text[start:end])
        if "scenes" not in board:
            await sent.edit_text("-- Storyboard missing 'scenes' key.\nYour concept was queued - open /lab to continue."); return
    except json.JSONDecodeError as e:
        await sent.edit_text(f"-- JSON parse error: {e}\nYour concept was queued - open /lab to continue."); return
    except Exception as e:
        await sent.edit_text(f"-- Storyboard failed: {e}\nYour concept was queued - open /lab to continue."); return

    lines = [f"- Storyboard - {concept}\n"]
    for s in board["scenes"]:
        lines.append(
            f"Scene {s.get('id','?')} [{s.get('duration','?')}s | {s.get('style','?')}]\n"
            f"- {s.get('description','')}\n"
            f"- {s.get('voiceover','')}\n"
        )
    lines.append("- Open /lab to continue the pipeline.")
    reply = "\n".join(lines)
    await sent.edit_text(reply[:4000])


# -- Main ----------------------------------------------------------------------

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
        ("mode", mode_cmd),
        ("shadow", shadow_cmd), ("boss_voice", boss_voice_cmd),
        ("end", end_cmd), ("extend", extend_cmd), ("deploy", deploy_cmd),
        ("vlab", vlab_cmd),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, freetext_handler))
    _hardening.start_monitor(interval_hours=6)
    print("- Twin Shadow online.")
    import asyncio as _asyncio
    from telegram.error import Conflict as _TGConflict
    async def _run():
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True, error_callback=lambda e: None)
            stop_event = _asyncio.Event()
            try:
                await stop_event.wait()
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                await app.updater.stop()
                await app.stop()
    while True:
        try:
            _asyncio.run(_run())
            break
        except _TGConflict:
            import time as _t
            print("-- Telegram 409 conflict - retrying in 15s...")
            _t.sleep(15)



async def queue_background_task(project_id: str) -> str:
    """
    Enqueue an approve job via Arq. Returns the job ID.
    Falls back to direct execution if Redis is not available.
    Wraps orch.enqueue_approve() for external callers.
    """
    return await orch.enqueue_approve(project_id)
