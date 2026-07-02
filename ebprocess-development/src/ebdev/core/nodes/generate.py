# -*- coding: utf-8 -*-
"""
generate.py
===========
Generate node - triggers builders concurrently using OpenCode.

Responsibilities
----------------
* Identify active platforms for the current execution wave.
* Verify planning stage outputs exist and are valid.
* Concurrently invoke OpenCode builder agents for each platform.
* Store and resume OpenCode conversation session IDs for task continuity.
* Consolidate build results to update the graph state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.nodes.common import send_progress
from ebdev.models.schemas import JobResult
from ebdev.services import db
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
async def generate_node(state: GraphState) -> GraphState:
    """
    Invoke builder agents concurrently for active stage platforms using asyncio.gather.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with the consolidated builder results.

    Raises
    ------
    ValueError
        If the SPOQ epic task directory is missing when execution mode is SPOQ.
    """
    state.last_node = "generate"
    await send_progress(state, f"Coding: Generating stage {state.current_stage + 1} code implementations concurrently...")
    start_time = time.time()
    
    ctx = state.context
    
    is_spoq = state.is_spoq
    if is_spoq:
        if ctx.spoq_epic_dir is None:
            raise ValueError("spoq_epic_dir cannot be None when execution_mode is 'spoq'")
        from ebdev.core.spoq_utils import get_active_wave_tasks
        tasks = get_active_wave_tasks(ctx.spoq_epic_dir)
        platforms = []
        for t in tasks:
            platforms.extend(t.get("skills_required", []))
        platforms = list(dict.fromkeys(platforms))
    else:
        platforms = ctx.platforms
    repo_path = Path(ctx.repo_path)
    
    # If the previous planning step failed, skip code generation
    if state.result and state.result.status == "failed":
        logger.info("Skipping due to plan failure.")
        return state.model_copy(update={"last_node": "generate"})

    results: dict[str, JobResult] = {}
    session_ids: dict[str, str] = {**state.opencode_session_ids}

    # 1. Async Generation Worker Function
    async def generate_single_platform(platform: str) -> tuple[str, JobResult, str | None]:
        logger.info("[%s] Running builder...", platform)
        
        # Check if platform is already done and successfully validated in previous stages/iterations
        if state.done_platforms.get(platform) is True:
            logger.info("[%s] Skipping because platform already successfully validated.", platform)
            existing_result = state.platform_results.get(platform) or JobResult(
                job_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary="Builder skipped. Previously validated successfully."
            )
            return platform, existing_result, session_ids.get(platform)
        
        plat_path = repo_path / platform if len(ctx.platforms) > 1 else repo_path
        # Project-scoped plan file: .opencode/<space_name>/<platform>_plan.md
        plan_file = ctx.project_storage_dir(config.OPENCODE_PROJECT_DIR) / f"{platform}_plan.md"

        if not plan_file.exists():
            err_result = JobResult(
                job_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="failed",
                errors=[f"Plan file {plan_file.name} missing. Planner must succeed first."]
            )
            return platform, err_result, None

        # Select agent role
        label = getattr(ctx, "ticket_label", "feature")
        target_agent = "build"
        if label == "bug":
            target_agent = "bug_fixer"
        elif label == "ui_refine":
            target_agent = "ui_refiner"

        # Localized job context copies
        plat_ctx = ctx.model_copy(update={
            "repo_path": str(plat_path),
            "platform": platform,
            "current_agent": target_agent
        })

        # Fetch session ID for this specific platform
        session_id_key = f"{ctx.ticket_id}_{platform}"
        existing_session_id = session_ids.get(platform) or await db.get_session_id(session_id_key)
        
        ticket_id = ctx.ticket.id if ctx.ticket else None
        
        if not existing_session_id and label in ("bug", "ui_refine") and ctx.linked_ticket_ids:
            for linked_id in ctx.linked_ticket_ids:
                linked_session_key = f"{linked_id}_{platform}"
                existing_session_id = await db.get_session_id(linked_session_key)
                if not existing_session_id:
                    existing_session_id = await db.get_session_id_by_jira_id(linked_session_key)
                if existing_session_id:
                    logger.info("[%s] Resuming session %r from linked ticket %s", platform, existing_session_id, linked_id)
                    break

        if not existing_session_id:
            existing_session_id = await db.get_session_id(session_id_key)
            if not existing_session_id and ticket_id:
                existing_session_id = await db.get_session_id_by_jira_id(f"{ticket_id}_{platform}")
        
        if existing_session_id:
            logger.info("[%s] Resuming OpenCode session: %s", platform, existing_session_id)

        # Progress callback loop
        loop = asyncio.get_running_loop()
        def _on_opencode_progress(msg: str):
            asyncio.run_coroutine_threadsafe(
                send_progress(state, f"[{platform}] Builder: {msg}"), loop
            )

        result, captured_session_id = await asyncio.to_thread(
            invoke_opencode,
            plat_ctx,
            progress_callback=_on_opencode_progress,
            session_id=existing_session_id
        )

        session_id_to_store = captured_session_id or existing_session_id
        if session_id_to_store:
            await db.save_session_id(session_id_key, session_id_to_store, jira_id=f"{ticket_id}_{platform}")

        return platform, result, session_id_to_store

    try:
        # Run all platform builder agents concurrently
        runs = await asyncio.gather(*[generate_single_platform(p) for p in platforms])
        
        for p, res, sid in runs:
            results[p] = res
            if sid:
                session_ids[p] = sid

        duration = round(time.time() - start_time, 2)
        logger.info("Concurrent builders finished in %ss.", duration)

        # Consolidate results
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
            summary=f"Builders finished. Consolidated status: {overall_status}",
            errors=combined_errors,
            warnings=combined_warnings,
            pr_url=list(results.values())[0].pr_url if results else None
        )

        return state.model_copy(update={
            "last_node": "generate",
            "result": consolidated_result,
            "platform_results": {**state.platform_results, **results},
            "opencode_session_ids": session_ids
        })

    except Exception as e:
        logger.error("CRITICAL ERROR in generation phase: %s", e)
        raise
