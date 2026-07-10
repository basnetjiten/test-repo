# -*- coding: utf-8 -*-
"""
strategize.py
=============
Strategize node — LLM architect evaluation that determines execution topology.

Responsibilities
----------------
* Call the LLM architect with the ticket + platform context to derive an
  ``OrchestrationStrategy`` (complexity, execution_mode, mocking_level,
  endpoints, task_contexts).
* Fall back to a rule-based classifier if the LLM architect fails or is
  unconfigured.
* Build the wave-based SPOQ task DAG from the derived strategy and write it
  into ``GraphState`` for downstream nodes.

Note: The node function is still named ``orchestrate_node`` to preserve the
LangGraph graph topology registration key (``"orchestrate_agent"``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import ValidationError

from ebdev.config import config
from ebdev.core.constants import Agents
from ebdev.core.logger import get_logger
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_map import build_epic_tasks, compute_epic_waves
from ebdev.core.throttle import CircuitBreakerOpenError
from ebdev.core.exceptions import OrchestrationError
from ebdev.models.graph_state import OrchestrationStrategy
from ebdev.models.spoq import SPOQMapEpic, SPOQTask
from ebdev.models.task import TaskArtifactState
from ebdev.services.epic_state import get_epic_state_service
from ebdev.services.opencode import OpenCodeAPIClient
from ebdev.services.prompts import build_orchestrator_prompt, to_container_path

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fallback OpenCode server URL if not configured in environmental settings.
DEFAULT_OPENCODE_SERVER_URL: str = config.OPENCODE_SERVER_DEFAULT_URL


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def orchestrate_node(state: GraphState) -> GraphState:
    """
    Analyze the ticket and active platforms to dynamically compile the OrchestrationStrategy.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with the orchestration strategy and, if applicable, the SPOQ epic task directory.
    """
    state.last_node = "orchestrate_agent"
    await send_progress(state, "Orchestrating: Analyzing ticket scope, dependencies, and complexity...")

    ctx = state.context
    assert ctx is not None, "orchestrate_node requires a JobContext"
    platforms = ctx.platforms
    ticket = ctx.ticket

    ticket_id: str = ticket.id if ticket else ""
    ticket_title: str = ticket.title if ticket else ""
    ticket_desc: str = ticket.description if ticket else ""
    ticket_ac: str = "\n".join(ticket.acceptance_criteria) if ticket else ""

    # 1. Determine Strategy — LLM first, raise error if fails
    strategy: OrchestrationStrategy | None = None
    tasks_exist = bool(ctx.ticket and ctx.ticket.tasks)

    if not (config.OPENCODE_API_KEY or config.OPENCODE_SERVER_URL):
        raise OrchestrationError("OpenCode credentials are not configured. Cannot perform dynamic orchestration.")

    try:
        # Build enriched prompt that includes task-level details so the LLM
        # can derive accurate mocking levels, feature names, and endpoints.
        task_details: list[str] = []
        if tasks_exist:
            for t in ctx.ticket.tasks:
                task_details.append(
                    f'  - Task {t.id}: "{t.name}" '
                    f"(platforms: {', '.join(t.active_platforms)})"
                )

        prompt = build_orchestrator_prompt(
            platforms=platforms,
            ticket_id=ticket_id,
            ticket_title=ticket_title,
            ticket_desc=ticket_desc,
            ticket_ac=ticket_ac,
            task_details=task_details if task_details else None,
        )
        server_url = config.OPENCODE_SERVER_URL or DEFAULT_OPENCODE_SERVER_URL
        client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY)

        session_id = await client.create_session(title=f"Orchestrator-{ticket_id}")
        res = await client.send_prompt_message(
            session_id=session_id,
            agent=Agents.ORCHESTRATOR,
            prompt=prompt,
            model=config.OPENCODE_MODEL,
        )
        logger.info("OpenCode raw response: %s", json.dumps(res, indent=2))

        parts = res.get("parts", [])
        reply_text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        logger.info("Extracted reply text: %s", reply_text)

        data = None
        try:
            parsed = json.loads(reply_text.strip())
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", reply_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))

        if data:
            strategy = OrchestrationStrategy(**data)
            logger.info(
                "LLM architect strategy: %s (%s complexity, mocking=%s)",
                strategy.execution_mode,
                strategy.complexity,
                strategy.mocking_level,
            )
    except (
        httpx.HTTPError,
        CircuitBreakerOpenError,
        json.JSONDecodeError,
        ValidationError,
        AttributeError,
        KeyError,
    ) as e:
        raise OrchestrationError(f"LLM architect failed to determine strategy: {e}") from e

    if strategy is None:
        raise OrchestrationError("Failed to generate dynamic orchestration strategy from LLM architect.")

    assert isinstance(strategy, OrchestrationStrategy)  # narrow type for static analysis

    # Log the strategy decision
    logger.info("=== ORCHESTRATION STRATEGY SELECTED ===")
    logger.info("Complexity:    %s", strategy.complexity)
    logger.info("Offline First: %s", strategy.offline_first)
    logger.info("UI/UX Only:    %s", strategy.ui_ux_only)
    logger.info("Execution:     %s", strategy.execution_mode)
    logger.info("Mock Level:    %s", strategy.mocking_level)
    logger.info("Reasoning:     %s", strategy.reasoning)

    # Map-level tracking stored in GraphState, persisted via LangGraph checkpointing
    derived_map_id = ticket_id
    if str(ticket_id).startswith("Epic-"):
        derived_map_id = ticket_id.replace("Epic-", "")
    map_id = ctx.map_id or (ticket_id if str(ticket_id).startswith("Map-") else f"Map-{derived_map_id}")

    # Build SPOQ task DAG in LangGraph state — no disk I/O
    active_epic_id: str | None = None

    active_epic_tasks: list[SPOQTask] = []
    if strategy.execution_mode == "spoq":
        derived_epic_id = f"Epic-{ticket_id}" if not str(ticket_id).startswith("Epic-") else str(ticket_id)
        epic_specs = (
            list(ctx.map_epics)
            if ctx.map_epics
            else [
                SPOQMapEpic(
                    id=derived_epic_id,
                    title=ticket_title or ctx.feature_name or "New Epic",
                    description=ticket_desc or ticket_title or "Program delivery epic.",
                    status="planned",
                    sprint="sprint-1",
                    depends_on=[],
                    platforms=platforms,
                    tasks=list(ctx.ticket.tasks) if ctx.ticket and ctx.ticket.tasks else [],
                    acceptance_criteria=list(ticket.acceptance_criteria) if ticket else [],
                )
            ]
        )

        # Compute ready waves and select the first epic
        waves = compute_epic_waves(epic_specs)
        ready_wave = waves[0] if waves else []
        active_epic_id = ready_wave[0] if ready_wave else epic_specs[0].id

        active_epic_spec = next((e for e in epic_specs if e.id == active_epic_id), epic_specs[0])

        if active_epic_spec is None:
            raise ValueError("SPOQ execution requires at least one epic.")

        active_epic_spec.status = "in-progress"
        active_epic_tasks = build_epic_tasks(active_epic_spec, mocking_level=strategy.mocking_level)

        logger.info("SPOQ dispatched epic %s with %d tasks (map %s)", active_epic_id, len(active_epic_tasks), map_id)

    # 2. Extract values directly from the LLM-derived strategy
    primary_feature = strategy.primary_feature_name
    derived_endpoints = strategy.endpoints
    task_contexts = {str(k): v for k, v in strategy.task_contexts.items()}  # pylint: disable=no-member

    shared_ctx_data = {
        "ticket_id": ticket_id,
        "mocking_level": strategy.mocking_level,
        "offline_first": strategy.offline_first,
        "complexity": strategy.complexity,
        "api_schema": None,
        "graphql_schema": None,
        "endpoints": derived_endpoints,
        "active_epic_id": active_epic_id,
        "task_contexts": task_contexts,
        "primary_feature_name": primary_feature,
    }
    if not primary_feature:
        raise OrchestrationError("LLM strategy is missing 'primary_feature_name'.")

    updated_ctx = ctx.model_copy(
        update={
            "mocking_level": strategy.mocking_level,
            "offline_first": strategy.offline_first,
            "spoq_epic_dir": active_epic_id,
            "map_id": map_id,
            "shared_context": shared_ctx_data,
            "feature_name": primary_feature,
        }
    )

    if strategy.execution_mode == "spoq" and active_epic_id:
        epic_dir = updated_ctx.project_storage_dir() / active_epic_id
        svc = get_epic_state_service(epic_dir)
        try:
            snapshot = await svc.load_or_init(epic_id=active_epic_id, space_name=updated_ctx.space_name)
            for task in active_epic_tasks:
                if not snapshot.get_task(task.id):
                    if task.platforms:
                        platform = task.platforms[0]
                    else:
                        raise OrchestrationError(f"Task {task.id} has no platforms associated with it.")

                    snapshot = snapshot.upsert_task(
                        TaskArtifactState(
                            task_id=task.id,
                            platform=platform,
                            status="pending_plan",
                        )
                    )
            await svc.save(snapshot)
        except Exception as e:
            if isinstance(e, OrchestrationError):
                raise
            logger.warning("Failed to initialize state.json (non-fatal): %s", e)

    return state.model_copy(
        update={
            "last_node": "orchestrate_agent",
            "strategy": strategy,
            "context": updated_ctx,
            "current_stage": 0,
            "spoq_tasks": active_epic_tasks,
            "shared_context": shared_ctx_data,
        }
    )
