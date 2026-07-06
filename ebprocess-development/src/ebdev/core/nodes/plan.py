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
        spoq_epic_dir = ctx.spoq_epic_dir
        tasks = get_active_wave_tasks(spoq_epic_dir)
        # Extract tasks and their required platforms
        tasks_to_plan = []
        for t in tasks:
            for p in t.get("skills_required", []):
                tasks_to_plan.append((t["id"], p))
                
        # Remove duplicates while preserving order
        seen = set()
        unique_tasks = []
        for tp in tasks_to_plan:
            if tp not in seen:
                seen.add(tp)
                unique_tasks.append(tp)
        tasks_to_plan = unique_tasks
    else:
        # Fallback to standard platform list
        tasks_to_plan = [("", p) for p in ctx.platforms]
        
    platforms = list({p for _, p in tasks_to_plan})
    
    # Reset done and failed platform states for the active platforms in the current wave
    done_platforms = {**state.done_platforms}
    failed_platforms = {**state.failed_platforms}
    for p in platforms:
        done_platforms[p] = False
        failed_platforms[p] = False

    repo_path = Path(ctx.repo_path)
    results: dict[str, JobResult] = {}
    
    # 1. Async Planning Worker Function
    async def plan_single_task_platform(task_id: str, platform: str) -> tuple[str, JobResult]:
        task_label = f"[{task_id}:{platform}]" if task_id else f"[{platform}]"
        logger.info("%s Running planner...", task_label)
        
        plat_path = ctx.platform_path(platform)
        task_id_str = str(task_id) if task_id else "default"
        if "-" in task_id_str:
            parts = task_id_str.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                phase_prefix = parts[0]
                jira_id = parts[1]
                task_id_dir = jira_id
                file_prefix = f"{phase_prefix}_"
            else:
                task_id_dir = task_id_str
                file_prefix = ""
        else:
            task_id_dir = task_id_str
            file_prefix = ""
            
        plan_file = ctx.project_storage_dir() / "plans" / task_id_dir / f"{file_prefix}plan_{platform}.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
            
        if plan_file.exists():
            plan_file.unlink()
            
        # Localized job context for this platform planning execution
        plat_ctx = ctx.model_copy(update={
            "repo_path": str(plat_path),
            "platform": platform,
            "active_task_id": task_id,
            "current_agent": "plan"
        })
        
        loop = asyncio.get_running_loop()
        def _on_opencode_progress(msg: str):
            asyncio.run_coroutine_threadsafe(
                send_progress(state, f"{task_label} Planner: {msg}"), loop
            )
            
        result, _ = await asyncio.to_thread(
            invoke_opencode,
            plat_ctx,
            progress_callback=_on_opencode_progress
        )
        
        # Verify plan output file or task YAML enrichment
        if result.status == "success":
            if is_spoq:
                # SPOQ Mode: Task YAML is enriched in-place
                assert spoq_epic_dir is not None  # guaranteed by check above
                yaml_path = Path(spoq_epic_dir) / f"{task_id_str}.yml"
                if not yaml_path.exists():
                    result = result.model_copy(update={
                        "status": "failed",
                        "errors": (result.errors or []) + [f"Task YAML file {yaml_path.name} missing."]
                    })
                else:
                    try:
                        import yaml
                        with open(yaml_path, 'r', encoding='utf-8') as f:
                            task_data = yaml.safe_load(f) or {}
                        desc = task_data.get("description", "")
                        files = task_data.get("files_to_touch", [])
                        criteria = task_data.get("acceptance_criteria", [])
                        
                        if not desc or len(desc.strip()) < 50:
                            result = result.model_copy(update={
                                "status": "failed",
                                "errors": (result.errors or []) + [f"Task YAML {yaml_path.name} has no enriched description."]
                            })
                        elif not files:
                            result = result.model_copy(update={
                                "status": "failed",
                                "errors": (result.errors or []) + [f"Task YAML {yaml_path.name} has empty files_to_touch."]
                            })
                        elif not criteria:
                            result = result.model_copy(update={
                                "status": "failed",
                                "errors": (result.errors or []) + [f"Task YAML {yaml_path.name} has empty acceptance_criteria."]
                            })
                        else:
                            logger.info("%s Verified task YAML enrichment in %s", task_label, yaml_path.name)
                    except Exception as e:
                        result = result.model_copy(update={
                            "status": "failed",
                            "errors": (result.errors or []) + [f"Failed to parse task YAML {yaml_path.name}: {e}"]
                        })
            else:
                # Non-SPOQ Fallback: verify plan file MD
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
                    logger.info("%s Verified plan file size: %d bytes", task_label, plan_file.stat().st_size)
                
        return f"{task_id}_{platform}", result

    try:
        # Run all planners concurrently
        platform_runs = await asyncio.gather(*[plan_single_task_platform(t, p) for t, p in tasks_to_plan])
        results = dict(platform_runs)
        
        duration = round(time.time() - start_time, 2)
        logger.info("Concurrent planners completed in %ss.", duration)

        # Consolidate results: if any platform planner failed, the step fails
        overall_status = "success"
        combined_errors = []
        combined_warnings = []
        
        for task_plat, res in results.items():
            platform = task_plat.split("_")[-1] if "_" in task_plat else task_plat
            if res.status == "failed":
                overall_status = "failed"
                failed_platforms[platform] = True
                combined_errors.extend(res.errors or [])
                logger.error("[%s] Planner failed. Errors: %s", task_plat, res.errors)
            combined_warnings.extend(res.warnings or [])

        consolidated_result = JobResult(
            task_id=ctx.ticket_id,
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
