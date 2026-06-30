# -*- coding: utf-8 -*-
"""Plan node - triggers multi-platform planning concurrently using OpenCode."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ebdev.config import config
from ebdev.models.schemas import GraphState, JobResult
from ebdev.services.opencode import invoke_opencode
from ebdev.core.nodes.common import send_progress


async def plan_node(state: GraphState) -> GraphState:
    """Invokes planners concurrently for all active platforms using asyncio.gather."""
    state.last_node = "plan"
    await send_progress(state, "Planning: Analyzing multi-platform architecture and requirements...")
    start_time = time.time()
    
    ctx = state.context
    platforms = ctx.platforms
    repo_path = Path(ctx.repo_path)
    
    plans_dir = Path(config.OPENCODE_PROJECT_DIR) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    
    results: dict[str, JobResult] = {}
    
    # 1. Async Planning Worker Function
    async def plan_single_platform(platform: str) -> tuple[str, JobResult]:
        print(f"[plan][{platform}] Running planner...")
        
        if len(platforms) > 1:
            plat_path = repo_path / platform
            plan_file = plans_dir / f"{platform}_plan.md"
        else:
            plat_path = repo_path
            plan_file = plans_dir / "plan.md"
            
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
                print(f"[plan][{platform}] Verified plan file size: {plan_file.stat().st_size} bytes")
                
        return platform, result

    try:
        # Run all planners concurrently
        platform_runs = await asyncio.gather(*[plan_single_platform(p) for p in platforms])
        results = dict(platform_runs)
        
        duration = round(time.time() - start_time, 2)
        print(f"[plan] Concurrence planners completed in {duration}s.")

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
            job_id=ctx.jira_ticket_id,
            jira_space_name=ctx.jira_space_name,
            jira_id=ctx.jira_ticket_id,
            status=overall_status,
            summary=f"Planners completed. Consolidated status: {overall_status}",
            errors=combined_errors,
            warnings=combined_warnings
        )

        return state.model_copy(update={
            "last_node": "plan",
            "result": consolidated_result,
            "platform_results": {**state.platform_results, **results}
        })

    except Exception as e:
        print(f"[plan] CRITICAL ERROR: {e}")
        raise
