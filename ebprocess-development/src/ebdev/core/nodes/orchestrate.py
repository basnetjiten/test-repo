# -*- coding: utf-8 -*-
"""
orchestrate.py
==============
Orchestrate node - runs LLM architect evaluation to decide execution topology.

Responsibilities
----------------
* Analyze the ticket and active platforms to dynamically compile the OrchestrationStrategy.
* Fall back to a rule-based classifier if LLM Architect fails or is unconfigured.
* Generate a wave-based DAG (SPOQ task queue) for multi-platform tasks.
"""

from __future__ import annotations

import json
import logging
import re

from typing import TYPE_CHECKING
from pathlib import Path

import httpx
from pydantic import ValidationError

from ebdev.config import config
from ebdev.core.constants import Agents
from ebdev.core.nodes.common import send_progress
from ebdev.core.spoq_map import build_epic_tasks, compute_epic_waves
from ebdev.models.schemas import OrchestrationStrategy, SPOQMapEpic
from ebdev.services.opencode import OpenCodeAPIClient
from ebdev.services.prompts import build_orchestrator_prompt

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fallback OpenCode server URL if not configured in environmental settings.
DEFAULT_OPENCODE_SERVER_URL: str = "http://opencode:4096"


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
    platforms = ctx.platforms
    ticket = ctx.ticket
    
    ticket_id = ticket.id if ticket else ctx.ticket_id
    ticket_title = ticket.title if ticket else ""
    ticket_desc = ticket.description if ticket else ""
    ticket_ac = "\n".join(ticket.acceptance_criteria) if ticket else ""
    
    # 1. Fallback Rule-Based Classifier (highly robust)
    offline_first_detected = any(
        kw in ticket_desc.lower() or kw in ticket_title.lower() or kw in ticket_ac.lower()
        for kw in ["offline", "local storage", "sync", "sqlite", "hive", "drift", "isar", "cache"]
    )
    
    ui_ux_only_detected = any(
        kw in ticket_desc.lower() or kw in ticket_title.lower() or kw in ticket_ac.lower()
        for kw in ["style", "ui", "ux", "color", "theme", "screen", "widget", "font", "padding", "margin"]
    ) and not any(
        kw in ticket_desc.lower() or kw in ticket_title.lower() or kw in ticket_ac.lower()
        for kw in ["api", "endpoint", "backend", "db", "database", "postgres", "sql", "migration"]
    )
    
    # Simple default stages based on platform dependencies
    # If "api" exists, it runs first in sequential/mock_first modes
    has_api = "api" in platforms
    other_platforms = [p for p in platforms if p != "api"]
    
    if has_api and len(platforms) > 1 and not ui_ux_only_detected:
        default_mode = "spoq"
        # Future: integrate with Jira/API to pull the exact task list and hours
        default_mocking = "mock_repositories" if not offline_first_detected else "live"
    else:
        default_mode = "parallel"
        default_mocking = "live"
        
    fallback_strategy = OrchestrationStrategy(
        complexity="high" if (offline_first_detected or (len(platforms) > 1 and not ui_ux_only_detected)) else "low",
        offline_first=offline_first_detected,
        ui_ux_only=ui_ux_only_detected,
        execution_mode=default_mode,
        mocking_level=default_mocking,
        max_repair_iterations=3,
        reasoning="Rule-based fallback evaluation based on ticket requirements and platform scope."
    )

    # 2. Try LLM Architect (OpenCode)
    # 1. Determine Strategy (Bypass LLM if tasks are explicitly provided)
    if ctx.ticket and ctx.ticket.tasks:
        logger.info("Explicit tasks provided in payload. Bypassing LLM orchestration.")
        strategy = OrchestrationStrategy(
            complexity="high",
            execution_mode="spoq",
            offline_first=False,
            ui_ux_only=False,
            mocking_level="mock_repositories",
            reasoning="Explicit task array provided by JIRA/estimation payload."
        )
    else:
        strategy = OrchestrationStrategy(
            complexity="low",
            execution_mode="direct",
            offline_first=False,
            ui_ux_only=False,
            mocking_level="mock_repositories",
            reasoning="Fallback strategy."
        )
        if config.OPENCODE_API_KEY or config.OPENCODE_SERVER_URL:
            try:
                prompt = build_orchestrator_prompt(
                    platforms=platforms,
                    ticket_id=ticket_id,
                    ticket_title=ticket_title,
                    ticket_desc=ticket_desc,
                    ticket_ac=ticket_ac
                )
                server_url = config.OPENCODE_SERVER_URL or DEFAULT_OPENCODE_SERVER_URL
                directory = ctx.repo_path or str(Path(config.WORKSPACE_DIR) / ctx.space_name)
                client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY, directory=directory)
                
                session_id = await client.create_session(title=f"Orchestrator-{ticket_id}")
                res = await client.send_prompt_message(
                    session_id=session_id,
                    agent=Agents.ORCHESTRATOR,
                    prompt=prompt,
                    model=config.OPENCODE_MODEL
                )
                
                # Parse response JSON
                parts = res.get("parts", [])
                reply_text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")

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
                    logger.info("LLM architect strategy selected: %s (%s complexity)", strategy.execution_mode, strategy.complexity)
            except (httpx.HTTPError, json.JSONDecodeError, ValidationError, AttributeError, KeyError) as e:
                logger.warning("LLM architect failed, using rule-based fallback: %s", e)

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
    active_epic_tasks: list = []
    if strategy.execution_mode == "spoq":
        derived_epic_id = f"Epic-{ticket_id}" if not str(ticket_id).startswith("Epic-") else str(ticket_id)
        epic_specs = list(ctx.map_epics) if ctx.map_epics else [
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

    shared_ctx_data = {
        "ticket_id": ticket_id,
        "mocking_level": strategy.mocking_level,
        "offline_first": strategy.offline_first,
        "complexity": strategy.complexity,
        "api_schema": None,
        "graphql_schema": None,
        "endpoints": [],
        "active_epic_id": active_epic_id,
    }

    updated_ctx = ctx.model_copy(update={
        "mocking_level": strategy.mocking_level,
        "offline_first": strategy.offline_first,
        "spoq_epic_dir": active_epic_id,
        "map_id": map_id,
        "shared_context": shared_ctx_data
    })

    return state.model_copy(update={
        "last_node": "orchestrate_agent",
        "strategy": strategy,
        "context": updated_ctx,
        "current_stage": 0,
        "spoq_tasks": active_epic_tasks,
        "shared_context": shared_ctx_data
    })
