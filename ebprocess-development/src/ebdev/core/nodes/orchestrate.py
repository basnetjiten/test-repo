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
from ebdev.core.name_utils import extract_feature_name
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
    
    # Simple default execution mode based on platform composition
    has_api = "api" in platforms
    default_mode = "spoq" if (has_api and len(platforms) > 1 and not ui_ux_only_detected) else "parallel"

    # 2. Rule-Based Fallback (used only when LLM is unavailable or fails)
    def _derive_mocking_level(tasks, platforms, offline_first: bool) -> str:
        """Derive mocking level from task composition when LLM is unavailable."""
        has_api_tasks = any("api" in t.active_platforms for t in tasks)
        has_backend_keywords = any(
            kw in (t.name or "").lower()
            for t in tasks
            for kw in ["api", "backend", "endpoint", "schema", "database",
                       "migration", "resolver", "controller"]
        )
        has_ui_only = all(
            "api" not in t.active_platforms and "web" not in t.active_platforms
            for t in tasks
        ) and any("flutter" in t.active_platforms for t in tasks)

        if offline_first:
            return "live"
        if has_api_tasks or has_backend_keywords:
            return "live"
        if has_ui_only:
            return "ui_stubs"
        return "mock_repositories"

    fallback_mocking = _derive_mocking_level(
        ctx.ticket.tasks if ctx.ticket and ctx.ticket.tasks else [],
        platforms,
        offline_first_detected,
    )

    fallback_strategy = OrchestrationStrategy(
        complexity="high" if (offline_first_detected or (len(platforms) > 1 and not ui_ux_only_detected)) else "low",
        offline_first=offline_first_detected,
        ui_ux_only=ui_ux_only_detected,
        execution_mode=default_mode,
        mocking_level=fallback_mocking,
        max_repair_iterations=3,
        reasoning="Rule-based fallback evaluation based on ticket requirements and platform scope."
    )

    # 3. Determine Strategy — LLM first, fall back to rule-based
    strategy: OrchestrationStrategy | None = None
    tasks_exist = bool(ctx.ticket and ctx.ticket.tasks)

    if config.OPENCODE_API_KEY or config.OPENCODE_SERVER_URL:
        try:
            # Build enriched prompt that includes task-level details so the LLM
            # can derive accurate mocking levels, feature names, and endpoints.
            task_details: list[str] = []
            if tasks_exist:
                for t in ctx.ticket.tasks:
                    task_details.append(
                        f"  - Task {t.id}: \"{t.name}\" "
                        f"(platforms: {', '.join(t.active_platforms)}, "
                        f"estimated: {sum(h.estimatedHour for h in t.hours):.1f}h)"
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
            directory = ctx.repo_path or str(Path(config.WORKSPACE_DIR) / ctx.space_name)
            client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY, directory=directory)

            session_id = await client.create_session(title=f"Orchestrator-{ticket_id}")
            res = await client.send_prompt_message(
                session_id=session_id,
                agent=Agents.ORCHESTRATOR,
                prompt=prompt,
                model=config.OPENCODE_MODEL,
            )

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
                logger.info("LLM architect strategy: %s (%s complexity, mocking=%s)",
                             strategy.execution_mode, strategy.complexity, strategy.mocking_level)
        except (httpx.HTTPError, json.JSONDecodeError, ValidationError, AttributeError, KeyError) as e:
            logger.warning("LLM architect failed, using rule-based strategy: %s", e)

    if strategy is None:
        strategy = fallback_strategy
        logger.info("Using rule-based strategy (mocking=%s, mode=%s)",
                     strategy.mocking_level, strategy.execution_mode)

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

    def capitalize_first(s: str) -> str:
        """Capitalise each dash/underscore/space-separated segment (e.g. 'enquiry-form' → 'EnquiryForm')."""
        return "".join(p.capitalize() for p in re.split(r"[-_\s]", s) if p)

    # Derive expected endpoints and schemas from task descriptions
    def _derive_expected_endpoints(tasks) -> list[dict]:
        """Extract expected API endpoints from task names."""
        endpoints = []
        entity_patterns: dict[str, list[str]] = {
            "enquiry": ["POST /enquiry", "GET /enquiries"],
            "user": ["POST /users", "GET /users", "PUT /users/{id}"],
            "auth": ["POST /auth/login", "POST /auth/register", "POST /auth/refresh"],
            "product": ["POST /products", "GET /products", "GET /products/{id}"],
            "order": ["POST /orders", "GET /orders", "GET /orders/{id}"],
            "profile": ["GET /profile", "PUT /profile"],
            "payment": ["POST /payments", "GET /payments/{id}"],
            "notification": ["GET /notifications", "PUT /notifications/{id}/read"],
            "feedback": ["POST /feedback", "GET /feedback"],
            "subscription": ["POST /subscriptions", "GET /subscriptions", "DELETE /subscriptions/{id}"],
        }
        for task in tasks:
            name_lower = (task.name or "").lower()
            nouns_found = [n for n in entity_patterns if n in name_lower]
            if nouns_found:
                for noun in nouns_found:
                    has_create = "create" in name_lower
                    endpoints.append({
                        "entity": noun,
                        "task_id": task.id,
                        "suggested_routes": entity_patterns[noun],
                        "graphql_mutation": f"create{capitalize_first(noun)}" if has_create else None,
                    })
            elif "create" in name_lower or "form" in name_lower:
                entity = "entity"
                for keyword in ("form", "page", "screen"):
                    idx = name_lower.find(keyword)
                    if idx > 0:
                        entity = name_lower[:idx].strip().replace(" ", "-")
                        break
                endpoints.append({
                    "entity": entity,
                    "task_id": task.id,
                    "suggested_routes": [f"POST /{entity}"],
                    "graphql_mutation": f"create{capitalize_first(entity.replace('-', '_'))}",
                })
        return endpoints

    derived_endpoints = _derive_expected_endpoints(ctx.ticket.tasks if ctx.ticket and ctx.ticket.tasks else [])

    # Build per-task context with derived feature names and expected layers
    task_contexts: dict[str, dict] = {}
    if ctx.ticket and ctx.ticket.tasks:
        for task in ctx.ticket.tasks:
            feature = extract_feature_name(task.name or "")
            platforms_in_task = task.active_platforms
            task_contexts[str(task.id)] = {
                "task_name": task.name,
                "feature_name": feature,
                "platforms": platforms_in_task,
                "needs_schema": "api" in platforms_in_task,
                "needs_ui": "flutter" in platforms_in_task,
                "expected_files": {
                    "api": [
                        f"libs/data-access/src/{feature}/{feature}.schema.ts",
                        f"libs/data-access/src/{feature}/{feature}.repository.ts",
                        f"libs/data-access/src/{feature}/index.ts",
                        f"apps/api/src/modules/{feature}/{feature}.service.ts",
                        f"apps/api/src/modules/{feature}/{feature}.resolver.ts",
                        f"apps/api/src/modules/{feature}/{feature}.module.ts",
                        f"apps/api/src/modules/{feature}/dto/input/create-{feature}.input.ts",
                        f"apps/api/src/i18n/en/{feature}.json",
                        f"apps/api/src/i18n/ne/{feature}.json",
                    ] if "api" in platforms_in_task else [],
                    "flutter": [
                        f"lib/features/{feature}/domain/repositories/{feature}_repository.dart",
                        f"lib/features/{feature}/data/models/{feature}_model.dart",
                        f"lib/features/{feature}/data/sources/{feature}_remote_source.dart",
                        f"lib/features/{feature}/presentation/cubit/{feature}_cubit.dart",
                        f"lib/features/{feature}/presentation/pages/{feature}_page.dart",
                    ] if "flutter" in platforms_in_task else [],
                },
                "data_access_registrations": [
                    f"libs/data-access/src/index.ts -> export * from './{feature}';",
                    f"libs/data-access/src/data-access.models.ts -> name: {capitalize_first(feature)}.name, schema: {capitalize_first(feature)}Schema",
                    f"libs/data-access/src/data-access.module.ts -> providers + exports",
                ] if "api" in platforms_in_task else [],
                "app_module_registrations": [
                    f"apps/api/src/app.module.ts -> import {capitalize_first(feature)}Module + add to imports + GraphQL include",
                ] if "api" in platforms_in_task else [],
            }

    primary_feature = ""
    if ctx.ticket and ctx.ticket.tasks:
        primary_feature = extract_feature_name(ctx.ticket.tasks[0].name or "")

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

    updated_ctx = ctx.model_copy(update={
        "mocking_level": strategy.mocking_level,
        "offline_first": strategy.offline_first,
        "spoq_epic_dir": active_epic_id,
        "map_id": map_id,
        "shared_context": shared_ctx_data,
        "feature_name": primary_feature or ctx.feature_name,
    })

    return state.model_copy(update={
        "last_node": "orchestrate_agent",
        "strategy": strategy,
        "context": updated_ctx,
        "current_stage": 0,
        "spoq_tasks": active_epic_tasks,
        "shared_context": shared_ctx_data
    })
