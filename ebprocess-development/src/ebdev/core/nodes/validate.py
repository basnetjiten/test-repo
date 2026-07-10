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
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.core.exceptions import EbDevError, EpicStateError
from ebdev.core.logger import get_logger
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_utils import get_state_active_tasks
from ebdev.models.task import TaskArtifacts, TaskArtifactState
from ebdev.platforms import get_platform_strategy
from ebdev.services import db
from ebdev.services.epic_state import get_epic_state_service
from ebdev.services.fs import AsyncFileSystemService
from ebdev.services.opencode import invoke_opencode

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState, JobContext
    from ebdev.models.spoq import SPOQTask

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _write_validation_state(
    ctx: "JobContext",
    completed_ids: set[str],
    *,
    passed: bool,
    platform_errors: dict[str, list[str]],
    generated_artifacts: dict[str, dict[str, str]] | None = None,
    spoq_tasks: list["SPOQTask"] | None = None,
) -> None:
    """
    Update ``state.json`` after a SPOQ validation wave completes.

    Parameters
    ----------
    ctx:
        Job context used to resolve the epic directory path and space name.
    completed_ids:
        Set of task IDs that were part of this validation wave.
    passed:
        ``True`` when all platforms passed; ``False`` on any failure.
    platform_errors:
        Per-platform error lists — used to derive journal path hints.
    generated_artifacts:
        Optional dictionary of generated artifacts from the GraphState to recover.
    spoq_tasks:
        Optional list of SPOQTask definitions to lookup task platforms.

    Notes
    -----
    Errors are caught and logged rather than re-raised so that a state.json
    write failure never interrupts the main pipeline.
    """
    if not ctx.spoq_epic_dir:
        return

    epic_dir = ctx.project_storage_dir() / ctx.spoq_epic_dir
    svc = get_epic_state_service(epic_dir)

    # Pre-map tasks to their platforms for fast lookup
    task_platforms = {}
    if spoq_tasks:
        task_platforms = {t.id: t.platforms for t in spoq_tasks}

    try:
        failure_count = sum(len(v) for v in platform_errors.values()) if not passed else 0
        if failure_count:
            logger.info(
                "Recording state for %d completed tasks (%d platform failures).",
                len(completed_ids),
                failure_count,
            )

        snapshot = await svc.load_or_init(
            epic_id=ctx.spoq_epic_dir,
            space_name=ctx.space_name,
        )
        for task_id in completed_ids:
            existing = snapshot.get_task(task_id)
            
            # Resolve platform
            if existing:
                platform = existing.platform
            else:
                platforms = task_platforms.get(task_id)
                if platforms:
                    platform = platforms[0]
                elif "api" in task_id:
                    platform = "api"
                elif "flutter" in task_id:
                    platform = "flutter"
                elif "web" in task_id:
                    platform = "web"
                elif "cms" in task_id:
                    platform = "cms"
                else:
                    platform = ctx.platform

            # Attempt to derive the journal path from the known naming convention.
            journal_rel: str | None = None
            if existing and existing.artifacts.journal:
                journal_rel = existing.artifacts.journal
            else:
                journals_dir = epic_dir / "journals"
                candidate = journals_dir / f"{task_id}.evaluation.md"
                if await AsyncFileSystemService.exists(candidate):
                    try:
                        journal_rel = str(candidate.relative_to(ctx.project_storage_dir().parent))
                    except ValueError:
                        journal_rel = str(candidate)

            # Resolve other artifacts
            art_from_state = None
            if not existing and generated_artifacts and task_id in generated_artifacts:
                art_from_state = generated_artifacts[task_id]

            if existing:
                contract_val = existing.artifacts.contract
                schema_file_val = existing.artifacts.schema_file
                verification_val = existing.artifacts.verification
            elif art_from_state:
                contract_val = art_from_state.get("contract") or None
                schema_file_val = art_from_state.get("schema_file") or None
                verification_val = art_from_state.get("verification") or None
            else:
                contract_val = None
                schema_file_val = None
                verification_val = None

            updated_artifacts = TaskArtifacts(
                contract=contract_val,
                journal=journal_rel,
                schema_file=schema_file_val,
                verification=verification_val,
            )
            task_state = TaskArtifactState(
                task_id=task_id,
                platform=platform,
                status="passed" if passed else "repairing",
                artifacts=updated_artifacts,
                repair_iteration=existing.repair_iteration if existing else 0,
                evaluation_avg=None,
                evaluation_min=None,
            )
            snapshot = snapshot.upsert_task(task_state)

        await svc.save(snapshot)
    except EpicStateError as exc:
        logger.warning("Could not update state.json after validation (non-fatal): %s", exc)


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
    assert ctx is not None, "validate_node requires a JobContext"

    is_spoq = state.is_spoq
    if is_spoq:
        active_tasks = get_state_active_tasks(state.spoq_tasks)
        tasks_to_validate: list[tuple[str, str]] = []
        for t in active_tasks:
            for p in t.platforms:
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

    Path(ctx.repo_path)

    await send_progress(
        state,
        f"Validating: Running stage {state.current_stage + 1} quality validation gates concurrently...",
    )
    start_time = time.time()

    # ------------------------------------------------------------------
    # Async Validation Worker
    # ------------------------------------------------------------------
    async def validate_single_task_platform(task_id: str, platform: str) -> tuple[str, list[str]]:
        task_label = f"[{task_id}:{platform}]" if task_id else f"[{platform}]"
        logger.info("%s Running validator...", task_label)
        plat_path = ctx.platform_path(platform)

        if not is_spoq:
            # Step 1: Run platform-level lint/test checks.
            strategy = get_platform_strategy(platform)
            lint_errors: list[str] = []
            try:
                lint_errors = await strategy.validate(plat_path)
            except (EbDevError, OSError, asyncio.TimeoutError) as e:
                lint_errors = [f"System check exception during validation on platform {platform}: {e!s}"]

            # Step 2: Also run the code_evaluator agent via OpenCode so that
            # structured evaluation journals are always produced, even in
            # non-SPOQ mode.
            eval_ctx = ctx.model_copy(
                update={
                    "repo_path": str(plat_path),
                    "platform": platform,
                    "active_task_id": task_id or ctx.ticket_id,
                    "current_agent": "code_evaluator",
                }
            )

            # Pre-create the journals directory so the evaluator can write its journal.
            journals_dir: Path | None = None
            task_id_str = str(ctx.task_id) if getattr(ctx, "task_id", None) else "default"
            if "-" in task_id_str:
                parts = task_id_str.rsplit("-", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    task_id_dir = parts[1]
                else:
                    task_id_dir = task_id_str
            else:
                task_id_dir = task_id_str
            journals_dir = ctx.project_storage_dir() / task_id_dir / "journals"
            await AsyncFileSystemService.ensure_directory(journals_dir)
            logger.info("%s Ensured journals directory: %s", task_label, journals_dir)

            loop = asyncio.get_running_loop()

            def _on_opencode_progress_non_spoq(msg: str) -> None:
                asyncio.run_coroutine_threadsafe(send_progress(state, f"{task_label} Evaluator: {msg}"), loop)

            session_id_key = f"{ctx.ticket_id}_{platform}"
            session_ids_ns = getattr(state, "opencode_session_ids", {}) or {}
            existing_session_id_ns = session_ids_ns.get(platform) or await db.get_session_id(session_id_key)

            eval_errors: list[str] = []
            try:
                eval_result, _ = await asyncio.to_thread(
                    invoke_opencode,
                    eval_ctx,
                    progress_callback=_on_opencode_progress_non_spoq,
                    session_id=existing_session_id_ns,
                )
                if eval_result.status == "failed" or eval_result.errors:
                    errs = eval_result.errors or []
                    remediation = getattr(eval_result, "remediation", "") or (
                        eval_result.summary if eval_result.status == "failed" else ""
                    )
                    if remediation:
                        errs.append(remediation)
                    eval_errors = errs
                else:
                    logger.info("%s code_evaluator passed: %s", task_label, eval_result.summary)
            except Exception as e:
                logger.warning("%s code_evaluator invocation failed (non-fatal): %s", task_label, e)
                eval_errors = [f"code_evaluator exception: {e}"]

            return platform, lint_errors + eval_errors

        # SPOQ mode: pre-create the journals directory so the evaluator can always write its output.
        if ctx.spoq_epic_dir:
            epic_dir = ctx.project_storage_dir() / ctx.spoq_epic_dir
            journals_dir_spoq = epic_dir / "journals"
            await AsyncFileSystemService.ensure_directory(journals_dir_spoq)
            logger.info("%s Ensured SPOQ journals directory: %s", task_label, journals_dir_spoq)

        eval_ctx = ctx.model_copy(
            update={
                "repo_path": str(plat_path),
                "platform": platform,
                "active_task_id": task_id,
                "current_agent": "code_evaluator",
            }
        )

        loop = asyncio.get_running_loop()

        def _on_opencode_progress(msg: str) -> None:
            asyncio.run_coroutine_threadsafe(send_progress(state, f"{task_label} Evaluator: {msg}"), loop)

        session_id_key = f"{ctx.ticket_id}_{platform}"
        session_ids = getattr(state, "opencode_session_ids", {}) or {}

        existing_session_id = session_ids.get(platform) or await db.get_session_id(session_id_key)

        try:

            result, _ = await asyncio.to_thread(
                invoke_opencode,
                eval_ctx,
                progress_callback=_on_opencode_progress,
                session_id=existing_session_id,
            )

            if result.status == "failed" or result.errors:
                errs = result.errors or []
                remediation = getattr(result, "remediation", "") or (
                    result.summary if result.status == "failed" else ""
                )
                if remediation:
                    errs.append(remediation)
                return platform, errs
            logger.info("%s Code validation passed: %s", task_label, result.summary)
            return platform, []
        except Exception as e:
            return platform, [f"Evaluator execution exception: {e}"]


    try:
        runs = await asyncio.gather(*[validate_single_task_platform(t, p) for t, p in tasks_to_validate])
        platform_errors: dict[str, list[str]] = {}
        for p, errs in runs:
            platform_errors.setdefault(p, []).extend(errs)

        duration = round(time.time() - start_time, 2)
        logger.info("Stage %d checks completed in %ss.", state.current_stage + 1, duration)

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
        updated_artifacts = {**state.generated_artifacts}

        if all_passed:
            if is_spoq:
                # Mark current wave tasks as completed in state
                completed_ids = {t.id for t in active_tasks}
                for i, task in enumerate(updated_spoq_tasks):
                    if task.id in completed_ids:
                        updated_spoq_tasks[i] = task.model_copy(update={"status": "completed"})
                logger.info("Marked %d SPOQ tasks as completed in state.", len(completed_ids))
                # Mirror completion into the .ebpearls artifact registry.
                await _write_validation_state(
                    ctx=ctx,
                    completed_ids=completed_ids,
                    passed=True,
                    platform_errors={},
                    generated_artifacts=state.generated_artifacts,
                    spoq_tasks=state.spoq_tasks,
                )

                # Update GraphState.generated_artifacts
                epic_dir = ctx.project_storage_dir() / ctx.spoq_epic_dir if ctx.spoq_epic_dir else None
                for task_id in completed_ids:
                    existing = updated_artifacts.get(task_id, {})
                    journal_rel = existing.get("journal") or ""
                    if not journal_rel and epic_dir:
                        candidate = epic_dir / "journals" / f"{task_id}.evaluation.md"
                        if await AsyncFileSystemService.exists(candidate):
                            try:
                                journal_rel = str(candidate.relative_to(ctx.project_storage_dir().parent))
                            except ValueError:
                                journal_rel = str(candidate)
                    updated_artifacts[task_id] = {
                        **existing,
                        "status": "passed",
                        "journal": journal_rel,
                    }

                # Check if all tasks in the current epic are done
                remaining = [t for t in updated_spoq_tasks if t.status in ("pending", "blocked")]
                if remaining:
                    stages_finished = False
                    status_msg = "Wave transition complete. Remaining tasks stay queued."
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
                    status_msg = f"Stage {state.current_stage + 1} passed. Advancing to next stage..."
                else:
                    status_msg = "All validations passed."
            else:
                status_msg = "All validations passed."
        else:
            status_msg = f"Validation failed with {len(combined_errors)} check errors. Repair needed."
            # Mirror failure into the .ebpearls artifact registry so agents
            # can identify which tasks need repair on next resume.
            if is_spoq:
                failed_ids = {
                    t.id
                    for t in active_tasks
                    if state.failed_platforms.get(next(iter(t.platforms), ctx.platform), False)
                }
                if failed_ids:
                    await _write_validation_state(
                        ctx=ctx,
                        completed_ids=failed_ids,
                        passed=False,
                        platform_errors=platform_errors,
                        generated_artifacts=state.generated_artifacts,
                        spoq_tasks=state.spoq_tasks,
                    )

                    # Update GraphState.generated_artifacts
                    epic_dir = ctx.project_storage_dir() / ctx.spoq_epic_dir if ctx.spoq_epic_dir else None
                    for task_id in failed_ids:
                        existing = updated_artifacts.get(task_id, {})
                        journal_rel = existing.get("journal") or ""
                        if not journal_rel and epic_dir:
                            candidate = epic_dir / "journals" / f"{task_id}.evaluation.md"
                            if await AsyncFileSystemService.exists(candidate):
                                try:
                                    journal_rel = str(candidate.relative_to(ctx.project_storage_dir().parent))
                                except ValueError:
                                    journal_rel = str(candidate)
                        updated_artifacts[task_id] = {
                            **existing,
                            "status": "evaluate_failed",
                            "journal": journal_rel,
                        }

        await send_progress(state, status_msg)

        updated_ctx = ctx.model_copy(
            update={
                "validation_errors": combined_errors,
            }
        )

        return state.model_copy(
            update={
                "last_node": "evaluator_agent",
                "context": updated_ctx,
                "done": all_passed and stages_finished,
                "current_stage": next_stage,
                "done_platforms": done_platforms,
                "failed_platforms": failed_platforms,
                "spoq_tasks": updated_spoq_tasks,
                "generated_artifacts": updated_artifacts,
            }
        )

    except (EbDevError, ValueError, KeyError, RuntimeError) as e:
        err_msg = f"System failure during validation phase: {e!s}"
        logger.error(err_msg)
        await send_progress(state, "Validation failed due to validation system error.")

        updated_ctx = ctx.model_copy(update={"validation_errors": [err_msg]})
        return state.model_copy(
            update={
                "last_node": "evaluator_agent",
                "context": updated_ctx,
                "done": False,
                "failed": True,
            }
        )
