"""
Twin Shadow — Part 4: Video Lab + Shadow Video
Video Lab:    Fal → HuggingFace → Replicate
Shadow Video: Grok aurora + ffmpeg
"""

from __future__ import annotations
import os
import subprocess
import tempfile
from pathlib import Path
from openai import AsyncOpenAI


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO LAB — fal primary, HF fallback, Replicate alt
# ══════════════════════════════════════════════════════════════════════════════

async def _fal_generate(prompt: str, model: str = "fal-ai/wan-2.1") -> str:
    try:
        import fal_client
    except ImportError:
        raise RuntimeError("fal-client not installed. Run: pip install fal-client")

    os.environ["FAL_KEY"] = os.environ.get("FAL_KEY", "")
    handler = await fal_client.submit_async(model, arguments={"prompt": prompt})
    result  = await handler.get()

    if "video" in result:
        return result["video"]["url"]
    if "images" in result:
        return result["images"][0]["url"]
    return str(result)


async def _hf_generate(prompt: str) -> str:
    import asyncio
    from huggingface_hub import InferenceClient

    client = InferenceClient(token=os.environ.get("HF_TOKEN", ""))
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: client.text_to_video(prompt, model="Wan-AI/Wan2.1-T2V-14B"),
    )
    return result.url if hasattr(result, "url") else str(result)


async def _replicate_generate(prompt: str) -> str:
    import asyncio
    import replicate

    loop   = asyncio.get_event_loop()
    output = await loop.run_in_executor(
        None,
        lambda: replicate.run("minimax/video-01", input={"prompt": prompt}),
    )
    return output[0] if isinstance(output, list) else str(output)


async def run_video(task: dict) -> dict:
    prompt = task["input"].get("prompt", "")
    errors = {}

    for provider, fn in [("fal", _fal_generate), ("huggingface", _hf_generate), ("replicate", _replicate_generate)]:
        try:
            url = await fn(prompt)
            return {"output": url, "module": "video", "provider": provider, "shadow": False}
        except Exception as e:
            errors[provider] = str(e)
            print(f"[VIDEO LAB] {provider} failed: {e}")

    return {"output": "All video providers failed.", "module": "video", "shadow": True, "errors": errors}


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
        model="grok-beta",
        messages=[
            {"role": "system", "content": "You are a creative video director. Raw, visual, precise."},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=2000,
    )
    return resp.choices[0].message.content


def _run_ffmpeg(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(["ffmpeg", "-y"] + args, capture_output=True, text=True, timeout=120)
        return result.returncode == 0, result.stderr
    except FileNotFoundError:
        return False, "ffmpeg not found. Install: https://ffmpeg.org/download.html"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out."


def images_to_slideshow(image_paths: list[str], output_path: str, duration: float = 3.0) -> tuple[bool, str]:
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


def add_audio_to_video(video_path: str, audio_path: str, output_path: str) -> tuple[bool, str]:
    ok, err = _run_ffmpeg(["-i", video_path, "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-shortest", output_path])
    return (ok, output_path) if ok else (False, err)


def add_text_overlay(video_path: str, text: str, output_path: str) -> tuple[bool, str]:
    safe = text.replace("'", "\\'").replace(":", "\\:")
    ok, err = _run_ffmpeg([
        "-i", video_path,
        "-vf", f"drawtext=text='{safe}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=h-th-20:box=1:boxcolor=black@0.5:boxborderw=5",
        "-c:a", "copy", output_path,
    ])
    return (ok, output_path) if ok else (False, err)


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
