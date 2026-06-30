# -*- coding: utf-8 -*-
"""Repair node - manages retry counts and platform-specific error aggregation for recovery."""

from __future__ import annotations

import time
from ebdev.config import config
from ebdev.models.schemas import GraphState, JobResult
from ebdev.core.nodes.common import send_progress


async def repair_node(state: GraphState) -> GraphState:
    """Analyze failed platforms, formatting repair contexts and retry counts."""
    state.last_node = "repair"
    iteration = state.context.repair_iteration + 1
    
    msg = f"Repairing: Evaluating recovery iteration #{iteration}..."
    await send_progress(state, msg)
    start_time = time.time()
    ctx = state.context
    platforms = ctx.platforms
    
    try:
        # Determine which platforms failed
        failed_plats = [p for p in platforms if state.failed_platforms.get(p)]
        print(f"[repair] Failed platforms requiring repair: {failed_plats}")

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
                job_id=ctx.jira_ticket_id,
                jira_space_name=ctx.jira_space_name,
                jira_id=ctx.jira_ticket_id,
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

        return state.model_copy(update={
            "last_node": "repair",
            "context": ctx.model_copy(update={
                "repair_iteration": iteration,
                "validation_errors": new_errors,
            }),
            "done": False,
        })

    except Exception as e:
        print(f"[repair] CRITICAL ERROR: {e}")
        raise
