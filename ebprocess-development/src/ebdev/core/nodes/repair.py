# -*- coding: utf-8 -*-
"""
repair.py
=========
Repair node - manages retry counts and platform-specific error aggregation for recovery.

Responsibilities
----------------
* Identify active failing platforms for the current stage.
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
from ebdev.core.spoq_utils import get_active_wave_tasks
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

    Raises
    ------
    ValueError
        If the SPOQ epic task directory is missing when execution mode is SPOQ.
    """
    state.last_node = "repair"
    iteration = state.context.repair_iteration + 1
    
    msg = f"Repairing: Evaluating recovery iteration #{iteration}..."
    await send_progress(state, msg)
    start_time = time.time()
    ctx = state.context
    
    is_spoq = state.is_spoq
    if is_spoq:
        if ctx.spoq_epic_dir is None:
            raise ValueError("spoq_epic_dir cannot be None when execution_mode is 'spoq'")
        spoq_epic_dir = ctx.spoq_epic_dir
        tasks = get_active_wave_tasks(spoq_epic_dir)
        active_platforms = []
        for t in tasks:
            active_platforms.extend(t.get("skills_required", []))
        active_platforms = list(dict.fromkeys(active_platforms))
    else:
        active_platforms = state.strategy.stages[state.current_stage] if (state.strategy and state.strategy.stages) else ctx.platforms
    
    try:
        # Determine which platforms failed in the active stage
        failed_plats = [p for p in active_platforms if state.failed_platforms.get(p)]
        logger.info("Active stage failed platforms requiring repair: %s", failed_plats)

        # Gather errors only from failing platforms
        failed_errors = []
        for p in failed_plats:
            failed_errors.append(f"--- platform {p} ---")
            # Filter context errors for this platform prefix
            plat_prefix = f"[{p}]"
            plat_errs = [e for e in ctx.validation_errors if e.startswith(plat_prefix)]
            if plat_errs:
                failed_errors.extend(plat_errs)
            else:
                failed_errors.append(f"Validation failed on platform '{p}'")

        # Accumulate validation errors for OpenCode builder retry feedback
        prev_errors = state.result.errors if state.result else []
        new_errors = failed_errors + prev_errors + [
            f"--- Repair attempt iteration {iteration} ---"
        ]

        # Reached limit - abort job
        if iteration >= config.MAX_REPAIR_ITERATIONS:
            failed_result = JobResult(
                task_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="failed",
                errors=new_errors,
                summary=f"Max repair iterations ({config.MAX_REPAIR_ITERATIONS}) reached. Validations still failing."
            )
            
            await send_progress(state, f"Max repair iterations reached. Job failed with platforms unresolved: {failed_plats}")

            return state.model_copy(update={
                "last_node": "repair",
                "context": ctx.model_copy(update={
                    "repair_iteration": iteration,
                    "validation_errors": new_errors,
                }),
                "result": failed_result,
                "done": True,
                "failed": True,
            })

        # Update retry iteration and errors list, route back to generate
        await send_progress(state, f"Initiating repair retry iteration {iteration} for platforms: {failed_plats}...")

        updated_failed_platforms = {**state.failed_platforms}
        for p in failed_plats:
            updated_failed_platforms[p] = False

        return state.model_copy(update={
            "last_node": "repair",
            "context": ctx.model_copy(update={
                "repair_iteration": iteration,
                "validation_errors": new_errors,
            }),
            "result": None,  # Clear the failed result to allow generate/builder to run again
            "failed_platforms": updated_failed_platforms,
            "done": False,
        })

    except Exception as e:
        logger.error("CRITICAL ERROR in repair phase: %s", e)
        raise
