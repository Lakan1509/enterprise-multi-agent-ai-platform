"""CASI API schemas — request/response models for the AI OS REST surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GoalCreate(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    requester: str = Field(default="api", max_length=100)


class GoalRunRequest(BaseModel):
    auto_approve: bool = False


class TaskOut(BaseModel):
    id: str
    name: str
    description: str
    agent_capability: str
    depends_on: list[str]
    approval_required: bool


class PlanOut(BaseModel):
    goal_id: str
    goal_text: str
    tasks: list[TaskOut]


class TaskOutcomeOut(BaseModel):
    task_id: str
    status: str
    attempts: int
    message: str | None = None
    error: str | None = None


class GoalOut(BaseModel):
    id: str
    text: str
    status: str
    created_at: str
    artifacts: list[str] = Field(default_factory=list)
    outcomes: list[TaskOutcomeOut] = Field(default_factory=list)


class ApprovalOut(BaseModel):
    id: str
    action: str
    details: dict[str, Any]
    status: str
    created_at: str
    resolved_at: str | None = None
    note: str = ""


class ApprovalResolve(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2000)


class AgentOut(BaseModel):
    name: str
    capabilities: list[str]


class LogEntry(BaseModel):
    ts: str
    event: str
    goal_id: str = ""
    task_id: str = ""
    actor: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
