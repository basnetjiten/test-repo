# -*- coding: utf-8 -*-
"""
spoq_utils.py
=============
Pure state-based SPOQ task management — operates on LangGraph state only.

All task access, status updates, and wave computation use the in-memory
GraphState.spoq_tasks list.  No YAML files are read or written.
LangGraph checkpointing (MemorySaver / PostgresSaver) persists the state
so sessions survive restarts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ebdev.models.schemas import SPOQTask

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_state_active_tasks(spoq_tasks: list[SPOQTask]) -> list[SPOQTask]:
    """
    Find all pending/blocked tasks ready to run (all deps completed) from state.

    Parameters
    ----------
    spoq_tasks : list[SPOQTask]
        The list of tasks in the current GraphState.

    Returns
    -------
    list[SPOQTask]
        The list of tasks ready for execution in the current wave.
    """
    tasks_by_id = {t.id: t for t in spoq_tasks}
    ready_tasks: list[SPOQTask] = []
    for t in spoq_tasks:
        if t.status in ("pending", "blocked", "in_progress"):
            deps_completed = True
            for dep_id in t.dependencies:
                dep_task = tasks_by_id.get(dep_id)
                if not dep_task or dep_task.status != "completed":
                    deps_completed = False
                    break
            if deps_completed:
                ready_tasks.append(t)
    return ready_tasks


def update_state_task_status(
    spoq_tasks: list[SPOQTask], task_id: str, status: str
) -> list[SPOQTask]:
    """
    Update status of a specific task in the list and return the updated list.

    Parameters
    ----------
    spoq_tasks : list[SPOQTask]
        The list of tasks in the current GraphState.
    task_id : str
        The ID of the target task to update.
    status : str
        The status string to set (e.g. "completed", "pending", "blocked").

    Returns
    -------
    list[SPOQTask]
        The updated list of SPOQ tasks.
    """
    updated: list[SPOQTask] = []
    for t in spoq_tasks:
        if t.id == task_id:
            updated.append(t.model_copy(update={"status": status}))
        else:
            updated.append(t)
    return updated
