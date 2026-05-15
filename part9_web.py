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
_STATIC          = _ROOT / "static"
_AUDIO_DIR       = _ROOT / "audio"
_RENDERS_DIR     = _ROOT / "renders"
_UPLOADS_DIR     = _ROOT / "uploads"
_USERS_FILE      = _ROOT / "users.json"
_INVITES_FILE    = _ROOT / "invites.json"
_SETTINGS_DIR    = _ROOT / "settings"
_TRANSCRIPTS_DIR = _ROOT / "transcripts"
_LAB_CONCEPT     = _ROOT / "lab_concept.json"

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
    @app.route("/")
    @_login_required
    def home():
        return send_from_directory(_STATIC, "index.html")

    @app.route("/lab")
    @_login_required
    def lab():
        return send_from_directory(_STATIC, "lab.html")

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

        # WEB_PASSWORD bypass (admin enters their env key directly)
        web_pw = os.environ.get("WEB_PASSWORD", "")
        if web_pw and password == web_pw and username in users:
            session.permanent = remember
            session["username"] = username
            session["is_admin"] = users[username].get("is_admin", False)
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
        d        = request.get_json(force=True) or {}
        bot      = d.get("bot", "twin")
        message  = d.get("message", "")
        history  = d.get("history", [])[-30:]
        sys_over = d.get("system_prompt", "")
        key      = "shadow_chat" if bot == "shadow" else "twin"
        sys_p    = sys_over or get_system_prompt(key)
        msgs     = [{"role": "system", "content": sys_p}] + history + [{"role": "user", "content": message}]
        return _sse_single(key, msgs)

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
        mode    = d.get("mode", "legal")
        message = d.get("message", "")
        history = d.get("history", [])[-20:]
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
        d      = request.get_json(force=True) or {}
        topic  = d.get("topic", "")
        cast   = d.get("cast", [])
        rounds = min(int(d.get("rounds", 1)), 4)
        prior  = d.get("prior_context", "")
        q: Q.Queue = Q.Queue()

        async def _run():
            ctx = prior or ""
            for rnd in range(rounds):
                for char in cast:
                    name = char.get("name", "HOST")
                    role = char.get("role", "Host")
                    key  = char.get("key",  "twin")
                    try:
                        provider, model = get_task_routing(key)
                        sys_p    = podcast_character_system(name, role)
                        user_msg = f"SHOW TOPIC: {topic}"
                        if ctx:
                            user_msg += f"\n\nDIALOGUE SO FAR:\n{ctx}"
                        user_msg += f"\n\nRound {rnd+1}. Your turn as {name}. Natural dialogue, 1-2 paragraphs."
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

    # ── TTS ───────────────────────────────────────────────────────────────────
    @app.route("/api/tts", methods=["POST"])
    @_login_required
    def api_tts():
        d     = request.get_json(force=True) or {}
        turns = d.get("turns", [])
        if not turns:
            return jsonify({"error": "no turns"}), 400
        text  = " ".join(f"{t.get('speaker','')}: {t.get('text','')}" for t in turns)
        fname = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        out   = _AUDIO_DIR / fname
        try:
            from gtts import gTTS
            gTTS(text=text[:3000], lang="en").save(str(out))
            return jsonify({"url": f"/audio/{fname}"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

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

    @app.route("/api/builder/apply_edit", methods=["POST"])
    @_login_required
    def api_builder_apply_edit():
        from part8_personas import get_system_prompt
        d           = request.get_json(force=True) or {}
        file_path   = d.get("file_path", "")
        instruction = d.get("instruction", "")
        current     = d.get("current_code", "")
        sys_p       = get_system_prompt("builder")
        prompt      = (f"Apply this edit to `{file_path}`:\n\n"
                       f"INSTRUCTION: {instruction}\n\n"
                       f"CURRENT CODE:\n```\n{current}\n```\n\n"
                       f"Return ONLY the complete updated file. No markdown fences.")
        msgs = [{"role": "system", "content": sys_p}, {"role": "user", "content": prompt}]
        return _sse_single("builder", msgs)

    @app.route("/api/builder/reject_edit", methods=["POST"])
    @_login_required
    def api_builder_reject_edit():
        return jsonify({"ok": True})

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

    # ── pipeline ──────────────────────────────────────────────────────────────
    _pipeline: list[dict] = []

    @app.route("/api/pipeline", methods=["GET", "POST"])
    @_login_required
    def api_pipeline():
        if request.method == "GET":
            return jsonify(_pipeline)
        job = {"id": uuid.uuid4().hex[:8], "status": "pending",
               **(request.get_json(force=True) or {})}
        _pipeline.append(job)
        return jsonify(job)

    @app.route("/api/pipeline/<job_id>", methods=["DELETE"])
    @_login_required
    def api_pipeline_delete(job_id):
        _pipeline[:] = [j for j in _pipeline if j["id"] != job_id]
        return jsonify({"ok": True})

    # ── transcripts / audio ───────────────────────────────────────────────────
    @app.route("/api/transcript/save", methods=["POST"])
    @_login_required
    def api_transcript_save():
        d    = request.get_json(force=True) or {}
        path = _TRANSCRIPTS_DIR / f"{uuid.uuid4().hex[:8]}.json"
        path.write_text(json.dumps(d, indent=2))
        return jsonify({"ok": True, "file": path.name})

    @app.route("/api/audio/list")
    @_login_required
    def api_audio_list():
        files = [f.name for f in sorted(_AUDIO_DIR.iterdir()) if f.suffix == ".mp3"]
        return jsonify(files)

    @app.route("/api/transcripts/list")
    @_login_required
    def api_transcripts_list():
        items = []
        for f in sorted(_TRANSCRIPTS_DIR.iterdir(), reverse=True):
            if f.suffix == ".json":
                try:
                    items.append(json.loads(f.read_text()))
                except Exception:
                    pass
        return jsonify(items)

    # ── social ────────────────────────────────────────────────────────────────
    try:
        from part12_social import run_social_pipeline, get_job, list_jobs
        _social_ok = True
    except Exception:
        _social_ok = False

    @app.route("/api/social/upload", methods=["POST"])
    @_login_required
    def api_social_upload():
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "no file"}), 400
        fname = f"{uuid.uuid4().hex[:12]}{Path(f.filename).suffix.lower()}"
        dest  = _UPLOADS_DIR / fname
        f.save(str(dest))
        return jsonify({"ok": True, "path": str(dest), "url": f"/uploads/{fname}"})

    @app.route("/api/social/process", methods=["POST"])
    @_login_required
    def api_social_process():
        if not _social_ok:
            return jsonify({"error": "social module unavailable"}), 503
        d          = request.get_json(force=True) or {}
        video_path = d.get("video_path", "")
        platforms  = d.get("platforms", ["tiktok"])
        Thread(target=lambda: _run_async(run_social_pipeline(video_path, platforms)),
               daemon=True).start()
        return jsonify({"ok": True})

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
            get_story, list_stories,
        )
        _story_ok = True
    except Exception:
        _story_ok = False

    @app.route("/api/story/bible/chat", methods=["POST"])
    @_login_required
    def api_bible_chat():
        if not _story_ok:
            return jsonify({"error": "story module unavailable"}), 503
        d = request.get_json(force=True) or {}
        try:
            result = _run_async(bible_chat(d.get("message",""), d.get("bible",{}),
                                           writer=d.get("writer","twin")))
            return jsonify({"response": result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/story/bible/complete", methods=["POST"])
    @_login_required
    def api_bible_complete():
        if not _story_ok:
            return jsonify({"error": "story module unavailable"}), 503
        d = request.get_json(force=True) or {}
        try:
            result = _run_async(complete_bible(d.get("partial",{}), d.get("idea",""),
                                               writer=d.get("writer","twin")))
            out = result.__dict__ if hasattr(result,"__dict__") else result
            return jsonify(out)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/story/generate", methods=["POST"])
    @_login_required
    def api_story_generate():
        if not _story_ok:
            return jsonify({"error": "story module unavailable"}), 503
        d  = request.get_json(force=True) or {}
        q: Q.Queue = Q.Queue()

        async def _gen():
            try:
                result = await generate_full_story(d.get("bible",{}), d.get("length","short"),
                                                   writer=d.get("writer","twin"))
                out = result.__dict__ if hasattr(result,"__dict__") else result
                q.put(("ok", json.dumps(out)))
            except Exception as e:
                q.put(("err", str(e)))
            q.put(None)

        Thread(target=lambda: _run_async(_gen()), daemon=True).start()

        def gen():
            while True:
                item = q.get()
                if item is None:
                    break
                kind, val = item
                yield _chunk_evt(val) if kind == "ok" else _err_evt(val)
            yield _done_evt()

        return _sse_response(gen)

    @app.route("/api/story/list")
    @_login_required
    def api_story_list():
        if not _story_ok:
            return jsonify([])
        return jsonify([s.__dict__ if hasattr(s,"__dict__") else s for s in list_stories()])

    @app.route("/api/story/<story_id>")
    @_login_required
    def api_story_get(story_id):
        if not _story_ok:
            abort(404)
        s = get_story(story_id)
        if not s:
            abort(404)
        return jsonify(s.__dict__ if hasattr(s,"__dict__") else s)

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
        fname = f"{uuid.uuid4().hex[:12]}{Path(f.filename).suffix.lower()}"
        dest  = _UPLOADS_DIR / fname
        f.save(str(dest))
        return jsonify({"ok": True, "path": str(dest), "url": f"/uploads/{fname}", "name": f.filename})

    @app.route("/api/lab/music")
    @_login_required
    def api_lab_music():
        music_dir = _STATIC / "music"
        if not music_dir.exists():
            return jsonify([])
        return jsonify([{"name": f.name, "url": f"/static/music/{f.name}"}
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
            {"role": "system", "content":
             "You generate short-form video storyboards as JSON. "
             "Return ONLY a JSON object with a 'scenes' array. "
             "Each scene: {scene_num, title, visual_prompt, voiceover, duration_s}. 4-6 scenes."},
            {"role": "user", "content": f"Create a storyboard for: {concept}"},
        ]
        try:
            raw    = _run_async(_ct("twin", msgs))
            m      = re.search(r"\{.*\}", raw, re.DOTALL)
            parsed = json.loads(m.group(0)) if m else {"scenes": []}
            return jsonify(parsed)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lab/image", methods=["POST"])
    @_login_required
    def api_lab_image():
        from part4_video import fal_generate_image
        d = request.get_json(force=True) or {}
        try:
            result = _run_async(fal_generate_image(d.get("prompt",""), d.get("style",""),
                                                    d.get("reference_frame")))
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
        script = d.get("script", "")
        if not script:
            return jsonify({"error": "no script"}), 400
        fname = f"vo_{uuid.uuid4().hex[:8]}.mp3"
        out   = _AUDIO_DIR / fname
        try:
            from gtts import gTTS
            gTTS(text=script[:3000], lang="en").save(str(out))
            return jsonify({"url": f"/audio/{fname}"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lab/render", methods=["POST"])
    @_login_required
    def api_lab_render():
        from part4_video import images_to_slideshow, concatenate_clips, mix_audio_onto_video
        import shutil
        d      = request.get_json(force=True) or {}
        images = d.get("images", [])
        clips  = d.get("clips",  [])
        audio  = d.get("audio")
        music  = d.get("music")
        fname  = f"render_{uuid.uuid4().hex[:8]}.mp4"
        out    = str(_RENDERS_DIR / fname)
        q: Q.Queue = Q.Queue()

        def _bg():
            try:
                if images:
                    q.put(("ok", "Building slideshow...\n"))
                    tmp = str(_RENDERS_DIR / f"slide_{uuid.uuid4().hex[:6]}.mp4")
                    ok, msg = images_to_slideshow(images, tmp)
                    if not ok:
                        q.put(("err", f"Slideshow: {msg}")); q.put(None); return
                    video = tmp
                elif clips:
                    q.put(("ok", "Concatenating clips...\n"))
                    ok, msg = concatenate_clips(clips, out)
                    if not ok:
                        q.put(("err", f"Concat: {msg}")); q.put(None); return
                    video = out
                else:
                    q.put(("err", "No images or clips.")); q.put(None); return

                if audio or music:
                    q.put(("ok", "Mixing audio...\n"))
                    ok, msg = mix_audio_onto_video(
                        video, audio or music, out,
                        music_path=(music if audio else None), music_vol=0.25,
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

    # ── bots ──────────────────────────────────────────────────────────────────
    _bots: dict[str, dict] = {}

    @app.route("/api/bots/spawn", methods=["POST"])
    @_login_required
    def api_bots_spawn():
        import subprocess
        d      = request.get_json(force=True) or {}
        script = d.get("script", "")
        name   = d.get("name", f"bot_{uuid.uuid4().hex[:6]}")
        if not script or not Path(script).exists():
            return jsonify({"error": f"script not found: {script}"}), 400
        try:
            proc = subprocess.Popen(["python3", script],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    cwd=str(_ROOT), text=True)
            _bots[name] = {"name": name, "pid": proc.pid, "proc": proc, "script": script}
            return jsonify({"ok": True, "id": name, "pid": proc.pid})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/bots/list")
    @_login_required
    def api_bots_list():
        return jsonify([{"id": k, "name": v["name"], "pid": v["pid"], "script": v["script"]}
                        for k, v in _bots.items()])

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
        lines = int(request.args.get("lines", 50))
        try:
            out  = []
            proc = b["proc"]
            while len(out) < lines:
                r, _, _ = select.select([proc.stdout], [], [], 0)
                if not r: break
                line = proc.stdout.readline()
                if not line: break
                out.append(line)
            return jsonify({"output": "".join(out)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── deploy ────────────────────────────────────────────────────────────────
    @app.route("/api/deploy/web", methods=["POST"])
    @_login_required
    def api_deploy_web():
        return jsonify({"ok": True, "message": "Use the Replit Publish button to deploy."})
