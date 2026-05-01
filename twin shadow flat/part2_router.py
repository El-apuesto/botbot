"""
Twin Shadow — Part 2: LLM Router
Universal async router for all OpenAI-compatible providers.
"""

from __future__ import annotations
import os
from typing import AsyncGenerator
from openai import AsyncOpenAI

from part1_registry import get_task_routing, get_provider_cfg


def _build_client(provider_key: str) -> AsyncOpenAI:
    cfg = get_provider_cfg(provider_key)

    if not cfg.get("openai_compat"):
        raise ValueError(f"{provider_key} is not OpenAI-compatible. Use its native module.")

    if "base_url_env" in cfg:
        base_url = os.environ.get(cfg["base_url_env"], "http://localhost:11434/v1")
    else:
        base_url = cfg["base_url"]

    if "api_key_env" in cfg:
        api_key = os.environ.get(cfg["api_key_env"], cfg.get("default_key", "none"))
    else:
        api_key = cfg.get("api_key", "none")

    return AsyncOpenAI(base_url=base_url, api_key=api_key)


async def stream_task(
    task_type: str,
    messages: list[dict],
    model_key_override: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response chunks for a task type."""
    provider_key, model_key = get_task_routing(task_type)
    if model_key_override:
        model_key = model_key_override

    cfg        = get_provider_cfg(provider_key)
    model_name = cfg["models"][model_key]
    client     = _build_client(provider_key)

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


async def call_task(
    task_type: str,
    messages: list[dict],
    model_key_override: str | None = None,
) -> str:
    """Non-streaming — collect full response."""
    full = ""
    async for chunk in stream_task(task_type, messages, model_key_override):
        full += chunk
    return full


async def call_task_with_fallback(
    task_type: str,
    messages: list[dict],
    fallback_task_type: str = "shadow",
) -> tuple[str, bool]:
    """Try primary, fall back on failure. Returns (result, was_fallback)."""
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
