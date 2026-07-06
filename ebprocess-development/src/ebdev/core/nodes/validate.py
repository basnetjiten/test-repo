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
* Update SPOQ task status in GraphState (persisted via LangGraph checkpointing).
* Advance to the next epic or finish when all tasks are validated.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.core.exceptions import EbDevError
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_utils import get_state_active_tasks
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
    """
    state.last_node = "evaluator_agent"
    ctx = state.context

    is_spoq = state.is_spoq
    if is_spoq:
        active_tasks = get_state_active_tasks(state.spoq_tasks)
        tasks_to_validate: list[tuple[str, str]] = []
        for t in active_tasks:
            for p in t.skills_required:
                tasks_to_validate.append((t.id, p))
        seen: set[tuple[str, str]] = set()
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
        active_tasks = []

    repo_path = Path(ctx.repo_path)

    await send_progress(
        state,
        f"Validating: Running stage {state.current_stage + 1} quality validation gates concurrently...",
    )
    start_time = time.time()

    # ------------------------------------------------------------------
    # Async Validation Worker
    # ------------------------------------------------------------------
    async def validate_single_task_platform(
        task_id: str, platform: str
    ) -> tuple[str, list[str]]:
        task_label = f"[{task_id}:{platform}]" if task_id else f"[{platform}]"
        logger.info("%s Running validator...", task_label)
        plat_path = ctx.platform_path(platform)

        if not is_spoq:
            strategy = get_platform_strategy(platform)
            try:
                errors = await strategy.validate(plat_path)
                return platform, errors
            except (EbDevError, OSError, asyncio.TimeoutError) as e:
                return platform, [
                    f"System check exception during validation on platform {platform}: {str(e)}"
                ]

        eval_ctx = ctx.model_copy(update={
            "repo_path": str(plat_path),
            "platform": platform,
            "active_task_id": task_id,
            "current_agent": "code_evaluator",
        })

        loop = asyncio.get_running_loop()
        def _on_opencode_progress(msg: str):
            asyncio.run_coroutine_threadsafe(
                send_progress(state, f"{task_label} Evaluator: {msg}"), loop
            )

        session_id_key = f"{ctx.ticket_id}_{platform}"
        session_ids = getattr(state, "opencode_session_ids", {}) or {}
        from ebdev.services import db

        existing_session_id = session_ids.get(platform) or await db.get_session_id(
            session_id_key
        )

        try:
            from ebdev.services.opencode import invoke_opencode

            result, _ = await asyncio.to_thread(
                invoke_opencode,
                eval_ctx,
                progress_callback=_on_opencode_progress,
                session_id=existing_session_id,
            )

            if result.status == "failed" or result.errors:
                errs = result.errors or []
                remediation = (
                    getattr(result, "remediation", "")
                    or (result.summary if result.status == "failed" else "")
                )
                if remediation:
                    errs.append(remediation)
                return platform, errs
            else:
                logger.info(
                    "%s Code validation passed: %s", task_label, result.summary
                )
                return platform, []
        except Exception as e:
            return platform, [f"Evaluator execution exception: {e}"]

    try:
        runs = await asyncio.gather(
            *[validate_single_task_platform(t, p) for t, p in tasks_to_validate]
        )
        platform_errors: dict[str, list[str]] = {}
        for p, errs in runs:
            platform_errors.setdefault(p, []).extend(errs)

        duration = round(time.time() - start_time, 2)
        logger.info(
            "Stage %d checks completed in %ss.", state.current_stage + 1, duration
        )

        all_passed = True
        combined_errors: list[str] = []
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

        next_stage = state.current_stage
        stages_finished = True
        updated_spoq_tasks = list(state.spoq_tasks)
        status_msg: str = ""

        if all_passed:
            if is_spoq:
                # Mark current wave tasks as completed in state
                completed_ids = {t.id for t in active_tasks}
                for i, task in enumerate(updated_spoq_tasks):
                    if task.id in completed_ids:
                        updated_spoq_tasks[i] = task.model_copy(
                            update={"status": "completed"}
                        )
                logger.info(
                    "Marked %d SPOQ tasks as completed in state.", len(completed_ids)
                )

                # Check if all tasks in the current epic are done
                remaining = [
                    t
                    for t in updated_spoq_tasks
                    if t.status in ("pending", "blocked")
                ]
                if remaining:
                    stages_finished = False
                    status_msg = (
                        "Wave transition complete. Remaining tasks stay queued."
                    )
                else:
                    # All tasks done — current epic is complete
                    active_epic_id = ctx.shared_context.get("active_epic_id")
                    status_msg = (
                        f"Epic {active_epic_id or 'unknown'} completed."
                        if active_epic_id
                        else "All validations passed."
                    )

            elif state.strategy and state.strategy.stages:
                if state.current_stage < len(state.strategy.stages) - 1:
                    next_stage = state.current_stage + 1
                    stages_finished = False
                    status_msg = (
                        f"Stage {state.current_stage + 1} passed. "
                        "Advancing to next stage..."
                    )
                else:
                    status_msg = "All validations passed."
            else:
                status_msg = "All validations passed."
        else:
            status_msg = (
                f"Validation failed with {len(combined_errors)} check errors. "
                "Repair needed."
            )

        await send_progress(state, status_msg)

        updated_ctx = ctx.model_copy(update={
            "validation_errors": combined_errors,
        })

        return state.model_copy(update={
            "last_node": "evaluator_agent",
            "context": updated_ctx,
            "done": all_passed and stages_finished,
            "current_stage": next_stage,
            "done_platforms": done_platforms,
            "failed_platforms": failed_platforms,
            "spoq_tasks": updated_spoq_tasks,
        })

    except (EbDevError, ValueError, KeyError, RuntimeError) as e:
        err_msg = f"System failure during validation phase: {str(e)}"
        logger.error(err_msg)
        await send_progress(
            state, "Validation failed due to validation system error."
        )

        updated_ctx = ctx.model_copy(update={"validation_errors": [err_msg]})
        return state.model_copy(update={
            "last_node": "evaluator_agent",
            "context": updated_ctx,
            "done": False,
            "failed": True,
        })
