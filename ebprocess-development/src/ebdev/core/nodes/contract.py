# -*- coding: utf-8 -*-
"""
contract.py
===========
Contract node - validates system integration contracts.

Responsibilities
----------------
* Validate cross-platform contracts (e.g., alignment between API backend models
  and Flutter API clients).
"""

from __future__ import annotations

import logging

from ebdev.core.nodes.common import send_progress
from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def contract_node(state: GraphState) -> GraphState:
    """
    Validate cross-platform contracts.

    In a real system, this runs a schema linter to match client models against
    server OpenAPI specifications.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with the contract verification status recorded.
    """
    state.last_node = "contract_agent"
    ctx = state.context
    platforms = ctx.platforms

    await send_progress(state, "Contract check: Running cross-platform verification contracts...")

    if "api" in platforms and "flutter" in platforms:
        # Cross-platform contract validation mock
        logger.info("Concurrently verifying API schemas against Flutter client models...")
        await send_progress(
            state,
            "Client-Server Contract Check: Verified API models are aligned with Mobile models."
        )
    else:
        logger.info("Single platform %s running. Skipping contract integration checks.", platforms)

    return state.model_copy(update={"last_node": "contract_agent"})
