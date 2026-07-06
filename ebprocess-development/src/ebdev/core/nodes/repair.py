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

import logging
import time
from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.nodes.common import send_progress
from ebdev.models.schemas import JobResult

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


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
    iteration = state.context.repair_iteration + 1

    msg = f"Repairing: Evaluating recovery iteration #{iteration}..."
    await send_progress(state, msg)
    start_time = time.time()
    ctx = state.context

    is_spoq = state.is_spoq
    if is_spoq:
        # Derive active platforms from current state tasks
        active_task_ids = {
            t.id
            for t in state.spoq_tasks
            if t.status in ("pending", "blocked", "in_progress")
        }
        all_platforms: list[str] = []
        for t in state.spoq_tasks:
            if t.id in active_task_ids:
                all_platforms.extend(t.skills_required)
        active_platforms = list(dict.fromkeys(all_platforms))
    else:
        active_platforms = (
            state.strategy.stages[state.current_stage]
            if (state.strategy and state.strategy.stages)
            else ctx.platforms
        )

    try:
        failed_plats = [p for p in active_platforms if state.failed_platforms.get(p)]
        logger.info(
            "Active stage failed platforms requiring repair: %s", failed_plats
        )

        failed_errors: list[str] = []
        for p in failed_plats:
            failed_errors.append(f"--- platform {p} ---")
            plat_prefix = f"[{p}]"
            plat_errs = [
                e for e in ctx.validation_errors if e.startswith(plat_prefix)
            ]
            if plat_errs:
                failed_errors.extend(plat_errs)
            else:
                failed_errors.append(f"Validation failed on platform '{p}'")

        prev_errors = state.result.errors if state.result else []
        new_errors = (
            failed_errors
            + prev_errors
            + [f"--- Repair attempt iteration {iteration} ---"]
        )

        if iteration >= config.MAX_REPAIR_ITERATIONS:
            failed_result = JobResult(
                task_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="failed",
                errors=new_errors,
                summary=(
                    f"Max repair iterations ({config.MAX_REPAIR_ITERATIONS}) "
                    "reached. Validations still failing."
                ),
            )

            await send_progress(
                state,
                f"Max repair iterations reached. "
                f"Job failed with platforms unresolved: {failed_plats}",
            )

            return state.model_copy(update={
                "last_node": "repair_agent",
                "context": ctx.model_copy(update={
                    "repair_iteration": iteration,
                    "validation_errors": new_errors,
                }),
                "result": failed_result,
                "done": True,
                "failed": True,
            })

        await send_progress(
            state,
            f"Initiating repair retry iteration {iteration} "
            f"for platforms: {failed_plats}...",
        )

        updated_failed_platforms = {**state.failed_platforms}
        for p in failed_plats:
            updated_failed_platforms[p] = False

        return state.model_copy(update={
            "last_node": "repair_agent",
            "context": ctx.model_copy(update={
                "repair_iteration": iteration,
                "validation_errors": new_errors,
            }),
            "result": None,
            "failed_platforms": updated_failed_platforms,
            "done": False,
        })

    except Exception as e:
        logger.error("CRITICAL ERROR in repair phase: %s", e)
        raise
