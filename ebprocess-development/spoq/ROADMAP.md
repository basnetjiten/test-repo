# SPOQ Epic Roadmap

> Centralized registry of all epics and their current disposition.
> Updated by the orchestrator on each status transition.
> Completion is tracked via status field — no filesystem move needed.

| Epic ID | Sprint | Title | Status | Depends On | Platforms |
|---------|--------|-------|--------|------------|-----------|
| Epic-44445 | sprint-1 | Project Configurations | in-progress | — | api, flutter |
| | | | | | |

## Status Meanings

| Status | Meaning |
|--------|---------|
| `planned` | Epic created, pending validation |
| `in-progress` | Execution started |
| `done` | All tasks passed code validation |

## Rules

- Orchestrator appends new entry during epic creation.
- Status updated on each transition (planned → in-progress → done).
- ROADMAP.md is the single source of truth for epic status.
