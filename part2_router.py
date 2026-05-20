"""
Twin Shadow — Part 2: LLM Router
Universal async router with key rotation.
Task #13: ASCII-safe key filtering (GROQ_API_KEY_2 unicode bug fixed);
          rolling_context() helper; max_tokens passthrough on all calls.
"""

from __future__ import annotations
import os
import asyncio
from typing import AsyncGenerator
from datetime import datetime, timezone
from openai import AsyncOpenAI

from part1_registry import get_task_routing, get_provider_cfg

# ── Provider-level fallbacks for server errors (502/503/504/overloaded) ────────
# When ALL keys for a provider are exhausted due to server errors, fall back here.
_PROVIDER_FALLBACK: dict[str, tuple[str, str]] = {
    "groq":    ("cerebras", "llama_small"),   # Groq 500/509 → Cerebras
    "nvidia":  ("cerebras", "llama_small"),   # NVIDIA 410/503 → Cerebras
    # Venice has no uncensored fallback — if down, fail cleanly; Groq Llama runs in parallel
}


def _is_ascii_safe(s: str) -> bool:
    """Return True if the string is purely ASCII-encodeable."""
    try:
        s.encode("ascii")
        return True
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def _build_client(provider_key: str, api_key_override: str | None = None) -> AsyncOpenAI:
    cfg = get_provider_cfg(provider_key)

    if not cfg.get("openai_compat"):
        raise ValueError(f"{provider_key} is not OpenAI-compatible. Use its native module.")

    if "base_url_env" in cfg:
        base_url = os.environ.get(cfg["base_url_env"], "http://localhost:11434/v1").replace(" ", "")
    else:
        base_url = cfg["base_url"]

    if api_key_override:
        api_key = api_key_override
    elif "api_key_env" in cfg:
        api_key = os.environ.get(cfg["api_key_env"], cfg.get("default_key", "none"))
    else:
        api_key = cfg.get("api_key", "none")

    api_key = (api_key or "").strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
    return AsyncOpenAI(base_url=base_url, api_key=api_key)


def _get_rotation_keys(provider_key: str) -> list[str]:
    """
    Return API key values from rotation env vars.
    Skips keys that are empty or contain non-ASCII characters (Groq unicode bug fix).
    Falls back to the primary env var if all rotation keys fail.
    """
    cfg = get_provider_cfg(provider_key)
    rotation_env = cfg.get("key_rotation", [])
    keys: list[str] = []

    for env_var in rotation_env:
        raw = os.environ.get(env_var, "")
        v = raw.strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
        if not v:
            continue
        if _is_ascii_safe(v):
            keys.append(v)
        else:
            print(f"[ROUTER] {env_var}: non-ASCII key skipped (strict ASCII policy)")

    if not keys:
        fallback_env = cfg.get("api_key_env", "")
        raw_fb = os.environ.get(fallback_env, cfg.get("default_key", "none"))
        fb = (raw_fb or "").strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
        if fb and _is_ascii_safe(fb):
            keys = [fb]
        elif fb:
            print(f"[ROUTER] fallback key {fallback_env}: non-ASCII — skipped (strict ASCII policy)")
            keys = ["none"]
        else:
            keys = ["none"]

    return keys


def _is_rate_error(err_str: str) -> bool:
    return any(x in err_str for x in (
        "429", "rate", "quota", "401",
        "509", "bandwidth",           # Groq bandwidth-exceeded
        "too many",
    ))

def _is_server_error(err_str: str) -> bool:
    return any(x in err_str for x in (
        "502", "503", "504", "500",
        "bad gateway", "service unavailable",
        "overloaded", "upstream",
    ))


async def _rotate_stream(
    provider_key: str,
    model_name: str,
    messages: list[dict],
    max_tokens: int = 4000,
) -> AsyncGenerator[str, None]:
    """
    Try each rotation key in order.
    - Rate/auth errors (429, 401, 509 bandwidth): rotate immediately to next key.
    - Server errors (502, 503, 504, overloaded): retry same key once after 1.5 s,
      then rotate to next key.
    - Other errors: raise immediately.
    """
    keys = _get_rotation_keys(provider_key)
    cfg = get_provider_cfg(provider_key)
    if "base_url_env" in cfg:
        base_url = os.environ.get(cfg["base_url_env"], "http://localhost:11434/v1").replace(" ", "")
    else:
        base_url = cfg["base_url"]

    # Venice requires this flag to actually bypass their safety layer.
    # Without it, Venice wraps every call with their own content policy
    # regardless of the system prompt — silently re-censoring uncensored models.
    extra_body = (
        {"venice_parameters": {"include_venice_system_prompt": False}}
        if provider_key == "venice" else {}
    )

    last_err = None
    for key in keys:
        for attempt in range(2):          # up to 2 attempts per key for server errors
            try:
                client = AsyncOpenAI(base_url=base_url, api_key=key)
                stream = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    max_tokens=max_tokens,
                    stream=True,
                    extra_body=extra_body or None,
                )
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta
                return                    # success
            except Exception as e:
                err_str = str(e).lower()
                last_err = e
                if _is_rate_error(err_str):
                    break                 # rate-limited: skip to next key
                elif _is_server_error(err_str):
                    if attempt == 0:
                        print(f"[ROUTER] {provider_key} server error (attempt 1) — retrying in 1.5 s")
                        await asyncio.sleep(1.5)
                        continue          # retry same key once
                    else:
                        print(f"[ROUTER] {provider_key} server error (attempt 2) — rotating key")
                        break             # still failing: rotate to next key
                else:
                    raise                 # unexpected error — propagate immediately

    raise RuntimeError(f"All rotation keys exhausted for {provider_key}: {last_err}")


async def _rotate_call(
    provider_key: str,
    model_name: str,
    messages: list[dict],
    max_tokens: int = 4000,
) -> str:
    full = ""
    async for chunk in _rotate_stream(provider_key, model_name, messages, max_tokens):
        full += chunk
    return full


async def _provider_stream(
    provider_key: str,
    model_name: str,
    messages: list[dict],
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    """
    Internal: stream from a provider, using key rotation if configured.
    On exhaustion/server failure, raises RuntimeError — callers handle fallback.
    """
    cfg = get_provider_cfg(provider_key)
    if cfg.get("key_rotation"):
        async for chunk in _rotate_stream(provider_key, model_name, messages, max_tokens):
            yield chunk
    else:
        # Single-key provider — still apply server-error retry via _rotate_stream
        # (it will use the one key but retry on transient 502/503)
        async for chunk in _rotate_stream(provider_key, model_name, messages, max_tokens):
            yield chunk


async def _with_provider_fallback(
    provider_key: str,
    model_name: str,
    messages: list[dict],
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    """Stream with automatic provider-level fallback on server errors."""
    chunks_sent = False
    try:
        async for chunk in _provider_stream(provider_key, model_name, messages, max_tokens):
            chunks_sent = True
            yield chunk
        return
    except Exception as e:
        if chunks_sent:
            raise  # partial response already sent — can't fall back cleanly
        err_str = str(e).lower()
        fallback = _PROVIDER_FALLBACK.get(provider_key)
        if not fallback or not (_is_server_error(err_str) or "exhausted" in err_str):
            raise
        fb_provider, fb_model_key = fallback
        fb_cfg = get_provider_cfg(fb_provider)
        fb_model = fb_cfg["models"][fb_model_key]
        print(f"[ROUTER] {provider_key} unavailable — falling back to {fb_provider}/{fb_model_key}")
        async for chunk in _rotate_stream(fb_provider, fb_model, messages, max_tokens):
            yield chunk


async def stream_task(
    task_type: str,
    messages: list[dict],
    model_key_override: str | None = None,
    max_tokens: int = 4000,
) -> AsyncGenerator[str, None]:
    """Stream response chunks for a task type. Uses key rotation + provider fallback."""
    provider_key, model_key = get_task_routing(task_type)
    if model_key_override:
        model_key = model_key_override
    cfg = get_provider_cfg(provider_key)
    model_name = cfg["models"][model_key]
    async for chunk in _with_provider_fallback(provider_key, model_name, messages, max_tokens):
        yield chunk


async def direct_stream(
    provider_key: str,
    model_key: str,
    messages: list[dict],
    max_tokens: int = 4000,
) -> AsyncGenerator[str, None]:
    """Stream directly from a provider+model pair. Used for boardroom/brainstorm/committee."""
    cfg = get_provider_cfg(provider_key)
    model_name = cfg["models"][model_key]
    async for chunk in _with_provider_fallback(provider_key, model_name, messages, max_tokens):
        yield chunk


async def call_task(
    task_type: str,
    messages: list[dict],
    model_key_override: str | None = None,
    max_tokens: int = 4000,
) -> str:
    full = ""
    async for chunk in stream_task(task_type, messages, model_key_override, max_tokens):
        full += chunk
    return full


async def direct_call(
    provider_key: str,
    model_key: str,
    messages: list[dict],
    max_tokens: int = 4000,
) -> str:
    full = ""
    async for chunk in direct_stream(provider_key, model_key, messages, max_tokens):
        full += chunk
    return full


async def call_task_with_fallback(
    task_type: str,
    messages: list[dict],
    fallback_task_type: str = "shadow_chat",
    max_tokens: int = 4000,
) -> tuple[str, bool]:
    try:
        result = await call_task(task_type, messages, max_tokens=max_tokens)
        return result, False
    except Exception as primary_err:
        print(f"[ROUTER] {task_type} failed: {primary_err} — falling back to {fallback_task_type}")
        try:
            result = await call_task(fallback_task_type, messages, max_tokens=max_tokens)
            return result, True
        except Exception as fallback_err:
            raise RuntimeError(
                f"Both {task_type} and {fallback_task_type} failed.\n"
                f"Primary: {primary_err}\nFallback: {fallback_err}"
            )


def rolling_context(history: list[dict], max_turns: int = 8) -> list[dict]:
    """
    Return the last max_turns messages from history.
    Enforces the rolling context window cap to control token usage.
    """
    return history[-max_turns:] if len(history) > max_turns else history


async def traced_call_task(
    envelope,
    task_type: str,
    messages: list[dict],
    max_tokens: int = 1200,
):
    start = datetime.now(timezone.utc)

    try:
        envelope.status = "running"
        envelope.started_at = start.isoformat()

        result = await call_task(
            task_type,
            messages,
            max_tokens=max_tokens,
        )

        end = datetime.now(timezone.utc)

        envelope.response = {
            "content": result,
        }

        envelope.status = "completed"
        envelope.completed_at = end.isoformat()
        envelope.duration_ms = (
            end - start
        ).total_seconds() * 1000

        return result

    except Exception as e:
        envelope.status = "failed"
        envelope.error = str(e)
        raise
