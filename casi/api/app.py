"""CASI FastAPI application factory — the API-first AI OS interface.

Exposes goals, plans, task outcomes, agents, artifacts, approvals, and logs.
All CASI component imports are lazy so the app module stays importable while
the package is being assembled.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query

from casi.api.schemas import (
    AgentOut,
    ApprovalOut,
    ApprovalResolve,
    GoalCreate,
    GoalOut,
    GoalRunRequest,
    LogEntry,
    PlanOut,
    TaskOutcomeOut,
    TaskOut,
)


def create_app(kernel: Any = None) -> FastAPI:
    """Build the FastAPI app. Pass an existing AIKernel or one is constructed."""
    from casi.kernel import AIKernel, GoalNotFound

    if kernel is None:
        kernel = AIKernel()

    from casi.security.auth import verify_api_key

    app = FastAPI(
        title="CASI — Artificial Intelligence Operating System",
        version="0.1.0",
        description="API-first AI OS: goals, agent execution, approvals, artifacts, audit logs.",
    )

    def _goal_out(goal: Any) -> GoalOut:
        outcomes: list[TaskOutcomeOut] = []
        if goal.report is not None:
            for oc in goal.report.outcomes:
                msg = oc.result.message if oc.result else None
                outcomes.append(
                    TaskOutcomeOut(
                        task_id=oc.task_id,
                        status=oc.status,
                        attempts=oc.attempts,
                        message=msg,
                        error=oc.error,
                    )
                )
        return GoalOut(
            id=goal.id,
            text=goal.text,
            status=goal.status.value if hasattr(goal.status, "value") else str(goal.status),
            created_at=goal.created_at,
            artifacts=list(goal.artifacts),
            outcomes=outcomes,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "version": "0.1.0"}

    @app.post("/goals", response_model=GoalOut, dependencies=[Depends(verify_api_key)])
    def create_goal(payload: GoalCreate) -> GoalOut:
        goal = kernel.create_goal(payload.text, requester=payload.requester)
        return _goal_out(goal)

    @app.get("/goals", response_model=list[GoalOut], dependencies=[Depends(verify_api_key)])
    def list_goals() -> list[GoalOut]:
        return [_goal_out(g) for g in kernel.list_goals()]

    @app.get("/goals/{goal_id}", response_model=GoalOut, dependencies=[Depends(verify_api_key)])
    def get_goal(goal_id: str) -> GoalOut:
        try:
            return _goal_out(kernel.get_goal(goal_id))
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/goals/{goal_id}/run", response_model=GoalOut, dependencies=[Depends(verify_api_key)])
    def run_goal(goal_id: str, payload: GoalRunRequest) -> GoalOut:
        try:
            goal = kernel.run_goal(goal_id, auto_approve=payload.auto_approve)
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _goal_out(goal)

    @app.get("/goals/{goal_id}/plan", response_model=PlanOut, dependencies=[Depends(verify_api_key)])
    def get_plan(goal_id: str) -> PlanOut:
        try:
            goal = kernel.get_goal(goal_id)
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if goal.plan is None:
            raise HTTPException(status_code=409, detail="Goal has not been planned yet.")
        return PlanOut(
            goal_id=goal.plan.goal_id,
            goal_text=goal.plan.goal_text,
            tasks=[
                TaskOut(
                    id=t.id,
                    name=t.name,
                    description=t.description,
                    agent_capability=t.agent_capability,
                    depends_on=list(t.depends_on),
                    approval_required=t.approval_required,
                )
                for t in goal.plan.tasks
            ],
        )

    @app.post("/goals/{goal_id}/cancel", response_model=GoalOut, dependencies=[Depends(verify_api_key)])
    def cancel_goal(goal_id: str) -> GoalOut:
        try:
            return _goal_out(kernel.cancel_goal(goal_id))
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/agents", response_model=list[AgentOut], dependencies=[Depends(verify_api_key)])
    def list_agents() -> list[AgentOut]:
        return [
            AgentOut(name=a.name, capabilities=list(a.capabilities))
            for a in kernel.registry.list()
        ]

    @app.get("/artifacts", dependencies=[Depends(verify_api_key)])
    def list_artifacts(goal_id: str = Query(default="")) -> dict[str, Any]:
        try:
            goal = kernel.get_goal(goal_id) if goal_id else None
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if goal is not None:
            return {"goal_id": goal.id, "artifacts": list(goal.artifacts)}
        return {
            "goals": [
                {"goal_id": g.id, "artifacts": list(g.artifacts)}
                for g in kernel.list_goals()
                if g.artifacts
            ]
        }

    @app.get("/approvals/pending", response_model=list[ApprovalOut], dependencies=[Depends(verify_api_key)])
    def pending_approvals() -> list[ApprovalOut]:
        return [ApprovalOut(**a.__dict__) for a in kernel.approvals.pending()]

    @app.post("/approvals/{approval_id}/resolve", response_model=ApprovalOut, dependencies=[Depends(verify_api_key)])
    def resolve_approval(approval_id: str, payload: ApprovalResolve) -> ApprovalOut:
        try:
            approval = kernel.resolve_approval(approval_id, payload.approved, payload.note)
        except Exception as exc:  # ApprovalNotFound / ValueError
            raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc)) from exc
        return ApprovalOut(**approval.__dict__)

    @app.get("/logs", response_model=list[LogEntry], dependencies=[Depends(verify_api_key)])
    def read_logs(goal_id: str = Query(default=""), limit: int = Query(default=200, le=2000)) -> list[LogEntry]:
        return [LogEntry(**e) for e in kernel.audit.read(goal_id=goal_id, limit=limit)]

    return app
