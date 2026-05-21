"""
Twin Shadow — Part 4: Video Lab + Shadow Video
Video Lab:    FAL → HuggingFace → Replicate
Shadow Video: Grok aurora + ffmpeg
FFMPEG utils: extract_last_frame, assemble_lab_video, download_file
"""

from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path
from openai import AsyncOpenAI

_FFMPEG = (
    shutil.which("ffmpeg")
    or "/nix/store/3zc5jbvqzrn8zmva4fx5p19goxxa8bm-ffmpeg-7.1/bin/ffmpeg"
)

def _fal_client():
    """Return a fresh fal_client.AsyncClient per call.
    The module-level async_client singleton binds asyncio.Lock objects to
    the event loop that existed at import time.  Because _run_async() creates
    a new event loop for every Flask request, those locks are always stale
    and auth resolution deadlocks / raises RuntimeError.  A fresh instance
    per call avoids that entirely."""
    try:
        from fal_client import AsyncClient
    except ImportError:
        return None
    key = os.environ.get("FAL_KEY", "")
    return AsyncClient(key=key if key else None)


# ══════════════════════════════════════════════════════════════════════════════
# FFMPEG HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _run_ffmpeg(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [_FFMPEG, "-y"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stderr
    except FileNotFoundError:
        return False, f"ffmpeg not found at {_FFMPEG}"
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

def _fal_http(endpoint: str, payload: dict) -> dict:
    """
    Call a FAL inference endpoint directly via urllib (synchronous).
    Bypasses fal_client's AsyncClient entirely — fal_client uses
    async_cached_property(asyncio.Lock) which binds to the import-time event
    loop and deadlocks / raises 401 when _run_async() creates a fresh loop
    per Flask request.
    Returns the parsed JSON response dict, or raises on HTTP error.
    """
    import json as _j
    key = os.environ.get("FAL_KEY", "")
    body = _j.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://fal.run/{endpoint}",
        data=body,
        headers={
            "Authorization": f"Key {key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return _j.loads(resp.read())


async def fal_generate_image(prompt: str, style: str = "", reference_frame_path: str | None = None) -> dict:
    """
    Generate an image via FAL using direct HTTP (no fal_client asyncio issues).
    Falls back to text-to-image if img2img fails.
    Returns {"url": ..., "local_path": None} or {"error": ...}.
    """
    import asyncio

    key = os.environ.get("FAL_KEY", "")
    if not key:
        return {"error": "FAL_KEY not set"}

    full_prompt = f"{prompt}, {style}" if style else prompt
    loop = asyncio.get_event_loop()

    # Try img2img with reference frame
    if reference_frame_path and Path(reference_frame_path).exists():
        try:
            from fal_client import SyncClient
            sync_client = SyncClient(key=key)
            ref_url = await loop.run_in_executor(None, sync_client.upload_file, reference_frame_path)
            data = await loop.run_in_executor(None, _fal_http, "fal-ai/flux/dev/image-to-image", {
                "prompt":              full_prompt[:500],
                "image_url":           ref_url,
                "strength":            0.65,
                "num_inference_steps": 28,
                "num_images":          1,
                "image_size":          "landscape_16_9",
            })
            images = data.get("images", [])
            if images:
                return {"url": images[0]["url"], "local_path": None, "reference_used": True}
        except Exception as e:
            print(f"[FAL] img2img failed ({e}), falling back to T2I")
            full_prompt += " — seamless continuation, matching visual style and lighting"

    # Text-to-image
    try:
        data = await loop.run_in_executor(None, _fal_http, "fal-ai/flux/schnell", {
            "prompt":              full_prompt[:500],
            "image_size":          "landscape_16_9",
            "num_images":          1,
            "num_inference_steps": 4,
        })
        images = data.get("images", [])
        if not images:
            return {"error": "No images returned", "raw": str(data)[:200]}
        return {"url": images[0]["url"], "local_path": None}
    except Exception as e:
        return {"error": str(e)}


async def lab_generate_image(prompt: str, style: str = "", reference_frame_path: str | None = None) -> dict:
    """
    Multi-provider image generation with waterfall fallback:
    FAL → Grok Aurora → HuggingFace FLUX.1-schnell → AIML Flux (key rotation).

    Each provider is skipped instantly if its key env var is not set.
    Returns {"url": ..., "local_path": None, "provider": "fal"|"grok"|"hf"|"aiml"}
    or {"error": "All image providers failed", "errors": {...}}.
    """
    full_prompt = f"{prompt}, {style}" if style else prompt
    errors: dict = {}

    # ── 1. FAL ────────────────────────────────────────────────────────────────
    if os.environ.get("FAL_KEY", ""):
        try:
            result = await fal_generate_image(prompt, style, reference_frame_path)
            if result.get("url"):
                result.setdefault("provider", "fal")
                return result
            errors["fal"] = result.get("error", "no url returned")
        except Exception as e:
            errors["fal"] = str(e)
            print(f"[IMAGE] FAL failed: {e}")
    else:
        print("[IMAGE] FAL_KEY not set — skipping FAL")

    # ── 2. Grok Aurora ────────────────────────────────────────────────────────
    if os.environ.get("GROK_API_KEY", ""):
        try:
            urls = await grok_imagine(full_prompt[:500], n=1)
            if urls:
                return {"url": urls[0], "local_path": None, "provider": "grok"}
            errors["grok"] = "no images returned"
        except Exception as e:
            errors["grok"] = str(e)
            print(f"[IMAGE] Grok Aurora failed: {e}")
    else:
        print("[IMAGE] GROK_API_KEY not set — skipping Grok Aurora")

    # ── 3. Replicate flux-schnell (free credits) ───────────────────────────────
    if os.environ.get("REPLICATE_API_TOKEN", ""):
        try:
            url = await _replicate_image(full_prompt[:500])
            if url:
                print(f"[IMAGE] Replicate OK — {url[:60]}")
                return {"url": url, "local_path": None, "provider": "replicate"}
            errors["replicate"] = "no url returned"
        except Exception as e:
            errors["replicate"] = str(e)
            print(f"[IMAGE] Replicate failed: {e}")
    else:
        print("[IMAGE] REPLICATE_API_TOKEN not set — skipping Replicate")

    # ── 4. HuggingFace FLUX.1-schnell ─────────────────────────────────────────
    hf_key = os.environ.get("HUGGINGFACE_API_KEY", "") or os.environ.get("HF_TOKEN", "")
    if hf_key:
        try:
            import asyncio as _asyncio, uuid as _uuid, tempfile as _tf
            from huggingface_hub import InferenceClient
            _hfc  = InferenceClient(token=hf_key)
            _loop = _asyncio.get_event_loop()
            img   = await _loop.run_in_executor(
                None,
                lambda: _hfc.text_to_image(full_prompt[:500], model="black-forest-labs/FLUX.1-schnell"),
            )
            tmp = Path(_tf.gettempdir()) / f"hf_img_{_uuid.uuid4().hex[:8]}.jpg"
            img.save(str(tmp), format="JPEG")
            # Return as file:// URI — urlretrieve in the route handles the copy
            return {"url": tmp.as_uri(), "local_path": None, "provider": "hf"}
        except Exception as e:
            errors["hf"] = str(e)
            print(f"[IMAGE] HuggingFace failed: {e}")
    else:
        print("[IMAGE] HUGGINGFACE_API_KEY not set — skipping HuggingFace")

    # ── 5. AIML (key rotation ×3, model cascade) ──────────────────────────────
    import json as _json
    aiml_keys = [
        v for k in ("AIML_API_KEY_1", "AIML_API_KEY_2", "AIML_API_KEY_3")
        if (v := os.environ.get(k, ""))
    ]
    _aiml_models = ["flux/schnell", "flux/dev", "dall-e-2"]
    if aiml_keys:
        import asyncio as _asyncio2
        _loop2 = _asyncio2.get_event_loop()
        for _ki, aiml_key in enumerate(aiml_keys, start=1):
            for _model in _aiml_models:
                _ekey = f"aiml_key{_ki}_{_model}"
                try:
                    body = _json.dumps({
                        "model":  _model,
                        "prompt": full_prompt[:500],
                        "n":      1,
                        "size":   "1024x1024",
                    }).encode()
                    req = urllib.request.Request(
                        "https://api.aimlapi.com/v1/images/generations",
                        data=body,
                        headers={
                            "Authorization": f"Bearer {aiml_key}",
                            "Content-Type":  "application/json",
                        },
                        method="POST",
                    )
                    data = await _loop2.run_in_executor(
                        None,
                        lambda r=req: _json.loads(urllib.request.urlopen(r, timeout=60).read()),
                    )
                    url = (data.get("data") or [{}])[0].get("url", "")
                    if url:
                        return {"url": url, "local_path": None, "provider": f"aiml/{_model}"}
                    errors[_ekey] = "no url in response"
                except Exception as e:
                    errors[_ekey] = str(e)
                    print(f"[IMAGE] AIML key{_ki}/{_model} failed: {e}")
                    continue  # always try next model / next key
    else:
        print("[IMAGE] No AIML keys set — skipping AIML")

    return {"error": "All image providers failed", "errors": errors}


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO LAB — fal primary, Replicate secondary (background thread jobs)
# ══════════════════════════════════════════════════════════════════════════════

async def _fal_generate(prompt: str, model: str = "fal-ai/wan-2.1", image_url: str | None = None) -> str:
    import asyncio, json as _j
    key = os.environ.get("FAL_KEY", "")
    if not key:
        raise RuntimeError("FAL_KEY not set")

    payload: dict = {"prompt": prompt}
    if image_url:
        payload["image_url"] = image_url

    def _call():
        body = _j.dumps(payload).encode()
        req = urllib.request.Request(
            f"https://fal.run/{model}",
            data=body,
            headers={
                "Authorization": f"Key {key}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return _j.loads(resp.read())

    result = await asyncio.get_event_loop().run_in_executor(None, _call)

    if "video" in result:
        url = result["video"].get("url") or result["video"]
        if isinstance(url, dict):
            url = url.get("url", str(url))
        return str(url)
    if "images" in result:
        return result["images"][0]["url"]
    return str(result)


async def _replicate_image(prompt: str) -> str:
    """Generate image via Replicate flux-schnell — returns a URL string."""
    import asyncio, replicate as _rep
    token = "".join(c for c in os.environ.get("REPLICATE_API_TOKEN", "") if ord(c) < 128).strip()
    client = _rep.Client(api_token=token)
    loop   = asyncio.get_event_loop()
    output = await loop.run_in_executor(
        None,
        lambda: client.run(
            "black-forest-labs/flux-schnell",
            input={"prompt": prompt, "num_outputs": 1, "aspect_ratio": "16:9"},
        ),
    )
    first = output[0] if isinstance(output, (list, tuple)) else output
    return str(first.url) if hasattr(first, "url") else str(first)


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
    errors: dict = {}

    # Build provider list — FAL first (fastest), Replicate second, HuggingFace last
    providers = []
    if os.environ.get("FAL_KEY", ""):
        providers.append(("fal", lambda p: _fal_generate(p, image_url=image_url)))
    else:
        print("[VIDEO] FAL_KEY not set — skipping FAL")
    if os.environ.get("REPLICATE_API_TOKEN", ""):
        providers.append(("replicate", lambda p: _replicate_generate(p, image_url=image_url)))
    else:
        print("[VIDEO] REPLICATE_API_TOKEN not set — skipping Replicate")
    if os.environ.get("HUGGINGFACE_API_KEY", "") or os.environ.get("HF_TOKEN", ""):
        providers.append(("huggingface", lambda p: _hf_generate(p)))
    else:
        print("[VIDEO] HUGGINGFACE_API_KEY not set — skipping HuggingFace")

    for provider, fn in providers:
        try:
            url = await fn(prompt)
            return {"output": url, "module": "video", "provider": provider, "shadow": False}
        except Exception as e:
            errors[provider] = str(e)
            print(f"[VIDEO LAB] {provider} failed: {e}")

    # ── Grok Aurora last resort: generate 4 stills → slideshow MP4 ────────────
    if os.environ.get("GROK_API_KEY", ""):
        try:
            import asyncio as _av, uuid as _uv, tempfile as _tv
            _lv   = _av.get_event_loop()
            _tdir = Path(_tv.gettempdir()) / f"grok_vid_{_uv.uuid4().hex[:8]}"
            _tdir.mkdir(parents=True, exist_ok=True)
            shot_prompts = [
                f"{prompt} — establishing shot",
                f"{prompt} — midpoint action",
                f"{prompt} — dramatic climax",
                f"{prompt} — closing frame",
            ]
            local_paths: list[str] = []
            for i, sp in enumerate(shot_prompts):
                try:
                    urls = await grok_imagine(sp, n=1)
                    if urls:
                        img_path = str(_tdir / f"shot_{i:02d}.jpg")
                        await _lv.run_in_executor(
                            None,
                            lambda u=urls[0], p=img_path: urllib.request.urlretrieve(u, p),
                        )
                        local_paths.append(img_path)
                except Exception as ge:
                    print(f"[VIDEO FALLBACK] Grok shot {i} failed: {ge}")
            if local_paths:
                renders_dir = Path(__file__).parent / "renders"
                renders_dir.mkdir(parents=True, exist_ok=True)
                _hex      = _uv.uuid4().hex[:8]
                slide_raw = str(renders_dir / f"grok_raw_{_hex}.mp4")
                out_path  = str(renders_dir / f"grok_slide_{_hex}.mp4")
                ok, result_path = images_to_slideshow(local_paths, slide_raw, duration=4.0)
                if ok:
                    # Pass through mix_audio_onto_video — with no audio supplied it does
                    # a fast shutil.copy2, keeping the pipeline consistent for future audio
                    ok2, final_path = mix_audio_onto_video(slide_raw, None, None, out_path)
                    final = final_path if ok2 else slide_raw
                    return {
                        "output":   f"/renders/{Path(final).name}",
                        "module":   "video",
                        "provider": "grok_slideshow",
                        "shadow":   True,
                        "note":     "Assembled from Grok Aurora stills (no live video provider available)",
                    }
                errors["grok_slideshow"] = result_path
        except Exception as e:
            errors["grok"] = str(e)
            print(f"[VIDEO LAB] Grok fallback failed: {e}")
    else:
        print("[VIDEO] GROK_API_KEY not set — no fallback available")

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
    """Direct HTTP to xAI image API — avoids AsyncOpenAI asyncio.Lock conflicts."""
    import json as _json, asyncio as _asyncio, urllib.error as _ue
    key = os.environ.get("GROK_API_KEY", "")
    if not key:
        return []
    # Try aurora first, fall back to grok-2-image-1212
    for model_name in ("grok-2-image-1212", "aurora"):
        body = _json.dumps({
            "model":  model_name,
            "prompt": prompt,
            "n":      n,
        }).encode()
        req = urllib.request.Request(
            "https://api.x.ai/v1/images/generations",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        loop = _asyncio.get_event_loop()
        def _call(r=req, m=model_name):
            try:
                return _json.loads(urllib.request.urlopen(r, timeout=90).read())
            except _ue.HTTPError as he:
                err_body = he.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"Grok Aurora HTTP {he.code} [{m}]: {err_body}")
        try:
            data = await loop.run_in_executor(None, _call)
            urls = [img["url"] for img in data.get("data", [])]
            if urls:
                print(f"[IMAGE] Grok Aurora OK ({model_name}) — {len(urls)} image(s)")
                return urls
            print(f"[IMAGE] Grok {model_name} returned no data: {str(data)[:200]}")
        except Exception as e:
            print(f"[IMAGE] Grok {model_name} failed: {e}")
    return []


async def grok_direct(prompt: str) -> str:
    client = _grok_client()
    try:
        resp = await client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": "You are a creative video director. Raw, visual, precise."},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=2000,
        )
        return resp.choices[0].message.content
    finally:
        try:
            await client.close()
        except Exception:
            pass


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
