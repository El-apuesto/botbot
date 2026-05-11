from datetime import datetime
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field


class TaskRecord(BaseModel):
    id: str
    project_id: str
    task_type: str

    status: Literal[
        "queued",
        "running",
        "completed",
        "failed",
        "retrying"
    ] = "queued"

    retries: int = 0

    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: Optional[dict[str, Any]] = None

    error: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TaskEvent(BaseModel):
    task_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewResult(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    revised_output: Optional[str] = None


class BriefSchema(BaseModel):
    """
    Typed schema for generate_brief() output.
    Replaces raw json.loads() so invalid LLM responses are caught at the boundary.
    """
    summary: str
    goals: list[str] = Field(default_factory=list)
    project_type: Literal["content", "saas", "hybrid", "video", "code"] = "hybrid"
    estimated_effort: Literal["quick", "medium", "heavy"] = "medium"
    modules_needed: list[str] = Field(
        default_factory=lambda: ["creative", "code", "business"]
    )
    tone: Literal["raw", "professional", "deadpan", "technical"] = "raw"
