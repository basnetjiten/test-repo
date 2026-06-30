# -*- coding: utf-8 -*-
"""Common utility functions for graph nodes within ebprocess-development."""

from __future__ import annotations

from ebdev.core.logger import get_logger
from ebdev.models.schemas import GraphState
from ebdev.services import db

logger = get_logger(__name__)


async def send_progress(state: GraphState, message: str) -> None:
    """Record a progress update in the DB and logger."""
    state.status_message = message
    logger.info(f"[{state.last_node}] {message}")

    try:
        await db.sync_state_to_db(state)
    except Exception as e:
        logger.error(f"DB sync failed: {e}")
