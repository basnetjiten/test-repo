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
    """Run code verification (linters/tests) concurrently for all platforms using Strategy patterns."""
    state.last_node = "validate"
    ctx = state.context
    platforms = ctx.platforms
    repo_path = Path(ctx.repo_path)
    
    await send_progress(state, "Validating: Running platform linter and test validation concurrently...")
    start_time = time.time()
    
    platform_errors: dict[str, list[str]] = {}
    validation_outputs: dict[str, str] = {}
    
    # 1. Async Validation Worker Function
    async def validate_single_platform(platform: str) -> tuple[str, list[str]]:
        print(f"[validate][{platform}] Running checks...")
        if len(platforms) > 1:
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
        # Run all platform validation checks concurrently
        runs = await asyncio.gather(*[validate_single_platform(p) for p in platforms])
        platform_errors = dict(runs)
        
        duration = round(time.time() - start_time, 2)
        print(f"[validate] Concurrence checks completed in {duration}s.")

        # Consolidate results: Graph passes ONLY if all platform error lists are empty
        all_passed = True
        combined_errors = []
        done_platforms = {}
        failed_platforms = {}

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

        status_msg = "All validations passed." if all_passed else f"Validation failed with {len(combined_errors)} check errors. Repair needed."
        await send_progress(state, status_msg)

        # Update context validation errors
        updated_ctx = ctx.model_copy(update={"validation_errors": combined_errors})

        return state.model_copy(update={
            "context": updated_ctx,
            "done": all_passed,
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
