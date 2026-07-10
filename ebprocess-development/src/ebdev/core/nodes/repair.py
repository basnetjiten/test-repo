# -*- coding: utf-8 -*-
"""
repair.py
=========
Repair node - manages retry counts and platform-specific error aggregation for recovery.

Responsibilities
----------------
* Identify active failing platforms for the current stage from LangGraph state.
* Aggregate validation errors for subsequent repair steps.
* Update retry iteration counters and validate boundaries.
* Determine when repair iterations have exceeded configured limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.exceptions import EpicStateError
from ebdev.core.logger import get_logger
from ebdev.core.nodes.common import send_progress
from ebdev.models.graph_state import JobResult
from ebdev.models.task import TaskArtifacts, TaskArtifactState, TaskStatus
from ebdev.services.epic_state import get_epic_state_service

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState, JobContext
    from ebdev.models.spoq import SPOQTask

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _write_repair_state(
    ctx: "JobContext",
    spoq_tasks: "list[SPOQTask]",
    *,
    iteration: int,
    blocked: bool,
    generated_artifacts: dict[str, dict[str, str]] | None = None,
) -> None:
    """
    Persist repair-iteration progress to ``state.json``.

    Parameters
    ----------
    ctx:
        Job context for path resolution.
    spoq_tasks:
        All current SPOQ task objects from GraphState.
    iteration:
        The new repair iteration counter value.
    blocked:
        ``True`` when max iterations exceeded — sets task status to ``blocked``.
    generated_artifacts:
        Optional dictionary of generated artifacts from the GraphState to recover.
    """
    if not ctx.spoq_epic_dir:
        return

    epic_dir = ctx.project_storage_dir() / ctx.spoq_epic_dir
    svc = get_epic_state_service(epic_dir)

    # Identify tasks that are in a repair-eligible state.
    repair_task_ids = {t.id for t in spoq_tasks if t.status in ("in_progress", "pending")}
    if not repair_task_ids:
        return

    new_status: TaskStatus = "needs_review" if blocked else "repairing"
    task_platforms = {t.id: t.platforms for t in spoq_tasks}

    try:
        snapshot = await svc.load_or_init(
            epic_id=ctx.spoq_epic_dir,
            space_name=ctx.space_name,
        )
        for task_id in repair_task_ids:
            existing = snapshot.get_task(task_id)
            
            # Resolve platform
            if existing:
                platform = existing.platform
            else:
                platforms = task_platforms.get(task_id)
                if platforms:
                    platform = platforms[0]
                elif "api" in task_id:
                    platform = "api"
                elif "flutter" in task_id:
                    platform = "flutter"
                elif "web" in task_id:
                    platform = "web"
                elif "cms" in task_id:
                    platform = "cms"
                else:
                    platform = ctx.platform

            # Resolve artifacts
            art_from_state = None
            if not existing and generated_artifacts and task_id in generated_artifacts:
                art_from_state = generated_artifacts[task_id]

            if existing:
                updated_artifacts = existing.artifacts
            elif art_from_state:
                updated_artifacts = TaskArtifacts(
                    contract=art_from_state.get("contract") or None,
                    journal=art_from_state.get("journal") or None,
                    schema_file=art_from_state.get("schema_file") or None,
                    verification=art_from_state.get("verification") or None,
                )
            else:
                updated_artifacts = TaskArtifacts()

            task_state = TaskArtifactState(
                task_id=task_id,
                platform=platform,
                status=new_status,
                artifacts=updated_artifacts,
                repair_iteration=iteration,
                evaluation_avg=existing.evaluation_avg if existing else None,
                evaluation_min=existing.evaluation_min if existing else None,
            )
            snapshot = snapshot.upsert_task(task_state)
        await svc.save(snapshot)
    except EpicStateError as exc:
        logger.warning("Could not update state.json during repair (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def repair_node(state: GraphState) -> GraphState:
    """
    Analyze failed platforms, formatting repair contexts and retry counts.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with repair retry counters or job failure results.
    """
    state.last_node = "repair_agent"
    ctx = state.context
    assert ctx is not None, "repair_node requires a JobContext"
    iteration = ctx.repair_iteration + 1

    msg = f"Repairing: Evaluating recovery iteration #{iteration}..."
    await send_progress(state, msg)
    is_spoq = state.is_spoq
    if is_spoq:
        # Derive active platforms from current state tasks
        active_task_ids = {t.id for t in state.spoq_tasks if t.status in ("pending", "blocked", "in_progress")}
        all_platforms: list[str] = []
        for t in state.spoq_tasks:
            if t.id in active_task_ids:
                all_platforms.extend(t.platforms)
        active_platforms = list(dict.fromkeys(all_platforms))
    else:
        active_platforms = (
            state.strategy.stages[state.current_stage] if (state.strategy and state.strategy.stages) else ctx.platforms
        )

    try:
        failed_plats = [p for p in active_platforms if state.failed_platforms.get(p)]
        logger.info("Active stage failed platforms requiring repair: %s", failed_plats)

        failed_errors: list[str] = []
        for p in failed_plats:
            failed_errors.append(f"--- platform {p} ---")
            plat_prefix = f"[{p}]"
            plat_errs = [e for e in ctx.validation_errors if e.startswith(plat_prefix)]
            if not plat_errs and state.platform_results:
                for k, res in state.platform_results.items():
                    if (k == p or k.endswith(f"_{p}")) and res.status == "failed" and res.errors:
                        plat_errs.extend(res.errors)
            if plat_errs:
                failed_errors.extend(plat_errs)
            else:
                failed_errors.append(f"Validation failed on platform '{p}'")

        prev_errors = state.result.errors if state.result else []
        new_errors = failed_errors + prev_errors + [f"--- Repair attempt iteration {iteration} ---"]

        # Format a clean flat summary of current validation errors for the progress report
        flat_errors = []
        for p in failed_plats:
            plat_prefix = f"[{p}]"
            plat_errs = [e[len(plat_prefix):].strip() for e in ctx.validation_errors if e.startswith(plat_prefix)]
            if not plat_errs and state.platform_results:
                for k, res in state.platform_results.items():
                    if (k == p or k.endswith(f"_{p}")) and res.status == "failed" and res.errors:
                        plat_errs.extend(res.errors)
            if plat_errs:
                flat_errors.append(f"{p}: {'; '.join(plat_errs)}")
            else:
                flat_errors.append(f"{p}: Unknown validation error")
        error_summary = " | ".join(flat_errors)

        if iteration >= config.MAX_REPAIR_ITERATIONS:
            failed_result = JobResult(
                task_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="failed",
                errors=new_errors,
                summary=(f"Max repair iterations ({config.MAX_REPAIR_ITERATIONS}) reached. Validations still failing."),
            )

            await send_progress(
                state,
                f"Max repair iterations reached. Job failed with platforms unresolved: {failed_plats}. Errors: {error_summary}",
            )
            # Mark all in-progress tasks as needs_review in the artifact registry.
            await _write_repair_state(
                ctx,
                state.spoq_tasks,
                iteration=iteration,
                blocked=True,
                generated_artifacts=state.generated_artifacts,
            )

            updated_artifacts = {**state.generated_artifacts}
            if is_spoq:
                repair_task_ids = {t.id for t in state.spoq_tasks if t.status in ("in_progress", "pending")}
                for task_id in repair_task_ids:
                    existing = updated_artifacts.get(task_id, {})
                    updated_artifacts[task_id] = {
                        **existing,
                        "status": "needs_review",
                        "repair_iteration": str(iteration),
                   }

            return state.model_copy(
                update={
                    "last_node": "repair_agent",
                    "context": ctx.model_copy(
                        update={
                            "repair_iteration": iteration,
                            "validation_errors": new_errors,
                        }
                    ),
                    "result": failed_result,
                    "done": True,
                    "failed": True,
                    "generated_artifacts": updated_artifacts,
                }
            )

        await send_progress(
            state,
            f"Initiating repair retry iteration {iteration} for platforms: {failed_plats}. Errors: {error_summary}",
        )
        # Advance repair_iteration in the artifact registry.
        await _write_repair_state(
            ctx,
            state.spoq_tasks,
            iteration=iteration,
            blocked=False,
            generated_artifacts=state.generated_artifacts,
        )

        updated_artifacts = {**state.generated_artifacts}
        if is_spoq:
            repair_task_ids = {t.id for t in state.spoq_tasks if t.status in ("in_progress", "pending")}
            for task_id in repair_task_ids:
                existing = updated_artifacts.get(task_id, {})
                updated_artifacts[task_id] = {
                    **existing,
                    "status": "repairing",
                    "repair_iteration": str(iteration),
                }

        updated_failed_platforms = {**state.failed_platforms}
        for p in failed_plats:
            updated_failed_platforms[p] = False

        return state.model_copy(
            update={
                "last_node": "repair_agent",
                "context": ctx.model_copy(
                    update={
                        "repair_iteration": iteration,
                        "validation_errors": new_errors,
                    }
                ),
                "result": None,
                "failed_platforms": updated_failed_platforms,
                "done": False,
                "generated_artifacts": updated_artifacts,
            }
        )

    except Exception as e:
        logger.error("CRITICAL ERROR in repair phase: %s", e)
        raise
