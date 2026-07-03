# -*- coding: utf-8 -*-
"""
plan.py
=======
Plan node - triggers multi-platform planning concurrently using OpenCode.

Responsibilities
----------------
* Identify active platforms for the current execution wave.
* Invoke OpenCode planners concurrently to generate implementation plans.
* Verify generated plan files (existence and minimum file size).
* Consolidate planner results to update the graph state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_utils import get_active_wave_tasks
from ebdev.models.schemas import JobResult
from ebdev.services.opencode import invoke_opencode

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def plan_node(state: GraphState) -> GraphState:
    """
    Invoke planners concurrently for all active stage platforms using asyncio.gather.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with the consolidated planner results.

    Raises
    ------
    ValueError
        If the SPOQ epic task directory is missing when execution mode is SPOQ.
    """
    state.last_node = "plan"
    await send_progress(state, f"Planning: Analyzing stage {state.current_stage + 1} architecture and requirements...")
    start_time = time.time()
    
    ctx = state.context
    
    is_spoq = state.is_spoq
    if is_spoq:
        if ctx.spoq_epic_dir is None:
            raise ValueError("spoq_epic_dir cannot be None when execution_mode is 'spoq'")
        tasks = get_active_wave_tasks(ctx.spoq_epic_dir)
        # Extract platforms from the skills_required of active tasks
        platforms = []
        for t in tasks:
            platforms.extend(t.get("skills_required", []))
        # Remove duplicates while preserving order
        platforms = list(dict.fromkeys(platforms))
    else:
        # Fallback to standard platform list
        platforms = ctx.platforms
    # Reset done and failed platform states for the active platforms in the current wave
    done_platforms = {**state.done_platforms}
    failed_platforms = {**state.failed_platforms}
    for p in platforms:
        done_platforms[p] = False
        failed_platforms[p] = False

    repo_path = Path(ctx.repo_path)
    results: dict[str, JobResult] = {}
    
    # 1. Async Planning Worker Function
    async def plan_single_platform(platform: str) -> tuple[str, JobResult]:
        logger.info("[%s] Running planner...", platform)
        
        plat_path = ctx.platform_path(platform)
        # Project-scoped plan file: .opencode/<space_name>/<platform>_plan.md
        prefix = f"{ctx.job_id}_" if ctx.job_id else ""
        plan_file = ctx.project_storage_dir(config.OPENCODE_PROJECT_DIR) / f"{prefix}{platform}_plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
            
        if plan_file.exists():
            plan_file.unlink()
            
        # Localized job context for this platform planning execution
        plat_ctx = ctx.model_copy(update={
            "repo_path": str(plat_path),
            "platform": platform,
            "current_agent": "plan"
        })
        
        loop = asyncio.get_running_loop()
        def _on_opencode_progress(msg: str):
            asyncio.run_coroutine_threadsafe(
                send_progress(state, f"[{platform}] Planner: {msg}"), loop
            )
            
        result, _ = await asyncio.to_thread(
            invoke_opencode,
            plat_ctx,
            progress_callback=_on_opencode_progress
        )
        
        # Verify plan output file
        if result.status == "success":
            if not plan_file.exists():
                result = result.model_copy(update={
                    "status": "failed",
                    "errors": (result.errors or []) + [f"Plan file {plan_file.name} missing."]
                })
            elif plan_file.stat().st_size < 50:
                result = result.model_copy(update={
                    "status": "failed",
                    "errors": (result.errors or []) + [f"Plan file {plan_file.name} is too small."]
                })
            else:
                logger.info("[%s] Verified plan file size: %d bytes", platform, plan_file.stat().st_size)
                
        return platform, result

    try:
        # Run all planners concurrently
        platform_runs = await asyncio.gather(*[plan_single_platform(p) for p in platforms])
        results = dict(platform_runs)
        
        duration = round(time.time() - start_time, 2)
        logger.info("Concurrent planners completed in %ss.", duration)

        # Consolidate results: if any platform planner failed, the step fails
        overall_status = "success"
        combined_errors = []
        combined_warnings = []
        
        for p, res in results.items():
            if res.status == "failed":
                overall_status = "failed"
                combined_errors.extend(res.errors or [])
            combined_warnings.extend(res.warnings or [])

        consolidated_result = JobResult(
            job_id=ctx.ticket_id,
            space_name=ctx.space_name,
            ticket_id=ctx.ticket_id,
            status=overall_status,
            summary=f"Planners completed. Consolidated status: {overall_status}",
            errors=combined_errors,
            warnings=combined_warnings
        )

        return state.model_copy(update={
            "last_node": "plan",
            "result": consolidated_result,
            "platform_results": {**state.platform_results, **results},
            "done_platforms": done_platforms,
            "failed_platforms": failed_platforms
        })

    except Exception as e:
        logger.error("CRITICAL ERROR in planning phase: %s", e)
        raise
