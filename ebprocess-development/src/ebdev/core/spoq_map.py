# -*- coding: utf-8 -*-
"""SPOQ map utilities for program-level epic orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

import yaml
from ebdev.models.schemas import EpicTask, SPOQMap, SPOQMapEpic, SPOQTask

logger = logging.getLogger(__name__)

MAP_JSON_FILENAME = "MAP.json"
MAP_MD_FILENAME = "MAP.md"
EPICS_DIRNAME = ""
JOURNALS_DIRNAME = "journals"


def _task_total_hours(task: EpicTask) -> float:
    return round(sum(hour.estimatedHour for hour in task.hours), 1)


def _estimate_range(hours: float) -> tuple[str, str, str]:
    optimistic = max(hours * 0.75, 0.5)
    realistic = max(hours, 0.5)
    pessimistic = max(hours * 1.5, 0.5)
    return (f"{optimistic:.1f}h", f"{realistic:.1f}h", f"{pessimistic:.1f}h")


def compute_epic_waves(epics: Sequence[SPOQMapEpic]) -> list[list[str]]:
    """Compute topological waves for epic-level dispatch."""
    epic_ids = [epic.id for epic in epics]
    epic_lookup = {epic.id: epic for epic in epics}

    indegree: dict[str, int] = {}
    for epic in epics:
        missing = [dep for dep in epic.depends_on if dep not in epic_lookup]
        if missing:
            raise ValueError(f"Epic {epic.id} depends on unknown epics: {missing}")
        indegree[epic.id] = len(epic.depends_on)

    waves: list[list[str]] = []
    assigned: set[str] = set()

    while len(assigned) < len(epic_ids):
        ready = [epic_id for epic_id in epic_ids if epic_id not in assigned and indegree.get(epic_id, 0) == 0]
        if not ready:
            remaining = set(epic_ids) - assigned
            raise ValueError(f"Cycle detected or unreachable epics in SPOQ map: {sorted(remaining)}")

        waves.append(ready)
        for epic_id in ready:
            assigned.add(epic_id)
            for other in epics:
                if epic_id in other.depends_on:
                    indegree[other.id] = max(0, indegree.get(other.id, 0) - 1)

    return waves


def build_epic_tasks(epic: SPOQMapEpic) -> list[SPOQTask]:
    """Convert a program-level epic into task YAML definitions."""
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
                skills_required=["api"] if "api" in active_platforms else [],
                outputs=["OpenAPI YAML", "Database schema models"],
            )
        )

        impl_tasks: list[str] = []
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
            tasks.append(
                SPOQTask(
                    id=flutter_id,
                    title=f"Flutter Impl for {task.name}",
                    epic=epic.id,
                    status="blocked",
                    phase=1,
                    dependencies=[contract_id],
                    skills_required=["flutter"],
                    outputs=["Working UI"],
                )
            )
            impl_tasks.append(flutter_id)

        if "web" in active_platforms:
            web_id = f"web-impl-{tid}"
            tasks.append(
                SPOQTask(
                    id=web_id,
                    title=f"Web Impl for {task.name}",
                    epic=epic.id,
                    status="blocked",
                    phase=1,
                    dependencies=[contract_id],
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

    return tasks


def render_epic_markdown(epic: SPOQMapEpic, tasks: Sequence[SPOQTask]) -> str:
    """Render the human-readable EPIC.md file for a single epic."""
    waves = compute_waves_from_tasks(tasks)
    total_hours = epic.estimated_hours or round(sum(_task_total_hours(task) for task in epic.tasks), 1)
    optimistic, realistic, pessimistic = _estimate_range(total_hours)

    lines: list[str] = [
        f"# Epic: {epic.title}",
        "",
        f"**Goal**: {epic.description or epic.title}",
        "",
        "## Architecture",
        "",
        f"- Epic ID: `{epic.id}`",
        f"- Status: `{epic.status}`",
        f"- Sprint: `{epic.sprint}`",
        f"- Platforms: {', '.join(epic.platforms) if epic.platforms else 'TBD'}",
        f"- Depends On: {', '.join(epic.depends_on) if epic.depends_on else 'None'}",
        "",
        "## Success Criteria",
    ]

    if epic.acceptance_criteria:
        for criterion in epic.acceptance_criteria:
            lines.append(f"- {criterion}")
    else:
        lines.append("- Epic tasks validate successfully and are ready for integration.")

    lines.extend([
        "",
        "## Dependency Graph",
        "",
        "```mermaid",
        "graph TD",
    ])

    for task in tasks:
        if task.dependencies:
            for dep in task.dependencies:
                lines.append(f"  {dep} --> {task.id}")
        else:
            lines.append(f"  {task.id}")

    lines.extend([
        "```",
        "",
        "## Wave Assignments",
        "",
        "| Wave | Tasks |",
        "|------|-------|",
    ])

    for wave_index, wave in enumerate(waves):
        lines.append(f"| {wave_index} | {', '.join(wave)} |")

    lines.extend([
        "",
        "## Effort Estimates",
        "",
        "| Task | Optimistic | Realistic | Pessimistic |",
        "|------|------------|-----------|-------------|",
    ])

    for task in epic.tasks:
        task_hours = _task_total_hours(task)
        task_optimistic, task_realistic, task_pessimistic = _estimate_range(task_hours)
        lines.append(f"| {task.id} | {task_optimistic} | {task_realistic} | {task_pessimistic} |")

    lines.extend([
        "",
        "## Risk Assessment",
        "",
        "- Cross-epic dependencies may delay the critical path if upstream epics slip.",
        "- Contract drift between API and client epics can cause rework if validation is incomplete.",
        "",
        "## Total Estimate",
        "",
        f"- Optimistic: {optimistic}",
        f"- Realistic: {realistic}",
        f"- Pessimistic: {pessimistic}",
    ])

    return "\n".join(lines).rstrip() + "\n"


def render_map_markdown(program_map: SPOQMap) -> str:
    """Render the human-readable MAP.md file."""
    waves = program_map.wave_assignments or compute_epic_waves(program_map.epics)
    epic_lookup = {epic.id: epic for epic in program_map.epics}

    lines: list[str] = [
        f"# Map: {program_map.title}",
        "",
        f"**Map ID**: `{program_map.id}`",
        f"**Status**: `{program_map.status}`",
        "",
        "## Vision",
        "",
        program_map.vision or "Coordinate multiple epics with wave-based dispatch.",
        "",
        "## Program Structure",
        "",
        f"- Epic Count: {len(program_map.epics)}",
        f"- Dispatch Strategy: {program_map.dispatch_strategy}",
        "",
        "## Epics",
        "",
        "| Epic ID | Sprint | Status | Depends On | Platforms | Estimated Hours | Path |",
        "|---------|--------|--------|------------|-----------|-----------------|------|",
    ]

    for epic in program_map.epics:
        depends_on = ", ".join(epic.depends_on) if epic.depends_on else "—"
        platforms = ", ".join(epic.platforms) if epic.platforms else "—"
        epic_path = epic.path or (f"{EPICS_DIRNAME}/{epic.id}" if EPICS_DIRNAME else epic.id)
        lines.append(
            f"| {epic.id} | {epic.sprint} | {epic.status} | {depends_on} | {platforms} | {epic.estimated_hours:.1f} | {epic_path} |"
        )

    lines.extend([
        "",
        "## Epic Dependencies",
        "",
        "```mermaid",
        "graph TD",
    ])

    for epic in program_map.epics:
        if epic.depends_on:
            for dep in epic.depends_on:
                lines.append(f"  {dep} --> {epic.id}")
        else:
            lines.append(f"  {epic.id}")

    lines.extend([
        "```",
        "",
        "## Dispatch Strategy",
        "",
        "- Wave 0 epics start first.",
        "- Later waves unlock only after their dependencies are marked done.",
        "- Each epic retains its own EPIC.md and task DAG.",
        "",
        "## Success Criteria",
        "",
    ])

    if program_map.success_criteria:
        for criterion in program_map.success_criteria:
            lines.append(f"- {criterion}")
    else:
        lines.append("- All epic wave dependencies resolve cleanly and every epic reaches done status.")

    lines.extend([
        "",
        "## Estimated Effort",
        "",
        "| Epic ID | Hours |",
        "|---------|-------|",
    ])

    for epic in program_map.epics:
        lines.append(f"| {epic.id} | {epic.estimated_hours:.1f} |")

    lines.extend([
        "",
        "## Risk Assessment",
        "",
    ])

    if program_map.risk_assessment:
        for item in program_map.risk_assessment:
            lines.append(f"- {item}")
    else:
        lines.append("- Cross-epic contract drift.")
        lines.append("- Dependency cycles or missing epic prerequisites.")

    lines.extend([
        "",
        "## Wave Assignments",
        "",
        "| Wave | Epics |",
        "|------|-------|",
    ])

    for wave_index, wave in enumerate(waves):
        lines.append(f"| {wave_index} | {', '.join(wave)} |")

    lines.extend([
        "",
        "## Epic Snapshot",
        "",
    ])

    for epic_id, epic in epic_lookup.items():
        lines.append(f"- {epic_id}: {epic.status}")

    return "\n".join(lines).rstrip() + "\n"


def compute_waves_from_tasks(tasks: Sequence[SPOQTask]) -> list[list[str]]:
    """Compute task waves from a set of task schemas."""
    if not tasks:
        return []

    tasks_by_id = {task.id: task for task in tasks}
    indegree = {task.id: len(task.dependencies) for task in tasks}
    waves: list[list[str]] = []
    assigned: set[str] = set()

    while len(assigned) < len(tasks_by_id):
        ready = [task_id for task_id in tasks_by_id if task_id not in assigned and indegree.get(task_id, 0) == 0]
        if not ready:
            remaining = set(tasks_by_id.keys()) - assigned
            raise ValueError(f"Cycle detected or unreachable tasks in epic DAG: {sorted(remaining)}")

        waves.append(ready)
        for task_id in ready:
            assigned.add(task_id)
            for task in tasks:
                if task_id in task.dependencies:
                    indegree[task.id] = max(0, indegree.get(task.id, 0) - 1)

    return waves


def load_map_manifest(map_dir: str | Path) -> SPOQMap:
    """Load a JSON map manifest from disk."""
    map_path = Path(map_dir) / MAP_JSON_FILENAME
    data = json.loads(map_path.read_text(encoding="utf-8"))
    return SPOQMap(**data)


def save_map_manifest(map_dir: str | Path, program_map: SPOQMap) -> Path:
    """Persist the MAP.json and MAP.md artifacts."""
    map_path = Path(map_dir)
    map_path.mkdir(parents=True, exist_ok=True)
    program_map.map_dir = str(map_path)

    json_path = map_path / MAP_JSON_FILENAME
    md_path = map_path / MAP_MD_FILENAME
    json_path.write_text(program_map.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    md_path.write_text(render_map_markdown(program_map), encoding="utf-8")
    return json_path


def materialize_spoq_map(project_spoq_dir: str | Path, program_map: SPOQMap) -> tuple[Path, list[Path]]:
    """Create the on-disk map, epic, and task artifacts."""
    project_spoq = Path(project_spoq_dir)
    map_dir = project_spoq / program_map.id
    epics_root = map_dir / EPICS_DIRNAME if EPICS_DIRNAME else map_dir
    epics_root.mkdir(parents=True, exist_ok=True)

    resolved_epics: list[SPOQMapEpic] = []
    epic_dirs: list[Path] = []

    for epic in program_map.epics:
        epic_dir = epics_root / epic.id
        epic_dir.mkdir(parents=True, exist_ok=True)
        (epic_dir / JOURNALS_DIRNAME).mkdir(parents=True, exist_ok=True)

        tasks = build_epic_tasks(epic)
        epic.path = f"{EPICS_DIRNAME}/{epic.id}" if EPICS_DIRNAME else epic.id
        epic.estimated_hours = round(sum(_task_total_hours(task) for task in epic.tasks), 1)

        epic_md = render_epic_markdown(epic, tasks)
        (epic_dir / "EPIC.md").write_text(epic_md, encoding="utf-8")

        for task in tasks:
            yml_path = epic_dir / f"{task.id}.yml"
            yml_path.write_text(yaml.dump(task.model_dump(mode="json"), default_flow_style=False, sort_keys=False), encoding="utf-8")

        resolved_epics.append(epic)
        epic_dirs.append(epic_dir)

    program_map.epics = resolved_epics
    program_map.wave_assignments = compute_epic_waves(program_map.epics)
    save_map_manifest(map_dir, program_map)
    return map_dir, epic_dirs


def find_epic_by_id(program_map: SPOQMap, epic_id: str) -> SPOQMapEpic | None:
    """Return the epic entry matching the provided ID."""
    for epic in program_map.epics:
        if epic.id == epic_id:
            return epic
    return None


def mark_epic_status(program_map: SPOQMap, epic_id: str, status: str) -> SPOQMap:
    """Update an epic status in the manifest and keep the map status in sync."""
    epic = find_epic_by_id(program_map, epic_id)
    if epic is None:
        raise ValueError(f"Epic {epic_id} not found in map {program_map.id}")

    epic.status = status
    statuses = [entry.status for entry in program_map.epics]
    if statuses and all(entry == "done" for entry in statuses):
        program_map.status = "done"
    elif any(entry == "in-progress" for entry in statuses) or any(entry == "done" for entry in statuses):
        program_map.status = "in-progress"
    else:
        program_map.status = "planned"
    return program_map


def next_ready_epic(program_map: SPOQMap) -> SPOQMapEpic | None:
    """Return the next epic that is ready to execute."""
    completed = {epic.id for epic in program_map.epics if epic.status == "done"}
    waves = program_map.wave_assignments or compute_epic_waves(program_map.epics)

    for wave in waves:
        for epic_id in wave:
            epic = find_epic_by_id(program_map, epic_id)
            if epic is None:
                continue
            if epic.status in {"done", "in-progress"}:
                continue
            if all(dep in completed for dep in epic.depends_on):
                return epic

    return None
