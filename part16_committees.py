"""
Twin Shadow — Part 16: Dynamic Committee Builder
TWIN reads a plain-language description and assembles a custom committee
from the available model roster. Committees can be saved, reused in missions,
or spun up on the fly by the internal engine.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_COMMITTEES_DIR = Path(__file__).parent / "committees"
_COMMITTEES_DIR.mkdir(exist_ok=True)

# ── Full model roster with capability descriptions ────────────────────────────
# This is what TWIN reads when designing a committee.
COMMITTEE_ROSTER = [
    # Strategy
    {"key": "committee_strategy_1", "name": "NEMOTRON",    "domain": "strategy",  "desc": "Macro strategy, long-range planning, market positioning, competitive landscape"},
    {"key": "committee_strategy_2", "name": "QWEN-STRAT",  "domain": "strategy",  "desc": "Competitive analysis, structured strategic frameworks, market dynamics, SWOT"},
    {"key": "committee_strategy_3", "name": "KIMI-THINK",  "domain": "strategy",  "desc": "Deep chain-of-thought reasoning, challenging assumptions, identifying blindspots and second-order effects"},
    # Finance
    {"key": "committee_finance_1",  "name": "PALMYRA-FIN", "domain": "finance",   "desc": "Financial modeling, unit economics, investment analysis, revenue projections"},
    {"key": "committee_finance_2",  "name": "MAGISTRAL",   "domain": "finance",   "desc": "Risk assessment, regulatory analysis, compliance, downside scenarios"},
    {"key": "committee_finance_3",  "name": "MISTRAL-FIN", "domain": "finance",   "desc": "Pricing strategy, revenue modeling, cost structures, margin analysis"},
    # Creative
    {"key": "committee_creative_1", "name": "PALMYRA-C",   "domain": "creative",  "desc": "Visual identity, brand aesthetics, art direction, campaign concepts and creative vision"},
    {"key": "committee_creative_2", "name": "QWEN-C",      "domain": "creative",  "desc": "Fast creative iteration, copywriting, messaging angles, taglines, ad copy"},
    {"key": "committee_creative_3", "name": "GLM",         "domain": "creative",  "desc": "Experimental creative, storytelling, narrative arcs, world-building, content formats"},
    # Tech
    {"key": "committee_tech_1",     "name": "CODESTRAL",   "domain": "tech",      "desc": "Code architecture, API design, technical implementation, software engineering"},
    {"key": "committee_tech_2",     "name": "GRANITE",     "domain": "tech",      "desc": "System design, DevOps, infrastructure, cloud, scalability planning"},
    {"key": "committee_tech_3",     "name": "GPT-TECH",    "domain": "tech",      "desc": "Product technology, integrations, technical feasibility, platform analysis"},
    # Operations
    {"key": "committee_ops_1",      "name": "SEED",        "domain": "ops",       "desc": "Operational planning, process design, execution logistics, project management"},
    {"key": "committee_ops_2",      "name": "STEPFUN",     "domain": "ops",       "desc": "Rapid ops decisions, workflow optimization, fast-turn execution planning"},
    {"key": "committee_ops_3",      "name": "QWEN-OPS",    "domain": "ops",       "desc": "Scaling operations, team structure, resource allocation, org design"},
    # Distribution / Growth
    {"key": "committee_dist_1",     "name": "GPT-120B",    "domain": "distribution", "desc": "Distribution strategy, channel partnerships, platform relationships, licensing"},
    {"key": "committee_dist_2",     "name": "QWEN-DIST",   "domain": "distribution", "desc": "Audience growth, community building, virality mechanics, social strategy"},
    {"key": "committee_dist_3",     "name": "DEEPSEEK",    "domain": "distribution", "desc": "Data-driven growth, analytics, performance marketing, conversion optimization"},
]

ROSTER_SUMMARY = "\n".join(
    f"  [{m['key']}] {m['name']} ({m['domain'].upper()}) — {m['desc']}"
    for m in COMMITTEE_ROSTER
)

# ── CommitteeConfig model ─────────────────────────────────────────────────────
class CommitteeConfig:
    def __init__(self, name: str, description: str, members: list[dict]):
        self.id          = uuid.uuid4().hex[:12]
        self.name        = name
        self.description = description
        self.members     = members   # [{key, name, role}]
        self.created     = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "name":        self.name,
            "description": self.description,
            "members":     self.members,
            "created":     self.created,
        }

    @staticmethod
    def from_dict(d: dict) -> "CommitteeConfig":
        c             = CommitteeConfig.__new__(CommitteeConfig)
        c.id          = d["id"]
        c.name        = d["name"]
        c.description = d["description"]
        c.members     = d["members"]
        c.created     = d["created"]
        return c

    def save(self):
        (_COMMITTEES_DIR / f"{self.id}.json").write_text(
            json.dumps(self.to_dict(), indent=2)
        )


def list_committees() -> list[CommitteeConfig]:
    out = []
    for f in sorted(_COMMITTEES_DIR.iterdir(), reverse=True):
        if f.suffix == ".json":
            try:
                out.append(CommitteeConfig.from_dict(json.loads(f.read_text())))
            except Exception:
                pass
    return out

def get_committee(cid: str) -> Optional[CommitteeConfig]:
    p = _COMMITTEES_DIR / f"{cid}.json"
    return CommitteeConfig.from_dict(json.loads(p.read_text())) if p.exists() else None

def delete_committee(cid: str) -> bool:
    p = _COMMITTEES_DIR / f"{cid}.json"
    if p.exists():
        p.unlink()
        return True
    return False


# ── TWIN committee designer ───────────────────────────────────────────────────
_DESIGN_SYSTEM = """\
You are the Twin Shadow committee architect. Given a user's description of the \
expertise they need, you select the best members from the available model roster \
and design a custom committee. You always return valid JSON and nothing else.\
"""

_DESIGN_PROMPT_TEMPLATE = """\
AVAILABLE COMMITTEE ROSTER:
{roster}

USER REQUEST:
"{request}"

Design a custom committee with 3 to 6 members from the roster above.
Pick members whose domains and descriptions best match the user's request.
Assign each member a specific ROLE NAME that fits this particular mission \
(e.g. "Music Industry Analyst", "Viral Growth Strategist", "A&R Scout").

Return ONLY a JSON object in this exact format — no explanation, no markdown:
{{
  "name": "Short committee name (3-5 words)",
  "description": "One sentence describing what this committee does",
  "members": [
    {{"key": "<roster key>", "name": "<model name from roster>", "role": "<specific role for this mission>"}},
    ...
  ]
}}
"""

async def design_committee(request: str) -> CommitteeConfig:
    """Ask TWIN to design a committee from the roster based on a plain-language request."""
    from part2_router import call_task

    prompt = _DESIGN_PROMPT_TEMPLATE.format(
        roster=ROSTER_SUMMARY,
        request=request,
    )
    raw = await call_task("twin", [
        {"role": "system",  "content": _DESIGN_SYSTEM},
        {"role": "user",    "content": prompt},
    ])

    # Parse the JSON — strip any accidental markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(
            l for l in lines
            if not l.startswith("```")
        ).strip()

    data = json.loads(cleaned)

    # Validate keys against roster
    valid_keys = {m["key"] for m in COMMITTEE_ROSTER}
    members = [
        m for m in data.get("members", [])
        if m.get("key") in valid_keys
    ]
    if not members:
        raise ValueError("TWIN returned no valid committee members")

    return CommitteeConfig(
        name        = data.get("name", "Custom Committee"),
        description = data.get("description", request),
        members     = members,
    )


# ── Custom committee mission executor ─────────────────────────────────────────
async def run_custom_committee(mission) -> None:
    """
    Execute a mission using a custom CommitteeConfig.
    mission.results["committee_config"] must contain the JSON-encoded config.
    """
    from part1_registry import get_task_routing
    from part2_router import direct_stream, call_task
    from part8_personas import get_system_prompt

    # Load the committee config stored in the mission
    config_data = mission.results.get("committee_config")
    if not config_data:
        # Fallback: try to find a saved committee with matching ID
        cid = getattr(mission, "committee_id", None)
        if cid:
            cfg = get_committee(cid)
            if not cfg:
                mission.log("error", f"Committee {cid} not found")
                return
            members = cfg.members
        else:
            mission.log("error", "No committee config attached to mission")
            return
    else:
        if isinstance(config_data, str):
            config_data = json.loads(config_data)
        members = config_data.get("members", [])

    transcript = ""
    for m in members:
        key  = m.get("key", "")
        name = m.get("name", key)
        role = m.get("role", name)
        try:
            provider, model_key = get_task_routing(key)
        except Exception as e:
            transcript += f"\n\n── {name} ERROR: could not route ({e})"
            mission.log("committee", transcript)
            continue

        try:
            sys_p = (
                f"You are {name}, serving as {role} on a research committee. "
                f"Be specific, analytical, and direct. Stay in your role. "
                f"No generic advice — give concrete, actionable insights specific to this brief."
            )
            user_msg = (
                f"COMMITTEE RESEARCH BRIEF:\n{mission.brief}\n\n"
                f"PRIOR DISCUSSION:\n{transcript[-3000:] if transcript else 'You are speaking first.'}\n\n"
                f"Speak as {name} ({role}). Give your 3-5 most important insights. Be specific."
            )
            msgs = [
                {"role": "system", "content": sys_p},
                {"role": "user",   "content": user_msg},
            ]
            text = ""
            async for chunk in direct_stream(provider, model_key, msgs):
                text += chunk
            transcript += f"\n\n── {name} · {role} ──\n{text}"
            mission.log("committee", transcript)
        except Exception as e:
            transcript += f"\n\n── {name} ERROR: {e}"
            mission.log("committee", transcript)

    # TWIN synthesizes
    try:
        msgs = [
            {"role": "system", "content": get_system_prompt("twin")},
            {"role": "user",   "content": (
                f"RESEARCH BRIEF:\n{mission.brief}\n\n"
                f"COMMITTEE SESSION TRANSCRIPT:\n{transcript}\n\n"
                "Synthesize this committee session into a final deliverable:\n"
                "- KEY FINDINGS (top 5, most important first)\n"
                "- RECOMMENDED ACTIONS (specific, ordered by priority)\n"
                "- CONSENSUS VIEW (what the committee agrees on)\n"
                "- DISSENTING INSIGHT (the most valuable contrarian point)\n"
                "- BOTTOM LINE (one decisive sentence)"
            )},
        ]
        synthesis = await call_task("twin", msgs)
        mission.log("synthesis", synthesis)
    except Exception as e:
        mission.log("synthesis", f"[ERROR] {e}")


# ── Async helper ───────────────────────────────────────────────────────────────
def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
