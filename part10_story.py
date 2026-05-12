"""
Twin Shadow -- Part 10: Story & Biography Generation
=====================================================
Replaces the standalone story_generation.py.
Wired to Twin Shadow's routing layer (part2_router) so all
provider fallback, key rotation, and model selection is automatic.

Features:
- Conversational story bible builder
- Chapter-by-chapter iterative generation
- Rolling 512-word context window
- Story bible injected into every chapter for long-form cohesion
- Vault storage after each chapter
- Works with any provider in the registry (Grok, Ollama, NVIDIA, etc.)
"""

from __future__ import annotations
import json
import asyncio
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

from part2_router import call_task, call_task_with_fallback

log = logging.getLogger("tsai.story")


# ---------------------------------------------------------------------------
# STORY BIBLE
# ---------------------------------------------------------------------------

BIBLE_SYSTEM = """You are Twin Shadow's story architect.
Your job is to help users build a complete story bible through natural conversation.
Ask one question at a time. Be direct and sharp - no fluff.
When you have enough to work with, output the bible as JSON.

Bible fields to collect:
- title (can be generated)
- genre
- tone
- logline (one sentence)
- characters (name, role, one-line description each)
- setting (time, place, atmosphere)
- central_conflict
- themes (2-3 max)
- unresolved_threads (things that must pay off)
- rules (world rules, character rules, anything that must stay consistent)

When the user says they're done or you have all critical fields,
output ONLY a JSON object with key "bible" containing all fields.
Never output the JSON until explicitly asked or user says done."""


BIBLE_FILL_SYSTEM = """You are Twin Shadow's story architect.
Given a partial story bible, fill in any missing fields creatively
so they fit perfectly with what's already established.
Be specific. No placeholders. No vague answers.
Return ONLY a complete JSON object with key "bible"."""


@dataclass
class StoryBible:
    title: str = ""
    genre: str = ""
    tone: str = ""
    logline: str = ""
    characters: list[dict] = field(default_factory=list)
    setting: str = ""
    central_conflict: str = ""
    themes: list[str] = field(default_factory=list)
    unresolved_threads: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """Format the bible as a system prompt block injected into every chapter."""
        chars = "\n".join(
            f"  - {c.get('name', '?')} ({c.get('role', '?')}): {c.get('description', '')}"
            for c in self.characters
        ) or "  (none defined)"

        threads = "\n".join(f"  - {t}" for t in self.unresolved_threads) or "  (none)"
        rules   = "\n".join(f"  - {r}" for r in self.rules) or "  (none)"
        themes  = ", ".join(self.themes) or "(none)"

        return f"""=== STORY BIBLE (MUST FOLLOW) ===
Title: {self.title}
Genre: {self.genre}
Tone: {self.tone}
Logline: {self.logline}
Setting: {self.setting}
Central Conflict: {self.central_conflict}
Themes: {themes}

Characters:
{chars}

Unresolved Threads (must pay off eventually):
{threads}

World/Character Rules (never break these):
{rules}
=== END BIBLE ==="""

    def is_ready(self) -> bool:
        return bool(self.genre and self.tone and self.logline and self.central_conflict)

    @classmethod
    def from_dict(cls, d: dict) -> "StoryBible":
        return cls(
            title=d.get("title", ""),
            genre=d.get("genre", ""),
            tone=d.get("tone", ""),
            logline=d.get("logline", ""),
            characters=d.get("characters", []),
            setting=d.get("setting", ""),
            central_conflict=d.get("central_conflict", ""),
            themes=d.get("themes", []),
            unresolved_threads=d.get("unresolved_threads", []),
            rules=d.get("rules", []),
        )


# ---------------------------------------------------------------------------
# BIBLE CONVERSATION
# ---------------------------------------------------------------------------

async def bible_chat(
    user_message: str,
    history: list[dict],
    partial_bible: dict | None = None,
) -> tuple[str, dict | None]:
    """
    One turn of the story bible conversation.

    Returns:
        (response_text, completed_bible_dict_or_None)
    """
    messages = [{"role": "system", "content": BIBLE_SYSTEM}]

    if partial_bible:
        messages.append({
            "role": "system",
            "content": f"Current partial bible: {json.dumps(partial_bible)}"
        })

    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        raw = await call_task("creative", messages)
    except Exception:
        raw, _ = await call_task_with_fallback("creative", messages, "shadow_chat")

    # Check if model returned a completed bible
    bible_dict = None
    if '{"bible"' in raw or '"bible":' in raw:
        try:
            clean = raw.strip()
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            parsed = json.loads(clean)
            if "bible" in parsed:
                bible_dict = parsed["bible"]
        except (json.JSONDecodeError, ValueError):
            pass

    return raw, bible_dict


async def complete_bible(partial: dict, original_idea: str) -> StoryBible:
    """
    Fill in any missing bible fields automatically so the story can proceed.
    """
    messages = [
        {"role": "system", "content": BIBLE_FILL_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Original idea: {original_idea}\n\n"
                f"Partial bible: {json.dumps(partial)}\n\n"
                "Fill in all missing fields creatively. Return ONLY JSON with key 'bible'."
            )
        }
    ]

    try:
        raw = await call_task("creative", messages)
        clean = raw.strip()
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean)
        bible_data = parsed.get("bible", parsed)
        # Merge with partial so nothing gets lost
        merged = {**partial, **bible_data}
        return StoryBible.from_dict(merged)
    except Exception as e:
        log.error("[STORY] bible completion failed: %s", e)
        return StoryBible.from_dict(partial)


# ---------------------------------------------------------------------------
# CHAPTER OUTLINE
# ---------------------------------------------------------------------------

LENGTH_CONFIG = {
    "short":   {"words": 3000,   "chapters": 1,  "words_per_chapter": 3000},
    "medium":  {"words": 8000,   "chapters": 2,  "words_per_chapter": 4000},
    "long":    {"words": 15000,  "chapters": 3,  "words_per_chapter": 5000},
    "novella": {"words": 45000,  "chapters": 6,  "words_per_chapter": 7500},
    "novel":   {"words": 90000,  "chapters": 12, "words_per_chapter": 7500},
    "epic":    {"words": 140000, "chapters": 18, "words_per_chapter": 7800},
}

OUTLINE_SYSTEM = """You are a story structure expert.
Respond with ONLY valid JSON - no markdown, no explanation."""


async def generate_outline(bible: StoryBible, length: str) -> list[dict]:
    """Generate chapter titles and summaries from the story bible."""
    cfg = LENGTH_CONFIG.get(length, LENGTH_CONFIG["short"])
    num_chapters = cfg["chapters"]
    total_words  = cfg["words"]

    prompt = (
        f"Create a {num_chapters}-chapter outline for this story.\n\n"
        f"{bible.to_prompt_block()}\n\n"
        f"Target total: {total_words} words.\n\n"
        "Return a JSON array:\n"
        '[{"title": "...", "summary": "2-3 sentences of what happens"}, ...]\n\n'
        "ONLY the JSON array."
    )

    messages = [
        {"role": "system", "content": OUTLINE_SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    try:
        raw = await call_task("creative", messages)
        clean = raw.strip()
        if "```" in clean:
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1])
        outline = json.loads(clean)
        if len(outline) != num_chapters:
            log.warning("[STORY] outline has %d chapters, expected %d", len(outline), num_chapters)
        return outline
    except Exception as e:
        log.error("[STORY] outline generation failed: %s", e)
        return [{"title": f"Chapter {i+1}", "summary": ""} for i in range(num_chapters)]


# ---------------------------------------------------------------------------
# CHAPTER GENERATION
# ---------------------------------------------------------------------------

CHAPTER_SYSTEM_BASE = """You are fantm.ink, Twin Shadow's master storyteller.

Your voice:
- Sardonic but never cruel
- Sharp observations on human absurdity
- Master of the unexpected twist
- Gallows humor done right
- Eloquent yet conversational
- Occasionally breaks the fourth wall

Write ONLY story content. No preamble. No meta-commentary.
Never contradict the story bible. Never break established rules."""


async def generate_chapter(
    chapter_title: str,
    chapter_summary: str,
    bible: StoryBible,
    context_summary: str = "",
    target_word_count: int = 4000,
    on_progress=None,
) -> str:
    """
    Generate a single chapter iteratively until target word count is reached.
    Bible is injected into every generation call for cohesion.

    Args:
        on_progress: optional async callable(words_done, words_target) for streaming progress
    """
    bible_block    = bible.to_prompt_block()
    system_prompt  = f"{CHAPTER_SYSTEM_BASE}\n\n{bible_block}"
    generated_text = ""
    total_words    = 0

    initial_prompt = (
        f"Chapter: {chapter_title}\n"
        f"Chapter goal: {chapter_summary}\n"
        + (f"Context from previous chapter (last 512 words):\n{context_summary}\n\n" if context_summary else "")
        + "Write the opening of this chapter now."
    )

    prompt = initial_prompt

    while total_words < target_word_count:
        remaining   = target_word_count - total_words
        max_tokens  = min(1500, int(remaining * 1.3))

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt},
        ]

        try:
            chunk, _ = await call_task_with_fallback("creative", messages, "creative_alt")
        except Exception as e:
            log.error("[STORY] chapter chunk failed: %s", e)
            break

        generated_text += chunk + "\n\n"
        total_words     = len(generated_text.split())

        if on_progress:
            try:
                await on_progress(total_words, target_word_count)
            except Exception:
                pass

        log.info("[STORY] chapter '%s': %d/%d words", chapter_title, total_words, target_word_count)

        # Rolling context window
        words       = generated_text.split()
        last_512    = " ".join(words[-512:]) if len(words) > 512 else generated_text
        prompt      = (
            f"Continue the story seamlessly.\n\n"
            f"Last written:\n{last_512}\n\n"
            f"Chapter goal: {chapter_summary}\n"
            f"Words written so far: {total_words}. Target: {target_word_count}."
        )

    return f"# {chapter_title}\n\n{generated_text.strip()}"


# ---------------------------------------------------------------------------
# TITLE GENERATION
# ---------------------------------------------------------------------------

async def generate_title(bible: StoryBible, preview: str) -> str:
    messages = [
        {"role": "system", "content": "You generate sharp, memorable story titles. Respond with ONLY the title - no quotes, no explanation."},
        {"role": "user",   "content": f"Genre: {bible.genre}\nTone: {bible.tone}\nLogline: {bible.logline}\nPreview: {preview[:400]}\n\nTitle:"},
    ]
    try:
        title = await call_task("creative", messages)
        return title.strip().strip('"').strip("'")[:100]
    except Exception:
        return f"{bible.genre.title()}: {bible.logline[:40]}"


# ---------------------------------------------------------------------------
# FULL STORY ORCHESTRATOR
# ---------------------------------------------------------------------------

@dataclass
class StoryResult:
    story_id: str
    title: str
    bible: StoryBible
    chapters: list[str]
    word_count: int
    status: str
    error: str = ""

    @property
    def full_text(self) -> str:
        return "\n\n---\n\n".join(self.chapters)


# In-memory store - replace with Supabase in production
_stories: dict[str, StoryResult] = {}
_story_counter = 0


def _next_story_id() -> str:
    global _story_counter
    _story_counter += 1
    return f"S{_story_counter:04d}"


async def generate_full_story(
    bible: StoryBible,
    length: str = "short",
    vault_fn=None,
    chat_id: int = 0,
    on_chapter_done=None,
) -> StoryResult:
    """
    Generate a complete multi-chapter story from a finished bible.

    Args:
        vault_fn:        optional async callable(chat_id, topic, content) to store in vault
        on_chapter_done: optional async callable(story_id, chapter_num, total) for progress
    """
    story_id = _next_story_id()
    cfg      = LENGTH_CONFIG.get(length, LENGTH_CONFIG["short"])

    result = StoryResult(
        story_id=story_id,
        title=bible.title or "Untitled",
        bible=bible,
        chapters=[],
        word_count=0,
        status="generating",
    )
    _stories[story_id] = result

    try:
        # Generate outline
        log.info("[STORY] generating outline for %s", story_id)
        outline = await generate_outline(bible, length)

        context_summary = ""
        words_per_chapter = cfg["words_per_chapter"]

        for i, chapter_info in enumerate(outline):
            chapter_num   = i + 1
            chapter_title = chapter_info.get("title", f"Chapter {chapter_num}")
            chapter_sum   = chapter_info.get("summary", "")

            log.info("[STORY] chapter %d/%d: %s", chapter_num, len(outline), chapter_title)

            chapter_text = await generate_chapter(
                chapter_title=chapter_title,
                chapter_summary=chapter_sum,
                bible=bible,
                context_summary=context_summary,
                target_word_count=words_per_chapter,
            )

            result.chapters.append(chapter_text)
            result.word_count = sum(len(c.split()) for c in result.chapters)

            # Rolling context
            words           = chapter_text.split()
            context_summary = " ".join(words[-512:]) if len(words) > 512 else chapter_text

            # Store chapter in vault
            if vault_fn and chat_id:
                try:
                    await vault_fn(
                        chat_id,
                        f"{result.title} - Ch.{chapter_num}",
                        chapter_text[:800],
                        project_id=story_id,
                    )
                except Exception as ve:
                    log.warning("[STORY] vault save failed: %s", ve)

            if on_chapter_done:
                try:
                    await on_chapter_done(story_id, chapter_num, len(outline))
                except Exception:
                    pass

        # Generate title if missing
        if not result.title or result.title == "Untitled":
            result.title = await generate_title(bible, result.chapters[0][:500])

        result.status = "completed"
        log.info("[STORY] completed %s - %d words", story_id, result.word_count)

    except Exception as e:
        result.status = "failed"
        result.error  = str(e)
        log.error("[STORY] generation failed: %s", e)

    return result


def get_story(story_id: str) -> StoryResult | None:
    return _stories.get(story_id)


def list_stories() -> list[StoryResult]:
    return list(_stories.values())
