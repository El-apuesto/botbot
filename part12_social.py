"""
Twin Shadow -- Part 11: Social Media Content Pipeline
======================================================
Full pipeline:
1. Upload audio or video
2. Rip audio from video via ffmpeg
3. Transcribe with Groq Whisper (raw, cleaned, or summarized)
4. Identify short content clips with timestamps
5. Cut clips with ffmpeg
6. Platform optimization (TikTok, Instagram, Facebook, YouTube)
7. Description, hashtags, YouTube title
8. Thumbnail: screenshot at timestamp + text overlay OR AI image prompt
9. Handoff clips to VIDEO LAB
"""

from __future__ import annotations
import os
import json
import asyncio
import logging
import subprocess
import tempfile
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from part2_router import call_task, call_task_with_fallback

log = logging.getLogger("tsai.social")

UPLOAD_DIR  = os.path.join(os.path.dirname(__file__), "uploads")
RENDERS_DIR = os.path.join(os.path.dirname(__file__), "renders")
os.makedirs(UPLOAD_DIR,  exist_ok=True)
os.makedirs(RENDERS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# DATA MODELS
# ---------------------------------------------------------------------------

@dataclass
class TranscriptSegment:
    start: float
    end:   float
    text:  str


@dataclass
class ClipSuggestion:
    start:       float
    end:         float
    reason:      str
    hook:        str        # first line / hook text
    score:       int        # 1-10
    clip_path:   str = ""   # set after ffmpeg cut


@dataclass
class PlatformContent:
    platform:    str
    title:       str = ""   # YouTube only
    description: str = ""
    hashtags:    list[str] = field(default_factory=list)
    optimized:   str = ""   # full optimized post text


@dataclass
class ThumbnailOption:
    type:         str        # "screenshot" or "ai_prompt"
    timestamp:    float = 0  # for screenshot
    image_path:   str = ""   # set after ffmpeg extraction
    text_overlay: str = ""
    ai_prompt:    str = ""   # for AI generation


@dataclass
class SocialResult:
    job_id:       str
    source_file:  str
    audio_path:   str = ""
    transcript:   list[TranscriptSegment] = field(default_factory=list)
    raw_text:     str = ""
    clean_text:   str = ""
    summary:      str = ""
    clips:        list[ClipSuggestion] = field(default_factory=list)
    platforms:    list[PlatformContent] = field(default_factory=list)
    thumbnails:   list[ThumbnailOption] = field(default_factory=list)
    status:       str = "pending"
    error:        str = ""
    created_at:   str = ""


_jobs: dict[str, SocialResult] = {}
_job_counter = 0


def _next_job_id() -> str:
    global _job_counter
    _job_counter += 1
    return f"SOC{_job_counter:04d}"


def get_job(job_id: str) -> SocialResult | None:
    return _jobs.get(job_id)


def list_jobs() -> list[SocialResult]:
    return list(_jobs.values())


# ---------------------------------------------------------------------------
# STEP 1+2: EXTRACT AUDIO FROM VIDEO
# ---------------------------------------------------------------------------

def is_video(path: str) -> bool:
    ext = Path(path).suffix.lower()
    return ext in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv"}


def rip_audio(video_path: str, output_dir: str) -> str:
    """Extract audio from video as mp3 using ffmpeg."""
    base     = Path(video_path).stem
    out_path = os.path.join(output_dir, f"{base}_audio.mp3")

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn",                    # no video
        "-acodec", "libmp3lame",
        "-ab", "128k",
        "-ar", "44100",
        out_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extract failed: {result.stderr[-500:]}")

    log.info("[SOCIAL] audio ripped: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# STEP 3: TRANSCRIBE WITH GROQ WHISPER
# ---------------------------------------------------------------------------

async def transcribe(audio_path: str) -> list[TranscriptSegment]:
    """Transcribe audio using Groq Whisper API with timestamps."""
    import httpx

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    log.info("[SOCIAL] transcribing: %s", audio_path)

    async with httpx.AsyncClient(timeout=300) as client:
        with open(audio_path, "rb") as f:
            response = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (Path(audio_path).name, f, "audio/mpeg")},
                data={
                    "model":            "whisper-large-v3",
                    "response_format":  "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
            )

    if response.status_code != 200:
        raise RuntimeError(f"Whisper API error {response.status_code}: {response.text[:500]}")

    data     = response.json()
    segments = []

    for seg in data.get("segments", []):
        segments.append(TranscriptSegment(
            start=float(seg.get("start", 0)),
            end=float(seg.get("end", 0)),
            text=seg.get("text", "").strip(),
        ))

    log.info("[SOCIAL] transcribed %d segments", len(segments))
    return segments


def segments_to_text(segments: list[TranscriptSegment]) -> str:
    return " ".join(s.text for s in segments).strip()


def segments_to_timestamped(segments: list[TranscriptSegment]) -> str:
    lines = []
    for s in segments:
        m, sec = divmod(int(s.start), 60)
        h, m   = divmod(m, 60)
        ts     = f"[{h:02d}:{m:02d}:{sec:02d}]" if h else f"[{m:02d}:{sec:02d}]"
        lines.append(f"{ts} {s.text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STEP 3b: OPTIONAL TRANSCRIPT PROCESSING
# ---------------------------------------------------------------------------

CLEAN_SYSTEM = """You clean up transcripts. Remove filler words (um, uh, like, you know, sort of, kind of, basically), 
fix run-on sentences, fix obvious transcription errors. Keep the speaker's voice intact. 
Return ONLY the cleaned transcript text."""

SUMMARY_SYSTEM = """You summarize spoken content concisely. 
Return a 3-5 sentence summary that captures the key points. No fluff."""


async def clean_transcript(raw: str) -> str:
    messages = [
        {"role": "system", "content": CLEAN_SYSTEM},
        {"role": "user",   "content": raw},
    ]
    try:
        return await call_task("brief", messages)
    except Exception:
        result, _ = await call_task_with_fallback("brief", messages, "shadow_chat")
        return result


async def summarize_transcript(raw: str) -> str:
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user",   "content": raw},
    ]
    try:
        return await call_task("brief", messages)
    except Exception:
        result, _ = await call_task_with_fallback("brief", messages, "shadow_chat")
        return result


# ---------------------------------------------------------------------------
# STEP 4: IDENTIFY SHORT CONTENT CLIPS
# ---------------------------------------------------------------------------

CLIP_SYSTEM = """You are a social media content strategist. 
Given a transcript with timestamps, identify the best short clips (30-90 seconds) for social media.

For each clip return:
- start: start time in seconds (float)
- end: end time in seconds (float)  
- reason: why this clip works for social media (1 sentence)
- hook: the opening line or hook of the clip (exact quote from transcript)
- score: 1-10 virality score

Return ONLY a JSON array. No markdown. No explanation.
[{"start": 0.0, "end": 45.0, "reason": "...", "hook": "...", "score": 8}, ...]

Prioritize: strong hooks, complete thoughts, emotional moments, surprising facts, 
clear takeaways, humor, controversy, relatable moments."""


async def identify_clips(
    timestamped_transcript: str,
    max_clips: int = 5,
) -> list[ClipSuggestion]:
    messages = [
        {"role": "system", "content": CLIP_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Find the top {max_clips} clips from this transcript:\n\n"
                f"{timestamped_transcript}\n\n"
                f"Return JSON array only."
            ),
        },
    ]

    try:
        raw = await call_task("brief", messages)
    except Exception:
        raw, _ = await call_task_with_fallback("brief", messages, "shadow_chat")

    try:
        clean = raw.strip()
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        data  = json.loads(clean)
        clips = []
        for c in data[:max_clips]:
            clips.append(ClipSuggestion(
                start=float(c.get("start", 0)),
                end=float(c.get("end", 60)),
                reason=c.get("reason", ""),
                hook=c.get("hook", ""),
                score=int(c.get("score", 5)),
            ))
        clips.sort(key=lambda x: x.score, reverse=True)
        return clips
    except Exception as e:
        log.error("[SOCIAL] clip parsing failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# STEP 5: CUT CLIPS WITH FFMPEG
# ---------------------------------------------------------------------------

def cut_clip(
    source_path: str,
    start: float,
    end:   float,
    output_path: str,
) -> str:
    """Cut a clip from source file using ffmpeg."""
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", source_path,
        "-t", str(duration),
        "-c:v", "copy",
        "-c:a", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"clip cut failed: {result.stderr[-300:]}")
    return output_path


def extract_screenshot(
    video_path: str,
    timestamp: float,
    output_path: str,
) -> str:
    """Extract a frame from video at given timestamp."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"screenshot failed: {result.stderr[-300:]}")
    return output_path


def add_text_overlay(
    image_path: str,
    text: str,
    output_path: str,
) -> str:
    """Add text overlay to image using ffmpeg."""
    safe_text = text.replace("'", "\\'").replace(":", "\\:")[:80]
    cmd = [
        "ffmpeg", "-y", "-i", image_path,
        "-vf", (
            f"drawtext=text='{safe_text}':"
            "fontsize=48:fontcolor=white:"
            "x=(w-text_w)/2:y=h-text_h-40:"
            "shadowcolor=black:shadowx=2:shadowy=2:"
            "box=1:boxcolor=black@0.5:boxborderw=10"
        ),
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"text overlay failed: {result.stderr[-300:]}")
    return output_path


# ---------------------------------------------------------------------------
# STEP 6: PLATFORM OPTIMIZATION
# ---------------------------------------------------------------------------

PLATFORM_SPECS = {
    "tiktok": {
        "desc_limit": 2200,
        "hashtag_count": 5,
        "needs_title": False,
        "notes": "Hook in first line. Short punchy sentences. Trending audio references if relevant.",
    },
    "instagram": {
        "desc_limit": 2200,
        "hashtag_count": 30,
        "needs_title": False,
        "notes": "Strong visual hook. Story in caption. Mix niche and broad hashtags.",
    },
    "facebook": {
        "desc_limit": 63206,
        "hashtag_count": 5,
        "needs_title": False,
        "notes": "Conversational tone. Ask a question to drive comments. Fewer hashtags.",
    },
    "youtube": {
        "desc_limit": 5000,
        "hashtag_count": 15,
        "needs_title": True,
        "notes": "SEO-optimized title (60 chars max). First 150 chars of description matter most. Include timestamps.",
    },
}

PLATFORM_SYSTEM = """You are a social media SEO and content strategist.
Given a transcript summary and platform specs, generate optimized content.
Return ONLY valid JSON. No markdown."""


async def optimize_for_platform(
    platform: str,
    summary: str,
    raw_transcript: str,
    clip_hook: str = "",
) -> PlatformContent:
    spec  = PLATFORM_SPECS.get(platform.lower(), PLATFORM_SPECS["youtube"])
    needs_title = spec["needs_title"]

    schema = {
        "description": "optimized description",
        "hashtags":    ["tag1", "tag2"],
    }
    if needs_title:
        schema["title"] = "SEO optimized title under 60 chars"

    messages = [
        {"role": "system", "content": PLATFORM_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Platform: {platform.upper()}\n"
                f"Platform notes: {spec['notes']}\n"
                f"Hashtag count: {spec['hashtag_count']}\n"
                f"Description limit: {spec['desc_limit']} chars\n"
                f"Needs title: {needs_title}\n\n"
                f"Content summary: {summary}\n\n"
                + (f"Opening hook: {clip_hook}\n\n" if clip_hook else "")
                + f"Return JSON: {json.dumps(schema)}"
            ),
        },
    ]

    try:
        raw = await call_task("brief", messages)
    except Exception:
        raw, _ = await call_task_with_fallback("brief", messages, "shadow_chat")

    try:
        clean = raw.strip()
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        data = json.loads(clean)
        return PlatformContent(
            platform=platform,
            title=data.get("title", ""),
            description=data.get("description", ""),
            hashtags=data.get("hashtags", []),
            optimized=(
                (data.get("title", "") + "\n\n" if needs_title else "") +
                data.get("description", "") + "\n\n" +
                " ".join(f"#{h.lstrip('#')}" for h in data.get("hashtags", []))
            ),
        )
    except Exception as e:
        log.error("[SOCIAL] platform parse failed for %s: %s", platform, e)
        return PlatformContent(platform=platform, description=summary)


# ---------------------------------------------------------------------------
# STEP 7: THUMBNAIL
# ---------------------------------------------------------------------------

THUMB_SYSTEM = """You are a thumbnail strategist. Given a transcript hook and content summary,
suggest the best timestamp for a thumbnail screenshot AND generate a Midjourney/DALL-E prompt
for an AI-generated alternative thumbnail.
Return ONLY JSON: {"timestamp": 12.5, "text_overlay": "Short punchy text", "ai_prompt": "detailed image prompt"}"""


async def suggest_thumbnail(
    hook: str,
    summary: str,
    segments: list[TranscriptSegment],
) -> ThumbnailOption:
    messages = [
        {"role": "system", "content": THUMB_SYSTEM},
        {"role": "user",   "content": f"Hook: {hook}\nSummary: {summary}\nAvailable timestamps: 0 to {segments[-1].end if segments else 60}s"},
    ]

    try:
        raw = await call_task("brief", messages)
        clean = raw.strip()
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        data = json.loads(clean)
        return ThumbnailOption(
            type="screenshot",
            timestamp=float(data.get("timestamp", 0)),
            text_overlay=data.get("text_overlay", ""),
            ai_prompt=data.get("ai_prompt", ""),
        )
    except Exception as e:
        log.error("[SOCIAL] thumbnail suggest failed: %s", e)
        return ThumbnailOption(type="screenshot", timestamp=0)


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

async def run_social_pipeline(
    source_path: str,
    platforms: list[str],
    transcript_mode: str = "raw",   # raw | clean | summary
    max_clips: int = 5,
    on_progress=None,
) -> SocialResult:
    """
    Full pipeline from upload to platform-ready content.

    Args:
        source_path:      path to uploaded audio or video file
        platforms:        list of platforms e.g. ["youtube", "tiktok"]
        transcript_mode:  "raw", "clean", or "summary"
        max_clips:        max number of short clips to identify
        on_progress:      optional async callable(step, message)
    """
    job_id = _next_job_id()
    result = SocialResult(
        job_id=job_id,
        source_file=source_path,
        status="processing",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _jobs[job_id] = result

    async def progress(step: str, msg: str):
        log.info("[SOCIAL] [%s] %s: %s", job_id, step, msg)
        if on_progress:
            try:
                await on_progress(step, msg)
            except Exception:
                pass

    try:
        # Step 1+2: rip audio if video
        await progress("AUDIO", "Extracting audio...")
        if is_video(source_path):
            audio_path = rip_audio(source_path, RENDERS_DIR)
        else:
            audio_path = source_path
        result.audio_path = audio_path

        # Step 3: transcribe
        await progress("TRANSCRIBE", "Transcribing with Whisper...")
        segments       = await transcribe(audio_path)
        result.transcript = segments
        result.raw_text   = segments_to_text(segments)
        timestamped       = segments_to_timestamped(segments)

        # Step 3b: optional processing
        display_transcript = result.raw_text
        if transcript_mode == "clean":
            await progress("CLEAN", "Cleaning transcript...")
            result.clean_text    = await clean_transcript(result.raw_text)
            display_transcript   = result.clean_text
        elif transcript_mode == "summary":
            await progress("SUMMARY", "Summarizing...")
            result.summary       = await summarize_transcript(result.raw_text)
            display_transcript   = result.summary

        if not result.summary:
            await progress("SUMMARY", "Generating summary...")
            result.summary = await summarize_transcript(result.raw_text[:3000])

        # Step 4: identify clips
        await progress("CLIPS", f"Finding top {max_clips} clips...")
        result.clips = await identify_clips(timestamped, max_clips)

        # Step 5: cut clips (only if source is video)
        if is_video(source_path) and result.clips:
            await progress("CUT", f"Cutting {len(result.clips)} clips...")
            base = Path(source_path).stem
            for i, clip in enumerate(result.clips):
                out = os.path.join(RENDERS_DIR, f"{base}_clip{i+1}.mp4")
                try:
                    cut_clip(source_path, clip.start, clip.end, out)
                    clip.clip_path = out
                except Exception as e:
                    log.warning("[SOCIAL] clip %d cut failed: %s", i+1, e)

        # Step 6: platform optimization
        top_hook = result.clips[0].hook if result.clips else ""
        for platform in platforms:
            await progress("PLATFORM", f"Optimizing for {platform}...")
            content = await optimize_for_platform(
                platform, result.summary, result.raw_text, top_hook
            )
            result.platforms.append(content)

        # Step 7: thumbnail suggestion
        if is_video(source_path) and result.clips:
            await progress("THUMBNAIL", "Suggesting thumbnails...")
            for clip in result.clips[:3]:
                thumb = await suggest_thumbnail(clip.hook, result.summary, segments)

                # Extract screenshot
                if is_video(source_path):
                    base = Path(source_path).stem
                    img_path = os.path.join(RENDERS_DIR, f"{base}_thumb_{int(clip.start)}.jpg")
                    try:
                        extract_screenshot(source_path, clip.start + 1, img_path)
                        if thumb.text_overlay:
                            overlay_path = img_path.replace(".jpg", "_overlay.jpg")
                            add_text_overlay(img_path, thumb.text_overlay, overlay_path)
                            thumb.image_path = overlay_path
                        else:
                            thumb.image_path = img_path
                    except Exception as e:
                        log.warning("[SOCIAL] screenshot failed: %s", e)

                result.thumbnails.append(thumb)

        result.status = "completed"
        await progress("DONE", f"Pipeline complete for job {job_id}")

    except Exception as e:
        result.status = "failed"
        result.error  = str(e)
        log.error("[SOCIAL] pipeline failed: %s", e)

    return result
