"""
Twin Shadow — Part 4: Video Lab + Shadow Video
Video Lab:    FAL → HuggingFace → Replicate
Shadow Video: Grok aurora + ffmpeg
FFMPEG utils: extract_last_frame, assemble_lab_video, download_file
"""

from __future__ import annotations
import os
import subprocess
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path
from openai import AsyncOpenAI


# ══════════════════════════════════════════════════════════════════════════════
# FFMPEG HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _run_ffmpeg(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-y"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stderr
    except FileNotFoundError:
        return False, "ffmpeg not found — check your PATH"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out"


def extract_last_frame(video_path: str, output_path: str | None = None) -> str | None:
    """Extract last frame of a video as PNG. Returns path or None on failure."""
    out = output_path or video_path.rsplit(".", 1)[0] + "_lastframe.png"
    ok, err = _run_ffmpeg(["-sseof", "-3", "-i", video_path, "-vframes", "1", "-q:v", "2", out])
    if not ok:
        # Fallback: extract first frame instead
        ok2, _ = _run_ffmpeg(["-i", video_path, "-vframes", "1", "-q:v", "2", out])
        return out if ok2 else None
    return out


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds using ffprobe, or 0.0 on error."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", video_path],
            capture_output=True, text=True, timeout=15,
        )
        import json
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


def download_file(url: str, dest_dir: str, filename: str | None = None) -> str | None:
    """Download a remote URL to dest_dir. Returns local path or None on failure."""
    try:
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        fname = filename or Path(url.split("?")[0]).name or "downloaded_file"
        dest  = str(Path(dest_dir) / fname)
        urllib.request.urlretrieve(url, dest)
        return dest
    except Exception as e:
        print(f"[VIDEO LAB] download_file failed: {e}")
        return None


def images_to_slideshow(image_paths: list[str], output_path: str, duration: float = 3.0) -> tuple[bool, str]:
    """Create an MP4 slideshow from local image files."""
    if not image_paths:
        return False, "No images."
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for img in image_paths:
            f.write(f"file '{img}'\nduration {duration}\n")
        list_file = f.name
    ok, err = _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path,
    ])
    Path(list_file).unlink(missing_ok=True)
    return (ok, output_path) if ok else (False, err)


def concatenate_clips(clip_paths: list[str], output_path: str) -> tuple[bool, str]:
    """Concatenate video clips (must have same codec/resolution) into one MP4."""
    if not clip_paths:
        return False, "No clips."
    if len(clip_paths) == 1:
        import shutil
        shutil.copy2(clip_paths[0], output_path)
        return True, output_path
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for c in clip_paths:
            f.write(f"file '{c}'\n")
        list_file = f.name
    ok, err = _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", output_path,
    ])
    Path(list_file).unlink(missing_ok=True)
    return (ok, output_path) if ok else (False, err)


def concatenate_clips_with_fade(
    clip_paths: list[str],
    output_path: str,
    fade_duration: float = 0.5,
) -> tuple[bool, str]:
    """
    Concatenate clips with FFMPEG xfade crossfade transitions.
    Produces video-only output (no audio stream) — call mix_audio_onto_video() afterwards.
    Falls back to hard-cut concatenation if xfade fails (codec mismatch, etc.).

    Offset formula for chain of N clips, step i (1-indexed):
        offset_i = sum(durations[0..i-1]) - i * fade_duration
    """
    if not clip_paths:
        return False, "No clips."
    if len(clip_paths) == 1:
        import shutil
        shutil.copy2(clip_paths[0], output_path)
        return True, output_path

    durations = [max(get_video_duration(p), fade_duration + 0.1) for p in clip_paths]

    inputs: list[str] = []
    for p in clip_paths:
        inputs.extend(["-i", p])

    fc_parts: list[str] = []
    prev_tag       = "[0:v]"
    cumulative_dur = 0.0

    for i in range(1, len(clip_paths)):
        cumulative_dur += durations[i - 1]
        offset  = max(0.0, cumulative_dur - i * fade_duration)
        out_tag = "[v]" if i == len(clip_paths) - 1 else f"[x{i}]"
        fc_parts.append(
            f"{prev_tag}[{i}:v]xfade=transition=fade"
            f":duration={fade_duration:.2f}:offset={offset:.3f}{out_tag}"
        )
        prev_tag = out_tag

    fc   = ";".join(fc_parts)
    ok, err = _run_ffmpeg(
        inputs + [
            "-filter_complex", fc,
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            output_path,
        ]
    )
    if ok:
        return True, output_path
    print(f"[VIDEO LAB] xfade failed ({err[:120]}), falling back to hard cut")
    return concatenate_clips(clip_paths, output_path)


def mix_audio_onto_video(
    video_path: str,
    voiceover_path: str | None,
    music_path: str | None,
    output_path: str,
) -> tuple[bool, str]:
    """
    Mix voiceover + optional background music onto video.
    Music is reduced to 25% volume to stay under voiceover.
    Returns (ok, output_path_or_error).
    """
    if not voiceover_path and not music_path:
        import shutil
        shutil.copy2(video_path, output_path)
        return True, output_path

    if voiceover_path and not music_path:
        ok, err = _run_ffmpeg([
            "-i", video_path, "-i", voiceover_path,
            "-c:v", "copy", "-c:a", "aac",
            "-filter_complex", "[1:a]volume=1.0[vo];[0:a][vo]amix=inputs=2:duration=shortest",
            "-shortest", output_path,
        ])
        if not ok:
            ok, err = _run_ffmpeg([
                "-i", video_path, "-i", voiceover_path,
                "-c:v", "copy", "-c:a", "aac", "-shortest", output_path,
            ])
        return (ok, output_path) if ok else (False, err)

    if music_path and not voiceover_path:
        ok, err = _run_ffmpeg([
            "-i", video_path, "-i", music_path,
            "-c:v", "copy", "-c:a", "aac",
            "-filter_complex", "[1:a]volume=0.25[bg];[0:a][bg]amix=inputs=2:duration=shortest",
            "-shortest", output_path,
        ])
        if not ok:
            ok, err = _run_ffmpeg([
                "-i", video_path, "-i", music_path,
                "-c:v", "copy", "-c:a", "aac", "-shortest", output_path,
            ])
        return (ok, output_path) if ok else (False, err)

    # Both voiceover + music
    ok, err = _run_ffmpeg([
        "-i", video_path, "-i", voiceover_path, "-i", music_path,
        "-c:v", "copy", "-c:a", "aac",
        "-filter_complex",
        "[1:a]volume=1.0[vo];[2:a]volume=0.25[bg];[vo][bg]amix=inputs=2:duration=shortest",
        "-shortest", output_path,
    ])
    return (ok, output_path) if ok else (False, err)


def add_audio_to_video(video_path: str, audio_path: str, output_path: str) -> tuple[bool, str]:
    ok, err = _run_ffmpeg(["-i", video_path, "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-shortest", output_path])
    return (ok, output_path) if ok else (False, err)


def mix_music_fullvol(video_path: str, music_path: str, output_path: str) -> tuple[bool, str]:
    """Mix a full music track at 100% volume onto video.
    Music loops if shorter than video; video ends when video ends (-shortest on video stream).
    """
    ok, err = _run_ffmpeg([
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-c:v", "copy", "-c:a", "aac",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest", output_path,
    ], timeout=600)
    if not ok:
        # Fallback: no loop, just lay the track as-is
        ok, err = _run_ffmpeg([
            "-i", video_path, "-i", music_path,
            "-c:v", "copy", "-c:a", "aac",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest", output_path,
        ], timeout=600)
    return (ok, output_path) if ok else (False, err)


def extract_audio(video_path: str, output_path: str) -> tuple[bool, str]:
    """Extract audio as mono 16kHz MP3 — for pre-processing before Whisper transcription."""
    ok, err = _run_ffmpeg([
        "-i", video_path,
        "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k",
        output_path,
    ])
    return (ok, output_path) if ok else (False, err)


def cut_segment(source_path: str, start: float, end: float, output_path: str) -> tuple[bool, str]:
    """Cut a precise segment (seconds). Re-encodes for clean keyframe-aligned cuts."""
    duration = round(end - start, 3)
    if duration <= 0:
        return False, "Invalid segment: end must be after start"
    ok, err = _run_ffmpeg([
        "-ss", f"{start:.3f}", "-i", source_path,
        "-t",  f"{duration:.3f}",
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        output_path,
    ])
    return (ok, output_path) if ok else (False, err)


def reformat_for_platform(source_path: str, platform: str, output_path: str) -> tuple[bool, str]:
    """
    Reformat video aspect ratio for a target platform.
    youtube  → 1920×1080 16:9  (letterbox black bars)
    tiktok   → 1080×1920 9:16  (centre crop)
    instagram→ 1080×1920 9:16  (centre crop)
    facebook → 1280×720  16:9  (letterbox black bars)
    """
    p = platform.lower()
    if p in ("tiktok", "instagram"):
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:(iw-1080)/2:(ih-1920)/2"
    elif p == "facebook":
        vf = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black"
    else:
        vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black"
    ok, err = _run_ffmpeg([
        "-i", source_path,
        "-vf", vf,
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        output_path,
    ])
    return (ok, output_path) if ok else (False, err)


def make_thumbnail(
    source_path: str,
    output_path: str,
    timestamp: float = 0.0,
    text: str = "",
    position: str = "bottom",
    font_path: str | None = None,
    font_size: int = 52,
    font_color: str = "white",
    outline: bool = True,
) -> tuple[bool, str]:
    """
    Extract a frame at timestamp seconds and optionally burn in overlay text.
    position: 'top' | 'center' | 'bottom'
    font_path: absolute path to a .ttf file (uses ffmpeg default if None)
    """
    y_expr = {"top": "th+20", "center": "(h-th)/2", "bottom": "h-th-30"}.get(position, "h-th-30")
    if not text:
        ok, err = _run_ffmpeg([
            "-ss", f"{timestamp:.3f}", "-i", source_path,
            "-vframes", "1", "-q:v", "2", output_path,
        ])
        return (ok, output_path) if ok else (False, err)

    safe  = text.replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")
    ffile = f"fontfile='{font_path}':" if font_path and Path(font_path).exists() else ""
    box   = ":box=1:boxcolor=black@0.65:boxborderw=12" if outline else ""
    vf = (
        f"drawtext={ffile}text='{safe}':fontcolor={font_color}:fontsize={font_size}"
        f":x=(w-text_w)/2:y={y_expr}{box}"
    )
    ok, err = _run_ffmpeg([
        "-ss", f"{timestamp:.3f}", "-i", source_path,
        "-vframes", "1", "-vf", vf, "-q:v", "2", output_path,
    ])
    return (ok, output_path) if ok else (False, err)


def add_text_overlay(video_path: str, text: str, output_path: str) -> tuple[bool, str]:
    safe = text.replace("'", "\\'").replace(":", "\\:")
    ok, err = _run_ffmpeg([
        "-i", video_path,
        "-vf", f"drawtext=text='{safe}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=h-th-20:box=1:boxcolor=black@0.5:boxborderw=5",
        "-c:a", "copy", output_path,
    ])
    return (ok, output_path) if ok else (False, err)


# ══════════════════════════════════════════════════════════════════════════════
# FAL IMAGE GENERATION
# ══════════════════════════════════════════════════════════════════════════════

async def fal_generate_image(prompt: str, style: str = "", reference_frame_path: str | None = None) -> dict:
    """
    Generate an image via FAL.
    When reference_frame_path is given:
      1. Upload the frame to FAL CDN via fal_client.upload_file()
      2. Use fal-ai/flux/dev/image-to-image for true visual continuity (strength=0.65)
      3. Fall back to text-only fal-ai/flux/schnell on failure
    Returns {"url": ..., "local_path": None} or {"error": ...}.
    """
    try:
        import fal_client
    except ImportError:
        return {"error": "fal-client not installed"}

    import asyncio

    full_prompt = f"{prompt}, {style}" if style else prompt

    # Try img2img with reference frame (real pixel-level visual anchor)
    if reference_frame_path and Path(reference_frame_path).exists():
        try:
            loop    = asyncio.get_event_loop()
            ref_url = await loop.run_in_executor(None, fal_client.upload_file, reference_frame_path)
            result  = await fal_client.run_async(
                "fal-ai/flux/dev/image-to-image",
                arguments={
                    "prompt":              full_prompt[:500],
                    "image_url":           ref_url,
                    "strength":            0.65,
                    "num_inference_steps": 28,
                    "num_images":          1,
                    "image_size":          "landscape_16_9",
                },
            )
            images = result.get("images", [])
            if images:
                return {"url": images[0]["url"], "local_path": None, "reference_used": True}
        except Exception as e:
            print(f"[VIDEO LAB] img2img failed ({e}), falling back to T2I")
            full_prompt += " — seamless continuation, matching visual style and lighting"

    # Text-to-image (no reference or img2img failed)
    try:
        result = await fal_client.run_async(
            "fal-ai/flux/schnell",
            arguments={
                "prompt":              full_prompt[:500],
                "image_size":          "landscape_16_9",
                "num_images":          1,
                "num_inference_steps": 4,
            },
        )
        images = result.get("images", [])
        if not images:
            return {"error": "No images returned"}
        return {"url": images[0]["url"], "local_path": None}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO LAB — fal primary, Replicate secondary (background thread jobs)
# ══════════════════════════════════════════════════════════════════════════════

async def _fal_generate(prompt: str, model: str = "fal-ai/wan-2.1", image_url: str | None = None) -> str:
    try:
        import fal_client
    except ImportError:
        raise RuntimeError("fal-client not installed. Run: pip install fal-client")

    args: dict = {"prompt": prompt}
    if image_url:
        args["image_url"] = image_url

    handler = await fal_client.submit_async(model, arguments=args)
    result  = await handler.get()

    if "video" in result:
        url = result["video"].get("url") or result["video"]
        if isinstance(url, dict):
            url = url.get("url", str(url))
        return str(url)
    if "images" in result:
        return result["images"][0]["url"]
    return str(result)


async def _hf_generate(prompt: str) -> str:
    import asyncio
    from huggingface_hub import InferenceClient

    client = InferenceClient(token=os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY", ""))
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: client.text_to_video(prompt, model="Wan-AI/Wan2.1-T2V-14B"),
    )
    return result.url if hasattr(result, "url") else str(result)


async def _replicate_generate(prompt: str, image_url: str | None = None) -> str:
    import asyncio
    import replicate

    _token = "".join(c for c in os.environ.get("REPLICATE_API_TOKEN", "") if ord(c) < 128).strip()
    _client = replicate.Client(api_token=_token)
    loop   = asyncio.get_event_loop()
    inp    = {"prompt": prompt}
    if image_url:
        inp["first_frame_image"] = image_url
    output = await loop.run_in_executor(
        None,
        lambda: _client.run("minimax/video-01", input=inp),
    )
    return output[0] if isinstance(output, list) else str(output)


async def run_video(task: dict) -> dict:
    prompt    = task["input"].get("prompt", "")
    image_url = task["input"].get("image_url")
    errors    = {}

    for provider, fn in [
        ("huggingface", _hf_generate),
        ("fal",         lambda p: _fal_generate(p, image_url=image_url)),
        ("replicate",   lambda p: _replicate_generate(p, image_url=image_url)),
    ]:
        try:
            url = await fn(prompt)
            return {"output": url, "module": "video", "provider": provider, "shadow": False}
        except Exception as e:
            errors[provider] = str(e)
            print(f"[VIDEO LAB] {provider} failed: {e}")

    return {"output": "All video providers failed.", "module": "video", "shadow": True, "errors": errors}


# ══════════════════════════════════════════════════════════════════════════════
# PEXELS STOCK VIDEO SEARCH
# ══════════════════════════════════════════════════════════════════════════════

async def search_pexels_videos(query: str, per_page: int = 12, orientation: str = "landscape") -> list[dict]:
    """Search Pexels for free stock videos. Returns list of result dicts."""
    import asyncio, json

    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY not configured")

    encoded = urllib.parse.quote(query)
    url = (
        f"https://api.pexels.com/videos/search"
        f"?query={encoded}&per_page={per_page}&orientation={orientation}"
    )

    def _fetch():
        req = urllib.request.Request(url, headers={"Authorization": api_key})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read())

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch)

    results = []
    for v in data.get("videos", []):
        files = sorted(v.get("video_files", []), key=lambda f: f.get("width", 0), reverse=True)
        hd   = next((f for f in files if f.get("quality") == "hd"), None)
        best = hd or (files[0] if files else None)
        if not best:
            continue
        results.append({
            "id":           v["id"],
            "url":          best["link"],
            "thumbnail":    v.get("image", ""),
            "duration":     v.get("duration", 0),
            "photographer": v.get("user", {}).get("name", "Pexels"),
            "width":        best.get("width", 0),
            "height":       best.get("height", 0),
        })
    return results


# ══════════════════════════════════════════════════════════════════════════════
# SHADOW VIDEO — Grok aurora + ffmpeg
# ══════════════════════════════════════════════════════════════════════════════

def _grok_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url="https://api.x.ai/v1",
        api_key=os.environ.get("GROK_API_KEY", ""),
    )


async def grok_imagine(prompt: str, n: int = 1) -> list[str]:
    client   = _grok_client()
    response = await client.images.generate(model="aurora", prompt=prompt, n=n)
    return [img.url for img in response.data]


async def grok_direct(prompt: str) -> str:
    client = _grok_client()
    resp   = await client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": "You are a creative video director. Raw, visual, precise."},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=2000,
    )
    return resp.choices[0].message.content


async def run_shadow_video(task: dict) -> dict:
    prompt     = task["input"].get("prompt", "")
    output_dir = Path(task["input"].get("output_dir", "/tmp/shadow_video"))
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        shot_list = await grok_direct(
            f"Write a 4-shot visual description list for: {prompt}\n"
            "Format: 4 lines, each a vivid single-image description. Nothing else."
        )
        shots = [s.strip() for s in shot_list.strip().split("\n") if s.strip()][:4]
    except Exception as e:
        return {"output": f"Grok direction failed: {e}", "module": "shadow_video", "shadow": True}

    image_urls = []
    for shot in shots:
        try:
            urls = await grok_imagine(shot, n=1)
            image_urls.extend(urls)
        except Exception as e:
            print(f"[SHADOW VIDEO] Image gen failed: {e}")

    if not image_urls:
        return {"output": "No images generated.", "module": "shadow_video", "shadow": True, "shots": shots}

    return {
        "output":       image_urls,
        "shot_list":    shots,
        "module":       "shadow_video",
        "shadow":       False,
        "ffmpeg_ready": True,
        "note":         "Download URLs to local paths then call images_to_slideshow().",
    }
