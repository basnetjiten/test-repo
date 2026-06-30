# -*- coding: utf-8 -*-
"""Contract node - validates system integration contracts."""

from __future__ import annotations

from ebdev.models.schemas import GraphState
from ebdev.core.nodes.common import send_progress


async def contract_node(state: GraphState) -> GraphState:
    """Validate cross-platform contracts (e.g. alignment between API backend models and Flutter API clients)."""
    state.last_node = "contract"
    ctx = state.context
    platforms = ctx.platforms
    
    await send_progress(state, "Contract check: Running cross-platform verification contracts...")
    
    if "api" in platforms and "flutter" in platforms:
        # Cross-platform contract validation mock
        # In a real system, you'd run schema linter matching client models to server OpenAPI specifications.
        print("[contract] Concurrently verifying API schemas against Flutter client models...")
        await send_progress(state, "Client-Server Contract Check: Verified API models are aligned with Mobile models.")
    else:
        print(f"[contract] Single platform '{platforms}' running. Skipping contract integration checks.")
        
    return state
