# -*- coding: utf-8 -*-
"""Orchestrate node - runs LLM architect evaluation to decide execution topology."""

from __future__ import annotations

import json
import re
from pathlib import Path
from pydantic import ValidationError

from ebdev.config import config
from ebdev.models.schemas import GraphState, OrchestrationStrategy, JobResult
from ebdev.core.nodes.common import send_progress
from ebdev.services.opencode import OpenCodeAPIClient


async def orchestrate_node(state: GraphState) -> GraphState:
    """Analyze the ticket and active platforms to dynamically compile the OrchestrationStrategy."""
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
        default_mode = "mock_first" if not offline_first_detected else "sequential"
        default_stages = [["api"], other_platforms]
        default_mocking = "mock_repositories" if not offline_first_detected else "live"
    else:
        default_mode = "parallel"
        default_stages = [platforms]
        default_mocking = "live"
        
    fallback_strategy = OrchestrationStrategy(
        complexity="high" if (offline_first_detected or (len(platforms) > 1 and not ui_ux_only_detected)) else "low",
        offline_first=offline_first_detected,
        ui_ux_only=ui_ux_only_detected,
        execution_mode=default_mode,
        stages=default_stages,
        mocking_level=default_mocking,
        max_repair_iterations=3,
        reasoning="Rule-based fallback evaluation based on ticket requirements and platform scope."
    )

    # 2. Try LLM Architect (OpenCode)
    strategy = fallback_strategy
    if config.OPENCODE_API_KEY or config.OPENCODE_SERVER_URL:
        try:
            prompt = f"""You are the Head Technical Architect for an autonomous multi-platform project.
Analyze this ticket and decide the best execution strategy for the platforms: {platforms}.

Ticket Details:
- ID: {ticket_id}
- Title: {ticket_title}
- Description: {ticket_desc}
- Acceptance Criteria: {ticket_ac}

Determine:
1. Complexity ("low", "medium", or "high").
2. Whether it requires an offline-first strategy (local-first storage/sync).
3. Whether it is a UI/UX-only presentation modification with no backend or database alterations.
4. Execution mode:
   - "parallel": Run all platforms concurrently (for low complexity, UI-only, or simple API adds).
   - "sequential": Run platforms in stages (e.g. API database migrations first, then frontend).
   - "mock_first": Run API first, frontend consumes mock repositories/clients based on API schemas to build UI/UX immediately.
5. Execution stages: A list of lists representing platform stages (e.g. [["api"], ["flutter", "web"]]).
6. Mocking level for frontends: "live" (connect directly) or "mock_repositories" (mock network/client implementations).
7. Reasoning: Explain the rationale.

You MUST return your decision in this exact JSON structure:
```json
{{
  "complexity": "low" | "medium" | "high",
  "offline_first": true | false,
  "ui_ux_only": true | false,
  "execution_mode": "parallel" | "sequential" | "mock_first",
  "stages": [["stage_1_platform"], ["stage_2_platform"]],
  "mocking_level": "live" | "mock_repositories" | "ui_stubs",
  "max_repair_iterations": 3,
  "reasoning": "rationale here"
}}
```
"""
            server_url = config.OPENCODE_SERVER_URL or "http://opencode:4096"
            client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY)
            
            session_id = await client.create_session(title=f"Architect-{ticket_id}")
            res = await client.send_prompt_message(
                session_id=session_id,
                agent="architect",
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
                print(f"[orchestrate] LLM architect strategy selected: {strategy.execution_mode} ({strategy.complexity} complexity)")
        except Exception as e:
            print(f"[orchestrate] Warning: LLM architect failed, using rule-based fallback: {e}")

    # Log the strategy decision
    print(f"\n=== ORCHESTRATION STRATEGY SELECTED ===")
    print(f"Complexity:    {strategy.complexity}")
    print(f"Offline First: {strategy.offline_first}")
    print(f"UI/UX Only:    {strategy.ui_ux_only}")
    print(f"Execution:     {strategy.execution_mode}")
    print(f"Stages:        {strategy.stages}")
    print(f"Mock Level:    {strategy.mocking_level}")
    print(f"Reasoning:     {strategy.reasoning}\n")

    updated_ctx = ctx.model_copy(update={
        "mocking_level": strategy.mocking_level,
        "offline_first": strategy.offline_first
    })

    return state.model_copy(update={
        "last_node": "orchestrate",
        "strategy": strategy,
        "context": updated_ctx,
        "current_stage": 0
    })
