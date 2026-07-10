# -*- coding: utf-8 -*-
"""Focused regression checks for SPOQ map computation (pure state, no disk I/O)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.core.spoq_map import build_epic_tasks, compute_epic_waves
from ebdev.models.spoq import SPOQMapEpic
from ebdev.models.ticket import EpicTask, EpicTaskHour, EpicTaskPlatform


def main() -> None:
    """Verify that epic wave computation and task DAG building work in-memory."""

    # --- Wave computation (no disk) ---
    epic_a = SPOQMapEpic(
        id="Epic-A",
        title="Epic A",
        description="First epic",
        depends_on=[],
        platforms=["api"],
    )
    epic_b = SPOQMapEpic(
        id="Epic-B",
        title="Epic B",
        description="Second epic",
        depends_on=["Epic-A"],
        platforms=["flutter"],
    )

    waves = compute_epic_waves([epic_a, epic_b])
    assert waves == [["Epic-A"], ["Epic-B"]], f"Expected 2 waves, got {waves}"

    # --- Task DAG building (no disk) ---
    task = EpicTask(
        id=101,
        name="Test Task",
        status="todo",
        hours=[
            EpicTaskHour(
                estimatedHour=1.0,
                taskId=101,
                platformId=1,
                platform=EpicTaskPlatform(id=1, name="api"),
            ),
            EpicTaskHour(
                estimatedHour=1.0,
                taskId=101,
                platformId=2,
                platform=EpicTaskPlatform(id=2, name="flutter"),
            ),
        ],
    )

    epic_task = SPOQMapEpic(
        id="Epic-T1",
        title="Task Epic",
        description="Epic with dual-platform tasks",
        tasks=[task],
        platforms=["api", "flutter"],
    )

    spoq_tasks = build_epic_tasks(epic_task)
    assert len(spoq_tasks) >= 3, (
        f"Expected at least 3 tasks (contract + api-impl + flutter-impl), got {len(spoq_tasks)}"
    )

    # Verify dependency chain: contract -> api-impl -> flutter-impl
    contract_task = next(t for t in spoq_tasks if t.id.startswith("contract-"))
    api_task = next(t for t in spoq_tasks if t.id.startswith("api-impl-"))
    flutter_task = next(t for t in spoq_tasks if t.id.startswith("flutter-impl-"))

    assert contract_task.phase == 0, "Contract task should be wave 0"
    assert contract_task.status == "pending"
    assert contract_task.dependencies == []

    assert api_task.dependencies == [contract_task.id], "API impl should depend on contract"
    assert flutter_task.dependencies == [contract_task.id, api_task.id], (
        "Flutter impl should depend on contract + API impl"
    )

    print("SPOQ map computation checks passed.")


if __name__ == "__main__":
    main()
