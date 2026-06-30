# -*- coding: utf-8 -*-
"""
validate.py
===========
Validate node - checks code linting, compilation, and tests concurrently.

Responsibilities
----------------
* Identify active platforms for the current stage or wave.
* Run platform validation checks (linting, tests, compilation) concurrently.
* Aggregate and record validation errors on failure.
* Update SPOQ task status on task success.
* Handle system-level errors during the validation phase.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.core.exceptions import EbDevError
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_utils import get_active_wave_tasks, update_spoq_task_status
from ebdev.platforms import get_platform_strategy

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def validate_node(state: GraphState) -> GraphState:
    """
    Run code verification concurrently for all active stage platforms.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with the validation results.

    Raises
    ------
    ValueError
        If the SPOQ epic task directory is missing when execution mode is SPOQ.
    """
    state.last_node = "validate"
    ctx = state.context
    
    active_tasks = []
    is_spoq = state.is_spoq
    if is_spoq:
        if ctx.spoq_epic_dir is None:
            raise ValueError("spoq_epic_dir cannot be None when execution_mode is 'spoq'")
        active_tasks = get_active_wave_tasks(ctx.spoq_epic_dir)
        platforms = []
        for t in active_tasks:
            platforms.extend(t.get("skills_required", []))
        platforms = list(dict.fromkeys(platforms))
    else:
        platforms = ctx.platforms
    repo_path = Path(ctx.repo_path)
    
    await send_progress(state, f"Validating: Running stage {state.current_stage + 1} linter and test validation concurrently...")
    start_time = time.time()
    
    platform_errors: dict[str, list[str]] = {}
    
    # 1. Async Validation Worker Function
    async def validate_single_platform(platform: str) -> tuple[str, list[str]]:
        logger.info("[%s] Running checks...", platform)
        if len(ctx.platforms) > 1:
            plat_path = repo_path / platform
        else:
            plat_path = repo_path

        strategy = get_platform_strategy(platform)
        try:
            errors = await strategy.validate(plat_path)
            return platform, errors
        except (EbDevError, OSError, asyncio.TimeoutError) as e:
            err_msg = f"System check exception during validation on platform {platform}: {str(e)}"
            return platform, [err_msg]

    try:
        # Run active platform validation checks concurrently
        runs = await asyncio.gather(*[validate_single_platform(p) for p in platforms])
        platform_errors = dict(runs)
        
        duration = round(time.time() - start_time, 2)
        logger.info("Stage %d checks completed in %ss.", state.current_stage + 1, duration)

        # Consolidate results: Graph passes ONLY if all platform error lists are empty
        all_passed = True
        combined_errors = []
        done_platforms = {**state.done_platforms}
        failed_platforms = {**state.failed_platforms}

        for p, errs in platform_errors.items():
            if errs:
                all_passed = False
                combined_errors.extend([f"[{p}] {e}" for e in errs])
                done_platforms[p] = False
                failed_platforms[p] = True
                logger.info("[%s] FAILED with %d error(s)", p, len(errs))
            else:
                done_platforms[p] = True
                failed_platforms[p] = False
                logger.info("[%s] PASSED", p)

        # Determine if we should proceed to the next stage or finish
        next_stage = state.current_stage
        stages_finished = True
        
        if all_passed and not is_spoq and state.strategy and state.strategy.stages:
            if state.current_stage < len(state.strategy.stages) - 1:
                next_stage = state.current_stage + 1
                stages_finished = False

        status_msg = "All validations passed." if all_passed else f"Validation failed with {len(combined_errors)} check errors. Repair needed."
        if all_passed and not stages_finished:
            status_msg = f"Stage {state.current_stage + 1} passed. Advancing to stage {next_stage + 1}..."
        await send_progress(state, status_msg)

        # Update context validation errors
        updated_ctx = ctx.model_copy(update={"validation_errors": combined_errors})

        # Check if validation passed for all platforms
        if all_passed and is_spoq and ctx.spoq_epic_dir is not None:
            # Mark active SPOQ tasks as completed!
            for t in active_tasks:
                update_spoq_task_status(ctx.spoq_epic_dir, t["id"], "completed")
            logger.info("Marked SPOQ tasks as completed for the current wave.")

        return state.model_copy(update={
            "context": updated_ctx,
            "done": all_passed and stages_finished,
            "current_stage": next_stage,
            "done_platforms": done_platforms,
            "failed_platforms": failed_platforms
        })

    except (EbDevError, ValueError, KeyError, RuntimeError) as e:
        err_msg = f"System failure during validation phase: {str(e)}"
        logger.error(err_msg)
        await send_progress(state, "Validation failed due to validation system error.")
        
        updated_ctx = ctx.model_copy(update={"validation_errors": [err_msg]})
        return state.model_copy(update={
            "context": updated_ctx,
            "done": False,
            "failed": True
        })
