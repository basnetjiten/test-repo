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

from collections import deque
from typing import Sequence

from ebdev.core.logger import get_logger
from ebdev.models.spoq import SPOQMapEpic, SPOQTask
from ebdev.models.ticket import EpicTask

logger = get_logger(__name__)


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
    epic_lookup = {epic.id: epic for epic in epics}

    # Validate all dependencies and build reverse adjacency in one pass — O(V+E).
    indegree: dict[str, int] = {}
    dependants: dict[str, list[str]] = {epic.id: [] for epic in epics}
    for epic in epics:
        missing = [dep for dep in epic.depends_on if dep not in epic_lookup]
        if missing:
            raise ValueError(f"Epic {epic.id} depends on unknown epics: {missing}")
        indegree[epic.id] = len(epic.depends_on)
        for dep in epic.depends_on:
            dependants[dep].append(epic.id)

    # Seed queue with all zero-indegree nodes — avoids O(V) scan per wave.
    queue: deque[str] = deque(eid for eid, deg in indegree.items() if deg == 0)
    total = len(epic_lookup)
    processed = 0
    waves: list[list[str]] = []

    while queue:
        # Drain the entire current wave in one pass.
        wave_size = len(queue)
        wave: list[str] = []
        for _ in range(wave_size):
            epic_id = queue.popleft()
            wave.append(epic_id)
            processed += 1
            for child_id in dependants[epic_id]:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)
        waves.append(wave)

    if processed != total:
        remaining = {eid for eid, deg in indegree.items() if deg > 0}
        raise ValueError(f"Cycle detected or unreachable epics in SPOQ map: {sorted(remaining)}")

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

    # Build reverse adjacency in one pass — O(V+E).
    dependants: dict[str, list[str]] = {task.id: [] for task in tasks}
    indegree: dict[str, int] = {task.id: 0 for task in tasks}
    for task in tasks:
        for dep_id in task.dependencies:
            if dep_id not in dependants:
                # External dependency already satisfied — skip.
                continue
            dependants[dep_id].append(task.id)
            indegree[task.id] += 1

    # Seed queue — no O(V) scan per wave.
    queue: deque[str] = deque(tid for tid, deg in indegree.items() if deg == 0)
    total = len(indegree)
    processed = 0
    waves: list[list[str]] = []

    while queue:
        wave_size = len(queue)
        wave: list[str] = []
        for _ in range(wave_size):
            task_id = queue.popleft()
            wave.append(task_id)
            processed += 1
            for child_id in dependants[task_id]:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)
        waves.append(wave)

    if processed != total:
        remaining = {tid for tid, deg in indegree.items() if deg > 0}
        raise ValueError(f"Cycle detected or unreachable tasks in epic DAG: {sorted(remaining)}")

    return waves


def build_epic_tasks(epic: SPOQMapEpic, mocking_level: str = "live") -> list[SPOQTask]:
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
                skills_required=(["api"] if "api" in active_platforms else [active_platforms[0]] if active_platforms else []),
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

        if "cms" in active_platforms:
            cms_id = f"cms-impl-{tid}"
            cms_deps = [contract_id]
            if api_id and mocking_level == "live":
                cms_deps.append(api_id)
            tasks.append(
                SPOQTask(
                    id=cms_id,
                    title=f"CMS Impl for {task.name}",
                    epic=epic.id,
                    status="blocked",
                    phase=1,
                    dependencies=cms_deps,
                    skills_required=["cms"],
                    outputs=["Working UI"],
                )
            )
            impl_tasks.append(cms_id)

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

    # Recalculate correct wave phases topologically — O(T) dict lookup, not O(T²) scan.
    try:
        waves = compute_waves_from_tasks(tasks)
        phase_map: dict[str, int] = {
            task_id: phase_index
            for phase_index, wave in enumerate(waves)
            for task_id in wave
        }
        tasks = [
            t.model_copy(update={"phase": phase_map[t.id]}) if t.id in phase_map else t
            for t in tasks
        ]
    except ValueError as e:
        logger.error("Failed to compute topological wave assignments: %s", e)

    return tasks
