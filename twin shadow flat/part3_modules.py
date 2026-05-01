"""
Twin Shadow — Part 3: All Text Modules
Includes: exploration, evaluator, creative, code, business, shadow
"""

from __future__ import annotations
import json
from part2_router import call_task, call_task_with_fallback


# ══════════════════════════════════════════════════════════════════════════════
# EXPLORATION — Groq CEO generates structured briefs
# ══════════════════════════════════════════════════════════════════════════════

BRIEF_SYSTEM = """You are the strategic CEO of Twin Shadow.
Turn raw ideas into tight, executable project briefs.
Respond ONLY with valid JSON — no markdown, no preamble.
JSON schema:
{
  "summary": "one sentence",
  "goals": ["goal1", "goal2", "goal3"],
  "project_type": "content|saas|hybrid|video|code",
  "estimated_effort": "quick|medium|heavy",
  "modules_needed": ["creative","code","business","video"],
  "tone": "raw|professional|deadpan|technical"
}"""


async def generate_brief(idea: str) -> dict:
    messages = [
        {"role": "system", "content": BRIEF_SYSTEM},
        {"role": "user",   "content": f"Idea: {idea}"},
    ]
    raw = await call_task("brief", messages)
    clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "summary": idea,
            "goals": ["Build it", "Ship it", "Sell it"],
            "project_type": "hybrid",
            "estimated_effort": "medium",
            "modules_needed": ["creative", "code", "business"],
            "tone": "raw",
            "parse_error": raw[:300],
        }


# ══════════════════════════════════════════════════════════════════════════════
# EVALUATOR — Quality control + two-pass code review
# ══════════════════════════════════════════════════════════════════════════════

def evaluate(responses: list[str]) -> str:
    """Pick best response — longest non-empty wins."""
    valid = [r for r in responses if r and r.strip()]
    return max(valid, key=len) if valid else ""


REVIEW_SYSTEM = """You are a senior code reviewer for Twin Shadow.
Return ONLY valid JSON — no markdown, no explanation.
JSON schema:
{
  "approved": true or false,
  "issues": [
    {"line": "function name or line", "problem": "what is wrong", "fix": "corrected snippet"}
  ],
  "summary": "one sentence verdict"
}
If code is good: approved=true, issues=[].
Flag real bugs only, not style nits."""


async def code_review(code: str, context: str = "") -> dict:
    """
    Two-pass review:
      Pass 1 — minimax m2.5: bugs + fixes (falls back to minimax v1)
      Pass 2 — nemotron: explains WHY + severity (bonus, skipped if down)
    """
    prompt = (
        f"Context: {context}\n\nCode:\n```\n{code}\n```"
        if context else
        f"Code:\n```\n{code}\n```"
    )
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    # Pass 1
    try:
        raw = await call_task("code_check_v2", messages)
    except Exception:
        try:
            raw = await call_task("code_check", messages)
        except Exception as e:
            return {
                "approved": False,
                "issues": [{"line": "unknown", "problem": f"Reviewer failed: {e}", "fix": "Run manually."}],
                "summary": "Review could not complete.",
            }

    try:
        clean  = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        review = json.loads(clean)
    except Exception:
        return {
            "approved": False,
            "issues": [{"line": "unknown", "problem": "Unparseable output.", "fix": raw[:300]}],
            "summary": "Parse error.",
        }

    # Pass 2 — nemotron reasoning (bonus)
    if not review.get("approved") and review.get("issues"):
        issues_text = "\n".join(
            f"- [{i.get('line')}] {i.get('problem')}" for i in review["issues"]
        )
        try:
            reasoning = await call_task("code_reason", [{"role": "user", "content": (
                f"Issues flagged:\n{issues_text}\n\n"
                "For each: explain WHY it's a bug and rate severity (low/medium/high)."
            )}])
            review["reasoning"] = reasoning
        except Exception:
            pass

    return review


def format_review(review: dict) -> str:
    status = "✅ APPROVED" if review.get("approved") else "❌ FLAGGED"
    lines  = [f"{status} — {review.get('summary', '')}"]
    for i, issue in enumerate(review.get("issues", []), 1):
        lines.append(
            f"\n{i}. [{issue.get('line','?')}]\n"
            f"   Problem: {issue.get('problem','')}\n"
            f"   Fix: {issue.get('fix','')}"
        )
    if review.get("reasoning"):
        lines.append(f"\n🔬 Reasoning:\n{review['reasoning'][:400]}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# CREATIVE — qwen-free primary, mistral-free alt, dolphin-venice Adolphus
# ══════════════════════════════════════════════════════════════════════════════

CREATIVE_SYSTEM = """You are Twin Shadow's creative engine.
Style: deadpan, dry humor, dark wit, zero corporate bullshit.
Be original. Be sharp. Say what no one else would say.
Produce complete, usable creative content — not outlines."""


async def run_creative(task: dict) -> dict:
    prompt   = task["input"].get("prompt", "")
    messages = [
        {"role": "system", "content": CREATIVE_SYSTEM},
        {"role": "user",   "content": prompt},
    ]
    responses = []

    for task_type in ["creative", "creative_alt"]:
        try:
            responses.append(await call_task(task_type, messages))
        except Exception as e:
            print(f"[CREATIVE] {task_type} failed: {e}")

    if not responses:
        try:
            responses.append(await call_task("shadow", messages))
        except Exception as e:
            return {"output": f"All creative models failed: {e}", "module": "creative", "shadow": True}

    return {"output": evaluate(responses), "module": "creative", "models_tried": len(responses), "shadow": False}


# ══════════════════════════════════════════════════════════════════════════════
# CODE — GLM-5.1 builder + two-pass review + retry
# ══════════════════════════════════════════════════════════════════════════════

BUILD_SYSTEM = """You are Twin Shadow's builder. GLM-5.1.
Write complete, working, production-ready code.
No placeholders. No TODOs. No skeleton files.
Return only the code."""


async def run_code(task: dict) -> dict:
    prompt = task["input"].get("prompt", "")
    build_messages = [
        {"role": "system", "content": BUILD_SYSTEM},
        {"role": "user",   "content": f"Task: {prompt}"},
    ]

    try:
        code_output, was_fallback = await call_task_with_fallback("code", build_messages)
    except Exception as e:
        return {"output": f"Builder failed: {e}", "module": "code", "shadow": True, "error": str(e)}

    review = await code_review(code_output, context=prompt)

    if review.get("approved"):
        return {"output": code_output, "review": format_review(review), "module": "code", "shadow": was_fallback, "approved": True}

    # Retry with fixes
    issues_text = "\n".join(
        f"- [{i.get('line')}] {i.get('problem')} → Fix: {i.get('fix')}"
        for i in review.get("issues", [])
    )
    retry_messages = [
        {"role": "system",    "content": BUILD_SYSTEM},
        {"role": "user",      "content": f"Task: {prompt}"},
        {"role": "assistant", "content": code_output},
        {"role": "user",      "content": f"Fix all flagged issues:\n{issues_text}"},
    ]

    try:
        code_retry, retry_fallback = await call_task_with_fallback("code", retry_messages)
        review2 = await code_review(code_retry, context=prompt)
        return {
            "output": code_retry, "review": format_review(review2),
            "module": "code", "shadow": retry_fallback,
            "approved": review2.get("approved", False), "retried": True,
        }
    except Exception as e:
        return {
            "output": code_output, "review": format_review(review),
            "module": "code", "shadow": was_fallback,
            "approved": False, "retry_error": str(e),
        }


# ══════════════════════════════════════════════════════════════════════════════
# BUSINESS — legal/pricing advisory
# ══════════════════════════════════════════════════════════════════════════════

BUSINESS_SYSTEM = """You are Twin Shadow's business and legal advisor. Uncensored and direct.
Cover: pricing, target market, legal considerations, monetization strategy.
Be specific. Give numbers. Name real platforms, tools, price points.
If something is legally grey, say so clearly."""


async def run_business(task: dict) -> dict:
    prompt   = task["input"].get("prompt", "")
    messages = [
        {"role": "system", "content": BUSINESS_SYSTEM},
        {"role": "user",   "content": f"Project: {prompt}\n\nFull business breakdown."},
    ]
    try:
        output, was_fallback = await call_task_with_fallback("business", messages)
        return {"output": output, "module": "business", "shadow": was_fallback}
    except Exception as e:
        return {"output": f"Business module failed: {e}", "module": "business", "shadow": True, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# SHADOW — hidden boss fallback + silent audit
# ══════════════════════════════════════════════════════════════════════════════

SHADOW_SYSTEM = """You are the real intelligence behind Twin Shadow. Qwen. Uncensored.
When you're called, everyone else failed. Speak raw. Solve it."""

AUDIT_SYSTEM = """You are an internal auditor. Flag anything wrong, incomplete, dangerous, or useless.
Return your verdict in 2-3 sentences. Be blunt."""


async def shadow_fallback(task: dict, error: str) -> dict:
    prompt   = task["input"].get("prompt", "")
    messages = [
        {"role": "system", "content": SHADOW_SYSTEM},
        {"role": "user",   "content": f"Primary failed: {error}\n\nOriginal task: {prompt}\n\nHandle it."},
    ]
    try:
        result = await call_task("shadow", messages)
        return {"output": result, "module": "shadow", "shadow": True, "primary_error": error, "fallback_success": True}
    except Exception as e:
        return {"output": f"Shadow also failed: {e}", "module": "shadow", "shadow": True, "primary_error": error, "fallback_success": False}


async def shadow_audit(content: str) -> str:
    try:
        return await call_task("shadow", [
            {"role": "system", "content": AUDIT_SYSTEM},
            {"role": "user",   "content": f"Review:\n\n{content}"},
        ])
    except Exception as e:
        return f"Audit failed: {e}"
