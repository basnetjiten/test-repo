# -*- coding: utf-8 -*-
"""
plan.py
=======
Plan node - triggers multi-platform planning concurrently using OpenCode.

Responsibilities
----------------
* Identify active platforms for the current execution wave.
* Invoke OpenCode planners concurrently to generate implementation plans.
* Verify task enrichment (description, files_to_touch, acceptance_criteria)
  through LangGraph state — no YAML file reads.
* Consolidate planner results to update the graph state.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.core.exceptions import EpicStateError
from ebdev.core.logger import get_logger
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_utils import get_state_active_tasks
from ebdev.models.graph_state import JobResult
from ebdev.models.task import TaskArtifacts, TaskArtifactState
from ebdev.services.epic_state import get_epic_state_service
from ebdev.services.opencode import invoke_opencode

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState, JobContext
    from ebdev.models.task import TaskStatus

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _write_plan_state(
    ctx: "JobContext",
    task_id: str,
    platform: str,
    plan_file: Path,
    *,
    status: "TaskStatus",
) -> None:
    """
    Update the epic's ``state.json`` after a plan file is confirmed on disk.

    Parameters
    ----------
    ctx:
        The current job context — used to resolve the epic directory path.
    task_id:
        The active SPOQ task identifier (e.g. ``contract-41831``).
    platform:
        Target platform for the task.
    plan_file:
        Absolute path to the verified plan Markdown file.
    status:
        The lifecycle status to record (``built`` | ``evaluate_failed``).

    Notes
    -----
    Errors are caught and logged rather than re-raised so that a state.json
    write failure never interrupts the main pipeline — the LangGraph
    checkpoint remains the authoritative state source.
    """
    if not ctx.spoq_epic_dir:
        return

    from ebdev.models.task import TaskStatus  # noqa: F401 — Literal type used at runtime

    epic_dir = ctx.project_storage_dir() / ctx.spoq_epic_dir
    svc = get_epic_state_service(epic_dir)

    try:
        snapshot = await svc.load_or_init(
            epic_id=ctx.spoq_epic_dir,
            space_name=ctx.space_name,
        )
        existing = snapshot.get_task(task_id)
        contract_rel = str(plan_file.relative_to(ctx.project_storage_dir().parent))
        updated_artifacts = TaskArtifacts(
            contract=contract_rel,
            journal=existing.artifacts.journal if existing else None,
            schema_file=existing.artifacts.schema_file if existing else None,
            verification=existing.artifacts.verification if existing else None,
        )
        task_state = TaskArtifactState(
            task_id=task_id,
            platform=platform,
            status=status,
            artifacts=updated_artifacts,
            repair_iteration=existing.repair_iteration if existing else 0,
        )
        await svc.update_task_state(snapshot, task_state)
    except EpicStateError as exc:
        logger.warning("Could not update state.json for task %s (non-fatal): %s", task_id, exc)


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
    """
    state.last_node = "planner_agent"
    await send_progress(state, f"Planning: Analyzing stage {state.current_stage + 1} architecture and requirements...")
    start_time = time.time()

    ctx = state.context

    is_spoq = state.is_spoq
    if is_spoq:
        tasks = get_state_active_tasks(state.spoq_tasks)
        tasks_to_plan: list[tuple[str, str]] = []
        for t in tasks:
            for p in t.skills_required:
                tasks_to_plan.append((t.id, p))
        # Deduplicate
        seen: set[tuple[str, str]] = set()
        unique_tasks = []
        for tp in tasks_to_plan:
            if tp not in seen:
                seen.add(tp)
                unique_tasks.append(tp)
        tasks_to_plan = unique_tasks
    else:
        tasks_to_plan = [("", p) for p in ctx.platforms]

    platforms = list({p for _, p in tasks_to_plan})

    done_platforms = {**state.done_platforms}
    failed_platforms = {**state.failed_platforms}
    for p in platforms:
        done_platforms[p] = False
        failed_platforms[p] = False

    results: dict[str, JobResult] = {}

    # ------------------------------------------------------------------
    # Async Planning Worker
    # ------------------------------------------------------------------
    async def plan_single_task_platform(task_id: str, platform: str) -> tuple[str, str, JobResult, Path]:
        task_label = f"[{task_id}:{platform}]" if task_id else f"[{platform}]"
        logger.info("%s Running planner...", task_label)

        plat_path = ctx.platform_path(platform)

        if is_spoq and ctx.spoq_epic_dir:
            plan_file = ctx.project_storage_dir() / ctx.spoq_epic_dir / f"{task_id}.md"
        else:
            plan_file_dir = ctx.project_storage_dir() / (task_id or "default")
            plan_file = plan_file_dir / f"plan_{platform}.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)

        # Check if plan already successfully generated/recorded in GraphState
        existing_art = state.generated_artifacts.get(task_id, {})
        if (
            existing_art.get("status") in ("built", "evaluating", "passed")
            and plan_file.exists()
            and plan_file.stat().st_size > 50
        ):
            logger.info("%s Plan already exists in registry, skipping plan generation", task_label)
            return (
                task_id,
                platform,
                JobResult(
                    task_id=ctx.ticket_id,
                    ticket_id=ctx.ticket_id,
                    status="success",
                    summary=f"Plan already exists for {task_id}",
                ),
                plan_file,
            )

        if plan_file.exists() and plan_file.stat().st_size > 50:
            logger.info("%s Plan already exists and valid on disk, skipping plan generation", task_label)
            # Ensure the artifact registry reflects the idempotency skip so
            # downstream agents see this task as already planned.
            if ctx.spoq_epic_dir:
                await _write_plan_state(ctx, task_id, platform, plan_file, status="built")
            return (
                task_id,
                platform,
                JobResult(
                    task_id=ctx.ticket_id,
                    ticket_id=ctx.ticket_id,
                    status="success",
                    summary=f"Plan already exists for {task_id}",
                ),
                plan_file,
            )

        plat_ctx = ctx.model_copy(
            update={
                "repo_path": str(plat_path),
                "platform": platform,
                "active_task_id": task_id,
                "current_agent": "plan",
                "shared_context": state.shared_context,
            }
        )

        loop = asyncio.get_running_loop()

        def _on_opencode_progress(msg: str):
            asyncio.run_coroutine_threadsafe(send_progress(state, f"{task_label} Planner: {msg}"), loop)

        result, _ = await asyncio.to_thread(invoke_opencode, plat_ctx, progress_callback=_on_opencode_progress)

        # Verify plan output
        if result.status == "success":
            if not plan_file.exists():
                result = result.model_copy(
                    update={
                        "status": "failed",
                        "errors": (result.errors or []) + [f"Plan file {plan_file.name} missing."],
                    }
                )
            elif plan_file.stat().st_size < 50:
                result = result.model_copy(
                    update={
                        "status": "failed",
                        "errors": (result.errors or []) + [f"Plan file {plan_file.name} is too small."],
                    }
                )
            else:
                logger.info(
                    "%s Verified plan file size: %d bytes",
                    task_label,
                    plan_file.stat().st_size,
                )
                # Register the generated contract artifact in state.json.
                if ctx.spoq_epic_dir and result.status == "success":
                    await _write_plan_state(ctx, task_id, platform, plan_file, status="built")
                elif ctx.spoq_epic_dir and result.status == "failed":
                    await _write_plan_state(ctx, task_id, platform, plan_file, status="evaluate_failed")

        return task_id, platform, result, plan_file

    try:
        # Before executing planner, check if plan already exists in generated_artifacts
        platform_runs = await asyncio.gather(*[plan_single_task_platform(t, p) for t, p in tasks_to_plan])
        results = {}
        updated_artifacts = {**state.generated_artifacts}

        for task_id, platform, res, plan_file in platform_runs:
            key = f"{task_id}_{platform}" if task_id else platform
            results[key] = res

            # Store in GraphState.generated_artifacts
            if is_spoq and task_id:
                contract_rel = ""
                if plan_file and plan_file.exists():
                    try:
                        contract_rel = str(plan_file.relative_to(ctx.project_storage_dir().parent))
                    except ValueError:
                        contract_rel = str(plan_file)

                existing = updated_artifacts.get(task_id, {})
                status = "built" if res.status == "success" else "evaluate_failed"
                updated_artifacts[task_id] = {
                    **existing,
                    "status": status,
                    "contract": contract_rel,
                }

        duration = round(time.time() - start_time, 2)
        logger.info("Concurrent planners completed in %ss.", duration)

        overall_status = "success"
        combined_errors: list[str] = []
        combined_warnings: list[str] = []

        for task_plat, res in results.items():
            platform = task_plat.split("_")[-1] if "_" in task_plat else task_plat
            if res.status == "failed":
                overall_status = "failed"
                failed_platforms[platform] = True
                combined_errors.extend(res.errors or [])
                logger.error("[%s] Planner failed. Errors: %s", task_plat, res.errors)
            combined_warnings.extend(res.warnings or [])

        # Move active SPOQ tasks to in_progress
        updated_spoq_tasks = list(state.spoq_tasks)
        if is_spoq:
            active_task_ids = {t for t, _ in tasks_to_plan}
            new_list: list = []
            for t in updated_spoq_tasks:
                if t.id in active_task_ids and t.status == "pending":
                    new_list.append(t.model_copy(update={"status": "in_progress"}))
                else:
                    new_list.append(t)
            updated_spoq_tasks = new_list

        consolidated_result = JobResult(
            task_id=ctx.ticket_id,
            ticket_id=ctx.ticket_id,
            status=overall_status,
            summary=f"Planners completed. Consolidated status: {overall_status}",
            errors=combined_errors,
            warnings=combined_warnings,
        )

        return state.model_copy(
            update={
                "last_node": "planner_agent",
                "result": consolidated_result,
                "platform_results": {**state.platform_results, **results},
                "done_platforms": done_platforms,
                "failed_platforms": failed_platforms,
                "spoq_tasks": updated_spoq_tasks,
                "generated_artifacts": updated_artifacts,
            }
        )

    except Exception as e:
        logger.error("CRITICAL ERROR in planning phase: %s", e)
        raise
