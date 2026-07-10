# -*- coding: utf-8 -*-
"""
generate.py
===========
Generate node - triggers builders concurrently using OpenCode.

Responsibilities
----------------
* Identify active platforms for the current execution wave from LangGraph state.
* Verify planning stage outputs exist and are valid.
* Concurrently invoke OpenCode builder agents for each platform.
* Store and resume OpenCode conversation session IDs for task continuity.
* Capture API schemas into shared context for cross-platform awareness.
* Consolidate build results to update the graph state.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.logger import get_logger
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_utils import get_state_active_tasks
from ebdev.models.graph_state import JobResult
from ebdev.services import db
from ebdev.services.fs import AsyncFileSystemService
from ebdev.services.opencode import invoke_opencode

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


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
    """
    state.last_node = "builder_agent"
    await send_progress(
        state,
        f"Coding: Generating stage {state.current_stage + 1} code implementations concurrently...",
    )
    start_time = time.time()

    ctx = state.context
    assert ctx is not None, "generate_node requires a JobContext"
    is_spoq = state.is_spoq
    if is_spoq:
        tasks = get_state_active_tasks(state.spoq_tasks)
        tasks_to_generate: list[tuple[str, str]] = []
        for t in tasks:
            for p in t.skills_required:
                tasks_to_generate.append((t.id, p))
        # Deduplicate
        seen: set[tuple[str, str]] = set()
        unique_tasks = []
        for tp in tasks_to_generate:
            if tp not in seen:
                seen.add(tp)
                unique_tasks.append(tp)
        tasks_to_generate = unique_tasks
    else:
        tasks_to_generate = [("", p) for p in ctx.platforms]

    list({p for _, p in tasks_to_generate})
    Path(ctx.repo_path)

    if state.result and state.result.status == "failed":
        logger.info("Skipping due to plan failure.")
        return state.model_copy(update={"last_node": "builder_agent"})

    results: dict[str, JobResult] = {}
    session_ids: dict[str, str] = {**state.opencode_session_ids}

    # ------------------------------------------------------------------
    # Async Generation Worker
    # ------------------------------------------------------------------
    async def generate_single_task_platform(task_id: str, platform: str) -> tuple[str, str, JobResult, str | None]:
        task_label = f"[{task_id}:{platform}]" if task_id else f"[{platform}]"
        logger.info("%s Running builder...", task_label)

        if state.done_platforms.get(platform) is True:
            logger.info(
                "[%s] Skipping because platform already successfully validated.",
                platform,
            )
            existing_result = state.platform_results.get(platform) or JobResult(
                task_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary="Builder skipped. Previously validated successfully.",
            )
            return task_id, platform, existing_result, session_ids.get(platform)

        plat_path = ctx.platform_path(platform)
        active_task_id = task_id or "default"

        # Non-SPOQ plan file verification
        if not is_spoq:
            task_id_str = str(ctx.task_id) if getattr(ctx, "task_id", None) else "default"
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

            plan_file = (
                ctx.project_storage_dir(config.OPENCODE_PROJECT_DIR) / task_id_dir / f"{file_prefix}plan_{platform}.md"
            )

            if not plan_file.exists():
                err_result = JobResult(
                    task_id=ctx.ticket_id,
                    space_name=ctx.space_name,
                    ticket_id=ctx.ticket_id,
                    status="failed",
                    errors=[f"Plan source {plan_file.name} missing. Planner must succeed first."],
                )
                return task_id, platform, err_result, None

        label = getattr(ctx, "ticket_label", "feature")
        target_agent = "build"
        if label == "bug":
            target_agent = "bug_fixer"
        elif label == "ui_refine":
            target_agent = "ui_refiner"

        # Check if we have an evaluation journal in state.generated_artifacts for this task
        remediation_content = None
        if is_spoq and task_id in state.generated_artifacts:
            journal_path_str = state.generated_artifacts[task_id].get("journal")
            if journal_path_str:
                # Resolve absolute path using project storage dir parent as base
                journal_path = Path(ctx.project_storage_dir().parent) / journal_path_str
                if await AsyncFileSystemService.exists(journal_path):
                    try:
                        content = await AsyncFileSystemService.read_text(journal_path)
                        if "## Remediation" in content:
                            remediation_content = content.split("## Remediation")[1].strip()
                            logger.info("%s Injected repair_journal from evaluation journal file", task_label)
                    except Exception as e:
                        logger.warning("%s Could not read journal file: %s", task_label, e)

        updated_shared = {**state.shared_context}
        if remediation_content:
            updated_shared["repair_journal"] = remediation_content

        plat_ctx = ctx.model_copy(
            update={
                "repo_path": str(plat_path),
                "platform": platform,
                "active_task_id": active_task_id if is_spoq else None,
                "current_agent": target_agent,
                "shared_context": updated_shared,
            }
        )

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
                    logger.info(
                        "[%s] Resuming session %r from linked ticket %s",
                        platform,
                        existing_session_id,
                        linked_id,
                    )
                    break

        if not existing_session_id:
            existing_session_id = await db.get_session_id(session_id_key)
            if not existing_session_id and ticket_id:
                existing_session_id = await db.get_session_id_by_jira_id(f"{ticket_id}_{platform}")

        # Guard: reject stale or mock session IDs that do not conform to the
        # OpenCode session ID format (must start with "ses"). Such values may
        # have been written to the DB by a previous test run and would cause
        # a 500 error when sent to the real OpenCode server.
        if existing_session_id and not existing_session_id.startswith("ses"):
            logger.warning(
                "[%s] Discarding invalid/stale session ID %r (does not start with 'ses'). "
                "A fresh session will be created.",
                platform,
                existing_session_id,
            )
            existing_session_id = None

        if existing_session_id:
            logger.info(
                "[%s] Resuming OpenCode session: %s",
                platform,
                existing_session_id,
            )

        loop = asyncio.get_running_loop()

        def _on_opencode_progress(msg: str) -> None:
            asyncio.run_coroutine_threadsafe(send_progress(state, f"[{platform}] Builder: {msg}"), loop)

        result, captured_session_id = await asyncio.to_thread(
            invoke_opencode,
            plat_ctx,
            progress_callback=_on_opencode_progress,
            session_id=existing_session_id,
        )

        session_id_to_store = captured_session_id or existing_session_id
        if session_id_to_store:
            await db.save_session_id(
                session_id_key,
                session_id_to_store,
                jira_id=f"{ticket_id}_{platform}",
            )

        return task_id, platform, result, session_id_to_store

    try:
        runs = await asyncio.gather(*[generate_single_task_platform(t, p) for t, p in tasks_to_generate])

        for task_id, p, res, sid in runs:
            key = f"{task_id}_{p}" if task_id else p
            results[key] = res
            if sid:
                session_ids[p] = sid

        duration = round(time.time() - start_time, 2)
        logger.info("Concurrent builders finished in %ss.", duration)

        overall_status = "success"
        combined_errors: list[str] = []
        combined_warnings: list[str] = []

        failed_platforms = {**state.failed_platforms}
        for task_plat, res in results.items():
            platform = task_plat.split("_")[-1] if "_" in task_plat else task_plat
            if res.status == "failed":
                overall_status = "failed"
                failed_platforms[platform] = True
                combined_errors.extend(res.errors or [])
            combined_warnings.extend(res.warnings or [])

        # Capture generated schemas into shared_context for cross-platform awareness
        updated_shared_context = {**state.shared_context}
        try:
            flutter_path = ctx.platform_path("flutter")
            graphql_file = flutter_path / "lib" / "graphql" / "schema.graphql"
            if await AsyncFileSystemService.exists(graphql_file):
                updated_shared_context["graphql_schema"] = await AsyncFileSystemService.read_text(graphql_file)
                logger.info("Captured GraphQL schema from %s", graphql_file.name)
        except Exception as e:
            logger.warning("Failed to check/read GraphQL schema: %s", e)

        try:
            api_path = ctx.platform_path("api")
            openapi_file = api_path / "swagger.json"
            if await AsyncFileSystemService.exists(openapi_file):
                updated_shared_context["api_schema"] = await AsyncFileSystemService.read_text(openapi_file)
                logger.info("Captured OpenAPI JSON spec from %s", openapi_file.name)
        except Exception as e:
            logger.warning("Failed to check/read OpenAPI spec: %s", e)

        consolidated_result = JobResult(
            task_id=ctx.ticket_id,
            space_name=ctx.space_name,
            ticket_id=ctx.ticket_id,
            status=overall_status,
            summary=f"Builders finished. Consolidated status: {overall_status}",
            errors=combined_errors,
            warnings=combined_warnings,
            pr_url=next(iter(results.values())).pr_url if results else None,
        )

        return state.model_copy(
            update={
                "last_node": "builder_agent",
                "result": consolidated_result,
                "platform_results": {**state.platform_results, **results},
                "opencode_session_ids": session_ids,
                "failed_platforms": failed_platforms,
                "shared_context": updated_shared_context,
            }
        )

    except Exception as e:
        logger.error("CRITICAL ERROR in generation phase: %s", e)
        raise
