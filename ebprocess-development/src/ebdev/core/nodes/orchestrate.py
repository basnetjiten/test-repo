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

import httpx
import yaml
from pydantic import ValidationError

from ebdev.config import config
from ebdev.core.constants import Agents
from ebdev.core.nodes.common import send_progress
from ebdev.models.schemas import OrchestrationStrategy, SPOQTask
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
        # TODO @Jiten Basnet: In the future, integrate with Jira/API to pull the exact task list and hours
        # for the sprint instead of using the LLM orchestrator to dynamically break down the Epic.
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
    strategy = fallback_strategy
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
            client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY)
            
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
            
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", reply_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
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

    # Generate SPOQ Task Queue
    spoq_epic_dir = None
    if strategy.execution_mode == "spoq":
        # Project-scoped SPOQ directory: .ebpearls/epics/<ticket_id>
        project_storage = ctx.project_storage_dir()
        epic_dir = project_storage / "epics" / ticket_id
        tasks_dir = epic_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        
        # Write EPIC.md
        epic_md = f"# Epic: {ticket_title}\n\n{ticket_desc}\n\n## Acceptance Criteria\n{ticket_ac}\n"
        (epic_dir / "EPIC.md").write_text(epic_md, encoding="utf-8")
        
        # Build DAG
        tasks_to_write = []
        
        # Phase 0: Contract
        contract_task = SPOQTask(
            id="00-contract",
            title="Define OpenAPI and Data Models",
            epic=ticket_id,
            status="pending",
            phase=0,
            dependencies=[],
            skills_required=["api"] if "api" in platforms else platforms[:1],
            outputs=["OpenAPI YAML", "Database schema models"]
        )
        tasks_to_write.append(contract_task)
        
        # Phase 1: Parallel Implementation
        impl_tasks = []
        for plat in platforms:
            task_id = f"01-{plat}-impl"
            impl_task = SPOQTask(
                id=task_id,
                title=f"Implement {plat.capitalize()} features",
                epic=ticket_id,
                status="blocked",
                phase=1,
                dependencies=["00-contract"],
                skills_required=[plat],
                outputs=[f"{plat.capitalize()} specific implementation"]
            )
            tasks_to_write.append(impl_task)
            impl_tasks.append(task_id)
            
        # Phase 2: Integration
        integration_task = SPOQTask(
            id="02-integration",
            title="Integrate and verify all platforms",
            epic=ticket_id,
            status="blocked",
            phase=2,
            dependencies=impl_tasks,
            skills_required=platforms,
            outputs=["Verified integration tests passing"]
        )
        tasks_to_write.append(integration_task)
        
        # Write YAMLs
        for t in tasks_to_write:
            yml_path = tasks_dir / f"{t.id}.yml"
            with open(yml_path, 'w', encoding="utf-8") as f:
                yaml.dump(t.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False)
                
        spoq_epic_dir = str(epic_dir)
        logger.info("Generated SPOQ execution queue in %s", spoq_epic_dir)

    updated_ctx = ctx.model_copy(update={
        "mocking_level": strategy.mocking_level,
        "offline_first": strategy.offline_first,
        "spoq_epic_dir": spoq_epic_dir
    })

    return state.model_copy(update={
        "last_node": "orchestrate",
        "strategy": strategy,
        "context": updated_ctx,
        "current_stage": 0
    })
