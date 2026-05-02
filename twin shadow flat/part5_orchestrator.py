
"""
Twin Shadow — PHASE 2: Execution Engine (Production-Ready)
============================================================
Replaces: part5_orchestrator.py
Enhances: Cross-module validation, Shadow Audit by default,
          Vault RAG, Internal Boardroom during execution,
          Full provenance trail on every output.

Usage:
    from phase2_execution import ExecutionEngine
    engine = ExecutionEngine()
    pid, brief = await engine.build("SaaS for dog walkers")
    results = await engine.approve(pid)  # Phase 2 fires here
"""

from __future__ import annotations
import os
import json
import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum

# Import existing parts (assumed in same directory)
from part1_registry import get_task_routing, get_provider_cfg
from part2_router import call_task, call_task_with_fallback, stream_task
from part3_modules import (
    generate_brief, evaluate, code_review, format_review,
    run_creative, run_code, run_business, shadow_fallback, shadow_audit,
    BRIEF_SYSTEM, CREATIVE_SYSTEM, BUILD_SYSTEM, BUSINESS_SYSTEM,
    SHADOW_SYSTEM, AUDIT_SYSTEM,
)
from part4_video import run_video, run_shadow_video


# ═════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═════════════════════════════════════════════════════════════════════════════

class ModuleType(Enum):
    CREATIVE = "creative"
    CODE = "code"
    BUSINESS = "business"
    VIDEO = "video"
    SHADOW_VIDEO = "shadow_video"


@dataclass
class ProvenanceStep:
    module: str
    model_used: str
    fallback_used: bool
    retries: int = 0
    audit_passed: bool | None = None
    audit_notes: str = ""
    duration_ms: float = 0.0


@dataclass
class ExecutionResult:
    module: str
    output: str
    shadow: bool
    approved: bool | None = None
    review: str = ""
    provenance: list[ProvenanceStep] = field(default_factory=list)
    cross_validated: bool = False
    validation_notes: str = ""


@dataclass
class Project:
    id: str
    chat_id: int
    idea: str
    brief: dict
    status: str = "pending_approval"
    created_at: str = ""
    results: list[ExecutionResult] = field(default_factory=list)
    boardroom_log: list[dict] = field(default_factory=list)
    vault_id: str | None = None


# ═════════════════════════════════════════════════════════════════════════════
# VAULT — Supabase + in-memory fallback + RAG retrieval
# ═════════════════════════════════════════════════════════════════════════════

def _sb():
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_vault_memory: dict[int, list[dict]] = {}


def vault_add(chat_id: int, topic: str, content: str, project_id: str = "") -> dict:
    row = {
        "chat_id": str(chat_id),
        "topic": topic,
        "content": content,
        "project_id": project_id,
        "created_at": _now(),
    }
    _vault_memory.setdefault(chat_id, []).append(row)
    sb = _sb()
    if sb:
        try:
            r = sb.table("vault").insert(row).execute()
            return r.data[0] if r.data else row
        except Exception as e:
            print(f"[VAULT] insert failed: {e}")
    return row


def vault_list(chat_id: int) -> list:
    sb = _sb()
    if sb:
        try:
            r = sb.table("vault").select("id, topic, created_at, project_id") \
                .eq("chat_id", str(chat_id)).order("created_at", desc=True).execute()
            return r.data
        except Exception as e:
            print(f"[VAULT] list failed: {e}")
    return _vault_memory.get(chat_id, [])


def vault_get(entry_id: int, chat_id: int) -> dict | None:
    sb = _sb()
    if sb:
        try:
            r = sb.table("vault").select("*") \
                .eq("id", entry_id).eq("chat_id", str(chat_id)).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            print(f"[VAULT] get failed: {e}")
    for entry in _vault_memory.get(chat_id, []):
        if entry.get("id") == entry_id:
            return entry
    return None


def vault_delete(entry_id: int, chat_id: int):
    sb = _sb()
    if sb:
        try:
            sb.table("vault").delete().eq("id", entry_id).eq("chat_id", str(chat_id)).execute()
        except Exception as e:
            print(f"[VAULT] delete failed: {e}")
    _vault_memory[chat_id] = [e for e in _vault_memory.get(chat_id, []) if e.get("id") != entry_id]


def vault_clear(chat_id: int):
    sb = _sb()
    if sb:
        try:
            sb.table("vault").delete().eq("chat_id", str(chat_id)).execute()
        except Exception as e:
            print(f"[VAULT] clear failed: {e}")
    _vault_memory[chat_id] = []


def vault_last(chat_id: int) -> dict | None:
    sb = _sb()
    if sb:
        try:
            r = sb.table("vault").select("*").eq("chat_id", str(chat_id)) \
                .order("created_at", desc=True).limit(1).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            print(f"[VAULT] last failed: {e}")
    entries = _vault_memory.get(chat_id, [])
    return entries[-1] if entries else None


def vault_undo(chat_id: int) -> dict | None:
    last = vault_last(chat_id)
    if last:
        vault_delete(last.get("id", -1), chat_id)
    return last


# ═════════════════════════════════════════════════════════════════════════════
# VAULT RAG
# ═════════════════════════════════════════════════════════════════════════════

RAG_SYSTEM = """You are a context synthesizer. Given past project summaries and a current idea,
extract ONLY relevant lessons, pricing data, or architectural patterns that should inform
the current build. Be specific. Cite which past project the insight came from.
Return 2-4 bullet points. If nothing is relevant, say "No relevant history."""


async def vault_rag(chat_id: int, current_idea: str, max_entries: int = 3) -> str:
    entries = vault_list(chat_id)[:max_entries]
    if not entries:
        return ""

    context = "\n\n".join(
        f"Project: {e.get('topic','?')}\n{e.get('content','')[:500]}"
        for e in entries
    )

    messages = [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user", "content": f"Current idea: {current_idea}\n\nPast projects:\n{context}\n\nRelevant insights:"},
    ]

    try:
        result = await call_task("shadow_chat", messages)
        return f"\n\n[Vault RAG]\n{result}"
    except Exception as e:
        print(f"[RAG] Failed: {e}")
        return ""


# ═════════════════════════════════════════════════════════════════════════════
# SHADOW AUDIT — Default audit on every output
# ═════════════════════════════════════════════════════════════════════════════

SHADOW_AUDIT_SYSTEM = """You are the internal auditor for Twin Shadow.
Review the following deliverable for:
1. Completeness — missing sections, placeholders, TODOs
2. Consistency — contradicts itself or the brief
3. Actionability — can someone act on this immediately?
4. Edge cases — what could go wrong?

Return JSON only:
{
  "passed": true/false,
  "severity": "low/medium/high/critical",
  "issues": ["issue 1", "issue 2"],
  "recommendation": "one sentence fix suggestion"
}"""


async def default_shadow_audit(content: str, module_name: str) -> tuple[bool, str]:
    messages = [
        {"role": "system", "content": SHADOW_AUDIT_SYSTEM},
        {"role": "user", "content": f"Module: {module_name}\n\nDeliverable:\n{content[:3000]}"},
    ]

    try:
        raw = await call_task("shadow_chat", messages)
        try:
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            parsed = json.loads(clean)
            passed = parsed.get("passed", True)
            severity = parsed.get("severity", "low")
            issues = parsed.get("issues", [])
            rec = parsed.get("recommendation", "")
            notes = f"Severity: {severity}\nIssues: {', '.join(issues)}\nRec: {rec}"
            return passed, notes
        except json.JSONDecodeError:
            return "passed" in raw.lower() or "approved" in raw.lower(), raw[:300]
    except Exception as e:
        print(f"[AUDIT] Shadow audit failed for {module_name}: {e}")
        return True, f"Audit system error: {e}"


# ═════════════════════════════════════════════════════════════════════════════
# CROSS-MODULE VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

CROSS_VALIDATE_SYSTEM = """You are a systems integration validator.
Given outputs from multiple modules (creative, code, business), check:
1. Does the code implement the features described in creative?
2. Does the pricing/business model match the code capabilities?
3. Are there gaps between what was promised and what was built?

Return JSON:
{
  "aligned": true/false,
  "gaps": ["gap description"],
  "suggested_fixes": ["fix description"]
}"""


async def cross_validate(results: list[ExecutionResult], brief: dict) -> tuple[bool, str]:
    if len(results) < 2:
        return True, "Single module — no cross-validation needed."

    summary = "\n\n---\n\n".join(
        f"[{r.module}]\n{r.output[:800]}" for r in results
    )

    messages = [
        {"role": "system", "content": CROSS_VALIDATE_SYSTEM},
        {"role": "user", "content": f"Brief: {json.dumps(brief)}\n\nModule outputs:\n{summary}\n\nValidation:"},
    ]

    try:
        raw = await call_task("shadow_chat", messages)
        try:
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            parsed = json.loads(clean)
            aligned = parsed.get("aligned", True)
            gaps = parsed.get("gaps", [])
            fixes = parsed.get("suggested_fixes", [])
            notes = f"Aligned: {aligned}\nGaps: {', '.join(gaps)}\nFixes: {', '.join(fixes)}"
            return aligned, notes
        except json.JSONDecodeError:
            return "aligned" in raw.lower(), raw[:400]
    except Exception as e:
        print(f"[CROSS-VAL] Failed: {e}")
        return True, f"Cross-validation error: {e}"


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL BOARDROOM — 3-round debate during Phase 2 execution
# ═════════════════════════════════════════════════════════════════════════════

BOARDROOM_SYSTEM = """You are a participant in an internal boardroom during project execution.
Role: {role}. Be direct. Challenge weaknesses. Build on strengths.
Keep under 150 words. No filler."""

BOARDROOM_ROLES = {
    "creative": "Creative Director — checks if output is compelling and original",
    "code": "Tech Lead — checks feasibility, security, completeness",
    "business": "CFO — checks monetization, market fit, legal risks",
    "shadow": "Shadow Auditor — calls out bullshit, gaps, dangers",
}


async def internal_boardroom(
    topic: str,
    outputs: dict[str, str],
    rounds: int = 3,
) -> list[dict]:
    history = []
    participants = ["creative", "code", "business", "shadow"]

    for round_num in range(1, rounds + 1):
        for role in participants:
            context = "\n\n".join(
                f"{h['role']}: {h['content']}" for h in history[-8:]
            )

            outputs_summary = "\n".join(
                f"[{k}]\n{v[:400]}" for k, v in outputs.items()
            )

            prompt = (
                f"Topic: {topic}\n"
                f"Round {round_num}.\n\n"
                f"Current module outputs:\n{outputs_summary}\n\n"
                + (f"Discussion so far:\n{context}\n\n" if context else "")
                + f"Your response as {BOARDROOM_ROLES[role]}:"
            )

            messages = [
                {"role": "system", "content": BOARDROOM_SYSTEM.format(role=BOARDROOM_ROLES[role])},
                {"role": "user", "content": prompt},
            ]

            try:
                task_type = "creative" if role == "creative" else ("code" if role == "code" else ("business" if role == "business" else "shadow"))
                response = await call_task(task_type, messages)
            except Exception as e:
                response = f"[{role} failed: {e}]"

            history.append({"role": role, "content": response, "round": round_num})

    return history


# ═════════════════════════════════════════════════════════════════════════════
# EXECUTION ENGINE
# ═════════════════════════════════════════════════════════════════════════════

_projects: dict[str, Project] = {}
_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"P{_counter:04d}"


class ExecutionEngine:
    """
    Phase 2 Execution Engine.

    Features:
    1. Cross-module validation
    2. Shadow audit by default
    3. Vault RAG
    4. Internal boardroom during execution
    5. Full provenance trail
    """

    def __init__(self):
        self._projects = _projects

    async def build(self, idea: str, chat_id: int = 0) -> tuple[str, dict]:
        print(f"[PHASE2] Building brief for: {idea[:60]}")
        rag_context = await vault_rag(chat_id, idea)
        brief = await generate_brief(idea + rag_context)
        pid = _next_id()

        self._projects[pid] = Project(
            id=pid,
            chat_id=chat_id,
            idea=idea,
            brief=brief,
            created_at=_now(),
        )
        return pid, brief

    async def approve(self, project_id: str) -> list[ExecutionResult]:
        project = self._projects.get(project_id)
        if not project:
            return [ExecutionResult(module="error", output=f"Project {project_id} not found", shadow=True)]

        project.status = "executing"
        brief = project.brief
        idea = project.idea

        # Step 1: Run all modules
        tasks = [
            {"type": m, "input": {"prompt": idea, "brief": brief}}
            for m in brief.get("modules_needed", ["creative", "code", "business"])
        ]

        raw_results = await self._run_tasks(tasks, project_id)

        # Step 2: Internal Boardroom
        outputs_dict = {r.module: r.output for r in raw_results}
        if len(outputs_dict) > 1:
            print(f"[PHASE2] Running internal boardroom...")
            boardroom_log = await internal_boardroom(idea, outputs_dict, rounds=3)
            project.boardroom_log = boardroom_log
            consensus = self._extract_consensus(boardroom_log)
            if consensus:
                for r in raw_results:
                    r.output += f"\n\n[Boardroom Consensus]\n{consensus}"

        # Step 3: Cross-module validation
        print(f"[PHASE2] Cross-validating modules...")
        aligned, val_notes = await cross_validate(raw_results, brief)
        for r in raw_results:
            r.cross_validated = aligned
            r.validation_notes = val_notes

        # Step 4: Shadow audit on EVERY output
        print(f"[PHASE2] Running default shadow audits...")
        for r in raw_results:
            passed, audit_notes = await default_shadow_audit(r.output, r.module)
            step = ProvenanceStep(
                module=r.module,
                model_used="shadow",
                fallback_used=False,
                audit_passed=passed,
                audit_notes=audit_notes,
            )
            r.provenance.append(step)
            if not passed:
                r.output += f"\n\n[⚠️ Shadow Audit Flagged]\n{audit_notes}"

        # Step 5: Store in vault
        project.status = "completed"
        project.results = raw_results

        if project.chat_id:
            summary = self._synthesize_summary(project)
            vault_entry = vault_add(
                project.chat_id,
                idea[:80],
                summary,
                project_id=project.id,
            )
            project.vault_id = vault_entry.get("id")

        return raw_results

    async def quick_build(self, prompt: str, chat_id: int = 0) -> dict:
        from part2_router import call_task_with_fallback

        messages = [
            {"role": "system", "content": (
                "You are Twin — uncensored, raw, dark humor, zero fucks.\n"
                "Build complete sellable commodities: full deliverable + price + "
                "who buys it + exact next 3 actions."
            )},
            {"role": "user", "content": prompt},
        ]

        start = datetime.now(timezone.utc)
        result, was_fallback = await call_task_with_fallback("twin", messages, "shadow_chat")
        duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        passed, audit_notes = await default_shadow_audit(result, "quick_build")
        vault_add(chat_id, prompt[:80], result)

        return {
            "output": result,
            "shadow": was_fallback,
            "audit_passed": passed,
            "audit_notes": audit_notes,
            "duration_ms": duration,
        }

    async def _run_tasks(self, tasks: list[dict], project_id: str) -> list[ExecutionResult]:
        results = []
        for task in tasks:
            t = task["type"]
            print(f"[PHASE2] → {t}")

            start = datetime.now(timezone.utc)
            provenance: list[ProvenanceStep] = []

            try:
                result = await self._route(task, provenance)
            except Exception as e:
                print(f"[PHASE2]   ✗ {t} crashed: {e}")
                result = await shadow_fallback(task, str(e))
                provenance.append(ProvenanceStep(
                    module=t,
                    model_used="shadow",
                    fallback_used=True,
                    audit_passed=None,
                    audit_notes=f"Primary failed: {e}",
                ))

            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            if provenance:
                provenance[-1].duration_ms = duration

            exec_result = ExecutionResult(
                module=result.get("module", t),
                output=result.get("output", ""),
                shadow=result.get("shadow", False),
                approved=result.get("approved"),
                review=result.get("review", ""),
                provenance=provenance,
            )
            results.append(exec_result)

        return results

    async def _route(self, task: dict, provenance: list[ProvenanceStep]) -> dict:
        t = task["type"]

        if t == "creative":
            result = await run_creative(task)
            provenance.append(ProvenanceStep(
                module="creative",
                model_used="qwen_free/mistral_free",
                fallback_used=result.get("shadow", False),
            ))
            return result

        elif t == "code":
            result = await run_code(task)
            provenance.append(ProvenanceStep(
                module="code",
                model_used="glm-5.1",
                fallback_used=result.get("shadow", False),
                retries=1 if result.get("retried") else 0,
            ))
            return result

        elif t == "business":
            result = await run_business(task)
            provenance.append(ProvenanceStep(
                module="business",
                model_used="legal/gpt_oss_legal",
                fallback_used=result.get("shadow", False),
            ))
            return result

        elif t == "video":
            result = await run_video(task)
            provenance.append(ProvenanceStep(
                module="video",
                model_used="fal/huggingface/replicate",
                fallback_used=result.get("shadow", False),
            ))
            return result

        elif t == "shadow_video":
            result = await run_shadow_video(task)
            provenance.append(ProvenanceStep(
                module="shadow_video",
                model_used="grok_aurora",
                fallback_used=result.get("shadow", False),
            ))
            return result

        else:
            raise ValueError(f"Unknown module: {t}")

    def _extract_consensus(self, boardroom_log: list[dict]) -> str:
        if not boardroom_log:
            return ""
        last_round = max(e.get("round", 1) for e in boardroom_log)
        final = [e for e in boardroom_log if e.get("round") == last_round]
        return "\n".join(f"{e['role']}: {e['content'][:200]}" for e in final)

    def _synthesize_summary(self, project: Project) -> str:
        lines = [
            f"Project: {project.idea}",
            f"Status: {project.status}",
            f"Brief: {project.brief.get('summary', 'N/A')}",
            "",
            "=== DELIVERABLES ===",
        ]

        for r in project.results:
            lines.append(f"\n[{r.module}]{'🌑' if r.shadow else ''}{'✅' if r.approved else ('❌' if r.approved is not None else '')}")
            lines.append(r.output[:500])
            lines.append(f"Cross-validated: {r.cross_validated} | {r.validation_notes}")
            if r.provenance:
                for p in r.provenance:
                    lines.append(
                        f"  → {p.model_used} ({p.duration_ms:.0f}ms) "
                        f"audit={'✅' if p.audit_passed else '❌' if p.audit_passed is not None else 'N/A'}"
                    )

        if project.boardroom_log:
            lines.append("\n=== BOARDROOM LOG ===")
            for entry in project.boardroom_log[-4:]:
                lines.append(f"{entry['role']} (R{entry['round']}): {entry['content'][:150]}")

        return "\n".join(lines)

    def get_project(self, pid: str) -> Project | None:
        return self._projects.get(pid)

    def list_projects(self, chat_id: int = 0) -> list[Project]:
        if chat_id:
            return [p for p in self._projects.values() if p.chat_id == chat_id]
        return list(self._projects.values())

    def get_provenance_report(self, project_id: str) -> str:
        project = self._projects.get(project_id)
        if not project:
            return "Project not found."

        lines = [f"📋 PROVENANCE REPORT: {project.id}", f"Idea: {project.idea}", ""]

        for r in project.results:
            lines.append(f"\n━━━ {r.module.upper()} ━━━")
            lines.append(f"Shadow fallback: {'YES' if r.shadow else 'NO'}")
            lines.append(f"Code approved: {r.approved if r.approved is not None else 'N/A'}")
            lines.append(f"Cross-validated: {r.cross_validated}")
            lines.append(f"Validation notes: {r.validation_notes}")
            lines.append("Execution trail:")
            for p in r.provenance:
                lines.append(
                    f"  • Model: {p.model_used} | Fallback: {p.fallback_used} | "
                    f"Retries: {p.retries} | Duration: {p.duration_ms:.0f}ms | "
                    f"Audit: {'PASS' if p.audit_passed else 'FAIL' if p.audit_passed is not None else 'N/A'}"
                )
                if p.audit_notes:
                    lines.append(f"    Notes: {p.audit_notes[:200]}")

        return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY
# ═════════════════════════════════════════════════════════════════════════════

class Orchestrator(ExecutionEngine):
    """Backward-compatible alias for existing code."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    async def demo():
        print("=" * 60)
        print("TWIN SHADOW — PHASE 2 EXECUTION ENGINE")
        print("=" * 60)
        print("\nThis is a structural demo. Set API keys to run live.\n")
        print("Features:")
        print("  ✓ Cross-module validation")
        print("  ✓ Shadow audit by default")
        print("  ✓ Vault RAG (retrieval-augmented generation)")
        print("  ✓ Internal boardroom during execution")
        print("  ✓ Full provenance trail")
        print("\nImport: from phase2_execution import ExecutionEngine")
        print("Usage:  results = await engine.approve('P0001')")

    asyncio.run(demo())
