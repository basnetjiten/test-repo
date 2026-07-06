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
from ebdev.core.spoq_map import (
    load_map_manifest,
    mark_epic_status,
    materialize_spoq_map,
    next_ready_epic,
    save_map_manifest,
)
from ebdev.models.schemas import OrchestrationStrategy, SPOQMap, SPOQMapEpic
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
    state.last_node = "orchestrate"
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

    # Generate SPOQ Map + Epic artifacts
    spoq_epic_dir = None
    spoq_map_dir = None
    map_id = ctx.map_id
    if strategy.execution_mode == "spoq":
        repo_root = Path(ctx.repo_path).absolute()
        if repo_root.name.endswith("-services") or repo_root.name.endswith("_flutter"):
            repo_root = repo_root.parent

        project_spoq = repo_root / "spoq"
        project_spoq.mkdir(parents=True, exist_ok=True)

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

        derived_map_id = ticket_id
        if str(ticket_id).startswith("Epic-"):
            derived_map_id = ticket_id.replace("Epic-", "")
        map_id = ctx.map_id or (ticket_id if str(ticket_id).startswith("Map-") else f"Map-{derived_map_id}")
        program_map = SPOQMap(
            id=map_id,
            title=ticket_title or ctx.feature_name or map_id,
            vision=ticket_desc or ticket_title or "Coordinate multiple epics through wave-based dispatch.",
            status="planned",
            epics=epic_specs,
            success_criteria=(
                list(ticket.acceptance_criteria)
                if ticket and ticket.acceptance_criteria
                else [criterion for epic in epic_specs for criterion in epic.acceptance_criteria]
            ),
            risk_assessment=[
                "Cross-epic dependencies can block downstream waves if upstream validation slips.",
                "Contracts shared across epics must stay aligned to avoid rework.",
            ],
        )

        map_dir, _epic_dirs = materialize_spoq_map(project_spoq, program_map)
        program_map = load_map_manifest(map_dir)

        active_epic = next_ready_epic(program_map) or program_map.epics[0]
        if active_epic is None:
            raise ValueError(f"SPOQ map {program_map.id} does not contain any epics.")

        mark_epic_status(program_map, active_epic.id, "in-progress")
        save_map_manifest(map_dir, program_map)

        roadmap_path = project_spoq / "ROADMAP.md"
        if not roadmap_path.exists():
            roadmap_path.write_text(
                "# SPOQ Epic Roadmap\n\n| Epic ID | Sprint | Title | Status | Depends On | Platforms |\n|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )

        def _upsert_roadmap_entry(epic: SPOQMapEpic) -> None:
            depends_on = ", ".join(epic.depends_on) if epic.depends_on else "—"
            platforms_str = ", ".join(epic.platforms) if epic.platforms else "—"
            row = f"| {epic.id} | {epic.sprint} | {epic.title} | {epic.status} | {depends_on} | {platforms_str} |"
            content = roadmap_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            for index, line in enumerate(lines):
                if line.strip().startswith(f"| {epic.id} |") or line.strip().startswith(f"|{epic.id}|"):
                    lines[index] = row
                    roadmap_path.write_text("\n".join(lines), encoding="utf-8")
                    return

            insert_idx = len(lines)
            for index, line in enumerate(lines):
                if line.strip().startswith("## Status Meanings"):
                    insert_idx = index
                    break
            lines.insert(insert_idx, row)
            roadmap_path.write_text("\n".join(lines), encoding="utf-8")

        for epic in program_map.epics:
            epic.status = "planned"
            _upsert_roadmap_entry(epic)

        active_epic = next_ready_epic(program_map) or program_map.epics[0]
        active_epic.status = "in-progress"
        _upsert_roadmap_entry(active_epic)
        from ebdev.core.spoq_utils import update_roadmap_status

        update_roadmap_status(str(roadmap_path), active_epic.id, "in-progress")
        save_map_manifest(map_dir, program_map)

        from ebdev.core.spoq_map import EPICS_DIRNAME
        spoq_map_dir = str(map_dir)
        if EPICS_DIRNAME:
            spoq_epic_dir = str(map_dir / EPICS_DIRNAME / active_epic.id)
        else:
            spoq_epic_dir = str(map_dir / active_epic.id)
        logger.info("Generated SPOQ map in %s with active epic %s", spoq_map_dir, spoq_epic_dir)

    updated_ctx = ctx.model_copy(update={
        "mocking_level": strategy.mocking_level,
        "offline_first": strategy.offline_first,
        "spoq_epic_dir": spoq_epic_dir,
        "spoq_map_dir": spoq_map_dir,
        "map_id": map_id
    })

    return state.model_copy(update={
        "last_node": "orchestrate",
        "strategy": strategy,
        "context": updated_ctx,
        "current_stage": 0
    })
