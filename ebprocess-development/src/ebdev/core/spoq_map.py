# -*- coding: utf-8 -*-
"""
spoq_map.py
===========
Pure state-based SPOQ map utilities for program-level epic orchestration.

All map and epic operations are in-memory computations.  No JSON/YAML files
are read or written.  Task DAGs and epic wave assignments are computed
deterministically and stored in LangGraph state (checkpointed via
MemorySaver / PostgresSaver).
"""

from __future__ import annotations

import logging
from typing import Sequence

from ebdev.models.schemas import EpicTask, SPOQMapEpic, SPOQTask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _task_total_hours(task: EpicTask) -> float:
    return round(sum(hour.estimatedHour for hour in task.hours), 1)


def _estimate_range(hours: float) -> tuple[str, str, str]:
    optimistic = max(hours * 0.75, 0.5)
    realistic = max(hours, 0.5)
    pessimistic = max(hours * 1.5, 0.5)
    return (f"{optimistic:.1f}h", f"{realistic:.1f}h", f"{pessimistic:.1f}h")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_epic_waves(epics: Sequence[SPOQMapEpic]) -> list[list[str]]:
    """
    Compute topological waves for epic-level dispatch (Kahn's algorithm).

    Parameters
    ----------
    epics : Sequence[SPOQMapEpic]
        The list of epic specifications.

    Returns
    -------
    list[list[str]]
        Ordered waves of epic IDs ready for concurrent execution.

    Raises
    ------
    ValueError
        If a cycle is detected or an epic depends on an unknown target.
    """
    epic_ids = [epic.id for epic in epics]
    epic_lookup = {epic.id: epic for epic in epics}

    indegree: dict[str, int] = {}
    for epic in epics:
        missing = [dep for dep in epic.depends_on if dep not in epic_lookup]
        if missing:
            raise ValueError(
                f"Epic {epic.id} depends on unknown epics: {missing}"
            )
        indegree[epic.id] = len(epic.depends_on)

    waves: list[list[str]] = []
    assigned: set[str] = set()

    while len(assigned) < len(epic_ids):
        ready = [
            epic_id
            for epic_id in epic_ids
            if epic_id not in assigned and indegree.get(epic_id, 0) == 0
        ]
        if not ready:
            remaining = set(epic_ids) - assigned
            raise ValueError(
                f"Cycle detected or unreachable epics in SPOQ map: {sorted(remaining)}"
            )

        waves.append(ready)
        for epic_id in ready:
            assigned.add(epic_id)
            for other in epics:
                if epic_id in other.depends_on:
                    indegree[other.id] = max(
                        0, indegree.get(other.id, 0) - 1
                    )

    return waves


def compute_waves_from_tasks(tasks: Sequence[SPOQTask]) -> list[list[str]]:
    """
    Compute task waves from a set of task schemas (Kahn's algorithm).

    Parameters
    ----------
    tasks : Sequence[SPOQTask]
        The task definitions.

    Returns
    -------
    list[list[str]]
        Ordered waves of task IDs.

    Raises
    ------
    ValueError
        If a cycle is detected in the task dependency graph.
    """
    if not tasks:
        return []

    tasks_by_id = {task.id: task for task in tasks}
    indegree = {task.id: len(task.dependencies) for task in tasks}
    waves: list[list[str]] = []
    assigned: set[str] = set()

    while len(assigned) < len(tasks_by_id):
        ready = [
            task_id
            for task_id in tasks_by_id
            if task_id not in assigned and indegree.get(task_id, 0) == 0
        ]
        if not ready:
            remaining = set(tasks_by_id.keys()) - assigned
            raise ValueError(
                f"Cycle detected or unreachable tasks in epic DAG: {sorted(remaining)}"
            )

        waves.append(ready)
        for task_id in ready:
            assigned.add(task_id)
            for task in tasks:
                if task_id in task.dependencies:
                    indegree[task.id] = max(
                        0, indegree.get(task.id, 0) - 1
                    )

    return waves


def build_epic_tasks(
    epic: SPOQMapEpic, mocking_level: str = "live"
) -> list[SPOQTask]:
    """
    Convert a program-level epic into task definitions (in-memory, no files).

    Parameters
    ----------
    epic : SPOQMapEpic
        The epic specification.
    mocking_level : str
        Frontend mocking level: "live", "mock_repositories", or "ui_stubs".

    Returns
    -------
    list[SPOQTask]
        The task DAG with correct phase assignments.
    """
    tasks: list[SPOQTask] = []

    for task in epic.tasks:
        active_platforms = task.active_platforms
        tid = str(task.id)

        contract_id = f"contract-{tid}"
        tasks.append(
            SPOQTask(
                id=contract_id,
                title=f"Define API Contracts for {task.name}",
                epic=epic.id,
                status="pending",
                phase=0,
                dependencies=[],
                skills_required=(
                    ["api"] if "api" in active_platforms else []
                ),
                outputs=["OpenAPI YAML", "Database schema models"],
            )
        )

        impl_tasks: list[str] = []
        api_id = None
        if "api" in active_platforms:
            api_id = f"api-impl-{tid}"
            tasks.append(
                SPOQTask(
                    id=api_id,
                    title=f"API Impl for {task.name}",
                    epic=epic.id,
                    status="blocked",
                    phase=1,
                    dependencies=[contract_id],
                    skills_required=["api"],
                    outputs=["Working routes"],
                )
            )
            impl_tasks.append(api_id)

        if "flutter" in active_platforms:
            flutter_id = f"flutter-impl-{tid}"
            flutter_deps = [contract_id]
            if api_id and mocking_level == "live":
                flutter_deps.append(api_id)
            tasks.append(
                SPOQTask(
                    id=flutter_id,
                    title=f"Flutter Impl for {task.name}",
                    epic=epic.id,
                    status="blocked",
                    phase=1,
                    dependencies=flutter_deps,
                    skills_required=["flutter"],
                    outputs=["Working UI"],
                )
            )
            impl_tasks.append(flutter_id)

        if "web" in active_platforms:
            web_id = f"web-impl-{tid}"
            web_deps = [contract_id]
            if api_id and mocking_level == "live":
                web_deps.append(api_id)
            tasks.append(
                SPOQTask(
                    id=web_id,
                    title=f"Web Impl for {task.name}",
                    epic=epic.id,
                    status="blocked",
                    phase=1,
                    dependencies=web_deps,
                    skills_required=["web"],
                    outputs=["Working UI"],
                )
            )
            impl_tasks.append(web_id)

        if impl_tasks:
            integration_id = f"integration-{tid}"
            tasks.append(
                SPOQTask(
                    id=integration_id,
                    title=f"Integration for {task.name}",
                    epic=epic.id,
                    status="blocked",
                    phase=2,
                    dependencies=impl_tasks,
                    skills_required=active_platforms,
                    outputs=["Verified integration"],
                )
            )

    # Recalculate correct wave phases topologically
    try:
        waves = compute_waves_from_tasks(tasks)
        for phase_index, wave in enumerate(waves):
            for task_id in wave:
                for t in tasks:
                    if t.id == task_id:
                        t.phase = phase_index
    except ValueError as e:
        logger.error(
            "Failed to compute topological wave assignments: %s", e
        )

    return tasks
