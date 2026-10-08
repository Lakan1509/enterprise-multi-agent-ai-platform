"""CASI FastAPI application factory — the API-first AI OS interface.

Exposes goals, plans, task outcomes, agents, artifacts, approvals, and logs.
All CASI component imports are lazy so the app module stays importable while
the package is being assembled.

Security model:

* :meth:`casi.config.Settings.validate_deployment` runs at startup and
  refuses to start non-loopback (or ``CASI_REQUIRE_AUTH=1``) deployments
  without a strong ``CASI_API_KEY``.
* Every request authenticates via the ``X-API-Key`` header. The key maps to
  a role: ``CASI_API_KEY`` -> ADMIN, ``CASI_OPERATOR_API_KEY`` -> OPERATOR,
  ``CASI_VIEWER_API_KEY`` -> VIEWER. Unknown/missing key -> 401 whenever any
  key is configured. With no keys configured (loopback dev), all callers are
  VIEWER and a loud warning is emitted at startup.
* Mutation endpoints additionally require the matching
  :class:`~casi.security.permissions.Capability`; failures are 403.
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
    TaskOut,
    TaskOutcomeOut,
)
from casi.security.auth import verify_api_key
from casi.security.permissions import Capability, PermissionDenied, Role, check


def require_capability(capability: Capability):
    """Build a FastAPI dependency enforcing auth + a role capability.

    Resolves the caller's role from the API key, then fails closed with 403
    when the role lacks ``capability``. Returns the authorized role.
    """

    def _dep(role: Role = Depends(verify_api_key)) -> Role:
        try:
            check(role, capability)
        except PermissionDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return role

    return _dep


# Read endpoints: any authenticated caller with workspace read access.
require_read = require_capability(Capability.READ_WORKSPACE)
# Goal mutations: operators and admins.
require_manage_goals = require_capability(Capability.MANAGE_GOALS)
# Resolving approvals: admins only (APPROVE_PUBLISH is ADMIN-only).
require_approve = require_capability(Capability.APPROVE_PUBLISH)


def create_app(kernel: Any = None, settings: Any = None) -> FastAPI:
    """Build the FastAPI app. Pass an existing AIKernel or one is constructed.

    Args:
        kernel: An existing :class:`~casi.kernel.AIKernel`.
        settings: Optional :class:`~casi.config.Settings`; falls back to
            ``kernel.settings`` or a fresh ``Settings()`` from the environment.

    Raises:
        casi.config.InsecureDeploymentError: If the deployment requires auth
            (non-loopback bind or ``CASI_REQUIRE_AUTH=1``) but no strong
            ``CASI_API_KEY`` is configured. Fail fast at startup.
    """
    from casi.config import Settings
    from casi.kernel import AIKernel, GoalNotFound

    if kernel is None:
        kernel = AIKernel()

    resolved_settings = settings
    if resolved_settings is None:
        resolved_settings = getattr(kernel, "settings", None)
    if resolved_settings is None:
        resolved_settings = Settings()
    resolved_settings.validate_deployment()

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

    @app.post("/goals", response_model=GoalOut)
    def create_goal(payload: GoalCreate, role: Role = Depends(require_manage_goals)) -> GoalOut:
        goal = kernel.create_goal(payload.text, requester=payload.requester)
        return _goal_out(goal)

    @app.get("/goals", response_model=list[GoalOut])
    def list_goals(role: Role = Depends(require_read)) -> list[GoalOut]:
        return [_goal_out(g) for g in kernel.list_goals()]

    @app.get("/goals/{goal_id}", response_model=GoalOut)
    def get_goal(goal_id: str, role: Role = Depends(require_read)) -> GoalOut:
        try:
            return _goal_out(kernel.get_goal(goal_id))
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/goals/{goal_id}/run", response_model=GoalOut)
    def run_goal(goal_id: str, payload: GoalRunRequest, role: Role = Depends(require_manage_goals)) -> GoalOut:
        # auto_approve=True requires APPROVE_OWN_RUNS; the kernel enforces it
        # and audits the decision. Passing the caller's role (never a config
        # default) is what makes the auto-approve path explicit.
        try:
            goal = kernel.run_goal(goal_id, auto_approve=payload.auto_approve, role=role)
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return _goal_out(goal)

    @app.get("/goals/{goal_id}/plan", response_model=PlanOut)
    def get_plan(goal_id: str, role: Role = Depends(require_read)) -> PlanOut:
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

    @app.post("/goals/{goal_id}/cancel", response_model=GoalOut)
    def cancel_goal(goal_id: str, role: Role = Depends(require_manage_goals)) -> GoalOut:
        try:
            return _goal_out(kernel.cancel_goal(goal_id))
        except GoalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/agents", response_model=list[AgentOut])
    def list_agents(role: Role = Depends(require_read)) -> list[AgentOut]:
        return [
            AgentOut(name=a.name, capabilities=list(a.capabilities))
            for a in kernel.registry.list()
        ]

    @app.get("/artifacts")
    def list_artifacts(goal_id: str = Query(default=""), role: Role = Depends(require_read)) -> dict[str, Any]:
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

    @app.get("/approvals/pending", response_model=list[ApprovalOut])
    def pending_approvals(role: Role = Depends(require_read)) -> list[ApprovalOut]:
        return [ApprovalOut(**a.__dict__) for a in kernel.approvals.pending()]

    @app.post("/approvals/{approval_id}/resolve", response_model=ApprovalOut)
    def resolve_approval(approval_id: str, payload: ApprovalResolve, role: Role = Depends(require_approve)) -> ApprovalOut:
        try:
            approval = kernel.resolve_approval(approval_id, payload.approved, payload.note)
        except Exception as exc:  # ApprovalNotFound / ValueError
            raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc)) from exc
        return ApprovalOut(**approval.__dict__)

    @app.get("/logs", response_model=list[LogEntry])
    def read_logs(
        goal_id: str = Query(default=""),
        limit: int = Query(default=200, le=2000),
        role: Role = Depends(require_read),
    ) -> list[LogEntry]:
        return [LogEntry(**e) for e in kernel.audit.read(goal_id=goal_id, limit=limit)]

    return app
