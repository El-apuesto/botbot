"""
Twin Shadow — Part 15: Autonomous Mission Engine
The app runs research missions in the background without user intervention.
Results persist to disk. Users create a mission and come back to finished work.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_MISSIONS_DIR = Path(__file__).parent / "missions"
_MISSIONS_DIR.mkdir(exist_ok=True)

# ── Status values ──────────────────────────────────────────────────────────────
STATUS_PENDING   = "pending"
STATUS_RUNNING   = "running"
STATUS_DONE      = "done"
STATUS_ERROR     = "error"

# ── Mission types ──────────────────────────────────────────────────────────────
MISSION_TYPES = {
    "gorilla_marketing": {
        "label":   "Gorilla Marketing Blueprint",
        "desc":    "6-specialist committee + TWIN blueprint. Moneyball philosophy.",
        "icon":    "⚡",
    },
    "boardroom_analysis": {
        "label":   "Full Boardroom Analysis",
        "desc":    "Complete board session: Strategy, Finance, Creative, Tech, Ops, Distribution.",
        "icon":    "🏛",
    },
    "strategy_deep": {
        "label":   "Strategy Deep Dive",
        "desc":    "3-model strategy committee consensus + TWIN summary.",
        "icon":    "🎯",
    },
    "creative_brief": {
        "label":   "Creative Brief",
        "desc":    "Creative committee generates concepts, angles, and copy directions.",
        "icon":    "✦",
    },
    "competitive_intel": {
        "label":   "Competitive Intelligence",
        "desc":    "Multi-model analysis of market position, threats, and opportunities.",
        "icon":    "🔍",
    },
    "custom_committee": {
        "label":   "Custom Committee",
        "desc":    "User-defined or engine-designed committee assembled from the full model roster.",
        "icon":    "⚙",
    },
}

# ── Mission data model ─────────────────────────────────────────────────────────
class Mission:
    def __init__(self, title: str, brief: str, mission_type: str):
        self.id           = uuid.uuid4().hex[:12]
        self.title        = title
        self.brief        = brief
        self.type         = mission_type
        self.status       = STATUS_PENDING
        self.created      = datetime.now(timezone.utc).isoformat()
        self.started      = None
        self.completed    = None
        self.error        = None
        self.results: dict[str, str] = {}   # phase_name → text

    def to_dict(self) -> dict:
        return {
            "id":        self.id,
            "title":     self.title,
            "brief":     self.brief,
            "type":      self.type,
            "status":    self.status,
            "created":   self.created,
            "started":   self.started,
            "completed": self.completed,
            "error":     self.error,
            "results":   self.results,
        }

    @staticmethod
    def from_dict(d: dict) -> "Mission":
        m            = Mission.__new__(Mission)
        m.id         = d["id"]
        m.title      = d["title"]
        m.brief      = d["brief"]
        m.type       = d["type"]
        m.status     = d["status"]
        m.created    = d["created"]
        m.started    = d.get("started")
        m.completed  = d.get("completed")
        m.error      = d.get("error")
        m.results    = d.get("results", {})
        return m

    def save(self):
        path = _MISSIONS_DIR / f"{self.id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    def log(self, phase: str, text: str):
        self.results[phase] = text
        self.save()

    def append_log(self, phase: str, chunk: str):
        self.results[phase] = self.results.get(phase, "") + chunk
        self.save()


# ── Persistence helpers ────────────────────────────────────────────────────────
def list_missions() -> list[Mission]:
    missions = []
    for f in sorted(_MISSIONS_DIR.iterdir(), reverse=True):
        if f.suffix == ".json":
            try:
                missions.append(Mission.from_dict(json.loads(f.read_text())))
            except Exception:
                pass
    return missions

def get_mission(mid: str) -> Optional[Mission]:
    path = _MISSIONS_DIR / f"{mid}.json"
    if not path.exists():
        return None
    return Mission.from_dict(json.loads(path.read_text()))

def delete_mission(mid: str) -> bool:
    path = _MISSIONS_DIR / f"{mid}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# ── Async execution helpers ────────────────────────────────────────────────────
def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

async def _collect_stream(gen) -> str:
    """Collect an async generator into a single string."""
    out = ""
    async for chunk in gen:
        out += chunk
    return out

async def _await_str(coro) -> str:
    """Await a coroutine that returns a string."""
    return await coro


# ── Mission executors ──────────────────────────────────────────────────────────
async def _exec_gorilla(mission: Mission):
    from part14_gorilla import (
        GORILLA_COMMITTEE, gorilla_member_prompt, gorilla_synthesizer_prompt,
    )
    from part2_router import direct_stream as _ds, stream_task as _st
    from part1_registry import get_task_routing

    transcript = ""
    for member in GORILLA_COMMITTEE:
        name = member["name"]
        key  = member["key"]
        try:
            provider, model = get_task_routing(key)
            msgs  = gorilla_member_prompt(member, mission.brief, transcript)
            text  = await _collect_stream(_ds(provider, model, msgs))
            transcript += f"\n\n[{name} — {member['role']}]\n{text}"
            mission.log("committee", transcript)
        except Exception as e:
            transcript += f"\n\n[{name}] ERROR: {e}"
            mission.log("committee", transcript)

    # TWIN compiles the blueprint
    try:
        msgs      = gorilla_synthesizer_prompt(mission.brief, transcript)
        blueprint = await _collect_stream(_st("twin", msgs))
        mission.log("blueprint", blueprint)
    except Exception as e:
        mission.log("blueprint", f"[ERROR] {e}")


async def _exec_boardroom(mission: Mission):
    from part8_personas import board_member_system
    from part1_registry import BOARD_MEMBERS, get_task_routing
    from part2_router import direct_stream as _ds

    transcript = ""
    for m in BOARD_MEMBERS:
        name = m["name"]
        key  = m["key"]
        try:
            provider, model = get_task_routing(key)
            sys_p    = board_member_system(name, m.get("role", ""), shadow_mode=False)
            user_msg = (
                f"BOARDROOM TOPIC: {mission.brief}\n\n"
                f"CONTEXT SO FAR:\n{transcript[-3000:] if transcript else 'None yet.'}\n\n"
                f"Speak as {name}. Direct, in-character, max 3 paragraphs."
            )
            msgs = [{"role": "system", "content": sys_p},
                    {"role": "user",   "content": user_msg}]
            text = await _collect_stream(_ds(provider, model, msgs))
            transcript += f"\n\n── {name} ({m.get('role','')}) ──\n{text}"
            mission.log("boardroom", transcript)
        except Exception as e:
            transcript += f"\n\n── {name} ERROR: {e} ──"
            mission.log("boardroom", transcript)

    # TWIN synthesizes
    from part2_router import call_task
    from part8_personas import get_system_prompt
    try:
        msgs = [
            {"role": "system", "content": get_system_prompt("twin")},
            {"role": "user",   "content": (
                f"Synthesize this boardroom session on: {mission.brief}\n\n"
                f"SESSION TRANSCRIPT:\n{transcript}\n\n"
                "Write a concise executive summary with: key consensus points, "
                "major disagreements, top 3 recommended actions, and one contrarian insight."
            )},
        ]
        summary = await call_task("twin", msgs)
        mission.log("summary", summary)
    except Exception as e:
        mission.log("summary", f"[ERROR] {e}")


async def _exec_strategy_deep(mission: Mission):
    from part1_registry import get_task_routing
    from part2_router import direct_stream as _ds, call_task as _ct
    from part8_personas import get_system_prompt

    members = [
        {"key": "committee_strategy_1", "name": "NEMOTRON",  "angle": "long-range macro positioning"},
        {"key": "committee_strategy_2", "name": "QWEN",      "angle": "competitive dynamics and market structure"},
        {"key": "committee_strategy_3", "name": "DEEPSEEK",  "angle": "assumptions, risks, and blind spots"},
    ]
    transcript = ""
    for m in members:
        try:
            provider, model = get_task_routing(m["key"])
            sys_p = (
                f"You are a senior strategy analyst specializing in {m['angle']}. "
                f"Be direct, specific, and analytical. No fluff."
            )
            user_msg = (
                f"STRATEGIC QUESTION: {mission.brief}\n\n"
                f"Analyze from the perspective of {m['angle']}. "
                f"Give 3-5 specific, actionable insights. Be concrete, not abstract."
            )
            if transcript:
                user_msg += f"\n\nPRIOR ANALYSIS:\n{transcript[-2000:]}"
            msgs = [{"role": "system", "content": sys_p},
                    {"role": "user",   "content": user_msg}]
            text = await _collect_stream(_ds(provider, model, msgs))
            transcript += f"\n\n── {m['name']} ({m['angle'].upper()}) ──\n{text}"
            mission.log("analysis", transcript)
        except Exception as e:
            transcript += f"\n\n── {m['name']} ERROR: {e}"
            mission.log("analysis", transcript)

    # TWIN consensus
    from part2_router import call_task
    from part8_personas import get_system_prompt as _gsp
    try:
        msgs = [
            {"role": "system", "content": _gsp("twin")},
            {"role": "user",   "content": (
                f"STRATEGIC QUESTION: {mission.brief}\n\n"
                f"THREE ANALYST PERSPECTIVES:\n{transcript}\n\n"
                "Synthesize into: 1 bold strategic recommendation, 3 ranked action steps, "
                "and 1 key risk to watch. Be specific and decisive."
            )},
        ]
        consensus = await call_task("twin", msgs)
        mission.log("consensus", consensus)
    except Exception as e:
        mission.log("consensus", f"[ERROR] {e}")


async def _exec_creative_brief(mission: Mission):
    from part1_registry import get_task_routing
    from part2_router import direct_stream as _ds, call_task as _ct
    from part8_personas import get_system_prompt

    members = [
        {"key": "committee_creative_1", "name": "PALMYRA",  "focus": "visual identity, aesthetic direction, and tone"},
        {"key": "committee_creative_2", "name": "QWEN",     "focus": "messaging, copy angles, and voice"},
        {"key": "committee_creative_3", "name": "GLM",      "focus": "campaign concepts and experiential ideas"},
    ]
    transcript = ""
    for m in members:
        try:
            provider, model = get_task_routing(m["key"])
            sys_p = (
                f"You are a senior creative director specializing in {m['focus']}. "
                "Be bold, specific, and original. No safe or generic ideas."
            )
            user_msg = (
                f"CREATIVE BRIEF: {mission.brief}\n\n"
                f"Generate 3 distinct, specific creative directions for {m['focus']}. "
                "Each direction needs: a name, a one-sentence concept, and 2 concrete executions."
            )
            if transcript:
                user_msg += f"\n\nOTHER CREATIVE INPUT:\n{transcript[-2000:]}"
            msgs = [{"role": "system", "content": sys_p},
                    {"role": "user",   "content": user_msg}]
            text = await _collect_stream(_ds(provider, model, msgs))
            transcript += f"\n\n── {m['name']} ({m['focus'].upper()}) ──\n{text}"
            mission.log("creative_input", transcript)
        except Exception as e:
            transcript += f"\n\n── {m['name']} ERROR: {e}"
            mission.log("creative_input", transcript)

    # TWIN compiles the brief
    from part2_router import call_task
    from part8_personas import get_system_prompt as _gsp2
    try:
        msgs = [
            {"role": "system", "content": _gsp2("twin")},
            {"role": "user",   "content": (
                f"ORIGINAL BRIEF: {mission.brief}\n\n"
                f"CREATIVE COMMITTEE INPUT:\n{transcript}\n\n"
                "Compile a final creative brief with:\n"
                "- THE BIG IDEA (1 sentence)\n"
                "- TONE & VOICE (3 adjectives + 1 anti-adjective)\n"
                "- TOP 3 CAMPAIGN CONCEPTS (name + description + why it works)\n"
                "- KEY VISUAL DIRECTION\n"
                "- COPY DIRECTIONS (3 headline angles)\n"
                "- WHAT TO AVOID"
            )},
        ]
        brief_doc = await call_task("twin", msgs)
        mission.log("brief_document", brief_doc)
    except Exception as e:
        mission.log("brief_document", f"[ERROR] {e}")


async def _exec_competitive_intel(mission: Mission):
    from part1_registry import get_task_routing
    from part2_router import direct_stream as _ds, call_task as _ct
    from part8_personas import get_system_prompt

    members = [
        {"key": "committee_dist_3",     "name": "DEEPSEEK", "lens": "market positioning and competitor mapping"},
        {"key": "committee_strategy_2", "name": "QWEN",     "lens": "market gaps, underserved niches, and white space"},
        {"key": "committee_finance_1",  "name": "PALMYRA",  "lens": "unit economics, pricing power, and moats"},
    ]
    transcript = ""
    for m in members:
        try:
            provider, model = get_task_routing(m["key"])
            sys_p = (
                f"You are a competitive intelligence analyst specializing in {m['lens']}. "
                "Be precise and analytical. Base conclusions on logical inference from the brief."
            )
            user_msg = (
                f"COMPETITIVE INTELLIGENCE REQUEST: {mission.brief}\n\n"
                f"Analyze through the lens of {m['lens']}. "
                "Give 3-4 specific findings with supporting reasoning."
            )
            if transcript:
                user_msg += f"\n\nPRIOR FINDINGS:\n{transcript[-2000:]}"
            msgs = [{"role": "system", "content": sys_p},
                    {"role": "user",   "content": user_msg}]
            text = await _collect_stream(_ds(provider, model, msgs))
            transcript += f"\n\n── {m['name']} ({m['lens'].upper()}) ──\n{text}"
            mission.log("intel", transcript)
        except Exception as e:
            transcript += f"\n\n── {m['name']} ERROR: {e}"
            mission.log("intel", transcript)

    # TWIN intelligence report
    from part2_router import call_task
    from part8_personas import get_system_prompt as _gsp3
    try:
        msgs = [
            {"role": "system", "content": _gsp3("twin")},
            {"role": "user",   "content": (
                f"REQUEST: {mission.brief}\n\n"
                f"ANALYST FINDINGS:\n{transcript}\n\n"
                "Write a competitive intelligence report with:\n"
                "- MARKET POSITION SUMMARY\n"
                "- TOP 3 THREATS (ranked by urgency)\n"
                "- TOP 3 OPPORTUNITIES (ranked by ease × impact)\n"
                "- RECOMMENDED MOVES (specific, immediate actions)\n"
                "- THE UNDERVALUED INSIGHT (what everyone else is missing)"
            )},
        ]
        report = await call_task("twin", msgs)
        mission.log("report", report)
    except Exception as e:
        mission.log("report", f"[ERROR] {e}")


# ── Executor dispatch ──────────────────────────────────────────────────────────
async def _exec_custom_committee(mission: Mission):
    from part16_committees import run_custom_committee
    await run_custom_committee(mission)

_EXECUTORS = {
    "gorilla_marketing":  _exec_gorilla,
    "boardroom_analysis": _exec_boardroom,
    "strategy_deep":      _exec_strategy_deep,
    "creative_brief":     _exec_creative_brief,
    "competitive_intel":  _exec_competitive_intel,
    "custom_committee":   _exec_custom_committee,
}


# ── Background Worker ──────────────────────────────────────────────────────────
class MissionEngine:
    """Singleton background worker. Picks up pending missions and runs them."""

    _instance: Optional["MissionEngine"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queue: list[str] = []      # mission IDs
        self._q_lock = threading.Lock()
        self._event  = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="MissionWorker")
        self._thread.start()
        # On startup, re-queue any missions that were interrupted mid-run
        self._recover()

    @classmethod
    def get(cls) -> "MissionEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def submit(self, mission: Mission):
        with self._q_lock:
            self._queue.append(mission.id)
        self._event.set()

    def _recover(self):
        """Re-queue any missions stuck in 'running' or 'pending' from a prior crash."""
        for m in list_missions():
            if m.status in (STATUS_PENDING, STATUS_RUNNING):
                m.status = STATUS_PENDING
                m.save()
                with self._q_lock:
                    if m.id not in self._queue:
                        self._queue.append(m.id)
        if self._queue:
            self._event.set()

    def _worker(self):
        while True:
            self._event.wait()
            self._event.clear()
            while True:
                with self._q_lock:
                    if not self._queue:
                        break
                    mid = self._queue.pop(0)
                self._run_one(mid)

    def _run_one(self, mid: str):
        mission = get_mission(mid)
        if not mission:
            return
        if mission.status == STATUS_DONE:
            return

        executor = _EXECUTORS.get(mission.type)
        if not executor:
            mission.status = STATUS_ERROR
            mission.error  = f"Unknown mission type: {mission.type}"
            mission.save()
            return

        mission.status  = STATUS_RUNNING
        mission.started = datetime.now(timezone.utc).isoformat()
        mission.save()

        try:
            _run_async(executor(mission))
            mission.status    = STATUS_DONE
            mission.completed = datetime.now(timezone.utc).isoformat()
            mission.save()
        except Exception as e:
            mission.status = STATUS_ERROR
            mission.error  = str(e)
            mission.save()


# ── Public API ─────────────────────────────────────────────────────────────────
def create_mission(title: str, brief: str, mission_type: str) -> Mission:
    m = Mission(title, brief, mission_type)
    m.save()
    MissionEngine.get().submit(m)
    return m

def retry_mission(mid: str) -> Optional[Mission]:
    m = get_mission(mid)
    if not m:
        return None
    m.status    = STATUS_PENDING
    m.error     = None
    m.started   = None
    m.completed = None
    m.results   = {}
    m.save()
    MissionEngine.get().submit(m)
    return m
