"""
Twin Shadow — Part 9: All Flask web routes.
Call register_routes(_flask) from part6_bot.py.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import queue as Q
import re
import urllib.parse
import urllib.request
import uuid
from functools import wraps
from pathlib import Path
from threading import Thread

from flask import (
    Flask, request, session, redirect, render_template,
    jsonify, Response, stream_with_context,
    send_from_directory, send_file, abort,
)

# ── paths ──────────────────────────────────────────────────────────────────────
_ROOT            = Path(__file__).parent
_STATIC          = _ROOT / "tsai" / "static"
_AUDIO_DIR       = _ROOT / "audio"
_RENDERS_DIR     = _ROOT / "renders"
_UPLOADS_DIR     = _ROOT / "uploads"
_USERS_FILE      = _ROOT / "users.json"
_INVITES_FILE    = _ROOT / "invites.json"
_SETTINGS_DIR    = _ROOT / "settings"
_TRANSCRIPTS_DIR = _ROOT / "transcripts"
_LAB_CONCEPT     = _ROOT / "lab_concept.json"
_BUILDS_DIR      = _ROOT / "builds"
_BUILDS_DIR.mkdir(parents=True, exist_ok=True)

for _d in [_AUDIO_DIR, _RENDERS_DIR, _UPLOADS_DIR, _SETTINGS_DIR, _TRANSCRIPTS_DIR]:
    _d.mkdir(exist_ok=True)

# ── user store ─────────────────────────────────────────────────────────────────
def _load_users() -> dict:
    if _USERS_FILE.exists():
        return json.loads(_USERS_FILE.read_text())
    return {}

def _save_users(u: dict):
    _USERS_FILE.write_text(json.dumps(u, indent=2))

def _load_invites() -> dict:
    if _INVITES_FILE.exists():
        return json.loads(_INVITES_FILE.read_text())
    return {}

def _save_invites(inv: dict):
    _INVITES_FILE.write_text(json.dumps(inv, indent=2))

def _create_invite() -> str:
    inv   = _load_invites()
    code  = uuid.uuid4().hex[:16]
    inv[code] = {"used": False}
    _save_invites(inv)
    return code

def _use_invite(code: str) -> bool:
    inv = _load_invites()
    if code not in inv or inv[code].get("used"):
        return False
    inv[code]["used"] = True
    _save_invites(inv)
    return True

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

# ── SSE constants ──────────────────────────────────────────────────────────────
_RS = "\x1e"   # speaker-marker start  (0x1E record separator)
_US = "\x1f"   # speaker-marker end    (0x1F unit separator)

def _speaker_evt(name: str) -> str:
    return f"data: {json.dumps({'text': _RS+'SPEAKER:'+name+_US})}\n\n"

def _end_evt() -> str:
    return f"data: {json.dumps({'text': _RS+'END'+_US})}\n\n"

def _chunk_evt(text: str) -> str:
    return f"data: {json.dumps({'text': text})}\n\n"

def _done_evt() -> str:
    return 'data: {"done": true}\n\n'

def _err_evt(msg: str) -> str:
    return f"data: {json.dumps({'error': str(msg)})}\n\n"

def _sse_response(gen_fn) -> Response:
    return Response(
        stream_with_context(gen_fn()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ── async helpers ──────────────────────────────────────────────────────────────
def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            # Drain any pending cleanup tasks (e.g. httpx AsyncClient.aclose)
            # so we don't get "RuntimeError: Event loop is closed" on GC
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()

def _stream_to_queue(async_gen, q: Q.Queue):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def _collect():
        try:
            async for item in async_gen:
                q.put(("ok", item))
        except Exception as e:
            q.put(("err", str(e)))
        q.put(None)
    loop.run_until_complete(_collect())
    loop.close()

def _sse_single(task_key: str, messages: list) -> Response:
    from part2_router import stream_task as _st
    q: Q.Queue = Q.Queue()
    Thread(target=_stream_to_queue, args=(_st(task_key, messages), q), daemon=True).start()
    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            kind, val = item
            yield _chunk_evt(val) if kind == "ok" else _err_evt(val)
        yield _done_evt()
    return _sse_response(gen)

# ── multi-agent SSE runner ─────────────────────────────────────────────────────
def _run_multi_agent(members: list[dict], build_msgs_fn, q: Q.Queue,
                     extra_kwargs: dict | None = None):
    """
    Run a list of board/brainstorm members sequentially, pushing speaker/chunk/end/err to q.
    build_msgs_fn(member, ctx) → list[dict]   (returns messages for this member)
    """
    from part2_router import direct_stream as _ds
    from part1_registry import get_task_routing

    async def _run():
        ctx = extra_kwargs.get("prior_context", "") if extra_kwargs else ""
        for m in members:
            key  = m["key"]
            name = m["name"]
            try:
                provider, model = get_task_routing(key)
                msgs = build_msgs_fn(m, ctx)
                q.put(("speaker", name))
                full = ""
                async for chunk in _ds(provider, model, msgs):
                    q.put(("ok", chunk))
                    full += chunk
                q.put(("end", name))
                ctx += f"\n\n{name}: {full}"
            except Exception as e:
                q.put(("err", f"[{name}] {e}"))
        q.put(None)

    Thread(target=lambda: _run_async(_run()), daemon=True).start()

def _multi_agent_sse(q: Q.Queue) -> Response:
    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            kind, val = item
            if kind == "speaker":
                yield _speaker_evt(val)
            elif kind == "ok":
                yield _chunk_evt(val)
            elif kind == "end":
                yield _end_evt()
            else:
                yield _err_evt(val)
        yield _done_evt()
    return _sse_response(gen)


# ══════════════════════════════════════════════════════════════════════════════
def register_routes(app: Flask):
    app.secret_key = os.environ.get("SECRET_KEY", os.environ.get("WEB_PASSWORD", "ts-default-2026"))

    # ── auth decorator ────────────────────────────────────────────────────────
    def _login_required(f):
        @wraps(f)
        def dec(*a, **kw):
            if "username" not in session:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "not authenticated"}), 401
                return redirect("/login")
            return f(*a, **kw)
        return dec

    # ── static media ──────────────────────────────────────────────────────────
    @app.route("/audio/<path:fname>")
    def serve_audio(fname):
        return send_from_directory(_AUDIO_DIR, fname)

    @app.route("/renders/<path:fname>")
    def serve_renders(fname):
        return send_from_directory(_RENDERS_DIR, fname)

    @app.route("/uploads/<path:fname>")
    def serve_uploads(fname):
        return send_from_directory(_UPLOADS_DIR, fname)

    # ── pages ─────────────────────────────────────────────────────────────────
    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(_STATIC, "send_btn.jpg", mimetype="image/jpeg")

    @app.route("/")
    @_login_required
    def home():
        og_url = request.host_url.rstrip("/")
        return render_template("index.html", og_url=og_url)

    @app.route("/lab")
    @_login_required
    def lab():
        return render_template("lab.html")

    # ── auth ──────────────────────────────────────────────────────────────────
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html", error=None, username_val="")

        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))
        users    = _load_users()

        # First-ever login: auto-create admin account
        if not users:
            users[username] = {"password": _hash(password), "is_admin": True}
            _save_users(users)
            session.permanent = remember
            session["username"] = username
            session["is_admin"] = True
            return redirect("/")

        # WEB_PASSWORD bypass — always grants admin
        web_pw = os.environ.get("WEB_PASSWORD", "")
        if web_pw and password == web_pw:
            if username not in users:
                users[username] = {"password": _hash(password), "is_admin": True}
                _save_users(users)
            else:
                users[username]["is_admin"] = True
                _save_users(users)
            session.permanent = remember
            session["username"] = username
            session["is_admin"] = True
            return redirect("/")

        user = users.get(username)
        if not user or user["password"] != _hash(password):
            return render_template("login.html", error="Invalid credentials.", username_val=username)

        session.permanent = remember
        session["username"] = username
        session["is_admin"] = user.get("is_admin", False)
        return redirect("/")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        users     = _load_users()
        first_run = len(users) == 0
        code      = request.args.get("invite", "") or request.form.get("invite_code", "")

        if request.method == "GET":
            if not first_run and not code:
                return render_template("register.html",
                                       error="Registration requires an invite link.",
                                       invite_code="", success=None)
            if not first_run and not _load_invites().get(code, {}).get("used") is False:
                inv = _load_invites()
                if code not in inv or inv[code].get("used"):
                    return render_template("register.html",
                                           error="Invalid or already-used invite link.",
                                           invite_code="", success=None)
            return render_template("register.html", error=None, invite_code=code, success=None)

        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")

        def _err(msg):
            return render_template("register.html", error=msg, invite_code=code, success=None)

        if not username or not password:
            return _err("Username and password required.")
        if not re.match(r"^[a-z0-9_]{3,32}$", username):
            return _err("3-32 chars: letters, numbers, underscore.")
        if password != confirm:
            return _err("Passwords do not match.")
        if len(password) < 6:
            return _err("Password must be at least 6 characters.")

        users    = _load_users()
        is_admin = len(users) == 0

        if not is_admin:
            if not code or not _use_invite(code):
                return _err("Invalid or already-used invite link.")

        if username in users:
            return _err("Username already taken.")

        users[username] = {"password": _hash(password), "is_admin": is_admin}
        _save_users(users)
        session["username"] = username
        session["is_admin"]  = is_admin
        return redirect("/")

    @app.route("/admin/invite")
    @_login_required
    def admin_invite():
        if not session.get("is_admin"):
            abort(403)
        code = _create_invite()
        base = request.host_url.rstrip("/")
        link = f"{base}/register?invite={code}"
        return render_template("invite.html", link=link)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/login")

    # ── me / settings ─────────────────────────────────────────────────────────
    @app.route("/api/me")
    @_login_required
    def api_me():
        return jsonify({"username": session["username"], "is_admin": session.get("is_admin", False)})

    @app.route("/api/user/settings", methods=["GET", "POST"])
    @_login_required
    def api_settings():
        path = _SETTINGS_DIR / f"{session['username']}.json"
        if request.method == "GET":
            return jsonify(json.loads(path.read_text()) if path.exists() else {})
        path.write_text(json.dumps(request.get_json(force=True) or {}, indent=2))
        return jsonify({"ok": True})

    # ── chat ──────────────────────────────────────────────────────────────────
    @app.route("/api/chat", methods=["POST"])
    @_login_required
    def api_chat():
        from part8_personas import get_system_prompt
        d             = request.get_json(force=True) or {}
        bot           = d.get("bot", "twin")
        message       = d.get("message", "")
        history       = d.get("history", [])[-30:]
        sys_over      = d.get("system_prompt", "")
        continue_mode = d.get("continue_mode", False)
        key           = "shadow_chat" if bot == "shadow" else "twin"
        sys_p         = sys_over or get_system_prompt(key)
        if continue_mode:
            user_msg = ("Continue your response from exactly where you stopped. "
                        "Do not repeat anything already written. "
                        "Pick up mid-sentence if that is where you left off.")
        else:
            user_msg = message
        msgs = [{"role": "system", "content": sys_p}] + history + [{"role": "user", "content": user_msg}]
        return _sse_single(key, msgs)

    # ── single-text TTS (ElevenLabs → gTTS) ──────────────────────────────────
    @app.route("/api/tts/chat", methods=["POST"])
    @_login_required
    def api_tts_chat():
        import os as _os, requests as _req
        d     = request.get_json(force=True) or {}
        text  = (d.get("text") or "")
        voice = d.get("voice", "twin").lower()
        if not text:
            return jsonify({"error": "no text"}), 400
        _EL_VOICES = {
            "twin":   "ErXwobaYiN019PkySvjV",
            "shadow": "VR6AewLTigWG4xSOukaG",
        }
        voice_id = _EL_VOICES.get(voice, _EL_VOICES["twin"])
        fname = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        out   = _AUDIO_DIR / fname
        _el_key_envs = ["ELEVENLABS_API_KEY"] + [f"ELEVENLABS_API_KEY_{i}" for i in range(1, 12)]
        for key_env in _el_key_envs:
            api_key = _os.environ.get(key_env, "")
            if not api_key:
                continue
            try:
                r = _req.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                    json={"text": text, "model_id": "eleven_multilingual_v2",
                          "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
                    timeout=20,
                )
                if r.status_code == 200:
                    out.write_bytes(r.content)
                    return jsonify({"url": f"/audio/{fname}", "engine": "elevenlabs"})
            except Exception:
                continue
        try:
            from gtts import gTTS
            gTTS(text=text, lang="en").save(str(out))
            return jsonify({"url": f"/audio/{fname}", "engine": "gtts"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── boardroom ─────────────────────────────────────────────────────────────
    @app.route("/api/boardroom", methods=["POST"])
    @_login_required
    def api_boardroom():
        from part8_personas import board_member_system
        from part1_registry import BOARD_MEMBERS, SHADOW_BOARD_MEMBERS
        d           = request.get_json(force=True) or {}
        topic       = d.get("topic", "")
        shadow_mode = d.get("shadow_mode", False)
        sys_over    = d.get("system_prompt", "")
        prior       = d.get("prior_context", "")
        steer       = d.get("steer", "")
        members     = SHADOW_BOARD_MEMBERS if shadow_mode else BOARD_MEMBERS
        context_sfx = (f"\n\nPRIOR CONTEXT:\n{prior}" if prior else "") + \
                      (f"\n\nSTEERING DIRECTIVE: {steer}" if steer else "")

        def build_msgs(m, _ctx):
            sys_p = (sys_over + "\n\n" if sys_over else "") + \
                     board_member_system(m["name"], m.get("role", ""), shadow_mode)
            return [
                {"role": "system", "content": sys_p},
                {"role": "user",   "content": f"BOARDROOM TOPIC: {topic}{context_sfx}\n\nSpeak as {m['name']}. Direct, in-character, max 3 paragraphs."},
            ]

        q: Q.Queue = Q.Queue()
        _run_multi_agent(members, build_msgs, q, {"prior_context": prior})
        return _multi_agent_sse(q)

    # ── brainstorm ────────────────────────────────────────────────────────────
    @app.route("/api/brainstorm", methods=["POST"])
    @_login_required
    def api_brainstorm():
        from part8_personas import brainstorm_system
        from part1_registry import BRAINSTORM_MEMBERS, SHADOW_BRAINSTORM_MEMBERS
        from part2_router import direct_stream as _ds
        from part1_registry import get_task_routing
        d           = request.get_json(force=True) or {}
        topic       = d.get("topic", "")
        shadow_mode = d.get("shadow_mode", False)
        rounds      = min(int(d.get("rounds", 1)), 3)
        sys_over    = d.get("system_prompt", "")
        members     = SHADOW_BRAINSTORM_MEMBERS if shadow_mode else BRAINSTORM_MEMBERS
        q: Q.Queue  = Q.Queue()

        async def _run():
            ctx = ""
            for rnd in range(rounds):
                for m in members:
                    key  = m["key"]
                    name = m["name"]
                    try:
                        provider, model = get_task_routing(key)
                        sys_p = (sys_over + "\n\n" if sys_over else "") + \
                                 brainstorm_system(name, shadow_mode=shadow_mode)
                        user_msg = f"BRAINSTORM: {topic}"
                        if ctx:
                            user_msg += f"\n\nPREVIOUS IDEAS:\n{ctx}"
                        user_msg += f"\n\nRound {rnd+1}. Speak as {name}. Add fresh ideas or build on previous ones. 2-3 paragraphs."
                        msgs = [{"role": "system", "content": sys_p},
                                {"role": "user",   "content": user_msg}]
                        q.put(("speaker", name))
                        full = ""
                        async for chunk in _ds(provider, model, msgs):
                            q.put(("ok", chunk))
                            full += chunk
                        q.put(("end", name))
                        ctx += f"\n\n{name}: {full}"
                    except Exception as e:
                        q.put(("err", f"[{name}] {e}"))
            q.put(None)

        Thread(target=lambda: _run_async(_run()), daemon=True).start()
        return _multi_agent_sse(q)

    # ── advisor ───────────────────────────────────────────────────────────────
    @app.route("/api/advisor", methods=["POST"])
    @_login_required
    def api_advisor():
        from part8_personas import get_system_prompt
        d       = request.get_json(force=True) or {}
        mode    = d.get("mode", d.get("advisor", "legal"))
        message = d.get("message", "")
        history = d.get("history", d.get("prior", []))[-20:]
        key     = "legal_finance" if mode == "legal" else "fast_reasoning"
        sys_p   = get_system_prompt(key)
        msgs    = [{"role": "system", "content": sys_p}] + history + [{"role": "user", "content": message}]
        return _sse_single(key, msgs)

    # ── podcast ───────────────────────────────────────────────────────────────
    @app.route("/api/podcast", methods=["POST"])
    @_login_required
    def api_podcast():
        from part8_personas import podcast_character_system
        from part2_router import direct_stream as _ds
        from part1_registry import get_task_routing
        d          = request.get_json(force=True) or {}
        topic      = d.get("topic", "")
        cast       = d.get("speakers", d.get("cast", []))
        rounds     = min(int(d.get("rounds", 1)), 4)
        prior      = d.get("prior_context", "")
        show_type  = d.get("show_type", "podcast")
        complexity = d.get("complexity", "simple")
        steer      = d.get("steer", "")
        q: Q.Queue = Q.Queue()

        async def _run():
            ctx = prior or ""
            for rnd in range(rounds):
                for char in cast:
                    name        = char.get("name", "HOST")
                    role        = char.get("role", "Host")
                    personality = char.get("personality", "")
                    key         = char.get("model", char.get("key", "twin"))
                    try:
                        provider, model = get_task_routing(key)
                        sys_p    = podcast_character_system(
                            name, role,
                            personality=personality,
                            show_type=show_type,
                            complexity=complexity,
                        )
                        user_msg = f"SHOW TOPIC: {topic}"
                        if ctx:
                            user_msg += f"\n\nDIALOGUE SO FAR:\n{ctx}"
                        if steer:
                            user_msg += f"\n\nDIRECTOR NOTE: {steer}"
                        user_msg += f"\n\nRound {rnd+1}. Your turn as {name}. Natural dialogue, stay in character."
                        msgs = [{"role": "system", "content": sys_p},
                                {"role": "user",   "content": user_msg}]
                        q.put(("speaker", name))
                        full = ""
                        async for chunk in _ds(provider, model, msgs):
                            q.put(("ok", chunk))
                            full += chunk
                        q.put(("end", name))
                        ctx += f"\n\n{name}: {full}"
                    except Exception as e:
                        q.put(("err", f"[{name}] {e}"))
            q.put(None)

        Thread(target=lambda: _run_async(_run()), daemon=True).start()
        return _multi_agent_sse(q)

    # ── ElevenLabs voice list (cached per process) ────────────────────────────
    _el_voice_cache: list = []

    def _fetch_el_voices(api_key: str) -> list:
        """Return [{name, voice_id}] from ElevenLabs API."""
        import requests as _req
        try:
            r = _req.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": api_key},
                timeout=10,
            )
            if r.status_code == 200:
                return [
                    {"name": v["name"], "voice_id": v["voice_id"]}
                    for v in r.json().get("voices", [])
                ]
        except Exception:
            pass
        return []

    def _el_voice_id(name_or_id: str, voices: list) -> str | None:
        """Resolve a voice name or voice_id to an EL voice_id. None if not found."""
        low = name_or_id.lower()
        for v in voices:
            if v["voice_id"] == name_or_id or v["name"].lower() == low:
                return v["voice_id"]
        return None

    @app.route("/api/voices")
    @_login_required
    def api_voices():
        import os as _os
        _el_key_envs = ["ELEVENLABS_API_KEY"] + [f"ELEVENLABS_API_KEY_{i}" for i in range(1, 12)]
        for key_env in _el_key_envs:
            api_key = _os.environ.get(key_env, "")
            if not api_key:
                continue
            voices = _fetch_el_voices(api_key)
            if voices:
                _el_voice_cache.clear()
                _el_voice_cache.extend(voices)
                return jsonify({"ok": True, "voices": voices})
        return jsonify({"ok": False, "voices": [], "error": "No ElevenLabs key configured"})

    # ── TTS ───────────────────────────────────────────────────────────────────
    @app.route("/api/tts", methods=["POST"])
    @_login_required
    def api_tts():
        import os as _os, subprocess, tempfile, shutil
        d               = request.get_json(force=True) or {}
        turns           = d.get("turns", [])
        voice_overrides = d.get("voice_overrides", {})   # {SPEAKER_NAME: voice_id}
        if not turns:
            return jsonify({"error": "no turns"}), 400

        fname   = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        out     = _AUDIO_DIR / fname
        _ffmpeg = shutil.which("ffmpeg") or "/nix/store/3zc5jbvqzrn8zmva4fx5p19goxxa8bm-ffmpeg-7.1/bin/ffmpeg"

        # ── resolve ElevenLabs key + voice map ───────────────────────────────
        el_key    = None
        el_voices: list = []
        _el_key_envs = ["ELEVENLABS_API_KEY"] + [f"ELEVENLABS_API_KEY_{i}" for i in range(1, 12)]
        for key_env in _el_key_envs:
            k = _os.environ.get(key_env, "")
            if k:
                el_key = k
                break

        if el_key:
            el_voices = _el_voice_cache if _el_voice_cache else _fetch_el_voices(el_key)
            if el_voices and not _el_voice_cache:
                _el_voice_cache.extend(el_voices)

        # ── ElevenLabs per-speaker clips ──────────────────────────────────────
        if el_key and el_voices:
            import requests as _req
            import tempfile as _tf2, shutil as _sh2
            tmpdir     = Path(_tf2.mkdtemp())
            clip_paths = []
            all_ok     = True
            # Spread board members across the first few EL voices for variety
            _default_voices = [v["voice_id"] for v in el_voices[:6]]
            _speaker_voice_map: dict[str, str] = {}

            def _pick_voice(speaker: str) -> str:
                """Return voice_id for speaker: override → assigned default → next in pool."""
                override = voice_overrides.get(speaker, voice_overrides.get(speaker.title(), ""))
                if override:
                    vid = _el_voice_id(override, el_voices)
                    if vid:
                        return vid
                if speaker not in _speaker_voice_map:
                    idx = len(_speaker_voice_map) % max(len(_default_voices), 1)
                    _speaker_voice_map[speaker] = _default_voices[idx] if _default_voices else el_voices[0]["voice_id"]
                return _speaker_voice_map[speaker]

            for i, turn in enumerate(turns):
                speaker  = (turn.get("speaker") or "SPEAKER").upper()
                text     = (turn.get("text") or "").strip()
                if not text:
                    continue

                clip_path = tmpdir / f"clip_{i:04d}.mp3"
                voice_id  = _pick_voice(speaker)
                try:
                    r = _req.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                        headers={"xi-api-key": el_key, "Content-Type": "application/json"},
                        json={
                            "text": text,
                            "model_id": "eleven_multilingual_v2",
                            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                        },
                        timeout=60,
                    )
                    if r.status_code == 200:
                        clip_path.write_bytes(r.content)
                        clip_paths.append(str(clip_path))
                    else:
                        all_ok = False
                        break
                except Exception:
                    all_ok = False
                    break

            if all_ok and clip_paths:
                if len(clip_paths) == 1:
                    _sh2.copy(clip_paths[0], str(out))
                else:
                    list_file = tmpdir / "list.txt"
                    list_file.write_text("\n".join(f"file '{p}'" for p in clip_paths))
                    result = subprocess.run(
                        [_ffmpeg, "-y", "-f", "concat", "-safe", "0",
                         "-i", str(list_file), "-c", "copy", str(out)],
                        capture_output=True, timeout=300,
                    )
                    if result.returncode != 0:
                        all_ok = False  # fall through to gTTS
                _sh2.rmtree(str(tmpdir), ignore_errors=True)
                if all_ok and out.exists():
                    return jsonify({"url": f"/audio/{fname}", "engine": "elevenlabs"})
            else:
                _sh2.rmtree(str(tmpdir), ignore_errors=True)

        # ── gTTS — single call on full concatenated transcript ────────────────
        # One HTTP session handles chunking internally; avoids per-turn rate
        # limits and the fragile ffmpeg-concat-or-first-clip fallback.
        import logging as _log
        _gtts_errors: list = []
        try:
            from gtts import gTTS as _gTTS

            # Build full text: "SPEAKER: text\n\n" for every non-empty turn
            parts = []
            for turn in turns:
                spk = (turn.get("speaker") or "").strip()
                txt = (turn.get("text")    or "").strip()
                if not txt:
                    continue
                parts.append(f"{spk + ': ' if spk else ''}{txt}")
            full_text = "\n\n".join(parts)

            if not full_text:
                return jsonify({"error": "TTS generation failed", "detail": ["no text"]}), 500

            # gTTS chunks long text automatically; no length cap needed
            _gTTS(text=full_text, lang="en").save(str(out))
            if out.exists():
                return jsonify({"url": f"/audio/{fname}", "engine": "gtts"})
            _gtts_errors.append("gTTS wrote no file")
        except Exception as _outer:
            _gtts_errors.append(str(_outer))
        _log.error("TTS all engines failed. gTTS errors: %s", _gtts_errors)
        return jsonify({"error": "TTS generation failed", "detail": _gtts_errors}), 500

    # ── builder ───────────────────────────────────────────────────────────────
    @app.route("/api/builder", methods=["POST"])
    @_login_required
    def api_builder():
        from part8_personas import get_system_prompt
        d        = request.get_json(force=True) or {}
        task     = d.get("task", "")
        sys_over = d.get("system_prompt", "")
        sys_p    = sys_over or get_system_prompt("builder")
        msgs     = [{"role": "system", "content": sys_p}, {"role": "user", "content": task}]
        return _sse_single("builder", msgs)

    # ── builder job store — runs LLM in background thread, survives page nav ──
    _builder_jobs: dict[str, dict] = {}   # job_id → {status, chunks, error}

    @app.route("/api/builder/job", methods=["POST"])
    @_login_required
    def api_builder_job_create():
        from part8_personas import get_system_prompt
        d           = request.get_json(force=True) or {}
        task        = d.get("task", "")
        sys_over    = d.get("system_prompt", "")
        uncensored  = bool(d.get("uncensored", False))
        if not task:
            return jsonify({"error": "task required"}), 400
        role   = "builder_uncensored" if uncensored else "builder"
        sys_p  = sys_over or get_system_prompt(role)
        msgs   = [{"role": "system", "content": sys_p}, {"role": "user", "content": task}]
        job_id = uuid.uuid4().hex[:12]
        _builder_jobs[job_id] = {"status": "running", "chunks": [], "error": None}

        # store extra context for continuation
        _builder_jobs[job_id]["role"]  = role
        _builder_jobs[job_id]["sys_p"] = sys_p
        _builder_jobs[job_id]["task"]  = task
        _builder_jobs[job_id]["output_incomplete"] = False

        def _output_incomplete(text: str) -> bool:
            if not text.strip():
                return False
            if text.count("```") % 2 == 1:
                return True
            s = text.rstrip()
            for marker in ("...", "…", "[continues", "[truncated", "# TODO", "// TODO"):
                if s.endswith(marker):
                    return True
            return False

        def _run():
            from part2_router import stream_task as _st
            import asyncio as _aio
            loop = _aio.new_event_loop()
            async def _collect():
                try:
                    async for chunk in _st(role, msgs, max_tokens=12000):
                        _builder_jobs[job_id]["chunks"].append(chunk)
                except Exception as exc:
                    _builder_jobs[job_id]["error"] = str(exc)
                finally:
                    _builder_jobs[job_id]["status"] = "done"
                    output = "".join(_builder_jobs[job_id]["chunks"])
                    _builder_jobs[job_id]["output_incomplete"] = _output_incomplete(output)
            loop.run_until_complete(_collect())
            loop.close()
            # ── auto-save completed build ──────────────────────────────────────
            output = "".join(_builder_jobs[job_id]["chunks"])
            if output.strip():
                import datetime as _dt
                ts     = _dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                bfile  = _BUILDS_DIR / f"{ts}_{job_id}.json"
                bfile.write_text(json.dumps({
                    "id":         job_id,
                    "ts":         ts,
                    "task":       task,
                    "role":       role,
                    "output":     output,
                    "error":      _builder_jobs[job_id]["error"],
                }, indent=2), encoding="utf-8")

        Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True, "job_id": job_id})

    @app.route("/api/builder/job/<job_id>", methods=["GET"])
    @_login_required
    def api_builder_job_status(job_id):
        job = _builder_jobs.get(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "status":            job["status"],
            "chunks":            job["chunks"],
            "error":             job["error"],
            "output_incomplete": job.get("output_incomplete", False),
        })

    @app.route("/api/builder/job/<job_id>/continue", methods=["POST"])
    @_login_required
    def api_builder_job_continue(job_id):
        job = _builder_jobs.get(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        if job.get("status") == "running":
            return jsonify({"error": "still running"}), 400
        existing = "".join(job["chunks"])
        role  = job.get("role", "builder")
        sys_p = job.get("sys_p", "")
        orig  = job.get("task", "")
        if not sys_p:
            from part8_personas import get_system_prompt
            sys_p = get_system_prompt(role)
        cont_msgs = [
            {"role": "system",    "content": sys_p},
            {"role": "user",      "content": orig},
            {"role": "assistant", "content": existing},
            {"role": "user",      "content": (
                "Continue from exactly where you stopped. "
                "Do NOT repeat any code or text already written. "
                "Resume mid-line or mid-block if needed. "
                "Complete the implementation fully."
            )},
        ]
        job["status"]            = "running"
        job["output_incomplete"] = False

        def _output_incomplete(text: str) -> bool:
            if not text.strip():
                return False
            if text.count("```") % 2 == 1:
                return True
            s = text.rstrip()
            for marker in ("...", "…", "[continues", "[truncated", "# TODO", "// TODO"):
                if s.endswith(marker):
                    return True
            return False

        def _run_cont():
            from part2_router import stream_task as _st
            import asyncio as _aio
            loop = _aio.new_event_loop()
            async def _collect():
                try:
                    async for chunk in _st(role, cont_msgs, max_tokens=12000):
                        job["chunks"].append(chunk)
                except Exception as exc:
                    job["error"] = str(exc)
                finally:
                    job["status"] = "done"
                    job["output_incomplete"] = _output_incomplete("".join(job["chunks"]))
            loop.run_until_complete(_collect())
            loop.close()

        Thread(target=_run_cont, daemon=True).start()
        return jsonify({"ok": True, "job_id": job_id})

    # Pending-edit store: edit_id → {file_path, new_code, description}
    _pending_edits: dict[str, dict] = {}

    def _safe_project_path(raw_fp: str) -> "Path | None":
        """
        Resolve raw_fp relative to _ROOT and confirm it stays inside _ROOT.
        Returns the resolved Path on success, None if it escapes (path traversal guard).
        """
        try:
            resolved = (_ROOT / raw_fp).resolve()
            _ROOT.resolve()  # ensure _ROOT itself resolves
            resolved.relative_to(_ROOT.resolve())  # raises ValueError if outside
            return resolved
        except (ValueError, Exception):
            return None

    @app.route("/api/builder/stage_edit", methods=["POST"])
    @_login_required
    def api_builder_stage_edit():
        """Register a pending edit and return an edit_id for the approval UI."""
        d    = request.get_json(force=True) or {}
        fp   = d.get("file_path", "")
        code = d.get("new_code", "")
        desc = d.get("description", "")
        if not fp or not code:
            return jsonify({"error": "file_path and new_code required"}), 400
        if _safe_project_path(fp) is None:
            return jsonify({"error": "file_path escapes project root — rejected"}), 400
        eid  = uuid.uuid4().hex[:12]
        _pending_edits[eid] = {"file_path": fp, "new_code": code, "description": desc}
        return jsonify({"ok": True, "edit_id": eid})

    @app.route("/api/builder/apply_edit", methods=["POST"])
    @_login_required
    def api_builder_apply_edit():
        import shutil as _sh, datetime as _dt
        d      = request.get_json(force=True) or {}
        eid    = d.get("edit_id", "")
        edit   = _pending_edits.pop(eid, None)
        if not edit:
            return jsonify({"error": f"No pending edit found for id={eid!r}. Stage the edit first."}), 404
        fp     = edit["file_path"]
        code   = edit["new_code"]
        target = _safe_project_path(fp)
        if target is None:
            return jsonify({"error": "file_path escapes project root — rejected"}), 400
        if not target.parent.exists():
            return jsonify({"error": f"Parent directory does not exist: {target.parent}"}), 400
        backup = str(target) + f".bak_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if target.exists():
            _sh.copy2(str(target), backup)
        target.write_text(code, encoding="utf-8")
        return jsonify({"ok": True, "file": fp, "backup": backup})

    @app.route("/api/builder/reject_edit", methods=["POST"])
    @_login_required
    def api_builder_reject_edit():
        d   = request.get_json(force=True) or {}
        eid = d.get("edit_id", "")
        _pending_edits.pop(eid, None)
        return jsonify({"ok": True})

    @app.route("/api/builder/builds", methods=["GET"])
    @_login_required
    def api_builder_builds_list():
        """List all saved builds, newest first."""
        builds = []
        for f in sorted(_BUILDS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                builds.append({
                    "id":    data.get("id", f.stem),
                    "file":  f.name,
                    "ts":    data.get("ts", ""),
                    "task":  data.get("task", "")[:120],
                    "role":  data.get("role", "builder"),
                    "error": data.get("error"),
                })
            except Exception:
                pass
        return jsonify(builds)

    @app.route("/api/builder/builds/<filename>", methods=["GET"])
    @_login_required
    def api_builder_builds_get(filename):
        """Return full content of a saved build."""
        if not re.match(r"^[\w\-. ]+\.json$", filename):
            return jsonify({"error": "invalid filename"}), 400
        f = _BUILDS_DIR / filename
        if not f.exists():
            return jsonify({"error": "not found"}), 404
        return jsonify(json.loads(f.read_text(encoding="utf-8")))

    @app.route("/api/builder/builds/<filename>", methods=["DELETE"])
    @_login_required
    def api_builder_builds_delete(filename):
        """Delete a saved build."""
        if not re.match(r"^[\w\-. ]+\.json$", filename):
            return jsonify({"error": "invalid filename"}), 400
        f = _BUILDS_DIR / filename
        if f.exists():
            f.unlink()
        return jsonify({"ok": True})

    @app.route("/api/builder/save", methods=["POST"])
    @_login_required
    def api_builder_save():
        """Write generated code to a .py file in the project root (browser-accessible)."""
        d    = request.get_json(force=True) or {}
        name = d.get("name", "")
        code = d.get("code", "")
        if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", name):
            return jsonify({"error": "invalid name — use letters, numbers, _ and - only"}), 400
        if not code:
            return jsonify({"error": "no code provided"}), 400
        if len(code) > 131072:
            return jsonify({"error": "code too large (128KB max)"}), 400
        dest = _ROOT / f"{name}.py"
        dest.write_text(code, encoding="utf-8")
        return jsonify({"ok": True, "path": str(dest.relative_to(_ROOT))})

    @app.route("/api/builder/read_file", methods=["POST"])
    @_login_required
    def api_builder_read_file():
        """Read a project file for preview in the self-edit approval UI."""
        d  = request.get_json(force=True) or {}
        fp = d.get("file_path", "")
        target = _safe_project_path(fp)
        if target is None:
            return jsonify({"error": "path rejected"}), 400
        if not target.exists():
            return jsonify({"content": None, "exists": False})
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return jsonify({"content": content, "exists": True, "lines": content.count("\n") + 1})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/builder/deploy", methods=["POST"])
    def api_builder_deploy():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)
        d    = request.get_json(force=True) or {}
        name = d.get("name", "")
        code = d.get("code", "")
        if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", name):
            return jsonify({"error": "invalid name"}), 400
        if len(code) > 65536:
            return jsonify({"error": "code too large (64KB max)"}), 400
        (_ROOT / f"{name}.py").write_text(code)
        return jsonify({"ok": True})

    # ── pipeline (DB-backed) ──────────────────────────────────────────────────
    from part_db import (
        pipeline_load, pipeline_add, pipeline_delete, pipeline_update_status,
        history_append, history_load, history_clear,
        session_save, session_list, session_get, session_delete,
    )

    @app.route("/api/pipeline", methods=["GET", "POST"])
    @_login_required
    def api_pipeline():
        uname = session.get("username", "anon")
        if request.method == "GET":
            return jsonify(pipeline_load(uname))
        d   = request.get_json(force=True) or {}
        job = {"id": uuid.uuid4().hex[:8], "status": d.get("status", "queued"), **d}
        job = pipeline_add(uname, job)
        return jsonify(job)

    @app.route("/api/pipeline/<job_id>", methods=["DELETE"])
    @_login_required
    def api_pipeline_delete(job_id):
        pipeline_delete(job_id, session.get("username", "anon"))
        return jsonify({"ok": True})

    @app.route("/api/pipeline/<job_id>", methods=["PATCH"])
    @_login_required
    def api_pipeline_patch(job_id):
        d = request.get_json(force=True) or {}
        pipeline_update_status(job_id, d.get("status", "queued"), session.get("username", "anon"))
        return jsonify({"ok": True})

    @app.route("/api/pipeline/<job_id>/status", methods=["POST"])
    @_login_required
    def api_pipeline_status(job_id):
        d = request.get_json(force=True) or {}
        pipeline_update_status(job_id, d.get("status", "queued"), session.get("username", "anon"))
        return jsonify({"ok": True})

    # ── chat history (DB-backed) ───────────────────────────────────────────────
    @app.route("/api/history/load", methods=["GET"])
    @_login_required
    def api_history_load():
        uname = session.get("username", "anon")
        bot   = request.args.get("bot", None)
        rows  = history_load(uname, bot=bot, limit=60)
        return jsonify(rows)

    @app.route("/api/history/save", methods=["POST"])
    @_login_required
    def api_history_save():
        uname = session.get("username", "anon")
        d     = request.get_json(force=True) or {}
        history_append(uname, d.get("bot", "twin"), d.get("role", "user"), d.get("content", ""))
        return jsonify({"ok": True})

    @app.route("/api/history/clear", methods=["POST"])
    @_login_required
    def api_history_clear():
        uname = session.get("username", "anon")
        bot   = (request.get_json(force=True) or {}).get("bot", None)
        history_clear(uname, bot=bot)
        return jsonify({"ok": True})

    # ── sessions save/load (boardroom / brainstorm / podcast) ─────────────────
    @app.route("/api/sessions/save", methods=["POST"])
    @_login_required
    def api_sessions_save():
        uname = session.get("username", "anon")
        d     = request.get_json(force=True) or {}
        sid   = session_save(uname, d.get("session_type","misc"), d.get("title",""), d.get("content",[]))
        return jsonify({"ok": bool(sid), "id": sid})

    @app.route("/api/sessions/list", methods=["GET"])
    @_login_required
    def api_sessions_list():
        uname = session.get("username", "anon")
        stype = request.args.get("type", None)
        return jsonify(session_list(uname, session_type=stype))

    @app.route("/api/sessions/<int:sid>", methods=["GET"])
    @_login_required
    def api_sessions_get(sid):
        uname = session.get("username", "anon")
        row   = session_get(sid, uname)
        return jsonify(row) if row else ("not found", 404)

    @app.route("/api/sessions/<int:sid>", methods=["DELETE"])
    @_login_required
    def api_sessions_delete(sid):
        session_delete(sid, session.get("username", "anon"))
        return jsonify({"ok": True})

    # ── transcripts / audio ───────────────────────────────────────────────────
    @app.route("/api/transcript/save", methods=["POST"])
    @_login_required
    def api_transcript_save():
        uname = session.get("username", "anon")
        d     = request.get_json(force=True) or {}
        stype = d.get("session_type", "misc")
        title = d.get("title", "")
        turns = d.get("turns") or [{"speaker": "LOG", "text": d.get("content", "")}]
        sid   = session_save(uname, stype, title, turns)
        return jsonify({"ok": bool(sid), "id": sid})

    @app.route("/api/audio/list")
    @_login_required
    def api_audio_list():
        files = [f.name for f in sorted(_AUDIO_DIR.iterdir()) if f.suffix == ".mp3"]
        return jsonify(files)

    @app.route("/api/transcripts/list")
    @_login_required
    def api_transcripts_list():
        uname = session.get("username", "anon")
        return jsonify(session_list(uname))

    # ── social ────────────────────────────────────────────────────────────────
    try:
        from part12_social import run_social_pipeline, get_job, list_jobs
        _social_ok = True
    except Exception:
        _social_ok = False

    # Map file_id → local filesystem path for the social pipeline
    _social_uploads: dict = {}

    @app.route("/api/social/upload", methods=["POST"])
    @_login_required
    def api_social_upload():
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "no file"}), 400
        fname = f"{uuid.uuid4().hex[:12]}{Path(f.filename).suffix.lower()}"
        dest  = _UPLOADS_DIR / fname
        f.save(str(dest))
        file_id = fname                      # file_id IS the filename
        _social_uploads[file_id] = str(dest)
        return jsonify({"ok": True, "file_id": file_id, "url": f"/uploads/{fname}"})

    @app.route("/api/social/process", methods=["POST"])
    @_login_required
    def api_social_process():
        if not _social_ok:
            return jsonify({"error": "social module unavailable"}), 503
        d          = request.get_json(force=True) or {}
        file_id    = d.get("file_id", d.get("video_path", ""))
        platforms  = d.get("platforms", ["tiktok"])
        max_clips  = int(d.get("max_clips", 5))
        # Resolve file_id → local path
        local_path = _social_uploads.get(file_id)
        if not local_path:
            # Try treating file_id as a direct path or uploads URL
            candidate = _UPLOADS_DIR / Path(file_id).name
            if candidate.exists():
                local_path = str(candidate)
            else:
                return jsonify({"error": f"Unknown file_id: {file_id}. Upload the file first."}), 400
        # Pre-assign a job_id so we can return it immediately
        job_id = f"SOC{uuid.uuid4().hex[:6].upper()}"
        Thread(
            target=lambda: _run_async(
                run_social_pipeline(local_path, platforms, max_clips=max_clips, job_id=job_id)
            ),
            daemon=True,
        ).start()
        return jsonify({"ok": True, "job_id": job_id})

    @app.route("/api/social/job/<job_id>")
    @_login_required
    def api_social_job(job_id):
        if not _social_ok:
            return jsonify({"error": "unavailable"}), 503
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify(job.__dict__ if hasattr(job, "__dict__") else job)

    @app.route("/api/social/jobs")
    @_login_required
    def api_social_jobs():
        if not _social_ok:
            return jsonify([])
        return jsonify([j.__dict__ if hasattr(j, "__dict__") else j for j in list_jobs()])

    @app.route("/api/social/download/<job_id>/<int:idx>")
    @_login_required
    def api_social_download(job_id, idx):
        if not _social_ok:
            abort(404)
        job   = get_job(job_id)
        clips = getattr(job, "clips", []) if job else []
        if not clips or idx >= len(clips):
            abort(404)
        cp = clips[idx]
        clip_path = cp.get("clip_path") if isinstance(cp, dict) else getattr(cp, "clip_path", None)
        if not clip_path or not Path(clip_path).exists():
            abort(404)
        return send_file(clip_path, as_attachment=True)

    # ── story ─────────────────────────────────────────────────────────────────
    try:
        from part10_story import (
            bible_chat, complete_bible, generate_full_story,
            get_story, list_stories, StoryBible, StoryResult, _stories,
        )
        _story_ok = True
    except Exception:
        _story_ok = False

    # Per-session bible chat history: {session_id: {"history": [...], "bible": dict|None}}
    _bible_sessions: dict = {}

    def _serialize_story(s) -> dict:
        """Serialize a StoryResult to JSON-safe dict (handles nested StoryBible)."""
        if not hasattr(s, "__dict__"):
            return s
        d = {}
        for k, v in s.__dict__.items():
            if hasattr(v, "__dict__"):
                d[k] = v.__dict__
            else:
                d[k] = v
        return d

    @app.route("/api/story/bible/chat", methods=["POST"])
    @_login_required
    def api_bible_chat():
        if not _story_ok:
            return jsonify({"error": "story module unavailable"}), 503
        d          = request.get_json(force=True) or {}
        msg        = d.get("message", "")
        session_id = d.get("session_id") or uuid.uuid4().hex[:12]
        writer     = d.get("writer", "twin")

        if session_id not in _bible_sessions:
            _bible_sessions[session_id] = {"history": [], "bible": None}
        sess = _bible_sessions[session_id]

        try:
            response_text, bible_dict = _run_async(
                bible_chat(msg, sess["history"], partial_bible=sess["bible"], writer=writer)
            )
            sess["history"].append({"role": "user",      "content": msg})
            sess["history"].append({"role": "assistant", "content": response_text})
            if bible_dict:
                sess["bible"] = bible_dict
            return jsonify({
                "response":   response_text,
                "session_id": session_id,
                "bible":      sess["bible"],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/story/bible/complete", methods=["POST"])
    @_login_required
    def api_bible_complete():
        if not _story_ok:
            return jsonify({"error": "story module unavailable"}), 503
        d          = request.get_json(force=True) or {}
        session_id = d.get("session_id", "")
        writer     = d.get("writer", "twin")
        # Accept bible from request body, fall back to session store
        partial = d.get("bible", {})
        if not partial and session_id in _bible_sessions:
            partial = _bible_sessions[session_id].get("bible") or {}
        try:
            result = _run_async(complete_bible(partial, d.get("idea", ""), writer=writer))
            out = result.__dict__ if hasattr(result, "__dict__") else result
            if session_id in _bible_sessions:
                _bible_sessions[session_id]["bible"] = out
            return jsonify({"bible": out})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/story/generate", methods=["POST"])
    @_login_required
    def api_story_generate():
        if not _story_ok:
            return jsonify({"error": "story module unavailable"}), 503
        d          = request.get_json(force=True) or {}
        bible_dict = d.get("bible", {})
        length     = d.get("length", "short")
        writer     = d.get("writer", "twin")

        # Convert dict → StoryBible object (fixes AttributeError on .to_prompt_block())
        bible = StoryBible.from_dict(bible_dict) if bible_dict else StoryBible.from_dict({})

        # Pre-register so poll endpoint returns "generating" immediately
        story_id = f"S{uuid.uuid4().hex[:6].upper()}"
        _stories[story_id] = StoryResult(
            story_id=story_id, title=bible.title or "Untitled",
            bible=bible, chapters=[], word_count=0, status="generating",
        )

        Thread(
            target=lambda: _run_async(
                generate_full_story(bible, length, writer=writer, story_id=story_id)
            ),
            daemon=True,
        ).start()
        return jsonify({"ok": True, "story_id": story_id})

    @app.route("/api/story/list")
    @_login_required
    def api_story_list():
        if not _story_ok:
            return jsonify([])
        return jsonify([_serialize_story(s) for s in list_stories()])

    @app.route("/api/story/<story_id>")
    @_login_required
    def api_story_get(story_id):
        if not _story_ok:
            abort(404)
        s = get_story(story_id)
        if not s:
            return jsonify({"status": "not_found"}), 404
        return jsonify(_serialize_story(s))

    @app.route("/api/story/<story_id>/download")
    @_login_required
    def api_story_download(story_id):
        if not _story_ok:
            abort(404)
        s = get_story(story_id)
        if not s:
            abort(404)
        content = getattr(s, "full_text", "") or ""
        title   = getattr(s, "title", story_id) or story_id
        return Response(content, mimetype="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{title}.txt"'})

    @app.route("/api/story/<story_id>/chapter/<int:chap_num>")
    @_login_required
    def api_story_chapter(story_id, chap_num):
        if not _story_ok:
            abort(404)
        s = get_story(story_id)
        if not s:
            abort(404)
        chapters = getattr(s, "chapters", []) or []
        if chap_num < 1 or chap_num > len(chapters):
            return jsonify({"error": "chapter not found"}), 404
        return jsonify({"chapter": chap_num, "content": chapters[chap_num - 1]})

    # ── book ──────────────────────────────────────────────────────────────────
    try:
        from part11_book import split_story as _split_story, build_pdf_template1, ChildrensBook
        _book_ok = True
    except Exception:
        _book_ok = False

    @app.route("/api/book/generate", methods=["POST"])
    @_login_required
    def api_book_generate():
        if not _book_ok:
            return jsonify({"error": "book module unavailable"}), 503
        d         = request.get_json(force=True) or {}
        concept   = d.get("concept", "")
        title     = d.get("title", "My Book")
        num_pages = int(d.get("num_pages", 8))
        q: Q.Queue = Q.Queue()

        async def _gen():
            try:
                pages    = await _split_story(concept, title, num_pages)
                book     = ChildrensBook(title=title, pages=pages, concept=concept)
                fname    = f"book_{uuid.uuid4().hex[:8]}.pdf"
                pdf_path = build_pdf_template1(book, str(_RENDERS_DIR / fname))
                q.put(("ok", f"DONE:{pdf_path}|"))
            except Exception as e:
                q.put(("err", str(e)))
            q.put(None)

        Thread(target=lambda: _run_async(_gen()), daemon=True).start()

        def gen():
            yield _chunk_evt("Generating book...\n")
            while True:
                item = q.get()
                if item is None:
                    break
                kind, val = item
                yield _chunk_evt(val) if kind == "ok" else _err_evt(val)
            yield _done_evt()

        return _sse_response(gen)

    # ── video lab ─────────────────────────────────────────────────────────────
    _lab_jobs: dict[str, dict] = {}

    @app.route("/api/lab/upload", methods=["POST"])
    @_login_required
    def api_lab_upload():
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "no file"}), 400
        ext   = Path(f.filename).suffix.lower()
        fname = f"{uuid.uuid4().hex[:12]}{ext}"
        dest  = _UPLOADS_DIR / fname
        f.save(str(dest))
        _vid_exts   = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
        _audio_exts = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}
        if ext in _vid_exts:
            file_type = "video"
        elif ext in _audio_exts:
            file_type = "audio"
        else:
            file_type = "image"
        url_path = f"/uploads/{fname}"
        return jsonify({
            "ok":        True,
            "path":      url_path,
            "url":       url_path,
            "name":      f.filename,
            "file_type": file_type,
        })

    @app.route("/api/lab/music")
    @_login_required
    def api_lab_music():
        music_dir = _STATIC / "music"
        if not music_dir.exists():
            return jsonify([])
        return jsonify([{"name": f.name, "url": f"/static/music/{f.name}",
                         "path": f"/static/music/{f.name}"}
                        for f in sorted(music_dir.iterdir())
                        if f.suffix.lower() in (".mp3", ".wav", ".ogg")])

    @app.route("/api/lab/music/add", methods=["POST"])
    @_login_required
    def api_lab_music_add():
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "no file"}), 400
        music_dir = _STATIC / "music"
        music_dir.mkdir(exist_ok=True)
        (music_dir / Path(f.filename).name).write_bytes(f.read())
        return jsonify({"ok": True})

    @app.route("/api/lab/storyboard", methods=["POST"])
    @_login_required
    def api_lab_storyboard():
        from part2_router import call_task as _ct
        d       = request.get_json(force=True) or {}
        concept = d.get("concept", "")
        msgs    = [
            {"role": "system", "content": (
                "You generate short-form video storyboards as JSON. "
                "Return ONLY valid JSON — no markdown, no code fences, no explanation. "
                "Output a JSON object with a 'scenes' array of 4-6 scenes. "
                "Each scene must have EXACTLY these fields: "
                "id (string like 'scene_1'), "
                "title (short scene name), "
                "description (detailed visual prompt for image/video AI — rich, specific, cinematic), "
                "voiceover (spoken narration text for this scene, 1-3 sentences), "
                "style (visual style descriptor like 'dark cinematic occult', 'neon noir', etc.), "
                "duration (integer seconds, 3-6). "
                "Example: {\"scenes\": [{\"id\": \"scene_1\", \"title\": \"Opening\", "
                "\"description\": \"A hooded figure stands at the edge of a cliff at dusk...\", "
                "\"voiceover\": \"In the beginning, there was only silence.\", "
                "\"style\": \"dark cinematic occult\", \"duration\": 4}]}"
            )},
            {"role": "user", "content": f"Create a storyboard for: {concept}"},
        ]
        try:
            raw = _run_async(_ct("twin", msgs))
            # Strip markdown fences if model wrapped in them
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
            # Find the JSON object
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return jsonify({"error": "Model did not return valid JSON", "raw": raw[:500]}), 500
            parsed = json.loads(m.group(0))
            # Ensure scenes have required fields with fallbacks
            scenes = parsed.get("scenes", [])
            for i, sc in enumerate(scenes):
                sc.setdefault("id", f"scene_{i+1}")
                sc.setdefault("description", sc.get("visual_prompt", sc.get("title", f"Scene {i+1}")))
                sc.setdefault("voiceover", sc.get("narration", sc.get("script", "")))
                sc.setdefault("style", "cinematic dark occult")
                sc.setdefault("duration", 4)
                sc.setdefault("title", f"Scene {i+1}")
            return jsonify({"scenes": scenes})
        except json.JSONDecodeError as e:
            return jsonify({"error": f"JSON parse error: {e}", "raw": raw[:500]}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lab/image", methods=["POST"])
    @_login_required
    def api_lab_image():
        import urllib.request as _ur
        from part4_video import lab_generate_image
        d = request.get_json(force=True) or {}
        # Frontend sends 'description'; support both field names
        prompt = d.get("prompt") or d.get("description", "")
        style  = d.get("style", "")
        ref    = d.get("reference_frame") or d.get("reference_clip")
        if not prompt:
            return jsonify({"error": "No prompt or description provided"}), 400
        try:
            result = _run_async(lab_generate_image(prompt, style, ref))
            # Download image locally so the render slideshow step has real filesystem paths
            if result.get("url") and not result.get("local_path"):
                try:
                    provider  = result.get("provider", "img")
                    img_fname = f"{provider}_{uuid.uuid4().hex[:8]}.jpg"
                    img_local = _UPLOADS_DIR / img_fname
                    _ur.urlretrieve(result["url"], str(img_local))
                    result["local_path"] = f"/uploads/{img_fname}"
                except Exception:
                    pass   # non-fatal — render falls back to clips-only
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lab/video/generate", methods=["POST"])
    @_login_required
    def api_lab_video_generate():
        from part4_video import run_video
        d      = request.get_json(force=True) or {}
        job_id = uuid.uuid4().hex[:12]
        _lab_jobs[job_id] = {"status": "pending", "id": job_id}

        def _bg():
            try:
                _lab_jobs[job_id]["status"] = "running"
                result = _run_async(run_video(d))
                _lab_jobs[job_id].update({"status": "done", "result": result})
            except Exception as e:
                _lab_jobs[job_id].update({"status": "error", "error": str(e)})

        Thread(target=_bg, daemon=True).start()
        return jsonify({"job_id": job_id})

    @app.route("/api/lab/video/status/<job_id>")
    @_login_required
    def api_lab_video_status(job_id):
        job = _lab_jobs.get(job_id)
        return jsonify(job) if job else (jsonify({"error": "not found"}), 404)

    @app.route("/api/lab/voice", methods=["POST"])
    @_login_required
    def api_lab_voice():
        d      = request.get_json(force=True) or {}
        # Frontend sends "text"; some callers send "script" — accept both
        script = d.get("text", d.get("script", ""))
        if not script:
            return jsonify({"error": "no script"}), 400
        fname = f"vo_{uuid.uuid4().hex[:8]}.mp3"
        out   = _AUDIO_DIR / fname
        try:
            from gtts import gTTS
            gTTS(text=script, lang="en").save(str(out))
            return jsonify({"url": f"/audio/{fname}", "path": f"/audio/{fname}"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lab/render", methods=["POST"])
    @_login_required
    def api_lab_render():
        from part4_video import images_to_slideshow, concatenate_clips, mix_audio_onto_video
        import shutil
        d      = request.get_json(force=True) or {}
        # Frontend sends image_paths / voiceover / output_name — accept both old and new field names
        images    = d.get("image_paths", d.get("images", []))
        clips     = d.get("clips",  [])
        audio     = d.get("voiceover", d.get("audio"))     # voiceover path
        music     = d.get("music")
        out_name  = d.get("output_name") or f"render_{uuid.uuid4().hex[:8]}"
        fname     = f"{out_name}.mp4" if not out_name.endswith(".mp4") else out_name
        out       = str(_RENDERS_DIR / fname)
        q: Q.Queue = Q.Queue()

        def _url_to_fs(p: str | None) -> str | None:
            """Resolve a web URL path like /audio/x.mp3 to an absolute filesystem path."""
            if not p:
                return None
            if p.startswith("/audio/"):
                return str(_AUDIO_DIR / p[len("/audio/"):])
            if p.startswith("/static/"):
                return str(_ROOT / "static" / p[len("/static/"):])
            if p.startswith("/uploads/"):
                return str(_ROOT / "uploads" / p[len("/uploads/"):])
            if p.startswith("/renders/"):
                return str(_RENDERS_DIR / p[len("/renders/"):])
            return p  # already a filesystem path

        def _bg():
            try:
                if images:
                    q.put(("ok", "Building slideshow...\n"))
                    tmp = str(_RENDERS_DIR / f"slide_{uuid.uuid4().hex[:6]}.mp4")
                    ok, msg = images_to_slideshow([_url_to_fs(i) or i for i in images], tmp)
                    if not ok:
                        q.put(("err", f"Slideshow: {msg}")); q.put(None); return
                    video = tmp
                elif clips:
                    q.put(("ok", "Concatenating clips...\n"))
                    ok, msg = concatenate_clips([_url_to_fs(c) or c for c in clips], out)
                    if not ok:
                        q.put(("err", f"Concat: {msg}")); q.put(None); return
                    video = out
                else:
                    q.put(("err", "No images or clips provided.")); q.put(None); return

                vo_path = _url_to_fs(audio)
                mu_path = _url_to_fs(music)

                if vo_path or mu_path:
                    q.put(("ok", "Mixing audio...\n"))
                    ok, msg = mix_audio_onto_video(
                        video,
                        vo_path,   # voiceover_path
                        mu_path,   # music_path (always 25% vol)
                        out,       # output_path
                    )
                    if not ok:
                        q.put(("err", f"Audio: {msg}")); q.put(None); return
                else:
                    if video != out:
                        shutil.copy(video, out)

                q.put(("ok", f"DONE:/renders/{fname}"))
            except Exception as e:
                q.put(("err", str(e)))
            q.put(None)

        Thread(target=_bg, daemon=True).start()

        def gen():
            while True:
                item = q.get()
                if item is None:
                    break
                kind, val = item
                if kind == "ok":
                    yield _chunk_evt(val)
                else:
                    yield _err_evt(val)
                    return
            yield _done_evt()

        return _sse_response(gen)

    @app.route("/api/lab/concept", methods=["GET", "POST"])
    @_login_required
    def api_lab_concept():
        if request.method == "GET":
            return jsonify(json.loads(_LAB_CONCEPT.read_text()) if _LAB_CONCEPT.exists() else {})
        _LAB_CONCEPT.write_text(json.dumps(request.get_json(force=True) or {}))
        return jsonify({"ok": True})

    # ── lab session persistence ────────────────────────────────────────────────
    @app.route("/api/lab/session", methods=["GET", "POST"])
    @_login_required
    def api_lab_session():
        uname    = session.get("username", "anon")
        sess_file = _ROOT / f"lab_session_{uname}.json"
        if request.method == "GET":
            if sess_file.exists():
                try:
                    return jsonify(json.loads(sess_file.read_text()))
                except Exception:
                    return jsonify({})
            return jsonify({})
        data = request.get_json(force=True) or {}
        sess_file.write_text(json.dumps(data))
        return jsonify({"ok": True})

    # ── lab file listing ───────────────────────────────────────────────────────
    @app.route("/api/lab/files")
    @_login_required
    def api_lab_files():
        exts_video = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
        exts_audio = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}
        exts_image = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        files = []
        if _UPLOADS_DIR.exists():
            for f in sorted(_UPLOADS_DIR.iterdir(), key=lambda x: -x.stat().st_mtime):
                ext = f.suffix.lower()
                if ext in exts_video | exts_audio | exts_image:
                    ftype = "video" if ext in exts_video else ("audio" if ext in exts_audio else "image")
                    files.append({
                        "name": f.name, "url": f"/uploads/{f.name}",
                        "path": str(f), "type": ftype,
                        "size": f.stat().st_size,
                    })
        if _RENDERS_DIR.exists():
            for f in sorted(_RENDERS_DIR.iterdir(), key=lambda x: -x.stat().st_mtime):
                if f.suffix.lower() == ".mp4":
                    files.append({
                        "name": f.name, "url": f"/renders/{f.name}",
                        "path": str(f), "type": "render",
                        "size": f.stat().st_size,
                    })
        return jsonify(files)

    # ── pexels stock footage ───────────────────────────────────────────────────
    @app.route("/api/lab/pexels")
    @_login_required
    def api_lab_pexels():
        import urllib.request as _ur
        key = os.environ.get("PEXELS_API_KEY", "")
        if not key:
            return jsonify({"results": [], "error": "PEXELS_API_KEY not set"})
        q           = request.args.get("q", "")
        orientation = request.args.get("orientation", "landscape")
        if not q:
            return jsonify({"results": []})
        url = f"https://api.pexels.com/videos/search?query={urllib.parse.quote(q)}&per_page=12&orientation={orientation}"
        req = urllib.request.Request(url, headers={"Authorization": key})
        try:
            with _ur.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            results = []
            for v in data.get("videos", []):
                files = v.get("video_files", [])
                hd = next((f for f in files if f.get("quality") in ("hd", "sd")), files[0] if files else None)
                if hd:
                    results.append({
                        "id": v["id"], "url": hd["link"],
                        "thumb": v.get("image", ""),
                        "duration": v.get("duration", 0),
                        "width": hd.get("width", 0), "height": hd.get("height", 0),
                    })
            return jsonify({"results": results})
        except Exception as e:
            return jsonify({"results": [], "error": str(e)})

    @app.route("/api/lab/pexels/import", methods=["POST"])
    @_login_required
    def api_lab_pexels_import():
        import urllib.request as _ur
        d   = request.get_json(force=True) or {}
        url = d.get("url", "")
        if not url:
            return jsonify({"error": "url required"}), 400
        fname = f"pexels_{uuid.uuid4().hex[:8]}.mp4"
        dest  = _UPLOADS_DIR / fname
        try:
            _ur.urlretrieve(url, str(dest))
            return jsonify({"ok": True, "url": f"/uploads/{fname}", "path": str(dest), "name": fname})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── fonts listing ──────────────────────────────────────────────────────────
    @app.route("/api/lab/fonts")
    @_login_required
    def api_lab_fonts():
        defaults = [
            {"name": "VT323",             "url": "https://fonts.googleapis.com/css2?family=VT323&display=swap"},
            {"name": "Press Start 2P",    "url": "https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap"},
            {"name": "Courier New",       "url": None},
            {"name": "Impact",            "url": None},
            {"name": "Arial Black",       "url": None},
        ]
        return jsonify(defaults)

    # ── ensure file is local (download remote URL) ─────────────────────────────
    @app.route("/api/lab/ensure_local", methods=["POST"])
    @_login_required
    def api_lab_ensure_local():
        import urllib.request as _ur
        d   = request.get_json(force=True) or {}
        url = d.get("url", "")
        if not url:
            return jsonify({"error": "url required"}), 400
        if url.startswith("/"):
            # Already a local path served by this server — resolve to filesystem
            rel = url.lstrip("/")
            local = _ROOT / rel
            if local.exists():
                return jsonify({"ok": True, "path": str(local), "url": url})
            return jsonify({"error": "local file not found"}), 404
        suffix = Path(url.split("?")[0]).suffix.lower() or ".mp4"
        fname  = f"dl_{uuid.uuid4().hex[:8]}{suffix}"
        dest   = _UPLOADS_DIR / fname
        try:
            _ur.urlretrieve(url, str(dest))
            return jsonify({"ok": True, "path": str(dest), "url": f"/uploads/{fname}"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── transcribe audio/video ─────────────────────────────────────────────────
    @app.route("/api/lab/transcribe", methods=["POST"])
    @_login_required
    def api_lab_transcribe():
        import urllib.request as _ur
        d         = request.get_json(force=True) or {}
        file_path = d.get("path", d.get("url", ""))
        hf_key    = os.environ.get("HUGGINGFACE_API_KEY", "")
        if not file_path:
            return jsonify({"error": "path required"}), 400
        # Resolve to local path
        if file_path.startswith("/uploads/") or file_path.startswith("/renders/"):
            rel  = file_path.lstrip("/")
            local = _ROOT / rel
        else:
            local = Path(file_path)
        if not local.exists():
            return jsonify({"error": f"File not found: {file_path}"}), 404
        if not hf_key:
            return jsonify({"error": "HUGGINGFACE_API_KEY not set — transcription unavailable"}), 400
        try:
            with open(str(local), "rb") as fh:
                audio_bytes = fh.read()
            req = urllib.request.Request(
                "https://api-inference.huggingface.co/models/openai/whisper-large-v3",
                data=audio_bytes,
                headers={"Authorization": f"Bearer {hf_key}", "Content-Type": "audio/mpeg"},
                method="POST",
            )
            with _ur.urlopen(req, timeout=120) as r:
                result = json.loads(r.read())
            return jsonify({"text": result.get("text", ""), "ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── highlight extraction (LLM-based) ───────────────────────────────────────
    @app.route("/api/lab/highlights", methods=["POST"])
    @_login_required
    def api_lab_highlights():
        from part2_router import call_task as _ct
        d          = request.get_json(force=True) or {}
        transcript = d.get("transcript", "")
        duration   = d.get("duration", 0)
        count      = min(int(d.get("count", 3)), 8)
        if not transcript:
            return jsonify({"error": "transcript required"}), 400
        msgs = [
            {"role": "system", "content": (
                "You extract highlight timestamps from transcripts. "
                "Return ONLY valid JSON: {\"highlights\": [{\"start\": 12.5, \"end\": 45.0, "
                "\"label\": \"Best moment\", \"reason\": \"why this is a highlight\"}]}. "
                "No markdown. No explanation. Just JSON."
            )},
            {"role": "user", "content": (
                f"Extract {count} highlight clips from this transcript. "
                f"Video duration: {duration}s.\n\nTRANSCRIPT:\n{transcript[:4000]}"
            )},
        ]
        try:
            raw = _run_async(_ct("twin", msgs))
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
            m   = re.search(r"\{[\s\S]*\}", raw)
            parsed = json.loads(m.group(0)) if m else {"highlights": []}
            return jsonify(parsed)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── FFmpeg cut ─────────────────────────────────────────────────────────────
    @app.route("/api/lab/cut", methods=["POST"])
    @_login_required
    def api_lab_cut():
        import subprocess, shutil
        d     = request.get_json(force=True) or {}
        path  = d.get("path", "")
        start = float(d.get("start", 0))
        end   = float(d.get("end", 0))
        if not path or end <= start:
            return jsonify({"error": "path, start, end required and end > start"}), 400
        # Resolve local file
        if path.startswith("/uploads/") or path.startswith("/renders/"):
            local = _ROOT / path.lstrip("/")
        else:
            local = Path(path)
        if not local.exists():
            return jsonify({"error": "file not found"}), 404
        ffmpeg = shutil.which("ffmpeg") or "/nix/store/3zc5jbvqzrn8zmva4fx5p19goxxa8bm-ffmpeg-7.1/bin/ffmpeg"
        fname  = f"cut_{uuid.uuid4().hex[:8]}.mp4"
        out    = _UPLOADS_DIR / fname
        try:
            result = subprocess.run([
                ffmpeg, "-y", "-i", str(local),
                "-ss", str(start), "-to", str(end),
                "-c", "copy", str(out),
            ], capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return jsonify({"error": result.stderr[-500:]}), 500
            return jsonify({"ok": True, "url": f"/uploads/{fname}", "path": str(out)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── LLM metadata generation ────────────────────────────────────────────────
    @app.route("/api/lab/metadata", methods=["POST"])
    @_login_required
    def api_lab_metadata():
        from part2_router import call_task as _ct
        d       = request.get_json(force=True) or {}
        concept = d.get("concept", d.get("prompt", ""))
        platform = d.get("platform", "youtube")
        if not concept:
            return jsonify({"error": "concept required"}), 400
        msgs = [
            {"role": "system", "content": (
                "You generate video metadata optimized for platform algorithms. "
                "Return ONLY valid JSON: {\"title\": \"...\", \"description\": \"...\", "
                "\"tags\": [\"tag1\", \"tag2\"], \"hashtags\": [\"#tag1\"], "
                "\"hook\": \"first 3 seconds hook line\", \"thumbnail_text\": \"short punchy text\"}. "
                "No markdown. Just JSON."
            )},
            {"role": "user", "content": f"Generate {platform} metadata for: {concept}"},
        ]
        try:
            raw = _run_async(_ct("twin", msgs))
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
            m   = re.search(r"\{[\s\S]*\}", raw)
            parsed = json.loads(m.group(0)) if m else {}
            return jsonify(parsed)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── FFmpeg reformat for platform ───────────────────────────────────────────
    @app.route("/api/lab/reformat", methods=["POST"])
    @_login_required
    def api_lab_reformat():
        import subprocess, shutil
        d        = request.get_json(force=True) or {}
        path     = d.get("path", "")
        platform = d.get("platform", "youtube").lower()
        if not path:
            return jsonify({"error": "path required"}), 400
        if path.startswith("/uploads/") or path.startswith("/renders/"):
            local = _ROOT / path.lstrip("/")
        else:
            local = Path(path)
        if not local.exists():
            return jsonify({"error": "file not found"}), 404
        # Platform format presets
        presets = {
            "tiktok":    {"w": 1080, "h": 1920, "fps": 30},
            "instagram": {"w": 1080, "h": 1920, "fps": 30},
            "youtube":   {"w": 1920, "h": 1080, "fps": 30},
            "shorts":    {"w": 1080, "h": 1920, "fps": 60},
            "twitter":   {"w": 1280, "h": 720,  "fps": 30},
            "square":    {"w": 1080, "h": 1080, "fps": 30},
        }
        p = presets.get(platform, presets["youtube"])
        ffmpeg = shutil.which("ffmpeg") or "/nix/store/3zc5jbvqzrn8zmva4fx5p19goxxa8bm-ffmpeg-7.1/bin/ffmpeg"
        fname  = f"rf_{platform}_{uuid.uuid4().hex[:6]}.mp4"
        out    = _RENDERS_DIR / fname
        vf     = f"scale={p['w']}:{p['h']}:force_original_aspect_ratio=decrease,pad={p['w']}:{p['h']}:-1:-1:color=black"
        try:
            result = subprocess.run([
                ffmpeg, "-y", "-i", str(local),
                "-vf", vf, "-r", str(p["fps"]),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", str(out),
            ], capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return jsonify({"error": result.stderr[-500:]}), 500
            return jsonify({"ok": True, "url": f"/renders/{fname}", "path": str(out), "platform": platform})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── music video ───────────────────────────────────────────────────────────
    @app.route("/api/lab/musicvideo", methods=["POST"])
    @_login_required
    def api_lab_musicvideo():
        from part4_video import (
            images_to_slideshow, concatenate_clips_with_fade, mix_audio_onto_video,
        )
        import shutil as _sh
        d          = request.get_json(force=True) or {}
        music      = d.get("music", "")
        files      = d.get("files", [])
        slide_dur  = float(d.get("slide_duration", 3.0))
        transition = d.get("transition", "fade")
        out_name   = d.get("output_name", f"mv_{uuid.uuid4().hex[:8]}")
        if not music:
            return jsonify({"error": "music track required"}), 400
        if not files:
            return jsonify({"error": "at least one image or clip required"}), 400

        fname = f"{out_name}.mp4"
        out   = str(_RENDERS_DIR / fname)
        q: Q.Queue = Q.Queue()

        def _resolve(p: str) -> str:
            if p.startswith("/uploads/") or p.startswith("/renders/"):
                return str(_ROOT / p.lstrip("/"))
            return p

        def _bg():
            try:
                img_exts  = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
                images    = [_resolve(f) for f in files if Path(f).suffix.lower() in img_exts]
                clips     = [_resolve(f) for f in files if Path(f).suffix.lower() not in img_exts]
                music_abs = _resolve(music)

                if images:
                    tmp_slide = str(_RENDERS_DIR / f"_slide_{uuid.uuid4().hex[:6]}.mp4")
                    q.put(("ok", f"- Building slideshow from {len(images)} images...\n"))
                    ok, msg = images_to_slideshow(images, tmp_slide, duration=slide_dur)
                    if not ok:
                        q.put(("err", f"Slideshow error: {msg}")); q.put(None); return
                    clips.insert(0, tmp_slide)

                if len(clips) > 1:
                    tmp_vid = str(_RENDERS_DIR / f"_cat_{uuid.uuid4().hex[:6]}.mp4")
                    q.put(("ok", f"- Joining {len(clips)} clips...\n"))
                    ok, msg = concatenate_clips_with_fade(clips, tmp_vid)
                    if not ok:
                        q.put(("err", f"Concat error: {msg}")); q.put(None); return
                    video = tmp_vid
                elif clips:
                    video = clips[0]
                else:
                    q.put(("err", "No usable media files.")); q.put(None); return

                q.put(("ok", "- Mixing music...\n"))
                ok, msg = mix_audio_onto_video(video, None, music_abs, out)
                if not ok:
                    q.put(("err", f"Audio mix error: {msg}")); q.put(None); return

                q.put(("ok", f"DONE:/renders/{fname}"))
            except Exception as e:
                q.put(("err", str(e)))
            q.put(None)

        Thread(target=_bg, daemon=True).start()

        def gen():
            while True:
                item = q.get()
                if item is None: break
                kind, val = item
                if kind == "ok":
                    yield _chunk_evt(val)
                else:
                    yield _err_evt(val); return
            yield _done_evt()

        return _sse_response(gen)

    # ── thumbnail ─────────────────────────────────────────────────────────────
    @app.route("/api/lab/thumbnail", methods=["POST"])
    @_login_required
    def api_lab_thumbnail():
        from part4_video import make_thumbnail
        d      = request.get_json(force=True) or {}
        src    = d.get("source_path", "")
        if not src:
            return jsonify({"error": "source_path required"}), 400
        if src.startswith("/uploads/") or src.startswith("/renders/"):
            local = _ROOT / src.lstrip("/")
        else:
            local = Path(src)
        if not local.exists():
            return jsonify({"error": f"file not found: {src}"}), 404

        fname  = f"thumb_{uuid.uuid4().hex[:8]}.jpg"
        out    = str(_RENDERS_DIR / fname)
        font   = d.get("font") or None
        if font and not Path(font).exists():
            font = None
        ok, msg = make_thumbnail(
            source_path = str(local),
            output_path = out,
            timestamp   = float(d.get("timestamp", 0.0)),
            text        = d.get("text", ""),
            position    = d.get("position", "bottom"),
            font_path   = font,
            font_size   = int(d.get("font_size", 52)),
            font_color  = d.get("font_color", "white"),
            outline     = bool(d.get("outline", True)),
        )
        if not ok:
            return jsonify({"error": msg}), 500
        return jsonify({"ok": True, "url": f"/renders/{fname}"})

    # ── bots ──────────────────────────────────────────────────────────────────
    _bots: dict[str, dict] = {}

    @app.route("/api/bots/spawn", methods=["POST"])
    @_login_required
    def api_bots_spawn():
        import subprocess, datetime as _dt
        d      = request.get_json(force=True) or {}
        name   = d.get("name", f"bot_{uuid.uuid4().hex[:6]}")
        # Accept explicit script path or infer from name
        script = d.get("script") or str(_ROOT / f"{name}.py")
        if not Path(script).exists():
            return jsonify({"error": f"Script not found: {script}. Build and deploy the bot first."}), 400
        try:
            proc = subprocess.Popen(["python3", script],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    cwd=str(_ROOT), text=True)
            _bots[name] = {
                "name": name, "pid": proc.pid, "proc": proc, "script": script,
                "created_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "output_lines": [],
            }
            return jsonify({"ok": True, "id": name, "name": name, "pid": proc.pid})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/bots/list")
    @_login_required
    def api_bots_list():
        import select
        out = []
        for k, v in _bots.items():
            proc = v["proc"]
            running = proc.poll() is None
            # Drain any new stdout without blocking
            try:
                while True:
                    r, _, _ = select.select([proc.stdout], [], [], 0)
                    if not r: break
                    line = proc.stdout.readline()
                    if not line: break
                    v["output_lines"].append(line.rstrip())
                    if len(v["output_lines"]) > 500:
                        v["output_lines"] = v["output_lines"][-500:]
            except Exception:
                pass
            tail = v["output_lines"][-3:]
            out.append({
                "id": k, "name": v["name"], "pid": v["pid"],
                "path": v.get("script", ""),
                "status": "running" if running else "stopped",
                "created_at": v.get("created_at", ""),
                "lines": len(v["output_lines"]),
                "tail": tail,
            })
        return jsonify(out)

    @app.route("/api/bots/stop/<bot_id>", methods=["POST"])
    @_login_required
    def api_bots_stop(bot_id):
        b = _bots.get(bot_id)
        if not b:
            return jsonify({"error": "not found"}), 404
        b["proc"].terminate()
        return jsonify({"ok": True})

    @app.route("/api/bots/<bot_id>", methods=["DELETE"])
    @_login_required
    def api_bots_delete(bot_id):
        b = _bots.pop(bot_id, None)
        if b:
            try: b["proc"].terminate()
            except Exception: pass
        return jsonify({"ok": True})

    @app.route("/api/bots/output/<bot_id>")
    @_login_required
    def api_bots_output(bot_id):
        import select
        b = _bots.get(bot_id)
        if not b:
            return jsonify({"error": "not found"}), 404
        max_lines = int(request.args.get("lines", 200))
        proc = b["proc"]
        try:
            while True:
                r, _, _ = select.select([proc.stdout], [], [], 0)
                if not r: break
                line = proc.stdout.readline()
                if not line: break
                b["output_lines"].append(line.rstrip())
                if len(b["output_lines"]) > 500:
                    b["output_lines"] = b["output_lines"][-500:]
        except Exception:
            pass
        return jsonify({"lines": b["output_lines"][-max_lines:]})

    # ── deploy ────────────────────────────────────────────────────────────────
    @app.route("/api/deploy/web", methods=["POST"])
    @_login_required
    def api_deploy_web():
        return jsonify({"ok": True, "message": "Use the Replit Publish button to deploy."})

    # ── guerrilla marketing war room ──────────────────────────────────────────
    @app.route("/api/gorilla/run", methods=["POST"])
    @_login_required
    def api_gorilla_run():
        from part14_gorilla import (
            GUERRILLA_COMMITTEE,
            guerrilla_intel_prompt, guerrilla_member_prompt, guerrilla_synthesizer_prompt,
        )
        from part2_router import direct_stream as _ds, stream_task as _st
        from part1_registry import get_task_routing

        d     = request.get_json(force=True) or {}
        brief = d.get("brief", "").strip()
        if not brief:
            return jsonify({"error": "brief required"}), 400

        q: Q.Queue = Q.Queue()

        async def _run():
            transcript = ""

            # Step 0: TWIN intelligence pre-brief (runs before committee)
            try:
                q.put(("speaker", "INTEL"))
                msgs  = guerrilla_intel_prompt(brief)
                intel = ""
                async for chunk in _st("twin", msgs):
                    q.put(("ok", chunk))
                    intel += chunk
                q.put(("end", "INTEL"))
                transcript += f"[TWIN INTELLIGENCE PRE-BRIEF]\n{intel}"
            except Exception as e:
                q.put(("err", f"[INTEL] {e}"))

            # Step 1-6: Six specialist committee members
            for member in GUERRILLA_COMMITTEE:
                name = member["name"]
                key  = member["key"]
                try:
                    provider, model = get_task_routing(key)
                    msgs = guerrilla_member_prompt(member, brief, transcript)
                    q.put(("speaker", name))
                    full = ""
                    async for chunk in _ds(provider, model, msgs):
                        q.put(("ok", chunk))
                        full += chunk
                    q.put(("end", name))
                    transcript += f"\n\n[{name} — {member['role']}]\n{full}"
                except Exception as e:
                    q.put(("err", f"[{name}] {e}"))

            # Step 7: TWIN synthesizes the final blueprint
            try:
                q.put(("speaker", "TWIN"))
                msgs = guerrilla_synthesizer_prompt(brief, transcript)
                async for chunk in _st("twin", msgs):
                    q.put(("ok", chunk))
                q.put(("end", "TWIN"))
            except Exception as e:
                q.put(("err", f"[TWIN] {e}"))

            q.put(None)

        Thread(target=lambda: _run_async(_run()), daemon=True).start()
        return _multi_agent_sse(q)

    @app.route("/api/gorilla/push_pipeline", methods=["POST"])
    @_login_required
    def api_gorilla_push_pipeline():
        d   = request.get_json(force=True) or {}
        job = {
            "id":     uuid.uuid4().hex[:8],
            "status": "pending",
            "type":   "gorilla_tactic",
            "title":  d.get("title", "Gorilla Tactic"),
            "detail": d.get("detail", ""),
            "phase":  d.get("phase", "1"),
        }
        pipeline_add(session.get("username", "anon"), job)
        return jsonify(job)

    # ── autonomous missions ────────────────────────────────────────────────────
    from part15_missions import (
        create_mission, get_mission, list_missions,
        delete_mission, retry_mission, MISSION_TYPES,
    )
    from part15_missions import MissionEngine as _ME
    _ME.get()   # start the background worker at import time

    @app.route("/api/missions", methods=["GET"])
    @_login_required
    def api_missions_list():
        return jsonify([m.to_dict() for m in list_missions()])

    @app.route("/api/missions", methods=["POST"])
    @_login_required
    def api_missions_create():
        d     = request.get_json(force=True) or {}
        title = d.get("title", "").strip()
        brief = d.get("brief", "").strip()
        mtype = d.get("type", "gorilla_marketing")
        if not title or not brief:
            return jsonify({"error": "title and brief required"}), 400
        if mtype not in MISSION_TYPES:
            return jsonify({"error": f"unknown type: {mtype}"}), 400
        m = create_mission(title, brief, mtype)
        return jsonify(m.to_dict()), 201

    @app.route("/api/missions/<mid>", methods=["GET"])
    @_login_required
    def api_missions_get(mid):
        m = get_mission(mid)
        if not m:
            return jsonify({"error": "not found"}), 404
        return jsonify(m.to_dict())

    @app.route("/api/missions/<mid>", methods=["DELETE"])
    @_login_required
    def api_missions_delete(mid):
        delete_mission(mid)
        return jsonify({"ok": True})

    @app.route("/api/missions/<mid>/retry", methods=["POST"])
    @_login_required
    def api_missions_retry(mid):
        m = retry_mission(mid)
        if not m:
            return jsonify({"error": "not found"}), 404
        return jsonify(m.to_dict())

    @app.route("/api/missions/types", methods=["GET"])
    @_login_required
    def api_missions_types():
        return jsonify(MISSION_TYPES)

    # ── dynamic committees ─────────────────────────────────────────────────────
    from part16_committees import (
        design_committee, list_committees, get_committee,
        delete_committee as _del_committee, COMMITTEE_ROSTER,
    )
    import asyncio as _asyncio

    def _run_async_c(coro):
        loop = _asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    @app.route("/api/committees/roster", methods=["GET"])
    @_login_required
    def api_committees_roster():
        return jsonify(COMMITTEE_ROSTER)

    @app.route("/api/committees/design", methods=["POST"])
    @_login_required
    def api_committees_design():
        d       = request.get_json(force=True) or {}
        request_text = d.get("request", "").strip()
        if not request_text:
            return jsonify({"error": "request required"}), 400
        try:
            cfg = _run_async_c(design_committee(request_text))
            return jsonify(cfg.to_dict())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/committees", methods=["GET"])
    @_login_required
    def api_committees_list():
        return jsonify([c.to_dict() for c in list_committees()])

    @app.route("/api/committees", methods=["POST"])
    @_login_required
    def api_committees_save():
        d = request.get_json(force=True) or {}
        if not d.get("id") or not d.get("members"):
            return jsonify({"error": "id and members required"}), 400
        from part16_committees import CommitteeConfig
        cfg = CommitteeConfig.from_dict(d)
        cfg.save()
        return jsonify(cfg.to_dict()), 201

    @app.route("/api/committees/<cid>", methods=["GET"])
    @_login_required
    def api_committees_get(cid):
        c = get_committee(cid)
        if not c:
            return jsonify({"error": "not found"}), 404
        return jsonify(c.to_dict())

    @app.route("/api/committees/<cid>", methods=["DELETE"])
    @_login_required
    def api_committees_delete(cid):
        _del_committee(cid)
        return jsonify({"ok": True})

    # ── smart guide / recommender ──────────────────────────────────────────────
    from part8_personas import TWIN_GUIDE_SYSTEM

    @app.route("/api/guide", methods=["POST"])
    @_login_required
    def api_guide():
        """User describes a goal → TWIN or SHADOW recommends the best tool + pre-fills brief."""
        from part8_personas import SHADOW_GUIDE_SYSTEM
        d     = request.get_json(force=True) or {}
        goal  = d.get("goal", "").strip()
        voice = d.get("voice", "twin").lower()   # "twin" or "shadow"
        if not goal:
            return jsonify({"error": "goal required"}), 400

        sys_prompt = TWIN_GUIDE_SYSTEM if voice != "shadow" else SHADOW_GUIDE_SYSTEM
        task_key   = "twin"  if voice != "shadow" else "shadow_chat"

        async def _call():
            from part2_router import call_task
            msgs = [
                {"role": "system", "content": sys_prompt},
                {"role": "user",   "content": f"USER GOAL:\n{goal}"},
            ]
            return await call_task(task_key, msgs)

        import asyncio as _aio
        loop = _aio.new_event_loop()
        try:
            raw = loop.run_until_complete(_call())
        finally:
            loop.close()

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(
                l for l in cleaned.split("\n") if not l.startswith("```")
            ).strip()

        try:
            rec = json.loads(cleaned)
            return jsonify(rec)
        except Exception:
            return jsonify({"explanation": raw, "tool": None, "title": "", "brief": "", "committee_request": ""})

    @app.route("/api/committees/<cid>/launch", methods=["POST"])
    @_login_required
    def api_committees_launch(cid):
        """Launch a saved committee as a new mission."""
        d     = request.get_json(force=True) or {}
        brief = d.get("brief", "").strip()
        if not brief:
            return jsonify({"error": "brief required"}), 400
        cfg = get_committee(cid)
        if not cfg:
            return jsonify({"error": "committee not found"}), 404
        m = create_mission(
            title       = d.get("title", cfg.name),
            brief       = brief,
            mission_type= "custom_committee",
        )
        import json as _json
        m.results["committee_config"] = _json.dumps(cfg.to_dict())
        m.save()
        return jsonify(m.to_dict()), 201

    # ══════════════════════════════════════════════════════════════════════════
    # LLM ADMIN PANEL — /api/admin/*
    # ══════════════════════════════════════════════════════════════════════════

    _ROLE_CATEGORIES = {
        "CORE":        ["twin","brief","shadow","shadow_chat","capi","relay"],
        "CREATIVE":    ["creative","creative_alt","creative_dolphin","multimodal",
                        "story","story_fallback","story_shadow","story_shadow_fallback"],
        "BUILDER":     ["builder","builder_uncensored","builder_review","builder_check",
                        "code","code_fallback","code_check","code_check_v2","code_reason"],
        "BOARDROOM":   ["board_strategy","board_finance","board_creative","board_tech",
                        "board_ops","board_distribution","board_venice","board_venice_llama",
                        "board_dolphin","board_hermes","board_gptoss","board_qwen",
                        "board_mistral","board_minimax","board_hermes_capped","shadow_chat"],
        "BRAINSTORM":  ["brainstorm_qwq","brainstorm_minimax","brainstorm_glm",
                        "brainstorm_kimi","brainstorm_mistral"],
        "COMMITTEE":   ["committee_strategy_1","committee_strategy_2","committee_strategy_3",
                        "committee_finance_1","committee_finance_2","committee_finance_3",
                        "committee_creative_1","committee_creative_2","committee_creative_3",
                        "committee_tech_1","committee_tech_2","committee_tech_3",
                        "committee_ops_1","committee_ops_2","committee_ops_3",
                        "committee_dist_1","committee_dist_2","committee_dist_3"],
        "SPECIALISTS": ["routing","summarize","vision","vision_fast","vision_uncensored",
                        "business","business_deep","legal_finance","fast_reasoning",
                        "chat_specialist"],
        "LOCAL":       ["local_shadow","local_adolphus"],
    }

    def _is_local_request() -> bool:
        return request.remote_addr in ("127.0.0.1", "::1", "localhost")

    @app.route("/api/admin/config", methods=["GET"])
    @_login_required
    def api_admin_config():
        from part1_registry import TASK_MODELS, PROVIDERS
        from part8_personas import get_system_prompt
        from part_llm_config import get_full_config, get_role_override, get_prompt_override
        cfg      = get_full_config()
        is_local = _is_local_request()
        roles    = []
        seen     = set()
        for cat, keys in _ROLE_CATEGORIES.items():
            for role in keys:
                if role in seen:
                    continue
                seen.add(role)
                if role not in TASK_MODELS:
                    continue
                if cat == "LOCAL" and not is_local:
                    continue
                def_p, def_m  = TASK_MODELS[role]
                ov            = get_role_override(role)
                cur_p         = ov["provider"]   if ov else def_p
                cur_m         = ov["model_key"]  if ov else def_m
                prompt_ov     = get_prompt_override(role)
                roles.append({
                    "role":               role,
                    "category":           cat,
                    "default_provider":   def_p,
                    "default_model_key":  def_m,
                    "current_provider":   cur_p,
                    "current_model_key":  cur_m,
                    "has_override":       ov is not None,
                    "current_prompt":     prompt_ov or get_system_prompt(role),
                    "has_prompt_override":prompt_ov is not None,
                })
        return jsonify({
            "roles":           roles,
            "is_local":        is_local,
            "ollama_base_url": cfg.get("ollama_base_url", "http://localhost:11434"),
            "templates":       list(cfg.get("templates", {}).keys()),
        })

    @app.route("/api/admin/models", methods=["GET"])
    @_login_required
    def api_admin_models():
        from part1_registry import PROVIDERS
        is_local = _is_local_request()
        result   = {}
        for prov, cfg in PROVIDERS.items():
            if prov == "ollama_local" and not is_local:
                continue
            models = cfg.get("models", {})
            result[prov] = {
                "desc":    cfg.get("desc", ""),
                "models":  list(models.keys()),
                "model_names": models,
            }
        return jsonify(result)

    @app.route("/api/admin/role/<role>", methods=["POST"])
    @_login_required
    def api_admin_set_role(role):
        from part1_registry import TASK_MODELS, PROVIDERS
        from part_llm_config import set_role as _sr
        d         = request.get_json(force=True) or {}
        provider  = d.get("provider", "").strip()
        model_key = d.get("model_key", "").strip()
        if not provider or not model_key:
            return jsonify({"error": "provider and model_key required"}), 400
        if provider not in PROVIDERS:
            return jsonify({"error": f"unknown provider: {provider}"}), 400
        if model_key not in PROVIDERS[provider].get("models", {}):
            return jsonify({"error": f"unknown model_key '{model_key}' for {provider}"}), 400
        _sr(role, provider, model_key)
        return jsonify({"ok": True, "role": role, "provider": provider, "model_key": model_key})

    @app.route("/api/admin/prompt/<role>", methods=["POST"])
    @_login_required
    def api_admin_set_prompt(role):
        from part_llm_config import set_prompt as _sp
        d      = request.get_json(force=True) or {}
        prompt = d.get("prompt", "")
        if not prompt.strip():
            return jsonify({"error": "prompt required"}), 400
        _sp(role, prompt)
        return jsonify({"ok": True, "role": role})

    @app.route("/api/admin/reset/role/<role>", methods=["POST"])
    @_login_required
    def api_admin_reset_role(role):
        from part_llm_config import reset_role as _rr
        _rr(role)
        return jsonify({"ok": True, "role": role, "reset": "role"})

    @app.route("/api/admin/reset/prompt/<role>", methods=["POST"])
    @_login_required
    def api_admin_reset_prompt(role):
        from part_llm_config import reset_prompt as _rp
        _rp(role)
        return jsonify({"ok": True, "role": role, "reset": "prompt"})

    @app.route("/api/admin/ollama/models", methods=["GET"])
    @_login_required
    def api_admin_ollama_models():
        import urllib.request as _ur
        import json as _j
        from part_llm_config import get_ollama_base_url
        base = get_ollama_base_url().rstrip("/")
        try:
            req  = _ur.Request(f"{base}/api/tags", headers={"Accept": "application/json"})
            with _ur.urlopen(req, timeout=5) as r:
                data   = _j.loads(r.read().decode())
                models = [m["name"] for m in data.get("models", [])]
                return jsonify({"ok": True, "models": models, "base_url": base})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "models": [], "base_url": base})

    @app.route("/api/admin/ollama/url", methods=["POST"])
    @_login_required
    def api_admin_ollama_url():
        from part_llm_config import set_ollama_base_url
        d   = request.get_json(force=True) or {}
        url = d.get("url", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        set_ollama_base_url(url)
        return jsonify({"ok": True, "url": url})

    @app.route("/api/admin/configs", methods=["GET"])
    @_login_required
    def api_admin_list_templates():
        from part_llm_config import list_templates
        return jsonify({"templates": list_templates()})

    @app.route("/api/admin/config/template", methods=["POST"])
    @_login_required
    def api_admin_save_template():
        from part_llm_config import save_template
        d    = request.get_json(force=True) or {}
        name = d.get("name", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        save_template(name)
        return jsonify({"ok": True, "name": name})

    @app.route("/api/admin/config/load/<name>", methods=["POST"])
    @_login_required
    def api_admin_load_template(name):
        from part_llm_config import load_template
        ok = load_template(name)
        if not ok:
            return jsonify({"error": "template not found"}), 404
        return jsonify({"ok": True, "name": name})

    @app.route("/api/admin/config/template/<name>", methods=["DELETE"])
    @_login_required
    def api_admin_delete_template(name):
        from part_llm_config import delete_template
        delete_template(name)
        return jsonify({"ok": True, "name": name})

    @app.route("/api/admin/ollama/assign", methods=["POST"])
    @_login_required
    def api_admin_ollama_assign():
        """Add a discovered Ollama model to ollama_local provider and assign it to a role."""
        from part1_registry import PROVIDERS
        from part_llm_config import set_role as _sr
        d         = request.get_json(force=True) or {}
        model_id  = d.get("model_id", "").strip()
        role      = d.get("role", "").strip()
        alias     = d.get("alias", model_id.split(":")[0]).strip()
        if not model_id or not role:
            return jsonify({"error": "model_id and role required"}), 400
        if not _is_local_request():
            return jsonify({"error": "ollama_local assignment only available on localhost"}), 403
        PROVIDERS["ollama_local"]["models"][alias] = model_id
        _sr(role, "ollama_local", alias)
        return jsonify({"ok": True, "role": role, "model_id": model_id, "alias": alias})
