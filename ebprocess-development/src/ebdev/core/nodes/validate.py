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
from ebdev.core.spoq_map import load_map_manifest, mark_epic_status, next_ready_epic, save_map_manifest
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
    spoq_epic_dir: str | None = None
    if is_spoq:
        if ctx.spoq_epic_dir is None:
            raise ValueError("spoq_epic_dir cannot be None when execution_mode is 'spoq'")
        spoq_epic_dir = ctx.spoq_epic_dir
        active_tasks = get_active_wave_tasks(spoq_epic_dir)
        tasks_to_validate = []
        for t in active_tasks:
            for p in t.get("skills_required", []):
                tasks_to_validate.append((t["id"], p))
        # Deduplicate
        seen = set()
        unique_tasks = []
        for tp in tasks_to_validate:
            if tp not in seen:
                seen.add(tp)
                unique_tasks.append(tp)
        tasks_to_validate = unique_tasks
        platforms = list({p for _, p in tasks_to_validate})
    else:
        platforms = ctx.platforms
        tasks_to_validate = [("", p) for p in platforms]
    repo_path = Path(ctx.repo_path)
    
    await send_progress(state, f"Validating: Running stage {state.current_stage + 1} quality validation gates concurrently...")
    start_time = time.time()
    
    platform_errors: dict[str, list[str]] = {}
    
    # 1. Async Validation Worker Function
    async def validate_single_task_platform(task_id: str, platform: str) -> tuple[str, list[str]]:
        task_label = f"[{task_id}:{platform}]" if task_id else f"[{platform}]"
        logger.info("%s Running validator...", task_label)
        plat_path = ctx.platform_path(platform)
        
        if not is_spoq:
            # Legacy non-SPOQ mode fallback
            strategy = get_platform_strategy(platform)
            try:
                errors = await strategy.validate(plat_path)
                return platform, errors
            except (EbDevError, OSError, asyncio.TimeoutError) as e:
                err_msg = f"System check exception during validation on platform {platform}: {str(e)}"
                return platform, [err_msg]
                
        # SPOQ Mode: Run the @code_evaluator agent via OpenCode
        eval_ctx = ctx.model_copy(update={
            "repo_path": str(plat_path),
            "platform": platform,
            "active_task_id": task_id,
            "current_agent": "code_evaluator"
        })
        
        loop = asyncio.get_running_loop()
        def _on_opencode_progress(msg: str):
            asyncio.run_coroutine_threadsafe(
                send_progress(state, f"{task_label} Evaluator: {msg}"), loop
            )
            
        session_id_key = f"{ctx.ticket_id}_{platform}"
        session_ids = getattr(state, "opencode_session_ids", {}) or {}
        from ebdev.services import db
        existing_session_id = session_ids.get(platform) or await db.get_session_id(session_id_key)
        
        try:
            from ebdev.services.opencode import invoke_opencode
            result, _ = await asyncio.to_thread(
                invoke_opencode,
                eval_ctx,
                progress_callback=_on_opencode_progress,
                session_id=existing_session_id
            )
            
            if result.status == "failed" or result.errors:
                errs = result.errors or []
                remediation = getattr(result, "remediation", "") or (result.summary if result.status == "failed" else "")
                if remediation:
                    errs.append(remediation)
                return platform, errs
            else:
                logger.info("%s Code validation passed: %s", task_label, result.summary)
                return platform, []
        except Exception as e:
            return platform, [f"Evaluator execution exception: {e}"]

    try:
        # Run active platform validation checks concurrently
        runs = await asyncio.gather(*[validate_single_task_platform(t, p) for t, p in tasks_to_validate])
        platform_errors = {}
        for p, errs in runs:
            platform_errors.setdefault(p, []).extend(errs)
        
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

        # Determine if we should proceed to the next stage, epic, or finish.
        next_stage = state.current_stage
        stages_finished = True
        next_epic_dir: str | None = None

        if all_passed:
            if is_spoq and spoq_epic_dir is not None:
                from ebdev.core.spoq_utils import get_spoq_tasks, update_roadmap_status

                # Mark active tasks of the current wave as completed since they passed validation
                for t in active_tasks:
                    update_spoq_task_status(spoq_epic_dir, t["id"], "completed")
                logger.info("Marked SPOQ tasks as completed for the current wave.")

                # Re-load all tasks to evaluate if the entire epic is finished
                all_tasks = get_spoq_tasks(spoq_epic_dir)
                remaining = [t for t in all_tasks if t.get("status", "pending") in ["pending", "blocked"]]
                current_epic_done = not remaining

                if current_epic_done:
                    if ctx.spoq_map_dir:
                        map_dir = Path(ctx.spoq_map_dir)
                        program_map = load_map_manifest(map_dir)
                        current_epic_id = Path(spoq_epic_dir).name
                        roadmap_path = map_dir.parent / "ROADMAP.md"

                        mark_epic_status(program_map, current_epic_id, "done")
                        update_roadmap_status(str(roadmap_path), current_epic_id, "done")

                        next_epic = next_ready_epic(program_map)
                        if next_epic is not None:
                            mark_epic_status(program_map, next_epic.id, "in-progress")
                            next_epic_dir = str(map_dir / "epics" / next_epic.id)
                            update_roadmap_status(str(roadmap_path), next_epic.id, "in-progress")
                            stages_finished = False
                            status_msg = f"Epic {current_epic_id} passed. Advancing to next epic {next_epic.id}."
                        else:
                            status_msg = "All validations passed across the full SPOQ map."
                        save_map_manifest(map_dir, program_map)
                    else:
                        status_msg = "All validations passed."
                else:
                    stages_finished = False
                    status_msg = f"Wave transition complete. Remaining tasks stay queued in the active epic."
            elif not is_spoq and state.strategy and state.strategy.stages:
                if state.current_stage < len(state.strategy.stages) - 1:
                    next_stage = state.current_stage + 1
                    stages_finished = False
                    status_msg = f"Stage {state.current_stage + 1} passed. Advancing to next stage..."
                else:
                    status_msg = "All validations passed."
            else:
                status_msg = "All validations passed."
        else:
            status_msg = f"Validation failed with {len(combined_errors)} check errors. Repair needed."

        await send_progress(state, status_msg)

        # Update context validation errors
        updated_ctx = ctx.model_copy(update={
            "validation_errors": combined_errors,
            "spoq_epic_dir": next_epic_dir or spoq_epic_dir,
        })

        return state.model_copy(update={
            "context": updated_ctx,
            "done": all_passed and stages_finished,
            "current_stage": next_stage,
            "done_platforms": done_platforms,
            "failed_platforms": failed_platforms,
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
