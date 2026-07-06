# -*- coding: utf-8 -*-
"""Focused regression checks for SPOQ map-level orchestration artifacts."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

# Ensure src directory is importable for direct execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.core.spoq_map import (
    load_map_manifest,
    materialize_spoq_map,
    mark_epic_status,
    next_ready_epic,
)
from ebdev.models.schemas import EpicTask, JobContext, SPOQMap, SPOQMapEpic, SprintTicket
from ebdev.services.prompts import build_prompt


def _task(task_id: int, platform_name: str) -> EpicTask:
    return EpicTask(
        id=task_id,
        name=f"Task {task_id}",
        status="todo",
        hours=[
            {
                "estimatedHour": 1.0,
                "taskId": task_id,
                "platformId": 1,
                "platform": {"id": 1, "name": platform_name},
            }
        ],
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        project_spoq = tmp_path / "spoq"

        epic_a = SPOQMapEpic(
            id="Epic-A",
            title="Epic A",
            description="First epic",
            tasks=[_task(101, "api")],
            platforms=["api"],
            acceptance_criteria=["Epic A completes"],
        )
        epic_b = SPOQMapEpic(
            id="Epic-B",
            title="Epic B",
            description="Second epic",
            depends_on=["Epic-A"],
            tasks=[_task(201, "flutter")],
            platforms=["flutter"],
            acceptance_criteria=["Epic B completes"],
        )

        program_map = SPOQMap(
            id="Map-999",
            title="Sample Program",
            vision="Deliver two dependent epics.",
            epics=[epic_a, epic_b],
            success_criteria=["Both epics complete."],
        )

        map_dir, epic_dirs = materialize_spoq_map(project_spoq, program_map)
        assert map_dir.exists()
        assert (map_dir / "MAP.md").exists()
        assert (map_dir / "MAP.json").exists()
        assert len(epic_dirs) == 2
        assert (epic_dirs[0] / "EPIC.md").exists()
        assert (epic_dirs[1] / "EPIC.md").exists()
        assert any(path.name.startswith("contract-") for path in epic_dirs[0].iterdir())

        loaded = load_map_manifest(map_dir)
        assert loaded.wave_assignments == [["Epic-A"], ["Epic-B"]]
        assert next_ready_epic(loaded).id == "Epic-A"

        mark_epic_status(loaded, "Epic-A", "done")
        assert next_ready_epic(loaded).id == "Epic-B"

        prompt_ctx = JobContext(
            task_id="999",
            space_name="AgentSwipe",
            ticket_id="Map-999",
            ticket=SprintTicket(
                id="Map-999",
                title="Sample Program",
                description="Deliver two dependent epics.",
                status="todo",
            ),
            repo_path=str(tmp_path),
            platforms=["api", "flutter"],
            current_agent="build",
            active_task_id="contract-101",
            spoq_map_dir=str(map_dir),
            spoq_epic_dir=str(epic_dirs[0]),
        )

        prompt = build_prompt(prompt_ctx, storage_dir=tmp_path, platform="api")
        assert "SPOQ Map File:" in prompt
        assert "MAP.md" in prompt

        print("SPOQ map contract checks passed.")


if __name__ == "__main__":
    main()
