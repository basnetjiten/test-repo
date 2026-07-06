# -*- coding: utf-8 -*-
"""
spoq_utils.py
=============
Utility functions for interacting with SPOQ tasks and epics.

Based on SPOQ methodology (arXiv:2606.03115v1): Specialist Orchestrated
Queuing for Multi-Agent Software Engineering.

Responsibilities
----------------
* Load SPOQ tasks from epic directories.
* Update statuses of individual SPOQ tasks.
* Compute wave assignments via topological sort.
* Identify tasks ready for execution in the current waves.
* Update ROADMAP.md for epic-level lifecycle tracking.
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
    tasks_dir = Path(epic_dir)
    if not tasks_dir.exists():
        return tasks

    for yml_path in sorted(tasks_dir.glob("*.yml")):
        try:
            with open(yml_path, 'r', encoding='utf-8') as f:
                task = yaml.safe_load(f)
                if task and "id" in task:
                    task.setdefault("status", "pending")
                    task.setdefault("phase", 0)
                    task.setdefault("dependencies", [])
                    task.setdefault("skills_required", [])
                    task.setdefault("files_to_touch", [])
                    task.setdefault("outputs", [])
                    task.setdefault("acceptance_criteria", [])
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
        The status string to set (e.g. "completed", "pending", "blocked").
    """
    yml_path = Path(epic_dir) / f"{task_id}.yml"
    if not yml_path.exists():
        yml_path = Path(epic_dir) / f"{task_id.split('-', 1)[-1]}.yml"
    if not yml_path.exists():
        logger.warning("Task YAML not found for %s in %s", task_id, epic_dir)
        return

    try:
        with open(yml_path, 'r', encoding='utf-8') as f:
            task = yaml.safe_load(f)

        task["status"] = status

        with open(yml_path, 'w', encoding='utf-8') as f:
            yaml.dump(task, f, default_flow_style=False, sort_keys=False)

        logger.info("Task %s status updated to %s", task_id, status)
    except (OSError, yaml.YAMLError) as e:
        logger.error("Error updating task %s: %s", task_id, e)


def compute_waves(epic_dir: str) -> list[list[dict[str, Any]]]:
    """
    Compute parallel execution waves via topological sort.

    Implements Algorithm 1 from the SPOQ paper: wave computation via
    topological sort on the task dependency DAG.

    Parameters
    ----------
    epic_dir : str
        The path to the SPOQ epic directory.

    Returns
    -------
    list[list[dict[str, Any]]]
        A list of waves, where each wave is a list of task dicts
        that can execute in parallel.
    """
    tasks = get_spoq_tasks(epic_dir)
    if not tasks:
        return []

    # Build task lookup and indegree map
    tasks_by_id: dict[str, dict[str, Any]] = {}
    indegree: dict[str, int] = {}
    for t in tasks:
        tid = t["id"]
        tasks_by_id[tid] = t
        indegree[tid] = len(t.get("dependencies", []))

    # Topological wave assignment (Kahn's algorithm)
    waves: list[list[dict[str, Any]]] = []
    assigned: set[str] = set()

    while len(assigned) < len(tasks):
        # Find all tasks with indegree 0 that haven't been assigned
        ready = [
            tasks_by_id[tid]
            for tid in tasks_by_id
            if tid not in assigned and indegree.get(tid, 0) == 0
        ]

        if not ready:
            logger.warning(
                "Cycle detected or unreachable tasks in %s. "
                "Remaining unassigned: %s",
                epic_dir,
                set(tasks_by_id.keys()) - assigned,
            )
            break

        waves.append(ready)

        # Mark as assigned and decrement dependents
        for t in ready:
            tid = t["id"]
            assigned.add(tid)
            for t2 in tasks:
                if tid in t2.get("dependencies", []):
                    dep_tid = t2["id"]
                    if dep_tid not in assigned:
                        indegree[dep_tid] = indegree.get(dep_tid, 1) - 1

    return waves


def get_active_wave_tasks(epic_dir: str) -> list[dict[str, Any]]:
    """
    Find all pending/blocked tasks that are ready to run (all deps completed).

    Uses the wave computation: returns all tasks in the first uncompleted wave.

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
        if t.get("status", "pending") in ("pending", "blocked"):
            deps_completed = True
            for dep_id in t.get("dependencies", []):
                dep_task = tasks_by_id.get(dep_id)
                if not dep_task or dep_task.get("status", "pending") != "completed":
                    deps_completed = False
                    break

            if deps_completed:
                ready_tasks.append(t)

    return ready_tasks


def update_roadmap_status(roadmap_path: str, epic_id: str, status: str) -> bool:
    """
    Update the ROADMAP.md status for an epic.

    Completion is tracked via ROADMAP.md status field (planned → in-progress → done)
    rather than filesystem moves. This keeps the structure flat and simple.

    Parameters
    ----------
    roadmap_path : str
        Path to spoq/ROADMAP.md.
    epic_id : str
        The epic identifier (e.g. "Epic-44445").
    status : str
        The new status: "planned", "in-progress", or "done".

    Returns
    -------
    bool
        True if the roadmap was updated successfully.
    """
    rp = Path(roadmap_path)
    if not rp.exists():
        logger.warning("ROADMAP.md not found at %s", roadmap_path)
        return False

    try:
        content = rp.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Failed to read ROADMAP.md: %s", e)
        return False

    # Try to find the epic row and update its status
    lines = content.splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"| {epic_id} |") or line.strip().startswith(f"|{epic_id}|"):
            parts = [p.strip() for p in line.split("|")]
            # Find status column (usually index 4 in | Epic ID | Sprint | Title | Status |...|)
            for j, part in enumerate(parts):
                if part in ("planned", "in-progress", "done", "blocked"):
                    parts[j] = status
                    lines[i] = "| " + " | ".join(parts[1:-1]) + " |"
                    updated = True
                    break
            break

    if updated:
        try:
            rp.write_text("\n".join(lines), encoding="utf-8")
            logger.info("ROADMAP.md updated: %s → %s", epic_id, status)
            return True
        except OSError as e:
            logger.error("Failed to write ROADMAP.md: %s", e)
            return False

    logger.warning("Epic %s not found in ROADMAP.md", epic_id)
    return False
