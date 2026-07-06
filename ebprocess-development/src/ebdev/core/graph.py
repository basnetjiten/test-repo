# -*- coding: utf-8 -*-
"""
graph.py
========
LangGraph orchestrator graph definition for ebprocess-development.

Responsibilities
----------------
* Define routing conditions between different pipeline stages.
* Construct and compile the stateful execution graph (LangGraph StateGraph).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
# Routing Logic
# ---------------------------------------------------------------------------
def _route_after_plan(state: GraphState) -> str:
    """
    Route after the planning phase.

    Routes to generate on success, or repair on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("generate" or "repair").
    """
    if state.result and state.result.status == "failed":
        return "repair"
    return "generate"


def _route_after_generate(state: GraphState) -> str:
    """
    Route after the code generation phase.

    Routes to validate on success, or repair on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("validate" or "repair").
    """
    if state.result and state.result.status == "failed":
        return "repair"
    return "validate"


def _route_after_validate(state: GraphState) -> str:
    """
    Route after the validation phase.

    Routes to next planning stage on success, repair on failure, or contract when finished.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("plan", "repair", "publish", or "contract").

    Raises
    ------
    ValueError
        If the SPOQ epic task directory is missing when execution mode is SPOQ.
    """
    if state.failed or any(state.failed_platforms.values()):
        return "repair"

    if state.is_spoq:
        return "publish" if state.done else "plan"

    # Backward compatibility for parallel/sequential without SPOQ
    active_platforms = state.context.platforms
    has_failures = any(state.failed_platforms.get(p) for p in active_platforms)

    if has_failures:
        return "repair"

    if state.done:
        return "contract"

    return "plan"


def _route_after_contract(state: GraphState) -> str:
    """
    Route after the contract validation phase.

    Routes to publish on success, or repair on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("publish" or "repair").
    """
    if state.result and state.result.status == "failed":
        return "repair"
    return "publish"


def _route_after_repair(state: GraphState) -> str:
    """
    Route after the repair phase.

    Routes to finalize if max retries hit, otherwise back to generate.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("finalize" or "generate").
    """
    if state.done:  # max iterations reached (failed or successful)
        return "finalize"
    return "generate"


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

    # Add execution nodes
    builder.add_node("prepare", prepare_node)
    builder.add_node("orchestrate", orchestrate_node)
    builder.add_node("plan", plan_node)
    builder.add_node("generate", generate_node)
    builder.add_node("validate", validate_node)
    builder.add_node("contract", contract_node)
    builder.add_node("repair", repair_node)
    builder.add_node("publish", publish_node)
    builder.add_node("finalize", finalize_node)

    # Establish routing edges
    builder.set_entry_point("prepare")
    builder.add_edge("prepare", "orchestrate")
    builder.add_edge("orchestrate", "plan")

    builder.add_conditional_edges("plan", _route_after_plan, {
        "generate": "generate",
        "repair": "repair",
    })

    builder.add_conditional_edges("generate", _route_after_generate, {
        "validate": "validate",
        "repair": "repair",
    })

    builder.add_conditional_edges("validate", _route_after_validate, {
        "contract": "contract",
        "publish": "publish",
        "repair": "repair",
        "plan": "plan",
    })

    builder.add_conditional_edges("contract", _route_after_contract, {
        "publish": "publish",
        "repair": "repair",
    })

    builder.add_conditional_edges("repair", _route_after_repair, {
        "generate": "generate",
        "finalize": "finalize",
    })

    builder.add_edge("publish", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


# Module-level compiled graph — required by langgraph.json
graph = build_graph()
