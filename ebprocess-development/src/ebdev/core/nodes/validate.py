# -*- coding: utf-8 -*-
"""Validate node - checks code linting, compilation, and tests concurrently."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ebdev.models.schemas import GraphState
from ebdev.platforms import get_platform_strategy
from ebdev.core.nodes.common import send_progress


async def validate_node(state: GraphState) -> GraphState:
    """Run code verification (linters/tests) concurrently for all active stage platforms."""
    state.last_node = "validate"
    ctx = state.context
    active_platforms = state.strategy.stages[state.current_stage] if (state.strategy and state.strategy.stages) else ctx.platforms
    repo_path = Path(ctx.repo_path)
    
    await send_progress(state, f"Validating: Running stage {state.current_stage + 1} linter and test validation concurrently...")
    start_time = time.time()
    
    platform_errors: dict[str, list[str]] = {}
    
    # 1. Async Validation Worker Function
    async def validate_single_platform(platform: str) -> tuple[str, list[str]]:
        print(f"[validate][{platform}] Running checks...")
        if len(ctx.platforms) > 1:
            plat_path = repo_path / platform
        else:
            plat_path = repo_path

        strategy = get_platform_strategy(platform)
        try:
            errors = await strategy.validate(plat_path)
            return platform, errors
        except Exception as e:
            err_msg = f"System check exception during validation on platform {platform}: {str(e)}"
            return platform, [err_msg]

    try:
        # Run active platform validation checks concurrently
        runs = await asyncio.gather(*[validate_single_platform(p) for p in active_platforms])
        platform_errors = dict(runs)
        
        duration = round(time.time() - start_time, 2)
        print(f"[validate] Stage {state.current_stage + 1} checks completed in {duration}s.")

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
                print(f"[validate][{p}] FAILED with {len(errs)} error(s)")
            else:
                done_platforms[p] = True
                failed_platforms[p] = False
                print(f"[validate][{p}] PASSED")

        # Determine if we should proceed to the next stage or finish
        next_stage = state.current_stage
        stages_finished = True
        if all_passed and state.strategy and state.strategy.stages:
            if state.current_stage < len(state.strategy.stages) - 1:
                next_stage = state.current_stage + 1
                stages_finished = False

        status_msg = "All validations passed." if all_passed else f"Validation failed with {len(combined_errors)} check errors. Repair needed."
        if all_passed and not stages_finished:
            status_msg = f"Stage {state.current_stage + 1} passed. Advancing to stage {next_stage + 1}..."
        await send_progress(state, status_msg)

        # Update context validation errors
        updated_ctx = ctx.model_copy(update={"validation_errors": combined_errors})

        return state.model_copy(update={
            "context": updated_ctx,
            "done": all_passed and stages_finished,
            "current_stage": next_stage,
            "done_platforms": done_platforms,
            "failed_platforms": failed_platforms
        })

    except Exception as e:
        err_msg = f"System failure during validation phase: {str(e)}"
        print(f"[validate] {err_msg}")
        await send_progress(state, "Validation failed due to validation system error.")
        
        updated_ctx = ctx.model_copy(update={"validation_errors": [err_msg]})
        return state.model_copy(update={
            "context": updated_ctx,
            "done": False,
            "failed": True
        })
