# -*- coding: utf-8 -*-
"""LangGraph orchestrator graph definition for ebprocess-development."""

from __future__ import annotations

from langgraph.graph import StateGraph, END

from ebdev.models.schemas import GraphState
from ebdev.core.nodes import (
    prepare_node,
    plan_node,
    generate_node,
    validate_node,
    contract_node,
    repair_node,
    publish_node,
    finalize_node,
)


# ── Routing Logic ────────────────────────────────────────────────────────────
def _route_after_plan(state: GraphState) -> str:
    """Route to generate on success, repair on failure."""
    if state.result and state.result.status == "failed":
        return "repair"
    return "generate"


def _route_after_generate(state: GraphState) -> str:
    """Route to validate on success, repair on failure."""
    if state.result and state.result.status == "failed":
        return "repair"
    return "validate"


def _route_after_validate(state: GraphState) -> str:
    """Route to contract check on pass, repair on fail."""
    if state.done:
        return "contract"
    return "repair"


def _route_after_contract(state: GraphState) -> str:
    """Route to publish on success, repair on failure."""
    if state.result and state.result.status == "failed":
        return "repair"
    return "publish"


def _route_after_repair(state: GraphState) -> str:
    """Route to finalize if max retries hit, otherwise back to generate."""
    if state.done:  # max iterations reached WITH/WITHOUT success (done=True)
        return "finalize"
    return "generate"


# ── Compile StateGraph ────────────────────────────────────────────────────────
def build_graph() -> StateGraph:
    """Construct and compile the stateful graph."""
    builder = StateGraph(GraphState)

    # Add execution nodes
    builder.add_node("prepare", prepare_node)
    builder.add_node("plan", plan_node)
    builder.add_node("generate", generate_node)
    builder.add_node("validate", validate_node)
    builder.add_node("contract", contract_node)
    builder.add_node("repair", repair_node)
    builder.add_node("publish", publish_node)
    builder.add_node("finalize", finalize_node)

    # Establish routing edges
    builder.set_entry_point("prepare")
    builder.add_edge("prepare", "plan")

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
        "repair": "repair",
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
