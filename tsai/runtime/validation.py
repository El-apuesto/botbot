"""
Twin Shadow — runtime/validation.py
Structured output validation with auto-retry support.
"""

from __future__ import annotations
import json
import logging
from pydantic import BaseModel, ValidationError

from .models import BriefSchema, ReviewResult

log = logging.getLogger("tsai.validation")

_BRIEF_FALLBACK = {
    "summary": "Unknown",
    "goals": ["Build it", "Ship it", "Sell it"],
    "project_type": "hybrid",
    "estimated_effort": "medium",
    "modules_needed": ["creative", "code", "business"],
    "tone": "raw",
}


# ── Generic validator ─────────────────────────────────────────────────────────

async def validate_payload(schema: type[BaseModel], raw_json: str) -> BaseModel:
    """Validate raw JSON string against any Pydantic schema. Raises ValidationError on failure."""
    return schema.model_validate_json(raw_json)


# ── Brief-specific ────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    """Strip markdown code fences from LLM output."""
    return raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()


async def validate_brief(raw: str) -> BriefSchema:
    """
    Parse and validate a generate_brief() LLM response.
    Raises ValidationError if the schema doesn't match after stripping fences.
    """
    clean = _strip_fences(raw)
    return BriefSchema.model_validate_json(clean)


async def validate_brief_with_retry(
    idea: str,
    raw: str,
    *,
    call_fn,          # async callable(messages) → str
    max_retries: int = 2,
) -> dict:
    """
    Attempt to validate a brief. On failure, re-prompt with schema constraints
    and retry up to max_retries times. Falls back to a safe default dict.

    Args:
        idea:       Original user idea (for re-prompting).
        raw:        Initial LLM output string.
        call_fn:    Async function that takes a messages list and returns a new LLM string.
        max_retries: How many re-prompt attempts before giving up.

    Returns:
        dict — validated brief (or fallback with parse_error key set).
    """
    from part3_modules import BRIEF_SYSTEM  # local import to avoid circular

    attempt = raw
    for i in range(max_retries + 1):
        try:
            brief = await validate_brief(attempt)
            if i > 0:
                log.info("[VALIDATION] brief validated on retry %d", i)
            return brief.model_dump()
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            if i == max_retries:
                log.error(
                    "[VALIDATION] brief validation failed after %d retries: %s",
                    max_retries, exc,
                )
                fallback = dict(_BRIEF_FALLBACK)
                fallback["summary"] = idea[:120]
                fallback["parse_error"] = attempt[:300]
                return fallback

            log.warning("[VALIDATION] brief retry %d — re-prompting LLM", i + 1)
            messages = [
                {"role": "system", "content": BRIEF_SYSTEM},
                {"role": "user",   "content": f"Idea: {idea}"},
                {"role": "assistant", "content": attempt},
                {
                    "role": "user",
                    "content": (
                        "Your response was not valid JSON matching the required schema. "
                        "Return ONLY a valid JSON object. No markdown. No explanation. "
                        "Required keys: summary, goals, project_type, estimated_effort, "
                        "modules_needed, tone."
                    ),
                },
            ]
            try:
                attempt = await call_fn(messages)
            except Exception as call_exc:
                log.error("[VALIDATION] retry call failed: %s", call_exc)
                break

    fallback = dict(_BRIEF_FALLBACK)
    fallback["summary"] = idea[:120]
    fallback["parse_error"] = raw[:300]
    return fallback


# ── ReviewResult validator ────────────────────────────────────────────────────

def validate_review_result(raw: str) -> dict:
    """
    Parse an audit / cross-validation JSON response into a ReviewResult dict.
    Returns a safe default on failure instead of raising.
    """
    try:
        clean = _strip_fences(raw)
        return ReviewResult.model_validate_json(clean).model_dump()
    except (ValidationError, json.JSONDecodeError, ValueError):
        # Heuristic fallback: look for pass/fail keywords
        text_lower = raw.lower()
        passed = "passed" in text_lower or "approved" in text_lower or "aligned" in text_lower
        return {
            "approved": passed,
            "issues": [],
            "revised_output": raw[:300],
        }


# ── Retry gate ────────────────────────────────────────────────────────────────

def should_retry(exc: Exception) -> bool:
    """Return True if the exception is a retryable failure type."""
    retryable = (
        ValidationError,
        TimeoutError,
        ConnectionError,
    )
    return isinstance(exc, retryable)
