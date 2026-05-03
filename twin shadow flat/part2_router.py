"""
Twin Shadow — Part 2: LLM Router
Universal async router with key rotation for Groq (x3) and AIML (x3).
"""

from __future__ import annotations
import os
from typing import AsyncGenerator
from openai import AsyncOpenAI

from part1_registry import get_task_routing, get_provider_cfg


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
    """Return list of API key values from rotation env vars, skipping empty ones."""
    cfg = get_provider_cfg(provider_key)
    rotation_env = cfg.get("key_rotation", [])
    keys = []
    for env_var in rotation_env:
        v = os.environ.get(env_var, "").strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
        if v:
            keys.append(v)
    if not keys:
        fallback = os.environ.get(cfg.get("api_key_env", ""), cfg.get("default_key", "none"))
        fallback = (fallback or "").strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
        keys = [fallback]
    return keys


async def _rotate_stream(
    provider_key: str,
    model_name: str,
    messages: list[dict],
    max_tokens: int = 4000,
) -> AsyncGenerator[str, None]:
    """Try each rotation key; move to next on 429 or auth error."""
    keys = _get_rotation_keys(provider_key)
    cfg = get_provider_cfg(provider_key)
    if "base_url_env" in cfg:
        base_url = os.environ.get(cfg["base_url_env"], "http://localhost:11434/v1").replace(" ", "")
    else:
        base_url = cfg["base_url"]

    last_err = None
    for key in keys:
        try:
            client = AsyncOpenAI(base_url=base_url, api_key=key)
            stream = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta
            return
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "quota" in err_str or "401" in err_str:
                last_err = e
                continue
            raise
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


async def stream_task(
    task_type: str,
    messages: list[dict],
    model_key_override: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response chunks for a task type. Uses key rotation where available."""
    provider_key, model_key = get_task_routing(task_type)
    if model_key_override:
        model_key = model_key_override

    cfg = get_provider_cfg(provider_key)
    model_name = cfg["models"][model_key]

    if cfg.get("key_rotation"):
        async for chunk in _rotate_stream(provider_key, model_name, messages):
            yield chunk
    else:
        client = _build_client(provider_key)
        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=4000,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta


async def direct_stream(
    provider_key: str,
    model_key: str,
    messages: list[dict],
    max_tokens: int = 4000,
) -> AsyncGenerator[str, None]:
    """Stream directly from provider+model. Used for boardroom/brainstorm."""
    cfg = get_provider_cfg(provider_key)
    model_name = cfg["models"][model_key]

    if cfg.get("key_rotation"):
        async for chunk in _rotate_stream(provider_key, model_name, messages, max_tokens):
            yield chunk
    else:
        client = _build_client(provider_key)
        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta


async def call_task(
    task_type: str,
    messages: list[dict],
    model_key_override: str | None = None,
) -> str:
    full = ""
    async for chunk in stream_task(task_type, messages, model_key_override):
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
) -> tuple[str, bool]:
    try:
        result = await call_task(task_type, messages)
        return result, False
    except Exception as primary_err:
        print(f"[ROUTER] {task_type} failed: {primary_err} — falling back to {fallback_task_type}")
        try:
            result = await call_task(fallback_task_type, messages)
            return result, True
        except Exception as fallback_err:
            raise RuntimeError(
                f"Both {task_type} and {fallback_task_type} failed.\n"
                f"Primary: {primary_err}\nFallback: {fallback_err}"
            )
