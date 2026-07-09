# -*- coding: utf-8 -*-
"""
common.py
=========
Common utility functions for graph nodes within ebprocess-development.

Responsibilities
----------------
* Update execution progress messages.
* Sync graph state changes to the database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebdev.core.logger import get_logger
from ebdev.services import db

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def send_progress(state: GraphState, message: str) -> None:
    """
    Record a progress update in the database and logger.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.
    message : str
        The progress message to record.
    """
    state.status_message = message
    logger.info("[%s] %s", state.last_node, message)

    try:
        await db.sync_state_to_db(state)
    except (OSError, ValueError) as e:
        logger.error("DB sync failed: %s", e)
