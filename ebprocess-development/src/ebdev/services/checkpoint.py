# -*- coding: utf-8 -*-
"""
checkpoint.py
=============
Checkpoint lifecycle utilities for LangGraph thread-scoped persistence.

Responsibilities
---------------
* Clean up completed thread checkpoints to prevent unbounded storage growth.
* Inspect checkpoint history for time-travel debugging.
* Respect ``CHECKPOINT_CLEANUP_ON_COMPLETE`` configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.logger import get_logger

if TYPE_CHECKING:
    from typing import Any

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def cleanup_thread(thread_id: str) -> bool:
    """
    Delete all checkpoints and writes for a completed thread.

    Uses the compiled graph's checkpointer when available; returns silently
    when running in MemorySaver mode (no persistent storage to clean).

    Parameters
    ----------
    thread_id : str
        The thread identifier (e.g. ``"thread-ENQ-5"``).

    Returns
    -------
    bool
        ``True`` if the thread was deleted, ``False`` if cleanup was skipped
        or the checkpointer is not available.
    """
    if not config.CHECKPOINT_CLEANUP_ON_COMPLETE:
        logger.debug("Checkpoint cleanup disabled by config.")
        return False

    # Lazy import to avoid circular dependency (graph imports nodes,
    # nodes import services, services should not import graph at top level)
    from ebdev.core.graph import graph as compiled_graph

    checkpointer = getattr(compiled_graph, "checkpointer", None)
    if checkpointer is None:
        return False

    # MemorySaver / InMemorySaver do not need cleanup (in-memory, dies with process)
    saver_name = type(checkpointer).__name__
    if saver_name in ("MemorySaver", "InMemorySaver"):
        return False

    if not hasattr(checkpointer, "adelete_thread"):
        logger.warning(
            "Checkpointer %r has no adelete_thread method — cannot clean up thread %s.",
            type(checkpointer).__name__,
            thread_id,
        )
        return False

    try:
        await checkpointer.adelete_thread(thread_id)
        logger.info("Cleaned up checkpoint thread: %s", thread_id)
        return True
    except Exception as exc:
        logger.exception("Failed to clean up checkpoint thread %s: %s", thread_id, exc)
        return False


async def get_thread_history(
    thread_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Retrieve checkpoint history for a thread (newest first).

    Useful for debugging, time-travel inspection, and audit trails.

    Parameters
    ----------
    thread_id : str
        Thread identifier.
    limit : int
        Maximum number of checkpoints to return.

    Returns
    -------
    list[dict]
        List of checkpoint metadata dicts with ``checkpoint_id``,
        ``step``, ``source``, ``created_at``, and ``next_nodes``.
    """
    from ebdev.core.graph import graph as compiled_graph

    checkpointer = getattr(compiled_graph, "checkpointer", None)
    if checkpointer is None or not hasattr(checkpointer, "alist"):
        return []

    thread_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    history: list[dict[str, Any]] = []

    try:
        import inspect

        res = checkpointer.alist(thread_config, limit=limit)
        results = await res if inspect.isawaitable(res) else res
        async for checkpoint_tuple in results:
            ckpt = checkpoint_tuple.checkpoint
            metadata = getattr(checkpoint_tuple, "metadata", {}) or {}

            history.append(
                {
                    "checkpoint_id": ckpt.get("id"),
                    "step": metadata.get("step"),
                    "source": metadata.get("source"),
                    "next_nodes": checkpoint_tuple.config.get("configurable", {}).get("checkpoint_ns", ""),
                }
            )
    except Exception as exc:
        logger.exception("Failed to fetch checkpoint history for thread %s: %s", thread_id, exc)

    return history
