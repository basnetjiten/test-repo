# -*- coding: utf-8 -*-
"""
spoq_utils.py
=============
Utility functions for interacting with SPOQ tasks and epics.

Responsibilities
----------------
* Load SPOQ tasks from epic directories.
* Update statuses of individual SPOQ tasks.
* Identify tasks ready for execution in the current waves.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_spoq_tasks(epic_dir: str) -> list[dict[str, Any]]:
    """
    Load all task YAMLs from the epic directory.

    Parameters
    ----------
    epic_dir : str
        The path to the SPOQ epic directory.

    Returns
    -------
    list[dict[str, Any]]
        The list of parsed task dictionaries.
    """
    tasks = []
    tasks_dir = Path(epic_dir) / "tasks"
    if not tasks_dir.exists():
        return tasks
        
    for yml_path in tasks_dir.glob("*.yml"):
        try:
            with open(yml_path, 'r', encoding='utf-8') as f:
                task = yaml.safe_load(f)
                tasks.append(task)
        except (OSError, yaml.YAMLError) as e:
            logger.warning("Failed to load task %s: %s", yml_path, e)
    return tasks


def update_spoq_task_status(epic_dir: str, task_id: str, status: str) -> None:
    """
    Update the status of a specific task.

    Parameters
    ----------
    epic_dir : str
        The path to the SPOQ epic directory.
    task_id : str
        The ID of the target task to update.
    status : str
        The status string to set (e.g. "completed", "pending").
    """
    yml_path = Path(epic_dir) / "tasks" / f"{task_id}.yml"
    if not yml_path.exists():
        return
        
    try:
        with open(yml_path, 'r', encoding='utf-8') as f:
            task = yaml.safe_load(f)
            
        task["status"] = status
        
        with open(yml_path, 'w', encoding='utf-8') as f:
            yaml.dump(task, f, default_flow_style=False, sort_keys=False)
    except (OSError, yaml.YAMLError) as e:
        logger.error("Error updating task %s: %s", task_id, e)


def get_active_wave_tasks(epic_dir: str) -> list[dict[str, Any]]:
    """
    Find all pending/blocked tasks that are ready to run (all dependencies completed).

    Parameters
    ----------
    epic_dir : str
        The path to the SPOQ epic directory.

    Returns
    -------
    list[dict[str, Any]]
        The list of tasks ready for execution in the current wave.
    """
    tasks = get_spoq_tasks(epic_dir)
    tasks_by_id = {t["id"]: t for t in tasks}
    
    ready_tasks = []
    for t in tasks:
        if t["status"] in ["pending", "blocked"]:
            deps_completed = True
            for dep_id in t.get("dependencies", []):
                dep_task = tasks_by_id.get(dep_id)
                if not dep_task or dep_task["status"] != "completed":
                    deps_completed = False
                    break
            
            if deps_completed:
                ready_tasks.append(t)
                
    return ready_tasks
