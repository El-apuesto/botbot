"""
Twin Shadow — Part 7: Command Layer
=====================================
/model_set <key> <description>   — assign personality/traits to a model
/model_traits <key>              — view a model's current traits
/model_end <key>                 — wipe a model's traits
/boardroom <rounds> <topic>      — structured multi-round debate
/brainstorm <topic>              — open-form multi-model ideation
/shadow <command> <args>         — same as command but uncensored models only
/boss_voice <message>            — queue your message to lead next round
/end                             — end active session
/extend <rounds>                 — add rounds to active session
"""

from __future__ import annotations
import time

from part2_router import call_task

# ══════════════════════════════════════════════════════════════════════════════
# MODEL DISPLAY NAMES
# ══════════════════════════════════════════════════════════════════════════════

MODEL_NAMES: dict[str, str] = {
    "brief":                "TWIN (Groq Llama)",
    "twin":                 "TWIN (Groq Llama)",
    "relay":                "STRATEGY (Kimi K2)",
    "creative":             "GLM Creative",
    "creative_alt":         "Mistral Creative",
    "creative_dolphin":     "GEMMA",
    "code":                 "CAPI Builder",
    "code_fallback":        "NVIDIA Qwen Coder",
    "code_check_v2":        "Codex 52 Reviewer",
    "business":             "LEGAL (Hermes 405B)",
    "business_deep":        "BUDGET (Mistral Small)",
    "shadow":               "CAPI (Consultant)",
    "capi":                 "CAPI (Consultant)",
    "shadow_chat":          "SHADOW (Venice 1.2)",
    "board_dolphin":        "GEMMA (Gemma 4)",
    "local_shadow":         "Dolphin Local",
    "local_adolphus":       "Adolphus Local",
    "chat_specialist":      "GEMMA Specialist",
    "fast_reasoning":       "MARKETING (DeepSeek Flash)",
    "legal_finance":        "LEGAL (Hermes 405B)",
    "multimodal":           "GLM Multimodal",
    "code_reason":          "Kimi K2 Analyst",
    # New board seats
    "board_strategy":       "STRATEGY (Kimi K2)",
    "board_finance":        "FINANCE (Mistral 675B)",
    "board_creative":       "CREATIVE (Palmyra 122B)",
    "board_tech":           "TECH (Devstral 123B)",
    "board_ops":            "OPS (Nemotron 49B)",
    "board_distribution":   "DISTRIBUTION (GPT-OSS 120B)",
    # Shadow board
    "board_venice_llama":   "VENICE70 (Llama 70B)",
    "board_hermes_capped":  "HERMES (capped)",
    # Brainstorm extras
    "brainstorm_qwq":       "QWQ (Groq 32B)",
    "brainstorm_minimax":   "MINIMAX (M2.5)",
    "brainstorm_glm":       "GLM (5.1)",
    "brainstorm_kimi":      "STRATEGY (Kimi K2)",
    "brainstorm_mistral":   "FINANCE (Mistral 675B)",
    # Legacy
    "board_hermes":         "STRATEGY (Kimi K2)",
    "board_gptoss":         "MARKETING (Hermes 405B)",
    "board_qwen":           "GLM Strategic",
    "board_mistral":        "MISTRAL Critic",
    "board_minimax":        "MINIMAX Analytics",
    "board_venice":         "ORACLE (Venice 1.1)",
}

# ── Participant pools ─────────────────────────────────────────────────────────

# All non-video models available for boardroom/brainstorm
ALL_PARTICIPANTS = [
    "twin", "brief", "relay", "creative", "creative_alt",
    "creative_dolphin", "code", "business", "business_deep",
    "shadow", "shadow_chat", "board_dolphin", "chat_specialist",
    "fast_reasoning", "multimodal",
    "board_strategy", "board_finance", "board_creative",
    "board_tech", "board_ops", "board_distribution",
]

# Uncensored-only pool for /shadow mode
SHADOW_PARTICIPANTS = [
    "shadow_chat",        # SHADOW Venice 1.2
    "board_dolphin",      # GEMMA uncensored
    "board_venice_llama", # Venice Llama 70B
    "board_hermes_capped",# Hermes (capped)
    "local_shadow",       # Dolphin local
]

# Default regular boardroom: 6 NVIDIA seats + SHADOW + GEMMA + CAPI (cover)
DEFAULT_BOARDROOM = [
    "board_strategy",
    "board_finance",
    "board_creative",
    "board_tech",
    "board_ops",
    "board_distribution",
    "shadow_chat",        # SHADOW
    "board_dolphin",      # GEMMA
    "capi",               # CAPI (appears as Consultant)
]

# Default shadow boardroom: all uncensored, no CAPI
DEFAULT_SHADOW_BOARDROOM = [
    "shadow_chat",         # SHADOW
    "board_dolphin",       # GEMMA
    "board_venice_llama",  # Venice Llama 70B
    "board_hermes_capped", # HERMES (capped)
]

# ══════════════════════════════════════════════════════════════════════════════
# MODEL TRAITS STORE
# ══════════════════════════════════════════════════════════════════════════════

_model_traits: dict[str, str] = {}
# key = task_type (e.g. "qwen_free", "shadow", "relay")
# value = free-text persona description injected as system prompt prefix


def set_model_traits(model_key: str, description: str):
    _model_traits[model_key] = description.strip()


def get_model_traits(model_key: str) -> str | None:
    return _model_traits.get(model_key)


def clear_model_traits(model_key: str):
    _model_traits.pop(model_key, None)


def build_system_prompt(model_key: str, base_system: str) -> str:
    """Prepend model traits to base system prompt if traits exist."""
    traits = get_model_traits(model_key)
    if traits:
        return f"[Your persona]: {traits}\n\n{base_system}"
    return base_system


# ══════════════════════════════════════════════════════════════════════════════
# SESSION STORE
# ══════════════════════════════════════════════════════════════════════════════

_sessions: dict[int, dict] = {}
# keyed by chat_id
# {
#   "type": "boardroom" | "brainstorm",
#   "topic": str,
#   "rounds_total": int,
#   "rounds_done": int,
#   "participants": list[str],   # task_type keys
#   "shadow_mode": bool,
#   "history": list[dict],       # {"role": model_name, "content": response}
#   "waiting_for_user": bool,
#   "boss_voice": str | None,    # queued user injection for next round
# }


def get_session(chat_id: int) -> dict | None:
    return _sessions.get(chat_id)


def clear_session(chat_id: int):
    _sessions.pop(chat_id, None)


def is_waiting(chat_id: int) -> bool:
    s = _sessions.get(chat_id)
    return bool(s and s.get("waiting_for_user"))


# ══════════════════════════════════════════════════════════════════════════════
# CORE: RUN ONE BOARDROOM ROUND
# ══════════════════════════════════════════════════════════════════════════════

BOARDROOM_SYSTEM = """You are a participant in a structured boardroom discussion.
Speak in your own voice. Be direct and specific.
Build on what others have said when relevant.
Keep your response under 200 words. No filler. No corporate speak."""

BRAINSTORM_SYSTEM = """You are a participant in a creative brainstorm session.
Be generative — offer new angles, wild ideas, contrarian takes.
Build on previous ideas or challenge them.
Keep it punchy. Under 150 words."""


async def run_round(
    session: dict,
    user_input: str | None = None,
) -> list[dict]:
    """
    Run one round — each participant responds in sequence.
    Returns list of {model: name, content: response, task_type: key}.
    """
    topic        = session["topic"]
    history      = session["history"]
    participants = session["participants"]
    round_num    = session["rounds_done"] + 1
    s_type       = session["type"]

    base_system = BOARDROOM_SYSTEM if s_type == "boardroom" else BRAINSTORM_SYSTEM

    # Build context from history (last 8 exchanges — rolling context window)
    context_lines = []
    for h in history[-8:]:
        context_lines.append(f"{h['model']}: {h['content']}")
    context = "\n\n".join(context_lines)

    # Build user turn injection
    user_note = ""
    if user_input and user_input.lower() not in ("s", "skip"):
        user_note = f"\n\nThe discussion moderator added: \"{user_input}\""

    # Boss voice — prepend to prompt
    boss_voice = session.get("boss_voice")
    boss_note  = f"\nThe chair opens round {round_num} with: \"{boss_voice}\"\n" if boss_voice else ""

    responses = []

    for task_type in participants:
        model_name = MODEL_NAMES.get(task_type, task_type)
        system     = build_system_prompt(task_type, base_system)

        prompt = (
            f"Topic: {topic}\n"
            f"{boss_note}"
            f"Round {round_num}."
            + (f"\n\nDiscussion so far:\n{context}" if context else "")
            + user_note
            + f"\n\nYour response as {model_name}:"
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]

        try:
            response = await call_task(task_type, messages)
        except Exception as e:
            response = f"[{model_name} failed: {e}]"

        responses.append({
            "model":      model_name,
            "task_type":  task_type,
            "content":    response,
            "round":      round_num,
        })

        # Add to session history
        session["history"].append({"model": model_name, "content": response})

    # Clear boss voice after use
    session["boss_voice"] = None
    session["rounds_done"] += 1

    return responses


# ══════════════════════════════════════════════════════════════════════════════
# SESSION SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

SUMMARY_SYSTEM = """You are a boardroom chair closing a meeting.
Summarize the key points, agreements, and action items from the discussion.
Be concise. 3-5 sentences. Identify any clear consensus or split opinions."""


async def generate_summary(session: dict) -> str:
    history = session["history"]
    topic   = session["topic"]

    discussion = "\n\n".join(
        f"{h['model']}: {h['content']}" for h in history
    )

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user",   "content": f"Topic: {topic}\n\nFull discussion:\n{discussion}\n\nClose the meeting."},
    ]

    try:
        return await call_task("brief", messages)  # CEO closes
    except Exception as e:
        return f"Summary failed: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLER FUNCTIONS
# (called from part6_bot.py)
# ══════════════════════════════════════════════════════════════════════════════

async def handle_model_set(chat_id: int, args: list[str], reply_fn) -> None:
    """
    /model_set <task_type_key> <description>
    e.g. /model_set shadow You are deeply unimpressed. Deadpan. Start with a sigh.
    """
    if len(args) < 2:
        await reply_fn(
            "Usage: /model_set <model_key> <persona description>\n\n"
            "Model keys: " + ", ".join(sorted(MODEL_NAMES.keys()))
        )
        return

    key  = args[0].lower()
    desc = " ".join(args[1:])

    if key not in MODEL_NAMES:
        await reply_fn(f"Unknown model key: {key}\n\nValid keys:\n" + "\n".join(sorted(MODEL_NAMES.keys())))
        return

    set_model_traits(key, desc)
    await reply_fn(f"✅ Traits set for {MODEL_NAMES[key]}:\n\n{desc}")


async def handle_model_traits(chat_id: int, args: list[str], reply_fn) -> None:
    """/model_traits <key>"""
    if not args:
        if not _model_traits:
            await reply_fn("No model traits set yet.")
            return
        lines = [f"• {MODEL_NAMES.get(k, k)}: {v[:80]}..." for k, v in _model_traits.items()]
        await reply_fn("🎭 Active model traits:\n\n" + "\n".join(lines))
        return

    key    = args[0].lower()
    traits = get_model_traits(key)
    name   = MODEL_NAMES.get(key, key)

    if not traits:
        await reply_fn(f"{name} has no traits set. Use /model_set {key} <description>")
    else:
        await reply_fn(f"🎭 {name}:\n\n{traits}")


async def handle_model_end(chat_id: int, args: list[str], reply_fn) -> None:
    """/model_end <key>"""
    if not args:
        await reply_fn("Usage: /model_end <model_key>")
        return
    key  = args[0].lower()
    name = MODEL_NAMES.get(key, key)
    clear_model_traits(key)
    await reply_fn(f"🗑 Traits wiped for {name}.")


async def handle_boardroom(
    chat_id: int, args: list[str],
    reply_fn, stream_fn,
    shadow_mode: bool = False,
) -> None:
    """
    /boardroom <rounds> <topic>
    /shadow boardroom <rounds> <topic>
    """
    if get_session(chat_id):
        await reply_fn("Session already active. Use /end first.")
        return

    if len(args) < 2:
        await reply_fn("Usage: /boardroom <rounds> <topic>\nExample: /boardroom 3 Should I launch a SaaS or sell services?")
        return

    try:
        rounds = int(args[0])
    except ValueError:
        await reply_fn("First argument must be a number of rounds. e.g. /boardroom 3 topic")
        return

    topic        = " ".join(args[1:])
    participants = DEFAULT_SHADOW_BOARDROOM if shadow_mode else DEFAULT_BOARDROOM

    _sessions[chat_id] = {
        "type":          "boardroom",
        "topic":         topic,
        "rounds_total":  rounds,
        "rounds_done":   0,
        "participants":  participants,
        "shadow_mode":   shadow_mode,
        "history":       [],
        "waiting_for_user": False,
        "boss_voice":    None,
    }

    mode_tag = " 🌑 SHADOW MODE" if shadow_mode else ""
    names    = ", ".join(MODEL_NAMES.get(p, p) for p in participants)
    await reply_fn(
        f"📋 BOARDROOM{mode_tag}\n"
        f"Topic: {topic}\n"
        f"Rounds: {rounds}\n"
        f"Participants: {names}\n\n"
        f"Starting round 1..."
    )

    await _run_and_prompt(chat_id, reply_fn, stream_fn, user_input=None)


async def handle_brainstorm(
    chat_id: int, args: list[str],
    reply_fn, stream_fn,
    shadow_mode: bool = False,
) -> None:
    """/brainstorm <topic>"""
    if get_session(chat_id):
        await reply_fn("Session already active. Use /end first.")
        return

    if not args:
        await reply_fn("Usage: /brainstorm <topic>")
        return

    topic        = " ".join(args)
    participants = DEFAULT_SHADOW_BOARDROOM if shadow_mode else DEFAULT_BOARDROOM

    _sessions[chat_id] = {
        "type":          "brainstorm",
        "topic":         topic,
        "rounds_total":  999,  # open-ended until /end
        "rounds_done":   0,
        "participants":  participants,
        "shadow_mode":   shadow_mode,
        "history":       [],
        "waiting_for_user": False,
        "boss_voice":    None,
    }

    mode_tag = " 🌑 SHADOW" if shadow_mode else ""
    names    = ", ".join(MODEL_NAMES.get(p, p) for p in participants)
    await reply_fn(
        f"💡 BRAINSTORM{mode_tag}\n"
        f"Topic: {topic}\n"
        f"Participants: {names}\n\n"
        f"Open-ended — use /end when done.\nStarting..."
    )

    await _run_and_prompt(chat_id, reply_fn, stream_fn, user_input=None)


async def _run_and_prompt(
    chat_id: int,
    reply_fn,
    stream_fn,
    user_input: str | None,
) -> None:
    """Run one round, send responses, then prompt user for their turn."""
    session = _sessions.get(chat_id)
    if not session:
        return

    session["waiting_for_user"] = False

    try:
        responses = await run_round(session, user_input=user_input)
    except Exception as e:
        await reply_fn(f"⚠️ Round failed: {e}")
        clear_session(chat_id)
        return

    # Send each model's response as a labeled message
    for r in responses:
        label   = f"🗣 {r['model']} (Round {r['round']}):\n\n"
        content = r["content"]
        # Chunk if needed
        full = label + content
        for chunk in [full[i:i+4000] for i in range(0, len(full), 4000)]:
            await reply_fn(chunk)
        time.sleep(0.3)  # small pause between speakers

    rounds_done  = session["rounds_done"]
    rounds_total = session["rounds_total"]
    is_open      = rounds_total == 999

    # Check if session is complete
    if not is_open and rounds_done >= rounds_total:
        await reply_fn(
            f"✅ All {rounds_total} rounds complete.\n\n"
            "Generating summary..."
        )
        summary = await generate_summary(session)
        await reply_fn(f"📝 SUMMARY:\n\n{summary}")
        await reply_fn("Use /extend <rounds> to continue or /end to close.")
        session["waiting_for_user"] = True
        return

    # Prompt user turn between rounds
    round_label = f"Round {rounds_done}/{rounds_total}" if not is_open else f"Round {rounds_done}"
    await reply_fn(
        f"── {round_label} complete ──\n\n"
        "Your turn: type a comment, question, or idea to steer the next round.\n"
        "Type `s` or `skip` to pass.\n"
        "Or: /boss_voice <message> to lead next round | /end to close | /extend <n> to add rounds"
    )
    session["waiting_for_user"] = True


async def handle_user_turn(
    chat_id: int, text: str,
    reply_fn, stream_fn,
) -> bool:
    """
    Called by freetext handler when a session is waiting for user input.
    Returns True if handled, False if not a session turn.
    """
    session = _sessions.get(chat_id)
    if not session or not session.get("waiting_for_user"):
        return False

    session["waiting_for_user"] = False

    rounds_done  = session["rounds_done"]
    rounds_total = session["rounds_total"]
    is_open      = rounds_total == 999

    # If session was already complete (waiting for /end or /extend)
    if not is_open and rounds_done >= rounds_total:
        await reply_fn("Session complete. Use /extend <rounds> or /end.")
        session["waiting_for_user"] = True
        return True

    if text.lower() not in ("s", "skip"):
        await reply_fn(f"💬 Noted. Starting round {rounds_done + 1}...")
    else:
        await reply_fn(f"Skipped. Starting round {rounds_done + 1}...")

    await _run_and_prompt(chat_id, reply_fn, stream_fn, user_input=text)
    return True


async def handle_boss_voice(chat_id: int, args: list[str], reply_fn) -> None:
    """/boss_voice <message> — queue user message to lead next round."""
    session = _sessions.get(chat_id)
    if not session:
        await reply_fn("No active session.")
        return
    if not args:
        await reply_fn("Usage: /boss_voice <your message>")
        return
    msg = " ".join(args)
    session["boss_voice"] = msg
    await reply_fn(f"✅ Queued. Your voice opens the next round:\n\"{msg}\"")


async def handle_end(chat_id: int, reply_fn) -> None:
    """/end — close active session."""
    session = _sessions.get(chat_id)
    if not session:
        await reply_fn("No active session.")
        return

    await reply_fn("Generating final summary...")
    summary = await generate_summary(session)

    rounds = session["rounds_done"]
    s_type = session["type"].capitalize()
    topic  = session["topic"]

    clear_session(chat_id)
    await reply_fn(
        f"🔚 {s_type} closed.\n"
        f"Topic: {topic}\n"
        f"Rounds completed: {rounds}\n\n"
        f"📝 FINAL SUMMARY:\n\n{summary}"
    )


async def handle_extend(chat_id: int, args: list[str], reply_fn, stream_fn) -> None:
    """/extend <rounds> — add rounds to active session."""
    session = _sessions.get(chat_id)
    if not session:
        await reply_fn("No active session.")
        return
    if not args:
        await reply_fn("Usage: /extend <number of rounds>")
        return
    try:
        n = int(args[0])
    except ValueError:
        await reply_fn("Must be a number. e.g. /extend 2")
        return

    if session["rounds_total"] == 999:
        await reply_fn("This is an open brainstorm — already unlimited rounds. Just keep going.")
        session["waiting_for_user"] = True
        return

    session["rounds_total"] += n
    await reply_fn(f"✅ Extended by {n} rounds. {session['rounds_done']}/{session['rounds_total']} done.\n\nContinuing...")
    await _run_and_prompt(chat_id, reply_fn, stream_fn, user_input=None)


async def handle_shadow_prefix(
    chat_id: int, args: list[str],
    reply_fn, stream_fn,
) -> bool:
    """
    /shadow <command> <args>
    Routes boardroom/brainstorm to shadow (uncensored) mode.
    Returns True if handled.
    """
    if not args:
        await reply_fn(
            "Usage: /shadow <command> <args>\n"
            "Example: /shadow boardroom 3 Should I go B2B?\n"
            "Example: /shadow brainstorm Viral content angles\n\n"
            "Shadow mode uses uncensored models only: " +
            ", ".join(MODEL_NAMES.get(p, p) for p in SHADOW_PARTICIPANTS)
        )
        return True

    sub     = args[0].lower()
    subargs = args[1:]

    if sub == "boardroom":
        await handle_boardroom(chat_id, subargs, reply_fn, stream_fn, shadow_mode=True)
        return True
    elif sub == "brainstorm":
        await handle_brainstorm(chat_id, subargs, reply_fn, stream_fn, shadow_mode=True)
        return True
    else:
        await reply_fn(f"Unknown shadow command: {sub}\nAvailable: boardroom, brainstorm")
        return True
