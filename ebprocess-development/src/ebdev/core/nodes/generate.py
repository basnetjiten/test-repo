# -*- coding: utf-8 -*-
"""Generate node - triggers builders concurrently using OpenCode."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ebdev.config import config
from ebdev.models.schemas import GraphState, JobResult
from ebdev.services.opencode import invoke_opencode
from ebdev.services import db
from ebdev.core.nodes.common import send_progress


async def generate_node(state: GraphState) -> GraphState:
    """Invokes builder agents concurrently for active platforms using asyncio.gather."""
    state.last_node = "generate"
    await send_progress(state, "Coding: Generating code implementations concurrently...")
    start_time = time.time()
    
    ctx = state.context
    platforms = ctx.platforms
    repo_path = Path(ctx.repo_path)
    
    # If the previous planning step failed, skip code generation
    if state.result and state.result.status == "failed":
        print("[generate] Skipping due to plan failure.")
        return state.model_copy(update={"last_node": "generate"})

    plans_dir = Path(config.OPENCODE_PROJECT_DIR) / "plans"
    results: dict[str, JobResult] = {}
    session_ids: dict[str, str] = {**state.opencode_session_ids}

    # 1. Async Generation Worker Function
    async def generate_single_platform(platform: str) -> tuple[str, JobResult, str | None]:
        print(f"[generate][{platform}] Running builder...")
        
        # Check if platform is already done and successfully validated in previous iterations
        if state.done_platforms.get(platform) is True:
            print(f"[generate][{platform}] Skipping because platform already successfully validated.")
            existing_result = state.platform_results.get(platform) or JobResult(
                job_id=ctx.jira_ticket_id,
                jira_space_name=ctx.jira_space_name,
                jira_id=ctx.jira_ticket_id,
                status="success",
                summary="Skip: Platform successfully validated in previous iteration."
            )
            return platform, existing_result, session_ids.get(platform)
        
        # Verify plan file exists
        if len(platforms) > 1:
            plat_path = repo_path / platform
            plan_file = plans_dir / f"{platform}_plan.md"
        else:
            plat_path = repo_path
            plan_file = plans_dir / "plan.md"

        if not plan_file.exists():
            err_result = JobResult(
                job_id=ctx.jira_ticket_id,
                jira_space_name=ctx.jira_space_name,
                jira_id=ctx.jira_ticket_id,
                status="failed",
                errors=[f"Plan file {plan_file.name} missing. Planner must succeed first."]
            )
            return platform, err_result, None

        # Select agent role
        label = getattr(ctx, "jira_label", "feature")
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
        session_id_key = f"{ctx.jira_ticket_id}_{platform}"
        existing_session_id = session_ids.get(platform) or await db.get_session_id(session_id_key)
        
        jira_id = ctx.jira_ticket.id if ctx.jira_ticket else None
        
        if not existing_session_id and label in ("bug", "ui_refine") and ctx.linked_jira_ids:
            for linked_id in ctx.linked_jira_ids:
                linked_session_key = f"{linked_id}_{platform}"
                existing_session_id = await db.get_session_id(linked_session_key)
                if not existing_session_id:
                    existing_session_id = await db.get_session_id_by_jira_id(linked_session_key)
                if existing_session_id:
                    print(f"[generate][{platform}] Resuming session {existing_session_id!r} from linked ticket {linked_id}")
                    break

        if not existing_session_id:
            existing_session_id = await db.get_session_id(session_id_key)
            if not existing_session_id and jira_id:
                existing_session_id = await db.get_session_id_by_jira_id(f"{jira_id}_{platform}")
        
        if existing_session_id:
            print(f"[generate][{platform}] Resuming OpenCode session: {existing_session_id}")

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
            await db.save_session_id(session_id_key, session_id_to_store, jira_id=f"{jira_id}_{platform}")

        return platform, result, session_id_to_store

    try:
        # Run all platform builder agents concurrently
        runs = await asyncio.gather(*[generate_single_platform(p) for p in platforms])
        
        for p, res, sid in runs:
            results[p] = res
            if sid:
                session_ids[p] = sid

        duration = round(time.time() - start_time, 2)
        print(f"[generate] Concurrence builders finished in {duration}s.")

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
            job_id=ctx.jira_ticket_id,
            jira_space_name=ctx.jira_space_name,
            jira_id=ctx.jira_ticket_id,
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
        print(f"[generate] CRITICAL ERROR: {e}")
        raise
