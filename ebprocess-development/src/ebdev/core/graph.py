# -*- coding: utf-8 -*-
"""
graph.py
========
LangGraph orchestrator graph definition for ebprocess-development.

Responsibilities
---------------
* Define routing conditions between different pipeline stages.
* Construct and compile the stateful execution graph (LangGraph StateGraph).
* Provide checkpointing via AsyncPostgresSaver for production persistence,
  with graceful fallback to MemorySaver for development or when Postgres
  is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from ebdev.core.nodes import (
    contract_node,
    finalize_node,
    generate_node,
    orchestrate_node,
    plan_node,
    prepare_node,
    publish_node,
    repair_node,
    validate_node,
)
from ebdev.models.schemas import GraphState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Checkpointing strategy
# ---------------------------------------------------------------------------
# PostgresSaver persists GraphState after every node transition so that
# sessions survive restarts and can resume from the exact last node.
# Falls back to MemorySaver (in-memory) when POSTGRES_URL is unset or Postgres
# is unreachable.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Routing Logic
# ---------------------------------------------------------------------------
def _route_after_plan(state: GraphState) -> str:
    """
    Route after the planning phase.

    Routes to builder_agent on success, or repair_agent on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("builder_agent" or "repair_agent").
    """
    if state.result and state.result.status == "failed":
        return "repair_agent"
    return "builder_agent"


def _route_after_generate(state: GraphState) -> str:
    """
    Route after the code generation phase.

    Routes to evaluator_agent on success, or repair_agent on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("evaluator_agent" or "repair_agent").
    """
    if state.result and state.result.status == "failed":
        return "repair_agent"
    return "evaluator_agent"


def _route_after_validate(state: GraphState) -> str:
    """
    Route after the validation phase.

    Routes to next planning stage on success, repair_agent on failure, or contract_agent when finished.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("planner_agent", "repair_agent", "publish_agent", or "contract_agent").

    Raises
    ------
    ValueError
        If the SPOQ epic task directory is missing when execution mode is SPOQ.
    """
    if state.failed or any(state.failed_platforms.values()):
        return "repair_agent"

    if state.is_spoq:
        return "publish_agent" if state.done else "planner_agent"

    # Backward compatibility for parallel/sequential without SPOQ
    active_platforms = state.context.platforms
    has_failures = any(state.failed_platforms.get(p) for p in active_platforms)

    if has_failures:
        return "repair_agent"

    if state.done:
        return "contract_agent"

    return "planner_agent"


def _route_after_contract(state: GraphState) -> str:
    """
    Route after the contract validation phase.

    Routes to publish_agent on success, or repair_agent on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("publish_agent" or "repair_agent").
    """
    if state.result and state.result.status == "failed":
        return "repair_agent"
    return "publish_agent"


def _route_after_repair(state: GraphState) -> str:
    """
    Route after the repair phase.

    Routes to finalize_agent if max retries hit, otherwise back to builder_agent.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("finalize_agent" or "builder_agent").
    """
    if state.done:  # max iterations reached (failed or successful)
        return "finalize_agent"
    return "builder_agent"


# ---------------------------------------------------------------------------
# Compile StateGraph
# ---------------------------------------------------------------------------
def build_graph() -> CompiledStateGraph:
    """
    Construct and compile the stateful execution graph.

    Returns
    -------
    CompiledStateGraph
        The compiled LangGraph StateGraph.
    """
    builder = StateGraph(GraphState)

    # Add execution nodes mapped to agent roles
    builder.add_node("prepare", prepare_node)
    builder.add_node("orchestrate_agent", orchestrate_node)
    builder.add_node("planner_agent", plan_node)
    builder.add_node("builder_agent", generate_node)
    builder.add_node("evaluator_agent", validate_node)
    builder.add_node("contract_agent", contract_node)
    builder.add_node("repair_agent", repair_node)
    builder.add_node("publish_agent", publish_node)
    builder.add_node("finalize_agent", finalize_node)

    # Establish routing edges
    builder.set_entry_point("prepare")
    builder.add_edge("prepare", "orchestrate_agent")
    builder.add_edge("orchestrate_agent", "planner_agent")

    builder.add_conditional_edges("planner_agent", _route_after_plan, {
        "builder_agent": "builder_agent",
        "repair_agent": "repair_agent",
    })

    builder.add_conditional_edges("builder_agent", _route_after_generate, {
        "evaluator_agent": "evaluator_agent",
        "repair_agent": "repair_agent",
    })

    builder.add_conditional_edges("evaluator_agent", _route_after_validate, {
        "contract_agent": "contract_agent",
        "publish_agent": "publish_agent",
        "repair_agent": "repair_agent",
        "planner_agent": "planner_agent",
    })

    builder.add_conditional_edges("contract_agent", _route_after_contract, {
        "publish_agent": "publish_agent",
        "repair_agent": "repair_agent",
    })

    builder.add_conditional_edges("repair_agent", _route_after_repair, {
        "builder_agent": "builder_agent",
        "finalize_agent": "finalize_agent",
    })

    builder.add_edge("publish_agent", "finalize_agent")
    builder.add_edge("finalize_agent", END)

    # Check if running under LangGraph API/dev server
    import os
    import sys
    is_langgraph = (
        "langgraph_api" in sys.modules
        or "langgraph_cli" in sys.modules
        or os.environ.get("LANGGRAPH_API_VERSION") is not None
        or any("langgraph" in arg for arg in sys.argv)
    )

    if is_langgraph:
        return builder.compile()
    else:
        from ebdev.config import config
        if config.POSTGRES_URL:
            try:
                from psycopg_pool import AsyncConnectionPool
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                async def _setup():
                    pool = AsyncConnectionPool(
                        conninfo=config.POSTGRES_URL,
                        min_size=1,
                        max_size=10,
                        kwargs={"autocommit": True},
                        open=False,
                    )
                    await pool.open()
                    saver = AsyncPostgresSaver(pool)
                    await saver.setup()
                    logger.info("LangGraph graph compiled with AsyncPostgresSaver checkpointer.")
                    return builder.compile(checkpointer=saver)

                return asyncio.run(_setup())
            except Exception as e:
                logger.warning("Postgres database is unreachable or failed to initialize: %s. Falling back to MemorySaver.", e)
                return builder.compile(checkpointer=MemorySaver())
        else:
            return builder.compile(checkpointer=MemorySaver())



# Module-level compiled graph — required by langgraph.json
graph = build_graph()
