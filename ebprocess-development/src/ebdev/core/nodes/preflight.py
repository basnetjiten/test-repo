# -*- coding: utf-8 -*-
"""
preflight.py
============
Preflight node - checks checkpointed state and state.json to determine resume target.

Responsibilities
----------------
* Load the artifact registry from .ebpearls/Epic-{id}/state.json if it exists.
* Synchronize file-based registry state into GraphState.generated_artifacts.
* Analyze task status to determine if we can skip planning or building.
* Set the skip_to routing property to jump directly to the target node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebdev.core.logger import get_logger
from ebdev.core.nodes.common import send_progress
from ebdev.services.epic_state import get_epic_state_service

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState

logger = get_logger(__name__)


async def preflight_node(state: GraphState) -> GraphState:
    """
    Perform preflight check to determine the next stage of execution.

    Loads the `.ebpearls` state registry and routes execution accordingly.
    """
    state.last_node = "preflight_agent"
    ctx = state.context
    assert ctx is not None, "preflight_node requires a JobContext"

    # Only run SPOQ state checks
    if not state.is_spoq or not ctx.spoq_epic_dir:
        return state.model_copy(update={"preflight_skip_to": None})

    await send_progress(state, "Preflight: Verifying workspace state and checkpoints...")

    epic_dir = ctx.project_storage_dir() / ctx.spoq_epic_dir
    svc = get_epic_state_service(epic_dir)

    try:
        # Load from disk to sync any manual corrections or file-based updates
        snapshot = await svc.load()
        if not snapshot:
            logger.info("No state.json found at %s. Proceeding with fresh plan.", epic_dir)
            return state.model_copy(update={"preflight_skip_to": None})

        # Sync disk state to GraphState
        updated_artifacts = {**state.generated_artifacts}
        for task_id, task_state in snapshot.tasks.items():
            updated_artifacts[task_id] = {
                "status": task_state.status,
                "contract": task_state.artifacts.contract or "",
                "journal": task_state.artifacts.journal or "",
                "verification": task_state.artifacts.verification or "",
                "schema_file": task_state.artifacts.schema_file or "",
                "repair_iteration": str(task_state.repair_iteration),
            }

        # Analyze task states in the registry to determine skip target
        all_tasks = list(snapshot.tasks.values())
        if not all_tasks:
            return state.model_copy(
                update={
                    "generated_artifacts": updated_artifacts,
                    "preflight_skip_to": None,
                }
            )

        # Count statuses
        total = len(all_tasks)
        passed_count = sum(1 for t in all_tasks if t.status == "passed")
        needs_review_count = sum(1 for t in all_tasks if t.status == "needs_review")
        built_or_failed_count = sum(1 for t in all_tasks if t.status in ("building", "evaluating", "repairing"))
        evaluating_count = sum(1 for t in all_tasks if t.status == "evaluating")

        logger.info(
            "Preflight task check summary: total=%d, passed=%d, needs_review=%d, built_or_failed=%d, evaluating=%d",
            total,
            passed_count,
            needs_review_count,
            built_or_failed_count,
            evaluating_count,
        )

        skip_to = None
        if passed_count == total:
            logger.info("All tasks are already completed successfully. Skipping to publish.")
            skip_to = "publish_agent"
            state.done = True
        elif needs_review_count == total:
            logger.info("All tasks need review. Skipping to finalize.")
            skip_to = "finalize_agent"
            state.failed = True
        elif built_or_failed_count > 0:
            logger.info("Found tasks in building/evaluating/repairing state. Skipping planning, routing to builder.")
            skip_to = "builder_agent"
        elif evaluating_count > 0:
            logger.info("Found tasks in evaluating state. Skipping planning and building, routing to evaluator.")
            skip_to = "evaluator_agent"

        return state.model_copy(
            update={
                "generated_artifacts": updated_artifacts,
                "preflight_skip_to": skip_to,
            }
        )

    except Exception as exc:
        logger.warning("Failed to perform preflight checks: %s. Proceeding normally.", exc)
        return state.model_copy(update={"preflight_skip_to": None})
