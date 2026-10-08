#!/usr/bin/env python3
"""CASI Milestone 1 acceptance demo — the full core loop, for real.

Goal: "Write a Python function `sort_list` that sorts a list of numbers
ascending, and include unit tests."

Flow: kernel.create_goal -> plan (DAG) -> agents execute in sandbox ->
pytest runs -> genuine test failure -> debugger repairs from the real
traceback -> tests re-run green -> reviewer verifies -> approval gate
PAUSES before publish -> human approves -> COMPLETED with verified artifact.

Nothing in the loop is stubbed: code is written to the workspace, pytest
executes in the sandbox, failures are parsed from real output, the patch
is a real source edit, and the approval gate genuinely blocks until a
human resolves it.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

# Allow running from the repo root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from casi.config import Settings
from casi.kernel import AIKernel
from casi.security.permissions import Role

GOAL_TEXT = (
    "Write a Python function `sort_list` that sorts a list of numbers "
    "ascending, and include unit tests."
)


def main() -> int:
    auto_approve = "--auto-approve" in sys.argv
    data_dir = Path(tempfile.mkdtemp(prefix="casi-demo-"))
    print(f"[demo] data dir: {data_dir}")

    settings = Settings(data_dir=data_dir)
    kernel = AIKernel(settings)

    # The demo acts as the human operator with full privileges. auto_approve
    # is passed explicitly here (never a config default) together with the
    # ADMIN role, so it flows through the kernel's capability-checked
    # auto-approve path (requires APPROVE_OWN_RUNS) and is audit-logged.
    demo_role = Role.ADMIN

    goal = kernel.create_goal(GOAL_TEXT, requester="milestone-demo")
    print(f"[demo] goal created: {goal.id}")

    # run_goal blocks at the approval gate, so run it in the background.
    result: dict = {}

    def _run() -> None:
        try:
            result["goal"] = kernel.run_goal(
                goal.id, auto_approve=auto_approve, role=demo_role
            )
        except Exception as exc:  # noqa: BLE001 — demo must report, not crash
            result["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    # Watch for the approval gate, then ask the human.
    approval = None
    deadline = time.time() + 300
    while time.time() < deadline:
        pending = kernel.approvals.pending()
        if pending:
            approval = pending[0]
            break
        if not worker.is_alive() and "goal" in result:
            break
        time.sleep(0.5)

    if approval is not None:
        print("\n" + "=" * 70)
        print("APPROVAL GATE — human decision required before publish")
        print(f"  approval id : {approval.id}")
        print(f"  action      : {approval.action}")
        print(f"  details     : {approval.details}")
        print("=" * 70)
        if sys.stdin.isatty():
            answer = input("Approve publish? [y/N] ").strip().lower()
            approved = answer in ("y", "yes")
        elif auto_approve:
            print("[demo] --auto-approve: resolving the pending approval "
                  "through the real gate (simulating the human decision).")
            time.sleep(1)
            approved = True
        else:
            print("[demo] non-interactive stdin: leaving approval PENDING.")
            print(f"[demo] resolve later: POST /approvals/{approval.id}/resolve")
            print("[demo] re-run with --auto-approve to complete the loop non-interactively.")
            approved = None
        if approved is not None:
            kernel.resolve_approval(approval.id, approved, note="milestone demo decision")
            print(f"[demo] approval {approval.id} -> {'APPROVED' if approved else 'REJECTED'}")

    worker.join(timeout=120)
    if worker.is_alive():
        print("[demo] ERROR: worker did not finish in time")
        return 2
    if "error" in result:
        print(f"[demo] ERROR: {result['error']}")
        return 1

    goal = result["goal"]
    print("\n" + "=" * 70)
    print(f"GOAL {goal.id}: {goal.status}")
    print("=" * 70)
    if goal.report:
        for oc in goal.report.outcomes:
            msg = oc.result.message if oc.result else ""
            flag = {"ok": "✓", "failed": "✗", "skipped": "→", "cancelled": "■"}.get(oc.status, "?")
            print(f"  [{flag}] {oc.task_id:22s} {oc.status:10s} attempts={oc.attempts} {msg}")
            if oc.error:
                print(f"       error: {oc.error[:200]}")
    print(f"\nartifacts ({len(goal.artifacts)}):")
    for a in goal.artifacts:
        print(f"  - {a}")

    # Show the verified artifact and the test report.
    ws_root = settings.resolved_workspace_dir
    for rel in goal.artifacts:
        p = ws_root / rel
        if p.suffix == ".py" and p.exists():
            print(f"\n--- {rel} ---")
            print(p.read_text()[:1500])

    print("\n--- audit tail ---")
    for entry in kernel.audit.read(goal_id=goal.id, limit=15):
        print(f"  {entry['ts']} {entry['event']} task={entry.get('task_id','')} actor={entry.get('actor','')}")

    ok = str(goal.status) == "GoalStatus.COMPLETED" or "COMPLETED" in str(goal.status)
    print(f"\n[demo] {'SUCCESS' if ok else 'NOT COMPLETED'}: status={goal.status}")
    print(f"[demo] data preserved at: {data_dir} (delete when done: rm -rf {data_dir})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
